"""Event log: the Ren Fabric's event-sourced operation record.

The spec's "everything should be event-sourced" (ren-fabric-spec
2026-10-06): every operation a worker performs - lease acquired, lease
released, N timeline operations, preview rendered, QC rejected/passed,
branch ready for review - is an event in one append-only log per
project, so a worker that disappears leaves a readable account of what
it did, and a resumed task can continue from the last checkpoint.

One canonical writer
-------------------
`emit` is the only way an event reaches the log. Every worker reads
the same file, so the log is the shared state agents communicate
through rather than messages passed between them. A writer that
bypasses `emit` - appending to the file directly, or writing a second
log - breaks the one property the resume path depends on: that the
whole history of a project's operations is in one place, in order.

Append-only
-----------
Events are never rewritten or deleted. A reader sees the whole history
of a project's operations, which is what makes resume possible - a task
killed mid-way left every operation it completed on disk, and the next
worker reads them back. The file is JSON Lines: one event per line,
each line a complete JSON object, so a reader can walk it without
parsing the whole file and a truncated tail (a killed writer's last
line) is skipped rather than fatal.

Checkpoints
----------
A task that can be resumed writes a `checkpoint` event naming its task
id and the state it has reached. `last_checkpoint` reads the newest
one for a task id, so a resumed worker continues from where the killed
one stopped rather than from the beginning. A checkpoint is an event
like any other - it is written through `emit`, so it carries a
sequence number and appears in the log's order, which is what lets a
reader tell "the checkpoint says step 3" from "step 3's events are
still in the log after it".

Sequence numbers
----------------
Every event carries a per-file sequence number, assigned under the
project file lock so two workers emitting at once never share one. A
reader can detect a gap (a lost event) and a resumed worker can read
from a known sequence rather than re-reading the whole file.

Reachability
------------
    python -m library.tools.event_log <project> [--task <id>] [--since <seq>]

`tests/unit/resolve/test_event_log.py`.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from library.tools.project_file_lock import lock_project_file

EVENT_LOG_FILE = "event_log.jsonl"
"""The log's filename, in the project's LOGS area beside
`perf_ledger.jsonl` - one append-only JSON Lines file per project."""

EVENT_LOG_ENV = "REN_EVENT_LOG"
"""An environment variable pointing directly at the log file, for a
writer that knows the log's path but not the project folder - the
Resolve lease, which is machine-wide and called from 42 places that
do not all carry a project folder. The runner sets it when it knows
the project; a writer that has neither the env var nor a project
folder emits nothing, which is the honest answer."""

CHECKPOINT = "checkpoint"
"""The event type that marks a task's resumable state."""

LEASE_ACQUIRED = "lease_acquired"
LEASE_RELEASED = "lease_released"
LEASE_EXPIRED = "lease_expired"
INSTANCE_LEASE_ACQUIRED = "instance_lease_acquired"
INSTANCE_LEASE_RELEASED = "instance_lease_released"
REEL_SIGNED_OFF = "reel_signed_off"
"""A built reel's durable approval, the spec's QC-passed event."""
SIGNOFF_WITHDRAWN = "signoff_withdrawn"
"""An approval taken back, the spec's QC-rejected event."""
BRANCH_CREATED = "branch_created"
"""A task worktree opened on a new `ren/<task>` branch."""
BRANCH_COMMITTED = "branch_committed"
"""A task worktree's allow-listed text committed."""
TIMELINE_OPERATIONS = "timeline_operations"
"""The N operations one committed patch ran, with its verdict."""


def log_path(project_folder) -> Path:
    """Where this project's event log lives: `pipeline_output/logs/event_log.jsonl`."""
    from library.tools.project_layout import Area, ProjectLayout
    return ProjectLayout(project_folder).read_dir(Area.LOGS) / EVENT_LOG_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _host() -> str:
    return socket.gethostname()


