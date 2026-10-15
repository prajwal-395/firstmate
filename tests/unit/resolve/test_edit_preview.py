"""A proxy preview shows what a program will do before it executes.

The gap this closes (gap map C-05): an edit program executes blind; the
first evidence it was wrong is a rendered export. The preview computes,
in pure Python over a recorded generation, the affected spans,
before/after values and duration change for each op.
"""
from __future__ import annotations

import time

import pytest

from library.tools import edit_algebra, edit_patch, edit_preview
from library.tools import timeline_shadow as shadow
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeResolve,
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)


@pytest.fixture
def world(tmp_path):
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    item = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
        yield project, timeline, item, store, base


def _patch_ops(preview_ops):
    """Convert preview ops (op + params) to patch operations (flat)."""
    return [{"op": op["op"], **op["params"]} for op in preview_ops]


def _apply(patch, project, timeline, store):
    return edit_patch.apply_patch(
        patch, resolve=FakeResolve(project),
        project=project, timeline=timeline,
        store=store,
        project_folder=store.path.parent)


def _patch_dict(pid, base, operations, domains, spans):
    return {"id": pid, "project": "Podcast", "timeline": "Reel 09",
            "base_generation": base, "capability": "reel.touchup",
            "affected_spans": spans, "conflict_domains": domains,
            "operations": operations}


def test_preview_shows_affected_spans_before_after_and_duration_change(world):
    """A 3-op program previews the spans, values and duration change it
    will produce - the editor approves or revises before it executes."""
    _project, _timeline, item, store, base = world
    uid = item.GetUniqueId()
    ops = [
        {"op": "clip.set_property", "params": {
            "unique_id": uid, "key": "ZoomX", "value": 1.4}},
        {"op": "clip.set_enabled", "params": {
            "unique_id": uid, "enabled": False}},
        {"op": "marker.add", "params": {
            "frame": 12, "color": "Blue", "name": "beat"}},
    ]
    preview = edit_preview.preview_program(ops, store.snapshot(base))

    assert preview["format"] == edit_preview.PREVIEW_FORMAT
    assert len(preview["ops"]) == 3

    prop = preview["ops"][0]
    assert prop["op"] == "clip.set_property"
    assert prop["affected_span"] == [0, 48]
    assert prop["before"] == {"ZoomX": 1.0}
    assert prop["after"] == {"ZoomX": 1.4}
    assert prop["duration_change"] == 0

    enabled = preview["ops"][1]
    assert enabled["op"] == "clip.set_enabled"
    assert enabled["affected_span"] == [0, 48]
    assert enabled["before"] == {"enabled": True}
    assert enabled["after"] == {"enabled": False}
    assert enabled["duration_change"] == 0

    marker = preview["ops"][2]
    assert marker["op"] == "marker.add"
    assert marker["affected_span"] == [12, 13]
    assert marker["before"] is None
    assert marker["after"]["name"] == "beat"
    assert marker["duration_change"] == 0

    assert preview["total_duration_change"] == 0
    assert preview["resulting_duration"] == 48


def test_preview_matches_the_committed_patch_readback(world):
    """The preview's affected spans and before/after values match what
    the committed patch's read-back shows - the editor sees what will
    happen, not a promise."""
    project, timeline, item, store, base = world
    uid = item.GetUniqueId()
    ops = [
        {"op": "clip.set_property", "params": {
            "unique_id": uid, "key": "ZoomX", "value": 1.4}},
        {"op": "clip.set_enabled", "params": {
            "unique_id": uid, "enabled": False}},
        {"op": "marker.add", "params": {
            "frame": 12, "color": "Blue", "name": "beat"}},
    ]
    preview = edit_preview.preview_program(ops, store.snapshot(base))

    patch = _patch_dict(
        "preview-fidelity", base.generation,
        _patch_ops(ops),
        ["picture_transform", "timeline_structure", "markers"],
        [[0, 48], [12, 13]],
    )
    receipt = _apply(patch, project, timeline, store)
    assert receipt["status"] == "committed", receipt

    head = store.head("Podcast", base.timeline_id)
    after_snap = store.snapshot(head)
    committed_clip = edit_algebra._clip(after_snap, uid)
    committed_marker = edit_algebra._timeline_marker(after_snap, 12)

    prop = preview["ops"][0]
    assert prop["after"]["ZoomX"] == committed_clip["transform"]["ZoomX"]

    enabled = preview["ops"][1]
    assert enabled["after"]["enabled"] == committed_clip["enabled"]

    marker = preview["ops"][2]
    assert marker["after"]["name"] == committed_marker["name"]
    assert marker["after"]["color"] == committed_marker["color"]


