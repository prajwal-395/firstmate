"""A recorded keep exclusion cuts an APPROVED moment's build ranges - one
reel in, one reel out, fewer seconds.

History: docs/evidence/reel_take_cuts.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
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
    reel with a hole where the struck seconds were - and the built
    timeline's OWN speech (`played_speech` over the cut ranges) carries
    the kept 'so what else' and none of the struck 'so what do they'."""
    from library.tools.reel_build import reel_ranges
    from library.tools.reel_quality_bar import played_speech
    tx = _false_start_tx()
    moment = _moment(0.0, 40.0)
    strike = [(10.0, 10.9, "lc-0002")]
    assert reel_ranges(moment, tx, extra_cuts=strike) == [
        (0.0, 10.0), (10.9, 40.0)]
    before = [w["word"] for line in played_speech(moment, tx, with_words=True)
              for w in line.get("words", [])]
    assert before.count("so") == 2  # false start AND kept opening
    after = [w["word"] for line in played_speech(
        moment, tx, with_words=True, extra_cuts=strike)
        for w in line.get("words", [])]
    assert "else" in after  # the kept continuation still plays
    assert after.count("so") == 1  # only the kept one
