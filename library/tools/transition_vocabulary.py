"""The one list of transition types the pipeline is allowed to plan.

Four vocabularies used to disagree: the handoff toolkit taught nine types,
the brand template allowed three, `transition_selector` honoured two, and
the renderer could draw four - and no two of those sets shared a token, so
nothing the editor asked for ever reached the picture.

This module is the single enumeration all four are now checked against
(`tests/test_transition_vocabulary.py`). A type belongs here only if some
mechanism can actually draw it, and `ROUTES` says WHICH mechanism, per
type. `WITHDRAWN` records the types that were advertised and are not
deliverable, each with the reason - a capability the renderer cannot
honour must be visibly absent, not silently downgraded.

There are two drawing routes and they are not interchangeable:

`fusion_per_clip`
    One comp per clip, built by `library/tools/fusion/effects.py` as a
    tail on the outgoing clip and a head on the incoming one. A comp
    sees only its own clip, which is why nothing on this route can MIX
    two pictures - the reason `cross_dissolve`, `wipe` and `whip_pan`
    are withdrawn below.

`overlay_element`
    A project-declared ALPHA element laid over the cut on its own track,
    placed by `library/tools/transition_overlay.py`. It hides the cut
    rather than dissolving across it, so it needs neither neighbour and
    the per-clip limitation never applies. Added 2026-09-07; the engine
    ships no element and keys nothing (AGENTS.md 14, and the measurement
    in `docs/CHROMA_KEY_TRANSITIONS_MEASURED.md`).


Rules relocated from AGENTS.md 5
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 5
keeps the headline and points here.

**Do not wire FCPXML or DRP project-file surgery back in.**
- Every transition type the pipeline may plan lives in ONE enumeration, `library/tools/transition_vocabulary.py`, with a recorded reason for each withdrawn type.
- **A cut the plan did not decorate is a hard cut.** `transition_selector` never invents a DRAWN transition; `WITHDRAWN_SCENE_CHANGE_DEFAULTS` records the three it used to.  A type advertised anywhere else fails CI.
- Adding a transition means adding a builder to `library/tools/fusion/effects.py` first.
- No per-clip Fusion comp can mix two clips, so there is no cross dissolve or wipe on this route.  A transition carried by an element laid OVER the cut mixes nothing and is a different route - `library/tools/transition_overlay.py`, `OVERLAY_TYPES` here.
"""

# Instantaneous transitions. Nothing is drawn; the label records the
# editorial intent of a cut that has no duration by definition.
CUT_TYPES = ("hard_cut", "jump_cut", "match_cut")

# Drawn by `library/tools/fusion/effects.py` as a tail effect on the
# outgoing clip plus a head effect on the incoming one. The names match
# `fx.transition_tail`/`fx.transition_head`'s dispatch exactly.
FUSION_TYPES = ("fade_to_black", "zoom_blur", "defocus", "flash")

# Drawn by laying a project-declared ALPHA element over the cut, on its
# own track - `library/tools/transition_overlay.py`. It mixes nothing, so
# the per-clip limitation that withdrew `wipe` and `cross_dissolve` below
# does not reach it: an overlay HIDES a cut instead of dissolving across
# one, and reads neither neighbour.
OVERLAY_TYPES = ("element_overlay",)

#: Everything the PER-CLIP FUSION route may plan - what step 4.02's
#: handoff offers, what a brand template's allow-list is checked against,
#: and what `apply_fusion_comps` will try to draw. `element_overlay` is
#: deliberately NOT here: that route builds one comp per clip and cannot
#: place a separate element, so advertising it there would advertise a
#: capability the route cannot deliver - the exact defect this module was
#: written to stop.
PLANNABLE_TYPES = CUT_TYPES + FUSION_TYPES

#: Every transition type this pipeline knows, on any route.
KNOWN_TYPES = PLANNABLE_TYPES + OVERLAY_TYPES

#: Which mechanism draws each type. A type with no route draws nothing,
#: and a type reaching the WRONG route is how `dissolve` once produced an
#: empty comp - so the route is stated per type rather than inferred from
#: whichever module happens to be holding the name.
ROUTE_CUT = "cut"
ROUTE_FUSION_PER_CLIP = "fusion_per_clip"
ROUTE_OVERLAY_ELEMENT = "overlay_element"

ROUTES = {
    **{t: ROUTE_CUT for t in CUT_TYPES},
    **{t: ROUTE_FUSION_PER_CLIP for t in FUSION_TYPES},
    **{t: ROUTE_OVERLAY_ELEMENT for t in OVERLAY_TYPES},
}

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
        "substitute, and neither is `element_overlay`: an element over "
        "the cut hides it, it does not blend one shot into the other."
    ),
    "dissolve": (
        "Same as cross_dissolve: mixing two clips needs both of them at "
        "once, which a per-clip Fusion comp never has."
    ),
    "wipe": (
        "A wipe REVEALS the incoming clip through the outgoing one, which "
        "needs both clips in one comp; see cross_dissolve. A shape or "
        "graphic that COVERS the cut instead of revealing through it is a "
        "different gesture and is deliverable - it is `element_overlay` in "
        "OVERLAY_TYPES, and it mixes nothing."
    ),
    "whip_pan": (
        "Directional blur carried across a cut needs both clips. zoom_blur "
        "is a crash zoom and looks nothing like a whip pan."
    ),
    "j_cut": (
        "An audio-lead edit, not a picture effect. The audio thread was ruled "
        "OUT OF SCOPE on 2026-08-15, and the unreachable J/L cut offset code "
        "was removed under that ruling."
    ),
    "l_cut": (
        "An audio-lag edit, not a picture effect. The audio thread was ruled "
        "OUT OF SCOPE on 2026-08-15, and the unreachable J/L cut offset code "
        "was removed under that ruling."
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
    """Why `raw` is not plannable on the per-clip Fusion route.

    Empty when it is. A type this pipeline really draws but on the OTHER
    route gets its own answer rather than "not a type this pipeline
    knows": the caller is misrouting a real capability, and telling them
    it does not exist sends them to build a second one.
    """
    key = str(raw or "").strip().lower()
    key = ALIASES.get(key, key)
    if key in PLANNABLE_TYPES:
        return ""
    if key in OVERLAY_TYPES:
        return (
            f"{key!r} is a real transition type and it is NOT drawn on the "
            f"per-clip Fusion route - it is an element laid over the cut on "
            f"its own track, placed by library/tools/transition_overlay.py "
            f"(route {ROUTE_OVERLAY_ELEMENT!r}). A per-clip comp cannot "
            f"place a separate element, so this type reaching a Fusion "
            f"builder is a misroute, not a missing capability."
        )
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


def known_type(raw):
    """The canonical name for `raw` on ANY route, or None.

    `canonical_type` deliberately answers only for the per-clip Fusion
    route, because that is what its callers - the handoff, the brand
    allow-list, `apply_fusion_comps`, `compile_manifest` - are asking.
    This is the question "is this a transition type at all".
    """
    key = str(raw or "").strip().lower()
    key = ALIASES.get(key, key)
    return key if key in KNOWN_TYPES else None


def is_overlay(transition_type) -> bool:
    """True when the type is drawn by placing an element over the cut."""
    return known_type(transition_type) in OVERLAY_TYPES


def route_of(transition_type):
    """Which mechanism draws this type, or None if nothing does."""
    return ROUTES.get(known_type(transition_type))


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
