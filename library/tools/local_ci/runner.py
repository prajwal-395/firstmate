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
import threading
import time
import xml.etree.ElementTree as ET

from library.tools.local_ci import config, history, hygiene

# Gate verdicts that may CERTIFY a main baseline (CI-07): only a full
# PASS. NARROWED PASS names missing environment, not a passing tree -
# run_gate already refuses it for merges below, and certification uses
# this same predicate so an incomplete machine can never bless a sha.
CERTIFIABLE_VERDICTS = ("PASS",)


def certifiable_verdict(verdict: str) -> bool:
    """Whether a gate verdict may certify a main baseline.

    Only `PASS` qualifies: NARROWED PASS / DID NOT RUN / FAIL (or a
    missing verdict line) refuse with the reason at the caller.
    """
    return verdict in CERTIFIABLE_VERDICTS


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


def pytest_argv(paths: list, workers: int = 2, checkout: str = "") -> list:
    """The pytest invocation for a selection, markers and ignores fixed.

    workers follows the machine (load.targets): one job may fan out to
    the measured worker count. REN_CI_PYTEST_WORKERS can override the
    dispatch default for a machine or run; there is no hard worker cap.
    The Tier-2-only files ride as `--ignore` so the conditional
    `tests/contracts` mandatory entry cannot re-admit the audit through
    directory expansion; `checkout` (the tree under test) filters the
    ignores to files it has, because `--ignore` on a path a PR deleted
    fails the run for somebody else's deletion. Without a checkout
    every ignore is passed, as before.
    """
    try:
        workers = int(os.environ.get("REN_CI_PYTEST_WORKERS", workers))
    except (TypeError, ValueError):
        raise ValueError("REN_CI_PYTEST_WORKERS must be a positive integer")
    if workers < 1:
        raise ValueError("REN_CI_PYTEST_WORKERS must be a positive integer")
    argv = ["-m", "pytest", *paths, "-m", config.PYTEST_MARKERS,
            "-v", "--tb=short", "-o", "faulthandler_timeout=300",
            "-n", str(workers)]
    for driven in config.RESOLVE_DRIVING:
        argv.extend(["--ignore", driven])
    for owned in config.TIER1_TIER2_ONLY:
        if checkout and not os.path.exists(os.path.join(checkout, owned)):
            continue
        argv.extend(["--ignore", owned])
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


# Process groups this process started and has not yet reaped, by pgid
# (which equals the direct child's pid: every run uses
# start_new_session=True). Tier-1 workers share this set across threads,
# and the daemon's SIGTERM handler drains it, so a launchd restart
# cannot leave a gate running under the old code beside the new
# daemon's batch (2026-10-10: an hour of old-code Tier-2 under
# heavy_work_lock while queue entries stayed testing). Only exact pgids
# from this registry are ever signalled - never a name or pattern.
_ACTIVE_GROUPS: set[int] = set()
_ACTIVE_GROUPS_LOCK = threading.Lock()


def _register_active_pgid(pgid: int) -> None:
    with _ACTIVE_GROUPS_LOCK:
        _ACTIVE_GROUPS.add(int(pgid))


def _unregister_active_pgid(pgid: int) -> None:
    with _ACTIVE_GROUPS_LOCK:
        _ACTIVE_GROUPS.discard(int(pgid))


def active_run_pgids() -> list[int]:
    """Exact process-group ids this process started and still owns."""
    with _ACTIVE_GROUPS_LOCK:
        return sorted(_ACTIVE_GROUPS)


