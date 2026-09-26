"""An edit Resolve has no verb for, composed out of the verbs it has.

Resolve's scripting API offers `MediaPool.AppendToTimeline` (place) and
`Timeline.DeleteClips` (delete) and nothing else for picture: no trim,
no move, no ripple, no `SetMediaPoolItem`.  Changing an item therefore
means deleting it and placing a new one, and the new one is a **new
object** carrying none of the old one's state.  What that costs against
a rebuild is MEASURED, not quoted: the composition mechanism alone
(delete, place, restore) holds at ~2 s, but staging (copy + conform)
measured 8.9-25.9 s and step 7 re-derivation 17.8-37.4 s, for composed
totals of 44.1 s and 54.9 s against rebuilds of 24.0 s and 59.2 s on
the two measured length-changing cases (`docs/RULE_EVIDENCE.md`,
"What it costs, and it is not the spike's figure").  The spike's
~112 s rebuild figure predates both the re-derivation condition below
and the PR 1217 lease split, and is not used as a comparison anywhere
in this module.

This module is that composition, and it is **seven steps**.  Each one
exists because something measurably failed without it (spike report
`vep-work-around-the-api-not-give-up-on-it`, 2026-09-11/12, every claim
executed against Resolve Studio 21.1 and measured on exported lossless
PNG RGB8):

  1. **Stage.**  Never edit an approved reel in place.  Work on a copy
     and `conform_comp_windows` it against the source first - a copy's
     `MediaIn` windows are NORMALISED by the copy (`comp_media_window`),
     so a copy is not automatically a faithful control.
  2. **Plan by arithmetic over a full read** (`plan_ripple`).  An item
     that straddles the cut is EXTENDED; one that starts at or after it
     is SHIFTED.  Every row, including audio.  Never a list of names:
     the spike's predecessor left V4 out of its plan and measured
     "13 frames of caption desync" as an API defect.
  3. **Capture** (`capture_item`) every item that changes.
  4. **Delete everything that changes, in ONE call, before placing
     anything** (`delete_all`).  This is the whole defence against the
     silent drop: `AppendToTimeline` will not place over a live item,
     returns a truthy list of zombie handles, and places nothing.
  5. **Place in ONE call, in increasing record order** (`place_all`),
     with `endFrame = left + duration` - `endFrame` is EXCLUSIVE, and
     passing `left + duration - 1` yields an item one frame short.
  6. **Verify by re-reading the track** (`verify_placement`), never by
     the return value.  The return value of a colliding append carries
     no information at all.
  7. **Restore** (`restore_item`): the 32 transform properties, the
     Fusion comp, **the comp's media window**, the colour grade
     (`CopyGrades`) and the A/V link - then **re-derive** every comp on
     a clip whose PLAYED LENGTH changed, through the builder
     (`rederive_comps`).

── Step 7's second half, and why this module withholds an artefact ────

**A per-clip Fusion comp is keyed to the window of footage the item
plays, so a trim invalidates it.**  Reel 01's head clip carries a
push-in as a 480-key `BezierSpline` over comp frames 0..479 - and
`fusion/played_window.py` states the law the comp is written under:
comp frame 0 is the clip's FIRST PLAYED FRAME.  Extend the item to 492
frames and the captured spline finishes 13 frames early and holds.
Measured: restoring the captured comp verbatim across that trim is
wrong on **489 of 492 frames** (mean 1.09/255, max 88) on a
cross-render floor of exactly 0.0000.  Nothing in the timeline's
readable state says so.  It looks right.

So capture-and-restore is the WRONG MODEL for the comp, and the captain
attached that as a condition: a clip whose played length changes must
have its comp **re-derived through the builder**, never restored from
the capture, and a composed edit that changes a played length and
cannot reach the comp generator must **REFUSE**, not restore the old
comp.

This module makes that refusal structural rather than conventional, in
four places, and the honest boundary of "structural" is named at the
end:

  a. `ItemCapture.__post_init__` REFUSES to hold a restorable comp for
     a change whose played length differs.  A hand-built capture that
     claims one raises `CompRestoreRefused` at construction.
  b. `capture_item` never exports such a comp to the restore directory
     at all.  It goes to `withheld_dir` instead, under a name the
     restore path never reads, so there is no artefact for the restore
     to find.  Diagnosis keeps it; the restore cannot reach it.
  c. `apply_composed_edit` calls `assert_rederivation_reachable` BEFORE
     step 3 - before anything is captured and long before anything is
     deleted - so an edit with no route to the comp generator refuses
     while the timeline is still whole.
  d. After the pass, `assert_rederived` re-reads every length-changed
     item and raises unless it carries a comp whose media window covers
     its NEW played length.  A generator that declined, crashed or
     never reached the clip is caught by state, not by its own report.

**Where "structural" stops.**  Inside this module a composed edit
cannot restore a stale comp: the data model will not hold one, the
artefact is not written where the restore looks, and the orchestrator
refuses before it destroys anything.  What no module can prevent is a
caller that does not use this module - a fresh script holding Resolve
handles can always call `ImportFusionComp` itself.  That is the same
boundary AGENTS.md 15 draws around `reel_read` ("do not write a new
probe"), and it is enforced the same way: by a test that fails when
library code reaches for the destructive call outside the modules that
own it (`tests/test_composed_edit_refusal.py`).

── What a composed edit cannot carry across ───────────────────────────

- `GetUniqueId()` cannot survive: a re-placed item is a new object.  Any
  record keyed by timeline-item id is invalidated by a composed edit.
- The link GROUP is reconstructed from (track, record frame), not
  restored: `GetLinkedItems()` returns opaque handles.
- The composition is NOT atomic.  Between step 4 and step 5 the
  timeline is missing every item that changes.  That is why step 1 is
  not optional: the edit runs on a staging copy and the approved reel
  is only ever replaced through `reel_replace_guard`.

Resolve is a single instance and other lanes drive it.  This module
holds no Resolve import: it takes live handles as arguments, so it is
driven under test by a fake (`tests/test_composed_edit.py`) and under
`AGENTS.md 5`'s process rule by whatever opened the project.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from library.tools import comp_media_window, reel_read

# ── Refusals ────────────────────────────────────────────────────────
#
# Every one of these stops the edit by name.  None of them is a warning:
# a composed edit that continues past any of them produces a reel that
# looks right and is wrong, which is the outcome this whole line of work
# exists to avoid.


class ComposedEditError(RuntimeError):
    """A composed edit that refused. Fail closed, always by name."""


class StagingNotConformed(ComposedEditError):
    """The staging copy's comp windows do not match the reel it copies."""


class SourceHeadroomExhausted(ComposedEditError):
    """An item was asked to play frames its source file does not have."""


class PlacementNotVerified(ComposedEditError):
    """Re-reading the track did not find what the place asked for."""


class RestoreNotVerified(ComposedEditError):
    """A restored value did not read back as the value that was set."""


class CompRestoreRefused(ComposedEditError):
    """A capture tried to carry a comp across a played-length change.

    The condition the captain attached to this path. See the module
    docstring: restoring a captured comp across a trim measured wrong on
    489 of 492 frames, silently.
    """


class InsertionUndeclared(ComposedEditError):
    """A new item was placed with no statement of how it is treated.

    A placed item comes back at IDENTITY. An ending appended beside
    three punched-in shots, with nothing said about its transform,
    renders at a different framing from everything around it - and
    renders, and looks like a choice. The engine may not invent one
    (AGENTS.md 10.5), so the caller DECLARES it, and declaring
    `properties={}` is how identity is chosen on purpose.
    """


class CompRederivationUnreachable(ComposedEditError):
    """A played length changes and the comp generator cannot be reached.

    Raised BEFORE anything is captured or deleted, so the timeline is
    still whole when the edit refuses.
    """


