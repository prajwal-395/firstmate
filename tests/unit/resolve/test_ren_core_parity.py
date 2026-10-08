"""Parity harness: the Python edit algebra and its Rust port, compared.

The Rust port (`native/ren-core`, the `ren_native` extension) sits
behind one switch (`library/tools/edit_backend.py`,
`REN_EDIT_BACKEND=python`) with the Python implementation selectable as
the fallback. This harness runs BOTH implementations over the same
corpus - every algebra op, and the patch cases the existing test suite
(`tests/unit/resolve/test_edit_patch.py`) exercises - and compares the
outputs the migration's proof names: span, keys, conflicts, inverse and
verify verdicts.

The Python side is called directly (`edit_algebra.ALGEBRA[...]`,
`patch_algebra._compose_python`, `edit_patch._validate_python`, ...),
never through the switch, so the comparison holds whichever backend a
run has active.

Skipped, naming the missing build, when the extension is not
importable: a tree without `scripts/build_ren_native.sh` runs the
Python implementation unchanged, and there is nothing to compare.
Skipped, naming the stale build, when the extension's baked-in source
fingerprint does not match this tree: an older binary predates the
algebra the current `library/tools` speaks, and `edit_backend.rust()`
refuses it the same way. The skip is not a pass - a fresh build that
genuinely disagrees still fails.

One Python bug this harness caught, fixed rather than copied: the
`mask.create`/`mask.edit` verify returned the `_verify_fusion_comps`
judge factory without calling it (a truthy function object where a
verdict belongs). PR 1786 rewrote the mask ops; their verdicts are
compared like every other op's.
"""

from __future__ import annotations

import copy
import itertools
import re
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from library.tools import (capabilities, edit_algebra, edit_backend,
                            edit_patch, patch_algebra)
from library.tools import timeline_shadow as shadow


def _load_ren_native():
    """The ren_native extension, or a module-level skip naming why not.

    Skipped, naming the missing build, when the extension is not
    importable: a tree without `scripts/build_ren_native.sh` runs the
    Python implementation unchanged, and there is nothing to compare.

    Skipped, naming the stale build, when the extension's baked-in
    source fingerprint does not match this tree: an older binary
    predates the algebra the current `library/tools` speaks, and
    `edit_backend.rust()` refuses it the same way. The skip is not a
    pass - a fresh build that genuinely disagrees still fails below.
    """
    try:
        import ren_native
    except ImportError:
        pytest.skip(
            "the ren_native extension is not built - run "
            "scripts/build_ren_native.sh <venv-bin-dir>",
            allow_module_level=True,
        )
    from library.tools.ren_native_source import source_fingerprint

    root = Path(__file__).resolve().parents[3]
    current = source_fingerprint(root)
    if current is None:
        pytest.skip(
            "the ren_native extension cannot be verified - this tree "
            "carries no native/ sources",
            allow_module_level=True,
        )
    baked = getattr(ren_native, "source_fingerprint", lambda: "unknown")()
    if baked != current:
        pytest.skip(
            f"the ren_native extension is stale - built from a different "
            f"tree (fingerprint {baked[:12]} vs this tree {current[:12]}); "
            f"rebuild with scripts/build_ren_native.sh <venv-bin-dir>",
            allow_module_level=True,
        )
    return ren_native


ren_native = _load_ren_native()

BASE = "Podcast"
TIMELINE = "Reel 09"

# ── The corpus ─────────────────────────────────────────────────────────
#
# One base snapshot and a case per algebra op (both a passing and a
# failing after-snapshot where the op has a read-back). The mutations
# are pure functions of the base, so a case is reproducible.


def _clip(uid, record_in, record_out, track_type="video", track_index=1,
          **extra):
    clip = {
        "unique_id": uid,
        "name": uid,
        "track_type": track_type,
        "track_index": track_index,
        "record_in": record_in,
        "record_out": record_out,
        "duration": record_out - record_in,
        "source_in_frame": 0,
        "source_out_frame": record_out - record_in,
        "source_file": f"/pool/{uid}.mov",
        "enabled": True,
        "speed": None,
        "transform": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0},
        "keyframes": {},
        "fusion": {"comp_names": []},
        "color": {"cdl": {}},
        "markers": [],
    }
    clip.update(extra)
    return clip


def _track(track_type, index, name, clips):
    return {"type": track_type, "index": index, "name": name,
            "clips": clips}


def _marker(frame, color, name, note="", duration=1, custom_data=""):
    return {"frame": frame, "color": color, "name": name, "note": note,
            "duration": duration, "custom_data": custom_data}


def _base_snapshot():
    return {
        "start_frame": 0,
        "end_frame": 480,
        "tracks": [
            _track("video", 1, "V1", [
                _clip("a", 0, 120, markers=[_marker(5, "Green", "note")]),
                _clip("b", 120, 240),
                _clip("c", 240, 480),
            ]),
            _track("video", 2, "V2", [_clip("d", 60, 180, track_index=2)]),
            _track("video", 3, "Overlay", [_clip("ov:1", 100, 148,
                                                track_index=3)]),
            _track("audio", 1, "A1", [_clip("e", 0, 240, track_type="audio"),
                                    _clip("f", 240, 480,
                                          track_type="audio")]),
        ],
        "markers": {"timeline": [_marker(10, "Blue", "beat")]},
    }


def _clips_of(snap):
    return [clip for track in snap["tracks"] for clip in track["clips"]]


def _find_clip(snap, uid):
    found = [c for c in _clips_of(snap) if c["unique_id"] == uid]
    return found[0] if len(found) == 1 else None


def _without_clip(base, uid):
    snap = copy.deepcopy(base)
    for track in snap["tracks"]:
        track["clips"] = [c for c in track["clips"]
                          if c["unique_id"] != uid]
    return snap


def _with_clip(base, clip):
    snap = copy.deepcopy(base)
    track = next(t for t in snap["tracks"]
                 if t["type"] == clip["track_type"]
                 and t["index"] == clip["track_index"])
    track["clips"].append(copy.deepcopy(clip))
    track["clips"].sort(key=lambda c: c["record_in"])
    return snap


def _with_transform(base, uid, **props):
    snap = copy.deepcopy(base)
    clip = _find_clip(snap, uid)
    assert clip is not None, uid
    clip["transform"].update(props)
    return snap


def _with_enabled(base, uid, enabled):
    snap = copy.deepcopy(base)
    clip = _find_clip(snap, uid)
    assert clip is not None, uid
    clip["enabled"] = enabled
    return snap


def _with_speed(base, uid, speed):
    snap = copy.deepcopy(base)
    clip = _find_clip(snap, uid)
    assert clip is not None, uid
    clip["speed"] = speed
    return snap


