"""Typed, project-scoped copy of a reel's TV-off ending.

The request names a live reference reel, the target reel's last picture
item and the frame where its native held segment begins. The broker
appends the target picture's own last played source frame, sets that
segment to 0% speed, copies only the reference TV-off Fusion composition,
and places the two-line logo after the hold. A present logo
already at the body cut is re-placed after the hold with its source range,
properties, enabled state, colour and clip markers carried across.

This is data-only broker work. It never switches Resolve projects and one
job holds the exclusive broker turn for one target reel.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from library.tools.resolved.jobs import EXCLUSIVE, JobRefused

KIND = "reel.copy_tv_ending"
PROJECT_NAME = "Podcast (field test)"
PROJECT_SLUG = "geo-podcast"
_PARAMS = frozenset({
    "project", "project_slug", "source_timeline", "target_timeline",
    "target_timeline_id", "target_picture_uid", "target_picture_track",
    "record_frame",
})
_POWER_TRANSFORM_KEYS = (
    "ZoomX", "ZoomY", "Pan", "Tilt", "RotationAngle",
    "AnchorPointX", "AnchorPointY", "CropLeft", "CropRight",
    "CropTop", "CropBottom", "FlipX", "FlipY",
)


def _reel_number(name: str) -> int | None:
    parts = name.split(maxsplit=2)
    if len(parts) < 2 or parts[0] != "Reel" or not parts[1].isdigit():
        return None
    return int(parts[1])


def _text(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JobRefused(f"{KIND} needs a non-empty {key!r}")
    return value.strip()


def prepare(params: dict) -> dict:
    """Validate the closed request and return its exclusive job shape."""
    if not isinstance(params, dict):
        raise JobRefused(f"{KIND} params must be an object")
    unknown = set(params) - _PARAMS
    if unknown:
        raise JobRefused(
            f"{KIND} refuses unknown field(s) or caller-supplied paths: "
            f"{sorted(unknown)}")
    project = _text(params, "project")
    if project != PROJECT_NAME:
        raise JobRefused(
            f"{KIND} may only touch {PROJECT_NAME!r}, not {project!r}")
    slug = _text(params, "project_slug")
    if slug != PROJECT_SLUG:
        raise JobRefused(
            f"{KIND} may only use project slug {PROJECT_SLUG!r}, "
            f"not {slug!r}")
    source = _text(params, "source_timeline")
    target = _text(params, "target_timeline")
    if (_reel_number(source) not in {10, 26}
            or _reel_number(target) not in {22, 27}
            or source == target):
        raise JobRefused(
            f"{KIND} accepts Reel 10 or 26 as the exact reference and "
            "Reel 22 or 27 as the exact target")
    picture_uid = params.get("target_picture_uid")
    picture_track = params.get("target_picture_track")
    if bool(picture_uid) == bool(picture_track):
        raise JobRefused(
            f"{KIND} needs exactly one of target_picture_uid or "
            "target_picture_track")
    if picture_uid is not None:
        _text(params, "target_picture_uid")
    if picture_track is not None:
        _text(params, "target_picture_track")
    target_id = params.get("target_timeline_id")
    if target_id is not None:
        _text(params, "target_timeline_id")
    frame = params.get("record_frame")
    if (not isinstance(frame, int) or isinstance(frame, bool) or frame < 0):
        raise JobRefused(f"{KIND} record_frame must be a non-negative integer")
    return {
        "mode": EXCLUSIVE,
        "priority": "mutation",
        "executed": True,
        "project": project,
        "timeline": target,
        "locality": "timeline",
        "coalesce_key": None,
        "qualification": False,
    }


def _exact_timeline(project, name: str, timeline_id: str | None = None):
    matches = []
    count = int(project.GetTimelineCount() or 0)
    for index in range(1, count + 1):
        timeline = project.GetTimelineByIndex(index)
        if (timeline and timeline.GetName() == name
                and (timeline_id is None
                     or str(timeline.GetUniqueId() or "") == timeline_id)):
            matches.append(timeline)
    if len(matches) != 1:
        identity = (f"name {name!r} and ID {timeline_id!r}"
                    if timeline_id else f"exact name {name!r}")
        raise JobRefused(f"{KIND} expected one timeline with {identity}; "
                         f"found {len(matches)}")
    return matches[0]


def _item_by_uid(timeline, uid: str):
    matches = []
    for track in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
        for item in timeline.GetItemListInTrack("video", track) or ():
            if str(item.GetUniqueId() or "") == uid:
                matches.append((track, item))
    if len(matches) != 1:
        raise JobRefused(
            f"{KIND} expected exactly one target picture with UID {uid!r}; "
            f"found {len(matches)}")
    return matches[0]


def _item_by_track_end(timeline, track_name: str, record_frame: int):
    rows = [index for index in range(
        1, int(timeline.GetTrackCount("video") or 0) + 1)
        if timeline.GetTrackName("video", index) == track_name]
    if len(rows) != 1:
        raise JobRefused(
            f"{KIND} expected one picture row named {track_name!r}; "
            f"found {len(rows)}")
    row = rows[0]
    matches = []
    for item in timeline.GetItemListInTrack("video", row) or ():
        pool_item = item.GetMediaPoolItem()
        media_type = (str(pool_item.GetClipProperty("Type") or "")
                      .strip().lower() if pool_item is not None else "")
        if (int(item.GetEnd()) == record_frame
                and media_type.startswith("video")):
            matches.append(item)
    if len(matches) != 1:
        raise JobRefused(
            f"{KIND} expected one video picture ending at frame "
            f"{record_frame} on row {track_name!r}; found {len(matches)}")
    return row, matches[0]


def _item_path(item) -> str:
    pool_item = item.GetMediaPoolItem()
    if pool_item is None:
        return ""
    return str(pool_item.GetClipProperty("File Path") or "")


def _track_index(project, timeline, wanted_name: str) -> int:
    matches = [index for index in range(
        1, int(timeline.GetTrackCount("video") or 0) + 1)
        if timeline.GetTrackName("video", index) == wanted_name]
    if len(matches) != 1:
        raise JobRefused(
            f"{KIND} expected one video row {wanted_name!r} on "
            f"{timeline.GetName()!r}; found {len(matches)}")
    return matches[0]


def _plan_post_header(timeline, record_frame: int, fps: float) -> list[dict]:
    """Plan the one picture dressing row against this ending boundary."""
    from library.tools import still_overlays

    plans = []
    found_header_row = False
    for row in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
        row_name = timeline.GetTrackName("video", row)
        if row_name != "Post Header":
            continue
        found_header_row = True
        triples = []
        for item in timeline.GetItemListInTrack("video", row) or ():
            pool_item = item.GetMediaPoolItem()
            path = (pool_item.GetClipProperty("File Path")
                    if pool_item is not None else None)
            triples.append((item, pool_item, path))
        swaps, _skipped, refused = still_overlays.plan_swaps(
            triples, row_name, row, int(timeline.GetStartFrame()),
            int(timeline.GetEndFrame()), fps, picture_end=record_frame)
        if refused:
            reasons = "; ".join(
                f"{entry['item']}: {entry['why']}" for entry in refused)
            raise JobRefused(
                f"{KIND} cannot end the Post Header at frame "
                f"{record_frame}: {reasons}")
        planned_uids = {str(swap["item_uid"]) for swap in swaps}
        for item, _pool_item, _path in triples:
            if (int(item.GetEnd()) > record_frame
                    and item.GetClipEnabled() is True
                    and str(item.GetUniqueId()) not in planned_uids):
                raise JobRefused(
                    f"{KIND} could not plan the active Post Header "
                    f"{item.GetName()!r} through frame {record_frame}")
        plans.extend(swaps)
    if _reel_number(timeline.GetName()) == 22 and not found_header_row:
        raise JobRefused(
            f"{KIND} Reel 22 has no exact 'Post Header' row to end")
    return plans


def _apply_post_header(resolve, project, pool, timeline, fps: float,
                       plans: list[dict], project_folder: str,
                       applied: list[dict]) -> list[dict]:
    """Apply the existing still-overlays resize/swap path to its plan."""
    from library.tools import still_overlays

    for swap in plans:
        if swap.get("keep_still"):
            receipt = still_overlays._resize_existing_still(
                resolve, project, pool, timeline, fps, swap,
                project_folder, timeline.GetName())
        else:
            receipt = still_overlays._apply_swap(
                resolve, project, pool, timeline, fps, swap,
                project_folder, timeline.GetName(), reuse={})
        if ([int(receipt["record"][0]), int(receipt["record"][1])]
                != [int(swap["record"][0]), int(swap["want_end"]) ]):
            raise JobRefused(
                f"{KIND} Post Header {swap['label']!r} reads back at "
                f"{receipt['record']}, expected "
                f"[{swap['record'][0]}, {swap['want_end']}]")
        applied.append(receipt)
    return applied


def _reference_items(timeline):
    from library.tools.reel_ending import is_freeze_path

    freezes = []
    logos = []
    for track in range(1, int(timeline.GetTrackCount("video") or 0) + 1):
        row_name = timeline.GetTrackName("video", track)
        for item in timeline.GetItemListInTrack("video", track) or ():
            path = _item_path(item)
            basename = Path(path).name.lower()
            if is_freeze_path(path):
                freezes.append((track, row_name, item))
            if basename.startswith("logo_bulb_lines_safe_"):
                logos.append((track, row_name, item))
    if len(freezes) != 1:
        raise JobRefused(
            f"{KIND} reference {timeline.GetName()!r} must carry exactly "
            f"one rendered freeze; found {len(freezes)}")
    if len(logos) != 1:
        raise JobRefused(
            f"{KIND} reference {timeline.GetName()!r} must carry exactly "
            f"one two-line logo; found {len(logos)}")
    return freezes[0], logos[0]


def _fusion_signature(content: str) -> dict:
    """The named TV nodes and all their keyframes, with media ignored."""
    from library.tools.fusion.nodes import BezierSpline, FusionNode
    from library.tools.fusion.parser import parse_comp

    comp = parse_comp(content)
    nodes = [node for node in comp.nodes if isinstance(node, FusionNode)]
    splines = [node for node in comp.nodes if isinstance(node, BezierSpline)]
    expected_nodes = {
        "band": ("PowerBand", "Background"),
        "mask": ("PowerBandMask", "RectangleMask"),
        "merge": ("PowerBandMerge", "Merge"),
        "dot": ("PowerDot", "Transform"),
        "decay": ("PowerDecay", "BrightnessContrast"),
    }
    roles = {}
    for role, (prefix, tool_type) in expected_nodes.items():
        found = [node for node in nodes
                 if node.name.startswith(prefix)
                 and node.tool_type == tool_type]
        if len(found) != 1:
            raise JobRefused(
                f"{KIND} comp has {len(found)} {role} node(s); "
                "expected one TV-off graph")
        roles[role] = (found[0].name, found[0].tool_type)

    expected_splines = {
        "band": ("PowerBand", "Height"),
        "dot": ("PowerDot", "Size"),
        "decay": ("PowerDecay", "Gain"),
    }
    curves = {}
    for role, (prefix, suffix) in expected_splines.items():
        found = [spline for spline in splines
                 if spline.name.startswith(prefix)
                 and spline.name.endswith(suffix)]
        if len(found) != 1 or not found[0].keyframes:
            raise JobRefused(
                f"{KIND} comp is missing its {role} TV-off spline")
        curves[role] = {
            "name": found[0].name,
            "keyframes": [
                (int(key.frame), float(key.value), key.lh, key.rh,
                 key.flags)
                for key in found[0].keyframes
            ],
        }
    return {"nodes": roles, "splines": curves}


def _export_comp(item, comp_index: int, path: Path, label: str) -> str:
    exported = item.ExportFusionComp(str(path), comp_index)
    if (not exported or not path.is_file() or path.stat().st_size == 0):
        raise JobRefused(
            f"{KIND} could not export {label} comp {comp_index}; "
            f"ExportFusionComp returned {exported!r}")
    return path.read_text(encoding="utf-8")


def _reference_tv_comp(freeze_item, directory: Path) -> tuple[int, Path, dict]:
    matches = []
    count = int(freeze_item.GetFusionCompCount() or 0)
    for index in range(1, count + 1):
        path = directory / f"reference-c{index}.comp"
        content = _export_comp(
            freeze_item, index, path, "reference freeze")
        try:
            signature = _fusion_signature(content)
        except JobRefused:
            continue
        matches.append((index, path, signature))
    if len(matches) != 1:
        raise JobRefused(
            f"{KIND} reference freeze must carry exactly one verified "
            f"TV-off comp; found {len(matches)}")
    return matches[0]


def _target_tv_comp(item, directory: Path) -> tuple[int, Path, dict] | None:
    matches = []
    count = int(item.GetFusionCompCount() or 0)
    for index in range(1, count + 1):
        path = directory / f"target-c{index}.comp"
        content = _export_comp(item, index, path, "target freeze")
        try:
            signature = _fusion_signature(content)
        except JobRefused:
            continue
        matches.append((index, path, signature))
    if len(matches) > 1:
        raise JobRefused(
            f"{KIND} target freeze carries {len(matches)} TV-off comps; "
            "the ending is ambiguous")
    return matches[0] if matches else None


def _read_int(value, label: str) -> int:
    if isinstance(value, bool):
        raise JobRefused(f"{KIND} {label} is unreadable")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise JobRefused(f"{KIND} {label} is unreadable") from exc
    if number != value:
        raise JobRefused(f"{KIND} {label} is not an integer frame")
    return number


def _source_frame_count(pool_item) -> int:
    """Read Resolve's string-valued media frame count as an integer."""
    value = pool_item.GetClipProperty("Frames")
    if isinstance(value, bool):
        raise JobRefused(f"{KIND} target source frame count is unreadable")
    try:
        count = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise JobRefused(
            f"{KIND} target source frame count is unreadable") from exc
    if not math.isfinite(count) or count <= 0 or not count.is_integer():
        raise JobRefused(
            f"{KIND} target source frame count is not an integer frame")
    return int(count)


