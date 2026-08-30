"""The panel's split is real, and the entry point imports the repository.

Two guarantees, both structural, both cheap to break by accident:

1. **`library/tools/panel/` holds no Qt and no Resolve**, so the half
   that decides things is ordinary Python that ordinary tests can drive.
   A view helper that reached for `DaVinciResolveScript` would silently
   move logic back across the line and out of reach of every test in
   this file's neighbours.
2. **The entry point imports the repository's modules rather than
   keeping a second copy of the rules.**  The scout's prototype
   deliberately imported nothing, to prove the no-dependency claim, and
   paid for it: it resolved a step directory to a node id by longest
   match, gave three steps the wrong status and hid one of two failures
   on 001.  `project_layout.node_id_for` is the one translator and the
   shipped panel calls it.

The widget layer itself is Qt inside Resolve's own `UIManager` and is
NOT covered here - it is covered by looking at it, and by the
responsiveness measurement in the PR. What IS covered is that it parses,
that it declares what the installer needs, and that everything it
decides lives on the other side of the line.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "library" / "tools" / "panel"
ENTRY_POINT = REPO_ROOT / "resolve_scripts" / "VEP Pipeline Panel.py"

# Modules that only exist inside Resolve's own script host, or that are
# the GUI toolkit. Either one in the package means logic has moved back
# across the line.
FORBIDDEN_IN_PACKAGE = ("DaVinciResolveScript", "BlackmagicFusion",
                        "fusionscript", "PySide2", "PySide6", "PyQt5")


def _modules():
    return sorted(p for p in PACKAGE.glob("*.py"))


def _imported_names(source: str):
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


# ── The package is Resolve-free ──────────────────────────────────────

def test_the_panel_package_imports_no_qt_and_no_resolve():
    for path in _modules():
        imported = _imported_names(path.read_text(encoding="utf-8"))
        for forbidden in FORBIDDEN_IN_PACKAGE:
            assert forbidden not in imported, (
                f"{path.name} imports {forbidden}. The panel package is the "
                f"half that tests can drive; anything that needs Resolve "
                f"belongs in the entry point.")


def test_every_panel_module_imports_without_resolve_running():
    """The proof, rather than the promise: they are imported right here,
    in a plain interpreter with no Resolve and no Fusion."""
    import importlib

    for path in _modules():
        if path.name == "__init__.py":
            continue
        module = importlib.import_module("library.tools.panel.%s" % path.stem)
        assert module is not None


def test_live_resolve_facts_arrive_as_a_plain_dataclass():
    """`ResolveContext` is what lets a test hand the join a playhead
    without an application running."""
    from library.tools.panel.clip_context import ResolveContext

    context = ResolveContext(page="edit", timecode="00:00:01:00")
    assert context.clip is None
    assert context.markers == []
    assert context.source_basename == ""


# ── The entry point ──────────────────────────────────────────────────

def test_the_entry_point_parses():
    ast.parse(ENTRY_POINT.read_text(encoding="utf-8"))


def test_the_entry_point_declares_the_line_the_installer_stamps():
    """`scripts/install_resolve_scripts.sh` replaces `REPO_ROOT = ""`
    with this checkout's path and RAISES if the line is absent, so a
    script without it silently never gets installed."""
    source = ENTRY_POINT.read_text(encoding="utf-8")
    assert 'REPO_ROOT = ""' in source


def test_the_installer_would_stamp_this_entry_point(tmp_path):
    """The installer's own stamping step, run here: the needle is found
    and the result is a valid module that names this checkout."""
    source = ENTRY_POINT.read_text(encoding="utf-8")
    stamped = source.replace('REPO_ROOT = ""',
                             "REPO_ROOT = " + repr(str(REPO_ROOT)), 1)
    assert str(REPO_ROOT) in stamped
    ast.parse(stamped)


def test_the_entry_point_imports_the_repository_rather_than_copying_it():
    """The scout's own structural recommendation, and the step-id bug in
    its section 4 is what the second copy already cost."""
    source = ENTRY_POINT.read_text(encoding="utf-8")
    assert "from library.tools.panel import" in source
    assert "from library.tools import review_gate" in source


def test_the_entry_point_re_implements_no_step_id_translation():
    """Any node-id guessing in the widget layer is the prototype's bug
    coming back. The translation happens in `project_layout.node_id_for`,
    which `panel/trace.py` calls."""
    source = ENTRY_POINT.read_text(encoding="utf-8")
    assert "node_id_of" not in source
    assert "longest match" not in source
    assert "split(\"_\", 2)" not in source


def test_the_entry_point_holds_no_credential():
    """It shells out to an already-authenticated CLI. A key written into
    Resolve's application-support folder would be one `cat` away in a
    folder that syncs and backs up."""
    source = ENTRY_POINT.read_text(encoding="utf-8")
    for smell in ("api_key", "API_KEY", "ANTHROPIC_API_KEY", "sk-ant",
                  "Bearer "):
        assert smell not in source, smell
    assert '"claude", "-p"' in source


def test_the_model_is_overridable_and_named():
    source = ENTRY_POINT.read_text(encoding="utf-8")
    assert "VEP_PANEL_MODEL" in source
    assert "claude-haiku-4-5-20251001" in source


def test_the_panel_starts_no_server_and_opens_no_socket():
    """No FastAPI, no localhost, no `pip install`. Resolve launches
    whatever interpreter it finds, so anything beyond the standard
    library is a thing that will one day not be installed."""
    source = ENTRY_POINT.read_text(encoding="utf-8")
    imported = _imported_names(source)
    for forbidden in ("fastapi", "uvicorn", "flask", "socket",
                      "http", "requests", "httpx", "webbrowser"):
        assert forbidden not in imported, forbidden
    for path in _modules():
        imported = _imported_names(path.read_text(encoding="utf-8"))
        for forbidden in ("fastapi", "uvicorn", "flask", "requests", "httpx"):
            assert forbidden not in imported, "%s: %s" % (path.name, forbidden)


def test_the_panel_needs_nothing_beyond_the_standard_library():
    """`panel/strip.py` encodes its PNG from `zlib` and `struct` for
    exactly this reason - no Pillow, no matplotlib."""
    from library.tools.panel import strip

    imported = _imported_names(
        (PACKAGE / "strip.py").read_text(encoding="utf-8"))
    assert imported <= {"__future__", "struct", "zlib", "dataclasses",
                        "typing", "os"}
    assert callable(strip.write_png)


# ── Every slow thing goes to a worker thread ─────────────────────────

# QUALIFIED, because two modules legitimately have a `preview`:
# `run_view.preview` resolves a whole run configuration and
# `trace.preview` renders one line of a level. Matching the bare name
# conflated them and flagged the cheap one.
SLOW_CALLS = {"trace.read_state", "trace.step_rows", "trace.trace_step",
              "clip_context.clip_facts", "run_view.preview",
              "run_view.start_run", "run_view.tail_log", "strip.draw",
              "ask_model"}

WORKER_FUNCTIONS = {
    # Functions that exist ONLY as `spawn` targets. Named rather than
    # inferred, because "is this ever called on the loop" is exactly the
    # question this test is asking and inferring it would beg it.
    "ask_model", "_read_project", "build_strip", "build_preview",
    "read_tail", "join_clip",
}


def test_every_slow_call_in_the_entry_point_goes_through_spawn():
    """The panel owns its event loop, so a slow call ON that loop is the
    one way it can freeze itself. Everything slow is `spawn`ed.

    Read off the source rather than measured, because the widget layer
    cannot be driven here - and a structural check that names the calls
    is what stops a seventh being added on the loop by accident.
    """
    tree = ast.parse(ENTRY_POINT.read_text(encoding="utf-8"))
    for function in [n for n in ast.walk(tree)
                     if isinstance(n, ast.FunctionDef)]:
        if function.name in WORKER_FUNCTIONS:
            continue
        for call in [n for n in ast.walk(function)
                     if isinstance(n, ast.Call)]:
            name = _called_name(call)
            if name.endswith("spawn"):
                continue
            if name in SLOW_CALLS and not _inside_a_spawn(function, call):
                pytest.fail(
                    "%s() calls %s() on the event loop. Wrap it in spawn(), "
                    "or add it to WORKER_FUNCTIONS with a reason."
                    % (function.name, name))


def _called_name(call: ast.Call) -> str:
    """`ask_model`, or `run_view.preview` for an attribute chain.

    The last TWO components, so a module qualifies the function and two
    modules may share a function name without the guard confusing them.
    """
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        parent = call.func.value
        if isinstance(parent, ast.Attribute):
            return "%s.%s" % (parent.attr, call.func.attr)
        if isinstance(parent, ast.Name):
            return "%s.%s" % (parent.id, call.func.attr)
        return call.func.attr
    return ""


def _inside_a_spawn(function: ast.FunctionDef, target: ast.Call) -> bool:
    """True when `target` is an ARGUMENT to a spawn, not a call on the
    loop - `self.spawn("state", self.m.trace.read_state, folder)` passes
    the function without calling it, so this only has to catch the
    `spawn(..., f(x))` shape."""
    for call in [n for n in ast.walk(function) if isinstance(n, ast.Call)]:
        if _called_name(call) != "spawn":
            continue
        for argument in call.args:
            if any(node is target for node in ast.walk(argument)):
                return True
    return False


def test_the_slow_calls_the_guard_watches_really_exist():
    """A guard naming functions nothing has is a guard that cannot fail,
    and a qualified name has to resolve on BOTH halves."""
    from library.tools.panel import clip_context, run_view, strip, trace

    modules = {"trace": trace, "run_view": run_view, "strip": strip,
               "clip_context": clip_context}
    for qualified in SLOW_CALLS:
        if "." not in qualified:
            continue
        module, function = qualified.split(".", 1)
        assert module in modules, qualified
        assert hasattr(modules[module], function), qualified


def test_the_heartbeat_is_written_where_an_observer_can_read_it():
    """The responsiveness proof is a panel heartbeat and an observer
    process timing Resolve on the same clock. The heartbeat has to reach
    disk for the observer to see it."""
    source = ENTRY_POINT.read_text(encoding="utf-8")
    assert "panel_beats.jsonl" in source
    assert '"beats"' in source


# ── Leaving without being in the room when fusionscript tears down ───

def test_the_panel_exits_without_running_static_destructors():
    """Blackmagic's `fusionscript.so` does not join its own `RemoteApp`
    thread before its static destructor frees the pool that thread is
    using, so a process that connected to Resolve can SEGFAULT on the way
    OUT - after its work is finished, with nothing of ours running. The
    captain sees "Python quit unexpectedly" and reads it, reasonably, as
    the panel dying.

    Measured from the crash report of 2026-08-30 14:05:04: main thread in
    `exit -> __cxa_finalize_ranges ->
    Fusion::ReusePoolManager::~ReusePoolManager`, thread `RemoteApp` in
    `Fusion::RemoteApp::DispatchPacket`, EXC_BAD_ACCESS.

    So the panel leaves through `os._exit`, which never reaches
    `__cxa_finalize_ranges`. `sys.exit` would.
    """
    source = ENTRY_POINT.read_text(encoding="utf-8")
    assert "os._exit(status)" in source
    assert "sys.exit(main())" not in source, (
        "sys.exit runs the static destructors this exists to skip")
    tree = ast.parse(source)
    leave = next(n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "_leave")
    flushes = [n for n in ast.walk(leave)
               if isinstance(n, ast.Call) and _called_name(n).endswith("flush")]
    assert len(flushes) >= 2, (
        "os._exit skips buffer flushing, so both streams are flushed first")


def test_the_step_list_holds_the_longest_step_name():
    """A step's name is the primary key of the trace view and must not
    need scrolling to read. `4_06_render_motion_graphics` is 27
    characters; the column was 190 px and rendered
    `4_06_render_motion_gr...`."""
    from library.tools import project_layout

    source = ENTRY_POINT.read_text(encoding="utf-8")
    longest = max(len(step.dirname) for step in project_layout.STEPS)
    # ~7.2 px per character in the 12 px monospace, plus the tree indent.
    needed = longest * 7.2 + 44
    tree = ast.parse(source)
    widths = None
    for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call)]:
        if _called_name(call).endswith("_columns") and call.args:
            if getattr(call.args[0], "value", None) == "steps":
                widths = [k.value for k in call.keywords
                          if k.arg == "widths"][0]
    assert widths is not None, "the steps tree declares no column widths"
    assert widths.elts[0].value >= needed, (
        "column 0 is %d px and the longest step name needs %d"
        % (widths.elts[0].value, needed))


def test_no_column_shows_a_truncation_that_reads_as_a_word():
    """`ledger` rendered as `ledge` and `preflight` as `pref` - both
    read as complete English, so a truncation was indistinguishable from
    a value. They come out of the list and go in the detail heading,
    whole."""
    source = ENTRY_POINT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for call in [n for n in ast.walk(tree) if isinstance(n, ast.Call)]:
        if (_called_name(call).endswith("_columns") and call.args
                and getattr(call.args[0], "value", None) == "steps"):
            headers = [k.value for k in call.keywords
                       if k.arg is None or k.arg == "headers"]
            names = [e.value for e in call.args[1].elts]
            assert "ledger" not in names, names
            del headers
