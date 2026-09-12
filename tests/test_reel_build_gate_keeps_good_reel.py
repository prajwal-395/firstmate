"""A gate-failing reel build must not overwrite the good timeline.

The defect this pins
--------------------
`rebuild_reels_in_project` deleted the existing timelines FIRST, placed
the rebuild, and ran the conformance verifier LAST. A build the gate
refused - reel 5 of the 2026-09-08 rebuild, F17 (mixed-speaker card)
plus F8 (end cuts Craig mid-word through 'about') - exited 1 AFTER
replacing the timeline, converting a live approved reel into
verify-failed content with no code/content fix in hand
(`docs/REEL_REBUILD_RUN_20260908_R3.md`).

The fix is structural: the rebuild is staged into a separate container,
the gate grades the staging, and the original is replaced only on a
pass. On a fail the staging is removed and the approved timeline is
still there. These tests read the survivors off a fake project whose
pool really creates, renames and deletes - a mock that only records
the calls cannot answer "is the good reel still there".

Deliberately convention-free: this file names no staging convention,
so it fails on the pre-fix code for the right reason (the original is
gone) rather than on an import.
"""
import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from library.tools.reel_build import (
    BACKUP_SUFFIX,
    STAGING_SUFFIX,
    promote_staged_reels,
    rebuild_reels_in_project,
)

MASTER = "GEO Podcast - Synced"
APPROVED = [f"Reel {n:02d} - moment-{n}" for n in range(1, 20)]
TARGET = "Reel 03 - moment-3"


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    yield


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        f'resolve: {{project_name: "Mock Project", '
        f'timeline_name: "{MASTER}"}}', encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text("{}", encoding="utf-8")
    return root


def _moment(number, name):
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = number
    moment.timeline_name = name
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


class FakeTimeline:
    """A timeline whose name really changes when renamed.

    It carries no rows: `GetTrackCount` answers 0 on both media types,
    so the replace guard (issue #925) diffs empty against empty and
    passes. A timeline the tests mean to grade row by row belongs in
    `test_promote_replace_guard.py`, whose fakes carry real rows.
    """

    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetTrackCount(self, kind):
        return 0

    def GetTrackName(self, kind, index):
        return ""

    def GetItemListInTrack(self, kind, index):
        return []

    # Promotion reads the captain's markers off the retiring timeline
    # before anything is renamed (`library/tools/marker_carry.py`), and
    # REFUSES a timeline whose markers it cannot see - so a fake that
    # cannot answer for them is a fake of a different object.
    def GetStartFrame(self):
        return 0

    def GetMarkers(self):
        return dict(getattr(self, "_markers", {}))

    def AddMarker(self, frame, color, name, note, duration, custom=""):
        self.added_markers = getattr(self, "added_markers", [])
        self.added_markers.append((frame, color, name, note))
        return True

    def GetUniqueId(self):
        if not getattr(self, "_uid", None):
            type(self)._seq = getattr(type(self), "_seq", 0) + 1
            self._uid = f"{type(self).__name__}-{type(self)._seq}"
        return self._uid


class FakeProject:
    """A Resolve project whose pool really creates, renames and deletes."""

    def __init__(self, names):
        self.timelines = [FakeTimeline(name) for name in names]
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        pool.CreateEmptyTimeline.side_effect = self._create
        self._pool = pool

    def _delete(self, timelines):
        for timeline in timelines:
            self.timelines.remove(timeline)
        return True

    def _create(self, name):
        timeline = FakeTimeline(name)
        self.timelines.append(timeline)
        return timeline

    def GetName(self):
        return "Mock Project"

    def GetMediaPool(self):
        return self._pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def names(self):
        return [t.GetName() for t in self.timelines]

    # A Resolve project HAS a cursor, and `resolve_lock`'s guard reads
    # it back by unique id - a fake without one cannot model the guard.
    def GetCurrentTimeline(self):
        # None until something sets it: a project that has not been
        # pointed anywhere has no cursor, and inventing one here would
        # hand the entry-unit guard a timeline nobody opened.
        return getattr(self, "_current", None)

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


def _drive(resolve_project, project_dir, gate_result, **kwargs):
    """Drive the build with the placer creating what it is asked for.

    The mocked `build_reel_timeline` creates the container it was given
    - the one honest half of the real placer for this question - so
    "what exists afterwards" is read off the project, not inferred.
    """
    with _patched_build(
            resolve_project, project_dir, gate_result) as (placed, gate):
        kwargs.setdefault("organise", False)
        record = rebuild_reels_in_project(str(project_dir), **kwargs)
    return record, placed, gate


@contextmanager
def _patched_build(resolve_project, project_dir, gate_result):
    """All patches for driving a build; yields (placed_mock, gate_mock).

    `_drive` covers the passing path; failing-path tests enter this
    directly so the gate mock survives the raise.
    """
    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        # The placer returns its build record now (the track
        # plan the timeline was placed from); the container
        # the mock creates is the half these tests grade.
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place) as placed, \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]) as caps, \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(i + 1, name)
                                for i, name in enumerate(APPROVED)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=gate_result) as gate:
        caps.return_value = []
        yield placed, gate


