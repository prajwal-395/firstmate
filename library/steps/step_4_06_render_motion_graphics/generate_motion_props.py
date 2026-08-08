#!/usr/bin/env python3
"""
Generate Remotion MotionGraphics input props from pipeline data.

Takes the enhancement_spec, creative_direction, and audio_spine and produces
per-spine-block MotionGraphics prop files for Remotion rendering.
"""

import json
from typing import Optional


def generate_motion_props(
    enhancement_spec: dict,
    creative_direction: dict,
    audio_spine: dict,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
) -> list[dict]:
    """Generate MotionGraphics props for each spine block.

    Returns a list of prop dicts, one per spine block, each with:
    - title, subtitle, accentColor, show flags
    - timelineProgressStart/End for global progress bar
    - durationInFrames, fps, width, height
    - block_position, timeline_start, timeline_end (metadata for placement)
    """
    structure = audio_spine.get("structure", [])
    if not structure:
        return []

    # Extract style from creative direction
    visual_style = creative_direction.get("visual_style", {})
    accent_color = visual_style.get(
        "accent_color",
        creative_direction.get("accent_color", "#00D4FF"),
    )
    title = creative_direction.get("title", "")
    subtitle = creative_direction.get("subtitle", "")

    # Series / episode label
    series_name = creative_direction.get("series_name", "")
    episode_label = creative_direction.get("episode_label", "")
    if not title and series_name:
        title = series_name
    if not subtitle and episode_label:
        subtitle = episode_label

    # Determine total timeline duration for progress calculation
    total_duration = 0
    for block in structure:
        block_end = block.get("timeline_end", 0)
        if block_end > total_duration:
            total_duration = block_end

    if total_duration == 0:
        return []

    # Which blocks get motion graphics? All speech and hook blocks.
    # B-roll / music-only blocks can optionally get accents.
    mg_blocks = []
    for block in structure:
        block_type = block.get("block_type", "")
        if block_type in ("hook", "speech", "broll"):
            mg_blocks.append(block)

    props_list = []
    for block in mg_blocks:
        block_start = block.get("timeline_start", 0)
        block_end = block.get("timeline_end", 0)
        block_dur = block_end - block_start
        if block_dur <= 0:
            continue

        block_type = block.get("block_type", "")
        block_position = block.get("position", 0)
        total_frames = max(1, round(block_dur * fps))

        # Progress bar range for this block within the full timeline
        progress_start = block_start / total_duration
        progress_end = block_end / total_duration

        # Only show upper third on hook and first speech block
        show_upper_third = block_type == "hook" or (
            block_type == "speech" and block_position <= 2
        )

        # B-roll blocks: show accents but not upper third or progress
        show_progress = block_type != "broll"
        show_accents = True

        props = {
            "title": title if show_upper_third else "",
            "subtitle": subtitle if show_upper_third else "",
            "accentColor": accent_color,
            "showUpperThird": show_upper_third,
            "showProgress": show_progress,
            "showAccents": show_accents,
            "timelineProgressStart": round(progress_start, 4),
            "timelineProgressEnd": round(progress_end, 4),
            "fps": fps,
            "width": width,
            "height": height,
            "durationInFrames": total_frames,
            # Metadata for placement (not consumed by Remotion)
            "_block_position": block_position,
            "_timeline_start": block_start,
            "_timeline_end": block_end,
            "_block_type": block_type,
        }
        props_list.append(props)

    return props_list
