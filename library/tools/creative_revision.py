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
STATUSES = frozenset({"prepared", "recorded", "verified", "promoted",
                      "approved", "rejected"})


class RevisionConflict(ValueError):
    """An existing revision id was reused with incompatible facts."""


class StaleRevision(RevisionConflict):
    """An update was based on an older revision version."""


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
                          "promoted": 3, "approved": 4, "rejected": 4}
        for revision_id, incoming in doc.get("revisions", {}).items():
            existing = revisions.get(revision_id)
            if existing is None:
                created = dict(incoming)
                created["version"] = 1
                created.pop("_expected_version", None)
                revisions[revision_id] = created
                continue
            actual = int(existing.get("version", 1))
            expected = incoming.get("_expected_version", actual)
            if expected != actual:
                raise StaleRevision(
                    f"revision {revision_id} changed from version {expected} "
                    f"to {actual}; reload it before updating")
            if (existing.get("status") == "approved"
                    and incoming.get("status") == "rejected"
                    and not incoming.get("revocations")):
                raise RevisionConflict(
                    f"revision {revision_id} is approved; use revoke() "
                    "with an explicit revocation decision")
            merged = dict(existing)
            for key, value in incoming.items():
                if key not in {"refs", "affected_entities", "status",
                               "_expected_version"} and value is not None:
                    merged[key] = value
            merged["refs"] = {**dict(existing.get("refs") or {}),
                              **dict(incoming.get("refs") or {})}
            merged["affected_entities"] = sorted(
                set(existing.get("affected_entities") or ()) |
                set(incoming.get("affected_entities") or ()))
            old_status = existing.get("status", "prepared")
            new_status = incoming.get("status", "prepared")
            new_rank = lifecycle_rank.get(new_status, 0)
            old_rank = lifecycle_rank.get(old_status, 0)
            # A later explicit approval/rejection is the current review
            # decision when both statuses are terminal review outcomes.
            merged["status"] = (
                new_status if new_rank > old_rank or
                (new_rank == old_rank == 3 and
                 str(incoming.get("updated_at") or "") >=
                 str(existing.get("updated_at") or "")) else old_status)
            merged["version"] = actual + 1
            revisions[revision_id] = merged
        current["format"] = FORMAT
        from library.tools.versions import store
        store.write_record(path, current, prefix=".creative-revisions-")


def _stamp() -> str:
    # Review outcome updates can be close together, so preserve enough
    # ordering precision for the canonical record's last-writer decision.
    return datetime.now(UTC).isoformat(timespec="microseconds")


def create(project: os.PathLike | str, *, revision_id: str | None = None,
           parents=(), author: str | None = None, intent: str = "",
           operations=(), base_generation: int | None = None,
           result_generation: int | None = None,
           project_model_revision: int | None = None,
           git_revision: str | None = None, affected_entities=(),
           verification: Mapping | None = None, recovery: Mapping | None = None,
           approval: Mapping | None = None, refs: Mapping | None = None,
           status: str = "prepared", expected_version: int | None = None,
           revocations=()) -> dict[str, Any]:
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
    if existing and expected_version is not None and int(
            existing.get("version", 1)) != expected_version:
        retry_matches = all(
            (existing.get("refs") or {}).get(key) == value
            for key, value in (refs or {}).items())
        retry_matches = retry_matches and {
            str(item) for item in affected_entities if item}.issubset(
                set(existing.get("affected_entities") or ()))
        retry_matches = retry_matches and (
            status == existing.get("status") or
            (status == "prepared" and existing.get("status") != "prepared"))
        for key, value in (("result_generation", result_generation),
                           ("project_model_revision", project_model_revision),
                           ("git_revision", git_revision),
                           ("verification", verification),
                           ("recovery", recovery), ("approval", approval)):
            if value is not None and existing.get(key) != value:
                retry_matches = False
        for key, value in (("parents", [str(parent) for parent in parents if parent]),
                           ("author", author), ("intent", intent),
                           ("operations", [dict(op) for op in operations
                                           if isinstance(op, Mapping)]),
                           ("base_generation", base_generation)):
            if value not in (None, [], "") and existing.get(key) != value:
                retry_matches = False
        if revocations and list(existing.get("revocations") or ()) != list(revocations):
            retry_matches = False
        if retry_matches:
            return dict(existing)
        raise StaleRevision(
            f"revision {rid} is version {existing.get('version', 1)}, "
            f"not expected version {expected_version}")
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
    if not existing:
        # The lock must refuse if another writer creates this same identity
        # after our initial read instead of treating it as an ordinary update.
        row["_expected_version"] = 0
    if existing:
        # Identity-defining facts cannot silently drift on retries.
        for key in ("parents", "intent", "operations", "base_generation"):
            if existing.get(key) not in (None, [], "") and row[key] not in (None, [], "") \
                    and existing[key] != row[key]:
                raise RevisionConflict(f"revision {rid} conflicts on {key}")
        if author is not None and existing.get("author") not in (None, author):
            raise RevisionConflict(f"revision {rid} conflicts on author")
        row = dict(existing)
        row["_expected_version"] = int(existing.get("version", 1))
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
        if revocations:
            row["revocations"] = list(revocations)
        # prepared never implies approved; only an explicit approval does.
        row["status"] = status if status != "prepared" or row.get("status") == "prepared" else row["status"]
        row["updated_at"] = _stamp()
    revisions[rid] = row
    _write(project, doc)
    return get(project, rid) or row


