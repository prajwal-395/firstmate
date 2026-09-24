"""Choosing between two versions of a reel is an ACT with a consequence.

Before this, two versions of a reel could be BUILT side by side and then
nothing: they could only be compared by eye, choosing one was not a
command, the loser sat in the captain's bin under its comparison name,
and the round said nothing about a decision having been made.

Three things are proved here, and they pull against each other exactly
the way retirement's two do:

1. the choice is REAL - the chosen variant becomes the reel, the version
   that held the name is retired, the loser is archived and the round
   records which won and why;
2. variants cannot ACCUMULATE - the retention bound is per REEL, so the
   archive is bounded by how many reels the project has rather than by
   how often the captain compares. This is the constraint that has
   bitten four times;
3. a SIGN-OFF still means what it meant - a signed-off reel is not
   replaced by accident, and an approval does not lapse because a
   timeline was renamed.

Every test fails if its mechanism is removed.
"""
import json
import pathlib
from unittest.mock import MagicMock

import pytest

from library.tools import reel_retirement as retire
from library.tools.versions import variants as choice

REEL = "Reel 09 - your-website-is-only-20-percent (final)"
JCUT = f"{REEL} (j-cut)"
CUTAWAY = f"{REEL} (reaction-cutaway)"


def rows(count=2, frames=100, name="clip"):
    return {"video:V1": {"media_type": "video", "index": 1, "name": "V1",
                         "items": [{"name": f"{name}-{i}", "start": i * 10,
                                    "end": i * 10 + 5, "duration": 5}
                                   for i in range(count)],
                         "count": count, "frames": frames}}


@pytest.fixture()
def project_folder(tmp_path):
    folder = tmp_path / "geo-podcast"
    (folder / "pipeline_output" / "review").mkdir(parents=True)
    return str(folder)


# ── What is alive ────────────────────────────────────────────────

def test_a_built_variant_records_what_it_contains(project_folder):
    """The rows are the payload that makes the comparison free, and
    that outlives the timeline they describe."""
    choice.record_build(project_folder, 9, REEL, " (j-cut)", rows(2),
                        watch="the join under the question")
    entry = choice.build_for(project_folder, 9, " (j-cut)")
    assert entry["timeline"] == JCUT
    assert entry["watch"] == "the join under the question"
    assert entry["rows"]["video:V1"]["count"] == 2
    written = pathlib.Path(choice.builds_path_for(project_folder))
    assert json.loads(written.read_text(
        encoding="utf-8"))["builds"][JCUT]["reel"] == 9


def test_rebuilding_a_variant_replaces_its_record(project_folder):
    """Two row snapshots for one live timeline is the disagreement
    AGENTS.md 10.1 keeps catching."""
    choice.record_build(project_folder, 9, REEL, " (j-cut)", rows(2))
    choice.record_build(project_folder, 9, REEL, " (j-cut)", rows(7))
    assert len(choice.builds_for_reel(project_folder, 9)) == 1
    assert choice.build_for(
        project_folder, 9, " (j-cut)")["rows"]["video:V1"]["count"] == 7


# ── COMPARED, off disk ───────────────────────────────────────────

def test_two_variants_are_compared_without_resolve(project_folder):
    choice.record_build(project_folder, 9, REEL, " (j-cut)", rows(2),
                        watch="the join")
    choice.record_build(project_folder, 9, REEL, " (reaction-cutaway)",
                        rows(4), watch="Akshita's reaction")
    diff = choice.compare(project_folder, 9, " (j-cut)",
                          " (reaction-cutaway)")
    assert diff["earlier"] == JCUT and diff["later"] == CUTAWAY
    assert diff["changed"], "four items against two is a change"
    text = choice.render_comparison(diff)
    assert "the join" in text and "Akshita's reaction" in text


