"""verify_export: the gate over a DELIVERED export, criterion by criterion.

`library/tools/export_verification.py` answers the captain's eight
final-output acceptance criteria (QA-001..QA-008).  This is its entry
point, so a step - or the pipeline on a model's behalf - can invoke it
by import or by shell:

    python3 -m library.skills.verify_export.skill \
        --video /path/to/export.mp4 --project-folder /path/to/project \
        --step-id verify-reel [--timeline "Reel 09 (final)"]

What it assembles
-----------------
The three records the verification reads, each read from where the
engine wrote it:

- the PLAN: the project's accepted edit plan (the compiled
  `assembly_manifest.json`), or `--plan` when the caller names one.
- the BUILD: the built timeline's own record - the committed timeline
  snapshot under `pipeline_output/review/`.  On disk, so no Resolve and
  no render are needed.
- the DELIVERY: the identity record the delivery wrote - the ledger and
  the `.deliver.json` sidecar beside the file.

The declared frame, rate, length, encoding and loudness come from the
delivery spec the project's named platform selects, so the export is
judged against the platform it was made for rather than a bare default.

What it returns
---------------
A verdict that GATES: `passed` plus one row per criterion, each with
its verdict, its detail, its evidence and its reader.

`passed` is True only when every criterion PASSED.  A criterion whose
evidence is missing is UNKNOWN, and an UNKNOWN criterion does not pass:
"a verified export includes evidence for every required acceptance
criterion", so an export nobody could check is an unchecked export.
The verdict says which criterion could not be determined and what would
resolve it.

Every invocation writes a RECEIPT to
`<project>/pipeline_output/skill_runs/<step_id>/verify_export.json`.
The receipt is what the must-check rule reads back: a model asserting
it checked writes no receipt, so a self-reported check fails the gate
the way a gate that cannot fail should.  See
`library/tools/pipeline_skills.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional
SKILL_NAME = "verify_export"


def _delivery_spec(project_folder: str):
    """The delivery spec the project's named platform selects, or None."""
    if not project_folder:
        return None
    try:
        from library.tools.delivery_spec import (
            resolve_delivery_spec_for_project)

        return resolve_delivery_spec_for_project(project_folder)
    except Exception:  # noqa: BLE001 - a missing spec is a missing spec
        return None


def _source_frame_rates(project_folder: str) -> Dict[str, float]:
    """Each footage file's measured frame rate, by path.

    The catalog measured it off the footage; the plan states source
    ranges in SECONDS and the timeline states them in FRAMES, so the
    comparison between them needs the source's own rate.  A file the
    catalog says nothing about is simply absent from the map, and the
    criterion that needs it says so.
    """
    if not project_folder:
        return {}
    from library.tools import capability_outputs
    from library.tools.project_layout import PIPELINE_DATA_FILE

    path = os.path.join(project_folder, PIPELINE_DATA_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {}
    catalog = capability_outputs.value(state, "footage.catalog",
                                       "clip_catalog")
    rates = {}
    for entry in catalog or []:
        if not isinstance(entry, dict):
            continue
        media = str(entry.get("path") or entry.get("file_path") or "")
        try:
            fps = float(entry.get("frame_rate"))
        except (TypeError, ValueError):
            continue
        if media and fps > 0:
            rates[os.path.normpath(media)] = fps
    return rates


def run(video_path: str,
        project_folder: str,
        step_id: str,
        timeline_name: str = "",
        plan_path: str = "") -> Dict[str, Any]:
    """Verify one delivered export and record the receipt.

    Returns a verdict dict with `passed`, one row per criterion, and the
    `receipt` path.  `passed` is False when any criterion fails or
    could not be determined - this skill GATES.  An absent file is a
    REFUSAL, recorded in the receipt, never a pass.
    """
    from library.tools import export_verification, pipeline_skills

    if not video_path or not os.path.isfile(video_path):
        verdict: Dict[str, Any] = {
            "skill": SKILL_NAME,
            "passed": False,
            "video_path": video_path,
            "criteria": [],
            "issues": [f"export file not found: {video_path}"],
        }
        verdict["receipt"] = pipeline_skills.write_receipt(
            project_folder, step_id, SKILL_NAME, verdict)
        return verdict

    plan = None
    if plan_path:
        try:
            with open(plan_path, encoding="utf-8") as handle:
                plan = json.load(handle)
        except (OSError, ValueError) as failed:
            verdict = {
                "skill": SKILL_NAME,
                "passed": False,
                "video_path": video_path,
                "criteria": [],
                "issues": [f"the plan could not be read: {failed}"],
            }
            verdict["receipt"] = pipeline_skills.write_receipt(
                project_folder, step_id, SKILL_NAME, verdict)
            return verdict
    if plan is None:
        plan = export_verification.load_plan(project_folder)

    delivery = export_verification.load_delivery(project_folder,
                                                  timeline_name)
    named = timeline_name or str((delivery or {}).get("timeline_name") or "")
    built = export_verification.load_built(project_folder, named) if named \
        else None

    spec = _delivery_spec(project_folder)
    expectation = export_verification.ExportExpectation(
        plan=plan,
        built=built,
        delivery=delivery,
        project_folder=project_folder or "",
        codec=(spec.codec if spec else ""),
        container=(spec.container if spec else ""),
        loudness_target_lufs=(spec.loudness_target_lufs if spec else None),
        loudness_tolerance_lufs=(spec.loudness_tolerance_lufs if spec else None),
        true_peak_ceiling_dbtp=(spec.true_peak_ceiling_dbtp if spec else None),
        source_frame_rates=_source_frame_rates(project_folder),
    )

    try:
        report = export_verification.verify_export(video_path, expectation)
    except export_verification.ExportVerificationRefused as refused:
        verdict = {
            "skill": SKILL_NAME,
            "passed": False,
            "video_path": video_path,
            "criteria": [],
            "issues": [str(refused)],
        }
        verdict["receipt"] = pipeline_skills.write_receipt(
            project_folder, step_id, SKILL_NAME, verdict)
        return verdict

    criteria = [result.as_dict() for result in report.results]
    issues = [f"{result.criterion}: {result.detail}"
              for result in report.results
              if result.verdict in (export_verification.FAIL,
                                    export_verification.UNKNOWN)]
    verdict = {
        "skill": SKILL_NAME,
        "passed": report.passed,
        "video_path": video_path,
        "timeline_name": named,
        "criteria": criteria,
        "issues": issues,
        "failed": [result.criterion for result in report.failures()],
        "unknown": [result.criterion for result in report.unknowns()],
    }
    verdict["receipt"] = pipeline_skills.write_receipt(
        project_folder, step_id, SKILL_NAME, verdict)
    return verdict


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Final-output gate (skill: verify_export): the "
                    "captain's eight acceptance criteria, one verdict "
                    "each, against a delivered export.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--project-folder", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument(
        "--timeline", default="",
        help="the timeline whose build and delivery the export is judged "
             "against; the delivery ledger names one when this is absent")
    parser.add_argument(
        "--plan", default="",
        help="the accepted edit plan; the project's compiled manifest when "
             "this is absent")
    args = parser.parse_args(argv)

    verdict = run(args.video, args.project_folder, args.step_id,
                  timeline_name=args.timeline, plan_path=args.plan)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
