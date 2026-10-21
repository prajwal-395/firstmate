"""PLAN-007 measured on the representative corpus (BASE-003).

The corpus's briefs name their target roles at the FEATURE level, and
the punch list asks (PLAN-007) that the evidence about a plan go back
to the planner as a narrow revision, and that the loop stop and ask
when the remaining problem is the user's.

These tests pin what the corpus's OWN measured evidence settles:

- a brief whose role the evidence grounds keeps its plan, and the loop
  makes no model call about it;
- a brief whose role the evidence does not ground is stopped before it
  is staged, with a QUESTION, and not a plan that names a target the
  footage does not carry;
- the loop asks once for the affected part, and a repeat of the same
  run costs nothing.

The measurement tool lives OUTSIDE the engine, because the sealed
answer key must not be a path the engine can read.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_tool():
    """The corpus replanning tool, which lives outside the engine."""
    path = REPO_ROOT / "evaluation" / "tools" / "replanning_corpus.py"
    spec = importlib.util.spec_from_file_location("editorial_replanning_tool",
                                                  path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = _load_tool()


@pytest.fixture(scope="module")
def payload():
    return tool.measure()


def test_the_measurement_covers_every_corpus_brief(payload):
    # Defect: a brief the measurement skipped was a brief nobody counted.
    assert payload["brief_count"] == 13
    assert payload["plans_assessed"] == 8


def test_a_plan_naming_a_target_the_footage_lacks_is_stopped_before_staging(payload):
    # Defect: the plan named a target the substrate does not carry and
    # went on to stage it.
    assert payload["plans_refused_before_staging"]["after"] == 5
    assert payload["plans_refused_before_staging"]["denominator"] == 8
    assert payload["plans_with_an_operation_to_stage"]["before"] == 8
    assert payload["plans_with_an_operation_to_stage"]["after"] == 3


def test_the_briefs_that_need_the_user_are_asked(payload):
    # Defect: a brief whose premise the footage lacks was answered with
    # a plan instead of a question.
    assert payload["revision_outcomes"]["asked_the_user"] == 5
    assert payload["revision_outcomes"]["by_status"][
        "needs_judgement"] == 5


def test_the_loop_asks_once_for_the_affected_part(payload):
    # Defect: the planner was asked once per planned operation.
    calls = payload["model_calls"]

    assert calls["baseline_one_call_per_planned_operation"]["calls"] == 11
    assert calls["the_loop"]["calls"] == 5
    assert calls["saved_against_the_operation_baseline"] == 6
    assert calls["a_repeat_of_the_same_run_costs"] == 0