def test_comparing_against_a_variant_nobody_built_refuses(project_folder):
    """An empty side reads exactly like a version that contained
    nothing, and the two must never be confused."""
    choice.record_build(project_folder, 9, REEL, " (j-cut)", rows(2))
    with pytest.raises(choice.ChoiceRefused) as refusal:
        choice.compare(project_folder, 9, " (j-cut)", " (never-built)")
    assert "never-built" in str(refusal.value)


# ── The choice, planned ──────────────────────────────────────────

def test_the_chosen_variant_takes_the_reels_name_and_the_rest_is_archived():
    plan = choice.plan_choice([REEL, JCUT, CUTAWAY], REEL,
                              {JCUT, CUTAWAY}, CUTAWAY, [JCUT], 4)
    assert plan["promote"] == (CUTAWAY, REEL)
    # The version that held the name is RETIRED, never deleted.
    assert plan["retire"][1] == f"{REEL} (archived round 004)"
    # And the loser goes to the same archive under the same family of
    # name, so no comparison timeline is left reading as a deliverable.
    assert plan["archive"][JCUT] == f"{JCUT} (archived round 004)"
    assert retire.is_archived_timeline(plan["archive"][JCUT])
    assert plan["collect"] == []


def test_choosing_something_that_was_never_built_refuses():
    with pytest.raises(choice.ChoiceRefused):
        choice.plan_choice([REEL, JCUT], REEL, {JCUT, CUTAWAY}, CUTAWAY,
                           [JCUT], 4)


# ── The bound: variants may not accumulate ───────────────────────

def test_the_archive_holds_one_unchosen_variant_per_REEL_not_per_suffix():
    """THE constraint. Every comparison invents a new suffix, so a bound
    keyed on the variant's own identity would keep one archived timeline
    per suffix the project ever tried - which grows with the number of
    comparisons and is exactly the clutter that has been complained
    about four times. Grouping the reel's variants together is what
    bounds the archive by the number of REELS."""
    tight = f"{REEL} (tight)"
    loose = f"{REEL} (loose)"
    existing = [REEL, f"{JCUT} (archived round 004)", tight, loose]
    plan = choice.plan_choice(existing, REEL,
                              {JCUT, CUTAWAY, tight, loose}, loose,
                              [tight], 5)
    assert plan["archive"][tight] == f"{tight} (archived round 005)"
    # The previous comparison's runner-up goes: one per reel, and the
    # one kept is the newest.
    assert plan["collect"] == [f"{JCUT} (archived round 004)"]
    assert f"{tight} (archived round 005)" in [
        k["name"] for k in plan["kept"]]


def test_the_bound_is_a_retention_not_a_timeout():
    """Nothing expires with the clock. A reel whose runner-up is the
    only archived variant keeps it however many rounds pass; it is the
    NEXT comparison that releases it, which is what makes the bound a
    function of the reels rather than of time."""
    assert choice.RETAINED_UNCHOSEN == 1
    existing = [REEL, f"{JCUT} (archived round 004)", CUTAWAY]
    for round_number in (5, 50, 500):
        plan = choice.plan_choice(existing, REEL, {JCUT, CUTAWAY},
                                  CUTAWAY, [], round_number)
        assert plan["collect"] == [], (
            f"round {round_number} collected the runner-up with no new "
            f"comparison - the bound has become a timeout")
        assert f"{JCUT} (archived round 004)" in [
            k["name"] for k in plan["kept"]]


def test_the_reels_OWN_retired_generations_are_bounded_too():
    """A choice retires the version that held the reel's name exactly
    as a promotion does, so the reel's own generations accumulate by
    the other door unless the same bound is asked of them. Measured
    2026-09-12 against a real Resolve: two choices left BOTH
    `(archived round 001)` and `(archived round 001.2)` standing."""
    first = f"{REEL} (archived round 001)"
    existing = [REEL, first, CUTAWAY]
    plan = choice.plan_choice(existing, REEL, {JCUT, CUTAWAY}, CUTAWAY,
                              [], 1)
    # The one retired by THIS choice is kept; the one before it goes.
    assert plan["retire"][1] == f"{REEL} (archived round 001.2)"
    assert first in plan["collect"]
    assert f"{REEL} (archived round 001.2)" in [
        k["name"] for k in plan["kept"]]


