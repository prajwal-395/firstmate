"""Derived-artifact lifecycle beside the canonical Project Model.

The Project Model event log is canonical (DATA-001/016); everything
computed from it - compat views, search indexes, signal projections,
materialized bins, cached graphs - is derived (DATA-003). This module
is the registry that keeps that boundary explicit:

- owners declare each derived artifact with the sources it was built
  from and the dotted path that rebuilds it,
- ``check_artifacts`` compares those records against the live model and
  marks what a source or model revision invalidated (DATA-017),
- ``observation_history`` replays the event log for one stable id, so
  the full revision chain of an observation survives (DATA-040),
- ``record_derivation`` captures the structured derivation record new
  writers stamp: producer version, preprocessing parameters, sampling
  policy and source revision (DATA-013).

Detection lives here; rebuilding stays with each artifact's owner, who
knows the producer budgets. An artifact nobody re-registers after its
owner lane lands reads as ``unowned`` rather than silently current.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from typing import Any, Callable

try:
    from library.tools import project_model_store
except ImportError:  # imported as `tools.*` from inside library/
    from tools import project_model_store

try:
    from library.tools.project_model_contract import validate_typed_confidence
except ImportError:  # imported as `tools.*` from inside library/
    from tools.project_model_contract import validate_typed_confidence

DERIVED_FILE = "derived_artifacts.json"

STATUSES = ("current", "stale_source", "stale_model", "stale_producer",
            "invalidated", "unowned", "missing")
"""Every lifecycle state a derived artifact can report."""

KNOWN_DERIVED = (
    {"kind": "compat_view", "name": "m9_compat_view",
     "description": "legacy M9-shaped query view over migrated evidence",
     "sources": ["observations", "events", "entities"]},
    {"kind": "search_index", "name": "bm25_index",
     "description": "BM25 text index over evidence payloads",
     "sources": ["observations", "events"]},
    {"kind": "search_index", "name": "minilm_index",
     "description": "MiniLM hybrid text embedding index",
     "sources": ["observations", "events"]},
    {"kind": "search_index", "name": "clip_index",
     "description": "CLIP visual embedding index over frame crops",
     "sources": ["observations", "tracks"]},
    {"kind": "search_index", "name": "unified_anchors",
     "description": "unified audiovisual anchor index",
     "sources": ["observations", "signals"]},
    {"kind": "signal_projection", "name": "signals_projection",
     "description": "projected signal views over raw signal samples",
     "sources": ["signals"]},
    {"kind": "materialized_view", "name": "one_second_bins",
     "description": "one-second world-model bins derived from intervals",
     "sources": ["observations", "events"]},
    {"kind": "graph_cache", "name": "footage_graph_cache",
     "description": "cached project-graph build over catalog inputs",
     "sources": ["assets", "streams"]},
)
"""Derived artifacts the engine is known to keep. Owners register the
rebuild entrypoint; until then each reads ``unowned``, never current."""


def _paths(project: Path) -> Path:
    return Path(project) / project_model_store.STORE_DIR / DERIVED_FILE


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _read_registry(project: Path) -> dict[str, dict[str, Any]]:
    path = _paths(project)
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(records, dict):
        return {}
    return {name: record for name, record in records.items()
            if isinstance(record, dict)}


def register_artifact(project: Path, *, name: str, kind: str,
                      source_digests: list[str], model_revision: int,
                      producer_versions: dict[str, str] | None = None,
                      rebuild: str | None = None,
                      owner: str | None = None) -> dict[str, Any]:
    """Declare a derived artifact and the inputs it was built from.

    ``rebuild`` is the dotted path of the owner's rebuild entrypoint;
    without one the artifact stays ``unowned``. Re-registering after a
    rebuild refreshes the record; the previous record is not kept here,
    the model event log already carries the history.
    """
    if not name or not kind:
        raise ValueError("derived artifacts need a name and kind")
    if not isinstance(model_revision, int) or model_revision < 0:
        raise ValueError("model revision must be a non-negative integer")
    record = {"name": name, "kind": kind,
              "source_digests": sorted(set(source_digests)),
              "model_revision": model_revision,
              "producer_versions": dict(producer_versions or {}),
              "rebuild": rebuild, "owner": owner,
              "recorded_at": int(time.time()),
              "status": "current" if rebuild else "unowned",
              "status_reason": (None if rebuild
                                else "no rebuild entrypoint registered")}
    registry = _read_registry(project)
    registry[name] = record
    _atomic_write(_paths(project), (json.dumps(
        registry, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    return record


def registered_artifacts(project: Path) -> dict[str, dict[str, Any]]:
    """Every declared derived artifact by name."""
    return _read_registry(project)


def invalidate_artifact(project: Path, name: str, reason: str) -> dict[str, Any]:
    """Mark a derived artifact invalidated so no reader trusts it.

    Invalidation is a status flip with a reason, not a deletion: the
    record of what was built from what survives for the audit trail,
    and the owner rebuilds through the registered entrypoint.
    """
    registry = _read_registry(project)
    record = registry.get(name)
    if record is None:
        raise KeyError(f"no derived artifact {name!r} registered")
    if not reason or not reason.strip():
        raise ValueError("invalidation needs a reason")
    record["status"] = "invalidated"
    record["status_reason"] = reason.strip()
    record["recorded_at"] = int(time.time())
    _atomic_write(_paths(project), (json.dumps(
        registry, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    return record


def check_artifacts(project: Path, model: dict[str, Any], *,
                    model_revision: int,
                    current_producers: dict[str, str] | None = None
                    ) -> dict[str, dict[str, Any]]:
    """Compare derived records against the live model (DATA-017).

    An artifact goes ``stale_source`` when a source digest it was built
    from no longer appears in the model assets, ``stale_model`` when the
    model moved past its recorded revision, and ``stale_producer`` when
    a named producer version changed. ``invalidated`` records stay
    invalidated until the owner re-registers; ``unowned`` records stay
    unowned. Nothing is rebuilt here.
    """
    live_digests = set()
    for asset in model.get("assets", []):
        digest = asset.get("content_digest") if isinstance(asset, dict) else None
        if isinstance(digest, str) and digest:
            live_digests.add(digest)
    current = dict(current_producers or {})
    report = {}
    for name, record in _read_registry(project).items():
        outcome = dict(record)
        if record.get("status") == "invalidated":
            report[name] = outcome
            continue
        if not record.get("rebuild"):
            outcome["status"] = "unowned"
            outcome["status_reason"] = "no rebuild entrypoint registered"
            report[name] = outcome
            continue
        recorded_digests = set(record.get("source_digests", []))
        if recorded_digests and not recorded_digests <= live_digests:
            outcome["status"] = "stale_source"
            outcome["status_reason"] = (
                "sources changed: "
                + ", ".join(sorted(recorded_digests - live_digests)))
        elif model_revision != record.get("model_revision"):
            outcome["status"] = "stale_model"
            outcome["status_reason"] = (
                f"model at revision {model_revision}, "
                f"artifact built at {record.get('model_revision')}")
        else:
            recorded_producers = record.get("producer_versions", {})
            drifted = [lane for lane, version in recorded_producers.items()
                       if lane in current and current[lane] != version]
            if drifted:
                outcome["status"] = "stale_producer"
                outcome["status_reason"] = (
                    "producer versions changed: " + ", ".join(sorted(drifted)))
            else:
                outcome["status"] = "current"
                outcome["status_reason"] = None
        report[name] = outcome
    return report


def _row_digest(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def _scan_collection(lines: list[str], collection: str,
                     row_id: str) -> list[dict[str, Any]]:
    """Replay event-log lines for one row id in one collection."""
    chain = []
    last_hash: str | None = None
    gone = False
    for line in lines:
        event = json.loads(line)
        payload = event.get("payload") or {}
        row = None
        for candidate in payload.get(collection, []) or []:
            if isinstance(candidate, dict) and candidate.get("id") == row_id:
                row = candidate
        if event.get("kind") == "replace" and row is None:
            model = payload if isinstance(payload, dict) else {}
            for candidate in model.get(collection, []) or []:
                if isinstance(candidate, dict) and candidate.get("id") == row_id:
                    row = candidate
        if row is None:
            if (last_hash is not None and not gone
                    and event.get("kind") == "merge_patch"
                    and collection in payload):
                chain.append({"revision": event.get("revision"),
                              "event_hash": event.get("event_hash"),
                              "author": event.get("author"),
                              "reason": event.get("reason"),
                              "change": "removed"})
                last_hash = None
                gone = True
            continue
        digest = _row_digest(row)
        change = "added" if last_hash is None else (
            "modified" if digest != last_hash else "carried")
        last_hash = digest
        gone = False
        chain.append({"revision": event.get("revision"),
                      "event_hash": event.get("event_hash"),
                      "author": event.get("author"),
                      "reason": event.get("reason"),
                      "change": change, "row": row})
    return chain


def collection_history(project: Path, collection: str,
                       row_id: str) -> list[dict[str, Any]]:
    """Replay the event log for one stable id in one collection.

    Same chain semantics as :func:`observation_history` (added,
    modified, carried, removed), generalized past the observation,
    event and provenance collections so clock transforms, policies and
    any other revisioned row keep their history too. Each entry
    carries the row as written at that revision.
    """
    if not collection or not isinstance(collection, str):
        raise ValueError("collection history needs a collection name")
    if not row_id or not isinstance(row_id, str):
        raise ValueError("collection history needs a row id")
    events_path = Path(project) / project_model_store.STORE_DIR / project_model_store.EVENTS_FILE
    if not events_path.is_file():
        raise FileNotFoundError(f"no project model event log in {events_path}")
    lines = events_path.read_text(encoding="utf-8").splitlines()
    return [{"revision": entry["revision"], "event_hash": entry["event_hash"],
             "author": entry["author"], "reason": entry["reason"],
             "change": entry["change"], "row": entry.get("row")}
            for entry in _scan_collection(lines, collection, row_id)]


def observation_history(project: Path, obs_id: str) -> list[dict[str, Any]]:
    """Replay the event log for one observation id (DATA-040).

    Each entry names the revision, event hash, author, reason, and what
    changed: ``added`` on first sight, ``modified`` when the row
    differs, ``removed`` when a merge drops it, ``carried`` when a full
    replace event keeps it byte-identical. Model-level history already
    lives in the event chain; this is the per-row chain on top of it.
    """
    if not obs_id or not isinstance(obs_id, str):
        raise ValueError("observation history needs an observation id")
    events_path = Path(project) / project_model_store.STORE_DIR / project_model_store.EVENTS_FILE
    if not events_path.is_file():
        raise FileNotFoundError(f"no project model event log in {events_path}")
    chain = []
    last_hash: str | None = None
    gone = False
    for line in events_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        payload = event.get("payload") or {}
        rows: dict[str, dict] = {}
        for key in ("observations", "events", "provenance"):
            for row in payload.get(key, []) or []:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    rows[row["id"]] = row
        if event.get("kind") == "replace" and obs_id not in rows:
            model = payload if isinstance(payload, dict) else {}
            for collection in ("observations", "events", "provenance"):
                for row in model.get(collection, []) or []:
                    if isinstance(row, dict) and row.get("id") == obs_id:
                        rows[obs_id] = row
        if obs_id not in rows:
            if last_hash is not None and not gone and event.get("kind") == "merge_patch":
                touched = any(
                    key in ("observations", "events", "provenance")
                    for key in payload)
                if touched:
                    chain.append({"revision": event.get("revision"),
                                  "event_hash": event.get("event_hash"),
                                  "author": event.get("author"),
                                  "reason": event.get("reason"),
                                  "change": "removed"})
                    last_hash = None
                    gone = True
            continue
        digest = hashlib.sha256(json.dumps(
            rows[obs_id], sort_keys=True, separators=(",", ":"),
            ensure_ascii=False).encode("utf-8")).hexdigest()
        change = "added" if last_hash is None else (
            "modified" if digest != last_hash else "carried")
        last_hash = digest
        gone = False
        chain.append({"revision": event.get("revision"),
                      "event_hash": event.get("event_hash"),
                      "author": event.get("author"),
                      "reason": event.get("reason"),
                      "change": change})
    return chain


def record_derivation(*, producer: str, producer_version: str,
                      preprocessing: dict[str, Any] | None = None,
                      sampling_policy: dict[str, Any] | None = None,
                      source_revision: str | None = None) -> dict[str, Any]:
    """Structured derivation record new writers stamp (DATA-013).

    Model version, preprocessing parameters, sampling policy and source
    revision travel as named fields, not as free text inside a method
    string, so a later reader can tell whether two rows are comparable
    without re-running anything.
    """
    if not producer or not producer_version:
        raise ValueError("derivation records need a producer and version")
    for label, mapping in (("preprocessing", preprocessing),
                           ("sampling_policy", sampling_policy)):
        if mapping is not None and not isinstance(mapping, dict):
            raise ValueError(f"{label} must be an object when present")
    try:
        frozen = json.dumps({"preprocessing": preprocessing or {},
                             "sampling_policy": sampling_policy or {}},
                            sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"derivation parameters must be JSON-safe: {exc}") from exc
    return {"producer": producer, "producer_version": producer_version,
            "preprocessing": preprocessing or {},
            "sampling_policy": sampling_policy or {},
            "source_revision": source_revision,
            "derivation_digest": "sha256:" + hashlib.sha256(
                frozen.encode("utf-8")).hexdigest()}


def normalize_envelope_preprocessing(envelope: dict[str, Any]) -> dict[str, Any]:
    """Lift a Source Memory envelope into a structured derivation (DATA-013).

    Envelopes record the sampling method as free text and the source
    revision as a digest; this normalizer carries both into the
    ``record_derivation`` shape without parsing taste out of the method
    string. What the envelope never recorded stays explicit: an absent
    method reads ``"unknown"`` with the reason named, never a guessed
    default. New writers should stamp ``record_derivation`` directly;
    this is the bridge for the legacy rows already on disk.
    """
    if not isinstance(envelope, dict):
        raise ValueError("envelope normalization needs the envelope object")
    producer = envelope.get("producer")
    producer_version = envelope.get("producer_version")
    if not producer or not producer_version:
        raise ValueError("envelope names no producer to preserve")
    method = envelope.get("sampling_method") or "unknown"
    intervals = envelope.get("coverage_intervals") or []
    preprocessing = dict(envelope.get("preprocessing") or {})
    preprocessing.setdefault("method", method)
    if envelope.get("sample_resolution") is not None:
        preprocessing.setdefault("sample_resolution", envelope["sample_resolution"])
    preprocessing.setdefault("confidence_semantics", envelope.get(
        "confidence_semantics", "producer_defined"))
    preprocessing.setdefault("method_breakdown", (
        "absent: the envelope records the method as one string"))
    preprocessing = {key: value for key, value in preprocessing.items()
                     if value is not None}
    sampling_policy = dict(envelope.get("sampling_policy") or {})
    sampling_policy.setdefault(
        "coverage_status", envelope.get("coverage_status", "unknown"))
    sampling_policy.setdefault("coverage_intervals",
                               len(intervals) if isinstance(intervals, list) else 0)
    sampling_policy.setdefault(
        "verified_status", envelope.get("verified_status", "unverified"))
    sampling_policy.setdefault("source_stream_id", envelope.get("source_stream_id"))
    return record_derivation(
        producer=str(producer), producer_version=str(producer_version),
        preprocessing=preprocessing, sampling_policy=sampling_policy,
        source_revision=envelope.get("source_digest"))


def typed_confidence(value: float, kind: str, producer: str,
                     calibration_version: str | None = None) -> dict[str, Any]:
    """Build a schema-valid typed confidence detail (DATA-041).

    Raw model scores keep their kind (rank, logit, cosine, raw_score);
    only a ``calibrated_probability`` with a calibration version may be
    read as a probability. Validation is the contract's own, so writers
    cannot stamp a kind the boundary refuses.
    """
    validate_typed_confidence(value, kind, producer, calibration_version)
    detail: dict[str, Any] = {"value": value, "kind": kind,
                              "producer": producer}
    if calibration_version is not None:
        detail["calibration_version"] = calibration_version
    return detail


def _default_m6_reader(project: Path, from_clock: str,
                       _to_clock: str) -> dict[str, Any] | None:
    """Resolve a transform's source digest through clocks and assets,
    then read its M6 direct measurement. Returns None when any link is
    missing: absent drift evidence stays absent, never zero."""
    try:
        from library.tools import conversation_clock
    except ImportError:  # imported as `tools.*` from inside library/
        from tools import conversation_clock
    try:
        _revision, model = project_model_store.load_project_model(Path(project))
    except (FileNotFoundError, ValueError):
        return None
    asset_id = None
    for clock in model.get("clocks", []):
        if isinstance(clock, dict) and clock.get("id") == from_clock:
            domain = clock.get("domain") or {}
            asset_id = domain.get("asset_id")
    digest = None
    for asset in model.get("assets", []):
        if isinstance(asset, dict) and asset.get("id") == asset_id:
            digest = asset.get("content_digest")
    if not isinstance(digest, str) or not digest:
        return None
    record = conversation_clock.read_clock(digest, Path(project))
    return record if isinstance(record, dict) else None


def _uncertainty_from_spread(p5_seconds: float, p95_seconds: float,
                             drift_seconds: float, ticks_per_second: int = 1000
                             ) -> int:
    """The migration's own ceiling formula, kept in one place so the
    history check recomputes rather than restates the bound."""
    return int(((Decimal(str(p95_seconds)) - Decimal(str(p5_seconds))
                 + Decimal(str(drift_seconds)))
                 * Decimal(ticks_per_second) / 2).to_integral_value(
                     rounding=ROUND_CEILING))


def transform_drift_history(
        project: Path, transform_id: str, *,
        m6_reader: Callable[..., dict[str, Any] | None] | None = None
) -> dict[str, Any]:
    """Preserve a clock transform's uncertainty and drift record (DATA-007).

    The model row carries the folded ``uncertainty_ticks`` bound; the
    M6 slot behind its provenance carries the measured spread and drift
    the bound was folded from. This assembles both plus the row's
    revision chain: every revision's bound, the measured components
    behind the live row, whether the recomputed bound matches the
    stored one, and a stability reading (stable, narrowed, growing,
    appeared, removed). Refusals (drift over tolerance, transitive or
    spread-less offsets) never become transforms, so they never appear
    here: absence from this history is the record of their absence.
    """
    if not transform_id or not isinstance(transform_id, str):
        raise ValueError("drift history needs a transform id")
    chain = collection_history(project, "time_transforms", transform_id)
    if not chain:
        return {"transform_id": transform_id, "found": False, "revisions": [],
                "measurement": None, "decomposition": None, "stability": "absent"}
    live = next((entry["row"] for entry in reversed(chain)
                 if entry.get("row") is not None), None)
    if live is None:
        return {"transform_id": transform_id, "found": False, "revisions": [],
                "measurement": None, "decomposition": None,
                "stability": "absent"}
    reader = m6_reader or _default_m6_reader
    measurement = reader(Path(project), live.get("from_clock"),
                         live.get("to_clock"))
    direct = measurement.get("direct_measurement") if isinstance(
        measurement, dict) else None
    decomposition = None
    if isinstance(direct, dict):
        try:
            p5 = float(direct["p5_seconds"])
            p95 = float(direct["p95_seconds"])
            drift = abs(float(direct.get("drift_seconds") or 0.0))
            recomputed = _uncertainty_from_spread(p5, p95, drift)
            stored = [(segment.get("uncertainty_ticks"), segment.get("offset_ticks"))
                      for segment in live.get("segments", [])]
            decomposition = {
                "spread_seconds": round(p95 - p5, 6),
                "drift_seconds": drift,
                "drift_exceeds_tolerance": direct.get("drift_exceeds_tolerance"),
                "recomputed_uncertainty_ticks": recomputed,
                "stored_segments": [
                    {"uncertainty_ticks": uncertainty, "offset_ticks": offset}
                    for uncertainty, offset in stored],
                "bound_matches_migration": all(
                    uncertainty == recomputed for uncertainty, _ in stored),
            }
        except (KeyError, TypeError, ValueError):
            decomposition = None
    bounds = [entry["row"].get("segments", [{}])[0].get("uncertainty_ticks")
              if entry["change"] != "removed" else None
              for entry in chain]
    if chain[-1]["change"] == "removed":
        stability = "removed"
    elif len(chain) == 1:
        stability = "appeared"
    elif any(bound is None for bound in bounds):
        stability = "unknown"
    elif all(bound == bounds[0] for bound in bounds):
        stability = "stable"
    elif bounds[-1] < bounds[0]:
        stability = "narrowed"
    else:
        stability = "growing"
    return {"transform_id": transform_id, "found": True,
            "from_clock": live.get("from_clock"),
            "to_clock": live.get("to_clock"),
            "provenance_id": live.get("provenance_id"),
            "revisions": [{key: entry[key] for key in
                           ("revision", "event_hash", "author", "reason", "change")}
                          for entry in chain],
            "uncertainty_ticks_by_revision": bounds,
            "measurement": ({"p5_seconds": direct.get("p5_seconds"),
                             "p95_seconds": direct.get("p95_seconds"),
                             "drift_seconds": direct.get("drift_seconds"),
                             "drift_exceeds_tolerance": direct.get(
                                 "drift_exceeds_tolerance")}
                            if isinstance(direct, dict) else None),
            "decomposition": decomposition, "stability": stability}
