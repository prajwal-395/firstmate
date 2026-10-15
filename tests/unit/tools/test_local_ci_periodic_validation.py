"""CI-14 periodic validation has an explicit trigger and evidence trail."""

from argparse import Namespace

import pytest

from library.tools.local_ci import __main__ as local_ci_cli
from library.tools.local_ci import result_cache


@pytest.fixture(autouse=True)
def _clear_machine_cache_switches(monkeypatch):
    monkeypatch.delenv("REN_CI_RESULT_CACHE", raising=False)
    monkeypatch.delenv("REN_CI_CACHE_VERSIONS", raising=False)


def test_periodic_policy_and_tooling_are_documented():
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    docs = (root / "docs" / "LOCAL_CI.md").read_text(encoding="utf-8")
    assert "| Periodic idle window | Complete offline suite and runtime dependency-index refresh" in docs
    assert "Resolve-live remains a separate controlled qualification lane" in docs
    assert "Verdict: not yet" in docs
    assert "cargo-nextest" in docs and "Pants" in docs


def test_evidence_command_queries_exact_sha(tmp_path, capsys):
    sha = "fixture-sha"
    result_cache.record_execution(
        str(tmp_path), sha, "periodic",
        {"tests/unit/test_fixture.py": {
            "passed": 4, "failed": 0, "skipped": 0, "total": 4}},
        log="periodic.log")

    assert local_ci_cli.cmd_evidence(
        Namespace(state=str(tmp_path), sha=sha)) == 0
    output = capsys.readouterr().out
    assert f"verification history for {sha}: 1 execution(s)" in output
    assert "periodic PASS 4 passed, 0 failed, 0 skipped; 1 files" in output


def test_validate_refreshes_coverage_only_after_full_pass(
        monkeypatch, tmp_path, capsys):
    from library.tools.local_ci import config, coverage_index, runner

    monkeypatch.setattr(config, "state_dir", lambda: str(tmp_path / "state"))
    monkeypatch.setattr(runner, "run_gate", lambda *args, **kwargs: {
        "verdict": "FAIL", "reason": "fixture failure", "ok": False,
        "requeue": False})
    collected = []
    monkeypatch.setattr(coverage_index, "collect",
                        lambda *args, **kwargs: collected.append(args))

    result = local_ci_cli.cmd_validate(Namespace(
        checkout=str(__import__("pathlib").Path(__file__).resolve().parents[3]),
        state=str(tmp_path / "state")))

    assert result == 1
    assert collected == []
    assert "periodic full-suite verdict: FAIL" in capsys.readouterr().out
