"""Where the Node dependencies live is ONE answer, and absence is LOUD.

`library/tools/shared_environment.py` is the module under test.  These are
the four properties `docs/SHARED_ENVIRONMENT.md` claims, each asserted
against the real module rather than described:

  1. the store is outside every checkout, and baked to nobody's home;
  2. two checkouts sharing a lockfile share one tree, and two that do
     not can never see each other's - so staleness is unreachable;
  3. absence REFUSES, by name, with the command that fixes it;
  4. presence does not refuse - including through a symlink, which is
     the whole mechanism.

Nothing here installs anything, reaches the network, or touches the
store on this machine: every case builds its own `remotion-subtitles`
under `tmp_path` and points `PIPELINE_NODE_STORE` at another one.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import shared_environment as ne  # noqa: E402
from library.tools import remotion_batch  # noqa: E402


def make_checkout(tmp_path, lock: str = '{"lockfileVersion": 3}',
                  name: str = "remotion-subtitles") -> Path:
    """A checkout's renderer directory: tracked manifests, no deps."""
    directory = tmp_path / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "package.json").write_text(
        json.dumps({"name": name}), encoding="utf-8")
    (directory / "package-lock.json").write_text(lock, encoding="utf-8")
    return directory


# ── 1. the store is outside every checkout ───────────────────────────

