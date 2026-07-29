#!/usr/bin/env python3
"""
resolve_bridge.py - DaVinci Resolve Python Scripting Bridge (v2)

Interfaces with DaVinci Resolve's scripting API to:
1. Extract information about the clip currently under the playhead
2. List all A-roll clips on a given video track
3. Import rendered overlay videos and place them on the timeline

Usage:
    python3 resolve_bridge.py get-clip                              # Get current clip info
    python3 resolve_bridge.py get-clip -o clip.json                 # Save to file
    python3 resolve_bridge.py list-clips [--track 1]                # List all clips on track
    python3 resolve_bridge.py import-overlay <path> <clip_info>     # Import overlay
    python3 resolve_bridge.py export-fcpxml <output_path>           # Export FCPXML
"""

import sys
import json
import os
import argparse

RESOLVE_MODULES_PATH = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/"


def connect_to_resolve():
    """Connect to the running DaVinci Resolve instance."""
    if RESOLVE_MODULES_PATH not in sys.path:
        sys.path.append(RESOLVE_MODULES_PATH)

    try:
        import DaVinciResolveScript as dvr_script
    except ImportError:
        print("ERROR: Could not import DaVinciResolveScript.", file=sys.stderr)
        sys.exit(1)

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        print("ERROR: Could not connect to DaVinci Resolve.", file=sys.stderr)
        sys.exit(1)

    return resolve


def _get_timeline_objects():
    """Helper: get resolve, project, media_pool, timeline objects."""
    resolve = connect_to_resolve()
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    if not project:
        print("ERROR: No project open.", file=sys.stderr)
        sys.exit(1)
    media_pool = project.GetMediaPool()
    timeline = project.GetCurrentTimeline()
    if not timeline:
        print("ERROR: No timeline active.", file=sys.stderr)
        sys.exit(1)
    return resolve, project, media_pool, timeline


def _item_to_dict(item, timeline, fps):
    """Convert a TimelineItem to a serializable dict."""
    mpi = item.GetMediaPoolItem()
    file_path = mpi.GetClipProperty("File Path") if mpi else None
    src_start = item.GetSourceStartFrame()
    src_end = item.GetSourceEndFrame()
    track_info = item.GetTrackTypeAndIndex()

    return {
        "clip_name": item.GetName(),
        "file_path": file_path,
        "timeline_start_frame": item.GetStart(),
        "timeline_end_frame": item.GetEnd(),
        "duration_frames": item.GetDuration(),
        "source_start_frame": src_start,
        "source_end_frame": src_end,
        "source_start_seconds": src_start / fps if src_start is not None else None,
        "source_end_seconds": src_end / fps if src_end is not None else None,
        "fps": fps,
        "width": int(timeline.GetSetting("timelineResolutionWidth") or 1080),
        "height": int(timeline.GetSetting("timelineResolutionHeight") or 1920),
        "track_type": track_info[0] if track_info else "video",
        "track_index": track_info[1] if track_info else 1,
        "video_track_count": timeline.GetTrackCount("video"),
        "timeline_name": timeline.GetName(),
        "is_transition": src_start is None,  # Transitions have no source frames
    }


def get_current_clip_info():
    """Get information about the clip currently under the playhead."""
    _, _, _, timeline = _get_timeline_objects()
    fps = float(timeline.GetSetting("timelineFrameRate") or 30)

    current_item = timeline.GetCurrentVideoItem()
    if not current_item:
        print("ERROR: No clip under playhead.", file=sys.stderr)
        sys.exit(1)

    return _item_to_dict(current_item, timeline, fps)


def list_clips_on_track(track_index=1):
    """List all clips on a specific video track, skipping transitions."""
    _, _, _, timeline = _get_timeline_objects()
    fps = float(timeline.GetSetting("timelineFrameRate") or 30)

    items = timeline.GetItemListInTrack("video", track_index)
    if not items:
        return []

    clips = []
    for item in items:
        info = _item_to_dict(item, timeline, fps)
        if not info["is_transition"]:  # Skip transitions
            clips.append(info)

    return clips


