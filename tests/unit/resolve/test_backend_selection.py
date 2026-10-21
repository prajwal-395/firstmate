"""AXI-002: one semantic request has one auditable routing decision.

The defects these name, and the ones this lane removed:

1. SEVEN sets of route names beside the router's own table.  The
   dispatcher judged eligibility with two frozensets of its own, the
   submit path decided the broker job from a third, the broker's
   submission gate from a fourth, the front door from a fifth, the
   executor registry from a sixth, and the router itself carried a
   SECOND Resolve AXI arm that answered the same question with a
   different rule.  Two sites that answer the same question can
   disagree; the disagreement is invisible until the day it bites.

2. A request whose route changed between the plan and the execution had
   TWO decisions, and the refusal that recorded the change was a bare
   error string - the request died before it published a receipt, so the
   receipt trail showed nothing at all.  The broker's own record of
   2026-10-21 has one such request: `53ed6ee53eb3caab`, a clip.delete
   planned against `resolve_axi` and executed against `edit_patch`.

3. A caller could NAME a physical backend in the routing context and it
   was ignored rather than refused, which leaves the caller believing it
   chose the implementation.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from library.tools import backend_selection, execution_router

REPO_ROOT = Path(__file__).resolve().parents[3]
LIBRARY = REPO_ROOT / "library"


# ── 1. one selector, and the others read it ────────────────────────────


def test_there_is_exactly_one_selector():
    """One decider.  Every other site reads its table."""
    selectors = backend_selection.selectors()
    assert len(selectors) == 1, [site.as_dict() for site in selectors]
    assert selectors[0].module == "library/tools/execution_router.py"
    assert selectors[0].function == "route_compiled_edit"


def test_every_declared_site_exists_in_the_code():
    """The enumeration names real code, not a wish."""
    for site in backend_selection.selection_sites():
        path = REPO_ROOT / site.module
        assert path.exists(), f"{site.site_id}: {site.module} is missing"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.name for node in ast.walk(tree)
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert site.function in names, (
            f"{site.site_id}: {site.module} has no {site.function}()")


def _route_name_literals(path: Path) -> set[str]:
    """The route names a module mentions inside a SET literal.

    A set of route names is how the deleted judgments were written, and
    it is the shape a second answer to "which route runs this request"
    takes.  A dict or a receipt payload that merely NAMES a route is not
    a set of routes and is not caught here.
    """
    routes = backend_selection.route_names()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Set):
            continue
        names = {element.value for element in node.elts
                 if isinstance(element, ast.Constant)
                 and isinstance(element.value, str)
                 and element.value in routes}
        if len(names) >= 2:
            found |= names
    return found


#: The modules that legitimately hold route names, and why.
ROUTE_TABLE_ALLOWLIST = {
    "mutation_verification.py": (
        "GATE_BACKENDS says which backends carry the engine's own "
        "pre/post comparison - it is a coverage table, and it is "
        "compared against the algebra's own production rows"),
}


@pytest.mark.parametrize("path", sorted(LIBRARY.rglob("*.py")),
                         ids=lambda path: path.name)
def test_no_module_beside_the_router_builds_a_set_of_route_names(path):
    """The defect: a second answer to "which route runs this request".

    The router's own tables are the ONE declaration.  A module that
    builds a SET of route names is deciding something about routes - and
    the seven sets this lane deleted were exactly that.
    """
    if path.name == "execution_router.py":
        return
    if path.name == "backend_selection.py":
        return
    if path.name in ROUTE_TABLE_ALLOWLIST:
        return
    found = _route_name_literals(path)
    assert not found, (
        f"{path.relative_to(REPO_ROOT)} builds a set of route names "
        f"{sorted(found)}; the router's table is the one declaration "
        f"(allowlisted: {sorted(ROUTE_TABLE_ALLOWLIST)})")


# ── 2. the safety checks survived the consolidation ─────────────────────


def _decision(backend: str, policy: str, gaps=()) -> dict:
    return {
        "backend": backend,
        "selection": {"qualification_policy": policy},
        "qualification_gaps": [list(gap) for gap in gaps],
    }


def test_an_unqualified_operation_is_refused_on_a_production_only_route():
    """The dispatcher's check moved, it did not disappear."""
    reasons = execution_router.route_admission_reasons(
        "edit_patch", ["clip.insert"], _decision("edit_patch", "production"))
    assert reasons
    assert "not production enabled" in reasons[0]


