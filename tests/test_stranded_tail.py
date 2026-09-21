"""A kept range that stops before its thought finishes.

Two field-test defects, one predicate family. Both are the same cause
at two sizes - a kept range stops before the thought it carries has
finished - and the existing gates cannot see either, for opposite
reasons:

- Reel 09 (one word): the keep edge lands 0.02s BEFORE the final
  word's start, so `midword_keep_edges` (which refuses an edge
  INSIDE a word) passes it clean and the last word is simply gone.
  The live timeline narrowed the Akshita keep bound 36510 down to
  36490; the staging records carry 36510 and that is the correct
  bound.
- Reel 04 (one sentence): the body ends on "why." while the sentence
  that states its point ("...is a decision engine.") starts five
  frames later. The boundary-repair machinery recorded that it knew
  the sentence continued and stopped, because no pin kind extends a
  body.

The fix under test: `stranded_tail_keep_edges` reports interior
edges that strand whole kept words (refused at build, like midword),
and `repair_moment_tail` extends or holds the stored moment end the
snap owns - extending to the sentence end where whole words stand
unplayed inside the speaker's pace, holding where the approved bound
already covers all but breath of the final word. Every threshold is
derived from the kept range's own segment word timings (median word
duration); the transcript-wide fallback says so loudly and never
extends.

Field-test numbers below are copied verbatim from
`pipeline_output/scratch/timeline_transcript/transcript.json` and the
approved reel spans. No test here reads that project (AGENTS.md 8):
the measurement travels as data so the case runs anywhere.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import reel_build
from library.tools.reel_build import (
    ReelBuildError,
    record_tail_repairs,
    reel_ranges,
    repair_moment_tail,
    stranded_tail_keep_edges,
)
from library.tools.reel_proposal import (
    Approval,
    ReelMoment,
    decision_lines,
    snap_moment_to_speech,
)

FPS = 24000 / 1001  # 23.976 exact - the reels' own frame rate


def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "timed": True}


def _seg(speaker, text, start, end, uid="u", words=()):
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": "/m/a.MXF",
            "source_start": start, "source_end": end,
            "resolve_item_id": uid, "words": list(words)}


def _tx(*segments):
    return {"segments": list(segments)}


def _moment(number, start, end):
    return ReelMoment(number=number, slug=f"reel-{number:02d}",
                      reason="a complete exchange",
                      timeline_start=start, timeline_end=end,
                      approval=Approval.APPROVED)


# ------------------------------------------------- Reel 09, verbatim
#
# Akshita's closing segment, 682.19-693.38s, and Craig's following
# turn opening at 694.1s. The approved body end is 693.3s - inside
# the final word "misrecommended." (692.50-693.38s) with 0.08s of it
# uncovered, and 0.8s before Craig's next speech.


def _reel9_transcript():
    return _tx(
        _seg("Akshita",
             "And so when AI reads all of these sources, that's how it's "
             "building a big picture of your company and your brand and "
             "you have to be aware of what it's saying or you can be "
             "completely re like misrecommended.",
             682.19, 693.38, "u-akshita",
             words=[
                 _w("And", 682.19, 682.71),
                 _w("so", 682.71, 683.08),
                 _w("when", 683.14, 683.28),
                 _w("AI", 683.28, 683.46),
                 _w("reads", 683.46, 683.74),
                 _w("all", 683.74, 683.93),
                 _w("of", 683.93, 684.04),
                 _w("these", 684.04, 684.21),
                 _w("sources,", 684.21, 684.78),
                 _w("that's", 685.04, 685.29),
                 _w("how", 685.29, 685.43),
                 _w("it's", 685.43, 685.54),
                 _w("building", 685.54, 685.87),
                 _w("a", 685.87, 685.92),
                 _w("big", 685.92, 686.19),
                 _w("picture", 686.19, 686.71),
                 _w("of", 687.15, 687.3),
                 _w("your", 687.3, 687.42),
                 _w("company", 687.42, 687.85),
                 _w("and", 687.85, 688.02),
                 _w("your", 688.02, 688.16),
                 _w("brand", 688.16, 688.57),
                 _w("and", 688.63, 689.04),
                 _w("you", 689.04, 689.28),
                 _w("have", 689.38, 689.63),
                 _w("to", 689.63, 689.72),
                 _w("be", 689.72, 689.87),
                 _w("aware", 689.87, 690.21),
                 _w("of", 690.21, 690.34),
                 _w("what", 690.34, 690.45),
                 _w("it's", 690.45, 690.61),
                 _w("saying", 690.61, 690.98),
                 _w("or", 691.04, 691.11),
                 _w("you", 691.11, 691.21),
                 _w("can", 691.21, 691.36),
                 _w("be", 691.36, 691.46),
                 _w("completely", 691.46, 692.0),
                 _w("re", 692.0, 692.18),
                 _w("like", 692.26, 692.5),
                 _w("misrecommended.", 692.5, 693.38),
             ]),
        _seg("Craig", "Well, I kind of tell people when I talk.",
             694.1, 698.0, "u-craig",
             words=[
                 _w("Well,", 694.1, 694.29),
                 _w("I", 694.29, 694.38),
                 _w("kind", 694.38, 694.59),
                 _w("of", 694.59, 694.67),
                 _w("tell", 694.67, 694.87),
                 _w("people", 694.87, 695.09),
             ]),
    )


def test_an_approved_bound_covering_all_but_breath_holds():
    """Reel 09's shape: the approved end sits 0.08s inside the final
    word - inside the speaker's 0.18s pace - with the next speech
    0.8s away. The repair holds it: drift since approval does not
    move approved spans."""
    new_end, finding = repair_moment_tail(
        693.3, 631.115, _reel9_transcript())
    assert new_end == 693.3
    assert finding["verdict"] == "hold"
    assert finding["word"] == "misrecommended."
    assert finding["remainder"] == pytest.approx(0.08)
    assert finding["pace"] == pytest.approx(0.18)


def test_an_edge_before_the_final_word_extends_to_it():
    """The defect's shape: an end 0.02s before "misrecommended."
    starts strands the whole word, so the repair extends to the
    sentence end - which is the word's end here."""
    new_end, finding = repair_moment_tail(
        692.48, 682.0, _reel9_transcript())
    assert new_end == 693.38
    assert finding["verdict"] == "extend"
    assert finding["kind"] == "word-tail"
    assert finding["tail_words"] == ["misrecommended."]


