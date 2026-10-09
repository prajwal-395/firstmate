"""Where a run spent its time and money: one ledger, one report.

The subsystems already measure themselves - vision splits extraction from
inference and counts ffprobe spawns, the source-memory lanes report
seconds per video-minute, the replay bench prices a prompt, the render
cache records reuse - but each answers in its own log, so nothing could
say what share of a RUN went where. This module is the one place those
answers land, and `ren profile` reads it back.

Two kinds of row, appended to `pipeline_output/logs/perf_ledger.jsonl`
(append-only, every run, keyed by the provenance `run_id`):

    capability  written by the FRONT DOOR that executes a capability: the
                runner's step loop and `provenance.observing_operation`.
                Wall, CPU (self + waited children), the children's peak
                RSS where this capability raised it, and bytes written
                under the output tree (from the provenance snapshots).
    span        written by a LAYER inside a capability that is worth
                naming in a profile: Gemma inference, MFA, a Resolve
                render, a Remotion render, a host-model answer. Wall is
                SELF time (a nested span's wall is not counted twice),
                plus whatever the layer can say cheaply: backend, model,
                calls, tokens, decoded source seconds, subprocesses,
                cache hits/misses, bytes, peak RSS.

The Resolve lease (`resolve_lock.resolve_lease`) writes two spans of its
own: `resolve_wait`, the seconds an acquisition waited, granted or
refused, and `resolve_hold`, the seconds it held Resolve. `ren profile`
reads them back as the run's share of agent time lost waiting, wait and
hold percentiles, hold utilization over the run window, render time and
cursor switches (`resolve_kpis`); the machine's side - every broker job,
every agent - is `ren resolved kpi` (`library/tools/resolved/kpi.py`).

A step runs as a subprocess, so the front door hands the ledger's path,
the run and the capability to its children through the environment
(`LEDGER_ENV`, `RUN_ENV`, `CAPABILITY_ENV`). A span with no ledger in its
environment is a no-op: a test, a REPL or a one-off script pays nothing.

Best-effort like `run_control.record_step_timing`: a row that fails to
persist must never take down a real run - the profile is then missing a
row, which reads as unattributed time, never as a wrong number. Nothing
here changes what any capability produces.

`profile()` also reports Ren own-code wall and explicitly classified
external-engine wall, both per capability and in total. The external
classification is deliberately allowlisted in `EXTERNAL_LAYERS`; an unknown
layer is not assumed to be an engine. Call families are append-only count
rows. A `null` count means that family has no instrumentation on this path,
not that the measured count was zero.

    python3 -m library.tools.perf_ledger <project> [--run <run_id>] [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import resource
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

LEDGER_FILE = "perf_ledger.jsonl"
LEDGER_ENV = "REN_PERF_LEDGER"
RUN_ENV = "REN_PERF_RUN"
CAPABILITY_ENV = "REN_PERF_CAPABILITY"
BACKENDS_ENV = "REN_PERF_BACKENDS"

CAPABILITY = "capability"
SPAN = "span"
COUNT = "count"

# Counters are deliberately explicit: call sites that can measure a request
# use ``count_call``; the report returns zero for an uninstrumented family
# instead of implying that it was measured.
COUNT_FAMILIES = ("broker_requests", "edit_core_calls", "graphics_renders",
                  "probes", "hashes", "subprocess_spawns", "cli_starts")
GRAPHICS_LAYERS = {"remotion_render", "hyperframes_render", "fframes_render",
                   "graphics_render", "hyperframes-caption",
                   "ffframes-caption"}

# Time spent waiting for or executing a separately scheduled engine. All
# other span and capability remainder time belongs to Ren. This list is kept
# explicit so a newly added layer is not silently called external.
EXTERNAL_LAYERS = {
    "gemma_inference": "model_inference", "mfa_align": "model_inference",
    "host_model": "model_inference",
    "semantic_inference_attempt": "model_inference",
    "model_load": "model_runtime",
    "demux": "ffmpeg",
    "resolve_render": "resolve_api", "resolve_wait": "resolve_lease_wait",
    "resolve_hold": "resolve_api", "ffmpeg_encode": "ffmpeg",
    "broker_request": "resolve_broker",
    "fframes_render": "graphics_engine",
    "fframes-caption": "graphics_engine",
    "hyperframes-caption": "graphics_engine",
    "transcribe": "model_inference", "language_id": "model_inference",
    "remotion_render": "graphics_engine",
    "hyperframes_render": "graphics_engine",
    "graphics_render": "graphics_engine", "model_task": "model_inference",
}

# The Resolve lease's two layers (`resolve_lock.resolve_lease`): the
# seconds a capability waited for its turn, and the seconds it held it.
RESOLVE_WAIT = "resolve_wait"
RESOLVE_HOLD = "resolve_hold"

# ru_maxrss is bytes on macOS and kilobytes on Linux.
_RSS_TO_MB = 1 / (1024 * 1024) if sys.platform == "darwin" else 1 / 1024

# Below this share a line folds into "everything else" in the report.
FOLD_BELOW_PCT = 1.0


def ledger_path(project_folder) -> Path:
    from library.tools.project_layout import Area, ProjectLayout
    return ProjectLayout(project_folder).read_dir(Area.LOGS) / LEDGER_FILE


def _append(path, row: dict[str, Any]) -> None:
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({k: v for k, v in row.items() if v is not None},
                          sort_keys=True) + "\n"
        # One write per row on an O_APPEND handle, so rows from a step's
        # subprocess and its parent interleave by line, never mid-line.
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except (OSError, TypeError, ValueError):
        pass


def _attach_backends(row: dict[str, Any]) -> None:
    """Carry the run's selected implementations onto every performance row."""
    raw = _env(BACKENDS_ENV)
    if not raw:
        return
    try:
        backends = json.loads(raw)
    except ValueError:
        return
    if isinstance(backends, dict):
        row["runtime_backends"] = backends