class CompRederivationNotProven(ComposedEditError):
    """The comp pass ran and the timeline does not show its result."""


# ── Step 2's vocabulary ─────────────────────────────────────────────

EXTEND = "extend"
SHIFT = "shift"
INSERT = "insert"

#: `AppendClipInfo.endFrame` is EXCLUSIVE.  Named rather than inlined
#: because passing `left + duration - 1` is the two-black-frames defect
#: the prior spike attributed to the API, and it is a caller bug.
END_FRAME_IS_EXCLUSIVE = True


def row_label(track_type: str, track_index: int) -> str:
    """`V1` / `A1` - the row name the SOP uses (`docs/TIMELINE_SOP.md`)."""
    return ("V" if str(track_type).lower().startswith("v") else "A") + str(
        int(track_index))


@dataclass(frozen=True)
class ItemChange:
    """One item the edit moves, and where it must end up.

    `item_index` is the item's position in its row, which is its
    identity across the delete: a re-placed item is a NEW object with a
    new `GetUniqueId()`, so nothing downstream may key on the id.
    """

    track_type: str
    track_index: int
    item_index: int
    record_frame: int
    duration: int
    left_offset: int
    previous_record: int
    previous_duration: int
    how: str
    comp_count: int = 0
    right_offset: Optional[int] = None
    name: str = ""

    @property
    def row(self) -> str:
        return row_label(self.track_type, self.track_index)

    @property
    def played_length_changes(self) -> bool:
        """Whether this item plays a different number of frames.

        The one predicate step 7 turns on.  Played length IS the item's
        duration: `played_window` renders the comp over `0 .. played-1`.
        """
        return int(self.duration) != int(self.previous_duration)

    @property
    def moves(self) -> bool:
        return (int(self.record_frame) != int(self.previous_record)
                or self.played_length_changes)


@dataclass(frozen=True)
class Insertion:
    """A genuinely new item - the new ending of an ending change.

    Carries its own media pool item because nothing on the timeline
    supplies one.
    """

    track_type: str
    track_index: int
    media_pool_item: Any
    left_offset: int
    duration: int
    record_frame: int
    name: str = ""
    #: How this item is treated. `None` means UNDECLARED and refuses;
    #: `{}` means identity, chosen. There is no third state and no
    #: default, because a default here is the engine choosing a framing.
    properties: Optional[Mapping[str, Any]] = None
    #: A live item to copy the colour grade from, or None for no grade.
    grade_from: Any = None

    @property
    def row(self) -> str:
        return row_label(self.track_type, self.track_index)


# ── Step 1: stage, and conform the copy's comp windows ──────────────


def read_windows(items_by_row: Mapping[str, Sequence[Any]]) -> dict:
    """`{(row, item_index, comp_index): window}` for every comp present.

    A window of `None` means no comp or no `MediaIn`: an ABSENCE, and it
    is reported as one rather than compared.
    """
    windows = {}
    for row, items in sorted(items_by_row.items()):
        for item_index, item in enumerate(items):
            try:
                count = int(item.GetFusionCompCount() or 0)
            except Exception:  # noqa: BLE001 - a handle that will not answer
                count = 0
            for comp_index in range(1, count + 1):
                windows[(row, item_index, comp_index)] = (
                    comp_media_window.read_window(item, comp_index))
    return windows


#: The four terms that decide WHICH FRAMES a comp reads.  `GlobalIn` /
#: `GlobalOut` are the comp frames the node is valid over and
#: `ClipTimeStart` / `ClipTimeEnd` are the source frames it maps them
#: to; these are the window, and a repair that moves one without the
#: others is a different picture rather than a fixed one.
WINDOW_FRAME_TERMS = ("GlobalIn", "GlobalOut", "ClipTimeStart",
                      "ClipTimeEnd")

#: The three terms that decide WHAT the comp is bound to.  A re-import
#: rebinds these from the timeline to the pool clip and there is nothing
#: wrong with that - `comp_media_window`'s repair record measured it as
#: the only change beside the window itself, on reels that then rendered
#: their endings Complete.  So they are RECORDED by the conform and are
#: not its verdict.
WINDOW_BINDING_TERMS = ("MediaSource", "MediaID", "AudioTrack")


def _frames_of(window: Optional[Mapping]) -> Optional[dict]:
    if window is None:
        return None
    return {name: window.get(name) for name in WINDOW_FRAME_TERMS}


def apply_window(item: Any, comp_index: int,
                 want: Mapping[str, Any]) -> tuple:
    """Put a comp's media window back, writing the LEAST that works.

    ── The measurement this function exists for ────────────────────────

    DaVinci Resolve Studio 21.1, 2026-09-12, on a duplicate of a lab reel
    whose V1 head clip plays source frames 3151..3270.  The copy came
    back `MediaPool`-bound at `GlobalIn 1 / ClipTimeStart 0`; the source
    reads `Timeline`-bound at `GlobalIn -3151 / ClipTimeStart -3151`.

        set MediaSource='Timeline', MediaID='', AudioTrack='Timeline Audio'
        read back  GlobalIn -3151  GlobalOut 2248
                   ClipTimeStart -3151  ClipTimeEnd 2248   <- exact
        then set the four frame terms to those same values
        read back  GlobalIn -3150  GlobalOut 2248
                   ClipTimeStart -3151  ClipTimeEnd 2247   <- OFF BY ONE

    **The window is DERIVED from the binding.**  With `MediaSource` set
    back to `Timeline`, Resolve recomputes the window from the item and
    it is already right; writing the frame terms on top of a correct
    window moves `GlobalIn` one frame LATER and `ClipTimeEnd` one frame
    EARLIER.  Every `SetInput` above returned `None`, the successes and
    the corruption alike, so only the read-back distinguishes them.

    A `GlobalIn` one frame late is exactly the `1 / 19` that stopped six
    of the captain's eight reels from rendering their own endings
    (`comp_media_window`), which makes a window-writing conform a
    candidate cause of that incident rather than a repair for it.

    So: write the binding, RE-READ, and write the frame terms only if
    the window is still wrong.  Returns `(window_after, receipt)`.
    """
    tool = comp_media_window.media_in_tool(item, comp_index)
    if tool is None:
        return None, {"wrote": [], "why": "no MediaIn"}
    receipt = {"wrote": list(WINDOW_BINDING_TERMS)}
    for name in WINDOW_BINDING_TERMS:
        tool.SetInput(name, want[name])
    after = comp_media_window.read_window(item, comp_index)
    if _frames_of(after) == _frames_of(want):
        receipt["derived_from_binding"] = True
        return after, receipt
    receipt["derived_from_binding"] = False
    receipt["wrote"] += list(WINDOW_FRAME_TERMS)
    for name in WINDOW_FRAME_TERMS:
        tool.SetInput(name, want[name])
    return comp_media_window.read_window(item, comp_index), receipt


