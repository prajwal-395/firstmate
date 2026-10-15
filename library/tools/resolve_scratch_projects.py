"""Guarded lifecycle for Ren-owned Resolve scratch projects.

Scratch projects are deliberately namespaced so lifecycle operations can
never target a captain-owned project by accident. The current project is
saved first and restored after: dirty-state handling follows
`qualification_project.borrowed` - save (which the captain's rule allows
a borrow-shaped flow), never discard. A boolean dirty check is not used
because the scripting bridge answers `GetIsProjectDirty` with None
(measured 2026-10-13, see `timeline_ingest`), so any such check would
refuse every project on this Resolve, forever. A failed save refuses
before any switch, exactly like `borrowed`.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path

from library.tools import resolve_switch_guard
from library.tools.qualification_project import switch_project
from library.tools.resolve_lock import assert_current_timeline

SCRATCH_PREFIX = "Ren Scratch - "


class ScratchProjectError(RuntimeError):
    pass


def _name(name: str) -> str:
    name = str(name or "").strip()
    if not name.startswith(SCRATCH_PREFIX) or not name[len(SCRATCH_PREFIX):].strip():
        raise ScratchProjectError(
            f"scratch project names must start with {SCRATCH_PREFIX!r} and include a suffix")
    return name


def _settle_after_lifecycle_call() -> None:
    resolve_switch_guard.note_project_switch()
    resolve_switch_guard.wait_for_settle()


def _starting_state(resolve, manager) -> dict:
    project = manager.GetCurrentProject()
    if project is None:
        raise ScratchProjectError("Resolve has no current project to restore")
    name = project.GetName()
    if not manager.SaveProject():
        raise ScratchProjectError(
            f"cannot switch from {name!r}: Resolve would not save it; "
            "not borrowing an unsavable project")
    timeline = project.GetCurrentTimeline()
    return {
        "project": name,
        "timeline_id": timeline.GetUniqueId() if timeline else None,
        "timeline": timeline.GetName() if timeline else None,
        "page": resolve.GetCurrentPage(),
    }


def _origin_path() -> Path:
    from library.tools.resolve_lock import lock_dir
    return lock_dir() / "resolve_axi_scratch_origin.json"


def _write_origin(state: dict) -> None:
    path = _origin_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def restore_recorded(resolve, manager) -> dict:
    """Restore the exact origin recorded by `open`, then clear its receipt."""
    path = _origin_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScratchProjectError("there is no recorded scratch-project origin to restore") from exc
    current = manager.GetCurrentProject()
    if current is None:
        raise ScratchProjectError("Resolve has no current project to restore from")
    if current.GetName() != state.get("scratch_project"):
        raise ScratchProjectError(
            f"cannot restore recorded origin: expected {state.get('scratch_project')!r} open, "
            f"found {current.GetName()!r}")
    if not manager.SaveProject():
        raise ScratchProjectError(
            "cannot restore: Resolve would not save the open scratch "
            "project; not leaving unsavable work behind")
    result = restore(resolve, manager, state)
    path.unlink()
    return result


@contextmanager
def _preserve_origin(resolve, manager):
    state = _starting_state(resolve, manager)
    try:
        yield state
    finally:
        current = manager.GetCurrentProject()
        if current is None or current.GetName() != state["project"]:
            restore(resolve, manager, state)
        else:
            # Still verify the timeline and page: lifecycle APIs can move
            # either without changing the project name.
            restore(resolve, manager, state)


def _timeline_by_id(project, uid):
    if uid is None:
        return None
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetUniqueId() == uid:
            return timeline
    return None


def restore(resolve, manager, state: dict) -> dict:
    """Restore the recorded project, timeline and page and verify each."""
    project = manager.GetCurrentProject()
    if project is None or project.GetName() != state["project"]:
        project = switch_project(manager, state["project"])
    if not project or project.GetName() != state["project"]:
        raise ScratchProjectError(f"restore did not reopen {state['project']!r}")
    timeline = _timeline_by_id(project, state["timeline_id"])
    if state["timeline_id"] is not None:
        if timeline is None:
            raise ScratchProjectError(
                f"restore cannot find timeline {state['timeline']!r} by its recorded id")
        assert_current_timeline(project, timeline)
        current = project.GetCurrentTimeline()
        if not current or current.GetUniqueId() != state["timeline_id"]:
            raise ScratchProjectError("restored timeline did not read back")
    page = state["page"]
    if (page is not None and resolve.GetCurrentPage() != page
            and (not resolve.OpenPage(page)
                 or resolve.GetCurrentPage() != page)):
        raise ScratchProjectError(f"restore did not return to page {page!r}")
    return {"project": state["project"], "timeline": state["timeline"],
            "page": state["page"], "verified": True}


def _listed(manager, name: str) -> bool:
    return name in (manager.GetProjectListInCurrentFolder() or [])


def create(resolve, manager, name: str) -> dict:
    name = _name(name)
    if _listed(manager, name):
        raise ScratchProjectError(f"project {name!r} already exists in the current folder")
    with _preserve_origin(resolve, manager) as state:
        created = manager.CreateProject(name)
        _settle_after_lifecycle_call()
        if not created or created.GetName() != name:
            raise ScratchProjectError(f"Resolve did not create scratch project {name!r}")
        pool = created.GetMediaPool()
        timeline = pool.CreateEmptyTimeline(f"{name} - Empty")
        if not timeline:
            raise ScratchProjectError(f"Resolve did not create an empty timeline in {name!r}")
        assert_current_timeline(created, timeline)
        if not manager.SaveProject():
            raise ScratchProjectError(f"Resolve did not save the new scratch project {name!r}")
        readback = manager.GetCurrentProject()
        current_timeline = readback.GetCurrentTimeline() if readback else None
        if (not readback or readback.GetName() != name or not current_timeline
                or current_timeline.GetUniqueId() != timeline.GetUniqueId()
                or int(readback.GetTimelineCount() or 0) != 1):
            raise ScratchProjectError(f"empty timeline did not read back in {name!r}")
        return {"project": name, "timeline": current_timeline.GetName(),
                "timeline_count": 1,
                "restored": {"project": state["project"],
                             "timeline": state["timeline"],
                             "page": state["page"], "verified": True}}


def import_project(resolve, manager, drp: str | Path, name: str) -> dict:
    name = _name(name)
    artifact = Path(drp).expanduser().resolve()
    if not artifact.is_file() or artifact.suffix.lower() != ".drp":
        raise ScratchProjectError(f"import requires an existing .drp file: {artifact}")
    if _listed(manager, name):
        raise ScratchProjectError(f"project {name!r} already exists in the current folder")
    with _preserve_origin(resolve, manager) as state:
        imported = manager.ImportProject(str(artifact), name)
        _settle_after_lifecycle_call()
        if not imported:
            raise ScratchProjectError(f"Resolve refused to import {artifact.name!r} as {name!r}")
        if not _listed(manager, name):
            raise ScratchProjectError(f"imported project {name!r} did not read back in the project list")
        project = switch_project(manager, name)
        if not project or project.GetName() != name:
            raise ScratchProjectError(f"imported project {name!r} did not open by exact name")
        current = manager.GetCurrentProject()
        if not current or current.GetName() != name:
            raise ScratchProjectError(f"imported project {name!r} did not read back as current")
        return {"project": name, "source": str(artifact), "imported": True,
                "timeline_count": int(project.GetTimelineCount() or 0),
                "restored": {"project": state["project"],
                             "timeline": state["timeline"],
                             "page": state["page"], "verified": True}}


def open_project(resolve, manager, name: str) -> dict:
    name = _name(name)
    state = _starting_state(resolve, manager)
    if name == state["project"]:
        raise ScratchProjectError(f"scratch project {name!r} is already open")
    if not _listed(manager, name):
        raise ScratchProjectError(f"scratch project {name!r} is not in the current folder")
    path = _origin_path()
    if path.exists():
        raise ScratchProjectError(
            "a scratch-project origin is already recorded; restore it before opening another scratch project")
    _write_origin({**state, "scratch_project": name})
    try:
        project = switch_project(manager, name)
    except Exception:
        # Keep the receipt when Resolve may have partially switched. It
        # remains the only safe route back after a failed switch.
        raise
    timeline = project.GetCurrentTimeline()
    return {"project": project.GetName(),
            "timeline": timeline.GetName() if timeline else None,
            "opened": True,
            "origin": {"project": state["project"], "timeline": state["timeline"],
                       "page": state["page"]},
            "restore_with": "resolve-axi project scratch restore --apply"}


def delete(resolve, manager, name: str) -> dict:
    name = str(name or "").strip()
    if name == "Podcast (field test)" or not name.startswith(SCRATCH_PREFIX):
        raise ScratchProjectError(f"refusing to delete non-scratch project {name!r}")
    name = _name(name)
    current = manager.GetCurrentProject()
    if current is not None and current.GetName() == name:
        raise ScratchProjectError(f"refusing to delete the open project {name!r}")
    if not _listed(manager, name):
        raise ScratchProjectError(f"scratch project {name!r} is not in the current folder")
    with _preserve_origin(resolve, manager) as state:
        if not manager.DeleteProject(name):
            raise ScratchProjectError(f"Resolve refused to delete scratch project {name!r}")
        _settle_after_lifecycle_call()
        if _listed(manager, name):
            raise ScratchProjectError(f"deleted project {name!r} remains in the project list")
        return {"project": name, "deleted": True,
                "restored": {"project": state["project"],
                             "timeline": state["timeline"],
                             "page": state["page"], "verified": True}}
