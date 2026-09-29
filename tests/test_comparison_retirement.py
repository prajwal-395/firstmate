"""Scratch and comparison timelines end the way retired reels do.

The rule, in one line: per reel, one live comparison and one archived
one - everything the engine recorded beyond that retires (live) or is
collected (archived) on the next promotion touching the reel, guarded
exactly like the reel archive, never taking a sign-off, a hold, a
variant, a plan final or anything unrecorded.

Each half has a test that fails without it: the family classifier is
narrow (a deliverable wearing parens is not a comparison), the bound
is per BASE reel rather than per suffix (or every new suffix would be
a new allowance), ordering is newest-first off the version record the
round diff itself reads, and the census test below proves the count
stays bounded across two rounds instead of growing with them.
"""
from unittest.mock import MagicMock, patch

import pytest
from tests.promotion_test_helpers import no_a_roll_track_plans

from library.tools import comparison_retirement as comp
from library.tools import reel_retirement as retire
from library.tools import resolve_bin_layout as bins

REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"
OTHER = "Reel 28 - the-nail-salon-query-google-cant-answer"
BASELINE = f"{REEL} (baseline scratch)"
BATCH = f"{REEL} (batch-1050 check)"
OTHER_BASELINE = f"{OTHER} (baseline scratch)"
VARIANT = f"{REEL} (reaction-cutaway)"
MASTER = "GEO Podcast - Synced"

PLAN = (REEL, OTHER)


def rounds(*entries):
    """A `discover`-shaped version record: (round, names...)."""
    return [{"round": number, "reels": {name: {"rows": {}}
                                       for name in names}}
            for number, names in entries]


# ── The family ───────────────────────────────────────────────────







def test_a_retired_comparison_is_not_graded_as_a_deliverable():
    """The sweep already declines archived names, whatever the reel
    part carries inside - so a retired comparison stops being graded
    the moment it retires, exactly like a retired reel."""
    from library.tools.reel_conformance_verifier import grades_as_a_reel

    assert grades_as_a_reel(BASELINE)
    assert not grades_as_a_reel(retire.archived_name(BASELINE, 2))


# ── The ordering ─────────────────────────────────────────────────



# ── The lifecycle ────────────────────────────────────────────────

def test_one_live_and_one_archived_per_reel_not_per_round():
    """The whole answer to the clutter question, stated twice: live
    beyond the newest retires, archived beyond the newest is
    collected. Remove either bound and a project grows one timeline
    per reel per comparison round, which is the complaint."""
    live = [BASELINE, BATCH]
    by_round = {BASELINE: 1, BATCH: 2}
    archived = [retire.archived_name(BASELINE, n) for n in (1, 2, 3)]
    plan = comp.plan_collection(
        live + archived + [REEL], [REEL], plan_finals=PLAN,
        rounds_by_name=by_round)
    assert [entry["name"] for entry in plan["retire"]] == [BASELINE]
    assert f"{BATCH!r}" in plan["retire"][0]["why"]
    assert plan["collect"] == [retire.archived_name(BASELINE, 2),
                               retire.archived_name(BASELINE, 1)]
    kept = {entry["name"] for entry in plan["kept"]}
    assert kept == {BATCH, retire.archived_name(BASELINE, 3)}
    # And another reel's generations are not this one's business.
    scoped = comp.plan_collection(
        [OTHER_BASELINE], [REEL], plan_finals=PLAN,
        rounds_by_name={OTHER_BASELINE: 2})
    assert scoped["retire"] == [] and scoped["collect"] == []


def test_a_signed_off_comparison_is_never_collected_or_retired():
    names = [BASELINE, BATCH,
             retire.archived_name(BASELINE, 2),
             retire.archived_name(BASELINE, 1)]
    plan = comp.plan_collection(
        names, [REEL], plan_finals=PLAN,
        rounds_by_name={BASELINE: 1, BATCH: 2},
        signed_identities={BASELINE})
    assert plan["retire"] == []
    assert plan["collect"] == []
    whys = " ".join(entry["why"] for entry in plan["kept"])
    assert "sign-off" in whys


def test_a_sign_off_on_the_base_does_not_protect_comparisons():
    """The approved cut lives on the base timeline itself, which this
    rule never names, and the rows live in the round record. A base
    sign-off protecting every comparison would make every signed
    reel's comparisons immortal - the bound would end exactly where
    approvals begin."""
    names = [BASELINE, BATCH]
    plan = comp.plan_collection(
        names, [REEL], plan_finals=PLAN,
        rounds_by_name={BASELINE: 1, BATCH: 2},
        signed_identities={REEL})
    assert [entry["name"] for entry in plan["retire"]] == [BASELINE]


