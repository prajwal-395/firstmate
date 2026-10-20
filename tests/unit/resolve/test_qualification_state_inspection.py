"""The production path must MEASURE the runtime before it routes a
promoted operation.

A break test on 2026-10-20 measured that through `ren revise` every
destructive edit was refused with

    "no production-qualified executor is available for: clip.delete;
     production routing fails closed (clip.delete: runtime qualification
     state was not inspected)"

and the reason was about the CODE, not about the timeline. `clip.delete`,
`clip.set_property` and `track.add` were promoted to production from live
evidence, and every production path asked the router to route them
WITHOUT a `qualification_state` - so the router could not check the
recorded qualification predicates at all, and refused with a statement
about a missing inspection.

These tests name that failure and its fix:

* the router still refuses a promoted operation when nobody looked, and
  says so (the fail-closed default is not weakened);
* the production planner and the front door's classifier now MEASURE the
  runtime and pass it, so a promoted operation routes when the runtime
  matches the recorded predicates;
* a runtime that does not match is refused with the SPECIFIC predicate
  that failed, not with "the state was not inspected";
* a runtime that could not be read is refused, and is never reported as
  inspected.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from library.tools import axi_qualification, edit_algebra  # noqa: E402
from library.tools.execution_router import (  # noqa: E402
    RoutingRefused,
    _qualification_matches,
    route_compiled_edit,
)

#: The operation the break test named, and the backend its live evidence
#: promoted it on.
PROMOTED_OP = "clip.set_property"
PROMOTED_BACKEND = "edit_patch"


def _recorded_predicates(op_id: str = PROMOTED_OP,
                         backend: str = PROMOTED_BACKEND) -> dict:
    """The predicates the recorded live evidence earned promotion on."""
    records = axi_qualification.load_qualification_records()
    record = records[(op_id, backend)]
    return {predicate["field"]: predicate["value"]
            for predicate in record["qualification_predicates"]}


def _qualified_state(op_id: str = PROMOTED_OP,
                     backend: str = PROMOTED_BACKEND) -> dict:
    """The runtime state the recorded predicates are satisfied by.

    A `one_of` predicate names a set; the qualified runtime answers with
    the one member the evidence was earned on.
    """
    state = {}
    for field, value in _recorded_predicates(op_id, backend).items():
        state[field] = value[0] if isinstance(value, list) else value
    return state


def _promoted_operation() -> list[dict]:
    return [{"op": PROMOTED_OP, "unique_id": "item-1",
             "key": "ZoomX", "value": 1.25}]


# ── The router's own contract ───────────────────────────────────────


def test_a_promoted_operation_is_refused_when_nobody_looked():
    """The fail-closed default: no inspection, no production route."""
    with pytest.raises(RoutingRefused) as refusal:
        route_compiled_edit(_promoted_operation())
    assert "runtime qualification state was not inspected" in str(refusal.value)


def test_a_measured_runtime_that_matches_routes_the_promoted_operation():
    decision = route_compiled_edit(_promoted_operation(), context={
        "qualification_state": {"inspected": True,
                                "state": _qualified_state()}})
    assert decision.backend == "edit_patch.property"
    assert decision.as_dict()["selection"]["qualification_state_inspected"] is True


def test_a_measured_runtime_that_does_not_match_names_the_predicate():
    """The refusal says WHICH condition the runtime is not in."""
    state = _qualified_state()
    state["project_name"] = "Ren Scratch - breaktest"
    with pytest.raises(RoutingRefused) as refusal:
        route_compiled_edit(_promoted_operation(), context={
            "qualification_state": {"inspected": True, "state": state}})
    message = str(refusal.value)
    assert "project_name is 'Ren Scratch - breaktest'" in message
    assert "not 'Ren Qualification'" in message
    assert "runtime qualification state was not inspected" not in message


def test_an_unreadable_runtime_is_refused_and_says_why():
    with pytest.raises(RoutingRefused) as refusal:
        route_compiled_edit(_promoted_operation(), context={
            "qualification_state": {
                "inspected": False,
                "reason": "Resolve has no project open"}})
    assert "Resolve has no project open" in str(refusal.value)


# ── The production path measures it ─────────────────────────────────


def test_the_axI_planner_measures_the_runtime_before_it_routes(monkeypatch):
    """The defect the break test showed: the planner never looked.

    `resolve_axi_core._prepare_request_plan` builds the routing context
    for every semantic request - `ren revise` and the broker's AXI
    execute path both go through it - so this is where the runtime has
    to be measured.
    """
    from library.tools import axi_qualification_state, resolve_axi_core

    measured = {"inspected": True, "state": _qualified_state()}
    seen = {}

    def inspect(**kwargs):
        seen.update(kwargs)
        return measured

    monkeypatch.setattr(axi_qualification_state, "inspect", inspect)

    request = SimpleNamespace(
        project="Ren Qualification",
        timeline="fm-scratch qualify",
        originator={"kind": "human", "name": "ren revise"})

    operations = _promoted_operation()
    context = resolve_axi_core._route_context(
        request, (PROMOTED_OP,), operations)
    assert seen["project"] == "Ren Qualification"
    assert seen["timeline"] == "fm-scratch qualify"
    assert context["qualification_state"]["inspected"] is True
    decision = route_compiled_edit(operations, context=context)
    assert decision.backend == "edit_patch.property"


def test_the_front_door_classifier_measures_the_runtime_before_it_refuses(
        monkeypatch):
    """`ren revise`'s own classifier routes the same program the same way.

    The classifier and the AXI planner must agree; the classifier asked
    the router for the PRODUCTION policy, so without a measured state it
    refused every promoted operation before the planner was ever asked.
    """
    from library.tools import axi_qualification_state, feedback_patch

    monkeypatch.setattr(
        axi_qualification_state, "inspect",
        lambda **kwargs: {"inspected": True, "state": _qualified_state()})

    note = {
        "basis": "edit_spec",
        "outcome": "unrouted",
        "steps": [], "frame": None,
        "evidence": {
            "edit_spec_ops": [{"op": PROMOTED_OP, "owner": None,
                               "ledger_op": "edit_program",
                               "unique_id": "item-1", "key": "ZoomX",
                               "value": 1.25}],
            "originator": "ren revise",
            "resolve_project": "Ren Qualification",
            "timeline": "fm-scratch qualify",
        },
    }
    verdict = feedback_patch.decide(note)
    assert verdict["verdict"] == feedback_patch.VERDICT_PATCH
    assert verdict["execution"]["backend"] == "edit_patch.property"


def test_a_caller_that_cannot_say_what_to_inspect_gets_no_state():
    """No project, no timeline, no inspection - and no pretending."""
    from library.tools import axi_qualification_state

    context = axi_qualification_state.with_runtime_state(
        {"require_qualified": True}, project="", timeline="")
    assert "qualification_state" not in context


def test_an_operation_that_was_never_promoted_is_not_measured(monkeypatch):
    """The predicates are what a PROMOTION is conditional on.

    An operation with no production-enabled row is refused either way, so
    the measurement would cost a Resolve read and say nothing.
    """
    from library.tools import axi_qualification_state

    read = []
    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda timeline: read.append(timeline) or [])
    context = axi_qualification_state.with_runtime_state(
        {"require_qualified": True}, project="Ren Qualification",
        timeline="fm-scratch qualify",
        operations=[{"op": "clip.trim", "unique_id": "item-1"}])
    assert "qualification_state" not in context
    assert read == []


def test_the_axi_command_dispatch_measures_the_runtime(monkeypatch):
    """`resolve-axi edit delete` and its siblings route through the router.

    The AXI verbs dispatch their operations with NO context, so the
    router saw the production policy with no measured state and refused
    every promoted operation - the refusal the live request matrix
    reported as the one remaining cause.
    """
    from library.tools import resolve_axi_core_commands as commands

    class _Timeline:
        def GetName(self):
            return "fm-scratch qualify"

    class _Project:
        def GetName(self):
            return "Ren Qualification"

    class _Resolve:
        def GetVersionString(self):
            return "DaVinci Resolve 21.1.0.14"

    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda timeline: [{"type": "video", "index": 1, "name": "Video 1",
                           "clips": [{"unique_id": "item-1",
                                      "media_type": "Video",
                                      "linked_unique_ids": []}]}])
    context = commands._qualification_context(
        _Resolve(), _Project(), _Timeline(),
        [{"op": "clip.delete", "unique_id": "item-1"}])
    assert context["qualification_state"]["inspected"] is True
    decision = route_compiled_edit(
        [{"op": "clip.delete", "unique_id": "item-1"}], context=context)
    assert decision.backend == "edit_patch"


# ── The measurement itself ──────────────────────────────────────────


def _fake_rows(*, media_type: str = "Video", linked=(), extra: int = 0):
    rows = [{
        "unique_id": "item-1",
        "media_type": media_type,
        "linked_unique_ids": list(linked),
        "track_type": "video",
        "track_index": 1,
    }]
    for index in range(extra):
        rows.append({"unique_id": f"item-{index + 2}",
                     "media_type": media_type,
                     "linked_unique_ids": [],
                     "track_type": "video", "track_index": 1})
    return rows


def test_the_measurement_describes_the_runtime_it_read(monkeypatch):
    """The measurement speaks the vocabulary the predicates are written in."""
    from library.tools import axi_qualification_state

    rows = _fake_rows()

    class _Timeline:
        def GetName(self):
            return "fm-scratch qualify"

    class _Project:
        def GetName(self):
            return "Ren Qualification"

    class _Resolve:
        def GetVersionString(self):
            return "DaVinci Resolve 21.1.0.14"

    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks", lambda timeline: [])
    state = axi_qualification_state.inspect_handles(
        resolve=_Resolve(), resolve_project=_Project(),
        timeline=_Timeline(),
        operations=[{"op": PROMOTED_OP, "unique_id": "item-1"}])
    assert state["inspected"] is True
    assert state["state"]["resolve_version"] == "DaVinci Resolve 21.1.0.14"
    assert state["state"]["project_name"] == "Ren Qualification"
    assert state["state"]["timeline_name"] == "fm-scratch qualify"

    # The vocabulary, measured from the rows.
    assert axi_qualification_state._media_type(rows, []) == "video"
    assert axi_qualification_state._clip_composition(rows) == \
        "single_video_clip"
    assert axi_qualification_state._linked_audio(
        rows, {"item-1": rows[0]}) == "no_linked_audio"
    assert axi_qualification_state._timeline_structure(rows) == \
        "single_seeded_clip_empty_baseline"


def test_the_measured_fixture_satisfies_the_recorded_predicates(monkeypatch):
    """The qualification fixture, measured, IS the qualified runtime.

    This is the load-bearing claim: the measurement produces the values
    the recorded evidence demands, so a promoted operation routes on the
    timeline the evidence was earned on.
    """
    from library.tools import axi_qualification_state

    class _Timeline:
        def GetName(self):
            return "fm-scratch qualify"

    class _Project:
        def GetName(self):
            return "Ren Qualification"

    class _Resolve:
        def GetVersionString(self):
            return "DaVinci Resolve 21.1.0.14"

    rows = _fake_rows()
    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda timeline: [{"type": "video", "index": 1, "name": "Video 1",
                           "clips": rows}])
    measured = axi_qualification_state.inspect_handles(
        resolve=_Resolve(), resolve_project=_Project(),
        timeline=_Timeline(),
        operations=[{"op": PROMOTED_OP, "unique_id": "item-1"}])

    assert measured["inspected"] is True
    for op_id, backend in (("clip.delete", "edit_patch"),
                            ("clip.set_property", "edit_patch")):
        row = edit_algebra.status(op_id).for_backend(backend)
        assert row is not None and row.production_enabled
        assert _qualification_matches(row, measured), (op_id, measured)


def test_a_timeline_with_more_than_the_seeded_clip_is_its_own_structure():
    """A second item is not an empty baseline, so it says so."""
    from library.tools import axi_qualification_state

    rows = _fake_rows(extra=1)
    assert axi_qualification_state._timeline_structure(rows) == \
        "multi_item_timeline"
    assert axi_qualification_state._timeline_structure([]) == "empty_fixture"


def test_a_clip_with_linked_audio_says_so():
    from library.tools import axi_qualification_state

    linked = {"unique_id": "audio-1", "media_type": "Audio",
              "linked_unique_ids": [], "track_type": "audio",
              "track_index": 1}
    rows = _fake_rows(linked=["audio-1"])
    by_id = {"item-1": rows[0], "audio-1": linked}
    assert axi_qualification_state._linked_audio(rows, by_id) == \
        "linked_audio_present"
    assert axi_qualification_state._clip_composition(rows) == \
        "linked_video_clip"
