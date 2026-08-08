#!/usr/bin/env python3
"""
segment_renderer.py - Render timeline segments for visual QA analysis.

Uses the DaVinci Resolve scripting API to render specific timeline ranges
to temporary files. Supports both single-frame renders (for frame grab QA)
and multi-frame segments (for video analysis via Gemma 4 12B).

Connection setup is the caller's responsibility - this module accepts
resolve/project/timeline objects as parameters.
"""

import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional


# Resolution presets for QA renders (width x height, 9:16 vertical)
QA_RESOLUTIONS = {
    "720p": (720, 1280),
    "540p": (540, 960),
    "360p": (360, 640),
    "full": (1080, 1920),
}

# Minimum file size (bytes) that indicates real content vs black/broken frame
MIN_VALID_FILE_SIZE = 2048


@dataclass
class SegmentRenderResult:
    """Result from rendering a timeline segment."""
    path: str
    mark_in: int
    mark_out: int
    duration_frames: int
    width: int
    height: int
    success: bool
    error: Optional[str] = None


def render_segment(resolve, project, timeline,
                   mark_in: int, mark_out: int,
                   output_dir: str = None,
                   resolution: str = "720p",
                   custom_name: str = None,
                   timeout: int = 120) -> SegmentRenderResult:
    """Render a timeline range to a video file for QA analysis.

    Args:
        resolve: DaVinci Resolve scripting API object.
        project: Current Resolve project.
        timeline: Current timeline.
        mark_in: First frame to render (timeline frame number).
        mark_out: Last frame to render (inclusive).
        output_dir: Directory for output. Created as tmpdir if None.
        resolution: One of QA_RESOLUTIONS keys.
        custom_name: Custom filename (without extension).
        timeout: Max seconds to wait for render.

    Returns:
        SegmentRenderResult with path to rendered file on success.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="vqa_segment_")
    os.makedirs(output_dir, exist_ok=True)

    width, height = QA_RESOLUTIONS.get(resolution, QA_RESOLUTIONS["720p"])
    name = custom_name or f"qa_segment_{mark_in}_{mark_out}"

    prev_page = resolve.GetCurrentPage()

    try:
        resolve.OpenPage("deliver")

        # Clear stale render jobs
        project.DeleteAllRenderJobs()

        settings = {
            "TargetDir": output_dir,
            "CustomName": name,
            "FormatWidth": width,
            "FormatHeight": height,
            "MarkIn": mark_in,
            "MarkOut": mark_out,
            "ExportVideo": True,
            "ExportAudio": False,  # visual QA doesn't need audio
        }

        project.SetRenderSettings(settings)
        project.AddRenderJob()
        project.StartRendering()

        # Poll for completion
        elapsed = 0.0
        poll_interval = 0.5
        while project.IsRenderingInProgress():
            time.sleep(poll_interval)
            elapsed += poll_interval
            if elapsed >= timeout:
                project.StopRendering()
                return SegmentRenderResult(
                    path="", mark_in=mark_in, mark_out=mark_out,
                    duration_frames=mark_out - mark_in + 1,
                    width=width, height=height, success=False,
                    error=f"Render timed out after {timeout}s",
                )

        # Locate the rendered file (Resolve appends format extension)
        rendered_path = _find_rendered_file(output_dir, name)
        if not rendered_path:
            return SegmentRenderResult(
                path="", mark_in=mark_in, mark_out=mark_out,
                duration_frames=mark_out - mark_in + 1,
                width=width, height=height, success=False,
                error="Rendered file not found in output directory",
            )

        # Sanity check: file too small means black/broken
        if os.path.getsize(rendered_path) < MIN_VALID_FILE_SIZE:
            return SegmentRenderResult(
                path=rendered_path, mark_in=mark_in, mark_out=mark_out,
                duration_frames=mark_out - mark_in + 1,
                width=width, height=height, success=False,
                error=f"Rendered file too small ({os.path.getsize(rendered_path)} bytes) - likely black/broken",
            )

        return SegmentRenderResult(
            path=rendered_path, mark_in=mark_in, mark_out=mark_out,
            duration_frames=mark_out - mark_in + 1,
            width=width, height=height, success=True,
        )

    finally:
        if prev_page and prev_page != "deliver":
            resolve.OpenPage(prev_page)


def render_single_frame(resolve, project, timeline,
                        frame: int,
                        output_dir: str = None,
                        resolution: str = "full") -> Optional[str]:
    """Render a single timeline frame and convert to PNG.

    Args:
        resolve: DaVinci Resolve scripting API object.
        project: Current Resolve project.
        timeline: Current timeline.
        frame: Timeline frame number to capture.
        output_dir: Directory for output. Created as tmpdir if None.
        resolution: Resolution preset key.

    Returns:
        Absolute path to the PNG file, or None on failure.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="vqa_frame_")

    result = render_segment(
        resolve, project, timeline,
        mark_in=frame, mark_out=frame,
        output_dir=output_dir,
        resolution=resolution,
        custom_name=f"frame_{frame}",
    )

    if not result.success:
        return None

    # Convert MOV/MP4 to PNG via ffmpeg
    png_path = os.path.splitext(result.path)[0] + ".png"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", result.path, "-frames:v", "1", png_path],
            capture_output=True, timeout=15, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    finally:
        # Clean up the video file regardless
        _cleanup(result.path)

    if os.path.exists(png_path) and os.path.getsize(png_path) > 0:
        return png_path
    return None


def cleanup_segment(path: str) -> None:
    """Remove a rendered segment file (idempotent)."""
    _cleanup(path)


def _find_rendered_file(output_dir: str, name_prefix: str) -> Optional[str]:
    """Find the rendered file by prefix, ignoring XML sidecar files."""
    for f in os.listdir(output_dir):
        if f.startswith(name_prefix) and not f.endswith(".xml"):
            return os.path.join(output_dir, f)
    return None


def _cleanup(path: str) -> None:
    """Silently remove a file."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
