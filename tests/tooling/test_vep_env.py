"""The environment hook and the `bin/vep` wrapper do what lanes did by hand.

Six lanes re-typed a ~200-character
`export RESOLVE_SCRIPT_API=... RESOLVE_SCRIPT_LIB=... &&` preamble 239
times and a literal venv path 379 times (measured 2026-09-18 from 2,016
recorded lane tool calls). Two artefacts replace both, and they are not
collapsed:

- `.opencode/plugins/vep-env.js` is the HOOK: it injects the two Resolve
  variables and `PIPELINE_PYTHON` into every shell without being asked.
- `bin/vep` is the SCRIPT: a lane calls it deliberately to run a Python
  entry point under the ladder-resolved interpreter with no preamble.

Per AGENTS.md 10.4 this proves BOTH directions at each level: the
resolved interpreter is served when present, and absence REFUSES - never
a silent stock-interpreter fallback, which fails forty seconds inside a
step with a traceback about a package nobody mentioned.

Nothing here writes to a real install location: every resolution runs
with fixture homes under `tmp_path`, and the one test that reads the
real durable venv is read-only and skips where none is built.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WRAPPER = REPO_ROOT / "bin" / "vep"
PLUGIN = REPO_ROOT / ".opencode" / "plugins" / "vep-env.js"

EXPECTED_API = ("/Library/Application Support/Blackmagic Design"
                "/DaVinci Resolve/Developer/Scripting")
EXPECTED_LIB = ("/Applications/DaVinci Resolve/DaVinci Resolve.app"
                "/Contents/Libraries/Fusion/fusionscript.so")

NO_NODE = "node is not on PATH, so the plugin's JavaScript cannot be run here"
NO_VEHICLE = "no python3 vehicle on PATH to serve as the ladder query here"
NO_DURABLE = "no durable ML venv on this machine to prove the real resolution with"
HAS_REPO_VENV = ("this checkout carries its own .venv, so the ladder "
                 "legitimately answers it and the refusal case cannot be staged here")

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)


def _make_exe(path: Path) -> Path:
    """Something the ladder's exists-and-executable predicate accepts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _vehicle_dir(tmp_path: Path) -> Path:
    """A bin dir with a `python3` vehicle, so the ladder can be asked
    however bare the ambient PATH is."""
    bindir = tmp_path / "vehicle-bin"
    bindir.mkdir(exist_ok=True)
    target = bindir / "python3"
    if not target.exists():
        target.symlink_to(Path(sys.executable))
    return bindir


def _env(tmp_path: Path, extra: dict | None = None) -> dict:
    """The environment a wrapper or hook runs under: fixture vep home,
    no override, a python3 vehicle on PATH."""
    env = dict(os.environ)
    env.pop("PIPELINE_PYTHON", None)
    env["PIPELINE_VEP_HOME"] = str(tmp_path / "vep")
    env["PATH"] = (str(_vehicle_dir(tmp_path))
                   + os.pathsep + env.get("PATH", ""))
    if extra:
        env.update(extra)
    return env


