"""Current plan means the PLAN, not the last build call.

Measured 2026-09-18 on the captain's project: building Reel 02 alone
filed eight accepted reels into Earlier plans. The plan hash had rotated
under them, so `write_provenance` dropped every entry but the reel that
build placed - and the organiser filed by the provenance record's
`built_reels`, reading a partial build as a plan change.

The plan source is the proposals file (`reel_proposals_v2.json`): it is
what the builder builds from (`reel_build` reads it through
`reel_proposal.read_proposal`) and what the captain rules on, while the
provenance record only says which plan a build consumed. Filing by the
plan keeps both properties below; filing by provenance cannot.
"""
from __future__ import annotations

import json

from library.tools import resolve_bin_layout as bins
from library.tools import resolve_organization as org
from library.tools.execution.organise_media_pool import plan_for_project
from library.tools.plan_provenance import current_plan_names
from library.tools.reel_proposal import reel_timeline_name

MASTER = "Master Timeline"
CURRENT_BIN = (bins.REELS_BIN, bins.REEL_STATE_BINS[org.CURRENT])
EARLIER_BIN = (bins.REELS_BIN, bins.REEL_STATE_BINS[org.EARLIER])


def _name(number, slug):
    return reel_timeline_name(number, slug)


def _moment(number, slug, approval="approved"):
    return {
        "number": number,
        "slug": slug,
        "reason": "a moment the captain ruled on",
        "timeline_start": 10.0,
        "timeline_end": 40.0,
        "approval": approval,
    }


def _write_live_plan(review_dir, moments):
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "reel_proposals_v2.json").write_text(
        json.dumps({"format": "reel_proposal/1",
                    "moment_count": len(moments),
                    "moments": moments}),
        encoding="utf-8")


def _write_archive(review_dir, stamp, names):
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / f"reel_proposals_v2_{stamp}.json").write_text(
        json.dumps({"moments": [{"timeline_name": n} for n in names]}),
        encoding="utf-8")


def _write_provenance(review_dir, built, plan_hash="7" * 64):
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / "plan_provenance.json").write_text(
        json.dumps({"plan_content_hash": plan_hash,
                    "built_at": "2026-09-18T01:08:46+00:00",
                    "built_reels": list(built)}),
        encoding="utf-8")


class _Clip:
    def __init__(self, name, uid, kind="Timeline"):
        self._name = name
        self._uid = uid
        self._kind = kind

    def GetUniqueId(self):
        return self._uid

    def GetName(self):
        return self._name

    def GetClipProperty(self, key):
        if key == "Type":
            return self._kind
        return ""

    def GetMetadata(self, key):
        return ""


class _Folder:
    def __init__(self, name, subs=(), clips=()):
        self._name = name
        self._subs = list(subs)
        self._clips = list(clips)

    def GetName(self):
        return self._name

    def GetSubFolderList(self):
        return list(self._subs)

    def GetClipList(self):
        return list(self._clips)


class _Timeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def GetTrackCount(self, kind):
        return 0


class _Pool:
    def __init__(self, root):
        self._root = root

    def GetRootFolder(self):
        return self._root

    def GetCurrentFolder(self):
        return self._root


class _Project:
    """A pool holding timeline clips in bins, and nothing else."""

    def __init__(self, placed):
        # placed: {timeline name: bin path tuple}; MASTER sits at root.
        self._timelines = [MASTER, *placed]
        clips_by_bin: dict[tuple, list] = {}
        for uid, name in enumerate(self._timelines):
            clips_by_bin.setdefault(
                placed.get(name, ()), []).append(_Clip(name, f"id-{uid}"))
        current = _Folder(
            "Current plan",
            clips=clips_by_bin.get(CURRENT_BIN, []))
        earlier = _Folder(
            "Earlier plans",
            clips=clips_by_bin.get(EARLIER_BIN, []))
        reels = _Folder(bins.REELS_BIN, subs=[current, earlier])
        self._root = _Folder("Master", subs=[reels],
                             clips=clips_by_bin.get((), []))

    def GetName(self):
        return "Mock Project"

    def GetMediaPool(self):
        return _Pool(self._root)

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return _Timeline(self._timelines[index - 1])


NINE = [(1, "geo-is-comprehension-not-position"),
        (2, "keyword-stuffing-now-costs-you"),
        (9, "your-website-is-only-20-percent"),
        (13, "the-accounting-firm-ai-called-healthcare"),
        (23, "why-small-business-wins-on-ai"),
        (26, "write-for-the-question-your-customer-ask"),
        (28, "the-nail-salon-query-google-cant-answer"),
        (30, "your-google-business-profile-and-the-map"),
        (31, "is-there-a-way-to-game-ai")]


def _review(tmp_path):
    return tmp_path / "pipeline_output" / "review"


