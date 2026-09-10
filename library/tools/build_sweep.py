"""The sweep every build runs, so clutter never becomes a chore again.

Three things accumulate across rebuilds, and before this module each had
a working mechanism that NOTHING CALLED:

1. **Superseded FILES.** `caption_asset_gc` is a complete reachability
   mark and a quarantining sweep - reached only through its own
   `argparse` CLI. No DAG node, no step and no build path invoked it, so
   it had never run.
2. **EMPTY BINS.** `resolve_organization.plan_retirements` DOES run on
   every build (through `organise_project`), but only ever reached the
   LEGACY tops. Per-reel bins live under the CURRENT scheme's tops, so
   a reel rebuilt under a new name left an empty bin no rule could
   collect. `is_spent_render_bin` is that rule.
3. **Superseded POOL ENTRIES.** `prune_orphans` removes items no
   timeline plays, with two instruments that must agree - reached only
   through `manage_project resolve-prune`.

So this module invents no policy. It runs the three existing mechanisms
in the one order that is safe, at the one moment the project is settled
(reel promotion, after the pool has been filed), and journals the lot.

**THE SAFETY RULE.** A file shared by several timelines is named by
several records, so the mark unions EVERY root - each timeline in the
database plus the pipeline's own records - before anything becomes a
candidate. `caption_asset_gc.sweep` then re-proves each candidate
against the CURRENT union and raises `SweepRefused` if any became
referenced, if any root is unreadable, or if the directory moved under
the mark. An unreadable root reads exactly like an empty project and
would condemn everything, so it REFUSES rather than sweeping.

**Nothing is unlinked.** Files move to `Area.QUARANTINE`, pool items are
removed but their files stay, and retired bins were empty. Every removal
is derivable by rebuilding, and the journal records the command that
regenerates it.

**Never swept, at any time:** source footage, timelines, `brand_assets/`
and `exports/`. Those are not in `SWEPT_AREAS`, which is the whole
enumeration of what this module may touch.

`tests/test_build_sweep.py`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from library.tools import caption_asset_gc as gc
from library.tools.project_layout import Area, ProjectLayout

SWEPT_AREAS = (
    Area.SUBTITLE_SEGMENTS,
    Area.MOTION_GRAPHICS_SEGMENTS,
    Area.TIMED_TEXT_SEGMENTS,
)
"""Every directory a build sweep may take a file out of. COMPLETE.

