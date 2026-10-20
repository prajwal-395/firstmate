"""Plain-language, read-only progress for a Ren project run.

This is a host-facing projection of the runner's own status, capability
timings and the Resolve broker's existing ping. It does not poll the
pipeline or trigger any work; one call is a handful of local file reads
and one optional broker ping.
"""
from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
import time
from pathlib import Path

from library.tools import perf_ledger, run_control

#: The run-record key `record_progress_event` appends to. The runner
#: writes one event per step boundary, so `ren status` and any later
#: reader can say what happened without re-deriving it.
PROGRESS_EVENTS_KEY = "progress_events"

#: A run that re-ran steps without bound would grow this list without
#: bound, and every boundary rewrites the record. Oldest-first drop is
#: honest here: the newest boundaries are what a reader asks about.
MAX_PROGRESS_EVENTS = 500

# Pipeline steps are grouped by the work a person sees. This is deliberately
# a presentation map, not an alternate execution plan.
_STAGE_BY_STEP = {
    **dict.fromkeys(("scan", "catalog", "semantic_analysis", "temporal_index",
                     "prosody_analysis", "ocr_extraction", "object_segmentation"),
                    "analysis"),
    **dict.fromkeys(("creative_direction", "speech_sequence", "music_selection",
                     "music_analysis", "mesh_spine", "plan_picture", "assign_aroll",
                     "select_broll", "review_rough_cut", "select_reels", "judge_reels",
                     "plan_subtitles", "plan_transitions", "plan_vfx", "plan_sfx",
                     "color_grade", "audio_mix", "build_edit_graph", "compile_manifest",
                     "creative_cohesion"), "reasoning"),
    **dict.fromkeys(("render_subtitles", "render_motion_graphics", "render"), "execution"),
    "validate": "verification",
}


def _stage(step_id: str) -> str:
    return _STAGE_BY_STEP.get(step_id, "execution")


def _stage_reason(stage: str, step_id: str) -> str:
    reasons = {
        "analysis": "Ren is measuring the source material needed for later editorial decisions.",
        "reasoning": "Ren is turning the available measurements and brief into an edit decision.",
        "execution": "Ren is applying the approved plan to produce the requested output.",
        "verification": "Ren is checking the output against its declared requirements.",
    }
    return reasons[stage]


def _next_action(outcome: str) -> str:
    return {
        "completed": "Continue to the next planned step.",
        "reused": "Continue using the existing result.",
        "failed": "Inspect the failure evidence and revise or retry the step.",
        "awaiting_llm": "Review the request and provide the requested decision.",
        "paused": "Resume the run when ready.",
        "held": "Review the hold and resume when ready.",
    }.get(outcome, "Check the run status for the next action.")


def _dag() -> list[dict]:
    path = Path(__file__).resolve().parents[1] / "processes" / "edit_video" / "dag.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)["nodes"]


def _broker() -> dict:
    try:
        from library.tools.resolved import client
        answer = client.ping()
    except (OSError, ConnectionError):
        answer = None
    if not answer:
        return {"serving": False, "busy": False, "lease": None}
    return {"serving": True, "busy": bool(answer.get("busy")),
            "lease": answer.get("lease")}


def _historical_medians(rows: list[dict], current_run_id: str | None) -> dict[str, float]:
    samples: dict[str, list[float]] = {}
    for row in rows:
        if (row.get("kind") == perf_ledger.CAPABILITY
                and row.get("run_id") != current_run_id
                and row.get("status") in ("ok", "reused")
                and isinstance(row.get("wall_s"), (int, float))):
            samples.setdefault(str(row.get("node") or row.get("capability")), []).append(
                float(row["wall_s"]))
    return {node: statistics.median(values) for node, values in samples.items()
            if len(values) >= 2}