def revoke(project: os.PathLike | str, revision_id: str, *,
           expected_version: int, decision: Mapping) -> dict[str, Any]:
    """Record an explicit, retryable revocation of an approved revision."""
    if not isinstance(decision, Mapping) or not decision:
        raise ValueError("revocation requires an explicit decision record")
    row = get(project, revision_id)
    if row is None:
        raise KeyError(f"unknown creative revision {revision_id!r}")
    event = {"decision": dict(decision), "event_id": str(
        decision.get("event_id") or uuid.uuid5(
            uuid.NAMESPACE_URL, json.dumps(dict(decision), sort_keys=True))) }
    if row.get("status") == "rejected" and event in (row.get("revocations") or ()):
        return row
    if row.get("status") != "approved":
        raise RevisionConflict("only an approved revision can be revoked")
    if int(row.get("version", 1)) != expected_version:
        raise StaleRevision(
            f"revision {revision_id} is version {row.get('version', 1)}, "
            f"not expected version {expected_version}")
    events = list(row.get("revocations") or ())
    if event not in events:
        events.append(event)
    return create(
        project, revision_id=revision_id, expected_version=expected_version,
        parents=row.get("parents") or (), author=row.get("author"),
        intent=row.get("intent") or "", operations=row.get("operations") or (),
        base_generation=row.get("base_generation"),
        result_generation=row.get("result_generation"),
        project_model_revision=row.get("project_model_revision"),
        git_revision=row.get("git_revision"),
        affected_entities=row.get("affected_entities") or (),
        verification=row.get("verification"), recovery=row.get("recovery"),
        approval=row.get("approval"), refs=row.get("refs") or {},
        status="rejected", revocations=events)


def get(project: os.PathLike | str, revision_id: str) -> dict[str, Any] | None:
    row = read(project)["revisions"].get(str(revision_id))
    if row:
        return dict(row)
    root = Path(project)
    if not (root / ".git").exists():
        return None
    from library.tools.versions import store

    refs = store.git(str(root), "for-each-ref", "--format=%(refname)",
                     "refs/heads")
    if refs.returncode != 0:
        return None
    for ref in refs.stdout.splitlines():
        blob = store.git(
            str(root), "show", f"{ref}:pipeline_output/review/{FILENAME}")
        if blob.returncode != 0:
            continue
        try:
            document = json.loads(blob.stdout)
        except (TypeError, ValueError):
            continue
        revisions = document.get("revisions", {}) if isinstance(document, dict) else {}
        if not isinstance(revisions, dict):
            continue
        found = revisions.get(str(revision_id))
        if found:
            return dict(found)
    return None


def list_revisions(project: os.PathLike | str) -> list[dict[str, Any]]:
    return sorted((dict(row) for row in read(project)["revisions"].values()),
                  key=lambda row: (row.get("created_at", ""), row["revision_id"]))


def _stable_project_identity(project: Path) -> str:
    """Read the project identity that survives moving its directory."""
    config_path = project / "project.yaml"
    if config_path.exists():
        try:
            from library.schemas.project_config import load_project_config
            config = load_project_config(
                config_path, strict=False, migrate_format=False)
            return f"{config.slug}\0{config.name}\0{config.created}"
        except (OSError, ValueError, TypeError):
            pass
    # Old projects without a readable declaration retain a deterministic
    # fallback; once a source row already carries an ID, reconcile preserves it.
    return project.name


