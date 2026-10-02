"""`judge_take_cuts` withdraws only take cuts that are structurally
indefensible as "one telling removed, a later one kept", each with its
reason, and leaves working cuts alone.

History: docs/evidence/reel_take_cuts.md.
"""
from __future__ import annotations
from types import SimpleNamespace
from library.tools import reel_build
import sys
from pathlib import Path
import json
import pytest
from library.tools.reel_proposal import (
    SNAP_DECISION_SECONDS,
    Approval,
    ReelMoment,
    preview_snap,
    write_proposal,
)
from library.tools.take_pick_preview import (
    THRESHOLD_DEFAULT,
    TakePickPreviewError,
    boundary_words,
    main,
    preview_take,
    render_take_preview,
    resolve_moment,
)
from library.tools import transcript_corrections as tc
from library.steps.step_3_04_select_reels import post_bridge
from library.tools.reel_build import (
    Cut,
    DURATION_RATIO,
    ReelBuildError,
    assert_takes_are_whole,
    redundant_takes,
    refused_take_groups,
)
from library.tools import retake_scan
from library.tools.reel_build import (
    CUT_WINDOW_SECONDS,
    suspected_takes,
)
from library.tools.reel_proposal import (
    CallToAction,
    enrich,
    validate_proposal,
)


def _words(text, start, ends, speaker="Craig"):
    parts = text.split(" ")
    return [{"word": word, "start": start if i == 0 else ends[i - 1],
             "end": ends[i], "timed": True}
            for i, word in enumerate(parts)]


def _seg(speaker, text, start, ends, uid="u"):
    return {"speaker": speaker, "text": text,
            "timeline_start": start, "timeline_end": ends[-1],
            "resolve_item_id": uid,
            "words": _words(text, start, ends)}


def _lc0004_transcript():
    """lc-0004's own words and seconds, exactly as
    `test_keep_insistence` states them: a lead-in plus one Craig
    sentence carrying a rhetorical triple, which the word-stream scan
    reads as a retake. Trimmed fixtures do NOT reproduce the cut (the
    weak 3-word band needs the full stream), so the words are whole."""
    lead_in = [
        ("their", 9.154, 9.414), ("cmo", 9.515, 10.077),
        ("or", 10.137, 10.237), ("their", 10.257, 10.378),
        ("head", 10.398, 10.518), ("of", 10.558, 10.638),
        ("marketing", 10.699, 11.040), ("hey", 11.060, 11.120),
    ]
    craig_words = [
        ("we've", 11.241, 11.401), ("got", 11.421, 11.522),
        ("to", 11.542, 11.582), ("get", 11.622, 11.723),
        ("into", 11.763, 11.924), ("geo", 11.984, 12.245),
        ("geo", 12.285, 12.526), ("geo", 12.586, 12.888),
        ("i", 13.008, 13.109), ("get", 13.149, 13.350),
        ("it", 13.390, 13.450), ("it's", 13.912, 14.033),
        ("something", 14.053, 14.314), ("that's", 14.334, 14.495),
        ("going", 14.515, 14.656), ("to", 14.676, 14.736),
        ("continually", 14.796, 15.298), ("eat", 15.459, 15.580),
        ("into", 15.600, 15.680),
    ]

    def _raw(words, item):
        first, last = words[0], words[-1]
        return {"timeline_start": first[1], "timeline_end": last[2],
                "speaker": "Craig", "resolve_item_id": item,
                "text": " ".join(w for w, _s, _e in words),
                "words": [{"word": w, "start": s, "end": e, "timed": True}
                          for w, s, e in words]}

    return {"segments": [_raw(lead_in, "clip-0"),
                         _raw(craig_words, "clip-1")]}


def _cut(dropped, kept, speaker="Craig", cont=0.667, jac=0.667):
    return reel_build.Cut(
        dropped_start=dropped[0], dropped_end=dropped[1],
        dropped_text="dropped", kept_start=kept[0], kept_end=kept[1],
        kept_text="kept", speaker=speaker,
        containment=cont, jaccard=jac)


# ── J1-J5: each structurally indefensible cut is withdrawn by name ──

def test_each_indefensible_cut_is_withdrawn_by_name():
    """J1 inverted (Reel 15's live defect: a word-stream window ran
    backwards across two simultaneous speakers), J1 causality (a kept
    span starting before the dropped span ends is bleed, not a retake),
    J4 cross-speaker (two voices saying DIFFERENT things at once), J5 a
    dropped edge strictly inside a timed word."""
    two_voices = {"segments": [
        _seg("Craig", "yeah so ai is actually better", 1200.588,
             [1200.70, 1200.90, 1201.10, 1201.30, 1201.50, 1201.70],
             uid="c"),
        _seg("Akshita", "quite another point being made now", 1200.633,
             [1200.75, 1200.95, 1201.15, 1201.35, 1201.45, 1201.55],
             uid="a"),
    ]}
    one_voice = {"segments": [
        _seg("Craig", "yeah so ai is actually better", 10.0,
             [10.2, 10.4, 10.6, 10.8, 10.9, 11.0], uid="c"),
    ]}
    mid_word = {"segments": [
        _seg("Akshita", "everything else on the broader web", 2428.626,
             [2428.95, 2429.17, 2429.33, 2429.50, 2429.76, 2429.86],
             uid="a1"),
        _seg("Akshita", "for example", 2429.90,
             [2430.00, 2430.48], uid="a2"),
    ]}
    cases = [
        (_cut((1203.06, 1200.63), (1201.59, 1203.39)), 1186.94, 1251.21,
         two_voices, "inverted_or_empty"),
        (_cut((10.0, 12.0), (11.0, 13.0)), 0.0, 20.0, one_voice,
         "spans_overlap"),
        (_cut((1200.60, 1201.40), (1202.00, 1203.00)), 1200.0, 1205.0,
         two_voices, "cross_speaker"),
        (_cut((2428.626, 2429.60), (2455.581, 2456.959), speaker="Akshita",
              cont=0.75, jac=0.60), 2420.0, 2460.0, mid_word,
         "mid_word_edge"),
    ]
    for cut, start, end, transcript, reason in cases:
        kept, withdrawn = reel_build.judge_take_cuts(
            [cut], start, end, transcript)
        assert kept == [], reason
        assert [w["reason"] for w in withdrawn] == [reason]


# ── J3/J4: a word-stream excision from inside one flowing utterance ──

def test_lc0004_the_mid_sentence_cut_is_withdrawn():
    """lc-0004: 'got to get into' (11.42-11.98) sits strictly inside one
    Craig segment (11.241-...) that flows on both sides of it. The
    candidate generator still proposes it; the judge withdraws it."""
    transcript = _lc0004_transcript()
    candidates = reel_build.redundant_takes(9.0, 16.0, transcript)
    assert len(candidates) == 1, (
        "the generator stopped seeing the repetition - the floor moved")
    kept, withdrawn = reel_build.judge_take_cuts(candidates, 9.0, 16.0,
                                                 transcript)
    assert kept == []
    assert [w["reason"] for w in withdrawn] == ["mid_utterance"]
    assert "11.42" in withdrawn[0]["why"]
    # At the build's own seam: the sentence plays whole with NO
    # insistence on file. Before the judge this returned two ranges.
    moment = SimpleNamespace(timeline_start=9.0, timeline_end=16.0,
                             number=1, slug="x", timeline_name="Reel 01 - x",
                             call_to_action=None)
    assert reel_build.reel_ranges(moment, transcript) == [(9.0, 16.0)]


# ── What the judge must NOT touch: the working cuts ──

def _pair_cut(dropped_seg, kept_seg):
    return reel_build.Cut(
        dropped_start=float(dropped_seg["timeline_start"]),
        dropped_end=float(dropped_seg["timeline_end"]),
        dropped_text=dropped_seg["text"],
        kept_start=float(kept_seg["timeline_start"]),
        kept_end=float(kept_seg["timeline_end"]),
        kept_text=kept_seg["text"],
        speaker=dropped_seg["speaker"],
        containment=1.0, jaccard=1.0)


