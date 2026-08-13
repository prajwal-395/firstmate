from library.tools.preset_indexer import find_preset_for_mood
from library.schemas.preset_metadata import PresetEntry

# Transition types that are instantaneous - no overlap, zero duration.
CUT_TYPES = ("cut", "hard_cut", "jump_cut")


def select_transition(
    from_clip: dict,
    to_clip: dict,
    brand_effect: dict,
    preset_index,
    creative_direction: dict,
    requested_type: str = "",
) -> dict:
    """
    Choose the best transition based on:
    - The type the creative plan explicitly requested (honoured when the
      brand template allows it) - the editor's call outranks the heuristic
    - Brand template's transition_types preference list
    - Creative direction energy/mood
    - Whether the two blocks come from the SAME source clip: a cut inside
      one take is a jump cut, a cut across takes is a scene change
    - Available presets in the library
    """
    # default fallback
    result = {"type": "cut", "macro_preset": None, "duration_ms": 0}

    # Brand preferences
    preferred_types = brand_effect.get("transition_types", ["cut", "dissolve"])
    duration_ms = brand_effect.get("transition_duration_ms", 500)

    energy = creative_direction.get("energy", "medium").lower()
    mood = creative_direction.get("mood", "neutral").lower()

    # 0. Honour an explicit creative choice when the brand permits it.
    #    Previously the plan's `type` was computed and then discarded here,
    #    which is why every transition in a run came out identical.
    requested = (requested_type or "").strip().lower()
    if requested and requested in [t.lower() for t in preferred_types]:
        result["type"] = requested
        result["duration_ms"] = 0 if requested in CUT_TYPES else duration_ms
        return result

    # Analyze clips. Spine blocks expose clip_id (see spine_contract), so
    # "did we change source clip?" is a real signal, not a None==None guess.
    from_clip_id = from_clip.get("clip_id")
    to_clip_id = to_clip.get("clip_id")
    same_source = (
        from_clip_id is not None
        and to_clip_id is not None
        and from_clip_id == to_clip_id
    )
    scene_change = (
        from_clip_id is not None
        and to_clip_id is not None
        and from_clip_id != to_clip_id
    )

    # 1. Same source clip = jump cut (nothing to dissolve between)
    if same_source:
        result["type"] = "jump_cut" if "jump_cut" in preferred_types else "cut"
        result["duration_ms"] = 0
        return result

    # 2. High energy or dynamic brand = try macro
    if (energy == "high" or "macro" in preferred_types) and preset_index:
        from library.tools.fusion_macro_loader import list_available_transitions
        available = list_available_transitions(preset_index)
        
        # Try to find one matching mood/energy
        best_macro = None
        best_score = -1
        for macro in available:
            score = 0
            tags = [t.lower() for t in macro.tags]
            if mood in tags: score += 2
            if energy in tags: score += 1
            if score > best_score:
                best_score = score
                best_macro = macro
                
        if best_macro is None and available:
            best_macro = available[0]
            
        if best_macro:
            result["type"] = "macro"
            result["macro_preset"] = best_macro
            result["duration_ms"] = duration_ms
            return result
            
    # 3. Scene change = dissolve/wipe
    if scene_change:
        if "wipe" in preferred_types:
            result["type"] = "wipe"
        elif "cross_dissolve" in preferred_types:
            result["type"] = "cross_dissolve"
        else:
            result["type"] = "dissolve"
        result["duration_ms"] = duration_ms
        return result

    # Fallback to first preferred type or cut
    result["type"] = preferred_types[0] if preferred_types else "cut"
    result["duration_ms"] = 0 if result["type"] in CUT_TYPES else duration_ms
    return result