def drop_passthrough_comps(rows: Mapping[str, Sequence[Any]]) -> list:
    """Remove every composition on the copy that draws nothing.

    Resolve gives a timeline item an auto-created composition -
    `MediaIn -> MediaOut` plus the two `AudioDisplay` nodes, no tool in
    between - and it has a media window like any other.  Measured
    2026-09-12 on a copy of a lab reel:

      - that window will NOT take a write: setting `GlobalIn` to 0 left
        it at 1, and setting `GlobalOut` did nothing, every `SetInput`
        returning `None` as it does when it works;
      - and it is not stable between reads - the same handle answered a
        matching window and then a differing one with no write in
        between, which is the same instability `comp_media_window`
        recorded on the captain's reels ("a cached window is never
        ground truth").

    So it cannot be conformed and it cannot be trusted, and it draws no
    picture.  It is REMOVED from the staging copy, and the proof that
    this changes nothing is the byte comparison against a rebuild: both
    composed cases render identical to a rebuild of the same edit whose
    items still carry theirs.

    Only a composition with no tool beyond the passthrough set goes. A
    graph that could not be READ stays - an unreadable comp is not
    evidence of an empty one.
    """
    emptied = []
    for row, items in sorted(rows.items()):
        for item_index, item in enumerate(items):
            count = int(_read(item, "GetFusionCompCount", 0) or 0)
            if not count:
                continue
            draws = [reel_read.comp_draws_something(
                {"tools": reel_read._comp_tools(item, index)})
                for index in range(1, count + 1)]
            if any(value is None or value for value in draws):
                continue
            for name in (item.GetFusionCompNameList() or []):
                item.DeleteFusionCompByName(name)
            emptied.append({"where": [row, item_index],
                            "comps_removed": count,
                            "comps_after": int(
                                _read(item, "GetFusionCompCount", 0) or 0)})
    return emptied


def conform_comp_windows(source_rows: Mapping[str, Sequence[Any]],
                         staged_rows: Mapping[str, Sequence[Any]],
                         comp_dir: Optional[str] = None) -> dict:
    """Step 1 - make the staging copy's media windows the source's.

    Measured (`comp_media_window`, and again in this lab): copying a
    timeline does not reliably reproduce every `MediaIn` window, and
    nothing in the timeline's readable structure differs - the only
    symptom is a render that differs, or fails.  So a copy used as the
    edit's staging, or as a control to judge it against, is conformed
    and VERIFIED before it is trusted.

    Two repairs, in the order that damages least, because ONE OF THEM
    DOES NOT ALWAYS WORK and which one depends on the direction:

      1. `SetInput` the four frame terms.  Cheap, and it leaves the comp
         alone - but Resolve lets a window be pushed LATER and will not
         always let it be pulled EARLIER.  Measured here: a copy whose
         `GlobalIn` read -3150 against the source's -3151 would not take
         the write, and `SetInput` returned `None` exactly as it does
         when it succeeds.
      2. Re-import the SOURCE item's own exported comp, then set the
         window on the fresh graph.  This is `comp_media_window`'s
         measured repair and it is the one that works on a window that
         will not move.  It needs somewhere to write the export, which
         is what `comp_dir` is; without one only repair 1 is available.

    A FRESH RE-READ is the verdict after each, never a return value.
    A window that will not conform RAISES rather than being reported as
    repaired.
    """
    receipt = {"repaired": [], "absent": [], "rebound": [],
               "emptied": drop_passthrough_comps(staged_rows)}
    # A comp that DRAWS NOTHING is not compared, because it has just
    # been removed from the copy. `drop_passthrough_comps` says why.
    source = {key: window for key, window in read_windows(source_rows).items()
              if reel_read.comp_draws_something(
                  {"tools": reel_read._comp_tools(
                      (source_rows.get(key[0]) or [None] * (key[1] + 1))[key[1]],
                      key[2])}) is not False}
    receipt["compared"] = len(source)

    for key, want in sorted(source.items()):
        row, item_index, comp_index = key
        if want is None:
            receipt["absent"].append({"where": list(key),
                                      "why": "source carries no MediaIn"})
            continue
        items = staged_rows.get(row) or []
        if item_index >= len(items):
            raise StagingNotConformed(
                f"REFUSING to stage: the copy has no item at "
                f"{row}[{item_index}] where the reel it copies carries a "
                f"comp. The copy is not the reel.")
        item = items[item_index]
        got = comp_media_window.read_window(item, comp_index)
        if _frames_of(got) == _frames_of(want):
            continue
        entry = {"where": list(key), "was": _frames_of(got),
                 "wanted": _frames_of(want), "how": None}

        after, wrote = apply_window(item, comp_index, want)
        entry["wrote"] = wrote
        if _frames_of(after) == _frames_of(want):
            entry["how"] = "SetInput"
            receipt["repaired"].append(entry)
            continue

        if comp_dir is None:
            raise StagingNotConformed(
                f"REFUSING to edit this staging copy: {row}[{item_index}] "
                f"comp {comp_index} reads {_frames_of(got)} where the reel "
                f"it copies reads {_frames_of(want)}, `SetInput` did not "
                f"move it, and no `comp_dir` was given for the re-import "
                f"that does. Resolve lets a window be pushed later and "
                f"not always pulled back.")

        os.makedirs(comp_dir, exist_ok=True)
        exported = os.path.join(
            comp_dir, f"conform_{row}_{item_index}_c{comp_index}.comp")
        source_item = (source_rows.get(row) or [])[item_index]
        source_item.ExportFusionComp(exported, comp_index)
        for name in (item.GetFusionCompNameList() or []):
            item.DeleteFusionCompByName(name)
        item.ImportFusionComp(exported)
        _after, wrote = apply_window(item, comp_index, want)
        entry["how"] = "re-import"
        entry["wrote_after_import"] = wrote
        entry["from"] = exported
        receipt["repaired"].append(entry)

    staged_after = read_windows(staged_rows)
    # A comp the conform DELETED has no window to compare, and that is
    # the outcome it wanted - it is not a residual difference.
    still = {str(key): [_frames_of(source[key]),
                        _frames_of(staged_after.get(key))]
             for key in source
             if source[key] is not None
             and _frames_of(staged_after.get(key)) != _frames_of(source[key])}
    receipt["rebound"] = [
        {"where": list(key),
         "source": {n: source[key].get(n) for n in WINDOW_BINDING_TERMS},
         "staged": {n: (staged_after.get(key) or {}).get(n)
                    for n in WINDOW_BINDING_TERMS}}
        for key in sorted(source)
        if source[key] is not None and staged_after.get(key) is not None
        and any(source[key].get(n) != staged_after[key].get(n)
                for n in WINDOW_BINDING_TERMS)]
    receipt["after"] = {str(k): v for k, v in sorted(staged_after.items())}
    if still:
        raise StagingNotConformed(
            f"REFUSING to edit this staging copy: {len(still)} comp media "
            f"window(s) still differ from the reel it copies after both "
            f"repairs, and the only symptom of that is a render that "
            f"differs or fails: {still}")
    return receipt


# ── Step 2: plan by arithmetic over a full read ─────────────────────


def treatment_comps(clip: Mapping) -> int:
    """How many of this clip's comps DRAW something.

    Measured 2026-09-12: a plain timeline item that nothing has treated
    reports `GetFusionCompCount() == 1` for Resolve's own empty
    composition - `MediaIn -> MediaOut` plus the two `AudioDisplay`
    nodes and no tool in between. Counting that as a treatment makes
    every overlay row look like a clip whose comp must be re-derived,
    and the edit then refuses work that is perfectly safe - a gate that
    fails correct output, which this repository holds to be no better
    than one that cannot fail (AGENTS.md 10.4).

    An UNREADABLE graph counts as a treatment. It is not evidence of an
    empty one, and the direction to be wrong in is the one that refuses.
    """
    rows = (clip.get("fusion") or {}).get("media_windows") or []
    if not rows:
        # No per-comp detail at all: fall back to the count, which is
        # the conservative reading.
        return int((clip.get("fusion") or {}).get("comp_count") or 0)
    count = 0
    for row in rows:
        draws = reel_read.comp_draws_something(row)
        if draws is None or draws:
            count += 1
    return count


