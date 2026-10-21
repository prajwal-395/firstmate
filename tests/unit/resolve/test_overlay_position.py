"""overlay.position: the staged translate-an-overlay operation.

Defects this names:
- the live request "move the lower third up by 40 output pixels" had no
  production path because overlay.position was absent from the edit
  algebra (data/vep-axi-live-qual editorial_acceptance, move_lower_third);
  moving via clip.set_property and naming it overlay.position would
  mislabel the executed operation.
- a position op without its own acceptance contract could claim success
  from structural read-back alone, with no rendered-pixel evidence that
  the box moved to the requested output pixels.
- a position mixed with another edit in one request would make the
  enabled/disabled rendered-pixel diff ambiguous about what moved.

Left for the full suite: these tests run there, not locally, per the
captain's no-local-tests rule. Production stays fail-closed until the
live-requests lane records qualifying evidence; nothing here marks the
op qualified.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import (
    edit_algebra,
    edit_execution_adapters,
    execution_router,
    operation_acceptance,
)


def _context(operation):
    return SimpleNamespace(
        operation=operation,
        snapshot={"reported_fps": 30.0, "tracks": [{
            "type": "video", "index": 3,
            "clips": [{
                "unique_id": "ov-1", "record_in": 96, "record_out": 216,
                "source_in_frame": 0, "source_out_frame": 119,
                "duration": 120, "source_file": "/media/logo.png",
            }],
        }]},
        request=SimpleNamespace(id="request-1", project="project-1",
                                timeline="Reel 01 - opening",
                                operations=[operation]),
    )


def test_position_is_a_canonical_algebra_op_with_signed_pixel_deltas():
    """The missing operation exists with the request's own vocabulary."""
    spec = edit_algebra.get("overlay.position")
    assert spec is not None
    assert spec.id == "overlay.position"
    assert spec.domain == "fusion"
    assert set(spec.required) == {"unique_id", "dx", "dy"}
    assert spec.item is True
    assert spec.key({"unique_id": "ov-1"}) == ("overlay", "ov-1")


def test_position_inverse_carries_the_previous_position():
    """A self-inverse value setter: the inverse restores, never replays."""
    spec = edit_algebra.get("overlay.position")
    assert spec.inverse.self_inverse is True
    assert spec.inverse.op is None
    assert "previous position" in spec.inverse.reason


def test_position_is_complex_localized_staged_never_direct():
    """A localized picture change stages with verification; direct is out."""
    assert (edit_algebra.safety_class("overlay.position")
            == edit_algebra.SAFETY_COMPLEX_LOCALIZED)


def test_position_is_qualified_on_the_state_the_evidence_was_earned_in():
    """The live qualification (AXI-008) earned the pair its predicates.

    The overlay geometry routes were implemented but unqualified: their
    own note said the live Resolve qualification "remains deferred".
    The AXI-008 lane ran them against a real Resolve on a disposable
    copy of the private scratch timeline, judged every geometry row on
    the RENDERED FRAMES, and the evidence was imported through the
    registry tool.  The pair is now production-ENABLED inside the
    measured state - and the state is named, not assumed.
    """
    status = edit_algebra.status("overlay.position")
    assert status is not None
    assert status.compilable is True
    assert status.qualified is True
    assert status.production_enabled is True
    row = status.for_backend("reel_touchup")
    assert row is not None and row.compilable is True
    assert row.production_enabled is True
    fields = {predicate["field"]: predicate["value"]
              for predicate in (row.qualification_predicates or [])}
    assert fields["frame_rate"] == "24"
    assert "still" in fields["media_type"]
    assert "video" in fields["media_type"]


def test_position_acceptance_needs_rendered_evidence_not_prose():
    """The contract names the requested position and refuses bare prose."""
    contract = operation_acceptance.contract_for("overlay.position")
    assert "requested_position" in contract["checks"]
    assert "unrelated_items_unchanged" in contract["checks"]
    assert "rendered_pixels_match_request" in contract["checks"]
    assert "inside_safe_zone" in contract["checks"]
    result = operation_acceptance.evaluate(
        {"op": "overlay.position", "unique_id": "ov-1",
         "dx": 0.0, "dy": -40.0},
        readback_passed=True)
    assert result["passed"] is False
    assert "rendered_pixels_match_request" in result["missing"]


def test_position_acceptance_fails_on_collateral_change():
    """One changed unrelated item fails the contract, never promotes."""
    contract = operation_acceptance.contract_for("overlay.position")
    evidence = {check: True for check in contract["checks"]}
    evidence["unrelated_items_unchanged"] = False
    result = operation_acceptance.evaluate(
        {"op": "overlay.position", "unique_id": "ov-1",
         "dx": 0.0, "dy": -40.0},
        readback_passed=True, evidence=evidence)
    assert result["passed"] is False
    assert "unrelated_items_unchanged" in result["failed"]


def test_position_adapter_is_registered_on_the_staged_backend(monkeypatch):
    """The operation binds to code, not just to a vocabulary row."""
    from library.tools import resolve_axi_core

    edit_execution_adapters.register_builtin_edit_executors()
    assert (("overlay.position", "reel_touchup")
            in resolve_axi_core.registered_edit_executors())


