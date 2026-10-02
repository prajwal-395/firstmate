"""Staging scratches cannot sit beside the deliverables (2026-09-11).

History: docs/evidence/resolve_test_history.md#test_staging_scratch_containment.
"""
from __future__ import annotations

from library.tools import resolve_bin_layout as bins
from library.tools import resolve_organization as org
from library.tools import staging_holds as holds

MASTER = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"

SCRATCH_13_A = (
    "Reel 13 - the-accounting-firm-ai-called-healthcare "
    "(scratch fm-restore) (rebuild staging)")
SCRATCH_13_B = (
    "Reel 13 - the-accounting-firm-ai-called-healthcare "
    "(scratch fm-restore2) (rebuild staging)")
SCRATCH_28 = (
    "Reel 28 - the-nail-salon-query-google-cant-answer "
    "(scratch fm-restore28) (rebuild staging)")
REAL_SCRATCHES = [SCRATCH_13_A, SCRATCH_13_B, SCRATCH_28]

FINAL_13 = "Reel 13 - the-accounting-firm-ai-called-healthcare"


def timeline(item_id, name, *, folder=()):
    return org.Artefact(item_id=item_id, name=name, kind="timeline",
                        file_path="", placed_by=(),
                        folder_path=tuple(folder))


def captains_pool():
    """The incident in miniature: the three scratches in the top-level
    reels bin, the captain's own Reel 13 one level deeper, the master
    present."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-final-13", FINAL_13,
                 folder=(bins.REELS_BIN,
                         bins.REEL_STATE_BINS[org.EARLIER])),
        timeline("t-scratch-a", SCRATCH_13_A,
                 folder=(bins.REELS_BIN,)),
        timeline("t-scratch-b", SCRATCH_13_B,
                 folder=(bins.REELS_BIN,)),
        timeline("t-scratch-28", SCRATCH_28,
                 folder=(bins.REELS_BIN,)),
    ]
    return artefacts


def a_plan(artefacts, **kw):
    return org.plan_organization(
        artefacts=artefacts,
        project_root=PROJECT_ROOT,
        master_timeline_name=MASTER,
        current_reels=kw.pop("current_reels", []),
        archived_plan_names=kw.pop("archived_plan_names", []),
        **kw)


# ------------------------------------------- the declared location


def test_the_scratch_bin_is_outside_every_bin_the_captain_reviews():
    """One location, named in code, outside the reels bins: not the
    reels root, not a state leaf, not the proof leaf - and canonical,
    so a verdict destination there converges rather than strands."""
    assert bins.SCRATCH_BIN != bins.REELS_BIN
    assert bins.SCRATCH_BIN not in bins.REEL_STATE_BINS.values()
    assert bins.SCRATCH_BIN != bins.REELS_PROOF_BIN
    assert bins.is_canonical((bins.SCRATCH_BIN,))
    # The classifier takes the incident's names and nothing the captain
    # reviews: a plain final, a versioned reel, and the pending
    # suffix-build name `(baseline scratch)` - which wears the word
    # scratch without the marker - all stay out.
    for name in REAL_SCRATCHES:
        assert bins.is_scratch_timeline(name), name
    assert not bins.is_scratch_timeline(FINAL_13)
    assert not bins.is_scratch_timeline("Reel 20 - slug v003")
    assert not bins.is_scratch_timeline(
        "SOP Proof_reel13_tail_breath (baseline scratch)")


class _FakeFolder:
    def __init__(self, name):
        self._name = name
        self._subs = []
        self._clips = []

    def GetName(self):
        return self._name

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return list(self._subs)


class _FakePool:
    """A pool that files like Resolve: creations land in CURRENT."""

    def __init__(self):
        self.root = _FakeFolder("Master")
        self._current = self.root
        self.timelines = []

    def GetRootFolder(self):
        return self.root

    def GetCurrentFolder(self):
        return self._current

    def SetCurrentFolder(self, folder):
        self._current = folder
        return True

    def AddSubFolder(self, parent, name):
        folder = _FakeFolder(name)
        parent._subs.append(folder)
        return folder

    def CreateEmptyTimeline(self, name):
        self.timelines.append((self._current.GetName(), name))
        folder = _FakeFolder(name)
        self._current._clips.append(folder)
        return folder


def _pool_with_current(name):
    pool = _FakePool()
    folder = _FakeFolder(name)
    pool.root._subs.append(folder)
    pool.SetCurrentFolder(folder)
    return pool


def test_a_staging_timeline_is_created_in_the_scratch_bin():
    """The choke point (`create_reel_timeline` is the only caller path
    a build places through): a staging name lands in the scratch bin
    even while the reels bin is current, and current is restored.
    Without the routing this creates in the reels bin, so it fails."""
    from library.tools.reel_build import create_reel_timeline

    pool = _pool_with_current(bins.REELS_BIN)
    current = pool.GetCurrentFolder()
    create_reel_timeline(pool, SCRATCH_13_A)
    assert pool.timelines == [(bins.SCRATCH_BIN, SCRATCH_13_A)]
    assert pool.GetCurrentFolder() is current
    # The other direction: deliverables are not swept in with the
    # throwaways.
    pool = _pool_with_current(bins.SCRATCH_BIN)
    create_reel_timeline(pool, FINAL_13)
    assert pool.timelines == [(bins.REELS_BIN, FINAL_13)]


def test_scratches_in_the_reels_bin_are_filed_back_to_scratch():
    """The captain's pool as found: all three scratches move out of
    the reels bin to the scratch bin, and nothing else moves. Without
    the filing branch they file to state bins among the deliverables,
    so this fails."""
    plan = a_plan(captains_pool(), archived_plan_names=[FINAL_13])
    moves = {v.name: v.destination for v in plan.moves}
    for name in REAL_SCRATCHES:
        assert moves[name] == (bins.SCRATCH_BIN,), name
    assert set(moves) == set(REAL_SCRATCHES)


def test_a_promoted_final_does_not_strand_in_the_scratch_bin():
    """Promotion renames the staging in place, so the fresh final
    briefly sits in the scratch bin: the organiser files it OUT to
    its state bin rather than leaving a deliverable there - the
    scratch bin stays a place throwaways sit, not one finals
    accumulate in."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-final-13", FINAL_13, folder=(bins.SCRATCH_BIN,)),
    ]
    plan = a_plan(artefacts, current_reels=[FINAL_13])
    moves = {v.name: v.destination for v in plan.moves}
    assert moves[FINAL_13] == (
        bins.REELS_BIN, bins.REEL_STATE_BINS[org.CURRENT])


