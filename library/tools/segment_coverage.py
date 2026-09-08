"""segment_coverage.py - how much of a clip time-bounded segments describe.

`scene[]` describes 374.2 s of project 001's 807.0 s of footage - 46.4%
(issue #302): the vision pass returns one segment for fifteen of seventeen
clips, and four long clips stop at 13.9-18.9 s while one 85.8 s clip was
described whole, so the cause is not a fixed cap and is not established.
What IS established is that nothing downstream could see the gap: the
segments were stored verbatim and rendered as prose with no account of
the range they do not cover, so a planning step chose windows blind to
their picture.

This module is the measurement both halves share. The producer
(`library/tools/analysis/vision_pipeline_v3.py`) normalizes what the
model returned and records the coverage on the profile; the adapter
(`library/tools/vision_schema_adapter.py`) renders the undescribed ranges
into the prose every consumer reads. Both halves read segment bounds and
the clip duration - both measured - and neither invents a location, a
lighting or a feature for a range the vision pass never described.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

# Gaps shorter than this are not reported.
#
# A sliver between two segments is not footage anyone can plan against -
# the same line `MIN_USABLE_RANGE_S` draws in `vision_pipeline_v3` - and
# model timestamps wobble by tenths (a segment ending at 46.0 s on a
# 45.943 s clip is sloppiness, not a gap worth naming).
GAP_REPORT_THRESHOLD_S = 0.5


def _duration_s(duration: Any) -> float:
    """The clip length as a float, or 0.0 when it was never measured."""
    try:
        value = float(duration)
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _bounds(segments: Any) -> List[Tuple[float, float]]:
    """Numeric [start, end) bounds, sorted, empties dropped.

    Non-dicts, non-numeric bounds and inverted ranges are skipped, never
    repaired: a segment without bounds is a malformed observation, and
    guessing its span would invent coverage.
    """
    bounds = []
    if not isinstance(segments, list):
        return bounds
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        try:
            start = float(seg.get("start"))
            end = float(seg.get("end"))
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        bounds.append((start, end))
    return sorted(bounds)


def _union_length(bounds: List[Tuple[float, float]]) -> float:
    """The length of the union, so overlaps are counted once."""
    total = 0.0
    run_start: float | None = None
    run_end = 0.0
    for start, end in bounds:
        if run_start is None or start > run_end:
            if run_start is not None:
                total += run_end - run_start
            run_start, run_end = start, end
        else:
            run_end = max(run_end, end)
    if run_start is not None:
        total += run_end - run_start
    return total


def normalize_segments(segments: Any, duration: Any) -> List[dict]:
    """Segments sorted by start and clamped to [0, duration].

    The model returns whatever timestamps it feels like - unsorted, past
    the clip end, zero-length - and every reader of `scene[]`/`camera[]`
    does its own bound arithmetic against the clip. Normalizing once at
    the producer keeps one sloppy end from becoming fifteen quiet
    misreads. Entries that survive keep every key verbatim; only `start`
    and `end` are touched.

    Without a measured duration there is nothing to clamp against, so
    dict entries pass through in their original order.
    """
    dicts = ([dict(s) for s in segments if isinstance(s, dict)]
             if isinstance(segments, list) else [])
    total = _duration_s(duration)
    if total <= 0:
        return dicts
    normalized = []
    for seg in dicts:
        try:
            start = float(seg.get("start"))
            end = float(seg.get("end"))
        except (TypeError, ValueError):
            continue
        start = max(0.0, start)
        end = min(total, end)
        if end <= start:
            continue
        seg = dict(seg)
        seg["start"] = start
        seg["end"] = end
        normalized.append(seg)
    normalized.sort(key=lambda s: (s["start"], s["end"]))
    return normalized


def coverage_gaps(segments: Any, duration: Any) -> List[List[float]]:
    """Ranges of [0, duration) no segment covers, longest first.

    The complement of the union, so overlaps never manufacture a gap and
    overshoot never manufactures one past the clip end. Slivers under
    `GAP_REPORT_THRESHOLD_S` are dropped. Empty when the duration was
    never measured: a gap against an unknown length is a guess.
    """
    total = _duration_s(duration)
    if total <= 0:
        return []
    clipped = []
    for start, end in _bounds(segments):
        if end <= 0 or start >= total:
            continue
        clipped.append((max(0.0, start), min(total, end)))
    gaps = []
    cursor = 0.0
    for start, end in sorted(clipped):
        if start > cursor:
            gaps.append([round(cursor, 3), round(start, 3)])
        cursor = max(cursor, end)
    if cursor < total:
        gaps.append([round(cursor, 3), round(total, 3)])
    gaps = [g for g in gaps if g[1] - g[0] >= GAP_REPORT_THRESHOLD_S]
    gaps.sort(key=lambda g: (g[0], g[1]))
    return gaps


def coverage_summary(segments: Any, duration: Any) -> Dict[str, Any]:
    """The described share of a clip as numbers, not prose.

    `described_s` is the union length (overlaps counted once), `ratio`
    is described over total, and `undescribed_ranges` is what
    `coverage_gaps` reports. All three derive from measured bounds; no
    range the vision pass skipped gains a location here.
    """
    total = _duration_s(duration)
    if total <= 0:
        return {
            "described_s": 0.0,
            "total_s": 0.0,
            "ratio": 0.0,
            "undescribed_ranges": [],
        }
    described = _union_length([
        (max(0.0, s), min(total, e))
        for s, e in _bounds(segments)
        if e > 0 and s < total
    ])
    described = round(min(described, total), 3)
    return {
        "described_s": described,
        "total_s": round(total, 3),
        "ratio": round(described / total, 4),
        "undescribed_ranges": coverage_gaps(segments, total),
    }