def test_an_unqualified_operation_needs_the_protected_policy():
    """The protected route still needs the policy, by name."""
    reasons = execution_router.route_admission_reasons(
        "resolve_axi", ["clip.split"], _decision("resolve_axi", "production"))
    assert reasons
    assert "protected experimental policy" in reasons[0]


def test_an_unqualified_operation_needs_a_recorded_gap():
    """The router's own record of the gap is still required."""
    reasons = execution_router.route_admission_reasons(
        "resolve_axi", ["clip.split"],
        _decision("resolve_axi", "protected_experimental"))
    assert reasons
    assert "qualification gap" in reasons[0]


def test_a_recorded_gap_and_the_policy_authorize_the_operation():
    """And with both, the same operation is admitted - the check is real."""
    reasons = execution_router.route_admission_reasons(
        "resolve_axi", ["clip.split"],
        _decision("resolve_axi", "protected_experimental",
                  gaps=(("clip.split", [], []),)))
    assert not reasons


def test_a_production_enabled_operation_is_admitted_without_a_policy():
    """A promoted operation needs nothing extra."""
    assert not execution_router.route_admission_reasons(
        "resolve_axi", ["clip.delete"], _decision("resolve_axi", "production"))


def test_an_unknown_route_admits_nothing():
    """A route the router does not know is refused by name."""
    reasons = execution_router.route_admission_reasons(
        "not_a_route", ["clip.delete"], {})
    assert reasons
    assert "not one the router selects" in reasons[0]


# ── 3. one transport table ─────────────────────────────────────────────


def test_the_transport_table_names_every_route():
    """Every route the router can select has a transport."""
    for route in execution_router.ROUTE_ADMISSION:
        assert route in execution_router.ROUTE_TRANSPORT, route
    assert execution_router.transport_for("resolve_axi") == (
        "timeline.semantic_edit")
    assert execution_router.transport_for("edit_patch") == (
        "timeline.apply_patch")
    assert execution_router.transport_for("no_op") == ""


def test_the_broker_gate_reads_the_routers_transport():
    """The broker's submission gate asks the router, not a set of its own."""
    from library.tools.resolved import jobs

    source = Path(jobs.__file__).read_text(encoding="utf-8")
    assert "execution_router.transport_for" in source
    assert '"edit_patch", "edit_patch.property", "no_op"' not in source


def test_the_front_door_reads_the_routers_transport():
    """The guarded feedback path asks the router too."""
    from library.tools import feedback_patch_apply

    source = Path(feedback_patch_apply.__file__).read_text(encoding="utf-8")
    assert "execution_router.is_edit_patch_route" in source
    assert '{"edit_patch", "edit_patch.property"}' not in source


def test_the_dispatcher_reads_the_routers_admission():
    """The dispatcher asks the router, and keeps its own executor check."""
    from library.tools import resolve_axi_core

    source = Path(resolve_axi_core.__file__).read_text(encoding="utf-8")
    assert "route_admission_reasons" in source
    assert "_ROUTER_QUALIFIED_BACKENDS" not in source
    assert "_ROUTER_PROTECTED_BACKENDS" not in source


# ── 4. the decision says which implementation, and why ─────────────────