def test_a_held_comparison_is_never_retired():
    """The hold shields the timeline it names - a pending promotion
    decision stays put - but it does not shield an older generation
    the held one already superseded. That one retires like any
    other: still openable, in the archive, named."""
    plan = comp.plan_collection(
        [BASELINE, BATCH], [REEL], plan_finals=PLAN,
        rounds_by_name={BASELINE: 2, BATCH: 1},
        held_names={BASELINE})
    assert [entry["name"] for entry in plan["retire"]] == [BATCH]
    kept = {entry["name"]: entry["why"] for entry in plan["kept"]}
    assert set(kept) == {BASELINE}
    assert "hold" in kept[BASELINE]

    older_held = comp.plan_collection(
        [BASELINE, BATCH], [REEL], plan_finals=PLAN,
        rounds_by_name={BASELINE: 1, BATCH: 2},
        held_names={BASELINE})
    assert older_held["retire"] == []
    assert {entry["name"] for entry in older_held["kept"]} == {
        BASELINE, BATCH}


def test_an_unrecorded_comparison_is_always_kept():
    """No landed round in the record means either hand-made (the
    captain's own - never auto-touched) or a stamp failure (already
    reported where it happened). Either way it stays, and says why
    every round so a human can act."""
    plan = comp.plan_collection(
        [BASELINE, BATCH], [REEL], plan_finals=PLAN,
        rounds_by_name={BATCH: 2})
    assert plan["retire"] == []
    why = next(entry["why"] for entry in plan["kept"]
               if entry["name"] == BASELINE)
    assert "no landed round" in why




# ── The Resolve half ─────────────────────────────────────────────

class FakeTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetMediaPoolItem(self):
        return f"pool:{self._name}"

    def GetMarkers(self):
        return {}


class FakeProject:
    def __init__(self, timelines):
        self.timelines = list(timelines)
        self.pool = MagicMock()
        self.pool.DeleteTimelines.side_effect = self._delete
        self.deleted = []

    def _delete(self, timelines):
        for timeline in timelines:
            self.deleted.append(timeline.GetName())
            self.timelines.remove(timeline)
        return True

    def GetMediaPool(self):
        return self.pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def names(self):
        return [t.GetName() for t in self.timelines]


def _drive(project, project_folder, promoted, monkeypatch,
           recorded):
    monkeypatch.setattr(comp, "_plan_finals", lambda _folder: PLAN)
    monkeypatch.setattr("library.tools.versions.rounds.discover",
                        lambda _folder, *extra: recorded)
    return comp.collect_for_bases(project, project.pool,
                                  project_folder, promoted, MASTER)


def test_the_census_is_bounded_across_two_rounds(tmp_path,
                                                 monkeypatch):
    """The demonstration: round one lands a comparison per reel,
    round two lands the next generation, and the census after round
    two shows one live and one archived per reel - bounded, not
    growing. A third round collects the first archived generation
    under the scope guard."""
    first = f"{REEL} (round-1 comparison)"
    first_other = f"{OTHER} (round-1 comparison)"
    second = f"{REEL} (round-2 comparison)"
    project = FakeProject([FakeTimeline(MASTER),
                           FakeTimeline(REEL), FakeTimeline(OTHER),
                           FakeTimeline(first),
                           FakeTimeline(first_other)])

    round_one = _drive(project, str(tmp_path), [first, first_other],
                       monkeypatch,
                       rounds((1, (first, first_other))))
    assert round_one["refused"] == ""
    # Each reel's only comparison is its newest: nothing retires yet.
    assert round_one["retired"] == {}
    assert project.deleted == []

    project.timelines.append(FakeTimeline(second))
    round_two = _drive(project, str(tmp_path), [second],
                       monkeypatch,
                       rounds((1, (first, first_other)),
                              (2, (second,))))
    assert round_two["refused"] == ""
    assert round_two["retired"] == {
        first: retire.archived_name(first, 1)}
    assert project.deleted == []

    live = [name for name in project.names()
            if comp.is_comparison_timeline(name, PLAN)]
    assert live == [first_other, second]
    archived = [name for name in project.names()
                if retire.is_archived_timeline(name)]
    assert archived == [retire.archived_name(first, 1)]

    third = f"{REEL} (round-3 comparison)"
    project.timelines.append(FakeTimeline(third))
    round_three = _drive(project, str(tmp_path), [third],
                         monkeypatch,
                         rounds((1, (first, first_other)),
                                (2, (second,)),
                                (3, (third,))))
    assert round_three["refused"] == ""
    assert round_three["collected"] == [
        retire.archived_name(first, 1)]
    assert retire.archived_name(second, 2) in project.names()
    # Bounded: one live and one archived comparison of the reel, no
    # matter how many rounds landed.
    assert [name for name in project.names()
            if comp.is_comparison_timeline(name, PLAN)] == [
               first_other, third]


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
            project, project.pool,
            [retire.archived_name(BASELINE, 1)])
    assert project.deleted == []