def _cpu_s(who: int) -> float:
    usage = resource.getrusage(who)
    return usage.ru_utime + usage.ru_stime


def _maxrss_mb(who: int) -> float:
    return round(resource.getrusage(who).ru_maxrss * _RSS_TO_MB, 1)


def bytes_written(before: dict[str, Any], after: dict[str, Any]) -> int:
    """Bytes of every file that appeared or changed between two
    `ProvenanceLedger.snapshot()`s - the walk the front door already
    takes, so the ledger adds none of its own."""
    return sum(stat[0] for path, stat in after.items()
               if before.get(path) != stat)


class OpenCapability:
    """One capability execution being timed: `begin` ... `end`.

    The runner's step loop has too many exits (a refusal, a gate, an
    awaited model) to sit inside one `with`, so it holds one of these
    and ends it in a `finally`. `row` is the record being built; a front
    door adds what only it knows (`status`, `bytes_written`).
    """

    def __init__(self, project_folder, capability_id: str, run_id: str,
                 node: str | None = None,
                 ledger_path_override: Path | None = None) -> None:
        self.path = (Path(ledger_path_override) if ledger_path_override
                     else ledger_path(project_folder))
        self._saved = {k: os.environ.get(k)
                       for k in (LEDGER_ENV, RUN_ENV, CAPABILITY_ENV,
                                 BACKENDS_ENV)}
        os.environ[LEDGER_ENV] = str(self.path)
        os.environ[RUN_ENV] = str(run_id)
        os.environ[CAPABILITY_ENV] = str(capability_id)
        try:
            from library.tools.runtime_backends import snapshot
            self._runtime_backends = snapshot(project_folder)
            os.environ[BACKENDS_ENV] = json.dumps(
                self._runtime_backends, sort_keys=True)
        except Exception:  # noqa: BLE001 - diagnostic metadata never blocks a run
            self._runtime_backends = {}
            os.environ[BACKENDS_ENV] = "{}"
        self.row: dict[str, Any] = {
            "kind": CAPABILITY, "run_id": run_id,
            "capability": capability_id, "node": node, "status": "ok",
            "started_at": round(time.time(), 3)}
        self._children_rss = _maxrss_mb(resource.RUSAGE_CHILDREN)
        self._cpu = (_cpu_s(resource.RUSAGE_SELF)
                     + _cpu_s(resource.RUSAGE_CHILDREN))
        self._t0 = time.perf_counter()
        self._ended = False

    def end(self, persist: bool = True) -> dict[str, Any]:
        if self._ended:
            return dict(self.row)
        self._ended = True
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        row = self.row
        row["wall_s"] = round(time.perf_counter() - self._t0, 3)
        row["cpu_s"] = round(_cpu_s(resource.RUSAGE_SELF)
                             + _cpu_s(resource.RUSAGE_CHILDREN) - self._cpu, 3)
        # The kernel keeps one high-water mark for all waited children,
        # so it names THIS capability only when this capability raised it.
        children_rss = _maxrss_mb(resource.RUSAGE_CHILDREN)
        if children_rss > self._children_rss:
            row["children_peak_rss_mb"] = children_rss
        row["self_peak_rss_mb"] = _maxrss_mb(resource.RUSAGE_SELF)
        if persist:
            row["runtime_backends"] = self._runtime_backends
            _append(self.path, row)
        return dict(row)


