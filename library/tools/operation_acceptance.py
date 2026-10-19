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


def contract_for(op_id: str, operation: dict | None = None) -> dict:
    """Describe required evidence for one semantic operation."""
    operation = operation or {}
    op_id = operation.get("acceptance_op", op_id)
    if op_id in {"overlay.add", "overlay.move", "overlay.resize",
                 "overlay.swap_pixels", "overlay.entry_motion"}:
        checks = ["target_identity", "requested_timeline_span",
                  "unrelated_items_unchanged", *_OVERLAY_VISUAL]
        if op_id == "overlay.add":
            checks.extend(("asset_identity", "alpha_preserved"))
        if op_id == "overlay.resize":
            checks.append("requested_size")
        return {"id": f"acceptance:{op_id}", "operation": op_id,
                "checks": list(dict.fromkeys(checks)),
                "structural_source": "timeline_readback",
                "visual_source": "independent_render"}
    if op_id == "clip.trim":
        checks = ["target_identity", "requested_source_in_out",
                  "requested_record_span", "unrelated_items_unchanged"]
    elif op_id in {"clip.move", "clip.slide", "clip.roll", "clip.slip"}:
        checks = ["target_identity", "requested_record_or_source_position",
                  "duration_preserved", "unrelated_items_unchanged"]
    elif op_id == "clip.insert":
        checks = ["target_identity", "source_identity", "requested_record_span",
                  "requested_track", "unrelated_items_unchanged"]
    elif op_id in {"clip.delete", "overlay.remove"}:
        checks = ["target_absent", "unrelated_items_unchanged"]
    elif op_id == "clip.replace":
        checks = ["target_identity", "requested_source_identity",
                  "record_span_preserved", "unrelated_items_unchanged"]
    else:
        checks = ["operation_readback_matches_request",
                  "unrelated_items_unchanged"]
    return {"id": f"acceptance:{op_id}", "operation": op_id,
            "checks": checks, "structural_source": "timeline_readback"}


def registry() -> dict[str, dict]:
    """Acceptance contracts for the complete semantic operation vocabulary."""
    from library.tools import edit_algebra

    return {op_id: contract_for(op_id) for op_id in edit_algebra.OP_STATUS}


def evaluate(operation: dict, *, readback_passed: bool,
             evidence: dict | None = None) -> dict:
    """Evaluate one contract; absent evidence is explicitly reported."""
    evidence = evidence or {}
    op_id = operation.get("acceptance_op", operation["op"])
    contract = contract_for(op_id, operation)
    results = []
    for check in contract["checks"]:
        if check in {"operation_readback_matches_request", "target_identity",
                     "requested_source_in_out", "requested_record_span",
                     "requested_record_or_source_position", "duration_preserved",
                     "requested_track", "target_absent", "source_identity",
                     "requested_source_identity", "record_span_preserved",
                     "requested_timeline_span", "asset_identity",
                     "unrelated_items_unchanged"}:
            passed = (evidence[check] is True if check in evidence else
                      readback_passed if check != "unrelated_items_unchanged" else False)
            # The algebra verifier is operation-specific, so its success
            # establishes the matching structural clause. Collateral diff
            # is separate and must be measured by a capable caller.
        else:
            passed = evidence.get(check) is True
        results.append({"check": check, "passed": bool(passed),
                        "evidence": evidence.get(check)})
    return {**contract, "passed": all(row["passed"] for row in results),
            "results": results,
            "missing": [row["check"] for row in results
                        if not row["passed"] and row["evidence"] is None]}
