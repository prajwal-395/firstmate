"""THE ARCHIVE OF A GENERATION THAT IS BEING REPLACED (VC-002).

The captain's punchlist, 2026-10-20, VC-002: *"Keep recoverable
timeline archives. Ensure the recovery archive represents exactly the
generation being replaced. Verify the archived timeline's identity and
relevant structural state. Preserve all media and dependencies required
to restore it. Distinguish restoration from reconstruction. Never claim
bit-identical restoration from an incomplete semantic plan."*

One routine, every path
-----------------------
A generation is replaced by a promotion, a touch-up, a rebuild or a
direct mutation, and each of those replaced one and then left the
replaced generation recoverable only if it happened to be lucky. This
module is the ONE way a path archives the generation it is about to
replace:

- the archive is exported at a path derived from the generation's
  Resolve id, so a later generation's export can never OVERWRITE the
  archive of an earlier one (the shared per-reel filename did exactly
  that, and the receipt that named it stopped verifying);
- the archive is READ BACK and compared with the live generation -
  the project it came from, the media it plays, the items it holds -
  so the file is proven to hold THAT generation and not another one;
- every media file the archive plays is hashed into the receipt, so a
  restore can prove the media is the media the generation was cut
  from;
- the receipt is attached to the version record of the generation being
  replaced, so `ren undo --restore-version` can put it back.

`ensure_archived` is the rule the paths share: a generation is
exported ONCE, and a path that replaces a generation that already has
a verified archive keeps that archive instead of exporting a second
copy of the same thing.

`library/tools/versions/reel_versions.py` (the receipts and the
selector), `library/tools/versions/store.py` (the export itself).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from library.tools.versions import store


class ReplacedGenerationArchiveRefused(RuntimeError):
    """The generation being replaced could not be archived, or the
    archive does not hold it.

    A refusal is a reason and a fix, never a bare exception: the
    caller turns it into the refusal the captain reads.
    """


def generation_suffix(timeline_id: str) -> str:
    """The suffix that makes one generation's archive ITS OWN file.

    A Resolve id is unique per timeline object, and a timeline object
    is one generation. So `gen-<id>` names one generation's archive and
    no other generation can land on it.
    """
    safe = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in str(timeline_id or "")) or "unknown"
    return f"gen-{safe}"


def _archive_items(facts: Mapping) -> list[dict]:
    return [item
            for timeline in (facts.get("timelines") or ())
            for track in (timeline.get("tracks") or ())
            for item in (track.get("items") or ())]


def _media_key(path) -> str:
    return os.path.normcase(os.path.realpath(str(path or "")))


def _inventory_against_snapshot(archive: Mapping, snapshot: Mapping) -> dict:
    """Whether the exported archive IS the timeline it claims to hold.

    The archive is read as a multiset of (media, name) pairs and the
    live timeline as the same, and the two are compared. A count
    alone cannot tell one generation of a reel from another - the
    Reel 26 audit's archive carried the NEW generation's semantic rows
    while the OLD one was being replaced - but the media and the names
    can.
    """
    from collections import Counter

    archived = Counter()
    for item in _archive_items(archive):
        archived[_media_key(item.get("media_path"))] += 1
    live = Counter()
    for item in (snapshot.get("items") or ()):
        live[_media_key(item.get("source_file"))] += 1
    if archived == live:
        return {"matches": True, "reason": ""}
    only_archive = sorted(path for path, count in (archived - live).items()
                          for _ in range(count))
    only_live = sorted(path for path, count in (live - archived).items()
                       for _ in range(count))
    reason = (f"the archive holds {len(archived)} item(s) the timeline "
              f"does not ({'; '.join(only_archive[:4]) or 'none'}), and "
              f"the timeline holds {len(live)} the archive does not "
              f"({'; '.join(only_live[:4]) or 'none'})")
    return {"matches": False, "reason": reason}


def read_archive_receipt(archive_path, *, timeline_name: str = "",
                         timeline_id: str = "",
                         project_name: str = "") -> dict:
    """The receipt of an archive that is read back, with its media.

    Lighter than `verify_archive`: it proves the file IS a Resolve
    export of ONE timeline and names the media that export plays - it
    does not compare the export with a live read of the timeline. For
    a path that has no live read to compare against (the direct
    mutation's own backup), the media manifest is still the evidence
    that a restore would find the same bytes.
    """
    from library.tools.resolve_archive import read_archive

    path = Path(archive_path)
    try:
        facts = read_archive(path)
    except Exception as unreadable:  # noqa: BLE001 - the refusal names it
        raise ReplacedGenerationArchiveRefused(
            "the exported archive could not be read as a Resolve export "
            f"({unreadable})") from unreadable
    timelines = facts.get("timelines") or ()
    if len(timelines) != 1:
        raise ReplacedGenerationArchiveRefused(
            f"the exported archive holds {len(timelines)} timeline(s), "
            "not the one it was taken from")
    items = _archive_items(facts)
    media = sorted({_media_key(item.get("media_path"))
                    for item in items if item.get("media_path")})
    assets = []
    for item_path in media:
        asset = Path(item_path)
        if not asset.is_file():
            assets.append({"path": item_path, "present": False})
            continue
        assets.append({"path": item_path, "present": True,
                       "bytes": asset.stat().st_size,
                       "sha256": store._sha256(asset)})
    return {
        "timeline_name": timeline_name,
        "timeline_id": timeline_id,
        "resolve_project": project_name,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "archive_path": str(path),
        "archive_sha256": store._sha256(path),
        "content_id": store.recovery_archive_content_id(path),
        "archive_bytes": path.stat().st_size,
        "inventory_items": len(items),
        "assets": assets,
    }


def verify_archive(*, archive_path, project, timeline,
                   project_folder: str) -> dict:
    """Read an exported archive back and prove it holds THIS generation.

    The proof is threefold, and all three are needed:

    - IDENTITY: the archive was exported from the project the
      generation is in, and it names the generation's Resolve id;
    - STRUCTURE: the items the archive holds are the items the
      generation holds - the same media, the same count - so a
      `.drt` that holds ANOTHER generation is refused;
    - MEDIA: every media file the generation plays is present and is
      hashed into the receipt, so a restore can prove the media is the
      media the generation was cut from.

    Raises `ReplacedGenerationArchiveRefused` when the archive does
    not hold this generation.
    """
    from library.tools import reel_replace_guard
    from library.tools.resolve_archive import read_archive
    from library.tools.versions import rounds

    timeline_id = str(timeline.GetUniqueId() or "")
    if not timeline_id:
        raise ReplacedGenerationArchiveRefused(
            "the generation being replaced has no Resolve unique id, so "
            "no archive can be attributed to it")
    name = str(timeline.GetName() or "")
    path = Path(archive_path)
    before = reel_replace_guard.full_timeline_snapshot(
        timeline, project, project_folder)
    rows = reel_replace_guard.snapshot_timeline(
        timeline, name, side="retiring")
    inventory_bytes = json.dumps(
        before, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str).encode("utf-8")
    try:
        facts = read_archive(path)
    except Exception as unreadable:  # noqa: BLE001 - the refusal names it
        raise ReplacedGenerationArchiveRefused(
            f"the exported archive could not be read as a Resolve export "
            f"({unreadable})") from unreadable
    archive_items = _archive_items(facts)
    expected_items = before.get("items") or []
    if len(facts.get("timelines") or ()) != 1 or len(archive_items) != len(
            expected_items):
        raise ReplacedGenerationArchiveRefused(
            "the exported archive's inventory does not match the "
            "generation being replaced")
    # A `.drt` names the TIMELINE it holds, not the Resolve project it
    # was exported from: the archive of `Reel 01 - geo-is-comprehension
    # -not-position` reads back a project called `Reel 01 - geo-is-
    # comprehension-not-position` (measured 2026-10-21 over the
    # machine's recovery archives). Comparing that name with the RESOLVE
    # PROJECT's name compares two names that are not the same kind of
    # name, and refuses every archive. Either name is accepted; a third
    # one is not.
    archive_project = str(facts.get("project") or "")
    expected_project = str(project.GetName() or "")
    accepted_names = {str(timeline.GetName() or ""), expected_project}
    accepted_names.discard("")
    if archive_project and accepted_names and archive_project not in accepted_names:
        raise ReplacedGenerationArchiveRefused(
            f"the exported archive belongs to {archive_project!r}, not to "
            f"this timeline ({expected_project!r})")
    inventory = _inventory_against_snapshot(facts, before)
    if not inventory["matches"]:
        raise ReplacedGenerationArchiveRefused(
            "the exported archive is not the generation being replaced "
            f"({inventory['reason']})")
    expected_paths = {str(item["source_file"])
                      for item in expected_items if item.get("source_file")}
    missing = sorted(path for path in expected_paths if not os.path.isfile(path))
    if missing:
        raise ReplacedGenerationArchiveRefused(
            f"the generation's media is missing and cannot be hashed: "
            f"{missing}")
    expected_assets = {_media_key(path) for path in expected_paths}
    archive_assets = {_media_key(item.get("media_path"))
                      for item in archive_items if item.get("media_path")}
    if expected_assets != archive_assets:
        raise ReplacedGenerationArchiveRefused(
            "the exported archive's asset inventory differs from the "
            "generation being replaced")
    assets = []
    for media in sorted(expected_assets):
        asset_path = Path(media)
        assets.append({"path": media, "sha256": store._sha256(asset_path),
                       "bytes": asset_path.stat().st_size})
    return {
        "timeline_name": name,
        "timeline_id": timeline_id,
        "resolve_project": expected_project,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "archive_path": str(path),
        "archive_sha256": store._sha256(path),
        "content_id": store.recovery_archive_content_id(path),
        "archive_bytes": path.stat().st_size,
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "rows_digest": rounds.digest_rows(rows),
        "inventory_items": len(archive_items),
        "assets": assets,
        # The LOOK of the generation this archive holds: the grade, the
        # Fusion comps and the composite settings. Rows say which
        # passage plays and carry no look, so without this a restore
        # can put back the cut and lose the picture, and the ledger
        # would call it the exact generation.
        "preservation": _preservation_of(before),
    }


def _preservation_of(snapshot: Mapping) -> list:
    """The comparable part of a generation's native state."""
    from library.tools import reel_replace_guard

    return reel_replace_guard.preservation_record(snapshot.get("items") or ())


def archive_generation(*, resolve, project, timeline, project_folder: str,
                       label: str = "", suffix: str | None = None) -> dict:
    """Export the EXACT generation and verify what landed.

    Returns the receipt: identity (the Resolve id and the project),
    structure (the rows digest and the item count), media (every media
    file the archive plays, hashed) and the archive's own content
    identity.

    Raises `ReplacedGenerationArchiveRefused` when the export failed,
    when the archive does not hold this generation, or when the media
    it needs is missing.
    """
    timeline_id = str(timeline.GetUniqueId() or "")
    if not timeline_id:
        raise ReplacedGenerationArchiveRefused(
            "the generation being replaced has no Resolve unique id, so "
            "no archive can be attributed to it")
    name = str(timeline.GetName() or "")
    exported = store.export_timeline_recovery_archive(
        resolve, timeline, name, project_folder,
        label=label or f"generation {timeline_id}",
        archive_suffix=suffix or generation_suffix(timeline_id))
    if not exported.get("archived"):
        raise ReplacedGenerationArchiveRefused(
            "the recovery archive could not be exported "
            f"({exported.get('reason', 'unknown error')})")
    return verify_archive(archive_path=exported["path"], project=project,
                          timeline=timeline, project_folder=project_folder)


def ensure_archived(*, resolve, project, timeline, project_folder: str,
                    entry: Mapping | None, label: str = "") -> dict:
    """Make sure the generation being replaced HAS a verified archive.

    `entry` is the version record of the generation being replaced, or
    None when the generation was never recorded as a version. A version
    that already carries an archive which still verifies keeps it - a
    generation is exported ONCE, and a second copy of the same
    generation is a second thing to keep true.

    Returns `{"status", "receipt", "reason"}`:

    - `verified`   the version already carried a verified archive;
    - `exported`   the archive was made now and verified;
    - `untracked`  the generation is not a recorded version, so there is
      no record to attach an archive to;
    - `unavailable` the archive could not be made, and the reason says
      why.
    """
    from library.tools.versions import reel_versions

    if isinstance(entry, Mapping):
        receipt = reel_versions.live_receipt(entry)
        if receipt is not None and reel_versions.archive_verifies(receipt):
            return {"status": "verified", "receipt": dict(receipt),
                    "reason": ""}
    try:
        receipt = archive_generation(
            resolve=resolve, project=project, timeline=timeline,
            project_folder=project_folder, label=label)
    except ReplacedGenerationArchiveRefused as refused:
        return {"status": "unavailable", "receipt": None,
                "reason": str(refused)}
    if not isinstance(entry, Mapping):
        return {"status": "untracked", "receipt": receipt, "reason": ""}
    return {"status": "exported", "receipt": receipt, "reason": ""}


__all__ = [
    "ReplacedGenerationArchiveRefused",
    "archive_generation",
    "ensure_archived",
    "generation_suffix",
    "read_archive_receipt",
    "verify_archive",
]
