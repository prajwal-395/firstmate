"""Keep-range edges the cutter draws must not land inside a word.

From `vep-approved-reels-still-have-visible-defects/report.md`: placed
cards match the plan within 2 frames on all 306 cards, so the defects
the captain watched were authored in the PLAN - and the interior keep
edges are drawn by the cutter (`redundant_takes` + `keep_ranges`), which
the plan gate never checks. `validate_proposal` refuses an OUTER
boundary cutting a segment (`partial_overlaps`), but nothing asks what
an INTERIOR edge cuts through:

- R07: the cut lands 0.02s into "Your" - the viewer hears "Your we-".
- R02 head: a 3-frame "their keywords" sliver, 125ms, less than the
  words themselves - the edge sits inside the words.
- R02/R06/R10 blips: 5-13f slivers playing the attack/onset of a word
  ("that's", "i kind", "Yeah") between cards.

A mid-word edge is measurable against word times TODAY
(`_word_intervals`, the same stream `_snap_out_of_words` reads), so it
is a MECHANICAL refusal, not taste (AGENTS.md 10.5): the check asks the
same strict-interior question the snap asks, of the edges the snap never
touches.

What this deliberately does NOT refuse: a keep edge on a word boundary
mid-sentence (R04's three pieces, R10's 25s jump, R08/R11 cold opens).
"Does not parse as a thought" is a judgement and belongs to the MODEL,
not to a rule in the engine - those stay reported
(`refused_take_groups`, `opening_observations`), never refused here.
"""

from __future__ import annotations

import pytest

from library.tools.reel_build import ReelBuildError, reel_ranges
from library.tools.reel_proposal import (
    Approval,
    ProposalError,
    ReelMoment,
    duplicate_takes,
    validate_proposal,
)


def _seg(speaker, text, start, end, uid="u", words=None):
    seg = {"speaker": speaker, "text": text, "timeline_start": start,
           "timeline_end": end, "source_file": "/m/a.MXF",
           "source_start": start, "source_end": end,
           "resolve_item_id": uid}
    if words is not None:
        seg["words"] = words
    return seg


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


def _tx(*segments):
    return {"segments": list(segments)}


def _r07_transcript():
    """R07's shape, report numbers kept: the dropped take starts at
    104.42s, which is 0.02s inside "Your" (104.40-104.90s)."""
    return _tx(
        _seg("Akshita", "Absolutely. Your website", 100.0, 104.42, "u1",
             words=[_w("Absolutely.", 100.0, 100.70),
                    _w("Your", 104.40, 104.90),
                    _w("website", 104.95, 105.60)]),
        _seg("Akshita", "is your resume, hiring managers check both",
             104.42, 109.0, "u2",
             words=[_w("is", 104.95, 105.10),
                    _w("both", 108.60, 109.00)]),
        _seg("Akshita", "is your resume, hiring managers check both places",
             112.0, 117.0, "u3",
             words=[_w("is", 112.10, 112.25),
                    _w("places", 116.60, 117.00)]),
        _seg("Akshita", "and that is the whole point", 118.0, 121.0, "u4",
             words=[_w("and", 118.0, 118.2),
                    _w("point", 120.6, 121.0)]),
    )


def _moment(start=100.0, end=121.0):
    return ReelMoment(number=7, slug="website-is-resume",
                      reason="a complete exchange",
                      timeline_start=start, timeline_end=end,
                      approval=Approval.APPROVED)


def test_a_keep_edge_inside_a_word_is_found():
    from library.tools.reel_build import midword_keep_edges
    found = midword_keep_edges(100.0, 121.0, _r07_transcript())
    assert len(found) == 1
    assert found[0]["edge"] == 104.42
    assert found[0]["word"] == "Your"


def test_the_build_refuses_a_midword_keep_edge():
    with pytest.raises(ReelBuildError):
        reel_ranges(_moment(), _r07_transcript())


def test_the_plan_refuses_a_midword_keep_edge():
    with pytest.raises(ProposalError, match="cut in half"):
        validate_proposal([_moment()], _r07_transcript(), 600.0)


def test_a_keep_edge_on_word_edges_passes():
    """The same takes, but the segment edge lands exactly where "Your"
    starts: the viewer hears whole words, so the plan stands."""
    tx = _tx(
        _seg("Akshita", "Absolutely.", 100.0, 101.0, "u1",
             words=[_w("Absolutely.", 100.0, 100.70)]),
        _seg("Akshita", "is your resume, hiring managers check both",
             104.42, 109.0, "u2",
             words=[_w("is", 104.50, 104.65),
                    _w("both", 108.60, 109.00)]),
        _seg("Akshita", "is your resume, hiring managers check both places",
             112.0, 117.0, "u3",
             words=[_w("places", 116.60, 117.00)]),
        _seg("Akshita", "and that is the whole point", 118.0, 121.0, "u4",
             words=[_w("point", 120.6, 121.0)]),
    )
    from library.tools.reel_build import midword_keep_edges
    assert midword_keep_edges(100.0, 121.0, tx) == []
    assert reel_ranges(_moment(), tx) == [(100.0, 104.42), (109.0, 121.0)]


