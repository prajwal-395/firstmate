"""How long the timeline is. One measure, so two gates cannot disagree.

The spine is the timeline: `mesh_spine` gives every block a
`timeline_start`/`timeline_end`, and the last `timeline_end` is where the
video stops. `a_roll_assignments` carries the same clock and is the
fallback for a caller that has the cut but not the spine.

What is NOT the timeline duration is `speech_sequence.body_sequence[-1]
["end_time"]`: that is a SOURCE timestamp - where the last passage ends
inside its own clip - and it has no relation to timeline position at all.
`step_5_03_creative_cohesion` used it as the duration and reported that
project 001's "actual duration (40.1s) is below the minimum target zone
(54.0s)" about a 54.77 s timeline that `review_rough_cut` had already
measured as inside the zone, one step earlier, with this measure.
"""


def measure_timeline_duration(data: dict) -> float:
    """Timeline length in seconds, or 0.0 when nothing says.

    0.0 means "no evidence", not "empty video" - a caller that would
    warn about a duration must check for it, or it reports every project
    with the spine unrouted as far too short.
    """
    spine = data.get("audio_spine") or data.get("timed_spine") or {}
    structure = spine.get("structure", []) if isinstance(spine, dict) else []
    duration = max(
        (block.get("timeline_end", 0) or 0 for block in structure),
        default=0.0,
    )
    if duration > 0:
        return float(duration)

    a_roll = data.get("a_roll_assignments") or []
    if isinstance(a_roll, dict):
        a_roll = a_roll.get("a_roll_assignments", []) or []
    duration = max(
        (ar.get("timeline_end", 0) or 0 for ar in a_roll
         if isinstance(ar, dict)),
        default=0.0,
    )
    return float(duration) if duration > 0 else 0.0
