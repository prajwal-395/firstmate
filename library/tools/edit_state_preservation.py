"""Shared state contract for edits that replace a timeline item.

Resolve assigns a new native item identity when a clip is re-placed. This
module captures the editorial state that can be carried to that new item,
restores it, and verifies the read-back. Callers must capture before deleting
the original and must refuse when this contract cannot represent its state.
"""

from __future__ import annotations

from collections.abc import Mapping

PICTURE_PROPERTIES = (
    "Pan", "Tilt", "ZoomX", "ZoomY", "ZoomGang", "RotationAngle",
    "AnchorPointX", "AnchorPointY", "CropLeft", "CropRight", "CropTop",
    "CropBottom", "Opacity",
)
READ_ONLY_PROPERTIES = frozenset({"Frames", "FPS", "Resolution"})


def _grade_node_count(item):
    """Read the grade count from Resolve's color node graph surface."""
    graph = item.GetNodeGraph()
    if graph is None:
        raise ValueError("the item's color node graph is unreadable")
    return graph.GetNumNodes()


class StatePreservationError(ValueError):
    """The requested replacement cannot safely carry the item's state."""


def preflight_source(state: Mapping, pool_item) -> None:
    """Refuse source-specific marker carry before the original is removed."""
    pool_uid = str(pool_item.GetUniqueId() or "") if pool_item else ""
    if state["markers"] and pool_uid != state["pool_uid"]:
        raise StatePreservationError(
            "clip markers belong to a different source and cannot be "
            "carried to the replacement")


def capture_clip_metadata(item) -> dict:
    """Capture enabled, color, flags, markers and the source identity."""
    try:
        flags = list(item.GetFlagList() or [])
        enabled = item.GetClipEnabled()
        color = item.GetClipColor()
        markers = dict(item.GetMarkers() or {})
        pool_item = item.GetMediaPoolItem()
        pool_uid = str(pool_item.GetUniqueId() or "") if pool_item else ""
        media_id = str(_pool_media_id(pool_item) or "")
        source_file = (str(pool_item.GetClipProperty("File Path") or "")
                       if pool_item else "")
        native_id = str(item.GetUniqueId() or "")
        linked = list(item.GetLinkedItems() or [])
        track_type, track_index = item.GetTrackTypeAndIndex()
        source_span = (item.GetSourceStartFrame(), item.GetSourceEndFrame())
        record_span = (item.GetStart(), item.GetEnd())
    except Exception as exc:
        raise StatePreservationError(
            f"the item's clip metadata could not be read ({exc})") from exc
    if not isinstance(enabled, bool):
        raise StatePreservationError("the item's enabled state is unreadable")
    if flags and not all(callable(getattr(item, name, None))
                         for name in ("AddFlag", "ClearFlags")):
        raise StatePreservationError(
            "the item's flags cannot be restored by this replacement path")
    if markers and not callable(getattr(item, "AddMarker", None)):
        raise StatePreservationError(
            "the item's clip markers cannot be restored by this path")
    identity = _editorial_identity(
        source_file=source_file, media_id=media_id,
        track_type=str(track_type), track_index=int(track_index),
        source_span=source_span, record_span=record_span)
    return {"enabled": enabled, "clip_color": color or "", "flags": flags,
            "markers": _normalize_markers(markers), "pool_uid": pool_uid,
            "native_object_id": native_id,
            "editorial_identity": {
                "source_file": source_file,
                "track_type": str(track_type),
                "track_index": int(track_index),
                "source_span": tuple(int(value) for value in source_span),
                "record_span": tuple(int(value) for value in record_span),
                # The DURABLE identity of the editorial moment this item
                # is: what it plays and the role it plays it in, and
                # NOT where it sits or what Resolve calls it.  A
                # replacement carries it, so the new item stands in for
                # the same moment (PRES-006).
                **({"token": identity.token,
                    "passage_id": identity.passage_id,
                    "material_id": identity.material_id,
                    "role": identity.role}
                   if identity is not None else {}),
            },
            "linked_item_ids": sorted({
                str(partner.GetUniqueId()) for partner in linked
                if partner is not None and partner.GetUniqueId()
                and str(partner.GetUniqueId()) != native_id})}


