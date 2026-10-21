"""The canonical Resolve AXI operation map.

One record per registered operation, answering the questions a model must
NOT answer for itself: what the operation MEANS, what it DEPENDS on,
WHICH backend can execute it, what it PRESERVES, what evidence QUALIFIES
it, and which project STATES it is supported in.

Everything here is DERIVED.  The algebra owns the semantics
(`library/tools/edit_algebra.py`), the implementations own the executors
(`edit_patch`, `feedback_patch_apply`, `reel_touchup`, the registered AXI
adapters, the UI recipes), and the qualification records own the evidence
(`library/tools/axi_qualification_records.json`).  This module joins them
and audits the join.  It declares no operation, no backend and no
qualification of its own, so a second hand-maintained roster cannot grow
beside the algebra's - which is the defect the map exists to prevent.

Served by `bin/resolve-axi operation-map`, because a model that picks its
own execution path is how a semantically DIFFERENT edit reaches the
timeline: `clip.trim` through the touch-up and `clip.trim` through the
patch executor are not the same promise, and only one of them is
qualified.
"""

from __future__ import annotations

import ast
import inspect
import re
from collections.abc import Mapping, Sequence
from functools import cache
from pathlib import Path
from typing import Any

from library.tools import audio_operations, edit_algebra

#: The schema the map is published under.  A consumer that reads a
#: different schema is reading something else, and says so.
SCHEMA = "ren-axi-operation-map/v1"

#: The execution paths an operation can take.  These are the captain's
#: own words, and they are a CLOSED set: a backend that needs a path
#: outside it is a finding for the audit, not a new path invented here.
EXECUTION_PATHS = (
    "native_resolve_api",
    "official_mcp",
    "advanced_mcp",
    "ui_automation",
    "edit_patch",
    "staged_rebuild",
    "native_engine",
)

#: Which path each registered backend takes.  A backend absent from this
#: table has no declared path, and the audit reports it rather than
#: guessing one.
BACKEND_EXECUTION_PATHS: dict[str, str] = {
    "edit_patch": "edit_patch",
    "feedback_patch_apply": "edit_patch",
    "reel_touchup": "staged_rebuild",
    "composed_edit": "staged_rebuild",
    "resolve_axi": "native_resolve_api",
    "reel_append_placement": "staged_rebuild",
    "reel_otio_placement": "staged_rebuild",
    "resolve_native_invocation": "native_resolve_api",
    "resolve.ui": "ui_automation",
    "native_mcp": "official_mcp",
}

#: Backends that are MACHINES rather than dispatch tables: a staged
#: rebuild engine, a placement recorder.  Each names the code that
#: implements it, and the audit resolves that reference against the tree
#: - a machine whose entry point is gone is a finding, not a capability.
ENGINE_BACKENDS: dict[str, dict[str, str]] = {
    "composed_edit": {
        "clip.set_property": "library/tools/composed_edit.py:set_properties",
    },
    "reel_append_placement": {
        "clip.insert": (
            "library/tools/reel_append_placement.py:append_recorded"),
    },
    "reel_otio_placement": {
        "clip.insert": "library/tools/reel_otio_placement.py:import_recorded",
    },
}

#: Which module a backend's code lives in.  Used to decide whether an
#: AXI command can reach it: a command that never imports the backend's
#: module cannot dispatch to it.
BACKEND_MODULES: dict[str, str] = {
    "edit_patch": "edit_patch",
    "feedback_patch_apply": "feedback_patch_apply",
    "reel_touchup": "reel_touchup",
    "composed_edit": "composed_edit",
    "resolve_axi": "resolve_axi_core",
    "reel_append_placement": "reel_append_placement",
    "reel_otio_placement": "reel_otio_placement",
    "resolve.ui": "ui_transaction",
}

#: The four support levels an operation can hold.  They are ordered from
#: most to least capable, and an operation's level is the BEST level any
#: of its backends reaches.
SUPPORT_PRODUCTION = "production_enabled"
SUPPORT_CONTEXT = "context_qualified"
SUPPORT_IMPLEMENTED = "implemented"
SUPPORT_UNSUPPORTED = "unsupported"
SUPPORT_LEVELS = (
    SUPPORT_PRODUCTION,
    SUPPORT_CONTEXT,
    SUPPORT_IMPLEMENTED,
    SUPPORT_UNSUPPORTED,
)

#: The repository root, for resolving implementation evidence.
ROOT = Path(__file__).resolve().parents[2]