def test_the_snap_holds_the_approved_bound_and_snaps_the_head():
    """End to end of the hold: the stored body end stays 693.3s -
    the only body_end move is the tail-hold, never a snap rewrite."""
    repaired, moves = snap_moment_to_speech(
        _moment(9, 631.115, 693.3), _reel9_transcript())
    assert repaired.timeline_end == 693.3
    body_ends = [m for m in moves if m["boundary"] == "body_end"]
    assert len(body_ends) == 1
    assert body_ends[0]["attribution"] == "tail-hold"
    assert body_ends[0]["now"] == 693.3


def test_the_held_bound_places_36510():
    """The acceptance, in frames: the held body end maps to source
    frame 36510 on the transcript's own clip alignment - not the
    live defect's 36490, and one frame under the snapped word end."""
    repaired, _ = snap_moment_to_speech(
        _moment(9, 631.115, 693.3), _reel9_transcript())
    ranges = reel_ranges(repaired, _reel9_transcript())
    assert ranges == [(repaired.timeline_start, 693.3)]
    clip = SimpleNamespace(
        timeline_start=682.19, timeline_end=693.38,
        source_in=1511.64, source_file="LC4932.MXF",
        track_index=0, speaker="Akshita", track_type="video")
    placed = reel_build.placements(ranges, [clip], FPS)
    assert round(placed[-1]["source_out"] * FPS) == 36510


