"""AXI-42: every AXI refusal is typed, actionable and reaches the caller
as a REFUSAL, not as an unexpected error.

Defects named here, each one a thing that used to be true:

- a routing refusal was a bare ``RuntimeError``, so it escaped the AXI
  CLI as an unexpected error (traceback, exit 1) and the qualification
  gap that caused it was never stated;
- a routing refusal carried no next action, so a host had to invent one;
- a routing refusal did not carry the backend limitations that blocked
  it, so "no backend is proven" said nothing about what would have to
  change;
- an ineligibility category outside the closed set was coerced into
  ``unsupported`` - the ONE category a rebuild may follow - so a typo or
  a broken live read turned into a silent rebuild (AXI-40);
- the incremental refusal was a bare ``Exception`` whose category was
  read with a permissive default, so the same coercion happened at the
  one place a fallback actually occurs.
"""
from __future__ import annotations

import pytest

from library.tools import edit_algebra
from library.tools.execution_router import (
    ROUTING_REFUSAL_CATEGORIES,
    IncrementalRoutingRefused,
    RoutingRefused,
    route_compiled_edit,
)
from library.tools.reel_incremental import (
    IncrementalBroken,
    IncrementalConflict,
    IncrementalRefused,
    IncrementalStale,
)
from library.tools.ren_refusal import (
    REFUSAL_EXIT_CODE,
    RenRefusal,
    refusal_result_dict,
)
from library.tools.transaction_orchestration import ExecutionRefusal


def test_routing_refusal_reaches_the_caller_as_a_refusal_not_a_crash():
    """A routing refusal must be the engine-wide refusal shape."""
    assert issubclass(RoutingRefused, RenRefusal)
    assert issubclass(IncrementalRoutingRefused, RenRefusal)

    with pytest.raises(RoutingRefused) as refused:
        route_compiled_edit([{"op": "no.such.operation"}])

    payload = refusal_result_dict(refused.value)
    assert payload["ok"] is False
    assert payload["exit"] == REFUSAL_EXIT_CODE
    assert payload["what"]
    assert payload["why"]
    assert payload["fix"]
    assert payload["recovery_class"] in {
        "auto_recoverable", "needs_question", "blocked"}


def test_routing_refusal_states_the_next_action_and_the_recovery_class():
    """A refusal without a fix is the defect this vocabulary removes."""
    with pytest.raises(RoutingRefused) as refused:
        route_compiled_edit([{"op": "marker.add", "frame": 12,
                              "color": "Blue", "name": "note"}])

    assert refused.value.category == "unsupported"
    assert refused.value.fix
    assert refused.value.recovery_class == "blocked"
    assert "production" in refused.value.why


def test_routing_refusal_carries_the_recorded_backend_limitations():
    """The refusal must name what would have to change, not just fail."""
    with pytest.raises(RoutingRefused) as refused:
        route_compiled_edit([{"op": "marker.add", "frame": 12,
                              "color": "Blue", "name": "note"}])

    limitations = refused.value.limitations
    assert limitations, "a refusal must carry the limitations that blocked it"
    assert any("marker.add" in line for line in limitations)
    entry = edit_algebra.status("marker.add")
    recorded = [line for backend in entry.backends
                for line in backend.limitations]
    assert recorded
    assert any(any(line in carried for carried in limitations)
               for line in recorded)


def test_unknown_ineligibility_category_is_refused_not_coerced():
    """AXI-40: an unknown category must never become `unsupported`.

    `unsupported` is the only category a rebuild may follow, so
    coercing an unrecognised value into it is how a broken read or a
    typo becomes a silent rebuild.
    """
    with pytest.raises(RoutingRefused) as refused:
        route_compiled_edit(
            [{"op": "clip.delete", "unique_id": "item-9"}],
            context={"request_incremental": True,
                     "ineligibility_category": "not_a_category",
                     "ineligibility_reason": "typo in the category"},
        )

    assert "not_a_category" in refused.value.why
    assert refused.value.fix


