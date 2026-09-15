"""The plugin asks the ONE interpreter ladder; it carries none of its own.

`library/tools/shared_environment.py` owns the ladder
(`INTERPRETER_CANDIDATES`, `interpreter_candidates()`,
`python_interpreter()`). The Workflow Integration plugin is JavaScript
inside Resolve's Electron host and cannot import that module, so it
ASKS it instead: `js/interpreter.js` shells out to any `python3` to run

    python3 -m library.tools.shared_environment --resolve-interpreter \
        --repo-root <REPO_ROOT>

and launches the bridge with the answered path. The bootstrap never
executes pipeline code - answering evaluates only stdlib path
predicates - so its own version cannot corrupt the answer, and every
other outcome refuses loudly where the message is read.

Per AGENTS.md 10.4 this proves BOTH directions, at the plugin level:
finding the durable interpreter when it is present, and a concrete
actionable refusal - never a silent stock-interpreter fallback - when
nothing supported exists anywhere.

Nothing here writes to a real install location or to the captain's
checkout: every resolution runs against fixture directories under
`tmp_path`, and the one test that reads the real durable venv
(`test_the_plugin_resolves_the_real_durable_venv_where_built`) is
read-only and skips where no durable venv is built.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import shared_environment as se  # noqa: E402

PLUGIN = (REPO_ROOT / "resolve_workflow_integration"
          / "com.videoeditingpilot.vep")
INTERPRETER_JS = PLUGIN / "js" / "interpreter.js"
SHARED_ENVIRONMENT = (REPO_ROOT / "library" / "tools"
                      / "shared_environment.py")

NO_NODE = "node is not on PATH, so the plugin's JavaScript cannot be run here"
NO_PYTHON3 = "no python3 on PATH to serve as the query vehicle here"
STOCK_PYTHON3 = Path("/usr/bin/python3")
NO_STOCK = "no stock interpreter on this machine to prove the vehicle with"


def _make_exe(path: Path) -> Path:
    """Something the ladder's exists-and-executable predicate accepts."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _query_env(tmp_path: Path, path=None, extra=None) -> dict:
    """The environment a query runs under: fixture vep home, no override.

    `PYTHONPATH` points at this checkout so that ANY bootstrap - even a
    stock one that never saw this repository - finds `library/`; the
    plugin's own spawn relies on its `cwd` for the same, which the tests
    below cannot use because their repo roots are fixture lanes.
    """
    env = dict(os.environ)
    env.pop("PIPELINE_PYTHON", None)
    env["PIPELINE_VEP_HOME"] = str(tmp_path / "vep")
    env["PYTHONPATH"] = str(REPO_ROOT)
    if path is not None:
        env["PATH"] = path
    if extra:
        env.update(extra)
    return env


def _run_query(python: str, repo_root: Path, env: dict):
    """The plugin's exact query, answered as `(returncode, report dict)`."""
    proc = subprocess.run(
        [python, "-m", "library.tools.shared_environment",
         "--resolve-interpreter", "--repo-root", str(repo_root)],
        capture_output=True, encoding="utf-8", env=env, timeout=120,
        check=False)
    assert proc.returncode == 0, (
        "the query vehicle itself failed: %s%s" % (proc.stdout, proc.stderr))
    return proc.returncode, json.loads(proc.stdout)


def _resolve_via_node(repo_root: Path, env: dict):
    """`js/interpreter.js` answering, run under plain node like the
    plugin's other JavaScript tests (`tests/test_workflow_integration_plugin.py`).

    `node` is resolved to an absolute path first, so a test may scrub
    PATH entirely (the no-bootstrap case) without losing the runner
    itself."""
    node = shutil.which("node")
    assert node is not None, NO_NODE
    script = ("const it = require(%s);"
              "it.resolvePython(%s).then(r => console.log(JSON.stringify(r)));"
              % (json.dumps(str(INTERPRETER_JS)), json.dumps(str(repo_root))))
    proc = subprocess.run([node, "-e", script], capture_output=True,
                          encoding="utf-8", env=env, timeout=120, check=False)
    assert proc.returncode == 0, (
        "node itself failed: %s%s" % (proc.stdout, proc.stderr))
    return json.loads(proc.stdout)