def test_working_pair_cuts_survive_the_judge():
    """Reel 28's false start ('And I got,' -> 'I got some,') and Reel
    31's 'Yeah.' -> 'Yeah.': whole segments, same speaker, causal.
    Withdrawing either would regress an approved reel with no complaint
    against it."""
    transcript = {"segments": [
        _seg("Akshita", "and i got", 2244.995,
             [2245.16, 2245.20, 2246.01], uid="a1"),
        _seg("Akshita", "and that is a very specific query", 2246.07,
             [2246.15, 2246.25, 2246.31, 2246.36, 2246.56, 2247.01,
              2247.25],
             uid="a2"),
        _seg("Akshita", "i got some", 2251.833,
             [2251.91, 2252.16, 2252.37], uid="a3"),
    ]}
    segs = transcript["segments"]
    cuts = [_pair_cut(segs[0], segs[2])]
    kept, withdrawn = reel_build.judge_take_cuts(cuts, 2244.0, 2253.0,
                                                 transcript)
    assert withdrawn == []
    assert kept == cuts
    # Reel 03's shape: finely segmented takes where the dropped span
    # covers whole segments - withdrawing interior excisions must not
    # take whole-telling removals with it.
    reel03 = {"segments": [
        _seg("Akshita", "search did not change", 301.24,
             [301.40, 301.70, 302.00, 302.57], uid="a1"),
        _seg("Akshita", "the question changed", 302.63,
             [302.80, 303.10, 303.45], uid="a2"),
        _seg("Akshita", "search did not change the question changed", 310.0,
             [310.20, 310.40, 310.60, 310.80, 311.00, 311.30, 311.60],
             uid="a3"),
    ]}
    whole = reel_build.Cut(
        dropped_start=301.24, dropped_end=303.45,
        dropped_text="search did not change the question changed",
        kept_start=310.0, kept_end=311.60,
        kept_text="search did not change the question changed",
        speaker="Akshita", containment=1.0, jaccard=1.0)
    assert reel_build.judge_take_cuts([whole], 300.0, 315.0, reel03) == (
        [whole], [])


# ── Through the build's own ranges ──

def test_the_build_no_longer_duplicates_seconds():
    """Reel 15's live defect, reproduced: a word-stream window straddling
    two SIMULTANEOUS speakers' segments runs backwards in time, so the
    generator proposes NEGATIVE cuts and `keep_ranges` lays the ranges
    over each other - seconds play twice. The judge withdraws every such
    cut, so `reel_ranges` stays disjoint.

    The words are tuned so the backwards windows actually match at the
    0.65 bar: Craig's tail (zeta eta theta) recurs inside Akshita's
    overlapping line, and the stream order (Craig's segment first)
    against the clock (Akshita's words earlier) turns the match
    upside-down."""
    transcript = {"segments": [
        _seg("Craig", "delta epsilon zeta eta theta", 9.0,
             [9.30, 10.40, 10.50, 10.60, 10.70], uid="c-long"),
        _seg("Akshita", "zeta eta theta iota zeta eta theta", 9.05,
             [9.15, 9.25, 9.35, 9.45, 9.55, 9.65, 9.75], uid="a-overlap"),
    ]}
    candidates = reel_build.redundant_takes(8.0, 12.0, transcript)
    assert len(candidates) >= 1, (
        "the generator stopped proposing the backwards cut - the floor "
        "moved")
    assert any(c.dropped_end <= c.dropped_start for c in candidates), (
        "expected the backwards (negative-range) cut")
    kept, withdrawn = reel_build.judge_take_cuts(candidates, 8.0, 12.0,
                                                 transcript)
    assert kept == []
    assert withdrawn, "every backwards cut must be withdrawn with a reason"
    assert {w["reason"] for w in withdrawn} <= {
        "inverted_or_empty", "spans_overlap", "cross_speaker",
        "mid_utterance", "mid_word_edge"}
    moment = SimpleNamespace(timeline_start=8.0, timeline_end=12.0,
                             number=15, slug="x", timeline_name="R15",
                             call_to_action=None)
    ranges = reel_build.reel_ranges(moment, transcript)
    for (a, b), (c, d) in zip(ranges, ranges[1:]):
        assert b <= c, f"overlapping ranges play seconds twice: {ranges}"
    assert ranges == sorted(ranges)


# ── Reel 08: mic bleed is one mic, not two voices ──

def _reel08_transcript(craig_text="Yeah, so ranking tells Google."):
    """Reel 08's marked telling, real texts and real word timings.

    Akshita says "Yeah, so ranking tells Google," twice running; the
    second telling adds "that you exist." Craig's track carries her
    first telling's words at her own seconds - his mic hearing her,
    not a second voice. `craig_text` swaps his words for the control.
    """
    return {"segments": [
        _seg("Akshita", "Mm-hmm.", 613.64, [614.57], uid="a0"),
        _seg("Akshita", "Yeah, so ranking tells Google,", 614.72,
             [614.91, 615.09, 615.44, 615.85, 616.48], uid="a1"),
        _seg("Craig", craig_text, 614.77,
             [614.97, 615.15, 615.48, 615.88, 616.48], uid="c1"),
        _seg("Akshita", "ranking tells Google that you exist.", 616.51,
             [616.79, 617.03, 617.35, 617.48, 617.62, 618.12], uid="a2"),
    ]}


def test_bleed_of_the_same_words_is_not_a_second_voice():
    """Reel 08's shape, stated input: the pair scan's cut drops
    Akshita's first telling (containment 0.750, Jaccard 0.600) and the
    judge used to withdraw it as cross_speaker on Craig's bleed. One
    telling heard on two mics is one telling, so the cut survives."""
    transcript = _reel08_transcript()
    cut = reel_build.Cut(
        dropped_start=614.72, dropped_end=616.48,
        dropped_text="Yeah, so ranking tells Google,",
        kept_start=616.51, kept_end=618.12,
        kept_text="ranking tells Google that you exist.",
        speaker="Akshita", containment=0.750, jaccard=0.600)
    kept, withdrawn = reel_build.judge_take_cuts(
        [cut], 588.258, 622.375, transcript)
    assert withdrawn == []
    assert kept == [cut]
    # The control: Craig saying DIFFERENT words at the same seconds is
    # an interruption, not bleed, and the cut is still withdrawn.
    kept, withdrawn = reel_build.judge_take_cuts(
        [cut], 588.258, 622.375,
        _reel08_transcript(craig_text="Right, that lands well today."))
    assert kept == []
    assert [w["reason"] for w in withdrawn] == ["cross_speaker"]


# --------------------------------------------------------------------------
# From test_take_cut_wordless_island.py
#
# Wordless islands stranded BETWEEN take cuts are absorbed, not placed as
# sub-floor F7 items; a spoken fragment there refuses by name.
#
# History: docs/evidence/reel_take_cuts.md.

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


