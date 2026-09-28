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

**Reading metadata back out of `Project.db` needs THREE instruments to
be right at once, and every one of them has been wrong here.**
`SELECT ... WHERE CAST(col AS BLOB) LIKE '%needle%'` silently matches
nothing where `instr(col, x'...')` matches; the `.drp` export omits the
table entirely; and a sweep over `pragma table_info` with
`connection.text_factory = bytes` gets BYTES column names, builds
`b'Name'` into the SQL, raises on every column and - with the usual
`except sqlite3.Error: continue` - reports zero hits, which reads
exactly like a sweep that looked.  Search for something you KNOW is
there first: a reel's own name lands in `Sm2Timeline.Name`,
`Sm2MpMedia.Name` and `Sm2MpFolder.Name`, and an instrument that cannot
find those has not looked.

Measured 2026-09-07 on the field test, once the sweep worked: the bin
tree is plain rows - `Sm2MpFolder` (57) and `Sm2MpFolder_Sm2MpMedia`
(2,632) - and the STAMPS rest in `BtLockableBlob.FieldsBlob`,
**zstd-compressed** (frame magic `28 b5 2f fd` at offset 9, which is why
no plain-bytes search finds them).  Decompressed, that column carries
all 48 `vep:state=` tags in the exact split the live API reports
(20 current / 20 earlier / 8 unrecorded), the plan hash, and all 48
`Comments` sentences.  So `BtLockableBlob` is where they rest and not
only where they pass through.  The clip COLOUR is not in the file as
`Green`/`Brown`/`Blue` text and was not located; the close-and-reopen
above is still the instrument that answers for it.
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

from library.tools import journal_naming
from library.tools.resolve_lock import under_lease