def _with_clip_replaced(base, clip):
    """The clip at `clip`'s id reads back as `clip`."""
    snap = _without_clip(base, clip["unique_id"])
    return _with_clip(snap, clip)


def _with_marker(base, frame, color, name, note="", duration=1,
                 custom_data=""):
    snap = copy.deepcopy(base)
    snap["markers"]["timeline"] = [
        m for m in snap["markers"]["timeline"] if m["frame"] != frame]
    snap["markers"]["timeline"].append(
        _marker(frame, color, name, note, duration, custom_data))
    return snap


def _without_marker(base, frame):
    snap = copy.deepcopy(base)
    snap["markers"]["timeline"] = [
        m for m in snap["markers"]["timeline"] if m["frame"] != frame]
    return snap


def _with_clip_marker(base, uid, frame, color, name, note="", duration=1,
                      custom_data=""):
    snap = copy.deepcopy(base)
    clip = _find_clip(snap, uid)
    assert clip is not None, uid
    clip["markers"] = [m for m in clip["markers"] if m["frame"] != frame]
    clip["markers"].append(
        _marker(frame, color, name, note, duration, custom_data))
    return snap


def _without_clip_marker(base, uid, frame):
    snap = copy.deepcopy(base)
    clip = _find_clip(snap, uid)
    assert clip is not None, uid
    clip["markers"] = [m for m in clip["markers"] if m["frame"] != frame]
    return snap


def _with_keyframes(base, uid, param, keyframes):
    snap = copy.deepcopy(base)
    clip = _find_clip(snap, uid)
    assert clip is not None, uid
    clip["keyframes"][param] = copy.deepcopy(keyframes)
    return snap


def _with_fusion(base, uid, comp_names):
    snap = copy.deepcopy(base)
    clip = _find_clip(snap, uid)
    assert clip is not None, uid
    clip["fusion"]["comp_names"] = list(comp_names)
    return snap


def _with_matte(base, uid, name, geometry, keyframes=()):
    """The clip carries matte `name`, read back as `reel_read` does."""
    snap = _with_fusion(base, uid, [name])
    _find_clip(snap, uid)["fusion"]["mattes"] = {
        name: {"geometry": dict(geometry), "keyframes": list(keyframes)}}
    return snap


def _with_cdl(base, uid, cdl):
    snap = copy.deepcopy(base)
    clip = _find_clip(snap, uid)
    assert clip is not None, uid
    clip["color"]["cdl"] = copy.deepcopy(cdl)
    return snap


def _with_track(base, track_type, index, name, clips):
    snap = copy.deepcopy(base)
    snap["tracks"] = [t for t in snap["tracks"]
                       if not (t["type"] == track_type
                               and t["index"] == index)]
    snap["tracks"].append(_track(track_type, index, name, clips))
    return snap


def _without_track(base, track_type, index):
    snap = copy.deepcopy(base)
    snap["tracks"] = [t for t in snap["tracks"]
                       if not (t["type"] == track_type
                               and t["index"] == index)]
    return snap


def _with_segments(base, uid, pieces):
    """The clip at `uid` reads back as `pieces`: [(record_in,
    record_out, speed), ...], the first keeping the id, the rest named
    `<uid>-split-<n>` (`_verify_retime_segments`)."""
    snap = _without_clip(base, uid)
    for index, (record_in, record_out, speed) in enumerate(pieces):
        piece_uid = uid if index == 0 else f"{uid}-split-{index}"
        snap = _with_clip(snap, _clip(piece_uid, record_in, record_out,
                                      speed=speed))
    return snap


