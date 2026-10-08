"""Offline proof that hand edits remain the reconciliation base."""
from __future__ import annotations

from library.tools import timeline_reconciliation as reconciliation
from library.tools import timeline_shadow


def _snapshot(*, a_in=0, a_out=24, a_source_in=0, a_source_out=24,
              a_transform=None, include_a=True, b_in=24):
    clips = []
    if include_a:
        clips.append({
            "unique_id": "a", "name": "A", "record_in": a_in,
            "record_out": a_out, "source_in_frame": a_source_in,
            "source_out_frame": a_source_out, "track_index": 1,
            "track_type": "video", "enabled": True,
            "transform": a_transform or {"ZoomX": 1.0, "Pan": 0.0},
            "markers": [],
        })
    clips.append({
        "unique_id": "b", "name": "B", "record_in": b_in,
        "record_out": b_in + 24, "source_in_frame": 0,
        "source_out_frame": 24, "track_index": 1,
        "track_type": "video", "enabled": True,
        "transform": {"ZoomX": 1.0}, "markers": [],
    })
    return {"timeline": "Draft", "start_frame": 0, "end_frame": 48,
            "tracks": [{"type": "video", "index": 1,
                        "name": "V1", "clips": clips}],
            "markers": {"timeline": [], "notes": []}}


def _run(base, current, *operations):
    return reconciliation.reconcile(
        approved_snapshot=base, current_snapshot=current,
        approved_generation=4, current_generation=5,
        ren_operations=operations)


def test_human_move_is_attributed_and_same_move_becomes_variant():
    result = _run(_snapshot(), _snapshot(a_in=8, a_out=32, b_in=32),
                  {"op": "clip.move", "unique_id": "a", "to_frame": 12})
    assert result["human_changes"]["a"] == ["record_in", "record_out"]
    assert result["status"] == "needs_creator_choice"
    assert result["patch"] is None
    assert result["variant"]["operations"] == [
        {"op": "clip.move", "unique_id": "a", "to_frame": 12}]
    assert result["question"] == (
        "Your timeline edit to a is preserved. Should I also apply the "
        "conflicting Ren change as a separate variant?")


def test_human_trim_conflicts_with_trim_but_keeps_human_bounds():
    result = _run(_snapshot(), _snapshot(a_out=20, a_source_out=20),
                  {"op": "clip.trim", "unique_id": "a",
                   "in_frame": 0, "out_frame": 18})
    assert result["human_changes"]["a"] == ["record_out", "source_out_frame"]
    assert result["variant"] is not None


def test_human_transform_conflicts_only_on_same_transform_property():
    result = _run(_snapshot(), _snapshot(a_transform={"ZoomX": 1.0, "Pan": 9.0}),
                  {"op": "clip.set_property", "unique_id": "a",
                   "key": "ZoomX", "value": 2.0},
                  {"op": "clip.set_property", "unique_id": "a",
                   "key": "Pan", "value": 2.0})
    assert result["patch"]["operations"] == [{
        "op": "clip.set_property", "unique_id": "a",
        "key": "ZoomX", "value": 2.0}]
    assert result["variant"]["operations"] == [{
        "op": "clip.set_property", "unique_id": "a",
        "key": "Pan", "value": 2.0}]


def test_human_deletion_is_preserved_and_ren_replacement_is_variant():
    result = _run(_snapshot(), _snapshot(include_a=False),
                  {"op": "clip.move", "unique_id": "a", "to_frame": 12})
    assert result["human_changes"]["a"] == ["deleted"]
    assert result["patch"] is None
    assert result["variant"]["operations"][0]["unique_id"] == "a"


def test_mixed_independent_human_and_ren_changes_yield_targeted_patch():
    result = _run(_snapshot(), _snapshot(a_in=8, a_out=32, b_in=32),
                  {"op": "clip.set_property", "unique_id": "b",
                   "key": "ZoomX", "value": 1.2})
    assert result["status"] == "ready"
    assert result["patch"] == {
        "base_generation": 5,
        "operations": [{"op": "clip.set_property", "unique_id": "b",
                        "key": "ZoomX", "value": 1.2}],
    }
    assert result["variant"] is None and result["question"] is None


def test_generation_comparison_attributes_observed_human_edit(tmp_path):
    store = timeline_shadow.ShadowStore(tmp_path / "shadow.sqlite3")
    base = _snapshot()
    current = _snapshot(a_in=8, a_out=32, b_in=32)
    store.record(project="P", timeline_id="T", timeline_name="Draft",
                 snapshot=base, source=timeline_shadow.PATCH, expected_head=0,
                 patch={"id": "approved"}, receipt={"status": "applied"})
    store.record(project="P", timeline_id="T", timeline_name="Draft",
                 snapshot=current, source=timeline_shadow.OBSERVED,
                 expected_head=1)
    result = reconciliation.reconcile_generations(
        store=store, project="P", timeline_id="T", approved_generation=1,
        ren_operations=[{"op": "clip.set_property", "unique_id": "b",
                         "key": "ZoomX", "value": 1.2}])
    assert result["attribution"] == {
        "approved_generation": 1, "current_generation": 2,
        "external_generations": [2], "agent_generations": [],
    }
    assert result["patch"]["base_generation"] == 2
