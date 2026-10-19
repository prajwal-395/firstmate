"""Deleting firstmate's proof artefacts, on purpose, once.

The Resolve half of `library/tools/proof_cleanup.py`, which holds
every rule and no API calls.  This file exists because `DeleteClips`
on a timeline's pool item DELETES THE TIMELINE - the catastrophic
failure mode everywhere else, and the intended act here, under the
captain's verbatim 2026-09-10 authority (*"yeah clean that up"*) for
the `SOP Proof_...` bin and the `SOP Proof_min-canvas-rail` timeline.
The same file also executes `plan_superseded_removal` plans, under
the captain's separate verbatim 2026-09-10 cleanup brief for the two
superseded Reel 09 timelines - the plan carries which authority
proven it, and the guard below accepts exactly those two names
beside the proof artefacts.

What is re-proven at the moment of the call
-------------------------------------------
The plan is a claim about an earlier moment and this call cannot be
undone, so every fact is read again live: the timeline proxy still
reports `Type == "Timeline"` under its exact planned name, every
content proxy reports a clip kind and no placement, every bin still
exists, and the planned name is still neither protected nor foreign
to the proof prefix.  One wrong entry refuses the whole run before
anything is deleted.

Order: pool items first (`DeleteClips` leaves every file on disk),
then bins deepest first (`DeleteFolders` judged per call).  Files are
never unlinked here.  The journal names the timeline, every bin, and
every removed item with the file behind it - it is the record, and
there is no revert: a deleted timeline does not come back.

`tests/unit/resolve/test_build_sweep.py`.
"""
from __future__ import annotations

import json
import os
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from library.tools import journal_naming
from library.tools import staging_holds as holds
from library.tools.proof_cleanup import (
    PROTECTED_TIMELINES,
    ProofRemovalRefused,
    captain_task_timeline_authority,
    is_authorised_demo,
    is_authorised_superseded,
)
from library.tools.resource_leases import (
    LeaseDeclaration,
    ResourceRef,
    resource_lease,
)

JOURNAL_PREFIX = "resolve_remove_proof"


def timeline_inventory(project) -> list[dict]:
    """The exact identity and row counts used around a retirement."""
    pool = project.GetMediaPool()
    stack = [pool.GetRootFolder()]
    pool_timelines = {}
    while stack:
        folder = stack.pop()
        for item in folder.GetClipList() or []:
            if (item.GetClipProperty("Type") or "") == "Timeline":
                pool_timelines.setdefault(str(item.GetName() or ""), []).append(item)
        stack.extend(folder.GetSubFolderList() or [])
    rows = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is None:
            raise ProofRemovalRefused(
                f"timeline {index} became unreadable during inventory")
        name = str(timeline.GetName() or "")
        pool_matches = pool_timelines.get(name, [])
        if len(pool_matches) != 1:
            raise ProofRemovalRefused(
                f"expected one media-pool timeline item for {name!r}, "
                f"found {len(pool_matches)}")
        rows.append({
            "item_id": str(timeline.GetUniqueId() or ""),
            "pool_item_id": str(pool_matches[0].GetUniqueId() or ""),
            "name": name,
            "video_tracks": int(timeline.GetTrackCount("video") or 0),
            "audio_tracks": int(timeline.GetTrackCount("audio") or 0),
        })
    return sorted(rows, key=lambda row: (row["item_id"], row["name"]))


def captain_task_target_inventory(project, target_names: set[str]) -> list[dict]:
    """The complete pre-retirement inventory, after exact-name proof."""
    rows = timeline_inventory(project)
    for name in sorted(target_names):
        found = [row for row in rows if row["name"] == name]
        if len(found) > 1:
            raise ProofRemovalRefused(
                f"{project.GetName()!r} has {len(found)} timelines named "
                f"{name!r}; exact-name retirement is ambiguous.")
        if found:
            captain_task_timeline_authority(
                str(project.GetName()), name, found[0]["item_id"])
    return rows


def _pool_proxy_by_id(project, item_id: str):
    pool = project.GetMediaPool()
    stack = [pool.GetRootFolder()]
    while stack:
        folder = stack.pop()
        for item in folder.GetClipList() or []:
            if str(item.GetUniqueId() or "") == item_id:
                return item
        stack.extend(folder.GetSubFolderList() or [])
    return None


