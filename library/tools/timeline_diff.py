"""Timeline diff: two exported timelines, expressed as edit_graph changes.

The gap this closes
-------------------
`rounds.diff_reel` diffs two row snapshots (`reel_read.rows_of`) and
reports per-row verdicts - which rows changed, which items are gone. That
answers the promotion guard's question ("did the captain lose a row?").
It does not answer the creative question the captain's commit example
asks: "11 trims, 4 clip replacements, 2 J-cuts, music cue shifted -1.73
sec". Those are typed changes, and the edit_graph vocabulary
(`edit_graph.Mutation`: retime_block, remove_node, reorder_passages) is
what a graph-level diff would speak.

This module diffs two timeline reads (`reel_read.read_tracks` shape -
the JSON a timeline export reads back as) and produces a list of typed
changes. Each change carries the edit_graph mutation it corresponds to
where the change IS a graph mutation (a move is a retime_block, a removal
is a remove_node), and names the timeline-level op where it is not (a
trim, a replacement, a J-cut - the graph has no node for a source-level
edit).

It builds on `reel_replace_guard` for the row pairing discipline and
`edit_graph.Mutation` for the mutation vocabulary - no parallel diff is
written. The row read carries no graph node ids, so a change's mutation
names its op and its field (a retime's new start) but not a node_id: the
graph that would supply one is built from `pipeline_data.json`, not from
the timeline.
"""
from __future__ import annotations

from typing import Mapping, Sequence

# ── The change vocabulary ────────────────────────────────────────────
#
# The edit_graph mutation ops where the change IS a graph mutation, plus
# the timeline-level ops the graph has no node for. A commit's `changes`
# list is built from these, so the captain's "11 trims, 4 replacements"
# is a count over this vocabulary.

OP_MOVE = "move"
"""An item kept its name and source, and changed its timeline span.

Expressed as `edit_graph.Mutation(op="retime_block")` - the block the
item plays moved to a new timeline start.
"""

OP_REMOVE = "remove"
"""An item disappeared from a row that still exists.

Expressed as `edit_graph.Mutation(op="remove_node")`.
"""

OP_TRIM = "trim"
"""An item kept its timeline span, and its source in/out moved.

No graph mutation: the edit_graph has no node for a source-level trim.
"""

OP_REPLACE = "replace"
"""A different clip took an item's place on the same row.

No graph mutation: a replacement is a remove plus an add at the timeline
level, and the graph node it affects is a planning decision, not a
timeline fact.
"""

OP_ADD = "add"
"""An item appeared on a row that already existed.

No graph mutation: the graph node a new item serves is a planning
decision the timeline cannot speak.
"""

OP_J_CUT = "j_cut"
"""An item's audio/video offset changed (left/right offset moved).

No graph mutation: a J-cut is a source-level edit the graph does not
model.
"""

CHANGE_OPS = (OP_MOVE, OP_REMOVE, OP_TRIM, OP_REPLACE, OP_ADD, OP_J_CUT)

#: The ops that ARE edit_graph mutations, and the mutation op each maps to.
_GRAPH_MUTATION_OPS = {
    OP_MOVE: "retime_block",
    OP_REMOVE: "remove_node",
}


def _tracks_by_key(tracks: Sequence[Mapping]) -> dict:
    """Index a track read by (type, index) - the row's identity."""
    return {(track.get("type"), track.get("index")): track
            for track in (tracks or ())}


def _clips_by_name(track: Mapping) -> dict:
    """Index a track's clips by name, in start order, for pairing.

    A clip name can occur twice on one row (the same source used in two
    places), so each name maps to a list and the diff pairs occurrences
    in start order - the same discipline `reel_replace_guard.diff_rows`
    uses for its (name, start, end) key.
    """
    clips: dict = {}
    for clip in sorted(track.get("clips") or (),
                       key=lambda entry: (entry.get("record_in") or 0)):
        clips.setdefault(clip.get("name") or "", []).append(clip)
    return clips


def _mutation_for(op: str, after: Mapping | None = None) -> dict | None:
    """The edit_graph mutation this change expresses, or None.

    A move is a retime_block (it carries the new timeline start); a
    removal is a remove_node. The row read carries no graph node ids, so
    the mutation names its op and its field but not a node_id - the graph
    that would supply one is built from `pipeline_data.json`.
    """
    if op == OP_MOVE:
        return {"op": "retime_block",
                "timeline_start": (after or {}).get("start")}
    if op == OP_REMOVE:
        return {"op": "remove_node"}
    return None