# ------------------------------------------------- Reel 04, verbatim
#
# The body ends at 285.42s, exactly on "why." - and the sentence that
# states the reel's point starts 0.20s later, inside the 0.29s pace.


def _reel4_transcript():
    return _tx(
        _seg("Akshita",
             "For Google search, it gave out a list from 2023, and for "
             "ChatGPT, it gave three recommendations with specific "
             "reasons why.",
             278.12, 285.42, "u-a",
             words=[
                 _w("For", 278.12, 278.41),
                 _w("Google", 278.41, 278.68),
                 _w("search,", 278.68, 279.16),
                 _w("it", 279.21, 279.29),
                 _w("gave", 279.29, 279.56),
                 _w("out", 279.56, 279.82),
                 _w("a", 279.82, 279.88),
                 _w("list", 279.88, 280.15),
                 _w("from", 280.15, 280.27),
                 _w("2023,", 280.27, 281.14),
                 _w("and", 281.14, 281.33),
                 _w("for", 281.33, 281.44),
                 _w("ChatGPT,", 281.44, 282.31),
                 _w("it", 282.48, 282.62),
                 _w("gave", 282.62, 282.99),
                 _w("three", 282.99, 283.13),
                 _w("recommendations", 283.13, 284.02),
                 _w("with", 284.02, 284.12),
                 _w("specific", 284.12, 284.55),
                 _w("reasons", 284.55, 285.03),
                 _w("why.", 285.03, 285.42),
             ]),
        _seg("Akshita",
             "So one, which is Google, is a search engine, and the "
             "other, ChatGPT, is a decision engine.",
             285.62, 290.23, "u-b",
             words=[
                 _w("So", 285.62, 285.96),
                 _w("one,", 285.96, 286.49),
                 _w("which", 286.55, 286.73),
                 _w("is", 286.73, 286.83),
                 _w("Google,", 286.83, 287.18),
                 _w("is", 287.18, 287.33),
                 _w("a", 287.33, 287.39),
                 _w("search", 287.39, 287.72),
                 _w("engine,", 287.72, 288.07),
                 _w("and", 288.07, 288.28),
                 _w("the", 288.28, 288.38),
                 _w("other,", 288.38, 288.63),
                 _w("ChatGPT,", 288.63, 289.29),
                 _w("is", 289.29, 289.37),
                 _w("a", 289.37, 289.42),
                 _w("decision", 289.42, 289.85),
                 _w("engine.", 289.85, 290.23),
             ]),
    )


def test_a_body_that_ends_before_its_point_extends_to_the_sentence():
    """Reel 04's shape: the 0.20s gap to "So" sits inside the 0.29s
    pace, so the body extends to the sentence end at 290.23s -
    carrying the "decision engine" punchline. Fixable, not demoted."""
    new_end, finding = repair_moment_tail(
        285.42, 267.358, _reel4_transcript())
    assert new_end == 290.23
    assert finding["verdict"] == "extend"
    assert finding["kind"] == "sentence-tail"
    assert finding["tail_words"][-2:] == ["decision", "engine."]
    assert finding["gap"] == pytest.approx(0.20)
    assert finding["pace"] == pytest.approx(0.27)


def test_the_extended_body_builds_one_clean_range():
    """End to end of the extend: the repaired moment lays down a
    single keep range through the punchline, with no take cut drawn
    inside it and no gate left to fail."""
    repaired, moves = snap_moment_to_speech(
        _moment(4, 267.358, 285.42), _reel4_transcript())
    assert repaired.timeline_end == 290.23
    assert moves[0]["attribution"] == "tail-extend"
    assert reel_ranges(repaired, _reel4_transcript()) == [
        (267.358, 290.23)]
    assert reel_build.midword_keep_edges(
        repaired.timeline_start, repaired.timeline_end,
        _reel4_transcript()) == []