from library.tools.reel_build import (
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


def _words_2(text, start, ends):
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
         "words": _words_2(
             "AI cares about who matters the most", 1214.57,
             [1214.81, 1215.13, 1215.55, 1216.26, 1217.00, 1217.60,
              1218.62])},
        {"speaker": "Akshita", "resolve_item_id": "drop-1",
         "text": "if you're a salon that specializes in 3D nail art",
         "timeline_start": 1219.06, "timeline_end": 1221.51,
         "bound": True,
         "words": _words_2(
             "if you're a salon that specializes in 3D nail art", 1219.06,
             [1219.103, 1219.304, 1219.344, 1219.765, 1219.925, 1220.607,
              1220.728, 1221.089, 1221.329, 1221.510])},
        {"speaker": "Akshita", "resolve_item_id": "drop-2",
         "text": "and your content is built around that niche",
         "timeline_start": 1221.73, "timeline_end": 1227.05,
         "bound": True,
         "words": _words_2(
             "and your content is built around that niche", 1221.73,
             [1221.811, 1221.972, 1222.433, 1222.574, 1222.915, 1223.276,
              1223.497, 1223.919])},
        {"speaker": "Akshita", "resolve_item_id": "pad-2",
         "text": "maybe hundreds of locations",
         "timeline_start": 1227.23, "timeline_end": 1228.58,
         "bound": True,
         "words": _words_2(
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
    _a_long_pause_between_take_cuts_survives()


# ── The kill chain, both ways ────────────────────────────────────────

def test_the_island_placed_is_an_f7_error():
    """The shape without the fix - two cuts leave a kept 0.22s sliver -
    and what the gate says about it: placed, the island is 5 frames,
    under the 12-frame floor."""
    ranges = keep_ranges(START, END, _cuts())
    assert (1221.51, 1221.73) in [(round(a, 2), round(b, 2))
                                  for a, b in ranges], ranges
    findings = check_short_av_items("Reel 15", _items(ranges),
                                    _items(ranges), FPS)
    errors = [f for f in findings if f.severity == "error"]
    assert len(errors) == 2, [f.message for f in findings]
    assert all(f.finding_class == FindingClass.F7 for f in errors)


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
    # The strike path's refusal still names the keep exclusion.
    strike = _transcript(extra_segments=[{
        "speaker": "Akshita", "resolve_item_id": "stowaway",
        "text": "sorry", "timeline_start": 1227.10,
        "timeline_end": 1227.30, "bound": True,
        "words": [{"word": "sorry", "start": 1227.10, "end": 1227.30,
                   "timed": True}]}])
    with pytest.raises(ReelBuildError, match="keep exclusion 'lc-x'"):
        reel_build.absorb_wordless_remnants(
            [(START, 1219.06), (1227.05, 1227.40), (1227.60, END)],
            [(1219.06, 1227.05, "lc-x")], strike)


def _a_long_pause_between_take_cuts_survives():
    """Past the floor the gap stops being edge-dust and becomes a real
    pause the reel plays - the absorb must not touch it."""
    cuts = [Cut(dropped_start=1219.06, dropped_end=1221.51,
                dropped_text="dropped", kept_start=1231.91,
                kept_end=1234.46, kept_text="kept", speaker="Akshita",
                containment=1.0, jaccard=0.875)]
    ranges = [(START, 1219.06), (1221.51, 1222.51), (1227.05, END)]
    assert absorb_wordless_take_gaps(
        ranges, cuts, _transcript()) == ranges


# --------------------------------------------------------------------------
# From test_take_pick_preview.py
#
# One reel's take-pick preview, in a single read.
#
# Each test names the defect it catches: a preview for the wrong reel,
# an empty report that reads like a clean one, a second implementation
# of the snap or the take scan drifting from the build, quiet edges
# with no words, and a drifted trim the preview stays silent about.

def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "timed": True}


def _seg_2(text, start, end, words):
    return {
        "speaker": "Host",
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "source_file": "/m/a.MXF",
        "source_start": 100.0,
        "source_end": 100.0 + (end - start),
        "resolve_item_id": "uid",
        "words": words,
    }


def _transcript_2():
    return {
        "segments": [
            _seg_2(
                "alpha beta",
                10.0,
                20.0,
                [_w("alpha", 10.5, 11.0), _w("beta", 12.0, 12.5)],
            ),
            _seg_2(
                "we launched the rocket",
                20.0,
                24.0,
                [
                    _w("we", 20.0, 20.2),
                    _w("launched", 20.3, 20.7),
                    _w("the", 20.8, 20.9),
                    _w("rocket", 21.0, 21.5),
                ],
            ),
            _seg_2(
                "gamma delta",
                19.5,
                30.0,
                [_w("gamma", 20.0, 20.5), _w("delta", 22.0, 22.5)],
            ),
            _seg_2(
                "we launched the rocket today",
                26.0,
                30.0,
                [
                    _w("we", 26.0, 26.2),
                    _w("launched", 26.3, 26.7),
                    _w("the", 26.8, 26.9),
                    _w("rocket", 27.0, 27.5),
                    _w("today", 27.6, 28.0),
                ],
            ),
            _seg_2(
                "epsilon zeta",
                29.5,
                40.0,
                [_w("epsilon", 31.0, 31.5), _w("zeta", 33.0, 33.5)],
            ),
        ]
    }


def _moment_2(number=7, start=19.7, end=33.6, slug="rocket-take"):
    return ReelMoment(
        number=number,
        slug=slug,
        reason="a complete exchange",
        timeline_start=start,
        timeline_end=end,
        approval=Approval.APPROVED,
    )


def _write_project(tmp_path, moments, transcript=None):
    """A scratch project holding a real proposal + transcript file."""
    from library.tools.reel_proposal import proposal_path
    from library.tools.timeline_transcript import transcript_path

    project = tmp_path / "project"
    transcript = _transcript_2() if transcript is None else transcript
    transcript_file = Path(transcript_path(str(project)))
    transcript_file.parent.mkdir(parents=True)
    transcript_file.write_text(json.dumps(transcript), encoding="utf-8")
    proposal_file = Path(proposal_path(str(project)))
    proposal_file.parent.mkdir(parents=True)
    write_proposal(proposal_file, moments, transcript)
    return project


# ------------------------------------------------- reuse, not rewrite


def test_the_snap_section_is_the_builds_own_preview(tmp_path):
    """`preview_take` narrows `preview_snap` to one moment rather than
    reimplementing it (byte-equal to the build's own call, at the snap
    bar), and its candidate cuts are `redundant_takes` over this reel's
    body - what the build will cut, not a second scan with its own bars."""
    from library.tools.reel_build import redundant_takes

    project = _write_project(tmp_path, [_moment_2(), _moment_2(8, 40.0, 45.0, "other")])
    report = preview_take(str(project), "7")
    assert THRESHOLD_DEFAULT == SNAP_DECISION_SECONDS
    assert report["snap"] == preview_snap(
        [_moment_2()], _transcript_2(), THRESHOLD_DEFAULT, tail_extend_authorizations={}
    )
    assert {m["reel"] for m in report["snap"]["moments"]} == {7}
    expected = redundant_takes(19.7, 33.6, _transcript_2())
    assert expected, "the fixture must hold a take or this pins nothing"
    assert [
        (t["dropped_start"], t["dropped_end"], t["kept_start"], t["kept_end"])
        for t in report["takes"]
    ] == [(c.dropped_start, c.dropped_end, c.kept_start, c.kept_end) for c in expected]


# ------------------------------------------------- addressing


def test_a_reel_answers_to_number_slug_and_timeline_name(tmp_path):
    """Number, slug and full timeline name all reach the same moment:
    a worker holding any one of the three spellings gets this reel."""
    project = _write_project(tmp_path, [_moment_2()])
    by_number = preview_take(str(project), "7")
    assert preview_take(str(project), "rocket-take")["slug"] == (by_number["slug"])
    assert (
        preview_take(str(project), "Reel 07 - rocket-take")["slug"]
        == (by_number["slug"])
    )
    _an_unknown_reel_refuses_rather_than_previewing_a_neighbour(tmp_path / "second")


def _an_unknown_reel_refuses_rather_than_previewing_a_neighbour(tmp_path):
    """A take-pick answered for the wrong reel is confidently wrong -
    worse than no answer - so an unknown reel is a refusal, and the
    CLI exits 2 rather than printing a neighbour's report."""
    tmp_path.mkdir()
    project = _write_project(tmp_path, [_moment_2()])
    with pytest.raises(TakePickPreviewError):
        resolve_moment([_moment_2()], "99")
    assert main([str(project), "--reel", "99"]) == 2
    # No proposal is not a reel with no takes, no drift and no snap: an
    # empty report would read as a clean one, so this refuses too.
    assert main([str(tmp_path / "empty"), "--reel", "7"]) == 2


# ------------------------------------------------- words once


def test_a_quiet_edge_still_names_its_words(tmp_path):
    """Edges the snap does not move get no pulled-in list - but the
    take-pick still needs the words there. Without this section the
    worker re-reads the whole transcript per quiet edge."""
    tx = {
        "segments": [
            _seg_2(
                "alpha beta gamma",
                10.0,
                20.0,
                [
                    _w("alpha", 10.5, 11.0),
                    _w("beta", 12.0, 12.5),
                    _w("gamma", 14.0, 14.5),
                ],
            ),
            _seg_2(
                "delta epsilon",
                20.0,
                30.0,
                [_w("delta", 21.0, 21.5), _w("epsilon", 23.0, 23.5)],
            ),
        ]
    }
    project = _write_project(tmp_path, [_moment_2(7, 10.0, 30.0)], tx)
    report = preview_take(str(project), "7")
    assert report["snap"]["moved"] == 0
    edges = {e["boundary"]: e for e in report["boundaries"]}
    assert [t["word"] for t in edges["body_start"]["words_after"]] == [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
    ]
    assert [t["word"] for t in edges["body_end"]["words_before"]] == [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
    ]
    text = render_take_preview(report)
    assert "words:" in text
    # The pivot walks one word stream: words at or before the edge land
    # before it, words after land after, nothing dropped at the joint.
    words = boundary_words(_transcript_2(), 20.0)
    assert [t["word"] for t in words["before"]][-3:] == ["alpha", "beta", "we"]
    assert [t["word"] for t in words["after"]][:2] == ["launched", "the"]


# ------------------------------------------------- freshness


def _write_edits(project, edits):
    from library.tools.captain_edits import edits_path

    path = Path(edits_path(str(project)))
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"key": "captain_edits", "source": "test", "value": edits}),
        encoding="utf-8",
    )


def test_a_drifted_trim_is_loud_and_a_stale_one_named(tmp_path):
    """The Reel 17 shape: the head pin's anchor re-timed since it was
    recorded, and a second pin's words are spoken nowhere. A preview
    silent about either would send the worker to approve takes under
    trims the build will place elsewhere - or not at all."""
    tx = {
        "segments": [
            _seg_2(
                "alpha beta gamma",
                10.0,
                20.0,
                [
                    _w("alpha", 10.5, 11.0),
                    _w("beta", 12.0, 12.5),
                    _w("gamma", 14.0, 14.5),
                ],
            ),
            _seg_2(
                "delta epsilon",
                20.0,
                30.0,
                [_w("delta", 21.0, 21.5), _w("epsilon", 23.0, 23.5)],
            ),
        ]
    }
    project = _write_project(tmp_path, [_moment_2(7, 10.0, 30.0)], tx)
    _write_edits(
        project,
        [
            {
                "kind": "span_retime",
                "anchor_phrase": "alpha beta",
                "edge": "head",
                "recorded_edge": 12.0,
                "reason": "trim the head onto the opening",
            },
            {
                "kind": "span_retime",
                "anchor_phrase": "never spoken words",
                "edge": "tail",
                "reason": "a pin to nowhere",
            },
        ],
    )
    report = preview_take(str(project), "7")
    drifted = report["freshness"]["drifted"]
    stale = report["freshness"]["stale"]
    assert len(drifted) == 1
    assert drifted[0]["anchor_phrase"] == "alpha beta"
    assert any(s.get("anchor_phrase") == "never spoken words" for s in stale)
    text = render_take_preview(report)
    assert "DRIFTED" in text and "STALE" in text


