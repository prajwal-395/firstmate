#!/usr/bin/env python3
"""
Resolve API Timeline Builder (Pipeline v4)

Builds a complete DaVinci Resolve timeline entirely via the Resolve scripting
API. This gives us:
  - Exact track targeting (trackIndex parameter)
  - Animated Fusion VFX via .comp file import (BezierSpline keyframes)
  - Track layout from ONE owner (library/tools/timeline_layout.py):
    the plan decides every track index and name from the material.
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


Rules relocated from AGENTS.md 5
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 5
keeps the headline and points here.

- Stabilization is the memory ceiling of the whole pipeline - do not run
  other heavy jobs beside it.  (The wording moved from AGENTS.md 5,
  which keeps the heading and the pointer here.)
- It changes picture steadiness and nothing else - never structure, timing, framing, grade, captions or sound.
- For a timeline meant to be scrubbed rather than shipped, pop `neural_engine_directives` off the **in-memory** manifest before `build_timeline` and leave the file on disk carrying it.

- **A row exists because something goes on it.**
- **Prefix overlay filenames with their context.** For example, `sub_craig_seg_000.mov`.
- **Place each angle's clips while only its own speech row exists**, or the timeline floods with empty tracks: multi-stream sources auto-link audio onto every existing audio track. Add the angle's speech row just before its audio, music and SFX rows after all speech is placed, and place every audio item with an explicit `mediaType: 2` and `trackIndex` - then READ BACK what landed and delete anything that is not the recorded program stream. This supersedes **Place V1 clips while only track A1 exists**, the single-angle form from when every timeline had one speech row: iPhone MOVs contain multiple audio streams, and the general form above is what stops the flood on multi-angle builds.
- **Resolve audio pool items report 24fps regardless of the timeline.** `AppendToTimeline`'s `startFrame`/`endFrame` are in the SOURCE timebase, so compute audio in/out with the pool item's own FPS.
- **Renders are silent unless you say otherwise.** `SetRenderSettings` must set `ExportAudio`/`AudioCodec` explicitly; `resolve_render.py` also probes the output for an audio stream before reporting success.
"""

import json
import math
import os
import subprocess
import sys
import time
from typing import Optional

from build_verification import (
    derive_verification_verdict,
    detect_unreachable_fusion_effects,
    format_fusion_drop_error,
)

# Add tools AND the repository root to the path. The repo root matters:
# this module is normally run as a SCRIPT (`resolve_build_timeline.py
# <manifest>`), so sys.path[0] is this directory and not the repo, and
# `visual_qa_router` imports `library.tools.*` absolutely. Without the
# root it raised ModuleNotFoundError - and because all four import groups
# below shared ONE try/except, that single failure set every timeline QA
# station, the neural-engine wrappers and the Fairlight helpers to None.
# The whole verification layer was dead in every scripted run, announced
# by one line reading "Timeline QA script not loaded".
# How long the Fusion comp pass may take before it is killed. Generous
# enough for a long edit, finite so a render cannot hang forever.
FUSION_SUBPROCESS_TIMEOUT_S = 600

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.join(_HERE, '../../tools'), os.path.join(_HERE, '../../..')):
    _p = os.path.abspath(_p)
    if _p not in sys.path:
        sys.path.append(_p)

from library.tools.stabilization_authorization import (  # noqa: E402
    FORMAT as STABILIZATION_AUTHORIZATION_FORMAT,
)
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402
from library.tools.spine_contract import (  # noqa: E402
    SPEECH_BLOCK_TYPES)
from library.tools.overlay_carriage import (  # noqa: E402
    apply_clip_attributes,
)
from library.tools.overlay_placement import (  # noqa: E402
    place_overlay_segment,
    sequence_frame_paths,
)
from library.tools.overlay_draw_intent import (  # noqa: E402
    draw_intent_for_segment,
)
from library.tools.execution.deliver_audio_mix import (  # noqa: E402
    PREMIX_SUFFIX, deliver_mix,
)
from library.tools import timeline_decisions  # noqa: E402
from library.tools.resolve_locale import (  # noqa: E402
    scriptapp_preserving_locale,
)
from library.tools.heavy_work_lock import heavy_work_locked  # noqa: E402
from library.tools.timeline_ingest import resolve_project_exactly  # noqa: E402
from library.tools.resolve_lock import (  # noqa: E402
    assert_current_timeline, under_lease)
from library.tools.timeline_layout import (  # noqa: E402
    allocate_non_overlapping_rows,
    plan_layout,
)
from library.tools import resolve_bin_layout as bin_layout  # noqa: E402

# One try per group, so a failure costs only its own group. Each records
# WHY, because "not loaded" without a reason is what let this sit.
_TOOLING_IMPORT_ERRORS = {}

try:
    from neural_engine import apply_super_scale, apply_stabilization
except ImportError as _e:
    apply_super_scale = apply_stabilization = None
    _TOOLING_IMPORT_ERRORS["neural_engine"] = str(_e)

# No fairlight_presets import: the per-item stub is removed (see the
# note at the placement site below), and nothing else in this file
# reads that module.

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
        plan_qa_checks, execute_qa_plan, perceptual_sample,
        analyze_frame_locally, format_frame_grab_for_llm,
        format_segment_result_for_llm, run_perceptual_observation,
        perceptual_qa_enabled
    )
except ImportError as _e:
    plan_qa_checks = run_perceptual_observation = None
    perceptual_qa_enabled = lambda: False
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
    # Through the wrapper: `scriptapp` leaves LC_CTYPE on `C`, and this
    # step reads UTF-8 manifests and writes UTF-8 logs afterwards.
    resolve = scriptapp_preserving_locale(dvr)
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
# Moved to library/tools/timeline_layout.py, the single owner of layout.
# The name stays here so existing importers keep working.

def _allocate_audio_tracks(clips, base_track_index=3, fps=30.0):
    """Allocate audio clips across tracks so none overlaps another.

    Returns list of (clip, track_index) tuples. See
    `library.tools.timeline_layout.allocate_non_overlapping_rows`.
    """
    spans = []
    for clip in clips or []:
        start = clip.get('timeline_in_frame', 0)
        end = clip.get('timeline_out_frame', start + round(fps))
        spans.append((start, end))
    allocations = allocate_non_overlapping_rows(
        spans, base_index=base_track_index)
    out = [(clip, row) for clip, (_, row) in zip(clips or [], allocations)]
    # Keep time order, as the old implementation returned.
    out.sort(key=lambda pair: pair[0].get('timeline_in_frame', 0))
    return out


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


def _item_source_size(timeline_item):
    """The placed item's own source resolution, or None.

    Judged by what `GetClipProperty` RETURNS (AGENTS.md 5): a proxy
    that does not serve it, a missing pool item or an unparseable
    string all answer None, and the caller then says the Pan went
    through unconverted rather than guessing a unit.
    """
    try:
        pool_item = timeline_item.GetMediaPoolItem()
        raw = pool_item.GetClipProperty("Resolution")
    except Exception:  # noqa: BLE001 - judged by the return, not raised
        return None
    try:
        width, height = str(raw).lower().split("x")
        width, height = int(width), int(height)
    except (AttributeError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (width, height)


def _apply_conform(timeline_item, clip: dict, results: dict,
                   frame_size=None, write_context: dict | None = None,
                   item_identity: dict | None = None) -> None:
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
    from library.tools.transform_write_log import set_property

    identity = item_identity or {
        "clip_id": clip.get("clip_id"),
        "source_file": clip.get("source_file") or clip.get("file_path"),
        "record_frame": clip.get("timeline_in_frame"),
    }

    def _set(prop, value):
        """Set one property, and say so when Resolve declines."""
        try:
            ok = set_property(
                timeline_item, prop, value, item_identity=identity,
                **(write_context or {}))
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

    # Pan/Tilt: the MANIFEST carries a pixel offset from centre, computed
    # by compile_manifest from a normalised -1..1 value. Resolve's Pan and
    # Tilt are NOT pixels - one unit moves the clip
    # `source_dim / frame_dim * fit * draw_gain` pixels, the one measured law in
    # `library/tools/resolve_transform.py` (per-build probe measurement,
    # `FALLBACK_DRAW_GAIN` when the probe cannot measure). For a delivery
    # that shares the source's aspect the geometry factor is exactly 1 on
    # both axes, so the factor IS the gain; for a landscape source
    # conformed into a vertical frame it is 2.0 on Pan and 0.633 on Tilt
    # under the fallback gain. A pixel value passed straight through is
    # therefore off by the gain even where the geometry coincides, and
    # the vertical aim is under-applied 3.16x further by the geometry.
    # Only applied when non-zero so projects that never set them are
    # byte-identical.
    pan_x = clip.get("framing_pan_x")
    pan_y = clip.get("framing_pan_y")
    if pan_x or pan_y:
        source_size = _item_source_size(timeline_item)
        if source_size and frame_size:
            from library.tools.resolve_transform import (
                fit_base_scale, units_for_shift)
            base = fit_base_scale(source_size[0], source_size[1],
                                  frame_size[0], frame_size[1])
            if pan_x:
                pan_x = round(units_for_shift(
                    pan_x, source_size[0], frame_size[0], base), 3)
            if pan_y:
                pan_y = round(units_for_shift(
                    pan_y, source_size[1], frame_size[1], base), 3)
        elif pan_x or pan_y:
            # Without both sizes the conversion is unknowable, so the
            # pixel value goes through as it always has - and SAYS it was
            # not converted, because a silent guess is how the two wrong
            # models above survived.
            results["warnings"].append(
                f"Conform pan for {label} was set in unconverted pixels: "
                f"source size {source_size!r}, frame size {frame_size!r}. "
                f"One of them is unreadable, so the Pan/Tilt unit could "
                f"not be derived (library/tools/resolve_transform.py).")
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

    if isinstance(project, dict) and not (project.get('name') or '').strip():
        errors.append(
            "manifest['project'] declares no 'name' - the timeline name "
            "compile_manifest writes there; a manifest without one cannot "
            "be built."
        )

    # The frame the timeline is built at is DECLARED by the manifest
    # (`compile_manifest` writes `resolve_delivery_format` there), never
    # defaulted here. A `.get('resolution', [1080, 1920])` would read a
    # missing declaration as vertical instead of failing (AGENTS.md
    # 10.1: a promised key that is missing fails loudly).
    resolution = (project or {}).get('resolution')
    if not resolution or len(list(resolution)) < 2:
        errors.append(
            "manifest['project'] declares no 'resolution' - the frame "
            "the timeline is built at. compile_manifest writes the "
            "delivery format there; a manifest without one cannot be "
            "built."
        )

    tracks = manifest.get('tracks', {})
    v1_clips = tracks.get('V1', {}).get('clips', [])
    v2_clips = tracks.get('V2', {}).get('clips', [])
    if not v1_clips:
        # A voiceover-over-B-roll cut has no V1 by design: its words
        # play from the audio intake on A1 and B-roll covers its
        # picture on V2. compile_manifest's coverage assertion already
        # proved every frame shows a clip on V1 or V2, so this gate
        # asks only that the picture exists somewhere - not that it
        # sits on a particular row.
        if not v2_clips:
            errors.append("No V1 (A-Roll) clips and no V2 cover either - "
                          "nothing names the picture")
        else:
            print("  Voiceover-led build: no V1 clips, picture rides "
                  f"V2 ({len(v2_clips)} clip(s))", file=sys.stderr)

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
    carrier_exists: Optional[bool] = None,
):
    """Create a transparent ProRes 4444 carrier clip via ffmpeg and import it.

    Generator presets produce content from nothing and are placed on
    the overlay track as Fusion comps on top of a transparent carrier
    clip. This function ensures the carrier always exists - no warnings,
    no silent skips.

    Returns the MediaPoolItem for the imported carrier, or raises
    RuntimeError if creation or import fails.
    """
    # The carrier persists across runs and is never mistaken for user
    # footage, because it lives in the project's output tree with
    # everything else the pipeline generated.  Without a project there is
    # nowhere to put it that belongs to anything, so it falls back to the
    # step directory as before.  See library/tools/project_layout.py.
    carrier_dir, carrier_path = _transparent_carrier_paths(
        project_folder, width, height, fps)
    os.makedirs(carrier_dir, exist_ok=True)

    # Generate via ffmpeg if not already on disk.
    if carrier_exists is None:
        carrier_exists = os.path.exists(carrier_path)
    if not carrier_exists:
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

    # Import into the media pool under the motion-graphics bin - the
    # single owner of every bin path is `resolve_bin_layout`, so the
    # carrier lands where the organiser will keep it rather than in a
    # bin of this build's own invention.
    media_pool.SetCurrentFolder(root_folder)
    gen_folder = None
    for sub in (root_folder.GetSubFolderList() or []):
        if sub.GetName() == bin_layout.MOTION_GRAPHICS_BIN:
            gen_folder = sub
            break
    if not gen_folder:
        gen_folder = media_pool.AddSubFolder(
            root_folder, bin_layout.MOTION_GRAPHICS_BIN)

    media_pool.SetCurrentFolder(gen_folder)
    imported = media_pool.ImportMedia([carrier_path])
    media_pool.SetCurrentFolder(root_folder)

    if not imported or len(imported) == 0:
        raise RuntimeError(
            f"Resolve refused to import transparent carrier: {carrier_path}"
        )

    carrier_item = imported[0]
    # The carrier is written by `_transparent_carrier` above and stays
    # ProRes 4444: it is a generated FILLER, not an overlay artefact,
    # and Resolve's auto data level is right for that codec. Its
    # attributes are still decided by the file rather than asserted
    # here (`library/tools/overlay_carriage.py`), so the day it is
    # written as something else the reading follows it.
    apply_clip_attributes(carrier_item, carrier_path)
    return carrier_item


def _transparent_carrier_paths(project_folder, width, height, fps):
    carrier_dir = (
        str(ProjectLayout(project_folder).write_dir(Area.CARRIERS, step="render"))
        if project_folder
        else os.path.join(os.path.dirname(__file__), "_carriers"))
    carrier_path = os.path.join(
        carrier_dir, f"transparent_{width}x{height}_{fps}fps.mov")
    return carrier_dir, carrier_path


# ─── Core: Build Timeline ────────────────────────────────────

def _pool_item_resolution(pool_item) -> tuple | None:
    """Read a media-pool item's stored frame dimensions, or return None.

    Legacy subtitle segments predate the `geometry` and `tight_box`
    fields. Their geometry can only be recovered safely from the media
    item itself; missing or unreadable dimensions never imply full-frame.
    """
    try:
        raw = pool_item.GetClipProperty("Resolution")
        parts = str(raw).lower().split("x")
        if len(parts) != 2:
            return None
        width, height = (int(part.strip()) for part in parts)
    except Exception:  # noqa: BLE001 - unreadable dimensions stay unknown
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def caption_segment_placement(seg: dict, si: int, caption_row: int,
                              *, media_pool_item=None,
                              frame_size=None) -> tuple:
    """(placement, refusal) for one caption segment's transform.

    Finding 21: one segment per build landed at Tilt 0, inside the
    picture, while every other segment rode its tight-box placement
    to the declared caption row. The chain has exactly one shape
    that ships Tilt 0 silently - a TIGHT canvas reaching
    `place_overlay_segment` with no `tight_box.placement`, where
    `placement=None` reads as "full canvas, nothing to do" and the
    clip sits centred. Whatever dropped the placement (a render
    fallback the record did not carry, stale state, a hand edit),
    the timeline must not carry a centred caption mutely.

    Returns the placement to ride, or (None, reason): a tight segment
    with no placement is REFUSED by name - skipped, warned, counted
    nowhere. A full-canvas segment (`geometry == "full"`) draws its
    text natively and rides untransformed, exactly as before. A
    segment too old to declare a geometry but carrying a placement
    rides it. A legacy segment with neither geometry nor a `tight_box`
    record is accepted as full-frame only when the Resolve media-pool
    dimensions exactly match the requested timeline frame. Unknown or
    different dimensions are refused rather than assumed full canvas.
    """
    tight_record = seg.get("tight_box")
    tight = tight_record or {}
    placement = tight.get("placement")
    if placement:
        return placement, ""
    geometry = seg.get("geometry", "") or ""
    if geometry == "full":
        return None, ""
    measured_size = None
    if not geometry and tight_record is None and frame_size is not None:
        measured_size = _pool_item_resolution(media_pool_item)
        if measured_size == tuple(frame_size):
            return None, ""
    seg_id = seg.get("segment_id") or seg.get("overlay_path") or "?"
    geometry_label = geometry or "legacy/unknown-geometry"
    dimension_note = ""
    if not geometry:
        if tight_record is not None:
            dimension_note = "a tight_box record exists but has no placement"
        elif frame_size is None:
            dimension_note = "timeline frame dimensions were unavailable"
        elif measured_size is None:
            dimension_note = "media dimensions were unreadable"
        else:
            dimension_note = (
                f"media dimensions {measured_size} do not match frame "
                f"{frame_size}")
    return None, (
        f"V{caption_row}[{si}] {seg_id}: {geometry_label} caption segment "
        f"with no tight_box placement - refusing to place it "
        f"{dimension_note + ' - ' if dimension_note else ''}"
        f"untransformed (an untransformed tight canvas sits centred "
        f"at Tilt 0, inside the picture, not on the declared row)")


def source_frame_span_for_timeline(source_in: float, source_out: float,
                                   timeline_in_frame: int,
                                   timeline_out_frame: int,
                                   timeline_fps: float,
                                   source_fps: float,
                                   label: str) -> tuple:
    """Map a planned record span to source frames at 100% speed.

    Round the timeline boundaries once, then derive the duration from
    their difference. Rounding source in and out independently can yield
    a different duration for the same seconds: B8's 0.836-3.234s source
    rounds to 72 frames, while its 7.185-9.583s record span is 71 frames.
    The source start stays anchored to the requested in point; its end is
    derived from the exact planned duration. A difference greater than a
    frame between the requested source length and the planned duration is
    a named refusal.
    """
    try:
        source_in = float(source_in)
        source_out = float(source_out)
        source_fps = float(source_fps)
        timeline_fps = float(timeline_fps)
        timeline_length = int(timeline_out_frame) - int(timeline_in_frame)
    except (TypeError, ValueError, OverflowError) as exc:
        return None, None, (
            f"{label}: source/timeline frame mapping is unreadable "
            f"({type(exc).__name__}: {exc})")
    if (not all(math.isfinite(value) for value in
                (source_in, source_out, source_fps, timeline_fps))
            or source_fps <= 0 or timeline_fps <= 0
            or source_out <= source_in or timeline_length <= 0):
        return None, None, (
            f"{label}: invalid source/timeline span or frame rate "
            f"({source_fps:g} source fps, {timeline_fps:g} timeline fps, "
            f"{timeline_length} timeline frames)")
    start = round(source_in * source_fps)
    requested_end = round(source_out * source_fps)
    length = round(timeline_length * source_fps / timeline_fps)
    if length <= 0:
        return None, None, (
            f"{label}: planned timeline span maps to no source frames at "
            f"{source_fps:g} source fps")
    requested_length = requested_end - start
    if abs(requested_length - length) > 1:
        return None, None, (
            f"{label}: source range is {requested_length} frames but its "
            f"planned timeline span maps to {length} source frames at "
            f"100% speed; refusing a difference larger than frame-rounding")
    return start, start + length, ""


def v1_plan_overlap_refusal(planned_start_frame: int,
                            planned_end_frame: int,
                            previous_planned_start_frame: int,
                            previous_planned_end_frame: int,
                            previous_label: str, clip_label: str) -> str:
    """Name any overlap declared by two V1 plan entries."""
    overlap = min(int(planned_end_frame), int(previous_planned_end_frame)) - max(
        int(planned_start_frame), int(previous_planned_start_frame))
    if overlap <= 0:
        return ""
    unit = "frame" if overlap == 1 else "frames"
    return (f"V1 placement for {clip_label} overlaps planned item "
            f"{previous_label} by {overlap} {unit}; refusing the overlap")


