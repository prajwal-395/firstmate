"""Pure-numpy geometric readings over depth and camera estimates.

Every function here is estimator-agnostic: it takes maps, poses and
confidence arrays in the lane conventions and returns numbers with
their basis stated. No model, no network, no footage - the benchmark
calls these, and the hermetic tests feed them synthetic arrays.

Conventions: depth maps are brighter-is-nearer (`depth_da3` already
oriented them); extrinsics are `(N, 3, 4)` or `(N, 4, 4)` world-to-cam
as DA3 reports; confidence is the estimator's native scale, compared
only within one estimator (GEO-009).
"""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np

STATIC_TRANSLATION_PER_SEC = 0.02
"""Below this per-second translation drift a tripod shot reads static.
In the estimator's native depth units per second; the benchmark
measures what stillness actually costs rather than tuning this."""
STATIC_ROTATION_DEG_PER_SEC = 0.5
SUBJECT_DOMINANT_RATIO = 3.0
"""Flow exceeds camera-explained motion by this factor before an
interval reads subject-dominant (GEO-007)."""


def _ranks(values: np.ndarray) -> np.ndarray:
    """Average ranks, so tied cells share one rank: without this a map
    of two plateaus correlates with its own negation at 0.44, not -1."""
    order = np.argsort(values, kind="stable")
    ordered = values[order]
    ranks = np.empty(values.size, dtype=np.float64)
    start = 0
    while start < values.size:
        end = start + 1
        while end < values.size and ordered[end] == ordered[start]:
            end += 1
        ranks[order[start:end]] = (start + end - 1) / 2.0
        start = end
    return ranks


def rank_correlation(first: np.ndarray, second: np.ndarray,
                     levels: int = 64) -> float:
    """Spearman rho between two maps: agreement up to any monotone
    rescale, which is the only agreement relative depth can promise.
    Both maps are quantised to `levels` first, so sub-perceptual
    estimator noise inside a flat region does not scramble the ranks."""
    first = np.asarray(first, dtype=np.float64).ravel()
    second = np.asarray(second, dtype=np.float64).ravel()
    if first.shape != second.shape or first.size < 2:
        raise ValueError("rank correlation needs two same-shaped maps "
                         "with at least two cells")
    if levels < 2:
        raise ValueError("rank correlation needs at least two levels")
    first = np.floor((first - first.min())
                     / max(first.max() - first.min(), 1e-12) * levels)
    second = np.floor((second - second.min())
                      / max(second.max() - second.min(), 1e-12) * levels)
    if np.all(first == first[0]) or np.all(second == second[0]):
        raise ValueError("rank correlation is undefined on a constant map")
    first_r, second_r = _ranks(first), _ranks(second)
    first_r -= first_r.mean()
    second_r -= second_r.mean()
    denom = math.sqrt(float((first_r ** 2).sum() * (second_r ** 2).sum()))
    if denom == 0:
        raise ValueError("rank correlation is undefined on a constant map")
    return float((first_r * second_r).sum() / denom)


def temporal_consistency(maps: list[np.ndarray]) -> list[float]:
    """Adjacent-frame rank agreement down a sequence (GEO-004). A
    perfectly still estimator on still footage reads near 1; warping
    reads lower. Raises on fewer than two maps."""
    if len(maps) < 2:
        raise ValueError("temporal consistency needs at least two maps")
    return [rank_correlation(first, second)
            for first, second in pairwise(maps)]


def edge_agreement(depth: np.ndarray, gray: np.ndarray,
                   top_fraction: float = 0.05,
                   tolerance_px: int = 2) -> float:
    """Fraction of the strongest depth discontinuities landing on image
    edges (GEO-004's spatial half). Depth edges that follow no picture
    edge are estimator invention, not scene structure."""
    import cv2

    if not 0 < top_fraction < 1:
        raise ValueError("top_fraction must be a proper fraction")
    depth = np.asarray(depth, dtype=np.float64)
    gray = np.asarray(gray, dtype=np.uint8)
    if depth.shape != gray.shape:
        raise ValueError(f"depth is {depth.shape}, picture is "
                         f"{gray.shape} - one window, one grid")
    gy, gx = np.gradient(depth)
    strength = np.hypot(gx, gy)
    edges = cv2.Canny(gray, 50, 150) > 0
    if not edges.any():
        raise ValueError("the picture has no detectable edges to agree with")
    cutoff = np.quantile(strength, 1.0 - top_fraction)
    hot = strength >= cutoff
    if not hot.any():
        raise ValueError("the depth map is flat at the top quantile")
    dist = cv2.distanceTransform((~edges).astype(np.uint8),
                                 cv2.DIST_L2, 3)
    return float((dist[hot] <= tolerance_px).mean())


def _as_4x4(extrinsic: np.ndarray) -> np.ndarray:
    matrix = np.asarray(extrinsic, dtype=np.float64)
    if matrix.shape == (3, 4):
        bottom = np.zeros((1, 4))
        bottom[0, 3] = 1.0
        return np.vstack([matrix, bottom])
    if matrix.shape == (4, 4):
        return matrix
    raise ValueError(f"extrinsic is {matrix.shape}, expected (3, 4) or (4, 4)")