def test_a_tail_extend_is_loud_and_pointed_at_its_record():
    """An extension is a real decision: over the two-second bar it
    shouts NEEDS DECISION with the pulled-in words, and the decide
    line points at the recorded WHY - never at the stale claim that
    no mechanism extends a body."""
    repaired, moves = snap_moment_to_speech(
        _moment(4, 267.358, 285.42), _reel4_transcript())
    extend = next(m for m in moves
                  if m.get("attribution") == "tail-extend")
    lines = decision_lines(4, extend, _reel4_transcript(), "")
    assert any("NEEDS DECISION" in line for line in lines)
    assert any("decision" in line and "engine" in line for line in lines)
    assert any("moment_boundary_repairs" in line for line in lines)
    assert not any("no pin kind" in line for line in lines)


# ------------------------------------------------- the family rules


def test_a_new_voice_taking_over_is_left_alone():
    """Turn boundaries are legitimate ends: the same words that
    extend a same-speaker thought stay untouched when another
    speaker takes over - reaching into their turn is selection's
    decision (redraw the span), never an automatic extension."""
    tx = _tx(
        _seg("Akshita", "the setup is done.", 10.0, 20.0, "u1",
             words=[_w("the", 10.0, 10.3),
                    _w("setup", 10.4, 10.7),
                    _w("is", 10.8, 10.9),
                    _w("done.", 11.0, 11.5)]),
        _seg("Craig", "and here is why it matters.", 11.6, 20.0, "u2",
             words=[_w("and", 11.6, 11.9),
                    _w("here", 12.0, 12.3),
                    _w("is", 12.4, 12.5),
                    _w("why", 12.6, 12.9),
                    _w("it", 13.0, 13.1),
                    _w("matters.", 13.2, 13.7)]),
    )
    new_end, finding = repair_moment_tail(11.5, 10.0, tx)
    assert (new_end, finding) == (11.5, None)


def test_take_cuts_are_not_stranded_tails():
    """The cutter's own edges are clean: words a take cut drops are
    removed on purpose, so the same plan that refuses a mid-word
    edge passes a take boundary on word edges untouched."""
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
    assert stranded_tail_keep_edges(100.0, 121.0, tx) == []


def test_a_tail_across_a_dropped_take_abstains_loudly():
    """Extending over a take the cutter removed would reinstate it:
    the predicate reports the crossing and applies nothing, so a
    human redraws the span."""
    tx = _tx(
        _seg("Host", "first telling of the line here.", 10.0, 20.0,
             "u1", words=[_w("first", 10.0, 10.3),
                          _w("telling", 10.4, 10.7),
                          _w("of", 10.8, 10.9),
                          _w("the", 11.0, 11.1),
                          _w("line", 11.2, 11.5),
                          _w("here.", 11.6, 12.0)]),
    )
    cuts = [reel_build.Cut(
        dropped_start=10.35, dropped_end=10.75, dropped_text="telling of",
        kept_start=30.0, kept_end=30.4, kept_text="telling of",
        speaker="Host", containment=1.0, jaccard=1.0)]
    new_end, finding = repair_moment_tail(
        10.3, 10.0, tx, take_cuts=cuts)
    assert new_end == 10.3
    assert finding["verdict"] == "abstain"
    assert "crosses a dropped take" in finding["reason"]


def test_the_fallback_pace_cleans_but_never_extends():
    """Where no bound segment reaches the edge, the transcript-wide
    fallback judges distance but never content: far speech is clean,
    nearby speech abstains for a human."""
    tx = _tx(
        _seg("Host", "a later thought.", 60.0, 70.0, "u1",
             words=[_w("later", 60.0, 60.4),
                    _w("thought.", 60.5, 61.0)]),
    )
    far_end, far_finding = repair_moment_tail(50.0, 40.0, tx)
    assert (far_end, far_finding) == (50.0, None)
    near_end, near_finding = repair_moment_tail(59.9, 40.0, tx)
    assert near_end == 59.9
    assert near_finding["verdict"] == "abstain"
    assert near_finding["inferred"] is False


