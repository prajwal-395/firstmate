"""Apply a recorded edit ledger to a timeline directly, without a build.

The gap this closes (C-14): the ledger is replayed by the build after its
own passes (`edit_ledger.replay_on_timeline`, called from `reel_build`).
There is no standalone "apply this ledger" operation, so an editor cannot
replay a recorded change in isolation - the only way to see it is a full
build, and the only way to undo it is another full build.

This module is that standalone replay: read the ledger, apply each row
to the target timeline through the same path the build uses, and journal
it so `ren undo` can reverse it in place.

What it does
------------
1. Reads the ledger (`edit_ledger.load_rows`)
2. Filters rows for the target reel (`edit_ledger.rows_for_reel`)
3. Opens an undo journal entry (reads the timeline before)
4. Applies each row via `edit_ledger.replay_on_timeline`
5. Closes the journal entry (reads the timeline after)
6. Returns a receipt with applied/unreplayable rows

The journal entry means `ren undo` can reverse the replay in place,
the same way it reverses a touch-up. A row the replay cannot apply is
REPORTED BY NAME and REFUSES the replay (F-17): the captain's edits are
never overwritten or lost.

`tests/unit/resolve/test_replay_ledger.py`.
"""

from __future__ import annotations

import os
import re as _re
import time

from library.tools import edit_ledger
from library.tools.ren_refusal import RenRefusal


class ReplayLedgerRefused(RenRefusal):
    """A ledger replay that refused. Fail closed, always by name."""


class ReplayLedgerError(RuntimeError):
    """A ledger replay that failed mid-flight."""


def replay_ledger(project_folder, *, reel_name="", timeline=None,
                  connect=None, resolve_project_name="",
                  item_for_span=None, transcript=None,
                  spans=None) -> dict:
    """Apply the recorded ledger to a timeline directly.

    Reads the ledger, filters rows for the target reel, opens an undo
    journal entry, applies each row through the build's replay path,
    closes the journal entry, and returns a receipt.

    `timeline` is the target timeline (a live Resolve timeline handle).
    `connect` is a seam for tests: `connect(resolve_name) -> project`.
    `item_for_span` maps a span index to its live timeline item.
    `transcript` is the project's transcript (loaded if not provided).
    `spans` are the placed picture spans (read from the timeline if not
    provided).

    Returns a receipt with:
    - `applied`: rows that were applied
    - `unreplayable`: rows that could not be replayed
    - `journal`: the undo journal entry id
    - `seconds`: wall time
    """
    rows = edit_ledger.load_rows(project_folder)
    scoped = edit_ledger.rows_for_reel(rows, reel_name)
    if not scoped:
        return {"applied": [], "unreplayable": [], "journal": "",
                "seconds": 0.0, "reel": reel_name}

    if timeline is None:
        timeline = _resolve_timeline(project_folder, reel_name,
                                     connect, resolve_project_name)

    if transcript is None:
        transcript = _load_transcript(project_folder)

    if spans is None or item_for_span is None:
        read_spans, read_items = _read_spans(timeline, transcript,
                                             project_folder)
        if spans is None:
            spans = read_spans
        if item_for_span is None:
            item_for_span = read_items

    reel_number = _reel_number(reel_name)
    final = reel_name

    from library.tools import undo_journal as _journal

    started = time.time()
    try:
        journal = _journal.open_entry(
            project_folder, final=final, reel=reel_number,
            resolve_project=resolve_project_name,
            spec={"rows": scoped, "op": "replay_ledger"},
            gate_class="replay",
            source_timeline=timeline,
            removals=[],
            project=None)
    except _journal.UndoRefused as unrecordable:
        raise ReplayLedgerRefused(
            unrecordable.what, unrecordable.why,
            unrecordable.fix) from unrecordable

    try:
        report = edit_ledger.replay_on_timeline(
            final, scoped, spans, transcript, timeline,
            item_for_span=item_for_span, reel_name=reel_name)
    except Exception as failed:
        _journal.fail_entry(project_folder, journal, repr(failed))
        raise ReplayLedgerError(
            f"replay_ledger failed on {final!r}: {failed}") from failed

    try:
        _journal.close_entry(project_folder, journal,
                             after_timeline=timeline,
                             rows={"applied": report["applied"],
                                   "unreplayable": report["unreplayable"]},
                             project=None)
    except Exception as failed:
        raise ReplayLedgerError(
            f"replay_ledger could not close its journal on {final!r}: "
            f"{failed}") from failed

    # F-17: an unreplayable row whose loss is real REFUSES the replay.
    # A row scoped to another reel is routine and does not refuse.
    try:
        edit_ledger.refuse_unreplayable(report["unreplayable"], reel_name)
    except edit_ledger.EditLedgerError as exc:
        raise ReplayLedgerRefused(
            str(exc),
            "the captain's edits are never overwritten or lost",
            "re-record the rows against the words now spoken, or remove "
            "them from the ledger, then re-run `ren replay-ledger`") from exc

    return {
        "applied": report["applied"],
        "planned": report.get("planned", []),
        "unreplayable": report["unreplayable"],
        "journal": journal["id"],
        "seconds": round(time.time() - started, 3),
        "reel": reel_name,
    }


