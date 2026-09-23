"""A per-clip Fusion comp's MediaIn must cover every frame the item PLAYS.

`played_window.py` owns comp time: **comp frame 0 is the clip's first
played frame**, and the comp is rendered over `0 .. played - 1`.  This
module owns the other half of the same statement, the one nothing read
until 2026-09-12: the comp's `MediaIn` has its own validity range, and
if that range does not cover those frames there is no picture under
them.  Resolve does not draw black there and does not warn - it FAILS
the render, part-way through, with a timecode and nothing else:

    JobStatus Failed   "The Fusion composition at 00:00:52:07 could not
                        be processed successfully."

── The measurement this module exists for ──────────────────────────────

DaVinci Resolve Studio 21.1.0.0014, macOS 26.3, 2026-09-12, on a DRP
copy of the captain's `Podcast (field test)` (the captain's own project
was read and never written).  Every reel's ending was rendered as a
lossless PNG sequence over the freeze tail's own 19 frames.

    reel                                   MediaIn GlobalIn/Out   render
    Reel 01 - geo-is-comprehension...           1 / 19            Failed
      the same timeline, window conformed to    0 / 18            Complete 19/19
    Reel 13 - the-accounting-firm...            1 / 19            Failed
    Reel 23 - why-small-business-wins...        1 / 19            Failed
    Reel 28 - the-nail-salon-query...           1 / 19            Failed
    Reel 30 - your-google-business-profile...   1 / 19            Failed
    Reel 31 - is-there-a-way-to-game-ai         1 / 19            Failed
    Reel 09 (final), Reel 26                    covered           Complete

Six of the captain's eight built reels could not render their own last
frames, and in all six it is the same item: the freeze tail, a 19-frame
hold whose comp is rendered over comp frames 0..18 while its `MediaIn`
begins at comp frame 1.  The failure names the hold's FIRST frame
(00:00:52:07 is reel frame 1255, where Reel 01's freeze starts), which
is the frame `GlobalIn 1` leaves uncovered.  How many frames Resolve
writes before it gives up varies run to run - 8, 11 and 17 of 19 were
all observed on one state - so the FILE COUNT is not the signal and the
window is.

── What the comp pass writes, and what it does not ─────────────────────

The banked comp this engine generates is CORRECT on disk: it carries
`["MediaIn1.GlobalStart"] = 0` and `["MediaIn1.GlobalEnd"] = clip_dur - 1`
(`fusion/engine.py`), and importing one onto a fresh 19-frame item reads
back `GlobalIn 0 / GlobalOut 18`.  Importing one onto an item that is
ALREADY drifted repairs it.  So nothing in this repository writes the
bad window - it is Resolve's, and the engine's part is to READ IT BACK
and repair it, which is AGENTS.md 5's rule in its sharpest form.

**`SetInput` is not the repair, and this was measured both ways.**  On a
drifted item, `SetInput("GlobalIn", 0)` returns `None` and the read-back
stays at 1 - Resolve will let the window be pushed LATER and will not
let it be pulled back.  `SetInput("GlobalOut", 18)` on the same item
takes.  A conform that trusted the return value, or that checked only
the terms that happen to move, would report a repair it did not make.
Re-importing the banked comp is the repair, and the verify is a re-read.

── Why this is not `DuplicateTimeline`'s fault ─────────────────────────

The spike that found the drift saw it on duplicates of Reel 01 and
attributed it to `DuplicateTimeline`.  Measured here, the attribution is
backwards: the duplicate carried `0 / 18`, which is the window the law
below gives, and the SOURCE reel carried `1 / 19`.  A duplicate, and a
DRP export/import, both NORMALISE some of these windows - which is why a
copy of the captain's project cannot be used to test his reels, and why
every measurement above was mirrored back onto the copy from the
captain's own `ExportFusionComp` output before it was rendered.

This engine calls `DuplicateTimeline` nowhere.  A reel is staged by `CreateEmptyTimeline` plus
`AppendToTimeline` plus the comp pass, so the comp pass IS the staging
step where a media window is established, and that is where the conform
is wired: `library/tools/execution/apply_fusion_comps.py`.

── Repair record: 2026-09-12, the captain's live reels ─────────────────

Five of the six drifted endings were repaired in place on `Podcast
(field test)` (Reels 13, 23, 28, 30, 31); Reel 01 already read `0 / 18`
with no write between this module's `1 / 19` measurement and the repair,
in a Resolve instance that never restarted - so a window can change
without a build, and a cached window is never ground truth.  Re-read
live, every time.

Each repair re-imported the item's OWN `ExportFusionComp` output with
the window lines restored to the known-good shape (`GlobalIn` line
removed, `GlobalOut 19 -> 18`, `Clip GlobalStart` removed, `Clip
GlobalEnd 19 -> 18`) - not one shared banked file, because each reel's
freeze carries its own inherited treatment.  `conform_item` above is
the repair path; its verify re-read on the same handle transiently
reported `0 / 19` on two reels before a fresh read settled on `0 / 18`,
so the verify that counts is a FRESH read, and the proof that counts is
a render: all eight reels' last 19 frames rendered 19/19 Complete after
the repair.  A re-import rebinds `MediaSource` Timeline -> MediaPool
(with a `MediaID`); a before/after snapshot diff showed that and the
window as the only changes - transforms, source ranges, grades,
captions and markers untouched.  The reversal is the drifted export,
re-imported the same way.
"""

from __future__ import annotations

