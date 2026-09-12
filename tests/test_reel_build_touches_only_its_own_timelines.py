"""A build may only delete what it is actually rebuilding.

The defect this pins
--------------------
`rebuild_reels_in_project` collected every timeline whose name began
`"Reel "` and called `DeleteTimelines` on all of them, unconditionally,
before placing anything.  On the field-test project that is nineteen
approved timelines destroyed in order to write nineteen - and building
ONE reel destroyed the other eighteen.  Nothing backed them up and
nothing said so.  The captain, 2026-09-06: *"a refusal is cheap and a
deleted timeline is not."*

The other half of the pair was already right: `write_provenance` has
MERGED rather than replaced since #568, because *"a partial rebuild must
not delete the provenance of the reels it did not touch"*.  A partial
rebuild could not happen, because the delete loop ran first and took
everything.

Both directions, because a guard that cannot fire is not a guard
----------------------------------------------------------------
`assert_deletion_scope` is asked of the LIST ABOUT TO BE DELETED rather
than recomputing the selection, so it can catch the selection being
wrong.  These tests call it directly with a permitted list and with a
refused one, and drive a whole build with an over-collecting selection
to prove it fires where it is actually wired - not merely where it is
defined.

The survivors are asserted by NAME.  The fake media pool really removes
what it is handed, so "the other eighteen are still there" is read off
the project afterwards rather than inferred from a mock call.
"""
import json

import pytest
from unittest.mock import MagicMock, patch

from library.tools.reel_build import (
    BACKUP_SUFFIX,
    ReelBuildError,
    STAGING_SUFFIX,
    assert_deletion_scope,
    rebuild_reels_in_project,
    timelines_to_replace,
)

MASTER = "GEO Podcast - Synced"
APPROVED = [f"Reel {n:02d} - moment-{n}" for n in range(1, 20)]
"""Nineteen, the field-test project's own shape - the number that makes
the old loop's blast radius the point rather than a detail."""


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


def _timeline(name):
    timeline = MagicMock()
    timeline._name = name
    timeline.GetName.side_effect = lambda: timeline._name
    def _rename(new):
        timeline._name = new
        return True
    timeline.SetName.side_effect = _rename
    return timeline


class FakeProject:
    """A Resolve project whose media pool really creates and deletes.

    A mock that only RECORDS the delete call cannot answer the question
    the captain asked - are the other eighteen still there - so this
    removes them and the tests read the survivors back off it.
    """

    def __init__(self, names):
        self.timelines = [_timeline(name) for name in names]
        self.created = []
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        pool.CreateEmptyTimeline.side_effect = self._create
        self._pool = pool

    def _create(self, name):
        timeline = _timeline(name)
        self.timelines.append(timeline)
        self.created.append(name)
        return timeline

    def _delete(self, timelines):
        for timeline in timelines:
            self.timelines.remove(timeline)
        return True

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


def _run(resolve_project, project_dir, **kwargs):
    """Drive the ONE build path with Resolve, the verifier and the placer faked."""
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
            patch("library.tools.reel_build.reel_subtitle_segments") as caps, \
            patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve_project), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[_moment(i + 1, name)
                                for i, name in enumerate(APPROVED)]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=0):
        caps.return_value = [{"overlay_path": "x.mov"}]
        # No media pool on the stand-in project, so filing is
        # exercised in tests/test_organise_media_pool.py instead.
        kwargs.setdefault("organise", False)
        record = rebuild_reels_in_project(str(project_dir), **kwargs)
    return record, placed, caps


# ── The question the captain asked ───────────────────────────────────

