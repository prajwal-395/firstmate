"""Repair a freeze-hold tail's Fusion comp media windows, in place.

A hold that cannot cover the frames it plays renders black (and, on the
captain's reels, a thin bright streak where one slice of the frame
decodes).  The failure signature is `comp_media_window`'s: a collapsed
`0 / 0` window, a binding to a media-pool item that no longer exists, or
the off-by-one `1 / 19` a frame-term write leaves behind
(`composed_edit.apply_window` measured both directions).  The windows
also FLUCTUATE between reads with no writes, so a repair that trusts one
read is a repair that reports what it happened to see.

This module is the in-place half of that repair: rebind each drawing
comp on the hold item to the Timeline binding Resolve derives the
correct window from, then prove the window with FRESH reads - a new
item handle per read, never the same handle twice - until consecutive
reads agree and cover every played frame.  A window that will not
converge raises instead of shipping: the alternative is promoting a
tail that cannot render.

Ownership: `comp_media_window` owns what a window means and whether one
covers; `composed_edit.apply_window` owns the binding write; this module
owns the stable re-read verdict and the hold scoping.  The `ren touch`
op `repair_hold_window` (`reel_touchup`) is the only caller: it stages,
calls `repair_hold_item_windows` on the staged hold, and promotes
through the normal touchup path, so the journal archives both sides and
the act is recorded as a Ren version.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Consecutive identical covered reads that count as stable.  Three: one
#: proves nothing about fluctuation, two is a coincidence, three is the
#: smallest run the Sep-12 repair record never saw flip.
STABLE_READS = 3

#: Fresh-read rounds before the repair refuses as non-converging.  Each
#: round is up to STABLE_READS reads, so the ceiling is bounded and the
#: receipt says how many it took.
MAX_ROUNDS = 5

#: The binding `apply_window` measured Resolve deriving the window from.
TIMELINE_BINDING = {
    "MediaSource": "Timeline",
    "MediaID": "",
    "AudioTrack": "Timeline Audio",
}


class HoldWindowRefused(RuntimeError):
    """The hold is not repairable in place; the staging stands as found."""


def _item_start(item: Any) -> int | None:
    """An item's record start, or None where it will not answer."""
    try:
        return int(item.GetStart())
    except Exception:  # noqa: BLE001 - unreadable handle, skip it
        return None


def _fresh_item(timeline: Any, row: str, record_frame: int) -> Any:
    """The staged hold item off a new handle, or refuse.

    Every stability read goes through here: a re-read on the handle the
    write used can answer from Resolve's cache, which is how a repair
    reports a window it did not make.
    """
    kind = str(row).strip().upper()
    if kind.startswith("V"):
        track_type: str | None = "video"
    elif kind.startswith("A"):
        track_type = "audio"
    else:
        raise HoldWindowRefused(
            f"row {row!r} names no video or audio track - a hold window "
            f"repair addresses its item by row, never by guess")
    try:
        index = int(kind[1:])
    except ValueError:
        raise HoldWindowRefused(
            f"row {row!r} names no numbered track") from None
    try:
        count = int(timeline.GetTrackCount(track_type) or 0)
    except Exception as exc:  # unreadable is unrepairable
        raise HoldWindowRefused(
            f"the staging copy would not say how many {track_type} tracks "
            f"it carries ({exc}) - nothing was written and the approved "
            f"timeline stands") from exc
    if index < 1 or index > count:
        raise HoldWindowRefused(
            f"row {row!r} is outside the staging copy's {count} "
            f"{track_type} track(s) - nothing was written and the "
            f"approved timeline stands")
    try:
        items = timeline.GetItemListInTrack(track_type, index) or []
    except Exception as exc:  # unreadable is unrepairable
        raise HoldWindowRefused(
            f"the staging copy would not list {row} ({exc}) - nothing "
            f"was written and the approved timeline stands") from exc
    hits = [item for item in items or ()
            if _item_start(item) == int(record_frame)]
    if len(hits) != 1:
        raise HoldWindowRefused(
            f"the staging copy holds {len(hits)} items at "
            f"{row}@{record_frame} - the plan and the timeline "
            f"disagree. Nothing is written and the approved timeline "
            f"stands.")
    return hits[0]


def _window_terms(window: Mapping | None) -> dict | None:
    if window is None:
        return None
    return {name: window.get(name) for name in (
        "MediaSource", "MediaID", "AudioTrack", "GlobalIn", "GlobalOut",
        "ClipTimeStart", "ClipTimeEnd")}


