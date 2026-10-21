"""Operation-specific acceptance evidence for Resolve edits.

The editor's read-back is the structural baseline. Overlay acceptance also
requires independent rendered evidence; inability to measure is a refusal
to claim success, never an implicit pass.
"""

from __future__ import annotations

_OVERLAY_VISUAL = (
    "projected_geometry_matches_request", "rendered_pixels_match_request",
    "bounding_box_matches_request", "inside_safe_zone", "not_clipped",
    "occlusion_acceptable", "no_unexpected_black_frames",
    "motion_matches_request",
)

_STRUCTURAL_CHECKS = {
    "operation_readback_matches_request", "target_identity",
    "requested_source_in_out", "requested_record_span",
    "requested_record_or_source_position", "duration_preserved",
    "requested_track", "target_absent", "source_identity",
    "requested_source_identity", "record_span_preserved",
    "requested_timeline_span", "asset_identity", "requested_size",
    "requested_position",
}


def contract_for(op_id: str, operation: dict | None = None) -> dict:
    """Describe required evidence for one semantic operation."""
    operation = operation or {}
    op_id = operation.get("acceptance_op", op_id)
    if op_id in {"overlay.add", "overlay.move", "overlay.resize",
                   "overlay.position", "overlay.swap_pixels", "overlay.replace",
                   "overlay.entry_motion"}:
        checks = ["target_identity", "requested_timeline_span",
                  "unrelated_items_unchanged", *_OVERLAY_VISUAL]
        if op_id == "overlay.add":
            checks.extend(("asset_identity", "alpha_preserved"))
        if op_id == "overlay.resize":
            checks.append("requested_size")
        if op_id == "overlay.position":
            checks.append("requested_position")
        return {"id": f"acceptance:{op_id}", "operation": op_id,
                "checks": list(dict.fromkeys(checks)),
                "structural_source": "timeline_readback",
                "visual_source": "independent_render"}
    if op_id == "clip.trim":
        checks = ["target_identity", "requested_source_in_out",
                  "requested_record_span", "approved_edits_preserved",
                  "linked_audio_preserved",
                  "unrelated_items_unchanged"]
    elif op_id in {"clip.move", "clip.slide", "clip.roll", "clip.slip"}:
        checks = ["target_identity", "requested_record_or_source_position",
                  "duration_preserved", "unrelated_items_unchanged"]
    elif op_id == "clip.insert":
        checks = ["target_identity", "source_identity", "requested_record_span",
                  "requested_track", "unrelated_items_unchanged"]
    elif op_id == "broll.insert":
        # The cover placement reads back like a clip.insert, and the
        # instruction additionally promises the underlying speech
        # survives: the A-roll rows and every audio row overlapping the
        # covered span must read back identical. Both halves are
        # structural timeline read-back, judged on the fresh live
        # snapshot - never on command prose.
        checks = ["target_identity", "source_identity",
                  "requested_record_span", "requested_track",
                  "underlying_speech_preserved",
                  "unrelated_items_unchanged"]
    elif op_id in {"clip.delete", "overlay.remove"}:
        checks = ["target_absent", "unrelated_items_unchanged"]
    elif op_id == "clip.remove":
        # A ripple remove promises more than an absence: the gap it
        # occupied closes, the target track's later clips and their
        # explicit link groups move one removed-duration earlier, and all
        # other clips stay fixed - reads back
        # from the fresh live snapshot. `gap_closed` and
        # `downstream_continuity_preserved` are deliberately outside
        # `_STRUCTURAL_CHECKS`, so they stay missing (never silently
        # passed) until the executing adapter measures them.
        checks = ["target_absent", "gap_closed",
                  "downstream_continuity_preserved",
                  "unrelated_items_unchanged"]
    elif op_id == "reel.opening":
        # The opening's own read-back postconditions: every carried trim
        # reads back at its declared span and every carried remove is
        # absent (`requested_opening_span`), retained clips keep their
        # source identity and transforms (`approved_edits_preserved`),
        # and nothing outside the decided edits moved
        # (`unrelated_items_unchanged`). All four arrive as explicit
        # adapter evidence off a fresh live read-back; anything missing
        # fails the contract rather than passing it.
        checks = ["target_identity", "requested_opening_span",
                  "approved_edits_preserved", "unrelated_items_unchanged"]
    elif op_id == "clip.replace":
        checks = ["target_identity", "requested_source_identity",
                  "record_span_preserved", "unrelated_items_unchanged"]
    elif op_id == "multicam.select_angle":
        checks = ["target_identity", "requested_camera_source_selected",
                  "record_span_preserved", "unrelated_items_unchanged"]
    elif op_id == "caption_fix":
        checks = ["target_identity", "requested_record_span",
                  "caption_text_applied", "caption_timing_preserved",
                  "unrelated_items_unchanged"]
        return {"id": f"acceptance:{op_id}", "operation": op_id,
                "checks": checks, "structural_source": "timeline_readback",
                "rendered_source": "caption_asset_rerender"}
    elif op_id == "caption.style":
        checks = ["target_identity", "requested_record_span",
                  "caption_text_preserved", "caption_style_applied",
                  "unrelated_items_unchanged"]
        return {"id": f"acceptance:{op_id}", "operation": op_id,
                "checks": checks, "structural_source": "timeline_readback",
                "rendered_source": "caption_asset_rerender"}
    elif op_id in {"audio.gain", "audio.fade"}:
        checks = ["target_identity", "requested_audio_span",
                  "audio_parameters_match_request", "unrelated_items_unchanged",
                  "audio_render_matches_request", "no_clipping",
                  "no_unintended_silence"]
        return {"id": f"acceptance:{op_id}", "operation": op_id,
                "checks": checks, "structural_source": "timeline_readback",
                "audio_source": "independent_audio_render"}
    elif op_id == "transition":
        checks = ["target_identity", "requested_timeline_span",
                  "transition_parameters_match_request",
                  "unrelated_items_unchanged", "rendered_transition_matches_request"]
        return {"id": f"acceptance:{op_id}", "operation": op_id,
                "checks": checks, "structural_source": "timeline_readback",
                "visual_source": "independent_render"}
    else:
        checks = ["operation_readback_matches_request",
                  "unrelated_items_unchanged"]
    return {"id": f"acceptance:{op_id}", "operation": op_id,
            "checks": checks, "structural_source": "timeline_readback"}


