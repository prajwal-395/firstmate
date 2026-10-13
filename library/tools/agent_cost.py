"""Privacy-safe agent-cost counters: aggregates only, never conversation text.

A host (Claude, opencode, a future MCP server) reports what one task cost
it - how many tool calls it made, how many bytes went each way, how long
it took - as aggregate NUMBERS. This module records exactly one aggregate
row per session into the performance ledger (`perf_ledger.jsonl`):

    {kind: "agent_task", host, task_class, tool_calls,
     input_bytes, output_bytes, wall_s}

No prompt text, no tool arguments, no tool names, no response content is
accepted by this API - there is no parameter for it, so none can be
persisted. `ren profile --agent` summarizes only these rows and names
any field it could not measure (`unmeasured`) instead of guessing zero.

Opt-in: nothing records unless the host explicitly calls `commit_session`
(or the `session` context manager below). The pipeline never parses raw
Claude/opencode conversation logs.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Self

AGENT_KIND = "agent_task"

# What an aggregate row may carry. Anything else - tool names, arguments,
# prompts, responses - is refused by `commit_session`.
_ROW_FIELDS = frozenset({
    "kind", "host", "task_class", "tool_calls",
    "input_bytes", "output_bytes", "wall_s",
    "run_id", "started_at",
})


def _check_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _check_count(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int or None")
    return value


def _check_wall(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("wall_s must be a non-negative number or None")
    if float(value) < 0:
        raise ValueError("wall_s must be a non-negative number or None")
    return round(float(value), 3)


def commit_session(ledger_path, *, host: str, task_class: str,
                   tool_calls: int | None = None,
                   input_bytes: int | None = None,
                   output_bytes: int | None = None,
                   wall_s: float | None = None,
                   run_id: str = "",
                   started_at: float | None = None,
                   **rejected: Any) -> dict[str, Any]:
    """Write exactly one aggregate agent-cost row; return the row written.

    Only aggregate numbers are accepted. Any extra keyword - a prompt, a
    tool name, arguments, response text - raises `TypeError`, so content
    can never reach the ledger through this path.
    """
    if rejected:
        unexpected = sorted(rejected)
        raise TypeError(
            "agent-cost rows carry aggregates only; refused "
            f"content-bearing field(s): {', '.join(unexpected)}")
    host = _check_str("host", host)
    task_class = _check_str("task_class", task_class)
    row: dict[str, Any] = {
        "kind": AGENT_KIND,
        "host": host,
        "task_class": task_class,
        "tool_calls": _check_count("tool_calls", tool_calls),
        "input_bytes": _check_count("input_bytes", input_bytes),
        "output_bytes": _check_count("output_bytes", output_bytes),
        "wall_s": _check_wall(wall_s),
        "run_id": str(run_id or ""),
        "started_at": round(started_at if started_at is not None
                            else time.time(), 3),
    }
    from library.tools import perf_ledger
    perf_ledger.commit_to_path(ledger_path, [row])
    return dict(row)


def commit_call_events(ledger_path, *, host: str, task_class: str,
                       events: list[dict[str, Any]],
                       run_id: str = "",
                       started_at: float | None = None) -> dict[str, Any]:
    """Aggregate one session's per-call measurements into one row.

    Each event carries only numbers - `input_bytes`, `output_bytes`,
    `wall_s` - and any content-bearing key raises, as in
    `commit_session`. The ledger receives exactly one row whose totals
    are the exact sums of the events.
    """
    if not isinstance(events, list) or not events:
        raise ValueError("events must be a non-empty list")
    total_in: int | None = 0
    total_out: int | None = 0
    total_wall = 0.0
    wall_measured = True
    for event in events:
        if not isinstance(event, dict):
            raise TypeError("each event must be a dict of numbers")
        extra = set(event) - {"input_bytes", "output_bytes", "wall_s"}
        if extra:
            raise TypeError(
                "agent-cost events carry aggregates only; refused "
                f"content-bearing field(s): {', '.join(sorted(extra))}")
        in_b = _check_count("input_bytes", event.get("input_bytes", 0))
        out_b = _check_count("output_bytes", event.get("output_bytes", 0))
        wall = event.get("wall_s", 0.0)
        if in_b is None or out_b is None:
            raise ValueError(
                "event input_bytes/output_bytes must be measured ints")
        total_in = (total_in or 0) + in_b
        total_out = (total_out or 0) + out_b
        if wall is None:
            wall_measured = False
        else:
            total_wall += float(_check_wall(wall) or 0.0)
    return commit_session(
        ledger_path, host=host, task_class=task_class,
        tool_calls=len(events), input_bytes=total_in,
        output_bytes=total_out,
        wall_s=round(total_wall, 3) if wall_measured else None,
        run_id=run_id, started_at=started_at)


class session:
    """Accumulate one host session's tool-call costs, then write one row.

    Usage::

        with agent_cost.session(path, host="synthetic",
                                task_class="reel_state") as s:
            s.record_call(input_bytes=120, output_bytes=340, wall_s=0.5)
            ...

    The context writes exactly one aggregate row on clean exit and
    nothing when the block raises. Only numbers are recorded.
    """

    def __init__(self, ledger_path, *, host: str, task_class: str,
                 run_id: str = "") -> None:
        self._ledger_path = Path(ledger_path)
        self._host = _check_str("host", host)
        self._task_class = _check_str("task_class", task_class)
        self._run_id = str(run_id or "")
        self._events: list[dict[str, Any]] = []
        self._started_at = round(time.time(), 3)
        self.row: dict[str, Any] | None = None

    def record_call(self, *, input_bytes: int = 0, output_bytes: int = 0,
                    wall_s: float = 0.0) -> None:
        self._events.append({
            "input_bytes": _check_count("input_bytes", input_bytes),
            "output_bytes": _check_count("output_bytes", output_bytes),
            "wall_s": _check_wall(wall_s),
        })

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        if exc_type is not None or not self._events:
            return
        self.row = commit_call_events(
            self._ledger_path, host=self._host,
            task_class=self._task_class, events=[
                {"input_bytes": e["input_bytes"],
                 "output_bytes": e["output_bytes"],
                 "wall_s": e["wall_s"]} for e in self._events],
            run_id=self._run_id, started_at=self._started_at)


def is_agent_row(row: dict[str, Any]) -> bool:
    """Whether a ledger row is an agent-cost aggregate (never inspects content)."""
    return isinstance(row, dict) and row.get("kind") == AGENT_KIND


def summarize(rows: list[dict[str, Any]],
              run_id: str | None = None) -> dict[str, Any]:
    """Group agent-cost rows by (host, task_class) into exact totals.

    A field no row measured stays `None` and renders as `unmeasured` -
    never as zero. Only aggregate keys appear; tool arguments and
    content cannot occur because no writer accepts them.
    """
    grouped: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        if not is_agent_row(row):
            continue
        if run_id is not None and str(row.get("run_id") or "") != run_id:
            continue
        key = (str(row.get("host")), str(row.get("task_class")))
        bucket = grouped.setdefault(key, {
            "host": row.get("host"), "task_class": row.get("task_class"),
            "sessions": 0, "tool_calls": 0, "input_bytes": 0,
            "output_bytes": 0, "wall_s": 0.0,
            "_calls_measured": True, "_in_measured": True,
            "_out_measured": True, "_wall_measured": True,
        })
        bucket["sessions"] += 1
        for source, dest, flag in (
                ("tool_calls", "tool_calls", "_calls_measured"),
                ("input_bytes", "input_bytes", "_in_measured"),
                ("output_bytes", "output_bytes", "_out_measured"),
                ("wall_s", "wall_s", "_wall_measured")):
            value = row.get(source)
            if value is None:
                bucket[flag] = False
            else:
                bucket[dest] = round(bucket[dest] + float(value), 3) \
                    if dest == "wall_s" else bucket[dest] + int(value)
    groups = []
    for key in sorted(grouped):
        bucket = grouped[key]
        groups.append({
            "host": bucket["host"], "task_class": bucket["task_class"],
            "sessions": bucket["sessions"],
            "tool_calls": bucket["tool_calls"]
            if bucket["_calls_measured"] else None,
            "input_bytes": bucket["input_bytes"]
            if bucket["_in_measured"] else None,
            "output_bytes": bucket["output_bytes"]
            if bucket["_out_measured"] else None,
            "wall_s": bucket["wall_s"]
            if bucket["_wall_measured"] else None,
        })
    return {"status": "ok" if groups else "unmeasured",
            "groups": groups,
            "sessions": sum(g["sessions"] for g in groups)}