def _op_cases():
    """`(label, op, after)` for every algebra op, pass and fail."""
    base = _base_snapshot()
    marker_add = {"op": "marker.add", "frame": 20, "color": "Blue",
                  "name": "x"}
    clip_marker_add = {"op": "clip_marker.add", "unique_id": "a",
                       "frame": 12, "color": "Green", "name": "reply",
                       "note": "answered", "duration": 3,
                       "custom_data": "r"}
    insert = {"op": "clip.insert", "unique_id": "new:1",
              "track_type": "video", "track_index": 2,
              "record_in": 100, "record_out": 148,
              "media_pool_item": "pool-1", "source_in": 0,
              "source_out": 48}
    trim = {"op": "clip.trim", "unique_id": "a", "in_frame": 10,
            "out_frame": 100}
    roll = {"op": "clip.roll", "unique_id": "a", "delta_frames": 10,
            "source_in": 10, "source_out": 130}
    slip = {"op": "clip.slip", "unique_id": "a", "delta_frames": -5,
            "source_in": -5, "source_out": 115}
    move = {"op": "clip.move", "unique_id": "a", "to_frame": 200}
    replace = {"op": "clip.replace", "unique_id": "a",
               "source_file": "/pool/other.mov"}
    set_enabled = {"op": "clip.set_enabled", "unique_id": "a",
                   "enabled": False}
    reframe = {"op": "clip.smart_reframe", "unique_id": "a",
               "keys": ["Pan", "Tilt", "ZoomX", "ZoomY"],
               "before": {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0,
                          "ZoomY": 1.0}}
    set_property = {"op": "clip.set_property", "unique_id": "a",
                    "key": "ZoomX", "value": 1.4}
    retime = {"op": "retime", "unique_id": "a", "percent": 50.0,
              "duration": 240}
    retime_segments = {"op": "retime.segments", "unique_id": "a",
                       "segments": [{"percent": 50.0, "record_frame": 0},
                                    {"percent": 100.0, "record_frame": 60}]}
    keyframe = {"op": "picture.keyframe", "unique_id": "a",
                "param": "ZoomX",
                "keyframes": [{"frame": 0, "value": 1.0,
                               "interpolation": "linear"},
                              {"frame": 60, "value": 2.0,
                               "interpolation": "linear"}]}
    node_set = {"op": "grade.node_set", "unique_id": "a", "node_index": 1,
                "params": {"slope_r": 1.6, "slope_g": 1.6, "slope_b": 1.6,
                           "offset_r": 0.0, "offset_g": 0.0,
                           "offset_b": 0.0, "power_r": 1.0,
                           "power_g": 1.0, "power_b": 1.0,
                           "saturation": 1.0}}
    node_set_cdl = {"Slope": "1.6000 1.6000 1.6000",
                    "Offset": "0.0000 0.0000 0.0000",
                    "Power": "1.0000 1.0000 1.0000",
                    "Saturation": "1.0000"}
    mask_create = {"op": "mask.create", "unique_id": "a",
                   "matte": "matte-1"}
    mask_delete = {"op": "mask.delete", "unique_id": "a",
                   "matte": "matte-1"}
    mask_edit = {"op": "mask.edit", "unique_id": "a", "matte": "matte-1",
                 "geometry": {"Width": 0.5, "Center": {"X": 0.4, "Y": 0.6}}}
    mask_keys = [{"frame": 0, "Center": {"X": 0.3, "Y": 0.3}, "Width": 0.3},
                 {"frame": 48, "Center": {"X": 0.7, "Y": 0.7}, "Width": 0.5}]
    mask_track = {"op": "mask.track", "unique_id": "a", "matte": "matte-1",
                  "track": {"keyframes": mask_keys},
                  "geometry": {"Height": 0.2, "Width": 0.9}}
    full = {"Center": {"X": 0.5, "Y": 0.5}, "Width": 1.0, "Height": 1.0}
    edited = {**full, "Width": 0.5, "Center": {"X": 0.4, "Y": 0.6}}
    tracked = {**full, "Height": 0.2}
    compound_create = {"op": "compound.create", "unique_id": "comp:1",
                      "child_unique_ids": ["a", "b"]}
    track_add = {"op": "track.add", "track_type": "video",
                 "name": "Overlay"}
    track_rename = {"op": "track.rename", "track_type": "video",
                    "track_index": 2, "name": "Graphics"}
    track_reorder = {"op": "track.reorder", "track_type": "video",
                     "from_index": 2, "to_index": 1, "name": "V2"}
    overlay_remove = {"op": "overlay.remove", "unique_id": "ov:1"}
    transform_override = {"op": "transform_override", "unique_id": "a",
                         "key": "Tilt", "value": -870.0}
    span_retime = {"op": "span_retime", "unique_id": "a",
                   "percent": 200.0}
    drop_fragment = {"op": "drop_fragment", "unique_id": "a"}
    return [
        # ── markers ──────────────────────────────────────────────
        ("marker.add", marker_add,
         _with_marker(base, 20, "Blue", "x")),
        ("marker.add-missing", marker_add, base),
        ("marker.delete", {"op": "marker.delete", "frame": 10},
         _without_marker(base, 10)),
        ("marker.delete-present", {"op": "marker.delete", "frame": 10},
         base),
        ("clip_marker.add", clip_marker_add,
         _with_clip_marker(base, "a", 12, "Green", "reply", "answered", 3,
                           "r")),
        ("clip_marker.add-missing", clip_marker_add, base),
        ("clip_marker.delete",
         {"op": "clip_marker.delete", "unique_id": "a", "frame": 5},
         _without_clip_marker(base, "a", 5)),
        ("clip_marker.delete-present",
         {"op": "clip_marker.delete", "unique_id": "a", "frame": 5}, base),

        # ── clip structure ──────────────────────────────────────
        ("clip.delete", {"op": "clip.delete", "unique_id": "a"},
         _without_clip(base, "a")),
        ("clip.delete-present", {"op": "clip.delete", "unique_id": "a"},
         base),
        ("clip.insert", insert,
         _with_clip(base, _clip("new:1", 100, 148, track_index=2))),
        ("clip.insert-missing", insert, base),
        ("clip.split", {"op": "clip.split", "unique_id": "a",
                        "at_frame": 60},
         _with_clip(base, _clip("a", 0, 60))),
        ("clip.split-wrong", {"op": "clip.split", "unique_id": "a",
                              "at_frame": 60}, base),
        ("clip.join", {"op": "clip.join", "unique_id": "a",
                       "other_unique_id": "b"}, _without_clip(base, "b")),
        ("clip.join-present", {"op": "clip.join", "unique_id": "a",
                               "other_unique_id": "b"}, base),
        ("clip.trim", trim, _with_clip(base, _clip("a", 10, 100))),
        ("clip.trim-wrong", trim, base),
        ("clip.ripple", {"op": "clip.ripple", "unique_id": "b",
                         "delta_frames": 10}, base),
        ("clip.ripple-gone", {"op": "clip.ripple", "unique_id": "b",
                              "delta_frames": 10}, _without_clip(base, "b")),
        ("clip.roll", roll,
         _with_clip(base, _clip("a", 0, 120, source_in_frame=10,
                                 source_out_frame=130))),
        ("clip.roll-wrong", roll, base),
        ("clip.slip", slip,
         _with_clip(base, _clip("a", 0, 120, source_in_frame=-5,
                                 source_out_frame=115))),
        ("clip.slip-wrong", slip, base),
        ("clip.move", move, _with_clip(base, _clip("a", 200, 320))),
        ("clip.move-wrong", move, base),
        ("clip.replace", replace,
         _with_clip(base, _clip("a", 0, 120,
                                 source_file="/pool/other.mov"))),
        ("clip.replace-wrong", replace, base),
        ("clip.set_enabled", set_enabled, _with_enabled(base, "a", False)),
        ("clip.set_enabled-wrong", set_enabled, base),
        ("clip.link", {"op": "clip.link", "unique_id": "a",
                       "with_unique_ids": ["b"]}, base),
        ("clip.unlink", {"op": "clip.unlink", "unique_id": "a"}, base),

        # ── clip properties ─────────────────────────────────────
        ("clip.smart_reframe", reframe, _with_transform(base, "a", Pan=1.5)),
        ("clip.smart_reframe-unchanged", reframe, base),
        ("clip.set_property", set_property,
         _with_transform(base, "a", ZoomX=1.4)),
        ("clip.set_property-wrong", set_property, base),

        # ── retime ──────────────────────────────────────────────
        ("retime", retime, _with_speed(base, "a", 50.0)),
        ("retime-wrong", retime, base),
        ("retime.segments", retime_segments,
         _with_segments(base, "a", [(0, 60, 50.0), (60, 120, 100.0)])),
        ("retime.segments-wrong", retime_segments, base),

        # ── picture keyframes ───────────────────────────────────
        ("picture.keyframe", keyframe,
         _with_keyframes(base, "a", "ZoomX",
                         [{"frame": 0, "value": 1.0},
                          {"frame": 60, "value": 2.0}])),
        ("picture.keyframe-wrong", keyframe, base),

        # ── audio (no read-back) ────────────────────────────────
        ("voice_isolation", {"op": "voice_isolation", "track_index": 1},
         base),
        ("audio.gain", {"op": "audio.gain", "unique_id": "a", "db": -3.0},
         base),
        ("audio.pan", {"op": "audio.pan", "unique_id": "a", "pan": 0.5},
         base),
        ("audio.fade", {"op": "audio.fade", "unique_id": "a",
                        "fade_in": {"frames": 10},
                        "fade_out": {"frames": 10}}, base),
        ("audio.automation", {"op": "audio.automation", "unique_id": "a",
                              "param": "volume",
                              "keyframes": [{"frame": 0, "value": -6.0}]},
         base),

        # ── grade ───────────────────────────────────────────────
        ("clip_lut", {"op": "clip_lut", "unique_id": "a",
                      "lut": "/luts/x.cube"}, base),
        ("grade", {"op": "grade", "unique_id": "a",
                   "grade": "Free_1.13.1.drx"}, base),
        ("grade.node_add", {"op": "grade.node_add", "unique_id": "a",
                            "node_index": 2}, base),
        ("grade.node_remove", {"op": "grade.node_remove", "unique_id": "a",
                               "node_index": 2}, base),
        ("grade.node_reorder", {"op": "grade.node_reorder",
                                "unique_id": "a", "from_index": 1,
                                "to_index": 2}, base),
        ("grade.node_set", node_set, _with_cdl(base, "a", node_set_cdl)),
        ("grade.node_set-wrong", node_set, base),

        # ── mask ────────────────────────────────────────────────
        ("mask.create", mask_create, _with_matte(base, "a", "matte-1", full)),
        ("mask.create-missing", mask_create, base),
        ("mask.create-unreadable", mask_create,
         _with_fusion(base, "a", ["matte-1"])),
        ("mask.delete", mask_delete, base),
        ("mask.delete-still-there", mask_delete,
         _with_matte(base, "a", "matte-1", full)),
        ("mask.edit", mask_edit, _with_matte(base, "a", "matte-1", edited)),
        ("mask.edit-wrong", mask_edit,
         _with_matte(base, "a", "matte-1", {**edited, "Width": 0.9})),
        ("mask.track", mask_track,
         _with_matte(base, "a", "matte-1", tracked, mask_keys)),
        ("mask.track-missing-key", mask_track,
         _with_matte(base, "a", "matte-1", tracked, mask_keys[:1])),
        ("mask.track-extra-key", mask_track,
         _with_matte(base, "a", "matte-1", tracked,
                     mask_keys + [{"frame": 96, "Width": 0.1}])),
        ("mask.track-wrong-center", mask_track,
         _with_matte(base, "a", "matte-1", tracked,
                     [mask_keys[0], {**mask_keys[1],
                                     "Center": {"X": 0.7, "Y": 0.1}}])),
        ("mask.track-wrong-geometry", mask_track,
         _with_matte(base, "a", "matte-1", {**tracked, "Height": 0.8},
                     mask_keys)),

        # ── multicam ────────────────────────────────────────────
        ("multicam.angle", {"op": "multicam.angle", "unique_id": "a",
                            "angle_index": 2}, base),

        # ── compound ────────────────────────────────────────────
        ("compound.create", compound_create,
         _with_clip(base, _clip("comp:1", 0, 240))),
        ("compound.create-missing", compound_create, base),
        ("compound.flatten", {"op": "compound.flatten",
                              "unique_id": "comp:1"},
         _without_clip(base, "comp:1")),
        ("compound.flatten-present", {"op": "compound.flatten",
                                      "unique_id": "comp:1"}, base),

        # ── tracks ──────────────────────────────────────────────
        ("track.add", track_add,
         _with_track(base, "video", 4, "Overlay", [])),
        ("track.add-missing", track_add, base),
        ("track.delete", {"op": "track.delete", "track_type": "video",
                          "track_index": 2}, _without_track(base, "video", 2)),
        ("track.delete-present",
         {"op": "track.delete", "track_type": "video", "track_index": 2},
         base),
        ("track.rename", track_rename,
         _with_track(base, "video", 2, "Graphics",
                     [_clip("d", 60, 180, track_index=2)])),
        ("track.rename-wrong", track_rename, base),
        ("track.reorder", track_reorder,
         _with_track(_without_track(base, "video", 2), "video", 1, "V2",
                     [_clip("d", 60, 180, track_index=1)])),
        ("track.reorder-wrong", track_reorder, base),

        # ── overlays ────────────────────────────────────────────
        ("overlay.add", {"op": "overlay.add", "row": "V2",
                         "record_frame": 100}, base),
        ("overlay.remove", overlay_remove, _without_clip(base, "ov:1")),
        ("overlay.remove-present", overlay_remove, base),
        ("overlay.swap_pixels", {"op": "overlay.swap_pixels",
                                 "unique_id": "ov:1",
                                 "file": "/renders/x.png"}, base),
        ("overlay.resize", {"op": "overlay.resize", "unique_id": "ov:1",
                            "width": 1920, "height": 1080}, base),
        ("overlay.entry_motion", {"op": "overlay.entry_motion",
                                  "unique_id": "ov:1", "frames": 30}, base),

        # ── projected captain edits ────────────────────────────
        ("transform_override", transform_override,
         _with_transform(base, "a", Tilt=-870.0)),
        ("transform_override-wrong", transform_override, base),
        ("span_retime", span_retime, _with_speed(base, "a", 200.0)),
        ("span_retime-wrong", span_retime, base),
        ("drop_fragment", drop_fragment, _without_clip(base, "a")),
        ("drop_fragment-present", drop_fragment, base),
        ("caption_fix", {"op": "caption_fix", "unique_id": "a",
                         "text": "hello"}, base),
        ("redraw_closer", {"op": "redraw_closer", "unique_id": "a",
                           "start": 12.5}, base),

        # ── plan-level ops (no span, no read-back) ─────────────
        ("angle_plan", {"op": "angle_plan", "plan": {"x": 1}}, base),
        ("plan_change", {"op": "plan_change", "node": "n1",
                         "note": "n"}, base),
        ("transition", {"op": "transition", "carrier": "c",
                        "transition": "t"}, base),
        ("shot_selection", {"op": "shot_selection", "plan": {}}, base),
        ("speech_selection", {"op": "speech_selection", "plan": {}},
         base),
        ("story_pacing", {"op": "story_pacing", "plan": {}}, base),
        ("music_selection", {"op": "music_selection", "plan": {}}, base),
        ("audio_mix", {"op": "audio_mix", "plan": {}}, base),
        ("look", {"op": "look", "plan": {}}, base),
        ("visual_effect", {"op": "visual_effect", "plan": {}}, base),
        ("sound_effect", {"op": "sound_effect", "plan": {}}, base),
        ("subject_effect", {"op": "subject_effect", "plan": {}}, base),
        ("motion_graphic", {"op": "motion_graphic", "plan": {}}, base),
        ("brand_asset", {"op": "brand_asset", "plan": {}}, base),
        ("end_card", {"op": "end_card", "plan": {}}, base),
    ]