def capability_availability(project: str) -> tuple:
    """What this project can use, and what it cannot - and what unblocks it.

    One reading, in its own function, so the progress report and the task
    state cannot disagree about which capability is missing. It reads the
    project's own files and touches no broker: a reader asking what Ren
    cannot do must not wake Resolve to find out.
    """
    root = Path(project).expanduser().resolve()
    index_file = (root / "pipeline_output" / "scratch" / "footage_index"
                  / "footage_index.json")
    available = []
    unavailable = []
    if index_file.is_file():
        available.append({"id": "footage.search",
                          "label": "Search the indexed footage and dialogue "
                                   "with `ren search`."})
        return available, unavailable
    from library.tools.project_layout import ProjectLayout
    temporal_ready = ProjectLayout(root).step_dir(
        "temporal_index", "output.json").is_file()
    if temporal_ready:
        available.append({"id": "footage.search_index",
                          "label": "Dialogue analysis is available; build its "
                                   "searchable index with `ren search-index`."})
        unavailable.append({"id": "footage.search",
                            "reason": "The searchable footage index has not "
                                      "been built.",
                            "next_action": "Run `ren search-index` when you "
                                           "want searchable footage."})
    else:
        unavailable.append({"id": "footage.search",
                            "reason": "Dialogue analysis and its searchable "
                                      "index are not available in this project "
                                      "yet.",
                            "next_action": "Run the relevant analysis before "
                                           "searching footage."})
    return available, unavailable


def progress_report(project: str, *, broker_reader=_broker) -> dict:
    """Return a compact report a host can relay without interpreting step ids."""
    root = Path(project).expanduser().resolve()
    status = run_control.read_run_status(str(root))
    nodes = _dag()
    by_id = {node["id"]: node for node in nodes}
    plan = [step for step in status.get("steps_to_run", []) if step in by_id]
    timings = status.get("step_timings") or {}
    done_ids = [step for step in plan if step in timings]
    active = status.get("active_steps") or []
    current = status.get("current_step")
    running = [step for step in active if step in by_id]
    if not running and current in by_id and status.get("status") == "running":
        running = [current]
    pending = [step for step in plan if step not in done_ids and step not in running]

    rows = perf_ledger.read_rows(str(root))
    stream = status.get("semantic_profile_stream") or {}
    run_id = stream.get("run_id")
    medians = _historical_medians(rows, run_id)
    remaining_ids = [*running, *pending]
    # Parallel workers make a summed estimate misleading. Keep the ETA
    # available only when this run has one active step and every step
    # still to come has enough prior measurements.
    estimate_supported = (len(running) <= 1
                          and all(step in medians for step in remaining_ids))
    remaining_estimates = ([medians[step] for step in pending]
                           if estimate_supported else [])
    current_started = status.get("current_step_started_at")
    if running and running[0] in medians and current_started:
        try:
            started = datetime.datetime.strptime(
                str(current_started), "%Y-%m-%dT%H:%M:%S").astimezone().timestamp()
            elapsed = max(0.0, time.time() - started)
            remaining_estimates.append(max(0.0, medians[running[0]] - elapsed))
        except (TypeError, ValueError):
            remaining_estimates.append(medians[running[0]])
    eta = (round(sum(remaining_estimates))
           if estimate_supported and remaining_ids else None)

    titles = lambda ids: [by_id[step]["name"] for step in ids]
    capabilities, unavailable = capability_availability(project)

    state = str(status.get("status") or "not_started")
    if state == "running":
        focus = titles(running)
        message = (f"I'm working on {', '.join(focus)}." if focus else "I'm working through the edit.")
        if done_ids:
            message = f"I've completed {len(done_ids)} of {len(plan)} planned steps. " + message
    elif state in ("success", "completed"):
        message = f"The run is complete. I finished {len(done_ids)} recorded steps."
    elif state in ("failed", "rejected"):
        message = "The run stopped before it finished."
    elif state == "held":
        message = "The run is paused at a step boundary."
    else:
        message = "No active run is recorded for this project."

    latest_event = (status.get(PROGRESS_EVENTS_KEY) or [])
    latest_event = latest_event[-1] if latest_event and isinstance(latest_event[-1], dict) else None
    visible_stage = _stage(running[0]) if running else (latest_event or {}).get("stage")
    visible_why = _stage_reason(visible_stage, running[0]) if running else (latest_event or {}).get("why")
    visible_evidence = (latest_event or {}).get("evidence", [])
    visible_next = (latest_event or {}).get("next_action") or ("Wait for the active step to finish." if state == "running" else "Review the run status.")
    if visible_stage:
        message += f" Stage: {visible_stage}. Why: {visible_why} Next: {visible_next}"
    if visible_evidence:
        message += " Evidence: " + ", ".join(str(row.get("path", row.get("ref", "")))
                                                for row in visible_evidence if isinstance(row, dict))
    if unavailable:
        message += " Unavailable: " + " ".join(
            f"{item['id']}: {item['reason']} {item['next_action']}" for item in unavailable)
    return {
        "project": str(root), "status": state, "message": message,
        "stage": visible_stage,
        "why": visible_why,
        "evidence": visible_evidence,
        "next_action": visible_next,
        "done": titles(done_ids), "running": titles(running),
        "up_next": titles(pending[:3]), "remaining_steps": len(pending),
        "estimated_seconds_remaining": eta,
        "estimate_basis": "median of at least two previous capability timings; omitted when history is insufficient" if eta is not None else None,
        "available_capabilities": capabilities,
        "unavailable_capabilities": unavailable,
        "resolve": broker_reader(),
        "updated_at": status.get("updated_at"),
    }


