"""Backend for resolve-axi's knowledge and LUT verbs: Blackmagic's own MCP.

`resolve-axi api ...` (stubs, search, docs, whats-new) and the `luts`
writes do not reimplement what Blackmagic ships: they call the native
21.1 MCP server (the `DaVinciResolve.mcpb` bundle inside the Resolve
app) over stdio JSON-RPC and shape the answer as TOON one layer up.
Nothing here touches the captain's project or timelines: these tools
read documentation, the changelog and the shared LUT directory, or
write into the shared `LUT/MCP` shelf. Reads that DO touch his
session live in `resolve_axi` itself, over the scripting API.

The bundle's `server/index.js` is a thin wrapper that proxies to the
`ResolveMCP` binary at a fixed absolute path, so it runs fine from a
cache copy: this module unpacks it under `~/.cache/resolve-axi/` keyed
by the bundle's mtime (a Resolve update re-unpacks once) and drives it
exactly like an MCP client - `initialize`, `notifications/initialized`,
`tools/call` - with a reader thread so a slow first spawn (the wrapper
cold-starts its child) cannot wedge the caller past `timeout`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import zipfile

from library.tools.resolve_lock import under_lease

#: The native server, as shipped inside the Resolve app.
BUNDLE_PATH = ("/Applications/DaVinci Resolve/DaVinci Resolve.app/"
               "Contents/Resources/DaVinciResolve.mcpb")

#: Where the wrapper's entry point lives inside the bundle.
WRAPPER_ENTRY = "server/index.js"

#: Machine-local unpack cache. Scratch, not project state.
CACHE_ROOT = os.path.join(os.path.expanduser("~"), ".cache", "resolve-axi")


class NativeMcpError(RuntimeError):
    """The native server could not be reached or refused the call."""


def _node() -> str:
    path = shutil.which("node")
    if not path:
        raise NativeMcpError(
            "no `node` on PATH - the native MCP wrapper needs it; "
            "install Node.js and re-run.")
    return path


def wrapper_path() -> str:
    """Unpacked `server/index.js`, refreshing the cache when stale."""
    if not os.path.exists(BUNDLE_PATH):
        raise NativeMcpError(
            f"no native MCP bundle at {BUNDLE_PATH} - install or update "
            f"DaVinci Resolve Studio (21.1+) and re-run.")
    try:
        mtime = str(int(os.path.getmtime(BUNDLE_PATH)))
    except OSError as exc:
        raise NativeMcpError(
            f"cannot stat the native MCP bundle ({exc}).") from exc
    entry = os.path.join(CACHE_ROOT, f"mcpb-{mtime}", WRAPPER_ENTRY)
    if not os.path.exists(entry):
        try:
            os.makedirs(os.path.dirname(entry), exist_ok=True)
            with zipfile.ZipFile(BUNDLE_PATH) as bundle:
                bundle.extract(WRAPPER_ENTRY,
                               os.path.join(CACHE_ROOT, f"mcpb-{mtime}"))
        except Exception as exc:
            raise NativeMcpError(
                f"cannot unpack the native MCP bundle ({exc}).") from exc
    if not os.path.exists(entry):
        raise NativeMcpError(
            f"the native MCP bundle holds no {WRAPPER_ENTRY} - refusing "
            f"to guess at a new layout.")
    return entry


@under_lease("call the native Resolve MCP server")
def call(tool: str, args: dict | None = None, timeout: int = 90) -> str:
    """Call one native MCP tool, return its first text block.

    Raises `NativeMcpError` with the server's own words when the call
    is refused or fails - the caller shapes that into `error:` + fix.
    """
    entry = wrapper_path()
    try:
        proc = subprocess.Popen(
            [_node(), entry],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True, bufsize=1, encoding="utf-8")
    except (OSError, NativeMcpError) as exc:
        raise NativeMcpError(f"cannot start the native MCP wrapper "
                             f"({exc}).") from exc

    def send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    answer: list = []
    failed: list = []

    def reader() -> None:
        try:
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == 2:
                    answer.append(msg)
                    break
        except Exception as exc:  # noqa: BLE001 - the thread must not die silent
            failed.append(exc)

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {}})
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
              "params": {"name": tool, "arguments": args or {}}})
        worker = threading.Thread(target=reader, daemon=True)
        worker.start()
        worker.join(timeout=timeout)
    except (OSError, ValueError) as exc:
        proc.kill()
        raise NativeMcpError(
            f"the native MCP call {tool!r} broke mid-flight "
            f"({exc}).") from exc
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 - reaped either way
            pass
    if failed:
        raise NativeMcpError(f"the native MCP call {tool!r} broke "
                             f"mid-flight ({failed[0]}).")
    if not answer:
        raise NativeMcpError(
            f"the native MCP call {tool!r} gave no answer within "
            f"{timeout}s - Resolve may be starting; re-run.")
    result = answer[0].get("result") or {}
    if answer[0].get("error"):
        detail = answer[0]["error"].get("message", answer[0]["error"])
        raise NativeMcpError(f"the native MCP refused {tool!r}: {detail}.")
    blocks = result.get("content") or []
    if result.get("isError"):
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        raise NativeMcpError(f"the native MCP refused {tool!r}: {text}.")
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "")
    raise NativeMcpError(f"the native MCP call {tool!r} returned no text.")
