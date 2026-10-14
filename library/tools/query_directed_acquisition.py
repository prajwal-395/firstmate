"""Query-directed, bounded acquisition of missing project evidence.

Planning is deliberately separate from running producers: callers first offer
the cheap cache lookup, then an interval-aware producer. A query that is
already answered (or cannot be changed by an available producer) schedules no
work. Producer callbacks execute serially for one source and report their
measured cost and peak RSS in the returned mapping.
"""

from __future__ import annotations

import fcntl
import hashlib
import math
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from library.tools import source_memory
from library.tools.evidence_query import (
    CONFIDENTLY_ABSENT,
    NOT_YET_ANALYSED,
    PRESENT,
)
from library.tools.heavy_work_lock import heavy_work_lock

SCHEMA_VERSION = 1
DEFAULT_RSS_CEILING_GB = 8.0
_TERMINAL_STATUSES = {PRESENT, CONFIDENTLY_ABSENT}


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a finite non-negative number") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return number


def _interval(start: Any, end: Any, duration: float) -> dict[str, float]:
    try:
        left, right = float(start), float(end)
    except (TypeError, ValueError):
        raise ValueError("acquisition intervals require numeric start_s/end_s") from None
    if not math.isfinite(left) or not math.isfinite(right) or left < 0 or right <= left:
        raise ValueError("acquisition intervals must be finite, non-empty and non-negative")
    left, right = min(duration, left), min(duration, right)
    if right <= left:
        raise ValueError("acquisition interval falls outside the source")
    return {"start_s": round(left, 3), "end_s": round(right, 3)}


def plan_query_acquisition(
    answer: Mapping[str, Any],
    query_plan: Mapping[str, Any],
    *,
    source_digest: str,
    duration_seconds: float,
    resolvable_ambiguities: Sequence[Mapping[str, Any]] = (),
    available_lanes: Sequence[str] | None = None,
    producer_budgets: Mapping[str, float] | None = None,
    window_seconds_by_lane: Mapping[str, float] | None = None,
    producer_cost_per_window_seconds: Mapping[str, float] | None = None,
    max_cost_seconds: float | None = None,
    max_peak_rss_gb: float = DEFAULT_RSS_CEILING_GB,
) -> dict[str, Any]:
    """Return only query-relevant lane/window work, or an explicit no-op.

    An ambiguity row names ``lane``, ``start_s`` and ``end_s``. It is accepted
    only when that lane is required by a query predicate. A missing lane uses
    the query's explicit source interval; an unscoped query needs the full
    source to establish its answer. Budget checks happen before any callback
    can decode or load a model.
    """
    duration = _finite_nonnegative(duration_seconds, "duration_seconds")
    if not isinstance(source_digest, str) or not source_digest.strip():
        raise ValueError("source_digest must identify the source being queried")
    if duration <= 0:
        raise ValueError("duration_seconds must be positive")
    rss_ceiling = _finite_nonnegative(max_peak_rss_gb, "max_peak_rss_gb")
    if rss_ceiling > DEFAULT_RSS_CEILING_GB:
        raise ValueError("max_peak_rss_gb cannot exceed the VI-12 8 GB ceiling")
    cost_ceiling = (None if max_cost_seconds is None else
                    _finite_nonnegative(max_cost_seconds, "max_cost_seconds"))
    status = answer.get("status")
    required = set(query_plan.get("required_lanes") or ())
    if status in _TERMINAL_STATUSES:
        reason = "the current answer cannot be changed by acquiring more evidence"
        return _no_run(reason, status)
    if status not in (NOT_YET_ANALYSED, "inconclusive"):
        return _no_run("the query state does not authorize evidence acquisition", status)

    supported = set(available_lanes) if available_lanes is not None else required
    missing = set(answer.get("required_missing_lanes") or ())
    lanes_to_run = required & (missing if status == NOT_YET_ANALYSED else required)
    lanes_to_run &= supported
    if not lanes_to_run:
        return _no_run("no required evidence lane is available to change the answer",
                       status)

    scope = query_plan.get("scope") or {}
    if scope.get("start_s") is not None and scope.get("end_s") is not None:
        default_interval = _interval(scope["start_s"], scope["end_s"], duration)
    else:
        default_interval = {"start_s": 0.0, "end_s": round(duration, 3)}

    intervals: dict[str, list[dict[str, float]]] = {lane: [] for lane in lanes_to_run}
    for lane in sorted(lanes_to_run):
        intervals[lane].append(default_interval)
    if status == "inconclusive":
        # Inconclusive evidence is actionable only when the caller can name
        # the specific ambiguity an available lane can resolve.
        intervals = {lane: [] for lane in lanes_to_run}
        for item in resolvable_ambiguities:
            lane = item.get("lane")
            if lane in lanes_to_run:
                intervals[lane].append(_interval(
                    item.get("start_s"), item.get("end_s"), duration))
        intervals = {lane: spans for lane, spans in intervals.items() if spans}
        if not intervals:
            return _no_run("no named ambiguity is resolvable by a required lane", status)

    budgets = producer_budgets or {}
    estimated_cost = 0.0
    steps = []
    for lane in sorted(intervals):
        if lane not in budgets:
            return _no_run(f"producer budget is not declared for lane {lane}",
                           status)
        lane_budget = _finite_nonnegative(budgets[lane], f"{lane} producer budget")
        lane_windows = _merge(intervals[lane])
        window_size = (window_seconds_by_lane or {}).get(lane)
        if window_size is not None:
            window_size = _finite_nonnegative(window_size, f"{lane} window size")
            if window_size <= 0:
                raise ValueError(f"{lane} window size must be positive")
            aligned = []
            for row in lane_windows:
                start = math.floor(row["start_s"] / window_size) * window_size
                while start < row["end_s"]:
                    aligned.append({"start_s": round(start, 3),
                                    "end_s": round(min(start + window_size,
                                                       duration), 3)})
                    start += window_size
            lane_windows = aligned
        for window in lane_windows:
            cost_budget = (float((producer_cost_per_window_seconds or {}).get(lane,
                                                                            lane_budget))
                           if window_size is not None else lane_budget *
                           ((window["end_s"] - window["start_s"]) / duration))
            steps.append({"lane": lane, "interval": window,
                          "cost_budget_seconds": round(cost_budget, 3),
                          "order": ["cached", "targeted"]})
            estimated_cost += cost_budget
    if cost_ceiling is not None and estimated_cost > cost_ceiling:
        return _no_run("estimated producer cost exceeds the query budget", status,
                       estimated_cost_seconds=round(estimated_cost, 3))
    return {"schema_version": SCHEMA_VERSION, "action": "refine",
            "source_digest": source_digest,
            "source_serial": True, "rss_ceiling_gb": rss_ceiling,
            "estimated_cost_seconds": round(estimated_cost, 3),
            "required_lanes": sorted(intervals), "steps": steps,
            "deferred_lanes": sorted(required - supported)}


