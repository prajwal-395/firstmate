"""The wording correction reaches regenerated displays by mechanism.

The source transcript is clean; the prompt half does not move copies
made before the correction (the 192 lucy->Lucie divergences: judge
quotes, selected moments, proposals, conformance verdicts). Each test
below regenerates a display from a STALE copy with an ACTIVE
correction on file and shows the post-pass carrying the corrected
spelling - or, where the regen point cannot run here, shows the unit
the regen point calls. Nothing here hand-edits a display: the
correction lives in `learned_context` and every fix below is derived
from it. Fixtures under `tmp_path` (AGENTS.md 8).
"""

import copy
import json
import os

from library.tools import transcript_corrections
from library.tools.display_respell import (
    apply_post_pass,
    respell_display,
)


def _project_with_correction(root, heard="lucy", correct="Lucie"):
    return transcript_corrections.record_spelling(
        root, heard, correct, "vetting the respell post-pass")


def test_respell_fixes_copy_and_keeps_identity():
    corrections = [{"id": "lc-1", "heard": "lucy", "correct": "Lucie"}]
    obj = {
        "reason": "visit lucy today",
        "cta": {"text": "say lucy now", "speaker": "Akshita"},
        "slug": "lucy-thing",
        "label": "mg_lucy",
        "timeline_name": "Reel 1 - lucy",
        "anchor_phrase": "say lucy",
        "source_file": "/x/lucy_takes/a.mov",
        "id": "sub_lucy_001",
    }
    report = respell_display(obj, corrections)
    assert report["replacements"] == 2
    assert obj["reason"] == "visit Lucie today"
    assert obj["cta"]["text"] == "say Lucie now"
    # Identity and reference never move.
    assert obj["slug"] == "lucy-thing"
    assert obj["label"] == "mg_lucy"
    assert obj["timeline_name"] == "Reel 1 - lucy"
    assert obj["anchor_phrase"] == "say lucy"
    assert obj["source_file"] == "/x/lucy_takes/a.mov"
    assert obj["id"] == "sub_lucy_001"
    assert obj["cta"]["speaker"] == "Akshita"


def test_respell_without_corrections_touches_nothing():
    obj = {"reason": "visit lucy today"}
    before = copy.deepcopy(obj)
    assert respell_display(obj, []) == {"replacements": 0, "fields": []}
    assert obj == before


def _words(*tokens, start=10.0):
    words = []
    cursor = start
    for token in tokens:
        words.append({"word": token, "start": cursor,
                      "end": round(cursor + 0.4, 3), "timed": True})
        cursor = round(cursor + 0.5, 3)
    return words


def _two_speaker_transcript(stale_word="lucy"):
    return {
        "derived_from": {"duration_seconds": 60.0},
        "segments": [
            {"text": f"say {stale_word} to the camera hello friend",
             "timeline_start": 10.0, "timeline_end": 16.0,
             "speaker": "A", "resolve_item_id": "x1",
             "words": _words("say", stale_word, "to", "the", "camera",
                             "hello", "friend")},
            {"text": "yes indeed my friend absolutely correct words",
             "timeline_start": 16.0, "timeline_end": 22.0,
             "speaker": "B", "resolve_item_id": "x2",
             "words": _words("yes", "indeed", "my", "friend",
                             "absolutely", "correct", "words",
                             start=16.0)},
        ],
    }


def test_select_reels_regen_carries_correction(tmp_path):
    from library.steps.step_3_04_select_reels.post_bridge import resolve

    root = str(tmp_path)
    _project_with_correction(root)
    transcript = _two_speaker_transcript(stale_word="lucy")
    llm = {"moments": [{
        "start": 10.0, "end": 22.0, "slug": "t",
        "reason": "visit lucy today"}]}
    out = resolve(llm, {"timeline_transcript": transcript,
                        "project_folder": root})
    moments = out["reel_selection"]["moments"]
    assert len(moments) == 1
    assert "lucy" not in json.dumps(moments).replace("Lucie", "")
    assert moments[0]["slug"] == "t"
    assert "Lucie" in moments[0]["transcript_preview"]


def test_select_reels_regen_without_project_is_unchanged():
    from library.steps.step_3_04_select_reels.post_bridge import resolve

    transcript = _two_speaker_transcript(stale_word="lucy")
    llm = {"moments": [{
        "start": 10.0, "end": 22.0, "slug": "t",
        "reason": "visit lucy today"}]}
    out = resolve(llm, {"timeline_transcript": transcript,
                        "project_folder": ""})
    moments = out["reel_selection"]["moments"]
    assert len(moments) == 1
    # No project, no corrections: the regen is what it always was.
    assert "visit lucy today" in moments[0]["reason"]


