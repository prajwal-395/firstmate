"""AXI-11: the route chosen for an executed edit is the path that ran.

Two requests failed in the AXI-11 lane with the SAME defect wearing two
faces:

* the logo placement ("add the supplied logo over the final five
  seconds") was refused under production because `clip.insert` has no
  production qualification, and the qualification-run policy selected
  `edit_patch` - a route that never executed - while the path that ran
  was the journaled AXI `edit place` verb;
* the B-roll removal ("remove the selected B-roll shot and close its
  gap") was refused under production with "runtime qualification state
  was not inspected", because the live adapter never put the MEASURED
  runtime into its routing context the way the production path has
  since PR 2893 - and the qualification-run policy again selected
  `edit_patch` while the path that ran was the AXI
  `edit track-ripple-remove` verb.

A route that names a path nobody executed records a decision that did
not happen.  These tests name that defect: the route must be the AXI
path, the measured runtime must be in the routing context, and nothing
here may mark an operation qualified - the live evidence belongs to the
qualification lane.
"""

from types import SimpleNamespace

import pytest

from library.tools import edit_algebra, execution_router
from library.tools.execution_router import RoutingRefused

PROTECTED = {
    "qualification_policy": "protected_experimental",
    "protected_staging": True,
    "preserve_approved_state": True,
    "verify_after_write": True,
}

PLACEMENT = [{
    "op": "clip.insert", "unique_id": "client-placement",
    "track_type": "video", "track_index": 3,
    "record_in": 96, "record_out": 216,
    "media_pool_item": "pool-item-7",
}]
REMOVAL = [{"op": "clip.remove", "unique_id": "cover-9"}]


def _recorded_state(op_id: str) -> dict:
    """The runtime the recorded qualification predicates describe."""
    row = edit_algebra.status(op_id).for_backend("resolve_axi")
    return {str(predicate["field"]): predicate["value"]
            for predicate in row.qualification_predicates}


def _inspected(state: dict) -> dict:
    return {"inspected": True, "state": state}


def test_the_qualification_run_route_names_the_axi_placement_path():
    """Defect: the placement ran through the journaled AXI `edit place`
    verb, and the route recorded `edit_patch` - a route nobody
    executed."""
    decision = execution_router.route_compiled_edit(
        PLACEMENT, context=PROTECTED)

    assert decision.backend == "resolve_axi"
    assert decision.mode == "staged"
    # The gap stays on the record: this route is IMPLEMENTED, not
    # qualified, and the evidence that would promote it is the live
    # qualification lane's to land.
    assert [gap[0] for gap in decision.qualification_gaps] == ["clip.insert"]


def test_the_qualification_run_route_names_the_axi_ripple_removal_path():
    """Defect: the removal ran through the AXI `edit
    track-ripple-remove` verb, and the route recorded `edit_patch`."""
    decision = execution_router.route_compiled_edit(
        REMOVAL, context=PROTECTED)

    assert decision.backend == "resolve_axi"
    assert [gap[0] for gap in decision.qualification_gaps] == ["clip.remove"]


def test_production_still_refuses_a_placement_with_no_qualification_evidence():
    """The placement has no production qualification, and nothing here
    may grant it: production routing fails closed and says so."""
    with pytest.raises(RoutingRefused, match="production routing fails closed"):
        execution_router.route_compiled_edit(PLACEMENT)


def test_production_routes_the_removal_when_the_measured_runtime_matches():
    """Defect: the removal was refused with "runtime qualification state
    was not inspected" - a statement about the code rather than about
    the timeline. With the MEASURED runtime in the context, the route is
    the AXI path."""
    decision = execution_router.route_compiled_edit(
        REMOVAL, context={
            "qualification_policy": "production",
            "qualification_state": _inspected(_recorded_state("clip.remove")),
        })

    assert decision.backend == "resolve_axi"
    assert decision.selection["qualification_state_inspected"] is True


def test_production_refuses_the_removal_when_the_measured_runtime_differs():
    """A runtime that does not satisfy the recorded predicates is not a
    production route, and the refusal names the predicate that failed."""
    state = dict(_recorded_state("clip.remove"))
    state["timeline_density"] = "dense"

    with pytest.raises(RoutingRefused, match="timeline_density"):
        execution_router.route_compiled_edit(
            REMOVAL, context={
                "qualification_policy": "production",
                "qualification_state": _inspected(state),
            })


def test_a_production_qualified_writer_elsewhere_keeps_the_route():
    """`clip.delete` is production-enabled on EditPatch, so the AXI
    adapter - which exists but is unqualified - must not displace it."""
    decision = execution_router.route_compiled_edit(
        [{"op": "clip.delete", "unique_id": "caption-3"}], context=PROTECTED)

    assert decision.backend != "resolve_axi"


