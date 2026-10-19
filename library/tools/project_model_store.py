"""Portable, revisioned storage for the canonical Project Model.

The existing project stores remain read-only inputs to ``parity_report``.
The event stream is the authority for this model; its snapshot is a
deterministic, atomically replaced cache which can always be replayed.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from library.tools.project_model import validate_project_model

# Validation verdicts are keyed by the verified chain head. Loads with
# changed file signatures still verify every event hash before using one.
_validated_heads: OrderedDict[tuple, None] = OrderedDict()
_validation_lock = threading.Lock()
_VALIDATED_HEAD_LIMIT = 64
_verified_revisions: OrderedDict[tuple, int] = OrderedDict()


def _file_signature(path: Path) -> tuple | None:
    try:
        stat = path.stat()
        return (stat.st_dev, stat.st_ino, stat.st_size,
                stat.st_mtime_ns, stat.st_ctime_ns)
    except OSError:
        return None

STORE_DIR = "project_model"
EVENTS_FILE = "events.jsonl"
SNAPSHOT_FILE = "snapshot.json"
CHECKPOINT_FILE = "checkpoint.json"
CHECKPOINT_INTERVAL = 32
PARITY_FILE = "parity-report.json"
PIPELINE_PROJECTION_KEY = "project_model_projection"
EVIDENCE_COLLECTIONS = (
    "assets", "streams", "entities", "observations", "identity_links",
    "masks", "object_classes", "tracks", "relations", "signals", "events",
    "interpretations", "coordinate_spaces", "time_transforms", "space_transforms",
    "query_results", "evaluation_policies", "provenance",
)

_COLLECTION_KEYS = frozenset(EVIDENCE_COLLECTIONS) | frozenset({
    "clocks", "clip_uses", "edits", "effects", "frame_maps", "intents",
    "nle_bindings", "receipts", "renders", "timeline_states",
})


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


def _read_events_tail(path: Path) -> dict[str, Any] | None:
    """The last event line without reading the whole history.

    Replace events carry a 350 MB model per line; a 50-revision history
    is 17 GB to parse fully but 350 MB for the tail. The tail's revision
    and head hash are enough to check snapshot freshness; a mismatch
    falls back to the full verified read.
    """
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            if size == 0:
                return None
            # Scan back for the last newline (the file ends with one).
            # Chunks nearer the end accumulate in `later` and join
            # once: prepending to a bytes buffer here was O(n^2) on
            # 340 MB replace lines and hung the live build past
            # 10 minutes.
            step = 1024 * 1024
            later: list[bytes] = []
            pos = size
            # Skip a single trailing newline, then find the previous one.
            if size >= 1:
                stream.seek(size - 1)
                if stream.read(1) == b"\n":
                    pos = size - 1
            while pos > 0:
                chunk_size = min(step, pos)
                pos -= chunk_size
                stream.seek(pos)
                chunk = stream.read(chunk_size)
                newline = chunk.rfind(b"\n")
                if newline != -1:
                    # `later` holds end-nearer chunks in read order;
                    # file order is its reverse.
                    line = b"".join(
                        [chunk[newline + 1:],
                         *reversed(later)]).strip()
                    if not line:
                        return None
                    return json.loads(line.decode("utf-8"))
                later.append(chunk)
                if pos == 0:
                    # No newline seen: the whole file is one line (a
                    # single-event log). File order is the reverse
                    # of read order.
                    line = b"".join(reversed(later)).strip()
                    if not line:
                        return None
                    return json.loads(line.decode("utf-8"))
    except (OSError, ValueError):
        return None


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


def _read_events_after(path: Path, *, revision: int, head_hash: str,
                      offset: int) -> list[dict[str, Any]]:
    """Read and verify only the event suffix anchored by a trusted checkpoint."""
    result = []
    previous = head_hash
    with path.open("rb") as stream:
        stream.seek(offset)
        for expected_revision, raw in enumerate(stream, revision + 1):
            if not raw.strip():
                continue
            event = json.loads(raw.decode("utf-8"))
            digest = event.pop("event_hash", None)
            if event.get("revision") != expected_revision or event.get("previous_hash") != previous:
                raise ValueError(f"broken project model event chain at revision {expected_revision}")
            if digest != hashlib.sha256(_json_bytes(event)).hexdigest():
                raise ValueError(f"project model event hash mismatch at revision {expected_revision}")
            event["event_hash"] = digest
            previous = digest
            result.append(event)
    return result


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


def _replay(events: list[dict[str, Any]], *, verified: bool = False) -> dict[str, Any] | None:
    model = None
    for event in events:
        if event["kind"] == "replace":
            model = event["payload"]
        elif event["kind"] == "merge_patch" and model is not None:
            model = _apply_patch(model, event["payload"])
        elif event["kind"] == "record_changes" and model is not None:
            model = _apply_record_changes(model, event["payload"])
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
        head = ((events[-1].get("event_hash"), validate_project_model)
                if verified else None)
        with _validation_lock:
            known = head is not None and head in _validated_heads
            if known:
                _validated_heads.move_to_end(head)
        if not known:
            validate_project_model(model)
            if head is not None:
                with _validation_lock:
                    _validated_heads[head] = None
                    if len(_validated_heads) > _VALIDATED_HEAD_LIMIT:
                        _validated_heads.popitem(last=False)
    return model


def _replay_suffix(model: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in events:
        if event["kind"] == "replace":
            model = event["payload"]
        elif event["kind"] == "merge_patch":
            model = _apply_patch(model, event["payload"])
        elif event["kind"] == "record_changes":
            model = _apply_record_changes(model, event["payload"])
        else:
            raise ValueError(f"invalid event kind at revision {event['revision']}")
    validate_project_model(model)
    return model


def _apply_record_changes(model: dict[str, Any], changes: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply stable-ID collection edits while preserving canonical row order."""
    result = dict(model)
    for change in changes:
        collection = change["collection"]
        operation = change["operation"]
        record_id = change["id"]
        rows = {row["id"]: row for row in result.get(collection, [])}
        if operation == "delete":
            if record_id not in rows:
                raise ValueError(f"record {record_id!r} does not exist in {collection}")
            del rows[record_id]
        elif operation in ("append", "update"):
            record = change["record"]
            if operation == "append" and record_id in rows:
                raise ValueError(f"record {record_id!r} already exists in {collection}")
            if operation == "update" and record_id not in rows:
                raise ValueError(f"record {record_id!r} does not exist in {collection}")
            if record["id"] != record_id:
                raise ValueError("record change id must match record id")
            rows[record_id] = record
        else:
            raise ValueError(f"unknown record operation {operation!r}")
        result[collection] = [rows[key] for key in sorted(rows)]
    return result