def _find_legacy_freeze(timeline, row: int, start: int, duration: int):
    from library.tools.reel_ending import is_freeze_path

    matches = []
    for item in timeline.GetItemListInTrack("video", row) or ():
        if (is_freeze_path(_item_path(item))
                and int(item.GetStart()) == start
                and int(item.GetDuration()) == duration):
            matches.append(item)
    if len(matches) > 1:
        raise JobRefused(
            f"{KIND} target has multiple legacy freezes at V{row} "
            f"frame {start}")
    return matches[0] if matches else None


def _find_native_hold(timeline, row: int, path: str, source_start: int,
                      start: int, duration: int):
    from library.tools.reel_ending import is_native_hold

    matches = []
    wanted_path = os.path.normcase(os.path.normpath(path))
    for item in timeline.GetItemListInTrack("video", row) or ():
        if (os.path.normcase(os.path.normpath(_item_path(item)))
                == wanted_path
                and int(item.GetSourceStartFrame()) == source_start
                and int(item.GetStart()) == start
                and int(item.GetDuration()) == duration
                and is_native_hold(item)):
            matches.append(item)
    if len(matches) > 1:
        raise JobRefused(
            f"{KIND} target has multiple native holds from frame "
            f"{source_start} at V{row} frame {start}")
    return matches[0] if matches else None


def _find_logo(timeline, row: int, path: str):
    matches = []
    for item in timeline.GetItemListInTrack("video", row) or ():
        if _item_path(item) == path:
            matches.append(item)
    if len(matches) > 1:
        raise JobRefused(
            f"{KIND} target row V{row} has multiple copies of the "
            "reference two-line logo")
    return matches[0] if matches else None


def _assert_span_free(timeline, row: int, start: int, end: int,
                      *, allowed_uids=(), label: str) -> None:
    allowed = {str(uid) for uid in allowed_uids}
    collisions = []
    for item in timeline.GetItemListInTrack("video", row) or ():
        if str(item.GetUniqueId() or "") in allowed:
            continue
        if int(item.GetStart()) < end and int(item.GetEnd()) > start:
            collisions.append(
                f"{item.GetName()} [{item.GetStart()}, {item.GetEnd()})")
    if collisions:
        raise JobRefused(
            f"{KIND} {label} span [{start}, {end}) collides on V{row} "
            f"with {collisions}")


