#!/usr/bin/env python3
"""
Step 5.1: Define Color Grading Specification

Define the color grading parameters — the node tree, per-clip adjustments,
and overall look per the style specification. This step is deterministic
because the parameters come directly from the style spec.

Classification: Deterministic / Specification
Input:  { "shot_list": {...} }
Output: { "color_grade_spec": { grade_pipeline, per_clip_adjustments } }
"""
import json
import os
import re
import subprocess
import sys
import time


# Style spec color grading pipeline (fixed)
GRADE_PIPELINE = {
    "node_1": {
        "type": "color_space_transform",
        "from": "camera",
        "to": "davinci_wide_gamut_intermediate",
    },
    "node_2": {
        "type": "primary_correction",
        "white_balance_offset": 200,
        "contrast_curve": "gentle_s",
        "lift_shadows": 0.02,
        "roll_highlights": -0.03,
        "saturation": "+10-15%",
    },
    "node_3": {
        "type": "warm_tone_shaping",
        "offset_red": 0.01,
        "offset_green": 0.005,
        "highlights": "warm_golden",
        "shadows": "cool_teal_hint",
        "skin_tone_protection": True,
    },
    "node_4": {
        "type": "creative_film_look",
        "glow_opacity": "10-15%",
        "grain_amount": "0.2-0.3",
        "vignette_amount": "0.15-0.20",
    },
    "node_5": {
        "type": "color_space_transform",
        "to": "rec709_gamma24",
    },
}


def _midpoint(spec: str, default: float) -> float:
    """Middle of a designed range like "10-15%" or "0.2-0.3".

    The pipeline's numbers come from the design above rather than being
    invented at the point of use, so a change to GRADE_PIPELINE moves the
    picture.
    """
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(spec))]
    if not numbers:
        return default
    value = sum(numbers) / len(numbers)
    return value / 100.0 if "%" in str(spec) else value


def _fusion_look(pipeline: dict) -> dict:
    """node_4's creative film look, as parameters the renderer reads.

    The five-node grade was designed and only node_2 ever reached the
    picture, as a CDL. Glow, grain and vignette are Fusion nodes the comp
    engine already draws (fx.glow / fx.grain / fx.vignette), so node_4 is
    delivered by emitting the parameter names apply_fusion_comps
    dispatches on. Halation has no Fusion builder and is not emitted -
    see grade_pipeline_delivery.
    """
    look = pipeline.get("node_4", {})
    glow = _midpoint(look.get("glow_opacity"), 0.12)
    grain = _midpoint(look.get("grain_amount"), 0.25)
    vignette = _midpoint(look.get("vignette_amount"), 0.175)
    return {
        "glow_gain": round(glow, 4),
        "glow_threshold": 0.75,
        "glow_size": 3.5,
        "film_grain": True,
        "film_grain_power": round(grain, 4),
        "film_grain_size": 1.5,
        "vignette": True,
        "vignette_blend": round(vignette, 4),
        "vignette_soft": 0.35,
    }


#: What actually happens to each designed node. A node with no
#: `delivered_by` does not reach the viewer, and the reason says why -
#: four of the five used to be silently unread.
GRADE_PIPELINE_DELIVERY = {
    "node_1": {
        "delivered_by": None,
        "reason": "A camera to DaVinci Wide Gamut transform is project-level "
                  "colour management, not a per-clip grade. Setting it would "
                  "change the colour science of the whole project.",
    },
    "node_2": {
        "delivered_by": "per_clip_adjustments[].cdl_values",
        "partial": ["contrast_curve", "roll_highlights"],
        "reason": "An ASC CDL has slope/offset/power/saturation only, so the "
                  "gentle-S contrast curve and the highlight roll-off have "
                  "nowhere to go. White balance, shadow lift, power and "
                  "saturation are carried.",
    },
    "node_3": {
        "delivered_by": None,
        "reason": "Warm/cool tone shaping with skin-tone protection needs a "
                  "hue qualifier node, which the Resolve API cannot create.",
    },
    "node_4": {"delivered_by": "fusion_look"},
    "node_5": {
        "delivered_by": None,
        "reason": "See node_1: the Rec.709 output transform is project-level "
                  "colour management. output_color_space records the intent.",
    },
}

# Reference brightness target (0-255 scale). Typical well-exposed
# iPhone footage sits around 115-130. We aim for the middle.
_REFERENCE_BRIGHTNESS = 122.0

# Maximum exposure offset we'll suggest (in stops-like units).
# Keeps adjustments conservative to avoid blowing out highlights.
_MAX_EXPOSURE_OFFSET = 0.5


