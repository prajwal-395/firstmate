"""Pacing windows: the plan's numeric or feel target and what was delivered.

Rung 7 (K1, PA3.2/PA2.2): a numeric average shot length rides the spine
as `pacing` windows with `asl_seconds`; a feel request such as "faster
cuts as it builds" carries `feel` in the same addressed window. Step
2.05's post-bridge validates either shape. This module measures the
BUILT picture beside them: the visible cuts in each
window (every V1 and V2 clip edge - a b-roll covering speech IS the
shot the viewer sees) and the delivered average shot length.

Report-only, by design: a target is a target, and failing a build on
a missed one would be a gate failing correct output (AGENTS.md 10.4).
`compile_manifest` records the rows as `pacing_report` and prints
them; no verdict, no threshold - the delta is the finding, and the
reader (the run log, the reviewer) judges it.

Dead sections
-------------
A dead section is a stretch of the timeline where no word is sounding,
the picture is static, and there is no audio activity. The captain's
example - "a long dead section at 2:31 in a rough cut" - is usually a
long pause INSIDE speech, not a whole block with no words. This module
finds those stretches by measuring the gaps between words on the
timeline and across block joins, then checking whether the picture and
audio are active during each gap.

Thresholds are derived from the sequence's own measured distribution:
the motion threshold is the median motion energy across all clips, and
the minimum duration is the 90th percentile of pause lengths. Both are
recorded on each dead section row with their source.
"""

from __future__ import annotations

import os
import statistics

from library.tools.frame_utils import seconds_to_frame


def _clip_edge_frames(clip: dict, fps: float) -> tuple:
    """(in_frame, out_frame) for a compiled picture clip."""
    try:
        start = int(clip["timeline_in_frame"])
    except (KeyError, TypeError, ValueError):
        start = seconds_to_frame(float(clip.get("timeline_in", 0.0)), fps)
    try:
        end = int(clip["timeline_out_frame"])
    except (KeyError, TypeError, ValueError):
        end = seconds_to_frame(float(clip.get("timeline_out", 0.0)), fps)
    return start, end


def measure_pacing(spine_blocks: list, v1_clips: list, v2_clips: list,
                   pacing: list, fps: float) -> list:
    """One row per planned window: target ASL beside delivered ASL.

    The window spans its start block's start to its end block's end.
    Every picture edge strictly inside the window is a cut; the window
    holds cuts + 1 shots, so delivered ASL is the span over that
    count. Windows naming blocks the spine does not carry are skipped
    with the reason on the row - measured, never refused, because the
    picture is already built and the report is what says the target
    missed its address.
    """
    by_position = {}
    for block in spine_blocks or []:
        if isinstance(block, dict):
            by_position.setdefault(str(block.get("position")), block)
    edges = []
    for clip in list(v1_clips or []) + list(v2_clips or []):
        if not isinstance(clip, dict):
            continue
        start, end = _clip_edge_frames(clip, fps)
        if end > start:
            edges.append((start, end))

    report = []
    for window in pacing or []:
        start_block = by_position.get(str(window.get("start_block")))
        end_block = by_position.get(str(window.get("end_block")))
        target = window.get("asl_seconds")
        if start_block is None or end_block is None:
            report.append({
                "start_block": window.get("start_block"),
                "end_block": window.get("end_block"),
                "target_asl_seconds": target,
                "target_feel": window.get("feel"),
                "delivered_asl_seconds": None,
                "cuts": None,
                "window_seconds": None,
                "note": "window names a block the built spine does "
                        "not carry - nothing measured",
            })
            continue
        try:
            win_start = int(start_block["timeline_start_frame"])
        except (KeyError, TypeError, ValueError):
            win_start = seconds_to_frame(
                float(start_block.get("timeline_start", 0.0)), fps)
        try:
            win_end = int(end_block["timeline_end_frame"])
        except (KeyError, TypeError, ValueError):
            win_end = seconds_to_frame(
                float(end_block.get("timeline_end", 0.0)), fps)
        # One cut per frame: abutting clips share the edge frame,
        # and counting both sides would double every straight cut.
        interior = {edge for start, end in edges
                    for edge in (start, end)
                    if win_start < edge < win_end}
        cuts = len(interior)
        # Each cut inside the window opens one more shot: the window
        # holds cuts + 1 of them.
        span_seconds = max(0.0, (win_end - win_start) / float(fps))
        delivered = (span_seconds / (cuts + 1)) if span_seconds else 0.0
        report.append({
            "start_block": window.get("start_block"),
            "end_block": window.get("end_block"),
            "target_asl_seconds": target,
            "target_feel": window.get("feel"),
            "delivered_asl_seconds": round(delivered, 3),
            "cuts": cuts,
            "window_seconds": round(span_seconds, 3),
        })
    return report


