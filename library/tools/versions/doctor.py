"""Read-only health report for a project's creative version history.

The report joins the existing version ledgers. It deliberately does not
repair, sync, reconcile, or otherwise write project or machine state.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True,
        text=True, encoding="utf-8")
    return result.stdout.strip()


def _tree_bytes(path: Path) -> int:
    total = 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    if not path.is_dir():
        return 0
    for base, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            try:
                total += (Path(base) / name).stat().st_size
            except OSError:
                continue
    return total


def _project_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    from library.tools.project_registry import get_project
    return Path(get_project(value).project_root).resolve()


def _git_status(root: Path) -> dict:
    try:
        branch = _git(root, "branch", "--show-current") or "(detached HEAD)"
        worktrees = _git(root, "worktree", "list", "--porcelain").splitlines()
        status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
        git_dir = Path(_git(root, "rev-parse", "--git-common-dir"))
        if not git_dir.is_absolute():
            git_dir = root / git_dir
        worktree_rows = [line[9:] for line in worktrees if line.startswith("worktree ")]
        declaration_prefixes = (
            "project.yaml", "brand.json", "external/declarations/",
            "context/", "profiles/", "pipeline_data.json",
            "pipeline_output/review/reel_variants.json",
            "pipeline_output/review/reel_proposals_v2.json",
        )
        declarations = [line for line in status.splitlines()
                        if any(line[3:].startswith(prefix)
                               for prefix in declaration_prefixes)]
        return {"available": True, "branch": branch,
                "worktrees": worktree_rows,
                "uncommitted": [line for line in status.splitlines() if line],
                "uncommitted_declarations": declarations,
                "git_bytes": _tree_bytes(git_dir)}
    except (OSError, subprocess.CalledProcessError) as exc:
        return {"available": False, "error": str(exc), "branch": None,
                "worktrees": [], "uncommitted": [],
                "uncommitted_declarations": [], "git_bytes": None}


def _revisions(root: Path) -> dict:
    from library.tools import creative_revision
    from library.tools.versions import reel_versions

    ledger = creative_revision.read(root)
    histories = reel_versions.read(root).get("reels", {})
    approved = {}
    for revision_id, revision in ledger.get("revisions", {}).items():
        if revision.get("status") != "approved":
            continue
        version_ref = (revision.get("refs") or {}).get("reel_version") or {}
        reel = str(version_ref.get("final") or "")
        version = version_ref.get("version")
        if reel and version is not None:
            prior = approved.get(reel)
            if prior is None or int(version) > int(prior["version"]):
                approved[reel] = {"version": version,
                                  "revision_id": revision_id}
    for reel, entries in histories.items():
        matching = [row for row in entries
                    if (row.get("approval") or {}).get("status") == "approved"
                    or row.get("revision_status") == "approved"]
        if matching:
            row = max(matching, key=lambda item: int(item["version"]))
            prior = approved.get(str(reel))
            if prior is None or int(row["version"]) > int(prior["version"]):
                approved[str(reel)] = {"version": row["version"],
                                       "revision_id": row.get("revision_id")}
    revisions = ledger.get("revisions", {})
    missing = []
    for revision_id, row in revisions.items():
        if row.get("status") == "approved":
            refs = row.get("refs") or {}
            if not refs.get("reel_version") and not refs.get("final"):
                missing.append({"revision_id": revision_id,
                                "issue": "approved_revision_missing_reel_reference"})
    return {"approved_by_reel": approved, "missing_references": missing}


def _project_findings(root: Path) -> dict:
    from library.tools import (
        creative_revision,
        event_log,
        plan_provenance,
        reel_edit_history,
    )
    from library.tools.transaction_receipt import interrupted
    from library.tools.versions import reel_versions, store, variants

    review = root / "pipeline_output" / "review"
    history = reel_versions.read(root).get("reels", {})
    provenance = plan_provenance.read_provenance(str(review)) or {}
    manual = []
    for reel in set(history) | set(provenance.get("reel_edit_history") or {}):
        manual.extend({"reel": reel, **row}
                      for row in reel_edit_history.unexplained_manual_edits(
                          str(review), str(reel)))
    carried = provenance.get(plan_provenance.CARRIED_EDITS_KEY) or {}
    carried_human_edits = [
        {"reel": reel, "edit_id": row.get("id"), "summary": row.get("summary")}
        for reel, edits in carried.items() for row in edits or ()
        if row.get("status") == "active"]
    try:
        pending = interrupted(event_log.read_events(str(root)))
    except (OSError, ValueError, TypeError):
        pending = []
    candidates = variants.read_builds(str(root)).get("builds", {})
    approved = _revisions(root)["approved_by_reel"]
    stale_candidates = []
    for candidate_id, row in candidates.items():
        base = str(row.get("base_final") or "")
        base_revision = str(row.get("base_revision") or "")
        current_revision = str((approved.get(base) or {}).get("revision_id") or "")
        if (row.get("status") in {"stale", "superseded", "abandoned"}
                or (base_revision and current_revision
                    and base_revision != current_revision)):
            stale_candidates.append({"id": candidate_id,
                                     "status": row.get("status", "stale"),
                                     "base_final": base,
                                     "base_revision": base_revision,
                                     "current_revision": current_revision})
    archives = []
    archive_root = root / "pipeline_output" / "review" / store.DRT_RECOVERY_DIRNAME
    referenced_archives = set()
    for rows in history.values():
        for version in rows or ():
            receipt = version.get("recovery_archive") or {}
            archive_path = receipt.get("path") or receipt.get("archive_path")
            if archive_path:
                referenced_archives.add(Path(archive_path).resolve())
    for path in sorted(archive_root.rglob("*.drt")):
        archives.append({"path": str(path.relative_to(root)),
                         "bytes": path.stat().st_size,
                         "stale": path.resolve() not in referenced_archives})
    for reel, rows in history.items():
        for version in rows or ():
            receipt = version.get("recovery_archive") or {}
            archive_path = receipt.get("path") or receipt.get("archive_path")
            if archive_path and not Path(archive_path).is_file():
                archives.append({"path": str(archive_path), "bytes": 0,
                                 "missing": True, "reel": reel,
                                 "version": version.get("version")})
    promotion_dir = review / "promotion_transactions"
    promotions = []
    for path in sorted(promotion_dir.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if row.get("state") != "complete":
                promotions.append({"transaction_id": row.get("transaction_id", path.stem),
                                   "state": row.get("state", "unknown"),
                                   "journal": str(path.relative_to(root))})
        except (OSError, ValueError, TypeError):
            promotions.append({"transaction_id": path.stem,
                               "state": "unreadable",
                               "journal": str(path.relative_to(root))})
    try:
        consistency = creative_revision.reconcile(root, repair=False)
    except Exception as exc:  # noqa: BLE001 - damaged ledgers become findings
        consistency = {"ok": False, "missing_references": [],
                       "error": str(exc)}
    return {"unfinished_transactions": pending,
            "unfinished_promotions": promotions,
            "unresolved_human_edits": manual,
            "carried_human_edits": carried_human_edits,
            "stale_candidates": stale_candidates,
            "recovery_archives": archives,
            "project_model_timeline_shadow": consistency}


def _qualification() -> list[dict]:
    from library.tools import edit_algebra
    return [{"operation": op_id,
             "status": entry.qualification_status,
             "production_enabled": entry.production_enabled,
             "backends": [{"backend": backend.backend,
                           "qualified": backend.qualified,
                           "production_enabled": backend.production_enabled}
                          for backend in entry.backends]}
            for op_id, entry in sorted(edit_algebra.OP_STATUS.items())]


def report(project: str) -> dict:
    root = _project_path(project)
    git = _git_status(root)
    revisions = _revisions(root)
    findings = _project_findings(root)
    cache_root = root / "pipeline_output" / "scratch"
    diagnostics = []
    if not git["available"]:
        diagnostics.append({"severity": "error", "message":
                            "Project Git repository is unavailable; initialize or repair its project store."})
    elif git["uncommitted"]:
        diagnostics.append({"severity": "warning", "message":
                            "Project has uncommitted changes; review the declarations and generated records before promoting."})
    for row in revisions["missing_references"]:
        diagnostics.append({"severity": "error", "message":
                            f"Approved revision {row['revision_id']} has no reel version reference."})
    for row in findings["project_model_timeline_shadow"].get("missing_references", []):
        diagnostics.append({"severity": "error", "message":
                            f"Revision reference is missing from {row.get('source')}: {row.get('entity')} ({row.get('issue')})."})
    if findings["unfinished_transactions"]:
        diagnostics.append({"severity": "error", "message":
                            "Unfinished edit transactions require recovery review before another edit."})
    if findings["unfinished_promotions"]:
        diagnostics.append({"severity": "error", "message":
                            "An unfinished promotion journal requires recovery review before another promotion."})
    if findings["unresolved_human_edits"]:
        diagnostics.append({"severity": "warning", "message":
                            "Unresolved human edits need review before a rebuild or promotion."})
    if not findings["project_model_timeline_shadow"].get("ok", False):
        diagnostics.append({"severity": "error", "message":
                            "Project Model, Timeline Shadow, or creative revision references are inconsistent; inspect the listed references before editing."})
    missing_archives = [row for row in findings["recovery_archives"] if row.get("missing")]
    if missing_archives:
        diagnostics.append({"severity": "error", "message":
                            "A reel version references a missing recovery archive; restore the exact archive before relying on recovery."})
    stale_archives = [row for row in findings["recovery_archives"] if row.get("stale")]
    if stale_archives:
        diagnostics.append({"severity": "warning", "message":
                            "Unreferenced .drt recovery archives are present; confirm their ownership before cleanup."})
    if findings["stale_candidates"]:
        diagnostics.append({"severity": "warning", "message":
                            "Stale or superseded candidate records are present; review them before choosing a candidate."})
    return {"format": "ren_versions_doctor/1", "project": str(root),
            "git": git, "approved_revisions": revisions["approved_by_reel"],
            **findings,
            "storage": {"cache_bytes": _tree_bytes(cache_root),
                        "git_bytes": git["git_bytes"],
                        "recovery_archive_bytes": sum(row["bytes"] for row in findings["recovery_archives"]),
                        "recovery_archive_count": len(findings["recovery_archives"]),
                        "drt_recovery_qualification": "built: timeline.drt_recovery_probe; live run deferred to vep-axi-live-qual"},
            "production_qualification": _qualification(),
            "diagnostics": diagnostics,
            "healthy": not any(row["severity"] == "error" for row in diagnostics)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only version-control health report")
    sub = parser.add_subparsers(dest="action", required=True)
    doctor = sub.add_parser("doctor", help="inspect project version health without changing it")
    doctor.add_argument("project", help="Project slug or absolute project path")
    doctor.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)
    try:
        result = report(args.project)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"ren versions doctor: {exc}", file=sys.stderr)
        return 2
    if args.json or os.environ.get("REN_OUTPUT") == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Version health: {'healthy' if result['healthy'] else 'findings'}")
        print(f"Project: {result['project']}")
        print(f"Branch: {result['git']['branch'] or 'unavailable'}")
        print(f"Worktrees: {len(result['git']['worktrees'])}; uncommitted paths: {len(result['git']['uncommitted'])}; declarations: {len(result['git']['uncommitted_declarations'])}")
        print(f"Git: {result['storage']['git_bytes']} bytes; cache: {result['storage']['cache_bytes']} bytes")
        print(f"Recovery archives: {result['storage']['recovery_archive_count']} .drt, {result['storage']['recovery_archive_bytes']} bytes ({result['storage']['drt_recovery_qualification']})")
        for reel, row in sorted(result["approved_revisions"].items()):
            print(f"Approved {reel}: version {row['version']} revision {row.get('revision_id') or '(legacy)'}")
        print(f"Unfinished transactions: {len(result['unfinished_transactions'])}; promotions: {len(result['unfinished_promotions'])}")
        print(f"Unresolved human edits: {len(result['unresolved_human_edits'])}; stale candidates: {len(result['stale_candidates'])}")
        for row in result["unfinished_transactions"]:
            print(f"  Recover transaction {row['transaction_id']} after stage {row['last_stage'] or 'unknown'}.")
        for row in result["unfinished_promotions"]:
            print(f"  Inspect promotion {row['transaction_id']} ({row['state']}) at {row['journal']}.")
        for row in result["unresolved_human_edits"]:
            print(f"  Resolve human edit on {row.get('reel')}: {row.get('summary') or row.get('id') or row.get('edit_id') or 'unexplained edit'}.")
        for row in result["stale_candidates"]:
            print(f"  Candidate {row['id']} is {row['status']} against {row.get('base_final') or 'unknown reel'}.")
        for row in result["recovery_archives"]:
            if row.get("missing"):
                print(f"  Missing recovery archive for {row.get('reel')} version {row.get('version')}: {row['path']}.")
            elif row.get("stale"):
                print(f"  Unreferenced recovery archive: {row['path']} ({row['bytes']} bytes).")
        consistency = result["project_model_timeline_shadow"]
        if not consistency.get("ok"):
            print("Project Model / Timeline Shadow references need review.")
            for row in consistency.get("missing_references", []):
                print(f"  {row.get('source')}: {row.get('entity')} - {row.get('issue')}.")
        for row in result["production_qualification"]:
            print(f"Operation {row['operation']}: {row['status']} (production enabled: {str(row['production_enabled']).lower()})")
        for row in result["diagnostics"]:
            print(f"{row['severity'].upper()}: {row['message']}")
        if not result["diagnostics"]:
            print("No actionable findings.")
    return 0 if result["healthy"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
