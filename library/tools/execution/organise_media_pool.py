"""Reading the media pool, and filing it the way its evidence says.

The Resolve half of `library/tools/resolve_organization.py`, which holds
every rule and no API calls.  Split that way because the rules are worth
testing without the application running, and because what this file
learned about the API is worth writing down next to the calls.

What the API will and will not do, measured on 21.0.0b.28
---------------------------------------------------------
- `MediaPool.AddSubFolder(parent, name)` **creates a SECOND folder when
  one of that name already exists**, with a different `GetUniqueId()`.
  Calling it on every run silently forks the layout, so `ensure_folder`
  looks the name up first and only creates when nothing answers.
  `DeleteFolders` then removes one of the pair and leaves the other,
  which is how the duplicate is easy to make and hard to notice.
- `AddSubFolder` also SETS the current folder to the one it made.
  `CreateEmptyTimeline` and `ImportMedia` put what they make into the
  current folder, so anything that creates a bin mid-build changes where
  the next timeline lands.  This module restores the current folder it
  found.
- `MoveClips([item], folder)` moves a TIMELINE's pool item as happily as
  a clip's, and moving it back restores the original state exactly -
  proven both ways on the captain's own project before anything else ran.
- `Timeline.GetMediaPoolItem()` returns the pool item for a timeline, so
  a timeline never has to be found by name.
- `MediaPoolItem.SetMetadata(key, value)` returns **False and stores
  nothing** for a key Resolve does not already know.  Accepted here:
  `Comments`, `Keywords`, `Description`, `Scene`, `Shot`, `Take`,
  `Angle`, `Reel Number`, `Move`, `Day / Night`, `Camera #`,
  `Production Name`, `Episode Name`, `Shot Type`, `Environment`,
  `Genre`, `People`, `Location`.  Refused: `Reel Name`, `Good Take`,
  `Clip Directory`, `Slate TC`, `Subclip`.
- `SetClipColor` / `ClearClipColor` / `AddFlag` / `ClearFlags` all work
  on a timeline's pool item.
- **Metadata and clip colour on a timeline's pool item SURVIVE a project
  close and reopen.**  Proven the only way it can be: a throwaway
  project, an empty timeline, `SetMetadata` + `SetClipColor`,
  `SaveProject`, `CloseProject`, `LoadProject`, read back - `Keywords`
  and `Clip Color` both came back.  So a reel found in Resolve tomorrow
  still names the plan hash that built it.
- **`ExportProject` does NOT carry either of them.**  Same values,
  exported to `.drp` and unpacked: the bin tree is there
  (`MediaPool/Master/001_Reels/002_Current plan/MpFolder.xml`) and the
  keywords and colours are not.  A project handed to another editor as
  a `.drp` arrives organised and unstamped, and `resolve-organize`
  re-derives the stamps from the plan records on disk.

**Do not read metadata back out of `Project.db` to check any of this.**
Two instruments said "it does not persist" and both were wrong:
`SELECT ... WHERE CAST(col AS BLOB) LIKE '%needle%'` silently matches
nothing in SQLite where `instr(col, x'...')` matches, and the `.drp`
export omits the table the value passes through (`BtLockableBlob`,
which is a staging area - the value is there right after `SaveProject`
and gone from it later, and where it finally rests was not found).  The
close-and-reopen above is the instrument that answers.
- **Smart bins are not scriptable.**  `GetSmartBinList`, `AddSmartBin`,
  `CreateSmartBin` and `GetSmartFolderList` are all absent from
  `MediaPool`, while `Sm2MpSmartFolder` exists in `Project.db` - the
  feature is in the file format and not in the API.
- **A folder cannot be coloured.**  `Sm2MpFolder` has a `ColorTag`
  column and no `SetColor`, `SetClipColor` or `SetFolderColor` exists on
  a Folder.  State is carried on the CLIPS instead, which is why
  `STATE_CLIP_COLOURS` is on the item and not on the bin.
- `hasattr` is True for every name including invented ones, exactly as
  AGENTS.md 5 says; `getattr(obj, name, None)` returns None for a name
  the proxy does not have, and that is what the probes above used.

Nothing here deletes
--------------------
There is no `DeleteFolders`, `DeleteClips` or `DeleteTimelines` call in
this file, and `tests/test_resolve_organization.py` asserts their
absence.  A revert moves items back and restores metadata; it reports
the bins it cannot un-create rather than deleting them.

`tests/test_resolve_organization.py`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from library.tools.resolve_organization import (
    Artefact,
    OrganizationError,
    Plan,
    plan_organization,
    state_from_keywords,
)

JOURNAL_PREFIX = "resolve_placements"
JOURNAL_GLOB = f"{JOURNAL_PREFIX}*.json"


def read_pool(project) -> tuple[list[Artefact], list[str], dict[str, str], str]:
    """Everything the filing rules need, read off a live project.

    Returns `(artefacts, duplicate_bin_names, recorded_states, root_name)`.

    "Which timeline places this item" is measured by walking every
    timeline's tracks, not by parsing a filename.  It costs one pass and
    it is the only source that can say an item is placed NOWHERE, which
    on the field test is 1,216 of 2,583 items.
    """
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    if not root:
        raise OrganizationError(
            "the media pool has no root folder. GetRootFolder() returned "
            f"{root!r} on project {project.GetName()!r}.")
    # Judge a Resolve call by what it RETURNS (AGENTS.md 5). A stand-in
    # answers every call with another stand-in, so the whole pass runs
    # and produces a plan made of objects - which surfaces far away, as
    # a serialisation error in the journal, instead of here.
    if not isinstance(root.GetName(), str):
        raise OrganizationError(
            f"the media pool's root folder answered GetName() with "
            f"{type(root.GetName()).__name__}, not a string. This is not "
            f"a live Resolve media pool, and organising it would file "
            f"nothing while reporting that it had.")

    placed_by: dict[str, set] = {}
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if not timeline:
            continue
        name = timeline.GetName()
        for track_type in ("video", "audio"):
            for track in range(1, (timeline.GetTrackCount(track_type) or 0) + 1):
                for item in (timeline.GetItemListInTrack(track_type, track) or []):
                    pool_item = item.GetMediaPoolItem()
                    if pool_item:
                        placed_by.setdefault(
                            pool_item.GetUniqueId(), set()).add(name)

    artefacts: list[Artefact] = []
    duplicates: list[str] = []
    recorded: dict[str, str] = {}

    def walk(folder, path: tuple[str, ...]) -> None:
        seen = set()
        for sub in (folder.GetSubFolderList() or []):
            sub_name = sub.GetName()
            if sub_name in seen:
                duplicates.append("/".join(path + (sub_name,)))
            seen.add(sub_name)
            walk(sub, path + (sub_name,))
        for clip in (folder.GetClipList() or []):
            item_id = clip.GetUniqueId()
            kind = ("timeline"
                    if (clip.GetClipProperty("Type") or "") == "Timeline"
                    else "clip")
            artefacts.append(Artefact(
                item_id=item_id,
                name=clip.GetName(),
                kind=kind,
                file_path=clip.GetClipProperty("File Path") or "",
                placed_by=tuple(sorted(placed_by.get(item_id, ()))),
                folder_path=path,
            ))
            if kind == "timeline":
                state = state_from_keywords(clip.GetMetadata("Keywords") or "")
                if state:
                    recorded[item_id] = state

    walk(root, ())
    return artefacts, duplicates, recorded, root.GetName()


def plan_for_project(project, project_folder: str,
                     master_timeline_name: str) -> tuple[Plan, list[Artefact], list[str], dict[str, str]]:
    """Read the project and the plan records, and decide what belongs where."""
    from library.tools.plan_provenance import archived_timeline_names, read_provenance

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    provenance = read_provenance(review_dir) or {}
    artefacts, duplicates, recorded, root_name = read_pool(project)
    plan = plan_organization(
        artefacts=artefacts,
        project_root=project_folder,
        master_timeline_name=master_timeline_name,
        built_reels=provenance.get("built_reels") or [],
        archived_plan_names=archived_timeline_names(review_dir),
        root_bin=root_name,
        plan_hash=provenance.get("plan_content_hash") or "",
        built_at=provenance.get("built_at") or "",
    )
    return plan, artefacts, duplicates, recorded


def ensure_folder(pool, parent, name: str,
                  created: list[str] | None = None,
                  at_path: tuple[str, ...] = ()):
    """The subfolder called `name` under `parent`, made only if absent.

    `AddSubFolder` does not check - it makes a second folder of the same
    name with a different id, and then half the reels file into each.
    Looking the name up first is the whole difference between an
    idempotent layout and one that forks on every run.

    A folder this call CREATES is appended to `created` by its full path,
    so a revert can name the bins it is leaving behind.
    """
    for sub in (parent.GetSubFolderList() or []):
        if sub.GetName() == name:
            return sub
    made = pool.AddSubFolder(parent, name)
    if not made:
        raise OrganizationError(
            f"AddSubFolder({parent.GetName()!r}, {name!r}) returned "
            f"{made!r}. Nothing was moved.")
    if created is not None:
        created.append("/".join(at_path + (name,)))
    return made


def _resolve_path(pool, root, path: tuple[str, ...], created: list[str]):
    folder = root
    for depth, part in enumerate(path):
        folder = ensure_folder(pool, folder, part, created, path[:depth])
    return folder


def _find_path(root, path: tuple[str, ...]):
    """An EXISTING folder at `path`, or None. Creates nothing."""
    folder = root
    for part in path:
        match = None
        for sub in (folder.GetSubFolderList() or []):
            if sub.GetName() == part:
                match = sub
                break
        if match is None:
            return None
        folder = match
    return folder


def apply_plan(project, plan: Plan, artefacts: list[Artefact],
               journal_path: str) -> dict:
    """File the pool the way the plan says, and journal every change.

    The journal records, per item, the bin it came FROM and the metadata
    it carried BEFORE - so `revert` needs nothing but the file.  It is
    written even when a later step fails, because a half-applied move
    that nobody recorded is the one outcome with no way back.
    """
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    before_current = pool.GetCurrentFolder()

    by_id = {a.item_id: a for a in artefacts}
    clip_by_id = {}

    def index_clips(folder) -> None:
        for clip in (folder.GetClipList() or []):
            clip_by_id[clip.GetUniqueId()] = clip
        for sub in (folder.GetSubFolderList() or []):
            index_clips(sub)

    index_clips(root)

    created: list[str] = []
    journal = {
        "organised_at": datetime.now(timezone.utc).isoformat(),
        "resolve_project": project.GetName(),
        "root_bin": plan.root_bin,
        "moves": [],
        "stamps": [],
        "folders_created": [],
    }

    try:
        by_destination: dict[tuple[str, ...], list] = {}
        for verdict in plan.moves:
            clip = clip_by_id.get(verdict.item_id)
            if clip is None:
                raise OrganizationError(
                    f"{verdict.name!r} is in the plan and not in the "
                    f"media pool. Nothing was moved.")
            by_destination.setdefault(verdict.destination, []).append(
                (verdict, clip))

        for destination, entries in by_destination.items():
            folder = _resolve_path(pool, root, destination, created)
            moved = pool.MoveClips([clip for _, clip in entries], folder)
            if not moved:
                raise OrganizationError(
                    f"MoveClips into {'/'.join(destination)} returned "
                    f"{moved!r} for {len(entries)} item(s). "
                    f"{len(journal['moves'])} item(s) had already moved - "
                    f"the journal at the path this call was given reverts "
                    f"them.")
            for verdict, _clip in entries:
                journal["moves"].append({
                    "item_id": verdict.item_id,
                    "name": verdict.name,
                    "kind": verdict.kind,
                    "from": "/".join(by_id[verdict.item_id].folder_path),
                    "to": "/".join(destination),
                })

        for stamp in plan.stamps:
            clip = clip_by_id.get(stamp["item_id"])
            if clip is None:
                continue
            entry = {"item_id": stamp["item_id"], "name": stamp["name"],
                     "before": {}, "after": {}}
            for key, value in stamp["fields"].items():
                entry["before"][key] = clip.GetMetadata(key) or ""
                if not clip.SetMetadata(key, value):
                    raise OrganizationError(
                        f"SetMetadata({key!r}, ...) returned False on "
                        f"{stamp['name']!r}. Resolve refuses a key it does "
                        f"not already know and stores nothing; the moves "
                        f"already made are in the journal.")
                entry["after"][key] = value
            entry["before"]["ClipColor"] = clip.GetClipProperty("Clip Color") or ""
            if not clip.SetClipColor(stamp["clip_color"]):
                raise OrganizationError(
                    f"SetClipColor({stamp['clip_color']!r}) returned False "
                    f"on {stamp['name']!r}.")
            entry["after"]["ClipColor"] = stamp["clip_color"]
            journal["stamps"].append(entry)
    finally:
        # In `finally` because a raise part-way through has still made
        # bins and still moved items, and a journal that omits them is
        # the one outcome with no way back.
        journal["folders_created"] = list(created)
        if before_current:
            pool.SetCurrentFolder(before_current)
        Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
        Path(journal_path).write_text(
            json.dumps(journal, indent=2), encoding="utf-8")

    journal["journal_path"] = journal_path
    return journal


def revert(project, journal_path: str) -> dict:
    """Put everything the journal names back where it was.

    Moves are undone by moving; metadata and clip colour are restored to
    the values recorded before.  **Bins are not un-created** - undoing a
    create means deleting, and this module does not delete - so they are
    returned by name for the operator to remove by hand if they want to.
    """
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    if journal.get("reverted_at"):
        raise OrganizationError(
            f"{journal_path} was already reverted at "
            f"{journal['reverted_at']}. Reverting it twice would move "
            f"items to where they sat before an apply that has already "
            f"been undone, which is not where they are now.")
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    before_current = pool.GetCurrentFolder()

    clip_by_id = {}

    def index_clips(folder) -> None:
        for clip in (folder.GetClipList() or []):
            clip_by_id[clip.GetUniqueId()] = clip
        for sub in (folder.GetSubFolderList() or []):
            index_clips(sub)

    index_clips(root)

    undone, missing = [], []
    try:
        by_origin: dict[str, list] = {}
        for move in journal.get("moves", []):
            clip = clip_by_id.get(move["item_id"])
            if clip is None:
                missing.append(move["name"])
                continue
            by_origin.setdefault(move["from"], []).append((move, clip))

        for origin, entries in by_origin.items():
            path = tuple(p for p in origin.split("/") if p)
            folder = _find_path(root, path) if path else root
            if folder is None:
                missing.extend(move["name"] for move, _ in entries)
                continue
            if not pool.MoveClips([clip for _, clip in entries], folder):
                raise OrganizationError(
                    f"MoveClips back into {origin or '(root)'} returned "
                    f"False for {len(entries)} item(s).")
            undone.extend(move["name"] for move, _ in entries)

        for stamp in journal.get("stamps", []):
            clip = clip_by_id.get(stamp["item_id"])
            if clip is None:
                continue
            for key, value in stamp["before"].items():
                if key == "ClipColor":
                    if value:
                        clip.SetClipColor(value)
                    else:
                        clip.ClearClipColor()
                else:
                    clip.SetMetadata(key, value)
    finally:
        if before_current:
            pool.SetCurrentFolder(before_current)

    journal["reverted_at"] = datetime.now(timezone.utc).isoformat()
    Path(journal_path).write_text(
        json.dumps(journal, indent=2), encoding="utf-8")

    return {
        "moved_back": undone,
        "not_found": missing,
        "bins_left_behind": journal.get("folders_created", []),
        "journal_path": journal_path,
    }


def journal_path_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS apply's journal goes. One file per apply, never reused.

    A fixed filename was the first shape and it is wrong: the second
    apply moves nothing, writes a journal saying so over the first one,
    and the whole organisation becomes irreversible - data loss wearing
    the shape of a write, the same defect `write_provenance` was fixed
    for.  So the name carries a UTC timestamp, exactly as `archive_plan`
    names its copies, and nothing here ever overwrites a journal.
    """
    stamp = when or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(project_folder, "pipeline_output", "review",
                        f"{JOURNAL_PREFIX}_{stamp}.json")