def test_a_signed_off_generation_of_the_reel_itself_is_never_collected():
    first = f"{REEL} (archived round 001)"
    plan = choice.plan_choice([REEL, first, CUTAWAY], REEL,
                              {JCUT, CUTAWAY}, CUTAWAY, [], 1,
                              signed_off_reels={REEL})
    assert plan["collect"] == []


def test_a_signed_off_unchosen_variant_is_never_collected():
    """The captain approved that cut; collecting it would delete the
    only copy of the thing they approved."""
    tight = f"{REEL} (tight)"
    existing = [REEL, f"{JCUT} (archived round 004)", tight, CUTAWAY]
    plan = choice.plan_choice(existing, REEL, {JCUT, CUTAWAY, tight},
                              CUTAWAY, [tight], 5,
                              signed_off_reels={JCUT})
    assert plan["collect"] == []
    assert any(JCUT in k["why"] and "sign-off" in k["why"]
               for k in plan["kept"])


def test_a_timeline_that_is_not_this_reels_variant_is_never_touched():
    """`variant_names` comes from the SPEC RECORD, so `(final)` - a
    reel's own name - can never be mistaken for a variation of it."""
    other = "Reel 13 - the-accounting-firm (final) (tight)"
    plan = choice.plan_choice(
        [REEL, JCUT, CUTAWAY, f"{other} (archived round 001)"],
        REEL, {JCUT, CUTAWAY}, CUTAWAY, [JCUT], 4)
    assert plan["collect"] == []
    assert other not in json.dumps(plan)


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


class RefusingRename(FakeTimeline):
    def SetName(self, name):
        return False


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


class DeletedTimeline(FakeTimeline):
    """Resolve's own behaviour: a deleted timeline answers GetName()
    with None. Measured 2026-09-12 against a real Resolve, and this is
    the shape that caught it."""

    def deleted(self):
        self._name = None


class RealisticDeleteProject(FakeProject):
    def _delete(self, timelines):
        for timeline in timelines:
            self.deleted.append(timeline.GetName())
            self.timelines.remove(timeline)
            timeline.deleted()
        return True


def test_the_collected_names_are_read_before_the_delete(project_folder):
    """Reading the report off a timeline object AFTER deleting it
    crashes the collection it was reporting on. Latent until now:
    `RETAINED_UNCHOSEN = 1` means nothing is collected until a reel is
    compared a SECOND time, so no test and no build had ever reached
    this line against a real Resolve."""
    tight = f"{REEL} (tight)"
    loose = f"{REEL} (loose)"
    older = f"{JCUT} (archived round 001)"
    choice.record_build(project_folder, 9, REEL, " (tight)", rows(1))
    choice.record_build(project_folder, 9, REEL, " (loose)", rows(1))
    project = RealisticDeleteProject([
        DeletedTimeline(REEL), DeletedTimeline(older),
        DeletedTimeline(tight), DeletedTimeline(loose)])
    report = choice.choose(project, project.pool, project_folder, 9, REEL,
                           " (loose)", "the loose framing breathes",
                           variant_names={JCUT, CUTAWAY, tight, loose})
    # The first comparison's runner-up goes; this one's is kept.
    assert report["collected"] == [older]
    assert older in choice.render_choice(report)
    assert None not in report["collected"]


class DecliningDeleteProject(FakeProject):
    def _delete(self, timelines):
        return False