def test_a_scratch_in_the_captains_own_tier_is_left_alone():
    """The captain's organisation wins wherever the two conflict: a
    scratch they deliberately filed outside every managed bin gets no
    verdict and no move - the plan never reaches into their tiers."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-scratch-a", SCRATCH_13_A,
                 folder=(bins.REELS_BIN, "Fully approved")),
    ]
    plan = a_plan(artefacts)
    assert plan.moves == []
    assert SCRATCH_13_A in [name for name, _ in plan.left_alone]


# --------------------------------- property 2: reported, never swept


def scratched_pool():
    """All three scratches binned where they belong - placement is
    right, so what is left to say is whether any of them still has a
    purpose."""
    artefacts = [timeline("t-master", MASTER)]
    for i, name in enumerate(REAL_SCRATCHES):
        artefacts.append(timeline(f"t-s{i}", name,
                                  folder=(bins.SCRATCH_BIN,)))
    return artefacts


def test_outlived_scratches_are_reported_by_name(tmp_path):
    """No holds anywhere: all three real names report as outlived,
    and the rendering carries each one. Without the report this is an
    empty list, so it fails."""
    report = org.scratch_report(scratched_pool(), str(tmp_path))
    assert report["present"] == sorted(REAL_SCRATCHES)
    assert report["held"] == []
    assert report["outlived"] == sorted(REAL_SCRATCHES)
    text = org.render_scratch_report(report)
    for name in REAL_SCRATCHES:
        assert name in text
    assert "outlived" in text


def test_held_scratches_report_as_pending_in_name_order(tmp_path):
    """A held scratch is a pending promotion with its destination and
    age, never outlived. Then the incident's own shape: two held at
    once. Sorting held entries without a key raises TypeError the
    moment a second hold lands - `sorted` over dicts has no order."""
    holds.take_hold(str(tmp_path), SCRATCH_13_A, awaiting=FINAL_13,
                    reason="staged rebuild awaiting promotion",
                    taken_by="rebuild_reels_in_project")
    report = org.scratch_report(scratched_pool(), str(tmp_path))
    assert [h["name"] for h in report["held"]] == [SCRATCH_13_A]
    assert report["held"][0]["awaiting"] == FINAL_13
    assert report["held"][0]["age"] != ""
    assert report["outlived"] == sorted([SCRATCH_13_B, SCRATCH_28])
    text = org.render_scratch_report(report)
    assert SCRATCH_13_A in text and "HELD" in text
    assert SCRATCH_28 in text and "outlived" in text

    holds.take_hold(str(tmp_path), SCRATCH_13_B, awaiting=FINAL_13,
                    reason="staged rebuild awaiting promotion",
                    taken_by="rebuild_reels_in_project")
    report = org.scratch_report(scratched_pool(), str(tmp_path))
    assert [h["name"] for h in report["held"]] == sorted(
        [SCRATCH_13_A, SCRATCH_13_B])
    assert report["outlived"] == [SCRATCH_28]
    text = org.render_scratch_report(report)
    assert SCRATCH_13_A in text and SCRATCH_13_B in text


def test_an_unreadable_holds_file_refuses_the_split_but_names_what_is_there(
        tmp_path):
    """A corrupt holds file reads exactly like "nothing is protected",
    so the held/outlived split refuses rather than reporting every
    held staging as outlived - while the names present, which need no
    holds, are still said."""
    holds_path = tmp_path / "pipeline_output" / "review"
    holds_path.mkdir(parents=True)
    (holds_path / "staging_holds.json").write_text(
        "{not json", encoding="utf-8")
    report = org.scratch_report(scratched_pool(), str(tmp_path))
    assert report["present"] == sorted(REAL_SCRATCHES)
    assert report["held"] == [] and report["outlived"] == []
    assert report["holds_unreadable"] != ""
    text = org.render_scratch_report(report)
    assert SCRATCH_13_A in text and "unreadable" in text.lower()