# --------------------------------------------------------------------------
# From test_take_verdict_cascade.py
#
# Each recorded duplicate-take fix replays through the build's exact
# cascade (exclusion cuts -> lead-in/tail growth -> `reel_ranges`): the bad
# take is gone, the kept telling plays whole, nothing refuses.
#
# History: docs/evidence/reel_take_cuts.md.

def _w_2(entries):
    """Timed words from `(text, start, end[, timed])` triples."""
    out = []
    for entry in entries:
        text, start, end = entry[0], entry[1], entry[2]
        timed = entry[3] if len(entry) > 3 else True
        out.append({"word": text, "start": start, "end": end, "timed": timed})
    return out


def _even(text, start, end):
    """Evenly spread timed words - interior filler only, never an edge
    under test."""
    parts = text.split(" ")
    step = (end - start) / max(len(parts), 1)
    return [
        {
            "word": word,
            "start": start + i * step,
            "end": start + (i + 1) * step,
            "timed": True,
        }
        for i, word in enumerate(parts)
    ]


def _seg_3(speaker, text, start, end, words, uid="u"):
    assert len(words) == len(text.split(" ")), (
        f"{text!r}: {len(words)} timings for {len(text.split(' '))} words"
    )
    return {
        "speaker": speaker,
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "resolve_item_id": uid,
        "words": words,
    }


def _cascade(body, spec, transcript):
    """The build's exact cascade for one recorded strike."""
    cuts = tc.exclusion_cuts_for_span(
        body[0], body[1], [{"start": spec[0], "end": spec[1], "id": "SPEC"}]
    )
    cuts = tc.grow_cuts_over_wordless_leadin(cuts, transcript)
    cuts, _held = tc.grow_cuts_over_wordless_tail(cuts, transcript)
    moment = SimpleNamespace(timeline_start=body[0], timeline_end=body[1])
    return reel_build.reel_ranges(moment, transcript, extra_cuts=cuts)


def _struck(ranges, span):
    """No played range touches the struck span."""
    return all(b <= span[0] or a >= span[1] for a, b in ranges)


def _plays(ranges, span):
    """Some played range covers the whole span."""
    return any(a <= span[0] and b >= span[1] for a, b in ranges)


# ── Reel 06: doubled answer + flubbed telling (lc-0093) ──


def _reel06():
    craig = _seg_3(
        "Craig",
        "So in this case size really doesn't matter.",
        422.68,
        425.48,
        _even("So in this case size really doesn't", 422.68, 425.13)
        + _w_2([("matter.", 425.13, 425.48)]),
        uid="c0",
    )
    first = _seg_3(
        "Akshita",
        "No, it doesn't.",
        425.87,
        426.66,
        _w_2(
            [
                ("No,", 425.87, 426.16),
                ("it", 426.16, 426.25),
                ("doesn't.", 426.25, 426.66),
            ]
        ),
        uid="a0",
    )
    flub = _seg_3(
        "Akshita",
        "Um I've seen 10-person agencies get recommended in AI over "
        "companies fifty times their size.",
        426.51,
        432.36,
        _w_2(
            [
                ("Um", 426.51, 426.63),
                ("I've", 426.70, 427.11),
                ("seen", 427.11, 427.96),
                ("10-person", 428.01, 428.03, False),
                ("agencies", 427.96, 428.66),
                ("get", 428.66, 428.78),
                ("recommended", 428.78, 429.28),
                ("in", 429.28, 429.40),
                ("AI", 429.40, 429.83),
                ("over", 429.83, 430.18),
                ("companies", 430.18, 430.98),
                ("fifty", 431.19, 431.43),
                ("times", 431.43, 431.74),
                ("their", 431.74, 431.86),
                ("size.", 431.86, 432.36),
            ]
        ),
        uid="a1",
    )
    nod = _seg_3(
        "Akshita",
        "Not at all.",
        432.67,
        433.09,
        _w_2([("Not", 432.67, 432.82), ("at", 432.82, 432.89), ("all.", 432.89, 433.09)]),
        uid="a2",
    )
    retake = _seg_3(
        "Akshita",
        "I've seen a 10-person agency get recommended over a company "
        "fifty times their size, and that's because every source they "
        "have consistently tells the same story.",
        432.83,
        440.30,
        _w_2([("I've", 432.83, 433.13)])
        + _even(
            "seen a 10-person agency get recommended over a company "
            "fifty times their size, and that's because every source "
            "they have consistently tells the same story.",
            433.13,
            440.30,
        ),
        uid="a3",
    )
    return {"segments": [craig, first, flub, nod, retake]}


def test_each_recorded_verdict_strikes_its_take_and_keeps_the_telling():
    """lc-0093 (425.87-432.36): the doubled answer and the flubbed
    telling go; "Not at all." and the clean retake play."""
    transcript = _reel06()
    ranges = _cascade((422.75, 461.24), (425.87, 432.36), transcript)
    assert _struck(ranges, (425.87, 432.36)), f"bad take still plays: {ranges}"
    assert _plays(ranges, (432.67, 433.09)), f"'Not at all.' lost: {ranges}"
    assert _plays(ranges, (432.83, 434.0)), f"retake opening lost: {ranges}"
    _reel21_verdict_keeps_the_restart()
    _reel24_verdict_removes_the_whole_bad_take()
    _reel03_verdict_keeps_the_recovery_whole()


# ── Reel 11: consolidation is a take choice (lc-0094) ──


def _reel11():
    craig = _seg_3(
        "Craig",
        "your website can be perfectly fine, but a lot of times "
        "everything else is broken.",
        776.32,
        781.31,
        _even(
            "your website can be perfectly fine, but a lot of times everything else is",
            776.32,
            780.83,
        )
        + _w_2([("broken.", 780.83, 781.31)]),
        uid="c0",
    )
    op = _seg_3(
        "Akshita",
        "Absolutely.",
        781.92,
        782.63,
        _w_2([("Absolutely.", 781.92, 782.63)]),
        uid="a0",
    )
    first = _seg_3(
        "Akshita",
        "Your website is your resume and everything else are your "
        "references and just like a hiring manner hiring manager would "
        "check both, AI checks both.",
        782.39,
        790.22,
        _w_2(
            [
                ("Your", 782.39, 782.68),
                ("website", 782.68, 783.21),
                ("is", 783.21, 783.37),
                ("your", 783.37, 783.46),
                ("resume", 783.46, 783.84),
                ("and", 783.84, 784.10),
                ("everything", 784.19, 784.61),
                ("else", 784.61, 784.81),
                ("are", 784.81, 784.87),
                ("your", 784.87, 784.99),
                ("references", 784.99, 785.79),
                ("and", 785.89, 786.21),
                ("just", 786.21, 786.43),
                ("like", 786.43, 786.57),
                ("a", 786.57, 786.63),
                ("hiring", 786.63, 787.01),
                ("manner", 787.01, 787.34),
                ("hiring", 787.55, 787.93),
                ("manager", 787.93, 788.30),
                ("would", 788.30, 788.43),
                ("check", 788.43, 788.80),
                ("both,", 788.94, 789.29),
                ("AI", 789.50, 789.71),
                ("checks", 789.71, 789.99),
                ("both.", 789.99, 790.22),
            ]
        ),
        uid="a1",
    )
    retake = _seg_3(
        "Akshita",
        "Yeah, so your website is your resume and everything else are "
        "your references, and just like a hiring manager would "
        "definitely check both, AI checks both.",
        790.55,
        797.57,
        _w_2([("Yeah,", 790.55, 790.72)])
        + _even(
            "so your website is your resume and everything else are "
            "your references, and just like a hiring manager would "
            "definitely check both, AI checks both.",
            790.72,
            797.57,
        ),
        uid="a2",
    )
    return {"segments": [craig, op, first, retake]}


