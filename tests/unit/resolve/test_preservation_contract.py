"""AGENT-012: preservation requirements and backend selection are part
of the planning contract.

Two things a plan may say about an edit had nowhere to go.

**Preservation.** "Do not change the music bed", "keep the approved
captions", "that marker must survive" - the requester had no field for
it, so the promise was either dropped or lived in the prose of a note.
What existed instead was a per-OP acceptance list derived from the op's
id (`operation_acceptance.contract_for`), which is the engine's guess
about what matters, not the requester's statement.

**Backend selection.** The router already chose the backend, and the
model was already forbidden to choose it (the co-editor skill says "Do
not choose a Resolve execution backend in the editorial plan"). What was
missing was the other half: the plan could not STATE a requirement the
route has to satisfy - "this must not be a full rebuild" - so the only
way to keep a rebuild from happening was to not ask for the edit at all.

These tests name the defects:

- the plan carries the preservation requirements and the route
  constraints, and an unknown name in either is REFUSED rather than
  ignored - a declaration nothing honours is not a declaration;
- a preservation requirement is judged over the state the edit was
  authorized against and the state it left behind, and one that cannot
  be judged is UNPROVABLE, never a pass;
- a route constraint may only NARROW: a route that breaks one is
  refused by name, with the routes the constraint admits beside it;
- the constraints reach the router's own evidence, so a receipt says
  which constraints the route satisfied.
"""
from __future__ import annotations

import pytest

from library.tools import (
    edit_patch,
    execution_router,
    preservation_invariant,
)
from library.tools.edit_request import (
    EditOperation,
    EditRequest,
    EditRequestError,
    EditTarget,
)


def _op(**kwargs):
    defaults = {"op": "clip.trim", "target": EditTarget(unique_id="clip-1"),
                "params": {"in_frame": 1, "out_frame": 2},
                "expected_effect": "trim the clip"}
    defaults.update(kwargs)
    return EditOperation(**defaults)


def _request(**kwargs):
    defaults = {"id": "req-1", "project": "Podcast", "timeline": "Reel 09",
                "operations": (_op(),)}
    defaults.update(kwargs)
    return EditRequest(**defaults)


def test_the_plan_carries_the_preservation_requirements():
    request = _request(
        preservation=({"kind": "row_unchanged", "track_type": "video",
                       "track_index": 2},))
    payload = request.to_dict()
    assert payload["preservation"] == [
        {"kind": "row_unchanged", "track_type": "video", "track_index": 2}]
    assert EditRequest.from_dict(payload).preservation == request.preservation


def test_an_operation_carries_its_own_preservation_requirements():
    operation = _op(preservation=({"kind": "clip_unchanged",
                                   "unique_id": "clip-1"},))
    payload = operation.to_dict()
    assert payload["preservation"] == [{"kind": "clip_unchanged",
                                        "unique_id": "clip-1"}]
    assert EditOperation.from_dict(payload).preservation == \
        operation.preservation


def test_an_unknown_preservation_requirement_is_refused():
    # Defect: a requirement the engine cannot judge is a promise nobody
    # is keeping, so it is refused when the plan is read.
    with pytest.raises(EditRequestError):
        _request(preservation=({"kind": "the_music_bed_must_survive"},))


def test_a_preservation_requirement_without_its_arguments_is_refused():
    with pytest.raises(EditRequestError):
        _request(preservation=({"kind": "row_unchanged"},))


def test_an_unknown_route_constraint_is_refused():
    with pytest.raises(EditRequestError):
        _request(backend_constraints=("please_do_a_rebuild",))


def test_the_plan_carries_the_route_constraints():
    request = _request(backend_constraints=("no_full_rebuild",))
    payload = request.to_dict()
    assert payload["backend_constraints"] == ["no_full_rebuild"]
    assert EditRequest.from_dict(payload).backend_constraints == \
        request.backend_constraints


