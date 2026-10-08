"""First value, contextual onboarding, and reproducible delivery receipts.

These are assignment-facing artifacts over the existing catalog, assignment
identity, approval/revision records, and deliverable QC contracts. The catalog
map is published by the catalog step, before semantic analysis starts.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from library.tools.project_layout import Area, ProjectLayout

ARTIFACT_VERSION = 1
ONBOARDING = {
    "version": 1,
    "title": "Direct this assignment",
    "lessons": [
        {"stage": "understand", "title": "Start with the footage map",
         "text": "Use the catalog to point Ren at the moments that matter."},
        {"stage": "review", "title": "Review the proposed cut",
         "text": "Give feedback in chat; Ren shows the change and named variants."},
        {"stage": "revise", "title": "Choose a revision",
         "text": "Only the variant you approve is applied."},
        {"stage": "deliver", "title": "Deliver when ready",
         "text": "The receipt links the approved version, files, specs, QC, and resume point."},
    ],
}


def first_value_catalog(catalog: Mapping, *, assignment_id: str | None = None) -> dict:
    """Create the useful, lightweight catalog map available before analysis."""
    clips = catalog["clip_catalog"]
    rows = []
    for clip in clips:
        rows.append({key: clip[key] for key in (
            "clip_id", "source_order", "filename", "duration_seconds",
            "width", "height", "frame_rate", "path") if key in clip})
    return {
        "version": ARTIFACT_VERSION,
        "kind": "footage_map",
        "assignment_id": assignment_id,
        "available_after": "footage.catalog",
        "deeper_analysis_complete": False,
        "total_clips": catalog["total_clips"],
        "clips": rows,
        "audio": [{key: item[key] for key in (
            "audio_id", "audio_order", "filename", "duration_seconds", "path")
                   if key in item} for item in catalog.get("audio_catalog", [])],
        "skipped_files": list(catalog.get("skipped_files", [])),
    }


def publish_first_value(project: str, catalog: Mapping, *,
                        assignment_id: str | None = None) -> Path:
    """Write the first-value artifact beside the catalog step's own output."""
    path = ProjectLayout(project).step_dir("catalog", create=True) / "first_value.json"
    _atomic_json(path, first_value_catalog(catalog, assignment_id=assignment_id))
    return path


def onboarding(assignment_id: str, *, context: str) -> dict:
    """Return the same assignment loop lesson at the point it becomes useful."""
    allowed = {"catalog_ready", "review_ready", "revision_ready", "delivery_ready"}
    if context not in allowed:
        raise ValueError(f"unknown onboarding context {context!r}")
    stages = {"catalog_ready": ("understand",), "review_ready": ("review",),
              "revision_ready": ("revise",), "delivery_ready": ("deliver",)}
    lessons = [lesson for lesson in ONBOARDING["lessons"]
               if lesson["stage"] in stages[context]]
    return {"version": ARTIFACT_VERSION, "assignment_id": assignment_id,
            "context": context, "dismissible": True, "lessons": lessons}


def dismiss_onboarding(project: str, assignment_id: str) -> dict:
    """Persist a dismissal for this assignment only."""
    path = _assignment_dir(project, assignment_id) / "onboarding.json"
    record = {"version": ARTIFACT_VERSION, "assignment_id": assignment_id,
              "dismissed": True}
    _atomic_json(path, record)
    return record


def load_onboarding(project: str, assignment_id: str, *, context: str) -> dict:
    path = _assignment_dir(project, assignment_id) / "onboarding.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if record is not None and record["assignment_id"] != assignment_id:
        raise ValueError("onboarding assignment identity mismatch")
    if record is not None and record["dismissed"]:
        return {"version": ARTIFACT_VERSION, "assignment_id": assignment_id,
                "context": context, "dismissed": True, "lessons": []}
    return onboarding(assignment_id, context=context)


def build_delivery_receipt(*, assignment_id: str, approved_version: Mapping,
                           files: Sequence[Mapping], qc: Mapping,
                           resume_point: Mapping, selected_platforms=()) -> dict:
    """Build the deterministic core receipt and only selected platform exports.

    Missing files and failed QC are retained as named outcomes. No platform is
    inferred: platform files are included only when their platform was chosen.
    """
    if approved_version["status"] != "approved":
        raise ValueError("delivery requires an approved version")
    if approved_version["assignment_id"] != assignment_id:
        raise ValueError("approved version belongs to another assignment")
    if not resume_point.get("action"):
        raise ValueError("resume_point must name the next action")
    selected = set(selected_platforms)
    included = []
    for item in files:
        platform = item.get("platform")
        if platform is not None and platform not in selected:
            continue
        included.append({
            "name": item["name"], "path": item["path"],
            "platform": platform, "spec": item.get("spec"),
            "exists": bool(item["exists"]), "status": item["status"],
            "qc": item.get("qc"),
        })
    included.sort(key=lambda row: (row["platform"] or "", row["name"], row["path"]))
    missing = [row["name"] for row in included if not row["exists"]]
    failed = [row["name"] for row in included if row["status"] == "failed"]
    failed_qc = [row["name"] for row in included
                 if row["qc"] is not None and row["qc"].get("passed") is False]
    return {
        "version": ARTIFACT_VERSION, "kind": "assignment_delivery_receipt",
        "assignment_id": assignment_id,
        "approved_version": dict(approved_version),
        "files": included,
        "qc": dict(qc),
        "resume_point": dict(resume_point),
        "selected_platforms": sorted(selected),
        "missing_outputs": missing,
        "failed_outputs": sorted(set(failed + failed_qc)),
        "status": "failed" if failed or failed_qc else "incomplete" if missing else "complete",
    }


def publish_delivery_receipt(project: str, *, assignment_id: str,
                             approved_version: Mapping, files: Sequence[Mapping],
                             qc: Mapping, resume_point: Mapping,
                             selected_platforms=()) -> tuple[Path, dict]:
    receipt = build_delivery_receipt(
        assignment_id=assignment_id, approved_version=approved_version,
        files=files, qc=qc, resume_point=resume_point,
        selected_platforms=selected_platforms)
    path = ProjectLayout(project).write_path(Area.EXPORTS, "assignments",
                                             f"{_safe_id(assignment_id)}-delivery.json")
    _atomic_json(path, receipt)
    return path, receipt


def _safe_id(value: str) -> str:
    if not value or value.strip() != value or any(c in value for c in "/\\\0"):
        raise ValueError("assignment_id must be a non-empty plain identifier")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _assignment_dir(project: str, assignment_id: str) -> Path:
    return (ProjectLayout(project).write_dir(Area.LLM_REQUESTS) /
            "assignments" / _safe_id(assignment_id))


def _atomic_json(path: Path, value: Mapping) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as f:
            temp = Path(f.name)
            json.dump(value, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
