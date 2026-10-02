"""Retiring the pipeline's dead bins, and only those.

The Resolve half of the retirement rules in
`library/tools/resolve_organization.py`, which holds every rule and no API
calls. Two populations, one deleter: the migration's legacy shells and
the current scheme's orphaned per-reel leaves (a staging name emptied
by promotion, a deleted timeline's bin). The split is the same one
`organise_media_pool.py` uses, and for a
sharper reason here: organising must never delete
(`tests/unit/resolve/test_resolve_organization.py` asserts that of both organiser
files), while THIS module's whole reason to exist is deletion. A
reader can see which file is which.

Two populations, two deletions
------------------------------
1. The migration's EMPTY legacy shells: `DeleteFolders` only, exactly
   as before.  An empty bin strands nothing.
2. The current scheme's DEAD per-reel leaves: a bin under a render bin
   that names no live timeline, retired WITH its contents.  The
   contents go first, as pool ITEMS, and only then does the bin go.

What each call does, and what it is NOT trusted with
----------------------------------------------------
The scripting reference says only this about `DeleteFolders`: it
"deletes the specified subfolders in the media pool" and returns a
bool.  It does NOT say what happens to anything still inside or below
the folder, so the folder call is NEVER made on a bin that holds
anything or has a kept sub-bin below it - proven off a fresh
`read_pool` taken immediately before the call, children before
parents, return value judged (AGENTS.md 5).

`DeleteClips` on a clip's pool item removes the ITEM and leaves the
file on disk; on a TIMELINE's pool item it DELETES THE TIMELINE
(AGENTS.md 5).  So the contents call is never made on anything the
live pool reports as a timeline: every content proxy's `Type` is
re-read at the moment of the call (the plan is a claim about an
earlier moment), and one wrong entry refuses the whole run.  What the
contents call takes is bounded by what the dead-bin proof allowed -
unplaced, pipeline-generated clips under that one bin, re-proven off
a fresh read, with the exact item set the plan named.  Files are
never unlinked here: a removed item's render is still on disk, so a
project that comes back wrong can have its items re-imported, and the
journal names every file path for the file-level sweep.

Reversibility: an empty shell re-creates exactly.  A dead bin
re-creates as an empty shell - its removed items do not come back,
and `revert` says so, naming the files for hand re-import.

`tests/unit/reels/test_reel_retirement.py`, `tests/unit/resolve/test_build_sweep.py`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from library.tools import journal_naming

JOURNAL_PREFIX = "resolve_retirements"
JOURNAL_GLOB = f"{JOURNAL_PREFIX}*.json"


class RetirementRefused(Exception):
    """Something about the retirement set is not safe, so NOTHING further
    is retired. Bins already retired in this run are in the journal."""


def read_bin_tree(project) -> dict[tuple[str, ...], object]:
    """Every sub-bin in the pool by path, as Folder objects.

    `read_pool` reports artefacts, and an empty bin has no artefacts - so
    the shells this module retires are invisible to it. This is the
    second read: the bin objects themselves, which is also what
    `DeleteFolders` takes.
    """
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    out: dict[tuple[str, ...], object] = {}

    def walk(folder, path: tuple[str, ...]) -> None:
        for sub in (folder.GetSubFolderList() or []):
            child = path + (sub.GetName(),)
            out[child] = sub
            walk(sub, child)

    walk(root, ())
    return out


def retire_bins(project, retirements: list[dict],
                journal_path: str,
                project_root: str | None = None) -> dict:
    """Delete the retired bins, deepest first, re-proving each one.

    Each path is re-read before its own deletion: scheme membership
    off the same rules that planned it, occupancy off a fresh
    `read_pool`.  A dead per-reel bin carries its contents: those are
    re-proven (still the exact planned item set, still unplaced clips
    under that bin, still no timeline among them by live proxy read)
    and removed as pool items first - files stay on disk.  The
    journal records every retired path with what it held, and is
    written even when a later deletion refuses.
    """
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import (
        is_retired_canonical_bin,
        is_retired_scheme_bin,
        plan_dead_render_bins,
    )

    pool = project.GetMediaPool()
    before_current = pool.GetCurrentFolder()
    journal = {
        "retired_at": datetime.now(timezone.utc).isoformat(),
        "resolve_project": project.GetName(),
        "retired": [],
    }
    by_path = {tuple(r["path"]): r for r in retirements}

    def index_clips(folder, into: dict) -> None:
        for clip in (folder.GetClipList() or []):
            into[clip.GetUniqueId()] = clip
        for sub in (folder.GetSubFolderList() or []):
            index_clips(sub, into)

    try:
        ordered = sorted((tuple(r["path"]) for r in retirements),
                         key=lambda p: (-len(p), list(p)))
        for path in ordered:
            entry = by_path[path]
            planned_contents = list(entry.get("contents") or [])
            artefacts, _, _, _ = read_pool(project)
            timeline_names = frozenset(
                a.name for a in artefacts if a.kind == "timeline")
            # An emptied canonical per-reel leaf retires like a legacy
            # shell (both populations retire only once empty); a leaf
            # retiring WITH contents goes through the dead-leaf
            # re-proof below instead.
            is_shell = (is_retired_scheme_bin(path, timeline_names)
                        or (not planned_contents
                            and is_retired_canonical_bin(
                                path, timeline_names)))
            dead_here = None
            if not is_shell and planned_contents:
                if project_root is None:
                    raise RetirementRefused(
                        f"{'/'.join(path)!r} retires with "
                        f"{len(planned_contents)} item(s), and no "
                        f"project root was given to re-prove they are "
                        f"the pipeline's. Nothing further was retired.")
                fresh_dead, _declined = plan_dead_render_bins(
                    artefacts, list(read_bin_tree(project)),
                    project_root, timeline_names)
                matches = [d for d in fresh_dead
                           if tuple(d["path"]) == path]
                if not matches:
                    raise RetirementRefused(
                        f"{'/'.join(path)!r} no longer proves dead - "
                        f"a timeline of that name may have landed, or "
                        f"something inside it is now placed. Nothing "
                        f"further was retired.")
                dead_here = matches[0]
                fresh_ids = {c["item_id"]
                             for c in dead_here.get("contents") or []}
                planned_ids = {c["item_id"] for c in planned_contents}
                if fresh_ids != planned_ids:
                    raise RetirementRefused(
                        f"{'/'.join(path)!r} holds a different item set "
                        f"than the plan named "
                        f"(plan {sorted(planned_ids)}, "
                        f"pool {sorted(fresh_ids)}). Nothing further "
                        f"was retired.")
            if not is_shell and dead_here is None:
                raise RetirementRefused(
                    f"{'/'.join(path)!r} is not a pipeline legacy bin "
                    f"or a proven-dead per-reel bin. "
                    f"Nothing further was retired.")
            if is_shell:
                holding = [a for a in artefacts
                           if (tuple(a.folder_path)[:len(path)] == path
                               and len(tuple(a.folder_path)) >= len(path))]
                if holding:
                    raise RetirementRefused(
                        f"{'/'.join(path)!r} is no longer empty - "
                        f"{len(holding)} item(s) sit in it, first "
                        f"{holding[0].name!r}. Nothing further was retired.")
            tree = read_bin_tree(project)
            kept_below = sorted(
                "/".join(b) for b in tree
                if len(b) > len(path) and b[:len(path)] == path)
            if kept_below:
                raise RetirementRefused(
                    f"{'/'.join(path)!r} still has sub-bin(s) below it: "
                    f"{', '.join(kept_below)}. Retiring it would take a "
                    f"bin this plan did not retire. Nothing further was "
                    f"retired.")
            removed_contents: list[dict] = []
            if dead_here is not None:
                proxies: dict = {}
                index_clips(pool.GetRootFolder(), proxies)
                targets = []
                for content in planned_contents:
                    proxy = proxies.get(content["item_id"])
                    if proxy is None:
                        raise RetirementRefused(
                            f"{content['name']!r} is in the plan and not "
                            f"in the pool. Nothing further was retired.")
                    # Re-read the KIND off the live proxy, never trust
                    # the plan: DeleteClips on a timeline's pool item
                    # deletes the timeline.
                    if (proxy.GetClipProperty("Type") or "") == "Timeline":
                        raise RetirementRefused(
                            f"{content['name']!r} reports Type 'Timeline' "
                            f"in the pool and the plan calls it a clip. "
                            f"DeleteClips would delete the timeline. "
                            f"Nothing further was retired.")
                    targets.append((content, proxy))
                for start in range(0, len(targets), 100):
                    chunk = targets[start:start + 100]
                    if not pool.DeleteClips(
                            [proxy for _, proxy in chunk]):
                        raise RetirementRefused(
                            f"DeleteClips returned a falsy answer for "
                            f"{len(chunk)} item(s) under "
                            f"{'/'.join(path)!r} after "
                            f"{len(removed_contents)} had been removed. "
                            f"The journal names those; every file is "
                            f"still on disk.")
                    for content, _proxy in chunk:
                        removed_contents.append({
                            "item_id": content["item_id"],
                            "name": content["name"],
                            "file_path": content.get("file_path", ""),
                            "from": content.get(
                                "folder", "/".join(path)),
                        })
                tree = read_bin_tree(project)
            folder = tree.get(path)
            if folder is None:
                raise RetirementRefused(
                    f"{'/'.join(path)!r} is not in the pool. Nothing "
                    f"further was retired.")
            if not pool.DeleteFolders([folder]):
                raise RetirementRefused(
                    f"DeleteFolders({ '/'.join(path)!r}) returned a falsy "
                    f"answer. The bin is still there and nothing further "
                    f"was retired.")
            journal["retired"].append({"path": "/".join(path),
                                       "occupancy_before":
                                           len(removed_contents),
                                       "contents": removed_contents})
    finally:
        if before_current:
            pool.SetCurrentFolder(before_current)
        Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
        Path(journal_path).write_text(
            json.dumps(journal, indent=2), encoding="utf-8")

    journal["journal_path"] = journal_path
    return {"retired": [r["path"] for r in journal["retired"]],
            "removed_items": sum(len(r.get("contents") or [])
                                 for r in journal["retired"]),
            "journal_path": journal_path}


def revert(project, journal_path: str) -> dict:
    """Re-create the retired shells. They were empty, so empty bins
    restore exactly what was removed.

    A dead per-reel bin re-creates as an EMPTY shell: its removed pool
    items do not come back (their files are still on disk, named in
    the journal for hand re-import), and the return says so rather
    than reading as a full undo."""
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    if journal.get("reverted_at"):
        raise RetirementRefused(
            f"{journal_path} was already reverted at "
            f"{journal['reverted_at']}. Re-creating its bins twice would "
            f"fork duplicates - `AddSubFolder` makes a second bin of the "
            f"same name instead of refusing.")
    from library.tools.execution.organise_media_pool import ensure_folder

    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    before_current = pool.GetCurrentFolder()

    recreated = []
    try:
        paths = sorted(
            (tuple(r["path"].split("/")) for r in journal.get("retired", [])),
            key=len)
        for path in paths:
            parent = root
            for depth, part in enumerate(path):
                parent = ensure_folder(pool, parent, part,
                                       created=recreated,
                                       at_path=path[:depth])
    finally:
        if before_current:
            pool.SetCurrentFolder(before_current)

    journal["reverted_at"] = datetime.now(timezone.utc).isoformat()
    Path(journal_path).write_text(
        json.dumps(journal, indent=2), encoding="utf-8")
    not_restored = [
        {"bin": r["path"], "name": c["name"],
         "file_path": c.get("file_path", "")}
        for r in journal.get("retired", [])
        for c in (r.get("contents") or [])]
    return {"recreated": sorted(set(recreated)),
            "contents_not_restored": not_restored,
            "journal_path": journal_path}


def journal_path_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS retirement's journal goes. One file per run, never
    reused - the same defect `organise_media_pool.journal_path_for`
    closes: a fixed filename lets a later empty run overwrite the record
    an undo needs.  Named through `library/tools/journal_naming.py`, which
    is where that guard actually lives."""
    return journal_naming.unique_path(
        os.path.join(project_folder, "pipeline_output", "review"),
        JOURNAL_PREFIX, when)


def journals(project_folder: str) -> list[dict]:
    """Every retirement journal in this project, newest first."""
    review = Path(project_folder) / "pipeline_output" / "review"
    out = []
    for path in sorted(review.glob(JOURNAL_GLOB), reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        out.append({
            "path": str(path),
            "retired_at": doc.get("retired_at", ""),
            "retired": len(doc.get("retired") or []),
            "reverted_at": doc.get("reverted_at"),
        })
    return out
