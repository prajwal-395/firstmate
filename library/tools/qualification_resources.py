"""Shared resource measurement recorder for model qualification runs.

A bakeoff lane measures its baseline and candidate implementations with this
recorder and pastes the emitted block into the qualification record that
``library.tools.model_qualification`` gates. Field names and validation match
that gate exactly, so a recorded block is never refused for a missing key.

The recorder runs no model itself. The caller supplies small phase callables
(decode/preprocess, inference, postprocess/serialize, plus a cold-start model
load) already wrapped in the lane's heavy-work lock; this module only times
them with ``time.perf_counter`` and reads process peak RSS with
``resource.getrusage``. Utilization percentages, memory pressure, and the
Resolve-open concurrency outcome are caller-measured and passed in - the
recorder validates them but never invents them. Anything unmeasured stays
explicit (``None`` for utilizations, ``"unknown"`` pressure, ``"not_run"``
concurrency) and adoption is refused downstream until those are measured.
"""

from __future__ import annotations

import resource
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

MEMORY_PRESSURES = ("normal", "warn", "critical", "unknown")
CONCURRENCY = ("passed", "failed", "not_run", "not_supported")

REQUIRED_MEASUREMENTS = (
    "cold_start_ms", "steady_state_ms", "decode_preprocess_ms", "inference_ms",
    "postprocess_serialize_ms", "end_to_end_ms", "peak_rss_mb",
    "throughput_source_hours_per_wall_hour", "useful_evidence_per_compute_second",
)


class ResourceMeasurementError(ValueError):
    """A resource measurement is missing, unmeasured, or contradictory."""


def _nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResourceMeasurementError(f"{name} must be a number")
    result = float(value)
    if result < 0 or result != result or result in (float("inf"), float("-inf")):
        raise ResourceMeasurementError(f"{name} must be a finite non-negative number")
    return result


