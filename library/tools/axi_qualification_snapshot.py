"""The pre- and post-operation snapshots a qualification run compares.

AXI-030 asks for two things of a qualification run: that the operation
did EXACTLY what was requested, and that it changed NOTHING ELSE. A
receipt that says "committed" answers the first question badly - it
answers "the write landed", not "the write landed and nothing else
moved".

So a qualification run takes a snapshot before the write and one after,
and this module turns the two into a verdict:

* `requested_changes` - the differences the operation ASKED for, each
  tied to the operation that asked for it;
* `unintended_modifications` - every difference no operation asked for.

A difference nobody asked for is a defect even when the operation
succeeded: a delete that also moved a clip, a property write that also
re-spanned its item, a placement that also re-grades what it covers.

The comparison is pure: it takes two plain snapshots and the operations
that were run, and answers. Nothing here reads Resolve - the live read
is `library/tools/reel_read.py` (the rows) and
`library/tools/axi_qualification_state.py` (the settings), and the
snapshot is what those two produce.

`tests/unit/resolve/test_axi_qualification_snapshot.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: The fields of one item that a comparison judges. A field that is not
#: here cannot change without the comparison noticing.
ITEM_FIELDS: tuple[str, ...] = (
    "name",
    "record_in",
    "record_out",
    "source_in_frame",
    "source_out_frame",
    "media_type",
    "linked_unique_ids",
    "enabled",
    "speed",
    "transform",
    "color",
    "fusion",
)

#: The operations that REMOVE the item they name.
REMOVE_OPS = frozenset({"clip.delete", "clip.remove"})

#: The operations that PLACE a new item. Their `unique_id` names the
#: item they create, so the item is absent before the write.
INSERT_OPS = frozenset({"clip.insert", "broll.insert"})

#: The operations that WRITE one property on the item they name.
PROPERTY_OPS = frozenset({"clip.set_property", "clip.set_enabled"})

#: The operations that change the SOURCE range of the item they name.
SOURCE_RANGE_OPS = frozenset({"clip.trim"})

#: The operations that change the RECORD range of the item they name.
RECORD_RANGE_OPS = frozenset({"clip.move"})

#: The operations that add a row.
TRACK_OPS = frozenset({"track.add"})


def item_state(clip: Mapping) -> dict:
    """One item, as the fields a comparison judges."""
    fusion = clip.get("fusion")
    if isinstance(fusion, Mapping):
        fusion = {"comp_count": fusion.get("comp_count", 0),
                  "comp_names": list(fusion.get("comp_names") or ())}
    else:
        fusion = {"comp_count": 0, "comp_names": []}
    return {
        "name": clip.get("name", ""),
        "record_in": clip.get("record_in"),
        "record_out": clip.get("record_out"),
        "source_in_frame": clip.get("source_in_frame"),
        "source_out_frame": clip.get("source_out_frame"),
        "media_type": clip.get("media_type"),
        "linked_unique_ids": sorted(str(uid) for uid in
                                    (clip.get("linked_unique_ids") or ())),
        "enabled": clip.get("enabled"),
        "speed": clip.get("speed"),
        "transform": dict(clip.get("transform") or {}),
        "color": dict(clip.get("color") or {}),
        "fusion": fusion,
    }


def snapshot(tracks: Sequence[Mapping], settings: Mapping | None = None
             ) -> dict:
    """A comparable snapshot of a timeline.

    `tracks` is the row shape `reel_read.read_tracks` produces and
    `settings` the timeline's own settings. The snapshot keeps what a
    comparison needs and nothing that changes without meaning - a read
    timestamp, for instance, would make every read a change.
    """
    rows = []
    for track in tracks or ():
        rows.append({
            "type": str(track.get("type") or ""),
            "index": track.get("index"),
            "name": str(track.get("name") or ""),
            "clips": [
                {"unique_id": str(clip.get("unique_id") or ""),
                 "track_type": str(track.get("type") or ""),
                 "track_index": track.get("index"),
                 **item_state(clip)}
                for clip in (track.get("clips") or ())
            ],
        })
    return {"tracks": rows, "settings": dict(settings or {})}


def _by_uid(state: Mapping) -> dict:
    items = {}
    for track in state.get("tracks", ()):
        for clip in track.get("clips", ()):
            items[str(clip.get("unique_id") or "")] = {
                **clip, "track_type": track.get("type"),
                "track_index": track.get("index")}
    return items


def _track_keys(state: Mapping) -> dict:
    return {(str(track.get("type") or ""), track.get("index")): track
            for track in state.get("tracks", ())}


def changes(before: Mapping, after: Mapping) -> dict:
    """Every difference between two snapshots, by item identity."""
    old = _by_uid(before)
    new = _by_uid(after)
    added = sorted(uid for uid in new if uid and uid not in old)
    removed = sorted(uid for uid in old if uid and uid not in new)
    changed = {}
    for uid in sorted(set(old) & set(new)):
        if not uid:
            continue
        fields = [field for field in ITEM_FIELDS
                  if old[uid].get(field) != new[uid].get(field)]
        if fields:
            changed[uid] = fields
    old_tracks = _track_keys(before)
    new_tracks = _track_keys(after)
    added_tracks = sorted(
        key for key in new_tracks if key not in old_tracks)
    removed_tracks = sorted(
        key for key in old_tracks if key not in new_tracks)
    return {"added": added, "removed": removed, "changed": changed,
            "added_tracks": added_tracks,
            "removed_tracks": removed_tracks}


def _operation_uid(operation: Mapping) -> str:
    return str(operation.get("unique_id")
               or operation.get("resolve_item_id") or "")


def _expected_field(op_id: str, operation: Mapping) -> tuple[str, ...]:
    """The item fields one operation is allowed to change."""
    if op_id in PROPERTY_OPS:
        return ("transform", "enabled")
    if op_id in SOURCE_RANGE_OPS:
        return ("source_in_frame", "source_out_frame", "record_in",
                "record_out")
    if op_id in RECORD_RANGE_OPS:
        return ("record_in", "record_out")
    return ()


def _property_field(operation: Mapping) -> str:
    """The item field a property write lands in."""
    key = str(operation.get("key") or "")
    if key in ("enabled", "clip_enabled"):
        return "enabled"
    return "transform"


def compare(before: Mapping, after: Mapping,
            operations: Sequence[Mapping] = ()) -> dict:
    """What the operation asked for, and what it changed besides.

    `operations` are the algebra operations the run executed. Each one
    says what it intends; every difference that no operation accounts
    for is an unintended modification, and a difference an operation
    accounts for but changed MORE fields than it asked for is reported
    as an unintended modification too.
    """
    delta = changes(before, after)
    requested: list[dict] = []
    unintended: list[dict] = []

    explained_added: set[str] = set()
    explained_removed: set[str] = set()
    explained_changed: dict[str, set[str]] = {}

    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        op_id = str(operation.get("op") or "")
        uid = _operation_uid(operation)
        if op_id in INSERT_OPS:
            if uid and uid in delta["added"]:
                explained_added.add(uid)
                requested.append({"op": op_id, "unique_id": uid,
                                  "change": "placed", "fields": []})
            continue
        if op_id in REMOVE_OPS:
            if uid and uid in delta["removed"]:
                explained_removed.add(uid)
                requested.append({"op": op_id, "unique_id": uid,
                                  "change": "removed", "fields": []})
            continue
        if op_id in TRACK_OPS:
            for key in delta["added_tracks"]:
                requested.append({"op": op_id, "unique_id": "",
                                  "change": "track_added",
                                  "fields": [f"{key[0]}{key[1]}"]})
            continue
        if not uid:
            continue
        fields = delta["changed"].get(uid)
        if not fields:
            continue
        allowed = set(_expected_field(op_id, operation))
        explained_changed.setdefault(uid, set()).update(
            field for field in fields if field in allowed)
        for field in fields:
            if field in allowed:
                requested.append({"op": op_id, "unique_id": uid,
                                  "change": "changed", "fields": [field]})
            else:
                unintended.append({"op": op_id, "unique_id": uid,
                                   "change": "changed", "fields": [field]})

    for uid in delta["added"]:
        if uid not in explained_added:
            unintended.append({"op": "", "unique_id": uid,
                               "change": "added", "fields": []})
    for uid in delta["removed"]:
        if uid not in explained_removed:
            unintended.append({"op": "", "unique_id": uid,
                               "change": "removed", "fields": []})
    for key in delta["added_tracks"]:
        if not any(entry["change"] == "track_added"
                   for entry in requested):
            unintended.append({"op": "", "unique_id": "",
                               "change": "track_added",
                               "fields": [f"{key[0]}{key[1]}"]})
    for key in delta["removed_tracks"]:
        unintended.append({"op": "", "unique_id": "",
                           "change": "track_removed",
                           "fields": [f"{key[0]}{key[1]}"]})

    return {
        "requested_changes": requested,
        "unintended_modifications": unintended,
        "summary": {
            "added": len(delta["added"]),
            "removed": len(delta["removed"]),
            "changed": len(delta["changed"]),
            "requested": len(requested),
            "unintended": len(unintended),
        },
    }


def preserved(verdict: Mapping) -> bool:
    """Whether the run changed nothing it was not asked to change."""
    return not (verdict.get("unintended_modifications") or [])