def test_reel11_verdict_consolidates_two_tellings():
    """lc-0094 (781.92-790.22): "Absolutely." and the stumbled first
    telling go; the restated telling plays. The pair scan finds this
    pair and the judge correctly withdraws it (mid-word edge), so only
    the verdict removes it."""
    transcript = _reel11()
    cuts = reel_build.redundant_takes(775.64, 819.13, transcript)
    kept, withdrawn = reel_build.judge_take_cuts(cuts, 775.64, 819.13, transcript)
    assert kept == [], f"nothing mechanical may cut here: {kept}"
    assert [w["reason"] for w in withdrawn] == ["mid_word_edge"]
    ranges = _cascade((775.64, 819.13), (781.92, 790.22), transcript)
    assert _struck(ranges, (781.92, 790.22)), f"first telling still plays: {ranges}"
    assert _plays(ranges, (790.55, 791.5)), f"restated telling lost: {ranges}"


# ── Reel 21: abandoned run-up, restart kept (lc-0095) ──


def _reel21():
    abandoned = _seg_3(
        "Akshita",
        "If that's the case that's a problem and your website is just "
        "a part of what's maybe",
        1794.86,
        1800.39,
        _even("If that's the", 1794.86, 1795.39)
        + _w_2(
            [
                ("case", 1795.39, 1795.79),
                ("that's", 1795.92, 1796.17),
                ("a", 1796.17, 1796.24),
                ("problem", 1796.24, 1796.82),
                ("and", 1796.89, 1797.06),
                ("your", 1797.06, 1797.22),
                ("website", 1797.22, 1797.87),
                ("is", 1797.87, 1798.14),
                ("just", 1798.14, 1798.48),
                ("a", 1798.55, 1798.60),
                ("part", 1798.60, 1799.31),
                ("of", 1799.34, 1799.51),
                ("what's", 1799.51, 1799.90),
                ("maybe", 1799.90, 1800.39),
            ]
        ),
        uid="a0",
    )
    restated = _seg_3(
        "Akshita",
        "and your website is just a part of your whole profile and "
        "that's only twenty percent.",
        1800.62,
        1804.88,
        _w_2([("and", 1800.62, 1800.71)])
        + _even(
            "your website is just a part of your whole profile and "
            "that's only twenty percent.",
            1800.71,
            1804.88,
        ),
        uid="a1",
    )
    return {"segments": [abandoned, restated]}


def _reel21_verdict_keeps_the_restart():
    """lc-0095 (1796.89-1800.39): the run-up tail goes; the setup and
    the restated telling play. An aborted telling that restarts clean
    keeps the RESTART, not the first attempt."""
    transcript = _reel21()
    ranges = _cascade((1743.05, 1814.68), (1796.89, 1800.39), transcript)
    assert _struck(ranges, (1796.89, 1800.39)), f"run-up still plays: {ranges}"
    assert _plays(ranges, (1795.39, 1796.82)), (
        f"setup ('that's a problem') lost: {ranges}"
    )
    assert _plays(ranges, (1800.62, 1801.5)), f"restated telling lost: {ranges}"


# ── Reel 24: whole bad-take segment (lc-0096) ──


def _reel24():
    prior = _seg_3(
        "Akshita",
        "The reason is YouTube counts as a very trustworthy source of "
        "information, geo optimized,",
        2032.40,
        2051.26,
        _even(
            "The reason is YouTube counts as a very trustworthy "
            "source of information, geo",
            2032.40,
            2050.68,
        )
        + _w_2([("optimized,", 2050.68, 2051.26)]),
        uid="a0",
    )
    bad = _seg_3(
        "Akshita",
        "make sure there's a very strong um okay there's a very strong concise but",
        2051.46,
        2058.18,
        _w_2(
            [
                ("make", 2051.46, 2051.61),
                ("sure", 2051.61, 2051.77),
                ("there's", 2051.77, 2051.99),
                ("a", 2051.99, 2052.05),
                ("very", 2052.05, 2052.67),
                ("strong", 2052.95, 2053.66),
                ("um", 2054.17, 2054.64),
                ("okay", 2055.27, 2055.47),
                ("there's", 2055.55, 2055.73),
                ("a", 2055.73, 2055.77),
                ("very", 2055.77, 2055.94),
                ("strong", 2055.94, 2056.45),
                ("concise", 2056.45, 2057.24),
                ("but", 2057.78, 2058.18),
            ]
        ),
        uid="a1",
    )
    completion = _seg_3(
        "Akshita",
        "that there's a very concise description of what exactly your video is about.",
        2058.53,
        2061.98,
        _w_2([("that", 2058.53, 2058.66)])
        + _even(
            "there's a very concise description of what exactly your video is about.",
            2058.66,
            2061.98,
        ),
        uid="a2",
    )
    return {"segments": [prior, bad, completion]}


def _reel24_verdict_removes_the_whole_bad_take():
    """lc-0096 (2051.46-2058.18): every bit of the bad take goes; the
    concise completion plays."""
    transcript = _reel24()
    ranges = _cascade((2009.53, 2079.92), (2051.46, 2058.18), transcript)
    assert _struck(ranges, (2051.46, 2058.18)), f"bad take still plays: {ranges}"
    assert _plays(ranges, (2058.53, 2059.8)), f"completion lost: {ranges}"


# ── Reel 03: mid-sentence stumble, recovery whole (lc-0097) ──


def _reel03():
    head = (
        "So instead of someone searching C Rm small business on "
        "Google, now they're going to Chat GPT and instead"
    )
    tail = "the best CRM if I run a 10 person law firm?"
    segment = _seg_3(
        "Akshita",
        head + " typing best C RMs, um s best what's " + tail,
        232.71,
        244.16,
        _even(head, 232.71, 238.00)
        + _w_2(
            [
                ("typing", 238.00, 238.48),
                ("best", 238.71, 238.93),
                ("C", 238.93, 239.04),
                ("RMs,", 239.04, 239.82),
                ("um", 239.82, 240.16),
                ("s", 240.20, 240.39),
                ("best", 241.58, 241.85),
                ("what's", 241.85, 242.02),
                ("the", 242.02, 242.10),
            ]
        )
        + _even("best CRM if I run a 10 person law firm?", 242.10, 244.16),
        uid="a0",
    )
    return {"segments": [segment]}


def _reel03_verdict_keeps_the_recovery_whole():
    """lc-0097 (238.71-241.85): the stumble goes; the recovery plays
    whole from "what's"."""
    transcript = _reel03()
    ranges = _cascade((213.26, 248.27), (238.71, 241.85), transcript)
    assert _struck(ranges, (238.71, 241.85)), f"stumble still plays: {ranges}"
    assert _plays(ranges, (241.85, 243.0)), f"recovery lost: {ranges}"
    assert _plays(ranges, (237.0, 238.48)), f"lead-in ('typing') lost: {ranges}"


# ── Reel 08: the mechanical cut (no verdict recorded) ──


def _reel08():
    return {
        "segments": [
            _seg_3(
                "Akshita",
                "Mm-hmm.",
                613.64,
                614.57,
                _w_2([("Mm-hmm.", 613.64, 614.57)]),
                uid="a0",
            ),
            _seg_3(
                "Akshita",
                "Yeah, so ranking tells Google,",
                614.72,
                616.48,
                _w_2(
                    [
                        ("Yeah,", 614.72, 614.91),
                        ("so", 614.91, 615.09),
                        ("ranking", 615.09, 615.44),
                        ("tells", 615.44, 615.85),
                        ("Google,", 615.85, 616.48),
                    ]
                ),
                uid="a1",
            ),
            _seg_3(
                "Craig",
                "Yeah, so ranking tells Google.",
                614.77,
                616.48,
                _w_2(
                    [
                        ("Yeah,", 614.77, 614.97),
                        ("so", 614.97, 615.15),
                        ("ranking", 615.15, 615.48),
                        ("tells", 615.48, 615.88),
                        ("Google.", 615.88, 616.48),
                    ]
                ),
                uid="c1",
            ),
            _seg_3(
                "Akshita",
                "ranking tells Google that you exist.",
                616.51,
                618.12,
                _w_2(
                    [
                        ("ranking", 616.51, 616.79),
                        ("tells", 616.79, 617.03),
                        ("Google", 617.03, 617.35),
                        ("that", 617.35, 617.48),
                        ("you", 617.48, 617.62),
                        ("exist.", 617.62, 618.12),
                    ]
                ),
                uid="a2",
            ),
        ]
    }


def test_reel08_mechanical_cut_needs_no_verdict():
    """Reel 08's marked retake: the pair scan finds it (0.750/0.600),
    the bleed-aware judge keeps it, and the build drops the first
    telling - no recorded verdict required."""
    transcript = _reel08()
    cuts = reel_build.redundant_takes(588.258, 622.375, transcript)
    kept, withdrawn = reel_build.judge_take_cuts(cuts, 588.258, 622.375, transcript)
    assert [(round(c.dropped_start, 2), round(c.dropped_end, 2)) for c in kept] == [
        (614.72, 616.48)
    ]
    # The word-stream fragment inside the telling withdraws correctly -
    # cutting at 615.85 clips "tells" - while the whole-telling pair
    # cut survives it.
    assert [
        (round(w["cut"].dropped_start, 2), round(w["cut"].dropped_end, 2), w["reason"])
        for w in withdrawn
    ] == [(614.72, 615.85, "mid_word_edge")]
    moment = SimpleNamespace(timeline_start=588.258, timeline_end=622.375)
    ranges = reel_build.reel_ranges(moment, transcript)
    assert _struck(ranges, (614.72, 616.48)), f"doubled telling still plays: {ranges}"
    assert _plays(ranges, (616.51, 618.12)), f"completed telling lost: {ranges}"


