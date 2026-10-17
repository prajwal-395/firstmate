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
PIPELINE_PROJECTION_KEY = "project_model_projection"
EVIDENCE_COLLECTIONS = (
    "assets", "streams", "entities", "observations", "identity_links",
    "masks", "object_classes", "tracks", "relations", "signals", "events",
    "interpretations", "coordinate_spaces", "time_transforms", "space_transforms",
    "query_results", "evaluation_policies", "provenance",
)


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
    # One validation for the replayed model, not one per event: every
    # event the log holds was schema-checked when it was committed, and
    # the hash chain `_read_events` verifies above already guards order
    # and tampering per event. Validating a 350 MB model with 116k
    # observations costs seconds per pass (measured 7.6 s on geo-podcast),
    # so per-event validation turns every load into events x seconds of
    # pure Ren CPU - the 12-minute build stall of 2026-10-17, caught on
    # the `sorted(validator.iter_errors(...))` frame inside
    # `protocol_conformance.validate`.
    if model is not None:
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


def pipeline_projection(project: Path) -> dict[str, Any] | None:
    """Return the small revision pointer projected into pipeline_data."""
    events_path, _, lock_path = _paths(Path(project))
    if not events_path.is_file():
        return None
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        events = _read_events(events_path)
        if not events:
            return None
        return {"revision": len(events), "head_hash": events[-1]["event_hash"]}


def project_pipeline_state(project: Path, state: dict[str, Any]) -> bool:
    """Refresh pipeline_data's revision pointer; the event log stays canonical."""
    projection = pipeline_projection(project)
    if projection is None:
        state.pop(PIPELINE_PROJECTION_KEY, None)
        return False
    state[PIPELINE_PROJECTION_KEY] = projection
    return True


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


def merge_evidence_model(project: Path, evidence_model: dict[str, Any], *,
                         expected_revision: int, author: str,
                         reason: str = "capability evidence update") -> int:
    """CAS-merge evidence collections, preserving all other model state."""
    validate_project_model(evidence_model)
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
        if evidence_model["id"] != model["id"]:
            raise ValueError("evidence update belongs to a different Project Model")
        if evidence_model["schema_version"] != model["schema_version"]:
            raise ValueError("evidence update uses a different Project Model schema version")
        patch: dict[str, list[dict[str, Any]]] = {}
        for key in EVIDENCE_COLLECTIONS:
            incoming = evidence_model.get(key, [])
            if not incoming:
                continue
            if key == "query_results":
                merged_rows = list(model.get(key, []))
                for row in incoming:
                    if row not in merged_rows:
                        merged_rows.append(row)
                if merged_rows != model.get(key, []):
                    model[key] = merged_rows
                    patch[key] = merged_rows
                continue
            by_id = {row["id"]: row for row in model.get(key, [])}
            for row in incoming:
                old = by_id.get(row["id"], {})
                merged = {**old, **row}
                if isinstance(old.get("metadata"), dict) and isinstance(row.get("metadata"), dict):
                    merged["metadata"] = {**old["metadata"], **row["metadata"]}
                by_id[row["id"]] = merged
            merged_rows = list(by_id.values())
            if merged_rows != model.get(key, []):
                model[key] = merged_rows
                patch[key] = merged_rows
        if not patch:
            return revision
        validate_project_model(model)
        event = {"revision": revision + 1,
                 "previous_hash": events[-1]["event_hash"],
                 "kind": "merge_patch", "author": author, "reason": reason,
                 "schema_version": model["schema_version"], "payload": patch}
        event["event_hash"] = hashlib.sha256(_json_bytes(event)).hexdigest()
        with events_path.open("ab") as stream:
            stream.write(_json_bytes(event))
            stream.flush()
            os.fsync(stream.fileno())
        events.append(event)
        _write_snapshot(snapshot_path, events, model)
        return revision + 1


def commit_evidence_import(project: Path, evidence_model: dict[str, Any], *,
                           expected_revision: int, author: str,
                           reason: str = "capability evidence import") -> int:
    """Create an initial model or CAS-merge an evidence-only update."""
    try:
        load_project_model(Path(project))
    except FileNotFoundError:
        return commit_project_model(Path(project), evidence_model,
                                    expected_revision=expected_revision,
                                    author=author, reason=reason)
    return merge_evidence_model(Path(project), evidence_model,
                                expected_revision=expected_revision,
                                author=author, reason=reason)


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
    events_path, _, lock_path = _paths(project)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        events = _read_events(events_path)
        canonical = _replay(events)
        current_projection = ({"revision": len(events),
                               "head_hash": events[-1]["event_hash"]}
                              if events else None)
        snapshot = _read_json(project / STORE_DIR / SNAPSHOT_FILE) or {}
    if canonical is None:
        raise FileNotFoundError(f"no canonical Project Model in {events_path}")
    if model is not None and model != canonical:
        raise RuntimeError("stale Project Model supplied to parity report")
    model = canonical
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
    revision = current_projection["revision"] if current_projection else 0
    head_hash = current_projection["head_hash"] if current_projection else None
    mismatches = [{"store": name, "kind": "unreadable", "path": row["path"]}
                  for name, row in adapters.items() if row["present"] and row.get("readable") is False]
    if snapshot.get("revision") != revision or snapshot.get("head_hash") != head_hash:
        mismatches.append({"store": "snapshot", "kind": "stale_revision",
                           "expected_revision": revision, "actual_revision": snapshot.get("revision"),
                           "action": ("run `bin/vep -m library.tools.project_model_store read "
                                      f"{project}` to replay and repair the snapshot")})

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
        projected = pipeline.get(PIPELINE_PROJECTION_KEY)
        adapters["pipeline_data"]["project_model_projection"] = projected
        if projected != (current_projection or {"revision": 0, "head_hash": None}):
            mismatches.append({"store": "pipeline_data", "kind": "project_model_projection_drift",
                               "expected_revision": revision,
                               "actual_revision": projected.get("revision") if isinstance(projected, dict) else None,
                               "action": "save pipeline state to refresh its Project Model revision pointer"})
    return {"schema_version": 1, "project_id": model["id"], "model_schema_version": model["schema_version"],
            "revision": {"number": revision, "head_hash": head_hash},
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
