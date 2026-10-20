"""Import and verify one qualification DRT in a retired Ren Scratch project."""

from __future__ import annotations

import hashlib
from pathlib import Path

from library.tools.resolved.jobs import (
    EXCLUSIVE,
    QUALIFICATION_PROJECT,
    JobRefused,
)

KIND = "timeline.drt_recovery_probe"
_PARAMS = frozenset({"project", "archive_path"})


def _archive_path(params: dict) -> Path:
    from library.tools import qualification_project

    value = params.get("archive_path")
    if not isinstance(value, str) or not value.strip():
        raise JobRefused(f"{KIND} needs a non-empty 'archive_path'")
    folder = qualification_project.drt_export_probe_project_folder().resolve()
    candidate = Path(value).expanduser().resolve()
    try:
        candidate.relative_to(folder)
    except ValueError as exc:
        raise JobRefused(
            f"{KIND} accepts archives only from {folder}") from exc
    if candidate.suffix.lower() != ".drt" or not candidate.is_file():
        raise JobRefused(f"{KIND} needs an existing .drt archive")
    return candidate


def prepare(params: dict) -> dict:
    if not isinstance(params, dict):
        raise JobRefused(f"{KIND} params must be an object")
    unknown = set(params) - _PARAMS
    if unknown:
        raise JobRefused(f"{KIND} refuses unknown field(s): {sorted(unknown)}")
    if params.get("project") != QUALIFICATION_PROJECT:
        raise JobRefused(f"{KIND} may only target {QUALIFICATION_PROJECT!r}")
    if "archive_path" in params:
        _archive_path(params)
    return {"mode": EXCLUSIVE, "priority": "mutation", "executed": True,
            "project": QUALIFICATION_PROJECT, "timeline": "",
            "locality": "project", "coalesce_key": None,
            "qualification": True}


def _timeline_inventory(project) -> dict[str, object]:
    inventory = {}
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline:
            uid = str(timeline.GetUniqueId() or "")
            if not uid or uid in inventory:
                raise JobRefused(f"{KIND} found an ambiguous timeline identity")
            inventory[uid] = timeline.GetName()
    return inventory


def _timeline_by_uid(project, timeline_uid: str):
    matches = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and str(timeline.GetUniqueId() or "") == timeline_uid:
            matches.append(timeline)
    if len(matches) != 1:
        raise JobRefused(
            f"{KIND} expected one imported timeline UID {timeline_uid!r}; "
            f"found {len(matches)}")
    return matches[0]


def _timeline_facts(timeline) -> dict:
    tracks = []
    for media_type in ("video", "audio"):
        count = int(timeline.GetTrackCount(media_type) or 0)
        for index in range(1, count + 1):
            items = timeline.GetItemListInTrack(media_type, index) or []
            tracks.append({"type": media_type, "items": len(items)})
    tracks.sort(key=lambda row: (row["type"], row["items"]))
    return {"tracks": tracks,
            "items": sum(track["items"] for track in tracks)}


def _expected_facts(document: dict) -> dict:
    timelines = document["timelines"]
    if len(timelines) != 1:
        raise JobRefused(f"{KIND} requires a DRT with exactly one timeline")
    tracks = []
    for row in timelines[0]["tracks"]:
        kind = str(row["type"] or "").lower()
        if "video" in kind:
            media_type = "video"
        elif "audio" in kind:
            media_type = "audio"
        else:
            raise JobRefused(f"{KIND} cannot verify archive track type {kind!r}")
        tracks.append({"type": media_type, "items": len(row["items"])})
    tracks.sort(key=lambda row: (row["type"], row["items"]))
    return {"tracks": tracks,
            "items": sum(track["items"] for track in tracks)}


