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
import hashlib
import json
import math
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from library.tools import artifact_lifecycle, resolve_switch_guard
from library.tools.qualification_project import switch_project
from library.tools.resolve_lock import assert_current_timeline

SCRATCH_PREFIX = "Ren Scratch - "


class ScratchProjectError(RuntimeError):
    pass


def _lifecycle_log_path() -> Path:
    """Use one machine-wide event stream for projects with no Ren folder."""
    from library.tools.resolve_lock import instance_lock_dir

    return instance_lock_dir() / "event_log.jsonl"


def _record_created_project(project, timeline=None, *, job_id="") -> list[str]:
    lifecycle = artifact_lifecycle
    project_id = str(project.GetUniqueId() or "")
    if not project_id:
        raise ScratchProjectError(
            "Resolve did not return a stable project identity; ownership was not recorded")
    owner_job = job_id or uuid.uuid4().hex
    run_id = owner_job
    project_identity = {"object_type": "project",
                        "project_unique_id": project_id,
                        "object_unique_id": project_id,
                        "project_name": project.GetName()}
    artifact_ids = []
    project_artifact_id = lifecycle.stable_artifact_id(
        project_id, run_id, owner_job, "resolve_scratch_project", project_id)
    lifecycle.register_resolve_artifact(
        _lifecycle_log_path(), artifact_id=project_artifact_id,
        kind="resolve_scratch_project", project_id=project_id,
        run_id=run_id, job_id=owner_job, identity=project_identity,
        retention_class="retained", state=lifecycle.RETAINED)
    artifact_ids.append(project_artifact_id)
    if timeline is not None:
        timeline_id = str(timeline.GetUniqueId() or "")
        if not timeline_id:
            raise ScratchProjectError(
                "Resolve did not return a stable timeline identity; ownership was not recorded")
        timeline_identity = {"object_type": "timeline",
                             "project_unique_id": project_id,
                             "object_unique_id": timeline_id,
                             "project_name": project.GetName(),
                             "object_name": timeline.GetName()}
        timeline_artifact_id = lifecycle.stable_artifact_id(
            project_id, run_id, owner_job, "resolve_scratch_timeline", timeline_id)
        lifecycle.register_resolve_artifact(
            _lifecycle_log_path(), artifact_id=timeline_artifact_id,
            kind="resolve_scratch_timeline", project_id=project_id,
            run_id=run_id, job_id=owner_job, identity=timeline_identity,
            retention_class="retained", state=lifecycle.RETAINED)
        artifact_ids.append(timeline_artifact_id)
    return artifact_ids


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
        # An untitled project holds no work - it is what a relaunch of
        # Resolve leaves behind, and it cannot be saved because it has
        # no name. Refusing to leave it would leave EVERY scratch
        # operation stuck, with no way out that does not need a person
        # in Resolve's UI. An empty one is not borrowed work, so it is
        # left as it is.
        if (str(name) == "Untitled Project"
                and not int(project.GetTimelineCount() or 0)):
            pass
        else:
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
        if current.GetName() == state.get("project"):
            # Resolve reopened the ORIGIN's project - a broker restart
            # does exactly that - so the machine is already where the
            # record wanted it and the record has nothing left to
            # restore. `open_project` refuses while a record exists and
            # `restore_recorded` used to refuse unless the SCRATCH
            # project was open, so after a restart neither could move
            # and the record had to be removed by hand. The record is
            # completed here instead.
            result = restore(resolve, manager, state)
            path.unlink(missing_ok=True)
            return result
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


def _parse_fps(fps) -> float | None:
    """Validate an optional frame-rate request; None means leave the default."""
    if fps is None or (isinstance(fps, str) and not fps.strip()):
        return None
    try:
        value = float(str(fps).strip())
    except (TypeError, ValueError) as exc:
        raise ScratchProjectError(
            f"scratch frame rate must be a number like 23.976, got {fps!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ScratchProjectError(
            f"scratch frame rate must be a positive number, got {fps!r}")
    return value