def _want_window(duration: int) -> dict:
    """The covered window a hold of `duration` frames must read back.

    Comp frames `0 .. duration - 1` over the Timeline binding Resolve
    derives the rest from (`apply_window` writes the frame terms only
    when the re-read after the binding is still wrong, because writing
    them onto a correct window moves it).
    """
    return {**TIMELINE_BINDING,
            "GlobalIn": 0, "GlobalOut": duration - 1,
            "ClipTimeStart": 0, "ClipTimeEnd": duration - 1}


def repair_hold_item_windows(timeline: Any, row: str, record_frame: int,
                             duration: int, comp_dir: str) -> dict:
    """Rebind every drawing comp on the staged hold item; verify stable.

    `duration` is the played length the windows must cover (comp frames
    `0 .. duration - 1`).  `comp_dir` takes the repaired-comp exports
    the re-import repair stages through.  Returns the receipt -
    per-comp before/after, every fresh read, what was written - so the
    touch files what it did instead of claiming a repair.  Raises
    `HoldWindowRefused` when a comp will not converge, when a read will
    not answer, or when the item holds no drawing comp at all.
    """
    if int(duration) <= 0:
        raise HoldWindowRefused(
            f"a hold of {duration} frames has no window to repair")
    import os as _os

    from library.tools import reel_read as _read

    _os.makedirs(comp_dir, exist_ok=True)
    receipt: dict = {
        "row": str(row).upper(), "record_frame": int(record_frame),
        "duration": int(duration), "comps": [], "stable_reads": STABLE_READS,
    }
    item = _fresh_item(timeline, row, record_frame)
    try:
        comp_count = int(item.GetFusionCompCount() or 0)
    except Exception as exc:  # unreadable is unrepairable
        raise HoldWindowRefused(
            f"the staged {row}@{record_frame} would not say how many "
            f"Fusion comps it carries ({exc}) - nothing was written "
            f"and the approved timeline stands") from exc
    if comp_count < 1:
        raise HoldWindowRefused(
            f"the staged {row}@{record_frame} carries no Fusion comp - "
            f"there is no media window to repair. Nothing was written "
            f"and the approved timeline stands.")
    repaired_exports: dict[int, str] = {}
    for index in range(1, comp_count + 1):
        tools = _read._comp_tools(item, index)
        draws = _read.comp_draws_something({"tools": tools})
        if draws is False:
            receipt["comps"].append(
                {"comp_index": index, "draws": False,
                 "action": "left alone - a passthrough draws nothing, "
                            "and an empty comp has no picture to lose"})
            continue
        entry = _repair_one_comp(
            timeline, row, record_frame, int(duration), index,
            draws, comp_dir, repaired_exports)
        receipt["comps"].append(entry)
    if not any(comp.get("draws") for comp in receipt["comps"]):
        raise HoldWindowRefused(
            f"the staged {row}@{record_frame} holds no drawing comp - "
            f"there is no media window to repair. Nothing was written "
            f"and the approved timeline stands.")
    return receipt


def _repair_one_comp(timeline: Any, row: str, record_frame: int,
                     duration: int, index: int, draws: bool | None,
                     comp_dir: str,
                     repaired_exports: dict[int, str]) -> dict:
    """Rebind one comp until its window is covered and stable.

    First the binding write (`apply_window`): cheap and it leaves the
    comp alone.  Where Resolve will not pull the window back - the
    measured `GlobalIn` that will not move - the second repair
    re-imports a sibling comp's own repaired export, which is the
    banked-comp direction `conform_item` measured: an import
    establishes a fresh binding and window on the item, while a
    `SetInput` onto a drifted window does not move it.  The sibling
    shares the hold's own treatment stack by construction (it was read
    off the same item), so nothing foreign reaches the picture.
    """
    from library.tools import comp_media_window as _windows
    from library.tools.composed_edit import apply_window

    entry: dict = {"comp_index": index,
                   "draws": True if draws else "unreadable-treated-as-drawing",
                   "reads": [], "wrote": [], "repaired": False}
    item = _fresh_item(timeline, row, record_frame)
    window = _windows.read_window(item, index)
    entry["reads"].append(_window_terms(window))
    reason = _windows.uncovered_reason(window, duration)
    if reason is None and window is not None:
        if _is_stable(timeline, row, record_frame, index,
                       _window_terms(window), entry["reads"]):
            entry["action"] = ("already covered and stable across "
                                f"{STABLE_READS} fresh reads - no write")
            _bank_repaired(timeline, row, record_frame, index, comp_dir,
                           repaired_exports, entry)
            entry["repaired"] = True
            entry["how"] = "already-covered"
            return entry
        reason = ("covered on first read but unstable across fresh "
                  "reads - a cached window is never ground truth")
    entry["first_reason"] = reason
    for _round in range(MAX_ROUNDS):
        item = _fresh_item(timeline, row, record_frame)
        _after, wrote = apply_window(
            item, index, _want_window(duration))
        entry["wrote"].append(wrote)
        if _covered_and_stable(timeline, row, record_frame, index,
                               duration, entry):
            entry["repaired"] = True
            entry["how"] = "rebind"
            break
    if not entry["repaired"]:
        _reimport_sibling(timeline, row, record_frame, duration, index,
                          comp_dir, repaired_exports, entry)
    if not entry["repaired"]:
        sibling = {key: entry[key] for key in (
            "sibling_donor", "sibling_refused", "imported_draws",
            "banked_export") if key in entry}
        raise HoldWindowRefused(
            f"the staged {row}@{record_frame} comp {index} would "
            f"not converge on a covered window ({len(entry['reads'])} "
            f"fresh reads: {entry['reads']}; sibling: {sibling}) - "
            f"the approved timeline stands unrepaired rather than "
            f"half-repaired.")
    if entry.get("how") == "rebind":
        _bank_repaired(timeline, row, record_frame, index, comp_dir,
                       repaired_exports, entry)
    entry["action"] = ("rebound to the Timeline binding Resolve "
                       "derives the window from; covered and stable "
                       f"across {STABLE_READS} fresh reads")
    return entry


