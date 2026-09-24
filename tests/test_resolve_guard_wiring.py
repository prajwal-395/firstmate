"""The guard is WIRED, and this is what stops it drifting back to zero.

`resolve_placement_lock` was removed on 2026-09-12 with zero callers in
library, tests, docs or scripts, while the check it was meant to protect
had 22. A guard nobody enters reads as coverage (AGENTS.md 10.4), so the
count is not a detail - it IS the property.

Three claims are pinned here:

1. Every `RESOLVE_CURSOR` operation in `concurrency_routing.OPERATIONS`
   really takes the lease.
2. Every module that connects to Resolve is accounted for in that table,
   so a new Resolve caller cannot appear unrouted.
3. Nothing under `library/` turns the refusal off.
"""

import ast
import importlib
from pathlib import Path

import pytest

from library.tools import resolve_lock
from library.tools.concurrency_routing import (
    OPERATIONS, RESOLVE_CURSOR, RESOLVE_READ)

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY = REPO_ROOT / "library"

#: Entry points that are a MODULE rather than a function - a script the
#: pipeline launches in its own process. Named here so the row below
#: does not silently pass on a module it cannot import a function from.
MODULE_ENTRY_POINTS = {
    "library.steps.step_6_01_render.resolve_build_timeline":
        "build_timeline",
    "library.tools.execution.apply_fusion_comps": "apply_fusion_comps",
    "library.tools.segment_renderer": "render_segment",
    "library.tools.execution.resolve_render": "render_timeline",
    "library.tools.marker_capture": "capture",
    "library.steps.step_1_03_semantic_analysis": None,
    "library.steps.step_4_05_render_subtitles": None,
    "library.steps.step_7_02_verify_reels": None,
}


def _resolve_entry_point(entry_point):
    """The callable a row names, or None where the row names no function."""
    if entry_point in MODULE_ENTRY_POINTS:
        attribute = MODULE_ENTRY_POINTS[entry_point]
        if attribute is None:
            return None
        module = importlib.import_module(entry_point)
        return getattr(module, attribute)
    module_name, _, attribute = entry_point.rpartition(".")
    module = importlib.import_module(module_name)
    return getattr(module, attribute)


CURSOR_ROWS = [op for op in OPERATIONS if op.exclusion == RESOLVE_CURSOR]
READ_ROWS = [op for op in OPERATIONS if op.exclusion == RESOLVE_READ]


def test_there_are_cursor_operations_to_check():
    """A wiring gate over an empty list is a gate that cannot fail."""
    assert len(CURSOR_ROWS) >= 6


@pytest.mark.parametrize("op", CURSOR_ROWS, ids=lambda op: op.name)
def test_every_cursor_operation_takes_the_lease(op):
    function = _resolve_entry_point(op.entry_point)
    if function is None:
        pytest.skip(
            f"{op.entry_point} names no importable callable in this "
            f"environment - a row that must gain one, not a pass")
    leased = getattr(function, "__resolve_lease__", None)
    assert leased is not None, (
        f"{op.entry_point} is routed {op.exclusion} and does not take the "
        f"instance. Decorate it with `resolve_lock.under_lease(...)`, or "
        f"move the row to the class it really belongs in.")
    assert leased[1] is True, f"{op.entry_point} holds a SHARED lease"
    assert leased[2] is op.human_initiated, (
        f"{op.entry_point} takes the instance with prefer={leased[2]} while "
        f"the table says human_initiated={op.human_initiated}. Which one a "
        f"person presses is a property of the operation, and the two halves "
        f"may not disagree about it.")


@pytest.mark.parametrize("op", READ_ROWS, ids=lambda op: op.name)
def test_every_read_operation_takes_a_shared_lease(op):
    """A reader has to wait on the LOCK, not on the handshake.

    Measured 2026-09-12 (`docs/DUAL_WORKFLOW_SYNC_2026-09-12.md`):
    while a sibling lane placed clips, a read-only call blocked for
    over twelve minutes inside `scriptapp("Resolve")` itself - the
    Fusion connect handshake, before any timeline was reached. So a
    reader that does not take the lease does not avoid waiting; it
    waits somewhere with no bound, no diagnostic and no holder to name.
    The shared lease moves that wait to a file lock that has all three,
    and lets readers run together.
    """
    function = _resolve_entry_point(op.entry_point)
    if function is None:
        pytest.skip(
            f"{op.entry_point} names no importable callable in this "
            f"environment - a row that must gain one, not a pass")
    leased = getattr(function, "__resolve_lease__", None)
    assert leased is not None, (
        f"{op.entry_point} is routed {op.exclusion} and takes nothing. "
        f"It will block inside the scriptapp handshake instead, which is "
        f"the starvation the routing table exists to end.")
    assert leased[1] is False, (
        f"{op.entry_point} is a READ holding an EXCLUSIVE lease - two "
        f"readers would serialise for no reason.")


# ── No Resolve caller escapes the table ─────────────────────────────

def _library_files():
    return [p for p in LIBRARY.rglob("*.py") if "__pycache__" not in str(p)]


