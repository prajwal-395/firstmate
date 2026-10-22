"""Latency budgets BY OPERATION CLASS, measured from the performance ledger.

The performance ledger (`library/tools/perf_ledger.py`) says WHERE a run's
time went.  It does not say whether any of that time was too much, because
"time" has no verdict in it: a reel build costs four minutes because the
machine was busy, or because the placement pass got slower, and the
ledger alone cannot tell those apart.

This module is the piece that says it.  It owns:

  * `OPERATION_CLASSES` - the vocabulary.  Seven classes, one per kind of
    work a person asks for, each naming the ledger signals that measure
    it.  A class is a THING THAT TAKES TIME, not a stage of a pipeline:
    the same class is measured the same way whatever ran it.

  * the baseline table (`library/tools/latency_budgets.json`) - the
    measured, uncontended cost of one unit of each class, with the
    sample count, the runs it was measured from, and the machine it was
    measured on.

  * the separation of WORK from CONTENTION.  A run's wall time includes
    the seconds it spent queued behind the one Resolve instance
    (`resolve_wait`) and the seconds it spent queued for the machine's
    heavy work (`schedule.queue_wait_s`).  Neither is the work; both are
    what happens when several lanes share one machine.  A budget set on
    the wall would therefore be a budget on the weather.  Every number
    here is WORK: the wall minus the contention that was subtracted from
    it, and the contention is reported beside it rather than hidden.

THE UNIT IS THE POINT.  A budget is a cost PER UNIT OF WORK, and two
units are only comparable when they are the same thing.  A catalogue read
of a project is measured per source file, because a project with 700
files does not cost what one with 7 does; a frame decode is measured per
frame, because a 990-frame clip does not cost what a 47-frame one does.
Every operation names the divisor that makes its unit comparable, and
the divisor is COUNTED from the ledger, never assumed.

WHAT A BUDGET IS NOT.  A budget is a REGRESSION TRIPWIRE, not a quality
score and not a correctness gate.  `check()` answers one question - "is
this class slower than the last time it was measured?" - and a class that
is over its budget means "look at this", not "this output is wrong".  No
number here is a quality measure, and nothing here may be cited as
evidence that an edit is good.  The same rule the repo already follows
for the performance profile: a measurement is a measurement, and the
verdict belongs to the checks that judge the output.

A class with no completed sample is UNMEASURED.  It is never zero and
never "within budget": `check()` says so, and the baseline file says
where the number would come from.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from library.tools import perf_ledger

#: The baseline table, beside this module.
BASELINE_PATH = Path(__file__).with_name("latency_budgets.json")

#: The schema the baseline file must carry.
BASELINE_SCHEMA = "ren-latency-budgets/1"


# ── The vocabulary ────────────────────────────────────────────────────────

#: The ledger kinds a class can be measured from.
CAPABILITY = "capability"
TASK = "task"
SPAN = "span"
PROBE = "probe"

#: How an operation's work is divided into comparable units.
ONE = "one"
"""The row IS one operation: one build, one edit, one verification."""

CALLS = "calls"
"""The row's own `calls` count: frames decoded, model calls made."""

FRAMES = "frames"
"""The row's own `frames` count: frames rendered."""

SOURCE_FILES = "source_files"
"""The source files the run ingested, counted from the ledger's own rows."""


@dataclass(frozen=True)
class Signal:
    """One ledger signal that measures an operation of a class.

    `kind` is the ledger row kind (or `probe`, for a measurement the
    engine does not write to a ledger); `value` is the capability id, the
    task class, the span layer, or the probe's name.
    """
    kind: str
    value: str

    def label(self) -> str:
        return f"{self.kind}:{self.value}"


@dataclass(frozen=True)
class Operation:
    """One measurable unit inside a class, and what it may cost."""
    key: str
    title: str
    unit: str
    signals: tuple[Signal, ...]
    divisor: str = ONE
    """How the row's work is divided into units of this operation."""


