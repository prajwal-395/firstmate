"""Pure verification helpers for the timeline builder.

These functions contain the logic that decides whether a build's QA
verification passed and whether planned Fusion effects were dropped.
They are deliberately free of any Resolve import or side effect so they
can be tested with plain data objects in CI.

The renderer (resolve_build_timeline.py) calls them; the test suite
imports and exercises them directly.
"""
import json
from typing import Any, Sequence


def derive_verification_verdict(qa_reports: Sequence[Any]) -> bool:
    """Derive the verification verdict from QA station outcomes.

    Each report must have a boolean ``.passed`` attribute. Returns True
    when all stations passed, or when no stations ran at all (no evidence
    of failure). Returns False when any station failed.

    This was previously a hardcoded ``all_passed = True`` that was never
    reassigned, so the gate could never fail regardless of station
    outcomes.
    """
    if not qa_reports:
        return True
    return all(rep.passed for rep in qa_reports)


def detect_unreachable_fusion_effects(
    per_clip_effects: dict[str, dict],
    labels_by_track: dict[int, set],
) -> list[dict[str, Any]]:
    """Find per_clip Fusion effects that no placed clip can carry.

    ``labels_by_track`` maps a video track index to the clip labels this
    build really placed on it, and its KEYS are the tracks the Fusion
    subprocess walks - see
    ``library/tools/execution/fusion_tracks.FUSION_COMP_TRACKS``. A label
    that appears on none of them gets no comp, and nothing else in the
    build notices: the picture underneath is intact and only the look is
    missing.

    V2 used to be unreachable by construction, because the subprocess
    read ``tracks['V1']`` alone. That is fixed, so a B-roll label is now
    a normal hit rather than a permanent drop, and what survives here is
    the real question: was this label placed at all?

    Returns a list of dicts, each with ``label``, ``track`` (the track it
    was expected on, or ``"unplaced"``), ``params`` and a human-readable
    ``detail``. An empty return means every planned effect has a clip.
    """
    if not per_clip_effects:
        return []

    reachable = set()
    for labels in labels_by_track.values():
        reachable |= set(labels)

    dropped = []
    for label in sorted(per_clip_effects):
        if label in reachable:
            continue
        params = per_clip_effects[label]
        dropped.append({
            "label": label,
            "track": "unplaced",
            "params": params,
            "detail": f"{label} (unplaced): {json.dumps(params, default=str)}",
        })

    return dropped


def format_fusion_drop_error(dropped: list[dict[str, Any]]) -> str:
    """Format the dropped-effects list into a single error message."""
    details = "; ".join(d["detail"] for d in dropped)
    return (
        f"Fusion per_clip effects planned for {len(dropped)} "
        f"clip(s) the Fusion pass cannot reach: "
        + details
    )
