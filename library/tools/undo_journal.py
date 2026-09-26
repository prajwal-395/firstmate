"""UNDO: a touch is reversed IN PLACE from its journal; a rebuild is rolled back to its version.

The captain, 2026-09-23 (D5): *"Both: in-place for touches, versions for
rebuilds"*. Before this module a touch-up left a live `(archived round
NNN)` copy of the pre-touch timeline in his project as its only way
back, promotion and `--delete-files` had no way back at all, and the
stated recovery was "check out the data and rebuild".

One verb, two mechanisms
------------------------
`ren undo <project> [reel]` reverses the newest act nothing has reversed
yet, read off the reel version ledger (`versions.reel_versions`):

- a TOUCH (`reel_touchup.apply_touchup`, one reel or `--all-reels`) is
  reversed IN PLACE on the same timeline from its journal entry, below.
  An `--all-reels` touch is one act across reels: its entries share a
  `batch` and are reversed together.
- a REBUILD is rolled back to the version before it: that version's
  plan moment is restored into `reel_proposals_v2.json`, the reel is
  rebuilt through the reels process, the touches that stood on the
  earlier version are re-applied from their journals, and the result is
  compared with the version it was meant to reach. This is the one
  restore route the 2026-09-10 ruling allows (the Resolve project is
  derived - declarations plus a rebuild, never a restored binary), and
  it is D7's "binaries recorded by hash and regenerated".

The journal entry
-----------------
Written BEFORE the touch changes anything, under
`pipeline_output/review/undo/<entry id>/` - on the store's allow-list,
text only (JSON and exported `.comp` Lua), so it is versioned with the
project and a lean retention policy never has to guess which binary it
needs:

    entry.json
      format        "undo_journal/1"
      id, reel, final, resolve_project, batch, spec, gate_class
      status        open -> applied | failed -> undone | undo_failed
      before        {"tracks": reel_read.read_tracks(approved timeline)}
                    - every item's media, span, row, transform, comp
                    windows, clip colour and markers, read with the
                    timeline CURRENT so the transform is not
                    cursor-scaled (`reel_read.read_tracks`)
      removed       per item the touch deletes without re-placing it
                    (`remove_overlay`, the old side of `swap_pixels`):
                    its `composed_edit.capture_item` - properties,
                    geometry, node count and its comps exported to
                    `comps/` with their media windows
      fusion_manifest  a copy of the recorded manifest, when the touch
                    changes a played length (the undo changes it back
                    and needs the same route to the comp pass)
      after         {"tracks": ...} of the promoted timeline, written
                    before the replaced generation is deleted
      version       the reel version the touch produced
      undo          the undo's receipt, once reversed

Why the live copy can go
------------------------
`reel_touchup._promote` used to RETIRE the replaced generation into
`05 - Reels/Archive` as `... (archived round NNN)`. With `before`,
`removed` and `after` on disk, that copy is no longer the only way
back, so a touch now DELETES it (`reel_retirement.delete_backups`, the
same bounded delete promotion uses) - once `after` is written, never
before. A reel carrying a sign-off still retires, exactly as promotion
does: the captain approved that cut. The one state the journal cannot
record - a colour grade on an item the touch deletes outright - is
refused at the touch (`reel_touchup`: a graded remove, like a graded
swap, needs a rebuild), so nothing the delete takes is unrecorded.

How the undo runs, in place
---------------------------
1. REFUSE by name when the live timeline no longer reads as `after` -
   the projection below, row by row. Something moved it since the
   touch (the captain's hand, a rebuild, another touch), and reversing
   the touch over it would destroy that work.
2. Plan the inverse by DIFF, not per op: items only `after` has are
   the touch's; items only `before` has are what it took. A pair on
   one row playing the same file from the same source offset is one
   item the touch moved or retimed - re-placed at its old span through
   `composed_edit.apply_composed_edit`, with its grade copied from a
   transient reference duplicate of the live timeline (a re-placed item
   comes back with one colour node). An unpaired `after` item is
   deleted; an unpaired `before` item is re-placed from its `removed`
   capture and restored by `composed_edit.restore_item` - its own comps,
   at its own played length, so the comp is exact rather than stale. A
   transform or comp the touch changed on an item it did not move is
   written back in place.
3. Carry the captain's clip markers off every re-placed item.
4. VERIFY by re-reading: the live timeline must read as `before`, or
   the undo raises and the reference duplicate stays standing, named,
   holding the post-touch state.
5. Delete the reference duplicate, mark the journal `undone`, append an
   `undo` version and close the carried signature.

The projection
--------------
What "reads as" compares, per row in record order: span, source offset,
source file, name, the transform (every key `GetProperty()` returns
except the read-only ones), each comp's media-window frames, and clip
colour. Not compared: unique ids (a re-placed item is a new object),
markers (carried, and a note typed after the touch must not block its
undo), CDL and flags (a touch never carried either, so the journal
would claim a restore nothing performs).

`tests/test_undo_journal.py`.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from library.tools import composed_edit as _ce
from library.tools.ren_refusal import RenRefusal

JOURNAL_FORMAT = "undo_journal/1"
JOURNAL_DIRNAME = "undo"
ENTRY_FILENAME = "entry.json"

STATUS_OPEN = "open"
STATUS_APPLIED = "applied"
STATUS_FAILED = "failed"
STATUS_UNDONE = "undone"
STATUS_UNDO_FAILED = "undo_failed"


class UndoRefused(RenRefusal):
    """An undo that declined before changing anything. Always by name."""


class TimelineMovedSinceTouch(UndoRefused):
    """The live timeline no longer reads as the touch left it."""


class UndoNotVerified(RuntimeError):
    """An undo that ran and does not read back as the prior state."""


class RollbackDiverged(RuntimeError):
    """A rolled-back rebuild that does not read as the version it targeted."""


# ── Where entries live ───────────────────────────────────────────


def journal_root(project_folder) -> str:
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        JOURNAL_DIRNAME)


def entry_dir(project_folder, entry_id: str) -> str:
    return os.path.join(journal_root(project_folder), entry_id)


def _entry_path(project_folder, entry_id: str) -> str:
    return os.path.join(entry_dir(project_folder, entry_id), ENTRY_FILENAME)


def _now() -> str:
    return _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40]


def new_entry_id(final: str) -> str:
    stamp = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_slug(final)}-{uuid.uuid4().hex[:6]}"


def new_batch_id() -> str:
    return "batch-" + uuid.uuid4().hex[:10]


def read_entry(project_folder, entry_id: str) -> dict:
    path = _entry_path(project_folder, entry_id)
    try:
        with open(path, encoding="utf-8") as handle:
            entry = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise UndoRefused(
            f"undo journal entry {entry_id!r} could not be read at {path}",
            f"({unreadable})",
            "recover the journal entry from backup, or list what is "
            "left with `ren undo <project> --list`") from unreadable
    if entry.get("format") != JOURNAL_FORMAT:
        raise UndoRefused(
            f"{path} is {entry.get('format')!r}, not {JOURNAL_FORMAT!r}",
            "an entry in a foreign format cannot be trusted",
            "recover the journal entry from backup, or list what is "
            "left with `ren undo <project> --list`")
    return entry


def write_entry(project_folder, entry: Mapping) -> str:
    from library.tools.versions import store

    return store.write_record(_entry_path(project_folder, entry["id"]),
                              entry, prefix=".undo-")


def list_entries(project_folder) -> list:
    """Every entry, oldest first."""
    root = journal_root(project_folder)
    if not os.path.isdir(root):
        return []
    entries = []
    for name in sorted(os.listdir(root)):
        if os.path.isfile(os.path.join(root, name, ENTRY_FILENAME)):
            entries.append(read_entry(project_folder, name))
    return entries


# ── Capture: the touch's side ────────────────────────────────────


def _identity_change(detail: Mapping) -> _ce.ItemChange:
    """An `ItemChange` that moves nothing: the capture's own span."""
    return _ce.ItemChange(
        track_type=detail["track_type"], track_index=int(detail["track_index"]),
        item_index=0, record_frame=int(detail["record_in"]),
        duration=int(detail["duration"]),
        left_offset=_source_trim(detail, "undo capture"),
        previous_record=int(detail["record_in"]),
        previous_duration=int(detail["duration"]), how="journal",
        comp_count=int((detail.get("fusion") or {}).get("comp_count") or 0),
        name=str(detail.get("name") or ""))


