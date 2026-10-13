"""Small, deterministic helpers for the Project Model's evidence contract.

These helpers operate on already-collected records. They do not schedule
analysis or infer absence from an empty result set.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from math import ceil, floor


class Certainty(str, Enum):
    OBSERVED = "observed"
    MEASURED = "measured"
    INFERRED = "inferred"
    INTERPRETED = "interpreted"


class ProcessingStatus(str, Enum):
    ANALYZED = "analyzed"
    NOT_ANALYZED = "not_analyzed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class QueryStatus(str, Enum):
    PRESENT = "present"
    CONFIDENTLY_ABSENT = "confidently_absent"
    NOT_YET_ANALYSED = "not_yet_analysed"
    INCONCLUSIVE = "inconclusive"


LEGACY_CERTAINTY = {
    "measurement": Certainty.MEASURED,
    "inference": Certainty.INFERRED,
    "creative_judgment": Certainty.INTERPRETED,
    # Evaluation is a policy result, not an epistemic certainty.
    "evaluation": None,
}


@dataclass(frozen=True)
class TimeMap:
    """One affine segment: target = source * slope + offset.

    Integer ticks and Fraction arithmetic retain source PTS/sample precision.
    A caller must provide a single segment whose half-open range contains the
    requested tick; gaps and ambiguous overlaps refuse.
    """

    source_clock: str
    target_clock: str
    source_start: int
    source_end: int
    target_start: int
    target_end: int
    slope: Fraction
    uncertainty_ticks: int = 0
    offset_ticks: Fraction | None = None
    invertible: bool = True

    def __post_init__(self) -> None:
        if self.source_start >= self.source_end or self.target_start >= self.target_end:
            raise ValueError("time-map segments must be non-empty half-open intervals")
        if self.slope <= 0 or self.uncertainty_ticks < 0:
            raise ValueError(
                "time-map slope must be positive and uncertainty non-negative"
            )

    def map_tick(self, tick: int) -> Fraction:
        if not self.source_start <= tick < self.source_end:
            raise ValueError("tick falls outside transform segment")
        offset = self.offset_ticks
        if offset is None:
            offset = Fraction(self.target_start) - self.source_start * self.slope
        return tick * self.slope + offset

    def inverse(self) -> TimeMap:
        if not self.invertible or self.slope == 0:
            raise ValueError("non-invertible time transform")
        offset = self.offset_ticks
        if offset is None:
            offset = Fraction(self.target_start) - self.source_start * self.slope
        inverse_slope = 1 / self.slope
        return TimeMap(
            self.target_clock,
            self.source_clock,
            self.target_start,
            self.target_end,
            self.source_start,
            self.source_end,
            inverse_slope,
            ceil(Fraction(self.uncertainty_ticks) * inverse_slope),
            -offset * inverse_slope,
        )


def compose_time_maps(first: TimeMap, second: TimeMap) -> TimeMap:
    """Compose contiguous affine segments, adding uncertainty conservatively."""
    if first.target_clock != second.source_clock:
        raise ValueError("time transform clock chain does not join")
    mapped_start = first.map_tick(first.source_start)
    mapped_end = first.map_tick(first.source_end - 1)
    if mapped_start < second.source_start or mapped_end >= second.source_end:
        raise ValueError("time transforms do not cover a continuous shared range")
    second_offset = second.offset_ticks
    if second_offset is None:
        second_offset = (
            Fraction(second.target_start) - second.source_start * second.slope
        )
    start = mapped_start * second.slope + second_offset
    end = mapped_end * second.slope + second_offset
    if end < start:
        raise ValueError("time transform is not monotonic")
    uncertainty = ceil(
        Fraction(first.uncertainty_ticks) * second.slope + second.uncertainty_ticks
    )
    first_offset = first.offset_ticks
    if first_offset is None:
        first_offset = Fraction(first.target_start) - first.source_start * first.slope
    return TimeMap(
        first.source_clock,
        second.target_clock,
        first.source_start,
        first.source_end,
        floor(start),
        ceil(end) + 1,
        first.slope * second.slope,
        uncertainty,
        second.slope * first_offset + second_offset,
        first.invertible and second.invertible,
    )


@dataclass(frozen=True)
class QueryCoverage:
    status: ProcessingStatus
    complete: bool
    fresh: bool
    candidates: tuple[str, ...] = ()


def query_status(coverage: Iterable[QueryCoverage]) -> QueryStatus:
    """Resolve the four query outcomes from explicit lane coverage."""
    lanes = tuple(coverage)
    if any(
        lane.candidates and lane.status is ProcessingStatus.ANALYZED and lane.fresh
        for lane in lanes
    ):
        return QueryStatus.PRESENT
    if not lanes or any(
        lane.status
        in (
            ProcessingStatus.NOT_ANALYZED,
            ProcessingStatus.UNAVAILABLE,
            ProcessingStatus.FAILED,
        )
        for lane in lanes
    ):
        return QueryStatus.NOT_YET_ANALYSED
    if any(not lane.fresh for lane in lanes):
        return QueryStatus.NOT_YET_ANALYSED
    if all(
        lane.status is ProcessingStatus.ANALYZED and lane.complete and lane.fresh
        for lane in lanes
    ):
        return QueryStatus.CONFIDENTLY_ABSENT
    return QueryStatus.INCONCLUSIVE


def validate_typed_confidence(
    value: float, kind: str, producer: str, calibration_version: str | None = None
) -> None:
    if not producer or kind not in {
        "rank",
        "logit",
        "cosine",
        "calibrated_probability",
        "uncertainty",
        "raw_score",
    }:
        raise ValueError("confidence requires a recognized score kind and producer")
    if kind == "calibrated_probability" and not calibration_version:
        raise ValueError("calibrated probability requires a calibration version")
    if not math.isfinite(value):
        raise ValueError("confidence value must be finite")
    if kind in {"calibrated_probability", "uncertainty"} and not 0 <= value <= 1:
        raise ValueError(f"{kind} must be between 0 and 1")