# ── The Python side, called directly (never through the switch) ─────────


def _py_declaration(op_id):
    spec = edit_algebra.ALGEBRA[op_id]
    return spec


def _patch(pid, base_generation, operations, domains, spans,
           capability="reel.touchup", preconditions=(), postconditions=()):
    return edit_patch.EditPatch(
        id=pid, project=BASE, timeline=TIMELINE,
        base_generation=base_generation, capability=capability,
        affected_spans=tuple(tuple(span) for span in spans),
        conflict_domains=tuple(domains),
        operations=tuple(dict(op) for op in operations),
        preconditions=tuple(dict(c) for c in preconditions),
        postconditions=tuple(dict(c) for c in postconditions))


def _patch_dict(patch):
    data = asdict(patch)
    data["affected_spans"] = [list(span)
                              for span in patch.affected_spans]
    for key in ("conflict_domains", "operations", "preconditions",
                "postconditions"):
        data[key] = list(data[key])
    return data


# ── Declaration, span, key, verify ──────────────────────────────────────


def test_the_algebra_id_sets_match():
    """The Rust algebra enumerates exactly the Python union, in order."""
    assert ren_native.op_ids() == list(edit_algebra.ALGEBRA)
    assert ren_native.patch_op_ids() == list(edit_algebra.patch_op_ids())


