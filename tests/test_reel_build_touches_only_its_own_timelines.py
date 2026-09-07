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
    ReelBuildError,
    assert_deletion_scope,
    rebuild_reels_in_project,
    timelines_to_replace,
)

MASTER = "GEO Podcast - Synced"
APPROVED = [f"Reel {n:02d} - moment-{n}" for n in range(1, 20)]
"""Nineteen, the field-test project's own shape - the number that makes
the old loop's blast radius the point rather than a detail."""


@pytest.fixture(autouse=True)
def mock_dvr():
    with patch.dict("sys.modules", {"DaVinciResolveScript": MagicMock()}):
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
    timeline.GetName.return_value = name
    return timeline


class FakeProject:
    """A Resolve project whose media pool really deletes.

    A mock that only RECORDS the delete call cannot answer the question
    the captain asked - are the other eighteen still there - so this
    removes them and the tests read the survivors back off it.
    """

    def __init__(self, names):
        self.timelines = [_timeline(name) for name in names]
        self.created = []
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        self._pool = pool

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


def _run(resolve_project, project_dir, **kwargs):
    """Drive the ONE build path with Resolve, the verifier and the placer faked."""
    with patch("library.tools.reel_build.build_reel_timeline") as placed, \
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
    the fake placer does not put it back.  The other EIGHTEEN and the
    master must still be there.  Under the loop this replaces, this
    assertion reads `['GEO Podcast - Synced']`.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, placed, _ = _run(resolve_project, project, only=[3])

    survivors = resolve_project.names()
    untouched = [name for name in APPROVED if name != "Reel 03 - moment-3"]
    assert MASTER in survivors
    for name in untouched:
        assert name in survivors, f"{name} was deleted by a build of reel 3"
    assert len(untouched) == 18
    assert survivors == [MASTER] + untouched
    assert record["timelines_built"] == ["Reel 03 - moment-3"]
    assert placed.call_count == 1


def test_building_one_reel_into_a_new_name_deletes_nothing_at_all(project):
    resolve_project = FakeProject([MASTER] + APPROVED)

    record, placed, _ = _run(resolve_project, project, only=[3],
                             name_suffix=" (pipeline rebuild)")

    assert resolve_project.names() == [MASTER] + APPROVED
    assert not resolve_project.GetMediaPool().DeleteTimelines.called
    assert record["timelines_built"] == [
        "Reel 03 - moment-3 (pipeline rebuild)"]
    assert placed.call_args[1]["timeline_name"] == (
        "Reel 03 - moment-3 (pipeline rebuild)")


def test_a_full_rebuild_replaces_its_own_output_and_spares_an_orphan(project):
    """`Reel 99` is a timeline no approved moment will recreate.

    The old loop deleted it because the name began "Reel ". Deleting a
    timeline nothing is about to replace is destruction, not a rebuild.
    """
    resolve_project = FakeProject([MASTER] + APPROVED + ["Reel 99 - orphan"])

    record, placed, _ = _run(resolve_project, project)

    assert "Reel 99 - orphan" in resolve_project.names()
    deleted = resolve_project.GetMediaPool().DeleteTimelines.call_args[0][0]
    assert sorted(t.GetName() for t in deleted) == sorted(APPROVED)
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
    """A caption named for the OLD timeline overwrites the old overlays.

    The segment filename's `timeline` component is the discriminator
    (`subtitle_segment_id.assert_named_timeline`) and it carries no
    caption content - so a rebuild under the old label writes new words
    into the exact files the approved timeline still points at, leaving
    every database row identical and the picture changed.
    """
    resolve_project = FakeProject([MASTER] + APPROVED)

    _, _, caps = _run(resolve_project, project, only=[3],
                      name_suffix=" (pipeline rebuild)")

    assert caps.call_args[1]["timeline_name"] == (
        "Reel 03 - moment-3 (pipeline rebuild)")


def test_the_record_says_what_the_build_was_asked_for(project):
    resolve_project = FakeProject([MASTER] + APPROVED)

    full, _, _ = _run(resolve_project, project)
    assert full["reels_requested"] is None
    assert full["name_suffix"] == ""
    assert json.dumps(full)

    resolve_project = FakeProject([MASTER] + APPROVED)
    partial, _, _ = _run(resolve_project, project, only=[3, 5],
                         name_suffix=" (pipeline rebuild)")
    assert partial["reels_requested"] == [3, 5]
    assert partial["name_suffix"] == " (pipeline rebuild)"


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