def diff_timelines(earlier: Sequence[Mapping], later: Sequence[Mapping]) -> list[dict]:
    """Diff two timeline reads into typed changes.

    `earlier` and `later` are `reel_read.read_tracks` outputs - lists of
    tracks, each with `clips` carrying `name`, `record_in`, `record_out`,
    `source_in_frame`, `source_out_frame`, `left_offset` and
    `right_offset`. The diff pairs clips by name within each row and
    reports every move, trim, replacement, J-cut, addition and removal.

    The pairing is by NAME, not by (name, start, end) as the replace
    guard's row diff pairs: a moved item is the same clip at a new span,
    and pairing by span would report it as a removal plus an addition,
    losing the fact that it moved. That lost fact is the defect this
    module exists to close.
    """
    changes: list[dict] = []
    earlier_tracks = _tracks_by_key(earlier or ())
    later_tracks = _tracks_by_key(later or ())

    for key in sorted(set(earlier_tracks) | set(later_tracks)):
        before = earlier_tracks.get(key)
        after = later_tracks.get(key)
        if before is None:
            changes.append({
                "op": OP_ADD, "row": _row_label(key), "item": "",
                "before": None,
                "after": {"track": after.get("name", ""),
                         "clips": len(after.get("clips") or ())},
                "mutation": None,
            })
            continue
        if after is None:
            changes.append({
                "op": OP_REMOVE, "row": _row_label(key), "item": "",
                "before": {"track": before.get("name", ""),
                           "clips": len(before.get("clips") or ())},
                "after": None,
                "mutation": _mutation_for(OP_REMOVE),
            })
            continue
        changes.extend(_diff_row(before, after))
    return changes


def _diff_row(before: Mapping, after: Mapping) -> list[dict]:
    """Diff one row's clips by name, in start order."""
    changes: list[dict] = []
    earlier_clips = _clips_by_name(before)
    later_clips = _clips_by_name(after)
    row = _row_label((before.get("type"), before.get("index")))

    for name in sorted(set(earlier_clips) | set(later_clips)):
        old_list = earlier_clips.get(name) or []
        new_list = later_clips.get(name) or []
        paired = min(len(old_list), len(new_list))
        for position in range(paired):
            changes.extend(_diff_clip(row, name, old_list[position],
                                      new_list[position]))
        for entry in old_list[paired:]:
            changes.append({
                "op": OP_REMOVE, "row": row, "item": name,
                "before": _clip_state(entry), "after": None,
                "mutation": _mutation_for(OP_REMOVE),
            })
        for entry in new_list[paired:]:
            changes.append({
                "op": OP_ADD, "row": row, "item": name,
                "before": None, "after": _clip_state(entry),
                "mutation": None,
            })
    return changes


def _diff_clip(row: str, name: str, before: Mapping,
               after: Mapping) -> list[dict]:
    """Diff one paired clip: move, trim, J-cut, or unchanged."""
    changes: list[dict] = []
    before_state = _clip_state(before)
    after_state = _clip_state(after)

    span_changed = (before_state["start"] != after_state["start"]
                    or before_state["end"] != after_state["end"])
    source_changed = (before_state.get("source_in") != after_state.get("source_in")
                      or before_state.get("source_out") != after_state.get("source_out"))
    offset_changed = (before_state.get("left_offset") != after_state.get("left_offset")
                      or before_state.get("right_offset") != after_state.get("right_offset"))

    if span_changed:
        changes.append({
            "op": OP_MOVE, "row": row, "item": name,
            "before": before_state, "after": after_state,
            "mutation": _mutation_for(OP_MOVE, after_state),
        })
    elif source_changed:
        changes.append({
            "op": OP_TRIM, "row": row, "item": name,
            "before": before_state, "after": after_state,
            "mutation": None,
        })
    if offset_changed:
        changes.append({
            "op": OP_J_CUT, "row": row, "item": name,
            "before": before_state, "after": after_state,
            "mutation": None,
        })
    return changes


def _clip_state(clip: Mapping) -> dict:
    """The comparable state of one timeline item."""
    return {
        "start": clip.get("record_in"),
        "end": clip.get("record_out"),
        "duration": clip.get("duration"),
        "source_in": clip.get("source_in_frame"),
        "source_out": clip.get("source_out_frame"),
        "left_offset": clip.get("left_offset"),
        "right_offset": clip.get("right_offset"),
    }


def _row_label(key) -> str:
    """The row's human label: `V1`, `A2` - the SOP's row name
    (`composed_edit.row_label`), so a diff's rows match the rows the
    touch-up and the timeline SOP speak of."""
    track_type, index = key
    if track_type and isinstance(index, int):
        letter = ("V" if str(track_type).lower().startswith("v") else "A")
        return f"{letter}{int(index)}"
    return str(key)


def summarize(changes: Sequence[Mapping]) -> dict:
    """Count changes by op - the captain's commit summary.

    `{"move": 11, "replace": 4, "j_cut": 2, ...}` over the change
    vocabulary, with only the ops that occurred. A commit records this
    beside its changes list, so "what did this commit do" is one read.
    """
    counts: dict = {}
    for change in changes or ():
        op = change.get("op")
        if op in CHANGE_OPS:
            counts[op] = counts.get(op, 0) + 1
    return dict(sorted(counts.items()))