def registry() -> dict[str, dict]:
    """Acceptance contracts for the complete semantic operation vocabulary."""
    from library.tools import edit_algebra

    return {op_id: contract_for(op_id) for op_id in edit_algebra.OP_STATUS}


def evaluate(operation: dict, *, readback_passed: bool | None,
             evidence: dict | None = None) -> dict:
    """Evaluate one contract; absent evidence is explicitly reported."""
    evidence = evidence or {}
    op_id = operation.get("acceptance_op") or operation["op"]
    contract = contract_for(op_id, operation)
    results = []
    for check in contract["checks"]:
        if check in evidence:
            passed = evidence[check] is True
            status = "passed" if passed else "failed"
        elif check in _STRUCTURAL_CHECKS:
            passed = readback_passed is True
            status = ("passed" if passed else
                      "failed" if readback_passed is False else "missing")
        elif check == "unrelated_items_unchanged":
            passed = False
            status = "missing"
        else:
            passed = False
            status = "missing"
        results.append({"check": check, "passed": bool(passed),
                        "status": status, "evidence": evidence.get(check)})
    return {**contract, "passed": all(row["passed"] for row in results),
            "results": results,
            "missing": [row["check"] for row in results
                        if row["status"] == "missing"],
            "failed": [row["check"] for row in results
                       if row["status"] == "failed"]}
