"""
Presets for the Fusion Composition Engine.

SEGMENT_PRESETS / SEGMENT_PRESETS_FLAT used to live here: seven curated
per-block-type looks (HOOK punch and glow, EMOTIONAL_PEAK with grain,
OUTRO with a 15-frame fade). They were DELETED under P3.4, by the
captain's ruling of 2026-08-16, because they could not be selected -
twice over, not once:

  1. The reader was `if not effects and label in SEGMENT_PRESETS` in
     apply_fusion_comps. It only fired for a clip carrying NO other
     effects, and since the house look landed, compile_manifest merges
     the Fusion half of the grade into per_clip for EVERY V1 and V2 clip.
     `effects` is therefore non-empty on every clip and the branch was
     unreachable regardless of the label casing.
  2. Five of the seven were keyed to block types the spine cannot emit.
     spine_contract documents block_type as "hook" | "speech" | anything
     else, so CORE_INSIGHT, TURNING_POINT, EMOTIONAL_PEAK, RESOLUTION and
     B_ROLL_CINEMATIC had no block that could ever select them.

The standing plan text said the fix was to correct the label casing. It
was not, and that theory is recorded as wrong in docs/PIPELINE_PLAN.md so
nobody re-derives it. Per-block-type looks are not off the table, but they
are a design job - new spine block types, plus a decision about how a
block look composes with a house look that every clip already carries -
and not a cleanup item.
"""

# ─── Transition Presets ──────────────────────────────────────

TRANSITION_PRESETS = {
    "fade_to_black": {"ttype": "fade_to_black", "dur_frames": 7},
    "zoom_blur": {"ttype": "zoom_blur", "dur_frames": 7},
    "defocus": {"ttype": "defocus", "dur_frames": 7},
    "flash": {"ttype": "flash", "dur_frames": 7},
    "hard_cut": {"ttype": "hard_cut", "dur_frames": 0},
}
