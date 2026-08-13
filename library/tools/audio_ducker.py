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

    # Merge segments that touch or overlap once the ramps are accounted
    # for. Back-to-back speech blocks used to each emit their own
    # release-then-attack pair, and because a release lands 500ms after a
    # block ends while the next attack starts 200ms before the next block
    # begins, the keyframe times ran BACKWARDS at every block boundary.
    # Resolve reads a non-monotonic automation curve as garbage.
    ordered = sorted(
        (
            (
                int(s.get("start_time", 0) * 1000),
                int(s.get("end_time", 0) * 1000),
            )
            for s in speech_segments
        ),
    )
    merged = []
    for start_ms, end_ms in ordered:
        if end_ms <= start_ms:
            continue
        if merged and start_ms - attack_ms <= merged[-1][1] + release_ms:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end_ms))
        else:
            merged.append((start_ms, end_ms))

    track_end_ms = int(music_track_duration * 1000)

    def push(time_ms: int, volume_db: float) -> None:
        """Append a keyframe, keeping the curve monotonic in time."""
        time_ms = max(0, min(time_ms, track_end_ms) if track_end_ms else max(0, time_ms))
        if keyframes and time_ms < keyframes[-1]["time_ms"]:
            return
        if keyframes and time_ms == keyframes[-1]["time_ms"]:
            # Same instant: the later value wins rather than stacking two
            # keyframes on one frame.
            keyframes[-1]["volume_db"] = volume_db
            return
        keyframes.append({"time_ms": time_ms, "volume_db": volume_db})

    for start_ms, end_ms in merged:
        push(max(0, start_ms - attack_ms), normal_vol)
        push(start_ms, duck_vol)
        push(end_ms, duck_vol)
        push(end_ms + release_ms, normal_vol)

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
