"""The one command that puts a script in Workspace > Scripts.

Two things are checked, and the second is the one that matters most: that
the installer is the ONLY thing in this repository that writes into the
captain's application support folder.  A script appearing there as a side
effect of running the pipeline would be a change to their application
that nobody asked for.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / "scripts" / "install_resolve_scripts.sh"
SOURCE_DIR = REPO / "resolve_scripts"
UTILITY = Path(
    "Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Fusion/Scripts/Utility"
)


def _run(home, *args):
    env = dict(os.environ, HOME=str(home))
    return subprocess.run(
        ["bash", str(INSTALLER), *args], env=env, capture_output=True,
        encoding="utf-8", check=False,
    )


def _entry_points():
    return sorted(SOURCE_DIR.glob("*.py"))


def test_there_is_something_to_install():
    assert _entry_points(), (
        f"{SOURCE_DIR} carries no entry point, so the installer installs "
        f"nothing and the button is unreachable"
    )


def test_every_entry_point_is_stampable_and_parses():
    """The installer replaces one exact line; a file without it is refused."""
    for path in _entry_points():
        source = path.read_text(encoding="utf-8")
        assert 'REPO_ROOT = ""' in source, (
            f"{path.name} has no REPO_ROOT line for the installer to stamp"
        )
        ast.parse(source, str(path))


def test_the_dry_run_writes_nothing(tmp_path):
    result = _run(tmp_path, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "would install" in result.stdout
    assert not (tmp_path / UTILITY).exists()


def test_install_puts_the_script_where_workspace_scripts_looks(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr

    installed = sorted((tmp_path / UTILITY).glob("*.py"))
    assert [p.name for p in installed] == [p.name for p in _entry_points()]

    for path in installed:
        source = path.read_text(encoding="utf-8")
        ast.parse(source, str(path))
        # Stamped, so the copy imports the repository rather than a copy
        # of the implementation that could drift from it.
        assert f"REPO_ROOT = {str(REPO)!r}" in source
        assert 'REPO_ROOT = ""' not in source


def test_the_stamped_copy_finds_the_repository(tmp_path):
    """What the stamp is FOR: the copy has to resolve `library/`."""
    assert _run(tmp_path).returncode == 0
    installed = min((tmp_path / UTILITY).glob("*.py"))
    loader = (
        "import importlib.util,sys;"
        "s=importlib.util.spec_from_file_location('e',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "print(m._repo_root())"
    )
    found = subprocess.run(
        [sys.executable, "-c", loader, str(installed)],
        capture_output=True, encoding="utf-8", check=False,
    )
    assert found.returncode == 0, found.stderr
    assert found.stdout.strip() == str(REPO)


def test_uninstall_takes_it_out_again(tmp_path):
    assert _run(tmp_path).returncode == 0
    assert sorted((tmp_path / UTILITY).glob("*.py"))
    result = _run(tmp_path, "--uninstall")
    assert result.returncode == 0, result.stderr
    assert not sorted((tmp_path / UTILITY).glob("*.py"))


def test_an_unknown_flag_is_refused_rather_than_treated_as_install(tmp_path):
    result = _run(tmp_path, "--please-just-do-it")
    assert result.returncode == 64
    assert not (tmp_path / UTILITY).exists()


# ── Nothing else may write there ────────────────────────────────────


@pytest.mark.parametrize("suffix", [".py", ".sh"])
def test_only_the_installer_names_the_scripts_folder(suffix):
    needle = "Fusion/Scripts"
    offenders = []
    for path in REPO.rglob(f"*{suffix}"):
        if any(part in (".git", "node_modules", ".venv", "__pycache__")
               for part in path.parts):
            continue
        if path in (INSTALLER, Path(__file__).resolve()) \
                or path.parent == SOURCE_DIR:
            continue
        if needle in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(REPO)))
    assert not offenders, (
        "these name the captain's Workspace > Scripts folder:\n  "
        + "\n  ".join(offenders) + "\n"
        "Installing a script there is one deliberate command "
        "(scripts/install_resolve_scripts.sh), never a side effect."
    )
