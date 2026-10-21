"""The operations the live benchmark found with an executor and NO route.

The unfamiliar-brief benchmark (PR 2989, `docs/benchmarks/2026-10-20-
unfamiliar-brief-acceptance.md`) measured, on real footage:

    `reel.opening`, `clip.trim` (alone), `multicam.select_angle`,
    `broll.insert`, `caption_fix`: refused by the router under the
    production policy, and not covered by the protected experimental
    policy either ... A lone `clip.trim` and a lone `clip.replace` are
    unroutable, so "tighten the opening" and "swap this clip's source"
    have no route at all.

Each test below names one of those refusals. None of them may be
answered by promoting an operation to production: the live evidence
that would qualify them belongs to the live qualification lane.
"""

from __future__ import annotations

import pytest

from library.tools import (
    edit_algebra,
    edit_qualification_policy,
    edit_request,
    execution_router,
    feedback_patch,
)

#: The operations the benchmark found with an executor and no route.
UNROUTABLE = ("reel.opening", "clip.trim", "clip.replace",
              "multicam.select_angle", "broll.insert", "caption_fix")

#: One flat algebra operation per id, with the parameters that operation
#: declares. The values are placeholders: routing reads the operation's
#: shape, not the footage.
OPERATIONS = {
    "reel.opening": {
        "op": "reel.opening", "unique_id": "clip-1", "brief": "tighten it",
        "edits": [{"op": "clip.trim", "unique_id": "clip-1",
                   "in_frame": 0, "out_frame": 24}],
    },
    "clip.trim": {"op": "clip.trim", "unique_id": "clip-1",
                  "in_frame": 0, "out_frame": 24},
    "clip.replace": {"op": "clip.replace", "unique_id": "clip-1",
                     "source_file": "/media/other.mov"},
    "multicam.select_angle": {
        "op": "multicam.select_angle", "unique_id": "clip-1",
        "speaker_id": "speaker-1",
        "assignment_basis": "diarized_speaker_identity",
        "source_file": "/media/camera_b.mov", "source_in_frame": 5,
        "source_out_frame": 64, "sync_evidence_id": "sync:camera-b:turn-1",
    },
    "broll.insert": {
        "op": "broll.insert", "unique_id": "clip-1", "track_type": "video",
        "track_index": 2, "record_in": 0, "record_out": 24,
        "source_in": 0, "source_out": 24, "clip": "/media/broll.mov",
    },
    "caption_fix": {"op": "caption_fix", "unique_id": "clip-1",
                    "text": "the caption it should read"},
}


def _protected(op_id: str) -> dict:
    context = edit_qualification_policy.protected_experimental_context(
        [op_id], originator_name="ren revise")
    assert context is not None, (
        f"{op_id} is covered by no protected policy, so it has no route "
        "at all")
    return context


# ── 1. The policy names every operation that has an executor ──────────


@pytest.mark.parametrize("op_id", UNROUTABLE)
def test_the_benchmark_operations_are_covered_by_the_protected_policy(op_id):
    """Catches: an operation with an implemented executor that the
    protected policy does not name. The router refused it under the
    production policy, so it had no route at all."""
    assert op_id in edit_qualification_policy.PROTECTED_EXPERIMENTAL_SINGLE_OPS
    context = edit_qualification_policy.protected_experimental_context(
        [op_id], originator_name="ren revise")
    assert context is not None
    assert context["qualification_policy"] == "protected_experimental"
    for name in edit_qualification_policy.PROTECTED_EXPERIMENTAL_GUARANTEES:
        assert context[name] is True


@pytest.mark.parametrize("op_id", UNROUTABLE)
def test_the_policy_is_not_granted_to_another_originator(op_id):
    """Catches: the protected route widened to a front door that does
    not own the staging guarantee."""
    assert edit_qualification_policy.protected_experimental_context(
        [op_id], originator_name="somebody else") is None