def journals(project_folder: str) -> list[dict]:
    """Every journal in this project, newest first, with what it holds.

    So an operator three days later can see which apply to undo instead
    of guessing at a filename.  A journal that has already been reverted
    says so.
    """
    review = Path(project_folder) / "pipeline_output" / "review"
    out = []
    for path in sorted(review.glob(JOURNAL_GLOB), reverse=True):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        out.append({
            "path": str(path),
            "organised_at": doc.get("organised_at", ""),
            "moves": len(doc.get("moves") or []),
            "stamps": len(doc.get("stamps") or []),
            "reverted_at": doc.get("reverted_at"),
        })
    return out


def organise_project(project, project_folder: str,
                     master_timeline_name: str,
                     apply: bool = False,
                     journal_path: str | None = None) -> dict:
    """Plan, and apply only when asked. Returns both halves of the record."""
    plan, artefacts, duplicates, _recorded = plan_for_project(
        project, project_folder, master_timeline_name)
    result = {"plan": plan.as_dict(), "duplicate_bins": duplicates,
              "applied": False}
    if apply:
        result["journal"] = apply_plan(
            project, plan, artefacts,
            journal_path or journal_path_for(project_folder))
        result["applied"] = True
    return result


def check_project(project, project_folder: str,
                  master_timeline_name: str) -> list[dict]:
    """What is wrong with the project as it stands, as findings."""
    from library.tools.resolve_organization import findings

    plan, artefacts, duplicates, recorded = plan_for_project(
        project, project_folder, master_timeline_name)
    return findings(artefacts, plan, duplicates, recorded)


def open_project(project_folder: str):
    """The Resolve project this project directory DECLARES, and its master.

    Addressed by the exact listed name through
    `timeline_ingest.resolve_project_exactly`, which refuses rather than
    opening one - opening a project is a write to the captain's session,
    and a near match lands on somebody else's work (AGENTS.md 5).

    Returns `(project, master_timeline_name)`.
    """
    import yaml

    from library.tools.marker_feedback import connect_resolve
    from library.tools.timeline_ingest import resolve_project_exactly

    config_path = os.path.join(project_folder, "project.yaml")
    with open(config_path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    resolve_config = config.get("resolve") or {}
    name = resolve_config.get("project_name")
    master = resolve_config.get("timeline_name")
    if not name or not master:
        raise OrganizationError(
            f"{config_path} must declare resolve.project_name and "
            f"resolve.timeline_name. Without the timeline name the master "
            f"cannot be told apart from a reel, and it is the one "
            f"timeline that is never moved (AGENTS.md 5).")

    resolve = connect_resolve()
    project = resolve_project_exactly(
        resolve.GetProjectManager(), name)
    return project, master
