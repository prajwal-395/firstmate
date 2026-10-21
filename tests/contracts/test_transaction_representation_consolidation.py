"""AXI-004: one model for the meaning of a request, on every front door.

The engine carried one user-driven edit in two internal models - the
canonical `EditRequest` and the legacy typed `AxiTransaction` - and
three semantic front doors read a request with the legacy one. Two
requests that meant the same thing could therefore be written in two
orders, and a preservation requirement could be lost on the way to the
write.

These tests hold the consolidation still. Each one names the defect it
guards, so a regression reads as the defect coming back rather than as a
number that moved.
"""

from __future__ import annotations

import pytest

from library.tools import edit_request, legacy_transaction
from library.tools import transaction_representations as inventory

# ── The acceptance gate: one model per front door ────────────────────


def test_every_semantic_front_door_reads_a_request_with_the_canonical_model():
    """Defect: two front doors read one request with two models.

    A front door that reads a request with a model other than the
    canonical one can interpret the same request differently - in a
    different order, or without the constraints the request names.
    """
    non_canonical = inventory.non_canonical_front_doors()
    assert non_canonical == (), (
        "these front doors read a request with a model that is not the "
        f"canonical {inventory.CANONICAL}: "
        + ", ".join(f"{door.name} ({door.entry})" for door in non_canonical))


def test_the_inventory_names_a_front_door_for_every_door_that_asks():
    """Defect: a door that asks for an edit is missing from the count.

    The measurement is only worth its denominators if every door that
    accepts an edit is in it. The MCP doors are the ones a host calls;
    the two in-library doors are the ones the pipeline calls.
    """
    names = {door.name for door in inventory.front_doors()}
    for expected in ("mcp.resolve_axi_edit_request",
                     "mcp.resolve_axi_edit_request_plan",
                     "mcp.resolve_axi_transaction",
                     "mcp.resolve_axi_transaction_submit",
                     "mcp.resolve_axi_transaction_plan"):
        assert expected in names


def test_the_legacy_representation_is_only_built_from_the_canonical_request():
    """Defect: the legacy model was built by the front doors themselves.

    A carrier built at a front door is a carrier whose meaning is
    decided there. The one production construction left is the
    canonical model's own conversion, which is the direction the
    consolidation wants.
    """
    sites = inventory.construction_sites()
    owners = {site.split(":")[0] for site in sites["AxiTransaction"]}
    assert owners <= {"library/tools/edit_request.py"}, (
        "the legacy carrier is constructed outside the canonical model's "
        f"own conversion: {sorted(owners)}")


# ── The differential: the same request, one order ────────────────────


def _operations() -> list[dict]:
    return [
        {"op": "clip.set_property", "unique_id": "item-2",
         "key": "Tilt", "value": 18.0, "depends_on": [1]},
        {"op": "clip.set_property", "unique_id": "item-1",
         "key": "Pan", "value": 24.0},
    ]


def _carrier(**overrides) -> dict:
    carrier = {
        "id": "stable-key", "project": "Project", "timeline": "Timeline",
        "base_generation": 7, "capability": "reel.touchup",
        "affected_spans": [[100, 120]],
        "conflict_domains": ["picture_transform"],
        "operations": _operations(),
    }
    carrier.update(overrides)
    return carrier


def _base() -> dict:
    """A recorded base the spans can be measured against."""
    return {
        "start_frame": 0, "end_frame": 200,
        "tracks": [{"type": "video", "index": 1, "name": "V1", "clips": [
            {"unique_id": "item-1", "name": "a.mov", "record_in": 100,
             "record_out": 120, "duration": 20, "transform": {"Pan": 0.0},
             "enabled": True, "markers": [], "track_type": "video",
             "track_index": 1},
            {"unique_id": "item-2", "name": "b.mov", "record_in": 100,
             "record_out": 120, "duration": 20, "transform": {"Tilt": 0.0},
             "enabled": True, "markers": [], "track_type": "video",
             "track_index": 1}]}],
        "markers": {"timeline": []},
    }


def test_the_legacy_door_writes_the_operations_in_the_canonical_order():
    """Defect: the legacy door wrote the operations in author order.

    The canonical model orders operations by the dependencies they
    declare. The legacy door wrote them in the order the caller listed
    them, so the same request reached the timeline in two different
    orders depending on which door it came through.
    """
    canonical = edit_request.from_algebra_ops(
        _operations(), project="Project", timeline="Timeline",
        expected_base_generation=7)
    canonical_patch = edit_request.compile_request(
        canonical, _base()).to_patch(
            "reel.touchup", base_generation=7, project="Project",
            timeline="Timeline", idempotency_key="stable-key")

    legacy_patch = legacy_transaction.patch_for(_carrier())

    assert legacy_patch["operations"] == canonical_patch["operations"]
    assert [op["key"] for op in legacy_patch["operations"]] == ["Pan", "Tilt"]