def test_building_one_reel_leaves_every_other_planned_reel_where_it_was(
        tmp_path):
    """The regression: nine approved, one placed, none moved.

    The provenance record names only the reel the last call placed (the
    plan hash rotated under the other eight, so the merge dropped them),
    while the archived plans still name all nine. Filing must follow the
    live plan - all nine stay Current, no timeline moves at all."""
    names = [_name(n, s) for n, s in NINE]
    placed = {n: CURRENT_BIN for n in names}
    review = _review(tmp_path)
    _write_live_plan(review, [_moment(n, s) for n, s in NINE])
    _write_archive(review, "20260917T000000Z", names)
    _write_provenance(review, [names[1]], plan_hash="2" * 64)

    assert current_plan_names(str(tmp_path), None) == set(names)

    plan, artefacts, _, _ = plan_for_project(
        _Project(placed), str(tmp_path), MASTER)
    states = {v.name: v.state for v in plan.verdicts
              if v.kind == "timeline"}
    assert states == {n: org.CURRENT for n in names}
    assert [v for v in plan.moves if v.kind == "timeline"] == []


def test_a_reel_the_plan_no_longer_names_still_demotes(tmp_path):
    """The intent that survives: a genuinely superseded reel moves to
    Earlier plans - moved and relabelled, never deleted."""
    dropped = _name(1, "geo-is-comprehension-not-position")
    kept = _name(2, "keyword-stuffing-now-costs-you")
    review = _review(tmp_path)
    _write_live_plan(review, [_moment(2, "keyword-stuffing-now-costs-you")])
    _write_archive(review, "20260917T000000Z", [dropped, kept])
    _write_provenance(review, [kept], plan_hash="2" * 64)

    plan, _, _, _ = plan_for_project(
        _Project({dropped: CURRENT_BIN, kept: CURRENT_BIN}),
        str(tmp_path), MASTER)
    verdicts = {v.name: v for v in plan.verdicts if v.kind == "timeline"}
    assert verdicts[dropped].state == org.EARLIER
    assert verdicts[dropped].destination == EARLIER_BIN
    assert verdicts[kept].state == org.CURRENT
    moves = {v.name: v for v in plan.moves if v.kind == "timeline"}
    assert set(moves) == {dropped}
    assert moves[dropped].destination == EARLIER_BIN
    stamps = {s["name"]: s for s in plan.stamps}
    assert stamps[dropped]["state"] == org.EARLIER
    # Moved and relabelled: the verdict and the stamp are the relabelling,
    # and the timeline is still filed somewhere - nothing deletes.
    assert dropped in verdicts


def test_only_approved_moments_are_current(tmp_path):
    """Proposed and rejected moments are the captain's undecided and
    refused - neither is the plan, so neither keeps a reel current."""
    review = _review(tmp_path)
    _write_live_plan(review, [
        _moment(1, "kept-slug", "approved"),
        _moment(2, "undecided-slug", "proposed"),
        _moment(3, "refused-slug", "rejected"),
    ])
    assert current_plan_names(str(tmp_path), None) == {_name(1, "kept-slug")}


def test_an_unreadable_plan_falls_back_to_provenance_instead_of_demoting(
        tmp_path, capsys):
    """No plan file is 'nothing here can say' - the filing keeps what the
    provenance record names instead of emptying Current plan. Said loudly,
    because a filing by a stale record is a guess being kept quiet."""
    kept = _name(2, "keyword-stuffing-now-costs-you")
    review = _review(tmp_path)
    review.mkdir(parents=True)
    _write_provenance(review, [kept], plan_hash="2" * 64)

    assert current_plan_names(
        str(tmp_path), {"built_reels": [kept]}) == {kept}
    assert "provenance" in capsys.readouterr().err

    plan, _, _, _ = plan_for_project(
        _Project({kept: CURRENT_BIN}), str(tmp_path), MASTER)
    assert {v.name: v.state for v in plan.verdicts
            if v.kind == "timeline"} == {kept: org.CURRENT}
    assert [v for v in plan.moves if v.kind == "timeline"] == []




def test_a_reel_the_captain_placed_by_hand_stays_where_they_put_it(
        tmp_path):
    """The existing rule survives the new source: a plan-named timeline
    in a bin outside the managed layout is where the captain put it."""
    kept = _name(2, "keyword-stuffing-now-costs-you")
    review = _review(tmp_path)
    _write_live_plan(review, [_moment(2, "keyword-stuffing-now-costs-you")])
    _write_provenance(review, [kept], plan_hash="2" * 64)

    project = _Project({})
    hand_bin = _Folder("Fully approved", clips=[_Clip(kept, "id-hand")])
    reels = next(s for s in project._root.GetSubFolderList()
                 if s.GetName() == bins.REELS_BIN)
    reels._subs.append(hand_bin)

    plan, _, _, _ = plan_for_project(project, str(tmp_path), MASTER)
    assert [v for v in plan.verdicts if v.name == kept] == []
    assert [v for v in plan.moves if v.name == kept] == []
    assert [s for s in plan.stamps if s["name"] == kept] == []
    assert kept in {name for name, _ in plan.left_alone}
