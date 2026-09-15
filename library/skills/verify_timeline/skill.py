"""verify_timeline: the deterministic gate over a built timeline.

Wraps `library.tools.timeline_conformance.verify_timeline` - the checks
that read the live Resolve timeline back against the SOP (docs/
TIMELINE_SOP.md) and need a running Resolve instance, the exact project
open, and the timeline addressed by its exact listed name. A skill entry
point, so a step (or the pipeline on its behalf) can invoke it by import
or by shell:

    python3 -m library.skills.verify_timeline.skill \
        --project "Exact Project Name" --timeline "Reel 09" \
        --project-folder /path/to/project --step-id build \
        [--plan-json '{"video_tracks": [...], ...}']

Every invocation writes a RECEIPT to
`<project>/pipeline_output/skill_runs/<step_id>/verify_timeline.json`
carrying the measured verdict. The receipt is what the must-check rule
reads back: a model asserting it checked writes no receipt, so a
self-reported check fails the gate the way a gate that cannot fail
should. See `library/tools/pipeline_skills.py`.

A refusal is a verdict, not an exception: Resolve not running, the open
project not exactly the named one, no timeline under the exact name, or
a malformed track plan all return `passed: False` with the reason in
`issues` - and the receipt records that refusal. An absent timeline is
not a conforming one.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

SKILL_NAME = "verify_timeline"


class TimelineUnreachable(RuntimeError):
    """Resolve, the project, or the timeline could not be reached."""


def plan_from_dict(raw: Any):
    """Rebuild the track plan a build result recorded, or refuse it.

    Takes the `serializable()` shape (`video_tracks`, `audio_tracks`,
    `material`) - the same shape `timeline_conformance`'s CLI accepts.
    Raises ValueError naming what is missing: a gate that checks
    against a guessed plan is worse than one that says it cannot run.
    """
    from library.tools.timeline_layout import TrackPlan, TrackSpec

    if not isinstance(raw, dict):
        raise ValueError(
            "verify_timeline needs the track plan as an object with "
            f"video_tracks/audio_tracks, got {type(raw).__name__}.")
    for key in ("video_tracks", "audio_tracks"):
        tracks = raw.get(key)
        if not isinstance(tracks, list):
            raise ValueError(
                f"verify_timeline needs plan[{key!r}] to be a list, got "
                f"{tracks!r}.")
        for track in tracks:
            if not isinstance(track, dict):
                raise ValueError(
                    f"verify_timeline needs every plan[{key!r}] entry to "
                    f"be an object, got {track!r}.")
    try:
        return TrackPlan(
            video_tracks=[TrackSpec(**t) for t in raw["video_tracks"]],
            audio_tracks=[TrackSpec(**t) for t in raw["audio_tracks"]],
            material=raw.get("material", {}),
        )
    except TypeError as exc:
        raise ValueError(
            f"verify_timeline REFUSED the track plan: {exc}.") from exc


def open_timeline(project_name: str, timeline_name: str):
    """The live timeline handle, addressed by exact names.

    A Resolve project is addressed by its EXACT listed name, never a
    prefix - and so is the timeline. Raises TimelineUnreachable naming
    which lookup failed: a gate that verifies the wrong timeline is
    worse than one that says it cannot run.
    """
    try:
        import DaVinciResolveScript as dvr
    except ImportError as exc:
        raise TimelineUnreachable(
            "Resolve scripting is not on this machine "
            "(no DaVinciResolveScript module).") from exc
    from library.tools.resolve_locale import scriptapp_preserving_locale

    resolve = scriptapp_preserving_locale(dvr)
    if resolve is None:
        raise TimelineUnreachable("Resolve is not running.")
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager is not None else None
    if project is None or project.GetName() != project_name:
        raise TimelineUnreachable(
            f"open project is not exactly {project_name!r}.")
    for index in range(1, (project.GetTimelineCount() or 0) + 1):
        candidate = project.GetTimelineByIndex(index)
        if candidate is not None and candidate.GetName() == timeline_name:
            return candidate
    raise TimelineUnreachable(f"no timeline exactly {timeline_name!r}.")


def _refusal(project_folder: str, step_id: str, timeline_name: str,
             reason: str) -> Dict[str, Any]:
    """A refusal verdict, receipted: the check did not run, and says so."""
    from library.tools import pipeline_skills

    verdict: Dict[str, Any] = {
        "skill": SKILL_NAME,
        "passed": False,
        "timeline": timeline_name,
        "checks": [],
        "checks_run": [],
        "checks_skipped": [],
        "issues": [reason],
    }
    verdict["receipt"] = pipeline_skills.write_receipt(
        project_folder, step_id, SKILL_NAME, verdict)
    return verdict


def run(timeline_name: str,
        project_folder: str,
        step_id: str,
        *,
        project: str,
        plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read the built timeline back against the SOP and record the receipt.

    Returns a verdict dict with `passed`, one row per executed check in
    `checks`, the openly skipped checks in `checks_skipped`, the SOP
    violations in `issues`, and the `receipt` path. `passed` is False
    when any check fails - this skill GATES. A timeline that cannot be
    reached, or a plan that cannot be rebuilt, is a refusal verdict,
    never an exception and never a pass.
    """
    from library.tools import pipeline_skills
    from library.tools.timeline_conformance import verify_timeline

    if not timeline_name:
        return _refusal(project_folder, step_id, timeline_name,
                        "verify_timeline needs a timeline name and got "
                        "none - a gate checks what was asked for, not "
                        "whatever is current.")
    if not project:
        return _refusal(project_folder, step_id, timeline_name,
                        "verify_timeline needs the exact Resolve project "
                        "name and got none.")
    track_plan = None
    if plan is not None:
        try:
            track_plan = plan_from_dict(plan)
        except ValueError as exc:
            return _refusal(project_folder, step_id, timeline_name,
                            str(exc))
    try:
        timeline = open_timeline(project, timeline_name)
    except TimelineUnreachable as exc:
        return _refusal(project_folder, step_id, timeline_name, str(exc))

    report = verify_timeline(timeline, plan=track_plan)
    by_check: Dict[str, List[str]] = {}
    for violation in report.get("violations", []):
        by_check.setdefault(violation.get("check", "?"), []).append(
            violation.get("detail", ""))
    checks = []
    for name in report.get("checks_run", []):
        hits = by_check.get(_violation_for(name), [])
        checks.append({
            "name": name,
            "passed": not hits,
            "detail": "; ".join(hits) if hits else "no violations",
        })
    issues = [v.get("detail", "") for v in report.get("violations", [])]
    verdict = {
        "skill": SKILL_NAME,
        "passed": bool(report.get("passed")),
        "timeline": timeline_name,
        "checks": checks,
        "checks_run": list(report.get("checks_run", [])),
        "checks_skipped": list(report.get("checks_skipped", [])),
        "issues": issues,
    }
    verdict["receipt"] = pipeline_skills.write_receipt(
        project_folder, step_id, SKILL_NAME, verdict)
    return verdict


