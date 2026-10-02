#!/usr/bin/env python3
"""
segment_renderer.py - Render timeline segments for visual QA analysis.

Uses the DaVinci Resolve scripting API to render specific timeline ranges
to temporary files. Supports both single-frame renders (for frame grab QA)
and multi-frame segments (for video analysis via Gemma 4 12B).

Connection setup is the caller's responsibility - this module accepts
resolve/project/timeline objects as parameters.
"""

import os
import subprocess
import tempfile
import time
from library.tools.resolve_lock import assert_current_timeline, under_lease
from dataclasses import dataclass
from typing import Optional


# Resolution presets for QA renders (width x height, 9:16 vertical)
QA_RESOLUTIONS = {
    "720p": (720, 1280),
    "540p": (540, 960),
    "360p": (360, 640),
    "full": (1080, 1920),
}

# Minimum file size (bytes) that indicates real content vs black/broken frame
MIN_VALID_FILE_SIZE = 2048


@dataclass
class SegmentRenderResult:
    """Result from rendering a timeline segment."""
    path: str
    mark_in: int
    mark_out: int
    duration_frames: int
    width: int
    height: int
    success: bool
    error: Optional[str] = None


class RenderSettingsError(RuntimeError):
    """A render left project-global state dirty and the cleanup failed.

    Render settings and the render queue are PROJECT-global in Resolve
    (H4 from the statefulness hazards investigation).  A render that
    changes them and does not clean up means the NEXT render - including
    the captain's own manual render on the Deliver page - inherits
    whatever this process left behind: a temp TargetDir, a QA custom
    name, 720p resolution, ExportAudio False.

    The Resolve scripting API has no GetRenderSettings(), so a full
    save-and-restore of the settings dict is impossible.  What IS
    restorable: format/codec (via GetCurrentRenderFormatAndCodec /
    SetCurrentRenderFormatAndCodec) and the render job itself (via
    DeleteRenderJob with the job ID this process created).  The guard
    is: borrow and restore on EVERY exit path including exceptions.
    """


# Two requested ranges this close are rendered as ONE job: every job pays
# Resolve's per-job setup, and a frame between two ranges costs one frame
# of render. The gap is where the two costs cross, MEASURED 2026-10-02 by
# `scripts/probe_render_control_plane.py` (Resolve Studio 21.1, synthetic
# 1080x1920 30 fps, a full-suite gate running beside it, load avg 25-50):
#   one 1-frame job, own start          0.81-0.87 s
#   five 1-frame jobs, ONE start        3.87 s (0.77 s a job - queueing
#                                       jobs together barely saves; merging
#                                       ranges into one job is the saving)
#   one 150-frame job at 720p           1.95 s -> ~7.5 ms a frame
#   one 1800-frame job at full 1080p    26.1 s -> ~14.5 ms a frame
# so the break-even gap is ~0.8 s / per-frame cost. A preset not measured
# takes the 720p value (cheaper frames only move the crossing further out).
MERGE_GAP_FRAMES = {"720p": 100, "full": 55}


def merge_gap_frames(resolution: str) -> int:
    return MERGE_GAP_FRAMES.get(resolution, MERGE_GAP_FRAMES["720p"])


def merge_ranges(ranges, max_gap_frames: int = MERGE_GAP_FRAMES["720p"]):
    """Inclusive frame ranges, merged where at most `max_gap_frames` apart.

    `[(300, 369), (372, 420)]` is one render of 300-420, not two jobs.
    """
    merged = []
    for mark_in, mark_out in sorted((int(a), int(b)) for a, b in ranges):
        if mark_out < mark_in:
            raise ValueError(f"range {mark_in}-{mark_out} ends before it starts")
        if merged and mark_in <= merged[-1][1] + 1 + max_gap_frames:
            merged[-1] = (merged[-1][0], max(merged[-1][1], mark_out))
        else:
            merged.append((mark_in, mark_out))
    return merged


@dataclass
class BatchRenderResult:
    """One batch: a SegmentRenderResult per merged range, in frame order."""
    segments: list
    error: Optional[str] = None

    def covering(self, mark_in: int, mark_out: int) -> Optional[SegmentRenderResult]:
        """The rendered segment that holds this whole range, if it rendered."""
        for seg in self.segments:
            if seg.success and seg.mark_in <= mark_in and mark_out <= seg.mark_out:
                return seg
        return None


def _failed(ranges, width, height, error):
    return BatchRenderResult(
        segments=[SegmentRenderResult(
            path="", mark_in=a, mark_out=b, duration_frames=b - a + 1,
            width=width, height=height, success=False, error=error)
            for a, b in ranges],
        error=error)


