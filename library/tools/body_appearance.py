"""Person body-appearance descriptor for face-absent re-identification.

NO model weights, NO downloads, NO learned parameters: pure numpy/cv2
arithmetic over caller-provided person-box crops. Commercially eligible
by construction: nothing ships but code.

It answers "same clothes/appearance?" across clips where no face is
visible. It is NOT a face recognizer and carries no identity: two people
in similar clothing will match.

Known limits: a clothing change defeats it (body ReID depends on
clothing, pose and occlusion, as insightface's own ReID notes state);
viewpoint and occlusion robustness are unmeasured here. Thresholds are
measured elsewhere and passed in by callers: :func:`match` returns a
plain similarity with no threshold inside.
"""

from __future__ import annotations

import cv2
import numpy as np

STUDY_DIM = 384
"""Descriptor length: 3 thirds x 8 H bins x 4 S bins x 4 V bins."""

_H_BINS = 8
_S_BINS = 4
_V_BINS = 4
_N_THIRDS = 3


def _as_crop(pixels: np.ndarray) -> np.ndarray:
    """Validate a BGR person-box crop, raising ValueError when degenerate."""
    if pixels is None:
        raise ValueError("empty person crop: None carries no appearance")
    arr = np.asarray(pixels)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"person crop must be HxWx3, got shape {arr.shape}")
    if arr.shape[0] <= 0 or arr.shape[1] <= 0:
        raise ValueError("empty person crop: zero area carries no appearance")
    if arr.dtype != np.uint8:
        arr = arr.astype(np.uint8)
    return arr


def describe_person_crop(pixels: np.ndarray) -> tuple[float, ...]:
    """Describe a person-box crop as a 384-d L2-unit HSV histogram vector.

    Split the crop into horizontal thirds (head/shoulders, torso, legs
    as a clothing proxy); per third take an 8x4x4 HSV histogram,
    L1-normalized per third, then concatenate and L2-normalize the whole
    vector. Returned floats are rounded to 6dp.
    """
    arr = _as_crop(pixels)
    hsv = cv2.cvtColor(arr, cv2.COLOR_BGR2HSV)
    height = hsv.shape[0]
    bounds = [0, height // _N_THIRDS, (2 * height) // _N_THIRDS, height]
    parts: list[np.ndarray] = []
    for index in range(_N_THIRDS):
        third = hsv[bounds[index] : bounds[index + 1]]
        if third.size == 0:
            parts.append(np.zeros(_H_BINS * _S_BINS * _V_BINS, dtype=np.float64))
            continue
        hist = (
            cv2.calcHist(
                [third],
                [0, 1, 2],
                None,
                [_H_BINS, _S_BINS, _V_BINS],
                [0, 180, 0, 256, 0, 256],
            )
            .flatten()
            .astype(np.float64)
        )
        total = hist.sum()
        if total > 0:
            hist /= total
        parts.append(hist)
    vector = np.concatenate(parts)
    norm = float(np.sqrt(float((vector * vector).sum())))
    if norm <= 0.0:
        raise ValueError("degenerate person crop: empty histogram")
    vector /= norm
    assert vector.shape[0] == STUDY_DIM
    return tuple(round(float(value), 6) for value in vector)


def match(
    a: tuple[float, ...] | list[float], b: tuple[float, ...] | list[float]
) -> float:
    """Cosine similarity of two descriptors as a plain float.

    No threshold inside: callers pass in a measured threshold.
    """
    va = np.asarray(a, dtype=np.float64).flatten()
    vb = np.asarray(b, dtype=np.float64).flatten()
    if va.shape != vb.shape:
        raise ValueError(f"descriptor shapes differ: {va.shape} vs {vb.shape}")
    denom = float(np.sqrt(float((va * va).sum())) * np.sqrt(float((vb * vb).sum())))
    if denom <= 0.0:
        return 0.0
    return float((va * vb).sum() / denom)


def describe_frame_boxes(
    frame_bgr: np.ndarray,
    boxes: list[tuple[float, float, float, float] | list[float]],
) -> tuple[list[tuple[float, ...]], list[int]]:
    """Describe each normalized [x1, y1, x2, y2] box of a BGR frame.

    Boxes use image-normalised coordinates (same convention as
    person_regions summaries). Boxes are clipped to the frame; a box
    that is degenerate after clipping is skipped, and its index is
    reported in the returned `skipped` list. Absence is reported, never
    synthesized into a default descriptor.
    """
    frame = _as_crop(frame_bgr)
    frame_h, frame_w = frame.shape[0], frame.shape[1]
    descriptors: list[tuple[float, ...]] = []
    skipped: list[int] = []
    for index, box in enumerate(boxes):
        try:
            x1, y1, x2, y2 = (float(v) for v in box)
        except (TypeError, ValueError):
            skipped.append(index)
            continue
        x1c = min(max(x1, 0.0), 1.0)
        y1c = min(max(y1, 0.0), 1.0)
        x2c = min(max(x2, 0.0), 1.0)
        y2c = min(max(y2, 0.0), 1.0)
        px1 = round(x1c * frame_w)
        py1 = round(y1c * frame_h)
        px2 = round(x2c * frame_w)
        py2 = round(y2c * frame_h)
        if px2 <= px1 or py2 <= py1:
            skipped.append(index)
            continue
        try:
            descriptors.append(describe_person_crop(frame[py1:py2, px1:px2]))
        except ValueError:
            skipped.append(index)
    return descriptors, skipped
