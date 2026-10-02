"""A promotion DELETES what it replaced, unless asked to keep it.

Default: one timeline per reel, empty archive. Opt-in `retain`: one
archived generation per REEL (never per round). A signed-off generation
is never deleted or collected, and the deletion scope refuses a list
beyond the promoted reels. History: docs/evidence/reel_retirement.md.
"""
from __future__ import annotations
from unittest.mock import patch
import pytest
from library.tools import reel_replace_guard
from library.tools import reel_retirement as retire
from tests.promotion_test_helpers import (
    no_a_roll_track_plans,
    record_ren_owned_inventory,
)
from tests.resolve_double import FakeProject, FakeTimeline, TimelineItemSpec
from unittest.mock import MagicMock
from library.tools import comparison_retirement as comp
import json
from pathlib import Path
from library.tools import resolve_bin_layout as bins
from library.tools.execution import retire_empty_bins as retire_2
from library.tools.resolve_organization import (
    BIN_REELS,
    BIN_SOURCE,
    BIN_SUBTITLES,
    Artefact,
)
from tests.resolve_double import make_project


REEL = "Reel 09 - your-website-is-only-20-percent"
OTHER = "Reel 13 - the-accounting-firm"


@pytest.fixture
def fake_preservation_snapshots(monkeypatch):
    monkeypatch.setattr(
        reel_replace_guard,
        "full_timeline_snapshot",
        lambda timeline, _project, _folder=None: {
            "timeline": {
                "name": timeline.GetName(),
                "unique_id": None,
                "settings": {},
                "start_frame": 0,
                "end_frame": 0,
            },
            "items": [],
            "markers": [],
        },
    )


# ── The naming ───────────────────────────────────────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_an_archived_name_can_never_be_mistaken_for_the_live_cut():
    name = retire.archived_name(REEL, 3)
    assert name == f"{REEL} (archived round 003)"
    assert retire.is_archived_timeline(name)
    assert not retire.is_archived_timeline(REEL)
    # And the classifier is narrow: a reel the captain happened to name
    # with the word is untouched.
    assert not retire.is_archived_timeline("My archived reel")
    assert retire.parse_archived(name) == (REEL, 3)


# ── The lifecycle ────────────────────────────────────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_the_archive_holds_one_generation_per_reel_not_one_per_round():
    """The whole answer to the clutter question. Bounded by the number
    of REELS, so it cannot grow with time. Remove the bound and a
    project accumulates one archived timeline per reel per round, which
    is the complaint, one round later."""
    names = [retire.archived_name(REEL, n) for n in (1, 2, 3)] + [
        retire.archived_name(OTHER, 1)
    ]
    plan = retire.plan_collection(names, [REEL])
    assert plan["collect"] == [
        retire.archived_name(REEL, 2),
        retire.archived_name(REEL, 1),
    ]
    # The newest is kept - it is what the round diff compares against.
    assert [kept["name"] for kept in plan["kept"]] == [retire.archived_name(REEL, 3)]
    # And another reel's generations are not this promotion's business.
    assert retire.archived_name(OTHER, 1) not in plan["collect"]
    # `retained=0` - the default-delete path - collects every unsigned
    # generation of a promoted reel, so the archive ends empty for it.
    plan = retire.plan_collection(names, [REEL], retained=0)
    assert sorted(plan["collect"]) == sorted(names[:3])
    assert [kept["name"] for kept in plan["kept"]] == []


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_a_signed_off_generation_is_never_collected():
    """The cut the captain approved is the one thing collection may not
    take. Remove this and the retention bound deletes the only copy of
    an approved reel."""
    names = [retire.archived_name(REEL, n) for n in (1, 2, 3)]
    plan = retire.plan_collection(names, [REEL], signed_off_reels={REEL})
    assert plan["collect"] == []
    whys = " ".join(kept["why"] for kept in plan["kept"])
    assert "sign-off" in whys


# ── The Resolve half ─────────────────────────────────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_retiring_renames_and_files_and_deletes_nothing():
    backup = FakeTimeline(f"{REEL} (pre-rebuild backup)")
    project = FakeProject([backup])
    report = retire.retire_timelines(
        project, project.GetMediaPool(), {REEL: backup}, {REEL: 2}
    )
    assert report["archived"] == {REEL: retire.archived_name(REEL, 2)}
    assert backup.GetName() == retire.archived_name(REEL, 2)
    assert project.deleted == []
    assert len(project.GetMediaPool().move_calls) == 1


