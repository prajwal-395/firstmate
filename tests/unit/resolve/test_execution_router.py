import pytest

from library.tools import edit_algebra
from library.tools.execution_router import (
    IncrementalRoutingRefused,
    RoutingGates,
    RoutingRefused,
    observe_with_gates,
    reset_gate_observations,
    route_compiled_edit,
    route_full_rebuild,
    route_incremental,
    route_no_op,
)
from library.tools.reel_incremental import (
    IncrementalBroken,
    IncrementalConflict,
    IncrementalStale,
    check_rows_identical,
)


def test_existing_item_property_change_needs_protected_experimental_policy():
    decision = route_compiled_edit([{
        "op": "clip.set_property", "unique_id": "item-1",
        "key": "ZoomX", "value": 1.25,
    }], context={"qualification_policy": "protected_experimental",
                 "protected_staging": True,
                 "preserve_approved_state": True,
                 "verify_after_write": True})

    assert decision.backend == "edit_patch.property"
    assert "full_rebuild" in {
        rejected.backend for rejected in decision.rejected_alternatives
    }
    assert any("must not reconstruct" in rejected.reason
               for rejected in decision.rejected_alternatives)
    assert decision.verification


def test_incremental_route_requires_positive_eligibility():
    decision = route_compiled_edit(
        [{"op": "not_an_algebra_op"}],
        context={"incremental_eligibility": True},
    )

    assert decision.backend == "incremental"
    assert decision.algebra_status == (("not_an_algebra_op", "unknown"),)


def test_incremental_refusal_names_reason_and_does_not_select_rebuild():
    with pytest.raises(IncrementalRoutingRefused,
                       match="incremental stale: stale recorded base") as refused:
        route_compiled_edit(
            [{"op": "not_an_algebra_op"}],
            context={"request_incremental": True,
                     "ineligibility_category": "stale",
                     "ineligibility_reason": "stale recorded base"},
        )
    assert refused.value.category == "stale"


def test_unsupported_incremental_can_select_explicit_rebuild_with_reason():
    decision = route_compiled_edit(
        [{"op": "reel.full_build"}],
        context={"request_incremental": True, "allow_full_rebuild": True,
                 "ineligibility_reason": "complex edit"},
    )

    assert decision.backend == "full_rebuild"
    assert decision.rejected_alternatives[0].reason == "unsupported: complex edit"


def test_explicit_rebuild_decision_records_incremental_rejection():
    decision = route_compiled_edit(
        [{"op": "reel.full_build"}],
        context={"requested_backend": "full_rebuild",
                 "reason": "incremental plan unsupported",
                 "ineligibility_reason": "unsupported: complex edit"},
    )

    assert decision.backend == "full_rebuild"
    assert decision.reasons == ("incremental plan unsupported",)
    assert decision.rejected_alternatives[0].reason == "unsupported: complex edit"


def test_unknown_operation_does_not_guess_a_backend():
    with pytest.raises(RoutingRefused, match="no backend is proven"):
        route_compiled_edit([{"op": "unknown.operation"}])


def test_incremental_live_inventory_mismatch_is_a_conflict():
    with pytest.raises(IncrementalConflict):
        check_rows_identical({("video", 1): [(1, 4, "a.mov")]},
                             {("video", 1): [(1, 4, "b.mov")]},
                             "footage")


def test_incremental_refusal_classes_remain_distinct():
    assert len({IncrementalStale.category, IncrementalConflict.category,
                IncrementalBroken.category}) == 3


def test_router_reports_matrix_qualification_not_function_existence():
    entry = edit_algebra.status("marker.add")
    if entry is None or not entry.production_enabled:
        pytest.skip("matrix changed: marker.add lost production status")
    decision = route_compiled_edit([{
        "op": "marker.add", "frame": 12, "color": "Blue", "name": "note",
    }])

    assert decision.backend == "edit_patch"
    assert decision.algebra_status == (("marker.add", "production_enabled"),)
    assert decision.qualification_gaps == ()


def test_router_refuses_unqualified_production_route():
    entry = edit_algebra.status("clip.delete")
    if entry is None or entry.production_enabled:
        pytest.skip("matrix changed: clip.delete gained production status")
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        route_compiled_edit([{"op": "clip.delete", "unique_id": "item-9"}])


