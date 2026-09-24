"""A promotion DELETES what it replaced, unless asked to keep it.

Three things are being proved, and the first is the default:

1. the default promotion deletes the backup, so one timeline per reel
   and an empty archive (the captain, 2026-09-18, withdrawing his own
   2026-09-12 retire rule after seeing "(archived round 001)" on reel
   titles - no leftovers wins, comparison becomes explicit);
2. the explicit opt-in still retires exactly one generation per reel,
   bounded so the archive cannot grow with time - the answer to the
   clutter question the captain has asked about four times (a scratch
   left in a reels bin is how they came to review a throwaway by
   mistake, `resolve_bin_layout.SCRATCH_BIN`);
3. a signed-off generation is never deleted or collected, and the
   deletion scope refuses a list that reaches beyond the reels being
   promoted.

The lifecycle is what reconciles the first two, and each half has a
test that fails without it: the retention bound is per REEL rather
than per round; the archived name can never be mistaken for the live
cut; and a stray archived timeline is filed back by the organiser
rather than sitting beside a deliverable.
"""
from unittest.mock import MagicMock, patch

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


# ── The opt-in ───────────────────────────────────────────────────


def test_zero_retention_collects_every_unsigned_generation():
    """The default-delete path plans with `retained=0`: every archived
    generation of a promoted reel goes, so the archive ends empty for
    it. Remove the parameter and the default path can only keep newest
    - the old default, one leftover per reel."""
    names = [retire.archived_name(REEL, n) for n in (1, 2, 3)]
    plan = retire.plan_collection(names, [REEL], retained=0)
    assert sorted(plan["collect"]) == sorted(names)
    assert plan["kept"] == []


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


# ── The default delete ───────────────────────────────────────────

def test_delete_backups_deletes_the_backup_and_nothing_else():
    """The new default: the generation the promotion just replaced is
    gone, the live reel untouched, and nothing was ever renamed into
    the archive."""
    from library.tools.reel_build import backup_name

    live = FakeTimeline(REEL)
    backup = FakeTimeline(backup_name(REEL))
    project = FakeProject([live, backup])
    record = retire.delete_backups(
        project, project.pool, {backup.GetName(): backup})
    assert record["deleted"] == [backup_name(REEL)]
    assert project.names() == [REEL]
    assert not [name for name in project.names()
                if retire.is_archived_timeline(name)]


def test_the_deletion_scope_refuses_a_list_beyond_the_promoted_reels():
    """`assert_deletion_scope` is asked of the list about to be deleted
    (the captain's 2026-09-06 ruling). A wrong list refuses rather than
    widening - the reason the new default path is allowed to delete."""
    from library.tools.reel_build import (
        assert_deletion_scope, backup_name)
    from library.tools.reel_build import ReelBuildError

    live = FakeTimeline(REEL)
    backup = FakeTimeline(backup_name(REEL))
    with pytest.raises(ReelBuildError):
        assert_deletion_scope([live, backup], {backup.GetName()})


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

    live = FakeTimeline(REEL)
    newest = FakeTimeline(retire.archived_name(REEL, 3))
    older = FakeTimeline(retire.archived_name(REEL, 2))
    project = DecliningProject([live, newest, older])
    with pytest.raises(retire.RetirementRefused):
        retire.collect_superseded(
            project, project.pool, project.names(), [REEL])
    assert retire.archived_name(REEL, 2) in project.names()


# ── The promotion, end to end ────────────────────────────────────

@pytest.fixture(autouse=True)
def mock_dvr(stub_resolve_script):
    yield


