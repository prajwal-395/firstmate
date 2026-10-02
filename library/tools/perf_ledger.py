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

A step runs as a subprocess, so the front door hands the ledger's path,
the run and the capability to its children through the environment
(`LEDGER_ENV`, `RUN_ENV`, `CAPABILITY_ENV`). A span with no ledger in its
environment is a no-op: a test, a REPL or a one-off script pays nothing.

Best-effort like `run_control.record_step_timing`: a row that fails to
persist must never take down a real run - the profile is then missing a
row, which reads as unattributed time, never as a wrong number. Nothing
here changes what any capability produces.

    python3 -m library.tools.perf_ledger <project> [--run <run_id>] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

LEDGER_FILE = "perf_ledger.jsonl"
LEDGER_ENV = "REN_PERF_LEDGER"
RUN_ENV = "REN_PERF_RUN"
CAPABILITY_ENV = "REN_PERF_CAPABILITY"

CAPABILITY = "capability"
SPAN = "span"

# ru_maxrss is bytes on macOS and kilobytes on Linux.
_RSS_TO_MB = 1 / (1024 * 1024) if sys.platform == "darwin" else 1 / 1024

# Below this share a line folds into "everything else" in the report.
FOLD_BELOW_PCT = 1.0


def ledger_path(project_folder) -> Path:
    from library.tools.project_layout import Area, ProjectLayout
    return ProjectLayout(project_folder).read_dir(Area.LOGS) / LEDGER_FILE


def _append(path, row: Dict[str, Any]) -> None:
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


def _cpu_s(who: int) -> float:
    usage = resource.getrusage(who)
    return usage.ru_utime + usage.ru_stime


def _maxrss_mb(who: int) -> float:
    return round(resource.getrusage(who).ru_maxrss * _RSS_TO_MB, 1)


def bytes_written(before: Dict[str, Any], after: Dict[str, Any]) -> int:
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
                 node: Optional[str] = None) -> None:
        self.path = ledger_path(project_folder)
        self._saved = {k: os.environ.get(k)
                       for k in (LEDGER_ENV, RUN_ENV, CAPABILITY_ENV)}
        os.environ[LEDGER_ENV] = str(self.path)
        os.environ[RUN_ENV] = str(run_id)
        os.environ[CAPABILITY_ENV] = str(capability_id)
        self.row: Dict[str, Any] = {
            "kind": CAPABILITY, "run_id": run_id,
            "capability": capability_id, "node": node, "status": "ok",
            "started_at": round(time.time(), 3)}
        self._children_rss = _maxrss_mb(resource.RUSAGE_CHILDREN)
        self._cpu = (_cpu_s(resource.RUSAGE_SELF)
                     + _cpu_s(resource.RUSAGE_CHILDREN))
        self._t0 = time.perf_counter()
        self._ended = False

    def end(self) -> None:
        if self._ended:
            return
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
        _append(self.path, row)


def begin(project_folder, capability_id: str, run_id: str,
          node: Optional[str] = None) -> Optional[OpenCapability]:
    """Start timing a capability; None (and nothing timed) on any error."""
    try:
        return OpenCapability(project_folder, capability_id, run_id, node)
    except Exception:  # noqa: BLE001 - the ledger never takes down a run
        return None


@contextmanager
def capability(project_folder, capability_id: str, run_id: str,
               node: Optional[str] = None) -> Iterator[Dict[str, Any]]:
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
                  node: Optional[str] = None) -> None:
    """A capability the run SKIPPED because its output was still good."""
    _append(ledger_path(project_folder),
            {"kind": CAPABILITY, "run_id": run_id, "capability": capability_id,
             "node": node, "status": "reused", "started_at": round(time.time(), 3),
             "wall_s": 0.0})


_local = threading.local()


@contextmanager
def span(layer: str, **fields: Any) -> Iterator[Dict[str, Any]]:
    """Name a layer's time inside the current capability.

    Yields a dict the layer fills with what it knows (`calls`,
    `input_tokens`, `output_tokens`, `decoded_source_s`, `subprocesses`,
    `cache_hits`, `cache_misses`, `bytes_read`, `bytes_written`,
    `backend`, `model`). Wall and CPU are SELF time: a span nested in
    another is subtracted from its parent, so a profile never counts one
    second twice.
    """
    path = os.environ.get(LEDGER_ENV)
    if not path:
        yield dict(fields)
        return
    stack = getattr(_local, "stack", None)
    if stack is None:
        stack = _local.stack = []
    row: Dict[str, Any] = dict(fields)
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
        row.update({
            "kind": SPAN, "layer": layer,
            "run_id": os.environ.get(RUN_ENV),
            "capability": os.environ.get(CAPABILITY_ENV),
            "started_at": round(started, 3),
            "wall_s": round(max(0.0, wall - frame["child_wall"]), 3),
            "cpu_s": round(max(0.0, cpu - frame["child_cpu"]), 3),
            "self_peak_rss_mb": _maxrss_mb(resource.RUSAGE_SELF),
        })
        _append(path, row)


def record(layer: str, wall_s: float, **fields: Any) -> None:
    """A span for an interval the caller already measured, ENDING now.

    For a layer whose wait is a loop that already times itself (a host
    model's answer through the file handshake). A leaf: nothing nested
    inside it is subtracted.
    """
    path = os.environ.get(LEDGER_ENV)
    if not path:
        return
    wall_s = max(0.0, float(wall_s))
    _append(path, {**fields, "kind": SPAN, "layer": layer,
                   "run_id": os.environ.get(RUN_ENV),
                   "capability": os.environ.get(CAPABILITY_ENV),
                   "started_at": round(time.time() - wall_s, 3),
                   "wall_s": round(wall_s, 3)})


