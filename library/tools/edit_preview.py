"""Preview what an edit program will do before it executes.

The gap this closes (gap map C-05): an edit program executes blind; the
first evidence it was wrong is a rendered export. The intent review
(`ren spec intent prepare/resolve`) judges the built timeline and the
export AFTER the fact. This module computes, in pure Python over a
recorded generation snapshot, what each op will change - the affected
spans, the before/after values and the expected duration change - so
the editor can approve or revise a program before it runs.

The preview is a third artifact beside the spec and the intent review.
It needs no Resolve read: the `timeline_shadow` store already holds
generations, and `edit_patch.validate` already computes spans from a
base. Ripple and global ops compute their span arithmetic in Python
(`composed_edit.plan_ripple` already does exactly this).

What the preview computes per op
--------------------------------
- `affected_span`: `[start, end)` in timeline frames, from the algebra's
  span function against the working state.
- `before` / `after`: the fields the op changes, read from the working
  state before and after the op is applied.
- `duration_change`: how the op changes the timeline's duration, in
  frames. Local ops change nothing; ripple ops shift the tail.

The working state is a deep copy of the base snapshot; each op's effect
is applied in sequence, so a later op sees the earlier ops' results -
the same order `apply_patch` commits them in.

`tests/unit/resolve/test_edit_preview.py`.
"""

from __future__ import annotations

import copy

from library.tools import edit_algebra
from library.tools.edit_algebra import _clip, _timeline_marker

#: The preview artifact's format version.
PREVIEW_FORMAT = "edit_program_preview/1"


class PreviewError(ValueError):
    """A program cannot be previewed against the given base."""


def _working_state(base: dict) -> dict:
    """A deep copy of the snapshot the preview applies ops against."""
    return copy.deepcopy(base)


def _duration(state: dict) -> int:
    return int(state["end_frame"]) - int(state["start_frame"])


def _clip_state(state: dict, unique_id: str) -> dict | None:
    return _clip(state, unique_id)


def _track_state(state: dict, track_type: str, index: int) -> dict | None:
    for track in state.get("tracks", []):
        if (track["type"] == track_type
                and int(track["index"]) == int(index)):
            return track
    return None


def _remove_clip(state: dict, unique_id: str) -> None:
    for track in state.get("tracks", []):
        track["clips"] = [c for c in track.get("clips", [])
                          if c["unique_id"] != unique_id]


def _add_clip(state: dict, clip: dict) -> None:
    track = _track_state(state, clip["track_type"], int(clip["track_index"]))
    if track is None:
        state["tracks"].append({
            "type": clip["track_type"],
            "index": int(clip["track_index"]),
            "name": clip.get("track_name", ""),
            "clips": [clip],
        })
    else:
        track.setdefault("clips", []).append(clip)


def _marker_at(state: dict, frame: int) -> dict | None:
    return _timeline_marker(state, frame)


def _remove_marker(state: dict, frame: int) -> None:
    state["markers"]["timeline"] = [
        m for m in state.get("markers", {}).get("timeline", [])
        if int(m["frame"]) != int(frame)]


def _clip_marker_at(state: dict, unique_id: str, frame: int) -> dict | None:
    clip = _clip_state(state, unique_id)
    if clip is None:
        return None
    for marker in clip.get("markers", []):
        if int(marker["frame"]) == int(frame):
            return marker
    return None


def _remove_clip_marker(state: dict, unique_id: str, frame: int) -> None:
    clip = _clip_state(state, unique_id)
    if clip is None:
        return
    clip["markers"] = [m for m in clip.get("markers", [])
                        if int(m["frame"]) != int(frame)]


# ── Effect functions: apply one op to the working state ────────────────
#
# Each returns `(before, after)` - the fields the op changes, read from
# the working state before and after. The effect is applied in place.