@dataclass(frozen=True)
class OperationClass:
    """A kind of work a person asks for, and the operations that measure it."""
    id: str
    title: str
    operations: tuple[Operation, ...]


def _op(key: str, title: str, unit: str, divisor: str,
        *signals: Signal) -> Operation:
    return Operation(key=key, title=title, unit=unit, signals=tuple(signals),
                     divisor=divisor)


#: The seven operation classes, each with the operations that measure it.
OPERATION_CLASSES: tuple[OperationClass, ...] = (
    OperationClass(
        id="metadata_read",
        title="Metadata reads and capability discovery",
        operations=(
            _op("catalogue_read", "One source file's metadata read",
                "one source file catalogued", SOURCE_FILES,
                Signal(CAPABILITY, "catalog"), Signal(CAPABILITY, "footage.catalog")),
            _op("project_scan", "One project folder scan",
                "one project folder scan", ONE,
                Signal(CAPABILITY, "scan")),
            _op("capability_discovery", "One capability-registry derivation",
                "one registry derivation", ONE,
                Signal(PROBE, "capability_discovery")),
        ),
    ),
    OperationClass(
        id="property_edit",
        title="Simple timeline property edits",
        operations=(
            _op("timeline_property_edit", "One property edit on a built reel",
                "one property edit", ONE,
                Signal(TASK, "axi.property_change")),
        ),
    ),
    OperationClass(
        id="caption_graphic_revision",
        title="Caption and graphic revisions",
        operations=(
            _op("caption_text_revision", "One caption text revision",
                "one caption revision", ONE,
                Signal(TASK, "axi.caption_fix")),
            _op("overlay_position_revision", "One overlay position revision",
                "one overlay revision", ONE,
                Signal(TASK, "axi.overlay_position")),
        ),
    ),
    OperationClass(
        id="structural_edit",
        title="Structural timeline edits",
        operations=(
            _op("timeline_trim", "One trim of a built reel's item",
                "one trim", ONE,
                Signal(TASK, "axi.trim")),
        ),
    ),
    OperationClass(
        id="reel_construction",
        title="Full reel construction",
        operations=(
            _op("reel_build", "One reel built from an approved moment",
                "one reel build", ONE,
                Signal(CAPABILITY, "reel.build")),
        ),
    ),
    OperationClass(
        id="source_analysis",
        title="Source analysis and video reasoning",
        operations=(
            _op("coarse_frame_decode", "One source frame decoded for the vision pass",
                "one decoded frame", CALLS,
                Signal(SPAN, "media_decode")),
            _op("video_reasoning", "One vision model reading of the decoded frames",
                "one model call", CALLS,
                Signal(SPAN, "gemma_inference")),
        ),
    ),
    OperationClass(
        id="delivery_render",
        title="Final delivery and rendering",
        operations=(
            _op("reel_render", "One built reel rendered to a file",
                "one rendered frame at 1920x1080", FRAMES,
                Signal(SPAN, "resolve_render")),
            _op("reel_verification", "One built reel graded against the plan it came from",
                "one verification", ONE,
                Signal(SPAN, "verification")),
        ),
    ),
)

CLASS_IDS: tuple[str, ...] = tuple(c.id for c in OPERATION_CLASSES)


def _operation_index() -> dict[str, Operation]:
    return {op.key: op for cls in OPERATION_CLASSES for op in cls.operations}


def _class_of(operation_key: str) -> str:
    for cls in OPERATION_CLASSES:
        for op in cls.operations:
            if op.key == operation_key:
                return cls.id
    raise KeyError(operation_key)


# ── The probes ────────────────────────────────────────────────────────────

def _probe_capability_discovery() -> float:
    """How long one cold capability-registry derivation takes, in seconds.

    The engine caches the registry, so the cost a person pays is the
    FIRST derivation in their process.  The cache is cleared before the
    sample so every sample is that cold derivation and not a lookup; the
    derivation itself is pure, so clearing the cache changes nothing but
    the timing.
    """
    from library.tools import capability_discovery
    from library.tools.capabilities import all as _all_capabilities
    _all_capabilities.cache_clear()
    started = time.perf_counter()
    capability_discovery.catalog(checks=None)
    return time.perf_counter() - started