def test_faithful_disfluency_is_not_a_take_and_not_a_midword_edge():
    """R12's "different different" is the speaker stuttering, not the
    editor keeping two takes: one adjacent repeat inside ONE segment is
    below every detector window (4-word `repeat`, 3-word `possible`),
    and with no cut there is no interior edge to refuse. A rule that
    could see this would mangle honest speech; the line is structural -
    cross-segment content repetition is editorial, intra-segment
    adjacency is faithful - so this plan must pass untouched."""
    tx = _tx(
        _seg("Akshita", "it was a different different problem", 10.0, 16.0,
             "u1", words=[_w("different", 12.0, 12.35),
                          _w("different", 12.35, 12.70),
                          _w("problem", 13.0, 13.5)]),
        _seg("Craig", "right, and then what happened", 17.0, 22.0, "u2",
             words=[_w("right,", 17.0, 17.3)]),
    )
    assert duplicate_takes(10.0, 22.0, tx) == []
    from library.tools.reel_build import midword_keep_edges, redundant_takes
    assert redundant_takes(10.0, 22.0, tx) == []
    assert midword_keep_edges(10.0, 22.0, tx) == []


def test_a_same_text_restatement_by_another_speaker_is_not_cut():
    """The R07 class of escape the window cannot explain: "check both"
    restated 12s apart is INSIDE `CUT_WINDOW_SECONDS`, so its survival
    is one of the other bars - here the same-speaker guard, which exists
    because mic bleed puts both sides of an exchange on one track and
    cutting across speakers deletes questions with their answers. Each
    bar that lets a repeat through is there because loosening it removed
    real content; that is a tuning cost, not a missing class."""
    tx = _tx(
        _seg("Craig", "hiring managers would check both places", 10.0, 14.0,
             "u1"),
        _seg("Akshita", "hiring managers would definitely check both",
             22.0, 26.0, "u2"),
    )
    from library.tools.reel_build import redundant_takes
    assert redundant_takes(0.0, 60.0, tx) == []


def test_float_dust_on_a_word_edge_is_not_a_midword_cut():
    """Reel 14 (2026-09-20): a take-cut edge at 1158.3199999999997s
    against the word start 1158.32s - 2.8e-13s of JSON float dust on
    the same instant - refused the quality bar's playable_ranges and
    emptied the reel text, failing a correct reel. Edges canonicalise
    to 6dp keys; words now canonicalise the same way, so dust is not
    a cut word while a genuine interior edge still fires."""
    from library.tools.reel_build import Cut, midword_keep_edges
    tx = _tx(
        _seg("Akshita", "setup words here", 1138.0, 1140.0, "u1",
             words=[_w("here", 1139.0, 1139.50)]),
        _seg("Akshita", "If you run ads", 1158.32, 1160.0, "u2",
             words=[_w("If", 1158.3199999999997, 1158.48),
                    _w("you", 1158.48, 1158.60)]),
        _seg("Akshita", "take two kept", 1168.0, 1170.0, "u3",
             words=[_w("kept", 1169.0, 1169.50)]),
    )
    cuts = [Cut(
        dropped_start=1158.3199999999997, dropped_end=1164.0,
        dropped_text="If you run ads", kept_start=1168.0,
        kept_end=1170.0, kept_text="take two kept", speaker="Akshita",
        containment=1.0, jaccard=1.0, basis="test dust")]
    assert midword_keep_edges(1138.0, 1170.0, tx, cuts) == []


def test_a_genuine_interior_edge_still_fires():
    """The dust canonicalisation must not swallow a real cut: an edge
    a full frame inside the word still refuses."""
    from library.tools.reel_build import Cut, midword_keep_edges
    tx = _tx(
        _seg("Akshita", "setup words here", 1138.0, 1140.0, "u1",
             words=[_w("here", 1139.0, 1139.50)]),
        _seg("Akshita", "If you run ads", 1158.32, 1160.0, "u2",
             words=[_w("If", 1158.32, 1158.48),
                    _w("you", 1158.48, 1158.60)]),
        _seg("Akshita", "take two kept", 1168.0, 1170.0, "u3",
             words=[_w("kept", 1169.0, 1169.50)]),
    )
    cuts = [__import__("library.tools.reel_build", fromlist=["x"]).Cut(
        dropped_start=1158.36, dropped_end=1164.0,
        dropped_text="If you run ads", kept_start=1168.0,
        kept_end=1170.0, kept_text="take two kept", speaker="Akshita",
        containment=1.0, jaccard=1.0, basis="test interior")]
    found = midword_keep_edges(1138.0, 1170.0, tx, cuts)
    assert len(found) == 1
    assert found[0]["word"] == "If"