def record_progress_event(project: str, step_id: str, outcome: str) -> dict | None:
    """Append one plain-language progress event to the run record.

    The runner calls this at each step boundary, AFTER it has written
    the boundary's own status, so `progress_report` reads the run as
    the step left it. `outcome` is the boundary in one word -
    `completed`, `reused`, `failed`, `awaiting_llm`, `paused` or `held`.

    Best-effort like every other status write: a progress line must
    never take down a real run. When the report itself cannot be built,
    the event still lands with a fallback message rather than nothing -
    a boundary with no record is how a step "ran" and nobody can say
    when.
    """
    try:
        report = progress_report(project)
        message = str(report.get("message") or "")
        done = len(report.get("done") or [])
        remaining = int(report.get("remaining_steps") or 0)
    except Exception:  # noqa: BLE001 - the event matters more than the prose
        message = f"Step {step_id} {outcome}."
        done = 0
        remaining = 0
    event = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "step": step_id,
        "outcome": outcome,
        "message": message,
        "steps_done": done,
        "steps_remaining": remaining,
        "stage": _stage(step_id),
        "why": _stage_reason(_stage(step_id), step_id),
        "evidence": [],
        "next_action": _next_action(outcome),
    }
    try:
        from library.tools.project_layout import ProjectLayout
        output = ProjectLayout(project).step_dir(step_id, "output.json")
        if output.is_file():
            event["evidence"] = [{"kind": "step_output", "path": str(output)}]
    except (KeyError, ValueError, OSError):
        pass
    try:
        current = run_control.read_run_status(str(project))
        events = list(current.get(PROGRESS_EVENTS_KEY) or [])
        events.append(event)
        del events[:-MAX_PROGRESS_EVENTS]
        run_control.write_run_status(str(project),
                                     **{PROGRESS_EVENTS_KEY: events})
        return event
    except Exception:  # noqa: BLE001 - a progress write never fails a run
        return None


def main(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(description="Read plain-language progress for a Ren project")
    parser.add_argument("project", help="project slug or absolute path")
    args = parser.parse_args(argv)
    from library.tools.project_registry import get_project, warn_config_findings
    config = get_project(args.project, strict=False)
    warn_config_findings(config)
    print(json.dumps(progress_report(str(config.project_root)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE,
        RenRefusal,
        emit_refusal_result,
        report_unexpected,
    )
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
