"""CI-02: the shared verification cache across tiers and sources.

Tier-2 full-gate runs and worker-local runs read and write the same
rows: Tier-2 files per-file verdicts from its gate JUnit report after
executing everything (sharing evidence is not skipping execution until
CI-08), and the pooled verify step consults first and reports
`reused N/M` before executing. Each row records its source
(local / pr / batch / periodic) without changing the key.

Every test below builds its checkout and cache under `tmp_path` with
machine-dependent key components pinned, so the suite is deterministic
on any machine and never reaches the real `~/.ren-ci` ledger.
"""

import os
from contextlib import contextmanager

import pytest

from library.tools.local_ci import queue, result_cache, runner

PINNED = {"interpreter": "python-test-3.12", "native": "native-test",
          "env": "env-test", "config_text": "config-test"}


@pytest.fixture(autouse=True)
def _hermetic_cache_env(monkeypatch):
    # Host or agent environment must not leak into the cache under
    # test: a kill-switch or version bound set outside would flip
    # reuse and eviction assertions.
    monkeypatch.delenv("REN_CI_RESULT_CACHE", raising=False)
    monkeypatch.delenv("REN_CI_CACHE_VERSIONS", raising=False)

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


def _pin_fingerprints(monkeypatch):
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


def _junit(path, cases):
    lines = ['<?xml version="1.0" encoding="utf-8"?>', "<testsuites>",
             '<testsuite name="pytest">']
    for rel, status in cases:
        name = rel.replace("/", ".")[:-len(".py")]
        open_tag = (f'<testcase classname="{name}" name="t" file="{rel}">')
        if status == "failed":
            lines += [open_tag, "<failure message=\"boom\"/>",
                      "</testcase>"]
        elif status == "skipped":
            lines += [open_tag, "<skipped/>", "</testcase>"]
        else:
            lines.append(open_tag + "</testcase>")
    lines += ["</testsuite>", "</testsuites>"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _gate_run(cases, verdict="FULL-SUITE GATE: PASS  |  unit: 1 passed"):
    """A scripted gate: writes the verdict log plus the JUnit report
    the real gate script hands off through REN_CI_JUNIT_OUT."""
    calls = []

    def run_logged(command, cwd, log_path, timeout, env=None):
        calls.append((command, dict(env or {})))
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"{verdict}\n")
        out = (env or {}).get(runner.TIER2_JUNIT_OUT_ENV, "")
        if out:
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            _junit(__import__("pathlib").Path(out), cases)
        return {"timed_out": False, "group_clean": True}

    return calls, run_logged


def _key(checkout, rel):
    return result_cache.compute_key(str(checkout), rel, **PINNED)


def _lookup(state_dir, checkout, rel):
    builder = result_cache.KeyBuilder(str(checkout))
    return result_cache.lookup(
        state_dir, str(checkout), rel, builder, **PINNED)


def test_tier2_write_files_a_verdict_under_the_locally_computed_key(
        tmp_path, monkeypatch):
    # Acceptance half one: a fixture gate log with a passing file yields
    # a stored row whose key equals the locally computed key for the
    # same tree - Tier-2 and local runs address the same row.
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    _pin_fingerprints(monkeypatch)
    rel = "tests/unit/test_widget.py"
    calls, run_logged = _gate_run([(rel, "passed")])
    monkeypatch.setattr(runner, "_run_logged", run_logged)

    result = runner.run_gate(
        str(checkout), str(tmp_path / "gate.log"), timeout=1200,
        cache={"state_dir": state_dir, "source": "batch"})

    assert result["ok"] is True
    assert calls and calls[0][0][1] == "scripts/full_suite_gate.sh"
    hit = _lookup(state_dir, checkout, rel)
    assert hit is not None
    assert hit["key"] == _key(checkout, rel)
    assert hit["source"] == "batch"


def test_tier2_gate_runs_despite_a_cache_hit_and_says_so(
        tmp_path, monkeypatch):
    # Test security (CI-02(c)): sharing evidence is not skipping
    # execution. A stored PASS under the same key still runs the full
    # gate on the exact tree, and the log line cites reuse + execution.
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    _pin_fingerprints(monkeypatch)
    rel = "tests/unit/test_widget.py"
    result_cache.store(
        state_dir, rel, _key(checkout, rel), 1, 1, head="pr-head",
        source="pr")
    calls, run_logged = _gate_run([(rel, "passed")])
    monkeypatch.setattr(runner, "_run_logged", run_logged)

    result = runner.run_gate(
        str(checkout), str(tmp_path / "gate.log"), timeout=1200,
        cache={"state_dir": state_dir, "source": "batch"})

    assert result["ok"] is True
    assert calls, "the gate must execute even on a full cache hit"
    log_text = (tmp_path / "gate.log").read_text(encoding="utf-8")
    assert "reused 1/2" in log_text
    assert "execut" in log_text


