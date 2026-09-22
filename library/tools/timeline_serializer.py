#!/usr/bin/env python3
"""
Timeline State Serializer

Reads the full state of a DaVinci Resolve timeline and serializes it to a
deterministic, git-diffable JSON format (.timeline.json).
"""

import json
import os
import sys
import datetime
from typing import Optional, Dict, Any, List

# ─── Resolve Connection ──────────────────────────────────────

def _connect_resolve():
    """Connect to running DaVinci Resolve instance."""
    api_path = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    lib_path = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

    if api_path not in sys.path:
        sys.path.append(os.path.join(api_path, "Modules"))
    os.environ["RESOLVE_SCRIPT_API"] = api_path
    os.environ["RESOLVE_SCRIPT_LIB"] = lib_path

    try:
        import DaVinciResolveScript as dvr
        resolve = dvr.scriptapp("Resolve")
        if not resolve:
            raise ConnectionError("Cannot connect to DaVinci Resolve. Is it running?")
        return resolve
    except ImportError:
        raise ImportError("DaVinciResolveScript module not found. Is Resolve installed?")

# ─── Helpers ─────────────────────────────────────────────────

def format_float(val: Any) -> Any:
    """Format floats consistently to 4 decimal places."""
    if isinstance(val, float):
        return round(val, 4)
    if isinstance(val, list):
        return [format_float(v) for v in val]
    if isinstance(val, dict):
        return {k: format_float(v) for k, v in val.items()}
    return val

def _clean_dict(d: dict) -> dict:
    """Recursively sort keys and format floats."""
    result = {}
    for k in sorted(d.keys()):
        v = d[k]
        if isinstance(v, dict):
            result[k] = _clean_dict(v)
        elif isinstance(v, list):
            result[k] = [format_float(x) if isinstance(x, (float, dict, list)) else x for x in v]
        elif isinstance(v, float):
            result[k] = round(v, 4)
        else:
            result[k] = v
    return result

# ─── Serializer ──────────────────────────────────────────────