def test_preview_ripple_shows_duration_change(world):
    """A ripple op previews the duration change it will produce - the
    editor sees the timeline will grow before it executes."""
    _project, _timeline, item, store, base = world
    uid = item.GetUniqueId()
    ops = [
        {"op": "clip.ripple", "params": {
            "unique_id": uid, "delta_frames": 30}},
    ]
    preview = edit_preview.preview_program(ops, store.snapshot(base))

    ripple = preview["ops"][0]
    assert ripple["op"] == "clip.ripple"
    assert ripple["affected_span"] == [0, 48]
    assert ripple["before"] == {"record_in": 0, "record_out": 48}
    assert ripple["after"] == {"record_in": 30, "record_out": 78}
    assert ripple["duration_change"] == 30
    assert preview["total_duration_change"] == 30
    assert preview["resulting_duration"] == 78


def test_preview_refuses_an_unknown_op(world):
    """An op the algebra does not hold is refused by name, not silently
    skipped - the editor cannot approve what cannot be previewed."""
    _project, _timeline, _item, store, base = world
    with pytest.raises(edit_preview.PreviewError, match="not an algebra op"):
        edit_preview.preview_program(
            [{"op": "no.such_op", "params": {}}], store.snapshot(base))


def test_preview_refuses_missing_required_params(world):
    """An op missing its required params is refused by name - the editor
    cannot approve what cannot be computed."""
    _project, _timeline, item, store, base = world
    uid = item.GetUniqueId()
    with pytest.raises(edit_preview.PreviewError, match="missing required"):
        edit_preview.preview_program(
            [{"op": "clip.trim", "params": {"unique_id": uid}}],
            store.snapshot(base))


def test_preview_is_pure_python_under_500ms_for_10_ops_500_clips(tmp_path):
    """The preview is pure Python over a snapshot: < 500 ms of process
    CPU for a 10-op program against a 500-clip generation. No Resolve
    read - the base here is a plain dict, so a Resolve call would fail
    rather than slow the test.

    CPU time, not wall time, because the requirement is on the
    algorithm's own cost: wall time bills in scheduler delays, swap
    churn and loaded-machine contention (measured 1381.7 ms wall on a
    machine with 139 MB free while the work itself was unchanged). CPU
    time keeps the bound while the host's load stops being the test."""
    clips = []
    for i in range(500):
        clips.append({
            "unique_id": f"clip-{i}",
            "name": f"clip-{i}",
            "track_type": "video",
            "track_index": 1,
            "record_in": i * 48,
            "record_out": (i + 1) * 48,
            "duration": 48,
            "source_in_frame": 0,
            "source_out_frame": 48,
            "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0,
                          "ZoomY": 1.0},
            "enabled": True,
            "speed": 100,
            "keyframes": {},
            "markers": [],
        })
    base = {
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": clips}],
        "markers": {"timeline": [], "notes": []},
        "start_frame": 0,
        "end_frame": 500 * 48,
    }
    ops = [
        {"op": "clip.trim", "params": {
            "unique_id": f"clip-{i}", "in_frame": i * 48 + 5,
            "out_frame": (i + 1) * 48 - 5}}
        for i in range(10)
    ]
    start = time.process_time()
    preview = edit_preview.preview_program(ops, base)
    elapsed_ms = (time.process_time() - start) * 1000

    assert len(preview["ops"]) == 10
    assert elapsed_ms < 500, (
        f"preview took {elapsed_ms:.1f} ms of CPU for 10 ops against 500 "
        f"clips; the requirement is < 500 ms")
