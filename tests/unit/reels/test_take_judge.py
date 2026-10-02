"""`judge_take_cuts` withdraws only take cuts that are structurally
indefensible as "one telling removed, a later one kept", each with its
reason, and leaves working cuts alone.

History: docs/evidence/reel_take_cuts.md.
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