def _parse_shape(width, height) -> tuple[int, int] | None:
    """Validate an optional resolution request; both or neither, positive ints."""
    missing = [label for label, value in (("width", width), ("height", height))
               if value is None or (isinstance(value, str) and not str(value).strip())]
    if len(missing) == 2:
        return None
    if len(missing) == 1:
        raise ScratchProjectError(
            f"scratch resolution needs both width and height, missing {missing[0]}")
    try:
        parsed = (float(str(width).strip()), float(str(height).strip()))
    except (TypeError, ValueError) as exc:
        raise ScratchProjectError(
            f"scratch resolution must be positive integers, "
            f"got {width!r}x{height!r}") from exc
    if (any(not math.isfinite(value) for value in parsed)
            or any(value != int(value) for value in parsed)
            or any(value <= 0 for value in parsed)):
        raise ScratchProjectError(
            f"scratch resolution must be positive integers, "
            f"got {width!r}x{height!r}")
    return (int(parsed[0]), int(parsed[1]))


def _write_project_setting(project, key: str, value: str) -> bool:
    """Write one project setting, preferring the plural API Resolve 21 offers."""
    try:
        return bool(project.SetSettings({key: value}))
    except AttributeError:
        return bool(project.SetSetting(key, value))


def _apply_project_shape(project, name: str, fps: float | None,
                         shape: tuple[int, int] | None) -> dict:
    """Set frame rate / resolution on a timeline-less project, verified by read-back.

    Runs between `CreateProject` and the first `CreateEmptyTimeline`: changing
    either setting once a timeline exists deadlocks Resolve, so a non-empty
    project refuses here instead of writing.
    """
    try:
        timeline_count = int(project.GetTimelineCount() or 0)
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise ScratchProjectError(
            f"cannot set the shape of {name!r}: Resolve did not return "
            f"a readable timeline count ({exc})") from exc
    if timeline_count:
        raise ScratchProjectError(
            f"cannot set the shape of {name!r}: it already holds "
            f"{timeline_count} timeline(s), and changing the project frame "
            "rate or resolution under existing timelines deadlocks Resolve")
    pending: dict[str, str] = {}
    if fps is not None:
        pending["timelineFrameRate"] = (
            str(int(fps)) if float(fps).is_integer() else str(fps))
    if shape is not None:
        pending["timelineResolutionWidth"] = str(shape[0])
        pending["timelineResolutionHeight"] = str(shape[1])
    for key, value in pending.items():
        try:
            wrote = _write_project_setting(project, key, value)
            back = project.GetSetting(key)
        except Exception as exc:
            raise ScratchProjectError(
                f"cannot set {key}={value} on {name!r} ({exc}); "
                "verify by hand") from exc
        try:
            actual = float(back)
            wanted = float(value)
        except (TypeError, ValueError):
            actual, wanted = back, value
        matches = (abs(actual - wanted) <= 0.0001
                   if isinstance(actual, float) and isinstance(wanted, float)
                   else actual == wanted)
        if not wrote or not matches:
            raise ScratchProjectError(
                f"Resolve refused {key}={value} on {name!r}: "
                f"write returned {wrote!r}, read back {back!r}")
    return pending


def _read_project_shape(project) -> dict:
    """Read back the project frame rate and resolution; None where unreadable."""
    try:
        fps = float(project.GetSetting("timelineFrameRate"))
        fps = fps if math.isfinite(fps) else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        fps = None
    shape = []
    for key in ("timelineResolutionWidth", "timelineResolutionHeight"):
        try:
            value = float(project.GetSetting(key))
            shape.append(int(value) if math.isfinite(value) and value.is_integer() else None)
        except (AttributeError, TypeError, ValueError, OverflowError):
            shape.append(None)
    return {"fps": fps,
            "resolution": (f"{shape[0]}x{shape[1]}"
                           if all(value is not None for value in shape) else None)}


