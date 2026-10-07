"""The full gate binds native build outputs outside disposable checkouts."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
PREPARE_SCRIPT = REPO_ROOT / "scripts" / "prepare_local_ci_gate.sh"


def _executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_checkout(tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    (checkout / "native").mkdir(parents=True)
    scripts.mkdir()
    shutil.copy2(PREPARE_SCRIPT, scripts / PREPARE_SCRIPT.name)
    _executable(
        scripts / "build_fframes_runtime.sh",
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'mkdir -p "$REPO_ROOT/native/target/release"\n'
        'cat > "$REPO_ROOT/native/target/release/ren-graphics-fframes" <<\'BIN\'\n'
        "#!/bin/sh\n"
        'if [ "${1:-}" = "--version" ]; then echo test-fframes; fi\n'
        "exit 0\n"
        "BIN\n"
        'chmod +x "$REPO_ROOT/native/target/release/ren-graphics-fframes"\n',
    )
    return checkout, scripts / PREPARE_SCRIPT.name


def _fake_cargo(bin_dir: Path, log_path: Path) -> None:
    _executable(
        bin_dir / "cargo",
        "#!/bin/sh\n"
        'printf "%s:%s\\n" "$CARGO_BUILD_JOBS" "$*" >> "$CARGO_CALLS"\n'
        'mkdir -p "$REPO_ROOT/native/target/debug"\n'
        'cat > "$REPO_ROOT/native/target/debug/ml-worker-client" <<\'BIN\'\n'
        "#!/bin/sh\nexit 0\n"
        "BIN\n"
        'chmod +x "$REPO_ROOT/native/target/debug/ml-worker-client"\n',
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)


def test_native_test_binaries_build_from_the_checkout_in_shared_cache(
        tmp_path):
    checkout, script = _fake_checkout(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "cargo-calls.log"
    _fake_cargo(bin_dir, calls)
    state_dir = tmp_path / "ren-ci"
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "REN_CI_STATE": str(state_dir),
        "CARGO_CALLS": str(calls),
    }

    result = subprocess.run(
        ["bash", str(script)], cwd=checkout, env=env,
        capture_output=True, encoding="utf-8", check=False, timeout=30)

    target = checkout / "native" / "target"
    assert result.returncode == 0, result.stderr
    assert target.is_symlink()
    assert target.resolve() == state_dir / "native-target"
    assert (target / "debug" / "ml-worker-client").is_file()
    assert (target / "release" / "ren-graphics-fframes").is_file()
    assert "CI native capabilities: PASS" in result.stdout
    cargo_call = calls.read_text(encoding="utf-8")
    assert "2:build --manifest-path" in cargo_call
    assert "--locked --bin ml-worker-client" in cargo_call


def test_native_setup_refuses_a_target_link_outside_its_cache(tmp_path):
    checkout, script = _fake_checkout(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "cargo-calls.log"
    _fake_cargo(bin_dir, calls)
    state_dir = tmp_path / "ren-ci"
    foreign_target = tmp_path / "foreign-target"
    foreign_target.mkdir()
    (checkout / "native" / "target").symlink_to(
        foreign_target, target_is_directory=True)
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "REN_CI_STATE": str(state_dir),
        "CARGO_CALLS": str(calls),
    }

    result = subprocess.run(
        ["bash", str(script)], cwd=checkout, env=env,
        capture_output=True, encoding="utf-8", check=False, timeout=30)

    assert result.returncode != 0
    assert "refusing native target link outside the Ren CI cache" in result.stderr
    assert not calls.exists()
