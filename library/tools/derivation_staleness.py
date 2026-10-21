"""Derivation-aware invalidation: what a revised observation touches.

The punch list's **B-13**: "If a detector result or identity link
changes, invalidate dependent interpretations without rebuilding
unrelated source measurements." The acceptance gate that follows it
is sharper: "If one piece of evidence is revised, dependent knowledge
is updated or marked stale **without corrupting unrelated data**."

**The edge is a citation, and the digest is what makes it checkable.**
An interpretation, an event and an identity link already cite the
observations they rest on (``evidence_ids``,
``supporting_observation_ids``). Those citations are the edge. What was
missing is the half that says whether the thing on the other end still
holds: an observation carries the digest of the derivation that produced
it (``payload.evidence_contract.derivation.derivation_digest``), so a
revision is a change of digest, and a dependant is stale exactly when
the digest it rests on moved.

**The registry is a projection, and it says so.** The Project Model is
canonical; this module writes ``derivation_state.json`` beside it, the
same way ``reel-projection.json`` sits beside the event log. It holds
the edges, the digest each cited observation had when the edge was
recorded, and the stale marks. It never edits a model row: a row in the
canonical model is changed by the model store, under its own revision
and event log, and nothing here writes beside it.

**Marking is not deleting.** A stale dependant keeps its row, its
citations and its history; it gains a mark that names the revised
observation and the reason. A reader that finds the mark knows the row
is out of date; a reader that does not look at the registry still reads
the canonical row and no more.

**Only what moved.** :func:`mark_stale` reports how many dependants it
marked and how many rows it left untouched, per collection, so "without
corrupting unrelated data" is a number and not a promise.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from library.tools import project_model_store
except ImportError:  # imported as `tools.*` from inside library/
    from tools import project_model_store

FORMAT = "derivation_state/1"
REGISTRY_FILE = "derivation_state.json"

DEPENDENT_COLLECTIONS = ("events", "interpretations", "identity_links")
"""The model collections whose rows are DERIVED from observations.

An event, an interpretation and an identity link all rest on
observations they cite. A signal or a track is a measurement, not a
reading, and is not derived from a reading of a measurement.
"""


# ── The digests ─────────────────────────────────────────────────────


def derivation_digest(observation: dict[str, Any]) -> str | None:
    """The digest of the derivation that produced one observation.

    Read from the observation's own evidence contract, where the
    migration stamps it. An observation with no derivation record has
    no digest, and that is reported as ``None``: absent is absent, and a
    digest invented here would make a revision look like a change in a
    basis nobody measured.
    """
    if not isinstance(observation, dict):
        return None
    payload = observation.get("payload")
    if not isinstance(payload, dict):
        return None
    contract = payload.get("evidence_contract")
    if not isinstance(contract, dict):
        contract = payload.get("evidence")
    if not isinstance(contract, dict):
        return None
    derivation = contract.get("derivation")
    if not isinstance(derivation, dict):
        return None
    digest = derivation.get("derivation_digest")
    if isinstance(digest, str) and digest:
        return digest
    return None


def row_digest(row: dict[str, Any]) -> str:
    """A content digest of one model row, for rows with no derivation."""
    return "sha256:" + hashlib.sha256(json.dumps(
        row, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def cited_observation_ids(row: dict[str, Any]) -> list[str]:
    """The rows one dependent row cites, in a stable order.

    An event and an interpretation cite through ``evidence_ids``; an
    identity link cites its anchor observation and the observations that
    support it. A contradicting observation is NOT support and is not
    cited here: a row does not depend on the evidence that argues
    against it.
    """
    if not isinstance(row, dict):
        return []
    cited: list[str] = []
    for key in ("evidence_ids", "supporting_observation_ids"):
        values = row.get(key)
        if isinstance(values, list):
            cited.extend(value for value in values
                         if isinstance(value, str) and value)
    anchor = row.get("observation_id")
    if isinstance(anchor, str) and anchor:
        cited.append(anchor)
    seen: set[str] = set()
    ordered = []
    for value in cited:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _citation_index(model: dict[str, Any]) -> tuple[
        set[str], dict[str, list[str]]]:
    """The model's observation ids, and each row's citations."""
    observations = {row["id"] for row in model.get("observations", [])
                    if isinstance(row, dict) and isinstance(row.get("id"), str)}
    citations: dict[str, list[str]] = {}
    for collection in DEPENDENT_COLLECTIONS:
        for row in model.get(collection, []) or []:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            citations[row["id"]] = cited_observation_ids(row)
    return observations, citations


def resolve_observations(model: dict[str, Any], row_id: str, *,
                         citations: dict[str, list[str]] | None = None,
                         observations: set[str] | None = None
                         ) -> list[str]:
    """The observations a row ultimately rests on.

    A row may cite another row - an interpretation cites the event it
    reads - so the edge is followed until it reaches observations. The
    walk is bounded, so a cycle in the citations cannot loop: a row
    that cites itself is a row that rests on nothing here.
    """
    known = set(observations) if observations is not None else set()
    index = citations if citations is not None else _citation_index(model)[1]
    found: list[str] = []
    seen_rows = {row_id}
    pending = list(index.get(row_id, []))
    while pending:
        current = pending.pop(0)
        if current in known:
            if current not in found:
                found.append(current)
            continue
        if current in seen_rows:
            continue
        seen_rows.add(current)
        pending.extend(index.get(current, []))
    return found


# ── The edges ───────────────────────────────────────────────────────


def build_edges(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The derivation edges of one model: dependant row -> cited rows.

    Each edge carries the digest each cited observation had NOW, so a
    later revision is a difference and not a guess. An observation with
    no derivation record contributes ``None``: the citation is still an
    edge, and the row that rests on it can still be marked stale by
    id.
    """
    observation_rows = {row["id"]: row for row in model.get("observations", [])
                        if isinstance(row, dict)
                        and isinstance(row.get("id"), str)}
    known = set(observation_rows)
    _known, citations = _citation_index(model)
    edges: dict[str, dict[str, Any]] = {}
    for collection in DEPENDENT_COLLECTIONS:
        for row in model.get(collection, []) or []:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            cited = resolve_observations(
                model, row["id"], citations=citations, observations=known)
            if not cited:
                continue
            edges[row["id"]] = {
                "collection": collection,
                "observation_ids": cited,
                "derivation_digests": {
                    observation_id: derivation_digest(
                        observation_rows[observation_id])
                    for observation_id in cited},
            }
    return edges


