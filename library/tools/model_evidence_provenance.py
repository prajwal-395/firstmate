"""Per-evidence model provenance and reuse gate.

The model registry answers whether an implementation may run for an
edition and workload purpose. This module records which implementation
    actually produced each evidence item, the checkpoint and license verdict
    used at generation time, and the registry's output-reuse classification.
Unknown output terms fail closed for reuse. This metadata is
kept beside evidence rather than in its payload, so provenance never
changes the evidence's factual certainty or confidence.

Reuse decisions are append-only audited. Cross-project reuse requires
the caller to pass the result of the existing mutual opt-in policy check;
this module never grants sharing by default. Restricted evidence may be
recomputed only through a caller-provided model runner, which must return
new evidence plus provenance that passes the same target-workload gate.
The caller owns scheduling any model run.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from library.tools import evidence_license, model_registry, project_model_store
except ImportError:  # imported as `tools.*` from inside library/
    from tools import evidence_license, model_registry, project_model_store

FORMAT = "model_evidence_provenance/1"
PROVENANCE_FILE = "model_evidence_provenance.jsonl"
AUDIT_FILE = "model_evidence_reuse.jsonl"
PURPOSES = frozenset(model_registry.PURPOSES)


def record_for_generation(
    evidence_ids: list[str],
    implementation_id: str,
    workload_purpose: str,
    *,
    registry: dict[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a provenance record for evidence from one exact implementation.

    Output-reuse terms are copied from the implementation's structured
    registry classification, whose evidence reference is preserved.
    """
    if not evidence_ids or any(not isinstance(value, str) or not value
                               for value in evidence_ids):
        raise ValueError("model evidence provenance needs non-empty evidence ids")
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("model evidence provenance ids must be unique")
    if workload_purpose not in PURPOSES:
        raise ValueError(f"unknown workload purpose {workload_purpose!r}")
    reg = registry or model_registry.load_registry()
    verdict = evidence_license.implementation_license(implementation_id, reg)
    implementation = next(row for row in reg["implementations"]
                          if row["id"] == implementation_id)
    edition = "commercial" if workload_purpose == "commercial_client" else "personal"
    if not evidence_license.authorized_for(implementation_id, edition,
                                           workload_purpose, reg):
        raise ValueError("producing implementation is not eligible for workload")
    terms = output_reuse_terms(implementation_id, reg)
    checkpoint = verdict["checkpoint"].get(
        "checkpoint", verdict["checkpoint"])
    license_record = implementation["license_evidence"]
    return {
        "format": FORMAT,
        "evidence_ids": list(evidence_ids),
        "implementation_id": implementation_id,
        "component_id": verdict["component_id"],
        "checkpoint": checkpoint,
        "checkpoint_revision": (checkpoint.get("version")
                                or implementation.get("revision")),
        "license_context": {
            "upstream_url": license_record.get("upstream_url"),
            "exact_version": license_record.get("exact_version"),
            "retrieved_at": license_record.get("retrieved_at"),
            "evidence_source": license_record.get("evidence_source"),
            "license": license_record.get("license"),
            "approval_status": verdict["approval_status"],
            "obligations": verdict["obligations"],
        },
        "workload_purpose": workload_purpose,
        "output_reuse": terms,
        "generated_at": generated_at or _utc_now(),
    }


def output_reuse_terms(implementation_id: str,
                       registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return explicit output rights and their license evidence reference."""
    reg = registry or model_registry.load_registry()
    implementation = next(row for row in reg["implementations"]
                          if row["id"] == implementation_id)
    value = implementation["output_reuse"]
    cls = value["class"]
    if cls == "commercial":
        status, purposes = "permitted", sorted(PURPOSES)
    elif cls == "personal_noncommercial":
        status, purposes = "restricted", ["personal_noncommercial",
                                            "permitted_research"]
    else:
        status, purposes = "unknown", []
    return {"class": cls, "status": status,
            "permitted_purposes": purposes,
            "permitted_editions": list(value["permitted_editions"]),
            "reuse_scope": value["reuse_scope"],
            "license_evidence_ref": value["license_evidence_ref"],
            "review_required": value["review_required"],
            "review_note": value["review_note"]}


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _project_file(project: str | Path, name: str) -> Path:
    return Path(project) / project_model_store.STORE_DIR / name


def append_generation_record(project: str | Path,
                             record: dict[str, Any]) -> None:
    """Persist model provenance beside the project's canonical model."""
    if record.get("format") != FORMAT:
        raise ValueError("unsupported model provenance format")
    if not record.get("evidence_ids"):
        raise ValueError("model evidence provenance needs evidence ids")
    if record["output_reuse"]["reuse_scope"] == "benchmark_only":
        raise ValueError("benchmark-only model output cannot be stored as reusable evidence")
    _append_jsonl(_project_file(project, PROVENANCE_FILE), record)


def record_generation(project: str | Path, evidence_ids: list[str],
                      implementation_id: str, workload_purpose: str, *,
                      registry: dict[str, Any] | None = None,
                      generated_at: str | None = None) -> dict[str, Any]:
    """Create and persist one model provenance record."""
    record = record_for_generation(
        evidence_ids, implementation_id, workload_purpose,
        registry=registry, generated_at=generated_at)
    append_generation_record(project, record)
    return record


def read_generation_records(project: str | Path) -> list[dict[str, Any]]:
    """Read generation provenance in append order; absent storage is empty."""
    path = _project_file(project, PROVENANCE_FILE)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line_number, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"invalid model evidence provenance at {path}:{line_number}") from exc
        if not isinstance(record, dict) or record.get("format") != FORMAT:
            raise ValueError(
                f"unsupported model evidence provenance at {path}:{line_number}")
        records.append(record)
    return records


