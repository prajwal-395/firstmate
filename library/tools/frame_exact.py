"""Frame-exact temporal addressing, and the refinement that earns it.

A boundary in this engine used to be a number of seconds.  Seconds are
the wrong unit for a cut: a 30 fps source's frame is 33.3 ms, and a
boundary that is only known to the nearest tenth of a second is three
frames away from the truth before anything is measured.  The M2 sample
that carries those seconds is a 2 Hz I-frame sample, so its gaps are
3-6 s - 90-190 frames of unobserved time - and the frame a reader
wanted was not in it.

This module owns three things:

* **The frame grid of a source** - :class:`SourceTimebase`: the exact
  rational rate the container declares, the container's own time base,
  where frame 0 sits, and how many frames there are.  The rational
  rate is read through :mod:`library.tools.source_clocks`, the ONE
  reading of a measured rate (PR 3037), and the frame arithmetic is
  integer arithmetic on container TICKS, so no boundary is ever
  rounded through a float.

* **A dense sample of an interval** - :func:`sample_interval` decodes
  a chosen interval at the source's own frame cadence and reports every
  frame with its SOURCE FRAME INDEX.  This is the zoom-in the plan
  asks for (C-06): the coarse 2 Hz sample says WHERE to look, and this
  says WHICH FRAME.

* **The refinement** - :func:`refine_boundary` runs a measured
  predicate over that dense sample and names the boundary frame, with
  the uncertainty it actually has (one frame) and the cost it actually
  paid.

Two rules this module keeps:

* **A boundary is a frame index, or it is nothing.**  A boundary that
  cannot be expressed as a source frame index is reported as absent,
  never as a rounded second that reads like a measurement.
* **The cost is measured and reported.**  A refinement that costs ten
  times the coarse sample is a worse engine, not a better one, so the
  decode and the measurement are timed and travel with the answer.

The coarse lane's predicate is injected, not invented here: this
module re-measures at frame cadence what the coarse lane measured at
2 Hz, and it says which rule turned a measured series into a boundary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from library.tools import source_clocks

SAMPLE_WIDTH = 384
"""The width a dense sample is decoded at - M2's own thumbnail width.

The dense sample re-measures what the coarse lane measured, so it is
decoded at the SAME width the coarse measurement used: a different
width is a different measurement, and a boundary found at another
scale is not the boundary the coarse lane was talking about.
"""

SEEK_MARGIN_SECONDS = 1.0
"""How far before the interval the decode seeks.

The seek has to land BEFORE the first wanted frame, or the frame that
carries the boundary is the one the seek threw away.  A second is far
more than any GOP this engine has met.
"""

READ_MARGIN_SECONDS = 0.5
"""How far past the interval the decode is allowed to READ.

The dense sample is an INTERVAL, and a decode that keeps reading after
the interval ends pays for every frame after it: on the 4K MXF corpus
a 2-second sample read to the end of the file and cost 25 minutes of
wall time for 48 frames.  The read is therefore bounded just past the
interval, and the frames past the end are dropped by the same
``select`` that keeps the wanted ones.

The bound is half a second past the end, which is more than one frame
period at every rate this engine has met, so the LAST wanted frame is
always read.  Measured on the M0 fixture: bounding the read changed
nothing about the frames produced (43 frames, byte-identical, same
timestamps) and cut the decode from 2.13 s to 1.44 s; on the 4K MXF
corpus the same bound took the same sample from 1509 s to 1.4 s.
"""

BOUNDARY_SLACK_SECONDS = 0.001
"""The slack on the interval's edges, in seconds.

