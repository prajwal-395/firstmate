"""A closer-less reel a project declares ends on its own thesis passes.

Reel 27 (2026-09-19): Q->A on Google reviews, shared website-checkout
closer deleted as topically alien, no own-thread replacement in the
episode. `declared`/`in_body` cannot express that - a closer inside
its own body is refused as a double play - so QB-CTA-ABSENT failed a
reel ending exactly where its authorised re-cut puts it (AGENTS.md
10.4). A HAND-WRITTEN `external/reel_ending.json` entry the reel
honours (anchor tail-matches played speech, run measured in timed
words) now reads `thesis`, never ABSENT.

Fail-closed both ways: undeclared absent reels still fail, and a
declaration whose anchor is not the reel's tail (or is unmeasurable
in timed words) fails too - a declaration nobody honours is not a
pass.

`library/tools/reel_quality_bar.py` (`thesis_reading`,
`cta_reading`, `exact_findings`, `judge`); declarations owned by
`library/tools/reel_ending.py`; wired into the gate in
`library/tools/reel_conformance_verifier.py`.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools import reel_quality_bar as qb
from library.tools.reel_proposal import ReelMoment


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


def _moment(number=2, start=10.0, end=50.0):
    return ReelMoment(number=number, slug="the-question", reason="",
                      timeline_start=start, timeline_end=end,
                      call_to_action=None)


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


def test_undeclared_absent_reel_still_fails():
    transcript = _transcript()
    moment = _moment()
    reading = qb.cta_reading(moment, transcript, {})
    assert reading["source"] == "absent"
    codes = {f.code for f in qb.exact_findings(moment, transcript, {})}
    assert qb.QB_CTA_ABSENT in codes


def test_declaration_whose_anchor_is_not_the_tail_still_fails():
    transcript = _transcript()
    moment = _moment()
    # True words of the reel, but the OPENING - not where it ends.
    thesis = _thesis("so what actually changed")
    reading = qb.cta_reading(moment, transcript, {}, thesis=thesis)
    assert reading["source"] == "absent"
    codes = {f.code for f in qb.exact_findings(moment, transcript, {},
                                               thesis=thesis)}
    assert qb.QB_CTA_ABSENT in codes


def test_declaration_with_words_nowhere_in_the_reel_still_fails():
    transcript = _transcript()
    moment = _moment()
    thesis = _thesis("words nobody ever said here")
    assert qb.cta_reading(
        moment, transcript, {}, thesis=thesis)["source"] == "absent"


def test_judge_reads_thesis_endings_off_the_project(tmp_path):
    transcript = _transcript()
    moment = _moment()
    external = tmp_path / "external"
    external.mkdir()
    (external / "reel_ending.json").write_text(json.dumps({
        "version": 1,
        "endings": [_thesis("ranking game stops paying")],
    }), encoding="utf-8")
    report = qb.judge([moment], transcript, None,
                      project_folder=str(tmp_path))
    assert len(report.verdicts) == 1
    assert report.verdicts[0].cta["source"] == "thesis"
    assert qb.QB_CTA_ABSENT not in {
        f.code for f in report.verdicts[0].findings}


def test_judge_without_a_project_reads_as_before(tmp_path):
    transcript = _transcript()
    moment = _moment()
    report = qb.judge([moment], transcript, None)
    assert report.verdicts[0].cta["source"] == "absent"
