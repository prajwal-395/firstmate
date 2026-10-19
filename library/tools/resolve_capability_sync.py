"""Discover upstream Resolve capabilities without changing AXI execution.

`scan` is read-only. `record` files newly observed capabilities in the
evaluation ledger and advances source fingerprints; it never edits the AXI
operation registry or enables a capability.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from library.tools import edit_algebra

ROOT = Path(__file__).resolve().parents[2]
SOURCES_PATH = ROOT / "docs" / "resolve_capability_sources.json"
LEDGER_PATH = ROOT / "docs" / "resolve_capability_candidates.json"
RESOLVE_SCRIPT_ROOTS = (
    Path("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"),
    Path("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/Developer/Scripting"),
)
MCP_BUNDLE = Path("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Resources/DaVinciResolve.mcpb")
GITHUB_API = "https://api.github.com"


def _read_url(url: str, *, timeout: int = 20) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ren-resolve-capability-sync/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _script_root() -> Path | None:
    configured = os.environ.get("RESOLVE_SCRIPT_API")
    candidates = ([Path(configured)] if configured else []) + list(RESOLVE_SCRIPT_ROOTS)
    return next((path for path in candidates if path.is_dir()), None)


def _version(root: Path) -> str:
    for app_info in (Path("/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Info.plist"),):
        if app_info.exists():
            try:
                result = subprocess.run(["/usr/libexec/PlistBuddy", "-c", "Print :CFBundleShortVersionString", str(app_info)], capture_output=True, text=True, encoding="utf-8", timeout=5, check=False)
                if result.returncode == 0:
                    return result.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                pass
    return "unknown"


def _api_methods(stub: str) -> set[str]:
    """Return documented Class.method signatures from the installed stub."""
    try:
        tree = ast.parse(stub)
    except SyntaxError:
        return set()
    found: set[str] = set()
    for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        for method in cls.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.name.startswith("_"):
                revision = hashlib.sha256(ast.dump(method).encode()).hexdigest()[:12]
                found.add(f"{cls.name}.{method.name}#{revision}")
    return found


def _changelog_entries(changelog: str) -> set[str]:
    """Extract versioned Added entries so new what's-new items are diffable."""
    version = "unknown"
    in_added = False
    entries = set()
    for line in changelog.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            version, in_added = heading.group(1), False
            continue
        if re.match(r"^Added:\s*$", line):
            in_added = True
            continue
        if re.match(r"^[A-Z][A-Za-z ]+:\s*$", line):
            in_added = False
        if in_added and line.startswith("-"):
            text = " ".join(line[1:].strip().split())
            revision = hashlib.sha256(text.encode()).hexdigest()[:12]
            entries.add(f"api-change:{version}:{text}#{revision}")
    return entries


def _mcp_tools(source: str) -> set[str]:
    """Extract explicit MCP tool declarations from Python source text."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    result = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
            continue
        if any(".tool" in ast.unparse(decorator) for decorator in node.decorator_list):
            revision = hashlib.sha256(ast.dump(node).encode()).hexdigest()[:12]
            result.add(f"mcp:{node.name}#{revision}")
    return result


def _official_mcp_tools() -> list[dict]:
    """Read the official bundle's declared MCP tools through tools/list."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is required to query the installed Blackmagic MCP tools/list")
    from library.tools.native_mcp import wrapper_path

    proc = subprocess.Popen([node, wrapper_path()], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="utf-8")
    try:
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n")
        proc.stdin.flush()
        responses: queue.Queue = queue.Queue()

        def read_response() -> None:
            for line in proc.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if message.get("id") == 2:
                    responses.put(message)
                    return

        import threading
        reader = threading.Thread(target=read_response, daemon=True)
        reader.start()
        try:
            message = responses.get(timeout=20)
        except queue.Empty as exc:
            raise RuntimeError("Blackmagic MCP tools/list timed out after 20 seconds") from exc
        if message.get("error"):
            raise RuntimeError(f"Blackmagic MCP tools/list failed: {message['error']}")
        result = message.get("result", {})
        return [{"name": str(tool["name"]), "description": str(tool.get("description", ""))}
                for tool in result.get("tools", [])]
    finally:
        proc.kill()


def _repo_snapshot(source: dict) -> tuple[str, set[str]]:
    """Inspect selected text files at the pinned commit and latest upstream."""
    repo = source["repo"]
    pin = source["pinned_commit"]
    owner_repo = repo.removeprefix("https://github.com/").removesuffix(".git")
    latest = json.loads(_read_url(f"{GITHUB_API}/repos/{owner_repo}/commits/{source.get('branch', 'main')}").decode("utf-8"))["sha"]
    ids: set[str] = set()
    fingerprints: list[str] = []
    for commit in (pin, latest):
        for path in source["files"]:
            raw = f"https://raw.githubusercontent.com/{owner_repo}/{commit}/{path}"
            data = _read_url(raw)
            fingerprints.append(f"{commit}:{path}:{_sha(data)}")
            if path.endswith(".py"):
                # Parse names only. Never import or execute third-party code.
                text = data.decode("utf-8", errors="replace")
                ids.update(_mcp_tools(text))
            elif path.endswith(".txt"):
                text = data.decode("utf-8", errors="replace")
                ids.update(f"api:{class_name}.{method}" for class_name, method in re.findall(r"\b([A-Z][A-Za-z0-9_]*)\.((?:Get|Set|Add|Delete|Create|Remove|Insert|Append|Move|Update|Analyze)[A-Z][A-Za-z0-9_]*)", text))
            elif "changelog" in path.lower():
                ids.update(_changelog_entries(data.decode("utf-8", errors="replace")))
    return _sha("\n".join(fingerprints).encode()), ids