# ── direction 1: the query finds the durable interpreter ─────────────

def test_the_query_names_the_durable_interpreter_when_present(tmp_path):
    """The rung that exists on this machine class, and the one that does not."""
    durable = _make_exe(tmp_path / "vep" / se.DURABLE_VENV_DIRNAME
                        / "bin" / "python3")
    lane = tmp_path / "lane"
    lane.mkdir()  # no .venv of its own

    _, report = _run_query(sys.executable, lane, _query_env(tmp_path))

    assert report["python"] == str(durable)
    assert report["error"] == ""
    assert report["tried"][0] == str(durable), (
        "the durable venv must outrank the checkout's own: %s" % report["tried"])


def test_a_stock_interpreter_can_still_ask_the_ladder(tmp_path):
    """The bootstrap property, exercised for real rather than argued.

    `/usr/bin/python3` is macOS's 3.9 - an interpreter that could never
    run the pipeline - and it is exactly what answers on machines where
    nothing newer is on PATH. It must still resolve the durable venv,
    because asking evaluates only path predicates.
    """
    if not STOCK_PYTHON3.is_file():
        pytest.skip(NO_STOCK)
    durable = _make_exe(tmp_path / "vep" / se.DURABLE_VENV_DIRNAME
                        / "bin" / "python3")
    lane = tmp_path / "lane"
    lane.mkdir()

    _, report = _run_query(str(STOCK_PYTHON3), lane, _query_env(tmp_path))

    assert report["python"] == str(durable), (
        "a stock vehicle answered wrongly: %s" % report)


def test_the_query_module_stays_parseable_by_a_stock_interpreter():
    """The grammar half of the bootstrap property, pinned so a later edit
    cannot silently require a newer vehicle than the machines carry.

    `ast.parse` with `feature_version=(3, 9)` refuses newer SYNTAX; the
    stdlib use stays auditable by inspection (long-stable `os`/`pathlib`
    calls only - the module docstring says so where the constraint lives).
    """
    source = SHARED_ENVIRONMENT.read_text(encoding="utf-8")
    try:
        ast.parse(source, feature_version=(3, 9))
    except SyntaxError as exc:
        pytest.fail("shared_environment.py uses syntax a stock vehicle "
                    "cannot parse (the plugin ASKS through any python3): %s"
                    % exc)


# ── direction 2: nothing supported exists, and the refusal is actionable ──

def test_the_query_refuses_naming_every_rung_when_nothing_exists(tmp_path):
    """Exit 0 with an empty answer: the QUERY succeeded, the LADDER found
    nothing. A nonzero exit always means the vehicle itself failed, never
    the ladder - the two failures need different remedies."""
    lane = tmp_path / "lane"
    lane.mkdir()

    returncode, report = _run_query(
        sys.executable, lane, _query_env(tmp_path))

    assert returncode == 0
    assert report["python"] == ""
    assert str(tmp_path / "vep" / se.DURABLE_VENV_DIRNAME) in report["error"]
    assert str(lane / ".venv") in report["error"]
    assert "docs/ML_ENVIRONMENT.md" in report["error"]


def test_the_explicit_override_wins_in_the_query(tmp_path):
    """Rung 1 is the explicit choice, including over a present durable venv."""
    durable = _make_exe(tmp_path / "vep" / se.DURABLE_VENV_DIRNAME
                        / "bin" / "python3")
    assert durable.is_file()
    chosen = _make_exe(tmp_path / "chosen")
    lane = tmp_path / "lane"
    lane.mkdir()

    _, report = _run_query(sys.executable, lane,
                           _query_env(tmp_path, extra={"PIPELINE_PYTHON": str(chosen)}))

    assert report["python"] == str(chosen)


# ── the plugin holds no ladder of its own ────────────────────────────