CATEGORIES = ("dialogue", "sequence", "captions", "graphics", "color", "audio")
"""The creator-facing categories a change summary speaks in, in order."""


def row_categories(row: str) -> set:
    """The categories a change on this timeline row establishes.

    Conservative by design: an audio row is audio and a video row is
    picture/sequence; the rest only where the row's own name says so.
    """
    label = str(row or "").lower()
    found = set()
    if label.startswith("a"):
        found.add("audio")
    elif label.startswith("v"):
        found.add("sequence")
    for category, words in {
            "captions": ("caption", "subtitle"),
            "graphics": ("graphic", "overlay", "title"),
            "color": ("color", "colour", "grade"),
            "dialogue": ("dialogue", "speech", "voice")}.items():
        if any(word in label for word in words):
            found.add(category)
    return found


def creative_summary(changes: Sequence[Mapping], *, fps: float | None = None) -> dict:
    """Describe one recorded revision in creator-facing timeline terms.

    The diff only proves edits to timeline rows. Category claims are kept
    conservative: audio rows establish audio changes, video rows establish
    picture/sequence changes, and other categories remain explicitly
    unknown unless the row name identifies them. Timecodes are emitted only
    when a frame rate was recorded by the caller.
    """
    rows = []
    changed_categories = set()
    for change in changes or ():
        op = change.get("op")
        if op not in CHANGE_OPS:
            continue
        before, after = change.get("before"), change.get("after")
        start_values = [state.get("start") for state in (before, after)
                        if isinstance(state, Mapping)
                        and isinstance(state.get("start"), (int, float))]
        end_values = [state.get("end") for state in (before, after)
                      if isinstance(state, Mapping)
                      and isinstance(state.get("end"), (int, float))]
        row = str(change.get("row") or "")
        changed_categories.update(row_categories(row))
        record = {"op": op, "row": row, "item": change.get("item") or ""}
        if start_values:
            record["start_frame"] = min(start_values)
            if fps:
                record["timecode"] = _timecode(min(start_values), fps)
        if end_values:
            record["end_frame"] = max(end_values)
            if fps:
                record["end_timecode"] = _timecode(max(end_values), fps)
        rows.append(record)
    category_status = {c: ("changed" if c in changed_categories else "unknown")
                       for c in CATEGORIES}
    return {
        "changes": rows,
        "changed_categories": [c for c in CATEGORIES if c in changed_categories],
        "untouched_categories": [],
        "category_status": category_status,
        "summary": _creative_sentence(rows),
    }


def _timecode(frame: int | float, fps: float) -> str:
    """Format a nonnegative timeline frame as HH:MM:SS:FF."""
    from library.tools.marker_feedback import frames_to_timecode
    return frames_to_timecode(int(frame), fps)


def _creative_sentence(rows: Sequence[Mapping]) -> str:
    if not rows:
        return "No timeline changes recorded."
    ops = {row["op"] for row in rows}
    if ops <= {OP_TRIM, OP_MOVE}:
        verb = "Adjusted"
    elif OP_REPLACE in ops:
        verb = "Replaced"
    elif OP_ADD in ops:
        verb = "Added"
    elif OP_REMOVE in ops:
        verb = "Removed"
    else:
        verb = "Changed"
    if len(rows) == 1:
        row = rows[0]
        subject = row["item"] or row["row"]
        when = row.get("timecode")
        return f"{verb} {subject}" + (f" at {when}." if when else ".")
    locations = [row.get("timecode") for row in rows if row.get("timecode")]
    suffix = f" across {len(locations)} timeline regions" if locations else f" across {len(rows)} timeline changes"
    if locations:
        suffix += f" starting at {', '.join(locations[:4])}"
    return verb + suffix + "."


class Uninvertible(ValueError):
    """A change the record cannot invert.

    Raised naming the op and item, so a revert names what it cannot
    undo instead of silently dropping it.
    """


def _is_clip_state(state) -> bool:
    """A per-item span/source record, not a whole-row summary.

    A diff's `before`/`after` is either a `_clip_state` (start, end,
    source in/out, offsets) or, for a whole-row add/remove, a
    `{"track", "clips"}` summary that names a clip COUNT, not the
    clips. Only the first carries enough to invert.
    """
    return isinstance(state, Mapping) and "start" in state


