"""What may be removed from a media pool, and what may never be.

The pure half of `library/tools/execution/prune_orphans.py`, holding
every rule and no API calls - the same split
`library/tools/resolve_organization.py` uses, and for the same reason:
an irreversible act deserves rules that can be tested without the
application running.

Why this is not in `resolve_organization`
----------------------------------------
Because organising must never delete, and
`tests/unit/resolve/test_resolve_organization.py::test_no_module_here_can_delete_anything`
asserts that of both organiser files.  That rule is right and it stays:
filing a reel the plan no longer names is housekeeping, and the
captain's ruling of 2026-09-06 is *"a refusal is cheap and a deleted
timeline is not"*.  Removal is a DIFFERENT act with a DIFFERENT
authority - the captain's ruling of 2026-09-07, *"go ahead and flush out
the davinci organization and clean up all the dead files"* - so it lives
in its own module, under its own name, where a reader can see that it
deletes.

What `DeleteClips` does, measured on 21.0.0b.28
-----------------------------------------------
Probed on a THROWAWAY project, never on the captain's:

- `MediaPool.DeleteClips([item])` returns **True** and removes the pool
  item.  **The file stays on disk**, so removing the item and deleting
  the file are two separate acts, and the item can be removed first and
  verified before anything on disk is touched.
- **`DeleteClips` on a TIMELINE's pool item DELETES THE TIMELINE.**  It
  returns True and the timeline is gone; `GetTimelineCount()` went 2 ->
  1 with no warning and nothing to undo.  A timeline's pool item is
  reached by `Timeline.GetMediaPoolItem()` and reports
  `GetClipProperty("Type") == "Timeline"`, which is how `read_pool`
  already tells the two kinds apart.  This is the catastrophic failure
  mode of the whole module and `assert_removable` is the guard: a
  removal set carrying one timeline removes NOTHING.
- `getattr(pool, "Invented", None)` is None while `hasattr` is True, so
  the probes above judged by what the call RETURNED (AGENTS.md 5).

Two instruments, and a disagreement is a REFUSAL
------------------------------------------------
"Which timeline places this item" decides what may be deleted, so it is
read TWICE, independently:

- **A** - the live scripting API, walking every timeline's tracks
  (`organise_media_pool.read_pool`).
- **B** - `Sm2TiItem.MediaFilePath` in a COPY of the saved `Project.db`.

On the field test both answer 1,364 distinct placed paths with a
symmetric difference of ZERO.  They are not two readings of one source:
A is the running application's model and B is what was last flushed to
disk, so they disagree exactly when the project has changed since it was
saved - which is precisely when a manifest computed a moment ago has
gone stale.  `assert_instruments_agree` refuses then, because deleting
on a stale reading is how the wrong file goes.

A sweep of B must be validated before it is believed.  `Project.db` has
1,948 columns, and a sweep that raises on every one of them reports zero
hits and reads exactly like a sweep that looked; the same trap cost a
day in #600.  `placed_paths_from_database` therefore looks for a path it
KNOWS is placed and refuses if it cannot find it.

No thresholds
-------------
There is no score here and there must not be one.  A file is deletable
when it is on disk, when its pool item is placed on no timeline, and
when no placed item anywhere points at it - three facts, each read, none
weighed.  How many superseded renders are too many was never this
module's question: the captain answered it.

`tests/unit/resolve/test_build_sweep.py`.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

from library.tools.resolve_organization import Artefact, is_generated


class RemovalRefused(Exception):
    """Something about the removal set is not safe, so NOTHING is removed."""


@dataclass(frozen=True)
class Removal:
    """One pool item marked for removal, and the file behind it.

    `file_state` is the whole reason a manifest is worth committing:
    `delete` and `keep_shared` and `already_gone` are three different
    outcomes for the same removal, and a count that blurred them would
    read as permission to delete all of them.
    """
    item_id: str
    name: str
    file_path: str
    folder_path: tuple[str, ...]
    file_state: str            # "delete" | "keep_shared" | "already_gone"
    size_bytes: int = 0


DELETE = "delete"
KEEP_SHARED = "keep_shared"
ALREADY_GONE = "already_gone"
FILE_STATES = (DELETE, KEEP_SHARED, ALREADY_GONE)


def assert_removable(items: Sequence[Artefact]) -> None:
    """Refuse a removal set that carries a timeline, or a placed item.

    THE gate.  `DeleteClips` on a timeline's pool item deletes the
    timeline and returns True, so a single wrong item in a list of 1,216
    destroys a reel the captain approved, silently, with nothing to undo.
    The check is on the whole SET and it refuses the whole set, because
    `DeleteClips` takes a list and a partially-safe list is not safe.
    """
    timelines = [a for a in items if a.kind != "clip"]
    if timelines:
        raise RemovalRefused(
            f"{len(timelines)} of {len(items)} item(s) marked for removal "
            f"are not clips: "
            f"{', '.join(repr(a.name) for a in timelines[:5])}. "
            f"DeleteClips on a timeline's pool item DELETES THE TIMELINE "
            f"and returns True. Nothing was removed.")
    placed = [a for a in items if a.placed_by]
    if placed:
        raise RemovalRefused(
            f"{len(placed)} of {len(items)} item(s) marked for removal are "
            f"placed on a timeline: "
            f"{', '.join(repr(a.name) for a in placed[:5])}. "
            f"Nothing was removed.")


def assert_instruments_agree(live_placed_paths: set[str],
                             saved_placed_paths: set[str]) -> None:
    """Refuse when the live project and the saved database disagree.

    They read the same fact from two places that drift apart exactly
    when the project has changed since it was last saved - which is
    exactly when a manifest computed a moment ago no longer describes
    what is there.
    """
    only_live = sorted(live_placed_paths - saved_placed_paths)
    only_saved = sorted(saved_placed_paths - live_placed_paths)
    if only_live or only_saved:
        raise RemovalRefused(
            f"the live project and the saved Project.db disagree about "
            f"what is placed: {len(only_live)} path(s) placed only in the "
            f"live project, {len(only_saved)} only in the saved database. "
            f"First disagreement: "
            f"{(only_live + only_saved)[0]!r}. Save the project in Resolve "
            f"and take a fresh copy of Project.db, then plan again. "
            f"Nothing was removed.")


def plan_removal(artefacts: Sequence[Artefact],
                 project_root: str,
                 saved_placed_paths: set[str],
                 sizes: dict[str, int] | None = None) -> list[Removal]:
    """Every unplaced generated clip, with what to do about its FILE.

    The items are the same population `unplaced_report` counts, derived
    from the same `artefacts`, so the report and the removal cannot
    disagree about which items are unplaced.

    A file is `keep_shared` when ANY placed item points at it - by
    either instrument.  `pool.ImportMedia` makes a second pool item for a
    path already in the pool, so three of the field test's 1,216 orphans
    name a file a live timeline still plays: removing those ITEMS is
    safe and deleting those FILES would take media off a timeline.
    """
    sizes = sizes or {}
    generated = [a for a in artefacts
                 if a.kind == "clip" and is_generated(a.file_path, project_root)]
    referenced = {a.file_path for a in generated if a.placed_by and a.file_path}
    referenced |= saved_placed_paths

    out: list[Removal] = []
    for artefact in generated:
        if artefact.placed_by:
            continue
        if artefact.file_path in referenced:
            state = KEEP_SHARED
        elif artefact.file_path in sizes:
            state = DELETE
        else:
            state = ALREADY_GONE
        out.append(Removal(
            item_id=artefact.item_id,
            name=artefact.name,
            file_path=artefact.file_path,
            folder_path=artefact.folder_path,
            file_state=state,
            size_bytes=sizes.get(artefact.file_path, 0)))
    return out


def files_to_delete(plan: Sequence[Removal]) -> list[str]:
    """The paths, and only the paths, that may be unlinked."""
    return sorted({r.file_path for r in plan if r.file_state == DELETE})


def assert_file_unreferenced(path: str, referenced: set[str]) -> None:
    """Refuse one file, at the moment of deleting it.

    Checked PER FILE rather than per batch, immediately before the
    unlink, because a manifest is a claim about a moment and the unlink
    happens in a later one.
    """
    if path in referenced:
        raise RemovalRefused(
            f"{path} is played by a timeline. Deleting it would take media "
            f"off a live timeline. Nothing further was deleted.")


def summarise(plan: Sequence[Removal]) -> dict:
    """The counts a reader has to see before authorising this."""
    by_state = {state: [r for r in plan if r.file_state == state]
                for state in FILE_STATES}
    return {
        "items": len(plan),
        "delete_files": len(by_state[DELETE]),
        "keep_shared": len(by_state[KEEP_SHARED]),
        "already_gone": len(by_state[ALREADY_GONE]),
        "bytes_freed": sum(r.size_bytes for r in by_state[DELETE]),
        "bytes_kept_shared": sum(r.size_bytes for r in by_state[KEEP_SHARED]),
    }


def render_summary(counts: dict) -> str:
    """The removal, in the sentences an operator has to read."""
    gib = counts["bytes_freed"] / (1024 ** 3)
    lines = [
        f"  {counts['items']} pool item(s) this pipeline generated are on "
        f"NO timeline and would be REMOVED from the pool.",
        f"    {counts['delete_files']} of their files would be DELETED "
        f"from disk, freeing {gib:.2f} GiB.",
        f"    {counts['already_gone']} file(s) are already gone, so those "
        f"pool items are offline and nothing on disk is touched for them.",
    ]
    if counts["keep_shared"]:
        mib = counts["bytes_kept_shared"] / (1024 ** 2)
        lines.append(
            f"    {counts['keep_shared']} file(s) are KEPT ({mib:.1f} MiB): "
            f"a PLACED item also points at them, so the pool item goes and "
            f"the file stays.")
    lines.append("  Removing a pool item is irreversible and deleting a "
                 "file is irreversible. The manifest lists every path.")
    return "\n".join(lines)


def render_manifest(plan: Sequence[Removal], *, resolve_project: str,
                    project_folder: str, planned_at: str,
                    counts: dict) -> str:
    """Every path, in full, as the committed record of what went.

    Not a size-filtered listing and not a sample: if a recovery is ever
    attempted this file is the only statement of what was there, so it
    names all of it, grouped by what happened to the file rather than
    sorted into one undifferentiated list.
    """
    lines = [
        "# Media-pool orphans removed from the captain's Resolve project",
        "",
        f"- Resolve project: `{resolve_project}`",
        f"- Pipeline project: `{project_folder}`",
        f"- Planned at: {planned_at}",
        "",
        "Every path below was a caption render this pipeline wrote whose",
        "pool item was placed on no timeline, established twice - by the",
        "live scripting API's timeline walk and by `Sm2TiItem.MediaFilePath`",
        "in a copy of the saved `Project.db`. See",
        "`library/tools/orphan_removal.py`.",
        "",
        "## Counts",
        "",
        f"- Pool items removed: **{counts['items']}**",
        f"- Files deleted: **{counts['delete_files']}** "
        f"({counts['bytes_freed'] / (1024 ** 3):.2f} GiB)",
        f"- Files kept because a placed item also uses them: "
        f"**{counts['keep_shared']}** "
        f"({counts['bytes_kept_shared'] / (1024 ** 2):.1f} MiB)",
        f"- Files already gone before this ran: **{counts['already_gone']}**",
        "",
    ]
    headings = {
        DELETE: ("Files deleted", "These were unlinked."),
        KEEP_SHARED: ("Files KEPT - a placed item also uses them",
                      "The pool item was removed; the file stays, because a "
                      "live timeline plays it."),
        ALREADY_GONE: ("Files already gone",
                       "The pool item was offline: only the item was "
                       "removed, nothing on disk was touched."),
    }
    for state in FILE_STATES:
        rows = sorted((r for r in plan if r.file_state == state),
                      key=lambda r: r.file_path)
        heading, note = headings[state]
        lines += [f"## {heading} ({len(rows)})", "", note, ""]
        for row in rows:
            lines.append(f"- `{row.file_path}`")
        lines.append("")
    return "\n".join(lines)
