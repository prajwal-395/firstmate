"""A failed QA station must be visible, and must not read as a warning.

Error-severity QA check failures used to be appended to
`results["warnings"]`, where they sat among "Fairlight preset not found"
and friends - and the build still printed "Build succeeded" with an empty
error list. A check that runs, can fail, and whose failure nobody sees is
barely better than one that cannot fail, which is the defect Phase 0
existed to remove.

Step one of the captain's two-step ruling (2026-08-16): make it LOUD and
distinct. Not fatal - `success` is deliberately unchanged, because nobody
has yet measured how often these fire on real footage, and making them
fatal on no evidence would be the mirror image of the mistake. Step two is
that measurement, and `qa_failures` is the channel that supplies it.
"""
import ast
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDERER = os.path.join(PROJECT_ROOT, "library", "steps", "step_6_01_render",
                        "resolve_build_timeline.py")


def _source():
    with open(RENDERER, encoding="utf-8") as f:
        return f.read()


def _run_qa_body():
    """The source of the nested `_run_qa` helper inside build_timeline."""
    tree = ast.parse(_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_qa":
            return ast.dump(node)
    pytest.fail("_run_qa not found in the renderer")


def test_qa_failures_are_not_filed_as_warnings():
    """The whole point: they must not be lost in the warning list."""
    dumped = _run_qa_body()
    assert "'warnings'" not in dumped and '"warnings"' not in dumped, (
        "QA check failures are being appended to results['warnings'] again. "
        "They belong in results['qa_failures'], where they are reported as "
        "their own category.")


def test_a_qa_failure_is_printed_as_a_failure_not_a_warning():
    src = _source()
    assert "QA CHECK FAILURE" in src, (
        "a failed station must be announced in the build output, not "
        "folded into the warning list")


def test_qa_failures_are_not_fatal_yet():
    """Step one of the ruling, held here so step two is a deliberate change.

    `success` must not depend on qa_failures until the evidence exists.
    Whoever makes it fatal should have to delete this test and say why.
    """
    src = _source()
    line = next(l for l in src.splitlines() if 'results["success"] =' in l)
    assert "qa_failures" not in line, (
        "QA failures have been made fatal. That is step TWO of the ruling "
        "and needs the measurement first: how often would a station have "
        "failed across real runs? Making it fatal without that number is "
        "the mirror image of the defect it fixes.")
    assert "errors" in line, (
        "results['success'] must still depend on results['errors']"
    )
    # success must NOT consult qa_reports, verification_passed, or
    # all_passed - that is the step-two gate.
    assert "qa_reports" not in line and "verification_passed" not in line, (
        "success has been coupled to QA station outcomes. That is step TWO."
    )


def test_the_render_step_forwards_qa_failures_to_the_ledger():
    """The evidence channel must survive into pipeline_data.json.

    step_6_01's output payload hand-picks its keys, so anything not named
    there is dropped before the ledger sees it. Step two of the ruling -
    deciding whether a failing station should be fatal - needs a count
    across real runs, and this is the only place that count can accrue.
    """
    step_py = os.path.join(PROJECT_ROOT, "library", "steps",
                           "step_6_01_render", "step.py")
    with open(step_py, encoding="utf-8") as f:
        src = f.read()
    assert '"qa_failures": result.get("qa_failures", [])' in src, (
        "the render step must forward qa_failures into its output, or the "
        "failure rate can never be measured from real runs")
