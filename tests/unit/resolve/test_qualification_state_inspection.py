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
    _predicate_mismatches,
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
    """The refusal says WHICH condition the runtime is not in.

    The condition it names is a DIMENSION now, not a project: a
    predicate that named the fixture project restricted the operation to
    the place the evidence was earned, so a runtime in a different
    project but the same state was refused for the wrong reason.
    """
    state = _qualified_state()
    state["media_type"] = "still"
    with pytest.raises(RoutingRefused) as refusal:
        route_compiled_edit(_promoted_operation(), context={
            "qualification_state": {"inspected": True, "state": state}})
    message = str(refusal.value)
    assert "media_type is 'still'" in message
    assert "runtime qualification state was not inspected" not in message


def test_a_predicate_no_longer_names_the_project_it_was_earned_in():
    """The recorded predicates are a property of a STATE.

    A predicate that names a project or a timeline restricts an operation
    to the place the evidence was earned - which is not a qualification
    boundary, and is what the v2 evidence format refuses.
    """
    records = axi_qualification.load_qualification_records()
    for (op_id, backend), record in records.items():
        fields = {predicate["field"]
                  for predicate in record["qualification_predicates"]}
        assert "project_name" not in fields, (op_id, backend)
        assert "timeline_name" not in fields, (op_id, backend)


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

    class _Project:
        def GetName(self):
            return "Ren Qualification"

    class _Resolve:
        def GetVersionString(self):
            return "21.1.0.14"

    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda timeline: _fake_tracks(*_fake_rows()))
    context = commands._qualification_context(
        _Resolve(), _Project(), _fake_timeline(),
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


def _fake_tracks(*rows):
    """The row shape `reel_read.read_tracks` produces."""
    return [{"type": "video", "index": 1, "name": "Video 1",
             "clips": list(rows)}]


def _fake_timeline(name="fm-scratch qualify"):
    """A timeline that answers the settings the measurement reads."""

    class _Timeline:
        def GetName(self):
            return name

        def GetSetting(self, key):
            return {"timelineFrameRate": "24",
                    "timelineResolutionWidth": "1920",
                    "timelineResolutionHeight": "1080"}.get(key)

        def GetTrackCount(self, track_type):
            return 0

    return _Timeline()


def test_the_measurement_describes_the_runtime_it_read(monkeypatch):
    """The measurement speaks the vocabulary the predicates are written in."""
    from library.tools import axi_qualification_state

    rows = _fake_rows()

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
        timeline=_fake_timeline(),
        operations=[{"op": PROMOTED_OP, "unique_id": "item-1"}])
    assert state["inspected"] is True
    assert state["state"]["resolve_version"] == "DaVinci Resolve 21.1.0.14"

    # The vocabulary, measured from the rows.
    assert axi_qualification_state._media_type(rows, []) == "video"
    assert axi_qualification_state._clip_composition(rows) == \
        "single_video_clip"
    assert axi_qualification_state._linked_audio(
        rows, {"item-1": rows[0]}) == "no_linked_audio"
    assert axi_qualification_state._timeline_structure(_fake_tracks(*rows)) \
        == "single_item_timeline"


def test_the_measurement_reports_every_dimension_a_predicate_may_name(
        monkeypatch):
    """A predicate never compares against a field the runtime did not read.

    The dimensions are the ONE enumeration the predicates are written
    in, so the measurement has to answer every one of them - and a
    dimension that cannot be read refuses rather than reporting a
    default as though it had been measured.
    """
    from library.tools import axi_qualification_dimensions as dimensions
    from library.tools import axi_qualification_state

    class _Project:
        def GetName(self):
            return "Ren Qualification"

    class _Resolve:
        def GetVersionString(self):
            return "DaVinci Resolve 21.1.0.14"

    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda timeline: _fake_tracks(*_fake_rows()))
    state = axi_qualification_state.inspect_handles(
        resolve=_Resolve(), resolve_project=_Project(),
        timeline=_fake_timeline(),
        operations=[{"op": PROMOTED_OP, "unique_id": "item-1"}])

    assert state["inspected"] is True
    assert set(state["state"]) == set(dimensions.DIMENSIONS)


