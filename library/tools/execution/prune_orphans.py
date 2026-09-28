"""Removing the pool items nothing plays, and deleting their files.

The Resolve half of `library/tools/orphan_removal.py`, which holds every
rule and no API calls.

The order is the whole safety argument
--------------------------------------
`DeleteClips` leaves the file on disk, so the two halves are genuinely
separable and this module separates them:

1. **Plan**, and write the manifest.  Nothing is touched.
2. **Remove the pool ITEMS**, and stop.  A pool item is a reference; the
   renders are all still on disk, so a project that comes back wrong at
   this point can have its items re-imported from files that never left.
3. **Verify** - the project still opens, all 49 timelines still hash the
   same, the master is still there.
4. Only then **delete the FILES**, re-proving per file, at the moment of
   the unlink, that no timeline plays it.

Step 3 is between 2 and 4 because that is the last moment the mistake is
recoverable.  Doing it the other way round - files first - would make
the verification a post-mortem.

Reading `Project.db`
--------------------
**Copy it before opening it; never open it in place** (AGENTS.md 5).
The copy is opened read-only through a `file:...?mode=ro` URI, so a bug
here cannot write to the captain's project even by accident.

`tests/test_orphan_removal.py`.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from library.tools import journal_naming
from library.tools.resolve_lock import under_lease

from library.tools.orphan_removal import (
    DELETE,
    RemovalRefused,
    assert_file_unreferenced,
    assert_instruments_agree,
    assert_removable,
    plan_removal,
    render_manifest,
    summarise,
)
from library.tools.resolve_organization import is_generated

JOURNAL_PREFIX = "resolve_prune"

# How the disk database is laid out under the Resolve Projects tree.
# `dblist.conf` names the active database; the project sits at
# `<db>/Resolve Projects/Users/<user>/Projects/<folder...>/<name>/Project.db`.
PROJECT_DB_NAME = "Project.db"


def placed_paths_from_database(db_path: str,
                               must_find: str | None = None) -> set[str]:
    """Every file path a timeline ITEM points at, from a COPY of the db.

    The second instrument.  `Sm2TiItem` is the timeline-item table and
    `MediaFilePath` is the file behind each one, so its distinct
    non-empty values are exactly "what some timeline plays" as the last
    save recorded it.

    `must_find` is a path the caller KNOWS is placed, and the sweep
    refuses if it cannot find it.  A query that raises - or a schema
    that moved - returns an empty set that reads exactly like a project
    with nothing on any timeline, which would mark every file deletable.
    A sweep that cannot find what it knows is there has not looked.
    """
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, PROJECT_DB_NAME)
        shutil.copy2(db_path, copy)
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "select distinct MediaFilePath from Sm2TiItem "
                "where MediaFilePath is not null and MediaFilePath <> ''"
            ).fetchall()
        except sqlite3.Error as exc:
            raise RemovalRefused(
                f"reading Sm2TiItem.MediaFilePath from {db_path} raised "
                f"{exc}. An unreadable database returns nothing, which "
                f"reads exactly like a project with an empty timeline and "
                f"would mark every file deletable. Nothing was removed."
            ) from exc
        finally:
            con.close()
    paths = {row[0] for row in rows}
    if must_find is not None and must_find not in paths:
        raise RemovalRefused(
            f"the saved database at {db_path} does not report "
            f"{must_find!r} as placed, and the live project does. Either "
            f"the project has changed since it was saved, or this sweep "
            f"is not reading what it thinks it is. Nothing was removed.")
    return paths


def timeline_names_from_database(db_path: str) -> set[str]:
    """Every timeline the saved project holds, from a COPY of the db.

    `Sm2Timeline.Name` is the timeline's own name - the same string
    `GetName()` returns and the same one build records bind to. Read
    through a copy, like every other reader here, so the captain's live
    file is never opened in place.

    A query that raises RAISES, for the reason `placed_paths_from_database`
    states: an empty answer reads exactly like a project with no
    timelines, and a caller that used it to decide what is superseded
    would call every recorded binding dead.
    """
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, PROJECT_DB_NAME)
        shutil.copy2(db_path, copy)
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = con.execute(
                'select "Name" from "Sm2Timeline" where "Name" is not null'
            ).fetchall()
        except sqlite3.Error as exc:
            raise RemovalRefused(
                f"reading Sm2Timeline.Name from {db_path} raised {exc}. "
                f"An unreadable database returns nothing, which reads "
                f"exactly like a project with no timelines at all and "
                f"would call every recorded binding dead. Nothing was "
                f"removed.") from exc
        finally:
            con.close()
    return {row[0] for row in rows if row[0]}


def timeline_digests(db_path: str) -> dict[str, str]:
    """A digest of every timeline's EDIT, from a COPY of the database.

    What an editor means by "the timeline is unchanged": its tracks, and
    every item on them with its name, its position, its duration, its
    source in-point and the file behind it.  Markers are deliberately
    NOT in it - they are annotation, not edit - and
    `library/tools/master_markers.py` says what that costs.

    Validated on the field test before it was trusted, both ways: 49
    timelines produce 49 DISTINCT digests and none is empty; a single
    frame nudged on a single item of the master moves exactly that one
    line and no other; and a byte-identical copy produces an identical
    listing.  A digest that hashed nothing would agree with itself
    forever, which is the failure this paragraph exists to rule out.
    """
    import hashlib

    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, PROJECT_DB_NAME)
        shutil.copy2(db_path, copy)
        con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            names = {r[0]: r[1] for r in con.execute(
                "select Sm2Timeline_id, Name from Sm2Timeline")}
            sequence_of = {r[0]: r[1] for r in con.execute(
                "select Sequence, Sm2Timeline_id from Sm2Timeline")}
            container_of = {r[0]: r[1] for r in con.execute(
                "select Sm2SequenceContainer_id, Sm2Sequence_id "
                "from Sm2SequenceContainer")}
            tracks = con.execute(
                "select Sm2TiTrack_id, Type, SubType, Flags, "
                "UserDefinedName, Sm2SequenceContainer_id, Sm2Sequence_id "
                "from Sm2TiTrack").fetchall()
            items: dict[str, list] = {}
            for row in con.execute(
                    'select link.DbOwner, item.Name, item.Start, '
                    'item.Duration, item."In", item.MediaFilePath, '
                    'item.MediaStartTime '
                    'from Sm2TiItem_Sm2TiTrack link '
                    'join Sm2TiItem item '
                    '  on item.Sm2TiItem_id = link.DbAssociate'):
                items.setdefault(row[0], []).append(
                    tuple(str(value) for value in row[1:]))
        finally:
            con.close()

    per_timeline: dict[str, list] = {}
    for track_id, kind, sub, flags, name, container, sequence in tracks:
        seq = sequence or container_of.get(container)
        timeline = sequence_of.get(seq)
        if timeline is None:
            continue
        per_timeline.setdefault(timeline, []).append(
            (kind, sub, flags, name, tuple(sorted(items.get(track_id, [])))))

    digests = {name: "EMPTY" for name in names.values()}
    for timeline, track_rows in per_timeline.items():
        blob = repr(sorted(track_rows, key=repr)).encode("utf-8")
        digests[names[timeline]] = hashlib.sha256(blob).hexdigest()
    return digests


@under_lease("read Resolve database path for orphan pruning",
             exclusive=False)
def project_database_path(project, project_folder: str) -> str:
    """Where the open project's `Project.db` is.

    The database ROOT is a machine setting, read off Resolve itself; the
    FOLDER inside it is the captain's own tree, and the project
    DECLARES it as `resolve.folder` in its `project.yaml` (AGENTS.md
    10.1: a project's own declarations reach the run).

    Measured: `GetCurrentFolder()` answers with the LEAF folder name
    only - `'Podcast'`, not `'Lucie Content/Social Media/Podcast'` - so
    it cannot build this path and the declaration is what does.  A
    guessed path that happens to exist would be another project's
    database, which is the same near-match failure AGENTS.md 5 refuses
    for project names.
    """
    import yaml

    from library.tools.marker_feedback import connect_resolve

    database = connect_resolve().GetProjectManager().GetCurrentDatabase() or {}
    if (database.get("DbType") or "") != "Disk":
        raise RemovalRefused(
            f"the current Resolve database is {database!r}, not a Disk "
            f"database. There is no Project.db to read, so the second "
            f"instrument cannot run. Nothing was removed.")
    root = database.get("DbPath") or _disk_database_root(
        database.get("DbName") or "")
    config_path = os.path.join(project_folder, "project.yaml")
    with open(config_path, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    folder = ((config.get("resolve") or {}).get("folder") or "")
    path = os.path.join(root, "Resolve Projects", "Users", "guest",
                        "Projects", *[p for p in folder.split("/") if p],
                        project.GetName(), PROJECT_DB_NAME)
    if not os.path.exists(path):
        raise RemovalRefused(
            f"expected the project database at {path} and there is no file "
            f"there. {config_path} declares resolve.folder {folder!r}; if "
            f"the project has moved in Resolve, that declaration is stale. "
            f"Nothing was removed.")
    return path


def _disk_database_root(db_name: str) -> str:
    """The named disk database's root, from `dblist.conf`.

    The file is colon-delimited text, NOT JSON - one database per line,
    `<name>:<path>:*:::DISK`, e.g.
    `poetic:/Users/<you>/Pictures/Davinci:*:::DISK`.  Matched on the
    name `GetCurrentDatabase()` reports, because a machine may have
    several and the first line is not necessarily the open one.
    """
    conf = Path.home() / ("Library/Preferences/Blackmagic Design/"
                          "DaVinci Resolve/dblist.conf")
    try:
        text = conf.read_text(encoding="utf-8")
    except OSError as exc:
        raise RemovalRefused(
            f"cannot read the Resolve database list at {conf}: {exc}. "
            f"Nothing was removed.") from exc
    listed = []
    for line in text.splitlines():
        fields = line.strip().split(":")
        if len(fields) < 2 or not fields[1]:
            continue
        listed.append((fields[0], fields[1]))
        if fields[0] == db_name:
            return fields[1]
    raise RemovalRefused(
        f"{conf} does not name a disk database called {db_name!r}. It "
        f"lists {listed!r}. Nothing was removed.")


@under_lease("save Resolve project before orphan pruning")
def save_project() -> bool:
    """Flush the open project to its `Project.db`.

    On the project MANAGER, not the project: `getattr(project,
    "SaveProject", None)` is None and calling it raises `TypeError`
    rather than saving - judged by what the call returned, never by
    `hasattr`, which is True for every name including invented ones
    (AGENTS.md 5).
    """
    from library.tools.marker_feedback import connect_resolve

    saved = connect_resolve().GetProjectManager().SaveProject()
    if not saved:
        raise RemovalRefused(
            f"SaveProject() returned {saved!r}. The database on disk is "
            f"not what the open project holds, so a digest read from it "
            f"would not describe what is there.")
    return saved


def survey(project, project_folder: str) -> dict:
    """Plan the removal off ONE read of the pool, with both instruments.

    Returns the plan, the counts, and the two placed-path sets - so the
    manifest, the removal and the verification all come from the same
    reading and cannot disagree about the project.
    """
    from library.tools.execution.organise_media_pool import read_pool

    artefacts, _duplicates, _recorded, _root = read_pool(project)
    live_placed = {a.file_path for a in artefacts
                   if a.placed_by and a.file_path
                   and is_generated(a.file_path, project_folder)}
    db_path = project_database_path(project, project_folder)
    # Validated against a path the LIVE project says is placed, so an
    # empty or moved table refuses instead of reading as "nothing is
    # placed" - which would mark every file deletable.
    probe = min(live_placed) if live_placed else None
    saved_placed = placed_paths_from_database(db_path, must_find=probe)
    saved_generated = {p for p in saved_placed
                       if is_generated(p, project_folder)}
    assert_instruments_agree(live_placed, saved_generated)

    sizes = {}
    for artefact in artefacts:
        if artefact.file_path and artefact.file_path not in sizes:
            try:
                sizes[artefact.file_path] = os.path.getsize(artefact.file_path)
            except OSError:
                continue
    plan = plan_removal(artefacts, project_folder, saved_placed, sizes)
    return {
        "artefacts": artefacts,
        "plan": plan,
        "counts": summarise(plan),
        "referenced": live_placed | saved_placed,
        "database_path": db_path,
    }


def write_manifest(survey_result: dict, project, project_folder: str,
                   manifest_path: str) -> str:
    """The full record, written BEFORE anything is removed."""
    text = render_manifest(
        survey_result["plan"],
        resolve_project=project.GetName(),
        project_folder=project_folder,
        planned_at=datetime.now(timezone.utc).isoformat(),
        counts=survey_result["counts"])
    Path(manifest_path).parent.mkdir(parents=True, exist_ok=True)
    Path(manifest_path).write_text(text, encoding="utf-8")
    return manifest_path


def remove_pool_items(project, survey_result: dict, journal_path: str,
                      batch: int = 100) -> dict:
    """Remove the pool items. Touches NO file on disk.

    `assert_removable` runs on the whole set first: `DeleteClips` on a
    timeline's pool item deletes the timeline, so one wrong entry in a
    list of 1,216 is a reel destroyed with nothing to undo.

    Batched because `DeleteClips` takes a list and a single list of
    1,216 proxies is one call whose failure says nothing about which of
    them it got to.  The journal records every item removed, with the
    bin it was in and the file behind it, so the manifest and the
    journal are two independent statements of what went.
    """
    by_id = {a.item_id: a for a in survey_result["artefacts"]}
    wanted = [by_id[r.item_id] for r in survey_result["plan"]]
    assert_removable(wanted)

    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    before_current = pool.GetCurrentFolder()

    clip_by_id = {}

    def index(folder) -> None:
        for clip in (folder.GetClipList() or []):
            clip_by_id[clip.GetUniqueId()] = clip
        for sub in (folder.GetSubFolderList() or []):
            index(sub)

    index(root)

    journal = {
        "pruned_at": datetime.now(timezone.utc).isoformat(),
        "resolve_project": project.GetName(),
        "removed": [],
        "not_in_pool": [],
    }
    targets = []
    for removal in survey_result["plan"]:
        clip = clip_by_id.get(removal.item_id)
        if clip is None:
            journal["not_in_pool"].append(removal.name)
            continue
        # Re-read the KIND off the live proxy rather than trusting the
        # plan: the plan is a claim about a moment and this is the call
        # that cannot be undone.
        if (clip.GetClipProperty("Type") or "") == "Timeline":
            raise RemovalRefused(
                f"{removal.name!r} reports Type 'Timeline' in the pool and "
                f"the plan calls it a clip. DeleteClips would delete the "
                f"timeline. Nothing was removed.")
        targets.append((removal, clip))

    try:
        for start in range(0, len(targets), batch):
            chunk = targets[start:start + batch]
            if not pool.DeleteClips([clip for _, clip in chunk]):
                raise RemovalRefused(
                    f"DeleteClips returned False for {len(chunk)} item(s) "
                    f"after {len(journal['removed'])} had been removed. "
                    f"The journal names those; every file is still on disk.")
            for removal, _clip in chunk:
                journal["removed"].append({
                    "item_id": removal.item_id,
                    "name": removal.name,
                    "file_path": removal.file_path,
                    "from": "/".join(removal.folder_path),
                    "file_state": removal.file_state,
                })
    finally:
        if before_current:
            pool.SetCurrentFolder(before_current)
        Path(journal_path).parent.mkdir(parents=True, exist_ok=True)
        Path(journal_path).write_text(
            json.dumps(journal, indent=2), encoding="utf-8")
    journal["journal_path"] = journal_path
    return journal


def files_from_journal(journal_path: str) -> list[str]:
    """The paths a completed item-removal said it would delete.

    The file list CANNOT come from a fresh survey once the items are
    gone: the survey derives orphans from pool items placed on no
    timeline, and after `remove_pool_items` those items do not exist, so
    a re-survey answers zero and would delete nothing while reporting
    success.  The journal is the record of what was removed, so the
    journal is what names the files.
    """
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    removed = journal.get("removed") or []
    if not removed:
        raise RemovalRefused(
            f"{journal_path} records no removed pool item, so there is no "
            f"file it authorises deleting. Nothing was deleted.")
    return sorted({entry["file_path"] for entry in removed
                   if entry.get("file_state") == DELETE
                   and entry.get("file_path")})


def current_referenced_paths(project, project_folder: str) -> set[str]:
    """What some timeline plays RIGHT NOW, by both instruments.

    Recomputed at deletion time rather than carried over from the plan:
    the plan was a claim about an earlier moment and this is the call
    that cannot be undone.
    """
    from library.tools.execution.organise_media_pool import read_pool

    artefacts, _dup, _rec, _root = read_pool(project)
    live = {a.file_path for a in artefacts if a.placed_by and a.file_path}
    db_path = project_database_path(project, project_folder)
    probe = min(live) if live else None
    return live | placed_paths_from_database(db_path, must_find=probe)


def delete_files(paths: list[str], referenced: set[str],
                 journal_path: str) -> dict:
    """Unlink the files, re-proving per file that nothing plays them.

    The manifest was a claim about the moment it was written; this is a
    later moment, so the proof is repeated here, for every single path,
    against the union of both instruments.  A file that has become
    referenced since stops the whole run rather than being skipped
    quietly.
    """
    deleted, freed, absent = [], 0, []
    for path in paths:
        assert_file_unreferenced(path, referenced)
        try:
            size = os.path.getsize(path)
        except OSError:
            absent.append(path)
            continue
        os.remove(path)
        deleted.append(path)
        freed += size
    record = {
        "deleted_at": datetime.now(timezone.utc).isoformat(),
        "deleted": deleted,
        "vanished_before_deletion": absent,
        "bytes_freed": freed,
    }
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    journal["files"] = record
    Path(journal_path).write_text(
        json.dumps(journal, indent=2), encoding="utf-8")
    return record


def journal_path_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS prune's journal goes. One file per prune, never reused.

    The same bargain `organise_media_pool.journal_path_for` strikes: a
    fixed filename lets the second run overwrite the first one's record,
    and for an irreversible act that record is all there is.  Named
    through `library/tools/journal_naming.py`, so the suffix past a
    same-second collision is the same one every journal writer gets.
    """
    return journal_naming.unique_path(
        os.path.join(project_folder, "pipeline_output", "review"),
        JOURNAL_PREFIX, when)


def manifest_path_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS prune's manifest goes, beside its journal."""
    return journal_naming.unique_path(
        os.path.join(project_folder, "pipeline_output", "review"),
        JOURNAL_PREFIX, when, suffix="_manifest.md")