def test_protected_experimental_route_requires_all_guarantees():
    operation = [{"op": "clip.delete", "unique_id": "item-9"}]
    with pytest.raises(RoutingRefused, match="requires explicit true guarantees"):
        route_compiled_edit(operation, context={
            "qualification_policy": "protected_experimental"})
    decision = route_compiled_edit(operation, context={
        "qualification_policy": "protected_experimental",
        "protected_staging": True,
        "preserve_approved_state": True,
        "verify_after_write": True,
    })
    assert decision.backend == "edit_patch"
    assert decision.qualification_gaps == (("clip.delete", (), ()),)
    assert any("protected experimental policy" in reason
               for reason in decision.reasons)


def test_strict_policy_refuses_unqualified_route():
    entry = edit_algebra.status("clip.delete")
    if entry is None or entry.production_enabled:
        pytest.skip("matrix changed: clip.delete gained production status")
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        route_compiled_edit([{"op": "clip.delete", "unique_id": "item-9"}],
                            context={"require_qualified": True})


def test_explicit_full_rebuild_never_tries_incremental():
    decision = route_compiled_edit(
        [{"op": "clip.set_property", "unique_id": "item-1",
          "key": "ZoomX", "value": 1.25}],
        context={"incremental_eligibility": True,
                 "requested_backend": "full_rebuild",
                 "reason": "operator demanded a rebuild",
                 "qualification_policy": "protected_experimental",
                 "protected_staging": True,
                 "preserve_approved_state": True,
                 "verify_after_write": True},
    )

    assert decision.backend == "full_rebuild"
    assert decision.mode == "staged_full_rebuild"


def test_no_op_outcome_writes_nothing():
    decision = route_compiled_edit(
        [{"op": "clip.set_property", "unique_id": "item-1",
          "key": "ZoomX", "value": 1.25}],
        context={"already_satisfied": True,
                 "reason": "property already reads 1.25"},
    )

    assert decision.backend == "no_op"
    assert decision.mode == "no_op"
    assert "no Resolve write issued" in decision.verification


def test_staged_context_names_staged_mode():
    decision = route_compiled_edit(
        [{"op": "clip.set_property", "unique_id": "item-1",
          "key": "ZoomX", "value": 1.25}],
        context={"staged": True,
                 "qualification_policy": "protected_experimental",
                 "protected_staging": True,
                 "preserve_approved_state": True,
                 "verify_after_write": True},
    )

    assert decision.backend == "edit_patch.property"
    assert decision.mode == "staged"


def test_missing_required_parameter_refuses_before_selection():
    with pytest.raises(RoutingRefused, match="missing required parameters"):
        route_compiled_edit([{"op": "clip.delete"}])


def test_media_type_mismatch_refuses_before_selection():
    with pytest.raises(RoutingRefused, match="does not support media type"):
        route_compiled_edit(
            [{"op": "marker.add", "frame": 3, "color": "Blue", "name": "n"}],
            context={"media_type": "audio"})


@pytest.mark.parametrize("category", ["unsupported", "stale", "conflict", "backend_failure"])
def test_refusal_categories_stay_distinct_and_never_rebuild(category):
    with pytest.raises(IncrementalRoutingRefused) as refused:
        route_compiled_edit(
            [{"op": "clip.delete", "unique_id": "item-9"}],
            context={"request_incremental": True,
                      "ineligibility_category": category,
                      "ineligibility_reason": f"plan is {category}"},
        )
    assert refused.value.category == category


def test_strategy_entry_points_name_modes():
    assert route_full_rebuild(reason="explicit").mode == "staged_full_rebuild"
    assert route_incremental().mode == "staged"
    assert route_no_op(reason="satisfied").backend == "no_op"


def test_observation_gates_keep_production_authoritative():
    reset_gate_observations()
    try:
        gates = RoutingGates(name="probe", incremental_candidate_ops=("clip.delete",))
        outcome = observe_with_gates(
            [{"op": "clip.delete", "unique_id": "item-9"}], gates=gates,
            context={"qualification_policy": "protected_experimental",
                     "protected_staging": True,
                     "preserve_approved_state": True,
                     "verify_after_write": True})

        assert outcome["production"].backend == "edit_patch"
        assert outcome["candidate"].backend == "incremental"
        assert outcome["agree"] is False

        second = observe_with_gates(
            [{"op": "clip.delete", "unique_id": "item-9"}],
            gates=RoutingGates(name="empty"),
            context={"qualification_policy": "protected_experimental",
                     "protected_staging": True,
                     "preserve_approved_state": True,
                     "verify_after_write": True})
        assert second["agree"] is True
        assert second["candidate"].backend == "edit_patch"
    finally:
        assert reset_gate_observations() == 2
