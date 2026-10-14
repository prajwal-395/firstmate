"""Portable, revisioned storage for the canonical Project Model.

The existing project stores remain read-only inputs to ``parity_report``.
The event stream is the authority for this model; its snapshot is a
deterministic, atomically replaced cache which can always be replayed.
"""
from __future__ import annotations

import fcntl
import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from library.tools.project_model import validate_project_model

STORE_DIR = "project_model"
EVENTS_FILE = "events.jsonl"
SNAPSHOT_FILE = "snapshot.json"
PARITY_FILE = "parity-report.json"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _paths(project: Path) -> tuple[Path, Path, Path]:
    root = Path(project) / STORE_DIR
    return root / EVENTS_FILE, root / SNAPSHOT_FILE, root / ".lock"


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous = "0" * 64
    for expected_revision, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid project model event at revision {expected_revision}: {exc}") from exc
        digest = event.pop("event_hash", None)
        if event.get("revision") != expected_revision or event.get("previous_hash") != previous:
            raise ValueError(f"broken project model event chain at revision {expected_revision}")
        actual = hashlib.sha256(_json_bytes(event)).hexdigest()
        if digest != actual:
            raise ValueError(f"project model event hash mismatch at revision {expected_revision}")
        event["event_hash"] = digest
        previous = digest
        events.append(event)
    return events