# ── The default delete ───────────────────────────────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_the_deletion_scope_refuses_a_list_beyond_the_promoted_reels():
    """`assert_deletion_scope` is asked of the list about to be deleted
    (the captain's 2026-09-06 ruling). A wrong list refuses rather than
    widening - the reason the new default path is allowed to delete."""
    from library.tools.reel_build import (
        ReelBuildError,
        assert_deletion_scope,
        backup_name,
    )

    live = FakeTimeline(REEL)
    backup = FakeTimeline(backup_name(REEL))
    with pytest.raises(ReelBuildError):
        assert_deletion_scope([live, backup], {backup.GetName()})


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_collection_only_ever_touches_archived_names():
    """`assert_deletion_scope` is asked of the list about to be deleted
    (the captain's 2026-09-06 ruling). A live reel in the project is
    never a candidate, whatever the plan said."""
    live = FakeTimeline(REEL)
    newest = FakeTimeline(retire.archived_name(REEL, 3))
    older = FakeTimeline(retire.archived_name(REEL, 2))
    project = FakeProject([live, newest, older])
    record = retire.collect_superseded(
        project, project.GetMediaPool(), project.names(), [REEL]
    )
    assert record["collected"] == [retire.archived_name(REEL, 2)]
    assert REEL in project.names()
    assert retire.archived_name(REEL, 3) in project.names()


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_a_delete_resolve_declines_is_refused_not_reported_collected():
    """The live-demo defect, 2026-09-14
    (`docs/LIVE_DEMO_1107_COMPARISON_RETIREMENT.md`): `DeleteTimelines`
    returned falsy and the name was kept under `collected` anyway, while
    the census still showed it present. Fails on the old shape (no
    raise, name reported collected); passes on the new (refused, the
    timeline still present for the next build to plan again)."""
    live = FakeTimeline(REEL)
    newest = FakeTimeline(retire.archived_name(REEL, 3))
    older = FakeTimeline(retire.archived_name(REEL, 2))
    project = FakeProject([live, newest, older], delete_ok=False)
    with pytest.raises(retire.RetirementRefused):
        retire.collect_superseded(
            project, project.GetMediaPool(), project.names(), [REEL]
        )
    assert retire.archived_name(REEL, 2) in project.names()


# ── The promotion, end to end ────────────────────────────────────


@pytest.fixture
def mock_dvr(stub_resolve_script):
    yield