def relative_motion(extrinsics: np.ndarray) -> list[dict]:
    """Per-step camera motion between consecutive views (GEO-006):
    translation norm plus rotation angle in degrees, both in the
    estimator's native scale. Raises on fewer than two views."""
    matrices = [_as_4x4(e) for e in np.asarray(extrinsics)]
    if len(matrices) < 2:
        raise ValueError("relative motion needs at least two views")
    steps = []
    for first, second in pairwise(matrices):
        delta = second @ np.linalg.inv(first)
        translation = float(np.linalg.norm(delta[:3, 3]))
        trace = float(np.trace(delta[:3, :3]))
        cos_angle = min(1.0, max(-1.0, (trace - 1.0) / 2.0))
        steps.append({"translation": translation,
                      "rotation_deg": math.degrees(math.acos(cos_angle))})
    return steps


def stillness(steps: list[dict], seconds_per_step: float) -> dict:
    """Whether a view sequence reads as a locked-off camera (GEO-005).
    Reports per-second drift beside the verdict, never just the word."""
    if seconds_per_step <= 0:
        raise ValueError("seconds_per_step must be positive")
    if not steps:
        raise ValueError("stillness needs at least one motion step")
    trans = float(np.median([s["translation"] for s in steps])
                  / seconds_per_step)
    rot = float(np.median([s["rotation_deg"] for s in steps])
                / seconds_per_step)
    return {"translation_per_sec": trans,
            "rotation_deg_per_sec": rot,
            "reads_static": bool(trans <= STATIC_TRANSLATION_PER_SEC
                                 and rot <= STATIC_ROTATION_DEG_PER_SEC)}


def motion_attribution(camera_per_sec: float, flow_per_sec: float,
                       subject_energy: float | None) -> dict:
    """Whose motion the picture's movement is (GEO-007): the camera's,
    the subject's, both, or neither. Flow far above camera-explained
    motion with subject energy present reads subject-dominant; flow at
    the camera's own rate reads camera-dominant."""
    if camera_per_sec < 0 or flow_per_sec < 0:
        raise ValueError("motion rates cannot be negative")
    if flow_per_sec <= STATIC_TRANSLATION_PER_SEC:
        return {"attribution": "still", "camera_per_sec": camera_per_sec,
                "flow_per_sec": flow_per_sec}
    if (camera_per_sec > STATIC_TRANSLATION_PER_SEC
            and flow_per_sec <= SUBJECT_DOMINANT_RATIO * camera_per_sec):
        return {"attribution": "camera-dominant",
                "camera_per_sec": camera_per_sec,
                "flow_per_sec": flow_per_sec}
    if subject_energy is not None and subject_energy > 0:
        return {"attribution": "subject-dominant",
                "camera_per_sec": camera_per_sec,
                "flow_per_sec": flow_per_sec}
    return {"attribution": "unattributed-motion",
            "camera_per_sec": camera_per_sec,
            "flow_per_sec": flow_per_sec}


def confidence_reliability(confidence: np.ndarray,
                           disagreement: np.ndarray,
                           bins: int = 5) -> dict:
    """Whether higher confidence means lower temporal disagreement
    (GEO-009). Bins cells by confidence, reports mean disagreement per
    bin plus the rank agreement between bin order and bin error: +1 is
    perfectly calibrated confidence, 0 is decoration."""
    if bins < 2:
        raise ValueError("reliability needs at least two bins")
    conf = np.asarray(confidence, dtype=np.float64).ravel()
    dis = np.asarray(disagreement, dtype=np.float64).ravel()
    if conf.shape != dis.shape or conf.size < bins:
        raise ValueError("confidence and disagreement must match with "
                         "at least one cell per bin")
    edges = np.quantile(conf, np.linspace(0, 1, bins + 1))
    rows = []
    for low, high in pairwise(edges):
        cell = (conf >= low) & (conf <= high if high == edges[-1]
                                else conf < high)
        if not cell.any():
            continue
        rows.append({"conf_mean": float(conf[cell].mean()),
                     "disagreement_mean": float(dis[cell].mean()),
                     "cells": int(cell.sum())})
    order = rank_correlation(
        np.array([r["conf_mean"] for r in rows]),
        np.array([-r["disagreement_mean"] for r in rows])) \
        if len(rows) >= 2 else 0.0
    return {"bins": rows, "calibration_rho": float(order)}


