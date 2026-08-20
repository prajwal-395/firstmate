"""Render the timed text segments a brand template declares.

``library/tools/timed_text_overlay.py`` says what a declaration means and
groups its moments into non-overlapping segments; this turns each segment
into a ProRes 4444 file with an alpha channel, which is what
``resolve_build_timeline`` can place on V6 over the finished picture.

Called by ``render_motion_graphics`` (4.06), beside the bookend render,
because both are "make the files the manifest is about to reference" and
both need the Remotion project already prepped.

A failed render RAISES. The two gates that would otherwise notice are the
coverage assertion in ``compile_manifest`` and the black-frame probe in
6.02 - and neither notices this one at all, because a missing overlay
leaves the picture underneath intact. So a template that declared an
episode number would simply ship without it, which is the exact defect
this module was written to end.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

from library.tools.timed_text_overlay import (
    TIMED_TEXT_COMPOSITION,
    plan_timed_text_segments,
)

# Where the rendered segments go, under the project's pipeline_output.
TIMED_TEXT_RENDER_DIRNAME = "timed_text_segments"

# A timed text segment is a few seconds of type on transparency. If it has
# not rendered in this long, something is wrong that waiting will not fix.
RENDER_TIMEOUT_SECONDS = 300


class TimedTextRenderError(RuntimeError):
    """A declared timed text segment could not be turned into a file."""


def render_timed_text_segments(
    template_effect: dict,
    remotion_dir: str,
    output_dir: str,
    fps: int = 30,
    width: int = 1080,
    height: int = 1920,
    spine_structure: list | None = None,
    stream=sys.stderr,
) -> list[dict]:
    """Render every segment the declaration implies; ``[]`` if undeclared.

    Each returned entry carries what the renderer needs to place the file:
    ``overlay_path``, ``timeline_start``, ``timeline_end`` and
    ``total_frames``, the same shape ``motion_graphics_overlay.segments``
    uses.
    """
    segments = plan_timed_text_segments(
        template_effect, fps=fps, width=width, height=height,
        spine_structure=spine_structure,
    )
    if not segments:
        return []

    os.makedirs(output_dir, exist_ok=True)
    print(f"  Timed text: {len(segments)} segment(s) declared",
          file=stream)

    rendered = []
    for segment in segments:
        name = f"timed_text_{segment['index']:03d}"
        out_path = os.path.join(output_dir, f"{name}.mov")
        props_path = os.path.join(output_dir, f"{name}_props.json")
        with open(props_path, "w", encoding="utf-8") as f:
            json.dump(segment["props"], f, indent=2, sort_keys=True)

        print(f"    [{segment['index']}] {name}: "
              f"{segment['moment_count']} moment(s), "
              f"{segment['total_frames']}f, "
              f"tl:{segment['timeline_start']:.2f}-"
              f"{segment['timeline_end']:.2f}s", file=stream)

        _render_one(name, out_path, props_path, remotion_dir)

        rendered.append({
            "overlay_path": out_path,
            "props_path": props_path,
            "timeline_start": segment["timeline_start"],
            "timeline_end": segment["timeline_end"],
            "total_frames": segment["total_frames"],
            "moment_count": segment["moment_count"],
            "bytes": os.path.getsize(out_path),
        })
        print(f"      OK: {out_path} ({rendered[-1]['bytes']} bytes)",
              file=stream)

    return rendered


def _render_one(name: str, out_path: str, props_path: str,
                remotion_dir: str) -> None:
    """One `npx remotion render` of TimedTextOverlay, judged by its result."""
    command = [
        "npx", "remotion", "render",
        TIMED_TEXT_COMPOSITION, out_path,
        "--props", props_path,
        "--codec", "prores",
        "--prores-profile", "4444",
        "--image-format", "png",
        # Without this the composition renders on black and the segment
        # hides the picture it is supposed to sit over.
        "--transparent",
    ]
    try:
        result = subprocess.run(
            command, cwd=remotion_dir, capture_output=True, check=False,
            # The pipeline writes UTF-8 status glyphs; the locale codec
            # would fail a render on a check mark in a child's stderr.
            text=True, encoding="utf-8", errors="replace",
            timeout=RENDER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimedTextRenderError(
            f"timed text segment '{name}' render timed out after "
            f"{RENDER_TIMEOUT_SECONDS}s") from exc

    if result.returncode != 0:
        raise TimedTextRenderError(
            f"timed text segment '{name}' render failed: "
            f"{result.stderr[-500:]}")
    if not os.path.exists(out_path):
        raise TimedTextRenderError(
            f"timed text segment '{name}' reported success but wrote no "
            f"file at {out_path}")