def _pool_media_id(pool_item) -> str:
    """The media pool item's MEDIA id, or its unique id.

    `reel_read` records the media id (`GetMediaId`) and the replace
    guard builds its source identity from it, so the editorial identity
    has to read the same one - two spellings of "which media" would be
    two materials.
    """
    if pool_item is None:
        return ""
    getter = getattr(pool_item, "GetMediaId", None)
    if callable(getter):
        try:
            media_id = getter()
        except Exception:  # noqa: BLE001 - Resolve proxy errors vary.
            media_id = None
        if media_id:
            return str(media_id)
    try:
        return str(pool_item.GetUniqueId() or "")
    except Exception:  # noqa: BLE001 - Resolve proxy errors vary.
        return ""


def _editorial_identity(*, source_file: str, media_id: str, track_type: str,
                        track_index: int, source_span, record_span):
    """The durable editorial identity of a timeline item, or None.

    `library.tools.editorial_identity` owns the identity; this is the
    replacement path's use of it.  An item with no media and no path
    names no editorial moment, and that is reported as None rather than
    as an identity over nothing.
    """
    from library.tools.editorial_identity import identity_of

    return identity_of({
        "source_file": source_file,
        "media_pool_item_id": media_id,
        "unique_id": "",
        "track_type": track_type,
        "track_index": track_index,
        "track_name": "",
        "name": "",
        "source_in_frame": int(source_span[0]),
        "source_out_frame": int(source_span[1]),
    })


def capture(item, *, picture: bool = True) -> dict:
    """Return the canonical, supported state of a timeline item.

    Fusion compositions currently have no qualified copy path in this
    replacement executor. Refuse them before any timeline mutation instead
    of silently dropping them.
    """
    try:
        properties = item.GetProperty()
        comp_count = int(item.GetFusionCompCount() or 0)
        node_count = _grade_node_count(item) if picture else 0
        native_object_id = str(item.GetUniqueId() or "")
    except Exception as exc:
        raise StatePreservationError(
            f"the item's preservation state could not be read ({exc})") from exc
    if not isinstance(properties, dict):
        raise StatePreservationError(
            "the item's approved picture properties could not be read")
    if comp_count:
        raise StatePreservationError(
            f"the item carries Fusion compositions ({comp_count}) that "
            "this replacement path cannot carry - a re-place gives the "
            "item a new identity, and a comp is keyed to the item it "
            "was written for. On a built reel, use the touch-up path "
            "(`ren touch` with a retime), which re-derives the "
            "compositions from the recorded manifest instead of "
            "carrying them across; or narrow a clip that carries no "
            "treatment.")
    metadata = capture_clip_metadata(item)
    keyframes = {}
    keyframe_reader = getattr(item, "GetKeyframes", None) if picture else None
    if callable(keyframe_reader):
        try:
            keyframes = {
                key: list(keyframe_reader(key) or [])
                for key in PICTURE_PROPERTIES
            }
        except Exception as exc:
            raise StatePreservationError(
                f"the item's animated properties could not be read ({exc})") \
                from exc
    if any(keyframes.values()):
        raise StatePreservationError(
            "the item has animated picture properties that this replacement "
            "path cannot carry")
    copy_grades = getattr(item, "CopyGrades", None) if picture else None
    if picture and (node_count is None or not callable(copy_grades)):
        raise StatePreservationError(
            "the item's grade cannot be captured and restored")
    return {
        "native_object_id": native_object_id,
        **metadata,
        "picture_properties": ({
            key: properties[key] for key in PICTURE_PROPERTIES
            if key in properties
        } if picture else {}),
        "grade_node_count": int(node_count or 0),
        "grade_source": item if picture else None,
    }