def retire_captain_task_timeline(
        resolve, project, *, project_name: str, timeline_name: str,
        item_id: str, backup_path: str, journal_path: str,
        expected_inventory: list[dict]) -> dict:
    """Retire one exact timeline under the 2026-10-19 authority.

    This is the named extension of proof cleanup for the captain's task.
    It backs up the target as a DRT first, deletes only its pool timeline
    item, then verifies the complete surviving inventory against the
    captured baseline. The caller holds the exclusive Ren instance lease
    across the whole cleanup session.
    """
    from library.tools.resolve_lock import held

    if not held():
        raise ProofRemovalRefused(
            "the Resolve instance lease is not held. Nothing was removed.")
    if str(project.GetName() or "") != project_name:
        raise ProofRemovalRefused(
            f"expected project {project_name!r}, found "
            f"{project.GetName()!r}. Nothing was removed.")
    authority = captain_task_timeline_authority(
        project_name, timeline_name, item_id)
    before = timeline_inventory(project)
    if before != expected_inventory:
        raise ProofRemovalRefused(
            "the live timeline inventory changed since it was captured. "
            "Nothing was removed.")
    matches = [row for row in before
               if row["item_id"] == item_id and row["name"] == timeline_name]
    same_name = [row for row in before if row["name"] == timeline_name]
    if len(matches) != 1 or len(same_name) != 1:
        raise ProofRemovalRefused(
            f"expected one exact timeline {timeline_name!r} ({item_id}); "
            f"found {len(matches)} matching ids and {len(same_name)} exact "
            "names. Nothing was removed.")

    destination = Path(backup_path)
    if destination.exists():
        raise ProofRemovalRefused(
            f"recovery archive already exists at {destination}; "
            "refusing to overwrite it. Nothing was removed.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    timeline = None
    named_timelines = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        candidate = project.GetTimelineByIndex(index)
        if (candidate and str(candidate.GetName() or "") == timeline_name
                and str(candidate.GetUniqueId() or "") == item_id):
            named_timelines.append(candidate)
    if len(named_timelines) == 1:
        timeline = named_timelines[0]
    if timeline is None:
        raise ProofRemovalRefused(
            "the target identity changed before its recovery archive. "
            "Nothing was removed.")
    planned_row = matches[0]
    pool_item = _pool_proxy_by_id(project, planned_row["pool_item_id"])
    if (pool_item is None
            or (pool_item.GetClipProperty("Type") or "") != "Timeline"):
        raise ProofRemovalRefused(
            f"{timeline_name!r} no longer reports Type 'Timeline'. "
            "Nothing was removed.")
    export_flag = getattr(resolve, "EXPORT_DRT", None)
    if export_flag is None:
        raise ProofRemovalRefused(
            "Resolve does not expose EXPORT_DRT. Nothing was removed.")
    if not timeline.Export(str(destination), export_flag):
        raise ProofRemovalRefused(
            f"Resolve refused the DRT backup for {timeline_name!r}. "
            "Nothing was removed.")
    if (destination.is_symlink() or not destination.is_file()
            or destination.stat().st_size == 0
            or not zipfile.is_zipfile(destination)):
        raise ProofRemovalRefused(
            f"the DRT backup at {destination} is missing, empty, or invalid. "
            "Nothing was removed.")

    journal = {
        "authority": authority,
        "removed_at": datetime.now(UTC).isoformat(),
        "resolve_project": project_name,
        "timeline": {"item_id": item_id, "name": timeline_name},
        "drt_backup": str(destination),
        "before": before,
        "verified_untouched": [row for row in before
                               if row["item_id"] != item_id],
        "removed": False,
    }
    journal_file = Path(journal_path)
    journal_file.parent.mkdir(parents=True, exist_ok=True)
    journal_file.write_text(json.dumps(journal, indent=2), encoding="utf-8")
    pool = project.GetMediaPool()
    if not pool.DeleteClips([pool_item]):
        raise ProofRemovalRefused(
            f"DeleteClips refused {timeline_name!r}; its DRT backup is "
            f"{destination}.")
    journal["removed"] = True
    journal["after"] = timeline_inventory(project)
    expected_after = [row for row in before if row["item_id"] != item_id]
    if journal["after"] != expected_after:
        journal["verification"] = "failed: surviving inventory differs"
        journal_file.write_text(json.dumps(journal, indent=2),
                                encoding="utf-8")
        raise ProofRemovalRefused(
            f"{timeline_name!r} was deleted, but the live inventory differs "
            f"from the expected survivors. Recovery archive: {destination}; "
            f"journal: {journal_file}.")
    journal["verification"] = "passed"
    journal_file.write_text(json.dumps(journal, indent=2), encoding="utf-8")
    return {"removed": timeline_name, "item_id": item_id,
            "drt_backup": str(destination),
            "verified_untouched": len(expected_after),
            "journal_path": str(journal_file)}


def remove_proof(project, plan: dict, journal_path: str) -> dict:
    """Delete the planned proof timeline, its bins' contents, and the bins."""
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.execution.retire_empty_bins import read_bin_tree

    timeline = plan["timeline"]
    superseded = is_authorised_superseded(timeline["name"])
    if ((timeline["name"] in PROTECTED_TIMELINES and not superseded)
            or (not is_authorised_demo(timeline["name"]) and not superseded)):
        raise ProofRemovalRefused(
            f"{timeline['name']!r} is protected or not an authorised "
            f"demo artefact - the plan was not proven. Nothing was "
            f"removed.")

    pool = project.GetMediaPool()
    before_current = pool.GetCurrentFolder()
    journal = {
        "removed_at": datetime.now(UTC).isoformat(),
        "resolve_project": project.GetName(),
        "timeline": dict(timeline),
        "bins": [],
        "removed_items": [],
    }
    # Re-proven LIVE, like every other fact here: the plan is a claim
    # about an earlier moment, and a hold taken between plan and
    # apply - a build staging while the operator sweeps - must still
    # refuse (issue #971). The project folder is read off the journal
    # path (`<project>/pipeline_output/review/<journal>`); a folder
    # with no holds file reads as no holds, which is the old
    # behaviour exactly. A journal path too shallow to name a
    # project carries no project to check and skips this one
    # re-proof - every journal this module writes names the project.
    parents = Path(journal_path).parents
    if len(parents) > 2:
        live_holds = holds.read_holds(str(parents[2]))
        if timeline["name"] in live_holds:
            raise ProofRemovalRefused(
                holds.refusal_message(timeline["name"],
                                      live_holds[timeline["name"]]))
    # What this run may delete, named before the first deletion:
    # the proof timeline itself, every retired bin path, and the
    # contents by file path (the spelling the prune pass uses)
    # falling back to item id. A prune on a shared file, or a
    # filing pass re-homing a shared bin, serialises against the
    # removal instead of racing it.
    _proof_bins = set()
    _proof_files = set()
    for _bin in plan.get("bins", []):
        if "/".join(_bin["path"]):
            _proof_bins.add("/".join(_bin["path"]))
        for _content in (_bin.get("contents") or []):
            _file = _content.get("file_path", "")
            _proof_files.add(_file if _file
                             else _content["item_id"])
    with resource_lease(
        LeaseDeclaration(
            exclusive=(
                frozenset({ResourceRef("timeline", timeline["name"])})
                | frozenset({ResourceRef("media_pool_bin", _bin)
                             for _bin in _proof_bins})
                | frozenset({ResourceRef("clip", _file)
                             for _file in _proof_files})),
        ),
        purpose=f"remove proof {timeline['name']}",
    ):
        try:
            artefacts, _, _, _ = read_pool(project)
            by_id = {a.item_id: a for a in artefacts}

            live = by_id.get(timeline["item_id"])
            if live is None or live.kind != "timeline" \
                    or live.name != timeline["name"]:
                raise ProofRemovalRefused(
                    f"timeline {timeline['name']!r} is not in the pool as "
                    f"planned. Nothing was removed.")

            proxies: dict = {}

            def index_clips(folder) -> None:
                for clip in (folder.GetClipList() or []):
                    proxies[clip.GetUniqueId()] = clip
                for sub in (folder.GetSubFolderList() or []):
                    index_clips(sub)

            index_clips(pool.GetRootFolder())

            timeline_proxy = proxies.get(timeline["item_id"])
            if timeline_proxy is None:
                raise ProofRemovalRefused(
                    f"timeline {timeline['name']!r} has no pool item. "
                    f"Nothing was removed.")
            if (timeline_proxy.GetClipProperty("Type") or "") != "Timeline":
                raise ProofRemovalRefused(
                    f"{timeline['name']!r} no longer reports Type "
                    f"'Timeline' in the pool. Nothing was removed.")

            targets = [(timeline["name"], timeline_proxy, "timeline", "")]
            doomed = timeline["name"]
            for bin_entry in plan["bins"]:
                for content in bin_entry.get("contents") or []:
                    artefact = by_id.get(content["item_id"])
                    survivors = (set(artefact.placed_by or ())
                                 - {doomed} if artefact is not None else set())
                    if artefact is None or artefact.kind != "clip" \
                            or survivors:
                        raise ProofRemovalRefused(
                            f"{content['name']!r} is still played by a "
                            f"surviving timeline. Nothing was removed.")
                    proxy = proxies.get(content["item_id"])
                    if proxy is None:
                        raise ProofRemovalRefused(
                            f"{content['name']!r} is in the plan and not in "
                            f"the pool. Nothing was removed.")
                    if (proxy.GetClipProperty("Type") or "") == "Timeline":
                        raise ProofRemovalRefused(
                            f"{content['name']!r} reports Type 'Timeline' "
                            f"in the pool. Nothing was removed.")
                    targets.append((content["name"], proxy, "clip",
                                    content.get("file_path", "")))

            if not pool.DeleteClips([proxy for _, proxy, _, _ in targets]):
                raise ProofRemovalRefused(
                    f"DeleteClips returned a falsy answer for "
                    f"{len(targets)} item(s) of proof "
                    f"{timeline['name']!r}. The bin(s) are still there "
                    f"and nothing further was removed.")
            journal["removed_items"] = [
                {"name": name, "kind": kind, "file_path": file_path}
                for name, _proxy, kind, file_path in targets]

            tree = read_bin_tree(project)
            for bin_entry in sorted(
                    plan["bins"], key=lambda e: ([-len(e["path"])]
                                                 + list(e["path"]))):
                path = tuple(bin_entry["path"])
                folder = tree.get(path)
                if folder is None:
                    raise ProofRemovalRefused(
                        f"{'/'.join(path)!r} is not in the pool. The "
                        f"timeline and items are already gone; the "
                        f"journal names them.")
                kept_below = sorted(
                    "/".join(b) for b in tree
                    if len(b) > len(path) and b[:len(path)] == path)
                if kept_below:
                    raise ProofRemovalRefused(
                        f"{'/'.join(path)!r} still has sub-bin(s) below "
                        f"it: {', '.join(kept_below)}. Nothing further "
                        f"was removed.")
                if not pool.DeleteFolders([folder]):
                    raise ProofRemovalRefused(
                        f"DeleteFolders({'/'.join(path)!r}) returned a "
                        f"falsy answer. Nothing further was removed.")
                journal["bins"].append({
                    "path": "/".join(path),
                    "contents": bin_entry.get("contents") or []})
                del tree[path]
        finally:
            if before_current:
                pool.SetCurrentFolder(before_current)
            Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
            Path(journal_path).write_text(
                json.dumps(journal, indent=2), encoding="utf-8")

    journal["journal_path"] = journal_path
    return {"timeline": timeline["name"],
            "removed_items": len(journal["removed_items"]),
            "bins": [b["path"] for b in journal["bins"]],
            "verified_untouched": [u["name"] for u in
                                   plan.get("verified_untouched") or []],
            "journal_path": journal_path}


def journal_path_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS proof removal's journal goes. One file per run.

    Suffixed past a collision: removals seconds apart share a
    second-granularity stamp, and the second journal must not
    overwrite the first - measured 2026-09-10, when nine consecutive
    removals landed in five journal files.  The stamp stays
    human-readable; only the disambiguator is appended.  Both halves are
    `library/tools/journal_naming.py` now: this module had the loop and the
    other four journal writers did not.
    """
    return journal_naming.unique_path(
        os.path.join(project_folder, "pipeline_output", "review"),
        JOURNAL_PREFIX, when)
