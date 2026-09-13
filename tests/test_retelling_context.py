"""What the model needs to judge a repetition, measured beside the spans.

The brief's three questions for every repeat - the surrounding
sentences, whether it crosses a speaker turn, whether the second
telling adds anything - reach no model today: `repetition_inside`
carries the two spans and `build_removes_it`, and paraphrase-class
repeats (lc-0006) are not carried at all, because no pair-scan run
contains them.

`take_cut_context` renders those three answers per cut, and
`possible_retellings` surfaces the cross-turn paraphrase suspects the
cut lane refuses by design, so the selecting model can redraw past one
telling while the boundary is still open. Both REPORT; neither cuts.

Inputs are the captain's own cases in synthetic fixtures: lc-0005's
closing-phrase tail and lc-0006's paraphrase across an interjection.
"""

from library.steps.step_3_04_select_reels import bridge
from library.tools import reel_build


def _seg(speaker, text, start, ends, uid="u"):
    parts = text.split(" ")
    return {"speaker": speaker, "text": text,
            "timeline_start": start, "timeline_end": ends[-1],
            "resolve_item_id": uid,
            "words": [{"word": word,
                       "start": start if i == 0 else ends[i - 1],
                       "end": ends[i], "timed": True}
                      for i, word in enumerate(parts)]}


def _lc0005_transcript():
    """lc-0005's shape: two DIFFERENT sentences about different things
    (profile links / reviews reflected) sharing only a closing phrase,
    26 seconds apart."""
    return {"segments": [
        _seg("Akshita",
             "and make sure everything on your google business profile "
             "links up with",
             2424.461,
             [2425.41, 2425.67, 2425.87, 2426.41, 2426.53, 2426.65,
              2426.93, 2427.32, 2427.64, 2427.76, 2427.82, 2428.02],
             uid="a1"),
        _seg("Akshita", "everything else on the broader web.", 2428.626,
             [2428.95, 2429.17, 2429.33, 2429.50, 2429.76, 2429.86],
             uid="a2"),
        _seg("Akshita", "for example", 2429.86,
             [2430.00, 2430.48], uid="a3"),
        _seg("Akshita",
             "make sure that your google reviews are all positive and "
             "that's being reflected",
             2450.961,
             [2451.16, 2451.54, 2451.95, 2452.13, 2452.47, 2453.03,
              2453.47, 2453.65, 2454.39, 2454.56, 2454.82, 2455.02,
              2455.46],
             uid="a4"),
        _seg("Akshita", "everywhere else on the broader web.", 2455.581,
             [2455.89, 2456.07, 2456.19, 2456.29, 2456.64, 2456.96],
             uid="a5"),
    ]}


def _lc0006_transcript():
    """lc-0006's shape: Craig says the same thing twice across
    Akshita's interjection, reworded past every bar the cut lane holds
    (containment 0.667/1.0, Jaccard 0.286/0.50, ratio 2.03)."""
    return {"segments": [
        _seg("Craig", "and that's what happened with that company why",
             889.92,
             [890.02, 890.20, 890.36, 890.54, 890.66, 890.84, 891.10,
              891.26],
             uid="c1"),
        _seg("Craig", "they came back as a healthcare company", 891.40,
             [891.48, 891.66, 891.88, 892.04, 892.10, 892.64, 893.02],
             uid="c2"),
        _seg("Akshita", "and not an accounting software company yes",
             893.455,
             [893.51, 893.70, 893.80, 894.16, 894.56, 894.94, 895.12],
             uid="a1"),
        _seg("Craig",
             "and that's one of the reasons why that company got "
             "called a",
             895.471,
             [895.55, 895.79, 895.93, 895.99, 896.09, 896.45, 896.77,
              897.20, 897.66, 897.86, 898.16, 898.20],
             uid="c3"),
        _seg("Craig", "healthcare company", 898.60,
             [899.00, 899.40], uid="c4"),
    ]}


# ── lc-0005: the cut context names the tail-drop ──

def test_the_tail_drop_reads_as_a_tail():
    """The decision the model never got: the dropped span is the TAIL
    of its sentence, so removing it orphans 'links up with'. The
    context says the sentence, the position, and that the kept telling
    swaps one content word (everything -> everywhere)."""
    transcript = _lc0005_transcript()
    cuts = reel_build.redundant_takes(2424.0, 2457.0, transcript)
    assert len(cuts) == 1, (
        "the generator stopped proposing lc-0005's cut - the floor moved")
    context = reel_build.take_cut_context(cuts[0], transcript)
    assert context["dropped_position"] == "tail"
    assert "links up with" in context["dropped_sentence"]
    assert context["crosses_turn"] is False
    assert "everywhere" in context["novel_words"]
    assert context["gap_seconds"] > 20


