"""Place one Resolve still at an exact frame duration.

Resolve's AppendToTimeline API does not accept a duration for image
items. The duration is a user preference, so a still placement saves
the complete current preference preset, loads a temporary preset with
the requested frame count, appends and reads the still back, then
restores and verifies the full preference snapshot in ``finally``.

The one-item delete and re-place route used by reel touchups calls the
same function. A failed preference restore or mismatched timeline read
refuses the operation by name.
"""

from __future__ import annotations

import os
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4

from library.tools.resolve_lock import assert_current_timeline


class StillPlacementRefused(RuntimeError):
    """Resolve could not place or safely restore an exact-duration still."""


def _normalized_preset(path: str) -> str:
    """Serialize a preference snapshot without generated ids or its name."""
    root = ET.parse(path).getroot()
    root.attrib.pop("DbId", None)
    name = root.find("Name")
    if name is not None:
        name.text = ""
    for node in root.iter():
        node.attrib.pop("DbId", None)
    return ET.tostring(root, encoding="unicode")


def _duration_preset(snapshot_path: str, preset_path: str,
                     preset_name: str, duration_frames: int,
                     fps: float) -> None:
    tree = ET.parse(snapshot_path)
    root = tree.getroot()
    name = root.find("Name")
    duration_type = root.find("StillDurationType")
    duration_secs = root.find("StillDurationSecs")
    duration_count = root.find("StillDurationFrames")
    if any(node is None for node in
           (name, duration_type, duration_secs, duration_count)):
        raise StillPlacementRefused(
            "Resolve's exported preference preset has no still-duration fields")
    name.text = preset_name
    duration_type.text = "DURATION_IN_FRAMES"
    duration_secs.text = format(duration_frames / fps, ".12g")
    duration_count.text = str(duration_frames)
    tree.write(preset_path, encoding="UTF-8", xml_declaration=True)


