"""Retiring the migration's empty shells, and only those.

The Resolve half of the retirement rules in
`library/tools/resolve_organization.py`, which holds every rule and no API
calls. The split is the same one `organise_media_pool.py` uses, and for a
sharper reason here: organising must never delete
(`tests/test_resolve_organization.py` asserts that of both organiser
files), while THIS module's whole reason to exist is one deletion. A
reader can see which file is which.

What `DeleteFolders` does, and what it is NOT trusted with
----------------------------------------------------------
The scripting reference says only this: `DeleteFolders([subfolders])`
"deletes the specified subfolders in the media pool" and returns a bool.
It does NOT say what happens to anything still inside or below the
folder - whether a non-empty folder refuses, empties, or takes its
contents with it - and the AGENTS.md 5 record is about a DIFFERENT call
(`DeleteClips` on a timeline's pool item DELETES THE TIMELINE). So the
folder call gets its own proof rather than borrowing that one's:

- The call is NEVER made on a bin that holds anything. Emptiness means
  zero artefacts in the whole subtree AND no kept sub-bin standing under
  it, proven off a fresh `read_pool` taken immediately before the call -
  not off the plan, which is a claim about an earlier moment. A bin that
  gained an item between plan and apply refuses instead of deleting.
- Children are deleted before parents, so no call ever has a retired
  sub-bin below it - the one recursion shape the reference leaves
  undefined never arises from here.
- The return value is judged (AGENTS.md 5): a falsy answer stops the
  run with `RetirementRefused`, and the bins already retired are in the
  journal.
- The call takes Folder objects, never pool items, so the `DeleteClips`
  failure mode - a timeline's pool item deleting the timeline - cannot
  trigger through this path: an empty folder holds no timelines by the
  proof above.

Reversibility: an empty bin carries nothing, so re-creating its path
restores exactly what was removed. The journal names every retired path
deepest first; `revert` re-creates them shallowest first and refuses a
journal that was already reverted.

`tests/test_retire_empty_shells.py`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

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
                journal_path: str) -> dict:
    """Delete the retired bins, deepest first, re-proving each one.

    Each path is re-read before its own deletion: occupancy off a fresh
    `read_pool`, scheme membership off the same rules that planned it.
    The journal records every retired path with the occupancy that
    proved it, and is written even when a later deletion refuses.
    """
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import (
        is_retired_scheme_bin,
    )

    pool = project.GetMediaPool()
    before_current = pool.GetCurrentFolder()
    journal = {
        "retired_at": datetime.now(timezone.utc).isoformat(),
        "resolve_project": project.GetName(),
        "retired": [],
    }

    try:
        ordered = sorted((tuple(r["path"]) for r in retirements),
                         key=lambda p: (-len(p), list(p)))
        for path in ordered:
            artefacts, _, _, _ = read_pool(project)
            timeline_names = frozenset(
                a.name for a in artefacts if a.kind == "timeline")
            if not is_retired_scheme_bin(path, timeline_names):
                raise RetirementRefused(
                    f"{'/'.join(path)!r} is not a pipeline legacy bin. "
                    f"Nothing further was retired.")
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
                                       "occupancy_before": 0})
    finally:
        if before_current:
            pool.SetCurrentFolder(before_current)
        Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
        Path(journal_path).write_text(
            json.dumps(journal, indent=2), encoding="utf-8")

    journal["journal_path"] = journal_path
    return {"retired": [r["path"] for r in journal["retired"]],
            "journal_path": journal_path}


def revert(project, journal_path: str) -> dict:
    """Re-create the retired shells. They were empty, so empty bins
    restore exactly what was removed."""
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
    return {"recreated": sorted(set(recreated)),
            "journal_path": journal_path}


def journal_path_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS retirement's journal goes. One file per run, never
    reused - the same defect `organise_media_pool.journal_path_for`
    closes: a fixed filename lets a later empty run overwrite the record
    an undo needs."""
    stamp = when or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(project_folder, "pipeline_output", "review",
                        f"{JOURNAL_PREFIX}_{stamp}.json")


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