# Resolve commands that intentionally have no EditRequest algebra operation.
# Their identities, types and parser paths are still discoverable here;
# these entries must never be treated as timeline-edit semantics.
AXI_COMMAND_CONTROL_SPECS: dict[str, dict[str, str]] = {
    "clip.move_across_tracks": {
        "control_kind": "timeline_command", "handler": "cmd_edit_move"},
    "clip.title": {
        "control_kind": "timeline_command", "handler": "cmd_edit_title"},
    "clip.transition": {
        "control_kind": "timeline_command", "handler": "cmd_edit_transition"},
    "clip.speed": {
        "control_kind": "timeline_command", "handler": "cmd_edit_speed"},
    "color.lut": {
        "control_kind": "color_command", "handler": "cmd_color_lut"},
    "audio.isolate": {
        "control_kind": "audio_command", "handler": "cmd_audio_isolate"},
    "multicam.build": {
        "control_kind": "multicam_command", "handler": "cmd_multicam_build"},
    "multicam.sync": {
        "control_kind": "multicam_command", "handler": "cmd_multicam_sync"},
    "timeline.duplicate": {
        "control_kind": "timeline_lifecycle_command",
        "handler": "cmd_timeline_duplicate"},
    "timeline.checkpoint": {
        "control_kind": "timeline_lifecycle_command",
        "handler": "cmd_timeline_checkpoint"},
    "timeline.delete": {
        "control_kind": "timeline_lifecycle_command",
        "handler": "cmd_timeline_retire_owned"},
    "media.ingest": {
        "control_kind": "media_command", "handler": "cmd_ingest"},
    "media.relink": {
        "control_kind": "media_command", "handler": "cmd_relink"},
    "render.queue": {
        "control_kind": "render_command", "handler": "cmd_render_queue"},
    "render.start": {
        "control_kind": "render_command", "handler": "cmd_render_start"},
    "render.stop": {
        "control_kind": "render_command", "handler": "cmd_render_stop"},
}

#: The router's backend set, computed once on first use.
_ROUTER_BACKENDS: tuple[str, ...] | None = None

#: A definition of a module-level or nested name.  Used to answer "does
#: this file define this symbol" without parsing the file.
_DEFINITION_RE = re.compile(
    r"^[ \t]*(?:async[ \t]+def|def|class)[ \t]+([A-Za-z_]\w*)"
    r"|^[ \t]*([A-Za-z_]\w*)[ \t]*(?::[^=\n]+)?[ \t]*=(?!=)",
    re.MULTILINE)

#: The backend a routing decision names.  The router builds its decisions
#: with a literal backend every time, and one of them is another
#: decision's backend, which is not a literal and is not matched here.
_ROUTER_BACKEND_RE = re.compile(r"backend=\"([A-Za-z_][\w.]*)\"")


# ── What each backend can ACTUALLY do ─────────────────────────────────
#
# The algebra's `_EXECUTOR_COVERAGE` is a DECLARATION.  The audit exists
# because a declaration and an implementation are different things, so
# the dispatch tables below are read from the code itself.


@cache
def dispatch_inventory() -> dict[str, dict[str, str]]:
    """Every operation each backend can execute, with the code that does it.

    Read from the dispatch tables the executors own - the EditPatch
    operation table, the feedback patch's supported set, the touch-up's
    handler table, the registered AXI adapters, the UI recipes - and from
    the machine backends' own entry points.  A backend with neither
    contributes no rows, and the audit reports the difference against the
    registry's declaration.

    `mechanism` names WHERE the executor lives, so a reader can open the
    implementation instead of trusting a table.
    """
    inventory: dict[str, dict[str, str]] = {}

    def record(backend: str, op_id: str, mechanism: str) -> None:
        if op_id not in edit_algebra.ALGEBRA:
            return
        inventory.setdefault(backend, {}).setdefault(op_id, mechanism)

    from library.tools import edit_patch
    for op_id in sorted(edit_patch.OPERATIONS):
        record("edit_patch", op_id,
               "library/tools/edit_patch.py:OPERATIONS")

    from library.tools import feedback_patch_apply
    for op_id in sorted(feedback_patch_apply.SUPPORTED_OPS):
        record("feedback_patch_apply", op_id,
               "library/tools/feedback_patch_apply.py:SUPPORTED_OPS")

    from library.tools import reel_touchup
    for name in sorted(reel_touchup._OP_HANDLERS):
        op_id = edit_algebra.TOUCHUP_VIEW.get(name)
        if op_id:
            record("reel_touchup", op_id,
                   f"library/tools/reel_touchup.py:_OP_HANDLERS[{name!r}]")

    from library.tools import edit_execution_adapters, resolve_axi_core
    edit_execution_adapters.register_builtin_edit_executors()
    for op_id, backend in resolve_axi_core.registered_edit_executors():
        record(backend, op_id,
               "library/tools/edit_execution_adapters.py:"
               "register_builtin_edit_executors")

    from library.tools.resolved import ui_transaction
    for op_id in sorted(ui_transaction.UI_OPERATION_ADAPTERS):
        record("resolve.ui", op_id,
               "library/tools/resolved/ui_transaction.py:"
               "UI_OPERATION_ADAPTERS")

    for backend, operations in ENGINE_BACKENDS.items():
        for op_id, mechanism in sorted(operations.items()):
            record(backend, op_id, mechanism)

    return inventory


@cache
def declared_coverage() -> dict[str, set[str]]:
    """The registry's own declaration, read per operation.

    `edit_algebra._EXECUTOR_COVERAGE` is written backend-first; the map
    reads it operation-first, because the map's unit is the operation.
    This is the DECLARATION the audit measures the code against.
    """
    declared: dict[str, set[str]] = {}
    for backend, operations in edit_algebra._EXECUTOR_COVERAGE.items():
        for op_id in operations:
            declared.setdefault(op_id, set()).add(backend)
    return declared


@cache
def implemented_coverage() -> dict[str, dict[str, str]]:
    """The dispatch inventory, read per operation."""
    implemented: dict[str, dict[str, str]] = {}
    for backend, operations in dispatch_inventory().items():
        for op_id, mechanism in operations.items():
            implemented.setdefault(op_id, {})[backend] = mechanism
    return implemented


