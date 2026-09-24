"""The outstanding-request surface: an awaiting reel must be NAMED, not passed silently.

A build that owes a model answer used to report success and say nothing
about it: F22 returned its findings unchanged for an
`awaiting_model_answer` record with nothing placed, and no rollup
anywhere counted what the build owed. These tests fail on that old
shape (F22 `== []`, `collect` unknown) and pass on the new one.
"""

from __future__ import annotations

import json

from library.tools import awaiting_model_answers as awaiting
from library.tools import reel_semantic_visual as sem
from library.tools.reel_conformance_verifier import (
    FindingClass,
    check_semantic_visuals,
)

FPS = 24000 / 1001


def _awaiting_record(reel="Reel 09"):
    return {"reel": reel, "basis": sem.AWAITING_MODEL_ANSWER,
            "entries": [], "dropped": [], "segments": []}


# ── F22 names the awaiting reel ──────────────────────────────────────

def test_an_awaiting_reel_with_nothing_placed_is_reported_not_passed():
    findings = check_semantic_visuals("Reel 09", [], _awaiting_record(),
                                      FPS)
    assert len(findings) == 1
    assert findings[0].finding_class == FindingClass.F22
    assert "awaiting_model_answer" in findings[0].message


def test_the_awaiting_finding_is_a_warning_not_a_gate_failure():
    """A build the captain wants to look at is still worth building;
    what is not acceptable is claiming it is finished."""
    findings = check_semantic_visuals("Reel 09", [], _awaiting_record(),
                                      FPS)
    assert findings[0].severity == "warning"


def test_a_decision_for_nothing_still_passes_quietly():
    """`model_planned_none` is a decision, not an owed answer - F22
    must not start naming every reel the model deliberately left
    empty."""
    findings = check_semantic_visuals(
        "Reel 09", [],
        {"reel": "Reel 09", "basis": sem.MODEL_PLANNED_NONE,
         "entries": [], "dropped": [], "segments": []}, FPS)
    assert findings == []




# ── The rollup counts what the build wrote ───────────────────────────

def _write_semantic(project, records):
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True, exist_ok=True)
    (review / sem.PLAN_FILENAME).write_text(
        json.dumps({"format": "semantic_visual_plans/1",
                    "plans": records}),
        encoding="utf-8")






def test_collect_reads_motion_from_state_when_not_built_this_call(tmp_path):
    """A partial build that did not touch a reel still counts the
    motion answer that reel owes, off the stored build record."""
    from library.tools import reel_look as look

    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    state = {"step_outputs": {
        "build_reels": {"reel_build": {"picture_motion": [
            {"reel": "Reel 07", "basis": look.MOTION_AWAITING_ANSWER,
             "proposed": 0, "resolved": 0, "dropped": []}]}}}}
    report = awaiting.collect(str(project), state=state)
    assert report["outstanding_answers"] == 1
    assert report["reels"] == [
        {"reel": "Reel 07", "layers": ["picture_motion"]}]




def test_summary_lines_name_every_incomplete_reel():
    lines = awaiting.summary_lines({
        "reels": [
            {"reel": "Reel 09",
             "layers": ["picture_motion", "semantic_visuals"]},
            {"reel": "Reel 07", "layers": ["picture_motion"]}],
        "outstanding_answers": 3})
    text = "\n".join(lines)
    assert "3" in lines[0] and "incomplete" in lines[0]
    assert "Reel 09" in text and "Reel 07" in text


# ── End to end off a withheld answer, no Resolve, no model ──────────

class _Moment:
    number = 9
    timeline_name = "Reel 09 - your-website-is-only-20-percent"


def test_a_withheld_answer_travels_from_build_to_rollup(tmp_path):
    """The scratch-project proof: no answer file, so `build_for_reel`
    records `awaiting_model_answer`; once written, `collect` owes it
    and F22 names it instead of passing it."""
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    segments, record = sem.build_for_reel(
        _Moment(), {"structure": []}, [(631.12, 693.3)], str(project),
        fps=FPS, width=1080, height=1920)
    assert segments == []
    assert record["basis"] == sem.AWAITING_MODEL_ANSWER
    sem.write_records(str(project), [record])

    report = awaiting.collect(str(project), motion_records=[])
    assert report["outstanding_answers"] == 1
    assert report["reels"] == [
        {"reel": _Moment.timeline_name, "layers": ["semantic_visuals"]}]

    stored = sem.read_records(str(project))
    findings = check_semantic_visuals(
        _Moment.timeline_name, [],
        sem.record_for_reel(stored, _Moment.timeline_name), FPS)
    assert len(findings) == 1
    assert findings[0].severity == "warning"
