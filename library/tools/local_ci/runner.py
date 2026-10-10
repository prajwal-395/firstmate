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
import signal
import subprocess
import time

from library.tools.local_ci import config, history, hygiene

# Pytest's terminal summary is surrounded by `=` characters. Anchor the
# parse to that line: an unanchored regex with optional groups matched the
# empty string before the counts and turned a real run into 0/0.
_SUMMARY_LINE = re.compile(r"^=+\s*(.*?)\s*=+$")
_SUMMARY_COUNTS = re.compile(
    r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b")
_GATE_VERDICT = re.compile(r"^(NARROWED PASS|DID NOT RUN|PASS|FAIL)\b")


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
            "-v", "--tb=short", "-o", "faulthandler_timeout=300",
            "-n", str(max(1, min(2, workers)))]
    for driven in config.RESOLVE_DRIVING:
        argv.extend(["--ignore", driven])
    return argv


def _process_group_exists(pgid: int) -> bool:
    """Whether a run's isolated process group still has members."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_process_group(process: subprocess.Popen,
                             grace_seconds: float = 5.0) -> bool:
    """Stop the pytest process and every child it spawned, boundedly.

    `subprocess.run(timeout=...)` only guarantees termination of its
    direct child. pytest-xdist workers share this run's process group,
    so SIGTERM/SIGKILL the group and verify the group is gone before a
    slot can be reused. The group id is the exact pid this runner
    started; it is not a name or pattern match.
    """
    pgid = process.pid
    if not _process_group_exists(pgid):
        return True
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError:
        return False

    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass

    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)

    if _process_group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            return False

    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        return False
    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _process_group_exists(pgid)


def _run_logged(command: list, cwd: str, log_path: str,
                timeout: int) -> dict:
    """Run a command in a new session with a hard timeout and log file.

    A timed-out pytest must end in a failure result, not leave xdist
    workers holding a slot or turn the status back into pending. The
    separate session gives cleanup an exact process-group id.
    """
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    started = time.monotonic()
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            try:
                process = subprocess.Popen(
                    command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                    encoding="utf-8", start_new_session=True)
            except OSError as exc:
                log.write(f"ren-ci could not start command: {exc}\n")
                return {"exit": 127, "timed_out": False,
                        "group_clean": True,
                        "elapsed": time.monotonic() - started}

            try:
                exit_code = process.wait(timeout=timeout)
                timed_out = False
                group_clean = _terminate_process_group(process)
            except subprocess.TimeoutExpired:
                timed_out = True
                group_clean = _terminate_process_group(process)
                exit_code = process.returncode
                log.write(
                    f"\nREN-CI TIMEOUT after {timeout}s; "
                    f"process_group_clean={group_clean}\n")
    except OSError as exc:
        return {"exit": 127, "timed_out": False, "group_clean": True,
                "elapsed": time.monotonic() - started,
                "error": str(exc)}
    return {"exit": exit_code if exit_code is not None else -1,
            "timed_out": timed_out, "group_clean": group_clean,
            "elapsed": time.monotonic() - started}


def _pytest_python() -> str:
    """The ladder-resolved interpreter, or ambient python3 when the
    ladder refuses (a checkout without the ML stack still runs the
    non-model selection; real_model is excluded by marker either way)."""
    try:
        from library.tools.shared_environment import python_interpreter
        python, _ = python_interpreter(os.getcwd())
        if python and os.path.exists(python):
            return python
    except (ImportError, OSError, RuntimeError):
        pass
    return "python3"


def _pytest_paths(paths: list, checkout: str) -> list:
    """Keep selected files that exist, but let pytest discover conftest files.

    Passing a conftest.py as an explicit test path can import it under the
    same module name as a nested conftest.py already loaded during collection.
    Pytest loads conftest files from the test tree itself, so they are never
    test arguments.
    """
    return [
        path for path in paths
        if os.path.basename(path) != "conftest.py"
        and os.path.exists(os.path.join(checkout, path))
    ]


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
    paths = _pytest_paths(paths, checkout)
    if not paths:
        raise hygiene.HygieneRefused(
            "selection vanished against the checkout - every selected "
            "path is absent, so this checkout is anomalous; retrying "
            "next pass on a fresh fetch")
    hygiene.bind_renderer_dependencies(checkout)
    broker_before = hygiene.broker_serving()
    result = _run_logged(
        [_pytest_python(), *pytest_argv(paths, workers)],
        checkout, log_path, timeout)
    timed_out = result["timed_out"]
    passed, failed, executed = _read_summary(log_path)
    timeout_diagnostic = _timeout_diagnostic(log_path) if timed_out else ""
    no_tests_executed = executed == 0
    reason = "no tests executed" if no_tests_executed else ""
    # Exit code and execution count both matter. A zero exit without an
    # executed test (including a missing summary or an all-skipped run)
    # is not evidence of a green check.
    if (result["exit"] != 0 or not result["group_clean"]
            or no_tests_executed):
        ok = False
        if failed == 0 and passed == 0:
            failed = 1
    else:
        ok = failed == 0
    broker_after = hygiene.broker_serving()
    leaked = broker_after and not broker_before
    return {"passed": passed, "failed": 0 if ok else max(failed, 1),
            "ok": ok and not leaked and result["group_clean"],
            "exit": result["exit"], "elapsed": result["elapsed"],
            "timed_out": timed_out,
            "timeout_diagnostic": timeout_diagnostic,
            "process_group_leaked": not result["group_clean"],
            "broker_leaked": leaked, "reason": reason, "log": log_path}


def run_static(checkout: str, changed: list) -> dict:
    """The folded-in fast checks, before any test: the enforcing ruff
    gate, the compile sweep over changed files, the AGENTS.md size
    gate. What `.github/workflows/ci.yml` ran FIRST so a lint failure
    billed one minute instead of thirteen - here it saves a whole
    checkout's pytest run instead. Returns {ok, reason}.

    ruff missing is a FAILED check, not a skip: the gate config is the
    build's one enforcing gate (AGENTS.md 9), and an unmeasured gate
    that reports green is worse than no gate. Remedy names itself.

    Only files PRESENT in the checkout compile: a stale-base diff
    names files the PR's tree never had (added on main after the
    branch), and py_compile on a missing path fails the PR for
    somebody else's file.
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
    py_files = [p for p in changed if p.endswith(".py")
                and os.path.isfile(os.path.join(checkout, p))]
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
             timeout: int | None = None) -> dict:
    """Tier-2: the FULL suite on the exact tree that would land.

    The timeout SCALES from the measured full-suite wall time times
    the load factor (history.default_tier2_timeout), the same way
    Tier-1 is budgeted from measured durations: an unloaded 700s
    suite must not die at a fixed 1200s under agent load at 98%
    complete. An explicit timeout still wins (tests pin it).

    Runs the checkout's OWN full_suite_gate.sh (the code under test
    gates itself - a stale gate script cannot bless a new tree) and
    reads its one verdict line. Only PASS is green: NARROWED PASS
    names missing environment, not a passing tree, so it requeues
    (requeue=True) rather than kicking - the PR is not the culprit, the
    machine is incomplete. FAIL is red. Anything else (no verdict
    line, timeout, crash) is DID NOT RUN: requeue, never merge blind.
    A TIMED-OUT gate judged nothing: the batch requeues as no verdict
    and no member is kicked - resolve_batch requeues the whole cluster
    on requeue=True without naming a culprit.
    """
    if timeout is None:
        timeout = history.default_tier2_timeout()
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    result = _run_logged(
        ["bash", "scripts/full_suite_gate.sh", "--exclude-real-model",
         "-n", "2"], checkout, log_path, timeout)
    timed_out = result["timed_out"]
    verdict = _read_verdict(log_path)
    if verdict == "PASS" and result["group_clean"]:
        return {"ok": True, "requeue": False, "verdict": verdict,
                "reason": "", "timed_out": False, "log": log_path}
    if (verdict in ("NARROWED PASS", "DID NOT RUN") or timed_out
            or not result["group_clean"]):
        return {"ok": False, "requeue": True, "verdict": verdict,
                "reason": f"full gate {verdict.lower()}"
                          + (" (timed out)" if timed_out else "")
                          + (" (process group did not exit)"
                             if not result["group_clean"] else "")
                          + f", log {log_path}",
                "timed_out": timed_out, "log": log_path}
    return {"ok": False, "requeue": False, "verdict": verdict,
            "reason": f"full gate FAIL, log {log_path}",
            "timed_out": False, "log": log_path}


