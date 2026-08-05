#!/usr/bin/env python3
"""
Frame Utilities — Shared timing conversion for the video editing pipeline.

This module provides the SINGLE authoritative boundary where floating-point
seconds are converted to integer frames. All steps from Phase 3 onward
should use these utilities to avoid accumulated rounding errors.

Design decisions:
  - seconds_to_frame() uses Python's round() (banker's rounding) for the
    conversion. This is the ONLY place rounding happens.
  - Frame values are plain Python ints — no numpy, no fractions.
  - Source timestamps (source_start, source_end, v1_source_in, etc.) are
    NOT converted here — they represent positions within source media and
    are converted at the XMEML output boundary using the source clip's
    native fps. Only timeline-relative positions are converted.

Usage:
    from tools.frame_utils import seconds_to_frame, frame_to_seconds
    from tools.frame_utils import convert_spine_to_frames
"""


def seconds_to_frame(seconds: float, fps: float) -> int:
    """Convert seconds to nearest frame index.

    This is the single rounding boundary for the entire pipeline.
    All downstream code works with the resulting integer frames,
    eliminating accumulated float errors and off-by-one artifacts.

    Examples:
        >>> seconds_to_frame(1.0, 30)
        30
        >>> seconds_to_frame(0.5, 30)
        15
        >>> seconds_to_frame(1.0, 29.97)
        30
    """
    return round(seconds * fps)


def frame_to_seconds(frame: int, fps: float) -> float:
    """Convert frame index back to seconds.

    Used when interfacing with components that still expect seconds
    (e.g., SRT subtitle files, ffmpeg seek positions).

    Examples:
        >>> frame_to_seconds(30, 30)
        1.0
        >>> frame_to_seconds(15, 30)
        0.5
    """
    return frame / fps


def duration_to_frames(duration_seconds: float, fps: float) -> int:
    """Convert a duration in seconds to frame count.

    For durations, we use round() to get the closest frame count.
    This may differ from (end_frame - start_frame) by ±1 frame
    due to independent rounding of start and end — that's expected
    and correct. The spine builder uses cumulative frame_cursor
    to avoid this.
    """
    return round(duration_seconds * fps)


def convert_spine_to_frames(spine: dict, fps: float = None) -> dict:
    """Add frame-based timeline fields to all blocks in a spine structure.

    Converts timeline_start/end and duration_seconds to integer frame
    equivalents, using a cumulative frame cursor to ensure blocks are
    contiguous (no gaps, no overlaps) — matching Palmier Pro's
    frame-accumulation pattern.

    Source timestamps (source_start, source_end, v1_source_in, etc.)
    are intentionally LEFT as seconds because they represent positions
    within source media, not the output timeline.

    New fields added to each block:
      - timeline_start_frame: int
      - timeline_end_frame: int
      - duration_frames: int

    The original seconds fields are preserved for backward compatibility.

    Args:
        spine: dict with "structure" list and optional "frame_rate"
        fps: override frame rate (uses spine["frame_rate"] if None)

    Returns:
        The same dict, mutated in place with _frame fields added.
    """
    if fps is None:
        fps = spine.get("frame_rate", 30.0)

    structure = spine.get("structure", [])
    if not structure:
        return spine

    # Cumulative frame cursor ensures contiguous blocks.
    # This is critical: for runs of adjacent blocks, we accumulate to avoid
    # 1-frame gaps from independent rounding. But if a block's timeline_start
    # deviates significantly from the cursor (e.g. lead-in silence, explicit
    # gap), we reset to the authoritative timeline_start value.
    frame_cursor = 0
    for block in structure:
        dur_seconds = block.get("duration_seconds", 0)
        dur_frames = seconds_to_frame(dur_seconds, fps)

        # Check if the block's declared timeline_start diverges from cursor.
        # This catches non-zero start offsets and explicit gaps.
        declared_start = block.get("timeline_start")
        if declared_start is not None:
            declared_start_frame = seconds_to_frame(declared_start, fps)
            # Fix H4: Threshold changed from > 0 to > 1.  A 1-frame
            # discrepancy is normal rounding noise from independent
            # seconds_to_frame() calls; resetting the cursor on it defeats
            # the purpose of the accumulator.  Only genuine gaps (2+ frames)
            # warrant a cursor reset.
            if abs(declared_start_frame - frame_cursor) > 1:
                frame_cursor = declared_start_frame

        block["timeline_start_frame"] = frame_cursor
        block["timeline_end_frame"] = frame_cursor + dur_frames
        block["duration_frames"] = dur_frames

        frame_cursor += dur_frames

    # Update spine-level metadata
    spine["total_duration_frames"] = frame_cursor
    spine["frame_rate"] = fps

    return spine


def convert_clip_to_frames(clip: dict, fps: float) -> dict:
    """Add frame fields to a clip dict (used by steps 3.x, 5.x).

    Adds:
      - timeline_in_frame
      - timeline_out_frame

    Source positions (source_in, source_out, video_in, video_out) are
    NOT converted — they use the source clip's native fps, which may
    differ from the project fps.
    """
    clip["timeline_in_frame"] = seconds_to_frame(
        clip.get("timeline_in", clip.get("timeline_start", 0)), fps)
    clip["timeline_out_frame"] = seconds_to_frame(
        clip.get("timeline_out", clip.get("timeline_end", 0)), fps)
    return clip


def convert_subtitle_to_frames(sub: dict, fps: float) -> dict:
    """Add frame fields to a subtitle entry dict."""
    sub["timeline_start_frame"] = seconds_to_frame(
        sub.get("timeline_start", 0), fps)
    sub["timeline_end_frame"] = seconds_to_frame(
        sub.get("timeline_end", 0), fps)
    return sub