#: How many times a probe is sampled.  A probe is not a ledger row, so
#: one sample would be one number with no spread behind it; several
#: samples give the same kind of distribution a ledger run does.
PROBE_SAMPLES = 15

#: The noise floor of the regression check, in seconds.
#:
#: A class is OVER BUDGET when its typical cost has risen above the
#: baseline by more than the baseline itself, or by more than this
#: floor - the same shape as the suite-performance gate's
#: `SLOW_TEST_REGRESSION_FLOOR_SECONDS` (`scripts/ci_perf_report.py`).
#: The machine is shared by every lane, so a class's measured cost
#: moves with the load; a gate that fired on that movement would be
#: noise dressed as a regression.
REGRESSION_FLOOR_SECONDS = 5.0

#: The measurements the engine does not write to a ledger.
PROBES: dict[str, Callable[[], float]] = {
    "capability_discovery": _probe_capability_discovery,
}


# ── The baseline table ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Budget:
    """One operation's measured budget, and where the number came from."""
    key: str
    class_id: str
    title: str
    unit: str
    signals: tuple[Signal, ...]
    divisor: str = ONE
    target_s: float | None = None
    basis: str = ""
    samples: int = 0
    measured_on: str = ""
    source: str = ""

    @property
    def measured(self) -> bool:
        return self.target_s is not None


@dataclass(frozen=True)
class Baseline:
    """The whole table: the classes' budgets, and the machine they are for."""
    schema: str
    revision: int
    measured_on: str
    machine: dict[str, Any]
    budgets: dict[str, Budget]

    def by_class(self) -> dict[str, list[Budget]]:
        grouped: dict[str, list[Budget]] = {c.id: [] for c in OPERATION_CLASSES}
        for budget in self.budgets.values():
            grouped[budget.class_id].append(budget)
        return grouped


def load_baseline(path: str | os.PathLike | None = None) -> Baseline:
    """Read the baseline table, and refuse one that does not match.

    The table names the operations it budgets.  A table that budgets an
    operation this module does not define, or that leaves one of them
    unbudgeted, is refused rather than half-applied: a budget that
    silently stopped covering an operation is a budget nobody believes.
    """
    target = Path(path) if path else BASELINE_PATH
    with open(target, encoding="utf-8") as handle:
        table = json.load(handle)
    if table.get("schema") != BASELINE_SCHEMA:
        raise ValueError(
            f"{target}: baseline schema {table.get('schema')!r} is not "
            f"{BASELINE_SCHEMA!r}")
    known = _operation_index()
    rows = table.get("operations") or {}
    unknown = sorted(set(rows) - set(known))
    if unknown:
        raise ValueError(
            f"{target}: budgets operations this module does not define: "
            + ", ".join(unknown))
    missing = sorted(set(known) - set(rows))
    if missing:
        raise ValueError(f"{target}: does not budget: " + ", ".join(missing))
    budgets: dict[str, Budget] = {}
    for key, row in rows.items():
        operation = known[key]
        class_id = str(row.get("class") or _class_of(key))
        if class_id not in CLASS_IDS:
            raise ValueError(
                f"{target}: {key} names class {class_id!r}, which is not one "
                "of the seven operation classes")
        value = row.get("target_s")
        if value is not None:
            value = float(value)
        budgets[key] = Budget(
            key=key,
            class_id=class_id,
            title=operation.title,
            unit=operation.unit,
            signals=operation.signals,
            divisor=operation.divisor,
            target_s=value,
            basis=str(row.get("basis") or ""),
            samples=int(row.get("samples") or 0),
            measured_on=str(row.get("measured_on") or ""),
            source=str(row.get("source") or ""),
        )
    return Baseline(
        schema=str(table.get("schema") or ""),
        revision=int(table.get("revision") or 0),
        measured_on=str(table.get("measured_on") or ""),
        machine=dict(table.get("machine") or {}),
        budgets=budgets,
    )


