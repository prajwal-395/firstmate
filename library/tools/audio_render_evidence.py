"""Measure a rendered audio file, and judge an audio change against it.

An audio operation is only finished when the RENDER says the change is
audible.  A read-back says the value was stored; it does not say the
clip plays at the level that was asked for, and it says nothing at all
about whether the rest of the mix moved.  This module is the measurement
that answers both, from the decoded samples of a rendered file:

* :func:`measure_window` - one window's RMS, peak and fraction of
  digital-zero samples.  A window that is QUIET and a window that is
  SILENT are different facts, and only the second one is a mute.
* :func:`measure_pan` - the same window read per channel, so a pan is
  measured as a channel BALANCE rather than as a level.
* :func:`render_timeline` - the render itself, through Resolve's own
  render API, so the measurement is of the picture and sound the
  timeline actually produces.
* :func:`gain_evidence` - the verdict: the measured change against the
  requested dB, and whether every OTHER window held its level.

**This module is deliberately free of repository imports.**  The live
measurement that qualifies the audio routes runs INSIDE Resolve, on the
broker's own connection, where the engine's package is not importable;
it loads this file by path so the numbers in the report come from the
shipped measurement and not from a copy of it.  Everything here is
stdlib, and it uses ``ffmpeg`` for the decode.

It measures a window's LEVEL, not a file's LOUDNESS: integrated LUFS is
``library/tools/render_qa.py``'s job and is not re-implemented here.
"""

from __future__ import annotations

import array
import math
import os
import subprocess
import time
from typing import Any, Mapping, Optional, Sequence

#: The decode rate every measurement is taken at.  Resolve renders the
#: measurement renders at 48 kHz, and a window measured at another rate
#: is not comparable with one measured at this one.
SAMPLE_RATE = 48000

#: The tolerance on a measured gain change, in dB.  A 0.5 dB window on
#: a decoded AAC render is a decoding artefact, not a write that missed:
#: the measured change must be within this of the requested dB.
GAIN_TOLERANCE_DB = 0.5

#: The tolerance on a window that was NOT asked to move, in dB.  The mix
#: is preserved when every untouched window reads within this of its
#: level before the change.
MIX_TOLERANCE_DB = 0.5


__all__ = [
    "AUDIO_VOLUME_PROPERTY",
    "AUDIO_VOLUME_ENABLED_PROPERTY",
    "AUDIO_VOLUME_MIN_DB",
    "AUDIO_VOLUME_MAX_DB",
    "audio_rows",
    "audio_levels",
    "audio_item_by_unique_id",
    "item_window",
    "read_level",
    "write_level",
    "restore_level",
    "linked_ids",
    "measure_window",
    "measure_pan",
    "render_timeline",
    "gain_evidence",
]

#: The property names Resolve 21.1 carries on an AUDIO item: the level
#: in dB, and the switch that enables it.  The spelling Ren used to try,
#: ``Volume``, is NOT among them - ``SetProperty("Volume")`` answers
#: False, so a write naming it writes nothing at all.
AUDIO_VOLUME_PROPERTY = "AudioVolume"
AUDIO_VOLUME_ENABLED_PROPERTY = "AudioVolumeEnabled"

#: Resolve 21.1's own bounds for the level, in dB.
AUDIO_VOLUME_MIN_DB = -100.0
AUDIO_VOLUME_MAX_DB = 30.0


# ── the item half ────────────────────────────────────────────────────
#
# These read and write the Resolve item itself.  They live beside the
# measurement so the live run that qualifies the route and the shipped
# executor run THE SAME code: the live script loads this file by path
# (the engine's package is not importable inside the broker's session)
# and calls these functions on the live handles.


def audio_rows(timeline) -> list:
    """Every audio item on the timeline, with the track it sits on."""
    rows = []
    try:
        count = int(timeline.GetTrackCount("audio") or 0)
    except Exception:  # noqa: BLE001 - an unreadable track count is empty
        count = 0
    for index in range(1, count + 1):
        try:
            items = timeline.GetItemListInTrack("audio", index) or []
        except Exception:  # noqa: BLE001 - an unreadable row is empty
            items = []
        for item in items:
            rows.append((index, item))
    return rows