# ── The AXI surfaces ──────────────────────────────────────────────────
#
# An operation is exposed through AXI when an AXI-served surface can
# request it.  The surfaces are read from the code: the router's own
# backend decisions, and the commands the CLI actually offers.


@cache
def _command_paths() -> dict[str, str]:
    """Map each AXI command handler to the CLI path that reaches it.

    Read from the live parser, so the map names the command an agent can
    actually type rather than a function name it cannot.
    """
    import argparse

    from library.tools import resolve_axi_core_commands

    def subparsers_of(parser):
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                return action
        return None

    paths: dict[str, str] = {}

    def walk(action, prefix: list[str]) -> None:
        if action is None:
            return
        for name, sub in sorted(action.choices.items()):
            handler = sub._defaults.get("func")
            path = prefix + [name]
            if handler is not None:
                paths[handler.__name__] = " ".join(path)
            walk(subparsers_of(sub), path)

    root = subparsers_of(resolve_axi_core_commands.build_parser())
    if root is not None:
        walk(root, [])
    return paths


def _module_tree(module) -> ast.Module:
    """The parsed source of a module, or an empty tree when unavailable.

    Read from the file rather than through `inspect.getsource`, which
    tokenizes the whole module to find a block - the slowest way to ask
    a Python file what it contains.
    """
    path = getattr(module, "__file__", None)
    if path:
        try:
            source = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            source = ""
        if source:
            return ast.parse(source)
    try:
        source = inspect.getsource(module)
    except (OSError, TypeError):
        return ast.Module(body=[], type_ignores=[])
    return ast.parse(source)


@cache
def _command_module_tree() -> ast.Module:
    """The AXI command module's parsed source, parsed once."""
    from library.tools import resolve_axi_core_commands

    return _module_tree(resolve_axi_core_commands)


def _imported_leaves(node: ast.AST) -> set[str]:
    """The `library.tools` module leaves a function imports."""
    leaves: set[str] = set()
    for inner in ast.walk(node):
        if not isinstance(inner, ast.ImportFrom):
            continue
        module = inner.module or ""
        if not module.startswith("library.tools"):
            continue
        for alias in inner.names:
            leaves.add(alias.name)
    return leaves


@cache
def router_backends() -> tuple[str, ...]:
    """The backends the execution router can select, read from its code.

    A router that names a backend is a backend a request may reach, so
    the set of `backend=` values it constructs IS the reachable set - no
    second table needed.
    """
    global _ROUTER_BACKENDS
    if _ROUTER_BACKENDS is not None:
        return _ROUTER_BACKENDS
    from library.tools import execution_router

    path = getattr(execution_router, "__file__", None)
    backends: set[str] = set()
    if path:
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        for match in _ROUTER_BACKEND_RE.finditer(text):
            backends.add(match.group(1))
    _ROUTER_BACKENDS = tuple(sorted(backends))
    return _ROUTER_BACKENDS


@cache
def axi_verbs() -> dict[str, dict[str, str]]:
    """The AXI commands that perform an operation, per operation.

    Two sources, because AXI reaches an operation two ways: a command
    that builds the operation itself (`sense reframe`), and a registered
    semantic adapter the command dispatches to (`edit trim`).  The first
    is read from the command module's literals, the second from the
    adapter table plus the implementation evidence naming the handler.
    """
    paths = _command_paths()
    verbs: dict[str, dict[str, str]] = {}

    def record(op_id: str, mechanism: str, path: str) -> None:
        if op_id not in edit_algebra.ALGEBRA:
            return
        verbs.setdefault(op_id, {}).setdefault(path, mechanism)

    for handler, ops in _operations_named_by_handlers().items():
        path = paths.get(handler)
        if not path:
            continue
        for op_id in sorted(ops):
            record(op_id, f"resolve_axi_core_commands.py:{handler}", path)

    for (op_id, backend), evidence in (
            edit_algebra._IMPLEMENTATION_EVIDENCE.items()):
        if backend != "resolve_axi":
            continue
        for item in evidence:
            path, _, symbol = str(item).rpartition(":")
            if not path.endswith("resolve_axi_core_commands.py"):
                continue
            cli = paths.get(symbol)
            if cli:
                record(op_id, f"resolve_axi_core_commands.py:{symbol}", cli)

    # UI recipes are requested as a broker-owned resolve.ui_transaction,
    # rather than as native edit commands. Publish that AXI request surface
    # beside the recipe so discovery does not strand implemented UI adapters
    # with no way to request their qualified path.
    from library.tools.resolved.ui_transaction import UI_OPERATION_ADAPTERS
    for op_id in sorted(UI_OPERATION_ADAPTERS):
        record(op_id, "resolved/ui_transaction.py:UI_OPERATION_ADAPTERS",
               "ren resolved submit resolve.ui_transaction '<json params>'")
    return verbs