def test_the_policy_refuses_an_operation_with_no_executor(monkeypatch):
    """Catches: the policy naming a vocabulary entry that nothing runs.
    A protected route may only name a path that exists."""
    monkeypatch.setattr(
        edit_qualification_policy, "PROTECTED_EXPERIMENTAL_SINGLE_OPS",
        frozenset({"multicam.angle"}))
    assert not edit_qualification_policy.has_implemented_executor(
        "multicam.angle")
    assert edit_qualification_policy.protected_experimental_context(
        ["multicam.angle"], originator_name="ren revise") is None


@pytest.mark.parametrize("op_id", UNROUTABLE)
def test_every_covered_operation_has_an_implemented_executor(op_id):
    """Catches: the policy granting a route to an operation no
    executor runs."""
    assert edit_qualification_policy.has_implemented_executor(op_id)


# ── 2. The router routes every one of them ───────────────────────────


@pytest.mark.parametrize("op_id", UNROUTABLE)
def test_each_covered_operation_routes_to_an_executor(op_id):
    """Catches: the operation covered by the policy and still refused
    by the router, so the policy named a route that did not exist."""
    decision = execution_router.route_compiled_edit(
        {"operations": [dict(OPERATIONS[op_id])]},
        context=_protected(op_id))
    assert decision.backend in {"resolve_axi", "edit_patch", "reel_touchup"}
    assert decision.mode == "staged"


@pytest.mark.parametrize("op_id", UNROUTABLE)
def test_production_routing_still_refuses_every_covered_operation(op_id):
    """Catches: an operation promoted to production to make it
    routable. The live evidence that would qualify it belongs to the
    live qualification lane, and nothing here grants it."""
    with pytest.raises(execution_router.RoutingRefused):
        execution_router.route_compiled_edit(
            {"operations": [dict(OPERATIONS[op_id])]},
            context={"require_qualified": True})


#: The operations the live qualification lane has since qualified, with
#: the live evidence that earned it.  They stay in the protected
#: policy's vocabulary - the policy is what a REQUEST may use - but the
#: registry now carries their own production row, and this test is no
#: longer the thing that says they are unqualified.
QUALIFIED_BY_THE_LIVE_LANE = {
    "broll.insert": "the product-format qualification of the AXI place "
                     "verb on a real reel (portrait 1080x1920, 23.976fps, "
                     "dense, linked video+audio, caption cards)",
    "clip.replace": "the product-format qualification of the EditPatch "
                    "source replacement on a real reel",
    "reel.opening": "the live reel.opening qualification on a Ren-owned "
                    "scratch copy in the product's delivery format "
                    "(portrait 1080x1920, 23.976fps): the decided "
                    "opening tighten committed, the trimmed clip read "
                    "back at its declared span, every other item "
                    "unchanged",
}


@pytest.mark.parametrize("op_id", [op for op in UNROUTABLE
                                   if op not in QUALIFIED_BY_THE_LIVE_LANE])
def test_no_covered_operation_is_production_enabled(op_id):
    """Catches: the qualification status edited to "production" so the
    protected route could be called a production one. Registering a
    route is not a qualification."""
    status = edit_algebra.status(op_id)
    assert status is not None
    assert not status.production_enabled
    assert not status.qualified


@pytest.mark.parametrize("op_id", sorted(QUALIFIED_BY_THE_LIVE_LANE))
def test_a_qualified_operation_carries_the_evidence_that_earned_it(op_id):
    """Catches: an operation promoted to production with no live evidence
    behind it.  The live qualification lane earns the promotion; the row
    must carry the coverage the measurement produced."""
    status = edit_algebra.status(op_id)
    assert status is not None
    assert status.production_enabled
    for backend in status.backends:
        if not backend.production_enabled:
            continue
        assert backend.qualification_predicates
        assert backend.qualification_evidence


def test_an_already_production_enabled_operation_keeps_its_predicates():
    """Catches: the protected route used to erase the recorded
    qualification of an operation that already had one.
    `broll.insert` is production-enabled on the qualification lane's
    recorded predicates - the product's own reels do not satisfy them,
    which is why the benchmark found it refused. The protected route is
    what runs it there, and it grants no new qualification."""
    row = edit_algebra.status("broll.insert").for_backend("resolve_axi")
    assert row is not None
    assert row.production_enabled
    assert row.qualification_predicates
    decision = execution_router.route_compiled_edit(
        {"operations": [dict(OPERATIONS["broll.insert"])]},
        context=_protected("broll.insert"))
    assert decision.qualification_gaps, (
        "the protected route must still record the gap it runs through")


