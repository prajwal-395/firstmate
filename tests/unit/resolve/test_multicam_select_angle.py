"""multicam.select_angle: show the marked speaker's assigned camera.

Defect: live request ``right_speaker_camera`` was refused by design
because preparation did not carry an assigned camera's synced source
range to an executable replacement. These contracts cover the resolved
range, preservation checks, and the still-unqualified production route.
"""

from types import SimpleNamespace

import pytest

from library.tools import (
    edit_algebra,
    edit_execution_adapters as adapters,
    operation_acceptance,
    resolve_axi_core,
)


def _snapshot(unique_id="turn-1"):
    return {"reported_fps": 30.0, "tracks": [{
        "type": "video", "index": 1, "name": "V1", "clips": [{
            "unique_id": unique_id, "record_in": 20, "record_out": 80,
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
    assert spec.required == (
        "unique_id", "speaker_id", "assignment_basis", "source_file",
        "source_in_frame", "source_out_frame", "sync_evidence_id")
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
    would commit a lie. Presence alone never claims the camera source:
    the read-back must name the requested source and synced range."""
    spec = edit_algebra.get("multicam.select_angle")
    operation = {"unique_id": "no-such-clip", "source_file": "/media/camera_b.mov",
                 "source_in_frame": 5, "source_out_frame": 64}
    missing = spec.verify(operation, {"tracks": []})
    assert isinstance(missing, str) and missing
    unproven = spec.verify(
        {**operation, "unique_id": "turn-1"}, _snapshot())
    assert isinstance(unproven, str) and unproven
    assert "camera" in unproven


def test_multicam_select_angle_acceptance_needs_camera_source_evidence():
    """Defect: claiming the camera without measuring it. The contract
    needs camera source read-back, the preserved record span, and an
    empty collateral diff - missing source evidence never passes."""
    contract = operation_acceptance.contract_for("multicam.select_angle")
    assert contract["checks"] == [
        "target_identity", "requested_camera_source_selected",
        "record_span_preserved", "unrelated_items_unchanged",
    ]
    assert contract["structural_source"] == "timeline_readback"
    operation = {"op": "multicam.select_angle", "unique_id": "turn-1",
                 "speaker_id": "speaker-1",
                 "assignment_basis": "diarized_speaker_identity",
                 "source_file": "/media/camera_b.mov",
                 "source_in_frame": 5, "source_out_frame": 64,
                 "sync_evidence_id": "sync:camera-b:turn-1"}
    unmeasured = operation_acceptance.evaluate(
        operation, readback_passed=True,
        evidence={"target_identity": True,
                  "record_span_preserved": True,
                  "unrelated_items_unchanged": True})
    assert not unmeasured["passed"]
    assert "requested_camera_source_selected" in unmeasured["missing"]
    contradicted = operation_acceptance.evaluate(
        operation, readback_passed=True,
        evidence={"target_identity": True,
                  "requested_camera_source_selected": False,
                  "record_span_preserved": True,
                  "unrelated_items_unchanged": True})
    assert not contradicted["passed"]
    assert "requested_camera_source_selected" in contradicted["failed"]
    proven = operation_acceptance.evaluate(
        operation, readback_passed=True,
        evidence={"target_identity": True,
                  "requested_camera_source_selected": True,
                  "record_span_preserved": True,
                  "unrelated_items_unchanged": True})
    assert proven["passed"]


def test_right_speaker_camera_uses_the_assigned_synced_source_range(
        monkeypatch):
    """Defect: live right_speaker_camera was refused by design despite a
    known speaker assignment and synced camera range."""
    adapters.register_builtin_edit_executors()
    assert ("multicam.select_angle", "resolve_axi") in set(
        resolve_axi_core.registered_edit_executors())

    from library.tools.resolve_axi_core import AxiResult

    def execute(command):
        assert command[:2] == ["edit", "switch-camera"]
        assert "--camera-source-file" in command
        assert "/media/camera_b.mov" in command
        return AxiResult(exit_code=0, stdout="camera selected", stderr="")

    monkeypatch.setattr(resolve_axi_core, "execute", execute)
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline",
                        lambda *_args: _snapshot("replacement-1"))
    receipt = adapters._multicam_select_angle(_context({
        "op": "multicam.select_angle", "unique_id": "turn-1",
        "speaker_id": "speaker-1",
        "assignment_basis": "diarized_speaker_identity",
        "source_file": "/media/camera_b.mov",
        "source_in_frame": 5, "source_out_frame": 64,
        "sync_evidence_id": "sync:camera-b:turn-1",
    }))
    assert receipt["status"] == "committed"
    assert receipt["acceptance_contracts"][0]["passed"]


def test_multicam_select_angle_revalidates_target_identity_first():
    """Defect: a stale target or unknown speaker assignment must refuse."""
    resolved = {
        "speaker_id": "speaker-1",
        "assignment_basis": "diarized_speaker_identity",
        "source_file": "/media/camera_b.mov",
        "source_in_frame": 5, "source_out_frame": 64,
        "sync_evidence_id": "sync:camera-b:turn-1",
    }
    with pytest.raises(ValueError, match="resolved to 0 timeline items"):
        adapters._multicam_select_angle(_context({
            "op": "multicam.select_angle", "unique_id": "no-such-clip",
            **resolved,
        }))
    with pytest.raises(ValueError, match="resolved speaker assignment"):
        adapters._multicam_select_angle(_context({
            "op": "multicam.select_angle", "unique_id": "turn-1",
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
