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


def detect_non_v1_fusion_drops(
    per_clip_effects: dict[str, dict],
    v1_labels: set[str],
    v2_labels: set[str],
) -> list[dict[str, Any]]:
    """Find per_clip Fusion effects that the subprocess cannot reach.

    The Fusion subprocess (apply_fusion_comps.py) only iterates V1 clips.
    Per-clip effects planned for labels on other tracks (e.g. B-roll on V2)
    are silently dropped. This function detects and reports them.

    Returns a list of dicts, each with:
      - ``label``: the clip label
      - ``track``: "V2" or "unknown track"
      - ``params``: the effect parameters that would have been applied
      - ``detail``: a human-readable string for the error message

    An empty return means every planned effect has a clip on V1.
    """
    if not per_clip_effects:
        return []

    dropped = []
    for label in sorted(per_clip_effects):
        if label in v1_labels:
            continue  # V1 - subprocess handles these
        params = per_clip_effects[label]
        track = "V2" if label in v2_labels else "unknown track"
        dropped.append({
            "label": label,
            "track": track,
            "params": params,
            "detail": f"{label} ({track}): {json.dumps(params, default=str)}",
        })

    return dropped


def format_fusion_drop_error(dropped: list[dict[str, Any]]) -> str:
    """Format the dropped-effects list into a single error message."""
    details = "; ".join(d["detail"] for d in dropped)
    return (
        f"Fusion per_clip effects planned for {len(dropped)} "
        f"non-V1 clip(s) that the Fusion subprocess cannot reach: "
        + details
    )
