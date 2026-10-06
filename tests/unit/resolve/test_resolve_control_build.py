"""The script that builds the resolve-control driver binary.

The gap this names: the driver's Python client spawns the release build
beside the checkout, and binaries are never committed - but no script
built it. `scripts/build_resolve_control.sh` is the one builder, and its
contract is: exit 0 and the binary built with cargo, exit 4 and nothing
built without it.

The with-cargo case compiles the driver in release (expensive: stated
reason - it pins that the one builder produces the binary the client
spawns, not just that the script exits 0).
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_resolve_control.sh"
RELEASE_BIN = (REPO_ROOT / "native" / "target" / "release"
               / "resolve-control")


def _cargo_available() -> bool:
    """The build script's own test: cargo on PATH, or at ~/.cargo/bin."""
    if shutil.which("cargo"):
        return True
    return (Path.home() / ".cargo" / "bin" / "cargo").is_file()


def test_the_build_script_refuses_an_argument():
    done = subprocess.run(
        ["bash", str(BUILD_SCRIPT), "extra-arg"],
        capture_output=True, encoding="utf-8", timeout=60, check=False,
    )
    assert done.returncode == 2, done.stderr


def test_the_build_script_builds_nothing_without_cargo(
        tmp_path, monkeypatch):
    """No cargo anywhere the script looks: exit 4, so the caller keeps
    whatever binary (or refusal) it already had."""
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin"
    done = subprocess.run(
        ["bash", str(BUILD_SCRIPT)],
        capture_output=True, encoding="utf-8", timeout=60, check=False,
        env=env, cwd=str(REPO_ROOT),
    )
    assert done.returncode == 4, done.stderr


@pytest.mark.skipif(not _cargo_available(), reason="no Rust toolchain")
def test_the_build_script_places_the_release_binary():
    done = subprocess.run(
        ["bash", str(BUILD_SCRIPT)],
        capture_output=True, encoding="utf-8", timeout=600, check=False,
        cwd=str(REPO_ROOT),
    )
    assert done.returncode == 0, done.stderr
    assert RELEASE_BIN.is_file()
    assert os.access(RELEASE_BIN, os.X_OK)