def open_entry(project_folder, *, final: str, reel: int,
               resolve_project: str, spec: Mapping, gate_class: str,
               source_timeline: Any, removals: Sequence[Mapping],
               fusion_manifest: Mapping | None = None,
               batch: str = "") -> dict:
    """Write the journal entry BEFORE the touch changes anything.

    Reads the approved timeline whole and captures every item the plan
    deletes without re-placing. Raises `UndoRefused` - and the touch
    must then refuse - when a removed item carries a colour grade, the
    one state no script can record (`reel_touchup` refuses it first,
    by the same rule as a graded swap).
    """
    from library.tools import reel_read

    entry_id = new_entry_id(final)
    root = entry_dir(project_folder, entry_id)
    comps_dir = os.path.join(root, "comps")
    before = reel_read.read_tracks(source_timeline)
    live_rows = _ce._rows_of(source_timeline)
    removed = []
    for target in removals or ():
        row = str(target["row"]).upper()
        frame = int(target["record_frame"])
        hits = [item for item in (live_rows.get(row) or [])
                if _ce._read(item, "GetStart", None) == frame]
        if len(hits) != 1:
            raise UndoRefused(
                f"the plan removes {row}@{frame} and the "
                f"approved timeline holds {len(hits)} item(s) there",
                "so the journal cannot record what the touch would take",
                "restate the touch against the live timeline, then "
                "re-run `ren touch`")
        item = hits[0]
        detail = _detail_at(before, row, frame)
        capture = _ce.capture_item(item, _identity_change(detail),
                                   comps_dir,
                                   os.path.join(root, "withheld"))
        if capture.node_count is not None and int(capture.node_count) > 1:
            raise UndoRefused(
                f"{row}@{frame} carries a colour grade "
                f"({capture.node_count} nodes)",
                "no script can record a grade, so removing it could not "
                "be undone",
                "rebuild the reel (`ren build <project>`) - a graded "
                "removal needs a rebuild, not a touch-up")
        removed.append({
            "row": row, "record_frame": frame,
            "duration": int(detail["duration"]),
            "left_offset": _source_trim(detail, "undo capture"),
            "source_file": detail.get("source_file") or "",
            "name": detail.get("name") or "",
            "clip_color": detail.get("clip_color") or "",
            "properties": dict(capture.properties),
            "node_count": capture.node_count,
            "comps": [{"index": comp.index,
                       "path": os.path.relpath(comp.path, root),
                       "window": comp.window}
                      for comp in capture.restorable_comps],
        })
    entry = {
        "format": JOURNAL_FORMAT,
        "id": entry_id,
        "reel": int(reel),
        "final": final,
        "resolve_project": resolve_project,
        "batch": batch or "",
        "spec": json.loads(json.dumps(dict(spec or {}), default=str)),
        "gate_class": gate_class,
        "opened_at": _now(),
        "status": STATUS_OPEN,
        "before": {"tracks": before},
        "removed": removed,
    }
    if fusion_manifest is not None:
        entry["fusion_manifest"] = "fusion_manifest.json"
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "fusion_manifest.json"), "w",
                  encoding="utf-8") as handle:
            json.dump(fusion_manifest, handle, indent=2, sort_keys=True)
    write_entry(project_folder, entry)
    return entry


def close_entry(project_folder, entry: dict, *, after_timeline: Any,
                rows: Mapping) -> dict:
    """Record what the touch left, and the version it produced.

    Called on the promoted timeline BEFORE the replaced generation is
    deleted: until this returns, the retired copy is the only way back.
    """
    from library.tools import reel_read
    from library.tools.versions import reel_versions

    entry["after"] = {"tracks": reel_read.read_tracks(after_timeline)}
    version = reel_versions.record(
        project_folder, entry["final"], kind=reel_versions.KIND_TOUCH,
        rows=rows, journal=entry["id"], batch=entry.get("batch") or None)
    entry["version"] = version["version"]
    entry["status"] = STATUS_APPLIED
    entry["applied_at"] = _now()
    write_entry(project_folder, entry)
    return entry


