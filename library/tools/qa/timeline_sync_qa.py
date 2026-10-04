import os
import sys

from library.tools.resolve_lock import under_lease

def _connect_resolve():
    """Connect to running DaVinci Resolve instance."""
    try:
        from library.tools.resolve_locale import load_resolve_script
        dvr = load_resolve_script()
        from library.tools.resolve_locale import scriptapp_preserving_locale
        resolve = scriptapp_preserving_locale(dvr, "Resolve")
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


def _caption_track_index(track_plan) -> int:
    """The video row the build laid captions on, off its own track plan.

    The builder places subtitle segments on `track_plan.caption_row()`,
    whose index moves with the material (V2 with no B-roll row above
    it, V3 with one). Reading a hardcoded V3 here failed every build
    whose captions landed elsewhere. Absent plan means an older build
    result: V3 is what those always used, so that stays the fallback
    rather than a refusal.
    """
    try:
        tracks = (track_plan or {}).get("video_tracks", []) or []
        for track in tracks:
            if isinstance(track, dict) and track.get("role") == "captions":
                index = track.get("index")
                if isinstance(index, int) and index >= 1:
                    return index
    except (AttributeError, TypeError):
        pass
    return 3


@under_lease("run timeline sync QA")
def run_timeline_sync_qa(manifest: dict, project_name: str, timeline_name: str, track_plan: dict = None) -> dict:
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

    
    # Check the captions row: wherever the build's own track plan laid
    # them, not a hardcoded V3 (see _caption_track_index).
    sub_overlay_info = manifest.get('subtitle_overlay', {})
    sub_segments = sub_overlay_info.get('segments', [])
    caption_track = _caption_track_index(track_plan)
    caption_items = timeline.GetItemListInTrack("video", caption_track) or []

    errors.extend(_verify_track_placement(
        f"V{caption_track}", sub_segments, caption_items, TOLERANCE,
        expected_frame_of=lambda seg: round(seg.get('timeline_start', 0) * fps),
        name_of=lambda seg: os.path.basename(seg.get('overlay_path', '')),
    ))

    if errors:
        raise RuntimeError("Timeline Sync QA Failed:\n" + "\n".join(errors))
        
    print("Timeline sync QA passed.", file=sys.stderr)
    return {"passed": True, "reason": "sync ok"}
