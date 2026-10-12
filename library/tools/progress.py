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
                str(current_started), "%Y-%m-%dT%H:%M:%S").timestamp()
            elapsed = max(0.0, time.time() - started)
            remaining_estimates.append(max(0.0, medians[running[0]] - elapsed))
        except (TypeError, ValueError):
            remaining_estimates.append(medians[running[0]])
    eta = (round(sum(remaining_estimates))
           if estimate_supported and remaining_ids else None)

    titles = lambda ids: [by_id[step]["name"] for step in ids]
    index_file = root / "pipeline_output" / "scratch" / "footage_index" / "footage_index.json"
    capabilities = []
    if index_file.is_file():
        capabilities.append({"id": "footage.search", "label": "Search the indexed footage and dialogue with `ren search`."})
    else:
        from library.tools.project_layout import ProjectLayout
        temporal_output = ProjectLayout(root).step_dir(
            "temporal_index", "output.json")
        temporal_ready = temporal_output.is_file()
    if not index_file.is_file() and temporal_ready:
        capabilities.append({"id": "footage.search_index", "label": "Dialogue analysis is available; build its searchable index with `ren search-index`."})

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

    return {
        "project": str(root), "status": state, "message": message,
        "done": titles(done_ids), "running": titles(running),
        "up_next": titles(pending[:3]), "remaining_steps": len(pending),
        "estimated_seconds_remaining": eta,
        "estimate_basis": "median of at least two previous capability timings; omitted when history is insufficient" if eta is not None else None,
        "available_capabilities": capabilities,
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
    }
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
    parser = argparse.ArgumentParser(description="Read plain-language progress for a Ren project")
    parser.add_argument("project", help="project slug or absolute path")
    args = parser.parse_args(argv)
    from library.tools.project_registry import get_project
    config = get_project(args.project)
    print(json.dumps(progress_report(str(config.project_root)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