#: Ladder content that may live in exactly one place
#: (`shared_environment.py`). A rung literal, a rung-order literal, or a
#: derivation of `vep_home()` in JavaScript is a second ladder, and two
#: ladders that can disagree are a worse defect than the one this fixes.
#: `PIPELINE_PYTHON` is deliberately NOT on this list: reading the
#: override's VALUE for the fast path is plumbing, and the ladder's
#: answer still governs whenever it names nothing executable.
FORBIDDEN_IN_JS = (
    "INTERPRETER_CANDIDATES",
    "venv-py312/bin/python3",
    ".venv/bin/python3",
    "XDG_DATA_HOME",
    "PIPELINE_VEP_HOME",
    "vepHome",
    "interpreterCandidates",
    "pythonExecutable",
)


def _js_sources():
    yield PLUGIN / "main.js"
    for path in sorted((PLUGIN / "js").glob("*.js")):
        yield path


def test_the_plugin_carries_no_interpreter_ladder():
    """The single-source-of-truth half: the ladder is queried, not mirrored.

    PR 1141 carried the rungs as an array literal in `main.js` with a test
    diffing it against the Python one. A diffed duplicate is still a
    duplicate - and a stale one the moment only one side is reinstalled -
    so the literal is gone and this test refuses its return in ANY plugin
    script, not just the file it used to live in.
    """
    offenders = []
    for path in _js_sources():
        # Comments may recount the history and name the single source;
        # code may not carry a second copy (the same doctrine as the
        # no-fallback test below).
        code = [line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("//")]
        for literal in FORBIDDEN_IN_JS:
            if any(literal in line for line in code):
                offenders.append("%s in %s" % (literal, path.name))
    assert not offenders, (
        "the plugin re-implements the ladder instead of asking it: "
        + "; ".join(offenders))


def test_the_plugin_asks_the_ladder_through_one_module():
    """The query has exactly one client, and the bridge goes through it."""
    main = (PLUGIN / "main.js").read_text(encoding="utf-8")
    assert "require('./js/interpreter.js')" in main
    assert "interpreter.resolvePython(REPO_ROOT)" in main
    query = INTERPRETER_JS.read_text(encoding="utf-8")
    assert "library.tools.shared_environment" in query
    assert "--resolve-interpreter" in query


def test_the_plugin_has_no_stock_interpreter_fallback():
    """`/usr/bin/python3` is not a fallback; it is a slower failure.

    It carries none of whisperx, mlx_vlm or torch, so the bridge died
    inside a step's import reporting a package nobody mentioned, on the
    one surface the captain actually presses. Comments may recount that
    history; code may not name the path.
    """
    offenders = []
    for path in _js_sources():
        code = [line for line in path.read_text(encoding="utf-8").splitlines()
                if not line.lstrip().startswith("//")]
        offenders += ["%s:%s" % (path.name, line.strip())
                      for line in code if "/usr/bin/python3" in line]
    assert not offenders, (
        "the plugin still falls back to a stock interpreter: "
        + "; ".join(offenders))


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_the_plugin_scripts_parse():
    """`node --check` is parse-only, so it holds for `main.js` too even
    though running it needs Electron."""
    for path in _js_sources():
        proc = subprocess.run(["node", "--check", str(path)],
                              capture_output=True, encoding="utf-8",
                              check=False)
        assert proc.returncode == 0, "%s: %s" % (path.name, proc.stderr)


# ── the plugin's answer, both directions, through its own JavaScript ──