def _effect_marker_add(op, state):
    frame = int(op["frame"])
    before = _marker_at(state, frame)
    state["markers"]["timeline"].append({
        "frame": frame, "color": op["color"], "name": op["name"],
        "note": op.get("note", ""), "duration": int(op.get("duration", 1)),
        "custom_data": op.get("custom_data", ""),
    })
    after = _marker_at(state, frame)
    return (None if before is None else dict(before), dict(after))


def _effect_marker_delete(op, state):
    frame = int(op["frame"])
    before = _marker_at(state, frame)
    _remove_marker(state, frame)
    after = _marker_at(state, frame)
    return (dict(before) if before else None, None if after is None else dict(after))


def _effect_clip_marker_add(op, state):
    uid, frame = op["unique_id"], int(op["frame"])
    before = _clip_marker_at(state, uid, frame)
    clip = _clip_state(state, uid)
    clip.setdefault("markers", []).append({
        "frame": frame, "color": op["color"], "name": op["name"],
        "note": op.get("note", ""), "duration": int(op.get("duration", 1)),
        "custom_data": op.get("custom_data", ""),
    })
    after = _clip_marker_at(state, uid, frame)
    return (None if before is None else dict(before), dict(after))


def _effect_clip_marker_delete(op, state):
    uid, frame = op["unique_id"], int(op["frame"])
    before = _clip_marker_at(state, uid, frame)
    _remove_clip_marker(state, uid, frame)
    after = _clip_marker_at(state, uid, frame)
    return (dict(before) if before else None, None if after is None else dict(after))


def _effect_clip_delete(op, state):
    uid = op["unique_id"]
    before = _clip_state(state, uid)
    _remove_clip(state, uid)
    return (dict(before) if before else None, None)


def _effect_clip_insert(op, state):
    uid = op["unique_id"]
    clip = {
        "unique_id": uid, "name": op.get("name", ""),
        "track_type": op["track_type"],
        "track_index": int(op["track_index"]),
        "record_in": int(op["record_in"]),
        "record_out": int(op["record_out"]),
        "duration": int(op["record_out"]) - int(op["record_in"]),
        "source_in_frame": op.get("source_in_frame", 0),
        "source_out_frame": op.get("source_out_frame", 0),
        "transform": {}, "enabled": True, "speed": 100,
        "keyframes": {}, "markers": [],
    }
    _add_clip(state, clip)
    after = _clip_state(state, uid)
    return (None, dict(after) if after else None)


def _effect_broll_insert(op, state):
    # The cover is a clip.insert-shaped placement: translate this op's
    # marked-range keys onto the placement effect's, then reuse it.
    return _effect_clip_insert({
        "unique_id": op["unique_id"], "track_type": op["track_type"],
        "track_index": op["track_index"], "record_in": op["record_in"],
        "record_out": op["record_out"], "name": op.get("clip", ""),
        "source_in_frame": op.get("source_in", 0),
        "source_out_frame": op.get("source_out", 0),
    }, state)


def _effect_clip_split(op, state):
    uid, at = op["unique_id"], int(op["at_frame"])
    before = _clip_state(state, uid)
    if before is None:
        return (None, None)
    first = copy.deepcopy(before)
    first["record_out"] = at
    first["duration"] = at - int(before["record_in"])
    second = copy.deepcopy(before)
    second["unique_id"] = uid + "-split-1"
    second["record_in"] = at
    second["duration"] = int(before["record_out"]) - at
    _remove_clip(state, uid)
    _add_clip(state, first)
    _add_clip(state, second)
    after = [_clip_state(state, uid), _clip_state(state, uid + "-split-1")]
    return (dict(before), [dict(c) if c else None for c in after])