def plan_ripple(tracks: Sequence[Mapping], cut_frame: int,
                delta_frames: int,
                exclude: Sequence = ()) -> list:
    """Step 2 - every item that must move, computed, never listed.

    `tracks` is `reel_read.read_tracks` output: every row, including
    audio.  The arithmetic is the whole plan:

        record_in <  cut <= record_out   ->  EXTEND by delta
        record_in >= cut                 ->  SHIFT  by delta

    and nothing else moves.  `cut_frame` is a record frame in Resolve's
    own space, where `record_out` is EXCLUSIVE - so an item that ends
    exactly at the cut straddles it and is extended, which is what
    carries a row out to a new ending.

    `exclude` names `(row, item_index)` pairs the caller has decided not
    to move - the ending case excludes the freeze hold, which has no
    source headroom to be lengthened into.  Excluding is the caller's
    declaration; it is never inferred from a refusal.

    Raises `SourceHeadroomExhausted` naming EVERY row that cannot supply
    the frames its extension asks for, rather than placing a hole.
    """
    cut_frame = int(cut_frame)
    delta_frames = int(delta_frames)
    excluded = {(str(r), int(i)) for r, i in exclude}
    changes, starved = [], []

    for track in tracks:
        track_type = track["type"]
        track_index = int(track["index"])
        row = row_label(track_type, track_index)
        for item_index, clip in enumerate(track.get("clips", [])):
            if (row, item_index) in excluded:
                continue
            start = int(clip["record_in"])
            end = int(clip["record_out"])
            duration = int(clip["duration"])
            if start < cut_frame <= end:
                how, record, new_duration = EXTEND, start, duration + delta_frames
            elif start >= cut_frame:
                how, record, new_duration = (
                    SHIFT, start + delta_frames, duration)
            else:
                continue
            right = clip.get("right_offset")
            if (how == EXTEND and delta_frames > 0
                    and right is not None and int(right) < delta_frames):
                starved.append(
                    {"row": row, "item_index": item_index,
                     "name": clip.get("name", ""),
                     "headroom_frames": int(right),
                     "needs_frames": delta_frames})
                continue
            changes.append(ItemChange(
                track_type=track_type, track_index=track_index,
                item_index=item_index, record_frame=record,
                duration=new_duration, left_offset=int(clip["left_offset"] or 0),
                previous_record=start, previous_duration=duration, how=how,
                comp_count=treatment_comps(clip),
                right_offset=None if right is None else int(right),
                name=clip.get("name", "")))

    if starved:
        raise SourceHeadroomExhausted(
            f"REFUSING to plan: {len(starved)} item(s) were asked to play "
            f"frames their source file does not have, and extending them "
            f"would place a hole rather than picture: {starved}. A longer "
            f"hold needs the asset re-rendered, which is a file operation "
            f"and not a timeline operation - or name it in `exclude` and "
            f"say what covers those frames instead.")
    return changes


def post_edit_spans(tracks: Sequence[Mapping],
                    changes: Sequence[ItemChange],
                    insertions: Sequence[Insertion] = (),
                    row: str = "V1") -> list:
    """Where every item on `row` will be AFTER the edit, in order.

    Computed over the FULL read, not over the plan: an item the plan
    does not touch still occupies frames, and a continuity check that
    saw only the moved items would call a row continuous because it
    could not see the hole.
    """
    moved = {(c.row, c.item_index): c for c in changes}
    spans = []
    for track in tracks:
        if row_label(track["type"], int(track["index"])) != row:
            continue
        for item_index, clip in enumerate(track.get("clips", [])):
            change = moved.get((row, item_index))
            if change is not None:
                start, duration = int(change.record_frame), int(change.duration)
            else:
                start, duration = int(clip["record_in"]), int(clip["duration"])
            spans.append((start, start + duration))
    spans += [(int(i.record_frame), int(i.record_frame) + int(i.duration))
              for i in insertions if i.row == row]
    return sorted(spans)


def assert_every_frame_covered(tracks: Sequence[Mapping],
                               changes: Sequence[ItemChange],
                               insertions: Sequence[Insertion] = (),
                               row: str = "V1") -> list:
    """The picture row must gain no gap and no overlap from the edit.

    AGENTS.md 10.2: every frame of the timeline must show a clip.  The
    spike's predecessor left two black frames behind by passing an
    inclusive `endFrame`; this is the arithmetic that catches that class
    BEFORE the delete, while the timeline is still whole.

    Judged on what the edit CHANGES: a gap or overlap already present
    before the edit is the approved build's shape - a V2 cutaway over a
    V1 hole, for example - and is not this edit's defect, so it does not
    refuse.  A gap or overlap the post-edit plan introduces that was not
    there before still refuses exactly as it always has.
    """
    spans = post_edit_spans(tracks, changes, insertions, row)
    before = post_edit_spans(tracks, (), (), row)
    gaps = [[a[1], b[0]] for a, b in zip(spans, spans[1:]) if b[0] > a[1]]
    overlaps = [[b[0], a[1]] for a, b in zip(spans, spans[1:]) if b[0] < a[1]]
    before_gaps = [[a[1], b[0]] for a, b in zip(before, before[1:])
                   if b[0] > a[1]]
    before_overlaps = [[b[0], a[1]] for a, b in zip(before, before[1:])
                       if b[0] < a[1]]
    new_gaps = [gap for gap in gaps if gap not in before_gaps]
    new_overlaps = [overlap for overlap in overlaps
                    if overlap not in before_overlaps]
    if new_gaps or new_overlaps:
        raise PlacementNotVerified(
            f"REFUSING to place: the plan leaves {len(new_gaps)} new "
            f"gap(s) ({new_gaps}) and {len(new_overlaps)} new overlap(s) "
            f"({new_overlaps}) on {row} "
            f"(before: {len(before_gaps)} gap(s), "
            f"{len(before_overlaps)} overlap(s)). Every frame of the "
            f"timeline must show a clip - Resolve draws nothing in a gap "
            f"and the reel delivers black - and `AppendToTimeline` "
            f"silently places NOTHING where it would collide with a live "
            f"item.")
    return spans


def assert_insertions_declared(tracks: Sequence[Mapping],
                               insertions: Sequence[Insertion]) -> list:
    """Every new item says how it is treated, before anything is deleted.

    Measured: a 24-frame ending appended beside three shots carrying
    `ZoomX 2.307` rendered at `ZoomX 1.0` and diverged from a rebuild of
    the same edit on 100% of its own frames, at mean 21.8/255 - while
    every other frame of the reel was byte-identical. The item placed
    fine, verified fine and looked like a wide shot.
    """
    undeclared = []
    for insertion in insertions:
        if insertion.properties is not None:
            continue
        # The nearest clip in front of it on the same row, WHOLE: the
        # caller has to be able to see what identity would look like
        # beside it, and a trimmed transform hides the terms that matter.
        before = None
        for track in tracks:
            if row_label(track["type"], int(track["index"])) != insertion.row:
                continue
            for clip in track.get("clips", []):
                if int(clip["record_out"]) <= int(insertion.record_frame):
                    if before is None or (int(clip["record_out"])
                                          > int(before["record_out"])):
                        before = clip
        undeclared.append({
            "row": insertion.row, "record_frame": insertion.record_frame,
            "name": insertion.name,
            "the_clip_in_front": None if before is None else before["name"],
            "and_it_carries": None if before is None
            else dict(before.get("transform") or {})})
    if undeclared:
        raise InsertionUndeclared(
            f"REFUSING before anything is deleted: {len(undeclared)} new "
            f"item(s) declare no treatment, and a placed item comes back "
            f"at IDENTITY - so it would render at a framing nobody chose, "
            f"beside neighbours that carry one: {undeclared}. Declare "
            f"`properties` on the Insertion (`{{}}` if identity is what "
            f"is wanted), and `grade_from` if it takes a grade.")
    return list(insertions)