from library.tools.resolve_organization import (
    Artefact,
    OrganizationError,
    Plan,
    plan_organization,
    state_from_keywords,
    unplaced_report,
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
    """Read the project and the plan records, and decide what belongs where.

    Current plan means the PLAN: the live proposals file's approved
    moments (`plan_provenance.current_plan_names`), never the reels the
    last build happened to place - so building one reel leaves every
    other planned reel exactly where it was, while a reel the live plan
    genuinely no longer names still demotes to Earlier plans."""
    from library.tools.plan_provenance import (
        archived_timeline_names, current_plan_names, read_provenance)

    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    provenance = read_provenance(review_dir) or {}
    artefacts, duplicates, recorded, root_name = read_pool(project)
    plan = plan_organization(
        artefacts=artefacts,
        project_root=project_folder,
        master_timeline_name=master_timeline_name,
        current_reels=current_plan_names(project_folder, provenance),
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


def import_into_bin(pool, dest: tuple[str, ...],
                    paths: list[str]) -> list:
    """Import files so they LAND in the declared bin, not in CURRENT.

    `CreateEmptyTimeline` and `ImportMedia` put what they make into
    whatever bin happens to be current - wherever the operator last
    clicked - so an import that does not choose its bin is a filing
    decision the lottery makes. This is the one import path builders
    use for generated overlays: the bins are ensured lookup-first
    (never forked), the current folder is set to the destination for
    exactly the import call, and restored afterwards even when the
    import raises. The return is judged by the caller (AGENTS.md 5):
    an empty answer means Resolve took nothing.

    `dest` is a bin path from the root, e.g. `("06 - Subtitle
    renders", "<reel>")` - built by the caller from
    `resolve_bin_layout.render_bin_for_file`, the same function the
    organiser's verdicts use, so import-time filing and the later
    organise pass cannot disagree about where a new item belongs.
    """
    root = pool.GetRootFolder()
    before_current = pool.GetCurrentFolder()
    try:
        folder = root
        for depth, part in enumerate(dest):
            folder = ensure_folder(pool, folder, part, None, dest[:depth])
        pool.SetCurrentFolder(folder)
        items = pool.ImportMedia(list(paths))
        return list(items) if items else []
    finally:
        if before_current is not None:
            pool.SetCurrentFolder(before_current)


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
    for.  So the name carries a UTC timestamp, and a second apply inside
    that second is suffixed past it rather than overwriting.  The stamp
    and the suffix are `library/tools/journal_naming.py`, which is the one
    place all six journal writers name a file, because four of them
    carried this docstring and none of the four had the suffix.
    """
    return journal_naming.unique_path(
        os.path.join(project_folder, "pipeline_output", "review"),
        JOURNAL_PREFIX, when)


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


def unplaced_cost(report: dict) -> dict:
    """`unplaced_report` plus what those items cost on disk, by `stat`.

    The pure half cannot answer this because it does no I/O, and the
    disk figure is what turns "1,216 items" into a decision the captain
    can actually take: on the field test 1,085 of the 1,216 files are
    still there and 131 are already gone, so the pool item is offline
    and the render it names cannot be recovered by keeping it.

    Sizes only.  This never asks whether a file LOOKS superseded, and it
    never proposes removing one - AGENTS.md 10.5: where a creative value
    is absent, report plainly.  A number is reported; nothing acts on it.
    """
    on_disk, missing, total, shared_bytes = 0, 0, 0, 0
    shared = set(report["shared_with_placed"])
    for path in report["paths"]:
        try:
            size = os.path.getsize(path)
        except OSError:
            missing += 1
            continue
        on_disk += 1
        total += size
        if path in shared:
            shared_bytes += size
    return dict(report, on_disk=on_disk, missing=missing,
                bytes_on_disk=total, bytes_shared=shared_bytes)


def render_unplaced(cost: dict) -> str:
    """The unplaced burden in the sentences an operator has to read.

    Said on every plan, every apply and every check, so that a bin that
    grows on each rebuild reports its own growth instead of quietly
    absorbing it.
    """
    if not cost["count"]:
        return ("  Nothing this pipeline generated is unplaced - every "
                "generated clip in the pool is on a timeline.")
    gib = cost["bytes_on_disk"] / (1024 ** 3)
    homes = " and ".join(f"{b!r}" for b in cost["bins"])
    lines = [
        f"  {cost['count']} clip(s) this pipeline generated are on NO "
        f"timeline, filed under {homes}.",
        f"    {cost['on_disk']} of their files are still on disk "
        f"({gib:.2f} GiB); {cost['missing']} are already gone, so those "
        f"pool items are offline.",
    ]
    if cost["shared_with_placed"]:
        shared_mib = cost["bytes_shared"] / (1024 ** 2)
        lines.append(
            f"    {len(cost['shared_with_placed'])} of those files a PLACED "
            f"item ALSO uses ({shared_mib:.1f} MiB) - removing the pool item "
            f"is safe there and deleting the FILE would take media off a "
            f"live timeline.")
    lines.append("    Nothing here is deleted. Whether any of it should be "
                 "is the captain's call.")
    return "\n".join(lines)


def survey_project(project, project_folder: str,
                   master_timeline_name: str) -> dict:
    """One read of the pool, and everything computed off that one read.

    `check_project` and the CLI both want the findings AND the unplaced
    burden, and reading the pool twice to get them would be two answers
    that can disagree about the same project.
    """
    from library.tools.execution import retire_empty_bins as retire
    from library.tools.resolve_organization import (
        findings,
        plan_dead_render_bins,
        plan_retirements,
        render_bin_census,
    )

    plan, artefacts, duplicates, recorded = plan_for_project(
        project, project_folder, master_timeline_name)
    tree = retire.read_bin_tree(project)
    retirements = plan_retirements(artefacts, list(tree),
                                   project_root=project_folder)
    _dead, declined = plan_dead_render_bins(
        artefacts, list(tree), project_folder)
    dead_paths = [e["path"] for e in retirements
                  if e.get("kind") == "dead_render_bin"]
    found = findings(artefacts, plan, duplicates, recorded,
                     dead_paths=dead_paths)
    for entry in retirements:
        if entry.get("kind") == "dead_render_bin":
            held = len(entry.get("contents") or [])
            found.append({
                "kind": "dead_render_bin",
                "name": "/".join(entry["path"]),
                "detail": f"per-reel bin {'/'.join(entry['path'])!r} "
                          f"names no live timeline - {entry['why']} "
                          f"({held} item(s) retire with it)",
            })
        else:
            found.append({
                "kind": "empty_legacy_bin",
                "name": "/".join(entry["path"]),
                "detail": f"legacy bin {'/'.join(entry['path'])!r} stands "
                          f"empty beside the numbered scheme - {entry['why']}",
            })
    return {
        "findings": found,
        "unplaced": unplaced_cost(unplaced_report(artefacts, project_folder)),
        "retirements": [dict(path=list(e["path"]), why=e["why"],
                             kind=e.get("kind", "legacy_shell"),
                             contents=list(e.get("contents") or []))
                        for e in retirements],
        "census": render_bin_census(artefacts, list(tree), retirements,
                                    declined=declined),
    }


def organise_project(project, project_folder: str,
                     master_timeline_name: str,
                     apply: bool = False,
                     journal_path: str | None = None,
                     retire_empty_shells: bool = True) -> dict:
    """Plan, and apply only when asked. Returns both halves of the record.

    Applying files the pool first and retires the emptied legacy shells
    second: the moves are what empty them, so the retirement is planned
    off a FRESH read taken after the moves land, never off the plan a
    moment ago. The retirement has its own journal and its own revert -
    `resolve-organize --revert` reads either.
    """
    from library.tools.execution import retire_empty_bins as retire
    from library.tools.resolve_organization import (
        plan_dead_render_bins,
        plan_retirements,
        render_bin_census,
        scratch_report,
    )

    plan, artefacts, duplicates, _recorded = plan_for_project(
        project, project_folder, master_timeline_name)
    tree = retire.read_bin_tree(project)
    retirements = plan_retirements(artefacts, list(tree),
                                   project_root=project_folder)
    _dead, declined = plan_dead_render_bins(
        artefacts, list(tree), project_folder)
    result = {"plan": plan.as_dict(), "duplicate_bins": duplicates,
              "applied": False,
              # Read from the same pass as the plan, so the filing and
              # the count of what is unplaced cannot disagree.
              "unplaced": unplaced_cost(
                  unplaced_report(artefacts, project_folder)),
              "retirements": [dict(path=list(e["path"]), why=e["why"],
                                   kind=e.get("kind", "legacy_shell"),
                                   contents=list(e.get("contents") or []))
                              for e in retirements],
              "declined": [dict(path=list(d["path"]), why=d["why"])
                           for d in declined],
              "held_for_dead_sweep": [],
              # Read from the same pass as the plan, so the report
              # cannot disagree with the filing about which scratches
              # are where. Said on every run: a scratch that outlives
              # its promotion is reported by name instead of waiting
              # to be discovered by the captain.
              "scratch": scratch_report(artefacts, project_folder),
              "census": render_bin_census(artefacts, list(tree),
                                          retirements, declined=declined)}
    if apply:
        # Dead bins retire WITH their contents, so the filing pass
        # holds those moves back: re-homing a dead reel's renders to
        # `Not placed on any timeline` first would just migrate the
        # accumulation instead of removing it.  What is held back is
        # reported, and the retirement below takes it with the bin -
        # pool items only, never files.
        dead_ids = {c["item_id"] for e in retirements
                    if e.get("kind") == "dead_render_bin"
                    for c in (e.get("contents") or [])}
        held = [v for v in plan.moves if v.item_id in dead_ids]
        if held:
            from dataclasses import replace as _replace
            plan = _replace(
                plan, moves=[v for v in plan.moves
                             if v.item_id not in dead_ids])
        result["held_for_dead_sweep"] = [
            {"name": v.name,
             "bin": "/".join(
                 next(a.folder_path for a in artefacts
                      if a.item_id == v.item_id))}
            for v in held]
        result["journal"] = apply_plan(
            project, plan, artefacts,
            journal_path or journal_path_for(project_folder))
        result["applied"] = True
        if retire_empty_shells:
            fresh, _, _, _ = read_pool(project)
            fresh_tree = retire.read_bin_tree(project)
            fresh_plan = plan_retirements(fresh, list(fresh_tree),
                                          project_root=project_folder)
            _fresh_dead, fresh_declined = plan_dead_render_bins(
                fresh, list(fresh_tree), project_folder)
            result["retirements"] = [
                dict(path=list(e["path"]), why=e["why"],
                     kind=e.get("kind", "legacy_shell"),
                     contents=list(e.get("contents") or []))
                for e in fresh_plan]
            result["declined"] = [
                dict(path=list(d["path"]), why=d["why"])
                for d in fresh_declined]
            result["census"] = render_bin_census(
                fresh, list(fresh_tree), fresh_plan,
                declined=fresh_declined)
            result["retirement"] = retire.retire_bins(
                project, fresh_plan,
                retire.journal_path_for(project_folder),
                project_root=project_folder)
    return result


def check_project(project, project_folder: str,
                  master_timeline_name: str) -> list[dict]:
    """What is wrong with the project as it stands, as findings.

    The findings half of `survey_project`.  An unplaced clip is NOT a
    finding: a render whose reel was rebuilt is filed exactly where its
    evidence puts it, and failing the check on it would fail correct
    output, which is the same defect as a gate that cannot fail read
    from the other side (AGENTS.md 10.4).  It is reported instead.
    """
    return survey_project(project, project_folder,
                          master_timeline_name)["findings"]


@under_lease("read Resolve project for media pool organization",
             exclusive=False)
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
