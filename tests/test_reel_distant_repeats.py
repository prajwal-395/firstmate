"""Distant repeats are reported, never cut - and the CTA is scanned at all.

The brief's measured case (report filed outside this tree, reconstructed
here from its description and the repo's own verbatim fixtures): reel 03
plays its tagline THREE times - twice in the body 40-150s apart with
another speaker between them, and again as the closing CTA from elsewhere
in the episode. Reel 07 says its line twice, twelve seconds apart, and
survives on a text bar rather than the window.

What today's code does with that shape (reproduced before the fix):

- `redundant_takes` over the body: [] - the pair is past CUT_WINDOW.
- `suspected_takes` over the body: [] - the loose scan breaks at the
  same window, so the report lane is blind past it too.
- the CTA range: never scanned - `reel_ranges` places it whole, so a
  closer echoing the body plays the line twice with nothing said.
- only `duplicate_takes` (word-stream, selection-time, report-only)
  sees the body pair.

The distinction this file pins, by rule versus by model:

- BY RULE, CUT: nothing new. Distance alone cannot tell a callback from
  a retake, so CUT_WINDOW_SECONDS still gates every cut. A near-identical
  same-speaker pair 45s apart is NOT cut here.
- BY RULE, REPORT: a distant pair that meets the CUT text bars with
  agreeing durations becomes a suspect (the markers lane), and a closer
  that echoes the body or repeats itself is named by `closer_repeats`.
  Both say why they were kept: distance / a deliberate choice.
- LEFT TO THE MODEL: whether a reported repeat is a retake to redraw
  past or a deliberate callback to keep.

`library/tools/reel_build.py` - `suspected_takes`, `closer_repeats`;
`library/tools/reel_proposal.py` - `enrich`.
"""

from __future__ import annotations

from library.tools.reel_build import (
    CUT_WINDOW_SECONDS,
    redundant_takes,
    suspected_takes,
)
from library.tools.reel_proposal import (
    Approval,
    CallToAction,
    ReelMoment,
    enrich,
    validate_proposal,
)

MXF = "/m/a.MXF"

TAGLINE = ("search didn't change, the question changed, whoever AI "
           "understands best gets the answer")


def _seg(speaker, text, start, end, uid="u"):
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": MXF,
            "source_start": start, "source_end": end,
            "resolve_item_id": uid}


def _reel_03_distant():
    """Reel-03 shape: the tagline twice in the body, 43.6s apart with
    Craig's turn between them, and a third time as the CTA."""
    return {"segments": [
        _seg("Akshita", "Yeah, " + TAGLINE, 301.2, 306.4, "a1"),
        _seg("Craig", "the questions change so that's why this is so "
                      "important", 313.7, 318.0, "c1"),
        _seg("Akshita", "Yeah, " + TAGLINE, 350.0, 355.2, "a2"),
        _seg("Craig", "to be the answer it's exactly why we've been "
                      "building this", 356.0, 360.0, "c2"),
        _seg("Akshita", TAGLINE, 900.0, 905.2, "cta1"),
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


def test_the_closer_echoing_the_body_is_named():
    """The CTA repeating the tagline is the reel's third play of it.
    The closer is placed whole - a deliberate choice is never silently
    shortened - but the echo is NAMED. FAILED before the fix: no
    function measured the CTA at all."""
    from library.tools.reel_build import closer_repeats
    found = closer_repeats(_moment_03(), _reel_03_distant())
    assert len(found) == 2, found
    assert {round(e["body_start"], 2) for e in found} == {301.2, 350.0}
    echo = next(e for e in found if round(e["body_start"], 2) == 301.2)
    assert echo["kind"] == "closer_echoes_body"
    assert round(echo["closer_start"], 2) == 900.0


def test_the_closer_echo_reaches_the_model_at_selection_time():
    """`enrich` carries the echo so the span can still be redrawn or a
    different closer picked. A moment with no CTA carries []."""
    enriched = enrich(_moment_03(), _reel_03_distant())
    assert len(enriched.closer_repeats) == 2
    assert {r["kind"] for r in enriched.as_dict()["closer_repeats"]} == \
        {"closer_echoes_body"}
    plain = ReelMoment(number=9, slug="no-closer", reason="ends on body",
                       timeline_start=BODY_START, timeline_end=BODY_END,
                       approval=Approval.APPROVED)
    assert enrich(plain, _reel_03_distant()).closer_repeats == ()


def test_reel_07s_twelve_second_pair_survives_on_the_text_bar():
    """Same speaker, twelve seconds apart - INSIDE the window, so its
    survival is one of the other bars: containment 1.000 but Jaccard
    0.500, under the cut bar. Neither the cut lane nor the new distant
    lane touches it."""
    tx = {"segments": [
        _seg("Akshita", "hiring managers check both places, that is the "
                        "whole point of a resume", 100.0, 105.0, "u1"),
        _seg("Akshita", "hiring managers check both", 117.0, 119.5, "u2"),
    ]}
    assert redundant_takes(90.0, 130.0, tx) == []
    distant = [c for c in suspected_takes(90.0, 130.0, tx)
               if c.kept_start - c.dropped_end > CUT_WINDOW_SECONDS]
    assert distant == []


def test_a_distant_fragment_restatement_is_neither_cut_nor_marked():
    """The duration guard's own case, at distance: the same sentence as
    a 4.3s line and a 0.5s fragment, 46s apart. Text bars pass, shape
    refuses - so no cut AND no suspect. A callback restated briefly
    must not even cost the captain a glance."""
    tx = {"segments": [
        _seg("Akshita", "it is going to start hallucinating because it is "
                        "confused about what you actually do",
             10.0, 14.3, "a"),
        _seg("Craig", "right, and then what happened", 20.0, 24.0, "c"),
        _seg("Akshita", "confused about what you actually do because it "
                        "is hallucinating", 60.0, 60.5, "b"),
    ]}
    assert redundant_takes(0.0, 70.0, tx) == []
    assert suspected_takes(0.0, 70.0, tx) == []


def test_a_distant_cross_speaker_echo_is_neither_cut_nor_marked():
    """Craig's question echoed in Akshita's answer a minute later is an
    exchange, not a take - the same-speaker guard holds at distance."""
    tx = {"segments": [
        _seg("Craig", "hiring managers would check both places", 10.0, 14.0,
             "u1"),
        _seg("Craig", "right, and then what happened", 30.0, 34.0, "c"),
        _seg("Akshita", "hiring managers would check both places", 74.0, 78.0,
             "u2"),
    ]}
    assert redundant_takes(0.0, 90.0, tx) == []
    assert suspected_takes(0.0, 90.0, tx) == []


def test_nothing_new_is_refused():
    """No approved reel is newly refused: the distant pair still builds
    and still validates, because suspects and closer echoes REPORT."""
    from library.tools.reel_build import reel_ranges
    tx = _reel_03_distant()
    ranges = reel_ranges(_moment_03(), tx)
    assert ranges[0][0] == BODY_START
    assert ranges[-1] == (900.0, 905.2)
    validate_proposal([_moment_03()], tx, 1200.0)
