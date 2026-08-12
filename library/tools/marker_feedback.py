import os
import sys
import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime

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
        # Mock for testing when API not available
        return None

# ─── Dataclasses ─────────────────────────────────────────────

@dataclass
class ClipContext:
    """Lightweight context about a clip at a given frame."""
    name: str
    track_type: str
    track_index: int
    clip_color: str = ""
    flags: List[str] = field(default_factory=list)

@dataclass
class ChangeRequest:
    frame_position: int
    duration_frames: int
    track_type: str
    track_index: int
    clip_name: str
    instruction: str
    source: str
    marker_color: str
    custom_data: Dict[str, Any]
    timestamp: str
    clip_color: Optional[str] = None
    clip_flags: List[str] = field(default_factory=list)
    clips_at_frame: List[Dict[str, Any]] = field(default_factory=list)


# ─── Core Functions ──────────────────────────────────────────

def _parse_custom_data(data_str: str) -> Dict[str, Any]:
    if not data_str:
        return {}
    try:
        return json.loads(data_str)
    except Exception:
        return {"raw": data_str}


def _get_clips_at_frame(timeline, frame: int) -> List[Any]:
    clips = []
    for track_type in ["video", "audio"]:
        track_count = timeline.GetTrackCount(track_type)
        for i in range(1, track_count + 1):
            items = timeline.GetItemListInTrack(track_type, i)
            if not items:
                continue
            for item in items:
                start = item.GetStart()
                end = item.GetEnd()
                if start <= frame < end:
                    clips.append({
                        "item": item,
                        "track_type": track_type,
                        "track_index": i
                    })
    return clips


def get_all_feedback() -> List[ChangeRequest]:
    """Get all markers and comments from the current timeline."""
    resolve = _connect_resolve()
    if not resolve:
        return []
        
    project_manager = resolve.GetProjectManager()
    project = project_manager.GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    if not timeline:
        return []
        
    requests = []
    timestamp = datetime.now().isoformat()
    
    # 1. Timeline-level markers
    timeline_markers = timeline.GetMarkers()
    if timeline_markers:
        for frame, marker in timeline_markers.items():
            # Find context
            clips_at_frame = _get_clips_at_frame(timeline, frame)
            
            # Build clip context list for all clips at this frame
            clip_context = []
            for clip_info in clips_at_frame:
                ci = clip_info["item"]
                clip_context.append({
                    "name": ci.GetName(),
                    "track_type": clip_info["track_type"],
                    "track_index": clip_info["track_index"],
                    "clip_color": ci.GetClipColor() or "",
                    "flags": ci.GetFlagList() or [],
                })

            # One request per timeline marker with all clips as context.
            # We do NOT guess which clip the marker targets - the natural language
            # instruction is the signal, and the LLM interprets it against the
            # clips_at_frame context.
            requests.append(ChangeRequest(
                frame_position=int(frame),
                duration_frames=marker.get("duration", 1),
                track_type="timeline",
                track_index=0,
                clip_name="",
                instruction=marker.get("note", ""),
                source="timeline_marker",
                marker_color=marker.get("color", ""),
                custom_data=_parse_custom_data(marker.get("customData", "")),
                timestamp=timestamp,
                clips_at_frame=clip_context,
            ))
                    
    # 2. Per-clip markers and comments
    for track_type in ["video", "audio"]:
        track_count = timeline.GetTrackCount(track_type)
        for i in range(1, track_count + 1):
            items = timeline.GetItemListInTrack(track_type, i)
            if not items:
                continue
                
            for item in items:
                start_frame = item.GetStart()
                
                # Clip comments
                comment = item.GetProperty("Comments")
                if comment:
                    requests.append(ChangeRequest(
                        frame_position=start_frame,
                        duration_frames=item.GetDuration(),
                        track_type=track_type,
                        track_index=i,
                        clip_name=item.GetName(),
                        instruction=comment,
                        source="clip_comment",
                        marker_color="",
                        custom_data={},
                        timestamp=timestamp,
                        clip_color=item.GetClipColor(),
                        clip_flags=item.GetFlagList()
                    ))
                    
                # Clip markers
                clip_markers = item.GetMarkers()
                if clip_markers:
                    for c_frame, marker in clip_markers.items():
                        # Map clip frame to timeline frame (approximate without LeftOffset)
                        # The API returns source frames or 0-indexed clip frames.
                        # Assuming it's relative to source start, we can just use timeline start + offset
                        left_offset = item.GetLeftOffset() if hasattr(item, 'GetLeftOffset') else 0
                        timeline_frame = start_frame + max(0, int(c_frame) - left_offset)
                        
                        requests.append(ChangeRequest(
                            frame_position=timeline_frame,
                            duration_frames=marker.get("duration", 1),
                            track_type=track_type,
                            track_index=i,
                            clip_name=item.GetName(),
                            instruction=marker.get("note", ""),
                            source="clip_marker",
                            marker_color=marker.get("color", ""),
                            custom_data=_parse_custom_data(marker.get("customData", "")),
                            timestamp=timestamp,
                            clip_color=item.GetClipColor(),
                            clip_flags=item.GetFlagList()
                        ))
                        
    return requests


def save_snapshot(path: str) -> None:
    requests = get_all_feedback()
    data = [asdict(r) for r in requests]
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


def load_snapshot(path: str) -> List[ChangeRequest]:
    if not os.path.exists(path):
        return []
    with open(path, 'r') as f:
        data = json.load(f)
    return [ChangeRequest(**d) for d in data]


def get_new_feedback(since_snapshot_path: str) -> List[ChangeRequest]:
    """Compare current markers against a previous snapshot."""
    old_requests = load_snapshot(since_snapshot_path)
    current_requests = get_all_feedback()
    
    # We define a request as "new" if we haven't seen one with the same instruction at the same frame
    old_signatures = {
        f"{r.frame_position}_{r.track_type}_{r.track_index}_{r.instruction}_{r.source}"
        for r in old_requests
    }
    
    new_requests = []
    for r in current_requests:
        sig = f"{r.frame_position}_{r.track_type}_{r.track_index}_{r.instruction}_{r.source}"
        if sig not in old_signatures:
            new_requests.append(r)
            
    return new_requests


def acknowledge_feedback(frame: int, response_note: str) -> None:
    """Adds a response marker to indicate processing."""
    resolve = _connect_resolve()
    if not resolve:
        return
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    
    timeline.AddMarker(
        frameId=frame,
        color="Green",
        name="Done",
        note=response_note,
        duration=1,
        customData=""
    )


def clear_processed_markers(snapshot_path: str) -> None:
    """Removes timeline markers that have been processed (based on snapshot)."""
    resolve = _connect_resolve()
    if not resolve:
        return
        
    project = resolve.GetProjectManager().GetCurrentProject()
    timeline = project.GetCurrentTimeline()
    
    old_requests = load_snapshot(snapshot_path)
    # We only clear timeline markers here for simplicity, though clip markers could be deleted via item.DeleteMarkerAtFrame
    processed_timeline_frames = [
        r.frame_position for r in old_requests if r.source == "timeline_marker"
    ]
    
    for frame in set(processed_timeline_frames):
        timeline.DeleteMarkerAtFrame(frame)


if __name__ == "__main__":
    feedback = get_all_feedback()
    print(json.dumps([asdict(f) for f in feedback], indent=2))