def _function_scopes(tree: ast.Module, prefix: str) -> dict[str, list[ast.AST]]:
    """The nodes inside each function whose name starts with ``prefix``.

    One pass over the tree, attributing each node to the function that
    encloses it.  Walking the tree once per function is how a map over a
    ten-thousand-line command module spends fifteen seconds reading it,
    and the command module holds one enormous parser builder - which is
    skipped here, because a function that is not a command cannot name
    the operation a command performs.
    """
    scopes: dict[str, list[ast.AST]] = {}

    def visit(node: ast.AST, current: ast.FunctionDef | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name.startswith(prefix):
                    scopes.setdefault(child.name, [])
                    visit(child, child)
                elif current is not None:
                    visit(child, current)
                continue
            if current is not None:
                scopes[current.name].append(child)
            visit(child, current)

    visit(tree, None)
    return scopes


@cache
def _command_scopes() -> dict[str, dict]:
    """What each AXI command names, in one pass over the command module.

    Two things are read together because they are read from the same
    source: the algebra operations a command builds, and the backends it
    imports.  A command that builds `{"op": "clip.delete"}` performs that
    operation; a command that imports `feedback_patch_apply` can
    dispatch to that backend.
    """
    scopes = _function_scopes(_command_module_tree(), "cmd_")
    named: dict[str, dict] = {}
    for handler, nodes in scopes.items():
        ops: set[str] = set()
        leaves: set[str] = set()
        for node in nodes:
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module.startswith("library.tools"):
                    for alias in node.names:
                        leaves.add(alias.name)
                continue
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "op"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)):
                    ops.add(value.value)
        if ops or leaves:
            named[handler] = {"operations": ops, "imports": leaves}
    return named


@cache
def _operations_named_by_handlers() -> dict[str, set[str]]:
    """Every algebra operation each AXI command handler names literally.

    A command that builds an operation dictionary says which operation it
    performs; reading those literals out of the command module is how the
    map knows a verb performs `clip.smart_reframe` rather than asserting
    it.
    """
    return {handler: set(entry["operations"])
            for handler, entry in _command_scopes().items()
            if entry["operations"]}


@cache
def command_backend_reach() -> dict[str, set[str]]:
    """Which backends each AXI command can dispatch to.

    Read from the imports inside each command's own body: a command that
    never imports a backend's module cannot dispatch to it, however much
    its help text promises.
    """
    reach: dict[str, set[str]] = {}
    for handler, entry in _command_scopes().items():
        backends = {backend for backend, leaf
                    in BACKEND_MODULES.items() if leaf in entry["imports"]}
        if backends:
            reach[handler] = backends
    return reach


@cache
def backend_exposure() -> dict[str, dict]:
    """How each backend is reached, and whether AXI reaches it at all.

    A backend the router can select, an AXI command that imports its
    module, or an AXI command that performs one of its operations is
    reachable through AXI.  One that is none of those is implemented
    somewhere AXI does not serve - which is a finding, because an agent
    that found it in the tree would use it and bypass the request,
    receipt and recovery contract.
    """
    router = set(router_backends())
    reach = command_backend_reach()
    verbs = axi_verbs()
    paths = _command_paths()
    verb_backends: dict[str, set[str]] = {}
    for handler, backends in reach.items():
        for backend in backends:
            verb_backends.setdefault(backend, set()).add(handler)
    implemented = implemented_coverage()
    operation_verbs: dict[str, set[str]] = {}
    for op_id, entries in verbs.items():
        for path in entries:
            operation_verbs.setdefault(op_id, set()).add(path)
    surfaces_by_operation: dict[str, set[str]] = {}
    for op_id, backends in implemented.items():
        for backend in backends:
            for path in operation_verbs.get(op_id, ()):
                surfaces_by_operation.setdefault(backend, set()).add(path)
    exposure: dict[str, dict] = {}
    for backend in sorted(set(BACKEND_EXECUTION_PATHS)
                          | set(edit_algebra._EXECUTOR_COVERAGE)):
        surfaces = []
        if backend in router:
            surfaces.append("edit-request")
        for handler in sorted(verb_backends.get(backend, ())):
            surfaces.append(f"command:{paths.get(handler, handler)}")
        for path in sorted(surfaces_by_operation.get(backend, ())):
            surfaces.append(f"command:{path}")
        exposure[backend] = {
            "backend": backend,
            "execution_path": BACKEND_EXECUTION_PATHS.get(backend),
            "axi_reachable": bool(surfaces),
            "surfaces": sorted(set(surfaces)),
        }
    return exposure


# ── Evidence resolution ───────────────────────────────────────────────