def _apply_patch(model: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = dict(model)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _apply_patch(result[key], value)
        else:
            result[key] = value
    return result


def _replay(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    model = None
    for event in events:
        if event["kind"] == "replace":
            model = event["payload"]
        elif event["kind"] == "merge_patch" and model is not None:
            model = _apply_patch(model, event["payload"])
        else:
            raise ValueError(f"invalid event kind or order at revision {event['revision']}")
        validate_project_model(model)
    return model


def _write_snapshot(path: Path, events: list[dict[str, Any]], model: dict[str, Any]) -> None:
    _atomic_write(path, _json_bytes({
        "revision": len(events),
        "head_hash": events[-1]["event_hash"],
        "model": model,
    }))


def load_project_model(project: Path) -> tuple[int, dict[str, Any]]:
    """Load and verify from the event log; repair a missing/stale snapshot."""
    events_path, snapshot_path, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        events = _read_events(events_path)
        model = _replay(events)
        if model is None:
            raise FileNotFoundError(f"no canonical Project Model in {events_path}")
        expected = {"revision": len(events), "head_hash": events[-1]["event_hash"], "model": model}
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            snapshot = None
        if snapshot != expected:
            _write_snapshot(snapshot_path, events, model)
        return len(events), model


def commit_project_model(project: Path, model: dict[str, Any], *, expected_revision: int,
                         author: str = "ren", reason: str = "") -> int:
    """Append a complete model revision using compare-and-swap semantics."""
    validate_project_model(model)
    events_path, snapshot_path, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        events = _read_events(events_path)
        revision = len(events)
        if revision != expected_revision:
            raise RuntimeError(f"stale project model revision {expected_revision}; current revision is {revision}")
        event = {
            "revision": revision + 1,
            "previous_hash": events[-1]["event_hash"] if events else "0" * 64,
            "kind": "replace",
            "author": author,
            "reason": reason,
            "schema_version": model["schema_version"],
            "payload": model,
        }
        event["event_hash"] = hashlib.sha256(_json_bytes(event)).hexdigest()
        events_path.parent.mkdir(parents=True, exist_ok=True)
        with events_path.open("ab") as stream:
            stream.write(_json_bytes(event))
            stream.flush()
            os.fsync(stream.fileno())
        events.append(event)
        _write_snapshot(snapshot_path, events, model)
        return revision + 1


def append_model_patch(project: Path, patch: dict[str, Any], *, expected_revision: int,
                       author: str = "ren", reason: str = "") -> int:
    """Append a deterministic JSON merge patch over the current model."""
    if not isinstance(patch, dict):
        raise TypeError("project model patch must be an object")
    events_path, snapshot_path, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        events = _read_events(events_path)
        revision = len(events)
        if revision != expected_revision:
            raise RuntimeError(f"stale project model revision {expected_revision}; current revision is {revision}")
        model = _replay(events)
        if model is None:
            raise FileNotFoundError(f"no canonical Project Model in {events_path}")
        updated = _apply_patch(model, patch)
        validate_project_model(updated)
        event = {"revision": revision + 1, "previous_hash": events[-1]["event_hash"],
                 "kind": "merge_patch", "author": author, "reason": reason,
                 "schema_version": updated["schema_version"], "payload": patch}
        event["event_hash"] = hashlib.sha256(_json_bytes(event)).hexdigest()
        with events_path.open("ab") as stream:
            stream.write(_json_bytes(event))
            stream.flush()
            os.fsync(stream.fileno())
        events.append(event)
        _write_snapshot(snapshot_path, events, updated)
        return revision + 1


def merge_evidence_records(project: Path, *, observations: list[dict[str, Any]],
                           events_to_merge: list[dict[str, Any]] | None = None,
                           provenance: list[dict[str, Any]] | None = None,
                           expected_revision: int,
                           author: str = "ren",
                           reason: str = "incremental evidence acquisition") -> int:
    """Idempotently merge evidence rows by stable ID into the current model."""
    events_path, snapshot_path, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current_events = _read_events(events_path)
        revision = len(current_events)
        if revision != expected_revision:
            raise RuntimeError(f"stale project model revision {expected_revision}; current revision is {revision}")
        model = _replay(current_events)
        if model is None:
            raise FileNotFoundError(f"no canonical Project Model in {events_path}")
        changed = False
        for key, rows in (("observations", observations),
                          ("events", events_to_merge or []),
                          ("provenance", provenance or [])):
            by_id = {row["id"]: row for row in model.get(key, [])}
            for row in rows:
                old = by_id.get(row["id"])
                if old != row:
                    by_id[row["id"]] = row
                    changed = True
            model[key] = list(by_id.values())
        if not changed:
            return revision
        validate_project_model(model)
        patch = {key: model[key] for key in
                 ("observations", "events", "provenance")}
        event = {"revision": revision + 1,
                 "previous_hash": current_events[-1]["event_hash"],
                 "kind": "merge_patch", "author": author,
                 "reason": reason, "schema_version": model["schema_version"],
                 "payload": patch}
        event["event_hash"] = hashlib.sha256(_json_bytes(event)).hexdigest()
        with events_path.open("ab") as stream:
            stream.write(_json_bytes(event))
            stream.flush()
            os.fsync(stream.fileno())
        current_events.append(event)
        _write_snapshot(snapshot_path, current_events, model)
        return revision + 1


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def parity_report(project: Path, model: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read-only inventory of legacy projections and their observable drift.

    This first bridge deliberately reports store-level parity evidence rather
    than guessing identity migrations that belong to Units 3 and 4.
    """
    project = Path(project)
    if model is None:
        _, model = load_project_model(project)
    artifacts = {
        "source_memory": project / "source_memory",
        "pipeline_data": project / "pipeline_data.json",
        "edit_ledger": project / "external" / "declarations" / "edit_ledger.json",
        "project_graph": project / "project_graph.json",
        "timeline_shadow": project / "pipeline_output" / "review" / "timeline_shadow.json",
        "graphics_ir": project / "graphics_ir",
    }
    adapters = {}
    for name, path in artifacts.items():
        exists = path.exists()
        details: dict[str, Any] = {"path": str(path.relative_to(project)), "present": exists}
        if path.is_dir():
            details["entries"] = len(list(path.iterdir()))
        elif exists:
            payload = _read_json(path)
            details["readable"] = payload is not None
            if isinstance(payload, list):
                details["rows"] = len(payload)
            elif isinstance(payload, dict):
                details["top_level_keys"] = sorted(payload.keys())
        adapters[name] = details
    snapshot = _read_json(project / STORE_DIR / SNAPSHOT_FILE) or {}
    mismatches = [{"store": name, "kind": "unreadable", "path": row["path"]}
                  for name, row in adapters.items() if row["present"] and row.get("readable") is False]

    # The catalog is the one legacy store with a direct, stable comparison:
    # model asset URIs should cover the catalog's recorded media paths.
    pipeline = _read_json(artifacts["pipeline_data"])
    if isinstance(pipeline, dict):
        from library.tools import capability_outputs
        catalog = capability_outputs.value(pipeline, "footage.catalog", "clip_catalog") or []
        model_uris = {asset["uri"] for asset in model["assets"]}
        catalog_uris = {str(row[key]) for row in catalog if isinstance(row, dict)
                        for key in ("file_path", "path", "source_path") if row.get(key)}
        missing = sorted(catalog_uris - model_uris)
        if missing:
            mismatches.append({"store": "pipeline_data", "kind": "catalog_assets_missing_from_model",
                               "values": missing})
        adapters["pipeline_data"]["catalog_rows"] = len(catalog)
        adapters["pipeline_data"]["model_asset_uris"] = len(model_uris)
    return {"schema_version": 1, "project_id": model["id"], "model_schema_version": model["schema_version"],
            "revision": {"number": snapshot.get("revision", 0), "head_hash": snapshot.get("head_hash")},
            "adapters": adapters, "mismatches": mismatches}


def write_parity_report(project: Path, model: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write the report outside legacy stores; those stores are never changed."""
    report = parity_report(project, model)
    _atomic_write(Path(project) / STORE_DIR / PARITY_FILE, _json_bytes(report))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read or update Ren's canonical Project Model store")
    subparsers = parser.add_subparsers(dest="command", required=True)
    init = subparsers.add_parser("init", help="create the first revision from a model JSON file")
    init.add_argument("project", type=Path)
    init.add_argument("model", type=Path)
    read = subparsers.add_parser("read", help="print the verified current snapshot")
    read.add_argument("project", type=Path)
    parity = subparsers.add_parser("parity", help="write a read-only legacy-store parity report")
    parity.add_argument("project", type=Path)
    args = parser.parse_args(argv)
    if args.command == "init":
        model = json.loads(args.model.read_text(encoding="utf-8"))
        revision = commit_project_model(args.project, model, expected_revision=0,
                                        author="ren", reason="initial import")
        print(json.dumps({"revision": revision, "store": str(args.project / STORE_DIR)}))
    elif args.command == "read":
        revision, model = load_project_model(args.project)
        print(json.dumps({"revision": revision, "model": model}, indent=2, ensure_ascii=False))
    else:
        report = write_parity_report(args.project)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
