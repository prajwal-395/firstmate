"""READ-ONLY capture of a live Resolve timeline. Writes nothing to Resolve.

A formatter over the one reader, `library.tools.reel_read`: the clips
are enumerated ONCE there (`read_reel`, quick mode) and this script
projects that reading into the long-standing capture envelope
(`timeline_settings` + `tracks` + `track_counts`), which
`project_data_guard` recognises. Do not add a fresh item loop here -
take a slice of the reader.

Run as:

    RESOLVE_SCRIPT_API=... RESOLVE_SCRIPT_LIB=... \
      python3 -m library.tools.capture_timeline "<timeline name>" out.json
"""
import json
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if os.environ.get("REPO"):
    sys.path.insert(0, os.environ["REPO"])
elif str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from library.tools.resolve_locale import scriptapp_preserving_locale
from library.tools import reel_read

TARGET = sys.argv[1]
OUT = sys.argv[2]

import DaVinciResolveScript as dvr

r = scriptapp_preserving_locale(dvr, "Resolve")
pm = r.GetProjectManager()
p = pm.GetCurrentProject()

tl = None
for i in range(1, p.GetTimelineCount() + 1):
    t = p.GetTimelineByIndex(i)
    if t.GetName() == TARGET:
        tl = t
        break
if tl is None:
    raise SystemExit("timeline not found: %r" % TARGET)


def safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:
        return "<error: %s>" % e


# The one read. Everything below is a projection of `result`, except the
# envelope extras Resolve reports about itself (product/version,
# settings, subtitle rows) which are not clips or markers. The project
# handle is the currency proof: when the target is not the current
# timeline this REFUSES rather than archiving scaled Pan/Tilt
# (`reel_read.assert_timeline_current`) - open the reel first, then
# capture.
result = reel_read.read_reel(tl, p.GetName(), mode=reel_read.QUICK,
                             resolve_project=p)
by_track = {(t["type"], t["index"]): t for t in result["tracks"]}

PROPS = [
    "Pan", "Tilt", "ZoomX", "ZoomY", "ZoomGang", "RotationAngle",
    "AnchorPointX", "AnchorPointY", "Pitch", "Yaw",
    "FlipX", "FlipY", "CropLeft", "CropRight", "CropTop", "CropBottom",
    "CropSoftness", "CropRetain", "DynamicZoomEase", "CompositeMode",
    "Opacity", "Distance", "Blend", "MotionEstType", "MotionEstimation",
    "Scaling", "ResizeFilter",
]

doc = {
    "resolve_product": safe(r.GetProductName),
    "resolve_version": safe(r.GetVersionString),
    "project": p.GetName(),
    "timeline": tl.GetName(),
    "timeline_settings": {
        k: safe(tl.GetSetting, k)
        for k in ("timelineResolutionWidth", "timelineResolutionHeight",
                  "timelineFrameRate", "timelineOutputResolutionWidth",
                  "timelineOutputResolutionHeight", "timelinePlaybackFrameRate")
    },
    "start_frame": safe(tl.GetStartFrame),
    "end_frame": safe(tl.GetEndFrame),
    "start_timecode": safe(tl.GetStartTimecode),
    "timeline_markers": safe(tl.GetMarkers),
    "track_counts": {tt: safe(tl.GetTrackCount, tt) for tt in ("video", "audio", "subtitle")},
    "tracks": [],
}

for tt in ("video", "audio", "subtitle"):
    n = doc["track_counts"][tt]
    if not isinstance(n, int):
        continue
    for idx in range(1, n + 1):
        track = {
            "type": tt,
            "index": idx,
            "name": safe(tl.GetTrackName, tt, idx),
            "enabled": safe(tl.GetIsTrackEnabled, tt, idx),
            "locked": safe(tl.GetIsTrackLocked, tt, idx),
            "items": [],
        }
        known = by_track.get((tt, idx))
        if known is not None:
            for clip in known["clips"]:
                props = dict(clip["transform"])
                rec = {
                    "name": clip["name"],
                    "unique_id": clip["unique_id"],
                    "record_start_frame": clip["record_in"],
                    "record_end_frame": clip["record_out"],
                    "duration_frames": clip["duration"],
                    "source_left_offset": clip["left_offset"],
                    "source_right_offset": clip["right_offset"],
                    "properties": {pk: props.get(pk) for pk in PROPS},
                    "properties_all": props,
                    "markers": {
                        m["frame"]: {
                            "color": m["color"], "name": m["name"],
                            "note": m["note"], "duration": m["duration"],
                            "customData": m["custom_data"],
                        }
                        for m in clip["markers"]
                    },
                    "flags": list(clip.get("flags") or []),
                    "color": clip["clip_color"],
                    "fusion_comp_count": clip["fusion"]["comp_count"],
                    "fusion_comp_names": clip["fusion"]["comp_names"],
                    "media_pool_item": {
                        "name": clip["name"],
                        "file_path": clip["source_file"],
                        "unique_id": clip["media_pool_item_id"],
                    },
                }
                track["items"].append(rec)
        else:
            # Subtitle rows: not clips, not markers - outside the one
            # reader's scope, read live as before.
            items = safe(tl.GetItemListInTrack, tt, idx) or []
            if isinstance(items, str):
                items = []
            for it in items:
                track["items"].append({
                    "name": safe(it.GetName),
                    "record_start_frame": safe(it.GetStart),
                    "record_end_frame": safe(it.GetEnd),
                })
        doc["tracks"].append(track)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2, sort_keys=True, default=str)
print("wrote", OUT)
tot = sum(len(t["items"]) for t in doc["tracks"])
print("tracks:", len(doc["tracks"]), "items:", tot, "timeline markers:", len(doc["timeline_markers"] or {}))