@cache
def _module_symbols(path: Path) -> frozenset[str]:
    """Every name a module defines.

    Read with a definition-pattern scan rather than a parse: the map
    resolves many evidence strings against the same module, and parsing
    a ten-thousand-line file to ask whether one function exists is the
    difference between a map that prints and a map that times out.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return frozenset()
    names: set[str] = set()
    for match in _DEFINITION_RE.finditer(text):
        names.add(match.group(1) or match.group(2))
    return frozenset(name for name in names if name)


def _symbol_in(path: Path, symbol: str) -> bool:
    """Whether the module at ``path`` defines ``symbol``."""
    return symbol in _module_symbols(path)


def resolve_evidence(evidence: str) -> dict:
    """Resolve one implementation-evidence string against the tree.

    A ``path:symbol`` reference is checked: the file must be in the tree
    and the symbol must be defined in it.  Anything else is prose - a PR
    note, a measured statement - and is reported as prose rather than
    failed, because prose is evidence and a missing symbol is not.
    """
    text = str(evidence)
    path, _, symbol = text.rpartition(":")
    if not path.endswith(".py") or not symbol:
        return {"evidence": text, "kind": "prose"}
    target = ROOT / path
    if not target.is_file():
        return {"evidence": text, "kind": "missing",
                "reason": f"{path} is not in the tree"}
    if not _symbol_in(target, symbol):
        return {"evidence": text, "kind": "missing",
                "reason": f"{path} defines no {symbol!r}"}
    return {"evidence": text, "kind": "resolved",
            "path": path, "symbol": symbol}


def _resolved_evidence(items: Sequence[str]) -> list[dict]:
    return [resolve_evidence(item) for item in items]


# ── Support levels ────────────────────────────────────────────────────


def support_level(status) -> str:
    """The best support level an operation's backends reach.

    `production_enabled` needs a completed live qualification record.
    `context_qualified` means live evidence exists but normal routing is
    still closed: the operation works only inside the context the
    evidence was earned in, and the map publishes that context so a
    reader can see the boundary rather than infer it.
    """
    if status.production_enabled:
        return SUPPORT_PRODUCTION
    if status.qualified:
        return SUPPORT_CONTEXT
    if status.implementation_status.startswith("implemented"):
        return SUPPORT_IMPLEMENTED
    return SUPPORT_UNSUPPORTED


# ── The map ───────────────────────────────────────────────────────────


def _backend_row(op_id: str, backend: str, declared: bool,
                 mechanism: str | None) -> dict:
    """One backend's capability for one operation."""
    status = edit_algebra.status(op_id)
    row = status.for_backend(backend) if status is not None else None
    evidence = list(row.implementation_evidence) if row else []
    return {
        "backend": backend,
        "execution_path": BACKEND_EXECUTION_PATHS.get(backend),
        "declared_by_registry": declared,
        "implemented": mechanism is not None,
        "mechanism": mechanism,
        "qualification_status": (row.qualification_status if row
                                  else "not_qualified"),
        "production_enabled": bool(row.production_enabled) if row else False,
        "qualified": bool(row.qualified) if row else False,
        "limitations": list(row.limitations) if row else [],
        "qualification_evidence": list(row.qualification_evidence) if row
        else [],
        "implementation_evidence": _resolved_evidence(evidence),
        "preservation_limits": list(row.preservation_limits) if row else [],
        "qualification_predicates": [dict(item) for item in
                                     (row.qualification_predicates if row
                                      else ())],
        "resolve_version": row.resolve_version if row else None,
        "qualification_media_types": list(row.qualification_media_types)
        if row else [],
        "qualification_timeline_conditions": list(
            row.qualification_timeline_conditions) if row else [],
        "visual_verification": list(row.visual_verification) if row else [],
        "audio_verification": list(row.audio_verification) if row else [],
        "recovery_qualification": (row.recovery_qualification if row
                                   else "not live-qualified"),
    }


def _audio_route(op_id: str) -> dict | None:
    """The audio route for one operation, or None when it is not audio."""
    row = audio_operations.BY_ID.get(op_id)
    if row is None:
        return None
    return {
        "route": row.route,
        "resolve_call": row.resolve_call,
        "read_back": row.read_back,
        "audible_verification": row.audible_verification,
        "preserves": list(row.preserves),
        "rebuilds": row.rebuilds,
        "refusal": audio_operations.refusal(op_id),
        "evidence": list(row.evidence),
    }


def _purpose(op) -> str:
    if op.note:
        return op.note
    if op.id.startswith("overlay."):
        action = op.id.removeprefix("overlay.").replace("_", " ")
        return f"{action.capitalize()} an overlay graphic."
    return f"{op.id.replace('.', ' ').replace('_', ' ')} operation."


