"""Deterministic verdict rules plus a few real-script wiring checks."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

from scripts.full_suite_gate_verdict import calculate_verdict, summarize

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_GATE = REPO_ROOT / "scripts" / "full_suite_gate.sh"


def test_summarize_fails_closed_for_absent_truncated_and_suite_less_reports(tmp_path):
    assert summarize(tmp_path / "missing.xml", 0) == (
        "ran-nothing unreadable JUnit report (FileNotFoundError)"
    )
    truncated = tmp_path / "truncated.xml"
    truncated.write_text("<testsuites><testsuite", encoding="utf-8")
    assert summarize(truncated, 0) == "ran-nothing unreadable JUnit report (ParseError)"
    no_suites = tmp_path / "no-suites.xml"
    no_suites.write_text("<testsuites />", encoding="utf-8")
    assert summarize(no_suites, 0) == "ran-nothing JUnit report contains no test suite"


@pytest.mark.parametrize(
    ("xml", "exit_code", "expected"),
    [
        (
            '<testsuite tests="0" skipped="0" failures="0" errors="0"/>',
            0,
            "ran-nothing pytest collected 0 tests",
        ),
        (
            '<testsuite tests="2" skipped="2" failures="0" errors="0"/>',
            0,
            "ran-nothing all 2 selected tests skipped, so nothing was measured",
        ),
        (
            '<testsuite tests="3" skipped="1" failures="1" errors="1"/>',
            0,
            "bad 2 executed, 1 failed, 1 errored",
        ),
        (
            '<testsuite tests="2" skipped="0" failures="0" errors="0"/>',
            1,
            "bad 2 executed with no recorded failure but pytest exited 1",
        ),
        (
            '<testsuite tests="2" skipped="0" failures="0" errors="0"/>',
            143,
            "crashed 2 passed then NATIVE CRASH (SIGTERM) - check ~/Library/Logs/DiagnosticReports/ for the faulting library",
        ),
        (
            '<testsuite tests="3" skipped="1" failures="0" errors="0"/>',
            0,
            "ok 2 passed, 1 skipped",
        ),
    ],
)
def test_summarize_report_counts_and_pytest_exit_codes(
    tmp_path, xml, exit_code, expected
):
    report = tmp_path / "report.xml"
    report.write_text(xml, encoding="utf-8")
    assert summarize(report, exit_code) == expected


@pytest.mark.parametrize(
    ("main_state", "real_state", "narrowed", "excluded", "expected", "exit_code"),
    [
        ("ok", "ok", 0, False, "PASS", 0),
        ("ok", "ok", 1, False, "NARROWED PASS", 1),
        ("ok", "ran-nothing", 0, True, "PASS", 0),
        ("ok", "ran-nothing", 0, False, "DID NOT RUN", 1),
        ("bad", "ok", 0, False, "FAIL", 1),
        ("crashed", "ok", 0, False, "FAIL", 1),
        ("ran-nothing", "ok", 0, False, "DID NOT RUN", 1),
        ("ok", "bad", 0, False, "FAIL", 1),
        ("ok", "crashed", 0, False, "FAIL", 1),
        ("ok", "ran-nothing", 1, False, "NARROWED PASS", 1),
    ],
)
def test_final_verdict_state_rules(
    main_state,
    real_state,
    narrowed,
    excluded,
    expected,
    exit_code,
):
    result = calculate_verdict(
        main_state,
        "main detail",
        real_state,
        "real detail",
        narrowed,
        excluded,
    )
    assert result.line.startswith(f"FULL-SUITE GATE: {expected}  |  ")
    assert result.exit_code == exit_code


def test_final_verdict_preserves_exact_output_for_excluded_real_model():
    result = calculate_verdict(
        "ok",
        "2 passed, 0 skipped",
        "ran-nothing",
        "ignored",
        0,
        True,
    )
    assert result == type(result)(
        "FULL-SUITE GATE: PASS  |  unit+scenario: 2 passed, 0 skipped"
        "  |  real_model excluded by local CI marker policy",
        0,
    )


def _write_shim(tmp_path: Path, script: str) -> Path:
    script = script.replace("exec python3", f'exec "{sys.executable}"')
    shim = tmp_path / "pytest-shim.sh"
    shim.write_text(script, encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _run_gate(shim: Path, *args: str) -> subprocess.CompletedProcess:
    tools = shim.parent / "tools"
    tools.mkdir(exist_ok=True)
    lock_python = tools / "python3"
    lock_python.write_text(
        f"#!/bin/sh\nexec \"{sys.executable}\" \"$@\"\n", encoding="utf-8"
    )
    lock_python.chmod(0o755)
    # Keep the subprocess on the real scheduler code path while making
    # admission independent of the host's transient memory pressure.
    (tools / "sitecustomize.py").write_text(
        "from library.tools import resource_scheduler\n"
        "resource_scheduler._memory_available_gb = lambda: (12.0, 24.0)\n",
        encoding="utf-8",
    )
    # The gate is a real subprocess and must not join the machine's
    # scheduler (or this test session's scheduler). Its admission path is
    # still exercised against a real scheduler in this private store.
    lock_dir = shim.parent / "gate-heavy-work.lock"
    result = subprocess.run(
        [str(LOCAL_GATE), *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
            "PYTHONPATH": f"{tools}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            "VEP_HEAVY_WORK_LOCK_DIR": str(lock_dir),
            # The pytest session may itself inherit the pooled gate token.
            # Do not let the child appear covered by that shared grant.
            "VEP_HEAVY_WORK_OWNER": "",
            "FULL_SUITE_GATE_PYTHON": str(shim),
            "FULL_SUITE_GATE_SKIP_NATIVE_PREP": "1",
            "REN_CI_SHADOW_AUDIT": "queue",
        },
        timeout=180,
    )
    assert (lock_dir.parent / "resource-scheduler.sqlite3").is_file()
    return result


def _report_shim(tmp_path: Path, report_body: str | None, exit_code: int = 0) -> Path:
    body = (
        "" if report_body is None else f"cat > \"$out\" <<'XML'\n{report_body}\nXML\n"
    )
    return _write_shim(
        tmp_path,
        "#!/bin/sh\n"
        "is_pytest=0\n"
        "out=''\n"
        "previous=''\n"
        'for arg in "$@"; do\n'
        '  case "$arg" in --junitxml=*) out="${arg#--junitxml=}" ;; esac\n'
        "  if [ \"$previous\" = '-m' ] && [ \"$arg\" = 'pytest' ]; then is_pytest=1; fi\n"
        '  previous="$arg"\n'
        "done\n"
        'if [ "$is_pytest" -eq 0 ]; then exec python3 "$@"; fi\n'
        f"{body}"
        f"exit {exit_code}\n",
    )


@pytest.mark.parametrize("report", [None, '<testsuites><testsuite tests="1"'])
def test_real_gate_rejects_missing_or_truncated_junit_report(tmp_path, report):
    """Defect caught: a successful pytest process without a complete JUnit report passes."""
    shim = _report_shim(tmp_path, report)
    result = _run_gate(shim, "--skip-real-model", "--no-parallel")
    verdicts = [
        line
        for line in result.stdout.splitlines()
        if line.startswith("FULL-SUITE GATE:")
    ]
    assert len(verdicts) == 1
    assert verdicts[0].startswith("FULL-SUITE GATE: DID NOT RUN")
    assert result.returncode != 0


def test_real_gate_calls_verdict_module_and_propagates_its_pass_exit(tmp_path):
    """Defect caught: the shell gate ignores the extracted verdict or its exit status."""
    shim = _report_shim(
        tmp_path,
        textwrap.dedent("""\
            <?xml version="1.0"?>
            <testsuites><testsuite name="pytest" tests="1" skipped="0"
              failures="0" errors="0"><testcase name="ok" classname="smoke"/>
            </testsuite></testsuites>
        """),
    )
    result = _run_gate(shim, "--exclude-real-model", "--no-parallel")
    assert (
        "FULL-SUITE GATE: PASS  |  unit+scenario: 1 passed, 0 skipped" in result.stdout
    )
    assert result.returncode == 0


def test_real_gate_fails_when_ren_native_tests_skip(tmp_path):
    shim = _report_shim(
        tmp_path,
        textwrap.dedent("""\
            <?xml version="1.0"?>
            <testsuites><testsuite name="pytest" tests="1" skipped="1"
              failures="0" errors="0"><testcase name="parity"
              classname="ren_native"><skipped message="the ren_native extension is stale"/>
            </testcase></testsuite></testsuites>
        """),
    )
    result = _run_gate(shim, "--exclude-real-model", "--no-parallel")
    assert "FULL-SUITE GATE: FAIL" in result.stdout
    assert "ren_native was missing or stale" in result.stdout
    assert result.returncode != 0


def test_native_tool_preparation_test_switch_keeps_production_ordering():
    source = LOCAL_GATE.read_text(encoding="utf-8")
    lock = source.index("heavy_work_lock owns --profile full_suite_gate")
    static = source.index('echo "=== full-suite gate: static source checks')
    native = source.index('echo "=== full-suite gate: prepare native test tools')
    pytest_pos = source.index("parallel lane - pytest -n ${WORKERS}")
    assert lock < static < native < pytest_pos
    assert "FULL_SUITE_GATE_SKIP_NATIVE_PREP:-0" in source
    assert (
        "FULL-SUITE GATE: DID NOT RUN (native test tools could not be prepared)"
        in source
    )
    assert "export PIPELINE_REN_NATIVE_CACHE=" in source
    assert "native_ci_cache.py" in source
    assert '. "${HOME}/.cargo/env"' in source
    assert "DIRECT_SHADOW_PLAN" in source
    assert '"${REN_CI_SHADOW_AUDIT:-}" != "queue"' in source
    assert "tier2_shadow_audit.py" in source


def test_the_local_gate_contract_without_hosted_ci():
    assert not (REPO_ROOT / ".github" / "workflows" / "ci.yml").exists()
    config = tomllib.loads(
        (REPO_ROOT / "ruff-ci-gate.toml").read_text(encoding="utf-8")
    )
    enforced = config["lint"]["select"]
    assert "PLW1510" in enforced and "B023" in enforced
    pytest_config = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    marker_names = {
        marker.split(":", maxsplit=1)[0]
        for marker in pytest_config["tool"]["pytest"]["ini_options"]["markers"]
    }
    assert {"unit", "scenario", "resolve_live", "real_model"} <= marker_names
    assert not {"heavy", "heavy_ml"} & marker_names
    assert set(pytest_config["tool"]["pytest"]["ini_options"]["testpaths"]) == {"tests"}
    gate = LOCAL_GATE.read_text(encoding="utf-8")
    assert "-p scripts.pytest_timing --timing-shard" in gate
    assert 'if [ "${VERDICT}" = "PASS" ]' in gate
    assert 'scripts/pytest_timing.py" update' in gate
    assert " -m real_model -rs --tb=short" in gate