def audio_item_by_unique_id(timeline, unique_id: str) -> list:
    """The audio item(s) carrying this unique id, on their tracks."""
    matches = []
    for index, item in audio_rows(timeline):
        try:
            found = str(item.GetUniqueId() or "")
        except Exception:  # noqa: BLE001 - an unreadable id is not a match
            continue
        if found == unique_id:
            matches.append((index, item))
    return matches


def read_level(item) -> dict:
    """One audio item's level and enablement, as it stands."""
    def _read(name):
        try:
            return item.GetProperty(name)
        except Exception:  # noqa: BLE001 - an unreadable property is None
            return None

    return {"level_db": _read(AUDIO_VOLUME_PROPERTY),
            "enabled": _read(AUDIO_VOLUME_ENABLED_PROPERTY)}


def write_level(item, db: float) -> dict:
    """Write the level on the item, and read it back.

    The enablement switch is written too: a level on a disabled item is
    a value nobody hears, and the request asked for a level that plays.
    The read-back is what decides whether the write landed - the call's
    own return value is not evidence.
    """
    try:
        set_volume = item.SetProperty(AUDIO_VOLUME_PROPERTY, float(db))
    except Exception as exc:  # noqa: BLE001 - a raised call is a row
        return {"set_return": False, "enabled_set_return": False,
                "read_back": None, "error": repr(exc)}
    try:
        set_enabled = item.SetProperty(AUDIO_VOLUME_ENABLED_PROPERTY, True)
    except Exception:  # noqa: BLE001 - the level is what was asked for
        set_enabled = False
    return {"set_return": bool(set_volume),
            "enabled_set_return": bool(set_enabled),
            "read_back": read_level(item)["level_db"]}


def restore_level(item, level_db, enabled) -> bool:
    """Write the previous level back, and say whether it took."""
    try:
        if level_db is not None:
            item.SetProperty(AUDIO_VOLUME_PROPERTY, float(level_db))
        if enabled is not None:
            item.SetProperty(AUDIO_VOLUME_ENABLED_PROPERTY, bool(enabled))
    except Exception:  # noqa: BLE001 - the report says it did not take
        return False
    if level_db is None:
        return True
    back = read_level(item)["level_db"]
    if back is None:
        return False
    return abs(float(back) - float(level_db)) <= 0.01


def linked_ids(item) -> list:
    """The item's linked partners, by unique id, sorted."""
    try:
        partners = item.GetLinkedItems() or []
    except Exception:  # noqa: BLE001 - an unreadable group is empty
        partners = []
    ids = []
    for partner in partners:
        try:
            ids.append(str(partner.GetUniqueId() or ""))
        except Exception:  # noqa: BLE001 - an unreadable id is not a name
            continue
    return sorted(ids)


def audio_levels(timeline) -> dict:
    """Every audio item's level and enablement, by unique id."""
    levels = {}
    for _index, item in audio_rows(timeline):
        try:
            uid = str(item.GetUniqueId() or "")
        except Exception:  # noqa: BLE001 - an unreadable id is not a row
            continue
        if not uid:
            continue
        levels[uid] = read_level(item)
    return levels


def item_window(record_in: int, record_out: int, fps: float,
                timeline_start: int) -> dict:
    """The measurement window inside one audio item's record span.

    The window sits INSIDE the clip, clear of its edges: a window that
    straddles a cut mixes two clips' audio into one number, and a
    window that reaches the very first frame of a clip catches the
    boundary itself.  The render's time zero is the timeline's start
    frame, so the window is measured in the render's own timebase.
    """
    start = (float(record_in) - float(timeline_start)) / fps
    end = (float(record_out) - float(timeline_start)) / fps
    span = end - start
    if span <= 0:
        return {"start": round(start, 3), "duration": 0.0}
    margin = min(0.25, span / 4.0)
    if span - 2 * margin < 0.2:
        # A clip shorter than half a second has no quiet margin: the
        # window is its middle half, which is still inside it.
        margin = span / 4.0
    return {"start": round(start + margin, 3),
            "duration": round(span - 2 * margin, 3)}


# ── the decode ───────────────────────────────────────────────────────


