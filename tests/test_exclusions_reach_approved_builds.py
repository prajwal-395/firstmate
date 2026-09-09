"""A recorded exclusion reaches an approved moment's build.

The captain struck "so what do they" (lc-0002, 653.42-654.35s) on
APPROVED Reel 09 three times, and the build played it every time.
Selection enforces strikes on NEW proposals only
(`select_reels/post_bridge.py`), and `rebuild_reels_in_project` never
read the store - so the guard meant to stop the ENGINE re-deciding an
approved range also stopped the CAPTAIN's own strikes on exactly the
reels they annotated. These tests pin the fix:

- a strike cuts the approved build's ranges (`reel_ranges(extra_cuts)`):
  one reel in, one reel out, fewer seconds - never two;
- the selection-time trim-or-drop (`apply_keep_exclusions`) is UNCHANGED,
  so the no-split rule still holds where proposals are drawn;
- a strike the build cannot honour (whole body gone, edge through a
  word) is refused or dropped WITH its reason, never placed silently.

Fail-before: `reel_ranges` takes no `extra_cuts`, and
`transcript_corrections` has no `exclusion_cuts_for_span` - the first
two tests error on import/call, not on assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _seg(speaker, text, start, end, uid="u", words=()):
    seg = {"speaker": speaker, "text": text, "timeline_start": start,
           "timeline_end": end, "source_file": "/m/a.MXF",
           "source_start": start, "source_end": end,
           "resolve_item_id": uid}
    if words:
        seg["words"] = [
            {"word": w, "start": s, "end": e, "timed": True}
            for w, s, e in words]
    return seg


def _tx(*segments):
    return {"segments": list(segments)}


def _moment(start, end, number=9):
    from library.tools.reel_proposal import ReelMoment
    return ReelMoment(
        number=number, slug="your-website-is-only-20-percent",
        reason="a complete exchange",
        timeline_start=start, timeline_end=end)


def _false_start_tx():
    """Craig's 653.421-656.760s segment in miniature: the false start
    'so what do they' (10.0-10.9s here) the captain struck, then the
    kept 'so what else ...' from 11.8s on."""
    return _tx(
        _seg("Akshita", "very important to be aware.", 5.0, 9.0, "u0"),
        _seg("Craig", "so what do they so what else do they need",
             10.0, 15.0, "u1",
             words=[("so", 10.0, 10.2), ("what", 10.25, 10.45),
                    ("do", 10.5, 10.65), ("they", 10.7, 10.9),
                    ("so", 11.8, 12.0), ("what", 12.05, 12.25),
                    ("else", 12.3, 12.5)]),
        _seg("Craig", "where is ai pulling all this from", 16.0, 19.0,
             "u2"),
    )


# ── The store half: overlaps clipped to the span ─────────────────────

def test_exclusion_cuts_clip_to_the_span_and_ignore_the_rest():
    from library.tools import transcript_corrections as tc
    exclusions = [{"start": 10.0, "end": 10.9, "id": "lc-0002",
                   "reason": "captain: false start"}]
    assert tc.exclusion_cuts_for_span(0.0, 40.0, exclusions) == [
        (10.0, 10.9, "lc-0002")]
    # Clipped, not dropped: a strike overhanging the span's edge still
    # cuts what it touches of it.
    assert tc.exclusion_cuts_for_span(10.5, 40.0, exclusions) == [
        (10.5, 10.9, "lc-0002")]
    # Nothing touching: the common case builds exactly what it built.
    assert tc.exclusion_cuts_for_span(0.0, 40.0, []) == []
    assert tc.exclusion_cuts_for_span(11.0, 40.0, exclusions) == []


# ── The build half: one reel in, one reel out, fewer seconds ─────────

def test_a_mid_moment_strike_cuts_the_builds_ranges_not_the_reel():
    """The bug, by demonstration: an interior strike used to be
    unreachable at build time (selection drops such moments, and the
    build never read the store), so approved Reel 09 kept playing the
    struck false start. Now it cuts the ranges - the reel stays ONE
    reel with a hole where the struck seconds were."""
    from library.tools.reel_build import reel_ranges
    ranges = reel_ranges(
        _moment(0.0, 40.0), _false_start_tx(),
        extra_cuts=[(10.0, 10.9, "lc-0002")])
    assert ranges == [(0.0, 10.0), (10.9, 40.0)]


def test_no_extra_cuts_builds_exactly_what_it_built_before():
    """The default is the old behaviour: every caller without a project
    in hand is untouched."""
    from library.tools.reel_build import reel_ranges
    assert reel_ranges(_moment(0.0, 40.0), _false_start_tx()) == [
        (0.0, 40.0)]


def test_an_edge_strike_trims_without_touching_the_far_edge():
    from library.tools.reel_build import reel_ranges
    ranges = reel_ranges(
        _moment(20.0, 40.0), _false_start_tx(),
        extra_cuts=[(18.0, 22.0, "lc-0007")])
    assert ranges == [(22.0, 40.0)]


def test_struck_words_are_absent_from_what_plays():
    """The captain's proof, at unit level: the built timeline's OWN
    speech (`played_speech` over the cut ranges, word entries) carries
    the kept 'so what else' and none of the struck 'so what do they'."""
    from library.tools.reel_quality_bar import played_speech
    tx = _false_start_tx()
    moment = _moment(0.0, 40.0)
    before = [w["word"] for line in played_speech(moment, tx, with_words=True)
              for w in line.get("words", [])]
    assert before.count("so") == 2  # false start AND kept opening
    after = [w["word"] for line in played_speech(
        moment, tx, with_words=True, extra_cuts=[(10.0, 10.9, "lc-0002")])
        for w in line.get("words", [])]
    assert "else" in after  # the kept continuation still plays
    first_so = [w for w in after if w == "so"]
    assert len(first_so) == 1  # only the kept one


# ── The neighbouring rule still holds ────────────────────────────────

def test_selection_time_interior_exclusion_still_drops_with_reason():
    """`apply_keep_exclusions` is UNCHANGED: at SELECTION time an
    interior strike drops the moment with the reason, because selection
    can only offer one window and two would be a new reel. The build
    cuts; the proposal drops; neither splits."""
    from library.tools import transcript_corrections as tc
    moments = [{"start": 20.0, "end": 40.0, "slug": "reel-09"}]
    exclusions = [{"start": 28.0, "end": 30.0, "id": "lc-0003",
                   "reason": "captain: mistake in the middle"}]
    kept, dropped = tc.apply_keep_exclusions(moments, exclusions)
    assert not kept
    assert len(dropped) == 1 and "lc-0003" in dropped[0]["reason"]


def test_a_strike_grows_back_over_wordless_clip_leadin():
    """Reel 09's shape: the clipped word starts at 10.0s but the
    previous word ends at 9.0s with silence between. The cut grows to
    9.0s - room tone nobody hears - so no sub-floor nub is left for
    the readability floor to refuse."""
    from library.tools import transcript_corrections as tc
    tx = _tx(
        _seg("Akshita", "very important to be aware.", 5.0, 9.0, "u0",
             words=[("aware.", 8.4, 9.0)]),
        _seg("Craig", "so what do they so what else", 10.0, 15.0, "u1",
             words=[("so", 10.0, 10.2), ("what", 10.25, 10.45)]),
    )
    cuts = tc.exclusion_cuts_for_span(
        0.0, 40.0, [{"start": 10.0, "end": 10.9, "id": "lc-0002",
                     "reason": "captain: false start"}])
    grown = tc.grow_cuts_over_wordless_leadin(cuts, tx)
    assert grown == [(9.0, 10.9, "lc-0002")]
    from library.tools.reel_build import reel_ranges
    assert reel_ranges(_moment(0.0, 40.0), tx,
                       extra_cuts=grown) == [(0.0, 9.0), (10.9, 40.0)]


def test_growth_stops_where_a_word_starts():
    """A word starting inside the gap spans the cut's own start - growing
    over it would eat speech, so the interval stands as recorded (and
    the mid-word check below refuses it, naming the strike)."""
    from library.tools import transcript_corrections as tc
    tx = _tx(
        _seg("Craig", "well so what", 9.0, 15.0, "u1",
             words=[("well", 9.0, 9.5), ("so", 9.8, 10.3)]),
    )
    cuts = [(10.0, 10.9, "lc-0002")]
    assert tc.grow_cuts_over_wordless_leadin(cuts, tx) == cuts


def test_a_wordless_clip_leadin_nub_is_absorbed_not_placed():
    """Reel 09's own defect: the master clip starts 0.28s before the
    struck word, so cutting at the word strands a 6-frame nub the F7
    floor refuses. Room tone times nothing, so the cut extends over
    the silence rather than building a refusal."""
    from library.tools.reel_build import reel_ranges
    tx = _tx(
        _seg("Akshita", "very important to be aware.", 5.0, 9.0, "u0"),
        _seg("Craig", "so what do they so what else", 10.0, 15.0, "u1",
             words=[("so", 10.0, 10.2), ("what", 10.25, 10.45),
                    ("do", 10.5, 10.65), ("they", 10.7, 10.9),
                    ("so", 11.8, 12.0)]),
    )
    # The struck word starts at 10.0 but the moment's kept audio starts
    # at 9.72 (clip lead-in, no timed words): the subtraction strands
    # (9.72, 10.0), which is absorbed into the strike.
    ranges = reel_ranges(_moment(9.72, 40.0), tx,
                         extra_cuts=[(10.0, 10.9, "lc-0002")])
    assert ranges == [(10.9, 40.0)]


def test_a_nub_carrying_speech_refuses_instead():
    """The other half of the rule: absorbing over words would delete
    speech the captain never struck, so a worded remnant refuses,
    naming the exclusion to re-record."""
    from library.tools.reel_build import ReelBuildError, reel_ranges
    tx = _tx(
        _seg("Craig", "well so what do they", 9.72, 15.0, "u1",
             words=[("well", 9.75, 9.95), ("so", 10.0, 10.2),
                    ("what", 10.25, 10.45), ("do", 10.5, 10.65),
                    ("they", 10.7, 10.9)]),
    )
    try:
        reel_ranges(_moment(9.72, 40.0), tx,
                    extra_cuts=[(10.0, 10.9, "lc-0002")])
    except ReelBuildError as refused:
        assert "lc-0002" in str(refused) and "well" in str(refused)
    else:
        raise AssertionError("a worded remnant must refuse, not absorb")


def test_a_strike_covering_the_whole_body_drops_with_reason():
    """Nothing would play, so no empty timeline is built: the reel is
    dropped WITH the reason (the build-time shape of no-split), and the
    loop turns this into a skip rather than a batch-killing refusal."""
    from library.tools.reel_build import ExclusionWipesBody, reel_ranges
    try:
        reel_ranges(_moment(0.0, 40.0), _false_start_tx(),
                    extra_cuts=[(0.0, 40.0, "lc-0009")])
    except ExclusionWipesBody as wiped:
        assert "lc-0009" in str(wiped)
    else:
        raise AssertionError("a wholly-struck body must not build")


def test_a_strike_edge_through_a_word_refuses_naming_the_strike():
    """Placing half a word and then jumping is the R07 defect; a strike
    edge at 10.55s sits inside 'do' (10.5-10.65s). Refused, naming the
    exclusion to re-record - the approved span itself stays as drawn."""
    from library.tools.reel_build import ReelBuildError, reel_ranges
    try:
        reel_ranges(_moment(0.0, 40.0), _false_start_tx(),
                    extra_cuts=[(10.55, 10.9, "lc-0011")])
    except ReelBuildError as refused:
        assert "lc-0011" in str(refused) and "do" in str(refused)
    else:
        raise AssertionError("a mid-word strike edge must refuse")


def test_a_bound_edge_wins_over_a_straddling_phantom():
    """The 01:02:14 tail in miniature: a straddling row with no source
    claims 5.0-9.9s ('well', ending exactly where the strike ends),
    while a BOUND word really ends at the strike's start edge (9.0s).
    The bound edge is ground truth about audible sound - the phantom
    cannot testify - and the strike's end sits exactly ON the
    phantom's own end, which is already clean. So the cut stands."""
    from library.tools.reel_build import (
        exclusion_midword_edges, reel_ranges)
    tx = _tx(
        _seg("Craig", "well", 5.0, 9.9, uid=None),
        _seg("Akshita", "completely misrecommended.", 7.0, 9.0, "u1",
             words=[("completely", 7.0, 8.0),
                    ("misrecommended.", 8.4, 9.0)]),
    )
    tx["segments"][0]["resolve_item_id"] = None
    intervals = [(9.0, 9.9, "lc-0014")]
    assert exclusion_midword_edges(0.0, 40.0, intervals, tx) == []
    assert reel_ranges(_moment(0.0, 40.0), tx,
                       extra_cuts=intervals) == [(0.0, 9.0), (9.9, 40.0)]


# ── Durability: the store, twice ─────────────────────────────────────

def test_a_recorded_strike_survives_a_second_read(tmp_path):
    """'Persisted through iterations' at unit level: the strike lives
    in the project's learned store, and two successive reads derive the
    same cut - no in-memory trim that one rebuild carries and the next
    forgets."""
    from library.tools import transcript_corrections as tc
    rec = tc.record_keep_exclusion(
        str(tmp_path), 10.0, 10.9,
        reason="captain: 'so what do they' feels like a mistake")
    assert rec["source"]["correction_type"] == tc.KEEP_EXCLUSION
    first = tc.exclusion_cuts_for_span(
        0.0, 40.0, tc.keep_exclusions(str(tmp_path)))
    second = tc.exclusion_cuts_for_span(
        0.0, 40.0, tc.keep_exclusions(str(tmp_path)))
    assert first == second == [(10.0, 10.9, rec["id"])]
    ranges_first = [(0.0, 40.0)]
    from library.tools.reel_build import subtract_interval_cuts
    assert (subtract_interval_cuts(ranges_first, first)
            == subtract_interval_cuts(ranges_first, second)
            == [(0.0, 10.0), (10.9, 40.0)])