def restore(item, state: Mapping, *, source_in: int, source_out: int,
            timeline=None, write_context=None) -> dict:
    """Carry captured state to ``item`` and verify every supported field."""
    mismatches = {}
    try:
        pool_item = item.GetMediaPoolItem()
        preflight_source(state, pool_item)
    except Exception as exc:  # noqa: BLE001 - Resolve proxy errors vary.
        return {"markers": str(exc)}
    mismatches.update(write_properties(
        item, state["picture_properties"], write_context=write_context,
        item_identity={"uid": str(item.GetUniqueId() or ""),
                       "name": str(item.GetName() or "")},
        require_reported=True))

    mismatches.update(restore_clip_metadata(
        item, state, source_in=source_in, source_out=source_out,
        timeline=timeline))

    grade_source = state["grade_source"]
    if grade_source is not None:
        try:
            grade_source.CopyGrades([item])
            nodes_actual = _grade_node_count(item)
            if nodes_actual != state["grade_node_count"]:
                mismatches["grade_node_count"] = [
                    state["grade_node_count"], nodes_actual]
        except Exception as exc:  # noqa: BLE001 - Resolve proxy errors vary.
            mismatches["grade_node_count"] = [
                state["grade_node_count"], f"copy/read failed: {exc}"]
    return mismatches


def restore_clip_metadata(item, state: Mapping, *, source_in=None,
                          source_out=None, timeline=None) -> dict:
    """Restore enabled, color, flags, and retained source markers."""
    mismatches = {}
    if state.get("enabled") is not None:
        try:
            wanted = state["enabled"]
            result = item.SetClipEnabled(bool(wanted))
            actual = item.GetClipEnabled()
            if result is False or actual is not wanted:
                mismatches["enabled"] = [wanted, actual]
        except Exception as exc:  # noqa: BLE001 - Resolve proxy errors vary.
            mismatches["enabled"] = [state["enabled"],
                                     f"write failed: {exc}"]
    if "clip_color" in state:
        try:
            wanted = state["clip_color"]
            result = (item.SetClipColor(wanted) if wanted else
                      item.ClearClipColor())
            actual = item.GetClipColor()
            if result is False or (actual or "") != (wanted or ""):
                mismatches["clip_color"] = [wanted, actual]
        except Exception as exc:  # noqa: BLE001 - Resolve proxy errors vary.
            mismatches["clip_color"] = [state["clip_color"],
                                         f"write failed: {exc}"]
    if "flags" in state:
        try:
            wanted_flags = state["flags"]
            actual_flags = list(item.GetFlagList() or [])
            if actual_flags and callable(getattr(item, "ClearFlags", None)):
                item.ClearFlags("All")
            if wanted_flags:
                for flag in wanted_flags:
                    if not item.AddFlag(flag):
                        mismatches["flags"] = [wanted_flags,
                                                "AddFlag returned false"]
                        break
            actual_flags = sorted(item.GetFlagList() or [])
            if actual_flags != sorted(wanted_flags):
                mismatches["flags"] = [wanted_flags, actual_flags]
        except Exception as exc:  # noqa: BLE001 - Resolve proxy errors vary.
            mismatches["flags"] = [state["flags"],
                                   f"read/write failed: {exc}"]
    if "markers" in state:
        try:
            pool_item = item.GetMediaPoolItem()
            actual_pool_uid = (str(pool_item.GetUniqueId() or "")
                               if pool_item else "")
            wanted_pool_uid = state.get("pool_uid", "")
            if state["markers"] and wanted_pool_uid != actual_pool_uid:
                return {**mismatches, "markers": [wanted_pool_uid,
                                                    actual_pool_uid]}
            expected = {
                frame: marker for frame, marker in state["markers"].items()
                if (source_in is None or source_out is None
                    or int(source_in) <= frame <= int(source_out))
            }
            for frame, marker in expected.items():
                current = _normalize_markers(item.GetMarkers() or {})
                if current.get(frame) == marker:
                    continue
                if not item.AddMarker(
                        frame, marker["color"], marker["name"],
                        marker["note"], marker["duration"],
                        marker["customData"]):
                    mismatches.setdefault("markers", {})[frame] = (
                        "AddMarker returned false")
            actual = _normalize_markers(item.GetMarkers() or {})
            if source_in is not None and source_out is not None:
                actual = {frame: marker for frame, marker in actual.items()
                          if int(source_in) <= frame <= int(source_out)}
            if actual != expected:
                mismatches["markers"] = [expected, actual]
        except Exception as exc:  # noqa: BLE001 - Resolve proxy errors vary.
            mismatches["markers"] = [state["markers"],
                                      f"read/write failed: {exc}"]
    linked_item_ids = set(state.get("linked_item_ids") or ())
    if linked_item_ids:
        if timeline is None:
            return {**mismatches, "linked_items":
                    "linked partners cannot be restored without a timeline"}
        try:
            partners = []
            for track_type in ("video", "audio"):
                for track_index in range(
                        1, int(timeline.GetTrackCount(track_type) or 0) + 1):
                    for candidate in timeline.GetItemListInTrack(
                            track_type, track_index) or []:
                        if str(candidate.GetUniqueId() or "") in linked_item_ids:
                            partners.append(candidate)
            found = {str(partner.GetUniqueId() or "") for partner in partners}
            if found != linked_item_ids:
                mismatches["linked_items"] = [sorted(linked_item_ids),
                                               sorted(found)]
            elif timeline.SetClipsLinked([item, *partners], True) is False:
                mismatches["linked_items"] = [sorted(linked_item_ids),
                                               "SetClipsLinked returned false"]
            else:
                actual = {str(partner.GetUniqueId() or "")
                          for partner in (item.GetLinkedItems() or [])
                          if str(partner.GetUniqueId() or "")
                          != str(item.GetUniqueId() or "")}
                if actual != linked_item_ids:
                    mismatches["linked_items"] = [
                        sorted(linked_item_ids), sorted(actual)]
        except Exception as exc:  # noqa: BLE001 - Resolve proxy errors vary.
            mismatches["linked_items"] = [sorted(linked_item_ids),
                                           f"read/write failed: {exc}"]
    return mismatches


