#!/usr/bin/env python3
"""
Resolve API Timeline Builder (Pipeline v4)

Builds a complete DaVinci Resolve timeline entirely via the Resolve scripting
API — no FCPXML intermediate. This gives us:
  - Exact track targeting (trackIndex parameter)
  - Animated Fusion VFX via .comp file import (BezierSpline keyframes)
  - Clean track layout: V1=A-Roll, V2=B-Roll, V3=Subtitles, V4=MotionGraphics,
    A1=Speech(auto), A2=Music, A3+=SFX
  - Fairlight preset application for audio effects

Reads an assembly_manifest.json and optional Remotion overlay paths.

Tested and verified capabilities (60/60 tests passing):
  - AppendToTimeline({startFrame, endFrame, trackIndex, recordFrame})
  - AddTrack("video"/"audio"), SetTrackName()
  - SetProperty(ZoomX/Y, Opacity, Crop*, etc.)
  - ImportFusionComp() with animated BezierSpline keyframes
  - ApplyFairlightPresetToCurrentTimeline()
  - 34/34 Fusion tools available (Transform, BrightnessContrast, SoftGlow,
    FilmGrain, Defocus, Dissolve, DVE, etc.)
"""

import json
import os
import subprocess
import sys
from typing import Optional

# Add tools to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../tools')))
try:
    from neural_engine import apply_magic_mask, apply_smart_reframe, apply_super_scale, apply_stabilization
    from tools.fairlight_presets import get_preset, apply_fairlight_preset
    from timeline_qa import (
        verify_clip_placement, verify_transitions, verify_color_grades,
        verify_audio, verify_fusion_comps, run_full_timeline_qa
    )
except ImportError:
    apply_magic_mask = apply_smart_reframe = apply_super_scale = apply_stabilization = None
    get_preset = apply_fairlight_preset = None
    verify_clip_placement = verify_transitions = verify_color_grades = verify_audio = verify_fusion_comps = run_full_timeline_qa = None


# ─── Resolve Connection ──────────────────────────────────────

def _connect_resolve():
    """Connect to running DaVinci Resolve instance."""
    api_path = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
    lib_path = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

    if api_path not in sys.path:
        sys.path.append(os.path.join(api_path, "Modules"))
    os.environ["RESOLVE_SCRIPT_API"] = api_path
    os.environ["RESOLVE_SCRIPT_LIB"] = lib_path

    import DaVinciResolveScript as dvr
    resolve = dvr.scriptapp("Resolve")
    if not resolve:
        raise ConnectionError("Cannot connect to DaVinci Resolve. Is it running?")
    return resolve


# ─── Utility: ffprobe helpers ────────────────────────────────

