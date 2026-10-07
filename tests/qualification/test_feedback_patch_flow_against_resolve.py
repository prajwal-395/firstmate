"""`feedback_patch_flow.handle_note` against a REAL running Resolve.

Live proof for slice 3, only in Ren Qualification (the `resolve_session`
fixture: exclusive lease, qual project, skip rather than contend).

What it proves live: a patchable note patches the existing "Q Markers"
timeline in place, the planted blue marker reads back untouched, the
green reply lands beside it only after the verified record, and the
timeline is left exactly as found.
"""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import asdict

import pytest

pytestmark = pytest.mark.resolve_live

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from library.tools import feedback_patch  # noqa: E402
from library.tools import feedback_patch_apply  # noqa: E402
from library.tools import feedback_patch_flow as flow  # noqa: E402
from library.tools import marker_resolution as resolution  # noqa: E402
from library.tools import marker_routing as routing  # noqa: E402
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


def test_patch_flow_leaves_blue_and_writes_green(
        resolve_session, tmp_path):
    from library.tools import edit_patch, resolve_lock
    from library.tools.marker_feedback import connect_resolve

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

        raw = {
            "source": "timeline_marker", "name": "feedback", "note": WORDS,
            "text": f"feedback\n\n{WORDS}",
            "frame": start + BLUE_FRAME_KEY,
            "timecode": "00:00:00:00",
            "frame_in_timeline_space": BLUE_FRAME_KEY,
            "unplaced_reason": "", "color": "Blue", "duration_frames": 1,
            "custom_data": {}, "custom_data_raw": "", "clips": [],
            "attachments": [], "read_at": "",
        }
        routed = asdict(routing.route_note(raw, TIMELINE, "proof"))
        assert routed["steps"] == ["plan_subtitles"], (
            f"the proof note must route patchable, routed {routed}")
        assert feedback_patch.decide(routed)["verdict"] == "patch"
        out = flow.handle_note(
            routed_note=routed, project_folder=str(tmp_path),
            operations=[{"op": "marker.add", "frame": PROBE_FRAME_KEY,
                         "color": "Green", "name": "proof patch",
                         "note": "proof", "duration": 1,
                         "custom_data": ""}],
            scope={"clips": [], "marker_frames": [PROBE_FRAME_KEY],
                   "clip_markers": []},
            idempotency_key=f"feedback-patch-flow-proof-{RUN}",
            project=project, timeline=timeline)
        assert out["action"] == flow.ACTION_PATCHED
        assert (out["record"]["status"] == "resolved_verified"
                and out["record"]["check"]
                == resolution.CHECK_FEEDBACK_PATCH)

        after = _marker_texts(timeline)
        assert after[BLUE_FRAME_KEY]["note"] == WORDS, (
            "the planted blue marker must read back untouched")
        green = after[REPLY_FRAME_KEY]
        assert green["color"] == "Green" and WORDS in (
            green.get("customData") or ""), (
            "no green reply beside the answered note")
    finally:
        cleanup = feedback_patch_apply.guarded_apply(
            project=project, timeline=timeline,
            operations=[{"op": "marker.delete", "frame": PROBE_FRAME_KEY},
                        {"op": "marker.delete", "frame": REPLY_FRAME_KEY},
                        {"op": "marker.delete", "frame": BLUE_FRAME_KEY}],
            scope={"clips": [],
                   "marker_frames": [PROBE_FRAME_KEY, REPLY_FRAME_KEY,
                                     BLUE_FRAME_KEY],
                   "clip_markers": []},
            idempotency_key=f"feedback-patch-flow-proof-cleanup-{RUN}")
        assert cleanup["outside_scope"] == []

    assert (feedback_patch_apply.inventory_changes(
        backup_snapshot, shadow.read_live(project, timeline)) == [])
    for key in (BLUE_FRAME_KEY, REPLY_FRAME_KEY, PROBE_FRAME_KEY):
        assert key not in _marker_texts(timeline)