def discover(sources: dict) -> dict:
    observations: dict[str, dict] = {}
    root = _script_root()
    if root:
        for name in ("README.md", "DaVinciResolveScript.pyi", "CHANGELOG.md"):
            path = root / name
            if not path.is_file():
                continue
            data = path.read_bytes()
            text = data.decode("utf-8", errors="replace")
            ids = _api_methods(text) if name.endswith(".pyi") else _changelog_entries(text) if "CHANGELOG" in name else set()
            observations[f"resolve:{name}"] = {"fingerprint": _sha(data), "capabilities": sorted(f"api:{v}" for v in ids), "version": _version(root)}
    if MCP_BUNDLE.is_file():
        tools = _official_mcp_tools()
        observations["blackmagic:installed-mcp"] = {
            "fingerprint": _sha(json.dumps(tools, sort_keys=True).encode()),
            "capabilities": sorted(f"mcp:{tool['name']}#{_sha(tool['description'].encode())[:12]}" for tool in tools),
        }
    for source in sources.get("third_party", []):
        fingerprint, capabilities = _repo_snapshot(source)
        observations[f"repo:{source['repo']}"] = {"fingerprint": fingerprint, "capabilities": sorted(capabilities), "pinned_commit": source["pinned_commit"]}
    ui = sources.get("qualified_ui_control", {})
    ui_path = ROOT / ui.get("path", "native/resolve-control")
    ui_ids = set(ui.get("qualified_capabilities", []))
    ui_files = sorted(ui_path.rglob("*.rs")) if ui_path.exists() else []
    ui_fingerprint = "\n".join(f"{path.relative_to(ui_path)}:{_sha(path.read_bytes())}" for path in ui_files)
    ui_revision = _sha(ui_fingerprint.encode())
    observations["ui:resolve-control"] = {
        "fingerprint": _sha(("\n".join(sorted(ui_ids)) + ui_fingerprint).encode()),
        "capabilities": sorted(ui_ids),
        "capability_fingerprints": {capability: ui_revision for capability in ui_ids},
        "qualification_evidence": ui.get("qualification_evidence", []),
    }
    return observations


def registry_capabilities() -> set[str]:
    return {f"axi:{op_id}" for op_id in edit_algebra.OP_STATUS}


def compare(observations: dict, baseline: dict) -> list[dict]:
    candidates: list[dict] = []
    for source, current in observations.items():
        previous = baseline.get(source, {})
        old = set(previous.get("capabilities", []))
        for capability in sorted(set(current.get("capabilities", [])) - old):
            candidates.append({"source": source, "upstream_id": capability, "change": "new", "status": "documented, not implemented", "stage": "evaluation", "qualification_status": "not qualified", "required_next_stage": "live qualification", "production_enabled": False})
        for capability, revision in current.get("capability_fingerprints", {}).items():
            previous_revision = previous.get("capability_fingerprints", {}).get(capability)
            if capability in old and previous_revision and revision != previous_revision:
                candidates.append({"source": source, "upstream_id": capability, "change": "changed", "revision": revision, "status": "documented, not implemented", "stage": "evaluation", "qualification_status": "not qualified", "required_next_stage": "live qualification", "production_enabled": False})
    return candidates


def scan(*, record: bool = False, sources_path: Path = SOURCES_PATH, ledger_path: Path = LEDGER_PATH) -> dict:
    sources = json.loads(sources_path.read_text(encoding="utf-8"))
    baseline = sources.get("baseline", {})
    current = discover(sources)
    initialized = bool(sources.get("baseline_initialized", False))
    candidates = compare(current, baseline) if initialized else []
    source_changes = []
    if initialized:
        for name, value in current.items():
            previous = baseline.get(name)
            if previous and (value["fingerprint"] != previous.get("fingerprint") or value.get("version") != previous.get("version")):
                source_changes.append({
                    "source": name,
                    "previous_fingerprint": previous.get("fingerprint"),
                    "current_fingerprint": value["fingerprint"],
                    "previous_version": previous.get("version"),
                    "current_version": value.get("version"),
                })
    registry = sorted(registry_capabilities())
    report = {
        "observations": current,
        "registry_comparison": {"axi_operation_ids": registry, "upstream_candidate_count": len(candidates)},
        "source_changes": source_changes,
        "candidates": candidates,
        "baseline_initialized": initialized,
        "recorded": bool(record),
    }
    if record and candidates:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {"schema_version": 1, "candidates": []}
        seen = {(row["source"], row["upstream_id"], row.get("revision")) for row in ledger["candidates"]}
        ledger["candidates"].extend(row for row in candidates if (row["source"], row["upstream_id"], row.get("revision")) not in seen)
        ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if record:
        sources["baseline"] = {
            **baseline,
            **{name: {key: value[key] for key in ("fingerprint", "capabilities", "capability_fingerprints", "version") if key in value} for name, value in current.items()},
        }
        sources["baseline_initialized"] = True
        sources_path.write_text(json.dumps(sources, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("scan", "record"), nargs="?", default="scan")
    args = parser.parse_args(argv)
    try:
        report = scan(record=args.command == "record")
    except (OSError, RuntimeError, ValueError, KeyError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        print(f"error: capability scan incomplete: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
