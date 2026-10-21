"""PLAN-005: the plan is checked against the timeline BEFORE it is staged.

The defect this closes: the engine learned that a plan could not run
only at the far end of the write path.  The live unfamiliar-brief
benchmark (PR 2989) paid for that - five of eight briefs spent their
attempts inside the staging path to find out that no route existed.

These tests name the defects the check has to catch, and the two rules
it must not break:

- a check that cannot SEE reports UNDETERMINED, and undetermined never
  reads as a refusal and never reads as a pass (AGENTS.md 10.3, 10.4);
- the check never picks a backend.  The route stays the router's
  (AGENTS.md 10.5).
"""

from __future__ import annotations

import pytest

from library.tools import plan_feasibility
from library.tools.plan_feasibility import (
    FeasibilityState,
    assess,
    refusal,
)


def _operation(op: str, **params) -> dict:
    return {"op": op, **params}


def _state(**kwargs) -> FeasibilityState:
    return FeasibilityState(**kwargs)


def _findings(verdict, check: str) -> list:
    return [row for row in verdict.findings if row.check == check]


def test_an_operation_with_no_eligible_route_is_refused_before_staging():
    # Defect: the benchmark's name-bar brief planned overlay.position,
    # which has an executor and NO production-enabled backend.  The
    # engine staged a candidate and refused there.
    verdict = assess([_operation("overlay.position", unique_id="item-1",
                                 dx=4, dy=-8)])

    assert verdict.infeasible
    route = _findings(verdict, plan_feasibility.CHECK_ROUTE_ELIGIBLE)[0]
    assert route.status == plan_feasibility.INFEASIBLE
    assert "no production-enabled backend" in route.reason
    assert "reel_touchup" in route.evidence["experimental_backends"]


def test_an_operation_with_no_executor_at_all_is_refused_by_name():
    verdict = assess([_operation("not.an.operation", unique_id="item-1")])

    assert verdict.infeasible
    known = _findings(verdict, plan_feasibility.CHECK_OPERATION_KNOWN)[0]
    assert "not.an.operation" in known.reason


def test_the_protected_policy_admits_the_operations_it_names():
    # The protected experimental policy is a ROUTE the front door may
    # take; a feasibility check that refused those operations would
    # refuse the front door's own route.
    verdict = assess(
        [_operation("overlay.position", unique_id="item-1", dx=4, dy=-8)],
        state=_state(policy=plan_feasibility.PROTECTED_EXPERIMENTAL_POLICY,
                     originator="ren touch"))

    route = _findings(verdict, plan_feasibility.CHECK_ROUTE_ELIGIBLE)[0]
    assert route.status == plan_feasibility.FEASIBLE
    assert "reel_touchup" in route.evidence["backends"]


def test_a_runtime_that_was_not_read_is_undetermined_and_never_a_refusal():
    # Defect: a check that could not measure the runtime used to read
    # as a failure, which would refuse a plan for a state nobody read.
    verdict = assess(
        [_operation("broll.insert", unique_id="new-item", track_type="video",
                    track_index=2, record_in=0, record_out=48,
                    source_in=0, source_out=48, clip="a.mov")],
        state=_state(items={}))

    assert not verdict.infeasible
    assert verdict.status == plan_feasibility.UNDETERMINED
    predicates = _findings(verdict,
                           plan_feasibility.CHECK_QUALIFICATION_PREDICATES)
    assert predicates and predicates[0].status == plan_feasibility.UNDETERMINED


def _qualified_state(op_id: str, backend: str) -> dict:
    """The runtime state the recorded evidence demands, by dimension.

    A `one_of` predicate names a set; the qualified runtime answers with
    the one member the evidence was earned on.
    """
    from library.tools import axi_qualification

    records = axi_qualification.load_qualification_records()
    record = records[(op_id, backend)]
    state = {}
    for predicate in record["qualification_predicates"]:
        value = predicate["value"]
        state[predicate["field"]] = (value[0] if isinstance(value, list)
                                     else value)
    return state


def test_a_measured_runtime_that_fails_a_predicate_is_refused_by_name():
    # The predicate check reads the router's own comparison, so the two
    # cannot disagree about which field failed.
    state = _qualified_state("broll.insert", "resolve_axi")
    state["existing_effects"] = "transforms+retiming"

    verdict = assess(
        [_operation("broll.insert", unique_id="new-item", track_type="video",
                    track_index=2, record_in=0, record_out=48,
                    source_in=0, source_out=48, clip="a.mov")],
        state=_state(runtime={"inspected": True, "state": state}))

    assert verdict.infeasible
    predicates = _findings(verdict,
                           plan_feasibility.CHECK_QUALIFICATION_PREDICATES)[0]
    assert predicates.status == plan_feasibility.INFEASIBLE
    assert predicates.evidence["mismatches"]