def operation_entry(op_id: str, *, declared: Mapping[str, set[str]],
                    inventory: Mapping[str, Mapping[str, str]],
                    verbs: Mapping[str, Mapping[str, str]],
                    exposure: Mapping[str, Mapping]) -> dict:
    """The canonical record for one operation."""
    op = edit_algebra.ALGEBRA[op_id]
    status = edit_algebra.status(op_id)
    declared_backends = sorted(declared.get(op_id, ()))
    implemented_backends = sorted(inventory.get(op_id, ()))
    backends = []
    for backend in sorted(set(declared_backends) | set(implemented_backends)):
        backends.append(_backend_row(
            op_id, backend, backend in declared_backends,
            inventory.get(op_id, {}).get(backend)))
    paths = sorted({row["execution_path"] for row in backends
                    if row["execution_path"]})
    controls = sorted(name for name, ids in
                      edit_algebra.AXI_OPERATION_MAP.items()
                      if op_id in ids)
    routable = sorted(
        backend for backend in implemented_backends
        if backend in router_backends()
        and (edit_algebra.status(op_id).for_backend(backend) is not None))
    command_paths = sorted(verbs.get(op_id, {}))
    from library.tools.resolved.ui_transaction import ui_operation_adapter
    ui_adapter = ui_operation_adapter(op_id)
    return {
        "id": op_id,
        "semantics": {
            "purpose": _purpose(op),
            "domain": op.domain,
            "note": op.note,
            "merge": op.merge,
            "targets_an_existing_item": op.item,
            "temporal_effect": op.temporal,
        },
        "dependencies": {
            "required_parameters": list(op.required),
            "required_resolve_capabilities": list(
                status.required_resolve_capabilities),
            "inverse": status.inverse_recovery,
            "state_domains": list(status.state_domains),
        },
        "backend_capabilities": backends,
        "preservation_constraints": {
            "safety_class": status.safety_class,
            "minimal_safe_mechanism": status.minimal_safe_mechanism,
            "unsupported_native_state": list(status.unsupported_native_state),
            "preservation_limits": sorted({
                limit
                for row in backends
                for limit in row["preservation_limits"]}),
            "verification_contract": status.verification_contract,
        },
        "qualification_evidence": sorted({
            item
            for row in backends
            for item in row["qualification_evidence"]}),
        "supported_project_states": {
            "media_types": list(status.media_types),
            "timeline_conditions": list(status.timeline_conditions),
            "resolve_capabilities": list(status.required_resolve_capabilities),
            "qualification_predicates": [
                dict(predicate)
                for row in backends
                for predicate in row["qualification_predicates"]],
            "resolve_versions": sorted({
                row["resolve_version"] for row in backends
                if row["resolve_version"]}),
        },
        "support_level": support_level(status),
        "implementation_status": status.implementation_status,
        "execution_paths": paths,
        "axi_controls": controls,
        "axi_verbs": command_paths,
        "how_to_request": {
            "edit_request_routable": bool(routable),
            "routable_backends": routable,
            "commands": command_paths,
            "ui_transaction": ui_adapter,
            "note": ("an operation with no routable backend and no command "
                     "has no AXI surface at all; one with a command and no "
                     "routable backend is executed by that command, not by "
                     "a semantic request"),
        },
        "measured_cost": status.measured_cost,
        "target_strategy": status.target_strategy,
        "workflow_evidence": list(status.workflow_evidence),
        # The ROUTE, for the operations whose semantics live in their own
        # enumeration.  An audio operation that says nothing about which
        # route writes it is a declaration an agent cannot act on, and the
        # route is not derivable from the backend list: the OTIO route
        # REBUILDS the timeline and the native one does not.
        "audio_route": _audio_route(op_id),
        "audit": {
            "declared_backends": declared_backends,
            "implemented_backends": implemented_backends,
            "declared_without_adapter": sorted(
                set(declared_backends) - set(implemented_backends)),
            "adapter_without_declaration": sorted(
                set(implemented_backends) - set(declared_backends)),
            "axi_reachable_backends": sorted(
                backend for backend in implemented_backends
                if exposure.get(backend, {}).get("axi_reachable")),
        },
    }


def _control_rows() -> dict:
    """The AXI control names agents ask for, and what each resolves to."""
    rows = {}
    for name, ids in sorted(edit_algebra.AXI_OPERATION_MAP.items()):
        rows[name] = {
            "algebra_operations": list(ids),
            "available": bool(ids) and all(
                edit_algebra.status(op_id).compilable for op_id in ids),
            "production_enabled": bool(ids) and all(
                edit_algebra.status(op_id).production_enabled
                for op_id in ids),
            "limitation": list(
                edit_algebra.AXI_OPERATION_LIMITATIONS.get(name, ())),
        }
    return rows


def command_control_rows() -> dict[str, dict]:
    """Typed AXI command controls that do not name algebra operations.

    Command paths come from the parsed CLI, so an inventory row cannot
    claim a surface that the user cannot invoke. These controls execute
    through AXI's brokered command path and never route as EditRequests.
    """
    paths = _command_paths()
    rows = {}
    for name, spec in sorted(AXI_COMMAND_CONTROL_SPECS.items()):
        handler = spec["handler"]
        command_path = paths.get(handler, "")
        limitations = [
            "This is an AXI command control, not an edit-algebra operation.",
            "Use its listed command; it is not eligible for EditRequest routing.",
        ]
        if name == "timeline.delete":
            limitations.append(
                "Retires only a job-owned disposable timeline whose identity, "
                "ownership and content fingerprint still match.")
        rows[name] = {
            "id": name,
            "type": "command_only",
            "support_level": "command_only",
            "control_kind": spec["control_kind"],
            "command": f"resolve-axi {command_path}" if command_path else None,
            "command_handler": handler,
            "command_available": bool(command_path),
            "algebra_operations": [],
            "edit_request_routable": False,
            "request_surface": {
                "kind": "resolve_axi_argv",
                "broker_owned": True,
            },
            "limitations": limitations,
        }
    return rows