def _legacy_revision_id(stable_project: str, source: str, key: str) -> str:
    seed = f"{stable_project}\0{source}\0{key}".encode()
    return str(uuid.uuid5(uuid.NAMESPACE_URL, hashlib.sha256(seed).hexdigest()))


def reconcile(project: os.PathLike | str, *, repair: bool = False) -> dict[str, Any]:
    """Check and repair the fast indexes without losing historical facts.

    Missing legacy identities are imported with deterministic ids derived
    from their authoritative source key. The source record is then linked
    back to that id; snapshots stay in their owner stores.
    """
    from library.tools import plan_provenance, reel_edit_history, undo_journal
    from library.tools.versions import reel_versions, store, variants

    project_path = Path(project)
    stable_project = _stable_project_identity(project_path)
    findings: list[dict[str, Any]] = []
    repaired = False
    seen_projection_ids: dict[tuple[str, str], str] = {}

    def legacy_id(source: str, key: str) -> str:
        # Existing projection IDs (including path-derived historical IDs) are
        # preferred by ensure(); only absent IDs use this portable namespace.
        return _legacy_revision_id(stable_project, source, key)

    def ensure(source: str, key: str, entity: str, existing_id: str | None,
               *, author: str, intent: str, operations=(), refs: Mapping | None = None,
               generation: int | None = None) -> str:
        nonlocal repaired
        rid = str(existing_id or legacy_id(source, key))
        claim = (source, rid)
        prior_key = seen_projection_ids.get(claim)
        if prior_key is not None and prior_key != key:
            findings.append({"source": source, "entity": entity,
                             "key": key, "revision_id": rid,
                             "issue": "duplicate_revision_reference",
                             "also_referenced_by": prior_key})
        else:
            seen_projection_ids[claim] = key
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
        else:
            canonical = get(project_path, rid)
            if canonical.get("revision_id") != rid:
                findings.append({"source": source, "entity": entity,
                                 "key": key, "revision_id": rid,
                                 "issue": "incompatible_revision_id"})
            source_ref = (canonical.get("refs") or {}).get(source)
            if source_ref and refs:
                mismatched = {name: value for name, value in refs.items()
                              if name in source_ref and source_ref[name] != value}
                if mismatched:
                    findings.append({"source": source, "entity": entity,
                                     "key": key, "revision_id": rid,
                                     "issue": "incompatible_revision_reference",
                                     "fields": sorted(mismatched)})
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
                     refs={"entry_id": key})
        if repair and not entry.get("revision_id"):
            entry["revision_id"] = rid
            undo_journal.write_entry(project_path, entry)

    # Validate references into the canonical event stream and project-scoped
    # Timeline Shadow mirror. Those stores own the event and observed state;
    # revisions own the creative linkage only.
    from library.tools import project_model_store
    from library.tools.versions import rounds

    events_path, _, _ = project_model_store._paths(project_path)
    event_head = 0
    if events_path.exists():
        event_head, _ = project_model_store.load_project_model(project_path)
    for revision in list_revisions(project_path):
        if revision.get("status") not in STATUSES:
            findings.append({"source": "creative_revision",
                             "entity": revision.get("revision_id"),
                             "key": revision.get("status"),
                             "issue": "invalid_status"})
        model_revision = revision.get("project_model_revision")
        if model_revision is not None and not 1 <= int(model_revision) <= event_head:
            findings.append({"source": "project_model", "entity": revision["revision_id"],
                             "key": model_revision,
                             "issue": "project_model_revision_missing"})

    shadow_path = review_dir / "timeline_shadow.json"
    if shadow_path.exists():
        shadow_doc = json.loads(shadow_path.read_text(encoding="utf-8"))
        shadow_generations: set[tuple[str, int]] = set()
        for row in shadow_doc.get("generations", ()):
            key = (str(row["timeline_id"]), int(row["generation"]))
            if key in shadow_generations:
                findings.append({"source": "timeline_shadow",
                                 "entity": key[0], "key": key[1],
                                 "issue": "duplicate_generation_reference"})
            shadow_generations.add(key)
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
