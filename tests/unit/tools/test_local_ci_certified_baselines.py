"""CI-07: certified baselines over the main-baseline cache.

The defect this pins: the baseline cache stored bare green verdicts
with no inventory, so any later incremental verdict (CI-08) would have
nothing to reuse against - and a NARROWED machine state (incomplete
environment, not a passing tree) could bless a sha as silently as a
PASS. After CI-07, certifying a sha records its inventory + per-file
verification keys + fingerprints, a NARROWED verdict is refused with
the reason, and a judged-but-uncertified verdict never reads as a
baseline.

Every test builds its checkout and ledger under `tmp_path` with
machine-dependent key components pinned, so the suite is deterministic
on any machine and never reaches the real `~/.ren-ci` ledger.
"""

import os

import pytest

from library.tools.local_ci import queue, result_cache, runner, state

PINNED = {"interpreter": "python-test-3.12", "native": "native-test",
          "env": "env-test", "config_text": "config-test"}


@pytest.fixture(autouse=True)
def _hermetic_ci_env(monkeypatch):
    # Host or agent environment must not leak into baselines under
    # test: fingerprints are pinned below, and the cache switches plus
    # the env-gating markers they derive from are cleared outright.
    monkeypatch.delenv("REN_CI_RESULT_CACHE", raising=False)
    monkeypatch.delenv("REN_CI_CACHE_VERSIONS", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("PIPELINE_SFX_LIBRARY", raising=False)
    monkeypatch.delenv("PIPELINE_MUSIC_LIBRARY", raising=False)
    monkeypatch.delenv("PIPELINE_PROJECTS_ROOT", raising=False)

    monkeypatch.setattr(result_cache, "pytest_interpreter_version",
                        lambda *args, **kwargs: PINNED["interpreter"])
    monkeypatch.setattr(result_cache, "native_fingerprint",
                        lambda *args, **kwargs: PINNED["native"])
    monkeypatch.setattr(result_cache, "config_fingerprint",
                        lambda *args, **kwargs: PINNED["config_text"])
    monkeypatch.setattr(result_cache, "env_fingerprint",
                        lambda *args, **kwargs: PINNED["env"])
    monkeypatch.setattr(runner, "_tier2_interpreter",
                        lambda checkout: PINNED["interpreter"])


PLAIN_TEST = "VALUE = 1\n\ndef test_value():\n    assert VALUE == 1\n"
OTHER_TEST = "OTHER = 2\n\ndef test_other():\n    assert OTHER == 2\n"


def _checkout(tmp_path):
    root = tmp_path / "checkout"
    (root / "tests" / "unit").mkdir(parents=True)
    (root / "tests" / "unit" / "test_widget.py").write_text(
        PLAIN_TEST, encoding="utf-8")
    (root / "tests" / "unit" / "test_other.py").write_text(
        OTHER_TEST, encoding="utf-8")
    (root / "pyproject.toml").write_text("[tool.pytest]\n",
                                         encoding="utf-8")
    return root


def _state_dir(tmp_path):
    path = tmp_path / "ren-ci"
    path.mkdir()
    return str(path)


def _key(checkout, rel):
    return result_cache.compute_key(str(checkout), rel, **PINNED)


def _gate_log(tmp_path, line="FULL-SUITE GATE: PASS  |  unit: 2 passed"):
    log = tmp_path / "gate.log"
    log.write_text(f"{line}\n", encoding="utf-8")
    return str(log)


def test_certifying_a_pass_verdict_records_inventory_keys_and_env(
        tmp_path):
    # Acceptance half one: certifying a fixture sha records its test
    # inventory, the per-file verification keys under the locally
    # computed values, the fingerprints, and the gate verdict line.
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    log = _gate_log(tmp_path)
    gate = {"ok": True, "requeue": False, "verdict": "PASS",
            "reason": "", "log": log}

    result = queue.certify_main_baseline(
        str(checkout), state_dir, "fixture-sha", gate)

    assert result["ok"] is True
    record = state.certified_baseline("fixture-sha", state_dir)
    assert record is not None
    assert sorted(record["inventory"]) == [
        "tests/unit/test_other.py", "tests/unit/test_widget.py"]
    assert record["keys"] == {
        rel: _key(checkout, rel) for rel in record["inventory"]}
    assert record["env"] == PINNED["env"]
    assert record["interpreter"] == PINNED["interpreter"]
    assert record["native"] == PINNED["native"]
    assert record["config"] == PINNED["config_text"]
    assert record["verdict"] == "PASS"
    assert record["verdict_line"] == \
        "FULL-SUITE GATE: PASS  |  unit: 2 passed"
    assert record["source"] == "batch"
    # The generalization keeps old readers working: a certified sha
    # still answers the green-verdict question judge_main asks.
    assert state.main_baseline_green(
        "fixture-sha", state_dir)["ok"] is True


def test_certifying_a_narrowed_verdict_is_refused_with_the_reason(
        tmp_path):
    # Acceptance half two: a NARROWED PASS names an incomplete machine,
    # never a passing tree - it stores nothing and says why.
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    log = _gate_log(tmp_path, "FULL-SUITE GATE: NARROWED PASS  |  no ffmpeg")
    gate = {"ok": False, "requeue": True, "verdict": "NARROWED PASS",
            "reason": "full gate narrowed pass", "log": log}

    result = queue.certify_main_baseline(
        str(checkout), state_dir, "fixture-sha", gate)

    assert result["ok"] is False
    assert "NARROWED PASS" in result["reason"]
    assert "FULL-SUITE GATE: PASS" in result["reason"]
    assert state.certified_baseline("fixture-sha", state_dir) is None
    assert "fixture-sha" not in state.read_main_baseline(state_dir)


def test_certifying_a_fail_verdict_is_refused_and_stores_nothing(
        tmp_path):
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    log = _gate_log(tmp_path, "FULL-SUITE GATE: FAIL  |  unit: 1 failed")
    gate = {"ok": False, "requeue": False, "verdict": "FAIL",
            "reason": "full gate FAIL", "log": log}

    result = queue.certify_main_baseline(
        str(checkout), state_dir, "fixture-sha", gate)

    assert result["ok"] is False
    assert "PASS" in result["reason"]
    assert state.certified_baseline("fixture-sha", state_dir) is None
    assert "fixture-sha" not in state.read_main_baseline(state_dir)


def test_a_judged_verdict_without_inventory_is_not_a_baseline(tmp_path):
    # The pre-CI-07 shape the cache holds on main: a bare green verdict
    # (judged, e.g. from a world with no checkout in hand) answers the
    # red-main question but never reads as a certified baseline.
    state_dir = _state_dir(tmp_path)
    state.record_main_baseline("judged-sha", True, "", state_dir)

    assert state.main_baseline_green("judged-sha", state_dir)["ok"] is True
    assert state.certified_baseline("judged-sha", state_dir) is None


def test_a_later_verdict_never_strips_a_certification(tmp_path):
    # judge_main records its verdict AFTER test_main certifies (the
    # checkout is gone by then): the verdict write must keep the
    # baseline payload - the same sha is the same tree.
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    gate = {"ok": True, "requeue": False, "verdict": "PASS",
            "reason": "", "log": _gate_log(tmp_path)}
    assert queue.certify_main_baseline(
        str(checkout), state_dir, "fixture-sha", gate)["ok"] is True

    state.record_main_baseline(
        "fixture-sha", True, "re-judged green", state_dir)

    record = state.certified_baseline("fixture-sha", state_dir)
    assert record is not None
    assert sorted(record["inventory"]) == [
        "tests/unit/test_other.py", "tests/unit/test_widget.py"]
    assert record["keys"] == {
        rel: _key(checkout, rel) for rel in record["inventory"]}
    assert record["reason"] == "re-judged green"


def test_certify_without_a_state_dir_refuses_and_reaches_no_ledger(
        tmp_path):
    # Scripted worlds (state_dir="") stay hermetic: certification
    # refuses before any fingerprint or ledger write.
    checkout = _checkout(tmp_path)
    gate = {"ok": True, "requeue": False, "verdict": "PASS",
            "reason": "", "log": _gate_log(tmp_path)}

    result = queue.certify_main_baseline(
        str(checkout), "", "fixture-sha", gate)

    assert result["ok"] is False
    assert "no state dir" in result["reason"]


def test_a_green_main_judgement_certifies_through_test_main(
        tmp_path, monkeypatch):
    # The real caller: World.test_main files the baseline from the
    # checkout that just passed, while the verdict itself is unchanged.
    from contextlib import contextmanager

    from library.tools.local_ci import hygiene
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)

    def run_gate(checkout_path, log_path, timeout=None, cache=None):
        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("FULL-SUITE GATE: PASS  |  unit: 2 passed\n")
        return {"ok": True, "requeue": False, "verdict": "PASS",
                "reason": "", "log": log_path}

    monkeypatch.setattr(hygiene, "fetch_base", lambda *args: None)

    @contextmanager
    def fake_checkout(mirror, base, state):
        yield str(checkout)

    monkeypatch.setattr(hygiene, "disposable_checkout", fake_checkout)
    monkeypatch.setattr(runner, "run_gate", run_gate)

    world = queue.World(mirror="mirror", state_dir=state_dir)
    verdict = world.test_main("fixture-sha")

    assert verdict.ok is True
    assert verdict.requeue is False
    record = state.certified_baseline("fixture-sha", state_dir)
    assert record is not None
    assert sorted(record["inventory"]) == [
        "tests/unit/test_other.py", "tests/unit/test_widget.py"]
    assert record["keys"] == {
        rel: _key(checkout, rel) for rel in record["inventory"]}


