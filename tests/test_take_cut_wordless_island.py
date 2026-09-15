"""Wordless islands stranded BETWEEN take cuts are absorbed, not placed.

The census for vep-yeah-breaks-reels walked all 22 approved reels'
keep ranges and found one reel carrying the same strand-a-nub shape
as the two fixed deaths: Reel 15
(`the-3d-nail-art-salon-beats-the-chains`, master 1186.94-1251.21s)
keeps 1221.51-1221.73s - 0.22s of room tone between dropping "if
you're a salon that specializes in 3D nail art" (1219.06-1221.51) and
"and your content is built around that niche, ..." (1221.73-1227.05).
Placing that island is a 5-frame picture+audio item the F7 floor
refuses - the death Reel 13 died with its strike's tail (PR 1058).
The strike path absorbs its own edge-dust
(`absorb_wordless_remnants`); the take path never did, so the island
survived `reel_ranges` and waited for the gate.

Numbers below are the field test's own, copied verbatim from
`pipeline_output/scratch/timeline_transcript/transcript.json` and the
approved reel span. No test here reads that project (AGENTS.md 8):
the measurement travels as data so the case runs anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from library.tools import reel_build
from library.tools.reel_build import (
    Cut,
    ReelBuildError,
    absorb_wordless_take_gaps,
    keep_ranges,
)
from library.tools.reel_conformance_verifier import (
    FindingClass,
    TimelineItem,
    check_short_av_items,
)

FPS = 24000 / 1001  # 23.976 exact - the reels' own frame rate
MXF = "/m/a.MXF"

#: Reel 15's span, verbatim from the approved proposal.
START, END = 1186.94, 1251.21


def _words(text, start, ends):
    parts = text.split(" ")
    assert len(parts) == len(ends), (text, ends)
    return [{"word": word, "start": start if i == 0 else ends[i - 1],
             "end": ends[i], "timed": True}
            for i, word in enumerate(parts)]


def _transcript(extra_segments=()):
    """The island's own neighbourhood, verbatim, plus span padding.

    Only the words the absorb checks are stated with measured times;
    the padding segments carry the span so `keep_ranges` has a body.
    """
    return {"segments": [
        {"speaker": "Akshita", "resolve_item_id": "pad-1",
         "text": "AI cares about who matters the most",
         "timeline_start": 1214.57, "timeline_end": 1218.62,
         "bound": True,
         "words": _words(
             "AI cares about who matters the most", 1214.57,
             [1214.81, 1215.13, 1215.55, 1216.26, 1217.00, 1217.60,
              1218.62])},
        {"speaker": "Akshita", "resolve_item_id": "drop-1",
         "text": "if you're a salon that specializes in 3D nail art",
         "timeline_start": 1219.06, "timeline_end": 1221.51,
         "bound": True,
         "words": _words(
             "if you're a salon that specializes in 3D nail art", 1219.06,
             [1219.103, 1219.304, 1219.344, 1219.765, 1219.925, 1220.607,
              1220.728, 1221.089, 1221.329, 1221.510])},
        {"speaker": "Akshita", "resolve_item_id": "drop-2",
         "text": "and your content is built around that niche",
         "timeline_start": 1221.73, "timeline_end": 1227.05,
         "bound": True,
         "words": _words(
             "and your content is built around that niche", 1221.73,
             [1221.811, 1221.972, 1222.433, 1222.574, 1222.915, 1223.276,
              1223.497, 1223.919])},
        {"speaker": "Akshita", "resolve_item_id": "pad-2",
         "text": "maybe hundreds of locations",
         "timeline_start": 1227.23, "timeline_end": 1228.58,
         "bound": True,
         "words": _words(
             "maybe hundreds of locations", 1227.23,
             [1227.49, 1227.85, 1227.95, 1228.58])},
        *extra_segments,
    ]}


def _cuts():
    """The two take cuts the engine derives on this span, verbatim:
    what they drop, and the kept takes they drop it for."""
    return [
        Cut(dropped_start=1219.06, dropped_end=1221.51,
            dropped_text="if you're a salon that specializes in 3D nail art",
            kept_start=1231.91, kept_end=1234.46,
            kept_text=("and if you're a salon that specializes in "
                       "3D nail art,"),
            speaker="Akshita", containment=1.0, jaccard=0.875),
        Cut(dropped_start=1221.73, dropped_end=1227.05,
            dropped_text=("and your content is built around that niche, "
                          "AI is going to pull you over nail salons"),
            kept_start=1237.33, kept_end=1243.03,
            kept_text=("and your content is built around that niche, "
                       "AI could definitely pull you over"),
            speaker="Akshita", containment=1.0, jaccard=0.875),
    ]


def _moment():
    return SimpleNamespace(timeline_start=START, timeline_end=END,
                           number=15,
                           slug="the-3d-nail-art-salon-beats-the-chains",
                           timeline_name="Reel 15", call_to_action=None)


def _items(ranges):
    out = []
    for a, b in ranges:
        frames = int(round((b - a) * FPS))
        out.append(TimelineItem(
            track_type="video", track_index=1,
            start_frame=int(round(a * FPS)),
            end_frame=int(round(a * FPS)) + frames,
            duration_frames=frames,
            source_start_frame=0, source_end_frame=frames,
            source_file=MXF, speaker="Akshita", name="take"))
    return out


# ── The island ───────────────────────────────────────────────────────

def test_the_island_survives_take_cuts_without_the_absorb():
    """The shape, stated without the fix: two cuts, one kept sliver."""
    ranges = keep_ranges(START, END, _cuts())
    assert (1221.51, 1221.73) in [(round(a, 2), round(b, 2))
                                  for a, b in ranges], ranges


def test_the_island_is_wordless_room_tone():
    """'art' ends 1221.510 and 'and' starts 1221.731: 221ms of room
    tone, the fix's precondition."""
    assert reel_build._remnant_has_timed_words(
        1221.51, 1221.73, _transcript()) is None


