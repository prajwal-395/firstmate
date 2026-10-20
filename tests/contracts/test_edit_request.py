"""VC-07: one typed edit-request contract every backend evaluates.

Same request, different backends, same meaning: `for_backend` annotates
qualification without rewriting the operations, and `route_request`
sends the identical op list through the authoritative router. Unknown
ops, missing params and dependency cycles refuse at construction.
"""
from __future__ import annotations

import pytest

from library.tools import edit_request
from library.tools.edit_request import (
    DIRECT,
    PLAN_CHANGE,
    EditOperation,
    EditRequest,
    EditTarget,
    compile_request,
)


def _target(uid="clip-1"):
    return EditTarget(unique_id=uid, name="a.mov",
                      track_type="video", track_index=1)


def _op(op="clip.set_property", uid="clip-1", **params):
    merged = ({"key": "Pan", "value": 5.0}
              if op == "clip.set_property" else {})
    merged.update(params)
    return EditOperation(op=op, target=_target(uid), params=merged,
                         expected_effect=f"set {op} on {uid}")


def _request(*ops, **extra):
    base = {"id": "req-1", "project": "Podcast", "timeline": "Reel 09",
            "operations": list(ops)}
    base.update(extra)
    return EditRequest.from_dict(base)


def test_request_round_trips_through_dicts():
    request = _request(_op(), _op(op="clip.set_enabled", enabled=True))
    again = EditRequest.from_dict(request.to_dict())
    assert again == request


def test_unknown_algebra_op_refuses():
    with pytest.raises(edit_request.EditRequestError):
        _request(EditOperation(op="clip.fly", target=_target(), params={},
                               expected_effect="fly"))


def test_missing_required_param_refuses():
    with pytest.raises(edit_request.EditRequestError,
                       match="missing required parameters"):
        _request(EditOperation(op="clip.set_property", target=_target(),
                               params={"key": "Pan"},
                               expected_effect="set pan"))


def test_operation_without_expected_effect_refuses():
    with pytest.raises(edit_request.EditRequestError,
                       match="expected effect"):
        EditOperation(op="clip.set_enabled", target=_target(),
                      params={"enabled": True}, expected_effect="  ")


def test_dependency_cycle_refuses():
    first = EditOperation(op="marker.delete", target=None,
                          params={"frame": 10},
                          expected_effect="drop the marker",
                          depends_on=(1,))
    second = EditOperation(op="marker.add", target=None,
                           params={"frame": 10, "color": "red", "name": "m"},
                           expected_effect="add the marker",
                           depends_on=(0,))
    with pytest.raises(edit_request.EditRequestError, match="cycle"):
        _request(first, second)


def test_mixed_request_orders_on_dependencies():
    plan = EditOperation(op="overlay.add", target=None,
                         params={"row": "V3", "record_frame": 10},
                         kind=PLAN_CHANGE,
                         expected_effect="stage the overlay declaration")
    direct = EditOperation(op="clip.set_property", target=_target(),
                           params={"key": "Pan", "value": 1.0},
                           expected_effect="nudge the a-roll",
                           depends_on=(1,))
    request = EditRequest(
        id="req-mix", project="Podcast", timeline="Reel 09",
        operations=(direct, plan))
    assert request.is_mixed
    compiled = compile_request(request)
    assert compiled.order[0] == 1  # the plan change the direct op needs
    assert compiled.kinds == (PLAN_CHANGE, DIRECT)


def test_same_request_same_ops_on_every_backend():
    request = _request(_op(), _op(op="clip.set_enabled", enabled=True))
    compiled = compile_request(request)
    views = [compiled.for_backend(backend)
             for backend in ("edit_patch", "reel_touchup", "resolve_axi")]
    op_lists = [[row["op"] for row in view["operations"]] for view in views]
    assert op_lists[0] == op_lists[1] == op_lists[2]
    assert [dict(op) for op in compiled.operations] == [
        {"op": "clip.set_property", "unique_id": "clip-1",
         "track_type": "video", "track_index": 1,
         "key": "Pan", "value": 5.0},
        {"op": "clip.set_enabled", "unique_id": "clip-1",
         "track_type": "video", "track_index": 1, "enabled": True},
    ]


