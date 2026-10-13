"""Live proof: leased Project Manager navigation and confirmation-gated open.

Live half of fm/vep-resolve-pm-navigate (firstmate steer 005). The
offline doubles in `tests/unit/resolve/test_timeline_ingest.py` pin the
logic; this pins the REAL Project Manager API names, the real
dirty-state read, and the real folder restore against Ren
Qualification - the one project a live test may touch.

Runs ONLY inside a granted borrow-resolve window (`REN_LIVE_RESOLVE=1`
with Ren Qualification open): `resolve_session` skips anywhere else,
and refuses to run in a user project. Ren-owned projects only - Ren
Qualification plus one clearly-named disposable scratch that is
deleted before the end. Never touches, saves, opens or switches to a
captain project. Leaves Resolve on Ren Qualification with the PM
folder restored and read back.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from library.tools import timeline_ingest
from library.tools.ren_refusal import RenRefusal
from library.tools.resolved.jobs import QUALIFICATION_PROJECT
from library.tools.timeline_ingest import TimelineIngestError

pytestmark = pytest.mark.resolve_live

EVIDENCE = []


def snap(manager, tag):
    """Project, folder, timelines; the walk's cursor move is restored.

    The list and leaf are read BEFORE the walk: the walk ends at the
    root, so anything read after it describes the root, not the
    folder (mismeasured exactly this way on the first live run).
    """
    proj = manager.GetCurrentProject()
    count = 0
    timelines = []
    if proj is not None:
        count = proj.GetTimelineCount() or 0
        for i in range(1, count + 1):
            tl = proj.GetTimelineByIndex(i)
            timelines.append(tl.GetName() if tl else None)
    leaf = manager.GetCurrentFolder()
    listed = sorted(manager.GetProjectListInCurrentFolder() or [])
    path = list(timeline_ingest._current_folder_path(manager))
    state = {
        "tag": tag,
        "project": proj.GetName() if proj else None,
        "folder_path": path,
        "folder_leaf": leaf,
        "timeline_count": count,
        "timelines": timelines,
        "projects_here": listed,
    }
    timeline_ingest._open_folder_path(manager, tuple(path))
    assert manager.GetCurrentFolder() == (path[-1] if path else "")
    EVIDENCE.append(state)
    return state


def find_home_folder(manager, name, start_path, limit=200):
    """Folders whose project list contains `name`; cursor restored after.

    The PM folder cursor is independent of the open project (measured
    live 2026-10-13: Ren Qualification open while the cursor sat in a
    folder of user projects), so the cursor path is never the answer -
    the tree is searched and the cursor put back.
    """
    found = []
    visits = []

    def visit(path):
        if len(visits) >= limit:
            return
        timeline_ingest._open_folder_path(manager, tuple(path))
        visits.append(list(path))
        if name in (manager.GetProjectListInCurrentFolder() or []):
            found.append(list(path))
        for child in timeline_ingest._subfolders(manager):
            visit(tuple(path) + (child,))

    try:
        visit(())
    finally:
        timeline_ingest._open_folder_path(manager, tuple(start_path))
    EVIDENCE.append({"tag": "home-search", "folders_visited": len(visits),
                     "home": found})
    return found


@pytest.mark.serial(reason="the PM folder walk and project switch hold "
                           "the exclusive instance lease")
def test_pm_navigate_live_proof(resolve_session, capsys):
    from library.tools.marker_feedback import connect_resolve
    from library.tools.qualification_project import (
        borrowed,
        switch_project,
    )

    scratch = f"Ren Scratch pm-navigate-{os.getpid()}-{int(time.time())}"
    created = False
    resolve = connect_resolve()
    with borrowed(resolve, qualification_only=True) as found:
        assert found["project"] == QUALIFICATION_PROJECT
        manager = resolve.GetProjectManager()
        before = snap(manager, "before")
        assert before["project"] == QUALIFICATION_PROJECT
        # The cursor folder is NOT the open project's home (live
        # 2026-10-13: cursor in a user-project folder while Ren
        # Qualification is open), so the declared folder is discovered
        # by search, never read off the cursor.
        homes = find_home_folder(manager, QUALIFICATION_PROJECT,
                                 before["folder_path"])
        assert len(homes) == 1, f"expected one home folder, found {homes}"
        EVIDENCE.append({"tag": "cursor-vs-home",
                         "cursor": before["folder_path"],
                         "home": homes[0]})
        folder_str = "/".join(homes[0])
        try:
            # 1. Declared-folder navigation restores; the open project
            # returns as-is with no confirm and no refusal.
            got = timeline_ingest.resolve_project_in_declared_folder(
                manager, QUALIFICATION_PROJECT, folder_str,
                confirm_open=False)
            assert got.GetName() == QUALIFICATION_PROJECT
            mid = snap(manager, "after-navigate-restore")
            assert mid["folder_path"] == before["folder_path"]
            assert mid["project"] == before["project"]
            assert mid["timeline_count"] == before["timeline_count"]

            # 2. A near name refuses; nothing moves.
            with pytest.raises(TimelineIngestError):
                timeline_ingest.resolve_project_in_declared_folder(
                    manager, QUALIFICATION_PROJECT + " (copy)", folder_str)
            assert (snap(manager, "after-exactness-refusal")["folder_path"]
                    == before["folder_path"])

            # Stage in the home folder: the scratch must live beside
            # Qualification, never among user projects.
            timeline_ingest._open_folder_path(manager, tuple(homes[0]))
            staged = snap(manager, "staged-in-home")
            assert staged["folder_path"] == homes[0]
            assert scratch not in staged["projects_here"]

            # 3. Disposable Ren-owned scratch for the ask/open loop.
            created_proj = manager.CreateProject(scratch)
            assert (created_proj is not None
                    and created_proj.GetName() == scratch)
            created = True
            if manager.GetCurrentProject().GetName() != (
                    QUALIFICATION_PROJECT):
                switch_project(manager, QUALIFICATION_PROJECT)
            timeline_ingest._open_folder_path(manager, tuple(homes[0]))
            assert scratch in (manager.GetProjectListInCurrentFolder()
                               or [])

            # 3a. No open without the yes: ask refusal, no switch.
            with pytest.raises(RenRefusal) as asked:
                timeline_ingest.resolve_project_in_declared_folder(
                    manager, scratch, folder_str, confirm_open=False)
            assert asked.value.code == "REN_RESOLVE_PROJECT_OPEN_ASK"
            assert asked.value.recovery_class == "needs_question"
            assert (manager.GetCurrentProject().GetName()
                    == QUALIFICATION_PROJECT)
            assert (snap(manager, "after-ask-refusal")["folder_path"]
                    == homes[0])

            # 3b. The live dirty-state read decides the next step.
            current = manager.GetCurrentProject()
            try:
                dirty = current.GetIsProjectDirty()
            except (AttributeError, TypeError):
                # Live 2026-10-13: the bridge resolves the check to None
                # rather than raising, so the call is a TypeError.
                dirty = "missing"
            EVIDENCE.append({"tag": "dirty-read", "value": dirty})
            if dirty == "missing":
                with pytest.raises(TimelineIngestError):
                    timeline_ingest.resolve_project_in_declared_folder(
                        manager, scratch, folder_str, confirm_open=True)
            elif dirty:
                with pytest.raises(TimelineIngestError,
                                   match="unsaved changes"):
                    timeline_ingest.resolve_project_in_declared_folder(
                        manager, scratch, folder_str, confirm_open=True)
            else:
                opened = timeline_ingest.resolve_project_in_declared_folder(
                    manager, scratch, folder_str, confirm_open=True)
                assert opened.GetName() == scratch
                EVIDENCE.append({"tag": "opened", "project": scratch})
                switch_project(manager, QUALIFICATION_PROJECT)
                assert (snap(manager, "after-switch-back")["project"]
                        == QUALIFICATION_PROJECT)
        finally:
            cur = manager.GetCurrentProject()
            if cur is None or cur.GetName() != QUALIFICATION_PROJECT:
                switch_project(manager, QUALIFICATION_PROJECT)
            if created:
                # Delete where the scratch lives, before restoring
                # the cursor to the user's folder.
                timeline_ingest._open_folder_path(manager, tuple(homes[0]))
                cur = manager.GetCurrentProject()
                if cur is not None and cur.GetName() == scratch:
                    switch_project(manager, QUALIFICATION_PROJECT)
                assert manager.DeleteProject(scratch)
                assert scratch not in (
                    manager.GetProjectListInCurrentFolder() or [])
            timeline_ingest._open_folder_path(
                manager, tuple(before["folder_path"]))
            final = snap(manager, "final")
            assert final["project"] == before["project"]
            assert final["folder_path"] == before["folder_path"]
            assert final["timeline_count"] == before["timeline_count"]
            assert final["projects_here"] == before["projects_here"]
    with capsys.disabled():
        print(json.dumps({"ok": True, "evidence": EVIDENCE}, indent=2))
