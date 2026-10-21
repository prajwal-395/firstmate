"""The media an ARCHIVED GENERATION still needs, read as liveness roots.

A reel version that carries a recovery archive is a generation Ren can
put BACK (`ren undo <project> <reel> --restore-version N`, PR 3079).
Its media are therefore not debris: a cleanup that cannot hear the
archive takes the file out from under a version that could still be
restored, and the restore then produces a timeline whose media is gone.

One reader, used by every cleanup path that can touch project media:

- the pool prune (`library/tools/execution/prune_orphans.py`),
- the build sweep and the reviewed purge, through
  `library/tools/caption_asset_gc.collect_pipeline_roots`,
- the machine storage plan (`library/tools/storage_lifecycle.py`).

WHAT IS READ
-------------
Only the project's OWN review area, and only the records that declare
ownership of a generation:

- `reel_versions.json` and its pending rollback/redo companions - the
  version ledger. Every entry's `rows` snapshot names clip NAMES, and
  the `recovery_archive` receipt names the archive plus the ASSET
  MANIFEST of the media the generation plays.
- the undo journal, the recovery archives, and the timeline snapshots -
  the journal a recorded version points at, which names each clip's
  media under `source_file`.
- every `.drt` recovery archive itself: the archive is parsed for the
  media paths it references, because a receipt written before the
  current format carries no asset manifest at all.

The reading is FAIL-CLOSED. A retained record that EXISTS and cannot be
read is not an empty record, so it refuses rather than proving nothing;
the same is true of an archive that cannot be parsed. An unreadable
root reads exactly like a project that references nothing, and a
cleanup that believes that would delete what it cannot see.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

MEDIA_PATH_KEYS = frozenset({
    "file_path", "media_path", "source_path", "asset_path", "source_file",
})
"""The keys a retained record names its media under.

`source_file` is the one the undo journal and the reel read use
(`reel_read` writes every item's media under it). It was missing from
the pool prune's set: a recorded version's own journal said which file
it played and the prune did not hear it.
"""

RECEIPT_KEYS = ("recovery_archive", "recovery_archives")
"""The keys a version entry keeps its recovery receipts under.

`recovery_archive` names the archive the version was born with, and
`recovery_archives` is the whole set, oldest first (VC-002, PR 3109): a
version can be archived more than once, and each archive is immutable.
A reader that heard only the first one would miss the media of every
later archive.
"""

RETAINED_RECORD_NAMES = (
    "reel_versions.json",
    "reel_versions_pending_rollback.json",
    "reel_versions_pending_redo.json",
    "resolve_checkpoints.json",
)
"""The ledgers that name a generation, by name. DECLARED, never globbed
over every JSON in the review area."""

RETAINED_RECORD_DIRS = (
    "undo", "recovery_archives", "timeline_snapshots", "snapshots",
)
"""The review sub-areas whose JSON records a generation's own media."""

ARCHIVE_DIRNAME = "recovery_archives"
"""Where the version store writes the `.drt` recovery archives
(`library/tools/versions/store.DRT_RECOVERY_DIRNAME`)."""


class VersionDependencyUnreadable(RuntimeError):
    """A retained record or archive exists and could not be read.

    Not an empty record: a cleanup that cannot hear it cannot prove
    anything about the media it names.
    """


def review_root(project_folder) -> Path:
    """The project's own review area, where the version records live."""
    return Path(str(project_folder)) / "pipeline_output" / "review"


def retained_record_paths(project_folder) -> list[Path]:
    """Every retained record that can name a generation's media."""
    review = review_root(project_folder)
    paths = [review / name for name in RETAINED_RECORD_NAMES]
    paths.extend(sorted(review.glob("*.timeline.json")))
    for name in RETAINED_RECORD_DIRS:
        directory = review / name
        if directory.is_dir():
            paths.extend(sorted(directory.rglob("*.json")))
    return paths


def retained_archive_paths(project_folder) -> list[Path]:
    """Every `.drt` recovery archive the version store wrote."""
    directory = review_root(project_folder) / ARCHIVE_DIRNAME
    if not directory.is_dir():
        return []
    return sorted(directory.rglob("*.drt"))


def _normalize(path: str) -> str:
    return os.path.abspath(os.path.expanduser(str(path)))


def _add(paths: set[str], raw) -> None:
    """Record a path in both its written and its resolved form.

    A record names its media by the path it wrote; the project layout
    names the same file by its RESOLVED path. Both forms are recorded,
    so a dependency matches whichever side is asking.
    """
    if not isinstance(raw, str) or not raw:
        return
    normalized = _normalize(raw)
    paths.add(normalized)
    resolved = os.path.realpath(normalized)
    if resolved != normalized:
        paths.add(resolved)


def _collect_media(value, paths: set[str]) -> None:
    """Walk a record for the media it names, under the keys it uses."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in MEDIA_PATH_KEYS:
                _add(paths, item)
            # A recovery receipt's asset manifest spells each media file
            # "path", so the receipt is read for what it DECLARES rather
            # than for the keys it happens to share with other records.
            # A version can carry SEVERAL receipts (`recovery_archives`,
            # VC-002), and each one declares its own media.
            if key in RECEIPT_KEYS:
                _collect_receipt_assets(item, paths)
            _collect_media(item, paths)
    elif isinstance(value, list):
        for item in value:
            _collect_media(item, paths)


def _collect_receipt_assets(receipt, paths: set[str]) -> None:
    """The media one recovery receipt declares, under its asset manifest."""
    if isinstance(receipt, dict):
        for asset in receipt.get("assets") or ():
            if isinstance(asset, dict):
                _add(paths, asset.get("path"))
        return
    if isinstance(receipt, list):
        for one in receipt:
            _collect_receipt_assets(one, paths)


def dependency_paths(project_folder) -> set[str]:
    """Every path a retained generation needs: its media and its archive.

    Raises `VersionDependencyUnreadable` when a retained record or
    archive exists and cannot be read. The caller decides what an
    unreadable root means; in every cleanup path it means the cleanup
    refuses.
    """
    paths: set[str] = set()
    for record_path in retained_record_paths(project_folder):
        if not record_path.is_file():
            continue
        try:
            _collect_media(json.loads(
                record_path.read_text(encoding="utf-8")), paths)
        except (OSError, ValueError) as exc:
            raise VersionDependencyUnreadable(
                f"retained recovery record {record_path} is unreadable; "
                f"cleanup cannot prove its media is unreferenced "
                f"({exc})") from exc
    for archive_path in retained_archive_paths(project_folder):
        # The archive is itself a dependency: it is the way back.
        _add(paths, str(archive_path))
        try:
            from library.tools.resolve_archive import archive_media_paths

            media_paths = archive_media_paths(archive_path)
        except Exception as exc:
            raise VersionDependencyUnreadable(
                f"retained recovery archive {archive_path} is unreadable "
                f"({exc}); cleanup cannot prove its media is "
                f"unreferenced") from exc
        for media in media_paths:
            _add(paths, media)
    return paths


__all__ = [
    "VersionDependencyUnreadable",
    "dependency_paths",
    "retained_archive_paths",
    "retained_record_paths",
    "review_root",
]