def test_a_target_that_is_not_on_the_timeline_is_refused():
    verdict = assess(
        [_operation("caption_fix", unique_id="missing-item", text="x")],
        state=_state(items={"other-item": {"track": "V1"}}))

    assert verdict.infeasible
    identity = _findings(verdict, plan_feasibility.CHECK_TARGET_IDENTITY)[0]
    assert "missing-item" in identity.reason


def test_a_target_on_a_row_the_timeline_does_not_have_is_refused():
    verdict = assess(
        [_operation("caption_fix", unique_id="item-1", text="x")],
        state=_state(items={"item-1": {"track": "V6"}},
                     tracks=("V1", "V2")))

    assert verdict.infeasible
    presence = _findings(verdict, plan_feasibility.CHECK_TRACK_PRESENCE)[0]
    assert "V6" in presence.reason


def test_a_source_handle_that_carries_no_frames_is_refused():
    verdict = assess(
        [_operation("caption_fix", unique_id="item-1", text="x")],
        state=_state(items={"item-1": {"track": "V1", "source_in": 10,
                                       "source_out": 10}}))

    assert verdict.infeasible
    handle = _findings(verdict, plan_feasibility.CHECK_SOURCE_HANDLE)[0]
    assert "carries no frames" in handle.reason


def test_a_requested_range_is_reported_against_the_handle_not_compared():
    # Defect avoided: `clip.trim` names RECORD frames and
    # `multicam.select_angle` names another camera's source, so a
    # comparison against the target's own source handle would refuse
    # edits that are perfectly possible.
    verdict = assess(
        [_operation("clip.trim", unique_id="item-1", in_frame=0,
                    out_frame=200)],
        state=_state(items={"item-1": {"track": "V1", "source_in": 0,
                                       "source_out": 100}}),
        checks=[plan_feasibility.CHECK_SOURCE_HANDLE])

    assert not verdict.infeasible
    handle = _findings(verdict, plan_feasibility.CHECK_SOURCE_HANDLE)[0]
    assert handle.status == plan_feasibility.FEASIBLE
    assert handle.evidence["requested"] == [0, 200]


def test_a_media_kind_the_operation_does_not_support_is_refused():
    # Defect: a lower third that is a rendered .mov was moved by a route
    # that only covers a native PNG still.
    verdict = assess(
        [_operation("overlay.position", unique_id="item-1", dx=0, dy=40)],
        state=_state(items={"item-1": {"track": "V6", "media_type": "Video"}}),
        checks=[plan_feasibility.CHECK_MEDIA_PREREQUISITE,
                plan_feasibility.CHECK_ROUTE_ELIGIBLE])

    media = _findings(verdict, plan_feasibility.CHECK_MEDIA_PREREQUISITE)
    assert media and media[0].status == plan_feasibility.FEASIBLE


def test_an_item_carrying_a_state_the_operation_is_not_qualified_for_is_reported():
    # Defect avoided: the registry's `unsupported_native_state` says the
    # operation is not PRESERVATION-QUALIFIED for that state, not that it
    # cannot be applied to an item carrying it - `clip.delete` declares
    # grades and Fusion, and deleting a graded clip is its job.  A
    # refusal here would invent a restriction the router does not make.
    verdict = assess(
        [_operation("clip.trim", unique_id="item-1", in_frame=0,
                    out_frame=10)],
        state=_state(items={"item-1": {"track": "V1", "effects": ["grades"]}}),
        checks=[plan_feasibility.CHECK_EFFECT_STATE,
                plan_feasibility.CHECK_ROUTE_ELIGIBLE])

    effect = _findings(verdict, plan_feasibility.CHECK_EFFECT_STATE)[0]
    assert effect.status == plan_feasibility.FEASIBLE
    assert effect.evidence["not_preservation_qualified_for"] == ["grades"]
    assert "read-back" in effect.reason


def test_a_structural_change_with_no_reconstruction_strategy_is_refused():
    verdict = assess(
        [_operation("clip.trim", unique_id="item-1", in_frame=0,
                    out_frame=10)],
        state=_state(backend_constraints=("no_full_rebuild",)),
        checks=[plan_feasibility.CHECK_RECONSTRUCTION_STRATEGY,
                plan_feasibility.CHECK_ROUTE_ELIGIBLE])

    strategy = _findings(verdict,
                         plan_feasibility.CHECK_RECONSTRUCTION_STRATEGY)[0]
    assert strategy.status == plan_feasibility.INFEASIBLE
    assert "no supported reconstruction strategy" in strategy.reason


