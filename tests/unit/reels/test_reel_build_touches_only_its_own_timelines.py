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

import pytest
from unittest.mock import MagicMock, patch

from library.tools.reel_build import (
    ReelBuildError,
    STAGING_SUFFIX,
    assert_deletion_scope,
    rebuild_reels_in_project,
    timelines_to_replace,
)
from tests.resolve_double import FakeProject, FakeTimeline

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
    (scratch / "transcript.json").write_text(
        '{"segments": []}', encoding="utf-8")
    return root


def _moment(number, name):
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = number
    moment.timeline_name = name
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0
    return moment


def _only_probe_and_backups_deleted(project, backups=()):
    """The build-time draw-gain probe creates and deletes its own
    scratch timeline (`draw_gain_probe.PROBE_TIMELINE_NAME`) on every
    build - that pair is the probe cleaning up after itself - and
    promotion deletes exactly the backups it replaced
    (`reel_retirement.delete_backups`). What these tests guard is that
    nothing ELSE is ever deleted."""
    from library.tools.draw_gain_probe import PROBE_TIMELINE_NAME

    allowed = {PROBE_TIMELINE_NAME} | set(backups or ())
    assert project.deleted, "expected the probe's own create-and-delete pair"
    for name in project.deleted:
        assert name in allowed, (
            f"the build deleted {name!r} - only the probe's scratch "
            f"timeline and the replaced backups may be deleted")
    for name in backups or ():
        assert name in project.deleted, (
            f"the replaced backup {name!r} was not deleted")


def _only_probe_deleted(project):
    """No reel was replaced, so no backup exists: only the probe's own
    scratch timeline may have been deleted."""
    _only_probe_and_backups_deleted(project, [])


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
            patch("library.tools.reel_replace_guard.full_timeline_snapshot",
                  side_effect=lambda timeline, _project, _folder=None: {
                      "timeline": {"name": timeline.GetName(),
                                   "unique_id": str(timeline.GetUniqueId()),
                                   "settings": {}, "start_frame": 0,
                                   "end_frame": 0},
                      "items": [], "markers": []}), \
            patch("library.tools.reel_conformance_verifier.run_verification",
                  return_value=0):
        caps.return_value = [{"overlay_path": "x.mov"}]
        # No media pool on the stand-in project, so filing is
        # exercised in tests/unit/resolve/test_organise_media_pool.py instead.
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
    # The one reel this build replaced leaves no second generation:
    # its backup is deleted by default, nothing is archived
    # (`library/tools/reel_retirement.py`).
    assert sorted(survivors) == sorted([MASTER] + APPROVED)
    _only_probe_and_backups_deleted(
        resolve_project,
        ["Reel 03 - moment-3 (pre-rebuild backup)"])
    assert record["timelines_built"] == ["Reel 03 - moment-3"]
    assert record["staged_timelines"] == {}
    assert placed.call_count == 1
    assert placed.call_args[1]["timeline_name"] == (
        "Reel 03 - moment-3" + STAGING_SUFFIX)


# ── The guard, both directions ───────────────────────────────────────


def test_the_guard_refuses_a_timeline_that_was_never_planned():
    with pytest.raises(ReelBuildError) as refused:
        assert_deletion_scope(
            [FakeTimeline("Reel 03 - moment-3"), FakeTimeline("Reel 07 - other")],
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
    _only_probe_deleted(resolve_project)


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
    # A refused build probes nothing: the probe runs after the plan
    # validation, so a build that never starts never touches Resolve.
    assert resolve_project.deleted == []


# ── What `only` accepts, and what it refuses ─────────────────────────


def test_reel_numbers_refuses_a_name_rather_than_guessing_a_moment():
    from library.tools.reel_build import reel_numbers

    with pytest.raises(ReelBuildError, match="must be reel NUMBERS"):
        reel_numbers(["Reel 03 - moment-3"])