#: Modules that connect to Resolve and are NOT dispatchable work: a
#: connection helper, a health probe, a capability probe, a debugging
#: serialiser. Each is reached THROUGH one of the routed operations or
#: run by hand by an operator, so routing it would name a unit a
#: supervisor never dispatches. Listed rather than pattern-matched, so
#: adding one is a decision somebody made on purpose.
CONNECTS_BUT_IS_NOT_DISPATCHED = {
    "library/tools/resolve_locale.py",        # the connection itself
    "library/tools/resolve_health.py",        # is Resolve up?
    "library/tools/resolve_relinker.py",      # operator tool
    "library/tools/resolve_project_sync.py",  # operator tool
    "library/tools/timeline_serializer.py",   # debugging dump
    "library/tools/capture_timeline.py",      # operator tool
    "library/tools/capture_fusion_comps.py",  # operator tool
    "library/tools/qa/timeline_sync_qa.py",   # operator tool
    "library/tools/build_version_control.py",  # called under `promote`
    "library/steps/step_4_05_render_subtitles/step.py",  # reached
    # through the routed `render subtitles` row: a connect-and-check
    # read (refuses unless the open project is this project's own)
    # and swap writes that take their own leases in `caption_swap`
    "library/steps/step_7_02_verify_reels/step.py",  # reached
    # through the routed `grab gate stills` row: a connect-and-check
    # read (refuses unless the open project is this project's own)
    # and grab writes that take their own exclusive lease in
    # `gate_stills`
    "library/tools/drift_check.py",  # takes no lease: called under
    # `build reels` at both ends, and the `drift` CLI holds the
    # instance itself - the cursor move is the write either way
    "library/tools/captain_edits.py",         # CLI, records under the key
    "library/tools/timeline_conformance.py",  # routed as a READ
    "library/tools/marker_feedback.py",       # routed as a READ
    "library/tools/reel_build.py",            # routed, three rows
    "library/steps/step_6_01_render/probe_resolve_capabilities.py",
    "library/steps/step_6_01_render/resolve_build_timeline.py",  # routed
    "library/tools/execution/apply_fusion_comps.py",             # routed
    "library/tools/execution/resolve_render.py",                 # routed
}


def test_every_module_that_connects_to_resolve_is_accounted_for():
    """A new Resolve caller must be routed or declared undispatched.

    This is the half that keeps the table honest as the engine grows:
    `route()` calls an unlisted entry point FREE, so an unrouted Resolve
    caller would be dispatched in parallel with a build. The only way
    that cannot happen is if an unrouted Resolve caller cannot exist.
    """
    routed = {op.entry_point for op in OPERATIONS}
    unaccounted = []
    for path in _library_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        if "scriptapp" not in source:
            continue
        relative = str(path.relative_to(REPO_ROOT))
        if relative in CONNECTS_BUT_IS_NOT_DISPATCHED:
            continue
        dotted = relative[:-3].replace("/", ".")
        if any(entry == dotted or entry.startswith(dotted + ".")
               for entry in routed):
            continue
        unaccounted.append(relative)
    assert not unaccounted, (
        "these modules connect to Resolve and are neither in "
        "`concurrency_routing.OPERATIONS` nor declared undispatched: "
        + ", ".join(sorted(unaccounted)))


def test_the_undispatched_list_names_only_real_connectors():
    """A stale exemption is a lie about what is still owed."""
    stale = [name for name in CONNECTS_BUT_IS_NOT_DISPATCHED
             if "scriptapp" not in (REPO_ROOT / name).read_text(
                 encoding="utf-8", errors="replace")]
    assert not stale, f"no longer connect to Resolve: {sorted(stale)}"


# ── The refusal cannot be turned off from inside the engine ─────────

def test_no_library_module_declares_itself_the_sole_writer():
    """`assume_sole_writer` turns the guard off. Only a test may.

    A module that declared itself the sole writer would have answered
    the question the guard exists to ask, and every write under it would
    be unguarded while still reading as covered.
    """
    offenders = []
    for path in _library_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        if "assume_sole_writer" not in source:
            continue
        if path.name == "resolve_lock.py":
            continue  # where it is DEFINED
        offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, offenders


@pytest.mark.heavy
def test_assert_current_timeline_still_has_its_callers():
    """The 22 write paths the refusal now covers, counted.

    If this drops it is because a write path stopped checking, which is
    the defect the check exists for - not because the check got tidier.
    """
    calls = 0
    for path in _library_files():
        source = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - not a python file we own
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "assert_current_timeline"):
                calls += 1
    assert calls >= 19, (
        f"only {calls} write paths still check the cursor before writing")


def test_the_live_resolve_fixture_has_users():
    """A fixture nobody takes is the zero-caller defect, in miniature.

    `resolve_session` is what stops a test that drives a live Resolve
    from wedging behind the captain or another lane. It is only worth
    anything where the tests that drive a live Resolve take it.
    """
    takers = [p.name for p in (REPO_ROOT / "tests").glob("test_*.py")
              if "resolve_session" in p.read_text(encoding="utf-8")]
    assert len(takers) >= 2, takers
    connectors = [p.name for p in (REPO_ROOT / "tests").glob("test_*.py")
                  if "connect_resolve()" in p.read_text(encoding="utf-8")]
    missing = sorted(set(connectors) - set(takers))
    assert not missing, (
        f"these tests connect to a live Resolve and do not lease it: "
        f"{missing}. They are a writer nobody counted.")