def test_a_straddling_word_interior_is_never_held():
    """No boundary is ever placed on a straddling row: an end inside
    one belongs to the snap as before, however small the remainder."""
    tx = _tx(
        _seg("Akshita", "recommend you.", 412.63, 413.85, "u-a",
             words=[_w("recommend", 412.63, 412.99),
                    _w("you", 413.01, 413.17),
                    _w("yours", 413.59, 413.85)]),
        _seg("Craig", "about", 412.77, 414.03, None,
             words=[_w("about", 412.77, 414.03)]),
    )
    new_end, finding = repair_moment_tail(413.851, 412.63, tx)
    assert (new_end, finding) == (413.851, None)


def test_a_correct_reel_comes_out_byte_identical():
    """The neighbour proof: a moment that ends on a segment edge with
    the next speech a proper distance away passes through untouched -
    the same object, no moves, so twenty-eight correct reels keep
    exactly what they had. (An end mid-segment still widens to the
    segment edge exactly as before - the tail pass finds nothing to
    judge there and the snap owns it.)"""
    tx = _tx(
        _seg("Host", "the point is made.", 10.0, 20.0, "u1",
             words=[_w("the", 10.0, 10.3),
                    _w("point", 10.4, 10.7),
                    _w("is", 10.8, 10.9),
                    _w("made.", 11.0, 11.5)]),
        _seg("Host", "a later thought.", 60.0, 70.0, "u2",
             words=[_w("later", 60.0, 60.4),
                    _w("thought.", 60.5, 61.0)]),
    )
    moment = _moment(1, 10.0, 20.0)
    repaired, moves = snap_moment_to_speech(moment, tx)
    assert repaired is moment
    assert moves == []
    widened, widen_moves = snap_moment_to_speech(_moment(1, 10.0, 11.5),
                                                 tx)
    assert (widened.timeline_start, widened.timeline_end) == (10.0, 20.0)
    assert [m["boundary"] for m in widen_moves] == ["body_end"]
    assert "attribution" not in widen_moves[0]


# ------------------------------------------------- the ledger


def test_tail_repairs_land_on_the_ledger_idempotently(tmp_path):
    """Repairs are recorded and reversible: each lands on
    moment_boundary_repairs.json in that file's own shape, a rebuild
    rewrites the same entry instead of duplicating it, and entries
    the repair did not write are never touched."""
    project = tmp_path / "project"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    ledger_file = review / "moment_boundary_repairs.json"
    ledger_file.write_text(json.dumps({
        "key": "moment_boundary_repairs",
        "source": "firstmate 2026-09-18",
        "value": [{"kind": "moment_boundary_repair", "reel": 2,
                   "boundary": "body_end", "was": 1.0, "now": 2.0,
                   "attribution": "snap", "reason": "older repair",
                   "source": "firstmate 2026-09-18"}],
    }), encoding="utf-8")
    repairs = [
        (9, {"boundary": "body_end", "was": 693.3, "now": 693.3,
             "attribution": "tail-hold", "why": "approved bound stands"}),
        (4, {"boundary": "body_end", "was": 285.42, "now": 290.23,
             "attribution": "tail-extend", "why": "finishes the tale"}),
    ]
    first = record_tail_repairs(str(project), repairs)
    assert first == str(ledger_file)
    written = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert len(written["value"]) == 3
    by_reel = {entry["reel"]: entry for entry in written["value"]}
    assert by_reel[2]["reason"] == "older repair"
    assert by_reel[9]["attribution"] == "tail-hold"
    assert by_reel[4]["now"] == 290.23
    record_tail_repairs(str(project), repairs)
    again = json.loads(ledger_file.read_text(encoding="utf-8"))
    assert len(again["value"]) == 3


# ------------------------------------------------- the caption side