def _utilization(value: Any, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ResourceMeasurementError(f"{name} must be a percentage or None")
    result = float(value)
    if result != result or not 0 <= result <= 100:
        raise ResourceMeasurementError(f"{name} must be between 0 and 100")
    return result


def peak_rss_mb() -> float:
    """Current process peak resident set size in MiB (portable macOS/Linux)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports KiB.
    unlikely_bytes = peak >= 1024 * 1024 * 1024
    if unlikely_bytes:
        return peak / (1024 * 1024)
    return peak / 1024


@dataclass(frozen=True)
class PhaseTimings:
    """Wall-clock measurements for one implementation pass, milliseconds."""

    decode_preprocess_ms: float
    inference_ms: float
    postprocess_serialize_ms: float
    end_to_end_ms: float


@dataclass(frozen=True)
class ResourceSample:
    """One implementation's resource measurements for a qualification record."""

    cold_start_ms: float | None
    steady_state_ms: float | None
    phases: PhaseTimings
    peak_rss_mb_value: float
    memory_pressure: str = "unknown"
    cpu_utilization_pct: float | None = None
    gpu_utilization_pct: float | None = None
    ane_utilization_pct: float | None = None
    throughput_source_hours_per_wall_hour: float | None = None
    useful_evidence_per_compute_second: float | None = None
    resolve_open_concurrency: str = "not_run"

    def __post_init__(self) -> None:
        for name in ("cold_start_ms", "steady_state_ms"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative(value, name)
        for name in ("decode_preprocess_ms", "inference_ms",
                     "postprocess_serialize_ms", "end_to_end_ms"):
            _nonnegative(getattr(self.phases, name), f"phases.{name}")
        phase_sum = (self.phases.decode_preprocess_ms
                     + self.phases.inference_ms
                     + self.phases.postprocess_serialize_ms)
        if self.phases.end_to_end_ms + 1e-6 < phase_sum:
            raise ResourceMeasurementError(
                "end_to_end_ms cannot be smaller than the sum of its phases")
        _nonnegative(self.peak_rss_mb_value, "peak_rss_mb")
        if self.memory_pressure not in MEMORY_PRESSURES:
            raise ResourceMeasurementError(
                f"memory_pressure must be one of {sorted(MEMORY_PRESSURES)}")
        for name in ("cpu_utilization_pct", "gpu_utilization_pct",
                     "ane_utilization_pct"):
            _utilization(getattr(self, name), name)
        for name in ("throughput_source_hours_per_wall_hour",
                     "useful_evidence_per_compute_second"):
            value = getattr(self, name)
            if value is not None:
                _nonnegative(value, name)
        if self.resolve_open_concurrency not in CONCURRENCY:
            raise ResourceMeasurementError(
                f"resolve_open_concurrency must be one of {sorted(CONCURRENCY)}")

    def to_record_block(self) -> dict[str, Any]:
        """Emit the resources.{role} block the qualification gate consumes."""
        return {
            "cold_start_ms": self.cold_start_ms,
            "steady_state_ms": self.steady_state_ms,
            "decode_preprocess_ms": self.phases.decode_preprocess_ms,
            "inference_ms": self.phases.inference_ms,
            "postprocess_serialize_ms": self.phases.postprocess_serialize_ms,
            "end_to_end_ms": self.phases.end_to_end_ms,
            "peak_rss_mb": self.peak_rss_mb_value,
            "memory_pressure": self.memory_pressure,
            "cpu_utilization_pct": self.cpu_utilization_pct,
            "gpu_utilization_pct": self.gpu_utilization_pct,
            "ane_utilization_pct": self.ane_utilization_pct,
            "throughput_source_hours_per_wall_hour":
                self.throughput_source_hours_per_wall_hour,
            "useful_evidence_per_compute_second":
                self.useful_evidence_per_compute_second,
            "resolve_open_concurrency": self.resolve_open_concurrency,
        }


def time_phase(work: Callable[[], Any]) -> tuple[float, Any]:
    """Run one phase, returning wall milliseconds and the phase result."""
    start = time.perf_counter()
    result = work()
    return (time.perf_counter() - start) * 1000.0, result


def measure_implementation(
    *,
    load_model: Callable[[], Any] | None,
    decode_preprocess: Callable[[], Any],
    infer: Callable[[], Any],
    postprocess_serialize: Callable[[], Any],
    source_seconds: float,
    useful_evidence_count: int,
    memory_pressure: str = "unknown",
    utilizations: Mapping[str, float | None] | None = None,
    resolve_open_concurrency: str = "not_run",
) -> ResourceSample:
    """Time one implementation pass and derive throughput/evidence rates.

    ``load_model`` runs once for the cold start and may be ``None`` when the
    model was already resident (cold start then stays unmeasured). The three
    phase callables run once for the steady-state pass; their wall times are
    recorded separately so decode, inference, and serialization costs never
    collapse into one number. ``source_seconds`` is the measured source audio
    or video duration the pass consumed; ``useful_evidence_count`` is the
    caller's count of evidence items the pass actually produced.
    """
    _nonnegative(source_seconds, "source_seconds")
    if (isinstance(useful_evidence_count, bool)
            or not isinstance(useful_evidence_count, int)
            or useful_evidence_count < 0):
        raise ResourceMeasurementError(
            "useful_evidence_count must be a non-negative integer")
    if memory_pressure not in MEMORY_PRESSURES:
        raise ResourceMeasurementError(
            f"memory_pressure must be one of {sorted(MEMORY_PRESSURES)}")
    if resolve_open_concurrency not in CONCURRENCY:
        raise ResourceMeasurementError(
            f"resolve_open_concurrency must be one of {sorted(CONCURRENCY)}")
    utils: dict[str, float | None] = {}
    for name in ("cpu_utilization_pct", "gpu_utilization_pct",
                 "ane_utilization_pct"):
        raw = (utilizations or {}).get(name)
        utils[name] = _utilization(raw, name)

    cold_ms: float | None = None
    if load_model is not None:
        cold_ms, _ = time_phase(load_model)
    decode_ms, _ = time_phase(decode_preprocess)
    infer_ms, infer_result = time_phase(infer)
    post_ms, _ = time_phase(postprocess_serialize)
    end_to_end_ms = decode_ms + infer_ms + post_ms
    steady_ms = end_to_end_ms

    wall_hours = end_to_end_ms / 3_600_000.0
    throughput = (source_seconds / 3600.0) / wall_hours if wall_hours > 0 else None
    compute_seconds = end_to_end_ms / 1000.0
    evidence_rate = (useful_evidence_count / compute_seconds
                     if compute_seconds > 0 else None)
    _ = infer_result
    return ResourceSample(
        cold_start_ms=cold_ms,
        steady_state_ms=steady_ms,
        phases=PhaseTimings(
            decode_preprocess_ms=decode_ms,
            inference_ms=infer_ms,
            postprocess_serialize_ms=post_ms,
            end_to_end_ms=end_to_end_ms,
        ),
        peak_rss_mb_value=peak_rss_mb(),
        memory_pressure=memory_pressure,
        cpu_utilization_pct=utils["cpu_utilization_pct"],
        gpu_utilization_pct=utils["gpu_utilization_pct"],
        ane_utilization_pct=utils["ane_utilization_pct"],
        throughput_source_hours_per_wall_hour=throughput,
        useful_evidence_per_compute_second=evidence_rate,
        resolve_open_concurrency=resolve_open_concurrency,
    )