# ── Step 3: capture ─────────────────────────────────────────────────


@dataclass(frozen=True)
class CapturedComp:
    """One exported Fusion comp, and the media window it was read with."""

    index: int
    path: str
    window: Optional[Mapping[str, Any]]
    bytes: Optional[int] = None
    seconds: Optional[float] = None


@dataclass(frozen=True)
class ItemCapture:
    """Everything needed to put one item back - minus what must not be.

    `restorable_comps` is EMPTY whenever the change alters the played
    length, and `__post_init__` refuses a capture that claims otherwise.
    This is the first of the four places the captain's condition is made
    structural (module docstring): the restore cannot put back a comp
    the data model will not carry.
    """

    change: ItemChange
    properties: Mapping[str, Any]
    geometry: Mapping[str, Any]
    media_pool_item: Any
    restorable_comps: tuple = ()
    #: Read by NOTHING in the restore path. Kept so a composed edit that
    #: went wrong can be diagnosed against the comp that was there.
    withheld_comps: tuple = ()
    node_count: Optional[int] = None

    def __post_init__(self):
        if self.change.played_length_changes and self.restorable_comps:
            raise CompRestoreRefused(
                f"REFUSING: {self.change.row}[{self.change.item_index}] "
                f"plays {self.change.previous_duration} -> "
                f"{self.change.duration} frames and this capture carries "
                f"{len(self.restorable_comps)} comp(s) to restore. A "
                f"per-clip comp is keyed to the window of footage the item "
                f"plays, so this trim invalidates it: restoring a captured "
                f"comp across a length change measured wrong on 489 of 492 "
                f"frames, silently. The comp is RE-DERIVED through the "
                f"builder or the edit refuses.")


def capture_item(item: Any, change: ItemChange, comp_dir: str,
                 withheld_dir: str) -> ItemCapture:
    """Step 3 - everything that will be destroyed by the delete.

    Comps land on disk.  A comp belonging to an item whose played length
    changes lands in `withheld_dir` instead of `comp_dir`, under a name
    the restore path never builds - the second of the four structural
    places.  There is nothing for the restore to find.
    """
    os.makedirs(comp_dir, exist_ok=True)
    os.makedirs(withheld_dir, exist_ok=True)
    rederive = change.played_length_changes
    target_dir = withheld_dir if rederive else comp_dir
    tag = f"{change.row}_{change.item_index}"

    comps = []
    try:
        count = int(item.GetFusionCompCount() or 0)
    except Exception:  # noqa: BLE001
        count = 0
    for comp_index in range(1, count + 1):
        suffix = "_WITHHELD_stale_across_trim" if rederive else ""
        path = os.path.join(target_dir, f"{tag}_c{comp_index}{suffix}.comp")
        started = time.time()
        item.ExportFusionComp(path, comp_index)
        comps.append(CapturedComp(
            index=comp_index, path=path,
            window=comp_media_window.read_window(item, comp_index),
            bytes=os.path.getsize(path) if os.path.exists(path) else None,
            seconds=round(time.time() - started, 3)))

    properties = item.GetProperty()
    if not isinstance(properties, dict):
        properties = {}
    return ItemCapture(
        change=change,
        properties={key: properties[key] for key in sorted(properties)},
        geometry={"name": _read(item, "GetName", ""),
                  "start": _read(item, "GetStart", None),
                  "duration": _read(item, "GetDuration", None),
                  "left_offset": _read(item, "GetLeftOffset", None),
                  "right_offset": _read(item, "GetRightOffset", None)},
        media_pool_item=item.GetMediaPoolItem(),
        restorable_comps=() if rederive else tuple(comps),
        withheld_comps=tuple(comps) if rederive else (),
        node_count=_read(item, "GetNumNodes", None))


def _read(obj, name, default):
    fn = getattr(obj, name, None)
    if fn is None:
        return default
    try:
        value = fn()
    except Exception:  # noqa: BLE001 - a handle that will not answer
        return default
    return default if value is None else value


# ── Step 4: delete everything that changes, in one call ─────────────


def delete_all(timeline, items: Sequence[Any]) -> dict:
    """Step 4 - ONE `DeleteClips`, before anything is placed.

    The whole defence against the silent drop.  `AppendToTimeline` will
    not place over a live item: it returns a truthy list containing a
    handle that answers getters and is on no track, and places nothing.
    A composition that never asks it to collide is unaffected by that,
    and ordering is the only thing that guarantees it.
    """
    started = time.time()
    returned = timeline.DeleteClips(list(items), False)
    return {"asked": len(items), "returned": returned,
            "seconds": round(time.time() - started, 3)}


# ── Step 5: place in one call, in increasing record order ───────────


def clip_info(media_pool_item: Any, left_offset: int, duration: int,
              track_index: int, record_frame: int,
              media_type: int = 1) -> dict:
    """One `AppendClipInfo`.

    `endFrame` is EXCLUSIVE: `left + duration`.  Passing
    `left + duration - 1` places an item one frame short, which is the
    prior spike's "two black frames" and is a caller bug, not the API.
    """
    return {"mediaPoolItem": media_pool_item,
            "startFrame": int(left_offset),
            "endFrame": int(left_offset) + int(duration),
            "trackIndex": int(track_index),
            "recordFrame": int(record_frame),
            "mediaType": int(media_type)}


def placement_order(captures: Sequence[ItemCapture],
                    insertions: Sequence[Insertion] = ()) -> list:
    """Everything to place, in increasing record order, as one list.

    Returns `[(record_frame, track_index, media_type, info, owner), ...]`
    already sorted.  Sorting here rather than at the call site is the
    point: the step is "place in ONE call, in increasing record order",
    and a caller that builds its own order can get one of the two wrong.
    """
    rows = []
    for capture in captures:
        change = capture.change
        rows.append((int(change.record_frame), int(change.track_index),
                     1 if change.track_type == "video" else 2,
                     clip_info(capture.media_pool_item, change.left_offset,
                               change.duration, change.track_index,
                               change.record_frame,
                               1 if change.track_type == "video" else 2),
                     capture))
    for insertion in insertions:
        rows.append((int(insertion.record_frame), int(insertion.track_index),
                     1 if insertion.track_type == "video" else 2,
                     clip_info(insertion.media_pool_item,
                               insertion.left_offset, insertion.duration,
                               insertion.track_index, insertion.record_frame,
                               1 if insertion.track_type == "video" else 2),
                     insertion))
    return sorted(rows, key=lambda row: (row[0], row[1]))


def place_all(media_pool, ordered: Sequence) -> dict:
    """Step 5 - ONE `AppendToTimeline`.

    The return value is recorded and is NOT the verdict: step 6 is.
    """
    started = time.time()
    returned = media_pool.AppendToTimeline([row[3] for row in ordered])
    return {"asked": len(ordered),
            "returned_truthy": bool(returned),
            "returned_count": len(returned or []),
            "seconds": round(time.time() - started, 3)}


# ── Step 6: verify by re-reading the track ──────────────────────────


