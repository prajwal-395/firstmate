"""The judge above the take-cut heuristic.

`reel_build.redundant_takes` is a candidate generator: it counts shared
words at CUT_CONTAINMENT/CUT_JACCARD over the transcript and calls the
winners retakes. Three of the captain's recorded corrections come from
treating those winners as decisions (lc-0004, lc-0005, lc-0006), and the
before-inventory for this change found a fourth live defect in the same
mechanism: on Reel 15 a word-stream window straddling two SIMULTANEOUS
speakers' segments runs backwards in time (first word 1203.06, last word
1200.63), becomes a Cut with a NEGATIVE range, and `keep_ranges` lays the
reel's ranges over each other - the seconds 1200.63-1203.06 play TWICE.

`judge_take_cuts` is the deterministic judge above those candidates. It
withdraws only cuts that are structurally indefensible as "one telling
removed, a later one kept", and reports each with its reason. What it
deliberately does NOT do is re-judge close pairs: Reel 15's salon
retakes (containment 1.000/0.875), Reel 28's false start and Reel 31's
"Yeah." all survive the judge unchanged, because a word-overlap
threshold cannot tell a reworded retake from a refrain tail and the
captain approved those reels.

Synthetic under `tmp_path`-style fixtures (AGENTS.md 8); the field-test
numbers appear as stated inputs, never read from a real project.
"""

from types import SimpleNamespace

from library.tools import reel_build


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


# ── J1: a cut must remove a positive range, first telling before second ──

def test_an_inverted_cut_is_withdrawn():
    """Reel 15's live defect, stated input: the word-stream window ran
    backwards across two simultaneous speakers, so the 'first' telling
    starts AFTER it ends."""
    transcript = {"segments": [
        _seg("Craig", "yeah so ai is actually better", 1200.588,
             [1200.70, 1200.90, 1201.10, 1201.30, 1201.50, 1201.70],
             uid="c"),
        _seg("Akshita", "yeah so ai is actually better", 1200.633,
             [1200.75, 1200.95, 1201.15, 1201.35, 1201.45, 1201.55],
             uid="a"),
    ]}
    cut = _cut((1203.06, 1200.63), (1201.59, 1203.39))
    kept, withdrawn = reel_build.judge_take_cuts([cut], 1186.94, 1251.21,
                                                 transcript)
    assert kept == []
    assert [w["reason"] for w in withdrawn] == ["inverted_or_empty"]


def test_an_empty_cut_is_withdrawn():
    kept, withdrawn = reel_build.judge_take_cuts(
        [_cut((10.0, 10.0), (12.0, 13.0))], 0.0, 20.0, {"segments": []})
    assert kept == []
    assert [w["reason"] for w in withdrawn] == ["inverted_or_empty"]


def test_a_cut_that_keeps_before_it_drops_is_withdrawn():
    """Causality: a retake's first telling must END before the second
    BEGINS. A kept span starting before the dropped span ends is
    simultaneous speech or bleed, not first-then-retake."""
    transcript = {"segments": [
        _seg("Craig", "yeah so ai is actually better", 10.0,
             [10.2, 10.4, 10.6, 10.8, 10.9, 11.0], uid="c"),
    ]}
    kept, withdrawn = reel_build.judge_take_cuts(
        [_cut((10.0, 12.0), (11.0, 13.0))], 0.0, 20.0, transcript)
    assert kept == []
    assert [w["reason"] for w in withdrawn] == ["spans_overlap"]


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


def test_a_word_stream_cut_covering_whole_segments_survives():
    """Reel 03's shape: finely segmented takes where the dropped span
    covers whole segments rather than cutting inside one. Withdrawing
    interior excisions must not take whole-telling removals with it."""
    transcript = {"segments": [
        _seg("Akshita", "search did not change", 301.24,
             [301.40, 301.70, 302.00, 302.57], uid="a1"),
        _seg("Akshita", "the question changed", 302.63,
             [302.80, 303.10, 303.45], uid="a2"),
        _seg("Akshita", "search did not change the question changed", 310.0,
             [310.20, 310.40, 310.60, 310.80, 311.00, 311.30, 311.60],
             uid="a3"),
    ]}
    cut = reel_build.Cut(
        dropped_start=301.24, dropped_end=303.45,
        dropped_text="search did not change the question changed",
        kept_start=310.0, kept_end=311.60,
        kept_text="search did not change the question changed",
        speaker="Akshita", containment=1.0, jaccard=1.0)
    kept, withdrawn = reel_build.judge_take_cuts([cut], 300.0, 315.0,
                                                 transcript)
    assert withdrawn == []
    assert kept == [cut]


def test_a_cross_speaker_word_stream_cut_is_withdrawn():
    """Mic bleed / simultaneous speech: the dropped span's own words are
    spoken by more than one speaker, so it is not one telling."""
    transcript = {"segments": [
        _seg("Craig", "yeah so ai is actually better", 1200.588,
             [1200.70, 1200.90, 1201.10, 1201.30, 1201.50, 1201.70],
             uid="c"),
        _seg("Akshita", "yeah so ai is actually better", 1200.633,
             [1200.75, 1200.95, 1201.15, 1201.35, 1201.45, 1201.55],
             uid="a"),
    ]}
    cut = _cut((1200.60, 1201.40), (1202.00, 1203.00))
    kept, withdrawn = reel_build.judge_take_cuts(
        [cut], 1200.0, 1205.0, transcript)
    assert kept == []
    assert [w["reason"] for w in withdrawn] == ["cross_speaker"]


# ── J5: the seam is decided with the cut ──

def test_a_cut_edge_inside_a_word_is_withdrawn():
    """A dropped edge landing strictly inside a timed word plays half a
    word and then jumps. The build refuses that loudly; the judge says
    it first, with the cut attached."""
    transcript = {"segments": [
        _seg("Akshita", "everything else on the broader web", 2428.626,
             [2428.95, 2429.17, 2429.33, 2429.50, 2429.76, 2429.86],
             uid="a1"),
        _seg("Akshita", "for example", 2429.90,
             [2430.00, 2430.48], uid="a2"),
    ]}
    cut = _cut((2428.626, 2429.60), (2455.581, 2456.959),
               speaker="Akshita", cont=0.75, jac=0.60)
    kept, withdrawn = reel_build.judge_take_cuts(
        [cut], 2420.0, 2460.0, transcript)
    assert kept == []
    assert [w["reason"] for w in withdrawn] == ["mid_word_edge"]


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


# ── Through the build's own ranges ──

def _moment(start=9.0, end=16.0):
    return SimpleNamespace(timeline_start=start, timeline_end=end,
                           number=1, slug="x", timeline_name="Reel 01 - x",
                           call_to_action=None)


def test_the_build_no_longer_cuts_lc0004_without_any_captain_record():
    """The regression proof at the seam that matters: `reel_ranges`
    over lc-0004's span plays the sentence whole with NO insistence on
    file. Before the judge this returned two ranges."""
    ranges = reel_build.reel_ranges(_moment(), _lc0004_transcript())
    assert ranges == [(9.0, 16.0)]


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
