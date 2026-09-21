"""A verify-refusing reel must not take its batch siblings down with it.

The defect, measured 2026-09-20 on lane `dup-takes-rebuild-4`: nine
placements to promote three reels - four refusals each discarded the
clean siblings, and the lane closed three notes in six hours against
`caption-text-wave`'s thirteen in seventy minutes on the same shape
of work. Three coupled defects:

1. The `verify_reels` node's `except Exception` discarded ALL
   `staged.values()` - not the reel that failed.
2. The in-process gate in `rebuild_reels_in_project` discarded all
   `built_reel_names` on any gate refusal.
3. `staging_holds.json` takes and releases were unlocked
   read-modify-write, so interleaved writers silently dropped holds.

The fix: `verify_built_reels` raises `ReelVerificationRefused`
carrying the refused staging names read off the report rows the gate
wrote, and both discard paths remove exactly those - a refusal that
names none, and any failure where the gate never graded, discards
NOTHING. Holds take/release run under an exclusive file lock.

These tests drive the real build and the real node against a fake
Resolve project whose pool really deletes, and assert on the
siblings' stagings EXISTING - not on a count - with their holds
held, while the raise still names the failed reel.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from library.tools import staging_holds as holds
from library.tools.reel_build import (
    STAGING_SUFFIX,
    ReelVerificationRefused,
    rebuild_reels_in_project,
)

MASTER = "GEO Podcast - Synced"
APPROVED = [f"Reel {n:02d} - moment-{n}" for n in range(1, 20)]
TARGET_A = "Reel 03 - moment-3"
TARGET_B = "Reel 04 - moment-4"
STAGED_A = TARGET_A + STAGING_SUFFIX
STAGED_B = TARGET_B + STAGING_SUFFIX


@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    # Stubbed through the shared fixture: `patch.dict` on
    # `sys.modules` restores the WHOLE dict and so evicts every
    # module first imported inside it (tests/conftest.py).
    yield


@pytest.fixture
def project_dir(tmp_path):
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
    """A timeline whose deletion really removes it from the project."""

    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetTrackCount(self, kind):
        return 0

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
    """A Resolve project whose pool really creates and deletes."""

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


def _write_gate_report(project_dir, rows):
    """The report shape the real gate writes: per-reel rows naming
    the GRADED (staging) containers with their error counts."""
    path = (project_dir / "pipeline_output" / "review"
            / "conformance_report.json")
    path.write_text(json.dumps({
        "has_errors": any(errors for _, _, errors in rows),
        "reels": [
            {"reel_name": name,
             "reel_number": number,
             "errors": errors,
             "warnings": 0,
             "captions": "0/0",
             "findings": [{"finding_class": "F8",
                           "severity": "error"}] if errors else []}
            for name, number, errors in rows
        ],
    }), encoding="utf-8")


def _drive_build(resolve_project, project_dir, rows, **kwargs):
    """Drive the real in-process build+gate over two reels.

    The placer really stages both containers; the gate stub writes
    the given per-reel report rows and returns 1 (refused)."""
    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        assert name, "the placer was asked to build into no container"
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}

    def _gate(**_gate_kwargs):
        _write_gate_report(project_dir, rows)
        return 1

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(3, TARGET_A),
                                _moment(4, TARGET_B)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  side_effect=_gate):
        kwargs.setdefault("organise", False)
        return rebuild_reels_in_project(
            str(project_dir), only=[3, 4], **kwargs)


# --------------------------------- the in-process gate: blast radius one


def test_one_refusing_reel_leaves_its_sibling_staged_and_held(project_dir):
    """The batch case from the lane's own log: four reels in, one
    refuses, the clean siblings' verified stagings AND their holds
    must survive - while the raise still names the failed reel."""
    resolve_project = FakeProject([MASTER, TARGET_A, TARGET_B])

    with pytest.raises(ReelVerificationRefused) as refused:
        _drive_build(resolve_project, project_dir,
                     [(STAGED_A, 3, 1), (STAGED_B, 4, 0)])

    message = str(refused.value)
    assert STAGED_A in message, (
        "the raise must name the reel that refused")
    assert "untouched by this refusal" in message

    # Asserted on EXISTING, not on a count: the sibling's staging is
    # still a timeline in the project and still under hold.
    assert STAGED_B in resolve_project.names(), (
        "the clean sibling's staging was discarded with the refusal")
    assert STAGED_A not in resolve_project.names(), (
        "the refused staging must be gone")
    assert TARGET_A in resolve_project.names()
    assert TARGET_B in resolve_project.names(), (
        "the approved reels were never named and must all survive")

    held = holds.held_names(str(project_dir))
    assert STAGED_B in held, (
        "the clean sibling's hold went with the refusal - a future "
        "prune is now unprotected against a live staging")
    assert STAGED_A not in held


def test_an_unattributed_refusal_discards_nothing(project_dir):
    """The fail-closed direction: the gate failed but no report row
    carries error findings, so the refusal names no reel - and no
    staging may go. The stagings stay with their holds until an
    operator reads the report and discards deliberately."""
    resolve_project = FakeProject([MASTER, TARGET_A, TARGET_B])

    with pytest.raises(ReelVerificationRefused, match="NO staging"):
        _drive_build(resolve_project, project_dir,
                     [(STAGED_A, 3, 0), (STAGED_B, 4, 0)])

    assert STAGED_A in resolve_project.names()
    assert STAGED_B in resolve_project.names()
    held = holds.held_names(str(project_dir))
    assert STAGED_A in held and STAGED_B in held


def test_an_all_passing_batch_is_unchanged(project_dir):
    """The other direction: a batch where every reel passes promotes
    both, leaves no staging container and no hold behind."""
    resolve_project = FakeProject([MASTER, TARGET_A, TARGET_B])

    def _place(**place_kwargs):
        name = place_kwargs.get("timeline_name")
        resolve_project.GetMediaPool().CreateEmptyTimeline(name)
        return {"track_plan": {"video_tracks": [],
                               "audio_tracks": [],
                               "material": {}}}

    def _gate(**_gate_kwargs):
        _write_gate_report(project_dir, [(STAGED_A, 3, 0),
                                         (STAGED_B, 4, 0)])
        return 0

    with patch("library.tools.reel_build.build_reel_timeline",
               side_effect=_place), \
            patch("library.tools.reel_build.reel_subtitle_segments",
                  return_value=[]), \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(3, TARGET_A),
                                _moment(4, TARGET_B)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  side_effect=_gate):
        record = rebuild_reels_in_project(
            str(project_dir), organise=False, only=[3, 4])

    assert sorted(record["timelines_built"]) == sorted([TARGET_A, TARGET_B])
    assert sorted(resolve_project.names()) == sorted(
        [MASTER, TARGET_A, TARGET_B])
    assert holds.held_names(str(project_dir)) == set()


# ------------------------- the verify_reels node: blast radius one


def _verify_step_module():
    from library.tools.operations import load_step_module

    return load_step_module("step_7_02_verify_reels", "step.py")


@contextmanager
def _noop_lease(*_args, **_kwargs):
    yield


def _drive_node(project_dir, resolve_project, gate_effect):
    """Drive the real node with the real discard underneath.

    Only the Resolve connection and the lease are stubbed - the
    discard that runs deletes real fake timelines and releases real
    holds, so "the sibling still exists" is read off the project."""
    module = _verify_step_module()
    with patch("library.tools.reel_build.verify_built_reels",
               side_effect=gate_effect), \
            patch("library.tools.reel_build._connect_resolve_project",
                  return_value=resolve_project), \
            patch("library.tools.reel_build.resolve_lease",
                  side_effect=_noop_lease), \
            patch("library.tools.timeline_transcript.transcript_path",
                  return_value=str(
                      project_dir / "pipeline_output" / "scratch"
                      / "timeline_transcript" / "transcript.json")):
        return module.verify_reels({
            "project_folder": str(project_dir),
            "reel_build": {
                "timelines_built": [STAGED_A, STAGED_B],
                "staged_timelines": {TARGET_A: STAGED_A,
                                     TARGET_B: STAGED_B},
                "resolve_project_name": "Mock Project",
                "master_timeline_name": MASTER,
                "plan_path": str(project_dir / "plan.json"),
            },
            "timeline_transcript": {"segments": []},
        })


def test_the_node_discards_only_the_reel_the_gate_named(project_dir):
    """The node's half of the lane log: the gate refuses reel A, the
    node discards A's staging through the REAL discard - and B's
    staging still exists, still held."""
    resolve_project = FakeProject(
        [MASTER, TARGET_A, TARGET_B, STAGED_A, STAGED_B])
    holds.take_hold(str(project_dir), STAGED_A, awaiting=TARGET_A,
                    taken_by="test")
    holds.take_hold(str(project_dir), STAGED_B, awaiting=TARGET_B,
                    taken_by="test")

    with pytest.raises(ReelVerificationRefused) as refused:
        _drive_node(project_dir, resolve_project,
                    ReelVerificationRefused(
                        f"F8 end cuts mid-word on {STAGED_A}",
                        failed_reels=[STAGED_A]))

    assert STAGED_A in str(refused.value)
    assert STAGED_B in resolve_project.names(), (
        "the clean sibling's staging was discarded with the refusal")
    assert STAGED_A not in resolve_project.names()
    held = holds.held_names(str(project_dir))
    assert STAGED_B in held
    assert STAGED_A not in held


def test_the_node_discards_nothing_when_the_gate_never_graded(project_dir):
    """A connect failure is not a verdict: both stagings stay, both
    holds stay, and the failure propagates naming that."""
    resolve_project = FakeProject(
        [MASTER, TARGET_A, TARGET_B, STAGED_A, STAGED_B])
    holds.take_hold(str(project_dir), STAGED_A, awaiting=TARGET_A,
                    taken_by="test")
    holds.take_hold(str(project_dir), STAGED_B, awaiting=TARGET_B,
                    taken_by="test")

    with pytest.raises(RuntimeError, match="Resolve connection refused"):
        _drive_node(project_dir, resolve_project,
                    RuntimeError("Resolve connection refused"))

    assert STAGED_A in resolve_project.names()
    assert STAGED_B in resolve_project.names()
    held = holds.held_names(str(project_dir))
    assert STAGED_A in held and STAGED_B in held
