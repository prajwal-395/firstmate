"""Read Resolve's installed scripting stub as a versioned capability map.

The stub and changelog are authoritative for the installed release. The map
is built on demand and kept in memory, so no Resolve installation files or
project data are changed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

DEFAULT_REFERENCE_DIR = Path(
    "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
    "Developer/Scripting"
)
_CLASS = re.compile(r"^class ([A-Za-z_]\w*)\s*(?:\(|:)")
_METHOD = re.compile(
    r"^\s+def ([A-Za-z_]\w*)\((.*)\)(?:\s*->\s*(.*))?:\s*(?:\.\.\.)?\s*$")
_SECTION = re.compile(r"^##\s+([0-9]+(?:\.[0-9]+)*)\s*$")
_CLASS_CONTEXT = {
    "Resolve": ("application", "Resolve application connection required"),
    "ProjectManager": ("project_manager", "connected Resolve instance required"),
    "Project": ("project", "exact open project required for project mutations"),
    "MediaPool": ("media_pool", "exact open project and media pool required"),
    "MediaPoolItem": ("media_pool", "item must belong to the open project's pool"),
    "Timeline": ("timeline", "exact open project and selected timeline required"),
    "TimelineItem": ("timeline", "item must resolve on the exact timeline"),
    "ColorGroup": ("color", "color group must belong to the open project"),
    "Folder": ("media_pool", "folder must belong to the open project's pool"),
    "Gallery": ("color", "gallery must belong to the open project"),
    "GalleryStillAlbum": ("color", "album must belong to the open gallery"),
    "Graph": ("fusion", "graph must belong to the target Fusion composition"),
    "MediaStorage": ("media_storage", "Resolve media storage required"),
}


class ResolveReferenceError(RuntimeError):
    """The installed Resolve API reference is missing or malformed."""


def _read_docstring(lines: list[str], index: int) -> str:
    if index + 1 >= len(lines):
        return ""
    match = re.match(r'^\s+"""(.*?)"""\s*$', lines[index + 1])
    return match.group(1).strip() if match else ""


def _literal_types(stub: str) -> dict[str, list[Any]]:
    """Extract Literal aliases so enum-like API inputs can be validated."""
    try:
        tree = ast.parse(stub)
    except SyntaxError:
        # The method-line parser below tolerates small hand-written stubs;
        # they may not form a complete module when a test ends in `def ...:`.
        return {}
    result = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        value = node.value
        if (isinstance(target, ast.Name) and isinstance(value, ast.Subscript)
                and ast.unparse(value.value).split(".")[-1] == "Literal"):
            values = value.slice.elts if isinstance(value.slice, ast.Tuple) else [value.slice]
            result[target.id] = [ast.literal_eval(item) for item in values]
    return result


def _typed_dict_types(stub: str) -> dict[str, dict[str, Any]]:
    """Extract documented TypedDict field shapes for nested argument checks."""
    try:
        tree = ast.parse(stub)
    except SyntaxError:
        return {}
    result = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(ast.unparse(base).split(".")[-1] == "TypedDict"
                   for base in node.bases):
            continue
        total = next((keyword.value.value for keyword in node.keywords
                      if keyword.arg == "total"
                      and isinstance(keyword.value, ast.Constant)), True)
        fields = {}
        for item in node.body:
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                continue
            field_type = ast.unparse(item.annotation)
            required = bool(total)
            if field_type.startswith("NotRequired["):
                field_type = field_type[len("NotRequired["):-1]
                required = False
            elif field_type.startswith("Required["):
                field_type = field_type[len("Required["):-1]
                required = True
            fields[item.target.id] = {"type": field_type, "required": required}
        result[node.name] = {"total": bool(total), "fields": fields}
    return result


def _methods(stub: str) -> dict[str, dict[str, dict[str, str]]]:
    classes: dict[str, dict[str, dict[str, str]]] = {}
    literal_types = _literal_types(stub)
    current: str | None = None
    lines = stub.splitlines()
    for index, line in enumerate(lines):
        class_match = _CLASS.match(line)
        if class_match:
            current = class_match.group(1)
            classes.setdefault(current, {})
            continue
        if current is None:
            continue
        method_match = _METHOD.match(line)
        if method_match:
            name, args, returns = method_match.groups()
            if name.startswith("_"):
                continue
            signature = f"({args})" + (f" -> {returns}" if returns else "")
            try:
                parsed = ast.parse(f"def _method{signature}: ...").body[0]
                parameters = []
                positional = parsed.args.posonlyargs + parsed.args.args
                defaults_start = len(positional) - len(parsed.args.defaults)
                for index, arg in enumerate(positional):
                    if arg.arg == "self":
                        continue
                    parameter = {
                        "name": arg.arg,
                        "type": ast.unparse(arg.annotation) if arg.annotation else "Any",
                        "required": index < defaults_start,
                    }
                    if parameter["type"] in literal_types:
                        parameter["allowed_values"] = literal_types[parameter["type"]]
                    parameters.append(parameter)
                for index, arg in enumerate(parsed.args.kwonlyargs):
                    if arg.arg == "self":
                        continue
                    parameter = {
                        "name": arg.arg,
                        "type": ast.unparse(arg.annotation) if arg.annotation else "Any",
                        "required": parsed.args.kw_defaults[index] is None,
                    }
                    if parameter["type"] in literal_types:
                        parameter["allowed_values"] = literal_types[parameter["type"]]
                    parameters.append(parameter)
                if parsed.args.vararg:
                    parameters.append({
                        "name": parsed.args.vararg.arg,
                        "type": ast.unparse(parsed.args.vararg.annotation)
                        if parsed.args.vararg.annotation else "Any",
                        "required": False, "kind": "var_positional",
                    })
                if parsed.args.kwarg:
                    parameters.append({
                        "name": parsed.args.kwarg.arg,
                        "type": ast.unparse(parsed.args.kwarg.annotation)
                        if parsed.args.kwarg.annotation else "Any",
                        "required": False, "kind": "var_keyword",
                    })
            except (SyntaxError, ValueError):
                parameters = []
            classes[current][name] = {
                "signature": signature,
                "parameters": parameters,
                "return_type": returns or "Any",
                "description": _read_docstring(lines, index),
                "documented": True,
                "observed": None,
                "page_context": _CLASS_CONTEXT.get(current, ("unknown", "inspect object context"))[0],
                "context_requirement": _CLASS_CONTEXT.get(current, ("unknown", "inspect object context"))[1],
            }
    return {name: methods for name, methods in classes.items() if methods}


def _changelog(changelog: str, version: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    section: str | None = None
    for line in changelog.splitlines():
        match = _SECTION.match(line)
        if match:
            section = match.group(1)
            sections.setdefault(section, [])
        elif section is not None and line.startswith("- "):
            sections[section].append(line[2:].strip())
    # Use the exact installed API version as the map key, and select the
    # matching changelog release (21.1.0 -> 21.1).
    match = re.search(r"\d+\.\d+", version)
    if not match:
        raise ResolveReferenceError(
            f"Resolve version {version!r} does not identify a release section")
    release = match.group(0)
    if release not in sections:
        raise ResolveReferenceError(
            f"Resolve API changelog has no section for {release} (installed {version})")
    return {release: sections[release]}


def build_capability_map(version: str, reference_dir: Path | str =
                         DEFAULT_REFERENCE_DIR) -> dict[str, Any]:
    """Parse all public classes/methods and release notes for `version`."""
    root = Path(reference_dir)
    stub_path = root / "DaVinciResolveScript.pyi"
    changelog_path = root / "CHANGELOG.md"
    try:
        stub = stub_path.read_text(encoding="utf-8")
        changelog = changelog_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResolveReferenceError(
            f"cannot read Resolve scripting reference under {root}: {exc}") from exc
    classes = _methods(stub)
    if not classes:
        raise ResolveReferenceError(f"no public API classes found in {stub_path}")
    version = str(version)
    for class_name, methods in classes.items():
        for method in methods.values():
            method["page_context"], method["context_requirement"] = _CLASS_CONTEXT.get(
                class_name, ("unknown", "inspect object context"))
    return {
        "schema_version": 1,
        "resolve_version": version,
        "versions": {
            version: {
                "api_reference": str(stub_path),
                "api_reference_sha256": hashlib.sha256(
                    stub.encode("utf-8")).hexdigest(),
                "class_count": len(classes),
                "method_count": sum(
                    len(methods) for methods in classes.values()),
                "classes": classes,
                "literal_types": _literal_types(stub),
                "typed_dict_types": _typed_dict_types(stub),
                "changelog": _changelog(changelog, version),
            }
        },
    }


def capability_map_for_version(capability_map: dict[str, Any],
                               version: str | None = None) -> dict[str, Any]:
    """Get the API entry keyed by Resolve's exact installed version."""
    version = str(version or capability_map["resolve_version"])
    return capability_map["versions"][version]