def test_the_legacy_door_keeps_the_extent_and_the_capability_it_declares():
    """Defect: the consolidation replaced a declared extent with a
    measured one.

    The canonical path measures the extent from the base snapshot; the
    legacy patch's declared spans are the caller's own contract with
    the collateral check. Replacing them would change what the write is
    judged against, so the carrier's declarations stay.
    """
    patch = legacy_transaction.patch_for(_carrier())
    assert patch["affected_spans"] == [[100, 120]]
    assert patch["conflict_domains"] == ["picture_transform"]
    assert patch["capability"] == "reel.touchup"
    assert patch["id"] == "stable-key"
    assert patch["base_generation"] == 7


# ── The constraints a request names must survive the model ──────────


def test_a_preservation_requirement_named_by_an_operation_survives():
    """Defect: a flat operation's preservation requirement was dropped.

    The flat builder kept `preservation` in the operation's PARAMETERS,
    so the requirement travelled to the backend as an unknown parameter
    and was never judged. A request that said what an operation must not
    change lost the requirement on the way in.
    """
    request = edit_request.from_algebra_ops(
        [{"op": "clip.set_property", "unique_id": "item-1",
          "key": "Pan", "value": 24.0,
          "preservation": [{"kind": "clip_unchanged",
                            "unique_id": "clip-9"}]}],
        project="Project", timeline="Timeline")
    assert request.operations[0].preservation == (
        {"kind": "clip_unchanged", "unique_id": "clip-9"},)
    assert "preservation" not in request.operations[0].params


def test_both_doors_keep_the_preservation_the_request_names():
    """Defect: one door kept a preservation requirement, the other lost it.

    The semantic route judges the request's requirements on the
    read-back; the durable route judges the patch's. A requirement the
    request states and one of the two routes drops is a requirement
    nobody keeps.
    """
    preservation = [{"kind": "clip_unchanged", "unique_id": "clip-9"}]
    carrier = _carrier(preservation=preservation)
    legacy_patch = legacy_transaction.patch_for(carrier)
    assert legacy_patch["preservation"] == preservation

    request = edit_request.from_algebra_ops(
        _operations(), project="Project", timeline="Timeline",
        expected_base_generation=7, preservation=preservation)
    canonical_patch = edit_request.compile_request(
        request, _base()).to_patch(
            "reel.touchup", base_generation=7, project="Project",
            timeline="Timeline", idempotency_key="stable-key",
            preservation=request.preservation)
    assert canonical_patch["preservation"] == preservation


def test_a_preservation_requirement_nobody_can_judge_is_refused():
    """Defect: a requirement nobody can prove is a promise nobody keeps.

    The legacy carrier now carries preservation, and a requirement the
    engine cannot judge is refused when the request is read - rather
    than carried to the write and ignored.
    """
    with pytest.raises(edit_request.EditRequestError):
        legacy_transaction.request_for(
            _carrier(preservation=[{"kind": "the_music_bed_must_survive"}]))


# ── A recorded transaction replays identically ───────────────────────


def test_a_recorded_transaction_replays_to_the_same_operations():
    """Defect: a recorded transaction stopped meaning what it meant.

    The event log holds the operations a transaction wrote, the
    generation it was planned against and the id it carried. Reading
    one back through the canonical request must give the same ordered
    operations, or a recovery would undo something other than what was
    written.
    """
    recorded = {
        "transaction_id": "stable-key", "project": "Project",
        "timeline": "Timeline", "base_generation": 7,
        "operations": _operations(),
    }
    request = legacy_transaction.recorded_request(recorded)
    compiled = edit_request.compile_request(request)

    assert request.id == "stable-key"
    assert compiled.operations == tuple(
        edit_request.compile_request(
            edit_request.from_algebra_ops(
                _operations(), project="Project", timeline="Timeline",
                expected_base_generation=7)).operations)
    assert [op["key"] for op in compiled.operations] == ["Pan", "Tilt"]


def test_a_recorded_transaction_replays_to_the_same_durable_patch():
    """Defect: the recorded patch and the rebuilt patch differed.

    A recorded transaction that rebuilds into a different patch is a
    transaction the broker would treat as new work.
    """
    carrier = _carrier()
    patch = legacy_transaction.patch_for(carrier)
    recorded = {
        "transaction_id": patch["id"], "project": patch["project"],
        "timeline": patch["timeline"],
        "base_generation": patch["base_generation"],
        "operations": patch["operations"],
    }
    request = legacy_transaction.recorded_request(recorded)
    compiled = edit_request.compile_request(request)
    assert [dict(op) for op in compiled.operations] == patch["operations"]


# ── The legacy door refuses what the canonical door refuses ─────────