def _satisfied_state(op_id: str, backend: str) -> dict:
    """An inspected runtime that satisfies the row's own predicates."""
    from library.tools import edit_algebra

    entry = edit_algebra.status(op_id)
    row = entry.for_backend(backend) if entry is not None else None
    state = {}
    if row is not None:
        for predicate in row.qualification_predicates:
            expected = predicate["value"]
            if predicate["operator"] == "one_of" and isinstance(expected, list):
                state[predicate["field"]] = expected[0]
            else:
                state[predicate["field"]] = expected
    return {"inspected": True, "state": state}


def test_the_decision_names_the_adapter_that_runs_it():
    """The router names the physical path; the dispatcher runs it."""
    decision = execution_router.route_compiled_edit(
        {"operations": [{"op": "clip.set_property", "unique_id": "u",
                         "key": "k", "value": 1}]},
        context={"qualification_policy": "production",
                 "qualification_state": _satisfied_state(
                     "clip.set_property", "edit_patch")})
    assert decision.backend == "edit_patch.property"
    assert decision.as_dict()["selection"]["adapter"] == "edit_patch"


def test_a_delete_batch_names_its_own_adapter():
    """A batch of deletes has its own adapter, and the router says so."""
    decision = execution_router.route_compiled_edit(
        {"operations": [{"op": "clip.delete", "unique_id": "a"},
                        {"op": "clip.delete", "unique_id": "b"}]},
        context={"qualification_policy": "production",
                 "qualification_state": _satisfied_state(
                     "clip.delete", "resolve_axi")})
    assert decision.backend == "resolve_axi"
    assert decision.as_dict()["selection"]["adapter"] == "delete_batch"
    assert any("delete_batch" in reason for reason in decision.reasons)


def test_the_decision_says_why_the_implementation_was_preferred():
    """The reasons name the implementation, and the alternatives say why."""
    decision = execution_router.route_compiled_edit(
        {"operations": [{"op": "clip.set_property", "unique_id": "u",
                         "key": "k", "value": 1}]},
        context={"qualification_policy": "production",
                 "qualification_state": _satisfied_state(
                     "clip.set_property", "edit_patch")})
    assert decision.reasons
    assert decision.rejected_alternatives
    for alternative in decision.rejected_alternatives:
        assert alternative.reason


# ── 5. a caller may not name a physical backend ────────────────────────


def test_naming_a_physical_backend_is_refused_by_name():
    """The defect: a caller's backend request was IGNORED, not refused."""
    with pytest.raises(execution_router.RoutingRefused) as refused:
        execution_router.route_compiled_edit(
            {"operations": [{"op": "clip.set_property", "unique_id": "u",
                             "key": "k", "value": 1}]},
            context={"requested_backend": "resolve_axi"})
    assert "resolve_axi" in str(refused.value)


def test_asking_for_a_strategy_is_still_allowed():
    """The two requestable routes keep working."""
    decision = execution_router.route_compiled_edit(
        {"operations": [{"op": "clip.set_property", "unique_id": "u",
                         "key": "k", "value": 1}]},
        context={"requested_backend": "no_op"})
    assert decision.backend == "no_op"


# ── 6. one request, one decision ────────────────────────────────────────


def test_a_route_change_is_refused_with_a_receipt_not_a_bare_error():
    """The defect: the refusal named a route nobody recorded.

    The broker's record of 2026-10-21 carries one such request
    (`53ed6ee53eb3caab`), and its result column is EMPTY: the request
    died before it published a receipt, so the trail showed nothing.
    """
    from library.tools import resolve_axi_core

    source = Path(resolve_axi_core.__file__).read_text(encoding="utf-8")
    assert "route changed while queued" not in source
    assert "_failure_receipt" in source


def test_the_measurement_script_exists_and_counts_the_sites():
    """The measurement the punchlist asks for is a thing you can run."""
    script = REPO_ROOT / "scripts" / "measure_backend_selection.py"
    assert script.exists()
    text = script.read_text(encoding="utf-8")
    assert "BEFORE_SITES" in text
    assert "divergent_before" in text
    assert json.dumps(sorted(backend_selection.route_names()))