def _logo_comp_is_passthrough(item) -> bool:
    from library.tools.reel_read import PASSTHROUGH_TOOLS

    for index in range(1, int(item.GetFusionCompCount() or 0) + 1):
        comp = item.GetFusionCompByIndex(index)
        if comp is None or isinstance(comp, str):
            return False
        tools = comp.GetToolList(False)
        if not isinstance(tools, dict):
            return False
        kinds = [tool.GetAttrs("TOOLS_RegID") for tool in tools.values()]
        if not kinds or any(kind not in PASSTHROUGH_TOOLS for kind in kinds):
            return False
    return True


def _logo_capture(item) -> dict:
    markers = dict(item.GetMarkers() or {})
    if not _logo_comp_is_passthrough(item):
        raise JobRefused(
            f"{KIND} refuses to move a logo with a drawing Fusion comp; "
            "the clip must remain a plain brand asset")
    enabled = item.GetClipEnabled()
    if not isinstance(enabled, bool):
        raise JobRefused(f"{KIND} logo enabled state is unreadable")
    return {
        "uid": str(item.GetUniqueId() or ""),
        "original_item": item,
        "pool_item": item.GetMediaPoolItem(),
        "start": int(item.GetStart()),
        "duration": int(item.GetDuration()),
        "source_start": _read_int(item.GetSourceStartFrame(),
                                   "logo source start"),
        "properties": dict(item.GetProperty() or {}),
        "enabled": enabled,
        "color": item.GetClipColor(),
        "markers": markers,
        "nodes": int(item.GetNumNodes() or 0),
    }


def _legacy_freeze_capture(item, directory: Path) -> dict:
    """Capture enough state to remove and inverse-restore one baked tail."""
    from library.tools.reel_read import _speed_percent

    enabled = item.GetClipEnabled()
    if not isinstance(enabled, bool):
        raise JobRefused(
            f"{KIND} legacy freeze enabled state is unreadable; "
            "cannot prove an inverse restore")
    pool_item = item.GetMediaPoolItem()
    if pool_item is None:
        raise JobRefused(
            f"{KIND} legacy freeze has no media pool item to restore")
    speed = _speed_percent(item)
    if speed is None or not math.isfinite(speed):
        raise JobRefused(
            f"{KIND} legacy freeze speed is unreadable; "
            "cannot prove an inverse restore")
    comp_count = _read_int(item.GetFusionCompCount(),
                           "legacy freeze Fusion comp count")
    tv_comp = _target_tv_comp(item, directory)
    if comp_count != 1 or tv_comp is None or tv_comp[0] != 1:
        raise JobRefused(
            f"{KIND} legacy freeze must have one Fusion comp and it must be "
            "the verified TV-off comp to permit an exact inverse restore")
    return {
        "uid": str(item.GetUniqueId() or ""),
        "original_item": item,
        "pool_item": pool_item,
        "start": _read_int(item.GetStart(), "legacy freeze record start"),
        "duration": _read_int(item.GetDuration(), "legacy freeze duration"),
        "speed": speed,
        "source_start": _read_int(item.GetSourceStartFrame(),
                                   "legacy freeze source start"),
        "properties": dict(item.GetProperty() or {}),
        "enabled": enabled,
        "color": item.GetClipColor(),
        "markers": dict(item.GetMarkers() or {}),
        "nodes": _read_int(item.GetNumNodes(), "legacy freeze node count"),
        "tv_comp_content": tv_comp[1].read_text(encoding="utf-8"),
        "tv_signature": tv_comp[2],
    }


def _restore_legacy_freeze(pool, timeline, row: int, capture: dict, *,
                           project_folder: str, timeline_name: str,
                           orphaned_items: list, directory: Path) -> dict:
    """Recreate the prior baked tail if a native replacement is refused."""
    landed_row, item = _append_item(
        pool, timeline, media_pool_item=capture["pool_item"], row=row,
        record_frame=capture["start"],
        source_start=capture["source_start"],
        duration=capture["duration"], label="inverse-restored legacy freeze",
        orphaned_items=orphaned_items)
    orphaned_items.append(item)
    _verify_appended(
        item, landed_row, media_pool_item=capture["pool_item"], row=row,
        record_frame=capture["start"], duration=capture["duration"],
        label="inverse-restored legacy freeze")
    from library.tools.reel_read import _speed_percent
    current_speed = _speed_percent(item)
    if current_speed is None or not math.isclose(
            current_speed, capture["speed"], abs_tol=0.001):
        changed_speed = item.SetSpeed({
            "Percentage": capture["speed"], "RippleTimeline": False})
        actual_speed = _speed_percent(item)
        if (not changed_speed or actual_speed is None
                or not math.isclose(actual_speed, capture["speed"],
                                    abs_tol=0.001)):
            raise JobRefused(
                f"{KIND} inverse restore could not restore legacy freeze speed")
    _apply_properties(
        item, capture["properties"], project_folder=project_folder,
        timeline_name=timeline_name,
        identity={"track": f"V{row}", "record_frame": capture["start"],
                  "restored_legacy_freeze": True})
    if not item.SetClipEnabled(capture["enabled"]):
        if item.GetClipEnabled() != capture["enabled"]:
            raise JobRefused(
                f"{KIND} inverse restore could not restore legacy freeze enabled state")
    if item.GetClipEnabled() != capture["enabled"]:
        raise JobRefused(
            f"{KIND} inverse restore lost legacy freeze enabled state")
    if capture["color"]:
        returned = item.SetClipColor(capture["color"])
        if not returned and item.GetClipColor() != capture["color"]:
            raise JobRefused(
                f"{KIND} inverse restore lost legacy freeze clip colour")
    _copy_clip_markers(capture["markers"], item)
    if capture["nodes"] > 1:
        original = capture["original_item"]
        if (not original.CopyGrades([item])
                or int(item.GetNumNodes() or 0) != capture["nodes"]):
            raise JobRefused(
                f"{KIND} inverse restore could not restore legacy freeze grade")
    comp_path = directory / "inverse-legacy-tv-off.comp"
    comp_path.write_text(capture["tv_comp_content"], encoding="utf-8")
    comp_receipt = _copy_reference_comp(
        item, 1, comp_path, capture["tv_signature"], directory)
    if (int(item.GetStart()) != capture["start"]
            or int(item.GetDuration()) != capture["duration"]
            or int(item.GetSourceStartFrame()) != capture["source_start"]
            or dict(item.GetProperty() or {}) != capture["properties"]
            or dict(item.GetMarkers() or {}) != capture["markers"]):
        raise JobRefused(
            f"{KIND} inverse-restored legacy freeze did not read back exactly")
    orphaned_items[:] = [other for other in orphaned_items if other is not item]
    return {"item": item, "comp": comp_receipt}


def _apply_properties(item, values: dict, *, project_folder: str,
                      timeline_name: str, identity: dict) -> None:
    from library.tools.transform_write_log import set_property

    before = dict(item.GetProperty() or {})
    for key, value in values.items():
        if before.get(key) == value:
            continue
        returned = set_property(
            item, key, value, item_identity=identity,
            project_folder=project_folder, timeline_name=timeline_name)
        after_value = (item.GetProperty() or {}).get(key)
        if not returned and after_value != value:
            raise JobRefused(
                f"{KIND} Resolve refused logo property {key}={value!r}")
        if after_value != value:
            raise JobRefused(
                f"{KIND} logo property {key} read back {after_value!r}, "
                f"wanted {value!r}")


def _copy_clip_markers(markers: dict, target) -> None:
    for frame, marker in sorted((markers or {}).items(),
                                key=lambda pair: int(pair[0])):
        added = target.AddMarker(
            int(frame), marker.get("color") or "", marker.get("name") or "",
            marker.get("note") or "", int(marker.get("duration") or 1),
            marker.get("customData") or "")
        if not added:
            raise JobRefused(
                f"{KIND} Resolve declined clip marker "
                f"{marker.get('name')!r}")
    if dict(target.GetMarkers() or {}) != dict(markers or {}):
        raise JobRefused(f"{KIND} clip markers did not read back")


def _video_items(timeline):
    return [(row, item)
            for row in range(1, int(timeline.GetTrackCount("video") or 0) + 1)
            for item in timeline.GetItemListInTrack("video", row) or ()]


