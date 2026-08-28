def align_sfx_to_prosody(sfx_spec: list, prosody_data: dict) -> list:
    """
    Adjust SFX event timing based on prosody analysis:
    - Whoosh/transition SFX on detected pause boundaries
    - Impact SFX on prosody emphasis peaks
    - Returns adjusted sfx_spec with refined timestamps

    There was a third branch here that pulled "rise"/"build" SFX to land
    1.5 s before an engagement peak, off an `engagement_scores` mapping.
    It is removed rather than left unwired: no step has ever emitted an
    `engagement_scores` key, no DAG edge carried one into plan_sfx, and
    the threshold it compared against was `> 0.8` while the only
    engagement number the pipeline ever produced was a 0-100 composite.
    So it could not fire on any project, by three independent routes at
    once. The composite itself is withdrawn - see
    library/tools/passage_engagement.py - so there is nothing left for it
    to read.

    An SFX that names no time of its own keeps the time the plan gave it.
    Nothing here invents one.
    """
    adjusted_sfx = []

    pauses = prosody_data.get("pauses", []) if prosody_data else []
    peaks = prosody_data.get("emphasis_peaks", []) if prosody_data else []

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
                
        adjusted_sfx.append(new_sfx)
        
    return adjusted_sfx


# `scale_sfx_density` was here and is DELETED, not disabled.
#
# It took the plan and an energy word and returned a SHORTER plan: on
# "calm" it kept only transition, whoosh and ambient entries; on
# "moderate" it dropped every second impact. The energy word it judged by
# was read from a key `creative_direction` does not have, so in practice
# the constant "moderate" decided it on every run.
#
# How many sound effects a piece gets is the creative direction's call
# (captain's ruling 2026-08-20). A function that deletes half of them
# because a constant says the piece is "moderate" is the same floor in
# the other direction, and leaving it here uncalled would state that
# taste as fact for the next reader. Guarded by
# tests/test_no_creative_floors.py.
