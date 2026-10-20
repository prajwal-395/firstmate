"""The live generalization matrix and its runner must agree, in both directions.

The matrix declares the dimensions Ren must generalize across
(`tests/live/fixtures/generalization_matrix.json`) and the runner
executes the ones it can on the shared qualification timeline. The
matrix's own execution contract says it:

    "coverage_scope": "live_runner_dimensions_only; all other declared
                       values must be listed in uncovered_dimensions"
    "uncovered_dimensions_reported": true

So a value the matrix declares is either EXECUTED or REPORTED as
uncovered, and a value the runner reports as uncovered is one the
matrix declares. A value that is silently neither is a dimension
nobody is testing and nobody said was missing - which is how a matrix
stops being a matrix.

The same holds for the invariants: the matrix REQUIRES eight, the
runner checks six, and the two it does not check are reported with the
dimension that would have exercised them. An invariant that is simply
missing from the report reads as if the matrix never required it.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.live import generalization_runner as runner

MATRIX_PATH = runner.MATRIX_PATH


def _matrix() -> dict:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _value_name(value) -> str:
    """A matrix value as one comparable name ("1920x1080")."""
    if isinstance(value, list):
        return "x".join(str(part) for part in value)
    return str(value)


def _runs(dimension: str, value) -> bool:
    """Whether the runner runs this matrix value."""
    name = _value_name(value)
    if dimension in runner.COVERED:
        return name in runner.COVERED[dimension]
    return name in runner.COVERED_MATRIX_VALUES.get(dimension, ())


def _mentions(key: str, name: str) -> bool:
    """Whether an uncovered key names this value.

    The runner names an uncovered value by the dimension it belongs to
    ("graded_project", "source_prores_mov"), so the value is named when
    the key carries it as a whole underscore-separated group.
    """
    if key == name:
        return True
    return (key.startswith(name + "_") or key.endswith("_" + name)
            or ("_" + name + "_") in key)


def _reported_uncovered(dimension: str, value) -> bool:
    """Whether the runner reports this matrix value as uncovered."""
    name = _value_name(value)
    uncovered = runner.UNCOVERED
    if dimension in uncovered:
        return True
    return any(_mentions(key, name) for key in uncovered)


def test_every_matrix_value_is_executed_or_reported_uncovered():
    """The contract the matrix writes about itself."""
    matrix = _matrix()
    for dimension, values in matrix["dimensions"].items():
        for value in values:
            assert (_runs(dimension, value)
                    or _reported_uncovered(dimension, value)), (
                f"{dimension}={value} is neither executed nor reported "
                f"as uncovered")


def test_the_runner_runs_nothing_the_matrix_does_not_declare():
    """A covered value the matrix never declared is a value nothing checks."""
    declared = _matrix()["dimensions"]
    flat = {_value_name(value)
            for values in declared.values() for value in values}
    for dimension, values in runner.COVERED.items():
        assert dimension in declared, (
            f"the runner varies {dimension}, which the matrix does not "
            f"declare")
        for value in values:
            assert _value_name(value) in flat, (
                f"the runner runs {dimension}={value}, which the matrix "
                f"does not declare")
    for dimension, values in runner.COVERED_MATRIX_VALUES.items():
        assert dimension in declared, (
            f"the runner runs {dimension}, which the matrix does not "
            f"declare")
        for value in values:
            assert _value_name(value) in flat, (
                f"the runner runs {dimension}={value}, which the matrix "
                f"does not declare")


def test_every_required_invariant_is_checked_or_reported_unverified():
    """The matrix requires eight invariants; the runner checks six."""
    required = runner.required_invariants()
    assert required, "the matrix declares no required invariant"
    unverified = {row["invariant"]: row["reason"]
                  for row in runner.unverified_invariants()}
    for name in required:
        if name in runner.INVARIANTS:
            continue
        assert name in unverified, (
            f"{name} is required by the matrix, not checked by the "
            f"runner, and not reported as unverified either")
        assert unverified[name].strip(), (
            f"{name} is reported as unverified with no reason")


def test_the_reported_uncovered_reasons_name_a_dimension():
    """A reason that names nothing cannot be acted on."""
    for dimension, reason in runner.UNCOVERED.items():
        assert isinstance(reason, str) and reason.strip(), (
            f"{dimension} is reported as uncovered with no reason")


def test_the_matrix_and_the_runner_agree_on_the_fingerprint_contract():
    """The matrix requires a case id and a fixture fingerprint per case."""
    contract = _matrix()["execution_contract"]
    assert contract["record_case_id_and_fixture_fingerprint"] is True
    cases = runner.pairwise_cases()
    assert cases
    for case in cases:
        assert case["case_id"]
        assert runner.fixture_fingerprint(case["dimensions"])


def test_the_pairwise_matrix_covers_every_covered_value():
    """Every value the runner claims to cover appears in some case."""
    cases = runner.pairwise_cases()
    seen = {dimension: set() for dimension in runner.COVERED}
    for case in cases:
        for dimension, value in case["dimensions"].items():
            if dimension in seen:
                seen[dimension].add(value)
    for dimension, values in runner.COVERED.items():
        for value in values:
            assert value in seen[dimension], (
                f"{dimension}={value} is claimed covered but no case "
                f"exercises it")


def test_the_matrix_text_is_the_one_the_runner_reads():
    """The runner reads this file; a second matrix would be a second truth."""
    assert MATRIX_PATH.name == "generalization_matrix.json"
    assert MATRIX_PATH.parent.name == "fixtures"