def test_declaration_parity():
    """Every op's domain, required, merge, temporal, item, has_apply
    and inverse, Python against Rust."""
    mismatches = []
    for op_id, spec in edit_algebra.ALGEBRA.items():
        decl = ren_native.declaration(op_id)
        assert decl is not None, op_id
        inverse = spec.inverse
        if (decl["domain"] != spec.domain
                or list(decl["required"]) != list(spec.required)
                or decl["merge"] != spec.merge
                or decl["temporal"] != spec.temporal
                or decl["item"] != spec.item
                or decl["has_apply"] != (spec.apply is not None)
                or decl["inverse"]["op"] != inverse.op
                or decl["inverse"]["self_inverse"] != inverse.self_inverse
                or decl["inverse"]["reason"] != inverse.reason):
            mismatches.append(op_id)
    assert not mismatches, mismatches


def test_span_parity():
    """The frames an op touches over the base snapshot."""
    base = _base_snapshot()
    mismatches = []
    for label, op, _after in _op_cases():
        spec = _py_declaration(op["op"])
        py = spec.span(op, base)
        rust = ren_native.span(op, base)
        rust = tuple(rust) if rust is not None else None
        if py != rust:
            mismatches.append((label, py, rust))
    assert not mismatches, mismatches


def test_span_many_parity():
    """The batched boundary returns the same spans as individual calls."""
    base = _base_snapshot()
    operations = [op for _label, op, _after in _op_cases()]
    expected = [ren_native.span(op, base) for op in operations]
    assert ren_native.span_many(operations, base) == expected


def test_key_parity():
    """What an op writes, as a key (numbers rendered as decimal
    strings on both sides)."""
    mismatches = []
    for label, op, _after in _op_cases():
        spec = _py_declaration(op["op"])
        py = tuple(str(part) for part in spec.key(op))
        rust = tuple(ren_native.key(op))
        if py != rust:
            mismatches.append((label, py, rust))
    assert not mismatches, mismatches


def test_verify_parity():
    """The read-back verdict of every op: None when the effect landed,
    a failure when it did not. The verdict is the contract; the wording
    is diagnostic and is not compared."""
    base = _base_snapshot()
    mismatches = []
    counts = {"landed": 0, "failed": 0}
    for label, op, after in _op_cases():
        spec = _py_declaration(op["op"])
        py = spec.verify(op, after)
        rust = ren_native.verify(op, after)
        if (py is None) != (rust is None):
            mismatches.append((label, py, rust))
        elif py is None:
            counts["landed"] += 1
        else:
            counts["failed"] += 1
    assert not mismatches, mismatches
    print(f"verify parity: {counts['landed']} landed, "
          f"{counts['failed']} failed, verdicts agree")


def test_verify_many_parity():
    """The batched boundary preserves every individual verify verdict."""
    cases = _op_cases()
    # Each corpus case has its own after-snapshot, so exercise the shared
    # snapshot path in batches grouped by snapshot equality.
    groups = {}
    for _label, op, after in cases:
        key = repr(after)
        groups.setdefault(key, (after, []))[1].append(op)
    for after, ops in groups.values():
        assert ren_native.verify_many(ops, after) == [
            ren_native.verify(op, after) for op in ops]


def test_production_batch_wrappers_match_individual_calls(monkeypatch):
    """The production edit-algebra entry points route through batch APIs."""
    monkeypatch.setenv(edit_backend.BACKEND_ENV, edit_backend.RUST)
    base = _base_snapshot()
    operations = [op for _label, op, _after in _op_cases()]
    assert edit_algebra.spans(operations, base) == ren_native.span_many(
        operations, base)
    assert edit_algebra.verifies(operations, base) == [
        edit_algebra.verify(op, base) for op in operations]


def test_snapshot_handle_reuses_and_updates_confirmed_property_writes():
    """A native handle keeps its snapshot resident and accepts measured edits."""
    base = _base_snapshot()
    operation = {"op": "clip.set_property", "unique_id": "a",
                 "key": "ZoomX", "value": 1.5}
    handle = ren_native.snapshot_handle(
        base, property_projection=True, include_transform=True)

    assert ren_native.span(operation, handle) == ren_native.span(operation, base)
    assert ren_native.verify(operation, handle) == ren_native.verify(operation, base)
    assert handle.apply_confirmed(operation)
    assert ren_native.verify(operation, handle) is None
    assert ren_native.verify(operation, base) is not None

    refreshed = copy.deepcopy(base)
    refreshed["tracks"][0]["clips"][0]["transform"]["ZoomX"] = 1.25
    handle.replace(refreshed)
    assert ren_native.verify(operation, handle) is not None


# ── Conflicts: meet, strictest, compose, carry ──────────────────────────


def test_meet_parity():
    """Two write keys MEET when one is a prefix of the other."""
    keys = []
    for _label, op, _after in _op_cases():
        keys.append(_py_declaration(op["op"]).key(op))
    mismatches = []
    for a, b in itertools.product(keys, keys):
        py = patch_algebra._meet(a, b)
        rust = ren_native.meet(list(a), list(b))
        if py != rust:
            mismatches.append((a, b, py, rust))
    assert not mismatches, mismatches


def test_strictest_parity():
    """The strictest of merge semantics, over the vocabulary."""
    vocab = list(patch_algebra.MERGE_SEMANTICS)
    mismatches = []
    for size in range(1, 4):
        for combo in itertools.combinations_with_replacement(vocab, size):
            py = patch_algebra._strictest(*combo)
            rust = ren_native.strictest(list(combo))
            if py != rust:
                mismatches.append((combo, py, rust))
    assert not mismatches, mismatches