def begin(project_folder, capability_id: str, run_id: str,
          node: str | None = None,
          ledger_path_override: Path | None = None
          ) -> OpenCapability | None:
    """Start timing a capability; None (and nothing timed) on any error."""
    try:
        return OpenCapability(project_folder, capability_id, run_id, node,
                              ledger_path_override)
    except Exception:  # noqa: BLE001 - the ledger never takes down a run
        return None


@contextmanager
def capability(project_folder, capability_id: str, run_id: str,
               node: str | None = None) -> Iterator[dict[str, Any]]:
    """Time one capability execution and append its row.

    Yields the row being built. The environment carries the ledger to
    every child for the duration, and is restored afterwards.
    """
    open_cap = begin(project_folder, capability_id, run_id, node)
    row = open_cap.row if open_cap else {}
    try:
        yield row
    except BaseException:
        row["status"] = "failed"
        raise
    finally:
        if open_cap:
            open_cap.end()


def record_reused(project_folder, capability_id: str, run_id: str,
                  node: str | None = None) -> None:
    """A capability the run SKIPPED because its output was still good."""
    _append(ledger_path(project_folder),
            {"kind": CAPABILITY, "run_id": run_id, "capability": capability_id,
             "node": node, "status": "reused", "started_at": round(time.time(), 3),
             "wall_s": 0.0})


def commit_rows(project_folder, rows: list[dict[str, Any]]) -> None:
    """Append worker timing rows from the run coordinator, in order."""
    path = ledger_path(project_folder)
    for row in rows:
        if row:
            _append(path, row)


def commit_to_path(path, rows: list[dict[str, Any]]) -> None:
    """Append already measured rows to a standalone ledger file."""
    for row in rows:
        if row:
            _append(path, row)


def commit(project_folder, row: dict[str, Any]) -> None:
    """Append one completed timing row from the run coordinator."""
    commit_rows(project_folder, [row])


_local = threading.local()


def _env(key: str) -> str | None:
    """Read a ledger value, preferring a thread-scoped recording context."""
    context = getattr(_local, "context", None)
    if context is not None and key in context:
        return context[key]
    return os.environ.get(key)


@contextmanager
def recording(path, run_id: str, capability_id: str):
    """Attach rows on this thread without changing a shared process env.

    The Resolve broker is a multithreaded service. A broker job may record
    into a caller's ledger, but changing ``os.environ`` would misroute rows
    from concurrent jobs. This scoped context is thread-local by design.
    """
    previous = getattr(_local, "context", None)
    _local.context = {LEDGER_ENV: str(path), RUN_ENV: str(run_id),
                      CAPABILITY_ENV: str(capability_id)}
    try:
        yield
    finally:
        if previous is None:
            del _local.context
        else:
            _local.context = previous


