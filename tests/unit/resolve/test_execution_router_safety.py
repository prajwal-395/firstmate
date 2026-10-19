"""VC-11 execution safety classes: the table and its router enforcement.

Nothing goes direct merely because it is small or an inverse name exists:
direct needs the per-op proof (reversible class, independent read-back,
qualified executor, proven inverse), and no caller flag can widen a class.
"""

import pytest

from library.tools import edit_algebra
from library.tools.execution_router import RoutingRefused, route_compiled_edit


def test_every_algebra_operation_carries_exactly_one_safety_class():
    assert set(edit_algebra.OP_SAFETY_CLASS) == set(edit_algebra.ALGEBRA)
    for op_id, cls in edit_algebra.OP_SAFETY_CLASS.items():
        assert cls in edit_algebra.SAFETY_CLASSES, op_id
    assert len(edit_algebra.SAFETY_CLASSES) == 6


def test_safety_class_policies_close_the_direct_bypass():
    policy = edit_algebra.SAFETY_CLASS_POLICY
    assert set(policy) == set(edit_algebra.SAFETY_CLASSES)
    assert policy[edit_algebra.SAFETY_REVERSIBLE_MUTATION].direct_allowed is True
    for cls in (edit_algebra.SAFETY_READ_ONLY, edit_algebra.SAFETY_NO_OP,
                edit_algebra.SAFETY_COMPLEX_LOCALIZED,
                edit_algebra.SAFETY_STRUCTURAL,
                edit_algebra.SAFETY_UNREPRESENTABLE):
        assert policy[cls].direct_allowed is False, cls
    assert policy[edit_algebra.SAFETY_NO_OP].staged_allowed is False
    assert edit_algebra.SAFETY_NO_OP not in set(
        edit_algebra.OP_SAFETY_CLASS.values())


def test_declared_inverse_is_not_proof():
    # clip.set_property names its own inverse, but no qualification evidence
    # proves it on edit_patch - so the name alone authorizes nothing.
    assert edit_algebra.get("clip.set_property").inverse.self_inverse is True
    assert edit_algebra.proven_inverse("clip.set_property", "edit_patch") == ()
    assert edit_algebra.proven_inverse("marker.add", "edit_patch") != ()


def test_readback_separates_judged_writes_from_vacuous_passes():
    assert edit_algebra.has_independent_readback("marker.add") is True
    assert edit_algebra.has_independent_readback("clip.delete") is True
    assert edit_algebra.has_independent_readback("clip.link") is False
    assert edit_algebra.has_independent_readback("audio.gain") is False


def test_proven_marker_write_may_go_direct():
    assert edit_algebra.direct_blockers(("marker.add",), "edit_patch") == ()
    decision = route_compiled_edit([{
        "op": "marker.add", "frame": 12, "color": "Blue", "name": "note",
    }])
    assert decision.backend == "edit_patch"
    assert decision.mode == "direct"


def test_small_but_unproven_property_write_stays_staged():
    assert edit_algebra.direct_blockers(
        ("clip.set_enabled",), "edit_patch.property") != ()
    decision = route_compiled_edit([{
        "op": "clip.set_enabled", "unique_id": "item-1", "enabled": False,
    }])
    assert decision.backend == "edit_patch.property"
    assert decision.mode == "staged"
    assert any("direct withheld" in reason for reason in decision.reasons)


def test_structural_write_never_goes_direct():
    decision = route_compiled_edit([{"op": "clip.delete", "unique_id": "item-9"}])
    assert decision.backend == "edit_patch"
    assert decision.mode == "staged"
    assert any("direct withheld" in reason for reason in decision.reasons)


def test_staged_flag_escalates_a_proven_write_but_never_widens():
    decision = route_compiled_edit(
        [{"op": "marker.add", "frame": 1, "color": "Blue", "name": "n"}],
        context={"staged": True})
    assert decision.backend == "edit_patch"
    assert decision.mode == "staged"


def test_mixed_request_takes_the_most_restrictive_class():
    assert edit_algebra.request_safety_class(
        ("marker.add", "clip.delete")) == edit_algebra.SAFETY_STRUCTURAL
    decision = route_compiled_edit([
        {"op": "marker.add", "frame": 1, "color": "Blue", "name": "n"},
        {"op": "clip.delete", "unique_id": "item-9"},
    ])
    assert decision.mode == "staged"


@pytest.mark.parametrize("flag", ["direct", "allow_direct", "force_direct"])
def test_generic_direct_flag_is_refused_not_honored(flag):
    with pytest.raises(RoutingRefused, match="never selected by flag"):
        route_compiled_edit(
            [{"op": "marker.add", "frame": 1, "color": "Blue", "name": "n"}],
            context={flag: True})


def test_unrepresentable_operation_is_refused():
    assert (edit_algebra.safety_class("audio.pan")
            == edit_algebra.SAFETY_UNREPRESENTABLE)
    with pytest.raises(RoutingRefused, match="no qualified executor proves"):
        route_compiled_edit([{"op": "audio.pan", "unique_id": "a-1", "pan": 0.0}])


def test_plan_delivery_is_refused_on_the_write_path():
    assert (edit_algebra.safety_class("transition")
            == edit_algebra.SAFETY_READ_ONLY)
    with pytest.raises(RoutingRefused, match="plan deliveries"):
        route_compiled_edit([{"op": "transition", "carrier": "c",
                              "transition": "cut"}])


def test_unknown_operation_keeps_its_existing_refusal():
    assert edit_algebra.safety_class("not.an.operation") is None
    assert edit_algebra.request_safety_class(("not.an.operation",)) is None
    with pytest.raises(RoutingRefused, match="no backend is proven"):
        route_compiled_edit([{"op": "not.an.operation"}])
