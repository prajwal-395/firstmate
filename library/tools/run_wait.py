"""Events instead of sleep loops: `ren wait <project>` blocks until a run needs its host.

A detached run (`ren edit --detach`) leaves the shell at once, and the
host that launched it needs to know when to act again without polling
in a `sleep` loop (measured: 304 sleep calls / 1,004 min of tool wall
in 10 days). This module is the other half: it blocks until the FIRST
of the following, prints ONE JSON object with `next_command`, and
exits 0 in every event case:

- `ask` - a new `llm_requests/*.json` with no response (path and kind)
- `review_gate` - a pending review gate (`review_gate.list_pending_gates`)
- `hold` - `pipeline.hold` is engaged
- `ended` - no live run (SUCCESS/FAILED/REFUSED and the summary's
  reason where the status file carries one)
- `lock_wait` - a heavy-work wait started (LE-01's `heavy_work_wait`
  record on the run status)
- `still_running` - the timeout lapsed (current step and elapsed time)

The default timeout (100 s) sits under the 120 s harness default.
T5's `progress_events` are carried on the `still_running`/`lock_wait`
payloads when the run wrote them; they are never required.

Read only: never calls a model or Resolve, never writes.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

from library.tools import run_control
from library.tools.llm_handshake import REQUESTS_SUBDIR, RESPONSES_SUBDIR

#: The default wait, under the 120 s harness default (LE-10).
DEFAULT_TIMEOUT_S = 100.0

#: How often the project files are re-read while waiting.
POLL_INTERVAL_S = 0.25

EVENTS = ("ask", "review_gate", "hold", "ended", "lock_wait", "still_running")


def _request_files(root: Path) -> list[Path]:
    """Every request file on disk, oldest first; temp files excluded."""
    directory = root / REQUESTS_SUBDIR
    try:
        files = [
            p
            for p in directory.iterdir()
            if p.is_file() and p.suffix == ".json" and not p.name.startswith(".")
        ]
    except OSError:
        return []
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0.0)
    return files


def pending_asks(project_root: str) -> list[dict]:
    """Every `llm_requests/*.json` with no response, oldest first.

    Each row is `{request, step, kind}`. A request whose JSON cannot
    be read is skipped on this poll rather than reported - the writer
    publishes atomically (`os.replace`), so an unreadable file is a
    reader that arrived mid-publish, not an ask.
    """
    root = Path(project_root)
    responses = root / RESPONSES_SUBDIR
    rows = []
    for request_path in _request_files(root):
        if (responses / request_path.name).is_file():
            continue
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        rows.append(
            {
                "request": str(request_path),
                "step": str(payload.get("step_id") or request_path.stem),
                "kind": str(payload.get("kind") or "llm_step"),
            }
        )
    return rows


def _elapsed_s(status: dict) -> float | None:
    """Seconds since `started_at`, or None where it is absent."""
    try:
        started = datetime.datetime.strptime(
            str(status.get("started_at")), "%Y-%m-%dT%H:%M:%S"
        ).astimezone().timestamp()
    except (TypeError, ValueError):
        return None
    return max(0.0, round(time.time() - started, 1))


def _detached(status: dict) -> dict:
    detached = status.get("detached")
    return dict(detached) if isinstance(detached, dict) else {}


def _latest_progress_message(status: dict) -> str | None:
    """T5's newest progress event message, when the run wrote any."""
    events = status.get("progress_events")
    if not isinstance(events, list) or not events:
        return None
    last = events[-1]
    if not isinstance(last, dict):
        return None
    message = last.get("message")
    return str(message) if message else None


def _latest_progress_event(status: dict) -> dict | None:
    events = status.get("progress_events")
    if not isinstance(events, list) or not events or not isinstance(events[-1], dict):
        return None
    return dict(events[-1])


