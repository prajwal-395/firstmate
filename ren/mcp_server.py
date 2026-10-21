"""Ren as typed MCP tools, over stdio.

An MCP-capable host (Claude Code, Codex, OpenCode) can call the Ren
catalog without building shell strings or parsing prose: tool names,
descriptions, `inputSchema`s and effect annotations are DERIVED from
the one registry (`ren.commands.VERBS`) - there is no second
hand-maintained catalog, and a verb missing here fails
`tests/contracts/test_mcp_server.py` rather than shipping silently.

Execution goes through the existing CLI dispatch: each call runs
`python -m ren.cli <verb> ...` in a subprocess, so exit codes,
refusals and the final `REN_RESULT` line are exactly what the shell
gets (`REN_OUTPUT=json` is set for the child). This start exposes what
is safe to run unattended:

- read-only verbs run directly,
- `dry_run` verbs run with `--dry-run` forced (an explicit
  `dry_run: false` is refused - the write path is closed),
- every other verb is listed (so discovery is complete) but its call
  is refused WITHOUT executing, carrying exit 4 and a `REN_RESULT`
  shaped payload naming the shell equivalent.

A later write-verb expansion builds on this same server and keeps the
CLI effect/refusal behavior.

The server starts only when run (`python -m ren.mcp_server`); nothing
enables it automatically. Host setup lives in `docs/customer/mcp.md`.

Standard library only: the server must start before the ML
environment exists, so nothing here imports the engine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
"""MCP protocol versions this server speaks, newest first. `initialize`
echoes the client's version when it is one of these, else the newest."""

TOOL_TIMEOUT_SECONDS = 100.0
"""One tool call never runs past this: it sits under the 120 s harness
default the way `ren wait`'s own default does."""

WRITE_REFUSED_CODE = "REN_MCP_WRITE_REFUSED"
"""The `code` a refused write-verb call carries in its `ren_result`."""

REN_RESULT_PREFIX = "REN_RESULT"
"""The final stderr line's prefix on the CLI path (`LE-03`); the
server parses it back out of the child's stderr."""


def engine_root() -> Path:
    """The engine tree this file serves: `$REN_ENGINE_ROOT` when set,
    else the checkout holding this file. Resolved fresh on every call -
    never at import - so tests can point it at a fixture."""
    override = os.environ.get("REN_ENGINE_ROOT")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1]


def server_version() -> str:
    """`ren.version.__version__` without importing the engine; the
    checkout fallback reads `unknown` rather than shelling out."""
    try:
        from ren.version import __version__
        return str(__version__)
    except Exception:  # noqa: BLE001 - the version is informational
        return "unknown"


def tool_name(verb_name: str) -> str:
    """`status` -> `ren_status`, `still-overlays` -> `ren_still_overlays`."""
    return "ren_" + verb_name.replace("-", "_")


def execution_mode(verb) -> str:
    """How this server runs a verb: `direct`, `dry_run` (with
    `--dry-run` forced), or `refused` (listed but never executed)."""
    if verb.read_only:
        return "direct"
    if getattr(verb, "typed_lifecycle", False):
        return "typed_lifecycle"
    if verb.dry_run:
        return "dry_run"
    return "refused"


def tool_for_verb(verb) -> dict:
    """One MCP tool record, derived from the catalog row - the single
    source `tools/list` serves and the contract test pins."""
    effects = list(verb.effects)
    description = f"{verb.summary} Effects: {', '.join(effects)}."
    mode = execution_mode(verb)
    if mode == "refused":
        description += (" This server refuses the call without running it "
                        f"(a write verb); run `ren {verb.name}` in a shell.")
    elif mode == "dry_run":
        description += (" Runs with --dry-run forced, changing nothing; "
                        f"run `ren {verb.name}` in a shell to apply.")
    elif mode == "typed_lifecycle":
        description += " Runs through the guarded typed storage lifecycle operations."
    return {
        "name": tool_name(verb.name),
        "description": description,
        "inputSchema": verb.input_schema,
        "annotations": {
            "readOnlyHint": verb.read_only,
            "destructiveHint": any(
                effect in ("project_write", "resolve_write", "render")
                for effect in effects),
            "idempotentHint": mode in ("direct", "dry_run"),
            "openWorldHint": "network" in effects,
        },
        "_meta": {"verb": verb.name, "effects": effects,
                  "read_only": verb.read_only,
                  "execution_mode": mode},
    }