def fail_entry(project_folder, entry: dict, why: str) -> None:
    """A touch that did not land: nothing to undo, and the entry says so."""
    entry["status"] = STATUS_FAILED
    entry["failed"] = why
    write_entry(project_folder, entry)


# ── The projection, and what differs ─────────────────────────────


def _row(detail: Mapping) -> str:
    return _ce.row_label(detail["track_type"], int(detail["track_index"]))


def _source_trim(detail: Mapping, context: str) -> int:
    value = detail.get("left_offset")
    if value is None:
        raise UndoRefused(
            f"{context} cannot preserve {_row(detail)} item's source trim "
            "because `left_offset` was unreadable",
            "undo would re-place it at frame 0 and expose any "
            "transparent preroll",
            "re-read the reel so the item's source trim is known, then "
            "re-run `ren undo`")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise UndoRefused(
            f"{context} cannot preserve {_row(detail)} item's invalid "
            f"source trim {value!r}",
            "undo needs an integer source frame",
            "re-read the reel so the item's source trim is known, then "
            "re-run `ren undo`") from exc


def _transform(detail: Mapping) -> dict:
    return {key: value for key, value in
            sorted((detail.get("transform") or {}).items())
            if key not in _ce.READ_ONLY_PROPERTIES}


def _windows(detail: Mapping) -> list:
    return [_ce._frames_of(entry.get("window")) for entry in
            ((detail.get("fusion") or {}).get("media_windows") or ())]


def _key(detail: Mapping) -> tuple:
    """Which item this is: row, span, source offset, file, name."""
    return (_row(detail), int(detail["record_in"]), int(detail["duration"]),
            _source_trim(detail, "undo comparison"),
            str(detail.get("source_file") or ""),
            str(detail.get("name") or ""))


def item_projection(detail: Mapping) -> dict:
    row, start, duration, left, source, name = _key(detail)
    return {"row": row, "start": start, "duration": duration,
            "left_offset": left, "source_file": source, "name": name,
            "transform": _transform(detail),
            "comps": int((detail.get("fusion") or {}).get("comp_count")
                         or 0),
            "windows": _windows(detail),
            "clip_color": str(detail.get("clip_color") or ""),
            "enabled": bool(detail.get("enabled", True))}


def _details(tracks: Sequence[Mapping]) -> list:
    return [clip for track in tracks or () for clip in
            (track.get("clips") or ())]


def projection(tracks: Sequence[Mapping]) -> dict:
    """`{row: [item projection, ...]}` in record order."""
    rows: dict = {}
    for detail in _details(tracks):
        rows.setdefault(_row(detail), []).append(item_projection(detail))
    for items in rows.values():
        items.sort(key=lambda item: (item["start"], item["source_file"]))
    return rows


def projection_diff(expected: Mapping, found: Mapping) -> list:
    """Every row where two projections disagree, said per item."""
    out = []
    for row in sorted(set(expected) | set(found)):
        want = list(expected.get(row) or ())
        got = list(found.get(row) or ())
        if want == got:
            continue
        missing = [item for item in want if item not in got]
        extra = [item for item in got if item not in want]
        out.append({"row": row,
                    "expected": [_say(item) for item in missing],
                    "found": [_say(item) for item in extra]})
    return out


def _say(item: Mapping) -> str:
    return (f"{item['name'] or '(unnamed)'} @{item['start']}"
            f"+{item['duration']} from {item['left_offset']}")


def _detail_at(tracks: Sequence[Mapping], row: str, frame: int) -> dict:
    hits = [detail for detail in _details(tracks)
            if _row(detail) == row and int(detail["record_in"]) == frame]
    if len(hits) != 1:
        raise UndoRefused(
            f"{row}@{frame} names {len(hits)} item(s) in the "
            f"journal's read",
            "so it cannot say what that item was",
            "re-read the reel (`ren drift <project>`) and restate the "
            "touch, then re-run `ren touch`")
    return hits[0]


# ── The inverse, as a pure plan ──────────────────────────────────


@dataclass
class InversePlan:
    #: `(after detail, before detail)`: one item the touch moved or
    #: retimed, re-placed at its old span.
    restores: list = field(default_factory=list)
    #: after details: items the touch placed, deleted by the undo.
    deletions: list = field(default_factory=list)
    #: before details: items the touch took, re-placed from `removed`.
    reinserts: list = field(default_factory=list)
    #: `(after detail, before detail)`: same item, transform or comps
    #: changed in place.
    in_place: list = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.restores or self.deletions or self.reinserts
                    or self.in_place)


def plan_inverse(before: Sequence[Mapping], after: Sequence[Mapping]) -> InversePlan:
    """What the undo must do to turn `after` back into `before`.

    Planned from the two reads alone, never from the touch's op list:
    a plan per op would have to know every op, and an op added later
    would be undone by nothing. A before/after item that cannot be
    paired uniquely REFUSES rather than guessing which went where.
    """
    plan = InversePlan()
    before_details = list(_details(before))
    after_details = list(_details(after))
    after_by_key: dict = {}
    for detail in after_details:
        after_by_key.setdefault(_key(detail), []).append(detail)
    before_only = []
    for detail in before_details:
        twins = after_by_key.get(_key(detail)) or []
        if twins:
            twin = twins.pop(0)
            if item_projection(twin) != item_projection(detail):
                plan.in_place.append((twin, detail))
        else:
            before_only.append(detail)
    after_only = [detail for twins in after_by_key.values()
                  for detail in twins]

    def pairing(detail):
        return (_row(detail), str(detail.get("source_file") or ""),
                _source_trim(detail, "undo comparison"),
                str(detail.get("name") or ""))

    unpaired_after = list(after_only)
    for detail in before_only:
        candidates = [other for other in unpaired_after
                      if pairing(other) == pairing(detail)]
        if len(candidates) > 1:
            raise UndoRefused(
                f"{len(candidates)} items the touch placed on "
                f"{_row(detail)} play {detail.get('name')!r} from source "
                f"frame {detail.get('left_offset')}",
                f"so which one was "
                f"{_row(detail)}@{detail['record_in']} cannot be said. "
                f"Nothing was changed",
                "re-run `ren undo` - if it refuses again the journal "
                "cannot reverse this touch; rebuild the reel instead")
        if candidates:
            unpaired_after.remove(candidates[0])
            plan.restores.append((candidates[0], detail))
        else:
            plan.reinserts.append(detail)
    plan.deletions = unpaired_after
    return plan


