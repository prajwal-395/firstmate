"""Tests for the intent receipt (gap E12, scaffold).

The receipt composes the intent-check verdicts (E1a-E1d) into a single
end-to-end proof that the render satisfies the creative direction. The
defects these tests name:

* A render that passes every structural gate but fails a creative goal
  has its failure scattered across qa_report.json rows - nothing
  composes them into a single named failure the captain can read.
* A render no intent check measured gets a "clean" receipt - a hollow
  proof that claims satisfaction it never measured.
* A goal whose checks did not run is reported as "pass" - an absent
  measurement read as a verdict of fine.

Nothing here asserts a registry or enumeration contains named entries;
each test drives the receipt against a named defect and reads the verdict
the reader would give it.
"""
import json
from pathlib import Path

from library.tools import intent_receipt as ir
from library.tools import qa_findings as qa


def _project(tmp_path: Path, rows) -> Path:
    """A project carrying one qa_report.json, and nothing else."""
    project = tmp_path / "a_project"
    (project / "exports").mkdir(parents=True)
    (project / "exports" / "qa_report.json").write_text(
        json.dumps(rows), encoding="utf-8")
    return project


def _receipt(tmp_path: Path, rows):
    project = _project(tmp_path, rows)
    return ir.compose_receipt(qa.load_findings(str(project), {}))


def _row(metric, passed, detail, severity="warning", value=None):
    return {"metric": metric, "passed": passed, "value": value,
            "threshold": None, "severity": severity, "detail": detail}


def test_a_failing_creative_goal_is_named_in_the_receipt(tmp_path):
    """A render that passes every structural gate but fails a creative
    goal has its failure composed into a single named row.

    The defect: the failure is scattered across qa_report.json rows
    nobody reads together, so a render that ships a creative failure
    looks clean beside its structural passes.
    """
    receipt = _receipt(tmp_path, [
        _row("lufs", True, "LUFS: -14.0", "info", -14.0),
        _row("resolution", True, "1080x1920", "info", "1080x1920"),
        _row("caption_legibility", False,
             "A caption whose smallest card inks under the legibility "
             "floor at the delivered resolution"),
        _row("caption_obscuring", True, "No caption overlaps a face"),
        _row("broll_correspondence", True, "2 of 2 cutaways illustrate"),
        _row("punch_in_face", True, "No face cut at a punch-in window"),
    ])

    assert not receipt.clean
    assert [g.goal for g in receipt.failures] == ["caption_quality"]
    failed = receipt.failures[0]
    assert failed.verdict == ir.FAIL
    assert len(failed.failed_findings) == 1
    assert "inks under the legibility floor" in (
        failed.failed_findings[0].detail)
    # The structural passes do not wash out the creative failure.
    assert receipt.goals_measured == 3


def test_passing_intent_checks_produce_a_clean_receipt(tmp_path):
    """A render whose intent checks all pass produces a clean receipt."""
    receipt = _receipt(tmp_path, [
        _row("lufs", True, "LUFS: -14.0", "info", -14.0),
        _row("caption_legibility", True, "Every card inks above the floor"),
        _row("caption_obscuring", True, "No caption overlaps a face"),
        _row("broll_correspondence", True, "2 of 2 cutaways illustrate"),
        _row("punch_in_face", True, "No face cut at a punch-in window"),
    ])

    assert receipt.clean
    assert receipt.failures == []
    assert receipt.goals_measured == 3


def test_no_intent_checks_is_not_a_clean_receipt(tmp_path):
    """A render no intent check measured is not a render that satisfied
    the creative direction.

    The defect: a receipt with no measurements reads as clean, so a
    render whose intent was never measured ships with a hollow proof.
    """
    receipt = _receipt(tmp_path, [
        _row("lufs", True, "LUFS: -14.0", "info", -14.0),
        _row("resolution", True, "1080x1920", "info", "1080x1920"),
        _row("black_frames", True, "No black frames", "info", 0),
    ])

    assert not receipt.clean
    assert receipt.goals_measured == 0
    assert receipt.failures == []
    lines = "\n".join(ir.summary_lines(receipt))
    assert "no creative-intent checks ran" in lines


def test_a_goal_whose_checks_did_not_run_is_not_measured(tmp_path):
    """A goal with no checks in the report is NOT_MEASURED, never pass.

    The defect: an absent measurement read as a verdict of fine - the
    receipt would claim a goal was satisfied when nothing measured it.
    """
    receipt = _receipt(tmp_path, [
        _row("caption_legibility", True, "Every card inks above the floor"),
        _row("caption_obscuring", True, "No caption overlaps a face"),
    ])

    by_goal = {g.goal: g for g in receipt.goals}
    assert by_goal["caption_quality"].verdict == ir.PASS
    assert by_goal["punch_in_face"].verdict == ir.NOT_MEASURED
    assert by_goal["broll_correspondence"].verdict == ir.NOT_MEASURED
    assert by_goal["heard_vs_planned"].verdict == ir.NOT_MEASURED
    # The gap is reported, not hidden: the summary names what was not
    # measured so a reader knows the proof is partial.
    lines = "\n".join(ir.summary_lines(receipt))
    assert "punch_in_face: not measured" in lines
    assert "1 of 4 creative goals measured" in lines


def test_the_receipt_reads_through_the_one_reader(tmp_path):
    """The receipt composes the findings qa_findings read, never a
    private opinion about which rows matter.

    The defect: a second classifier that decides which findings are
    intent findings would drift from the reader the run summary and
    step 3.03 use, so the receipt could name a failure the summary does
    not see (or hide one it does).
    """
    rows = [
        _row("punch_in_face", False,
             "A face cut by the frame edge at a declared punch-in window"),
    ]
    project = _project(tmp_path, rows)
    findings = qa.load_findings(str(project), {})
    receipt = ir.compose_receipt(findings)

    # The receipt's source is the reader's source, not a second guess.
    assert receipt.source == findings.source
    assert receipt.source_detail == findings.source_detail
    # A failing row the reader classifies as failing is a failure here.
    punch = {g.goal: g for g in receipt.goals}["punch_in_face"]
    assert punch.verdict == ir.FAIL
    assert punch.findings[0].metric == "punch_in_face"


def test_multiple_failures_in_one_goal_are_all_named(tmp_path):
    """A goal with two failing checks names both, not just the first."""
    receipt = _receipt(tmp_path, [
        _row("caption_legibility", False, "Card 3 inks under the floor"),
        _row("caption_obscuring", False, "Card 5 overlaps a face"),
    ])

    failed = receipt.failures[0]
    assert failed.goal == "caption_quality"
    assert len(failed.failed_findings) == 2
    details = [f.detail for f in failed.failed_findings]
    assert "inks under the floor" in details[0]
    assert "overlaps a face" in details[1]
    lines = "\n".join(ir.summary_lines(receipt))
    assert lines.count("✗ caption_quality") == 2
