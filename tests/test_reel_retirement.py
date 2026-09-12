"""Promotion RETIRES the timeline it replaces, and the archive ENDS.

Two things are being proved, and they pull against each other:

1. promotion no longer deletes what it replaced, so a round can be
   compared against the last (the captain, 2026-09-12);
2. the archive does not become the clutter the captain has asked about
   four times - a scratch left in a reels bin is how they came to review
   a throwaway by mistake (`resolve_bin_layout.SCRATCH_BIN`).

The lifecycle is what reconciles them, and each half has a test that
fails without it: the retention bound is per REEL rather than per round,
so the archive cannot grow with time; a signed-off generation is never
collected; the archived name can never be mistaken for the live cut; and
a stray archived timeline is filed back by the organiser rather than
sitting beside a deliverable.
"""
from unittest.mock import MagicMock

import pytest

from library.tools import reel_retirement as retire
from library.tools import resolve_bin_layout as bins

REEL = "Reel 09 - your-website-is-only-20-percent"
OTHER = "Reel 13 - the-accounting-firm"


# ── The naming ───────────────────────────────────────────────────

def test_an_archived_name_can_never_be_mistaken_for_the_live_cut():
    name = retire.archived_name(REEL, 3)
    assert name == f"{REEL} (archived round 003)"
    assert retire.is_archived_timeline(name)
    assert not retire.is_archived_timeline(REEL)
    # And the classifier is narrow: a reel the captain happened to name
    # with the word is untouched.
    assert not retire.is_archived_timeline("My archived reel")
    assert retire.parse_archived(name) == (REEL, 3)


def test_two_retirements_in_one_round_do_not_share_a_name():
    """Resolve allows duplicate timeline names, so a collision would
    make two generations indistinguishable and the retention bound
    unable to tell them apart."""
    first = retire.archived_name(REEL, 3, taken=set())
    second = retire.archived_name(REEL, 3, taken={first})
    third = retire.archived_name(REEL, 3, taken={first, second})
    assert len({first, second, third}) == 3
    # Still parses to the same round - a second retirement inside round
    # 3 is not a round of its own.
    assert all(retire.parse_archived(name) == (REEL, 3)
               for name in (first, second, third))
    # And sorts after the bare form, so "newest first" holds.
    assert retire.generations_of([first, second], REEL)[0] == second


def test_the_archive_bin_is_the_one_already_declared_for_it():
    """`resolve_bin_layout` owns every bin path; nothing invents one.
    The archive bin has carried this purpose since the 2026-09-09 reset
    and had no user until retirement."""
    assert retire.ARCHIVE_BIN == (bins.REELS_BIN, bins.REELS_ARCHIVE_BIN)
    declared = {entry.path for entry in bins.BINS}
    assert retire.ARCHIVE_BIN in declared


# ── The lifecycle ────────────────────────────────────────────────

def test_the_archive_holds_one_generation_per_reel_not_one_per_round():
    """The whole answer to the clutter question. Bounded by the number
    of REELS, so it cannot grow with time. Remove the bound and a
    project accumulates one archived timeline per reel per round, which
    is the complaint, one round later."""
    names = [retire.archived_name(REEL, n) for n in (1, 2, 3)] + \
            [retire.archived_name(OTHER, 1)]
    plan = retire.plan_collection(names, [REEL])
    assert plan["collect"] == [retire.archived_name(REEL, 2),
                               retire.archived_name(REEL, 1)]
    # The newest is kept - it is what the round diff compares against.
    assert [kept["name"] for kept in plan["kept"]] == [
        retire.archived_name(REEL, 3)]
    # And another reel's generations are not this promotion's business.
    assert retire.archived_name(OTHER, 1) not in plan["collect"]


def test_a_signed_off_generation_is_never_collected():
    """The cut the captain approved is the one thing collection may not
    take. Remove this and the retention bound deletes the only copy of
    an approved reel."""
    names = [retire.archived_name(REEL, n) for n in (1, 2, 3)]
    plan = retire.plan_collection(names, [REEL], signed_off_reels={REEL})
    assert plan["collect"] == []
    whys = " ".join(kept["why"] for kept in plan["kept"])
    assert "sign-off" in whys


def test_a_first_retirement_collects_nothing():
    plan = retire.plan_collection([retire.archived_name(REEL, 1)], [REEL])
    assert plan["collect"] == []


# ── The Resolve half ─────────────────────────────────────────────

