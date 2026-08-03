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
import subprocess
import sys


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
        "halation": "optional_subtle",
    },
    "node_5": {
        "type": "color_space_transform",
        "to": "rec709_gamma24",
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
            capture_output=True, text=True, timeout=30,
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


def define_color_grade(shot_list: dict, project_folder: str = "") -> dict:
    """Define the color grading specification based on style spec.

    Runs per-clip exposure analysis via ffprobe to estimate brightness
    offsets instead of defaulting everything to 0.0.
    """
    entries = shot_list.get("entries", [])

    # Identify clips that may need per-clip adjustments
    per_clip_adjustments = []
    seen_clips = set()

    for entry in entries:
        # Only process video entries (V1/V2), and only once per clip
        if entry["track"] not in ("V1", "V2"):
            continue
        if entry["clip_id"] in seen_clips:
            continue
        seen_clips.add(entry["clip_id"])

        # Estimate exposure from the source file
        source_file = entry.get("source_file", "")
        if source_file and not os.path.isabs(source_file) and project_folder:
            source_file = os.path.join(project_folder, source_file)

        exposure_offset = _estimate_exposure(source_file)

        if exposure_offset == 0.0:
            notes = "Exposure within normal range, no adjustment needed"
        elif exposure_offset > 0:
            notes = f"Clip underexposed, brightening by {exposure_offset:.3f}"
        else:
            notes = f"Clip overexposed, darkening by {abs(exposure_offset):.3f}"

        per_clip_adjustments.append({
            "entry_id": entry["entry_id"],
            "clip_id": entry["clip_id"],
            "exposure_offset": exposure_offset,
            "white_balance_override": None,
            "notes": notes,
        })

    return {
        "color_grade_spec": {
            "grade_pipeline": GRADE_PIPELINE,
            "per_clip_adjustments": per_clip_adjustments,
            "output_color_space": "Rec.709, Gamma 2.4",
            "consistency_notes": (
                "Grade pipeline is uniform across all clips. "
                "Per-clip exposure offsets are estimated from average "
                "brightness analysis via ffprobe signalstats."
            ),
        },
    }


def main():
    input_data = json.loads(sys.stdin.read())
    project_folder = input_data.get("project_folder", "")

    # Build a shot_list-compatible structure from upstream data
    entries = []

    # A-roll assignments
    for assignment in input_data.get("a_roll_assignments", []):
        for seg in assignment.get("video_segments", []):
            entries.append({
                "track": "V1",
                "clip_id": seg.get("clip_id", assignment.get("source_clip_id", "")),
                "entry_id": seg.get("segment_id", ""),
                "source_file": seg.get("source_file", assignment.get("source_file", "")),
            })

    # B-roll assignments
    for broll in input_data.get("b_roll_assignments", []):
        entries.append({
            "track": "V2",
            "clip_id": broll.get("clip_id", broll.get("source_clip_id", "")),
            "entry_id": broll.get("assignment_id", ""),
            "source_file": broll.get("source_file", ""),
        })

    shot_list = {"entries": entries}
    result = define_color_grade(shot_list, project_folder)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
