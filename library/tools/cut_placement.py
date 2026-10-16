"""Pause-centered cut placement over measured signals (EDIT-003 glue).

EDIT-003 allows finer analysis around candidate cut points. Cut-point
SELECTION belongs to vep-pl-f04-edit-points: this module never selects.
It REFINES a proposed cut position against the measurements F04's own
inputs provide (word timings with pause gaps, prosody peaks, action
phases, crosstalk spans): snap to the nearest pause center, veto
mid-word and mid-execution placements, keep clear of crosstalk and
prosody peaks. Every adjustment names the measurement that caused it,
and a cut that cannot be placed well is vetoed with the reason rather
than nudged silently.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence

from library.tools.action_phases import crosses_execution
from library.tools.phrase_coherence import pause_center_cuts


def refine_cut(candidate_seconds: float,
              words: Sequence[Mapping[str, Any]],
              phased_actions: Sequence[Mapping[str, Any]] = (),
              crosstalk_spans: Sequence[Mapping[str, Any]] = (),
              prosody_peaks: Sequence[float] = (),
              min_pause_seconds: float = 0.15,
              peak_keepout_seconds: float = 0.25,
              search_seconds: float = 2.0) -> dict[str, Any]:
    """Refine one proposed cut against measured signals.

    Returns `{status, position_seconds, adjustments[], vetoes[]}`.
    `status` is `placed` (position sits in a measured pause, clear of
    vetoes), `moved` (snapped to the nearest pause center within
    `search_seconds`), or `vetoed` (mid-word with no pause nearby,
    inside measured execution, or inside a crosstalk span). Prosody
    peaks push the cut to the nearest pause edge outside the keepout;
    when no pause qualifies, the peak keepout is REPORTED, never a veto
    on its own, because emphasis alone is not a no-cut signal.
    """
    adjustments: list[str] = []
    vetoes: list[str] = []
    position = float(candidate_seconds)
    ordered = sorted(words, key=lambda row: float(row["source_start"]))
    for row in ordered:
        word_start = float(row["source_start"])
        word_end = float(row["source_end"])
        if word_start < position < word_end:
            pauses = pause_center_cuts(
                ordered, min_pause_seconds=min_pause_seconds)
            nearby = [cut for cut in pauses
                      if abs(cut["position_seconds"] - position)
                      <= search_seconds]
            if not nearby:
                vetoes.append(
                    f"mid-word at {position}s with no measured pause "
                    f"within {search_seconds}s")
                return {"status": "vetoed", "position_seconds": position,
                        "adjustments": adjustments, "vetoes": vetoes}
            best = min(nearby,
                       key=lambda cut: abs(cut["position_seconds"]
                                           - position))
            adjustments.append(
                f"mid-word {position}s moved to pause center "
                f"{best['position_seconds']}s")
            position = float(best["position_seconds"])
            break
    for phased in phased_actions:
        if crosses_execution(position, phased):
            vetoes.append(
                f"position {position}s crosses measured action execution")
            return {"status": "vetoed", "position_seconds": position,
                    "adjustments": adjustments, "vetoes": vetoes}
    for span in crosstalk_spans:
        start = float(span.get("start_seconds", span.get("start", 0.0)))
        end = float(span.get("end_seconds", span.get("end", 0.0)))
        if start <= position < end:
            vetoes.append(
                f"position {position}s inside crosstalk span "
                f"{start}-{end}s")
            return {"status": "vetoed", "position_seconds": position,
                    "adjustments": adjustments, "vetoes": vetoes}
    for peak in prosody_peaks:
        if abs(float(peak) - position) <= peak_keepout_seconds:
            adjustments.append(
                f"position {position}s within {peak_keepout_seconds}s of "
                f"prosody peak at {peak}s; prefer a pause edge nearby")
            break
    status = "moved" if adjustments and any(
        "moved to pause center" in item for item in adjustments) else "placed"
    return {"status": status, "position_seconds": position,
            "adjustments": adjustments, "vetoes": vetoes}