def run(job: dict, resolve) -> dict:
    """Exercise Resolve's DRT import under the broker's whole-take lease."""
    from library.tools import (
        qualification_project,
        resolve_archive,
        resolve_scratch_projects,
    )

    params = job["params"]
    prepare(params)
    archive = (_archive_path(params) if "archive_path" in params else None)
    manager = resolve.GetProjectManager()
    scratch_name = f"{resolve_scratch_projects.SCRATCH_PREFIX}DRT {job['id'][:12]}"
    created = opened = restored = retired = False

    def perform() -> dict:
        nonlocal archive, created, opened, restored, retired
        try:
            if archive is None:
                from library.tools.resolved import drt_export_probe
                project = manager.GetCurrentProject()
                current = project.GetCurrentTimeline()
                target = next((project.GetTimelineByIndex(index)
                               for index in range(
                                   1, int(project.GetTimelineCount() or 0) + 1)
                               if project.GetTimelineByIndex(index)
                               and project.GetTimelineByIndex(index).GetUniqueId()
                               != (current.GetUniqueId() if current else None)), None)
                if target is None:
                    raise JobRefused(
                        f"{KIND} needs a non-current qualification timeline")
                exported = drt_export_probe.run(
                    {"id": job["id"], "params": {
                        "project": QUALIFICATION_PROJECT,
                        "timeline_uid": str(target.GetUniqueId())}},
                    resolve, project=project)
                if not exported.get("archived") or not exported.get("archive_path"):
                    raise JobRefused(
                        f"{KIND} could not create its DRT fixture: {exported!r}")
                archive = Path(exported["archive_path"]).resolve()
            try:
                archive_facts = resolve_archive.read_archive(archive)
                expected = _expected_facts(archive_facts)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise JobRefused(
                    f"{KIND} cannot validate archive: {exc}") from exc
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            timeline_name = f"Ren DRT Restore {digest[:12]}"
            resolve_scratch_projects.create(resolve, manager, scratch_name)
            created = True
            resolve_scratch_projects.open_project(resolve, manager, scratch_name)
            opened = True
            project = manager.GetCurrentProject()
            if not project or project.GetName() != scratch_name:
                raise JobRefused(f"{KIND} could not open its exact scratch project")
            before = _timeline_inventory(project)
            imported = project.GetMediaPool().ImportTimelineFromFile(
                str(archive), {"timelineName": timeline_name,
                               "importSourceClips": False})
            after = _timeline_inventory(project)
            added = {uid: name for uid, name in after.items()
                     if uid not in before}
            matches = [(uid, name) for uid, name in added.items()
                       if name == timeline_name]
            if len(matches) != 1 or len(added) != 1:
                raise JobRefused(
                    f"{KIND} import was ambiguous: returned={bool(imported)}, "
                    f"new_timelines={added!r}")
            uid, name = matches[0]
            timeline = _timeline_by_uid(project, uid)
            # Establishing the cursor is a write to shared Resolve
            # state, so it goes through the guarded implementation -
            # which sets, reads back and refuses without an exclusive
            # lease - under an explicit exclusive section the guard
            # contract recognises, inside the broker's whole-take lease.
            from library.tools.resolve_lock import (
                assert_current_timeline, resolve_lease,
            )
            with resolve_lease(
                    f"{KIND} select imported timeline", exclusive=True):
                assert_current_timeline(project, timeline)
            actual = _timeline_facts(timeline)
            if actual != expected:
                raise JobRefused(
                    f"{KIND} structure mismatch: expected={expected!r}, "
                    f"actual={actual!r}")
            verified = {"timeline_uid": uid, "timeline_name": name,
                        "tracks": actual["tracks"], "items": actual["items"]}
            if not manager.SaveProject():
                raise JobRefused(
                    f"{KIND} could not save the verified scratch project")
            saved = manager.GetCurrentProject()
            readback = _timeline_inventory(saved) if saved else {}
            if (saved is None or saved.GetName() != scratch_name
                    or uid not in readback):
                raise JobRefused(
                    f"{KIND} did not read back the saved scratch import")
            saved_facts = _timeline_facts(_timeline_by_uid(saved, uid))
            if saved_facts != expected:
                raise JobRefused(
                    f"{KIND} saved structure mismatch: expected={expected!r}, "
                    f"actual={saved_facts!r}")
            resolve_scratch_projects.restore_recorded(resolve, manager)
            opened = False
            restored = True
            retirement = resolve_scratch_projects.delete(
                resolve, manager, scratch_name)
            retired = bool(retirement["deleted"] and retirement["verified"])
            if not retired:
                raise JobRefused(f"{KIND} scratch project was not retired")
            return {"project": QUALIFICATION_PROJECT, "archive": str(archive),
                    "archive_sha256": digest, "archive_facts": expected,
                    "imported": verified, "saved_readback": True,
                    "origin_restored": restored, "scratch_retired": retired,
                    "retirement": retirement}
        except Exception as exc:
            cleanup = {"origin_restored": restored,
                       "scratch_retired": retired}
            try:
                current = manager.GetCurrentProject()
                if current and current.GetName() == scratch_name:
                    resolve_scratch_projects.restore_recorded(resolve, manager)
                    opened = False
                    cleanup["origin_restored"] = True
            except Exception as cleanup_exc:  # noqa: BLE001 - best-effort recovery receipt
                cleanup["restore_error"] = str(cleanup_exc)
            try:
                if created and not opened:
                    retirement = resolve_scratch_projects.delete(
                        resolve, manager, scratch_name)
                    cleanup["scratch_retired"] = bool(retirement["deleted"])
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve primary failure
                cleanup["retire_error"] = str(cleanup_exc)
            raise JobRefused(f"{KIND} failed: {exc}; cleanup={cleanup}") from exc

    with qualification_project.borrowed(resolve, qualification_only=True):
        return perform()