@pytest.fixture
def review_project(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


MASTER = "Podcast - Synced"


def _rows(name):
    return [("Akshita", [TimelineItemSpec(f"{name} clip", 0, 100)])]


def _pair(final):
    return (
        FakeTimeline(final, video=_rows(final)),
        FakeTimeline(f"{final} (rebuild staging)", video=_rows(final)),
    )


def _promote(
    resolve,
    project,
    staged_to_final,
    retain=None,
    supersede=None,
    prior_ren_inventory=False,
):
    import json

    from library.tools.reel_build import promote_staged_reels

    (project / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}), encoding="utf-8"
    )
    if prior_ren_inventory:
        record_ren_owned_inventory(project, resolve)
    with (
        patch("library.tools.resolve_locale.scriptapp_preserving_locale"),
        patch("library.tools.reel_build.resolve_project_exactly", return_value=resolve),
    ):
        return promote_staged_reels(
            str(project),
            "Mock Project",
            MASTER,
            staged_to_final,
            organise=False,
            retain=retain,
            supersede=supersede,
            track_plans=no_a_roll_track_plans(staged_to_final),
        )


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_default_promotion_leaves_one_timeline_per_reel(review_project):
    """The new default, end to end: the backup is deleted, nothing is
    renamed into the archive, and each final appears exactly once."""
    from library.tools.reel_build import backup_name

    original, staging = _pair(REEL)
    other, other_staging = _pair(OTHER)
    resolve = FakeProject(
        [FakeTimeline(MASTER), original, staging, other, other_staging]
    )

    promoted = _promote(
        resolve,
        review_project,
        {REEL: staging.GetName(), OTHER: other_staging.GetName()},
    )

    assert sorted(promoted["promoted"]) == sorted([REEL, OTHER])
    names = resolve.names()
    assert names.count(REEL) == 1
    assert names.count(OTHER) == 1
    # The backups are gone - deleted, not renamed - and the archive
    # was never touched.
    assert promoted["retirement"]["deleted"] == sorted(
        [backup_name(REEL), backup_name(OTHER)]
    )
    assert promoted["retirement"]["archived"] == {}
    assert promoted["retirement"]["collected"] == []
    assert resolve.deleted == sorted([backup_name(REEL), backup_name(OTHER)])
    assert not [name for name in names if retire.is_archived_timeline(name)]
    assert not [name for name in names if name.endswith("(pre-rebuild backup)")]
    assert "Deleted 2 superseded backup(s)" in retire.render(promoted["retirement"])


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_explicit_retain_keeps_exactly_one_generation(review_project):
    """The reachable path: naming the reel retires its backup into the
    archive, and the retention bound still holds - one generation, not
    one per round."""
    from library.tools.reel_build import backup_name

    original, staging = _pair(REEL)
    legacy = FakeTimeline(retire.archived_name(REEL, 1))
    resolve = FakeProject([FakeTimeline(MASTER), original, staging, legacy])
    promoted = _promote(
        resolve,
        review_project,
        {REEL: staging.GetName()},
        retain=[REEL],
        prior_ren_inventory=True,
    )

    # No round was ever stamped in this project, so the retired
    # version is labelled with the current round - colliding with the
    # legacy generation, which gains a `.2` sibling rather than sharing
    # its name.
    expected = retire.archived_name(REEL, 1, taken={retire.archived_name(REEL, 1)})
    assert promoted["retirement"]["deleted"] == []
    assert promoted["retirement"]["archived"] == {REEL: expected}
    assert backup_name(REEL) not in resolve.names()
    assert expected in resolve.names()
    # The older generation is collected under the bound; the newest -
    # the one just retired - is what a round diff compares against.
    assert promoted["retirement"]["collected"] == [retire.archived_name(REEL, 1)]
    assert [kept["name"] for kept in promoted["retirement"]["kept"]] == [expected]


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_a_signed_off_backup_retires_on_the_default_path(review_project):
    """A sign-off is the captain approving a specific cut, so deleting
    its only copy is exactly what "unless i explicitly ask for
    otherwise" does not cover: the backup retires even with no
    `retain` declaration."""
    from library.tools import reel_signoff as signoff

    signoff.sign_off(str(review_project), REEL, note="ships")
    original, staging = _pair(REEL)
    resolve = FakeProject([FakeTimeline(MASTER), original, staging])

    promoted = _promote(
        resolve, review_project, {REEL: staging.GetName()}, supersede=[REEL]
    )

    assert promoted["promoted"] == [REEL]
    assert REEL in promoted["superseded_signoffs"]
    assert promoted["retirement"]["deleted"] == []
    assert promoted["retirement"]["archived"] == {REEL: retire.archived_name(REEL, 1)}
    assert retire.archived_name(REEL, 1) in resolve.names()
    # And the sign-off itself is superseded, never deleted.
    assert signoff.signoff_for(str(review_project), REEL) is None
    history = signoff.read_signoffs(str(review_project))["superseded"]
    assert history[0]["note"] == "ships"
    assert history[0]["ended_by"] == "superseded"


# --------------------------------------------------------------------------
# From test_comparison_retirement.py
#
# Scratch and comparison timelines end the way retired reels do.
#
# The rule, in one line: per reel, one live comparison and one archived
# one - everything the engine recorded beyond that retires (live) or is
# collected (archived) on the next promotion touching the reel, guarded
# exactly like the reel archive, never taking a sign-off, a hold, a
# variant, a plan final or anything unrecorded.
#
# Each half has a test that fails without it: the family classifier is
# narrow (a deliverable wearing parens is not a comparison), the bound
# is per BASE reel rather than per suffix (or every new suffix would be
# a new allowance), ordering is newest-first off the version record the
# round diff itself reads, and the census test below proves the count
# stays bounded across two rounds instead of growing with them.

REEL_2 = "Reel 13 - the-accounting-firm-ai-called-healthcare"
OTHER_2 = "Reel 28 - the-nail-salon-query-google-cant-answer"
BASELINE = f"{REEL_2} (baseline scratch)"
BATCH = f"{REEL_2} (batch-1050 check)"
OTHER_BASELINE = f"{OTHER_2} (baseline scratch)"
VARIANT = f"{REEL_2} (reaction-cutaway)"
MASTER_2 = "GEO Podcast - Synced"

PLAN = (REEL_2, OTHER_2)


