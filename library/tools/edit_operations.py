"""The operation types Ren can translate and the DAG node each owns.

Natural-language words do not select a step. The host emits one of
these operation types; the ledger reader and marker router use this
single enumeration to validate and deliver it.
"""

from __future__ import annotations

DIRECT_OP_OWNERS = {
    "voice_isolation": "render",
    "clip_lut": "render",
    "transform_override": "render",
    "span_retime": "mesh_spine",
    "drop_fragment": "mesh_spine",
    "caption_fix": "plan_subtitles",
    "redraw_closer": "select_reels",
    "retime": "mesh_spine",
    "grade": "render",
    "angle_plan": "select_broll",
}

# These operations are stored as one `plan_change` row and delivered to
# the named planning node through the linked marker note. Their renderer
# remains the owning step's declared plan, so Resolve does not claim to
# replay them as direct timeline writes.
PLAN_OPERATION_OWNERS = {
    "transition": "plan_transitions",
    "shot_selection": "select_broll",
    "speech_selection": "speech_sequence",
    "story_pacing": "mesh_spine",
    "music_selection": "music_selection",
    "audio_mix": "audio_mix",
    "look": "color_grade",
    "visual_effect": "plan_vfx",
    "sound_effect": "plan_sfx",
    "subject_effect": "plan_vfx",
    "motion_graphic": "render_motion_graphics",
    "brand_asset": "render_motion_graphics",
    "end_card": "render_motion_graphics",
}

# Typed values that a planning operation can carry beyond its free-form
# note. `story_pacing` owns the order of the speech spine, so the words
# anchor names the requested passage while this value names the structure.
PLAN_OPERATION_VALUE_CONTRACTS = {
    "story_pacing": {
        "required_anchor": "words",
        "values": {
            "opening_structure": {
                "unit": "spine structure",
                "allowed": {
                    "cold_open_then_intro": (
                        "Open on the anchored spoken passage, return to an "
                        "intro, then continue with the planned story opening."
                    ),
                },
            },
        },
    },
}

OP_OWNERS = {**DIRECT_OP_OWNERS, **PLAN_OPERATION_OWNERS}

# Rung 7 (E3) gave each planner fields that carry a number the requester
# stated, in frames or seconds, beside the feel word it otherwise uses.
# A typed value in one of these units reaches its owner naming the fields
# that can hold it, so the stated number is carried rather than re-read
# as a feel. The field names are the owners' own (their handoff.md).
STATED_NUMBER_FIELDS = {
    "speech_sequence": {
        "frames": ("trim_head_frames", "trim_tail_frames"),
        "seconds": ("trim_head_seconds", "trim_tail_seconds"),
    },
    "mesh_spine": {
        "frames": ("end_offset_frames", "fade_out_frames"),
        "seconds": ("duration_seconds", "minimum_seconds",
                    "maximum_seconds", "target_seconds",
                    "end_offset_seconds", "fade_out_seconds"),
    },
    "select_broll": {
        "frames": ("slip_frames",),
        "seconds": ("slip_seconds",),
    },
    "plan_transitions": {
        "frames": ("duration_frames", "lead_frames", "lag_frames",
                   "offset_frames"),
        "seconds": ("duration_seconds", "lead_seconds", "lag_seconds",
                    "offset_seconds"),
    },
    "plan_vfx": {
        "frames": ("hold_frames", "offset_frames"),
        "seconds": ("hold_seconds", "offset_seconds"),
    },
    "plan_sfx": {
        "frames": ("fade_in_frames", "fade_out_frames", "offset_frames"),
        "seconds": ("fade_in_seconds", "fade_out_seconds", "offset_seconds",
                    "duration_seconds"),
    },
}

_UNIT_ALIASES = {
    "frames": ("frame", "frames", "f", "fr"),
    "seconds": ("second", "seconds", "s", "sec", "secs"),
}


def timing_unit(unit: str) -> str:
    """`frames` or `seconds` for a timing unit, else the empty string."""
    word = (unit or "").strip().lower()
    for canonical, aliases in _UNIT_ALIASES.items():
        if word in aliases:
            return canonical
    return ""


def stated_number_fields(owner: str, unit: str) -> tuple:
    """The owner's rung 7 fields able to carry a value in this unit."""
    return STATED_NUMBER_FIELDS.get(owner, {}).get(timing_unit(unit), ())


def owner_for_op(op: str) -> str:
    """Return the declared owner of one operation type."""
    try:
        return OP_OWNERS[op]
    except KeyError as exc:
        raise ValueError(
            f"unknown edit operation type {op!r}; known types: "
            f"{', '.join(OP_OWNERS)}") from exc


def owner_for_row(row: dict) -> str:
    """Resolve a ledger row's owner through its typed operation."""
    if row.get("op") == "plan_change":
        return owner_for_op((row.get("params") or {}).get("operation_type"))
    return owner_for_op(row.get("op"))
