"""The local runner must test the disposable checkout it was given."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

from library.tools.local_ci import runner


REPO_ROOT = Path(__file__).resolve().parents[3]


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_full_suite_gate_prepends_disposable_checkout_to_pythonpath(
        tmp_path):
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "scripts/full_suite_gate.sh",
                 scripts / "full_suite_gate.sh")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "python3", "#!/bin/sh\nexit 0\n")
    scope_file = tmp_path / "pythonpath.txt"
    fake_gate_python = tmp_path / "gate-python"
    _executable(fake_gate_python, "#!/bin/sh\nprintf '%s' \"$PYTHONPATH\" "
                f"> {str(scope_file)!r}\nexit 0\n")
    installed_root = tmp_path / "installed-primary"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "PYTHONPATH": str(installed_root),
        "PYTHONSAFEPATH": "1",
        "FULL_SUITE_GATE_PYTHON": str(fake_gate_python),
        "SCOPE_FILE": str(scope_file),
    }

    result = subprocess.run(
        ["/bin/bash", str(scripts / "full_suite_gate.sh"), "--help"],
        cwd=checkout, env=env, capture_output=True, encoding="utf-8",
        check=False, timeout=30)

    assert result.returncode == 0, result.stderr
    assert scope_file.read_text(encoding="utf-8") == (
        f"{checkout}:{installed_root}")


def test_run_gate_excludes_real_model_and_limits_workers(monkeypatch,
                                                          tmp_path):
    calls = []

    def run_logged(command, cwd, log_path, timeout, env=None):
        calls.append((command, cwd, log_path, timeout))
        with open(log_path, "w", encoding="utf-8") as log:
            log.write("FULL-SUITE GATE: PASS  |  unit+scenario: 7 passed")
        return {"timed_out": False, "group_clean": True}

    monkeypatch.setattr(runner, "_run_logged", run_logged)

    result = runner.run_gate(str(tmp_path), str(tmp_path / "gate.log"),
                             timeout=1200)

    assert result["ok"] is True
    assert calls[0][0] == [
        "bash", "scripts/full_suite_gate.sh", "--exclude-real-model",
        "-n", "2",
    ]


def test_a_timed_out_gate_requeues_as_no_verdict(monkeypatch, tmp_path):
    # The October lane: an unloaded 700s suite killed at a fixed 1200s
    # under agent load at 98% complete. A timeout judged nothing, so
    # the batch requeues - it is never a red verdict on the members.
    def run_logged(command, cwd, log_path, timeout, env=None):
        with open(log_path, "w", encoding="utf-8") as log:
            log.write(f"\nREN-CI TIMEOUT after {timeout}s; "
                      "process_group_clean=True\n")
        return {"timed_out": True, "group_clean": True}

    monkeypatch.setattr(runner, "_run_logged", run_logged)

    result = runner.run_gate(str(tmp_path), str(tmp_path / "gate.log"),
                             timeout=1200)

    assert result["ok"] is False
    assert result["requeue"] is True
    assert result["timed_out"] is True
    assert "timed out" in result["reason"]


def test_run_gate_scales_its_timeout_from_the_measured_suite(
        monkeypatch, tmp_path):
    from library.tools.local_ci import history
    seen = []

    def run_logged(command, cwd, log_path, timeout, env=None):
        seen.append(timeout)
        with open(log_path, "w", encoding="utf-8") as log:
            log.write("FULL-SUITE GATE: PASS  |  unit+scenario: 7 passed")
        return {"timed_out": False, "group_clean": True}

    monkeypatch.setattr(runner, "_run_logged", run_logged)
    monkeypatch.setattr(history, "full_suite_wall_seconds",
                        lambda _d: 700.0)

    result = runner.run_gate(str(tmp_path), str(tmp_path / "gate.log"))

    assert result["ok"] is True
    assert seen == [1400]


def test_read_verdict_keeps_gate_outcomes_distinct(tmp_path):
    log_path = tmp_path / "gate.log"
    cases = [
        ("FULL-SUITE GATE: NARROWED PASS - missing capability | detail",
         "NARROWED PASS"),
        ("FULL-SUITE GATE: DID NOT RUN (native tools missing)",
         "DID NOT RUN"),
        ("FULL-SUITE GATE: FAIL (static source checks)", "FAIL"),
    ]

    for line, expected in cases:
        log_path.write_text(line + "\n", encoding="utf-8")
        assert runner._read_verdict(str(log_path)) == expected


def test_run_does_not_pass_conftest_files_as_pytest_paths(monkeypatch,
                                                           tmp_path):
    checkout = tmp_path / "checkout"
    root_conftest = checkout / "tests" / "conftest.py"
    nested_conftest = checkout / "tests" / "unit" / "reels" / "conftest.py"
    test_file = checkout / "tests" / "unit" / "tools" / "test_example.py"
    for path in (root_conftest, nested_conftest, test_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    monkeypatch.setattr(runner.hygiene, "disk_ok", lambda _path: True)
    monkeypatch.setattr(runner.hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(runner.hygiene, "broker_serving", lambda: False)
    monkeypatch.setattr(runner, "_pytest_python", lambda: "python3")
    monkeypatch.setattr(runner, "_read_summary", lambda _path: (1, 0, True))
    commands = []
    events = []

    def bind_dependencies(path):
        events.append(("bind", path))

    monkeypatch.setattr(runner.hygiene, "bind_renderer_dependencies",
                        bind_dependencies)

    def run_logged(command, _cwd, _log_path, _timeout):
        commands.append(command)
        events.append(("pytest", command))
        return {"exit": 0, "timed_out": False, "group_clean": True,
                "elapsed": 0}

    monkeypatch.setattr(
        runner, "_run_logged", run_logged)

    result = runner.run(
        ["tests/conftest.py", "tests/unit/reels/conftest.py",
         "tests/unit/tools/test_example.py"],
        str(checkout), str(tmp_path / "pytest.log"))

    assert result["ok"] is True
    assert commands[0][3] == "tests/unit/tools/test_example.py"
    assert [event[0] for event in events] == ["bind", "pytest"]
    assert events[0][1] == str(checkout)
