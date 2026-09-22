import os
import sys

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
            return None
        return resolve
    except ImportError:
        return None

def _verify_track_placement(track, expected_clips, timeline_items,
                            tolerance, expected_frame_of, name_of) -> list:
    """Compare planned positions to actual ones, claiming items in order.

    Each timeline item can satisfy at most one manifest clip, so repeated
    use of one source file no longer collapses onto its first occurrence.
    """
    errors = []
    unclaimed = list(timeline_items)

    for clip in expected_clips:
        expected_frame = expected_frame_of(clip)
        if expected_frame is None:
            continue
        basename = name_of(clip)

        candidates = [
            item for item in unclaimed if basename in item.GetName()
        ]
        if not candidates:
            errors.append(
                f"{track} missing: could not find {basename} in track {track}"
            )
            continue

        best = min(candidates, key=lambda i: abs(i.GetStart() - expected_frame))
        unclaimed.remove(best)
        actual_frame = best.GetStart()
        if abs(actual_frame - expected_frame) > tolerance:
            errors.append(
                f"{track} desync: {basename} expected at {expected_frame}, "
                f"actual {actual_frame} "
                f"(off by {abs(actual_frame - expected_frame)})"
            )

    return errors


def run_timeline_sync_qa(manifest: dict, project_name: str, timeline_name: str) -> dict:
    if os.environ.get("SKIP_QA_CHECKS") == "1":
        print("Skipping timeline sync QA check (SKIP_QA_CHECKS=1)", file=sys.stderr)
        return {"passed": True, "reason": "skipped"}
        
    resolve = _connect_resolve()
    if not resolve:
        print("Warning: Could not connect to Resolve for sync QA check", file=sys.stderr)
        return {"passed": True, "reason": "resolve_not_available"}
        
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    
    if project_name and (not project or project.GetName() != project_name):
        project = pm.LoadProject(project_name)
        
    if not project:
        raise RuntimeError("Could not find Resolve project for sync QA check")
        
    # Find timeline
    timeline = None
    for i in range(1, int(project.GetTimelineCount()) + 1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() == timeline_name:
            timeline = tl
            break
            
    if not timeline:
        raise RuntimeError(f"Could not find timeline '{timeline_name}' for sync QA check")

    # Read through the handle: every check below is `timeline.Get*`,
    # so setting the current timeline first would move the cursor for
    # no reader - the unleashed move that killed a sibling lane's
    # Fusion pass on 2026-09-20. The cursor stays where it was.
    fps = float(timeline.GetSetting("timelineFrameRate"))
    
    # Check V1
    tracks = manifest.get('tracks', {})
    v1_clips = tracks.get('V1', {}).get('clips', [])
    v1_items = timeline.GetItemListInTrack("video", 1) or []
    
    errors = []
    
    # We allow +/- 2 frames tolerance
    TOLERANCE = 2
    
    # Verify V1 clips.
    # A shortform edit returns to the same source file many times, so
    # matching by basename found the FIRST occurrence every time and
    # reported six correctly-placed clips as desynced. Match by position
    # instead: the nearest unclaimed item from the same source file.
    errors.extend(_verify_track_placement(
        "V1", v1_clips, v1_items, TOLERANCE,
        expected_frame_of=lambda c: c.get('timeline_in_frame'),
        name_of=lambda c: os.path.basename(c.get('source_file', '')),
    ))

    
    # Check V3 Subtitles
    sub_overlay_info = manifest.get('subtitle_overlay', {})
    sub_segments = sub_overlay_info.get('segments', [])
    v3_items = timeline.GetItemListInTrack("video", 3) or []
    
    errors.extend(_verify_track_placement(
        "V3", sub_segments, v3_items, TOLERANCE,
        expected_frame_of=lambda seg: round(seg.get('timeline_start', 0) * fps),
        name_of=lambda seg: os.path.basename(seg.get('overlay_path', '')),
    ))

    if errors:
        raise RuntimeError("Timeline Sync QA Failed:\n" + "\n".join(errors))
        
    print("Timeline sync QA passed.", file=sys.stderr)
    return {"passed": True, "reason": "sync ok"}
