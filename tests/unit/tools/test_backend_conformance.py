"""The backend conformance check: the comparison, the recorded set, and
the run itself.

Defects caught:

* a comparison that ignores one of the three answers (the exit code,
  the prose, the state the operation left behind) - a divergence in any
  of them must be named;
* a backend that could not be run counted as an agreement, or as a
  disagreement - it is UNMEASURED, and an absent binary must never read
  as a pass;
* a recorded request set that has drifted from the fixtures it names,
  or that stops covering the verbs the native front door serves;
* the native front door and the Python CLI answering one recorded
  request differently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from library.tools import backend_conformance as conformance


def _outcome(backend="native", **fields):
    outcome = conformance.Outcome(backend=backend)
    for name, value in fields.items():
        setattr(outcome, name, value)
    return outcome


def _request(identifier="r"):
    return conformance.Request(
        id=identifier,
        argv=("check", "demo"),
        env={},
        files=(),
        footage=(),
        omit=(),
        note="",
        directory=Path("."),
    )


def test_a_different_exit_code_is_a_disagreement():
    comparison = conformance.compare(
        _request(),
        {
            "native": _outcome(exit_code=0),
            "python": _outcome(exit_code=4),
        },
    )
    assert "exit_code" in comparison.differences
    assert not comparison.agreed
    assert comparison.outcome_class == "disagreed"


def test_different_prose_is_a_disagreement():
    comparison = conformance.compare(
        _request(),
        {
            "native": _outcome(exit_code=4, stderr="ren: refused - one\n"),
            "python": _outcome(exit_code=4, stderr="ren: refused - another\n"),
        },
    )
    assert "stderr" in comparison.differences
    assert not comparison.agreed


def test_different_state_is_a_disagreement():
    comparison = conformance.compare(
        _request(),
        {
            "native": _outcome(exit_code=0, state={"before": "{}", "after": "{}"}),
            "python": _outcome(exit_code=0, state={"before": "{}", "after": '{"x": "1"}'}),
        },
    )
    assert "state" in comparison.differences
    assert not comparison.agreed


def test_the_same_answer_agrees():
    comparison = conformance.compare(
        _request(),
        {
            "native": _outcome(exit_code=0, stdout="ok\n", stderr="",
                               state={"before": "{}", "after": "{}"}),
            "python": _outcome(exit_code=0, stdout="ok\n", stderr="",
                               state={"before": "{}", "after": "{}"}),
        },
    )
    assert comparison.agreed
    assert comparison.differences == []
    assert comparison.outcome_class == "completed"


def test_a_backend_that_could_not_run_is_unmeasured_not_agreed():
    comparison = conformance.compare(
        _request(),
        {
            "native": _outcome(exit_code=0),
            "python": _outcome(error="the backend could not be started"),
        },
    )
    assert not comparison.measured
    assert not comparison.agreed
    assert comparison.outcome_class == "unmeasured"


def test_an_unmeasured_request_is_not_counted_as_a_disagreement():
    report = conformance.Report(
        comparisons=[
            conformance.Comparison(
                request=_request("a"),
                outcomes={"native": _outcome(exit_code=0)},
                differences=[],
            ),
            conformance.Comparison(
                request=_request("b"),
                outcomes={
                    "native": _outcome(exit_code=0),
                    "python": _outcome(error="no binary"),
                },
                differences=[],
            ),
        ],
        backends=["native", "python"],
    )
    assert report.requests == 2
    assert len(report.disagreed) == 0
    assert len(report.unmeasured) == 1
    assert report.classification()["unmeasured"] == 1


def test_the_recorded_set_names_fixtures_that_exist():
    requests = conformance.load_requests()
    assert requests, "the recorded request set is empty"
    identifiers = [request.id for request in requests]
    assert len(set(identifiers)) == len(identifiers), "two requests share an id"
    root = conformance.FIXTURE_ROOT
    for request in requests:
        assert request.argv, f"{request.id} names no argument"
        assert request.id == request.directory.name, (
            f"{request.id} does not match its directory")
        for relative in request.files:
            assert (root / relative).is_file(), (
                f"{request.id} names a fixture that is not there: {relative}")


def test_the_recorded_set_covers_the_verbs_the_native_front_door_serves():
    verbs = {request.argv[0] for request in conformance.load_requests()}
    assert {"info", "status", "check"} <= verbs, (
        "the recorded set must cover every verb the native front door "
        f"serves; it covers {sorted(verbs)}")


def test_the_recorded_set_covers_a_refusal_as_well_as_a_success():
    requests = conformance.load_requests()
    kinds = {request.id for request in requests}
    assert any("missing_project" in identifier for identifier in kinds), (
        "the recorded set must record a refusal, not only successes")
    assert any("valid" in identifier for identifier in kinds), (
        "the recorded set must record a success, not only refusals")


@pytest.mark.skipif(
    conformance.native_binary() is None,
    reason="no native front door built (cargo build -p ren-cli); the "
           "conformance run needs the binary to compare against",
)
def test_the_two_backends_answer_the_recorded_requests_the_same_way(tmp_path):
    report = conformance.run_suite(
        conformance.load_requests(),
        conformance.default_backends(),
        tmp_path,
    )
    disagreements = [
        f"{comparison.request.id}: {comparison.differences}"
        for comparison in report.disagreed
    ]
    assert not disagreements, "the backends disagree: " + "; ".join(
        disagreements)
    assert len(report.unmeasured) == 0, (
        "a backend could not be run: "
        + "; ".join(
            f"{c.request.id}: {c.outcomes}" for c in report.unmeasured))