def _event_scopes(event: dict[str, Any]) -> set[tuple[str, str | None]]:
    """Return conservative write scopes; legacy collection patches overlap all rows."""
    if event["kind"] == "record_changes":
        return {(change["collection"], change["id"])
                for change in event["payload"]}
    if event["kind"] == "merge_patch":
        return {(key, None) for key in event["payload"]}
    if event["kind"] == "replace":
        return {("*", None)}
    return {("*", None)}


def _scopes_overlap(left: set[tuple[str, str | None]],
                    right: set[tuple[str, str | None]]) -> bool:
    return any(a_collection == "*" or b_collection == "*"
               or (a_collection == b_collection
                   and (a_id is None or b_id is None or a_id == b_id))
               for a_collection, a_id in left
               for b_collection, b_id in right)


#: Collections small enough to ride a sidecar: the routing cascade
#: resolves a note's anchor against frame maps and clocks only, and the
#: reel writer only ever replaces these five. Everything evidence-sized
#: stays in the snapshot and the event log.
REEL_SCOPED_COLLECTIONS = (
    "clocks", "clip_uses", "edits", "effects", "frame_maps", "masks",
)

VALIDATED_MODELS_FILE = "validated-models.json"
"""Persistent set of validated model hashes, newest last, capped.

The in-process `_validated_heads` cache dies with every build process,
so each of the 4-5 model loads per end-to-end build re-validated the
same unchanged head (8.5 s a pass, 36 s in input routing alone). A model
hash recorded here was validated at commit time in a previous process;
hash equality is byte-identity with validated bytes, so the fast path
skips validation without weakening it. Tampered bytes hash differently
and fall through to the full verified read, which refuses.
"""
_VALIDATED_MODELS_LIMIT = 256


