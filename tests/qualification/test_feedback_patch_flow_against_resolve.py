"""`ren revise` against a REAL running Resolve.

Live proof for slice 3, only in Ren Qualification (the `resolve_session`
fixture: exclusive lease, qual project, skip rather than contend).

What it proves live: a pulled marker note enters through `ren revise`,
patches the existing "Q Markers" timeline in place, its source marker is
removed after verification, the green reply lands beside it, and cleanup
restores the timeline exactly as found.
"""

from __future__ import annotations

import os
import sys
import uuid
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.resolve_live

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from library.tools import feedback_patch_apply  # noqa: E402
from library.tools import llm_handshake  # noqa: E402
from library.tools import marker_feedback  # noqa: E402
from library.tools import revise  # noqa: E402
from library.tools import timeline_shadow as shadow  # noqa: E402

TIMELINE = "Q Markers"
BLUE_FRAME_KEY = 40
REPLY_FRAME_KEY = BLUE_FRAME_KEY + 1
PROBE_FRAME_KEY = 24
WORDS = "the subtitles should read 2021"

#: Unique per run: a repeated idempotency key replays the recorded
#: receipt instead of writing, so a fixed key proves only the first run.
RUN = uuid.uuid4().hex[:8]


def _timeline(project, name: str):
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() == name:
            return timeline
    pytest.skip(f"no timeline {name!r} in Ren Qualification - "
                f"`ren qualification reset` builds it")


def _marker_texts(timeline) -> dict:
    return {int(raw): dict(marker)
            for raw, marker in (timeline.GetMarkers() or {}).items()}


def _live_head_generation(project, timeline) -> int:
    """The shadow head for the LIVE handles, never a name lookup.

    The machine shadow store can hold several timeline ids that were
    all named "Q Markers" by other lanes' resets, so resolving the
    head by name refuses rather than picks one. The live handles
    already identify the exact timeline.
    """
    store = shadow.ShadowStore()
    project_name, timeline_id, _name = shadow.timeline_key(
        project, timeline)
    head = store.head(project_name, timeline_id)
    assert head is not None, "the planted patch observes the timeline first"
    return head.generation


def test_ren_revise_removes_pulled_source_marker_and_writes_reply(
        resolve_session, tmp_path, monkeypatch):
    from library.tools import edit_patch, resolve_lock
    from library.tools.marker_feedback import connect_resolve

    # A private shadow store, as the report's scratch broker used: the
    # machine store can hold several timeline ids all named "Q Markers"
    # from other lanes' resets, and `revise` resolves its reel by name.
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))

    resolve = connect_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    assert (project.GetName()
            == feedback_patch_apply.QUALIFICATION_PROJECT)
    timeline = _timeline(project, TIMELINE)
    resolve_lock.assert_current_timeline(project, timeline)

    start = int(timeline.GetStartFrame())
    span = int(timeline.GetEndFrame()) - start
    before = _marker_texts(timeline)
    backup_snapshot = shadow.read_live(project, timeline)
    for key in (BLUE_FRAME_KEY, REPLY_FRAME_KEY, PROBE_FRAME_KEY):
        if key in before or not 0 <= key < span:
            pytest.skip(f"frame key {key} is occupied or off {TIMELINE!r} "
                        f"- another run left a marker; refusing to touch it")
    assert (0, "Blue", "start") in [
        (key, marker.get("color"), marker.get("name"))
        for key, marker in before.items()], (
        f"the declared witnesses are not on {TIMELINE!r}")

    blue = {"op": "marker.add", "frame": BLUE_FRAME_KEY, "color": "Blue",
            "name": "feedback", "note": WORDS, "duration": 1,
            "custom_data": ""}
    try:
        planted = edit_patch.apply_live_patch(
            project=project, timeline=timeline,
            capability="reel.touchup", operations=[blue],
            conflict_domains=("markers",),
            idempotency_key=f"feedback-patch-flow-proof-blue-{RUN}")
        assert planted["status"] == "committed"

        root = tmp_path / "qualification-project"
        root.mkdir()
        (root / "project.yaml").write_text(
            'name: "Ren Qualification"\nslug: qualification\n'
            'project_format_version: 1\nresolve:\n  project_name: '
            '"Ren Qualification"\n', encoding="utf-8")
        marker_feedback.pull(str(root), timeline=timeline, project=project)
        pulled = next(note for note in revise._routed_notes(str(root))
                      if note.frame == start + BLUE_FRAME_KEY)
        note_id = pulled.note_id
        started_code = revise.main([str(root), "--note-id", note_id])
        assert started_code == 0
        record = revise.newest_open(str(root))
        assert record and record["note_id"] in (
            pulled.note_id, pulled.edit_link_id)
        program = {"kind": "edit_program", "reel": TIMELINE,
                   "base_generation": str(_live_head_generation(
                       project, timeline)),
                   "idempotency_key": f"ren-revise-live-{RUN}",
                   "anchor": {"kind": "reel"},
                   "ops": [{"op": "marker.add", "text": "proof patch",
                            "params": {"frame": PROBE_FRAME_KEY,
                                       "color": "Green", "name": "proof patch",
                                       "note": "proof", "duration": 1,
                                       "custom_data": ""}}]}
        Path(llm_handshake.response_path(str(root), record["spec_id"])) \
            .write_text(json.dumps({"format": "edit_spec/1",
                                    "request": pulled.text,
                                    "program": program}),
                        encoding="utf-8")
        result_code = revise.main([str(root)])
        assert result_code == 0
        revision = revise.newest_open(str(root))
        assert revision is None
        after = _marker_texts(timeline)
        assert BLUE_FRAME_KEY not in after, "verified source marker remains"
        green = after[REPLY_FRAME_KEY]
        assert green["color"] == "Green" and WORDS in (
            green.get("customData") or ""), (
            "no green reply beside the answered note")
    finally:
        live_markers = _marker_texts(timeline)
        cleanup_frames = [key for key in
                          (PROBE_FRAME_KEY, REPLY_FRAME_KEY, BLUE_FRAME_KEY)
                          if key in live_markers]
        if cleanup_frames:
            cleanup = feedback_patch_apply.guarded_apply(
                project=project, timeline=timeline,
                operations=[{"op": "marker.delete", "frame": frame}
                            for frame in cleanup_frames],
                scope={"clips": [], "marker_frames": cleanup_frames,
                       "clip_markers": []},
                idempotency_key=f"feedback-patch-flow-proof-cleanup-{RUN}")
            assert cleanup["outside_scope"] == []

    assert (feedback_patch_apply.inventory_changes(
        backup_snapshot, shadow.read_live(project, timeline)) == [])
    for key in (BLUE_FRAME_KEY, REPLY_FRAME_KEY, PROBE_FRAME_KEY):
        assert key not in _marker_texts(timeline)