A frame that starts ON the interval's edge belongs to it, and the
filter's bounds are written to six decimals - so without a millisecond
of slack the edge frame is lost to the rounding, and the sample
reports one frame fewer than the interval holds.
"""

_PTS_TIME = re.compile(r"pts_time:([0-9.]+)")


def _child_cpu_seconds() -> float:
    """The CPU seconds this process's children have burned so far.

    The wall clock on a shared machine measures the neighbours as much
    as the work, so a cost that has to be COMPARED across runs is
    measured in CPU seconds: the decode's and the measurement's own
    work, with the contention left out.
    """
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    return usage.ru_utime + usage.ru_stime


class FrameExactError(RuntimeError):
    """A refusal with a named reason. Never a silent empty result."""


# ── The frame grid ────────────────────────────────────────────────────


@dataclass(frozen=True)
class SourceTimebase:
    """One source's own frame grid, in exact arithmetic.

    ``fps_num``/``fps_den`` is the container's measured rate as the
    exact rational it is.  ``time_base_num``/``time_base_den`` is the
    container's own time base - the tick the container counts in -
    and every conversion here goes through integer TICKS, because a
    float second cannot hold a frame boundary exactly.

    ``start_time`` is where frame 0 sits on the source's timeline, and
    ``total_frames`` is the container's own frame count when it
    declares one.

    ``rate_basis`` names where the rate was read from
    (``avg_frame_rate``, ``r_frame_rate``), so a reader can tell a
    measured rate from a declared one.
    """

    fps_num: int
    fps_den: int
    time_base_num: int = 1
    time_base_den: int = 1000
    start_time: float = 0.0
    total_frames: int | None = None
    rate_basis: str = "avg_frame_rate"

    def __post_init__(self) -> None:
        if self.fps_num <= 0 or self.fps_den <= 0:
            raise ValueError("frame rate must be a positive rational")
        if self.time_base_num <= 0 or self.time_base_den <= 0:
            raise ValueError("time base must be a positive rational")

    @property
    def fps(self) -> Fraction:
        """The exact rate."""
        return Fraction(self.fps_num, self.fps_den)

    @property
    def ticks_per_second(self) -> Fraction:
        """How many container ticks one second holds."""
        return Fraction(self.time_base_den, self.time_base_num)

    @property
    def frame_duration_seconds(self) -> Fraction:
        """How long one frame lasts, exactly."""
        return 1 / self.fps

    def start_ticks(self) -> int:
        """The tick frame 0 starts on."""
        return round(Fraction(self.start_time) * self.ticks_per_second)

    def ticks_of(self, seconds: float) -> int:
        """A time in seconds as a container tick."""
        return round(Fraction(seconds) * self.ticks_per_second)

    def frame_of_ticks(self, ticks: int) -> int:
        """The frame a tick falls in (floor, never rounded)."""
        exact = ((ticks - self.start_ticks()) * self.fps
                 / self.ticks_per_second)
        return exact.numerator // exact.denominator

    def frame_at(self, seconds: float) -> int:
        """The frame whose span contains a time."""
        return self.frame_of_ticks(self.ticks_of(seconds))

    def frame_at_or_after(self, seconds: float) -> int:
        """The first frame that STARTS at or after a time.

        The frame CONTAINING a time starts before it, so an interval
        that begins mid-frame holds one fewer frame than the same
        interval beginning on a frame edge.
        """
        exact = ((self.ticks_of(seconds) - self.start_ticks()) * self.fps
                 / self.ticks_per_second)
        return -(-exact.numerator // exact.denominator)

    def on_grid(self, seconds: float) -> bool:
        """Whether a measured time sits exactly on this grid.

        A time that does NOT sit on the grid means the source's frames
        are not evenly spaced - a variable frame rate - and an index
        computed from the average rate is then an APPROXIMATION.  The
        sample says so instead of claiming an exact frame.
        """
        exact = ((self.ticks_of(seconds) - self.start_ticks()) * self.fps
                 / self.ticks_per_second)
        return exact.denominator == 1

    def seconds_at(self, frame: int) -> float:
        """The time a frame starts at."""
        ticks = (Fraction(self.start_ticks())
                 + Fraction(frame) * self.ticks_per_second / self.fps)
        return float(ticks / self.ticks_per_second)

    def to_dict(self) -> dict:
        return {"fps_num": self.fps_num, "fps_den": self.fps_den,
                "time_base_num": self.time_base_num,
                "time_base_den": self.time_base_den,
                "start_time": self.start_time,
                "total_frames": self.total_frames,
                "rate_basis": self.rate_basis}

    @staticmethod
    def from_dict(record: dict) -> SourceTimebase:
        return SourceTimebase(
            fps_num=int(record["fps_num"]),
            fps_den=int(record["fps_den"]),
            time_base_num=int(record.get("time_base_num", 1)),
            time_base_den=int(record.get("time_base_den", 1000)),
            start_time=float(record.get("start_time", 0.0)),
            total_frames=(None if record.get("total_frames") is None
                          else int(record["total_frames"])),
            rate_basis=str(record.get("rate_basis", "avg_frame_rate")))


def _ratio(value: Any, default: tuple[int, int]) -> tuple[int, int]:
    """A ``"num/den"`` string as a pair of ints."""
    if not value:
        return default
    try:
        num, den = str(value).split("/")
        numerator, denominator = int(num), int(den)
    except (TypeError, ValueError):
        return default
    if numerator <= 0 or denominator <= 0:
        return default
    return numerator, denominator


def _container_rate(value: Any) -> Fraction | None:
    """A container's declared rate as the exact rational it is.

    A container declares ``"30000/1001"`` - a string, and an EXACT
    rational.  Reading it through a float first would throw away the
    fraction that makes it exact, so the string is parsed as a
    rational and only a value that is already a number goes through
    `source_clocks.rational_frame_rate`, the one reading of a
    measured float rate.
    """
    if value is None:
        return None
    if isinstance(value, str) and "/" in value:
        num, den = _ratio(value, (0, 0))
        if num and den:
            return Fraction(num, den)
        return None
    return source_clocks.rational_frame_rate(value)


def timebase_from_stream(stream: dict) -> SourceTimebase:
    """The frame grid of one ffprobe video stream.

    The rate is read through `source_clocks.rational_frame_rate` - the
    one reading of a measured rate - and the container's own time base
    is kept, so a frame boundary is a tick and never a float.

    A stream with no measurable rate is REFUSED: a frame grid that
    guesses a rate is the silent-30-fps defect this engine does not
    ship.
    """
    rate = _container_rate(stream.get("avg_frame_rate"))
    basis = "avg_frame_rate"
    if rate is None:
        rate = _container_rate(stream.get("r_frame_rate"))
        basis = "r_frame_rate"
    if rate is None:
        raise FrameExactError(
            "no measured frame rate for this source: the container "
            "declares neither avg_frame_rate nor r_frame_rate")
    time_base = _ratio(stream.get("time_base"), (1, 1000))
    total = stream.get("nb_frames")
    try:
        total_frames = int(total) if total is not None else None
    except (TypeError, ValueError):
        total_frames = None
    try:
        start_time = float(stream.get("start_time") or 0.0)
    except (TypeError, ValueError):
        start_time = 0.0
    return SourceTimebase(fps_num=rate.numerator, fps_den=rate.denominator,
                          time_base_num=time_base[0],
                          time_base_den=time_base[1],
                          start_time=start_time,
                          total_frames=total_frames,
                          rate_basis=basis)


def probe_stream(source_file: str) -> dict:
    """The video stream of a media file, as ffprobe reports it."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
             "stream=avg_frame_rate,r_frame_rate,time_base,start_time,nb_frames",
             "-of", "json", source_file],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=120, check=False)
    except FileNotFoundError as exc:
        raise FrameExactError("ffprobe not found") from exc
    if out.returncode != 0:
        raise FrameExactError(
            f"ffprobe failed for {source_file}: {(out.stderr or '')[:200]}")
    try:
        streams = (json.loads(out.stdout or "") or {}).get("streams") or []
    except ValueError as exc:
        raise FrameExactError(
            f"ffprobe wrote unreadable JSON for {source_file}") from exc
    if not streams:
        raise FrameExactError(f"{source_file} carries no video stream")
    return streams[0]