def test_a_delete_resolve_declines_is_refused_not_reported_collected(
        project_folder):
    """The live-demo defect's sibling: PR 1149 settled this exact shape
    for `reel_retirement.collect_superseded`, and `choose` carries its
    own copy - `DeleteTimelines` returns falsy and the names are kept
    under `collected` anyway, while the census still shows them present
    (`docs/LIVE_DEMO_1107_COMPARISON_RETIREMENT.md`). Fails on the old
    shape (no raise, the name reported collected while still present);
    passes on the new (refused, the generation still present for the
    next choice to plan again)."""
    tight = f"{REEL} (tight)"
    loose = f"{REEL} (loose)"
    older = f"{JCUT} (archived round 001)"
    choice.record_build(project_folder, 9, REEL, " (tight)", rows(1))
    choice.record_build(project_folder, 9, REEL, " (loose)", rows(1))
    project = DecliningDeleteProject([
        FakeTimeline(REEL), FakeTimeline(older),
        FakeTimeline(tight), FakeTimeline(loose)])
    with pytest.raises(choice.ChoiceRefused) as refusal:
        choice.choose(project, project.pool, project_folder, 9, REEL,
                      " (loose)", "the loose framing breathes",
                      variant_names={JCUT, CUTAWAY, tight, loose})
    assert older in str(refusal.value)
    assert "nothing was reported collected" in str(refusal.value)
    assert older in project.names()


def _built(project_folder):
    choice.record_build(project_folder, 9, REEL, " (j-cut)", rows(2))
    choice.record_build(project_folder, 9, REEL, " (reaction-cutaway)",
                        rows(4))


def test_choosing_renames_retires_archives_and_records(project_folder):
    _built(project_folder)
    incumbent = FakeTimeline(REEL)
    project = FakeProject([incumbent, FakeTimeline(JCUT),
                           FakeTimeline(CUTAWAY)])
    report = choice.choose(project, project.pool, project_folder, 9,
                           REEL, " (reaction-cutaway)",
                           "her reaction lands the joke; the J-cut "
                           "reads as a mistake",
                           variant_names={JCUT, CUTAWAY})
    names = project.names()
    # The chosen variant IS the reel now - the same timeline, renamed.
    assert REEL in names
    assert CUTAWAY not in names
    # The version that held the name was retired, not deleted.
    assert report["retired"] in names
    assert retire.is_archived_timeline(report["retired"])
    # The loser is in the archive under a name nothing reads as a
    # deliverable.
    assert report["archived"][JCUT] in names
    assert project.deleted == []
    # And the round says which won and why.
    from library.tools.versions import rounds
    recorded = rounds.read_rounds(project_folder)
    entry = recorded["rounds"][-1]["reels"][REEL]
    assert entry["choice"]["chosen"] == " (reaction-cutaway)"
    assert entry["choice"]["over"] == [JCUT]
    assert "lands the joke" in entry["choice"]["why"]
    # The round carries the CHOSEN cut's rows, so the next round can be
    # diffed against it with Resolve closed.
    assert entry["rows"]["video:V1"]["count"] == 4
    assert "lands the joke" in choice.render_choice(report)


def test_a_choice_with_no_reason_is_refused(project_folder):
    _built(project_folder)
    project = FakeProject([FakeTimeline(REEL), FakeTimeline(CUTAWAY)])
    with pytest.raises(choice.ChoiceRefused) as refusal:
        choice.choose(project, project.pool, project_folder, 9, REEL,
                      " (reaction-cutaway)", "   ")
    assert "no reason" in str(refusal.value)
    assert project.names() == [REEL, CUTAWAY], "nothing moved"


def test_the_build_record_stops_claiming_what_is_no_longer_alive(
        project_folder):
    _built(project_folder)
    project = FakeProject([FakeTimeline(REEL), FakeTimeline(JCUT),
                           FakeTimeline(CUTAWAY)])
    choice.choose(project, project.pool, project_folder, 9, REEL,
                  " (reaction-cutaway)", "chose the cutaway",
                  variant_names={JCUT, CUTAWAY})
    assert choice.builds_for_reel(project_folder, 9) == []