def terminate_process_group_by_pgid(pgid: int,
                                    grace_seconds: float = 5.0) -> bool:
    """Stop one tracked process group by its exact pgid, boundedly.

    SIGTERM first, then SIGKILL past the grace. Returns whether the
    group is gone. Never matches by name: the caller names the exact
    pgid this runner started.
    """
    pgid = int(pgid)
    if not _process_group_exists(pgid):
        return True
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if _process_group_exists(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError:
            return False
    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _process_group_exists(pgid)


def terminate_active_runs(grace_seconds: float = 5.0) -> dict[int, bool]:
    """Stop every process group this process started. Returns pgid->gone.

    The daemon's shutdown path: a SIGTERM or launchd restart must not
    leave a gate process group running under the old code beside the
    replacement daemon's batch.
    """
    results: dict[int, bool] = {}
    for pgid in active_run_pgids():
        results[pgid] = terminate_process_group_by_pgid(
            pgid, grace_seconds=grace_seconds)
    return results


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
                timeout: int, env: dict | None = None) -> dict:
    """Run a command in a new session with a hard timeout and log file.

    A timed-out pytest must end in a failure result, not leave xdist
    workers holding a slot or turn the status back into pending. The
    separate session gives cleanup an exact process-group id. The
    group is registered while it runs so the daemon's shutdown handler
    can terminate exactly the groups it started on SIGTERM. `env`, when
    given, replaces the inherited environment for the child only - the
    parent process is untouched, so concurrent runs never see it.
    """
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    started = time.monotonic()
    try:
        with open(log_path, "w", encoding="utf-8") as log:
            try:
                process = subprocess.Popen(
                    command, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                    encoding="utf-8", start_new_session=True, env=env)
            except OSError as exc:
                log.write(f"ren-ci could not start command: {exc}\n")
                return {"exit": 127, "timed_out": False,
                        "group_clean": True,
                        "elapsed": time.monotonic() - started}

            _register_active_pgid(process.pid)
            try:
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
            finally:
                _unregister_active_pgid(process.pid)
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
        workers: int = 2, junit_path: str = "") -> dict:
    """Run pytest over paths in checkout, log to log_path. Returns the
    result: {passed, failed, exit, elapsed, timed_out}. Never raises on
    test failure - a red suite is a RESULT, not an exception. Raises
    HygieneRefused only when the run itself could not start.

    junit_path, when given, adds `--junitxml` so the Tier-1 result cache
    can attribute per-file verdicts. Tier-2 never passes one: the full
    gate writes its own reports, and the shared-cache hook in `run_gate`
    collects them through `REN_CI_JUNIT_OUT` instead."""
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
    command = [_pytest_python(), *pytest_argv(paths, workers, checkout)]
    if junit_path:
        command += ["--junitxml", junit_path]
    result = _run_logged(command, checkout, log_path, timeout)
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


# The gate script's handoff for the shared verification cache: when
# set, `scripts/full_suite_gate.sh` copies its main-phase JUnit report
# to this path before its EXIT trap cleans the private report dir. An
# env var (not a flag) so checkouts whose gate script predates the
# handoff simply ignore it instead of failing on an unknown option.
TIER2_JUNIT_OUT_ENV = "REN_CI_JUNIT_OUT"


def _tier2_interpreter(checkout: str) -> str:
    """The interpreter string Tier-2 keys its evidence under.

    Resolved through the same ladder as Tier-1 (`runner.run` passes
    the ladder python as `pytest_python`), but against the checkout
    under test rather than the daemon's cwd - so a Tier-2 verdict and
    a local verdict on the same tree compute the same key and share
    rows across sources. Falls back to the ambient interpreter string
    when the ladder refuses, exactly like `pytest_interpreter_version`.
    """
    from library.tools.local_ci import result_cache
    try:
        from library.tools.shared_environment import python_interpreter
        python, _ = python_interpreter(os.path.abspath(checkout))
        if python and os.path.exists(python):
            return result_cache.pytest_interpreter_version(python)
    except (ImportError, OSError, RuntimeError):
        pass
    return result_cache.pytest_interpreter_version("")


def tier2_inventory(checkout: str) -> list:
    """Every test file the full gate may execute on this checkout.

    The gate itself decides lanes and ignores (resolve drivers,
    real-model policy); files it never runs simply never appear in
    its report and are never filed - so consulting the whole `tests/`
    tree is honest, and recording only ever touches reported files.
    """
    from library.tools.local_ci import result_cache
    return result_cache.expand_selection(checkout, ["tests"])


def tier2_consult(checkout: str, state_dir: str, interpreter: str,
                  builder=None) -> dict:
    """Consult the shared cache for the Tier-2 inventory, read-only.

    Returns the `partition` result (`reused N/M` reads off it). The
    shadow planner derives its hypothetical execution set from this
    partition and the full-gate report; every eligible test still runs.
    """
    from library.tools.local_ci import result_cache
    own = builder or result_cache.KeyBuilder(checkout)
    return result_cache.partition(
        state_dir, checkout, tier2_inventory(checkout), builder=own,
        interpreter=interpreter)


