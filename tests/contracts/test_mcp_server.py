"""The Ren MCP server: one tool per catalog verb, derived - never listed twice.

Defect caught in advance: an MCP adapter with its own hand-maintained
tool list drifts from `ren --help` the first time a verb is added, and
a host then calls a verb the server never heard of - or never learns a
new one. `ren/mcp_server.py` derives names, descriptions, schemas and
effect annotations from `ren.commands.VERBS`, and routes each call
through the existing `ren.cli` dispatch, so exit codes and the final
`REN_RESULT` line are what the shell gets.

Hermetic: the stdio fixture builds its project under `tmp_path` and
addresses it by absolute path, so it never touches the projects root
the session sees. No Resolve, no model, no renders.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools.ren_refusal import RenRefusal
from ren import mcp_server
from ren.commands import VERBS
from ren.mcp_server import (
    PROTOCOL_VERSIONS,
    WRITE_REFUSED_CODE,
    build_argv,
    call_tool,
    execution_mode,
    handle_message,
    refusal_payload,
    serve,
    tool_for_verb,
    tool_name,
)

ROOT = Path(__file__).resolve().parents[2]

BY_NAME = {verb.name: verb for verb in VERBS}


def _list_tools():
    response = handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
         "params": {}})
    assert "result" in response
    return response["result"]["tools"]


# ── the catalog is the single source ────────────────────────────────

def test_tools_cover_every_catalog_verb_exactly_once():
    tools = _list_tools()
    assert [tool["_meta"]["verb"] for tool in tools] == [
        verb.name for verb in VERBS]
    assert [tool["name"] for tool in tools] == [
        tool_name(verb.name) for verb in VERBS]
    assert len({tool["name"] for tool in tools}) == len(tools)


def test_each_tool_carries_its_verbs_effects_and_required_arguments():
    by_verb = {tool["_meta"]["verb"]: tool for tool in _list_tools()}
    assert set(by_verb) == set(BY_NAME)
    for name, verb in BY_NAME.items():
        tool = by_verb[name]
        assert tool["_meta"]["effects"] == list(verb.effects), name
        assert tool["_meta"]["read_only"] == verb.read_only, name
        assert tool["inputSchema"] == verb.input_schema, name
        assert tool["inputSchema"]["required"] == [
            param.name for param in verb.params if param.required], name


def test_annotations_read_off_the_catalog_effects():
    by_verb = {tool["_meta"]["verb"]: tool for tool in _list_tools()}
    status = by_verb["status"]
    assert status["annotations"]["readOnlyHint"] is True
    assert status["annotations"]["destructiveHint"] is False
    assert status["annotations"]["idempotentHint"] is True
    edit = by_verb["edit"]
    assert edit["annotations"]["readOnlyHint"] is False
    assert edit["annotations"]["destructiveHint"] is True
    assert edit["annotations"]["idempotentHint"] is True
    assert edit["_meta"]["execution_mode"] == "dry_run"
    new = by_verb["new"]
    assert new["_meta"]["execution_mode"] == "refused"
    assert "in a shell" in new["description"]
    search = by_verb["search"]
    assert search["annotations"]["openWorldHint"] is True


def test_the_comparison_can_fail():
    """This contract is a real gate: a verb with different effects or a
    missing row changes the derived tool, so drift fails above."""
    from ren.commands import Verb
    altered = Verb("status", "Projects", "Show a project's pipeline status",
                   effects=("read", "project_write"), subcommand="status")
    assert (tool_for_verb(altered)["annotations"]["destructiveHint"]
            is True)
    assert (tool_for_verb(altered)["_meta"]["effects"]
            != tool_for_verb(BY_NAME["status"])["_meta"]["effects"])
    assert len(_list_tools()) == len(VERBS)
    assert {tool["name"] for tool in _list_tools()} != (
        {tool["name"] for tool in _list_tools()} | {"ren_missing_verb"})


def test_no_second_hand_maintained_catalog():
    """The server derives from `VERBS`: its source constructs no `Verb`
    rows and names no verb literal of its own."""
    source = Path(mcp_server.__file__).read_text(encoding="utf-8")
    assert "Verb(" not in source
    assert "VERBS" in source
    for verb in VERBS:
        assert f'"ren_{verb.name.replace("-", "_")}"' not in source, verb.name


def test_refusal_payload_carries_the_ren_result_shape():
    refused = RenRefusal("what happened", "why it refused",
                         "run `ren x` to fix it")
    payload = refusal_payload("edit", "what happened", "why it refused",
                              "run `ren x` to fix it")
    assert set(payload) == (set(refused.to_dict()) | {"ok", "exit", "verb"})
    assert payload["exit"] == 4
    assert payload["code"] == WRITE_REFUSED_CODE
    assert payload["recovery_class"] == "blocked"


def test_execution_modes_match_the_brief():
    assert execution_mode(BY_NAME["status"]) == "direct"
    assert execution_mode(BY_NAME["edit"]) == "dry_run"
    assert execution_mode(BY_NAME["new"]) == "refused"
    assert execution_mode(BY_NAME["uninstall"]) == "dry_run"


# ── protocol framing, in process ─────────────────────────────────────

def _roundtrip(messages, execute=None):
    stdin = io.BytesIO(
        "".join(json.dumps(message) + "\n"
                for message in messages).encode("utf-8"))
    stdout = io.BytesIO()
    kwargs = {} if execute is None else {"execute": execute}
    assert serve(stdin, stdout, **kwargs) == 0
    return [json.loads(line) for line in
            stdout.getvalue().decode("utf-8").splitlines()]


def test_initialize_negotiates_the_protocol_version():
    [echoed, latest] = _roundtrip([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-03-26"}},
        {"jsonrpc": "2.0", "id": 2, "method": "initialize",
         "params": {"protocolVersion": "2099-01-01"}}])
    assert echoed["result"]["protocolVersion"] == "2025-03-26"
    assert latest["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]
    assert latest["result"]["serverInfo"]["name"] == "ren"
    assert latest["result"]["capabilities"] == {"tools": {}}


def test_notifications_get_no_reply_and_ping_answers():
    assert _roundtrip([
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/cancelled",
         "params": {"reason": "done"}}]) == [
        {"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_protocol_errors_use_json_rpc_codes():
    [listed] = _roundtrip([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
         "params": {}}])
    assert listed["result"]["tools"]
    stdin = io.BytesIO(b"{not json\n")
    stdout = io.BytesIO()
    assert serve(stdin, stdout) == 0
    [parse_error] = [json.loads(line) for line in
                     stdout.getvalue().decode("utf-8").splitlines()]
    assert parse_error["error"]["code"] == -32700
    [invalid] = _roundtrip([{"id": 1, "method": "tools/list"}])
    assert invalid["error"]["code"] == -32600
    [unknown_method] = _roundtrip(
        [{"jsonrpc": "2.0", "id": 1, "method": "tools/burn", "params": {}}])
    assert unknown_method["error"]["code"] == -32601
    [unknown_tool] = _roundtrip(
        [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
          "params": {"name": "ren_missing", "arguments": {}}}])
    assert unknown_tool["error"]["code"] == -32602
    [bad_params] = _roundtrip(
        [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
          "params": {"name": "ren_status", "arguments": []}}])
    assert bad_params["error"]["code"] == -32602


def test_argv_mapping_puts_the_project_first_and_json_on():
    argv, error = build_argv(BY_NAME["status"], {"project": "demo"})
    assert error is None
    assert argv == ["status", "demo"]
    argv, error = build_argv(
        BY_NAME["work-state"], {"project": "demo", "json": False})
    assert error is None
    assert argv == ["work-state", "demo"]
    assert "--json" not in argv
    argv, error = build_argv(
        BY_NAME["timeline"], {"args": ["log", "--reel", "3"]})
    assert error is None
    assert argv == ["timeline", "log", "--reel", "3"]


def test_missing_or_mistyped_arguments_are_usage_errors():
    _, error = build_argv(BY_NAME["status"], {})
    assert error["exit"] == 2
    assert "project" in error["ren_result"]["exception"]
    _, error = build_argv(BY_NAME["status"], {"project": "demo",
                                              "args": "now"})
    assert error["exit"] == 2


def test_dry_run_verbs_force_the_flag_and_refuse_without_it():
    seen = []

    def fake(argv, timeout=0):
        seen.append(argv)
        return {"ok": True, "exit": 0, "verb": argv[0], "argv": argv,
                "stdout": "plan only", "stderr": "", "ren_result": None}

    result = call_tool(BY_NAME["edit"], {"project": "demo"}, fake)
    assert "isError" not in result
    assert seen == [["edit", "demo", "--json", "--dry-run"]]
    refused = call_tool(BY_NAME["edit"], {"project": "demo",
                                          "dry_run": False}, fake)
    assert refused["isError"] is True
    assert len(seen) == 1
    payload = json.loads(refused["content"][0]["text"])
    assert payload["ren_result"]["exit"] == 4
    assert payload["ren_result"]["code"] == WRITE_REFUSED_CODE


def test_write_verbs_are_refused_without_executing():
    seen = []

    def fake(argv, timeout=0):
        seen.append(argv)
        raise AssertionError("a refused verb must never execute")

    result = call_tool(BY_NAME["new"], {"slug": "demo"}, fake)
    assert result["isError"] is True
    assert seen == []
    payload = json.loads(result["content"][0]["text"])
    assert payload["ren_result"]["exit"] == 4
    assert payload["ren_result"]["code"] == WRITE_REFUSED_CODE
    assert "ren new" in payload["ren_result"]["fix"]


# ── the acceptance fixture: a real server over stdio ────────────────

def _write_minimal_project(directory: Path) -> Path:
    project = directory / "demo"
    (project / "raw").mkdir(parents=True)
    (project / "exports").mkdir(parents=True)
    (project / "project.yaml").write_text(
        "project_format_version: 2\n"
        "name: Demo Project\n"
        "slug: demo\n"
        "client: \"\"\n"
        "created: \"2026-10-01\"\n"
        "status: in_progress\n"
        "source:\n"
        "  fps: 29.97\n"
        "  language: en\n"
        "  shape: speech\n"
        "pipeline:\n"
        "  brand_template: \"\"\n"
        "  creative_brief: brief.md\n"
        "resolve:\n"
        "  project_name: Demo Project\n"
        "  database: local\n"
        "  folder: \"\"\n"
        "  timeline_name: Main Edit\n"
        "tags:\n"
        "  - test\n"
        "description: A demo\n"
        "ren_version: \"ren 0.1.0+abc.dev.personal\"\n",
        encoding="utf-8")
    (project / "brief.md").write_text("brief\n", encoding="utf-8")
    return project


def _run_stdio_server(requests: list) -> list:
    """Launch `python -m ren.mcp_server` over stdio, send every
    request, close stdin, and return each response line. The child
    inherits this session's sandbox (`PIPELINE_PROJECTS_ROOT` points
    at an empty directory), so slugs cannot bind a real project."""
    env = dict(os.environ)
    env["REN_ENGINE_ROOT"] = str(ROOT)
    proc = subprocess.Popen(
        [sys.executable, "-m", "ren.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, cwd=str(ROOT), env=env,
        encoding="utf-8")
    try:
        script = "".join(json.dumps(message) + "\n" for message in requests)
        try:
            stdout, _ = proc.communicate(script, timeout=120)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                stdout, _ = proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, _ = proc.communicate()
            pytest.fail("the MCP server did not answer within 120s")
        assert proc.returncode == 0, (
            f"the MCP server exited {proc.returncode}")
        return [json.loads(line) for line in stdout.splitlines()
                if line.strip()]
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


def test_stdio_lists_calls_and_refuses(tmp_path):
    project = _write_minimal_project(tmp_path / "projects")
    missing = tmp_path / "projects" / "nope"
    requested_slug = "mcp-must-not-create-this"

    responses = _run_stdio_server([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18",
                    "clientInfo": {"name": "acceptance", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "ren_status",
                    "arguments": {"project": str(project)}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "ren_status",
                    "arguments": {"project": str(missing)}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "ren_new",
                    "arguments": {"slug": requested_slug}}},
    ])

    by_id = {response["id"]: response for response in responses
             if "id" in response}
    assert by_id[1]["result"]["protocolVersion"] == "2025-06-18"

    tools = by_id[2]["result"]["tools"]
    assert [tool["_meta"]["verb"] for tool in tools] == [
        verb.name for verb in VERBS]
    for tool in tools:
        verb = BY_NAME[tool["_meta"]["verb"]]
        assert tool["_meta"]["effects"] == list(verb.effects)
        assert tool["inputSchema"]["required"] == [
            param.name for param in verb.params if param.required]

    called = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert by_id[3]["result"].get("isError") in (None, False)
    assert called["ok"] is True and called["exit"] == 0
    assert "Demo Project" in called["stdout"]
    assert called["ren_result"] is None

    refused = json.loads(by_id[4]["result"]["content"][0]["text"])
    assert by_id[4]["result"].get("isError") is True
    assert refused["ok"] is False
    result = refused["ren_result"]
    assert result["ok"] is False
    assert result["exit"] == 4
    assert result["code"]
    assert result["fix"]
    assert result["recovery_class"]

    write = json.loads(by_id[5]["result"]["content"][0]["text"])
    assert by_id[5]["result"].get("isError") is True
    assert write["ren_result"]["exit"] == 4
    assert write["ren_result"]["code"] == WRITE_REFUSED_CODE
    assert not (tmp_path / "projects" / requested_slug).exists()
