"""A small set of end-to-end checks for the test gate's verdict."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_GATE = REPO_ROOT / "scripts" / "full_suite_gate.sh"


def _write_shim(tmp_path: Path, source: str) -> Path:
    # The shim stands in for the gate interpreter, so its non-pytest
    # fallthrough must run the SAME interpreter the test itself runs
    # under - never the ambient `python3`, which on this machine is a
    # bare 3.14 with none of the ML stack. A shim that delegates to a
    # different interpreter judges the gate's static checks under an
    # environment the real gate never runs in.
    source = source.replace("exec python3", f'exec "{sys.executable}"')
    shim = tmp_path / "pytest-shim.sh"
    shim.write_text(source, encoding="utf-8")
    shim.chmod(0o755)
    return shim


def _run_gate(shim: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(LOCAL_GATE), *args],
        capture_output=True,
        encoding="utf-8",
        check=False,
        cwd=REPO_ROOT,
        env={**os.environ, "FULL_SUITE_GATE_PYTHON": str(shim)},
    )


def _verdict(result: subprocess.CompletedProcess) -> str:
    verdicts = [
        line for line in result.stdout.splitlines()
        if line.startswith("FULL-SUITE GATE:")
    ]
    assert len(verdicts) == 1, (
        f"expected one gate verdict, got {verdicts}\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    return verdicts[0]


def test_native_gate_tools_are_prepared_after_lock_and_before_pytest():
    source = LOCAL_GATE.read_text(encoding="utf-8")
    lock = source.index(
        "heavy_work_lock owns --profile full_suite_gate")
    static = source.index(
        'echo "=== full-suite gate: static source checks')
    native = source.index(
        'echo "=== full-suite gate: prepare native test tools')
    pytest_pos = source.index("parallel lane - pytest -n ${WORKERS}")

    assert lock < static < native < pytest_pos
    assert "FULL-SUITE GATE: DID NOT RUN (native test tools could not be prepared)" in source


def _report_shim(tmp_path: Path, xml: str, exit_code: int = 0) -> Path:
    script = (
        "#!/bin/sh\n"
        "is_pytest=0\n"
        'previous=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        '    --junitxml=*) out="${arg#--junitxml=}" ;;\n'
        '  esac\n'
        '  if [ "$previous" = "-m" ] && [ "$arg" = "pytest" ]; then\n'
        "    is_pytest=1\n"
        "  fi\n"
        '  previous="$arg"\n'
        'done\n'
        'if [ "$#" -ge 2 ] && [ "$1" = "-c" ] '
        '&& [ "$2" = "import parselmouth" ]; then exit 1; fi\n'
        'if [ "$is_pytest" -eq 0 ]; then exec python3 "$@"; fi\n'
        'if [ -n "${out:-}" ]; then\n'
        '  cat > "$out" <<\'XML\'\n'
        f"{textwrap.dedent(xml).strip()}\n"
        "XML\n"
        "fi\n"
        f"exit {exit_code}\n"
    )
    return _write_shim(tmp_path, script)


def test_a_success_exit_without_a_junit_report_cannot_pass(tmp_path):
    shim = _write_shim(tmp_path, "#!/bin/sh\nexit 0\n")

    result = _run_gate(shim, "--skip-real-model", "--no-parallel")

    assert "DID NOT RUN" in _verdict(result)
    assert result.returncode != 0
    assert "test_marker_capture_against_resolve.py" in result.stdout


def test_a_zero_test_junit_report_cannot_pass(tmp_path):
    shim = _write_shim(
        tmp_path,
        textwrap.dedent("""\
            #!/bin/sh
            previous=""
            is_pytest=0
            for arg in "$@"; do
              if [ "$previous" = "-m" ] && [ "$arg" = "pytest" ]; then
                is_pytest=1
              fi
              case "$arg" in --junitxml=*) out="${arg#--junitxml=}" ;; esac
              previous="$arg"
            done
            if [ "$is_pytest" -eq 0 ]; then exec python3 "$@"; fi
            cat > "$out" <<XML
            <testsuites><testsuite name="pytest" tests="0" skipped="0"
              failures="0" errors="0"/></testsuites>
            XML
            exit 0
        """),
    )

    result = _run_gate(shim, "--skip-real-model")

    assert "DID NOT RUN" in _verdict(result)
    assert "merged report contains 0 tests" in result.stdout
    assert result.returncode != 0


def test_a_clean_report_with_a_nonzero_pytest_exit_fails(tmp_path):
    shim = _report_shim(
        tmp_path,
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites><testsuite name="pytest" tests="2" skipped="0"
          failures="0" errors="0">
          <testcase name="test_a" classname="m"/>
          <testcase name="test_b" classname="m"/>
        </testsuite></testsuites>
        """,
        exit_code=1,
    )

    result = _run_gate(shim, "--skip-real-model", "--no-parallel")

    assert _verdict(result).startswith("FULL-SUITE GATE: FAIL")
    assert "pytest exited 1" in result.stdout
    assert result.returncode != 0