def _merge(windows: Sequence[Mapping[str, float]]) -> list[dict[str, float]]:
    merged: list[list[float]] = []
    for row in sorted(windows, key=lambda w: (w["start_s"], w["end_s"])):
        start, end = float(row["start_s"]), float(row["end_s"])
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [{"start_s": round(a, 3), "end_s": round(b, 3)} for a, b in merged]


def _no_run(reason: str, status: Any, **extra: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "action": "no_run",
            "answer_status": status, "reason": reason, "steps": [], **extra}


@contextmanager
def _source_lock(source_digest: str):
    """Serialize refinements for one content identity across processes."""
    lock_root = source_memory.memory_root() / ".query-acquisition-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(source_digest.encode("utf-8")).hexdigest() + ".lock"
    with (lock_root / name).open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def execute_query_acquisition(
    plan: Mapping[str, Any],
    *,
    cached: Callable[..., Mapping[str, Any] | None],
    producers: Mapping[str, Callable[..., Mapping[str, Any]]],
    max_cost_seconds: float | None = None,
) -> dict[str, Any]:
    """Try cached evidence first, then run the targeted producer serially.

    Callbacks receive ``lane`` and ``start_s``/``end_s``. Producer results
    must report ``cost_seconds`` and ``peak_rss_gb``; an over-budget result is
    recorded as deferred and never reported as acquired evidence.
    """
    if plan.get("action") != "refine":
        return {"schema_version": SCHEMA_VERSION, "action": "no_run",
                "steps": [], "reason": plan.get("reason", "plan requests no work")}
    rss_ceiling = _finite_nonnegative(plan["rss_ceiling_gb"], "rss_ceiling_gb")
    cost_ceiling = (None if max_cost_seconds is None else
                    _finite_nonnegative(max_cost_seconds, "max_cost_seconds"))
    source_digest = plan.get("source_digest")
    if not isinstance(source_digest, str) or not source_digest.strip():
        raise ValueError("refinement plan must carry a source_digest")
    spent = 0.0
    rows = []
    for step in plan["steps"]:
        lane, window = step["lane"], step["interval"]
        args = {"lane": lane, "start_s": window["start_s"],
                "end_s": window["end_s"]}
        started = time.perf_counter()
        hit = cached(**args)
        cache_elapsed = time.perf_counter() - started
        if hit is not None and hit.get("sufficient") is True:
            rows.append({**args, "status": "cached", "result": dict(hit),
                         "cost_seconds": round(cache_elapsed, 6),
                         "peak_rss_gb": 0.0})
            continue
        producer = producers.get(lane)
        if producer is None:
            rows.append({**args, "status": "deferred",
                         "reason": "required targeted producer unavailable",
                         "cost_seconds": round(cache_elapsed, 6),
                         "peak_rss_gb": 0.0})
            continue
        step_budget = float(step["cost_budget_seconds"])
        if (cost_ceiling is not None
                and spent + step_budget > cost_ceiling):
            rows.append({**args, "status": "deferred",
                         "reason": "query cost budget exhausted",
                         "cost_seconds": 0.0, "peak_rss_gb": 0.0})
            continue
        # The machine grant serializes source refinements with every other
        # model/decode job. Existing producers may safely re-enter it.
        with _source_lock(source_digest):
            # A peer may have filled source memory after the first read but
            # before this process acquired the per-source lease.
            hit = cached(**args)
            if hit is not None and hit.get("sufficient") is True:
                rows.append({**args, "status": "cached", "result": dict(hit),
                             "cost_seconds": round(cache_elapsed, 6),
                             "peak_rss_gb": 0.0})
                continue
            with heavy_work_lock("vi12-query-directed-acquisition",
                                 profile="query_refinement"):
                result = dict(producer(
                    **args,
                    budget_seconds=float(step["cost_budget_seconds"]),
                    max_peak_rss_gb=rss_ceiling))
        cost = _finite_nonnegative(result.get("cost_seconds"), "producer cost_seconds")
        rss = _finite_nonnegative(result.get("peak_rss_gb"), "producer peak_rss_gb")
        producer_version = result.get("model_version") or result.get("producer_version")
        spent += cost
        if result.get("measured") is False:
            rows.append({**args, "status": "deferred",
                         "reason": "producer did not establish complete coverage",
                         "cost_seconds": cost, "peak_rss_gb": rss,
                         "model_version": producer_version})
            continue
        if not isinstance(producer_version, str) or not producer_version.strip():
            rows.append({**args, "status": "deferred",
                         "reason": "producer/model version unavailable",
                         "cost_seconds": cost, "peak_rss_gb": rss})
            continue
        lane_budget = float(step["cost_budget_seconds"])
        if (rss > rss_ceiling or cost > lane_budget
                or (cost_ceiling is not None and spent > cost_ceiling)):
            rows.append({**args, "status": "deferred",
                         "reason": "producer exceeded RSS or query cost budget",
                         "cost_seconds": cost, "cost_budget_seconds": lane_budget,
                         "peak_rss_gb": rss,
                         "model_version": producer_version})
            continue
        rows.append({**args, "status": "acquired", "result": result,
                     "cost_seconds": cost, "cost_budget_seconds": lane_budget,
                     "peak_rss_gb": rss,
                     "model_version": producer_version,
                     "samples": result.get("samples"),
                     "coverage": result.get("coverage")})
    return {"schema_version": SCHEMA_VERSION, "action": "executed",
            "source_serial": True, "cost_seconds": round(spent, 6),
            "peak_rss_gb": round(max((r.get("peak_rss_gb", 0) for r in rows),
                                      default=0.0), 3),
            "rss_ceiling_gb": rss_ceiling, "steps": rows}