def rounds(*entries):
    """A `discover`-shaped version record: (round, names...)."""
    return [
        {"round": number, "reels": {name: {"rows": {}} for name in names}}
        for number, names in entries
    ]


# ── The lifecycle ────────────────────────────────────────────────


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_one_live_and_one_archived_per_reel_not_per_round():
    """The whole answer to the clutter question, stated twice: live
    beyond the newest retires, archived beyond the newest is
    collected. Remove either bound and a project grows one timeline
    per reel per comparison round, which is the complaint."""
    live = [BASELINE, BATCH]
    by_round = {BASELINE: 1, BATCH: 2}
    archived = [retire.archived_name(BASELINE, n) for n in (1, 2, 3)]
    plan = comp.plan_collection(
        live + archived + [REEL_2], [REEL_2], plan_finals=PLAN, rounds_by_name=by_round
    )
    assert [entry["name"] for entry in plan["retire"]] == [BASELINE]
    assert f"{BATCH!r}" in plan["retire"][0]["why"]
    assert plan["collect"] == [
        retire.archived_name(BASELINE, 2),
        retire.archived_name(BASELINE, 1),
    ]
    kept = {entry["name"] for entry in plan["kept"]}
    assert kept == {BATCH, retire.archived_name(BASELINE, 3)}
    # And another reel's generations are not this one's business.
    scoped = comp.plan_collection(
        [OTHER_BASELINE], [REEL_2], plan_finals=PLAN, rounds_by_name={OTHER_BASELINE: 2}
    )
    assert scoped["retire"] == [] and scoped["collect"] == []


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_only_a_sign_off_on_the_comparison_itself_protects_it():
    names = [
        BASELINE,
        BATCH,
        retire.archived_name(BASELINE, 2),
        retire.archived_name(BASELINE, 1),
    ]
    plan = comp.plan_collection(
        names,
        [REEL_2],
        plan_finals=PLAN,
        rounds_by_name={BASELINE: 1, BATCH: 2},
        signed_identities={BASELINE},
    )
    assert plan["retire"] == []
    assert plan["collect"] == []
    whys = " ".join(entry["why"] for entry in plan["kept"])
    assert "sign-off" in whys
    # A sign-off on the BASE does not protect its comparisons: the
    # approved cut lives on the base timeline, which this rule never
    # names, or the bound would end exactly where approvals begin.
    plan = comp.plan_collection(
        names,
        [REEL_2],
        plan_finals=PLAN,
        rounds_by_name={BASELINE: 1, BATCH: 2},
        signed_identities={REEL_2},
    )
    assert [entry["name"] for entry in plan["retire"]] == [BASELINE]


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_a_held_comparison_is_never_retired():
    """The hold shields the timeline it names - a pending promotion
    decision stays put - but it does not shield an older generation
    the held one already superseded. That one retires like any
    other: still openable, in the archive, named."""
    plan = comp.plan_collection(
        [BASELINE, BATCH],
        [REEL_2],
        plan_finals=PLAN,
        rounds_by_name={BASELINE: 2, BATCH: 1},
        held_names={BASELINE},
    )
    assert [entry["name"] for entry in plan["retire"]] == [BATCH]
    kept = {entry["name"]: entry["why"] for entry in plan["kept"]}
    assert set(kept) == {BASELINE}
    assert "hold" in kept[BASELINE]

    older_held = comp.plan_collection(
        [BASELINE, BATCH],
        [REEL_2],
        plan_finals=PLAN,
        rounds_by_name={BASELINE: 1, BATCH: 2},
        held_names={BASELINE},
    )
    assert older_held["retire"] == []
    assert {entry["name"] for entry in older_held["kept"]} == {BASELINE, BATCH}


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_an_unrecorded_comparison_is_always_kept():
    """No landed round in the record means either hand-made (the
    captain's own - never auto-touched) or a stamp failure (already
    reported where it happened). Either way it stays, and says why
    every round so a human can act."""
    plan = comp.plan_collection(
        [BASELINE, BATCH], [REEL_2], plan_finals=PLAN, rounds_by_name={BATCH: 2}
    )
    assert plan["retire"] == []
    why = next(entry["why"] for entry in plan["kept"] if entry["name"] == BASELINE)
    assert "no landed round" in why


# ── The Resolve half ─────────────────────────────────────────────