def test_the_placement_has_an_implemented_axi_adapter():
    """The AXI place verb is a guarded write with a read-back, so the
    registry may name it - and its qualification is the live
    qualification lane's to land.

    The lane landed it: the AXI place verb was qualified on a Ren-owned
    scratch copy of a REAL reel in the product format (portrait
    1080x1920, 23.976fps, dense, with linked video+audio and caption
    cards), and the row carries that coverage.  What the row may NOT
    claim is a state the evidence does not cover: the predicates are the
    product format, and a landscape fixture does not satisfy them.
    """
    row = edit_algebra.status("clip.insert").for_backend("resolve_axi")

    assert row is not None
    assert row.compilable is True
    assert row.implementation_evidence
    assert row.production_enabled is True
    assert row.qualification_predicates
    assert [predicate["value"] for predicate in row.qualification_predicates
            if predicate["field"] == "project_settings"
            and predicate["operator"] == "equals"] == ["portrait_1080x1920"]


def test_the_placement_axi_adapter_is_registered_as_an_executor():
    """A route that names a path with no callable adapter records a
    decision that could not have happened."""
    from library.tools import edit_execution_adapters, resolve_axi_core

    edit_execution_adapters.register_builtin_edit_executors()

    assert ("clip.insert", "resolve_axi") in set(
        resolve_axi_core.registered_edit_executors())


def test_the_placement_adapter_writes_through_the_journaled_axi_verb(
        monkeypatch):
    """The physical write is the AXI `edit place` verb, named by the
    pool item's unique id, and the read-back is a fresh live snapshot."""
    from library.tools import edit_execution_adapters as adapters
    from library.tools import resolve_axi_core

    calls = []
    placed = {
        "unique_id": "placed-1", "record_in": 96, "record_out": 216,
        "source_in_frame": 0, "source_out_frame": 119,
        "source_file": "/media/logo.png", "track_type": "video",
        "track_index": 3,
    }
    after = {"reported_fps": 24.0, "tracks": [{
        "type": "video", "index": 3, "clips": [placed],
    }]}
    monkeypatch.setattr(resolve_axi_core, "execute", lambda command: (
        calls.append(command) or SimpleNamespace(
            ok=True, stdout="command completed\njournal:\n  entry: journal-7",
            stderr="")))
    monkeypatch.setattr(resolve_axi_core, "read_live_timeline",
                        lambda *_args: after)
    monkeypatch.setattr(adapters, "_project_folder",
                        lambda _context: "/tmp/project")

    context = SimpleNamespace(
        operation=PLACEMENT[0],
        snapshot={"reported_fps": 24.0, "tracks": []},
        request=SimpleNamespace(id="request-7", project="project-7",
                                timeline="Reel 01 - opening",
                                operations=[PLACEMENT[0]]),
    )
    receipt = adapters._place(context)

    assert calls == [[
        "edit", "place", "--clip-id", "pool-item-7",
        "--media", "video", "--track", "3", "--record", "96",
        "--project-folder", "/tmp/project", "--require-recovery",
        "--apply", "--project", "project-7", "--timeline", "Reel 01 - opening",
    ]]
    assert receipt["execution_contract"]["verification"]["status"] == "verified"
    assert receipt["target_identity"]["unique_id"] == "placed-1"


def test_the_live_adapter_routes_with_the_measured_runtime_state():
    """Defect: the live adapter never put the measured runtime into its
    routing context, so a promoted operation was refused with "runtime
    qualification state was not inspected"."""
    from tests.live import editorial_live_adapter as adapter

    backend = adapter._route_executing(
        REMOVAL, entry="resolve-axi edit track-ripple-remove",
        basis="the AXI track-scoped ripple verb executed the removal",
        qualification_state=_inspected(_recorded_state("clip.remove")))

    assert backend["router_routes"]["production"]["selected"] == "resolve_axi"
    assert backend["selection_correct"] is True


def test_the_live_adapter_route_matches_the_executed_axi_entry():
    """The route is `resolve_axi`; the executed entry is the CLI's
    `resolve-axi edit place`. The spelling differs, the path does not."""
    from tests.live import editorial_live_adapter as adapter

    backend = adapter._route_executing(
        PLACEMENT, entry="resolve-axi edit place",
        basis="the journaled AXI place verb executed the placement")

    assert backend["selected"] == "resolve_axi"
    assert backend["selection_correct"] is True


def test_the_live_adapter_still_records_the_production_refusal():
    """Both verdicts stay on the record: the production refusal for an
    unqualified placement is not hidden by the qualification-run route."""
    from tests.live import editorial_live_adapter as adapter

    backend = adapter._route_executing(
        PLACEMENT, entry="resolve-axi edit place",
        basis="the journaled AXI place verb executed the placement")

    assert backend["router_routes"]["production"]["selected"] == ""
    assert "production routing fails closed" in (
        backend["router_routes"]["production"]["refusal"])