@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
@pytest.mark.skipif(shutil.which("python3") is None, reason=NO_PYTHON3)
def test_the_plugin_resolves_the_durable_interpreter(tmp_path):
    """Direction 1 at the plugin level: real `js/interpreter.js`, real
    ladder, fixture durable venv. The bootstrap is whatever `python3` is
    on PATH - its version must not matter, because it only asks."""
    durable = _make_exe(tmp_path / "vep" / se.DURABLE_VENV_DIRNAME
                        / "bin" / "python3")
    lane = tmp_path / "lane"
    lane.mkdir()

    answer = _resolve_via_node(lane, _query_env(tmp_path))

    assert answer["ok"] is True, answer
    assert answer["python"] == str(durable)


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
@pytest.mark.skipif(shutil.which("python3") is None, reason=NO_PYTHON3)
def test_the_plugin_refuses_actionably_when_nothing_exists(tmp_path):
    """Direction 2 at the plugin level: the ladder's own wording flows
    through the JavaScript VERBATIM - a second wording there would be a
    second ladder's worth of drift."""
    lane = tmp_path / "lane"
    lane.mkdir()

    answer = _resolve_via_node(lane, _query_env(tmp_path))

    assert answer["ok"] is False, answer
    assert str(tmp_path / "vep" / se.DURABLE_VENV_DIRNAME) in answer["error"]
    assert str(lane / ".venv") in answer["error"]
    assert "docs/ML_ENVIRONMENT.md" in answer["error"]


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_the_plugin_refuses_when_no_bootstrap_exists(tmp_path):
    """No `python3` on PATH is a refusal, never a reason to reach for a
    stock interpreter by absolute path instead."""
    lane = tmp_path / "lane"
    lane.mkdir()
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    env = _query_env(tmp_path, path=str(empty_bin))

    answer = _resolve_via_node(lane, env)

    assert answer["ok"] is False, answer
    assert "no `python3` on PATH" in answer["error"]
    assert "PIPELINE_PYTHON" in answer["error"]
    assert "docs/ML_ENVIRONMENT.md" in answer["error"]


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
def test_the_explicit_override_resolves_without_a_bootstrap(tmp_path):
    """The fast path survives a machine where nothing can even ask: rung 1
    IS the explicit choice, so an executable override answers directly."""
    chosen = _make_exe(tmp_path / "chosen")
    lane = tmp_path / "lane"
    lane.mkdir()
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    env = _query_env(tmp_path, path=str(empty_bin),
                     extra={"PIPELINE_PYTHON": str(chosen)})

    answer = _resolve_via_node(lane, env)

    assert answer == {"ok": True, "python": str(chosen)}, answer


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
@pytest.mark.skipif(shutil.which("python3") is None, reason=NO_PYTHON3)
def test_the_fast_path_agrees_with_the_ladder(tmp_path, monkeypatch):
    """The fast path applies the same predicate as rung 1, so it cannot
    disagree with the query - this runs both and compares."""
    _make_exe(tmp_path / "vep" / se.DURABLE_VENV_DIRNAME / "bin" / "python3")
    chosen = _make_exe(tmp_path / "chosen")
    lane = tmp_path / "lane"
    lane.mkdir()
    monkeypatch.setenv("PIPELINE_PYTHON", str(chosen))
    monkeypatch.setenv("PIPELINE_VEP_HOME", str(tmp_path / "vep"))

    ladder_answer = se.python_interpreter(str(lane))[0]
    js_answer = _resolve_via_node(lane, _query_env(
        tmp_path, extra={"PIPELINE_PYTHON": str(chosen)}))

    assert ladder_answer == str(chosen)
    assert js_answer == {"ok": True, "python": ladder_answer}


@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
@pytest.mark.skipif(shutil.which("python3") is None, reason=NO_PYTHON3)
def test_a_moved_checkout_refuses_by_name(tmp_path):
    """The stamped checkout moved after install: a missing command and a
    missing `cwd` fail the spawn identically, so the checkout's presence
    is established first and its absence refused under its own name."""
    gone = tmp_path / "lane-that-moved"

    answer = _resolve_via_node(gone, _query_env(tmp_path))

    assert answer["ok"] is False, answer
    assert str(gone) in answer["error"]
    assert "install_workflow_integration" in answer["error"]


# ── the machine this runs on, read-only ──────────────────────────────

@pytest.mark.skipif(shutil.which("node") is None, reason=NO_NODE)
@pytest.mark.skipif(shutil.which("python3") is None, reason=NO_PYTHON3)
def test_the_plugin_resolves_the_real_durable_venv_where_built():
    """Where the durable venv exists, the plugin's own JavaScript resolves
    it - the brief's first direction, against the real environment rather
    than a fixture. Read-only throughout: the checkout is only stated,
    the interpreter only asked its version. Skips where no durable venv
    is built, where the fixture test above is the whole proof."""
    env = dict(os.environ)
    env.pop("PIPELINE_PYTHON", None)
    env.pop("PYTHONPATH", None)
    expected, _ = se.python_interpreter(str(REPO_ROOT))
    if not expected:
        pytest.skip("no durable interpreter on this machine")
    answer = _resolve_via_node(REPO_ROOT, env)
    assert answer == {"ok": True, "python": expected}, answer