def verify_placement(live_rows: Mapping[str, Sequence[Any]],
                     ordered: Sequence) -> dict:
    """Step 6 - every expected item is really there, at its own frame.

    Judged by a RE-READ of the track: Resolve's append returns a truthy
    list of live-looking handles whether it placed anything or not, so
    the return value is not evidence and is never consulted here.

    Returns `{(row, record_frame): live item}` for the restore to use,
    and raises `PlacementNotVerified` naming everything that is missing
    or the wrong length.
    """
    landed, missing = {}, []
    for record_frame, track_index, media_type, _info, owner in ordered:
        row = row_label("video" if media_type == 1 else "audio", track_index)
        wanted = int(_info["endFrame"]) - int(_info["startFrame"])
        hits = [item for item in (live_rows.get(row) or [])
                if _read(item, "GetStart", None) == record_frame]
        if len(hits) != 1 or _read(hits[0], "GetDuration", None) != wanted:
            missing.append({
                "row": row, "record_frame": record_frame,
                "wanted_duration": wanted,
                "found": [{"start": _read(h, "GetStart", None),
                           "duration": _read(h, "GetDuration", None)}
                          for h in hits]})
            continue
        landed[(row, record_frame)] = hits[0]
    if missing:
        raise PlacementNotVerified(
            f"REFUSING to restore: {len(missing)} of {len(ordered)} placed "
            f"item(s) are not on the track at the frame and length they "
            f"were asked for. Resolve's append returned a live-looking "
            f"handle and the track disagrees, so the track wins: "
            f"{missing}")
    return landed


# ── Step 7: restore, then re-derive ─────────────────────────────────

#: Properties Resolve reports and will not take back. `GetProperty()`
#: returns the whole dict including read-only members; setting one is
#: not a failure to report, it is a key that was never ours to set.
READ_ONLY_PROPERTIES = ("Frames", "FPS", "Resolution")


def set_properties(item: Any, properties: Mapping[str, Any]) -> dict:
    """Write the transform and READ IT BACK. The return value lies.

    `SetProperty` on `AnchorPointX`/`AnchorPointY` returns `False` while
    the value it set is correct, so the write is judged by a re-read on
    the item and nothing else. Returns `{key: [wanted, got]}` for every
    key that did not take - empty when they all did.
    """
    for key, value in (properties or {}).items():
        if key in READ_ONLY_PROPERTIES or value is None:
            continue
        if isinstance(value, str) and value.startswith("<"):
            continue
        item.SetProperty(key, value)
    now = item.GetProperty()
    if not isinstance(now, dict):
        return {}
    diff = {}
    for key, value in (properties or {}).items():
        if key in READ_ONLY_PROPERTIES or value is None:
            continue
        if isinstance(value, str) and value.startswith("<"):
            continue
        if key in now and not _same_value(now[key], value):
            diff[key] = [value, now[key]]
    return diff


#: How far a numeric property may read back from what was written and
#: still be the value written. Resolve stores doubles and hands some back
#: one ulp off (2026-09-25: Pan -8.610478359908884 read back as
#: ...885, refusing two correct post-header swaps); a millionth of a
#: Pan/Tilt unit or a zoom is far below a drawn pixel.
READBACK_TOLERANCE = 1e-6


def _same_value(got: Any, wanted: Any) -> bool:
    numbers = (int, float)
    if (isinstance(got, numbers) and isinstance(wanted, numbers)
            and not isinstance(got, bool) and not isinstance(wanted, bool)):
        return abs(float(got) - float(wanted)) <= READBACK_TOLERANCE
    return got == wanted


def treat_insertion(item: Any, insertion: Insertion) -> dict:
    """Put the DECLARED treatment on a newly placed item, and read back.

    The other half of `assert_insertions_declared`: the declaration is
    checked before the delete and applied here, and both the transform
    and the grade are judged by a re-read.
    """
    receipt = {"row": insertion.row, "record_frame": insertion.record_frame,
               "name": insertion.name,
               "properties": dict(insertion.properties or {})}
    diff = set_properties(item, insertion.properties or {})
    receipt["property_readback_diff"] = diff
    if diff:
        raise RestoreNotVerified(
            f"REFUSING: the new item on {insertion.row} at "
            f"{insertion.record_frame} did not take its declared "
            f"treatment: {diff}. It would render at a framing nobody "
            f"chose.")
    if insertion.grade_from is not None:
        insertion.grade_from.CopyGrades([item])
        wanted = _read(insertion.grade_from, "GetNumNodes", None)
        after = _read(item, "GetNumNodes", None)
        receipt["grade"] = {"nodes_wanted": wanted, "nodes_after": after}
        if wanted is not None and after is not None and after != wanted:
            raise RestoreNotVerified(
                f"REFUSING: the new item on {insertion.row} at "
                f"{insertion.record_frame} carries {after} colour node(s) "
                f"and its reference carries {wanted}.")
    return receipt


def restore_item(item: Any, capture: ItemCapture, *,
                 grade_source: Any = None, timeline: Any = None,
                 link_with: Sequence[Any] = ()) -> dict:
    """Step 7 (first half) - put back everything the delete destroyed.

    Properties, the comp, **the comp's media window**, the grade and the
    A/V link.  Every one is judged by a READ-BACK, never by a return
    value: `SetProperty` on `AnchorPointX` returns `False` while the
    value it set is correct, and `SetInput` returns `None` for every
    media-window field whether it took or not.

    A comp is restored only from `capture.restorable_comps`, which is
    empty by construction when the played length changed.  There is no
    other route to a comp path in this function - the capture's
    `withheld_comps` are never read here.
    """
    receipt = {"row": capture.change.row,
               "item_index": capture.change.item_index,
               "comps": [], "windows": [], "grade": None, "link": None,
               "comp_rederivation_required":
                   capture.change.played_length_changes}
    started = time.time()

    property_diff = set_properties(item, capture.properties or {})

    for comp in capture.restorable_comps:
        if not comp.path or not os.path.exists(comp.path):
            raise RestoreNotVerified(
                f"REFUSING: {capture.change.row}"
                f"[{capture.change.item_index}] captured comp "
                f"{comp.index} is not on disk at {comp.path!r}, so the "
                f"item would be re-placed with no treatment at all.")
        item.ImportFusionComp(comp.path)
        receipt["comps"].append({"index": comp.index, "path": comp.path})
        if comp.window is None:
            continue
        if comp_media_window.media_in_tool(item, comp.index) is None:
            raise RestoreNotVerified(
                f"REFUSING: {capture.change.row}"
                f"[{capture.change.item_index}] comp {comp.index} has no "
                f"MediaIn after import, so its played window cannot be "
                f"put back and the clip would play from source frame 0.")
        readback, wrote = apply_window(item, comp.index, comp.window)
        receipt["windows"].append({"index": comp.index, "wanted": comp.window,
                                   "readback": readback, "wrote": wrote})
        if _frames_of(readback) != _frames_of(comp.window):
            raise RestoreNotVerified(
                f"REFUSING: {capture.change.row}"
                f"[{capture.change.item_index}] comp {comp.index} media "
                f"window read back as {readback} and was set to "
                f"{comp.window}. `ImportFusionComp` re-binds MediaIn to "
                f"the whole pool clip, so an unrestored window plays a "
                f"different moment of the take - the clip renders, and "
                f"it is the wrong footage.")

    if grade_source is not None:
        grade_started = time.time()
        grade_source.CopyGrades([item])
        after = _read(item, "GetNumNodes", None)
        wanted = _read(grade_source, "GetNumNodes", None)
        receipt["grade"] = {"nodes_wanted": wanted, "nodes_after": after,
                            "seconds": round(time.time() - grade_started, 3)}
        if wanted is not None and after is not None and after != wanted:
            raise RestoreNotVerified(
                f"REFUSING: {capture.change.row}"
                f"[{capture.change.item_index}] carries {after} colour "
                f"node(s) after CopyGrades and the reference carries "
                f"{wanted}. A re-placed item comes back with the grade "
                f"stripped, and the difference is visible picture.")

    if timeline is not None and link_with:
        receipt["link"] = timeline.SetClipsLinked(
            [item] + list(link_with), True)

    receipt["property_readback_diff"] = property_diff
    if property_diff:
        raise RestoreNotVerified(
            f"REFUSING: {capture.change.row}"
            f"[{capture.change.item_index}] transform properties did not "
            f"read back as they were set: "
            f"{receipt['property_readback_diff']}. A re-placed item comes "
            f"back at identity, so an unrestored transform is a visible "
            f"framing change - the letterbox the prior spike measured.")
    receipt["seconds"] = round(time.time() - started, 3)
    return receipt