# ── Measuring work, and the contention inside it ──────────────────────────

def _overlap(begin: float, end: float, other_begin: float, other_end: float):
    low = max(begin, other_begin)
    high = min(end, other_end)
    return (low, high) if high > low else None


def _union_length(intervals: Iterable[tuple[float, float]]) -> float:
    """The seconds covered by a set of intervals, counted once each."""
    ordered = sorted(intervals)
    if not ordered:
        return 0.0
    total = 0.0
    low, high = ordered[0]
    for begin, end in ordered[1:]:
        if begin > high:
            total += high - low
            low, high = begin, end
        else:
            high = max(high, end)
    return total + (high - low)


class _Contention:
    """The seconds a run spent QUEUED rather than working.

    Two queues, and both are contention rather than work:

      * `resolve_wait` - the wait for the one Resolve instance.  The
        lease's own hold is work; the queue in front of it is not.  A
        wait that was REFUSED is contention too: the seconds spent
        waiting for a lease that never came are the same seconds, spent
        on the queue either way.
      * `schedule.queue_wait_s` - the wait for the machine's heavy work
        slot, which the scheduler records as a queue wait before the
        scheduled work began.
    """

    def __init__(self, rows: list[dict[str, Any]]):
        self._waits: list[tuple[str | None, float, float]] = []
        self._queue: list[tuple[str | None, float, float]] = []
        self._ingested: dict[str | None, int] = {}
        for row in rows:
            run_id = row.get("run_id")
            if row.get("kind") == perf_ledger.SPAN:
                layer = row.get("layer")
                if layer == perf_ledger.RESOLVE_WAIT:
                    started = float(row.get("started_at") or 0.0)
                    self._waits.append((run_id, started,
                                        started + float(row.get("wall_s") or 0.0)))
                elif layer == "source_ingest":
                    self._ingested[run_id] = self._ingested.get(run_id, 0) + 1
            elif row.get("kind") == perf_ledger.SCHEDULE:
                started = float(row.get("started_at") or 0.0)
                wait = float(row.get("queue_wait_s") or 0.0)
                if wait > 0.0:
                    self._queue.append((run_id, started - wait, started))

    def _overlaps(self, rows, run_id, begin, end) -> list[tuple[float, float]]:
        intervals = []
        for owner_run, low, high in rows:
            if owner_run != run_id:
                continue
            clipped = _overlap(begin, end, low, high)
            if clipped:
                intervals.append(clipped)
        return intervals

    def lease_wait(self, run_id, begin, end) -> float:
        """The seconds this window spent queued for the one Resolve."""
        return _union_length(self._overlaps(self._waits, run_id, begin, end))

    def machine_queue(self, run_id, begin, end) -> float:
        """The seconds this window spent queued for the machine's heavy slot."""
        return _union_length(self._overlaps(self._queue, run_id, begin, end))

    def source_files(self, run_id) -> int:
        """How many source files the run ingested, counted from its rows."""
        return self._ingested.get(run_id, 0)


@dataclass
class Sample:
    """One measured operation: what it cost, and what it waited for."""
    operation: str
    work_s: float
    contention_s: float
    wall_s: float
    units: float
    outcome: str
    run_id: str = ""


@dataclass
class OperationMeasurement:
    """Every sample of one operation, and the verdict against its budget."""
    operation: str
    class_id: str
    samples: list[Sample] = field(default_factory=list)

    @property
    def completed(self) -> list[Sample]:
        return [s for s in self.samples if s.outcome == "ok"]

    @property
    def refused(self) -> list[Sample]:
        return [s for s in self.samples if s.outcome != "ok"]

    def work_seconds(self) -> list[float]:
        """The work per unit, over the completed samples only."""
        return [s.work_s / s.units for s in self.completed if s.units > 0]

    def verdict(self, target_s: float | None) -> str:
        """`within_budget`, `over_budget`, or `unmeasured`.

        The comparison is the class's TYPICAL cost against the budget,
        with the same noise floor the check uses: a class has to cost
        MATERIALLY more than its baseline to be over budget, because
        the machine is shared and one slow sample is the weather.
        """
        if target_s is None:
            return "unmeasured"
        values = self.work_seconds()
        if not values:
            return "unmeasured"
        measured = _quantile(values, 0.5)
        excess = measured - target_s
        return ("over_budget"
                if excess > max(target_s, REGRESSION_FLOOR_SECONDS)
                else "within_budget")


