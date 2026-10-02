#!/usr/bin/env python3
"""Does Resolve's control plane stay usable while a render is running?

Plan item 8 asks whether the Resolve CONTROL PLANE (reads, marker writes,
cursor moves) and the RENDER ENGINE can be separate scheduler resources,
or whether a render must hold the whole instance exclusively for its whole
length. Today every render does (`segment_renderer.render_batch`,
`resolve_axi render start`). This measures it instead of assuming it, and
also measures the two numbers that set `segment_renderer.MERGE_GAP_FRAMES`:
the fixed cost of one render job and the cost of one rendered frame.

It touches ONLY a disposable project it creates, on synthetic media:

1. saves the current project and records it, its project-manager folder
   and its current timeline (name and unique id); the disposable project
   is created in that same folder, so loading back needs no folder move;
2. creates `Ren Qualification - render control plane` (refuses if one
   exists), imports a generated 60 s 1080x1920 test pattern, builds two
   timelines from it;
3. COST: renders 5 one-frame jobs one batch each, then the same 5 frames
   as one batch of 5 jobs, then 1 job of 150 frames - wall seconds each;
4. CONTROL PLANE: starts a whole-timeline render and, while
   `IsRenderingInProgress()` is True, issues each probe call below,
   recording latency, return value and any exception; then reads back
   whether the render finished and how many frames its file holds;
5. in `finally`: loads the recorded project from its folder, makes the
   recorded timeline current by unique id, reads it back, and deletes
   the disposable project.

    bin/vep scripts/probe_render_control_plane.py --out <report.json>

Run it only inside a granted Resolve window: it switches projects.

Measured 2026-10-02 (Resolve Studio 21.1, a 26 s whole-timeline render):
every READ on a named handle answered in under 20 ms with correct values
(`GetCurrentTimeline`, `GetItemListInTrack`, `GetMarkers`,
`GetCurrentTimecode`, `GetRenderJobStatus`), but EVERY WRITE returned
`None` and did nothing - `AddMarker` on either timeline (no marker after),
`SetCurrentTimeline` (the cursor stayed put), `OpenPage` - and
`GetCurrentPage` answered `None`. The render was intact (1800/1800
frames). So the render engine is NOT a separate resource from the control
plane for writers: a render keeps the cursor and the exclusive lease for
its whole length. The cost numbers are in `segment_renderer.MERGE_GAP_FRAMES`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from library.tools.resolve_lock import under_lease  # noqa: E402

SCRATCH_PROJECT = "Ren Qualification - render control plane"
FPS = 30
SECONDS = 60


def _timed(fn):
    t0 = time.monotonic()
    try:
        value = fn()
        return {"seconds": round(time.monotonic() - t0, 4),
                "returned": repr(value)[:200], "raised": None}
    except Exception as exc:  # the measurement IS whether it raises
        return {"seconds": round(time.monotonic() - t0, 4),
                "returned": None, "raised": f"{type(exc).__name__}: {exc}"}


def _make_media(workdir: str) -> str:
    path = os.path.join(workdir, "pattern.mov")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"testsrc2=size=1080x1920:rate={FPS}", "-t", str(SECONDS),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        check=True, capture_output=True, encoding="utf-8")
    return path


def _render(project, timeline, ranges, outdir, name):
    """Queue one job per range, start together, wait. Returns wall seconds."""
    project.SetCurrentTimeline(timeline)
    ids = []
    for k, (a, b) in enumerate(ranges):
        if not project.SetRenderSettings({
                "TargetDir": outdir, "CustomName": f"{name}_{k:03d}",
                "SelectAllFrames": False, "MarkIn": a, "MarkOut": b,
                "FormatWidth": 720, "FormatHeight": 1280,
                "ExportVideo": True, "ExportAudio": False}):
            raise RuntimeError(f"SetRenderSettings refused {a}-{b}")
        job = project.AddRenderJob()
        if not job:
            raise RuntimeError(f"AddRenderJob refused {a}-{b}")
        ids.append(job)
    queued = {j["JobId"]: j for j in project.GetRenderJobList() or []}
    for job, (a, b) in zip(ids, ranges):
        got = (queued.get(job, {}).get("MarkIn"), queued.get(job, {}).get("MarkOut"))
        if got != (a, b):
            raise RuntimeError(f"queued {got} for {a}-{b}; refusing to start")
    t0 = time.monotonic()
    if not project.StartRendering(ids, False):
        raise RuntimeError("StartRendering returned False")
    while project.IsRenderingInProgress():
        time.sleep(0.1)
    seconds = time.monotonic() - t0
    for job in ids:
        project.DeleteRenderJob(job)
    return round(seconds, 3)


def _frames_in(outdir: str, prefix: str):
    for f in os.listdir(outdir):
        if f.startswith(prefix) and not f.endswith(".xml"):
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-count_frames", "-select_streams",
                 "v:0", "-show_entries", "stream=nb_read_frames", "-of",
                 "csv=p=0", os.path.join(outdir, f)],
                capture_output=True, encoding="utf-8", check=False)
            # ffprobe can print the count more than once (one per
            # section); the first line is the video stream's.
            lines = [ln for ln in out.stdout.split() if ln.strip()]
            return int(lines[0]) if lines else 0
    return None


def _cost(project, timeline, outdir):
    frames = [100, 400, 700, 1000, 1300]
    separate = [_render(project, timeline, [(f, f)], outdir, f"one_{f}")
                for f in frames]
    together = _render(project, timeline, [(f, f) for f in frames], outdir, "five")
    long_range = _render(project, timeline, [(200, 349)], outdir, "range150")
    return {
        "one_frame_job_seconds": separate,
        "five_one_frame_jobs_one_start_seconds": together,
        "one_job_150_frames_seconds": long_range,
    }


def _control_plane(resolve, project, render_tl, other_tl, outdir):
    probes = {
        "GetCurrentTimeline": lambda: project.GetCurrentTimeline().GetName(),
        "GetCurrentPage": resolve.GetCurrentPage,
        "GetItemListInTrack": lambda: len(
            render_tl.GetItemListInTrack("video", 1) or []),
        "GetMarkers": render_tl.GetMarkers,
        "GetCurrentTimecode": render_tl.GetCurrentTimecode,
        "GetRenderJobStatus": lambda: project.GetRenderJobStatus(job),
        "AddMarker_other_timeline": lambda: other_tl.AddMarker(
            30, "Blue", "probe", "during render", 1),
        "AddMarker_rendering_timeline": lambda: render_tl.AddMarker(
            60, "Red", "probe", "during render", 1),
        "SetCurrentTimeline_other": lambda: project.SetCurrentTimeline(other_tl),
        "GetCurrentTimeline_after_switch": lambda: project.GetCurrentTimeline().GetName(),
        "SetCurrentTimeline_back": lambda: project.SetCurrentTimeline(render_tl),
        "OpenPage_edit": lambda: resolve.OpenPage("edit"),
    }
    project.SetCurrentTimeline(render_tl)
    last = SECONDS * FPS - 1
    project.SetRenderSettings({
        "TargetDir": outdir, "CustomName": "whole", "SelectAllFrames": False,
        "MarkIn": 0, "MarkOut": last, "FormatWidth": 1080,
        "FormatHeight": 1920, "ExportVideo": True, "ExportAudio": False})
    job = project.AddRenderJob()
    t0 = time.monotonic()
    project.StartRendering([job], False)
    while not project.IsRenderingInProgress() and time.monotonic() - t0 < 10:
        time.sleep(0.05)
    results = {}
    for name, probe in probes.items():
        rendering_before = project.IsRenderingInProgress()
        results[name] = _timed(probe)
        results[name]["rendering_before"] = rendering_before
        results[name]["rendering_after"] = project.IsRenderingInProgress()
    while project.IsRenderingInProgress():
        time.sleep(0.2)
    status = _timed(lambda: project.GetRenderJobStatus(job))
    project.DeleteRenderJob(job)
    return {
        "render_seconds": round(time.monotonic() - t0, 2),
        "probes": results,
        "job_status_after": status,
        "frames_expected": last + 1,
        "frames_rendered": _timed(lambda: _frames_in(outdir, "whole")),
        "markers_after": {"rendering": repr(render_tl.GetMarkers())[:300],
                          "other": repr(other_tl.GetMarkers())[:300]},
    }


@under_lease("probe render control plane")
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    import DaVinciResolveScript as dvr
    from library.tools.resolve_locale import scriptapp_preserving_locale

    resolve = scriptapp_preserving_locale(dvr, "Resolve")
    if not resolve:
        print("Resolve is not running", file=sys.stderr)
        return 2
    pm = resolve.GetProjectManager()
    home = pm.GetCurrentProject()
    home_name = home.GetName()
    home_folder = pm.GetCurrentFolder()
    home_tl = home.GetCurrentTimeline()
    home_tl_name = home_tl.GetName() if home_tl else None
    home_tl_id = home_tl.GetUniqueId() if home_tl else None
    home_page = resolve.GetCurrentPage()
    home_count = home.GetTimelineCount()
    if SCRATCH_PROJECT in (pm.GetProjectListInCurrentFolder() or []):
        print(f"{SCRATCH_PROJECT!r} already exists - refusing", file=sys.stderr)
        return 2
    if not pm.SaveProject():
        print(f"could not save {home_name!r} - refusing to leave it",
              file=sys.stderr)
        return 2

    workdir = tempfile.mkdtemp(prefix="ren_render_probe_")
    report = {"home": {"project": home_name, "folder": home_folder,
                       "timeline": home_tl_name, "timeline_id": home_tl_id,
                       "timeline_count": home_count, "page": home_page}}
    created = False
    try:
        media = _make_media(workdir)
        project = pm.CreateProject(SCRATCH_PROJECT)
        if not project:
            raise RuntimeError("CreateProject returned nothing")
        created = True
        project.SetSetting("timelineFrameRate", str(FPS))
        project.SetSetting("timelineResolutionWidth", "1080")
        project.SetSetting("timelineResolutionHeight", "1920")
        pool = project.GetMediaPool()
        items = pool.ImportMedia([media])
        if not items:
            raise RuntimeError("ImportMedia returned nothing")
        render_tl = pool.CreateTimelineFromClips("render", items)
        other_tl = pool.CreateTimelineFromClips("other", items)
        if not (render_tl and other_tl):
            raise RuntimeError("CreateTimelineFromClips returned nothing")
        # MarkIn/MarkOut then mean the same thing whether Resolve reads
        # them as absolute or start-relative frames.
        for tl in (render_tl, other_tl):
            tl.SetStartTimecode("00:00:00:00")
        report["scratch_timeline"] = {
            "start_frame": render_tl.GetStartFrame(),
            "end_frame": render_tl.GetEndFrame(),
            "load_avg": os.getloadavg(),
        }
        resolve.OpenPage("deliver")
        report["cost"] = _cost(project, render_tl, workdir)
        report["control_plane"] = _control_plane(
            resolve, project, render_tl, other_tl, workdir)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        restore = {}
        if created:
            back = pm.LoadProject(home_name)
            restore["loaded"] = bool(back)
            if back and home_tl_id:
                match = next(
                    (back.GetTimelineByIndex(i)
                     for i in range(1, back.GetTimelineCount() + 1)
                     if back.GetTimelineByIndex(i).GetUniqueId() == home_tl_id),
                    None)
                restore["timeline_set"] = bool(
                    match and back.SetCurrentTimeline(match))
                current = back.GetCurrentTimeline()
                restore["timeline_now"] = current.GetName() if current else None
                restore["timeline_matches"] = bool(
                    current and current.GetUniqueId() == home_tl_id)
                restore["timeline_count"] = back.GetTimelineCount()
            if home_page:
                resolve.OpenPage(home_page)
            restore["scratch_deleted"] = bool(
                back and pm.DeleteProject(SCRATCH_PROJECT))
        report["restore"] = restore
        shutil.rmtree(workdir, ignore_errors=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
    print(json.dumps(report, indent=2))
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    sys.exit(main())