def test_router_refuses_unqualified_compiled_ops_without_losing_the_request():
    from library.tools import execution_router

    request = _request(_op())
    compiled = compile_request(request)
    compiled_edit, _context = compiled.to_router_input(
        {"staged": True})
    with pytest.raises(execution_router.RoutingRefused,
                       match="production routing fails closed"):
        execution_router.route_compiled_edit(
            compiled_edit, context={"staged": True})
    assert [op["op"] for op in compiled.operations] == ["clip.set_property"]


@pytest.mark.parametrize("operation", [
    EditOperation(op="clip.remove", target=EditTarget(unique_id="cover-1"),
                  params={}, expected_effect="remove the B-roll and close its gap"),
    EditOperation(
        op="broll.insert", target=EditTarget(unique_id="cover-new"),
        params={"track_type": "video", "track_index": 2,
                "record_in": 10, "record_out": 60,
                "source_in": 100, "source_out": 149,
                "clip": "broll.mp4"},
        expected_effect="cover the marked phrase while keeping dialogue"),
], ids=("remove-with-ripple", "broll-over-dialogue"))
def test_ordinary_edit_request_selects_its_qualified_resolve_axi_writer(
        operation):
    """Defect: EditPatch's compilable row shadowed the qualified AXI
    adapter, so an ordinary EditRequest never reached the semantic route
    whose runtime predicates it satisfied."""
    from library.tools import axi_qualification

    backend = "resolve_axi"
    record = axi_qualification.load_qualification_records()[(operation.op, backend)]
    state = {}
    for predicate in record["qualification_predicates"]:
        value = predicate["value"]
        state[predicate["field"]] = (
            value[0] if isinstance(value, list) else value)
    request = _request(operation)
    decision = edit_request.route_request(
        compile_request(request),
        context={"qualification_state": {"inspected": True,
                                         "state": state}})

    assert decision["backend"] == "resolve_axi"
    assert decision["selection"]["qualification_state_inspected"] is True
    assert decision["selection"]["mutation_scope"] == "targeted"
    assert decision["selection"]["transaction_ownership"]["broker_kind"] == (
        "timeline.semantic_edit")


def test_ordinary_edit_request_does_not_fall_through_when_axi_predicates_miss():
    """Defect: fixing writer precedence must not let a mismatched Resolve
    state reach EditPatch just because its implementation is compilable."""
    from library.tools import axi_qualification, execution_router

    record = axi_qualification.load_qualification_records()[
        ("clip.remove", "resolve_axi")]
    state = {predicate["field"]: (
        predicate["value"][0]
        if isinstance(predicate["value"], list)
        else predicate["value"])
        for predicate in record["qualification_predicates"]}
    state["timeline_density"] = "dense"
    request = _request(EditOperation(
        op="clip.remove", target=EditTarget(unique_id="cover-1"),
        params={}, expected_effect="remove the B-roll and close its gap"))

    with pytest.raises(execution_router.RoutingRefused,
                       match="production routing fails closed"):
        edit_request.route_request(
            compile_request(request),
            context={"qualification_state": {"inspected": True,
                                             "state": state}})


def test_touchup_spec_produces_the_request():
    request = edit_request.from_touchup_spec(
        {"reel": 9, "edits": [
            {"op": "set_properties", "unique_id": "clip-1",
             "key": "Pan", "value": 2.0,
             "expected_effect": "nudge the interview"},
        ]},
        project="Podcast", timeline="Reel 09")
    assert [op.op for op in request.operations] == ["clip.set_property"]
    assert request.originator["name"] == "reel touchup"