@under_lease("render segments")
def render_batch(resolve, project, timeline, ranges,
                 output_dir: str = None,
                 resolution: str = "720p",
                 max_gap_frames: Optional[int] = None,
                 name_prefix: str = "qa_batch",
                 timeout: int = 120) -> BatchRenderResult:
    """Render several timeline ranges in ONE configure-render-cleanup pass.

    The ranges are merged (`merge_ranges`), one job is queued per merged
    range, every job's MarkIn/MarkOut is read back off the queue BEFORE
    anything starts, and the jobs start together and are polled once.
    The page, the format/codec and the queue are put back once, on every
    exit path. A caller wanting frames or sub-ranges cuts them from the
    result with ffmpeg (`extract_frames`, `trim_to_range`).
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="vqa_segment_")
    os.makedirs(output_dir, exist_ok=True)

    width, height = QA_RESOLUTIONS.get(resolution, QA_RESOLUTIONS["720p"])
    if max_gap_frames is None:
        max_gap_frames = merge_gap_frames(resolution)
    merged = merge_ranges(ranges, max_gap_frames)
    if not merged:
        return BatchRenderResult(segments=[])
    names = ([name_prefix] if len(merged) == 1 else
             [f"{name_prefix}_r{k:03d}" for k in range(len(merged))])

    prev_page = resolve.GetCurrentPage()

    # Save the format/codec so we can restore it.  This is the ONLY
    # render state Resolve lets us read back; the rest of
    # SetRenderSettings is write-only.
    saved_format_codec = project.GetCurrentRenderFormatAndCodec() or {}

    # Track the job ids we create so we delete ONLY our own jobs in the
    # finally block - never DeleteAllRenderJobs, which destroys the
    # captain's queued jobs and any concurrent process's jobs.
    our_job_ids = []

    try:
        resolve.OpenPage("deliver")

        for (mark_in, mark_out), name in zip(merged, names):
            project.SetRenderSettings({
                "TargetDir": output_dir,
                "CustomName": name,
                "FormatWidth": width,
                "FormatHeight": height,
                # WITHOUT `SelectAllFrames: False` Resolve IGNORES the
                # range and queues the whole timeline (§5: judge the call
                # by what it returns, and here even the return lies - both
                # SetRenderSettings and AddRenderJob succeed).  Measured
                # 2026-09-11 on `Podcast (field test)` / Reel 09: a request
                # for frame 300 alone queued MarkIn 0 / MarkOut 1665 and
                # rendered 1,471 TIFFs of the entire reel before it was
                # stopped by hand.  On the captain's machine - the fleet's
                # one hard CPU limiter - a "single frame" that renders a
                # whole timeline is not a slow check, it is an outage.
                "SelectAllFrames": False,
                "MarkIn": mark_in,
                "MarkOut": mark_out,
                "ExportVideo": True,
                "ExportAudio": False,  # visual QA doesn't need audio
            })
            assert_current_timeline(project, timeline)
            our_job_ids.append(project.AddRenderJob())

        # And then what Resolve actually QUEUED.  A range that did not
        # take is caught HERE, before a frame is rendered, because the
        # cost of finding out afterwards is the whole timeline.  The
        # jobs are deleted by the `finally` below either way.
        queue = {job.get("JobId"): job for job in (project.GetRenderJobList() or [])}
        for job_id, (mark_in, mark_out) in zip(our_job_ids, merged):
            queued = queue.get(job_id) if job_id else None
            if queued is None:
                return _failed(merged, width, height,
                               f"AddRenderJob returned {job_id!r} but no such "
                               f"job is in the render queue - nothing was started")
            got_in, got_out = queued.get("MarkIn"), queued.get("MarkOut")
            if (got_in, got_out) != (mark_in, mark_out):
                return _failed(merged, width, height,
                               f"Resolve queued MarkIn={got_in} MarkOut={got_out} "
                               f"for a request of {mark_in}-{mark_out} - refusing "
                               f"to start a render of a range nobody asked for")
        project.StartRendering(our_job_ids, isInteractiveMode=False)

        # Poll for completion
        elapsed = 0.0
        poll_interval = 0.5
        while project.IsRenderingInProgress():
            time.sleep(poll_interval)
            elapsed += poll_interval
            if elapsed >= timeout:
                project.StopRendering()
                return _failed(merged, width, height,
                               f"Render timed out after {timeout}s")

        segments = []
        for (mark_in, mark_out), name in zip(merged, names):
            seg = SegmentRenderResult(
                path="", mark_in=mark_in, mark_out=mark_out,
                duration_frames=mark_out - mark_in + 1,
                width=width, height=height, success=False)
            # Locate the rendered file (Resolve appends format extension)
            rendered_path = _find_rendered_file(output_dir, name)
            if not rendered_path:
                seg.error = "Rendered file not found in output directory"
            elif os.path.getsize(rendered_path) < MIN_VALID_FILE_SIZE:
                # Sanity check: file too small means black/broken
                seg.path = rendered_path
                seg.error = (f"Rendered file too small ({os.path.getsize(rendered_path)} "
                             f"bytes) - likely black/broken")
            else:
                seg.path, seg.success = rendered_path, True
            segments.append(seg)
        return BatchRenderResult(segments=segments)

    finally:
        # Restore format/codec - the only render state with a read-back
        # API.  Do this BEFORE deleting the jobs so any error here does
        # not skip the job cleanup.
        if saved_format_codec:
            try:
                project.SetCurrentRenderFormatAndCodec(
                    saved_format_codec.get("format", ""),
                    saved_format_codec.get("codec", ""),
                )
            except Exception:
                pass  # best-effort; the job cleanup below is more important

        # Delete ONLY the jobs this process created.
        for job_id in our_job_ids:
            if job_id:
                try:
                    project.DeleteRenderJob(job_id)
                except Exception:
                    pass  # best-effort

        if prev_page and prev_page != "deliver":
            resolve.OpenPage(prev_page)


@under_lease("render segments")
def render_segment(resolve, project, timeline,
                   mark_in: int, mark_out: int,
                   output_dir: str = None,
                   resolution: str = "720p",
                   custom_name: str = None,
                   timeout: int = 120) -> SegmentRenderResult:
    """Render one timeline range to a video file: a batch of one."""
    batch = render_batch(
        resolve, project, timeline, [(mark_in, mark_out)],
        output_dir=output_dir, resolution=resolution,
        name_prefix=custom_name or f"qa_segment_{mark_in}_{mark_out}",
        timeout=timeout)
    return batch.segments[0]


def extract_frames(segment: SegmentRenderResult, frames,
                   output_dir: str = None) -> dict:
    """PNGs of timeline `frames` cut from one rendered segment, by ffmpeg.

    One decode of the segment for all of them. A frame outside the
    segment, or one ffmpeg did not write, is absent from the result -
    never a path to nothing.
    """
    wanted = sorted({int(f) for f in frames
                     if segment.mark_in <= int(f) <= segment.mark_out})
    if not segment.success or not wanted:
        return {}
    output_dir = output_dir or os.path.dirname(segment.path)
    stem = os.path.splitext(os.path.basename(segment.path))[0]
    pattern = os.path.join(output_dir, f"{stem}_f%04d.png")
    select = "+".join(f"eq(n\\,{f - segment.mark_in})" for f in wanted)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", segment.path,
             "-vf", f"select={select}", "-fps_mode", "passthrough",
             pattern],
            capture_output=True, timeout=60, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {}
    out = {}
    # The select filter emits in decode order, so output k is wanted[k].
    for k, frame in enumerate(wanted, start=1):
        path = pattern % k
        if os.path.exists(path) and os.path.getsize(path) > 0:
            out[frame] = path
    return out


def trim_to_range(segment: SegmentRenderResult, mark_in: int, mark_out: int,
                  output_path: str) -> Optional[str]:
    """The sub-range `mark_in`-`mark_out` of a rendered segment, as its own file."""
    if not segment.success:
        return None
    if (mark_in, mark_out) == (segment.mark_in, segment.mark_out):
        return segment.path
    start, end = mark_in - segment.mark_in, mark_out - segment.mark_in + 1
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", segment.path, "-an",
             "-vf", f"trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS",
             output_path],
            capture_output=True, timeout=60, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return output_path if os.path.exists(output_path) else None


@under_lease("render segments")
def render_frames(resolve, project, timeline, frames,
                  output_dir: str = None,
                  resolution: str = "full",
                  max_gap_frames: Optional[int] = None) -> dict:
    """PNGs of many timeline frames from ONE batch render.

    Returns `{frame: png_path}`; a frame that did not render is absent.
    The rendered video files are removed once the frames are cut.
    """
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="vqa_frame_")
    frames = sorted({int(f) for f in frames})
    batch = render_batch(resolve, project, timeline,
                         [(f, f) for f in frames], output_dir=output_dir,
                         resolution=resolution, max_gap_frames=max_gap_frames,
                         name_prefix="frames")
    out = {}
    for seg in batch.segments:
        out.update(extract_frames(seg, frames, output_dir))
        _cleanup(seg.path)
    return out


def render_single_frame(resolve, project, timeline,
                        frame: int,
                        output_dir: str = None,
                        resolution: str = "full") -> Optional[str]:
    """Render a single timeline frame to a PNG; None on failure."""
    return render_frames(resolve, project, timeline, [frame],
                         output_dir=output_dir,
                         resolution=resolution).get(int(frame))


def cleanup_segment(path: str) -> None:
    """Remove a rendered segment file (idempotent)."""
    _cleanup(path)


def _find_rendered_file(output_dir: str, name_prefix: str) -> Optional[str]:
    """Find the rendered file by prefix, ignoring XML sidecar files."""
    for f in os.listdir(output_dir):
        if f.startswith(name_prefix) and not f.endswith(".xml"):
            return os.path.join(output_dir, f)
    return None


def _cleanup(path: str) -> None:
    """Silently remove a file."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
