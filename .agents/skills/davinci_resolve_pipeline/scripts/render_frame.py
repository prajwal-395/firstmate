#!/usr/bin/env python3
"""Render a single frame from the timeline to a PNG file.

Usage:
    python render_frame.py <frame_number> [output_dir]

    # Or import:
    from render_frame import render_frame_to_png
    path = render_frame_to_png(resolve, project, frame_num=45, output_dir="/tmp/screenshots")
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

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

    # Switch to Deliver page
    resolve.OpenPage("deliver")
    time.sleep(0.3)

    project.SetRenderSettings({
        "TargetDir": output_dir,
        "CustomName": custom_name,
        "FormatWidth": width,
        "FormatHeight": height,
        "MarkIn": frame_num,
        "MarkOut": frame_num,
    })

    pid = project.AddRenderJob()
    if not pid:
        print(f"  ✗ Failed to add render job for frame {frame_num}")
        resolve.OpenPage("edit")
        return None

    project.StartRendering()
    for _ in range(30):
        time.sleep(0.5)
        if not project.IsRenderingInProgress():
            break

    project.DeleteAllRenderJobs()

    # Convert .mov to .png
    mov_path = os.path.join(output_dir, f"{custom_name}.mov")
    png_path = os.path.join(output_dir, f"{custom_name}.png")

    if not os.path.exists(mov_path):
        print(f"  ✗ No output file for frame {frame_num}")
        resolve.OpenPage("edit")
        return None

    file_size = os.path.getsize(mov_path)
    if file_size < 2000:
        print(f"  ✗ Frame {frame_num}: {file_size} bytes (BLACK/BROKEN)")
        resolve.OpenPage("edit")
        return None

    os.system(f'ffmpeg -y -i "{mov_path}" -frames:v 1 "{png_path}" 2>/dev/null')

    resolve.OpenPage("edit")
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