def _compose_cases():
    """The patch pairs the existing test suite exercises.

    The `serialize` verdict is unreachable through the current algebra
    on both sides: no operation declares `ordered` (the only
    commutative op, `marker.delete`, meets itself identically), and
    the Python compose reads plan-level ops from `edit_patch.OPERATIONS`
    where they do not exist. Both implementations carry the verdict for
    the operation that needs one (`patch_algebra`).
    """
    marker_30 = {"op": "marker.add", "frame": 30, "color": "Red",
                 "name": "m"}
    marker_20 = {"op": "marker.add", "frame": 20, "color": "Red",
                 "name": "n"}
    zoom = {"op": "clip.set_property", "unique_id": "a", "key": "ZoomX",
            "value": 1.2}
    zoom_other = {**zoom, "value": 1.4}
    ripple = _patch("cut", 1, [{"op": "marker.add", "frame": 10,
                                "color": "Blue", "name": "c"}],
                    ["markers"], [[10, 20]])
    return [
        ("different-timelines",
         _patch("m", 1, [marker_30], ["markers"], [[30, 31]]),
         replace(_patch("o", 1, [marker_20], ["markers"], [[20, 21]]),
                 timeline="Other")),
        ("no-write-meets",
         _patch("m", 1, [marker_30], ["markers"], [[30, 31]]),
         _patch("n", 1, [marker_20], ["markers"], [[20, 21]])),
        ("global-effect",
         _patch("g", 1, [marker_30], ["markers"], [[30, 31]],
                capability="test.global"),
         _patch("h", 1, [marker_20], ["markers"], [[20, 21]])),
        ("frame-past-ripple",
         ripple,
         _patch("m", 1, [marker_30], ["markers"], [[30, 31]])),
        ("clip-past-ripple",
         ripple,
         _patch("z", 1, [zoom], ["picture_transform"], [[0, 48]],
                capability="reel.set_properties")),
        ("exclusive-meeting",
         _patch("del", 1, [{"op": "clip.delete", "unique_id": "a"}],
                ["timeline_structure"], [[0, 48]]),
         _patch("z", 1, [zoom], ["picture_transform"], [[0, 48]],
                capability="reel.set_properties")),
        ("same-write-merges",
         _patch("s1", 1, [zoom], ["picture_transform"], [[0, 48]],
                capability="reel.set_properties"),
         _patch("s2", 1, [zoom], ["picture_transform"], [[0, 48]],
                capability="reel.set_properties")),
        ("different-values-conflict",
         _patch("s1", 1, [zoom], ["picture_transform"], [[0, 48]],
                capability="reel.set_properties"),
         _patch("s2", 1, [zoom_other], ["picture_transform"], [[0, 48]],
                capability="reel.set_properties")),
    ]


def _semantics_of(patch):
    sem = capabilities.get(patch.capability).execution.patch
    return {"temporal_effect": sem.temporal_effect,
            "merge_semantics": sem.merge_semantics}


@pytest.fixture
def declared_semantics(monkeypatch):
    """One extra capability the compose cases need: a `global` temporal
    effect, which no registry capability declares."""
    from dataclasses import replace as dc_replace

    original = capabilities.all()
    spec = next(s for s in original if s.id == "reel.touchup")
    sem = spec.execution.patch
    global_spec = dc_replace(
        spec,
        id="test.global",
        execution=dc_replace(
            spec.execution,
            patch=dc_replace(sem, temporal_effect="global",
                             merge_semantics="commutative")))
    monkeypatch.setattr(
        capabilities, "all",
        lambda: tuple(list(original) + [global_spec]))


def test_compose_parity(declared_semantics):
    """How `later` lands once `earlier` has: the verdict, Python
    against Rust."""
    base = _base_snapshot()
    mismatches = []
    counts = {}
    for label, earlier, later in _compose_cases():
        py = patch_algebra._compose_python(earlier, later, base)
        rust = ren_native.compose(_patch_dict(earlier),
                                  _patch_dict(later), base,
                                  _semantics_of(earlier),
                                  _semantics_of(later))
        counts[py.verdict] = counts.get(py.verdict, 0) + 1
        if (py.verdict, py.reason) != (rust["verdict"], rust["reason"]):
            mismatches.append((label, py.verdict, py.reason,
                               rust["verdict"], rust["reason"]))
    assert not mismatches, mismatches
    print(f"compose parity: {counts}")


def _carry_store(tmp_path, index):
    return shadow.ShadowStore(tmp_path / f"shadow-{index}.db")


def _record(store, snapshot, source, patch=None):
    head = store.head(BASE, TIMELINE)
    return store.record(project=BASE, timeline_id=TIMELINE,
                        timeline_name=TIMELINE, snapshot=snapshot,
                        source=source,
                        expected_head=head.generation if head else 0,
                        patch=patch)


