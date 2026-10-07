"""Run one PR check: pytest the selection in a disposable checkout.

One run is: hygiene gates (disk, gate deference) -> checkout exists ->
broker snapshot -> pytest -> broker-leak verdict -> record. The pytest
itself runs under `bin/vep`'s ladder-resolved interpreter when
available (the ML stack lives there, not in the ambient python3), and
always with the resolve_live + real_model markers off: nothing the
service runs may touch live Resolve.

The report line is the brief's contract:
`PR <url> head <full sha> - <N> passed, <M> failed, selection
<command and paths>`. Every surface (status description, `ren ci
status`, the done summary) prints this shape so a supervisor can grep it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

from library.tools.local_ci import config, hygiene, selection

# `18 passed, 3 skipped` / `2 failed, 18 passed` - the tail pytest
# prints. Parsed, never trusted for the verdict alone: the verdict is
# the EXIT CODE (0 pass, anything else fail), and the counts decorate
# it. A collection error prints no summary line at all, which reads as
# 0/0 FAILED - fail-closed, never "no tests ran, ship it".
_SUMMARY = re.compile(r"(?:(\d+) failed)?,?\s*(?:(\d+) passed)?")


def format_report(url: str, head: str, passed: int, failed: int,
                  command: str) -> str:
    """The brief's report line, greppable on every surface."""
    return (f"PR {url} head {head} - {passed} passed, {failed} failed, "
            f"selection {command}")


def pytest_argv(paths: list, workers: int = 2) -> list:
    """The pytest invocation for a selection, markers and ignores fixed.

    workers follows the machine (load.targets): one job may fan out to
    -n 2, three jobs run -n 1 each. Never above 2 (spec 3's per-run
    bound stands - the parallelism the refinement adds is ACROSS PRs).
    """
    argv = ["-m", "pytest", *paths, "-m", config.PYTEST_MARKERS,
            "-q", "--tb=short", "-n", str(max(1, min(2, workers)))]
    for driven in config.RESOLVE_DRIVING:
        argv.extend(["--ignore", driven])
    return argv


def _pytest_python() -> str:
    """The ladder-resolved interpreter, or ambient python3 when the
    ladder refuses (a checkout without the ML stack still runs the
    non-model selection; real_model is excluded by marker either way)."""
    try:
        from library.tools.shared_environment import python_interpreter
        python, _ = python_interpreter(os.getcwd())
        if python and os.path.exists(python):
            return python
    except Exception:
        pass
    return "python3"


