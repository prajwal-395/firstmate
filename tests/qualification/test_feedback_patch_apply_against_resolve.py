"""`feedback_patch_apply.guarded_apply` against a REAL running Resolve.

Live proof for slice 2, and only in Ren Qualification
(`qualification_project.borrowed()` discipline via the `resolve_session`
fixture: exclusive lease, qual project, skip naming the holder rather
than waiting). Never the captain's projects - `require_qualification`
refuses those by name, and this file never names one.

What it proves live (the fakes in `unit/resolve/test_feedback_patch_apply.py`
cannot): a marker patch commits on the existing "Q Markers" timeline,
the declared witnesses (Blue@0 and Green@48) read back identical, an
out-of-scope target is refused with nothing written, and the timeline
is left exactly as found.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

pytestmark = pytest.mark.resolve_live

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from library.tools import feedback_patch_apply as apply_mod  # noqa: E402
from library.tools import timeline_shadow as shadow  # noqa: E402

TIMELINE = "Q Markers"
PROBE_FRAME = 24
WITNESSES = {(0, "Blue", "start"), (48, "Green", "mid")}

#: Unique per run. EditPatch replays a repeated idempotency key with the
#: recorded receipt instead of writing, so a fixed key proves only the
#: first run - every later run must write for real.
RUN = uuid.uuid4().hex[:8]


def _timeline(project, name: str):
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() == name:
            return timeline
    pytest.skip(f"no timeline {name!r} in Ren Qualification - "
                f"`ren qualification reset` builds it")


def _witnesses_of(snapshot: dict) -> set:
    return {(int(marker["frame"]), marker.get("color") or "",
             marker.get("name") or "")
            for marker in (snapshot.get("markers") or {}).get("timeline", [])}


def test_guarded_apply_patches_in_place_and_leaves_the_rest(
        resolve_session):
    from library.tools import resolve_lock
    from library.tools.marker_feedback import connect_resolve

    resolve = connect_resolve()
    project = resolve.GetProjectManager().GetCurrentProject()
    assert (project.GetName()
            == apply_mod.QUALIFICATION_PROJECT), project.GetName()
    timeline = _timeline(project, TIMELINE)
    resolve_lock.assert_current_timeline(project, timeline)

    made = apply_mod.backup(project, timeline)
    if PROBE_FRAME in {frame for frame, _, _ in
                       _witnesses_of(made["snapshot"])}:
        pytest.skip(f"frame {PROBE_FRAME} already carries a marker on "
                    f"{TIMELINE!r} - another run left one; refusing to "
                    f"replace what is not mine")
    if not WITNESSES <= _witnesses_of(made["snapshot"]):
        pytest.skip(f"the declared witnesses are not all on {TIMELINE!r} - "
                    f"another qual run is holding it mid-change; refusing "
                    f"to judge their state")

    scope = {"clips": [], "marker_frames": [PROBE_FRAME],
             "clip_markers": []}
    receipt = apply_mod.guarded_apply(
        project=project, timeline=timeline,
        operations=[{"op": "marker.add", "frame": PROBE_FRAME,
                     "color": "Green", "name": "patch-proof",
                     "note": "feedback-patch slice 2 live proof"}],
        scope=scope, idempotency_key=f"feedback-patch-slice-2-proof-{RUN}")
    assert receipt["outside_scope"] == []

    try:
        # An out-of-scope target is refused with nothing written.
        before = shadow.snapshot_hash(
            shadow.read_live(project, timeline))
        with pytest.raises(apply_mod.PatchRefused):
            apply_mod.guarded_apply(
                project=project, timeline=timeline,
                operations=[{"op": "marker.add", "frame": PROBE_FRAME + 1,
                             "color": "Green", "name": "must-not-land",
                             "note": "out of scope"}],
                scope=scope,
                idempotency_key=f"feedback-patch-slice-2-refuse-{RUN}")
        assert (shadow.snapshot_hash(shadow.read_live(project, timeline))
                == before), "a refused patch wrote to the timeline"
    finally:
        # The timeline is left exactly as found.
        cleanup = apply_mod.guarded_apply(
            project=project, timeline=timeline,
            operations=[{"op": "marker.delete", "frame": PROBE_FRAME}],
            scope=scope,
            idempotency_key=f"feedback-patch-slice-2-cleanup-{RUN}")
        assert cleanup["outside_scope"] == []

    assert (apply_mod.inventory_changes(
        made["snapshot"], shadow.read_live(project, timeline)) == [])