def axi_tool() -> dict:
    """The AXI control boundary, with its own CLI-equivalent guards."""
    return {
        "name": "resolve_axi",
        "description": ("Execute one typed Resolve AXI command in-process. "
                        "Writes retain AXI's explicit --apply/--unsafe "
                        "flags, cursor assertions, broker routing and leases."),
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "command": {"type": "string", "minLength": 1},
                "arguments": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["command", "arguments"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True,
                         "idempotentHint": False, "openWorldHint": False},
        "_meta": {"control_boundary": "resolve-axi",
                  "execution_mode": "shared_core"},
    }


def axi_transaction_tool() -> dict:
    return {
        "name": "resolve_axi_transaction",
        "description": ("Submit one generation-pinned typed EditPatch with "
                        "multiple native operations; apply must be explicitly "
                        "true and the broker verifies the single receipt."),
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "transaction": {"type": "object", "minProperties": 1},
                "apply": {"type": "boolean"},
            },
            "required": ["transaction", "apply"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True,
                         "idempotentHint": False, "openWorldHint": False},
        "_meta": {"control_boundary": "resolve-axi",
                  "execution_mode": "guarded_transaction"},
    }


def axi_transaction_submit_tool() -> dict:
    return {
        "name": "resolve_axi_transaction_submit",
        "description": "Queue a typed EditPatch transaction and return its broker job id.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "transaction": {"type": "object", "minProperties": 1},
                "apply": {"type": "boolean"},
            },
            "required": ["transaction", "apply"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True,
                         "idempotentHint": False, "openWorldHint": False},
        "_meta": {"control_boundary": "resolve-axi",
                  "execution_mode": "guarded_transaction_submit"},
    }


def axi_transaction_retrieve_tool() -> dict:
    return {
        "name": "resolve_axi_transaction_retrieve",
        "description": "Retrieve the current or completed receipt for a broker job.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "job_id": {"type": "string", "minLength": 1},
                "wait": {"type": "number", "minimum": 0},
            },
            "required": ["job_id"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                         "idempotentHint": True, "openWorldHint": False},
        "_meta": {"control_boundary": "resolve-axi",
                  "execution_mode": "transaction_retrieve"},
    }


def axi_transaction_cancel_tool() -> dict:
    return {
        "name": "resolve_axi_transaction_cancel",
        "description": "Request broker cancellation when the job declares a safe stop path.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {"job_id": {"type": "string", "minLength": 1}},
            "required": ["job_id"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True,
                         "idempotentHint": True, "openWorldHint": False},
        "_meta": {"control_boundary": "resolve-axi",
                  "execution_mode": "safe_job_cancel"},
    }


def axi_transaction_plan_tool() -> dict:
    return {
        "name": "resolve_axi_transaction_plan",
        "description": "Validate a typed EditPatch against its recorded base and return a no-write backend plan.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {"transaction": {"type": "object", "minProperties": 1}},
            "required": ["transaction"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                         "idempotentHint": True, "openWorldHint": False},
        "_meta": {"control_boundary": "resolve-axi",
                  "execution_mode": "transaction_plan"},
    }


def axi_edit_request_tool() -> dict:
    return {
        "name": "resolve_axi_edit_request",
        "description": ("Submit one semantic EditRequest through the AXI "
                        "router. apply must be explicitly true; the SDK "
                        "prepares and authorizes the selected backend."),
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "request": {"type": "object", "minProperties": 1},
                "apply": {"type": "boolean"},
            },
            "required": ["request", "apply"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True,
                         "idempotentHint": False, "openWorldHint": False},
        "_meta": {"control_boundary": "resolve-axi",
                  "execution_mode": "semantic_request_submit"},
    }