def test_an_undeclared_skip_in_a_single_process_report_fails(tmp_path):
    shim = _report_shim(
        tmp_path,
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites><testsuite name="pytest" tests="2" skipped="1"
          failures="0" errors="0">
          <testcase name="test_a" classname="m"/>
          <testcase name="test_b" classname="m">
            <skipped message="a skip nobody declared"/>
          </testcase>
        </testsuite></testsuites>
        """,
    )

    result = _run_gate(shim, "--skip-real-model", "--no-parallel")

    assert _verdict(result).startswith("FULL-SUITE GATE: FAIL")
    assert "undeclared" in result.stdout
    assert result.returncode != 0


def test_an_undeclared_skip_in_the_merged_parallel_report_fails(tmp_path):
    shim = _write_shim(
        tmp_path,
        textwrap.dedent("""\
            #!/bin/sh
            lane=serial
            previous=""
            is_pytest=0
            for arg in "$@"; do
              if [ "$arg" = "-n" ]; then lane=parallel; fi
              if [ "$previous" = "-m" ] && [ "$arg" = "pytest" ]; then
                is_pytest=1
              fi
              case "$arg" in --junitxml=*) out="${arg#--junitxml=}" ;; esac
              previous="$arg"
            done
            if [ "$is_pytest" -eq 0 ]; then exec python3 "$@"; fi
            if [ "$lane" = "parallel" ]; then
              cat > "$out" <<XML
            <testsuites><testsuite name="pytest" tests="1" skipped="1"
              failures="0" errors="0">
              <testcase name="parallel_test" classname="m">
                <skipped message="a skip nobody declared"/>
              </testcase>
            </testsuite></testsuites>
            XML
            else
              cat > "$out" <<XML
            <testsuites><testsuite name="pytest" tests="1" skipped="0"
              failures="0" errors="0">
              <testcase name="serial_test" classname="m"/>
            </testsuite></testsuites>
            XML
            fi
            exit 0
        """),
    )

    result = _run_gate(shim, "--skip-real-model")

    assert _verdict(result).startswith("FULL-SUITE GATE: FAIL")
    assert "undeclared skips in the merged report" in result.stdout
    assert "parallel_test" in result.stdout
    assert result.returncode != 0


def test_a_missing_capability_skip_narrows_the_verdict_by_name(tmp_path):
    shim = _report_shim(
        tmp_path,
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites><testsuite name="pytest" tests="2" skipped="1"
          failures="0" errors="0">
          <testcase name="test_a" classname="m"/>
          <testcase name="test_b" classname="m">
            <skipped message="ffmpeg is not available"/>
          </testcase>
        </testsuite></testsuites>
        """,
    )

    result = _run_gate(shim, "--no-parallel")

    verdict = _verdict(result)
    assert verdict.startswith("FULL-SUITE GATE: NARROWED PASS")
    assert "ffmpeg (1 tests skipped)" in result.stdout
    assert "real_model (category not measured)" in result.stdout
    assert "parselmouth not importable" in result.stdout
    assert result.returncode != 0


def test_local_ci_real_model_exclusion_is_not_environment_narrowing(tmp_path):
    shim = _report_shim(
        tmp_path,
        """\
        <?xml version="1.0" encoding="utf-8"?>
        <testsuites><testsuite name="pytest" tests="2" skipped="0"
          failures="0" errors="0">
          <testcase name="test_a" classname="m"/>
          <testcase name="test_b" classname="m"/>
        </testsuite></testsuites>
        """,
    )

    result = _run_gate(shim, "--exclude-real-model", "--no-parallel")

    assert _verdict(result).startswith("FULL-SUITE GATE: PASS")
    assert "real_model excluded by local CI marker policy" in result.stdout
    assert "pytest -m real_model" not in result.stdout
    assert result.returncode == 0