def test_an_unreadable_setting_refuses_the_measurement(monkeypatch):
    """A frame rate the timeline did not say is not a frame rate."""
    from library.tools import axi_qualification_state

    class _Timeline:
        def GetName(self):
            return "fm-scratch qualify"

        def GetSetting(self, key):
            return None

        def GetTrackCount(self, track_type):
            return 0

    class _Project:
        def GetName(self):
            return "Ren Qualification"

    class _Resolve:
        def GetVersionString(self):
            # What Resolve ACTUALLY returns. The stub used to answer
            # "DaVinci Resolve 21.1.0.14", which no Resolve has ever
            # answered, and the fiction is why the version predicate
            # looked satisfiable in a unit test and refused every
            # promoted edit in the live run.
            return "21.1.0.14"

    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks", lambda timeline: [])
    state = axi_qualification_state.inspect_handles(
        resolve=_Resolve(), resolve_project=_Project(),
        timeline=_Timeline(),
        operations=[{"op": PROMOTED_OP, "unique_id": "item-1"}])

    assert state["inspected"] is False
    assert "timelineFrameRate" in state["reason"]


def test_the_measured_fixture_satisfies_the_recorded_predicates(monkeypatch):
    """The qualification fixture, measured, IS the qualified runtime.

    This is the load-bearing claim: the measurement produces the values
    the recorded evidence demands, so a promoted operation routes on the
    timeline the evidence was earned on.
    """
    from library.tools import axi_qualification_state

    class _Project:
        def GetName(self):
            return "Ren Qualification"

    class _Resolve:
        def GetVersionString(self):
            return "21.1.0.14"

    rows = _fake_rows()
    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda timeline: _fake_tracks(*rows))
    measured = axi_qualification_state.inspect_handles(
        resolve=_Resolve(), resolve_project=_Project(),
        timeline=_fake_timeline(),
        operations=[{"op": PROMOTED_OP, "unique_id": "item-1"}])

    assert measured["inspected"] is True
    for op_id, backend in (("clip.delete", "edit_patch"),
                            ("clip.set_property", "edit_patch")):
        row = edit_algebra.status(op_id).for_backend(backend)
        assert row is not None and row.production_enabled
        assert _qualification_matches(row, measured), (op_id, measured)


def test_a_recorded_predicate_a_runtime_cannot_satisfy_is_a_defect():
    """The general form of the defect the live run measured.

    A production-enabled operation carries the CONDITIONS its evidence was
    earned under, and the runtime is compared against them. A condition
    written in a notation the runtime never reports can never be
    satisfied, so the operation is refused on every timeline - including
    the one the evidence was earned on.

    Measured 2026-10-20: every promoted operation (`clip.delete`,
    `clip.set_property`) was refused in production routing with
    "resolve_version is '21.1.0.14', not 'DaVinci Resolve 21.1.0.14'" -
    a statement about two notations and not about the timeline. The
    version now has ONE reading, so the comparison is version against
    version.
    """
    records = axi_qualification.load_qualification_records()
    assert records, "the registry carries no evidence to check"

    for (op_id, backend), record in records.items():
        row = edit_algebra.status(op_id).for_backend(backend)
        assert row is not None and row.production_enabled, (op_id, backend)
        version = record["resolve_version"]
        assert version, (op_id, backend)
        for predicate in record["qualification_predicates"]:
            if predicate["field"] != "resolve_version":
                continue
            values = predicate["value"]
            values = values if isinstance(values, list) else [values]
            for value in values:
                # The recorded condition is written in the canonical
                # reading, so the runtime's own reading can satisfy it.
                assert value == axi_qualification.canonical_resolve_version(
                    value), (op_id, backend, value)
                # And the canonical reading of the evidence's own version
                # is that value: the record and its condition agree.
                assert value == axi_qualification.canonical_resolve_version(
                    version), (op_id, backend, value, version)


