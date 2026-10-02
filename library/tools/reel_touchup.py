"""A change to the timeline in front of you, instead of a rebuild of it.

`manage_project.py build-reels` stages a fresh timeline and promotes it
over the old one; this module changes the built reel instead, through
`library/tools/composed_edit.py`'s in-place editor.

What the caller states, and what it does not
--------------------------------------------
The caller states the change STRUCTURALLY: which reel, which item,
what changes - one of the nine ops below.  Mapping a captain's
natural-language note onto such a change is the NEXT task and is
explicitly not this one; this module is the mechanism that task will
call.

The nine ops
------------
The first five delete and re-place through the composition; `add_row`
makes a declared row; `set_enabled`, `set_properties` and
`entry_motion` are IN-PLACE: they write onto the staged item itself - no delete,
no place - and ride `Qualification.in_place` rather than
changes/insertions/removals.  They run before the composition off the
same staging, so a capture taken after them carries them, and a spec
mixing them with composition ops stays servable: one source item, one
edit (`_check_single_claim`).

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
  in the receipt - carried, never invented (AGENTS.md 10.5) - unless
  the edit declares `properties` for a file cut to a different canvas.
  Its source trim is carried too, unless the edit explicitly declares
  `left_offset`: rendered overlays can include head handles, and
  replacing a trimmed item with one starting at frame 0 exposes them.
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
- `set_properties` - IN-PLACE.  A property mapping written onto an
  already-placed item with `composed_edit.set_properties` and judged
  by read-back - no delete, no place.  Any row: nothing is vacated
  and no comp is disturbed.  A key `set_properties` would silently
  skip (read-only, None, a `<placeholder>`) REFUSES here instead,
  naming it: a spec asking to set `Resolution` is a caller error,
  not a no-op to wave through.
- `add_row` - a new NAMED video row on top of the stack, made on the
  staging copy and read back; later edits in the same spec may place
  onto it. A reel already carrying a row of that name refuses.
- `set_enabled` - IN-PLACE. An overlay item (V3+) switched on or off;
  it keeps its span, file and treatment, so disabling a graphic never
  deletes it.
- `entry_motion` - IN-PLACE.  An entrance and/or exit fade authored
  as a Fusion comp (`fusion.comp_builder.build_effect_comp` over the
  `fade_in_frames`/`fade_out_frames` keys, the same dispatch the comp
  pass reads) and imported onto the staged item, then conformed by
  the pass's own `comp_media_window.conform_item` and verified by
  re-read.  Overlay rows (V3+ video) only: V1/V2 are comp-bearing
  rows whose treatments the comp pass owns (same boundary as
  `add_overlay`), audio rows carry no Fusion comps, and an item
  already carrying a drawing comp refuses - a second treatment the
  recorded manifest does not know would be dropped silently by the
  next re-derivation.  The ramp must fit inside what the item plays
  (`fade_in + fade_out <= duration - 1`), else the effect holds
  across the whole clip (`fusion.played_window`).

The qualification gate
----------------------
`qualify` classifies a change before anything is staged, into:

- `composed` - no clip's played length changes.  No comp is re-derived
  and no comp pass runs.  Whether that beats a rebuild is MEASURED per
  edit in the receipt, never quoted; if it does not, that is reported as
  a measured non-finding, not shipped as a speed improvement.
- `composed_with_rederivation` - a played length changes.  Routed
  through the same composition, but ONLY with the real comp pass
  (`ReelLookRederiver` over the recorded fusion manifest) and ONLY with
  its cost said out loud: the comp pass is most of what a rebuild
  costs.  Never presented as a quick refresh.
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
`tests/unit/resolve/test_composed_edit.py` attempts the bypass eight ways
and must keep passing.

A pixel swap is still delete-and-place: Resolve has no
`SetMediaPoolItem`, so changing what an item plays means deleting it and
placing a new one.  "Cheap" for a swap means "no comp pass", never "no
delete".

"Staged", not "in place on the captain's timeline"
--------------------------------------------------
Build beside the captain (ruling 2026-09-09).  "In place" here means
"onto the existing built reel rather than a from-scratch rebuild", NOT
onto whatever the captain is reviewing without a copy.  `apply_touchup`
duplicates the approved timeline into `reel_build.staging_name` (the
same staging the build uses, so the bin layout and the stale-debris
refusal both recognise it), conforms it against the source, edits the
copy, verifies by re-reading the track, and only then swaps the names -
with the replace guard, the sign-off check, marker carry, the undo
journal and the carried signature close, mirroring
`promote_staged_reels` phases 0-2.

The way back is the JOURNAL, not a copy (captain, D5, 2026-09-23).
`undo_journal.open_entry` reads the approved timeline before anything is
staged; `close_entry` reads the promoted one before the replaced
generation is deleted; `ren undo` reverses the touch in place from the
two.  A removal of a GRADED item refuses here, like a graded swap: no
script can record a grade, so the journal could not put it back.

Grades ride from the APPROVED timeline: a re-placed item comes back
with one colour node where it had eight, so every re-placed change
carries its grade from the live item on the untouched reel
(`_grade_sources_for`, resolved by source row + pre-edit record
frame and judged by read-back).

The measured staging, mechanism, comp-pass and rebuild costs behind the
gate, and the reading of `reel_rebuild_need`'s "not faster" finding:
docs/evidence/reel_touchup.md.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from library.tools import composed_edit as _ce
from library.tools import dirty_regions as _dirty_regions
from library.tools.execution.fusion_tracks import FUSION_COMP_TRACKS
from library.tools.ren_refusal import RenRefusal

# ── Refusals ─────────────────────────────────────────────────────────
#
# Every one stops the touchup by name, before anything is staged.


class TouchupRefused(RenRefusal):
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
    #: IN-PLACE edits - `set_properties` and `entry_motion` - as
    #: `{"kind", "row", "item_index", "record_frame", ...}` dicts.
    #: They write onto the staged item itself (no delete, no place)
    #: and run before the composition, so the capture carries them.
    in_place: list = field(default_factory=list)
    #: Cross-row moves, stated with both ends: `ItemChange` carries
    #: the target row but the source index, so the overlap check and
    #: the count planner read the move here rather than inferring it.
    moves: list = field(default_factory=list)
    #: Rows the touch ADDS on top of the video stack (`add_row`), as
    #: `{"row": "V9", "name": "Post Header"}` in the order they are made.
    new_rows: list = field(default_factory=list)
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
        f"no item at {row}[{item_index}] on this reel",
        "the change names an item the timeline does not have, so "
        "there is nothing to qualify",
        "re-read the reel and state the item by its current position "
        "in the --edits JSON, then re-run `ren touch`")


def _track_exists(tracks: Sequence[Mapping], row: str) -> bool:
    want = str(row).upper()
    return any(_row_of(t["type"], int(t["index"])) == want for t in tracks)


def _is_caption_row(tracks: Sequence[Mapping], row: str) -> bool:
    want = str(row).upper()
    return any(
        _row_of(track["type"], int(track["index"])) == want
        and str(track.get("name") or "").strip().casefold()
        in {"subtitles", "captions"}
        for track in tracks)


def _video_rows_of(tracks: Sequence[Mapping]) -> list:
    """Every video row the track read carries, in track-index order."""
    rows = []
    for track in tracks or ():
        try:
            track_type = track["type"]
            track_index = int(track["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if not str(track_type).lower().startswith("v"):
            continue
        row = _row_of(track_type, track_index)
        if row not in rows:
            rows.append(row)
    return rows


def _named_hits_on_row(tracks: Sequence[Mapping], row: str,
                       old_clip: str) -> list:
    """`(item_index, clip)` pairs named `old_clip` on `row`, in order."""
    hits = []
    for track in tracks or ():
        try:
            track_row = _row_of(track["type"], int(track["index"]))
        except (KeyError, TypeError, ValueError):
            continue
        if track_row != str(row).upper():
            continue
        for index, clip in enumerate(track.get("clips") or ()):
            if str((clip or {}).get("name") or "") == old_clip:
                hits.append((index, clip))
    return hits


def locate_named_clip(tracks: Sequence[Mapping], old_clip: str,
                      row: str = "") -> tuple:
    """The ONE item named `old_clip`, found across the video rows.

    `row` is an optional narrowing, never a requirement: empty means the
    timeline's video rows are all searched. A narrowed row searches only
    that row. Zero matches, or more than one, refuses naming which rows
    were searched and what was found - so the caller checks the ROW
    instead of re-checking a name that was right all along. Returns
    `(found_row, item_index, clip)`.
    """
    video_rows = _video_rows_of(tracks)
    if row:
        want = str(row).upper()
        searched = [want]
        unsearched = [entry for entry in video_rows if entry != want]
        hits = [(want, index, clip) for index, clip
                in _named_hits_on_row(tracks, want, old_clip)]
        if not hits:
            elsewhere = sorted({entry for entry in video_rows
                                if _named_hits_on_row(
                                    tracks, entry, old_clip)})
            hint = (f" the name does appear on "
                    f"{', '.join(elsewhere)} - which is why the row, "
                    f"not the name, is what to re-check."
                    if elsewhere else " the name appears on none of "
                    "them either, so re-check both the row and the "
                    "name.")
            raise TouchupRefused(
                f"no item named {old_clip!r} on {want}",
                f"looked in {want}; did not search "
                f"{', '.join(unsearched) or 'no other video row'}.{hint}",
                "re-check the row and the name in the --edits JSON "
                "against the live timeline, then re-run `ren touch`")
        if len(hits) > 1:
            positions = ", ".join(
                f"{found}[{index}] @{clip.get('record_in')}.."
                f"{clip.get('record_out')}"
                for found, index, clip in hits
            )
            raise TouchupRefused(
                f"{old_clip!r} appears {len(hits)} times on "
                f"{want} ({positions})",
                f"one spec cannot mean {len(hits)} items",
                "state which position the swap addresses in the --edits "
                "JSON, then re-run `ren touch`")
        (found_row, index, clip) = hits[0]
        return (found_row, int(index), clip)
    searched = list(video_rows)
    if not searched:
        raise TouchupRefused(
            f"no item named {old_clip!r} anywhere",
            "the track read carries no video row to search, so there is "
            "nothing to address",
            "re-check the reel's rows (`ren drift <project>` shows the "
            "live timeline), then re-run `ren touch`")
    hits = []
    for entry in searched:
        hits.extend((entry, index, clip) for index, clip
                    in _named_hits_on_row(tracks, entry, old_clip))
    if not hits:
        raise TouchupRefused(
            f"no item named {old_clip!r} on any of the "
            f"searched video rows ({', '.join(searched)})",
            "the swap names an item the live timeline does not have, so "
            "there is nothing to address",
            "re-check the clip name against the live timeline, then "
            "re-run `ren touch`")
    if len(hits) > 1:
        positions = ", ".join(
            f"{found}[{index}] @{clip.get('record_in')}.."
            f"{clip.get('record_out')}"
            for found, index, clip in hits
        )
        raise TouchupRefused(
            f"{old_clip!r} appears {len(hits)} times across "
            f"the searched video rows ({positions})",
            f"one spec cannot mean {len(hits)} items",
            "narrow with `row` or state which position the swap "
            "addresses in the --edits JSON, then re-run `ren touch`")
    (found_row, index, clip) = hits[0]
    return (found_row, int(index), clip)


def swap_spec_for_tracks(tracks: Sequence[Mapping], *, reel: int,
                         old_clip: str, new_media: str,
                         row: str = "") -> dict:
    """The `swap_pixels` spec for a named clip, row located, not stated.

    Name-to-position happens here, once, off the same track read the
    gate qualifies - so the position cannot disagree with the
    qualification.
    """
    (found_row, index, _clip) = locate_named_clip(
        tracks, old_clip, row=row)
    return {
        "reel": int(reel),
        "edits": [
            {
                "op": "swap_pixels",
                "row": found_row,
                "item": int(index),
                "media": str(new_media),
            }
        ],
    }


def reel_numbers(project_folder: str) -> list:
    """Every reel number the plan names, in plan order."""
    from library.tools.reel_proposal import proposal_path, read_proposal

    return [int(moment.number) for moment
            in read_proposal(str(proposal_path(project_folder)))]


def touchup_all_reels(project_folder: str, *, old_clip: str = "",
                      new_media: str = "", row: str = "",
                      reels=None, reader=None, applier=None,
                      allow_drops=None, supersede=None,
                      accept_editor_changes=None,
                      spec_for=None) -> dict:
    """The same named-clip swap on every reel, reporting per reel.

    `spec_for(tracks, number) -> spec` replaces the swap with any other
    per-reel change (`reel_post_header.touch_spec` is one); the reading,
    the batch and the per-reel refusals stay this function's.

    One reel's refusal never stops the rest: a refused reel is reported
    with its reason alongside the receipts of the reels that landed.
    `reader(reel, final_name) -> tracks` and
    `applier(project_folder, spec) -> receipt` are seams for tests; the
    defaults read the live timeline and execute the touchup for real.
    """
    numbers = ([int(entry) for entry in reels]
               if reels is not None else reel_numbers(project_folder))
    if reader is None:
        def reader(number, final_name, _folder=project_folder):
            return _live_tracks_for_reel(_folder, int(number),
                                         final_name)
    if applier is None:
        def applier(folder, spec, _real=apply_touchup):
            return _real(folder, spec)
    # One act across reels: every entry this call journals shares the
    # batch, and `ren undo` reverses them together.
    from library.tools.undo_journal import new_batch_id
    batch = new_batch_id()
    per_reel = []
    for number in numbers:
        try:
            final = resolve_final_name(project_folder, int(number))
        except TouchupRefused as refused:
            per_reel.append({"reel": int(number), "final": "",
                             "ok": False, "refused": str(refused)})
            continue
        try:
            tracks = reader(int(number), final)
        except TouchupRefused as refused:
            per_reel.append({"reel": int(number), "final": final,
                             "ok": False, "refused": str(refused)})
            continue
        except Exception as exc:  # noqa: BLE001 - one reel never stops rest
            per_reel.append({"reel": int(number), "final": final,
                             "ok": False,
                             "refused": f"the live read failed ({exc!r})"})
            continue
        try:
            spec = (spec_for(tracks, int(number)) if spec_for is not None
                    else swap_spec_for_tracks(
                        tracks, reel=int(number), old_clip=old_clip,
                        new_media=new_media, row=row))
        except TouchupRefused as refused:
            per_reel.append({"reel": int(number), "final": final,
                             "ok": False, "refused": str(refused)})
            continue
        spec = dict(spec)
        spec["batch"] = batch
        if allow_drops is not None:
            spec["allow_drops"] = list(allow_drops)
        if supersede is not None:
            spec["supersede"] = list(supersede)
        if accept_editor_changes is not None:
            spec["accept_editor_changes"] = (
                [accept_editor_changes]
                if isinstance(accept_editor_changes, (str, int))
                else list(accept_editor_changes))
        try:
            receipt = applier(project_folder, spec)
        except (TouchupRefused, TouchupError) as refused:
            per_reel.append({"reel": int(number), "final": final,
                             "ok": False, "refused": str(refused)})
            continue
        except Exception as exc:  # noqa: BLE001 - one reel never stops rest
            per_reel.append({"reel": int(number), "final": final,
                             "ok": False,
                             "refused": f"the touchup failed ({exc!r})"})
            continue
        per_reel.append({"reel": int(number), "final": final,
                         "ok": True, "refused": "",
                         "receipt": receipt})
    landed = sum(1 for entry in per_reel if entry["ok"])
    return {"reels": per_reel, "landed": landed,
            "refused": len(per_reel) - landed,
            "ok": landed == len(per_reel)}


def _live_tracks_for_reel(project_folder: str, reel: int,
                          final_name: str) -> list:
    """Tracks of the EXACT-named reel timeline, read live in Resolve."""
    from library.tools import reel_read as _read
    from library.tools.project_registry import get_project
    from library.tools.reel_build import _connect_resolve_project as _connect
    from library.tools.reel_build import timelines_to_replace
    from library.tools.resolve_lock import cursor_excursion, resolve_lease

    try:
        resolve_name = get_project(project_folder).resolve.project_name
    except Exception:  # noqa: BLE001 - resolve the binding off disk instead
        import yaml as _yaml

        with open(os.path.join(project_folder, "project.yaml"),
                  encoding="utf-8") as handle:
            resolve_name = ((_yaml.safe_load(handle).get("resolve")
                             or {}).get("project_name", ""))
    # The connection handshake itself must happen after the lease is
    # acquired. Pan/Tilt readings also depend on the CURRENT timeline,
    # so read_tracks must run inside a cursor excursion to this reel.
    # The excursion restores the captain's entry timeline and timecode.
    with resolve_lease(f"read touchup {final_name}", exclusive=True):
        project = _connect(resolve_name or "")
        found = {timeline.GetName(): timeline for timeline
                 in timelines_to_replace(project, {final_name})}
        if final_name not in found:
            raise TouchupRefused(
                f"no timeline called {final_name!r} is in the "
                f"open Resolve project",
                "a touchup edits the reel's existing timeline, and there "
                "is none to edit",
                "build it first (`ren build <project>`), then touch it up")
        with cursor_excursion(project, found[final_name],
                              f"read touchup {final_name}"):
            return _read.read_tracks(found[final_name],
                                     resolve_project=project)


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
            f"{what} on {row} at {start} for {duration}f "
            f"collides with a live item",
            "`AppendToTimeline` silently places NOTHING where it would "
            "collide, so a touchup never asks it to",
            "state a free span in the --edits JSON, then re-run "
            "`ren touch`")


def qualify(tracks: Sequence[Mapping], spec: Mapping) -> Qualification:
    """Classify a structured change. Pure: reads nothing but `tracks`.

    `spec` is `{"reel": N, "edits": [...], "exclude": [[row, idx]]}`.
    Each edit carries `op` - `move`, `swap_pixels`, `add_overlay`,
    `remove_overlay`, `retime`, `set_properties` or `entry_motion` -
    and the fields that op documents in the module docstring.
    Raises `TouchupRefused` for anything unclassifiable, naming why.
    Never falls back to a rebuild.
    """
    edits = list((spec or {}).get("edits") or ())
    if not edits:
        raise TouchupRefused(
            "the change names no edits",
            "a touchup with nothing to do is not a no-op to wave "
            "through - it is a caller that failed to say what it wants",
            "pass the change as --edits JSON or --edits-file PATH to "
            "`ren touch`")
    exclude = [(str(r), int(i)) for r, i in
               ((spec or {}).get("exclude") or ())]
    # A copy: `add_row` appends the row it will make, so later edits in
    # the same spec can place onto it, without touching the caller's read.
    tracks = list(tracks or ())
    spans = _spans_of(tracks)
    notes: list = []
    changes: list = []
    insertions: list = []
    removals: list = []
    moves: list = []
    in_place: list = []
    length_changing = False

    for position, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise TouchupRefused(
                f"edit {position} is not a mapping ({edit!r})",
                "the gate classifies structure, and there is no "
                "structure here to classify",
                f"write edit {position} as an object with an `op` in the "
                f"--edits JSON, then re-run `ren touch`")
        op = str(edit.get("op") or "")
        handler = _OP_HANDLERS.get(op)
        if handler is None:
            raise TouchupRefused(
                f"edit {position} names op {op!r}",
                f"the gate knows {len(_OP_HANDLERS)} ops: {sorted(_OP_HANDLERS)}. "
                f"Anything else is unclassifiable - the gate is extended "
                f"deliberately, never by guessing what {op!r} means",
                f"use one of {sorted(_OP_HANDLERS)} as the op for edit "
                f"{position}, then re-run `ren touch`")
        length_changing = handler(
            edit, position, tracks, spans, changes, insertions,
            removals, moves, exclude, notes, in_place) or length_changing

    _prune_shadowed_rewrites(changes, removals, moves, in_place)
    _check_post_edit_overlaps(tracks, changes, insertions, removals,
                              moves)
    _check_single_claim(changes, removals, moves, in_place)

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
                         moves=moves, in_place=in_place,
                         new_rows=[{"row": t["row"], "name": t["name"]}
                                   for t in tracks if t.get("added")],
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
                    f"the plan overlaps on {row}: "
                    f"{first[2]} @{first[0]}..{first[1]} and "
                    f"{second[2]} @{second[0]}..{second[1]}",
                    "`AppendToTimeline` silently places nothing on "
                    "collision, so an overlapping plan never "
                    "reaches the delete",
                    "state the ordering explicitly in the --edits JSON, "
                    "then re-run `ren touch`")


def _prune_shadowed_rewrites(changes: list,
                               removals: Sequence[dict],
                               moves: Sequence[dict],
                               in_place: Sequence[dict]) -> None:
    """Drop rewrites of items another edit already claims.

    A remove plans zero-length rewrites for every kept item on its
    row, but a later edit in the same spec may move one of those
    items - whose placement the move then verifies - or write onto
    one in place.  Re-placing it at its old span too would put it on
    the timeline twice (or churn a re-place the in-place write makes
    needless: the composition's capture reads the staged item AFTER
    the in-place write, so a pruned rewrite loses nothing).  A
    rewrite is identified structurally (a SHIFT to its own span),
    so only those go; a ripple shift to a NEW span still conflicts
    and `_check_single_claim` refuses it below.
    """
    claimed = {(str(r.get("row")).upper(), int(r.get("item_index")))
               for r in removals}
    claimed |= {(str(m.get("from_row")).upper(),
                 int(m.get("from_index"))) for m in moves}
    claimed |= {(str(e.get("row")).upper(), int(e.get("item_index")))
                for e in in_place}
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
                          moves: Sequence[dict],
                          in_place: Sequence[dict]) -> None:
    """One source item, one edit. Two edits addressing the same item -
    a rewrite of V4[2] plus a move of V4[2] - would place it twice.
    The overlap check cannot see that (the spans differ), so this
    refuses it by position before anything is staged.  An in-place
    write claims its item the same way: a move or a ripple shift of
    an item another edit writes onto would re-place it around the
    write, and two in-place writes on one item would need a merge
    order the gate will not invent - state those as two touchups.
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
    for entry in in_place:
        key = (str(entry.get("row")).upper(),
               int(entry.get("item_index")))
        claims.setdefault(key, []).append(str(entry.get("kind")))
    doubled = {key: kinds for key, kinds in claims.items()
               if len(kinds) > 1}
    if doubled:
        raise TouchupRefused(
            f"two edits address the same item: {doubled}",
            "one of them would place it twice",
            "state the change as one edit per item in the --edits JSON, "
            "then re-run `ren touch`")