def _source_to_timeline(source_seconds: float, block: dict) -> float:
    """Map a source-domain second to the timeline domain for this block."""
    src_start = float(block.get("source_start", 0.0))
    tl_start = float(block.get("timeline_start", 0.0))
    return tl_start + (source_seconds - src_start)


def _timeline_to_source(timeline_seconds: float, block: dict) -> float:
    """Map a timeline-domain second to the source domain for this block."""
    src_start = float(block.get("source_start", 0.0))
    tl_start = float(block.get("timeline_start", 0.0))
    return src_start + (timeline_seconds - tl_start)


def _block_speech_spans_timeline(block: dict) -> list:
    """The block's word spans in TIMELINE seconds, as (start, end) pairs."""
    spans = []
    for entry in block.get("word_timestamps") or []:
        if not isinstance(entry, dict):
            continue
        try:
            start = float(entry["source_start"])
            end = float(entry["source_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if end > start:
            spans.append((_source_to_timeline(start, block),
                          _source_to_timeline(end, block)))
    return spans


def _all_speech_spans_timeline(spine_blocks: list) -> list:
    """All speech spans on the timeline, sorted by start time."""
    spans = []
    for block in spine_blocks or []:
        if not isinstance(block, dict):
            continue
        spans.extend(_block_speech_spans_timeline(block))
    return sorted(spans, key=lambda s: s[0])


def _clip_motion_at(temporal_index: dict, source_start: float,
                    source_end: float) -> dict:
    """Measure motion activity in a source-domain span."""
    motion = temporal_index.get("motion_energy") or {}
    motion_values = motion.get("values") or []
    motion_sr = float(motion.get("sample_rate_hz", 0))

    flow = temporal_index.get("optical_flow_direction") or {}
    flow_values = [
        v.get("magnitude") for v in (flow.get("values") or [])
        if isinstance(v, dict)
    ]
    flow_sr = float(flow.get("sample_rate_hz", 0))

    result = {
        "motion_mean": None,
        "motion_peak": None,
        "camera_motion_mean": None,
        "has_scene_boundary": False,
    }

    if motion_values and motion_sr > 0:
        i_start = max(0, int(source_start * motion_sr))
        i_end = min(len(motion_values), int(source_end * motion_sr) + 1)
        segment = [v for v in motion_values[i_start:i_end]
                   if isinstance(v, (int, float))]
        if segment:
            result["motion_mean"] = round(
                sum(segment) / len(segment), 4)
            result["motion_peak"] = round(max(segment), 4)

    if flow_values and flow_sr > 0:
        i_start = max(0, int(source_start * flow_sr))
        i_end = min(len(flow_values), int(source_end * flow_sr) + 1)
        segment = [v for v in flow_values[i_start:i_end]
                   if isinstance(v, (int, float))]
        if segment:
            result["camera_motion_mean"] = round(
                sum(segment) / len(segment), 4)

    for boundary in temporal_index.get("scene_boundaries") or []:
        t = boundary.get("time") if isinstance(boundary, dict) else None
        if isinstance(t, (int, float)):
            if source_start <= float(t) <= source_end:
                result["has_scene_boundary"] = True
                break

    return result


def _clip_audio_activity_at(temporal_index: dict, source_start: float,
                             source_end: float) -> dict:
    """Measure audio activity in a source-domain span."""
    result = {"has_speech_region": False, "has_audio_event": False}

    for region in temporal_index.get("speech_regions") or []:
        if not isinstance(region, dict):
            continue
        try:
            r_start = float(region["start"])
            r_end = float(region["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if r_start < source_end and r_end > source_start:
            result["has_speech_region"] = True
            break

    for event in temporal_index.get("audio_events") or []:
        if not isinstance(event, dict):
            continue
        t = event.get("time")
        if isinstance(t, (int, float)):
            if source_start <= float(t) <= source_end:
                result["has_audio_event"] = True
                break

    return result


def _collect_motion_values(temporal_indices: dict) -> list:
    """All motion energy values across all clips, for threshold derivation."""
    values = []
    for idx in temporal_indices.values():
        if not isinstance(idx, dict):
            continue
        motion = idx.get("motion_energy") or {}
        for v in motion.get("values") or []:
            if isinstance(v, (int, float)):
                values.append(float(v))
    return values


def _derive_motion_threshold(motion_values: list) -> float:
    """Derive the motion threshold from the sequence's own distribution.

    Uses the median motion energy across all clips. A stretch with motion
    below this is static relative to the sequence's own baseline.
    """
    if not motion_values:
        return 0.0
    return round(statistics.median(motion_values), 4)


def _derive_min_duration(pause_lengths: list) -> float:
    """Derive the minimum dead section duration from the sequence's own pauses.

    Uses the 90th percentile of pause lengths. A gap shorter than this
    is a normal pause, not a dead section.
    """
    if not pause_lengths:
        return 0.0
    sorted_pauses = sorted(pause_lengths)
    idx = int(len(sorted_pauses) * 0.9) - 1
    idx = max(0, min(idx, len(sorted_pauses) - 1))
    return round(sorted_pauses[idx], 3)


def _motion_is_active(mean, threshold: float) -> bool:
    """Whether a measured motion value counts as visual activity.

    At or above the sequence's own median is active. The degenerate case is
    a sequence whose median is 0: there, only motion above 0 is active, or
    a fully static sequence would count itself as active and nothing would
    ever be dead.
    """
    if mean is None:
        return False
    if threshold <= 0.0:
        return mean > 0.0
    return mean >= threshold


def _merge_gaps(gaps: list) -> list:
    """Merge overlapping or adjacent gaps on the timeline.

    A pause spanning a block join can be reported twice: once as the gap
    between the last word of block N and the first word of block N+1, and
    once as the block-edge gap (last word -> block end, block start -> first
    word). This merges overlapping intervals so each pause is checked once.
    """
    if not gaps:
        return []
    sorted_gaps = sorted(gaps, key=lambda g: g[0])
    merged = [list(sorted_gaps[0])]
    for start, end in sorted_gaps[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _temporal_index_has_measurements(temporal_index: dict) -> bool:
    """Whether the temporal index carries any motion or audio measurements.

    An empty index (or one with no motion/speech fields) is unmeasured,
    not measured-as-silent. A gap in an unmeasured clip is skipped, never
    reported dead.
    """
    if not temporal_index:
        return False
    motion = temporal_index.get("motion_energy") or {}
    if motion.get("values"):
        return True
    flow = temporal_index.get("optical_flow_direction") or {}
    if flow.get("values"):
        return True
    if temporal_index.get("scene_boundaries"):
        return True
    if temporal_index.get("speech_regions"):
        return True
    if temporal_index.get("audio_events"):
        return True
    return False


def find_dead_sections(spine_blocks: list, temporal_indices: dict) -> list:
    """Find dead sections in the assembled sequence.

    A dead section is a stretch of the timeline where no word is sounding,
    the picture is static, and there is no audio activity. The captain's
    example - a long dead section at 2:31 in a rough cut - is usually a
    long pause INSIDE speech, not a whole block with no words.

    This function measures the gaps between words on the timeline and
    across block joins, then checks whether the picture and audio are
    active during each gap. Thresholds are derived from the sequence's
    own measured distribution: the motion threshold is the median motion
    energy across all clips, and the minimum duration is the 90th
    percentile of pause lengths.

    A gap in a clip with no temporal index (or no motion/speech fields) is
    unmeasured and skipped, never reported dead.

    Args:
        spine_blocks: the spine blocks, each with timeline_start/timeline_end,
            clip_id, source_start/source_end, and word_timestamps.
        temporal_indices: a dict mapping clip_id to that clip's temporal
            index (the per-clip measurements).

    Returns a list of dead section records, each with:
        - timeline_start, timeline_end: where on the timeline
        - duration_seconds: how long
        - block_position: the spine block position (or None for block joins)
        - clip_id: the source clip
        - reasons: what is missing
        - motion_mean, motion_peak, camera_motion_mean: the measurements
        - motion_threshold: the derived threshold used
        - min_duration_threshold: the derived minimum duration used
    """
    speech_spans = _all_speech_spans_timeline(spine_blocks)
    motion_values = _collect_motion_values(temporal_indices)
    motion_threshold = _derive_motion_threshold(motion_values)

    # Collect all pause lengths (gaps between speech) for threshold derivation
    pause_lengths = []
    for i in range(len(speech_spans) - 1):
        gap = speech_spans[i + 1][0] - speech_spans[i][1]
        if gap > 0:
            pause_lengths.append(gap)
    # Also include gaps at the start and end of the timeline
    for block in spine_blocks or []:
        if not isinstance(block, dict):
            continue
        try:
            tl_start = float(block["timeline_start"])
            tl_end = float(block["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        block_spans = _block_speech_spans_timeline(block)
        if block_spans:
            pause_lengths.append(block_spans[0][0] - tl_start)
            pause_lengths.append(tl_end - block_spans[-1][1])
    min_duration = _derive_min_duration(pause_lengths)

    # Find all gaps on the timeline
    gaps = []
    for i in range(len(speech_spans) - 1):
        gap_start = speech_spans[i][1]
        gap_end = speech_spans[i + 1][0]
        if gap_end > gap_start:
            gaps.append((gap_start, gap_end))

    # Also check gaps at the start and end of each block
    for block in spine_blocks or []:
        if not isinstance(block, dict):
            continue
        try:
            tl_start = float(block["timeline_start"])
            tl_end = float(block["timeline_end"])
        except (KeyError, TypeError, ValueError):
            continue
        block_spans = _block_speech_spans_timeline(block)
        if block_spans:
            if block_spans[0][0] > tl_start:
                gaps.append((tl_start, block_spans[0][0]))
            if block_spans[-1][1] < tl_end:
                gaps.append((block_spans[-1][1], tl_end))
        else:
            # No speech in this block - the whole block is a gap
            gaps.append((tl_start, tl_end))

    # Merge overlapping gaps so a pause spanning a block join is one gap
    gaps = _merge_gaps(gaps)

    # Check each gap for deadness
    dead = []
    for gap_start, gap_end in gaps:
        duration = gap_end - gap_start
        if duration < min_duration:
            continue

        # Find which block(s) this gap falls in
        gap_blocks = []
        for block in spine_blocks or []:
            if not isinstance(block, dict):
                continue
            try:
                tl_start = float(block["timeline_start"])
                tl_end = float(block["timeline_end"])
            except (KeyError, TypeError, ValueError):
                continue
            if tl_start < gap_end and tl_end > gap_start:
                gap_blocks.append(block)

        if not gap_blocks:
            continue

        # Check each block's source span for this gap
        for block in gap_blocks:
            clip_id = str(block.get("clip_id", ""))
            if not clip_id:
                continue

            temporal_index = temporal_indices.get(clip_id, {})

            # A clip with no temporal index is unmeasured, not dead
            if not _temporal_index_has_measurements(temporal_index):
                continue

            # Map the gap to source domain
            src_start = _timeline_to_source(gap_start, block)
            src_end = _timeline_to_source(gap_end, block)

            motion = _clip_motion_at(temporal_index, src_start, src_end)
            audio = _clip_audio_activity_at(temporal_index, src_start, src_end)

            has_visual_activity = (
                _motion_is_active(motion["motion_mean"], motion_threshold)
                or _motion_is_active(motion["camera_motion_mean"],
                                     motion_threshold)
                or motion["has_scene_boundary"]
            )
            has_audio_activity = (audio["has_speech_region"]
                                  or audio["has_audio_event"])

            if not has_visual_activity and not has_audio_activity:
                dead.append({
                    "timeline_start": round(gap_start, 3),
                    "timeline_end": round(gap_end, 3),
                    "duration_seconds": round(duration, 3),
                    "block_position": block.get("position"),
                    "clip_id": clip_id,
                    "reasons": ["no visual change", "no audio activity"],
                    "motion_mean": motion["motion_mean"],
                    "motion_peak": motion["motion_peak"],
                    "camera_motion_mean": motion["camera_motion_mean"],
                    "motion_threshold": motion_threshold,
                    "min_duration_threshold": min_duration,
                })

    # A merged gap is checked once per overlapping block; when both blocks
    # carry the same clip, that would report the same dead stretch twice.
    # One finding per clip per timeline span.
    seen = set()
    unique_dead = []
    for entry in dead:
        key = (entry["clip_id"], entry["timeline_start"],
               entry["timeline_end"])
        if key in seen:
            continue
        seen.add(key)
        unique_dead.append(entry)
    return unique_dead