def _read_last_seq(path: Path) -> int:
    """The sequence number of the last complete line, or 0.

    A killed writer may leave a partial last line; it is skipped, not
    fatal - the events before it are the worker's real history.

    Reads only the tail of the file, not the whole log: the log is
    append-only and unbounded, so reading every line to find the last
    sequence number would make each emit slower forever. Seeks back
    from EOF to the last newline and reads only that chunk.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            if size == 0:
                return 0
            # Read back up to 64KB - far more than any single event.
            chunk_size = min(size, 65536)
            handle.seek(size - chunk_size)
            chunk = handle.read(chunk_size).decode("utf-8", errors="replace")
    except OSError:
        return 0
    last = 0
    for line in chunk.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("seq"), int):
            last = event["seq"]
    return last


def emit(project_folder, event_type: str, task: str = "",
         **fields: Any) -> dict:
    """Append one event to the project's log. The canonical writer.

    `event_type` is what happened (`lease_acquired`, `checkpoint`, ...).
    `task` is the worker's own task id - the name the spec's example
    uses (`ren-04`, `qc-02`) - so a reader can filter one worker's
    operations. `**fields` are the event's own data: what was acquired,
    what the checkpoint's state is, why a lease expired.

    Returns the event as written, so a caller can read back its
    sequence number. The write is serialized by the project file lock
    so two workers emitting at once never share a sequence number,
    and the append is one `write` on an `O_APPEND` handle, so a reader
    never sees half a line.
    """
    path = log_path(project_folder)
    return emit_path(path, event_type, task=task, **fields)


def emit_path(path, event_type: str, task: str = "", **fields: Any) -> dict:
    """Append one event to a log at a direct path. The canonical writer
    for a caller that knows the log's path but not the project folder -
    the Resolve lease, which is machine-wide and called from places
    that do not all carry a project folder. The runner sets
    `REN_EVENT_LOG` when it knows the project.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with lock_project_file(path):
        seq = _read_last_seq(path) + 1
        event = {
            "seq": seq,
            "at": _now(),
            "host": _host(),
            "pid": os.getpid(),
            "type": event_type,
            "task": task,
            "fields": fields,
        }
        line = json.dumps(event, sort_keys=True,
                          ensure_ascii=True) + "\n"
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line)
    return event


def read_events(project_folder, task: str = "",
                since: int = 0) -> List[dict]:
    """Read the project's events, oldest first.

    `task` filters to one worker's operations; `since` filters to
    events after a sequence number, which is how a resumed worker
    reads only what it has not seen. A partial last line (a killed
    writer's tail) is skipped, not fatal - the events before it are
    the worker's real history.
    """
    path = log_path(project_folder)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    events = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if task and event.get("task") != task:
            continue
        if since and (event.get("seq") or 0) <= since:
            continue
        events.append(event)
    return events


def last_checkpoint(project_folder, task: str) -> Optional[dict]:
    """The newest checkpoint event for a task, or None.

    A resumed worker calls this to find where the killed one stopped.
    None means the task never checkpointed - the worker starts from
    the beginning, which is the honest answer rather than a invented
    state.
    """
    path = log_path(project_folder)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return None
    found = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") != CHECKPOINT:
            continue
        if task and event.get("task") != task:
            continue
        found = event
    return found


def checkpoint(project_folder, task: str, **state: Any) -> dict:
    """Write a checkpoint event: the state a resumed task continues from.

    The checkpoint is an event like any other - written through
    `emit`, so it carries a sequence number and appears in the log's
    order. A reader can therefore tell "the checkpoint says step 3"
    from "step 3's events are still in the log after it", which is
    what makes the resume honest rather than a guess.
    """
    return emit(project_folder, CHECKPOINT, task=task, state=state)


def _main(argv=None) -> int:
    """`python -m library.tools.event_log <project> [--task <id>] [--since <seq>]."""
    parser = argparse.ArgumentParser(
        prog="python -m library.tools.event_log",
        description="Read a project's event log: every operation its "
                    "workers performed, oldest first.")
    parser.add_argument("project", help="the project folder")
    parser.add_argument("--task", default="",
                        help="only this worker's events")
    parser.add_argument("--since", type=int, default=0,
                        help="only events after this sequence number")
    args = parser.parse_args(argv)
    events = read_events(args.project, task=args.task, since=args.since)
    for event in events:
        fields = event.get("fields") or {}
        summary = " ".join(f"{k}={v}" for k, v in sorted(fields.items()))
        print(f"{event.get('seq')} {event.get('at')} "
              f"{event.get('task', '')} {event.get('type')} {summary}")
    if not events:
        print("event log: no events - no worker has operated on this project.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