def _carry_cases(tmp_path):
    """`(label, patch, rows)` - the patch planned on the base, and the
    generations recorded since, as the store holds them."""
    base = _base_snapshot()
    zoom = {"op": "clip.set_property", "unique_id": "a", "key": "ZoomX",
            "value": 1.2}
    marker_40 = {"op": "marker.add", "frame": 40, "color": "Blue",
                 "name": "m"}
    marker_10 = {"op": "marker.add", "frame": 10, "color": "Blue",
                 "name": "c"}
    ripple_patch = {"id": "cut", "project": BASE, "timeline": TIMELINE,
                    "base_generation": 1, "capability": "reel.touchup",
                    "affected_spans": [[10, 20]],
                    "conflict_domains": ["markers"],
                    "operations": [marker_10]}
    cases = []

    # Nothing since the base: the patch moves onto the base unchanged.
    store = _carry_store(tmp_path, 1)
    _record(store, base, shadow.OBSERVED)
    stale = _patch("m", 1, [{"op": "marker.add", "frame": 20,
                              "color": "Red", "name": "n"}],
                   ["markers"], [[20, 21]])
    cases.append(("no-generations", stale, store))

    # A commuting generation since: the patch moves onto the head.
    store = _carry_store(tmp_path, 2)
    _record(store, base, shadow.OBSERVED)
    head = _record(store, _with_marker(base, 40, "Blue", "m"),
                    shadow.PATCH,
                    patch={"id": "q", "project": BASE, "timeline": TIMELINE,
                           "base_generation": 1,
                           "capability": "reel.touchup",
                           "affected_spans": [[40, 41]],
                           "conflict_domains": ["markers"],
                           "operations": [marker_40]})
    stale = _patch("m", 1, [{"op": "marker.add", "frame": 20,
                              "color": "Red", "name": "n"}],
                   ["markers"], [[20, 21]])
    cases.append(("commute", stale, store))

    # A ripple generation since: the later clip-addressed patch
    # rebases onto the head with its spans re-measured.
    store = _carry_store(tmp_path, 3)
    _record(store, base, shadow.OBSERVED)
    _record(store, base, shadow.PATCH, patch=ripple_patch)
    stale = _patch("z", 1, [zoom], ["picture_transform"], [[0, 48]],
                   capability="reel.set_properties")
    cases.append(("rebase", stale, store))

    # A conflicting generation since: the carry refuses.
    store = _carry_store(tmp_path, 4)
    _record(store, base, shadow.OBSERVED)
    _record(store, _without_clip(base, "a"), shadow.PATCH,
            patch={"id": "del", "project": BASE, "timeline": TIMELINE,
                   "base_generation": 1, "capability": "reel.touchup",
                   "affected_spans": [[0, 48]],
                   "conflict_domains": ["timeline_structure"],
                   "operations": [{"op": "clip.delete",
                                   "unique_id": "a"}]})
    stale = _patch("z", 1, [zoom], ["picture_transform"], [[0, 48]],
                   capability="reel.set_properties")
    cases.append(("conflict", stale, store))

    # An `observed` generation since: a change nobody recorded.
    store = _carry_store(tmp_path, 5)
    _record(store, base, shadow.OBSERVED)
    _record(store, _with_marker(base, 40, "Blue", "m"), shadow.OBSERVED)
    stale = _patch("m", 1, [{"op": "marker.add", "frame": 20,
                              "color": "Red", "name": "n"}],
                   ["markers"], [[20, 21]])
    cases.append(("observed", stale, store))

    # The moved patch no longer validates on the head.
    store = _carry_store(tmp_path, 6)
    _record(store, base, shadow.OBSERVED)
    _record(store, _without_clip(base, "a"), shadow.PATCH,
            patch={"id": "del", "project": BASE, "timeline": TIMELINE,
                   "base_generation": 1, "capability": "reel.touchup",
                   "affected_spans": [[0, 48]],
                   "conflict_domains": ["timeline_structure"],
                   "operations": [{"op": "clip.delete",
                                   "unique_id": "a"}]})
    stale = _patch("z", 1, [zoom], ["picture_transform"], [[0, 48]],
                   capability="reel.set_properties")
    cases.append(("validate-fails", stale, store))

    # The moved patch's preconditions do not hold on the head.
    store = _carry_store(tmp_path, 7)
    _record(store, base, shadow.OBSERVED)
    _record(store, _with_marker(base, 40, "Blue", "m"), shadow.PATCH,
            patch={"id": "q", "project": BASE, "timeline": TIMELINE,
                   "base_generation": 1, "capability": "reel.touchup",
                   "affected_spans": [[40, 41]],
                   "conflict_domains": ["markers"],
                   "operations": [marker_40]})
    stale = _patch("m", 1, [{"op": "marker.add", "frame": 20,
                              "color": "Red", "name": "n"}],
                   ["markers"], [[20, 21]],
                   preconditions=[{"kind": "clip_present",
                                   "unique_id": "a"}])
    cases.append(("preconditions-fail", stale, store))
    return cases


def test_carry_parity(tmp_path):
    """A stale patch moved onto the head, or the reason it cannot: the
    outcome and the moved patch, Python against Rust."""
    mismatches = []
    counts = {"moved": 0, "refused": 0}
    for label, patch, store in _carry_cases(tmp_path):
        py_moved, py_reason = patch_algebra._carry_python(
            patch, store, BASE, TIMELINE)
        rows = store.since_with_snapshots(BASE, TIMELINE,
                                          since=patch.base_generation)
        row_dicts = [
            {"generation": gen.generation, "source": gen.source,
             "patch": gen.patch, "snapshot": snap,
             "semantics": patch_algebra._semantics_dict(
                 edit_patch.EditPatch.from_dict(gen.patch))
             if gen.patch else None}
            for gen, snap in rows
        ]
        semantics = patch_algebra._semantics_dict(patch)
        try:
            rust_moved = ren_native.carry(_patch_dict(patch), row_dicts,
                                           semantics)
            rust_reason = None
        except ValueError as exc:
            rust_moved, rust_reason = None, str(exc)
        if (py_moved is None) != (rust_moved is None):
            mismatches.append((label, py_reason, rust_reason))
        elif py_moved is None:
            counts["refused"] += 1
        else:
            counts["moved"] += 1
            if _patch_dict(py_moved) != rust_moved:
                mismatches.append(
                    (label, _patch_dict(py_moved), rust_moved))
    assert not mismatches, mismatches
    print(f"carry parity: {counts['moved']} moved, "
          f"{counts['refused']} refused, outcomes agree")


# ── Validation, conditions, problems ────────────────────────────────────


def _validate_cases():
    """Patches the existing test suite's validation cases: a true
    declaration, and the ways one can say less or more than it does."""
    zoom = {"op": "clip.set_property", "unique_id": "a", "key": "ZoomX",
            "value": 1.2}
    declared = capabilities.get("reel.set_properties").execution.patch
    touchup = capabilities.get("reel.touchup").execution.patch
    return [
        ("true-declaration",
         _patch("p", 1, [zoom], ["picture_transform"], [[0, 48]],
                capability="reel.set_properties"),
         declared),
        ("domain-not-declared",
         _patch("p", 1, [{"op": "marker.add", "frame": 5, "color": "Blue",
                          "name": "x"}], ["picture_transform"],
                [[0, 10]], capability="reel.set_properties"),
         declared),
        ("span-not-covered",
         _patch("p", 1, [{"op": "marker.add", "frame": 50, "color": "Blue",
                          "name": "x"}], ["markers"], [[0, 10]],
                capability="reel.touchup"),
         touchup),
        ("target-not-on-base",
         _patch("p", 1, [{"op": "clip.delete", "unique_id": "nobody"}],
                ["timeline_structure"], [[0, 48]],
                capability="reel.touchup"),
         touchup),
        ("unknown-domain",
         _patch("p", 1, [zoom], ["picture_transform", "bogus"],
                [[0, 48]], capability="reel.set_properties"),
         declared),
        ("empty-spans",
         _patch("p", 1, [zoom], ["picture_transform"], [],
                capability="reel.set_properties"),
         declared),
        ("no-operations",
         _patch("p", 1, [], ["picture_transform"], [[0, 48]],
                capability="reel.set_properties"),
         declared),
        ("unknown-operation",
         _patch("p", 1, [{"op": "grade", "unique_id": "a",
                          "grade": "Free.drx"}], ["color"], [[0, 48]],
                capability="reel.touchup"),
         touchup),
        ("missing-required-field",
         _patch("p", 1, [{"op": "clip.trim", "unique_id": "a",
                          "in_frame": 10}], ["timeline_structure"],
                [[0, 48]], capability="reel.touchup"),
         touchup),
        ("non-transform-property",
         _patch("p", 1, [{"op": "clip.set_property", "unique_id": "a",
                          "key": "Speed", "value": 2.0}],
                ["picture_transform"], [[0, 48]],
                capability="reel.set_properties"),
         declared),
        ("malformed-reframe-keys",
         _patch("p", 1, [{"op": "clip.smart_reframe", "unique_id": "a",
                          "keys": ["Pan"],
                          "before": {"Pan": 0.0}}],
                ["picture_transform"], [[0, 48]],
                capability="reel.touchup"),
         touchup),
        ("malformed-condition",
         _patch("p", 1, [zoom], ["picture_transform"], [[0, 48]],
                capability="reel.set_properties",
                preconditions=[{"kind": "mystery"}]),
         declared),
    ]


