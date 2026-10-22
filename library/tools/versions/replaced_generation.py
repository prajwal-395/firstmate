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
  IDENTITY - its Resolve id AND the digest of the rows it holds - so a
  later generation's export can never OVERWRITE the archive of an
  earlier one (the shared per-reel filename did exactly that, and the
  receipt that named it stopped verifying);
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

VC-009: what a generation IS, and what one costs
-------------------------------------------------
A Resolve id names a timeline OBJECT. It does not name a generation:
the same object holds several generations over time - the captain
edits it by hand, a build replaces it, a touch swaps it - and every
one of those is a different generation that has to be recoverable on
its own. Measured 2026-10-21 on the Ren-owned `vc004-hand-edit`
scratch copy: ONE Resolve id (`...0061a60e06`) carried FOUR recovery
receipts, three of them full exports of the same object, and the
second adoption OVERWROTE the first adoption's file at the same
path - so the first version's receipt named a file whose bytes had
changed, and that version stopped verifying.

So a generation is (Resolve id, rows digest): the id names the object,
the digest names what the object holds. Two rules follow, and both
are VC-009:

- the archive file is named by the GENERATION, so exporting a changed
  generation never destroys the archive of the generation before it;
- an archive that already holds this generation is REUSED, not
  exported again. A `.drt` export is a full reconstruction of the
  whole timeline - the expensive half of creating a version - and
  re-exporting an unchanged generation buys nothing. The reuse is
  allowed only after the SAME read-back the export path gets
  (`verify_archive`), so the archive that is kept is proven to hold
  this generation and to carry its media.

`library/tools/versions/reel_versions.py` (the receipts and the
selector), `library/tools/versions/store.py` (the export itself),
`library/tools/versions/overhead.py` (what a version costs, per edit
type).
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


def generation_suffix(timeline_id: str, rows_digest: str = "") -> str:
    """The suffix that makes one generation's archive ITS OWN file.

    A Resolve id is unique per timeline OBJECT, and a timeline object
    holds several GENERATIONS over time (the captain's hand edit, a
    rebuild, a touch). So `gen-<id>` alone is not a generation's name:
    a second generation of the same object exported to it OVERWROTE
    the first one's archive, and the receipt that named it stopped
    verifying.

    With the rows digest the suffix names the generation: the id says
    which object, the digest says what it holds. The same generation
    always lands on the same file (so it is exported once), and a
    different generation never lands on it (so it cannot destroy the
    archive of the generation before it).

    The digest is optional because a caller that does not know the
    rows yet still needs a per-object name - and the export path
    verifies the archive against the live generation afterwards, so a
    name that is too coarse is caught rather than trusted.
    """
    safe = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in str(timeline_id or "")) or "unknown"
    digest = "".join(
        char for char in str(rows_digest or "") if char.isalnum())[:12]
    if digest:
        return f"gen-{safe}-{digest}"
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
                       label: str = "", suffix: str | None = None,
                       rows_digest: str = "") -> dict:
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
        archive_suffix=suffix or generation_suffix(timeline_id, rows_digest))
    if not exported.get("archived"):
        raise ReplacedGenerationArchiveRefused(
            "the recovery archive could not be exported "
            f"({exported.get('reason', 'unknown error')})")
    return verify_archive(archive_path=exported["path"], project=project,
                          timeline=timeline, project_folder=project_folder)


def live_rows_digest(timeline, name: str = "") -> str:
    """The digest of the rows the live timeline holds right now.

    A generation is (Resolve id, rows digest), and this is the half
    that says what the timeline object holds. It is read through the
    one row projection the replace guard uses, so the digest that
    names a generation and the guard's own rows cannot disagree.

    An unreadable timeline reads as NO digest: a generation nobody can
    name is never reused (fail-closed), and the export path still
    proves the archive against the live timeline.
    """
    from library.tools import reel_replace_guard
    from library.tools.versions import rounds

    try:
        rows = reel_replace_guard.snapshot_timeline(
            timeline, str(name or timeline.GetName() or ""),
            side="retiring")
    except Exception:  # noqa: BLE001 - an unreadable timeline names nothing
        return ""
    return rounds.digest_rows(rows)