def axi_edit_request_plan_tool() -> dict:
    return {
        "name": "resolve_axi_edit_request_plan",
        "description": "Prepare and route an EditRequest without contacting Resolve.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {"request": {"type": "object", "minProperties": 1}},
            "required": ["request"],
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False,
                         "idempotentHint": True, "openWorldHint": False},
        "_meta": {"control_boundary": "resolve-axi",
                  "execution_mode": "semantic_request_plan"},
    }


def call_axi_edit_request_plan(arguments: dict) -> dict:
    request = arguments.get("request")
    if not isinstance(request, dict):
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2, "error": "request must be an EditRequest object"})}],
            "isError": True}
    from library.tools.resolve_axi_sdk import prepare_edit
    try:
        result = prepare_edit(request)
        return {"content": [{"type": "text", "text": json.dumps(
            result, ensure_ascii=False, default=str)}]}
    except (KeyError, TypeError, ValueError, RuntimeError, PermissionError) as exc:
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2, "error": str(exc)})}], "isError": True}


def call_axi_edit_request(arguments: dict) -> dict:
    if arguments.get("apply") is not True:
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 4,
            "error": "semantic edits require apply: true"})}], "isError": True}
    request = arguments.get("request")
    if not isinstance(request, dict):
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2, "error": "request must be an EditRequest object"})}],
            "isError": True}
    from library.tools.resolve_axi_sdk import submit_edit
    try:
        result = submit_edit(request, authorized=True)
        return {"content": [{"type": "text", "text": json.dumps(
            result, ensure_ascii=False, default=str)}]}
    except (KeyError, TypeError, ValueError, RuntimeError, PermissionError) as exc:
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 4, "error": str(exc)})}], "isError": True}


def call_axi_transaction_plan(arguments: dict) -> dict:
    patch = arguments.get("transaction")
    if not isinstance(patch, dict):
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2,
            "error": "transaction must be an EditPatch object"})}],
            "isError": True}
    from library.tools.resolve_axi_sdk import AxiTransaction, plan
    try:
        result = plan(_typed_axi_transaction(AxiTransaction, patch))
        return {"content": [{"type": "text", "text": json.dumps(
            result, ensure_ascii=False, default=str)}]}
    except (KeyError, TypeError, ValueError) as exc:
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2, "error": str(exc)})}], "isError": True}


def call_axi_transaction_submit(arguments: dict) -> dict:
    if arguments.get("apply") is not True:
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 4,
            "error": "AXI transactions require apply: true"})}],
            "isError": True}
    patch = arguments.get("transaction")
    if not isinstance(patch, dict):
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2,
            "error": "transaction must be an EditPatch object"})}],
            "isError": True}
    from library.tools.resolve_axi_sdk import AxiTransaction, submit_transaction_job
    try:
        typed = _typed_axi_transaction(AxiTransaction, patch)
        job = submit_transaction_job(typed)
        return {"content": [{"type": "text", "text": json.dumps(
            job, ensure_ascii=False, default=str)}]}
    except (KeyError, TypeError, ValueError) as exc:
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2, "error": str(exc)})}], "isError": True}


def call_axi_transaction_retrieve(arguments: dict) -> dict:
    job_id, wait = arguments.get("job_id"), arguments.get("wait", 0.0)
    if not isinstance(job_id, str) or not job_id.strip():
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2, "error": "job_id must be non-empty"})}],
            "isError": True}
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait < 0:
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2, "error": "wait must be non-negative"})}],
            "isError": True}
    from library.tools.resolve_axi_sdk import get_edit_job
    job = get_edit_job(job_id, wait=float(wait))
    return {"content": [{"type": "text", "text": json.dumps(
        job, ensure_ascii=False, default=str)}]}


def call_axi_transaction_cancel(arguments: dict) -> dict:
    job_id = arguments.get("job_id")
    if not isinstance(job_id, str) or not job_id.strip():
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2, "error": "job_id must be non-empty"})}],
            "isError": True}
    from library.tools.resolve_axi_sdk import cancel_edit_job
    try:
        result = cancel_edit_job(job_id)
        return {"content": [{"type": "text", "text": json.dumps(
            result, ensure_ascii=False, default=str)}]}
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 4, "error": str(exc)})}], "isError": True}


