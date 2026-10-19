"""CI-10: shadow validation over a pooled (or Tier-2) verdict.

The defect this pins: nothing re-ran the full suite after a verdict
landed, so a regression the incremental evidence missed stayed missed
with no record. After CI-10, `ren ci shadow <sha> --claim <file>`
re-gates the tree when the machine is idle and diffs the outcomes: a
green claim against a red gate files a defect naming the missed
edge(s); a matching green files nothing; and the shadow never writes
the queue, the checks, the baselines or the shared cache - verdicts
are read, never changed.

Every test scripts the checkout and the gate (context manager +
callable seams on `run_shadow`): the gate script never runs under
test. State lives under `tmp_path`; host or agent environment markers
are cleared so fingerprints and ledger paths cannot leak in.
"""

import contextlib
import json
import os

import pytest

from library.tools.local_ci import hygiene, result_cache, runner, shadow, state

SHA = "f" * 40
OTHER_SHA = "e" * 40


@pytest.fixture(autouse=True)
def _hermetic_ci_env(monkeypatch):
    monkeypatch.delenv("REN_CI_STATE", raising=False)
    monkeypatch.delenv("REN_CI_RESULT_CACHE", raising=False)
    monkeypatch.delenv("REN_CI_CACHE_VERSIONS", raising=False)
    monkeypatch.delenv("REN_CI_TIER1_BUDGET_SECONDS", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("PIPELINE_SFX_LIBRARY", raising=False)
    monkeypatch.delenv("PIPELINE_MUSIC_LIBRARY", raising=False)
    monkeypatch.delenv("PIPELINE_PROJECTS_ROOT", raising=False)


def _state_dir(tmp_path):
    path = tmp_path / "ren-ci"
    path.mkdir()
    return str(path)


def _checkout_fn(path, seen=None):
    @contextlib.contextmanager
    def run(mirror, sha, state_dir):
        assert sha == SHA
        (seen or {}).update({"mirror": mirror, "sha": sha,
                             "state_dir": state_dir})
        yield str(path)
    return run


def _gate_fn(verdict, outcomes, log_line=None, calls=None):
    def run(checkout, log_path, junit_path):
        (calls if calls is not None else []).append(
            {"checkout": checkout, "log": log_path, "junit": junit_path})
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write((log_line if log_line is not None
                          else f"FULL-SUITE GATE: {verdict}") + "\n")
        return {"gate": {"ok": verdict == "PASS", "requeue": False,
                         "verdict": verdict, "reason": "", "log": log_path},
                "outcomes": dict(outcomes)}
    return run


def _green_claim(**over):
    claim = {"verdict": "green",
             "executed": ["tests/unit/test_a.py"],
             "reused": ["tests/unit/test_b.py"]}
    claim.update(over)
    return claim


def _red_outcomes():
    return {"tests/unit/test_a.py": {"passed": 1, "failed": 0,
                                     "skipped": 0, "total": 1},
            "tests/unit/test_b.py": {"passed": 0, "failed": 2,
                                     "skipped": 0, "total": 2}}


# -- acceptance: green claim + red gate files the missed edge --------

def test_green_claim_against_a_red_gate_files_the_missed_edge(tmp_path):
    # Acceptance half one: the incremental evidence counted test_b
    # green without executing it, but the shadow gate finds it red -
    # the finding names exactly that file.
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    result = shadow.run_shadow(
        state_dir, SHA, _green_claim(),
        checkout_fn=_checkout_fn(checkout),
        gate_fn=_gate_fn("FAIL", _red_outcomes()))

    assert result["ok"] is True
    assert result["verdict"] == "FAIL"
    finding = result["finding"]
    assert finding is not None
    assert finding["sha"] == SHA
    assert finding["missed"] == ["tests/unit/test_b.py"]
    assert finding["executed_failed"] == []
    assert finding["shadow_verdict"] == "FAIL"
    stored = shadow.read_findings(state_dir)
    assert len(stored) == 1
    assert stored[0]["missed"] == ["tests/unit/test_b.py"]


def test_a_red_file_the_claim_executed_is_contradiction_not_a_miss(
        tmp_path):
    # A red file the evidence DID execute yet still claimed green is
    # contradictory evidence, not a missed edge: it lands in
    # executed_failed, never in missed.
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    claim = _green_claim(executed=["tests/unit/test_a.py",
                                   "tests/unit/test_b.py"],
                         reused=[])

    result = shadow.run_shadow(
        state_dir, SHA, claim,
        checkout_fn=_checkout_fn(checkout),
        gate_fn=_gate_fn("FAIL", _red_outcomes()))

    assert result["ok"] is True
    assert result["finding"]["missed"] == []
    assert result["finding"]["executed_failed"] == ["tests/unit/test_b.py"]


def test_matching_green_files_nothing(tmp_path):
    # Acceptance half two: claim green, gate PASS - no finding, and no
    # ledger file appears at all.
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    outcomes = {"tests/unit/test_a.py": {"passed": 1, "failed": 0,
                                         "skipped": 0, "total": 1}}

    result = shadow.run_shadow(
        state_dir, SHA, _green_claim(),
        checkout_fn=_checkout_fn(checkout),
        gate_fn=_gate_fn("PASS", outcomes))

    assert result["ok"] is True
    assert result["finding"] is None
    assert shadow.read_findings(state_dir) == []
    assert not os.path.exists(os.path.join(
        state_dir, shadow.SHADOW_FINDINGS_FILENAME))


def test_tier2_audit_keeps_full_gate_and_hypothetical_skip_counts(tmp_path):
    state_dir = _state_dir(tmp_path)
    plan = {"executed": ["tests/unit/test_a.py"],
            "reused": ["tests/unit/test_b.py"], "integration": [],
            "total_tests": 3, "skipped_tests": 2, "total_files": 2,
            "skipped_files": 1, "skipped_fraction": 2 / 3}
    record = shadow.record_tier2_audit(
        state_dir, SHA, plan, {"verdict": "PASS"},
        {"tests/unit/test_a.py": {"failed": 0, "total": 1}})
    assert record["full_gate_verdict"] == "PASS"
    assert record["skipped_tests"] == 2
    with open(os.path.join(state_dir, shadow.TIER2_AUDIT_FILENAME),
              encoding="utf-8") as handle:
        saved = json.loads(handle.readline())
    assert saved["sha"] == SHA
    assert saved["disagreement"] is False


def test_tier2_audit_files_missed_regression_when_both_verdicts_are_red(
        tmp_path):
    state_dir = _state_dir(tmp_path)
    plan = {"executed": ["tests/unit/test_a.py"],
            "reused": ["tests/unit/test_b.py"], "integration": [],
            "total_tests": 4, "skipped_tests": 1, "total_files": 2,
            "skipped_files": 1, "skipped_fraction": 0.25}
    outcomes = {
        "tests/unit/test_a.py": {"passed": 0, "failed": 1,
                                 "skipped": 0, "total": 1},
        "tests/unit/test_b.py": {"passed": 2, "failed": 1,
                                 "skipped": 0, "total": 3},
    }

    record = shadow.record_tier2_audit(
        state_dir, SHA, plan, {"verdict": "FAIL"}, outcomes)

    assert record["incremental_verdict"] == "FAIL"
    assert record["disagreement"] is False
    assert record["missed_failed_tests"] == ["tests/unit/test_b.py"]
    assert record["reuse_accuracy"] == 0.0
    assert record["defect_type"] == "test-intelligence"
    finding, = shadow.read_findings(state_dir)
    assert finding["missed"] == ["tests/unit/test_b.py"]


def test_direct_gate_audit_uses_the_pre_gate_partition(
        monkeypatch, tmp_path):
    state_dir = _state_dir(tmp_path)
    checkout = str(tmp_path / "checkout")
    outcomes = {"tests/unit/test_a.py": {"failed": 0, "total": 1}}
    plan = {"executed": ["tests/unit/test_a.py"],
            "reused": [], "integration": [],
            "total_tests": 1, "skipped_tests": 0, "total_files": 1,
            "skipped_files": 0, "skipped_fraction": 0.0}
    monkeypatch.setattr(result_cache, "parse_junit",
                        lambda _: outcomes)
    monkeypatch.setattr(runner, "tier2_incremental_plan",
                        lambda *_args, **_kwargs: plan)
    prepared = {"head": SHA, "consulted": {"expanded": [], "reused": []}}
    record = shadow.complete_direct_gate_audit(
        checkout, state_dir, prepared, "unused.xml", "PASS")
    assert record["sha"] == SHA
    assert record["full_gate_verdict"] == "PASS"
    assert record["total_tests"] == 1


# -- refusals: nothing spent, nothing filed ---------------------------

def test_a_red_claim_needs_no_shadow_and_spends_no_gate(tmp_path):
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []

    result = shadow.run_shadow(
        state_dir, SHA, {"verdict": "red", "executed": [], "reused": []},
        checkout_fn=_checkout_fn(checkout),
        gate_fn=_gate_fn("FAIL", _red_outcomes(), calls=calls))

    assert result["ok"] is False
    assert "red" in result["reason"]
    assert calls == []
    assert shadow.read_findings(state_dir) == []


def test_a_malformed_claim_is_refused_with_the_reason(tmp_path):
    state_dir = _state_dir(tmp_path)
    assert shadow.run_shadow(
        state_dir, SHA, None)["ok"] is False
    assert shadow.run_shadow(
        state_dir, SHA, {"verdict": "green"})["ok"] is False
    result = shadow.run_shadow(
        state_dir, SHA, {"verdict": "green", "executed": "tests/x.py",
                         "reused": []})
    assert result["ok"] is False
    assert "executed/reused" in result["reason"]


def test_without_a_state_dir_the_shadow_refuses_and_reaches_no_ledger(
        tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []

    result = shadow.run_shadow(
        "", SHA, _green_claim(),
        checkout_fn=_checkout_fn(checkout),
        gate_fn=_gate_fn("FAIL", _red_outcomes(), calls=calls))

    assert result["ok"] is False
    assert "no state dir" in result["reason"]
    assert calls == []


def test_a_narrowed_gate_judged_nothing_and_files_nothing(tmp_path):
    # An incomplete machine is not a missed regression: narrowed (or
    # crashed) gates file no finding.
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    for verdict in ("NARROWED PASS", "DID NOT RUN"):
        result = shadow.run_shadow(
            state_dir, SHA, _green_claim(),
            checkout_fn=_checkout_fn(checkout),
            gate_fn=_gate_fn(verdict, {}))

        assert result["ok"] is False
        assert "judged nothing" in result["reason"]
    assert shadow.read_findings(state_dir) == []


def test_a_fail_gate_without_per_file_outcomes_files_an_unnamed_defect(
        tmp_path):
    # The regression is real (green claim vs red gate) even when the
    # gate left no per-file report: the defect is filed with the edge
    # unnamed rather than dropped.
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()

    result = shadow.run_shadow(
        state_dir, SHA, _green_claim(),
        checkout_fn=_checkout_fn(checkout),
        gate_fn=_gate_fn("FAIL", {}))

    assert result["ok"] is True
    assert result["finding"]["missed"] == []
    assert result["finding"]["per_file"] is False
    assert len(shadow.read_findings(state_dir)) == 1


# -- the idle gate: never beside live verification -------------------

def test_a_busy_tier1_slot_refuses_the_shadow(tmp_path):
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []

    slots = hygiene.Slots(state_dir, jobs=1)
    with slots.claim({"kind": "tier1", "pr": 1, "head": SHA}):
        result = shadow.run_shadow(
            state_dir, OTHER_SHA, _green_claim(),
            checkout_fn=_checkout_fn(checkout),
            gate_fn=_gate_fn("FAIL", _red_outcomes(), calls=calls))

    assert result["ok"] is False
    assert "Tier-1" in result["reason"]
    assert calls == []
    assert shadow.read_findings(state_dir) == []


def test_a_gate_in_flight_refuses_the_shadow(tmp_path, monkeypatch):
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []
    monkeypatch.setattr(hygiene, "running_gate_jobs", lambda: [
        {"owner": "scripts/full_suite_gate.sh", "state": "running"}])

    result = shadow.run_shadow(
        state_dir, SHA, _green_claim(),
        checkout_fn=_checkout_fn(checkout),
        gate_fn=_gate_fn("FAIL", _red_outcomes(), calls=calls))

    assert result["ok"] is False
    assert "full-suite gate" in result["reason"]
    assert calls == []


def test_a_testing_queue_member_refuses_the_shadow(tmp_path):
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    calls = []
    state.write_queue([{"pr": 9, "head": SHA,
                        "status": state.ENTRY_TESTING}], state_dir)

    result = shadow.run_shadow(
        state_dir, OTHER_SHA, _green_claim(),
        checkout_fn=_checkout_fn(checkout),
        gate_fn=_gate_fn("FAIL", _red_outcomes(), calls=calls))

    assert result["ok"] is False
    assert "Tier-2" in result["reason"]
    assert calls == []


def test_an_idle_machine_is_admitted(tmp_path):
    state_dir = _state_dir(tmp_path)
    assert shadow.machine_idle(state_dir) == (True, "idle")


# -- shadows change no verdict, only findings ------------------------

def test_a_shadow_writes_no_queue_check_baseline_or_cache_ledger(
        tmp_path):
    # The spec's hard line: every pre-existing ledger file is
    # byte-identical after a shadow that files a defect.
    state_dir = _state_dir(tmp_path)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    state.write_queue([{"pr": 9, "head": SHA,
                        "status": state.ENTRY_QUEUED}], state_dir)
    state.record_check({"pr": 9, "head": SHA, "ok": True, "passed": 3,
                        "failed": 0}, state_dir)
    state.record_main_baseline(SHA, True, "pooled green", state_dir)
    result_cache.store(state_dir, "tests/unit/test_a.py", "canned-key",
                       1, 1, SHA, "batch")

    before = {}
    for dirpath, _dirnames, filenames in os.walk(state_dir):
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as handle:
                before[os.path.relpath(path, state_dir)] = handle.read()

    result = shadow.run_shadow(
        state_dir, SHA, _green_claim(),
        checkout_fn=_checkout_fn(checkout),
        gate_fn=_gate_fn("FAIL", _red_outcomes()))

    assert result["ok"] is True
    assert result["finding"] is not None
    after = {}
    for dirpath, _dirnames, filenames in os.walk(state_dir):
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, "rb") as handle:
                after[os.path.relpath(path, state_dir)] = handle.read()
    for rel, content in before.items():
        assert after[rel] == content, f"shadow rewrote {rel}"
    assert set(after) - set(before) == {
        shadow.SHADOW_FINDINGS_FILENAME,
        os.path.join("logs", os.path.basename(result["log"]))}


def test_findings_are_bounded_oldest_first(tmp_path):
    state_dir = _state_dir(tmp_path)
    for index in range(shadow.SHADOW_FINDINGS_KEEP + 5):
        shadow.record_finding(state_dir, {"sha": f"{index:040d}",
                                          "missed": []})

    stored = shadow.read_findings(state_dir)
    assert len(stored) == shadow.SHADOW_FINDINGS_KEEP
    assert stored[0]["sha"] == f"{5:040d}"
    assert stored[-1]["sha"] == f"{shadow.SHADOW_FINDINGS_KEEP + 4:040d}"


def test_automated_tier2_audit_records_plan_rate_and_every_disagreement(
        tmp_path):
    plan = {"executed": ["tests/contracts/test_api.py"],
            "reused": ["tests/unit/test_cached.py"],
            "integration": ["tests/contracts/test_api.py"],
            "total_files": 2, "skipped_files": 1,
            "total_tests": 4, "skipped_tests": 2,
            "skipped_fraction": 0.5}
    outcomes = {
        "tests/contracts/test_api.py": {"passed": 2, "failed": 0},
        "tests/unit/test_cached.py": {"passed": 0, "failed": 1},
    }

    first = shadow.record_tier2_audit(
        str(tmp_path), SHA, plan, {"verdict": "FAIL"}, outcomes)
    second = shadow.record_tier2_audit(
        str(tmp_path), SHA, plan, {"verdict": "FAIL"}, outcomes)

    assert first["incremental_verdict"] == "PASS"
    assert first["disagreement"] is True
    assert first["defect_type"] == "test-intelligence"
    assert first["missed_failed_tests"] == ["tests/unit/test_cached.py"]
    assert first["skipped_fraction"] == 0.5
    assert first["total_tests"] == 4
    assert first["skipped_tests"] == 2
    assert second["disagreement"] is True
    with open(tmp_path / shadow.TIER2_AUDIT_FILENAME,
              encoding="utf-8") as handle:
        assert len(handle.readlines()) == 2
    assert len(shadow.read_findings(str(tmp_path))) == 2


# -- the read-only junit handoff on run_gate --------------------------

def test_run_gate_junit_out_sees_outcomes_without_filing(
        tmp_path, monkeypatch):
    # CI-10's reader: the gate script's JUnit handoff lands at the
    # given path and the shared cache gains no rows.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    state_dir = _state_dir(tmp_path)
    junit_out = str(tmp_path / "shadow.junit.xml")
    seen = {}

    def fake_run_logged(command, cwd, log_path, timeout, env=None):
        seen["env"] = dict(env or {})
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("FULL-SUITE GATE: FAIL\n")
        target = (env or {}).get(runner.TIER2_JUNIT_OUT_ENV, "")
        assert target == junit_out
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(
                '<?xml version="1.0" encoding="utf-8"?>'
                "<testsuite><testcase classname=\"t\" name=\"one\" "
                "file=\"tests/unit/test_a.py\">"
                "<failure message=\"boom\"/></testcase></testsuite>")
        return {"exit": 1, "timed_out": False, "group_clean": True,
                "elapsed": 1.0}

    monkeypatch.setattr(runner, "_run_logged", fake_run_logged)

    gate = runner.run_gate(str(checkout), str(tmp_path / "gate.log"),
                           timeout=60, junit_out=junit_out)

    assert gate["verdict"] == "FAIL"
    assert seen["env"][runner.TIER2_JUNIT_OUT_ENV] == junit_out
    outcomes = result_cache.parse_junit(junit_out)
    assert list(outcomes) == ["tests/unit/test_a.py"]
    assert outcomes["tests/unit/test_a.py"]["failed"] == 1
    assert not os.path.exists(result_cache.cache_path(state_dir))


def test_run_gate_cache_hook_still_owns_the_handoff(
        tmp_path, monkeypatch):
    # With both hooks given, the shared-cache hook wins: one run never
    # files through two identities, and the shadow path is not written.
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    state_dir = _state_dir(tmp_path)
    junit_out = str(tmp_path / "shadow.junit.xml")
    seen = {}

    def fake_run_logged(command, cwd, log_path, timeout, env=None):
        seen["env"] = dict(env or {})
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("FULL-SUITE GATE: PASS\n")
        return {"exit": 0, "timed_out": False, "group_clean": True,
                "elapsed": 1.0}

    monkeypatch.setattr(runner, "_run_logged", fake_run_logged)
    monkeypatch.setattr(
        runner, "tier2_consult",
        lambda *args, **kwargs: {"reused": [], "total": 0})
    monkeypatch.setattr(
        runner, "_tier2_record",
        lambda *args, **kwargs: {"stored": [], "outcomes": {}})

    gate = runner.run_gate(
        str(checkout), str(tmp_path / "gate.log"), timeout=60,
        cache={"state_dir": state_dir, "source": "batch", "head": SHA,
               "interpreter": "python-test-3.12"},
        junit_out=junit_out)

    assert gate["verdict"] == "PASS"
    assert seen["env"][runner.TIER2_JUNIT_OUT_ENV].endswith(".junit.xml")
    assert seen["env"][runner.TIER2_JUNIT_OUT_ENV] != junit_out
    assert not os.path.exists(junit_out)


# -- the thin CLI -----------------------------------------------------

def test_shadow_command_refuses_an_unreadable_claim(tmp_path, capsys):
    from argparse import Namespace

    from library.tools.local_ci import __main__ as cli
    args = Namespace(state=_state_dir(tmp_path), sha=SHA,
                     claim=str(tmp_path / "no-such.json"))

    assert cli.cmd_shadow(args) == 2
    assert "cannot read claim" in capsys.readouterr().out


def test_shadow_command_reports_a_confirmed_green(tmp_path, capsys,
                                                  monkeypatch):
    from argparse import Namespace

    from library.tools.local_ci import __main__ as cli
    claim_path = tmp_path / "claim.json"
    claim_path.write_text(json.dumps(_green_claim()), encoding="utf-8")
    monkeypatch.setattr(
        shadow, "run_shadow",
        lambda *args, **kwargs: {"ok": True, "sha": SHA, "verdict": "PASS",
                                 "finding": None, "log": "logs/x.log"})
    args = Namespace(state=_state_dir(tmp_path), sha=SHA,
                     claim=str(claim_path))

    assert cli.cmd_shadow(args) == 0
    assert "files nothing" in capsys.readouterr().out


def test_shadow_command_reports_a_filed_defect(tmp_path, capsys,
                                               monkeypatch):
    from argparse import Namespace

    from library.tools.local_ci import __main__ as cli
    claim_path = tmp_path / "claim.json"
    claim_path.write_text(json.dumps(_green_claim()), encoding="utf-8")
    monkeypatch.setattr(
        shadow, "run_shadow",
        lambda *args, **kwargs: {"ok": True, "sha": SHA, "verdict": "FAIL",
                                 "finding": {"missed": [
                                     "tests/unit/test_b.py"],
                                     "per_file": True},
                                 "log": "logs/x.log"})
    args = Namespace(state=_state_dir(tmp_path), sha=SHA,
                     claim=str(claim_path))

    assert cli.cmd_shadow(args) == 0
    out = capsys.readouterr().out
    assert "DEFECT" in out
    assert "tests/unit/test_b.py" in out


def test_shadow_command_propagates_a_refusal(tmp_path, capsys,
                                             monkeypatch):
    from argparse import Namespace

    from library.tools.local_ci import __main__ as cli
    claim_path = tmp_path / "claim.json"
    claim_path.write_text(json.dumps(_green_claim()), encoding="utf-8")
    monkeypatch.setattr(
        shadow, "run_shadow",
        lambda *args, **kwargs: {"ok": False,
                                 "reason": "shadow waits: Tier-1 holds it"})
    args = Namespace(state=_state_dir(tmp_path), sha=SHA,
                     claim=str(claim_path))

    assert cli.cmd_shadow(args) == 2
    assert "shadow waits" in capsys.readouterr().out


def test_status_names_shadow_findings(tmp_path, capsys, monkeypatch):
    from argparse import Namespace

    from library.tools.local_ci import __main__ as cli
    from library.tools.local_ci import load, service
    state_dir = _state_dir(tmp_path)
    shadow.record_finding(state_dir, {"sha": SHA,
                                      "missed": ["tests/unit/test_b.py"],
                                      "shadow_verdict": "FAIL"})
    plan = {"tier1_jobs": 1, "pytest_n": 2, "window": 600,
            "headroom": 0.0, "heavy": []}
    monkeypatch.setattr(load, "measure", dict)
    monkeypatch.setattr(load, "targets", lambda _m: plan)
    monkeypatch.setattr(load, "describe", lambda _p: "jobs=1")
    monkeypatch.setattr(service, "installed", lambda: False)
    monkeypatch.setattr(service, "plist_path",
                        lambda: "/tmp/com.ren.ci.plist")

    assert cli.cmd_status(Namespace(state=state_dir)) == 0
    out = capsys.readouterr().out
    assert "shadow findings: 1 recorded" in out
    assert SHA[:12] in out
    assert "tests/unit/test_b.py" in out