def _drive(project, project_folder, promoted, monkeypatch, recorded):
    monkeypatch.setattr(comp, "_plan_finals", lambda _folder: PLAN)
    monkeypatch.setattr(
        "library.tools.versions.rounds.discover", lambda _folder, *extra: recorded
    )
    return comp.collect_for_bases(
        project, project.GetMediaPool(), project_folder, promoted, MASTER_2
    )


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_the_census_is_bounded_across_two_rounds(tmp_path, monkeypatch):
    """The demonstration: round one lands a comparison per reel,
    round two lands the next generation, and the census after round
    two shows one live and one archived per reel - bounded, not
    growing. A third round collects the first archived generation
    under the scope guard."""
    first = f"{REEL_2} (round-1 comparison)"
    first_other = f"{OTHER_2} (round-1 comparison)"
    second = f"{REEL_2} (round-2 comparison)"
    project = FakeProject(
        [
            FakeTimeline(MASTER_2),
            FakeTimeline(REEL_2),
            FakeTimeline(OTHER_2),
            FakeTimeline(first),
            FakeTimeline(first_other),
        ]
    )

    round_one = _drive(
        project,
        str(tmp_path),
        [first, first_other],
        monkeypatch,
        rounds((1, (first, first_other))),
    )
    assert round_one["refused"] == ""
    # Each reel's only comparison is its newest: nothing retires yet.
    assert round_one["retired"] == {}
    assert project.deleted == []

    project.timelines.append(FakeTimeline(second))
    round_two = _drive(
        project,
        str(tmp_path),
        [second],
        monkeypatch,
        rounds((1, (first, first_other)), (2, (second,))),
    )
    assert round_two["refused"] == ""
    assert round_two["retired"] == {first: retire.archived_name(first, 1)}
    assert project.deleted == []

    live = [name for name in project.names() if comp.is_comparison_timeline(name, PLAN)]
    assert live == [first_other, second]
    archived = [name for name in project.names() if retire.is_archived_timeline(name)]
    assert archived == [retire.archived_name(first, 1)]

    third = f"{REEL_2} (round-3 comparison)"
    project.timelines.append(FakeTimeline(third))
    round_three = _drive(
        project,
        str(tmp_path),
        [third],
        monkeypatch,
        rounds((1, (first, first_other)), (2, (second,)), (3, (third,))),
    )
    assert round_three["refused"] == ""
    assert round_three["collected"] == [retire.archived_name(first, 1)]
    assert retire.archived_name(second, 2) in project.names()
    # Bounded: one live and one archived comparison of the reel, no
    # matter how many rounds landed.
    assert [
        name for name in project.names() if comp.is_comparison_timeline(name, PLAN)
    ] == [first_other, third]


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_a_guard_refusal_stops_and_reports(tmp_path, monkeypatch):
    """The scope guard is asked of the list about to be deleted. A
    selection that smuggles a live name refuses rather than widening
    - and the refusal is recorded, with nothing deleted."""
    old = FakeTimeline(retire.archived_name(BASELINE, 1))
    new = FakeTimeline(retire.archived_name(BASELINE, 2))
    live = FakeTimeline(BASELINE)
    project = FakeProject([live, old, new])

    from library.tools import reel_build

    real_lookup = reel_build.timelines_to_replace

    def smuggled(project_, wanted):
        return real_lookup(project_, wanted) + [live]

    monkeypatch.setattr(reel_build, "timelines_to_replace", smuggled)
    with pytest.raises(reel_build.ReelBuildError):
        comp._collect_archived(
            project, project.GetMediaPool(), [retire.archived_name(BASELINE, 1)]
        )
    assert project.deleted == []


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_a_delete_resolve_declines_is_refused_not_reported_collected_2():
    """The live-demo defect, 2026-09-14
    (`docs/LIVE_DEMO_1107_COMPARISON_RETIREMENT.md`): `DeleteTimelines`
    returned falsy and the name was kept under `collected` anyway, while
    the census still showed it present. Fails on the old shape (no
    raise, name reported collected); passes on the new (refused, the
    timeline still present for the next build to plan again)."""
    old = FakeTimeline(retire.archived_name(BASELINE, 1))
    new = FakeTimeline(retire.archived_name(BASELINE, 2))
    project = FakeProject([FakeTimeline(BASELINE), new, old], delete_ok=False)
    with pytest.raises(comp.ComparisonRefused):
        comp._collect_archived(
            project, project.GetMediaPool(), [retire.archived_name(BASELINE, 1)]
        )
    assert retire.archived_name(BASELINE, 1) in project.names()


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_a_failed_read_is_a_refusal_not_an_empty_answer(tmp_path, monkeypatch):
    """An unreadable holds file reads exactly like "nothing is
    protected" - so judging against it would condemn every held
    comparison, and the driver refuses instead."""
    monkeypatch.setattr(comp, "_plan_finals", lambda _folder: PLAN)
    monkeypatch.setattr(
        "library.tools.staging_holds.read_holds",
        MagicMock(side_effect=RuntimeError("torn write")),
    )
    project = FakeProject(
        [FakeTimeline(MASTER_2), FakeTimeline(REEL_2), FakeTimeline(BASELINE)]
    )
    report = comp.collect_for_bases(
        project, project.GetMediaPool(), str(tmp_path), [REEL_2], MASTER_2
    )
    assert "Nothing was moved" in report["refused"]
    assert project.names() == [MASTER_2, REEL_2, BASELINE]