def _append_item(pool, timeline, *, media_pool_item, row: int,
                 record_frame: int, source_start: int, duration: int,
                 label: str, orphaned_items: list):
    before_uids = {str(item.GetUniqueId() or "")
                   for _track, item in _video_items(timeline)}
    pool_uid = str(media_pool_item.GetUniqueId() or "")
    append_error = None
    try:
        returned = pool.AppendToTimeline([{
            "mediaPoolItem": media_pool_item,
            "startFrame": source_start,
            "endFrame": source_start + duration,
            "mediaType": 1,
            "trackIndex": row,
            "recordFrame": record_frame,
        }])
    except Exception as exc:  # Resolve can mutate before surfacing failure.
        returned = None
        append_error = exc
    landed = [(track, item) for track, item in _video_items(timeline)
              if str(item.GetUniqueId() or "") not in before_uids]
    if append_error is not None or len(landed) != 1:
        orphaned_items.extend(item for _track, item in landed)
        if landed:
            candidates = [item for _track, item in landed]
            removed = timeline.DeleteClips(candidates, False)
            remaining = {str(item.GetUniqueId() or "")
                         for _track, item in _video_items(timeline)}
            if (not removed or any(str(item.GetUniqueId() or "") in remaining
                                   for item in candidates)):
                raise JobRefused(
                    f"{KIND} AppendToTimeline created {len(landed)} "
                    f"{label} "
                    "items and its inverse could not remove all of them")
        if append_error is not None:
            raise JobRefused(
                f"{KIND} AppendToTimeline raised after creating "
                f"{len(landed)} {label} item(s): {append_error}") \
                from append_error
        raise JobRefused(
            f"{KIND} AppendToTimeline returned {returned!r}; expected one "
            f"{label} "
            f"new item from pool UID {pool_uid!r}, "
            f"found {len(landed)}")
    return landed[0]


def _verify_appended(item, landed_row: int, *, media_pool_item,
                     row: int, record_frame: int, duration: int,
                     label: str) -> None:
    pool_item = item.GetMediaPoolItem()
    actual_pool_uid = (str(pool_item.GetUniqueId() or "")
                       if pool_item is not None else "")
    wanted_pool_uid = str(media_pool_item.GetUniqueId() or "")
    if (landed_row != row or int(item.GetStart()) != record_frame
            or int(item.GetDuration()) != duration
            or actual_pool_uid != wanted_pool_uid):
        raise JobRefused(
            f"{KIND} {label} landed on V{landed_row} at "
            f"[{item.GetStart()}, {item.GetEnd()}) from pool UID "
            f"{actual_pool_uid!r}; expected V{row} at "
            f"[{record_frame}, {record_frame + duration}) from "
            f"{wanted_pool_uid!r}")


def _copy_captured_logo_state(captured: dict, target, *, project_folder: str,
                              timeline_name: str, record_frame: int) -> None:
    _apply_properties(
        target, captured["properties"], project_folder=project_folder,
        timeline_name=timeline_name,
        identity={"track": "closing logo", "record_frame": record_frame,
                  "source_item_uid": captured["uid"]})
    enabled_return = target.SetClipEnabled(bool(captured["enabled"]))
    if (not enabled_return
            and target.GetClipEnabled() != captured["enabled"]):
        raise JobRefused(f"{KIND} could not carry the logo enabled state")
    if target.GetClipEnabled() != captured["enabled"]:
        raise JobRefused(f"{KIND} logo enabled state did not read back")
    if captured["color"]:
        returned = target.SetClipColor(captured["color"])
        if not returned and target.GetClipColor() != captured["color"]:
            raise JobRefused(f"{KIND} could not carry the logo clip colour")
    _copy_clip_markers(captured["markers"], target)
    if captured["nodes"] > 1:
        copied = captured["original_item"].CopyGrades([target])
        if not copied or int(target.GetNumNodes() or 0) != captured["nodes"]:
            raise JobRefused(f"{KIND} could not carry the logo grade")


def _restore_logo(pool, timeline, row: int, capture: dict, *,
                  project_folder: str,
                  timeline_name: str, orphaned_items: list) -> dict:
    landed_row, item = _append_item(
        pool, timeline,
        media_pool_item=capture["pool_item"], row=row,
        record_frame=capture["start"], source_start=capture["source_start"],
        duration=capture["duration"], label="inverse-restored logo",
        orphaned_items=orphaned_items)
    orphaned_items.append(item)
    try:
        _verify_appended(
            item, landed_row, media_pool_item=capture["pool_item"], row=row,
            record_frame=capture["start"], duration=capture["duration"],
            label="inverse-restored logo")
    except Exception:
        removed = timeline.DeleteClips([item], False)
        remaining = {str(other.GetUniqueId() or "")
                     for _track, other in _video_items(timeline)}
        if (not removed
                or str(item.GetUniqueId() or "") in remaining):
            raise JobRefused(
                f"{KIND} inverse restore could not remove its misplaced "
                "logo before refusing")
        raise
    _apply_properties(
        item, capture["properties"], project_folder=project_folder,
        timeline_name=timeline_name,
        identity={"track": f"V{row}", "record_frame": capture["start"],
                  "restored_logo": True})
    if capture["enabled"] is not None:
        returned = item.SetClipEnabled(bool(capture["enabled"]))
        if not returned and item.GetClipEnabled() != capture["enabled"]:
            raise JobRefused(f"{KIND} inverse restore could not re-enable logo")
    if capture["color"]:
        returned = item.SetClipColor(capture["color"])
        if not returned and item.GetClipColor() != capture["color"]:
            raise JobRefused(f"{KIND} inverse restore lost logo clip colour")
    for frame, marker in sorted(capture["markers"].items(),
                                key=lambda pair: int(pair[0])):
        added = item.AddMarker(
            int(frame), marker.get("color") or "", marker.get("name") or "",
            marker.get("note") or "", int(marker.get("duration") or 1),
            marker.get("customData") or "")
        if not added:
            raise JobRefused(
                f"{KIND} inverse restore could not restore logo marker "
                f"{marker.get('name')!r}")
    if dict(item.GetMarkers() or {}) != capture["markers"]:
        raise JobRefused(f"{KIND} inverse restore lost logo clip markers")
    if capture["nodes"] > 1:
        # Meaningful Fusion graphs were refused before mutation; this is
        # only the Color-page grade, copied from the existing clip later.
        original = capture["original_item"]
        if not original.CopyGrades([item]):
            raise JobRefused(f"{KIND} inverse restore could not restore logo grade")
    orphaned_items[:] = [other for other in orphaned_items if other is not item]
    return item


def _snapshot_without(snapshot: dict, *, remove_uids=()) -> dict:
    result = json.loads(json.dumps(snapshot, default=str))
    doomed = {str(uid) for uid in remove_uids}
    if doomed:
        for key, items in result["rows"].items():
            result["rows"][key] = [
                item for item in items if str(item["uid"]) not in doomed]
    return result


def _inventory_matches(before: dict, after: dict) -> bool:
    """Compare a restored inventory while allowing Resolve to renew UIDs."""
    left = json.loads(json.dumps(before, default=str))
    right = json.loads(json.dumps(after, default=str))
    for snapshot in (left, right):
        for items in snapshot["rows"].values():
            for item in items:
                item.pop("uid", None)
            items.sort(key=lambda item: json.dumps(
                item, sort_keys=True, default=str))
    return left == right


def _verify_inventory(before: dict, after: dict, *,
                      remove_before=(), remove_after=(),
                      freeze_uid: str | None = None,
                      freeze_comp_added: bool = False,
                      expected_end: int) -> list[str]:
    problems = []
    left = _snapshot_without(before, remove_uids=remove_before)
    right = _snapshot_without(after, remove_uids=remove_after)
    if int(after["end"][1]) != int(expected_end):
        problems.append(
            f"timeline ends at {after['end'][1]}, expected {expected_end}")
    if before["end"][0] != after["end"][0]:
        problems.append("timeline start frame changed")
    left["end"] = [before["end"][0], int(expected_end)]
    right["end"] = [after["end"][0], int(expected_end)]
    if freeze_uid and freeze_comp_added:
        before_item = next((item for items in left["rows"].values()
                            for item in items
                            if str(item["uid"]) == str(freeze_uid)), None)
        after_item = next((item for items in right["rows"].values()
                           for item in items
                           if str(item["uid"]) == str(freeze_uid)), None)
        if before_item is None or after_item is None:
            problems.append("pre-existing freeze disappeared")
        elif int(after_item["fusion_comps"]) != (
                int(before_item["fusion_comps"]) + 1):
            problems.append(
                "pre-existing freeze did not gain exactly one TV-off comp")
        else:
            after_item["fusion_comps"] = before_item["fusion_comps"]
    if left != right:
        problems.append("timeline inventory has an unexplained difference")
    if before["timeline_markers"] != after["timeline_markers"]:
        problems.append("timeline markers changed")
    return problems