def _typed_axi_transaction(transaction_type, patch: dict):
    return transaction_type(
        id=patch["id"], project=patch["project"],
        timeline=patch["timeline"],
        base_generation=int(patch["base_generation"]),
        capability=patch["capability"],
        affected_spans=tuple(tuple(span) for span in patch["affected_spans"]),
        conflict_domains=tuple(patch["conflict_domains"]),
        operations=tuple(patch["operations"]),
        preconditions=tuple(patch.get("preconditions", ())),
        postconditions=tuple(patch.get("postconditions", ())),
    )


def call_axi(arguments: dict) -> dict:
    """Run a typed AXI call without a subprocess or a second dispatcher."""
    command, extra = arguments.get("command"), arguments.get("arguments")
    if (not isinstance(command, str) or not command.strip()
            or not isinstance(extra, list)
            or any(not isinstance(value, str) for value in extra)):
        return {"content": [{"type": "text", "text": json.dumps({
            "ok": False, "exit": 2,
            "error": "command must be non-empty and arguments must be strings"})}],
            "isError": True}
    from library.tools.resolve_axi_sdk import call
    result = call(command, *extra)
    envelope = result.as_dict()
    return {"content": [{"type": "text",
                          "text": json.dumps(envelope, ensure_ascii=False)}],
            **({"isError": True} if not result.ok else {})}


def call_axi_transaction(arguments: dict) -> dict:
    if arguments.get("apply") is not True:
        payload = {"ok": False, "exit": 4,
                   "error": "AXI transactions require apply: true"}
        return {"content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": True}
    patch = arguments.get("transaction")
    if not isinstance(patch, dict):
        payload = {"ok": False, "exit": 2,
                   "error": "transaction must be an EditPatch object"}
        return {"content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": True}
    from library.tools.resolve_axi_sdk import AxiTransaction, apply_transaction
    try:
        typed = AxiTransaction(
            id=patch["id"], project=patch["project"],
            timeline=patch["timeline"],
            base_generation=int(patch["base_generation"]),
            capability=patch["capability"],
            affected_spans=tuple(tuple(span) for span in patch["affected_spans"]),
            conflict_domains=tuple(patch["conflict_domains"]),
            operations=tuple(patch["operations"]),
            preconditions=tuple(patch.get("preconditions", ())),
            postconditions=tuple(patch.get("postconditions", ())),
        )
        receipt = apply_transaction(typed)
        return {"content": [{"type": "text", "text": json.dumps(
            receipt, ensure_ascii=False, default=str)}],
            **({"isError": True} if receipt.get("status")
               != "committed" else {})}
    except (KeyError, TypeError, ValueError) as exc:
        payload = {"ok": False, "exit": 2, "error": str(exc)}
        return {"content": [{"type": "text", "text": json.dumps(payload)}],
                "isError": True}


def refusal_payload(verb_name: str, what: str, why: str, fix: str) -> dict:
    """A `REN_RESULT`-shaped refusal for a call this server never
    executes. Same keys as `RenRefusal.to_dict()` plus the exit (pinned
    by `test_mcp_server.py`), so a host parses one shape either way."""
    return {"ok": False, "exit": 4, "code": WRITE_REFUSED_CODE,
            "what": what, "why": why, "fix": fix,
            "user_message": ("I couldn't safely finish this step. "
                             "The requested change has not been confirmed; "
                             "resolve the issue below before continuing."),
            "recovery_class": "blocked", "conflict_class": "",
            "ask_reason": "",
            "verb": verb_name}


def parse_ren_result(stderr_text: str) -> dict | None:
    """The child's final `REN_RESULT {...}` line as a dict, or None."""
    payload = None
    for line in stderr_text.splitlines():
        if line.startswith(REN_RESULT_PREFIX + " "):
            try:
                payload = json.loads(line[len(REN_RESULT_PREFIX) + 1:])
            except ValueError:
                payload = None
    return payload if isinstance(payload, dict) else None


