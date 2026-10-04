"""Ren runs from an installed engine tree, not only from a checkout.

The defects this names (P0 "Create a real Ren distribution" / "Version
the actual product", verified against origin/main - every claim held:
`ren/__init__.py` defined the engine as the package's parent,
`ren.cli` refused without `manage_project.py` + `bin/vep` beside it,
`pyproject.toml` hardcoded `0.1.0` with no release path):

* the resolver must prefer an explicit `$REN_ENGINE_ROOT`, then the
  checkout, then the installed `<vep_home>/current` pointer - and an
  explicit root that holds no engine must refuse, not silently fall
  through to a different engine;
* `ren/engine_root.vep_home()` mirrors
  `shared_environment.vep_home()` without importing it (the resolver
  runs before `library/` is importable) - the two must agree, or the
  pointer lookup searches a home nothing installs into;
* the built tree must hold every entry a verb reads (a tree missing
  `bin/vep` built fine and then refused every non-builtin verb);
* `ren --version`, `ren doctor` and `ren new` must run off the built
  tree with no checkout on the path, and the created project must
  stamp the build that created it.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ren import engine_root
from ren.version import REQUIRES_PYTHON, __version__, version_string


def _fake_engine(path: Path) -> Path:
    """A minimal directory the resolver accepts as an engine root."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "manage_project.py").write_text("# fake engine\n", encoding="utf-8")
    (path / "library").mkdir(exist_ok=True)
    (path / "ren").mkdir(exist_ok=True)
    (path / "ren" / "cli.py").write_text("# fake front door\n", encoding="utf-8")
    (path / "bin").mkdir(exist_ok=True)
    (path / "bin" / "ren").write_text("#!/bin/sh\n", encoding="utf-8")
    (path / "bin" / "vep").write_text("#!/bin/sh\n", encoding="utf-8")
    return path


# ── Resolution order ──────────────────────────────────────────


def test_explicit_engine_root_wins_over_the_checkout(tmp_path, monkeypatch):
    fake = _fake_engine(tmp_path / "elsewhere")
    monkeypatch.setenv(engine_root.ENGINE_ENV, str(fake))
    assert engine_root.find_engine_root() == fake
    assert engine_root.require_engine_root() == fake


