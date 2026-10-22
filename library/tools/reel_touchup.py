"""A change to the timeline in front of you, instead of a rebuild of it.

`manage_project.py build-reels` stages a fresh timeline and promotes it
over the old one; this module changes the built reel instead, through
`library/tools/composed_edit.py`'s in-place editor.

What the caller states, and what it does not
--------------------------------------------
The caller states the change STRUCTURALLY: which reel, which item,
what changes - one of the eleven ops below.  Mapping a captain's
natural-language note onto such a change is the NEXT task and is
explicitly not this one; this module is the mechanism that task will
call.

The eleven ops
------------
The first five delete and re-place through the composition; `add_row`
makes a declared row; `set_enabled`, `set_properties`,
`entry_motion` and `repair_hold_window` are IN-PLACE: they write onto
the staged item itself - no delete, no place - and ride
`Qualification.in_place` rather than changes/insertions/removals.
They run before the composition off the same staging, so a capture
taken after them carries them, and a spec mixing them with
composition ops stays servable: one source item, one edit
(`_check_single_claim`). The `set_properties` and `set_enabled`
writes are filed onto the carried-edits ledger at promotion
(`editor_edit_carry.derive_touch_edits`), so the next plan-change
rebuild carries them like the editor's own - and refuses by name when
the touched passage no longer plays.  `entry_motion` and
`repair_hold_window` write Fusion comps no rebuild re-derives, so
they are not carried there.

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
- `resize_still` - the duration of one native PNG still on V3+,
  re-placed at its existing frame with a temporary still-duration
  preference. It refuses animation/video, any Fusion treatment, a
  collision, and any second edit in the same request. It does not
  ripple neighboring items or run the Fusion comp pass.
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
- `repair_hold_window` - IN-PLACE.  A freeze-hold tail's Fusion comp
media windows rebound to the Timeline binding Resolve derives the
correct window from (`hold_window_repair.repair_hold_item_windows`),
then proved by fresh reads until consecutive reads agree and cover
every played frame.  The target states `row`, `item`, `record_frame`
and `duration`, and the source basename it expects: the gate refuses
unless exactly that hold is there - a baked hold file
(`reel_ending.BAKED_HOLD_PREFIXES`) or a native zero-speed hold -
carrying at least one Fusion comp.  No span, no source range, no
transform, no grade and no neighbouring item moves: the only
difference the promotion carries is the comp inputs the reads prove.
A hold whose windows will not converge refuses rather than shipping
a tail that cannot render (`comp_media_window`).

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
- `composed_still_resize` - one untreated native PNG still changes
  duration. The one still is deleted and re-placed at the requested
  frame count through Resolve's preference preset API; every preference
  is restored and compared in `finally`, and the timeline item is read
  back before promotion. No other item is re-placed and no comp pass
  runs.
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

import contextlib
import logging
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from library.tools import composed_edit as _ce
from library.tools import dirty_regions as _dirty_regions
from library.tools import touchup_phases
from library.tools.ren_refusal import RenRefusal
from library.tools.resolve_session import session_scope

_logger = logging.getLogger(__name__)

# ── Refusals ─────────────────────────────────────────────────────────
#
# Every one stops the touchup by name, before anything is staged.


class TouchupRefused(RenRefusal):
    """A touchup that refused. Fail closed, always by name."""

    USER_MESSAGE = ("I couldn't make this touchup as asked, so the reel "
                    "is unchanged. Check the issue below and ask again "
                    "with a touchup I can stage.")

    SUPPLIES = "touchup_edit"
    """A touchup's fix may name only what a touchup edit can carry.

    This is the guard that keeps the defect PR 3076 fixed: a refusal
    that recommends an argument the caller has no way to supply is
    refused here, at the raise site, instead of reaching the caller as
    advice they cannot act on.
    """


class TouchupError(RuntimeError):
    """A touchup that failed mid-flight. The staging still stands."""


# ── The qualification ────────────────────────────────────────────────

COMPOSED = "composed"
COMPOSED_WITH_REDERIVATION = "composed_with_rederivation"
COMPOSED_STILL_RESIZE = "composed_still_resize"

# The router's structural view must retain each touch-up's semantic op id.
# This is an execution adapter over the existing algebra, not a second
# operation vocabulary.
_STRUCTURAL_OPERATION_IDS = {
    "retime": "clip.ripple",
    "add_overlay": "overlay.add",
    "remove_overlay": "overlay.remove",
    "swap_pixels": "overlay.swap_pixels",
    "resize_still": "overlay.resize",
    "resize_overlay": "overlay.resize",
    "position_overlay": "overlay.position",
    "split": "clip.split",
    "slide": "clip.slide",
    "roll": "clip.roll",
    "add_row": "track.add",
    "move": "clip.move",
}

#: Rows whose spans are continuous program.  Vacating one - moving an
#: item away or removing it - leaves black or silence, so the gate
#: refuses and the caller states the covering change instead.  V1/V2
#: are the picture rows (`execution/fusion_tracks.FUSION_COMP_TRACKS`
#: plus the layout owner minting picture first); every audio row is
#: program sound.  Overlay rows are V3+ video by the SOP's layout
#: order (`docs/TIMELINE_SOP.md`), sparse by nature, so a gap there
#: is an ordinary state.
def _is_audio_row(row: str) -> bool:
    return str(row).upper().startswith("A")


def _is_continuous_row(row: str, tracks: Sequence[Mapping]) -> bool:
    """Infer picture continuity from live semantic names, never indices."""
    row = str(row).upper()
    if _is_audio_row(row):
        return True
    from library.tools.timeline_layout import SINGLETON_NAMES
    for track in tracks or ():
        if _row_of(track["type"], int(track["index"])) == row:
            name = str(track.get("name") or "").strip().casefold()
            # Unknown/default names are conservatively treated as picture
            # program: an uncertain role must never make deletion permissive.
            return name not in {value.casefold() for value in SINGLETON_NAMES}
    return False


def _picture_rows(tracks: Sequence[Mapping]) -> list[str]:
    from library.tools.timeline_layout import SINGLETON_NAMES
    return [
        _row_of(track["type"], int(track["index"]))
        for track in tracks or ()
        if str(track["type"]).lower().startswith("v")
        and str(track.get("name") or "").strip().casefold()
        not in {value.casefold() for value in SINGLETON_NAMES}]


def _row_has_live_comps(row: str, tracks: Sequence[Mapping]) -> bool:
    return any(
        _row_of(track["type"], int(track["index"])) == str(row).upper()
        and any(_ce.treatment_comps(clip)
                for clip in (track.get("clips") or ()))
        for track in tracks or ())


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


@contextlib.contextmanager
def _project_open(resolve_name: str):
    """The project this touch edits, OPEN under the lease it holds.

    The open project is GLOBAL session state: one Resolve instance, one
    open project, and every lane's job sees it. A touch that edits a
    reel in its own project therefore has to make that project the open
    one - under the lease the touch already holds, so no other lane can
    move the cursor between the open and the write - and the borrowed
    project is put back when the touch ends.

    Measured 2026-10-21: ten live write attempts on this machine, every
    one refused, seven of them because another lane had opened its own
    project first (once the captain's own `Podcast (field test)`).
    """
    from library.tools.marker_feedback import connect_resolve
    from library.tools.resolve_project_open import opened

    resolve = connect_resolve()
    with opened(resolve, resolve_name):
        yield


@contextlib.contextmanager
def _no_project_open(resolve_name: str):
    """The null open: a caller that brought its own connection."""
    yield


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
        with _project_open(resolve_name):
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
                  ignore: tuple | None = None) -> bool:
    end = int(start) + int(duration)
    for span_start, span_end in spans:
        if ignore is not None and (span_start, span_end) == ignore:
            continue
        if int(start) < int(span_end) and int(span_start) < end:
            return False
    return True


def _check_free(spans: Sequence[tuple], row: str, start: int,
                duration: int, ignore: tuple | None = None,
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
    `remove_overlay`, `retime`, `set_properties`, `entry_motion` or
    `repair_hold_window` - and the fields that op documents in the
    module docstring.
    Raises `TouchupRefused` for anything unclassifiable, naming why.
    Never falls back to a rebuild.
    """
    transaction_operations = list(
        (spec or {}).get("transaction_operations") or ())
    if transaction_operations:
        operation_ids = tuple(str(op["op"])
                              for op in transaction_operations)
        operation_set = set(operation_ids)
        supported = (
            len(operation_ids) == 2
            and operation_set in ({"clip.trim", "clip.insert"},
                                  {"clip.replace", "caption.style"}))
        targets = [str(op.get("unique_id") or "")
                   for op in transaction_operations]
        if not supported or not all(targets) or targets[0] == targets[1]:
            raise TouchupRefused(
                "the semantic transaction is not one of the qualified "
                "protected compound shapes",
                "staging accepts trim+insert or replace+caption.style with "
                "distinct stable targets only",
                "re-prepare the request as one supported compound")
        return Qualification(
            gate_class=COMPOSED,
            cost_statement=("protected experimental semantic transaction: "
                            "one staging candidate, full-request read-back, "
                            "then guarded promotion"),
            notes=["operations: " + " + ".join(operation_ids)])

    edits = list((spec or {}).get("edits") or ())
    if not edits:
        raise TouchupRefused(
            "the change names no edits",
            "a touchup with nothing to do is not a no-op to wave "
            "through - it is a caller that failed to say what it wants",
            "pass the change as --edits JSON or --edits-file PATH to "
            "`ren touch`")
    resize_positions = [index for index, edit in enumerate(edits)
                        if isinstance(edit, Mapping)
                        and str(edit.get("op") or "") in {
                            "resize_still", "resize_overlay"}]
    if resize_positions and (len(edits) != 1 or len(resize_positions) != 1):
        raise TouchupRefused(
            "a resize must be the only edit in its request",
            "resize verification requires one isolated staged change; "
            "mixing it with another resize or structural write would make "
            "the candidate evidence ambiguous",
            "resize one overlay or still per `ren touch` request")
    position_positions = [index for index, edit in enumerate(edits)
                          if isinstance(edit, Mapping)
                          and str(edit.get("op") or "") in {
                              "position_overlay"}]
    if position_positions and (len(edits) != 1 or len(position_positions) != 1):
        raise TouchupRefused(
            "a position must be the only edit in its request",
            "position verification requires one isolated staged change; "
            "mixing it with another position or structural write would make "
            "the candidate evidence ambiguous",
            "position one overlay per `ren touch` request")
    exclude = [(str(r), int(i)) for r, i in
               ((spec or {}).get("exclude") or ())]
    # An edit may also name the items a ripple must NOT move.  The
    # refusal that tells the caller to exclude a colliding item has to
    # name a way the caller CAN exclude it, and the spec-level list was
    # not reachable from `ren touch` (the CLI builds the spec from
    # `--edits` alone) - so the advice named a field the caller had no
    # way to set.  The per-edit list is the same vocabulary, one edit
    # nearer the thing it is about.
    for position, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            continue
        named = edit.get("exclude")
        if named is None:
            continue
        if (not isinstance(named, (list, tuple))
                or not all(isinstance(pair, (list, tuple))
                           and len(pair) == 2 for pair in named)):
            raise TouchupRefused(
                f"edit {position} names an `exclude` that is not a list "
                f"of [row, item] pairs ({named!r})",
                "an exclusion names the items a ripple must not move, so "
                "an unreadable one cannot be honoured",
                f"write edit {position}'s `exclude` as "
                "[[\"V4\", 21]], then re-run `ren touch`")
        for pair in named:
            try:
                exclude.append((str(pair[0]), int(pair[1])))
            except (TypeError, ValueError) as unreadable:
                raise TouchupRefused(
                    f"edit {position} names an exclusion that is not a "
                    f"row and an item index ({pair!r})",
                    "an exclusion names the items a ripple must not "
                    "move, so an unreadable one cannot be honoured",
                    f"write edit {position}'s `exclude` as "
                    "[[\"V4\", 21]], then re-run `ren touch`") from unreadable
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

    if (resize_positions
            and str(edits[resize_positions[0]].get("op") or "")
            == "resize_still"):
        cost = ("composed_still_resize: one untreated native PNG overlay "
                "is deleted and re-placed at the same record frame with "
                "the requested frame duration. Resolve preferences are "
                "snapshotted, restored and read back; no ripple and no "
                "Fusion comp pass run. The staging copy is still verified "
                "and promoted through the normal touchup path.")
        gate_class = COMPOSED_STILL_RESIZE
    elif length_changing:
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
                               (f"moved-in:{move['from_row']}"
                                f"[{move['from_index']}]")))
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
        for first, second in pairwise(placed):
            if second[0] < first[1]:
                raise TouchupRefused(
                    f"the plan overlaps on {row}: "
                    f"{first[2]} @{first[0]}..{first[1]} and "
                    f"{second[2]} @{second[0]}..{second[1]}",
                    "`AppendToTimeline` silently places nothing on "
                    "collision, so an overlapping plan never "
                    "reaches the delete",
                    _overlap_fix(row, first, second, tracks, changes))


def _overlap_fix(row: str, first, second,
                 tracks: Sequence[Mapping],
                 changes: Sequence[Mapping]) -> str:
    """What the caller can DO about an overlap on this row.

    The refusal used to recommend "state the ordering explicitly in
    the --edits JSON" - and a `retime` edit has NO ordering field, so
    the advice named something the caller could not act on (measured
    2026-10-20: "a ripple that overlaps a caption row is refused with
    a fix the caller cannot apply", `docs/benchmarks/
    2026-10-20-live-user-visible-latency.md`, finding 7).

    So the fix says what the collision ACTUALLY is, because the two
    kinds have different answers:

    - the frames the edit removes carry WORDS.  A caption card is a
      RENDERED file, so the cards whose words were removed cannot be
      fixed by moving them, and the cards after them cannot move
      without landing on them.  The edit is not a pause removal: it
      cuts speech.  The answer is a master without the cut.
    - the frames the edit removes carry no words.  Then the caption
      row's cards move with the picture, and the collision is
      positional: the caller can leave the colliding card where it is
      and own its timing.
    """
    caption = _is_caption_row_label(row, tracks)
    index = _label_index(second[2])
    removed = _removed_frames(changes)
    stale = _cards_over_removed(tracks, removed) if removed else []
    if caption and stale:
        named = ", ".join(item for item, _span in stale)
        return (
            f"the frames this edit removes carry words: {named} cover "
            "them. A caption card is a RENDERED file, so a card whose "
            "words were removed cannot be fixed by moving it - and the "
            "cards after them cannot move without landing on them. "
            "This edit cuts speech, not a pause: rebuild the reel from "
            "a master without those frames, or trim inside a block "
            "where the frames you remove carry no words.")
    if caption:
        return (
            f"the caption card {second[2]} is timed to the words, and "
            f"this plan moves it onto {first[2]}. Exclude it from the "
            f"move (\"exclude\": [[\"{row}\", {index}]] on the edit in "
            "the --edits JSON) and own its timing, or plan a move whose "
            "caption lands clear.")
    return (
        f"the plan moves {second[2]} onto {first[2]}. Exclude it from "
        f"the move (\"exclude\": [[\"{row}\", {index}]] on the edit in "
        "the --edits JSON) and say what covers the frames it leaves, or "
        "plan a shorter move.")


def _removed_frames(changes: Sequence[Mapping]) -> tuple | None:
    """The frames the edit takes OUT of the clip it shortens.

    The clip that changes its played length is the one that STRADDLES
    the cut, so it is the change the plan marks as an extension; the
    frames it no longer plays are the ones it used to play last.
    """
    for change in changes:
        how = str(getattr(change, "how", "") or "")
        if how != _ce.EXTEND:
            continue
        duration = int(getattr(change, "duration", 0))
        previous = int(getattr(change, "previous_duration", 0))
        if duration >= previous:
            continue
        record = int(getattr(change, "record_frame", 0))
        return (record + duration, record + previous)
    return None


def _cards_over_removed(tracks: Sequence[Mapping],
                         removed: tuple) -> list:
    """The caption cards whose words fall inside the removed frames."""
    out = []
    for track in tracks:
        if not _is_caption_row_label(_row_of(track["type"],
                                              int(track["index"])),
                                     tracks):
            continue
        for index, clip in enumerate(track.get("clips") or ()):
            start = int(clip.get("record_in") or 0)
            end = int(clip.get("record_out") or 0)
            if start < removed[1] and removed[0] < end:
                row = _row_of(track["type"], int(track["index"]))
                out.append((f"{row}[{index}]", (start, end)))
    return out


def _label_index(label: str) -> str:
    """The item index out of a placed label (`live:V4[21]` -> `21`)."""
    text = str(label or "")
    if "[" not in text or "]" not in text:
        return "?"
    return text.rsplit("[", 1)[-1].split("]", 1)[0]


def _is_caption_row_label(row: str, tracks: Sequence[Mapping]) -> bool:
    """Whether this row carries caption cards.

    Judged on the row's NAME, never its index: the caption row is the
    one the project's layout calls Subtitles, and an index says
    nothing about what plays on it.
    """
    wanted = str(row).upper()
    for track in tracks:
        if _row_of(track["type"], int(track["index"])) != wanted:
            continue
        name = str(track.get("name") or "").strip().casefold()
        return name in {"subtitles", "captions", "caption"}
    return False


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
    if _is_continuous_row(row, tracks):
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
    if _row_has_live_comps(row, tracks):
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
    if _is_continuous_row(row, tracks):
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
                     "stable_id": (
                         f"{row}:{clip.get('media_pool_item_id') or clip.get('source_file') or ''}:"
                         f"{clip.get('source_in_frame')}:{clip.get('source_out_frame')}:"
                         f"{item_index}"
                         if (clip.get("media_pool_item_id")
                             or clip.get("source_file"))
                         and clip.get("source_in_frame") is not None
                         and clip.get("source_out_frame") is not None
                         else None),
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


def _op_slip(edit, position, tracks, spans, changes, insertions,
             removals, moves, exclude, notes, in_place) -> bool:
    """Shift one item's source window while its record span stays fixed."""
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    delta = int(edit.get("delta_frames", 0))
    if not row or item_index is None or delta == 0:
        raise TouchupRefused(
            f"edit {position} (`slip`) needs `row`, `item` and a nonzero "
            f"`delta_frames` (got {dict(edit)!r})",
            "a source-window edit with no target or no movement is not "
            "a slip",
            f"give edit {position} a target and nonzero delta, then "
            f"re-run `ren touch`")
    clip = _find_clip(tracks, row, int(item_index))
    left_offset = clip.get("left_offset")
    source_frames = clip.get("source_frames")
    duration = int(clip["duration"])
    if left_offset is None or source_frames is None:
        raise TouchupRefused(
            f"edit {position} (`slip`) cannot read the source bounds of "
            f"{row}[{item_index}]",
            "the new source window cannot be proven to fit the media",
            "re-read the timeline and source metadata, then re-run "
            "`ren touch`")
    new_left = int(left_offset) + delta
    if new_left < 0 or new_left + duration > int(source_frames):
        raise TouchupRefused(
            f"edit {position} (`slip`) requests source frames "
            f"{new_left}..{new_left + duration} of {source_frames}",
            "the source window exceeds measured media bounds",
            "choose a delta that keeps the full played span inside the "
            "source, then re-run `ren touch`")
    track_type = "audio" if row.startswith("A") else "video"
    changes.append(_ce.ItemChange(
        track_type=track_type, track_index=int(row[1:]),
        item_index=int(item_index), record_frame=int(clip["record_in"]),
        duration=duration, left_offset=new_left,
        previous_record=int(clip["record_in"]),
        previous_duration=duration, how="slip",
        comp_count=_ce.treatment_comps(clip),
        right_offset=int(source_frames) - new_left - duration,
        name=clip.get("name", "")))
    notes.append(f"edit {position}: slip {row}[{item_index}] source "
                 f"window by {delta:+d}f; record span unchanged")
    return False


