"""REEL VERSIONS: every state a reel's timeline has held, in order, never overwritten.
Part of the version model (`library/tools/versions/__init__.py`).

The captain, 2026-09-23 (D5): *"Both: in-place for touches, versions for
rebuilds"*. This ledger is the "versions" half and the stack the undo
reads for both halves.

Why a ledger beside `rounds.json`
---------------------------------
A round holds ONE entry per reel (`rounds.stamp_promotion` writes
`current["reels"][final] = entry`), so a reel rebuilt twice inside one
round kept only the second build: the first was OVERWRITTEN, and a
rebuild could not be rolled back to it because nothing said what it
was. A round is a grouping of versions; this is the versions
themselves - appended, numbered per reel, and never rewritten except to
mark one undone.

What a version is
-----------------
`{"version", "kind", "at", "rows", "rows_digest", ...}` per reel, where
`rows` is the `reel_read.rows_of` snapshot every other version record
already stores (`rounds`, `variants`) - text, a few kilobytes, the D7
"binaries recorded by hash and regenerated" shape. The kinds:

- `build`    a rebuild promoted. Carries `plan_moment` - the reel's own
             entry in `reel_proposals_v2.json` at build time - because
             that is the declaration a rollback restores.
- `touch`    a touch-up promoted (`reel_touchup`). Carries `journal`,
             the undo journal entry that can reverse it IN PLACE
             (`library/tools/undo_journal.py`).
- `undo`     a touch reversed in place.
- `rollback` a rebuild rolled back to the version before it.

`build` and `touch` are ACTS - the things `ren undo` reverses. `undo`
and `rollback` are states the undo produced; they are recorded so the
ledger stays a complete account of what the timeline held, and they are
never themselves undone (there is no redo: a reversed act is re-done by
doing it again). An act that was reversed carries `undone_by`, naming
the version that reversed it, and the next undo reads past it.

Where it lives: `pipeline_output/review/reel_versions.json`, on the
store's allow-list, written through `store.write_record` like every
other record here.

`tests/unit/resolve/test_undo_journal.py`.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime

from library.tools.versions import store

REEL_VERSIONS_FILENAME = "reel_versions.json"
REEL_VERSIONS_FORMAT = "reel_versions/1"

KIND_BUILD = "build"
KIND_TOUCH = "touch"
KIND_UNDO = "undo"
KIND_ROLLBACK = "rollback"

#: What `ren undo` reverses. The other two kinds are what it produced.
ACTS = (KIND_BUILD, KIND_TOUCH)
KINDS = (KIND_BUILD, KIND_TOUCH, KIND_UNDO, KIND_ROLLBACK)


class ReelVersionsUnreadable(RuntimeError):
    """The ledger exists and cannot be read. Never replaced with an empty one."""


def path_for(project_folder) -> str:
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        REEL_VERSIONS_FILENAME)


def read(project_folder) -> dict:
    """The ledger, or an empty one when none was ever written."""
    path = path_for(project_folder)
    if not os.path.exists(path):
        return {"format": REEL_VERSIONS_FORMAT, "reels": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise ReelVersionsUnreadable(
            f"{path} exists but could not be read ({unreadable}). It is "
            f"the only account of which version each reel has held; an "
            f"undo that read it as empty would reverse the wrong thing.") \
            from unreadable
    if (not isinstance(document, dict)
            or not isinstance(document.get("reels"), dict)):
        raise ReelVersionsUnreadable(
            f"{path} is not a {REEL_VERSIONS_FORMAT} document.")
    return document


def _write(project_folder, document: Mapping) -> str:
    return store.write_record(path_for(project_folder), document,
                              prefix=".reel-versions-")


def versions_of(project_folder, final: str) -> list:
    """Every version of one reel, oldest first."""
    return list(read(project_folder)["reels"].get(final) or ())


def record(project_folder, final: str, *, kind: str, rows: Mapping,
           at: str | None = None, **fields) -> dict:
    """Append the next version of `final`. Returns the entry written.

    `fields` carries the kind's own evidence (`plan_moment`, `journal`,
    `round`, `built_with`, `undoes`, `batch`) verbatim; absent is
    recorded absent, never filled in.
    """
    from library.tools.versions import rounds as _rounds

    if kind not in KINDS:
        raise ValueError(f"{kind!r} is not a reel version kind {KINDS}")
    document = read(project_folder)
    history = list(document["reels"].get(final) or ())
    entry = {
        "version": len(history) + 1,
        "kind": kind,
        "at": at or datetime.now(UTC).isoformat(
            timespec="seconds"),
        "rows": dict(rows or {}),
        "rows_digest": _rounds.digest_rows(rows or {}),
    }
    entry.update({key: value for key, value in fields.items()
                  if value is not None})
    history.append(entry)
    document["reels"][final] = history
    document["format"] = REEL_VERSIONS_FORMAT
    _write(project_folder, document)
    return entry


def mark_undone(project_folder, final: str, version: int,
                by_version: int) -> None:
    """Say which version reversed act `version`. The act is not removed."""
    document = read(project_folder)
    history = list(document["reels"].get(final) or ())
    for entry in history:
        if int(entry.get("version", 0)) == int(version):
            entry["undone_by"] = int(by_version)
            break
    else:
        raise ReelVersionsUnreadable(
            f"{final!r} has no version {version} to mark undone.")
    document["reels"][final] = history
    _write(project_folder, document)


def live_acts(project_folder, final: str) -> list:
    """The acts on this reel nothing has reversed, newest LAST."""
    return [entry for entry in versions_of(project_folder, final)
            if entry.get("kind") in ACTS and not entry.get("undone_by")]


def latest_act(project_folder, final: str) -> dict | None:
    acts = live_acts(project_folder, final)
    return acts[-1] if acts else None


def state_before(project_folder, final: str, version: int) -> dict | None:
    """The version the timeline held immediately before `version`."""
    earlier = [entry for entry in versions_of(project_folder, final)
               if int(entry["version"]) < int(version)]
    return earlier[-1] if earlier else None


def build_before(project_folder, final: str, version: int) -> dict | None:
    """The newest REBUILT state before `version` that carries its plan.

    A rollback restores a plan and rebuilds, so the state it can reach
    is one a build (or an earlier rollback) produced, never one a
    touch-up produced on top of it - those are re-applied from their
    journals afterwards (`undo_journal.rollback_rebuild`).
    """
    for entry in reversed(versions_of(project_folder, final)):
        if int(entry["version"]) >= int(version):
            continue
        if entry.get("kind") in (KIND_BUILD, KIND_ROLLBACK):
            return entry
    return None


PENDING_ROLLBACK_FILENAME = "reel_versions_pending_rollback.json"


def _pending_path(project_folder) -> str:
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        PENDING_ROLLBACK_FILENAME)


def set_pending_rollback(project_folder, final: str, version: int) -> None:
    """Tell the promotion that the build about to land rolls act `version` back.

    The rollback runs the reels process, which records whatever it
    promotes as a `build`; this is how that promotion knows to record a
    `rollback` instead and mark the act it reverses.
    """
    path = _pending_path(project_folder)
    pending = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            pending = json.load(handle)
    pending[final] = int(version)
    store.write_record(path, pending, prefix=".pending-")


def take_pending_rollback(project_folder, final: str) -> int | None:
    """The act a promotion of `final` rolls back, consumed once; or None."""
    path = _pending_path(project_folder)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        pending = json.load(handle)
    version = pending.pop(final, None)
    if pending:
        store.write_record(path, pending, prefix=".pending-")
    else:
        os.unlink(path)
    return version


__all__ = [
    "ACTS",
    "KIND_BUILD",
    "KIND_ROLLBACK",
    "KIND_TOUCH",
    "KIND_UNDO",
    "ReelVersionsUnreadable",
    "build_before",
    "latest_act",
    "live_acts",
    "mark_undone",
    "path_for",
    "read",
    "record",
    "set_pending_rollback",
    "state_before",
    "take_pending_rollback",
    "versions_of",
]