def probe_timebase(source_file: str) -> SourceTimebase:
    """The frame grid of a media file, measured with ffprobe."""
    return timebase_from_stream(probe_stream(source_file))


# ── The dense sample ──────────────────────────────────────────────────


@dataclass(frozen=True)
class SampledFrame:
    """One frame of a dense sample, with its source frame index."""

    frame_index: int
    pts_seconds: float
    file: str
    on_grid: bool

    def to_dict(self) -> dict:
        return {"frame_index": self.frame_index,
                "pts_seconds": round(self.pts_seconds, 6),
                "file": self.file,
                "on_grid": self.on_grid}


def _select_expression(start: float, end: float, width: int) -> str:
    """The ffmpeg filter that keeps exactly the wanted frames."""
    return (f"select='between(t\\,{start - BOUNDARY_SLACK_SECONDS:.6f}\\,"
            f"{end + BOUNDARY_SLACK_SECONDS:.6f})',"
            f"scale={width}:-2,showinfo")


def sample_interval(source_file: str, timebase: SourceTimebase,
                    start_seconds: float, end_seconds: float,
                    out_dir: str | os.PathLike,
                    *, width: int = SAMPLE_WIDTH,
                    use_hwaccel: bool = True) -> dict:
    """Decode an interval at the source's own frame cadence.

    Every frame of the interval is decoded - no frame is skipped, so
    the interval is covered at frame cadence and not at the coarse
    lane's 2 Hz.  Each frame is reported with its SOURCE FRAME INDEX,
    read off the container's own ticks.

    The report carries the cost: the decode seconds, the frames
    decoded, and the frames the interval SHOULD have held, so a
    caller can see a source whose frames are not evenly spaced instead
    of reading a smooth series that was never measured.

    Returns a dict with ``frames`` (a list of :class:`SampledFrame`
    records), ``timebase``, ``cost`` and ``coverage``.
    """
    if end_seconds <= start_seconds:
        raise FrameExactError(
            "the refinement interval must be a positive span of time")
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    pattern = str(out / "frame_%06d.jpg")

    seek = max(0.0, start_seconds - SEEK_MARGIN_SECONDS)
    # The read is bounded: an interval sample must not pay for the rest
    # of the file.  `-to` is an INPUT option here, so it bounds the
    # demux; the `select` below still drops whatever falls outside the
    # interval, and the margin keeps the last wanted frame inside the
    # bound.
    read_until = end_seconds + BOUNDARY_SLACK_SECONDS + READ_MARGIN_SECONDS
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-y", "-copyts"]
    if use_hwaccel:
        cmd += ["-hwaccel", "videotoolbox"]
    cmd += ["-ss", f"{seek:.6f}", "-to", f"{read_until:.6f}",
            "-i", source_file, "-an", "-sn",
            "-vf", _select_expression(start_seconds, end_seconds, width),
            "-fps_mode", "passthrough", "-q:v", "3", pattern]

    from library.tools import perf_ledger
    started = time.perf_counter()
    cpu_before = _child_cpu_seconds()
    with perf_ledger.media_decode(
            source_file, purpose="frame_exact.dense_sample",
            media_kind="video", access_pattern="random",
            calls=1, backend="ffmpeg",
            requested_hwaccel=("videotoolbox" if use_hwaccel else None),
            target_width=width) as measurement:
        proc = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                              errors="replace", timeout=1800, check=False)
        measurement["frame_count"] = 0
    decode_seconds = round(time.perf_counter() - started, 3)
    decode_cpu = round(_child_cpu_seconds() - cpu_before, 3)
    if proc.returncode != 0:
        raise FrameExactError(
            f"the dense sample failed for {source_file}: "
            f"{(proc.stderr or '')[-300:]}")

    times = [float(m) for m in _PTS_TIME.findall(proc.stderr or "")]
    files = sorted(out.glob("frame_*.jpg"))
    if len(times) != len(files):
        raise FrameExactError(
            f"the dense sample lost frames for {source_file}: "
            f"{len(files)} images, {len(times)} timestamps")

    frames = []
    for path, pts in zip(files, times):
        frames.append(SampledFrame(
            frame_index=timebase.frame_at(pts),
            pts_seconds=pts,
            file=str(path),
            on_grid=timebase.on_grid(pts)))

    expected = (timebase.frame_at(end_seconds + BOUNDARY_SLACK_SECONDS)
                - timebase.frame_at_or_after(
                    start_seconds - BOUNDARY_SLACK_SECONDS) + 1)
    return {
        "source_file": os.path.abspath(source_file),
        "timebase": timebase.to_dict(),
        "frames": [f.to_dict() for f in frames],
        "coverage": {
            "start_seconds": start_seconds,
            "end_seconds": end_seconds,
            "frames_decoded": len(frames),
            "frames_expected": expected,
            "first_frame_index": frames[0].frame_index if frames else None,
            "last_frame_index": frames[-1].frame_index if frames else None,
            "all_on_grid": all(f.on_grid for f in frames) if frames else None,
        },
        "cost": {"decode_seconds": decode_seconds,
                 "decode_cpu_seconds": decode_cpu,
                 "frames_decoded": len(frames)},
    }