# ── The promotion wiring ─────────────────────────────────────────


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    return root


def _row_timeline(name):
    return FakeTimeline(
        name,
        video=[
            ("Akshita", [TimelineItemSpec("clip", 0, 131)]),
            ("Subtitles", [TimelineItemSpec("card", 0, 131)]),
        ],
    )


@pytest.mark.usefixtures("fake_preservation_snapshots")
@pytest.mark.usefixtures("mock_dvr")
def test_promotion_retires_the_comparison_it_supersedes(project_dir, monkeypatch):
    """End to end through `promote_staged_reels`: a suffix build
    promotes its new comparison, the older recorded comparison of
    the same reel retires to the archive under the round it was
    current for, the other reel's comparison is untouched, and the
    result carries the comparison report."""
    import json

    from library.tools.reel_build import promote_staged_reels

    fresh = f"{REEL_2} (round-2 comparison)"
    staging = _row_timeline(f"{fresh} (rebuild staging)")
    old = _row_timeline(BASELINE)
    other_old = _row_timeline(OTHER_BASELINE)
    resolve = FakeProject(
        [
            _row_timeline(MASTER_2),
            _row_timeline(REEL_2),
            _row_timeline(OTHER_2),
            staging,
            old,
            other_old,
        ]
    )
    staged_to_final = {fresh: staging.GetName()}
    (project_dir / "pipeline_output" / "review" / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}), encoding="utf-8"
    )
    from library.tools import plan_provenance

    inventory = reel_replace_guard.timeline_inventory(resolve)
    operation = plan_provenance.begin_timeline_inventory(
        str(project_dir / "pipeline_output" / "review"),
        "prior test build",
        inventory,
        ren_created_names={entry["name"] for entry in inventory},
    )
    plan_provenance.finish_timeline_inventory(
        str(project_dir / "pipeline_output" / "review"), operation, inventory
    )
    monkeypatch.setattr(comp, "_plan_finals", lambda _folder: PLAN)
    monkeypatch.setattr(
        "library.tools.versions.rounds.discover",
        lambda _folder, *extra: rounds(
            (1, (BASELINE, OTHER_BASELINE)),
        ),
    )

    with (
        patch("library.tools.resolve_locale.scriptapp_preserving_locale"),
        patch("library.tools.reel_build.resolve_project_exactly", return_value=resolve),
    ):
        result = promote_staged_reels(
            str(project_dir),
            "Mock Project",
            MASTER_2,
            staged_to_final,
            organise=False,
            track_plans=no_a_roll_track_plans(staged_to_final),
        )

    assert result["comparison_retirement"]["refused"] == ""
    assert result["comparison_retirement"]["retired"] == {
        BASELINE: retire.archived_name(BASELINE, 1)
    }
    assert retire.archived_name(BASELINE, 1) in resolve.names()
    assert BASELINE not in resolve.names()
    assert fresh in resolve.names()
    assert OTHER_BASELINE in resolve.names()
    assert resolve.deleted == []


# --------------------------------------------------------------------------
# From test_retire_empty_shells.py
#
# Retiring the migration's empty shells, and nothing else.
#
# The live migration emptied the legacy bins and left them standing beside the
# numbered scheme (`Reels`, `Reel subtitles`, `Subtitles`, ...). To the captain
# that IS disorganised. The no-delete rule exists so nothing is ever stranded;
# an EMPTY bin strands nothing, so retiring a provably empty legacy shell is
# safe and leaving it is the complaint.
#
# Every gate here is proven in BOTH directions (AGENTS.md 10.4): a retirement
# that must happen and one that must not, on the same shape.

MASTER_3 = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"


