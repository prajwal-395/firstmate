#!/usr/bin/env python3
"""Capture paths and check install cleanup ownership without reading file data."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

LIVE_STATE_BASENAMES = {
    "resource-scheduler.sqlite3", "resource-scheduler.sqlite3-journal",
    "resource-scheduler.sqlite3-shm", "resource-scheduler.sqlite3-wal",
    "ren-resolved.sqlite3", "ren-resolved.sqlite3-journal",
    "ren-resolved.sqlite3-shm", "ren-resolved.sqlite3-wal",
}
LIVE_STATE_PREFIXES = (".local/share/vep/source_memory/", ".local/share/vep/logs/")


def _find(root: Path, *, owner_uid: int | None = None) -> tuple[list[str], list[str]]:
    """Use find's native traversal; it continues past privacy-protected folders."""
    # TCC-protected Trash can stall a recursive walk even when find eventually
    # reports EPERM. Prune it explicitly and surface it as skipped below.
    command = ["/usr/bin/find", str(root), "-mindepth", "1", "(",
               "-path", str(root / ".Trash"), "-o", "-path", str(root / ".Trashes"),
               ")", "-prune", "-o", "(", "-type", "f", "-o", "-type", "l", ")"]
    if owner_uid is not None:
        command.extend(["-uid", str(owner_uid)])
    command.append("-print0")
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    paths = [os.fsdecode(item) for item in result.stdout.split(b"\0") if item]
    errors = [line for line in os.fsdecode(result.stderr).splitlines() if line.strip()]
    if result.returncode not in (0, 1):
        raise RuntimeError(f"find exited {result.returncode}: {errors}")
    return paths, errors


def snapshot(home: str | Path, *, paths_only: bool = False,
             owner_uid: int | None = None,
             path_prefix: str | Path | None = None) -> dict:
    """Return relative file paths and, optionally, matching owner paths."""
    root = Path(home).resolve()
    scan_root = root if path_prefix is None else Path(path_prefix).resolve()
    if not scan_root.is_relative_to(root):
        raise ValueError(f"path prefix must be inside snapshot root: {scan_root}")
    paths, errors = _find(scan_root)
    relative = sorted(Path(path).relative_to(root).as_posix() for path in paths)
    owned: list[str] = []
    if owner_uid is not None:
        owned_paths, owner_errors = _find(root, owner_uid=owner_uid)
        owned = sorted(Path(path).relative_to(root).as_posix() for path in owned_paths)
        errors.extend(owner_errors)
    # Error text from find includes absolute paths. Store concise, stable entries.
    skipped_set = set()
    for line in errors:
        detail = line.removeprefix("find: ")
        path = detail.rsplit(": ", 1)[0] if ": " in detail else detail
        skipped_set.add(path.removeprefix(str(root) + "/") or path)
    skipped_set.update(name for name in (".Trash", ".Trashes") if (root / name).exists())
    skipped = sorted(skipped_set)
    return {"files": {path: -1 for path in relative}, "owned": owned,
            "owner_uid": owner_uid, "skipped": skipped}


def is_live_state(relative: str) -> bool:
    normalized = relative.replace(os.sep, "/")
    return normalized.startswith(LIVE_STATE_PREFIXES) or (
        normalized.startswith(".local/share/vep/")
        and Path(normalized).name in LIVE_STATE_BASENAMES
    )


def compare(before_vep: dict, after_home: dict,
            renqualify_uid: int) -> tuple[list[str], list[str], list[str], list[str]]:
    """Compare VEP paths and check ownership across the caller's home."""
    before_files, after_files = before_vep["files"], after_home["files"]
    prefix = ".local/share/vep/"
    before_paths = set(before_files)
    after_paths = {path[len(prefix):] for path in after_files if path.startswith(prefix)}
    added = sorted(prefix + path for path in after_paths - before_paths)
    removed = sorted(prefix + path for path in before_paths - after_paths)
    owned = [path for path in after_home.get("owned", [])]
    failures = sorted(path for path in added + removed if not is_live_state(path))
    failures.extend(owned)
    if after_home.get("owner_uid") != renqualify_uid:
        failures.append("ownership snapshot UID does not match qualification UID")
    skipped = sorted(set(after_home["skipped"]) | {
        prefix + path for path in before_vep["skipped"]
    })
    return added, removed, sorted(set(failures)), skipped


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or not isinstance(value.get("files"), dict)
            or not isinstance(value.get("skipped"), list)
            or not isinstance(value.get("owned", []), list)
            or (value.get("owner_uid") is not None
                and not isinstance(value.get("owner_uid"), int))
            or any(not isinstance(name, str) or not isinstance(uid, int)
                   for name, uid in value["files"].items())
            or any(not isinstance(item, str) for item in value["skipped"])
            or any(not isinstance(item, str) for item in value.get("owned", []))):
        raise ValueError(f"invalid preservation snapshot: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture")
    capture.add_argument("home", type=Path)
    capture.add_argument("output", type=Path)
    capture.add_argument("--paths-only", action="store_true", help="compatibility flag; snapshots never read file data")
    capture.add_argument("--owner-uid", type=int)
    capture.add_argument("--path-prefix", type=Path,
                         help="limit captured paths while still checking owner-uid across home")
    check = commands.add_parser("compare")
    check.add_argument("before", type=Path)
    check.add_argument("after", type=Path)
    check.add_argument("--renqualify-uid", required=True, type=int)
    args = parser.parse_args(argv)
    try:
        if args.command == "capture":
            args.output.write_text(json.dumps(snapshot(args.home, paths_only=args.paths_only,
                                                       owner_uid=args.owner_uid,
                                                       path_prefix=args.path_prefix),
                                                sort_keys=True), encoding="utf-8")
            return 0
        before, after = _read(args.before), _read(args.after)
        added, removed, failures, skipped = compare(before, after, args.renqualify_uid)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"preservation check failed: {exc}", file=sys.stderr)
        return 1
    print(f"Added paths ({len(added)}):")
    for path in added:
        print(f"  {path}{' [allowed live state]' if is_live_state(path) else ''}")
    print(f"Removed paths ({len(removed)}):")
    for path in removed:
        print(f"  {path}{' [allowed live state]' if is_live_state(path) else ''}")
    if skipped:
        print(f"Skipped unreadable or concurrently removed paths ({len(skipped)}):")
        for path in skipped:
            print(f"  {path}")
    owned = after.get("owned", [])
    if owned:
        print(f"Files still owned by renqualify ({len(owned)}):")
        for path in owned:
            print(f"  {path}")
    if failures:
        print(f"FAIL: {len(failures)} unexpected path or ownership change(s).")
        return 1
    print("PASS: no unexpected VEP path changes and no renqualify-owned files remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
