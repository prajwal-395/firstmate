"""Read Resolve's installed scripting stub as a versioned capability map.

The stub and changelog are authoritative for the installed release. The map
is built on demand and kept in memory, so no Resolve installation files or
project data are changed.
"""

from __future__ import annotations

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
    r"^\s+def ([A-Za-z_]\w*)\((.*)\)(?:\s*->\s*(.*))?:$")
_SECTION = re.compile(r"^##\s+([0-9]+(?:\.[0-9]+)*)\s*$")


class ResolveReferenceError(RuntimeError):
    """The installed Resolve API reference is missing or malformed."""


def _read_docstring(lines: list[str], index: int) -> str:
    if index + 1 >= len(lines):
        return ""
    match = re.match(r'^\s+"""(.*?)"""\s*$', lines[index + 1])
    return match.group(1).strip() if match else ""


def _methods(stub: str) -> dict[str, dict[str, dict[str, str]]]:
    classes: dict[str, dict[str, dict[str, str]]] = {}
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
            classes[current][name] = {
                "signature": f"({args})" + (f" -> {returns}" if returns else ""),
                "description": _read_docstring(lines, index),
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


def callable_method(target: Any, method_name: str):
    """Return an actually callable proxy method, tolerating proxy lookup errors.

    Resolve proxies can make `hasattr` misleading. Callers must still judge
    the result and read it back after a write.
    """
    try:
        method = getattr(target, method_name)
    except Exception:
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
