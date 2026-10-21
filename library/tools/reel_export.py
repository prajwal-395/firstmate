"""Guarded reel-level export: one named timeline out of ANY Resolve project.

The gap this closes
-------------------
Ren could copy a `.drt` into a Ren-owned scratch project
(`resolve_scratch_projects.import_project`), but the `.drt` had to come
from the project that was OPEN. So a run that needed a reel out of the
captain's `Podcast (field test)` could only ever measure the one reel
that happened to be open, and every brief was measured against reel 13
whatever its premise (`docs/benchmarks/2026-10-20-unfamiliar-brief-acceptance.md`).

This module is the other half: it opens an arbitrary project for
READING, exports one named timeline to a `.drt`, and puts the machine
back exactly where it was.

The rules, and why each one is here
----------------------------------
1. **The source project is read-only.**  It is opened, one timeline is
   exported, and it is never saved, edited, renamed or written.  A
   captain's project is not ours to change, and a change nobody asked
   for is worse than a refusal.
2. **The source is never saved.**  The borrow discipline elsewhere in
   this repository saves the project it leaves, because Resolve
   discards unsaved work when the machine moves on.  That is right for
   a Ren project and wrong for the source: saving it is a write.  So
   the source is never saved, and the state of the source - clean,
   dirty, or the `None` the live bridge answers with - is REPORTED
   rather than assumed.
3. **The origin comes back.**  The project, its current timeline (by
   unique id) and the Resolve page are restored and READ BACK - on the
   failure path as well as the success path.  The origin is the project
   the machine returns to, so it is the one whose unsaved work this
   operation protects: a captain-owned origin that reports unsaved
   changes is REFUSED before anything is written.
4. **The cursor inside the source does not move.**  The export takes a
   handle to the named timeline and exports it, so the source's current
   timeline is the same one before and after.
5. **One writer.**  Everything here runs under the exclusive Resolve
   lease, which is what the AXI command takes.

`tests/unit/resolve/test_reel_export.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

from library.tools import resolve_scratch_projects as scratch
from library.tools.qualification_project import (
    QualificationError,
    open_project_for_reading,
)


class ReelExportError(RuntimeError):
    """The export could not be done safely. Nothing was left half-written."""


#: Where the `.drt` goes when the caller names no place of its own.
#:
#: It is the machine's own directory, not a checkout and not a
#: project: the archive outlives the run that made it and belongs to
#: nobody's project folder.
EXPORT_DIRNAME = "reel-exports"


def _ren_owned(project_name: str) -> bool:
    """Whether a project belongs to Ren's disposable namespace.

    The scratch projects and the qualification project are ours to save
    and to delete.  Everything else - the captain's projects above all -
    is not ours to write.
    """
    name = str(project_name or "")
    return (name.startswith(scratch.SCRATCH_PREFIX)
            or name in {"Ren Qualification", "Resolve Qualification"})


def _dirty_state(project):
    """Resolve's unsaved-changes reading, or None when it cannot say.

    The bridge answers `GetIsProjectDirty` with None on live Resolve
    (measured 2026-10-13, `library/tools/timeline_ingest.py`), so the
    call fails and the state is UNKNOWN.  Unknown is not clean, and it
    is reported as unknown rather than dressed up as a reading.
    """
    if project is None:
        return None
    try:
        dirty = project.GetIsProjectDirty()
    except Exception:  # noqa: BLE001 - a bridge that cannot say
        return None
    return dirty if isinstance(dirty, bool) else None


def _record_origin(resolve, manager) -> dict:
    """The project, timeline and page the machine must come back to."""
    project = manager.GetCurrentProject()
    if project is None:
        raise ReelExportError("Resolve has no open project to return to")
    timeline = project.GetCurrentTimeline()
    return {"project": project.GetName(),
            "timeline": timeline.GetName() if timeline else None,
            "timeline_id": timeline.GetUniqueId() if timeline else None,
            "page": resolve.GetCurrentPage()}


def _prepare_to_leave(manager, project_name: str, *, may_save: bool) -> dict:
    """Decide what happens to a project the operation is about to leave.

    A Ren-owned project is saved, so leaving it costs nothing.  A
    project that is not ours is NOT written: it is read for its
    unsaved-changes state, and one that says it HAS unsaved changes is
    REFUSED, because Resolve would discard them when the machine moves
    on.  A project whose state cannot be read is reported as unknown -
    the receipt says so rather than claiming a reading it does not have.
    """
    if may_save and _ren_owned(project_name):
        if not manager.SaveProject():
            raise ReelExportError(
                f"cannot leave {project_name!r}: Resolve would not save it")
        return {"saved": True, "dirty_state": None}
    project = manager.GetCurrentProject()
    dirty = _dirty_state(project)
    if dirty is True:
        raise ReelExportError(
            f"refusing to leave {project_name!r}: it holds unsaved changes "
            "and this operation never saves a project it does not own. "
            "Save it in Resolve, then run the export again.")
    return {"saved": False, "dirty_state": dirty}


def _source_state(project) -> dict:
    """What the source project holds, read as it is found."""
    timeline = project.GetCurrentTimeline()
    return {"current_timeline": timeline.GetName() if timeline else None,
            "current_timeline_id": (str(timeline.GetUniqueId() or "")
                                    if timeline else None),
            "timeline_count": int(project.GetTimelineCount() or 0)}


def _timeline_named(project, name: str):
    """The timeline named exactly `name`, or a refusal naming the rest."""
    from library.tools import timeline_ingest

    try:
        return timeline_ingest.timeline_named(project, name)
    except timeline_ingest.TimelineIngestError as exc:
        raise ReelExportError(str(exc)) from exc


def _restore_origin(resolve, manager, origin: dict) -> dict:
    """Put the machine back on the recorded project, timeline and page."""
    try:
        return scratch.restore(resolve, manager, origin)
    except (scratch.ScratchProjectError, QualificationError) as exc:
        raise ReelExportError(
            f"the export could not put the machine back on "
            f"{origin['project']!r} timeline {origin['timeline']!r}: {exc}. "
            "Reopen it by hand.") from exc


def _export(resolve, timeline, timeline_name: str, destination: Path) -> dict:
    """Write the `.drt` through the one export path in the repository."""
    from library.tools.versions import store

    archive = store.export_timeline_recovery_archive(
        resolve, timeline, timeline_name, "",
        label=f"reel export {timeline_name}", destination=destination)
    if not archive.get("archived"):
        raise ReelExportError(
            f"the .drt did not land: {archive.get('reason') or 'unknown'}")
    return archive


def export_timeline(resolve, manager, *, source_project: str,
                    timeline_name: str, destination,
                    source_folder: str = "") -> dict:
    """Export one named timeline out of `source_project` to a `.drt`.

    The source project is opened for reading, the timeline is exported,
    and the origin project, timeline and page are restored and read
    back.  The source project is never saved and never written.
    """
    from library.tools.resolve_lock import exclusive_held

    if not exclusive_held():
        raise ReelExportError(
            "the reel export needs the EXCLUSIVE Resolve lease: it moves "
            "the machine between projects, and a shared instance would "
            "move it under another writer")
    source_project = str(source_project or "").strip()
    timeline_name = str(timeline_name or "").strip()
    if not source_project:
        raise ReelExportError(
            "the export needs the EXACT name of the project to read from")
    if not timeline_name:
        raise ReelExportError(
            "the export needs the EXACT name of the timeline to copy out")
    destination = Path(destination).expanduser().resolve()
    if destination.suffix.lower() != ".drt":
        raise ReelExportError(
            f"the export destination must be a .drt file: {destination}")

    origin = _record_origin(resolve, manager)
    source_was_open = origin["project"] == source_project
    # The origin is left twice - to read the source, and to make the
    # scratch project - so what leaving it costs is settled BEFORE
    # anything is written.  A captain-owned origin that holds unsaved
    # changes is refused here, while there is still nothing to undo.
    origin_leave = _prepare_to_leave(
        manager, origin["project"], may_save=not source_was_open)
    try:
        if not source_was_open:
            try:
                open_project_for_reading(manager, source_project,
                                          folder=source_folder)
            except QualificationError as exc:
                raise ReelExportError(
                    f"cannot read {source_project!r}: {exc}") from exc
        source = manager.GetCurrentProject()
        if source is None or source.GetName() != source_project:
            raise ReelExportError(
                f"Resolve did not open {source_project!r} (it has "
                f"{source.GetName() if source else None!r})")
        timeline = _timeline_named(source, timeline_name)
        before = _source_state(source)
        # The state of the source as it was FOUND, read before the
        # export: after the machine leaves it, Resolve may have
        # discarded unsaved work, and a clean reading afterwards would
        # hide that.
        source_dirty = _dirty_state(source)
        shape = scratch.read_project_shape(source)
        archive = _export(resolve, timeline, timeline_name, destination)
        after = _source_state(source)
        if before["current_timeline_id"] != after["current_timeline_id"]:
            raise ReelExportError(
                f"the export moved the current timeline of {source_project!r}; "
                "the source must be left as it was found")
    finally:
        restored = _restore_origin(resolve, manager, origin)
    return {
        "source_project": source_project,
        "source_timeline": timeline_name,
        "source_opened_by_the_export": not source_was_open,
        "source": {"saved": False,
                   "dirty_state": source_dirty,
                   "current_timeline": before["current_timeline"],
                   "current_timeline_id": before["current_timeline_id"],
                   "timeline_count": before["timeline_count"],
                   "cursor_stayed_put": True,
                   "shape": shape},
        "drt": {"path": archive["path"], "bytes": archive.get("bytes"),
                "sha256": archive.get("sha256"),
                "content_id": archive.get("content_id")},
        "origin": {"project": origin["project"],
                   "timeline": origin["timeline"],
                   "timeline_id": origin["timeline_id"],
                   "page": origin["page"],
                   "saved": origin_leave["saved"],
                   "dirty_state": origin_leave["dirty_state"],
                   "restored": restored},
    }


def _safe_name(value: str) -> str:
    """A file-name-safe form of a project or timeline name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-") or "reel"