@pytest.fixture
def review_project(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


class FakeItem:
    def __init__(self, name, start, end):
        self._n, self._s, self._e = name, start, end

    def GetName(self):
        return self._n

    def GetStart(self):
        return self._s

    def GetEnd(self):
        return self._e

    def GetDuration(self):
        return self._e - self._s


class StagedTimeline:
    def __init__(self, name, video=()):
        self._name = name
        self._rows = {"video": list(video), "audio": []}

    def GetName(self):
        return self._name

    def SetName(self, name):
        self._name = name
        return True

    def GetTrackCount(self, kind):
        return len(self._rows[kind])

    def GetTrackName(self, kind, index):
        return self._rows[kind][index - 1][0]

    def GetItemListInTrack(self, kind, index):
        return self._rows[kind][index - 1][1]

    def GetStartFrame(self):
        return 0

    def GetMarkers(self):
        return {}

    def AddMarker(self, *args, **kwargs):
        return True

    def GetMediaPoolItem(self):
        return f"pool:{self._name}"


class ResolveProject:
    def __init__(self, timelines):
        self.timelines = list(timelines)
        pool = MagicMock()
        pool.DeleteTimelines.side_effect = self._delete
        self._pool = pool
        self.deleted = []

    def _delete(self, timelines):
        for timeline in timelines:
            self.deleted.append(timeline.GetName())
            self.timelines.remove(timeline)
        return True

    def GetMediaPool(self):
        return self._pool

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def names(self):
        return [t.GetName() for t in self.timelines]


MASTER = "Podcast - Synced"


def _rows(name):
    return [("Akshita", [FakeItem(f"{name} clip", 0, 100)])]


def _pair(final):
    return (StagedTimeline(final, video=_rows(final)),
            StagedTimeline(f"{final} (rebuild staging)",
                           video=_rows(final)))


def _promote(resolve, project, staged_to_final, retain=None,
             supersede=None):
    import json

    from library.tools.reel_build import promote_staged_reels

    (project / "pipeline_output" / "review"
     / "plan_provenance.json").write_text(
        json.dumps({"built_reels": sorted(staged_to_final.values())}),
        encoding="utf-8")
    with patch("library.tools.resolve_locale.scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  return_value=resolve):
        return promote_staged_reels(
            str(project), "Mock Project", MASTER, staged_to_final,
            organise=False, retain=retain, supersede=supersede)


def test_default_promotion_leaves_one_timeline_per_reel(review_project):
    """The new default, end to end: the backup is deleted, nothing is
    renamed into the archive, and each final appears exactly once."""
    from library.tools.reel_build import backup_name

    original, staging = _pair(REEL)
    other, other_staging = _pair(OTHER)
    resolve = ResolveProject([StagedTimeline(MASTER), original, staging,
                              other, other_staging])

    promoted = _promote(
        resolve, review_project,
        {REEL: staging.GetName(), OTHER: other_staging.GetName()})

    assert sorted(promoted["promoted"]) == sorted([REEL, OTHER])
    names = resolve.names()
    assert names.count(REEL) == 1
    assert names.count(OTHER) == 1
    # The backups are gone - deleted, not renamed - and the archive
    # was never touched.
    assert promoted["retirement"]["deleted"] == sorted(
        [backup_name(REEL), backup_name(OTHER)])
    assert promoted["retirement"]["archived"] == {}
    assert promoted["retirement"]["collected"] == []
    assert resolve.deleted == sorted(
        [backup_name(REEL), backup_name(OTHER)])
    assert not [name for name in names
                if retire.is_archived_timeline(name)]
    assert not [name for name in names
                if name.endswith("(pre-rebuild backup)")]
    assert "Deleted 2 superseded backup(s)" in \
        retire.render(promoted["retirement"])


def test_explicit_retain_keeps_exactly_one_generation(review_project):
    """The reachable path: naming the reel retires its backup into the
    archive, and the retention bound still holds - one generation, not
    one per round."""
    from library.tools.reel_build import backup_name

    original, staging = _pair(REEL)
    legacy = StagedTimeline(retire.archived_name(REEL, 1))
    resolve = ResolveProject([StagedTimeline(MASTER), original, staging,
                              legacy])

    promoted = _promote(resolve, review_project,
                        {REEL: staging.GetName()}, retain=[REEL])

    # No round was ever stamped in this project, so the retired
    # version is labelled with the current round - colliding with the
    # legacy generation, which gains a `.2` sibling rather than sharing
    # its name.
    expected = retire.archived_name(
        REEL, 1, taken={retire.archived_name(REEL, 1)})
    assert promoted["retirement"]["deleted"] == []
    assert promoted["retirement"]["archived"] == {REEL: expected}
    assert backup_name(REEL) not in resolve.names()
    assert expected in resolve.names()
    # The older generation is collected under the bound; the newest -
    # the one just retired - is what a round diff compares against.
    assert promoted["retirement"]["collected"] == [
        retire.archived_name(REEL, 1)]
    assert [kept["name"] for kept in
            promoted["retirement"]["kept"]] == [expected]


def test_a_signed_off_backup_retires_on_the_default_path(review_project):
    """A sign-off is the captain approving a specific cut, so deleting
    its only copy is exactly what "unless i explicitly ask for
    otherwise" does not cover: the backup retires even with no
    `retain` declaration."""
    from library.tools import reel_signoff as signoff

    signoff.sign_off(str(review_project), REEL, note="ships")
    original, staging = _pair(REEL)
    resolve = ResolveProject([StagedTimeline(MASTER), original, staging])

    promoted = _promote(resolve, review_project,
                        {REEL: staging.GetName()}, supersede=[REEL])

    assert promoted["promoted"] == [REEL]
    assert promoted["retirement"]["deleted"] == []
    assert promoted["retirement"]["archived"] == {
        REEL: retire.archived_name(REEL, 1)}
    assert retire.archived_name(REEL, 1) in resolve.names()
    # And the sign-off itself is superseded, never deleted.
    assert signoff.signoff_for(str(review_project), REEL) is None
    assert signoff.read_signoffs(
        str(review_project))["superseded"][0]["note"] == "ships"


