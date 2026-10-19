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
            assert backend.qualified == bool(backend.qualification_evidence)
        assert status.qualified == any(item.qualified for item in status.backends)
        assert status.production_enabled == any(
            item.production_enabled for item in status.backends)


def test_live_qualification_is_narrow_and_backend_queryable():
    for op_id in ("marker.add", "marker.delete"):
        assert edit_algebra.is_production_enabled(
            op_id, "edit_patch")
        assert edit_algebra.is_production_enabled(
            op_id, "feedback_patch_apply")
    assert edit_algebra.is_production_enabled(
        "clip.set_property", "reel_touchup")
    assert edit_algebra.is_production_enabled(
        "clip.set_property", "composed_edit")

    # Availability on a backend is not qualification on that backend.
    assert edit_algebra.status("clip.set_property", "edit_patch").compilable
    assert not edit_algebra.is_production_enabled(
        "clip.set_property", "edit_patch")
    assert not edit_algebra.is_production_enabled(
        "clip.trim", "edit_patch")
    assert edit_algebra.status("not.an.operation") is None
    assert edit_algebra.status("clip.move", "missing_backend") is None


def test_executor_coverage_matches_the_current_dispatches():
    from library.tools import edit_patch, feedback_patch_apply, reel_touchup

    coverage = edit_algebra._EXECUTOR_COVERAGE
    assert coverage["edit_patch"] == set(edit_patch.OPERATIONS)
    assert coverage["feedback_patch_apply"] == set(
        feedback_patch_apply.SUPPORTED_OPS)
    assert coverage["reel_touchup"] == {
        edit_algebra.TOUCHUP_VIEW[name]
        for name in reel_touchup._OP_HANDLERS
    }
