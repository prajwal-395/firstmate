def compute_ducking_curves(speech_segments: list, music_track_duration: float) -> list:
    """
    Given speech segment timestamps from prosody analysis, compute music volume ducking keyframes:
    - During speech: duck music to -18 LUFS
    - During pauses: bring music back to -12 LUFS
    - Smooth ramp: 200ms attack, 500ms release
    - Returns list of {"time_ms": int, "volume_db": float} keyframes
    """
    keyframes = []
    
    # Start at normal volume (-12)
    keyframes.append({"time_ms": 0, "volume_db": -12.0})
    
    duck_vol = -18.0
    normal_vol = -12.0
    attack_ms = 200
    release_ms = 500
    
    if not speech_segments:
        # No speech, just keep normal volume
        return keyframes

    for segment in speech_segments:
        start_ms = int(segment.get("start_time", 0) * 1000)
        end_ms = int(segment.get("end_time", 0) * 1000)
        
        # Ramp down starts before speech
        ramp_down_start = max(0, start_ms - attack_ms)
        
        # Only add normal volume keyframe if it doesn't conflict with previous release
        if not keyframes or keyframes[-1]["time_ms"] < ramp_down_start:
            keyframes.append({"time_ms": ramp_down_start, "volume_db": normal_vol})
            
        keyframes.append({"time_ms": start_ms, "volume_db": duck_vol})
        
        # Keep ducked during speech
        keyframes.append({"time_ms": end_ms, "volume_db": duck_vol})
        
        # Ramp up after speech
        ramp_up_end = min(int(music_track_duration * 1000), end_ms + release_ms)
        keyframes.append({"time_ms": ramp_up_end, "volume_db": normal_vol})
        
    return keyframes

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
