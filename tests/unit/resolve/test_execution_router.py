import pytest

from library.tools.execution_router import (
    IncrementalRoutingRefused, RoutingRefused, route_compiled_edit,
)
from library.tools.reel_incremental import (
    IncrementalBroken, IncrementalConflict, IncrementalStale,
    check_rows_identical,
)


def test_existing_item_property_change_routes_to_direct_patch():
    decision = route_compiled_edit([{
        "op": "clip.set_property", "unique_id": "item-1",
        "key": "ZoomX", "value": 1.25,
    }])

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