def test_the_version_has_one_reading():
    """Resolve reports a bare version; the label is decoration."""
    canonical = axi_qualification.canonical_resolve_version

    assert canonical("21.1.0.14") == "21.1.0.14"
    assert canonical("DaVinci Resolve 21.1.0.14") == "21.1.0.14"
    assert canonical("DaVinci Resolve Studio 21.1.0.14") == "21.1.0.14"
    assert canonical("  DaVinci  Resolve  21.1.0.14 ") == "21.1.0.14"
    assert canonical("") == ""
    assert canonical(None) == ""


def test_the_state_the_live_run_measured_now_satisfies_the_recorded_conditions():
    """The 2026-10-20 live run's own measurement, replayed.

    The generalization matrix refused 15 of its 20 cases, and every one
    of them with the same line: "no production-qualified executor is
    available for: clip.delete; production routing fails closed
    (clip.delete: runtime state does not satisfy recorded qualification
    predicates: resolve_version is '21.1.0.14', not 'DaVinci Resolve
    21.1.0.14'; media_type is 'video + audio', not one of ['video'];
    timeline_str...)".

    This is that measurement, as the refusal printed it, replayed against
    the recorded conditions. The conditions the evidence was earned under
    have to be satisfiable by the runtime the evidence was earned on.

    The conditions are DIMENSIONS now, so the measurement is replayed in
    the dimension vocabulary - the same measurement, read the way the
    router reads it.
    """
    measured = {
        "resolve_version": "21.1.0.14",
        "media_type": "video",
        "clip_composition": "linked_video_clip",
        "linked_audio_configuration": "linked_audio_present",
        "track_layout": "video+audio",
        "track_occupancy": "mixed",
        "timeline_density": "light",
        "timeline_structure": "multi_item_timeline",
        "project_settings": "landscape_1920x1080",
        "frame_rate": "24",
        "existing_effects": "none",
    }
    inspected = {"inspected": True, "state": measured}

    for op_id, backend in (("clip.delete", "edit_patch"),
                            ("clip.set_property", "edit_patch")):
        row = edit_algebra.status(op_id).for_backend(backend)
        assert row is not None and row.production_enabled, (op_id, backend)
        assert _qualification_matches(row, inspected), (
            op_id, _predicate_mismatches([row], inspected))
        assert _predicate_mismatches([row], inspected) == ()


def test_the_measurement_reads_the_version_in_the_one_reading(monkeypatch):
    """The measurement is where the version enters the system.

    Resolve's answer is read in the reading the recorded conditions are
    written in, so the comparison the router makes is version against
    version - whatever notation Resolve happens to use.
    """
    from library.tools import axi_qualification_state

    class _Project:
        def GetName(self):
            return "Ren Qualification"

    class _Resolve:
        def GetVersionString(self):
            return "DaVinci Resolve 21.1.0.14"

    monkeypatch.setattr(
        "library.tools.reel_read.read_tracks",
        lambda timeline: _fake_tracks(*_fake_rows()))
    measured = axi_qualification_state.inspect_handles(
        resolve=_Resolve(), resolve_project=_Project(),
        timeline=_fake_timeline(),
        operations=[{"op": PROMOTED_OP, "unique_id": "item-1"}])

    assert measured["state"]["resolve_version"] == "21.1.0.14"
    row = edit_algebra.status("clip.delete").for_backend("edit_patch")
    assert _qualification_matches(row, measured)


def test_a_timeline_with_more_than_the_seeded_clip_is_its_own_structure():
    """A second item is not an empty baseline, so it says so."""
    from library.tools import axi_qualification_state

    rows = _fake_rows(extra=1)
    assert axi_qualification_state._timeline_structure(_fake_tracks(*rows)) \
        == "multi_item_timeline"
    assert axi_qualification_state._timeline_structure([]) == \
        "empty_timeline"


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

