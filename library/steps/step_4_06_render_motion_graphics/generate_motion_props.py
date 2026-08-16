#!/usr/bin/env python3
"""
Generate Remotion MotionGraphics input props from pipeline data.

Takes the enhancement_spec, creative_direction, and audio_spine and produces
per-spine-block MotionGraphics prop files for Remotion rendering.
"""

import json
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")))
from library.tools.brand_palette import (
    accent_color as brand_accent_color,
    roles_from_palette,
)

# The cyan every video carried until P3.1. It is not any shipped
# template's colour and it is NO LONGER A FALLBACK - it exists only so
# tests can assert it never reaches a frame again. The captain's ruling of
# 2026-08-16: the accents belong to whichever templates want them, and
# when a template wants them the colour comes from its own palette, never
# from a constant in this file.
WITHDRAWN_LEGACY_ACCENT_COLOR = "#00D4FF"

# What the upper third's text uses when a template supplies no colour at
# all. Not an accent - just legible.
NEUTRAL_TEXT_COLOR = "#FFFFFF"


class MissingAccentColor(ValueError):
    """A template asked for accents but supplies no colour to draw them in.

    Raised rather than defaulted. Drawing 6px corner brackets in a
    hardcoded cyan is exactly what P3.1 removed, and drawing them in the
    text colour would be a silent substitution of a different design.
    """


def _as_bool(value, default: bool) -> bool:
    """Template flags arrive from YAML, so accept what YAML produces.

    An unrecognised value keeps the default rather than being read as
    falsey - a typo silently switching the house style off is the kind of
    quiet change this repo keeps having to undo.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "yes", "on", "1"):
            return True
        if text in ("false", "no", "off", "0"):
            return False
    return default


def generate_motion_props(
    enhancement_spec: dict,
    creative_direction: dict,
    audio_spine: dict,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    brand_style: dict = None,
    brand_effect: dict = None,
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

    # ── Whether the accents are drawn at all (P3.1 / Q3) ──
    # `show_accents` was hardcoded True, so four glowing L-brackets and a
    # progress bar sat on every frame of every video the pipeline has ever
    # made, and nothing in any config turned them off.
    #
    # The captain's ruling of 2026-08-16: templates are a growing library,
    # so there is no universal default. A template that declares NOTHING
    # gets NOTHING. Declaring is what turns an element on. That is the
    # opposite of the old behaviour, and deliberately so - preserving the
    # old behaviour is the thing that was rejected.
    effect = brand_effect or {}
    accents_enabled = _as_bool(effect.get("motion_accents"), False)
    progress_enabled = _as_bool(effect.get("motion_progress_bar"), False)

    # ── The accent colour (P3.1) ──
    # The template's own palette first, then a per-video creative
    # direction. There is no constant fallback: a template that wants
    # accents must supply a colour to draw them in.
    declared_accent = visual_style.get(
        "accent_color", creative_direction.get("accent_color"))
    accent_color = brand_accent_color(
        (brand_style or {}).get("color_palette"), declared_accent)

    if accents_enabled and not accent_color:
        raise MissingAccentColor(
            "A brand template enabled motion_accents but supplies no usable "
            "accent colour. style.color_palette must contain one that would "
            "read on screen - saturated, and vivid if it is dark; see "
            "library/tools/brand_palette.py - or creative_direction must "
            "name one. Corner brackets are not drawn in a default colour."
        )

    if not accent_color:
        # Nothing is being drawn in an accent colour, but the upper third
        # still needs to be legible. Prefer the palette's own text colour.
        palette_roles = roles_from_palette((brand_style or {}).get("color_palette"))
        accent_color = palette_roles.get("text", NEUTRAL_TEXT_COLOR)
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
        block_idx = 0
        if isinstance(block_position, str):
            if block_position.startswith("body_"):
                try:
                    block_idx = int(block_position.split("_")[1])
                except:
                    pass
        elif isinstance(block_position, int):
            block_idx = block_position

        show_upper_third = (
            block_type == "hook"
            or (block_type == "speech" and block_idx <= 2)
        )

        # B-roll blocks: show accents but not upper third or progress
        show_progress = (block_type != "broll") and progress_enabled
        show_accents = accents_enabled

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
