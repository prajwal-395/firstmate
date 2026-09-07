"""How loud the SPEECH is, over the ranges the edit really plays.

`step_5_02_audio_mix` used to carry a constant named
`SPEECH_LOUDNESS_IS_UNMEASURED`, and it named exactly one missing
thing: *"the separation a window delivers is speech loudness minus
`bed_level_after_gain_lufs`, and nothing in the pipeline measures the
loudness of the speech that plays. It would take one ffmpeg `loudnorm`
pass over the A-roll ranges `a_roll_assignments` names."*

This is that pass.  Step 1.05 measures prosody, step 1.04 measures
speech REGIONS, and neither reports a level; `music_measurement.loudness`
already runs the same pass on a whole music candidate, and this runs it
on a RANGE of the footage instead.

**Measured, exposed, and nothing more.**  The number this makes possible
is `separation_delivered_db` - the speech's own loudness minus where the
plan's clip gain puts the bed - which is arithmetic over two
measurements.  What separation a window OUGHT to deliver is not here and
must not be: `music_behavior.SEPARATION_TARGETS_DB` is empty, the master
loudness target is an open captain decision, and an engine-supplied
number would be a strength nobody chose arriving one level up
(AGENTS.md 10.4, 10.5).  On project 001, 0 of 8 windows meet the 18 dB
the check currently reads off the clip gain, the worst is +6.27 dB, and
the master is -21.72 LUFS against a -14 target.  Those are the captain's
(`mix-levels-need-an-owner`).

**Cost.**  One `loudnorm` pass per speech block, measured on 001:
**0.23 s wall clock** for a 2.4 s range of a 1920x1080 h264 MOV, and
about the same for a 10 s range - the pass is dominated by the seek and
the decode of a few seconds of audio, not by the block's length.  001
has 8 speech blocks, so about **1.8 s added to a run**.  A project with
40 speech blocks pays about 9 s.  It is measured, not assumed:
`MEASURED_COST` records it.

**An absence is admitted, never filled in.**  A block with no source
file, an unreadable file, or a range ffmpeg reports nothing for comes
back with `measured: False` and the reason - never a level of 0, which
would read as silence.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

MEASURED_COST = {
    "seconds_per_block": 0.23,
    "measured_on": "project 001, IMG_1816.MOV (1920x1080 h264), a 2.398s "
                   "range, 2026-08-30",
    "note": "the pass is dominated by the seek and a few seconds of audio "
            "decode rather than by the block's length; 001's 8 speech "
            "blocks cost about 1.8s of a run",
}

# The audio stream measured. An iPhone MOV carries several (AGENTS.md 5),
# and the first is the one Resolve links to the placed clip. Recorded on
# every reading rather than assumed by a reader.
SPEECH_AUDIO_STREAM = 0

# What this module deliberately does not answer.
NO_TARGET_IS_SUPPLIED = (
    "this measures what the speech IS, and the separation a window will "
    "deliver follows from it by subtraction. What separation a window "
    "OUGHT to deliver is not measured and not supplied: "
    "music_behavior.SEPARATION_TARGETS_DB is empty and the master "
    "loudness target is an open captain decision. A number invented here "
    "would be a strength nobody chose (AGENTS.md 10.4, 10.5)."
)

FFMPEG_TIMEOUT_SECONDS = 120


def _loudnorm_json(stderr: str) -> Optional[dict]:
    blob, depth = "", 0
    for line in (stderr or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            depth, blob = 1, "{"
            continue
        if depth:
            blob += stripped
            if stripped.startswith("}"):
                break
    if not blob:
        return None
    try:
        return json.loads(blob)
    except ValueError:
        return None


def measure_range(source_file: str, start: float, end: float,
                  stream: int = SPEECH_AUDIO_STREAM) -> dict:
    """Integrated loudness of one range of one file, or a stated absence.

    `{"measured": True, "integrated_lufs": ..., "true_peak_dbtp": ...,
    "loudness_range_lu": ..., "seconds": ..., "audio_stream": n}` or
    `{"measured": False, "reason": ...}`. Never a level of 0.
    """
    if not source_file:
        return {"measured": False,
                "reason": "the assignment names no source file"}
    if not os.path.exists(source_file):
        return {"measured": False,
                "reason": f"the source file is not on disk: {source_file}"}
    try:
        seconds = round(float(end) - float(start), 3)
    except (TypeError, ValueError):
        return {"measured": False,
                "reason": f"the range is not a pair of seconds: "
                          f"{start!r}-{end!r}"}
    if seconds <= 0:
        return {"measured": False,
                "reason": f"the range is {seconds}s long, so there is "
                          f"nothing to measure"}
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-nostats",
             "-ss", f"{max(0.0, float(start)):.3f}", "-t", f"{seconds:.3f}",
             "-i", source_file, "-map", f"0:a:{stream}",
             "-af", "loudnorm=print_format=json", "-f", "null", "-"],
            capture_output=True, encoding="utf-8",
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"measured": False, "reason": f"ffmpeg did not run: {exc}"}

    data = _loudnorm_json(result.stderr)
    if not data or "input_i" not in data:
        return {"measured": False,
                "reason": (f"ffmpeg loudnorm printed no summary for "
                           f"stream {stream} of this range")}
    try:
        integrated = float(data["input_i"])
    except (TypeError, ValueError):
        return {"measured": False,
                "reason": f"loudnorm reported {data.get('input_i')!r}"}
    # -inf / -70 is loudnorm's floor for a silent range. It is a real
    # measurement of silence, and it is said rather than rounded.
    reading = {
        "measured": True,
        "integrated_lufs": round(integrated, 2),
        "seconds": seconds,
        "audio_stream": stream,
    }
    for key, name in (("input_tp", "true_peak_dbtp"),
                      ("input_lra", "loudness_range_lu")):
        try:
            reading[name] = round(float(data[key]), 2)
        except (KeyError, TypeError, ValueError):
            pass
    return reading


def _segments(entry: dict) -> list:
    """The (source_file, in, out) ranges one assignment really plays."""
    segments = entry.get("video_segments")
    if isinstance(segments, list) and segments:
        return [(s.get("source_file"), s.get("video_in"), s.get("video_out"))
                for s in segments if isinstance(s, dict)]
    return []


def measure_speech_blocks(a_roll_assignments) -> dict:
    """`spine_block_position -> reading`, one loudnorm pass per range.

    An assignment cut from several segments is measured on its LONGEST
    one and says so: loudnorm cannot be run across a discontinuity, and
    averaging two integrated readings is not an integrated reading of
    the pair.
    """
    out = {}
    for entry in a_roll_assignments or []:
        if not isinstance(entry, dict):
            continue
        position = entry.get("spine_block_position")
        if position is None:
            continue
        ranges = [r for r in _segments(entry)
                  if r[0] and isinstance(r[1], (int, float))
                  and isinstance(r[2], (int, float))]
        if not ranges:
            out[position] = {
                "measured": False,
                "reason": "the assignment names no source range"}
            continue
        longest = max(ranges, key=lambda r: float(r[2]) - float(r[1]))
        reading = measure_range(*longest)
        if len(ranges) > 1:
            reading["segments_in_the_block"] = len(ranges)
            reading["measured_segment"] = {
                "source_file": longest[0],
                "video_in": longest[1], "video_out": longest[2]}
        out[position] = reading
    return out


def separation_delivered_db(speech_lufs, bed_after_gain_lufs):
    """How far the speech sits above the bed, in dB. None when unknown.

    Arithmetic over two measurements. It is not compared with anything:
    see :data:`NO_TARGET_IS_SUPPLIED`.
    """
    if not isinstance(speech_lufs, (int, float)):
        return None
    if not isinstance(bed_after_gain_lufs, (int, float)):
        return None
    return round(float(speech_lufs) - float(bed_after_gain_lufs), 2)