def test_tier2_failure_evicts_but_unreported_files_keep_their_rows(
        tmp_path, monkeypatch):
    # Fail closed per file: a red file evicts its stale PASS, while a
    # file the gate never ran (resolve drivers, policy-excluded
    # real_model) keeps whatever verdict it had - absence from the
    # report is not a verdict.
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    _pin_fingerprints(monkeypatch)
    red, quiet = "tests/unit/test_widget.py", "tests/unit/test_other.py"
    result_cache.store(
        state_dir, red, _key(checkout, red), 1, 1, source="pr")
    result_cache.store(
        state_dir, quiet, _key(checkout, quiet), 1, 1, source="pr")
    _calls, run_logged = _gate_run(
        [(red, "failed")],
        verdict="FULL-SUITE GATE: FAIL  |  unit: 1 failed")
    monkeypatch.setattr(runner, "_run_logged", run_logged)

    result = runner.run_gate(
        str(checkout), str(tmp_path / "gate.log"), timeout=1200,
        cache={"state_dir": state_dir, "source": "batch"})

    assert result["ok"] is False and result["requeue"] is False
    assert _lookup(state_dir, checkout, red) is None
    assert _lookup(state_dir, checkout, quiet) is not None


def test_run_gate_without_the_hook_files_no_verdict_anywhere(
        tmp_path, monkeypatch):
    # The main behavior CI-02 builds on: a Tier-2 gate run with no
    # cache hook files no verdict anywhere (default preserves it).
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    _pin_fingerprints(monkeypatch)
    calls, run_logged = _gate_run([("tests/unit/test_widget.py", "passed")])
    monkeypatch.setattr(runner, "_run_logged", run_logged)

    result = runner.run_gate(
        str(checkout), str(tmp_path / "gate.log"), timeout=1200)

    assert result["ok"] is True
    assert calls
    assert not os.path.exists(
        os.path.join(state_dir, result_cache.CACHE_FILENAME))


def test_pooled_verify_reports_reuse_before_executing(
        tmp_path, monkeypatch, capsys):
    # Acceptance half two: the pooled verify step consults first and
    # reports `reused N/M`, then executes the gate anyway.
    from library.tools.local_ci import hygiene
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    _pin_fingerprints(monkeypatch)
    first = "tests/unit/test_widget.py"
    result_cache.store(
        state_dir, first, _key(checkout, first), 1, 1, source="pr")
    events = []

    real_consult = runner.tier2_consult

    def consult(checkout_path, state, interpreter, builder=None):
        events.append("consult")
        return real_consult(checkout_path, state, interpreter, builder)

    def run_gate(checkout_path, log, timeout=None, cache=None):
        events.append("gate")
        assert cache is not None and cache["source"] == "batch"
        assert cache["state_dir"] == state_dir
        return {"ok": True, "reason": "", "requeue": False}

    monkeypatch.setattr(hygiene, "fetch_base", lambda *args: None)

    @contextmanager
    def fake_checkout(mirror, base, state):
        yield str(checkout)

    monkeypatch.setattr(hygiene, "disposable_checkout", fake_checkout)
    monkeypatch.setattr(queue, "_stack_checkout",
                        lambda *args: {"ok": True, "reason": "",
                                       "conflict": ""})
    monkeypatch.setattr(runner, "tier2_consult", consult)
    monkeypatch.setattr(runner, "run_gate", run_gate)

    world = queue.World(mirror="mirror", state_dir=state_dir)
    verdict = world.test_stack(
        "base", [{"pr": 1, "head": "head-1", "status": "queued",
                  "reason": ""}])

    assert verdict.ok is True
    assert events == ["consult", "gate"]
    out = capsys.readouterr().out
    assert "reused 1/2" in out


