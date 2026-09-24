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


# ── Durability: the store, twice ─────────────────────────────────────