def clip(item_id, name, *, path, placed_by=(), folder=()):
    return Artefact(
        item_id=item_id,
        name=name,
        kind="clip",
        file_path=path,
        placed_by=tuple(placed_by),
        folder_path=tuple(folder),
    )


def timeline(item_id, name, *, folder=()):
    return Artefact(
        item_id=item_id,
        name=name,
        kind="timeline",
        file_path="",
        placed_by=(),
        folder_path=tuple(folder),
    )


def names(plan):
    return ["/".join(e["path"]) for e in plan]


# ------------------------------------------------------- the pure plan


def test_plan_retires_exactly_the_provably_empty_pipeline_shells():
    """One row per case, each proven in BOTH directions: what must
    retire and what must stay, on the same shape. Every retirement says
    why, naming the scheme that superseded it."""
    from library.tools.resolve_organization import BIN_UNPLACED, plan_retirements

    master = timeline("t-master", MASTER_3)
    live = timeline("t-live", "Reel 09 - slug v003", folder=(BIN_REELS, "Current plan"))
    rows = [
        # The captain's complaint, literally: legacy tops standing empty
        # beside the numbered scheme.
        ("emptied legacy tops", [master],
         [("Reels",), ("Reel subtitles",), ("Subtitles",), (BIN_REELS,), (BIN_SUBTITLES,)],
         {}, {"Reels", "Reel subtitles", "Subtitles"}, {BIN_REELS, BIN_SUBTITLES}),
        ("a legacy bin holding anything is not a shell",
         [master, clip("c-cam", "cam.mov", path="/elsewhere/cam.mov", folder=("Subtitles",))],
         [("Subtitles",), (BIN_SOURCE,)], {}, set(), {"Subtitles"}),
        # A shell whose only children are emptied per-reel leaves.
        ("empty sub-bins under a shell",
         [master],
         [("Reel subtitles",), ("Reel subtitles", "Reel 01 - live"),
          ("Reel subtitles", "Not placed on any timeline")],
         {"timeline_names": ["Reel 01 - live"]},
         {"Reel subtitles", "Reel subtitles/Reel 01 - live",
          "Reel subtitles/Not placed on any timeline"}, set()),
        # A bin in neither scheme is the captain's.
        ("captain-made bins", [master], [("My selects",), ("VOX test",), ("Reels",)],
         {}, {"Reels"}, {"My selects", "VOX test"}),
        # `Reels/Fully approved` is the captain's tier; its parent stays.
        ("captain's tier blocks its parent", [master], [("Reels",), ("Reels", "Fully approved")],
         {}, set(), {"Reels", "Reels/Fully approved"}),
        # Nothing says the pipeline made `my picks`, so it and its parent stay.
        ("unknown leaf under a legacy top", [master],
         [("Reel subtitles",), ("Reel subtitles", "my picks")],
         {}, set(), {"Reel subtitles", "Reel subtitles/my picks"}),
        # Empty canonical per-reel bins naming nothing live retire; the
        # live reel's bin, the canonical tops and the unplaced bin stay.
        ("canonical per-reel bins", [master, live],
         [(BIN_SUBTITLES,), (BIN_SUBTITLES, "Reel 09 - slug (staging)"),
          (BIN_SUBTITLES, "Reel 09 - slug v003"), (BIN_SUBTITLES, BIN_UNPLACED),
          (bins.MOTION_GRAPHICS_BIN,), (bins.MOTION_GRAPHICS_BIN, "Reel 04 - deleted")],
         {}, {f"{BIN_SUBTITLES}/Reel 09 - slug (staging)",
              f"{bins.MOTION_GRAPHICS_BIN}/Reel 04 - deleted"},
         {BIN_SUBTITLES, f"{BIN_SUBTITLES}/Reel 09 - slug v003",
          f"{BIN_SUBTITLES}/{BIN_UNPLACED}", bins.MOTION_GRAPHICS_BIN}),
    ]
    for label, artefacts, tree, kwargs, retire_set, keep_set in rows:
        plan = plan_retirements(artefacts, tree, **kwargs)
        retired = names(plan)
        assert set(retired) == retire_set, label
        assert not keep_set & set(retired), label
        assert all(entry["why"].strip() for entry in plan), label
    # Children before parents.
    plan = plan_retirements([master], rows[2][2], **rows[2][3])
    ordered = names(plan)
    assert ordered.index("Reel subtitles/Reel 01 - live") < ordered.index(
        "Reel subtitles"
    )
    # The why names the scheme that superseded the bin.
    by_name = {
        "/".join(e["path"]): e["why"]
        for e in plan_retirements([master], [("Reels",), ("V1",)])
    }
    assert bins.REELS_BIN in by_name["Reels"]
    assert bins.SOURCE_BIN in by_name["V1"]


