"""A closer-less reel a project declares ends on its own thesis passes.

Fail-closed both ways: undeclared absent reels still fail, and a
declaration whose anchor is neither timed nor in the approved preview
fails too.

History: `docs/evidence/reel_quality_bar.md` (test_quality_bar_thesis_ending.py).
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import reel_quality_bar as qb
from library.tools.reel_proposal import CallToAction, ReelMoment


def _w(word, start, end):
    return {"word": word, "start": start, "end": end, "timed": True}


def _transcript():
    words_a = [_w(w, 10.0 + i * 0.5, 10.5 + i * 0.5)
               for i, w in enumerate(
                   "so what actually changed about search".split())]
    words_b = [_w(w, 20.0 + i * 0.5, 20.5 + i * 0.5)
               for i, w in enumerate(
                   "the question changed people ask now".split())]
    words_c = [_w(w, 40.0 + i * 0.5, 40.5 + i * 0.5)
               for i, w in enumerate(
                   "and that means the old ranking game stops paying".split())]
    return {
        "segments": [
            {"timeline_start": 10.0, "timeline_end": 20.0,
             "speaker": "Craig",
             "text": "so what actually changed about search",
             "resolve_item_id": "item_10", "words": words_a},
            {"timeline_start": 20.0, "timeline_end": 40.0,
             "speaker": "Akshita",
             "text": "the question changed people ask now",
             "resolve_item_id": "item_20", "words": words_b},
            {"timeline_start": 40.0, "timeline_end": 50.0,
             "speaker": "Craig",
             "text": "and that means the old ranking game stops paying",
             "resolve_item_id": "item_40", "words": words_c},
        ],
        "derived_from": {"duration_seconds": 1000.0,
                         "fps": 24000 / 1001},
    }


def _moment(number=2, start=10.0, end=50.0, transcript_preview=""):
    return ReelMoment(number=number, slug="the-question", reason="",
                      timeline_start=start, timeline_end=end,
                      transcript_preview=transcript_preview,
                      call_to_action=None)


def _moment_with_cta(transcript_preview=""):
    return ReelMoment(
        number=2, slug="the-question", reason="",
        timeline_start=10.0, timeline_end=25.0,
        transcript_preview=transcript_preview,
        call_to_action=CallToAction(
            timeline_start=5.0, timeline_end=8.0,
            text="go check it out, the links in the bio",
            speaker="Craig"))


def _transcript_with_cta():
    return {
        "segments": [
            {"timeline_start": 5.0, "timeline_end": 8.0,
             "speaker": "Craig", "text": "go check it out the links in the bio",
             "resolve_item_id": "cta",
             "words": [
                 _w(word, 5.0 + i * 0.5, 5.4 + i * 0.5)
                 for i, word in enumerate([
                     "go", "check", "it", "out", "the", "links",
                     "in", "bio"])]},
            {"timeline_start": 10.0, "timeline_end": 20.0,
             "speaker": "Craig", "text": "so what actually changed about search",
             "resolve_item_id": "body_1",
             "words": [
                 _w(word, 10.0 + i * 0.5, 10.4 + i * 0.5)
                 for i, word in enumerate([
                     "so", "what", "actually", "changed", "about",
                     "search"])]},
            {"timeline_start": 20.0, "timeline_end": 25.0,
             "speaker": "Akshita", "text": "and AI really likes that",
             "resolve_item_id": "body_2",
             "words": [
                 _w(word, 20.0 + i * 0.5, 20.4 + i * 0.5)
                 for i, word in enumerate([
                     "and", "AI", "really", "likes", "that"])]},
        ],
        "derived_from": {"duration_seconds": 1000.0,
                         "fps": 24000 / 1001},
    }


def _thesis(anchor):
    return {"reel": "Reel 02 - the-question",
            "ends_on": {"anchor_phrase": anchor},
            "tail_element": "none",
            "reason": "test: captain-authorised re-cut ends on its thesis"}


def test_declared_and_honoured_thesis_reads_thesis_not_absent():
    transcript = _transcript()
    moment = _moment()
    thesis = _thesis("ranking game stops paying")
    reading = qb.cta_reading(moment, transcript, {}, thesis=thesis)
    assert reading["source"] == "thesis"
    assert reading["is_the_ending"]
    assert reading["span"][0] < reading["span"][1]
    codes = {f.code for f in qb.exact_findings(moment, transcript, {},
                                               thesis=thesis)}
    assert qb.QB_CTA_ABSENT not in codes


def test_an_unhonoured_or_missing_declaration_still_fails():
    """Fail-closed both ways: no declaration, and a declaration whose
    anchor is true words of the reel but its OPENING, not its tail."""
    transcript = _transcript()
    moment = _moment()
    for thesis in (None, _thesis("so what actually changed")):
        kwargs = {} if thesis is None else {"thesis": thesis}
        reading = qb.cta_reading(moment, transcript, {}, **kwargs)
        assert reading["source"] == "absent"
        codes = {f.code for f in qb.exact_findings(moment, transcript, {},
                                                   **kwargs)}
        assert qb.QB_CTA_ABSENT in codes


def test_recorded_ending_in_approved_preview_passes_when_timing_omits_words():
    transcript = _transcript()
    anchor = "clearly saying why you're better than your competitor"
    moment = _moment(
        transcript_preview=(
            "The captain-approved answer ends with clearly saying why "
            "you're better than your competitor. A later exchange follows."))
    thesis = _thesis(anchor)

    reading = qb.cta_reading(moment, transcript, {}, thesis=thesis)
    codes = {f.code for f in qb.exact_findings(
        moment, transcript, {}, thesis=thesis)}

    assert reading["source"] == "thesis"
    assert reading["span"] is None
    assert "approved moment preview" in reading["evidence"]
    assert qb.QB_CTA_ABSENT not in codes


def test_a_recorded_body_ending_removes_a_planned_cta_from_the_batch_check(
        tmp_path):
    moment = _moment_with_cta()
    transcript = _transcript_with_cta()
    ending = _thesis("and AI really likes that")
    external = tmp_path / "external"
    external.mkdir()
    (external / "reel_ending.json").write_text(json.dumps({
        "version": 1,
        "endings": [ending],
    }), encoding="utf-8")

    report = qb.judge([moment], transcript, None,
                      project_folder=str(tmp_path))

    assert report.verdicts[0].cta["source"] == "thesis"
    assert qb.QB_CTA_ABSENT not in {
        f.code for f in report.verdicts[0].findings}


def test_preview_phrase_inside_body_does_not_remove_a_planned_cta(tmp_path):
    moment = _moment_with_cta(
        transcript_preview=(
            "clearly saying why you're better than your competitor. "
            "A follow-up thought comes after it."))
    ending = _thesis("clearly saying why you're better than your competitor")
    external = tmp_path / "external"
    external.mkdir()
    (external / "reel_ending.json").write_text(json.dumps({
        "version": 1,
        "endings": [ending],
    }), encoding="utf-8")

    report = qb.judge([moment], _transcript_with_cta(), None,
                      project_folder=str(tmp_path))

    assert report.verdicts[0].cta["source"] == "declared"
