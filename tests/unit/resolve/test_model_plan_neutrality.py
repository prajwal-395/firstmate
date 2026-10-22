"""INFRA-004: the model-facing plan names the MATERIAL, not the instance.

The sibling lane (`test_intent_neutrality.py`) took the intent record
and the machine plan out of Resolve's hands.  The surface left was the
one the MODEL reads and writes: `edit_spec` handed the model every
recorded Resolve item id and the prompt asked it to write one back.

A `unique_id` in Ren's vocabulary is Resolve's `GetUniqueId()` of a
timeline item: an EXECUTION ADDRESS.  It changes when the reel is
rebuilt, and it means nothing to another NLE.  A creative plan that
carries one is a plan for one instance of one application.

These tests name the defects that surface had and the ones the
neutral address brings with it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

SNAPSHOT = {
    "fps": 24,
    "start_frame": 0,
    "end_frame": 600,
    "tracks": [
        {"type": "video", "index": 1, "name": "A-roll", "clips": [
            {"unique_id": "clip-a", "name": "host.mov",
             "source_file": "/footage/host.mov",
             "source_in_frame": 100, "source_out_frame": 400,
             "record_in": 0, "record_out": 300,
             "track_type": "video", "track_index": 1},
            {"unique_id": "clip-b", "name": "host.mov",
             "source_file": "/footage/host.mov",
             "source_in_frame": 400, "source_out_frame": 700,
             "record_in": 300, "record_out": 600,
             "track_type": "video", "track_index": 1},
        ]},
        {"type": "video", "index": 5, "name": "Subtitles", "clips": [
            {"unique_id": "cap-1", "name": "",
             "source_file": "", "source_in_frame": None,
             "source_out_frame": None,
             "record_in": 24, "record_out": 48,
             "track_type": "video", "track_index": 5},
        ]},
    ],
    "markers": {"timeline": [], "notes": []},
}


def _request(target: dict) -> dict:
    return {
        "id": "recorded:1",
        "project": "Recorded",
        "timeline": "Reel 26",
        "expected_base_generation": 1,
        "operations": [{
            "op": "clip.set_property",
            "target": target,
            "params": {"key": "Pan", "value": 1.0},
            "kind": "direct",
            "expected_effect": "nudge the frame",
        }],
    }


def _material_target(**overrides) -> dict:
    target = {
        "source_file": "/footage/host.mov",
        "source_in_frame": 100,
        "source_out_frame": 400,
        "track_type": "video",
        "track_index": 1,
    }
    target.update(overrides)
    return target


def test_the_rows_the_model_reads_carry_no_instance_id():
    """The defect: the model-facing plan handed the model the Resolve
    item ids of the reel's items.

    A `unique_id` is an EXECUTION ADDRESS - it changes when the reel is
    rebuilt and means nothing to another NLE - so a creative plan that
    carries one is a plan for one instance of one application.  The rows
    now name the material each item plays and the row it plays on."""
    from library.tools import edit_spec

    rows = edit_spec._timeline_rows(SNAPSHOT)
    items = [item for row in rows for item in row["items"]]
    assert items, "the reel's items must still be listed"
    assert all("unique_id" not in item for item in items)
    first = items[0]
    assert first["source_file"] == "/footage/host.mov"
    assert first["source_in_frame"] == 100
    assert first["source_out_frame"] == 400
    assert first["track_index"] == 1


def test_a_plan_written_without_an_instance_id_resolves_by_its_address():
    """The defect this fixes: a plan that names the material it acts
    on had nowhere to go - the request refused it for a missing
    `unique_id`, and the executor had no way to find the item.

    The address resolves against the RECORDED reel, and the instance it
    names is filled in at the binding - so the router sees the same
    operation it would have seen."""
    from library.tools import edit_preparation, edit_request

    request = edit_request.EditRequest.from_dict(
        _request(_material_target()))
    result = edit_preparation.prepare(
        request, base_snapshot=SNAPSHOT, base_generation=1)
    assert tuple(result.refusals) == ()
    assert result.ready
    resolved = result.resolved[0]
    assert resolved.status == "resolved"
    assert resolved.unique_id == "clip-a"
    assert "address" in resolved.note


def test_an_address_that_matches_two_recorded_items_is_refused():
    """The defect this avoids: an address that names two placements of
    the same material is AMBIGUOUS, and the engine must refuse it
    rather than pick one - a guess about the editor's intent is not a
    plan."""
    from library.tools import edit_preparation, edit_request

    twice = {
        "fps": 24, "start_frame": 0, "end_frame": 600,
        "tracks": [{"type": "video", "index": 1, "name": "A-roll", "clips": [
            {"unique_id": "clip-a", "name": "host.mov",
             "source_file": "/footage/host.mov",
             "source_in_frame": 100, "source_out_frame": 400,
             "record_in": 0, "record_out": 300,
             "track_type": "video", "track_index": 1},
            {"unique_id": "clip-b", "name": "host.mov",
             "source_file": "/footage/host.mov",
             "source_in_frame": 100, "source_out_frame": 400,
             "record_in": 300, "record_out": 600,
             "track_type": "video", "track_index": 1},
        ]}],
        "markers": {"timeline": [], "notes": []},
    }
    request = edit_request.EditRequest.from_dict(_request(_material_target()))
    result = edit_preparation.prepare(
        request, base_snapshot=twice, base_generation=1)
    assert not result.ready
    assert result.resolved[0].status == "ambiguous"
    assert any("must say which one" in refusal
               for refusal in result.refusals)


def test_an_item_with_no_material_is_addressed_by_its_placement():
    """The defect this avoids: a caption the engine drew has no file to
    name, so a plan that could only address material could not say
    which caption it meant - and the model would have had to ask about
    every caption on the reel.

    The PLACEMENT - the row and the frames it occupies - is the address
    an item without material has."""
    from library.tools import edit_preparation, edit_request

    request = edit_request.EditRequest.from_dict(_request({
        "track_type": "video", "track_index": 5,
        "record_in": 24, "record_out": 48,
    }))
    result = edit_preparation.prepare(
        request, base_snapshot=SNAPSHOT, base_generation=1)
    assert tuple(result.refusals) == ()
    assert result.resolved[0].unique_id == "cap-1"


def test_the_router_sees_the_same_operations_for_an_addressed_plan():
    """The fidelity the lane must not break: a plan written without an
    instance id must reach the router as the SAME operations the
    recorded plan reached it as - the same target, the same order, the
    same kinds."""
    from library.tools import edit_preparation, edit_request

    recorded = edit_request.EditRequest.from_dict(
        _request({"unique_id": "clip-a", "track_type": "video",
                  "track_index": 1, "name": "host.mov"}))
    addressed = edit_request.EditRequest.from_dict(
        _request(_material_target()))
    result = edit_preparation.prepare(
        addressed, base_snapshot=SNAPSHOT, base_generation=1)
    bound, _notes = edit_preparation.bind_request(addressed, result.resolved)
    assert bound is not None
    before = edit_request.compile_request(recorded, SNAPSHOT)
    after = edit_request.compile_request(bound, SNAPSHOT)
    assert before.to_router_input()[0] == after.to_router_input()[0]


def test_a_second_item_is_named_by_its_address_too():
    """The defect this avoids: an operation that names TWO items
    (`clip.join`) had no way to say which two, once the plan stopped
    carrying instance ids - and the plan could not be written at all.

    The second item is named by its address under `other`, and the
    binding fills the instance the operation needs."""
    from library.tools import edit_preparation, edit_request

    request = edit_request.EditRequest.from_dict({
        "id": "recorded:2",
        "project": "Recorded",
        "timeline": "Reel 26",
        "expected_base_generation": 1,
        "operations": [{
            "op": "clip.join",
            "target": _material_target(),
            "params": {"other": _material_target(
                source_file="/footage/host.mov",
                source_in_frame=400, source_out_frame=700)},
            "kind": "direct",
            "expected_effect": "join the two cuts",
        }],
    })
    result = edit_preparation.prepare(
        request, base_snapshot=SNAPSHOT, base_generation=1)
    assert tuple(result.refusals) == ()
    bound, _notes = edit_preparation.bind_request(request, result.resolved)
    flat = bound.operations[0].to_algebra_op()
    assert flat["unique_id"] == "clip-a"
    assert flat["other_unique_id"] == "clip-b"
    # The ADDRESS is the plan's own vocabulary; the bound request
    # speaks the instance the backend executes.
    assert "other" not in flat


def test_the_recorded_plan_keeps_no_instance_id():
    """The defect: the model-facing plan the engine RECORDS carried the
    instance id the model wrote.

    An id that names a recorded item is translated into the address it
    stands for, so the recorded plan names the material; the binding
    mints the instance again.  An id that names nothing recorded is
    still a question."""
    from library.tools import edit_spec

    timeline = {"available": True, "rows": edit_spec._timeline_rows(SNAPSHOT)}
    spec = {"program": {"kind": "edit_program", "ops": [
        {"op": "clip.set_enabled",
         "params": {"unique_id": "clip-a", "enabled": False}}]}}
    edit_spec._clarify_unlisted_program_targets(spec, timeline, SNAPSHOT)
    params = spec["program"]["ops"][0]["params"]
    assert "unique_id" not in params
    assert params["source_file"] == "/footage/host.mov"
    assert params["source_in_frame"] == 100
    assert params["source_out_frame"] == 400


def test_an_address_the_reel_does_not_carry_asks():
    """The defect this avoids: a plan that names an item the recorded
    reel does not hold must become a QUESTION, not a silent write
    against whatever the id happens to name."""
    from library.tools import edit_spec

    timeline = {"available": True, "rows": edit_spec._timeline_rows(SNAPSHOT)}
    spec = {"program": {"kind": "edit_program", "reel": "Reel 26", "ops": [
        {"op": "clip.set_enabled", "params": {
            "source_file": "/footage/other.mov",
            "source_in_frame": 10, "source_out_frame": 20,
            "track_index": 1, "enabled": False}}]}}
    edit_spec._clarify_unlisted_program_targets(spec, timeline, SNAPSHOT)
    op = spec["program"]["ops"][0]
    assert op["status"] == "needs_clarification"
    assert "other.mov" in op["missing_referent"]
    assert op["question"]


def test_the_prompt_asks_for_the_material_and_never_for_an_id():
    """The defect: the prompt told the model to resolve each target to
    its observed `unique_id` - so the model-facing plan asked for the
    very identifier this lane removes."""
    from library.tools import edit_spec

    source = (ROOT / "library" / "tools" / "edit_spec.py").read_text(
        encoding="utf-8")
    assert "unique_id" not in source.split("def prepare_request")[1].split(
        "def load_response_spec")[0]


def test_the_measurement_reports_no_instance_id_and_an_unchanged_route():
    """The measurement is the lane's own gate: over the committed
    recorded corpus, the model-facing plan carries no Resolve
    identifier, and the recorded requests route exactly as they did."""
    result = subprocess.run(
        [sys.executable, "scripts/measure_model_plan_neutrality.py"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
        check=False)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["model_facing_context"]["resolve_specific_fields"] == 0
    assert report["model_facing_plan"]["resolve_specific_fields"] == 0
    assert report["routing"]["same_route"] is True
    assert report["routing"]["neutral_resolved_by_address"] == (
        report["routing"]["prepared"])
