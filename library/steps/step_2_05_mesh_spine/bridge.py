#!/usr/bin/env python3
"""Step 2.05 pre-bridge: resolve the duration zone into the LLM context.

The handoff tells the model its spine "must fall within the project's
target duration zone", and the post-bridge validates against
``(min, target, max)`` computed from ``PROJECT_TARGET_TOLERANCE``.  Until
now the model saw only the centre - ``target_duration_seconds: 60.0`` -
and was rejected for landing outside a band it was never shown.

This bridge resolves the zone to real numbers and ships them with a
legend, the same way ``music_measurement.MEASUREMENT_LEGEND`` ships the
column definitions for step 2.04.  A definition, never a conclusion.

Input:  (whatever gather_step_inputs hands)
Output: { "duration_zone": { "minimum_seconds", "target_seconds",
                              "maximum_seconds", "zone_legend" } }
        or {} when nothing declared a target.
"""
import json
import sys

from library.tools.duration_targets import (
    DURATION_ZONE_LEGEND,
    NO_TARGET_DECLARED,
    get_target_duration_zone,
)


def main():
    data = json.loads(sys.stdin.read())

    zone = get_target_duration_zone(data)
    if zone is None:
        print(
            f"  duration zone: {NO_TARGET_DECLARED}",
            file=sys.stderr,
        )
        json.dump({}, sys.stdout)
        return

    min_dur, target_dur, max_dur = zone
    result = {
        "duration_zone": {
            "minimum_seconds": round(min_dur, 1),
            "target_seconds": round(target_dur, 1),
            "maximum_seconds": round(max_dur, 1),
            "zone_legend": dict(DURATION_ZONE_LEGEND),
        },
    }

    print(
        f"  duration zone: [{min_dur:.1f}s, {target_dur:.1f}s, {max_dur:.1f}s]",
        file=sys.stderr,
    )
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
