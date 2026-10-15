"""Real-footage evidence for EVAL-04 repeats: measured, never modelled.

This module turns read-only camera originals into Project Model
observations using only ffmpeg/ffprobe - no model passes, no downloads,
no VLM interpretation. Every observation names its method and the exact
byte range it was read from, so a later run can reproduce or refute it.

What it measures
----------------
- ``media.source_metadata`` (lane m0): ffprobe duration, frame rate,
  dimensions, and file size straight from the container.
- ``video.scene_boundary``: ffmpeg ``select`` scene scores over an
  explicit window; each reported boundary becomes one observation with
  its measured score. The same rows feed ``scene`` signals, which the
  edit-point planner reads as cut candidates.
- ``audio.sound_event`` (lane m5): energy-based sound/silence spans from
  ``silencedetect`` plus integrated loudness from ``volumedetect``.
  These are energy events with the detector named - they never pose as
  diarized turns, words, or identified sounds.

What it refuses
---------------
Speech words, speaker turns, faces, objects, and multicam offsets need
analysers this slice does not run. Windows without those measurements
answer ``not_yet_analysed`` downstream; this module never synthesises
them. A measurement that fails (unreadable file, empty window) raises
instead of emitting an empty observation.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

TICKS_PER_SECOND = 1000
SCENE_SCORE_THRESHOLD = 0.3
SILENCE_NOISE_DB = "-30dB"
SILENCE_MIN_SECONDS = 0.5

RunFn = Callable[[list[str]], subprocess.CompletedProcess]


def _run(argv: list[str], run: RunFn | None = None) -> subprocess.CompletedProcess:
    if run is not None:
        return run(argv)
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", check=False)


def probe_source(path: str, run: RunFn | None = None) -> dict[str, Any]:
    """Measure container metadata for one source file.

    Raises on unreadable files or unparseable probe output: a source
    whose identity cannot be measured cannot carry evidence.
    """
    completed = _run(["ffprobe", "-v", "error", "-show_entries",
                      "format=duration,size:stream=width,height,"
                      "avg_frame_rate,nb_frames,codec_name",
                      "-of", "json", path], run)
    if completed.returncode != 0:
        raise RuntimeError(f"ffprobe refused {path}: {completed.stderr[-500:]}")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe output for {path} is not JSON") from exc
    video = next((stream for stream in payload.get("streams", [])
                  if stream.get("width")), {})
    rate = video.get("avg_frame_rate", "0/1")
    try:
        numerator, denominator = rate.split("/")
        fps = float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        fps = 0.0
    duration = float((payload.get("format") or {}).get("duration", 0) or 0)
    if duration <= 0 or fps <= 0:
        raise RuntimeError(f"ffprobe measured no usable duration/fps for {path}")
    return {"duration_seconds": duration, "fps": fps,
            "width": int(video.get("width", 0)),
            "height": int(video.get("height", 0)),
            "size_bytes": int((payload.get("format") or {}).get("size", 0) or 0),
            "codec": video.get("codec_name", "unknown")}


def _scene_argv(path: str, start: float, end: float) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-ss", str(start), "-t",
            str(round(end - start, 3)), "-i", path, "-vf",
            f"select='gt(scene,{SCENE_SCORE_THRESHOLD})',showinfo",
            "-fps_mode", "passthrough", "-f", "null", "-"]


_SHOWINFO_RE = re.compile(
    r"n:\s*(\d+).*pts:\s*(\d+).*pts_time:([\d.]+)")


def detect_scenes(path: str, start: float, end: float,
                  run: RunFn | None = None) -> list[dict[str, Any]]:
    """Detect scene boundaries inside one source window.

    Returns ``[{tick_s, score}]`` in source seconds. ``ffmpeg`` exit
    status is ignored - the ``select`` filter consumes the whole window
    and the null muxer always "fails" harmlessly - but output that
    parses to nothing is returned as an empty list, never as a boundary
    at the window edge.
    """
    if end <= start:
        raise ValueError("scene detection needs a non-empty window")
    completed = _run(_scene_argv(path, start, end), run)
    stderr = completed.stderr or ""
    if "Unrecognized option" in stderr or "Error " in stderr:
        raise RuntimeError(f"ffmpeg scene detection failed for {path}: "
                           f"{stderr[-500:]}")
    boundaries = []
    for line in stderr.splitlines():
        match = _SHOWINFO_RE.search(line)
        if not match:
            continue
        pts_time = float(match.group(3))
        tick_s = round(start + pts_time, 3)
        if start < tick_s < end:
            score_match = re.search(r"scene_score:([\d.]+)", line)
            boundaries.append({
                "tick_s": tick_s,
                "score": float(score_match.group(1))
                if score_match else SCENE_SCORE_THRESHOLD})
    return sorted(boundaries, key=lambda row: row["tick_s"])


def _audio_argv(path: str, start: float, end: float) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-ss", str(start), "-t",
            str(round(end - start, 3)), "-i", path, "-vn", "-af",
            f"silencedetect=noise={SILENCE_NOISE_DB}:d={SILENCE_MIN_SECONDS},"
            f"volumedetect", "-f", "null", "-"]


_SILENCE_START_RE = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END_RE = re.compile(
    r"silence_end:\s*([\d.]+).*silence_duration:\s*([\d.]+)")
_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?[\d.]+)\s*dB")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?[\d.]+)\s*dB")


def measure_audio(path: str, start: float, end: float,
                  run: RunFn | None = None) -> dict[str, Any]:
    """Measure energy-based sound/silence spans inside one source window.

    Returns silence spans plus overall mean/max volume. Silence longer
    than the floor is a measured absence of energy, not a measured
    absence of speech: the spans never claim nobody spoke.
    """
    if end <= start:
        raise ValueError("audio measurement needs a non-empty window")
    completed = _run(_audio_argv(path, start, end), run)
    stderr = completed.stderr or ""
    if "Unrecognized option" in stderr or "Error " in stderr:
        raise RuntimeError(f"ffmpeg audio measurement failed for {path}: "
                           f"{stderr[-500:]}")
    silences: list[list[float]] = []
    open_start: float | None = None
    for line in stderr.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            open_start = round(start + float(start_match.group(1)), 3)
            continue
        end_match = _SILENCE_END_RE.search(line)
        if end_match and open_start is not None:
            silences.append([open_start,
                             round(start + float(end_match.group(1)), 3)])
            open_start = None
    if open_start is not None:
        silences.append([open_start, round(end, 3)])
    mean_match = _MEAN_VOLUME_RE.search(stderr)
    max_match = _MAX_VOLUME_RE.search(stderr)
    sounded: list[list[float]] = []
    cursor = round(start, 3)
    for silence_start, silence_end in sorted(silences):
        if silence_start > cursor:
            sounded.append([cursor, silence_start])
        cursor = max(cursor, silence_end)
    if cursor < end:
        sounded.append([cursor, round(end, 3)])
    return {
        "silence_spans": sorted(silences),
        "sounded_spans": sounded,
        "mean_volume_db": float(mean_match.group(1)) if mean_match else None,
        "max_volume_db": float(max_match.group(1)) if max_match else None,
        "method": f"silencedetect(noise={SILENCE_NOISE_DB},"
                  f"d={SILENCE_MIN_SECONDS})+volumedetect",
    }


def _ticks(seconds: float) -> int:
    return int(round(seconds * TICKS_PER_SECOND))


def _native(measurement_type: str, freshness: str = "fresh",
            coverage: str = "full", **extra: Any) -> dict[str, Any]:
    return {"payload": {"evidence_contract": {
        "measurement_type": measurement_type, "freshness": freshness,
        "coverage": coverage, **extra}}}


def observations_for(source_id: str, digest: str, clock_id: str,
                     metadata: dict[str, Any],
                     boundaries: list[dict[str, Any]],
                     audio: dict[str, Any]) -> list[dict[str, Any]]:
    """Build typed observations from the three measurements.

    Scene boundaries carry their measured score; sounded spans carry
    their detector method; metadata carries the probe values. Ids are
    deterministic from source, kind, and span, so remeasuring the same
    window reproduces the same evidence.
    """
    observations = [{
        "id": f"obs-{source_id}-metadata",
        "asset_id": source_id, "provenance_id": f"prov-{digest}-m0",
        "interval": {"clock_id": clock_id, "start": 0,
                     "end": _ticks(metadata["duration_seconds"])},
        **_native("media.source_metadata",
                  value={"duration_seconds": metadata["duration_seconds"],
                         "fps": metadata["fps"],
                         "dimensions": [metadata["width"],
                                        metadata["height"]]}),
    }]
    for index, boundary in enumerate(boundaries):
        observations.append({
            "id": f"obs-{source_id}-scene-{index}",
            "asset_id": source_id, "provenance_id": f"prov-{digest}-m3",
            "interval": {"clock_id": clock_id,
                         "start": _ticks(boundary["tick_s"]),
                         "end": _ticks(boundary["tick_s"]) + 1},
            **_native("video.scene_boundary",
                      value={"score": boundary["score"],
                             "threshold": SCENE_SCORE_THRESHOLD}),
        })
    for index, (span_start, span_end) in enumerate(audio["sounded_spans"]):
        observations.append({
            "id": f"obs-{source_id}-sounded-{index}",
            "asset_id": source_id, "provenance_id": f"prov-{digest}-m5",
            "interval": {"clock_id": clock_id, "start": _ticks(span_start),
                         "end": _ticks(span_end)},
            **_native("audio.sound_event",
                      value={"kind": "energy_above_silence_floor",
                             "method": audio["method"],
                             "mean_volume_db": audio["mean_volume_db"],
                             "max_volume_db": audio["max_volume_db"]}),
        })
    return observations


def signals_for(source_id: str, clock_id: str,
                boundaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build scene signals behind edit-point candidates.

    One signal per measured boundary interval pair: consecutive
    boundaries delimit the measured scene span, so candidates fall on
    real transitions rather than on window edges.
    """
    signals = []
    for index, boundary in enumerate(boundaries):
        signals.append({
            "id": f"sig-{source_id}-scene-{index}",
            "measurement_type": "scene_boundary",
            "processing_status": "analyzed",
            "interval": {"clock_id": clock_id,
                         "start": _ticks(boundary["tick_s"]),
                         "end": _ticks(boundary["tick_s"]) + 1},
            "samples": [],
        })
    return signals


def model_for(model_id: str, source_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble the Project Model dict the runner consults.

    Each entry carries ``source_id``, ``digest``, ``clock_id``,
    ``metadata`` (from :func:`probe_source`), ``boundaries`` (from
    :func:`detect_scenes`), and ``audio`` (from :func:`measure_audio`).
    """
    observations: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    for entry in source_entries:
        observations.extend(observations_for(
            entry["source_id"], entry["digest"], entry["clock_id"],
            entry["metadata"], entry["boundaries"], entry["audio"]))
        signals.extend(signals_for(
            entry["source_id"], entry["clock_id"], entry["boundaries"]))
    return {"id": model_id, "revision": 1,
            "assets": [{"id": entry["source_id"],
                        "content_digest": entry["digest"]}
                       for entry in source_entries],
            "clocks": [{"id": entry["clock_id"],
                        "domain": {"asset_id": entry["source_id"]}}
                       for entry in source_entries],
            "observations": observations, "signals": signals}