def test_a_preservation_requirement_is_judged_over_the_two_snapshots():
    before = {"tracks": [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"unique_id": "a", "name": "A", "record_in": 0, "record_out": 100,
         "source_in_frame": 10, "source_out_frame": 110,
         "source_file": "/m/a.mov"}]}],
        "markers": {"notes": []}}
    after = {"tracks": [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"unique_id": "a", "name": "A", "record_in": 0, "record_out": 100,
         "source_in_frame": 10, "source_out_frame": 110,
         "source_file": "/m/a.mov"},
        {"unique_id": "b", "name": "B", "record_in": 100, "record_out": 200,
         "source_in_frame": 120, "source_out_frame": 220,
         "source_file": "/m/b.mov"}]}],
        "markers": {"notes": []}}
    results = preservation_invariant.judge_requirements(
        [{"kind": "clip_unchanged", "unique_id": "a"},
         {"kind": "row_unchanged", "track_type": "video", "track_index": 1}],
        before=before, after=after)
    assert results[0]["status"] == preservation_invariant.PRESERVED
    assert results[1]["status"] == preservation_invariant.VIOLATED


def test_a_requirement_that_cannot_be_judged_is_unprovable_not_a_pass():
    before = {"tracks": [], "markers": {"notes": []}}
    after = {"tracks": [], "markers": {"notes": []}}
    results = preservation_invariant.judge_requirements(
        [{"kind": "clip_unchanged", "unique_id": "never-was-there"}],
        before=before, after=after)
    assert results[0]["status"] == preservation_invariant.UNPROVABLE


def test_a_route_constraint_may_only_narrow():
    # Defect: the plan could not say "this must not be a full rebuild",
    # so the only way to keep one from happening was not to ask.
    assert execution_router._constraint_violation(
        "full_rebuild", ("no_full_rebuild",)) == "no_full_rebuild"
    assert execution_router._constraint_violation(
        "edit_patch", ("no_full_rebuild",)) == ""
    assert execution_router._constraint_violation(
        "reel_touchup", ("direct_patch_only",)) == "direct_patch_only"
    assert execution_router._constraint_violation(
        "edit_patch", ("direct_patch_only",)) == ""


def test_a_constraint_the_router_does_not_know_is_a_violation():
    # A constraint nobody reads is a declaration nothing honours.
    assert execution_router._constraint_violation(
        "edit_patch", ("please_do_a_rebuild",)) == "please_do_a_rebuild"


def test_a_route_that_breaks_a_constraint_is_refused_by_name():
    with pytest.raises(execution_router.RoutingRefused) as refused:
        execution_router.route_compiled_edit(
            [{"op": "reel.full_build"}],
            context={"requested_backend": "full_rebuild",
                     "reason": "the plan says so",
                     "ineligibility_reason": "unsupported: complex edit",
                     "backend_constraints": ("no_full_rebuild",)})
    assert "no_full_rebuild" in refused.value.what
    assert "edit_patch" in refused.value.fix


def test_the_constraints_reach_the_routers_own_evidence():
    decision = execution_router.route_compiled_edit(
        [{"op": "reel.full_build"}],
        context={"requested_backend": "full_rebuild",
                 "reason": "the plan says so",
                 "ineligibility_reason": "unsupported: complex edit",
                 "backend_constraints": ("staged_only",)})
    assert decision.backend == "full_rebuild"
    assert decision.selection["backend_constraints"] == ["staged_only"]


def test_the_patch_carries_the_preservation_requirements():
    patch = edit_patch.EditPatch.from_dict({
        "id": "patch-1", "project": "Podcast", "timeline": "Reel 09",
        "base_generation": 3, "capability": "reel.touchup",
        "affected_spans": [[0, 48]], "conflict_domains": ["timeline_structure"],
        "operations": [{"op": "clip.trim", "unique_id": "clip-1",
                        "in_frame": 1, "out_frame": 2}],
        "preservation": [{"kind": "clip_unchanged", "unique_id": "clip-1"}]})
    assert patch.preservation == ({"kind": "clip_unchanged",
                                   "unique_id": "clip-1"},)
    assert patch.to_dict()["preservation"] == [
        {"kind": "clip_unchanged", "unique_id": "clip-1"}]
