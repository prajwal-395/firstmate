"""AXI-39 / AXI-41: the routing decision says what it will disturb and
who owns the write.

Defects named here, each one a thing that used to be true:

- the decision recorded which backend was chosen and nothing about WHAT
  that backend does to the timeline, so a receipt could not answer "was
  the rest of the reel preserved?" - the question AXI-39 asks;
- a full rebuild recorded the targeted route as "not requested or not
  eligible" even when the targeted route WAS eligible, so a receipt
  misstated why the timeline was reconstructed instead of patched;
- the decision carried no lease, no broker and no transaction owner, so
  "all Resolve mutations respect the single-instance broker and
  transaction ownership" was a property of the code path that ran rather
  than of the decision that chose it - invisible to every reader of the
  receipt;
- a route the router does not know about borrowed no ownership at all,
  silently, instead of saying it declares none.
"""
from __future__ import annotations

from library.tools.execution_router import (
    ROUTE_MUTATION_SCOPE,
    ROUTE_TRANSACTION_OWNERSHIP,
    STRATEGY_FULL_BUILD_ID,
    STRATEGY_INCREMENTAL_ID,
    route_compiled_edit,
    route_structural_candidate,
)

#: The guarded context the protected routes need.
GUARDS = {"qualification_policy": "protected_experimental",
          "protected_staging": True,
          "preserve_approved_state": True,
          "verify_after_write": True}


def _reachable_backends() -> set[str]:
    """Every backend the router can select through its public entry."""
    backends = {
        route_compiled_edit(
            [{"op": "clip.set_property", "unique_id": "i", "key": "ZoomX",
              "value": 1.1}], context=dict(GUARDS)).backend,
        route_compiled_edit(
            [{"op": "clip.delete", "unique_id": "i"}],
            context=dict(GUARDS)).backend,
        route_compiled_edit(
            [{"op": "clip.trim", "unique_id": "i", "in_frame": 1,
              "out_frame": 2},
             {"op": "clip.insert", "unique_id": "n", "track_type": "video",
              "track_index": 1, "record_in": 2, "record_out": 3,
              "media_pool_item": "p"}], context=dict(GUARDS)).backend,
        route_compiled_edit(
            [{"op": STRATEGY_FULL_BUILD_ID}],
            context={"requested_backend": "full_rebuild"}).backend,
        route_compiled_edit(
            [{"op": STRATEGY_INCREMENTAL_ID}],
            context={"incremental_eligibility": True}).backend,
        route_compiled_edit(
            [{"op": "clip.set_property", "unique_id": "i", "key": "ZoomX",
              "value": 1.1}], context={"already_satisfied": True}).backend,
    }
    return backends


def test_every_route_the_router_can_select_declares_its_scope():
    """A decision that cannot say what it disturbs says nothing."""
    for backend in _reachable_backends():
        assert backend in ROUTE_MUTATION_SCOPE, backend
        assert ROUTE_MUTATION_SCOPE[backend] in {
            "none", "targeted", "full_rebuild"}


def test_every_route_the_router_can_select_declares_its_ownership():
    """Every write route says who owns it and under which lease."""
    for backend in _reachable_backends():
        assert backend in ROUTE_TRANSACTION_OWNERSHIP, backend
        ownership = ROUTE_TRANSACTION_OWNERSHIP[backend]
        assert ownership["single_instance"] is True
        assert ownership["enforced_by"] == "library/tools/resolve_lock.py"
        if ownership["writes"]:
            assert ownership["lease"] == "exclusive"


def test_a_route_that_writes_demands_the_exclusive_lease():
    """AXI-41: a write under a shared lease is not a single writer."""
    decision = route_compiled_edit(
        [{"op": "clip.delete", "unique_id": "i"}], context=dict(GUARDS))

    ownership = decision.as_dict()["selection"]["transaction_ownership"]
    assert ownership["lease"] == "exclusive"
    assert ownership["writes"] is True
    assert ownership["single_instance"] is True
    assert ownership["broker_kind"] == "timeline.apply_patch"


def test_a_no_op_route_writes_nothing_and_owns_nothing():
    decision = route_compiled_edit(
        [{"op": "clip.set_property", "unique_id": "i", "key": "ZoomX",
          "value": 1.1}], context={"already_satisfied": True})

    selection = decision.as_dict()["selection"]
    assert selection["mutation_scope"] == "none"
    assert selection["transaction_ownership"]["writes"] is False
    assert selection["transaction_ownership"]["lease"] == "none"


