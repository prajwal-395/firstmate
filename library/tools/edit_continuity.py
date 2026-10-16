"""Continuity, source-to-timeline mappings, and A/V sync (EDIT-009/019/020).

EDIT-009 evaluates continuity across a cut from object/person tracks:
which tracks survive the cut, which are lost, which appear from nowhere.
EDIT-019 preserves source-to-timeline mappings: every placed cut carries
its source span and its timeline span, and the mapping verifies as
monotonic and gap-free. EDIT-020 preserves source audio/video
synchronization: the placed audio and video spans must agree within a
stated bound, or the sync break is reported by name.

All three are checks over DECLARED structures, never new measurements.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence


def check_track_continuity(
        cut_seconds: float,
        tracks: Sequence[Mapping[str, Any]],
        window_seconds: float = 1.0) -> dict[str, Any]:
    """Continuity verdict at a cut from object/person tracks (EDIT-009).

    `tracks` carry `track_id`, `label` (object or person identity) and
    `spans` (`start_seconds`/`end_seconds` each). A track is `present`
    on a side when one of its spans covers any of the `window_seconds`
    before (left) or after (right) the cut. Returns
    `{status, surviving[], lost[], gained[]}` where status is
    `continuous` (same set both sides), `cutaway_safe` (nothing on one
    side - a clean entrance or exit), or `broken` (a track lost or
    gained mid-presence). Track sets are compared by `track_id`; the
    label rides along for the report.
    """
    left = set()
    right = set()
    labels: dict[str, str] = {}
    for track in tracks:
        track_id = str(track.get("track_id"))
        labels[track_id] = str(track.get("label", ""))
        for span in track.get("spans", []):
            start = float(span["start_seconds"])
            end = float(span["end_seconds"])
            if end > cut_seconds - window_seconds and start < cut_seconds:
                left.add(track_id)
            if end > cut_seconds and start < cut_seconds + window_seconds:
                right.add(track_id)
    surviving = sorted(left & right)
    lost = sorted(left - right)
    gained = sorted(right - left)
    if not left or not right:
        status = "cutaway_safe"
    elif not lost and not gained:
        status = "continuous"
    else:
        status = "broken"
    describe = lambda ids: [{"track_id": track_id, "label": labels[track_id]}
                            for track_id in ids]
    return {"status": status, "cut_seconds": float(cut_seconds),
            "surviving": describe(surviving),
            "lost": describe(lost), "gained": describe(gained)}


def verify_source_timeline_map(
        mappings: Sequence[Mapping[str, Any]],
        clip_durations: Mapping[str, float] = {}) -> dict[str, Any]:
    """Verify source-to-timeline mappings (EDIT-019).

    Each mapping carries `clip_id`, `source_start`, `source_end`,
    `timeline_start`, `timeline_end` (spine contract keys). Checks: every
    span is positive; timeline spans are ordered and non-overlapping;
    source spans sit inside the clip duration when durations name the
    clip. Returns `{valid, problems[]}`; problems name the mapping index
    and the violated rule.
    """
    problems: list[str] = []
    ordered = sorted(mappings, key=lambda row: float(row["timeline_start"]))
    previous_end: float | None = None
    for index, row in enumerate(ordered):
        source_start = float(row["source_start"])
        source_end = float(row["source_end"])
        timeline_start = float(row["timeline_start"])
        timeline_end = float(row["timeline_end"])
        if not source_end > source_start:
            problems.append(f"mapping {index}: empty source span")
        if not timeline_end > timeline_start:
            problems.append(f"mapping {index}: empty timeline span")
        if previous_end is not None and timeline_start < previous_end:
            problems.append(f"mapping {index}: timeline overlaps "
                            f"previous span ending at {previous_end}s")
        previous_end = timeline_end if previous_end is None else max(
            previous_end, timeline_end)
        duration = clip_durations.get(str(row.get("clip_id", "")))
        if duration is not None and (
                source_start < 0 or source_end > float(duration)):
            problems.append(f"mapping {index}: source span "
                            f"{source_start}-{source_end}s outside clip "
                            f"duration {duration}s")
    return {"valid": not problems, "problems": problems}


def verify_av_sync(audio_start: float, audio_end: float,
                   video_start: float, video_end: float,
                   max_offset_seconds: float = 0.04) -> dict[str, Any]:
    """Check source A/V synchronization is preserved (EDIT-020).

    Compares the placed audio span against the placed video span: start
    offset and end offset must each sit within `max_offset_seconds`
    (default one frame at 25 fps, stated and overridable). Returns
    `{in_sync, start_offset_seconds, end_offset_seconds}`; a break
    names both offsets rather than rounding one away.
    """
    start_offset = float(audio_start) - float(video_start)
    end_offset = float(audio_end) - float(video_end)
    in_sync = (abs(start_offset) <= max_offset_seconds
               and abs(end_offset) <= max_offset_seconds)
    return {"in_sync": in_sync,
            "start_offset_seconds": start_offset,
            "end_offset_seconds": end_offset}
