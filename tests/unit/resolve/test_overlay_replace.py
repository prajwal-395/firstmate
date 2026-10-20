"""The named overlay replace operation and its staged production path.

Each test names the defect it pins: the live run failed the ordinary
request "Replace the selected lower third with the supplied approved
version and preserve its position, timing, and all other approved
edits." with "no production path for overlay.replace: overlay.replace
is absent from the edit algebra" - a delete-plus-place composition is
not the named operation.
"""

from types import SimpleNamespace

from library.tools import edit_algebra, edit_preview, operation_acceptance
from library.tools import edit_execution_adapters as adapters

OPERATION = {
    "op": "overlay.replace",
    "unique_id": "clip-17",
    "file": "/media/approved-lower-third.png",
}


def _context(operation):
    return SimpleNamespace(
        operation=operation,
        snapshot={"reported_fps": 30.0, "tracks": [{
            "type": "video", "index": 2,
            "clips": [{
                "unique_id": "clip-17", "record_in": 20, "record_out": 80,
                "source_in_frame": 5, "source_out_frame": 64,
                "duration": 60, "source_file": "/media/music.wav",
            }],
        }]},
        request=SimpleNamespace(id="request-1", project="project-1",
                                timeline="Reel 01 - opening",
                                operations=[operation]),
    )


def _verified_touchup_receipt(spec):
    operation = spec["acceptance_operations"][0]
    return {
        "transaction": {"verification": {"status": "verified"}},
        "acceptance_contracts": [{
            "operation": operation["op"], "passed": True,
            "results": [{"check": "operation_readback_matches_request",
                          "passed": True}],
        }],
    }


def test_replace_is_a_canonical_operation_with_file_identity():
    """A delete-plus-place composition is not the named replace operation."""
    spec = edit_algebra.get("overlay.replace")
    assert spec is not None
    assert spec.domain == "fusion"
    assert tuple(spec.required) == ("unique_id", "file")
    assert spec.item is True
    assert spec.inverse.self_inverse is True
    assert "previous file" in spec.inverse.reason
    assert spec.key(dict(OPERATION)) == ("overlay", "clip-17")


def test_replace_is_complex_localized_never_direct():
    """A localized-but-complex overlay write must never go direct."""
    assert (edit_algebra.safety_class("overlay.replace")
            == edit_algebra.SAFETY_COMPLEX_LOCALIZED)
    assert edit_algebra.direct_blockers(["overlay.replace"], "reel_touchup")


def test_replace_has_a_registered_staged_backend():
    """The canonical op needs a production path, not an algebra absence."""
    assert "overlay.replace" in edit_algebra._EXECUTOR_COVERAGE["reel_touchup"]
    from library.tools import resolve_axi_core

    adapters.register_builtin_edit_executors()
    assert ("overlay.replace", "reel_touchup") in set(
        resolve_axi_core.registered_edit_executors())


def test_replace_adapter_reuses_swap_pixels_preserving_span(monkeypatch):
    """The approved file lands on the target's own row and index."""
    from pathlib import Path

    from library.tools import project_registry, reel_proposal, reel_touchup

    context = _context(dict(OPERATION))
    monkeypatch.setattr(project_registry, "resolve_project_path",
                        lambda _project: Path("/tmp/project/project.yaml"))
    proposal = SimpleNamespace(number=1, timeline_name="Reel 01 - opening")
    monkeypatch.setattr(reel_proposal, "proposal_path",
                        lambda _folder: "/tmp/project/proposal.json")
    monkeypatch.setattr(reel_proposal, "read_proposal",
                        lambda _path: [proposal])
    calls = []
    monkeypatch.setattr(reel_touchup, "apply_touchup", lambda _folder, spec: (
        calls.append(spec) or _verified_touchup_receipt(spec)))

    receipt = adapters._touchup(context)

    assert calls[0]["edits"] == [{
        "op": "swap_pixels", "row": "V2", "item": 0,
        "media": "/media/approved-lower-third.png",
    }]
    assert calls[0]["acceptance_operations"] == [context.operation]
    assert receipt["operation_id"] == "overlay.replace"
    assert (receipt["execution_contract"]["verification"]["status"]
            == "verified")
    assert (receipt["execution_contract"]["recovery"]["status"]
            == "recorded")


def test_replace_acceptance_requires_readback_and_collateral_evidence():
    """Success cannot be claimed from command prose without read-back."""
    contract = operation_acceptance.contract_for("overlay.replace")
    assert {"target_identity", "requested_timeline_span",
            "unrelated_items_unchanged"} <= set(contract["checks"])
    result = operation_acceptance.evaluate(
        dict(OPERATION), readback_passed=True,
        evidence={"unrelated_items_unchanged": True})
    assert result["passed"] is False
    assert set(result["missing"]) == {
        "projected_geometry_matches_request",
        "rendered_pixels_match_request",
        "bounding_box_matches_request",
        "inside_safe_zone",
        "not_clipped",
        "occlusion_acceptable",
        "no_unexpected_black_frames",
        "motion_matches_request",
    }


def test_replace_stays_fail_closed_until_live_qualification():
    """An unqualified op must not read as production-enabled."""
    status = edit_algebra.status("overlay.replace")
    assert status.compilable is True
    assert status.production_enabled is False
    assert status.qualification_status == "not_qualified"
    backend = status.for_backend("reel_touchup")
    assert backend is not None
    assert backend.production_enabled is False
    capability = edit_algebra.capability("overlay.replace")
    assert capability["implemented"] is True
    assert capability["production_enabled"] is False


def test_replace_previews_the_file_swap():
    """A dry run of the named op must show the file it would land."""
    assert (edit_preview._EFFECTS["overlay.replace"]
            is edit_preview._effect_overlay_swap_pixels)
    state = {"tracks": [{"type": "video", "index": 3, "clips": [{
        "unique_id": "ov-1", "record_in": 10, "record_out": 50,
        "overlay_file": "old-lower-third.png",
    }]}]}
    before, after = edit_preview._EFFECTS["overlay.replace"](
        {"unique_id": "ov-1", "file": "approved-lower-third.png"}, state)
    assert before == {"file": "old-lower-third.png"}
    assert after == {"file": "approved-lower-third.png"}