@pytest.mark.parametrize("category", list(ROUTING_REFUSAL_CATEGORIES))
def test_every_known_category_is_refused_by_name_and_never_rebuilds(category):
    """A stale, conflicting or broken verdict keeps its category."""
    with pytest.raises(IncrementalRoutingRefused) as refused:
        route_compiled_edit(
            [{"op": "clip.delete", "unique_id": "item-9"}],
            context={"request_incremental": True,
                     "ineligibility_category": category,
                     "ineligibility_reason": f"plan is {category}"},
        )

    assert refused.value.category == category
    assert refused.value.fix
    assert refused.value.recovery_class in {
        "auto_recoverable", "needs_question", "blocked"}


def test_broken_is_a_category_of_its_own_and_never_rebuilds():
    """A live state nobody could read must not be rebuilt over."""
    assert "broken" in ROUTING_REFUSAL_CATEGORIES

    with pytest.raises(IncrementalRoutingRefused) as refused:
        route_compiled_edit(
            [{"op": "clip.delete", "unique_id": "item-9"}],
            context={"request_incremental": True,
                     "ineligibility_category": "broken",
                     "ineligibility_reason": "the live read failed"},
        )

    assert refused.value.category == "broken"
    assert refused.value.recovery_class == "blocked"


def test_incremental_refusal_is_typed_with_a_category_and_a_fix():
    """The incremental path must refuse in the same shape as every other."""
    assert issubclass(IncrementalRefused, RenRefusal)

    refusal = IncrementalRefused("the plan is not incrementally applicable")
    assert refusal.category == "unsupported"
    assert refusal.fix
    assert refusal.recovery_class == "auto_recoverable"

    categories = {IncrementalStale.category, IncrementalConflict.category,
                  IncrementalBroken.category}
    assert categories == {"stale", "conflict", "broken"}
    for cls in (IncrementalStale, IncrementalConflict, IncrementalBroken):
        raised = cls("live state disagrees with the plan")
        assert raised.category == cls.category
        assert raised.fix
        assert raised.code == cls.CODE
        assert raised.code != IncrementalRefused.CODE


def test_incremental_refusal_category_set_is_closed():
    """An unrecognised category must raise, not default."""
    with pytest.raises(ValueError):
        IncrementalRefused("something happened", category="whatever")


def test_execution_refusal_normalizes_a_refusal_exception():
    """One normalized value carries the category, the fix and the limits."""
    refusal = RoutingRefused(
        "no backend is proven for operations: marker.add",
        limitations=("marker.add on edit_patch: no live evidence",),
    )

    normalized = ExecutionRefusal.from_exception(refusal)
    assert normalized.category == "unsupported"
    assert normalized.reason == "no backend is proven for operations: marker.add"
    assert normalized.fix == refusal.fix
    assert normalized.limitations == refusal.limitations
    assert normalized.as_dict()["fix"] == refusal.fix


def test_execution_refusal_treats_an_untyped_error_as_a_backend_failure():
    """An exception that is not a refusal is a failure, not a decision."""
    normalized = ExecutionRefusal.from_exception(RuntimeError("boom"))

    assert normalized.category == "backend_failure"
    assert normalized.reason == "boom"


def test_axi_cli_boundary_prints_a_routing_refusal_as_a_refusal(capsys):
    """The CLI must exit 4 with the refusal, not 1 with a traceback."""
    from library.tools import resolve_axi_core_commands as commands

    def refuse(argv=None):
        raise RoutingRefused(
            "no backend is proven for operations: marker.add",
            limitations=("marker.add on edit_patch: no live evidence",),
        )

    code = commands.cli_boundary(refuse)

    captured = capsys.readouterr()
    assert code == REFUSAL_EXIT_CODE
    assert "ren: refused" in captured.err
    assert "no backend is proven" in captured.err
    assert "fix:" in captured.err


def test_axi_cli_boundary_keeps_an_unexpected_error_an_error(capsys):
    """An unexpected error keeps its traceback: a fix for a bug is a lie."""
    from library.tools import resolve_axi_core_commands as commands

    def crash(argv=None):
        raise RuntimeError("a bug, not a refusal")

    code = commands.cli_boundary(crash)

    captured = capsys.readouterr()
    assert code == 1
    assert "Traceback" in captured.err