def timeline_frame_rate_refusal(set_result, rate_readback,
                                requested_fps: float) -> str:
    """Refuse a timeline whose frame grid does not match the manifest."""
    try:
        actual_fps = float(rate_readback)
    except (TypeError, ValueError, OverflowError):
        return (f"Resolve returned an unreadable timeline frame rate "
                f"({rate_readback!r}) for requested {requested_fps:g} fps")
    try:
        requested_fps = float(requested_fps)
    except (TypeError, ValueError, OverflowError):
        return f"Manifest timeline frame rate is unreadable ({requested_fps!r})"
    if set_result is False:
        return (f"Resolve refused the timeline frame-rate setting for "
                f"{requested_fps:g} fps (read back {rate_readback!r})")
    if (not math.isfinite(actual_fps) or not math.isfinite(requested_fps)
            or actual_fps <= 0 or requested_fps <= 0
            or abs(actual_fps - requested_fps) > 0.0001):
        return (f"Resolve timeline frame rate mismatch: requested "
                f"{requested_fps:g} fps, read back {rate_readback!r}. "
                "Refusing placement on a different frame grid")
    return ""


def project_frame_rate_refusal(project, requested_fps: float) -> str:
    """Ensure OTIO rebuilds inherit the frame rate the placement used.

    Resolve's audio-mix round trip rebuilds the timeline from project
    settings. A per-timeline rate can read back correctly when first set,
    then revert to the project's rate on that rebuild. Initialize the
    project rate only while it has no timelines; changing an established
    project's rate could alter the captain's existing timelines.
    """
    try:
        requested = float(requested_fps)
    except (TypeError, ValueError, OverflowError):
        return f"Manifest frame rate is unreadable ({requested_fps!r})"
    if not math.isfinite(requested) or requested <= 0:
        return f"Manifest frame rate must be positive and finite, got {requested!r}"

    try:
        timeline_count = int(project.GetTimelineCount())
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        return ("Resolve did not return a readable timeline count for its "
                f"project ({type(exc).__name__}: {exc}); refusing to set "
                "the project frame rate")

    key = "timelineFrameRate"
    try:
        current_raw = project.GetSetting(key)
        current = float(current_raw)
    except (AttributeError, TypeError, ValueError, OverflowError):
        current_raw = None
        current = None
    if (current is not None and math.isfinite(current)
            and abs(current - requested) <= 0.0001):
        return ""

    rate = str(int(requested)) if requested.is_integer() else str(requested)
    if timeline_count:
        return (
            f"Resolve project frame rate mismatch: requested {requested:g} "
            f"fps, project reads {current_raw!r}, and {timeline_count} "
            "existing timeline(s) prevent changing the project frame rate. "
            "The audio-mix OTIO rebuild inherits the project rate; use an "
            "empty Resolve project at the requested frame rate.")

    try:
        set_result = project.SetSetting(key, rate)
        readback = project.GetSetting(key)
        actual = float(readback)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        return (f"Resolve could not set and read back project frame rate "
                f"{requested:g} fps ({type(exc).__name__}: {exc})")
    if (set_result is not True or not math.isfinite(actual)
            or abs(actual - requested) > 0.0001):
        return (f"Resolve refused the project frame-rate setting: requested "
                f"{requested:g} fps, SetSetting returned {set_result!r}, "
                f"read back {readback!r}")
    return ""


def project_timeline_shape_refusal(project, requested_width: int,
                                   requested_height: int) -> str:
    """Do not resize the project underneath timelines that inherit it.

    Resolve rescales stored Pan/Tilt when the project timeline resolution
    changes. That is expected for a timeline using project settings, but it
    silently rewrites existing framing. New timelines are made custom below;
    existing timelines must already be custom before this build changes the
    project resolution.
    """
    try:
        raw_requested = (float(requested_width), float(requested_height))
        if any(not math.isfinite(value) or not value.is_integer()
               for value in raw_requested):
            raise ValueError("resolution dimensions must be integers")
        requested = tuple(int(value) for value in raw_requested)
    except (TypeError, ValueError, OverflowError):
        return ("Manifest timeline resolution is unreadable: requested "
                f"{requested_width!r}x{requested_height!r}")
    if any(value <= 0 for value in requested):
        return ("Manifest timeline resolution must be positive, got "
                f"{requested[0]}x{requested[1]}")

    keys = ("timelineResolutionWidth", "timelineResolutionHeight")
    current_raw = []
    current = []
    for key in keys:
        try:
            raw = project.GetSetting(key)
            numeric = float(raw)
            if not math.isfinite(numeric) or not numeric.is_integer():
                raise ValueError(f"not an integer resolution: {raw!r}")
            current_raw.append(raw)
            current.append(int(numeric))
        except (AttributeError, TypeError, ValueError, OverflowError):
            current_raw.append(None)
            current.append(None)
    if tuple(current) == requested:
        return ""

    try:
        timeline_count = int(project.GetTimelineCount())
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        return ("Resolve did not return a readable timeline count for its "
                f"project ({type(exc).__name__}: {exc}); refusing to set "
                "the project timeline resolution")
    if timeline_count < 0:
        return (f"Resolve returned an invalid timeline count "
                f"({timeline_count}); refusing to set the project timeline "
                "resolution")
    if timeline_count == 0:
        return ""

    old_shape = (f"{current_raw[0]}x{current_raw[1]}"
                 if all(value is not None for value in current_raw)
                 else f"{current_raw[0]!r}x{current_raw[1]!r}")
    for index in range(1, timeline_count + 1):
        try:
            timeline = project.GetTimelineByIndex(index)
        except Exception as exc:  # noqa: BLE001 - Resolve proxy may fail
            return ("Resolve could not inspect existing timeline "
                    f"{index} ({type(exc).__name__}: {exc}); refusing to "
                    "change the project timeline resolution")
        if timeline is None:
            return ("Resolve did not return existing timeline "
                    f"{index} of {timeline_count}; refusing to change the "
                    "project timeline resolution")
        try:
            use_custom_settings = timeline.GetSetting("useCustomSettings")
        except Exception as exc:  # noqa: BLE001 - fail closed on unknown
            return ("Resolve could not read whether existing timeline "
                    f"{index} uses custom settings "
                    f"({type(exc).__name__}: {exc}); refusing to change "
                    "the project timeline resolution")
        if str(use_custom_settings).strip() != "1":
            try:
                name = timeline.GetName()
            except Exception:  # noqa: BLE001 - keep the refusal actionable
                name = f"at index {index}"
            return (
                f"Resolve project timeline resolution mismatch: requested "
                f"{requested[0]}x{requested[1]}, project reads {old_shape}, "
                f"and existing timeline {name!r} uses project settings. "
                "Changing the project resolution rescales stored Pan/Tilt; "
                "set existing timelines to custom settings before building.")
    return ""


def caption_block_offsets(v1_clips, placed_by_label) -> dict:
    """{spine block index: measured V1 start offset} for caption placement.

    Each placed A-roll clip's live start minus its planned start is the
    offset its block's captions shift by. A block whose placed item will
    not answer for its start (`GetStart` raising `AttributeError`) is
    LEFT OUT: the caption loop below already skips blocks missing from
    this map, so an unreadable start reads as an unplaced block rather
    than publishing offset 0 - "aligned" - with no measurement behind
    it. Labels that never parse as `speech_N`/`hook_N` are not blocks
    and never enter the map.
    """
    block_offsets = {}
    for clip in v1_clips or []:
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
                            print(f"  ⚠ {label}: placed item reports "
                                  f"no start, leaving block "
                                  f"{block_idx} out of the caption "
                                  f"offsets", file=sys.stderr)
                            continue
                        estimated_start = clip.get('timeline_in_frame', 0)
                        block_offsets[block_idx] = actual_start - estimated_start
            except ValueError:
                pass
    return block_offsets


class SpeechChannelRefused(ValueError):
    """No speech channel reaches the timeline by default.

    The 6.01 build used to place every angle's audio on an undeclared
    `program_channel: 1` - stream 0 dressed as the mix, the same failure
    `ProgramStreamRefused` stops at the catalog. Which channel of a
    multi-channel source is the program mix is declared (the manifest
    angle, or the project's `source.program_stream`) or recorded (the
    catalog's selection); a single-channel source is its own answer.
    Anything else refuses, naming the angle and the source.
    """


def read_declared_program_stream(project_folder: str):
    """The project's `source.program_stream` declaration, or None.

    Read through `footage_identity` - the one module that owns footage
    properties - so the declaration works whether or not the key
    reached the run's broadcast `project_config`. Anything that is not
    a positive int reads as undeclared here; the schema
    (`ProjectConfig.validate`) is what tells the project its
    declaration is malformed.
    """
    if not project_folder:
        return None
    try:
        from library.tools.footage_identity import (
            declared_program_stream)
    except ImportError:
        return None
    try:
        return declared_program_stream(project_folder)
    except Exception:
        return None


def read_catalog_program_channels(project_folder: str):
    """`({basename: channel}, {basename: refusal}, {basename: reason})`.

    The catalog's own route - `pipeline_data.json`, the file a step's
    output is guaranteed to have landed in - read the way
    `reel_build.catalog_program_channels` reads it, without importing
    that module for one lookup. A project whose catalog predates stream
    recording comes back empty on both.
    """
    channels: dict = {}
    refusals: dict = {}
    reasons: dict = {}
    if not project_folder:
        return channels, refusals, reasons
    try:
        with open(ProjectLayout(project_folder).pipeline_data_path,
                  encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return channels, refusals, reasons
    from library.tools import capability_outputs
    entries = capability_outputs.value(
        state, "footage.catalog", "clip_catalog") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source_file") or entry.get("path") or ""
        if not source:
            continue
        keys = {source, source.rsplit("/", 1)[-1]}
        selection = entry.get("program_stream") or {}
        try:
            channel = int(selection.get("channel"))
        except (TypeError, ValueError):
            channel = None
        if channel is not None and channel >= 1:
            for key in keys:
                channels.setdefault(key, channel)
            reason = selection.get("reason")
            if reason:
                for key in keys:
                    reasons.setdefault(key, str(reason))
            continue
        refusal = entry.get("program_stream_refusal")
        if refusal:
            for key in keys:
                refusals.setdefault(key, str(refusal))
    return channels, refusals, reasons


def resolve_speech_channel(angle_key: str, angle_label: str,
                           manifest_channel,
                           source_basenames,
                           catalog_channels: dict,
                           catalog_refusals: dict,
                           declared_channel,
                           single_stream_basenames,
                           catalog_reasons: dict | None = None) -> tuple:
    """The speech channel for one angle: `(channel, basis)`, or a refusal.

    Precedence - the project's stated preference first, then what was
    recorded, then what is mechanically certain:
    manifest angle declaration, `source.program_stream` declaration,
    the catalog's recorded selection, a single-stream source (there is
    nothing else it could be). A multi-stream source with none of those
    raises SpeechChannelRefused naming the angle and the source: the
    mix is declared or measured, never stream 0 dressed as the mix.
    Every source on the angle must resolve to ONE channel; sources
    that disagree refuse the same way.
    """
    try:
        manifest_channel = (None if manifest_channel is None
                            else int(manifest_channel))
    except (TypeError, ValueError):
        raise SpeechChannelRefused(
            f"REFUSING to place speech for angle {angle_label!r}: its "
            f"manifest declares program_channel "
            f"{manifest_channel!r}, which is not a channel ordinal.")
    if manifest_channel is not None:
        return manifest_channel, "manifest angle declaration"
    if declared_channel is not None:
        return int(declared_channel), "source.program_stream declaration"
    if not source_basenames:
        raise SpeechChannelRefused(
            f"REFUSING to place speech for angle {angle_label!r}: no "
            f"source clips, so no program stream can be resolved.")
    resolved: dict = {}
    catalog_reasons = catalog_reasons or {}
    for basename in sorted(source_basenames):
        channel = catalog_channels.get(basename)
        basis = "the catalog's recorded program stream"
        reason = catalog_reasons.get(basename)
        if reason:
            basis += f"; {reason}"
        if channel is None and basename in single_stream_basenames:
            channel, basis = 1, "single-stream source"
        if channel is None:
            refusal = catalog_refusals.get(basename)
            raise SpeechChannelRefused(
                f"REFUSING to place speech for angle {angle_label!r}: "
                f"{basename} carries multiple audio streams and no "
                f"program stream is declared or recorded"
                + (f" - {refusal}" if refusal else "") + ". Declare "
                f"source.program_stream in the project's project.yaml "
                f"and re-run catalog_footage.")
        resolved[basename] = (int(channel), basis)
    distinct = {channel for channel, _ in resolved.values()}
    if len(distinct) != 1:
        raise SpeechChannelRefused(
            f"REFUSING to place speech for angle {angle_label!r}: its "
            f"sources resolve to different program streams - "
            + ", ".join(f"{s} CH{c}" for s, (c, _) in
                        sorted(resolved.items())))
    channel = next(iter(distinct))
    basis = "; ".join(sorted({b for _, b in resolved.values()}))
    return channel, basis


def mapping_carries_program(channels, expected) -> bool:
    """Whether a placed item's channel mapping carries the program stream.

    `channels` is the `channel_idx` list read off the item's
    `GetSourceAudioChannelMapping` (track 1), `expected` the angle's
    resolved program channel. A mapping CARRYING the program channel
    stays - a stereo speech item maps `CH[1, 2]` and carries program
    CH1 in it. The exact-equality this replaces (`channels == [1]`)
    deleted every such stereo item as "non-program audio" and built
    iPhone footage with no dialogue at all while reporting success
    (finding 4: "A1 non-program audio removed" x12, export -91 dB
    over the spoken hook).
    """
    return expected in list(channels or [])


class _PhaseClock:
    """Where the master build's Resolve hold goes, one phase at a time.

    `build_timeline` holds Resolve for its whole body, so the lease's
    `resolve_hold` row is one number. Each `lap` closes the running
    phase and appends it to the performance ledger as
    `edit_placement.<phase>` the moment it ends, so `ren profile` can
    rank the phases even when a later one fails. Ledger only: nothing
    the build returns or places changes, and with no ledger in the
    environment (`perf_ledger.LEDGER_ENV`) a lap records nothing.
    What the phases measured live: docs/EDIT_VIDEO_BUILD_HOLD_MEASURED.md.
    """

    def __init__(self, timeline_name: str):
        self.timeline_name = timeline_name
        self.phase = None
        self.started = 0.0

    def lap(self, phase: Optional[str]) -> None:
        now = time.perf_counter()
        if self.phase is not None:
            try:
                from library.tools import perf_ledger
                perf_ledger.record(
                    f"edit_placement.{self.phase}", now - self.started,
                    phase=self.phase, timeline=self.timeline_name)
            except Exception:  # noqa: BLE001 - timing never fails a build
                pass
        self.phase, self.started = phase, now


def _apply_neural_engine_directives(
        manifest, timeline, v1_labels, v2_labels, results):
    """Apply compiled per-item Neural Engine directives.

    Stabilization is guarded at the last boundary before Resolve: even a
    stale or hand-edited manifest cannot call ``TimelineItem.Stabilize``
    without the typed user authorization that compile_manifest records.
    """
    neural_directives = manifest.get("neural_engine_directives", {})
    if not neural_directives or apply_stabilization is None:
        return

    print(f"\n── Neural Engine: {len(neural_directives)} clips ──",
          file=sys.stderr)

    def apply_track(track, labels):
        items = timeline.GetItemListInTrack("video", 1 if track == "V1" else 2) or []
        for ci, label in enumerate(labels):
            if label not in neural_directives or ci >= len(items):
                continue
            directives = neural_directives[label]
            tl_clip = items[ci]
            stabilization = directives.get("stabilize")
            if stabilization:
                if (not isinstance(stabilization, dict)
                        or stabilization.get("format")
                        != STABILIZATION_AUTHORIZATION_FORMAT
                        or not stabilization.get("authorizations")):
                    raise ValueError(
                        f"Refusing Resolve Stabilize on {track}{ci} {label}: "
                        "the directive has no valid user authorization record"
                    )
                ids = ", ".join(
                    str(record["authorization_id"])
                    for record in stabilization["authorizations"]
                )
                ok = apply_stabilization(tl_clip)
                mark = "✓" if ok else "✗"
                print(
                    f"  {mark} [{track}{ci}] {label}: Stabilization "
                    f"(authorized by {ids})",
                    file=sys.stderr,
                )
                if not ok:
                    results["warnings"].append(
                        f"Stabilization refused on {track}{ci} {label}")
            if directives.get("super_scale"):
                ok = apply_super_scale(
                    tl_clip, scale_factor=directives["super_scale"])
                mark = "✓" if ok else "✗"
                print(f"  {mark} [{track}{ci}] {label}: Super Scale "
                      f"{directives['super_scale']}x", file=sys.stderr)
                if not ok:
                    results["warnings"].append(
                        f"Super Scale refused on {track}{ci} {label}")

    apply_track("V1", v1_labels)
    apply_track("V2", v2_labels)


def _prepare_timeline_build(
    manifest: dict,
    subtitle_overlay_path: Optional[str],
    motion_graphics_path: Optional[str],
    project_folder: str,
) -> dict:
    """Validate inputs and build the Resolve-independent placement plan.

    This runs before either machine resource admission or the Resolve
    cursor lease. The only plan detail that still needs Resolve is the
    legacy single-channel fallback; its read-back is filled into the
    already-built plan after the pool is read under the lease.
    """
    errors = _preflight_check(manifest)
    if errors:
        return {"errors": errors}

    project_settings = manifest["project"]
    timeline_name = project_settings["name"]
    width, height = project_settings["resolution"][:2]
    # The timeline grid is the manifest's product delivery rate. Individual
    # source timebases are read from their pool items later, inside the lease.
    fps = float(project_settings.get("frame_rate", 30))
    total_duration = project_settings.get("duration_seconds", 46.0)
    tracks = manifest.get("tracks", {})
    v1_clips = tracks.get("V1", {}).get("clips", [])
    v2_clips = tracks.get("V2", {}).get("clips", [])
    a2_clips = tracks.get("A2", {}).get("clips", [])
    a1_voiceover_clips = [
        clip for clip in tracks.get("A1", {}).get("clips", [])
        if isinstance(clip, dict) and clip.get("voiceover")]
    a3_clips = tracks.get("A3", {}).get("clips", [])
    room_tone_fills = manifest.get("room_tone_fills", []) or []
    jl_cut_plans = manifest.get("jl_cuts", []) or []

    path_state = {}

    def path_exists(path):
        if not path:
            return False
        if path not in path_state:
            path_state[path] = os.path.exists(path)
        return path_state[path]

    sub_overlay_info = manifest.get("subtitle_overlay", {})
    mg_overlay_info = manifest.get("motion_graphics_overlay", {})
    tt_overlay_info = manifest.get("timed_text_overlay", {})
    if sub_overlay_info.get("available") is False:
        sub_overlay_info = {}
        print("  ⚠ Subtitles marked as not available, skipping", file=sys.stderr)
    if mg_overlay_info.get("available") is False:
        mg_overlay_info = {}
        print("  ⚠ Motion graphics marked as not available, skipping",
              file=sys.stderr)
    sub_segments = sub_overlay_info.get("segments", [])
    mg_segments = mg_overlay_info.get("segments", [])
    tt_segments = tt_overlay_info.get("segments", [])

    if (not sub_segments and subtitle_overlay_path
            and path_exists(subtitle_overlay_path)):
        sub_segments = [{
            "overlay_path": subtitle_overlay_path,
            "timeline_start": 0,
            "timeline_end": total_duration,
            "total_frames": round(total_duration * fps),
        }]
    if not sub_segments:
        legacy_sub = sub_overlay_info.get("overlay_path", "")
        if legacy_sub and path_exists(legacy_sub):
            sub_segments = [{
                "overlay_path": legacy_sub,
                "timeline_start": 0,
                "timeline_end": total_duration,
                "total_frames": round(total_duration * fps),
            }]
    if (not mg_segments and motion_graphics_path
            and path_exists(motion_graphics_path)):
        mg_segments = [{
            "overlay_path": motion_graphics_path,
            "timeline_start": 0,
            "timeline_end": total_duration,
            "total_frames": round(total_duration * fps),
        }]
    if not mg_segments:
        legacy_mg = mg_overlay_info.get("overlay_path", "")
        if legacy_mg and path_exists(legacy_mg):
            mg_segments = [{
                "overlay_path": legacy_mg,
                "timeline_start": 0,
                "timeline_end": total_duration,
                "total_frames": round(total_duration * fps),
            }]

    for clip in v1_clips + v2_clips + a2_clips + a3_clips:
        if "source_in" in clip:
            clip["source_in_frame"] = round(clip["source_in"] * fps)
        if "source_out" in clip:
            clip["source_out_frame"] = round(clip["source_out"] * fps)
        if "timeline_in" in clip:
            clip["timeline_in_frame"] = round(clip["timeline_in"] * fps)
        if "timeline_out" in clip:
            clip["timeline_out_frame"] = round(clip["timeline_out"] * fps)
    for segment in sub_segments + mg_segments + tt_segments:
        if "source_in" in segment:
            segment["source_in_frame"] = round(segment["source_in"] * fps)
        if "source_out" in segment:
            segment["source_out_frame"] = round(segment["source_out"] * fps)
        if "timeline_in" in segment:
            segment["timeline_in_frame"] = round(segment["timeline_in"] * fps)
        if "timeline_out" in segment:
            segment["timeline_out_frame"] = round(segment["timeline_out"] * fps)
        if "source_in_frame" not in segment:
            segment["total_frames"] = round(
                (segment.get("timeline_end", 0)
                 - segment.get("timeline_start", 0)) * fps)

    declared_angles = [angle for angle in (manifest.get("angles") or [])
                       if angle.get("key")]
    declared_by_key = {angle["key"]: angle for angle in declared_angles}
    marked_keys = []
    for clip in v1_clips:
        key = clip.get("angle")
        if key and key not in marked_keys:
            marked_keys.append(key)
    if marked_keys:
        default_angle = marked_keys[0]
        angle_keys = list(marked_keys)
    elif declared_angles:
        default_angle = declared_angles[0]["key"]
        angle_keys = [angle["key"] for angle in declared_angles]
    else:
        default_angle = "main"
        angle_keys = ["main"]
    for clip in v1_clips:
        clip.setdefault("angle", default_angle)

    angle_sources = {}
    for clip in v1_clips:
        if clip.get("video_only"):
            continue
        source = clip.get("source_file", "")
        if source:
            angle_sources.setdefault(
                clip.get("angle", default_angle), set()).add(source)

    (catalog_channels, catalog_refusals,
     catalog_reasons) = read_catalog_program_channels(project_folder)
    declared_channel = read_declared_program_stream(project_folder)
    material_angles = []
    pending_speech_angles = {}
    speech_bases = {}
    for key in angle_keys:
        if not marked_keys and not declared_angles:
            break
        declaration = declared_by_key.get(key, {})
        label = declaration.get("label") or key
        angle_files = {path.rsplit("/", 1)[-1]
                       for path in angle_sources.get(key, set())}
        if not angle_files:
            try:
                channel = int(declaration.get("program_channel")
                              or declared_channel or 1)
            except (TypeError, ValueError):
                channel = int(declared_channel or 1)
            basis = "no clips on this angle - row naming only"
        else:
            manifest_channel = declaration.get("program_channel")
            can_resolve_without_pool = (
                manifest_channel is not None
                or declared_channel is not None
                or all(name in catalog_channels for name in angle_files))
            try:
                if can_resolve_without_pool:
                    channel, basis = resolve_speech_channel(
                        key, label, manifest_channel, angle_files,
                        catalog_channels, catalog_refusals,
                        declared_channel, set(), catalog_reasons)
                else:
                    channel = 1
                    basis = "Resolve pool read-back pending"
                    pending_speech_angles[key] = angle_files
            except SpeechChannelRefused as exc:
                return {"errors": [str(exc)]}
        speech_bases[key] = basis
        material_angles.append({
            "key": key,
            "label": label,
            "speech_name": declaration.get("speech_name")
            or f"{label} CH{channel}",
            "program_channel": channel,
        })

    spine_blocks = manifest.get("_spine_blocks", []) or []
    if spine_blocks:
        spine_has_speech = (
            any(isinstance(block, dict)
                and block.get("block_type") in SPEECH_BLOCK_TYPES
                for block in spine_blocks)
            or bool(a1_voiceover_clips))
    else:
        spine_has_speech = (
            any(not clip.get("video_only") and not clip.get("picture_led")
                for clip in v1_clips)
            or bool(a1_voiceover_clips))

    def span_seconds(start_s, end_s):
        return [round((start_s or 0) * fps), round((end_s or 0) * fps)]

    material = {
        "angles": material_angles,
        "has_broll": bool(v2_clips),
        "v1_intentionally_empty": not v1_clips,
        "speech_row_intentionally_empty": not spine_has_speech,
        "picture_led_spans": [
            [round(float(block.get("timeline_start", 0)) * fps),
             round(float(block.get("timeline_end", 0)) * fps)]
            for block in spine_blocks
            if isinstance(block, dict)
            and block.get("block_type") == "picture"],
        "caption_spans": [span_seconds(segment.get("timeline_start"),
                                        segment.get("timeline_end"))
                          for segment in sub_segments],
        "mg_spans": [span_seconds(segment.get("timeline_start"),
                                   segment.get("timeline_end"))
                     for segment in mg_segments],
        "has_generators": bool(manifest.get("generator_overlays", [])),
        "timed_text_spans": [span_seconds(segment.get("timeline_start"),
                                            segment.get("timeline_end"))
                             for segment in tt_segments],
        "music_spans": [span_seconds(clip.get("timeline_in"),
                                      clip.get("timeline_out", total_duration))
                        for clip in a2_clips],
        "sfx_spans": [span_seconds(clip.get("timeline_in", 0),
                                    clip.get("timeline_out",
                                             clip.get("timeline_in", 0)))
                      for clip in a3_clips],
    }
    try:
        track_plan = plan_layout(material)
    except (TypeError, ValueError) as exc:
        return {"errors": [f"Could not build timeline placement plan: {exc}"]}

    legacy_sources = {
        clip.get("source_file", "").rsplit("/", 1)[-1]
        for clip in v1_clips if clip.get("source_file")
        and not clip.get("video_only")}
    legacy_fallback_needed = (
        not marked_keys and not declared_angles and bool(legacy_sources))
    legacy_program_channel = None
    legacy_program_basis = ""
    if legacy_fallback_needed:
        if (declared_channel is not None
                or all(name in catalog_channels for name in legacy_sources)):
            try:
                legacy_program_channel, legacy_program_basis = resolve_speech_channel(
                    "main", "main", None, legacy_sources,
                    catalog_channels, catalog_refusals, declared_channel,
                    set(), catalog_reasons)
            except SpeechChannelRefused as exc:
                return {"errors": [str(exc)]}

    results = {
        "success": False,
        "timeline_name": timeline_name,
        "tracks": {},
        "errors": [],
        "warnings": [],
        "qa_failures": [],
    }
    source_paths = [clip.get("source_file", "")
                    for clip in (v1_clips + v2_clips + a2_clips + a3_clips
                                 + a1_voiceover_clips + room_tone_fills)]
    overlay_paths = [segment.get("overlay_path", "")
                     for segment in sub_segments + mg_segments + tt_segments]
    for segment in sub_segments:
        frame_dir = ((segment.get("frames") or {}).get("dir", "")
                     if segment.get("container") == "frames" else "")
        if frame_dir:
            overlay_paths.extend(sequence_frame_paths(frame_dir))
    available_paths = {path for path in source_paths + overlay_paths
                       if path_exists(path)}
    fusion_script_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "tools", "execution", "apply_fusion_comps.py")
    carrier_exists = None
    if material["has_generators"]:
        _, carrier_path = _transparent_carrier_paths(
            project_folder, width, height, fps)
        carrier_exists = path_exists(carrier_path)

    return {
        "errors": [],
        "project_settings": project_settings,
        "timeline_name": timeline_name,
        "width": width,
        "height": height,
        "fps": fps,
        "total_duration": total_duration,
        "tracks": tracks,
        "v1_clips": v1_clips,
        "v2_clips": v2_clips,
        "a2_clips": a2_clips,
        "a1_voiceover_clips": a1_voiceover_clips,
        "a3_clips": a3_clips,
        "room_tone_fills": room_tone_fills,
        "jl_cut_plans": jl_cut_plans,
        "spine_has_speech": spine_has_speech,
        "sub_segments": sub_segments,
        "mg_segments": mg_segments,
        "tt_segments": tt_segments,
        "results": results,
        "available_paths": available_paths,
        "track_plan": track_plan,
        "angle_sources": angle_sources,
        "declared_by_key": declared_by_key,
        "catalog_channels": catalog_channels,
        "catalog_refusals": catalog_refusals,
        "catalog_reasons": catalog_reasons,
        "declared_channel": declared_channel,
        "pending_speech_angles": pending_speech_angles,
        "speech_bases": speech_bases,
        "legacy_sources": legacy_sources,
        "legacy_fallback_needed": legacy_fallback_needed,
        "legacy_program_channel": legacy_program_channel,
        "legacy_program_basis": legacy_program_basis,
        "fusion_script_path": fusion_script_path,
        "fusion_script_exists": path_exists(fusion_script_path),
        "carrier_exists": carrier_exists,
    }