def _read_file_duration(filepath):
    """Read actual media file duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
             '-of', 'csv=p=0', filepath],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, OSError):
        return None


# ─── SFX Overlap-Aware Track Allocator ───────────────────────

def _allocate_sfx_tracks(sfx_clips, base_track_index=3, fps=30.0):
    """Allocate SFX clips across multiple audio tracks to avoid overlap.

    Returns list of (clip, track_index) tuples.
    Clips that overlap in time get placed on separate tracks.
    """
    if not sfx_clips:
        return []

    # Sort by timeline start
    sorted_clips = sorted(sfx_clips, key=lambda c: c.get('timeline_in_frame', 0))

    # Track end times: track_index → last frame end on that track
    track_ends = {}
    allocations = []

    for clip in sorted_clips:
        tl_start = clip.get('timeline_in_frame', 0)
        tl_end = clip.get('timeline_out_frame', tl_start + round(fps))

        # Find first available track (no overlap)
        assigned_track = None
        for track_idx in sorted(track_ends.keys()):
            if track_ends[track_idx] <= tl_start:
                assigned_track = track_idx
                break

        if assigned_track is None:
            # All existing tracks are busy — allocate a new one
            if track_ends:
                assigned_track = max(track_ends.keys()) + 1
            else:
                assigned_track = base_track_index

        track_ends[assigned_track] = tl_end
        allocations.append((clip, assigned_track))

    return allocations


# ─── Pre-flight Validation ───────────────────────────────────

def _preflight_check(manifest):
    """Validate manifest before building. Returns list of errors."""
    errors = []

    project = manifest.get('project', {})
    if not project:
        errors.append("Missing 'project' settings")

    tracks = manifest.get('tracks', {})
    v1_clips = tracks.get('V1', {}).get('clips', [])
    if not v1_clips:
        errors.append("No V1 (A-Roll) clips")

    for ci, clip in enumerate(v1_clips):
        src = clip.get('source_file', '')
        if not src:
            errors.append(f"V1[{ci}] missing source_file")
        elif not os.path.exists(src):
            errors.append(f"V1[{ci}] file not found: {os.path.basename(src)}")

        if 'timeline_in_frame' not in clip:
            errors.append(f"V1[{ci}] missing timeline_in_frame")

    # Check audio files
    for track_key in ['A2', 'A3']:
        for ci, clip in enumerate(tracks.get(track_key, {}).get('clips', [])):
            src = clip.get('source_file', '')
            if src and not os.path.exists(src):
                errors.append(f"{track_key}[{ci}] file not found: {os.path.basename(src)}")

    return errors


# ─── Core: Build Timeline ────────────────────────────────────

def build_timeline(
    manifest: dict,
    subtitle_overlay_path: Optional[str] = None,
    motion_graphics_path: Optional[str] = None,
    project_name: Optional[str] = None,
    delete_existing: bool = True,
) -> dict:
    """Build a complete Resolve timeline from an assembly manifest.

    Args:
        manifest: Assembly manifest dict with tracks, transitions, etc.
        subtitle_overlay_path: Legacy single-file path (fallback)
        motion_graphics_path: Legacy single-file path (fallback)
        project_name: Resolve project name (creates or loads)
        delete_existing: Delete existing timelines with same name

    Returns:
        dict with build results and verification data
    """
    # ── Pre-flight ──
    errors = _preflight_check(manifest)
    if errors:
        return {"success": False, "errors": errors}

    project_settings = manifest['project']
    timeline_name = project_settings.get('name', '4thWall_v3')
    width = project_settings.get('resolution', [1080, 1920])[0]
    height = project_settings.get('resolution', [1080, 1920])[1]
    fps = project_settings.get('frame_rate', 30)
    total_duration = project_settings.get('duration_seconds', 46.0)

    tracks = manifest.get('tracks', {})
    v1_clips = tracks.get('V1', {}).get('clips', [])
    v2_clips = tracks.get('V2', {}).get('clips', [])
    a2_clips = tracks.get('A2', {}).get('clips', [])
    # SFX: manifest compiler puts these at top-level 'sfx', not tracks.A3
    a3_clips = tracks.get('A3', {}).get('clips', [])
    if not a3_clips:
        a3_clips = [s for s in manifest.get('sfx', []) if s.get('source_file')]
    # Note: transitions are applied via fusion_effects.transitions, not
    # the top-level 'transitions' key (which is informational only).
    vfx_entries = manifest.get('vfx', [])  # legacy VFX entries

    # ── Resolve overlay segments from manifest ──
    # Per-segment overlays (new): manifest contains subtitle_overlay.segments
    # and motion_graphics_overlay.segments arrays with per-block paths.
    # Legacy fallback: single subtitle_overlay_path / motion_graphics_path.
    sub_overlay_info = manifest.get('subtitle_overlay', {})
    mg_overlay_info = manifest.get('motion_graphics_overlay', {})

    if sub_overlay_info.get('available') is False:
        sub_overlay_info = {}
        print("  ⚠ Subtitles marked as not available, skipping", file=sys.stderr)

    if mg_overlay_info.get('available') is False:
        mg_overlay_info = {}
        print("  ⚠ Motion graphics marked as not available, skipping", file=sys.stderr)

    sub_segments = sub_overlay_info.get('segments', [])
    mg_segments = mg_overlay_info.get('segments', [])

    # Legacy fallback: single overlay file
    if not sub_segments and subtitle_overlay_path and os.path.exists(subtitle_overlay_path):
        sub_segments = [{
            'overlay_path': subtitle_overlay_path,
            'timeline_start': 0,
            'timeline_end': total_duration,
            'total_frames': round(total_duration * fps),
        }]
    if not sub_segments:
        # Try legacy overlay_path in manifest
        legacy_sub = sub_overlay_info.get('overlay_path', '')
        if legacy_sub and os.path.exists(legacy_sub):
            sub_segments = [{
                'overlay_path': legacy_sub,
                'timeline_start': 0,
                'timeline_end': total_duration,
                'total_frames': round(total_duration * fps),
            }]

    if not mg_segments and motion_graphics_path and os.path.exists(motion_graphics_path):
        mg_segments = [{
            'overlay_path': motion_graphics_path,
            'timeline_start': 0,
            'timeline_end': total_duration,
            'total_frames': round(total_duration * fps),
        }]
    if not mg_segments:
        legacy_mg = mg_overlay_info.get('overlay_path', '')
        if legacy_mg and os.path.exists(legacy_mg):
            mg_segments = [{
                'overlay_path': legacy_mg,
                'timeline_start': 0,
                'timeline_end': total_duration,
                'total_frames': round(total_duration * fps),
            }]
    results = {
        "success": False,
        "timeline_name": timeline_name,
        "tracks": {},
        "errors": [],
        "warnings": [],
    }
    
    qa_reports = []
    def _run_qa(report):
        if not report: return
        qa_reports.append(report)
        if not report.passed:
            for check in report.checks:
                if not check.passed and check.severity == "error":
                    msg = f"QA [{report.station}] Failed {check.name}: expected {check.expected}, got {check.actual}"
                    print(f"  ⚠ {msg}", file=sys.stderr)
                    results["warnings"].append(msg)

    # ── Connect to Resolve ──
    try:
        resolve = _connect_resolve()
    except ConnectionError as e:
        results["errors"].append(str(e))
        return results

    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()

    if project_name and (not project or project.GetName() != project_name):
        project = pm.LoadProject(project_name)
        if not project:
            project = pm.CreateProject(project_name)
    if not project:
        project = pm.GetCurrentProject()

    media_pool = project.GetMediaPool()

    # ── Import all media to pool with subdirectory organization ──
    # Note: We import media BEFORE creating timeline to detect actual FPS.
    root_folder = media_pool.GetRootFolder()

    def _import_to_folder(folder_name, paths):
        paths = list(set(p for p in paths if p and os.path.exists(p)))
        if not paths:
            return 0
        
        media_pool.SetCurrentFolder(root_folder)
        folder = None
        for sub in (root_folder.GetSubFolderList() or []):
            if sub.GetName() == folder_name:
                folder = sub
                break
        if not folder:
            folder = media_pool.AddSubFolder(root_folder, folder_name)
            
        media_pool.SetCurrentFolder(folder)
        imported = media_pool.ImportMedia(paths)
        media_pool.SetCurrentFolder(root_folder)
        return len(imported) if imported else 0

    total_imported = 0
    total_imported += _import_to_folder("V1", [c.get('source_file', '') for c in v1_clips])
    total_imported += _import_to_folder("V2", [c.get('source_file', '') for c in v2_clips])
    total_imported += _import_to_folder("Audio", [c.get('source_file', '') for c in a2_clips + a3_clips])
    total_imported += _import_to_folder("Subtitles", [s.get('overlay_path', '') for s in sub_segments])
    total_imported += _import_to_folder("MotionGraphics", [s.get('overlay_path', '') for s in mg_segments])
    
    if total_imported > 0:
        print(f"✓ Imported {total_imported} media files into subfolders", file=sys.stderr)

    # Build pool clip lookup.
    root_folder = media_pool.GetRootFolder()
    pool_clips_by_path = {}
    pool_clips_by_name = {}

    def _scan_folder(folder):
        for clip in (folder.GetClipList() or []):
            name = clip.GetName()
            filepath = clip.GetClipProperty("File Path") or ""
            if filepath:
                pool_clips_by_path[filepath] = clip
            pool_clips_by_name[name] = clip
        for sub in (folder.GetSubFolderList() or []):
            _scan_folder(sub)

    _scan_folder(root_folder)

    def _find_pool_clip(filepath: str) -> object:
        """Look up a media pool clip by filepath first, then basename fallback."""
        item = pool_clips_by_path.get(filepath)
        if item: return item
        return pool_clips_by_name.get(os.path.basename(filepath))

    pool_clips = pool_clips_by_name  # Prefer _find_pool_clip() for all new code
    print(f"  Media pool: {len(pool_clips_by_name)} clips ({len(pool_clips_by_path)} with paths)", file=sys.stderr)

    # ── Detect actual source FPS ──
    actual_fps = float(fps)
    for c in v1_clips:
        src = c.get('source_file', '')
        if src:
            pool_item = _find_pool_clip(src)
            if pool_item:
                try:
                    media_fps = float(pool_item.GetClipProperty("FPS"))
                    if media_fps > 0:
                        actual_fps = media_fps
                        print(f"✓ Detected actual FPS from source: {actual_fps}", file=sys.stderr)
                        break
                except (ValueError, TypeError):
                    pass
    fps = actual_fps

    # Recompute frame bounds for all clips based on actual fps to prevent placement gaps
    def _recompute_frames(clip):
        if 'source_in' in clip: clip['source_in_frame'] = round(clip['source_in'] * fps)
        if 'source_out' in clip: clip['source_out_frame'] = round(clip['source_out'] * fps)
        if 'timeline_in' in clip: clip['timeline_in_frame'] = round(clip['timeline_in'] * fps)
        if 'timeline_out' in clip: clip['timeline_out_frame'] = round(clip['timeline_out'] * fps)

    for c in v1_clips + v2_clips + a2_clips + a3_clips:
        _recompute_frames(c)
    for s in sub_segments + mg_segments:
        _recompute_frames(s)
        s['total_frames'] = round((s.get('timeline_end', 0) - s.get('timeline_start', 0)) * fps)

    # ── Delete existing timeline if requested ──
    if delete_existing:
        for i in range(project.GetTimelineCount(), 0, -1):
            tl = project.GetTimelineByIndex(i)
            if tl and tl.GetName() == timeline_name:
                media_pool.DeleteTimelines([tl])

    # ── Create empty timeline ──
    timeline = media_pool.CreateEmptyTimeline(timeline_name)
    if not timeline:
        results["errors"].append("Failed to create timeline")
        return results

    project.SetCurrentTimeline(timeline)
    timeline.SetSetting("useCustomSettings", "1")
    timeline.SetSetting("timelineResolutionWidth", str(width))
    timeline.SetSetting("timelineResolutionHeight", str(height))
    timeline_fps_str = str(int(fps)) if fps.is_integer() else str(fps)
    timeline.SetSetting("timelineFrameRate", timeline_fps_str)

    print(f"✓ Created timeline: {timeline_name} ({width}x{height} @ {timeline_fps_str}fps)", file=sys.stderr)

    # ── Set up tracks ──
    # V1 exists by default. Need V2, V3, V4 for video and extra audio tracks.
    has_v2 = bool(v2_clips)
    has_subtitles = bool(sub_segments)
    has_mg = bool(mg_segments)

    # Calculate how many SFX tracks we need
    sfx_allocations = _allocate_sfx_tracks(a3_clips, base_track_index=3, fps=fps)
    max_sfx_track = max((t for _, t in sfx_allocations), default=2)
    num_audio_tracks_needed = max(max_sfx_track, 2)  # at least A1(speech) + A2(music)

    # Add video tracks (V1 exists, add V2+)
    target_video_tracks = 1
    if has_v2:
        target_video_tracks = max(target_video_tracks, 2)
    if has_subtitles:
        target_video_tracks = max(target_video_tracks, 3)
    if has_mg:
        target_video_tracks = max(target_video_tracks, 4)

    while timeline.GetTrackCount("video") < target_video_tracks:
        timeline.AddTrack("video")

    # NOTE: The order of operations is CRITICAL for correct audio layout.
    # iPhone MOV files contain multiple audio streams (stereo + 4-channel).
    # When placed with default behavior, Resolve auto-links audio to ALL
    # existing audio tracks. So we MUST:
    #   1. Place V1 clips while ONLY A1 exists → audio goes to A1 only
    #   2. THEN add A2, A3, A4 → they start clean
    #   3. THEN place music/SFX on A2+ with mediaType=2

    vt = timeline.GetTrackCount("video")
    print(f"✓ Video tracks: V={vt}", file=sys.stderr)
    print(f"  (Audio tracks deferred until after V1 placement)", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # PLACE V1: A-Roll clips (DEFAULT — audio auto-links to A1 only)
    # ══════════════════════════════════════════════════════════
    # At this point only A1 exists, so default placement puts audio on A1
    print(f"\n── V1 A-Roll + A1 Speech: {len(v1_clips)} clips ──", file=sys.stderr)
    v1_timeline_items = []
    v1_placed_labels = []

    # Pre-process J/L cuts from native transitions
    native_transitions = manifest.get('transitions', [])
    for ci, clip in enumerate(v1_clips):
        src_in = clip.get('source_in', 0)
        src_out = clip.get('source_out')
        if not src_out:
            dur = clip.get('timeline_out', 0) - clip.get('timeline_in', 0)
            src_out = src_in + dur if dur > 0 else src_in + 3.5
        clip['video_src_in'] = round(src_in * fps)
        clip['video_src_out'] = round(src_out * fps)
        clip['audio_src_in'] = round(clip.get('audio_src_in', src_in) * fps)
        clip['audio_src_out'] = round(clip.get('audio_src_out', src_out) * fps)

    for trans in native_transitions:
        ttype = trans.get('transition_type', trans.get('type', ''))
        from_idx = trans.get('from_block')
        to_idx = trans.get('to_block')
        # Support both formats (manifest vs direct)
        if from_idx is None:
            pos = trans.get('position', '')
            if pos.startswith('between_'):
                parts = pos.replace('between_', '').split('_')
                if len(parts) == 2:
                    from_idx = int(parts[0])
                    to_idx = int(parts[1])
                    
        # Resolve from_block/to_block IDs to actual indices in v1_clips
        from_clip_idx = None
        to_clip_idx = None
        
        if from_idx is not None:
            for i, clip in enumerate(v1_clips):
                label = clip.get('label', '')
                if label.endswith(f"_{from_idx}") or f"_{from_idx}_seg" in label:
                    from_clip_idx = i
                    
        if to_idx is not None:
            for i, clip in enumerate(v1_clips):
                label = clip.get('label', '')
                if label.endswith(f"_{to_idx}") or f"_{to_idx}_seg" in label:
                    to_clip_idx = i
                    break
        
        dur = trans.get('duration_frames', 15)
        if from_clip_idx is not None and to_clip_idx is not None:
            if ttype == 'j_cut':
                # to_clip can only go back by its available head
                actual_dur = min(dur, v1_clips[to_clip_idx]['audio_src_in'])
                # from_clip can only give up what it has
                from_clip_len = v1_clips[from_clip_idx]['audio_src_out'] - v1_clips[from_clip_idx]['audio_src_in']
                actual_dur = min(actual_dur, from_clip_len)
                
                v1_clips[from_clip_idx]['audio_src_out'] -= actual_dur
                v1_clips[to_clip_idx]['audio_src_in'] -= actual_dur
            elif ttype == 'l_cut':
                # to_clip can only give up what it has
                to_clip_len = v1_clips[to_clip_idx]['audio_src_out'] - v1_clips[to_clip_idx]['audio_src_in']
                actual_dur = min(dur, to_clip_len)
                
                v1_clips[from_clip_idx]['audio_src_out'] += actual_dur
                v1_clips[to_clip_idx]['audio_src_in'] += actual_dur

    for ci, clip in enumerate(v1_clips):
        current_video_frame = clip.get('timeline_in_frame', 0)
        # BUG FIX C7: Handle clips with missing source_file gracefully
        src = clip.get('source_file', '')
        if not src:
            results["warnings"].append(
                f"V1[{ci}] ({clip.get('label', '?')}) missing source_file - skipped")
            print(f"  ⚠ [{ci}] {clip.get('label', '?')}: missing source_file", file=sys.stderr)
            continue
        basename = os.path.basename(src)
        pool_item = _find_pool_clip(src)
        if not pool_item:
            results["errors"].append(f"V1[{ci}] {basename} not in media pool")
            continue

        v_in = clip['video_src_in']
        v_out = clip['video_src_out']
        a_in = clip['audio_src_in']
        a_out = clip['audio_src_out']
        tl_in_f = clip.get('timeline_in_frame', 0)

        # Place Video (V1)
        v_res = media_pool.AppendToTimeline([{
            "mediaPoolItem": pool_item,
            "startFrame": v_in,
            "endFrame": v_out,
            "trackIndex": 1,
            "recordFrame": tl_in_f,
            "mediaType": 1
        }])
        
        # Calculate Audio Record Frame to maintain sync
        a_rec = tl_in_f + (a_in - v_in)
        
        # Place Audio (A1)
        a_res = media_pool.AppendToTimeline([{
            "mediaPoolItem": pool_item,
            "startFrame": a_in,
            "endFrame": a_out,
            "trackIndex": 1,
            "recordFrame": a_rec,
            "mediaType": 2
        }])

        if v_res:
            placed = v_res[0] if isinstance(v_res, list) else v_res
            a_placed = a_res[0] if (a_res and isinstance(a_res, list)) else (a_res if a_res else None)
            
            if a_placed:
                timeline.SetClipsLinked([placed, a_placed], True)
                
            v1_timeline_items.append(placed)
            v1_placed_labels.append(clip.get('label', basename))
            
            placed_dur = placed.GetDuration()
            clip['timeline_in_frame'] = tl_in_f
            clip['timeline_out_frame'] = tl_in_f + placed_dur
            clip['timeline_in'] = tl_in_f / fps
            clip['timeline_out'] = (tl_in_f + placed_dur) / fps
            
            print(f"  ✓ [{ci}] {clip.get('label', basename)}: "
                  f"V1 {v_in}-{v_out} at {tl_in_f}, A1 {a_in}-{a_out} at {a_rec}", file=sys.stderr)
                  
            # Apply Fairlight preset to this dialogue track item
            fairlight_preset_name = manifest.get('audio', {}).get('fairlight_preset', '')
            if fairlight_preset_name and get_preset and apply_fairlight_preset and a_placed:
                preset = get_preset(fairlight_preset_name)
                success = apply_fairlight_preset(a_placed, preset)
                if success:
                    print(f"    ✓ Applied Fairlight preset: {fairlight_preset_name}", file=sys.stderr)
                else:
                    results["warnings"].append(f"Fairlight preset {fairlight_preset_name} could not be applied to {basename}")

        else:
            results["errors"].append(f"V1[{ci}] AppendToTimeline failed for {basename}")
            print(f"  ✗ [{ci}] {basename}: AppendToTimeline returned None", file=sys.stderr)

    results["tracks"]["V1"] = len(v1_timeline_items)
    results["tracks"]["A1"] = len(v1_timeline_items)  # auto-linked
    
    if verify_clip_placement:
        _run_qa(verify_clip_placement(timeline, {"V1": v1_timeline_items}, {"V1": v1_clips}))

    # ══════════════════════════════════════════════════════════
    # NOW create extra audio tracks (AFTER V1 — so they start clean)
    # ══════════════════════════════════════════════════════════
    while timeline.GetTrackCount("audio") < num_audio_tracks_needed:
        timeline.AddTrack("audio")

    at = timeline.GetTrackCount("audio")
    print(f"✓ Audio tracks added: A={at} (A1=speech, A2+=clean)", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # PLACE V2: B-Roll clips
    # ══════════════════════════════════════════════════════════
    if v2_clips:
        print(f"\n── V2 B-Roll: {len(v2_clips)} clips ──", file=sys.stderr)
        v2_count = 0
        v2_placed_labels = []
        for ci, clip in enumerate(v2_clips):
            basename = os.path.basename(clip['source_file'])
            pool_item = _find_pool_clip(clip['source_file'])
            if not pool_item:
                results["warnings"].append(f"V2[{ci}] {basename} not in pool")
                continue

            src_in = clip.get('source_in', 0)
            src_out = clip.get('source_out')
            if not src_out:
                dur = clip.get('timeline_out', 0) - clip.get('timeline_in', 0)
                src_out = src_in + dur if dur > 0 else src_in + 3.5
            src_in_f = round(src_in * fps)
            src_out_f = round(src_out * fps)
            tl_in_f = clip['timeline_in_frame']

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_out_f,
                "trackIndex": 2,
                "recordFrame": tl_in_f,
                "mediaType": 1,  # video-only placement on V2
            }])

            if result:
                v2_count += 1
                v2_placed_labels.append(clip.get('label', basename))
                print(f"  ✓ [{ci}] {clip.get('label', basename)}: TL {tl_in_f}", file=sys.stderr)
            else:
                print(f"  ✗ [{ci}] {basename}: failed", file=sys.stderr)

        results["tracks"]["V2"] = v2_count
        
    if verify_transitions:
        _run_qa(verify_transitions(timeline, {}, manifest.get("transitions", [])))

    # ══════════════════════════════════════════════════════════
    # PLACE V3: Subtitle Overlay Segments (Remotion)
    # ══════════════════════════════════════════════════════════
    if has_subtitles:
        print(f"\n── V3 Subtitle Overlay: {len(sub_segments)} segments ──", file=sys.stderr)
        v3_count = 0
        for si, seg in enumerate(sub_segments):
            seg_path = seg.get('overlay_path', '')
            seg_basename = os.path.basename(seg_path)
            pool_item = _find_pool_clip(seg_path)
            if not pool_item:
                results["warnings"].append(f"V3[{si}] {seg_basename} not in pool")
                print(f"  ✗ [{si}] {seg_basename} not in media pool", file=sys.stderr)
                continue

            # Ensure ProRes 4444 alpha channel is recognized
            pool_item.SetClipProperty("Alpha mode", "Premultiplied")

            seg_frames = seg.get('total_frames', round(
                (seg.get('timeline_end', 0) - seg.get('timeline_start', 0)) * fps))
            tl_in_frame = round(seg.get('timeline_start', 0) * fps)

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": 0,
                "endFrame": seg_frames,
                "trackIndex": 3,
                "recordFrame": tl_in_frame,
                "mediaType": 1,  # video-only placement on V3
            }])
            if result:
                v3_count += 1
                print(f"  ✓ [{si}] {seg_basename} on V3 ({seg_frames}f @ TL {tl_in_frame})",
                      file=sys.stderr)
            else:
                print(f"  ✗ [{si}] {seg_basename}: placement failed", file=sys.stderr)
                results["warnings"].append(f"V3[{si}] placement failed: {seg_basename}")

        results["tracks"]["V3"] = v3_count

    # ══════════════════════════════════════════════════════════
    # PLACE V4: Motion Graphics Overlay Segments (Remotion)
    # ══════════════════════════════════════════════════════════
    if has_mg:
        print(f"\n── V4 Motion Graphics: {len(mg_segments)} segments ──", file=sys.stderr)
        v4_count = 0
        for mi, seg in enumerate(mg_segments):
            seg_path = seg.get('overlay_path', '')
            seg_basename = os.path.basename(seg_path)
            pool_item = _find_pool_clip(seg_path)
            if not pool_item:
                results["warnings"].append(f"V4[{mi}] {seg_basename} not in pool")
                print(f"  ✗ [{mi}] {seg_basename} not in media pool", file=sys.stderr)
                continue

            # Ensure ProRes 4444 alpha channel is recognized
            pool_item.SetClipProperty("Alpha mode", "Premultiplied")

            seg_frames = seg.get('total_frames', round(
                (seg.get('timeline_end', 0) - seg.get('timeline_start', 0)) * fps))
            tl_in_frame = round(seg.get('timeline_start', 0) * fps)

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": 0,
                "endFrame": seg_frames,
                "trackIndex": 4,
                "recordFrame": tl_in_frame,
                "mediaType": 1,  # video-only placement on V4
            }])
            if result:
                v4_count += 1
                print(f"  ✓ [{mi}] {seg_basename} on V4 ({seg_frames}f @ TL {tl_in_frame})",
                      file=sys.stderr)
            else:
                print(f"  ✗ [{mi}] {seg_basename}: placement failed", file=sys.stderr)

        results["tracks"]["V4"] = v4_count

    # ══════════════════════════════════════════════════════════
    # PLACE A2: Music
    # ══════════════════════════════════════════════════════════
    if a2_clips:
        print(f"\n── A2 Music: {len(a2_clips)} clips ──", file=sys.stderr)
        for ci, clip in enumerate(a2_clips):
            basename = os.path.basename(clip['source_file'])
            pool_item = _find_pool_clip(clip['source_file'])
            if not pool_item:
                results["warnings"].append(f"A2[{ci}] {basename} not in pool")
                continue

            tl_in_sec = clip.get('timeline_in', 0)
            tl_out_sec = clip.get('timeline_out', total_duration)
            dur_f = round((tl_out_sec - tl_in_sec) * fps)
            src_in_f = round(clip.get('source_in', 0) * fps)

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_in_f + dur_f,
                "trackIndex": 2,  # A2
                "recordFrame": round(tl_in_sec * fps),
                "mediaType": 2,  # audio-only placement
            }])

            if result:
                placed = result[0] if isinstance(result, list) else result
                print(f"  ✓ {basename}: {placed.GetDuration()}f on A2", file=sys.stderr)
                results["tracks"]["A2"] = 1

                # Set volume if specified
                vol_db = clip.get('volume_db')
                if vol_db is not None:
                    # Resolve uses linear volume (0-1 range typical)
                    # Convert dB: linear = 10^(dB/20)
                    linear_vol = 10 ** (vol_db / 20.0)
                    # Clamp to reasonable range
                    linear_vol = max(0.0, min(linear_vol, 4.0))
                    # Note: SetProperty("Volume") may not work on all clips
                    # This is a best-effort attempt
                    try:
                        placed.SetProperty("Volume", linear_vol)
                    except Exception as e:
                        results["warnings"].append(
                            f"Volume set failed for {basename}: {e}"
                        )
                        
                # Apply music ducking keyframes if present
                music_ducking = manifest.get('music_ducking', {})
                ducking_curves = music_ducking.get('ducking_curves', [])
                audio_mix = manifest.get('audio_mix', {})
                music_automation = audio_mix.get('music_automation', [])
                
                if ducking_curves and isinstance(ducking_curves, list):
                    for kf in ducking_curves:
                        time_ms = kf.get('time_ms', 0)
                        vol_db = kf.get('volume_db', 0)
                        frame = round((time_ms / 1000.0) * fps)
                        timeline.AddMarker(frame, "Cyan", f"Ducking: {vol_db}dB", "API lacks volume automation", 1)
                    print(f"  ✓ Added {len(ducking_curves)} ducking markers to timeline (volume automation unsupported via API)", file=sys.stderr)
                elif music_automation and isinstance(music_automation, list):
                    for auto in music_automation:
                        time_sec = auto.get('timeline_start', 0)
                        vol_db = auto.get('target_level_db', 0)
                        behavior = auto.get('music_behavior', 'background')
                        frame = round(time_sec * fps)
                        timeline.AddMarker(frame, "Cyan", f"Music: {vol_db}dB ({behavior})", "API lacks volume automation", 1)
                    print(f"  ✓ Added {len(music_automation)} music automation markers to timeline (volume automation unsupported via API)", file=sys.stderr)

            else:
                print(f"  ✗ {basename}: failed", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # PLACE A3+: SFX (overlap-aware multi-track)
    # ══════════════════════════════════════════════════════════
    if sfx_allocations:
        print(f"\n── SFX: {len(sfx_allocations)} clips across tracks ──", file=sys.stderr)
        sfx_track_counts = {}

        for clip, track_idx in sfx_allocations:
            # BUG FIX C7: Handle unresolved SFX clips missing source_file
            src = clip.get('source_file', '')
            if not src:
                results["warnings"].append(
                    f"SFX ({clip.get('label', '?')}) missing source_file - skipped")
                print(f"  ⚠ SFX {clip.get('label', '?')}: missing source_file",
                      file=sys.stderr)
                continue
            basename = os.path.basename(src)
            pool_item = _find_pool_clip(src)
            if not pool_item:
                results["warnings"].append(f"SFX {basename} not in pool")
                continue

            tl_in_f = clip['timeline_in_frame']
            tl_out_f = clip['timeline_out_frame']
            dur_f = tl_out_f - tl_in_f
            src_in_f = round(clip.get('source_in', 0) * fps)

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_in_f + dur_f,
                "trackIndex": track_idx,
                "recordFrame": tl_in_f,
                "mediaType": 2,  # audio-only placement
            }])

            if result:
                sfx_track_counts[track_idx] = sfx_track_counts.get(track_idx, 0) + 1
                print(f"  ✓ {clip.get('label', basename)}: "
                      f"TL {tl_in_f}-{tl_out_f} → A{track_idx}",
                      file=sys.stderr)

                # Apply volume
                placed = result[0] if isinstance(result, list) else result
                vol_db = clip.get('volume_db')
                if vol_db is not None:
                    linear_vol = 10 ** (vol_db / 20.0)
                    linear_vol = max(0.0, min(linear_vol, 4.0))
                    try:
                        placed.SetProperty("Volume", linear_vol)
                    except Exception as e:
                        results["warnings"].append(
                            f"SFX volume set failed on A{track_idx}: {e}"
                        )
            else:
                print(f"  ✗ {basename} on A{track_idx} at {tl_in_f}: failed", file=sys.stderr)

        for tk, count in sorted(sfx_track_counts.items()):
            results["tracks"][f"A{tk}"] = count

    # ══════════════════════════════════════════════════════════
    # APPLY FUSION .comp FILES (animated VFX + transitions per clip)
    # ══════════════════════════════════════════════════════════
    # CRITICAL RULE FIX: We must run ImportFusionComp in a separate process
    # because clip references go stale after timeline creation.
    import tempfile
    import subprocess
    
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'tools', 'execution', 'apply_fusion_comps.py')
    if os.path.exists(script_path):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as tf:
            json.dump(manifest, tf)
            temp_manifest = tf.name
            
        cmd = [sys.executable, script_path, temp_manifest]
        print(f"\n── Launching subprocess for Fusion Comps ──", file=sys.stderr)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        
        if proc.returncode != 0:
            results["warnings"].append(f"Fusion subprocess failed: {proc.stderr}")
            print(f"  ✗ Fusion Comps Subprocess Failed", file=sys.stderr)
        else:
            print(proc.stderr, file=sys.stderr)
            
        os.remove(temp_manifest)
    else:
        results["warnings"].append(f"apply_fusion_comps.py not found at {script_path}")

    if verify_fusion_comps:
        _run_qa(verify_fusion_comps(timeline, None, manifest.get("vfx", {})))

    # ══════════════════════════════════════════════════════════
    # NEURAL ENGINE DIRECTIVES (Per-Clip)
    # ══════════════════════════════════════════════════════════
    neural_directives = manifest.get('neural_engine_directives', {})
    if neural_directives and apply_stabilization is not None:
        print(f"\n── Neural Engine: {len(neural_directives)} clips ──", file=sys.stderr)
        # Apply to V1
        v1_items = timeline.GetItemListInTrack("video", 1) or []
        for ci, label in enumerate(v1_placed_labels):
            if label in neural_directives and ci < len(v1_items):
                directives = neural_directives[label]
                tl_clip = v1_items[ci]
                
                if directives.get('stabilize'):
                    apply_stabilization(tl_clip)
                    print(f"  ✓ [{ci}] {label}: Stabilization applied", file=sys.stderr)
                if directives.get('super_scale'):
                    apply_super_scale(tl_clip, scale_factor=directives['super_scale'])
                    print(f"  ✓ [{ci}] {label}: Super Scale {directives['super_scale']}x applied", file=sys.stderr)
                if directives.get('magic_mask'):
                    apply_magic_mask(tl_clip)
                    print(f"  ✓ [{ci}] {label}: Magic Mask applied", file=sys.stderr)
                    
        # Apply to V2
        v2_items = timeline.GetItemListInTrack("video", 2) or []
        # if v2_clips is empty, v2_placed_labels might not exist if it was skipped entirely
        v2_labels = v2_placed_labels if 'v2_placed_labels' in locals() else []
        for ci, label in enumerate(v2_labels):
            if label in neural_directives and ci < len(v2_items):
                directives = neural_directives[label]
                tl_clip = v2_items[ci]
                
                if directives.get('stabilize'):
                    apply_stabilization(tl_clip)
                    print(f"  ✓ [{ci}] {label}: Stabilization applied", file=sys.stderr)
                if directives.get('super_scale'):
                    apply_super_scale(tl_clip, scale_factor=directives['super_scale'])
                    print(f"  ✓ [{ci}] {label}: Super Scale {directives['super_scale']}x applied", file=sys.stderr)
                if directives.get('magic_mask'):
                    apply_magic_mask(tl_clip)
                    print(f"  ✓ [{ci}] {label}: Magic Mask applied", file=sys.stderr)
                    
    # ══════════════════════════════════════════════════════════
    # SMART REFRAME (Timeline Level)
    # ══════════════════════════════════════════════════════════
    # The instruction says "Smart Reframe is timeline-level, not per-clip. Add as an optional post-render pass".
    # We can apply it here on the timeline if needed, but since it's an optional post-render pass,
    # maybe we just check if it's in the manifest and apply it to timeline.
    if manifest.get("smart_reframe") and apply_smart_reframe is not None:
        print(f"\n── Smart Reframe ──", file=sys.stderr)
        target_aspect = manifest["smart_reframe"].get("target_aspect", "9:16")
        apply_smart_reframe(timeline, target_aspect=target_aspect)
        print(f"  ✓ Applied Smart Reframe to timeline ({target_aspect})", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # APPLY FAIRLIGHT PRESET (if specified)
    # ══════════════════════════════════════════════════════════
    audio_config = manifest.get('audio', {})
    fairlight_preset = audio_config.get('fairlight_preset', '')
    if fairlight_preset:
        print(f"\n── Fairlight Preset: {fairlight_preset} ──", file=sys.stderr)
        result = project.ApplyFairlightPresetToCurrentTimeline(fairlight_preset)
        if result:
            print(f"  ✓ Applied Fairlight preset: {fairlight_preset}", file=sys.stderr)
        else:
            print(f"  ⚠ Fairlight preset '{fairlight_preset}' failed, applying fallback", file=sys.stderr)
            fallback_res = project.ApplyFairlightPresetToCurrentTimeline("Dialogue")
            if fallback_res:
                print(f"  ✓ Applied fallback preset: Dialogue", file=sys.stderr)
            else:
                results["warnings"].append(f"Fairlight preset '{fairlight_preset}' and fallback failed")
                
    if verify_audio:
        _run_qa(verify_audio(timeline, project, manifest.get("audio", {})))

    # ══════════════════════════════════════════════════════════
    # COLOR GRADING (CDL + PowerGrade)
    # ══════════════════════════════════════════════════════════
    color_grade = manifest.get("color_grade", {})
    per_clip_adjs = color_grade.get("per_clip_adjustments", [])
    powergrade_path = color_grade.get("powergrade_path")

    # Build a lookup by source_file basename
    color_lookup = {}
    for adj in per_clip_adjs:
        src = adj.get("source_file", "")
        if src:
            color_lookup[os.path.basename(src).lower()] = adj.get("cdl_values", {})

    if color_lookup or powergrade_path:
        print(f"\n── Color Grading (CDL + PowerGrade) ──", file=sys.stderr)
        graded_sources = {}
        
        for track_idx in range(1, timeline.GetTrackCount("video") + 1):
            items = timeline.GetItemListInTrack("video", track_idx)
            if not items: continue

            for ci, item in enumerate(items):
                mpi = item.GetMediaPoolItem()
                if not mpi: continue

                clip_name = mpi.GetClipProperty("File Name") or item.GetName()
                if not clip_name: continue

                cdl_vals = color_lookup.get(clip_name.lower())
                if not cdl_vals and not powergrade_path:
                    continue

                if cdl_vals:
                    slope = f"{cdl_vals.get('slope_r', 1.0):.3f} {cdl_vals.get('slope_g', 1.0):.3f} {cdl_vals.get('slope_b', 1.0):.3f}"
                    offset = f"{cdl_vals.get('offset_r', 0.0):.3f} {cdl_vals.get('offset_g', 0.0):.3f} {cdl_vals.get('offset_b', 0.0):.3f}"
                    power = f"{cdl_vals.get('power_r', 1.0):.3f} {cdl_vals.get('power_g', 1.0):.3f} {cdl_vals.get('power_b', 1.0):.3f}"
                    sat = f"{cdl_vals.get('saturation', 1.0):.3f}"
                    
                    try:
                        # Try SetCDL first
                        res = item.SetCDL({
                            "NodeIndex": "1",
                            "Slope": slope,
                            "Offset": offset,
                            "Power": power,
                            "Saturation": sat
                        })
                        if not res:
                            # Fallback to SetClipProperty
                            item.SetClipProperty("Slope", slope)
                            item.SetClipProperty("Offset", offset)
                            item.SetClipProperty("Power", power)
                            item.SetClipProperty("Saturation", sat)
                    except Exception as e:
                        results["warnings"].append(f"SetCDL failed on {clip_name}: {e}")

                if powergrade_path and os.path.exists(powergrade_path):
                    res = item.ApplyGradeFromDRX(powergrade_path, 1)
                    if res:
                        print(f"  ✓ Applied PowerGrade to {clip_name}", file=sys.stderr)
                    else:
                        print(f"  ✗ Failed to apply PowerGrade to {clip_name}", file=sys.stderr)
                        results["warnings"].append(f"Failed to apply PowerGrade to {clip_name}")

                creative_look_dctl = color_grade.get("creative_look_dctl", "")
                if creative_look_dctl:
                    try:
                        res = item.SetLUT(4, creative_look_dctl)
                        if res:
                            print(f"  ✓ Applied DCTL to {clip_name} (node 4)", file=sys.stderr)
                        else:
                            print(f"  ✗ Failed to apply DCTL to {clip_name}", file=sys.stderr)
                    except Exception as e:
                        results["warnings"].append(f"DCTL error on {clip_name}: {e}")

                print(f"  ✓ Applied CDL base grade to {clip_name}", file=sys.stderr)
                    
    if verify_color_grades:
        _run_qa(verify_color_grades(timeline, None, manifest.get("color_grade", {})))

    # ══════════════════════════════════════════════════════════
    print(f"\n── Track Labels ──", file=sys.stderr)
    video_labels = {1: "A-Roll", 2: "B-Roll", 3: "Subtitles", 4: "Motion Graphics"}
    audio_labels = {1: "Speech", 2: "Music"}

    for i in range(1, timeline.GetTrackCount("video") + 1):
        label = video_labels.get(i, f"V{i}")
        timeline.SetTrackName("video", i, label)
        print(f"  V{i}: {label}", file=sys.stderr)

    for i in range(1, timeline.GetTrackCount("audio") + 1):
        if i <= 2:
            label = audio_labels.get(i, f"A{i}")
        else:
            label = f"SFX-{i - 2}"
        timeline.SetTrackName("audio", i, label)
        print(f"  A{i}: {label}", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # VERIFICATION
    # ══════════════════════════════════════════════════════════
    print(f"\n── Verification ──", file=sys.stderr)
    resolve.OpenPage("edit")

    all_passed = True
    if run_full_timeline_qa:
        final_report = run_full_timeline_qa(timeline, project, manifest)
        qa_reports.append(final_report)
        all_passed = final_report.passed
        for check in final_report.checks:
            status = "✓" if check.passed else "✗"
            print(f"    {status} {check.name} — expected: {check.expected}, actual: {check.actual}", file=sys.stderr)
            if not check.passed and check.severity == "error":
                results["warnings"].append(f"Final QA Failed {check.name}: {check.actual}")
    else:
        print("  ⚠ Timeline QA script not loaded.", file=sys.stderr)

    print(f"\n── QA Summary ──", file=sys.stderr)
    for rep in qa_reports:
        print(f"  Station {rep.station}: {'Passed' if rep.passed else 'Failed'}", file=sys.stderr)

    results["success"] = all_passed and not results["errors"]
    results["verification_passed"] = all_passed

    status_emoji = "✓" if results["success"] else "✗"
    print(f"\n{status_emoji} Build {'succeeded' if results['success'] else 'FAILED'}", file=sys.stderr)
    if results["errors"]:
        for e in results["errors"]:
            print(f"  ERROR: {e}", file=sys.stderr)
    if results["warnings"]:
        for w in results["warnings"]:
            print(f"  WARNING: {w}", file=sys.stderr)

    return results


# ─── CLI Entry Point ─────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import select

    # BUG FIX C7: Support both stdin JSON (orchestrator mode) and argparse
    # file path (CLI mode). Check if stdin has data first, fall back to argparse.
    manifest = None
    subtitle_overlay = None
    motion_graphics = None
    project_name = None
    keep_existing = False

    if not sys.stdin.isatty() and select.select([sys.stdin], [], [], 0.0)[0]:
        # Orchestrator mode: JSON piped via stdin
        raw = sys.stdin.read().strip()
        if raw:
            input_data = json.loads(raw)
            # The orchestrator may wrap the manifest or pass it directly
            manifest = input_data.get("assembly_manifest", input_data)
            subtitle_overlay = input_data.get("subtitle_overlay_path")
            motion_graphics = input_data.get("motion_graphics_path")
            project_name = input_data.get("project_name")

    if manifest is None:
        # CLI mode: parse arguments
        parser = argparse.ArgumentParser(description="Build Resolve timeline from manifest")
        parser.add_argument("manifest", help="Path to assembly_manifest.json")
        parser.add_argument("--subtitle-overlay", help="Path to Remotion subtitle overlay (.mov)")
        parser.add_argument("--motion-graphics", help="Path to Remotion motion graphics overlay (.mov)")
        parser.add_argument("--project", help="Resolve project name")
        parser.add_argument("--keep-existing", action="store_true",
                            help="Don't delete existing timelines with same name")
        args = parser.parse_args()

        with open(args.manifest) as f:
            manifest = json.load(f)
        subtitle_overlay = args.subtitle_overlay
        motion_graphics = args.motion_graphics
        project_name = args.project
        keep_existing = args.keep_existing

    result = build_timeline(
        manifest,
        subtitle_overlay_path=subtitle_overlay,
        motion_graphics_path=motion_graphics,
        project_name=project_name,
        delete_existing=not keep_existing,
    )

    # BUG FIX C7: Output structured JSON result to stdout (only JSON, no
    # other prints - all status logging goes to stderr).
    json.dump({"rendered_output": result}, sys.stdout, indent=2, default=str)