def _base(project_ref: str, root: Path, status: dict, pid: int | None) -> dict:
    detached = _detached(status)
    return {
        "project": str(root),
        "run_id": status.get("run_id") or detached.get("run_id"),
        "pid": pid if pid is not None else detached.get("pid"),
        "log": detached.get("log"),
        "current_step": status.get("current_step"),
        "elapsed_s": _elapsed_s(status),
    }


def _finished(status: dict) -> bool:
    """Whether the run wrote an ending (whatever it said)."""
    return bool(status.get("finished_at") or status.get("summary_status"))


def _interrupted(status: dict) -> bool:
    """A recorded run with no live pid and no ending written."""
    return bool(status) and not _finished(status)


def _ended_status(status: dict) -> str:
    value = status.get("summary_status") or status.get("status") or ""
    return str(value).upper() or "UNKNOWN"


def _ended_reason(status: dict) -> str:
    held = status.get("held_before_step")
    if held:
        return f"held before {held}"
    gate = status.get("paused_at_gate")
    if gate:
        return f"paused at review gate {gate}"
    return ""


def poll_once(project_ref: str, project_root: str) -> dict | None:
    """One check for the first event in LE-10 order, or None to keep waiting.

    Order: ask, review gate, hold, ended (no live run), lock wait.
    A stale ask on a finished run does not mask its ending, but an
    ask on an `AWAITING_LLM` run still fires: answering it is what
    resumes that run.
    """
    root = Path(project_root)
    status = run_control.read_run_status(project_root)
    pid = run_control.running_pid(project_root)
    alive = pid is not None
    finished = _finished(status)
    summary = str(status.get("summary_status") or "").upper()

    asks = pending_asks(project_root)
    if asks and (alive or not finished or summary == "AWAITING_LLM"):
        first = asks[0]
        event = _base(project_ref, root, status, pid)
        event.update(
            {
                "event": "ask",
                "request": first["request"],
                "step": first["step"],
                "kind": first["kind"],
                "pending_asks": len(asks),
                "next_command": f"ren wait {project_ref}",
                "detail": (
                    f"step {first['step']} asks ({first['kind']}): "
                    f"read it with `ren asks {project_ref}` (each shared "
                    f"prompt once; the raw {first['request']} carries the "
                    f"prompt only by reference), write its response, "
                    f"then `ren wait {project_ref}` again"
                ),
            }
        )
        return event

    try:
        from library.tools import review_gate

        gates = review_gate.list_pending_gates(project_root)
    except (OSError, ValueError):
        gates = []
    if gates:
        event = _base(project_ref, root, status, pid)
        event.update(
            {
                "event": "review_gate",
                "gates": list(gates),
                "next_command": (
                    "python3 -m library.tools.review_gate show "
                    f"--project {root} --step {gates[0]}"
                ),
                "detail": (
                    f"review gate pending on {gates[0]}: inspect it, "
                    f"then answer (approve/reject/revise)"
                ),
            }
        )
        return event

    hold = run_control.hold_requested(project_root)
    if hold is not None:
        event = _base(project_ref, root, status, pid)
        event.update(
            {
                "event": "hold",
                "hold": hold,
                "next_command": f"ren resume {project_ref}",
                "detail": "the handbrake is engaged; resume when ready",
            }
        )
        return event

    if not alive:
        if not status:
            # No run recorded yet (a detached child that has not
            # written its status, or no run at all): that is "nothing
            # yet", not an ending. Keep waiting for the timeout.
            return None
        event = _base(project_ref, root, status, pid)
        if _interrupted(status):
            latest = _latest_progress_event(status)
            event.update(
                {
                    "event": "ended",
                    "status": "INTERRUPTED",
                    "reason": (
                        "no live run and no ending was written - "
                        "the run was interrupted"
                    ),
                    "finished_at": status.get("finished_at"),
                    "pending_asks": len(asks),
                    "progress": latest.get("message") if latest else None,
                    "progress_event": latest,
                    "interruption": {
                        "detected": True,
                        "reason": "The process ended without writing a run outcome.",
                        "last_completed_step": status.get("last_completed_step"),
                    },
                    "retry": {
                        "state": "not_started",
                        "guidance": "Check `ren status` and the run log before resuming; Ren cannot tell whether the interrupted step was safe to repeat.",
                    },
                    "next_command": f"ren status {project_ref}",
                    "detail": "The run was interrupted before recording an outcome. Check its last progress event and log before deciding whether to resume.",
                }
            )
            return event
        event.update(
            {
                "event": "ended",
                "status": _ended_status(status),
                "reason": _ended_reason(status),
                "finished_at": status.get("finished_at"),
                "pending_asks": len(asks),
                "next_command": f"ren status {project_ref}",
                "detail": "no live run; read its outcome",
            }
        )
        return event

    lock_wait = status.get("heavy_work_wait")
    if lock_wait:
        holders = []
        message = ""
        if isinstance(lock_wait, dict):
            holders = lock_wait.get("holders") or []
            message = str(lock_wait.get("message") or "")
        event = _base(project_ref, root, status, pid)
        event.update(
            {
                "event": "lock_wait",
                "holders": holders,
                "message": message,
                "progress": _latest_progress_message(status),
                "progress_event": _latest_progress_event(status),
                "next_command": f"ren wait {project_ref}",
                "detail": message or "the run waits on the heavy-work lock",
            }
        )
        return event

    return None