def _bank_repaired(timeline: Any, row: str, record_frame: int, index: int,
                   comp_dir: str, repaired_exports: dict[int, str],
                   entry: dict) -> None:
    """Export a repaired comp as the sibling repair's banked source.

    A failed export refuses the whole repair: a sibling re-import
    with no proved export would invent the bank it draws on.
    """
    import os as _os

    path = _os.path.join(
        comp_dir, f"hold_{str(row).lower()}_{record_frame}_c{index}.comp")
    item = _fresh_item(timeline, row, record_frame)
    try:
        exported = item.ExportFusionComp(path, index)
    except Exception as exc:  # an unexportable repair proves nothing
        raise HoldWindowRefused(
            f"the staged {row}@{record_frame} comp {index} repaired "
            f"but would not export ({exc}) - the approved timeline "
            f"stands") from exc
    if not exported:
        raise HoldWindowRefused(
            f"the staged {row}@{record_frame} comp {index} repaired "
            f"but refused to export - the approved timeline stands")
    repaired_exports[index] = path
    entry["banked_export"] = path


def _covered_and_stable(timeline: Any, row: str, record_frame: int,
                        index: int, duration: int, entry: dict) -> bool:
    """One fresh read plus the stability tail; records everything."""
    from library.tools import comp_media_window as _windows

    fresh = _fresh_item(timeline, row, record_frame)
    window = _windows.read_window(fresh, index)
    entry["reads"].append(_window_terms(window))
    return (_windows.uncovered_reason(window, duration) is None
            and window is not None
            and _is_stable(timeline, row, record_frame, index,
                           _window_terms(window), entry["reads"]))