@contextmanager
def span(layer: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Name a layer's time inside the current capability.

    Yields a dict the layer fills with what it knows (`calls`,
    `input_tokens`, `output_tokens`, `decoded_source_s`, `subprocesses`,
    `cache_hits`, `cache_misses`, `bytes_read`, `bytes_written`,
    `backend`, `model`). Wall and CPU are SELF time: a span nested in
    another is subtracted from its parent, so a profile never counts one
    second twice.
    """
    path = _env(LEDGER_ENV)
    if not path:
        yield dict(fields)
        return
    stack = getattr(_local, "stack", None)
    if stack is None:
        stack = _local.stack = []
    row: dict[str, Any] = dict(fields)
    frame = {"child_wall": 0.0, "child_cpu": 0.0, "children": []}
    stack.append(frame)
    started = time.time()
    t0 = time.perf_counter()
    c0 = time.thread_time()
    try:
        yield row
    finally:
        stack.pop()
        wall = time.perf_counter() - t0
        cpu = time.thread_time() - c0
        if stack:
            stack[-1]["child_wall"] += wall
            stack[-1]["child_cpu"] += cpu
            stack[-1]["children"].append((started, started + wall))
        if frame["children"]:
            # Self time is not one interval once a span has children:
            # say exactly which seconds were this layer's own.
            row["self_intervals"] = _gaps(
                (started, started + wall), frame["children"])
        if int(row.get("subprocesses") or 0) > 0:
            count_call("subprocess_spawns", int(row["subprocesses"]))
        if layer in GRAPHICS_LAYERS:
            count_call("graphics_renders", int(row.get("calls") or 1))
        row.update({
            "kind": SPAN, "layer": layer,
            "run_id": _env(RUN_ENV),
            "capability": _env(CAPABILITY_ENV),
            "started_at": round(started, 3),
            "wall_s": round(max(0.0, wall - frame["child_wall"]), 3),
            "cpu_s": round(max(0.0, cpu - frame["child_cpu"]), 3),
            "self_peak_rss_mb": _maxrss_mb(resource.RUSAGE_SELF),
        })
        _attach_backends(row)
        _append(path, row)


def record(layer: str, wall_s: float, started_at: float | None = None,
           **fields: Any) -> None:
    """A span for an interval the caller already measured.

    For a layer whose wait is a loop that already times itself (a host
    model's answer through the file handshake, a Resolve lease's
    acquisition). The interval ends now unless `started_at` says where
    it began. A leaf: nothing nested inside it is subtracted, but it IS
    subtracted from an enclosing `span`, so its seconds are counted once.
    """
    path = _env(LEDGER_ENV)
    if not path:
        return
    wall_s = max(0.0, float(wall_s))
    if started_at is None:
        started_at = time.time() - wall_s
    stack = getattr(_local, "stack", None)
    if stack:
        stack[-1]["child_wall"] += wall_s
        stack[-1]["children"].append((started_at, started_at + wall_s))
    row = {**fields, "kind": SPAN, "layer": layer,
           "run_id": _env(RUN_ENV),
           "capability": _env(CAPABILITY_ENV),
           "started_at": round(started_at, 3),
           "wall_s": round(wall_s, 3)}
    if int(row.get("subprocesses") or 0) > 0:
        count_call("subprocess_spawns", int(row["subprocesses"]))
    if layer in GRAPHICS_LAYERS:
        count_call("graphics_renders", int(row.get("calls") or 1))
    _attach_backends(row)
    _append(path, row)


def count_call(family: str, amount: int = 1, **fields: Any) -> None:
    """Record a measurable per-run call count without adding wall time."""
    if family not in COUNT_FAMILIES or amount < 0:
        return
    path = _env(LEDGER_ENV)
    if not path:
        return
    row = {**fields, "kind": COUNT, "family": family, "count": int(amount),
           "run_id": _env(RUN_ENV),
           "capability": _env(CAPABILITY_ENV),
           "started_at": round(time.time(), 3)}
    _attach_backends(row)
    _append(path, row)


def run(layer: str, *args: Any, fields: dict[str, Any] | None = None,
        **kwargs: Any):
    """`subprocess.run`, charged to `layer` - for a tool whose whole cost
    is one child process (a Remotion or HyperFrames render, an encode)."""
    import subprocess
    check = kwargs.pop("check", False)
    with span(layer, subprocesses=1, **(fields or {})):
        return subprocess.run(*args, check=check, **kwargs)


# ── Reading it back ────────────────────────────────────────────────

def read_rows(project_folder, ledger_override=None) -> list[dict[str, Any]]:
    rows = []
    try:
        path = Path(ledger_override) if ledger_override else ledger_path(project_folder)
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        pass
    return rows


def run_ids(rows: list[dict[str, Any]]) -> list[str]:
    """Run ids in the order their first row was written."""
    seen: dict[str, None] = {}
    for row in rows:
        rid = row.get("run_id")
        if rid and rid not in seen:
            seen[rid] = None
    return list(seen)


def _gaps(outer: tuple, inner: list[tuple]) -> list[list[float]]:
    """`outer` minus every `inner` interval, as [start, end] pairs."""
    gaps, cursor = [], outer[0]
    for begin, finish in sorted(inner):
        if begin > cursor:
            gaps.append([round(cursor, 3), round(begin, 3)])
        cursor = max(cursor, finish)
    if outer[1] > cursor:
        gaps.append([round(cursor, 3), round(outer[1], 3)])
    return gaps


def union_s(intervals: list[tuple]) -> float:
    """Seconds covered by a set of (start, end) intervals."""
    total, reach = 0.0, None
    for begin, finish in sorted(intervals):
        if reach is None or begin > reach:
            total += finish - begin
            reach = finish
        elif finish > reach:
            total += finish - reach
            reach = finish
    return total


def profile(rows: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    """Attribute one run's wall to named layers and capability remainders.

    The whole is the sum of the run's capability walls. A layer is
    charged the time its spans COVER, not their sum, so a render fanned
    out over four threads is charged its wall once; where two layers
    overlap in time each is charged the overlap, and the shares then add
    past 100 - which the report says (`overlap_s`) rather than hides.
    What a capability spent outside every span is charged to the
    capability itself as "(other)".
    """
    caps = [r for r in rows if r.get("run_id") == run_id
            and r.get("kind") == CAPABILITY]
    spans = [r for r in rows if r.get("run_id") == run_id
             and r.get("kind") == SPAN]
    # None means this checkout did not instrument that family yet; zero
    # means it was measured and no calls occurred.
    counts = {name: None for name in COUNT_FAMILIES}
    for r in rows:
        if r.get("run_id") == run_id and r.get("kind") == COUNT:
            family = r.get("family")
            if isinstance(family, str) and family in counts:
                counts[family] = (counts[family] or 0) + int(
                    r.get("count") or 0)
    total = sum(float(r.get("wall_s") or 0.0) for r in caps)
    buckets: dict[str, dict[str, Any]] = {}

    def bucket(name: str) -> dict[str, Any]:
        return buckets.setdefault(name, {"wall_s": 0.0, "cpu_s": 0.0,
                                         "calls": 0, "input_tokens": 0,
                                         "output_tokens": 0,
                                         "subprocesses": 0})

    def intervals_of(s: dict[str, Any]) -> list[tuple]:
        if s.get("self_intervals"):
            return [(float(a), float(b)) for a, b in s["self_intervals"]]
        begin = float(s.get("started_at") or 0.0)
        return [(begin, begin + float(s.get("wall_s") or 0.0))]

    by_cap_layer: dict[tuple, list[tuple]] = {}
    by_cap: dict[str, list[tuple]] = {}
    by_cap_external: dict[str, dict[str, list[tuple]]] = {}
    for s in spans:
        layer, cap = str(s.get("layer")), str(s.get("capability"))
        b = bucket(layer)
        b["cpu_s"] += float(s.get("cpu_s") or 0.0)
        for key in ("calls", "input_tokens", "output_tokens", "subprocesses"):
            b[key] += int(s.get(key) or 0)
        by_cap_layer.setdefault((cap, layer), []).extend(intervals_of(s))
        by_cap.setdefault(cap, []).extend(intervals_of(s))
        external = s.get("external_engine") or EXTERNAL_LAYERS.get(layer)
        if external:
            by_cap_external.setdefault(cap, {}).setdefault(
                str(external), []).extend(intervals_of(s))
    layer_sum = 0.0
    for (_cap, layer), intervals in by_cap_layer.items():
        covered = union_s(intervals)
        buckets[layer]["wall_s"] += covered
        layer_sum += covered

    per_capability = []
    inside = 0.0
    external_totals: dict[str, float] = {}
    own_code_s = 0.0
    cap_names = set()
    for c in caps:
        cap = str(c.get("capability"))
        cap_names.add(cap)
        wall = float(c.get("wall_s") or 0.0)
        covered = min(wall, union_s(by_cap.get(cap, [])))
        inside += covered
        external_by_kind = by_cap_external.get(cap, {})
        external_union = min(wall, union_s([
            interval for values in external_by_kind.values()
            for interval in values]))
        own = max(0.0, wall - external_union)
        own_code_s += own
        # Keep the historical span/remainder rows additive for existing
        # profile consumers. The explicit run-level split above is computed
        # independently from external intervals.
        bucket(f"{cap} (other)")["wall_s"] += max(0.0, wall - covered)
        for name, intervals in external_by_kind.items():
            external_totals[name] = external_totals.get(name, 0.0) + min(
                wall, union_s(intervals))
        cap_row = {k: c.get(k) for k in (
            "capability", "node", "status", "wall_s", "cpu_s",
            "children_peak_rss_mb", "self_peak_rss_mb", "bytes_written")}
        cap_row.update({"own_code_s": round(own, 3),
                        "external_s": round(external_union, 3),
                        "external_by_engine_s": {
                            k: round(min(wall, union_s(v)), 3)
                            for k, v in sorted(external_by_kind.items())}})
        per_capability.append(cap_row)

    lines = []
    for name, b in sorted(buckets.items(), key=lambda kv: -kv[1]["wall_s"]):
        if b["wall_s"] <= 0 and not b["calls"]:
            continue
        pct = 100.0 * b["wall_s"] / total if total else 0.0
        lines.append({"name": name, "pct": round(pct, 1),
                      **{k: (round(v, 3) if isinstance(v, float) else v)
                         for k, v in b.items()}})
    external_s = max(0.0, total - own_code_s)
    return {"run_id": run_id, "total_wall_s": round(total, 3),
            "own_code_s": round(own_code_s, 3),
            "own_code_pct": (round(100 * own_code_s / total, 1)
                             if total else None),
            "external_s": round(external_s, 3),
            "external_pct": (round(100 * external_s / total, 1)
                             if total else None),
            "external_by_engine_s": {
                k: round(v, 3) for k, v in sorted(external_totals.items())},
            "call_counts": counts,
            "overlap_s": round(max(0.0, layer_sum - inside), 3),
            "lines": lines, "capabilities": per_capability,
            "resolve": resolve_kpis(spans, total, caps),
            "spans_without_capability_row": sorted(set(by_cap) - cap_names)}


def quantile(values: list[float], q: float) -> float | None:
    """Nearest-rank quantile; None for no values (never a zero)."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(q * len(ordered)) - 1)], 3)


def resolve_kpis(spans: list[dict[str, Any]], total_wall_s: float,
                 caps: list[dict[str, Any]]) -> dict[str, Any]:
    """What one run paid for the one Resolve.

    `blocked_s` is the time the run's capabilities spent waiting for a
    Resolve lease (`RESOLVE_WAIT` rows: the captain's signal, the
    broker's queue and the flock, granted or refused); `useful_s` is
    the rest of the run's capability wall. `lost_ratio` is blocked over
    total - the single-Resolve plan's KPI: once it is small, software
    has nothing left to win from one Resolve instance. `held_s` is the
    time the run held Resolve and `exclusive_held_s` the part no other
    lease could share, and `timeline_switches` the cursor moves made
    under those holds. The hold percentiles are per lease; utilization
    is the union of hold intervals over the run's elapsed capability
    window, from the earliest capability start to the latest end.
    `concurrency` is how many capabilities ran at
    once on average outside Resolve: each capability's seconds outside
    its own waits and holds, over the wall clock those seconds cover.
    """
    waits = [s for s in spans if s.get("layer") == RESOLVE_WAIT]
    holds = [s for s in spans if s.get("layer") == RESOLVE_HOLD]
    renders = [s for s in spans if s.get("layer") == "resolve_render"]
    blocked = sum(float(s.get("wall_s") or 0.0) for s in waits)
    wait_values = [float(s.get("wall_s") or 0.0) for s in waits]
    hold_values = [float(s.get("held_s") or 0.0) for s in holds]
    in_resolve: dict[str, list[tuple]] = {}
    for rows, length_key in ((waits, "wall_s"), (holds, "held_s")):
        for s in rows:
            begin = float(s.get("started_at") or 0.0)
            in_resolve.setdefault(str(s.get("capability")), []).append(
                (begin, begin + float(s.get(length_key) or 0.0)))
    free: list[tuple] = []
    for c in caps:
        begin = float(c.get("started_at") or 0.0)
        outer = (begin, begin + float(c.get("wall_s") or 0.0))
        free.extend(tuple(g) for g in _gaps(
            outer, in_resolve.get(str(c.get("capability")), [])))
    free_s = sum(end - begin for begin, end in free)
    clock = union_s(free)
    cap_starts = [float(c.get("started_at") or 0.0) for c in caps]
    cap_ends = [start + float(c.get("wall_s") or 0.0)
                for start, c in zip(cap_starts, caps)]
    run_window = ((max(cap_ends) - min(cap_starts))
                  if cap_starts else 0.0)
    hold_intervals = [(float(s.get("started_at") or 0.0),
                       float(s.get("started_at") or 0.0)
                       + float(s.get("held_s") or 0.0)) for s in holds]
    return {
        "leases": len(holds),
        "refused": sum(1 for s in waits if s.get("outcome") == "refused"),
        "blocked_s": round(blocked, 3),
        "useful_s": round(max(0.0, total_wall_s - blocked), 3),
        "lost_ratio": (round(blocked / total_wall_s, 4)
                       if total_wall_s else None),
        "wait_p50_s": quantile(wait_values, 0.5),
        "wait_p95_s": quantile(wait_values, 0.95),
        "hold_p50_s": quantile(hold_values, 0.5),
        "hold_p95_s": quantile(hold_values, 0.95),
        "utilization": (round(union_s(hold_intervals) / run_window, 4)
                        if run_window else None),
        "held_s": round(sum(float(s.get("held_s") or 0.0)
                            for s in holds), 3),
        "exclusive_held_s": round(sum(float(s.get("held_s") or 0.0)
                                      for s in holds if s.get("exclusive")),
                                  3),
        "render_s": round(sum(float(s.get("wall_s") or 0.0)
                              for s in renders), 3),
        "timeline_switches": sum(int(s.get("timeline_switches") or 0)
                                 for s in holds),
        "concurrency": round(free_s / clock, 2) if clock else None,
    }


def render(report: dict[str, Any]) -> str:
    total = report["total_wall_s"]
    out = [(f"Run {report['run_id']}: {total:.1f}s across "
            f"{len(report['capabilities'])} capability row(s)")]
    own_pct = report.get("own_code_pct")
    external_pct = report.get("external_pct")
    if own_pct is not None and external_pct is not None:
        out.append(f"Ren own code {report['own_code_s']:.1f}s ({own_pct:.1f}%), "
                   f"external engines {report['external_s']:.1f}s "
                   f"({external_pct:.1f}%)")
        engines = report.get("external_by_engine_s") or {}
        if engines:
            out.append("External by engine: " + ", ".join(
                f"{name}={seconds:.1f}s" for name, seconds in
                sorted(engines.items())))
    out.append("Calls: " + ", ".join(
        f"{name}={count if count is not None else 'unmeasured'}"
        for name, count in report["call_counts"].items()))
    shown, folded = [], 0.0
    for line in report["lines"]:
        if line["pct"] < FOLD_BELOW_PCT:
            folded += line["pct"]
        else:
            shown.append(line)
    summary = ", ".join(f"{l['pct']:.0f}% {l['name']}" for l in shown)
    if folded:
        summary += f", {folded:.0f}% everything else"
    out.append(summary or "(no time recorded)")
    out.append("")
    out.append(f"{'layer / capability':<40} {'wall s':>9} {'%':>6} "
               f"{'cpu s':>8} {'calls':>6} {'procs':>6} {'tok in':>8} "
               f"{'tok out':>8}")
    for line in report["lines"]:
        out.append(f"{line['name'][:40]:<40} {line['wall_s']:>9.1f} "
                   f"{line['pct']:>6.1f} {line['cpu_s']:>8.1f} "
                   f"{line['calls'] or '':>6} {line['subprocesses'] or '':>6} "
                   f"{line['input_tokens'] or '':>8} "
               f"{line['output_tokens'] or '':>8}")
    if report["capabilities"]:
        out.append("")
        out.append(f"{'capability':<36} {'wall s':>9} {'own s':>9} "
                   f"{'external s':>11}")
        for cap in report["capabilities"]:
            out.append(f"{str(cap.get('capability') or '')[:36]:<36} "
                       f"{float(cap.get('wall_s') or 0):>9.1f} "
                       f"{float(cap.get('own_code_s') or 0):>9.1f} "
                       f"{float(cap.get('external_s') or 0):>11.1f}")
    if report["spans_without_capability_row"]:
        out.append("")
        out.append("Spans with no capability row (the run did not finish "
                   "them, or ran outside a front door): "
                   + ", ".join(report["spans_without_capability_row"]))
    kpi = report.get("resolve") or {}
    if kpi.get("leases") or kpi.get("blocked_s"):
        out.append("")
        ratio = kpi["lost_ratio"]
        out.append(
            f"Resolve: {kpi['blocked_s']:.1f}s waiting / "
            f"{total:.1f}s capability wall = "
            f"{100 * ratio:.1f}% of agent time lost to Resolve"
            if ratio is not None else "Resolve: no capability wall")
        utilization = ("-" if kpi["utilization"] is None else
                       f"{100 * kpi['utilization']:.1f}%")
        concurrency = ("-" if kpi["concurrency"] is None else
                       str(kpi["concurrency"]))
        out.append(
            f"  {kpi['leases']} lease(s), {kpi['refused']} refused; wait "
            f"p50 {_seconds(kpi['wait_p50_s'])} p95 "
            f"{_seconds(kpi['wait_p95_s'])}; hold p50 "
            f"{_seconds(kpi['hold_p50_s'])} p95 "
            f"{_seconds(kpi['hold_p95_s'])}; held {kpi['held_s']:.1f}s "
            f"({kpi['exclusive_held_s']:.1f}s exclusive); render "
            f"{kpi['render_s']:.1f}s; {kpi['timeline_switches']} timeline "
            f"switch(es); Resolve utilization {utilization}; "
            f"free-work concurrency {concurrency}")
    if report["overlap_s"] >= 0.05:
        out.append(f"{report['overlap_s']:.1f}s ran in two layers at once "
                   f"and is counted in both, so the shares add past 100%.")
    return "\n".join(out)


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}s"


