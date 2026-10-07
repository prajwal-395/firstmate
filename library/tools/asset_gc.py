"""Garbage-collect what Ren generated and no timeline plays.

The leftover problem: `still-overlays` replaced overlay videos with
stills in place and left the replaced `.mov` pool items behind, and the
dedupe pass leaves superseded duplicate stills behind it. The existing
`resolve-prune` path removes pool items and then DELETES the files -
two separate irreversible consents. This module is the recoverable
half: the pool items go, and the generated files move to a trash folder
inside the project with a manifest, never unlinked. A mistaken
collection is a move back, not a re-render.

Two rules, each read, none weighed - the same vocabulary as
`library/tools/orphan_removal.py`, which plans the removal this module
then makes recoverable:

- NEVER-REFERENCED: no timeline in the project uses the item. Proven
  twice - the live timeline walk and `Sm2TiItem.MediaFilePath` in a
  copy of the saved `Project.db` - and a disagreement between the two
  refuses, because deleting on a stale reading is how the wrong file
  goes.
- NEVER-FOOTAGE: Ren itself generated the file. Provenance is a path
  fact (`resolve_organization.is_generated` - inside the project
  directory, where every step writes), never a name parse: footage and
  anything the captain imported lives outside it and is refused even
  when no timeline plays it.

This module holds no Resolve calls and does no pool writes - the
filesystem moves only - so every rule is testable without the
application running; `library/tools/execution/asset_gc.py` drives the
pool side and calls back here for the trash half.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

from library.tools import journal_naming
from library.tools.orphan_removal import (
    Removal,
    RemovalRefused,
    assert_file_unreferenced,
    plan_removal,
)
from library.tools.resolve_organization import is_generated

TRASH_PREFIX = "asset_gc"
TRASH_DIRNAME = "trash"
MANIFEST_NAME = "manifest.json"


class GcRefused(RemovalRefused):
    """Something about the collection set is not safe, so NOTHING moves."""


def trash_dir_for(project_folder: str, when: str | None = None) -> str:
    """Where THIS collection's trash goes. One folder per run, never reused.

    Beside the journals under `pipeline_output`, so a second run cannot
    overwrite the first one's manifest - for a collection the manifest
    is the only way back.
    """
    return journal_naming.unique_path(
        os.path.join(project_folder, "pipeline_output", TRASH_DIRNAME),
        TRASH_PREFIX, when, suffix="")


def assert_gc_safe(plan: list, referenced: set,
                   project_root: str) -> None:
    """Refuse a collection carrying a referenced item or a footage file.

    THE gate, and deliberately redundant with `plan_removal`: the plan
    is a claim about a moment and this is the call that moves files, so
    the proof is repeated here. A plan that names a path some timeline
    plays, or a path outside the project directory, moves NOTHING -
    the     whole set refuses, because a partially-safe list is not safe.
    Only entries that would MOVE (`delete`) are proven: a `keep_shared`
    entry names a file a placed item also uses, which is exactly why
    its file stays while its pool item goes.
    """
    for removal in plan:
        if removal.file_state != "delete":
            continue
        path = removal.file_path
        if path in referenced:
            raise GcRefused(
                f"{path!r} is played by a timeline right now. Moving it "
                f"to trash would take media off a live timeline. Nothing "
                f"was moved.")
        if not is_generated(path, project_root):
            raise GcRefused(
                f"{path!r} is not inside the project directory, so this "
                f"pipeline did not write it - it may be footage or "
                f"something the captain imported. Nothing was moved.")


def plan_gc(artefacts: list, project_root: str,
            saved_placed_paths: set,
            sizes: dict | None = None) -> list:
    """Every unplaced Ren-generated clip, proven twice before it moves.

    `saved_placed_paths` is the database instrument's placed set; the
    live instrument's is read off `artefacts` (`placed_by`). A file is
    collected only when NEITHER instrument places it AND the path fact
    says Ren wrote it. The returned list carries `_project_root`
    alongside so `assert_gc_safe` can re-prove provenance at move time.
    """
    plan = plan_removal(artefacts, project_root, saved_placed_paths,
                        sizes)
    referenced = ({a.file_path for a in artefacts if a.placed_by}
                  | set(saved_placed_paths))
    assert_gc_safe(plan, referenced, project_root)
    return plan


def render_gc_report(plan: list, counts: dict,
                     trash_dir: str) -> str:
    """The dry run: every path with its size, and where it would go."""
    gib = counts["bytes_freed"] / (1024 ** 3)
    lines = [
        f"  {counts['items']} pool item(s) Ren generated are on NO "
        f"timeline and would leave the pool.",
        f"    {counts['delete_files']} of their files would move to "
        f"trash ({gib:.2f} GiB), recoverable under:",
        f"    {trash_dir}",
        f"    {counts['already_gone']} file(s) are already gone, and "
        f"{counts['keep_shared']} are KEPT - a placed item also uses "
        f"them, so the pool item goes and the file stays.",
        "  Nothing was changed. Pass --apply to collect; files are "
        "moved, never deleted.",
    ]
    for removal in sorted(plan, key=lambda r: r.file_path):
        if removal.file_state == "delete":
            mib = removal.size_bytes / (1024 ** 2)
            lines.append(f"    trash {mib:8.1f} MiB  {removal.file_path}")
    return "\n".join(lines)


def move_to_trash(paths: list, referenced: set,
                  trash_dir: str) -> dict:
    """Move the files into `trash_dir` and write the manifest.

    Re-proves per file, at the moment of the move, that no timeline
    plays it: the plan was a claim about an earlier moment and this is
    the call that cannot be undone quietly. A file that has become
    referenced since stops the whole run rather than being skipped
    quietly. Missing files are recorded, not created. Returns the
    record that `manifest.json` also carries.
    """
    os.makedirs(trash_dir, exist_ok=True)
    moved, freed, absent = [], 0, []
    entries = []
    for path in sorted(set(paths)):
        assert_file_unreferenced(path, referenced)
        try:
            size = os.path.getsize(path)
        except OSError:
            absent.append(path)
            entries.append({"original": path, "trashed": None,
                            "size_bytes": 0, "state": "already_gone"})
            continue
        dest = os.path.join(trash_dir, os.path.basename(path))
        stem, ext = os.path.splitext(dest)
        counter = 2
        while os.path.exists(dest):
            dest = f"{stem}_{counter}{ext}"
            counter += 1
        shutil.move(path, dest)
        moved.append(path)
        freed += size
        entries.append({"original": path, "trashed": dest,
                        "size_bytes": size, "state": "trashed"})
    record = {
        "moved_at": datetime.now(timezone.utc).isoformat(),
        "trash_dir": trash_dir,
        "moved": moved,
        "already_gone": absent,
        "bytes_trashed": freed,
        "entries": entries,
    }
    with open(os.path.join(trash_dir, MANIFEST_NAME),
              "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
    return record


def render_gc_manifest(plan: list, *, resolve_project: str,
                       project_folder: str, planned_at: str,
                       counts: dict, trash_dir: str) -> str:
    """Every path, in full, as the committed record of what went.

    Not a size-filtered listing and not a sample: if a restore is ever
    attempted the trash folder plus this file are the only statements
    of what was there, so it names all of it, grouped by what happened
    to the file rather than sorted into one undifferentiated list.
    """
    lines = [
        "# Ren-generated orphans collected into trash",
        "",
        f"- Resolve project: `{resolve_project}`",
        f"- Pipeline project: `{project_folder}`",
        f"- Planned at: {planned_at}",
        f"- Trash folder: `{trash_dir}`",
        "",
        "Every path below was a file this pipeline wrote whose pool",
        "item was placed on no timeline, established twice - by the",
        "live scripting API's timeline walk and by `Sm2TiItem.MediaFilePath`",
        "in a copy of the saved `Project.db` - and proven inside the",
        "project directory, so footage and captain-imported media can",
        "never appear here. See `library/tools/asset_gc.py`.",
        "",
        "Files are MOVED, never deleted: the trash folder holds them,",
        "and its `manifest.json` maps each trashed file back to where",
        "it came from.",
        "",
        "## Counts",
        "",
        f"- Pool items removed: **{counts['items']}**",
        f"- Files moved to trash: **{counts['delete_files']}** "
        f"({counts['bytes_freed'] / (1024 ** 3):.2f} GiB)",
        f"- Files kept because a placed item also uses them: "
        f"**{counts['keep_shared']}** "
        f"({counts['bytes_kept_shared'] / (1024 ** 2):.1f} MiB)",
        f"- Files already gone before this ran: **{counts['already_gone']}**",
        "",
    ]
    headings = {
        "delete": ("Files moved to trash", "These were moved, not unlinked."),
        "keep_shared": ("Files KEPT - a placed item also uses them",
                        "The pool item was removed; the file stays, because a "
                        "live timeline plays it."),
        "already_gone": ("Files already gone",
                         "The pool item was offline: only the item was "
                         "removed, nothing on disk was touched."),
    }
    for state in ("delete", "keep_shared", "already_gone"):
        rows = sorted((r for r in plan if r.file_state == state),
                      key=lambda r: r.file_path)
        heading, note = headings[state]
        lines += [f"## {heading} ({len(rows)})", "", note, ""]
        for row in rows:
            lines.append(f"- `{row.file_path}`")
        lines.append("")
    return "\n".join(lines)


def files_to_trash(plan: list) -> list:
    """The paths, and only the paths, that may move to trash."""
    return sorted({r.file_path for r in plan if r.file_state == "delete"})