def test_touchup_move_maps_to_record_onto_to_frame():
    request = edit_request.from_touchup_spec(
        {"reel": 9, "edits": [
            {"op": "move", "row": "V3", "item": 0, "to_record": 48,
             "expected_effect": "slide the overlay right"}]},
        project="Podcast", timeline="Reel 09")
    (op,) = request.operations
    assert op.op == "clip.move"
    assert op.to_algebra_op()["to_frame"] == 48
    assert op.target.track_type == "video"
    assert op.target.track_index == 3
    assert op.target.position == 0


def test_touchup_set_properties_expands_each_stated_value():
    request = edit_request.from_touchup_spec(
        {"reel": 9, "edits": [
            {"op": "set_properties", "unique_id": "clip-1",
             "properties": {"Pan": 1.0, "Tilt": 2.0},
             "expected_effect": "reframe the interview"}]},
        project="Podcast", timeline="Reel 09")
    assert [(op.op, op.params["key"], op.params["value"])
            for op in request.operations] == [
        ("clip.set_property", "Pan", 1.0),
        ("clip.set_property", "Tilt", 2.0),
    ]


def test_touchup_add_row_reads_the_video_declaration():
    request = edit_request.from_touchup_spec(
        {"reel": 9, "edits": [{"op": "add_row", "name": "V9"}]},
        project="Podcast", timeline="Reel 09")
    (op,) = request.operations
    assert op.op == "track.add"
    assert op.to_algebra_op() == {
        "op": "track.add", "name": "V9", "track_type": "video"}


def test_positional_target_round_trips():
    target = EditTarget(track_type="video", track_index=3, position=0)
    assert EditTarget.from_dict(target.to_dict()) == target
    assert not target.is_stable


def test_unknown_touchup_name_refuses_instead_of_guessing():
    with pytest.raises(edit_request.EditRequestError, match="maps to nothing"):
        edit_request.from_touchup_spec(
            {"reel": 9, "edits": [{"op": "levitate"}]},
            project="Podcast", timeline="Reel 09")


def test_edit_program_keeps_its_base_pin():
    request = edit_request.from_edit_program(
        {"id": "prog-1", "reel": "Reel 09", "base_generation": 7,
         "ops": [{"op": "clip.set_enabled", "unique_id": "clip-1",
                  "enabled": False, "text": "switch the cutaway off"}]},
        project="Podcast", timeline="Reel 09")
    assert request.expected_base_generation == 7
    assert request.operations[0].expected_effect == "switch the cutaway off"


def test_axi_operations_default_to_direct_mutations():
    request = edit_request.from_axi_operations(
        [{"op": "marker.add", "frame": 12, "color": "red", "name": "beat"}],
        project="Podcast", timeline="Reel 09", base_generation=4)
    assert request.operations[0].kind == DIRECT
    assert request.expected_base_generation == 4


def test_compiled_patch_carries_computed_scope():
    base = {
        "start_frame": 0, "end_frame": 200,
        "tracks": [{"type": "video", "index": 1, "name": "V1", "clips": [
            {"unique_id": "clip-1", "name": "a.mov",
             "record_in": 0, "record_out": 48, "duration": 48,
             "transform": {"Pan": 0.0}, "enabled": True, "markers": [],
             "track_type": "video", "track_index": 1}]}],
        "markers": {"timeline": []},
    }
    request = _request(_op())
    compiled = compile_request(request, base)
    assert compiled.affected_spans == ((0, 48),)
    assert "picture_transform" in compiled.conflict_domains
    patch = compiled.to_patch("reel.touchup", base_generation=9,
                              project="Podcast", timeline="Reel 09")
    assert patch["affected_spans"] == [[0, 48]]
    assert [op["op"] for op in patch["operations"]] == ["clip.set_property"]


def test_patch_without_spans_refuses_and_names_preparation():
    request = _request(_op())
    compiled = compile_request(request)
    with pytest.raises(edit_request.EditRequestError,
                       match="preparation"):
        compiled.to_patch("reel.touchup", base_generation=9,
                          project="Podcast", timeline="Reel 09")
