"""Action preparation, execution, and completion phases (EDIT-007/008).

EDIT-007 distinguishes what an action interval is doing at each moment:
preparation (moving toward the act), execution (the act itself), and
completion (settling after it). EDIT-008 cuts on precise action
boundaries: at phase edges, never mid-execution.

Phases derive from MEASURED motion-energy samples inside a verified
action event span, never from thirds or any fixed split: the onset is
the first sustained sample at or above the activation level, the
execution runs to the last such sample, and what remains on either side
is preparation and completion. The activation level defaults to half the
in-span maximum (scale-free and stated), and callers may pass their own.
With no motion samples, or no sample reaching the level, the interval
is UNPHASEABLE and says so: an action with no measured dynamics gets no
phase boundaries rather than invented ones.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence

# Scale-free mechanical default: half the in-span motion maximum. Stated
# here, overridable per call, never tuned per clip in silence.
DEFAULT_ACTIVATION_FRACTION = 0.5


def phase_action(event_start: float, event_end: float,
                 motion_samples: Sequence[Mapping[str, Any]],
                 activation_fraction: float = DEFAULT_ACTIVATION_FRACTION,
                 evidence_id: str = "") -> dict[str, Any]:
    """Split a verified action span into preparation/execution/completion.

    `motion_samples` carry `tick_seconds` (or `seconds`) and `value`.
    Returns `{status, phases[] | [], reasons[]}`. `status` is `phased`
    with three half-open `phases` (`preparation`, `execution`,
    `completion`, each with `start_seconds`/`end_seconds` plus the
    activation evidence), or `unphaseable` with the reason. An empty
    preparation or completion (onset at the span edge) is recorded with
    zero length rather than dropped, so a reader can tell "measured
    none" from "never asked".
    """
    if not event_end > event_start:
        return {"status": "unphaseable",
                "reasons": ["empty action span"], "phases": []}
    inside = sorted(
        (float(sample.get("tick_seconds", sample.get("seconds", 0.0))),
         float(sample.get("value", 0.0)))
        for sample in motion_samples
        if isinstance(sample, Mapping)
        and event_start <= float(sample.get(
            "tick_seconds", sample.get("seconds", 0.0))) < event_end)
    if not inside:
        return {"status": "unphaseable",
                "reasons": ["no motion samples inside the action span"],
                "phases": []}
    peak = max(value for _, value in inside)
    level = activation_fraction * peak
    active = [tick for tick, value in inside if value >= level]
    if not active:
        return {"status": "unphaseable",
                "reasons": ["no motion sample reaches the activation "
                            f"level {level}"],
                "phases": []}
    onset, offset = active[0], active[-1]
    phases = [
        {"phase": "preparation", "start_seconds": event_start,
         "end_seconds": onset},
        {"phase": "execution", "start_seconds": onset,
         "end_seconds": offset},
        {"phase": "completion", "start_seconds": offset,
         "end_seconds": event_end},
    ]
    return {"status": "phased", "reasons": [],
            "activation_level": level,
            "activation_fraction": activation_fraction,
            "evidence_id": evidence_id, "phases": phases}


def action_boundary_cuts(phased: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Cut positions at measured phase boundaries (EDIT-008).

    One cut at the preparation/execution edge and one at the
    execution/completion edge. Zero-length edge phases yield no cut
    there: a boundary with no extent on one side is not a place to cut.
    Each cut names its phase edge and the activation evidence.
    """
    if phased.get("status") != "phased":
        return []
    by_name = {phase["phase"]: phase for phase in phased.get("phases", [])}
    cuts: list[dict[str, Any]] = []
    for edge, before, after in (("preparation_execution", "preparation",
                                 "execution"),
                                ("execution_completion", "execution",
                                 "completion")):
        first = by_name.get(before, {})
        second = by_name.get(after, {})
        if not first or not second:
            continue
        if first["end_seconds"] <= first["start_seconds"]:
            continue
        if second["end_seconds"] <= second["start_seconds"]:
            continue
        cuts.append({"position_seconds": float(first["end_seconds"]),
                     "edge": edge,
                     "evidence_id": phased.get("evidence_id", "")})
    return cuts


def crosses_execution(position_seconds: float,
                      phased: Mapping[str, Any]) -> bool:
    """Whether a cut position lands strictly inside measured execution."""
    if phased.get("status") != "phased":
        return False
    for phase in phased.get("phases", []):
        if phase.get("phase") == "execution":
            return bool(float(phase["start_seconds"])
                        < position_seconds
                        < float(phase["end_seconds"]))
    return False