def wait_for_event(
    project_ref: str,
    project_root: str,
    timeout: float = DEFAULT_TIMEOUT_S,
    poll_interval: float = POLL_INTERVAL_S,
) -> dict:
    """Block until the first event or the timeout; always an event dict."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        event = poll_once(project_ref, project_root)
        if event is not None:
            return event
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            root = Path(project_root)
            status = run_control.read_run_status(project_root)
            pid = run_control.running_pid(project_root)
            event = _base(project_ref, root, status, pid)
            event.update(
                {
                    "event": "still_running",
                    "timeout_s": float(timeout),
                    "progress": _latest_progress_message(status),
                    "progress_event": _latest_progress_event(status),
                    "next_command": f"ren wait {project_ref}",
                    "detail": "nothing needs the host yet; wait again",
                }
            )
            return event
        time.sleep(min(poll_interval, remaining))


def resolve_root(project_ref: str) -> Path:
    """The project root for a slug or path; read-only (LE-02 lenient)."""
    from library.tools.project_registry import get_project

    config = get_project(project_ref, strict=False)
    return Path(str(config.project_root))


def main(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    # A `ren` verb already set it through the environment, and an
    # explicit value always wins.
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren wait",
        description="Wait for the next thing a pipeline run needs "
        "(an ask, a gate, a hold, the end) and print it as "
        "one JSON object. Exits 0 in every event case.",
    )
    parser.add_argument("project", help="project slug or path")
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_S,
        help="seconds to wait (default 100, under the 120 s harness default)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the event object indented instead of on one line",
    )
    args = parser.parse_args(argv)
    try:
        root = resolve_root(args.project)
    except FileNotFoundError as exc:
        from library.tools.ren_refusal import (
            REFUSAL_EXIT_CODE,
            RenRefusal,
            emit_refusal_result,
        )

        refused = RenRefusal(
            f"{exc}",
            "no project with this slug is under PIPELINE_PROJECTS_ROOT",
            f"run `ren new {args.project}` to create it, `ren projects` "
            f"to list them, or pass the project's absolute path "
            f"instead of the slug",
            blocker="missing_media",
            next_action="supply_the_missing_media",
        )
        print(refused.render(), file=__import__("sys").stderr)
        emit_refusal_result(refused)
        return REFUSAL_EXIT_CODE
    event = wait_for_event(args.project, str(root), timeout=args.timeout)
    if args.json:
        print(json.dumps(event, indent=2, ensure_ascii=False, default=str))
    else:
        print(json.dumps(event, ensure_ascii=False, default=str))
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
