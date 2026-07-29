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


def define_color_grade(shot_list: dict) -> dict:
    """
    Define the color grading specification based on style spec.
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

        # Default: no adjustment needed
        # In a real implementation, exposure analysis would happen here
        per_clip_adjustments.append({
            "entry_id": entry["entry_id"],
            "clip_id": entry["clip_id"],
            "exposure_offset": 0.0,
            "white_balance_override": None,
            "notes": "Default grade — adjust if clip is over/underexposed",
        })

    return {
        "color_grade_spec": {
            "grade_pipeline": GRADE_PIPELINE,
            "per_clip_adjustments": per_clip_adjustments,
            "output_color_space": "Rec.709, Gamma 2.4",
            "consistency_notes": (
                "Grade pipeline is uniform across all clips. "
                "Per-clip exposure adjustments may be needed for clips "
                "shot in different lighting conditions."
            ),
        },
    }


def main():
    input_data = json.loads(sys.stdin.read())

    # Build a shot_list-compatible structure from upstream data
    # The grade pipeline is uniform, so we just need clip IDs for per-clip adjustments
    entries = []

    # A-roll assignments
    for assignment in input_data.get("a_roll_assignments", []):
        for seg in assignment.get("video_segments", []):
            entries.append({
                "track": "V1",
                "clip_id": seg.get("clip_id", assignment.get("source_clip_id", "")),
                "entry_id": seg.get("segment_id", ""),
            })

    # B-roll assignments
    for broll in input_data.get("b_roll_assignments", []):
        entries.append({
            "track": "V2",
            "clip_id": broll.get("clip_id", broll.get("source_clip_id", "")),
            "entry_id": broll.get("assignment_id", ""),
        })

    shot_list = {"entries": entries}
    result = define_color_grade(shot_list)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
