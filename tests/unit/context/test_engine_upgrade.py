"""Installed engine updates keep projects outside the version store.

These cases name the upgrade contract: a candidate is verified before
`current` moves, failed verification leaves the active version intact,
and rollback swaps only the two engine pointers.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools.ren_refusal import RenRefusal
from ren import engine_root, upgrade


def _fake_engine(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "manage_project.py").write_text("# fake engine\n", encoding="utf-8")
    (path / "library").mkdir(exist_ok=True)
    (path / "ren").mkdir(exist_ok=True)
    (path / "ren" / "cli.py").write_text("# fake front door\n", encoding="utf-8")
    (path / "bin").mkdir(exist_ok=True)
    (path / "bin" / "ren").write_text("#!/bin/sh\n", encoding="utf-8")
    (path / "bin" / "vep").write_text("#!/bin/sh\n", encoding="utf-8")
    return path


def _current_version(home: Path) -> str | None:
    return engine_root.version_at_pointer(engine_root.current_link(home), home)


def _previous_version(home: Path) -> str | None:
    return engine_root.version_at_pointer(engine_root.previous_link(home), home)


def test_upgrade_stages_then_switches_and_rollback_only_swaps_pointers(
        tmp_path, monkeypatch):
    home = tmp_path / "support" / "Ren"
    old = _fake_engine(home / "versions" / "old")
    candidate = _fake_engine(home / "versions" / "candidate")
    (home / "current").symlink_to(Path("versions") / "old")
    projects = tmp_path / "projects" / "sample"
    (projects / "raw").mkdir(parents=True)
    project_yaml = projects / "project.yaml"
    project_yaml.write_text("project_format_version: 1\n", encoding="utf-8")
    media = projects / "raw" / "keep.mov"
    media.write_bytes(b"customer media")

    monkeypatch.setattr(
        upgrade, "stage_versioned", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(upgrade, "_verify_doctor", lambda *_args: None)
    monkeypatch.setattr(upgrade, "_verify_project_formats", lambda *_args: None)

    installed = upgrade.upgrade(tmp_path / "source", home=home)

    assert installed == candidate
    assert _current_version(home) == "candidate"
    assert _previous_version(home) == "old"
    assert engine_root.installed_root(home) == candidate.resolve()

    restored = upgrade.rollback(home=home)

    assert restored == old
    assert _current_version(home) == "old"
    assert _previous_version(home) == "candidate"
    assert project_yaml.read_text(encoding="utf-8") == "project_format_version: 1\n"
    assert media.read_bytes() == b"customer media"


def test_failed_candidate_doctor_keeps_both_pointers_and_discards_candidate(
        tmp_path, monkeypatch):
    home = tmp_path / "support" / "Ren"
    _fake_engine(home / "versions" / "old")
    _fake_engine(home / "versions" / "prior")
    candidate = _fake_engine(home / "versions" / "candidate")
    (home / "current").symlink_to(Path("versions") / "old")
    (home / "previous").symlink_to(Path("versions") / "prior")

    monkeypatch.setattr(
        upgrade, "stage_versioned", lambda *_args, **_kwargs: candidate)

    def doctor_fails(*_args):
        raise RuntimeError("candidate required checks failed")

    monkeypatch.setattr(upgrade, "_verify_doctor", doctor_fails)
    monkeypatch.setattr(upgrade, "_verify_project_formats", lambda *_args: None)

    with pytest.raises(RenRefusal, match="candidate required checks failed"):
        upgrade.upgrade(tmp_path / "source", home=home)

    assert _current_version(home) == "old"
    assert _previous_version(home) == "prior"
    assert not candidate.exists()


def test_rollback_refuses_when_previous_is_the_active_version(tmp_path):
    home = tmp_path / "support" / "Ren"
    _fake_engine(home / "versions" / "active")
    (home / "current").symlink_to(Path("versions") / "active")
    (home / "previous").symlink_to(Path("versions") / "active")

    with pytest.raises(RenRefusal, match="already names the active engine"):
        upgrade.rollback(home=home)

    assert _current_version(home) == "active"
    assert _previous_version(home) == "active"


def test_project_config_paths_follow_the_registry_layout(tmp_path):
    from library.tools.project_registry import project_config_paths

    root = tmp_path / "projects"
    flat = root / "flat" / "project.yaml"
    grouped = root / "client" / "grouped" / "project.yaml"
    hidden = root / ".archived" / "private" / "project.yaml"
    for path in (flat, grouped, hidden):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("name: example\n", encoding="utf-8")

    assert project_config_paths(root) == [grouped, flat]


def test_incompatible_project_format_refuses_before_switching(
        tmp_path, monkeypatch):
    from library.tools import paths

    home = tmp_path / "support" / "Ren"
    _fake_engine(home / "versions" / "active")
    (home / "current").symlink_to(Path("versions") / "active")
    monkeypatch.setattr(paths, "PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(
        upgrade.subprocess, "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0,
            stdout=json.dumps([{
                "project": str(tmp_path / "projects" / "too-new"),
                "compatible": False,
                "reason": "format 9 is unsupported",
                "why": "candidate supports 0..1",
                "fix": "use a newer Ren build",
            }]), stderr=""))

    with pytest.raises(RenRefusal, match="candidate supports 0..1"):
        upgrade._verify_project_formats(home / "versions" / "active", home)

    assert _current_version(home) == "active"


def test_project_format_failure_keeps_the_active_version(
        tmp_path, monkeypatch):
    home = tmp_path / "support" / "Ren"
    _fake_engine(home / "versions" / "active")
    _fake_engine(home / "versions" / "prior")
    candidate = _fake_engine(home / "versions" / "candidate")
    (home / "current").symlink_to(Path("versions") / "active")
    (home / "previous").symlink_to(Path("versions") / "prior")
    monkeypatch.setattr(
        upgrade, "stage_versioned", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(upgrade, "_verify_doctor", lambda *_args: None)

    def formats_fail(*_args):
        raise RenRefusal(
            "project format is not supported", "candidate supports 0..1",
            "open the project with a newer Ren build")

    monkeypatch.setattr(upgrade, "_verify_project_formats", formats_fail)

    with pytest.raises(RenRefusal, match="candidate supports 0..1"):
        upgrade.upgrade(tmp_path / "source", home=home)

    assert _current_version(home) == "active"
    assert _previous_version(home) == "prior"
    assert not candidate.exists()


def test_failed_current_pointer_switch_restores_previous_pointer(
        tmp_path, monkeypatch):
    home = tmp_path / "support" / "Ren"
    _fake_engine(home / "versions" / "old")
    _fake_engine(home / "versions" / "prior")
    candidate = _fake_engine(home / "versions" / "candidate")
    (home / "current").symlink_to(Path("versions") / "old")
    (home / "previous").symlink_to(Path("versions") / "prior")
    monkeypatch.setattr(
        upgrade, "stage_versioned", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(upgrade, "_verify_doctor", lambda *_args: None)
    monkeypatch.setattr(upgrade, "_verify_project_formats", lambda *_args: None)

    def switch_fails(*_args):
        raise OSError("simulated atomic pointer failure")

    monkeypatch.setattr(upgrade, "switch_current", switch_fails)

    with pytest.raises(RenRefusal, match="simulated atomic pointer failure"):
        upgrade.upgrade(tmp_path / "source", home=home)

    assert _current_version(home) == "old"
    assert _previous_version(home) == "prior"
    assert not candidate.exists()


def _proof_env(home: Path, box: Path) -> dict:
    env = dict(os.environ)
    env.pop("REN_ENGINE_ROOT", None)
    env.update({
        "HOME": str(box / "home"),
        "XDG_DATA_HOME": str(box / "xdg"),
        "PIPELINE_VEP_HOME": str(home),
        "PIPELINE_PYTHON": sys.executable,
        "PIPELINE_PROJECTS_ROOT": str(box / "projects"),
        "REN_CONFIG": str(box / "config.env"),
        "PYTHONPATH": "",
        "PATH": os.pathsep.join(
            (str(home / "current" / "bin"), env.get("PATH", ""))),
    })
    return env


def _run_ren(home: Path, box: Path, *args: str):
    return subprocess.run(
        ["ren", *args], cwd=str(box), env=_proof_env(home, box),
        capture_output=True, encoding="utf-8", timeout=360, check=False)


def test_upgrade_failed_upgrade_and_rollback_in_a_throwaway_home(tmp_path):
    """Exercise the installed CLI with isolated HOME and project storage."""
    from ren.package_engine import install_versioned

    home = tmp_path / "support" / "Ren"
    box = tmp_path / "customer"
    box.mkdir()
    project = box / "projects" / "sample"
    (project / "raw").mkdir(parents=True)
    project_yaml = project / "project.yaml"
    project_yaml.write_text(
        "name: Sample\nproject_format_version: 1\n", encoding="utf-8")
    media = project / "raw" / "keep.mov"
    media.write_bytes(b"customer media")

    old_tree = install_versioned(
        home, REPO_ROOT, channel="test", sha="old-build",
        built_at="2026-10-04")
    old_version = old_tree.name
    env = _proof_env(home, box)
    Path(env["HOME"]).mkdir(parents=True)
    Path(env["XDG_DATA_HOME"]).mkdir(parents=True)

    upgraded = _run_ren(home, box, "upgrade", str(REPO_ROOT))
    assert upgraded.returncode == 0, upgraded.stderr + upgraded.stdout
    new_version = _current_version(home)
    assert new_version and new_version != old_version
    assert _previous_version(home) == old_version

    failed = _run_ren(home, box, "upgrade", str(box / "missing-engine"))
    assert failed.returncode == 4, failed.stderr + failed.stdout
    assert _current_version(home) == new_version
    assert _previous_version(home) == old_version
    version = _run_ren(home, box, "--version")
    assert version.returncode == 0, version.stderr
    assert new_version in version.stdout

    rolled_back = _run_ren(home, box, "rollback")
    assert rolled_back.returncode == 0, rolled_back.stderr + rolled_back.stdout
    assert _current_version(home) == old_version
    assert _previous_version(home) == new_version
    assert project_yaml.read_text(encoding="utf-8") == (
        "name: Sample\nproject_format_version: 1\n")
    assert media.read_bytes() == b"customer media"
