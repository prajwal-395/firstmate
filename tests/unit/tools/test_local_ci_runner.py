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


def test_run_gate_limits_the_full_suite_to_two_workers(monkeypatch,
                                                       tmp_path):
    calls = []

    def run_logged(command, cwd, log_path, timeout):
        calls.append((command, cwd, log_path, timeout))
        return {"timed_out": False, "group_clean": True}

    monkeypatch.setattr(runner, "_run_logged", run_logged)
    monkeypatch.setattr(runner, "_read_verdict", lambda _path: "PASS")

    result = runner.run_gate(str(tmp_path), str(tmp_path / "gate.log"))

    assert result["ok"] is True
    assert calls[0][0] == ["bash", "scripts/full_suite_gate.sh", "-n", "2"]