def tier2_incremental_plan(checkout: str, consulted: dict,
                          observed_outcomes=None) -> dict:
    """Return the shadow-only CI-08 plan for a Tier-2 tree.

    Contract, scenario, and tooling tests are the conservative
    integration set and always run, even when a content key hits.
    Tooling tests inspect repository-wide behavior and subprocess
    targets, so they are not reusable without an explicit complete
    dependency declaration. The full gate remains the sole merge
    authority; this plan only measures files an incremental run could
    have omitted.
    """
    from library.tools.local_ci import result_cache
    observed = (set(consulted["expanded"]) if observed_outcomes is None
                else set(observed_outcomes))
    integration = set(result_cache.expand_selection(
        checkout, ["tests/contracts", "tests/scenarios",
                   "tests/tooling"])) & observed
    reused = {item["path"] for item in consulted["reused"]} & observed
    run = sorted((observed - reused) | integration)
    skipped = sorted(observed - set(run))
    total_files = len(observed)
    skipped_files = len(skipped)
    total_tests = sum(int(outcome["total"])
                      for outcome in (observed_outcomes or {}).values())
    skipped_tests = sum(int((observed_outcomes or {})[path]["total"])
                        for path in skipped)
    return {"executed": run, "reused": skipped,
            "integration": sorted(integration & set(consulted["expanded"])),
            "total_files": total_files, "skipped_files": skipped_files,
            "total_tests": total_tests, "skipped_tests": skipped_tests,
            "skipped_fraction": (skipped_tests / total_tests
                                 if total_tests else 0.0)}


def _tier2_record(checkout: str, state_dir: str, junit_path: str,
                  interpreter: str, head: str, source: str,
                  builder=None, verdict: str = "") -> dict:
    """File the gate's per-file verdicts, write-only on the cache.

    Only files the report names are touched: a file the gate never
    ran (resolve drivers, policy-excluded real_model) keeps whatever
    verdict it had. A missing or unparseable report files nothing -
    the gate run that produced no machine-readable result leaves no
    reusable evidence. Read-only on the report, write-only on the
    cache: the report file itself is never modified.
    """
    from library.tools.local_ci import result_cache
    own = builder or result_cache.KeyBuilder(checkout)
    outcomes = result_cache.parse_junit(junit_path)
    executed = sorted(
        rel for rel in outcomes
        if os.path.isfile(os.path.join(checkout, rel)))
    return result_cache.record_from_junit(
        state_dir, checkout, junit_path, executed, head=head,
        builder=own, source=source, interpreter=interpreter,
        execution_verdict=verdict)


def _tier2_cache_line(consulted: dict, stored: int, outcomes: int) -> str:
    """The gate log's account of what the shared cache said and did.

    Always cites reuse AND execution: the full gate runs every file as
    the shadow comparator, so a hit is not an actual skip yet.
    """
    reused = len(consulted.get("reused", []))
    total = consulted.get("total", 0)
    line = (f"ren-ci shared cache: reused {reused}/{total} test files"
            f" from prior evidence; full gate executed for the shadow "
            f"audit (incremental mode remains shadow-only)")
    if outcomes == 0:
        line += " - no gate JUnit report to file, stored nothing"
    else:
        line += f" - filed {stored} verdict(s) from the gate report"
    return line + "\n"