def _resolve_project(ref: str) -> str:
    from library.tools.project_registry import get_project, resolve_project_path
    found = resolve_project_path(ref)
    if found is not None:
        return str(found.parent)
    config = get_project(ref)
    return str(config._project_root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren profile",
        description="Where a run spent its time: each named layer's share "
                    "of the run's wall, then what each capability spent "
                    "outside them. Reads pipeline_output/logs/"
                    f"{LEDGER_FILE}; changes nothing.")
    parser.add_argument("project", nargs="?", help="a project slug or path")
    parser.add_argument("--run", help="a run id (default: the latest)")
    parser.add_argument("--ledger", help="read a standalone perf ledger JSONL path")
    parser.add_argument("--list", action="store_true",
                        help="list the run ids the ledger holds")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.project and not args.ledger:
        parser.error("provide a project or --ledger")
    project = (_resolve_project(args.project) if not args.ledger
               else args.project or "")
    rows = read_rows(project, args.ledger)
    ids = run_ids(rows)
    if args.list:
        print("\n".join(ids))
        return 0
    if not ids:
        print(f"No performance rows in {ledger_path(project)}: no run has "
              f"recorded one yet.", file=sys.stderr)
        return 1
    run_id = args.run or ids[-1]
    if run_id not in ids:
        print(f"No run {run_id!r} in {ledger_path(project)}; "
              f"`--list` shows what is there.", file=sys.stderr)
        return 1
    report = profile(rows, run_id)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