def _restore_target(pool, timeline, *, freeze_item, freeze_added: bool,
                    comp_added: bool, comp_names: list, logo_item,
                    logo_added: bool, old_logo, logo_moved: bool,
                    old_freeze, old_freeze_removed: bool,
                    orphaned_items: list,
                    logo_row: int, header_plans: list,
                    header_applied: list, resolve, project, fps: float,
                    project_folder: str, timeline_name: str) -> dict:
    from library.tools import still_overlays

    details = {"freeze_removed": not freeze_added,
               "tv_comp_removed": not comp_added,
               "new_logo_removed": not logo_added,
               "legacy_freeze_restored": not old_freeze_removed,
               "append_orphans_removed": False,
               "old_logo_restored": not logo_moved,
               "post_header_restored": False,
               "errors": []}
    try:
        doomed = []
        if logo_added and logo_item is not None:
            doomed.append(logo_item)
        if freeze_added and freeze_item is not None:
            doomed.append(freeze_item)
        doomed.extend(orphaned_items)
        current_uids = {str(item.GetUniqueId() or "")
                        for _row, item in _video_items(timeline)}
        unique_doomed = {}
        for item in doomed:
            uid = str(item.GetUniqueId() or "")
            if uid in current_uids:
                unique_doomed[uid] = item
        if unique_doomed and not timeline.DeleteClips(
                list(unique_doomed.values()), False):
            raise JobRefused(f"{KIND} inverse restore could not remove tail items")
        remaining = {str(item.GetUniqueId() or "")
                     for _row, item in _video_items(timeline)}
        details["freeze_removed"] = (
            not freeze_added or freeze_item is None
            or str(freeze_item.GetUniqueId() or "") not in remaining)
        details["new_logo_removed"] = (
            not logo_added or logo_item is None
            or str(logo_item.GetUniqueId() or "") not in remaining)
        details["append_orphans_removed"] = all(
            str(item.GetUniqueId() or "") not in remaining
            for item in orphaned_items)
        if comp_added and not freeze_added:
            if freeze_item is None or not comp_names:
                raise JobRefused(
                    f"{KIND} inverse restore cannot identify the added TV comp")
            names = list(freeze_item.GetFusionCompNameList() or [])
            for comp_name in comp_names:
                if comp_name in names:
                    removed = freeze_item.DeleteFusionCompByName(comp_name)
                    names = list(freeze_item.GetFusionCompNameList() or [])
                    if not removed and comp_name in names:
                        raise JobRefused(
                            f"{KIND} inverse restore could not remove comp "
                            f"{comp_name!r}")
            details["tv_comp_removed"] = not (
                set(comp_names)
                & set(freeze_item.GetFusionCompNameList() or []))
        elif comp_added and freeze_added:
            details["tv_comp_removed"] = details["freeze_removed"]
        if old_freeze_removed:
            if old_freeze is None:
                raise JobRefused(
                    f"{KIND} inverse restore has no legacy freeze capture")
            with tempfile.TemporaryDirectory(
                    prefix="ren-tv-ending-restore-") as tmp:
                restored_freeze = _restore_legacy_freeze(
                    pool, timeline, old_freeze["row"],
                    old_freeze["capture"],
                    project_folder=project_folder,
                    timeline_name=timeline_name,
                    orphaned_items=orphaned_items,
                    directory=Path(tmp))
            item = restored_freeze["item"]
            details["legacy_freeze_restored"] = (
                int(item.GetStart()) == old_freeze["capture"]["start"]
                and int(item.GetDuration())
                == old_freeze["capture"]["duration"]
                and int(item.GetSourceStartFrame())
                == old_freeze["capture"]["source_start"])
        if logo_moved and old_logo is not None:
            restored = _restore_logo(
                pool, timeline, logo_row, old_logo,
                project_folder=project_folder,
                timeline_name=timeline_name,
                orphaned_items=orphaned_items)
            details["old_logo_restored"] = (
                int(restored.GetStart()) == old_logo["start"]
                and int(restored.GetDuration()) == old_logo["duration"]
                and int(restored.GetSourceStartFrame()) == old_logo["source_start"]
                and dict(restored.GetProperty() or {}) == old_logo["properties"]
                and restored.GetClipEnabled() == old_logo["enabled"]
                and restored.GetClipColor() == old_logo["color"]
                and dict(restored.GetMarkers() or {}) == old_logo["markers"]
                and int(restored.GetNumNodes() or 0) == old_logo["nodes"])
        if header_plans:
            still_overlays._restore_swaps(
                resolve, project, pool, timeline, fps,
                header_plans, header_applied,
                project_folder, timeline_name)
            details["post_header_restored"] = True
        else:
            details["post_header_restored"] = True
    except Exception as exc:  # noqa: BLE001 - report inverse failure by name
        details["errors"].append(f"{type(exc).__name__}: {exc}")
        try:
            remaining = {str(item.GetUniqueId() or "")
                         for _row, item in _video_items(timeline)}
            details["freeze_removed"] = (
                not freeze_added or freeze_item is None
                or str(freeze_item.GetUniqueId() or "") not in remaining)
            details["new_logo_removed"] = (
                not logo_added or logo_item is None
                or str(logo_item.GetUniqueId() or "") not in remaining)
            if comp_added and freeze_added:
                details["tv_comp_removed"] = details["freeze_removed"]
            elif comp_added and freeze_item is not None:
                details["tv_comp_removed"] = not (
                    set(comp_names)
                    & set(freeze_item.GetFusionCompNameList() or []))
            pending = {str(item.GetUniqueId() or ""): item
                       for item in orphaned_items
                       if str(item.GetUniqueId() or "") in remaining}
            if pending:
                removed = timeline.DeleteClips(
                    list(pending.values()), False)
                remaining = {str(item.GetUniqueId() or "")
                             for _row, item in _video_items(timeline)}
                if (not removed
                        or any(uid in remaining for uid in pending)):
                    raise JobRefused(
                        f"{KIND} inverse retry could not remove partial "
                        "appended items")
            remaining = {str(item.GetUniqueId() or "")
                         for _row, item in _video_items(timeline)}
            details["append_orphans_removed"] = all(
                str(item.GetUniqueId() or "") not in remaining
                for item in orphaned_items)
            if old_freeze_removed and old_freeze is not None:
                details["legacy_freeze_restored"] = any(
                    _item_path(item) == _item_path(
                        old_freeze["capture"]["original_item"])
                    and int(item.GetStart()) == old_freeze["capture"]["start"]
                    and int(item.GetDuration())
                    == old_freeze["capture"]["duration"]
                    for row, item in _video_items(timeline)
                    if row == old_freeze["row"])
        except Exception as cleanup_exc:  # noqa: BLE001
            details["errors"].append(
                f"orphan cleanup: {type(cleanup_exc).__name__}: "
                f"{cleanup_exc}")
    details["restored"] = (
        details["freeze_removed"] and details["tv_comp_removed"]
        and details["new_logo_removed"]
        and details["legacy_freeze_restored"]
        and details["append_orphans_removed"]
        and details["old_logo_restored"]
        and details["post_header_restored"] and not details["errors"])
    return details