def _quantile(values: list[float], quantile: float) -> float:
    """Nearest-rank quantile; the ledger's own definition."""
    ordered = sorted(values)
    if not ordered:
        raise ValueError("no values")
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _ceiling(value: float) -> float:
    """The value rounded UP to the table's precision.

    A budget is a CEILING, so the number stored must be at or above
    the measurement it was derived from.  Rounding down would store a
    target below a sample that EQUALS it, and the check would then
    report a class as over budget for being exactly on it.
    """
    return math.ceil(value * 10000.0) / 10000.0


def _matches(signal: Signal, row: dict[str, Any]) -> bool:
    """Whether this row is the thing the signal names."""
    kind = row.get("kind")
    if kind != signal.kind:
        return False
    if kind == perf_ledger.CAPABILITY:
        return row.get("capability") == signal.value
    if kind == perf_ledger.TASK:
        return row.get("task_class") == signal.value
    if kind == perf_ledger.SPAN:
        return row.get("layer") == signal.value
    return False


def _window_and_wall(signal: Signal, row: dict[str, Any]) -> tuple[float, float, str] | None:
    """The row's wall, the window it covers, and how it ended."""
    kind = row.get("kind")
    if kind == perf_ledger.CAPABILITY:
        wall = float(row.get("wall_s") or 0.0)
        started = float(row.get("started_at") or 0.0)
        return wall, started, str(row.get("status") or "")
    if kind == perf_ledger.TASK:
        if row.get("state") != "completed":
            return None
        wall = float(row.get("latency_s") or 0.0)
        started = float(row.get("requested_at") or 0.0)
        return wall, started, str(row.get("outcome") or "")
    if kind == perf_ledger.SPAN:
        wall = float(row.get("wall_s") or 0.0)
        started = float(row.get("started_at") or 0.0)
        return wall, started, "ok"
    return None


def _units(operation: Operation, row: dict[str, Any],
           contention: _Contention) -> float:
    """How many units of this operation the row covers."""
    if operation.divisor == ONE:
        return 1.0
    if operation.divisor == CALLS:
        return float(row.get("calls") or 0)
    if operation.divisor == FRAMES:
        return float(row.get("frames") or 0)
    if operation.divisor == SOURCE_FILES:
        return float(contention.source_files(str(row.get("run_id") or "")))
    return 1.0


def _sample_for(operation: Operation, row: dict[str, Any],
                contention: _Contention) -> Sample | None:
    """The work this row cost, and the contention inside it."""
    window = _window_and_wall(operation.signals[0], row)
    if window is None:
        return None
    wall, started, outcome = window
    if wall <= 0.0:
        return None
    run_id = str(row.get("run_id") or "")
    end = started + wall
    waited = contention.lease_wait(run_id, started, end)
    waited += contention.machine_queue(run_id, started, end)
    units = _units(operation, row, contention)
    return Sample(operation=operation.key,
                  work_s=max(0.0, wall - waited), contention_s=waited,
                  wall_s=wall, units=units, outcome=outcome, run_id=run_id)