def test_parallel_lane_exit_codes_reach_the_verdict(tmp_path):
    shim = _write_shim(
        tmp_path,
        textwrap.dedent("""\
            #!/bin/sh
            is_pytest=0
            lane=serial
            previous=""
            for arg in "$@"; do
              case "$arg" in
                -n) lane=parallel ;;
                --junitxml=*) out="${arg#--junitxml=}" ;;
              esac
              if [ "$previous" = "-m" ] && [ "$arg" = "pytest" ]; then
                is_pytest=1
              fi
              previous="$arg"
            done
            if [ "$is_pytest" -eq 0 ]; then exec python3 "$@"; fi
            cat > "$out" <<XML
            <?xml version="1.0" encoding="utf-8"?>
            <testsuites><testsuite name="pytest" tests="1" skipped="0"
              failures="0" errors="0">
              <testcase name="test_${lane}" classname="m"/>
            </testsuite></testsuites>
            XML
            exit 1
        """),
    )

    result = _run_gate(shim, "--skip-real-model")

    assert _verdict(result).startswith("FULL-SUITE GATE: FAIL")
    assert "pytest exited 1" in result.stdout
    assert result.returncode != 0


def test_duplicate_lane_nodeids_do_not_merge_as_pass(tmp_path):
    shim = _write_shim(
        tmp_path,
        textwrap.dedent("""\
            #!/bin/sh
            previous=""
            out=""
            is_pytest=0
            for arg in "$@"; do
              if [ "$arg" = "-n" ]; then lane=parallel; fi
              if [ "$previous" = "-m" ] && [ "$arg" = "pytest" ]; then
                is_pytest=1
              fi
              case "$arg" in --junitxml=*) out="${arg#--junitxml=}" ;; esac
              previous="$arg"
            done
            if [ "$is_pytest" -eq 0 ]; then exec python3 "$@"; fi
            cat > "$out" <<XML
            <testsuites><testsuite name="pytest" tests="1" skipped="0"
              failures="0" errors="0">
              <testcase name="same_test" classname="same_module"/>
            </testsuite></testsuites>
            XML
            exit 0
        """),
    )

    result = _run_gate(shim, "--skip-real-model")

    assert "DID NOT RUN" in _verdict(result)
    assert "duplicate nodeid same_module::same_test" in result.stdout
    assert result.returncode != 0


def test_missing_xdist_does_not_report_a_pass(tmp_path):
    shim = _write_shim(
        tmp_path,
        "#!/bin/sh\n"
        'for arg in "$@"; do\n'
        '  if [ "$arg" = "-n" ]; then\n'
        '    echo "unrecognized arguments: -n auto --dist loadfile" >&2\n'
        '    exit 4\n'
        '  fi\n'
        'done\n'
        'exec python3 "$@"\n',
    )

    result = _run_gate(shim, "--skip-real-model")

    assert "DID NOT RUN" in _verdict(result)
    assert "pytest-xdist" in result.stdout
    assert result.returncode != 0


def test_the_local_gate_contract_without_hosted_ci():
    # 2026-10-07: ci.yml is deleted (PR CI is ren-ci now -
    # tests/contracts/test_no_hosted_actions.py owns that rule), so the
    # workflow-shape assertions this test used to carry live there.
    # What stays here is the LOCAL half it has always guarded: the
    # enforcing ruff config, the pytest markers and testpaths, and the
    # full-suite gate's own load-bearing strings.
    assert not (REPO_ROOT / ".github" / "workflows" / "ci.yml").exists()

    config = tomllib.loads((REPO_ROOT / "ruff-ci-gate.toml").read_text(encoding="utf-8"))
    enforced = config["lint"]["select"]
    assert "PLW1510" in enforced and "B023" in enforced

    pytest_config = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    marker_names = {
        marker.split(":", maxsplit=1)[0]
        for marker in pytest_config["tool"]["pytest"]["ini_options"]["markers"]
    }
    assert {"unit", "scenario", "resolve_live", "real_model"} <= marker_names
    assert not {"heavy", "heavy_ml"} & marker_names
    assert set(pytest_config["tool"]["pytest"]["ini_options"]["testpaths"]) == {
        "tests",
    }

    gate = LOCAL_GATE.read_text(encoding="utf-8")
    assert "-p scripts.pytest_timing --timing-shard" in gate
    assert 'if [ "${VERDICT}" = "PASS" ]' in gate
    assert 'scripts/pytest_timing.py" update' in gate
    assert ' -m real_model -rs --tb=short' in gate