def test_store_root_is_outside_the_checkout(monkeypatch):
    """The whole point: not one location per checkout.

    A store under the repository would die with a treehouse lane exactly
    as the ML venv would (docs/ML_ENVIRONMENT.md), which is the failure
    this design exists to avoid.
    """
    monkeypatch.delenv("PIPELINE_NODE_STORE", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    root = ne.store_root()
    assert REPO_ROOT not in root.parents and root != REPO_ROOT, (
        f"the dependency store resolved INSIDE the checkout at {root}")


#: The two home roots, ASSEMBLED rather than written.
#: `tests/test_tests_never_reach_real_projects.py` refuses a literal one
#: in any test module, and it is right to - including in the test that
#: exists to refuse them elsewhere.
HOME_ROOTS = tuple(f"{os.sep}{name}{os.sep}" for name in ("Users", "home"))


# ── 2. sharing is by lockfile, so staleness is unreachable ───────────

def test_two_checkouts_with_one_lockfile_share_one_entry(tmp_path):
    """The duplication this replaces: 585 MB per checkout, measured."""
    lane_a = make_checkout(tmp_path / "lane_a")
    lane_b = make_checkout(tmp_path / "lane_b")
    assert ne.store_key(lane_a) == ne.store_key(lane_b)
    assert ne.store_entry(lane_a) == ne.store_entry(lane_b)


def test_a_different_lockfile_can_never_reach_the_same_entry(tmp_path):
    """Staleness is not invalidated, it is unreachable.

    There is no cache-expiry step to forget, because a checkout whose
    dependency set changed asks for a different directory.
    """
    old = make_checkout(tmp_path / "old", lock='{"lockfileVersion": 3}')
    new = make_checkout(tmp_path / "new",
                        lock='{"lockfileVersion": 3, "packages": {}}')
    assert ne.store_key(old) != ne.store_key(new)
    assert ne.store_entry(old) != ne.store_entry(new)


def test_a_checkout_with_no_lockfile_refuses_rather_than_inventing_a_key(
        tmp_path):
    """A key with no lockfile behind it is a promise the store cannot keep."""
    directory = tmp_path / "remotion-subtitles"
    directory.mkdir()
    with pytest.raises(ne.NodeDependenciesMissing) as raised:
        ne.store_key(directory)
    assert "package-lock.json" in str(raised.value)


# ── 3. absence REFUSES, by name ──────────────────────────────────────


def test_the_refusal_names_the_store_entry_and_the_install_command(
        tmp_path, monkeypatch):
    """`npm install` was the remedy that produced the duplication.

    A message that repeats it teaches the defect, so the refusal names
    the one script and the one store entry instead.
    """
    monkeypatch.setenv("PIPELINE_NODE_STORE", str(tmp_path / "store"))
    monkeypatch.delenv("PIPELINE_NODE_MODULES", raising=False)
    directory = make_checkout(tmp_path / "lane")
    message = ne.missing_message(directory)
    assert ne.INSTALL_SCRIPT in message
    assert str(ne.store_entry(directory)) in message
    assert "docs/SHARED_ENVIRONMENT.md" in message
    assert "npm install" not in message


def test_the_refusal_distinguishes_unbound_from_uninstalled(
        tmp_path, monkeypatch):
    """Two different problems with two different fixes.

    Installed-but-unbound is one symlink away; uninstalled is an
    `npm ci`.  A message that conflated them would send a reader to
    re-download 285 MB they already have.
    """
    store = tmp_path / "store"
    monkeypatch.setenv("PIPELINE_NODE_STORE", str(store))
    monkeypatch.delenv("PIPELINE_NODE_MODULES", raising=False)
    directory = make_checkout(tmp_path / "lane")

    assert "No store entry exists" in ne.missing_message(directory)

    ne.store_node_modules(directory).mkdir(parents=True)
    assert "They ARE installed" in ne.missing_message(directory)


def test_a_batch_refuses_with_that_message_rather_than_reaching_node(
        tmp_path, monkeypatch):
    """The loud half, where it actually matters.

    Without this the absence reaches `node` and returns as
    ERR_MODULE_NOT_FOUND for a package no step named.
    """
    monkeypatch.setenv("PIPELINE_NODE_STORE", str(tmp_path / "store"))
    monkeypatch.delenv("PIPELINE_NODE_MODULES", raising=False)
    directory = make_checkout(tmp_path / "repo")
    (directory / remotion_batch.BATCH_SCRIPT).write_text("", encoding="utf-8")

    with pytest.raises(remotion_batch.RemotionBatchError) as raised:
        remotion_batch.render_batch(
            "Card", [remotion_batch.RenderJob({}, str(tmp_path / "o.png"))],
            str(tmp_path / "work"), repo_root=str(tmp_path / "repo"))
    assert ne.INSTALL_SCRIPT in str(raised.value)


# ── 4. presence does NOT refuse - including through the symlink ──────


def test_a_checkout_bound_by_symlink_is_present(tmp_path, monkeypatch):
    """The mechanism itself.

    58 tests and Node both ask `<remotion_dir>/node_modules`; a symlink
    is true for every one of them, which is why no test file needed an
    edit for the bytes to move out of the checkout.
    """
    monkeypatch.setenv("PIPELINE_NODE_STORE", str(tmp_path / "store"))
    monkeypatch.delenv("PIPELINE_NODE_MODULES", raising=False)
    directory = make_checkout(tmp_path / "lane")
    store_tree = ne.store_node_modules(directory)
    store_tree.mkdir(parents=True)
    (store_tree / "react").mkdir()

    (directory / "node_modules").symlink_to(store_tree)

    assert ne.dependencies_present(directory) is True
    assert os.path.isdir(directory / "node_modules"), (
        "the predicate every skip marker in this suite uses must follow "
        "the bind")
    assert (directory / "node_modules" / "react").is_dir()


def test_a_dangling_bind_is_absent_not_present(tmp_path, monkeypatch):
    """A symlink to a store entry that was cleaned up is NOT installed.

    `is_dir()` follows the link, so this answers False - which is the
    truth, because node would fail on it.
    """
    monkeypatch.setenv("PIPELINE_NODE_STORE", str(tmp_path / "store"))
    monkeypatch.delenv("PIPELINE_NODE_MODULES", raising=False)
    directory = make_checkout(tmp_path / "lane")
    (directory / "node_modules").symlink_to(tmp_path / "gone")

    assert ne.dependencies_present(directory) is False


# ── the location is computed ONCE ────────────────────────────────────


def _string_literals_outside_docs(path: Path) -> set:
    """Every string the module USES, ignoring prose about it.

    Docstrings and comments are how this repository records why a rule
    exists (AGENTS.md, "Maintaining this file"), so a check that read
    them would refuse the explanation of itself.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docs.add(id(body[0].value))
        # A bare string statement is this repo's attribute-docstring idiom.
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            docs.add(id(node.value))
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docs}


# ── the install script is the one way to fill the store ──────────────

def test_the_install_script_exists_and_reports_without_installing(tmp_path):
    """`--check` must answer without touching the network or the store."""
    script = REPO_ROOT / ne.INSTALL_SCRIPT
    assert script.is_file() and os.access(script, os.X_OK)

    directory = make_checkout(tmp_path / "lane")
    store = tmp_path / "store"
    result = subprocess.run(
        [str(script), "--check", str(directory)],
        cwd=REPO_ROOT, capture_output=True, encoding="utf-8", check=False,
        env=dict(os.environ, PIPELINE_NODE_STORE=str(store)))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "NODE DEPS: ABSENT" in result.stdout
    assert not store.exists(), "--check wrote into the store"

    (directory / "node_modules").mkdir()
    result = subprocess.run(
        [str(script), "--check", str(directory)],
        cwd=REPO_ROOT, capture_output=True, encoding="utf-8", check=False,
        env=dict(os.environ, PIPELINE_NODE_STORE=str(store)))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "NODE DEPS: PRESENT" in result.stdout


# ── the PYTHON half: one ladder, queried, never mirrored ─────────────
#
# The plugin is JavaScript inside Resolve's Electron host and cannot
# import `shared_environment`, so it ASKS the ladder instead of carrying
# it: `js/interpreter.js` runs
# `python3 -m library.tools.shared_environment --resolve-interpreter`.
# A mirrored rung literal in `main.js` with a test diffing the two was
# tried (PR 1141) and removed: a diffed duplicate is still a duplicate,
# stale the moment only one side is reinstalled. The no-mirror contract
# and both query directions live in
# `tests/test_plugin_interpreter_query.py`; what stays here is the
# ladder's own ordering and refusal.


def test_the_durable_venv_outranks_a_checkouts_own():
    """Order is the whole content of a ladder.

    `~/.local/share/vep/venv-py312` is the one that is built and
    verified (docs/ML_ENVIRONMENT.md); a checkout `.venv` may be a
    half-built experiment, so it is tried after, never before.
    """
    kinds = [kind for kind, _ in ne.INTERPRETER_CANDIDATES]
    assert kinds.index("vep_home") < kinds.index("repo")
    assert kinds.index("env") == 0, "an explicit override must win outright"


def test_a_checkout_with_no_venv_anywhere_refuses_naming_every_rung(
        tmp_path, monkeypatch):
    """The failure the plugin used to report was true and useless.

    It named `/usr/bin/python3` failing on an import, which says nothing
    about where the interpreter should have been.
    """
    monkeypatch.delenv("PIPELINE_PYTHON", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    checkout = tmp_path / "lane"
    checkout.mkdir()

    path, why_not = ne.python_interpreter(checkout)
    assert path == ""
    assert str(tmp_path / "vep" / ne.DURABLE_VENV_DIRNAME) in why_not
    assert str(checkout / ".venv") in why_not
    assert "docs/ML_ENVIRONMENT.md" in why_not


# ── the DeepFilterNet half: one binary per machine ────────────────────
#
# Fidelity rung R5d measured DeepFilterNet3 but could not install it:
# the shared venv is Python 3.12 with numpy 2.x and deepfilterlib
# ships no cp312 wheel. The engine runs the prebuilt Rust binary
# instead, installed once per machine - and these assert the
# resolution the probe and the doctor both read.


def test_deepfilter_binary_defaults_under_vep_home(tmp_path, monkeypatch):
    """No override: `<vep_home>/bin/deep-filter`, outside every checkout."""
    monkeypatch.delenv("PIPELINE_DEEPFILTER_BINARY", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    binary = ne.deepfilter_binary()
    assert binary == tmp_path / "vep" / "bin" / "deep-filter"
    assert REPO_ROOT not in binary.parents


def test_deepfilter_explicit_path_wins_outright(tmp_path, monkeypatch):
    """An escape hatch for a layout this module did not anticipate."""
    elsewhere = tmp_path / "elsewhere" / "deep-filter"
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    monkeypatch.setenv("PIPELINE_DEEPFILTER_BINARY", str(elsewhere))
    assert ne.deepfilter_binary() == elsewhere


def test_deepfilter_absence_names_the_install_command(tmp_path, monkeypatch):
    """Missing is a refusal with the fix, not a bare path."""
    monkeypatch.delenv("PIPELINE_DEEPFILTER_BINARY", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    usable, detail = ne.deepfilter_available()
    assert not usable
    assert ne.DEEPFILTER_INSTALL_SCRIPT in detail
    assert str(tmp_path / "vep" / "bin" / "deep-filter") in detail
    with pytest.raises(ne.DeepFilterEnvironmentMissing):
        ne.require_deepfilter()


def test_deepfilter_presence_is_the_executable_bit(tmp_path, monkeypatch):
    """An executable file at the resolved path is usable, by version."""
    monkeypatch.delenv("PIPELINE_DEEPFILTER_BINARY", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    binary = ne.deepfilter_binary()
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\necho deep_filter 0.5.6\n", encoding="utf-8")
    assert ne.deepfilter_available() == (False, ne.deepfilter_missing_message())
    binary.chmod(0o755)
    usable, detail = ne.deepfilter_available()
    assert usable and ne.DEEPFILTER_VERSION in detail
    assert ne.require_deepfilter() == binary


def test_the_deepfilter_install_script_reports_without_installing(tmp_path):
    """`--check` must answer without touching the network or the store."""
    script = REPO_ROOT / ne.DEEPFILTER_INSTALL_SCRIPT
    assert script.is_file() and os.access(script, os.X_OK)

    env = dict(os.environ, PIPELINE_VEP_HOME=str(tmp_path / "vep"))
    result = subprocess.run(
        [str(script), "--check"],
        cwd=REPO_ROOT, capture_output=True, encoding="utf-8", check=False,
        env=env)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "DEEPFILTERNET BINARY: ABSENT" in result.stdout
    assert not (tmp_path / "vep").exists(), "--check wrote into the store"

    binary = tmp_path / "vep" / "bin" / "deep-filter"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    binary.chmod(0o755)
    result = subprocess.run(
        [str(script), "--check"],
        cwd=REPO_ROOT, capture_output=True, encoding="utf-8", check=False,
        env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DEEPFILTERNET BINARY: PRESENT" in result.stdout


