"""Canonical, compact identity for one creative timeline revision.

The revision ledger owns creative revision identity and lifecycle facts.
Timeline snapshots, edit programs, receipts, archives, Project Model events,
and version rows remain in their existing stores and are linked here by ref.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORMAT = "creative_revisions/1"
FILENAME = "creative_revisions.json"
STATUSES = frozenset({"prepared", "recorded", "verified", "approved", "rejected"})


class RevisionConflict(ValueError):
    """An existing revision id was reused with incompatible facts."""


def path_for(project: os.PathLike | str) -> Path:
    return Path(project) / "pipeline_output" / "review" / FILENAME


def read(project: os.PathLike | str) -> dict[str, Any]:
    path = path_for(project)
    if not path.exists():
        return {"format": FORMAT, "revisions": {}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("revisions"), dict):
        raise TypeError(f"{path} is not a {FORMAT} document")
    return doc


def _write(project: os.PathLike | str, doc: Mapping[str, Any]) -> None:
    from library.tools.project_file_lock import lock_project_file

    path = path_for(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock_project_file(path):
        # Re-read under the lock and merge to avoid dropping another writer's
        # newly appended revisions.
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            current = {"format": FORMAT, "revisions": {}}
        revisions = current.setdefault("revisions", {})
        lifecycle_rank = {"prepared": 0, "recorded": 1, "verified": 2,
                          "approved": 3, "rejected": 3}
        for revision_id, incoming in doc.get("revisions", {}).items():
            existing = revisions.get(revision_id)
            if existing is None:
                revisions[revision_id] = incoming
                continue
            merged = dict(existing)
            for key, value in incoming.items():
                if key not in {"refs", "affected_entities", "status"} and value is not None:
                    merged[key] = value
            merged["refs"] = {**dict(existing.get("refs") or {}),
                              **dict(incoming.get("refs") or {})}
            merged["affected_entities"] = sorted(
                set(existing.get("affected_entities") or ()) |
                set(incoming.get("affected_entities") or ()))
            old_status = existing.get("status", "prepared")
            new_status = incoming.get("status", "prepared")
            merged["status"] = (
                new_status if lifecycle_rank.get(new_status, 0) >
                lifecycle_rank.get(old_status, 0) else old_status)
            revisions[revision_id] = merged
        current["format"] = FORMAT
        from library.tools.versions import store
        store.write_record(path, current, prefix=".creative-revisions-")


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def create(project: os.PathLike | str, *, revision_id: str | None = None,
           parents=(), author: str | None = None, intent: str = "",
           operations=(), base_generation: int | None = None,
           result_generation: int | None = None,
           project_model_revision: int | None = None,
           git_revision: str | None = None, affected_entities=(),
           verification: Mapping | None = None, recovery: Mapping | None = None,
           approval: Mapping | None = None, refs: Mapping | None = None,
           status: str = "prepared") -> dict[str, Any]:
    """Create a revision, or idempotently enrich its refs/lifecycle facts.

    Payloads are references or compact semantic operations; timeline snapshots
    belong in their existing stores and must never be copied into this ledger.
    """
    if status not in STATUSES:
        raise ValueError(f"unsupported creative revision status {status!r}")
    rid = str(revision_id or uuid.uuid4())
    doc = read(project)
    revisions = doc["revisions"]
    existing = revisions.get(rid)
    row = {
        "revision_id": rid,
        "parents": [str(parent) for parent in parents if parent],
        "author": str(author or "ren"), "intent": str(intent or ""),
        "operations": [dict(op) for op in operations if isinstance(op, Mapping)],
        "base_generation": base_generation,
        "result_generation": result_generation,
        "project_model_revision": project_model_revision,
        "git_revision": git_revision,
        "affected_entities": sorted({str(item) for item in affected_entities if item}),
        "verification": dict(verification) if verification is not None else None,
        "recovery": dict(recovery) if recovery is not None else None,
        "approval": dict(approval) if approval is not None else None,
        "refs": dict(refs or {}),
        "status": status,
        "created_at": _stamp(),
        "updated_at": _stamp(),
    }
    if existing:
        # Identity-defining facts cannot silently drift on retries.
        for key in ("parents", "intent", "operations", "base_generation"):
            if existing.get(key) not in (None, [], "") and row[key] not in (None, [], "") \
                    and existing[key] != row[key]:
                raise RevisionConflict(f"revision {rid} conflicts on {key}")
        if author is not None and existing.get("author") not in (None, author):
            raise RevisionConflict(f"revision {rid} conflicts on author")
        row = dict(existing)
        for key, value in {
            "result_generation": result_generation,
            "project_model_revision": project_model_revision,
            "git_revision": git_revision,
            "verification": dict(verification) if verification is not None else None,
            "recovery": dict(recovery) if recovery is not None else None,
            "approval": dict(approval) if approval is not None else None,
        }.items():
            if value is not None:
                row[key] = value
        row["affected_entities"] = sorted(set(row.get("affected_entities", ())) |
                                          set(row["affected_entities"]))
        merged_refs = dict(row.get("refs") or {})
        merged_refs.update(dict(refs or {}))
        row["refs"] = merged_refs
        # prepared never implies approved; only an explicit approval does.
        row["status"] = status if status != "prepared" or row.get("status") == "prepared" else row["status"]
        row["updated_at"] = _stamp()
    revisions[rid] = row
    _write(project, doc)
    return row


def get(project: os.PathLike | str, revision_id: str) -> dict[str, Any] | None:
    row = read(project)["revisions"].get(str(revision_id))
    return dict(row) if row else None


def list_revisions(project: os.PathLike | str) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in read(project)["revisions"].values()),
                  key=lambda row: (row.get("created_at", ""), row["revision_id"]))


def reconcile(project: os.PathLike | str, *, repair: bool = False) -> dict[str, Any]:
    """Check and repair the fast indexes without losing historical facts.

    Missing legacy identities are imported with deterministic ids derived
    from their authoritative source key. The source record is then linked
    back to that id; snapshots stay in their owner stores.
    """
    from library.tools import plan_provenance, reel_edit_history, undo_journal
    from library.tools.versions import reel_versions, store, variants

    project_path = Path(project)
    findings: list[dict[str, Any]] = []
    repaired = False

    def legacy_id(source: str, key: str) -> str:
        seed = f"{project_path.resolve()}\0{source}\0{key}".encode()
        return str(uuid.uuid5(uuid.NAMESPACE_URL, hashlib.sha256(seed).hexdigest()))

    def ensure(source: str, key: str, entity: str, existing_id: str | None,
               *, author: str, intent: str, operations=(), refs: Mapping | None = None,
               generation: int | None = None) -> str:
        nonlocal repaired
        rid = str(existing_id or legacy_id(source, key))
        if rid not in read(project_path)["revisions"]:
            findings.append({"source": source, "entity": entity,
                             "key": key, "revision_id": rid,
                             "issue": "canonical_record_missing"})
            if repair:
                create(project_path, revision_id=rid, author=author,
                       intent=intent, operations=operations,
                       result_generation=generation,
                       affected_entities=[entity] if entity else (),
                       refs={source: dict(refs or {"key": key})}, status="recorded")
                repaired = True
        if not existing_id:
            findings.append({"source": source, "entity": entity,
                             "key": key, "revision_id": rid,
                             "issue": "projection_reference_missing"})
            repaired = repaired or repair
        return rid

    versions_doc = reel_versions.read(project_path)
    versions_changed = False
    for final, entries in versions_doc.get("reels", {}).items():
        for entry in entries:
            key = f"{final}:{entry['version']}"
            rid = ensure("reel_version", key, str(final), entry.get("revision_id"),
                         author="legacy", intent=f"{entry['kind']} version",
                         refs={"final": str(final), "version": entry["version"]})
            if repair and not entry.get("revision_id"):
                entry["revision_id"] = rid
                versions_changed = True
    if versions_changed:
        reel_versions._write(project_path, versions_doc)

    review_dir = project_path / "pipeline_output" / "review"
    provenance = plan_provenance.read_provenance(str(review_dir)) or {}
    history = provenance.get("reel_edit_history") or {}
    history_changed = False
    for final, entries in history.items():
        for entry in entries or ():
            key = str(entry.get("id") or f"{final}:{entry.get('at')}:{entry.get('act')}")
            rid = ensure("reel_edit_history", key, str(final), entry.get("revision_id"),
                         author=str(entry.get("actor") or "legacy"),
                         intent=str(entry.get("summary") or entry.get("act") or ""),
                         refs={"final": str(final), "entry_id": key})
            if repair and not entry.get("revision_id"):
                entry["revision_id"] = rid
                refs = dict(entry.get("refs") or {})
                refs["revision_id"] = rid
                entry["refs"] = refs
                history_changed = True
    if history_changed:
        ids = {str(entry.get("id")): entry.get("revision_id")
               for entries in history.values() for entry in entries or ()}

        def link_history(doc):
            table = doc.get("reel_edit_history") or {}
            for entries in table.values():
                for row in entries or ():
                    rid = ids.get(str(row.get("id")))
                    if rid:
                        row["revision_id"] = rid
                        refs = dict(row.get("refs") or {})
                        refs["revision_id"] = rid
                        row["refs"] = refs

        reel_edit_history._mutate(str(review_dir), link_history)

    for commit in undo_journal.list_commits(project_path):
        key = str(commit["id"])
        rid = ensure("creative_commit", key, str(commit.get("timeline") or ""),
                     commit.get("revision_id"), author=str(commit.get("agent") or "legacy"),
                     intent=str(commit.get("intent") or ""),
                     operations=commit.get("changes") or (),
                     refs={"commit_id": key})
        if repair and not commit.get("revision_id"):
            commit["revision_id"] = rid
            store.write_record(
                undo_journal._commit_path(project_path, key), commit,
                prefix=".commit-")

    for entry in undo_journal.list_entries(project_path):
        key = str(entry["id"])
        rid = ensure("undo_journal", key, str(entry.get("final") or ""),
                     entry.get("revision_id"), author="legacy",
                     intent=str((entry.get("accepted_edit") or {}).get("intent") or
                                (entry.get("spec") or {}).get("note") or "touch-up"),
                     operations=(entry.get("spec") or {}).get("edits") or (),
                     refs={"entry_id": key, "status": entry.get("status")})
        if repair and not entry.get("revision_id"):
            entry["revision_id"] = rid
            undo_journal.write_entry(project_path, entry)

    # Validate references into the canonical event stream and project-scoped
    # Timeline Shadow mirror. Those stores own the event and observed state;
    # revisions own the creative linkage only.
    from library.tools import project_model_store
    from library.tools.versions import rounds

    events_path, _, _ = project_model_store._paths(project_path)
    if events_path.exists():
        event_head, _ = project_model_store.load_project_model(project_path)
        for revision in list_revisions(project_path):
            model_revision = revision.get("project_model_revision")
            if model_revision is not None and not 1 <= int(model_revision) <= event_head:
                findings.append({"source": "project_model", "entity": revision["revision_id"],
                                 "key": model_revision,
                                 "issue": "project_model_revision_missing"})

    shadow_path = review_dir / "timeline_shadow.json"
    if shadow_path.exists():
        shadow_doc = json.loads(shadow_path.read_text(encoding="utf-8"))
        shadow_generations = {
            (str(row["timeline_id"]), int(row["generation"]))
            for row in shadow_doc.get("generations", ())
        }
        for revision in list_revisions(project_path):
            ref = revision.get("refs", {}).get("timeline_shadow") or {}
            key = (str(ref.get("timeline_id") or ""), int(ref.get("generation") or 0))
            if ref and key not in shadow_generations:
                findings.append({"source": "timeline_shadow",
                                 "entity": revision["revision_id"],
                                 "key": key, "issue": "generation_missing"})

    round_numbers = {int(row["round"]) for row in
                     rounds.read_rounds(project_path).get("rounds") or ()}
    for revision in list_revisions(project_path):
        round_ref = revision.get("refs", {}).get("round") or {}
        round_number = round_ref.get("round")
        if round_number is not None and int(round_number) not in round_numbers:
            findings.append({"source": "rounds", "entity": revision["revision_id"],
                             "key": round_number, "issue": "round_missing"})
    variant_specs = variants.read_variant_specs(str(project_path)).get("variants") or {}
    variant_declaration_count = sum(len(rows or ()) for rows in variant_specs.values())

    return {"ok": not findings, "missing_references": findings,
            "revision_count": len(read(project_path)["revisions"]),
            "repaired": repaired,
            "checked_surfaces": ["reel_versions", "reel_edit_history",
                                 "creative_commits", "undo_journal",
                                 "project_model_events", "timeline_shadow",
                                 "rounds", "variant_declarations"],
            "variant_declaration_count": variant_declaration_count}