def test_reel_ranges_absorbs_the_island(monkeypatch):
    """The fix, through the build funnel: the cutter's two cuts go in
    (pinned, so the scan's windowing cannot move them on a trimmed
    fixture) and no wordless sub-floor island comes out. The judge,
    the wholeness guard and the absorb all run for real."""
    monkeypatch.setattr(reel_build, "redundant_takes",
                        lambda start, end, transcript: _cuts())
    ranges = reel_build.reel_ranges(
        _moment(), _transcript(), extra_cuts=(), insisted_spans=())
    # NOTE: this goes through the real cutter scan on a trimmed
    # fixture, so it asserts the absorb's EFFECT (no wordless
    # sub-floor island) rather than the exact range list.
    for a, b in ranges:
        if b - a < reel_build.ABSORB_REMNANT_SECONDS:
            assert reel_build._remnant_has_timed_words(
                a, b, _transcript()) is not None, (a, b)


def test_absorb_wordless_take_gaps_merges_the_two_cuts():
    """Directly: the island range is gone, absorbed into the cut."""
    ranges = keep_ranges(START, END, _cuts())
    out = absorb_wordless_take_gaps(ranges, _cuts(), _transcript())
    assert (1221.51, 1221.73) not in [(round(a, 2), round(b, 2))
                                      for a, b in out], out
    assert (round(START, 2), 1219.06) in [(round(a, 2), round(b, 2))
                                          for a, b in out], out
    assert (1227.05, round(END, 2)) in [(round(a, 2), round(b, 2))
                                        for a, b in out], out


# ── The kill chain, both ways ────────────────────────────────────────

def test_the_island_placed_is_an_f7_error():
    """What the gate says about the unfixed ranges: the 0.22s island
    as a placed item is 5 frames, under the 12-frame floor."""
    ranges = keep_ranges(START, END, _cuts())
    findings = check_short_av_items("Reel 15", _items(ranges),
                                    _items(ranges), FPS)
    errors = [f for f in findings if f.severity == "error"]
    assert len(errors) == 2, [f.message for f in findings]
    assert all(f.finding_class == FindingClass.F7 for f in errors)


def test_the_absorbed_reel_clears_the_f7_floor():
    """And after the absorb: the same items, no findings."""
    ranges = absorb_wordless_take_gaps(keep_ranges(START, END, _cuts()),
                                       _cuts(), _transcript())
    assert check_short_av_items("Reel 15", _items(ranges),
                                _items(ranges), FPS) == []


# ── The refusal branch ───────────────────────────────────────────────

def test_a_spoken_fragment_between_take_cuts_refuses():
    """Absorbing speech would delete words the cutter kept, so the
    build refuses, naming the take cut - it never swallows the word."""
    transcript = _transcript(extra_segments=[{
        "speaker": "Akshita", "resolve_item_id": "stowaway",
        "text": "sorry", "timeline_start": 1221.55,
        "timeline_end": 1221.70, "bound": True,
        "words": [{"word": "sorry", "start": 1221.55, "end": 1221.70,
                   "timed": True}]}])
    with pytest.raises(ReelBuildError, match="take cut 1219.06-1221.51"):
        absorb_wordless_take_gaps(keep_ranges(START, END, _cuts()),
                                  _cuts(), transcript)


def test_a_long_pause_between_take_cuts_survives():
    """Past the floor the gap stops being edge-dust and becomes a real
    pause the reel plays - the absorb must not touch it."""
    cuts = [Cut(dropped_start=1219.06, dropped_end=1221.51,
                dropped_text="dropped", kept_start=1231.91,
                kept_end=1234.46, kept_text="kept", speaker="Akshita",
                containment=1.0, jaccard=0.875)]
    ranges = [(START, 1219.06), (1221.51, 1222.51), (1227.05, END)]
    assert absorb_wordless_take_gaps(
        ranges, cuts, _transcript()) == ranges


# ── The strike wording is untouched ──────────────────────────────────

def test_the_strike_refusal_still_names_the_exclusion():
    """The `kind` default keeps the strike message byte-identical: a
    spoken strike remnant still names the keep exclusion."""
    transcript = _transcript(extra_segments=[{
        "speaker": "Akshita", "resolve_item_id": "stowaway",
        "text": "sorry", "timeline_start": 1227.10,
        "timeline_end": 1227.30, "bound": True,
        "words": [{"word": "sorry", "start": 1227.10, "end": 1227.30,
                   "timed": True}]}])
    ranges = [(START, 1219.06), (1227.05, 1227.40), (1227.60, END)]
    with pytest.raises(ReelBuildError, match="keep exclusion 'lc-x'"):
        reel_build.absorb_wordless_remnants(
            ranges, [(1219.06, 1227.05, "lc-x")], transcript)