def test_incremental_shadow_keeps_contracts_in_execution_set(tmp_path):
    checkout = _checkout(tmp_path)
    contracts = checkout / "tests" / "contracts"
    contracts.mkdir()
    (contracts / "test_contract.py").write_text(
        "def test_contract(): pass\n", encoding="utf-8")
    scenarios = checkout / "tests" / "scenarios"
    scenarios.mkdir()
    (scenarios / "test_flow.py").write_text(
        "def test_flow(): pass\n", encoding="utf-8")
    consulted = {"expanded": ["tests/unit/test_widget.py",
                              "tests/unit/test_other.py",
                              "tests/contracts/test_contract.py",
                              "tests/scenarios/test_flow.py"],
                 "reused": [{"path": "tests/unit/test_widget.py"}],
                 "total": 4}
    outcomes = {
        "tests/unit/test_widget.py": {"total": 4},
        "tests/unit/test_other.py": {"total": 2},
        "tests/contracts/test_contract.py": {"total": 3},
        "tests/scenarios/test_flow.py": {"total": 1},
    }

    plan = runner.tier2_incremental_plan(
        str(checkout), consulted, observed_outcomes=outcomes)

    assert plan["executed"] == ["tests/contracts/test_contract.py",
                                 "tests/scenarios/test_flow.py",
                                 "tests/unit/test_other.py"]
    assert plan["reused"] == ["tests/unit/test_widget.py"]
    assert plan["integration"] == ["tests/contracts/test_contract.py",
                                    "tests/scenarios/test_flow.py"]
    assert plan["total_tests"] == 10
    assert plan["skipped_tests"] == 4
    assert plan["skipped_fraction"] == pytest.approx(0.4)


def test_main_baseline_verify_consults_and_files_as_batch(
        tmp_path, monkeypatch, capsys):
    # The main-alone baseline judge shares the hook: consult first,
    # execute always, file as `batch` evidence.
    from library.tools.local_ci import hygiene
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    _pin_fingerprints(monkeypatch)
    seen = {}

    def run_gate(checkout_path, log, timeout=None, cache=None):
        seen.update(cache or {})
        return {"ok": True, "reason": "", "requeue": False}

    monkeypatch.setattr(hygiene, "fetch_base", lambda *args: None)

    @contextmanager
    def fake_checkout(mirror, base, state):
        yield str(checkout)

    monkeypatch.setattr(hygiene, "disposable_checkout", fake_checkout)
    monkeypatch.setattr(runner, "run_gate", run_gate)

    world = queue.World(mirror="mirror", state_dir=state_dir)
    verdict = world.test_main("base")

    assert verdict.ok is True
    assert seen.get("source") == "batch"
    assert seen.get("state_dir") == state_dir
    assert "reused 0/2" in capsys.readouterr().out


def test_source_is_recorded_without_changing_the_key(tmp_path):
    # CI-02(b): the source rides along as metadata - the same tree
    # computes the same key whatever the source, and each row names
    # the executor that produced it.
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    rel = "tests/unit/test_widget.py"
    key = result_cache.compute_key(str(checkout), rel, **PINNED)
    assert result_cache.compute_key(str(checkout), rel, **PINNED) == key
    result_cache.store(state_dir, rel, key, 1, 1, source="pr")
    assert _lookup(state_dir, checkout, rel)["source"] == "pr"
    result_cache.store(state_dir, rel, key, 1, 1, source="batch")
    hit = _lookup(state_dir, checkout, rel)
    assert hit["key"] == key
    assert hit["source"] == "batch"
    assert set(result_cache.SOURCES) == {
        "local", "pr", "batch", "periodic"}


def test_ci01_database_gains_source_without_losing_verdicts(tmp_path):
    # Migration: a CI-01-era database (multiversion rows, no source
    # column) keeps every verdict; old rows read back with an
    # unrecorded source.
    import sqlite3
    checkout, state_dir = _checkout(tmp_path), _state_dir(tmp_path)
    rel = "tests/unit/test_widget.py"
    key = result_cache.compute_key(str(checkout), rel, **PINNED)
    db = os.path.join(state_dir, result_cache.CACHE_FILENAME)
    connection = sqlite3.connect(db)
    try:
        connection.execute(
            "CREATE TABLE tier1_results ("
            " test_path TEXT NOT NULL,"
            " key TEXT NOT NULL,"
            " passed INTEGER NOT NULL,"
            " total INTEGER NOT NULL,"
            " head TEXT NOT NULL DEFAULT '',"
            " recorded_at REAL NOT NULL,"
            " last_used REAL NOT NULL,"
            " PRIMARY KEY (test_path, key))")
        connection.execute(
            "INSERT INTO tier1_results"
            " (test_path, key, passed, total, head, recorded_at, last_used)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rel, key, 1, 1, "head-old", 1.0, 1.0))
        connection.commit()
    finally:
        connection.close()
    hit = _lookup(state_dir, checkout, rel)
    assert hit is not None and hit["passed"] == 1
    assert hit["head"] == "head-old"
    assert hit["source"] == ""