def _source_frame_seconds(picture, timeline_fps: float) -> tuple[str, float, int]:
    from library.tools.reel_read import _speed_percent

    pool_item = picture.GetMediaPoolItem()
    if pool_item is None:
        raise JobRefused(f"{KIND} target picture has no media pool item")
    path = str(pool_item.GetClipProperty("File Path") or "")
    if not path or not os.path.isfile(path):
        raise JobRefused(
            f"{KIND} target picture source is not readable on disk: {path!r}")
    raw_fps = pool_item.GetClipProperty("FPS")
    try:
        source_fps = float(raw_fps)
    except (TypeError, ValueError):
        source_fps = timeline_fps
    if not math.isfinite(source_fps) or source_fps <= 0:
        raise JobRefused(f"{KIND} target source frame rate is unreadable")
    speed = _speed_percent(picture)
    if speed is None or not math.isclose(speed, 100.0, abs_tol=0.001):
        raise JobRefused(
            f"{KIND} target picture speed is {speed!r}%; the held-frame "
            "native hold requires a verified 100% source clip")
    source_start_frame = _read_int(
        picture.GetSourceStartFrame(), "target picture source start")
    record_frames = (_read_int(picture.GetEnd(), "target picture record end")
                     - _read_int(picture.GetStart(),
                                 "target picture record start"))
    played_source_frames = int(round(
        record_frames * source_fps / timeline_fps))
    if source_start_frame < 0 or played_source_frames <= 0:
        raise JobRefused(f"{KIND} target picture has an invalid played span")
    # GetSourceEndFrame has been measured one frame off on real clips.
    # The last frame rendered by a 100% item is its source start plus the
    # played source-frame count, minus one - the same quantisation used by
    # the builder's AppendToTimeline path.
    held_source_frame = source_start_frame + played_source_frames - 1
    held_seconds = held_source_frame / source_fps
    if not math.isfinite(held_seconds) or held_source_frame < 0:
        raise JobRefused(f"{KIND} target final source frame is unreadable")
    return path, source_fps, held_source_frame


def _copy_reference_comp(target_item, source_index: int,
                         source_path: Path, signature: dict,
                         directory: Path, on_added=None) -> dict:
    before = list(target_item.GetFusionCompNameList() or [])
    add_error = None
    try:
        added = target_item.AddFusionComp()
    except Exception as exc:  # Resolve can add a comp before surfacing error.
        added = False
        add_error = exc
    after = list(target_item.GetFusionCompNameList() or [])
    new_names = [name for name in after if name not in before]
    if add_error is not None or not added or len(new_names) != 1:
        if on_added is not None:
            for new_name in new_names:
                on_added(new_name)
        for new_name in reversed(new_names):
            if new_name not in list(target_item.GetFusionCompNameList() or []):
                continue
            removed = target_item.DeleteFusionCompByName(new_name)
            if (not removed
                    or new_name in list(
                        target_item.GetFusionCompNameList() or [])):
                raise JobRefused(
                    f"{KIND} could not remove unexpected comp "
                    f"{new_name!r} after AddFusionComp returned "
                    f"{added!r}") from add_error
        if add_error is not None:
            raise JobRefused(
                f"{KIND} AddFusionComp raised before a verified result: "
                f"{add_error}") from add_error
        raise JobRefused(
            f"{KIND} AddFusionComp returned {added!r}; expected one new "
            f"comp, found {new_names}")
    name = new_names[0]
    if on_added is not None:
        on_added(name)
    try:
        imported = target_item.ImportFusionComp(str(source_path))
        if not imported:
            raise JobRefused(
                f"{KIND} ImportFusionComp refused the TV-off comp")
        names_after = list(target_item.GetFusionCompNameList() or [])
        if name not in names_after:
            raise JobRefused(
                f"{KIND} imported TV-off comp {name!r} did not read back; "
                f"names are {names_after}")
        target_index = names_after.index(name) + 1
        target_path = directory / "target-tv-readback.comp"
        content = _export_comp(
            target_item, target_index, target_path, "target freeze")
        actual = _fusion_signature(content)
        if actual != signature:
            raise JobRefused(
                f"{KIND} TV-off splines differ after import: reference and "
                "target readbacks do not match")
        return {"source_comp_index": source_index,
                "target_comp_index": target_index,
                "target_comp_name": name,
                "signature": signature}
    except Exception as exc:
        if name in list(target_item.GetFusionCompNameList() or []):
            removed = target_item.DeleteFusionCompByName(name)
            if (not removed
                    or name in list(target_item.GetFusionCompNameList() or [])):
                raise JobRefused(
                    f"{KIND} failed to remove TV-off comp {name!r} after "
                    "an unsuccessful import or readback") from exc
        raise