def export_destination(source_project: str, timeline_name: str,
                       archive_dir=None) -> Path:
    """Where the `.drt` of one export goes by default."""
    if archive_dir is None:
        from library.tools.shared_environment import vep_home

        archive_dir = vep_home() / EXPORT_DIRNAME
    directory = Path(archive_dir).expanduser()
    stem = f"{_safe_name(source_project)}--{_safe_name(timeline_name)}"
    return directory / f"{stem}.drt"


def _shape_number(resolution, index: int):
    """The width or the height out of a `WxH` string, or None."""
    if not resolution or "x" not in str(resolution):
        return None
    parts = str(resolution).split("x")
    if index >= len(parts):
        return None
    try:
        return int(parts[index])
    except (TypeError, ValueError):
        return None


def export_into_scratch(resolve, manager, *, source_project: str,
                         timeline_name: str, scratch_project: str = "",
                         fps=None, width=None, height=None,
                         source_folder: str = "",
                         archive_dir=None) -> dict:
    """Copy one timeline out of a project, and into a Ren-owned scratch project.

    The `.drt` is the way out of the source project and the way into the
    scratch project: the scratch import is the one path that puts a
    copied timeline into a project Ren owns, and the export is the one
    path that writes the `.drt`.

    The delivery shape of the copy is the SOURCE's shape, read from the
    source project, so the copy is the reel and not a re-framed version
    of it.  An explicit `fps`/`width`/`height` overrides it.
    """
    name = scratch.scratch_project_name(scratch_project) if scratch_project else ""
    destination = export_destination(source_project, timeline_name,
                                     archive_dir)
    exported = export_timeline(resolve, manager,
                               source_project=source_project,
                               timeline_name=timeline_name,
                               destination=destination,
                               source_folder=source_folder)
    result = {"export": exported, "scratch": None}
    if not name:
        return result
    shape = exported["source"].get("shape") or {}
    fps_value = fps if fps is not None else shape.get("fps")
    width_value = (width if width is not None
                   else _shape_number(shape.get("resolution"), 0))
    height_value = (height if height is not None
                    else _shape_number(shape.get("resolution"), 1))
    if fps_value is None or width_value is None or height_value is None:
        raise ReelExportError(
            f"the shape of {source_project!r} could not be read, so the "
            "copy cannot be given the source's frame rate and resolution; "
            "pass them explicitly")
    imported = scratch.import_project(
        resolve, manager, exported["drt"]["path"], name,
        fps=fps_value, width=width_value, height=height_value,
        # The origin is not saved here: the export above already
        # established what leaving it costs, and the source is never
        # written.
        save_origin=False)
    result["scratch"] = imported
    return result