def _resolve_timeline(project_folder, reel_name, connect,
                      resolve_project_name):
    """The target timeline handle, from Resolve."""
    if connect is None:
        from library.tools.reel_build import (
            _connect_resolve_project as _connect)
        connect = _connect
    resolve_name = resolve_project_name or _resolve_name(project_folder)
    project = connect(resolve_name)
    from library.tools.reel_build import timelines_to_replace
    found = {t.GetName(): t for t in
             timelines_to_replace(project, {reel_name})}
    if reel_name not in found:
        raise ReplayLedgerRefused(
            f"no timeline called {reel_name!r} is in Resolve "
            f"project {resolve_name!r}",
            "a ledger replay edits an existing timeline, and there is "
            "none to edit",
            "build it first (`ren build <project>`), then replay the "
            "ledger onto it")
    return found[reel_name]


def _resolve_name(project_folder: str) -> str:
    """The Resolve project name, from the declaration or the folder."""
    import yaml as _yaml

    with open(os.path.join(project_folder, "project.yaml"),
              encoding="utf-8") as handle:
        resolve_config = ((_yaml.safe_load(handle) or {}).get("resolve")
                          or {})
    return (str(resolve_config.get("project_name") or "").strip()
            or os.path.basename(project_folder))


def _reel_number(reel_name: str) -> int:
    """The reel number from a reel name, or 0 for the master timeline."""
    match = _re.match(r"^Reel\s+(\d+)(?:\s|$)", str(reel_name or ""))
    return int(match.group(1)) if match else 0


def _load_transcript(project_folder) -> dict:
    """The project's master transcript, or {} where none is on file."""
    try:
        from library.tools import captain_edits as _edits
        return _edits.load_transcript(project_folder) or {}
    except Exception:  # noqa: BLE001 - a missing transcript reads as absent
        return {}


def _read_spans(timeline, transcript, project_folder):
    """The timeline's video items as spans, with master ranges.

    Returns `(spans, item_for_span)`. Each span carries `master` (the
    master-transcript range the item's source footage covers) so
    word-anchored rows match the same items the build's replay matches.
    The master range is computed from the item's source file and the
    project's transcript; where the source file is not in the transcript,
    `master` is None and word-anchored rows report it as unreplayable
    rather than guessing.

    `item_for_span` maps a span index to its live timeline item, the
    handle `apply_lut_to_item` and `apply_power_grade` write to.
    """
    from library.tools import reel_read as _read

    fps = _project_fps(project_folder)
    rows = _read.live_items(timeline)
    spans = []
    items = []
    for row in rows:
        if row.get("type") != "video":
            continue
        for item in row.get("items") or []:
            clip = _read.clip_detail(item, "video",
                                     int(row.get("index") or 1),
                                     str(row.get("name") or ""))
            master = _master_range(clip, transcript, fps)
            spans.append({
                "master": master,
                "record_in": clip.get("record_in"),
                "record_out": clip.get("record_out"),
                "source_file": clip.get("source_file"),
            })
            items.append(item)

    def _item_for_span(index):
        if index is None or index < 0 or index >= len(items):
            return None
        return items[index]

    return spans, _item_for_span


def _master_range(clip: dict, transcript: dict, fps: float):
    """The master-transcript range a timeline item's source covers.

    The transcript segment for the item's source file carries
    `timeline_start` (where the file starts on the master timeline) and
    `source_start` (the source frame at that point). The item's source
    in/out frames map onto that clock. Returns None where the source
    file is not in the transcript - a word-anchored row then reports the
    item as unmatched rather than guessing.
    """
    source_file = str(clip.get("source_file") or "")
    if not source_file:
        return None
    source_in = clip.get("source_in_frame")
    source_out = clip.get("source_out_frame")
    if source_in is None or source_out is None:
        return None
    for segment in (transcript or {}).get("segments") or []:
        if str(segment.get("source_file") or "") != source_file:
            continue
        try:
            timeline_start = float(segment.get("timeline_start"))
            segment_source_in = float(segment.get("source_start"))
        except (TypeError, ValueError):
            continue
        master_start = timeline_start + (float(source_in)
                                         - segment_source_in) / fps
        master_end = timeline_start + (float(source_out)
                                       - segment_source_in) / fps
        return (round(master_start, 3), round(master_end, 3))
    return None


def _project_fps(project_folder) -> float:
    """The project's fps, from its own declaration."""
    import yaml as _yaml

    try:
        with open(os.path.join(project_folder, "project.yaml"),
                  encoding="utf-8") as handle:
            source = ((_yaml.safe_load(handle) or {}).get("source")
                      or {})
        return float(source.get("fps") or 30)
    except Exception:  # noqa: BLE001 - a missing declaration reads as 30
        return 30.0