def test_only_a_full_pass_is_a_certifiable_verdict():
    # The merge predicate and the certification predicate are one:
    # NARROWED PASS / DID NOT RUN / FAIL / missing never certify.
    assert runner.certifiable_verdict("PASS") is True
    assert runner.certifiable_verdict("NARROWED PASS") is False
    assert runner.certifiable_verdict("DID NOT RUN") is False
    assert runner.certifiable_verdict("FAIL") is False
    assert runner.certifiable_verdict("") is False


def test_the_gate_verdict_line_reads_the_log_verbatim(tmp_path):
    log = _gate_log(tmp_path, "FULL-SUITE GATE: PASS  |  unit: 2 passed")
    assert runner.gate_verdict_line(log) == \
        "FULL-SUITE GATE: PASS  |  unit: 2 passed"
    missing = str(tmp_path / "no-such.log")
    assert runner.gate_verdict_line(missing) == ""
    empty = tmp_path / "empty.log"
    empty.write_text("no verdict here\n", encoding="utf-8")
    assert runner.gate_verdict_line(str(empty)) == ""


def test_status_names_which_baselines_are_certified(tmp_path, capsys):
    # A judged-green run never reads as a certified one on `ren ci status`.
    import argparse

    from library.tools.local_ci import __main__ as cli
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    gate = {"ok": True, "requeue": False, "verdict": "PASS",
            "reason": "", "log": _gate_log(tmp_path)}
    assert queue.certify_main_baseline(
        str(checkout), state_dir, "certified-sha", gate)["ok"] is True
    state.record_main_baseline("judged-sha", True, "", state_dir)

    args = argparse.Namespace(state=state_dir)
    assert cli.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "certified-sha"[:12] in out and "certified 2 files" in out
    judged_line = next(
        line for line in out.splitlines() if "judged-sha"[:12] in line)
    assert "certified" not in judged_line