def test_a_refused_plan_is_revised_when_one_operation_survives():
    # The revision names what it drops and what it no longer covers; it
    # is a proposal, and the caller decides.
    verdict = assess([_operation("overlay.position", unique_id="item-1",
                                 dx=4, dy=-8),
                      _operation("broll.insert", unique_id="new-item",
                                 track_type="video", track_index=2,
                                 record_in=0, record_out=48, source_in=0,
                                 source_out=48, clip="a.mov")])

    assert verdict.infeasible
    revision = verdict.revision
    assert revision is not None
    assert revision["status"] == "proposed"
    assert revision["dropped"] == ["overlay.position"]
    assert [op["op"] for op in revision["operations"]] == ["broll.insert"]


def test_a_plan_whose_every_operation_is_refused_has_no_revision():
    verdict = assess([_operation("overlay.position", unique_id="item-1",
                                 dx=4, dy=-8),
                      _operation("caption_fix", unique_id="item-2",
                                 text="x")])

    assert verdict.infeasible
    assert verdict.revision["status"] == "rejected"
    assert verdict.revision["operations"] == []


def test_the_refusal_carries_the_next_action_and_the_reasons():
    verdict = assess([_operation("overlay.position", unique_id="item-1",
                                 dx=4, dy=-8)])
    payload = refusal(verdict)

    assert payload["category"] == "unsupported"
    assert payload["recovery_class"] == "blocked"
    assert "infeasible before staging" in payload["reason"]
    assert payload["fix"]


def test_the_check_never_chooses_a_backend():
    # AGENTS.md 10.5: the model does not pick a backend, and neither
    # does the feasibility check.
    verdict = assess([_operation("broll.insert", unique_id="new-item",
                                 track_type="video", track_index=2,
                                 record_in=0, record_out=48, source_in=0,
                                 source_out=48, clip="a.mov")])

    route = _findings(verdict, plan_feasibility.CHECK_ROUTE_ELIGIBLE)[0]
    assert set(route.evidence["backends"]) == {"resolve_axi"}
    assert "backend" not in verdict.as_dict()


def test_a_narrowed_run_reports_only_the_checks_it_ran():
    verdict = assess([_operation("overlay.position", unique_id="item-1",
                                 dx=4, dy=-8)],
                     checks=[plan_feasibility.CHECK_ROUTE_ELIGIBLE])

    assert {row.check for row in verdict.findings} == {
        plan_feasibility.CHECK_ROUTE_ELIGIBLE}


def test_the_capability_query_is_asked_whether_the_operation_may_run_here(
        monkeypatch):
    # The punch list's first question, asked in the engine's own words:
    # "may the planned operations execute on the actual timeline?".  The
    # query is the router asked directly, so its refusal is the router's.
    def _refuses(op_id, *, conditions=None, params=None):
        return {"answer": "not_supported",
                "why": ["the router refused this operation here"],
                "runtime_routing": {}}

    monkeypatch.setattr(plan_feasibility, "capability_query", _refuses)
    verdict = assess([_operation("broll.insert", unique_id="new-item",
                                 track_type="video", track_index=2,
                                 record_in=0, record_out=48, source_in=0,
                                 source_out=48, clip="a.mov")])

    assert verdict.infeasible
    query = _findings(verdict, plan_feasibility.CHECK_CAPABILITY_QUERY)[0]
    assert "the router refused this operation here" in query.reason


def test_a_capability_query_that_cannot_answer_is_undetermined(monkeypatch):
    # An uninspected state cannot satisfy a recorded predicate, and it
    # is not a refusal either.
    def _unknown(op_id, *, conditions=None, params=None):
        return {"answer": "unknown", "why": [], "runtime_routing": {}}

    monkeypatch.setattr(plan_feasibility, "capability_query", _unknown)
    verdict = assess([_operation("broll.insert", unique_id="new-item",
                                 track_type="video", track_index=2,
                                 record_in=0, record_out=48, source_in=0,
                                 source_out=48, clip="a.mov")])

    assert not verdict.infeasible
    query = _findings(verdict, plan_feasibility.CHECK_CAPABILITY_QUERY)[0]
    assert query.status == plan_feasibility.UNDETERMINED


def test_the_capability_query_is_given_the_measured_state(monkeypatch):
    seen = {}

    def _records(op_id, *, conditions=None, params=None):
        seen["conditions"] = conditions
        seen["params"] = params
        return {"answer": "supported", "why": [], "runtime_routing": {}}

    monkeypatch.setattr(plan_feasibility, "capability_query", _records)
    state = _state(runtime={"inspected": True, "state": {"frame_rate": "24"}})
    assess([_operation("broll.insert", unique_id="new-item", track_type="video",
                       track_index=2, record_in=0, record_out=48,
                       source_in=0, source_out=48, clip="a.mov")],
           state=state)

    assert seen["conditions"] == {"frame_rate": "24"}
    assert seen["params"]["op"] == "broll.insert"