def _effect_clip_join(op, state):
    uid, other = op["unique_id"], op["other_unique_id"]
    before_self = _clip_state(state, uid)
    before_other = _clip_state(state, other)
    if before_self is None or before_other is None:
        return (None, None)
    joined = copy.deepcopy(before_self)
    joined["record_out"] = max(int(before_self["record_out"]),
                               int(before_other["record_out"]))
    joined["duration"] = (joined["record_out"]
                          - int(joined["record_in"]))
    _remove_clip(state, uid)
    _remove_clip(state, other)
    _add_clip(state, joined)
    after = _clip_state(state, uid)
    return ({"self": dict(before_self), "other": dict(before_other)},
            dict(after) if after else None)


def _effect_clip_trim(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"record_in": clip["record_in"], "record_out": clip["record_out"]}
    clip["record_in"] = int(op["in_frame"])
    clip["record_out"] = int(op["out_frame"])
    clip["duration"] = int(op["out_frame"]) - int(op["in_frame"])
    after = {"record_in": clip["record_in"], "record_out": clip["record_out"]}
    return (before, after)


def _effect_clip_ripple(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    delta = int(op["delta_frames"])
    before = {"record_in": clip["record_in"], "record_out": clip["record_out"]}
    for track in state.get("tracks", []):
        for c in track.get("clips", []):
            if int(c["record_in"]) >= int(clip["record_in"]):
                c["record_in"] = int(c["record_in"]) + delta
                c["record_out"] = int(c["record_out"]) + delta
    state["end_frame"] = int(state["end_frame"]) + delta
    after = {"record_in": clip["record_in"], "record_out": clip["record_out"]}
    return (before, after)


def _effect_clip_roll(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    delta = int(op["delta_frames"])
    before = {"source_in_frame": clip.get("source_in_frame"),
              "source_out_frame": clip.get("source_out_frame")}
    clip["source_in_frame"] = int(clip.get("source_in_frame", 0)) + delta
    clip["source_out_frame"] = int(clip.get("source_out_frame", 0)) + delta
    after = {"source_in_frame": clip["source_in_frame"],
             "source_out_frame": clip["source_out_frame"]}
    return (before, after)


def _effect_clip_slip(op, state):
    return _effect_clip_roll(op, state)


def _effect_clip_slide(op, state):
    uid, to_frame = op["unique_id"], int(op["to_frame"])
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"record_in": clip["record_in"], "record_out": clip["record_out"]}
    duration = int(clip["duration"])
    clip["record_in"] = to_frame
    clip["record_out"] = to_frame + duration
    after = {"record_in": clip["record_in"], "record_out": clip["record_out"]}
    return (before, after)


def _effect_clip_move(op, state):
    return _effect_clip_slide(op, state)


def _effect_clip_replace(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"source_file": clip.get("source_file")}
    clip["source_file"] = op["source_file"]
    after = {"source_file": clip["source_file"]}
    return (before, after)


def _effect_clip_set_enabled(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"enabled": clip.get("enabled")}
    clip["enabled"] = bool(op["enabled"])
    after = {"enabled": clip["enabled"]}
    return (before, after)


def _effect_clip_link(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"linked": clip.get("linked", [])}
    clip["linked"] = list(op["with_unique_ids"])
    after = {"linked": clip["linked"]}
    return (before, after)


def _effect_clip_unlink(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"linked": clip.get("linked", [])}
    clip["linked"] = []
    after = {"linked": clip["linked"]}
    return (before, after)


def _effect_clip_smart_reframe(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {key: clip["transform"].get(key) for key in op["keys"]}
    for key in op["keys"]:
        clip["transform"][key] = op["value"].get(key, clip["transform"].get(key))
    after = {key: clip["transform"].get(key) for key in op["keys"]}
    return (before, after)


def _effect_clip_set_property(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {op["key"]: clip["transform"].get(op["key"])}
    clip.setdefault("transform", {})[op["key"]] = op["value"]
    after = {op["key"]: clip["transform"][op["key"]]}
    return (before, after)


def _effect_retime(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"speed": clip.get("speed")}
    clip["speed"] = op["percent"]
    after = {"speed": clip["speed"]}
    return (before, after)


def _effect_retime_segments(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"speed": clip.get("speed")}
    segments = sorted(op["segments"], key=lambda s: int(s["record_frame"]))
    pieces = []
    for i, seg in enumerate(segments):
        piece = copy.deepcopy(clip)
        piece["unique_id"] = f"{uid}-split-{i}"
        piece["record_in"] = int(seg["record_frame"])
        piece["speed"] = seg["percent"]
        pieces.append(piece)
    _remove_clip(state, uid)
    for piece in pieces:
        _add_clip(state, piece)
    after = [{"unique_id": p["unique_id"], "speed": p["speed"]}
             for p in pieces]
    return (before, after)


def _effect_picture_keyframe(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {op["param"]: clip.get("keyframes", {}).get(op["param"], [])}
    clip.setdefault("keyframes", {})[op["param"]] = op["keyframes"]
    after = {op["param"]: clip["keyframes"][op["param"]]}
    return (before, after)


def _effect_voice_isolation(op, state):
    track = _track_state(state, "audio", int(op["track_index"]))
    before = {"isolated": track.get("isolated") if track else None}
    if track is not None:
        track["isolated"] = True
    after = {"isolated": track.get("isolated") if track else None}
    return (before, after)


def _effect_audio_gain(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"gain": clip.get("gain")}
    clip["gain"] = op["db"]
    after = {"gain": clip["gain"]}
    return (before, after)


def _effect_audio_pan(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"pan": clip.get("pan")}
    clip["pan"] = op["pan"]
    after = {"pan": clip["pan"]}
    return (before, after)


def _effect_audio_fade(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"fade_in": clip.get("fade_in"), "fade_out": clip.get("fade_out")}
    clip["fade_in"] = op["fade_in"]
    clip["fade_out"] = op["fade_out"]
    after = {"fade_in": clip["fade_in"], "fade_out": clip["fade_out"]}
    return (before, after)


def _effect_audio_automation(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {op["param"]: clip.get("automation", {}).get(op["param"], [])}
    clip.setdefault("automation", {})[op["param"]] = op["keyframes"]
    after = {op["param"]: clip["automation"][op["param"]]}
    return (before, after)


def _effect_clip_lut(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"lut": clip.get("lut")}
    clip["lut"] = op["lut"]
    after = {"lut": clip["lut"]}
    return (before, after)


def _effect_grade(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"grade": clip.get("grade")}
    clip["grade"] = op["grade"]
    after = {"grade": clip["grade"]}
    return (before, after)


def _effect_grade_node_set(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {int(op["node_index"]): clip.get("grade_nodes", {}).get(
        int(op["node_index"]))}
    clip.setdefault("grade_nodes", {})[int(op["node_index"])] = op["params"]
    after = {int(op["node_index"]): clip["grade_nodes"][int(op["node_index"])]}
    return (before, after)


def _effect_mask(op, state, change):
    """One matte's (before, after), each `None` where it is absent."""
    clip = _clip_state(state, op["unique_id"])
    if clip is None:
        return (None, None)
    mattes = clip.setdefault("mattes", {})
    before = mattes.get(op["matte"])
    after = change(dict(before) if before is not None else None)
    if after is None:
        mattes.pop(op["matte"], None)
    else:
        mattes[op["matte"]] = after
    return (before, after)


def _effect_mask_create(op, state):
    return _effect_mask(op, state, lambda _m: {"geometry": {},
                                               "keyframes": []})


def _effect_mask_delete(op, state):
    return _effect_mask(op, state, lambda _m: None)


def _effect_mask_edit(op, state):
    def change(matte):
        matte = matte or {"geometry": {}, "keyframes": []}
        return {**matte, "geometry": {**matte["geometry"], **op["geometry"]}}
    return _effect_mask(op, state, change)


def _effect_mask_track(op, state):
    def change(matte):
        matte = matte or {"geometry": {}, "keyframes": []}
        return {**matte, "keyframes": list(op["track"]["keyframes"])}
    return _effect_mask(op, state, change)


def _effect_multicam_angle(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"angle": clip.get("angle")}
    clip["angle"] = op["angle_index"]
    after = {"angle": clip["angle"]}
    return (before, after)


def _effect_compound_create(op, state):
    uid = op["unique_id"]
    before = [_clip_state(state, child) for child in op["child_unique_ids"]]
    compound = {
        "unique_id": uid, "name": "compound",
        "track_type": "video", "track_index": 1,
        "record_in": min(int(c["record_in"]) for c in before if c),
        "record_out": max(int(c["record_out"]) for c in before if c),
        "duration": max(int(c["record_out"]) for c in before if c)
        - min(int(c["record_in"]) for c in before if c),
        "compound": list(op["child_unique_ids"]),
        "transform": {}, "enabled": True, "speed": 100,
        "keyframes": {}, "markers": [],
    }
    for child in op["child_unique_ids"]:
        _remove_clip(state, child)
    _add_clip(state, compound)
    after = _clip_state(state, uid)
    return ([dict(c) if c else None for c in before],
            dict(after) if after else None)


def _effect_compound_flatten(op, state):
    uid = op["unique_id"]
    before = _clip_state(state, uid)
    if before is None:
        return (None, None)
    children = before.get("compound", [])
    _remove_clip(state, uid)
    for child_uid in children:
        child = _clip_state(state, child_uid)
        if child is not None:
            _add_clip(state, copy.deepcopy(child))
    return (dict(before), None)


def _effect_track_add(op, state):
    before = None
    state["tracks"].append({
        "type": op["track_type"], "index": len(state["tracks"]) + 1,
        "name": op["name"], "clips": [],
    })
    after = state["tracks"][-1]
    return (before, dict(after))


def _effect_track_delete(op, state):
    track = _track_state(state, op["track_type"], int(op["track_index"]))
    before = dict(track) if track else None
    state["tracks"] = [t for t in state["tracks"]
                       if not (t["type"] == op["track_type"]
                               and int(t["index"]) == int(op["track_index"]))]
    after = _track_state(state, op["track_type"], int(op["track_index"]))
    return (before, None if after is None else dict(after))


def _effect_track_rename(op, state):
    track = _track_state(state, op["track_type"], int(op["track_index"]))
    if track is None:
        return (None, None)
    before = {"name": track["name"]}
    track["name"] = op["name"]
    after = {"name": track["name"]}
    return (before, after)


def _effect_track_reorder(op, state):
    track = _track_state(state, op["track_type"], int(op["from_index"]))
    if track is None:
        return (None, None)
    before = {"index": track["index"]}
    track["index"] = int(op["to_index"])
    after = {"index": track["index"]}
    return (before, after)


def _effect_overlay_add(op, state):
    before = None
    clip = {
        "unique_id": f"overlay-{op['row']}-{op['record_frame']}",
        "name": "overlay", "track_type": "video",
        "track_index": int(op["row"]),
        "record_in": int(op["record_frame"]),
        "record_out": int(op["record_frame"]) + int(op.get("duration", 1)),
        "duration": int(op.get("duration", 1)),
        "overlay": True, "transform": {}, "enabled": True,
        "speed": 100, "keyframes": {}, "markers": [],
    }
    _add_clip(state, clip)
    after = _clip_state(state, clip["unique_id"])
    return (before, dict(after) if after else None)


def _effect_overlay_remove(op, state):
    uid = op["unique_id"]
    before = _clip_state(state, uid)
    _remove_clip(state, uid)
    return (dict(before) if before else None, None)


def _effect_overlay_swap_pixels(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"file": clip.get("overlay_file")}
    clip["overlay_file"] = op["file"]
    after = {"file": clip["overlay_file"]}
    return (before, after)


def _effect_overlay_resize(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"width": clip.get("overlay_width"),
              "height": clip.get("overlay_height")}
    clip["overlay_width"] = op["width"]
    clip["overlay_height"] = op["height"]
    after = {"width": clip["overlay_width"], "height": clip["overlay_height"]}
    return (before, after)


def _effect_overlay_position(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"dx": clip.get("overlay_dx", 0),
              "dy": clip.get("overlay_dy", 0)}
    clip["overlay_dx"] = before["dx"] + op["dx"]
    clip["overlay_dy"] = before["dy"] + op["dy"]
    after = {"dx": clip["overlay_dx"], "dy": clip["overlay_dy"]}
    return (before, after)


def _effect_overlay_entry_motion(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"entry_motion": clip.get("entry_motion")}
    clip["entry_motion"] = op["frames"]
    after = {"entry_motion": clip["entry_motion"]}
    return (before, after)


def _effect_transform_override(op, state):
    return _effect_clip_set_property(op, state)


def _effect_span_retime(op, state):
    return _effect_retime(op, state)


def _effect_drop_fragment(op, state):
    return _effect_clip_delete(op, state)


def _effect_caption_fix(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"caption": clip.get("caption")}
    clip["caption"] = op["text"]
    after = {"caption": clip["caption"]}
    return (before, after)


def _effect_redraw_closer(op, state):
    uid = op["unique_id"]
    clip = _clip_state(state, uid)
    if clip is None:
        return (None, None)
    before = {"record_in": clip["record_in"]}
    clip["record_in"] = int(op["start"])
    clip["duration"] = int(clip["record_out"]) - int(op["start"])
    after = {"record_in": clip["record_in"]}
    return (before, after)


#: The effect each op has on the working state. Plan-level ops have no
#: timeline effect; they are delivered to a planner.
_EFFECTS = {
    "marker.add": _effect_marker_add,
    "marker.delete": _effect_marker_delete,
    "clip_marker.add": _effect_clip_marker_add,
    "clip_marker.delete": _effect_clip_marker_delete,
    "clip.delete": _effect_clip_delete,
    "clip.insert": _effect_clip_insert,
    "broll.insert": _effect_broll_insert,
    "clip.split": _effect_clip_split,
    "clip.join": _effect_clip_join,
    "clip.trim": _effect_clip_trim,
    "clip.ripple": _effect_clip_ripple,
    "clip.roll": _effect_clip_roll,
    "clip.slip": _effect_clip_slip,
    "clip.slide": _effect_clip_slide,
    "clip.move": _effect_clip_move,
    "clip.replace": _effect_clip_replace,
    "clip.set_enabled": _effect_clip_set_enabled,
    "clip.link": _effect_clip_link,
    "clip.unlink": _effect_clip_unlink,
    "clip.smart_reframe": _effect_clip_smart_reframe,
    "clip.set_property": _effect_clip_set_property,
    "retime": _effect_retime,
    "retime.segments": _effect_retime_segments,
    "picture.keyframe": _effect_picture_keyframe,
    "voice_isolation": _effect_voice_isolation,
    "audio.gain": _effect_audio_gain,
    "audio.pan": _effect_audio_pan,
    "audio.fade": _effect_audio_fade,
    "audio.automation": _effect_audio_automation,
    "clip_lut": _effect_clip_lut,
    "grade": _effect_grade,
    "grade.node_set": _effect_grade_node_set,
    "mask.create": _effect_mask_create,
    "mask.delete": _effect_mask_delete,
    "mask.edit": _effect_mask_edit,
    "mask.track": _effect_mask_track,
    "multicam.angle": _effect_multicam_angle,
    "multicam.select_angle": _effect_multicam_angle,
    "compound.create": _effect_compound_create,
    "compound.flatten": _effect_compound_flatten,
    "track.add": _effect_track_add,
    "track.delete": _effect_track_delete,
    "track.rename": _effect_track_rename,
    "track.reorder": _effect_track_reorder,
    "overlay.add": _effect_overlay_add,
    "overlay.remove": _effect_overlay_remove,
    "overlay.swap_pixels": _effect_overlay_swap_pixels,
    "overlay.replace": _effect_overlay_swap_pixels,
    "overlay.resize": _effect_overlay_resize,
    "overlay.position": _effect_overlay_position,
    "overlay.entry_motion": _effect_overlay_entry_motion,
    "transform_override": _effect_transform_override,
    "span_retime": _effect_span_retime,
    "drop_fragment": _effect_drop_fragment,
    "caption_fix": _effect_caption_fix,
    "redraw_closer": _effect_redraw_closer,
}

#: Ops delivered to a planner: no timeline effect to preview.
_PLAN_OPS = frozenset({
    "angle_plan", "plan_change", "transition", "shot_selection",
    "speech_selection", "story_pacing", "music_selection", "audio_mix",
    "look", "visual_effect", "sound_effect", "subject_effect",
    "motion_graphic", "brand_asset", "end_card",
})


def preview_program(ops: list[dict], base: dict) -> dict:
    """Preview what a program will do against a base generation snapshot.

    Pure Python over the snapshot: no Resolve read. Each op's effect is
    applied to a working copy in sequence, so a later op sees the
    earlier ops' results - the order `apply_patch` commits them in.

    Args:
        ops: A list of ``{"op": <algebra id>, "params": {...}}`` dicts,
            in execution order.
        base: A shadow generation snapshot (``timeline_shadow``).

    Returns:
        A preview artifact: per op, the affected span, the before/after
        values and the duration change; plus the total duration change
        and the resulting timeline duration.

    Raises:
        PreviewError: names the op that cannot be previewed and why.
    """
    if not isinstance(ops, list) or not ops:
        raise PreviewError("preview_program needs a non-empty list of ops")
    if not isinstance(base, dict) or "tracks" not in base:
        raise PreviewError(
            "preview_program needs a shadow generation snapshot with tracks")

    state = _working_state(base)
    preview_ops = []
    total_duration_change = 0

    for index, op in enumerate(ops):
        if not isinstance(op, dict):
            raise PreviewError(f"ops[{index}] must be an object")
        op_id = op.get("op")
        algebra_op = edit_algebra.get(op_id)
        if algebra_op is None:
            raise PreviewError(
                f"ops[{index}].op is {op_id!r}: not an algebra op. Known "
                f"ops: {', '.join(edit_algebra.ALGEBRA)}.")
        params = op.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise PreviewError(f"ops[{index}].params must be an object")
        missing = [key for key in algebra_op.required if key not in params]
        if missing:
            raise PreviewError(
                f"ops[{index}] ({op_id}) is missing required params: "
                f"{', '.join(missing)}.")

        duration_before = _duration(state)
        span = None
        if algebra_op.span is not None:
            try:
                span = algebra_op.span(params, state)
            except (KeyError, TypeError, ValueError):
                span = None

        if op_id in _PLAN_OPS:
            preview_ops.append({
                "op": op_id,
                "text": op.get("text", ""),
                "affected_span": None,
                "before": None,
                "after": None,
                "duration_change": 0,
                "plan_level": True,
            })
            continue

        effect = _EFFECTS.get(op_id)
        if effect is None:
            raise PreviewError(
                f"ops[{index}] ({op_id}) has no preview effect; its "
                f"executor lands with the gap that owns it")

        before, after = effect(params, state)
        duration_after = _duration(state)
        duration_change = duration_after - duration_before
        total_duration_change += duration_change

        preview_ops.append({
            "op": op_id,
            "text": op.get("text", ""),
            "affected_span": (None if span is None
                              else [int(span[0]), int(span[1])]),
            "before": before,
            "after": after,
            "duration_change": duration_change,
        })

    return {
        "format": PREVIEW_FORMAT,
        "ops": preview_ops,
        "total_duration_change": total_duration_change,
        "resulting_duration": _duration(state),
    }