# ── The undo, on live handles ────────────────────────────────────


def _rows_of(timeline) -> dict:
    return _ce._rows_of(timeline)


def _at(rows: Mapping, row: str, frame: int):
    hits = [item for item in (rows.get(row) or [])
            if _ce._read(item, "GetStart", None) == int(frame)]
    return hits[0] if len(hits) == 1 else None


def _change_for(after: Mapping, before: Mapping, index: int) -> _ce.ItemChange:
    return _ce.ItemChange(
        track_type=after["track_type"], track_index=int(after["track_index"]),
        item_index=int(index), record_frame=int(before["record_in"]),
        duration=int(before["duration"]),
        left_offset=_source_trim(before, "undo placement"),
        previous_record=int(after["record_in"]),
        previous_duration=int(after["duration"]), how="undo",
        comp_count=int((after.get("fusion") or {}).get("comp_count") or 0),
        name=str(after.get("name") or ""))


def _index_of(rows: Mapping, detail: Mapping) -> int:
    items = rows.get(_row(detail)) or []
    for index, item in enumerate(items):
        if _ce._read(item, "GetStart", None) == int(detail["record_in"]):
            return index
    raise UndoRefused(
        f"{_row(detail)}@{detail['record_in']} is not on the "
        f"live timeline",
        "so the undo cannot address it",
        "the timeline moved since the touch - re-run `ren undo`; if it "
        "still refuses, the touch cannot be undone in place")


def undo_in_place(*, timeline, media_pool, entry: Mapping, entry_root: str,
                  reference, rederiver: _ce.CompRederiver,
                  resolve_media: Callable[[str], Any],
                  work_dir: str) -> dict:
    """Reverse one journaled touch on `timeline` itself. Returns a receipt.

    `reference` is a duplicate of `timeline` taken before this call -
    the post-touch state - read only for colour grades. `resolve_media`
    maps a source file path to a live media pool item.
    """
    from library.tools import marker_carry, reel_read

    receipt: dict = {"entry": entry["id"]}
    before = entry["before"]["tracks"]
    after = entry["after"]["tracks"]

    # 1. The timeline must read as the touch left it.
    live = reel_read.read_tracks(timeline)
    moved = projection_diff(projection(after), projection(live))
    if moved:
        raise TimelineMovedSinceTouch(
            f"{entry['final']!r} has changed since touch {entry['id']} "
            f"({', '.join(d['row'] for d in moved)}): {moved}",
            "undoing the touch over it would destroy that work - "
            "nothing was changed",
            "undo the later work first (`ren undo <project> --list` "
            "shows the acts, newest first), then undo this touch")

    # 2. The plan, and every refusal it can raise, before any write.
    plan = plan_inverse(before, after)
    receipt["plan"] = {
        "restores": [f"{_row(a)}@{a['record_in']}->{b['record_in']}"
                     for a, b in plan.restores],
        "deletions": [f"{_row(a)}@{a['record_in']}" for a in plan.deletions],
        "reinserts": [f"{_row(b)}@{b['record_in']}" for b in plan.reinserts],
        "in_place": [f"{_row(a)}@{a['record_in']}" for a, _b in plan.in_place],
    }
    for a, b in plan.in_place:
        had = int((b.get("fusion") or {}).get("comp_count") or 0)
        has = int((a.get("fusion") or {}).get("comp_count") or 0)
        if has < had:
            raise UndoRefused(
                f"{_row(a)}@{a['record_in']} carries {has} "
                f"comp(s) where it carried {had}",
                "a touch never removes a comp in place, so the journal "
                "cannot reverse it. Nothing was changed",
                "rebuild the reel (`ren build <project>`) instead of "
                "undoing in place")
    if plan.empty:
        raise UndoRefused(
            f"touch {entry['id']} left {entry['final']!r} "
            f"reading exactly as before it",
            "there is nothing to undo",
            "pick another entry (`ren undo <project> --list`), or leave "
            "the reel as it is")
    removed = {(str(r["row"]), int(r["record_frame"])): r
               for r in entry.get("removed") or ()}
    insertions, reinsert_captures = [], []
    for detail in plan.reinserts:
        capture = removed.get((_row(detail), int(detail["record_in"])))
        if capture is None:
            raise UndoRefused(
                f"the touch took {_row(detail)}@"
                f"{detail['record_in']} and the journal holds no capture "
                f"of it",
                "so it cannot be put back. Nothing was changed",
                "rebuild the reel (`ren build <project>`) - this touch "
                "cannot be undone in place")
        mpi = resolve_media(capture["source_file"])
        if mpi is None:
            raise UndoRefused(
                f"{capture['source_file']!r} is not in the media pool",
                f"so {_row(detail)}@{detail['record_in']} cannot be "
                f"re-placed. Nothing was changed",
                "re-import the source file into the media pool, then "
                "re-run `ren undo`")
        insertions.append(_ce.Insertion(
            track_type=detail["track_type"],
            track_index=int(detail["track_index"]), media_pool_item=mpi,
            left_offset=int(capture["left_offset"]),
            duration=int(capture["duration"]),
            record_frame=int(capture["record_frame"]),
            name=capture.get("name") or "",
            properties=dict(capture.get("properties") or {})))
        reinsert_captures.append((detail, capture, mpi))
    rows = _rows_of(timeline)
    provisional = [_change_for(a, b, _index_of(rows, a))
                   for a, b in plan.restores]
    _ce.assert_rederivation_reachable(provisional, rederiver, insertions)
    reference_rows = _rows_of(reference)
    grade_by_frame = {}
    for a, _b in plan.restores:
        source = _at(reference_rows, _row(a), a["record_in"])
        if source is None:
            raise UndoRefused(
                f"the reference copy holds no single item at "
                f"{_row(a)}@{a['record_in']} to carry its grade from",
                "without the reference there is no grade to restore. "
                "Nothing was changed",
                "rebuild the reel (`ren build <project>`) - this touch "
                "cannot be undone in place")
        grade_by_frame[(_row(a), int(a["record_in"]))] = source

    # 3. The captain's clip markers on everything about to be re-placed.
    replaced = {(a["track_type"], int(a["track_index"]), int(a["record_in"]))
                for a, _b in plan.restores} | {
        (a["track_type"], int(a["track_index"]), int(a["record_in"]))
        for a in plan.deletions}
    notes = [marker for marker in marker_carry.read_clip_markers(timeline)
             if marker.get("anchor") and (
                 marker["anchor"]["track_type"],
                 int(marker["anchor"]["track_index"]),
                 int(marker["anchor"]["timeline_start"])) in replaced]

    # Everything from here writes. A refusal past this line is no
    # longer "nothing was changed", so it is raised as a failed undo.
    try:
        _write_inverse(timeline, media_pool, entry_root, plan,
                       rows, insertions, reinsert_captures,
                       grade_by_frame, rederiver, work_dir, receipt)
    except UndoRefused as refused:
        raise UndoNotVerified(str(refused)) from refused

    # Rows the touch ADDED (`add_row`) go with it, once emptied - the
    # item projection has no row for an empty track, so it cannot see one.
    before_rows = sum(1 for t in before
                      if str(t.get("type", "")).lower().startswith("v"))
    dropped_rows = []
    while int(timeline.GetTrackCount("video") or 0) > before_rows:
        index = int(timeline.GetTrackCount("video"))
        if timeline.GetItemListInTrack("video", index):
            break
        name = timeline.GetTrackName("video", index)
        if not timeline.DeleteTrack("video", index):
            break
        dropped_rows.append(f"V{index} {name}")
    receipt["rows_removed"] = dropped_rows

    # 7. The notes, carried by source frame onto what now plays them.
    carried, uncarried = marker_carry.plan_clip_carry(notes, timeline,
                                                      entry["final"])
    declined = (marker_carry.place_clip_markers(timeline, carried)
                if carried else [])
    if uncarried or declined:
        marker_carry.report_clip(entry["final"], carried,
                                 list(uncarried) + list(declined))
    receipt["markers"] = {"carried": len(carried) - len(declined),
                          "uncarried": [m.get("note", "") for m in
                                        list(uncarried) + list(declined)]}

    # 8. The verdict is the read, never a return value.
    restored = reel_read.read_tracks(timeline)
    wrong = projection_diff(projection(before), projection(restored))
    if wrong:
        raise UndoNotVerified(
            f"{entry['final']!r} does not read as it did before touch "
            f"{entry['id']}: {wrong}")
    receipt["verified"] = True
    return receipt