def _op_resize_still(edit, position, tracks, spans, changes, insertions,
                     removals, moves, exclude, notes, in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    duration = edit.get("duration")
    if not row or item_index is None or duration is None:
        raise TouchupRefused(
            f"edit {position} (`resize_still`) needs `row`, `item` and "
            f"`duration` (got {dict(edit)!r})",
            "a still resize must name one existing overlay and its new "
            "frame duration",
            f"give edit {position} all three keys, then re-run `ren touch`")
    if (not row.startswith("V") or not row[1:].isdigit()
            or _is_continuous_row(row, tracks)):
        raise TouchupRefused(
            f"edit {position} (`resize_still`) targets {row}",
            "only an overlay on a sparse V3+ row can be re-placed without "
            "changing the picture program",
            "name a native still on an untreated overlay row, then "
            "re-run `ren touch`")
    clip = _find_clip(tracks, row, int(item_index))
    source = str(clip.get("source_file") or "")
    if (clip.get("media_type") != "Still"
            or not source.lower().endswith(".png")):
        raise TouchupRefused(
            f"edit {position} (`resize_still`) targets {row}[{item_index}] "
            f"with media_type={clip.get('media_type')!r}, "
            f"source_file={source!r}",
            "the direct duration route is qualified only for a native "
            "PNG still; video and animated artwork keep their own timing",
            "use `resize_still` only for a Resolve `Still` backed by PNG")
    comp_count = _ce.treatment_comps(clip)
    if comp_count:
        raise TouchupRefused(
            f"edit {position} (`resize_still`) targets {row}[{item_index}] "
            f"with {comp_count} drawing Fusion comp(s)",
            "a played-length change invalidates a comp keyed to the old "
            "window, and the still-resize path deliberately does not run "
            "the manifest comp pass",
            "rebuild the reel or remove the treatment through its owning "
            "plan before resizing")
    old = int(clip["duration"])
    new = int(duration)
    if new <= 0:
        raise TouchupRefused(
            f"edit {position} (`resize_still`) asks for {new} frames",
            "a still must play for at least one frame",
            "give `duration` a positive frame count")
    if new == old:
        raise TouchupRefused(
            f"edit {position} (`resize_still`) keeps the existing "
            f"{old}-frame duration",
            "a same-length delete and re-place would add risk without "
            "changing the timeline",
            "state a different positive frame duration")
    left_offset = clip.get("left_offset")
    if left_offset is None:
        raise TouchupRefused(
            f"edit {position} (`resize_still`) cannot read the source "
            f"offset of {row}[{item_index}]",
            "the replacement must preserve the existing media window",
            "re-read the reel and retry when its source offset is readable")
    try:
        left_offset = int(left_offset)
    except (TypeError, ValueError) as exc:
        raise TouchupRefused(
            f"edit {position} (`resize_still`) has invalid source offset "
            f"{left_offset!r}",
            "the staged replacement must preserve a whole source frame",
            "re-read the reel and retry when its source offset is readable") from exc
    record = int(clip["record_in"])
    _check_free(spans.get(row, []), row, record, new,
                ignore=(record, record + old),
                what=f"edit {position} (`resize_still`)")
    changes.append(_ce.ItemChange(
        track_type="video", track_index=int(row[1:]),
        item_index=int(item_index), record_frame=record,
        duration=new, left_offset=left_offset,
        previous_record=record, previous_duration=old,
        how=_ce.RESIZE_STILL, comp_count=0,
        right_offset=clip.get("right_offset"), name=clip.get("name", "")))
    notes.append(f"edit {position}: resize still {row}[{item_index}] "
                 f"@{record} {old}->{new}f; same item only, no ripple")
    return True


def _overlay_media_kind(clip: Mapping, position: int, op: str) -> str:
    """The media kind of one overlay target, or a refusal by name.

    The route covers a native PNG still AND a rendered movie overlay -
    the engine's own lower thirds are `.mov` files - and both draw
    through the same transform chain, so the geometry of a move or a
    resize is the same for both. The kind is decided by the media's OWN
    kind and its own container, never by its file name.

    A rendered movie overlay may carry an AUDIO stream: Resolve labels
    such a file "Video + Audio", because the label names the streams the
    file carries and not the kind of media it is. The name bar the
    captain placed by hand is exactly that file, and refusing it refused
    the graphic the brief was about.

    What separates the rendered card from a DIALOGUE clip is the
    media's own alpha plane, and it is measured: a rendered overlay
    carries one and a dialogue clip does not. A video without one is
    refused by name, so widening the label does not widen the route to
    the footage.
    """
    from library.tools import overlay_media

    kind, why = overlay_media.classify(clip)
    if kind is None:
        raise TouchupRefused(
            f"edit {position} (`{op}`) targets unsupported media "
            f"{str(clip.get('source_file') or '')!r}",
            "this route covers a native PNG still or a rendered movie "
            f"overlay, and {why}",
            "use a native PNG still or a rendered movie overlay without a "
            "Fusion treatment")
    if kind == overlay_media.RENDERED_VIDEO:
        source = str(clip.get("source_file") or "")
        plane = overlay_media.alpha_plane(source)
        if plane is None:
            raise TouchupRefused(
                f"edit {position} (`{op}`) cannot read the media at "
                f"{source!r}",
                "whether the media carries an alpha plane is what tells a "
                "rendered overlay from a dialogue clip, and it could not "
                "be measured",
                "point the request at a readable rendered overlay")
        if not plane:
            raise TouchupRefused(
                f"edit {position} (`{op}`) targets {source!r}, which "
                "carries no alpha plane",
                "a rendered overlay carries an alpha plane and a "
                "dialogue clip does not, so this media is footage and "
                "not an overlay",
                "target a rendered overlay or a native PNG still")
        speed = clip.get("speed")
        try:
            normal = speed is not None and abs(float(speed) - 100.0) <= 1e-6
        except (TypeError, ValueError):
            normal = False
        if not normal:
            raise TouchupRefused(
                f"edit {position} (`{op}`) targets an overlay whose speed "
                f"is {speed!r}",
                "a retimed overlay's source window does not map one to one "
                "onto the asset's frames, so the frame that draws it "
                "cannot be located",
                "use an overlay playing at normal speed")
    return kind


def _op_resize_overlay(edit, position, tracks, spans, changes, insertions,
                       removals, moves, exclude, notes, in_place) -> bool:
    """Declare output-pixel geometry for one untreated overlay.

    The target is a native PNG still or a rendered movie overlay - both
    draw through the same transform chain. Native transform lowering
    happens against the live staged item in ``_apply_in_place`` so it can
    use that timeline's resolution and proxy units. Promotion is gated by
    a rendered enabled/disabled pixel diff.
    """
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    try:
        width, height = int(edit["width"]), int(edit["height"])
    except (KeyError, TypeError, ValueError) as invalid:
        raise TouchupRefused(
            f"edit {position} (`resize_overlay`) needs integer width/height",
            "output geometry must be expressed in positive delivery pixels",
            "provide positive output-pixel width and height") from invalid
    if (not row.startswith("V") or not row[1:].isdigit()
            or int(row[1:]) < 3 or item_index is None
            or width <= 0 or height <= 0):
        raise TouchupRefused(
            f"edit {position} (`resize_overlay`) has an invalid overlay target or size",
            "only an existing V3+ overlay with positive output dimensions is supported",
            "address one overlay row item and provide positive width/height")
    clip = _find_clip(tracks, row, int(item_index))
    source = str(clip.get("source_file") or "")
    kind = _overlay_media_kind(clip, position, "resize_overlay")
    if _ce.treatment_comps(clip):
        raise TouchupRefused(
            f"edit {position} (`resize_overlay`) targets {row}[{item_index}] "
            "with a drawing Fusion comp",
            "the comp draws the overlay itself, so the native transform is "
            "not what places it",
            "use an overlay without a Fusion treatment")
    if clip.get("enabled") is not True:
        raise TouchupRefused(
            f"edit {position} (`resize_overlay`) target {row}[{item_index}] "
            "is disabled or its enabled state is unreadable",
            "rendered-pixel verification temporarily toggles the candidate item",
            "use a visible overlay with readable enabled state")
    in_place.append({"kind": "overlay_resize", "row": row,
                     "item_index": int(item_index),
                     "record_frame": int(clip["record_in"]),
                     "width": width, "height": height,
                     "media_kind": kind,
                     "source_file": source})
    notes.append(f"edit {position}: resize {row}[{item_index}] to "
                 f"{width}x{height} output pixels; verify rendered box")
    return False


def _op_position_overlay(edit, position, tracks, spans, changes, insertions,
                         removals, moves, exclude, notes, in_place) -> bool:
    """Declare an output-pixel translation for one untreated overlay.

    The target is a native PNG still or a rendered movie overlay - both
    draw through the same transform chain. Native transform lowering
    happens against the live staged item in ``_apply_in_place`` so it can
    use that timeline's resolution and proxy units. Size and timing are
    preserved; only the box centre moves by the signed (dx, dy)
    output-pixel delta. Promotion is gated by a rendered enabled/disabled
    pixel diff, the same evidence the resize path uses.
    """
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    try:
        dx, dy = float(edit["dx"]), float(edit["dy"])
    except (KeyError, TypeError, ValueError) as invalid:
        raise TouchupRefused(
            f"edit {position} (`position_overlay`) needs numeric dx/dy",
            "output geometry must be expressed as a signed delivery-pixel delta",
            "provide signed output-pixel dx and dy") from invalid
    from math import isfinite as _isfinite
    if (not row.startswith("V") or not row[1:].isdigit()
            or int(row[1:]) < 3 or item_index is None
            or not _isfinite(dx) or not _isfinite(dy)
            or (dx == 0 and dy == 0)):
        raise TouchupRefused(
            f"edit {position} (`position_overlay`) has an invalid overlay target or delta",
            "only an existing V3+ overlay with a non-zero finite output-pixel delta is supported",
            "address one overlay row item and provide a non-zero dx/dy")
    clip = _find_clip(tracks, row, int(item_index))
    source = str(clip.get("source_file") or "")
    kind = _overlay_media_kind(clip, position, "position_overlay")
    if _ce.treatment_comps(clip):
        raise TouchupRefused(
            f"edit {position} (`position_overlay`) targets {row}[{item_index}] "
            "with a drawing Fusion comp",
            "the comp draws the overlay itself, so the native transform is "
            "not what places it",
            "use an overlay without a Fusion treatment")
    if clip.get("enabled") is not True:
        raise TouchupRefused(
            f"edit {position} (`position_overlay`) target {row}[{item_index}] "
            "is disabled or its enabled state is unreadable",
            "rendered-pixel verification temporarily toggles the candidate item",
            "use a visible overlay with readable enabled state")
    in_place.append({"kind": "overlay_position", "row": row,
                     "item_index": int(item_index),
                     "record_frame": int(clip["record_in"]),
                     "dx": dx, "dy": dy,
                     "media_kind": kind,
                     "source_file": source})
    notes.append(f"edit {position}: position {row}[{item_index}] by "
                 f"({dx:g}, {dy:g}) output pixels; verify rendered box")
    return False


def _require_simple_picture_clip(clip: Mapping, op: str, position: int,
                                 row: str, index: int) -> None:
    if not row.startswith("V"):
        raise TouchupRefused(
            f"edit {position} (`{op}`) targets {row}[{index}]",
            "the first candidate adapter supports picture clips only",
            "use a video-track target")
    if clip.get("speed") is None or abs(float(clip["speed"]) - 100.0) > 1e-6:
        raise TouchupRefused(
            f"edit {position} (`{op}`) target {row}[{index}] has speed "
            f"{clip.get('speed')!r}",
            "variable-speed source/record conversion is not qualified",
            "use normal-speed clips")
    fusion = clip.get("fusion") or {}
    if (int(fusion.get("comp_count") or 0) > 0
            or _ce.treatment_comps(clip)):
        raise TouchupRefused(
            f"edit {position} (`{op}`) target {row}[{index}] carries a "
            "Fusion composition",
            "the candidate path cannot prove comp state on reconstructed items",
            "use a clip without Fusion compositions")
    if clip.get("left_offset") is None:
        raise TouchupRefused(
            f"edit {position} (`{op}`) target {row}[{index}] has no readable source offset",
            "source-window state is required for exact candidate placement",
            "re-read the target and retry")
    if not isinstance(clip.get("transform"), Mapping):
        raise TouchupRefused(
            f"edit {position} (`{op}`) target {row}[{index}] has no readable transform",
            "candidate-local state carry needs a measured native transform",
            "re-read the target and retry")
    unsupported_state = []
    for state_field in ("keyframes",):
        if clip.get(state_field) is None:
            unsupported_state.append(f"unreadable {state_field}")
        elif clip[state_field]:
            unsupported_state.append(state_field)
    color = clip.get("color")
    if not isinstance(color, Mapping):
        unsupported_state.append("unreadable color state")
    elif (color.get("cdl") or color.get("color_group")
          or color.get("luts")):
        unsupported_state.append("color grade")
    if unsupported_state:
        raise TouchupRefused(
            f"edit {position} (`{op}`) target {row}[{index}] carries "
            f"unsupported state: {', '.join(unsupported_state)}",
            "the candidate reconstruction cannot preserve and prove this item state",
            "use a plain clip with readable default editorial state")


def _adjacent_clips(tracks: Sequence[Mapping], row: str, index: int,
                    position: int, op: str) -> tuple[Mapping, Mapping,
                                                      Mapping, Mapping]:
    track = next((track for track in tracks
                  if _row_of(track["type"], int(track["index"])) == row), None)
    clips = list((track or {}).get("clips") or ())
    if not 0 < index < len(clips) - 1:
        raise TouchupRefused(
            f"edit {position} (`{op}`) target {row}[{index}] has no two neighbours",
            "slide must keep both neighbouring source windows intact",
            "target an item with a preceding and following clip")
    previous, target, following = clips[index - 1:index + 2]
    if (int(previous["record_out"]) != int(target["record_in"])
            or int(target["record_out"]) != int(following["record_in"])):
        raise TouchupRefused(
            f"edit {position} (`{op}`) target is not between two contiguous clips",
            "this slide route cannot manufacture or cover an existing gap",
            "address a target with contiguous neighbours")
    return track, previous, target, following


def _change_from_clip(track: Mapping, index: int, clip: Mapping,
                      record: int, duration: int, left: int, how: str):
    return _ce.ItemChange(
        track_type=str(track["type"]), track_index=int(track["index"]),
        item_index=int(index), record_frame=int(record),
        duration=int(duration), left_offset=int(left),
        previous_record=int(clip["record_in"]),
        previous_duration=int(clip["duration"]), how=how,
        comp_count=0, right_offset=clip.get("right_offset"),
        name=str(clip.get("name") or ""))


def _op_split(edit, position, tracks, spans, changes, insertions,
              removals, moves, exclude, notes, in_place) -> bool:
    row, index = str(edit.get("row") or "").upper(), edit.get("item")
    if not row.startswith("V") or index is None:
        raise TouchupRefused(
            f"edit {position} (`split`) needs a video row and item",
            "a split must identify one picture clip",
            "address the clip by video row and item")
    clip = _find_clip(tracks, row, int(index))
    _require_simple_picture_clip(clip, "split", position, row, int(index))
    at = int(edit["at_frame"])
    start, end = int(clip["record_in"]), int(clip["record_out"])
    if not start < at < end:
        raise TouchupRefused(
            f"edit {position} (`split`) boundary {at} is outside the interior of [{start}, {end})",
            "split must leave two positive, non-overlapping spans",
            "choose an interior record-frame boundary")
    left = int(clip["left_offset"])
    second_left = left + at - start
    duration_a, duration_b = at - start, end - at
    source_frames = clip.get("source_frames")
    if source_frames is None or second_left + duration_b > int(source_frames):
        raise TouchupRefused(
            f"edit {position} (`split`) source window is unreadable or exceeds media bounds",
            "both output source spans must be measured and inside the source",
            "re-read source duration and choose an in-bounds cut")
    changes.append(_change_from_clip(
        next(track for track in tracks
             if _row_of(track["type"], int(track["index"])) == row),
        int(index), clip, start, duration_a, left, _ce.SHIFT))
    insertions.append(_PendingSwap(
        row=row, record_frame=at, duration=duration_b,
        media=str(clip["source_file"]), left_offset=second_left,
        carry_from=(row, int(index)), name=str(clip.get("name") or ""),
        position=position))
    notes.append(f"edit {position}: split {row}[{index}] at {at}; verify both source and record spans")
    return True


def _op_slide(edit, position, tracks, spans, changes, insertions,
              removals, moves, exclude, notes, in_place) -> bool:
    row, index = str(edit.get("row") or "").upper(), edit.get("item")
    if not row.startswith("V") or index is None:
        raise TouchupRefused(
            f"edit {position} (`slide`) needs a video row and item",
            "the first candidate slide composes one picture item and both neighbours",
            "address one video item with contiguous neighbours")
    track, previous, target, following = _adjacent_clips(
        tracks, row, int(index), position, "slide")
    delta = int(edit["to_record"]) - int(target["record_in"])
    if not delta:
        raise TouchupRefused(
            f"edit {position} (`slide`) requests the current record frame",
            "an unchanged cut needs no staged write",
            "drop the no-op or choose a different record frame")
    for idx, clip in zip((int(index) - 1, int(index), int(index) + 1),
                         (previous, target, following)):
        _require_simple_picture_clip(clip, "slide", position, row, idx)
    if abs(delta) >= min(int(previous["duration"]), int(following["duration"])):
        raise TouchupRefused(
            f"edit {position} (`slide`) displacement {delta} consumes a neighbour",
            "the target must remain between positive-duration adjacent items",
            "reduce the displacement")
    prev_len = int(previous["duration"]) + delta
    next_len = int(following["duration"]) - delta
    next_left = int(following["left_offset"]) + delta
    previous_source_frames = previous.get("source_frames")
    following_source_frames = following.get("source_frames")
    if (prev_len <= 0 or next_len <= 0 or next_left < 0
            or previous_source_frames is None
            or int(previous["left_offset"]) + prev_len
            > int(previous_source_frames)
            or following_source_frames is None
            or next_left + next_len > int(following_source_frames)
            or (delta > 0
                and int(previous.get("right_offset") or 0) < delta)):
        raise TouchupRefused(
            f"edit {position} (`slide`) lacks source headroom for {delta:+d} frames",
            "both neighboring source windows must remain in media bounds",
            "choose a smaller displacement")
    first_index = int(index) - 1
    last_index = int(index) + 1
    changes.extend((
        _change_from_clip(track, first_index, previous,
                          int(previous["record_in"]), prev_len,
                          int(previous["left_offset"]), _ce.EXTEND),
        _change_from_clip(track, int(index), target,
                          int(edit["to_record"]), int(target["duration"]),
                          int(target["left_offset"]), _ce.SHIFT),
        _change_from_clip(track, last_index, following,
                          int(following["record_in"]) + delta, next_len,
                          next_left, _ce.SHIFT),
    ))
    notes.append(f"edit {position}: slide {row}[{index}] by {delta:+d} frames with both neighbours")
    return True


def _op_roll(edit, position, tracks, spans, changes, insertions,
             removals, moves, exclude, notes, in_place) -> bool:
    row, index = str(edit.get("row") or "").upper(), edit.get("item")
    if not row.startswith("V") or index is None:
        raise TouchupRefused(
            f"edit {position} (`roll`) needs an outgoing video row and item",
            "the first candidate roll changes one shared cut between two picture clips",
            "address the outgoing video clip by row and item")
    track = next((track for track in tracks
                  if _row_of(track["type"], int(track["index"])) == row), None)
    clips = list((track or {}).get("clips") or ())
    index = int(index)
    if not 0 <= index < len(clips) - 1:
        raise TouchupRefused(
            f"edit {position} (`roll`) target {row}[{index}] has no following clip",
            "a roll requires two adjacent items at one shared cut",
            "target the outgoing item at a contiguous cut")
    outgoing, incoming = clips[index:index + 2]
    if int(outgoing["record_out"]) != int(incoming["record_in"]):
        raise TouchupRefused(
            f"edit {position} (`roll`) items are not contiguous",
            "a shared-cut roll cannot change an existing gap or overlap",
            "target adjacent clips at an exact shared boundary")
    _require_simple_picture_clip(outgoing, "roll", position, row, index)
    _require_simple_picture_clip(incoming, "roll", position, row, index + 1)
    delta = int(edit["delta_frames"])
    if not delta or abs(delta) >= min(int(outgoing["duration"]),
                                      int(incoming["duration"])):
        raise TouchupRefused(
            f"edit {position} (`roll`) displacement {delta} is empty or consumes an item",
            "both sides of the cut must retain positive duration",
            "choose a smaller non-zero cut movement")
    out_len = int(outgoing["duration"]) + delta
    in_len = int(incoming["duration"]) - delta
    in_left = int(incoming["left_offset"]) + delta
    outgoing_source_frames = outgoing.get("source_frames")
    incoming_source_frames = incoming.get("source_frames")
    if (in_left < 0 or outgoing_source_frames is None
            or int(outgoing["left_offset"]) + out_len
            > int(outgoing_source_frames)
            or incoming_source_frames is None
            or in_left + in_len > int(incoming_source_frames)
            or (delta > 0
                and int(outgoing.get("right_offset") or 0) < delta)):
        raise TouchupRefused(
            f"edit {position} (`roll`) lacks source headroom for {delta:+d} frames",
            "both source windows must remain inside measured media bounds",
            "choose a smaller cut movement")
    changes.extend((
        _change_from_clip(track, index, outgoing,
                          int(outgoing["record_in"]), out_len,
                          int(outgoing["left_offset"]), _ce.EXTEND),
        _change_from_clip(track, index + 1, incoming,
                          int(incoming["record_in"]) + delta, in_len,
                          in_left, _ce.SHIFT),
    ))
    notes.append(f"edit {position}: roll {row}[{index}] shared cut by {delta:+d} frames")
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
    if _is_continuous_row(row, tracks) or _is_audio_row(row):
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


def _op_audio_fade(edit, position, tracks, spans, changes, insertions,
                   removals, moves, exclude, notes, in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    try:
        fade_in = int(edit["fade_in_frames"])
        fade_out = int(edit["fade_out_frames"])
    except (KeyError, TypeError, ValueError) as invalid:
        raise TouchupRefused(
            f"edit {position} (`audio_fade`) needs integer frame counts",
            "Resolve's native fade API takes frame durations",
            "re-read the audio item and provide non-negative fade durations"
        ) from invalid
    if not row.startswith("A") or item_index is None:
        raise TouchupRefused(
            f"edit {position} (`audio_fade`) needs an audio row and item",
            "native item fades must target one audio clip",
            "address the exact audio row and item")
    if fade_in < 0 or fade_out < 0 or (fade_in == 0 and fade_out == 0):
        raise TouchupRefused(
            f"edit {position} (`audio_fade`) needs at least one positive "
            "fade duration and no negative values",
            "an empty or negative fade is not a meaningful edit",
            "provide non-negative fade durations with one greater than zero")
    clip = _find_clip(tracks, row, int(item_index))
    duration = int(clip["duration"])
    if fade_in + fade_out > duration:
        raise TouchupRefused(
            f"edit {position} fades total {fade_in + fade_out} frames on "
            f"a {duration}-frame item",
            "overlapping endpoint fades are outside this adapter's "
            "supported conditions",
            "shorten the fades so their combined duration fits the item")
    in_place.append({
        "kind": "audio_fade", "row": row,
        "item_index": int(item_index),
        "record_frame": int(clip["record_in"]),
        "record_out": int(clip["record_out"]),
        "source_in": int(clip["source_in_frame"]),
        "source_out": int(clip["source_out_frame"]),
        "source_file": str(clip["source_file"]),
        "fade_in_frames": fade_in, "fade_out_frames": fade_out,
    })
    notes.append(f"edit {position}: set {row}[{item_index}] audio fades "
                 f"to {fade_in}f in / {fade_out}f out")
    return False


def _op_native_transition(edit, position, tracks, spans, changes,
                          insertions, removals, moves, exclude, notes,
                          in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    incoming_index = edit.get("item")
    if not row.startswith("V") or incoming_index is None:
        raise TouchupRefused(
            f"edit {position} (`native_transition`) needs a video row and "
            "incoming item index",
            "native transitions join two adjacent video items",
            "address the exact incoming video item")
    try:
        cut = int(edit["cut_frame"])
        duration = int(edit["duration_frames"])
        outgoing_uid = str(edit["outgoing_unique_id"])
        incoming_uid = str(edit["incoming_unique_id"])
    except (KeyError, TypeError, ValueError) as invalid:
        raise TouchupRefused(
            f"edit {position} (`native_transition`) needs exact carrier ids, "
            "cut frame and duration frames",
            "an addressless cut cannot safely select a transition target",
            "provide outgoing/incoming ids and positive duration frames") from invalid
    if duration < 1 or not outgoing_uid or not incoming_uid:
        raise TouchupRefused(
            f"edit {position} (`native_transition`) has invalid duration or "
            "empty item identity",
            "Resolve AddTransition needs two identified clips and a duration",
            "provide non-empty item ids and a positive frame duration")
    matching = [
        (track, index, clip)
        for track in tracks
        if _row_of(track["type"], int(track["index"])) == row
        for index, clip in enumerate(track.get("clips") or ())
        if clip.get("unique_id") in {outgoing_uid, incoming_uid}
    ]
    outgoing = [(index, clip) for _track, index, clip in matching
                if clip.get("unique_id") == outgoing_uid]
    incoming = [(index, clip) for _track, index, clip in matching
                if clip.get("unique_id") == incoming_uid]
    if len(outgoing) != 1 or len(incoming) != 1:
        raise TouchupRefused(
            f"edit {position} transition carrier ids matched "
            f"{len(outgoing)} outgoing and {len(incoming)} incoming clips",
            "the exact cut is ambiguous or no longer on the named track",
            "re-read the timeline and address both adjacent clips again")
    out_index, out_clip = outgoing[0]
    in_index, in_clip = incoming[0]
    if (in_index != out_index + 1 or in_index != int(incoming_index)
            or int(out_clip["record_out"]) != cut
            or int(in_clip["record_in"]) != cut + 1):
        raise TouchupRefused(
            f"edit {position} transition ids do not form the exact cut "
            f"{row}@{cut}",
            "the outgoing/incoming pair must be adjacent at the named frame",
            "re-read the adjacent items and provide their exact cut frame")
    raw_type = str(edit["transition_type"])
    category = str(edit["category"])
    alignment = str(edit.get("alignment", "center"))
    from library.tools import native_ops

    canonical = native_ops.native_canonical(raw_type)
    if canonical is None:
        native_ops.refuse_native_transition(raw_type, category)
        raise TouchupRefused(
            f"edit {position} native transition {raw_type!r} is not granted",
            "the requested Resolve transition has no measured native route",
            "choose a granted transition type")
    if category not in native_ops.granted_categories(canonical):
        raise TouchupRefused(
            f"edit {position} {raw_type!r} is not granted in {category!r}",
            "the native type/category pair has not been measured as available",
            "choose a granted category for this type")
    if alignment != "center":
        raise TouchupRefused(
            f"edit {position} alignment {alignment!r} is unsupported",
            "only centered transition placement has an exact-cut verifier",
            "use center alignment")
    in_place.append({
        "kind": "native_transition", "row": row,
        "item_index": in_index, "record_frame": int(in_clip["record_in"]),
        "cut_frame": cut, "outgoing_unique_id": outgoing_uid,
        "incoming_unique_id": incoming_uid,
        "duration_frames": duration,
        "transition_type": native_ops.resolve_transition_name(canonical),
        "category": category, "alignment": alignment,
    })
    notes.append(
        f"edit {position}: add {raw_type} at exact {row}@{cut} cut "
        f"for {duration} frames on staged timeline")
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
    if _row_has_live_comps(row, tracks):
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


def _op_repair_hold_window(edit, position, tracks, spans, changes,
                           insertions, removals, moves, exclude, notes,
                           in_place) -> bool:
    row = str(edit.get("row") or "").upper()
    item_index = edit.get("item")
    if not row or item_index is None:
        raise TouchupRefused(
            f"edit {position} (`repair_hold_window`) needs "
            f"`row` and `item` (got {dict(edit)!r})",
            "a window repair naming no item cannot be staged",
            f"give edit {position} `row` and `item`, then re-run "
            f"`ren touch`")
    try:
        record_frame = int(edit.get("record_frame"))
        duration = int(edit.get("duration"))
    except (TypeError, ValueError):
        raise TouchupRefused(
            f"edit {position} (`repair_hold_window`) needs "
            f"`record_frame` and `duration` as frame counts "
            f"(got {edit.get('record_frame')!r}/"
            f"{edit.get('duration')!r})",
            "the repair pins its item by span, so a shifted timeline "
            "refuses instead of repairing the wrong item",
            f"give edit {position} integer `record_frame` and "
            f"`duration`, then re-run `ren touch`")
    source = str(edit.get("source") or "")
    if not source:
        raise TouchupRefused(
            f"edit {position} (`repair_hold_window`) names no `source`",
            "the repair pins its item by the source basename it "
            "expects, so a re-placed timeline refuses instead of "
            "repairing whatever sits at that span now",
            f"name the hold file's basename as `source` on edit "
            f"{position}, then re-run `ren touch`")
    if _is_audio_row(row):
        raise TouchupRefused(
            f"edit {position} repairs a hold window on {row}",
            "a window repair rebinds Fusion comp inputs - audio rows "
            "carry no Fusion comps",
            "repair the hold on its video row, then re-run `ren touch`")
    clip = _find_clip(tracks, row, int(item_index))
    if int(clip["record_in"]) != record_frame:
        raise TouchupRefused(
            f"edit {position} (`repair_hold_window`) expects "
            f"{row}[{item_index}] at record frame {record_frame}, and "
            f"it starts at {clip['record_in']}",
            "the timeline moved under the spec - repairing by position "
            "would repair the wrong item",
            f"re-read the reel and restate edit {position}, then "
            f"re-run `ren touch`")
    if int(clip["duration"]) != duration:
        raise TouchupRefused(
            f"edit {position} (`repair_hold_window`) expects "
            f"{row}[{item_index}] to play {duration}f, and it plays "
            f"{clip['duration']}f",
            "a window is judged against the frames its item plays - "
            "repairing against a stale length covers the wrong span",
            f"re-read the reel and restate edit {position}, then "
            f"re-run `ren touch`")
    base = str(clip.get("source_file") or "").rsplit("/", 1)[-1]
    if base != source:
        raise TouchupRefused(
            f"edit {position} (`repair_hold_window`) expects "
            f"{source!r} at {row}[{item_index}], and {base!r} is there",
            "the timeline changed under the spec - repairing by span "
            "alone would repair the wrong pixels",
            f"re-read the reel and restate edit {position}, then "
            f"re-run `ren touch`")
    from library.tools import hold_window_repair as _hold
    try:
        native = (clip.get("speed") is not None
                  and abs(float(clip.get("speed"))) <= 1e-9)
    except (TypeError, ValueError):
        native = False
    if not _hold.is_hold_source(
            str(clip.get("source_file") or "")) and not native:
        raise TouchupRefused(
            f"edit {position} (`repair_hold_window`) targets "
            f"{row}[{item_index}] {base!r}, which is neither a baked "
            f"hold file nor a native zero-speed hold",
            "a window repair outside a freeze-hold tail is a treatment "
            "change by another name, and treatments ride the comp pass "
            "(`ren build`), not a touchup",
            f"point edit {position} at the reel's freeze-hold tail, "
            f"then re-run `ren touch`")
    if not _ce.treatment_comps(clip):
        raise TouchupRefused(
            f"edit {position} (`repair_hold_window`) targets "
            f"{row}[{item_index}], which carries no drawing comp",
            "there is no media window to repair on an untreated item",
            "nothing to do - drop the edit and re-run `ren touch`")
    in_place.append({"kind": "repair_hold_window", "row": row,
                     "item_index": int(item_index),
                     "record_frame": record_frame,
                     "duration": duration,
                     "source": base})
    notes.append(f"edit {position}: repair hold windows on "
                 f"{row}[{item_index}] {base!r} ({duration}f, in place - "
                 f"rebind with stable re-read verify, no delete/place)")
    return False


_OP_HANDLERS = {
    "move": _op_move,
    "swap_pixels": _op_swap_pixels,
    "add_overlay": _op_add_overlay,
    "add_row": _op_add_row,
    "set_enabled": _op_set_enabled,
    "audio_fade": _op_audio_fade,
    "native_transition": _op_native_transition,
    "remove_overlay": _op_remove_overlay,
    "retime": _op_retime,
    "slip": _op_slip,
    "resize_still": _op_resize_still,
    "resize_overlay": _op_resize_overlay,
    "position_overlay": _op_position_overlay,
    "split": _op_split,
    "slide": _op_slide,
    "roll": _op_roll,
    "set_properties": _op_set_properties,
    "entry_motion": _op_entry_motion,
    "repair_hold_window": _op_repair_hold_window,
}

#: Every operation a touch-up can carry. ONE enumeration: the undo
#: journal reads the rebuild policy of an accepted touch from THIS list,
#: so a new op cannot leave a landed touch without a way back
#: (`library/tools/undo_journal.py`).
TOUCH_OPS = frozenset(_OP_HANDLERS)


#: The fields a touchup EDIT carries.  The refusal that recommends an
#: argument may name only these - the vocabulary the caller can supply
#: on this surface - and `refusal_taxonomy` checks a fix against it.
EDIT_FIELDS = frozenset({
    "op", "row", "item", "item_index", "record_frame", "duration",
    "media", "source", "name", "properties", "enabled", "exclude",
    "left_offset", "to_record", "to_row", "record", "alignment",
    "delta_frames", "fade_in_frames", "fade_out_frames",
})
"""Every field a touchup edit may carry, read off the handlers above."""


#: The flags `ren touch-reel` accepts (manage_project.py's
#: `touch-reel` parser).  A refusal's fix may name these and no other
#: argument: a fix that recommends a flag the caller cannot pass is
#: advice they cannot act on.
TOUCHUP_FLAGS = frozenset({
    "--edits", "--edits-file", "--allow-drop", "--supersede",
    "--accept-editor-changes", "--all-reels", "--old-clip",
    "--new-media", "--row", "--staged",
})
"""The CLI flags the touch-up surface accepts."""


def supplyable_arguments() -> tuple[str, ...]:
    """What the caller can supply on the touch-up edit surface.

    The ops the gate knows, the fields an edit may carry, and the CLI
    flags that carry it.  A refusal's fix is checked against this, so
    it cannot recommend a field the caller has no way to set.
    """
    return tuple(sorted(set(_OP_HANDLERS) | set(EDIT_FIELDS)
                        | set(TOUCHUP_FLAGS)))


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
    carry_from: tuple | None = None
    declared_properties: Mapping[str, Any] | None = None
    preservation_state: Mapping[str, Any] | None = None
    name: str = ""
    position: int = 0


def _is_native_still(pool_item: Any) -> bool:
    """Whether a pool item is a native Resolve still (PNG image).

    The same predicate `still_placement.place_still_exact` enforces:
    Resolve's `AppendToTimeline` takes no duration for image items, so
    a still insertion can never ride the normal one-call placement -
    its duration arrives through the still-duration preference route
    instead. Anything unreadable answers False, keeping the normal
    route: a caller that cannot prove a still gets the video path.
    """
    try:
        kind = pool_item.GetClipProperty("Type")
        path = pool_item.GetClipProperty("File Path")
    except Exception:  # noqa: BLE001 - unreadable is not a still
        return False
    return kind == "Still" and str(path or "").lower().endswith(".png")


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

    def reachable_reason(self, changes) -> str | None:
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

    def expects_comp(self, row: str, record_frame: int) -> bool | None:
        return False


class _StillResizeRederiver(_ce.CompRederiver):
    """The no-comp receipt reserved for one untreated native still resize."""

    def reachable_reason(self, changes) -> str | None:
        invalid = [change for change in changes
                   if change.how != _ce.RESIZE_STILL
                   or change.comp_count != 0
                   or change.track_type != "video"
                   or change.track_index < 3]
        if invalid:
            return ("the no-comp still route accepts only untreated V3+ "
                    f"stills; got {[f'{c.row}[{c.item_index}]' for c in invalid]}")
        if len([change for change in changes if change.played_length_changes]) != 1:
            return "the no-comp still route accepts exactly one length change"
        return None

    def rederive(self, changes) -> dict:
        return {"ran": True, "ok": True, "comp_pass": "not_needed",
                "why": "one native PNG still changed length and carried no drawing comp"}

    def expects_comp(self, row: str, record_frame: int) -> bool | None:
        return False


# ── The recorded fusion manifest, for the length-changing class ──────


def recorded_fusion_manifest(project_folder: str,
                             timeline_name: str,
                             tracks: Sequence[Mapping] | None = None
                             ) -> dict | None:
    """The fusion manifest the last build wrote for this reel.

    `reel_look.apply_comps` writes it beside the project under
    scratch (`<slug>_fusion_manifest.json`) before launching the
    comp pass.  When it is there it is the manifest the re-derivation
    needs; when it is not, the length-changing class has no route to
    the comp generator and refuses rather than guessing one.

    WHERE THE FILE IS, and why the name alone is not enough:
    the build composes on a STAGING container and the promotion
    renames that container to the reel's final name - but the manifest
    was written under the staging name and nothing renamed it with
    the timeline.  So a BUILT reel's manifest is filed under a name
    that STARTS with the reel's own slug and carries a staging
    suffix, and looking it up by the reel's exact name finds nothing:
    every length-changing touch-up on a built reel then refused with
    "no recorded fusion manifest ... is on disk" and told the caller to
    rebuild the reel - the expensive path the touch-up exists to
    avoid (measured 2026-10-20, `docs/benchmarks/
    2026-10-20-live-user-visible-latency.md`, finding 3).

    So the reel's record is the newest candidate whose rows DESCRIBE
    the timeline being edited, and `check_manifest_matches` judges
    that claim on the live read: a manifest that describes a different
    cut is refused by name, not used.
    """
    import json as _json

    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_look import _slug

    scratch = str(ProjectLayout(project_folder).read_dir(Area.SCRATCH))
    directory = os.path.join(scratch, "reel_look")
    slug = _slug(timeline_name)
    exact = os.path.join(directory, f"{slug}_fusion_manifest.json")
    if os.path.isfile(exact):
        manifest = _read_manifest(exact, _json)
        if manifest is not None:
            return manifest
    candidates = []
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return None
    for name in names:
        if not name.endswith("_fusion_manifest.json"):
            continue
        if not name[: -len("_fusion_manifest.json")].startswith(slug):
            continue
        path = os.path.join(directory, name)
        try:
            candidates.append((os.path.getmtime(path), path))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    if tracks is not None:
        for _mtime, path in candidates:
            manifest = _read_manifest(path, _json)
            if manifest is None:
                continue
            if _manifest_describes(manifest, tracks):
                return manifest
    manifest = _read_manifest(candidates[0][1], _json)
    return manifest


def _read_manifest(path: str, json_module) -> dict | None:
    """One manifest file, or None when it cannot be read."""
    try:
        with open(path, encoding="utf-8") as handle:
            manifest = json_module.load(handle)
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _manifest_describes(manifest: Mapping,
                         tracks: Sequence[Mapping]) -> bool:
    """Whether the manifest's rows describe the timeline being edited.

    The comp pass maps manifest clip specs to live items by source
    path in order, so a manifest with a different source sequence on a
    comp-bearing row would write comps onto the wrong clips.  This is
    the same comparison `check_manifest_matches` makes, asked as a
    question instead of as a refusal.
    """
    wanted = _manifest_source_sequence(manifest)
    live = _live_source_sequence(tracks)
    return all(live.get(row) == sources
               for row, sources in wanted.items())


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
    rows = set()
    for track in tracks:
        if str(track.get("type", "")).lower().startswith("v"):
            rows.add(_row_of(track["type"], int(track["index"])))
    out = {}
    for track in tracks:
        row = _row_of(track["type"], int(track["index"]))
        if row not in rows:
            continue
        out[row] = [str(c.get("source_file") or "")
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


def _find_pool_item(pool: Any, wanted: str) -> Any | None:
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
            except Exception as unreadable:  # noqa: BLE001
                _logger.debug(
                    "skipping media-pool clip with unreadable file path: %s",
                    unreadable)
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
        if _is_native_still(mpi):
            # A still holds one source frame by nature; its timeline
            # duration arrives through the still-duration preference
            # route (`still_placement.place_still_exact`), never from
            # source frames - so there is no span to judge here. The
            # placed duration is judged by a re-read after placement.
            continue
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


def _pool_source_frames(pool_item: Any) -> int | None:
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
        preservation_state = None
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
            from library.tools import edit_state_preservation
            try:
                preservation_state = edit_state_preservation.capture(source)
                edit_state_preservation.preflight_source(
                    preservation_state, mpi)
                properties = dict(preservation_state["picture_properties"])
            except edit_state_preservation.StatePreservationError as exc:
                raise TouchupRefused(
                    f"edit {item.position} cannot preserve state from "
                    f"{row}[{index}]: {exc}",
                    "the replacement cannot safely carry the source item state",
                    "choose a replacement with compatible source state or "
                    "rebuild the reel") from exc
            if not properties:
                raise TouchupRefused(
                    f"edit {item.position} cannot read transform state from "
                    f"{row}[{index}]",
                    "the inserted half must inherit measurable native item state",
                    "re-read the source clip and retry")
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
            properties=properties, grade_from=grade_from,
            preservation_state=preservation_state))
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


def _pre_delete_removed(project: Any, timeline: Any,
                        removals: Sequence[dict], journal_id: str,
                        project_folder: str = "") -> dict:
    """Delete the removal targets on the STAGING copy through EditPatch.

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
    operations = []
    for entry, item in zip(removals, victims):
        try:
            unique_id = str(item.GetUniqueId() or "")
        except Exception:  # noqa: BLE001
            unique_id = ""
        if not unique_id:
            raise TouchupError(
                f"the staging item at {entry.get('row')}@"
                f"{entry.get('record_frame')} has no unique id - "
                "EditPatch cannot safely address the local delete")
        operations.append({"op": "clip.delete", "unique_id": unique_id})
    from library.tools import edit_patch
    patch_receipt = edit_patch.apply_live_patch(
        project=project, timeline=timeline, capability="reel.touchup",
        operations=operations, conflict_domains=("timeline_structure",),
        idempotency_key=f"touch:{journal_id}:local-deletes",
        project_folder=project_folder or None)
    if patch_receipt["status"] != "committed":
        raise TouchupError(
            f"the staging local-delete patch did not verify: "
            f"{patch_receipt}")
    return {"asked": len(victims),
            "seconds": round(time.time() - started, 3),
            "patch_receipt": patch_receipt}


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


def _place_still_insertions(project_folder: str, project: Any,
                            pool: Any, staged: Any, final: str,
                            still_insertions: Sequence, *,
                            resolve_app=None) -> list:
    """Place still insertions at exact durations on the staging copy.

    Each still rides the still-duration preference route
    (`still_placement.place_still_exact`) - the composition's one-call
    placement cannot carry a duration for image items - then takes its
    declared treatment through the same `treat_insertion` read-back
    the video batch uses. In record order, so two stills land
    deterministically. Anything that does not place, or does not take
    its treatment, raises `TouchupError`: the staging stands for
    diagnosis and the approved timeline was never touched.
    """
    if resolve_app is None:
        from library.tools.reel_build import _connect_resolve
        resolve_app = _connect_resolve()
    from library.tools.still_placement import place_still_exact
    try:
        fps = float(staged.GetSetting("timelineFrameRate"))
    except (TypeError, ValueError, AttributeError) as unreadable:
        raise TouchupError(
            f"the staging timeline's frame rate is unreadable "
            f"({unreadable}); the approved timeline stands") from unreadable
    placed = []
    for entry in sorted(still_insertions,
                        key=lambda item: (int(item.record_frame),
                                          int(item.track_index))):
        started = time.time()
        result = place_still_exact(
            resolve_app, project, pool, staged, entry.media_pool_item,
            int(entry.duration), int(entry.record_frame),
            int(entry.track_index), fps, timeline_name=final)
        treatment = _ce.treat_insertion(
            result["item"], entry,
            write_context={"project_folder": project_folder})
        placed.append({"row": entry.row,
                       "record_frame": int(entry.record_frame),
                       "duration": int(entry.duration),
                       "name": entry.name,
                       "seconds": round(time.time() - started, 3),
                       "treatment": treatment})
    return placed


def _assert_still_insertions(tracks: Sequence[Mapping],
                             still_insertions: Sequence) -> None:
    """Every placed still must read back at its planned span.

    Judged off a fresh track read - never off the placement's return
    value. A still that landed short (the preference route missed) or
    not at all fails the touchup by name instead of promoting a
    timeline whose logo ends mid-reel.
    """
    spans: dict = {}
    for track in tracks or ():
        try:
            row = _row_of(track["type"], int(track["index"]))
        except (KeyError, TypeError, ValueError):
            continue
        spans[row] = [(int(c["record_in"]), int(c["record_out"]))
                      for c in (track.get("clips", []) or ())]
    for entry in still_insertions:
        want = (int(entry.record_frame),
                int(entry.record_frame) + int(entry.duration))
        if want not in (spans.get(str(entry.row).upper()) or ()):
            raise TouchupError(
                f"the still on {entry.row} at {want[0]} for "
                f"{entry.duration}f did not read back at its planned "
                f"span (row now holds "
                f"{spans.get(str(entry.row).upper())}) - the staging "
                f"stands and the approved timeline was never touched.")


def _cleanup_refused_staging(project, pool, staging, restore_to, receipt,
                             project_folder, resolve_app=None) -> dict:
    """A refused or failed touch cleans up its own staging.

    The staging is a duplicate of the approved timeline, which still
    stands under its own name - so the half-finished edit is exported
    to the recovery archive the build uses (recoverable, not debris)
    and then deleted, leaving the project's timeline count as the
    touch found it.  The cursor goes back to what this touch found,
    but only where this touch's own lease is what moved it onto the
    staging; a cursor the captain moved mid-section is his, not ours
    to move.

    Export first, then delete: a staging whose `.drt` did not land is
    LEFT STANDING, with the reason on the receipt, because the archive
    is the only copy of the half-finished edit once the staging is
    gone.  Returns the cleanup report.
    """
    from library.tools.reel_build import timelines_to_replace
    from library.tools.resolve_lock import assert_current_timeline
    from library.tools.versions import store as _store

    found = timelines_to_replace(project, {staging})
    if not found:
        return {"staging": staging, "cleaned": False,
                "reason": "the staging is no longer in the project"}
    staged = found[0]
    if resolve_app is None:
        from library.tools.reel_build import _connect_resolve
        resolve_app = _connect_resolve()
    archive = _store.export_timeline_recovery_archive(
        resolve_app, staged, staging, project_folder,
        label=f"refused touch {staging}")
    if not archive["archived"]:
        return {
            "staging": staging,
            "cleaned": False,
            "staging_left_standing": staging,
            "reason": archive.get("reason", ""),
            "archive": archive,
        }
    report: dict = {
        "staging": staging,
        "cleaned": True,
        "archive": archive,
    }
    # The archive is now the ONLY copy of the half-finished edit, and
    # the staging is about to be deleted. It is HELD against the touch
    # that needs it: the cleanup must not collect it until the edit is
    # safely committed or the recovery is explicitly abandoned.
    if archive.get("path"):
        from library.tools import edit_recovery

        report["recovery_archive_hold"] = edit_recovery.hold_recovery_archive(
            project_folder, archive["path"],
            transaction_id=str(receipt.get("journal") or staging),
            project_id=str(project.GetName() or "project"),
            reason=f"recovery archive of the refused touch {staging}")
    current = project.GetCurrentTimeline()
    if (current is not None and restore_to is not None
            and current.GetName() == staging):
        assert_current_timeline(project, restore_to)
        report["cursor_restored_to"] = restore_to.GetName()
    pool.DeleteTimelines([staged])
    return report


def _values_equal(got, want) -> bool:
    """One requested value against what the timeline reads, tolerantly.

    Mirrors `edit_preparation._op_already_satisfied`: exact equality,
    with a 1e-6 tolerance for float pairs so a repr difference never
    reads as a content change.
    """
    if got == want:
        return True
    return (isinstance(got, float) and isinstance(want, float)
            and abs(got - want) <= 1e-6)


def _live_in_place_no_op(qualification: Qualification,
                         tracks: Sequence[Mapping]) -> str | None:
    """Why this touch-up needs no write, judged against the live read.

    VC-16: the no-op path as the cheapest successful edit. Only a pure
    in-place spec qualifies - `set_properties`/`set_enabled` writes with
    no delete, no place, no row, no move - and only when EVERY
    requested value already reads identical on the live timeline the
    gate just qualified. The answer is a reason string, or None.

    None means "not proven satisfied", never "satisfied": a value that
    differs falls through to the normal staged path (a write IS
    needed), and a value that cannot be judged - a missing item, an
    unreadable enabled state (`None` stays unknown, never `False`),
    an unspoken transform (`{}` is "Resolve did not say", never
    identity) - also falls through. An UNVERIFIED live state proceeds
    to staging and read-back verification; it is never reported as an
    unchanged one.
    """
    if (qualification.changes or qualification.insertions
            or qualification.removals or qualification.moves
            or qualification.new_rows):
        return None
    if not qualification.in_place:
        return None
    by_row: dict = {}
    for track in tracks or ():
        try:
            row = _row_of(track["type"], int(track["index"]))
        except (KeyError, TypeError, ValueError):
            continue
        by_row.setdefault(row, list(track.get("clips") or ()))
    matched: list[str] = []
    for entry in qualification.in_place:
        if not isinstance(entry, Mapping):
            return None
        kind = str(entry.get("kind") or "")
        row = str(entry.get("row") or "").upper()
        try:
            position = int(entry.get("item_index"))
        except (TypeError, ValueError):
            return None
        clips = by_row.get(row) or ()
        if not 0 <= position < len(clips):
            return None
        clip = clips[position]
        if not isinstance(clip, Mapping):
            return None
        if kind == "set_enabled":
            live = clip.get("enabled")
            if live is None:
                return None
            if bool(live) is not bool(entry.get("enabled")):
                return None
            matched.append(
                f"{row}[{position}] enabled already reads {bool(live)}")
        elif kind == "set_properties":
            wanted = entry.get("properties") or {}
            if not isinstance(wanted, Mapping) or not wanted:
                return None
            transform = clip.get("transform")
            scope = transform if isinstance(transform, Mapping) else {}
            for key, want in wanted.items():
                got = scope.get(key, clip.get(key))
                if got is None or not _values_equal(got, want):
                    return None
                matched.append(
                    f"{row}[{position}] {key} already reads {got!r}")
        else:
            # `entry_motion` and anything new: stage and verify.
            return None
    if not matched:
        return None
    return ("already satisfied: " + "; ".join(matched)
            + " - no Resolve write is needed")


def _audio_fades(timeline: Any) -> dict:
    """Read every audio item's native frame fades for preservation checks."""
    fades = {}
    count = int(timeline.GetTrackCount("audio") or 0)
    for track_index in range(1, count + 1):
        for item in timeline.GetItemListInTrack("audio", track_index) or ():
            pool_item = item.GetMediaPoolItem()
            if pool_item is None:
                raise TouchupError(
                    f"A{track_index} contains an audio item with no media item; "
                    "its fade state cannot be preserved")
            path = str(pool_item.GetClipProperty("File Path") or "")
            if not path:
                raise TouchupError(
                    f"A{track_index} item has no readable source path; "
                    "its fade state cannot be preserved")
            key = (track_index, path, int(item.GetSourceStartFrame()),
                   int(item.GetSourceEndFrame()), int(item.GetStart()),
                   int(item.GetEnd()))
            if key in fades:
                raise TouchupError(
                    f"A{track_index} has ambiguous duplicate audio item "
                    f"identity {key!r}; fade state cannot be preserved")
            values = item.GetFades()
            if not isinstance(values, Mapping) or not {
                    "FadeIn", "FadeOut"} <= values.keys():
                raise TouchupError(
                    f"A{track_index} returned incomplete GetFades state")
            fades[key] = {"FadeIn": int(values["FadeIn"]),
                          "FadeOut": int(values["FadeOut"])}
    return fades


def _overlay_asset_mode(source_size: tuple[int, int],
                       frame: tuple[int, int]) -> str:
    """Whether the overlay fills the delivery frame or is a tight box."""
    return "full_frame" if tuple(source_size) == tuple(frame) else "tight"


def _overlay_source_geometry(item: Any, timeline: Any, project: Any,
                             entry: Mapping, op: str):
    """The target overlay's own canvas, its native transform, the model.

    The SAME geometry for both media kinds: an item's own source canvas
    and its native transform decide where it draws, so a native PNG still
    and a rendered movie overlay move and resize through one chain. Only
    the MEASUREMENT of the canvas differs between them, and that
    measurement is `overlay_media`'s - the same instrument the
    rendered-pixel verification reads the asset with.
    """
    from library.tools import overlay_media
    from library.tools.layout_model import (
        LayoutModel,
        Transform,
        current_proxy_units,
    )

    pool_item = item.GetMediaPoolItem()
    if pool_item is None:
        raise TouchupRefused(
            f"{op} target has no media-pool item",
            "the source canvas and identity must be measured before a transform write",
            "re-read the exact overlay")
    path = str(pool_item.GetClipProperty("File Path") or "")
    if not path or os.path.realpath(path) != os.path.realpath(
            str(entry["source_file"])):
        raise TouchupRefused(
            f"{op} source identity changed on staging",
            "the candidate must still contain the exact targeted asset",
            "discard the stale candidate and re-address the overlay")
    kind, why = overlay_media.classify({
        "source_file": path,
        "media_type": str(pool_item.GetClipProperty("Type") or ""),
    })
    if kind is None:
        raise ValueError(f"unsupported media {path!r}: {why}")
    qualified = str(entry.get("media_kind") or "")
    if qualified and qualified != kind:
        raise ValueError(
            f"the staged media is a {kind} where the request qualified a "
            f"{qualified}")
    try:
        media = overlay_media.measure(kind, path, pool_item=pool_item)
    except overlay_media.OverlayMediaError as exc:
        raise ValueError(str(exc)) from exc
    frame = (int(timeline.GetSetting("timelineResolutionWidth")),
             int(timeline.GetSetting("timelineResolutionHeight")))
    stored = item.GetProperty() or {}
    if not isinstance(stored, Mapping):
        raise TypeError("native transform properties are unreadable")
    units = current_proxy_units(project)
    current = Transform.from_dict({**dict(stored),
                                   "pan_units": units,
                                   "tilt_units": units})
    if abs(current.rotation) > 1e-7 or abs(current.scaling - 1.0) > 1e-7:
        raise ValueError(
            f"rotation or non-native Scaling has no qualified {op} projection")
    model = LayoutModel(*frame, units, units)
    return media.canvas, current, model, frame, path


def _overlay_resize_projection(item: Any, timeline: Any, project: Any,
                               entry: Mapping) -> tuple[dict, dict]:
    """Lower output-pixel size to native properties and a pixel-QA record."""
    from library.tools.layout_model import (
        GeometryDescriptor,
        LayoutItem,
    )

    source_size, current, model, frame, path = (), None, None, (), ""
    try:
        source_size, current, model, frame, path = _overlay_source_geometry(
            item, timeline, project, entry, "overlay.resize")
        current_item = LayoutItem(*source_size, current)
        centre = model.local_to_canvas(
            current_item, source_size[0] * current.anchor_x,
            source_size[1] * current.anchor_y)
        width, height = int(entry["width"]), int(entry["height"])
        # Resolve holds ONE zoom for both axes: `resolve_transform` calls
        # it "Resolve's ZoomX/ZoomY, one number for both" and refuses an
        # independent ZoomX/ZoomY as not qualified.  A requested size
        # whose aspect differs from the source's therefore cannot be
        # delivered: the uniform zoom that fits the height draws a
        # DIFFERENT width, and the caller is answered with a graphic
        # they did not ask for.  Refused by name, with both aspects
        # named - the size that CAN be drawn is the size that keeps the
        # source's aspect.
        source_w, source_h = float(source_size[0]), float(source_size[1])
        if source_w > 0 and source_h > 0 and width > 0 and height > 0:
            source_ratio = source_w / source_h
            wanted_ratio = float(width) / float(height)
            if abs(wanted_ratio - source_ratio) > 1e-3 * max(
                    1.0, abs(source_ratio)):
                raise ValueError(
                    f"the requested size {width}x{height} has a different "
                    f"aspect from the source's {int(source_w)}x"
                    f"{int(source_h)}, and Resolve holds one zoom for both "
                    "axes - the overlay would be drawn at a different "
                    "width than the one requested")
        box = (centre[0] - width / 2, centre[1] - height / 2,
               centre[0] + width / 2, centre[1] + height / 2)
        if (box[0] < 0 or box[1] < 0
                or box[2] > frame[0] or box[3] > frame[1]):
            raise ValueError(
                "requested size around the current centre would clip the overlay")
        lowered = model.place_absolute_box(
            source_size, box, anchor=(current.anchor_x, current.anchor_y),
            rotation=0.0, crop=(0.0, 0.0, 0.0, 0.0),
            draw_gain=current.draw_gain)
        properties = {key: float(value) for key, value in {
            "Pan": lowered.pan, "Tilt": lowered.tilt,
            "ZoomX": lowered.zoom_x, "ZoomY": lowered.zoom_y,
            "Scaling": lowered.scaling,
        }.items()}
        descriptor = GeometryDescriptor(
            frame=frame, box=box, canvas=source_size,
            anchor=(current.anchor_x, current.anchor_y),
            source_size=source_size,
            asset_mode=_overlay_asset_mode(source_size, frame))
        record = {
            "label": f"{entry['row']}@{entry['record_frame']}",
            "overlay_path": path, "absolute_box": box,
            "geometry": descriptor.to_dict(), "canvas_wh": source_size,
            "timeline_start_frame": int(item.GetStart()),
            "duration_frames": int(item.GetDuration()),
            "source_start_frame": int(item.GetSourceStartFrame()),
            "requested_size": [width, height],
            "media_kind": entry.get("media_kind"),
        }
        return properties, record
    except TouchupRefused:
        raise
    except Exception as exc:
        raise TouchupRefused(
            f"overlay.resize geometry could not be lowered: {exc}",
            "source pixels, proxy units, transform, and timeline canvas must all be measurable",
            "correct the overlay or timeline measurements and retry") from exc


def _overlay_position_projection(item: Any, timeline: Any, project: Any,
                                 entry: Mapping) -> tuple[dict, dict]:
    """Lower an output-pixel translation to native properties and pixel-QA."""
    from library.tools.layout_model import (
        GeometryDescriptor,
        LayoutItem,
    )

    source_size, current, model, frame, path = (), None, None, (), ""
    try:
        source_size, current, model, frame, path = _overlay_source_geometry(
            item, timeline, project, entry, "overlay.position")
        current_item = LayoutItem(*source_size, current)
        centre = model.local_to_canvas(
            current_item, source_size[0] * current.anchor_x,
            source_size[1] * current.anchor_y)
        placed = model.placed_box(current_item)
        width, height = placed[2] - placed[0], placed[3] - placed[1]
        dx, dy = float(entry["dx"]), float(entry["dy"])
        box = (centre[0] + dx - width / 2, centre[1] + dy - height / 2,
               centre[0] + dx + width / 2, centre[1] + dy + height / 2)
        if (box[0] < 0 or box[1] < 0
                or box[2] > frame[0] or box[3] > frame[1]):
            raise ValueError(
                "translated box would clip outside the delivery frame")
        lowered = model.place_absolute_box(
            source_size, box, anchor=(current.anchor_x, current.anchor_y),
            rotation=0.0, crop=(0.0, 0.0, 0.0, 0.0),
            draw_gain=current.draw_gain)
        properties = {key: float(value) for key, value in {
            "Pan": lowered.pan, "Tilt": lowered.tilt,
            "ZoomX": lowered.zoom_x, "ZoomY": lowered.zoom_y,
            "Scaling": lowered.scaling,
        }.items()}
        descriptor = GeometryDescriptor(
            frame=frame, box=box, canvas=source_size,
            anchor=(current.anchor_x, current.anchor_y),
            source_size=source_size,
            asset_mode=_overlay_asset_mode(source_size, frame))
        record = {
            "label": f"{entry['row']}@{entry['record_frame']}",
            "overlay_path": path, "absolute_box": box,
            "geometry": descriptor.to_dict(), "canvas_wh": source_size,
            "timeline_start_frame": int(item.GetStart()),
            "duration_frames": int(item.GetDuration()),
            "source_start_frame": int(item.GetSourceStartFrame()),
            "requested_delta": [dx, dy],
            "media_kind": entry.get("media_kind"),
        }
        return properties, record
    except TouchupRefused:
        raise
    except Exception as exc:
        raise TouchupRefused(
            f"overlay.position geometry could not be lowered: {exc}",
            "source pixels, proxy units, transform, and timeline canvas must all be measurable",
            "correct the overlay or timeline measurements and retry") from exc


@session_scope("reel touch-up")
def apply_touchup(project_folder: str, spec: Mapping,
                  resolve_project_name: str = "",
                  allow_drops=None,
                  supersede=None,
                  connect=None,
                  rederiver_override=None,
                  accept_editor_changes=None,
                  resolve_app=None,
                  staged_only: bool = False,
                  opener=None) -> dict:
    """Apply a structured change to a built reel's existing timeline.

    Stages a DUPLICATE beside the approved reel, conforms it, routes
    the qualified plan through `composed_edit.apply_composed_edit`,
    verifies by re-reading the track, guards the replacement and
    promotes by rename.  The replaced generation is deleted once the
    undo journal holds both sides (`undo_journal`), or retired to the
    archive when the reel carries a sign-off.

    A change the router proves DIRECT - a property write on an item it
    names, nothing else - does not stage at all: it is written on the
    approved timeline through the EditPatch, read back, and recorded as
    a generation whose inverse restores the accepted generation
    (`library/tools/touchup_direct.py`).  Every other change stages,
    because the staging protects against failure modes a direct write
    does not.

    `staged_only` forces the staged route.  It can only escalate to
    staged - never widen a request to a direct write (VC-11).

    The whole touch-up runs inside ONE broker session
    (`library/tools/resolve_session.py`): a touch-up takes MANY leases,
    and between two of them another lane's exclusive lease could move
    the cursor to ITS project - so the touch-up's next call would find
    a timeline that is not its own.  The session holds the broker's
    turn across the whole operation, so no other lane is granted a turn
    in between.

    `connect` is a seam for tests: `connect(resolve_name) ->
    project`.  `rederiver_override` is the same for the comp pass.
    `opener` is the seam for the PROJECT OPEN: `opener(resolve_name)`
    is a context manager that makes that project the open one for the
    length of the touch. A caller that brings its own `connect` owns
    its own connection, and gets no open.
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
        opener = opener or _project_open
    else:
        opener = opener or _no_project_open
    return _apply_under_lease(
        project_folder, spec, final, resolve_name, declared_drops,
        declared_supersede, declared_editor_acceptance, connect,
        rederiver_override, resolve_app, started, staged_only, opener)


def _apply_under_lease(project_folder: str, spec: Mapping, final: str,
                       resolve_name: str, declared_drops,
                       declared_supersede, accept_editor_changes, connect,
                       rederiver_override, resolve_app,
                       started: float, staged_only: bool = False,
                       opener=None) -> dict:
    from library.tools.resolve_lock import under_lease
    from library.tools.resource_leases import (
        LeaseDeclaration,
        ResourceRef,
        resource_lease,
    )
    opener = opener or _project_open

    @under_lease(f"touch up {final}")
    def _guarded():
        # The resource lease declares what this touch-up touches BEFORE
        # the destructive change: the project it reads, and the timeline
        # it rewrites - WRITE + EXCLUSIVE, the spec's own pairing, so
        # no other agent may move or delete a clip in this timeline
        # mid-touch-up. The instance lease above is the coarse fence;
        # this is the fine-grained declaration.
        #
        # The touch's three phases are measured INSIDE the lease, so the
        # split is subtracted from the hold it came out of and no second
        # is counted twice (`touchup_phases`).
        with touchup_phases.phases("reel"):
            with resource_lease(
                LeaseDeclaration(
                    read=frozenset({ResourceRef("project", resolve_name)}),
                    write=frozenset({ResourceRef("timeline", final)}),
                    exclusive=frozenset({ResourceRef("timeline", final)}),
                ),
                purpose=f"touch up {final}",
            ):
                with opener(resolve_name):
                    return _apply_connected(
                        project_folder, spec, final, resolve_name,
                        declared_drops, declared_supersede,
                        accept_editor_changes,
                        connect, rederiver_override, resolve_app, started,
                        staged_only=staged_only)

    return _guarded()


def _apply_connected(project_folder: str, spec: Mapping, final: str,
                     resolve_name: str, declared_drops,
                     declared_supersede, accept_editor_changes, connect,
                     rederiver_override, resolve_app,
                     started: float, staged_only: bool = False) -> dict:
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
            _dt.UTC).isoformat(timespec="seconds"),
    }
    # VC-07: the touch-up spec as one typed edit request. Annotation
    # only - the qualified plan below still executes; a spec that does
    # not convert never fails the touch-up, it only leaves this unset.
    try:
        from library.tools import edit_request as _edit_request

        _request = _edit_request.from_touchup_spec(
            spec, project=resolve_name, timeline=final)
        from library.tools.composed_edit import operation_description
        receipt["edit_request"] = {
            "id": _request.id,
            "operations": [
                {"op": op.op, "kind": op.kind,
                 "expected_effect": op.expected_effect,
                 "description": operation_description({"op": op.op})}
                for op in _request.operations],
        }
    except Exception:  # noqa: BLE001 - annotation must not fail the edit
        receipt["edit_request"] = None
    if spec.get("transaction_operations"):
        receipt["edit_request"] = {
            "id": str(spec.get("transaction_id") or ""),
            "operations": [
                {"op": str(operation["op"]), "kind": "direct",
                 "expected_effect": str(operation.get("expected_effect")
                                         or operation["op"]),
                 "description": str(operation["op"])}
                for operation in spec["transaction_operations"]],
        }

    project = connect(resolve_name)
    pool = project.GetMediaPool()
    # What the cursor was when this touch found it, so a refused or
    # failed one can put it back: the lease below moves it onto the
    # approved timeline and then the staging, and a touch that refuses
    # must leave the project - cursor included - as it found it.
    pre_touch_current = project.GetCurrentTimeline()
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
    audio_fades = [entry for entry in qualification.in_place
                   if entry.get("kind") == "audio_fade"]
    if audio_fades and (
            len(audio_fades) != 1 or len(qualification.in_place) != 1
            or qualification.changes or qualification.insertions
            or qualification.removals or qualification.moves
            or qualification.new_rows):
        raise TouchupRefused(
            "audio.fade must be the only edit in a touch-up request",
            "the native fade writer has only been bounded for a localized "
            "in-place change; composition could otherwise replace audio "
            "items and lose their fade state",
            "submit one audio.fade operation by itself")
    transitions = [entry for entry in qualification.in_place
                   if entry.get("kind") == "native_transition"]
    if transitions and (
            len(transitions) != 1 or len(qualification.in_place) != 1
            or qualification.changes or qualification.insertions
            or qualification.removals or qualification.moves
            or qualification.new_rows):
        raise TouchupRefused(
            "transition must be the only edit in a touch-up request",
            "the native transition is qualified only on an unchanged staged cut",
            "submit one exact-cut transition by itself")
    # The rendered-pixel verification of an overlay resize or move needs a
    # live Resolve handle, and the touch-up already opens its own
    # connection to stage the reel - so the handle is connected here
    # rather than refused at the verification. Measured 2026-10-20: an
    # `overlay.resize` through `ren revise` reached the verification with
    # no handle and raised "overlay.resize requires a Resolve handle for
    # independent rendered-pixel verification; approved timeline remains
    # intact" - a refusal for a handle this file could open.
    needs_pixel_verification = any(
        str(entry.get("kind")) in {"overlay_resize", "overlay_position"}
        for entry in (qualification.in_place or ()))
    if (resolve_app is None
            and (qualification.gate_class == COMPOSED_STILL_RESIZE
                 or needs_pixel_verification)):
        from library.tools.reel_build import _connect_resolve
        resolve_app = _connect_resolve()
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
    receipt["dirty"] = (
        {"basis": "semantic_transaction",
         "operations": [str(operation["op"])
                        for operation in spec["transaction_operations"]]}
        if spec.get("transaction_operations") else
        _dirty_regions.dirty_from_spec(
            spec, tracks, first_frame=_live_prop(source, "GetStartFrame"),
            end_frame=_live_prop(source, "GetEndFrame"), fps=fps))

    # VC-16: the no-op path as the cheapest successful edit. A pure
    # in-place spec whose requested values the live timeline already
    # reads returns here with "already satisfied": no undo-journal
    # entry (the way back for a change), no timeline inventory, no
    # staging duplicate, and further down no Git branch/commit,
    # Project Model change, promotion, retirement or recovery
    # archive - there is no change to record or recover. An
    # unjudgeable spec falls through to staging and read-back
    # verification; an unverified live state is never reported as
    # an unchanged one.
    no_op_reason = _live_in_place_no_op(qualification, tracks)
    if no_op_reason is not None:
        from library.tools import execution_router as _router

        no_op_routing = _router.route_no_op(
            reason=no_op_reason, context={"reel": final})
        receipt["status"] = "already_satisfied"
        receipt["reason"] = no_op_reason
        receipt["routing"] = no_op_routing.as_dict()
        receipt["mutated"] = False
        receipt["staging_created"] = False
        receipt["receipt_path"] = ""
        receipt["seconds"] = round(time.time() - started, 3)
        print(f"touch-reel {final}: already satisfied - "
              f"no write issued", flush=True)
        print(f"  {no_op_reason}", flush=True)
        return receipt

    # THE DIRECT ROUTE, asked before the staging is built.
    #
    # A change that writes nothing but a property on an item it names
    # is a reversible mutation: the router can prove the direct path
    # for it (VC-11), and the direct path does not duplicate the reel,
    # does not rename a candidate over it, and does not run the
    # whole-reel bookkeeping a promotion runs - 53.6 s of a 58.5 s
    # touch on the Ren-owned scratch copy.
    #
    # The staging is not removed from the touch path: it protects
    # against a real failure mode (a partial write, a lost row, a
    # wrong target, an editor's hand edit the change would destroy),
    # and every one of those is still refused BY NAME here.  A request
    # the router cannot prove direct stages, exactly as before.
    if not staged_only:
        direct_proof = _prove_direct(project_folder, spec, final,
                                     resolve_name, source, project,
                                     qualification, receipt, tracks,
                                     resolve_app)
    else:
        direct_proof = None
    selection = _select_execution_route(project_folder, receipt,
                                        direct_proof, final)
    if selection.strategy == "direct_patch" and direct_proof is not None:
        direct = _apply_direct(project_folder, spec, final, resolve_name,
                               source, project, qualification, receipt,
                               started, tracks, resolve_app,
                               proof=direct_proof)
        if direct is not None:
            return direct

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
    elif qualification.gate_class == COMPOSED_STILL_RESIZE:
        rederiver = _StillResizeRederiver()
        receipt["rederiver"] = "no-comp still resize (no Fusion pass)"
    else:
        manifest = recorded_fusion_manifest(project_folder, final, tracks)
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
    from library.tools.composed_edit import prepare_candidate
    # The staging copy is the touch's FIRST phase: the duplicate of the
    # approved timeline that every later phase edits.  Measured, never
    # assumed (`touchup_phases`).
    touchup_phases.open_phase("duplicate")
    try:
        staged = prepare_candidate(source, staging)
    except Exception as candidate_error:
        _journal.fail_entry(project_folder, journal,
                            "the staging copy did not land")
        _provenance.finish_timeline_inventory(
            review_dir, inventory_operation, _guard.timeline_inventory(project))
        raise TouchupError(
            f"the staging copy did not land as {staging!r} "
            f"({candidate_error}) - nothing was edited and the approved "
            f"timeline stands.") from candidate_error
    finally:
        touchup_phases.close_phase("duplicate")
    from library.tools.composed_edit import CandidateLifecycle
    candidate_lifecycle = CandidateLifecycle.create(source, staged, final)
    candidate_lifecycle.operations = list(
        (receipt.get("edit_request") or {}).get("operations") or ())
    # The staging double is a BRANCH off the final timeline's latest
    # commit. Best-effort, like the commit - a branch that cannot be
    # recorded never fails the touch (the journal is the way back), but
    # it is VISIBLE on the receipt, never swallowed in silence. The
    # branch's base is captured for the promotion's merge: a merge is
    # recorded only when the timeline has moved on (the base is no
    # longer the head), so a linear touch is just its commit.
    try:
        base = _journal.latest_commit_for_timeline(project_folder, final)
        receipt["branch_base"] = base.get("id") if base else None
        receipt["branch"] = _journal.record_branch(
            project_folder, name=staging,
            base=receipt["branch_base"],
            timeline=final, note=f"touch {journal['id']}")
    except _journal.BranchRefused as unbranched:
        print(f"creative branch not recorded: {unbranched}",
              file=sys.stderr)
        receipt["branch"] = None
        receipt["branch_error"] = str(unbranched)
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
                         accept_editor_changes, final, stage_started, journal,
                         spec, candidate_lifecycle=candidate_lifecycle,
                         resolve_app=resolve_app)
    except Exception as failed:
        # A refused or failed touch cleans up after itself: its staging
        # is a duplicate of the approved timeline, which still stands
        # under its own name, so the half-finished edit is exported to
        # the recovery archive and the staging deleted - the project
        # left as the touch found it, cursor included.  The inventory
        # below then reads a net-zero timeline count, which is the
        # truth: this touch created and destroyed its own staging.
        # The cleanup is best effort: a failure inside it is recorded
        # on the receipt and the original refusal still propagates, with
        # the journal and inventory steps below still run.
        try:
            receipt["staging_cleanup"] = _cleanup_refused_staging(
                project, pool, staging,
                pre_touch_current if pre_touch_current is not None else source,
                receipt, project_folder, resolve_app=resolve_app)
        except Exception as cleanup_failed:  # noqa: BLE001 - never mask
            receipt["staging_cleanup_error"] = repr(cleanup_failed)
        if journal.get("status") == _journal.STATUS_OPEN:
            _journal.fail_entry(project_folder, journal, repr(failed))
        _provenance.finish_timeline_inventory(
            review_dir, inventory_operation, _guard.timeline_inventory(project))
        # AXI-016: a request that did not land is a MEASURED request
        # too - the strategy it ran and the outcome it reached are what
        # the cost comparison is made of.
        _record_execution_request(
            receipt, "staged_incremental", final,
            verification=("refused" if isinstance(failed, TouchupRefused)
                          else "failed"))
        raise
    receipt["seconds"] = round(time.time() - started, 3)
    # A touch and a planned incremental build expose the same transaction
    # vocabulary. The journal remains the authoritative recovery record;
    # this additive view links its verified candidate to the revision it
    # produced without claiming success before promotion returns.
    receipt["transaction"] = {
        "transaction_id": candidate_lifecycle.transaction_id,
        "journal_id": journal["id"],
        "backend": "reel_touchup",
        "status": (receipt.get("candidate_lifecycle") or {}).get(
            "state", "promotion_failed"),
        "revision_recorded": bool(receipt.get("version")
                                   and receipt.get("commit")),
        "operations": (receipt.get("edit_request") or {}).get(
            "operations", []),
        "base_revision": receipt.get("branch_base"),
        "revision": receipt.get("commit", {}).get("id")
            if isinstance(receipt.get("commit"), Mapping) else None,
        "verification": {"status": "verified"
                         if receipt.get("promoted") else "not_verified"},
        "promotion": receipt.get("promoted"),
    }
    receipt["receipt_path"] = _write_receipt(
        project_folder, final, receipt, journal["id"])
    # AXI-016: the request's own row - the strategy that ran, the wall
    # clock it took and the verification it produced - so the three
    # strategies are measured in the same unit.
    _record_execution_request(
        receipt, "staged_incremental", final,
        verification=("verified" if receipt.get("promoted") else "failed"))
    return receipt


def _prove_direct(project_folder: str, spec: Mapping, final: str,
                  resolve_name: str, source: Any, project: Any,
                  qualification: Qualification, receipt: dict,
                  tracks: Sequence[Mapping],
                  resolve_app: Any = None) -> dict | None:
    """The direct route's PROOF, or None when this is not a direct request.

    AXI-016: the proof is what makes the direct write SAFE, and safety
    is a precondition of the cost-aware selection - not a preference.
    Nothing is written here.  The proof is the plan (a property write on
    an item the request names), the router's decision on it against the
    MEASURED runtime, and the editor-change guard (the approved
    timeline carries no unattributed hand edit, so a direct write
    destroys nobody's accepted edit).

    None means the direct route is not available for this request, and
    the reason is on the receipt.
    """
    from library.tools import touchup_direct as _direct

    touchup_phases.open_phase("direct")
    try:
        touchup_phases.mark("plan")
        try:
            plan = _direct.plan(qualification, source)
        except _direct.DirectRouteUnavailable as unavailable:
            receipt["direct_route"] = f"staged: {unavailable}"
            touchup_phases.note("direct", f"declined: {unavailable}")
            return None
        touchup_phases.mark("route")
        # The Resolve app handle, for the one measurement the
        # qualification needs: the runtime state the evidence was
        # earned under.  It is opened only now - after the plan has
        # said the request is a property write - so a touch that will
        # stage anyway pays nothing for it.
        if resolve_app is None:
            try:
                from library.tools.reel_build import _connect_resolve
                resolve_app = _connect_resolve()
            except Exception:  # noqa: BLE001 - no app, no direct route
                resolve_app = None
        try:
            decision = _direct.route(plan["operations"], resolve=resolve_app,
                                     project=project, timeline=source)
        except _direct.DirectRouteUnavailable as unavailable:
            receipt["direct_route"] = f"staged: {unavailable}"
            touchup_phases.note("direct", f"declined: {unavailable}")
            return None
        touchup_phases.mark("editor_guard")
        guard = _direct.editor_changes_clear(project_folder, final,
                                              project, source)
        if not guard["clear"]:
            receipt["direct_route"] = (
                f"staged: the approved timeline carries "
                f"{guard['pending']} unattributed editor change(s), and a "
                f"direct write has no candidate to carry them on")
            touchup_phases.note("direct", receipt["direct_route"])
            return None
    finally:
        touchup_phases.close_phase("direct")
    return {"plan": plan, "decision": decision, "guard": guard}


def _select_execution_route(project_folder: str, receipt: dict,
                            direct_proof: dict | None,
                            final: str):
    """The route this touch runs, chosen from the SAFE ones by cost.

    AXI-016: the direct route and the staged route are both offered
    when the direct route is PROVEN, and the selection takes the
    cheapest one this project has MEASURED.  Where the cost has not
    been measured here, the route that disturbs the least is taken and
    the record says the cost was unmeasured - an unmeasured strategy is
    never reported as a cheap one.

    The comparison rides the receipt, so a reader can see what each
    route costs HERE and why this one was taken.
    """
    from library.tools import execution_cost as _cost
    from library.tools import execution_router as _router

    candidates = []
    if direct_proof is not None:
        candidates.append(_cost.Candidate(
            "direct_patch", True,
            "the router proved the direct mode against the measured "
            "runtime, and the approved timeline carries no unattributed "
            "editor change",
            _cost.profile("direct_patch").verification))
    else:
        # The direct route was asked and DECLINED.  The comparison
        # carries the decline and its reason, so a reader can see that
        # the smaller mutation was not taken BECAUSE it was not
        # verified here - not because nobody asked.
        declined = str(receipt.get("direct_route") or "")
        if declined.startswith("staged: "):
            candidates.append(_cost.Candidate(
                "direct_patch", False, declined[len("staged: "):],
                _cost.profile("direct_patch").verification))
    candidates.append(_cost.Candidate(
        "staged_incremental", True,
        "the staging protects against a partial write, a lost row, a "
        "wrong target and an editor's hand edit",
        _cost.profile("staged_incremental").verification))
    # The ROUTER chooses: this path asks, it does not decide, so the
    # engine keeps ONE place where a route is chosen.
    selection = _router.select_route(
        candidates, costs=_measured_execution_costs(project_folder))
    receipt["execution_cost"] = selection.as_dict()
    if direct_proof is not None and selection.strategy != "direct_patch":
        # The direct route was PROVEN and was not taken: the record says
        # so, because a reader who finds the proof on the receipt and
        # no write must be able to say why.
        receipt["direct_route"] = (
            "direct: proven and not taken - " + selection.reason)
    return selection


def _measured_execution_costs(project_folder: str) -> dict:
    """What THIS project has measured for each execution strategy.

    Read from the project's own performance ledger, never from a table
    of another project's numbers.  A project with no ledger has measured
    nothing, and the answer says so rather than reporting a zero.
    """
    from library.tools import execution_cost as _cost
    from library.tools import perf_ledger as _perf

    if not project_folder:
        return {}
    try:
        rows = _perf.read_rows(project_folder)
    except OSError:
        return {}
    return _cost.measured_costs(rows)


def _record_execution_request(receipt: dict, strategy: str, final: str,
                              *, verification: str = "verified") -> None:
    """Record this request's strategy, wall clock and verification.

    AXI-016: the three strategies are only comparable if they are
    measured the same way, so every touch writes the same row: the
    strategy that ran, the seconds it took and the outcome it reached.
    A touch with no ledger attached records nothing, exactly as every
    other measurement in the engine does.
    """
    from library.tools import execution_cost as _cost

    wall = receipt.get("seconds")
    if not isinstance(wall, (int, float)):
        return
    try:
        _cost.record_request(
            strategy=strategy, wall_s=float(wall),
            verification=str(verification),
            request_id=str(receipt.get("journal") or ""),
            target=str(final or ""))
    except ValueError:
        # A strategy nobody declared is a defect in the CALLER, not a
        # reason to lose the touch's own record.
        return


def _apply_direct(project_folder: str, spec: Mapping, final: str,
                  resolve_name: str, source: Any, project: Any,
                  qualification: Qualification, receipt: dict,
                  started: float, tracks: Sequence[Mapping],
                  resolve_app: Any = None,
                  proof: Mapping | None = None) -> dict | None:
    """The direct route for a qualified small touch-up, or None to stage.

    None means "this request is not a direct request": the caller
    stages it, exactly as it always did.  A dict is a finished touch:
    the write landed, it was read back, the journal holds the state
    before and after it, and the version it produced is undoable.

    The four requirements are the module's (`touchup_direct`): the plan
    is nothing but a property write, the router proves the direct mode
    against the measured runtime, the approved timeline carries no
    unattributed editor change, and the write is an EditPatch.  The
    first three are the PROOF (`_prove_direct`), which the cost-aware
    selection asked for before it chose this route.
    """
    from library.tools import touchup_direct as _direct

    if proof is None:
        proof = _prove_direct(project_folder, spec, final, resolve_name,
                               source, project, qualification, receipt,
                               tracks, resolve_app)
    if proof is None:
        return None
    plan = proof["plan"]
    decision = proof["decision"]
    guard = proof["guard"]

    touchup_phases.open_phase("direct")
    try:
        # THE UNDO JOURNAL, before anything changes: the same record the
        # staged route files, with the reads this route already made
        # handed over instead of paid for twice.
        touchup_phases.mark("journal")
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
                    batch=str(spec.get("batch") or ""),
                    project=project,
                    before_tracks=tracks,
                    preservation_before=guard["live"])
        except _journal.UndoRefused as unrecordable:
            raise TouchupRefused(
                unrecordable.what, unrecordable.why,
                unrecordable.fix) from unrecordable
        receipt["journal"] = journal["id"]

        touchup_phases.mark("write")
        key = _direct.idempotency_key(final)
        try:
            patch = _direct.apply(
                project=project, timeline=source,
                project_folder=project_folder,
                capability="reel.touchup",
                operations=plan["operations"], idempotency_key=key)
        except _direct.DirectRouteUnavailable as failed:
            _journal.fail_entry(project_folder, journal, str(failed))
            raise TouchupError(
                f"the direct write did not verify: {failed} - the "
                f"approved timeline is unchanged.") from failed

        # The journal closes on the state this write produced, and the
        # baseline learns it, so the next touch reads the change as
        # Ren's own write and not as an editor's hand edit.
        touchup_phases.mark("close")
        from library.tools import reel_replace_guard as _guard
        rows = _guard.snapshot_timeline(source, final, side="retiring")
        _journal.close_entry(project_folder, journal,
                             after_timeline=source, rows=rows,
                             project=project)
        receipt["version"] = journal["version"]
        touchup_phases.note(
            "direct",
            f"applied: {decision['backend']} wrote "
            f"{len(plan['operations'])} operation(s) in place")
    finally:
        touchup_phases.close_phase("direct")

    receipt["gate"] = dict(receipt.get("gate") or {})
    receipt["gate"]["route"] = "direct"
    receipt["direct"] = {
        "backend": decision["backend"],
        "mode": decision["mode"],
        "operations": plan["operations"],
        "targets": plan["targets"],
        "patch_id": str(patch.get("patch_id") or ""),
        "generation": patch.get("generation"),
        "reasons": decision["reasons"],
        "editor_changes": {"pending": guard["pending"]},
    }
    receipt["direct_route"] = (
        f"direct: {decision['backend']} in {decision['mode']} mode - "
        f"{'; '.join(decision['reasons'])}")
    receipt["status"] = "applied_direct"
    receipt["mutated"] = True
    receipt["staging_created"] = False
    receipt["promoted"] = None
    receipt["recovery"] = {
        "act": f"patch:{patch.get('patch_id') or ''}",
        "how": "the touch's own journal restores the accepted generation "
               "(`ren undo`); the EditPatch's recorded inverse is the "
               "second way back",
    }
    receipt["seconds"] = round(time.time() - started, 3)
    receipt["receipt_path"] = _write_receipt(
        project_folder, final, receipt, journal["id"])
    # AXI-016: the same row the staged route writes, so the two are
    # measured in the same unit on the same ledger.
    _record_execution_request(receipt, "direct_patch", final,
                               verification="verified")
    print(f"touch-reel {final}: direct ({decision['backend']}) - "
          f"written in place, no staging, no promotion", flush=True)
    print(f"  {receipt['direct_route']}", flush=True)
    print(f"  recovery: journal {journal['id']} / "
          f"patch {patch.get('patch_id')}", flush=True)
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


def _apply_in_place(project: Any, staged: Any,
                    qualification: Qualification, comp_dir: str,
                    journal_id: str,
                    project_folder: str | None = None,
                    source: Any = None) -> dict:
    """Write in-place edits onto staging; EditPatch owns timeline writes.

    Runs BEFORE the composition, addressed by pre-edit record frame
    (nothing has moved yet): a composition capture taken after this
    reads the written state, so a re-place further down the path
    carries the in-place write rather than losing it.  A write that
    does not read back raises `TouchupError` - the staging stands for
    diagnosis and the approved timeline was never touched.
    """

    applied: dict = {"properties": [], "enabled": [], "audio_fades": [],
                     "transitions": [], "overlay_resizes": [],
                     "overlay_positions": [],
                     "entry_motion": [], "hold_windows": []}
    if not qualification.in_place:
        return applied
    rows = _live_rows(staged)
    patch_operations = []
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
        if entry.get("kind") == "native_transition":
            if source is None:
                raise TouchupRefused(
                    "transition has no approved source timeline handle",
                    "the exact staged cut cannot be compared to its source",
                    "execute the transition through staged reel touch-up")
            track_index = int(row[1:])

            def track_state(timeline, _track_index=track_index):
                live = timeline.GetItemListInTrack(
                    "video", _track_index) or []
                state = []
                for clip in live:
                    identity = str(clip.GetUniqueId() or "")
                    values = (clip.GetStart(), clip.GetEnd())
                    if not identity or any(value is None for value in values):
                        raise ValueError(
                            "transition carrier item identity or span is unreadable")
                    state.append((identity, int(values[0]), int(values[1])))
                return live, state

            try:
                _source_items, source_state = track_state(source)
                _staged_items, staged_state = track_state(staged)
            except Exception as failed:
                raise TouchupRefused(
                    f"transition carrier read-back failed ({failed})",
                    "staging must prove the source track and exact cut before writing",
                    "re-read the outgoing and incoming items") from failed
            if source_state != staged_state:
                raise TouchupRefused(
                    "transition staging changed video track identity or spans",
                    "the candidate must preserve the complete addressed track",
                    "discard the candidate and retry from an unchanged timeline")
            outgoing_uid = str(entry["outgoing_unique_id"])
            incoming_uid = str(entry["incoming_unique_id"])
            outgoing = [index for index, row_state in enumerate(staged_state)
                        if row_state[0] == outgoing_uid]
            incoming = [index for index, row_state in enumerate(staged_state)
                        if row_state[0] == incoming_uid]
            if (len(outgoing) != 1 or len(incoming) != 1
                    or incoming[0] != outgoing[0] + 1
                    or staged_state[outgoing[0]][2] != int(entry["cut_frame"])
                    or staged_state[incoming[0]][1] != int(entry["cut_frame"]) + 1):
                raise TouchupRefused(
                    "staged transition carrier no longer forms the named exact cut",
                    "outgoing and incoming identities must remain adjacent at promotion",
                    "re-read the timeline and plan the exact cut again")
            from library.tools.resolve_api_capabilities import callable_method

            add_transition = callable_method(item, "AddTransition")
            if add_transition is None:
                raise TouchupRefused(
                    "Resolve does not expose TimelineItem.AddTransition on staging",
                    "the required native transition API is unavailable",
                    "use a Resolve version with the documented transition API")
            duration = int(entry["duration_frames"])
            payload = {
                "type": str(entry["transition_type"]),
                "category": str(entry["category"]),
                "position": "start", "alignment": "center",
                "duration": duration,
            }
            try:
                placed = add_transition(payload)
                if placed is None or placed is False:
                    raise ValueError("AddTransition returned no transition item")
                name = str(placed.GetName())
                start = int(placed.GetStart())
                end = int(placed.GetEnd())
                actual_duration = int(placed.GetDuration())
            except Exception as failed:
                raise TouchupError(
                    f"AddTransition failed on staging ({failed}); approved "
                    "timeline remains untouched") from failed
            expected_start = int(entry["cut_frame"]) - duration // 2
            expected_end = expected_start + duration - 1
            if (name.casefold() != payload["type"].casefold()
                    or actual_duration != duration
                    or start != expected_start or end != expected_end):
                raise TouchupError(
                    f"staged transition read back {name!r} at [{start}, {end}] "
                    f"({actual_duration} frames), expected {payload['type']!r} "
                    f"at [{expected_start}, {expected_end}] ({duration} frames); "
                    "approved timeline remains untouched")
            after_state = track_state(staged)[1]
            if after_state != source_state:
                raise TouchupError(
                    "staged native transition changed video item identities or "
                    "spans outside the named effect")
            applied["transitions"].append({
                "row": row, "cut_frame": int(entry["cut_frame"]),
                "outgoing_unique_id": outgoing_uid,
                "incoming_unique_id": incoming_uid,
                "type": name, "category": payload["category"],
                "record_in": start, "record_out": end,
                "duration_frames": actual_duration, "verified": True,
            })
            continue
        if entry.get("kind") == "audio_fade":
            if source is None:
                raise TouchupRefused(
                    "audio.fade has no approved source timeline handle",
                    "existing native audio fade state cannot be compared "
                    "with staging",
                    "execute the fade through the staged reel touch-up path")
            before_source = _audio_fades(source)
            before_staged = _audio_fades(staged)
            if before_source != before_staged:
                raise TouchupRefused(
                    "the staging copy changed existing audio fade state",
                    "all audio item fades must survive timeline duplication "
                    "before the requested fade is applied",
                    "restore the audio fades and retry on a clean staging copy")
            key = (int(row[1:]), str(entry["source_file"]),
                   int(entry["source_in"]), int(entry["source_out"]),
                   int(entry["record_frame"]), int(entry["record_out"]))
            if key not in before_staged:
                raise TouchupError(
                    f"staged audio item {row}@{entry['record_frame']} has no "
                    "matching native fade-state identity")
            previous = dict(before_staged[key])
            wanted = {"FadeIn": int(entry["fade_in_frames"]),
                      "FadeOut": int(entry["fade_out_frames"])}
            setter = getattr(item, "SetFades", None)
            getter = getattr(item, "GetFades", None)
            if not callable(setter) or not callable(getter):
                raise TouchupRefused(
                    "Resolve does not expose TimelineItem.SetFades/GetFades "
                    "on the staged audio clip",
                    "the documented method is not callable on this item",
                    "use a Resolve version with the documented audio fade API")
            try:
                result = setter(dict(wanted))
                readback = getter()
                got = {"FadeIn": int(readback["FadeIn"]),
                       "FadeOut": int(readback["FadeOut"])}
            except Exception as failed:  # noqa: BLE001 - API failures need compensation.
                result, got, failure = False, None, failed
            else:
                failure = None
            if result is not True or got != wanted:
                try:
                    restored = setter(dict(previous))
                    restored_values = getter()
                    restore_ok = (
                        restored is True
                        and int(restored_values["FadeIn"]) == previous["FadeIn"]
                        and int(restored_values["FadeOut"]) == previous["FadeOut"])
                except Exception:  # noqa: BLE001 - report failed compensation.
                    restore_ok = False
                raise TouchupError(
                    f"SetFades read back {got!r}, expected {wanted!r}; "
                    f"compensation {'verified' if restore_ok else 'FAILED'}"
                    + (f" ({failure})" if failure else ""))
            expected_all = dict(before_source)
            expected_all[key] = wanted
            after_staged = _audio_fades(staged)
            if after_staged != expected_all:
                raise TouchupError(
                    "audio fade read-back changed unrelated audio fade state "
                    "or lost an item during the staged write")
            applied["audio_fades"].append({
                "row": row, "record_frame": entry["record_frame"],
                "record_out": entry["record_out"],
                "source_file": entry["source_file"],
                "source_in": entry["source_in"],
                "source_out": entry["source_out"],
                "before": previous, "after": wanted, "verified": True,
            })
            continue
        if entry.get("kind") == "set_enabled":
            want = bool(entry["enabled"])
            try:
                unique_id = str(item.GetUniqueId() or "")
            except Exception:  # noqa: BLE001
                unique_id = ""
            if not unique_id:
                raise TouchupError(
                    f"the staged {row}@{entry.get('record_frame')} has "
                    "no unique id for its EditPatch")
            patch_operations.append({"op": "clip.set_enabled",
                                     "unique_id": unique_id,
                                     "enabled": want})
            applied["enabled"].append({
                "row": row, "record_frame": entry["record_frame"],
                "enabled": want})
            continue
        if entry.get("kind") == "set_properties":
            try:
                unique_id = str(item.GetUniqueId() or "")
            except Exception:  # noqa: BLE001
                unique_id = ""
            if not unique_id:
                raise TouchupError(
                    f"the staged {row}@{entry.get('record_frame')} has "
                    "no unique id for its EditPatch")
            for key, value in sorted(
                    (entry.get("properties") or {}).items()):
                patch_operations.append({
                    "op": "clip.set_property", "unique_id": unique_id,
                    "key": key, "value": value})
            applied["properties"].append({
                "row": row, "record_frame": entry["record_frame"],
                "properties": dict(entry.get("properties") or {})})
        if entry.get("kind") == "overlay_resize":
            try:
                unique_id = str(item.GetUniqueId() or "")
            except Exception:  # noqa: BLE001 - a staged target must be addressable
                unique_id = ""
            if not unique_id:
                raise TouchupError(
                    f"the staged {row}@{entry.get('record_frame')} has no "
                    "unique id for overlay.resize")
            properties, pixel_record = _overlay_resize_projection(
                item, staged, project, entry)
            for key, value in sorted(properties.items()):
                patch_operations.append({
                    "op": "clip.set_property", "unique_id": unique_id,
                    "key": key, "value": value})
            applied["overlay_resizes"].append({
                "row": row, "record_frame": entry["record_frame"],
                "properties": properties, "pixel_record": pixel_record,
                "verified": False})
            continue
        if entry.get("kind") == "overlay_position":
            try:
                unique_id = str(item.GetUniqueId() or "")
            except Exception:  # noqa: BLE001 - a staged target must be addressable
                unique_id = ""
            if not unique_id:
                raise TouchupError(
                    f"the staged {row}@{entry.get('record_frame')} has no "
                    "unique id for overlay.position")
            properties, pixel_record = _overlay_position_projection(
                item, staged, project, entry)
            for key, value in sorted(properties.items()):
                patch_operations.append({
                    "op": "clip.set_property", "unique_id": unique_id,
                    "key": key, "value": value})
            applied["overlay_positions"].append({
                "row": row, "record_frame": entry["record_frame"],
                "properties": properties, "pixel_record": pixel_record,
                "verified": False})
            continue
    if patch_operations:
        from library.tools import edit_patch
        from library.tools import transaction_orchestration as _orch
        from library.tools.execution_router import route_compiled_edit
        # VC-12: the routing decision is consumed from the router and
        # attached through the shared orchestration (same dict, same key).
        applied.update(_orch.attach_routing(
            {}, route_compiled_edit(
                {"operations": patch_operations},
                # This writer only targets the disposable staging copy.
                # Promotion remains downstream of whole-plan verification;
                # the original approved timeline is left intact on refusal.
                context={
                    "staged": True,
                    "qualification_policy": "protected_experimental",
                    "protected_staging": True,
                    "preserve_approved_state": True,
                    "verify_after_write": True,
                }), key="routing"))
        applied["patch_receipt"] = edit_patch.apply_live_patch(
            project=project, timeline=staged, capability="reel.touchup",
            operations=patch_operations,
            conflict_domains=("picture_transform", "timeline_structure"),
            idempotency_key=f"touch:{journal_id}:in-place",
            project_folder=project_folder)
        if applied["patch_receipt"]["status"] != "committed":
            raise TouchupError(
                f"the staging in-place patch did not verify: "
                f"{applied['patch_receipt']}")
    for entry in qualification.in_place:
        if entry.get("kind") != "entry_motion":
            continue
        row = str(entry.get("row")).upper()
        items = rows.get(row) or []
        hits = [item for item in items
                if _live_prop(item, "GetStart", None)
                == int(entry["record_frame"])]
        if len(hits) != 1:
            raise TouchupError(
                f"the staging copy holds {len(hits)} items at "
                f"{row}@{entry.get('record_frame')} for entry_motion - "
                "the plan and timeline disagree")
        applied["entry_motion"].append(_import_entry_comp(
            hits[0], entry, comp_dir))
    for entry in qualification.in_place:
        if entry.get("kind") != "repair_hold_window":
            continue
        row = str(entry.get("row")).upper()
        items = rows.get(row) or []
        hits = [item for item in items
                if _live_prop(item, "GetStart", None)
                == int(entry["record_frame"])]
        if len(hits) != 1:
            raise TouchupError(
                f"the staging copy holds {len(hits)} items at "
                f"{row}@{entry.get('record_frame')} for the hold "
                f"window repair - the plan and timeline disagree")
        applied["hold_windows"].append(_repair_hold_window(
            staged, entry, comp_dir))
    return applied


def _axi_track(row: str) -> str:
    """The AXI's spelling of a row: `video1`, `audio2`.

    The touch-up addresses rows the way the SOP names them (`V1`, `A1`)
    and the AXI reads `video1`/`audio2` - and it refuses a track it does
    not recognise ("bad track 'V1' - tracks read video1, audio2, ..."),
    so the compound's trim never reached the clip it named.
    """
    text = str(row).upper()
    kind = "video" if text.startswith("V") else "audio"
    return f"{kind}{text[1:]}"


def _apply_semantic_transaction_staged(
        project_folder: str, project: Any, source: Any, staged: Any,
        staging: str, final: str, qualification: Qualification,
        receipt: dict, declared_drops, declared_supersede,
        accept_editor_changes, journal: Mapping, spec: Mapping,
        candidate_lifecycle=None) -> None:
    """Apply one supported semantic compound to staging and verify it whole."""
    import json as _json
    import os as _os

    from library.tools import edit_algebra, edit_patch, operation_acceptance
    from library.tools import reel_read as _read
    from library.tools import transaction_orchestration as _orchestration

    operations = [dict(row) for row in spec["transaction_operations"]]
    original_target_ids = [str(operation["unique_id"])
                           for operation in operations]
    source_tracks = _read.read_tracks(source)
    before_tracks = _read.read_tracks(staged)
    before = {"tracks": before_tracks}
    source_by_id = {
        str(clip["unique_id"]): (track, clip)
        for track in source_tracks for clip in (track.get("clips") or ())
        if clip.get("unique_id")
    }
    staged_by_row = {
        _ce.row_label(track["type"], int(track["index"])): track
        for track in before_tracks
    }
    target_ids = {}
    for operation in operations:
        source_id = str(operation["unique_id"])
        if operation["op"] == "clip.insert":
            target_ids[source_id] = source_id
            continue
        source_hit = source_by_id.get(source_id)
        if source_hit is None:
            raise TouchupError(
                f"semantic transaction target {source_id!r} is absent "
                "from the approved source snapshot")
        source_track, source_clip = source_hit
        row = _ce.row_label(source_track["type"], int(source_track["index"]))
        staged_track = staged_by_row.get(row)
        candidates = [clip for clip in (staged_track or {}).get("clips", ())
                      if str(clip.get("source_file") or "")
                      == str(source_clip.get("source_file") or "")
                      and int(clip["record_in"]) == int(source_clip["record_in"])
                      and int(clip["record_out"]) == int(source_clip["record_out"])
                      and int(clip["source_in_frame"])
                      == int(source_clip["source_in_frame"])
                      and int(clip["source_out_frame"])
                      == int(source_clip["source_out_frame"])]
        if len(candidates) != 1:
            raise TouchupError(
                f"staging target {source_id!r} matched {len(candidates)} "
                "items by source and span; no positional guess is allowed")
        target_ids[source_id] = str(candidates[0]["unique_id"])
    rendered_files = {
        target_ids[source_id]: path
        for source_id, path in (spec.get("rendered_caption_files") or {}).items()
        if source_id in target_ids
    }
    rendered_styles = {
        target_ids[source_id]: style
        for source_id, style in (spec.get("rendered_caption_styles") or {}).items()
        if source_id in target_ids
    }

    mapped_operations = [
        {**operation, "unique_id": target_ids[str(operation["unique_id"])]}
        for operation in operations
    ]
    patch_operations = []
    style_expectations = {}
    for operation in mapped_operations:
        if operation["op"] == "clip.trim":
            continue
        if operation["op"] == "caption.style":
            rendered = rendered_files.get(str(operation["unique_id"]))
            if not rendered or not _os.path.isfile(str(rendered)):
                raise TouchupError(
                    "caption.style has no rendered asset for the staged swap")
            old = next(clip for track in before_tracks
                       for clip in track.get("clips", ())
                       if str(clip["unique_id"])
                       == str(operation["unique_id"]))
            old_props_path = str(old["source_file"]).removesuffix(".mov") + "_props.json"
            new_props_path = str(rendered).removesuffix(".mov") + "_props.json"
            try:
                with open(old_props_path, encoding="utf-8") as handle:
                    old_props = _json.load(handle)
                with open(new_props_path, encoding="utf-8") as handle:
                    new_props = _json.load(handle)
            except (OSError, ValueError) as exc:
                raise TouchupError(
                    f"caption.style render props are unreadable: {exc}") from exc
            if ({key: value for key, value in old_props.items()
                 if key != "style"}
                    != {key: value for key, value in new_props.items()
                        if key != "style"}):
                raise TouchupError(
                    "caption.style render changed text, bounds or timing")
            style_expectations[str(operation["unique_id"])] = {
                "source_file": str(rendered),
                "text_preserved": True,
                "style_applied": (new_props.get("style")
                                  == rendered_styles.get(
                                      str(operation["unique_id"]))),
            }
            patch_operations.append({
                "op": "clip.replace", "unique_id": operation["unique_id"],
                "source_file": str(rendered),
            })
        else:
            patch_operations.append(dict(operation))

    writes = []
    for index, operation in enumerate(mapped_operations):
        if operation["op"] != "clip.trim":
            continue
        source_clip = next(clip for track in before_tracks
                           for clip in track.get("clips", ())
                           if str(clip["unique_id"])
                           == str(operation["unique_id"]))
        source_in = (int(source_clip["source_in_frame"])
                     + int(operation["in_frame"])
                     - int(source_clip["record_in"]))
        source_out = (int(source_clip["source_out_frame"])
                      - int(source_clip["record_out"])
                      + int(operation["out_frame"]))
        track = next(track for track in before_tracks
                     if any(str(clip["unique_id"])
                            == str(operation["unique_id"])
                            for clip in track.get("clips", ())))
        row = _ce.row_label(track["type"], int(track["index"]))
        item_index = next(i for i, clip in enumerate(track["clips"])
                          if str(clip["unique_id"])
                          == str(operation["unique_id"]))
        from library.tools import resolve_axi_core

        result = resolve_axi_core.execute([
            "edit", "trim", "--track", _axi_track(row),
            "--index", str(item_index),
            "--source-in", str(source_in), "--source-out", str(source_out),
            "--target-unique-id", str(operation["unique_id"]),
            "--expected-source-file", str(source_clip["source_file"]),
            "--expected-source-in-frame",
            str(source_clip["source_in_frame"]),
            "--expected-source-out-frame",
            str(source_clip["source_out_frame"]),
            "--expected-record-in-frame", str(source_clip["record_in"]),
            "--expected-record-out-frame", str(source_clip["record_out"]),
            "--project-folder", project_folder, "--require-recovery",
            "--apply", "--project", str(project.GetName()),
            "--timeline", staging,
        ])
        if not result.ok:
            raise TouchupError(
                result.stderr.strip() or result.stdout.strip()
                or "staged clip.trim command failed")
        after_trim = _read.read_tracks(staged)
        row_hits = [clip for candidate_track in after_trim
                    if _ce.row_label(candidate_track["type"],
                                     int(candidate_track["index"])) == row
                    for clip in candidate_track.get("clips", ())
                    if str(clip.get("source_file") or "")
                    == str(source_clip.get("source_file") or "")
                    and int(clip["record_in"]) == int(operation["in_frame"])
                    and int(clip["record_out"]) == int(operation["out_frame"])]
        if len(row_hits) != 1:
            raise TouchupError(
                f"staged clip.trim read back {len(row_hits)} matching items")
        operation["unique_id"] = str(row_hits[0]["unique_id"])
        writes.append({"operation_index": index, "command": "edit.trim",
                       "verified": True, "receipt": result.stdout})
        for patch_operation in patch_operations:
            if patch_operation.get("unique_id") == operation["unique_id"]:
                patch_operation["unique_id"] = operation["unique_id"]

    if patch_operations:
        domains = tuple(sorted({edit_algebra.get(operation["op"]).domain
                                for operation in patch_operations}))
        patch_receipt = edit_patch.apply_live_patch(
            project=project, timeline=staged, capability="reel.touchup",
            operations=patch_operations, conflict_domains=domains,
            idempotency_key=(str(spec.get("transaction_id") or journal["id"])
                             + ":semantic-compound"),
            project_folder=project_folder)
        if patch_receipt.get("status") != "committed":
            raise TouchupError(
                f"staged compound EditPatch did not commit: {patch_receipt}")
        receipt.setdefault("edit_patches", []).append(patch_receipt)
        writes.append({"command": "EditPatch", "verified": True,
                       "receipt": patch_receipt})

    after_tracks = _read.read_tracks(staged)
    after = {"tracks": after_tracks}
    # The compiled operations remain the user's request. Only target IDs
    # are rebound to this staging copy; inserts receive their resolved ID
    # from the EditPatch receipt.
    for operation, mapped in zip(operations, mapped_operations):
        operation["unique_id"] = mapped["unique_id"]
    for operation in operations:
        if operation["op"] != "clip.insert":
            continue
        from library.tools import edit_algebra as _algebra
        pool_item = _algebra._find_pool_item_by_id(
            project.GetMediaPool(), operation["media_pool_item"])
        expected_file = (None if pool_item is None else
                         pool_item.GetClipProperty("File Path"))
        track = next(track for track in after_tracks
                     if str(track["type"]).lower()
                     == str(operation["track_type"]).lower()
                     and int(track["index"]) == int(operation["track_index"]))
        matches = [clip for clip in track.get("clips", ())
                   if str(clip.get("source_file")) == str(expected_file)
                   and int(clip["record_in"]) == int(operation["record_in"])
                   and int(clip["record_out"]) == int(operation["record_out"])]
        if len(matches) != 1:
            raise TouchupError(
                f"clip.insert read back {len(matches)} matching placements")
        operation["unique_id"] = str(matches[0]["unique_id"])
    failures = edit_algebra.verifies(operations, after)
    spans = [span for span in edit_algebra.spans(operations, before)
             if span is not None]
    collateral = _orchestration.unrelated_clip_changes(
        operations=operations, affected_spans=spans, id_map=None,
        before=before, after=after)
    audio_before = [clip for track in before_tracks
                    if str(track["type"]).lower().startswith("audio")
                    for clip in track.get("clips", ())]
    audio_after = [clip for track in after_tracks
                   if str(track["type"]).lower().startswith("audio")
                   for clip in track.get("clips", ())]
    acceptances = []
    for index, (operation, failure) in enumerate(zip(operations, failures)):
        evidence = {"target_identity": failure is None,
                    "unrelated_items_unchanged": not collateral}
        if operation["op"] == "clip.trim":
            before_target = next(clip for track in before_tracks
                                 for clip in track.get("clips", ())
                                 if str(clip["unique_id"])
                                 == original_target_ids[index])
            after_target = next((clip for track in after_tracks
                                 for clip in track.get("clips", ())
                                 if str(clip["unique_id"])
                                 == str(operation["unique_id"])), None)
            evidence.update({
                "requested_source_in_out": failure is None,
                "requested_record_span": failure is None,
                "approved_edits_preserved": (
                    after_target is not None
                    and isinstance(before_target.get("transform"), dict)
                    and isinstance(after_target.get("transform"), dict)
                    and before_target["transform"]
                    == after_target["transform"]),
                "linked_audio_preserved": audio_before == audio_after,
            })
        elif operation["op"] == "clip.insert":
            clip = next((clip for track in after_tracks
                         for clip in track.get("clips", ())
                         if str(clip["unique_id"])
                         == str(operation["unique_id"])), None)
            from library.tools import edit_algebra as _algebra
            pool_item = _algebra._find_pool_item_by_id(
                project.GetMediaPool(), operation["media_pool_item"])
            expected_file = (None if pool_item is None else
                             pool_item.GetClipProperty("File Path"))
            track = next((track for track in after_tracks
                          if str(track["type"]).lower()
                          == str(operation["track_type"]).lower()
                          and int(track["index"])
                          == int(operation["track_index"])), None)
            evidence.update({
                "source_identity": (clip is not None and expected_file
                                    and str(clip.get("source_file"))
                                    == str(expected_file)),
                "requested_record_span": (
                    clip is not None
                    and int(clip["record_in"]) == int(operation["record_in"])
                    and int(clip["record_out"]) == int(operation["record_out"])),
                "requested_track": (track is not None and clip is not None
                                    and any(str(item["unique_id"])
                                            == str(clip["unique_id"])
                                            for item in track.get("clips", ()))),
            })
        elif operation["op"] == "clip.replace":
            before_target = next(clip for track in before_tracks
                                 for clip in track.get("clips", ())
                                 if str(clip["unique_id"])
                                 == original_target_ids[index])
            after_target = next((clip for track in after_tracks
                                 for clip in track.get("clips", ())
                                 if str(clip["unique_id"])
                                 == str(operation["unique_id"])), None)
            evidence.update({
                "requested_source_identity": (after_target is not None
                    and after_target.get("source_file")
                    == operation["source_file"]),
                "record_span_preserved": (after_target is not None
                    and int(after_target["record_in"])
                    == int(before_target["record_in"])
                    and int(after_target["record_out"])
                    == int(before_target["record_out"])),
            })
        elif operation["op"] == "caption.style":
            style = style_expectations[str(operation["unique_id"])]
            evidence.update({
                "requested_record_span": failure is None,
                "caption_text_preserved": style["text_preserved"],
                "caption_style_applied": (
                    style["style_applied"]
                    and any(str(clip.get("unique_id"))
                            == str(operation["unique_id"])
                            and str(clip.get("source_file"))
                            == style["source_file"]
                            for track in after_tracks
                            for clip in track.get("clips", ()))),
            })
        acceptance = operation_acceptance.evaluate(
            operation, readback_passed=failure is None and not collateral,
            evidence=evidence)
        acceptances.append(acceptance)
    if collateral or any(row.get("passed") is not True
                         for row in acceptances):
        raise TouchupError(
            "full semantic compound verification failed before promotion: "
            f"operations={failures}, collateral={collateral}, "
            f"acceptance={acceptances}")
    receipt["semantic_writes"] = writes
    receipt["acceptance_contracts"] = acceptances
    receipt["verification_read"] = _summarise_rows(after_tracks)
    receipt["in_place"] = {}
    request_checks = [
        {"operation_index": index, "operation": str(operation["op"]),
         "passed": failure is None, "failure": failure}
        for index, (operation, failure) in enumerate(zip(operations, failures))
    ]
    receipt["semantic_full_request_verification"] = {
        "status": "verified", "checks": request_checks,
        "operation_acceptance": acceptances,
        "unrelated_items_unchanged": not collateral,
        "collateral_changes": collateral,
        "snapshot_source": "staged_live_readback",
        "operation_count": len(operations),
    }
    if candidate_lifecycle is not None:
        candidate_lifecycle.mark_applying()
        candidate_lifecycle.mark_applied()
    _promote(project_folder, project, project.GetMediaPool(), final, staging,
             declared_drops, declared_supersede, accept_editor_changes,
             receipt, dict(journal), spec, qualification,
             candidate_lifecycle=candidate_lifecycle)


def _repair_hold_window(staged: Any, entry: Mapping,
                        comp_dir: str) -> dict:
    """Rebind the staged hold item's comp windows, verified by re-read.

    The repair runs on the STAGING copy only: `hold_window_repair`
    rebinds each drawing comp to the Timeline binding and proves the
    window with fresh reads until consecutive reads agree and cover
    every played frame.  A hold that will not converge raises instead
    of shipping - the staging stands for diagnosis and the approved
    timeline was never touched.  The receipt carries the per-comp
    before/after and every fresh read, so the touch files what the
    promotion's only difference is.
    """
    from library.tools import hold_window_repair as _hold

    row = str(entry.get("row")).upper()
    try:
        record_frame = int(entry["record_frame"])
        duration = int(entry["duration"])
    except (TypeError, ValueError):
        raise TouchupError(
            f"the staged hold repair on {row} names no integer "
            f"record frame and duration ({entry.get('record_frame')!r}/"
            f"{entry.get('duration')!r}) - the plan and the timeline "
            f"disagree and the approved timeline stands") from None
    source = str(entry.get("source") or "")
    try:
        receipt = _hold.repair_hold_item_windows(
            staged, row, record_frame, duration, comp_dir)
    except _hold.HoldWindowRefused as refused:
        raise TouchupError(
            f"the staged {row}@{record_frame} hold window repair "
            f"refused: {refused}") from refused
    receipt["row"] = row
    receipt["record_frame"] = record_frame
    receipt["source"] = source
    return receipt


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
                 journal: dict, spec: Mapping, *,
                 candidate_lifecycle=None,
                 resolve_app=None) -> None:
    """Conform, compose, verify and promote the staging copy.

    Runs inside the cursor fence: the staging is the cursor for the
    whole section, and a foreign move fails the touchup rather than
    writing onto the wrong timeline.

    The conform/compose/verify phase opens here and closes when the
    promotion opens (`touchup_phases`): the two are DISJOINT intervals
    of one lease, and the profile must be able to tell them apart.
    """
    from library.tools import reel_read as _read

    touchup_phases.open_phase("conform_compose_verify")

    source_rows = _live_rows(source)
    source_snapshot_tracks = _read.read_tracks(source)
    staged_rows = _live_rows(staged)
    structural = {str(edit.get("op") or "")
                  for edit in (spec.get("edits") or ())}
    if structural & {"split", "slide", "roll"}:
        for change in qualification.changes:
            row = _row_of(change.track_type, int(change.track_index))
            items = source_rows.get(row) or []
            index = int(change.item_index)
            if index >= len(items):
                raise TouchupRefused(
                    f"structural edit cannot read source item {row}[{index}]",
                    "candidate-local reconstruction requires the approved source item",
                    "re-read the timeline and retry")
            item = items[index]
            linked_reader = getattr(item, "GetLinkedItems", None)
            if linked_reader is None:
                raise TouchupRefused(
                    f"structural edit cannot determine linked-item state for {row}[{index}]",
                    "reconstruction must preserve linked audio/video relationships",
                    "use a Resolve build with readable linked-item state")
            try:
                linked = list(linked_reader() or [])
            except Exception as exc:
                raise TouchupRefused(
                    f"structural edit cannot read linked-item state for {row}[{index}]: {exc}",
                    "reconstruction must preserve linked audio/video relationships",
                    "re-read the timeline and retry") from exc
            if linked:
                raise TouchupRefused(
                    f"structural edit target {row}[{index}] is linked",
                    "this candidate route cannot carry linked items",
                    "use an unlinked clip or a link-aware route")
    from library.tools.project_layout import Area, ProjectLayout
    comp_dir = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.SCRATCH)),
        "touchup", _safe_slug(final))
    os.makedirs(comp_dir, exist_ok=True)
    touchup_phases.mark("conform")
    conform_receipt = _ce.conform_comp_windows(
        source_rows, staged_rows, comp_dir=comp_dir,
        preserve_passthrough=True)
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

    if spec.get("transaction_operations"):
        _apply_semantic_transaction_staged(
            project_folder, project, source, staged, staging, final,
            qualification, receipt, declared_drops, declared_supersede,
            accept_editor_changes, journal, spec,
            candidate_lifecycle=candidate_lifecycle)
        return

    # In-place writes land FIRST, before anything is deleted: the
    # composition's capture reads the staged item as it stands, so a
    # re-place further down carries the write instead of losing it.
    receipt["new_rows"] = _add_rows(staged, qualification.new_rows)
    receipt["in_place"] = _apply_in_place(
        project, staged, qualification, comp_dir, journal["id"],
        project_folder, source=source)
    if receipt["in_place"].get("patch_receipt"):
        receipt.setdefault("edit_patches", []).append(
            receipt["in_place"]["patch_receipt"])

    insertions = _resolve_insertions(
        pool, staged, qualification.insertions)
    # A native still never rides the composition's one-call placement:
    # `AppendToTimeline` takes no duration for image items, so each
    # still insertion is placed after the composition through the
    # still-duration preference route and judged by a re-read, like
    # the still resize - without disturbing the video batch. Scoped
    # to the no-length-change class: a played-length change plus a
    # still insertion is refused rather than sequenced by guesswork.
    still_insertions = [entry for entry in insertions
                        if _is_native_still(entry.media_pool_item)]
    insertions = [entry for entry in insertions
                  if not _is_native_still(entry.media_pool_item)]
    if still_insertions and qualification.gate_class != COMPOSED:
        raise TouchupRefused(
            f"{len(still_insertions)} still insertion(s) in a "
            f"{qualification.gate_class} change",
            "still insertions ride only the no-length-change class - "
            "sequencing a preference-route placement around a ripple "
            "or a re-derivation is unproven",
            "state the change without a played-length edit, then "
            "re-run `ren touch`")
    changes = list(qualification.changes)

    # Vacate the removal spans FIRST, on the staging copy: the
    # composition only deletes what it re-places, so without
    # this the swap's insertion would collide with the item it
    # replaces and `AppendToTimeline` would silently place
    # nothing.  `_resolve_insertions` above already read the
    # carried treatments off these same handles, so nothing
    # needed dies with them.
    receipt["pre_delete"] = _pre_delete_removed(
        project, staged, qualification.removals, journal["id"],
        project_folder=project_folder)
    if receipt["pre_delete"].get("patch_receipt"):
        receipt.setdefault("edit_patches", []).append(
            receipt["pre_delete"]["patch_receipt"])
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

    still_placer = None
    if qualification.gate_class == COMPOSED_STILL_RESIZE:
        if resolve_app is None:
            raise TouchupError(
                "the still resize has no active Resolve application handle; "
                "the approved timeline stands")
        from library.tools.still_placement import place_still_exact
        try:
            fps = float(staged.GetSetting("timelineFrameRate"))
        except (TypeError, ValueError, AttributeError) as unreadable:
            raise TouchupError(
                f"the staging timeline's frame rate is unreadable "
                f"({unreadable}); the approved timeline stands") from unreadable

        def still_placer(capture):
            change = capture.change
            return place_still_exact(
                resolve_app, project, pool, staged,
                capture.media_pool_item, int(change.duration),
                int(change.record_frame), int(change.track_index), fps,
                timeline_name=final)

    edit_started = time.time()
    touchup_phases.mark("compose")
    from library.tools import reel_component_manifest as _component_manifest
    prior_component_manifest = _component_manifest.latest_manifest(
        project_folder, final)
    # In particular, overlay.add is a protected staged operation, not a
    # clip insertion; the latter loses the overlay verification and recovery
    # contract at the router boundary.
    structural_operations = [
        {"op": _STRUCTURAL_OPERATION_IDS[str(edit.get("op") or "")]}
        for edit in (spec.get("edits") or ())
        if str(edit.get("op") or "") in _STRUCTURAL_OPERATION_IDS
    ]
    staged_tracks_before_edit = _read.read_tracks(staged)
    composed = _ce.apply_composed_edit(
        timeline=staged, media_pool=pool, changes=changes,
        insertions=insertions,
        comp_dir=os.path.join(comp_dir, "comps"),
        withheld_dir=os.path.join(comp_dir, "withheld"),
        rederiver=rederiver,
        grade_sources=grade_sources,
        link_rows=_link_rows_for(staged_tracks_before_edit),
        picture_rows=_picture_rows(staged_tracks_before_edit),
        still_placer=still_placer,
        write_context={"project_folder": project_folder,
                       "structural_operations": structural_operations,
                       "structural_deletions": qualification.removals},
        component_manifest=prior_component_manifest,
        candidate_lifecycle=candidate_lifecycle)
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
    touchup_phases.mark("verify")
    verify_started = time.time()
    if still_insertions:
        receipt["still_insertions"] = _place_still_insertions(
            project_folder, project, pool, staged, final,
            still_insertions, resolve_app=resolve_app)
        staged_tracks = _read.read_tracks(staged)
        _assert_still_insertions(staged_tracks, still_insertions)
    resize_rows = (receipt.get("in_place") or {}).get(
        "overlay_resizes") or []
    if resize_rows:
        if resolve_app is None:
            raise TouchupError(
                "overlay.resize requires a Resolve handle for independent "
                "rendered-pixel verification; approved timeline remains intact")
        from library.tools.overlay_verify import verify_rendered_boxes
        pixel_report = verify_rendered_boxes(
            resolve_app, project, staged,
            [row["pixel_record"] for row in resize_rows],
            os.path.join(comp_dir, "overlay_resize_pixels"),
            tolerance_px=2)
        if (pixel_report.get("passed") is not True
                or int(pixel_report.get("checked", 0)) != len(resize_rows)):
            raise TouchupError(
                "overlay.resize rendered-pixel verification failed or was "
                f"incomplete: {pixel_report}")
        for row in resize_rows:
            row["verified"] = True
        receipt["in_place"]["overlay_resize_pixels"] = pixel_report
    position_rows = (receipt.get("in_place") or {}).get(
        "overlay_positions") or []
    if position_rows:
        if resolve_app is None:
            raise TouchupError(
                "overlay.position requires a Resolve handle for independent "
                "rendered-pixel verification; approved timeline remains intact")
        from library.tools.overlay_verify import verify_rendered_boxes
        position_report = verify_rendered_boxes(
            resolve_app, project, staged,
            [row["pixel_record"] for row in position_rows],
            os.path.join(comp_dir, "overlay_position_pixels"),
            tolerance_px=2)
        if (position_report.get("passed") is not True
                or int(position_report.get("checked", 0)) != len(position_rows)):
            raise TouchupError(
                "overlay.position rendered-pixel verification failed or was "
                f"incomplete: {position_report}")
        for row in position_rows:
            row["verified"] = True
        receipt["in_place"]["overlay_position_pixels"] = position_report
    staged_tracks = _read.read_tracks(staged)
    receipt["verification_read"] = _summarise_rows(staged_tracks)
    # The trim claims, judged on the read-back that just happened.
    # A length-changing edit is a trim of one clip and a move of
    # everything it moves, and the receipt says which of those it
    # PROVED - source frames, record frames, the move, the linked
    # audio and the caption row - rather than saying "trim" and leaving
    # the reader to guess what that covered.
    trim_claims = _judge_trim_claims(
        spec, qualification, staged_tracks_before_edit, staged_tracks)
    if trim_claims is not None:
        receipt["trim_qualification"] = trim_claims
        failed = [name for name, row in trim_claims["claims"].items()
                  if row.get("status") == "failed"]
        unmeasured = [name for name, row in trim_claims["claims"].items()
                      if row.get("status") == "unmeasured"]
        if failed or unmeasured:
            raise TouchupError(
                f"the trim's own claims did not hold on the staged "
                f"read-back (failed: {failed or 'none'}; unmeasured: "
                f"{unmeasured or 'none'}); the approved timeline is "
                "unchanged")
    acceptance_operations = list(spec.get("acceptance_operations") or ())
    if acceptance_operations:
        from library.tools import edit_algebra, operation_acceptance
        from library.tools.transaction_orchestration import (
            unrelated_clip_changes,
        )

        before_snapshot = {"tracks": source_snapshot_tracks}
        after_snapshot = {"tracks": staged_tracks}
        failures = edit_algebra.verifies(
            acceptance_operations, after_snapshot)
        spans = [span for span in edit_algebra.spans(
            acceptance_operations, before_snapshot) if span is not None]
        collateral = unrelated_clip_changes(
            operations=acceptance_operations,
            affected_spans=spans, id_map=None,
            before=before_snapshot, after=after_snapshot)
        contracts = []
        for operation, failure in zip(acceptance_operations, failures):
            contracts.append(operation_acceptance.evaluate(
                operation, readback_passed=failure is None,
                evidence={"unrelated_items_unchanged": not collateral}))
        receipt["acceptance_contracts"] = contracts
        failed = [contract for contract in contracts
                  if not contract["passed"]]
        if failed:
            detail = [{"operation": contract["operation"],
                       "missing": contract["missing"],
                       "failed": contract["failed"]}
                      for contract in failed]
            raise TouchupError(
                f"operation acceptance failed before promotion: {detail}; "
                "approved timeline remains intact")
    receipt["verify_seconds"] = round(
        time.time() - verify_started, 3)

    _promote(project_folder, project, pool, final, staging,
             declared_drops, declared_supersede, accept_editor_changes,
             receipt, journal, spec, qualification,
             candidate_lifecycle=candidate_lifecycle)


def _link_rows_for(tracks: Sequence[Mapping]) -> dict:
    """`{row: partner_row}` for the rows whose clips are linked.

    A re-placed item is a NEW object on the timeline, so the link
    between a picture and its audio is gone unless the composition
    re-establishes it.  A ripple re-places BOTH - the picture and the
    sound - so without this the edit that was supposed to keep the
    reel's sound with its picture DROPS the link between them, and the
    reel ships with a picture and a sound that no longer move
    together.

    The composition restores a link to the partner items that start at
    the same record frame on the partner row, so the mapping is the row
    PAIR the live read says are linked - read off the clips' own linked
    ids, never guessed from a row index.
    """
    row_of_id: dict = {}
    for track in tracks:
        row = _row_of(track["type"], int(track["index"]))
        for clip in track.get("clips") or ():
            unique_id = str(clip.get("unique_id") or "")
            if unique_id:
                row_of_id[unique_id] = row
    partners: dict = {}
    for track in tracks:
        row = _row_of(track["type"], int(track["index"]))
        rows = set()
        for clip in track.get("clips") or ():
            for linked in clip.get("linked_unique_ids") or ():
                partner_row = row_of_id.get(str(linked))
                if partner_row and partner_row != row:
                    rows.add(partner_row)
        if len(rows) == 1:
            partners[row] = next(iter(rows))
    return partners


def _summarise_rows(tracks: Sequence[Mapping]) -> dict:
    """`{row: [{start, duration}]}` - the verification read, pasted."""
    out = {}
    for track in tracks:
        row = _row_of(track["type"], int(track["index"]))
        out[row] = [{"start": int(c["record_in"]),
                     "duration": int(c["duration"])}
                    for c in (track.get("clips", []) or ())]
    return out


def _judge_trim_claims(spec: Mapping, qualification,
                       before_tracks: Sequence[Mapping],
                       after_tracks: Sequence[Mapping]) -> dict | None:
    """Judge a length-changing touch-up's own trim claims.

    A retime IS a trim of one clip and a move of everything the change
    moves, and the two are separate promises.  `library/tools/
    trim_qualification.py` owns the claims and how each is judged; this
    hands it the plan's own arithmetic and the read-backs that
    surround the edit, and returns its verdict.

    None when nothing's played length changed: there is no trim to
    claim, and a receipt that claimed one anyway would be a lie.
    """
    from library.tools import trim_qualification as _trim

    changes = list(getattr(qualification, "changes", None) or ())
    stretched = [change for change in changes
                 if getattr(change, "played_length_changes", False)]
    if not stretched:
        return None
    target = stretched[0]
    moves = tuple(
        (str(change.row), int(change.item_index),
         int(change.record_frame),
         int(change.record_frame) + int(change.duration))
        for change in changes)
    request = _trim.TrimRequest(
        row=str(target.row),
        item_index=int(target.item_index),
        source_delta=(int(target.duration)
                      - int(target.previous_duration)),
        record_in=int(target.record_frame),
        record_duration=int(target.duration),
        semantics=_trim.RIPPLE,
        moves=moves)
    verdict = _trim.judge(before_tracks, after_tracks, request)
    return verdict.to_dict()


@touchup_phases.measured("promote")
def _archive_replaced_generation(project_folder: str, project: Any,
                                 timeline: Any, final: str) -> dict:
    """Archive the generation a touch-up is about to replace (VC-002).

    A touch replaces a generation like any other path does, so the
    generation it replaces is archived BEFORE the swap - with its
    identity, its structure and its media, and attached to the version
    record that generation is.

    A generation is exported ONCE: a version that already carries an
    archive which still verifies keeps it, and no `.drt` is written
    again.

    Unlike the promotion, this does NOT refuse the touch when the
    archive cannot be made, and the difference is deliberate: a
    touch's own reversal is its undo journal, and refusing the
    captain's edit because a `.drt` could not be exported would trade a
    recoverable edit for an unrecoverable one. What the promotion
    refuses on, the touch RECORDS - so a reader knows whether the
    generation the touch replaced can be put back, and says so by
    name instead of by silence.
    """
    from library.tools.versions import replaced_generation, reel_versions

    history = reel_versions.versions_of(project_folder, final)
    entry = history[-1] if history else None
    if entry is None:
        # The generation is not a recorded version, so there is no
        # record to attach an archive to and nothing that could be
        # restored by name. Said so, not guessed at.
        return {"status": "untracked",
                "reason": "the generation being replaced is not a "
                          "recorded version"}
    receipt = reel_versions.live_receipt(entry)
    if receipt is not None and reel_versions.archive_verifies(receipt):
        # The generation is exported ONCE: a version that already
        # carries an archive which still verifies keeps it, and no
        # `.drt` is written again - and no Resolve handle is needed.
        return {"status": "verified", "receipt": dict(receipt),
                "reason": ""}
    from library.tools.reel_build import _connect_resolve
    try:
        resolve = _connect_resolve()
    except Exception as exc:  # noqa: BLE001 - the reason is the record
        return {"status": "unavailable",
                "reason": f"no Resolve handle to export the archive: {exc!r}"}
    outcome = replaced_generation.ensure_archived(
        resolve=resolve, project=project, timeline=timeline,
        project_folder=project_folder, entry=entry,
        label=f"generation replaced by a touch on {final}",
        rows_digest=replaced_generation.live_rows_digest(timeline, final))
    if outcome["status"] in ("exported", "reused"):
        reel_versions.attach_recovery_archive(
            project_folder, final, int(entry["version"]),
            outcome["receipt"])
    return outcome


def _promote(project_folder: str, project: Any, pool: Any,
             final: str, staging: str, declared_drops,
             declared_supersede, accept_editor_changes, receipt: dict,
             journal: dict, spec: Mapping, qualification,
             candidate_lifecycle=None) -> None:
    """Guard, swap names, carry markers, close the journal, delete the
    replaced generation, close the signature."""
    from library.tools import plan_provenance as _provenance
    from library.tools import reel_read as _read
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

    # The promotion's own steps, measured.  The punchlist asks for the
    # promotion's seconds to be SPLIT - the state reconciliation and
    # the final promotion are two different bills - and the marks are
    # where one ends and the next begins.  The steps sum to the phase,
    # so the split moves no seconds.
    touchup_phases.mark("snapshots")

    audio_fade_entries = [entry for entry in qualification.in_place
                          if entry.get("kind") == "audio_fade"]
    if audio_fade_entries:
        if len(audio_fade_entries) != 1:
            raise TouchupError(
                "audio.fade promotion needs exactly one target item")
        entry = audio_fade_entries[0]
        row = str(entry["row"]).upper()
        key = (int(row[1:]), str(entry["source_file"]),
               int(entry["source_in"]), int(entry["source_out"]),
               int(entry["record_frame"]), int(entry["record_out"]))
        recorded = (receipt.get("in_place") or {}).get("audio_fades") or []
        if len(recorded) != 1 or recorded[0].get("verified") is not True:
            raise TouchupError(
                "audio.fade has no exact staged read-back receipt")
        before = _audio_fades(originals[final])
        after = _audio_fades(staged_found[staging])
        prior = recorded[0]["before"]
        requested = recorded[0]["after"]
        if before.get(key) != prior:
            raise TouchupError(
                "the approved audio fade changed after staging; promotion "
                "refuses to overwrite that newer edit")
        expected = dict(before)
        expected[key] = requested
        if after != expected:
            raise TouchupError(
                "audio fade promotion read-back found a changed target or "
                "unrelated audio fade state")
        receipt["audio_fade_promotion_readback"] = {
            "before": len(before), "after": len(after),
            "target": key, "verified": True,
        }

    transition_entries = [entry for entry in qualification.in_place
                          if entry.get("kind") == "native_transition"]
    if transition_entries:
        if len(transition_entries) != 1:
            raise TouchupError(
                "transition promotion needs exactly one exact-cut target")
        entry = transition_entries[0]
        recorded = (receipt.get("in_place") or {}).get("transitions") or []
        if len(recorded) != 1 or recorded[0].get("verified") is not True:
            raise TouchupError(
                "transition has no exact staged AddTransition read-back")
        row = str(entry["row"]).upper()
        outgoing_uid = str(entry["outgoing_unique_id"])
        incoming_uid = str(entry["incoming_unique_id"])
        cut = int(entry["cut_frame"])
        source_tracks = _read.read_tracks(originals[final])
        staged_tracks = _read.read_tracks(staged_found[staging])

        def find_cut(tracks):
            matches = [
                [clip for clip in track.get("clips") or ()]
                for track in tracks
                if _row_of(track["type"], int(track["index"])) == row
            ]
            if len(matches) != 1:
                return None
            clips = matches[0]
            out = [index for index, clip in enumerate(clips)
                   if clip.get("unique_id") == outgoing_uid]
            inc = [index for index, clip in enumerate(clips)
                   if clip.get("unique_id") == incoming_uid]
            if (len(out) != 1 or len(inc) != 1
                    or inc[0] != out[0] + 1
                    or int(clips[out[0]]["record_out"]) != cut
                    or int(clips[inc[0]]["record_in"]) != cut + 1):
                return None
            return clips[out[0]], clips[inc[0]]

        source_cut = find_cut(source_tracks)
        staged_cut = find_cut(staged_tracks)
        if source_cut is None or staged_cut is None or source_cut != staged_cut:
            raise TouchupError(
                "the approved and staged timelines no longer share the exact "
                "outgoing/incoming cut; transition promotion refused")
        wanted_start = cut - int(entry["duration_frames"]) // 2
        wanted_end = wanted_start + int(entry["duration_frames"]) - 1
        if (recorded[0].get("outgoing_unique_id") != outgoing_uid
                or recorded[0].get("incoming_unique_id") != incoming_uid
                or recorded[0].get("cut_frame") != cut
                or recorded[0].get("record_in") != wanted_start
                or recorded[0].get("record_out") != wanted_end
                or recorded[0].get("duration_frames")
                != int(entry["duration_frames"])):
            raise TouchupError(
                "transition promotion read-back does not match the exact "
                "requested cut, type and duration")
        receipt["transition_promotion_readback"] = {
            "outgoing_unique_id": outgoing_uid,
            "incoming_unique_id": incoming_uid,
            "cut_frame": cut,
            "record_in": wanted_start,
            "record_out": wanted_end,
            "verified": True,
        }

    live_full = _guard.full_timeline_snapshot(
        originals[final], project, project_folder)
    staged_full = _guard.full_timeline_snapshot(
        staged_found[staging], project, project_folder)
    from library.tools import editor_edit_carry as _editor_carry
    try:
        touch_edits = _editor_carry.derive_touch_edits(
            final, live_full, staged_full, receipt.get("in_place") or {},
            journal_id=journal["id"],
            plan_version=_editor_carry._plan_version(project_folder))
    except _editor_carry.EditorEditCarryRefused as unstatable:
        raise TouchupError(str(unstatable)) from unstatable
    receipt["touch_carried"] = [
        {"id": edit["id"], "kind": edit["kind"], "field": edit["field"],
         "row": edit["row"], "name": edit["name"],
         "source": edit["source"], "wording": edit["wording"]}
        for edit in touch_edits]
    touchup_phases.mark("editor_guard")
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
        # Items this spec removes are replaced wholesale by declaration -
        # their baseline-vs-live deltas are the touch's own replacement,
        # never unattributed losses (`reel_replace_guard`).  An in-place
        # hold-window repair rewrites the same item's comp inputs by
        # declaration too, so its span attributes the same way: the
        # journal archives both sides and the receipt records every
        # write, which is what makes the delta attributed rather than
        # phantom.
        replaced_spans = []
        for removal in (getattr(qualification, "removals", None) or ()):
            row = str((removal or {}).get("row") or "").upper()
            try:
                record_frame = int((removal or {}).get("record_frame"))
                duration = int((removal or {}).get("duration"))
                if row.startswith("V"):
                    replaced_spans.append(
                        ("video", int(row[1:]), record_frame, duration))
                elif row.startswith("A"):
                    replaced_spans.append(
                        ("audio", int(row[1:]), record_frame, duration))
            except (TypeError, ValueError):
                continue
        for entry in (getattr(qualification, "in_place", None) or ()):
            if str((entry or {}).get("kind") or "") != "repair_hold_window":
                continue
            row = str((entry or {}).get("row") or "").upper()
            try:
                record_frame = int((entry or {}).get("record_frame"))
                duration = int((entry or {}).get("duration"))
                if row.startswith("V"):
                    replaced_spans.append(
                        ("video", int(row[1:]), record_frame, duration))
                elif row.startswith("A"):
                    replaced_spans.append(
                        ("audio", int(row[1:]), record_frame, duration))
            except (TypeError, ValueError):
                continue
        receipt["editor_changes"] = _guard.protect_editor_changes(
            project_folder, final, live_full, live_full, staged_full,
            accept=_guard.accepts_editor_changes(
                final, accept_editor_changes), detection=detection,
            carried_edits={"edits": edits, "superseded": superseded,
                           "written": [], "already_held": []},
            replaced_spans=replaced_spans)
    except _guard.EditorChangeRefused as refused:
        raise TouchupError(str(refused)) from refused

    declared = _guard.parse_specs(declared_drops, [final])
    editor_override_rows = _guard.accepted_editor_drop_rows(
        receipt["editor_changes"])
    allowed_rows = set(declared.get(final, ())) | editor_override_rows
    _signoff.assert_declared(project_folder, final,
                             declared_supersede,
                             command="touch-reel")
    touchup_phases.mark("replace_guard")
    incoming_rows = _guard.snapshot_timeline(staged_found[staging],
                                             staging, side="staged")
    retired_rows = _guard.snapshot_timeline(originals[final], final,
                                            side="retiring")
    receipt["replace_report"] = _guard.check_replacement(
        final, staging, retired_rows, incoming_rows,
        allowed=allowed_rows)
    receipt["accepted_editor_drop_rows"] = sorted(editor_override_rows)

    touchup_phases.mark("markers")
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

    # VC-002: the generation this touch is about to REPLACE is archived
    # before it is replaced, so the state the captain saw is still
    # there afterwards. The archive holds that generation exactly -
    # its identity, its structure and its media - and it is attached
    # to the version record that generation is, so
    # `ren undo --restore-version` can put it back.
    receipt["replaced_generation_archive"] = _archive_replaced_generation(
        project_folder, project, originals[final], final)

    backup = backup_name(final)
    retry_backup_ids = _failed_touchup_backup_ids(
        project_folder, final, backup,
        receipt.get("timeline_inventory_before") or ())
    from library.tools.composed_edit import (
        CandidateLifecycle,
        promote_candidates,
        rename_candidate_set,
    )
    if candidate_lifecycle is None:
        candidate_lifecycle = CandidateLifecycle.create(
            originals[final], staged_found[staging], final)
        candidate_lifecycle.mark_applied()
    candidate_lifecycle.mark_verified(
        modified=bool(receipt.get("verification_read")),
        preserved=(not receipt["replace_report"].get("refused")
                   and not receipt["editor_changes"].get("refused")),
        evidence={"readback": receipt.get("verification_read"),
                  "replacement": receipt["replace_report"],
                  "editor_changes": receipt["editor_changes"].get(
                      "preservation")})
    try:
        _rename_result, lifecycle_records = promote_candidates(
            [candidate_lifecycle],
            lambda: rename_candidate_set([{
                "original": originals[final],
                "candidate": staged_found[staging],
                "final": final, "staging": staging, "backup": backup,
                "original_id": originals[final].GetUniqueId(),
                "candidate_id": staged_found[staging].GetUniqueId(),
            }]))
    except Exception as promotion_error:
        raise TouchupError(
            f"the verified candidate {staging!r} could not be promoted "
            f"({promotion_error}); its approved generation remains "
            f"recoverable.") from promotion_error
    touchup_phases.mark("rename")
    receipt["candidate_lifecycle"] = lifecycle_records[0]
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
    touchup_phases.mark("marker_gate")
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

    if receipt.get("edit_patches"):
        from library.tools import timeline_shadow
        try:
            head = timeline_shadow.observe(project, staged_found[staging])
        except Exception as unreadable:
            raise TouchupError(
                f"{final!r} is promoted, but the final EditPatch generation "
                f"could not be recorded ({unreadable}); recover from "
                f"{backup!r} and re-run `ren timeline observe`.") \
                from unreadable
        receipt["edit_patch_head"] = head.summary()

    # THE JOURNAL CLOSES before the replaced generation goes: until
    # `after` is on disk, the backup is the only way back.
    touchup_phases.mark("journal")
    from library.tools import undo_journal as _journal
    _journal.close_entry(project_folder, journal,
                         after_timeline=staged_found[staging],
                         rows=incoming_rows, project=project)
    receipt["version"] = journal["version"]
    # The creative commit this touch is, recorded from the closed
    # journal's before/after. Non-fatal: a commit that cannot be
    # recorded never fails the touch (the journal is the way back), but
    # it is VISIBLE - printed to stderr and put on the receipt - never
    # swallowed in silence.
    try:
        receipt["commit"] = _journal.record_touch_commit(
            project_folder, journal=journal, spec=spec,
            qualification=qualification, version=journal.get("version"))
    except _journal.CommitRecordError as unrecorded:
        print(f"creative commit not recorded: {unrecorded}",
              file=sys.stderr)
        receipt["commit"] = None
        receipt["commit_error"] = str(unrecorded)
    # The promotion MERGES the touch commit into the final timeline's
    # chain - but only when the branches have DIVERGED: the touch
    # commit's parent is no longer the timeline's head (another commit
    # landed while the staging was in flight). A linear touch stands on
    # the branch's own base, and recording a merge for it would fill
    # history with no-op merges. Best-effort, like the commit.
    commit = receipt.get("commit")
    branch_base = receipt.get("branch_base")
    if (commit and commit.get("parent") and branch_base
            and commit["parent"] != branch_base):
        try:
            receipt["merge"] = _journal.merge_commits(
                project_folder, timeline=final,
                left=branch_base, right=commit["id"],
                note=f"touch {journal['id']}")
        except _journal.MergeConflict as unmerged:
            print(f"creative merge not recorded: {unmerged}",
                  file=sys.stderr)
            receipt["merge"] = None
            receipt["merge_error"] = str(unmerged)
    if journal.get("preservation_after") is not None:
        from library.tools import editor_edit_carry as _editor_carry
        prior = receipt["editor_changes"].get("carried_edits") or {}
        combined = {**prior,
                    "edits": list(prior.get("edits") or ()) + touch_edits,
                    "superseded": dict(prior.get("superseded") or {})}
        receipt["carried_edits"] = _editor_carry.record_after_promotion(
            project_folder, final, combined,
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
        ren_owned_ids=ren_owned_ids,
        operation_id=receipt["inventory_operation"])
    for record_id in (receipt.get("editor_changes") or {}).get("carried", ()):
        _provenance.resolve_editor_change(
            os.path.join(project_folder, "pipeline_output", "review"),
            final, record_id, status="carried")
    for record_id in (receipt.get("editor_changes") or {}).get(
            "touch_replaced", ()):
        _provenance.resolve_editor_change(
            os.path.join(project_folder, "pipeline_output", "review"),
            final, record_id, status="superseded",
            superseded_by=f"touchup {journal['id']} (spec-replaced item)")
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
    touchup_phases.mark("retire")
    from library.tools import reel_retirement as _retire
    from library.tools.versions import rounds as _rounds
    backup_timelines = timelines_to_replace(project, {backup})
    current_backup_id = str(originals[final].GetUniqueId() or "")
    stale_backups = [timeline for timeline in backup_timelines
                     if str(timeline.GetUniqueId() or "")
                     in retry_backup_ids]
    stale_backup_ids = {str(timeline.GetUniqueId() or "")
                        for timeline in stale_backups}
    current_backup = next((timeline for timeline in backup_timelines
                           if str(timeline.GetUniqueId() or "")
                           == current_backup_id), None)
    if current_backup is None:
        raise TouchupError(
            f"the replaced generation for {final!r} is no longer present "
            f"as {backup!r}; no backup was deleted.")
    known_backup_ids = stale_backup_ids | {current_backup_id}
    unowned_backups = [timeline for timeline in backup_timelines
                       if str(timeline.GetUniqueId() or "")
                       not in known_backup_ids]
    recorded = _rounds.discover(project_folder)
    current_round = recorded[-1]["round"] if recorded else 1
    by_final = {final: _retire.retiring_round(recorded, final,
                                              current_round)}
    if _signoff.base_name(final) in _signoff.signed_off(project_folder):
        stale_retirement = (_retire.delete_backups(
            project, pool, stale_backups, project_folder=project_folder,
            run_id=journal["id"], job_id=journal["id"]) if stale_backups
            else {"deleted": []})
        retirement = _retire.retire_timelines(
            project, pool,
            {final: current_backup},
            by_final)
        retirement["deleted"] = stale_retirement["deleted"]
    else:
        retirement = {"archived": {}, "unfiled": [], "collected": [],
                      "kept": [], **_retire.delete_backups(
                          project, pool,
                          [current_backup, *stale_backups],
                          project_folder=project_folder,
                          run_id=journal["id"], job_id=journal["id"])}
    retirement.setdefault("kept", [])
    retirement["kept"].extend(
        {"name": backup,
         "why": "not tied to an incomplete failed Ren touch promotion"}
        for _timeline in unowned_backups)
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


def _failed_touchup_backup_ids(project_folder: str, final: str,
                               backup: str,
                               inventory_before: Sequence[Mapping]) -> set[str]:
    """Find backups a failed Ren touch actually renamed aside.

    A backup name alone does not establish ownership. The timeline id must
    have moved from the final name to the backup name in a touch operation's
    before/after inventory, and that operation must line up with a failed
    touch journal entry for this same reel. The caller deletes it only after
    the retry's marker gate and undo journal have both closed successfully.
    """
    from datetime import datetime

    from library.tools import plan_provenance as _provenance
    from library.tools import undo_journal as _journal

    current_backup_ids = {
        str(item["unique_id"])
        for item in inventory_before
        if item["name"] == backup and item.get("unique_id")
    }
    if not current_backup_ids:
        return set()

    failed_entries = [entry for entry in _journal.list_entries(project_folder)
                      if entry.get("final") == final
                      and entry.get("status") == "failed"]
    if not failed_entries:
        return set()
    review_dir = os.path.join(project_folder, "pipeline_output", "review")
    provenance = _provenance.read_provenance(review_dir) or {}
    history = provenance.get(_provenance.TIMELINE_INVENTORY_KEY) or []
    ren_backup_ids = set()
    for operation in history:
        if operation.get("operation") != "touchup":
            continue
        try:
            operation_time = datetime.fromisoformat(operation["started_at"])
        except (KeyError, TypeError, ValueError):
            continue
        matched_failure = False
        for entry in failed_entries:
            try:
                opened_at = datetime.fromisoformat(entry["opened_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if abs((operation_time - opened_at).total_seconds()) <= 60:
                matched_failure = True
                break
        if not matched_failure:
            continue
        before_final_ids = {
            str(item["unique_id"])
            for item in operation.get("before") or ()
            if item["name"] == final and item.get("unique_id")
        }
        after_backup_ids = {
            str(item["unique_id"])
            for item in operation.get("after") or ()
            if item["name"] == backup and item.get("unique_id")
        }
        ren_backup_ids.update(before_final_ids & after_backup_ids)
    return current_backup_ids & ren_backup_ids


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
                   receipt: dict, journal_id: str = "") -> str:
    """The touchup's own account of itself, readable afterwards.

    Filed onto the touch's undo journal entry (cascade Lane E2, R6),
    never as a standalone `touchup_*.json` file: the entry already
    carries the request (`spec`) and the before/after that reverse
    it, so the account joins them under `receipt` and `read_entry`
    serves the whole act. Files from before this lane stay on disk,
    ignored. Returns the entry path as the receipt's address.
    """
    if not journal_id:
        journal_id = str((receipt or {}).get("journal") or "")
    if not journal_id:
        raise TouchupError(
            f"the touch-up on {final!r} has no journal entry to file "
            f"its receipt on - refusing to write a standalone receipt "
            f"file no reader owns")
    from library.tools import undo_journal as _journal

    path = _journal._entry_path(project_folder, journal_id)
    receipt = dict(receipt or {})
    receipt["receipt_path"] = path
    return _journal.attach_receipt(project_folder, journal_id, receipt)


__all__ = [
    "COMPOSED",
    "COMPOSED_STILL_RESIZE",
    "COMPOSED_WITH_REDERIVATION",
    "Qualification",
    "TouchupError",
    "TouchupRefused",
    "_NullRederiver",
    "apply_touchup",
    "check_manifest_matches",
    "locate_named_clip",
    "pool_item_for_path",
    "qualify",
    "recorded_fusion_manifest",
    "reel_numbers",
    "resolve_final_name",
    "swap_spec_for_tracks",
    "touchup_all_reels",
]