def _same(wanted, actual) -> bool:
    """The transform-write rule for two values: the same one the
    placement pass asks before it skips a write."""
    from library.tools.transform_write_log import values_agree

    return values_agree(wanted, actual)


def write_properties(item, properties: Mapping, *, write_context=None,
                     item_identity=None, old_properties=None,
                     require_reported=False) -> dict:
    """Write a declared property map and verify it from Resolve read-back."""
    from library.tools.transform_write_log import set_property

    written = {}
    for key, wanted in properties.items():
        if (key in READ_ONLY_PROPERTIES or wanted is None
                or (isinstance(wanted, str) and wanted.startswith("<"))):
            continue
        args = dict(write_context or {})
        if old_properties is not None and key in old_properties:
            args["old_value"] = old_properties[key]
        try:
            set_property(item, key, wanted,
                         item_identity=item_identity, **args)
        except Exception as exc:  # noqa: BLE001 - Resolve proxy errors vary.
            written[key] = [wanted, f"write failed: {exc}"]
            continue
        written[key] = wanted
    try:
        actual_properties = item.GetProperty()
    except Exception as exc:  # noqa: BLE001 - Resolve proxy errors vary.
        return {key: [wanted, f"read failed: {exc}"]
                for key, wanted in written.items()}
    if not isinstance(actual_properties, dict):
        return {} if not require_reported else {
            key: [wanted, "property dictionary is unreadable"]
            for key, wanted in written.items()}
    mismatches = {}
    for key, wanted in written.items():
        if isinstance(wanted, list):
            mismatches[key] = wanted
        elif key not in actual_properties:
            if require_reported:
                mismatches[key] = [wanted, "property was not reported"]
        elif not _same(wanted, actual_properties[key]):
            mismatches[key] = [wanted, actual_properties[key]]
    return mismatches


def _normalize_markers(markers: Mapping) -> dict:
    normalized = {}
    for raw_frame, raw_marker in markers.items():
        frame = int(raw_frame)
        marker = dict(raw_marker)
        normalized[frame] = {
            "color": str(marker.get("color") or "Blue"),
            "name": str(marker.get("name") or ""),
            "note": str(marker.get("note") or ""),
            "duration": int(marker.get("duration") or 1),
            "customData": str(marker.get("customData") or ""),
        }
    return normalized