def _finalize_prepared_speech_channels(prepared, find_pool_clip):
    """Resolve only legacy channel fallbacks from leased pool read-backs."""
    single_channel_sources = set()
    pending_sources = set(prepared["legacy_sources"]
                          if prepared["legacy_fallback_needed"]
                          and prepared["legacy_program_channel"] is None
                          else ())
    for key in prepared["pending_speech_angles"]:
        pending_sources.update(prepared["angle_sources"].get(key, set()))
    for source in pending_sources:
        pool_item = find_pool_clip(source)
        if pool_item is None:
            continue
        try:
            audio_channels = int(str(
                pool_item.GetClipProperty("Audio Ch")).strip())
        except (Exception, TypeError, ValueError):
            continue
        if audio_channels == 1:
            single_channel_sources.add(source.rsplit("/", 1)[-1])

    if (prepared["legacy_fallback_needed"]
            and prepared["legacy_program_channel"] is None):
        try:
            (prepared["legacy_program_channel"],
             prepared["legacy_program_basis"]) = resolve_speech_channel(
                "main", "main", None, prepared["legacy_sources"],
                prepared["catalog_channels"],
                prepared["catalog_refusals"],
                prepared["declared_channel"], single_channel_sources,
                prepared["catalog_reasons"])
        except SpeechChannelRefused as exc:
            prepared["results"]["errors"].append(str(exc))
            print(f"  ✗ {exc}", file=sys.stderr)
            return str(exc)

    for angle in prepared["track_plan"].material["angles"]:
        key = angle["key"]
        if key not in prepared["pending_speech_angles"]:
            continue
        declaration = prepared["declared_by_key"].get(key, {})
        label = declaration.get("label") or key
        try:
            channel, basis = resolve_speech_channel(
                key, label, declaration.get("program_channel"),
                prepared["pending_speech_angles"][key],
                prepared["catalog_channels"],
                prepared["catalog_refusals"],
                prepared["declared_channel"], single_channel_sources,
                prepared["catalog_reasons"])
        except SpeechChannelRefused as exc:
            prepared["results"]["errors"].append(str(exc))
            print(f"  ✗ {exc}", file=sys.stderr)
            return str(exc)
        angle["program_channel"] = channel
        prepared["speech_bases"][key] = basis
        if not declaration.get("speech_name"):
            angle["speech_name"] = f"{angle['label']} CH{channel}"

    for angle in prepared["track_plan"].material["angles"]:
        print(f"  Speech for angle {angle['label']!r}: program "
              f"CH{angle['program_channel']} "
              f"({prepared['speech_bases'][angle['key']]})",
              file=sys.stderr)

    names_by_angle = {angle["key"]: angle["speech_name"]
                      for angle in prepared["track_plan"].material["angles"]}
    for track in prepared["track_plan"].audio_tracks:
        if track.role == "speech" and track.occupant in names_by_angle:
            track.name = names_by_angle[track.occupant]
    prepared["results"]["track_plan"] = (
        prepared["track_plan"].serializable())
    return ""


@under_lease("render the edit timeline", capability="render.build",
             phase="placement")