def test_the_router_answers_what_it_knows_about_one_operation():
    # The seam: the router's own eligibility, read without routing, so
    # the feasibility check and the route cannot disagree.
    from library.tools import execution_router

    facts = execution_router.eligibility("overlay.position")

    assert facts["known"] is True
    assert facts["production_enabled"] is False
    assert facts["implemented_backends"] == ("reel_touchup",)
    assert facts["media_types"]
    assert execution_router.eligibility("not.an.operation")["known"] is False


def test_the_router_names_the_predicates_a_runtime_fails():
    from library.tools import execution_router

    state = _qualified_state("broll.insert", "resolve_axi")
    state["existing_effects"] = "transforms+retiming"

    mismatches = execution_router.qualification_mismatches(
        "broll.insert", {"inspected": True, "state": state})

    assert any("existing_effects" in row for row in mismatches)


def test_an_uninspected_runtime_reports_no_mismatch():
    from library.tools import execution_router

    assert execution_router.qualification_mismatches(
        "broll.insert", {"inspected": False, "reason": "not read"}) == ()


@pytest.mark.parametrize("op_id", ["overlay.position", "clip.trim",
                                   "caption_fix", "reel.opening",
                                   "multicam.select_angle"])
def test_the_benchmarks_operations_have_no_production_route(op_id):
    # The measurement's finding, asserted here so it cannot drift: the
    # operations the eight-brief benchmark planned are implemented and
    # not production-enabled, which is why the plans were refused only
    # at the end of the write path.
    from library.tools import execution_router

    facts = execution_router.eligibility(op_id)

    assert facts["known"] is True
    assert facts["compilable"] is True
    assert facts["production_enabled"] is False
    assert facts["implemented_backends"]


# ── The gate, on the path that stages ────────────────────────────────
#
# The engine's semantic edit path prepares, routes and then writes.  The
# gate sits between the preparation and the route: a plan that cannot
# execute on the timeline it names is refused there, and nothing is
# staged for it.

REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"
SNAPSHOT = {
    # A shadow snapshot is what `reel_read` measured off the timeline,
    # and it carries the timeline's own frame bounds: the edit core
    # compiles a request against the base snapshot, and a base with no
    # frame bounds is not a timeline snapshot.
    "timeline": REEL,
    "start_frame": 0,
    "end_frame": 48,
    "tracks": [{
        "type": "video", "index": 1, "name": "V1",
        "clips": [{
            "unique_id": "clip-1", "name": "card",
            "record_in": 0, "record_out": 48,
            "source_in_frame": 0, "source_out_frame": 48,
            "media_type": "Video", "source_file": "/tmp/card.mov",
        }],
    }],
    "fps": 23.976,
    "markers": {"timeline": []},
}


def _recorded_base(tmp_path, monkeypatch):
    from library.tools import timeline_shadow

    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    store = timeline_shadow.ShadowStore()
    store.record(project="Podcast (field test)", timeline_id="t1",
                 timeline_name=REEL, snapshot=SNAPSHOT,
                 source=timeline_shadow.OBSERVED, expected_head=0)
    return store


def _request(op: str, params: dict, *, originator: str = "ren revise"):
    from library.tools.edit_request import from_edit_program

    return from_edit_program(
        {"ops": [{"op": op, "params": params}],
         "reel": REEL, "base_generation": "1",
         "idempotency_key": "t1"},
        project="Podcast (field test)", timeline=REEL,
        originator={"kind": "human", "name": originator})


def test_a_plan_with_no_eligible_route_is_refused_before_anything_is_staged(
        tmp_path, monkeypatch):
    # Defect: the plan was routed and staged first, and the refusal came
    # back from the end of the write path.
    from library.tools import resolve_axi_core

    _recorded_base(tmp_path, monkeypatch)
    request = _request("clip.set_enabled",
                       {"unique_id": "clip-1", "enabled": False},
                       originator="ren touch")

    with pytest.raises(ValueError) as raised:
        resolve_axi_core._prepare_request_plan(request)

    assert "infeasible before staging" in str(raised.value)
    assert "clip.set_enabled" in str(raised.value)


def test_the_gate_admits_the_operation_the_protected_policy_names(
        tmp_path, monkeypatch):
    # The protected experimental policy is a route the front door owns;
    # a gate that refused it would refuse the front door's own route.
    from library.tools import resolve_axi_core

    _recorded_base(tmp_path, monkeypatch)
    request = _request("overlay.position",
                       {"unique_id": "clip-1", "dx": 4, "dy": -8},
                       originator="ren touch")

    plan, *_rest = resolve_axi_core._prepare_request_plan(request)

    assert plan["feasibility"]["status"] in (
        plan_feasibility.FEASIBLE, plan_feasibility.UNDETERMINED)
    assert "infeasible" not in plan["backend_reason"]