class FakeTimeline:
    def __init__(self, name):
        self._name = name
        self.moved_to = None

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetMediaPoolItem(self):
        return f"pool:{self._name}"


class RefusingTimeline(FakeTimeline):
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


def test_retiring_renames_and_files_and_deletes_nothing():
    backup = FakeTimeline(f"{REEL} (pre-rebuild backup)")
    project = FakeProject([backup])
    report = retire.retire_timelines(
        project, project.pool, {REEL: backup}, {REEL: 2})
    assert report["archived"] == {REEL: retire.archived_name(REEL, 2)}
    assert backup.GetName() == retire.archived_name(REEL, 2)
    assert project.deleted == []
    project.pool.MoveClips.assert_called_once()


def test_a_rename_resolve_refuses_raises_rather_than_leaving_debris():
    """A timeline left called `... (pre-rebuild backup)` is debris the
    next build refuses on - said now rather than discovered later."""
    backup = RefusingTimeline(f"{REEL} (pre-rebuild backup)")
    project = FakeProject([backup])
    with pytest.raises(retire.RetirementRefused) as refused:
        retire.retire_timelines(project, project.pool, {REEL: backup},
                                {REEL: 2})
    assert "nothing was deleted" in str(refused.value)


def test_collection_only_ever_touches_archived_names():
    """`assert_deletion_scope` is asked of the list about to be deleted
    (the captain's 2026-09-06 ruling). A live reel in the project is
    never a candidate, whatever the plan said."""
    live = FakeTimeline(REEL)
    newest = FakeTimeline(retire.archived_name(REEL, 3))
    older = FakeTimeline(retire.archived_name(REEL, 2))
    project = FakeProject([live, newest, older])
    record = retire.collect_superseded(
        project, project.pool, project.names(), [REEL])
    assert record["collected"] == [retire.archived_name(REEL, 2)]
    assert REEL in project.names()
    assert retire.archived_name(REEL, 3) in project.names()


def test_a_signed_off_reel_keeps_every_generation_through_resolve():
    newest = FakeTimeline(retire.archived_name(REEL, 3))
    older = FakeTimeline(retire.archived_name(REEL, 2))
    project = FakeProject([FakeTimeline(REEL), newest, older])
    record = retire.collect_superseded(
        project, project.pool, project.names(), [REEL],
        signed_off_reels={REEL})
    assert record["collected"] == []
    assert project.deleted == []


def test_the_organiser_files_a_stray_archived_timeline_back():
    """Out of the review path by construction, not by tidying
    afterwards - the rule the scratch bin established.

    Remove the organiser's archived branch and this timeline files as
    an ordinary reel, into a bin the captain reviews, one level from
    their deliverables. That is the scratch incident again.
    """
    from library.tools.resolve_organization import Artefact, plan_organization

    archived = retire.archived_name(REEL, 2)
    plan = plan_organization(
        [Artefact(item_id="t-master", name="Master", kind="timeline",
                  file_path="", placed_by=(), folder_path=()),
         Artefact(item_id="t-old", name=archived, kind="timeline",
                  file_path="", placed_by=(), folder_path=())],
        "/projects/p", "Master", built_reels=[REEL],
        archived_plan_names=[])
    verdict = [entry for entry in plan.verdicts
               if entry.name == archived]
    assert verdict, "the organiser left the archived timeline alone"
    assert verdict[0].destination == retire.ARCHIVE_BIN
    assert "archive" in verdict[0].why


def test_an_archived_generation_is_not_graded_as_a_deliverable():
    """The conformance sweep enumerates `Reel ...` timelines off the
    live project. Retirement puts a second one there per reel, and
    grading it against the CURRENT plan reports a mismatch that is a
    fact about it being retired, not a defect.

    Remove the exclusion and every round doubles the sweep's findings -
    which is how a real finding stops being read.
    """
    from library.tools.reel_conformance_verifier import grades_as_a_reel

    archived = retire.archived_name(REEL, 3)
    assert grades_as_a_reel(REEL)
    assert not grades_as_a_reel(archived)
    # And it is still reachable by name: refusing to look at something
    # the operator asked for explicitly is a different failure from
    # quietly grading what they did not.
    assert grades_as_a_reel(archived, only_reels=[archived])
    # A staging container still grades - that is what the gate reads.
    assert grades_as_a_reel(f"{REEL} (rebuild staging)")
    assert not grades_as_a_reel("Podcast - Synced")
