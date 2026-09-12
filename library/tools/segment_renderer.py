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
from library.tools.resolve_lock import assert_current_timeline, under_lease
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


class RenderSettingsError(RuntimeError):
    """A render left project-global state dirty and the cleanup failed.

    Render settings and the render queue are PROJECT-global in Resolve
    (H4 from the statefulness hazards investigation).  A render that
    changes them and does not clean up means the NEXT render - including
    the captain's own manual render on the Deliver page - inherits
    whatever this process left behind: a temp TargetDir, a QA custom
    name, 720p resolution, ExportAudio False.

    The Resolve scripting API has no GetRenderSettings(), so a full
    save-and-restore of the settings dict is impossible.  What IS
    restorable: format/codec (via GetCurrentRenderFormatAndCodec /
    SetCurrentRenderFormatAndCodec) and the render job itself (via
    DeleteRenderJob with the job ID this process created).  The guard
    is: borrow and restore on EVERY exit path including exceptions.
    """


@under_lease("render segments")
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

    # Save the format/codec so we can restore it.  This is the ONLY
    # render state Resolve lets us read back; the rest of
    # SetRenderSettings is write-only.
    saved_format_codec = project.GetCurrentRenderFormatAndCodec() or {}

    # Track the job id we create so we can delete ONLY our own job in
    # the finally block - never DeleteAllRenderJobs, which destroys
    # the captain's queued jobs and any concurrent process's jobs.
    our_job_id = None

    try:
        resolve.OpenPage("deliver")

        settings = {
            "TargetDir": output_dir,
            "CustomName": name,
            "FormatWidth": width,
            "FormatHeight": height,
            # WITHOUT `SelectAllFrames: False` Resolve IGNORES the
            # range and queues the whole timeline (§5: judge the call
            # by what it returns, and here even the return lies - both
            # SetRenderSettings and AddRenderJob succeed).  Measured
            # 2026-09-11 on `Podcast (field test)` / Reel 09: a request
            # for frame 300 alone queued MarkIn 0 / MarkOut 1665 and
            # rendered 1,471 TIFFs of the entire reel before it was
            # stopped by hand.  On the captain's machine - the fleet's
            # one hard CPU limiter - a "single frame" that renders a
            # whole timeline is not a slow check, it is an outage.
            "SelectAllFrames": False,
            "MarkIn": mark_in,
            "MarkOut": mark_out,
            "ExportVideo": True,
            "ExportAudio": False,  # visual QA doesn't need audio
        }

        project.SetRenderSettings(settings)
        assert_current_timeline(project, timeline)
        our_job_id = project.AddRenderJob()
        # And then what Resolve actually QUEUED.  A range that did not
        # take is caught HERE, before a frame is rendered, because the
        # cost of finding out afterwards is the whole timeline.  The
        # job is deleted by the `finally` below either way.
        queued = next((job for job in (project.GetRenderJobList() or [])
                       if job.get("JobId") == our_job_id), None)
        if queued is None:
            return SegmentRenderResult(
                path="", mark_in=mark_in, mark_out=mark_out,
                duration_frames=mark_out - mark_in + 1,
                width=width, height=height, success=False,
                error=(f"AddRenderJob returned {our_job_id!r} but no such "
                       f"job is in the render queue - nothing was started"),
            )
        got_in, got_out = queued.get("MarkIn"), queued.get("MarkOut")
        if (got_in, got_out) != (mark_in, mark_out):
            return SegmentRenderResult(
                path="", mark_in=mark_in, mark_out=mark_out,
                duration_frames=mark_out - mark_in + 1,
                width=width, height=height, success=False,
                error=(f"Resolve queued MarkIn={got_in} MarkOut={got_out} "
                       f"for a request of {mark_in}-{mark_out} - refusing "
                       f"to start a render of a range nobody asked for"),
            )
        project.StartRendering([our_job_id], isInteractiveMode=False)

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
        # Restore format/codec - the only render state with a read-back
        # API.  Do this BEFORE deleting the job so any error here does
        # not skip the job cleanup.
        if saved_format_codec:
            try:
                project.SetCurrentRenderFormatAndCodec(
                    saved_format_codec.get("format", ""),
                    saved_format_codec.get("codec", ""),
                )
            except Exception:
                pass  # best-effort; the job cleanup below is more important

        # Delete ONLY the job this process created.
        if our_job_id:
            try:
                project.DeleteRenderJob(our_job_id)
            except Exception:
                pass  # best-effort

        if prev_page and prev_page != "deliver":
            resolve.OpenPage(prev_page)


@under_lease("render segments")
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
