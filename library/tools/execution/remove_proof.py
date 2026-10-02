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

`tests/unit/resolve/test_dead_render_bins.py`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from library.tools import journal_naming

from library.tools.proof_cleanup import (
    PROTECTED_TIMELINES,
    ProofRemovalRefused,
    is_authorised_demo,
    is_authorised_superseded,
)
from library.tools import staging_holds as holds

JOURNAL_PREFIX = "resolve_remove_proof"


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
        "removed_at": datetime.now(timezone.utc).isoformat(),
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
