def compute_sfx_ducking(sfx_events: list, speech_segments: list) -> list:
    """
    Ensure SFX don't overlap with speech by adjusting timing and volume.
    Returns adjusted sfx_events.
    """
    adjusted_sfx = []
    
    for sfx in sfx_events:
        sfx_start = sfx.get("start_time", 0)
        sfx_end = sfx.get("end_time", sfx_start + 1.0)
        
        overlap = False
        for segment in speech_segments:
            speech_start = segment.get("start_time", 0)
            speech_end = segment.get("end_time", 0)
            
            # Check overlap
            if sfx_start < speech_end and sfx_end > speech_start:
                overlap = True
                break
                
        new_sfx = dict(sfx)
        if overlap:
            # If overlap, duck the SFX volume so it doesn't clash with speech
            new_sfx["volume_db"] = new_sfx.get("volume_db", 0) - 6.0
            
        adjusted_sfx.append(new_sfx)
        
    return adjusted_sfx