def test_a_held_bound_clips_its_caption_block_and_says_so():
    """The neighbour the hold would otherwise break: a range end
    inside the final word must not silently uncaption the block -
    the spine clips it to the range and reports the cut."""
    from library.tools.reel_spine import spine_for_reel

    tx = _reel9_transcript()
    moment = _moment(9, 631.115, 693.3)
    spine = spine_for_reel(moment, tx, [(631.115, 693.3)])
    assert len(spine["structure"]) == 1
    assert spine["structure"][-1]["timeline_end"] == pytest.approx(
        693.3 - 631.115)
    clipped = spine["range_clipped_blocks"]
    assert len(clipped) == 1
    assert clipped[0]["master_end"] == pytest.approx(693.38)
    assert clipped[0]["clipped_to"] == pytest.approx(693.3)
    words = [entry["word"] for entry in
             spine["structure"][-1]["word_timestamps"]]
    assert words[-1] == "misrecommended."


def test_an_abstention_is_loud_in_the_preview_and_the_build():
    """What the pass cannot judge is held for a human on both
    surfaces: the preview renders it HELD FOR DECISION (counted as
    flagged, never as moved), and the build's decision lines say
    the same."""
    from library.tools.reel_proposal import preview_snap, render_snap_preview

    tx = _tx(
        _seg("Host", "a later thought.", 60.0, 70.0, "u1",
             words=[_w("later", 60.0, 60.4),
                    _w("thought.", 60.5, 61.0)]),
    )
    report = preview_snap([_moment(1, 40.0, 59.9)], tx)
    assert report["moved"] == 0
    assert report["flagged"] == 1
    text = render_snap_preview(report)
    assert "HELD FOR DECISION" in text
    move = report["moments"][0]["moves"][0]
    assert move["abstained"] is True
    lines = decision_lines(1, move, tx, "")
    assert any("NEEDS DECISION" in line for line in lines)
    assert any("redraw" in line for line in lines)


def _reel28_transcript():
    """Reel 28's shape, load-bearing numbers verbatim: the closing
    thought runs 2256.08-2265.42s (9.34s, pace 0.21s) and the
    same-voice sentence after the approved bound runs
    2265.62-2287.14s (21.54s added). The middle run's interior words
    are abbreviated - only its span enters the verdict, and the span
    (text and times) is verbatim."""
    return _tx(
        _seg("Akshita",
             "So I am using AI a lot and I do think a lot of our "
             "audience will be as well and they're typing in very "
             "specific queries that don't work for Google but work "
             "for AI.",
             2256.08, 2265.42, "u-a",
             words=[
                 _w("So", 2256.08, 2256.53),
                 _w("I", 2257.1, 2257.27),
                 _w("am", 2257.27, 2257.49),
                 _w("using", 2257.49, 2257.81),
                 _w("AI", 2257.81, 2258.0),
                 _w("a", 2258.0, 2258.09),
                 _w("lot", 2258.09, 2258.4),
                 _w("and", 2258.4, 2258.53),
                 _w("I", 2258.53, 2258.6),
                 _w("do", 2258.6, 2258.83),
                 _w("think", 2258.83, 2259.14),
                 _w("a", 2259.26, 2259.32),
                 _w("lot", 2259.32, 2259.64),
                 _w("of", 2259.64, 2259.75),
                 _w("our", 2259.75, 2259.9),
                 _w("audience", 2259.9, 2260.3),
                 _w("will", 2260.3, 2260.4),
                 _w("be", 2260.4, 2260.61),
                 _w("as", 2260.61, 2260.8),
                 _w("well", 2260.8, 2261.14),
                 _w("and", 2261.4, 2261.64),
                 _w("they're", 2261.64, 2261.73),
                 _w("typing", 2261.73, 2262.1),
                 _w("in", 2262.1, 2262.26),
                 _w("very", 2262.26, 2262.5),
                 _w("specific", 2262.5, 2263.06),
                 _w("queries", 2263.06, 2263.43),
                 _w("that", 2263.43, 2263.59),
                 _w("don't", 2263.59, 2263.81),
                 _w("work", 2263.81, 2263.97),
                 _w("for", 2263.97, 2264.11),
                 _w("Google", 2264.11, 2264.45),
                 _w("but", 2264.45, 2264.6),
                 _w("work", 2264.6, 2264.81),
                 _w("for", 2264.81, 2264.98),
                 _w("AI.", 2265.01, 2265.42),
             ]),
        _seg("Akshita",
             "So make sure that your when you're trying to optimize "
             "your content, your website, your LinkedIn, etcetera, "
             "Try to see what you have as a differentiator, your "
             "niche, and try to see how humans would give queries to "
             "Chat GPT that are more um that are more like niche and "
             "have a lot more words in it because that's not usually",
             2265.62, 2284.59, "u-b",
             words=[
                 _w("So", 2265.62, 2265.81),
                 _w("make", 2265.81, 2266.09),
                 _w("sure", 2266.09, 2266.48),
                 _w("that", 2266.48, 2266.91),
                 _w("your", 2266.91, 2267.44),
                 _w("when", 2267.51, 2267.66),
                 _w("etcetera,", 2270.98, 2271.51),
                 _w("usually", 2284.27, 2284.59),
             ]),
        _seg("Akshita",
             "how they would search on Google and try to optimize "
             "for that.",
             2284.55, 2287.14, "u-c",
             words=[
                 _w("how", 2284.55, 2284.67),
                 _w("they", 2284.67, 2284.81),
                 _w("would", 2284.81, 2284.96),
                 _w("search", 2284.96, 2285.19),
                 _w("on", 2285.19, 2285.32),
                 _w("Google", 2285.32, 2285.76),
                 _w("and", 2286.09, 2286.19),
                 _w("try", 2286.19, 2286.47),
                 _w("to", 2286.47, 2286.61),
                 _w("optimize", 2286.61, 2286.8),
                 _w("for", 2286.8, 2286.92),
                 _w("that.", 2286.92, 2287.14),
             ]),
    )


