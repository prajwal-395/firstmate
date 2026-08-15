"""The one list of transition types the pipeline is allowed to plan.

Four vocabularies used to disagree: the handoff toolkit taught nine types,
the brand template allowed three, `transition_selector` honoured two, and
the renderer could draw four - and no two of those sets shared a token, so
nothing the editor asked for ever reached the picture.

This module is the single enumeration all four are now checked against
(`tests/test_transition_vocabulary.py`). A type belongs here only if the
Fusion engine can actually draw it, because Fusion is the route: the
timeline is built through the Python API and every effect on it is a
Fusion comp. `WITHDRAWN` records the types that were advertised and are
not deliverable, each with the reason - a capability the renderer cannot
honour must be visibly absent, not silently downgraded.
"""

# Instantaneous transitions. Nothing is drawn; the label records the
# editorial intent of a cut that has no duration by definition.
CUT_TYPES = ("hard_cut", "jump_cut", "match_cut")

# Drawn by `library/tools/fusion/effects.py` as a tail effect on the
# outgoing clip plus a head effect on the incoming one. The names match
# `fx.transition_tail`/`fx.transition_head`'s dispatch exactly.
FUSION_TYPES = ("fade_to_black", "zoom_blur", "defocus", "flash")

#: Everything a planner, a brand template or a handoff may name.
PLANNABLE_TYPES = CUT_TYPES + FUSION_TYPES

#: Spellings that mean an existing type, not a new one.
ALIASES = {
    "cut": "hard_cut",
    "none": "hard_cut",
    "dip_to_black": "fade_to_black",
    "fade": "fade_to_black",
    "fade_out": "fade_to_black",
    # A "zoom transition" is the crash zoom `_zoom_blur_transition` draws.
    # Two names for one effect is how the vocabularies drifted apart.
    "zoom_transition": "zoom_blur",
    "crash_zoom": "zoom_blur",
    "blur_dissolve": "defocus",
    "brightness_flash": "flash",
}

#: Advertised once, deliverable by nothing. Kept so the withdrawal is
#: visible and a planner that names one gets told why.
WITHDRAWN = {
    "cross_dissolve": (
        "A per-clip Fusion comp sees only its own clip, and V2 is an "
        "additive overlay that cannot read V1 (AGENTS.md section 5), so "
        "nothing on this route can mix the outgoing and incoming clips. "
        "fade_to_black is a dip to black, not a dissolve - it is not a "
        "substitute."
    ),
    "dissolve": (
        "Same as cross_dissolve: mixing two clips needs both of them at "
        "once, which a per-clip Fusion comp never has."
    ),
    "wipe": (
        "A wipe reveals the incoming clip through the outgoing one. Needs "
        "both clips in one comp; see cross_dissolve."
    ),
    "whip_pan": (
        "Directional blur carried across a cut needs both clips. zoom_blur "
        "is a crash zoom and looks nothing like a whip pan."
    ),
    "j_cut": (
        "An audio-lead edit, not a picture effect. The offset code in "
        "resolve_build_timeline is unreachable (the manifest carries no "
        "from_block/to_block) and belongs with the audio work."
    ),
    "l_cut": (
        "An audio-lag edit, not a picture effect. Unreachable for the same "
        "reason as j_cut, and it belongs with the audio work."
    ),
    "light_leak": (
        "Needs a light-leak asset library. library/presets ships none."
    ),
    "macro": (
        "The macro transition path was removed because fusion_macro_loader "
        "previously failed on missing files, falling back to fade_to_black. "
        "While title macros (intro/outro) have been restored with real .setting "
        "files, macro transitions remain unsupported as they are not wired."
    ),
}


def canonical_type(raw):
    """Return the canonical name for `raw`, or None if it is not plannable.

    None covers both withdrawn types and outright unknown ones; use
    `withdrawal_reason` to tell a caller which it was.
    """
    key = str(raw or "").strip().lower()
    key = ALIASES.get(key, key)
    return key if key in PLANNABLE_TYPES else None


def withdrawal_reason(raw) -> str:
    """Why `raw` is not plannable. Empty when it is."""
    key = str(raw or "").strip().lower()
    key = ALIASES.get(key, key)
    if key in PLANNABLE_TYPES:
        return ""
    if key in WITHDRAWN:
        return WITHDRAWN[key]
    return (
        f"{raw!r} is not a transition type this pipeline knows. "
        f"Plannable types: {', '.join(PLANNABLE_TYPES)}."
    )


def is_cut(transition_type) -> bool:
    """True when the type is instantaneous and draws nothing."""
    return canonical_type(transition_type) in CUT_TYPES


def is_drawn(transition_type) -> bool:
    """True when the type becomes a Fusion head/tail effect."""
    return canonical_type(transition_type) in FUSION_TYPES


def filter_allowed(types, source: str = "brand template"):
    """Narrow a caller-supplied allow-list to the plannable types.

    Returns (allowed, rejected). An allow-list that names something the
    renderer cannot draw is a configuration error, not a silent no-op, so
    the rejected entries come back for the caller to report.
    """
    allowed, rejected = [], []
    for raw in types or ():
        canonical = canonical_type(raw)
        if canonical and canonical not in allowed:
            allowed.append(canonical)
        elif not canonical:
            rejected.append((str(raw), withdrawal_reason(raw)))
    return allowed, rejected
