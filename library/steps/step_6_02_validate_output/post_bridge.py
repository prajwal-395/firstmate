#!/usr/bin/env python3
"""Step 6.02 post-bridge: one verdict from the deterministic and LLM halves.

The work is `resolve_validation`, which takes the merged answer and
returns the final verdict.  `main()` owns the process: stdin and
stdout.  See AGENTS.md 3.
"""
import sys
import json
import os


def _repo_root() -> str:
    return os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", ".."))


def _skill_runs_dir(project_folder: str) -> str:
    """This run's skill receipts, under the project that owns them."""
    root = _repo_root()
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from library.tools import pipeline_skills
        reldir = pipeline_skills.RECEIPTS_RELDIR
    except ImportError:
        reldir = os.path.join("pipeline_output", "skill_runs")
    return os.path.join(str(project_folder), reldir)


def _verify_timeline_receipts(project_folder: str) -> list:
    """Every `verify_timeline` receipt under this project's skill runs.

    The step's own node id is not in the merge data the runner hands
    this script, so the lookup is project-scoped rather than
    step-scoped: 6.02 is the only declarer of `verify_timeline`, so in
    practice exactly one receipt exists - this run's. If a second step
    ever declares it, scope this by step id (which the runner must then
    pass along). Unreadable files are skipped: a half-written receipt
    is not a verdict.
    """
    base = _skill_runs_dir(project_folder)
    try:
        step_ids = sorted(os.listdir(base))
    except OSError:
        return []
    found = []
    for step_id in step_ids:
        path = os.path.join(base, step_id, "verify_timeline.json")
        try:
            with open(path, encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError):
            continue
        result = record.get("result") if isinstance(record, dict) else None
        if isinstance(result, dict):
            found.append((step_id, result))
    return found


def _track_plan_available(data: dict) -> bool:
    """Whether this step's inputs carried the build's own track plan.

    The plan travels on the render record 6.01 wrote
    (`rendered_output.track_plan`, forwarded by its payload helper); the
    DAG edge names it `rendered_output` here (`render_output` is read as
    a fallback for callers holding the pre-edge spelling). A build that
    recorded no plan - anything rendered before the forwarding - carries
    none, and then the skill's openly-skipped link checks are the
    pipeline's gap, not the model's: the structural half is the whole
    gate that run could answer, and it stands.
    """
    data = data or {}
    rendered = data.get("rendered_output") or data.get("render_output") or {}
    plan = rendered.get("track_plan") if isinstance(rendered, dict) else None
    return (isinstance(plan, dict)
            and isinstance(plan.get("video_tracks"), list)
            and isinstance(plan.get("audio_tracks"), list))


def timeline_gate_failure(data: dict):
    """The `verify_timeline` verdict as a failure reason, or None.

    A gating skill's receipt is the measured verdict, read back from
    disk rather than from the answer's claim that it checked
    (`library/tools/pipeline_skills`). The must-check already fails the
    step when no receipt exists at all; this reads the verdict itself,
    because a FAILED check with a receipt on disk would otherwise pass
    the must-check and ship:

    - `passed: false` fails, violations and refusals alike. A refusal
      (Resolve down, wrong project, no such timeline, malformed plan)
      is not a conforming timeline: an unverified timeline is not
      approved, and the receipt names why.
    - `passed: true` with openly skipped checks fails only when the
      plan was in this step's inputs and the link/stream checks were
      skipped anyway: the whole SOP was answerable and half of it was
      not run. Without a plan in context the structural half stands.
    """
    project_folder = (data or {}).get("project_folder") or ""
    if not project_folder:
        return None
    receipts = _verify_timeline_receipts(project_folder)
    if not receipts:
        return None
    for step_id, result in receipts:
        if result.get("passed"):
            continue
        issues = result.get("issues") or ["no reason recorded"]
        detail = "; ".join(str(i) for i in issues if i)
        checks = result.get("checks") or []
        failed = [c.get("name") for c in checks
                  if isinstance(c, dict) and not c.get("passed")]
        where = f" ({'/'.join(failed)})" if failed else ""
        return (
            f"verify_timeline FAILED the timeline "
            f"(receipt skill_runs/{step_id}/verify_timeline.json)"
            f"{where}: {detail}. A timeline that disobeys the SOP - or "
            f"that could not be read back - is not approved.")
    if _track_plan_available(data):
        skipped = sorted({s for _, result in receipts
                          for s in (result.get("checks_skipped") or [])})
        if skipped:
            return (
                f"verify_timeline passed only its structural half: "
                f"{', '.join(skipped)} skipped openly although the "
                f"build's track plan is in this step's inputs "
                f"(rendered_output.track_plan). Re-run the skill with "
                f"--plan-json so the whole SOP is read, not just its "
                f"structural half - a gate that did not run is not a "
                f"pass.")
    return None