def create(resolve, manager, name: str, fps=None, width=None, height=None) -> dict:
    name = _name(name)
    fps_value = _parse_fps(fps)
    shape = _parse_shape(width, height)
    if _listed(manager, name):
        raise ScratchProjectError(f"project {name!r} already exists in the current folder")
    with _preserve_origin(resolve, manager) as state:
        created = manager.CreateProject(name)
        _settle_after_lifecycle_call()
        if not created or created.GetName() != name:
            raise ScratchProjectError(f"Resolve did not create scratch project {name!r}")
        requested: dict = {}
        if fps_value is not None or shape is not None:
            requested = _apply_project_shape(created, name, fps_value, shape)
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
        owned_artifacts = _record_created_project(readback, current_timeline)
        return {"project": name, "timeline": current_timeline.GetName(),
                "timeline_count": 1,
                "owned_artifacts": owned_artifacts,
                **({"requested": requested} if requested else {}),
                **_read_project_shape(readback),
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
        owned_artifacts = _record_created_project(current)
        return {"project": name, "source": str(artifact), "imported": True,
                "owned_artifacts": owned_artifacts,
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


def delete(resolve, manager, name: str, *, artifact_id: str = "") -> dict:
    name = str(name or "").strip()
    if name == "Podcast (field test)" or not name.startswith(SCRATCH_PREFIX):
        raise ScratchProjectError(f"refusing to delete non-scratch project {name!r}")
    name = _name(name)
    current = manager.GetCurrentProject()
    if current is not None and current.GetName() == name:
        raise ScratchProjectError(f"refusing to delete the open project {name!r}")
    if not _listed(manager, name):
        raise ScratchProjectError(f"scratch project {name!r} is not in the current folder")
    lifecycle = artifact_lifecycle
    log_path = _lifecycle_log_path()
    candidates = [row for row in lifecycle.resolve_artifacts(log_path)
                  if row["kind"] == "resolve_scratch_project"
                  and row["identity"].get("project_name") == name
                  and (not artifact_id or row["artifact_id"] == artifact_id)
                  and row.get("state") != lifecycle.PURGED]
    if len(candidates) != 1:
        raise ScratchProjectError(
            f"project {name!r} has no unique recorded Resolve identity; "
            "unrecorded resources are protected")
    ownership = candidates[0]
    identity = ownership["identity"]
    if identity.get("object_type") != "project" or not identity.get("object_unique_id"):
        raise ScratchProjectError("recorded scratch project identity is incomplete")
    if ownership["state"] == lifecycle.RETAINED:
        lifecycle.transition_resolve_artifact(
            log_path, ownership["artifact_id"], lifecycle.DISPOSABLE,
            reason="explicit guarded scratch-project retirement requested")
    elif ownership["state"] != lifecycle.DISPOSABLE:
        raise ScratchProjectError(
            f"scratch project is not eligible for retirement: {ownership['state']}")

    def retire_exact(recorded_identity):
        with _preserve_origin(resolve, manager) as state:
            target = switch_project(manager, name)
            target_id = str(target.GetUniqueId() or "") if target else ""
            if target_id != recorded_identity["object_unique_id"]:
                raise ScratchProjectError(
                    "Resolve project identity changed since ownership was recorded")
            if not manager.SaveProject():
                raise ScratchProjectError(
                    "Resolve would not save the exact scratch project; deletion refused")
        from library.tools.shared_environment import vep_home

        backup_root = vep_home() / "resolve-scratch-backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_"
                            for c in name)
        backup = backup_root / f"{safe_name}_{stamp}.drp"
        if not manager.ExportProject(name, str(backup)):
            raise ScratchProjectError(
                f"Resolve could not export {name!r}; project was not deleted")
        if not backup.is_file() or backup.stat().st_size <= 0:
            raise ScratchProjectError(
                f"Resolve export for {name!r} did not produce a readable "
                "non-empty .drp; project was not deleted")
        receipt_path = backup.with_suffix(".json")
        receipt_path.write_text(json.dumps({
            "project": name, "resolve_project_id": recorded_identity["object_unique_id"],
            "backup": str(backup), "exported_at": datetime.now(UTC).isoformat(),
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        }, indent=2) + "\n", encoding="utf-8")
        if not manager.DeleteProject(name):
            raise ScratchProjectError(f"Resolve refused to delete scratch project {name!r}")
        _settle_after_lifecycle_call()
        if _listed(manager, name):
            raise ScratchProjectError(f"deleted project {name!r} remains in the project folder")
        return {"verified": True, "project": name,
                "backup": str(backup), "receipt": str(receipt_path),
                "restored": {"project": state["project"],
                             "timeline": state["timeline"],
                             "page": state["page"], "verified": True}}

    result = lifecycle.retire_resolve_artifact(
        log_path, ownership["artifact_id"], identity, retire_exact)
    return {"project": name, "deleted": True, **result["receipt"]}


def plan_collection(manager) -> Path:
    """Write a manifest from exact owned identities; legacy rows stay protected."""
    from library.tools.shared_environment import vep_home
    from library.tools import artifact_lifecycle as lifecycle

    current = manager.GetCurrentProject()
    current_id = str(current.GetUniqueId() or "") if current else ""
    rows = [row for row in lifecycle.resolve_artifacts(_lifecycle_log_path())
            if row["kind"] == "resolve_scratch_project"
            and row.get("state") == lifecycle.RETAINED
            and row["identity"].get("object_unique_id") != current_id]
    rows.sort(key=lambda row: row["artifact_id"])
    candidates = [{"artifact_id": row["artifact_id"],
                   "identity": row["identity"],
                   "project": row["identity"]["project_name"],
                   "selected": False} for row in rows]
    rows_digest = hashlib.sha256(json.dumps(
        [{key: row[key] for key in ("artifact_id", "identity", "project")}
         for row in candidates], sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    directory = vep_home() / "resolve-scratch-gc"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    manifest = directory / f"plan_{stamp}.json"
    manifest.write_text(json.dumps({
        "format": "ren-resolve-scratch-gc/1",
        "created_at": datetime.now(UTC).isoformat(),
        "current_project_id_kept": current_id,
        "candidate_identities_sha256": rows_digest,
        "candidates": candidates,
    }, indent=2) + "\n", encoding="utf-8")
    return manifest


def apply_collection(resolve, manager, manifest: str | Path) -> dict:
    """Apply only reviewed manifest rows, exporting each .drp first."""
    path = Path(manifest).expanduser().resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScratchProjectError(f"cannot read scratch collection manifest: {exc}") from exc
    if document.get("format") != "ren-resolve-scratch-gc/1":
        raise ScratchProjectError("unsupported scratch collection manifest")
    rows = document.get("candidates")
    if not isinstance(rows, list):
        raise ScratchProjectError("scratch collection manifest has no candidate list")
    planned = [{"artifact_id": row["artifact_id"],
                "identity": row["identity"],
                "project": _name(row["project"]),
                "selected": bool(row.get("selected"))} for row in rows
               if isinstance(row, dict)]
    digest = hashlib.sha256(json.dumps(
        [{key: row[key] for key in ("artifact_id", "identity", "project")}
         for row in planned], sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()
    if digest != document.get("candidate_identities_sha256"):
        raise ScratchProjectError(
            "candidate identities changed since planning; create a fresh manifest")
    selected = [row for row in planned if row["selected"]]
    available = set(manager.GetProjectListInCurrentFolder() or [])
    current = manager.GetCurrentProject()
    current_id = str(current.GetUniqueId() or "") if current else ""
    for row in selected:
        name = row["project"]
        if (row["identity"].get("object_unique_id") == current_id
                or name not in available):
            raise ScratchProjectError(
                f"{name!r} is open or no longer present; nothing was collected")
    removed = []
    for row in selected:
        removed.append(delete(resolve, manager, row["project"],
                              artifact_id=row["artifact_id"]))
        document["collected"] = removed
        document["updated_at"] = datetime.now(UTC).isoformat()
        path.write_text(json.dumps(document, indent=2) + "\n",
                        encoding="utf-8")
    return {"manifest": str(path), "removed": removed}