def _read_verdict(log_path: str) -> str:
    """Read the gate's status token, ignoring its optional summary columns."""
    try:
        with open(log_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return "DID NOT RUN"
    for line in text.splitlines():
        if line.startswith("FULL-SUITE GATE:"):
            detail = line.split("FULL-SUITE GATE:", 1)[1].strip()
            match = _GATE_VERDICT.match(detail)
            return match.group(1) if match else detail.partition("|")[0].strip()
    return "DID NOT RUN"


def _read_summary(log_path: str) -> tuple[int, int, int]:
    """Return passed, failed, and executed counts from pytest's summary.

    Errors count as failures; skipped and deselected tests do not count as
    executed. Missing or unrecognized summaries return zeros, which the
    caller treats as a failed run.
    """
    try:
        with open(log_path, encoding="utf-8") as handle:
            tail = handle.read()[-4000:]
    except OSError:
        return 0, 0, 0
    summary = None
    for line in tail.splitlines():
        found = _SUMMARY_LINE.fullmatch(line.strip())
        if found:
            summary = found.group(1)
    if summary is None or "no tests ran" in summary:
        return 0, 0, 0

    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0,
              "xfailed": 0, "xpassed": 0}
    for count, outcome in _SUMMARY_COUNTS.findall(summary):
        key = "errors" if outcome in ("error", "errors") else outcome
        counts[key] += int(count)
    passed = counts["passed"]
    failed = counts["failed"] + counts["errors"]
    executed = (passed + failed + counts["xfailed"] + counts["xpassed"])
    return passed, failed, executed


def _timeout_diagnostic(log_path: str) -> str:
    """Best available breadcrumb for the test that held the run open.

    `faulthandler_timeout` writes Python frames for a stuck test; verbose
    pytest output also names completed/current items. Keep the last
    relevant line in the ledger/status description so a timeout can be
    diagnosed without attaching to the next run.
    """
    try:
        with open(log_path, encoding="utf-8") as handle:
            lines = handle.read()[-32_000:].splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("File \"tests/") and ", in " in stripped:
            return stripped[:220]
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("tests/") and "::" in stripped:
            return stripped[:220]
    return ""