def _violation_for(check: str) -> str:
    """The violation tag a passing check must have none of."""
    return {
        "no_empty_tracks": "empty_track",
        "named_tracks": "unnamed_track",
        "singleton_roles": "duplicate_role",
        "aroll_linked": "aroll_unlinked",
        "captions_linked": "caption_unlinked",
        "program_stream": "program_stream",
    }.get(check, check)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic timeline-SOP gate "
                    "(skill: verify_timeline).")
    parser.add_argument("--project", required=True,
                        help="Exact Resolve project name")
    parser.add_argument("--timeline", required=True,
                        help="Exact timeline name")
    parser.add_argument("--project-folder", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--plan-json", default=None,
                        help="TrackPlan serializable as a JSON string "
                             "(from a build result's track_plan); "
                             "without it, link and stream checks are "
                             "skipped openly")
    args = parser.parse_args(argv)

    plan = None
    if args.plan_json:
        try:
            plan = json.loads(args.plan_json)
        except ValueError as exc:
            print(f"verify_timeline REFUSED: --plan-json is not JSON: "
                  f"{exc}", file=sys.stderr)
            return 2
        if not isinstance(plan, dict):
            print("verify_timeline REFUSED: --plan-json must decode to "
                  "an object with video_tracks/audio_tracks",
                  file=sys.stderr)
            return 2

    verdict = run(args.timeline, args.project_folder, args.step_id,
                  project=args.project, plan=plan)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