# ── Step 7 (second half): the re-derivation, and its refusal ────────


class CompRederiver:
    """The route from a composed edit into the builder's comp generator.

    Subclasses answer two questions and nothing else: whether the
    generator can be reached at all (`reachable_reason`, asked BEFORE
    anything is destroyed) and what happened when it ran (`rederive`).

    `ReelLookRederiver` below is the real one.  A composed edit
    constructed with `rederiver=None` and a plan that changes a played
    length refuses - it does not fall back.
    """

    def reachable_reason(self, changes: Sequence[ItemChange]) -> Optional[str]:
        """Why the generator cannot be reached, or None if it can."""
        raise NotImplementedError

    def rederive(self, changes: Sequence[ItemChange]) -> dict:
        """Run the generator. Returns a receipt; state is the verdict."""
        raise NotImplementedError

    def expects_comp(self, row: str, record_frame: int) -> Optional[bool]:
        """Whether this generator writes a comp on that row.

        `None` means it will not say, and an unknown is never read as a
        finding: `assert_rederived` skips what it cannot be told about
        rather than refusing correct output.
        """
        return None


@dataclass(frozen=True)
class _Check:
    """One item `assert_rederived` must find on the timeline and judge."""

    row: str
    item_index: int
    record_frame: int
    duration: int


def assert_rederivation_reachable(changes: Sequence[ItemChange],
                                  rederiver: Optional[CompRederiver],
                                  insertions: Sequence[Insertion] = ()) -> dict:
    """The third structural place - asked BEFORE anything is destroyed.

    An edit that changes no played length and inserts nothing needs no
    generator and says so.  One that does, and has none, refuses while
    the timeline is still whole.

    An INSERTION needs the generator as surely as a trim does: a newly
    placed item carries no comp at all, so an inserted ending renders
    with none of the treatment every clip around it has.
    """
    trimmed = [c for c in changes if c.played_length_changes]
    with_comps = [c for c in trimmed if c.comp_count]
    if not trimmed and not insertions:
        return {"required": False, "trimmed": 0, "with_comps": 0,
                "insertions": 0}
    if rederiver is None:
        raise CompRederivationUnreachable(
            f"REFUSING before anything is captured or deleted: "
            f"{len(trimmed)} item(s) change played length, "
            f"{len(insertions)} item(s) are newly placed "
            f"({[f'{c.row}[{c.item_index}] {c.previous_duration}->{c.duration}' for c in trimmed]}) "
            f"and this edit has no route to the comp generator. A "
            f"per-clip comp is keyed to the window of footage the item "
            f"plays: restoring the captured one across a trim measured "
            f"wrong on 489 of 492 frames and looked right. Give the edit "
            f"a CompRederiver, or do not change a played length.")
    reason = rederiver.reachable_reason(changes)
    if reason:
        raise CompRederivationUnreachable(
            f"REFUSING before anything is captured or deleted: "
            f"{len(trimmed)} item(s) change played length and the comp "
            f"generator cannot be reached - {reason}")
    return {"required": True, "trimmed": len(trimmed),
            "with_comps": len(with_comps), "insertions": len(insertions),
            "items": [f"{c.row}[{c.item_index}]" for c in trimmed]}


def assert_rederived(live_rows: Mapping[str, Sequence[Any]],
                     changes: Sequence[ItemChange],
                     receipt: Mapping[str, Any],
                     insertions: Sequence[Insertion] = (),
                     rederiver: Optional["CompRederiver"] = None) -> dict:
    """The fourth structural place - read the timeline, not the report.

    After the pass, every item whose played length changed and which
    carried a comp must carry one again, and that comp's media window
    must cover its NEW played length.  A generator that declined,
    crashed, or simply never reached the clip is caught here by state.
    """
    if not receipt.get("ran"):
        raise CompRederivationNotProven(
            f"the comp generator did not run: {receipt}")
    if receipt.get("ok") is False:
        raise CompRederivationNotProven(
            f"the comp generator ran and failed: {receipt}")
    verdicts, failures = [], []
    checks = [_Check(c.row, c.item_index, c.record_frame, c.duration)
              for c in changes
              if c.played_length_changes and c.comp_count]
    # A newly placed item carries no comp at all, so it is checked
    # wherever the generator SAYS it writes one. A rederiver that will
    # not say is not second-guessed: an unknown is not a finding, and a
    # gate that refused correct output would be no better than one that
    # cannot fail (AGENTS.md 10.4).
    for insertion in insertions:
        expects = (rederiver.expects_comp(insertion.row,
                                          insertion.record_frame)
                   if rederiver is not None else None)
        if expects:
            checks.append(_Check(insertion.row, -1, insertion.record_frame,
                                 insertion.duration))
    for change in checks:
        items = live_rows.get(change.row) or []
        item = next((i for i in items
                     if _read(i, "GetStart", None) == change.record_frame),
                    None)
        if item is None:
            failures.append({"where": f"{change.row}[{change.item_index}]",
                             "why": "no item at its record frame"})
            continue
        count = int(_read(item, "GetFusionCompCount", 0) or 0)
        window = comp_media_window.read_window(item, 1) if count else None
        uncovered = comp_media_window.uncovered_reason(window, change.duration)
        verdicts.append({"where": f"{change.row}[{change.item_index}]",
                         "played_frames": change.duration,
                         "comp_count": count, "window": window})
        if not count:
            failures.append({"where": f"{change.row}[{change.item_index}]",
                             "why": "carries no comp after the pass"})
        elif uncovered:
            failures.append({"where": f"{change.row}[{change.item_index}]",
                             "why": uncovered})
    if failures:
        raise CompRederivationNotProven(
            f"REFUSING to hand this edit on: the comp pass reported "
            f"success and the timeline does not show it - {failures}. A "
            f"clip whose played length changed and whose comp was not "
            f"re-derived renders a treatment keyed to the length it used "
            f"to play, silently.")
    return {"checked": len(verdicts), "items": verdicts}