@heavy_work_locked("edit timeline placement", "render.build:placement")
def _build_timeline_under_lease(
    manifest: dict,
    subtitle_overlay_path: Optional[str] = None,
    motion_graphics_path: Optional[str] = None,
    project_name: Optional[str] = None,
    project_folder: str = "",
    _prepared: Optional[dict] = None,
) -> dict:
    """Build a complete Resolve timeline from an assembly manifest.

    Args:
        manifest: Assembly manifest dict with tracks, transitions, etc.
        subtitle_overlay_path: Legacy single-file path (fallback)
        motion_graphics_path: Legacy single-file path (fallback)
        project_name: Resolve project name (creates or loads)

    Returns:
        dict with build results and verification data
    """
    if _prepared is None:
        raise RuntimeError(
            "build_timeline preparation must finish before the Resolve lease")
    prepared = _prepared
    if prepared["errors"]:
        return {"success": False, "errors": prepared["errors"]}

    project_settings = prepared["project_settings"]
    timeline_name = prepared["timeline_name"]
    _clock = _PhaseClock(timeline_name)
    _clock.lap("plan_overlays")
    width, height = prepared["width"], prepared["height"]
    fps = prepared["fps"]
    total_duration = prepared["total_duration"]
    tracks = prepared["tracks"]
    v1_clips = prepared["v1_clips"]
    v2_clips = prepared["v2_clips"]
    a2_clips = prepared["a2_clips"]
    a1_voiceover_clips = prepared["a1_voiceover_clips"]
    a3_clips = prepared["a3_clips"]
    room_tone_fills = prepared["room_tone_fills"]
    jl_cut_plans = prepared["jl_cut_plans"]
    _spine_has_speech = prepared["spine_has_speech"]
    sub_segments = prepared["sub_segments"]
    mg_segments = prepared["mg_segments"]
    tt_segments = prepared["tt_segments"]
    results = prepared["results"]
    available_paths = prepared["available_paths"]
    track_plan = prepared["track_plan"]

    qa_reports = []
    def _run_qa(report):
        if not report: return
        qa_reports.append(report)
        if not report.passed:
            for check in report.checks:
                if not check.passed and check.severity == "error":
                    msg = f"QA [{report.station}] Failed {check.name}: expected {check.expected}, got {check.actual}"
                    print(f"  ✗ {msg}", file=sys.stderr)
                    results["qa_failures"].append({
                        "station": report.station,
                        "check": check.name,
                        "expected": check.expected,
                        "actual": check.actual,
                        "detail": msg,
                    })

    _clock.lap("connect")
    # ── Connect to Resolve ──
    try:
        resolve = _connect_resolve()
    except ConnectionError as e:
        results["errors"].append(str(e))
        return results

    pm = resolve.GetProjectManager()
    if project_name:
        project = resolve_project_exactly(pm, project_name)
    else:
        project = pm.GetCurrentProject()

    media_pool = project.GetMediaPool()

    _clock.lap("pool_import")
    # ── Import all media to pool with subdirectory organization ──
    # Note: We import media BEFORE creating timeline to detect actual FPS.
    root_folder = media_pool.GetRootFolder()

    # What the pool already holds, BEFORE this build adds anything.
    # Judged defensively: on fakes and older proxies these calls can
    # answer anything, and a scan that raises must read as "nothing
    # pooled", never as a failed build.
    _pooled_paths = set()
    try:
        _stack = [root_folder]
        while _stack:
            _folder = _stack.pop()
            try:
                _clips = list(_folder.GetClipList() or [])
            except (Exception, TypeError):
                _clips = []
            for _c in _clips:
                try:
                    _fp = _c.GetClipProperty("File Path") or ""
                except Exception:
                    _fp = ""
                if _fp:
                    _pooled_paths.add(_fp)
            try:
                _stack.extend(list(_folder.GetSubFolderList() or []))
            except (Exception, TypeError):
                pass
    except Exception:
        _pooled_paths = set()

    def _import_to_folder(folder_name, paths):
        # The pool is not a scratch dir: media that is already pooled is
        # NOT imported again. ImportMedia never dedupes (measured
        # 2026-09-09: re-importing one pooled MXF grew the pool 221 to
        # 222), so importing blindly litters a shared project with a
        # duplicate per source per build. Exact-path match only: a mere
        # basename match could be a different file with the same name.
        wanted = [p for p in paths if p and p in available_paths]
        skipped = [p for p in wanted if p in _pooled_paths]
        if skipped:
            results.setdefault("pool_dedupe_skipped", []).extend(skipped)
            print(f"  Pool already holds {len(skipped)} file(s) for "
                  f"{folder_name}, not re-importing", file=sys.stderr)
        paths = [p for p in wanted if p not in _pooled_paths]
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
    # Every import folder is named by `resolve_bin_layout` - the single
    # owner of bin paths. Source picture and sound file under the
    # captain's own `Source footage`; subtitle, motion-graphics and
    # timed-text renders (all 4.05/4.06-family products) under their
    # render bins, where the organiser's per-timeline filing keeps them.
    total_imported += _import_to_folder(
        bin_layout.SOURCE_BIN, [c.get('source_file', '') for c in v1_clips])
    total_imported += _import_to_folder(
        bin_layout.SOURCE_BIN, [c.get('source_file', '') for c in v2_clips])
    total_imported += _import_to_folder(
        bin_layout.SOURCE_BIN,
        [c.get('source_file', '') for c in a2_clips + a3_clips])
    # Room-tone fills ride the same bin as the fetched bed and SFX:
    # pipeline audio the build placed, filed by the organiser under
    # the timelines that place it.
    total_imported += _import_to_folder(
        bin_layout.SOURCE_BIN,
        [c.get('source_file', '') for c in room_tone_fills])
    # Voiceover narration rides no V1 carrier, so its files arrive
    # through no other import: without this the A1 placement below
    # finds nothing pooled and the words never reach the timeline.
    total_imported += _import_to_folder(
        bin_layout.SOURCE_BIN,
        [c.get('source_file', '') for c in a1_voiceover_clips])
    # A sequence arrives as its frame files in one call, which is what
    # groups them into a single image-sequence pool item.
    sub_paths = [s.get('overlay_path', '') for s in sub_segments]
    for s in sub_segments:
        frame_dir = ((s.get('frames') or {}).get('dir', '')
                     if s.get('container') == 'frames' else '')
        if frame_dir:
            sub_paths += sequence_frame_paths(frame_dir)
    total_imported += _import_to_folder(
        bin_layout.SUBTITLES_BIN, sub_paths)
    total_imported += _import_to_folder(
        bin_layout.MOTION_GRAPHICS_BIN,
        [s.get('overlay_path', '') for s in mg_segments])
    total_imported += _import_to_folder(
        bin_layout.MOTION_GRAPHICS_BIN,
        [s.get('overlay_path', '') for s in tt_segments])
    
    if total_imported > 0:
        print(f"✓ Imported {total_imported} media files into subfolders", file=sys.stderr)

    _clock.lap("pool_scan")
    # Build pool clip lookup.
    root_folder = media_pool.GetRootFolder()
    pool_clips_by_path = {}
    pool_clips_by_name = {}
    # An image sequence reports one File Path with a bracket range
    # (`dir/frame-[00-46].png`), so no single frame path matches it.
    # The frame directory does - and frame directories are per segment,
    # so the mapping stays one to one.
    pool_sequences_by_dir = {}

    def _scan_folder(folder):
        for clip in (folder.GetClipList() or []):
            name = clip.GetName()
            filepath = clip.GetClipProperty("File Path") or ""
            if filepath:
                pool_clips_by_path[filepath] = clip
                if '[' in filepath:
                    pool_sequences_by_dir[
                        os.path.dirname(filepath.split('[')[0])] = clip
            pool_clips_by_name[name] = clip
        for sub in (folder.GetSubFolderList() or []):
            _scan_folder(sub)

    _scan_folder(root_folder)

    def _find_pool_clip(filepath: str) -> object:
        """Look up a media pool clip by filepath first, then basename fallback."""
        item = pool_clips_by_path.get(filepath)
        if item: return item
        return pool_clips_by_name.get(os.path.basename(filepath))

    def _find_pool_sequence(frame_dir: str) -> object:
        """The pool item for a rendered frame directory, or None."""
        return pool_sequences_by_dir.get(
            os.path.normpath(frame_dir or ""))

    pool_clips = pool_clips_by_name  # Prefer _find_pool_clip() for all new code
    print(f"  Media pool: {len(pool_clips_by_name)} clips ({len(pool_clips_by_path)} with paths)", file=sys.stderr)

    _clock.lap("fps_and_track_plan")
    channel_error = _finalize_prepared_speech_channels(
        prepared, _find_pool_clip)
    if channel_error:
        return results
    track_plan = prepared["track_plan"]
    results["track_plan"] = track_plan.serializable()
    results["stream_enforcement"] = {"checked": 0, "deleted": []}
    results["link_groups"] = []
    results["caption_links"] = []
    results["deleted_empty_tracks"] = []
    print(f"  Track plan: "
          f"{[(t.index, t.name) for t in track_plan.video_tracks]} / "
          f"{[(t.index, t.name) for t in track_plan.audio_tracks]}",
          file=sys.stderr)

    _clock.lap("timeline_name_check")
    # ── Auto-increment timeline name to accumulate drafts ──
    # The project declares a base name (like Pipeline_Edit).
    # We append a timestamp and duration to satisfy the captain's request:
    # "how do I tell which short is which" and "how do I compare drafts".
    import datetime
    base_name = timeline_name
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    duration_str = f"_{int(total_duration)}s" if total_duration else ""
    
    existing_names = set()
    for i in range(1, project.GetTimelineCount() + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            existing_names.add(tl.GetName())
            
    draft = 1
    while True:
        suffix = f"_{draft:02d}" if draft > 1 else ""
        candidate = f"{base_name}_{timestamp}{duration_str}{suffix}"
        if candidate not in existing_names and f"{candidate}{PREMIX_SUFFIX}" not in existing_names:
            timeline_name = candidate
            results["timeline_name"] = timeline_name
            break
        draft += 1

    # ── A name already in use is a REFUSAL, never a deletion ──
    #
    # This block used to delete every timeline carrying the build's name,
    # and step 6.01 called it with `delete_existing=True` unconditionally
    # against a hardcoded "Pipeline_Edit".  So a second render silently
    # destroyed the timeline the captain had spent a review annotating,
    # and the only thing standing between them and that loss was
    # `guard_timeline_deletion` - which passes once the notes have been
    # collected, because collected notes are no longer AT RISK as data.
    # A Text+ block is not a note, nothing collects one, and it went with
    # the timeline.
    #
    # The build now never deletes a timeline it did not create in this
    # run.  A collision is refused by name, and the fix is one line in
    # the project's own project.yaml (`resolve.timeline_name`), which is
    # what the captain asked for: "can you not just call it pipeline edit
    # 2 or something and then render it?"
    #
    # `<name>__premix` is checked too: the audio-mix round trip parks the
    # placement timeline under that name for the length of one import, so
    # a run killed mid-import leaves one behind, and building over it
    # would fail later and less clearly.
    taken = []
    for i in range(project.GetTimelineCount(), 0, -1):
        tl = project.GetTimelineByIndex(i)
        if tl and tl.GetName() in {timeline_name,
                                   f"{timeline_name}{PREMIX_SUFFIX}"}:
            taken.append(tl.GetName())
    if taken:
        results["errors"].append(
            f"Refusing to build: {' and '.join(sorted(taken))} already "
            f"exist(s) in this Resolve project, and this build does not "
            f"delete a timeline it did not create. Choose another name "
            f"with `resolve.timeline_name` in the project's project.yaml, "
            f"or rename the existing timeline in Resolve."
        )
        results["timeline_name_taken"] = sorted(taken)
        return results

    timeline_fps_str = str(int(fps)) if fps.is_integer() else str(fps)
    _project_fps_refusal = project_frame_rate_refusal(project, fps)
    if _project_fps_refusal:
        results["errors"].append(_project_fps_refusal)
        return results
    _project_timeline_shape_refusal = project_timeline_shape_refusal(
        project, width, height)
    if _project_timeline_shape_refusal:
        results["errors"].append(_project_timeline_shape_refusal)
        return results

    _clock.lap("timeline_create")
    # ── Create empty timeline ──
    timeline = media_pool.CreateEmptyTimeline(timeline_name)
    if not timeline:
        results["errors"].append("Failed to create timeline")
        return results

    # Establishing the cursor goes through the guard, like every write
    # below: setting it directly bypasses the lease refusal and the
    # fence's drift record, and an unleased move is what killed a
    # sibling lane's Fusion pass on 2026-09-20.
    assert_current_timeline(project, timeline)

    # The shape and frame rate go on the PROJECT, not only on the timeline.
    #
    # The audio mix round trip re-imports the timeline through OTIO
    # (AGENTS.md 5, "the import REBUILDS the timeline"), and the rebuilt
    # one inherits the PROJECT's resolution - so per-timeline custom
    # settings applied here are discarded a few hundred lines later. A
    # fresh Resolve project defaults to 1920x1080, so project 001 built a
    # correct 1080x1920 timeline and delivered a landscape master, and
    # every structural check passed on it: duration, framerate, audio
    # streams, even frame occupancy at 100%. It stayed hidden for the life
    # of the pipeline because the one project it had ever rendered into
    # had been set to vertical BY HAND.
    #
    # Every call is judged by what it RETURNS (AGENTS.md 5): the old code
    # discarded four return values and then printed a tick carrying the
    # manifest's numbers, which is why the log said 1080x1920 while the
    # timeline was 1920x1080.
    # The shape is confirmed by READING IT BACK, never by trusting the
    # write. `SetSetting` returning True is a claim; `GetSetting` is the
    # evidence, and it is the evidence that decides whether the build
    # continues. Measured on Resolve 21 (2026-08-29): these keys commit
    # synchronously, 20/20 immediate reads matched, so the retry below is
    # a bound rather than a wait - it costs nothing when the first read
    # already agrees and it does not encode a settle time nobody measured.
    #
    # The project frame rate is established before the timeline is
    # created: the timeline's own setting can read back at 30fps and then
    # revert to the project's 24fps when OTIO rebuilds it. A project with
    # existing timelines cannot be changed safely, so
    # `project_frame_rate_refusal` names that mismatch before placement.
    def _confirm(obj, key, value, attempts=5):
        """Write, then read back. Returns the value Resolve reports."""
        before = obj.GetSetting(key)
        if str(before) == str(value):
            return str(before)
        for _ in range(attempts):
            obj.SetSetting(key, str(value))
            got = obj.GetSetting(key)
            if str(got) == str(value):
                return str(got)
        return str(obj.GetSetting(key))

    shape = (("timelineResolutionWidth", width),
             ("timelineResolutionHeight", height),
             ("timelineOutputResolutionWidth", width),
             ("timelineOutputResolutionHeight", height))

    wrong = []
    for key, value in shape:
        got = _confirm(project, key, value)
        if got != str(value):
            wrong.append(f"project.{key}: asked {value}, reads {got}")

    timeline.SetSetting("useCustomSettings", "1")
    for key, value in (("timelineResolutionWidth", width),
                       ("timelineResolutionHeight", height)):
        _confirm(timeline, key, value)
    _fps_set_result = timeline.SetSetting(
        "timelineFrameRate", timeline_fps_str)
    _fps_readback = timeline.GetSetting("timelineFrameRate")
    _fps_refusal = timeline_frame_rate_refusal(
        _fps_set_result, _fps_readback, fps)

    if wrong:
        results["errors"].append(
            "Resolve will not hold the timeline shape: " + "; ".join(wrong)
            + ". The render would inherit whatever the project already "
            "held, which is how a vertical edit ships as landscape.")
        return results
    if _fps_refusal:
        results["errors"].append(_fps_refusal)
        return results

    print(f"✓ Created timeline: {timeline_name} "
          f"({project.GetSetting('timelineResolutionWidth')}x"
          f"{project.GetSetting('timelineResolutionHeight')} @ "
          f"{float(_fps_readback):g}fps, read back from Resolve)",
          file=sys.stderr)

    _clock.lap("track_setup")
    # ── Set up tracks: the plan's rows, and only those ──
    # Video rows are created up front. Speech audio rows are added one
    # angle at a time during placement below, so an angle's audio never
    # lands while a later row exists to catch a spill; music and SFX
    # rows come after all speech is placed. A row exists because the
    # plan put something on it: nothing here is sized from what MIGHT
    # be placed.
    has_v2 = bool(v2_clips)
    has_subtitles = bool(sub_segments)
    has_mg = bool(mg_segments)
    has_timed_text = bool(tt_segments)
    generator_overlays = manifest.get('generator_overlays', [])
    has_generators = bool(generator_overlays)

    # The bed is allocated FIRST, because a crossfade puts two music
    # clips on the timeline at once and the SFX bucket has to start above
    # whatever the bed used. Bases come from the plan: speech rows first,
    # then the bed, then SFX - so with two speech rows the bed starts at
    # A3, exactly as the SOP's fixed order says.
    n_speech_rows = len(track_plan.speech_rows())
    music_allocations = _allocate_audio_tracks(a2_clips,
                                               base_track_index=n_speech_rows + 1,
                                               fps=fps)
    max_music_track = max((t for _, t in music_allocations),
                          default=n_speech_rows)
    sfx_allocations = _allocate_audio_tracks(
        a3_clips, base_track_index=max_music_track + 1, fps=fps)

    # The packing must land on the plan's rows, exactly. A packing that
    # disagrees with the plan is a builder bug, and placing against it
    # anyway is how rows appear that the plan never named.
    _planned_audio_rows = {t.index for t in track_plan.audio_tracks}
    _off_plan = sorted({r for _, r in music_allocations + sfx_allocations}
                       - _planned_audio_rows)
    if _off_plan:
        results["errors"].append(
            f"Audio packing disagrees with the track plan on rows "
            f"{_off_plan}; refusing to place against an unnamed layout.")
        return results
    num_audio_tracks_needed = max(
        [t.index for t in track_plan.audio_tracks] or [1])

    # Add video rows (V1 exists, add V2+). Every row added here is on
    # the plan; occupancy is enforced after placement, so a row whose
    # placements all fail is DELETED, never kept blank.
    while timeline.GetTrackCount("video") < len(track_plan.video_tracks):
        timeline.AddTrack("video")

    vt = timeline.GetTrackCount("video")
    print(f"✓ Video tracks: V={vt} (plan: "
          f"{[(t.index, t.name) for t in track_plan.video_tracks]})",
          file=sys.stderr)
    print(f"  (Speech audio rows are added one angle at a time, "
          f"music/SFX rows after speech placement)", file=sys.stderr)

    _program_channel = {
        angle["key"]: angle["program_channel"]
        for angle in track_plan.material["angles"]}
    if not _program_channel and prepared["legacy_fallback_needed"]:
        # The legacy single-camera manifest: no angles declared, no
        # clips marked, one speech row for everything. The channel for
        # it resolves the same way - declaration, recording, then the
        # single-stream mechanical answer - and refuses the same way.
        # The old `or {"main": 1}` put every undeclared multi-stream
        # source on stream 0 without a word said.
        _main_channel = prepared["legacy_program_channel"]
        if _main_channel is None:
            raise RuntimeError(
                "legacy speech channel was not resolved before placement")
        print(f"  Speech for the single row: program "
              f"CH{_main_channel} ({prepared['legacy_program_basis']})",
              file=sys.stderr)
        _program_channel = {"main": _main_channel}

    def _enforce_program_stream(angle_key, placed_items, label):
        """Only mappings carrying the recorded program stream stay on a
        speech row.

        Every audio AppendToTimeline return is read back: items whose
        channel mapping is not the angle's recorded program channel are
        deleted from the timeline on the spot and recorded. An item
        whose mapping cannot be read is KEPT and reported - an
        unreadable check must not delete picture, and it must not read
        as a passing one either.
        """
        expected = _program_channel.get(angle_key)
        if expected is None:
            # Unreachable when the resolution above ran: every angle
            # with clips resolved or refused the build. Kept and
            # reported rather than defaulted, so a future caller that
            # reaches here without resolving still cannot place an
            # unchosen stream silently.
            results["warnings"].append(
                f"{label}: no resolved program channel for this angle - "
                f"kept, UNVERIFIED")
            return list(placed_items or [])
        kept = []
        for item in placed_items or []:
            results["stream_enforcement"]["checked"] += 1
            try:
                mapping = json.loads(item.GetSourceAudioChannelMapping())
                channels = (mapping.get("track_mapping", {})
                            .get("1", {}).get("channel_idx", []))
            except Exception as exc:
                results["warnings"].append(
                    f"{label}: placed audio mapping unreadable ({exc}) - "
                    f"kept, UNVERIFIED")
                kept.append(item)
                continue
            if mapping_carries_program(channels, expected):
                kept.append(item)
            else:
                try:
                    timeline.DeleteClips([item], False)
                except Exception as exc:
                    results["errors"].append(
                        f"{label}: stray CH{channels} item could not be "
                        f"removed: {exc}")
                    continue
                results["stream_enforcement"]["deleted"].append(
                    {"label": label, "expected_channel": expected,
                     "placed_channels": list(channels or [])})
                print(f"  ✗ {label}: placed CH{channels}, program is "
                      f"CH{expected} - removed", file=sys.stderr)
        return kept

    _clock.lap("place_a_roll")
    # ══════════════════════════════════════════════════════════
    # PLACE A-ROLL, one angle per picture row, each with its speech row
    # ══════════════════════════════════════════════════════════
    # Each camera angle is its own row: picture on the plan's a-roll row
    # for the angle, speech on the plan's speech row for the same angle.
    # The angle's speech row is added just before its first audio item,
    # so no audio is ever placed while a later row exists to catch a
    # spill. Linking happens AFTER the mix round trip, in one span-based
    # pass (the import rebuilds the timeline and rebinds every handle).
    print(f"\n── A-Roll + Speech by angle: {len(v1_clips)} clips ──",
          file=sys.stderr)
    v1_timeline_items = []
    v1_placed_labels = []
    placed_by_row = {}

    # Preserve planned V1 boundaries before placement read-back updates
    # the mutable clip rows for later caption offset calculations.
    _planned_v1_spans = {}
    for ci, clip in enumerate(v1_clips):
        src_in = clip.get('source_in', 0)
        src_out = clip.get('source_out')
        if src_out is None:
            dur = clip.get('timeline_out', 0) - clip.get('timeline_in', 0)
            src_out = src_in + dur if dur > 0 else src_in + 3.5
        planned_in = clip.get('timeline_in_frame')
        if planned_in is None:
            planned_in = round(float(clip.get('timeline_in', 0)) * fps)
        planned_out = clip.get('timeline_out_frame')
        if planned_out is None:
            planned_out = (
                round(float(clip['timeline_out']) * fps)
                if clip.get('timeline_out') is not None
                else int(planned_in) + round(
                    (float(src_out) - float(src_in)) * fps))
        _planned_v1_spans[id(clip)] = (
            int(planned_in), int(planned_out),
            float(src_in), float(src_out),
            float(clip.get('audio_src_in', src_in)),
            float(clip.get('audio_src_out', src_out)),
        )

    _clips_by_angle = {}
    for clip in v1_clips:
        _clips_by_angle.setdefault(clip.get("angle", "main"), []).append(clip)

    for _angle in track_plan.aroll_rows():
        _angle_key = _angle.occupant
        _vrow = _angle.index
        _speech = track_plan.speech_row_for_angle(_angle_key)
        if _speech is None:
            results["errors"].append(
                f"Track plan has a picture row for angle {_angle_key!r} "
                f"with no speech row; refusing to place it unlinked.")
            continue
        _arow = _speech.index
        while timeline.GetTrackCount("audio") < _arow:
            timeline.AddTrack("audio")
        _placed_here = []
        for ci, clip in enumerate(_clips_by_angle.get(_angle_key, [])):
            current_video_frame = clip.get('timeline_in_frame', 0)
            # BUG FIX C7: Handle clips with missing source_file gracefully
            src = clip.get('source_file', '')
            if not src:
                results["warnings"].append(
                    f"V{_vrow}[{ci}] ({clip.get('label', '?')}) missing source_file - skipped")
                print(f"  ⚠ [{ci}] {clip.get('label', '?')}: missing source_file", file=sys.stderr)
                continue
            basename = os.path.basename(src)
            pool_item = _find_pool_clip(src)
            if not pool_item:
                results["errors"].append(f"V{_vrow}[{ci}] {basename} not in media pool")
                continue

            (_planned_tl_in, _planned_tl_out, _video_source_in,
             _video_source_out, _audio_source_in,
             _audio_source_out) = _planned_v1_spans[id(clip)]
            tl_in_f = _planned_tl_in
            _source_rate = _source_fps(pool_item, fps)
            v_in, v_out, _frame_error = source_frame_span_for_timeline(
                _video_source_in, _video_source_out,
                _planned_tl_in, _planned_tl_out, fps, _source_rate,
                clip.get('label', basename))
            if _frame_error:
                results["errors"].append(_frame_error)
                print(f"  ✗ {_frame_error}", file=sys.stderr)
                continue
            a_in, a_out, _audio_frame_error = (
                source_frame_span_for_timeline(
                    _audio_source_in, _audio_source_out,
                    _planned_tl_in, _planned_tl_out, fps, _source_rate,
                    f"{clip.get('label', basename)} dialogue audio"))
            if _audio_frame_error and not clip.get("video_only"):
                results["errors"].append(_audio_frame_error)
                print(f"  ✗ {_audio_frame_error}", file=sys.stderr)
                continue
            clip['video_src_in'] = v_in
            clip['video_src_out'] = v_out
            clip['audio_src_in'] = a_in
            clip['audio_src_out'] = a_out

            # Shared frame boundaries make only plan-declared overlap a
            # placement error. Refuse it before Resolve silently moves
            # the item away from the record frame in the manifest.
            if _placed_here:
                _plan_refusal = ""
                for _, _previous_clip in _placed_here:
                    _previous_span = _planned_v1_spans[id(_previous_clip)]
                    _plan_refusal = v1_plan_overlap_refusal(
                        _planned_tl_in, _planned_tl_out,
                        _previous_span[0], _previous_span[1],
                        _previous_clip.get("label", "previous V1 clip"),
                        clip.get("label", basename))
                    if _plan_refusal:
                        break
                if _plan_refusal:
                    results["errors"].append(_plan_refusal)
                    print(f"  ✗ {_plan_refusal}", file=sys.stderr)
                    continue

            # Place Video (plan row, video-only)
            assert_current_timeline(project, timeline)
            v_res = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": v_in,
                "endFrame": v_out,
                "trackIndex": _vrow,
                "recordFrame": tl_in_f,
                "mediaType": 1
            }])

            # Calculate Audio Record Frame to maintain sync
            a_rec = tl_in_f + round(
                (_audio_source_in - _video_source_in) * fps)

            # Place Audio (plan row, audio-only, explicit). A clip marked
            # video_only has no audio to place - a declared intro / outro /
            # end card is a silent card unless its template said otherwise -
            # and asking Resolve for an audio item from a file with no
            # audio stream returns nothing while looking like a failed
            # placement in the log.
            if clip.get("video_only"):
                a_res = None
            else:
                assert_current_timeline(project, timeline)
                a_res = media_pool.AppendToTimeline([{
                    "mediaPoolItem": pool_item,
                    "startFrame": a_in,
                    "endFrame": a_out,
                    "trackIndex": _arow,
                    "recordFrame": a_rec,
                    "mediaType": 2
                }])

            if v_res:
                placed = v_res[0] if isinstance(v_res, list) else v_res
                a_list = (a_res if isinstance(a_res, list)
                          else ([a_res] if a_res else []))
                a_label = f"A{_arow}[{ci}] {clip.get('label', basename)}"
                kept = _enforce_program_stream(_angle_key, a_list, a_label)
                a_placed = kept[0] if kept else None

                _apply_conform(
                    placed, clip, results, frame_size=(width, height),
                    write_context={
                        "project_folder": project_folder,
                        "project": project_name,
                        "timeline_name": timeline_name,
                    },
                    item_identity={
                        "source_file": clip["source_file"],
                        "track": f"V{_vrow}",
                        "record_frame": tl_in_f,
                    })
                v1_timeline_items.append(placed)
                _placed_here.append((placed, clip))
                v1_placed_labels.append(clip.get('label', basename))

                placed_dur = placed.GetDuration()
                clip['timeline_in_frame'] = tl_in_f
                clip['timeline_out_frame'] = tl_in_f + placed_dur
                clip['timeline_in'] = tl_in_f / fps
                clip['timeline_out'] = (tl_in_f + placed_dur) / fps

                audio_note = ("silent (video_only)" if clip.get("video_only")
                              else (f"A{_arow} {a_in}-{a_out} at {a_rec}"
                                    if a_placed else
                                    f"A{_arow} non-program audio removed"))
                print(f"  ✓ [{ci}] {clip.get('label', basename)}: "
                      f"V{_vrow} {v_in}-{v_out} at {tl_in_f}, {audio_note}", file=sys.stderr)

                # No per-item Fairlight application here. A stub used to
                # call apply_fairlight_preset(a_placed, preset) and print
                # "Applied Fairlight preset" for an EQ nothing applied -
                # the API has no per-item EQ controls, so it is removed
                # (library/tools/fairlight_presets.py). The preset name
                # still reaches the one real application, the
                # timeline-level ApplyFairlightPresetToCurrentTimeline
                # call below, which is judged by what Resolve returns.

            else:
                results["errors"].append(f"V{_vrow}[{ci}] AppendToTimeline failed for {basename}")
                print(f"  ✗ [{ci}] {basename}: AppendToTimeline returned None", file=sys.stderr)

        results["tracks"][f"V{_vrow}"] = len(_placed_here)
        results["tracks"][f"A{_arow}"] = sum(
            1 for _, c in _placed_here if not c.get("video_only"))
        placed_by_row[f"V{_vrow}"] = ([p for p, _ in _placed_here],
                                      [c for _, c in _placed_here])

    if verify_clip_placement:
        for _row, (_items, _clips) in placed_by_row.items():
            _run_qa(verify_clip_placement(timeline, {_row: _items}, {_row: _clips}))

    _clock.lap("place_room_tone")
    # ══════════════════════════════════════════════════════════
    # PLACE ROOM-TONE FILLS (J/L joins, fidelity rung R5a)
    # ══════════════════════════════════════════════════════════
    # The speech row is continuous by construction: each J/L trim
    # opened exactly one gap and each fill below closes one. Fills
    # ride the angle's own speech row as audio-only items (the SFX
    # placement shape below), placed HERE - after speech, before the
    # music/SFX rows exist - and BEFORE deliver_mix, so the OTIO
    # round trip carries them like every other audio item. Each
    # placement is judged by read-back: a fill that did not land is
    # a gap left as digital silence, so it errors the build rather
    # than warning past it. The link pass joins each fill to its
    # picture group after the rebuild.
    _fill_placed = []  # (fill record, timeline item)
    results["jl_cuts"] = []
    if room_tone_fills:
        print(f"\n── Room-tone fills: {len(room_tone_fills)} gap(s) ──",
              file=sys.stderr)
        _fill_speech = track_plan.speech_row_for_angle("main")
        if _fill_speech is None:
            _rows = track_plan.speech_rows()
            _fill_speech = _rows[0] if _rows else None
        if _fill_speech is None:
            results["errors"].append(
                "J/L room-tone fills planned with no speech row; "
                "refusing to place them unrowed.")
        else:
            _frow = _fill_speech.index
            while timeline.GetTrackCount("audio") < _frow:
                timeline.AddTrack("audio")
            for fill in room_tone_fills:
                _fsrc = fill.get('source_file', '')
                _flabel = fill.get('label', '?')
                if not _fsrc:
                    results["errors"].append(
                        f"Room-tone fill {_flabel} names no source_file - "
                        f"its gap would ship as digital silence.")
                    continue
                _fpool = _find_pool_clip(_fsrc)
                if not _fpool:
                    results["errors"].append(
                        f"Room-tone fill {_flabel} ({_fsrc}) is not in "
                        f"the media pool - its gap would ship as "
                        f"digital silence.")
                    continue
                _f_tl_in = fill.get('timeline_in_frame')
                _f_tl_out = fill.get('timeline_out_frame')
                if _f_tl_in is None or _f_tl_out is None:
                    results["errors"].append(
                        f"Room-tone fill {_flabel} carries no timeline "
                        f"frames - nothing says where its gap is.")
                    continue
                _f_src_fps = _source_fps(_fpool, fps)
                _f_src_in = round(fill.get('source_in', 0) * _f_src_fps)
                _f_src_dur = round(((_f_tl_out - _f_tl_in) / fps)
                                   * _f_src_fps)
                assert_current_timeline(project, timeline)
                _f_res = media_pool.AppendToTimeline([{
                    "mediaPoolItem": _fpool,
                    "startFrame": _f_src_in,
                    "endFrame": _f_src_in + _f_src_dur,
                    "trackIndex": _frow,
                    "recordFrame": _f_tl_in,
                    "mediaType": 2,  # audio-only placement
                }])
                if not _f_res:
                    results["errors"].append(
                        f"Room-tone fill {_flabel} at {_f_tl_in}: "
                        f"AppendToTimeline returned nothing - its gap "
                        f"would ship as digital silence.")
                    continue
                _f_item = (_f_res[0] if isinstance(_f_res, list)
                           else _f_res)
                try:
                    _f_span = (_f_item.GetStart(), _f_item.GetEnd(),
                               _f_item.GetDuration())
                except Exception as exc:
                    results["errors"].append(
                        f"Room-tone fill {_flabel} placed but unreadable "
                        f"({exc}) - an unjudged placement claims "
                        f"nothing.")
                    continue
                if (_f_span[0] != _f_tl_in
                        or _f_span[0] + _f_span[2] != _f_tl_out):
                    results["errors"].append(
                        f"Room-tone fill {_flabel} read back "
                        f"{_f_span[0]}-{_f_span[0] + _f_span[2]} "
                        f"against planned {_f_tl_in}-{_f_tl_out} - the "
                        f"gap is not exactly covered.")
                    continue
                _fill_placed.append((fill, _f_item))
                results["tracks"][f"A{_frow}"] = (
                    results["tracks"].get(f"A{_frow}", 0) + 1)
                print(f"  ✓ {_flabel}: A{_frow} {_f_span[0]}-"
                      f"{_f_span[0] + _f_span[2]} "
                      f"({fill.get('level_dbfs')} dBFS room)",
                      file=sys.stderr)

    _clock.lap("audio_rows")
    # ══════════════════════════════════════════════════════════
    # NOW create music and SFX audio rows (AFTER speech — so they start clean)
    # ══════════════════════════════════════════════════════════
    while timeline.GetTrackCount("audio") < num_audio_tracks_needed:
        timeline.AddTrack("audio")

    at = timeline.GetTrackCount("audio")
    print(f"✓ Audio tracks added: A={at} (speech rows, then bed, then SFX)", file=sys.stderr)

    _clock.lap("place_b_roll")
    # ══════════════════════════════════════════════════════════
    # PLACE B-ROLL (plan row, video-only)
    # ══════════════════════════════════════════════════════════
    _broll_row = next((t.index for t in track_plan.video_tracks
                       if t.role == "b_roll"), None)
    if v2_clips:
        if _broll_row is None:
            results["errors"].append(
                "B-roll clips planned with no b-roll row; refusing.")
            return results
        print(f"\n── V{_broll_row} B-Roll: {len(v2_clips)} clips ──", file=sys.stderr)
        v2_count = 0
        v2_placed_labels = []
        for ci, clip in enumerate(v2_clips):
            basename = os.path.basename(clip['source_file'])
            pool_item = _find_pool_clip(clip['source_file'])
            if not pool_item:
                results["warnings"].append(f"V{_broll_row}[{ci}] {basename} not in pool")
                continue

            src_in = clip.get('source_in', 0)
            src_out = clip.get('source_out')
            if not src_out:
                dur = clip.get('timeline_out', 0) - clip.get('timeline_in', 0)
                src_out = src_in + dur if dur > 0 else src_in + 3.5
            src_in_f = round(src_in * fps)
            src_out_f = round(src_out * fps)
            tl_in_f = clip['timeline_in_frame']

            assert_current_timeline(project, timeline)
            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_out_f,
                "trackIndex": _broll_row,
                "recordFrame": tl_in_f,
                "mediaType": 1,  # video-only placement on the b-roll row
            }])

            if result:
                placed_v2 = result[0] if isinstance(result, list) else result
                _apply_conform(
                    placed_v2, clip, results, frame_size=(width, height),
                    write_context={
                        "project_folder": project_folder,
                        "project": project_name,
                        "timeline_name": timeline_name,
                    },
                    item_identity={
                        "source_file": clip["source_file"],
                        "track": f"V{_broll_row}",
                        "record_frame": tl_in_f,
                    })
                v2_count += 1
                v2_placed_labels.append(clip.get('label', basename))
                print(f"  ✓ [{ci}] {clip.get('label', basename)}: TL {tl_in_f}", file=sys.stderr)
            else:
                print(f"  ✗ [{ci}] {basename}: failed", file=sys.stderr)

        results["tracks"][f"V{_broll_row}"] = v2_count

    _clock.lap("native_operations")
    # ══════════════════════════════════════════════════════════
    # NATIVE RESOLVE OPERATIONS (fidelity rung 3b)
    # ══════════════════════════════════════════════════════════
    # Speed ramps, freezes and Resolve's own transitions reach the
    # timeline here - through `TimelineItem.SetSpeed` /
    # `TimelineItem.AddTransition`, each judged by its read-back
    # (`library/tools/native_ops_apply.py`). After picture placement
    # (the ops address placed items by span) and before the Fusion
    # pass (comps are keyed to the placed ranges; neither op moves a
    # cut - plan speed ops never ripple, and a centered transition
    # consumes handles, not timeline).
    _native_speed_ops = manifest.get("native_speed_ops", []) or []
    _native_trans = manifest.get("native_transitions", []) or []
    if _native_speed_ops or _native_trans:
        from library.tools import native_ops_apply as _native_apply
        print(f"\n── Native Resolve ops: {len(_native_speed_ops)} speed, "
              f"{len(_native_trans)} transition(s) ──", file=sys.stderr)
        try:
            assert_current_timeline(project, timeline)
        except Exception as exc:
            results["errors"].append(f"native ops refused: {exc}")
        else:
            if _native_speed_ops:
                # Dialogue rows only for the linked-audio half: the bed
                # and SFX rows never ride a picture retime (finding 17).
                _dialogue_tracks = [r.index for r in
                                    track_plan.speech_rows()]
                _speed_report = _native_apply.apply_native_speed_ops(
                    timeline, _native_speed_ops, fps=fps,
                    dialogue_tracks=_dialogue_tracks)
                results["native_speed_ops"] = _speed_report
                for row in _speed_report["applied"]:
                    print(f"  ✓ {row['op_id']} {row['item']!r}: "
                          f"{row['percent']:g}% "
                          f"({row['verified']})", file=sys.stderr)
                for row in _speed_report["failed"]:
                    msg = (f"native speed {row['op_id']}: {row['what']}")
                    results["errors"].append(msg)
                    print(f"  ✗ {msg}", file=sys.stderr)
            if _native_trans:
                try:
                    _ordered_v1 = sorted(
                        v1_timeline_items, key=lambda it: it.GetStart())
                except Exception as exc:
                    results["errors"].append(
                        "native transitions refused: V1 items would not "
                        f"order ({exc})")
                    _ordered_v1 = None
                # The V2 items for transitions planned into the b-roll
                # (finding 16): read back off the b-roll row in
                # timeline order, the same order the compile indexed
                # `after_clip` into. None where the timeline carries
                # no b-roll row - a V2 op then refuses by name in the
                # applicator rather than landing on V1's clips.
                _ordered_v2 = None
                if _broll_row is not None:
                    try:
                        _ordered_v2 = sorted(
                            timeline.GetItemListInTrack(
                                "video", _broll_row) or [],
                            key=lambda it: it.GetStart())
                    except Exception as exc:
                        results["errors"].append(
                            "native transitions refused: V2 items would "
                            f"not order ({exc})")
                        _ordered_v1 = None
                if _ordered_v1 is not None:
                    _trans_report = (
                        _native_apply.apply_native_transitions(
                            timeline, _ordered_v1, _native_trans, fps=fps,
                            v2_items=_ordered_v2))
                    results["native_transitions"] = _trans_report
                    for row in _trans_report["applied"]:
                        print(f"  ✓ {row['transition_id']} "
                              f"{row['type']!r} ({row['category']}) "
                              f"({row['verified']})", file=sys.stderr)
                    for row in _trans_report["failed"]:
                        msg = (f"native transition "
                               f"{row['transition_id']}: {row['what']}")
                        results["errors"].append(msg)
                        print(f"  ✗ {msg}", file=sys.stderr)

    _clock.lap("place_captions")
    # ══════════════════════════════════════════════════════════
    # PLACE CAPTIONS (plan row, video-only overlays)
    # ══════════════════════════════════════════════════════════
    _caption_row = (track_plan.caption_row().index
                    if track_plan.caption_row() is not None else None)
    if has_subtitles:
        if _caption_row is None:
            results["errors"].append(
                "Subtitle segments planned with no caption row; refusing.")
            return results
        print(f"\n── V{_caption_row} Subtitle Overlay: {len(sub_segments)} segments ──", file=sys.stderr)
        
        # Build mapping from spine block position -> actual V1 timeline position offset
        placed_by_label = dict(zip(v1_placed_labels, v1_timeline_items))
        block_offsets = caption_block_offsets(v1_clips, placed_by_label)

        import re
        v3_count = 0
        for si, seg in enumerate(sub_segments):
            seg_path = seg.get('overlay_path', '')
            seg_basename = os.path.basename(seg_path)
            frames_info = seg.get('frames') or {}
            frame_dir = frames_info.get('dir', '') if seg.get(
                'container') == 'frames' else ''
            if frame_dir:
                seg_basename = os.path.basename(frame_dir.rstrip('/'))

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

            if frame_dir:
                pool_item = _find_pool_sequence(frame_dir)
            else:
                pool_item = _find_pool_clip(seg_path)
            if not pool_item:
                results["warnings"].append(f"V{_caption_row}[{si}] {seg_basename} not in pool")
                print(f"  ✗ [{si}] {seg_basename} not in media pool", file=sys.stderr)
                continue

            # The clip attributes an alpha artefact needs, decided by
            # the FILE - see library/tools/overlay_carriage.py. Not
            # just the alpha mode: a `qtrle` overlay whose Data Level
            # is left on Resolve's default `Auto` is read as video
            # range and composites the WHOLE frame 16/255 dark,
            # including every pixel where the overlay is transparent.
            apply_clip_attributes(pool_item, seg_path)

            offset_f = block_offsets.get(block_idx, 0) if block_idx is not None else 0

            # Trim the rendered animation handles: place the clip on its
            # TRUE content bounds so adjacent blocks do not overlap. A
            # frame sequence shares the mov's frame numbering - the
            # render covers the same padded span - so the trim is the
            # same arithmetic.
            src_in_f = seg.get('source_in_frame', 0)
            src_out_f = seg.get('source_out_frame')
            if src_out_f is None:
                src_out_f = src_in_f + seg.get('total_frames', round(
                    (seg.get('timeline_end', 0) - seg.get('timeline_start', 0)) * fps))
            seg_frames = src_out_f - src_in_f
            tl_in_frame = round(seg.get('timeline_start', 0) * fps)

            # Shift the subtitle's timeline_start by the offset
            tl_in_frame += offset_f

            # The caption artefact rides the placement its tight box
            # computed - read off the entry step 4.05 recorded - and
            # the placer SETS it then READS BACK what Resolve holds.
            # See library/tools/overlay_placement.py.
            # A tight segment with no placement never reaches the
            # placer: `caption_segment_placement` refuses it by name
            # (finding 21 - an untransformed tight canvas sits
            # centred at Tilt 0, inside the picture). What ships is
            # either row-placed or absent, never silently centred.
            # Legacy geometry-less captions are the one backward-
            # compatibility case: the helper admits them only when the
            # imported media dimensions equal this timeline's frame.
            # `draw_intent` arms the pixel half: the held values are
            # judged against the DECLARED caption row, so a sidecar
            # placement served under a superseded row is REPORTED
            # rather than shipped. The master honours no pins (that
            # would move placements, not just judge them), so this is
            # the row path only - `intent` stays None.
            assert_current_timeline(project, timeline)
            _seg_placement, _seg_refusal = caption_segment_placement(
                seg, si, _caption_row,
                media_pool_item=pool_item,
                frame_size=(width, height))
            if _seg_refusal:
                results["warnings"].append(_seg_refusal)
                print(f"  ✗ [{si}] {seg_basename}: {_seg_refusal}",
                      file=sys.stderr)
                continue
            placed, note = place_overlay_segment(
                media_pool, timeline, pool_item,
                track_index=_caption_row, record_frame=tl_in_frame,
                source_in_frame=src_in_f, source_out_frame=src_out_f,
                placement=_seg_placement,
                label=f"V{_caption_row}[{si}] {seg_basename}",
                draw_intent=draw_intent_for_segment(
                    seg, kind="caption",
                    frame_wh=(width, height),
                    project_folder=project_folder),
                resolve_project=project,
                project_folder=project_folder,
                timeline_name=timeline_name)
            if placed:
                v3_count += 1
                print(f"  ✓ [{si}] {seg_basename} on V{_caption_row} ({seg_frames}f @ TL {tl_in_frame})",
                      file=sys.stderr)
                if note:
                    results["warnings"].append(note)
                    print(f"  ⚠ {note}", file=sys.stderr)
            else:
                print(f"  ✗ [{si}] {seg_basename}: placement failed", file=sys.stderr)
                results["warnings"].append(f"V{_caption_row}[{si}] placement failed: {seg_basename}")

        results["tracks"][f"V{_caption_row}"] = v3_count

    _clock.lap("place_motion_graphics")
    # ══════════════════════════════════════════════════════════
    # PLACE MOTION GRAPHICS (plan rows, one row per overlapping layer)
    # ══════════════════════════════════════════════════════════
    _mg_rows = [t.index for t in track_plan.video_tracks
                if t.role == "motion_graphics"]
    if has_mg:
        if not _mg_rows:
            results["errors"].append(
                "Motion-graphics segments planned with no MG row; refusing.")
            return results
        print(f"\n── Motion Graphics ({','.join(f'V{r}' for r in _mg_rows)}): "
              f"{len(mg_segments)} segments ──", file=sys.stderr)
        # The segment's own LANE where the plan recorded one - a lane IS
        # a row (`motion_graphics_plan.plan_segments`), and reading it
        # back beats re-deriving the packing from spans that happen to
        # tie. Segments written before lanes existed re-derive.
        if all("lane" in s for s in mg_segments):
            _mg_allocations = [
                (None, _mg_rows[0] + int(s.get("lane", 0) or 0))
                for s in mg_segments]
        else:
            _mg_allocations = allocate_non_overlapping_rows(
                [(round(s.get("timeline_start", 0) * fps),
                  round(s.get("timeline_end", 0) * fps))
                 for s in mg_segments],
                base_index=_mg_rows[0])
        _mg_off_plan = sorted({r for _, r in _mg_allocations} - set(_mg_rows))
        if _mg_off_plan:
            results["errors"].append(
                f"Motion-graphics packing disagrees with the track plan on "
                f"rows {_mg_off_plan}; refusing.")
            return results
        v4_count = 0
        _mg_counts = {}
        for mi, seg in enumerate(mg_segments):
            seg_path = seg.get('overlay_path', '')
            seg_basename = os.path.basename(seg_path)
            pool_item = _find_pool_clip(seg_path)
            _mg_row = _mg_allocations[mi][1]
            if not pool_item:
                results["warnings"].append(f"V{_mg_row}[{mi}] {seg_basename} not in pool")
                print(f"  ✗ [{mi}] {seg_basename} not in media pool", file=sys.stderr)
                continue

            # The clip attributes an alpha artefact needs, decided by
            # the FILE - see library/tools/overlay_carriage.py. Not
            # just the alpha mode: a `qtrle` overlay whose Data Level
            # is left on Resolve's default `Auto` is read as video
            # range and composites the WHOLE frame 16/255 dark,
            # including every pixel where the overlay is transparent.
            apply_clip_attributes(pool_item, seg_path)

            seg_frames = seg.get('total_frames', round(
                (seg.get('timeline_end', 0) - seg.get('timeline_start', 0)) * fps))
            tl_in_frame = round(seg.get('timeline_start', 0) * fps)

            # The graphic artefact rides the placement its tight box
            # computed - read off the entry step 4.06 recorded - and
            # the placer SETS it then READS BACK what Resolve holds.
            # See library/tools/overlay_placement.py.
            assert_current_timeline(project, timeline)
            placed, note = place_overlay_segment(
                media_pool, timeline, pool_item,
                track_index=_mg_row, record_frame=tl_in_frame,
                source_in_frame=0, source_out_frame=seg_frames,
                placement=(seg.get("tight_box") or {}).get("placement"),
                label=f"V{_mg_row}[{mi}] {seg_basename}",
                resolve_project=project,
                project_folder=project_folder,
                timeline_name=timeline_name)
            if placed:
                v4_count += 1
                _mg_counts[_mg_row] = _mg_counts.get(_mg_row, 0) + 1
                print(f"  ✓ [{mi}] {seg_basename} on V{_mg_row} ({seg_frames}f @ TL {tl_in_frame})",
                      file=sys.stderr)
                if note:
                    results["warnings"].append(note)
                    print(f"  ⚠ {note}", file=sys.stderr)
            else:
                print(f"  ✗ [{mi}] {seg_basename}: placement failed", file=sys.stderr)
                results["warnings"].append(f"V{_mg_row}[{mi}] placement failed: {seg_basename}")

        for _r in _mg_rows:
            results["tracks"][f"V{_r}"] = _mg_counts.get(_r, 0)

    _clock.lap("place_generators")
    # ══════════════════════════════════════════════════════════
    # PLACE GENERATOR EFFECTS (plan row, transparent carriers)
    # ══════════════════════════════════════════════════════════
    # Generator presets produce content from nothing (no image input).
    # They are placed on the plan's generator row as transparent carrier
    # clips; the Fusion comp import subprocess imports the .setting file
    # onto each clip.
    # Composite mode is set per-entry (default: Screen) so the generated
    # content blends over the picture below.
    _gen_row = next((t.index for t in track_plan.video_tracks
                     if t.role == "generators"), None)
    if has_generators:
        if _gen_row is None:
            results["errors"].append(
                "Generator overlays planned with no generator row; refusing.")
            return results
        print(f"\n-- V{_gen_row} Generator Effects: {len(generator_overlays)} overlays --", file=sys.stderr)
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
            carrier_exists=prepared["carrier_exists"],
        )

        # Store generator overlay metadata for apply_fusion_comps
        # to pick up and import the .setting files. Each entry carries
        # the plan row it was placed on: the Fusion pass must read the
        # carriers off THAT row, never off a hardcoded V5.
        manifest.setdefault('fusion_effects', {})
        manifest['fusion_effects']['generator_overlays'] = generator_overlays
        for gen in generator_overlays:
            gen.setdefault("timeline_row", _gen_row)

        COMPOSITE_MODES = {
            "normal": 0, "screen": 5, "add": 1, "multiply": 3,
        }
        for gi, gen in enumerate(generator_overlays):
            tl_start = gen['timeline_start']
            tl_end = gen['timeline_end']
            dur_frames = round((tl_end - tl_start) * fps)
            tl_in_frame = round(tl_start * fps)

            assert_current_timeline(project, timeline)
            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": transparent_carrier,
                "startFrame": 0,
                "endFrame": dur_frames,
                "trackIndex": _gen_row,
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
                    f"V{_gen_row} {dur_frames}f @ TL {tl_in_frame} "
                    f"(composite: {mode_name})",
                    file=sys.stderr,
                )
            else:
                print(
                    f"  X [{gi}] {gen['effect_name']}: "
                    f"placement failed",
                    file=sys.stderr,
                )

        results["tracks"][f"V{_gen_row}"] = v5_count

    _clock.lap("place_timed_text")
    # ══════════════════════════════════════════════════════════
    # PLACE TIMED TEXT (plan rows, one row per overlapping layer)
    # ══════════════════════════════════════════════════════════
    # The moments a brand template declared in effect.timed_text_overlay,
    # rendered by 4.06 into one ProRes 4444 alpha file per cluster of
    # moments whose spans touch. Timeline frames throughout - the segment
    # already knows where it goes, so there is no block offset to apply.
    _tt_rows = [t.index for t in track_plan.video_tracks
                if t.role == "timed_text"]
    if has_timed_text:
        if not _tt_rows:
            results["errors"].append(
                "Timed-text segments planned with no timed-text row; "
                "refusing.")
            return results
        print(f"\n-- Timed Text ({','.join(f'V{r}' for r in _tt_rows)}): "
              f"{len(tt_segments)} segments --",
              file=sys.stderr)
        _tt_allocations = allocate_non_overlapping_rows(
            [(round(s.get("timeline_start", 0) * fps),
              round(s.get("timeline_end", 0) * fps)) for s in tt_segments],
            base_index=_tt_rows[0])
        _tt_off_plan = sorted({r for _, r in _tt_allocations} - set(_tt_rows))
        if _tt_off_plan:
            results["errors"].append(
                f"Timed-text packing disagrees with the track plan on rows "
                f"{_tt_off_plan}; refusing.")
            return results
        v6_count = 0
        _tt_counts = {}
        for ti, seg in enumerate(tt_segments):
            seg_path = seg.get('overlay_path', '')
            seg_basename = os.path.basename(seg_path)
            pool_item = _find_pool_clip(seg_path)
            _tt_row = _tt_allocations[ti][1]
            if not pool_item:
                results["warnings"].append(f"V{_tt_row}[{ti}] {seg_basename} not in pool")
                print(f"  X [{ti}] {seg_basename} not in media pool",
                      file=sys.stderr)
                continue

            # The clip attributes an alpha artefact needs, decided by
            # the FILE - see library/tools/overlay_carriage.py. Not
            # just the alpha mode: a `qtrle` overlay whose Data Level
            # is left on Resolve's default `Auto` is read as video
            # range and composites the WHOLE frame 16/255 dark,
            # including every pixel where the overlay is transparent.
            apply_clip_attributes(pool_item, seg_path)

            seg_frames = seg.get('total_frames', round(
                (seg.get('timeline_end', 0) - seg.get('timeline_start', 0)) * fps))
            tl_in_frame = round(seg.get('timeline_start', 0) * fps)

            # Through the ONE overlay placer, like the captions and the
            # motion graphics above. A timed-text card is a full-frame
            # overlay artefact (`library/tools/timed_text_render.py`),
            # so it needs no transform, and routing it here is what
            # gives it the read-back every other overlay row already
            # has. It used to carry its own inline append, which meant
            # the one row on the timeline nobody ever asked Resolve
            # what it held.
            assert_current_timeline(project, timeline)
            placed, note = place_overlay_segment(
                media_pool, timeline, pool_item,
                track_index=_tt_row, record_frame=tl_in_frame,
                source_in_frame=0, source_out_frame=seg_frames,
                label=f"V{_tt_row}[{ti}] {seg_basename}",
                resolve_project=project,
                project_folder=project_folder,
                timeline_name=timeline_name)
            if placed:
                v6_count += 1
                _tt_counts[_tt_row] = _tt_counts.get(_tt_row, 0) + 1
                print(f"  V [{ti}] {seg_basename} on V{_tt_row} "
                      f"({seg_frames}f @ TL {tl_in_frame})", file=sys.stderr)
                if note:
                    results["warnings"].append(note)
                    print(f"  ! {note}", file=sys.stderr)
            else:
                print(f"  X [{ti}] {seg_basename}: placement failed - "
                      f"{note}", file=sys.stderr)
                results["warnings"].append(
                    f"V{_tt_row}[{ti}] placement failed: {seg_basename}")

        for _r in _tt_rows:
            results["tracks"][f"V{_r}"] = _tt_counts.get(_r, 0)
        if v6_count != len(tt_segments):
            # A declared moment that did not land is invisible everywhere
            # downstream: the picture underneath is intact, so render QA
            # sees nothing wrong. Say it here or nobody says it.
            results["qa_failures"].append({
                "station": "timed_text",
                "check": "declared_segments_placed",
                "expected": f"{len(tt_segments)} segments on timed-text rows",
                "actual": f"{v6_count} placed",
                "detail": (
                    f"QA [timed_text] Failed declared_segments_placed: "
                    f"{v6_count}/{len(tt_segments)} declared timed text "
                    f"segments reached the timeline"),
            })

    _clock.lap("place_voiceover_and_music")
    # ══════════════════════════════════════════════════════════
    # PLACE A2: Music
    # ══════════════════════════════════════════════════════════
    # ══════════════════════════════════════════════════════════
    # PLACE VOICEOVER (manifest A1 voiceover clips onto a speech row)
    # ══════════════════════════════════════════════════════════
    # Words from the audio intake have no V1 carrier, so their sound
    # does not arrive linked under a picture the way A-roll speech
    # does. Each voiceover clip is placed audio-only on a speech row -
    # the link pass below then joins it to the captions it spans, and
    # the row's own speech item feeds the same checks A-roll speech
    # feeds. The first speech row carries it: narration is not
    # angle-bound, and one row is all a voiceover-led cut mints (a
    # multi-angle piece with voiceover keeps the single narration row
    # rather than duplicating the words per angle).
    if a1_voiceover_clips:
        _voice_rows = track_plan.speech_rows()
        if not _voice_rows:
            results["errors"].append(
                "Voiceover narration to place and the track plan mints "
                "no speech row; refusing to park words on an unnamed row.")
            return results
        _voice_row = _voice_rows[0].index
        print(f"\n── A1 Voiceover: {len(a1_voiceover_clips)} clip(s) on "
              f"A{_voice_row} ──", file=sys.stderr)
        while timeline.GetTrackCount("audio") < _voice_row:
            timeline.AddTrack("audio")
        for ci, clip in enumerate(a1_voiceover_clips):
            basename = os.path.basename(clip.get('source_file', ''))
            pool_item = _find_pool_clip(clip.get('source_file', ''))
            if not pool_item:
                results["warnings"].append(
                    f"Voiceover[{ci}] {basename} not in pool")
                continue
            tl_in_sec = clip.get('timeline_in', 0)
            tl_out_sec = clip.get('timeline_out', total_duration)
            src_fps = _source_fps(pool_item, fps)
            src_dur_f = round((tl_out_sec - tl_in_sec) * src_fps)
            src_in_f = round(clip.get('source_in', 0) * src_fps)
            assert_current_timeline(project, timeline)
            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_in_f + src_dur_f,
                "trackIndex": _voice_row,
                "recordFrame": round(tl_in_sec * fps),
                "mediaType": 2,  # audio-only placement
            }])
            if result:
                placed = result[0] if isinstance(result, list) else result
                print(f"  ✓ {basename}: {placed.GetDuration()}f on "
                      f"A{_voice_row}", file=sys.stderr)
                results["tracks"][f"A{_voice_row}"] = (
                    results["tracks"].get(f"A{_voice_row}", 0) + 1)
            else:
                print(f"  ✗ {basename} on A{_voice_row}: failed",
                      file=sys.stderr)

    if music_allocations:
        print(f"\n── A2+ Music: {len(a2_clips)} clips across "
              f"{len({t for _, t in music_allocations})} track(s) ──",
              file=sys.stderr)
        for ci, (clip, music_track_idx) in enumerate(music_allocations):
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

            assert_current_timeline(project, timeline)
            result = media_pool.AppendToTimeline([{
                "mediaPoolItem": pool_item,
                "startFrame": src_in_f,
                "endFrame": src_in_f + src_dur_f,
                "trackIndex": music_track_idx,  # A2, or a lane above it
                                                # when a crossfade overlaps
                "recordFrame": round(tl_in_sec * fps),
                "mediaType": 2,  # audio-only placement
            }])

            if result:
                placed = result[0] if isinstance(result, list) else result
                print(f"  ✓ {basename}: {placed.GetDuration()}f on "
                      f"A{music_track_idx}", file=sys.stderr)
                results["tracks"][f"A{music_track_idx}"] = (
                    results["tracks"].get(f"A{music_track_idx}", 0) + 1)
                # No level is set here. `SetProperty("Volume", ...)`
                # returns False on every audio TimelineItem - the object
                # has no property dictionary at all - so the call that
                # used to sit here changed nothing while reading as a
                # mix. Levels are delivered together, after placement,
                # by deliver_mix below.
            else:
                print(f"  ✗ {basename} on A{music_track_idx}: failed",
                      file=sys.stderr)

    _clock.lap("place_sfx")
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

            assert_current_timeline(project, timeline)
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

                # `volume_db` is delivered by deliver_mix below, for the
                # same reason as A2: no scripting call sets an audio
                # level. This is the hop where per-clip SFX volume used
                # to be lost.
            else:
                print(f"  ✗ {basename} on A{track_idx} at {tl_in_f}: failed", file=sys.stderr)

        for tk, count in sorted(sfx_track_counts.items()):
            results["tracks"][f"A{tk}"] = count

    _clock.lap("deliver_audio_mix")
    # ══════════════════════════════════════════════════════════
    # DELIVER THE AUDIO MIX (OTIO round trip)
    # ══════════════════════════════════════════════════════════
    # THIS IS WHY IT IS HERE AND NOT LATER. The OTIO import REBUILDS the
    # timeline, and Fusion comps and CDL grades do not survive it -
    # placement, transform, markers and native transitions do (AGENTS.md
    # section 5). Everything below this line therefore has to run on the
    # timeline the import produced, and this is the last moment before
    # the Fusion pass draws anything worth losing.
    #
    # `timeline` is REBOUND on success. Nothing below may hold a
    # TimelineItem from before this point; they all re-read the track.
    audio_mix = manifest.get('audio_mix', {})
    mix_report = deliver_mix(
        resolve, project, media_pool, timeline, manifest,
        fps=fps, project_folder=project_folder)
    results["audio_mix_delivery"] = {
        "delivered": mix_report["delivered"],
        "reason": mix_report["reason"],
        "levels_applied": len(mix_report["applied"]),
        "otio_path": mix_report["mixed_otio_path"] or mix_report["otio_path"],
    }

    if mix_report["delivered"]:
        timeline = mix_report["timeline"]
        timeline_name = mix_report["timeline_name"]
        results["timeline_name"] = timeline_name
        curves = sum(1 for a in mix_report["applied"] if a.get("keyframes"))
        print(f"\n── Audio mix: {len(mix_report['applied'])} clip levels "
              f"({curves} automated) written through OTIO ──", file=sys.stderr)
        for entry in mix_report["applied"]:
            keys = entry.get("keyframes") or {}
            detail = (f"{len(keys)} keyframes, "
                      f"{min(keys.values()):.0f}..{max(keys.values()):.0f}dB"
                      if keys else f"{entry['level_db']:.1f}dB")
            print(f"  ✓ {entry['label']}: {detail}", file=sys.stderr)
    else:
        print(f"\n── Audio mix NOT delivered: {mix_report['reason']} ──",
              file=sys.stderr)

    for target in mix_report["unmatched"]:
        msg = (f"planned level for {target['label']} matched no clip at frame "
               f"{target['start_frame']} - it does not reach the mix")
        results["warnings"].append(msg)
        print(f"  ⚠ {msg}", file=sys.stderr)
    for complaint in mix_report["complaints"]:
        msg = f"audio mix read back wrong: {complaint}"
        results["warnings"].append(msg)
        print(f"  ⚠ {msg}", file=sys.stderr)
    for row in mix_report.get("stem_unmatched", []) or []:
        msg = (f"cleanup stem {row.get('label', '?')} matched no clip: "
               f"{row.get('reason', '')}")
        results["errors"].append(msg)
        print(f"  ✗ {msg}", file=sys.stderr)
    for row in mix_report.get("stem_applied", []) or []:
        print(f"  ✓ cleanup stem {row['label']} on the timeline",
              file=sys.stderr)

    _clock.lap("voice_isolation")
    # ══════════════════════════════════════════════════════════
    # DIALOGUE CLEANUP: VOICE ISOLATION (fidelity rung R5d)
    # ══════════════════════════════════════════════════════════
    # Plan-requested only: step 5.02 validates each entry and compile
    # resolves it against the played clips; here the amount reaches
    # the speech track AFTER the OTIO round trip above, because the
    # import rebuilds the timeline and would discard a setting written
    # before it. Each write is judged by Get read-back
    # (`dialogue_cleanup.apply_voice_isolation`, the same discipline as
    # the `audio isolate` resolve-axi verb) - and a write that does not
    # land errors the build rather than warning past it, the way an
    # unplaced room-tone fill does: uncleaned dialogue reported clean
    # is the fake success rung 1 removed.
    results["voice_isolation"] = []
    cleanup_plan = ((manifest.get("audio") or {})
                    .get("dialogue_cleanup") or {})
    voice_rows = cleanup_plan.get("voice_isolation", []) or []
    if voice_rows:
        _speech = track_plan.speech_row_for_angle("main")
        if _speech is None:
            _rows = track_plan.speech_rows()
            _speech = _rows[0] if _rows else None
        if _speech is None:
            results["errors"].append(
                "Voice isolation planned with no speech row; refusing "
                "to apply it unrowed.")
        else:
            from library.tools import dialogue_cleanup as _dclean
            print(f"\n── Voice isolation: {len(voice_rows)} request(s) "
                  f"on A{_speech.index} ──", file=sys.stderr)
            while timeline.GetTrackCount("audio") < _speech.index:
                timeline.AddTrack("audio")
            for _row in voice_rows:
                try:
                    assert_current_timeline(project, timeline)
                    _applied = _dclean.apply_voice_isolation(
                        timeline, _speech.index, _row.get("amount"))
                except _dclean.DialogueCleanupRefused as exc:
                    msg = (f"voice isolation on A{_speech.index} for "
                           f"{_row.get('source', '?')} declined: "
                           f"{exc.what}")
                    results["errors"].append(msg)
                    print(f"  ✗ {msg}", file=sys.stderr)
                    continue
                _applied["source"] = _row.get("source", "")
                results["voice_isolation"].append(_applied)
                print(f"  ✓ A{_speech.index} voice isolation "
                      f"{_applied['amount']} ({_applied['verified']})",
                      file=sys.stderr)

    if not mix_report["delivered"]:
        # THE FALLBACK, and it is a fallback: a cyan marker is a note
        # asking a human to set the level by hand, not a level. It is
        # written only when the real route above declined, and it says so.
        music_automation = audio_mix.get('music_automation', [])
        if music_automation:
            results["warnings"].append(
                f"audio mix not delivered ({mix_report['reason']}); "
                f"{len(music_automation)} target levels left as markers for a "
                f"human to set by hand")
            for auto in music_automation:
                frame = round(auto.get('timeline_start', 0) * fps)
                timeline.AddMarker(
                    frame, "Cyan",
                    f"UNAPPLIED target: {auto.get('target_level_db', 0)}dB "
                    f"({auto.get('music_behavior') or 'unspecified'})",
                    "The pipeline could not write this level; set it by hand",
                    1)
            print(f"  ⚠ Fell back to {len(music_automation)} cyan markers",
                  file=sys.stderr)

    # The master limiter is a MASTER BUS setting, not a clip volume, so
    # no clip-level route reaches it. We attempt to apply a Fairlight preset
    # if one exists and the Resolve API is new enough (20.2.2+).
    # If not, we fall back to a marker so it is not silently unapplied.
    master_limiter = audio_mix.get('master_limiter', {})
    if master_limiter and master_limiter.get('enabled'):
        threshold_db = master_limiter.get('threshold_db', -1.0)
        preset_name = "Pipeline_Master_Limiter"
        applied = False
        
        # Check if the API is available by calling it, not with hasattr
        # (AGENTS.md section 5: hasattr is always True on Resolve's
        # scripting proxies, including invented names). Any failure to
        # read the presets means the route is unavailable - the guard
        # judges the outcome, never the spelling of the failure - so a
        # declined probe falls back to the marker below instead of
        # failing the build.
        presets = None
        try:
            presets = resolve.GetFairlightPresets()
        except Exception:
            pass
            
        if presets is not None:
            # presets could be a list or dict depending on Resolve version.
            # We check both keys and values because the API documentation doesn't specify
            # the dict structure and we had an empty dict during testing. Note this is an
            # open question rather than deliberate breadth.
            preset_exists = False
            if isinstance(presets, dict):
                preset_exists = preset_name in presets or preset_name in presets.values()
            elif isinstance(presets, list) or isinstance(presets, tuple):
                preset_exists = preset_name in presets
            else:
                preset_exists = False
                
            if preset_exists:
                try:
                    assert_current_timeline(project, timeline)
                    applied = project.ApplyFairlightPresetToCurrentTimeline(preset_name)
                except Exception as exc:
                    # A decline is a decline, whatever its shape: a missing
                    # method (TypeError), a timeline race mid-apply
                    # (ResolveRaceError from the assert above), or a build
                    # that answers some other way. The marker below is the
                    # fallback for all of them - the limiter must never
                    # fail the build.
                    print(f"  ⚠ Fairlight preset '{preset_name}' could not be applied: {exc}",
                          file=sys.stderr)
                    applied = False
                    
                if applied:
                    print(f"  ✓ Applied Fairlight preset '{preset_name}' for master limiter", file=sys.stderr)
                else:
                    print(f"  ⚠ Fairlight preset '{preset_name}' found but failed to apply", file=sys.stderr)
            else:
                print(f"  ⚠ Fairlight preset '{preset_name}' not found. Falling back to marker.", file=sys.stderr)
        else:
            print("  ⚠ Resolve build lacks GetFairlightPresets API. Falling back to marker.", file=sys.stderr)
            
        if not applied:
            timeline.AddMarker(
                0, "Purple", f"Master Limiter: {threshold_db}dBTP",
                "Set the master track limiter to this threshold", 1)

    if verify_audio:
        _run_qa(verify_audio(timeline, project, manifest.get("audio", {})))

    _clock.lap("record_decisions")
    # ══════════════════════════════════════════════════════════
    # RECORD WHAT DECIDED EACH CLIP
    # ══════════════════════════════════════════════════════════
    # The producer side of the captain's note loop (AGENTS.md 15). It
    # reads the manifest and writes a ledger - no Resolve call, nothing
    # visible, and it is what lets a note typed on a clip reach the step
    # whose decision put that clip there.
    #
    # IT CREATES NO MARKER. A marker is drawn on the timeline ruler, and
    # thirty of them nobody asked for would be a visible change to the
    # captain's timeline in exchange for a payload the UI cannot show.
    # Markers that ARE there get the decision merged into their
    # customData, which leaves name, note, colour and duration alone.
    #
    # It never fails the build: the ledger is an audit artifact, and a
    # render that is otherwise correct is not made wrong by one.
    if project_folder:
        try:
            ledger_file = timeline_decisions.write_ledger(
                project_folder, manifest)
            ledger = timeline_decisions.read_ledger(project_folder)
            stamped = timeline_decisions.stamp_timeline(timeline, ledger)
            results["decision_ledger"] = {
                "path": str(ledger_file),
                "placements": len(ledger.get("placements") or []),
                "unstamped": len(ledger.get("unstamped") or []),
                "markers_seen": stamped.markers_seen,
                "markers_stamped": stamped.markers_stamped,
                "records_written": stamped.records_written,
            }
            print(f"\n── Decision ledger: "
                  f"{len(ledger.get('placements') or [])} placements, "
                  f"{len(ledger.get('unstamped') or [])} with no deciding "
                  f"step; {stamped.markers_stamped}/{stamped.markers_seen} "
                  f"existing markers stamped ──", file=sys.stderr)
            for refusal in stamped.refused:
                msg = (f"Resolve refused the decision stamp at frame "
                       f"{refusal['frame']}: {refusal['reason']}")
                results["warnings"].append(msg)
                print(f"  ⚠ {msg}", file=sys.stderr)
        except Exception as exc:
            msg = f"decision ledger not written: {exc}"
            results["warnings"].append(msg)
            print(f"  ⚠ {msg}", file=sys.stderr)

    _clock.lap("fusion_comps")
    # ══════════════════════════════════════════════════════════
    # APPLY FUSION .comp FILES (animated VFX + transitions per clip)
    # ══════════════════════════════════════════════════════════
    # CRITICAL RULE FIX: We must run ImportFusionComp in a separate process
    # because clip references go stale after timeline creation.
    import tempfile
    
    script_path = prepared["fusion_script_path"]
    if prepared["fusion_script_exists"]:
        # The Fusion pass walks the timeline the plan laid out. It still
        # reads per-clip comps off manifest tracks V1/V2 (its own
        # conformance is a filed follow-up); the plan travels with the
        # manifest so that lane has the row mapping without another
        # builder change.
        manifest.setdefault("track_plan", track_plan.serializable())
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as tf:
            json.dump(manifest, tf)
            temp_manifest = tf.name
            
        cmd = [sys.executable, script_path, temp_manifest]
        if project_folder:
            cmd += ["--project-folder", project_folder]
        # ── Destination guard ──
        # Tell the subprocess which project and timeline it must find
        # current before writing any Fusion comp.  The subprocess
        # verifies immediately before its first mutation and refuses on
        # mismatch, which is the guard against H2 (the wrong-destination
        # hazard where ImportFusionComp lands on the captain's rough
        # cut because the same footage matches on both timelines).
        #
        # project.GetName() is the EXACT listed name (AGENTS.md section
        # 5); timeline_name is the name build_timeline just created.
        current_project_name = project.GetName()
        cmd += ["--expected-project", current_project_name]
        cmd += ["--expected-timeline", timeline_name]
        # The verify_treatment receipt the Fusion pass writes is filed
        # under this DAG node, so the check that ran where the damage
        # happens reads back from disk.
        cmd += ["--step-id", "render"]
        print(f"\n── Launching subprocess for Fusion Comps ──", file=sys.stderr)
        print(f"  expected: project={current_project_name!r} "
              f"timeline={timeline_name!r}", file=sys.stderr)
        # BOUNDED. This call had no timeout, and it is the one subprocess in
        # the renderer that talks to Resolve from a second process - so when
        # Resolve does not answer, it waits forever. That is not only a test
        # problem: a real render would hang with no diagnostic and no way to
        # tell it from a slow Fusion pass. It hung the whole test suite three
        # times, at ~58%, with 2.75s of CPU over ten minutes of wall clock,
        # because tests/unit/resolve/test_resolve_build_timeline.py drives build_timeline
        # with a mocked Resolve and a blanket os.path.exists patch, which
        # let this launch for real against the live application.
        #
        # A timeout is a FAILURE here, not a skip: comps that did not draw
        # must not read as comps that did.
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=FUSION_SUBPROCESS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            msg = (f"Fusion subprocess did not finish within "
                   f"{FUSION_SUBPROCESS_TIMEOUT_S}s and was killed; no comps "
                   f"were applied")
            results["errors"].append(msg)
            print(f"  ✗ {msg}", file=sys.stderr)
            proc = None

        if proc is None:
            pass
        elif proc.returncode != 0:
            # A non-zero return from a subprocess that mutates the captain's
            # project is an ERROR, not a warning.  The comps it claims it
            # drew may be on the wrong timeline (destination mismatch),
            # partially applied, or absent, and the parent cannot tell which.
            # Downgrading this to a warning allowed a destination-mismatch
            # refusal to read as a soft problem the build survived, when in
            # fact no comps reached the intended timeline.
            msg = f"Fusion subprocess failed (exit {proc.returncode}): {proc.stderr}"
            results["errors"].append(msg)
            print(f"  ✗ Fusion Comps Subprocess Failed", file=sys.stderr)
            if proc.stderr:
                print(proc.stderr, file=sys.stderr)
        else:
            print(proc.stderr, file=sys.stderr)
            
        os.remove(temp_manifest)
    else:
        results["warnings"].append(f"apply_fusion_comps.py not found at {script_path}")

    _clock.lap("unreachable_fusion_effects")
    # ── Detect planned Fusion effects that the subprocess cannot reach ──
    # The logic lives in build_verification.detect_unreachable_fusion_effects
    # so it can be tested with plain data objects, without Resolve. The
    # tracks it is handed are the tracks the Fusion pass walks; see
    # library/tools/execution/fusion_tracks.FUSION_COMP_TRACKS.
    fusion_effects = manifest.get("fusion_effects", {})
    per_clip_fx = fusion_effects.get("per_clip", {})
    if per_clip_fx:
        # Labels this run really placed, per plan row. A-roll labels are
        # recorded per angle row (not lumped on V1), so a second angle's
        # clips are reachable rather than permanent drops.
        placed_by_track = {}
        for _rowkey, (_items, _clips) in placed_by_row.items():
            placed_by_track[int(_rowkey[1:])] = {
                c.get("label", "") for c in _clips}
        if 'v2_placed_labels' in locals() and _broll_row is not None:
            placed_by_track.setdefault(_broll_row, set()).update(
                v2_placed_labels)

        dropped = detect_unreachable_fusion_effects(per_clip_fx, placed_by_track)
        if dropped:
            msg = format_fusion_drop_error(dropped)
            results["errors"].append(msg)
            print(f"  ✗ {msg}", file=sys.stderr)

    if verify_fusion_comps:
        # verify_fusion_comps reads per_clip - that is fusion_effects, not
        # the flat vfx LIST, which has no .get(). per_clip is keyed by clip
        # LABEL, so the station needs the labels this run actually placed,
        # in timeline order, per plan row.
        _fusion_labels = {}
        for _rowkey, (_items, _clips) in placed_by_row.items():
            _fusion_labels[int(_rowkey[1:])] = [
                c.get("label", "") for c in _clips]
        if 'v2_placed_labels' in locals() and _broll_row is not None:
            _fusion_labels.setdefault(_broll_row, []).extend(v2_placed_labels)
        _run_qa(verify_fusion_comps(
            timeline,
            _fusion_labels,
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

    _clock.lap("neural_directives")
    # ══════════════════════════════════════════════════════════
    # NEURAL ENGINE DIRECTIVES (Per-Clip)
    # ══════════════════════════════════════════════════════════
    _apply_neural_engine_directives(
        manifest, timeline, v1_placed_labels,
        v2_placed_labels if 'v2_placed_labels' in locals() else [], results)

    # Smart Reframe used to be applied here, on the timeline, and printed a
    # tick whatever Resolve answered. It is withdrawn; see the note at the
    # top of library/tools/neural_engine.py. Framing is delivered per clip
    # by _apply_conform above.

    _clock.lap("fairlight_preset")
    # ══════════════════════════════════════════════════════════
    # APPLY FAIRLIGHT PRESET (if specified)
    # ══════════════════════════════════════════════════════════
    audio_config = manifest.get('audio', {})
    fairlight_preset = audio_config.get('fairlight_preset', '')
    if fairlight_preset:
        print(f"\n── Fairlight Preset: {fairlight_preset} ──", file=sys.stderr)
        try:
            assert_current_timeline(project, timeline)
            result = project.ApplyFairlightPresetToCurrentTimeline(fairlight_preset)
            if result:
                print(f"  ✓ Applied Fairlight preset: {fairlight_preset}", file=sys.stderr)
            else:
                print(f"  ⚠ Fairlight preset '{fairlight_preset}' failed, applying fallback", file=sys.stderr)
                assert_current_timeline(project, timeline)
                fallback_res = project.ApplyFairlightPresetToCurrentTimeline("Dialogue")
                if fallback_res:
                    print(f"  ✓ Applied fallback preset: Dialogue", file=sys.stderr)
                else:
                    results["warnings"].append(f"Fairlight preset '{fairlight_preset}' and fallback failed")
        except Exception as e:
            results["warnings"].append(f"Fairlight preset '{fairlight_preset}' exception: {e}")
            print(f"  ⚠ Fairlight preset '{fairlight_preset}' raised an exception: {e}", file=sys.stderr)

    # The audio mix is NOT here. It is delivered at placement time, in
    # the OTIO round trip above, because the import that carries it
    # rebuilds the timeline and would discard every Fusion comp drawn
    # since. The cyan-marker path that used to sit here is its fallback.

    _clock.lap("color_grade")
    # ══════════════════════════════════════════════════════════
    # COLOR GRADING (house look: CDL half, then the PowerGrade route)
    # ══════════════════════════════════════════════════════════
    # The look's other half is Fusion, and it does not arrive here: it is
    # merged into fusion_effects.per_clip by compile_manifest and drawn by
    # apply_fusion_comps. The THIRD route is the project's own PowerGrade:
    # `color.power_grade_drx` in its project.yaml, applied per clip with
    # `Graph.ApplyGradeFromDRX` (library/tools/color_page_grade.py).
    # Applying a DRX REPLACES the clip's whole node graph - including the
    # node SetCDL just wrote - so a declared DRX is the grade, not an
    # addition to it. An undeclared one changes nothing here.
    color_grade = manifest.get("color_grade", {})
    per_clip_adjs = color_grade.get("per_clip_adjustments", [])
    series_look = color_grade.get("series_look")

    # Build a lookup by source_file basename
    color_lookup = {}
    for adj in per_clip_adjs:
        src = adj.get("source_file", "")
        if src:
            color_lookup[os.path.basename(src).lower()] = adj.get("cdl_values", {})

    if color_lookup:
        label = series_look or "no house look named - exposure only"
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

                # Finding 28: a True return from SetCDL is not evidence
                # the grade landed - only the read-back is. What GetCDL
                # holds afterwards is compared with the four specified
                # terms (`library/tools/cdl_readback.py`): a mismatch
                # ERRORS naming the clip (the build must not report a
                # grade it does not carry), an unreadable read-back
                # warns (the grade may be on and only the read-back
                # broke - timeline_qa's station-3 philosophy). The
                # exported pixels get their own verdict in 6.02
                # (`render_qa.measure_grade_delivery`).
                from library.tools import cdl_readback as _cdl
                try:
                    _actual = item.GetCDL() or {}
                except Exception as _e:
                    _actual = {}
                    _cdl_read_error = f"{type(_e).__name__}: {_e}"
                else:
                    _cdl_read_error = ""
                if not isinstance(_actual, dict) or not _actual:
                    try:
                        _prop_actual = {
                            "Slope": item.GetClipProperty("Slope"),
                            "Offset": item.GetClipProperty("Offset"),
                            "Power": item.GetClipProperty("Power"),
                            "Saturation": item.GetClipProperty(
                                "Saturation"),
                        }
                    except Exception:
                        _prop_actual = {}
                    if any(v for v in _prop_actual.values()):
                        _actual = _prop_actual
                _mismatches = _cdl.compare_cdl(_actual, cdl_vals)
                if _mismatches and _actual:
                    _msg = (f"CDL on {clip_name} reads back different "
                            f"from the plan: {'; '.join(_mismatches)}")
                    results["errors"].append(_msg)
                    print(f"  ✗ {_msg}", file=sys.stderr)
                elif _mismatches:
                    # No read-back at all (measured 2026-09-25: this
                    # build serves neither GetCDL nor the Slope clip
                    # properties): one line naming that, not four
                    # unverifiable terms. The grade is UNVERIFIED here;
                    # 6.02 judges its pixels on the export.
                    _msg = (f"CDL on {clip_name} is unverified "
                            f"({(_cdl_read_error or 'this Resolve build '
                                                 'serves no CDL read-back')})"
                            f" - 6.02 judges its exported pixels")
                    results["warnings"].append(_msg)
                    print(f"  ⚠ {_msg}", file=sys.stderr)
                else:
                    print(f"  ✓ CDL base grade on {clip_name} "
                          f"(read back equal)", file=sys.stderr)

    # The PowerGrade route, per clip, after the CDL: a declared DRX
    # replaces the whole node graph, so this runs inside the same clip
    # loop position rather than as a second pass that could disagree
    # about which items carry a grade.
    power_grade = None
    if project_folder:
        try:
            from library.tools.color_page_grade import (
                apply_power_grade,
                resolve_color_page_grade,
            )
            power_grade = resolve_color_page_grade(project_folder)
        except Exception as e:
            # A malformed declaration refuses the render rather than
            # rendering ungraded: a dropped grade ships forty minutes in.
            results["errors"].append(f"color.power_grade_drx refused: {e}")
            print(f"  ✗ color.power_grade_drx refused: {e}", file=sys.stderr)
            power_grade = "refused"

    if power_grade is not None and power_grade != "refused":
        print(f"\n── Color Grading (PowerGrade: "
              f"{os.path.basename(power_grade['path'])}) ──",
              file=sys.stderr)
        for track_idx in range(1, timeline.GetTrackCount("video") + 1):
            items = timeline.GetItemListInTrack("video", track_idx)
            if not items:
                continue
            for item in items:
                try:
                    mpi = item.GetMediaPoolItem()
                    clip_name = ((mpi.GetClipProperty("File Name")
                                  if mpi else None) or item.GetName()
                                 or "unnamed clip")
                except Exception:
                    clip_name = "unnamed clip"
                record = apply_power_grade(item, power_grade["path"])
                if record.get("applied"):
                    print(f"  ✓ Applied PowerGrade to {clip_name} "
                          f"({record.get('nodes')} nodes)", file=sys.stderr)
                else:
                    results["warnings"].append(
                        f"PowerGrade did not land on {clip_name}: "
                        f"{record.get('reason')}")
                    print(f"  ⚠ PowerGrade did not land on {clip_name}: "
                          f"{record.get('reason')}", file=sys.stderr)


    if verify_color_grades:
        _run_qa(verify_color_grades(timeline, None, manifest.get("color_grade", {})))

    _clock.lap("link_pass")
    # ══════════════════════════════════════════════════════════
    # LINK PASS: picture to speech, captions into the group
    # ══════════════════════════════════════════════════════════
    # Span-based, on the timeline as it stands AFTER the mix round trip
    # above rebound it: no handle from before that point is held here.
    # Every a-roll picture item links to the speech item starting on the
    # same frame of its angle's speech row. Every caption item whose
    # span falls inside a speech item joins that item's group - picture,
    # speech and caption in ONE SetClipsLinked call, because linking is
    # exclusive, not additive: a second pair-call breaks the first group
    # (measured 2026-09-09). The call's result is read back, not trusted.
    print(f"\n── Link Pass ──", file=sys.stderr)
    _speech_index = []  # (start, end, item, angle_key)
    # Room-tone fills (fidelity rung R5a) are speech-row items that
    # link ONLY inside their J/L group below - never by same-start
    # (no picture starts mid-clip) and never as a caption host (a
    # word-timed caption fully inside a wordless gap is a plan that
    # contradicts its own words). They are indexed separately and
    # kept out of the pair loop's index.
    _fill_starts = {
        int(f.get("timeline_in_frame")) for f in room_tone_fills
        if isinstance(f, dict) and f.get("timeline_in_frame") is not None
    }
    _main_speech = track_plan.speech_row_for_angle("main")
    _main_speech_idx = (_main_speech.index if _main_speech is not None
                        else None)
    for _srow in track_plan.speech_rows():
        try:
            _sitems = timeline.GetItemListInTrack("audio", _srow.index) or []
        except Exception:
            _sitems = []
        for _s in _sitems:
            try:
                _span = (_s.GetStart(), _s.GetEnd())
            except Exception:
                continue
            if (_main_speech_idx is not None
                    and _srow.index == _main_speech_idx
                    and _span[0] in _fill_starts):
                continue
            _speech_index.append((_span[0], _span[1], _s,
                                  _srow.occupant))

    def _picture_at(angle_key, start):
        _vrow = track_plan.video_row_for_angle(angle_key)
        if _vrow is None:
            return None
        try:
            _pitems = timeline.GetItemListInTrack("video", _vrow.index) or []
        except Exception:
            return None
        _exact = [p for p in _pitems
                  if _safe_span(p) is not None and _safe_span(p)[0] == start]
        if _exact:
            return _exact[0]
        try:
            _same = [p for p in _pitems if p.GetStart() == start]
        except Exception:
            return None
        return _same[0] if _same else None

    def _safe_span(item):
        try:
            return (item.GetStart(), item.GetEnd())
        except Exception:
            return None

    def _linked_ids(item):
        try:
            return {i.GetUniqueId() for i in (item.GetLinkedItems() or [])}
        except Exception:
            return set()

    def _item_uid(item):
        try:
            return item.GetUniqueId()
        except Exception:
            return None

    # J/L groups (fidelity rung R5a): an offset pair cannot link by
    # same-start - its starts differ BY DESIGN - so each group is
    # resolved here from the manifest records, by timeline frame: the
    # picture item, the offset speech item, and the fill. A claimed
    # speech item is never paired twice (linking is exclusive: a
    # second call breaks the first group), and the caption loop below
    # folds each fill into its host's group call instead of ejecting
    # it with a picture-plus-speech-only call.
    _v1_by_position = {}
    for _vc in v1_clips:
        if isinstance(_vc, dict) and _vc.get("spine_position") is not None:
            _v1_by_position[str(_vc.get("spine_position"))] = _vc
    _jl_claimed_speech = set()  # start frames the groups own
    _jl_host_fill = {}  # speech start frame -> fill timeline item
    _jl_pending = []  # (join label, kind, pic, speech, fill or None)
    for _plan in jl_cut_plans:
        if not isinstance(_plan, dict):
            continue
        _kind = _plan.get("kind")
        _join = (f"{_plan.get('outgoing_position')}->"
                 f"{_plan.get('incoming_position')}")
        _out = _v1_by_position.get(str(_plan.get("outgoing_position")))
        _inc = _v1_by_position.get(str(_plan.get("incoming_position")))
        if _out is None or _inc is None:
            results["warnings"].append(
                f"J/L link {_kind} join {_join}: positions match no V1 "
                f"clip - its items link by same-start only.")
            continue
        try:
            _pcut = int(round(float(_plan["picture_cut_timeline"]) * fps))
            _acut = int(round(float(_plan["audio_cut_timeline"]) * fps))
            _out_pic = int(_out["timeline_in_frame"])
            _in_pic = int(_inc["timeline_in_frame"])
        except (KeyError, TypeError, ValueError):
            results["warnings"].append(
                f"J/L link {_kind} join {_join}: records carry no "
                f"frames - its items link by same-start only.")
            continue
        if _kind == "j_cut":
            _pic_start, _speech_start, _fill_start = (
                _out_pic, _out_pic, _acut)
        elif _kind == "l_cut":
            _pic_start, _speech_start, _fill_start = (
                _in_pic, _acut, _pcut)
        else:
            results["warnings"].append(
                f"J/L link join {_join} names kind {_kind!r} - its "
                f"items link by same-start only.")
            continue
        _pic_item = _picture_at("main", _pic_start)
        # Speech and fill members come off the main speech row by
        # start frame: a same-start item on any other row is another
        # clip's sound, not this join's.
        _speech_item = None
        _fill_item = None
        if _main_speech_idx is not None:
            try:
                _row_items = (timeline.GetItemListInTrack(
                    "audio", _main_speech_idx) or [])
            except Exception:
                _row_items = []
            _by_start = {}
            for _cand in _row_items:
                try:
                    _by_start.setdefault(_cand.GetStart(), _cand)
                except Exception:
                    continue
            _speech_item = _by_start.get(_speech_start)
            _fill_item = _by_start.get(_fill_start)
        if _pic_item is None or _speech_item is None \
                or _fill_item is None:
            results["warnings"].append(
                f"J/L link {_kind} join {_join}: "
                f"{'picture ' if _pic_item is None else ''}"
                f"{'speech ' if _speech_item is None else ''}"
                f"{'fill ' if _fill_item is None else ''}unmatched on "
                f"the timeline - its items link by same-start only.")
            continue
        _jl_claimed_speech.add(_speech_start)
        _jl_host_fill[_speech_start] = _fill_item
        _jl_pending.append((_join, _kind, _pic_item, _speech_item,
                            _fill_item))

    for _join, _kind, _pic_item, _speech_item, _fill_item in _jl_pending:
        try:
            ok = timeline.SetClipsLinked(
                [_pic_item, _speech_item, _fill_item], True)
        except Exception as exc:
            results["warnings"].append(
                f"Link J/L {_kind} join {_join} raised {exc!r}")
            continue
        if not ok:
            results["warnings"].append(
                f"Link J/L {_kind} join {_join} declined")
            continue
        _have = _linked_ids(_speech_item)
        if _item_uid(_pic_item) in _have \
                and _item_uid(_fill_item) in _have:
            results["link_groups"].append(
                {"picture_start": _pic_item.GetStart(),
                 "speech_row_occupant": "main",
                 "members": 3, "jl_kind": _kind, "join": _join})
            print(f"  ✓ J/L {_kind} join {_join}: picture + speech + "
                  f"fill linked", file=sys.stderr)
        else:
            results["warnings"].append(
                f"Link J/L {_kind} join {_join} read back unlinked")
    for _start, _end, _s, _angle_key in _speech_index:
        if _start in _jl_claimed_speech and _angle_key == "main":
            # Claimed by a J/L group above: a J-cut's outgoing speech
            # starts with its picture, so the same-start pairing below
            # would link it a second time and break the triple. The
            # group call is the link; there is no second one.
            continue
        _pic = _picture_at(_angle_key, _start)
        _pic = _picture_at(_angle_key, _start)
        if _pic is None or _pic is _s:
            continue
        try:
            ok = timeline.SetClipsLinked([_pic, _s], True)
        except Exception as exc:
            results["warnings"].append(
                f"Link A-roll to speech at frame {_start} raised {exc!r}")
            continue
        if not ok:
            results["warnings"].append(
                f"Link A-roll to speech at frame {_start} declined")
            continue
        _have = _linked_ids(_s)
        if _item_uid(_pic) in _have:
            results["link_groups"].append(
                {"picture_start": _start, "speech_row_occupant": _angle_key,
                 "members": 2})
        else:
            results["warnings"].append(
                f"Link A-roll to speech at frame {_start} read back "
                f"unlinked")

    _cap_row = (track_plan.caption_row().index
                if track_plan.caption_row() is not None else None)
    if _cap_row is not None:
        try:
            _cap_items = timeline.GetItemListInTrack("video", _cap_row) or []
        except Exception:
            _cap_items = []
        # One link call per host, joining EVERY caption it spans. A
        # call per caption redefines the group each time, so only the
        # last caption per host stayed linked while every earlier one
        # read back linked at its own call - and the timeline checker
        # then failed all but one caption per speech span as unlinked.
        _caps_by_host = {}
        for _cap in _cap_items:
            _span = _safe_span(_cap)
            if _span is None:
                continue
            _cs, _ce = _span
            _hosts = [(s, e, item, key) for (s, e, item, key) in _speech_index
                      if s <= _cs and _ce <= e]
            if not _hosts:
                results["warnings"].append(
                    f"Caption at {_cs}-{_ce} falls inside no speech span; "
                    f"left unlinked")
                continue
            _ss, _se, _host, _hkey = _hosts[0]
            _pic = _picture_at(_hkey, _ss)
            _key = (_ss, _item_uid(_host))
            _entry = _caps_by_host.setdefault(_key, {
                "speech_start": _ss, "host": _host, "pic": _pic,
                "caps": []})
            _entry["caps"].append((_cs, _ce, _cap))
        for _key in sorted(_caps_by_host):
            _entry = _caps_by_host[_key]
            _host, _pic = _entry["host"], _entry["pic"]
            # A host claimed by a J/L group brings its fill along:
            # the group call below is the ONE call for this host, and
            # a picture-plus-speech-only call here would eject the
            # fill (linking is exclusive, not additive).
            _extra = ([_jl_host_fill[_entry["speech_start"]]]
                      if _entry["speech_start"] in _jl_host_fill else [])
            _group = ([_pic] if _pic is not None and _pic is not _host
                      else []) + [_host] + _extra + [
                          c for _, _, c in _entry["caps"]]
            try:
                ok = timeline.SetClipsLinked(_group, True)
            except Exception as exc:
                results["warnings"].append(
                    f"Caption link at {_entry['caps'][0][0]} raised {exc!r}")
                continue
            if not ok:
                results["warnings"].append(
                    f"Caption link at {_entry['caps'][0][0]} declined")
                continue
            for _cs, _ce, _cap in _entry["caps"]:
                _have = _linked_ids(_cap)
                _want = {_item_uid(m) for m in _group if m is not _cap}
                _want.discard(None)
                if _want and _want <= _have:
                    results["caption_links"].append(
                        {"caption_start": _cs, "caption_end": _ce,
                         "speech_start": _entry["speech_start"],
                         "members": len(_group)})
                    print(f"  ✓ Caption {_cs}-{_ce} joins speech "
                          f"{_entry['speech_start']} ({len(_group)}-group)",
                          file=sys.stderr)
                else:
                    results["warnings"].append(
                        f"Caption link at {_cs} read back unlinked")

    _clock.lap("track_labels")
    # ══════════════════════════════════════════════════════════
    print(f"\n── Track Labels ──", file=sys.stderr)
    # Names come from the plan, which named them from the material, and
    # they are applied BEFORE empty rows are deleted below: deleting a
    # middle row shifts every row above it down, so naming afterwards
    # would hang the wrong names on the survivors. Nothing here invents
    # a name: a row the plan did not name is an error, not a fallback.
    _plan_names = {(t.media_type, t.index): t.name
                   for t in track_plan.video_tracks + track_plan.audio_tracks}
    for i in range(1, timeline.GetTrackCount("video") + 1):
        label = _plan_names.get(("video", i))
        if label is None:
            results["errors"].append(
                f"Video row {i} is not on the track plan; refusing to name it.")
            continue
        timeline.SetTrackName("video", i, label)
        print(f"  V{i}: {label}", file=sys.stderr)

    for i in range(1, timeline.GetTrackCount("audio") + 1):
        label = _plan_names.get(("audio", i))
        if label is None:
            results["errors"].append(
                f"Audio row {i} is not on the track plan; refusing to name it.")
            continue
        timeline.SetTrackName("audio", i, label)
        print(f"  A{i}: {label}", file=sys.stderr)

    _clock.lap("occupancy")
    # ══════════════════════════════════════════════════════════
    # OCCUPANCY: a row with nothing on it leaves the timeline
    # ══════════════════════════════════════════════════════════
    # The plan creates a row because something goes on it. When every
    # placement for a row failed, keeping the blank row is exactly the
    # defect being fixed - the row is deleted, and the deletion is on
    # the record. Rows Resolve created on its own that the plan never
    # asked for go the same way when empty, and are errors when
    # occupied. Delete from the top down so indices below hold still.
    # The one exception is a PLANNED-empty V1: a voiceover-led cut
    # mints the legacy row with nothing to place (the picture rides
    # V2 by design), and deleting it would shift every row above it
    # down - B-roll onto V1, captions onto V2 - against the plan, the
    # names and every check that reads them. The same holds for a
    # PLANNED-empty speech row on a music-led cut (no A-roll words, no
    # voiceover): deleting it drops the music row onto A1.
    _keep_planned_empty_v1 = not v1_clips
    _keep_planned_empty_speech = {
        t.index for t in track_plan.speech_rows()
    } if not _spine_has_speech else set()
    for _mt in ("video", "audio"):
        try:
            _count = timeline.GetTrackCount(_mt) or 0
        except Exception:
            continue
        for _idx in range(_count, 0, -1):
            _spec = _plan_names.get((_mt, _idx))
            try:
                _items = timeline.GetItemListInTrack(_mt, _idx) or []
            except Exception:
                continue
            if _items:
                if _spec is None:
                    results["errors"].append(
                        f"Unplanned {_mt} row {_idx} carries "
                        f"{len(_items)} item(s); refusing to keep it.")
                continue
            if (_keep_planned_empty_v1 and _mt == "video" and _idx == 1
                    and _spec is not None):
                print(f"  Keeping planned-empty V1 ({_spec}): "
                      f"voiceover-led picture rides V2 by design",
                      file=sys.stderr)
                continue
            if (_mt == "audio" and _idx in _keep_planned_empty_speech
                    and _spec is not None):
                print(f"  Keeping planned-empty {_spec} (A{_idx}): "
                      f"music-led cut carries no speech by design",
                      file=sys.stderr)
                continue
            try:
                _gone = timeline.DeleteTrack(_mt, _idx)
            except Exception as exc:
                results["errors"].append(
                    f"Empty {_mt.upper()}{_idx} "
                    f"({_spec or 'unplanned'}) could not be removed: {exc}")
                continue
            if _gone:
                results["deleted_empty_tracks"].append(
                    {"media_type": _mt, "index": _idx,
                     "name": _spec or "unplanned"})
                print(f"  ✗ Empty row {_mt.upper()}{_idx} "
                      f"({_spec or 'unplanned'}) removed", file=sys.stderr)
            else:
                results["errors"].append(
                    f"Empty {_mt.upper()}{_idx} "
                    f"({_spec or 'unplanned'}) declined deletion")

    # The DRP transition-surgery pass used to sit here. It exported the
    # project to a temp .drp, edited it, and printed "RELOAD REQUIRED /
    # please import this DRP manually" - while step_6_01_render went on to
    # export the live, unmodified timeline. It could not fire in any case:
    # it needed a from_block or "between_N_M" position that the transition
    # spec has never carried. Transitions go through Fusion (see
    # library/tools/transition_vocabulary.py); the surgery tool and its
    # test remain in the tree unused.

    _clock.lap("verification")
    # ══════════════════════════════════════════════════════════
    # VERIFICATION
    # ══════════════════════════════════════════════════════════
    print(f"\n── Verification ──", file=sys.stderr)
    resolve.OpenPage("edit")

    if run_full_timeline_qa:
        final_report = run_full_timeline_qa(timeline, project, manifest)
        _run_qa(final_report)
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

            # Each frame grab is a REAL Deliver-page render, polled to
            # completion. This loop never ran in production, because
            # `plan_qa_checks` read a top-level "clips" key that has never
            # existed and so returned zero grabs; fixing that switched on a
            # dormant path that renders once per placed clip and would add
            # minutes to every export unasked.
            #
            # So it is opt-in, on the same switch as the perceptual
            # observation it feeds. Turning a key-name bug into a silent
            # multi-minute cost would be a poor trade for a fix.
            if not perceptual_qa_enabled():
                print(f"  Skipped: {len(qa_plan.frame_grabs)} frame grab(s) "
                      f"and {len(qa_plan.segment_checks)} segment check(s) "
                      f"available. Set PIPELINE_PERCEPTUAL_QA=1 to run them.",
                      file=sys.stderr)
                qa_plan.frame_grabs = []
                qa_plan.segment_checks = []

            # ONE Resolve render for the whole pass: the composite grabs,
            # the segment checks and the perceptual observation's frames
            # are batched (`segment_renderer.render_batch`); grabs whose
            # question the source file answers never reach Resolve
            # (`qa_fidelity`).
            perceptual_frames = []
            if run_perceptual_observation and qa_plan.frame_grabs:
                perceptual_frames = [
                    g.frame_number
                    for g in perceptual_sample(qa_plan)[0]]
            execution = execute_qa_plan(
                resolve, project, timeline, qa_plan,
                extra_frames=perceptual_frames)

            for fg_req, res in zip(qa_plan.frame_grabs,
                                   execution.frame_results):
                if res.image_path:
                    res.check = analyze_frame_locally(
                        res.image_path, fg_req.check_type, fg_req.context,
                        project_folder=project_folder or None,
                        step_id="render")
                visual_qa_results.append(format_frame_grab_for_llm(res))
                
            for res in execution.segment_results:
                visual_qa_results.append(format_segment_result_for_llm(res))
                
            if visual_qa_results:
                results["visual_qa"] = visual_qa_results
            print(f"  ✓ Completed {len(qa_plan.frame_grabs)} frame grabs and {len(qa_plan.segment_checks)} segment checks", file=sys.stderr)

            # ── Perceptual observation (Q8) ──
            # A model that WATCHES the render. Every other gate here is
            # technical and none of them would catch a letterboxed edit
            # with the subject's head cropped off.
            #
            # OBSERVATION ONLY: this never fails a build and never touches
            # `success`. There is no evidence yet about its false-positive
            # rate, and it has not been calibrated against the captain.
            if run_perceptual_observation:
                try:
                    observation = run_perceptual_observation(
                        resolve, project, timeline, manifest, fps=fps,
                        project_folder=project_folder or None,
                        rendered_frames=execution.composite_frames)
                    if observation:
                        results["perceptual_observation"] = observation
                        n = len(observation.get("findings", []))
                        print(f"\n── Perceptual QA (observation only) ──",
                              file=sys.stderr)
                        print(f"  {observation['frames_examined']} frames "
                              f"examined, {n} observation(s)", file=sys.stderr)
                        if observation.get("frames_not_examined"):
                            print(f"  {observation['frames_not_examined']} "
                                  f"frame(s) not examined (cost bound)",
                                  file=sys.stderr)
                        for f in observation.get("findings", []):
                            print(f"    · frame {f['frame']} [{f['dimension']}]"
                                  f" {f['detail'] or f['value']}",
                                  file=sys.stderr)
                        print("  Not a gate. Nothing here failed the build.",
                              file=sys.stderr)
                except Exception as e:
                    # An observation that breaks must not break a render.
                    print(f"  ⚠ Perceptual QA unavailable: {e}", file=sys.stderr)
                    results["warnings"].append(f"Perceptual QA unavailable: {e}")
        except Exception as e:
            print(f"  ✗ Visual QA router failed: {e}", file=sys.stderr)
            results["warnings"].append(f"Visual QA router failed: {e}")

    print(f"\n── QA Summary ──", file=sys.stderr)
    for rep in qa_reports:
        print(f"  Station {rep.station}: {'Passed' if rep.passed else 'Failed'}", file=sys.stderr)

    # Derive the verification verdict from the station outcomes it collects.
    # The logic lives in build_verification.derive_verification_verdict so
    # it can be tested with plain data objects, without Resolve.
    #
    # verification_passed reflects whether QA stations passed.
    # success is deliberately NOT gated on QA stations - that is step two
    # of the captain's ruling (see tests/unit/resolve/test_resolve_build_timeline.py::
    # test_loud_banner_prints_on_qa_failure_but_not_fatal).
    verification_passed = derive_verification_verdict(qa_reports)

    results["success"] = not results["errors"]
    results["verification_passed"] = verification_passed

    status_emoji = "✓" if results["success"] else "✗"
    print(f"\n{status_emoji} Build {'succeeded' if results['success'] else 'FAILED'}", file=sys.stderr)

    # Say this loudly and on its own, immediately under the verdict. A
    # build that succeeded with failing QA stations is a specific and
    # important state, and it must not read like a clean run.
    if results["qa_failures"]:
        stations = sorted({f["station"] for f in results["qa_failures"]})
        print(f"\n{'!' * 60}", file=sys.stderr)
        print(f"  {len(results['qa_failures'])} QA CHECK FAILURE(S) across "
              f"{len(stations)} station(s): {', '.join(stations)}",
              file=sys.stderr)
        print("  The timeline was built. These checks say part of it is "
              "not what the manifest asked for.", file=sys.stderr)
        for f in results["qa_failures"]:
            print(f"    ✗ [{f['station']}] {f['check']}: "
                  f"expected {f['expected']}, got {f['actual']}", file=sys.stderr)
        print("  Not fatal by ruling, pending evidence on how often these "
              "fire on real footage.", file=sys.stderr)
        print(f"{'!' * 60}", file=sys.stderr)

    if results["errors"]:
        for e in results["errors"]:
            print(f"  ERROR: {e}", file=sys.stderr)
    if results["warnings"]:
        for w in results["warnings"]:
            print(f"  WARNING: {w}", file=sys.stderr)

    _clock.lap("version_record")
    # ══════════════════════════════════════════════════════════
    # VERSION-CONTROL RECORD (per-project git repo)
    # ══════════════════════════════════════════════════════════
    # AFTER comps and grade, so the record describes the finished
    # timeline: a final OTIO export (the mix path's .delivered.otio
    # only exists when levels were delivered), the serializer read,
    # and a note naming what the export cannot see - committed
    # together with the .comp files.  Never fails the build.
    if project_folder:
        try:
            from library.tools.versions import store as _store
            _vc = _store.record_finished_timeline(
                resolve, timeline, project_folder, timeline_name)
            results["build_record"] = {
                k: v for k, v in _vc.items() if k != "files"}
            if _vc.get("committed"):
                print(f"\n── Version control: committed {_vc['commit']} "
                      f"({len(_vc.get('files', []))} file(s)) ──",
                      file=sys.stderr)
            else:
                msg = (f"version-control record not committed: "
                       f"{_vc.get('reason', 'unknown')}")
                results["warnings"].append(msg)
                print(f"  ⚠ {msg}", file=sys.stderr)
        except Exception as exc:
            msg = f"version-control record failed: {exc!r}"
            results["warnings"].append(msg)
            print(f"  ⚠ {msg}", file=sys.stderr)

    _clock.lap(None)
    return results