def _write_inverse(timeline, media_pool, entry_root, plan, rows,
                   insertions, reinsert_captures, grade_by_frame,
                   rederiver, work_dir, receipt) -> None:
    """Steps 4-6 of the undo: every write, in the one order that works."""
    # 4. In place: transforms and comps the touch changed on items it
    # did not move.
    receipt["in_place"] = []
    for a, b in plan.in_place:
        item = _at(rows, _row(a), a["record_in"])
        receipt["in_place"].append(_revert_in_place(item, a, b))

    # 5. Delete what the touch placed, then re-place the rest.
    if plan.deletions:
        victims = [_at(rows, _row(a), a["record_in"]) for a in plan.deletions]
        timeline.DeleteClips(victims, False)
        receipt["deleted"] = len(victims)
    rows = _rows_of(timeline)
    changes = [_change_for(a, b, _index_of(rows, a))
               for a, b in plan.restores]
    grade_sources = {(c.row, c.item_index):
                     grade_by_frame[(c.row, c.previous_record)]
                     for c in changes}
    if changes or insertions:
        composed = _ce.apply_composed_edit(
            timeline=timeline, media_pool=media_pool, changes=changes,
            insertions=insertions,
            comp_dir=os.path.join(work_dir, "comps"),
            withheld_dir=os.path.join(work_dir, "withheld"),
            rederiver=rederiver, grade_sources=grade_sources,
            link_rows={}, picture_row="V1")
        receipt["composed"] = {"plan": composed.plan,
                               "verified": composed.verified,
                               "rederived": composed.rederived}

    # 6. What the composition cannot know: each item's own prior state.
    rows = _rows_of(timeline)
    for detail, capture, mpi in reinsert_captures:
        item = _at(rows, _row(detail), detail["record_in"])
        comps = tuple(_ce.CapturedComp(
            index=int(comp["index"]),
            path=os.path.join(entry_root, comp["path"]),
            window=comp.get("window")) for comp in capture.get("comps") or ())
        _ce.restore_item(item, _ce.ItemCapture(
            change=_identity_change(detail),
            properties=dict(capture.get("properties") or {}),
            geometry={}, media_pool_item=mpi, restorable_comps=comps))
    for _a, b in plan.restores:
        item = _at(rows, _row(b), b["record_in"])
        diff = _ce.set_properties(item, _transform(b))
        if diff:
            raise UndoNotVerified(
                f"{_row(b)}@{b['record_in']} did not take its prior "
                f"transform back: {diff}.")
    for detail in [b for _a, b in plan.restores] + list(plan.reinserts):
        colour = detail.get("clip_color") or ""
        item = _at(rows, _row(detail), detail["record_in"])
        if colour and _ce._read(item, "GetClipColor", "") != colour:
            item.SetClipColor(colour)