def _estimate_exposure(filepath: str) -> float:
    """Estimate per-clip exposure offset by measuring average brightness.

    Uses ffprobe's signalstats filter to get the YAVG (luma average) of a
    sample of frames (every 5th frame, capped at first 10 seconds), then
    compares against the reference brightness to produce an offset.

    Returns:
        Exposure offset in approximate stops. Positive = brighten,
        negative = darken. Returns 0.0 if analysis fails.
    """
    if not filepath or not os.path.exists(filepath):
        return 0.0

    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet',
             '-f', 'lavfi',
             '-i', f'movie={filepath},select=not(mod(n\\,5)),signalstats',
             '-show_entries', 'frame_tags=lavfi.signalstats.YAVG',
             '-of', 'csv=p=0',
             '-read_intervals', '%+10',  # first 10 seconds only
             ],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )

        if result.returncode != 0 or not result.stdout.strip():
            return 0.0

        # Parse YAVG values and compute mean
        values = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if line:
                try:
                    values.append(float(line))
                except ValueError:
                    continue

        if not values:
            return 0.0

        avg_brightness = sum(values) / len(values)

        # Compute offset: positive means clip is dark (needs brightening)
        # Scale factor: 25 units of brightness ~ 0.5 stops
        raw_offset = (_REFERENCE_BRIGHTNESS - avg_brightness) / 50.0
        # Clamp to safe range
        return max(-_MAX_EXPOSURE_OFFSET,
                   min(_MAX_EXPOSURE_OFFSET, round(raw_offset, 3)))

    except (subprocess.TimeoutExpired, FileNotFoundError,
            ValueError, OSError):
        return 0.0


def _extract_frame(video_path: str) -> str:
    """Extract a single frame from video for color analysis."""
    import tempfile
    if not os.path.exists(video_path):
        return ""
    fd, path = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    try:
        subprocess.run(
            ['ffmpeg', '-y', '-v', 'quiet', '-i', video_path, '-vframes', '1', '-q:v', '2', path],
            check=True
        )
        return path
    except Exception:
        if os.path.exists(path):
            os.remove(path)
        return ""