def test_building_one_reel_leaves_the_other_eighteen_present(project):
    """The whole defect, stated as the survivors.

    Reel 03 is legitimately replaced - it is the one being rebuilt, and
    promotion retires its original to a backup and moves the passing
    staging onto its name. The other EIGHTEEN and the master must still
    be there, and no staging or backup container may be left behind.
    Under the loop this replaces, this assertion reads
    `['GEO Podcast - Synced']`.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, placed, _ = _run(resolve_project, project, only=[3])

    survivors = resolve_project.names()
    untouched = [name for name in APPROVED if name != "Reel 03 - moment-3"]
    assert MASTER in survivors
    for name in untouched:
        assert name in survivors, f"{name} was deleted by a build of reel 3"
    assert len(untouched) == 18
    # Plus the retired generation of the one reel this build replaced:
    # promotion archives it rather than deleting it
    # (`library/tools/reel_retirement.py`).
    assert sorted(survivors) == sorted(
        [MASTER] + APPROVED + ["Reel 03 - moment-3 (archived round 001)"])
    assert record["timelines_built"] == ["Reel 03 - moment-3"]
    assert record["staged_timelines"] == {}
    assert placed.call_count == 1
    assert placed.call_args[1]["timeline_name"] == (
        "Reel 03 - moment-3" + STAGING_SUFFIX)


def test_building_one_reel_into_a_new_name_deletes_nothing_at_all(project):
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, placed, _ = _run(resolve_project, project, only=[3],
                             name_suffix=" (pipeline rebuild)")

    assert sorted(resolve_project.names()) == sorted(
        [MASTER] + APPROVED + ["Reel 03 - moment-3 (pipeline rebuild)"])
    assert not resolve_project.GetMediaPool().DeleteTimelines.called
    assert record["timelines_built"] == [
        "Reel 03 - moment-3 (pipeline rebuild)"]
    assert placed.call_args[1]["timeline_name"] == (
        "Reel 03 - moment-3 (pipeline rebuild)" + STAGING_SUFFIX)


def test_a_full_rebuild_replaces_its_own_output_and_spares_an_orphan(project):
    """`Reel 99` is a timeline no approved moment will recreate.

    The old loop deleted it because the name began "Reel ". Deleting a
    timeline nothing is about to replace is destruction, not a rebuild.
    Promotion RETIRES this run's originals - nineteen, each renamed
    into the archive under the round it was current for - and deletes
    nothing at all on a first retirement. The orphan is neither
    retired nor deleted: it is not a reel this build placed.
    """
    from library.tools import reel_retirement

    resolve_project = FakeProject([MASTER] + APPROVED + ["Reel 99 - orphan"])

    record, placed, _ = _run(resolve_project, project)

    assert "Reel 99 - orphan" in resolve_project.names()
    assert resolve_project.GetMediaPool().DeleteTimelines.call_args is None
    archived = sorted(name for name in resolve_project.names()
                      if reel_retirement.is_archived_timeline(name))
    assert archived == sorted(
        reel_retirement.archived_name(name, 1) for name in APPROVED)
    assert sorted(resolve_project.names()) == sorted(
        [MASTER] + APPROVED + ["Reel 99 - orphan"] + archived)
    assert record["timelines_built"] == APPROVED
    assert placed.call_count == 19


# ── The guard, both directions ───────────────────────────────────────

def test_the_guard_permits_replacing_exactly_what_is_being_placed():
    """The direction that must NOT fire. A guard that refuses correct
    output is no more coverage than one that cannot fire (AGENTS.md
    10.4)."""
    planned = {"Reel 03 - moment-3", "Reel 04 - moment-4"}
    assert_deletion_scope(
        [_timeline("Reel 03 - moment-3"), _timeline("Reel 04 - moment-4")],
        planned)
    assert_deletion_scope([], planned)


def test_the_guard_refuses_a_timeline_that_was_never_planned():
    with pytest.raises(ReelBuildError) as refused:
        assert_deletion_scope(
            [_timeline("Reel 03 - moment-3"), _timeline("Reel 07 - other")],
            {"Reel 03 - moment-3"})
    message = str(refused.value)
    assert "Reel 07 - other" in message
    assert "REFUSING to build" in message
    assert "Reel 03 - moment-3" in message


def test_the_guard_is_wired_where_the_deletion_happens(project):
    """Fires on the REAL path, not merely where it is defined.

    The selection is made to over-collect - the shape the old loop had -
    and the build must refuse before anything is deleted or placed.
    Nothing is deleted before the gate passes any more, so the refusal
    now fires at the staging check: a debris container is never reused
    as this run's staging, whatever produced the list.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)
    everything = [t for t in resolve_project.timelines
                  if t.GetName().startswith("Reel ")]

    with patch("library.tools.reel_build.timelines_to_replace",
               return_value=everything):
        with pytest.raises(ReelBuildError, match="REFUSING to build"):
            _run(resolve_project, project, only=[3])

    assert resolve_project.names() == [MASTER] + APPROVED
    assert not resolve_project.GetMediaPool().DeleteTimelines.called