def test_a_further_passage_reports_rather_than_extending():
    """The Reel 28 carve-out, as a general predicate rather than a
    per-reel exception: the tail adds 21.54s past a 9.34s closing
    thought - a further passage absorbed, not a severed tail
    finished - so the bound keeps its approved value and the fully
    measured finding is reported for a human instead of applied."""
    new_end, finding = repair_moment_tail(
        2265.6, 2217.71, _reel28_transcript())
    assert new_end == 2265.6
    assert finding["verdict"] == "report"
    assert finding["tail_end"] == 2287.14
    assert finding["kind"] == "sentence-tail"
    assert finding["inferred"] is True


def test_a_reported_reel_places_as_approved_but_stays_loud():
    """End to end of the carve-out: the snap keeps the approved
    bound (no body_end value move), records a tail-report move, and
    the preview flags it without counting it as moved."""
    from library.tools.reel_proposal import preview_snap

    repaired, moves = snap_moment_to_speech(
        _moment(28, 2217.71, 2265.6), _reel28_transcript())
    assert repaired.timeline_end == 2265.6
    reports = [m for m in moves
               if m.get("attribution") == "tail-report"]
    assert len(reports) == 1
    assert reports[0]["reported"] is True
    report = preview_snap([_moment(28, 2217.71, 2265.6)],
                          _reel28_transcript())
    assert report["moved"] == 0
    assert report["flagged"] == 1
    lines = decision_lines(28, report["moments"][0]["moves"][0],
                           _reel28_transcript(), "")
    assert any("NEEDS DECISION" in line for line in lines)
    assert any("moment_boundary_repairs" in line for line in lines)


# ------------------------------------------------- the authorised reel
#
# The captain, 2026-09-21, answering the board: "Extend it" - Reel 28
# lengthens by design, and every other accepted reel keeps reporting.
# The ruling travels as data
# (`library/tools/tail_extend_authorization.py`); the predicate,
# its thresholds and its report verdict are untouched.


