"""Rendered-output checks and story evaluation (EDIT-022/023/024).

EDIT-022 renders candidate edits and verifies the actual output; EDIT-023
evaluates audio loudness, continuity, and other technical output
properties; EDIT-024 evaluates coherent storytelling separately from
mechanical edit correctness. The separation is the point: a render can
be mechanically correct (right levels, no black, audio present) and
still miss the story, or tell the story with a technical fault. One
report never stands in for the other, and neither is ever called
editorial quality - the first is mechanical correctness, the second is
intent satisfaction.

This module evaluates MEASUREMENT dicts (produced by render_qa over a
rendered file, or by the demo harness over a plan). It runs no ffmpeg
itself; the benchmark script measures, this module judges.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence

# Broadcast-adjacent mechanical bounds, stated and overridable. These
# are technical delivery bounds, not taste: loudness in LUFS, true peak
# in dBTP, one-frame sync at common delivery rates.
DEFAULT_LOUDNESS_TARGET_LUFS = -14.0
DEFAULT_LOUDNESS_TOLERANCE_LU = 1.0
DEFAULT_TRUE_PEAK_CEILING_DBTP = -1.0
DEFAULT_DURATION_TOLERANCE_SECONDS = 0.12


def mechanical_report(measurements: Mapping[str, Any],
                      expected: Mapping[str, Any]) -> dict[str, Any]:
    """Mechanical correctness of one rendered output (EDIT-022/023).

    `measurements` carries what was measured off the render
    (`lufs`, `true_peak_dbtp`, `width`, `height`, `fps`,
    `duration_seconds`, `audio_streams`, `black_ratio`,
    `frozen_ratio` - absent keys are reported unmeasured, never
    assumed). `expected` carries the delivery contract (`width`,
    `height`, `fps`, `duration_seconds`, plus optional loudness
    bounds). Returns `{mechanically_correct, checks[]}` where each
    check is `{property, status: pass|fail|unmeasured, detail}`.
    """
    loudness_target = float(expected.get(
        "loudness_target_lufs", DEFAULT_LOUDNESS_TARGET_LUFS))
    loudness_tolerance = float(expected.get(
        "loudness_tolerance_lu", DEFAULT_LOUDNESS_TOLERANCE_LU))
    peak_ceiling = float(expected.get(
        "true_peak_ceiling_dbtp", DEFAULT_TRUE_PEAK_CEILING_DBTP))
    duration_tolerance = float(expected.get(
        "duration_tolerance_seconds",
        DEFAULT_DURATION_TOLERANCE_SECONDS))
    checks: list[dict[str, Any]] = []

    def check(property_name: str, status: str, detail: str) -> None:
        checks.append({"property": property_name, "status": status,
                       "detail": detail})

    lufs = measurements.get("lufs")
    if lufs is None:
        check("loudness", "unmeasured", "no LUFS measurement")
    elif abs(float(lufs) - loudness_target) <= loudness_tolerance:
        check("loudness", "pass", f"{lufs} LUFS within "
                                 f"{loudness_tolerance} LU of target")
    else:
        check("loudness", "fail", f"{lufs} LUFS against target "
                                  f"{loudness_target} LUFS")
    peak = measurements.get("true_peak_dbtp")
    if peak is None:
        check("true_peak", "unmeasured", "no true-peak measurement")
    elif float(peak) <= peak_ceiling:
        check("true_peak", "pass", f"{peak} dBTP at or under ceiling")
    else:
        check("true_peak", "fail", f"{peak} dBTP over ceiling "
                                   f"{peak_ceiling} dBTP")
    for dimension in ("width", "height"):
        measured = measurements.get(dimension)
        wanted = expected.get(dimension)
        if measured is None or wanted is None:
            check(f"frame_{dimension}", "unmeasured",
                  "no measured or expected value")
        elif int(measured) == int(wanted):
            check(f"frame_{dimension}", "pass", f"{measured}")
        else:
            check(f"frame_{dimension}", "fail",
                  f"measured {measured}, expected {wanted}")
    for key, label in (("fps", "frame_rate"),
                       ("duration_seconds", "duration")):
        measured = measurements.get(key)
        wanted = expected.get(key)
        if measured is None or wanted is None:
            check(label, "unmeasured", "no measured or expected value")
        else:
            tolerance = (duration_tolerance if key == "duration_seconds"
                         else 0.01)
            if abs(float(measured) - float(wanted)) <= tolerance:
                check(label, "pass", f"{measured}")
            else:
                check(label, "fail",
                      f"measured {measured}, expected {wanted}")
    streams = measurements.get("audio_streams")
    if streams is None:
        check("audio_stream", "unmeasured", "no stream count measured")
    elif int(streams) >= 1:
        check("audio_stream", "pass", f"{streams} audio stream(s)")
    else:
        check("audio_stream", "fail", "render carries no audio stream")
    for key, label in (("black_ratio", "undeclared_black"),
                       ("frozen_ratio", "freeze_frames")):
        ratio = measurements.get(key)
        if ratio is None:
            check(label, "unmeasured", "not measured")
        elif float(ratio) <= 0.0:
            check(label, "pass", "none detected")
        else:
            check(label, "fail", f"ratio {ratio}")
    failures = [check_ for check_ in checks
                if check_["status"] == "fail"]
    return {"mechanically_correct": not failures, "checks": checks}


def storytelling_report(
        candidate: Mapping[str, Any],
        required_moments: Sequence[Mapping[str, Any]],
        require_complete_exchange: bool = False) -> dict[str, Any]:
    """Intent satisfaction of one candidate (EDIT-024).

    `required_moments` are the brief's must-cover spans
    (`moment_id`, `start_seconds`, `end_seconds`); the candidate
    satisfies one when their overlap covers at least half the moment.
    `require_complete_exchange` additionally requires the candidate to
    carry a complete question/answer unit (`candidate["exchange"]`
    truthy, as `question_answer_units` or PR 2603 exchanges produce).
    Returns `{story_satisfied, gaps[]}`. A story gap is a missing
    moment or a missing exchange - never a loudness number.
    """
    gaps: list[str] = []
    start = float(candidate.get("start_seconds", 0.0))
    end = float(candidate.get("end_seconds", 0.0))
    for moment in required_moments:
        moment_start = float(moment["start_seconds"])
        moment_end = float(moment["end_seconds"])
        overlap = min(end, moment_end) - max(start, moment_start)
        needed = 0.5 * (moment_end - moment_start)
        if overlap < needed:
            gaps.append(f"required moment {moment.get('moment_id')} "
                        f"uncovered ({max(overlap, 0.0):.2f}s of "
                        f"{needed:.2f}s needed)")
    if require_complete_exchange and not candidate.get("exchange"):
        gaps.append("no complete question/answer unit in candidate")
    return {"story_satisfied": not gaps, "gaps": gaps}