def test_the_selection_matches_a_name_exactly_never_by_prefix():
    resolve_project = FakeProject(
        [MASTER, "Reel 03 - moment-3", "Reel 03 - moment-3 (old)",
         "Reel 30 - moment-30"])

    found = timelines_to_replace(resolve_project, {"Reel 03 - moment-3"})

    assert [t.GetName() for t in found] == ["Reel 03 - moment-3"]


# ── What the build was ASKED for ─────────────────────────────────────

def test_only_refuses_a_reel_the_plan_has_not_approved(project):
    resolve_project = FakeProject([MASTER] + APPROVED)

    with pytest.raises(ReelBuildError, match=r"build reel\(s\) \[77\]"):
        _run(resolve_project, project, only=[77])

    assert resolve_project.names() == [MASTER] + APPROVED
    assert not resolve_project.GetMediaPool().DeleteTimelines.called


def test_the_suffix_reaches_the_caption_filenames(project):
    """A rebuild under a staging suffix still places through its own
    timeline label.

    The label no longer names any file - segment filenames are
    provenance plus content digest - but it is recorded on every
    entry's binding (which placing the shared file serves) and it
    drives the rejected-reel refusal, so the staging container's
    renders must still travel under the staged name.

    The approved timeline's overlays are untouched until promotion
    renames the timeline (pool items, not filenames, are what it
    points at).
    """
    resolve_project = FakeProject([MASTER] + APPROVED)

    _, _, caps = _run(resolve_project, project, only=[3],
                      name_suffix=" (pipeline rebuild)")

    assert caps.call_args[1]["timeline_name"] == (
        "Reel 03 - moment-3 (pipeline rebuild)" + STAGING_SUFFIX)


def test_the_record_says_what_the_build_was_asked_for(project):
    resolve_project = FakeProject([MASTER] + APPROVED)

    full, _, _ = _run(resolve_project, project)
    assert full["reels_requested"] is None
    assert full["name_suffix"] == ""
    assert full["staged_timelines"] == {}
    assert json.dumps(full)

    resolve_project = FakeProject([MASTER] + APPROVED)
    partial, _, _ = _run(resolve_project, project, only=[3, 5],
                         name_suffix=" (pipeline rebuild)")
    assert partial["reels_requested"] == [3, 5]
    assert partial["name_suffix"] == " (pipeline rebuild)"
    assert partial["staged_timelines"] == {}


# ── What `only` accepts, and what it refuses ─────────────────────────

def test_reel_numbers_accepts_the_spellings_an_override_arrives_in():
    from library.tools.reel_build import reel_numbers

    assert reel_numbers(None) is None
    assert reel_numbers([3]) == {3}
    assert reel_numbers(["3", 5]) == {3, 5}
    assert reel_numbers("3 5") == {3, 5}
    assert reel_numbers("3,5") == {3, 5}
    assert reel_numbers([]) == set()


def test_reel_numbers_refuses_a_name_rather_than_guessing_a_moment():
    from library.tools.reel_build import reel_numbers

    with pytest.raises(ReelBuildError, match="must be reel NUMBERS"):
        reel_numbers(["Reel 03 - moment-3"])