def test_a_rename_resolve_refuses_leaves_everything_recoverable(
        project_folder):
    _built(project_folder)
    project = FakeProject([FakeTimeline(REEL), RefusingRename(CUTAWAY)])
    with pytest.raises(choice.ChoiceRefused) as refusal:
        choice.choose(project, project.pool, project_folder, 9, REEL,
                      " (reaction-cutaway)", "chose the cutaway",
                      variant_names={JCUT, CUTAWAY})
    assert "Nothing was deleted" in str(refusal.value)
    assert project.deleted == []
    # The incumbent is safe under its archived name and the variant is
    # still under its own.
    assert CUTAWAY in project.names()


# ── The sign-off, and what a variant does to it ──────────────────

def test_choosing_over_a_signed_off_reel_refuses_by_name(project_folder):
    from library.tools import reel_signoff

    _built(project_folder)
    reel_signoff.sign_off(project_folder, REEL,
                          note="this is the one, do not touch it")
    project = FakeProject([FakeTimeline(REEL), FakeTimeline(CUTAWAY)])
    with pytest.raises(reel_signoff.SignOffNotDeclared) as refusal:
        choice.choose(project, project.pool, project_folder, 9, REEL,
                      " (reaction-cutaway)", "chose the cutaway",
                      variant_names={JCUT, CUTAWAY})
    assert "do not touch it" in str(refusal.value)
    assert project.names() == [REEL, CUTAWAY], "nothing moved"


def test_a_declared_supersession_proceeds_and_is_recorded(project_folder):
    from library.tools import reel_signoff

    _built(project_folder)
    reel_signoff.sign_off(project_folder, REEL, note="the old one")
    project = FakeProject([FakeTimeline(REEL), FakeTimeline(CUTAWAY)])
    report = choice.choose(project, project.pool, project_folder, 9, REEL,
                           " (reaction-cutaway)", "the cutaway is better",
                           variant_names={JCUT, CUTAWAY},
                           supersede_declared=[REEL])
    assert report["superseded_signoff"]["note"] == "the old one"
    # Recorded, never deleted.
    document = reel_signoff.read_signoffs(project_folder)
    assert reel_signoff.signoff_for(project_folder, REEL) is None
    assert any(entry["note"] == "the old one"
               for entry in document["superseded"])


def test_a_signed_off_variant_carries_its_approval_onto_the_reel(
        project_folder):
    """A variant IS built, so it can be signed off (the captain's own
    ruling). When it wins, the cut the captain approved did not change -
    its container did - and an approval the machine dropped because of a
    rename would be an approval it withdrew on their behalf."""
    from library.tools import reel_signoff

    _built(project_folder)
    reel_signoff.sign_off(project_folder, CUTAWAY,
                          note="her reaction is the whole thing",
                          by="captain")
    project = FakeProject([FakeTimeline(REEL), FakeTimeline(CUTAWAY)])
    report = choice.choose(project, project.pool, project_folder, 9, REEL,
                           " (reaction-cutaway)", "chose the cutaway",
                           variant_names={JCUT, CUTAWAY})
    assert report["carried_signoff"]["note"] == "her reaction is the " \
                                                "whole thing"
    carried = reel_signoff.signoff_for(project_folder, REEL)
    assert carried is not None
    assert carried["note"] == "her reaction is the whole thing"
    assert carried["by"] == "captain"
    # And it is no longer claimed on the name that no longer exists.
    assert reel_signoff.signoff_for(project_folder, CUTAWAY) is None


def test_a_variant_has_its_own_signoff_identity():
    """`feedback_ledger.base_reel_name` strips the BUILD's own container
    suffixes and leaves everything else alone, so a variant is its own
    reel to a sign-off - which is what lets one be signed off at all
    without any change to `reel_signoff`."""
    from library.tools import reel_signoff

    assert reel_signoff.base_name(CUTAWAY) == CUTAWAY
    assert reel_signoff.base_name(f"{REEL} (rebuild staging)") == REEL