def serialize_timeline_state(
    manifest_path: Optional[str] = None,
    pipeline_data_path: Optional[str] = None,
    resolve_mock = None,
    timeline=None,
) -> Dict[str, Any]:
    """
    Reads the current timeline state from Resolve and serializes it to a dictionary.

    Pass `timeline` to read a NAMED timeline through its own handle:
    nothing below reads the cursor, so setting the current timeline in
    order to read is a cursor move with no reader behind it - the exact
    move that killed a sibling lane's Fusion pass on 2026-09-20. A
    caller that names its handle keeps the cursor where it found it.
    Without `timeline` the current one is read, as before.
    """
    if timeline is None:
        if resolve_mock:
            resolve = resolve_mock
        else:
            resolve = _connect_resolve()
        pm = resolve.GetProjectManager()
        project = pm.GetCurrentProject()
        if not project:
            raise RuntimeError("No project is currently open in Resolve.")

        timeline = project.GetCurrentTimeline()
    if not timeline:
        raise RuntimeError("No timeline is currently open.")
        
    state: Dict[str, Any] = {
        "schema_version": "1.0",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "metadata": {},
        "tracks": [],
        "pipeline_state": {}
    }
    
    # Metadata
    state["metadata"]["name"] = timeline.GetName()
    state["metadata"]["start_frame"] = timeline.GetStartFrame()
    state["metadata"]["end_frame"] = timeline.GetEndFrame()
    state["metadata"]["timecode"] = timeline.GetStartTimecode()
    
    try:
        fps_str = timeline.GetSetting("timelineFrameRate")
        state["metadata"]["fps"] = format_float(float(fps_str)) if fps_str else 0.0
    except Exception:
        state["metadata"]["fps"] = 0.0
        
    try:
        width = timeline.GetSetting("timelineResolutionWidth")
        height = timeline.GetSetting("timelineResolutionHeight")
        state["metadata"]["resolution"] = [int(width) if width else 0, int(height) if height else 0]
    except Exception:
        state["metadata"]["resolution"] = [0, 0]

    # Timeline Markers
    markers = timeline.GetMarkers() or {}
    state["metadata"]["markers"] = []
    for frame, marker in markers.items():
        state["metadata"]["markers"].append({
            "frame": frame,
            "color": marker.get("color", ""),
            "name": marker.get("name", ""),
            "note": marker.get("note", ""),
            "duration": marker.get("duration", 0),
            "custom_data": marker.get("customData", "")
        })

    # Tracks and Clips.
    #
    # The per-item detail is read ONCE, in `reel_read.clip_detail` - the
    # one place a clip is enumerated. What follows is that reading
    # projected into this serializer's long-standing output shape, which
    # stays byte-identical so `.timeline.json` diffs keep working. A
    # caller that needs clips takes a slice of `reel_read.read_reel`
    # instead of copying this loop.
    from library.tools.reel_read import clip_detail

    for track_type in ["video", "audio"]:
        track_count = timeline.GetTrackCount(track_type)
        for t in range(1, track_count + 1):
            track_name = timeline.GetTrackName(track_type, t)
            
            track_data = {
                "type": track_type,
                "index": t,
                "name": track_name,
                "clips": []
            }
            
            items = timeline.GetItemListInTrack(track_type, t) or []
            for item in items:
                detail = clip_detail(item, track_type, t, track_name)
                properties = detail["transform"]

                clip_data = {
                    "unique_id": detail["unique_id"],
                    "name": detail["name"],
                    "record_in": detail["record_in"],
                    "record_out": detail["record_out"],
                    "duration": detail["duration"],
                    "source_in": detail["source_in_frame"],
                    "source_out": detail["source_out_frame"],
                    "left_offset": detail["left_offset"],
                    "right_offset": detail["right_offset"],
                    "enabled": detail["enabled"],
                    "clip_color": detail["clip_color"],
                    "media_pool_item_id": detail["media_pool_item_id"],
                    "file_path": detail["source_file"],
                }

                # Transform
                clip_data["transform"] = {
                    "Pan": properties.get("Pan", 0.0),
                    "Tilt": properties.get("Tilt", 0.0),
                    "ZoomX": properties.get("ZoomX", 1.0),
                    "ZoomY": properties.get("ZoomY", 1.0),
                    "RotationAngle": properties.get("RotationAngle", 0.0),
                    "AnchorPointX": properties.get("AnchorPointX", 0.0),
                    "AnchorPointY": properties.get("AnchorPointY", 0.0),
                    "Pitch": properties.get("Pitch", 0.0),
                    "Yaw": properties.get("Yaw", 0.0),
                    "FlipX": properties.get("FlipX", False),
                    "FlipY": properties.get("FlipY", False),
                }

                # Crop
                clip_data["crop"] = {
                    "CropLeft": properties.get("CropLeft", 0.0),
                    "CropRight": properties.get("CropRight", 0.0),
                    "CropTop": properties.get("CropTop", 0.0),
                    "CropBottom": properties.get("CropBottom", 0.0),
                    "CropSoftness": properties.get("CropSoftness", 0.0),
                }

                # Composite
                clip_data["composite"] = {
                    "Opacity": properties.get("Opacity", 100.0),
                    "CompositeMode": properties.get("CompositeMode", "Normal"),
                }

                # Retime (mocking/extracting if available)
                clip_data["retime"] = {
                    "process": properties.get("RetimeProcess", ""),
                    "motion_estimation": properties.get("MotionEstimation", ""),
                    "speed_ratio": properties.get("Speed", 1.0),
                }

                # Audio
                if track_type == "audio":
                    clip_data["audio"] = {
                        "Volume": properties.get("Volume", 0.0),
                        "Pan": properties.get("Pan", 0.0),
                        "AudioSyncOffset": properties.get("AudioSyncOffset", 0),
                    }

                clip_data["color"] = {
                    "cdl": detail["color"]["cdl"],
                    "color_group_name": detail["color"]["color_group"],
                    "grade_version_names": [],
                }

                clip_data["fusion"] = {
                    "comp_count": detail["fusion"]["comp_count"],
                    "comp_names": detail["fusion"]["comp_names"],
                }

                # Markers
                clip_data["markers"] = []
                for marker in detail["markers"]:
                    clip_data["markers"].append({
                        "frame": marker["frame"],
                        "color": marker["color"],
                        "name": marker["name"],
                        "note": marker["note"],
                        "duration": marker["duration"],
                        "custom_data": marker["custom_data"],
                    })

                track_data["clips"].append(clip_data)
                
            state["tracks"].append(track_data)

    # Merging pipeline state
    if manifest_path and os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r') as f:
                state["pipeline_state"]["assembly_manifest"] = json.load(f)
        except Exception as e:
            state["pipeline_state"]["assembly_manifest_error"] = str(e)
            
    if pipeline_data_path and os.path.exists(pipeline_data_path):
        try:
            with open(pipeline_data_path, 'r') as f:
                state["pipeline_state"]["pipeline_data"] = json.load(f)
        except Exception as e:
            state["pipeline_state"]["pipeline_data_error"] = str(e)

    # Clean and return
    return _clean_dict(state)