def operation_map() -> dict:
    """The whole canonical map, generated from the code as it stands.

    Returned as plain data: a consumer reads it, never calls into it.
    """
    declared = declared_coverage()
    inventory = implemented_coverage()
    verbs = axi_verbs()
    exposure = backend_exposure()
    entries = {
        op_id: operation_entry(op_id, declared=declared,
                               inventory=inventory, verbs=verbs,
                               exposure=exposure)
        for op_id in edit_algebra.ALGEBRA
    }
    levels: dict[str, list[str]] = {level: [] for level in SUPPORT_LEVELS}
    for op_id, entry in entries.items():
        levels[entry["support_level"]].append(op_id)
    paths: dict[str, list[str]] = {path: [] for path in EXECUTION_PATHS}
    for op_id, entry in entries.items():
        for path in entry["execution_paths"]:
            paths[path].append(op_id)
    return {
        "schema": SCHEMA,
        "generated_by": "library/tools/operation_map.py",
        "sources": [
            "library/tools/edit_algebra.py",
            "library/tools/edit_patch.py",
            "library/tools/feedback_patch_apply.py",
            "library/tools/reel_touchup.py",
            "library/tools/edit_execution_adapters.py",
            "library/tools/execution_router.py",
            "library/tools/resolved/ui_transaction.py",
            "library/tools/axi_qualification_records.json",
        ],
        "operation_count": len(entries),
        "support_levels": {level: sorted(levels[level])
                           for level in SUPPORT_LEVELS},
        "execution_paths": {path: sorted(paths[path])
                            for path in EXECUTION_PATHS},
        "backends": exposure,
        "axi_controls": _control_rows(),
        "command_controls": command_control_rows(),
        "operations": entries,
        "audit": audit(),
    }


# ── The audit ─────────────────────────────────────────────────────────


@cache
def audit() -> dict:
    """What the registry says against what the code does.

    Each finding names the operation and the mechanism, because a finding
    nobody can act on is a note, not a finding.
    """
    declared = declared_coverage()
    inventory = implemented_coverage()
    verbs = axi_verbs()
    exposure = backend_exposure()

    declared_without_adapter = []
    adapter_without_declaration = []
    duplicate_mechanisms = []
    implemented_outside_axi = []
    unresolved_evidence = []
    for op_id in sorted(edit_algebra.ALGEBRA):
        declared_backends = set(declared.get(op_id, ()))
        implemented_backends = set(inventory.get(op_id, ()))
        for backend in sorted(declared_backends - implemented_backends):
            declared_without_adapter.append({
                "operation": op_id,
                "backend": backend,
                "axi_verbs": sorted(verbs.get(op_id, {})),
                "reason": ("the registry declares this backend can execute "
                           "the operation, and no dispatch table provides it"),
            })
        for backend in sorted(implemented_backends - declared_backends):
            adapter_without_declaration.append({
                "operation": op_id,
                "backend": backend,
                "reason": ("a dispatch table provides this operation on this "
                           "backend, and the registry does not declare it"),
            })
        mechanisms = []
        for backend in sorted(implemented_backends):
            mechanisms.append({
                "backend": backend,
                "execution_path": BACKEND_EXECUTION_PATHS.get(backend),
                "mechanism": inventory[op_id][backend],
            })
        for path, mechanism in sorted(verbs.get(op_id, {}).items()):
            mechanisms.append({"backend": "resolve_axi",
                               "execution_path": "native_resolve_api",
                               "mechanism": mechanism, "command": path})
        if len(mechanisms) > 1:
            duplicate_mechanisms.append({
                "operation": op_id,
                "mechanisms": mechanisms,
                "reason": ("more than one mechanism writes this operation; "
                           "they are not interchangeable, so a reader must "
                           "not pick one by name"),
            })
        reachable = sorted(
            backend for backend in implemented_backends
            if exposure.get(backend, {}).get("axi_reachable"))
        command_paths = sorted(verbs.get(op_id, {}))
        routable = sorted(
            backend for backend in implemented_backends
            if backend in router_backends()
            and (edit_algebra.status(op_id).for_backend(backend) is not None))
        # An AXI surface for ONE operation is a backend the router can
        # select for it, or a command that performs it.  A backend that
        # is reachable for SOME other operation is not a surface for
        # this one: `caption.style` lives on the `resolve_axi` backend,
        # and no AXI command performs it.
        if implemented_backends and not routable and not command_paths:
            implemented_outside_axi.append({
                "operation": op_id,
                "backends": sorted(implemented_backends),
                "axi_reachable_backends": reachable,
                "reason": ("implemented, and no AXI surface reaches it: "
                           "no backend the router can select lists it, and "
                           "no AXI command performs it"),
            })
        for backend in sorted(implemented_backends):
            row = edit_algebra.status(op_id)
            evidence = row.for_backend(backend) if row else None
            for item in (evidence.implementation_evidence if evidence
                         else ()):
                resolved = resolve_evidence(item)
                if resolved["kind"] == "missing":
                    unresolved_evidence.append({
                        "operation": op_id,
                        "backend": backend,
                        "evidence": resolved["evidence"],
                        "reason": resolved["reason"],
                    })

    controls_without_adapter = []
    for name, ids in sorted(edit_algebra.AXI_OPERATION_MAP.items()):
        if not ids:
            controls_without_adapter.append({
                "control": name,
                "algebra_operations": [],
                "reason": ("the control name is offered but no algebra "
                           "operation backs it, so it cannot be executed"),
                "limitation": list(
                    edit_algebra.AXI_OPERATION_LIMITATIONS.get(name, ())),
            })
            continue
        missing = [op_id for op_id in ids if not inventory.get(op_id)]
        if missing:
            controls_without_adapter.append({
                "control": name,
                "algebra_operations": list(ids),
                "reason": ("the control resolves to operations with no "
                           "dispatch table behind them"),
                "limitation": list(
                    edit_algebra.AXI_OPERATION_LIMITATIONS.get(name, ())),
            })

    outside_the_registry = _implemented_outside_the_registry()
    planning_only = _planning_only_rows()
    adapters_without_a_route = _adapters_without_a_route()
    command_control_surface_gaps = [
        {"control": name, "handler": row["command_handler"]}
        for name, row in command_control_rows().items()
        if not row["command_available"]
    ]

    return {
        "declared_without_adapter": declared_without_adapter,
        "adapter_without_declaration": adapter_without_declaration,
        "adapters_without_a_route": adapters_without_a_route,
        "duplicate_mechanisms": duplicate_mechanisms,
        "implemented_outside_axi": implemented_outside_axi,
        "implemented_outside_the_registry": outside_the_registry,
        "axi_controls_without_adapter": controls_without_adapter,
        "command_control_surface_gaps": command_control_surface_gaps,
        "unresolved_implementation_evidence": unresolved_evidence,
        "planning_only": planning_only,
        "counts": {
            "declared_without_adapter": len(declared_without_adapter),
            "adapter_without_declaration": len(adapter_without_declaration),
            "adapters_without_a_route": len(adapters_without_a_route),
            "duplicate_mechanisms": len(duplicate_mechanisms),
            "implemented_outside_axi": len(implemented_outside_axi),
            "implemented_outside_the_registry": len(outside_the_registry),
            "axi_controls_without_adapter": len(controls_without_adapter),
            "command_control_surface_gaps": len(
                command_control_surface_gaps),
            "unresolved_implementation_evidence": len(unresolved_evidence),
            "planning_only": len(planning_only),
        },
    }


