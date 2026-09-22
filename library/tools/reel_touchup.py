"""A change to the timeline in front of you, instead of a rebuild of it.

The only path the captain could reach staged a fresh timeline and
promoted it over the old one (`manage_project.py build-reels`).  There
was no verb that changed the timeline they were looking at - while
`library/tools/composed_edit.py` sat beside it: a complete, tested,
pixel-verified in-place editor with zero production callers.  This
module is the path that connects the two.

What the caller states, and what it does not
--------------------------------------------
The caller states the change STRUCTURALLY: which reel, which item,
what changes - one of the five ops below.  Mapping a captain's
natural-language note onto such a change is the NEXT task and is
explicitly not this one; this module is the mechanism that task will
call.

The five ops
------------
- `move` - an overlay item to a different record position on the SAME
  row.  Same duration, same pixels.  A move across rows refuses: the
  composition addresses what it deletes by (row, position), so a
  cross-row plan would capture whatever sits at that position on the
  target row.  State that as `remove_overlay` plus `add_overlay`.
  The source row must be an overlay row (V3+ video): vacating V1, V2
  or any audio row refuses, because that leaves black or silence in
  continuous program and stating the covering change is the caller's
  job, not the gate's guess.
- `swap_pixels` - an overlay item's pixels for a re-rendered file at
  the same span.  The replaced item must carry no drawing Fusion comp
  (there is nothing to carry a treatment across a pool-item swap) and
  no colour grade beyond the default single node (an `Insertion`
  cannot take a grade from an item about to be deleted).  Its
  transform properties are CARRIED from its own live read and declared
  in the receipt - carried, never invented (AGENTS.md 10.5).
- `add_overlay` - a new overlay item at a stated record frame, on an
  OVERLAY row (V3+).  Adding to a comp-bearing row (V1/V2,
  `FUSION_COMP_TRACKS`) refuses: every clip there carries a
  treatment comp and a newly placed item has no manifest spec for
  the pass to key one to, so it would land untreated beside treated
  neighbours.  `properties` is REQUIRED: a placed item comes back at
  identity, so an undeclared treatment renders a framing nobody chose
  (`composed_edit.InsertionUndeclared`).
- `remove_overlay` - an overlay item off its row, same source-row rule
  as `move`.  `composed_edit` only deletes what it re-places, so the
  target is pre-deleted in one call on the staging copy (which places
  nothing and therefore cannot collide) while the row's kept items
  ride the composition as zero-length rewrites.
- `retime` - a played-length change with a ripple, planned by
  `composed_edit.plan_ripple` over the full read.  This is the class
  that pays the comp pass.

The qualification gate
----------------------
`qualify` classifies a change before anything is staged, into:

- `composed` - no clip's played length changes.  No comp is
  re-derived and no comp pass runs.  What this costs against a
  rebuild is MEASURED, not quoted: the composition mechanism holds
  at ~2s (delete 0.01-0.14s, place 0.25-0.57s, restore 0.03-4.09s)
  but staging (copy + conform) measured 8.9-25.9s and a rebuild of
  the same edit measured 19.4-67.1s (`docs/RULE_EVIDENCE.md`,
  "What it costs, and it is not the spike's figure").  Both cases
  measured there were length-CHANGING, so the no-length-change
  class - this one - had never been measured at all until this
  module's own live measurement.  The gate routes it; the numbers
  say whether it was worth routing.
- `composed_with_rederivation` - a played length changes.  Routed
  through the same composition, but ONLY with the real comp pass
  (`ReelLookRederiver` over the recorded fusion manifest) and ONLY
  with its cost said out loud: the comp pass is 17.0-63.7s of
  fixed overhead, most of what a rebuild costs, and on the two
  measured length-changing cases the composed totals (44.1s,
  54.9s) were SLOWER than or level with the rebuilds (24.0s,
  59.2s).  Never presented as a quick refresh.
- refusal - anything the gate cannot classify.  No guess, and no
  silent fallback to a full rebuild: a silent fallback is exactly the
  behaviour this module exists to remove.

The `_NullRederiver` is not a bypass.  It is constructed ONLY for the
`composed` class, its `reachable_reason` refuses any played-length
change defensively, and its `rederive` returns a receipt that says
`skipped` with why.  The verdict that counts is still state:
`assert_rederived` checks every length-changed comp item and every
comp-expecting insertion off the live timeline, and with none present
there is nothing whose comp could be stale.  The four structural
refusals in `composed_edit` are untouched -
`tests/test_composed_edit_refusal.py` attempts the bypass eight ways
and must keep passing.

"Staged", not "in place on the captain's timeline"
--------------------------------------------------
The captain ruled 2026-09-09: build beside them.  "In place" here
means "onto the existing built reel rather than a from-scratch
rebuild", NOT onto whatever the captain is reviewing without a copy.
`apply_touchup` duplicates the approved timeline into
`reel_build.staging_name` (the same staging the build uses, so the
bin layout and the stale-debris refusal both recognise it), conforms
it against the source, edits the copy, verifies by re-reading the
track, and only then swaps the names - with the replace guard, the
sign-off check, marker carry, archive retirement and the carried
signature close, mirroring `promote_staged_reels` phases 0-2.
Grades ride from the APPROVED timeline: a re-placed item comes back
with one colour node where it had eight, so every re-placed change
carries its grade from the live item on the untouched reel
(`_grade_sources_for`, resolved by source row + pre-edit record
frame and judged by read-back).

On `reel_rebuild_need`'s "on this reel the composed path is not
faster"
-----------------------------------------------
Answered, not a contradiction: the ~2s is the mechanism alone,
and the totals include staging (8.9-25.9s) and re-derivation
(17.8-37.4s).  Both cases measured in `docs/RULE_EVIDENCE.md` were
length-changing, so both paid step 7.  The gate's design rests on
exactly that reading: no-length-change edits skip the comp pass,
and length-changing edits pay it and say so.  Whether skipping the
comp pass is enough to beat a rebuild - staging alone can cost more
than a whole rebuild's best case - is what the live measurement
under `apply_touchup` decides, per edit, in the receipt.  If the
composed path loses on the no-length-change class too, that is
reported as a measured non-finding, not shipped as a speed
improvement.

On whether a pixel swap avoids delete-and-place
------------------------------------------------
It does not.  Resolve has no `SetMediaPoolItem`: changing what an
item plays means deleting it and placing a new one.  So a swap is the
full seven steps - delete-all, place-all, verify by re-read,
restore - and "cheap" for swaps means "no comp pass", never "no
delete" and never a ratio against a rebuild quoted from the old
spike figure.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from library.tools import composed_edit as _ce
from library.tools.execution.fusion_tracks import FUSION_COMP_TRACKS

# ── Refusals ─────────────────────────────────────────────────────────
#
# Every one stops the touchup by name, before anything is staged.


class TouchupRefused(RuntimeError):
    """A touchup that refused. Fail closed, always by name."""


class TouchupError(RuntimeError):
    """A touchup that failed mid-flight. The staging still stands."""


# ── The qualification ────────────────────────────────────────────────

COMPOSED = "composed"
COMPOSED_WITH_REDERIVATION = "composed_with_rederivation"

#: Rows whose spans are continuous program.  Vacating one - moving an
#: item away or removing it - leaves black or silence, so the gate
#: refuses and the caller states the covering change instead.  V1/V2
#: are the picture rows (`execution/fusion_tracks.FUSION_COMP_TRACKS`
#: plus the layout owner minting picture first); every audio row is
#: program sound.  Overlay rows are V3+ video by the SOP's layout
#: order (`docs/TIMELINE_SOP.md`), sparse by nature, so a gap there
#: is an ordinary state.
CONTINUOUS_VIDEO_ROWS = ("V1", "V2")


#: Rows the comp pass writes per-clip Fusion comps on. Read off the
#: one enumeration (`execution/fusion_tracks.FUSION_COMP_TRACKS`) rather
#: than restated, so the gate and the pass cannot disagree about which
#: rows are treated. A manifest can additionally declare V3+ rows for a
#: future multi-angle reel; `qualify` is pure over the track read and
#: has no manifest, so the static rows are the refusal's boundary and
#: that future is named in `_op_add_overlay` rather than guessed at.
COMP_ROWS = {f"V{index}" for index in FUSION_COMP_TRACKS}


def _is_audio_row(row: str) -> bool:
    return str(row).upper().startswith("A")


def _is_continuous_row(row: str) -> bool:
    row = str(row).upper()
    return row in CONTINUOUS_VIDEO_ROWS or _is_audio_row(row)


def _row_of(track_type: str, track_index: int) -> str:
    return _ce.row_label(track_type, track_index)


@dataclass
class Qualification:
    """What the gate decided, and what it will cost to do."""

    gate_class: str
    changes: list = field(default_factory=list)
    insertions: list = field(default_factory=list)
    removals: list = field(default_factory=list)
    #: Cross-row moves, stated with both ends: `ItemChange` carries
    #: the target row but the source index, so the overlap check and
    #: the count planner read the move here rather than inferring it.
    moves: list = field(default_factory=list)
    cost_statement: str = ""
    notes: list = field(default_factory=list)


def _find_clip(tracks: Sequence[Mapping], row: str,
               item_index: int) -> Mapping:
    for track in tracks:
        if _row_of(track["type"], int(track["index"])) != str(row).upper():
            continue
        clips = list(track.get("clips", []) or ())
        if 0 <= int(item_index) < len(clips):
            return clips[int(item_index)]
    raise TouchupRefused(
        f"REFUSING: no item at {row}[{item_index}] on this reel. "
        f"The change names an item the timeline does not have, so "
        f"there is nothing to qualify - re-read the reel and state "
        f"the item by its current position.")


def _track_exists(tracks: Sequence[Mapping], row: str) -> bool:
    want = str(row).upper()
    return any(_row_of(t["type"], int(t["index"])) == want for t in tracks)


def _spans_of(tracks: Sequence[Mapping]) -> dict:
    """`{row: [(start, end)]}` for every row, off the full read."""
    spans: dict = {}
    for track in tracks:
        row = _row_of(track["type"], int(track["index"]))
        spans[row] = [(int(c["record_in"]), int(c["record_out"]))
                      for c in (track.get("clips", []) or ())]
    return spans


def _span_is_free(spans: Sequence[tuple], start: int, duration: int,
                  ignore: Optional[tuple] = None) -> bool:
    end = int(start) + int(duration)
    for span_start, span_end in spans:
        if ignore is not None and (span_start, span_end) == ignore:
            continue
        if int(start) < int(span_end) and int(span_start) < end:
            return False
    return True


def _check_free(spans: Sequence[tuple], row: str, start: int,
                duration: int, ignore: Optional[tuple] = None,
                what: str = "the placement") -> None:
    if not _span_is_free(spans, start, duration, ignore):
        raise TouchupRefused(
            f"REFUSING: {what} on {row} at {start} for {duration}f "
            f"collides with a live item. `AppendToTimeline` silently "
            f"places NOTHING where it would collide, so a touchup "
            f"never asks it to - state a free span.")


def qualify(tracks: Sequence[Mapping], spec: Mapping) -> Qualification:
    """Classify a structured change. Pure: reads nothing but `tracks`.

    `spec` is `{"reel": N, "edits": [...], "exclude": [[row, idx]]}`.
    Each edit carries `op` - `move`, `swap_pixels`, `add_overlay`,
    `remove_overlay` or `retime` - and the fields that op documents
    in the module docstring.  Raises `TouchupRefused` for anything
    unclassifiable, naming why.  Never falls back to a rebuild.
    """
    edits = list((spec or {}).get("edits") or ())
    if not edits:
        raise TouchupRefused(
            "REFUSING: the change names no edits. A touchup with "
            "nothing to do is not a no-op to wave through - it is a "
            "caller that failed to say what it wants.")
    exclude = [(str(r), int(i)) for r, i in
               ((spec or {}).get("exclude") or ())]
    spans = _spans_of(tracks)
    notes: list = []
    changes: list = []
    insertions: list = []
    removals: list = []
    moves: list = []
    length_changing = False

    for position, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise TouchupRefused(
                f"REFUSING: edit {position} is not a mapping "
                f"({edit!r}). The gate classifies structure, and "
                f"there is no structure here to classify.")
        op = str(edit.get("op") or "")
        handler = _OP_HANDLERS.get(op)
        if handler is None:
            raise TouchupRefused(
                f"REFUSING: edit {position} names op {op!r}, and the "
                f"gate knows five ops: "
                f"{sorted(_OP_HANDLERS)}. Anything else is "
                f"unclassifiable - extend the gate deliberately "
                f"rather than guessing what {op!r} means.")
        length_changing = handler(
            edit, position, tracks, spans, changes, insertions,
            removals, moves, exclude, notes) or length_changing

    _prune_shadowed_rewrites(changes, removals, moves)
    _check_post_edit_overlaps(tracks, changes, insertions, removals,
                              moves)
    _check_single_claim(changes, removals, moves)

    if length_changing:
        cost = ("composed_with_rederivation: this change alters a "
                "played length, so the Fusion comp pass runs after "
                "the composition. That pass measured 17.0-63.7s of "
                "fixed overhead, and on the two measured "
                "length-changing cases the composed totals (44.1s, "
                "54.9s) were SLOWER than or level with the rebuilds "
                "(24.0s, 59.2s) - `docs/RULE_EVIDENCE.md`, 'What it "
                "costs, and it is not the spike's figure'. This is "
                "NOT a quick refresh.")
        gate_class = COMPOSED_WITH_REDERIVATION
    else:
        cost = ("composed: no played length changes, so no comp is "
                "re-derived and no comp pass runs. The mechanism "
                "holds at ~2s (delete 0.01-0.14s, place 0.25-0.57s, "
                "restore 0.03-4.09s) but staging (copy + conform) "
                "measured 8.9-25.9s against a rebuild of the same "
                "edit at 19.4-67.1s - and the no-length-change class "
                "had never been measured at all until this path's "
                "own live measurement. The receipt's seconds say "
                "whether it was worth routing.")
        gate_class = COMPOSED
    return Qualification(gate_class=gate_class, changes=changes,
                         insertions=insertions, removals=removals,
                         moves=moves,
                         cost_statement=cost, notes=notes)


def _check_post_edit_overlaps(tracks: Sequence[Mapping],
                              changes: Sequence[_ce.ItemChange],
                              insertions: Sequence[_ce.Insertion],
                              removals: Sequence[dict],
                              moves: Sequence[dict]) -> None:
    """No two post-edit spans may overlap on any row.

    Computed over the FULL read plus the plan, before anything is
    deleted: an overlap the plan creates is a collision
    `AppendToTimeline` would silently swallow, and the re-read after
    would be the first to say so - after the delete.  Overlay rows
    get no exemption: stacked graphics are not expressible as one
    plan, so they refuse here and the caller states the ordering.
    """
    removed_keys = {(str(r.get("row")).upper(), int(r.get("item_index")))
                    for r in removals}
    moved_from = {(str(m.get("from_row")).upper(),
                   int(m.get("from_index"))) for m in moves}
    # Same-row changes keyed by (row, index): retime/shift/rewrite.
    # A move's change is keyed by its TARGET row, so it is never
    # looked up here - its two ends live in `moves`, stated with
    # both ends rather than inferred.
    moved_changes = {id(m.get("change")) for m in moves}
    same_row = {(c.row, c.item_index): c for c in changes
                if id(c) not in moved_changes}
    for track in tracks:
        row = _row_of(track["type"], int(track["index"]))
        placed: list = []
        for item_index, clip in enumerate(track.get("clips", []) or ()):
            if (row, item_index) in removed_keys:
                continue
            if (row, item_index) in moved_from:
                continue  # moved away: its old span is vacated
            change = same_row.get((row, item_index))
            if change is not None:
                placed.append((int(change.record_frame),
                               int(change.record_frame)
                               + int(change.duration),
                               f"edit:{row}[{item_index}]"))
            else:
                placed.append((int(clip["record_in"]),
                               int(clip["record_out"]),
                               f"live:{row}[{item_index}]"))
        for move in moves:
            if str(move.get("to_row")).upper() == row:
                placed.append((int(move["to_record"]),
                               int(move["to_record"])
                               + int(move["duration"]),
                               f"moved-in:{move['from_row']}"
                               f"[{move['from_index']}]"))
        for insertion in insertions:
            # `_PendingSwap` at gate time, `composed_edit.Insertion`
            # if re-checked later: same attribute names, both read
            # the same way.
            if str(insertion.row).upper() == row:
                placed.append((int(insertion.record_frame),
                               int(insertion.record_frame)
                               + int(insertion.duration),
                               f"new:{insertion.name or row}"))
        placed.sort()
        for first, second in zip(placed, placed[1:]):
            if second[0] < first[1]:
                raise TouchupRefused(
                    f"REFUSING: the plan overlaps on {row}: "
                    f"{first[2]} @{first[0]}..{first[1]} and "
                    f"{second[2]} @{second[0]}..{second[1]}. "
                    f"`AppendToTimeline` silently places nothing on "
                    f"collision, so an overlapping plan never "
                    f"reaches the delete - state the ordering "
                    f"explicitly.")


def _prune_shadowed_rewrites(changes: list,
                               removals: Sequence[dict],
                               moves: Sequence[dict]) -> None:
    """Drop rewrites of items another edit already claims.

    A remove plans zero-length rewrites for every kept item on its
    row, but a later edit in the same spec may move one of those
    items - whose placement the move then verifies.  Re-placing it
    at its old span too would put it on the timeline twice.  A
    rewrite is identified structurally (a SHIFT to its own span),
    so only those go; a ripple shift to a NEW span still conflicts
    and `_check_single_claim` refuses it below.
    """
    claimed = {(str(r.get("row")).upper(), int(r.get("item_index")))
               for r in removals}
    claimed |= {(str(m.get("from_row")).upper(),
                 int(m.get("from_index"))) for m in moves}
    moved_ids = {id(m.get("change")) for m in moves}
    kept = []
    for change in changes:
        if (id(change) not in moved_ids
                and change.how == _ce.SHIFT
                and int(change.record_frame)
                == int(change.previous_record)
                and int(change.duration)
                == int(change.previous_duration)
                and (change.row, change.item_index) in claimed):
            continue
        kept.append(change)
    changes[:] = kept


def _check_single_claim(changes: Sequence[_ce.ItemChange],
                          removals: Sequence[dict],
                          moves: Sequence[dict]) -> None:
    """One source item, one edit. Two edits addressing the same item -
    a rewrite of V4[2] plus a move of V4[2] - would place it twice.
    The overlap check cannot see that (the spans differ), so this
    refuses it by position before anything is staged.
    """
    moved_ids = {id(m.get("change")) for m in moves}
    claims: dict = {}
    for change in changes:
        if id(change) in moved_ids:
            continue
        key = (change.row, change.item_index)
        claims.setdefault(key, []).append(f"{change.how}@{change.row}")
    for entry in removals:
        key = (str(entry.get("row")).upper(),
               int(entry.get("item_index")))
        claims.setdefault(key, []).append("remove")
    for move in moves:
        key = (str(move.get("from_row")).upper(),
               int(move.get("from_index")))
        claims.setdefault(key, []).append("move")
    doubled = {key: kinds for key, kinds in claims.items()
               if len(kinds) > 1}
    if doubled:
        raise TouchupRefused(
            f"REFUSING: two edits address the same item: {doubled}. "
            f"One of them would place it twice. State the change as "
            f"one edit per item.")


# ── The five ops ─────────────────────────────────────────────────────
#
# Each takes the edit, its position, the full read, the live spans and
# the plan under construction.  Returns True when it alters a played
# length.  Every one raises `TouchupRefused` for what it cannot
# classify.


def _op_move(edit, position, tracks, spans, changes, insertions,
             removals, moves, exclude, notes) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    to_row = str(edit.get("to_row") or row).upper()
    to_record = edit.get("to_record")
    if not row or item_index is None or to_record is None:
        raise TouchupRefused(
            f"REFUSING: edit {position} (`move`) needs `row`, `item` "
            f"and `to_record` (got {dict(edit)!r}). A move to "
            f"nowhere is unclassifiable.")
    if _is_continuous_row(row):
        raise TouchupRefused(
            f"REFUSING: edit {position} moves {row}[{item_index}] "
            f"away, and {row} is continuous program - vacating it "
            f"leaves black or silence. State the covering change "
            f"(what plays those frames instead) rather than asking "
            f"the gate to guess it.")
    if not _track_exists(tracks, to_row):
        raise TouchupRefused(
            f"REFUSING: edit {position} moves to row {to_row}, and "
            f"this reel has no such row. The gate never invents a "
            f"track - name one the timeline already carries.")
    if to_row != row:
        raise TouchupRefused(
            f"REFUSING: edit {position} moves {row}[{item_index}] to "
            f"{to_row}, and a move across rows is not a composed "
            f"edit: the composition addresses what it deletes by "
            f"(row, position), so a cross-row plan would capture "
            f"whatever sits at that position on the TARGET row and "
            f"delete a bystander while duplicating the moved item. "
            f"State it as two edits - `remove_overlay` from {row} "
            f"plus `add_overlay` on {to_row} carrying the treatment "
            f"explicitly - rather than asking the gate to guess the "
            f"carrying.")
    clip = _find_clip(tracks, row, int(item_index))
    duration = int(clip["duration"])
    own_span = (int(clip["record_in"]), int(clip["record_out"]))
    _check_free(spans.get(to_row, []), to_row, int(to_record),
                duration,
                ignore=own_span if to_row == row else None,
                what=f"edit {position} (`move`)")
    from_row = row
    track_type = "audio" if to_row.startswith("A") else "video"
    track_index = int(to_row[1:])
    change = _ce.ItemChange(
        track_type=track_type, track_index=track_index,
        item_index=int(item_index), record_frame=int(to_record),
        duration=duration, left_offset=int(clip["left_offset"] or 0),
        previous_record=int(clip["record_in"]),
        previous_duration=duration, how=_ce.SHIFT,
        comp_count=_ce.treatment_comps(clip),
        right_offset=clip.get("right_offset"),
        name=clip.get("name", ""))
    changes.append(change)
    moves.append({"from_row": from_row,
                  "from_index": int(item_index), "to_row": to_row,
                  "to_record": int(to_record), "duration": duration,
                  "change": change})
    notes.append(f"edit {position}: move {from_row}[{item_index}] "
                 f"@{own_span[0]} -> {to_row}@{to_record} "
                 f"({duration}f, same pixels)")
    return False


def _op_swap_pixels(edit, position, tracks, spans, changes, insertions,
                    removals, moves, exclude, notes) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    media = edit.get("media")
    if not row or item_index is None or not media:
        raise TouchupRefused(
            f"REFUSING: edit {position} (`swap_pixels`) needs `row`, "
            f"`item` and `media` (got {dict(edit)!r}).")
    clip = _find_clip(tracks, row, int(item_index))
    if _ce.treatment_comps(clip):
        raise TouchupRefused(
            f"REFUSING: edit {position} swaps the pixels of "
            f"{row}[{item_index}], and that item carries a drawing "
            f"Fusion comp. A pool-item swap cannot carry a treatment "
            f"across - there is no route from the old comp to the "
            f"new item - so this needs a rebuild, not a touchup.")
    duration = int(edit.get("duration") or clip["duration"])
    removals.append({"row": row, "item_index": int(item_index),
                     "record_frame": int(clip["record_in"]),
                     "duration": int(clip["duration"]),
                     "why": f"edit {position} (`swap_pixels`)"})
    insertions.append(_PendingSwap(
        row=row, record_frame=int(clip["record_in"]),
        duration=duration, media=str(media),
        left_offset=int(edit.get("left_offset") or 0),
        carry_from=(row, int(item_index)),
        name=str(edit.get("name") or clip.get("name", "")),
        position=position))
    notes.append(f"edit {position}: swap {row}[{item_index}] pixels "
                 f"for {media} at @{clip['record_in']} ({duration}f)")
    return False


def _op_add_overlay(edit, position, tracks, spans, changes, insertions,
                    removals, moves, exclude, notes) -> bool:
    row = str(edit.get("row") or "").upper()
    media = edit.get("media")
    record = edit.get("record")
    duration = edit.get("duration")
    properties = edit.get("properties")
    if (not row or not media or record is None or duration is None):
        raise TouchupRefused(
            f"REFUSING: edit {position} (`add_overlay`) needs `row`, "
            f"`media`, `record` and `duration` (got {dict(edit)!r}).")
    if properties is None:
        raise TouchupRefused(
            f"REFUSING: edit {position} (`add_overlay`) declares no "
            f"`properties`. A placed item comes back at IDENTITY, so "
            f"it would render at a framing nobody chose - declare "
            f"them (`{{}}` if identity is what is wanted).")
    if not _track_exists(tracks, row):
        raise TouchupRefused(
            f"REFUSING: edit {position} adds to row {row}, and this "
            f"reel has no such row. The gate never invents a track.")
    if row in COMP_ROWS:
        raise TouchupRefused(
            f"REFUSING: edit {position} adds a new item on {row}, "
            f"and {row} is a comp-bearing row - the pass writes "
            f"per-clip Fusion comps there "
            f"(`execution/fusion_tracks.FUSION_COMP_TRACKS`), so "
            f"every clip around it carries a treatment. A newly "
            f"placed item has no manifest spec for the pass to key "
            f"one to, so it would land with no comp beside treated "
            f"neighbours - and render, looking like a choice. "
            f"Rebuild the reel with `build-reels`, which plans the "
            f"new clip with its treatment, instead of touching it "
            f"up.")
    _check_free(spans.get(row, []), row, int(record), int(duration),
                what=f"edit {position} (`add_overlay`)")
    insertions.append(_PendingSwap(
        row=row, record_frame=int(record), duration=int(duration),
        media=str(media), left_offset=int(edit.get("left_offset") or 0),
        carry_from=None, declared_properties=dict(properties),
        name=str(edit.get("name") or ""), position=position))
    notes.append(f"edit {position}: add {row}@{record} ({duration}f) "
                 f"from {media}")
    return False


def _op_remove_overlay(edit, position, tracks, spans, changes,
                       insertions, removals, moves, exclude, notes
                       ) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    if not row or item_index is None:
        raise TouchupRefused(
            f"REFUSING: edit {position} (`remove_overlay`) needs "
            f"`row` and `item` (got {dict(edit)!r}).")
    if _is_continuous_row(row):
        raise TouchupRefused(
            f"REFUSING: edit {position} removes {row}[{item_index}], "
            f"and {row} is continuous program - removing it leaves "
            f"black or silence. State the covering change rather "
            f"than asking the gate to guess it.")
    clip = _find_clip(tracks, row, int(item_index))
    removals.append({"row": row, "item_index": int(item_index),
                     "record_frame": int(clip["record_in"]),
                     "duration": int(clip["duration"]),
                     "why": f"edit {position} (`remove_overlay`)"})
    # `composed_edit` has no delete-without-place primitive: it only
    # deletes what it re-places.  So the row's kept items ride the
    # composition as zero-length rewrites, and the removed item is
    # pre-deleted in one call on the STAGING copy before the
    # composition runs (`_pre_delete_removed`) - a delete with no
    # placement after it cannot collide, and a refusal later still
    # leaves the approved timeline whole because the staging is
    # disposable.
    for track in tracks:
        track_row = _row_of(track["type"], int(track["index"]))
        if track_row != row:
            continue
        for other_index, other in enumerate(
                track.get("clips", []) or ()):
            if int(other_index) == int(item_index):
                continue
            changes.append(_ce.ItemChange(
                track_type=track["type"],
                track_index=int(track["index"]),
                item_index=int(other_index),
                record_frame=int(other["record_in"]),
                duration=int(other["duration"]),
                left_offset=int(other["left_offset"] or 0),
                previous_record=int(other["record_in"]),
                previous_duration=int(other["duration"]),
                how=_ce.SHIFT,
                comp_count=_ce.treatment_comps(other),
                right_offset=other.get("right_offset"),
                name=other.get("name", "")))
    notes.append(f"edit {position}: remove {row}[{item_index}] "
                 f"(@{clip['record_in']}, {clip['duration']}f)")
    return False


def _op_retime(edit, position, tracks, spans, changes, insertions,
               removals, moves, exclude, notes) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    duration = edit.get("duration")
    if not row or item_index is None or duration is None:
        raise TouchupRefused(
            f"REFUSING: edit {position} (`retime`) needs `row`, "
            f"`item` and `duration` (got {dict(edit)!r}).")
    clip = _find_clip(tracks, row, int(item_index))
    old = int(clip["duration"])
    new = int(duration)
    if new <= 0:
        raise TouchupRefused(
            f"REFUSING: edit {position} retimes {row}[{item_index}] "
            f"to {new}f. A zero or negative played length is not a "
            f"trim - to take an item out, remove it explicitly.")
    if new == old:
        notes.append(f"edit {position}: retime {row}[{item_index}] "
                     f"to its own length ({old}f) - no-op, qualified "
                     f"without planning")
        return False
    delta = new - old
    cut_frame = int(clip["record_out"])
    try:
        planned = _ce.plan_ripple(tracks, cut_frame, delta,
                                  exclude=exclude)
    except _ce.SourceHeadroomExhausted as starved:
        raise TouchupRefused(
            f"REFUSING: edit {position} retimes {row}[{item_index}] "
            f"{old}->{new}f and {starved} lengthening an item past "
            f"its source file places a hole rather than picture.")
    if not any(c.row == row and c.item_index == int(item_index)
               and c.played_length_changes for c in planned):
        raise TouchupRefused(
            f"REFUSING: edit {position} retimes {row}[{item_index}] "
            f"{old}->{new}f and the ripple planner did not extend "
            f"that item (cut @{cut_frame}, delta {delta:+d}). The "
            f"plan and the ask disagree, so nothing is staged.")
    changes.extend(planned)
    stretched = sorted(f"{c.row}[{c.item_index}]" for c in planned
                       if c.played_length_changes)
    shifted = sorted(f"{c.row}[{c.item_index}]" for c in planned
                     if not c.played_length_changes)
    notes.append(f"edit {position}: retime {row}[{item_index}] "
                 f"{old}->{new}f ({delta:+d}f); stretched "
                 f"{stretched}; shifted {len(shifted)} item(s)")
    return True


_OP_HANDLERS = {
    "move": _op_move,
    "swap_pixels": _op_swap_pixels,
    "add_overlay": _op_add_overlay,
    "remove_overlay": _op_remove_overlay,
    "retime": _op_retime,
}


@dataclass
class _PendingSwap:
    """A swap/add the gate qualified but Resolve has not resolved yet.

    `media` is a FILE PATH until the apply resolves it to a live pool
    item (importing it first when the pool does not hold it).
    `carry_from` names the live item whose transform is carried, or
    None when the spec declares `declared_properties` outright.
    """

    row: str
    record_frame: int
    duration: int
    media: str
    left_offset: int = 0
    carry_from: Optional[tuple] = None
    declared_properties: Optional[Mapping[str, Any]] = None
    name: str = ""
    position: int = 0


# ── The rederiver that is only a stand-in ────────────────────────────


class _NullRederiver(_ce.CompRederiver):
    """The comp generator's seat, held for an edit that needs none.

    Constructed ONLY for the `composed` class.  `reachable_reason`
    refuses any played-length change defensively, so a caller that
    wired the wrong rederiver to a trim still refuses before the
    delete.  `rederive` reports `skipped` with why - and the verdict
    that counts is still state: `assert_rederived` re-reads every
    length-changed comp item off the live timeline, and with none in
    the plan there is nothing whose comp could be stale.
    """

    def __init__(self, why: str):
        self.why = why

    def reachable_reason(self, changes) -> Optional[str]:
        trimmed = [c for c in changes if c.played_length_changes]
        if trimmed:
            return ("this edit changes a played length and was handed "
                    "the null rederiver, which re-derives nothing - "
                    f"{[f'{c.row}[{c.item_index}]' for c in trimmed]}. "
                    "A trim needs the real comp pass or it refuses.")
        return None

    def rederive(self, changes) -> dict:
        return {"ran": True, "ok": True, "comp_pass": "skipped",
                "why": self.why}

    def expects_comp(self, row: str, record_frame: int) -> Optional[bool]:
        return False


# ── The recorded fusion manifest, for the length-changing class ──────


def recorded_fusion_manifest(project_folder: str,
                             timeline_name: str) -> Optional[dict]:
    """The fusion manifest the last build wrote for this timeline.

    `reel_look.apply_comps` writes it beside the project under
    scratch (`<slug>_fusion_manifest.json`) before launching the
    comp pass.  When it is there it is the manifest the re-derivation
    needs; when it is not, the length-changing class has no route to
    the comp generator and refuses rather than guessing one.
    """
    import json as _json

    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_look import _slug

    scratch = str(ProjectLayout(project_folder).read_dir(Area.SCRATCH))
    candidate = os.path.join(scratch, "reel_look",
                             f"{_slug(timeline_name)}_fusion_manifest.json")
    if not os.path.isfile(candidate):
        return None
    try:
        with open(candidate, encoding="utf-8") as handle:
            manifest = _json.load(handle)
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _manifest_source_sequence(manifest: Mapping) -> dict:
    """`{row: [source_file, ...]}` for every comp-bearing video row."""
    from library.tools.execution.fusion_tracks import fusion_comp_tracks

    out = {}
    for index, clips, _transitions in fusion_comp_tracks(manifest):
        row = _row_of("video", index)
        out[row] = [str(c.get("source_file") or "") for c in clips]
    return out


def _live_source_sequence(tracks: Sequence[Mapping]) -> dict:
    """`{row: [source_file, ...]}` off the live read, same shape."""
    from library.tools.execution.fusion_tracks import (
        FUSION_COMP_TRACKS)

    rows = {f"V{i}" for i in FUSION_COMP_TRACKS}
    for track in tracks:
        if str(track.get("type", "")).lower().startswith("v"):
            rows.add(_row_of(track["type"], int(track["index"])))
    out = {}
    for track in tracks:
        row = _row_of(track["type"], int(track["index"]))
        if row not in rows:
            continue
        out[row] = [str((c.get("source_file") or ""))
                    for c in (track.get("clips", []) or ())]
    return out


def check_manifest_matches(manifest: Mapping,
                           tracks: Sequence[Mapping]) -> None:
    """The recorded manifest must describe the timeline being edited.

    The comp pass maps manifest clip specs to live items by source
    path in order: a manifest with a different source sequence on a
    comp-bearing row would write comps onto the wrong clips.  Refuse
    before anything is staged when they disagree.
    """
    wanted = _manifest_source_sequence(manifest)
    live = _live_source_sequence(tracks)
    mismatched = {}
    for row, sources in sorted(wanted.items()):
        if live.get(row) != sources:
            mismatched[row] = {
                "manifest_clips": len(sources),
                "timeline_clips": len(live.get(row) or []),
            }
    if mismatched:
        raise TouchupRefused(
            f"REFUSING: the recorded fusion manifest no longer "
            f"describes this reel's timeline ({mismatched}). The "
            f"comp pass maps specs to items by source in order, so "
            f"it would write comps onto the wrong clips. Rebuild "
            f"the reel with `build-reels` - which re-derives the "
            f"manifest - instead of touching it up.")


# ── Resolving media paths to pool items ──────────────────────────────


def pool_item_for_path(pool: Any, path: str) -> Any:
    """The live pool item for a file, importing it when absent.

    Matched by full path, the same rule the comp pass's matcher
    holds to (`apply_fusion_comps`: matching by full path only).
    Raises `TouchupRefused` when the file is not on disk or the pool
    will not take it - both before anything is staged.
    """
    wanted = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(wanted):
        raise TouchupRefused(
            f"REFUSING: the overlay file is not on disk: {wanted}. "
            f"A touchup never renders media - render it first, then "
            f"state its path.")
    found = _find_pool_item(pool, wanted)
    if found is not None:
        return found
    imported = pool.ImportMedia([wanted])
    if not imported:
        raise TouchupRefused(
            f"REFUSING: the pool would not import {wanted} "
            f"(`ImportMedia` returned nothing). Nothing was staged.")
    found = _find_pool_item(pool, wanted)
    if found is None:
        raise TouchupRefused(
            f"REFUSING: {wanted} imported but no pool item reads "
            f"back at that path. Nothing was staged.")
    return found


def _find_pool_item(pool: Any, wanted: str) -> Optional[Any]:
    try:
        root = pool.GetRootFolder()
    except Exception:  # noqa: BLE001 - a pool that will not answer
        return None
    if root is None:
        return None
    stack = [root]
    seen = set()
    while stack:
        folder = stack.pop()
        if id(folder) in seen:
            continue
        seen.add(id(folder))
        try:
            clips = folder.GetClipList() or []
        except Exception:  # noqa: BLE001
            clips = []
        for clip in clips:
            try:
                path = clip.GetClipProperty("File Path") or ""
            except Exception:  # noqa: BLE001
                continue
            if os.path.abspath(str(path)) == wanted:
                return clip
        try:
            subfolders = folder.GetSubFolderList() or []
        except Exception:  # noqa: BLE001
            subfolders = []
        stack.extend(subfolders)
    return None


def _pool_source_frames(pool_item: Any) -> Optional[int]:
    try:
        raw = pool_item.GetClipProperty("Frames")
    except Exception:  # noqa: BLE001
        return None
    try:
        return int(float(str(raw)))
    except (TypeError, ValueError):
        return None


# ── Applying: stage, compose, verify, promote ────────────────────────


def resolve_final_name(project_folder: str, reel: int) -> str:
    """The approved timeline name for a reel number, off the plan."""
    from library.tools.reel_proposal import proposal_path, read_proposal

    for moment in read_proposal(str(proposal_path(project_folder))):
        if int(moment.number) == int(reel):
            return moment.timeline_name
    raise TouchupRefused(
        f"REFUSING: the plan names no reel {reel}. A touchup edits "
        f"a built reel the plan describes - it never invents one.")


def _resolve_insertions(pool: Any, timeline: Any,
                        pending: Sequence[_PendingSwap]) -> list:
    """Pending swaps/adds to live `composed_edit.Insertion`s."""
    from library.tools import reel_read as _read

    resolved = []
    for item in pending:
        mpi = pool_item_for_path(pool, item.media)
        frames = _pool_source_frames(mpi)
        if (frames is not None
                and int(item.left_offset) + int(item.duration) > frames):
            raise TouchupRefused(
                f"REFUSING: edit {item.position} wants "
                f"{item.duration}f from offset {item.left_offset} of "
                f"{item.media}, which holds {frames}f. Placing it "
                f"would put a hole where picture was asked for.")
        if item.carry_from is not None:
            row, index = item.carry_from
            live = _live_rows(timeline)
            items = live.get(str(row).upper()) or []
            if int(index) >= len(items):
                raise TouchupRefused(
                    f"REFUSING: edit {item.position} carries the "
                    f"treatment of {row}[{index}], which is no "
                    f"longer on the staging timeline. The plan and "
                    f"the timeline disagree, so nothing is deleted.")
            source = items[int(index)]
            try:
                properties = dict(source.GetProperty() or {})
            except Exception:  # noqa: BLE001
                properties = {}
            nodes = _live_prop(source, "GetNumNodes", None)
            if nodes is not None and int(nodes) > 1:
                raise TouchupRefused(
                    f"REFUSING: edit {item.position} swaps "
                    f"{row}[{index}], which carries a colour grade "
                    f"({nodes} nodes). An `Insertion` cannot take a "
                    f"grade from an item about to be deleted, so a "
                    f"graded swap needs a rebuild, not a touchup.")
            grade_from = None
        else:
            properties = dict(item.declared_properties or {})
            grade_from = None
        media_type = 2 if str(item.row).upper().startswith("A") else 1
        resolved.append(_ce.Insertion(
            track_type="audio" if media_type == 2 else "video",
            track_index=int(str(item.row)[1:]),
            media_pool_item=mpi, left_offset=int(item.left_offset),
            duration=int(item.duration),
            record_frame=int(item.record_frame), name=item.name,
            properties=properties, grade_from=grade_from))
    return resolved


def _live_prop(item: Any, name: str, default: Any = None) -> Any:
    """One getter off a live handle, never raising.

    The module reads live handles in three places (grade-node check
    here, row handles below); each goes through this rather than
    reaching into `composed_edit`'s own private reader.
    """
    fn = getattr(item, name, None)
    if fn is None:
        return default
    try:
        value = fn()
    except Exception:  # noqa: BLE001 - a handle that will not answer
        return default
    return default if value is None else value


def _live_rows(timeline: Any) -> dict:
    """Live item handles per row, off the one reader.

    `reel_read.live_track_items` holds the tree's one `GetItemListInTrack`
    call (AGENTS.md 15); `reel_read.live_items` is the touchup's slice
    of it.  Re-read after every mutation: a handle
    held across a delete or a place is a zombie that still answers
    getters.
    """
    from library.tools import reel_read as _read

    return {_ce.row_label(row["type"], row["index"]): row["items"]
            for row in _read.live_items(timeline)}


def _pre_delete_removed(timeline: Any, removals: Sequence[dict]) -> dict:
    """Delete the removal targets on the STAGING copy, in one call.

    `composed_edit` only deletes what it re-places, so a removal -
    and the old item of a pixel swap - would otherwise survive the
    composition: the swap's insertion would then collide with the
    item it replaces, and `AppendToTimeline` would silently place
    nothing while the re-read found the OLD item at the expected
    span.  This call vacates those spans first.  It places nothing,
    so it cannot collide; and it runs on the disposable staging
    copy, so a refusal later still leaves the approved timeline
    whole - the staging is simply discarded, and the receipt says
    so.
    """
    if not removals:
        return {"asked": 0, "seconds": 0.0}
    started = time.time()
    rows = _live_rows(timeline)
    victims = []
    missing = []
    for entry in removals:
        row = str(entry.get("row")).upper()
        items = rows.get(row) or []
        hits = [item for item in items
                if _live_prop(item, "GetStart", None)
                == int(entry["record_frame"])]
        if len(hits) != 1:
            missing.append({"row": row,
                            "record_frame": entry.get("record_frame"),
                            "found": len(hits)})
            continue
        victims.append(hits[0])
    if missing:
        raise TouchupError(
            f"the staging copy does not hold what the plan removes: "
            f"{missing}. The plan and the timeline disagree - "
            f"nothing further is deleted and the approved timeline "
            f"stands.")
    timeline.DeleteClips(victims, False)
    return {"asked": len(victims),
            "seconds": round(time.time() - started, 3)}


def _rekey_changes(staging_tracks: Sequence[Mapping],
                   qualification: Qualification) -> list:
    """Re-key the plan's item indexes off a fresh read.

    `ItemChange.item_index` is the item's position in its row, and
    the pre-delete vacated spans - so every index planned before it
    may be stale.  The items themselves have not moved yet: each is
    still at its `previous_record` on its source row (the move's
    source row lives in `qualification.moves`, since a move's change
    is keyed by its TARGET row).  Re-resolve each to its current
    position; refuse on any ambiguity rather than guessing.
    """
    import dataclasses as _dc

    position: dict = {}
    for track in staging_tracks:
        row = _row_of(track["type"], int(track["index"]))
        for index, clip in enumerate(track.get("clips", []) or ()):
            key = (row, int(clip["record_in"]))
            if key in position:
                raise TouchupError(
                    f"two items share {row}@{key[1]} on the staging "
                    f"copy - the plan cannot address one of them by "
                    f"position. Nothing further is deleted.")
            position[key] = index
    move_source = {id(m.get("change")): str(m.get("from_row")).upper()
                   for m in qualification.moves}
    rekeyed = []
    by_id = {}
    for change in qualification.changes:
        source_row = move_source.get(id(change), change.row)
        key = (source_row, int(change.previous_record))
        if key not in position:
            raise TouchupError(
                f"the staging copy has no item at "
                f"{source_row}@{change.previous_record} for the "
                f"planned {change.how}. The plan and the timeline "
                f"disagree - nothing further is deleted and the "
                f"approved timeline stands.")
        new = _dc.replace(change, item_index=position[key])
        by_id[id(change)] = new
        rekeyed.append(new)
    # The moves table states each move with both ends; re-point its
    # change at the rekeyed object so everything downstream of here -
    # the grade carry, the overlap accounting - reads the move rather
    # than inferring it from a stale identity.
    for move in qualification.moves:
        if id(move.get("change")) in by_id:
            move["change"] = by_id[id(move["change"])]
    return rekeyed


def _grade_sources_for(source: Any,
                       changes: Sequence[_ce.ItemChange],
                       moves: Sequence[dict]) -> dict:
    """`(row, item_index)` to the live item carrying this change's grade.

    A re-placed item comes back with one colour node where it had
    eight, so every change the composition re-places carries its grade
    from the item that already has it.  The source is a LIVE item on
    the APPROVED timeline - which this whole path never mutates - so
    the delete cannot take it.  Addressed by (source row, pre-edit
    record frame): the pre-delete re-seats positional indexes, but an
    item's pre-edit span is stable, and a move's source row lives in
    the moves table (its change is keyed by the target row).  A change
    with no live item at its pre-edit span gets no entry, and the
    restore then judges the grade like every other property - by
    read-back, never by assumption.
    """
    approved: dict = {}
    for row, items in _live_rows(source).items():
        for item in items:
            approved[(str(row).upper(),
                      _live_prop(item, "GetStart", None))] = item
    move_source = {id(m.get("change")): str(m.get("from_row")).upper()
                   for m in moves}
    out: dict = {}
    for change in changes:
        src_row = str(move_source.get(id(change), change.row)).upper()
        src = approved.get((src_row, int(change.previous_record)))
        if src is not None:
            out[(change.row, change.item_index)] = src
    return out


def _plan_post_edit_counts(tracks: Sequence[Mapping],
                           qualification: Qualification) -> dict:
    """`{row: clip count}` the edited timeline will carry, per row.

    Read off the live counts plus the plan's arithmetic - removals
    take one, insertions add one, moves take one from the source row
    and add one to the target - never off the plan alone, so a plan
    that disagrees with the timeline refuses here rather than
    handing the comp pass a miscount it would write onto the wrong
    clips.
    """
    counts: dict = {}
    for track in tracks:
        row = _row_of(track["type"], int(track["index"]))
        counts[row] = len(list(track.get("clips", []) or ()))
    for entry in qualification.removals:
        row = str(entry.get("row")).upper()
        counts[row] = counts.get(row, 0) - 1
    for move in qualification.moves:
        from_row = str(move.get("from_row")).upper()
        to_row = str(move.get("to_row")).upper()
        counts[from_row] = counts.get(from_row, 0) - 1
        counts[to_row] = counts.get(to_row, 0) + 1
    for insertion in qualification.insertions:
        row = str(insertion.row).upper()
        counts[row] = counts.get(row, 0) + 1
    void = sorted(row for row, count in counts.items() if count < 0)
    if void:
        raise TouchupRefused(
            f"REFUSING: the plan accounts {void} below zero items. "
            f"The plan and the timeline disagree - nothing is staged.")
    return counts


def apply_touchup(project_folder: str, spec: Mapping,
                  resolve_project_name: str = "",
                  allow_drops=None,
                  supersede=None,
                  connect=None,
                  rederiver_override=None) -> dict:
    """Apply a structured change to a built reel's existing timeline.

    Stages a DUPLICATE beside the approved reel, conforms it, routes
    the qualified plan through `composed_edit.apply_composed_edit`,
    verifies by re-reading the track, guards the replacement and
    promotes by rename - retiring the replaced generation to the
    archive.  The approved timeline is never edited directly.

    `connect` is a seam for tests: `connect(resolve_name) ->
    project`.  `rederiver_override` is the same for the comp pass.
    """
    from library.tools import reel_signoff as _signoff

    started = time.time()
    reel = int((spec or {}).get("reel"))
    final = resolve_final_name(project_folder, reel)
    declared_drops = allow_drops
    if declared_drops is None:
        declared_drops = list((spec or {}).get("allow_drops") or ())
    declared_supersede = supersede
    if declared_supersede is None:
        declared_supersede = list((spec or {}).get("supersede") or ())

    _signoff.assert_declared(project_folder, final,
                             declared_supersede,
                             command="touch-reel")

    from library.tools.project_registry import get_project
    try:
        config = get_project(project_folder)
        resolve_name = (resolve_project_name
                        or config.resolve.project_name)
    except Exception:  # noqa: BLE001 - a path, not a slug
        import yaml as _yaml
        with open(os.path.join(project_folder, "project.yaml"),
                  encoding="utf-8") as handle:
            resolve_name = (resolve_project_name
                            or (_yaml.safe_load(handle).get("resolve")
                                or {}).get(
                                    "project_name",
                                    os.path.basename(project_folder)))

    if connect is None:
        from library.tools.reel_build import (
            _connect_resolve_project as _connect)
        connect = _connect
    return _apply_under_lease(
        project_folder, spec, final, resolve_name, declared_drops,
        declared_supersede, connect, rederiver_override, started)


def _apply_under_lease(project_folder: str, spec: Mapping, final: str,
                       resolve_name: str, declared_drops,
                       declared_supersede, connect,
                       rederiver_override, started: float) -> dict:
    from library.tools.resolve_lock import under_lease

    @under_lease(f"touch up {final}")
    def _guarded():
        return _apply_connected(
            project_folder, spec, final, resolve_name,
            declared_drops, declared_supersede, connect,
            rederiver_override, started)

    return _guarded()


def _apply_connected(project_folder: str, spec: Mapping, final: str,
                     resolve_name: str, declared_drops,
                     declared_supersede, connect,
                     rederiver_override, started: float) -> dict:
    import datetime as _dt

    from library.tools import reel_read as _read
    from library.tools.reel_build import (
        staging_name,
        timelines_to_replace,
    )

    receipt: dict = {
        "reel": int(spec.get("reel")),
        "final": final,
        "started": _dt.datetime.now(
            _dt.timezone.utc).isoformat(timespec="seconds"),
    }

    project = connect(resolve_name)
    pool = project.GetMediaPool()
    found = {t.GetName(): t for t in
             timelines_to_replace(project, {final})}
    if final not in found:
        raise TouchupRefused(
            f"REFUSING: no timeline called {final!r} is in Resolve "
            f"project {resolve_name!r}. A touchup edits the reel's "
            f"existing timeline - build it with `build-reels` first.")
    source = found[final]
    staging = staging_name(final)
    if timelines_to_replace(project, {staging}):
        raise TouchupRefused(
            f"REFUSING: a staging container {staging!r} from an "
            f"interrupted run is still in the project. Clear it in "
            f"Resolve before re-running; reusing it would grade one "
            f"run's content as another's.")

    tracks = _read.read_tracks(source)
    qualification = qualify(tracks, spec)
    receipt["gate"] = {
        "class": qualification.gate_class,
        "cost": qualification.cost_statement,
        "notes": list(qualification.notes),
    }
    # Said out loud, on the run that pays it - never just in the
    # receipt file.
    print(f"touch-reel {final}: {qualification.gate_class}",
          flush=True)
    print(f"  {qualification.cost_statement}", flush=True)
    for note in qualification.notes:
        print(f"  - {note}", flush=True)

    # The rederiver, chosen by the gate class - never by a flag.
    if rederiver_override is not None:
        rederiver = rederiver_override
        receipt["rederiver"] = "override (tests only)"
    elif qualification.gate_class == COMPOSED:
        rederiver = _NullRederiver(
            "no played length changes and no comp-bearing row "
            "touched, so there is nothing to re-derive")
        receipt["rederiver"] = "null (nothing to re-derive)"
    else:
        manifest = recorded_fusion_manifest(project_folder, final)
        if manifest is None:
            raise TouchupRefused(
                f"REFUSING: this change alters a played length and no "
                f"recorded fusion manifest for {final!r} is on disk. "
                f"Without the manifest there is no route to the comp "
                f"generator, and a trim without re-derivation renders "
                f"wrong on 99% of the clip's frames while looking "
                f"right. Rebuild the reel with `build-reels` - which "
                f"re-derives the manifest - instead.")
        check_manifest_matches(manifest, tracks)
        from library.tools.composed_edit import ReelLookRederiver
        rederiver = ReelLookRederiver(
            manifest, project_folder, resolve_name, staging)
        rederiver.expected_row_counts = _plan_post_edit_counts(
            tracks, qualification)
        receipt["rederiver"] = "reel_look.apply_comps over the " \
            "recorded fusion manifest"

    stage_started = time.time()
    staged = source.DuplicateTimeline(staging)
    if staged is None or staged.GetName() != staging:
        raise TouchupError(
            f"the staging copy did not land as {staging!r} - "
            f"nothing was edited and the approved timeline stands.")
    # `AppendToTimeline` writes to the CURRENT timeline, so the
    # staging must be the cursor before anything places - and a
    # foreign move mid-section must fail the touchup rather than
    # write onto the wrong timeline.  `cursor_fence` establishes the
    # cursor, re-reads it on exit, and raises on drift; the lease it
    # takes nests inside the outer one.
    from library.tools.resolve_lock import cursor_fence
    try:
        with cursor_fence(project, staged, f"touch up {final}"):
            _edit_staged(project_folder, project, pool, source,
                         staged, staging, qualification, rederiver,
                         receipt, declared_drops, declared_supersede,
                         final, stage_started)
    except Exception:
        # The approved timeline still stands under its own name; the
        # staging holds the half-done edit for diagnosis.  Delete
        # nothing: a failed touchup must never widen into a loss.
        receipt["staging_left_standing"] = staging
        raise
    receipt["seconds"] = round(time.time() - started, 3)
    _write_receipt(project_folder, final, receipt)
    return receipt


def _edit_staged(project_folder: str, project: Any, pool: Any,
                 source: Any, staged: Any, staging: str,
                 qualification: Qualification, rederiver: Any,
                 receipt: dict, declared_drops, declared_supersede,
                 final: str, stage_started: float) -> None:
    """Conform, compose, verify and promote the staging copy.

    Runs inside the cursor fence: the staging is the cursor for the
    whole section, and a foreign move fails the touchup rather than
    writing onto the wrong timeline.
    """
    from library.tools import reel_read as _read

    source_rows = _live_rows(source)
    staged_rows = _live_rows(staged)
    from library.tools.project_layout import Area, ProjectLayout
    comp_dir = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "touchup", _safe_slug(final))
    os.makedirs(comp_dir, exist_ok=True)
    conform_receipt = _ce.conform_comp_windows(
        source_rows, staged_rows, comp_dir=comp_dir)
    receipt["conform"] = {
        "compared": conform_receipt.get("compared"),
        "repaired": len(conform_receipt.get("repaired") or ()),
        "emptied": conform_receipt.get("emptied"),
    }
    # Staging is its own metered phase: copy + conform measured
    # 8.9-25.9s on the spike's reel, which can exceed a whole
    # rebuild's best case - so the receipt says what staging
    # cost separately from what the composition cost.
    receipt["stage_seconds"] = round(time.time() - stage_started,
                                     3)

    insertions = _resolve_insertions(
        pool, staged, qualification.insertions)
    changes = list(qualification.changes)

    # Vacate the removal spans FIRST, on the staging copy: the
    # composition only deletes what it re-places, so without
    # this the swap's insertion would collide with the item it
    # replaces and `AppendToTimeline` would silently place
    # nothing.  `_resolve_insertions` above already read the
    # carried treatments off these same handles, so nothing
    # needed dies with them.
    receipt["pre_delete"] = _pre_delete_removed(
        staged, qualification.removals)
    # The pre-delete moved nothing but re-seated every row it
    # touched: re-key the plan's positional indexes off a fresh
    # read before the composition addresses anything by them.
    changes = _rekey_changes(_read.read_tracks(staged),
                             qualification)
    # Grades ride from the APPROVED timeline, never from the staging
    # copy: a re-placed item comes back with one colour node where it
    # had eight, and the staging items are about to be deleted. The
    # approved reel is never mutated, so its handles stay live
    # through the restore, which judges every grade by read-back.
    grade_sources = _grade_sources_for(source, changes,
                                       qualification.moves)
    receipt["grades_carried"] = len(grade_sources)

    edit_started = time.time()
    composed = _ce.apply_composed_edit(
        timeline=staged, media_pool=pool, changes=changes,
        insertions=insertions,
        comp_dir=os.path.join(comp_dir, "comps"),
        withheld_dir=os.path.join(comp_dir, "withheld"),
        rederiver=rederiver,
        grade_sources=grade_sources,
        link_rows={},
        picture_row="V1")
    receipt["composed_seconds"] = round(time.time() - edit_started,
                                        3)
    receipt["composed"] = {
        "plan": composed.plan,
        "captured": composed.captured,
        "deleted": composed.deleted,
        "placed": composed.placed,
        "verified": composed.verified,
        "rederivation_required":
            composed.rederivation_required,
        "rederived": composed.rederived,
        "seconds": composed.seconds,
    }

    # Verify by RE-READING the track - never by a return value.
    # `apply_composed_edit` already verifies placement off a
    # re-read; this second read checks the whole row reads back
    # as the plan says it should.
    verify_started = time.time()
    staged_tracks = _read.read_tracks(staged)
    receipt["verification_read"] = _summarise_rows(staged_tracks)
    receipt["verify_seconds"] = round(
        time.time() - verify_started, 3)

    _promote(project_folder, project, pool, final, staging,
             declared_drops, declared_supersede, receipt)


def _summarise_rows(tracks: Sequence[Mapping]) -> dict:
    """`{row: [{start, duration}]}` - the verification read, pasted."""
    out = {}
    for track in tracks:
        row = _row_of(track["type"], int(track["index"]))
        out[row] = [{"start": int(c["record_in"]),
                     "duration": int(c["duration"])}
                    for c in (track.get("clips", []) or ())]
    return out


def _promote(project_folder: str, project: Any, pool: Any,
             final: str, staging: str, declared_drops,
             declared_supersede, receipt: dict) -> None:
    """Guard, swap names, carry markers, retire, close the signature."""
    from library.tools import reel_replace_guard as _guard
    from library.tools import reel_signoff as _signoff
    from library.tools.reel_build import (
        backup_name,
        timelines_to_replace,
    )

    staged_found = {t.GetName(): t for t in
                    timelines_to_replace(project, {staging})}
    originals = {t.GetName(): t for t in
                 timelines_to_replace(project, {final})}
    if staging not in staged_found or final not in originals:
        raise TouchupError(
            f"the staging {staging!r} or the approved {final!r} "
            f"vanished mid-touchup - nothing was renamed.")

    declared = _guard.parse_specs(declared_drops, [final])
    _signoff.assert_declared(project_folder, final,
                             declared_supersede,
                             command="touch-reel")
    incoming_rows = _guard.snapshot_timeline(staged_found[staging],
                                             staging, side="staged")
    retired_rows = _guard.snapshot_timeline(originals[final], final,
                                            side="retiring")
    receipt["replace_report"] = _guard.check_replacement(
        final, staging, retired_rows, incoming_rows,
        allowed=declared.get(final, ()))

    from library.tools import marker_carry as _markers
    carried_markers = None
    notes = _markers.read_markers(originals[final], final)
    if notes:
        # `final`, as in promotion: the re-pair binds by durable
        # identity over the reel name, so omitting it unpairs every
        # reply. See `reel_build` above.
        keep, lost = _markers.plan_carry(notes, staged_found[staging],
                                         final)
        _markers.report(final, keep, lost)
        carried_markers = {"carried": keep, "uncarried": lost}

    backup = backup_name(final)
    if not originals[final].SetName(backup):
        raise TouchupError(
            f"Resolve would not rename {final!r} aside to "
            f"{backup!r}. Nothing was deleted and the staging "
            f"{staging!r} is untouched.")
    if not staged_found[staging].SetName(final):
        # The approved content is safe under the backup name; say so
        # by name rather than leaving the operator to infer it.
        raise TouchupError(
            f"Resolve would not rename staging {staging!r} to "
            f"{final!r}. The approved content is safe under "
            f"{backup!r} - rename it back in Resolve and re-run.")
    receipt["promoted"] = {"staging": staging, "final": final,
                           "backup": backup}
    if carried_markers and carried_markers["carried"]:
        declined = _markers.place(staged_found[staging],
                                  carried_markers["carried"])
        carried_markers["declined"] = declined
    receipt["markers"] = carried_markers or {"carried": [],
                                             "uncarried": []}

    # Retire, never delete: the replaced generation goes to the
    # archive bin, bounded by reels rather than rounds - the same
    # rule promotion follows.
    from library.tools import reel_retirement as _retire
    from library.tools import round_version as _rounds
    recorded = _rounds.discover(project_folder)
    current_round = recorded[-1]["round"] if recorded else 1
    backup_objects = {t.GetName(): t for t in timelines_to_replace(
        project, {backup})}
    by_final = {final: _retire.retiring_round(recorded, final,
                                              current_round)}
    retirement = _retire.retire_timelines(project, pool,
                                          {final: backup_objects[backup]}
                                          if backup in backup_objects
                                          else {},
                                          by_final)
    receipt["retirement"] = _retire.render(retirement)

    for entry in (_signoff.base_name(final),):
        if entry not in _signoff.parse_supersede(declared_supersede):
            continue
        ended = _signoff.supersede(project_folder, final,
                                   round_number=by_final.get(final))
        if ended:
            receipt["signoff_superseded"] = ended

    # Close the carried signature: the approved timeline under its
    # final name is new content, so the next build must read it back
    # NOW rather than compare against the replaced generation.
    try:
        from library.tools import reel_rebuild_need as _need_record
        from library.tools.plan_provenance import (
            record_carried_digests as _record_carried)
        live = None
        for index in range(1, (project.GetTimelineCount() or 0) + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline is not None and timeline.GetName() == final:
                live = timeline
        if live is not None:
            digest = _need_record.carried_digest_live(project, live)
            if digest:
                import os as _os
                review_dir = _os.path.join(project_folder,
                                           "pipeline_output", "review")
                _record_carried(review_dir, {final: digest})
                receipt["signature_closed"] = True
    except Exception as signature_failed:  # noqa: BLE001 - never fatal
        receipt["signature_closed"] = (
            f"not closed ({signature_failed}) - the next build will "
            f"place this reel again rather than assume")


def _safe_slug(text: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


def _write_receipt(project_folder: str, final: str,
                   receipt: dict) -> str:
    """The touchup's own account of itself, readable afterwards."""
    import datetime as _dt
    import json as _json

    review_dir = os.path.join(project_folder, "pipeline_output",
                              "review")
    os.makedirs(review_dir, exist_ok=True)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(review_dir,
                        f"touchup_{_safe_slug(final)}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        _json.dump(receipt, handle, indent=2, default=str)
    return path


__all__ = [
    "COMPOSED",
    "COMPOSED_WITH_REDERIVATION",
    "Qualification",
    "TouchupError",
    "TouchupRefused",
    "_NullRederiver",
    "apply_touchup",
    "check_manifest_matches",
    "pool_item_for_path",
    "qualify",
    "recorded_fusion_manifest",
    "resolve_final_name",
]
