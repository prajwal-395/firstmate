"""Distant repeats are reported, never cut - and the CTA is scanned at all.

By rule, CUT_WINDOW_SECONDS still gates every cut; a distant same-speaker
pair becomes a suspect and a closer echoing the body is named. Whether a
reported repeat is a retake or a callback is left to the model.

History: `docs/evidence/reel_distant_repeats.md` (test_reel_distant_repeats.py).
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