def _adapters_without_a_route() -> list[dict]:
    """Registered adapters the router cannot select.

    An adapter registered against a backend the router never names is a
    real implementation that no semantic request can reach: the
    operation is executed by its AXI command, or by nothing.  The map
    reports it rather than leaving the reader to assume the two routes
    are the same.
    """
    router = set(router_backends())
    exposure = backend_exposure()
    implemented = implemented_coverage()
    rows = []
    for backend in sorted(set(BACKEND_EXECUTION_PATHS)
                          | set(edit_algebra._EXECUTOR_COVERAGE)):
        if backend in router:
            continue
        operations = sorted(
            op_id for op_id, backends in implemented.items()
            if backend in backends)
        if not operations:
            continue
        rows.append({
            "backend": backend,
            "operations": operations,
            "router_backends": sorted(router),
            "reachable_by_command": exposure.get(
                backend, {}).get("surfaces", []),
            "reason": ("the router names no such backend, so a registered "
                       "adapter on it is unreachable from a semantic "
                       "request"),
        })
    return rows


def _implemented_outside_the_registry() -> list[dict]:
    """Implementations that are not operations in the registry at all.

    A UI recipe that names no algebra operation is a writer the map
    cannot describe, and a reader who found it in the tree would use it
    without a receipt.  It is reported by name.
    """
    from library.tools.resolved import ui_transaction

    rows = []
    for op_id in sorted(ui_transaction.UI_OPERATION_ADAPTERS):
        if op_id in edit_algebra.ALGEBRA:
            continue
        descriptor = ui_transaction.UI_OPERATION_ADAPTERS[op_id]
        rows.append({
            "implementation": op_id,
            "provider": descriptor.get("provider"),
            "implementation_module": descriptor.get("implementation"),
            "reason": ("implemented and declared as a UI operation, and "
                       "absent from the operation registry, so no AXI "
                       "request can express it"),
        })
    return rows


def _planning_only_rows() -> list[dict]:
    """Planning operations, and the primitives they compile into.

    A planning operation is a STATEMENT about the cut, not a timeline
    write.  It has no executor and must not be given one: it reaches the
    timeline by compiling into primitives that do have writers, which is
    why the compilation is declared rather than left to a reader to
    guess.
    """
    from library.tools import edit_operations

    rows = []
    for op_id, status in sorted(edit_algebra.OP_STATUS.items()):
        if status.implementation_status != "planning_only":
            continue
        primitives = list(
            edit_operations.PLAN_OPERATION_PRIMITIVES.get(op_id, ()))
        rows.append({
            "operation": op_id,
            "owner": edit_operations.OP_OWNERS.get(op_id),
            "compiles_into": primitives,
            "reason": ("a planning operation states an editorial intent; it "
                       "reaches the timeline through the primitives below, "
                       "not through an executor of its own"),
        })
    return rows


def planning_operations_without_primitives() -> list[str]:
    """Planning operations with no declared compilation.

    A planning operation whose primitives are undeclared cannot be
    compiled by anyone, so the map reports it rather than leaving the
    reader to infer one.
    """
    return [row["operation"] for row in audit()["planning_only"]
            if not row["compiles_into"]]


def stale_planning_declarations() -> list[str]:
    """Declared compilations for operations that are no longer planning-only.

    A table entry for an operation that gained an executor is a claim
    that stopped being true, and the audit says so.
    """
    from library.tools import edit_operations

    stale = []
    for op_id in sorted(edit_operations.PLAN_OPERATION_PRIMITIVES):
        status = edit_algebra.status(op_id)
        if status is None or status.implementation_status != "planning_only":
            stale.append(op_id)
    return stale


def to_json(payload: Mapping[str, Any] | None = None) -> str:
    """The map as one JSON document, sorted and stable."""
    import json

    data = dict(payload if payload is not None else operation_map())
    return json.dumps(data, sort_keys=True, indent=2)