def _run_wrapper(args: list, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([str(WRAPPER), *args], capture_output=True,
                          encoding="utf-8", env=env, timeout=120, check=False)


# ── the wrapper reports the resolved environment ───────────────────

def test_the_wrapper_reports_the_ladder_answer_with_no_preamble(tmp_path):
    """Cold start: none of the three set, no arguments - the wrapper
    prints all three resolved values and exits 0. The interpreter is a
    fixture durable venv, so this proves resolution, not the machine."""
    durable = _make_exe(tmp_path / "vep" / "venv-py312" / "bin" / "python3")
    proc = _run_wrapper([], _env(tmp_path))
    assert proc.returncode == 0, proc.stderr
    lines = dict(line.split("=", 1) for line in proc.stdout.splitlines()
                 if "=" in line)
    assert lines["RESOLVE_SCRIPT_API"] == EXPECTED_API
    assert lines["RESOLVE_SCRIPT_LIB"] == EXPECTED_LIB
    assert lines["PIPELINE_PYTHON"] == str(durable)


def test_an_explicit_choice_wins_in_the_wrapper(tmp_path):
    """Rung 1 outright: PIPELINE_PYTHON naming an executable is served
    as-is, and the Resolve defaults still land beside it."""
    chosen = _make_exe(tmp_path / "mine" / "bin" / "python3")
    proc = _run_wrapper([], _env(tmp_path,
                                 {"PIPELINE_PYTHON": str(chosen)}))
    assert proc.returncode == 0, proc.stderr
    assert f"PIPELINE_PYTHON={chosen}" in proc.stdout
    assert f"RESOLVE_SCRIPT_API={EXPECTED_API}" in proc.stdout


def test_explicit_resolve_paths_are_never_overwritten(tmp_path):
    """A non-standard Resolve install stays addressable: set values
    pass through untouched."""
    _make_exe(tmp_path / "vep" / "venv-py312" / "bin" / "python3")
    proc = _run_wrapper([], _env(tmp_path, {
        "RESOLVE_SCRIPT_API": "/opt/resolve/Scripting",
        "RESOLVE_SCRIPT_LIB": "/opt/resolve/fusionscript.so"}))
    assert proc.returncode == 0, proc.stderr
    assert "RESOLVE_SCRIPT_API=/opt/resolve/Scripting" in proc.stdout
    assert "RESOLVE_SCRIPT_LIB=/opt/resolve/fusionscript.so" in proc.stdout


def test_the_wrapper_execs_arguments_under_the_resolved_interpreter(tmp_path):
    """`bin/vep <py-args>` replaces `<venv-python> <py-args>`: the args
    run under the resolved interpreter, which the shim observes."""
    shim = tmp_path / "shim" / "python3"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.write_text("#!/bin/sh\necho \"INTERP=$0 ARGS=$*\"\n", encoding="utf-8")
    shim.chmod(0o755)
    proc = _run_wrapper(["manage_project.py", "list"],
                        _env(tmp_path, {"PIPELINE_PYTHON": str(shim)}))
    assert proc.returncode == 0, proc.stderr
    assert f"INTERP={shim} ARGS=manage_project.py list" in proc.stdout


# ── the wrapper refuses rather than falling back ───────────────────

def test_the_wrapper_refuses_when_no_interpreter_exists(tmp_path):
    """Empty vep home, no override, no checkout venv: exit 3 naming the
    ladder - and NOT exit 0 from a stock interpreter running the args."""
    if (REPO_ROOT / ".venv" / "bin" / "python3").is_file():
        pytest.skip(reason=HAS_REPO_VENV)
    proc = _run_wrapper(["-c", "print('SHOULD NEVER RUN')"],
                        _env(tmp_path))
    assert proc.returncode == 3, (
        f"refusal must exit 3, got {proc.returncode}: {proc.stdout}{proc.stderr}")
    assert "SHOULD NEVER RUN" not in proc.stdout
    assert "ML stack" in proc.stderr






# ── the hook injects the environment without being asked ───────────

def _run_hook(env: dict, cwd: str) -> dict:
    """`shell.env` answering, run under plain node like the workflow
    plugin's own JavaScript tests."""
    node = shutil.which("node")
    assert node is not None, NO_NODE
    script = (
        "import(%s).then(async (m) => {"
        "  const plugin = await m.VepEnv({ directory: %s });"
        "  const output = { env: {} };"
        "  await plugin['shell.env']({ cwd: %s }, output);"
        "  console.log(JSON.stringify(output.env));"
        "});" % (json.dumps(str(PLUGIN)), json.dumps(cwd), json.dumps(cwd)))
    proc = subprocess.run([node, "--input-type=module", "-e", script],
                          capture_output=True, encoding="utf-8", env=env,
                          timeout=120, check=False)
    assert proc.returncode == 0, (
        "node itself failed: %s%s" % (proc.stdout, proc.stderr))
    return json.loads(proc.stdout)


@needs_node
def test_the_hook_injects_all_three_without_being_asked(tmp_path):
    """The hook's whole reason to exist: a shell that set nothing gets
    the two Resolve variables and the override interpreter."""
    chosen = _make_exe(tmp_path / "mine" / "bin" / "python3")
    injected = _run_hook(_env(tmp_path, {"PIPELINE_PYTHON": str(chosen)}),
                         str(REPO_ROOT))
    assert injected["RESOLVE_SCRIPT_API"] == EXPECTED_API
    assert injected["RESOLVE_SCRIPT_LIB"] == EXPECTED_LIB
    assert injected["PIPELINE_PYTHON"] == str(chosen)


@needs_node
def test_the_hook_asks_the_ladder_when_nothing_is_set(tmp_path):
    """No override: the hook queries `shared_environment` and serves a
    fixture durable venv - the ladder, not a mirrored path."""
    durable = _make_exe(tmp_path / "vep" / "venv-py312" / "bin" / "python3")
    injected = _run_hook(_env(tmp_path), str(REPO_ROOT))
    assert injected["PIPELINE_PYTHON"] == str(durable)
    assert injected["RESOLVE_SCRIPT_API"] == EXPECTED_API


@needs_node
def test_the_hook_leaves_the_interpreter_unset_when_nothing_resolves(tmp_path):
    """Empty vep home, no override, no checkout venv: the Resolve
    constants still land, but no stock interpreter is served as one."""
    if (REPO_ROOT / ".venv" / "bin" / "python3").is_file():
        pytest.skip(reason=HAS_REPO_VENV)
    injected = _run_hook(_env(tmp_path), str(REPO_ROOT))
    assert injected["RESOLVE_SCRIPT_API"] == EXPECTED_API
    assert injected["RESOLVE_SCRIPT_LIB"] == EXPECTED_LIB
    assert "PIPELINE_PYTHON" not in injected


@needs_node
def test_the_hook_carries_no_interpreter_ladder_of_its_own():
    """A ladder duplicated in a second language drifts; the hook must
    query `shared_environment` instead. It therefore names no venv and
    no candidate list."""
    text = PLUGIN.read_text(encoding="utf-8")
    assert "venv-py312" not in text
    assert "INTERPRETER_CANDIDATES" not in text
    assert "shared_environment" in text


@needs_node
def test_the_hook_has_no_stock_interpreter_fallback():
    """Serving the ambient python3 as PIPELINE_PYTHON would reintroduce
    the exact trap this exists to close."""
    text = PLUGIN.read_text(encoding="utf-8")
    assert "/usr/bin/python3" not in text
