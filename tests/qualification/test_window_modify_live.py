"""Window modification closed live in Ren Qualification.

Builds its own scratch timeline (named for this run, deleted after):
one qual clip, one appended window node, then the modify recipe sets
the Size field and the panel reads it back. The re-read IS the gate -
a typed value the panel does not show is refused, which is what makes
the field-behavior assumption (click focuses and selects all; Return
commits) fail closed instead of silent.

Drives the live Resolve: skipped unless `REN_LIVE_RESOLVE=1` inside a
granted borrow-resolve window (`resolve_session` holds the whole file's
lease, one live take at a time across lanes). Panel clicks and typing
need the foreground - the runner refuses a locked screen instead of
typing blind.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.resolve_live

SCRATCH_PREFIX = "Q WindowModify "


def _pool_clip(pool, stem):
    root = pool.GetRootFolder()
    for item in root.GetClipList() or []:
        name = item.GetName() or ""
        if name == stem or name.rsplit(".", 1)[0] == stem:
            return item
    return None


def test_set_size_reads_back_on_the_panel(resolve_session, tmp_path):
    """The modify loop closed live: append a window node, set Size to
    75.00, and the panel reads 75.00 back."""
    from library.tools.computer_use.client import DriverClient
    from library.tools.computer_use.scripts import (
        Anchor,
        Script,
        ScriptStep,
        run_script,
    )
    from library.tools.computer_use.window_modify import run_window_modify
    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import borrowed

    resolve = connect_resolve()
    with borrowed(resolve):
        manager = resolve.GetProjectManager()
        project = manager.LoadProject("Ren Qualification")
        assert project.GetName() == "Ren Qualification"
        pool = project.GetMediaPool()
        clip = _pool_clip(pool, "qual_b")
        assert clip is not None, "no qual_b in the pool - reset first"
        name = SCRATCH_PREFIX + uuid.uuid4().hex[:8]
        timeline = pool.CreateEmptyTimeline(name)
        assert timeline is not None, f"could not create {name!r}"
        try:
            project.SetCurrentTimeline(timeline)
            assert pool.AppendToTimeline([clip]), "could not place qual_b"
            items = timeline.GetItemListInTrack("video", 1) or []
            assert len(items) == 1, f"scratch holds {len(items)} clips"
            item = items[0]
            uid = item.GetUniqueId()
            assert int(item.GetNumNodes()) == 1, "fresh clip has no 1 node"
            with DriverClient() as client:
                create = Script(
                    goal="append a circle window node",
                    steps=[ScriptStep(
                        action="press",
                        anchors=[Anchor(via="ax_path", params={"path": [
                            "Color", "Nodes", "Add Serial Node + CPW"]})])])
                assert run_script(client, create).outcome == "done"
                import time as _time

                after = int(item.GetNumNodes())
                for _ in range(5):
                    if after == 2:
                        break
                    _time.sleep(3)
                    after = int(item.GetNumNodes())
                assert after == 2, f"create unverified: 1 -> {after}"
                receipt = run_window_modify(client, resolve, {
                    "unique_id": uid,
                    "values": {"size": "75.00"},
                    "timeline": name,
                    "foreground": True,
                })
            assert receipt["values"] == {"size": "75.00"}
            assert receipt["read_after"] == {"Size": "75.00"}
            assert int(item.GetNumNodes()) == 2
        finally:
            pool.DeleteTimelines([timeline])
