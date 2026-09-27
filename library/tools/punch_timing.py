"""Measured timing contract for independently timed zoom emphasis ramps.

The bounds come from the captain's rendered punch comparison recorded in
``data/vep-ken-burns-dynamic-punches/report.md``. They apply independently
to the move into the peak and the move out from the release anchor; the hold
between those anchors is derived by the planner and never declared.
"""

from __future__ import annotations

import math

from library.tools.frame_utils import duration_to_frames
from library.tools.ren_refusal import RenRefusal

MIN_PUNCH_RAMP_SECONDS = 0.67
MAX_PUNCH_RAMP_SECONDS = 1.8
NEUTRAL_PUNCH_RAMP_SECONDS = 1.1
PUNCH_TIMING_MARKER = "<!-- PUNCH_TIMING_BOUNDS -->"


class PunchTimingRefused(RenRefusal):
    """A zoom emphasis timing request contradicts the measured contract."""


def timing_prompt_addition() -> str:
    """Render the measured band into the planner prompt from one source."""
    return (
        f"Each zoom_emphasis ramp must take {MIN_PUNCH_RAMP_SECONDS:.2f} to "
        f"{MAX_PUNCH_RAMP_SECONDS:.2f} seconds. This is a measured range, "
        f"not a default: below {MIN_PUNCH_RAMP_SECONDS:.2f}s reads as "
        f"whiplash; above {MAX_PUNCH_RAMP_SECONDS:.1f}s the move stops "
        "paying for itself, and at "
        "3.0s it becomes indistinguishable from the speaker's own movement. "
        f"{NEUTRAL_PUNCH_RAMP_SECONDS:.1f}s is a confirmed neutral reference, "
        "not a value to copy automatically. Choose each ramp from the "
        "emotional intent."
    )


def ramp_duration_frames(
    value, *, movement: str, frame_rate: float, position=None
) -> int:
    """Validate one declared ramp duration and convert it to timeline frames.

    Values are refused outside the measured band; neither edge is silently
    clamped. A caller must name the direction so the refusal points to the
    decision that needs replanning.
    """
    where = f" on block {position!r}" if position is not None else ""
    try:
        seconds = float(value)
    except (OverflowError, TypeError, ValueError):
        seconds = math.nan
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(seconds)
    ):
        raise PunchTimingRefused(
            what=(
                f"zoom_emphasis {movement} duration{where} is not a "
                f"finite number of seconds: {value!r}"
            ),
            why=(
                "the zoom build and release are independent creative "
                "timing decisions, and neither has a default"
            ),
            fix=(
                f"re-plan zoom_emphasis with {movement} as a number from "
                f"{MIN_PUNCH_RAMP_SECONDS:.2f} to "
                f"{MAX_PUNCH_RAMP_SECONDS:.2f} seconds"
            ),
        )

    if seconds < MIN_PUNCH_RAMP_SECONDS:
        raise PunchTimingRefused(
            what=(
                f"zoom_emphasis {movement} duration {seconds:g}s{where} "
                f"is below the measured {MIN_PUNCH_RAMP_SECONDS:.2f}s "
                "floor"
            ),
            why=("the captain's render review found shorter gathers read as whiplash"),
            fix=(
                f"re-plan this {movement} between "
                f"{MIN_PUNCH_RAMP_SECONDS:.2f} and "
                f"{MAX_PUNCH_RAMP_SECONDS:.2f} seconds"
            ),
        )
    if seconds > MAX_PUNCH_RAMP_SECONDS:
        raise PunchTimingRefused(
            what=(
                f"zoom_emphasis {movement} duration {seconds:g}s{where} "
                f"is above the measured {MAX_PUNCH_RAMP_SECONDS:.1f}s "
                "ceiling"
            ),
            why=(
                "longer ramps stop paying for themselves; the rendered "
                "3.0s move was indistinguishable from the speaker's own "
                "movement, like the rejected 8.13s move"
            ),
            fix=(
                f"re-plan this {movement} between "
                f"{MIN_PUNCH_RAMP_SECONDS:.2f} and "
                f"{MAX_PUNCH_RAMP_SECONDS:.2f} seconds"
            ),
        )
    if (
        isinstance(frame_rate, bool)
        or not isinstance(frame_rate, (int, float))
        or not math.isfinite(float(frame_rate))
        or frame_rate <= 0
    ):
        raise ValueError(
            f"zoom_emphasis needs a positive finite frame rate, got {frame_rate!r}"
        )

    frames = duration_to_frames(seconds, float(frame_rate))
    if frames < 1:
        raise PunchTimingRefused(
            what=(
                f"zoom_emphasis {movement} duration {seconds:g}s{where} "
                "contains no timeline frame"
            ),
            why="a ramp with no frames cannot move the picture",
            fix=(
                f"re-plan this {movement} between "
                f"{MIN_PUNCH_RAMP_SECONDS:.2f} and "
                f"{MAX_PUNCH_RAMP_SECONDS:.2f} seconds"
            ),
        )
    return frames
