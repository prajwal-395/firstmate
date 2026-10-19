"""VC-08: preparation explains a proposed edit without modifying anything.

Preparation is read-only: it resolves identities against a recorded
snapshot, computes affected scope from the algebra, detects
no-write-satisfiable requests and states unresolved assumptions. These
tests run hermetic - a plain snapshot dict, no Resolve, no store, no
model downloads - and prove the base snapshot is left untouched.
"""
from __future__ import annotations

import copy

from library.tools import edit_preparation
from library.tools.edit_request import (
    EditOperation,
    EditRequest,
    EditTarget,
    from_touchup_spec,
)


def _clip(uid="clip-1", name="a.mov", start=0, end=48, pan=0.0,
          enabled=True, track_type="video", track_index=1):
    return {"unique_id": uid, "name": name,
            "record_in": start, "record_out": end, "duration": end - start,
            "transform": {"Pan": pan}, "enabled": enabled, "markers": [],
            "track_type": track_type, "track_index": track_index}


def _base(*clips, markers=()):
    return {
        "start_frame": 0, "end_frame": 200,
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": list(clips)}],
        "markers": {"timeline": list(markers)},
    }


def _request(*ops, base_generation=9):
    return EditRequest.from_dict({
        "id": "req-1", "project": "Podcast", "timeline": "Reel 09",
        "expected_base_generation": base_generation,
        "operations": [op.to_dict() for op in ops]})


def _set_pan(uid="clip-1", value=5.0):
    return EditOperation(op="clip.set_property",
                         target=EditTarget(unique_id=uid),
                         params={"key": "Pan", "value": value},
                         expected_effect="nudge the interview frame")


def test_prepare_resolves_scope_and_stays_ready():
    base = _base(_clip(), _clip(uid="clip-2", name="b.mov",
                                start=48, end=96))
    result = edit_preparation.prepare(
        _request(_set_pan()), base_snapshot=base, base_generation=9)
    assert result.ready, result.refusals
    assert result.base_generation == 9
    assert result.resolved[0].status == "resolved"
    assert result.affected.clips == ("clip-1",)
    assert result.affected.spans == ((0, 48),)
    # The neighbour on the same span is a dependent, not a target.
    assert result.affected.dependents == ()
    assert result.plan["steps"][0]["op"] == "clip.set_property"
    assert not result.no_write_needed


def test_prepare_detects_dependent_clips_on_overlapping_spans():
    base = _base(_clip(end=60), _clip(uid="clip-2", name="b.mov",
                                      start=40, end=96))
    result = edit_preparation.prepare(
        _request(_set_pan()), base_snapshot=base, base_generation=9)
    assert result.ready, result.refusals
    assert result.affected.dependents == ("clip-2",)


def test_prepare_detects_no_write_satisfiable():
    base = _base(_clip(pan=5.0))
    result = edit_preparation.prepare(
        _request(_set_pan(value=5.0)), base_snapshot=base,
        base_generation=9)
    assert result.ready, result.refusals
    assert result.no_write_needed
    assert "already satisfies" in result.no_write.reason
    text = edit_preparation.explain(result)
    assert "NO WRITE NEEDED" in text


def test_prepare_refuses_a_target_missing_from_the_base():
    base = _base(_clip())
    result = edit_preparation.prepare(
        _request(_set_pan(uid="clip-9")), base_snapshot=base,
        base_generation=9)
    assert not result.ready
    assert any("clip-9" in refusal for refusal in result.refusals)
    assert "REFUSED" in edit_preparation.explain(result)


def test_prepare_states_a_name_matched_assumption():
    base = _base(_clip())
    op = EditOperation(op="clip.set_enabled",
                       target=EditTarget(name="a.mov", track_type="video",
                                         track_index=1),
                       params={"enabled": False},
                       expected_effect="switch the cutaway off")
    result = edit_preparation.prepare(
        _request(op), base_snapshot=base, base_generation=9)
    assert result.ready, result.refusals
    assert result.resolved[0].status == "assumed"
    assert result.resolved[0].unique_id == "clip-1"
    assert any("by name" in note for note in result.assumptions)


def test_prepare_without_a_base_is_not_ready():
    result = edit_preparation.prepare(_request(_set_pan()))
    assert not result.ready
    assert result.refusals


def test_prepare_leaves_the_base_snapshot_untouched():
    base = _base(_clip())
    frozen = copy.deepcopy(base)
    result = edit_preparation.prepare(
        _request(_set_pan()), base_snapshot=base, base_generation=9)
    assert base == frozen
    # Explaining is a second read with no write behind it.
    edit_preparation.explain(result)
    assert base == frozen


def test_prepare_marks_ambiguous_names_unplannable():
    base = _base(_clip(uid="clip-1"), _clip(uid="clip-2"))
    op = EditOperation(op="clip.set_enabled",
                       target=EditTarget(name="a.mov"),
                       params={"enabled": False},
                       expected_effect="switch it off")
    result = edit_preparation.prepare(
        _request(op), base_snapshot=base, base_generation=9)
    assert not result.ready
    assert any("matches 2 clips" in refusal for refusal in result.refusals)


def test_prepare_resolves_track_position_and_binds_it():
    base = _base(_clip())
    request = from_touchup_spec(
        {"reel": 9, "edits": [
            {"op": "set_properties", "row": "V1", "item": 0,
             "properties": {"Pan": 5.0},
             "expected_effect": "nudge the interview"}]},
        project="Podcast", timeline="Reel 09")
    result = edit_preparation.prepare(
        request, base_snapshot=base, base_generation=9)
    assert result.ready, result.refusals
    assert result.resolved[0].status == "assumed"
    assert result.resolved[0].unique_id == "clip-1"
    assert any("position 0" in note for note in result.assumptions)
    assert any("filled from assumed resolution" in note
               for note in result.assumptions)
    assert result.affected.spans == ((0, 48),)
    assert not result.no_write_needed
    assert "Pan reads 0.0, wants 5.0" in result.no_write.reason
    assert "write needed" in edit_preparation.explain(result)


def test_prepare_refuses_position_outside_the_row():
    base = _base(_clip())
    request = from_touchup_spec(
        {"reel": 9, "edits": [
            {"op": "set_properties", "row": "V1", "item": 4,
             "properties": {"Pan": 5.0},
             "expected_effect": "nudge the interview"}]},
        project="Podcast", timeline="Reel 09")
    result = edit_preparation.prepare(
        request, base_snapshot=base, base_generation=9)
    assert not result.ready
    assert any("outside" in refusal for refusal in result.refusals)


def test_plan_is_machine_readable_and_ordered():
    first = EditOperation(op="marker.add", target=None,
                          params={"frame": 10, "color": "red", "name": "m"},
                          expected_effect="mark the beat")
    second = _set_pan()
    request = _request(first, second)
    result = edit_preparation.prepare(
        request, base_snapshot=_base(_clip()), base_generation=9)
    assert result.ready, result.refusals
    assert result.plan["order"] == [0, 1]
    assert [step["op"] for step in result.plan["steps"]] == [
        "marker.add", "clip.set_property"]
    assert result.plan["request_id"] == "req-1"
    as_dict = result.as_dict()
    assert as_dict["ready"] and not as_dict["no_write_needed"]