class ReelLookRederiver(CompRederiver):
    """`reel_look.apply_comps` - the builder's own comp pass, by name.

    The pass reads each clip's played length off the LIVE timeline item
    (`apply_fusion_comps`: `played = tl_clip.GetDuration()`), so running
    it after the composed edit keys every comp to the length the item
    NOW plays.  That is the whole reason the re-derivation is a route
    into the builder rather than arithmetic on the capture.

    `manifest` must describe the timeline the edit PRODUCED, not the one
    it started from: the pass maps manifest clip specs to timeline items
    by position along the row, so a manifest with a different number of
    clips on a comp-bearing row would write comps onto the wrong clips.
    `reachable_reason` refuses that before anything is destroyed.
    """

    #: Rows the pass writes comps on. One enumeration lives in
    #: `execution/fusion_tracks.py`; this reads it rather than restating.
    def __init__(self, manifest: Mapping, project_folder: str,
                 resolve_project_name: str, timeline_name: str,
                 python_executable: Optional[str] = None,
                 apply_comps=None):
        self.manifest = manifest
        self.project_folder = project_folder
        self.resolve_project_name = resolve_project_name
        self.timeline_name = timeline_name
        self.python_executable = python_executable
        self._apply_comps = apply_comps
        self.expected_row_counts: dict = {}

    def _comp_rows(self) -> dict:
        from library.tools.execution.fusion_tracks import fusion_comp_tracks
        return {row_label("video", index): clips
                for index, clips, _t in fusion_comp_tracks(self.manifest)}

    def reachable_reason(self, changes: Sequence[ItemChange]) -> Optional[str]:
        if not self.project_folder or not os.path.isdir(self.project_folder):
            return (f"the project folder {self.project_folder!r} is not a "
                    f"directory, so the pass has nowhere to write its "
                    f"manifest")
        if not self.timeline_name:
            return ("no timeline name was given, and the pass refuses every "
                    "mutation rather than writing comps onto whatever is "
                    "current")
        if not (self.manifest.get("fusion_effects", {}).get("per_clip")):
            return ("the manifest declares no per-clip Fusion effects, so "
                    "the pass would return without writing a single comp "
                    "while reporting success")
        rows = self._comp_rows()
        for change in changes:
            if not (change.played_length_changes and change.comp_count):
                continue
            if change.row not in rows:
                return (f"{change.row} carries a comp on a clip this edit "
                        f"trims, and the manifest describes no clips on "
                        f"that row for the pass to key them to")
        for row, clips in sorted(rows.items()):
            expected = self.expected_row_counts.get(row)
            if expected is not None and len(clips) != expected:
                return (f"the manifest describes {len(clips)} clip(s) on "
                        f"{row} and the edited timeline will carry "
                        f"{expected}; the pass maps specs to items by "
                        f"position along the row, so it would write comps "
                        f"onto the wrong clips")
        return None

    def expects_comp(self, row: str, record_frame: int) -> Optional[bool]:
        """Whether the pass writes a comp on `row`, off the manifest.

        Per ROW, not per clip: the pass maps specs to items by position
        along the row and the manifest handed to it is the POST-EDIT
        one, so a row it describes with per-clip effects is a row every
        clip on it gets a comp from - the newly placed one included.
        """
        clips = self._comp_rows().get(row)
        if clips is None:
            return None
        per_clip = self.manifest.get("fusion_effects", {}).get("per_clip", {})
        return any(clip.get("label") in per_clip for clip in clips)

    def rederive(self, changes: Sequence[ItemChange]) -> dict:
        apply_comps = self._apply_comps
        if apply_comps is None:
            from library.tools import reel_look
            apply_comps = reel_look.apply_comps
        started = time.time()
        ok = apply_comps(self.manifest, self.project_folder,
                         self.resolve_project_name, self.timeline_name,
                         python_executable=self.python_executable)
        return {"ran": True, "ok": bool(ok),
                "timeline": self.timeline_name,
                "items": [f"{c.row}[{c.item_index}]" for c in changes
                          if c.played_length_changes],
                "seconds": round(time.time() - started, 3)}


# ── The seven steps, in order, as one call ──────────────────────────


@dataclass
class ComposedEditReceipt:
    """What each step did. Written so a composed edit can be read back."""

    staged: dict = field(default_factory=dict)
    plan: list = field(default_factory=list)
    rederivation_required: dict = field(default_factory=dict)
    captured: list = field(default_factory=list)
    deleted: dict = field(default_factory=dict)
    placed: dict = field(default_factory=dict)
    verified: dict = field(default_factory=dict)
    restored: list = field(default_factory=list)
    inserted: list = field(default_factory=list)
    rederived: dict = field(default_factory=dict)
    seconds: float = 0.0


def apply_composed_edit(*, timeline, media_pool,
                        changes: Sequence[ItemChange],
                        insertions: Sequence[Insertion] = (),
                        comp_dir: str, withheld_dir: str,
                        rederiver: Optional[CompRederiver] = None,
                        grade_sources: Optional[Mapping] = None,
                        link_rows: Mapping[str, str] = None,
                        picture_row: str = "V1") -> ComposedEditReceipt:
    """Steps 3 to 7, in the one order that works, on a STAGED timeline.

    Step 1 (`conform_comp_windows`) and step 2 (`plan_ripple`) run
    before this and hand it their results, because both are decisions
    the caller owns: which copy to edit, and where the cut is.

    `grade_sources` maps `(row, item_index)` to a LIVE item on the
    untouched reel to copy the colour grade from - a re-placed item
    comes back with one node where it had eight.

    Raises, and destroys nothing, if the edit changes a played length
    and has no route to the comp generator.
    """
    started = time.time()
    receipt = ComposedEditReceipt()
    changes = list(changes)
    insertions = list(insertions)
    grade_sources = dict(grade_sources or {})
    link_rows = dict(link_rows or {})

    receipt.plan = [
        {"row": c.row, "item_index": c.item_index, "how": c.how,
         "record_frame": c.record_frame, "duration": c.duration,
         "played_length_changes": c.played_length_changes,
         "comp_count": c.comp_count} for c in changes]

    # BEFORE anything is captured, and long before anything is deleted.
    tracks_before = reel_read.read_tracks(timeline)
    assert_insertions_declared(tracks_before, insertions)
    receipt.rederivation_required = assert_rederivation_reachable(
        changes, rederiver, insertions)
    assert_every_frame_covered(tracks_before, changes, insertions,
                               row=picture_row)

    rows_before = _rows_of(timeline)
    captures = []
    for change in changes:
        items = rows_before.get(change.row) or []
        if change.item_index >= len(items):
            raise PlacementNotVerified(
                f"REFUSING: the plan names {change.row}"
                f"[{change.item_index}] and that row holds "
                f"{len(items)} item(s). The plan and the timeline "
                f"disagree, so nothing is deleted.")
        captures.append(capture_item(items[change.item_index], change,
                                     comp_dir, withheld_dir))
    receipt.captured = [
        {"row": c.change.row, "item_index": c.change.item_index,
         "restorable_comps": len(c.restorable_comps),
         "withheld_comps": len(c.withheld_comps),
         "withheld_paths": [w.path for w in c.withheld_comps]}
        for c in captures]

    victims = [rows_before[c.change.row][c.change.item_index]
               for c in captures]
    receipt.deleted = delete_all(timeline, victims)

    ordered = placement_order(captures, insertions)
    receipt.placed = place_all(media_pool, ordered)

    rows_after = _rows_of(timeline)
    landed = verify_placement(rows_after, ordered)
    receipt.verified = {"landed": len(landed), "asked": len(ordered)}

    for capture in captures:
        change = capture.change
        item = landed[(change.row, change.record_frame)]
        link_with = ()
        partner_row = link_rows.get(change.row)
        if partner_row:
            link_with = [i for i in (rows_after.get(partner_row) or [])
                         if _read(i, "GetStart", None) == change.record_frame]
        receipt.restored.append(restore_item(
            item, capture,
            grade_source=grade_sources.get((change.row, change.item_index)),
            timeline=timeline, link_with=link_with))

    for insertion in insertions:
        item = landed[(insertion.row, insertion.record_frame)]
        receipt.inserted.append(treat_insertion(item, insertion))

    if receipt.rederivation_required.get("required"):
        pass_receipt = rederiver.rederive(changes)
        receipt.rederived = dict(pass_receipt)
        receipt.rederived["verified"] = assert_rederived(
            _rows_of(timeline), changes, pass_receipt, insertions,
            rederiver)

    receipt.seconds = round(time.time() - started, 3)
    return receipt


def _rows_of(timeline) -> dict:
    """Live item handles per row, taken from the one reader.

    `reel_read.live_track_items` holds the tree's one `GetItemListInTrack`
    call (AGENTS.md 15); `reel_read.live_items` is this function's slice
    of it.
    Re-read after every mutation: a handle held across a delete or a
    place is a zombie that still answers getters.
    """
    return {row_label(row["type"], row["index"]): row["items"]
            for row in reel_read.live_items(timeline)}