# --------------------------------------------------------------------------
# From test_take_verdicts_recorded.py
#
# The model's take verdicts stick: `takes_dropped` becomes exclusions.
#
# Step 3.04's handoff has always asked the model for `takes_dropped` -
# "any repeated take you are choosing not to play" - and the post-bridge
# never read it, so every verdict evaporated with the run. The
# post-bridge now records each valid verdict as a keep exclusion
# (authored `model`, with the reel slug and the reason on the record),
# refuses invalid ones loudly, and never breaks the batch over either.
#
# All projects live under `tmp_path` (AGENTS.md 8).

def _words_3(start, end):
    out = []
    cursor = float(start)
    n = 0
    while cursor < float(end) - 1e-9:
        stop = min(cursor + 1.0, float(end))
        out.append({"word": f"word{n}", "start": cursor, "end": stop,
                    "timed": True})
        cursor = stop
        n += 1
    return out


def _transcript_3():
    return {"segments": [
        {"speaker": "Akshita", "text": "ten seconds of speech here",
         "timeline_start": 10.0, "timeline_end": 20.0,
         "resolve_item_id": "a", "source_file": "LC4932.MXF",
         "source_start": 0.0, "source_end": 10.0,
         "words": _words_3(10.0, 20.0)},
    ]}


def _survivor(entry, start=10.0, end=20.0, slug="test-reel"):
    return [(dict(entry), start, end, slug)]


def test_a_valid_verdict_is_recorded_with_its_reason(tmp_path):
    recorded, refused = post_bridge.record_take_verdicts(
        _survivor({"takes_dropped": [
            {"start": 12.0, "end": 15.0,
             "reason": "the second telling is tighter"}]}),
        _transcript_3(), str(tmp_path), 100.0)
    assert refused == []
    assert len(recorded) == 1
    assert recorded[0]["id"].startswith("lc-")
    stored = tc.keep_exclusions(str(tmp_path))
    assert len(stored) == 1
    assert stored[0]["author"] == "model"
    assert "test-reel" in stored[0]["reason"]
    assert "the second telling is tighter" in stored[0]["reason"]
    # A string verdict parses to the same record shape.
    recorded, refused = post_bridge.record_take_verdicts(
        _survivor({"takes_dropped": ["16-18 - second telling is tighter"]}),
        _transcript_3(), str(tmp_path), 100.0)
    assert refused == []
    assert [(r["start"], r["end"]) for r in recorded] == [(16.0, 18.0)]
    _an_exact_duplicate_is_not_re_recorded(tmp_path / "second")


def test_an_invalid_verdict_is_refused_and_nothing_stored(tmp_path):
    """No reason; the whole moment (that rejects the reel - `considered`'s
    job, not a take drop's); an edge through a word (the build would
    play half a word and jump); outside the timeline."""
    cases = [
        ({"start": 12.0, "end": 15.0}, None),
        ({"start": 10.0, "end": 20.0, "reason": "hate it"}, None),
        ({"start": 12.5, "end": 15.0, "reason": "tighter"}, "word"),
        ({"start": 200.0, "end": 205.0, "reason": "tighter"}, None),
    ]
    for verdict, said in cases:
        recorded, refused = post_bridge.record_take_verdicts(
            _survivor({"takes_dropped": [verdict]}),
            _transcript_3(), str(tmp_path), 100.0)
        assert recorded == [], verdict
        assert len(refused) == 1, verdict
        if said:
            assert said in refused[0]["reason"]
    assert tc.keep_exclusions(str(tmp_path)) == []


def _an_exact_duplicate_is_not_re_recorded(tmp_path):
    tmp_path.mkdir()
    tc.record_keep_exclusion(str(tmp_path), 12.0, 15.0,
                             "captain strike", author="captain")
    recorded, refused = post_bridge.record_take_verdicts(
        _survivor({"takes_dropped": [
            {"start": 12.0, "end": 15.0, "reason": "tighter"}]}),
        _transcript_3(), str(tmp_path), 100.0)
    assert recorded == []
    assert refused == []
    assert len(tc.keep_exclusions(str(tmp_path))) == 1


# --------------------------------------------------------------------------
# From test_reel_partial_take_cuts.py
#
# A repeated take is removed WHOLE or not at all (`redundant_runs`,
# `refused_take_groups`, `assert_takes_are_whole`); a withheld cut is
# reported, never silent. Reel 03's real segments travel as data.
#
# History: docs/evidence/reel_take_cuts.md.

def _seg_4(speaker, text, start, end, uid, words=None):
    segment = {"speaker": speaker, "text": text, "timeline_start": start,
               "timeline_end": end, "source_file": MXF,
               "source_start": start, "source_end": end,
               "resolve_item_id": uid}
    if words is not None:
        segment["words"] = [
            {"word": word, "start": at, "end": until, "timed": True}
            for word, at, until in words]
    return segment


TAKE_ONE = "3d0a-take-one"
TAKE_TWO = "55c6-take-two"

# Reel 03, master 301.241-341.270s. Take one is the first four lines;
# take two is the next four; the 309.92/310.12 pair is one utterance
# WhisperX emitted twice, 0.200s and 0.260s long, carrying the whole
# sentence as its text both times.
REEL_03_SEGMENTS = [
    _seg_4("Akshita", "Yeah, so search didn't change.", 301.241, 302.566,
         TAKE_ONE,
         [("Yeah,", 301.241, 301.441), ("so", 301.522, 301.642),
          ("search", 301.662, 301.883), ("didn't", 301.903, 302.164),
          ("change.", 302.204, 302.566)]),
    _seg_4("Akshita", "The question changed.", 302.626, 303.449, TAKE_ONE,
         [("The", 302.626, 302.706), ("question", 302.746, 303.068),
          ("changed.", 303.108, 303.449)]),
    _seg_4("Akshita", "And whoever AI best understands, gets the answer.",
         303.549, 306.400, TAKE_ONE,
         [("And", 303.549, 303.670), ("whoever", 303.971, 304.332),
          ("AI", 304.453, 304.633), ("best", 304.674, 304.894),
          ("understands,", 304.935, 305.577), ("gets", 305.938, 306.119),
          ("the", 306.139, 306.219), ("answer.", 306.259, 306.400)]),
    _seg_4("Akshita", "yeah", 306.400, 306.527, TAKE_ONE,
         [("yeah", 306.400, 306.527)]),
    _seg_4("Akshita", "So,yeah, so search didn't change", 306.801, 307.596,
         TAKE_TWO),
    _seg_4("Akshita", "The question changed.", 307.840, 308.840, TAKE_TWO),
    _seg_4("Akshita", "So,yeah", 308.840, 309.111, TAKE_TWO),
    _seg_4("Akshita", "and whoever AI understands best, gets the answer.",
         309.320, 309.920, TAKE_TWO),
    _seg_4("Akshita", "Yeah, so search didn't change. The question changed. "
                    "And whoever AI understands best, gets the answer.",
         309.920, 310.120, TAKE_TWO),
    _seg_4("Akshita", "Yeah, so search didn't change. The question changed. "
                    "And whoever AI understands best, gets the answer.",
         310.120, 310.380, TAKE_TWO),
    _seg_4("Akshita", "best will have the answers.", 310.380, 312.041, TAKE_TWO),
    _seg_4("Craig", "the questions change so that's why this is so important "
                  "that's who ai is going to recommend", 313.690, 317.950,
         "craig-1"),
    _seg_4("Craig", "to be the answer it's exactly why we've been building "
                  "this platform", 318.091, 321.530, "craig-2"),
]

REEL_03 = {"segments": REEL_03_SEGMENTS}
REEL_03_START, REEL_03_END = 301.241, 341.270

# What `redundant_takes` returned on this span before the rule landed:
# two of take one's four lines, and nothing for the other two.
THE_PARTIAL_CUT = [
    Cut(dropped_start=301.241, dropped_end=302.566,
        dropped_text="Yeah, so search didn't change.",
        kept_start=306.801, kept_end=307.596,
        kept_text="So,yeah, so search didn't change",
        speaker="Akshita", containment=1.0, jaccard=1.0),
    Cut(dropped_start=302.626, dropped_end=303.449,
        dropped_text="The question changed.",
        kept_start=307.840, kept_end=308.840,
        kept_text="The question changed.",
        speaker="Akshita", containment=1.0, jaccard=1.0),
]