def place_still_exact(resolve, project, pool, timeline, media_pool_item,
                      duration_frames: int, record_frame: int,
                      track_index: int, fps: float, *,
                      timeline_name: str = "") -> dict:
    """Append a native still and verify its requested timeline duration.

    The Resolve item is read back from the named timeline row. The
    returned append handle is also judged, but it cannot stand in for
    the row read: Resolve has returned truthy handles for clips it did
    not place.
    """
    if (not isinstance(duration_frames, int)
            or isinstance(duration_frames, bool)
            or duration_frames < 1):
        raise ValueError("still duration must be a positive integer frame count")
    if not isinstance(record_frame, int) or isinstance(record_frame, bool):
        raise ValueError("still record frame must be an integer")
    if not isinstance(track_index, int) or track_index < 1:
        raise ValueError("still video track index must be positive")
    if float(fps) <= 0:
        raise ValueError("still placement needs a positive timeline frame rate")

    media_path = media_pool_item.GetClipProperty("File Path")
    media_kind = media_pool_item.GetClipProperty("Type")
    if media_kind != "Still" or not str(media_path).lower().endswith(".png"):
        raise StillPlacementRefused(
            f"{timeline_name}: exact still placement expected a native PNG, "
            f"Resolve read type={media_kind!r}, path={media_path!r}")

    initial_preset_names = list(resolve.GetUserPreferencesPresetList() or [])
    token = uuid4().hex
    snapshot_name = f"ren_still_restore_{token}"
    duration_name = f"ren_still_{duration_frames}f_{token}"
    readback_name = f"ren_still_readback_{token}"
    operation_error = None
    restore_errors = []
    landed = None
    operation_result = None
    snapshot_saved = False

    with tempfile.TemporaryDirectory(prefix="ren_still_duration_") as folder:
        snapshot_path = os.path.join(folder, "snapshot.preset")
        duration_path = os.path.join(folder, "duration.preset")
        readback_path = os.path.join(folder, "readback.preset")
        try:
            assert_current_timeline(project, timeline)
            snapshot_saved = bool(resolve.SaveUserPreferencesPreset(
                snapshot_name))
            if not snapshot_saved:
                raise StillPlacementRefused(
                    f"{timeline_name}: Resolve refused to save the current "
                    "user-preference snapshot")
            if not resolve.ExportUserPreferencesPreset(
                    snapshot_name, snapshot_path):
                raise StillPlacementRefused(
                    f"{timeline_name}: Resolve refused to export the current "
                    "user-preference snapshot")

            _duration_preset(snapshot_path, duration_path, duration_name,
                             duration_frames, float(fps))
            if not resolve.ImportUserPreferencesPreset(
                    duration_path, duration_name):
                raise StillPlacementRefused(
                    f"{timeline_name}: Resolve refused the {duration_frames}-frame "
                    "still-duration preset")
            if not resolve.LoadUserPreferencesPreset(duration_name):
                raise StillPlacementRefused(
                    f"{timeline_name}: Resolve refused to load the "
                    f"{duration_frames}-frame still-duration preset")

            assert_current_timeline(project, timeline)
            returned = pool.AppendToTimeline([{
                "mediaPoolItem": media_pool_item,
                "mediaType": 1,
                "trackIndex": track_index,
                "recordFrame": record_frame,
            }])
            if not returned or len(returned) != 1:
                raise StillPlacementRefused(
                    f"{timeline_name}: AppendToTimeline did not return one "
                    f"still at frame {record_frame}")
            source_uid = media_pool_item.GetUniqueId()
            candidates = timeline.GetItemListInTrack(
                "video", track_index) or []
            for candidate in candidates:
                candidate_pool_item = candidate.GetMediaPoolItem()
                if (candidate.GetStart() == record_frame
                        and candidate_pool_item
                        and candidate_pool_item.GetUniqueId() == source_uid):
                    landed = candidate
                    break
            if landed is None:
                raise StillPlacementRefused(
                    f"{timeline_name}: Resolve returned an append handle but "
                    f"the still is absent from V{track_index} at frame "
                    f"{record_frame}")
            actual_duration = float(landed.GetDuration())
            if (abs(actual_duration - duration_frames) > 1e-6):
                raise StillPlacementRefused(
                    f"{timeline_name}: still at frame {record_frame} reads "
                    f"{actual_duration:g} frames; requested "
                    f"{duration_frames}. The user-preference route did not "
                    "place the exact duration")
            if landed.GetMediaPoolItem().GetClipProperty("Type") != "Still":
                raise StillPlacementRefused(
                    f"{timeline_name}: frame {record_frame} did not remain "
                    "a native Resolve still")
            assert_current_timeline(project, timeline)
            operation_result = {
                "item": landed,
                "start_frame": int(round(landed.GetStart())),
                "duration_frames": int(round(actual_duration)),
                "media_path": media_path,
                "media_type": media_kind,
            }
        except Exception as exc:  # restoration below runs on every path
            operation_error = exc
        finally:
            if snapshot_saved:
                try:
                    if not resolve.LoadUserPreferencesPreset(snapshot_name):
                        restore_errors.append(
                            "Resolve refused to load the saved preference snapshot")
                    elif not resolve.SaveUserPreferencesPreset(readback_name):
                        restore_errors.append(
                            "Resolve refused to save the restored preference readback")
                    elif not resolve.ExportUserPreferencesPreset(
                            readback_name, readback_path):
                        restore_errors.append(
                            "Resolve refused to export the restored preference readback")
                    elif (_normalized_preset(snapshot_path)
                          != _normalized_preset(readback_path)):
                        restore_errors.append(
                            "restored Resolve user preferences differ from the saved snapshot")
                except Exception as exc:  # noqa: BLE001
                    restore_errors.append(
                        f"preference restoration/readback raised "
                        f"{type(exc).__name__}: {exc}")

            try:
                names_to_delete = [snapshot_name, duration_name, readback_name]
                present = set(resolve.GetUserPreferencesPresetList() or [])
                for name in names_to_delete:
                    if name in present and not resolve.DeleteUserPreferencesPreset(name):
                        restore_errors.append(
                            f"Resolve refused to delete temporary preset {name!r}")
                if list(resolve.GetUserPreferencesPresetList() or []) != initial_preset_names:
                    restore_errors.append(
                        "the Resolve user-preference preset list did not return to its initial state")
            except Exception as exc:  # noqa: BLE001
                restore_errors.append(
                    f"temporary preset cleanup raised {type(exc).__name__}: {exc}")

    if restore_errors:
        detail = "; ".join(restore_errors)
        if operation_error is not None:
            detail = f"{operation_error}; additionally, {detail}"
        raise StillPlacementRefused(
            f"{timeline_name}: could not safely restore Resolve user "
            f"preferences after placing a still: {detail}") from operation_error
    if operation_error is not None:
        if isinstance(operation_error, StillPlacementRefused):
            raise operation_error
        raise StillPlacementRefused(
            f"{timeline_name}: still placement failed: "
            f"{type(operation_error).__name__}: {operation_error}") from operation_error
    return operation_result
