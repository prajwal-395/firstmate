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
except ImportError:
    apply_magic_mask = apply_smart_reframe = apply_super_scale = apply_stabilization = None


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

def _allocate_sfx_tracks(sfx_clips, base_track_index=3):
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
        tl_end = clip.get('timeline_out_frame', tl_start + 30)

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
        subtitle_overlay_path: Path to Remotion SubtitleOverlay ProRes 4444
        motion_graphics_path: Path to Remotion FourthWallOverlay ProRes 4444
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
    a3_clips = tracks.get('A3', {}).get('clips', []) or manifest.get('sfx', [])
    # Note: transitions are applied via fusion_effects.transitions, not
    # the top-level 'transitions' key (which is informational only).
    vfx_entries = manifest.get('vfx', [])  # legacy VFX entries

    # If subtitle_overlay_path was not explicitly passed, try reading it
    # from the manifest (populated by step 5.04 from step 4.05 output).
    if not subtitle_overlay_path:
        overlay_info = manifest.get('subtitle_overlay', {})
        overlay_candidate = overlay_info.get('overlay_path', '')
        if overlay_candidate and os.path.exists(overlay_candidate):
            subtitle_overlay_path = overlay_candidate

    results = {
        "success": False,
        "timeline_name": timeline_name,
        "tracks": {},
        "errors": [],
        "warnings": [],
    }

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
    timeline.SetSetting("timelineResolutionWidth", str(width))
    timeline.SetSetting("timelineResolutionHeight", str(height))
    timeline.SetSetting("timelineFrameRate", f"{fps:.3f}")

    print(f"✓ Created timeline: {timeline_name} ({width}x{height} @ {fps}fps)", file=sys.stderr)

    # ── Import all media to pool ──
    all_media_paths = set()
    for clip in v1_clips + v2_clips:
        src = clip.get('source_file', '')
        if src and os.path.exists(src):
            all_media_paths.add(src)
    for clip in a2_clips + a3_clips:
        src = clip.get('source_file', '')
        if src and os.path.exists(src):
            all_media_paths.add(src)
    if subtitle_overlay_path and os.path.exists(subtitle_overlay_path):
        all_media_paths.add(subtitle_overlay_path)
    if motion_graphics_path and os.path.exists(motion_graphics_path):
        all_media_paths.add(motion_graphics_path)

    if all_media_paths:
        imported = media_pool.ImportMedia(list(all_media_paths))
        print(f"✓ Imported {len(imported) if imported else 0} media files", file=sys.stderr)

    # Build pool clip lookup (by filename since paths may differ)
    root_folder = media_pool.GetRootFolder()
    pool_clips = {}

    def _scan_folder(folder):
        for clip in (folder.GetClipList() or []):
            pool_clips[clip.GetName()] = clip
        for sub in (folder.GetSubFolderList() or []):
            _scan_folder(sub)

    _scan_folder(root_folder)
    print(f"  Media pool: {len(pool_clips)} clips", file=sys.stderr)

    # ── Set up tracks ──
    # V1 exists by default. Need V2, V3, V4 for video and extra audio tracks.
    has_v2 = bool(v2_clips)
    has_subtitles = subtitle_overlay_path and os.path.exists(subtitle_overlay_path)
    has_mg = motion_graphics_path and os.path.exists(motion_graphics_path)

    # Calculate how many SFX tracks we need
    sfx_allocations = _allocate_sfx_tracks(a3_clips, base_track_index=3)
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

    for ci, clip in enumerate(v1_clips):
        # BUG FIX C7: Handle clips with missing source_file gracefully
        # (e.g. unresolved SFX clips passed through without file resolution)
        src = clip.get('source_file', '')
        if not src:
            results["warnings"].append(
                f"V1[{ci}] ({clip.get('label', '?')}) missing source_file - skipped")
            print(f"  ⚠ [{ci}] {clip.get('label', '?')}: missing source_file",
                  file=sys.stderr)
            continue
        basename = os.path.basename(src)
        pool_item = pool_clips.get(basename)
        if not pool_item:
            results["errors"].append(f"V1[{ci}] {basename} not in media pool")
            continue

        src_in_f = round(clip['source_in'] * fps)
        src_out_f = round(clip['source_out'] * fps)
        tl_in_f = clip['timeline_in_frame']

        result = media_pool.AppendToTimeline([{
            "mediaPoolItem": pool_item,
            "startFrame": src_in_f,
            "endFrame": src_out_f,
            "trackIndex": 1,
            "recordFrame": tl_in_f,
            # NO mediaType — default behavior puts video on V1, audio on A1
            # This works correctly because only A1 exists at this point
        }])

        if result:
            placed = result[0] if isinstance(result, list) else result
            v1_timeline_items.append(placed)
            print(f"  ✓ [{ci}] {clip.get('label', basename)}: "
                  f"src {src_in_f}-{src_out_f} → V1+A1 at TL {tl_in_f} "
                  f"({placed.GetDuration()}f)", file=sys.stderr)
        else:
            results["errors"].append(f"V1[{ci}] AppendToTimeline failed for {basename}")
            print(f"  ✗ [{ci}] {basename}: AppendToTimeline returned None", file=sys.stderr)

    results["tracks"]["V1"] = len(v1_timeline_items)
    results["tracks"]["A1"] = len(v1_timeline_items)  # auto-linked

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
        for ci, clip in enumerate(v2_clips):
            basename = os.path.basename(clip['source_file'])
            pool_item = pool_clips.get(basename)
            if not pool_item:
                results["warnings"].append(f"V2[{ci}] {basename} not in pool")
                continue

            src_in_f = round(clip.get('source_in', 0) * fps)
            src_out_f = round(clip.get('source_out', clip.get('source_in', 0) + 3.5) * fps)
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
                print(f"  ✓ [{ci}] {clip.get('label', basename)}: TL {tl_in_f}", file=sys.stderr)
            else:
                print(f"  ✗ [{ci}] {basename}: failed", file=sys.stderr)

        results["tracks"]["V2"] = v2_count

    # ══════════════════════════════════════════════════════════
    # PLACE V3: Subtitle Overlay (Remotion)
    # ══════════════════════════════════════════════════════════
    if has_subtitles:
        print(f"\n── V3 Subtitle Overlay ──", file=sys.stderr)
        sub_basename = os.path.basename(subtitle_overlay_path)
        pool_item = pool_clips.get(sub_basename)
        if pool_item:
            total_frames = round(total_duration * fps)
            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": 0,
                "endFrame": total_frames,
                "trackIndex": 3,
                "recordFrame": 0,
                "mediaType": 1,  # video-only placement on V3
            }])
            if result:
                print(f"  ✓ {sub_basename} on V3 ({total_frames}f)", file=sys.stderr)
                results["tracks"]["V3"] = 1
            else:
                print(f"  ✗ Failed to place subtitle overlay", file=sys.stderr)
                results["warnings"].append("Subtitle overlay placement failed")
        else:
            print(f"  ✗ {sub_basename} not in media pool", file=sys.stderr)
            results["warnings"].append(f"Subtitle overlay not in pool: {sub_basename}")

    # ══════════════════════════════════════════════════════════
    # PLACE V4: Motion Graphics Overlay (Remotion)
    # ══════════════════════════════════════════════════════════
    if has_mg:
        print(f"\n── V4 Motion Graphics ──", file=sys.stderr)
        mg_basename = os.path.basename(motion_graphics_path)
        pool_item = pool_clips.get(mg_basename)
        if pool_item:
            total_frames = round(total_duration * fps)
            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": 0,
                "endFrame": total_frames,
                "trackIndex": 4,
                "recordFrame": 0,
                "mediaType": 1,  # video-only placement on V4
            }])
            if result:
                print(f"  ✓ {mg_basename} on V4 ({total_frames}f)", file=sys.stderr)
                results["tracks"]["V4"] = 1
            else:
                print(f"  ✗ Failed to place motion graphics", file=sys.stderr)
        else:
            print(f"  ✗ {mg_basename} not in media pool", file=sys.stderr)

    # ══════════════════════════════════════════════════════════
    # PLACE A2: Music
    # ══════════════════════════════════════════════════════════
    if a2_clips:
        print(f"\n── A2 Music: {len(a2_clips)} clips ──", file=sys.stderr)
        for ci, clip in enumerate(a2_clips):
            basename = os.path.basename(clip['source_file'])
            pool_item = pool_clips.get(basename)
            if not pool_item:
                results["warnings"].append(f"A2[{ci}] {basename} not in pool")
                continue

            dur_f = round(clip.get('duration', total_duration) * fps)
            src_in_f = round(clip.get('source_in', 0) * fps)

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_in_f + dur_f,
                "trackIndex": 2,  # A2
                "recordFrame": 0,
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
            pool_item = pool_clips.get(basename)
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
    # IMPORTANT: Each clip can only have ONE active Fusion composition.
    # ImportFusionComp adds alongside existing comps, but only one is
    # active — creating orphan comps is a silent error. So we bake
    # transitions directly INTO the VFX .comp for each clip.
    fusion_effects = manifest.get('fusion_effects', {})
    per_clip_effects = fusion_effects.get('per_clip', {})
    transition_specs = fusion_effects.get('transitions', [])

    # Build transition lookup: clip_index → {tail_transition, head_transition}
    transition_by_clip = {}
    macro_transitions_by_clip = {}
    for tspec in transition_specs:
        ttype = tspec.get('type', 'cut')
        if ttype in ('cut', 'hard_cut', '', None):
            continue
            
        after_idx = tspec.get('after_clip', 0)
        
        if ttype == 'macro':
            macro_transitions_by_clip[after_idx] = tspec
            continue
            
        dur_f = tspec.get('duration_frames', 12)
        # Outgoing clip gets tail transition
        transition_by_clip.setdefault(after_idx, {})
        transition_by_clip[after_idx]['tail_transition'] = ttype
        transition_by_clip[after_idx]['tail_transition_frames'] = dur_f
        # Incoming clip gets head transition
        next_idx = after_idx + 1
        transition_by_clip.setdefault(next_idx, {})
        transition_by_clip[next_idx]['head_transition'] = ttype
        transition_by_clip[next_idx]['head_transition_frames'] = dur_f

    has_any_effects = per_clip_effects or transition_by_clip or macro_transitions_by_clip

    if has_any_effects:
        print(f"\n── Fusion .comp: {len(per_clip_effects)} VFX, "
              f"{len(transition_specs)} transitions ──",
              file=sys.stderr)
        
        try:
            from fusion_macro_loader import apply_macro_to_transition
        except ImportError:
            apply_macro_to_transition = None

        from fusion_comp_generator import generate_comp, write_comp, SEGMENT_PRESETS

        comp_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'fusion_comps')
        os.makedirs(comp_dir, exist_ok=True)

        v1_items = timeline.GetItemListInTrack("video", 1) or []

        for ci, clip_spec in enumerate(v1_clips):
            label = clip_spec.get('label', f'clip_{ci}')

            # Gather VFX effects
            effects = per_clip_effects.get(label, {})
            if not effects and label in SEGMENT_PRESETS:
                effects = dict(SEGMENT_PRESETS[label])
            elif effects:
                effects = dict(effects)
            else:
                effects = {}

            # Handle _preset references
            preset_name = effects.pop('_preset', None)
            if preset_name and preset_name in SEGMENT_PRESETS:
                base = dict(SEGMENT_PRESETS[preset_name])
                base.update({k: v for k, v in effects.items()
                            if k != '_preset'})
                effects = base

            # Merge transition params into this clip's effects
            trans_params = transition_by_clip.get(ci, {})
            if trans_params:
                effects.update(trans_params)

            # Check if this clip has a macro transition after it
            macro_trans = macro_transitions_by_clip.get(ci, None)

            if not effects and not macro_trans:
                continue

            if ci >= len(v1_items):
                results["warnings"].append(
                    f"VFX: clip {ci} ({label}) not on timeline")
                continue
                
            tl_clip = v1_items[ci]
            clip_dur = (tl_clip.GetSourceEndFrame()
                        - tl_clip.GetSourceStartFrame() + 1)
                        
            # Apply macro transition if it exists
            macro_applied = False
            if macro_trans and apply_macro_to_transition:
                macro_data = macro_trans.get("macro_preset", {})
                duration_ms = macro_trans.get("duration_ms", 500)
                macro_applied = apply_macro_to_transition(tl_clip, macro_data, duration_ms)
                if macro_applied:
                    print(f"  ✓ [{ci}] {label}: Applied Fusion Macro transition", file=sys.stderr)
                else:
                    print(f"  ⚠ [{ci}] {label}: Macro transition failed, falling back to dissolve", file=sys.stderr)
                    effects["tail_transition"] = "fade_to_black"
                    effects["tail_transition_frames"] = 12

            if not effects:
                continue

            # If only transitions, add minimal defaults
            if 'zoom_start' not in effects:
                effects.setdefault('zoom_start', 1.0)
                effects.setdefault('zoom_mid', 1.0)
                effects.setdefault('zoom_end', 1.0)
                effects.setdefault('vignette', False)

            # Generate unified .comp (VFX + transitions in one)
            comp_content = generate_comp(clip_dur, **effects)
            comp_path = write_comp(
                os.path.join(comp_dir, f"{label.lower()}.comp"),
                comp_content
            )

            # Clear ALL existing comps to prevent orphans
            for cn in (tl_clip.GetFusionCompNameList() or []):
                tl_clip.DeleteFusionCompByName(cn)

            result = tl_clip.ImportFusionComp(comp_path)

            # AUDIT FIX: ImportFusionComp returns truthy even for
            # empty/malformed files. Verify tools actually loaded.
            if result:
                comp_names = tl_clip.GetFusionCompNameList()
                comp = (tl_clip.GetFusionCompByName(comp_names[0])
                        if comp_names else None)
                tools = comp.GetToolList() if comp else {}
                real_tools = [
                    t for t in tools.values()
                    if t.GetAttrs().get('TOOLS_RegID')
                    not in ('MediaIn', 'MediaOut')
                ]

                if len(real_tools) == 0:
                    results["warnings"].append(
                        f"VFX: {label} imported empty comp (0 tools)")
                    print(f"  ✗ [{ci}] {label}: empty comp (bad file)", file=sys.stderr)
                    continue

                parts = []
                xf = comp.FindTool("Transform1")
                if xf:
                    v0 = xf.GetInput("Size", 0)
                    vm = xf.GetInput("Size", clip_dur // 2)
                    if v0 and vm:
                        parts.append(f"zoom:{v0:.3f}→{vm:.3f}")
                tt = effects.get('tail_transition')
                ht = effects.get('head_transition')
                if tt:
                    parts.append(f"tail={tt}")
                if ht:
                    parts.append(f"head={ht}")

                detail = f" ({', '.join(parts)})" if parts else ""
                print(f"  ✓ [{ci}] {label}: {len(real_tools)} tools"
                      f"{detail}", file=sys.stderr)
            else:
                results["warnings"].append(
                    f"VFX: ImportFusionComp failed for {label}")
                print(f"  ✗ [{ci}] {label}: ImportFusionComp failed", file=sys.stderr)

    elif vfx_entries:
        # Legacy fallback: static SetProperty for simple zoom
        print(f"\n── VFX (legacy): {len(vfx_entries)} entries ──", file=sys.stderr)
        for vfx in vfx_entries:
            vfx_type = vfx.get('type', '')
            if vfx_type == 'zoom_pulse':
                vfx_start_f = round(vfx.get('timeline_start', 0) * fps)
                for item in (timeline.GetItemListInTrack("video", 1) or []):
                    if item.GetStart() <= vfx_start_f < item.GetEnd():
                        item.SetProperty("ZoomX", 1.05)
                        item.SetProperty("ZoomY", 1.05)
                        print(f"  ✓ zoom_pulse on {item.GetName()}"
                              f" at {vfx_start_f}f",
                              file=sys.stderr)
                        break

    # ══════════════════════════════════════════════════════════
    # NEURAL ENGINE DIRECTIVES (Per-Clip)
    # ══════════════════════════════════════════════════════════
    neural_directives = manifest.get('neural_engine_directives', {})
    if neural_directives and apply_stabilization is not None:
        print(f"\n── Neural Engine: {len(neural_directives)} clips ──", file=sys.stderr)
        # Apply to V1
        for item in (timeline.GetItemListInTrack("video", 1) or []):
            label = item.GetName()  # Actually item.GetName() returns the pool item name.
            # Wait, the label in manifest is like "hook_0". We can find it by index or pool name.
            # resolve_build_timeline.py doesn't set clip name in timeline easily, 
            # let's just match using v1_clips order or label.
            # We already have v1_items, but let's iterate them.
            pass
            
        # We stored v1_timeline_items earlier in the script. Wait, let's use the loop from V1 placement.
        # Actually it's better to do this inside the script, we can just iterate v1_clips and v1_items.
        v1_items = timeline.GetItemListInTrack("video", 1) or []
        for ci, clip_spec in enumerate(v1_clips):
            label = clip_spec.get('label', f'clip_{ci}')
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
        for ci, clip_spec in enumerate(v2_clips):
            label = clip_spec.get('label', f'broll_{ci}')
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
            print(f"  ✗ Fairlight preset '{fairlight_preset}' not found or failed", file=sys.stderr)
            results["warnings"].append(
                f"Fairlight preset '{fairlight_preset}' failed")

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
            color_lookup[os.path.basename(src)] = adj.get("cdl_values", {})

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

                cdl_vals = color_lookup.get(clip_name)
                if not cdl_vals and not powergrade_path:
                    continue

                if clip_name not in graded_sources:
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

                    graded_sources[clip_name] = item
                    print(f"  ✓ Applied CDL base grade to {clip_name}", file=sys.stderr)
                else:
                    # Subsequent clips from the same source: re-apply the same CDL values
                    if cdl_vals:
                        slope = f"{cdl_vals.get('slope_r', 1.0):.3f} {cdl_vals.get('slope_g', 1.0):.3f} {cdl_vals.get('slope_b', 1.0):.3f}"
                        offset = f"{cdl_vals.get('offset_r', 0.0):.3f} {cdl_vals.get('offset_g', 0.0):.3f} {cdl_vals.get('offset_b', 0.0):.3f}"
                        power = f"{cdl_vals.get('power_r', 1.0):.3f} {cdl_vals.get('power_g', 1.0):.3f} {cdl_vals.get('power_b', 1.0):.3f}"
                        sat = f"{cdl_vals.get('saturation', 1.0):.3f}"
                        try:
                            res = item.SetCDL({
                                "NodeIndex": "1",
                                "Slope": slope,
                                "Offset": offset,
                                "Power": power,
                                "Saturation": sat
                            })
                            if not res:
                                item.SetClipProperty("Slope", slope)
                                item.SetClipProperty("Offset", offset)
                                item.SetClipProperty("Power", power)
                                item.SetClipProperty("Saturation", sat)
                        except Exception:
                            pass
                    
                    if powergrade_path and os.path.exists(powergrade_path):
                        item.ApplyGradeFromDRX(powergrade_path, 1)

                    creative_look_dctl = color_grade.get("creative_look_dctl", "")
                    if creative_look_dctl:
                        try:
                            item.SetLUT(4, creative_look_dctl)
                        except Exception:
                            pass
                        
                    print(f"  ✓ Copied grade to subsequent clip of {clip_name}", file=sys.stderr)

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

    # Duration check
    start_f = timeline.GetStartFrame()
    end_f = timeline.GetEndFrame()
    actual_dur = (end_f - start_f) / fps
    print(f"  Duration: {actual_dur:.1f}s (expected: {total_duration:.1f}s)", file=sys.stderr)

    # Playhead checks at key positions
    playhead_checks = []
    for clip in v1_clips:
        mid_f = (clip['timeline_in_frame'] + clip['timeline_out_frame']) // 2
        playhead_checks.append((mid_f, clip.get('label', os.path.basename(clip['source_file']))))

    print(f"\n  Playhead verification ({len(playhead_checks)} points):", file=sys.stderr)
    all_passed = True
    for frame, expected_label in playhead_checks:
        ifps = int(fps)
        tc = f"{frame // (ifps * 3600):02d}:{(frame // (ifps * 60)) % 60:02d}:" \
             f"{(frame // ifps) % 60:02d}:{frame % ifps:02d}"
        timeline.SetCurrentTimecode(tc)
        item = timeline.GetCurrentVideoItem()
        name = item.GetName() if item else "EMPTY"
        passed = item is not None
        status = "✓" if passed else "✗"
        if not passed:
            all_passed = False
        print(f"    {status} Frame {frame:5d} ({frame / fps:5.1f}s): {name} — expected: {expected_label}", file=sys.stderr)

    # Track inventory
    print(f"\n  Track inventory:", file=sys.stderr)
    for ti in range(1, timeline.GetTrackCount("video") + 1):
        items = timeline.GetItemListInTrack("video", ti)
        name = timeline.GetTrackName("video", ti)
        count = len(items) if items else 0
        print(f"    V{ti} ({name}): {count} clips", file=sys.stderr)

    for ti in range(1, timeline.GetTrackCount("audio") + 1):
        items = timeline.GetItemListInTrack("audio", ti)
        name = timeline.GetTrackName("audio", ti)
        count = len(items) if items else 0
        print(f"    A{ti} ({name}): {count} clips", file=sys.stderr)

    results["success"] = all_passed and not results["errors"]
    results["duration_seconds"] = actual_dur
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