def _find_record(evidence_id: str,
                 records: list[dict[str, Any]]) -> dict[str, Any] | None:
    matches = [record for record in records
               if evidence_id in record.get("evidence_ids", [])]
    if len(matches) > 1:
        raise ValueError(f"evidence {evidence_id!r} has multiple model provenance records")
    return matches[0] if matches else None


def _decision(row: dict[str, Any], record: dict[str, Any] | None,
              edition: str, purpose: str,
              registry: dict[str, Any]) -> tuple[bool, str]:
    if record is None:
        return False, "model provenance is missing"
    if record.get("format") != FORMAT:
        return False, "unsupported model provenance format"
    if purpose not in PURPOSES:
        raise ValueError(f"unknown workload purpose {purpose!r}")
    implementation_id = record.get("implementation_id")
    try:
        implementation_license = evidence_license.implementation_license(
            implementation_id, registry)
    except (KeyError, TypeError):
        return False, "producing implementation is not registered"
    terms = record.get("output_reuse")
    if isinstance(terms, dict) and terms.get("reuse_scope") == "benchmark_only":
        return False, "research-only or unknown outputs are benchmark-only"
    if not evidence_license.authorized_for(implementation_id, edition,
                                           purpose, registry):
        return False, "producing implementation is not eligible for target workload"
    if not isinstance(terms, dict):
        return False, "output reuse terms are missing"
    current_terms = output_reuse_terms(implementation_id, registry)
    if terms != current_terms:
        return False, "recorded output reuse terms are missing or stale"
    if terms["reuse_scope"] == "benchmark_only":
        return False, "research-only or unknown outputs are benchmark-only"
    if (edition not in terms["permitted_editions"]
            or purpose not in terms["permitted_purposes"]):
        return False, f"output reuse terms {terms['status']} do not permit {purpose}"
    context = record.get("license_context")
    implementation = next(row for row in registry["implementations"]
                          if row["id"] == implementation_id)
    expected_checkpoint = implementation.get("checkpoint")
    if record.get("checkpoint") != expected_checkpoint:
        return False, "recorded checkpoint revision is missing or stale"
    checkpoint_revision = (expected_checkpoint.get("version")
                           or implementation.get("revision"))
    if (not isinstance(checkpoint_revision, str) or not checkpoint_revision
            or record.get("checkpoint_revision") != checkpoint_revision):
        return False, "recorded checkpoint revision is missing or stale"
    generation_purpose = record.get("workload_purpose")
    if generation_purpose not in PURPOSES:
        return False, "generation workload purpose is missing or invalid"
    license_record = implementation["license_evidence"]
    expected_context = {
        "upstream_url": license_record.get("upstream_url"),
        "exact_version": license_record.get("exact_version"),
        "retrieved_at": license_record.get("retrieved_at"),
        "evidence_source": license_record.get("evidence_source"),
        "license": license_record.get("license"),
        "approval_status": implementation_license["approval_status"],
        "obligations": implementation_license["obligations"],
    }
    if not isinstance(context, dict) or context != expected_context:
        return False, "recorded license verdict is missing or stale"
    if row.get("id") not in record.get("evidence_ids", []):
        return False, "evidence id is not linked to this model provenance"
    return True, "reuse permitted"


def check_reuse_record(evidence_id: str, record: dict[str, Any] | None, *,
                       edition: str, purpose: str,
                       registry: dict[str, Any] | None = None
                       ) -> tuple[bool, str]:
    """Check one stored provenance record against the current registry."""
    reg = registry or model_registry.load_registry()
    return _decision({"id": evidence_id}, record, edition, purpose, reg)


def _eligible_recompute_implementations(
    record: dict[str, Any] | None,
    edition: str,
    purpose: str,
    registry: dict[str, Any],
) -> list[str]:
    if record is None:
        return []
    original_id = record.get("implementation_id")
    try:
        original = next(row for row in registry["implementations"]
                        if row["id"] == original_id)
    except (KeyError, StopIteration):
        return []
    candidates = model_registry.manifest(edition, purpose, registry).get(
        "implementations", [])
    capability_set = set(original["capabilities"])
    return sorted({row["id"] for row in candidates
                   if capability_set.intersection(row["capabilities"])})