from typing import Any, Optional

#: Every input that describes where a `MediaIn` reads from.  Read as a
#: set, never one term: `GlobalIn`/`GlobalOut` are the comp frames the
#: node is valid over and `ClipTimeStart`/`ClipTimeEnd` are the source
#: frames it maps them to, and a repair that moves one without the other
#: is a different picture rather than a fixed one.
WINDOW_INPUTS = ("MediaSource", "MediaID", "AudioTrack", "GlobalIn",
                 "GlobalOut", "ClipTimeStart", "ClipTimeEnd")


class CompWindowUncovered(RuntimeError):
    """A comp's MediaIn does not cover the frames its item plays.

    Raised rather than shipped: the reel renders until Resolve reaches
    the uncovered frame and then fails the whole job, so a build that
    let this through delivers a reel that stops part-way through its own
    ending.
    """


def media_in_tool(item: Any, index: int = 1) -> Optional[Any]:
    """The comp's `MediaIn` tool, or None.

    Found by `TOOLS_RegID`, never by name: the tool is `MediaIn1` in a
    generated comp and Resolve renames on import.
    """
    try:
        comp = item.GetFusionCompByIndex(index)
    except Exception:  # noqa: BLE001 - a handle that will not answer
        return None
    if comp is None or isinstance(comp, str):
        return None
    try:
        tools = comp.GetToolList(False) or {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(tools, dict):
        return None
    for _key, tool in sorted(tools.items()):
        try:
            if tool.GetAttrs("TOOLS_RegID") == "MediaIn":
                return tool
        except Exception:  # noqa: BLE001 - a tool that will not name itself
            continue
    return None


def read_window(item: Any, index: int = 1) -> Optional[dict]:
    """The comp's media window as Resolve reads it back, or None.

    None means there is no comp or no `MediaIn` in it - an ABSENCE, not
    a pass. Callers distinguish the two; `uncovered_reason` is only
    asked about a window that exists.
    """
    tool = media_in_tool(item, index)
    if tool is None:
        return None
    window = {}
    for key in WINDOW_INPUTS:
        try:
            window[key] = tool.GetInput(key)
        except Exception:  # noqa: BLE001 - reported as unreadable
            window[key] = None
    return window


def uncovered_reason(window: Optional[dict],
                     played_frames: int) -> Optional[str]:
    """Why this window fails to cover `0 .. played_frames - 1`, or None.

    The whole predicate, in one place, on the two terms that decide it.

    A window of `None` - no comp, or a handle that would not answer - is
    NOT a finding here. There is nothing to judge, and a check that
    reported an unreadable handle as a defect would fail correct output,
    which this repository holds to be no better than a gate that cannot
    fail (AGENTS.md 10.4). The absence is REPORTED by the caller instead:
    `conform_item` records it as `unreadable` and says so.
    """
    if played_frames is None or int(played_frames) <= 0:
        return None
    last = int(played_frames) - 1
    if window is None:
        return None
    start, end = window.get("GlobalIn"), window.get("GlobalOut")
    if start is None or end is None:
        return None
    try:
        start, end = float(start), float(end)
    except (TypeError, ValueError):
        return f"GlobalIn/GlobalOut are not numbers ({start!r}/{end!r})"
    if start > 0:
        return (f"GlobalIn {start:g} is past comp frame 0, so the first "
                f"of the {int(played_frames)} frames this item plays has "
                f"no media under it")
    if end < last:
        return (f"GlobalOut {end:g} stops before comp frame {last}, so the "
                f"last {int(last - end)} of the {int(played_frames)} frames "
                f"this item plays have no media under them")
    return None


def covers(window: Optional[dict], played_frames: int) -> bool:
    """Whether this window covers every comp frame the item plays."""
    return uncovered_reason(window, played_frames) is None


def conform_item(item: Any, played_frames: int, comp_path: str,
                 *, index: int = 1, label: str = "") -> dict:
    """Read, compare, repair, verify - one item's comp media window.

    The repair is a re-import of the BANKED comp, because that is the
    one that was measured to work (see the module docstring); `SetInput`
    is not used, because it cannot pull a window back. The verify is a
    re-read on a fresh handle, never a return value.

    Returns the receipt - `before`, `repaired`, `after`, `reason` - so a
    caller can SAY what it did. Raises `CompWindowUncovered` when the
    window is still uncovered after the repair, because the alternative
    is promoting a reel that cannot render its own frames.
    """
    before = read_window(item, index)
    reason = uncovered_reason(before, played_frames)
    receipt = {"label": label, "played_frames": played_frames,
               "before": before, "reason": reason, "repaired": False,
               "unreadable": before is None
               or before.get("GlobalIn") is None
               or before.get("GlobalOut") is None,
               "after": before}
    if reason is None:
        return receipt
    for name in (item.GetFusionCompNameList() or []):
        item.DeleteFusionCompByName(name)
    item.ImportFusionComp(comp_path)
    receipt["repaired"] = True
    after = read_window(item, index)
    receipt["after"] = after
    still = uncovered_reason(after, played_frames)
    receipt["reason_after"] = still
    if still is not None:
        raise CompWindowUncovered(
            f"REFUSING to build {label or 'this item'}: {still}. "
            f"Re-importing {comp_path} did not repair it "
            f"(window {before} -> {after}). Resolve fails the whole "
            f"render job at the first uncovered frame, so this reel "
            f"would stop part-way through rather than deliver short.")
    return receipt