def has_documented_method(capability_map: dict[str, Any], class_name: str,
                          method_name: str) -> bool:
    """Whether the installed release's shipped stub documents this method."""
    release = capability_map_for_version(capability_map)
    return method_name in release["classes"].get(class_name, {})


def observe_methods(capability_map: dict[str, Any], handles: dict[str, Any]) -> dict[str, list[str]]:
    """Record which documented methods are callable on supplied live proxies.

    `handles` is explicit because Resolve exposes different objects only in
    their project/page context. This probes availability, not behavior or
    write safety; callers still verify every operation's result.
    """
    release = capability_map_for_version(capability_map)
    observed: dict[str, list[str]] = {}
    for class_name, methods in release["classes"].items():
        handle = handles.get(class_name)
        names = []
        for name, entry in methods.items():
            available = bool(handle is not None and callable_method(handle, name))
            entry["observed"] = available if handle is not None else None
            if available:
                names.append(name)
        if names:
            observed[class_name] = names
    return observed


def method_capability_records(capability_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Emit normalized native-method facts for AXI capability-registry joins.

    Method-level production enablement is always false here: only an
    editorial operation/backend pair with its own qualification can be
    production-enabled by the registry authority.
    """
    release = capability_map_for_version(capability_map)
    records = []
    for class_name, methods in release["classes"].items():
        for method_name, metadata in methods.items():
            records.append({
                "id": f"native_api.{class_name}.{method_name}",
                "backend": "native_scripting",
                "resolve_version": capability_map["resolve_version"],
                "documented": metadata["documented"],
                "observed": metadata["observed"],
                "implemented": True,
                "tested": False,
                "live_qualified": False,
                "production_enabled": False,
                "signature": metadata["signature"],
                "parameters": metadata["parameters"],
                "return_type": metadata["return_type"],
                "page_context": metadata["page_context"],
                "context_requirement": metadata["context_requirement"],
                "api_reference_sha256": release["api_reference_sha256"],
            })
    return records


def callable_method(target: Any, method_name: str):
    """Return an actually callable proxy method, tolerating proxy lookup errors.

    Resolve proxies can make `hasattr` misleading. Callers must still judge
    the result and read it back after a write.
    """
    try:
        method = getattr(target, method_name)
    except Exception:  # noqa: BLE001 - Resolve proxy lookup errors vary by release
        return None
    return method if callable(method) else None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Emit the installed DaVinci Resolve API capability map")
    parser.add_argument("--version", required=True,
                        help="exact version returned by Resolve.GetVersionString")
    parser.add_argument("--reference-dir", type=Path,
                        default=DEFAULT_REFERENCE_DIR)
    args = parser.parse_args()
    print(json.dumps(build_capability_map(args.version, args.reference_dir),
                     indent=2, sort_keys=True))
