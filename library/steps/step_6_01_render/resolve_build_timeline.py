#!/usr/bin/env python3
"""
Resolve API Timeline Builder (Pipeline v4)

Builds a complete DaVinci Resolve timeline entirely via the Resolve scripting
API. This gives us:
  - Exact track targeting (trackIndex parameter)
  - Animated Fusion VFX via .comp file import (BezierSpline keyframes)
  - Clean track layout: V1=A-Roll, V2=B-Roll, V3=Subtitles, V4=MotionGraphics,
    V5=GeneratorEffects, A1=Speech(auto), A2=Music, A3+=SFX
  - Fairlight preset application for audio effects

Reads an assembly_manifest.json and optional Remotion overlay paths.

Note: Live Resolve interaction tools (project_manager, timeline, media_pool, etc.)
are available to agents via the `davinci-resolve` MCP server.

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

# Add tools AND the repository root to the path. The repo root matters:
# this module is normally run as a SCRIPT (`resolve_build_timeline.py
# <manifest>`), so sys.path[0] is this directory and not the repo, and
# `visual_qa_router` imports `library.tools.*` absolutely. Without the
# root it raised ModuleNotFoundError - and because all four import groups
# below shared ONE try/except, that single failure set every timeline QA
# station, the neural-engine wrappers and the Fairlight helpers to None.
# The whole verification layer was dead in every scripted run, announced
# by one line reading "Timeline QA script not loaded".
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, '../../tools'), os.path.join(_HERE, '../../..')):
    _p = os.path.abspath(_p)
    if _p not in sys.path:
        sys.path.append(_p)

# One try per group, so a failure costs only its own group. Each records
# WHY, because "not loaded" without a reason is what let this sit.
_TOOLING_IMPORT_ERRORS = {}

try:
    from neural_engine import apply_super_scale, apply_stabilization
except ImportError as _e:
    apply_super_scale = apply_stabilization = None
    _TOOLING_IMPORT_ERRORS["neural_engine"] = str(_e)

try:
    from fairlight_presets import get_preset, apply_fairlight_preset
except ImportError as _e:
    get_preset = apply_fairlight_preset = None
    _TOOLING_IMPORT_ERRORS["fairlight_presets"] = str(_e)

try:
    from timeline_qa import (
        verify_clip_placement, verify_transitions, verify_color_grades,
        verify_audio, verify_fusion_comps, run_full_timeline_qa
    )
except ImportError as _e:
    verify_clip_placement = verify_transitions = verify_color_grades = None
    verify_audio = verify_fusion_comps = run_full_timeline_qa = None
    _TOOLING_IMPORT_ERRORS["timeline_qa"] = str(_e)

try:
    from visual_qa_router import (
        plan_qa_checks, execute_frame_grab, execute_video_segment_check,
        analyze_frame_locally, format_frame_grab_for_llm, format_segment_result_for_llm
    )
except ImportError as _e:
    plan_qa_checks = None
    _TOOLING_IMPORT_ERRORS["visual_qa_router"] = str(_e)


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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5
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

def _source_fps(pool_item, timeline_fps: float) -> float:
    """Frame rate of a pool item's own timebase.

    AppendToTimeline's startFrame/endFrame are in the SOURCE's frame rate,
    not the timeline's. Resolve gives audio-only clips a nominal 24fps, so
    computing their in/out points at the timeline's 30fps stretched the
    music to 125% of its intended length and left ~11s of trailing music
    past the last picture.
    """
    try:
        fps = float(pool_item.GetClipProperty("FPS"))
        if fps > 0:
            return fps
    except (TypeError, ValueError):
        pass
    return timeline_fps


# Resolve's Inspector Transform properties, as `TimelineItem.GetProperty()`
# reports them on a video item. The horizontal and vertical position are
# **Pan** and **Tilt**. There is no `PanX` and no `PanY`: setting either
# returns False, reads back None, and changes nothing on screen. Measured
# on Resolve 21.0.0b.28 - three renders at pan +682.67, 0 and -682.67 came
# out byte-identical until these names were corrected.
_CONFORM_ZOOM_PROPS = ("ZoomX", "ZoomY")
_CONFORM_PAN_PROP = "Pan"
_CONFORM_TILT_PROP = "Tilt"


def _apply_conform(timeline_item, clip: dict, results: dict) -> None:
    """Scale and optionally pan a clip within the output frame.

    ``fill_zoom`` controls how much of the gap between fit (letterbox) and
    fill (no bars) is closed.  ``framing_pan_x`` / ``framing_pan_y`` shift
    the crop window within the zoomed source so the framing is not locked
    to dead centre.  A positive ``framing_pan_x`` moves the picture right,
    which moves the crop window LEFT over the source - so a subject in the
    left third of a landscape frame needs a positive value.

    compile_manifest computes all three values from the per-clip
    ``framing_intent`` parameter and writes them into the manifest clip
    dict.  This function applies them to the placed Resolve TimelineItem.

    Every SetProperty is judged by its RETURN VALUE. Resolve does not raise
    on a property name it does not know - it returns False and carries on,
    which is how `PanX` survived in this function with a passing test suite
    behind it.
    """
    if not clip.get("needs_conform"):
        return
    zoom = clip.get("fill_zoom")
    if not zoom or zoom <= 1.0:
        return
    label = clip.get("label", "?")

    def _set(prop, value):
        """Set one property, and say so when Resolve declines."""
        try:
            ok = timeline_item.SetProperty(prop, value)
        except Exception as e:  # Resolve raises bare Exceptions here
            results["warnings"].append(
                f"Conform {prop} failed for {label}: {e}")
            return False
        if not ok:
            results["warnings"].append(
                f"Conform {prop}={value} refused by Resolve for {label}")
        return bool(ok)

    for prop in _CONFORM_ZOOM_PROPS:
        if not _set(prop, zoom):
            return

    # Pan/Tilt: pixel offset from centre, computed by compile_manifest from
    # a normalised -1..1 value. Only applied when non-zero so existing
    # projects that never set them are byte-identical.
    pan_x = clip.get("framing_pan_x")
    pan_y = clip.get("framing_pan_y")
    if pan_x:
        _set(_CONFORM_PAN_PROP, pan_x)
    if pan_y:
        _set(_CONFORM_TILT_PROP, pan_y)


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


# ─── Transparent Carrier for Generator Overlays ──────────────

def _ensure_transparent_carrier(
    project_folder: str,
    width: int,
    height: int,
    fps: int,
    duration_s: float,
    media_pool,
    root_folder,
):
    """Create a transparent ProRes 4444 carrier clip via ffmpeg and import it.

    Generator presets produce content from nothing and are placed on
    the overlay track as Fusion comps on top of a transparent carrier
    clip. This function ensures the carrier always exists - no warnings,
    no silent skips.

    Returns the MediaPoolItem for the imported carrier, or raises
    RuntimeError if creation or import fails.
    """
    # The carrier lives next to the project's pipeline assets so it
    # persists across runs and is never mistaken for user footage.
    carrier_dir = os.path.join(project_folder, "pipeline_assets") if project_folder else os.path.join(os.path.dirname(__file__), "_carriers")
    os.makedirs(carrier_dir, exist_ok=True)
    carrier_path = os.path.join(
        carrier_dir,
        f"transparent_{width}x{height}_{fps}fps.mov",
    )

    # Generate via ffmpeg if not already on disk.
    if not os.path.exists(carrier_path):
        # Duration needs to be at least as long as the longest generator
        # overlay, but we generate one that covers the whole timeline to
        # be safe. Generous ceiling avoids off-by-one frame issues.
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=black@0.0:s={width}x{height}:r={fps}:d={duration_s + 1}",
            "-c:v", "prores_ks",
            "-profile:v", "4",       # ProRes 4444 for alpha
            "-pix_fmt", "yuva444p10le",
            "-t", str(duration_s + 1),
            carrier_path,
        ]
        result = subprocess.run(
            cmd, capture_output=True, encoding="utf-8",
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed to create transparent carrier: {result.stderr}"
            )
        print(
            f"  Created transparent carrier: {carrier_path}",
            file=sys.stderr,
        )

    # Import into the media pool under a Generators subfolder.
    media_pool.SetCurrentFolder(root_folder)
    gen_folder = None
    for sub in (root_folder.GetSubFolderList() or []):
        if sub.GetName() == "Generators":
            gen_folder = sub
            break
    if not gen_folder:
        gen_folder = media_pool.AddSubFolder(root_folder, "Generators")

    media_pool.SetCurrentFolder(gen_folder)
    imported = media_pool.ImportMedia([carrier_path])
    media_pool.SetCurrentFolder(root_folder)

    if not imported or len(imported) == 0:
        raise RuntimeError(
            f"Resolve refused to import transparent carrier: {carrier_path}"
        )

    carrier_item = imported[0]
    # ProRes 4444 alpha must be recognized as premultiplied.
    carrier_item.SetClipProperty("Alpha mode", "Premultiplied")
    return carrier_item


# ─── Core: Build Timeline ────────────────────────────────────

def build_timeline(
    manifest: dict,
    subtitle_overlay_path: Optional[str] = None,
    motion_graphics_path: Optional[str] = None,
    project_name: Optional[str] = None,
    delete_existing: bool = True,
    project_folder: str = "",
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
    # SFX live in exactly one place: tracks.A3.clips (see compile_manifest).
    a3_clips = tracks.get('A3', {}).get('clips', [])
    # Note: transitions are applied via fusion_effects.transitions, not
    # the top-level 'transitions' key (which is informational only).

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
        if 'source_in_frame' not in s:
            s['total_frames'] = round(
                (s.get('timeline_end', 0) - s.get('timeline_start', 0)) * fps)

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
    generator_overlays = manifest.get('generator_overlays', [])
    has_generators = bool(generator_overlays)

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
    if has_generators:
        target_video_tracks = max(target_video_tracks, 5)

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

    # Compute per-clip source frame ranges for video and audio placement.
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
                
            _apply_conform(placed, clip, results)
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
                placed_v2 = result[0] if isinstance(result, list) else result
                _apply_conform(placed_v2, clip, results)
                v2_count += 1
                v2_placed_labels.append(clip.get('label', basename))
                print(f"  ✓ [{ci}] {clip.get('label', basename)}: TL {tl_in_f}", file=sys.stderr)
            else:
                print(f"  ✗ [{ci}] {basename}: failed", file=sys.stderr)

        results["tracks"]["V2"] = v2_count
        
    # ══════════════════════════════════════════════════════════
    # PLACE V3: Subtitle Overlay Segments (Remotion)
    # ══════════════════════════════════════════════════════════
    if has_subtitles:
        print(f"\n── V3 Subtitle Overlay: {len(sub_segments)} segments ──", file=sys.stderr)
        
        # Build mapping from spine block position -> actual V1 timeline position offset
        block_offsets = {}
        placed_by_label = dict(zip(v1_placed_labels, v1_timeline_items))
        
        for clip in v1_clips:
            label = clip.get('label', '')
            parts = label.split('_')
            # e.g., "speech_3", "speech_3_seg0", "hook_1"
            if len(parts) >= 2 and parts[0] in ('speech', 'hook'):
                try:
                    block_idx = int(parts[1])
                    if block_idx not in block_offsets:
                        if label in placed_by_label:
                            placed = placed_by_label[label]
                            try:
                                actual_start = placed.GetStart()
                            except AttributeError:
                                actual_start = clip.get('timeline_in_frame', 0)
                                
                            estimated_start = clip.get('timeline_in_frame', 0)
                            block_offsets[block_idx] = actual_start - estimated_start
                except ValueError:
                    pass

        import re
        v3_count = 0
        for si, seg in enumerate(sub_segments):
            seg_path = seg.get('overlay_path', '')
            seg_basename = os.path.basename(seg_path)
            
            # Look up which spine block it belongs to
            block_idx = seg.get('_block_position')
            if block_idx is None:
                m = re.search(r'sub_block_(\d+)', seg_basename)
                if m:
                    block_idx = int(m.group(1))

            if block_idx is not None and block_idx not in block_offsets:
                # Block was cut from the final timeline (no V1 clip placed)
                print(f"  ⚠ [{si}] {seg_basename}: spine block {block_idx} missing from V1, skipping", file=sys.stderr)
                continue
                
            pool_item = _find_pool_clip(seg_path)
            if not pool_item:
                results["warnings"].append(f"V3[{si}] {seg_basename} not in pool")
                print(f"  ✗ [{si}] {seg_basename} not in media pool", file=sys.stderr)
                continue

            # Ensure ProRes 4444 alpha channel is recognized
            pool_item.SetClipProperty("Alpha mode", "Premultiplied")

            offset_f = block_offsets.get(block_idx, 0) if block_idx is not None else 0

            # Trim the rendered animation handles: place the clip on its
            # TRUE content bounds so adjacent blocks do not overlap.
            src_in_f = seg.get('source_in_frame', 0)
            src_out_f = seg.get('source_out_frame')
            if src_out_f is None:
                src_out_f = src_in_f + seg.get('total_frames', round(
                    (seg.get('timeline_end', 0) - seg.get('timeline_start', 0)) * fps))
            seg_frames = src_out_f - src_in_f
            tl_in_frame = round(seg.get('timeline_start', 0) * fps)

            # Shift the subtitle's timeline_start by the offset
            tl_in_frame += offset_f

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_out_f,
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
    # PLACE V5: Generator Effect Overlays (Fusion Presets)
    # ══════════════════════════════════════════════════════════
    # Generator presets produce content from nothing (no image input).
    # They are placed on V5 as transparent carrier clips; the Fusion
    # comp import subprocess imports the .setting file onto each clip.
    # Composite mode is set per-entry (default: Screen) so the generated
    # content blends over the picture on V1/V2 below.
    if has_generators:
        print(f"\n-- V5 Generator Effects: {len(generator_overlays)} overlays --", file=sys.stderr)
        v5_count = 0

        # Create and import the transparent carrier clip. This call
        # generates the MOV via ffmpeg if it doesn't exist on disk,
        # imports it to the media pool, and returns the pool item.
        # It raises RuntimeError on failure - no silent skips.
        transparent_carrier = _ensure_transparent_carrier(
            project_folder=project_folder,
            width=width,
            height=height,
            fps=fps,
            duration_s=total_duration,
            media_pool=media_pool,
            root_folder=root_folder,
        )

        # Store generator overlay metadata for apply_fusion_comps
        # to pick up and import the .setting files.
        manifest.setdefault('fusion_effects', {})
        manifest['fusion_effects']['generator_overlays'] = generator_overlays

        COMPOSITE_MODES = {
            "normal": 0, "screen": 5, "add": 1, "multiply": 3,
        }
        for gi, gen in enumerate(generator_overlays):
            tl_start = gen['timeline_start']
            tl_end = gen['timeline_end']
            dur_frames = round((tl_end - tl_start) * fps)
            tl_in_frame = round(tl_start * fps)

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": transparent_carrier,
                "startFrame": 0,
                "endFrame": dur_frames,
                "trackIndex": 5,
                "recordFrame": tl_in_frame,
                "mediaType": 1,
            }])

            if result:
                placed = result[0] if isinstance(result, list) else result
                # Set composite mode for the overlay blend
                mode_name = gen.get('composite_mode', 'screen')
                mode_val = COMPOSITE_MODES.get(mode_name, 5)
                placed.SetProperty('CompositeMode', mode_val)
                v5_count += 1
                print(
                    f"  V [{gi}] {gen['effect_name']}: "
                    f"V5 {dur_frames}f @ TL {tl_in_frame} "
                    f"(composite: {mode_name})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  X [{gi}] {gen['effect_name']}: "
                    f"placement failed",
                    file=sys.stderr,
                )

        results["tracks"]["V5"] = v5_count

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
            src_fps = _source_fps(pool_item, fps)
            # Source in/out are in the SOURCE's timebase; the record frame
            # is in the timeline's.
            src_dur_f = round((tl_out_sec - tl_in_sec) * src_fps)
            src_in_f = round(clip.get('source_in', 0) * src_fps)

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_in_f + src_dur_f,
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
            src_fps = _source_fps(pool_item, fps)
            src_dur_f = round(((tl_out_f - tl_in_f) / fps) * src_fps)
            src_in_f = round(clip.get('source_in', 0) * src_fps)

            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_in_f + src_dur_f,
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
        if project_folder:
            cmd += ["--project-folder", project_folder]
        print(f"\n── Launching subprocess for Fusion Comps ──", file=sys.stderr)
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        
        if proc.returncode != 0:
            results["warnings"].append(f"Fusion subprocess failed: {proc.stderr}")
            print(f"  ✗ Fusion Comps Subprocess Failed", file=sys.stderr)
        else:
            print(proc.stderr, file=sys.stderr)
            
        os.remove(temp_manifest)
    else:
        results["warnings"].append(f"apply_fusion_comps.py not found at {script_path}")

    if verify_fusion_comps:
        # verify_fusion_comps reads per_clip - that is fusion_effects, not
        # the flat vfx LIST, which has no .get(). per_clip is keyed by clip
        # LABEL, so the station needs the labels this run actually placed,
        # in timeline order, per track.
        _run_qa(verify_fusion_comps(
            timeline,
            {
                1: v1_placed_labels,
                2: v2_placed_labels if 'v2_placed_labels' in locals() else [],
            },
            manifest.get("fusion_effects", {})))

    # Transitions are drawn by the Fusion pass above, so the station that
    # checks them has to run after it - not back at V2 placement, where it
    # used to sit and could never have seen a comp.
    planned_transitions = manifest.get("fusion_effects", {}).get("transitions", [])
    if verify_transitions:
        transition_report = verify_transitions(timeline, None, planned_transitions)
        _run_qa(transition_report)
        # An individual miss is a warning; every single one missing is the
        # collapse this whole layer exists to catch - zero transitions of
        # any kind reached the finished video for the life of the
        # pipeline, and nothing noticed.
        if planned_transitions and not transition_report.passed:
            v1_with_comps = sum(
                1 for item in (timeline.GetItemListInTrack("video", 1) or [])
                if item.GetFusionCompNameList()
            )
            if v1_with_comps == 0:
                results["errors"].append(
                    f"{len(planned_transitions)} transitions were planned "
                    f"and not one clip on V1 carries a Fusion comp"
                )

    # ══════════════════════════════════════════════════════════
    # NEURAL ENGINE DIRECTIVES (Per-Clip)
    # ══════════════════════════════════════════════════════════
    neural_directives = manifest.get('neural_engine_directives', {})
    if neural_directives and apply_stabilization is not None:
        print(f"\n── Neural Engine: {len(neural_directives)} clips ──", file=sys.stderr)

        def _apply_directives(track, items, labels):
            """Apply one track's directives, recording what really happened.

            The wrappers return False when Resolve declines - which they
            do - and this used to print a tick regardless of the answer.
            Magic Mask is not handled at all: CreateMagicMask returns
            False for every mode, so compile_manifest no longer emits it.
            """
            for ci, label in enumerate(labels):
                if label not in neural_directives or ci >= len(items):
                    continue
                directives = neural_directives[label]
                tl_clip = items[ci]

                if directives.get('stabilize'):
                    ok = apply_stabilization(tl_clip)
                    mark = "✓" if ok else "✗"
                    print(f"  {mark} [{track}{ci}] {label}: Stabilization",
                          file=sys.stderr)
                    if not ok:
                        results["warnings"].append(
                            f"Stabilization refused on {track}{ci} {label}")
                if directives.get('super_scale'):
                    ok = apply_super_scale(
                        tl_clip, scale_factor=directives['super_scale'])
                    mark = "✓" if ok else "✗"
                    print(f"  {mark} [{track}{ci}] {label}: Super Scale "
                          f"{directives['super_scale']}x", file=sys.stderr)
                    if not ok:
                        results["warnings"].append(
                            f"Super Scale refused on {track}{ci} {label}")

        _apply_directives(
            "V1", timeline.GetItemListInTrack("video", 1) or [],
            v1_placed_labels)
        # v2_placed_labels only exists when V2 placement ran at all.
        _apply_directives(
            "V2", timeline.GetItemListInTrack("video", 2) or [],
            v2_placed_labels if 'v2_placed_labels' in locals() else [])

    # Smart Reframe used to be applied here, on the timeline, and printed a
    # tick whatever Resolve answered. It is withdrawn; see the note at the
    # top of library/tools/neural_engine.py. Framing is delivered per clip
    # by _apply_conform above.

    # ══════════════════════════════════════════════════════════
    # APPLY FAIRLIGHT PRESET (if specified)
    # ══════════════════════════════════════════════════════════
    audio_config = manifest.get('audio', {})
    fairlight_preset = audio_config.get('fairlight_preset', '')
    if fairlight_preset:
        print(f"\n── Fairlight Preset: {fairlight_preset} ──", file=sys.stderr)
        try:
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
        except Exception as e:
            results["warnings"].append(f"Fairlight preset '{fairlight_preset}' exception: {e}")
            print(f"  ⚠ Fairlight preset '{fairlight_preset}' raised an exception: {e}", file=sys.stderr)

    # Apply audio mix automation and master limiter markers
    audio_mix = manifest.get('audio_mix', {})
    music_automation = audio_mix.get('music_automation', [])
    master_limiter = audio_mix.get('master_limiter', {})
    
    if master_limiter and master_limiter.get('enabled'):
        threshold_db = master_limiter.get('threshold_db', -1.0)
        timeline.AddMarker(0, "Purple", f"Master Limiter: {threshold_db}dBTP", "Set the master track limiter to this threshold", 1)

    if music_automation and isinstance(music_automation, list):
        for auto in music_automation:
            time_sec = auto.get('timeline_start', 0)
            vol_db = auto.get('target_level_db', 0)
            behavior = auto.get('music_behavior', 'background')
            frame = round(time_sec * fps)
            timeline.AddMarker(frame, "Cyan", f"Target Level: {vol_db}dB ({behavior})", "Duck or boost the music track to this target level", 1)
        print(f"  ✓ Added {len(music_automation)} audio target markers to timeline", file=sys.stderr)

    if verify_audio:
        _run_qa(verify_audio(timeline, project, manifest.get("audio", {})))

    # ══════════════════════════════════════════════════════════
    # COLOR GRADING (house look: CDL half)
    # ══════════════════════════════════════════════════════════
    # The look's other half is Fusion, and it does not arrive here: it is
    # merged into fusion_effects.per_clip by compile_manifest and drawn by
    # apply_fusion_comps. There is no PowerGrade route - see
    # library/tools/house_look.py for why the look is CDL plus Fusion.
    color_grade = manifest.get("color_grade", {})
    per_clip_adjs = color_grade.get("per_clip_adjustments", [])
    house_look = color_grade.get("house_look")

    # Build a lookup by source_file basename
    color_lookup = {}
    for adj in per_clip_adjs:
        src = adj.get("source_file", "")
        if src:
            color_lookup[os.path.basename(src).lower()] = adj.get("cdl_values", {})

    if color_lookup:
        label = house_look or "no house look named - exposure only"
        print(f"\n── Color Grading (CDL: {label}) ──", file=sys.stderr)

        for track_idx in range(1, timeline.GetTrackCount("video") + 1):
            items = timeline.GetItemListInTrack("video", track_idx)
            if not items: continue

            for ci, item in enumerate(items):
                mpi = item.GetMediaPoolItem()
                if not mpi: continue

                clip_name = mpi.GetClipProperty("File Name") or item.GetName()
                if not clip_name: continue

                cdl_vals = color_lookup.get(clip_name.lower())
                if not cdl_vals:
                    continue

                # 4 decimals, matching the precision the looks are
                # authored at - at 3 the offsets, which are the smallest
                # numbers in a CDL, lose part of the shadow tint.
                slope = f"{cdl_vals.get('slope_r', 1.0):.4f} {cdl_vals.get('slope_g', 1.0):.4f} {cdl_vals.get('slope_b', 1.0):.4f}"
                offset = f"{cdl_vals.get('offset_r', 0.0):.4f} {cdl_vals.get('offset_g', 0.0):.4f} {cdl_vals.get('offset_b', 0.0):.4f}"
                power = f"{cdl_vals.get('power_r', 1.0):.4f} {cdl_vals.get('power_g', 1.0):.4f} {cdl_vals.get('power_b', 1.0):.4f}"
                sat = f"{cdl_vals.get('saturation', 1.0):.4f}"

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

                print(f"  ✓ Applied CDL base grade to {clip_name}", file=sys.stderr)


    if verify_color_grades:
        _run_qa(verify_color_grades(timeline, None, manifest.get("color_grade", {})))

    # ══════════════════════════════════════════════════════════
    print(f"\n── Track Labels ──", file=sys.stderr)
    video_labels = {1: "A-Roll", 2: "B-Roll", 3: "Subtitles", 4: "Motion Graphics", 5: "Generator Effects"}
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

    # The DRP transition-surgery pass used to sit here. It exported the
    # project to a temp .drp, edited it, and printed "RELOAD REQUIRED /
    # please import this DRP manually" - while step_6_01_render went on to
    # export the live, unmodified timeline. It could not fire in any case:
    # it needed a from_block or "between_N_M" position that the transition
    # spec has never carried. Transitions go through Fusion (see
    # library/tools/transition_vocabulary.py); the surgery tool and its
    # test remain in the tree unused.

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
        # A verification layer that is absent must not read like a passing
        # one. Name the module and the reason, and put it on the record as
        # a warning the caller can see - not one line on stderr.
        reason = _TOOLING_IMPORT_ERRORS.get("timeline_qa", "reason not recorded")
        msg = (f"Timeline QA did not load ({reason}) - no clip placement, "
               f"transition, colour, audio or Fusion-comp check ran")
        print(f"  ⚠ {msg}", file=sys.stderr)
        results["warnings"].append(msg)

    if plan_qa_checks:
        print(f"\n── Visual QA Router ──", file=sys.stderr)
        try:
            qa_plan = plan_qa_checks(manifest, phase="post_build", fps=fps)
            visual_qa_results = []
            
            for fg_req in qa_plan.frame_grabs:
                res = execute_frame_grab(resolve, project, timeline, fg_req)
                if res.image_path:
                    res.check = analyze_frame_locally(res.image_path, fg_req.check_type, fg_req.context)
                visual_qa_results.append(format_frame_grab_for_llm(res))
                
            for seg_req in qa_plan.segment_checks:
                res = execute_video_segment_check(resolve, project, timeline, seg_req)
                visual_qa_results.append(format_segment_result_for_llm(res))
                
            results["visual_qa"] = visual_qa_results
            print(f"  ✓ Completed {len(qa_plan.frame_grabs)} frame grabs and {len(qa_plan.segment_checks)} segment checks", file=sys.stderr)
        except Exception as e:
            print(f"  ✗ Visual QA router failed: {e}", file=sys.stderr)
            results["warnings"].append(f"Visual QA router failed: {e}")

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
