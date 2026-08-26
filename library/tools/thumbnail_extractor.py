"""
thumbnail_extractor.py - Extract representative frames from video clips.

Uses ffmpeg to extract thumbnails at key timestamps for the review dashboard.
Thumbnails are stored in pipeline_output/thumbnails/ as JPEGs.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import List, Optional

from library.tools.project_layout import Area, ProjectLayout


def _thumbnails_dir(project_dir: str) -> Path:
    """Get the thumbnails directory for a project."""
    return ProjectLayout(project_dir).write_dir(Area.THUMBNAILS)


def extract_thumbnail(
    video_path: str,
    output_path: str,
    timestamp_s: float = 1.0,
    width: int = 320,
) -> bool:
    """Extract a single thumbnail frame from a video file.

    Returns True if successful.
    """
    try:
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(timestamp_s),
            "-i", video_path,
            "-frames:v", "1",
            "-vf", f"scale={width}:-1",
            "-q:v", "3",
            output_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        return result.returncode == 0 and os.path.exists(output_path)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def extract_clip_thumbnail(
    project_dir: str,
    clip_id: str,
    video_path: str,
    timestamp_s: float = 1.0,
    width: int = 320,
) -> str:
    """Extract a thumbnail for a clip and store it in the thumbnails directory.

    Returns the relative URL path for the dashboard (e.g., /thumbnails/IMG_1234.jpg).
    Returns empty string on failure.
    """
    thumbs = _thumbnails_dir(project_dir)
    # Sanitize clip_id for filename
    safe_id = clip_id.replace("/", "_").replace(" ", "_")
    output_path = thumbs / f"{safe_id}.jpg"

    if output_path.exists():
        return f"/thumbnails/{safe_id}.jpg"

    success = extract_thumbnail(video_path, str(output_path), timestamp_s, width)
    if success:
        return f"/thumbnails/{safe_id}.jpg"
    return ""


def extract_thumbnails_for_catalog(
    project_dir: str,
    clip_catalog: list,
) -> dict:
    """Extract thumbnails for all clips in the catalog.

    Returns a dict mapping clip_id -> thumbnail URL path.
    """
    results = {}
    for clip in clip_catalog:
        clip_id = clip.get("clip_id", clip.get("filename", ""))
        filepath = clip.get("filepath", clip.get("path", ""))

        if not filepath or not clip_id:
            continue

        # If filepath is relative, resolve against project dir
        if not os.path.isabs(filepath):
            filepath = str(
                ProjectLayout(project_dir).resolve_project_relative(filepath))

        if not os.path.exists(filepath):
            # Try raw/ subdirectory
            alt_path = str(ProjectLayout(project_dir).read_path(
                Area.RAW, os.path.basename(filepath)))
            if os.path.exists(alt_path):
                filepath = alt_path
            else:
                continue

        # Extract at 1 second (or 10% of duration if available)
        duration = clip.get("duration_s", clip.get("duration", 0))
        ts = min(1.0, duration * 0.1) if duration else 1.0

        url = extract_clip_thumbnail(project_dir, clip_id, filepath, ts)
        if url:
            results[clip_id] = url

    return results


def extract_timestamp_thumbnail(
    project_dir: str,
    clip_id: str,
    video_path: str,
    timestamp_s: float,
    label: str = "",
) -> str:
    """Extract a thumbnail at a specific timestamp (for B-roll candidates, etc).

    Returns the relative URL path.
    """
    thumbs = _thumbnails_dir(project_dir)
    safe_id = clip_id.replace("/", "_").replace(" ", "_")
    ts_label = f"{timestamp_s:.1f}".replace(".", "_")
    suffix = f"_{label}" if label else ""
    filename = f"{safe_id}_t{ts_label}{suffix}.jpg"
    output_path = thumbs / filename

    if output_path.exists():
        return f"/thumbnails/{filename}"

    success = extract_thumbnail(video_path, str(output_path), timestamp_s)
    if success:
        return f"/thumbnails/{filename}"
    return ""


def get_thumbnail_url(project_dir: str, clip_id: str) -> str:
    """Get the thumbnail URL for a clip if it exists."""
    thumbs = _thumbnails_dir(project_dir)
    safe_id = clip_id.replace("/", "_").replace(" ", "_")
    output_path = thumbs / f"{safe_id}.jpg"
    if output_path.exists():
        return f"/thumbnails/{safe_id}.jpg"
    return ""