def test_a_delete_resolve_declines_is_refused_not_reported_collected():
    """The live-demo defect, 2026-09-14
    (`docs/LIVE_DEMO_1107_COMPARISON_RETIREMENT.md`): `DeleteTimelines`
    returned falsy and the name was kept under `collected` anyway, while
    the census still showed it present. Fails on the old shape (no
    raise, name reported collected); passes on the new (refused, the
    timeline still present for the next build to plan again)."""
    class DecliningProject(FakeProject):
        def _delete(self, timelines):
            return False

    old = FakeTimeline(retire.archived_name(BASELINE, 1))
    new = FakeTimeline(retire.archived_name(BASELINE, 2))
    project = DecliningProject([FakeTimeline(BASELINE), new, old])
    with pytest.raises(comp.ComparisonRefused):
        comp._collect_archived(
            project, project.pool,
            [retire.archived_name(BASELINE, 1)])
    assert retire.archived_name(BASELINE, 1) in project.names()


def test_a_failed_read_is_a_refusal_not_an_empty_answer(tmp_path,
                                                        monkeypatch):
    """An unreadable holds file reads exactly like "nothing is
    protected" - so judging against it would condemn every held
    comparison, and the driver refuses instead."""
    monkeypatch.setattr(comp, "_plan_finals", lambda _folder: PLAN)
    monkeypatch.setattr(
        "library.tools.staging_holds.read_holds",
        MagicMock(side_effect=RuntimeError("torn write")))
    project = FakeProject([FakeTimeline(MASTER),
                           FakeTimeline(REEL), FakeTimeline(BASELINE)])
    report = comp.collect_for_bases(project, project.pool,
                                    str(tmp_path), [REEL], MASTER)
    assert "Nothing was moved" in report["refused"]
    assert project.names() == [MASTER, REEL, BASELINE]




# ── The promotion wiring ─────────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    yield


@pytest.fixture
def project_dir(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    return root


class FakeItem:
    def __init__(self, name, start, end):
        self._name = name
        self._start = start
        self._end = end

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start


class RowTimeline(FakeTimeline):
    def __init__(self, name):
        super().__init__(name)
        self._rows = {"video": [("Akshita",
                                 [FakeItem("clip", 0, 131)]),
                                ("Subtitles",
                                 [FakeItem("card", 0, 131)])],
                      "audio": []}

    def GetTrackCount(self, kind):
        return len(self._rows[kind])

    def GetTrackName(self, kind, index):
        return self._rows[kind][index - 1][0]

    def GetItemListInTrack(self, kind, index):
        return list(self._rows[kind][index - 1][1])


def test_promotion_retires_the_comparison_it_supersedes(project_dir,
                                                        monkeypatch):
    """End to end through `promote_staged_reels`: a suffix build
    promotes its new comparison, the older recorded comparison of
    the same reel retires to the archive under the round it was
    current for, the other reel's comparison is untouched, and the
    result carries the comparison report."""
    import json

    from library.tools.reel_build import promote_staged_reels

    fresh = f"{REEL} (round-2 comparison)"
    staging = RowTimeline(f"{fresh} (rebuild staging)")
    old = RowTimeline(BASELINE)
    other_old = RowTimeline(OTHER_BASELINE)
    resolve = FakeProject([RowTimeline(MASTER), RowTimeline(REEL),
                           RowTimeline(OTHER), staging, old,
                           other_old])
    staged_to_final = {fresh: staging.GetName()}
    (project_dir / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(json.dumps(
         {"built_reels": sorted(staged_to_final.values())}),
        encoding="utf-8")
    monkeypatch.setattr(comp, "_plan_finals", lambda _folder: PLAN)
    monkeypatch.setattr(
        "library.tools.versions.rounds.discover",
        lambda _folder, *extra: rounds((1, (BASELINE,
                                           OTHER_BASELINE)),))

    with patch("library.tools.resolve_locale."
               "scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve):
        result = promote_staged_reels(
            str(project_dir), "Mock Project", MASTER,
            staged_to_final, organise=False,
            track_plans=no_a_roll_track_plans(staged_to_final))

    assert result["comparison_retirement"]["refused"] == ""
    assert result["comparison_retirement"]["retired"] == {
        BASELINE: retire.archived_name(BASELINE, 1)}
    assert retire.archived_name(BASELINE, 1) in resolve.names()
    assert BASELINE not in resolve.names()
    assert fresh in resolve.names()
    assert OTHER_BASELINE in resolve.names()
    assert resolve.deleted == []