def run(layer: str, *args: Any, fields: Optional[Dict[str, Any]] = None,
        **kwargs: Any):
    """`subprocess.run`, charged to `layer` - for a tool whose whole cost
    is one child process (a Remotion or HyperFrames render, an encode)."""
    import subprocess
    check = kwargs.pop("check", False)
    with span(layer, subprocesses=1, **(fields or {})):
        return subprocess.run(*args, check=check, **kwargs)


# ── Reading it back ────────────────────────────────────────────────

def read_rows(project_folder) -> List[Dict[str, Any]]:
    rows = []
    try:
        with open(ledger_path(project_folder), encoding="utf-8") as fh:
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


def run_ids(rows: List[Dict[str, Any]]) -> List[str]:
    """Run ids in the order their first row was written."""
    seen: Dict[str, None] = {}
    for row in rows:
        rid = row.get("run_id")
        if rid and rid not in seen:
            seen[rid] = None
    return list(seen)


def _gaps(outer: tuple, inner: List[tuple]) -> List[List[float]]:
    """`outer` minus every `inner` interval, as [start, end] pairs."""
    gaps, cursor = [], outer[0]
    for begin, finish in sorted(inner):
        if begin > cursor:
            gaps.append([round(cursor, 3), round(begin, 3)])
        cursor = max(cursor, finish)
    if outer[1] > cursor:
        gaps.append([round(cursor, 3), round(outer[1], 3)])
    return gaps


def _union_s(intervals: List[tuple]) -> float:
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


def profile(rows: List[Dict[str, Any]], run_id: str) -> Dict[str, Any]:
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
    total = sum(float(r.get("wall_s") or 0.0) for r in caps)
    buckets: Dict[str, Dict[str, Any]] = {}

    def bucket(name: str) -> Dict[str, Any]:
        return buckets.setdefault(name, {"wall_s": 0.0, "cpu_s": 0.0,
                                         "calls": 0, "input_tokens": 0,
                                         "output_tokens": 0,
                                         "subprocesses": 0})

    def intervals_of(s: Dict[str, Any]) -> List[tuple]:
        if s.get("self_intervals"):
            return [(float(a), float(b)) for a, b in s["self_intervals"]]
        begin = float(s.get("started_at") or 0.0)
        return [(begin, begin + float(s.get("wall_s") or 0.0))]

    by_cap_layer: Dict[tuple, List[tuple]] = {}
    by_cap: Dict[str, List[tuple]] = {}
    for s in spans:
        layer, cap = str(s.get("layer")), str(s.get("capability"))
        b = bucket(layer)
        b["cpu_s"] += float(s.get("cpu_s") or 0.0)
        for key in ("calls", "input_tokens", "output_tokens", "subprocesses"):
            b[key] += int(s.get(key) or 0)
        by_cap_layer.setdefault((cap, layer), []).extend(intervals_of(s))
        by_cap.setdefault(cap, []).extend(intervals_of(s))
    layer_sum = 0.0
    for (_cap, layer), intervals in by_cap_layer.items():
        covered = _union_s(intervals)
        buckets[layer]["wall_s"] += covered
        layer_sum += covered

    per_capability = []
    inside = 0.0
    cap_names = set()
    for c in caps:
        cap = str(c.get("capability"))
        cap_names.add(cap)
        wall = float(c.get("wall_s") or 0.0)
        covered = min(wall, _union_s(by_cap.get(cap, [])))
        inside += covered
        bucket(f"{cap} (other)")["wall_s"] += max(0.0, wall - covered)
        per_capability.append({k: c.get(k) for k in (
            "capability", "node", "status", "wall_s", "cpu_s",
            "children_peak_rss_mb", "self_peak_rss_mb", "bytes_written")})

    lines = []
    for name, b in sorted(buckets.items(), key=lambda kv: -kv[1]["wall_s"]):
        if b["wall_s"] <= 0 and not b["calls"]:
            continue
        pct = 100.0 * b["wall_s"] / total if total else 0.0
        lines.append({"name": name, "pct": round(pct, 1),
                      **{k: (round(v, 3) if isinstance(v, float) else v)
                         for k, v in b.items()}})
    return {"run_id": run_id, "total_wall_s": round(total, 3),
            "overlap_s": round(max(0.0, layer_sum - inside), 3),
            "lines": lines, "capabilities": per_capability,
            "spans_without_capability_row": sorted(set(by_cap) - cap_names)}


def render(report: Dict[str, Any]) -> str:
    total = report["total_wall_s"]
    out = [f"Run {report['run_id']}: {total:.1f}s across "
           f"{len(report['capabilities'])} capability row(s)"]
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
    if report["spans_without_capability_row"]:
        out.append("")
        out.append("Spans with no capability row (the run did not finish "
                   "them, or ran outside a front door): "
                   + ", ".join(report["spans_without_capability_row"]))
    if report["overlap_s"] >= 0.05:
        out.append(f"{report['overlap_s']:.1f}s ran in two layers at once "
                   f"and is counted in both, so the shares add past 100%.")
    return "\n".join(out)


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
    parser.add_argument("project", help="a project slug or path")
    parser.add_argument("--run", help="a run id (default: the latest)")
    parser.add_argument("--list", action="store_true",
                        help="list the run ids the ledger holds")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    project = _resolve_project(args.project)
    rows = read_rows(project)
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