def summarize_interval(depth: np.ndarray, confidence: np.ndarray | None,
                       camera_state: str, depth_kind: str,
                       unit: str) -> dict:
    """The searchable per-interval record (GEO-010): near/mid/far
    tercile fractions, mean confidence, the camera state the pose read,
    and the scale the numbers are in. Relative and metric records are
    never mixed: `depth_kind`/`unit` travel with the numbers (GEO-008)."""
    if depth_kind not in ("relative", "metric"):
        raise ValueError(f"depth_kind {depth_kind!r} is not relative/metric")
    if camera_state not in ("static", "moving", "unknown"):
        raise ValueError(f"camera_state {camera_state!r} is not "
                         f"static/moving/unknown")
    depth = np.asarray(depth, dtype=np.float64)
    if depth.size == 0:
        raise ValueError("cannot summarize an empty depth map")
    mean_confidence: float | None = None
    if confidence is not None:
        confidence = np.asarray(confidence, dtype=np.float64)
        if not np.isfinite(confidence).all():
            raise ValueError("confidence carries non-finite cells - "
                             "an unmeasured map, not a number")
        mean_confidence = float(np.mean(confidence))
    thirds = np.quantile(depth, [1 / 3, 2 / 3])
    return {"near_fraction": float((depth >= thirds[1]).mean()),
            "mid_fraction": float(((depth >= thirds[0])
                                   & (depth < thirds[1])).mean()),
            "far_fraction": float((depth < thirds[0]).mean()),
            "mean_confidence": mean_confidence,
            "camera_state": camera_state,
            "depth_kind": depth_kind,
            "unit": unit}


def select_intervals(records: list[dict], *,
                     camera_state: str | None = None,
                     depth_kind: str | None = None,
                     min_confidence: float | None = None) -> list[dict]:
    """The searchable read over per-interval records (GEO-010): keep the
    records matching every stated predicate. A record missing a filtered
    key is OUT, never a match; a `None` confidence never clears a
    `min_confidence` bar. Pure filter - the retrieval lane owns the
    index, this owns the predicate."""
    kept = []
    for record in records:
        if camera_state is not None \
                and record.get("camera_state") != camera_state:
            continue
        if depth_kind is not None \
                and record.get("depth_kind") != depth_kind:
            continue
        if min_confidence is not None:
            conf = record.get("mean_confidence")
            if conf is None or not np.isfinite(conf) \
                    or float(conf) < min_confidence:
                continue
        kept.append(record)
    return kept


def match_inliers(gray_first: np.ndarray, gray_second: np.ndarray,
                  depth_first: np.ndarray, depth_second: np.ndarray,
                  depth_tolerance: float = 0.15) -> dict:
    """Correspondence with and without depth gating (GEO-011): ORB
    matches between adjacent frames, filtered to pairs whose
    brighter-nearer values agree within tolerance. Reports both inlier
    counts so the reader sees what the gate costs and what it buys."""
    import cv2

    gray_first = np.asarray(gray_first, dtype=np.uint8)
    gray_second = np.asarray(gray_second, dtype=np.uint8)
    depth_first = np.asarray(depth_first, dtype=np.float64)
    depth_second = np.asarray(depth_second, dtype=np.float64)
    if gray_first.shape != gray_second.shape:
        raise ValueError("both frames must share one shape")
    if depth_first.shape != gray_first.shape \
            or depth_second.shape != gray_first.shape:
        raise ValueError("each depth map must match its frame")
    orb = cv2.ORB_create(nfeatures=500)
    key_first, desc_first = orb.detectAndCompute(gray_first, None)
    key_second, desc_second = orb.detectAndCompute(gray_second, None)
    if desc_first is None or desc_second is None or len(key_first) < 4 \
            or len(key_second) < 4:
        return {"matches": 0, "inliers_plain": 0, "inliers_gated": 0,
                "unmeasured_because": "too few keypoints to match"}
    pairs = cv2.BFMatcher(cv2.NORM_HAMMING,
                          crossCheck=True).match(desc_first, desc_second)
    if len(pairs) < 4:
        return {"matches": len(pairs), "inliers_plain": 0,
                "inliers_gated": 0,
                "unmeasured_because": "too few matches for a homography"}
    height, width = gray_first.shape
    gated = []
    for pair in pairs:
        x0, y0 = (round(v) for v in key_first[pair.queryIdx].pt)
        x1, y1 = (round(v) for v in key_second[pair.trainIdx].pt)
        if not (0 <= x0 < width and 0 <= y0 < height
                and 0 <= x1 < width and 0 <= y1 < height):
            continue
        scale = max(depth_first.max(), depth_second.max(), 1e-9)
        if abs(float(depth_first[y0, x0] - depth_second[y1, x1])) \
                / scale <= depth_tolerance:
            gated.append(pair)
    plain = _homography_inliers(key_first, key_second, pairs)
    kept = _homography_inliers(
        key_first, key_second, gated) if len(gated) >= 4 else 0
    return {"matches": len(pairs), "gated_matches": len(gated),
            "inliers_plain": plain, "inliers_gated": kept}


def _homography_inliers(key_first, key_second, pairs) -> int:
    import cv2

    src = np.float32([key_first[p.queryIdx].pt for p in pairs])
    dst = np.float32([key_second[p.trainIdx].pt for p in pairs])
    _matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    if mask is None:
        return 0
    return int(mask.sum())