def test_the_legacy_door_refuses_an_operation_the_algebra_does_not_know():
    """Defect: the two doors refused different requests.

    The canonical model refuses an operation the algebra does not know.
    The legacy door wrote it into a patch and let the write fail later;
    now it refuses the same request the canonical door refuses.
    """
    with pytest.raises(edit_request.EditRequestError):
        legacy_transaction.request_for(
            _carrier(operations=[{"op": "clip.teleport",
                                  "unique_id": "item-1"}]))


def test_the_legacy_door_refuses_a_carrier_with_no_operations():
    """Defect: an empty carrier was planned as a transaction.

    A carrier with no operations is not a request, and the canonical
    model refuses it.
    """
    with pytest.raises(legacy_transaction.LegacyTransactionRefused):
        legacy_transaction.request_for(_carrier(operations=[]))


# ── The round trip is lossless over the WHOLE algebra ────────────────


#: A value for every required parameter the algebra declares. The
#: request builder only asks that a required parameter is present and
#: not None, so the value's shape does not matter here.
_VALUES: dict[str, object] = {
    "angle_index": 1, "assignment_basis": "speaker", "at_frame": 10,
    "before": {"zoom": 1.0}, "brief": "tighten", "carrier": "voice",
    "child_unique_ids": ["item-2"], "clip": "a.mov", "color": "#ffffff",
    "db": -3.0, "delta_frames": 12, "direction": "left", "dx": 10.0,
    "dy": 4.0, "edits": [{"op": "clip.trim"}], "enabled": True,
    "fade_in": 12.0, "fade_out": 12.0, "file": "/media/clip.mov",
    "frame": 10, "frames": 24, "from_index": 1, "geometry": {"x": 0.5},
    "grade": "neutral", "height": 360, "in_frame": 10, "key": "Pan",
    "keyframes": [{"frame": 0, "value": 0.0}], "keys": ["auto"],
    "lut": "/grades/lut.cube", "matte": "matte-1",
    "media_pool_item": "pool-1", "name": "clip name", "node": "node-1",
    "node_index": 0, "note": "a note", "other_unique_id": "item-2",
    "out_frame": 20, "pan": 0.0, "param": "Pan", "params": {"Pan": 0.0},
    "percent": 100.0, "plan": {"op": "clip.trim"}, "point": {"x": 0.5},
    "record_frame": 10, "record_in": 100, "record_out": 120,
    "row": 3, "segments": [{"in": 0, "out": 10}], "shape": "circle",
    "source_file": "/media/clip.mov", "source_in": 0, "source_in_frame": 1,
    "source_out": 10, "source_out_frame": 20, "speaker_id": "speaker-1",
    "start": 0, "style": "minimal", "sync_evidence_id": "evidence-1",
    "text": "the words", "to_frame": 30, "to_index": 2, "track": "V1",
    "track_index": 1, "track_type": "video", "transition": "cross dissolve",
    "unique_id": "item-1", "value": 24.0, "width": 640,
    "with_unique_ids": ["item-2"],
}


def _flat_op(op_id: str) -> dict:
    from library.tools import edit_algebra

    spec = edit_algebra.get(op_id)
    assert spec is not None, f"{op_id} is not in the algebra"
    flat: dict = {"op": op_id}
    for name in spec.required:
        assert name in _VALUES, (
            f"the corpus has no value for the required parameter {name!r} "
            f"of {op_id!r} - a missing value makes the round trip untested")
        flat[name] = _VALUES[name]
    return flat


def test_the_adapter_keeps_every_operation_the_algebra_declares():
    """Defect: the adapter dropped a key an operation declared.

    The adapter reads a flat operation as a canonical request and
    compiles it back. Every key the algebra declares must survive the
    round trip: an operation that lost a parameter on the way in would
    be written with less meaning than the caller asked for, and a
    recorded transaction would replay as different work.
    """
    from library.tools import edit_algebra

    for op_id in sorted(edit_algebra.OP_STATUS):
        flat = _flat_op(op_id)
        request = edit_request.from_algebra_ops(
            [flat], project="Project", timeline="Timeline")
        compiled = edit_request.compile_request(request)
        assert dict(compiled.operations[0]) == flat, (
            f"the round trip changed {op_id}: "
            f"{dict(compiled.operations[0])} is not {flat}")


def test_the_adapter_writes_the_whole_algebra_in_the_canonical_order():
    """Defect: a multi-operation request was written in author order.

    The corpus is every operation the algebra declares, so the ordering
    the legacy door now uses is exercised against the whole vocabulary
    rather than against one hand-written pair.
    """
    from library.tools import edit_algebra

    ops = [_flat_op(op_id) for op_id in sorted(edit_algebra.OP_STATUS)]
    carrier = _carrier(operations=ops)
    request = legacy_transaction.request_for(carrier)
    compiled = edit_request.compile_request(request)
    assert [dict(op) for op in compiled.operations] == ops