def test_a_false_start_reads_as_an_onset():
    """Reel 28's shape: the dropped false start OPENS its sentence, so
    the same context that condemns the tail-drop clears this one."""
    transcript = {"segments": [
        _seg("Akshita", "that has five star reviews and yada.", 2241.15,
             [2241.27, 2241.41, 2241.57, 2242.02, 2242.52, 2242.70,
              2243.85],
             uid="a0"),
        _seg("Akshita", "and i got", 2244.995,
             [2245.16, 2245.20, 2246.01], uid="a1"),
        _seg("Akshita", "and that is a very specific query", 2246.07,
             [2246.15, 2246.25, 2246.31, 2246.36, 2246.56, 2247.01,
              2247.25],
             uid="a2"),
        _seg("Akshita", "i got some", 2251.833,
             [2251.91, 2252.16, 2252.37], uid="a3"),
    ]}
    cuts = reel_build.redundant_takes(2241.0, 2253.0, transcript)
    assert len(cuts) == 1
    context = reel_build.take_cut_context(cuts[0], transcript)
    assert context["dropped_position"] == "onset"


# ── lc-0006: the paraphrase is surfaced with its turn-crossing ──

def test_the_cutter_still_misses_the_paraphrase():
    """The deterministic floor does not move: the paraphrase scores
    below the cut bars, so no cut is proposed."""
    transcript = _lc0006_transcript()
    assert reel_build.redundant_takes(889.0, 900.0, transcript) == []


def test_the_paraphrase_is_surfaced_for_the_model():
    """But it is CAUGHT: `possible_retellings` names the pair, that it
    crosses Akshita's turn, both tellings' sentences, and what the
    second telling adds."""
    found = reel_build.possible_retellings(889.0, 900.0,
                                           _lc0006_transcript())
    assert len(found) == 1
    retelling = found[0]
    assert retelling["speaker"] == "Craig"
    assert retelling["crosses_turn"] is True
    assert "accounting software" in retelling["between_text"]
    assert "healthcare company" in retelling["kept_sentence"]
    assert retelling["dropped_start"] < 893.455 < retelling["kept_start"]


def test_no_retelling_where_the_turn_does_not_cross():
    """The same near-miss WITHOUT an interjection is an ordinary
    suspect, not a retelling: nothing is surfaced."""
    transcript = {"segments": [
        _seg("Craig", "and that's what happened with that company why",
             889.92,
             [890.02, 890.20, 890.36, 890.54, 890.66, 890.84, 891.10,
              891.26],
             uid="c1"),
        _seg("Craig",
             "and that's one of the reasons why that company got "
             "called a",
             895.471,
             [895.55, 895.79, 895.93, 895.99, 896.09, 896.45, 896.77,
              897.20, 897.66, 897.86, 898.16, 898.20],
             uid="c3"),
    ]}
    assert reel_build.possible_retellings(889.0, 900.0, transcript) == []


# ── The bridge carries both where the boundary is still open ──

def test_repetition_inside_carries_cut_context():
    """Each reported run's cuts arrive with their sentence position,
    so the model can tell a tail-drop from a false start."""
    entries = bridge.repetition_inside(2424.0, 2457.0,
                                       _lc0005_transcript())
    assert len(entries) == 1
    cuts = entries[0].get("cuts")
    assert cuts, "run entries must carry their cuts' context"
    assert cuts[0]["dropped_position"] == "tail"


def test_candidates_carry_possible_retellings():
    """The lc-0006 span, as a candidate window, offers the retelling
    where the model can still redraw past one telling."""
    data = {"timeline_transcript": _lc0006_transcript()}
    import copy
    data["timeline_transcript"] = copy.deepcopy(_lc0006_transcript())
    entries = bridge.repetition_inside(889.0, 900.0,
                                       data["timeline_transcript"])
    # No pair-scan run exists here (nothing paired), so the run list is
    # empty - and the retelling must still be offered, not lost with it.
    assert entries == []
    from library.tools import reel_build as _rb
    assert len(_rb.possible_retellings(
        889.0, 900.0, data["timeline_transcript"])) == 1
