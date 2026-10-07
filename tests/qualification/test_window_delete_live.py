"""Window deletion closed live in Ren Qualification.

Builds its own scratch timeline (named for this run, deleted after -
nothing touches a timeline that was already there): one qual clip,
one appended window node, then the delete recipe removes it and the
node count reads back down. The node is ungraded throughout - deletion
needs no pixels, only the menu and the count.

Drives the live Resolve: skipped unless `REN_LIVE_RESOLVE=1` inside a
granted borrow-resolve window (`resolve_session` holds the whole file's
lease, one live take at a time across lanes). Menu presses are a
background route - no focus is taken. The Color page assertion inside
the runner needs its pixels, so a locked screen refuses the run
instead of pressing against the wrong page.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.resolve_live

SCRATCH_PREFIX = "Q WindowDelete "


def _pool_clip(pool, stem):
    root = pool.GetRootFolder()
    for item in root.GetClipList() or []:
        name = item.GetName() or ""
        if name == stem or name.rsplit(".", 1)[0] == stem:
            return item
    return None


def test_create_then_delete_reads_back_on_the_count(
        resolve_session, tmp_path):
    """The delete loop closed live: append a window node (+1), delete
    it (-1), and the count returns to exactly where it started."""
    from library.tools.computer_use.client import DriverClient
    from library.tools.computer_use.scripts import (
        Anchor,
        Script,
        ScriptStep,
        run_script,
    )
    from library.tools.computer_use.window_delete import run_window_delete
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
                receipt = run_window_delete(client, resolve, {
                    "unique_id": uid, "node_index": 2,
                    "timeline": name,
                    "foreground": False,
                })
            assert receipt["nodes_before"] == 2
            assert receipt["nodes_after"] == 1
            assert int(item.GetNumNodes()) == 1
        finally:
            pool.DeleteTimelines([timeline])