def _run(job: dict, resolve, project) -> dict:
    params = job["params"]
    prepare(params)
    from library.tools import project_registry, resolve_lock, still_overlays
    from library.tools.reel_ending import FreezeTail
    from library.tools.versions.store import export_timeline_recovery_archive

    config = project_registry.get_project(PROJECT_SLUG)
    if (config.slug != PROJECT_SLUG
            or config.resolve.project_name != PROJECT_NAME):
        raise JobRefused(
            f"{KIND} project registry has {config.slug!r}/"
            f"{config.resolve.project_name!r}; expected "
            f"{PROJECT_SLUG!r}/{PROJECT_NAME!r}")
    project_folder = str(config.project_root)
    source = _exact_timeline(project, params["source_timeline"])
    target = _exact_timeline(
        project, params["target_timeline"], params.get("target_timeline_id"))
    if source is target:
        raise JobRefused(f"{KIND} source and target timelines must differ")
    record_frame = int(params["record_frame"])
    if params.get("target_picture_uid"):
        target_picture_row, picture = _item_by_uid(
            target, params["target_picture_uid"])
    else:
        target_picture_row, picture = _item_by_track_end(
            target, params["target_picture_track"], record_frame)
    if int(picture.GetEnd()) != record_frame:
        raise JobRefused(
            f"{KIND} target picture {picture.GetName()!r} ends at "
            f"{picture.GetEnd()}, not requested record frame {record_frame}")
    picture_path = _item_path(picture)
    if not picture_path or Path(picture_path).name.lower().startswith("logo_"):
        raise JobRefused(
            f"{KIND} target picture {picture.GetName()!r} does not identify "
            "a source picture clip")
    (_source_freeze_row, _source_freeze_name, source_freeze), \
        (_source_logo_row, source_logo_name, source_logo) = _reference_items(source)
    source_duration = _read_int(source_freeze.GetDuration(),
                                "reference freeze duration")
    logo_duration = _read_int(source_logo.GetDuration(),
                              "reference logo duration")
    if source_duration < 2 or logo_duration < 1:
        raise JobRefused(f"{KIND} reference ending has an invalid duration")
    logo_path = _item_path(source_logo)
    if not logo_path or not os.path.isfile(logo_path):
        raise JobRefused(
            f"{KIND} reference two-line logo is not readable: {logo_path!r}")
    reference_logo = _logo_capture(source_logo)
    if not reference_logo["enabled"]:
        raise JobRefused(f"{KIND} reference two-line logo is disabled")
    target_logo_row = _track_index(project, target, source_logo_name)
    existing_logo = _find_logo(target, target_logo_row, logo_path)
    old_logo = (_logo_capture(existing_logo)
                if existing_logo is not None else None)
    if old_logo is not None and old_logo["start"] not in (
            record_frame, record_frame + source_duration):
        raise JobRefused(
            f"{KIND} target logo starts at {old_logo['start']}; expected it "
            f"at the body cut {record_frame} or after the freeze at "
            f"{record_frame + source_duration}")
    logo_record_frame = record_frame + source_duration
    logo_already_after = (
        old_logo is not None and old_logo["start"] == logo_record_frame)
    source_logo_start = reference_logo["source_start"]
    if (logo_already_after
            and (old_logo["duration"] != logo_duration
                 or old_logo["source_start"] != source_logo_start)):
        raise JobRefused(
            f"{KIND} target two-line logo at {logo_record_frame} differs "
            "from the reference duration or source range")

    try:
        timeline_fps = float(target.GetSetting("timelineFrameRate") or 0)
    except (TypeError, ValueError):
        timeline_fps = 0
    if not math.isfinite(timeline_fps) or timeline_fps <= 0:
        raise JobRefused(f"{KIND} target timeline frame rate is unreadable")
    held_from, source_fps, held_source_frame = _source_frame_seconds(
        picture, timeline_fps)
    held_seconds = held_source_frame / source_fps
    header_plans = _plan_post_header(target, record_frame, timeline_fps)
    freeze = FreezeTail(
        held_from=held_from,
        held_source_seconds=held_seconds,
        source_fps=source_fps,
        held_source_frame=held_source_frame,
        reel_start_frame=record_frame,
        duration_frames=source_duration,
        element="tv_power_tail",
        track_index=target_picture_row,
    )

    comp_names = []

    def note_comp_added(name):
        nonlocal comp_added, comp_name
        comp_added = True
        comp_name = name
        if name not in comp_names:
            comp_names.append(name)

    archive = export_timeline_recovery_archive(
        resolve, target, target.GetName(), project_folder,
        label=f"{KIND} {job.get('id', '')}")
    if archive.get("archived") is not True:
        raise JobRefused(
            f"{KIND} .drt backup failed; no timeline write was made: "
            f"{archive.get('reason')}")

    purpose = f"{KIND} {target.GetName()}"
    from library.tools.resolved import cancellation

    def safe_checkpoint():
        """A Stop is honored inside the inverse-restore transaction."""
        cancellation.checkpoint(job.get("id", ""))

    with resolve_lock.cursor_excursion(project, target, purpose):
        before = still_overlays.inventory_snapshot(target)
        pool = project.GetMediaPool()
        target_freeze = None
        target_legacy_freeze = None
        legacy_freeze = None
        legacy_freeze_removed = False
        new_freeze = None
        freeze_added = False
        comp_added = False
        comp_name = None
        new_logo = None
        logo_added = False
        logo_moved = False
        orphaned_items = []
        header_applied = []
        comp_receipt = None
        try:
            safe_checkpoint()
            with tempfile.TemporaryDirectory(prefix="ren-tv-ending-") as tmp:
                temp_dir = Path(tmp)
                source_comp_index, source_comp_path, signature = (
                    _reference_tv_comp(source_freeze, temp_dir))
                target_freeze = _find_native_hold(
                    target, target_picture_row, held_from,
                    held_source_frame, record_frame, source_duration)
                target_legacy_freeze = _find_legacy_freeze(
                    target, target_picture_row, record_frame, source_duration)
                if target_freeze is not None and target_legacy_freeze is not None:
                    raise JobRefused(
                        f"{KIND} target has both a native hold and a legacy "
                        "freeze at the requested ending span")
                if target_legacy_freeze is not None:
                    legacy_freeze = {
                        "row": target_picture_row,
                        "capture": _legacy_freeze_capture(
                            target_legacy_freeze, temp_dir),
                    }
                    if legacy_freeze["capture"]["tv_signature"] != signature:
                        raise JobRefused(
                            f"{KIND} legacy freeze carries a different "
                            "TV-off comp; refusing to replace it")
                _assert_span_free(
                    target, target_picture_row, record_frame,
                    record_frame + source_duration,
                    allowed_uids=[str(item.GetUniqueId() or "") for item in
                                  (target_freeze, target_legacy_freeze)
                                  if item is not None],
                    label="native TV-off hold")
                _assert_span_free(
                    target, target_logo_row, logo_record_frame,
                    logo_record_frame + logo_duration,
                    allowed_uids=([old_logo["uid"]]
                                  if old_logo is not None else ()),
                    label="two-line logo")
                if target_freeze is not None and logo_already_after:
                    existing_comp = _target_tv_comp(target_freeze, temp_dir)
                    if (existing_comp is not None
                            and existing_comp[2] == signature
                            and not header_plans):
                        return {
                            "kind": KIND, "changed": False,
                            "already_applied": True,
                            "project": PROJECT_NAME,
                            "source_timeline": source.GetName(),
                            "target_timeline": target.GetName(),
                            "freeze": {
                                "name": target_freeze.GetName(),
                                "record": [record_frame,
                                           record_frame + source_duration]},
                            "logo": {
                                "name": existing_logo.GetName(),
                                "record": [logo_record_frame,
                                           logo_record_frame
                                           + old_logo["duration"]]},
                            "drt_backup": archive.get("path"),
                            "before": before,
                            "after": before,
                        }
                header_applied = _apply_post_header(
                    resolve, project, pool, target, timeline_fps,
                    header_plans, project_folder, header_applied)
                safe_checkpoint()
                if target_legacy_freeze is not None:
                    old_uid = str(target_legacy_freeze.GetUniqueId() or "")
                    delete_error = None
                    try:
                        removed = target.DeleteClips(
                            [target_legacy_freeze], False)
                    except Exception as exc:  # Resolve may mutate before error.
                        removed = False
                        delete_error = exc
                    remaining = {str(item.GetUniqueId() or "") for item in
                                 target.GetItemListInTrack(
                                     "video", target_picture_row) or ()}
                    legacy_freeze_removed = old_uid not in remaining
                    if (delete_error is not None or not removed
                            or not legacy_freeze_removed):
                        error = JobRefused(
                            f"{KIND} DeleteClips returned {removed!r} for "
                            f"the legacy freeze; it "
                            f"{'was removed before refusal' if legacy_freeze_removed else 'still reads'}")
                        if delete_error is not None:
                            raise error from delete_error
                        raise error
                if target_freeze is None:
                    source_pool_item = picture.GetMediaPoolItem()
                    source_frames = _source_frame_count(source_pool_item)
                    if freeze.source_end_frame > source_frames:
                        raise JobRefused(
                            f"{KIND} native hold needs source frames "
                            f"[{held_source_frame}, {freeze.source_end_frame}) "
                            f"but {picture_path!r} contains {source_frames}")
                    landed_row, new_freeze = _append_item(
                        pool, target, media_pool_item=source_pool_item,
                        row=target_picture_row, record_frame=record_frame,
                        source_start=held_source_frame,
                        duration=source_duration,
                        label="native hold",
                        orphaned_items=orphaned_items)
                    freeze_added = True
                    safe_checkpoint()
                    _verify_appended(
                        new_freeze, landed_row,
                        media_pool_item=source_pool_item,
                        row=target_picture_row, record_frame=record_frame,
                        duration=source_duration, label="native hold")
                    if int(new_freeze.GetSourceStartFrame()) != held_source_frame:
                        raise JobRefused(
                            f"{KIND} native hold starts at source frame "
                            f"{new_freeze.GetSourceStartFrame()}, expected "
                            f"{held_source_frame}")
                    if legacy_freeze is not None:
                        old_markers = legacy_freeze["capture"]["markers"]
                        _copy_clip_markers(old_markers, new_freeze)
                        if dict(new_freeze.GetMarkers() or {}) != old_markers:
                            raise JobRefused(
                                f"{KIND} native hold did not preserve the "
                                "legacy clip markers")
                    from library.tools import native_ops_apply
                    speed_report = native_ops_apply.apply_native_speed_ops(
                        target,
                        [{"op_id": f"tv-off-hold@{record_frame}",
                          "effect_type": "freeze_frame",
                          "timeline_start": record_frame / timeline_fps,
                          "timeline_end": (record_frame + source_duration)
                                          / timeline_fps}],
                        fps=timeline_fps, dialogue_tracks=None,
                        video_tracks=(target_picture_row,))
                    if speed_report["failed"]:
                        raise JobRefused(
                            f"{KIND} native hold was refused: "
                            + "; ".join(row["what"]
                                        for row in speed_report["failed"]))
                    from library.tools.reel_ending import is_native_hold
                    if not is_native_hold(new_freeze):
                        raise JobRefused(
                            f"{KIND} native hold did not read back at 0% speed")
                    if (int(new_freeze.GetStart()) != record_frame
                            or int(new_freeze.GetEnd())
                            != record_frame + source_duration
                            or int(new_freeze.GetSourceStartFrame())
                            != held_source_frame):
                        raise JobRefused(
                            f"{KIND} SetSpeed changed the native hold's "
                            "record span or held source frame")
                    wanted = dict(picture.GetProperty() or {})
                    for key in _POWER_TRANSFORM_KEYS:
                        if key not in wanted:
                            continue
                        _apply_properties(
                            new_freeze, {key: wanted[key]},
                            project_folder=project_folder,
                            timeline_name=target.GetName(),
                            identity={"source_file": held_from,
                                "track": f"V{target_picture_row}",
                                "record_frame": record_frame},
                        )
                        actual = (new_freeze.GetProperty() or {}).get(key)
                        if actual != wanted[key]:
                            raise JobRefused(
                                f"{KIND} native hold transform {key} reads "
                                f"{actual!r}, wanted {wanted[key]!r}")
                    copied_grade = picture.CopyGrades([new_freeze])
                    if (not copied_grade
                            or int(new_freeze.GetNumNodes() or 0)
                            != int(picture.GetNumNodes() or 0)):
                        raise JobRefused(
                            f"{KIND} could not copy and verify the target "
                            "picture grade onto its held frame")
                else:
                    new_freeze = target_freeze

                safe_checkpoint()
                existing_comp = _target_tv_comp(new_freeze, temp_dir)
                if existing_comp is None:
                    comp_receipt = _copy_reference_comp(
                        new_freeze, source_comp_index,
                        source_comp_path, signature, temp_dir,
                        on_added=note_comp_added)
                elif existing_comp[2] != signature:
                    raise JobRefused(
                        f"{KIND} target freeze carries a different TV-off "
                        "comp; refusing to replace it")
                else:
                    comp_receipt = {
                        "target_comp_index": existing_comp[0],
                        "target_comp_name": new_freeze.GetFusionCompNameList()[
                            existing_comp[0] - 1],
                        "signature": signature,
                        "already_present": True,
                    }

            if old_logo is None:
                safe_checkpoint()
                source_pool = source_logo.GetMediaPoolItem()
                if source_pool is None:
                    raise JobRefused(
                        f"{KIND} reference two-line logo has no media pool item")
                landed_row, new_logo = _append_item(
                    pool, target,
                    media_pool_item=source_pool, row=target_logo_row,
                    record_frame=logo_record_frame,
                    source_start=source_logo_start,
                    duration=logo_duration, label="reference logo",
                    orphaned_items=orphaned_items)
                logo_added = True
                _verify_appended(
                    new_logo, landed_row, media_pool_item=source_pool,
                    row=target_logo_row, record_frame=logo_record_frame,
                    duration=logo_duration, label="reference logo")
                _copy_captured_logo_state(
                    reference_logo, new_logo, project_folder=project_folder,
                    timeline_name=target.GetName(),
                    record_frame=logo_record_frame)
            elif old_logo["start"] == record_frame:
                delete_error = None
                try:
                    removed = target.DeleteClips([existing_logo], False)
                except Exception as exc:  # Resolve can mutate before error.
                    removed = False
                    delete_error = exc
                logo_moved = not any(
                    str(item.GetUniqueId() or "") == old_logo["uid"]
                    for item in target.GetItemListInTrack(
                        "video", target_logo_row) or ())
                if delete_error is not None or not removed or not logo_moved:
                    error = JobRefused(
                        f"{KIND} DeleteClips returned {removed!r}; the old "
                        f"logo {'still reads' if not logo_moved else 'was '
                        'removed but Resolve refused the result'} at the body cut")
                    if delete_error is not None:
                        raise error from delete_error
                    raise error
                landed_row, new_logo = _append_item(
                    pool, target,
                    media_pool_item=old_logo["pool_item"], row=target_logo_row,
                    record_frame=logo_record_frame,
                    source_start=old_logo["source_start"],
                    duration=old_logo["duration"], label="moved logo",
                    orphaned_items=orphaned_items)
                logo_added = True
                _verify_appended(
                    new_logo, landed_row,
                    media_pool_item=old_logo["pool_item"],
                    row=target_logo_row, record_frame=logo_record_frame,
                    duration=old_logo["duration"], label="moved logo")
                _copy_captured_logo_state(
                    old_logo, new_logo, project_folder=project_folder,
                    timeline_name=target.GetName(),
                    record_frame=logo_record_frame)
            else:
                new_logo = existing_logo

            after = still_overlays.inventory_snapshot(target)
            safe_checkpoint()
            remove_before = [str(swap["item_uid"])
                             for swap in header_plans]
            remove_after = [str(receipt["uid"])
                            for receipt in header_applied]
            if logo_moved:
                remove_before.append(old_logo["uid"])
            if legacy_freeze_removed and legacy_freeze is not None:
                remove_before.append(legacy_freeze["capture"]["uid"])
            if freeze_added:
                remove_after.append(str(new_freeze.GetUniqueId() or ""))
            if logo_added:
                remove_after.append(str(new_logo.GetUniqueId() or ""))
            expected_end = max(
                int(before["end"][1]), record_frame + source_duration,
                logo_record_frame + int(new_logo.GetDuration()))
            problems = _verify_inventory(
                before, after, remove_before=remove_before,
                remove_after=remove_after,
                freeze_uid=(str(new_freeze.GetUniqueId() or "")
                            if not freeze_added else None),
                freeze_comp_added=(comp_added and not freeze_added),
                expected_end=expected_end)
            if problems:
                raise JobRefused(
                    f"{KIND} after-inventory check failed: "
                    + "; ".join(problems))
        except Exception as exc:
            from library.tools.resolved.cancellation import CancelRequested

            restored = _restore_target(
                pool, target, freeze_item=new_freeze,
                freeze_added=freeze_added, comp_added=comp_added,
                comp_names=comp_names, logo_item=new_logo,
                logo_added=logo_added, old_logo=old_logo,
                logo_moved=logo_moved, old_freeze=legacy_freeze,
                old_freeze_removed=legacy_freeze_removed,
                orphaned_items=orphaned_items,
                logo_row=target_logo_row,
                header_plans=header_plans,
                header_applied=header_applied,
                resolve=resolve, project=project, fps=timeline_fps,
                project_folder=project_folder,
                timeline_name=target.GetName())
            try:
                after_restore = still_overlays.inventory_snapshot(target)
                restored["inventory_matches_before"] = _inventory_matches(
                    before, after_restore)
                if not restored["inventory_matches_before"]:
                    restored["errors"].append(
                        "inverse inventory differs from the pre-edit snapshot")
                    restored["restored"] = False
            except Exception as read_exc:  # noqa: BLE001
                after_restore = None
                restored["inventory_matches_before"] = False
                restored["errors"].append(
                    f"post-restore inventory read failed: {read_exc}")
                restored["restored"] = False
            if isinstance(exc, CancelRequested):
                if restored.get("restored") and restored.get(
                        "inventory_matches_before"):
                    raise
                raise JobRefused(
                    f"{KIND} cancellation could not prove inverse restore; "
                    f"drt_backup={archive.get('path')}; "
                    f"inverse_restore={restored}",
                    result={"drt_backup": archive.get("path"),
                            "before": before,
                            "after_restore": after_restore,
                            "inverse_restore": restored}) from exc
            raise JobRefused(
                f"{KIND} refused after mutation: {type(exc).__name__}: {exc}; "
                f"inverse_restore={restored}; "
                f"drt_backup={archive.get('path')}",
                result={"drt_backup": archive.get("path"),
                        "before": before,
                        "after_restore": after_restore,
                        "inverse_restore": restored}) from exc

        # `cursor_excursion` verifies the original timeline was restored.
        return {
            "kind": KIND,
            "changed": True,
            "project": PROJECT_NAME,
            "source_timeline": source.GetName(),
            "target_timeline": target.GetName(),
            "source_freeze": {
                "name": source_freeze.GetName(),
                "record": [int(source_freeze.GetStart()),
                           int(source_freeze.GetEnd())],
                "comp_index": source_comp_index,
            },
            "freeze": {
                "name": new_freeze.GetName(),
                "uid": str(new_freeze.GetUniqueId() or ""),
                "record": [record_frame, record_frame + source_duration],
                "source_file": held_from,
                "held_from": held_from,
                "held_source_seconds": held_seconds,
                "held_source_frame": held_source_frame,
                "speed_percentage": 0.0,
                "comp": comp_receipt,
            },
            "replaced_legacy_freeze": legacy_freeze_removed,
            "post_header": [
                {"name": swap["label"], "record": receipt["record"],
                 "source_file": receipt["still_path"]}
                for swap, receipt in zip(header_plans, header_applied)
            ],
            "logo": {
                "name": new_logo.GetName(),
                "uid": str(new_logo.GetUniqueId() or ""),
                "record": [logo_record_frame,
                           logo_record_frame + int(new_logo.GetDuration())],
                "source_file": _item_path(new_logo),
                "moved_existing": bool(logo_moved),
            },
            "drt_backup": archive.get("path"),
            "before": before,
            "after": after,
            "inverse_restore_on_unexplained_difference": True,
        }


def run(job: dict, resolve) -> dict:
    """Execute one target reel edit on the broker's Resolve connection."""
    from library.tools.resolve_deadline import DeadlineProxy, deadline_proxy
    from library.tools.transform_write_log import write_scope
    from library.tools.resolved.jobs import _open_project

    if not isinstance(resolve, DeadlineProxy):
        resolve = deadline_proxy(
            resolve, label=f"{KIND} {job.get('id', '')}")
    params = job["params"]
    prepare(params)
    project = _open_project(resolve, PROJECT_NAME)
    with write_scope(project=PROJECT_NAME,
                     timeline_name=params["target_timeline"],
                     run_id=job.get("id")):
        return _run(job, resolve, project)
