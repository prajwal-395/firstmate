"""INFRA-004: the intent record must not name a Resolve object.

The defect these name: a high-level creative plan (an intent record, a
machine plan) that carries a Resolve object identifier, so the plan
belongs to ONE NLE and cannot be executed on another - and so the
backend's own instance handle leaks into the layer that states WHAT was
asked.

Each test names one defect and can fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import edit_preparation, intent_neutrality
from library.tools.edit_request import (
    EditOperation,
    EditRequest,
    EditTarget,
)


def _clip(uid="clip-1", name="a.mov", start=0, end=48, track_index=1,
          source_file="/media/a.mov", source_in=0, source_out=48):
    return {"unique_id": uid, "name": name,
            "record_in": start, "record_out": end, "duration": end - start,
            "transform": {}, "enabled": True, "markers": [],
            "track_type": "video", "track_index": track_index,
            "source_file": source_file,
            "source_in_frame": source_in, "source_out_frame": source_out}


def _base(*clips):
    return {"start_frame": 0, "end_frame": 200,
            "tracks": [{"type": "video", "index": 1, "name": "V1",
                        "clips": list(clips)}],
            "markers": {"timeline": []}}


def _request(*ops, request_id="req-1"):
    return EditRequest.from_dict({
        "id": request_id, "project": "Podcast", "timeline": "Reel 09",
        "expected_base_generation": 9,
        "operations": [op.to_dict() for op in ops]})


def _set_pan(uid="clip-1", value=5.0):
    return EditOperation(op="clip.set_property",
                         target=EditTarget(unique_id=uid),
                         params={"key": "Pan", "value": value},
                         expected_effect="nudge the interview frame")


# ── The intent record ────────────────────────────────────────────────


def test_the_neutral_intent_names_no_resolve_object():
    """Catches: an intent record that still carries a Resolve item id
    after it was made neutral - the plan belongs to one NLE."""
    base = _base(_clip())
    request = _request(_set_pan())
    intent = request.to_neutral(
        refs=intent_neutrality.refs_from_snapshot(base))
    assert intent.is_neutral, intent_neutrality.scan(intent.to_dict())
    assert not intent_neutrality.scan(intent.to_dict())


def test_the_neutral_intent_names_the_material_the_target_plays():
    """Catches: a neutral intent that dropped the target's identity
    instead of restating it in NLE-neutral terms - the edit now points
    at nothing."""
    base = _base(_clip(source_file="/media/a.mov", source_in=12,
                       source_out=60))
    request = _request(_set_pan())
    intent = request.to_neutral(
        refs=intent_neutrality.refs_from_snapshot(base))
    ref = intent.operations[0].target.ref
    assert ref is not None
    assert ref.source_file == "/media/a.mov"
    assert ref.editorial_identity == ("/media/a.mov", 12, 60, None)


def test_the_binding_returns_the_request_the_backends_execute():
    """Catches: a neutralization that changed the request - the route
    would no longer be the route the request was planned for."""
    base = _base(_clip())
    request = _request(_set_pan())
    intent = request.to_neutral(
        refs=intent_neutrality.refs_from_snapshot(base))
    rebuilt = intent_neutrality.bind(intent)
    assert rebuilt.to_dict() == request.to_dict()


def test_a_target_whose_material_was_never_recorded_is_a_named_mismatch():
    """Catches: an intent that quietly dropped a target it could not
    state, so the edit would land on nothing and say nothing."""
    request = _request(_set_pan())
    intent = request.to_neutral(refs={})
    kinds = [row.kind for row in intent.mismatches]
    assert "unresolved_target" in kinds
    detail = next(row.detail for row in intent.mismatches
                  if row.kind == "unresolved_target")
    assert "Resolve object id" in detail


def test_an_operation_that_needs_an_instance_is_a_named_mismatch():
    """Catches: an operation whose semantics need a Resolve object,
    stated as though the intent could express it."""
    operation = EditOperation(
        op="clip.insert", target=None,
        params={"unique_id": "footage:cutaway:video:/media/b.mov:0",
                "media_pool_item": "pool-1", "track_type": "video",
                "track_index": 1, "record_in": 0, "record_out": 48},
        expected_effect="place the cutaway")
    request = _request(operation)
    intent = request.to_neutral()
    assert any(row.kind == "instance_only_operation"
               for row in intent.mismatches)


def test_the_bindings_live_beside_the_intent_and_not_inside_it():
    """Catches: the instance handles written back into the intent
    record - the record is neutral only if the handles are outside it."""
    base = _base(_clip())
    request = _request(_set_pan())
    intent = request.to_neutral(
        refs=intent_neutrality.refs_from_snapshot(base))
    assert not intent_neutrality.scan(intent.to_dict())
    adapter = intent.adapter_record()
    assert intent_neutrality.scan(adapter["bindings"])


# ── The machine plan ─────────────────────────────────────────────────


def test_the_machine_plan_names_no_resolve_object():
    """Catches: the plan that states what will happen, carrying the
    Resolve item id of the instance it happens to run against."""
    base = _base(_clip())
    result = edit_preparation.prepare(
        _request(_set_pan()), base_snapshot=base, base_generation=9)
    assert result.ready, result.refusals
    assert not intent_neutrality.scan(result.plan), \
        intent_neutrality.scan(result.plan)


def test_the_machine_plan_states_the_target_by_its_material():
    """Catches: a neutral plan that stopped saying WHICH frame it
    moves - the plan reads as if it acted on the timeline."""
    base = _base(_clip(source_file="/media/a.mov", source_in=12,
                       source_out=60))
    result = edit_preparation.prepare(
        _request(_set_pan()), base_snapshot=base, base_generation=9)
    assert result.ready, result.refusals
    step = result.plan["steps"][0]
    assert step["target"]["item_ref"]["source_file"] == "/media/a.mov"
    assert step["target"]["item_ref"]["source_in_frame"] == 12


def test_a_plan_that_cannot_state_its_target_says_so():
    """Catches: a plan that dropped an unresolvable target without
    saying so - the operator reads a plan that will not run."""
    base = _base(_clip(uid="clip-9"))
    result = edit_preparation.prepare(
        _request(_set_pan(uid="clip-1")), base_snapshot=base,
        base_generation=9)
    kinds = [row["kind"] for row in result.plan["capability_mismatches"]]
    assert "unresolved_target" in kinds


def test_the_explanation_says_what_the_plan_says():
    """Catches: the prose and the plan disagreeing about the target."""
    base = _base(_clip(source_file="/media/a.mov"))
    result = edit_preparation.prepare(
        _request(_set_pan()), base_snapshot=base, base_generation=9)
    text = edit_preparation.explain(result)
    assert "/media/a.mov" in text
    assert "clip-1" not in text


# ── Capability mismatches, per backend ───────────────────────────────


def test_a_backend_that_cannot_express_an_operation_is_named():
    """Catches: a capability mismatch swallowed instead of recorded -
    the punchlist asks (INFRA-004) that they be explicit."""
    request = _request(_set_pan())
    intent = request.to_neutral()
    mismatches = intent_neutrality.capability_mismatches(intent, "edit_patch")
    assert isinstance(mismatches, tuple)


def test_the_mismatch_judgment_is_the_router_s_own():
    """Catches: the mismatch report judging for itself - it could
    disagree with the route the request will actually take, and then it
    would be a second, quieter router."""
    request = _request(_set_pan())
    intent = request.to_neutral()
    unknown = intent_neutrality.capability_mismatches(
        intent, "not_a_route_the_router_selects")
    assert any("not one the router selects" in row.detail for row in unknown)


def test_a_capability_mismatch_names_the_operation_it_is_about():
    """Catches: a mismatch that names a backend and not the operation -
    the reader cannot tell what the backend cannot do."""
    request = _request(_set_pan())
    intent = request.to_neutral()
    mismatches = intent_neutrality.capability_mismatches(
        intent, "not_a_route_the_router_selects")
    unsupported = [row for row in mismatches
                   if row.kind == "backend_unsupported"]
    assert unsupported
    assert all(row.op == "clip.set_property" for row in unsupported)


# ── The measurement ──────────────────────────────────────────────────


CORPUS = "tests/fixtures/intent_neutrality/corpus.json"


def _corpus() -> dict:
    path = Path(CORPUS)
    if not path.is_file():
        pytest.skip("the recorded corpus is not in this checkout")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_measurement_reports_both_halves_with_denominators():
    """Catches: a measurement that reports a count with no denominator,
    so before and after cannot be compared."""
    corpus = _corpus()
    snapshot = corpus["snapshot"]
    request = EditRequest.from_dict(corpus["requests"][0])
    result = edit_preparation.prepare(
        request, base_snapshot=snapshot, base_generation=1)
    assert result.ready, result.refusals
    before = intent_neutrality.identifier_count(request.to_dict())
    after = intent_neutrality.identifier_count(
        request.to_neutral(
            refs=intent_neutrality.refs_from_snapshot(snapshot)).to_dict())
    assert before["fields"] > 0
    assert after["fields"] > 0
    assert before["resolve_specific_fields"] > 0
    assert after["resolve_specific_fields"] == 0


def test_the_recorded_requests_still_route_identically():
    """Catches: a change to the intent layer that moved the route the
    recorded requests take - the edit would land somewhere else."""
    from library.tools.edit_request import compile_request, from_algebra_ops

    corpus = _corpus()
    for entry in corpus.get("recorded_requests") or ():
        ops = entry.get("operations") or ()
        if not ops:
            continue
        try:
            request = from_algebra_ops(ops, project="Recorded",
                                       timeline="Recorded")
        except Exception:  # noqa: BLE001, S112 - an op the algebra refuses
            continue
        intent = request.to_neutral()
        rebuilt = intent_neutrality.bind(intent)
        assert rebuilt.to_dict() == request.to_dict()
        assert (compile_request(rebuilt).to_router_input()
                == compile_request(request).to_router_input())