def import_overlay_to_timeline(overlay_path, clip_info, track_name="Subtitles (Remotion)"):
    """Import overlay and place on a named track, aligned with the original clip."""
    _, _, media_pool, timeline = _get_timeline_objects()

    overlay_path = os.path.abspath(overlay_path)
    if not os.path.exists(overlay_path):
        raise FileNotFoundError(f"Overlay not found: {overlay_path}")

    print(f"Importing: {overlay_path}", file=sys.stderr)

    # Import into media pool
    clips = media_pool.ImportMedia([overlay_path])
    if not clips:
        raise RuntimeError(f"Failed to import {overlay_path}")

    overlay_clip = clips[0]

    # Find or create the named track
    current_track_count = timeline.GetTrackCount("video")
    target_track = None
    for t in range(1, current_track_count + 1):
        if timeline.GetTrackName("video", t) == track_name:
            target_track = t
            break

    if target_track is None:
        timeline.AddTrack("video")
        target_track = timeline.GetTrackCount("video")
        timeline.SetTrackName("video", target_track, track_name)

    print(f"Placing on track '{track_name}' ({target_track}) at frame {clip_info['timeline_start_frame']}", file=sys.stderr)

    result = media_pool.AppendToTimeline([{
        "mediaPoolItem": overlay_clip,
        "startFrame": 0,
        "endFrame": clip_info["duration_frames"],
        "trackIndex": target_track,
        "recordFrame": clip_info["timeline_start_frame"],
        "mediaType": 1,
    }])

    if not result:
        raise RuntimeError("Failed to place overlay on timeline")

    return {
        "success": True,
        "track_index": target_track,
        "track_name": track_name,
        "timeline_start_frame": clip_info["timeline_start_frame"],
        "duration_frames": clip_info["duration_frames"],
    }


def clear_track(track_name):
    """Remove all clips from a named video track."""
    _, _, _, timeline = _get_timeline_objects()

    # Find the track by name
    track_count = timeline.GetTrackCount("video")
    target_track = None
    for t in range(1, track_count + 1):
        if timeline.GetTrackName("video", t) == track_name:
            target_track = t
            break

    if target_track is None:
        return {"success": True, "message": f"Track '{track_name}' not found, nothing to clear", "deleted": 0}

    # Get all items on that track
    items = timeline.GetItemListInTrack("video", target_track)
    if not items:
        return {"success": True, "message": f"Track '{track_name}' is already empty", "deleted": 0}

    # Delete each item
    deleted = 0
    for item in items:
        if timeline.DeleteTimelineItem(item):
            deleted += 1

    return {
        "success": True,
        "track_index": target_track,
        "track_name": track_name,
        "deleted": deleted,
    }


def main():
    parser = argparse.ArgumentParser(description="DaVinci Resolve scripting bridge v2")
    subparsers = parser.add_subparsers(dest="command")

    # get-clip
    p = subparsers.add_parser("get-clip", help="Get clip under playhead")
    p.add_argument("--output", "-o", help="Output JSON file")

    # list-clips
    p = subparsers.add_parser("list-clips", help="List all clips on a track")
    p.add_argument("--track", type=int, default=1, help="Video track index (default: 1)")
    p.add_argument("--output", "-o", help="Output JSON file")

    # import-overlay
    p = subparsers.add_parser("import-overlay", help="Import overlay to timeline")
    p.add_argument("overlay_path", help="Path to overlay .mov file")
    p.add_argument("clip_info_json", help="Path to clip info JSON")
    p.add_argument("--track-name", default="Subtitles (Remotion)",
                   help="Target track name (default: 'Subtitles (Remotion)')")

    # clear-track
    p = subparsers.add_parser("clear-track", help="Remove all clips from a named track")
    p.add_argument("track_name", help="Name of the track to clear")

    # export-fcpxml
    p = subparsers.add_parser("export-fcpxml", help="Export timeline as FCPXML")
    p.add_argument("output_path", help="Output FCPXML file path")

    args = parser.parse_args()

    if args.command == "get-clip":
        result = get_current_clip_info()
        out = json.dumps(result, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(out)
            print(f"Written to {args.output}", file=sys.stderr)
        else:
            print(out)

    elif args.command == "list-clips":
        result = list_clips_on_track(args.track)
        out = json.dumps(result, indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(out)
            print(f"Written to {args.output} ({len(result)} clips)", file=sys.stderr)
        else:
            print(out)

    elif args.command == "import-overlay":
        with open(args.clip_info_json) as f:
            clip_info = json.load(f)
        result = import_overlay_to_timeline(args.overlay_path, clip_info, args.track_name)
        print(json.dumps(result, indent=2))

    elif args.command == "clear-track":
        result = clear_track(args.track_name)
        print(json.dumps(result, indent=2))

    elif args.command == "export-fcpxml":
        resolve, _, _, timeline = _get_timeline_objects()
        path = os.path.abspath(args.output_path)
        success = timeline.Export(path, resolve.EXPORT_FCPXML_1_10, resolve.EXPORT_NONE)
        print(json.dumps({"success": success, "path": path}, indent=2))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