def save_timeline_state(state: Dict[str, Any], output_path: str):
    """Saves serialized state to a JSON file."""
    with open(output_path, 'w') as f:
        json.dump(state, f, indent=2, sort_keys=True)

# ─── Differ ──────────────────────────────────────────────────

def diff_timeline_states(old_path: str, new_path: str) -> Dict[str, Any]:
    """
    Loads two .timeline.json files and produces a structured diff.
    """
    with open(old_path, 'r') as f:
        old_state = json.load(f)
    with open(new_path, 'r') as f:
        new_state = json.load(f)
        
    diff = {
        "added_clips": [],
        "removed_clips": [],
        "moved_clips": [],
        "changed_grades": [],
    }
    
    # Map old clips
    old_clips = {}
    for track in old_state.get("tracks", []):
        for clip in track.get("clips", []):
            uid = clip.get("unique_id")
            if uid:
                old_clips[uid] = clip
                
    new_clips = {}
    for track in new_state.get("tracks", []):
        for clip in track.get("clips", []):
            uid = clip.get("unique_id")
            if uid:
                new_clips[uid] = clip
                
    for uid, clip in new_clips.items():
        if uid not in old_clips:
            diff["added_clips"].append(clip["name"])
        else:
            old_clip = old_clips[uid]
            # Check moved
            if old_clip.get("record_in") != clip.get("record_in"):
                diff["moved_clips"].append({
                    "name": clip["name"],
                    "old_in": old_clip.get("record_in"),
                    "new_in": clip.get("record_in")
                })
            # Check grades
            old_cdl = old_clip.get("color", {}).get("cdl", {})
            new_cdl = clip.get("color", {}).get("cdl", {})
            if old_cdl != new_cdl:
                diff["changed_grades"].append({
                    "name": clip["name"],
                    "old_cdl": old_cdl,
                    "new_cdl": new_cdl
                })
                
    for uid, clip in old_clips.items():
        if uid not in new_clips:
            diff["removed_clips"].append(clip["name"])
            
    return diff

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="Timeline State Serializer")
    parser.add_argument("--out", "-o", help="Output JSON path")
    parser.add_argument("--manifest", "-m", help="Path to assembly_manifest.json")
    parser.add_argument("--pipeline-data", "-p", help="Path to pipeline_data.json")
    
    args = parser.parse_args()
    try:
        state = serialize_timeline_state(
            manifest_path=args.manifest,
            pipeline_data_path=args.pipeline_data
        )
        if args.out:
            save_timeline_state(state, args.out)
            print(f"Timeline state saved to {args.out}")
        else:
            print(json.dumps(state, indent=2, sort_keys=True))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
