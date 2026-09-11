"""READ-ONLY capture of a live Resolve timeline. Writes nothing to Resolve.

Kept in the pipeline (moved out of a per-project captures/ drop zone):
dumping a timeline's full item state is reusable diagnosis tooling for any
Resolve timeline, and this script takes the timeline name and output path
as arguments with no project constants baked in.

Run as:

    RESOLVE_SCRIPT_API=... RESOLVE_SCRIPT_LIB=... \
      PYTHONPATH="$RESOLVE_SCRIPT_API/Modules" \
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
import DaVinciResolveScript as dvr
from library.tools.resolve_locale import scriptapp_preserving_locale

TARGET = sys.argv[1]
OUT = sys.argv[2]

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
        items = safe(tl.GetItemListInTrack, tt, idx) or []
        if isinstance(items, str):
            items = []
        for it in items:
            mpi = safe(it.GetMediaPoolItem)
            rec = {
                "name": safe(it.GetName),
                "unique_id": safe(it.GetUniqueId),
                "record_start_frame": safe(it.GetStart),
                "record_end_frame": safe(it.GetEnd),
                "duration_frames": safe(it.GetDuration),
                "source_left_offset": safe(it.GetLeftOffset),
                "source_right_offset": safe(it.GetRightOffset),
                "properties": {},
                "markers": safe(it.GetMarkers),
                "flags": safe(it.GetFlagList),
                "color": safe(it.GetClipColor),
                "fusion_comp_count": safe(it.GetFusionCompCount),
                "fusion_comp_names": safe(it.GetFusionCompNameList),
                "media_pool_item": None,
            }
            allprops = safe(it.GetProperty)
            if isinstance(allprops, dict):
                rec["properties_all"] = {k: v for k, v in allprops.items()}
            for pk in PROPS:
                rec["properties"][pk] = safe(it.GetProperty, pk)
            if mpi and not isinstance(mpi, str):
                rec["media_pool_item"] = {
                    "name": safe(mpi.GetName),
                    "file_path": safe(mpi.GetClipProperty, "File Path"),
                    "resolution": safe(mpi.GetClipProperty, "Resolution"),
                    "fps": safe(mpi.GetClipProperty, "FPS"),
                    "duration": safe(mpi.GetClipProperty, "Duration"),
                    "start": safe(mpi.GetClipProperty, "Start"),
                    "end": safe(mpi.GetClipProperty, "End"),
                    "type": safe(mpi.GetClipProperty, "Type"),
                    "unique_id": safe(mpi.GetUniqueId),
                }
            track["items"].append(rec)
        doc["tracks"].append(track)

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2, sort_keys=True, default=str)
print("wrote", OUT)
tot = sum(len(t["items"]) for t in doc["tracks"])
print("tracks:", len(doc["tracks"]), "items:", tot, "timeline markers:", len(doc["timeline_markers"] or {}))