def invert_changes(changes: Sequence[Mapping]) -> list[dict]:
    """The inverse of a diff: the changes that undo it.

    A move/trim/replace/J-cut swaps its before and after (a move's
    `retime_block` mutation is re-pointed at the restored start); an
    add becomes a remove and a remove an add. A whole-row add/remove
    (`item` is empty, the state a `{"track", "clips"}` summary) and
    anything malformed raises `Uninvertible` naming the change: the
    record does not carry the clips, so no inverse exists, and a
    revert built on this must refuse rather than claim a clean undo.

    Inverting twice returns the original changes.
    """
    inverse: list[dict] = []
    for change in changes or ():
        change = dict(change)
        op = change.get("op")
        before, after = change.get("before"), change.get("after")
        if op in (OP_MOVE, OP_TRIM, OP_REPLACE, OP_J_CUT):
            if not (_is_clip_state(before) and _is_clip_state(after)):
                raise Uninvertible(
                    f"cannot invert {op} of {change.get('item')!r} on "
                    f"{change.get('row')}: it has no before-and-after "
                    f"clip state")
            change["before"], change["after"] = (
                dict(after), dict(before))
            if op == OP_MOVE:
                change["mutation"] = _mutation_for(
                    OP_MOVE, change["after"])
        elif op == OP_ADD and before is None and _is_clip_state(after):
            change.update(op=OP_REMOVE, before=dict(after), after=None,
                          mutation=_mutation_for(OP_REMOVE))
        elif op == OP_REMOVE and after is None and _is_clip_state(before):
            change.update(op=OP_ADD, before=None, after=dict(before),
                          mutation=None)
        else:
            raise Uninvertible(
                f"cannot invert {op} of {change.get('item')!r} on "
                f"{change.get('row')}: the record does not carry the "
                f"clips it would restore")
        inverse.append(change)
    return inverse


_STATE_KEYS = ("start", "end", "duration", "source_in", "source_out",
               "left_offset", "right_offset")
"""The comparable fields of one timeline item, in `_clip_state` order."""


def _same_state(first, second) -> bool:
    """Two clip states agree on every comparable field."""
    return all((first or {}).get(key) == (second or {}).get(key)
               for key in _STATE_KEYS)


def clip_states(tracks) -> dict:
    """Every clip's comparable state, keyed by `(row, item)`.

    Values are LISTS in start order: a clip appearing twice on one
    row keeps both states, so a proof never silently picks one. A
    tracks value that does not read as track rows raises
    `ValueError` naming what failed, so the caller refuses the
    destination instead of proving against garbage.
    """
    if isinstance(tracks, (str, bytes, Mapping)) or not isinstance(
            tracks, Sequence):
        raise ValueError(
            "destination tracks do not read as a list of track rows")
    states: dict = {}
    for track in tracks:
        if not isinstance(track, Mapping):
            raise ValueError(
                "destination tracks do not read as a list of track rows")
        clips = track.get("clips") or ()
        if isinstance(clips, (str, bytes, Mapping)) or not isinstance(
                clips, Sequence):
            raise ValueError(
                "destination tracks do not read as a list of track rows")
        ordered = sorted(
            (clip for clip in clips if isinstance(clip, Mapping)),
            key=lambda entry: (entry.get("record_in") or 0))
        row = _row_label((track.get("type"), track.get("index")))
        for clip in ordered:
            states.setdefault((row, clip.get("name") or ""),
                              []).append(_clip_state(clip))
    return states


def changes_hold(changes: Sequence[Mapping], tracks) -> list:
    """The picked changes that do NOT hold against current track reads.

    Returns problem strings, empty when every change still applies:
    a state change (move, trim, replace, J-cut, remove) needs its
    `before` state present exactly once; an add needs its clip
    absent. A whole-row change (`item` is empty, the state a clip
    count, not the clips) and a clip appearing twice on one row are
    unprovable and reported, never guessed. Raises `ValueError` when
    the tracks do not read as track rows.
    """
    states = clip_states(tracks)
    problems: list = []
    for change in changes or ():
        op = change.get("op")
        row, item = change.get("row"), change.get("item")
        before = change.get("before")
        if not item:
            problems.append(
                f"{op} on {row}: the record names a clip count, not "
                f"the clips - no current state can prove it")
            continue
        found = states.get((row, item), [])
        if op == OP_ADD:
            if found:
                problems.append(
                    f"add {item!r} on {row}: the clip is already there")
            continue
        if not _is_clip_state(before):
            problems.append(
                f"{op} {item!r} on {row}: the change carries no "
                f"before state to prove")
            continue
        if not found:
            problems.append(
                f"{op} {item!r} on {row}: the clip is gone from the "
                f"current reads")
        elif len(found) > 1:
            problems.append(
                f"{op} {item!r} on {row}: the clip appears "
                f"{len(found)} times - the proof cannot tell which "
                f"the change meant")
        elif not _same_state(found[0], before):
            problems.append(
                f"{op} {item!r} on {row}: the clip no longer reads "
                f"as the change left it")
    return problems
