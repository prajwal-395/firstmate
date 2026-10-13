#!/usr/bin/env python3
"""Render a single frame from the timeline to a PNG file.

Usage:
    python render_frame.py <frame_number> [output_dir]

    # Or import:
    from render_frame import render_frame_to_png
    path = render_frame_to_png(resolve, project, frame_num=45, output_dir="/tmp/screenshots")
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from library.tools.resolve_api_capabilities import callable_method
from library.tools.resolve_lock import under_lease

# Add parent for connect_resolve
sys.path.insert(0, os.path.dirname(__file__))


@under_lease("render a frame from the Resolve timeline")
def render_frame_to_png(resolve, project, frame_num,
                        output_dir="/tmp/resolve_screenshots",
                        width=1080, height=1920, name_prefix="frame"):
    """Render a single frame from the current timeline to a PNG.

    Args:
        resolve: Resolve API handle
        project: Current project handle
        frame_num: Timeline frame number to render
        output_dir: Where to save the output
        width/height: Output resolution
        name_prefix: Prefix for the output filename

    Returns:
        str: Path to the PNG file, or None if render failed.
    """
    os.makedirs(output_dir, exist_ok=True)
    custom_name = f"{name_prefix}_{frame_num}"
    set_settings = callable_method(project, "SetRenderSettings")
    add_job = callable_method(project, "AddRenderJob")
    start_rendering = callable_method(project, "StartRendering")
    stop_rendering = callable_method(project, "StopRendering")
    get_job_status = callable_method(project, "GetRenderJobStatus")
    delete_job = callable_method(project, "DeleteRenderJob")
    if any(method is None for method in (
            set_settings, add_job, start_rendering, stop_rendering,
            get_job_status, delete_job)):
        raise RuntimeError("Resolve does not expose the required render API")

    if not set_settings({
        "TargetDir": output_dir,
        "CustomName": custom_name,
        "FormatWidth": width,
        "FormatHeight": height,
        "SelectAllFrames": False,
        "MarkIn": frame_num,
        "MarkOut": frame_num,
        "ExportVideo": True,
        "ExportAudio": False,
    }):
        raise RuntimeError("Resolve rejected one-frame render settings")

    pid = add_job()
    if not pid:
        raise RuntimeError(f"Failed to add render job for frame {frame_num}")
    try:
        if not start_rendering([pid], isInteractiveMode=False):
            raise RuntimeError(f"Failed to start render job {pid}")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            status = get_job_status(pid) or {}
            job_status = status.get("JobStatus")
            if job_status == "Complete":
                break
            if job_status in {"Failed", "Cancelled", "Background Render Cancelled",
                              "Remote Render Cancelled"}:
                raise RuntimeError(
                    f"Render job {pid} ended as {job_status}: "
                    f"{status.get('Error', 'no error reported')}")
            time.sleep(0.5)
        else:
            stop_rendering()
            raise TimeoutError(f"Render job {pid} did not finish within 60 seconds")
    finally:
        # Never clear another caller's queued render jobs.
        if not delete_job(pid):
            print(f"  ⚠ Resolve did not delete render job {pid}", file=sys.stderr)

    # Convert .mov to .png
    mov_path = os.path.join(output_dir, f"{custom_name}.mov")
    png_path = os.path.join(output_dir, f"{custom_name}.png")

    if not os.path.exists(mov_path):
        print(f"  ✗ No output file for frame {frame_num}")
        return None

    file_size = os.path.getsize(mov_path)
    if file_size < 2000:
        print(f"  ✗ Frame {frame_num}: {file_size} bytes (BLACK/BROKEN)")
        return None

    # Convert .mov to .png as an argv subprocess: paths reach ffmpeg as
    # separate arguments, never interpolated into a shell word, so a
    # hostile output_dir or name_prefix cannot inject shell syntax.
    subprocess.run(
        ["ffmpeg", "-y", "-i", mov_path, "-frames:v", "1", png_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )

    print(f"  ✓ Frame {frame_num}: {file_size} bytes → {png_path}")
    return png_path


if __name__ == "__main__":
    from connect_resolve import connect

    frame = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "/tmp/resolve_screenshots"

    with connect() as (resolve, project, timeline):
        path = render_frame_to_png(resolve, project, frame, out_dir)
        if path:
            print(f"Saved: {path}")
        else:
            print("Render failed")
