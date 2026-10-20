"""multicam.select_angle: show the marked speaker's camera for their turn.

Defect: the ordinary request "Show the camera assigned to the marked
speaker for their speaking turn, preserving the edit points." had no
production path - multicam.select_angle was absent from the edit
algebra (first live run of the AXI execution consolidation punch
list). These contracts pin the operation's canonical shape, its
independent read-back, its acceptance evidence, and its fail-closed
posture until the live-requests lane records qualifying evidence.
"""

from types import SimpleNamespace

import pytest

from library.tools import (
    edit_algebra,
    edit_execution_adapters as adapters,
    operation_acceptance,
    resolve_axi_core,
)


def _snapshot():
    return {"reported_fps": 30.0, "tracks": [{
        "type": "video", "index": 1, "name": "V1", "clips": [{
            "unique_id": "turn-1", "record_in": 20, "record_out": 80,
            "source_in_frame": 5, "source_out_frame": 64,
            "duration": 60, "source_file": "/media/camera_b.mov",
        }],
    }]}


def _context(operation):
    return SimpleNamespace(
        operation=operation,
        snapshot=_snapshot(),
        compiled=SimpleNamespace(operations=()),
        operation_index=0,
        request=SimpleNamespace(id="request-1", project="project-1",
                                timeline="Reel 01 - opening",
                                operations=[operation]),
    )


def test_multicam_select_angle_is_a_canonical_algebra_operation():
    """Defect: the request named an operation the algebra did not contain."""
    spec = edit_algebra.get("multicam.select_angle")
    assert spec is not None
    assert spec.domain == "picture_transform"
    assert spec.required == ("unique_id", "angle_index")
    assert spec.item is True
    assert spec.merge == "replace"
    assert callable(spec.span)
    assert callable(spec.verify)
    assert spec.inverse.self_inverse
    assert spec.inverse.op is None
    assert "previous angle" in spec.inverse.reason
    status = edit_algebra.status("multicam.select_angle")
    assert status is not None
    assert status.id == "multicam.select_angle"
    assert set(status.param_schema["required"]) == set(spec.required)


def test_multicam_select_angle_verifier_detects_the_absent_write():
    """Defect: a verifier that passes on a timeline the op did not change
    would commit a lie. Presence alone never claims the camera: without
    an angle in the inventory the read-back fails, naming what is
    missing."""
    spec = edit_algebra.get("multicam.select_angle")
    missing = spec.verify(
        {"unique_id": "no-such-clip", "angle_index": 1},
        {"tracks": []})
    assert isinstance(missing, str) and missing
    unproven = spec.verify(
        {"unique_id": "turn-1", "angle_index": 1}, _snapshot())
    assert isinstance(unproven, str) and unproven
    assert "angle" in unproven


def test_multicam_select_angle_acceptance_needs_independent_angle_evidence():
    """Defect: claiming the camera without measuring it. The contract
    needs the angle read-back, the preserved record span, and an empty
    collateral diff - a missing angle is missing evidence, never a
    pass."""
    contract = operation_acceptance.contract_for("multicam.select_angle")
    assert contract["checks"] == [
        "target_identity", "requested_angle_selected",
        "record_span_preserved", "unrelated_items_unchanged",
    ]
    assert contract["structural_source"] == "timeline_readback"
    operation = {"op": "multicam.select_angle", "unique_id": "turn-1",
                 "angle_index": 1}
    unmeasured = operation_acceptance.evaluate(
        operation, readback_passed=True,
        evidence={"target_identity": True,
                  "record_span_preserved": True,
                  "unrelated_items_unchanged": True})
    assert not unmeasured["passed"]
    assert "requested_angle_selected" in unmeasured["missing"]
    contradicted = operation_acceptance.evaluate(
        operation, readback_passed=True,
        evidence={"target_identity": True,
                  "requested_angle_selected": False,
                  "record_span_preserved": True,
                  "unrelated_items_unchanged": True})
    assert not contradicted["passed"]
    assert "requested_angle_selected" in contradicted["failed"]
    proven = operation_acceptance.evaluate(
        operation, readback_passed=True,
        evidence={"target_identity": True,
                  "requested_angle_selected": True,
                  "record_span_preserved": True,
                  "unrelated_items_unchanged": True})
    assert proven["passed"]


def test_multicam_select_angle_adapter_is_registered_but_refuses_before_mutation(
        monkeypatch):
    """Defect: no production path for the request. The pair is
    registered so routing resolves it, and the executor refuses before
    any Resolve contact - there is no guarded set-angle verb and no
    angle in the inventory to verify against."""
    adapters.register_builtin_edit_executors()
    assert ("multicam.select_angle", "resolve_axi") in set(
        resolve_axi_core.registered_edit_executors())

    def _explodes(*_args, **_kwargs):
        raise AssertionError("Resolve was contacted")

    monkeypatch.setattr(resolve_axi_core, "execute", _explodes)
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline", _explodes)
    with pytest.raises(RuntimeError, match="refused before mutation"):
        adapters._multicam_select_angle(_context({
            "op": "multicam.select_angle", "unique_id": "turn-1",
            "angle_index": 1,
        }))


def test_multicam_select_angle_revalidates_target_identity_first():
    """Defect: switching the wrong turn. A stale or ambiguous target,
    or a non-integer angle, refuses as a ValueError before the
    production refusal is even reached."""
    with pytest.raises(ValueError, match="resolved to 0 timeline items"):
        adapters._multicam_select_angle(_context({
            "op": "multicam.select_angle", "unique_id": "no-such-clip",
            "angle_index": 1,
        }))
    with pytest.raises(ValueError, match="integer angle_index"):
        adapters._multicam_select_angle(_context({
            "op": "multicam.select_angle", "unique_id": "turn-1",
            "angle_index": "camera-b",
        }))


def test_multicam_select_angle_stays_fail_closed_for_production():
    """Defect: promoting an unqualified write. The op stays
    unrepresentable, unqualified, and blocked from direct execution
    until the live-requests lane records qualifying evidence."""
    assert (edit_algebra.safety_class("multicam.select_angle")
            == edit_algebra.SAFETY_UNREPRESENTABLE)
    status = edit_algebra.status("multicam.select_angle")
    assert status.qualification_status == "not_qualified"
    assert not status.qualified
    assert not status.production_enabled
    row = status.for_backend("resolve_axi")
    assert row is not None
    assert row.compilable is True
    assert row.qualification_status == "not_qualified"
    assert row.implementation_status == "implemented_unqualified"
    assert not edit_algebra.is_production_enabled(
        "multicam.select_angle", "resolve_axi")
    assert not edit_algebra.has_independent_readback("multicam.angle")
    assert edit_algebra.has_independent_readback("multicam.select_angle")
    assert edit_algebra.direct_blockers(
        ["multicam.select_angle"], "resolve_axi")
