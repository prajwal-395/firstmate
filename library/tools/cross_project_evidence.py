"""Share the source intelligence another project measured.

The punch list's **VI-041**: "Support explicit opt-in sharing of reusable
footage knowledge between projects."

What the engine already has, and what this module connects:

- ``cross_project_policy.json`` in a project root, declaring a stable
  ``project_id`` and the projects it grants access to. Absent
  declarations mean private; malformed ones refuse the query.
- ``model_evidence_provenance.reuse_evidence``: the gate. It checks the
  mutual grant, the licence, the edition and the workload purpose,
  refuses unknown output terms, and appends every decision to an
  append-only audit.
- ``project_model_store.merge_evidence_records``: the compare-and-swap
  merge that puts rows into a project's canonical model.

What was missing: the middle. The gate had no way to be handed another
project's evidence, because nothing ever read it out of one.

**The opt-in is checked BEFORE the source is read.** A project that has
not granted access is not opened: the request returns without touching
the source's model, and the report says the grant is absent. A share
that is only audited after the fact is not an opt-in.

**The gate decides, this module does not.** Every model-derived row
passes through ``reuse_evidence``, which is the only thing that knows
what a licence permits for a given edition and purpose. This module
does not decide, does not soften, and does not have a second path
around it: a row the gate refuses is refused here, with the gate's own
reason.

**Deterministic measurements use a separate explicit import.** They now
live in project-private source-memory roots. This command imports the
deterministic slot allowlist only after mutual project consent; model-
derived observations continue through the licence gate and audit below.

**The Project Model import is a merge, not a copy.** Its rows go into the target's
canonical model by stable id, under a compare-and-swap against the
target's current revision. The target must already hold the asset and
the clock the rows sit on - the ids are derived from the content
digest, so a target with the same footage has them. A target without
them is told, rather than being given rows that would make its model
invalid.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

FORMAT = "cross_project_evidence/1"


class CrossProjectEvidenceError(ValueError):
    """A sharing request that cannot be answered as asked."""


def _policy_id(project: Path) -> str | None:
    """The project's own stable id, from its cross-project policy.

    A project with no policy has no stable id, and a project with no
    stable id cannot be granted anything: there is nothing to name the
    grant by.
    """
    from library.tools.project_graph.cross_project import _policy
    try:
        policy = _policy(project)
    except (OSError, ValueError, TypeError):
        return None
    return policy.get("project_id") if policy else None


def _mutual_grant(source: Path, target: Path, source_id: str,
                  target_id: str) -> bool:
    """Whether both projects grant each other, by the SAME policy check.

    The check is ``model_evidence_provenance``'s, so the graph query,
    the evidence acquisition and this share all agree on what a grant
    is. A second implementation of the same rule is a second answer.
    """
    from library.tools import model_evidence_provenance
    return bool(model_evidence_provenance._mutual_project_share(
        source, target, source_id, target_id))


def _source_observations(model: dict[str, Any],
                         digest: str | None) -> list[dict[str, Any]]:
    """The source's observations about the shared footage.

    A digest selects the footage by CONTENT identity: ``asset-{digest}``
    is the id every lane derives from the same bytes, so two projects
    cut from the same camera file name the same asset.
    """
    rows = []
    for row in model.get("observations", []):
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if digest and row.get("asset_id") != f"asset-{digest}":
            continue
        rows.append(row)
    return rows


def _model_provenance(row: dict[str, Any]) -> tuple[dict[str, Any] | None,
                                                     dict[str, Any] | None]:
    """The row's model provenance record and its producer, if it has them.

    The migrated rows carry both: ``payload.model_provenance`` is the
    generation record and ``payload.model_provenance_source`` names the
    project that produced it. A row without them has no licence record,
    and the gate refuses it.
    """
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return None, None
    record = payload.get("model_provenance")
    source = payload.get("model_provenance_source")
    if not isinstance(record, dict) or not isinstance(source, dict):
        return None, None
    if not record.get("evidence_ids"):
        return None, None
    return record, source


def share_evidence(source_project: str | Path, target_project: str | Path, *,
                   digest: str | None = None,
                   edition: str = "commercial",
                   purpose: str = "commercial_client",
                   expected_revision: int | None = None,
                   dry_run: bool = False,
                   registry: dict[str, Any] | None = None) -> dict[str, Any]:
    """Share the source project's measured source intelligence.

    Returns a report naming what was shared, what was refused and why,
    and the target's revision after the merge. A share that is refused
    outright says so and reads nothing.
    """
    source_root = Path(source_project).expanduser().resolve()
    target_root = Path(target_project).expanduser().resolve()
    if not source_root.is_dir() or not target_root.is_dir():
        raise CrossProjectEvidenceError(
            "both project roots must be directories")
    source_id = _policy_id(source_root)
    target_id = _policy_id(target_root)
    report: dict[str, Any] = {
        "format": FORMAT,
        "source_project": str(source_root),
        "target_project": str(target_root),
        "source_project_id": source_id,
        "target_project_id": target_id,
        "opt_in": "absent",
        "shared": [],
        "measurements_shared": {},
        "refused": [],
        "imported": False,
        "target_revision": None,
        "reason": None,
    }
    if not source_id or not target_id:
        report["reason"] = ("one of the projects has no cross_project_policy.json, "
                            "so there is no stable id to grant anything by")
        return report
    if not _mutual_grant(source_root, target_root, source_id, target_id):
        report["reason"] = ("the two projects do not grant each other "
                            "cross-project access")
        return report
    report["opt_in"] = "mutual"

    # Deterministic source measurements live in project-private stores.
    # Import them only after the same mutual grant checked above; model
    # outputs remain on the separate licence-aware path below.
    if not dry_run:
        from library.tools import source_memory
        source_fingerprints = source_memory.load_recorded_fingerprints(
            str(source_root))
        digests = ([digest] if digest else sorted({
            str(row["content_digest"])
            for row in source_fingerprints.values()
            if isinstance(row, dict) and row.get("content_digest")}))
        for source_digest in digests:
            try:
                imported = source_memory.import_deterministic_measurements(
                    source_root, target_root, source_digest)
            except (OSError, ValueError, TypeError) as exc:
                report["refused"].append({
                    "content_digest": source_digest,
                    "reason": f"deterministic measurement import refused: {exc}"})
                continue
            if imported:
                report["measurements_shared"][source_digest] = imported

    from library.tools import project_model_store
    from library.tools import model_evidence_provenance
    from library.tools import model_registry

    try:
        _source_revision, source_model = project_model_store.load_project_model(
            source_root)
    except (FileNotFoundError, ValueError, KeyError):
        report["reason"] = "the source project has no Project Model to share"
        return report
    try:
        target_revision, target_model = project_model_store.load_project_model(
            target_root)
    except (FileNotFoundError, ValueError, KeyError):
        report["reason"] = "the target project has no Project Model to share into"
        return report

    rows = _source_observations(source_model, digest)
    report["candidate_count"] = len(rows)
    target_clocks = {row.get("id") for row in target_model.get("clocks", [])}
    target_assets = {row.get("id") for row in target_model.get("assets", [])}
    registry = registry if registry is not None else model_registry.load_registry()
    audit_path = (target_root / project_model_store.STORE_DIR /
                  model_evidence_provenance.AUDIT_FILE)
    shared: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = list(report["refused"])
    for row in rows:
        record, producer = _model_provenance(row)
        if record is None or producer is None:
            refused.append({"evidence_id": row.get("id"),
                            "reason": "the row carries no model provenance, so "
                                      "no licence record governs it"})
            continue
        if producer.get("project_id") != source_id:
            refused.append({"evidence_id": row.get("id"),
                            "reason": "the row was produced by another project, "
                                      "so it is not this project's to share"})
            continue
        try:
            decision = model_evidence_provenance.reuse_evidence(
                [{"id": row["id"]}], [record],
                source_project_id=source_id, target_project_id=target_id,
                edition=edition, purpose=purpose,
                source_project_root=source_root,
                target_project_root=target_root,
                audit_path=audit_path, registry=registry)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            refused.append({"evidence_id": row.get("id"),
                            "reason": f"the reuse gate could not decide: {exc}"})
            continue
        if not decision.get("reused"):
            for entry in decision.get("refused", []):
                refused.append({"evidence_id": entry.get("evidence_id"),
                                "reason": entry.get("reason")})
            continue
        clock_id = (row.get("interval") or {}).get("clock_id")
        if clock_id and target_clocks and clock_id not in target_clocks:
            refused.append({"evidence_id": row.get("id"),
                            "reason": "the target project does not hold the "
                                      "clock this row sits on, so importing it "
                                      "would make the target's model invalid"})
            continue
        asset_id = row.get("asset_id")
        if asset_id and target_assets and asset_id not in target_assets:
            refused.append({"evidence_id": row.get("id"),
                            "reason": "the target project does not hold the "
                                      "asset this row belongs to"})
            continue
        shared.append(row)

    report["shared"] = [row["id"] for row in shared]
    report["refused"] = refused
    report["audit"] = str(audit_path)
    if not shared:
        report["reason"] = report["reason"] or "nothing was shareable"
        return report
    if dry_run:
        report["reason"] = "dry run: nothing was imported"
        return report
    if expected_revision is None:
        expected_revision = target_revision
    try:
        revision = project_model_store.merge_evidence_records(
            target_root, observations=shared, expected_revision=expected_revision,
            author="ren", reason=("evidence shared by project "
                                  f"{source_id} under a mutual opt-in"))
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        report["reason"] = f"the target's model refused the merge: {exc}"
        return report
    report["imported"] = True
    report["target_revision"] = revision
    return report


def main(argv=None) -> int:
    """``share <source> <target>``: what one project may share with another."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    share = sub.add_parser(
        "share", help="Share the source intelligence another project measured")
    share.add_argument("target_project",
                       help="the project the knowledge is shared INTO")
    share.add_argument("--from", dest="source_project", required=True,
                       help="the project the knowledge comes from; both "
                            "projects must grant each other in their "
                            "cross_project_policy.json")
    share.add_argument("--digest", default=None,
                       help="share only the footage with this content digest")
    share.add_argument("--edition", default="commercial",
                       choices=("commercial", "personal"))
    share.add_argument("--purpose", default="commercial_client",
                       choices=("commercial_client", "personal_noncommercial",
                                "permitted_research"))
    share.add_argument("--dry-run", action="store_true",
                       help="decide and report; import nothing")
    share.add_argument("--json", action="store_true",
                       help="accepted for the front door; the answer is "
                            "always JSON")
    args = parser.parse_args(argv)
    if args.command == "share":
        report = share_evidence(
            args.source_project, args.target_project, digest=args.digest,
            edition=args.edition, purpose=args.purpose, dry_run=args.dry_run)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