# ── The registry ────────────────────────────────────────────────────


def _registry_path(project: Path) -> Path:
    return Path(project) / project_model_store.STORE_DIR / REGISTRY_FILE


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


def read_registry(project: Path) -> dict[str, Any]:
    """The derivation registry of one project; absent is empty."""
    path = _registry_path(Path(project))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"format": FORMAT, "dependants": {}, "observations": {}}
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        return {"format": FORMAT, "dependants": {}, "observations": {}}
    payload.setdefault("dependants", {})
    payload.setdefault("observations", {})
    return payload


def write_registry(project: Path, registry: dict[str, Any]) -> None:
    """Write the derivation registry beside the model."""
    registry = dict(registry)
    registry["format"] = FORMAT
    registry["recorded_at"] = int(time.time())
    _atomic_write(_registry_path(Path(project)), (json.dumps(
        registry, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def _load_model(project: Path) -> dict[str, Any]:
    try:
        _revision, model = project_model_store.load_project_model(Path(project))
    except (FileNotFoundError, ValueError, KeyError, OSError) as exc:
        raise ValueError(
            f"this project has no Project Model to derive from: {exc}") from exc
    return model


def refresh(project: Path, model: dict[str, Any] | None = None) -> dict[str, Any]:
    """Re-read the model's edges, and clear the marks the derivers fixed.

    A stale mark records the digests the dependant rested on when it was
    marked. When the model now carries different digests for the same
    citations, the deriver has re-run and rewritten the row, so the
    mark is resolved rather than left standing on a row that is
    current. A mark whose digests still hold stays marked.
    """
    project = Path(project)
    model = model if model is not None else _load_model(project)
    registry = read_registry(project)
    edges = build_edges(model)
    resolved: list[str] = []
    for row_id, edge in edges.items():
        record = registry["dependants"].get(row_id)
        if not isinstance(record, dict) or not record.get("stale"):
            continue
        stale_digests = record.get("stale_digests") or {}
        live_digests = edge["derivation_digests"]
        if any(stale_digests.get(key) != live_digests.get(key)
               for key in set(stale_digests) | set(live_digests)):
            record["stale"] = False
            record["resolved_at"] = int(time.time())
            record["resolved_digests"] = live_digests
            resolved.append(row_id)
    registry["dependants"] = {
        row_id: {**edges.get(row_id, {}),
                 **({key: value for key, value in (registry["dependants"].get(row_id) or {}).items()
                     if key not in ("collection", "observation_ids", "derivation_digests")})}
        for row_id in set(edges) | set(registry["dependants"])}
    for record in registry["dependants"].values():
        # A dependant nobody marked is not stale: say so, so a reader
        # does not have to know that the key is absent.
        record.setdefault("stale", False)
    registry["observations"] = {
        row["id"]: {"derivation_digest": derivation_digest(row)}
        for row in model.get("observations", []) or []
        if isinstance(row, dict) and isinstance(row.get("id"), str)}
    write_registry(project, registry)
    return {"edges": len(edges), "resolved": sorted(resolved),
            "stale": sorted(row_id for row_id, record in registry["dependants"].items()
                            if record.get("stale"))}


def mark_stale(project: Path, revised_observation_ids: list[str], *,
               reason: str) -> dict[str, Any]:
    """Mark the dependants of a revised observation stale, and only those.

    A dependant is stale when it rests on an observation the caller
    named as revised, or when the digest it rests on has moved since
    the edge was recorded. Nothing else is marked, and no model row is
    written: the report names what was marked and what was left alone.
    """
    project = Path(project)
    if not revised_observation_ids:
        raise ValueError("marking stale needs the revised observation ids")
    if not reason or not reason.strip():
        raise ValueError("marking stale needs a reason")
    model = _load_model(project)
    registry = read_registry(project)
    edges = build_edges(model)
    observations = {row["id"]: row for row in model.get("observations", [])
                    if isinstance(row, dict) and isinstance(row.get("id"), str)}
    revised = [value for value in dict.fromkeys(revised_observation_ids)
               if isinstance(value, str) and value]
    unknown = [value for value in revised if value not in observations]
    if unknown:
        raise ValueError(f"the model has no observation {unknown[0]!r}")

    stale: list[dict[str, Any]] = []
    untouched: list[str] = []
    for row_id, edge in sorted(edges.items()):
        record = registry["dependants"].get(row_id)
        record = dict(record) if isinstance(record, dict) else {}
        cited = edge["observation_ids"]
        live_digests = edge["derivation_digests"]
        prior_digests = (record.get("derivation_digests")
                         if isinstance(record.get("derivation_digests"), dict)
                         else {}) or {}
        moved = [observation_id for observation_id in cited
                 if observation_id in revised
                 or prior_digests.get(observation_id) != live_digests.get(observation_id)]
        if not moved:
            untouched.append(row_id)
            continue
        record.update({
            "collection": edge["collection"],
            "observation_ids": cited,
            "derivation_digests": live_digests,
            "stale": True,
            "stale_reason": reason.strip(),
            "stale_since": int(time.time()),
            "stale_digests": {key: prior_digests.get(key) for key in live_digests},
            "stale_observations": moved,
        })
        registry["dependants"][row_id] = record
        stale.append({"id": row_id, "collection": edge["collection"],
                      "observation_ids": moved})
    registry["observations"] = {
        row["id"]: {"derivation_digest": derivation_digest(row)}
        for row in model.get("observations", []) or []
        if isinstance(row, dict) and isinstance(row.get("id"), str)}
    write_registry(project, registry)

    by_collection: dict[str, int] = {}
    for row in stale:
        by_collection[row["collection"]] = by_collection.get(row["collection"], 0) + 1
    model_rows = sum(len(model.get(collection, []) or [])
                     for collection in DEPENDENT_COLLECTIONS)
    return {
        "project": str(project),
        "revised_observation_ids": revised,
        "reason": reason.strip(),
        "stale_dependants": stale,
        "stale_count": len(stale),
        "stale_by_collection": by_collection,
        "untouched_dependants": len(untouched),
        "model_rows_untouched": model_rows - len(stale),
    }


def report(project: Path) -> dict[str, Any]:
    """What this project derives, and what of it is stale."""
    project = Path(project)
    registry = read_registry(project)
    dependants = registry.get("dependants") or {}
    stale = sorted(row_id for row_id, record in dependants.items()
                   if record.get("stale"))
    by_collection: dict[str, int] = {}
    for row_id in stale:
        collection = (dependants[row_id] or {}).get("collection") or "unknown"
        by_collection[collection] = by_collection.get(collection, 0) + 1
    return {
        "project": str(project),
        "format": registry.get("format", FORMAT),
        "dependants": len(dependants),
        "stale": stale,
        "stale_count": len(stale),
        "stale_by_collection": by_collection,
        "observations_with_digest": sum(
            1 for record in (registry.get("observations") or {}).values()
            if record.get("derivation_digest")),
    }


def main(argv=None) -> int:
    """``report``/``refresh``/``mark``: the derivation registry of a project."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    report_parser = sub.add_parser(
        "report", help="What this project derives, and what of it is stale")
    report_parser.add_argument("project")
    refresh_parser = sub.add_parser(
        "refresh", help="Re-read the model's edges and clear the marks the derivers fixed")
    refresh_parser.add_argument("project")
    mark_parser = sub.add_parser(
        "mark", help="Mark the dependants of a revised observation stale")
    mark_parser.add_argument("project")
    mark_parser.add_argument("--observation", action="append", required=True,
                             dest="observations",
                             help="the revised observation id; repeatable")
    mark_parser.add_argument("--reason", required=True,
                             help="why the observation was revised")
    args = parser.parse_args(argv)
    if args.command == "report":
        print(json.dumps(report(Path(args.project)), indent=2, sort_keys=True))
        return 0
    if args.command == "refresh":
        print(json.dumps(refresh(Path(args.project)), indent=2, sort_keys=True))
        return 0
    if args.command == "mark":
        print(json.dumps(mark_stale(Path(args.project), args.observations,
                                    reason=args.reason),
                         indent=2, sort_keys=True))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
