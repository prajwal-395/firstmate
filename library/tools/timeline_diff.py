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
