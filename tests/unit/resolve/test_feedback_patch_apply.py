"""The guarded feedback-patch apply: backup, scope, restore, refusal.

Offline first: snapshots are fabricated in `read_live` shape, Resolve is
faked at the `shadow`/`edit_patch` seam. Live proof is
`tests/qualification/test_feedback_patch_apply_against_resolve.py`, which
alone may touch Resolve - and only Ren Qualification, never the captain's
projects.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import feedback_patch_apply as apply_mod  # noqa: E402
from library.tools import timeline_shadow as shadow  # noqa: E402


def _clip(unique_id: str, **kw) -> dict:
    clip = {
        "unique_id": unique_id,
        "name": kw.get("name", f"clip_{unique_id}"),
        "record_in": kw.get("record_in", 0),
        "record_out": kw.get("record_out", 48),
        "duration": kw.get("duration", 48),
        "enabled": kw.get("enabled", True),
        "transform": dict(kw.get("transform") or {"Opacity": 100.0}),
        "markers": [dict(marker) for marker in kw.get("markers") or []],
        "track_type": "video",
        "track_index": 1,
    }
    return clip


def _marker(frame: int, color="Green", name="reply", note="done") -> dict:
    return {"frame": frame, "color": color, "name": name, "note": note,
            "duration": 1, "custom_data": ""}


def _snap(clips, markers=None) -> dict:
    return {
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": clips}],
        "markers": {"timeline": markers or [], "notes": []},
    }


@pytest.fixture
def before():
    return _snap([_clip("aaa"), _clip("bbb")],
                 markers=[_marker(10, color="Blue", name="feedback",
                                  note="fix the caption")])


# ── The gates that need no Resolve ───────────────────────────────────

def test_wrong_project_refuses_by_name():
    project = SimpleNamespace(GetName=lambda: "Podcast (field test)")
    with pytest.raises(apply_mod.WrongProject):
        apply_mod.require_qualification(project)


def test_qualification_project_passes():
    project = SimpleNamespace(GetName=lambda: "Ren Qualification")
    assert (apply_mod.require_qualification(project) == "Ren Qualification")


def test_scope_covers_names_the_gap():
    operations = [{"op": "marker.add", "frame": 24, "color": "Green",
                   "name": "reply", "note": "done"},
                  {"op": "clip.set_property", "unique_id": "zzz",
                   "key": "Opacity", "value": 90.0}]
    scope = {"clips": ["aaa"], "marker_frames": [24], "clip_markers": []}
    gaps = apply_mod.scope_covers(operations, scope)
    assert gaps == [{"op": "clip.set_property", "kind": "clip",
                     "locator": "zzz"}]


def test_scope_covers_empty_when_inside():
    operations = [{"op": "marker.add", "frame": 24, "color": "Green",
                   "name": "reply", "note": "done"}]
    scope = {"clips": [], "marker_frames": [24], "clip_markers": []}
    assert apply_mod.scope_covers(operations, scope) == []


def test_inventory_changes_empty_on_identical(before):
    assert apply_mod.inventory_changes(before, copy.deepcopy(before)) == []


def test_inventory_changes_sees_property_and_marker(before):
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][0]["transform"]["Opacity"] = 90.0
    after["markers"]["timeline"].append(_marker(24))
    changes = apply_mod.inventory_changes(before, after)
    assert {"where": "clip", "unique_id": "aaa", "change": "transform.Opacity",
            "before": 100.0, "after": 90.0} in changes
    assert {"where": "marker", "change": "added",
            "marker": (24, "Green", "reply", "done")} in changes


def test_changes_outside_scope_keeps_hand_edits(before):
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][0]["transform"]["Opacity"] = 90.0  # in scope
    after["tracks"][0]["clips"][1]["transform"]["Opacity"] = 50.0  # his edit
    scope = {"clips": ["aaa"], "marker_frames": [], "clip_markers": []}
    outside = apply_mod.changes_outside_scope(before, after, scope)
    assert outside == [{"where": "clip", "unique_id": "bbb",
                        "change": "transform.Opacity", "before": 100.0,
                        "after": 50.0}]


def test_inverse_restores_previous_property_value(before):
    operations = [{"op": "clip.set_property", "unique_id": "aaa",
                   "key": "Opacity", "value": 90.0}]
    assert apply_mod.inverse_operations(operations, before) == [
        {"op": "clip.set_property", "unique_id": "aaa",
         "key": "Opacity", "value": 100.0}]


def test_inverse_readds_deleted_marker_fields(before):
    operations = [{"op": "marker.delete", "frame": 10}]
    assert apply_mod.inverse_operations(operations, before) == [
        {"op": "marker.add", "frame": 10, "color": "Blue",
         "name": "feedback", "note": "fix the caption", "duration": 1,
         "custom_data": ""}]


def test_inverse_refuses_unrestorable_op(before):
    with pytest.raises(apply_mod.UnrestorableOp):
        apply_mod.inverse_operations([{"op": "clip.delete",
                                       "unique_id": "aaa"}], before)


def test_inverse_refuses_missing_before_state(before):
    with pytest.raises(apply_mod.UnrestorableOp):
        apply_mod.inverse_operations(
            [{"op": "clip.set_property", "unique_id": "ghost",
              "key": "Opacity", "value": 1.0}], before)


# ── guarded_apply against a fake Resolve ─────────────────────────────

class _FakeResolve:
    """A live snapshot plus paired quirks standing in for Resolve."""

    def __init__(self, snapshot, quirk=None, quirk_reverts=False):
        self.state = copy.deepcopy(snapshot)
        self.quirk = quirk
        self.quirk_reverts = quirk_reverts
        self.applied = []

    def apply(self, **kw):
        operations = kw["operations"]
        self.applied.append([dict(op) for op in operations])
        for op in operations:
            self._one(op)
        return {"status": "committed",
                "operations": len(operations)}

    def _one(self, op):
        op_id = op["op"]
        if op_id == "marker.add":
            self.state["markers"]["timeline"] = [
                marker for marker in self.state["markers"]["timeline"]
                if int(marker["frame"]) != int(op["frame"])]
            self.state["markers"]["timeline"].append({
                "frame": int(op["frame"]), "color": op["color"],
                "name": op["name"], "note": op.get("note", ""),
                "duration": int(op.get("duration", 1)),
                "custom_data": op.get("custom_data", "")})
        elif op_id == "marker.delete":
            self.state["markers"]["timeline"] = [
                marker for marker in self.state["markers"]["timeline"]
                if int(marker["frame"]) != int(op["frame"])]
        elif op_id == "clip.set_property":
            clip = next(clip for clip in
                        self.state["tracks"][0]["clips"]
                        if clip["unique_id"] == op["unique_id"])
            clip["transform"][op["key"]] = op["value"]
        if self.quirk is not None:
            self.quirk(self.state, op)
        if self.quirk_reverts and op.get("op", "").endswith("delete"):
            pass


@pytest.fixture
def live(monkeypatch, before):
    from library.tools import resolve_lock
    from library.tools import edit_patch

    fake = _FakeResolve(before)
    project = SimpleNamespace(GetName=lambda: "Ren Qualification")
    timeline = SimpleNamespace()
    monkeypatch.setattr(resolve_lock, "exclusive_held", lambda: True)
    monkeypatch.setattr(shadow, "read_live",
                        lambda *args, **kw: copy.deepcopy(fake.state))
    monkeypatch.setattr(shadow, "observe",
                        lambda *args, **kw: SimpleNamespace(generation=7))
    monkeypatch.setattr(shadow, "snapshot_hash", lambda snapshot: "hash")
    monkeypatch.setattr(edit_patch, "apply_live_patch", fake.apply)
    return SimpleNamespace(project=project, timeline=timeline, fake=fake)


def test_guarded_apply_commits_in_scope_change(live):
    receipt = apply_mod.guarded_apply(
        project=live.project, timeline=live.timeline,
        operations=[{"op": "marker.add", "frame": 24, "color": "Green",
                     "name": "reply", "note": "done"}],
        scope={"clips": [], "marker_frames": [24], "clip_markers": []},
        idempotency_key="test-1")
    assert receipt["outside_scope"] == []
    assert receipt["patch"]["status"] == "committed"
    assert receipt["backup"] == {"generation": 7, "hash": "hash"}


def test_guarded_apply_refuses_out_of_scope_before_writing(live):
    with pytest.raises(apply_mod.PatchRefused):
        apply_mod.guarded_apply(
            project=live.project, timeline=live.timeline,
            operations=[{"op": "marker.add", "frame": 99, "color": "Green",
                         "name": "reply", "note": "done"}],
            scope={"clips": [], "marker_frames": [24], "clip_markers": []},
            idempotency_key="test-2")
    assert live.fake.applied == []  # Resolve never written


def test_guarded_apply_restores_and_reports_outside_movement(
        monkeypatch, live, before):
    def quirk(state, op):
        # A paired Resolve side effect: adding a marker flips an
        # out-of-scope clip's transform; deleting one flips it back.
        for clip in state["tracks"][0]["clips"]:
            if clip["unique_id"] == "bbb":
                clip["transform"]["Opacity"] = (
                    50.0 if op["op"] == "marker.add" else 100.0)

    live.fake.quirk = quirk
    with pytest.raises(apply_mod.PatchMovedTooMuch) as excinfo:
        apply_mod.guarded_apply(
            project=live.project, timeline=live.timeline,
            operations=[{"op": "marker.add", "frame": 24, "color": "Green",
                         "name": "reply", "note": "done"}],
            scope={"clips": [], "marker_frames": [24], "clip_markers": []},
            idempotency_key="test-3")
    assert excinfo.value.outside == [
        {"where": "clip", "unique_id": "bbb", "change": "transform.Opacity",
         "before": 100.0, "after": 50.0}]
    # Restored: the live state matches the backup inventory again.
    assert apply_mod.inventory_changes(before, live.fake.state) == []
    assert excinfo.value.backup["snapshot"] == before


def test_guarded_apply_names_unrestorable_residue(monkeypatch, live):
    def quirk(state, op):
        if op["op"] == "marker.add":  # one-way side effect, never reverted
            for clip in state["tracks"][0]["clips"]:
                if clip["unique_id"] == "bbb":
                    clip["transform"]["Opacity"] = 50.0

    live.fake.quirk = quirk
    with pytest.raises(apply_mod.PatchRestoreFailed) as excinfo:
        apply_mod.guarded_apply(
            project=live.project, timeline=live.timeline,
            operations=[{"op": "marker.add", "frame": 24, "color": "Green",
                         "name": "reply", "note": "done"}],
            scope={"clips": [], "marker_frames": [24], "clip_markers": []},
            idempotency_key="test-4")
    assert excinfo.value.residue == [
        {"where": "clip", "unique_id": "bbb", "change": "transform.Opacity",
         "before": 100.0, "after": 50.0}]


# ── The offline audit caller ─────────────────────────────────────────

def test_check_cli_clean_and_dirty(tmp_path, before):
    after = copy.deepcopy(before)
    after["markers"]["timeline"].append(_marker(24))
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    scope_path = tmp_path / "scope.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")
    scope_path.write_text(json.dumps(
        {"clips": [], "marker_frames": [24], "clip_markers": []}),
        encoding="utf-8")
    assert apply_mod.main(["check", "--before", str(before_path),
                           "--after", str(after_path),
                           "--scope", str(scope_path)]) == 0
    scope_path.write_text(json.dumps(
        {"clips": [], "marker_frames": [], "clip_markers": []}),
        encoding="utf-8")
    assert apply_mod.main(["check", "--before", str(before_path),
                           "--after", str(after_path),
                           "--scope", str(scope_path)]) == 1