def query_with_acquisition(
    model: Mapping[str, Any],
    query_plan: Mapping[str, Any],
    *,
    source_digest: str,
    duration_seconds: float,
    cached: Callable[..., Mapping[str, Any] | None],
    producers: Mapping[str, Callable[..., Mapping[str, Any]]],
    refresh_model: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    max_cost_seconds: float,
    available_lanes: Sequence[str] | None = None,
    producer_budgets: Mapping[str, float] | None = None,
    window_seconds_by_lane: Mapping[str, float] | None = None,
    producer_cost_per_window_seconds: Mapping[str, float] | None = None,
    resolvable_ambiguities: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Opt-in query -> bounded acquisition -> refreshed-model query flow.

    ``refresh_model`` is called only after at least one producer was
    accepted as acquired or cached. It owns migration/Project Model commit;
    a failed or over-budget producer cannot be represented as new evidence.
    Callers that do not explicitly invoke this function remain read-only.
    """
    from library.tools.evidence_query import evidence_for

    before = evidence_for(dict(model), dict(query_plan))
    plan = plan_query_acquisition(
        before, query_plan, source_digest=source_digest,
        duration_seconds=duration_seconds,
        resolvable_ambiguities=resolvable_ambiguities,
        available_lanes=available_lanes,
        producer_budgets=producer_budgets,
        window_seconds_by_lane=window_seconds_by_lane,
        producer_cost_per_window_seconds=producer_cost_per_window_seconds,
        max_cost_seconds=max_cost_seconds)
    execution = execute_query_acquisition(
        plan, cached=cached, producers=producers,
        max_cost_seconds=max_cost_seconds)
    acquired = any(row.get("status") in ("acquired", "cached")
                   for row in execution.get("steps", []))
    if not acquired:
        return {"answer": before, "acquisition_plan": plan,
                "acquisition": execution, "requery": None,
                "edit_ready": before.get("status") == PRESENT}
    refreshed = refresh_model(model)
    after = evidence_for(dict(refreshed), dict(query_plan))
    return {"answer": before, "acquisition_plan": plan,
            "acquisition": execution, "requery": after,
            "edit_ready": after.get("status") == PRESENT}
