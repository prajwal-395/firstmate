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
    assert entry is not None
    assert not entry.production_enabled
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        route_compiled_edit([{
            "op": "marker.add", "frame": 12,
            "color": "Blue", "name": "note",
        }])
    assert entry.for_backend("edit_patch").qualification_status == (
        "evidence_incomplete")


def test_router_refuses_unqualified_production_route():
    entry = edit_algebra.status("marker.add")
    assert entry is not None
    assert not entry.production_enabled
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        route_compiled_edit([{"op": "marker.add", "frame": 12,
                              "color": "Blue", "name": "note"}])


@pytest.mark.parametrize("operations", [
    [
        {"op": "clip.trim", "unique_id": "clip-1",
         "in_frame": 10, "out_frame": 20},
        {"op": "clip.insert", "unique_id": "new-clip",
         "track_type": "video", "track_index": 1,
         "record_in": 20, "record_out": 30,
         "media_pool_item": "source-clip"},
    ],
    [
        {"op": "clip.replace", "unique_id": "clip-1",
         "source_file": "/media/replacement.mov"},
        {"op": "caption.style", "unique_id": "caption-1",
         "style": "minimal"},
    ],
], ids=("trim-plus-insert", "replace-plus-restyle"))
def test_unqualified_compound_requests_remain_refused_before_execution(
        operations):
    """Defect: mixed requests must not run across unqualified writers."""
    with pytest.raises(RoutingRefused,
                       match="production routing fails closed"):
        route_compiled_edit(operations)


@pytest.mark.parametrize("operations", [
    [
        {"op": "clip.trim", "unique_id": "clip-1",
         "in_frame": 10, "out_frame": 20},
        {"op": "clip.insert", "unique_id": "new-clip",
         "track_type": "video", "track_index": 1,
         "record_in": 20, "record_out": 30,
         "media_pool_item": "pool-item"},
    ],
    [
        {"op": "clip.replace", "unique_id": "clip-1",
         "source_file": "/media/replacement.mov"},
        {"op": "caption.style", "unique_id": "caption-1",
         "style": "minimal"},
    ],
], ids=("trim-plus-insert", "replace-plus-restyle"))
def test_protected_compound_routes_to_one_staging_transaction(operations):
    decision = route_compiled_edit(operations, context={
        "qualification_policy": "protected_experimental",
        "protected_staging": True,
        "preserve_approved_state": True,
        "verify_after_write": True,
    })

    assert decision.backend == "reel_touchup"
    assert decision.mode == "staged"
    assert "production routing remains closed pending live evidence" in " ".join(
        decision.reasons)
    assert decision.as_dict()["selection"]["qualification_policy"] == (
        "protected_experimental")


def test_production_route_requires_inspected_state_matching_fixture_predicates():
    operation = [{"op": "clip.set_property", "unique_id": "item-1",
                  "key": "ZoomX", "value": 1.25}]
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        route_compiled_edit(operation)

    state = {"resolve_version": "21.1.0.14",
             "project_name": "Ren Qualification",
             "timeline_name": "fm-scratch qualify",
             "media_type": "video", "clip_composition": "single_video_clip",
             "linked_audio_configuration": "no_linked_audio",
             "timeline_structure": "single_seeded_clip_empty_baseline"}
    decision = route_compiled_edit(operation, context={
        "qualification_state": {"inspected": True, "state": state}})
    assert decision.backend == "edit_patch.property"
    assert decision.as_dict()["selection"]["qualification_state_inspected"] is True

    state["clip_composition"] = "still_clip"
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        route_compiled_edit(operation, context={
            "qualification_state": {"inspected": True, "state": state}})


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
    assert decision.qualification_gaps == (("clip.delete", ("edit_patch",), ()),)
    assert any("protected experimental policy" in reason
               for reason in decision.reasons)


@pytest.mark.parametrize("operation", [
    {"op": "audio.gain", "unique_id": "audio-1", "db": -3.0},
    {"op": "audio.fade", "unique_id": "audio-1",
     "fade_in": 0.2, "fade_out": 0.3},
])
def test_audio_touchup_route_is_protected_and_staged(operation):
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        route_compiled_edit([operation])

    decision = route_compiled_edit([operation], context={
        "qualification_policy": "protected_experimental",
        "protected_staging": True,
        "preserve_approved_state": True,
        "verify_after_write": True,
    })

    assert decision.backend == "reel_touchup"
    assert decision.mode == "staged"
    assert decision.qualification_gaps == ((operation["op"], (), ()),)
    assert decision.as_dict()["selection"]["qualification_policy"] == (
        "protected_experimental")


def test_audio_compiled_request_keeps_router_policy_explicit():
    from library.tools.edit_request import CompiledEdit

    compiled = CompiledEdit(
        request_id="audio-request", order=(0,),
        operations=({"op": "audio.fade", "unique_id": "audio-1",
                    "fade_in": 0.2, "fade_out": 0.3},),
        affected_spans=((10, 20),), conflict_domains=("audio_mix",),
        kinds=("audio.fade",),
    )
    operations, context = compiled.to_router_input()

    assert context == {}
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        route_compiled_edit(operations, context=context)
    unrelated = CompiledEdit(
        request_id="property-request", order=(0,),
        operations=({"op": "clip.delete", "unique_id": "clip-1"},),
        affected_spans=((10, 20),), conflict_domains=("structure",),
        kinds=("clip.delete",),
    )
    _, unrelated_context = unrelated.to_router_input()
    assert unrelated_context == {}


def test_transition_adapter_uses_protected_route_when_registered():
    status = edit_algebra.status("transition")
    row = status.for_backend("reel_touchup") if status else None
    if (row is None or not row.compilable
            or edit_algebra.safety_class("transition")
            == edit_algebra.SAFETY_READ_ONLY):
        pytest.skip("exact-cut touch-up adapter is not on this base yet")

    decision = route_compiled_edit([{
        "op": "transition", "carrier": {"outgoing_unique_id": "a",
                                           "incoming_unique_id": "b",
                                           "cut_frame": 120},
        "transition": {"type": "Cross Dissolve", "duration_frames": 12},
    }], context={
        "qualification_policy": "protected_experimental",
        "protected_staging": True,
        "preserve_approved_state": True,
        "verify_after_write": True,
    })

    assert decision.backend == "reel_touchup"
    assert decision.mode == "staged"
    assert decision.qualification_gaps == (("transition", (), ()),)


def test_strict_policy_refuses_unqualified_route():
    entry = edit_algebra.status("marker.add")
    assert entry is not None
    assert not entry.production_enabled
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        route_compiled_edit([{"op": "marker.add", "frame": 12,
                              "color": "Blue", "name": "note"}],
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
