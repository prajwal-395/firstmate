"""
Presets and Recipes for the Fusion Composition Engine.

SEGMENT_PRESETS_FLAT — the original flat-param format for backward
compatibility with resolve_build_timeline.py.

SEGMENT_RECIPES — structured recipes mapping effect names to params.
Used by CompEngine.from_preset().
"""

# ─── Flat Presets (backward compat) ──────────────────────────
# These match the exact format used by the old fusion_comp_generator.py
# and resolve_build_timeline.py. Keys are the flat param names
# consumed by CompEngine.from_params().

SEGMENT_PRESETS_FLAT = {
    "HOOK": {
        "zoom_start": 1.0, "zoom_mid": 1.04, "zoom_end": 1.03,
        "pan_start": (0.5, 0.5), "pan_end": (0.5, 0.49),
        "grade_gain": 1.05, "grade_contrast": 0.04, "grade_saturation": 1.15,
        "glow_gain": 0.08,
    },
    "CORE_INSIGHT": {
        "zoom_start": 1.02, "zoom_mid": 1.0, "zoom_end": 1.02,
        "grade_gain": 1.03, "grade_contrast": 0.04, "grade_saturation": 1.10,
        "glow_gain": 0.08,
    },
    "TURNING_POINT": {
        "zoom_start": 1.0, "zoom_mid": 1.04, "zoom_end": 1.04,
        "grade_gain": 1.05, "grade_contrast": 0.06, "grade_saturation": 1.15,
        "glow_gain": 0.12, "glow_threshold": 0.72, "glow_size": 4.0,
    },
    "EMOTIONAL_PEAK": {
        "zoom_start": 1.0, "zoom_mid": 1.04, "zoom_end": 1.03,
        "pan_start": (0.5, 0.5), "pan_end": (0.5, 0.48),
        "grade_gain": 1.06, "grade_contrast": 0.05, "grade_saturation": 1.18,
        "glow_gain": 0.10, "glow_threshold": 0.70, "glow_size": 4.5,
        "film_grain": True, "film_grain_power": 0.15,
    },
    "RESOLUTION": {
        "zoom_start": 1.03, "zoom_mid": 1.0, "zoom_end": 1.02,
        "pan_start": (0.5, 0.5), "pan_end": (0.51, 0.5),
        "grade_gain": 1.02, "grade_contrast": 0.03, "grade_saturation": 1.10,
        "glow_gain": 0.08,
    },
    "B_ROLL_CINEMATIC": {
        "zoom_start": 1.02, "zoom_mid": 1.04, "zoom_end": 1.02,
        "pan_start": (0.5, 0.5), "pan_end": (0.52, 0.49),
        "grade_gain": 1.08, "grade_contrast": 0.06, "grade_saturation": 1.20,
        "glow_gain": 0.10,
        "film_grain": True, "film_grain_power": 0.20,
    },
    "OUTRO": {
        "zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0,
        "grade_gain": 1.05, "grade_contrast": 0.04, "grade_saturation": 1.08,
        "glow_gain": 0.06, "glow_threshold": 0.78, "glow_size": 3.0,
        "fade_out_frames": 15,
    },
}

# Re-export for backward compat
SEGMENT_PRESETS = SEGMENT_PRESETS_FLAT


# ─── Structured Recipes ──────────────────────────────────────
# Each recipe maps effect function names to their params.
# Used by CompEngine.from_preset().

SEGMENT_RECIPES = {
    "HOOK": {
        "zoom": {"start": 1.0, "mid": 1.04, "end": 1.03, "pan_end": (0.5, 0.49)},
        "grade": {"gain": 1.05, "contrast": 0.04, "saturation": 1.15},
        "glow": {"gain": 0.08},
        "vignette": {},
    },
    "CORE_INSIGHT": {
        "zoom": {"start": 1.02, "mid": 1.0, "end": 1.02},
        "grade": {"gain": 1.03, "contrast": 0.04, "saturation": 1.10},
        "glow": {"gain": 0.08},
        "vignette": {},
    },
    "TURNING_POINT": {
        "zoom": {"start": 1.0, "mid": 1.04, "end": 1.04},
        "grade": {"gain": 1.05, "contrast": 0.06, "saturation": 1.15},
        "glow": {"gain": 0.12, "threshold": 0.72, "size": 4.0},
        "vignette": {},
    },
    "EMOTIONAL_PEAK": {
        "zoom": {"start": 1.0, "mid": 1.04, "end": 1.03, "pan_end": (0.5, 0.48)},
        "grade": {"gain": 1.06, "contrast": 0.05, "saturation": 1.18},
        "glow": {"gain": 0.10, "threshold": 0.70, "size": 4.5},
        "grain": {"power": 0.15},
        "vignette": {},
    },
    "RESOLUTION": {
        "zoom": {"start": 1.03, "mid": 1.0, "end": 1.02, "pan_end": (0.51, 0.5)},
        "grade": {"gain": 1.02, "contrast": 0.03, "saturation": 1.10},
        "glow": {"gain": 0.08},
        "vignette": {},
    },
    "B_ROLL_CINEMATIC": {
        "zoom": {"start": 1.02, "mid": 1.04, "end": 1.02, "pan_end": (0.52, 0.49)},
        "grade": {"gain": 1.08, "contrast": 0.06, "saturation": 1.20},
        "glow": {"gain": 0.10},
        "grain": {"power": 0.20},
        "vignette": {},
    },
    "OUTRO": {
        "grade": {"gain": 1.05, "contrast": 0.04, "saturation": 1.08},
        "glow": {"gain": 0.06, "threshold": 0.78, "size": 3.0},
        "vignette": {},
        "fade": {"fade_out": 15},
    },
}

# ─── Transition Presets ──────────────────────────────────────

TRANSITION_PRESETS = {
    "fade_to_black": {"ttype": "fade_to_black", "dur_frames": 7},
    "zoom_blur": {"ttype": "zoom_blur", "dur_frames": 7},
    "defocus": {"ttype": "defocus", "dur_frames": 7},
    "flash": {"ttype": "flash", "dur_frames": 7},
    "hard_cut": {"ttype": "hard_cut", "dur_frames": 0},
}