def _reel28_authorizations():
    return {28: {"reel": 28,
                 "reason": "captain 2026-09-21: extend it",
                 "measured_edge": 2265.6,
                 "measured_tail_end": 2287.14,
                 "measured_gap": 0.02}}


def test_an_authorised_report_applies_as_an_extension():
    """Reel 28 with its recorded ruling extends exactly where the
    predicate would have: the body runs to the sentence end at
    2287.14s, and the finding, the WHY and the move all name whose
    decision that was."""
    repaired, moves = snap_moment_to_speech(
        _moment(28, 2217.71, 2265.6), _reel28_transcript(),
        tail_extend_authorizations=_reel28_authorizations())
    assert repaired.timeline_end == 2287.14
    extends = [m for m in moves
               if m.get("attribution") == "tail-extend"]
    assert len(extends) == 1
    assert extends[0]["now"] == 2287.14
    assert (extends[0]["authorized_by"]
            == "captain 2026-09-21: extend it")
    assert "captain 2026-09-21" in extends[0]["why"]
    assert reel_ranges(repaired, _reel28_transcript()) == [
        (repaired.timeline_start, 2287.14)]
    assert reel_build.midword_keep_edges(
        repaired.timeline_start, repaired.timeline_end,
        _reel28_transcript()) == []


def test_an_authorised_reel_is_loud_about_its_new_length():
    """The 21.54s extension crosses the two-second bar, so the
    decision lines shout NEEDS DECISION with the pulled-in words -
    lengthening an accepted reel is never a quiet move."""
    from library.tools.reel_proposal import preview_snap

    report = preview_snap([_moment(28, 2217.71, 2265.6)],
                          _reel28_transcript(),
                          tail_extend_authorizations=(
                              _reel28_authorizations()))
    assert report["moved"] == 1
    assert report["flagged"] == 1
    lines = decision_lines(28, report["moments"][0]["moves"][0],
                           _reel28_transcript(), "")
    assert any("NEEDS DECISION" in line for line in lines)


def test_a_sibling_reel_with_the_same_shape_still_reports():
    """The ruling is per reel, not per shape: Reel 10's identical
    severed tail, with no entry of its own, keeps the approved bound
    and reports - the carve-out the captain declined to widen stays
    closed."""
    repaired, moves = snap_moment_to_speech(
        _moment(10, 2217.71, 2265.6), _reel28_transcript(),
        tail_extend_authorizations=_reel28_authorizations())
    assert repaired.timeline_end == 2265.6
    assert [m for m in moves
            if m.get("attribution") == "tail-report"] != []


def test_an_authorised_extension_answers_the_recorded_report(tmp_path):
    """The ledger stops asking once answered: recording an
    authorised tail-extend supersedes the same reel and boundary's
    tail-report entry, and says so - while an UNauthorised extend
    would leave both standing."""
    project = tmp_path / "project"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    ledger_file = review / "moment_boundary_repairs.json"
    ledger_file.write_text(json.dumps({
        "key": "moment_boundary_repairs",
        "source": "firstmate 2026-09-19",
        "value": [{"kind": "moment_boundary_repair", "reel": 28,
                   "boundary": "body_end", "was": 2265.6, "now": 2265.6,
                   "attribution": "tail-report",
                   "reason": "reported, never applied",
                   "source": "firstmate 2026-09-19"}],
    }), encoding="utf-8")
    record_tail_repairs(
        str(project),
        [(28, {"boundary": "body_end", "was": 2265.6, "now": 2287.14,
               "attribution": "tail-extend",
               "why": "applied under captain 2026-09-21: extend it",
               "authorized_by": "captain 2026-09-21: extend it"})])
    written = json.loads(ledger_file.read_text(encoding="utf-8"))
    attributions = [entry["attribution"]
                    for entry in written["value"]]
    assert attributions == ["tail-extend"]
    assert (written["value"][0]["authorized_by"]
            == "captain 2026-09-21: extend it")
