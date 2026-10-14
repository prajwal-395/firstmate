"""Evidence-bound continuity and transform feasibility scoring.

This module consumes measurements; it never extracts pixels. Missing evidence
stays ``unmeasured`` and cannot produce a safe verdict. Scores are pairwise
comparisons with explicit measurement provenance, not semantic judgments.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

CONTINUITY_AXES = ("screen_direction", "scale", "light", "color", "motion", "audio")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _difference_score(left: Any, right: Any, tolerance: float) -> dict:
    a, b = _finite_number(left), _finite_number(right)
    if a is None or b is None:
        return {"status": "unmeasured", "score": None, "measurements": []}
    delta = abs(a - b)
    return {"status": "measured", "score": round(max(0.0, 1.0 - delta / tolerance), 4),
            "difference": round(delta, 4), "measurements": [a, b]}


def score_shot_pair(left: Mapping[str, Any], right: Mapping[str, Any],
                    policy: Mapping[str, Any] | None = None) -> dict:
    """Compare paired measured features using caller-owned policy tolerances.

    Each axis is returned independently. A global score is present only when
    both the evidence and policy define at least one comparable axis.
    """
    policy = policy or {}
    tolerances = policy.get("tolerances", {})
    if not isinstance(tolerances, Mapping):
        tolerances = {}
    result = {}
    for axis in CONTINUITY_AXES:
        if axis == "screen_direction":
            a, b = left.get(axis), right.get(axis)
            result[axis] = ({"status": "measured", "score": 1.0 if a == b else 0.0,
                             "measurements": [a, b]}
                            if isinstance(a, str) and isinstance(b, str)
                            else {"status": "unmeasured", "score": None,
                                  "measurements": []})
        else:
            tolerance = _finite_number(tolerances.get(axis))
            result[axis] = (_difference_score(left.get(axis), right.get(axis), tolerance)
                            if tolerance is not None and tolerance > 0 else
                            {"status": "unmeasured", "score": None,
                             "measurements": [], "reason": "policy tolerance absent"})
    scores = [row["score"] for row in result.values() if row["score"] is not None]
    result["overall"] = round(sum(scores) / len(scores), 4) if scores else None
    result["status"] = "measured" if scores else "unmeasured"
    result["policy_id"] = policy.get("id")
    return result


def assess_target_crop(subject_box: Mapping[str, Any] | None,
                       crop: Mapping[str, Any] | None,
                       policy: Mapping[str, Any] | None = None) -> dict:
    """Measure crop containment and policy-approved overlay regions.

    Boxes use normalized ``[left, top, right, bottom]`` coordinates. Overlay
    safety is only asserted when the subject and an allowed region are both
    measured/provided and the crop contains that region.
    """
    policy = policy or {}
    box = subject_box.get("box") if isinstance(subject_box, Mapping) else None
    crop_box = crop.get("box") if isinstance(crop, Mapping) else None
    valid = lambda b: (isinstance(b, (list, tuple)) and len(b) == 4
                       and all(_finite_number(v) is not None for v in b)
                       and 0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1)
    if not valid(box) or not valid(crop_box):
        return {"status": "unmeasured", "safe": None,
                "reason": "subject and target crop boxes are required measurements",
                "measurements": []}
    contained = (crop_box[0] <= box[0] and crop_box[1] <= box[1]
                 and crop_box[2] >= box[2] and crop_box[3] >= box[3])
    allowed = policy.get("overlay_regions", [])
    overlays = []
    for region in allowed:
        if not valid(region):
            continue
        region_in_crop = (crop_box[0] <= region[0] and crop_box[1] <= region[1]
                          and crop_box[2] >= region[2] and crop_box[3] >= region[3])
        overlays.append({"region": list(region), "safe": bool(contained and region_in_crop and
                        (region[2] <= box[0] or region[0] >= box[2] or
                         region[3] <= box[1] or region[1] >= box[3])),
                         "basis": "measured_subject_box_and_policy_region"})
    direction = (subject_box.get("facing_direction")
                 or subject_box.get("movement_direction"))
    lead_room = (round(1.0 - box[2], 4) if direction == "right" else
                 round(box[0], 4) if direction == "left" else None)
    return {"status": "measured", "subject_contained": contained,
            "safe": contained if not overlays else all(row["safe"] for row in overlays),
            "subject_position": [round((box[0] + box[2]) / 2, 4),
                                 round((box[1] + box[3]) / 2, 4)],
            "headroom": round(box[1] - crop_box[1], 4),
            "lead_room": lead_room,
            "overlays": overlays, "measurements": {"subject_box": list(box),
            "crop_box": list(crop_box)}, "policy_id": policy.get("id")}


def transformation_feasibility(measurements: Mapping[str, Any],
                               policy: Mapping[str, Any] | None = None) -> dict:
    """Report stabilize/grade feasibility only when measured limits exist."""
    policy = policy or {}
    motion = _finite_number(measurements.get("residual_motion"))
    headroom = _finite_number(measurements.get("grade_headroom"))
    motion_limit = _finite_number(policy.get("max_residual_motion"))
    grade_limit = _finite_number(policy.get("min_grade_headroom"))
    stabilize = ("unmeasured" if motion is None or motion_limit is None else
                 "feasible" if motion <= motion_limit else "infeasible")
    grade = ("unmeasured" if headroom is None or grade_limit is None else
             "feasible" if headroom >= grade_limit else "infeasible")
    return {"stabilize": stabilize, "grade": grade,
            "basis": {"residual_motion": motion, "max_residual_motion": motion_limit,
                      "grade_headroom": headroom, "min_grade_headroom": grade_limit},
            "policy_id": policy.get("id")}