def test_a_nonempty_canonical_orphan_stays_and_says_so():
    """An orphaned per-reel bin that still holds something is not a
    shell - the census says keep, with the reason, rather than going
    quiet about it."""
    from library.tools.resolve_organization import (
        plan_retirements,
        render_bin_census,
    )
    from library.tools.resolve_organization import BIN_UNPLACED

    artefacts = [
        timeline("t-master", MASTER_3),
        clip(
            "c-old",
            "old.mov",
            path=f"{PROJECT_ROOT}/pipeline_output/old.mov",
            placed_by=["Reel 04 - deleted"],
            folder=(BIN_SUBTITLES, "Reel 04 - deleted"),
        ),
    ]
    tree = [
        (BIN_SUBTITLES,),
        (BIN_SUBTITLES, "Reel 04 - deleted"),
        (BIN_SUBTITLES, BIN_UNPLACED),
    ]
    plan = plan_retirements(artefacts, tree)
    assert names(plan) == []
    text = render_bin_census(artefacts, tree, plan)
    assert "keep" in text
    assert f"{BIN_SUBTITLES}/Reel 04 - deleted (1 item(s))" in text


# ------------------------------------------------------- the executor


def empty_pool_with_shells():
    project = make_project("Fake")
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    shells = pool.AddSubFolder(root, "Reels")
    pool.AddSubFolder(shells, "Unrecorded")
    pool.AddSubFolder(root, "My selects")
    return project, root


def test_retiring_removes_the_shells_keeps_the_captains_bins_and_reverts(tmp_path):
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements

    proj, _root = empty_pool_with_shells()
    artefacts, _, _, _ = read_pool(proj)
    tree = retire_2.read_bin_tree(proj)
    plan = plan_retirements(artefacts, list(tree))
    journal_path = str(tmp_path / "retire.json")
    result = retire_2.retire_bins(proj, plan, journal_path)
    remaining = sorted("/".join(p) for p in retire_2.read_bin_tree(proj))
    assert remaining == ["My selects"]
    assert sorted(result["retired"]) == [
        "Reels",
        "Reels/Unrecorded",
    ]
    assert result["journal_path"] == journal_path
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    assert sorted(r["path"] for r in journal["retired"]) == [
        "Reels",
        "Reels/Unrecorded",
    ]
    # And it reverts by recreating the empty shells from the journal.
    undone = retire_2.revert(proj, journal_path)
    assert sorted(undone["recreated"]) == ["Reels", "Reels/Unrecorded"]
    assert sorted("/".join(p) for p in retire_2.read_bin_tree(proj)) == [
        "My selects",
        "Reels",
        "Reels/Unrecorded",
    ]


def test_a_bin_that_gained_an_item_between_plan_and_apply_refuses(tmp_path):
    """The two-pass shape that caught the migration stranding nothing:
    plan read-only, then re-read and reconcile before touching."""
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements

    proj, root = empty_pool_with_shells()
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire_2.read_bin_tree(proj)))
    pool = proj.GetMediaPool()
    shells = next(s for s in root.GetSubFolderList() if s.GetName() == "Reels")
    pool.SetCurrentFolder(shells)
    pool.ImportMedia(["/elsewhere/x.mov"])
    with pytest.raises(retire_2.RetirementRefused, match="no longer empty"):
        retire_2.retire_bins(proj, plan, str(tmp_path / "retire.json"))
    assert "Reels" in ["/".join(p) for p in retire_2.read_bin_tree(proj)]


def test_a_deletefolders_refusal_stops_the_run_and_keeps_the_bin(tmp_path):
    proj, _root = empty_pool_with_shells()
    plan = [
        {"path": ("Reels", "Unrecorded"), "why": "test"},
        {"path": ("Reels",), "why": "test"},
    ]
    proj.GetMediaPool().delete_folders_ok = False
    with pytest.raises(retire_2.RetirementRefused, match="DeleteFolders"):
        retire_2.retire_bins(proj, plan, str(tmp_path / "retire.json"))
    assert "Reels" in ["/".join(p) for p in retire_2.read_bin_tree(proj)]