# ── The coherent outcome happens ─────────────────────────────────────

def test_a_take_is_cut_whole_or_not_at_all():
    """Neither of take one's first two lines goes on its own.

    Before the rule this returned three cuts, two of them here.  The
    third line could not be paired safely, so none of the run may go.
    """
    cuts = redundant_takes(REEL_03_START, REEL_03_END, REEL_03)
    dropped = sorted(round(cut.dropped_start, 3) for cut in cuts)
    assert 301.241 not in dropped, cuts
    assert 302.626 not in dropped, cuts
    _a_repeated_run_that_can_go_whole_still_goes()


def _a_repeated_run_that_can_go_whole_still_goes():
    """The rule withholds partial cuts, not cutting.

    Both lines of this take pair safely, so both are removed - the same
    two-line shape reel 03's take one has, without the third line the
    duration test refuses.
    """
    line_one = "so last week we ran an audit on a client for their website"
    line_two = "and their SEO team had stuffed the H1 tags with keywords"
    transcript = {"segments": [
        _seg_4("Akshita", line_one, 10.0, 14.0, "a"),
        _seg_4("Akshita", line_two, 14.1, 18.0, "a"),
        _seg_4("Akshita", line_one, 20.0, 24.5, "b"),
        _seg_4("Akshita", line_two, 24.6, 28.0, "b"),
    ]}
    cuts = redundant_takes(0.0, 60.0, transcript)
    assert sorted(round(cut.dropped_start, 3) for cut in cuts) == [10.0, 14.1]


# ── The orphan outcome is refused ────────────────────────────────────

def test_a_partly_cut_take_is_refused():
    """The gate fires on the exact cut list the rebuild used.

    `assert_takes_are_whole` is asked of the list ABOUT TO BE APPLIED,
    whatever produced it, so a second producer cannot strand a fragment
    by going round `redundant_takes`.
    """
    with pytest.raises(ReelBuildError) as refusal:
        assert_takes_are_whole(THE_PARTIAL_CUT, REEL_03_START, REEL_03_END,
                               REEL_03)
    said = str(refusal.value)
    assert "WHOLE or not at all" in said
    assert "And whoever AI best understands, gets the answer." in said


# ── What the refusal SAYS ────────────────────────────────────────────

def test_the_refused_run_names_what_stopped_it_and_what_it_kept_in():
    """A withheld cut is reported, never silent.

    The report is what reaches the model at selection time
    (`reel_proposal.enrich`) and the operator at build time, because the
    repetition is STILL IN THE REEL and somebody has to know that.
    """
    groups = refused_take_groups(REEL_03_START, REEL_03_END, REEL_03)
    assert len(groups) == 1, groups
    group = groups[0]

    assert group["speaker"] == "Akshita"
    assert group["lines"][0] == "Yeah, so search didn't change."
    assert len(group["would_have_cut"]) == 2
    stopper = group["could_not_cut"][0]
    assert stopper["duration_ratio"] == 4.75
    assert f"DURATION_RATIO={DURATION_RATIO}" in stopper["refused_by"]
    assert "still in the reel" in group["why_nothing_was_cut"]
    # Only a run where a cut was actually taken away is reported: a lone
    # pair the duration test refuses on its own (Reels 07, 17, 18) has
    # nothing withheld, and calling it a refusal would describe a
    # difference the rule does not make.
    long_line = ("it is going to start hallucinating because it is confused "
                 "about what you actually do")
    lone = {"segments": [
        _seg_4("Akshita", long_line, 10.0, 14.3, "a"),
        _seg_4("Akshita", "confused about what you actually do hallucinating",
             15.0, 15.5, "b"),
    ]}
    assert redundant_takes(0.0, 60.0, lone) == []
    assert refused_take_groups(0.0, 60.0, lone) == []


# --------------------------------------------------------------------------
# From test_retake_scan.py
#
# `retake_scan` REPORTS false starts, paraphrases and cross-segment
# verbatims with both texts, a basis and a recommended strike; it cuts
# nothing, and legitimate repetition does not report.
#
# History: docs/evidence/reel_take_cuts.md.

def _even_words(text, start, end):
    """Timed words spread evenly across a span.

    Order-exact, timing-approximate: enough for detectors that read
    the wording and the segment boundaries, never for ones asserting
    exact word-edge spans (those carry real timings below).
    """
    parts = text.split(" ")
    n = len(parts)
    step = (end - start) / max(n, 1)
    return [{"word": word, "start": start + i * step,
             "end": start + (i + 1) * step, "timed": True}
            for i, word in enumerate(parts)]


def _seg_5(speaker, text, start, end, words=None, uid="u"):
    return {"speaker": speaker, "text": text,
            "timeline_start": start, "timeline_end": end,
            "resolve_item_id": uid, "source_file": "LC4932.MXF",
            "source_start": 0.0, "source_end": end - start,
            "words": words if words is not None
            else _even_words(text, start, end)}


def _real_words(entries, offset=0.0):
    return [{"word": word, "start": start + offset,
             "end": end + offset, "timed": True}
            for word, start, end in entries]


# ── Reel 06: a two-take pair past the pair-scan bars ──

def _reel06_transcript():
    """Akshita's two tellings, real texts and boundaries, even words.

    The pair scores containment 0.69 / Jaccard 0.39 - under both bars -
    while the shared head reads similarity 1.0 on the word stream.
    """
    return {"segments": [
        _seg_5("Akshita", "No, it doesn't.", 425.87, 426.66, uid="a0"),
        _seg_5("Akshita",
             "Um I've seen 10-person agencies get recommended in AI "
             "over companies fifty times their size.",
             426.51, 432.36, uid="a1"),
        _seg_5("Akshita", "Not at all.", 432.67, 433.09, uid="a2"),
        _seg_5("Akshita",
             "I've seen a 10-person agency get recommended over a "
             "company fifty times their size, and that's because every "
             "source they have consistently tells the same story.",
             432.83, 440.30, uid="a3"),
    ]}


def test_a_retake_shape_reports_its_strike():
    """Reel 06's shape: the head repeats word for word while the tails
    diverge past the pair-scan bars. Reported with the whole first
    telling as the strike - never cut, because a window is not a
    telling."""
    reported = retake_scan.scan_span(
        422.66, 461.40, _reel06_transcript())["reported"]
    verbatim = [c for c in reported if c["kind"] == "verbatim_retelling"]
    assert len(verbatim) == 1
    found = verbatim[0]
    assert (found["dropped_start"], found["dropped_end"]) == (426.51, 432.36)
    assert found["speaker"] == "Akshita"
    assert found["similarity"] == 1.0
    assert "426.51-432.36" in found["recommended_action"].replace(" ", "")
    _a_restart_inside_one_segment_reports_the_run_up()


# ── Reel 24: a false start and its restart in one segment ──

def _reel24_segment():
    """The marked segment, real text and real word timings."""
    words = _real_words([
        ("make", 2051.46, 2051.61), ("sure", 2051.61, 2051.77),
        ("there's", 2051.77, 2051.99), ("a", 2051.99, 2052.05),
        ("very", 2052.05, 2052.67), ("strong", 2052.95, 2053.66),
        ("um", 2054.17, 2054.64), ("okay", 2055.27, 2055.47),
        ("there's", 2055.55, 2055.73), ("a", 2055.73, 2055.77),
        ("very", 2055.77, 2055.94), ("strong", 2055.94, 2056.45),
        ("concise", 2056.45, 2057.24), ("but", 2057.78, 2058.18),
    ])
    return _seg_5("Akshita",
                "make sure there's a very strong um okay there's a "
                "very strong concise but",
                2051.46, 2058.18, words=words, uid="a1")


def _a_restart_inside_one_segment_reports_the_run_up():
    """Reel 24's shape: "there's a very strong" twice with "um okay"
    between, in a telling that ends on "but". The first copy is the
    flubbed run-up, at real word seconds."""
    transcript = {"segments": [
        _seg_5("Akshita", "YouTube Shorts, make sure that when you post.",
             2040.00, 2051.26, uid="a0"),
        _reel24_segment(),
        _seg_5("Akshita",
             "that there's a very concise description of your video.",
             2058.53, 2061.98, uid="a2"),
    ]}
    reported = retake_scan.scan_span(
        2040.00, 2070.00, transcript)["reported"]
    restarts = [c for c in reported
                if c["shape"] == "restart_inside_one_segment"]
    assert len(restarts) == 1
    found = restarts[0]
    assert found["kind"] == "false_start"
    assert (found["dropped_start"], found["dropped_end"]) == (2051.77, 2053.66)
    assert (found["kept_start"], found["kept_end"]) == (2055.55, 2056.45)


# ── Reel 21: an abandoned telling restated from its head ──

