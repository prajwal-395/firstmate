from library.tools.preset_indexer import find_preset_for_mood
from library.schemas.preset_metadata import PresetEntry

def select_transition(from_clip: dict, to_clip: dict, brand_effect: dict, preset_index, creative_direction: dict) -> dict:
    """
    Choose the best transition based on:
    - Brand template's transition_types preference list
    - Creative direction energy/mood
    - Clip content similarity (same speaker = cut, scene change = dissolve/wipe, high energy = dynamic macro)
    - Available presets in the library
    """
    # default fallback
    result = {"type": "cut", "macro_preset": None, "duration_ms": 0}
    
    # Analyze clips
    same_speaker = (from_clip.get("speaker") == to_clip.get("speaker")) and from_clip.get("speaker") is not None
    scene_change = (from_clip.get("scene_id") != to_clip.get("scene_id"))
    
    # Brand preferences
    preferred_types = brand_effect.get("transition_types", ["cut", "dissolve"])
    duration_ms = brand_effect.get("transition_duration_ms", 500)
    
    energy = creative_direction.get("energy", "medium").lower()
    mood = creative_direction.get("mood", "neutral").lower()
    
    # 1. Same speaker = cut (unless brand says otherwise, but usually cut)
    if same_speaker and "cut" in preferred_types:
        result["type"] = "cut"
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
            result["duration_ms"] = duration_ms
        else:
            result["type"] = "dissolve"
            result["duration_ms"] = duration_ms
        return result
        
    # Fallback to first preferred type or cut
    result["type"] = preferred_types[0] if preferred_types else "cut"
    result["duration_ms"] = duration_ms if result["type"] != "cut" else 0
    return result