def build_timeline(
    manifest: dict,
    subtitle_overlay_path: Optional[str] = None,
    motion_graphics_path: Optional[str] = None,
    project_name: Optional[str] = None,
    project_folder: str = "",
) -> dict:
    """Prepare a placement plan, then execute it under Resolve and machine leases."""
    prepared = _prepare_timeline_build(
        manifest, subtitle_overlay_path, motion_graphics_path, project_folder)
    if prepared["errors"]:
        return {"success": False, "errors": prepared["errors"]}
    return _build_timeline_under_lease(
        manifest,
        subtitle_overlay_path=subtitle_overlay_path,
        motion_graphics_path=motion_graphics_path,
        project_name=project_name,
        project_folder=project_folder,
        _prepared=prepared,
    )


# The routed operation is the public entry point. Its lease is deliberately
# acquired by the prepared worker only after the wrapper has finished offline
# manifest, path and placement-plan work.
build_timeline.__resolve_lease__ = _build_timeline_under_lease.__resolve_lease__
build_timeline.__resolve_execution__ = _build_timeline_under_lease.__resolve_execution__


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
        args = parser.parse_args()

        with open(args.manifest) as f:
            manifest = json.load(f)
        subtitle_overlay = args.subtitle_overlay
        motion_graphics = args.motion_graphics
        project_name = args.project

    result = build_timeline(
        manifest,
        subtitle_overlay_path=subtitle_overlay,
        motion_graphics_path=motion_graphics,
        project_name=project_name,
    )

    # BUG FIX C7: Output structured JSON result to stdout (only JSON, no
    # other prints - all status logging goes to stderr).
    json.dump({"rendered_output": result}, sys.stdout, indent=2, default=str)