def run(paths: list, checkout: str, log_path: str,
        timeout: int = config.RUN_TIMEOUT_SECONDS,
        workers: int = 2) -> dict:
    """Run pytest over paths in checkout, log to log_path. Returns the
    result: {passed, failed, exit, elapsed, timed_out}. Never raises on
    test failure - a red suite is a RESULT, not an exception. Raises
    HygieneRefused only when the run itself could not start."""
    if not hygiene.disk_ok(checkout):
        free = hygiene.disk_free(checkout)
        raise hygiene.HygieneRefused(
            f"disk floor: {free // 1024 // 1024}MB free, "
            f"need {config.DISK_FLOOR_BYTES // 1024 // 1024}MB - refusing")
    if hygiene.gate_running():
        raise hygiene.HygieneRefused(
            "a full-suite gate holds the machine - deferred, not skipped")
    broker_before = hygiene.broker_serving()
    started = time.time()
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.run(
                [_pytest_python(), *pytest_argv(paths, workers)],
                cwd=checkout, stdout=log, stderr=subprocess.STDOUT,
                encoding="utf-8", check=False, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc = None
        timed_out = True
    elapsed = time.time() - started
    passed, failed = _read_summary(log_path)
    # Exit code is the verdict; counts decorate it. A nonzero exit with
    # an unreadable summary (collection error, timeout, crash) still
    # fails: 0/0 is a failed run, never "no tests ran, ship it".
    if proc is None or proc.returncode != 0:
        ok = False
        if failed == 0 and passed == 0:
            failed = 1
    else:
        ok = failed == 0
    broker_after = hygiene.broker_serving()
    leaked = broker_after and not broker_before
    return {"passed": passed, "failed": 0 if ok else max(failed, 1),
            "ok": ok and not leaked, "exit": -1 if proc is None else proc.returncode,
            "elapsed": elapsed, "timed_out": timed_out,
            "broker_leaked": leaked, "log": log_path}


def run_static(checkout: str, changed: list) -> dict:
    """The folded-in fast checks, before any test: the enforcing ruff
    gate, the compile sweep over changed files, the AGENTS.md size
    gate. What `.github/workflows/ci.yml` ran FIRST so a lint failure
    billed one minute instead of thirteen - here it saves a whole
    checkout's pytest run instead. Returns {ok, reason}.

    ruff missing is a FAILED check, not a skip: the gate config is the
    build's one enforcing gate (AGENTS.md 9), and an unmeasured gate
    that reports green is worse than no gate. Remedy names itself.
    """
    if shutil.which("ruff") is None:
        return {"ok": False,
                "reason": "ruff not on PATH - install it (pip install ruff)"}
    gate = subprocess.run(
        ["ruff", "check", "library/", "tests/", "--config",
         "ruff-ci-gate.toml", "--output-format=concise"],
        cwd=checkout, capture_output=True, encoding="utf-8",
        check=False, timeout=300)
    if gate.returncode != 0:
        return {"ok": False,
                "reason": f"ruff gate failed:\n{gate.stdout.strip()[:2000]}"}
    py_files = [p for p in changed if p.endswith(".py")]
    if py_files:
        compiled = subprocess.run(
            ["python3", "-m", "py_compile", *py_files],
            cwd=checkout, capture_output=True, encoding="utf-8",
            check=False, timeout=120)
        if compiled.returncode != 0:
            return {"ok": False,
                    "reason": "compile check failed:\n"
                              f"{compiled.stderr.strip()[:2000]}"}
    size = subprocess.run(
        ["python3", "scripts/check_agents_md_size.py"],
        cwd=checkout, capture_output=True, encoding="utf-8",
        check=False, timeout=60)
    if size.returncode != 0:
        return {"ok": False,
                "reason": f"AGENTS.md size gate failed:\n{size.stdout.strip()[:2000]}"}
    return {"ok": True, "reason": ""}


def run_gate(checkout: str, log_path: str,
             timeout: int = config.TIER2_TIMEOUT_SECONDS) -> dict:
    """Tier-2: the FULL suite on the exact tree that would land.

    Runs the checkout's OWN full_suite_gate.sh (the code under test
    gates itself - a stale gate script cannot bless a new tree) and
    reads its one verdict line. Only PASS is green: NARROWED PASS
    names missing environment, not a passing tree, so it requeues
    (requeue=True) rather than kicking - the PR is not the culprit, the
    machine is incomplete. FAIL is red. Anything else (no verdict
    line, timeout, crash) is DID NOT RUN: requeue, never merge blind.
    """
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            proc = subprocess.run(
                ["bash", "scripts/full_suite_gate.sh"],
                cwd=checkout, stdout=log, stderr=subprocess.STDOUT,
                encoding="utf-8", check=False, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        proc = None
        timed_out = True
    verdict = _read_verdict(log_path)
    if verdict == "PASS":
        return {"ok": True, "requeue": False, "verdict": verdict,
                "reason": "", "timed_out": False, "log": log_path}
    if verdict in ("NARROWED PASS", "DID NOT RUN") or timed_out:
        return {"ok": False, "requeue": True, "verdict": verdict,
                "reason": f"full gate {verdict.lower()}"
                          + (" (timed out)" if timed_out else "")
                          + f", log {log_path}",
                "timed_out": timed_out, "log": log_path}
    return {"ok": False, "requeue": False, "verdict": verdict,
            "reason": f"full gate FAIL, log {log_path}",
            "timed_out": False, "log": log_path}


def _read_verdict(log_path: str) -> str:
    """The gate's one verdict line (`FULL-SUITE GATE: <verdict>`), or
    DID NOT RUN when the run never printed one."""
    try:
        with open(log_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return "DID NOT RUN"
    for line in text.splitlines():
        if line.startswith("FULL-SUITE GATE:"):
            return line.split("FULL-SUITE GATE:", 1)[1].strip()
    return "DID NOT RUN"


def _read_summary(log_path: str) -> tuple:
    """Passed/failed counts off pytest's tail line, or (0, 0) when the
    run never got that far (collection error, timeout, crash)."""
    try:
        with open(log_path, encoding="utf-8") as handle:
            tail = handle.read()[-4000:]
    except OSError:
        return 0, 0
    match = None
    for line in tail.splitlines():
        found = _SUMMARY.search(line)
        if found and ("passed" in line or "failed" in line):
            match = found
    if match is None:
        return 0, 0
    failed = int(match.group(1) or 0)
    passed = int(match.group(2) or 0)
    return passed, failed
