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


def test_store_root_follows_the_machines_own_data_dir(monkeypatch, tmp_path):
    """Derived, never baked - so it installs on any machine."""
    monkeypatch.delenv("PIPELINE_NODE_STORE", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert ne.store_root() == tmp_path / "xdg" / "vep" / "node"

    monkeypatch.setenv("PIPELINE_NODE_STORE", str(tmp_path / "elsewhere"))
    assert ne.store_root() == tmp_path / "elsewhere"


#: The two home roots, ASSEMBLED rather than written.
#: `tests/test_tests_never_reach_real_projects.py` refuses a literal one
#: in any test module, and it is right to - including in the test that
#: exists to refuse them elsewhere.
HOME_ROOTS = tuple(f"{os.sep}{name}{os.sep}" for name in ("Users", "home"))


def test_no_home_directory_is_baked_into_the_module():
    """A path only one machine has is not a default.

    The store must resolve on the next machine, which is the captain's
    whole directive.  `store_root()` derives it; a literal here would be
    the defect wearing the fix's clothes.
    """
    source = (REPO_ROOT / "library" / "tools"
              / "shared_environment.py").read_text(encoding="utf-8")
    code = [line for line in source.splitlines()
            if not line.lstrip().startswith("#")]
    offenders = [line for line in code
                 if any(root in line for root in HOME_ROOTS)]
    assert not offenders, (
        "an absolute home directory is baked into the resolution: "
        + "; ".join(offenders))


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


def test_the_key_is_the_lockfile_not_the_manifest(tmp_path):
    """A range in package.json resolves two ways; an entry names one."""
    one = make_checkout(tmp_path / "one")
    two = make_checkout(tmp_path / "two")
    (two / "package.json").write_text(
        json.dumps({"name": "remotion-subtitles", "dependencies":
                    {"react": "^19.0.0"}}), encoding="utf-8")
    assert ne.store_key(one) == ne.store_key(two)


def test_a_checkout_with_no_lockfile_refuses_rather_than_inventing_a_key(
        tmp_path):
    """A key with no lockfile behind it is a promise the store cannot keep."""
    directory = tmp_path / "remotion-subtitles"
    directory.mkdir()
    with pytest.raises(ne.NodeDependenciesMissing) as raised:
        ne.store_key(directory)
    assert "package-lock.json" in str(raised.value)


# ── 3. absence REFUSES, by name ──────────────────────────────────────

def test_absence_raises_rather_than_returning_a_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_NODE_STORE", str(tmp_path / "store"))
    monkeypatch.delenv("PIPELINE_NODE_MODULES", raising=False)
    directory = make_checkout(tmp_path / "lane")
    assert ne.dependencies_present(directory) is False
    with pytest.raises(ne.NodeDependenciesMissing):
        ne.require_dependencies(directory)


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


def test_the_environment_requirement_refuses_and_names_the_store():
    """`env.remotion_installed` is the prerequisite layer's half."""
    from library.tools import requirements

    requirement = next(r for r in requirements.ENVIRONMENT
                       if r.name == "env.remotion_installed")
    refused = requirement.check(requirement.refuting_context())
    assert refused.satisfied is False
    assert ne.INSTALL_SCRIPT in refused.reason
    assert "SHARED_ENVIRONMENT.md" in refused.reason
    assert requirement.check(requirement.satisfying_context()).satisfied


# ── 4. presence does NOT refuse - including through the symlink ──────

def test_a_real_node_modules_does_not_fire_the_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_NODE_STORE", str(tmp_path / "store"))
    monkeypatch.delenv("PIPELINE_NODE_MODULES", raising=False)
    directory = make_checkout(tmp_path / "lane")
    (directory / "node_modules").mkdir()

    assert ne.dependencies_present(directory) is True
    assert ne.require_dependencies(directory) == directory / "node_modules"


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


def test_pipeline_node_modules_overrides_outright(tmp_path, monkeypatch):
    """The escape hatch, for a layout this module did not anticipate."""
    elsewhere = tmp_path / "somewhere" / "node_modules"
    elsewhere.mkdir(parents=True)
    monkeypatch.setenv("PIPELINE_NODE_MODULES", str(elsewhere))
    directory = make_checkout(tmp_path / "lane")
    assert ne.node_modules(directory) == elsewhere
    assert ne.dependencies_present(directory) is True


# ── the location is computed ONCE ────────────────────────────────────

def test_paths_and_remotion_batch_do_not_compute_the_location_separately():
    """Two derivations of one path is one of them to forget.

    `remotion_batch` derived it from `__file__` while `paths` derived it
    from `PILOT_ROOT`; neither followed an override, because there was
    none to follow.
    """
    batch = (REPO_ROOT / "library" / "tools"
             / "remotion_batch.py").read_text(encoding="utf-8")
    assert 'root / REMOTION_DIRNAME' not in batch
    assert "_node_env.remotion_dir(repo_root)" in batch

    # Every module that LOCATES the renderer at runtime, including the
    # two steps that actually launch it. A step deriving its own would
    # not follow `PIPELINE_REMOTION_DIR`, which would make the override
    # a half-truth - true for the prerequisite check and false for the
    # render it gates.
    for module in (
        "library/tools/paths.py",
        "library/tools/requirements.py",
        "library/steps/step_4_05_render_subtitles/step.py",
        "library/steps/step_4_06_render_motion_graphics/post_bridge.py",
    ):
        spelled = _string_literals_outside_docs(REPO_ROOT / module)
        assert ne.REMOTION_DIRNAME not in spelled, (
            f"{module} spells the renderer directory itself instead of "
            f"reading it from shared_environment")


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


def test_every_reader_follows_one_override(tmp_path):
    """Set the override once; all three agree, in a real interpreter."""
    elsewhere = make_checkout(tmp_path / "custom", name="renderer")
    env = dict(os.environ, PIPELINE_REMOTION_DIR=str(elsewhere))
    out = subprocess.run(
        [sys.executable, "-c", (
            "from library.tools.paths import REMOTION_DIR\n"
            "from library.tools.remotion_batch import remotion_dir\n"
            "from library.tools import requirements, shared_environment\n"
            "print(REMOTION_DIR)\nprint(remotion_dir())\n"
            "print(requirements.REMOTION_DIR)\n"
            "print(shared_environment.remotion_dir())")],
        cwd=REPO_ROOT, env=env, capture_output=True,
        encoding="utf-8", check=True)
    reported = out.stdout.split()
    assert reported == [str(elsewhere)] * 4, reported


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


# ── the PYTHON half: one ladder, in two languages ────────────────────

PLUGIN_MAIN = (REPO_ROOT / "resolve_workflow_integration"
               / "com.videoeditingpilot.vep" / "main.js")


def _js_interpreter_candidates() -> list:
    """The plugin's ladder, read out of its own source as data.

    Parsed rather than trusted: the point of the check is that the two
    literals are the SAME ladder, and a test that read a comment saying
    so would pass while they diverged.
    """
    import re

    source = PLUGIN_MAIN.read_text(encoding="utf-8")
    block = re.search(r"const INTERPRETER_CANDIDATES = \[(.*?)\n\];",
                      source, re.S)
    assert block, "main.js has no INTERPRETER_CANDIDATES array to compare"
    return [tuple(pair) for pair in re.findall(
        r"\[\s*'([^']+)'\s*,\s*'([^']+)'\s*\]", block.group(1))]


def test_the_plugin_carries_the_same_interpreter_ladder():
    """The one caller that CANNOT import the module still obeys it.

    `main.js` is JavaScript inside Resolve's Electron host, so it cannot
    read `shared_environment` at all - which is exactly how it came to
    resolve `<REPO_ROOT>/.venv/bin/python3` long after no checkout on
    this machine had one.
    """
    assert _js_interpreter_candidates() == list(ne.INTERPRETER_CANDIDATES)


def test_the_plugin_has_no_stock_interpreter_fallback():
    """`/usr/bin/python3` is not a fallback; it is a slower failure.

    It carries none of whisperx, mlx_vlm or torch, so the bridge died
    inside a step's import reporting a package nobody mentioned, on the
    one surface the captain actually presses.
    """
    source = PLUGIN_MAIN.read_text(encoding="utf-8")
    code = [line for line in source.splitlines()
            if not line.lstrip().startswith("//")]
    offenders = [line for line in code if "/usr/bin/python3" in line]
    assert not offenders, (
        "the plugin still falls back to a stock interpreter: "
        + "; ".join(offenders))


def test_the_durable_venv_outranks_a_checkouts_own():
    """Order is the whole content of a ladder.

    `~/.local/share/vep/venv-py312` is the one that is built and
    verified (docs/ML_ENVIRONMENT.md); a checkout `.venv` may be a
    half-built experiment, so it is tried after, never before.
    """
    kinds = [kind for kind, _ in ne.INTERPRETER_CANDIDATES]
    assert kinds.index("vep_home") < kinds.index("repo")
    assert kinds.index("env") == 0, "an explicit override must win outright"


def test_the_interpreter_resolves_to_the_durable_venv(tmp_path, monkeypatch):
    """The rung that exists on this machine, and the one that does not."""
    monkeypatch.delenv("PIPELINE_PYTHON", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))

    durable = tmp_path / "vep" / ne.DURABLE_VENV_DIRNAME / "bin" / "python3"
    durable.parent.mkdir(parents=True)
    durable.write_text("#!/bin/sh\n", encoding="utf-8")
    durable.chmod(0o755)

    checkout = tmp_path / "lane"
    (checkout / ".venv" / "bin").mkdir(parents=True)
    own = checkout / ".venv" / "bin" / "python3"
    own.write_text("#!/bin/sh\n", encoding="utf-8")
    own.chmod(0o755)

    path, why_not = ne.python_interpreter(checkout)
    assert path == str(durable), "the checkout's own venv outranked the durable one"
    assert why_not == ""


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