def _reimport_sibling(timeline: Any, row: str, record_frame: int,
                      duration: int, index: int, comp_dir: str,
                      repaired_exports: dict[int, str], entry: dict) -> None:
    """Overwrite a stuck comp with a repaired sibling's own export.

    The sibling was read off the same hold item and proved covered and
    stable, so its export carries the hold's own treatment with a
    working window - the banked-comp direction, without inventing a
    comp the reel never had.  The import overwrites the stuck comp in
    its own slot (load + import, the reset direction): Resolve refuses
    this item's comp delete, so no delete is attempted and the stack
    shape cannot move.  Marks `entry["repaired"]` on a proved import;
    records the refusal when no sibling has repaired yet or the import
    will not verify.
    """
    if not repaired_exports:
        entry["sibling_refused"] = (
            "no sibling comp has repaired yet - there is no proved "
            "export to re-import, and inventing one would be a new "
            "treatment by another name")
        return
    from library.tools import reel_read as _read

    donor = max(repaired_exports)
    if donor == index:
        entry["sibling_refused"] = (
            "the only repaired export is this comp's own - "
            "re-importing it cannot move a window that will not move")
        return
    item = _fresh_item(timeline, row, record_frame)
    try:
        names_before = list(item.GetFusionCompNameList() or [])
    except Exception:  # noqa: BLE001 - unreadable names are unrepairable
        entry["sibling_refused"] = (
            "the staged item would not name its comps - nothing was "
            "deleted and the approved timeline stands")
        return
    if len(names_before) < index:
        entry["sibling_refused"] = (
            f"the staged item carries {len(names_before)} comps, so "
            f"comp {index} is not there to replace")
        return
    victim = names_before[index - 1]
    entry["sibling_donor"] = {"comp_index": donor,
                              "export": repaired_exports[donor]}
    try:
        loaded = item.LoadFusionCompByName(victim)
    except Exception as exc:  # noqa: BLE001 - judged by re-read below
        entry["load_error"] = repr(exc)
        loaded = None
    try:
        names_mid = list(item.GetFusionCompNameList() or [])
    except Exception:  # noqa: BLE001 - unreadable names are unrepairable
        entry["sibling_refused"] = (
            "the staged item would not name its comps after the load "
            "- nothing further was written")
        return
    if len(names_mid) != len(names_before):
        entry["sibling_refused"] = (
            f"loading {victim!r} moved the stack "
            f"({len(names_before)} -> {len(names_mid)} comps) - nothing "
            f"further was written")
        return
    try:
        item.ImportFusionComp(repaired_exports[donor])
    except Exception as exc:  # noqa: BLE001 - judged by re-read below
        entry["import_error"] = repr(exc)
    try:
        names_after = list(item.GetFusionCompNameList() or [])
    except Exception:  # noqa: BLE001 - unreadable names are unrepairable
        entry["sibling_refused"] = (
            "the staged item would not name its comps after the "
            "import - the stack shape is unproved, so the staging "
            "copy refuses instead of promoting it")
        return
    if len(names_after) != len(names_before):
        entry["sibling_refused"] = (
            f"the import landed {len(names_after)} comps where "
            f"{len(names_before)} stood - the stack shape moved, so "
            "the staging copy refuses instead of promoting a new shape")
        return
    entry["load_returned"] = loaded
    entry["names_after_import"] = names_after
    if _covered_and_stable(timeline, row, record_frame, index,
                           duration, entry):
        entry["repaired"] = True
        entry["how"] = f"sibling-reimport (donor comp {donor})"
        repaired_exports[index] = repaired_exports[donor]
        tools = _read._comp_tools(_fresh_item(timeline, row, record_frame),
                                  index)
        entry["imported_draws"] = _read.comp_draws_something(
            {"tools": tools})
    else:
        entry["sibling_refused"] = (
            "the sibling import landed but its window is not covered "
            "and stable - the staging copy refuses instead of "
            "promoting it")


def _is_stable(timeline: Any, row: str, record_frame: int, index: int,
               window: dict | None, reads: list) -> bool:
    """Two more fresh reads identical to `window`, recorded onto `reads`.

    Returns False (recording what differed) instead of raising: the
    caller decides whether to rebind again or refuse.  A read that will
    not answer is instability too.
    """
    from library.tools import comp_media_window as _windows

    if window is None:
        return False
    for _ in range(STABLE_READS - 1):
        try:
            fresh = _fresh_item(timeline, row, record_frame)
            now = _windows.read_window(fresh, index)
        except HoldWindowRefused:
            reads.append(None)
            return False
        terms = _window_terms(now)
        reads.append(terms)
        if terms != window:
            return False
    return True


def is_hold_source(source_file: str) -> bool:
    """Whether this source path is a freeze-hold tail by convention."""
    from library.tools.reel_ending import BAKED_HOLD_PREFIXES

    base = str(source_file or "").rsplit("/", 1)[-1]
    return base.startswith(BAKED_HOLD_PREFIXES)


def describe_hold(item: Any) -> dict:
    """What the staged item is: baked hold file, native hold, or neither.

    A repair that cannot say what it is repairing is a repair addressed
    by hope.  The caller refuses on `"kind": "neither"`.
    """
    from library.tools.reel_ending import is_freeze_path, is_native_hold

    path = _pool_path(item)
    if not path:
        try:
            path = str(item.GetName() or "")
        except Exception:  # noqa: BLE001 - a nameless item is not a hold
            path = ""
    if is_freeze_path(path):
        return {"kind": "baked", "source": path}
    if is_native_hold(item):
        return {"kind": "native", "source": path}
    return {"kind": "neither", "source": path}


def _pool_path(item: Any) -> str:
    """An item's pool file path, or "" where it will not answer."""
    try:
        pool_item = item.GetMediaPoolItem()
    except Exception:  # noqa: BLE001 - unreadable path is not a hold
        return ""
    if pool_item is None:
        return ""
    for prop in ("File Path", "Clip Path"):
        path = _clip_property(pool_item, prop)
        if path:
            return path
    return ""


def _clip_property(pool_item: Any, prop: str) -> str:
    """One pool property, or "" where Resolve will not answer it."""
    try:
        return str(pool_item.GetClipProperty(prop) or "")
    except Exception:  # noqa: BLE001 - the next property name is tried
        return ""
