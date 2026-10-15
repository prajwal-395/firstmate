"""Reference style measurement.

Given a reference video, measure the style facts an edit can be
constrained by: the shot-length distribution, the pacing, the
transition vocabulary, the palette, the contrast, the caption and type
style, and the camera-movement distribution.  This is the
CONTENT-vs-STYLE separation the captain's spec section 6 names - the
reference's WORDS are content and are never read here; only how it is
cut, how it looks, how its text is set and how much motion it carries
are measured.

Everything here is a FACT (:data:`library.tools.editorial_types.FACT`):
a measurement of the reference's pixels and cut points, never a
judgment about the project's own footage.  The values are data the
editorial track reads - the pacing slot's measured rung, the
transition planner's vocabulary - not taste the engine invents
(AGENTS.md 10.5).

Cut detection uses per-frame luma and saturation jumps (ffmpeg
``signalstats``), NOT the scene filter the temporal index uses.  The
scene filter is luma-weighted and misses cuts between shots of
similar luma: measured on a red->green solid-color cut, the scene
filter scored it below 0.001 (undetectable) while the saturation
jump was 59.  A style measurement that misses a third of the cuts
reports a shot-length distribution that is not the reference's.

The cut threshold is derived from the video's OWN frame-delta
distribution at measure time, not fixed: the median within-shot delta
is the noise floor, and a cut is a jump far above it.  The absolute
floors and margins below are the part of the threshold that is not
the data itself - the sensitivity of the measurement, recorded beside
every cut so a reader can see what fired.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

# ── Cut-detection sensitivity ────────────────────────────────────────
#
# A cut is a frame-to-frame jump in luma or saturation.  The jump is
# compared against a threshold derived from the video's own delta
# distribution (see `_cut_threshold`); these constants are the part
# that is NOT the data: the absolute floor for a noise-free video and
# the margin above the noise floor.  They are the measurement's
# sensitivity, the same kind of parameter the scene filter's 0.3 is -
# the difference is that the data-derived part adapts to the video.
_LUMA_CUT_FLOOR = 8.0
_SATURATION_CUT_FLOOR = 15.0
_LUMA_EVENT_FLOOR = 3.0
_SATURATION_EVENT_FLOOR = 5.0
_CUT_MARGIN = 20.0
_EVENT_MARGIN = 5.0

# ── Transition classification ────────────────────────────────────────
#
# Change events closer than this are one transition, not two.  A
# gradual transition (dissolve, fade) spans at most this many seconds;
# a cluster of change events longer than this is a camera move within
# one shot, not a transition between shots.
_TRANSITION_CLUSTER_SECONDS = 0.25
_GRADUAL_TRANSITION_MAX_SECONDS = 2.0

# The shot-length histogram bins, in seconds.  Presentation bins the
# way a ruler's marks are - they do not decide what a shot length is,
# they only group what was measured.
_HISTOGRAM_BINS = ((0.0, 1.0), (1.0, 2.0), (2.0, 4.0), (4.0, 8.0),
                   (8.0, float("inf")))


def _run_ffmpeg_stats(video_path: str) -> str:
    """Per-frame luma and saturation, as ffmpeg's metadata text."""
    result = subprocess.run(
        ["ffmpeg", "-i", video_path, "-vf", "signalstats,metadata=print",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )
    return result.stderr


def _probe_duration(video_path: str) -> float:
    """The reference's duration in seconds, or 0.0 when unreadable."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def _frame_deltas(stats_text: str) -> list[dict]:
    """Per-frame (luma, saturation) and the frame-to-frame jumps.

    Parses ffmpeg's ``signalstats,metadata=print`` output - one
    ``YAVG``/``SATAVG`` pair per frame - and returns, for each frame
    after the first, the absolute jump in each channel from the
    previous frame.  A cut is a jump, so the jump is the unit.
    """
    frames: list[dict] = []
    current: dict = {}
    for line in stats_text.splitlines():
        luma = re.search(r"lavfi\.signalstats\.YAVG=([\d.]+)", line)
        if luma:
            if current:
                frames.append(current)
            current = {"luma": float(luma.group(1))}
            continue
        sat = re.search(r"lavfi\.signalstats\.SATAVG=([\d.]+)", line)
        if sat and current is not None:
            current["saturation"] = float(sat.group(1))
    if current:
        frames.append(current)

    deltas: list[dict] = []
    for index in range(1, len(frames)):
        before, after = frames[index - 1], frames[index]
        if "luma" not in before or "luma" not in after:
            continue
        deltas.append({
            "frame": index,
            "luma_jump": abs(after["luma"] - before["luma"]),
            "saturation_jump": abs(after.get("saturation", 0.0)
                                   - before.get("saturation", 0.0)),
        })
    return deltas


def _cut_threshold(deltas: list[float], floor: float, margin: float) -> float:
    """A cut threshold derived from the video's own delta distribution.

    The median within-shot delta is the noise floor - most frames are
    inside a shot, so the median jump is the video's own noise.  A cut
    is a jump far above it.  The absolute floor handles a noise-free
    video where the median is zero.  The derived threshold is recorded
    beside every cut so a reader can see what fired.
    """
    if not deltas:
        return floor
    noise_floor = statistics.median(deltas)
    return max(floor, noise_floor * margin)


def _change_events(deltas: list[dict], luma_low: float,
                   saturation_low: float) -> list[dict]:
    """Frames whose jump exceeds the low threshold, with their time."""
    events = []
    for delta in deltas:
        if (delta["luma_jump"] > luma_low
                or delta["saturation_jump"] > saturation_low):
            events.append(delta)
    return events


def _cluster_events(events: list[dict], fps: float) -> list[list[dict]]:
    """Change events grouped into transitions by time proximity.

    Events closer than ``_TRANSITION_CLUSTER_SECONDS`` are one
    transition.  A hard cut is a single event; a dissolve is a run of
    them; a camera move is a long run the classification below keeps
    out of the vocabulary.
    """
    if not events:
        return []
    clusters: list[list[dict]] = [[events[0]]]
    for event in events[1:]:
        last = clusters[-1][-1]
        gap = (event["frame"] - last["frame"]) / fps if fps > 0 else 0.0
        if gap <= _TRANSITION_CLUSTER_SECONDS:
            clusters[-1].append(event)
        else:
            clusters.append([event])
    return clusters


def _classify_clusters(clusters: list[list[dict]], fps: float,
                       luma_high: float, saturation_high: float) -> list[dict]:
    """Each cluster as a hard cut, a gradual transition, or neither.

    A cluster with one event whose jump exceeds the high threshold is
    a hard cut.  A cluster of several events is a gradual transition
    (a dissolve or fade) when it spans less than
    ``_GRADUAL_TRANSITION_MAX_SECONDS`` - longer than that is a camera
    move within one shot, not a transition between shots.  A cluster
    whose every jump stays under the high threshold and spans too long
    to be a dissolve is a camera move and is kept out of the vocabulary.
    """
    classified = []
    for cluster in clusters:
        if not cluster:
            continue
        max_luma = max(e["luma_jump"] for e in cluster)
        max_saturation = max(e["saturation_jump"] for e in cluster)
        exceeds_high = (max_luma > luma_high
                        or max_saturation > saturation_high)
        span_seconds = ((cluster[-1]["frame"] - cluster[0]["frame"])
                        / fps if fps > 0 else 0.0)
        if len(cluster) == 1:
            if exceeds_high:
                classified.append({"type": "hard_cut", "cluster": cluster})
            continue
        if span_seconds > _GRADUAL_TRANSITION_MAX_SECONDS:
            continue
        if exceeds_high or len(cluster) >= 2:
            classified.append({"type": "gradual", "cluster": cluster})
    return classified


def measure_shot_boundaries(video_path: str) -> dict:
    """Cut points in the reference, with the thresholds that found them.

    Returns the boundaries (each a time in seconds with the jumps that
    fired), the transition classification, and the derived thresholds
    with their derivation recorded beside them.
    """
    stats_text = _run_ffmpeg_stats(video_path)
    deltas = _frame_deltas(stats_text)
    duration = _probe_duration(video_path)
    fps = _video_fps(video_path)

    luma_jumps = [d["luma_jump"] for d in deltas]
    saturation_jumps = [d["saturation_jump"] for d in deltas]
    luma_high = _cut_threshold(luma_jumps, _LUMA_CUT_FLOOR, _CUT_MARGIN)
    saturation_high = _cut_threshold(saturation_jumps,
                                     _SATURATION_CUT_FLOOR, _CUT_MARGIN)
    luma_low = _cut_threshold(luma_jumps, _LUMA_EVENT_FLOOR, _EVENT_MARGIN)
    saturation_low = _cut_threshold(saturation_jumps,
                                    _SATURATION_EVENT_FLOOR, _EVENT_MARGIN)

    events = _change_events(deltas, luma_low, saturation_low)
    clusters = _cluster_events(events, fps)
    classified = _classify_clusters(clusters, fps, luma_high,
                                     saturation_high)

    boundaries = []
    for item in classified:
        cluster = item["cluster"]
        first = cluster[0]
        boundaries.append({
            "time_seconds": round(first["frame"] / fps, 3) if fps > 0 else 0.0,
            "transition": item["type"],
            "luma_jump": round(max(e["luma_jump"] for e in cluster), 3),
            "saturation_jump": round(max(e["saturation_jump"]
                                         for e in cluster), 3),
        })

    return {
        "boundaries": boundaries,
        "thresholds": {
            "luma_cut": round(luma_high, 3),
            "saturation_cut": round(saturation_high, 3),
            "luma_noise_floor": round(statistics.median(luma_jumps), 3)
            if luma_jumps else 0.0,
            "saturation_noise_floor": round(statistics.median(saturation_jumps), 3)
            if saturation_jumps else 0.0,
            "derived_as": "max(floor, margin x median frame-to-frame jump) "
                          "from this video's own delta distribution",
        },
        "shot_motion": _shot_motion(deltas, boundaries, fps),
        "fps": fps,
        "duration_seconds": duration,
    }


def _shot_motion(deltas: list, boundaries: list, fps: float) -> list:
    """Mean frame-to-frame luma jump within each shot.

    A shot's motion energy is the mean luma jump between consecutive
    frames inside it.  This is not camera movement alone - a gesturing
    subject in front of a locked-off camera also raises it - but it is
    the reference's own motion distribution, and the editorial track
    reads it as a style fact.
    """
    if not deltas or fps <= 0:
        return []
    cut_frames = [int(b["time_seconds"] * fps) for b in boundaries]
    last_frame = deltas[-1]["frame"] + 1
    intervals = []
    start = 0
    for cut in cut_frames:
        if cut > start:
            intervals.append((start, cut))
        start = cut
    if start < last_frame:
        intervals.append((start, last_frame))
    motion = []
    for shot_start, shot_end in intervals:
        jumps = [d["luma_jump"] for d in deltas
                 if shot_start <= d["frame"] < shot_end]
        if jumps:
            motion.append({
                "start_frame": shot_start,
                "end_frame": shot_end,
                "mean_luma_jump": round(statistics.fmean(jumps), 3),
                "frames": len(jumps),
            })
    return motion


def _video_fps(video_path: str) -> float:
    """The reference's frame rate, or 0.0 when unreadable."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )
    try:
        num, _, den = result.stdout.strip().partition("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError, AttributeError):
        return 0.0


def shot_length_distribution(boundaries: list, duration: float) -> dict:
    """Shot-length statistics: the distribution, not just the mean.

    The mean alone cannot tell a piece cut to a uniform rhythm from
    one that mixes long and short shots at the same average - the
    coefficient of variation and the histogram are what a reader
    compares against another reference's shape.
    """
    times = [0.0] + [b["time_seconds"] for b in boundaries] + [duration]
    lengths = [round(times[i + 1] - times[i], 3)
               for i in range(len(times) - 1)]
    lengths = [length for length in lengths if length > 0]
    if not lengths:
        return {
            "shot_count": 0,
            "asl_seconds": 0.0,
            "median_seconds": 0.0,
            "p10_seconds": 0.0,
            "p90_seconds": 0.0,
            "min_seconds": 0.0,
            "max_seconds": 0.0,
            "std_dev_seconds": 0.0,
            "coefficient_of_variation": 0.0,
            "histogram": [],
        }
    ordered = sorted(lengths)
    mean = statistics.fmean(lengths)
    std_dev = statistics.pstdev(lengths) if len(lengths) > 1 else 0.0
    histogram = []
    for start, end in _HISTOGRAM_BINS:
        count = sum(1 for length in lengths if start <= length < end)
        histogram.append({
            "bin_start_seconds": start,
            "bin_end_seconds": (end if end != float("inf") else None),
            "count": count,
        })
    return {
        "shot_count": len(lengths),
        "asl_seconds": round(mean, 3),
        "median_seconds": round(statistics.median(lengths), 3),
        "p10_seconds": round(_percentile(ordered, 10), 3),
        "p90_seconds": round(_percentile(ordered, 90), 3),
        "min_seconds": round(min(lengths), 3),
        "max_seconds": round(max(lengths), 3),
        "std_dev_seconds": round(std_dev, 3),
        "coefficient_of_variation": round(std_dev / mean, 3) if mean else 0.0,
        "histogram": histogram,
    }


def _percentile(ordered: list, percent: float) -> float:
    """The value at `percent` of an ascending list, linearly interpolated."""
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = (percent / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    fraction = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * fraction


def transition_vocabulary(boundaries: list) -> dict:
    """How the reference transitions between shots, counted by type."""
    hard_cuts = sum(1 for b in boundaries if b["transition"] == "hard_cut")
    gradual = sum(1 for b in boundaries if b["transition"] == "gradual")
    total = hard_cuts + gradual
    return {
        "hard_cuts": hard_cuts,
        "gradual_transitions": gradual,
        "total_transitions": total,
        "gradual_share": round(gradual / total, 3) if total else 0.0,
    }


def pacing_profile(boundaries: list, duration: float) -> dict:
    """Cuts per minute and whether the piece accelerates.

    The first half's average shot length beside the second half's is
    the cheapest reading of whether the piece tightens as it goes - a
    style fact the pacing slot and the mesh spine can both read.
    """
    if duration <= 0:
        return {
            "cuts_per_minute": 0.0,
            "first_half_asl_seconds": 0.0,
            "second_half_asl_seconds": 0.0,
            "accelerates": False,
        }
    half = duration / 2.0
    first_lengths = []
    second_lengths = []
    times = [0.0] + [b["time_seconds"] for b in boundaries] + [duration]
    for i in range(len(times) - 1):
        start, end = times[i], times[i + 1]
        length = end - start
        if length <= 0:
            continue
        if start < half:
            first_lengths.append(length)
        else:
            second_lengths.append(length)
    first_asl = (statistics.fmean(first_lengths) if first_lengths else 0.0)
    second_asl = (statistics.fmean(second_lengths) if second_lengths else 0.0)
    return {
        "cuts_per_minute": round(len(boundaries) / duration * 60.0, 3),
        "first_half_asl_seconds": round(first_asl, 3),
        "second_half_asl_seconds": round(second_asl, 3),
        "accelerates": (second_asl > 0 and first_asl > 0
                         and second_asl < first_asl),
    }


def measure_reference_editorial_style(video_path: str) -> dict:
    """The editorial half of the Style Representation, as one dict.

    The shot-length distribution, the transition vocabulary and the
    pacing, measured from the reference's own pixels.  This is the
    data the editorial track reads; the graphics half (palette,
    contrast, caption and type style) is a separate slice.
    """
    measured = measure_shot_boundaries(video_path)
    boundaries = measured["boundaries"]
    duration = measured["duration_seconds"]
    return {
        "source": video_path,
        "duration_seconds": duration,
        "shot_length_distribution": shot_length_distribution(boundaries,
                                                            duration),
        "transition_vocabulary": transition_vocabulary(boundaries),
        "pacing": pacing_profile(boundaries, duration),
        "thresholds": measured["thresholds"],
    }


def measure_reference_asl(reference_path: str) -> float:
    """The reference's average shot length, for the pacing slot.

    The one editorial value a reference's pixels determine directly:
    how long the reference holds a shot.  The pacing slot reads this
    as its measured rung - the project declared a reference, and this
    is what measuring it says.
    """
    style = measure_reference_editorial_style(reference_path)
    return style["shot_length_distribution"]["asl_seconds"]


# ── The camera-movement half ─────────────────────────────────────────
#
# How much motion the reference's shots carry, as a distribution.  The
# signal is the mean frame-to-frame luma jump within each shot - the
# same delta the cut detector reads, averaged inside the shot instead
# of at its boundary.  It is not camera movement alone: a gesturing
# subject in front of a locked-off camera raises it exactly as a
# shaking camera does.  It is the reference's own motion distribution,
# and the editorial track reads it as a style fact - how much motion
# this reference carries, not what moves.


def measure_reference_camera_movement(video_path: str) -> dict:
    """The camera-movement half of the Style Representation, as one dict.

    The distribution of per-shot motion energies: the median, the mean,
    the quartiles and the range.  No threshold classifies a shot as
    static or moving - the distribution IS the measurement, and a
    reader compares the project's own shots against it.
    """
    measured = measure_shot_boundaries(video_path)
    motion = measured["shot_motion"]
    if not motion:
        return {
            "source": video_path,
            "shots": 0,
            "median_motion_energy": 0.0,
            "mean_motion_energy": 0.0,
            "min_motion_energy": 0.0,
            "max_motion_energy": 0.0,
        }
    energies = sorted(m["mean_luma_jump"] for m in motion)
    return {
        "source": video_path,
        "shots": len(energies),
        "median_motion_energy": round(statistics.median(energies), 3),
        "mean_motion_energy": round(statistics.fmean(energies), 3),
        "min_motion_energy": round(energies[0], 3),
        "max_motion_energy": round(energies[-1], 3),
    }


# ── The graphic-density half ─────────────────────────────────────────
#
# How many distinct visual elements the reference's frames carry, as a
# distribution.  The signal is the number of distinct colour regions per
# frame - a proxy for graphic density that counts overlays, text blocks
# and subject as separate elements without needing to classify them.
# A frame with a person, a caption and a logo carries more elements than
# a frame with a person alone.


def _frame_element_count(frame_path: str) -> int:
    """Distinct colour regions in one frame, as an element count.

    The frame's pixels are quantized to a small palette; each quantized
    colour covering more than `_MIN_ELEMENT_COVERAGE` of the frame is
    one element.  The count is a proxy - it cannot tell a graphic from
    a subject - but it is the reference's own visual density, and the
    graphics IR reads it as a style fact.  An unreadable frame is 0:
    it was never decoded, so there is no measurement to report.

    The resize is NEAREST, not the default bilinear: bilinear blends
    the boundary between two regions into intermediate shades that
    were never in the frame, and each shade wide enough to clear the
    coverage floor would count as a false element.
    """
    import numpy as np
    from PIL import Image
    try:
        with Image.open(frame_path) as image:
            image = image.convert("RGB").resize(
                (_SAMPLE_SIZE, _SAMPLE_SIZE), Image.NEAREST)
            pixels = np.asarray(image, dtype=np.uint8).reshape(-1, 3)
    except (OSError, ValueError):
        return 0
    if len(pixels) == 0:
        return 0
    quantized = (pixels // _ELEMENT_QUANTIZE) * _ELEMENT_QUANTIZE
    _, counts = np.unique(quantized, axis=0, return_counts=True)
    elements = sum(1 for c in counts if c / len(pixels) >= _MIN_ELEMENT_COVERAGE)
    return elements


def measure_reference_graphic_density(video_path: str) -> dict:
    """The graphic-density half of the Style Representation, as one dict.

    The distribution of per-frame element counts: the median, the mean,
    the range, and the elements per minute.  No threshold classifies a
    frame as dense or sparse - the distribution IS the measurement.
    """
    measured = measure_shot_boundaries(video_path)
    duration = measured["duration_seconds"]
    times = _reference_frame_times(measured["boundaries"], duration)
    frames = _extract_frames(video_path, times)
    try:
        counts = [_frame_element_count(path) for path in frames]
        counts = [c for c in counts if c > 0]
        if not counts:
            return {
                "source": video_path,
                "sampled_frames": 0,
                "median_elements": 0.0,
                "mean_elements": 0.0,
                "min_elements": 0,
                "max_elements": 0,
                "elements_per_minute": 0.0,
            }
        per_minute = (statistics.fmean(counts) / duration * 60.0
                      if duration > 0 else 0.0)
        return {
            "source": video_path,
            "sampled_frames": len(counts),
            "median_elements": round(statistics.median(counts), 3),
            "mean_elements": round(statistics.fmean(counts), 3),
            "min_elements": min(counts),
            "max_elements": max(counts),
            "elements_per_minute": round(per_minute, 3),
        }
    finally:
        _drop_frames(frames)


# ── The music-sync half ────────────────────────────────────────────
#
# Whether the reference cuts on the beat.  The cuts come from the same
# shot boundaries the editorial half measures; the beats come from the
# reference's own audio track, read by the music pipeline's tempo
# tracker (`analyze_tempo_beats` - beat_this preferred, librosa
# fallback), never synthesised.  A reference whose audio carries no
# detectable rhythm has no sync to measure, and says so.


def _extract_reference_audio(video_path: str):
    """The reference's audio as a temporary wav path, or None.

    Mono 22050 Hz, the rate the tempo tracker reads.  None when the
    reference carries no extractable audio - a silent reference, not
    an error.  The caller removes the file's directory with
    `_drop_frames`, the same way sampled frames are removed.
    """
    import tempfile
    audio_dir = tempfile.mkdtemp(prefix="vep_reference_audio_")
    out_path = os.path.join(audio_dir, "audio.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", video_path, "-vn",
         "-ac", "1", "-ar", "22050", out_path],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300, check=False)
    if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    import shutil
    shutil.rmtree(audio_dir, ignore_errors=True)
    return None


def _absent_music_sync(video_path: str, cuts: int, reason: str) -> dict:
    """No sync measurement, with the reason it is absent.

    A reference with no rhythm says nothing about cutting on the
    beat - an absence the direction reads as "no constraint", never a
    zero share a reader could mistake for "cuts free of the music".
    """
    return {
        "source": video_path,
        "beats_available": False,
        "reason": reason,
        "cuts": cuts,
    }


def measure_reference_music_sync(video_path: str) -> dict:
    """The music-sync half of the Style Representation, as one dict.

    Each cut's offset to its nearest beat, the share of cuts landing
    inside the on-beat window, and the same share against downbeats.
    The window is half the median inter-beat interval of the
    reference's OWN beats (`library.tools.beat_grid`) - a cut inside
    it is nearer that beat than its neighbour.  No constant decides
    what "on the beat" means for a stranger's tempo.
    """
    from library.tools.beat_grid import (
        MIN_USABLE_BEATS,
        nearest_beat_offsets,
        onbeat_tolerance_seconds,
    )
    measured = measure_shot_boundaries(video_path)
    cuts = [b["time_seconds"] for b in measured["boundaries"]]
    audio_path = _extract_reference_audio(video_path)
    if audio_path is None:
        return _absent_music_sync(video_path, len(cuts),
                                  "no extractable audio stream")
    try:
        from library.tools.analysis.music_pipeline import (
            analyze_tempo_beats)
        tempo = analyze_tempo_beats(audio_path)
    finally:
        _drop_frames([audio_path])
    beats = tempo.get("beats") or []
    if len(beats) < MIN_USABLE_BEATS:
        return _absent_music_sync(
            video_path, len(cuts),
            f"only {len(beats)} beats detected "
            f"(fewer than {MIN_USABLE_BEATS}) - no rhythm to cut against")
    tolerance = onbeat_tolerance_seconds(beats)
    offsets = nearest_beat_offsets(cuts, beats)
    on_beat = sum(1 for o in offsets if abs(o) <= tolerance)
    downbeats = tempo.get("downbeats") or []
    down_offsets = nearest_beat_offsets(cuts, downbeats)
    on_downbeat = sum(1 for o in down_offsets if abs(o) <= tolerance)
    return {
        "source": video_path,
        "beats_available": True,
        "bpm": tempo.get("bpm"),
        "beat_count": len(beats),
        "downbeat_source": tempo.get("downbeat_source"),
        "cuts": len(cuts),
        "onbeat_tolerance_seconds": tolerance,
        "tolerance_derived_as": "half the median inter-beat interval "
                                "of this reference's own beats",
        "median_abs_offset_seconds": (
            round(statistics.median([abs(o) for o in offsets]), 4)
            if offsets else 0.0),
        "on_beat_cuts": on_beat,
        "on_beat_share": round(on_beat / len(offsets), 4) if offsets else 0.0,
        "on_downbeat_cuts": on_downbeat,
        "on_downbeat_share": (round(on_downbeat / len(down_offsets), 4)
                              if down_offsets else 0.0),
    }


# ── The framing half ─────────────────────────────────────────────
#
# How the reference frames its subjects: how often a face is on
# screen, where it sits horizontally, and how large it is.  Faces are
# the subject signal (AGENTS.md 10.3) - the same shared Haar cascade
# and the same detection parameters the subject probe reads the
# project's own footage with, so a reference's framing compares
# against the project's on the same terms.  No width band classifies
# a shot as close or wide - the distribution IS the measurement.


def _frame_subject(frame_path: str, cascade) -> tuple | None:
    """The largest plausible face in one frame, or None.

    Centre-x and width as fractions of the frame width, the same pair
    `compute_face_presence` banks per sample.  The largest face wins,
    and a face hard against the frame edge is refused as too likely a
    false positive - both carried over from the subject probe rather
    than re-decided here.
    """
    import cv2
    from library.tools.subject_framing import (
        MAX_PLAUSIBLE_CX,
        MIN_PLAUSIBLE_CX,
    )
    image = cv2.imread(frame_path)
    if image is None:
        return None
    height, width = image.shape[:2]
    if width <= 0:
        return None
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(grey, 1.1, 5)
    if faces is None or len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    cx = (int(x) + int(w) / 2.0) / width
    if not (MIN_PLAUSIBLE_CX <= cx <= MAX_PLAUSIBLE_CX):
        return None
    return (round(cx, 4), round(int(w) / float(width), 4))


def measure_reference_framing(video_path: str) -> dict:
    """The framing half of the Style Representation, as one dict.

    One frame per shot, read by the shared face cascade: the share of
    frames carrying a face, the median horizontal placement, and the
    median face width with its range.  A reference that never shows a
    face is presence 0.0 - a measured absence of talking heads, never
    a failure.  An environment whose OpenCV carries no cascade cannot
    measure at all, and says so.
    """
    from library.tools.subject_framing import load_face_cascade
    measured = measure_shot_boundaries(video_path)
    duration = measured["duration_seconds"]
    times = _reference_frame_times(measured["boundaries"], duration)
    frames = _extract_frames(video_path, times)
    try:
        cascade = load_face_cascade()
        if cascade is None:
            return {
                "source": video_path,
                "faces_measurable": False,
                "reason": "no face cascade in this OpenCV",
                "sampled_frames": len(frames),
            }
        subjects = []
        for path in frames:
            try:
                reading = _frame_subject(path, cascade)
            except Exception:  # noqa: BLE001
                reading = None
            if reading is not None:
                subjects.append(reading)
        if not subjects:
            return {
                "source": video_path,
                "faces_measurable": True,
                "face_presence": 0.0,
                "faces": 0,
                "sampled_frames": len(frames),
            }
        centres = sorted(s[0] for s in subjects)
        widths = sorted(s[1] for s in subjects)
        return {
            "source": video_path,
            "faces_measurable": True,
            "face_presence": round(len(subjects) / len(frames), 4)
            if frames else 0.0,
            "faces": len(subjects),
            "sampled_frames": len(frames),
            "median_center_x": round(statistics.median(centres), 4),
            "median_face_width": round(statistics.median(widths), 4),
            "min_face_width": widths[0],
            "max_face_width": widths[-1],
        }
    finally:
        _drop_frames(frames)


# ── The sound-design half ────────────────────────────────────────
#
# How dense the reference's sound design is: strong transients per
# minute - impacts, hits and hard attacks, the events an SFX plan
# places.  Detection and the strong/weak split are the preflight's
# own (`detect_onsets_with_strength`, `classify_onsets`): strong is
# the top quartile of the reference's OWN onset strengths, never a
# fixed level a stranger's mix would have to share.  Speech syllables
# land in the weak three quartiles with the soft attacks; a dialogue
# piece does not read as designed.


def measure_reference_sound_design(video_path: str) -> dict:
    """The sound-design half of the Style Representation, as one dict.

    Onsets from the reference's own audio track, split strong/weak by
    their own strength distribution: the strong-transient density the
    SFX slot reads as its measured rung.  A reference with no
    extractable audio, no onset detector, or no classifiable onsets
    is unmeasurable with its reason - never a zero density a plan
    could mistake for "no sound design here".
    """
    try:
        import librosa  # noqa: F401
    except ImportError:
        return {
            "source": video_path,
            "transients_measurable": False,
            "reason": "librosa not available",
        }
    measured = measure_shot_boundaries(video_path)
    duration = measured["duration_seconds"]
    audio_path = _extract_reference_audio(video_path)
    if audio_path is None:
        return {
            "source": video_path,
            "transients_measurable": False,
            "reason": "no extractable audio stream",
        }
    try:
        from library.capabilities.temporal.index import (
            classify_onsets,
            detect_onsets_with_strength,
        )
        times, strengths = detect_onsets_with_strength(audio_path)
        classified = classify_onsets(times, strengths)
    finally:
        _drop_frames([audio_path])
    if classified is None:
        return {
            "source": video_path,
            "transients_measurable": False,
            "reason": "onset strengths unreadable",
        }
    strong = sum(1 for o in classified if o["kind"] == "strong")
    per_minute = (strong / duration * 60.0) if duration > 0 else 0.0
    return {
        "source": video_path,
        "transients_measurable": True,
        "onsets": len(classified),
        "strong_transients": strong,
        "strong_transients_per_minute": round(per_minute, 3),
        "duration_seconds": duration,
    }


def measure_reference_sfx_density(reference_path: str):
    """The reference's strong-transient density, for the SFX slot.

    The one sound value a reference's audio determines directly: how
    many hard attacks each minute carries.  The SFX slot reads this as
    its measured rung - the project declared a reference, and this is
    what measuring it says.  None when the reference's transients are
    unmeasurable - an absence, not a value: a reference with no audio
    says nothing about sound effects per minute, and the slot falls to
    the next rung.
    """
    sound = measure_reference_sound_design(reference_path)
    if not sound.get("transients_measurable"):
        return None
    return sound["strong_transients_per_minute"]


# ── The graphics half ──────────────────────────────────────────────
#
# The palette and the contrast are measured from the reference's
# FRAMES, not its cut points: one frame per shot, at the shot's
# midpoint - the frame that most represents what the shot shows.  The
# colours are the reference's own; what it shows (its content) is not
# read, only how it looks.

# How many frames the graphics half samples.  A presentation
# resolution, the way the histogram bins are - it does not decide what
# the palette is, it decides how finely the reference is sampled.
_MAX_GRAPHICS_FRAMES = 8

# How many colours the palette quantizes to.  The ruler's marks, not
# a creative decision: the reference's own colours, grouped so a
# reader can compare one reference's palette against another's.
_PALETTE_COLOURS = 5

# The frame size the pixels are sampled at.  Small because the
# palette and the contrast are distribution facts, not detail facts.
_SAMPLE_SIZE = 48

# The colour quantization step for element counting.  Presentation
# bins the way a ruler's marks are - they do not decide what a colour
# is, they only group what was measured.
_ELEMENT_QUANTIZE = 32

# The minimum share of the frame a quantized colour must cover to
# count as one element.  A region smaller than this is noise, not
# an element.
_MIN_ELEMENT_COVERAGE = 0.02


def _reference_frame_times(boundaries: list, duration: float) -> list:
    """The midpoint of each shot, evenly capped at `_MAX_GRAPHICS_FRAMES`.

    A shot's midpoint is its most representative frame - the cut
    points are where the shot CHANGES, the middle is where it simply
    IS.  More shots than the cap are sampled evenly across the piece
    so a long reference is not sampled only at its start.
    """
    if duration <= 0:
        return []
    times = [0.0] + [b["time_seconds"] for b in boundaries] + [duration]
    midpoints = [(times[i] + times[i + 1]) / 2.0
                 for i in range(len(times) - 1)]
    midpoints = [t for t in midpoints if 0 < t < duration]
    if len(midpoints) > _MAX_GRAPHICS_FRAMES:
        indices = [round(i * (len(midpoints) - 1)
                       / (_MAX_GRAPHICS_FRAMES - 1))
                   for i in range(_MAX_GRAPHICS_FRAMES)]
        midpoints = [midpoints[i] for i in indices]
    return midpoints


def _extract_frames(video_path: str, times: list) -> list:
    """Frames at `times`, as JPEG paths.  A failed extraction is absent.

    The frames go to a temporary directory the caller removes: the
    measurement is read-only on the project, and the reference's frames
    are not project data.
    """
    import tempfile
    frames_dir = tempfile.mkdtemp(prefix="vep_reference_frames_")
    frames = []
    for index, moment in enumerate(times):
        out_path = os.path.join(frames_dir, f"frame_{index:04d}.jpg")
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(moment), "-i",
             video_path, "-vframes", "1", "-q:v", "3", out_path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, check=False)
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            frames.append(out_path)
    return frames


def _drop_frames(frames: list) -> None:
    """Remove the sampled frames and their directory."""
    import shutil
    for path in frames:
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)


def _frame_luma(frame_path: str) -> list:
    """One frame's pixels as luma values (0-255), downscaled."""
    import numpy as np
    from PIL import Image
    with Image.open(frame_path) as image:
        image = image.convert("L").resize((_SAMPLE_SIZE, _SAMPLE_SIZE))
        return np.asarray(image, dtype=np.float64).ravel()


def _frame_colours(frame_path: str):
    """One frame's pixels as an (N, 3) uint8 array, downscaled."""
    import numpy as np
    from PIL import Image
    with Image.open(frame_path) as image:
        image = image.convert("RGB").resize((_SAMPLE_SIZE, _SAMPLE_SIZE))
        return np.asarray(image, dtype=np.uint8).reshape(-1, 3)


def measure_palette(frames: list) -> dict:
    """The reference's dominant colours, with their share of the frame.

    Every sampled frame's pixels are quantized together to
    `_PALETTE_COLOURS` colours; each colour's share of the total pixel
    count is its coverage.  The palette is the reference's own colours
    grouped the way a ruler groups - what it shows is content and is
    never read here.
    """
    if not frames:
        return {"colours": [], "sampled_frames": 0}
    import numpy as np
    from PIL import Image
    pixels = np.concatenate([_frame_colours(path) for path in frames])
    if len(pixels) == 0:
        return {"colours": [], "sampled_frames": 0}
    flat = Image.fromarray(pixels.reshape(1, -1, 3), "RGB")
    quantized = flat.quantize(colors=_PALETTE_COLOURS,
                               method=Image.Quantize.MEDIANCUT)
    palette = quantized.getpalette() or []
    counts = quantized.getcolors() or []
    total = sum(count for count, _ in counts)
    colours = []
    for count, index in sorted(counts, reverse=True):
        r, g, b = palette[index * 3:index * 3 + 3]
        colours.append({
            "hex": f"#{r:02x}{g:02x}{b:02x}",
            "coverage": round(count / total, 4) if total else 0.0,
        })
    return {"colours": colours, "sampled_frames": len(frames)}


def measure_contrast(frames: list) -> dict:
    """The reference's contrast: the spread of its own luma distribution.

    The luma values of every sampled frame, pooled: their mean, their
    spread (standard deviation) and the dark-to-light range (p90 minus
    p10).  A piece whose luma clusters in a narrow band is low-
    contrast; one that uses the full range is high-contrast.  The
    numbers ARE the reading - no threshold labels them, because the
    contrast is a fact to compare, not a category to assign.
    """
    if not frames:
        return {"luma_mean": 0.0, "luma_std": 0.0, "luma_p10": 0.0,
                "luma_p90": 0.0, "contrast_range": 0.0,
                "sampled_frames": 0}
    import numpy as np
    luma = []
    for path in frames:
        luma.extend(_frame_luma(path))
    values = np.asarray(luma, dtype=np.float64)
    p10 = float(np.percentile(values, 10))
    p90 = float(np.percentile(values, 90))
    return {
        "luma_mean": round(float(values.mean()), 3),
        "luma_std": round(float(values.std()), 3),
        "luma_p10": round(p10, 3),
        "luma_p90": round(p90, 3),
        "contrast_range": round(p90 - p10, 3),
        "sampled_frames": len(frames),
    }


def measure_reference_graphical_style(video_path: str) -> dict:
    """The graphics half of the Style Representation, as one dict.

    The palette and the contrast, measured from one frame per shot.
    This is the data the graphics IR and the color step read; the
    caption and type style is a separate slice.
    """
    measured = measure_shot_boundaries(video_path)
    duration = measured["duration_seconds"]
    times = _reference_frame_times(measured["boundaries"], duration)
    frames = _extract_frames(video_path, times)
    try:
        return {
            "source": video_path,
            "palette": measure_palette(frames),
            "contrast": measure_contrast(frames),
        }
    finally:
        _drop_frames(frames)


def measure_reference_style(video_path: str) -> dict:
    """The whole Style Representation, as one dict.

    The editorial half (shot lengths, transitions, pacing), the
    graphics half (palette, contrast), the text half (captions, type),
    the camera-movement half (motion-energy distribution), the
    graphic-density half (per-frame element counts), the music-sync
    half (cut-to-beat alignment), the framing half (face presence,
    placement and scale) and the sound-design half (strong-transient
    density).  CONTENT is never read: the reference's words, faces and
    subject are its own; only how it looks, how it is cut, how its
    text is set, how much motion it carries, how densely its frames
    are filled, whether its cuts land on its beats, how it frames the
    people in it and how dense its sound design is are measured.
    """
    editorial = measure_reference_editorial_style(video_path)
    graphical = measure_reference_graphical_style(video_path)
    text = measure_reference_text_style(video_path)
    camera = measure_reference_camera_movement(video_path)
    density = measure_reference_graphic_density(video_path)
    sync = measure_reference_music_sync(video_path)
    framing = measure_reference_framing(video_path)
    sound = measure_reference_sound_design(video_path)
    return {
        "source": video_path,
        "duration_seconds": editorial["duration_seconds"],
        "shot_length_distribution":
            editorial["shot_length_distribution"],
        "transition_vocabulary": editorial["transition_vocabulary"],
        "pacing": editorial["pacing"],
        "palette": graphical["palette"],
        "contrast": graphical["contrast"],
        "text": text,
        "camera_movement": camera,
        "graphic_density": density,
        "music_sync": sync,
        "framing": framing,
        "sound_design": sound,
    }


# ── The text half ──────────────────────────────────────────────────
#
# Whether the reference carries captions, where they sit, how large
# they are, what colour they are and how many words per caption - and
# the same for non-caption text (titles, graphics).  The text's WORDS
# are content: they are counted, never read for meaning.

# A detection whose centre is below this normalized y is a caption.
# The bottom third of the frame is where captions live; the middle and
# the top are where titles and graphics live.
_CAPTION_Y = 0.6

# How many of the brightest pixels in a text box are read as the
# text's own colour.  Text is usually the brightest thing in its box
# (white or vivid on a darker background); the box's mean would be the
# background.
_TEXT_BRIGHTEST_FRACTION = 0.2


def _frame_array(frame_path: str):
    """A frame as an RGB numpy array, or None when unreadable."""
    import numpy as np
    from PIL import Image
    try:
        with Image.open(frame_path) as image:
            return np.asarray(image.convert("RGB"), dtype=np.uint8)
    except (OSError, ValueError):
        return None


def _text_colour(frame: np.ndarray, bbox_norm: tuple) -> str | None:
    """The colour of the text in a detection, as hex, or None.

    The brightest pixels in the box are the text itself; their mean is
    the text's colour.  None when the box is empty or off-frame.
    """
    import numpy as np
    if frame is None:
        return None
    height, width = frame.shape[:2]
    x, y, w, h = bbox_norm
    x0, y0 = int(x * width), int(y * height)
    x1, y1 = int((x + w) * width), int((y + h) * height)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(width, x1), min(height, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    region = frame[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64)
    if len(region) == 0:
        return None
    luma = region @ np.array([0.299, 0.587, 0.114])
    count = max(1, int(len(region) * _TEXT_BRIGHTEST_FRACTION))
    brightest = region[np.argsort(luma)[-count:]]
    mean = brightest.mean(axis=0)
    return "#{:02x}{:02x}{:02x}".format(
        int(mean[0]), int(mean[1]), int(mean[2]))


def _ocr_detections(frame_path: str) -> list:
    """OCR detections for one frame, or [] when OCR is unavailable.

    The OCR model is a heavy dependency; a frame it cannot read, or an
    environment without the model, yields no detections - the text
    style is ABSENT for that frame, never an error.
    """
    frame = _frame_array(frame_path)
    if frame is None:
        return []
    try:
        from library.tools.analysis.ocr_extractor import OCRExtractor
        extractor = OCRExtractor()
        return extractor.extract_text_from_frame(frame)
    except Exception:  # noqa: BLE001
        return []


def _classify_detections(detections: list, frame_path: str) -> dict:
    """Caption and title readings from one frame's detections.

    A detection is a caption when its centre sits in the bottom third
    of the frame; everything else is a title or a graphic.  The words
    are counted, never read.
    """
    frame = _frame_array(frame_path)
    captions = []
    titles = []
    for detection in detections:
        x, y, w, h = detection.bbox_normalized
        centre_y = y + h / 2.0
        words = len(str(detection.text).split())
        entry = {
            "words": words,
            "height_fraction": round(h, 4),
            "colour": _text_colour(frame, detection.bbox_normalized),
        }
        if centre_y >= _CAPTION_Y:
            captions.append(entry)
        else:
            titles.append(entry)
    return {"captions": captions, "titles": titles}


def _summarise_text(readings: list, key: str) -> dict:
    """The mean reading for captions or titles across sampled frames.

    Presence is the fraction of frames carrying the kind; position,
    size, colour and words are the means over the frames that carry
    it.  A kind the reference never carries is ABSENT - zero presence,
    never a default.
    """
    frames_with = [r for r in readings if r[key]]
    if not frames_with:
        return {"presence": 0.0, "count": 0}
    entries = [e for r in frames_with for e in r[key]]
    heights = [e["height_fraction"] for e in entries]
    words = [e["words"] for e in entries]
    colours = [e["colour"] for e in entries if e["colour"]]
    return {
        "presence": round(len(frames_with) / len(readings), 4),
        "count": len(entries),
        "mean_height_fraction": round(sum(heights) / len(heights), 4),
        "mean_words": round(sum(words) / len(words), 3),
        "colours": colours,
    }


def measure_reference_text_style(video_path: str) -> dict:
    """The text half of the Style Representation, as one dict.

    One frame per shot, read by OCR: whether the reference carries
    captions, where they sit, how large, what colour, how many words
    per caption - and the same for non-caption text.  The words are
    counted, never read for meaning.
    """
    measured = measure_shot_boundaries(video_path)
    duration = measured["duration_seconds"]
    times = _reference_frame_times(measured["boundaries"], duration)
    frames = _extract_frames(video_path, times)
    try:
        readings = []
        for path in frames:
            detections = _ocr_detections(path)
            readings.append(_classify_detections(detections, path))
        return {
            "source": video_path,
            "captions": _summarise_text(readings, "captions"),
            "titles": _summarise_text(readings, "titles"),
            "sampled_frames": len(readings),
        }
    finally:
        _drop_frames(frames)


def measure_reference_words_per_caption(video_path: str):
    """The reference's mean words per caption, for the subtitle slot.

    The one text value a reference's frames determine directly: how
    many words the reference puts on one caption.  The subtitle slot
    reads this as its measured rung - the project declared a reference,
    and this is what measuring it says.  None when the reference
    carries no captions - an absence, not a value: a reference without
    captions says nothing about words per card, and the slot falls to
    the next rung.
    """
    style = measure_reference_text_style(video_path)
    captions = style["captions"]
    return captions.get("mean_words")


# ── Persistence: measure once, reuse when the reference is unchanged ──
#
# The reference is decoded by ffmpeg twice per run on the first run
# (once here for the step context, once by the pacing slot's measured
# rung) and again on EVERY run, because state is re-read from disk and
# the measurement is not persisted.  The fix: the measurement is stored
# in the project's pipeline state beside a CONTENT DIGEST of the
# reference file, and a run whose reference is unchanged reuses the
# stored value instead of decoding again.  The digest is the reference
# file's own bytes - not its name, not its mtime - so a reference that
# was replaced by a different file of the same name is re-measured.

_DIGEST_CHUNK = 1 << 20


def reference_digest(video_path: str) -> str:
    """The reference file's content digest, or "" when unreadable.

    A SHA-256 over the file's bytes, read in chunks so a long reference
    is not held in memory.  The digest is the key the stored
    measurement is reused by: same bytes, same style.
    """
    import hashlib
    try:
        hasher = hashlib.sha256()
        with open(video_path, "rb") as handle:
            while True:
                chunk = handle.read(_DIGEST_CHUNK)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return ""


def stored_reference_style(project_folder: str):
    """The stored representation, its digest, and any failure.

    Reads the project's pipeline state: the measurement the runner
    took on an earlier run, beside the digest of the reference file it
    was measured from, and the recorded failure when a declared
    reference could not be measured.  ``(None, "", "")`` when the
    project has no state or the reference was never measured - never
    a re-measurement.
    """
    if not project_folder:
        return None, "", ""
    try:
        from library.tools.project_layout import ProjectLayout
        state_path = ProjectLayout(project_folder).pipeline_data_path
        if not os.path.isfile(state_path):
            return None, "", ""
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return None, "", ""
    style = state.get("reference_style")
    if not isinstance(style, dict):
        return None, "", state.get("reference_style_error") or ""
    return (style, state.get("reference_style_digest") or "",
            state.get("reference_style_error") or "")


def measure_reference_style_cached(project_folder: str, reference: str):
    """The Style Representation, measured once and reused.

    The reference the project declares, measured from its own pixels
    and frames.  When the project's pipeline state already holds a
    measurement whose digest matches the reference file's current
    bytes, the stored value is returned and the reference is NOT
    decoded again - a run whose reference is unchanged costs no ffmpeg.

    Returns ``(style, digest, measured_now)``: the representation, the
    digest it was measured from, and whether this call measured it
    (True) or reused the stored value (False).  A reference that
    cannot be read returns ``(None, "", False)``.
    """
    reference_path = (reference if os.path.isabs(reference)
                      else os.path.join(project_folder, reference))
    digest = reference_digest(reference_path)
    if not digest:
        return None, "", False
    stored, stored_digest, _ = stored_reference_style(project_folder)
    if stored is not None and stored_digest == digest:
        return stored, digest, False
    style = measure_reference_style(reference_path)
    return style, digest, True