def test_position_adapter_maps_deltas_to_a_staged_touchup(monkeypatch):
    """dx/dy reach the touch-up as signed output pixels, target re-read."""
    from library.tools import project_registry, reel_proposal, reel_touchup

    calls = []

    def fake_touchup(_folder, spec):
        calls.append(spec)
        operation = spec["acceptance_operations"][0]
        return {
            "journal": "journal-touchup-test",
            "transaction": {"verification": {"status": "verified"}},
            "acceptance_contracts": [{
                "operation": operation["op"], "passed": True,
                "results": [{"check": "operation_readback_matches_request",
                             "passed": True}],
            }],
        }

    monkeypatch.setattr(project_registry, "resolve_project_path",
                        lambda _project: Path("/tmp/project/project.yaml"))
    proposal = SimpleNamespace(number=1, timeline_name="Reel 01 - opening")
    monkeypatch.setattr(reel_proposal, "proposal_path",
                        lambda _folder: "/tmp/project/proposal.json")
    monkeypatch.setattr(reel_proposal, "read_proposal",
                        lambda _path: [proposal])
    monkeypatch.setattr(reel_touchup, "apply_touchup", fake_touchup)
    context = _context({"op": "overlay.position", "unique_id": "ov-1",
                        "dx": 0.0, "dy": -40.0})

    edit_execution_adapters._touchup(context)

    assert calls[0]["edits"] == [{
        "op": "position_overlay", "row": "V3", "item": 0,
        "dx": 0.0, "dy": -40.0,
    }]
    assert calls[0]["acceptance_operations"] == [context.operation]


def test_position_qualify_refuses_a_zero_delta():
    """A move that moves nothing is not a staged write."""
    from library.tools import reel_touchup

    tracks = [{
        "type": "video", "index": 3, "clips": [{
            "record_in": 96, "record_out": 216, "duration": 120,
            "source_file": "/media/logo.png", "media_type": "Still",
            "enabled": True,
        }],
    }]
    with pytest.raises(reel_touchup.TouchupRefused, match="non-zero"):
        reel_touchup._op_position_overlay(
            {"row": "V3", "item": 0, "dx": 0.0, "dy": 0.0}, 0,
            tracks, {}, [], [], [], [], [], [], [])


def test_position_qualify_must_ride_alone():
    """A second edit in the request makes the pixel diff ambiguous."""
    from library.tools import reel_touchup

    tracks = [{
        "type": "video", "index": 3, "clips": [{
            "record_in": 96, "record_out": 216, "duration": 120,
            "source_file": "/media/logo.png", "media_type": "Still",
            "enabled": True,
        }],
    }]
    spec = {"reel": 1, "edits": [
        {"op": "position_overlay", "row": "V3", "item": 0,
         "dx": 0.0, "dy": -40.0},
        {"op": "position_overlay", "row": "V3", "item": 0,
         "dx": 10.0, "dy": 0.0},
    ]}
    with pytest.raises(reel_touchup.TouchupRefused, match="only edit"):
        reel_touchup.qualify(tracks, spec)


def test_production_routing_stages_position_where_the_evidence_was_earned():
    """The qualified pair routes, and it routes STAGED.

    Before the AXI-008 qualification the pair had no evidence and
    ordinary routing refused it by name.  It is qualified now, inside
    the state the live run measured, and the route it takes is the
    staged one - the approved timeline is never edited in place.
    """
    operations = [{"op": "overlay.position", "unique_id": "ov-1",
                   "dx": 0.0, "dy": -40.0}]
    decision = execution_router.route_compiled_edit(
        {"operations": operations},
        context={"incremental_eligibility": True,
                 "qualification_policy": "protected_experimental",
                 "protected_staging": True,
                 "preserve_approved_state": True,
                 "verify_after_write": True})
    # The qualified pair routes through the incremental STRATEGY, whose
    # executor is the touch-up path, and it routes STAGED: the approved
    # timeline is never edited in place.
    assert decision.backend == "incremental"
    assert decision.mode == "staged"


def test_preview_accumulates_signed_deltas():
    """The dry-run shows where the box goes: up 40 reads dy=-40."""
    from library.tools import edit_preview

    base = {
        "start_frame": 0, "end_frame": 216,
        "tracks": [{
            "type": "video", "index": 3, "name": "Overlays",
            "clips": [{
                "unique_id": "ov-1", "record_in": 96, "record_out": 216,
                "duration": 120,
            }],
        }],
    }
    preview = edit_preview.preview_program(
        [{"op": "overlay.position",
          "params": {"unique_id": "ov-1", "dx": 0.0, "dy": -40.0}},
         {"op": "overlay.position",
          "params": {"unique_id": "ov-1", "dx": 10.0, "dy": 0.0}}],
        base)
    assert preview["ops"][0]["affected_span"] == [96, 216]
    assert preview["ops"][0]["before"] == {"dx": 0, "dy": 0}
    assert preview["ops"][0]["after"] == {"dx": 0.0, "dy": -40.0}
    assert preview["ops"][1]["after"] == {"dx": 10.0, "dy": -40.0}
    assert preview["total_duration_change"] == 0
