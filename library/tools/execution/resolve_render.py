#!/usr/bin/env python3
"""Export a built Resolve timeline to a file on disk.

Step 6.01 built a timeline and stopped there, so every run ended with
`distribution_ready: false` and the note "re-run validation with
output_path set".  Nothing ever set it, because nothing ever rendered.

This module drives the Deliver page: it queues a render job for the
current timeline, starts it, waits for completion, and returns the path
of the file Resolve actually wrote.

Run standalone (the timeline must already exist in the current project):

    python3 resolve_render.py --timeline Pipeline_Edit \\
        --output-dir /path/to/exports --name my_edit
"""

import json
import os
import sys
import time
from library.tools.resolve_lock import assert_current_timeline, under_lease
from library.tools.heavy_work_lock import heavy_work_locked
from library.tools import perf_ledger

# Resolve's Python API is not importable until these are set - see
# AGENTS.md section 5.
RESOLVE_API = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
RESOLVE_LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"

# Poll interval and ceiling for a render. A 60s vertical edit renders in
# well under a minute; the ceiling exists so a wedged Resolve fails the
# step instead of hanging the pipeline.
POLL_SECONDS = 3
DEFAULT_TIMEOUT_SECONDS = 1800

# A render that produced fewer bytes than this is not a video.
MIN_PLAUSIBLE_BYTES = 100_000


class RenderError(RuntimeError):
    """The export did not produce a usable file."""


def _connect():
    if RESOLVE_API not in sys.path:
        sys.path.append(os.path.join(RESOLVE_API, "Modules"))
    os.environ["RESOLVE_SCRIPT_API"] = RESOLVE_API
    os.environ["RESOLVE_SCRIPT_LIB"] = RESOLVE_LIB
    import DaVinciResolveScript as dvr

    from library.tools.resolve_locale import scriptapp_preserving_locale
    resolve = scriptapp_preserving_locale(dvr, "Resolve")
    if not resolve:
        raise ConnectionError(
            "Cannot connect to DaVinci Resolve. Is it running with a "
            "project open?"
        )
    return resolve


def _find_timeline(project, timeline_name: str):
    """The timeline handle named `timeline_name`, without moving anything.

    Read-only: naming a handle is not a cursor move, so this takes no
    lease and disturbs no holder. A caller that then needs it CURRENT
    establishes it through `_select_timeline`, which goes through the
    guard exactly like every other cursor move.
    """
    if not timeline_name:
        timeline = project.GetCurrentTimeline()
        if not timeline:
            raise RenderError("No current timeline to render")
        return timeline

    for i in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(i)
        if timeline and timeline.GetName() == timeline_name:
            return timeline

    raise RenderError(
        f"Timeline {timeline_name!r} is not in this project. Available: "
        f"{[project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)]}"
    )


def _select_timeline(project, timeline_name: str):
    """Make *timeline_name* current, or raise if it does not exist."""
    timeline = _find_timeline(project, timeline_name)
    if timeline_name:
        # Establishing the cursor IS a write to instance-global state:
        # through the guard, under a lease, like every other cursor
        # move - an unleased select walked the cursor out from under a
        # sibling lane's Fusion pass on 2026-09-20.
        assert_current_timeline(project, timeline)
    return timeline


def _assert_has_audio(path: str) -> None:
    """A silent export is a failed export - catch it here, not two steps on."""
    import subprocess

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  WARNING: could not probe {path} for audio: {e}",
              file=sys.stderr)
        return

    if not probe.stdout.strip():
        raise RenderError(
            f"{path} has no audio stream. Resolve rendered picture only - "
            f"check the render preset's ExportAudio setting."
        )


def _candidate_render_files(output_dir: str, name: str) -> list:
    if not os.path.isdir(output_dir):
        return []
    return [
        f for f in os.listdir(output_dir)
        if f.startswith(name)
    ]


