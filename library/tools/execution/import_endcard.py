#!/usr/bin/env python3
"""
import_endcard.py - Import rendered Lucie end card into DaVinci Resolve

Imports the rendered LucieEndCard motion graphic and places it at the end
of the existing timeline on V1 (the A-Roll track), extending the edit.

Usage:
    python3 import_endcard.py [--endcard-path PATH]
"""

import sys
import json
import os
import argparse

RESOLVE_MODULES_PATH = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/"

DEFAULT_ENDCARD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "output",
    "lucie_endcard.mov",
)


def connect_to_resolve():
    """Connect to the running DaVinci Resolve instance."""
    if RESOLVE_MODULES_PATH not in sys.path:
        sys.path.append(RESOLVE_MODULES_PATH)

    try:
        import DaVinciResolveScript as dvr_script
    except ImportError:
        print("ERROR: Could not import DaVinciResolveScript.", file=sys.stderr)
        print("Make sure DaVinci Resolve is running and the scripting module is installed.", file=sys.stderr)
        sys.exit(1)

    resolve = dvr_script.scriptapp("Resolve")
    if resolve is None:
        print("ERROR: Could not connect to DaVinci Resolve.", file=sys.stderr)
        print("Make sure DaVinci Resolve is running.", file=sys.stderr)
        sys.exit(1)

    return resolve


def get_timeline_end_frame(timeline):
    """Get the frame number where the last clip ends on V1."""
    fps = float(timeline.GetSetting("timelineFrameRate") or 30)
    
    # Check all video tracks to find the latest end frame
    track_count = timeline.GetTrackCount("video")
    max_end = 0
    
    for t in range(1, track_count + 1):
        items = timeline.GetItemListInTrack("video", t)
        if items:
            for item in items:
                end = item.GetEnd()
                if end and end > max_end:
                    max_end = end
    
    return max_end, fps


def import_endcard(endcard_path, track_index=1):
    """Import the end card and place it at the end of the timeline."""
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

    endcard_path = os.path.abspath(endcard_path)
    if not os.path.exists(endcard_path):
        print(f"ERROR: End card not found at: {endcard_path}", file=sys.stderr)
        sys.exit(1)

    # Get where the timeline currently ends
    end_frame, fps = get_timeline_end_frame(timeline)
    print(f"Timeline currently ends at frame {end_frame} ({end_frame / fps:.2f}s)", file=sys.stderr)

    # Import the end card into the media pool
    print(f"Importing: {endcard_path}", file=sys.stderr)
    clips = media_pool.ImportMedia([endcard_path])
    if not clips:
        print("ERROR: Failed to import end card into media pool.", file=sys.stderr)
        sys.exit(1)

    endcard_clip = clips[0]

    # Get the clip duration
    clip_props = endcard_clip.GetClipProperty()
    clip_duration = int(clip_props.get("Frames", 150))
    print(f"End card duration: {clip_duration} frames ({clip_duration / fps:.2f}s)", file=sys.stderr)

    # Place on V1 at the end of the timeline
    print(f"Placing on V1 at frame {end_frame}", file=sys.stderr)

    result = media_pool.AppendToTimeline([{
        "mediaPoolItem": endcard_clip,
        "startFrame": 0,
        "endFrame": clip_duration,
        "trackIndex": track_index,
        "recordFrame": end_frame,
        "mediaType": 1,  # Video
    }])

    if not result:
        # Try alternative approach: AppendToTimeline without specifying recordFrame
        print("Direct placement failed. Trying AppendToTimeline...", file=sys.stderr)
        result = media_pool.AppendToTimeline([{
            "mediaPoolItem": endcard_clip,
            "trackIndex": track_index,
            "mediaType": 1,
        }])

    if not result:
        print("ERROR: Failed to place end card on timeline.", file=sys.stderr)
        sys.exit(1)

    new_end = end_frame + clip_duration
    print(f"\n✓ End card placed successfully!", file=sys.stderr)
    print(f"  Timeline now ends at frame {new_end} ({new_end / fps:.2f}s)", file=sys.stderr)
    print(f"  End card: {end_frame / fps:.2f}s → {new_end / fps:.2f}s", file=sys.stderr)

    return {
        "success": True,
        "endcard_path": endcard_path,
        "timeline_start_frame": end_frame,
        "timeline_end_frame": new_end,
        "duration_frames": clip_duration,
        "timeline_start_seconds": end_frame / fps,
        "timeline_end_seconds": new_end / fps,
        "fps": fps,
    }


def main():
    parser = argparse.ArgumentParser(description="Import Lucie end card into DaVinci Resolve")
    parser.add_argument(
        "--endcard-path",
        default=DEFAULT_ENDCARD_PATH,
        help=f"Path to rendered end card .mov (default: {DEFAULT_ENDCARD_PATH})",
    )
    parser.add_argument(
        "--track",
        type=int,
        default=1,
        help="Video track index to place on (default: 1 = V1)",
    )
    args = parser.parse_args()

    result = import_endcard(args.endcard_path, args.track)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
