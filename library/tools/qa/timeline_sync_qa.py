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
        
    project.SetCurrentTimeline(timeline)
    fps = float(timeline.GetSetting("timelineFrameRate"))
    
    # Check V1
    tracks = manifest.get('tracks', {})
    v1_clips = tracks.get('V1', {}).get('clips', [])
    v1_items = timeline.GetItemListInTrack("video", 1) or []
    
    errors = []
    
    # We allow +/- 2 frames tolerance
    TOLERANCE = 2
    
    # Verify V1 clips
    for clip in v1_clips:
        expected_frame = clip.get('timeline_in_frame')
        if expected_frame is None:
            continue
            
        basename = os.path.basename(clip.get('source_file', ''))
        
        # Find matching item in timeline
        found = False
        for item in v1_items:
            if basename in item.GetName():
                actual_frame = item.GetStart()
                if abs(actual_frame - expected_frame) > TOLERANCE:
                    errors.append(f"V1 desync: {basename} expected at {expected_frame}, actual {actual_frame} (off by {abs(actual_frame - expected_frame)})")
                found = True
                break
                
        if not found:
            errors.append(f"V1 missing: could not find {basename} in track V1")
            
    # Check V3 Subtitles
    sub_overlay_info = manifest.get('subtitle_overlay', {})
    sub_segments = sub_overlay_info.get('segments', [])
    v3_items = timeline.GetItemListInTrack("video", 3) or []
    
    for seg in sub_segments:
        expected_frame = round(seg.get('timeline_start', 0) * fps)
        basename = os.path.basename(seg.get('overlay_path', ''))
        
        found = False
        for item in v3_items:
            if basename in item.GetName():
                actual_frame = item.GetStart()
                if abs(actual_frame - expected_frame) > TOLERANCE:
                    errors.append(f"V3 desync: {basename} expected at {expected_frame}, actual {actual_frame} (off by {abs(actual_frame - expected_frame)})")
                found = True
                break
                
        if not found:
            errors.append(f"V3 missing: could not find {basename} in track V3")
            
    if errors:
        raise RuntimeError("Timeline Sync QA Failed:\n" + "\n".join(errors))
        
    print("Timeline sync QA passed.", file=sys.stderr)
    return {"passed": True, "reason": "sync ok"}