All three are `Kind.OUTPUT` written by a render step, so every file in
them is derivable by re-running that step - which is what makes moving
one recoverable in the sense that matters. Raw footage, the captain's
`brand_assets/` and the stills under `exports/` are absent by
construction, not by a check that could be forgotten."""

REGENERATE = {
    Area.SUBTITLE_SEGMENTS:
        "manage_project.py run <project> --only render_subtitles "
        "--rerun render_subtitles",
    Area.MOTION_GRAPHICS_SEGMENTS:
        "manage_project.py run <project> --only render_motion_graphics "
        "--rerun render_motion_graphics",
    Area.TIMED_TEXT_SEGMENTS:
        "manage_project.py run <project> --only render_motion_graphics "
        "--rerun render_motion_graphics",
}
"""The command that brings each area's files back. Written into the
journal beside what was moved, because "recoverable" has to mean an
operator can read HOW, not just that a copy exists somewhere."""


class SweepRefused(Exception):
    """The sweep declined to remove something, and removed NOTHING
    further. The journal holds whatever had already landed."""


def journal_path_for(project_folder: str, when: str | None = None) -> str:
    """Where this sweep's journal goes. One file per sweep, never reused.

    Suffixed past a collision: consecutive sweeps share a
    second-granularity stamp, and the second journal must not
    overwrite the first - measured 2026-09-10, when this lane's
    verify-twice run landed its empty second pass on top of the
    first pass's 102-file record.  Same disambiguator
    `library/tools/execution/remove_proof.py` uses.
    """
    stamp = when or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    layout = ProjectLayout(project_folder)
    candidate = str(layout.write_path(
        Area.QUARANTINE, f"build_sweep_{stamp}.json"))
    sibling = 2
    while os.path.exists(candidate):
        candidate = str(layout.write_path(
            Area.QUARANTINE, f"build_sweep_{stamp}_{sibling}.json"))
        sibling += 1
    return candidate


def _area_dirs(project_folder: str) -> list[tuple[Area, str]]:
    """The swept areas that exist on disk, in sweep order."""
    layout = ProjectLayout(project_folder)
    out = []
    for area in SWEPT_AREAS:
        path = str(layout.read_dir(area))
        if os.path.isdir(path):
            out.append((area, path))
    return out


def collect_roots(project_folder: str, db_paths: list[str]) -> list:
    """Every reference root, unioned, with the ledger narrowed ONCE.

    The order is the point: the Resolve roots are read first because
    they are what says which timelines exist, and that answer is what
    `collect_pipeline_roots` needs to tell a live ledger entry from a
    record of a build whose timeline is long gone. Derived in one place
    (`live_timelines_from`) so the mark and the sweep cannot disagree.
    """
    roots = gc.collect_resolve_roots(list(db_paths))
    roots += gc.collect_pipeline_roots(
        project_folder, live_timelines=gc.live_timelines_from(roots))
    return roots


def sweep_files(project_folder: str, db_paths: list[str],
                apply: bool = False) -> dict:
    """Mark every swept area against one union of roots, then quarantine.

    Returns one record per area. With `apply` false nothing moves and
    the marks are still written - the mark is read-only by construction,
    so a plan costs the same read and answers the same question.

    A `SweepRefused` from any area propagates: the sweep is meant to be
    loud. Areas already swept in this pass keep what they moved, and the
    journal says which those were.
    """
    roots = collect_roots(project_folder, db_paths)
    unreadable = [r for r in roots if r.status == gc.UNREADABLE]
    if unreadable:
        raise SweepRefused(
            "refusing to sweep: root(s) "
            + ", ".join(f"{r.name!r} ({r.detail})" for r in unreadable)
            + ". An unreadable root reads exactly like a project with "
              "nothing on any timeline and would condemn every file in "
              "every swept area. Nothing was moved.")
    per_area = []
    for area, asset_dir in _area_dirs(project_folder):
        result = gc.mark(project_folder, asset_dir, roots)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        layout = ProjectLayout(project_folder)
        # Suffixed past a collision, like the journal above: a
        # verify-twice run must not land its second mark on the first.
        mark_path = str(layout.write_path(
            Area.QUARANTINE, f"mark_{area.value}_{stamp}.json"))
        sibling = 2
        while os.path.exists(mark_path):
            mark_path = str(layout.write_path(
                Area.QUARANTINE,
                f"mark_{area.value}_{stamp}_{sibling}.json"))
            sibling += 1
        result.write_json(mark_path)
        entry = {
            "area": area.value,
            "asset_dir": asset_dir,
            "mark_path": mark_path,
            "live": len(result.live),
            "orphans": len(result.orphans),
            "bytes_orphaned": result.bytes_orphaned,
            "regenerate": REGENERATE[area],
            "moved": [],
            "moved_count": 0,
        }
        if apply and result.orphans:
            try:
                record = gc.sweep(mark_path, project_folder=project_folder,
                                  fresh_roots=roots,
                                  manifest_tag=area.value)
            except gc.SweepRefused as refused:
                raise SweepRefused(
                    f"{area.value}: {refused}") from refused
            entry.update(moved=record["moved"],
                         moved_count=record["moved_count"],
                         bytes_reclaimed=record["bytes_reclaimed"],
                         quarantine_dir=record["quarantine_dir"],
                         manifest_path=record["manifest_path"])
        per_area.append(entry)
    return {"roots": [{"name": r.name, "status": r.status,
                       "detail": r.detail} for r in roots],
            "areas": per_area}


def sweep_pool(project, project_folder: str, apply: bool = False) -> dict:
    """Remove pool items no timeline plays. Touches NO file.

    `prune_orphans` owns every rule here and refuses on its own terms:
    a set carrying a timeline's pool item refuses (`DeleteClips` on one
    DELETES THE TIMELINE), a set carrying a placed item refuses, and the
    live project and the saved database must agree about what is placed
    before anything goes.

    `keep_shared` is the content-keyed case PR 905 introduced: one file
    legitimately serves several timelines, so a second pool item for it
    is removable while the FILE must stay. Nothing here deletes a file,
    so that distinction costs nothing - it is reported, not acted on.
    """
    from library.tools.execution import prune_orphans

    survey = prune_orphans.survey(project, project_folder)
    counts = dict(survey["counts"])
    record = {"counts": counts, "removed": 0, "journal_path": ""}
    if not apply or not survey["plan"]:
        return record
    journal = prune_orphans.journal_path_for(project_folder)
    removed = prune_orphans.remove_pool_items(project, survey, journal)
    record["removed"] = len(removed.get("removed", []))
    record["journal_path"] = removed.get("journal_path", journal)
    return record


def sweep_bins(project, project_folder: str, apply: bool = False) -> dict:
    """Retire the bins that are now empty, off a FRESH read.

    Fresh because the pool removal above is what empties them: a
    retirement planned before it would name bins that were still
    occupied, and one planned off the plan rather than the result is a
    claim about a moment that has passed.
    """
    from library.tools.execution import retire_empty_bins as retire
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements

    artefacts, _, _, _ = read_pool(project)
    tree = retire.read_bin_tree(project)
    planned = plan_retirements(artefacts, list(tree))
    record = {"planned": [{"path": "/".join(e["path"]), "why": e["why"]}
                          for e in planned],
              "retired": [], "journal_path": ""}
    if not apply or not planned:
        return record
    journal = retire.journal_path_for(project_folder)
    done = retire.retire_bins(project, planned, journal)
    record["retired"] = done["retired"]
    record["journal_path"] = done["journal_path"]
    return record


def sweep_build(project, project_folder: str,
                apply: bool = False,
                db_paths: list[str] | None = None) -> dict:
    """The whole sweep, in the one order that is safe.

    Pool items first, because removing them is what empties the bins.
    Files second, because a file is a candidate only once no timeline
    and no current record names it, and the pool removal does not change
    either. Bins last, off a read taken after both.

    The project is SAVED first: the two-instrument agreement
    `prune_orphans` insists on compares the live project against the
    saved database, and an unsaved change makes them disagree - which is
    a refusal, correctly, but a needless one at the end of a build that
    just wrote the timelines.
    """
    from library.tools.execution import prune_orphans

    started = datetime.now(timezone.utc).isoformat()
    if apply:
        prune_orphans.save_project()
    if db_paths is None:
        db_paths = [prune_orphans.project_database_path(
            project, project_folder)]

    record = {
        "swept_at": started,
        "applied": bool(apply),
        "resolve_project": project.GetName() if project else "",
        "project_folder": project_folder,
        "refused": [],
    }
    try:
        record["pool"] = sweep_pool(project, project_folder, apply=apply)
        record["files"] = sweep_files(project_folder, db_paths, apply=apply)
        record["bins"] = sweep_bins(project, project_folder, apply=apply)
    except Exception as refused:  # every refusal is reported, then re-raised
        record["refused"].append(f"{type(refused).__name__}: {refused}")
        record["journal_path"] = _write_journal(project_folder, record)
        raise
    record["journal_path"] = _write_journal(project_folder, record)
    return record


def _write_journal(project_folder: str, record: dict) -> str:
    path = journal_path_for(project_folder)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(record, indent=2, default=str),
                          encoding="utf-8")
    return path


def render_sweep(record: dict) -> str:
    """What the sweep did, in the sentences an operator has to read.

    Printed on every build. A sweep that removed nothing SAYS SO, and a
    sweep that declined to remove something says what and why - the
    refusal is the part worth reading.
    """
    lines = []
    verb = "Swept" if record.get("applied") else "Would sweep"
    pool = record.get("pool") or {}
    counts = pool.get("counts") or {}
    lines.append(
        f"  {verb} {pool.get('removed', 0)} media-pool item(s) no timeline "
        f"plays ({counts.get('keep_shared', 0)} of them name a file a "
        f"PLACED item also uses - the item goes, the file stays).")
    for area in (record.get("files") or {}).get("areas", []):
        gib = area.get("bytes_orphaned", 0) / (1024 ** 3)
        lines.append(
            f"  {area['area']}: {area['live']} live, {area['orphans']} "
            f"orphaned ({gib:.2f} GiB); moved {area['moved_count']} to "
            f"quarantine.")
        if area["moved_count"]:
            lines.append(f"    regenerate with: {area['regenerate']}")
    bins = record.get("bins") or {}
    if bins.get("retired"):
        lines.append(f"  Retired {len(bins['retired'])} empty bin(s): "
                     f"{', '.join(bins['retired'])}")
    else:
        lines.append(f"  No empty bin to retire "
                     f"({len(bins.get('planned', []))} planned).")
    for refusal in record.get("refused", []):
        lines.append(f"  REFUSED: {refusal}")
    if record.get("journal_path"):
        lines.append(f"  Sweep journal: {record['journal_path']}")
    return "\n".join(lines)
