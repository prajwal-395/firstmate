"""A captain's hand edit becomes a Ren VERSION, not a lost state.

The captain, 2026-10-19, on Reel 26: he had corrected the corner logo's
timing by hand in Resolve, and that hand edit was nowhere Ren knew
about. The next rebuild, undo or rollback had no record of what the
timeline actually held, so "the state the captain accepted" was a
story, not a version.

What this is
-------------
An ADOPTED version: the captain's own edit, observed on the live
timeline by its Resolve unique id, captured whole - the rows, the
immutable `.drt` of that exact generation, and the hashed asset
manifest that says which media the generation plays - and appended to
the reel's version ledger.

It is a STATE, not an act: `ren undo` reverses what Ren did, and it
does not reverse the captain's hand. So this kind is deliberately not
an ACT (`reel_versions.ACTS`), and the version it appends is never
rewritten.

The audit's own words (Reel 26 grade and version-control audit,
"Logo adoption recommendation"):

    "Ren should observe the exact live timeline by its Resolve unique
    ID, capture a complete before/after snapshot, and file the
    captain's change as a manual edit with the reason that the corner
    logo timing was corrected... The version model should append an
    adopted/manual version pointing to that identity and complete
    snapshot; it should not fabricate a Ren build entry or imply a
    render was refreshed."

`tests/unit/reels/test_reel_adoption.py`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

from library.tools.versions import replaced_generation
from library.tools.versions import reel_versions, rounds, store


def _archive_item_count(archive_path: str) -> int:
    """How many items the exported archive holds.

    Read out of the archive itself, so the count is the archive's and
    not the live timeline's: the restore compares the two.
    """
    from library.tools.resolve_archive import read_archive

    try:
        facts = read_archive(archive_path)
    except (OSError, ValueError):
        return 0
    return sum(len(track.get("items") or ())
               for timeline in (facts.get("timelines") or ())
               for track in (timeline.get("tracks") or ()))


def adopt_live_timeline(project_folder: str, timeline_name: str, *,
                        reason: str, by: str = "captain",
                        timeline_id: str = "",
                        resolve_project_name: str = "",
                        resolve=None) -> dict:
    """Record the captain's hand edit on a live timeline as a version.

    The live timeline is resolved by its Resolve unique id when one is
    given - a NAME is not an identity, and the field-test project holds
    several timelines under one reel's name - and the name is then the
    assertion.

    Returns the appended version entry, with the recovery receipt that
    makes it restorable.
    """
    from library.tools import reel_build
    from library.tools.qualification_project import switch_project
    from library.tools.resolve_lock import resolve_lease
    from library.tools.timeline_ingest import resolve_project_exactly

    project_folder = os.path.abspath(project_folder)
    if not resolve_project_name:
        resolve_project_name = _project_name(project_folder)

    if resolve is None:
        with resolve_lease("adopt a hand-edited reel", exclusive=True):
            resolve = reel_build._connect_resolve()
            manager = resolve.GetProjectManager()
            switch_project(manager, resolve_project_name)
            project = resolve_project_exactly(manager, resolve_project_name)
            return _adopt(project_folder, project, resolve, timeline_name,
                          reason=reason, by=by, timeline_id=timeline_id)
    project = resolve_project_exactly(resolve.GetProjectManager(),
                                      resolve_project_name)
    return _adopt(project_folder, project, resolve, timeline_name,
                  reason=reason, by=by, timeline_id=timeline_id)


def _project_name(project_folder: str) -> str:
    """The Resolve project this project declares, or its own name."""
    import yaml

    path = os.path.join(project_folder, "project.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        named = str((config.get("resolve") or {}).get("project_name") or "")
        if named:
            return named
    return os.path.basename(project_folder)


def _timeline_by_identity(project, timeline_name: str, timeline_id: str):
    """The one timeline this adoption is about."""
    matches = []
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        candidate = project.GetTimelineByIndex(index)
        if not candidate:
            continue
        if timeline_id:
            try:
                unique_id = str(candidate.GetUniqueId() or "")
            except Exception:  # noqa: BLE001
                unique_id = ""
            if unique_id == str(timeline_id):
                matches.append(candidate)
        elif candidate.GetName() == timeline_name:
            matches.append(candidate)
    if not matches:
        raise reel_versions.ReelVersionsUnreadable(
            f"no timeline named {timeline_name!r} (id {timeline_id!r}) is "
            f"in this project, so there is nothing to adopt")
    if len(matches) > 1:
        raise reel_versions.ReelVersionsUnreadable(
            f"{len(matches)} timelines are named {timeline_name!r}; a name "
            f"cannot say which generation to adopt")
    target = matches[0]
    if timeline_id and str(target.GetUniqueId() or "") != str(timeline_id):
        raise reel_versions.ReelVersionsUnreadable(
            f"the Resolve id {timeline_id!r} names timeline "
            f"{target.GetName()!r}, not {timeline_name!r}")
    return target


def _adopt(project_folder: str, project, resolve, timeline_name: str, *,
           reason: str, by: str, timeline_id: str) -> dict:
    """Capture the live generation and append the adopted version."""
    from library.tools import reel_read
    from library.tools.resolve_lock import assert_current_timeline

    target = _timeline_by_identity(project, timeline_name, timeline_id)
    unique_id = str(target.GetUniqueId() or "")
    assert_current_timeline(project, target)

    tracks = reel_read.read_tracks(target)
    rows = reel_read.rows_of({"tracks": tracks})

    archive = store.export_timeline_recovery_archive(
        resolve, target, target.GetName(), project_folder,
        # VC-002: the captain's hand edit is a generation like any other,
        # and its archive is a file of its own - the shared per-reel path
        # is overwritten by the next build, and a version that cannot be
        # restored is a note, not a version.
        archive_suffix=replaced_generation.generation_suffix(unique_id))
    if not archive.get("archived"):
        raise reel_versions.ReelVersionsUnreadable(
            f"the live generation of {timeline_name!r} could not be "
            f"exported to a recovery archive "
            f"({archive.get('reason', 'unknown reason')}), so it cannot "
            f"be adopted: a version that cannot be restored is a note, "
            f"not a version")
    archive_path = archive["path"]

    assets = []
    media = set()
    for track in tracks:
        for clip in track.get("clips") or ():
            source = str(clip.get("source_file") or "")
            if not source:
                continue
            media.add(source)
    for source in sorted(media):
        if not os.path.isfile(source):
            continue
        assets.append({"path": source,
                       "bytes": os.path.getsize(source),
                       "sha256": store._sha256(source)})
    receipt = {
        "timeline_id": unique_id,
        "timeline_name": target.GetName(),
        "resolve_project": str(project.GetName() or ""),
        "archive_path": str(archive_path),
        "archive_sha256": store._sha256(archive_path),
        "content_id": store.recovery_archive_content_id(archive_path),
        "rows_digest": rounds.digest_rows(rows),
        # The item count the archive holds: the restore checks the
        # archive's inventory against it, and a receipt without it
        # cannot be restored at all.
        "inventory_items": _archive_item_count(archive_path),
        "assets": assets,
    }
    entry = reel_versions.record(
        project_folder, target.GetName(),
        kind=reel_versions.KIND_ADOPT,
        rows=rows,
        reason=str(reason or ""),
        by=str(by or "captain"),
        timeline_id=unique_id,
        adopted=True,
        recovery_archive=receipt)
    reel_versions.attach_recovery_archive(
        project_folder, target.GetName(), int(entry["version"]), receipt)
    # PROD-008: a hand edit is a PRODUCT outcome as well as a version.
    # The captain's correction is now read where the product reads it:
    # what he changed, how much, and whether the change was a fault in
    # the output or a matter of taste. Recording it is instrumentation:
    # the adoption stands even when the outcome cannot be written.
    try:
        from library.tools import edit_outcome

        edit_outcome.record_outcome(project_folder, target.GetName())
    except Exception as exc:  # noqa: BLE001 - the adoption already landed
        print(f"  edit outcome unrecorded for {target.GetName()!r}: "
              f"{exc!r} - the adopted version stands without it",
              file=sys.stderr)
    return entry


__all__ = ["adopt_live_timeline"]