def test_the_decision_says_whether_it_reconstructs_the_timeline():
    """AXI-39: a targeted route and a rebuild are not the same edit."""
    targeted = route_compiled_edit(
        [{"op": "clip.set_property", "unique_id": "i", "key": "ZoomX",
          "value": 1.1}], context=dict(GUARDS))
    rebuild = route_compiled_edit(
        [{"op": STRATEGY_FULL_BUILD_ID}],
        context={"requested_backend": "full_rebuild",
                 "reason": "the footage changed"})

    assert targeted.as_dict()["selection"]["mutation_scope"] == "targeted"
    assert rebuild.as_dict()["selection"]["mutation_scope"] == "full_rebuild"


def test_a_rebuild_over_an_eligible_targeted_route_says_so():
    """The receipt must not claim the targeted route was ineligible."""
    decision = route_compiled_edit(
        [{"op": STRATEGY_FULL_BUILD_ID}],
        context={"requested_backend": "full_rebuild",
                 "reason": "the operator asked for a rebuild",
                 "incremental_eligibility": True})

    rejected = decision.rejected_alternatives[0]
    assert rejected.backend == "incremental"
    assert "was eligible" in rejected.reason
    assert "not eligible" not in rejected.reason
    assert "the operator asked for a rebuild" in rejected.reason


def test_a_rebuild_without_an_eligibility_result_still_states_its_reason():
    decision = route_compiled_edit(
        [{"op": STRATEGY_FULL_BUILD_ID}],
        context={"requested_backend": "full_rebuild",
                 "reason": "the operator asked for a rebuild"})

    rejected = decision.rejected_alternatives[0]
    assert rejected.reason == "not requested or not eligible"


def test_an_unknown_route_declares_no_ownership_rather_than_borrowing_one():
    """A route the router does not know must not claim another's lease."""
    from library.tools.execution_router import _ownership_for

    ownership = _ownership_for("a.route.nobody.knows")
    assert ownership["lease"] == "unknown"
    assert ownership["writes"] is None
    assert ownership["single_instance"] is True


def test_the_structural_candidate_declares_the_scope_it_chose():
    """The observation-only structural route is a targeted one."""
    decision = route_structural_candidate(
        operations=[{"op": "clip.insert", "unique_id": "n",
                     "track_type": "video", "track_index": 1,
                     "record_in": 2, "record_out": 3,
                     "media_pool_item": "p"}],
        context={"structural_patch": {"full_reconstruction_required": False},
                 "native_effects_proven": True,
                 "structural_executor_backend": "composed_edit"})

    selection = decision.as_dict()["selection"]
    assert selection["mutation_scope"] in {"targeted", "full_rebuild"}
    assert selection["transaction_ownership"]["lease"] == "exclusive"
    assert selection["transaction_ownership"]["single_instance"] is True


def test_the_structural_route_is_owned_by_the_semantic_commit_adapter():
    """The structural route runs under the typed semantic commit."""
    ownership = ROUTE_TRANSACTION_OWNERSHIP["composed_edit.structural"]
    assert ownership["broker_kind"] == "timeline.semantic_edit"
    assert ownership["lease"] == "exclusive"
    assert ownership["writes"] is True


def test_a_reel_build_record_carries_the_scope_and_the_ownership():
    """A build record answers the same two questions as a router decision."""
    from library.tools import reel_execution_contract

    record = reel_execution_contract.publish({
        "timeline_name": "Reel 1",
        "placement_check": {"passed": True, "repair_attempted": False},
        "stream_enforcement": {"checked": 0, "deleted": [], "unverified": []},
    }, backend="full_rebuild")

    assert record["mutation_scope"] == "full_rebuild"
    assert record["transaction_ownership"]["lease"] == "exclusive"
    assert record["transaction_ownership"]["single_instance"] is True

    incremental = reel_execution_contract.publish({
        "timeline_name": "Reel 1",
        "placement_check": {"passed": True, "repair_attempted": False},
        "stream_enforcement": {"checked": 0, "deleted": [], "unverified": []},
    }, backend="incremental")

    assert incremental["mutation_scope"] == "targeted"