def _files_written_since(output_dir: str, name: str, since: float) -> list:
    """Files matching *name* whose contents were written after *since*.

    Resolve overwrites an existing export of the same name, so a
    before/after directory diff comes back empty on every re-run and the
    old code then validated whichever stale file sorted last. Modification
    time is what actually distinguishes this run's output.
    """
    fresh = []
    for f in _candidate_render_files(output_dir, name):
        path = os.path.join(output_dir, f)
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime >= since:
            fresh.append((mtime, f))
    return [f for _, f in sorted(fresh)]


@under_lease("render out")
@heavy_work_locked("Resolve timeline render")
def render_timeline(
    timeline_name: str = "",
    output_dir: str = "",
    output_name: str = "",
    fmt: str = "mp4",
    codec: str = "H264",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Queue, start and await a render of the timeline. Returns a report.

    Raises RenderError when Resolve refuses the job or the resulting file
    is missing or implausibly small.
    """
    resolve = _connect()
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        raise RenderError("No project is open in Resolve")

    timeline = _select_timeline(project, timeline_name)
    timeline_name = timeline.GetName()
    output_name = output_name or timeline_name

    if not output_dir:
        raise RenderError("output_dir is required")
    os.makedirs(output_dir, exist_ok=True)

    prev_page = resolve.GetCurrentPage()

    # Save format/codec - the ONLY render state Resolve lets us read
    # back (H4 from the statefulness hazards investigation).  The rest
    # of SetRenderSettings is write-only, so we cannot save-and-restore
    # the full settings dict.  We track our own job ID and clean it up
    # on every exit path including exceptions.
    saved_format_codec = project.GetCurrentRenderFormatAndCodec() or {}
    our_job_id = None

    try:
        resolve.OpenPage("deliver")

        if not project.SetCurrentRenderFormatAndCodec(fmt, codec):
            raise RenderError(
                f"Resolve rejected render format/codec {fmt}/{codec}. "
                f"Available formats: {list((project.GetRenderFormats() or {}).keys())}"
            )

        # Render the whole timeline: clear any inherited in/out marks by
        # spanning the timeline's own frame range.
        start_frame = timeline.GetStartFrame()
        end_frame = timeline.GetEndFrame()
        # Nor is RESOLUTION on by default, and it is the same defect as audio
        # one line down: SetRenderSettings inherits whatever the PROJECT's
        # render preset last held, and a freshly created Resolve project
        # defaults to 1920x1080. Project 001 built a correct 1080x1920
        # timeline and rendered out landscape, and every structural check
        # passed - duration, framerate, audio streams, frame occupancy - on a
        # file of the wrong shape. It only stayed hidden because every project
        # the pipeline had rendered into before had been set to vertical BY
        # HAND. Read off the TIMELINE that was actually built, so there is no
        # second source of truth to disagree with the manifest, and REFUSE
        # rather than inherit when it cannot be read.
        def _timeline_setting(key: str) -> int:
            try:
                return int(timeline.GetSetting(key))
            except (TypeError, ValueError):
                return 0

        width = _timeline_setting("timelineResolutionWidth")
        height = _timeline_setting("timelineResolutionHeight")
        if width <= 0 or height <= 0:
            raise RenderError(
                f"Timeline {timeline_name!r} reports no usable resolution "
                f"({width}x{height}), so the render would silently inherit the "
                f"Resolve project's own default. Refusing rather than guessing."
            )

        settings = {
            "TargetDir": output_dir,
            "CustomName": output_name,
            "MarkIn": start_frame,
            "MarkOut": max(start_frame, end_frame - 1),
            "SelectAllFrames": False,
            "FormatWidth": width,
            "FormatHeight": height,
            # Audio is NOT on by default: whatever the project's last render
            # preset had wins, and a silent export passes every structural
            # check while being useless. State it explicitly.
            "ExportAudio": True,
            "AudioCodec": "aac",
            "AudioBitDepth": 16,
            "AudioSampleRate": 48000,
        }
        if not project.SetRenderSettings(settings):
            raise RenderError(f"Resolve rejected render settings: {settings}")

        assert_current_timeline(project, timeline)
        our_job_id = project.AddRenderJob()
        if not our_job_id:
            raise RenderError(
                "AddRenderJob returned no job id - Resolve would not queue the "
                "render (check that the timeline is not empty)"
            )

        print(f"  Queued render job {our_job_id} for {timeline_name} "
              f"({start_frame}-{end_frame})", file=sys.stderr)

        # Filesystem mtimes have coarse resolution on some volumes; back the
        # cutoff off by a second so this run's own output is never excluded.
        render_started_at = time.time() - 1.0

        # Resolve renders in its own process: this is the wall the
        # profile charges to it. See library/tools/perf_ledger.py.
        with perf_ledger.span("resolve_render", backend="resolve",
                              frames=end_frame - start_frame):
            if not project.StartRendering([our_job_id], isInteractiveMode=False):
                raise RenderError(f"StartRendering failed for job {our_job_id}")

            deadline = time.time() + timeout_seconds
            status = {}
            while time.time() < deadline:
                if not project.IsRenderingInProgress():
                    break
                status = project.GetRenderJobStatus(our_job_id) or {}
                print(f"  Rendering... {status.get('CompletionPercentage', 0)}%",
                      file=sys.stderr)
                time.sleep(POLL_SECONDS)
            else:
                project.StopRendering()
                raise RenderError(
                    f"Render did not finish within {timeout_seconds}s "
                    f"(last status: {status})"
                )

        status = project.GetRenderJobStatus(our_job_id) or {}
        job_status = status.get("JobStatus", "Unknown")
        if job_status != "Complete":
            raise RenderError(
                f"Render job {our_job_id} ended as {job_status}: "
                f"{status.get('Error', 'no error reported')}"
            )

        produced = _files_written_since(output_dir, output_name, render_started_at)
        if not produced:
            stale = sorted(_candidate_render_files(output_dir, output_name))
            raise RenderError(
                f"Render reported Complete but wrote no file starting with "
                f"{output_name!r} in {output_dir} during this run "
                f"(pre-existing files there: {stale or 'none'})"
            )

        output_path = os.path.join(output_dir, produced[-1])
        size_bytes = os.path.getsize(output_path)
        if size_bytes < MIN_PLAUSIBLE_BYTES:
            raise RenderError(
                f"Rendered file {output_path} is only {size_bytes} bytes - "
                f"that is not a video"
            )

        _assert_has_audio(output_path)

        print(f"  \u2713 Rendered {output_path} ({size_bytes / 1e6:.1f} MB)",
              file=sys.stderr)

        return {
            "output_path": output_path,
            "size_bytes": size_bytes,
            "job_id": our_job_id,
            "job_status": job_status,
            "timeline_name": timeline_name,
            "format": fmt,
            "codec": codec,
        }

    finally:
        # Restore format/codec - the only render state with a read-back
        # API.  Do this BEFORE deleting the job so any error here does
        # not skip the job cleanup.
        if saved_format_codec:
            try:
                project.SetCurrentRenderFormatAndCodec(
                    saved_format_codec.get("format", ""),
                    saved_format_codec.get("codec", ""),
                )
            except Exception:
                pass  # best-effort; the job cleanup below is more important

        # Delete ONLY the job this process created.
        if our_job_id:
            try:
                project.DeleteRenderJob(our_job_id)
            except Exception:
                pass  # best-effort

        if prev_page and prev_page != "deliver":
            try:
                resolve.OpenPage(prev_page)
            except Exception:
                pass  # best-effort; must not mask a propagating exception


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Render a Resolve timeline")
    parser.add_argument("--timeline", default="")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--format", default="mp4")
    parser.add_argument("--codec", default="H264")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()

    result = render_timeline(
        timeline_name=args.timeline,
        output_dir=args.output_dir,
        output_name=args.name,
        fmt=args.format,
        codec=args.codec,
        timeout_seconds=args.timeout,
    )
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