def _reel21_a_words():
    """Reel 21's abandoned telling, real text and real word timings."""
    return _real_words([
        ("If", 1794.86, 1795.04), ("that's", 1795.04, 1795.28),
        ("the", 1795.28, 1795.39), ("case", 1795.39, 1795.79),
        ("that's", 1795.92, 1796.17), ("a", 1796.17, 1796.24),
        ("problem", 1796.24, 1796.82), ("and", 1796.89, 1797.06),
        ("your", 1797.06, 1797.22), ("website", 1797.22, 1797.87),
        ("is", 1797.87, 1798.14), ("just", 1798.14, 1798.48),
        ("a", 1798.55, 1798.60), ("part", 1798.60, 1799.31),
        ("of", 1799.34, 1799.51), ("what's", 1799.51, 1799.90),
        ("maybe", 1799.90, 1800.39),
    ])


# ── Positive controls: legitimate repetition that must NOT report ──

def test_legitimate_repetition_does_not_report():
    """lc-0005's rhetorical triple ("geo geo geo", emphasis inside one
    telling); Reel 21's unmarked contrast questions (one shared content
    word, the first ends complete); parallel structure in one complete
    sentence. Real texts and boundaries, even words."""
    cases = [
        ((9.0, 16.0), [
            _seg_5("Craig", "their CMO or their head of marketing",
                 9.154, 11.120, uid="c0"),
            _seg_5("Craig",
                 "we've got to get into geo geo geo i get it it's "
                 "something that's going to continually eat into",
                 11.241, 15.680, uid="c1"),
        ]),
        ((1791.00, 1795.00), _contrast_questions()),
        ((99.0, 105.0), [
            _seg_5("Akshita",
                 "if you're gonna post daily you're gonna burn out fast.",
                 100.00, 104.00, uid="a0"),
        ]),
    ]
    for (start, end), segments in cases:
        assert retake_scan.scan_span(
            start, end, {"segments": segments})["reported"] == [], segments


def _contrast_questions():
    return [
        _seg_5("Akshita",
             "Are you actually showing up the way you want there?",
             1791.08, 1793.47, uid="a0"),
        _seg_5("Akshita", "Are you not even showing up at all?",
             1793.17, 1794.59, uid="a1"),
    ]


def test_telling_properties_measure_concise_and_clear():
    """The properties the model reads: durations, content-word counts,
    completeness, and disfluency with seconds."""
    transcript = {"segments": [
        _seg_5("Akshita",
             "If that's the case that's a problem and your website is "
             "just a part of what's maybe",
             1794.86, 1800.39, words=_reel21_a_words(), uid="a1"),
    ]}
    props = retake_scan.telling_properties(1794.86, 1800.39, transcript)
    assert props["duration_seconds"] == 5.53
    assert props["ends_complete"] is False
    assert props["content_words"] > 0
    assert props["disfluencies"] == []


# ── The selection bridge carries candidates to the model ──

def test_the_bridge_reports_candidates_with_a_verdict_line():
    """Reel 21's shape: "your website is just a part of what's maybe"
    restated as "your website is just a part of your whole profile".
    The scan reports the run-up's tail from the joining "and" (the setup
    "that's a problem" survives, at real word seconds), and
    `retake_candidates_inside` gives the model what judging takes: both
    tellings with measured properties, the basis, and a concrete
    recommended strike - marked as work the build will not do."""
    from library.steps.step_3_04_select_reels import bridge

    transcript = {"segments": [
        _seg_5("Akshita", "Are you not even showing up at all?",
             1793.17, 1794.59, uid="a0"),
        _seg_5("Akshita",
             "If that's the case that's a problem and your website is "
             "just a part of what's maybe",
             1794.86, 1800.39, words=_reel21_a_words(), uid="a1"),
        _seg_5("Akshita",
             "and your website is just a part of your whole profile "
             "and that's only twenty percent.",
             1800.62, 1804.88, uid="a2"),
    ]}
    reported = retake_scan.scan_span(
        1793.00, 1805.00, transcript)["reported"]
    assert [(c["kind"], c["shape"], c["kept_start"], c["kept_end"])
            for c in reported] == [
        ("false_start", "abandoned_telling_restated", 1800.62, 1804.88)]
    found = bridge.retake_candidates_inside(1793.00, 1805.00, transcript)
    assert len(found) == 1
    context = found[0]
    assert (context["dropped_start"], context["dropped_end"]) == (1796.89, 1800.39)
    assert context["judge"]["build_removes"] is False
    assert context["recommended_action"].startswith("strike 1796.89")
    assert context["dropped_ends_complete"] is False
    assert context["kept_ends_complete"] is True
    assert context["basis"]
    assert bridge.retake_candidates_inside(
        1791.00, 1795.00, {"segments": _contrast_questions()}) == []


# --------------------------------------------------------------------------
# From test_reel_distant_repeats.py
#
# Distant repeats are reported, never cut - and the CTA is scanned at all.
#
# By rule, CUT_WINDOW_SECONDS still gates every cut; a distant same-speaker
# pair becomes a suspect and a closer echoing the body is named. Whether a
# reported repeat is a retake or a callback is left to the model.
#
# History: `docs/evidence/reel_distant_repeats.md` (test_reel_distant_repeats.py).

TAGLINE = ("search didn't change, the question changed, whoever AI "
           "understands best gets the answer")


def _seg_6(speaker, text, start, end, uid="u"):
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": MXF,
            "source_start": start, "source_end": end,
            "resolve_item_id": uid}


def _reel_03_distant():
    """Reel-03 shape: the tagline twice in the body, 43.6s apart with
    Craig's turn between them, and a third time as the CTA."""
    return {"segments": [
        _seg_6("Akshita", "Yeah, " + TAGLINE, 301.2, 306.4, "a1"),
        _seg_6("Craig", "the questions change so that's why this is so "
                      "important", 313.7, 318.0, "c1"),
        _seg_6("Akshita", "Yeah, " + TAGLINE, 350.0, 355.2, "a2"),
        _seg_6("Craig", "to be the answer it's exactly why we've been "
                      "building this", 356.0, 360.0, "c2"),
        _seg_6("Akshita", TAGLINE, 900.0, 905.2, "cta1"),
    ]}


def _moment_03():
    return ReelMoment(
        number=3, slug="search-didnt-change",
        reason="the tightest thing said in the episode",
        timeline_start=300.0, timeline_end=365.0,
        approval=Approval.APPROVED,
        call_to_action=CallToAction(timeline_start=900.0,
                                    timeline_end=905.2))


BODY_START, BODY_END = 300.0, 365.0


def test_the_distant_tagline_pair_is_not_cut():
    """Distance alone cannot tell a callback from a retake, so the cut
    lane stays gated on CUT_WINDOW_SECONDS. FAILED before the fix only
    in the sense that nothing else saw it either - cuts stay []."""
    tx = _reel_03_distant()
    assert 350.0 - 306.4 > CUT_WINDOW_SECONDS
    assert redundant_takes(BODY_START, BODY_END, tx) == []


def test_the_distant_tagline_pair_is_suspected():
    """The ONLY reason the pair survives is distance - same speaker,
    containment and Jaccard over the cut bars, durations agreeing - so
    it joins the markers lane instead of vanishing. FAILED before the
    fix: suspected_takes broke at the same window and returned []."""
    found = suspected_takes(BODY_START, BODY_END, _reel_03_distant())
    starts = sorted(round(c.dropped_start, 2) for c in found)
    assert 301.2 in starts, found
    pair = next(c for c in found
                if round(c.dropped_start, 2) == 301.2)
    assert round(pair.kept_start, 2) == 350.0
    assert pair.speaker == "Akshita"


def test_the_closer_echo_reaches_the_model_at_selection_time():
    """`enrich` carries the echo so the span can still be redrawn or a
    different closer picked. A moment with no CTA carries []."""
    enriched = enrich(_moment_03(), _reel_03_distant())
    found = enriched.as_dict()["closer_repeats"]
    assert len(found) == 2, found
    assert {r["kind"] for r in found} == {"closer_echoes_body"}
    # The closer is placed whole, but the echo of each body play is NAMED.
    assert {round(e["body_start"], 2) for e in found} == {301.2, 350.0}
    assert {round(e["closer_start"], 2) for e in found} == {900.0}
    plain = ReelMoment(number=9, slug="no-closer", reason="ends on body",
                       timeline_start=BODY_START, timeline_end=BODY_END,
                       approval=Approval.APPROVED)
    assert enrich(plain, _reel_03_distant()).closer_repeats == ()


def test_nothing_new_is_refused():
    """No approved reel is newly refused: the distant pair still builds
    and still validates, because suspects and closer echoes REPORT."""
    from library.tools.reel_build import reel_ranges
    tx = _reel_03_distant()
    ranges = reel_ranges(_moment_03(), tx)
    assert ranges[0][0] == BODY_START
    assert ranges[-1] == (900.0, 905.2)
    validate_proposal([_moment_03()], tx, 1200.0)