def test_an_explicit_interpreter_wins_outright(tmp_path, monkeypatch):
    chosen = tmp_path / "chosen"
    chosen.write_text("#!/bin/sh\n", encoding="utf-8")
    chosen.chmod(0o755)
    monkeypatch.setenv("PIPELINE_PYTHON", str(chosen))
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    assert ne.python_interpreter(tmp_path)[0] == str(chosen)


def test_the_panel_reads_the_ladder_rather_than_its_own_venv_path():
    """The panel was the first place to get this right and the last to move.

    It refused correct machines: a lane with no `.venv` was told to make
    one, when the interpreter it needed already existed outside every
    checkout.
    """
    from library.tools.panel import run_view

    source = (REPO_ROOT / "library" / "tools" / "panel"
              / "run_view.py").read_text(encoding="utf-8")
    assert "shared_environment.python_interpreter" in source
    assert run_view.pipeline_interpreter.__module__ == "library.tools.panel.run_view"


def test_vep_home_is_one_root_both_halves_sit_under(monkeypatch, tmp_path):
    """The Node store and the ML venv are siblings, by construction.

    Two roots would be two things to relocate and one to forget.
    """
    monkeypatch.delenv("PIPELINE_NODE_STORE", raising=False)
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))
    assert ne.store_root().parent == ne.vep_home()
    assert (ne.vep_home() / ne.DURABLE_VENV_DIRNAME).parent == ne.vep_home()