# ── Measuring the dense sample ────────────────────────────────────────


def measure_frames(frame_paths: Sequence[str]) -> list[dict]:
    """Measure a dense sample the way M3 measures the coarse one.

    The same Apple Vision helper, the same face/hand records, so a
    boundary found here is the boundary the coarse lane was talking
    about - only measured at frame cadence.
    """
    from library.tools import vision_measure
    helper, reason = vision_measure.ensure_helper()
    if helper is None:
        raise FrameExactError(f"Vision helper unavailable: {reason}")
    docs = vision_measure.measure_frames(list(frame_paths), helper)
    from library.tools import person_measurements
    measured = []
    for doc in docs:
        keep = [True] * len(person_measurements._boxed_faces(doc))
        record = person_measurements.frame_record(doc, keep, 0.0)
        # The pixel size travels with the record: a distance between
        # two normalised points is only a distance once the frame's
        # size is known (`person_measurements` says so).
        record["pixels"] = doc.get("pixels")
        measured.append(record)
    return measured


def hand_mouth_distances(measured: Sequence[dict]) -> list[float | None]:
    """The hand-to-lips distance of every measured frame.

    The coarse lane's own geometry (`event_spans.hand_mouth_distance`):
    the closest confident hand joint to an outer-lip point, in face
    widths.  It is re-used rather than re-invented, so a refined
    boundary and a coarse candidate are the same measurement.
    """
    from library.tools import event_spans
    out: list[float | None] = []
    for record in measured:
        width, height = (record.get("pixels")
                         or (SAMPLE_WIDTH, SAMPLE_WIDTH * 9 // 16))
        width, height = float(width), float(height)
        face = next((f for f in record.get("faces") or []
                     if f.get("outer_lips")), None)
        if face is None:
            out.append(None)
            continue
        out.append(event_spans.hand_mouth_distance(
            face, record.get("hands") or [], width, height))
    return out


# ── The refinement ────────────────────────────────────────────────────


BOUNDARY_RULES = ("closest_approach", "first_hit", "arrival")
"""How a measured series becomes a boundary.

``closest_approach`` names the frame where the measured quantity is
extreme - the peak of a gesture ("the device is AT the mouth here").
``first_hit`` names the first frame that satisfies a predicate.
``arrival`` names the first frame at which the hand is AT the lips
(:data:`CONTACT_DISTANCE_FACE_WIDTHS`).

The three answer different questions, and for a HELD gesture they do
not answer the same one: a gesture that is held for five seconds has a
peak somewhere inside the hold and an arrival at its edge, and a cut
that waits for the peak lands seconds after the moment the gesture
arrived.  The M0 scenario wants the arrival.
"""

CONTACT_DISTANCE_FACE_WIDTHS = 0.25
"""The hand-to-lips distance at which the hand is AT the lips.

The coarse lane's own candidate rule fires at
``library.tools.event_spans.HAND_CANDIDATE_MAX_DISTANCE`` (1.5 face
widths), which answers "is the hand NEAR the mouth": on the M0 fixture
that question was true on 62 of the 89 coarse frames of a take whose
speaker holds the car keys near his face throughout, so it cannot name
an arrival either.  The question a boundary needs is the tight one -
the hand is AT the lips - and this is that distance.

Measured 2026-10-21 on the M0 fixture (IMG_1822, 30 fps, whole take):
the frames in which the hand holds the car keys against the lips
measure 0.02-0.05 face widths and the frames of the approach measure
0.13-0.43, so 0.25 separates them.  It is measured on ONE take by ONE
speaker and is not calibrated on the D-01 corpus; it is DECLARED here
so the number that decides a boundary is visible rather than implicit,
and ``--contact-distance`` moves it.
"""


def _closest_approach(series: Sequence[float | None]
                      ) -> int | None:
    """The index of the extreme of a measured series."""
    measured = [(i, v) for i, v in enumerate(series) if v is not None]
    if not measured:
        return None
    return min(measured, key=lambda row: row[1])[0]


def _arrival(series: Sequence[float | None],
             contact_distance: float) -> int | None:
    """The index of the first frame AT the lips.

    The first frame whose measured distance is at or below the contact
    distance - the frame the gesture ARRIVED, which for a held gesture
    is not the frame it is deepest at.
    """
    for index, value in enumerate(series):
        if value is not None and value <= contact_distance:
            return index
    return None


def refine_boundary(source_file: str, timebase: SourceTimebase,
                    start_seconds: float, end_seconds: float,
                    out_dir: str | os.PathLike, *,
                    rule: str = "closest_approach",
                    series_fn: Callable[[Sequence[dict]], Sequence[float | None]] | None = None,
                    predicate_fn: Callable[[dict], bool] | None = None,
                    width: int = SAMPLE_WIDTH,
                    contact_distance: float = CONTACT_DISTANCE_FACE_WIDTHS
                    ) -> dict:
    """Narrow a broad interval to a frame-exact boundary.

    The interval is re-sampled at the source's own frame cadence and
    the measured series over it is turned into ONE frame index, with
    the uncertainty that frame carries (one frame) and the cost the
    refinement paid.

    The series is measured, not asserted: `series_fn` returns the
    measured value per frame (the coarse lane's own geometry by
    default - the hand-to-lips distance), and `predicate_fn` returns
    whether a frame satisfies the coarse lane's predicate.  A
    refinement with no measurable series is REFUSED, not reported as a
    boundary.
    """
    if rule not in BOUNDARY_RULES:
        raise FrameExactError(
            f"unknown boundary rule {rule!r}; expected one of "
            f"{BOUNDARY_RULES}")
    sample = sample_interval(source_file, timebase, start_seconds,
                              end_seconds, out_dir, width=width)
    frames = [SampledFrame(**f) for f in sample["frames"]]
    if not frames:
        raise FrameExactError(
            "the refinement interval decoded no frames: the boundary "
            "cannot be named")

    started = time.perf_counter()
    cpu_before = _child_cpu_seconds()
    measured = measure_frames([f.file for f in frames])
    measure_seconds = round(time.perf_counter() - started, 3)
    measure_cpu = round(_child_cpu_seconds() - cpu_before, 3)

    if series_fn is None:
        series_fn = hand_mouth_distances
    series = list(series_fn(measured))

    if rule == "closest_approach":
        position = _closest_approach(series)
    elif rule == "arrival":
        position = _arrival(series, contact_distance)
    else:
        if predicate_fn is None:
            raise FrameExactError(
                "the first_hit rule needs the predicate that defines a hit")
        hits = [bool(predicate_fn(record)) for record in measured]
        position = next((i for i, hit in enumerate(hits) if hit), None)

    report = {
        "source_file": os.path.abspath(source_file),
        "timebase": sample["timebase"],
        "interval": {"start_seconds": start_seconds,
                     "end_seconds": end_seconds},
        "rule": rule,
        "series": [None if v is None else round(float(v), 4) for v in series],
        "contact_distance_face_widths": (contact_distance
                                         if rule == "arrival" else None),
        "coverage": sample["coverage"],
        "cost": {"decode_seconds": sample["cost"]["decode_seconds"],
                 "decode_cpu_seconds": sample["cost"].get(
                     "decode_cpu_seconds"),
                 "measure_seconds": measure_seconds,
                 "measure_cpu_seconds": measure_cpu,
                 "total_seconds": round(
                     sample["cost"]["decode_seconds"] + measure_seconds, 3),
                 "total_cpu_seconds": round(
                     (sample["cost"].get("decode_cpu_seconds") or 0.0)
                     + measure_cpu, 3),
                 "frames_decoded": len(frames)},
    }
    if position is None:
        report["boundary"] = None
        report["status"] = "no_boundary_measured"
        report["reason"] = (
            "no frame in the interval carried a measurement, so no "
            "boundary frame exists to name")
        return report

    chosen = frames[position]
    truncated = position in (0, len(frames) - 1)
    report["status"] = ("boundary_truncated" if truncated
                        else "boundary_measured")
    report["boundary"] = {
        "frame_index": chosen.frame_index,
        "seconds": round(chosen.pts_seconds, 6),
        "uncertainty_seconds": round(
            float(timebase.frame_duration_seconds), 6),
        "basis": ("the measured series' extreme in the refined interval"
                  if rule == "closest_approach"
                  else ("the first frame at which the hand is at the lips"
                        if rule == "arrival"
                        else "the first frame satisfying the predicate")),
        "on_grid": chosen.on_grid,
    }
    if truncated:
        report["reason"] = (
            "the measured extreme is at the edge of the interval, so the "
            "interval may have cut it off: widen the interval before "
            "trusting this frame as the boundary")
    return report


def coarse_frame_index(m2_document: dict, seconds: float) -> int | None:
    """The source frame index of the COARSE sample nearest a time.

    A coarse candidate (an event span, a measured moment) names a
    time; the coarse sample is the map from times to frames, and this
    is the crossing point: the candidate's own coarse frame, which the
    refinement then makes exact.  None when the sample carries no
    index - a source whose rate was never measured.
    """
    frames = (m2_document or {}).get("frames") or []
    best = None
    for row in frames:
        index = row.get("frame_index")
        if index is None:
            continue
        distance = abs(float(row.get("t", 0.0)) - float(seconds))
        if best is None or distance < best[0]:
            best = (distance, int(index))
    return best[1] if best is not None else None


def refine_from_frame_index(source_file: str, timebase: SourceTimebase,
                            coarse_frame_index: int, out_dir: str | os.PathLike,
                            *, half_window_seconds: float = 0.5,
                            rule: str = "closest_approach",
                            width: int = SAMPLE_WIDTH,
                            contact_distance: float = CONTACT_DISTANCE_FACE_WIDTHS
                            ) -> dict:
    """Refine a boundary around a COARSE FRAME INDEX.

    The coarse sample (M2) carries the source frame index of every
    frame it took, so the caller that knows "the event is near frame
    997" can ask for the exact frame without converting a time and
    hoping.  The window is the coarse frame plus or minus
    `half_window_seconds`, and it must CONTAIN the true boundary: a
    window that does not is reported as truncating the answer, never
    as a boundary.
    """
    if half_window_seconds <= 0:
        raise FrameExactError("the refinement window must be a positive span")
    centre = timebase.seconds_at(coarse_frame_index)
    report = refine_boundary(source_file, timebase,
                             centre - half_window_seconds,
                             centre + half_window_seconds,
                             out_dir, rule=rule, width=width,
                             contact_distance=contact_distance)
    report["coarse_frame_index"] = coarse_frame_index
    report["coarse_seconds"] = round(centre, 6)
    return report


# ── The command line ──────────────────────────────────────────────────


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="frame_exact",
        description="Frame-exact addressing: a source frame index, and "
                    "the refinement that narrows a broad interval to one.")
    sub = parser.add_subparsers(dest="command")
    p_sample = sub.add_parser("sample",
                               help="a dense sample of an interval")
    p_sample.add_argument("source")
    p_sample.add_argument("--from", dest="start", type=float, required=True)
    p_sample.add_argument("--to", dest="end", type=float, required=True)
    p_sample.add_argument("--out", required=True)
    p_sample.add_argument("--width", type=int, default=SAMPLE_WIDTH)
    p_refine = sub.add_parser("refine",
                               help="a frame-exact boundary for an interval")
    p_refine.add_argument("source")
    p_refine.add_argument("--from", dest="start", type=float, default=None)
    p_refine.add_argument("--to", dest="end", type=float, default=None)
    p_refine.add_argument("--out", required=True)
    p_refine.add_argument("--rule", choices=BOUNDARY_RULES,
                          default="closest_approach")
    p_refine.add_argument(
        "--contact-distance", type=float,
        default=CONTACT_DISTANCE_FACE_WIDTHS,
        help=("the hand-to-lips distance at which the hand is AT the lips; "
              "the `arrival` rule uses it to name the frame a held "
              "gesture arrived on"))
    p_refine.add_argument("--width", type=int, default=SAMPLE_WIDTH)
    p_refine.add_argument("--frame-index", type=int, default=None,
                          help="a coarse source frame index to refine "
                               "around (instead of --from/--to)")
    p_refine.add_argument("--half-window", type=float, default=0.5,
                          help="the window around --frame-index, in seconds")
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    timebase = probe_timebase(args.source)
    if args.command == "sample":
        report = sample_interval(args.source, timebase, args.start,
                                 args.end, args.out, width=args.width)
    elif args.frame_index is not None:
        report = refine_from_frame_index(
            args.source, timebase, args.frame_index, args.out,
            half_window_seconds=args.half_window, rule=args.rule,
            width=args.width, contact_distance=args.contact_distance)
    elif args.start is None or args.end is None:
        parser.error("refine needs --from/--to or --frame-index")
        return 2
    else:
        report = refine_boundary(args.source, timebase, args.start, args.end,
                                 args.out, rule=args.rule, width=args.width,
                                 contact_distance=args.contact_distance)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