# ── 3. The front door reaches the operations the planner would run ────


def _program_note(op_id: str) -> dict:
    return {
        "basis": "edit_spec", "outcome": "unrouted", "steps": [],
        "frame": None,
        "evidence": {
            "edit_spec_ops": [
                {"op": op_id, "owner": None, "ledger_op": "edit_program",
                 **{key: value for key, value in OPERATIONS[op_id].items()
                    if key != "op"}},
            ],
            "originator": "ren revise",
        },
    }


@pytest.mark.parametrize("op_id", UNROUTABLE)
def test_the_front_door_reaches_the_operations_the_planner_would_run(op_id):
    """Catches: the front door's classifier refusing an operation the
    AXI planner grants to `ren revise`, so the grant was unreachable
    and the whole edit surface stayed closed behind the
    production-enabled operations."""
    verdict = feedback_patch.decide(_program_note(op_id))
    assert verdict["verdict"] == feedback_patch.VERDICT_PATCH
    assert verdict["execution"]["backend"] in {
        "resolve_axi", "edit_patch", "reel_touchup"}


@pytest.mark.parametrize("op_id", UNROUTABLE)
def test_the_program_keeps_the_parameters_the_operation_declares(op_id):
    """Catches: the request builder swallowing a parameter the
    operation DECLARES. `clip.replace` names the file it writes and
    `multicam.select_angle` names the assigned camera's source; read
    as the target's identity they were dropped, and the operation was
    then refused for missing parameters it had been given."""
    request = edit_request.from_edit_program(
        {"ops": [{"op": op_id,
                  "params": {key: value
                             for key, value in OPERATIONS[op_id].items()
                             if key != "op"}}],
         "reel": "Reel 1", "base_generation": "1",
         "idempotency_key": "revise-1"},
        project="Podcast (field test)", timeline="Reel 1")
    compiled = edit_request.compile_request(request)
    (operation,) = compiled.operations
    for key in edit_algebra.get(op_id).required:
        assert operation.get(key) is not None, (
            f"{op_id} lost its declared parameter {key!r} on the way to "
            "the request")


# ── 4. The camera switch is routed, and never directly ───────────────

def test_the_camera_switch_is_staged_and_never_direct():
    """Catches: a camera switch classed as having no executor, so the
    router refused it before any route was chosen - while the AXI
    adapter that runs it had been in the tree all along."""
    assert edit_algebra.safety_class("multicam.select_angle") == (
        edit_algebra.SAFETY_COMPLEX_LOCALIZED)
    assert not edit_algebra.SAFETY_CLASS_POLICY[
        edit_algebra.SAFETY_COMPLEX_LOCALIZED].direct_allowed
    assert edit_algebra.direct_blockers(
        ["multicam.select_angle"], "resolve_axi")


# ── 5. The revision path reads the same operation the planner does ───


@pytest.mark.parametrize("op_id", UNROUTABLE)
def test_the_revision_path_keeps_the_parameters_the_operation_declares(op_id):
    """Catches: the revision path's own program reader dropping a
    parameter the operation declares. The request the revision compiles
    is the request the planner routes, and a `caption_fix` that lost
    its caption was refused for a parameter the note had stated."""
    from library.tools import revise

    program = {
        "kind": "edit_program", "id": "program-1", "base_generation": 3,
        "ops": [{"op": op_id, "text": "do the thing",
                  "params": {key: value
                             for key, value in OPERATIONS[op_id].items()
                             if key != "op"}}],
    }
    record = {"revise_id": "revise-1", "note_id": "note-1",
              "note": "do the thing"}
    request = revise._compile_revision_program(
        program, project="Podcast (field test)", reel="Reel 1",
        record=record)
    (operation,) = edit_request.compile_request(request).operations
    for key in edit_algebra.get(op_id).required:
        assert operation.get(key) is not None, (
            f"{op_id} lost its declared parameter {key!r} on the way "
            "through the revision path")
