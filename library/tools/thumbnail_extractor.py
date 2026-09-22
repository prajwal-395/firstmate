"""
thumbnail_extractor.py - Extract representative frames from video clips.

Uses ffmpeg to extract thumbnails at key timestamps for the review dashboard.
Thumbnails are stored in pipeline_output/thumbnails/ as JPEGs.

Still SELECTION - which frame a thumbnail shows - is owned by
`library/tools/frame_ranker.py` (FIRSTMATE VERDICT 2026-09-18): the LAION
aesthetic head picks among candidate instants behind a sharpness gate, and
the legacy timestamp is the fallback whenever the ranker cannot judge.
Both dashboard surfaces - the footage-library browser (/api/clips) and the
review timeline (/api/timeline) - serve the one canonical file per clip
through `get_thumbnail_url`, so one wired selection serves both.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from library.tools import frame_ranker
from library.tools.project_layout import Area, ProjectLayout


def _thumbnails_dir(project_dir: str) -> Path:
    """Get the thumbnails directory for a project."""
    return ProjectLayout(project_dir).write_dir(Area.THUMBNAILS)


def _cached_url(thumbs: Path, filename: str) -> str:
    """The dashboard URL when a usable thumbnail is already cached.

    Usable means non-empty: a zero-byte file is a capture that did not
    happen (see `marker_capture`'s "WHEN THE ROUTE FAILS"), so it is
    removed and the thumbnail is re-extracted rather than served.
    """
    output_path = thumbs / filename
    if output_path.exists():
        try:
            if output_path.stat().st_size > 0:
                return f"/thumbnails/{filename}"
        except OSError:
            pass
        try:
            output_path.unlink()
        except OSError:
            pass
    return ""


def extract_thumbnail(
    video_path: str,
    output_path: str,
    timestamp_s: float = 1.0,
    width: int = 320,
) -> bool:
    """Extract a single thumbnail frame from a video file.

    Returns True if successful - which means a non-empty file on disk.
    A zero-byte file is a capture that did not happen (see
    `marker_capture`'s "WHEN THE ROUTE FAILS") and reads as failure.
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
        return (result.returncode == 0 and os.path.exists(output_path)
                and os.path.getsize(output_path) > 0)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
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

    cached = _cached_url(thumbs, f"{safe_id}.jpg")
    if cached:
        return cached

    success = extract_thumbnail(video_path, str(output_path), timestamp_s, width)
    if success:
        return f"/thumbnails/{safe_id}.jpg"
    return ""


class _NoUsableCandidate(frame_ranker.FrameRankerUnavailable):
    """Internal: the ranker judged, and the judgement was "nothing usable"."""


def extract_ranked_clip_thumbnail(
    project_dir: str,
    clip_id: str,
    video_path: str,
    duration_s: float,
    scorer,
    sharpness_fn=None,
    width: int = 320,
    fallback_timestamp_s: float = None,
) -> str:
    """A thumbnail chosen by the aesthetic ranker behind the sharpness gate.

    Extracts one frame per candidate instant, scores each, and promotes the
    `frame_ranker.choose` winner to the clip's canonical `{clip_id}.jpg` -
    the file `get_thumbnail_url` serves to both the footage browser and the
    review timeline.  Losing candidates are deleted, never served.

    `scorer` supplies `score_files(paths) -> list[float]` (a loaded
    `frame_ranker.LaionAestheticScorer`, or a stub in tests).
    `sharpness_fn(path) -> float` defaults to the gate's own geometry.
    Any failure to judge - scorer unavailable, nothing extracted, nothing
    sharp - falls back to the legacy single-timestamp pick and says so on
    stderr.  Returns empty string only when the fallback fails too.
    """
    thumbs = _thumbnails_dir(project_dir)
    safe_id = clip_id.replace("/", "_").replace(" ", "_")
    canonical = thumbs / f"{safe_id}.jpg"

    cached = _cached_url(thumbs, f"{safe_id}.jpg")
    if cached:
        return cached

    if fallback_timestamp_s is None:
        fallback_timestamp_s = frame_ranker.legacy_timestamp(duration_s)

    def _fallback(reason: str) -> str:
        print(f"  thumbnail {clip_id}: ranker stood down ({reason}); "
              f"legacy pick at {fallback_timestamp_s:.3f}s", file=sys.stderr)
        if extract_thumbnail(video_path, str(canonical),
                             fallback_timestamp_s, width):
            return f"/thumbnails/{safe_id}.jpg"
        return ""

    try:
        times = frame_ranker.candidate_timestamps(duration_s)
    except (TypeError, ValueError) as exc:
        return _fallback(f"no candidates: {exc}")

    staged = []
    for i, ts in enumerate(times):
        cand_path = thumbs / f"{safe_id}_rank{i:02d}.jpg"
        try:
            ok = extract_thumbnail(video_path, str(cand_path), ts, width)
        except (OSError, subprocess.SubprocessError):
            ok = False
        if ok:
            staged.append({"timestamp": ts, "path": str(cand_path)})
    if not staged:
        return _fallback("no candidate extracted")
    try:
        try:
            scores = scorer.score_files([s["path"] for s in staged])
        except AttributeError as exc:
            raise frame_ranker.FrameRankerUnavailable(
                f"scorer has no score_files: {exc}") from exc
        if len(scores) != len(staged):
            raise _NoUsableCandidate("scorer returned a short score list")
        measure = (sharpness_fn if sharpness_fn is not None
                   else frame_ranker.sharpness_of_image)
        candidates = []
        for row, score in zip(staged, scores):
            try:
                sharp = measure(row["path"])
            except Exception:  # noqa: BLE001 - unmeasurable is ineligible, not fatal
                sharp = None
            candidates.append({
                "id": f"{safe_id}@{row['timestamp']:.3f}s",
                "timestamp": row["timestamp"],
                "path": row["path"],
                "aesthetic_score": float(score),
                "sharpness": sharp,
            })
        winner, report = frame_ranker.choose(candidates)
        if winner is None:
            raise _NoUsableCandidate("nothing passed the sharpness gate")
        os.replace(winner["path"], canonical)
        print(f"  thumbnail {clip_id}: ranker pick "
              f"{winner['id']} score={winner['aesthetic_score']:.2f} "
              f"sharpness={winner['sharpness']:.1f} "
              f"over {len(candidates)} candidates", file=sys.stderr)
        return f"/thumbnails/{safe_id}.jpg"
    except _NoUsableCandidate:
        return _fallback("nothing passed the sharpness gate")
    except frame_ranker.FrameRankerUnavailable as exc:
        return _fallback(f"scorer unavailable: {exc}")
    finally:
        for row in staged:
            try:
                if os.path.exists(row["path"]):
                    os.unlink(row["path"])
            except OSError:
                pass


def extract_thumbnails_for_catalog(
    project_dir: str,
    clip_catalog: list,
    scorer=None,
) -> dict:
    """Extract thumbnails for all clips in the catalog.

    `scorer` is a loaded `frame_ranker` scorer (anything with
    `score_files(paths) -> list[float]`).  When None - the default, and
    the case wherever the 1.6 GB weights are not loaded - every clip gets
    the legacy timestamp pick, byte-identical to before the adoption.
    When supplied, each clip's thumbnail is the sharpness-gated aesthetic
    pick, falling back to the legacy pick per clip wherever the ranker
    cannot judge.  Returns a dict mapping clip_id -> thumbnail URL path.
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
        ts = frame_ranker.legacy_timestamp(duration) if duration else 1.0

        if scorer is None:
            url = extract_clip_thumbnail(project_dir, clip_id, filepath, ts)
        else:
            url = extract_ranked_clip_thumbnail(
                project_dir, clip_id, filepath, duration, scorer,
                fallback_timestamp_s=ts)
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

    cached = _cached_url(thumbs, filename)
    if cached:
        return cached

    success = extract_thumbnail(video_path, str(thumbs / filename), timestamp_s)
    if success:
        return f"/thumbnails/{filename}"
    return ""


def get_thumbnail_url(project_dir: str, clip_id: str) -> str:
    """Get the thumbnail URL for a clip if a usable one is cached."""
    thumbs = _thumbnails_dir(project_dir)
    safe_id = clip_id.replace("/", "_").replace(" ", "_")
    return _cached_url(thumbs, f"{safe_id}.jpg")