def run_verb(argv: list, timeout: float = TOOL_TIMEOUT_SECONDS) -> dict:
    """Run one verb through the existing CLI dispatch and return the
    typed envelope `tools/call` serves. `argv[0]` is the verb name."""
    root = engine_root()
    env = dict(os.environ)
    env["REN_ENGINE_ROOT"] = str(root)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(root), env.get("PYTHONPATH", "")) if part)
    env["REN_OUTPUT"] = "json"
    try:
        done = subprocess.run(
            [sys.executable, "-m", "ren.cli", *argv],
            capture_output=True, encoding="utf-8", timeout=timeout,
            cwd=str(root), env=env, check=False)
    except subprocess.TimeoutExpired as exc:
        partial = ""
        try:
            partial = (exc.stderr or b"").decode("utf-8", "replace")
            if isinstance(exc.stderr, str):
                partial = exc.stderr
        except Exception:  # noqa: BLE001 - best-effort stderr
            partial = ""
        return {"ok": False, "exit": 1, "verb": argv[0] if argv else "",
                "argv": argv, "stdout": "", "stderr": partial,
                "ren_result": {"ok": False, "exit": 1,
                               "code": "REN_MCP_TIMEOUT",
                               "exception": f"tool call past {timeout}s"}}
    stderr_text = done.stderr or ""
    return {"ok": done.returncode == 0, "exit": done.returncode,
            "verb": argv[0] if argv else "", "argv": argv,
            "stdout": done.stdout or "", "stderr": stderr_text,
            "ren_result": parse_ren_result(stderr_text)}


def _bad_args(verb_name: str, message: str) -> dict:
    """A usage error (the CLI's exit 2) for a mistyped tool call."""
    return {"ok": False, "exit": 2, "verb": verb_name, "argv": None,
            "stdout": "", "stderr": message,
            "ren_result": {"ok": False, "exit": 2,
                           "code": "REN_MCP_BAD_ARGS",
                           "exception": message}}


def build_argv(verb, arguments: dict) -> tuple:
    """`(argv, error)`: map typed arguments onto CLI tokens. Exactly
    one is None. The declared POSITIONAL arguments go first, in the
    order the registry declares them; an argument that names a FLAG
    reaches the verb as that flag and its value; `json`/`dry_run` flags
    follow from the catalog booleans; `args` appends verbatim."""
    name = verb.name
    argv = [name]
    for param in verb.params:
        if param.flag or param.name not in arguments:
            continue
        value = arguments[param.name]
        if not isinstance(value, str) or not value:
            return None, _bad_args(
                name, f"argument {param.name!r} must be a non-empty "
                      f"string")
        argv.append(value)
    for param in verb.params:
        if param.required and param.name not in arguments:
            return None, _bad_args(
                name, f"missing required argument {param.name!r}")
    for param in verb.params:
        if not param.flag or param.name not in arguments:
            continue
        value = arguments[param.name]
        if not isinstance(value, str) or not value:
            return None, _bad_args(
                name, f"argument {param.name!r} must be a non-empty "
                      f"string")
        argv.extend([param.flag, value])
    if verb.json and "json" in arguments:
        if not isinstance(arguments["json"], bool):
            return None, _bad_args(name, "argument 'json' must be boolean")
        if arguments["json"]:
            argv.append("--json")
    elif verb.json:
        argv.append("--json")
    mode = execution_mode(verb)
    if verb.dry_run and "dry_run" in arguments:
        if not isinstance(arguments["dry_run"], bool):
            return None, _bad_args(
                name, "argument 'dry_run' must be boolean")
        if not arguments["dry_run"]:
            if mode == "dry_run":
                payload = refusal_payload(
                    name,
                    f"ren {name} without --dry-run changes files",
                    "this server only runs dry-run verbs with --dry-run "
                    "forced; the write path is closed",
                    f"call this tool with dry_run:true, or run `ren {name}` "
                    f"in a shell to apply")
                return None, {"ok": False, "exit": 4, "verb": name,
                              "argv": None, "stdout": "",
                              "stderr": payload["what"],
                              "ren_result": payload}
        else:
            argv.append("--dry-run")
    elif verb.dry_run:
        argv.append("--dry-run")
    extra = arguments.get("args", [])
    if not isinstance(extra, list) or not all(
            isinstance(token, str) for token in extra):
        return None, _bad_args(
            name, "argument 'args' must be an array of strings")
    argv.extend(extra)
    return argv, None