def _decode(path: str, channels: int) -> array.array:
    """The decoded samples of ``path``, as interleaved 16-bit PCM."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-nostdin", "-i", path,
           "-f", "s16le", "-acodec", "pcm_s16le", "-ac", str(channels),
           "-ar", str(SAMPLE_RATE), "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    data = proc.stdout
    if not data:
        raise RuntimeError(
            "ffmpeg decoded no audio from {!r} (exit {})".format(
                path, proc.returncode))
    samples = array.array("h")
    samples.frombytes(data[:len(data) - (len(data) % 2)])
    return samples


def _window(samples: array.array, start: Optional[float],
            duration: Optional[float]) -> array.array:
    """The slice of ``samples`` a time window covers."""
    total = len(samples)
    first = 0 if start is None else int(start * SAMPLE_RATE)
    last = total if duration is None else int(
        (start or 0) * SAMPLE_RATE + duration * SAMPLE_RATE)
    return samples[max(0, first):max(0, min(total, last))]


def _rms_db(samples: Sequence[int]) -> Optional[float]:
    """The RMS of a window, in dBFS, or None when the window is empty."""
    if not samples:
        return None
    total = 0.0
    for value in samples:
        sample = value / 32768.0
        total += sample * sample
    rms = (total / len(samples)) ** 0.5
    if rms <= 0:
        return None
    return round(20.0 * math.log10(rms), 2)


def _peak_db(samples: Sequence[int]) -> Optional[float]:
    """The peak of a window, in dBFS, or None when the window is empty."""
    if not samples:
        return None
    peak = max(abs(value) for value in samples)
    if peak <= 0:
        return None
    return round(20.0 * math.log10(peak / 32768.0), 2)


# ── the measurements ─────────────────────────────────────────────────


def measure_window(path: str, start: Optional[float] = None,
                   duration: Optional[float] = None) -> dict:
    """One window of the rendered audio, measured in SAMPLES.

    The window is cut from the decoded PCM, not from a filter's report:
    ``volumedetect`` and ``ebur128`` measure the stream they are given,
    and a seek that lands on a frame boundary mixes two clips' windows
    into one number.  The readings are the window's own RMS, its own
    peak, and the fraction of its samples that are DIGITAL ZERO - which
    is how "the muted clip is silent" is told from "the muted clip is
    quiet".
    """
    samples = _decode(path, 1)
    window = _window(samples, start, duration)
    out = {
        "window_seconds": round(len(window) / SAMPLE_RATE, 3),
        "rms_db": None,
        "peak_db": None,
        "digital_zero_fraction": None,
    }
    if not window:
        return out
    zeros = sum(1 for value in window if value == 0)
    out["rms_db"] = _rms_db(window)
    out["peak_db"] = _peak_db(window)
    out["digital_zero_fraction"] = round(zeros / len(window), 4)
    return out


def measure_pan(path: str, start: Optional[float] = None,
                duration: Optional[float] = None) -> dict:
    """One window's channel BALANCE: the pan, measured as sound.

    A pan does not change the window's level much - it moves energy
    between the channels.  So the reading is each channel's own RMS, the
    balance between them (a negative balance is the window sitting
    further LEFT than right), and the level of the DIFFERENCE signal -
    the window's own ``left - right``, which is silent when the two
    channels carry the same thing and loud when the pan has moved the
    signal off centre.  ``balance_db`` is None when either channel is
    silent, because a silent channel has no balance to speak of.
    """
    samples = _decode(path, 2)
    window = _window(samples, start, duration)
    left = window[0::2]
    right = window[1::2]
    difference = array.array("h")
    for left_value, right_value in zip(left, right):
        difference.append(max(-32768, min(32767, left_value - right_value)))
    left_rms = _rms_db(left)
    right_rms = _rms_db(right)
    balance = None
    if left_rms is not None and right_rms is not None:
        balance = round(left_rms - right_rms, 2)
    return {
        "window_seconds": round(len(window) / SAMPLE_RATE, 3),
        "left_rms_db": left_rms,
        "right_rms_db": right_rms,
        "balance_db": balance,
        "difference_db": _rms_db(difference),
        "left_digital_zero_fraction": (
            round(sum(1 for value in left if value == 0) / len(left), 4)
            if left else None),
        "right_digital_zero_fraction": (
            round(sum(1 for value in right if value == 0) / len(right), 4)
            if right else None),
    }


# ── the render ───────────────────────────────────────────────────────


def render_timeline(project: Any, timeline: Any, out_path: str, *,
                    width: int = 1920, height: int = 1080) -> str:
    """Render the timeline's whole span to ``out_path``, and return it.

    Resolve's own render job, on the timeline as it stands.  The
    render settings are restored afterwards, and the job is deleted, so
    a measurement leaves no render queue behind it.
    """
    saved = project.GetCurrentRenderFormatAndCodec() or {}
    job_id = None
    try:
        project.SetCurrentRenderFormatAndCodec("mp4", "H.264")
        start = int(timeline.GetStartFrame())
        end = int(timeline.GetEndFrame())
        settings = {
            "TargetDir": os.path.dirname(os.path.abspath(out_path)),
            "CustomName": os.path.splitext(
                os.path.basename(out_path))[0],
            "MarkIn": start,
            "MarkOut": max(start, end - 1),
            "SelectAllFrames": False,
            "FormatWidth": width,
            "FormatHeight": height,
            "ExportAudio": True,
            "AudioCodec": "aac",
            "AudioBitDepth": 16,
            "AudioSampleRate": SAMPLE_RATE,
        }
        if not project.SetRenderSettings(settings):
            raise RuntimeError("SetRenderSettings refused")
        job_id = project.AddRenderJob()
        if not job_id:
            raise RuntimeError("AddRenderJob returned nothing")
        if not project.StartRendering([job_id], isInteractiveMode=False):
            raise RuntimeError("StartRendering refused")
        deadline = time.time() + 900
        while time.time() < deadline:
            if not project.IsRenderingInProgress():
                break
            time.sleep(1.0)
        else:
            project.StopRendering()
            raise RuntimeError("render timed out")
        status = project.GetRenderJobStatus(job_id) or {}
        if status.get("JobStatus") != "Complete":
            raise RuntimeError("render ended {!r}: {!r}".format(
                status.get("JobStatus"), status.get("Error")))
    finally:
        if job_id:
            try:
                project.DeleteRenderJob(job_id)
            except Exception:  # noqa: BLE001 - the job is already gone
                pass
        try:
            if saved.get("format") and saved.get("codec"):
                project.SetCurrentRenderFormatAndCodec(saved["format"],
                                                        saved["codec"])
        except Exception:  # noqa: BLE001 - the render settings are restored
            pass
    if not os.path.exists(out_path):
        raise RuntimeError("no render at {}".format(out_path))
    return out_path


# ── the verdict ──────────────────────────────────────────────────────


def gain_evidence(before_path: str, after_path: str,
                  windows: Mapping[str, Mapping[str, float]],
                  requested_db: float, *,
                  target: str = "",
                  tolerance_db: float = GAIN_TOLERANCE_DB,
                  mix_tolerance_db: float = MIX_TOLERANCE_DB) -> dict:
    """Judge a gain change by what the two renders say.

    ``windows`` names each window to measure: ``{"target": {"start": s,
    "duration": s}, "other": {...}}``.  The target window's measured
    change must be within ``tolerance_db`` of the requested dB, and
    every other window must hold its level within ``mix_tolerance_db``
    - the mix did not move.

    A window that is silent before AND after cannot show a change, and
    says so (``measurable: false``) rather than reporting a delta of
    zero: a silent window is not a window that stayed at its level.
    """
    readings = {}
    for name, window in windows.items():
        before = measure_window(before_path, window.get("start"),
                                window.get("duration"))
        after = measure_window(after_path, window.get("start"),
                               window.get("duration"))
        delta = None
        if before["rms_db"] is not None and after["rms_db"] is not None:
            delta = round(after["rms_db"] - before["rms_db"], 2)
        readings[name] = {"before": before, "after": after,
                          "delta_db": delta}

    target_reading = readings.get(target)
    measured = target_reading["delta_db"] if target_reading else None
    target_matches = (
        target_reading is not None
        and measured is not None
        and abs(measured - float(requested_db)) <= tolerance_db)

    moved = []
    for name, reading in readings.items():
        if name == target:
            continue
        if reading["delta_db"] is None:
            moved.append({"window": name, "delta_db": None,
                          "reason": "the window is silent in one render, "
                                    "so its level cannot be compared"})
            continue
        if abs(reading["delta_db"]) > mix_tolerance_db:
            moved.append({"window": name, "delta_db": reading["delta_db"],
                          "reason": "an untouched window moved"})
    return {
        "requested_db": float(requested_db),
        "measured_db": measured,
        "tolerance_db": tolerance_db,
        "mix_tolerance_db": mix_tolerance_db,
        "target_window": target,
        "target_matches_request": bool(target_matches),
        "mix_preserved": not moved,
        "windows_moved": moved,
        "readings": readings,
    }