def _revert_in_place(item, after: Mapping, before: Mapping) -> dict:
    if item is None:
        raise UndoRefused(
            f"{_row(after)}@{after['record_in']} is not on the "
            f"live timeline to revert in place",
            "the timeline moved under the undo",
            "re-run `ren undo` against the live timeline; if it still "
            "refuses, the touch cannot be undone in place")
    out = {"row": _row(after), "record_frame": int(after["record_in"])}
    if _transform(after) != _transform(before):
        diff = _ce.set_properties(item, _transform(before))
        if diff:
            raise UndoNotVerified(
                f"{out['row']}@{out['record_frame']} did not take its "
                f"prior transform back: {diff}.")
        out["transform"] = True
    if bool(after.get("enabled", True)) != bool(before.get("enabled", True)):
        item.SetClipEnabled(bool(before.get("enabled", True)))
        if bool(item.GetClipEnabled()) != bool(before.get("enabled", True)):
            raise UndoNotVerified(
                f"{out['row']}@{out['record_frame']} did not switch back "
                f"{'on' if before.get('enabled', True) else 'off'}.")
        out["enabled"] = bool(before.get("enabled", True))
    had = int((before.get("fusion") or {}).get("comp_count") or 0)
    has = int(_ce._read(item, "GetFusionCompCount", 0) or 0)
    if has > had:
        # Comps a touch ADDED (`entry_motion`) sit after the ones the
        # item carried; they go, newest first, by the name Resolve reads.
        names = list(item.GetFusionCompNameList() or [])
        for name in reversed(names[had:]):
            item.DeleteFusionCompByName(name)
        out["comps_removed"] = has - had
    return out


# ── The orchestration: journal + Resolve ─────────────────────────


def _connect(resolve_name: str):
    from library.tools.reel_build import _connect_resolve_project
    return _connect_resolve_project(resolve_name)


def _resolve_name(project_folder: str) -> str:
    from library.tools.project_registry import get_project
    return get_project(project_folder).resolve.project_name


def undo_touch(project_folder: str, entry_id: str, *, connect=None,
               rederiver_override=None) -> dict:
    """Reverse one touch in place, under the lease, with a verified read."""
    from library.tools.resolve_lock import under_lease

    entry = read_entry(project_folder, entry_id)
    if entry.get("status") != STATUS_APPLIED:
        raise UndoRefused(
            f"touch {entry_id} is {entry.get('status')!r}, not applied",
            "there is nothing of it on the timeline to undo",
            "pick an applied entry (`ren undo <project> --list` shows "
            "them, newest first)")

    @under_lease(f"undo touch on {entry['final']}")
    def _guarded():
        return _undo_touch_connected(project_folder, entry,
                                     connect or _connect,
                                     rederiver_override)

    return _guarded()


def _undo_touch_connected(project_folder, entry, connect,
                          rederiver_override) -> dict:
    from library.tools import reel_retirement, reel_touchup
    from library.tools.reel_build import backup_name, timelines_to_replace
    from library.tools.reel_replace_guard import snapshot_timeline
    from library.tools.resolve_lock import cursor_fence
    from library.tools.versions import reel_versions

    final = entry["final"]
    project = connect(entry["resolve_project"])
    pool = project.GetMediaPool()
    found = {t.GetName(): t for t in timelines_to_replace(project, {final})}
    if final not in found:
        raise UndoRefused(
            f"no timeline called {final!r} is in Resolve project "
            f"{entry['resolve_project']!r}",
            "the touch cannot be undone on a timeline that is not there",
            "restore the timeline in Resolve (or rebuild the reel), then "
            "re-run `ren undo`")
    live = found[final]
    reference_name = backup_name(final)
    if timelines_to_replace(project, {reference_name}):
        raise UndoRefused(
            f"{reference_name!r} is already in the project",
            "left by an interrupted run",
            "clear it in Resolve before re-running `ren undo`")

    before = entry["before"]["tracks"]
    plan = plan_inverse(before, entry["after"]["tracks"])
    length_changes = any(int(a["duration"]) != int(b["duration"])
                         for a, b in plan.restores)
    root = entry_dir(project_folder, entry["id"])
    if rederiver_override is not None:
        rederiver = rederiver_override
    elif not length_changes:
        rederiver = reel_touchup._NullRederiver(
            "the undo changes no played length")
    else:
        manifest_file = entry.get("fusion_manifest")
        if not manifest_file:
            raise UndoRefused(
                f"undoing touch {entry['id']} changes a played length "
                f"and its journal holds no fusion manifest",
                "there is no route to the comp pass. Nothing was changed",
                "rebuild the reel (`ren build <project>`) - this touch "
                "cannot be undone in place")
        with open(os.path.join(root, manifest_file), encoding="utf-8") as fh:
            manifest = json.load(fh)
        reel_touchup.check_manifest_matches(manifest, before)
        rederiver = _ce.ReelLookRederiver(manifest, project_folder,
                                          entry["resolve_project"], final)
        # The pass maps specs to items by position along each row, so
        # it is told how many items the undo leaves there: `before`'s.
        rederiver.expected_row_counts = {
            row: len(items) for row, items in projection(before).items()}

    reference = live.DuplicateTimeline(reference_name)
    if reference is None or reference.GetName() != reference_name:
        raise UndoRefused(
            f"Resolve would not duplicate {final!r} as the undo's reference",
            "without the reference copy there is nothing to undo "
            "against - nothing was changed",
            "clear bin space in Resolve and re-run `ren undo`")
    from library.tools.project_layout import Area, ProjectLayout
    work_dir = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "undo", entry["id"])
    try:
        with cursor_fence(project, live, f"undo touch on {final}"):
            receipt = undo_in_place(
                timeline=live, media_pool=pool, entry=entry,
                entry_root=root, reference=reference, rederiver=rederiver,
                resolve_media=lambda path: reel_touchup.pool_item_for_path(
                    pool, path),
                work_dir=work_dir)
            rows = snapshot_timeline(live, final, side="staged")
    except UndoRefused:
        # Raised before the first write: the timeline is as the touch
        # left it, so the reference is only debris.
        reel_retirement.delete_backups(project, pool,
                                       {reference_name: reference})
        raise
    except Exception as failed:
        entry["status"] = STATUS_UNDO_FAILED
        entry["undo_failed"] = {"at": _now(), "why": repr(failed),
                                "reference": reference_name}
        write_entry(project_folder, entry)
        raise UndoNotVerified(
            f"the undo of {entry['id']} did not finish ({failed}). The "
            f"post-touch state is safe on {reference_name!r} and the "
            f"pre-touch state is in {_entry_path(project_folder, entry['id'])}.") \
            from failed
    receipt["reference_deleted"] = reel_retirement.delete_backups(
        project, pool, {reference_name: reference})["deleted"]
    version = reel_versions.record(project_folder, final,
                                   kind=reel_versions.KIND_UNDO, rows=rows,
                                   undoes=entry.get("version"),
                                   journal=entry["id"])
    if entry.get("version"):
        reel_versions.mark_undone(project_folder, final,
                                  int(entry["version"]),
                                  int(version["version"]))
    receipt["version"] = version["version"]
    receipt["signature_closed"] = reel_touchup.close_signature(
        project_folder, project, final)
    entry["status"] = STATUS_UNDONE
    entry["undo"] = {**receipt, "at": _now()}
    write_entry(project_folder, entry)
    return receipt