def test_judge_bridge_lines_carry_correction(tmp_path):
    from library.steps.step_3_05_judge_reels.bridge import build_context
    from library.tools.reel_proposal import ReelMoment

    root = str(tmp_path)
    _project_with_correction(root)
    transcript = _two_speaker_transcript(stale_word="lucy")
    moment = ReelMoment.from_dict({
        "number": 1, "slug": "t", "reason": "r",
        "timeline_start": 10.0, "timeline_end": 22.0,
        "approval": "proposed"})
    from library.tools.reel_proposal import enrich
    moment = enrich(moment, transcript)
    out = build_context({
        "timeline_transcript": transcript,
        "reel_selection": {"moments": [moment.as_dict()]},
        "project_folder": root})
    says = [line["says"] for row in out["reels_to_read"]
            for line in row["lines"]]
    assert says and all("Lucie" in line or "lucy" not in line
                        for line in says)


def test_judge_post_bridge_corrects_quote_rather_than_refusing(tmp_path):
    from library.steps.step_3_05_judge_reels.post_bridge import resolve

    root = str(tmp_path)
    _project_with_correction(root)
    # Clean transcript (post-correction), reader quoting the OLD
    # spelling: without the post-pass the quote check refuses the
    # reading; with it the quote is corrected and grounded.
    transcript = _two_speaker_transcript(stale_word="Lucie")
    moment_dict = {
        "number": 1, "slug": "t", "reason": "r",
        "timeline_start": 10.0, "timeline_end": 22.0,
        "approval": "proposed",
        "transcript_preview": transcript["segments"][0]["text"],
        "speakers": ["A", "B"]}
    llm = {"readings": [{
        "reel": 1, "rank": 1,
        "claim": "the closer names lucy",
        "claim_quote": "say lucy to the camera",
        "opening_quote": "say lucy to the camera",
        "closing_quote": "absolutely correct words"}]}
    data = {"timeline_transcript": transcript,
            "reel_selection": {"moments": [moment_dict]},
            "reels_to_read": [{"reel": 1, "runs_for_seconds": 12.0}],
            "project_folder": root}
    out = resolve(llm, data)
    assert out["reel_judgement"]["refused"] == []
    readings = out["reel_judgement"]["readings"]
    assert len(readings) == 1
    assert "lucy" not in json.dumps(readings).replace("Lucie", "")


def test_judge_post_bridge_holds_grounding_on_stale_transcript(tmp_path):
    from library.steps.step_3_05_judge_reels.post_bridge import resolve

    root = str(tmp_path)
    _project_with_correction(root)
    # Stale transcript, reader who read the correction and wrote it
    # right: without the post-pass the corrected quote is UNGROUNDED
    # against stale words and refused; with it both sides respell and
    # the reading stands.
    transcript = _two_speaker_transcript(stale_word="lucy")
    moment_dict = {
        "number": 1, "slug": "t", "reason": "r",
        "timeline_start": 10.0, "timeline_end": 22.0,
        "approval": "proposed",
        "transcript_preview": transcript["segments"][0]["text"],
        "speakers": ["A", "B"]}
    llm = {"readings": [{
        "reel": 1, "rank": 1,
        "claim": "the closer names Lucie",
        "claim_quote": "say Lucie to the camera",
        "opening_quote": "say Lucie to the camera",
        "closing_quote": "absolutely correct words"}]}
    data = {"timeline_transcript": transcript,
            "reel_selection": {"moments": [moment_dict]},
            "reels_to_read": [{"reel": 1, "runs_for_seconds": 12.0}],
            "project_folder": root}
    out = resolve(llm, data)
    assert out["reel_judgement"]["refused"] == []
    assert len(out["reel_judgement"]["readings"]) == 1


def test_post_pass_never_refuses_and_says_what_moved(tmp_path, capsys):
    obj = {"text": "say lucy now"}
    report = apply_post_pass(obj, str(tmp_path), "vetting")
    assert report == {"replacements": 0, "fields": []}
    assert obj == {"text": "say lucy now"}
    assert capsys.readouterr().err == ""


def test_verdict_shaped_report_respells_deterministically():
    corrections = [{"id": "lc-1", "heard": "lucy", "correct": "Lucie"}]
    report = {"quality_bar": {"verdicts": [{
        "name": "Reel 1", "number": 1,
        "call_to_action": {"text": "the lucy system"},
        "findings": [{"code": "QB_X", "severity": "warning",
                      "detail": {"text": "says lucy twice"}}]}]}}
    out = respell_display(report, corrections)
    assert out["replacements"] == 2
    verdict = report["quality_bar"]["verdicts"][0]
    assert verdict["call_to_action"]["text"] == "the Lucie system"
    assert verdict["findings"][0]["code"] == "QB_X"
    assert verdict["name"] == "Reel 1"
