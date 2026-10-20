from PIL import Image

from library.tools import operation_acceptance, overlay_verify, transaction_receipt


def test_overlay_acceptance_does_not_pass_without_visual_evidence():
    result = operation_acceptance.evaluate(
        {"op": "overlay.add", "row": 2, "record_frame": 10},
        readback_passed=True)
    assert not result["passed"]
    assert "rendered_pixels_match_request" in result["missing"]
    assert "inside_safe_zone" in result["missing"]


def test_property_write_uses_overlay_contract_only_when_explicitly_typed():
    generic = operation_acceptance.contract_for("clip.set_property", {
        "key": "Pan"})
    overlay = operation_acceptance.contract_for("clip.set_property", {
        "key": "Pan", "acceptance_op": "overlay.move"})
    assert "inside_safe_zone" not in generic["checks"]
    assert "inside_safe_zone" in overlay["checks"]


def test_every_production_enabled_operation_has_an_acceptance_contract():
    from library.tools import edit_algebra

    contracts = operation_acceptance.registry()
    enabled = [op_id for op_id, status in edit_algebra.OP_STATUS.items()
               if status.production_enabled]
    # Staged adapters stay fail-closed until their live qualification is
    # complete, so an empty enabled set is an expected state.
    assert all(op_id in contracts and contracts[op_id]["checks"]
               for op_id in enabled)


def test_overlay_acceptance_passes_only_when_each_contract_check_is_measured():
    operation = {"op": "overlay.add"}
    contract = operation_acceptance.contract_for(operation["op"])
    evidence = {check: True for check in contract["checks"]
                if check not in {"target_identity", "requested_timeline_span",
                                 "asset_identity", "unrelated_items_unchanged"}}
    result = operation_acceptance.evaluate(
        operation, readback_passed=True,
        evidence={**evidence, "unrelated_items_unchanged": True})
    assert result["passed"]


def test_rendered_frame_verifier_measures_bbox_safe_zone_and_clipping(tmp_path):
    path = tmp_path / "overlay.png"
    image = Image.new("RGBA", (100, 80), (0, 0, 0, 0))
    for x in range(20, 40):
        for y in range(15, 30):
            image.putpixel((x, y), (255, 20, 10, 255))
    image.save(path)
    mask_path = tmp_path / "forbidden.png"
    Image.new("L", (100, 80), 0).save(mask_path)
    result = overlay_verify.verify_rendered_frames(
        [{"path": str(path)}], expected_bbox=(20, 15, 40, 30),
        safe_zone=(5, 5, 95, 75), forbidden_mask_path=str(mask_path))
    assert result["passed"]
    assert result["measured"][0]["bbox"] == (20, 15, 40, 30)
    assert result["occlusion"]["status"] == "measured"
    assert result["measured"][0]["alpha_ink_pixels"] == 300


def test_rendered_frame_verifier_checks_animated_size_and_binary_occlusion(tmp_path):
    paths = []
    for index, box in enumerate(((10, 10, 30, 30), (20, 10, 50, 30))):
        path = tmp_path / f"frame-{index}.png"
        image = Image.new("RGBA", (100, 80), (0, 0, 0, 0))
        for x in range(box[0], box[2]):
            for y in range(box[1], box[3]):
                image.putpixel((x, y), (255, 20, 10, 255))
        image.save(path)
        paths.append({"path": str(path)})
    forbidden = tmp_path / "forbidden.png"
    mask = Image.new("L", (100, 80), 0)
    mask.putpixel((15, 15), 128)
    mask.save(forbidden)

    result = overlay_verify.verify_rendered_frames(
        paths, expected_boxes=((10, 10, 30, 30), (20, 10, 50, 30)),
        forbidden_mask_path=str(forbidden))

    assert not result["passed"]
    assert result["measured"][1]["bbox"] == (20, 10, 50, 30)
    assert result["findings"][0]["check"] == "occlusion_acceptable"
    assert result["findings"][0]["overlap_pixels"] == 1


def test_lifecycle_stops_at_applied_when_acceptance_fails():
    receipt = transaction_receipt.standardize({
        "status": "verification_failed", "patch_id": "p-1",
        "base_generation": 4, "generation": 5,
        "operations": [{"returned": True}],
        "acceptance_contracts": [{"passed": False}],
    })
    assert receipt["lifecycle"]["stage"] == "APPLIED"
    assert receipt["verified"]["status"] == "failed"
    assert receipt["committed"]["status"] == "readback_persisted_unverified"


def test_lifecycle_does_not_verify_a_receipt_with_no_operation_evidence():
    receipt = transaction_receipt.standardize({
        "status": "committed", "patch_id": "p-2",
        "base_generation": 4, "generation": 5,
        "operations": [{"returned": True}],
    })

    assert receipt["verified"]["status"] == "failed"
    assert receipt["committed"]["status"] == "readback_persisted_unverified"
    assert receipt["lifecycle"]["stage"] == "APPLIED"


def test_acceptance_distinguishes_missing_evidence_from_failed_measurement():
    result = operation_acceptance.evaluate(
        {"op": "clip.move"}, readback_passed=True,
        evidence={"target_identity": False})
    assert "target_identity" in result["failed"]
    assert "unrelated_items_unchanged" in result["missing"]


def test_acceptance_reports_unavailable_readback_as_missing_evidence():
    result = operation_acceptance.evaluate(
        {"op": "caption_fix", "unique_id": "clip-1", "text": "Correct"},
        readback_passed=None)
    assert not result["passed"]
    assert "operation_readback_matches_request" in result["missing"]


def test_caption_style_contract_demands_independent_rendered_evidence():
    """Defect: a restyle claimed from prose while the render was never
    measured would ship the wrong look under a passing receipt."""
    result = operation_acceptance.evaluate(
        {"op": "caption.style", "unique_id": "clip-9", "style": "minimal"},
        readback_passed=True)
    assert not result["passed"]
    assert "caption_text_preserved" in result["missing"]
    assert "caption_style_applied" in result["missing"]
    assert "unrelated_items_unchanged" in result["missing"]


def test_caption_style_passes_only_when_every_check_is_measured():
    """Defect: a restyle that rewrote words, skipped the swap, or moved
    a neighbour must read as failed, never as verified."""
    contract = operation_acceptance.contract_for("caption.style")
    assert contract["checks"] == [
        "target_identity", "requested_record_span",
        "caption_text_preserved", "caption_style_applied",
        "unrelated_items_unchanged"]
    assert contract["rendered_source"] == "caption_asset_rerender"
    evidence = {check: True for check in contract["checks"]}
    result = operation_acceptance.evaluate(
        {"op": "caption.style", "unique_id": "clip-9", "style": "minimal"},
        readback_passed=True, evidence=evidence)
    assert result["passed"]
    rewritten = operation_acceptance.evaluate(
        {"op": "caption.style", "unique_id": "clip-9", "style": "minimal"},
        readback_passed=True,
        evidence={**evidence, "caption_text_preserved": False})
    assert not rewritten["passed"]
    assert "caption_text_preserved" in rewritten["failed"]


def test_audio_changes_require_independent_render_evidence():
    result = operation_acceptance.evaluate(
        {"op": "audio.gain"}, readback_passed=True,
        evidence={"target_identity": True, "requested_audio_span": True,
                  "audio_parameters_match_request": True,
                  "unrelated_items_unchanged": True})
    assert not result["passed"]
    assert result["audio_source"] == "independent_audio_render"
    assert "audio_render_matches_request" in result["missing"]