def _mutual_project_share(source_root: str | Path,
                          target_root: str | Path,
                          source_project_id: str,
                          target_project_id: str) -> bool:
    """Verify the same two-sided project policy used by graph queries."""
    if source_project_id == target_project_id:
        return False
    try:
        from library.tools.project_graph.cross_project import _policy
    except ImportError:  # imported as `tools.*` from inside library/
        from tools.project_graph.cross_project import _policy
    source = _policy(Path(source_root).expanduser().resolve())
    target = _policy(Path(target_root).expanduser().resolve())
    return bool(
        source and target
        and source["project_id"] == source_project_id
        and target["project_id"] == target_project_id
        and target_project_id in source["share_with"]
        and source_project_id in target["share_with"]
    )


def _append_audit(path: str | Path,
                  events: list[dict[str, Any]]) -> None:
    if not events:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _append_jsonl(target, *events)


def _append_jsonl(path: str | Path, *records: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(target, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            for record in records:
                stream.write(json.dumps(record, sort_keys=True,
                                        ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def reuse_evidence(
    rows: list[dict[str, Any]],
    provenance_records: list[dict[str, Any]],
    *,
    source_project_id: str,
    target_project_id: str,
    edition: str,
    purpose: str,
    source_project_root: str | Path,
    target_project_root: str | Path,
    audit_path: str | Path,
    registry: dict[str, Any] | None = None,
    recompute: Callable[[dict[str, Any], str, str],
                        tuple[dict[str, Any], dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Gate reuse; recompute denied evidence through eligible models if supplied.

    Cross-project use verifies the existing two-sided policy files by
    project root and stable id. Missing, malformed, or mismatched policies
    refuse before returning evidence. ``recompute`` receives the source row,
    eligible implementation id, and target purpose, and returns a new row
    plus its generation provenance. The returned row must pass the same
    target eligibility and output-rights checks.
    """
    if not source_project_id or not target_project_id:
        raise ValueError("source and target project ids are required")
    if edition not in {"commercial", "personal"}:
        raise ValueError(f"unknown edition {edition!r}")
    if purpose not in PURPOSES:
        raise ValueError(f"unknown workload purpose {purpose!r}")
    reg = registry or model_registry.load_registry()
    source_root = Path(source_project_root).expanduser().resolve()
    target_root = Path(target_project_root).expanduser().resolve()
    if not source_root.is_dir() or not target_root.is_dir():
        raise ValueError("source and target project roots must be directories")
    cross_project = source_root != target_root
    if not cross_project and source_project_id != target_project_id:
        raise ValueError("project ids disagree for the same project root")
    cross_project_authorized = (not cross_project or _mutual_project_share(
        source_root, target_root,
        source_project_id, target_project_id))
    audit: list[dict[str, Any]] = []
    reused, recomputed, refused = [], [], []

    for row in rows:
        evidence_id = row["id"]
        record = _find_record(evidence_id, provenance_records)
        allowed, reason = _decision(row, record, edition, purpose, reg)
        if cross_project and not cross_project_authorized:
            allowed, reason = False, "mutual cross-project opt-in is absent"
        action = "reused" if allowed else "refused"
        final_row = row
        final_record = record

        if allowed:
            reused.append(row)
        elif cross_project and not cross_project_authorized:
            refused.append({"evidence_id": evidence_id, "reason": reason})
        else:
            candidates = _eligible_recompute_implementations(
                record, edition, purpose, reg)
            replacement = None
            if recompute is not None:
                for candidate_id in candidates:
                    try:
                        candidate_row, candidate_record = recompute(
                            row, candidate_id, purpose)
                    except (OSError, RuntimeError, ValueError) as exc:
                        reason = f"recompute failed for {candidate_id}: {exc}"
                        continue
                    is_allowed, candidate_reason = _decision(
                        candidate_row, candidate_record, edition, purpose, reg)
                    if is_allowed:
                        replacement = (candidate_row, candidate_record,
                                       candidate_id)
                        break
                    reason = f"recomputed evidence refused: {candidate_reason}"
            if replacement is None:
                refused.append({"evidence_id": evidence_id,
                                "reason": reason,
                                "recompute_candidates": candidates})
            else:
                final_row, final_record, candidate_id = replacement
                recomputed.append(final_row)
                action = "recomputed"
                reason = f"recomputed using eligible implementation {candidate_id}"

        audit.append({
            "format": "model_evidence_reuse_audit/1",
            "at": _utc_now(),
            "source_project_id": source_project_id,
            "target_project_id": target_project_id,
            "evidence_id": evidence_id,
            "source_implementation_id": (record or {}).get("implementation_id"),
            "output_reuse_class": ((record or {}).get("output_reuse") or {}).get("class"),
            "license_evidence_ref": ((record or {}).get("output_reuse") or {}).get(
                "license_evidence_ref"),
            "target_implementation_id": (final_record or {}).get("implementation_id"),
            "target_edition": edition,
            "target_purpose": purpose,
            "action": action,
            "reason": reason,
        })

    _append_audit(audit_path, audit)
    return {"reused": reused, "recomputed": recomputed,
            "refused": refused, "audit": audit}
