"""The edit algebra exposes evidence-backed execution status per backend."""

from library.tools import edit_algebra


def test_every_algebra_operation_has_complete_status_metadata():
    assert set(edit_algebra.OP_STATUS) == set(edit_algebra.ALGEBRA)
    for op_id, spec in edit_algebra.ALGEBRA.items():
        status = edit_algebra.status(op_id)
        assert status is not None
        assert status.id == op_id
        assert status.defined is True
        assert status.compilable == bool(status.backends)
        assert status.param_schema["type"] == "object"
        assert set(status.param_schema["required"]) == set(spec.required)
        assert status.state_domains == (spec.domain,)
        assert status.required_resolve_capabilities
        assert status.media_types
        assert status.timeline_conditions
        assert status.precondition_validator
        assert status.verification_contract
        assert status.inverse_recovery["op"] == spec.inverse.op
        for backend in status.backends:
            assert backend.compilable is True
            assert backend.production_enabled is backend.qualified
            assert backend.limitations
            assert backend.qualified == bool(
                backend.qualification_evidence and backend.resolve_version
                and backend.qualification_media_types
                and backend.qualification_timeline_conditions
                and backend.preservation_limits
                and backend.recovery_qualification != "not live-qualified")
        assert status.qualified == any(item.qualified for item in status.backends)
        assert status.production_enabled == any(
            item.production_enabled for item in status.backends)


def test_legacy_live_evidence_does_not_fill_missing_qualification_fields():
    for op_id in ("marker.add", "marker.delete"):
        for backend in ("edit_patch", "feedback_patch_apply"):
            row = edit_algebra.status(op_id, backend)
            assert row.qualification_evidence
            assert not row.production_enabled
            assert row.qualification_status == "evidence_incomplete"
            assert row.resolve_version is None
    for backend in ("reel_touchup", "composed_edit"):
        assert edit_algebra.status("clip.set_property", backend).production_enabled is False

    # Availability on a backend is not qualification on that backend.
    assert edit_algebra.status("clip.set_property", "edit_patch").compilable
    assert not edit_algebra.is_production_enabled(
        "clip.set_property", "edit_patch")
    assert not edit_algebra.is_production_enabled(
        "clip.trim", "edit_patch")
    assert edit_algebra.status("not.an.operation") is None
    assert edit_algebra.status("clip.move", "missing_backend") is None


def test_all_83_operations_have_an_audited_implementation_classification():
    assert len(edit_algebra.OP_STATUS) == 83
    assert {row.implementation_status for row in edit_algebra.OP_STATUS.values()} <= {
        "implemented_qualified", "implemented_unqualified", "workflow_only",
        "no_executor", "planning_only",
    }


def test_caption_style_stays_fail_closed_until_live_qualification():
    """Defect: a restyle with no live qualification evidence must never
    read as production-ready - the ordinary request
    "Apply the supplied caption style consistently to every caption"
    fails today on exactly this missing path."""
    entry = edit_algebra.status("caption.style", "resolve_axi")
    assert entry is not None and entry.compilable
    assert not entry.qualified
    assert not entry.production_enabled
    assert not edit_algebra.status("caption.style").qualified
    assert not edit_algebra.status("caption.style").production_enabled
    assert (edit_algebra.safety_class("caption.style")
            == edit_algebra.SAFETY_COMPLEX_LOCALIZED)
    assert edit_algebra.has_independent_readback("caption.style")
    assert edit_algebra.get("caption.style").inverse.self_inverse


def test_caption_style_verifier_judges_presence_and_timing_never_taste():
    """Defect: a snapshot judge that scored taste would invent it, and
    one that passed a lost or drifted card would commit a lie - the
    style itself is judged by the acceptance contract's rendered
    evidence, never here."""
    op = edit_algebra.get("caption.style")
    base = {"tracks": [{"type": "video", "index": 3, "name": "Subtitles",
                        "clips": [{"unique_id": "cap-1", "record_in": 10,
                                   "record_out": 40}]}]}
    assert op.verify(
        {"unique_id": "cap-1", "style": "minimal"}, base) is None
    assert "not on the timeline" in op.verify(
        {"unique_id": "gone", "style": "minimal"}, base)
    assert "must not move" in op.verify(
        {"unique_id": "cap-1", "style": "minimal",
         "record_in": 10, "record_out": 99}, base)


def test_executor_coverage_matches_the_current_dispatches():
    from library.tools import (
        edit_execution_adapters,
        edit_patch,
        feedback_patch_apply,
        reel_touchup,
        resolve_axi_core,
    )

    coverage = edit_algebra._EXECUTOR_COVERAGE
    assert coverage["edit_patch"] == set(edit_patch.OPERATIONS)
    assert coverage["feedback_patch_apply"] == set(
        feedback_patch_apply.SUPPORTED_OPS)
    touchup_handlers = {
        edit_algebra.TOUCHUP_VIEW[name]
        for name in reel_touchup._OP_HANDLERS
    }
    edit_execution_adapters.register_builtin_edit_executors()
    touchup_adapters = {
        operation_id for operation_id, backend in
        resolve_axi_core.registered_edit_executors()
        if backend == "reel_touchup"
    }
    assert coverage["reel_touchup"] == touchup_handlers | touchup_adapters