# ── Rolling a rebuild back ───────────────────────────────────────


def restore_plan_moment(project_folder: str, moment: Mapping) -> str:
    """Put one reel's recorded plan moment back into the live plan.

    Every other reel's moment is left byte-for-byte as it is; the plan
    as it stood is archived first (`plan_provenance.archive_plan`).
    """
    from library.tools.plan_provenance import archive_plan
    from library.tools.reel_proposal import proposal_path
    from library.tools.stable_json import dumps_stable

    path = str(proposal_path(project_folder))
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    moments = list(document.get("moments") or ())
    hits = [i for i, m in enumerate(moments)
            if int(m.get("number", -1)) == int(moment["number"])]
    if len(hits) != 1:
        raise UndoRefused(
            f"the plan holds {len(hits)} moment(s) numbered "
            f"{moment['number']}",
            "so the rollback cannot say which to restore. Nothing was "
            "changed",
            "fix the duplicated reel number in the proposal, then "
            "re-run `ren undo`")
    archive_plan(path)
    moments[hits[0]] = dict(moment)
    document["moments"] = moments
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(dumps_stable(document))
    return path


def _build_one_reel(project_folder: str, reel: int, supersede=()) -> int:
    """`manage_project.py build-reels --only-reel N`, in its OWN process.

    A build creates timelines and the touches re-applied after it
    import Fusion comps, and those two never share a process (AGENTS.md
    5). The build needs the ML interpreter (AGENTS.md 9), so it runs
    under the one `shared_environment` finds, or refuses naming why.
    """
    import subprocess
    from pathlib import Path

    from library.tools.shared_environment import python_interpreter

    repo = Path(__file__).resolve().parents[2]
    python, why_not = python_interpreter(str(repo))
    if not python:
        raise UndoRefused(
            "the rollback's rebuild needs the ML interpreter and none "
            "was found",
            f"{why_not}",
            "set up the ML environment (docs/ML_ENVIRONMENT.md), then "
            "re-run `ren undo`")
    argv = [python, str(repo / "manage_project.py"), "build-reels",
            str(project_folder), "--only-reel", str(int(reel))]
    for name in supersede or ():
        argv += ["--supersede", name]
    return subprocess.run(argv, cwd=str(repo), check=False).returncode


def rollback_rebuild(project_folder: str, final: str, act: Mapping, *,
                     read_live_rows: Callable[[str], Mapping],
                     build: Callable[[str, int], int] | None = None,
                     touch: Callable[[str, Mapping], Any] | None = None,
                     supersede=()) -> dict:
    """Roll a rebuild back to the version before it, and say if it got there.

    `read_live_rows(final)` reads the live rows; `build` and `touch` are
    the reels process and `reel_touchup.apply_touchup`, seams for tests.
    """
    from library.tools.versions import reel_versions, rounds

    history = reel_versions.versions_of(project_folder, final)
    newest = history[-1]
    live = dict(read_live_rows(final) or {})
    if rounds.digest_rows(live) != newest["rows_digest"]:
        raise TimelineMovedSinceTouch(
            f"{final!r} no longer reads as version {newest['version']} "
            f"({newest['kind']}) - it was changed outside Ren since",
            "rolling back would destroy that work; nothing was changed",
            "re-apply the outside change after the rollback, or leave "
            "the timeline as it is - `ren undo` will not destroy it")
    target = reel_versions.state_before(project_folder, final,
                                        int(act["version"]))
    base = reel_versions.build_before(project_folder, final,
                                      int(act["version"]))
    if target is None or base is None or not base.get("plan_moment"):
        raise UndoRefused(
            f"version {act['version']} of {final!r} is the first recorded "
            f"build with a plan behind it",
            "so there is no earlier version to roll back to",
            "there is nothing to roll back - leave the reel as it is")
    if base["plan_moment"] == act.get("plan_moment"):
        raise UndoRefused(
            f"versions {base['version']} and {act['version']} of {final!r} "
            f"were built from the same plan moment",
            "what changed between them is outside the plan, so a "
            "rollback would rebuild version "
            f"{act['version']} again",
            "change the plan moment first, then roll back - or leave the "
            "reel as it is")
    replays = [entry for entry in reel_versions.live_acts(project_folder,
                                                          final)
               if entry["kind"] == reel_versions.KIND_TOUCH
               and int(base["version"]) < int(entry["version"])
               < int(act["version"])]
    reel = int(base["plan_moment"]["number"])
    restore_plan_moment(project_folder, base["plan_moment"])
    reel_versions.set_pending_rollback(project_folder, final,
                                       int(act["version"]))
    code = (build or (lambda folder, number: _build_one_reel(
        folder, number, supersede)))(project_folder, reel)
    unplaced = reel_versions.take_pending_rollback(project_folder, final)
    if code or unplaced is not None:
        # The promotion consumes the marker; one still standing means
        # nothing was promoted, and it must not label a later build.
        raise UndoNotVerified(
            f"the rebuild for the rollback of {final!r} "
            f"{'refused (exit ' + str(code) + ')' if code else 'promoted nothing'}"
            f"; the plan now holds version {base['version']}'s moment "
            f"and the timeline was not replaced.")
    replayed = []
    for entry in replays:
        journal = read_entry(project_folder, entry["journal"])
        spec = dict(journal["spec"])
        (touch or _apply_touch)(project_folder, spec)
        replayed.append(entry["journal"])
    after = dict(read_live_rows(final) or {})
    if rounds.digest_rows(after) != target["rows_digest"]:
        diff = rounds.diff_reel(target["rows"], after)
        raise RollbackDiverged(
            f"{final!r} was rebuilt from version {base['version']}'s plan"
            f"{' and ' + str(len(replayed)) + ' touch(es) re-applied' if replayed else ''}"
            f", and it does not read as version {target['version']}: "
            f"{diff}. Inputs outside the plan changed since then; the "
            f"rollback is on the timeline and recorded, and this is what "
            f"it could not reproduce.")
    return {"final": final, "rolled_back": act["version"],
            "to": target["version"], "replayed": replayed}