def call_tool(verb, arguments: dict, execute=run_verb) -> dict:
    """A `tools/call` result (content plus `isError`), routing through
    the CLI dispatch or refusing before it, per `execution_mode`."""
    name = verb.name
    if execution_mode(verb) == "typed_lifecycle":
        try:
            allowed = {param.name for param in verb.params} | {"json"}
            unknown = set(arguments) - allowed
            if unknown:
                raise ValueError(f"unknown storage arguments: {sorted(unknown)}")
            for param in verb.params:
                value = arguments.get(param.name)
                if param.required and value is None:
                    raise ValueError(f"missing required argument {param.name!r}")
                if value is not None and (not isinstance(value, str) or not value):
                    raise ValueError(
                        f"argument {param.name!r} must be a non-empty string")
            from library.tools import storage_cli, storage_lifecycle

            if name == "storage-plan":
                projects = storage_cli._projects(arguments.get("project", ""))
                result = storage_lifecycle.plan(
                    projects, output=arguments["output"])
            else:
                operation = {"storage-collect": storage_lifecycle.collect,
                             "storage-purge": storage_lifecycle.purge,
                             "storage-restore": storage_lifecycle.restore}[name]
                result = operation(arguments["manifest"])
            envelope = {"ok": True, "exit": 0, "verb": name,
                        "result": result}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            payload = refusal_payload(
                name, f"storage lifecycle operation refused: {exc}",
                "the registered ownership, manifest, or filesystem checks did not pass",
                "refresh the storage plan and resolve the reported condition")
            envelope = {"ok": False, "exit": 3, "verb": name,
                        "stderr": payload["what"], "ren_result": payload}
        return {"content": [{"type": "text", "text": json.dumps(envelope)}],
                **({"isError": True} if not envelope["ok"] else {})}
    if execution_mode(verb) == "refused":
        payload = refusal_payload(
            name,
            f"ren {name} may write files, Resolve state or renders",
            "this server starts with read-only and dry-run verbs; "
            "write verbs are listed for discovery but never executed",
            f"run `ren {name}` in a shell")
        return {"content": [{"type": "text",
                             "text": json.dumps(
                                 {"ok": False, "exit": 4, "verb": name,
                                  "argv": None, "stdout": "",
                                  "stderr": payload["what"],
                                  "ren_result": payload})}],
                "isError": True}
    argv, error = build_argv(verb, arguments)
    if error is not None:
        return {"content": [{"type": "text",
                             "text": json.dumps(error)}],
                "isError": True}
    envelope = execute(argv)
    result = {"content": [{"type": "text",
                           "text": json.dumps(envelope)}]}
    if not envelope.get("ok", False):
        result["isError"] = True
    return result


def _error(code: int, message: str, request_id=None) -> dict:
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": code, "message": message}}


