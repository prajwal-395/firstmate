"""Typed depth and camera/world geometry contracts.

Monocular depth is relative. A value becomes metric only when a scale
calibration with recorded provenance is supplied; camera/world transforms
are explicit homogeneous transforms with named coordinate spaces.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum


class GeometryRefusal(ValueError):
    """Requested geometry is unsupported by the available evidence."""


class DepthKind(StrEnum):
    RELATIVE = "relative"
    METRIC = "metric"


@dataclass(frozen=True)
class ScaleCalibration:
    """Proven scale mapping from estimator units to metres."""

    id: str
    metres_per_unit: float
    method: str
    provenance_id: str

    def __post_init__(self) -> None:
        if not self.id or not self.method or not self.provenance_id:
            raise GeometryRefusal("scale calibration needs id, method, and provenance")
        if not math.isfinite(self.metres_per_unit) or self.metres_per_unit <= 0:
            raise GeometryRefusal("scale calibration must be finite and positive")


@dataclass(frozen=True)
class DepthReading:
    kind: DepthKind
    value: float
    unit: str
    calibration_id: str | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.value):
            raise GeometryRefusal("depth reading must be finite")
        if self.kind is DepthKind.RELATIVE:
            if self.unit != "normalized" or self.calibration_id is not None:
                raise GeometryRefusal("relative depth must be normalized and uncalibrated")
        elif self.unit != "m" or not self.calibration_id:
            raise GeometryRefusal("metric depth requires metres and scale calibration")


def metric_depth(value: float, *, calibration: ScaleCalibration | None,
                 unit: str = "estimator_unit") -> DepthReading:
    """Convert a scale-bearing measurement to metres, refusing guesses."""
    if calibration is None:
        raise GeometryRefusal("metric depth requires scale calibration")
    if unit != "estimator_unit":
        raise GeometryRefusal(f"unsupported calibrated depth unit {unit!r}")
    return DepthReading(DepthKind.METRIC,
                        float(value) * calibration.metres_per_unit, "m",
                        calibration.id)


@dataclass(frozen=True)
class CameraWorldTransform:
    """Rigid camera-to-world transform in row-major homogeneous form."""

    from_space: str
    to_space: str
    matrix: tuple[float, ...]
    provenance_id: str

    def __post_init__(self) -> None:
        if not self.from_space or not self.to_space or not self.provenance_id:
            raise GeometryRefusal("transform needs named spaces and provenance")
        if len(self.matrix) != 16 or not all(map(math.isfinite, self.matrix)):
            raise GeometryRefusal("camera/world transform must be a finite 4x4 matrix")
        m = self.matrix
        if any(abs(m[i] - expected) > 1e-6 for i, expected in
               zip((12, 13, 14, 15), (0.0, 0.0, 0.0, 1.0))):
            raise GeometryRefusal("transform must be homogeneous")

    def apply(self, point: Sequence[float]) -> tuple[float, float, float]:
        if len(point) != 3 or not all(map(math.isfinite, point)):
            raise GeometryRefusal("3D point must contain three finite coordinates")
        x, y, z = point
        m = self.matrix
        out = (m[0]*x + m[1]*y + m[2]*z + m[3],
               m[4]*x + m[5]*y + m[6]*z + m[7],
               m[8]*x + m[9]*y + m[10]*z + m[11])
        return out