def run_gate(checkout: str, log_path: str,
             timeout: int | None = None,
             cache: dict | None = None,
             junit_out: str = "") -> dict:
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

    `cache` is the shared-verification-cache hook (CI-02): None (the
    default) keeps main behavior - execute, judge, file nothing. A
    dict `{"state_dir": ..., "source": ..., "head": ...}` (plus an
    optional prebuilt `"builder"` and `"consulted"` partition from the
    caller) consults FIRST, executes ALWAYS, and files the gate's
    per-file verdicts AFTER. The separate CI-08 shadow planner reports
    the files it would skip, but the cache hook itself never skips. A falsy
    `state_dir` disables the hook, so direct callers without a state
    dir stay hermetic and file nothing.

    `junit_out` is the read-only twin of that hook for callers that
    must see per-file outcomes without filing any (CI-10 shadow
    validation): when given AND no cache hook is active, the gate
    script's JUnit handoff is pointed at this path through the same
    `REN_CI_JUNIT_OUT` env var, and nothing is written to the shared
    cache. With a cache hook active the hook owns the handoff path
    and `junit_out` is ignored, so one run never files through two
    identities.

    The returned dict always carries `failed_tests`: the test files
    with any failure/error in the gate's JUnit report (CI-12's "which
    tests failed" read). Empty on green, on requeue, and whenever no
    report exists - an empty list falls back to the legacy full-gate
    bisect, never to an empty blame set.
    """
    if timeout is None:
        timeout = history.default_tier2_timeout()
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    hook = cache if cache and cache.get("state_dir") else None
    consulted: dict | None = None
    interpreter = ""
    junit_path = ""
    child_env: dict | None = None
    if hook is not None:
        from library.tools.local_ci import result_cache
        if not hook.get("head"):
            tree = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=checkout,
                capture_output=True, encoding="utf-8", check=False,
                timeout=30)
            if tree.returncode == 0:
                hook["head"] = tree.stdout.strip()
        interpreter = hook.get("interpreter") or _tier2_interpreter(checkout)
        builder = hook.get("builder") or result_cache.KeyBuilder(checkout)
        hook["builder"] = builder
        consulted = hook.get("consulted")
        if consulted is None:
            consulted = tier2_consult(
                checkout, hook["state_dir"], interpreter, builder)
            hook["consulted"] = consulted
        junit_path = log_path + ".junit.xml"
        child_env = {**os.environ, TIER2_JUNIT_OUT_ENV: junit_path,
                     "REN_CI_SHADOW_AUDIT": "queue"}
    elif junit_out:
        child_env = {**os.environ, TIER2_JUNIT_OUT_ENV: junit_out}
    try:
        tier2_workers = int(os.environ.get("REN_CI_PYTEST_WORKERS", "2"))
    except (TypeError, ValueError):
        raise ValueError("REN_CI_PYTEST_WORKERS must be a positive integer")
    if tier2_workers < 1:
        raise ValueError("REN_CI_PYTEST_WORKERS must be a positive integer")
    result = _run_logged(
        ["bash", "scripts/full_suite_gate.sh", "--exclude-real-model",
         "-n", str(tier2_workers)], checkout, log_path, timeout,
        env=child_env)
    timed_out = result["timed_out"]
    verdict = _read_verdict(log_path)
    outcomes = result_cache.parse_junit(junit_path) if hook is not None else {}
    if hook is not None and consulted is not None:
        from library.tools.local_ci import result_cache
        summary = _tier2_record(
            checkout, hook["state_dir"], junit_path, interpreter,
            hook.get("head", ""), hook.get("source", ""),
            hook.get("builder"), verdict=verdict)
        if not summary["outcomes"] and hook.get("head"):
            result_cache.record_execution(
                hook["state_dir"], hook["head"],
                hook.get("source", "local"), {}, verdict=verdict,
                log=log_path)
        try:
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(_tier2_cache_line(
                    consulted, len(summary["stored"]),
                    len(summary["outcomes"])))
        except OSError:
            pass
    if certifiable_verdict(verdict) and result["group_clean"]:
        return {"ok": True, "requeue": False, "verdict": verdict,
                "reason": "", "timed_out": False, "log": log_path,
                "failed_tests": [], "outcomes": outcomes}
    if (verdict in ("NARROWED PASS", "DID NOT RUN") or timed_out
            or not result["group_clean"]):
        return {"ok": False, "requeue": True, "verdict": verdict,
                "reason": f"full gate {verdict.lower()}"
                          + (" (timed out)" if timed_out else "")
                          + (" (process group did not exit)"
                             if not result["group_clean"] else "")
                          + f", log {log_path}",
                "timed_out": timed_out, "log": log_path,
                "failed_tests": [], "outcomes": outcomes}
    return {"ok": False, "requeue": False, "verdict": verdict,
            "reason": f"full gate FAIL, log {log_path}",
            "timed_out": False, "log": log_path,
            "failed_tests": failed_tests_from_junit(junit_path),
            "outcomes": outcomes}


def failed_tests_from_junit(junit_path: str) -> list:
    """Test files with any failure/error in a pytest `--junitxml` report.

    The resolver's "which tests failed" read (CI-12): the
    `render_qa`-style failing-test list, wired into failure diagnosis.
    Missing or unparseable XML is [] - the caller falls back to the
    legacy full-gate bisect, never to an empty blame set.
    """
    if not junit_path:
        return []
    from library.tools.local_ci import result_cache
    outcomes = result_cache.parse_junit(junit_path)
    return sorted(
        rel for rel, outcome in outcomes.items()
        if (outcome.get("failed") or 0) > 0)


def failed_test_ids(junit_path: str) -> list[str]:
    """Return exact pytest node ids from a Tier-1 JUnit report."""
    if not junit_path:
        return []
    try:
        root = ET.parse(junit_path).getroot()
    except (OSError, ET.ParseError):
        return []
    failed = []
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        name = case.get("name", "")
        classname = case.get("classname", "")
        if name:
            failed.append(f"{classname}::{name}" if classname else name)
    return sorted(set(failed))


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


def gate_verdict_line(log_path: str) -> str:
    """The gate's own one-line verdict, verbatim for baseline records.

    Returns the full `FULL-SUITE GATE: ...` line (including any summary
    columns the gate prints), or "" when the log carries none - a timed
    out or crashed gate judged nothing, so there is no line to certify.
    """
    try:
        with open(log_path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("FULL-SUITE GATE:"):
            return line.strip()
    return ""


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