def measure(rows: list[dict[str, Any]],
            baseline: Baseline | None = None,
            probes: bool = True) -> dict[str, OperationMeasurement]:
    """Measure every operation of every class from one ledger.

    Only COMPLETED operations count towards a budget: a refused edit
    costs what it costs to refuse, and pooling a refusal with an edit
    would make the budget a statement about refusals.  Refusals are
    counted and reported beside the completed samples, never mixed into
    them.
    """
    contention = _Contention(rows)
    operations = _operation_index()
    measurements: dict[str, OperationMeasurement] = {
        key: OperationMeasurement(operation=key, class_id=_class_of(key))
        for key in operations
    }
    for row in rows:
        for key, operation in operations.items():
            if not any(_matches(signal, row) for signal in operation.signals):
                continue
            sample = _sample_for(operation, row, contention)
            if sample is not None:
                measurements[key].samples.append(sample)
    if probes:
        for key, operation in operations.items():
            for signal in operation.signals:
                if signal.kind != PROBE:
                    continue
                probe = PROBES.get(signal.value)
                if probe is None:
                    continue
                for _ in range(PROBE_SAMPLES):
                    seconds = probe()
                    measurements[key].samples.append(Sample(
                        operation=key, work_s=seconds, contention_s=0.0,
                        wall_s=seconds, units=1.0, outcome="ok"))
    return measurements


# ── The check ─────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """One operation that is over its budget, and by how much."""
    class_id: str
    operation: str
    title: str
    measured_p50_s: float
    target_s: float
    samples: int
    ratio: float


def check(measurements: dict[str, OperationMeasurement],
          baseline: Baseline) -> list[Finding]:
    """The operations whose work per unit has regressed past its budget.

    The comparison is a class's TYPICAL (p50) cost against the
    baseline it was measured from.  A single slow sample is the
    weather - one build on a loaded machine - so the bar is the
    baseline's own magnitude (plus a floor), the same shape as the
    suite-performance gate's regression check: the class has to cost
    MATERIALLY more than it did, not a little more.
    """
    findings: list[Finding] = []
    for key, measurement in measurements.items():
        budget = baseline.budgets.get(key)
        if budget is None or budget.target_s is None:
            continue
        values = measurement.work_seconds()
        if not values:
            continue
        measured = _quantile(values, 0.5)
        excess = measured - budget.target_s
        if excess <= max(budget.target_s, REGRESSION_FLOOR_SECONDS):
            continue
        findings.append(Finding(
            class_id=measurement.class_id,
            operation=key,
            title=budget.title,
            measured_p50_s=measured,
            target_s=budget.target_s,
            samples=len(values),
            ratio=measured / budget.target_s if budget.target_s else float("inf")))
    findings.sort(key=lambda f: -f.ratio)
    return findings


def machine_facts() -> dict[str, Any]:
    """What machine a measurement was taken on, in comparable facts."""
    facts: dict[str, Any] = {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cores": os.cpu_count(),
    }
    try:
        completed = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, encoding="utf-8", check=False)
        brand = (completed.stdout or "").strip()
        if brand:
            facts["cpu"] = brand
    except OSError:
        pass
    return facts


