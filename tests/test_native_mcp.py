"""native_mcp: the wrapper is driven, never trusted.

Every test here fakes the node child process - no Resolve, no bundle,
no real project (AGENTS.md 8). Two behaviours get permanent coverage
because the verbs above depend on them:

1. A refusal from the server (or silence, or a missing bundle)
   surfaces as `NativeMcpError` carrying the server's own words, so
   the axi layer can print `error:` plus a fix instead of hanging or
   dumping a traceback.
2. The request actually sent is `tools/call` with the tool name and
   arguments the verb asked for - the shape Blackmagic's wrapper
   routes to its binary.
"""

import json

import pytest

from library.tools import native_mcp
from library.tools.native_mcp import NativeMcpError, call


class _FakeStdin:
    def __init__(self):
        self.written = []

    def write(self, text):
        self.written.append(text)

    def flush(self):
        pass


class _FakeProc:
    def __init__(self, lines):
        self.stdin = _FakeStdin()
        self.stdout = iter(lines)
        self.killed = False
        self.argv = None

    def kill(self):
        self.killed = True


def _result(text=None, error=None, is_error=False):
    if error is not None:
        return {"jsonrpc": "2.0", "id": 2, "error": error}
    return {"jsonrpc": "2.0", "id": 2,
            "result": {"content": [{"type": "text", "text": text or ""}],
                       "isError": is_error}}


def _patch(monkeypatch, lines):
    monkeypatch.setattr(native_mcp, "wrapper_path",
                        lambda: "/fake/server/index.js")
    procs = []

    def factory(argv, **_kwargs):
        proc = _FakeProc(lines)
        proc.argv = argv
        procs.append(proc)
        return proc

    monkeypatch.setattr(native_mcp.subprocess, "Popen", factory)
    return procs


def test_call_sends_tools_call_and_returns_text(monkeypatch):
    procs = _patch(monkeypatch, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}),
        json.dumps(_result("hello")),
    ])
    assert call("get_resolve_status", {}) == "hello"
    sent = [json.loads(line) for line in procs[0].stdin.written]
    assert sent[0]["method"] == "initialize"
    assert sent[2] == {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "get_resolve_status",
                                  "arguments": {}}}
    assert procs[0].killed


def test_call_surfaces_a_refusal_in_its_own_words(monkeypatch):
    _patch(monkeypatch, [json.dumps(_result("needs a version",
                                            is_error=True))])
    with pytest.raises(NativeMcpError, match="needs a version"):
        call("get_whats_new", {})


def test_call_names_silence_instead_of_hanging(monkeypatch):
    _patch(monkeypatch, [])
    with pytest.raises(NativeMcpError, match="no answer"):
        call("get_resolve_status", {}, timeout=2)


def test_a_missing_bundle_or_node_is_a_sentence_not_a_traceback(monkeypatch):
    monkeypatch.setattr(native_mcp, "BUNDLE_PATH",
                        "/nonexistent/DaVinciResolve.mcpb")
    with pytest.raises(NativeMcpError, match="no native MCP bundle"):
        call("get_resolve_status", {})

    monkeypatch.setattr(native_mcp, "wrapper_path",
                        lambda: "/fake/server/index.js")
    monkeypatch.setattr(native_mcp.shutil, "which", lambda _name: None)
    with pytest.raises(NativeMcpError, match="no `node`"):
        call("get_resolve_status", {})
