def align_sfx_to_prosody(sfx_spec: list, prosody_data: dict, engagement_scores: dict) -> list:
    """
    Adjust SFX event timing based on prosody analysis:
    - Whoosh/transition SFX on detected pause boundaries
    - Impact SFX on prosody emphasis peaks
    - Rise SFX before engagement score peaks (hook moments)
    - Returns adjusted sfx_spec with refined timestamps
    """
    adjusted_sfx = []
    
    pauses = prosody_data.get("pauses", []) if prosody_data else []
    peaks = prosody_data.get("emphasis_peaks", []) if prosody_data else []
    high_engagement = []
    
    if engagement_scores:
        for t, score in engagement_scores.items():
            if score > 0.8:  # threshold for peak
                try:
                    high_engagement.append(float(t))
                except ValueError:
                    pass
    
    for sfx in sfx_spec:
        new_sfx = dict(sfx)
        sfx_type = new_sfx.get("type", "").lower()
        original_time = new_sfx.get("start_time", 0.0)
        
        if "whoosh" in sfx_type or "transition" in sfx_type:
            # Align to nearest pause boundary
            nearest = None
            min_diff = float("inf")
            for pause in pauses:
                pause_time = pause.get("start_time", 0)
                diff = abs(original_time - pause_time)
                if diff < min_diff and diff < 2.0:  # within 2 seconds
                    min_diff = diff
                    nearest = pause_time
            if nearest is not None:
                new_sfx["start_time"] = nearest
                
        elif "impact" in sfx_type:
            # Align to prosody emphasis peak
            nearest = None
            min_diff = float("inf")
            for peak in peaks:
                diff = abs(original_time - peak)
                if diff < min_diff and diff < 2.0:
                    min_diff = diff
                    nearest = peak
            if nearest is not None:
                new_sfx["start_time"] = nearest
                
        elif "rise" in sfx_type or "build" in sfx_type:
            # Align to end before an engagement peak
            nearest = None
            min_diff = float("inf")
            for hook in high_engagement:
                # We want the rise to end at the hook, so it starts maybe 1.5s before
                target_start = hook - 1.5
                diff = abs(original_time - target_start)
                if diff < min_diff and diff < 3.0:
                    min_diff = diff
                    nearest = target_start
            if nearest is not None:
                new_sfx["start_time"] = max(0.0, nearest)
                
        adjusted_sfx.append(new_sfx)
        
    return adjusted_sfx

def scale_sfx_density(sfx_spec: list, energy_level: str) -> list:
    """
    Adjust SFX count based on energy:
    - "high": keep all SFX, add additional micro-impacts (or at least keep all)
    - "moderate": keep transition SFX, reduce impacts
    - "calm": minimal SFX, only scene transitions
    """
    energy = energy_level.lower() if energy_level else "moderate"
    
    if energy == "high":
        # Keep all, in a full implementation we might duplicate/add some
        return list(sfx_spec)
        
    filtered_sfx = []
    
    if energy == "calm":
        for sfx in sfx_spec:
            sfx_type = sfx.get("type", "").lower()
            if "transition" in sfx_type or "whoosh" in sfx_type or "ambient" in sfx_type:
                filtered_sfx.append(sfx)
    else:
        # moderate
        impact_count = 0
        for sfx in sfx_spec:
            sfx_type = sfx.get("type", "").lower()
            if "impact" in sfx_type:
                impact_count += 1
                if impact_count % 2 == 1:  # keep half
                    filtered_sfx.append(sfx)
            else:
                filtered_sfx.append(sfx)
                
    return filtered_sfx