def same_machine(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Whether two measurements were taken on the same machine."""
    for key in ("platform", "machine", "cpu", "cores"):
        if left.get(key) is None or right.get(key) is None:
            continue
        if left.get(key) != right.get(key):
            return False
    return True


def report(rows: list[dict[str, Any]], baseline: Baseline,
           measurements: dict[str, OperationMeasurement] | None = None,
           probes: bool = True) -> dict[str, Any]:
    """The per-class table, with the verdict on every operation."""
    measurements = measurements if measurements is not None else measure(
        rows, baseline, probes=probes)
    findings = check(measurements, baseline)
    classes: list[dict[str, Any]] = []
    for cls in OPERATION_CLASSES:
        entry: dict[str, Any] = {"id": cls.id, "title": cls.title, "operations": []}
        for operation in cls.operations:
            measured = measurements[operation.key]
            budget = baseline.budgets[operation.key]
            values = measured.work_seconds()
            entry["operations"].append({
                "key": operation.key,
                "title": operation.title,
                "unit": operation.unit,
                "target_s": budget.target_s,
                "basis": budget.basis,
                "samples": len(values),
                "measured_p50_s": _quantile(values, 0.5) if values else None,
                "measured_p95_s": _quantile(values, 0.95) if values else None,
                "measured_min_s": min(values) if values else None,
                "measured_max_s": max(values) if values else None,
                "contention_s": round(sum(s.contention_s for s in measured.samples), 3),
                "refused": len(measured.refused),
                "measured_on": budget.measured_on,
                "source": budget.source,
                "verdict": measured.verdict(budget.target_s),
            })
        classes.append(entry)
    return {
        "schema": "ren-latency-budget-report/1",
        "baseline_revision": baseline.revision,
        "baseline_measured_on": baseline.measured_on,
        "machine_matches_baseline": same_machine(baseline.machine, machine_facts()),
        "classes": classes,
        "findings": [vars(f) for f in findings],
        "over_budget": len(findings),
    }


def render(result: dict[str, Any]) -> str:
    """The table, as text."""
    lines = ["", "Latency budgets by operation class (work per unit, contention excluded):"]
    if not result.get("machine_matches_baseline", True):
        lines.append("  This machine is not the one the baseline was measured on;"
                     " the numbers below are advisory.")
    for entry in result["classes"]:
        lines.append(f"  {entry['title']}")
        for operation in entry["operations"]:
            target = operation["target_s"]
            measured = operation["measured_p50_s"]
            shown = "unmeasured" if measured is None else f"{measured:.3f}s"
            budget = "unbudgeted" if target is None else f"{target:.3f}s"
            verdict = operation["verdict"]
            mark = {"within_budget": "ok", "over_budget": "OVER",
                    "unmeasured": "-"}.get(verdict, "?")
            lines.append(
                f"    {operation['title']:<44} {shown:>10}  budget {budget:>10}"
                f"  [{mark}]  n={operation['samples']}  per {operation['unit']}"
                + (f"  contention {operation['contention_s']:.1f}s"
                   if operation["contention_s"] else ""))
    findings = result.get("findings") or []
    if findings:
        lines.append("")
        lines.append(f"  {len(findings)} operation(s) over budget:")
        for finding in findings:
            lines.append(
                f"    {finding['title']}: {finding['measured_p50_s']:.3f}s per unit"
                f" against a {finding['target_s']:.3f}s budget"
                f" ({finding['ratio']:.2f}x, n={finding['samples']})")
    else:
        lines.append("")
        lines.append("  Every measured class is within its budget.")
    return "\n".join(lines)


# ── The command ───────────────────────────────────────────────────────────

def _read_rows(project: str | None, ledger: str | None) -> list[dict[str, Any]]:
    if ledger:
        return perf_ledger.read_rows("", ledger)
    if not project:
        raise ValueError("a project or a ledger is required")
    return perf_ledger.read_rows(project)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren budget",
        description="Compare each operation class's measured work against "
                    "its budget, and name the classes that are over it.")
    parser.add_argument("project", nargs="?", help="a project slug or path")
    parser.add_argument("--ledger", help="read a standalone perf ledger JSONL path")
    parser.add_argument("--baseline", help="baseline table path "
                                           f"(default: {BASELINE_PATH.name})")
    parser.add_argument("--no-probe", action="store_true",
                        help="skip the measurements the engine does not write "
                             "to a ledger")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        baseline = load_baseline(args.baseline)
        rows = _read_rows(args.project, args.ledger)
    except (OSError, ValueError) as exc:
        print(f"budget: {exc}", file=sys.stderr)
        return 1
    if not rows:
        # A ledger with no rows says nothing about any class.  Saying
        # "unmeasured" for all of them would read like a measurement
        # that found nothing, which is a different fact from "there is
        # nothing here to measure".
        print("budget: no performance rows to measure. "
              + ("Give a project that has run, or a --ledger path."
                 if not args.project else
                 f"{args.project} has not recorded a run yet."),
              file=sys.stderr)
        return 1
    result = report(rows, baseline, probes=not args.no_probe)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render(result))
    return 1 if result["over_budget"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