def test_an_explicit_root_without_an_engine_refuses_by_name(
        tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(engine_root.ENGINE_ENV, str(empty))
    assert engine_root.find_engine_root() is None
    with pytest.raises(RuntimeError, match="REN_ENGINE_ROOT"):
        engine_root.require_engine_root()
    with pytest.raises(RuntimeError, match="REN_ENGINE_ROOT"):
        engine_root.resolve_engine_root(REPO_ROOT)


def test_the_checkout_resolves_with_nothing_set(monkeypatch):
    monkeypatch.delenv(engine_root.ENGINE_ENV, raising=False)
    monkeypatch.delenv("PIPELINE_VEP_HOME", raising=False)
    found = engine_root.find_engine_root()
    assert found is not None
    assert engine_root.is_engine_root(found)
    assert (found / "manage_project.py").is_file()


def test_vep_home_mirrors_shared_environment(tmp_path, monkeypatch):
    """The resolver's home and the Node/venv halves' home must agree.

    The resolver cannot import `shared_environment` (it runs before
    `library/` is importable), so it mirrors the six lines. A mirror
    that drifted would aim the `current`-pointer lookup at a home
    nothing installs into.
    """
    from library.tools import shared_environment
    cases = [
        {"PIPELINE_VEP_HOME": str(tmp_path / "custom")},
        {"XDG_DATA_HOME": str(tmp_path / "xdg")},
        {},
    ]
    for case in cases:
        monkeypatch.delenv("PIPELINE_VEP_HOME", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        for key, value in case.items():
            monkeypatch.setenv(key, value)
        assert engine_root.vep_home() == shared_environment.vep_home()


def test_the_current_pointer_resolves_and_a_dangling_one_does_not(
        tmp_path, monkeypatch):
    home = tmp_path / "vep"
    versioned = home / "versions" / "9.9.9"
    _fake_engine(versioned)
    (home / "versions").mkdir(parents=True, exist_ok=True)
    link = home / "current"
    link.symlink_to(Path("versions") / "9.9.9")
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(home))
    monkeypatch.delenv(engine_root.ENGINE_ENV, raising=False)
    assert engine_root.installed_root() == versioned.resolve()

    link.unlink()
    link.symlink_to(Path("versions") / "0.0.0-missing")
    assert engine_root.installed_root() is None


# ── One version source ────────────────────────────────────────


def test_the_version_is_stated_once():
    """`ren/version.py` is the source; `pyproject.toml` reads it by attr.

    A hardcoded `[project] version` beside the module would let the
    installed metadata and `ren --version` disagree.
    """
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = { attr = "ren.version.__version__" }' in text
    project_section = text.split("[project]")[1].split("[", 1)[0]
    declarations = [
        line.split("#")[0].strip() for line in project_section.splitlines()
    ]
    assert not any(line.startswith("version ") or line.startswith("version=")
                   for line in declarations), (
        "a second version declaration in [project]")


def test_requires_python_matches_pyproject():
    from ren.version import REQUIRES_PYTHON
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'requires-python = "{REQUIRES_PYTHON}"' in text


# ── The built tree ────────────────────────────────────────────


def _build_tree(dest: Path) -> Path:
    from ren.package_engine import build_engine_tree
    return build_engine_tree(REPO_ROOT, dest, channel="test",
                             sha="abc123def456", built_at="2026-10-04")


def test_the_built_tree_holds_every_entry_a_verb_reads(tmp_path):
    """A tree missing `bin/vep` once built fine, then refused every
    non-builtin verb - so the manifest is asserted, not assumed."""
    dest = _build_tree(tmp_path / "engine")
    for sentinel in engine_root.SENTINELS:
        assert (dest / sentinel).exists(), f"built tree lacks {sentinel}"
    # Process manifests, schemas, presets, renderer source, skills,
    # requirements and the runtime assets refusals name.
    assert (dest / "library" / "processes" / "edit_video" / "dag.json").is_file()
    assert (dest / "library" / "processes" / "reels" / "dag.json").is_file()
    assert (dest / "library" / "schemas" / "project_config.py").is_file()
    assert (dest / "library" / "presets").is_dir()
    assert (dest / "library" / "skills").is_dir()
    assert (dest / ".agents" / "skills").is_dir()
    assert (dest / "remotion-subtitles" / "src").is_dir()
    assert (dest / "remotion-subtitles" / "package-lock.json").is_file()
    assert (dest / "requirements" / "core.txt").is_file()
    assert (dest / "scripts" / "install_node_deps.sh").is_file()
    assert (dest / "THIRD_PARTY_NOTICES").is_file()
    assert os.access(dest / "bin" / "vep", os.X_OK)
    assert os.access(dest / "bin" / "ren", os.X_OK)
    # Generated bytes stay out: caches, bytecode, the shared node store.
    assert not (dest / "remotion-subtitles" / "node_modules").exists()
    assert not list(dest.rglob("__pycache__"))
    assert not list(dest.rglob("*.pyc"))
    # The packaged build record answers `ren --version` off the tree.
    record = (dest / "ren" / "_build.py").read_text(encoding="utf-8")
    assert "BUILD_CHANNEL = 'test'" in record


def test_rebuilding_a_version_directory_refuses(tmp_path):
    from ren import package_engine
    home = tmp_path / "vep"
    build_id = f"{__version__}+abc123.test.2026-10-04"
    first = package_engine.install_versioned(
        home, REPO_ROOT, channel="test", sha="abc123",
        built_at="2026-10-04")
    assert first == home / "versions" / build_id
    assert os.readlink(home / "current") == f"versions/{build_id}"
    record = (first / "ren" / "_build.py").read_text(encoding="utf-8")
    with pytest.raises(RuntimeError, match="immutable"):
        package_engine.install_versioned(
            home, REPO_ROOT, channel="test", sha="abc123",
            built_at="2026-10-04")
    assert os.readlink(home / "current") == f"versions/{build_id}"
    assert (first / "ren" / "_build.py").read_text(
        encoding="utf-8") == record
    assert not list((home / "versions").glob(".*.staging"))
    with pytest.raises(RuntimeError, match="no complete engine"):
        package_engine.switch_current(home, "0.0.0-missing")


def test_failed_version_build_keeps_the_active_tree(tmp_path, monkeypatch):
    from ren import package_engine
    home = tmp_path / "vep"
    _fake_engine(home / "versions" / "old")
    (home / "current").symlink_to(Path("versions") / "old")

    def fail_build(*args, **kwargs):
        raise RuntimeError("simulated build failure")

    monkeypatch.setattr(package_engine, "build_engine_tree", fail_build)
    with pytest.raises(RuntimeError, match="simulated build failure"):
        package_engine.install_versioned(
            home, REPO_ROOT, channel="test", sha="abc123",
            built_at="2026-10-04")

    assert os.readlink(home / "current") == "versions/old"
    build_id = f"{__version__}+abc123.test.2026-10-04"
    assert not (home / "versions" / build_id).exists()
    assert not list((home / "versions").glob(".*.staging"))


def test_build_metadata_cannot_escape_the_version_directory(tmp_path):
    from ren import package_engine
    home = tmp_path / "vep"
    with pytest.raises(ValueError, match="metadata channel"):
        package_engine.install_versioned(
            home, REPO_ROOT, channel="../../outside", sha="abc123",
            built_at="2026-10-04")
    assert not home.exists()


# ── Doctor and projects report the build ──────────────────────


def test_doctor_json_carries_the_build_identity(monkeypatch, capsys):
    from ren import doctor
    monkeypatch.setattr(doctor, "run_checks", lambda probe=None, needs=None: [])
    assert doctor.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ren"]["version"] == __version__
    assert report["ren"]["requires_python"] == REQUIRES_PYTHON
    assert report["ren"]["engine_root"] == str(
        engine_root.require_engine_root())


def test_a_new_project_stamps_the_build_that_created_it(tmp_path):
    from library.schemas.project_config import load_project_config
    from library.tools.project_registry import create_project
    config = create_project("stamped", name="Stamped",
                            root=tmp_path / "projects")
    text = (tmp_path / "projects" / "stamped" / "project.yaml").read_text(
        encoding="utf-8")
    assert f"ren_version: {version_string()}" in text
    assert load_project_config(
        tmp_path / "projects" / "stamped" / "project.yaml").ren_version == \
        config.ren_version != ""


# ── The no-checkout proof ─────────────────────────────────────
#
# Build a tree into a temp dir, then run `ren --version`, `ren doctor`
# and `ren new` off it with the checkout nowhere on the path. A tree
# that only worked beside its checkout would fail every one.


def _proof_env(tree: Path, box: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tree)
    env["REN_ENGINE_ROOT"] = str(tree)
    env["REN_CONFIG"] = str(box / "config.env")
    env["PIPELINE_PROJECTS_ROOT"] = str(box / "projects")
    env["PIPELINE_VEP_HOME"] = str(box / "vep")
    env["PIPELINE_PYTHON"] = sys.executable
    env["PATH"] = os.pathsep.join(
        [str(tree / "bin"), env.get("PATH", "")])
    return env


def _run_box(args: list, tree: Path, box: Path, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ren", *args],
        capture_output=True, encoding="utf-8", timeout=300, check=False,
        cwd=str(cwd), env=_proof_env(tree, box))


def test_version_doctor_and_new_run_with_no_checkout_on_the_path(tmp_path):
    tree = _build_tree(tmp_path / "engine")
    box = tmp_path / "box"
    box.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    checkout = str(REPO_ROOT)

    assert checkout not in _proof_env(tree, box)["PYTHONPATH"].split(os.pathsep)
    assert checkout not in _proof_env(tree, box)["PATH"].split(os.pathsep)

    done = _run_box(["--version"], tree, box, work)
    assert done.returncode == 0, done.stderr
    assert f"(engine {tree})" in done.stdout
    assert checkout not in done.stdout

    done = _run_box(["doctor", "--json"], tree, box, work)
    assert done.returncode in (0, 1), done.stderr  # 1: a bare box fails required lines
    report = json.loads(done.stdout)
    assert report["ren"]["engine_root"] == str(tree)
    assert report["ren"]["version"] == __version__
    assert report["ren"]["channel"] == "test"
    assert report["ren"]["requires_python"] == REQUIRES_PYTHON
    assert len(report["checks"]) > 0

    done = _run_box(["new", "proof-proj", "--name", "Proof",
                     "--non-interactive"], tree, box, work)
    assert done.returncode == 0, done.stderr + done.stdout
    yaml_path = box / "projects" / "proof-proj" / "project.yaml"
    assert yaml_path.is_file()
    text = yaml_path.read_text(encoding="utf-8")
    assert "ren_version: " in text
    assert __version__ in text