def define_color_grade(shot_list: dict, project_folder: str = "", reference_image: str = "") -> dict:
    """Define the color grading specification based on style spec.

    Runs per-clip exposure analysis via ffprobe to estimate brightness
    offsets instead of defaulting everything to 0.0.
    """
    entries = shot_list.get("entries", [])

    # Identify clips that may need per-clip adjustments
    per_clip_adjustments = []
    seen_clips = set()
    clip_frames = {}

    for entry in entries:
        if entry["track"] not in ("V1", "V2"):
            continue
        if entry["clip_id"] in seen_clips:
            continue
        seen_clips.add(entry["clip_id"])

        source_file = entry.get("source_file", "")
        if source_file and not os.path.isabs(source_file) and project_folder:
            source_file = os.path.join(project_folder, source_file)
            
        if reference_image:
            frame_path = _extract_frame(source_file)
            if frame_path:
                clip_frames[entry["clip_id"]] = frame_path

    # Compute matches if reference is provided
    cdl_matches = {}
    if reference_image:
        try:
            from library.tools.look_matcher import match_clips_to_reference
            cdl_matches = match_clips_to_reference(reference_image, clip_frames)
        except ImportError:
            pass
            
    # Cleanup temp frames
    for frame_path in clip_frames.values():
        if os.path.exists(frame_path):
            os.remove(frame_path)

    seen_clips.clear()

    for entry in entries:
        if entry["track"] not in ("V1", "V2"):
            continue
        if entry["clip_id"] in seen_clips:
            continue
        seen_clips.add(entry["clip_id"])

        source_file = entry.get("source_file", "")
        if source_file and not os.path.isabs(source_file) and project_folder:
            source_file = os.path.join(project_folder, source_file)

        if reference_image and entry["clip_id"] in cdl_matches:
            match = cdl_matches[entry["clip_id"]]
            notes = "AI Look Match CDL applied"
            cdl_values = {
                "slope_r": match["slope"][0],
                "slope_g": match["slope"][1],
                "slope_b": match["slope"][2],
                "offset_r": match["offset"][0],
                "offset_g": match["offset"][1],
                "offset_b": match["offset"][2],
                "power_r": match["power"][0],
                "power_g": match["power"][1],
                "power_b": match["power"][2],
                "saturation": match["saturation"]
            }
            exposure_offset = 0.0
        else:
            exposure_offset = _estimate_exposure(source_file)
    
            if exposure_offset == 0.0:
                notes = "Exposure within normal range, no adjustment needed"
            elif exposure_offset > 0:
                notes = f"Clip underexposed, brightening by {exposure_offset:.3f}"
            else:
                notes = f"Clip overexposed, darkening by {abs(exposure_offset):.3f}"
    
            slope = round(2.0 ** exposure_offset, 3)
            node_2 = GRADE_PIPELINE["node_2"]
            wb_offset = node_2.get("white_balance_offset", 0)
            # node_2's shadow lift is a CDL offset on all three channels.
            # It was designed and then dropped: only white balance reached
            # the offsets, so the grade sat 0.02 darker than intended.
            lift = node_2.get("lift_shadows", 0.0)
            offset_r = round(wb_offset / 10000.0 + lift, 3)
            offset_g = round(lift, 3)
            offset_b = round(-wb_offset / 10000.0 + lift, 3)

            cdl_values = {
                "slope_r": slope,
                "slope_g": slope,
                "slope_b": slope,
                "offset_r": offset_r,
                "offset_g": offset_g,
                "offset_b": offset_b,
                "power_r": 0.95,
                "power_g": 0.95,
                "power_b": 0.95,
                "saturation": 1.12
            }

        per_clip_adjustments.append({
            "entry_id": entry.get("entry_id", ""),
            "clip_id": entry["clip_id"],
            "source_file": source_file,
            "exposure_offset": exposure_offset,
            "white_balance_override": None,
            "notes": notes,
            "cdl_values": cdl_values
        })

    return {
        "color_grade_spec": {
            "grade_pipeline": GRADE_PIPELINE,
            "grade_pipeline_delivery": GRADE_PIPELINE_DELIVERY,
            "per_clip_adjustments": per_clip_adjustments,
            # node_4, as Fusion parameters. compile_manifest merges this
            # onto every V1/V2 clip's effects.
            "fusion_look": _fusion_look(GRADE_PIPELINE),
            "output_color_space": "Rec.709, Gamma 2.4",
            "consistency_notes": (
                "Grade pipeline is uniform across all clips. "
                "Per-clip exposure offsets are estimated from average "
                "brightness analysis via ffprobe signalstats."
            ),
            # No PowerGrade ships with the pipeline:
            # library/presets/powergrades/default.drx is a placeholder, not
            # a real .drx. Set this to a real file to have the renderer
            # apply it - it will fail loudly if the path does not resolve.
            "powergrade_path": None,

        },
    }


def main():
    import sys
    input_data = json.loads(sys.stdin.read())
    project_folder = input_data.get("project_folder", "")
    brand_template = input_data.get("brand_template", {})
    style = brand_template.get("style", {})
    reference_image = style.get("reference_look_image", "")

    
    entries = []

    # A-roll assignments
    for assignment in input_data.get("a_roll_assignments", []):
        vsegs = assignment.get("video_segments", [])
        if vsegs:
            for seg_idx, seg in enumerate(vsegs):
                entries.append({
                    "track": "V1",
                    "clip_id": seg.get("clip_id", assignment.get("source_clip_id", "")),
                    "entry_id": seg.get("segment_id", seg.get("clip_id", f"{assignment.get('spine_block_position')}_seg{seg_idx}")),
                    "source_file": seg.get("source_file", assignment.get("source_file", "")),
                })
        else:
            entries.append({
                "track": "V1",
                "clip_id": assignment.get("source_clip_id", assignment.get("clip_id", "")),
                "entry_id": assignment.get("assignment_id", ""),
                "source_file": assignment.get("source_file", ""),
            })

    # B-roll assignments
    for broll in input_data.get("b_roll_assignments", []):
        assigned = broll.get("assigned_clip", broll)
        entries.append({
            "track": "V2",
            "clip_id": assigned.get("clip_id", assigned.get("source_clip_id", "")),
            "entry_id": broll.get("assignment_id", ""),
            "source_file": assigned.get("source_file", broll.get("source_file", "")),
        })

    # B-roll interjections
    for interj in input_data.get("b_roll_interjections", []):
        clip = interj.get("assigned_clip", interj)
        entries.append({
            "track": "V2",
            "clip_id": clip.get("clip_id", clip.get("source_clip_id", "")),
            "entry_id": interj.get("assignment_id", ""),
            "source_file": clip.get("source_file", interj.get("source_file", "")),
        })

    shot_list = {"entries": entries}
    result = define_color_grade(shot_list, project_folder, reference_image)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