def preservation_digest(preservation) -> str:
    """The digest of a generation's LOOK: its grade, comps and composite.

    The rows say what a generation plays; the preservation says what it
    looks like. A captain's hand edit that changes only the grade keeps
    every row, so the rows alone cannot name that generation - and an
    archive of the generation BEFORE the grade would be reused as if it
    were this one, and a restore would bring back the wrong look.

    An absent preservation reads as NO digest: a generation whose look
    nobody can name is never reused.
    """
    if not preservation:
        return ""
    payload = json.dumps(preservation, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def live_preservation_digest(timeline) -> str:
    """The digest of the live generation's look, right now.

    Read through the one preservation projection the replace guard
    uses, so the digest that names a generation and the guard's own
    preservation record cannot disagree.

    An unreadable timeline reads as NO digest: fail-closed.
    """
    from library.tools import reel_read, reel_replace_guard

    try:
        tracks = reel_read.read_tracks(timeline)
        items = reel_replace_guard.snapshot_items(tracks)
        return preservation_digest(
            reel_replace_guard.preservation_record(items))
    except Exception:  # noqa: BLE001 - an unreadable timeline names nothing
        return ""


def reusable_archive(*, project_folder: str, final: str, timeline_id: str,
                     rows_digest: str, preservation_digest: str = "",
                     media_paths: object = None) -> dict | None:
    """The receipt of an archive that ALREADY holds this generation.

    A generation is (Resolve id, rows digest). An archive whose
    receipt names both, whose file still hashes true, and whose media
    manifest is the media the generation plays now, holds THIS
    generation - so it is reused instead of exported again.

    The search is over the WHOLE ledger of the reel, not only its
    newest version: the archive of a generation is often attached to
    the version that produced it, and the version being replaced may
    be a LATER row for the same generation.

    Fail-closed, every step:

    - no digest, no reuse: a generation nobody can name is not reused;
    - a receipt without the media manifest is not reused - the export
      path always produces one, and a reuse must not hand a reader
      LESS evidence than the export would have;
    - a file that no longer hashes true is not reused;
    - a receipt whose media set differs from the live one is not
      reused: the generation moved.

    `media_paths` is the live generation's media, when the caller has
    it. Without it the media set is not compared here - the caller
    that reuses goes on to prove the archive against the live
    generation anyway.
    """
    from library.tools.versions import reel_versions

    if not rows_digest:
        return None
    wanted = {os.path.normcase(os.path.realpath(str(path)))
              for path in (media_paths or ())}
    for entry in reversed(reel_versions.versions_of(project_folder, final)):
        for receipt in reel_versions.recovery_archives_of(entry):
            normalized = reel_versions.read_recovery_receipt(receipt)
            if str(normalized["timeline_id"] or "") != str(timeline_id or ""):
                continue
            if str(normalized["rows_digest"] or "") != str(rows_digest):
                continue
            if not normalized["has_asset_manifest"]:
                continue
            if not preservation_digest:
                # The caller cannot state the generation's look, so the
                # archive on disk cannot be proven to hold THIS
                # generation: a hand edit that changed only the grade
                # would be reused as if it had not happened.
                return None
            if _receipt_look_digest(receipt) != preservation_digest:
                continue
            if wanted:
                held = {os.path.normcase(os.path.realpath(
                    str(asset.get("path") or "")))
                    for asset in normalized["assets"]}
                if held != wanted:
                    continue
            if not reel_versions.archive_verifies(receipt):
                continue
            return dict(receipt)
    return None


def _receipt_look_digest(receipt: Mapping) -> str:
    """The look digest a receipt carries, from either shape it may hold.

    A receipt written now carries `preservation_digest`; one written
    before it carries the `preservation` record itself. Either way the
    digest is the same number, so a reader that has both reads them as
    the same generation.
    """
    stated = str(receipt.get("preservation_digest") or "")
    if stated:
        return stated
    return preservation_digest(receipt.get("preservation"))


def ensure_archived(*, resolve, project, timeline, project_folder: str,
                    entry: Mapping | None, label: str = "",
                    rows_digest: str = "") -> dict:
    """Make sure the generation being replaced HAS a verified archive.

    `entry` is the version record of the generation being replaced, or
    None when the generation was never recorded as a version. A version
    that already carries an archive which still verifies keeps it - a
    generation is exported ONCE, and a second copy of the same
    generation is a second thing to keep true.

    `rows_digest` is the digest of the rows the generation holds now.
    With it, an archive of ANOTHER version that already holds THIS
    generation is reused instead of exported again (VC-009): the
    export is a full reconstruction of the whole timeline, and an
    unchanged generation does not need a second one. The reused
    archive is proved against the live generation by the same
    read-back the export path gets, so the archive that is kept is
    proven to hold this generation and to carry its media.

    Returns `{"status", "receipt", "reason"}`:

    - `verified`   the version already carried a verified archive;
    - `reused`     an archive of this generation already existed and
                   was proved against the live generation;
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
    if rows_digest:
        # The look is read ONLY when there is a candidate to check: a
        # generation nobody has archived yet pays nothing for the
        # reuse, and the export path reads the timeline anyway.
        look = live_preservation_digest(timeline)
        reusable = reusable_archive(
            project_folder=project_folder,
            final=str(timeline.GetName() or ""),
            timeline_id=str(timeline.GetUniqueId() or ""),
            rows_digest=rows_digest, preservation_digest=look)
        if reusable is not None:
            archive_path = str(reusable.get("archive_path")
                               or reusable.get("path") or "")
            try:
                proved = verify_archive(
                    archive_path=archive_path, project=project,
                    timeline=timeline, project_folder=project_folder)
            except ReplacedGenerationArchiveRefused:
                # The archive on disk does not hold this generation
                # after all: it is not reused, and the export below
                # writes the generation's own file.
                reusable = None
            else:
                return {"status": "reused", "receipt": proved,
                        "reason": ""}
    try:
        receipt = archive_generation(
            resolve=resolve, project=project, timeline=timeline,
            project_folder=project_folder, label=label,
            rows_digest=rows_digest)
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
    "live_rows_digest",
    "read_archive_receipt",
    "reusable_archive",
    "verify_archive",
]