def test_validate_parity():
    """A declaration must be true: both implementations accept the same
    patches and refuse the same patches."""
    base = _base_snapshot()
    mismatches = []
    counts = {"accepted": 0, "refused": 0}
    for label, patch, declared in _validate_cases():
        try:
            edit_patch._validate_python(patch, base, declared)
            py_refused, py_reason = None, None
        except edit_patch.PatchRefused as refused:
            py_refused, py_reason = True, refused.what
        rust_reason = ren_native.validate_patch(
            _patch_dict(patch), base,
            {"operations": list(declared.operations),
             "conflict_domains": list(declared.conflict_domains)})
        if (py_reason is None) != (rust_reason is None):
            mismatches.append((label, py_reason, rust_reason))
        elif py_reason is None:
            counts["accepted"] += 1
        else:
            counts["refused"] += 1
    assert not mismatches, mismatches
    print(f"validate parity: {counts['accepted']} accepted, "
          f"{counts['refused']} refused, decisions agree")


def _condition_cases():
    return [
        ("present", [{"kind": "clip_present", "unique_id": "a"}], True),
        ("absent", [{"kind": "clip_absent", "unique_id": "nobody"}], True),
        ("marker-present", [{"kind": "marker_present", "frame": 10}], True),
        ("marker-absent", [{"kind": "marker_absent", "frame": 99}], True),
        ("clip-marker-present",
         [{"kind": "clip_marker_present", "unique_id": "a", "frame": 5}],
         True),
        ("clip-marker-absent",
         [{"kind": "clip_marker_absent", "unique_id": "a", "frame": 99}],
         True),
        ("transform-equals",
         [{"kind": "clip_transform_equals", "unique_id": "a",
           "properties": {"ZoomX": 1.0}}], True),
        ("duration-unchanged", [{"kind": "duration_unchanged"}], True),
        ("clip-count", [{"kind": "clip_count", "track_type": "video",
                         "track_index": 1, "count": 3}], True),
        ("present-but-missing",
         [{"kind": "clip_present", "unique_id": "nobody"}], False),
        ("absent-but-present",
         [{"kind": "clip_absent", "unique_id": "a"}], False),
        ("marker-present-but-missing",
         [{"kind": "marker_present", "frame": 99}], False),
        ("transform-differs",
         [{"kind": "clip_transform_equals", "unique_id": "a",
           "properties": {"ZoomX": 2.0}}], False),
        ("clip-count-differs",
         [{"kind": "clip_count", "track_type": "video",
           "track_index": 1, "count": 2}], False),
    ]


def _normalize(text):
    """The Rust failure texts spell ids and JSON values with Debug
    (`ClipId("a")`, `Some(Number(1.5))`) where Python uses repr
    (`'a'`, `1.5`). The verdict is the contract; the wording is
    compared after this normalization."""
    text = re.sub(r'ClipId\("([^"]*)"\)', r"'\1'", text)
    text = re.sub(r'Some\(Number\(([^)]*)\)\)', r'\1', text)
    return re.sub(r'Number\(([^)]*)\)', r'\1', text)


def test_judge_conditions_parity():
    """Every condition judged on the same snapshot: the failures,
    Python against Rust."""
    base = _base_snapshot()
    head = _with_transform(base, "a", ZoomX=1.5)
    mismatches = []
    counts = {"hold": 0, "fail": 0}
    for label, conditions, holds_on_base in _condition_cases():
        for snap, what in ((base, "base"), (head, "head")):
            py = [_normalize(f) for f in edit_patch._judge(conditions,
                                                            snap, base)]
            rust = [_normalize(f)
                    for f in ren_native.judge_conditions(conditions,
                                                         snap, base)]
            if py != rust:
                mismatches.append((label, what, py, rust))
            elif py:
                counts["fail"] += 1
            else:
                counts["hold"] += 1
    assert not mismatches, mismatches
    print(f"conditions parity: {counts['hold']} hold, "
          f"{counts['fail']} fail, verdicts agree")


def _problems_cases():
    from dataclasses import replace as dc_replace

    def _sem(capability_id, **overrides):
        spec = next(s for s in capabilities.all()
                    if s.id == capability_id)
        sem = spec.execution.patch
        values = {"operations": list(sem.operations),
                  "conflict_domains": list(sem.conflict_domains),
                  "temporal_effect": sem.temporal_effect,
                  "merge_semantics": sem.merge_semantics}
        values.update(overrides)
        return dc_replace(sem, **values)

    return [
        ("registry", None, None),
        ("loose-merge",
         {"reel.set_properties": _sem("reel.set_properties",
                                      merge_semantics="commutative")},
         {"reel.set_properties"}),
        ("undeclared-domain",
         {"reel.set_properties": _sem("reel.set_properties",
                                      conflict_domains=("picture_transform",
                                                        "bogus"))},
         {"reel.set_properties"}),
        ("unknown-operation",
         {"reel.set_properties": _sem("reel.set_properties",
                                      operations=("clip.set_property",
                                                  "marker.add"))},
         {"reel.set_properties"}),
        ("no-operations",
         {"reel.set_properties": _sem("reel.set_properties",
                                      operations=())},
         {"reel.set_properties"}),
        ("unregistered",
         {"ghost.capability": _sem("reel.set_properties")},
         {"reel.set_properties"}),
    ]


def test_problems_parity():
    """Every capability declaration its own operations break, Python
    against Rust (compared as a set: the order is the dict's on one
    side and the sorted keys' on the other)."""
    mismatches = []
    for label, declarations, capability_ids in _problems_cases():
        if declarations is None:
            declarations = {
                spec.id: spec.execution.patch
                for spec in capabilities.all()
                if spec.execution.patch is not None
            }
            capability_ids = capabilities.ids()
        py = {_normalize(f).replace('"', "'")
              for f in patch_algebra._problems_python(declarations,
                                                      capability_ids)}
        rust = {_normalize(f).replace('"', "'")
                for f in ren_native.problems(
                    {cap_id: _sem_dict(sem)
                     for cap_id, sem in declarations.items()},
                    list(capability_ids))}
        if py != rust:
            mismatches.append((label, sorted(py - rust),
                               sorted(rust - py)))
    assert not mismatches, mismatches


def _sem_dict(sem):
    return {"operations": list(sem.operations),
            "conflict_domains": list(sem.conflict_domains),
            "temporal_effect": sem.temporal_effect,
            "merge_semantics": sem.merge_semantics}