def resolve_validation(data: dict) -> dict:
    """Combine the deterministic checks and the model's reading into one status.

    The deterministic half is decisive: anything but `pass` there is a
    fail regardless of what the model said.
    """
    det = data.get("deterministic_validation", {})
    llm = data.get("validation_result", {})

    # The timeline gate is decisive over both halves: a timeline that
    # disobeys the SOP - or that could not be read back - fails the
    # step no matter what the file measurements and the model say.
    # `check_validation_verdict` in run_pipeline fails the run on a
    # `validate` verdict of fail, so this is what stops the build.
    gate = timeline_gate_failure(data or {})
    if gate is not None:
        checks = dict(det.get("checks", {}))
        checks.update(llm.get("checks", {}))
        checks["timeline_sop"] = {"pass": False, "issues": [gate]}
        all_issues = [f"[timeline_sop] {gate}"]
        all_issues.extend(det.get("all_issues", []))
        all_issues.extend(llm.get("all_issues", []))
        summary = det.get("summary", "")
        if llm.get("summary"):
            summary += " | LLM: " + llm.get("summary")
        summary = (summary + " | " if summary else "") + "Timeline SOP gate failed"
        return {"validation_result": {
            "status": "fail",
            "checks": checks,
            "all_issues": all_issues,
            "distribution_ready": False,
            "critical_checks_passed": det.get("critical_checks_passed", False),
            "summary": summary,
            "qa_report_path": det.get("qa_report_path", ""),
            "qa_report": det.get("qa_report", []),
        }}

    det_status = det.get("status")
    llm_status = llm.get("status")

    if det_status != "pass":
        status = "fail"
        if not det.get("summary"):
            det["summary"] = "Deterministic validation failed to produce a valid status"
    elif llm_status == "fail":
        status = "fail"
    elif llm_status == "undetermined":
        status = "undetermined"
    elif llm_status != "pass":
        status = "fail"
    else:
        status = "pass"
        
    distribution_ready = (status == "pass")

    checks = dict(det.get("checks", {}))
    checks.update(llm.get("checks", {}))
    
    all_issues = list(det.get("all_issues", []))
    all_issues.extend(llm.get("all_issues", []))
    
    summary = det.get("summary", "")
    if llm.get("summary"):
        summary += " | LLM: " + llm.get("summary")

    final_result = {
        "status": status,
        "checks": checks,
        "all_issues": all_issues,
        "distribution_ready": distribution_ready,
        "critical_checks_passed": det.get("critical_checks_passed", False),
        "summary": summary,
        "qa_report_path": det.get("qa_report_path", ""),
        "qa_report": det.get("qa_report", [])
    }
    
    # ONE verdict leaves this node, and it is `validation_result`.
    # `final_qa_decision` was declared beside it and echoed from
    # `data.get("final_qa_decision", "")` - a key no edge routed, no
    # handoff asked the model for and no default supplied, so it was the
    # empty string on every run and no reader existed. Declared, not
    # produced, and unread: the whole shape is deleted rather than given
    # a value nobody would consult. `run_pipeline.py` reads
    # `validation_result.status`, which is the real verdict.
    return {"validation_result": final_result}


def main():
    json.dump(resolve_validation(json.load(sys.stdin)), sys.stdout, indent=2)


if __name__ == "__main__":
    main()