def _result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def handle_message(message, execute=run_verb):
    """One JSON-RPC message -> response dict, None for notifications,
    or a list of responses for a batch. Pure dispatch; `serve` owns
    the stdio framing."""
    from ren.commands import VERBS

    if isinstance(message, list):
        responses = [handle_message(item, execute) for item in message]
        return [response for response in responses if response is not None]
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(-32600, "invalid request: expected JSON-RPC 2.0")
    if "method" not in message or not isinstance(message["method"], str):
        return _error(-32600, "invalid request: missing method",
                      message.get("id"))
    method = message["method"]
    request_id = message.get("id")
    params = message.get("params", {})
    if not isinstance(params, dict):
        return _error(-32602, "invalid params: expected an object",
                      request_id)
    if request_id is None:
        if method in ("notifications/initialized",
                      "notifications/cancelled"):
            return None
        return _error(-32600,
                      "invalid request: missing id outside a notification")

    if method == "initialize":
        asked = params.get("protocolVersion", "")
        negotiated = (asked if asked in PROTOCOL_VERSIONS
                      else PROTOCOL_VERSIONS[0])
        return _result(request_id, {
            "protocolVersion": negotiated,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ren",
                           "version": server_version()}})
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {
        "tools": [axi_tool(), axi_edit_request_tool(),
                  axi_edit_request_plan_tool(), axi_transaction_tool(),
                  axi_transaction_submit_tool(), axi_transaction_retrieve_tool(),
                  axi_transaction_cancel_tool(),
                  axi_transaction_plan_tool(),
                      *[tool_for_verb(verb) for verb in VERBS]]})
    if method == "tools/call":
        tool = params.get("name", "")
        arguments = params.get("arguments", {})
        if not isinstance(tool, str) or not tool:
            return _error(-32602, "invalid params: missing tool name",
                          request_id)
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return _error(-32602,
                          "invalid params: arguments must be an object",
                          request_id)
        if tool == "resolve_axi":
            return _result(request_id, call_axi(arguments))
        if tool == "resolve_axi_transaction":
            return _result(request_id, call_axi_transaction(arguments))
        if tool == "resolve_axi_edit_request":
            return _result(request_id, call_axi_edit_request(arguments))
        if tool == "resolve_axi_edit_request_plan":
            return _result(request_id, call_axi_edit_request_plan(arguments))
        if tool == "resolve_axi_transaction_submit":
            return _result(request_id, call_axi_transaction_submit(arguments))
        if tool == "resolve_axi_transaction_retrieve":
            return _result(request_id, call_axi_transaction_retrieve(arguments))
        if tool == "resolve_axi_transaction_cancel":
            return _result(request_id, call_axi_transaction_cancel(arguments))
        if tool == "resolve_axi_transaction_plan":
            return _result(request_id, call_axi_transaction_plan(arguments))
        by_tool = {tool_name(verb.name): verb for verb in VERBS}
        verb = by_tool.get(tool)
        if verb is None:
            return _error(-32602, f"unknown tool {tool!r}", request_id)
        return _result(request_id, call_tool(verb, arguments, execute))
    if method in ("resources/list", "prompts/list"):
        key = method.split("/")[0]
        return _result(request_id, {key: []})
    return _error(-32601, f"method not found: {method}", request_id)


def serve(stdin=None, stdout=None, execute=run_verb) -> int:
    """The stdio loop: one JSON-RPC object per line in, one per line
    out. Logging goes to stderr; stdout carries protocol only."""
    if stdin is None:
        stdin = sys.stdin.buffer
    if stdout is None:
        stdout = sys.stdout.buffer
    for raw in stdin:
        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            continue
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response = _error(-32700, "parse error")
            stdout.write((json.dumps(response) + "\n").encode("utf-8"))
            stdout.flush()
            continue
        try:
            response = handle_message(message, execute)
        except Exception as exc:  # noqa: BLE001 - one bad message never kills the server
            print(f"ren mcp: request failed: {exc!r}", file=sys.stderr)
            response = _error(-32603, "internal error",
                              message.get("id") if isinstance(
                                  message, dict) else None)
        if response is None:
            continue
        try:
            stdout.write((json.dumps(response) + "\n").encode("utf-8"))
            stdout.flush()
        except BrokenPipeError:
            return 0
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in ([],):
        root = engine_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        return serve()
    if args[:1] == ["--help"] or args[:1] == ["-h"]:
        print("usage: python -m ren.mcp_server\n"
              "\n"
              "Serve the Ren verb catalog as MCP tools over stdio "
              "(one JSON-RPC object per line). See docs/customer/mcp.md "
              "for host setup. Nothing starts this automatically.")
        return 0
    print(f"ren mcp: unknown arguments {args!r}; "
          f"`python -m ren.mcp_server --help` lists them.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
