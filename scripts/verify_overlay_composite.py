#!/usr/bin/env python3
"""Prove in Resolve that an overlay composites without darkening the frame.

R6, as a command.  The defect this answers is not hypothetical and not
subtle: a QuickTime Animation overlay imported on Resolve's default
`Auto` data level is read as video range, and the premultiplied
composite then adds a below-black to the WHOLE frame - measured 16/255
on every pixel, including every pixel where the overlay's own alpha is
zero.  It ships looking like a grade problem.

So this builds the smallest thing that can show it: a scratch project,
a flat plate on V1, the overlay on V2, two single-frame exports through
Deliver - one with the overlay, one without - and
`overlay_carriage.assert_transparent_region_unchanged` over the pair.
The verdict is on PIXELS RESOLVE DREW, not on whether a property was
set (AGENTS.md 5: measure a grade on an EXPORT, never on a viewer).

    python3 scripts/verify_overlay_composite.py <overlay.mov> [--frame N]

Exits 0 when the plate came through, 1 when it did not, 2 when the
measurement could not be taken at all.

RESOLVE IS ONE SHARED INSTANCE.  This creates its own project, restores
whatever project and timeline were open, and deletes the one it made -
but it does switch the current project while it runs, so do not fire it
while someone is working.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.overlay_carriage import (  # noqa: E402
    TransparentRegionDarkened,
    apply_clip_attributes,
    assert_transparent_region_unchanged,
    probe_overlay,
)

SCRATCH_PROJECT = "SCRATCH_overlay_composite_probe"
PLATE_VALUE = "0x808080"
"""Mid grey: a below-black added to it is unmistakable, and a lift is too."""


def _raw(path: str, pix_fmt: str, timeout: int = 120):
    import numpy as np

    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-pix_fmt", pix_fmt,
         "-f", "rawvideo", "-"],
        capture_output=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"cannot decode {path}")
    dtype = "<u2" if pix_fmt.endswith("48le") else "u1"
    return np.frombuffer(result.stdout, dtype=dtype)


def _plate(directory: str, width: int, height: int, rate: str,
           frames: int) -> str:
    path = os.path.join(directory, "plate.mov")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i",
         f"color=c={PLATE_VALUE}:s={width}x{height}:r={rate}:d=30",
         "-frames:v", str(frames),
         "-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le",
         path], check=True, capture_output=True)
    return path


def _shape(overlay: str) -> tuple[int, int, str, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
         "-of", "csv=p=0", overlay],
        capture_output=True, text=True, check=True).stdout.strip()
    width, height, rate, frames = out.split(",")
    return int(width), int(height), rate, int(frames)


def verify(overlay: str, frame: int) -> int:
    import numpy as np

    fields = probe_overlay(overlay)
    if not fields:
        print(f"cannot probe {overlay}", file=sys.stderr)
        return 2
    width, height, rate, frames = _shape(overlay)
    if frame >= frames:
        print(f"{overlay} has {frames} frames; {frame} is past the end",
              file=sys.stderr)
        return 2

    # AGENTS.md 9: reach Resolve through the wrapper, never
    # `dvr.scriptapp` - the raw call resets LC_CTYPE to C and every
    # later read of this repository's UTF-8 sources raises.
    from library.tools.marker_feedback import (
        RESOLVE_SCRIPT_API,
        RESOLVE_SCRIPT_LIB,
    )
    from library.tools.resolve_locale import scriptapp_preserving_locale

    modules = os.path.join(RESOLVE_SCRIPT_API, "Modules")
    if modules not in sys.path:
        sys.path.append(modules)
    os.environ.setdefault("RESOLVE_SCRIPT_API", RESOLVE_SCRIPT_API)
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", RESOLVE_SCRIPT_LIB)
    try:
        import DaVinciResolveScript as dvr
    except ImportError:
        print("DaVinci Resolve's scripting module is not importable; set "
              "RESOLVE_SCRIPT_API and RESOLVE_SCRIPT_LIB (AGENTS.md 9)",
              file=sys.stderr)
        return 2
    resolve = scriptapp_preserving_locale(dvr)
    if resolve is None:
        print("Resolve is not reachable", file=sys.stderr)
        return 2

    manager = resolve.GetProjectManager()
    was = manager.GetCurrentProject()
    was_name = was.GetName() if was else ""
    was_timeline = was.GetCurrentTimeline() if was else None
    was_timeline_name = was_timeline.GetName() if was_timeline else ""

    work = tempfile.mkdtemp(prefix="overlay_composite_")
    try:
        plate = _plate(work, width, height, rate, frames)
        manager.DeleteProject(SCRATCH_PROJECT)
        project = manager.CreateProject(SCRATCH_PROJECT)
        if not project:
            print("Resolve would not create the scratch project",
                  file=sys.stderr)
            return 2
        project.SetSetting("timelineResolutionWidth", str(width))
        project.SetSetting("timelineResolutionHeight", str(height))
        pool = project.GetMediaPool()
        resolve.GetMediaStorage().AddItemListToMediaPool([plate, overlay])
        items = {clip.GetName(): clip
                 for clip in pool.GetRootFolder().GetClipList()}
        overlay_item = items.get(os.path.basename(overlay))
        plate_item = items.get(os.path.basename(plate))
        if not overlay_item or not plate_item:
            print("Resolve would not import the probe media",
                  file=sys.stderr)
            return 2
        # The attributes under test, applied exactly as a build applies
        # them - and read back, so a refusal is a refusal here too.
        apply_clip_attributes(overlay_item, overlay, probe=fields)

        exported = {}
        for tag, with_overlay in (("plate", False), ("composite", True)):
            timeline = pool.CreateEmptyTimeline(f"T_{tag}")
            project.SetCurrentTimeline(timeline)
            timeline.SetSetting("useCustomSettings", "1")
            timeline.SetSetting("timelineResolutionWidth", str(width))
            timeline.SetSetting("timelineResolutionHeight", str(height))
            timeline.AddTrack("video")
            pool.AppendToTimeline([{"mediaPoolItem": plate_item,
                                    "startFrame": 0, "endFrame": frames - 1,
                                    "trackIndex": 1, "recordFrame": 0,
                                    "mediaType": 1}])
            if with_overlay:
                pool.AppendToTimeline([{"mediaPoolItem": overlay_item,
                                        "startFrame": 0,
                                        "endFrame": frames - 1,
                                        "trackIndex": 2, "recordFrame": 0,
                                        "mediaType": 1}])
            project.DeleteAllRenderJobs()
            project.SetCurrentRenderFormatAndCodec("png", "RGB16")
            project.SetRenderSettings({
                "SelectAllFrames": False, "MarkIn": frame, "MarkOut": frame,
                "TargetDir": work, "CustomName": tag,
                "ExportVideo": True, "ExportAudio": False,
                "FormatWidth": width, "FormatHeight": height})
            job = project.AddRenderJob()
            if not job or not project.StartRendering(isInteractiveMode=False):
                print(f"Resolve would not render the {tag} frame",
                      file=sys.stderr)
                return 2
            path = os.path.join(work, f"{tag}{frame:08d}.png")
            deadline = time.time() + 180
            while not os.path.isfile(path) and time.time() < deadline:
                time.sleep(1)
            if not os.path.isfile(path):
                # A capture that did not happen RAISES (AGENTS.md 5).
                print(f"the {tag} frame was never written to {path}",
                      file=sys.stderr)
                return 2
            exported[tag] = path

        composite = _raw(exported["composite"], "rgb48le").reshape(
            height, width, 3).astype(float) / 257.0
        plate_pixels = _raw(exported["plate"], "rgb48le").reshape(
            height, width, 3).astype(float) / 257.0
        alpha = _raw_alpha(overlay, frame, width, height)
        worst = assert_transparent_region_unchanged(
            composite, plate_pixels, alpha,
            what=f"{os.path.basename(overlay)} frame {frame}")
        print(f"PASS  {os.path.basename(overlay)} frame {frame}: the plate "
              f"comes through untouched under alpha 0 "
              f"(worst {worst:.6f} of 255, "
              f"{int(np.count_nonzero(alpha == 0)):,} pixels measured)")
        return 0
    except TransparentRegionDarkened as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        return 1
    finally:
        if was_name:
            manager.LoadProject(was_name)
            restored = manager.GetCurrentProject()
            if restored and was_timeline_name:
                for index in range(1, restored.GetTimelineCount() + 1):
                    found = restored.GetTimelineByIndex(index)
                    if found.GetName() == was_timeline_name:
                        restored.SetCurrentTimeline(found)
                        break
        manager.DeleteProject(SCRATCH_PROJECT)
        subprocess.run(["rm", "-rf", work], check=False)


def _raw_alpha(overlay: str, frame: int, width: int, height: int):
    """The overlay's OWN alpha plane at one frame, off the file."""
    import numpy as np

    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", overlay,
         "-vf", f"select=eq(n\\,{frame}),alphaextract",
         "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, check=False)
    if result.returncode != 0 or len(result.stdout) != width * height:
        raise RuntimeError(
            f"cannot read the alpha plane of {overlay} at frame {frame}")
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overlay")
    parser.add_argument("--frame", type=int, default=0,
                        help="which frame to export and measure")
    args = parser.parse_args()
    return verify(os.path.abspath(args.overlay), args.frame)


if __name__ == "__main__":
    sys.exit(main())