# ── The nine ops ─────────────────────────────────────────────────────
#
# Each takes the edit, its position, the full read, the live spans and
# the plan under construction.  Returns True when it alters a played
# length.  Every one raises `TouchupRefused` for what it cannot
# classify.  The last argument, `in_place`, is the list the two
# IN-PLACE ops (`set_properties`, `entry_motion`) record into - the
# composition ops ignore it.


def _op_move(edit, position, tracks, spans, changes, insertions,
             removals, moves, exclude, notes, in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    to_row = str(edit.get("to_row") or row).upper()
    to_record = edit.get("to_record")
    if not row or item_index is None or to_record is None:
        raise TouchupRefused(
            f"edit {position} (`move`) needs `row`, `item` "
            f"and `to_record` (got {dict(edit)!r})",
            "a move to nowhere is unclassifiable",
            f"give edit {position} `row`, `item` and `to_record` in the "
            f"--edits JSON, then re-run `ren touch`")
    if _is_continuous_row(row):
        raise TouchupRefused(
            f"edit {position} moves {row}[{item_index}] "
            f"away, and {row} is continuous program",
            "vacating it leaves black or silence",
            "state the covering change (what plays those frames instead) "
            "in the --edits JSON, then re-run `ren touch`")
    if not _track_exists(tracks, to_row):
        raise TouchupRefused(
            f"edit {position} moves to row {to_row}, and "
            f"this reel has no such row",
            "the gate never invents a track",
            "name a row the timeline already carries in the --edits "
            "JSON, then re-run `ren touch`")
    if to_row != row:
        raise TouchupRefused(
            f"edit {position} moves {row}[{item_index}] to {to_row}",
            "a move across rows is not a composed edit: the composition "
            "addresses what it deletes by (row, position), so a "
            "cross-row plan would capture whatever sits at that "
            "position on the TARGET row and delete a bystander while "
            "duplicating the moved item",
            f"state it as two edits - `remove_overlay` from {row} "
            f"plus `add_overlay` on {to_row} carrying the treatment "
            f"explicitly - then re-run `ren touch`")
    clip = _find_clip(tracks, row, int(item_index))
    left_offset = clip.get("left_offset")
    if left_offset is None and _is_caption_row(tracks, row):
        raise TouchupRefused(
            f"edit {position} (`move`) moves caption {row}[{item_index}], "
            "and its `left_offset` was unreadable",
            "the replacement item would default to frame 0 and expose any "
            "transparent preroll",
            "re-read the reel so the caption's source trim is known, then "
            "re-run `ren touch`")
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
        duration=duration, left_offset=int(left_offset or 0),
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
                    removals, moves, exclude, notes, in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    media = edit.get("media")
    if not row or item_index is None or not media:
        raise TouchupRefused(
            f"edit {position} (`swap_pixels`) needs `row`, "
            f"`item` and `media` (got {dict(edit)!r})",
            "a swap naming no item or no replacement cannot be staged",
            f"give edit {position} `row`, `item` and `media` in the "
            f"--edits JSON, then re-run `ren touch`")
    clip = _find_clip(tracks, row, int(item_index))
    if _ce.treatment_comps(clip):
        raise TouchupRefused(
            f"edit {position} swaps the pixels of "
            f"{row}[{item_index}], and that item carries a drawing "
            f"Fusion comp",
            "a pool-item swap cannot carry a treatment across - there "
            "is no route from the old comp to the new item",
            "rebuild the reel (`ren build <project>`) instead of "
            "touching it up")
    # A replacement is a NEW timeline item. Preserve the old item's
    # source trim unless the edit explicitly chooses another one: a
    # caption render carries head/tail handles, and resetting a trimmed
    # caption to frame 0 exposes its head handle as a visible delay.
    # The touchup log records these as pixel swaps, but the trim is part
    # of how those pixels are timed.
    if "left_offset" in edit:
        left_offset = edit["left_offset"]
    else:
        left_offset = clip.get("left_offset")
    if left_offset is None:
        raise TouchupRefused(
            f"edit {position} (`swap_pixels`) replaces {row}[{item_index}], "
            "and the read did not say which source frame the item starts "
            "on (`left_offset` unreadable)",
            "a replacement timeline item does not inherit the old "
            "item's source trim, so a rendered handle could become "
            "visible and move its content",
            f"re-read the reel or state `left_offset` explicitly in edit "
            f"{position}, then re-run `ren touch`")
    try:
        left_offset = int(left_offset)
    except (TypeError, ValueError) as exc:
        raise TouchupRefused(
            f"edit {position} (`swap_pixels`) has invalid `left_offset` "
            f"{left_offset!r}",
            "the replacement's source frame must be a whole frame",
            f"state an integer `left_offset` in edit {position}, then "
            f"re-run `ren touch`") from exc
    duration = int(edit.get("duration") or clip["duration"])
    removals.append({"row": row, "item_index": int(item_index),
                     "record_frame": int(clip["record_in"]),
                     "duration": int(clip["duration"]),
                     "why": f"edit {position} (`swap_pixels`)"})
    # The replaced item's transform rides across, unless the edit
    # declares the new one: a file cut to a different canvas (a
    # full-frame graphic re-rendered tight) is drawn somewhere else by
    # the old item's Pan/Tilt.
    properties = edit.get("properties")
    insertions.append(_PendingSwap(
        row=row, record_frame=int(clip["record_in"]),
        duration=duration, media=str(media),
        left_offset=left_offset,
        carry_from=None if properties is not None else (row,
                                                        int(item_index)),
        declared_properties=(dict(properties) if properties is not None
                             else None),
        name=str(edit.get("name") or os.path.basename(str(media))),
        position=position))
    notes.append(f"edit {position}: swap {row}[{item_index}] pixels "
                 f"for {media} at @{clip['record_in']} ({duration}f)")
    return False


def _op_add_overlay(edit, position, tracks, spans, changes, insertions,
                    removals, moves, exclude, notes, in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    media = edit.get("media")
    record = edit.get("record")
    duration = edit.get("duration")
    properties = edit.get("properties")
    if (not row or not media or record is None or duration is None):
        raise TouchupRefused(
            f"edit {position} (`add_overlay`) needs `row`, "
            f"`media`, `record` and `duration` (got {dict(edit)!r})",
            "an overlay naming no row, media, position or length cannot "
            "be staged",
            f"give edit {position} all four keys in the --edits JSON, "
            f"then re-run `ren touch`")
    if properties is None:
        raise TouchupRefused(
            f"edit {position} (`add_overlay`) declares no `properties`",
            "a placed item comes back at IDENTITY, so it would render "
            "at a framing nobody chose",
            f"declare `properties` on edit {position} (`{{}}` if identity "
            f"is what is wanted), then re-run `ren touch`")
    if not _track_exists(tracks, row):
        raise TouchupRefused(
            f"edit {position} adds to row {row}, and this "
            f"reel has no such row",
            "the gate never invents a track",
            "name a row the timeline already carries in the --edits "
            "JSON, then re-run `ren touch`")
    left_offset = edit.get("left_offset")
    if left_offset is None and _is_caption_row(tracks, row):
        raise TouchupRefused(
            f"edit {position} (`add_overlay`) adds to caption row {row} "
            "without an explicit `left_offset`",
            "the new item would default to frame 0 and expose any "
            "transparent preroll",
            f"state the caption render's source trim as `left_offset` in "
            f"edit {position}, then re-run `ren touch`")
    try:
        left_offset = int(left_offset or 0)
    except (TypeError, ValueError) as exc:
        raise TouchupRefused(
            f"edit {position} (`add_overlay`) has invalid `left_offset` "
            f"{left_offset!r}",
            "the source trim must be a whole frame",
            f"state an integer `left_offset` in edit {position}, then "
            f"re-run `ren touch`") from exc
    if row in COMP_ROWS:
        raise TouchupRefused(
            f"edit {position} adds a new item on {row}, "
            f"and {row} is a comp-bearing row",
            "the pass writes per-clip Fusion comps there "
            "(`execution/fusion_tracks.FUSION_COMP_TRACKS`), so every "
            "clip around it carries a treatment. A newly placed item "
            "has no manifest spec for the pass to key one to, so it "
            "would land with no comp beside treated neighbours - and "
            "render, looking like a choice",
            "rebuild the reel with `ren build <project>` "
            "(manage_project.py build-reels), which plans the "
            "new clip with its treatment, instead of touching it up")
    _check_free(spans.get(row, []), row, int(record), int(duration),
                what=f"edit {position} (`add_overlay`)")
    insertions.append(_PendingSwap(
        row=row, record_frame=int(record), duration=int(duration),
        media=str(media), left_offset=left_offset,
        carry_from=None, declared_properties=dict(properties),
        name=str(edit.get("name") or ""), position=position))
    notes.append(f"edit {position}: add {row}@{record} ({duration}f) "
                 f"from {media}")
    return False


def _op_remove_overlay(edit, position, tracks, spans, changes,
                       insertions, removals, moves, exclude, notes,
                       in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    if not row or item_index is None:
        raise TouchupRefused(
            f"edit {position} (`remove_overlay`) needs "
            f"`row` and `item` (got {dict(edit)!r})",
            "a removal naming no item cannot be staged",
            f"give edit {position} `row` and `item` in the --edits JSON, "
            f"then re-run `ren touch`")
    if _is_continuous_row(row):
        raise TouchupRefused(
            f"edit {position} removes {row}[{item_index}], "
            f"and {row} is continuous program",
            "removing it leaves black or silence",
            "state the covering change in the --edits JSON, then re-run "
            "`ren touch`")
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
            other_left_offset = other.get("left_offset")
            if (other_left_offset is None
                    and _is_caption_row(tracks, row)):
                raise TouchupRefused(
                    f"edit {position} (`remove_overlay`) would re-place "
                    f"caption {row}[{other_index}], and its `left_offset` "
                    "was unreadable",
                    "the kept item would default to frame 0 and expose "
                    "any transparent preroll",
                    "re-read the reel so every caption source trim is "
                    "known, then re-run `ren touch`")
            changes.append(_ce.ItemChange(
                track_type=track["type"],
                track_index=int(track["index"]),
                item_index=int(other_index),
                record_frame=int(other["record_in"]),
                duration=int(other["duration"]),
                left_offset=int(other_left_offset or 0),
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
               removals, moves, exclude, notes, in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    duration = edit.get("duration")
    if not row or item_index is None or duration is None:
        raise TouchupRefused(
            f"edit {position} (`retime`) needs `row`, "
            f"`item` and `duration` (got {dict(edit)!r})",
            "a retime naming no item or no length cannot be staged",
            f"give edit {position} `row`, `item` and `duration` in the "
            f"--edits JSON, then re-run `ren touch`")
    clip = _find_clip(tracks, row, int(item_index))
    old = int(clip["duration"])
    new = int(duration)
    if new <= 0:
        raise TouchupRefused(
            f"edit {position} retimes {row}[{item_index}] "
            f"to {new}f",
            "a zero or negative played length is not a trim",
            "to take an item out, remove it explicitly with a "
            "`remove_overlay` edit, then re-run `ren touch`")
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
    except _ce.CaptionSourceTrimUnreadable as unreadable:
        raise TouchupRefused(
            f"edit {position} (`retime`) would move a subtitle item whose "
            f"source trim is unreadable: {unreadable}",
            "the ripple would place a replacement caption at frame 0",
            "re-read the reel so each caption's source trim is known, then "
            "re-run `ren touch`") from unreadable
    except _ce.SourceHeadroomExhausted as starved:
        raise TouchupRefused(
            f"edit {position} retimes {row}[{item_index}] "
            f"{old}->{new}f and {starved}",
            "lengthening an item past its source file places a hole "
            "rather than picture",
            "shorten the retime to fit the source headroom, then "
            "re-run `ren touch`")
    if not any(c.row == row and c.item_index == int(item_index)
               and c.played_length_changes for c in planned):
        raise TouchupRefused(
            f"edit {position} retimes {row}[{item_index}] "
            f"{old}->{new}f and the ripple planner did not extend "
            f"that item (cut @{cut_frame}, delta {delta:+d})",
            "the plan and the ask disagree, so nothing is staged",
            "restate the retime against the live timeline, then re-run "
            "`ren touch`")
    changes.extend(planned)
    stretched = sorted(f"{c.row}[{c.item_index}]" for c in planned
                       if c.played_length_changes)
    shifted = sorted(f"{c.row}[{c.item_index}]" for c in planned
                     if not c.played_length_changes)
    notes.append(f"edit {position}: retime {row}[{item_index}] "
                 f"{old}->{new}f ({delta:+d}f); stretched "
                 f"{stretched}; shifted {len(shifted)} item(s)")
    return True


def _op_set_properties(edit, position, tracks, spans, changes,
                         insertions, removals, moves, exclude, notes,
                         in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    properties = edit.get("properties")
    if not row or item_index is None or not isinstance(properties,
                                                       Mapping):
        raise TouchupRefused(
            f"edit {position} (`set_properties`) needs "
            f"`row`, `item` and a `properties` mapping (got "
            f"{dict(edit)!r})",
            "a property write naming no item or no mapping cannot be "
            "staged",
            f"give edit {position} `row`, `item` and `properties` in the "
            f"--edits JSON, then re-run `ren touch`")
    if not properties:
        raise TouchupRefused(
            f"edit {position} (`set_properties`) names no properties",
            "a touchup with nothing to write is a caller that failed to "
            "say what it wants",
            f"name at least one property on edit {position}, then re-run "
            f"`ren touch`")
    unsettable = {
        key: ("read-only - Resolve reports it and will not take it "
              "back" if key in _ce.READ_ONLY_PROPERTIES
              else ("None - there is no value to write" if value is None
                    else "a placeholder, not a value"))
        for key, value in properties.items()
        if (key in _ce.READ_ONLY_PROPERTIES or value is None
            or (isinstance(value, str) and value.startswith("<")))}
    if unsettable:
        raise TouchupRefused(
            f"edit {position} (`set_properties`) asks to "
            f"set what cannot be set: {unsettable}",
            "`composed_edit.set_properties` would skip these silently, "
            "so the gate refuses them loudly instead",
            f"drop {sorted(unsettable)} from edit {position}'s "
            f"properties, then re-run `ren touch`")
    clip = _find_clip(tracks, row, int(item_index))
    # Every unsettable key raised above, so what remains is all of it.
    wanted = dict(properties)
    in_place.append({"kind": "set_properties", "row": row,
                     "item_index": int(item_index),
                     "record_frame": int(clip["record_in"]),
                     "properties": wanted})
    notes.append(f"edit {position}: set {row}[{item_index}] "
                 f"{sorted(wanted)} in place (no delete, no place - "
                 f"SetProperty with read-back)")
    return False


def _op_add_row(edit, position, tracks, spans, changes, insertions,
                removals, moves, exclude, notes, in_place) -> bool:
    """A new, NAMED video row on top of the stack - declared, never invented.

    The gate refuses to place onto a row the reel does not carry
    (`add_overlay`), because inventing one is the gate guessing. A
    spec that states the row by name is not a guess: the row is made
    on the staging copy, above every row the build placed, and its
    name is read back. A reel already carrying a row of that name
    refuses - a second one would be two rows doing one row's job.
    """
    name = str(edit.get("name") or "").strip()
    if not name:
        raise TouchupRefused(
            f"edit {position} (`add_row`) names no row",
            "a row nobody named is the default-named row the SOP forbids",
            f"give edit {position} a `name`, then re-run `ren touch`")
    if any(str(t.get("name") or "") == name for t in tracks
           if str(t.get("type", "")).lower().startswith("v")):
        raise TouchupRefused(
            f"edit {position} adds a row {name!r} and this reel already "
            f"carries one",
            "two rows of one name are two rows doing one row's job",
            f"place onto the existing {name!r} row instead")
    index = len(_video_rows_of(tracks)) + 1
    row = f"V{index}"
    tracks.append({"type": "video", "index": index, "name": name,
                   "clips": [], "added": True, "row": row})
    spans.setdefault(row, [])
    notes.append(f"edit {position}: add row {row} {name!r} on top")
    return False


def _op_set_enabled(edit, position, tracks, spans, changes, insertions,
                    removals, moves, exclude, notes, in_place) -> bool:
    """Switch one item on or off IN PLACE - it stays on the timeline.

    Disabling is how a graphic leaves the picture without leaving the
    reel: nothing is deleted, the item keeps its span, file and
    treatment, and `ren undo` (or one click in Resolve) brings it back.
    """
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    enabled = edit.get("enabled")
    if not row or item_index is None or not isinstance(enabled, bool):
        raise TouchupRefused(
            f"edit {position} (`set_enabled`) needs `row`, `item` and a "
            f"boolean `enabled` (got {dict(edit)!r})",
            "a switch naming no item or no state cannot be staged",
            f"give edit {position} `row`, `item` and `enabled`, then "
            f"re-run `ren touch`")
    if _is_continuous_row(row) or _is_audio_row(row):
        raise TouchupRefused(
            f"edit {position} switches an item on {row}",
            "switching off continuous picture or program sound leaves "
            "black or silence",
            "switch overlay items (V3+) only")
    clip = _find_clip(tracks, row, int(item_index))
    in_place.append({"kind": "set_enabled", "row": row,
                     "item_index": int(item_index),
                     "record_frame": int(clip["record_in"]),
                     "enabled": enabled})
    notes.append(f"edit {position}: {'enable' if enabled else 'disable'} "
                 f"{row}[{item_index}] in place")
    return False


def _op_entry_motion(edit, position, tracks, spans, changes,
                     insertions, removals, moves, exclude, notes,
                     in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    try:
        fade_in = int(edit.get("fade_in_frames") or 0)
        fade_out = int(edit.get("fade_out_frames") or 0)
    except (TypeError, ValueError):
        raise TouchupRefused(
            f"edit {position} (`entry_motion`) needs "
            f"`fade_in_frames`/`fade_out_frames` as frame counts "
            f"(got {edit.get('fade_in_frames')!r}/"
            f"{edit.get('fade_out_frames')!r})",
            "a ramp that is not a frame count cannot be drawn",
            f"give edit {position} integer frame counts, then re-run "
            f"`ren touch`")
    if not row or item_index is None:
        raise TouchupRefused(
            f"edit {position} (`entry_motion`) needs "
            f"`row` and `item` (got {dict(edit)!r})",
            "motion naming no item cannot be staged",
            f"give edit {position} `row` and `item`, then re-run "
            f"`ren touch`")
    if fade_in < 0 or fade_out < 0:
        raise TouchupRefused(
            f"edit {position} (`entry_motion`) names a "
            f"negative ramp ({fade_in}/{fade_out}f)",
            "a ramp runs forward or not at all",
            f"use non-negative ramps on edit {position}, then re-run "
            f"`ren touch`")
    if fade_in == 0 and fade_out == 0:
        raise TouchupRefused(
            f"edit {position} (`entry_motion`) animates "
            f"nothing - both ramps are 0f",
            "a touchup with nothing to draw is a caller that failed to "
            "say what it wants",
            f"give edit {position} a non-zero ramp, then re-run "
            f"`ren touch`")
    if _is_audio_row(row):
        raise TouchupRefused(
            f"edit {position} puts entry motion on {row}",
            "entry motion is a Fusion video treatment - audio rows "
            "carry no Fusion comps",
            "put the motion on a video row, then re-run `ren touch`")
    if row in COMP_ROWS:
        raise TouchupRefused(
            f"edit {position} animates an item on {row}, "
            f"and {row} is a comp-bearing row",
            "the pass writes per-clip Fusion comps there "
            "(`execution/fusion_tracks.FUSION_COMP_TRACKS`), so a "
            "second treatment stacked beside the pass's own would "
            "be dropped silently by the next re-derivation, whose "
            "manifest does not know it",
            "rebuild the reel (`ren build <project>`), which plans the "
            "treatment whole, instead of touching it up")
    clip = _find_clip(tracks, row, int(item_index))
    if _ce.treatment_comps(clip):
        raise TouchupRefused(
            f"edit {position} animates {row}[{item_index}], "
            f"and that item already carries a drawing comp (or one "
            f"that could not be read - an unreadable graph is not "
            f"evidence of an empty one)",
            "a second treatment the recorded manifest does not know "
            "would be dropped silently by the next re-derivation",
            "rebuild the reel (`ren build <project>`) instead of "
            "touching it up")
    duration = int(clip["duration"])
    if fade_in + fade_out > duration - 1:
        raise TouchupRefused(
            f"edit {position} (`entry_motion`) wants "
            f"fade_in {fade_in}f + fade_out {fade_out}f on "
            f"{row}[{item_index}], which plays {duration}f",
            "the ramps need one frame more of clip than of ramp between "
            "them - a ramp longer than its clip never reaches "
            "neutral, so the effect would hold across the whole "
            "clip (`fusion.played_window`)",
            f"shorten the ramps to fit {duration}f on edit {position}, "
            f"then re-run `ren touch`")
    left_offset = clip.get("left_offset")
    if left_offset is None:
        raise TouchupRefused(
            f"edit {position} (`entry_motion`) animates "
            f"{row}[{item_index}], and the read did not say which "
            f"source frame that item starts on (`left_offset` "
            f"unreadable)",
            "the entry comp is keyed to the played window, so without "
            "it there is nothing to key to",
            "re-read the reel (`ren drift <project>`) - if the offset "
            "is still unreadable the item cannot carry entry motion")
    in_place.append({"kind": "entry_motion", "row": row,
                     "item_index": int(item_index),
                     "record_frame": int(clip["record_in"]),
                     "fade_in_frames": fade_in,
                     "fade_out_frames": fade_out,
                     "duration": duration,
                     "left_offset": int(left_offset)})
    notes.append(f"edit {position}: entry-motion {row}[{item_index}] "
                 f"in {fade_in}f / out {fade_out}f over {duration}f "
                 f"(authored Fusion fade, no rebuild)")
    return False


_OP_HANDLERS = {
    "move": _op_move,
    "swap_pixels": _op_swap_pixels,
    "add_overlay": _op_add_overlay,
    "add_row": _op_add_row,
    "set_enabled": _op_set_enabled,
    "remove_overlay": _op_remove_overlay,
    "retime": _op_retime,
    "set_properties": _op_set_properties,
    "entry_motion": _op_entry_motion,
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
    from library.tools.execution.fusion_tracks import FUSION_COMP_TRACKS

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
            f"the recorded fusion manifest no longer "
            f"describes this reel's timeline ({mismatched})",
            "the comp pass maps specs to items by source in order, so "
            "it would write comps onto the wrong clips",
            "rebuild the reel with `ren build <project>` "
            "(manage_project.py build-reels) - which re-derives the "
            "manifest - instead of touching it up")


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
            f"the overlay file is not on disk: {wanted}",
            "a touchup never renders media",
            "render it first, then state its path in the --edits JSON "
            "and re-run `ren touch`")
    found = _find_pool_item(pool, wanted)
    if found is not None:
        return found
    imported = pool.ImportMedia([wanted])
    if not imported:
        raise TouchupRefused(
            f"the pool would not import {wanted} "
            f"(`ImportMedia` returned nothing)",
            "without a pool item there is nothing to place - and "
            "nothing was staged",
            "check the file is a format Resolve imports, then re-run "
            "`ren touch`")
    found = _find_pool_item(pool, wanted)
    if found is None:
        raise TouchupRefused(
            f"{wanted} imported but no pool item reads back at that path",
            "the import reported success and the item is not there - "
            "nothing was staged",
            "re-import the file in Resolve by hand, then re-run "
            "`ren touch`")
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


def check_source_lengths(pool: Any,
                         pending: Sequence[_PendingSwap]) -> None:
    """Refuse a swap/add its file cannot fill, before anything is staged.

    Run ahead of the journal and the staging copy, so a refusal leaves
    nothing behind: raised from inside the staged edit it came after
    the conform and the in-place writes, and left a half-edited
    "(rebuild staging)" timeline that refused every re-run until it
    was deleted by hand (26 of them on the geo-podcast finals,
    2026-09-25).

    The pool keeps the length it read at import. A file re-rendered
    LONGER at the same path - a TV-frame overlay is one artefact per
    still, re-rendered when a longer run needs it
    (`reel_look.frame_overlay_segments`) - still reads short there, so
    a short pool item is re-read from disk (`ReplaceClip` onto its own
    path) before the span is judged against it.
    """
    for item in pending:
        mpi = pool_item_for_path(pool, item.media)
        needed = int(item.left_offset) + int(item.duration)
        frames = _pool_source_frames(mpi)
        if frames is not None and needed > frames and mpi.ReplaceClip(
                os.path.abspath(os.path.expanduser(item.media))):
            frames = _pool_source_frames(mpi)
        if frames is not None and needed > frames:
            raise TouchupRefused(
                f"edit {item.position} wants "
                f"{item.duration}f from offset {item.left_offset} of "
                f"{item.media}, which holds {frames}f",
                "placing it would put a hole where picture was asked for",
                "shorten the span to fit the source file, then re-run "
                "`ren touch`")


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
        f"the plan names no reel {reel}",
        "a touchup edits a built reel the plan describes - it never "
        "invents one",
        "run `ren propose <project>` first, or touch a reel the plan "
        "names (`ren status <project>` lists them)")



def _resolve_insertions(pool: Any, timeline: Any,
                        pending: Sequence[_PendingSwap]) -> list:
    """Pending swaps/adds to live `composed_edit.Insertion`s."""

    resolved = []
    for item in pending:
        mpi = pool_item_for_path(pool, item.media)
        if item.carry_from is not None:
            row, index = item.carry_from
            live = _live_rows(timeline)
            items = live.get(str(row).upper()) or []
            if int(index) >= len(items):
                raise TouchupRefused(
                    f"edit {item.position} carries the "
                    f"treatment of {row}[{index}], which is no "
                    f"longer on the staging timeline",
                    "the plan and the timeline disagree, so nothing is "
                    "deleted",
                    "re-read the reel and restate the swap against the "
                    "live timeline, then re-run `ren touch`")
            source = items[int(index)]
            try:
                properties = dict(source.GetProperty() or {})
            except Exception:  # noqa: BLE001
                properties = {}
            nodes = _live_prop(source, "GetNumNodes", None)
            if nodes is not None and int(nodes) > 1:
                raise TouchupRefused(
                    f"edit {item.position} swaps "
                    f"{row}[{index}], which carries a colour grade "
                    f"({nodes} nodes)",
                    "an `Insertion` cannot take a grade from an item "
                    "about to be deleted",
                    "rebuild the reel (`ren build <project>`) - a graded "
                    "swap needs a rebuild, not a touchup")
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
            f"the plan accounts {void} below zero items",
            "the plan and the timeline disagree - nothing is staged",
            "restate the plan against the live timeline (`ren drift "
            "<project>` shows it), then re-run `ren touch`")
    return counts


def apply_touchup(project_folder: str, spec: Mapping,
                  resolve_project_name: str = "",
                  allow_drops=None,
                  supersede=None,
                  connect=None,
                  rederiver_override=None,
                  accept_editor_changes=None) -> dict:
    """Apply a structured change to a built reel's existing timeline.

    Stages a DUPLICATE beside the approved reel, conforms it, routes
    the qualified plan through `composed_edit.apply_composed_edit`,
    verifies by re-reading the track, guards the replacement and
    promotes by rename.  The replaced generation is deleted once the
    undo journal holds both sides (`undo_journal`), or retired to the
    archive when the reel carries a sign-off.  The approved timeline is
    never edited directly.

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
    declared_editor_acceptance = accept_editor_changes
    if declared_editor_acceptance is None:
        declared_editor_acceptance = (spec or {}).get(
            "accept_editor_changes")

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
        from library.tools.reel_build import _connect_resolve_project as _connect
        connect = _connect
    return _apply_under_lease(
        project_folder, spec, final, resolve_name, declared_drops,
        declared_supersede, declared_editor_acceptance, connect,
        rederiver_override, started)


def _apply_under_lease(project_folder: str, spec: Mapping, final: str,
                       resolve_name: str, declared_drops,
                       declared_supersede, accept_editor_changes, connect,
                       rederiver_override, started: float) -> dict:
    from library.tools.resolve_lock import under_lease

    @under_lease(f"touch up {final}")
    def _guarded():
        return _apply_connected(
            project_folder, spec, final, resolve_name,
            declared_drops, declared_supersede, accept_editor_changes, connect,
            rederiver_override, started)

    return _guarded()


def _apply_connected(project_folder: str, spec: Mapping, final: str,
                     resolve_name: str, declared_drops,
                     declared_supersede, accept_editor_changes, connect,
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
            f"no timeline called {final!r} is in Resolve "
            f"project {resolve_name!r}",
            "a touchup edits the reel's existing timeline, and there "
            "is none to edit",
            "build it first (`ren build <project>`), then touch it up")
    source = found[final]
    staging = staging_name(final)
    if timelines_to_replace(project, {staging}):
        raise TouchupRefused(
            f"a staging container {staging!r} from an "
            f"interrupted run is still in the project",
            "reusing it would grade one run's content as another's",
            "clear it in Resolve before re-running `ren touch`")

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
    # What this touch can have changed, for the checks that follow it
    # (`dirty_regions`): read off the PRE-edit timeline, before staging.
    try:
        fps = source.GetSetting("timelineFrameRate")
    except Exception:  # noqa: BLE001 - judged by dirty_from_spec
        fps = None
    receipt["dirty"] = _dirty_regions.dirty_from_spec(
        spec, tracks, first_frame=_live_prop(source, "GetStartFrame"),
        end_frame=_live_prop(source, "GetEndFrame"), fps=fps)

    # The rederiver, chosen by the gate class - never by a flag.
    manifest = None
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
                f"this change alters a played length and no "
                f"recorded fusion manifest for {final!r} is on disk",
                "without the manifest there is no route to the comp "
                "generator, and a trim without re-derivation renders "
                "wrong on 99% of the clip's frames while looking right",
                "rebuild the reel (`ren build <project>`) - which "
                "re-derives the manifest - instead")
        check_manifest_matches(manifest, tracks)
        from library.tools.composed_edit import ReelLookRederiver
        rederiver = ReelLookRederiver(
            manifest, project_folder, resolve_name, staging)
        rederiver.expected_row_counts = _plan_post_edit_counts(
            tracks, qualification)
        receipt["rederiver"] = "reel_look.apply_comps over the " \
            "recorded fusion manifest"

    check_source_lengths(pool, qualification.insertions)

    # THE UNDO JOURNAL, before anything changes (`undo_journal`): the
    # approved timeline read whole, with it CURRENT so no transform
    # reads cursor-scaled, and every item the plan deletes outright
    # captured. It is what lets the replaced generation be deleted
    # below instead of left in the project as an archived copy.
    from library.tools import undo_journal as _journal
    from library.tools.resolve_lock import cursor_fence
    try:
        with cursor_fence(project, source, f"journal {final}"):
            journal = _journal.open_entry(
                project_folder, final=final, reel=int(spec.get("reel")),
                resolve_project=resolve_name, spec=spec,
                gate_class=qualification.gate_class,
                source_timeline=source,
                removals=qualification.removals,
                fusion_manifest=(manifest if qualification.gate_class
                                 == COMPOSED_WITH_REDERIVATION else None),
                batch=str(spec.get("batch") or ""),
                project=project)
    except _journal.UndoRefused as unrecordable:
        # The journal's refusal already carries what/why/fix - carry
        # it across unchanged so the shape survives the translation.
        raise TouchupRefused(
            unrecordable.what, unrecordable.why,
            unrecordable.fix) from unrecordable
    receipt["journal"] = journal["id"]

    from library.tools import plan_provenance as _provenance
    from library.tools import reel_replace_guard as _guard
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    inventory_before = _guard.timeline_inventory(project)
    inventory_operation = _provenance.begin_timeline_inventory(
        review_dir, "touchup", inventory_before,
        ren_created_names={final})
    receipt["inventory_operation"] = inventory_operation
    receipt["timeline_inventory_before"] = inventory_before

    stage_started = time.time()
    staged = source.DuplicateTimeline(staging)
    if staged is None or staged.GetName() != staging:
        _journal.fail_entry(project_folder, journal,
                            "the staging copy did not land")
        _provenance.finish_timeline_inventory(
            review_dir, inventory_operation, _guard.timeline_inventory(project))
        raise TouchupError(
            f"the staging copy did not land as {staging!r} - "
            f"nothing was edited and the approved timeline stands.")
    # `AppendToTimeline` writes to the CURRENT timeline, so the
    # staging must be the cursor before anything places - and a
    # foreign move mid-section must fail the touchup rather than
    # write onto the wrong timeline.  `cursor_fence` establishes the
    # cursor, re-reads it on exit, and raises on drift; the lease it
    # takes nests inside the outer one.
    try:
        with cursor_fence(project, staged, f"touch up {final}"):
            _edit_staged(project_folder, project, pool, source,
                         staged, staging, qualification, rederiver,
                         receipt, declared_drops, declared_supersede,
                         accept_editor_changes, final, stage_started, journal)
    except Exception as failed:
        # The approved timeline still stands under its own name; the
        # staging holds the half-done edit for diagnosis.  Delete
        # nothing: a failed touchup must never widen into a loss.
        receipt["staging_left_standing"] = staging
        if journal.get("status") == _journal.STATUS_OPEN:
            _journal.fail_entry(project_folder, journal, repr(failed))
        _provenance.finish_timeline_inventory(
            review_dir, inventory_operation, _guard.timeline_inventory(project))
        raise
    receipt["seconds"] = round(time.time() - started, 3)
    receipt["receipt_path"] = _write_receipt(project_folder, final, receipt)
    return receipt


def _add_rows(staged: Any, new_rows: Sequence[Mapping]) -> list:
    """Make the rows `add_row` declared, on the staging copy, read back."""
    made = []
    for entry in new_rows or ():
        if not staged.AddTrack("video"):
            raise TouchupError(f"Resolve would not add row {entry['row']} "
                               f"to the staging copy")
        index = int(staged.GetTrackCount("video"))
        if f"V{index}" != entry["row"]:
            raise TouchupError(
                f"the new row landed as V{index}, the plan said "
                f"{entry['row']} - the staging copy and the read disagree")
        staged.SetTrackName("video", index, entry["name"])
        if staged.GetTrackName("video", index) != entry["name"]:
            raise TouchupError(f"row {entry['row']} would not take the name "
                               f"{entry['name']!r}")
        made.append(dict(entry))
    return made


def _apply_in_place(staged: Any, qualification: Qualification,
                    comp_dir: str) -> dict:
    """Write every in-place edit onto the staging copy. No delete, no place.

    Runs BEFORE the composition, addressed by pre-edit record frame
    (nothing has moved yet): a composition capture taken after this
    reads the written state, so a re-place further down the path
    carries the in-place write rather than losing it.  A write that
    does not read back raises `TouchupError` - the staging stands for
    diagnosis and the approved timeline was never touched.
    """

    applied: dict = {"properties": [], "entry_motion": []}
    if not qualification.in_place:
        return applied
    rows = _live_rows(staged)
    for entry in qualification.in_place:
        row = str(entry.get("row")).upper()
        items = rows.get(row) or []
        hits = [item for item in items
                if _live_prop(item, "GetStart", None)
                == int(entry["record_frame"])]
        if len(hits) != 1:
            raise TouchupError(
                f"the staging copy holds {len(hits)} items at "
                f"{row}@{entry.get('record_frame')} for the "
                f"{entry.get('kind')} write - the plan and the "
                f"timeline disagree. Nothing further is written "
                f"and the approved timeline stands.")
        item = hits[0]
        if entry.get("kind") == "set_enabled":
            want = bool(entry["enabled"])
            item.SetClipEnabled(want)
            if bool(item.GetClipEnabled()) is not want:
                raise TouchupError(
                    f"the staged {row}@{entry.get('record_frame')} reads "
                    f"back {'disabled' if want else 'enabled'} after the "
                    f"switch - nothing further is edited and the approved "
                    f"timeline stands.")
            applied.setdefault("enabled", []).append({
                "row": row, "record_frame": entry["record_frame"],
                "enabled": want})
            continue
        if entry.get("kind") == "set_properties":
            diff = _ce.set_properties(
                item, dict(entry.get("properties") or {}))
            if diff:
                raise TouchupError(
                    f"the staged {row}@{entry.get('record_frame')} "
                    f"did not take its properties: {diff}. A "
                    f"property that will not read back is not "
                    f"written - nothing further is edited and the "
                    f"approved timeline stands.")
            applied["properties"].append({
                "row": row, "record_frame": entry["record_frame"],
                "properties": dict(entry.get("properties") or {})})
        else:
            applied["entry_motion"].append(_import_entry_comp(
                item, entry, comp_dir))
    return applied


def _import_entry_comp(item: Any, entry: Mapping, comp_dir: str) -> dict:
    """Author the entry fade and put it on the staged item, verified.

    The comp is built by the comp builder's own `fade_in_frames` /
    `fade_out_frames` dispatch - the same keys the comp pass reads -
    over the item's played window, sized to the SOURCE frame read off
    the pool item (a canvas sized by guess paints a hard-edged
    rectangle, so an unreadable resolution refuses).  After the
    import the pass's own `comp_media_window.conform_item` judges the
    media window, and the new comp must both exist and draw
    something - judged by re-read, never by the import's return.
    """
    from library.tools import comp_media_window as _windows
    from library.tools import pool_stream_meta as _pool_meta
    from library.tools.fusion.comp_builder import build_effect_comp

    row = str(entry.get("row")).upper()
    record = int(entry["record_frame"])
    duration = int(entry["duration"])
    left = int(entry["left_offset"])
    fade_in = int(entry.get("fade_in_frames") or 0)
    fade_out = int(entry.get("fade_out_frames") or 0)

    try:
        pool_item = item.GetMediaPoolItem()
    except Exception:  # noqa: BLE001 - a handle that will not answer
        pool_item = None
    source_frames = (_pool_source_frames(pool_item)
                     if pool_item is not None else None)
    if source_frames is None:
        raise TouchupError(
            f"the staged {row}@{record} will not say how many frames "
            f"its source file holds, so no entry comp can be sized "
            f"for it. Nothing was imported and the approved timeline "
            f"stands.")
    stream = (_pool_meta.pool_stream(pool_item)
              if pool_item is not None else {})
    if not stream.get("width") or not stream.get("height"):
        raise TouchupError(
            f"the staged {row}@{record} will not say what frame its "
            f"source carries (`Resolution` unreadable), so no entry "
            f"comp canvas can be sized. A guessed canvas paints a "
            f"hard-edged rectangle (`fusion.comp_builder`: "
            f"`MissingSourceFrame`) - nothing was imported and the "
            f"approved timeline stands.")
    res = (int(stream["width"]), int(stream["height"]))

    content = build_effect_comp(
        {"fade_in_frames": fade_in, "fade_out_frames": fade_out,
         "source_in_frame": left,
         "source_out_frame": left + duration - 1},
        int(source_frames), res)
    os.makedirs(comp_dir, exist_ok=True)
    comp_path = os.path.join(
        comp_dir, f"entry_{_safe_slug(row)}_{record}.comp")
    with open(comp_path, "w", encoding="utf-8") as handle:
        handle.write(content)

    before = _live_prop(item, "GetFusionCompCount", None)
    imported = item.ImportFusionComp(comp_path)
    names = list(item.GetFusionCompNameList() or [])
    if not imported or before is None or len(names) != int(before) + 1:
        raise TouchupError(
            f"the staged {row}@{record} did not take its entry comp "
            f"(import returned {imported!r}, comp count "
            f"{before}->{len(names)}). Nothing further is edited and "
            f"the approved timeline stands.")
    fresh = item.GetFusionCompByIndex(len(names))
    try:
        tools = (fresh.GetToolList(False) or {}) if fresh else {}
        reg_ids = [tool.GetAttrs("TOOLS_RegID")
                   for tool in tools.values()]
    except Exception:  # noqa: BLE001 - judged below, never raised here
        reg_ids = None
    from library.tools import reel_read as _read
    if not _read.comp_draws_something({"tools": reg_ids}):
        raise TouchupError(
            f"the staged {row}@{record} imported {comp_path} but the "
            f"new comp draws nothing - an entry motion that draws "
            f"nothing is not rendered (`motion_graphics_plan`). "
            f"Nothing further is edited and the approved timeline "
            f"stands.")
    window_receipt = _windows.conform_item(
        item, duration, comp_path, index=len(names),
        label=f"{row}@{record} entry")
    return {"row": row, "record_frame": record,
            "fade_in_frames": fade_in, "fade_out_frames": fade_out,
            "comp": comp_path, "window": window_receipt}


def _edit_staged(project_folder: str, project: Any, pool: Any,
                 source: Any, staged: Any, staging: str,
                 qualification: Qualification, rederiver: Any,
                 receipt: dict, declared_drops, declared_supersede,
                 accept_editor_changes, final: str, stage_started: float,
                 journal: dict) -> None:
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

    # In-place writes land FIRST, before anything is deleted: the
    # composition's capture reads the staged item as it stands, so a
    # re-place further down carries the write instead of losing it.
    receipt["new_rows"] = _add_rows(staged, qualification.new_rows)
    receipt["in_place"] = _apply_in_place(
        staged, qualification, comp_dir)

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
             declared_drops, declared_supersede, accept_editor_changes,
             receipt, journal)


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
             declared_supersede, accept_editor_changes, receipt: dict,
             journal: dict) -> None:
    """Guard, swap names, carry markers, close the journal, delete the
    replaced generation, close the signature."""
    from library.tools import reel_replace_guard as _guard
    from library.tools import reel_signoff as _signoff
    from library.tools import plan_provenance as _provenance
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

    live_full = _guard.full_timeline_snapshot(
        originals[final], project, project_folder)
    staged_full = _guard.full_timeline_snapshot(
        staged_found[staging], project, project_folder)
    try:
        _guard.assert_target_inventory_unchanged(
            receipt["timeline_inventory_before"],
            _guard.timeline_inventory(project), [final])
        _provenance.assert_not_editor_timeline(
            os.path.join(project_folder, "pipeline_output", "review"),
            originals[final])
        # The staging is a copy of the live timeline, so the editor's
        # edits are on it already: they are filed as carried edits, not
        # re-applied over what this touch changes on purpose.
        from library.tools import editor_edit_carry as _editor_carry
        detection = _guard.detect_editor_changes(
            project_folder, final, live_full, live_full)
        edits, superseded = _editor_carry.edits_in_force(
            project_folder, final, detection["pending"])
        receipt["editor_changes"] = _guard.protect_editor_changes(
            project_folder, final, live_full, live_full, staged_full,
            accept=_guard.accepts_editor_changes(
                final, accept_editor_changes), detection=detection,
            carried_edits={"edits": edits, "superseded": superseded,
                           "written": [], "already_held": []})
    except _guard.EditorChangeRefused as refused:
        raise TouchupError(str(refused)) from refused

    declared = _guard.parse_specs(declared_drops, [final])
    editor_override_rows = _guard.accepted_editor_drop_rows(
        receipt["editor_changes"])
    allowed_rows = set(declared.get(final, ())) | editor_override_rows
    _signoff.assert_declared(project_folder, final,
                             declared_supersede,
                             command="touch-reel")
    incoming_rows = _guard.snapshot_timeline(staged_found[staging],
                                             staging, side="staged")
    retired_rows = _guard.snapshot_timeline(originals[final], final,
                                            side="retiring")
    receipt["replace_report"] = _guard.check_replacement(
        final, staging, retired_rows, incoming_rows,
        allowed=allowed_rows)
    receipt["accepted_editor_drop_rows"] = sorted(editor_override_rows)

    from library.tools import marker_carry as _markers
    from library.tools import marker_gate as _gate
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
    # The clip plane beside it, as in promotion: a marker on a clip
    # item dies with its item, and the timeline read above never saw
    # it. Carried by source file and source frame, reported by name
    # where no unique placement resolves.
    clip_notes = _markers.read_clip_markers(originals[final], final)
    if clip_notes:
        clip_keep, clip_lost = _markers.plan_clip_carry(
            clip_notes, staged_found[staging], final)
        _markers.report_clip(final, clip_keep, clip_lost)
        entry = carried_markers or {"carried": [], "uncarried": []}
        entry["clip_carried"] = clip_keep
        entry["clip_uncarried"] = clip_lost
        carried_markers = entry
    # The gate's baseline: the same pre-rename reads, filed before
    # anything is renamed (`library/tools/marker_gate.py`).
    try:
        gate_capture = _gate.assemble_capture(final, notes, clip_notes)
        gate_path = _gate.write_capture(project_folder, final,
                                        gate_capture)
    except OSError as capture_failed:
        raise TouchupError(
            f"the marker capture for {final!r} could not be filed "
            f"({capture_failed}) - nothing was renamed.") \
            from capture_failed
    fleet_before = _gate.fleet_snapshot(project)

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
    if carried_markers and carried_markers.get("clip_carried"):
        clip_declined = _markers.place_clip_markers(
            staged_found[staging], carried_markers["clip_carried"])
        carried_markers["clip_declined"] = clip_declined
    receipt["markers"] = carried_markers or {"carried": [],
                                              "uncarried": []}

    # ── THE MARKER GATE ──────────────────────────────────────
    # The outside count, before the replaced generation retires:
    # the promoted reel re-read live, both planes, diffed by
    # identity against the pre-rename capture.
    try:
        _gate.verify_promotion(final, gate_capture,
                               staged_found[staging],
                               carried_markers)
    except _markers.MarkerCarryUnreadable as unreadable:
        raise TouchupError(
            f"{final!r} is promoted, but its live markers could not "
            f"be re-read ({unreadable}) - recover from {backup!r} "
            f"and the capture at {gate_path}.") from unreadable
    except _gate.MarkerGateLost as lost:
        raise TouchupError(
            f"{lost} Recover from {backup!r} and the capture at "
            f"{gate_path}.") from lost
    fleet = _gate.check_fleet(
        fleet_before, _gate.fleet_snapshot(project),
        {final, staging, backup})
    if fleet["decreased"]:
        shrunk = "; ".join(
            f"{entry['reel']} {entry['plane']} "
            f"{entry['before']}->{entry['after']}"
            for entry in fleet["decreased"])
        raise TouchupError(
            f"{final!r} is promoted, but {shrunk} - marker count(s) "
            f"SHRANK on reel(s) this touch-up did not touch.")

    # THE JOURNAL CLOSES before the replaced generation goes: until
    # `after` is on disk, the backup is the only way back.
    from library.tools import undo_journal as _journal
    _journal.close_entry(project_folder, journal,
                         after_timeline=staged_found[staging],
                         rows=incoming_rows, project=project)
    receipt["version"] = journal["version"]
    if journal.get("preservation_after") is not None:
        from library.tools import editor_edit_carry as _editor_carry
        receipt["carried_edits"] = _editor_carry.record_after_promotion(
            project_folder, final,
            receipt["editor_changes"].get("carried_edits"),
            journal["preservation_after"], act=f"touch {journal['id']}")
    ren_owned_ids = set()
    for timeline in (originals[final], staged_found[staging]):
        try:
            unique_id = timeline.GetUniqueId()
        except Exception:  # noqa: BLE001
            unique_id = None
        if unique_id:
            ren_owned_ids.add(str(unique_id))
    _provenance.record_unattributed_timeline_changes(
        os.path.join(project_folder, "pipeline_output", "review"),
        receipt["timeline_inventory_before"],
        _guard.timeline_inventory(project), operation="touchup",
        ren_owned_ids=ren_owned_ids)
    for record_id in (receipt.get("editor_changes") or {}).get("carried", ()):
        _provenance.resolve_editor_change(
            os.path.join(project_folder, "pipeline_output", "review"),
            final, record_id, status="carried")
    for record_id in (receipt.get("editor_changes") or {}).get(
            "superseded", ()):
        _provenance.resolve_editor_change(
            os.path.join(project_folder, "pipeline_output", "review"),
            final, record_id, status="superseded",
            superseded_by=f"--accept-editor-changes {final}")

    # The replaced generation is DELETED: the journal restores the
    # pre-touch state in place (`ren undo`), so a live `(archived
    # round NNN)` copy is clutter, not safety (captain, D5,
    # 2026-09-23). A reel carrying a sign-off still RETIRES, exactly
    # as promotion does - the cut the captain approved is kept.
    from library.tools import reel_retirement as _retire
    from library.tools.versions import rounds as _rounds
    backup_objects = {t.GetName(): t for t in timelines_to_replace(
        project, {backup})}
    recorded = _rounds.discover(project_folder)
    current_round = recorded[-1]["round"] if recorded else 1
    by_final = {final: _retire.retiring_round(recorded, final,
                                              current_round)}
    if _signoff.base_name(final) in _signoff.signed_off(project_folder):
        retirement = _retire.retire_timelines(
            project, pool,
            {final: backup_objects[backup]}
            if backup in backup_objects else {},
            by_final)
    else:
        retirement = {"archived": {}, "unfiled": [], "collected": [],
                      "kept": [], **_retire.delete_backups(
                          project, pool,
                          {backup: backup_objects[backup]}
                          if backup in backup_objects else {})}
    receipt["retirement"] = _retire.render(retirement)
    _provenance.finish_timeline_inventory(
        os.path.join(project_folder, "pipeline_output", "review"),
        receipt["inventory_operation"], _guard.timeline_inventory(project))

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
    receipt["signature_closed"] = close_signature(project_folder,
                                                  project, final)


def close_signature(project_folder: str, project: Any, final: str):
    """Record what `final` now carries, so the next build reads it back.

    True when closed; otherwise the reason, which is never fatal: an
    unclosed signature only means the next build places the reel again
    rather than assume.
    """
    try:
        from library.tools import reel_rebuild_need as _need_record
        from library.tools.plan_provenance import (
            record_carried_digests as _record_carried,
        )
        live = None
        for index in range(1, (project.GetTimelineCount() or 0) + 1):
            timeline = project.GetTimelineByIndex(index)
            if timeline is not None and timeline.GetName() == final:
                live = timeline
        if live is None:
            return f"not closed (no timeline called {final!r})"
        digest = _need_record.carried_digest_live(project, live)
        if not digest:
            return "not closed (no digest could be read)"
        review_dir = os.path.join(project_folder, "pipeline_output",
                                  "review")
        _record_carried(review_dir, {final: digest})
        return True
    except Exception as signature_failed:  # noqa: BLE001 - never fatal
        return (f"not closed ({signature_failed}) - the next build will "
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
    "touchup_all_reels",
    "check_manifest_matches",
    "locate_named_clip",
    "pool_item_for_path",
    "qualify",
    "recorded_fusion_manifest",
    "reel_numbers",
    "resolve_final_name",
    "swap_spec_for_tracks",
]