def _gate_failed_report(project_dir):
    """Reel 5's shape: planning findings, not placement ones."""
    path = (project_dir / "pipeline_output" / "review"
            / "conformance_report.json")
    path.write_text(json.dumps({
        "has_errors": True,
        "findings": [
            {"severity": "error", "finding_class": "F17",
             "message": "caption card 'recommend you or your brand.' "
                        "mixes speakers: Akshita, Craig"},
            {"severity": "error", "finding_class": "F8",
             "message": "END at 413.85s cuts Craig mid-speech, "
                        "through the word 'about'"},
        ],
    }), encoding="utf-8")


def test_a_gate_failing_build_leaves_the_approved_timeline_in_place(project):
    """The defect, stated as the survivors.

    The gate refuses the rebuild (F17 + F8, reel 5's shape) and the
    build raises - but the approved timeline must still be there, and
    no failing content may sit under its name. Under the
    delete-first code this reads the replaced timeline: the original
    is gone and the assertion fails on the survivors, not on a mock.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)
    _gate_failed_report(project)
    original = next(t for t in resolve_project.timelines
                    if t.GetName() == TARGET)

    with pytest.raises(RuntimeError, match="defective timeline"):
        _drive(resolve_project, project, 1, only=[3])

    survivors = resolve_project.names()
    assert original in resolve_project.timelines, (
        f"{TARGET} was destroyed by a build the gate refused - the "
        "timeline under its name now is the failing rebuild, not the "
        "approved reel")
    assert survivors.count(TARGET) == 1
    assert sorted(survivors) == sorted([MASTER] + APPROVED), (
        "a refused build must leave every timeline exactly as it was: "
        f"{sorted(survivors)}")


def test_a_gate_failing_build_grades_the_staging_not_the_approved_reel(project):
    """The gate never sees the approved timeline: it is asked to grade
    the staging container the build placed, by its staging name."""
    resolve_project = FakeProject([MASTER] + APPROVED)
    _gate_failed_report(project)

    with _patched_build(resolve_project, project, 1) as (_, gate), \
            pytest.raises(RuntimeError, match="defective timeline"):
        rebuild_reels_in_project(str(project), organise=False, only=[3])

    assert gate.call_args[1]["only_reels"] == [TARGET + STAGING_SUFFIX]


def test_a_gate_failing_build_reports_the_planning_findings(project):
    """The raise still carries the gate's own findings, not a swap error."""
    resolve_project = FakeProject([MASTER] + APPROVED)
    _gate_failed_report(project)

    with pytest.raises(RuntimeError) as refused:
        _drive(resolve_project, project, 1, only=[3])

    message = str(refused.value)
    assert "F17" in message and "F8" in message


def test_a_passing_build_replaces_the_target_and_reports_final_names(project):
    """The other direction: a clean gate still replaces, and the record
    names the reels the captain sees - no staging container leaks into
    the project or the record."""
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, placed, _ = _drive(resolve_project, project, 0, only=[3])

    assert placed.call_count == 1
    assert record["timelines_built"] == [TARGET]
    assert record["staged_timelines"] == {}
    # The replaced timeline is RETIRED, not deleted: it is renamed into
    # `05 - Reels/Archive` under the round it was current for
    # (`library/tools/reel_retirement.py`), so the previous cut is
    # still there to compare the round against.
    assert sorted(resolve_project.names()) == sorted(
        [MASTER] + APPROVED + [f"{TARGET} (archived round 001)"])
    # The sidecar baselines followed the promotion: filed under the
    # final name the gate passed, with no staging key left behind.
    provenance = json.loads(
        (project / "pipeline_output" / "review"
         / "plan_provenance.json").read_text(encoding="utf-8"))
    assert TARGET in provenance["built_reels"]
    assert not [name for name in provenance["built_reels"]
                if name.endswith(STAGING_SUFFIX)]


def test_stage_then_promote_is_the_dag_path_end_to_end(project):
    """`verify=False` stops at staging and returns the mapping; the
    promotion retires the original to a backup and moves the staging
    onto the final name - no staging or backup container left."""
    resolve_project = FakeProject([MASTER] + APPROVED)
    original = next(t for t in resolve_project.timelines
                    if t.GetName() == TARGET)

    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        # The placer returns its build record now (the track
        # plan the timeline was placed from); the container
        # the mock creates is the half these tests grade.
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(i + 1, name)
                                for i, name in enumerate(APPROVED)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=0):
        record = rebuild_reels_in_project(
            str(project), organise=False, verify=False, only=[3])

        staging = TARGET + STAGING_SUFFIX
        assert record["timelines_built"] == [staging]
        assert record["staged_timelines"] == {TARGET: staging}
        # Staged and ungraded: BOTH containers exist, the original
        # untouched.
        assert original in resolve_project.timelines
        assert staging in resolve_project.names()

        promoted = promote_staged_reels(
            str(project), "Mock Project", MASTER,
            record["staged_timelines"], organise=False)

    assert promoted["promoted"] == [TARGET]
    # Retired, not deleted: the object is still in the project under
    # its archived name (`library/tools/reel_retirement.py`).
    assert original in resolve_project.timelines
    assert original.GetName() == f"{TARGET} (archived round 001)"
    # The replaced timeline is RETIRED, not deleted: it is renamed into
    # `05 - Reels/Archive` under the round it was current for
    # (`library/tools/reel_retirement.py`), so the previous cut is
    # still there to compare the round against.
    assert sorted(resolve_project.names()) == sorted(
        [MASTER] + APPROVED + [f"{TARGET} (archived round 001)"])
    assert not [name for name in resolve_project.names()
                if name.endswith((STAGING_SUFFIX, BACKUP_SUFFIX))]
