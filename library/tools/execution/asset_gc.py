"""Collect Ren's orphaned renders into trash, and the dry-run hook.

The Resolve half of `library/tools/asset_gc.py`, which holds every rule
and no API calls. The order is the safety argument, inherited from
`library/tools/execution/prune_orphans.py`:

1. **Plan**, and write the manifest. Nothing is touched.
2. **Remove the pool ITEMS**, and stop. A pool item is a reference; the
   files are all still on disk, so a project that comes back wrong at
   this point can have its items re-imported from files that never left.
3. **Verify** - every timeline still digests the same.
4. Only then **move the FILES to trash**, re-proving per file, at the
   moment of the move, that no timeline plays it.

Step 3 is between 2 and 4 because that is the last moment the mistake
is recoverable. The trash folder plus its manifest is the way back; a
`resolve-prune --delete-files` unlink has none.

`report_after` is the hook `still-overlays`, `touch-reel` and
`build-reels` call when they finish: a dry-run collection report so
leftovers never accumulate silently. It only READS - the pool walk
uses `GetTimelineByIndex` item reads that never move the cursor, and
the project lookup refuses rather than opening anything - so it takes
no lease and restores nothing. It never raises: a hook that fails the
command it follows would turn every build into a GC verdict.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from library.tools import journal_naming
from library.tools.resolve_lock import under_lease

JOURNAL_PREFIX = "asset_gc"


def journal_path_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS collection's journal goes. One file per run, never reused."""
    return journal_naming.unique_path(
        os.path.join(project_folder, "pipeline_output", "review"),
        JOURNAL_PREFIX, when)


def manifest_path_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS collection's manifest goes, beside its journal."""
    return journal_naming.unique_path(
        os.path.join(project_folder, "pipeline_output", "review"),
        JOURNAL_PREFIX, when, suffix="_manifest.md")


def survey_holdout(project, project_folder: str) -> dict:
    """Plan the collection off ONE read of the pool, holding out disputes.

    Same two instruments as `prune_orphans.survey`, but a disagreement
    holds out only the disputed paths instead of refusing the whole
    run (captain 2026-10-07): anything EITHER instrument places counts
    as used and is kept. An unreadable database still refuses outright
    - a sweep that cannot find what it knows is there has not looked.
    """
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.execution.prune_orphans import (
        placed_paths_from_database,
        project_database_path,
    )
    from library.tools import asset_gc as _gc
    from library.tools.resolve_organization import is_generated

    artefacts, _duplicates, _recorded, _root = read_pool(project)
    live_placed = {a.file_path for a in artefacts
                   if a.placed_by and a.file_path
                   and is_generated(a.file_path, project_folder)}
    db_path = project_database_path(project, project_folder)
    probe = min(live_placed) if live_placed else None
    saved_placed = placed_paths_from_database(db_path, must_find=probe)
    saved_generated = {p for p in saved_placed
                       if is_generated(p, project_folder)}

    sizes = {}
    for artefact in artefacts:
        if artefact.file_path and artefact.file_path not in sizes:
            try:
                sizes[artefact.file_path] = os.path.getsize(artefact.file_path)
            except OSError:
                continue
    plan, disputed, held = _gc.plan_gc_holdout(
        artefacts, project_folder, live_placed, saved_generated, sizes)
    return {
        "artefacts": artefacts,
        "plan": plan,
        "disputed": disputed,
        "held": held,
        "referenced": live_placed | saved_placed,
        "database_path": db_path,
    }


def collect_now(project, survey_result: dict, project_folder: str,
                journal_path: str, trash_dir: str) -> dict:
    """Remove the pool items, verify the timelines, trash the files.

    Pool-item removal touches NO file on disk; the trash move re-proves
    each file unreferenced at move time against the union of both
    instruments, recomputed after the removal. Returns the journal
    plus the trash record.
    """
    from library.tools.execution.prune_orphans import (
        current_referenced_paths,
        remove_pool_items,
        save_project,
        timeline_digests,
    )
    from library.tools import asset_gc as _gc
    from library.tools.orphan_removal import files_to_delete, summarise

    # Saved BEFORE the "before" snapshot, so the comparison isolates
    # THIS collection: anything already pending in the open project would
    # otherwise land in the "after" save and read as damage done here.
    # SaveProject is on the project MANAGER; `project.SaveProject` is
    # None, and calling it raises TypeError rather than saving.
    save_project()
    before = timeline_digests(survey_result["database_path"])
    journal = remove_pool_items(project, survey_result, journal_path)
    save_project()
    after = timeline_digests(survey_result["database_path"])
    moved = sorted(name for name in set(before) | set(after)
                   if before.get(name) != after.get(name))
    if moved:
        raise _gc.GcRefused(
            f"{len(moved)} timeline(s) digest differently after the pool "
            f"removal ({', '.join(moved[:5])}). The files are all still "
            f"on disk - re-import from the journal before going further. "
            f"Nothing was moved to trash.")
    referenced = current_referenced_paths(project, project_folder)
    _gc.assert_gc_safe(survey_result["plan"], referenced, project_folder)
    trash = _gc.move_to_trash(files_to_delete(survey_result["plan"]),
                              referenced, trash_dir,
                              owned_roots=[project_folder])
    journal["trash"] = {"trash_dir": trash_dir,
                        "moved": trash["moved"],
                        "already_gone": trash["already_gone"],
                        "bytes_trashed": trash["bytes_trashed"]}
    Path(journal_path).write_text(
        json.dumps(journal, indent=2), encoding="utf-8")
    counts = summarise(survey_result["plan"])
    counts["bytes_trashed"] = trash["bytes_trashed"]
    return {"journal": journal, "trash": trash, "counts": counts}


def report_after(project_folder: str) -> None:
    """Print the GC dry-run report. Never raises, never writes.

    The broker answers first: where no broker serves, no live Resolve
    is reachable from here, and probing further would only attempt a
    binding import that cannot succeed. That keeps stubbed callers
    (tests driving the command without Resolve) free of connection
    attempts - the hook reports the skip instead.
    """
    try:
        from library.tools.resolved import client as _broker

        try:
            reachable = bool(_broker.serving() or _broker.in_broker())
        except Exception:  # noqa: BLE001 - unanswered means unreachable
            reachable = False
        if not reachable:
            print("  gc dry-run skipped: no broker serving")
            return
        from library.tools.execution.organise_media_pool import open_project
        from library.tools import asset_gc as _gc
        from library.tools.orphan_removal import summarise

        project, _master = open_project(project_folder)
        result = survey_holdout(project, project_folder)
        counts = summarise(result["plan"])
        print(_gc.render_gc_report(
            result["plan"], counts,
            _gc.trash_dir_for(project_folder,
                              datetime.now(timezone.utc).isoformat()),
            result["disputed"]))
    except Exception as exc:  # noqa: BLE001 - a hook never fails its caller
        print(f"  gc dry-run skipped: {exc}")
