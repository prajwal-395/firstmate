"""Defect-naming contracts for the reel.opening operation.

The captain's AXI consolidation punch list failed the ordinary request
"Tighten the opening to the supplied brief while preserving the
speaker's meaning and approved edits." with "no production path for
reel.opening: reel.opening is absent from the edit algebra". These
tests pin the production path this lane adds - the canonical algebra
op, its acceptance contract, its registered backend, and the
fail-closed qualification - so a later edit that drops any half
reintroduces the named defect instead of silently reopening it.
"""

from __future__ import annotations

import pytest

from library.tools import edit_algebra, operation_acceptance


def _opening(**overrides):
    op = {"op": "reel.opening", "unique_id": "o1",
          "brief": "open on the claim, not the throat-clearing",
          "edits": [{"op": "clip.trim", "unique_id": "o1",
                     "in_frame": 10, "out_frame": 100},
                    {"op": "clip.delete", "unique_id": "o2"}]}
    op.update(overrides)
    return op


def _clip(uid, record_in, record_out):
    return {"unique_id": uid, "name": uid, "track_type": "video",
            "track_index": 1, "record_in": record_in,
            "record_out": record_out,
            "duration": record_out - record_in,
            "source_in_frame": 0,
            "source_out_frame": record_out - record_in,
            "source_file": f"/pool/{uid}.mov", "enabled": True,
            "speed": None,
            "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0,
                          "ZoomY": 1.0},
            "markers": []}


def _base():
    return {"start_frame": 0, "end_frame": 360, "tracks": [
        {"type": "video", "index": 1, "name": "V1",
         "clips": [_clip("o1", 0, 120), _clip("o2", 120, 240),
                   _clip("k", 240, 360)]}]}


def test_reel_opening_is_defined_with_anchor_brief_and_edits():
    """Defect: the punch-list failure - no production path because the
    op is absent from the edit algebra."""
    spec = edit_algebra.get("reel.opening")

    assert spec is not None
    assert spec.domain == "timeline_structure"
    assert tuple(spec.required) == ("unique_id", "brief", "edits")
    assert spec.key(_opening()) == ("reel", "opening", "o1")
    assert spec.inverse.self_inverse is True
    assert spec.inverse.reason


def test_reel_opening_span_covers_the_carried_edits():
    """Defect: a planning-level op whose span misses its edits would
    schedule and validate against frames it does not touch."""
    span = edit_algebra.get("reel.opening").span(_opening(), _base())

    assert span == (0, 240)


def test_reel_opening_verifier_detects_the_absent_write():
    """Defect: a verifier that passes on a timeline the op did not
    change would commit a lie - judged on the base, the tighten is
    absent, so it must fail by name."""
    failure = edit_algebra.get("reel.opening").verify(_opening(), _base())

    assert isinstance(failure, str) and failure


def test_reel_opening_acceptance_names_opening_and_collateral_checks():
    """Defect: claiming the tighten without independent read-back
    postconditions and a collateral-change check."""
    contract = operation_acceptance.contract_for("reel.opening")

    assert contract["checks"] == [
        "target_identity", "requested_opening_span",
        "approved_edits_preserved", "unrelated_items_unchanged"]
    assert contract["structural_source"] == "timeline_readback"


def test_reel_opening_acceptance_passes_only_on_complete_evidence():
    """Defect: an acceptance that passes with a check missing would let
    half a tighten read as a committed one."""
    full = {"target_identity": True, "requested_opening_span": True,
            "approved_edits_preserved": True,
            "unrelated_items_unchanged": True}
    assert operation_acceptance.evaluate(
        _opening(), readback_passed=True,
        evidence=dict(full))["passed"] is True
    for missing in full:
        partial = {key: value for key, value in full.items()
                   if key != missing}
        assert operation_acceptance.evaluate(
            _opening(), readback_passed=False,
            evidence=partial)["passed"] is False


def test_reel_opening_adapter_is_registered_on_its_declared_backend():
    """Defect: a registered executor the algebra does not advertise (or
    the reverse) - the router and the registry would disagree about the
    production path."""
    from library.tools import edit_execution_adapters, resolve_axi_core

    edit_execution_adapters.register_builtin_edit_executors()

    assert ("reel.opening", "resolve_axi") in \
        resolve_axi_core.registered_edit_executors()
    status = edit_algebra.status("reel.opening")

    assert status.for_backend("resolve_axi") is not None


def test_reel_opening_stays_fail_closed_until_qualified():
    """Defect: production traffic reaching an unqualified executor. The
    live-requests lane records the qualifying evidence; until then the
    op is implemented but the router refuses it."""
    status = edit_algebra.status("reel.opening")

    assert status.compilable is True
    assert status.qualified is False
    assert status.production_enabled is False
    assert edit_algebra.is_production_enabled(
        "reel.opening", "resolve_axi") is False


def test_reel_opening_refuses_ordinary_production_routing():
    """Defect: the router selecting a backend no qualification proves -
    production routing must fail closed, naming the missing proof."""
    from library.tools.execution_router import (
        RoutingRefused,
        route_compiled_edit,
    )

    with pytest.raises(RoutingRefused, match="production"):
        route_compiled_edit({"operations": [_opening()]})


def test_reel_opening_compiles_to_direct_structural_edits_only():
    """Defect: the planning-level op reaching a rebuild-shaped path. It
    compiles through EditRequest to its carried trim/remove edits -
    direct mutations with computed spans - and never a full rebuild."""
    from library.tools.edit_request import compile_request, from_algebra_ops

    request = from_algebra_ops(
        [{"op": "clip.trim", "unique_id": "o1", "in_frame": 10,
          "out_frame": 100},
         {"op": "clip.delete", "unique_id": "o2"}],
        project="p", timeline="t",
        originator={"kind": "agent", "name": "reel.opening"})
    compiled = compile_request(request, _base())

    assert [op["op"] for op in compiled.operations] == [
        "clip.trim", "clip.delete"]
    assert tuple(compiled.affected_spans) == ((0, 240),)
    assert tuple(compiled.conflict_domains) == ("timeline_structure",)
    assert set(compiled.kinds) == {"direct"}


def test_reel_opening_without_a_brief_is_not_decided():
    """Defect: tightening an opening with no supplied brief would invent
    the target - the adapter refuses before compilation."""
    from library.tools.edit_execution_adapters import (
        _reel_opening_decided,
    )

    with pytest.raises(ValueError, match="brief"):
        _reel_opening_decided(_opening(brief="  "))
    with pytest.raises(ValueError, match="structural edits"):
        _reel_opening_decided(_opening(edits=[]))
    with pytest.raises(ValueError, match="names 'clip.move'"):
        _reel_opening_decided(_opening(edits=[{"op": "clip.move",
                                               "unique_id": "o1",
                                               "to_frame": 5}]))


def test_reel_opening_ripple_refuses_before_the_first_write():
    """Defect: a half-tightened opening - no ripple executor qualifies,
    so a ripple edit must refuse before anything mutates, never after
    the trims have already landed."""
    from library.tools.edit_execution_adapters import (
        _reel_opening_decided,
    )

    with pytest.raises(ValueError, match="ripple"):
        _reel_opening_decided(_opening(edits=[
            {"op": "clip.trim", "unique_id": "o1",
             "in_frame": 10, "out_frame": 100},
            {"op": "clip.ripple", "unique_id": "o2",
             "delta_frames": -20}]))
