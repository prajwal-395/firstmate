"""The native front door an engine tree ships, and the script that builds it.

The defect this names: migration 2 of the Rust-core plan replaces the
installed `ren` front door with a native binary while every command
still runs the Python implementation underneath. Two behaviours carry
that, and both are machine-dependent in the same way:

* `ren/package_engine.py` must place the native binary at `bin/ren` of
  a built tree when the Rust toolchain is there, and keep the bash
  door when it is not - packaging must never fail for lack of cargo;
* `scripts/build_ren_cli.sh` is the one builder (binaries are never
  committed), and its contract is: exit 0 and the binary placed with
  cargo, exit 4 and nothing placed without it.

The binary's own behaviour - exec `python -m ren.cli` with the
original argv, refuse with the ladder's message - is pinned by the
crate's tests under `native/ren-cli/`.
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

BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_ren_cli.sh"
BASH_FRONT_DOOR = REPO_ROOT / "bin" / "ren"


def _cargo_available() -> bool:
    """The build script's own test: cargo on PATH, or at ~/.cargo/bin."""
    if shutil.which("cargo"):
        return True
    return (Path.home() / ".cargo" / "bin" / "cargo").is_file()


def _build_tree(dest: Path) -> Path:
    from ren.package_engine import build_engine_tree
    return build_engine_tree(REPO_ROOT, dest, channel="test",
                             sha="abc123def456", built_at="2026-10-04")


# ── The packaging wiring ─────────────────────────────────────


def test_a_built_tree_ships_the_native_front_door_with_cargo(
        tmp_path, packageable_native_runtime):
    dest = _build_tree(tmp_path / "engine")
    front_door = dest / "bin" / "ren"
    assert front_door.is_file()
    assert os.access(front_door, os.X_OK)
    if _cargo_available():
        assert front_door.read_bytes() != BASH_FRONT_DOOR.read_bytes()
    else:
        assert front_door.read_bytes() == BASH_FRONT_DOOR.read_bytes()


def test_the_native_front_door_still_runs_the_python_implementation(
        tmp_path, monkeypatch, packageable_native_runtime):
    """The placed binary is a front door, not a different product: it
    answers `ren --version` off the tree it sits in."""
    if not _cargo_available():
        pytest.skip("no Rust toolchain on this machine")
    # An explicit `$REN_ENGINE_ROOT` names the engine outright and wins
    # over the tree holding the binary - so without this, a worker
    # launched with that variable set (ren-ci inherits it from its own
    # checkout) would name the ambient engine instead of the built tree
    # and fail for no reason the binary controls.
    monkeypatch.delenv("REN_ENGINE_ROOT", raising=False)
    dest = _build_tree(tmp_path / "engine")
    done = subprocess.run(
        [str(dest / "bin" / "ren"), "--version"],
        capture_output=True, encoding="utf-8", timeout=120, check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("ren 0.1.0+")
    assert str(dest) in done.stdout


# ── The build script's contract ──────────────────────────────


def test_the_build_script_places_the_binary_when_cargo_is_available(tmp_path):
    if not _cargo_available():
        pytest.skip("no Rust toolchain on this machine")
    target = tmp_path / "bin"
    target.mkdir()
    done = subprocess.run(
        ["bash", str(BUILD_SCRIPT), str(target)],
        capture_output=True, encoding="utf-8", timeout=600, check=False,
    )
    assert done.returncode == 0, done.stderr
    placed = target / "ren"
    assert placed.is_file()
    assert os.access(placed, os.X_OK)
    assert placed.read_bytes() != BASH_FRONT_DOOR.read_bytes()


def test_the_build_script_keeps_the_bash_door_without_cargo(
        tmp_path, monkeypatch):
    """No cargo anywhere the script looks: exit 4, nothing written, so
    the caller's bash door stands and packaging succeeds."""
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:/bin"
    target = tmp_path / "bin"
    target.mkdir()
    done = subprocess.run(
        ["bash", str(BUILD_SCRIPT), str(target)],
        capture_output=True, encoding="utf-8", timeout=600, check=False,
        env=env,
    )
    assert done.returncode == 4
    assert "cargo" in done.stderr
    assert not (target / "ren").exists()