def _apply_touch(project_folder: str, spec: Mapping):
    from library.tools import reel_touchup
    return reel_touchup.apply_touchup(project_folder, spec)


# ── `ren undo` ───────────────────────────────────────────────────


def undo_stack(project_folder, final: str = "") -> list:
    """The acts `ren undo` would reverse, newest first."""
    from library.tools.versions import reel_versions

    finals = ([final] if final
              else list(reel_versions.read(project_folder)["reels"]))
    acts = [(name, act) for name in finals
            for act in reel_versions.live_acts(project_folder, name)]
    return sorted(acts, key=lambda pair: (str(pair[1]["at"]),
                                          int(pair[1]["version"])),
                  reverse=True)


def undo(project_folder: str, *, final: str = "", entry_id: str = "",
         connect=None, read_live_rows=None, build=None, touch=None,
         supersede=()) -> list:
    """Reverse the newest act (on `final`, or anywhere), or a named touch.

    Returns one receipt per reel reversed. A named entry that is not
    its reel's newest act refuses: a later act stands on top of it.
    """
    from library.tools.versions import reel_versions

    if entry_id:
        entry = read_entry(project_folder, entry_id)
        newest = reel_versions.latest_act(project_folder, entry["final"])
        if newest is None or newest.get("journal") != entry_id:
            raise UndoRefused(
                f"{entry_id} is not the newest act on {entry['final']!r} "
                f"(that is version {(newest or {}).get('version')}, "
                f"{(newest or {}).get('kind')})",
                "a later act stands on top of it",
                "undo the later one first (`ren undo <project> --list` "
                "shows the order)")
        targets = [(entry["final"], newest)]
    else:
        stack = undo_stack(project_folder, final)
        if not stack:
            raise UndoRefused(
                f"nothing recorded on {final or 'this project'!r} is "
                f"left to undo",
                "every recorded act was already undone, or none was "
                "ever recorded",
                "there is nothing to undo - leave the reels as they are")
        name, act = stack[0]
        targets = [(name, act)]
        if act["kind"] == reel_versions.KIND_TOUCH and act.get("batch"):
            targets = [(other, other_act) for other, other_act
                       in undo_stack(project_folder)
                       if other_act.get("batch") == act["batch"]]
            for other, _act in targets:
                if reel_versions.latest_act(project_folder, other) != _act:
                    raise UndoRefused(
                        f"the all-reels touch {act['batch']} has a later "
                        f"act on {other!r} standing on it",
                        "an all-reels touch is undone whole, and a later "
                        "act stands in the way",
                        f"undo the later act on {other!r} first, then "
                        f"re-run `ren undo`")
    receipts = []
    for name, act in targets:
        if act["kind"] == reel_versions.KIND_TOUCH:
            receipts.append(undo_touch(project_folder, act["journal"],
                                       connect=connect))
        else:
            receipts.append(rollback_rebuild(
                project_folder, name, act,
                read_live_rows=read_live_rows or _live_rows_reader(
                    project_folder, connect),
                build=build, touch=touch, supersede=supersede))
    return receipts


def _live_rows_reader(project_folder, connect=None):
    def read(final):
        from library.tools.reel_build import timelines_to_replace
        from library.tools.reel_replace_guard import snapshot_timeline
        from library.tools.resolve_lock import resolve_lease

        with resolve_lease(f"read {final} for undo", exclusive=False):
            project = (connect or _connect)(_resolve_name(project_folder))
            found = {t.GetName(): t
                     for t in timelines_to_replace(project, {final})}
            if final not in found:
                raise UndoRefused(
                    f"no timeline called {final!r} is in the Resolve "
                    f"project",
                    "the undo cannot read a timeline that is not there",
                    "restore the timeline in Resolve (or rebuild the "
                    "reel), then re-run `ren undo`")
            return snapshot_timeline(found[final], final, side="retiring")
    return read


def render_stack(stack: Sequence[tuple]) -> str:
    if not stack:
        return "Nothing to undo."
    lines = []
    for final, act in stack:
        what = (f"touch {act.get('journal')}"
                if act["kind"] == "touch" else "rebuild")
        batch = f" (all-reels {act['batch']})" if act.get("batch") else ""
        lines.append(f"  {final} v{act['version']} {what}{batch} "
                     f"at {act['at']}")
    return "\n".join(lines)


__all__ = [
    "JOURNAL_FORMAT",
    "InversePlan",
    "RollbackDiverged",
    "TimelineMovedSinceTouch",
    "UndoNotVerified",
    "UndoRefused",
    "close_entry",
    "fail_entry",
    "list_entries",
    "open_entry",
    "plan_inverse",
    "projection",
    "projection_diff",
    "read_entry",
    "restore_plan_moment",
    "rollback_rebuild",
    "undo",
    "undo_in_place",
    "undo_stack",
    "undo_touch",
]