def _read_validated_models(store_dir: Path) -> list[str]:
    try:
        items = json.loads(
            (store_dir / VALIDATED_MODELS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [h for h in items if isinstance(h, str)][: _VALIDATED_MODELS_LIMIT]


def _record_validated_model(store_dir: Path, model_hash: str) -> None:
    try:
        known = _read_validated_models(store_dir)
        known = [h for h in known if h != model_hash] + [model_hash]
        store_dir.mkdir(parents=True, exist_ok=True)
        (store_dir / VALIDATED_MODELS_FILE).write_text(
            json.dumps(known[-_VALIDATED_MODELS_LIMIT:]), encoding="utf-8")
    except OSError:
        pass

REEL_PROJECTION_FILE = "reel-projection.json"
"""Tiny sidecar beside the snapshot: reel-scoped rows plus revision/head.

Marker routing resolves anchors against frame maps and clocks; loading
the 350 MB snapshot to reach a few dozen rows cost 36 s per build. The
sidecar is written on every commit from the just-validated model and
read back only when its revision and head still match the log tail -
anything else falls back to the full verified load.
"""


def _reel_projection(model: dict[str, Any]) -> dict[str, Any]:
    return {key: model.get(key) or []
            for key in REEL_SCOPED_COLLECTIONS}


def _write_snapshot(path: Path, events: list[dict[str, Any]], model: dict[str, Any],
                    *, revision: int | None = None) -> None:
    # Every caller holds the store lock and passes the model it just
    # validated, so recording its hash extends that verdict to future
    # processes: see VALIDATED_MODELS_FILE.
    revision = len(events) if revision is None else revision
    model_hash = hashlib.sha256(_json_bytes(model)).hexdigest()
    _atomic_write(path, _json_bytes({
        "revision": revision,
        "head_hash": events[-1]["event_hash"],
        "model": model,
        "model_hash": model_hash,
    }))
    _record_validated_model(path.parent, model_hash)
    _write_reel_projection(path, revision, events[-1]["event_hash"], model)
    _write_checkpoint(path, events[-1], model,
                      force=not path.with_name(CHECKPOINT_FILE).exists())
    # Every caller has just validated this exact model while holding
    # the store lock. Preserve that verdict for the head we wrote too,
    # not only heads subsequently loaded from disk.
    events_path = path.with_name(EVENTS_FILE)
    key = (events_path.resolve(), _file_signature(events_path),
           validate_project_model)
    with _validation_lock:
        head = (events[-1]["event_hash"], validate_project_model)
        _validated_heads[head] = None
        _validated_heads.move_to_end(head)
        _verified_revisions[key] = revision
        _verified_revisions.move_to_end(key)
        for cache in (_validated_heads, _verified_revisions):
            if len(cache) > _VALIDATED_HEAD_LIMIT:
                cache.popitem(last=False)


def _write_reel_projection(snapshot_path: Path, revision: int, head_hash: str,
                           model: dict[str, Any]) -> None:
    _atomic_write(snapshot_path.with_name(REEL_PROJECTION_FILE), _json_bytes({
        "revision": revision, "head_hash": head_hash,
        "collections": _reel_projection(model),
    }))


def _write_checkpoint(snapshot_path: Path, head: dict[str, Any],
                      model: dict[str, Any], *, force: bool = False) -> None:
    revision = head["revision"]
    checkpoint_path = snapshot_path.with_name(CHECKPOINT_FILE)
    if revision % CHECKPOINT_INTERVAL and not force and checkpoint_path.exists():
        return
    events_path = snapshot_path.with_name(EVENTS_FILE)
    payload = {"revision": revision, "head_hash": head["event_hash"],
               "event_offset": events_path.stat().st_size,
               "model": model,
               "model_hash": hashlib.sha256(_json_bytes(model)).hexdigest()}
    payload["checkpoint_hash"] = hashlib.sha256(_json_bytes(payload)).hexdigest()
    _atomic_write(checkpoint_path, _json_bytes(payload))


def migrate_project_model_checkpoint(project: Path) -> int:
    """Create an acceleration checkpoint without modifying historical events."""
    events_path, snapshot_path, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        events = _read_events(events_path)
        if not events:
            raise FileNotFoundError(f"no canonical Project Model in {events_path}")
        model = _replay(events, verified=True)
        if model is None:
            raise FileNotFoundError(f"no canonical Project Model in {events_path}")
        payload = {"revision": len(events), "head_hash": events[-1]["event_hash"],
                   "event_offset": events_path.stat().st_size, "model": model,
                   "model_hash": hashlib.sha256(_json_bytes(model)).hexdigest()}
        payload["checkpoint_hash"] = hashlib.sha256(_json_bytes(payload)).hexdigest()
        _atomic_write(snapshot_path.with_name(CHECKPOINT_FILE), _json_bytes(payload))
        _record_validated_model(snapshot_path.parent, payload["model_hash"])
        return len(events)


def load_project_model(project: Path) -> tuple[int, dict[str, Any]]:
    """Load and verify from the event log; repair a missing/stale snapshot."""
    events_path, snapshot_path, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        revision, model, _head = _load_locked(events_path, snapshot_path)
        return revision, model


def _load_locked(events_path: Path, snapshot_path: Path
                 ) -> tuple[int, dict[str, Any], str]:
    """Load and verify with the store lock already held.

    Returns (revision, model, head_hash). The snapshot fast path, tail
    verification, repair and verdict caching live here so writers
    (merge_patch, evidence merges) reuse them instead of re-scanning
    the whole event log per commit.
    """
    signature = _file_signature(events_path)
    # Fast path: when the snapshot's revision and head match the
    # log tail, the snapshot model IS the replay without scanning
    # 17 GB of history. The tail hash is recomputed for the last
    # event; a middle tamper changes the head and falls back to the
    # full verified read, which refuses.
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        snapshot = None
    if isinstance(snapshot, dict) and signature is not None:
        tail = _read_events_tail(events_path)
        if (tail is not None
                and tail.get("revision") == snapshot.get("revision")
                and tail.get("event_hash") == snapshot.get("head_hash")):
            digest = tail.pop("event_hash", None)
            actual = hashlib.sha256(_json_bytes(tail)).hexdigest()
            tail["event_hash"] = digest
            # The snapshot model hash guards a snapshot whose keys
            # match but whose model was replaced: hashing 350 MB is
            # ~1 s, vs 50 s to replay 17 GB of history.
            replay_model_hash: str | None = None
            if isinstance(snapshot.get("model"), dict):
                replay_model_hash = hashlib.sha256(
                    _json_bytes(snapshot["model"])).hexdigest()
            model_hash_ok = (
                isinstance(snapshot.get("model_hash"), str)
                and replay_model_hash == snapshot["model_hash"]
            )
            if digest == actual and model_hash_ok:
                head = ((snapshot.get("head_hash"), validate_project_model))
                with _validation_lock:
                    known = head in _validated_heads
                    if known:
                        _validated_heads.move_to_end(head)
                if not known and replay_model_hash in _read_validated_models(
                        snapshot_path.parent):
                    # Validated at commit time by an earlier process:
                    # the hash is byte-identity with validated bytes.
                    known = True
                if not known:
                    validate_project_model(snapshot["model"])
                    _record_validated_model(
                        snapshot_path.parent, replay_model_hash or "")
                    with _validation_lock:
                        _validated_heads[head] = None
                        if len(_validated_heads) > _VALIDATED_HEAD_LIMIT:
                            _validated_heads.popitem(last=False)
                key = (events_path.resolve(), signature, validate_project_model)
                with _validation_lock:
                    _verified_revisions[key] = snapshot["revision"]
                    _verified_revisions.move_to_end(key)
                    if len(_verified_revisions) > _VALIDATED_HEAD_LIMIT:
                        _verified_revisions.popitem(last=False)
                # The snapshot was parsed fresh above, so its model is
                # already caller-owned: no deepcopy, no shared cache.
                return (snapshot["revision"], snapshot["model"],
                        snapshot["head_hash"])
    # A checkpoint is a verified replay boundary. It keeps recovery bounded
    # when the ordinary head snapshot is missing or stale, without rewriting
    # or renumbering any historical event hashes.
    try:
        checkpoint = json.loads(
            snapshot_path.with_name(CHECKPOINT_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        checkpoint = None
    if isinstance(checkpoint, dict):
        checkpoint_hash = checkpoint.pop("checkpoint_hash", None)
        log_tail = _read_events_tail(events_path)
        # An in-place rewrite keeps the stored event_hash field while
        # changing the bytes it was computed over: recompute the tail's
        # own hash before trusting its revision+head (as the snapshot
        # fast path above does). Without this the checkpoint path
        # serves the pre-rewrite model after a same-size tamper.
        tail_self_ok = False
        if isinstance(log_tail, dict) and isinstance(log_tail.get("event_hash"), str):
            tail_copy = dict(log_tail)
            tail_digest = tail_copy.pop("event_hash", None)
            try:
                tail_self_ok = (
                    tail_digest == hashlib.sha256(_json_bytes(tail_copy)).hexdigest())
            except (TypeError, ValueError):
                tail_self_ok = False
        checkpoint_ok = (
            checkpoint_hash == hashlib.sha256(_json_bytes(checkpoint)).hexdigest()
            and type(checkpoint.get("revision")) is int
            and isinstance(checkpoint.get("head_hash"), str)
            and type(checkpoint.get("event_offset")) is int
            and checkpoint["event_offset"] <= events_path.stat().st_size
            and log_tail is not None
            and tail_self_ok
            and log_tail.get("revision", 0) >= checkpoint.get("revision", 0)
            and (log_tail.get("revision") != checkpoint.get("revision")
                 or log_tail.get("event_hash") == checkpoint.get("head_hash"))
            and isinstance(checkpoint.get("model"), dict)
            and checkpoint.get("model_hash") == hashlib.sha256(
                _json_bytes(checkpoint["model"])).hexdigest()
            and checkpoint["model_hash"] in _read_validated_models(snapshot_path.parent)
        )
        suffix = None
        if checkpoint_ok:
            try:
                suffix = _read_events_after(
                    events_path, revision=checkpoint["revision"],
                    head_hash=checkpoint["head_hash"],
                    offset=checkpoint["event_offset"])
            except (OSError, ValueError):
                checkpoint_ok = False
        if checkpoint_ok and suffix is not None:
            if suffix:
                model = _replay_suffix(checkpoint["model"], suffix)
                revision = checkpoint["revision"] + len(suffix)
                head_hash = suffix[-1]["event_hash"]
            else:
                model = checkpoint["model"]
                revision = checkpoint["revision"]
                head_hash = checkpoint["head_hash"]
            if revision > 0:
                _write_snapshot(snapshot_path, [{"event_hash": head_hash,
                                                 "revision": revision}], model,
                                revision=revision)
                return revision, model, head_hash
    events = _read_events(events_path)
    model = _replay(events, verified=True)
    if model is None:
        raise FileNotFoundError(f"no canonical Project Model in {events_path}")
    expected_revision = len(events)
    expected_head = events[-1]["event_hash"]
    try:
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        snapshot = None
    # Freshness is revision + head hash + model hash. The model hash
    # guards a snapshot whose keys match but whose model was replaced:
    # hashing 350 MB is ~1 s, vs seconds to deep-compare two models on
    # every load (measured 77 s across three boundaries). A mismatch
    # rewrites the snapshot from the verified events replay.
    replay_hash = hashlib.sha256(_json_bytes(model)).hexdigest()
    snapshot_self_ok = (
        isinstance(snapshot, dict)
        and isinstance(snapshot.get("model"), dict)
        and isinstance(snapshot.get("model_hash"), str)
        and hashlib.sha256(
            _json_bytes(snapshot["model"])).hexdigest()
        == snapshot.get("model_hash")
    )
    snapshot_fresh = (
        isinstance(snapshot, dict)
        and snapshot.get("revision") == expected_revision
        and snapshot.get("head_hash") == expected_head
        and isinstance(snapshot.get("model_hash"), str)
        and replay_hash == snapshot.get("model_hash")
        and snapshot_self_ok
    )
    if not snapshot_fresh:
        _write_snapshot(snapshot_path, events, model)
    if signature is not None and _file_signature(events_path) == signature:
        key = (events_path.resolve(), signature, validate_project_model)
        with _validation_lock:
            _verified_revisions[key] = len(events)
            _verified_revisions.move_to_end(key)
            if len(_verified_revisions) > _VALIDATED_HEAD_LIMIT:
                _verified_revisions.popitem(last=False)
    return len(events), model, events[-1]["event_hash"]


def load_reel_projection(project: Path) -> tuple[int, dict[str, Any] | None]:
    """The reel-scoped rows at the log head, or (revision, None) to fall back.

    Reads only the KB-sized sidecar plus the log tail: no snapshot parse,
    no validation. The sidecar is written on every commit from the
    just-validated model, so a revision+head match means its rows are the
    validated head's. Anything else - missing sidecar, tail mismatch,
    tampered tail hash - returns None and the caller takes the full
    verified load. Never raises for store damage; a missing log returns
    (0, None).
    """
    events_path, _, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            sidecar = json.loads(
                events_path.with_name(REEL_PROJECTION_FILE).read_text(
                    encoding="utf-8"))
        except (OSError, ValueError):
            return 0, None
        if not isinstance(sidecar, dict):
            return 0, None
        tail = _read_events_tail(events_path)
        if tail is None:
            return 0, None
        if (tail.get("revision") != sidecar.get("revision")
                or tail.get("event_hash") != sidecar.get("head_hash")):
            return 0, None
        digest = tail.pop("event_hash", None)
        actual = hashlib.sha256(_json_bytes(tail)).hexdigest()
        tail["event_hash"] = digest
        if digest != actual:
            return 0, None
        collections = sidecar.get("collections")
        if not isinstance(collections, dict):
            return 0, None
        if (set(collections) != set(REEL_SCOPED_COLLECTIONS)
                or not all(isinstance(collections[key], list)
                           for key in REEL_SCOPED_COLLECTIONS)):
            return 0, None
        return int(sidecar["revision"]), dict(collections)


def project_model_revision(project: Path) -> int:
    """The validated revision pin, without loading unused model data again.

    A changed file must pass a full load. The kernel signature includes
    inode and ctime, so replacing the log or restoring its mtime cannot
    preserve a cached verdict. All reads and writes use the store lock.
    """
    events_path, _, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        signature = _file_signature(events_path)
        key = (events_path.resolve(), signature, validate_project_model)
        with _validation_lock:
            revision = _verified_revisions.get(key)
            if signature is not None and revision is not None:
                _verified_revisions.move_to_end(key)
                return revision
    revision, _ = load_project_model(project)
    return revision


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
        # Fast path: when the tail matches the snapshot at the expected
        # revision, append without scanning GBs of history. The tail hash
        # is recomputed; a middle tamper changes the head and falls back
        # to the full verified read, which refuses. A concurrent append
        # changes the tail revision and raises stale revision below.
        if expected_revision > 0:
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                snapshot = None
            if (isinstance(snapshot, dict)
                    and snapshot.get("revision") == expected_revision):
                tail = _read_events_tail(events_path)
                if (tail is not None
                        and tail.get("revision") == expected_revision
                        and tail.get("event_hash") == snapshot.get("head_hash")):
                    digest = tail.pop("event_hash", None)
                    actual = hashlib.sha256(_json_bytes(tail)).hexdigest()
                    tail["event_hash"] = digest
                    if digest == actual:
                        event = {
                            "revision": expected_revision + 1,
                            "previous_hash": digest,
                            "kind": "replace",
                            "author": author,
                            "reason": reason,
                            "schema_version": model["schema_version"],
                            "payload": model,
                        }
                        event["event_hash"] = hashlib.sha256(
                            _json_bytes(event)).hexdigest()
                        events_path.parent.mkdir(parents=True, exist_ok=True)
                        with events_path.open("ab") as stream:
                            stream.write(_json_bytes(event))
                            stream.flush()
                            os.fsync(stream.fileno())
                        _write_snapshot(snapshot_path, [{
                            "event_hash": digest,
                        }] * expected_revision + [event], model)
                        return expected_revision + 1
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
                       author: str = "ren", reason: str = "",
                       allow_rebase: bool = True) -> int:
    """Append a deterministic JSON merge patch over the current model."""
    if not isinstance(patch, dict):
        raise TypeError("project model patch must be an object")
    events_path, snapshot_path, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        revision, model, head_hash = _load_locked(events_path, snapshot_path)
        checked: list[dict[str, Any]] = []
        if revision != expected_revision:
            if not allow_rebase or expected_revision > revision:
                raise RuntimeError(f"stale project model revision {expected_revision}; current revision is {revision}")
            events = _read_events(events_path)
            for prior in events[expected_revision:]:
                checked.append({"revision": prior["revision"],
                                "event_hash": prior["event_hash"]})
                if _scopes_overlap({(key, None) for key in patch},
                                   _event_scopes(prior)):
                    raise RuntimeError(
                        f"stale project model revision {expected_revision}; "
                        f"conflicting update at revision {prior['revision']}")
        updated = _apply_patch(model, patch)
        validate_project_model(updated)
        event = {"revision": revision + 1, "previous_hash": head_hash,
                  "kind": "merge_patch", "author": author, "reason": reason,
                  "schema_version": updated["schema_version"], "payload": patch,
                  "rebase_checked": checked}
        event["event_hash"] = hashlib.sha256(_json_bytes(event)).hexdigest()
        with events_path.open("ab") as stream:
            stream.write(_json_bytes(event))
            stream.flush()
            os.fsync(stream.fileno())
        _write_snapshot(snapshot_path, [{"event_hash": head_hash}, event], updated,
                        revision=revision + 1)
        return revision + 1


def commit_project_model_records(
        project: Path, changes: list[dict[str, Any]], *, expected_revision: int,
        author: str = "ren", reason: str = "", allow_rebase: bool = True) -> int:
    """Append record-level collection changes, rebasing only disjoint scopes.

    ``changes`` contains append/update/delete operations keyed by stable ID.
    On a stale base the global revision remains authoritative, but the write
    may proceed if every intervening event is provably disjoint. The event
    records the exact intervening heads considered during that decision.
    """
    if not changes:
        return project_model_revision(Path(project))
    normalized = sorted(changes, key=lambda item: (
        item["collection"], item["id"], item["operation"]))
    seen: set[tuple[str, str]] = set()
    for change in normalized:
        if change["collection"] not in _COLLECTION_KEYS:
            raise ValueError(f"not a Project Model collection: {change['collection']!r}")
        if not isinstance(change["id"], str) or not change["id"]:
            raise ValueError("record changes require a non-empty stable id")
        key = (change["collection"], change["id"])
        if key in seen:
            raise ValueError(f"record changed more than once: {key!r}")
        seen.add(key)
        if change["operation"] not in ("append", "update", "delete"):
            raise ValueError(f"unknown record operation {change['operation']!r}")
        if change["operation"] != "delete" and change["record"]["id"] != change["id"]:
            raise ValueError("record change id must match record id")
    requested_scope = _event_scopes({"kind": "record_changes", "payload": normalized})
    events_path, snapshot_path, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        revision, model, head_hash = _load_locked(events_path, snapshot_path)
        if expected_revision > revision:
            raise RuntimeError(
                f"stale project model revision {expected_revision}; current revision is {revision}")
        checked: list[dict[str, Any]] = []
        if revision != expected_revision:
            if not allow_rebase:
                raise RuntimeError(
                    f"stale project model revision {expected_revision}; current revision is {revision}")
            current_events = _read_events(events_path)
            for prior in current_events[expected_revision:]:
                checked.append({"revision": prior["revision"],
                                "event_hash": prior["event_hash"]})
                if _scopes_overlap(requested_scope, _event_scopes(prior)):
                    raise RuntimeError(
                        f"project model record conflict at revision {prior['revision']}")
            head_hash = current_events[-1]["event_hash"]
        updated = _apply_record_changes(model, normalized)
        validate_project_model(updated)
        event = {"revision": revision + 1,
                 "previous_hash": head_hash,
                 "kind": "record_changes", "author": author, "reason": reason,
                 "schema_version": updated["schema_version"],
                 "payload": normalized, "rebase_checked": checked}
        event["event_hash"] = hashlib.sha256(_json_bytes(event)).hexdigest()
        with events_path.open("ab") as stream:
            stream.write(_json_bytes(event))
            stream.flush()
            os.fsync(stream.fileno())
        _write_snapshot(snapshot_path, [{"event_hash": head_hash,
                                        "revision": revision}, event], updated,
                        revision=revision + 1)
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
        model = _load_locked(events_path, snapshot_path)[1]
        changes = []
        for collection, rows in (("observations", observations),
                                 ("events", events_to_merge or []),
                                 ("provenance", provenance or [])):
            existing = {row["id"]: row for row in model.get(collection, [])}
            rows_by_id = {row["id"]: row for row in rows}
            for record_id in sorted(rows_by_id):
                row = rows_by_id[record_id]
                prior = existing.get(row["id"])
                if prior == row:
                    continue
                changes.append({"collection": collection,
                                "operation": "update" if prior is not None else "append",
                                "id": row["id"], "record": row})
        if not changes:
            revision, _, _ = _load_locked(events_path, snapshot_path)
            if revision != expected_revision:
                raise RuntimeError(
                    f"stale project model revision {expected_revision}; current revision is {revision}")
            return revision
    return commit_project_model_records(
        project, changes, expected_revision=expected_revision,
        author=author, reason=reason, allow_rebase=False)


def merge_evidence_model(project: Path, evidence_model: dict[str, Any], *,
                         expected_revision: int, author: str,
                         reason: str = "capability evidence update") -> int:
    """CAS-merge evidence collections, preserving all other model state."""
    validate_project_model(evidence_model)
    events_path, _snapshot_path, lock_path = _paths(Path(project))
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        events = _read_events(events_path)
        revision = len(events)
        model = _replay(events, verified=True)
        if model is None:
            raise FileNotFoundError(f"no canonical Project Model in {events_path}")
        if evidence_model["id"] != model["id"]:
            raise ValueError("evidence update belongs to a different Project Model")
        if evidence_model["schema_version"] != model["schema_version"]:
            raise ValueError("evidence update uses a different Project Model schema version")
        changes: list[dict[str, Any]] = []
        query_merge: list[dict[str, Any]] | None = None
        for key in (*EVIDENCE_COLLECTIONS, "clocks"):
            if key == "query_results":
                # Query rows carry no stable id (status/candidate lists),
                # so union them by value as the pre-record merge did.
                # An id-keyed record change cannot represent them.
                current_rows = list(model.get(key, []) or [])
                merged_rows = list(current_rows)
                for row in evidence_model.get(key, []) or []:
                    if row not in merged_rows:
                        merged_rows.append(row)
                if merged_rows != current_rows:
                    query_merge = merged_rows
                continue
            incoming_by_id = {row["id"]: row
                              for row in evidence_model.get(key, [])}
            incoming = [incoming_by_id[record_id]
                        for record_id in sorted(incoming_by_id)]
            if not incoming:
                continue
            by_id = {row["id"]: row for row in model.get(key, [])}
            for row in incoming:
                old = by_id.get(row["id"], {})
                merged = {**old, **row}
                if isinstance(old.get("metadata"), dict) and isinstance(row.get("metadata"), dict):
                    merged["metadata"] = {**old["metadata"], **row["metadata"]}
                by_id[row["id"]] = merged
            for row in incoming:
                merged = by_id[row["id"]]
                old = next((candidate for candidate in model.get(key, [])
                            if candidate["id"] == row["id"]), None)
                if old != merged:
                    changes.append({"collection": key,
                                    "operation": "update" if old is not None else "append",
                                    "id": row["id"], "record": merged})
        if query_merge is not None:
            # Id-less rows cannot ride a record_changes event: fold them
            # and any id-based updates into one merge_patch revision.
            updated = (_apply_record_changes(model, changes)
                       if changes else dict(model))
            updated["query_results"] = query_merge
            validate_project_model(updated)
            if revision != expected_revision:
                current_events = _read_events(events_path)
                if expected_revision > revision:
                    raise RuntimeError(
                        f"stale project model revision {expected_revision}; current revision is {revision}")
                checked: list[dict[str, Any]] = []
                touched = {(c["collection"], None) for c in changes} | {("query_results", None)}
                for prior in current_events[expected_revision:]:
                    checked.append({"revision": prior["revision"],
                                    "event_hash": prior["event_hash"]})
                    if _scopes_overlap(touched, _event_scopes(prior)):
                        raise RuntimeError(
                            f"stale project model revision {expected_revision}; "
                            f"conflicting update at revision {prior['revision']}")
            else:
                checked = []
            patch: dict[str, Any] = {"query_results": query_merge}
            for collection in {c["collection"] for c in changes}:
                patch[collection] = updated[collection]
            event = {"revision": revision + 1,
                     "previous_hash": events[-1]["event_hash"] if events else "0" * 64,
                     "kind": "merge_patch", "author": author, "reason": reason,
                     "schema_version": updated["schema_version"], "payload": patch,
                     "rebase_checked": checked}
            event["event_hash"] = hashlib.sha256(_json_bytes(event)).hexdigest()
            snapshot_path = events_path.with_name(SNAPSHOT_FILE)
            with events_path.open("ab") as stream:
                stream.write(_json_bytes(event))
                stream.flush()
                os.fsync(stream.fileno())
            events.append(event)
            _write_snapshot(snapshot_path, events, updated)
            return revision + 1
        if not changes:
            if revision != expected_revision:
                raise RuntimeError(
                    f"stale project model revision {expected_revision}; current revision is {revision}")
            return revision
    return commit_project_model_records(
        project, changes, expected_revision=expected_revision,
        author=author, reason=reason, allow_rebase=True)


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
        canonical = _replay(events, verified=True)
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
