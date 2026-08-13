#!/usr/bin/env python3
"""
Step 1.2: Catalog Raw Footage

For each video file in the inventory, extracts technical metadata using
ffprobe: duration, resolution, frame rate, codec, audio channels, file size,
creation timestamp, and rotation. Stores the catalog in chronological order
by creation timestamp and assigns sequential source_order integers.

Classification: Deterministic / Information Retrieval
Archetype: Information Retrieval
Idempotent: Yes

Input:  { "raw_footage_files": [{ path, filename, extension, size_bytes }, ...] }
Output: { "clip_catalog": [{ clip_id, source_order, path, ..., has_audio }, ...] }

Requires: ffprobe (from ffmpeg) available on PATH.
"""
import json
import os
import subprocess
import sys
from datetime import datetime


def extract_metadata(filepath: str) -> dict:
    """
    Extract technical metadata from a video file using ffprobe.
    Returns a dict of metadata fields, or a dict with an 'error' key if extraction fails.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                filepath,
            ],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found. Install ffmpeg: brew install ffmpeg"
        )
    except subprocess.TimeoutExpired as e:
        err_msg = f"ffprobe timed out after {e.timeout}s"
        print(f"WARNING: {err_msg} for {filepath}", file=sys.stderr)
        return {"error": err_msg, "timeout_duration": e.timeout}

    if result.returncode != 0:
        err_msg = f"ffprobe failed (exit {result.returncode}): {result.stderr[:200]}"
        print(f"WARNING: {err_msg} for {filepath}", file=sys.stderr)
        return {"error": err_msg}

    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError:
        err_msg = f"ffprobe returned invalid JSON"
        print(f"WARNING: {err_msg} for {filepath}", file=sys.stderr)
        return {"error": err_msg}

    # Find video and audio streams
    video_stream = None
    audio_stream = None
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream
        elif stream.get("codec_type") == "audio" and audio_stream is None:
            audio_stream = stream

    if not video_stream:
        err_msg = "No video stream found"
        print(f"WARNING: {err_msg} in {filepath}", file=sys.stderr)
        return {"error": err_msg}

    fmt = probe.get("format", {})
    fmt_tags = fmt.get("tags", {})

    # Extract creation_time from format tags (multiple possible keys)
    creation_time = None
    for key in [
        "creation_time",
        "com.apple.quicktime.creationdate",
        "date",
    ]:
        val = fmt_tags.get(key) or fmt_tags.get(key.lower())
        if val:
            creation_time = val
            break

    # Also check video stream tags
    if not creation_time:
        stream_tags = video_stream.get("tags", {})
        for key in ["creation_time", "date"]:
            val = stream_tags.get(key)
            if val:
                creation_time = val
                break

    # Parse frame rate from video stream
    frame_rate = None
    fps_str = video_stream.get("r_frame_rate", "")
    if fps_str and "/" in fps_str:
        num, den = fps_str.split("/")
        if int(den) > 0:
            frame_rate = round(int(num) / int(den), 3)
    elif fps_str:
        try:
            frame_rate = float(fps_str)
        except ValueError:
            pass

    # Extract rotation from video stream side_data or tags
    rotation = 0
    for side_data in video_stream.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = int(side_data["rotation"])
            break
    if rotation == 0:
        rot_tag = video_stream.get("tags", {}).get("rotate", "0")
        try:
            rotation = int(rot_tag)
        except ValueError:
            rotation = 0

    return {
        "duration_seconds": round(float(fmt.get("duration", 0)), 3),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "frame_rate": frame_rate,
        "video_codec": video_stream.get("codec_name", "unknown"),
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
        "audio_channels": (
            int(audio_stream["channels"])
            if audio_stream and "channels" in audio_stream
            else None
        ),
        "audio_sample_rate": (
            int(audio_stream["sample_rate"])
            if audio_stream and "sample_rate" in audio_stream
            else None
        ),
        "creation_time": creation_time,
        "rotation": rotation,
        "pixel_format": video_stream.get("pix_fmt", "unknown"),
        "has_audio": audio_stream is not None,
    }


def parse_creation_time(ct_str: str | None) -> datetime | None:
    """Parse a creation_time string into a datetime for sorting."""
    if not ct_str:
        return None
    # Try common formats
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(ct_str, fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def catalog_footage(raw_footage_files: list) -> dict:
    """
    Extract metadata for each file, sort chronologically, assign ordering.
    """
    entries = []
    skipped = []

    for file_info in raw_footage_files:
        filepath = file_info["path"]

        if not os.path.isfile(filepath):
            skipped.append({
                "path": filepath,
                "reason": "file not found"
            })
            print(
                f"WARNING: File not found, skipping: {filepath}",
                file=sys.stderr,
            )
            continue

        metadata = extract_metadata(filepath)
        if metadata is None or "error" in metadata:
            err = metadata.get("error", "metadata extraction failed") if metadata else "metadata extraction failed"
            skipped.append({
                "path": filepath,
                "reason": err,
                "timeout_duration": metadata.get("timeout_duration") if metadata else None
            })
            continue

        entries.append({
            "path": filepath,
            "source_file": filepath,  # Alias for downstream steps (3.1, 3.3)
            "filename": file_info["filename"],
            "file_size_bytes": file_info["size_bytes"],
            "clip_id": file_info.get("clip_id", ""),
            **metadata,
        })

    if not entries:
        raise ValueError(
            "No files yielded valid metadata. "
            "Cannot build catalog."
        )

    # --- Fall back to filesystem mtime when creation_time is absent ---
    # H5 fix: Some clips (e.g., screen recordings, re-encoded files) lack
    # creation_time metadata. Using mtime preserves chronological ordering
    # instead of crashing the pipeline.
    for entry in entries:
        if not entry.get("creation_time"):
            mtime = os.path.getmtime(entry["path"])
            entry["creation_time"] = datetime.fromtimestamp(mtime).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            print(
                f"WARNING: No creation_time for {entry['filename']}, "
                f"falling back to filesystem mtime: {entry['creation_time']}",
                file=sys.stderr,
            )

    # --- Sort by creation_time ascending, filename as tiebreaker ---
    entries.sort(key=lambda e: (
        parse_creation_time(e["creation_time"]) or datetime.min,
        e["filename"],
    ))

    # --- Assign source_order ---
    for i, entry in enumerate(entries):
        entry["source_order"] = i + 1

    # --- Derive project fps and resolution ---
    fps_counts = {}
    res_counts = {}
    for entry in entries:
        fps = entry.get("frame_rate")
        if fps:
            fps_counts[fps] = fps_counts.get(fps, 0) + 1
        w = entry.get("width")
        h = entry.get("height")
        if w and h:
            res = (w, h)
            res_counts[res] = res_counts.get(res, 0) + 1
            
    project_fps = max(fps_counts, key=fps_counts.get) if fps_counts else 30.0
    project_res = list(max(res_counts, key=res_counts.get)) if res_counts else [1080, 1920]

    # --- Verification ---
    # No null values for critical fields
    for entry in entries:
        for field in ["duration_seconds", "width", "height", "frame_rate"]:
            assert entry.get(field) is not None, \
                f"Missing {field} for {entry['filename']}"

    # source_order values are unique and sequential
    orders = [e["source_order"] for e in entries]
    assert orders == list(range(1, len(entries) + 1)), \
        "source_order values are not sequential"

    # clip_id values are unique
    ids = [e["clip_id"] for e in entries]
    assert len(ids) == len(set(ids)), "Duplicate clip_id values"

    return {
        "clip_catalog": entries,
        "total_clips": len(entries),
        "skipped_files": skipped,
        "project_fps": project_fps,
        "project_resolution": project_res,
    }


def main():
    input_data = json.loads(sys.stdin.read())
    raw_footage_files = input_data.get("raw_footage_files")

    if not raw_footage_files:
        print(json.dumps({
            "error": "Missing required input: raw_footage_files",
            "step": "1.2_catalog_footage"
        }))
        sys.exit(1)

    try:
        result = catalog_footage(raw_footage_files)
    except (ValueError, RuntimeError) as e:
        print(json.dumps({
            "error": str(e),
            "step": "1.2_catalog_footage"
        }))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
