"""Measurements taken on the RENDERED file, not on the plan that made it.

Everything here judges the master ffmpeg produced.  That is the whole
point: a plan can declare a limiter, a fill and a duck, and none of it
reaches the picture or the mix unless something applied it.  A gate that
reads the manifest cannot tell the difference; these can.

Four of the baseline-craft properties live here.  P1 (the picture fills
the delivery frame, and one video has one geometry) and P4 (deliverable
loudness without clipping) FAIL a build.  P2 (somewhere in the frame
there is colour) and P3 (where someone speaks, the speech is above the
bed) REPORT A NUMBER and do not fail, because their thresholds are open
captain decisions - see the two `_GATES` booleans below, which are the
whole of what promoting them costs.
"""

import json
import subprocess
import os
import statistics
import tempfile
import re
from dataclasses import dataclass
from typing import Any, Iterator, List, Optional, Sequence

try:
    from library.tools.spine_contract import MAX_DECLARED_BLACK_BEAT_SECONDS
except ImportError:  # imported as a top-level module from library/tools
    from spine_contract import MAX_DECLARED_BLACK_BEAT_SECONDS

try:
    from library.tools.framing_intent import DEFAULT_FRAMING_INTENT, FILL
except ImportError:  # imported as a top-level module from library/tools
    from framing_intent import DEFAULT_FRAMING_INTENT, FILL

# Slack when matching detected black against a declared beat. blackdetect
# reports whole-frame timestamps, so the segment it reports for a beat can
# run a frame wider than the gap the manifest planned; 50ms covers a frame
# at any framerate the pipeline ships. Without it a beat declared at
# exactly MAX_DECLARED_BLACK_BEAT_SECONDS would pass compile_manifest and
# then fail here, one render too late.
DECLARED_BEAT_TOLERANCE_SECONDS = 0.05


@dataclass
class RenderQAResult:
    metric: str
    passed: bool
    value: Any  # measured value
    threshold: Any  # pass/fail threshold
    severity: str  # "error", "warning", "info"
    detail: str

# ── P4: deliverable at platform loudness, without clipping ──
#
# The tolerance was +/-2.  At +/-2 a master can sit 2 dB under target and
# pass, and no platform turns a quiet file up - it plays quieter than the
# video before it in the feed, which is a retention cost before a frame is
# judged.  +/-1 is the tolerance the three destination platforms'
# normalisation actually leaves.
DEFAULT_LUFS_TOLERANCE = 1.0

# The ceiling is not a convention: it is the manifest's own
# `audio_mix.master_limiter.threshold_db`, which every compiled manifest
# so far declares as -1.0 with `enabled: true`.  A master above it is a
# master whose declared limiter did not run, and it clips on any lossy
# re-encode a platform performs.
DEFAULT_TRUE_PEAK_CEILING_DBTP = -1.0


def measure_lufs(video_path: str, target_lufs: float = -14.0,
                 tolerance: float = DEFAULT_LUFS_TOLERANCE,
                 true_peak_ceiling: float = DEFAULT_TRUE_PEAK_CEILING_DBTP) -> RenderQAResult:
    """Integrated loudness AND true peak, both as pass/fail (P4).

    The peak half used to be advisory in the weakest possible way: it set
    `severity = "warning"` and only when the LUFS check had already
    passed, and it never touched `passed`.  On project 001 the LUFS check
    failed, so a +1.85 dBTP master - 2.85 dB over the limiter the manifest
    itself declared - was printed inside a detail string and dropped.

    It gates rather than warning because it measures the DELIVERED FILE.
    The open decision underneath the mix is about the route by which
    levels reach Resolve; this number is downstream of every route, and a
    master that clips is a defect whichever one is chosen.
    """
    try:
        cmd = [
            'ffmpeg', '-i', video_path, '-af', 'loudnorm=print_format=json',
            '-f', 'null', '-'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        
        # parse json from stderr (loudnorm filter outputs json to stderr)
        output = result.stderr
        json_str = ""
        in_json = False
        for line in output.splitlines():
            if line.strip() == "{":
                in_json = True
            if in_json:
                json_str += line + "\n"
            if line.strip() == "}":
                break
                
        if not json_str:
            return RenderQAResult("lufs", False, None, target_lufs, "error", "Failed to parse loudnorm output")
            
        data = json.loads(json_str)
        input_i = float(data.get("input_i", 0))
        input_tp = float(data.get("input_tp", 0))
        
        lufs_ok = abs(input_i - target_lufs) <= tolerance
        peak_ok = input_tp <= true_peak_ceiling
        passed = lufs_ok and peak_ok

        faults = []
        if not lufs_ok:
            faults.append(
                f"integrated {input_i:.2f} LUFS is "
                f"{abs(input_i - target_lufs):.2f} dB off the {target_lufs:.1f} "
                f"target (tolerance +/-{tolerance:.1f})"
            )
        if not peak_ok:
            faults.append(
                f"true peak {input_tp:+.2f} dBTP is "
                f"{input_tp - true_peak_ceiling:.2f} dB over the "
                f"{true_peak_ceiling:.1f} dBTP ceiling - the declared "
                f"limiter did not reach the master"
            )

        detail = f"LUFS: {input_i:.2f}, True Peak: {input_tp:.2f}"
        if faults:
            detail += " - " + "; ".join(faults)

        return RenderQAResult(
            metric="lufs",
            passed=passed,
            value={"input_i": input_i, "input_tp": input_tp,
                   "lufs_passed": lufs_ok, "true_peak_passed": peak_ok},
            threshold={"target_lufs": target_lufs, "tolerance": tolerance,
                       "true_peak_ceiling": true_peak_ceiling},
            severity="error" if not passed else "info",
            detail=detail
        )
    except Exception as e:
        return RenderQAResult("lufs", False, str(e), target_lufs, "error", f"Error measuring LUFS: {e}")

def segment_is_declared(segment: dict, declared_beats: Optional[List] = None,
                        max_declared_seconds: float = MAX_DECLARED_BLACK_BEAT_SECONDS) -> bool:
    """True when a detected black segment is a beat the plan declared.

    The plan declares beats on spine blocks (see
    `library/tools/spine_contract.py`); `declared_beats` is what
    `declared_black_beat_ranges` returned for the manifest that produced
    this render.  A segment is excused only when it sits inside one of
    those ranges AND runs no longer than a deliberate beat may - so a
    render that turned a declared 0.4s hold into three seconds of black
    is still a defect, and black anywhere else always is.
    """
    if not declared_beats:
        return False
    if segment["duration"] > max_declared_seconds + DECLARED_BEAT_TOLERANCE_SECONDS:
        return False
    return any(
        start - DECLARED_BEAT_TOLERANCE_SECONDS <= segment["start"]
        and segment["end"] <= end + DECLARED_BEAT_TOLERANCE_SECONDS
        for start, end in declared_beats
    )


def detect_black_frames(video_path: str, min_duration: float = 0.5,
                        declared_beats: Optional[List] = None,
                        max_declared_seconds: float = MAX_DECLARED_BLACK_BEAT_SECONDS) -> RenderQAResult:
    """Detect sustained black frames using ffmpeg blackdetect.

    Black the plan deliberately declared is not a defect - the captain's
    ruling is that a short, defensible hold on black is allowed.  Pass the
    declared beat ranges and each segment is tagged `declared`; only the
    undeclared ones fail the check.  With no ranges passed, every black
    segment fails, which is what an unplanned render deserves.
    """
    try:
        cmd = [
            'ffmpeg', '-i', video_path,
            '-vf', f'blackdetect=d={min_duration}:pix_th=0.10',
            '-f', 'null', '-'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        
        black_segments = []
        for line in result.stderr.splitlines():
            # Example: [blackdetect @ 0x123] black_start:1 black_end:2 black_duration:1
            if "black_duration" in line:
                m_start = re.search(r'black_start:([0-9.]+)', line)
                m_end = re.search(r'black_end:([0-9.]+)', line)
                m_dur = re.search(r'black_duration:([0-9.]+)', line)
                if m_start and m_end and m_dur:
                    segment = {
                        "start": float(m_start.group(1)),
                        "end": float(m_end.group(1)),
                        "duration": float(m_dur.group(1))
                    }
                    segment["declared"] = segment_is_declared(
                        segment, declared_beats, max_declared_seconds
                    )
                    black_segments.append(segment)

        undeclared = [s for s in black_segments if not s["declared"]]
        declared_count = len(black_segments) - len(undeclared)
        passed = len(undeclared) == 0

        if undeclared:
            detail = f"Found {len(undeclared)} undeclared black frame segments"
        elif declared_count:
            detail = (f"No undeclared black frames "
                      f"({declared_count} declared black beat(s) allowed through)")
        else:
            detail = "No black frames detected"

        return RenderQAResult(
            metric="black_frames",
            passed=passed,
            value=black_segments,
            threshold={"min_duration": min_duration,
                       "max_declared_seconds": max_declared_seconds},
            severity="error" if not passed else "info",
            detail=detail
        )
    except Exception as e:
        return RenderQAResult("black_frames", False, str(e), min_duration, "error", f"Error detecting black frames: {e}")

def detect_freeze_frames(video_path: str, min_duration: float = 1.0) -> RenderQAResult:
    """Detect frozen/stuck frames using ffmpeg freezedetect."""
    try:
        cmd = [
            'ffmpeg', '-i', video_path,
            '-vf', f'freezedetect=n=0.003:d={min_duration}',
            '-f', 'null', '-'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        
        freeze_segments = []
        current_freeze = {}
        for line in result.stderr.splitlines():
            if "lavfi.freezedetect.freeze_start" in line:
                m = re.search(r'freeze_start: ([0-9.]+)', line)
                if m:
                    current_freeze["start"] = float(m.group(1))
            elif "lavfi.freezedetect.freeze_duration" in line:
                m = re.search(r'freeze_duration: ([0-9.]+)', line)
                if m:
                    current_freeze["duration"] = float(m.group(1))
            elif "lavfi.freezedetect.freeze_end" in line:
                m = re.search(r'freeze_end: ([0-9.]+)', line)
                if m:
                    current_freeze["end"] = float(m.group(1))
                    if "start" in current_freeze and "duration" in current_freeze:
                        freeze_segments.append(dict(current_freeze))
                    current_freeze = {}

        passed = len(freeze_segments) == 0
        detail = f"Found {len(freeze_segments)} frozen frame segments" if not passed else "No freeze frames detected"
        
        return RenderQAResult(
            metric="freeze_frames",
            passed=passed,
            value=freeze_segments,
            threshold=min_duration,
            severity="error" if not passed else "info",
            detail=detail
        )
    except Exception as e:
        return RenderQAResult("freeze_frames", False, str(e), min_duration, "error", f"Error detecting freeze frames: {e}")

def analyze_color_histogram(video_path: str, sample_count: int = 5) -> RenderQAResult:
    """Sample frames at key moments and check exposure.

    This used to also carry a saturation floor, `SATAVG < 10`.  It is gone
    and `measure_chroma_presence` (P2) replaces it, because frame-mean
    saturation cannot be the statistic at the channel layer: the captain's
    own two reference frames measure 32.7 and 3.5, a 9.3x spread, and the
    Punch Card reference - a grey industrial gym with one red accent,
    selected as what that series should look like - would have been
    REJECTED by this floor.  It also measured the whole frame, which on a
    letterboxed master is mostly bar.  Two checks answering the same
    question with different statistics is one check too many, so this one
    keeps exposure and gives colour away.
    """
    # First get duration
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        dur_res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        duration = float(dur_res.stdout.strip())
    except Exception as e:
        return RenderQAResult("color_histogram", False, str(e), None, "error", "Could not get duration")

    sample_points = [duration * (i + 1) / (sample_count + 1) for i in range(sample_count)]
    issues = []
    frames_data = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, t in enumerate(sample_points):
            img_path = os.path.join(tmpdir, f'frame_{i}.png')
            subprocess.run(['ffmpeg', '-y', '-ss', str(t), '-i', video_path, '-vframes', '1', '-f', 'image2', img_path], capture_output=True, timeout=15)
            
            if not os.path.exists(img_path):
                continue

            try:
                cmd = ['ffprobe', '-f', 'lavfi', '-i', f'movie={img_path},signalstats', '-show_entries', 'frame_tags=lavfi.signalstats.YAVG,lavfi.signalstats.YMIN,lavfi.signalstats.YMAX,lavfi.signalstats.SATAVG', '-of', 'default=noprint_wrappers=1:nokey=1']
                sig_res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
                lines = sig_res.stdout.strip().splitlines()
                if len(lines) >= 4:
                    yavg = float(lines[0])
                    # order of tags might vary, best to use json or precise formatting
            except Exception:
                pass
            
            # Using a more robust signalstats query
            try:
                cmd = ['ffprobe', '-f', 'lavfi', '-i', f'movie={img_path},signalstats', '-show_entries', 'frame_tags', '-print_format', 'json']
                sig_res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
                data = json.loads(sig_res.stdout)
                tags = data.get("frames", [{}])[0].get("tags", {})
                yavg = float(tags.get("lavfi.signalstats.YAVG", 0))
                satavg = float(tags.get("lavfi.signalstats.SATAVG", 0))
                
                frames_data.append({"time": t, "yavg": yavg, "satavg": satavg})
                if yavg < 16:
                    issues.append(f"Extremely dark frame at {t:.2f}s (mean={yavg:.1f})")
                elif yavg > 240:
                    issues.append(f"Extremely bright frame at {t:.2f}s (mean={yavg:.1f})")

                # satavg is recorded, not judged. See the docstring: the
                # colour question is measure_chroma_presence's now.
            except Exception as e:
                pass

    passed = len(issues) == 0
    detail = "Color levels look normal" if passed else "; ".join(issues)
    
    return RenderQAResult(
        metric="color_histogram",
        passed=passed,
        value=frames_data,
        threshold={"min_y": 16, "max_y": 240},
        severity="warning" if not passed else "info",
        detail=detail
    )

# ─────────────────────────────────────────────────────────────
# Baseline craft, measured on the render
#
# P1 and P2 both sample the master and both need the same two things: the
# frame size, and a stream of raw frames.  The helpers below are shared so
# there is one answer to "what did ffmpeg hand us" rather than two.
# ─────────────────────────────────────────────────────────────

# A row (or a pixel) is LIT at or above this mean luma.  It is the
# pipeline's own black threshold, the same one `detect_black_frames`
# passes to blackdetect as `pix_th=0.10` (0.10 * 255 ~ 25 on a full-range
# scale, 12 on the 16..235 video range this footage carries).  Sharing it
# means "black" means one thing across this module.
LIT_LUMA_THRESHOLD = 12.0

# 1 Hz is enough to ESTABLISH a defect at the magnitudes measured on 001,
# but a gate wants finer: a single filled B-roll cutaway shorter than a
# second must not slip between two samples.  2 Hz is the compromise -
# double the evidence, still ~110 frames on a 55s master.
DEFAULT_SAMPLE_FPS = 2.0

# ── P1 targets ──
# The frame FILLS by default (library/tools/framing_intent.py), so a
# master whose picture occupies a third of the delivery frame is a
# defect. 0.95 rather than 1.00 leaves room for a genuinely dark row at
# the very top or bottom of a correctly filled picture.
MIN_FILL_ROW_FRACTION = 0.95

# One video, one geometry. This half is sourced from the ABSENCE of any
# mechanism that would deliberately vary the picture size mid-cut: no
# document describes it and nothing offers it as a choice. Project 001
# changed geometry twice in 55 seconds - the letterbox heuristic keyed on
# subject visibility, so A-roll (a face) letterboxed at 34% and B-roll (no
# face) filled at 97% - and nothing noticed.
MAX_ROW_FRACTION_SPREAD = 0.05

# ── P2: the gate switch ──
# The chroma floor is an OPEN CAPTAIN DECISION
# (`vep-craft-reference-decomposition-decision-craft-chroma-floor-value`):
# whether a render fails for being grey, and at what number. The shape is
# settled by measurement - the 99th percentile of per-pixel chroma, not a
# mean - but the value is taste, and it decides whether a deliberately
# monochrome look is rejected. So P2 reports its number and does not fail.
# Promoting it is this boolean plus a `chroma_floor` at the call site.
CHROMA_PRESENCE_GATES = False

# Fraction of sampled seconds that must clear the floor once one exists.
DEFAULT_CHROMA_PASS_FRACTION = 0.9

# ── P3: the gate switch ──
# The mix NOW HAS a delivery route. It did not when this switch was
# written: `SetProperty("Volume")` returns False on Resolve 21, the
# Fairlight preset failed, and the level automation the plan wrote
# reached nothing - so the check could not pass whatever anyone
# configured, and a gate that must fail teaches everyone to ignore the
# report. The OTIO round trip (AGENTS.md section 5, "The mix goes
# through OTIO") closed that, and 001 now renders with every planned dB
# measurably present.
#
# The switch stays False anyway, because promoting it is a separate
# captain decision about what a FAILING mix should cost a run, not a
# consequence of the route existing. Promoting it is this boolean.
SPEECH_ABOVE_BED_GATES = False

# A `silent` window is judged by how far its music sits below the median
# of the non-silent windows. 30 dB rather than the plan's own 90 because a
# least-squares fit cannot resolve further; 90 is the plan's ambition, 30
# is what the method can honestly assert.
SILENT_WINDOW_MARGIN_DB = 30.0

# Block types that carry a voice. A non-speech pacing beat has nothing to
# be above, so its window is measured and reported but not judged.
SPEECH_BEARING_BLOCK_TYPES = ("speech", "hook")


def _probe_video_size(video_path: str) -> Optional[tuple]:
    """(width, height) of the first video stream, or None."""
    cmd = ['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
           '-show_entries', 'stream=width,height', '-of', 'json', video_path]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    streams = json.loads(res.stdout or "{}").get("streams", [])
    if not streams or "width" not in streams[0]:
        return None
    return int(streams[0]["width"]), int(streams[0]["height"])


def _stream_raw_frames(video_path: str, pix_fmt: str, planes: int,
                       width: int, height: int,
                       sample_fps: float) -> Iterator["object"]:
    """Yield sampled frames as (planes, height, width) uint8 arrays.

    Streamed one frame at a time rather than read whole: a 55-second
    1080x1920 master at 2 Hz in yuv444p is 680 MB if you slurp it, and
    every consumer here reduces each frame to a handful of scalars.
    """
    import numpy as np

    frame_bytes = planes * width * height
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-i', video_path,
           '-vf', f'fps={sample_fps},format={pix_fmt}',
           '-f', 'rawvideo', '-pix_fmt', pix_fmt, '-']
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(
                planes, height, width)
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait(timeout=30)


def measure_frame_occupancy(
        video_path: str,
        framing_intents: Optional[Sequence[float]] = None,
        sample_fps: float = DEFAULT_SAMPLE_FPS,
        min_fill_fraction: float = MIN_FILL_ROW_FRACTION,
        max_spread: float = MAX_ROW_FRACTION_SPREAD) -> RenderQAResult:
    """P1: the picture fills the delivery frame, and one video has one geometry.

    Samples the master, takes the per-row mean luma of each frame, and
    counts the rows at or above `LIT_LUMA_THRESHOLD`.  That count over the
    frame height is the fraction of the delivery frame the picture
    occupies.

    Two assertions, and they fail for different reasons:

    * **Fill** - the median occupancy is at least `min_fill_fraction`.
      Applies only where every clip declared FILL, which is what nothing
      declaring anything resolves to.  A series that declares
      `framing_intent: 0.0` wants bars and gets no floor; there is no
      channel-wide number for how much of the frame a deliberately inset
      picture should occupy, and inventing one here would answer a brand
      decision.
    * **Consistency** - the spread between the least and most occupied
      sampled frame is at most `max_spread`.  Applies whenever the video
      declared ONE intent, whatever it was.  A viewer cannot name "framing
      intent" but can see the picture jump size twice in 55 seconds.

    `framing_intents` is the set of per-clip declarations the manifest
    carries (compile_manifest writes `framing_intent` on every conformed
    clip).  None means nothing was declared, which resolves to the default.
    """
    try:
        import numpy as np
    except ImportError as e:  # pragma: no cover - numpy is a hard dependency
        return RenderQAResult("frame_occupancy", False, str(e), None,
                              "error", f"numpy unavailable: {e}")

    try:
        size = _probe_video_size(video_path)
        if not size:
            return RenderQAResult("frame_occupancy", False, None, None,
                                  "error", "No video stream found")
        width, height = size

        fractions = []
        bands = []
        for frame in _stream_raw_frames(video_path, 'gray', 1,
                                        width, height, sample_fps):
            row_mean = frame[0].astype(np.float32).mean(axis=1)
            lit = np.flatnonzero(row_mean >= LIT_LUMA_THRESHOLD)
            fractions.append(lit.size / height)
            bands.append((int(lit.min()), int(lit.max())) if lit.size
                         else (None, None))

        if not fractions:
            return RenderQAResult("frame_occupancy", False, None, None,
                                  "error",
                                  "Could not sample any frame from the render")

        declared = sorted({float(i) for i in framing_intents}) if framing_intents \
            else [float(DEFAULT_FRAMING_INTENT)]
        median_fraction = float(statistics.median(fractions))
        spread = float(max(fractions) - min(fractions))

        faults = []
        fill_applies = all(i == FILL for i in declared)
        if fill_applies and median_fraction < min_fill_fraction:
            faults.append(
                f"the picture occupies {median_fraction:.1%} of the frame "
                f"height (floor {min_fill_fraction:.0%}) in a video whose "
                f"framing declares FILL - it is letterboxed and nothing "
                f"asked for bars"
            )

        one_geometry = len(declared) == 1
        if one_geometry and spread > max_spread:
            lo = min(range(len(fractions)), key=lambda i: fractions[i])
            hi = max(range(len(fractions)), key=lambda i: fractions[i])
            faults.append(
                f"the picture changes size within the video: "
                f"{fractions[lo]:.1%} at {lo / sample_fps:.1f}s vs "
                f"{fractions[hi]:.1%} at {hi / sample_fps:.1f}s "
                f"(spread {spread:.2f}, bound {max_spread:.2f}) - one video "
                f"has one geometry"
            )

        detail = (f"picture occupies {median_fraction:.1%} of the frame "
                  f"(spread {spread:.2f} over {len(fractions)} samples)")
        if faults:
            detail += " - " + "; ".join(faults)

        return RenderQAResult(
            metric="frame_occupancy",
            passed=not faults,
            value={
                "median_lit_fraction": round(median_fraction, 4),
                "min_lit_fraction": round(float(min(fractions)), 4),
                "max_lit_fraction": round(float(max(fractions)), 4),
                "spread": round(spread, 4),
                "frames_sampled": len(fractions),
                "picture_band_first_frame": bands[0],
                "declared_framing_intents": declared,
            },
            threshold={"min_fill_fraction": min_fill_fraction,
                       "max_spread": max_spread,
                       "lit_luma_threshold": LIT_LUMA_THRESHOLD,
                       "fill_floor_applies": fill_applies,
                       "consistency_applies": one_geometry},
            severity="error" if faults else "info",
            detail=detail,
        )
    except Exception as e:
        return RenderQAResult("frame_occupancy", False, str(e), None,
                              "error", f"Error measuring frame occupancy: {e}")


def measure_chroma_presence(
        video_path: str,
        chroma_floor: Optional[float] = None,
        min_pass_fraction: float = DEFAULT_CHROMA_PASS_FRACTION,
        sample_fps: float = DEFAULT_SAMPLE_FPS,
        gate: bool = CHROMA_PRESENCE_GATES) -> RenderQAResult:
    """P2: somewhere in the frame there is colour.

    Per sampled frame, the 99th percentile of per-pixel chroma
    ``sqrt((U-128)^2 + (V-128)^2)`` over LIT pixels only - so letterbox
    bars and true black cannot dilute the answer, which is the second
    thing wrong with a whole-frame mean.

    The percentile is the point.  A mean measures the PALETTE, which is
    series taste; the 99th percentile measures whether colour exists
    anywhere, which is the channel-wide claim.  A deliberately desaturated
    look with one hot accent passes it; a globally grey frame does not.

    **This reports and does not fail.**  `chroma_floor` is supplied by the
    caller and there is no default, because the number is an open captain
    decision.  With no floor there is nothing to judge and the value is
    the whole output.
    """
    try:
        import numpy as np
    except ImportError as e:  # pragma: no cover - numpy is a hard dependency
        return RenderQAResult("chroma_presence", True, str(e), None,
                              "warning", f"numpy unavailable: {e}")

    try:
        size = _probe_video_size(video_path)
        if not size:
            return RenderQAResult("chroma_presence", True, None, chroma_floor,
                                  "warning", "No video stream found")
        width, height = size

        p99s = []
        for frame in _stream_raw_frames(video_path, 'yuv444p', 3,
                                        width, height, sample_fps):
            y = frame[0].astype(np.float32)
            u = frame[1].astype(np.float32)
            v = frame[2].astype(np.float32)
            chroma = np.sqrt((u - 128.0) ** 2 + (v - 128.0) ** 2)
            lit = chroma[y >= LIT_LUMA_THRESHOLD]
            if lit.size:
                p99s.append(float(np.percentile(lit, 99)))

        if not p99s:
            return RenderQAResult("chroma_presence", True, None, chroma_floor,
                                  "warning",
                                  "Could not sample any lit pixel from the render")

        median_p99 = float(statistics.median(p99s))
        value = {
            "median_p99_chroma": round(median_p99, 2),
            "min_p99_chroma": round(min(p99s), 2),
            "max_p99_chroma": round(max(p99s), 2),
            "frames_sampled": len(p99s),
        }

        if chroma_floor is None:
            return RenderQAResult(
                metric="chroma_presence",
                passed=True,
                value=value,
                threshold={"chroma_floor": None, "gates": gate},
                severity="info",
                detail=(f"p99 chroma over lit pixels: median {median_p99:.1f} "
                        f"(min {min(p99s):.1f}, max {max(p99s):.1f}) - "
                        f"no floor declared, reporting only"),
            )

        clearing = [p for p in p99s if p >= chroma_floor]
        pass_fraction = len(clearing) / len(p99s)
        meets = pass_fraction >= min_pass_fraction
        value["pass_fraction"] = round(pass_fraction, 4)
        value["meets_floor"] = meets

        detail = (f"p99 chroma over lit pixels: median {median_p99:.1f}; "
                  f"{len(clearing)} of {len(p99s)} samples clear the "
                  f"{chroma_floor:.0f} floor ({pass_fraction:.0%}, "
                  f"needs {min_pass_fraction:.0%})")
        if not meets and not gate:
            detail += " - REPORTED ONLY, the chroma floor is an open decision"

        return RenderQAResult(
            metric="chroma_presence",
            passed=meets if gate else True,
            value=value,
            threshold={"chroma_floor": chroma_floor,
                       "min_pass_fraction": min_pass_fraction,
                       "gates": gate},
            severity=("error" if gate else "warning") if not meets else "info",
            detail=detail,
        )
    except Exception as e:
        return RenderQAResult("chroma_presence", True, str(e), chroma_floor,
                              "warning", f"Error measuring chroma: {e}")


def _decode_mono(path: str, sample_rate: int = 48000):
    """Decode a media file to mono float32 at `sample_rate`."""
    import numpy as np

    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-i', path,
           '-ac', '1', '-ar', str(sample_rate), '-f', 'f32le', '-']
    res = subprocess.run(cmd, capture_output=True, timeout=300)
    return np.frombuffer(res.stdout, dtype='<f4')


def _db(x: float) -> float:
    import numpy as np
    return float(20.0 * np.log10(max(float(x), 1e-12)))


def measure_speech_above_bed(
        video_path: str,
        music_path: str,
        music_automation: Sequence[dict],
        spine_blocks: Optional[Sequence[dict]] = None,
        sample_rate: int = 48000,
        silent_margin_db: float = SILENT_WINDOW_MARGIN_DB,
        gate: bool = SPEECH_ABOVE_BED_GATES) -> RenderQAResult:
    """P3: where someone speaks, the speech is above the bed.

    For each window in `audio_mix.music_automation`, least-squares fits the
    music against the master (``g = <music, mix> / <music, music>``).  The
    music's contribution to that window is ``g * rms(music)``; everything
    else - speech, SFX, ambience - is ``rms(mix - g * music)``.  The
    difference in dB is what the ear judges, and the correlation is
    reported beside it as a confidence.

    **The targets are the manifest's own numbers**, not a convention and
    not taste: `background` -> +18 dB, `prominent` -> +6 dB, from
    `audio_mix.track_levels` against a speech reference of 0 dB.  A
    `silent` window is judged on the music's CONTRIBUTION, `silent_margin_db`
    below the median of the non-silent windows - the gain column shows
    whether the automation ran, the contribution column shows whether the
    viewer can hear it, and the second is the one that matters.

    Only speech-bearing blocks are judged; the rest are measured and
    reported.  `spine_blocks` supplies `block_type` by position - read
    ONLY that from it.  The behaviour is read from `music_automation`,
    which is the half carrying the dB this measurement is judged against;
    `_spine_blocks` carries the same word in the same vocabulary
    (`library/tools/music_behavior.py`) but no level.

    **This reports and does not fail** - see `SPEECH_ABOVE_BED_GATES`.
    """
    try:
        import numpy as np
    except ImportError as e:  # pragma: no cover - numpy is a hard dependency
        return RenderQAResult("speech_above_bed", True, str(e), None,
                              "warning", f"numpy unavailable: {e}")

    try:
        if not music_automation:
            return RenderQAResult("speech_above_bed", True, None, None, "info",
                                  "No music automation planned - nothing to measure")

        block_type_by_position = {}
        for block in (spine_blocks or []):
            if "position" in block:
                block_type_by_position[str(block["position"])] = \
                    block.get("block_type")

        mix = _decode_mono(video_path, sample_rate).astype(np.float64)
        music = _decode_mono(music_path, sample_rate).astype(np.float64)
        if mix.size == 0 or music.size == 0:
            return RenderQAResult("speech_above_bed", True, None, None,
                                  "warning",
                                  "Could not decode the master or the music bed")

        windows = []
        for w in music_automation:
            a = int(float(w.get("timeline_start", 0.0)) * sample_rate)
            b = int(float(w.get("timeline_end", 0.0)) * sample_rate)
            x = mix[a:min(b, mix.size)]
            y = music[a:min(b, music.size)]
            n = min(x.size, y.size)
            if n < sample_rate // 10:  # under 100 ms: nothing to fit
                continue
            x, y = x[:n], y[:n]
            denom = float(y @ y)
            if denom <= 0:
                continue
            g = float(y @ x) / denom
            residual = x - g * y
            music_db = _db(abs(g) * float(np.sqrt(np.mean(y ** 2))))
            other_db = _db(float(np.sqrt(np.mean(residual ** 2))))
            corr_denom = float(np.sqrt((x @ x) * denom))
            behaviour = w.get("music_behavior")
            position = w.get("spine_block_position")
            windows.append({
                "timeline_start": round(float(w.get("timeline_start", 0.0)), 3),
                "timeline_end": round(float(w.get("timeline_end", 0.0)), 3),
                "music_behavior": behaviour,
                "target_level_db": w.get("target_level_db"),
                "block_type": block_type_by_position.get(str(position)),
                "music_in_mix_db": round(music_db, 2),
                "non_music_db": round(other_db, 2),
                "margin_db": round(other_db - music_db, 2),
                "fitted_gain_db": round(_db(abs(g)), 2),
                "correlation": round(
                    float(x @ y) / corr_denom if corr_denom > 0 else 0.0, 3),
            })

        if not windows:
            return RenderQAResult("speech_above_bed", True, None, None,
                                  "warning",
                                  "No music automation window was long enough to fit")

        non_silent = [w["music_in_mix_db"] for w in windows
                      if w["music_behavior"] != "silent"]
        silent_reference = (statistics.median(non_silent) if non_silent
                            else None)

        # The targets are the plan's own dB, sign-flipped: a bed planned at
        # -18 dB against a 0 dB speech reference is a bed 18 dB DOWN.
        for w in windows:
            if w["music_behavior"] == "silent":
                w["required_margin_db"] = None
                continue
            target = w.get("target_level_db")
            w["required_margin_db"] = abs(float(target)) if target is not None \
                else None

        judged, failures = [], []
        for w in windows:
            if w.get("block_type") not in SPEECH_BEARING_BLOCK_TYPES:
                w["judged"] = False
                continue
            w["judged"] = True
            if w["music_behavior"] == "silent":
                if silent_reference is None:
                    w["judged"] = False
                    continue
                ok = w["music_in_mix_db"] <= silent_reference - silent_margin_db
            else:
                need = w["required_margin_db"]
                if need is None:
                    w["judged"] = False
                    continue
                ok = w["margin_db"] >= need
            w["meets_plan"] = ok
            judged.append(w)
            if not ok:
                failures.append(w)

        meets = not failures
        detail = (f"{len(judged) - len(failures)} of {len(judged)} "
                  f"speech-bearing windows meet the margin the plan itself "
                  f"declared")
        if failures:
            worst = min(failures,
                        key=lambda w: (w["margin_db"] - (w["required_margin_db"] or 0)))
            detail += (f"; worst {worst['timeline_start']:.2f}-"
                       f"{worst['timeline_end']:.2f}s planned "
                       f"{worst['music_behavior']}, speech is "
                       f"{worst['margin_db']:+.1f} dB over the bed")
            if not gate:
                detail += (" - REPORTED ONLY, the mix has no delivery route "
                           "(open captain decision)")

        return RenderQAResult(
            metric="speech_above_bed",
            passed=meets if gate else True,
            value={"windows": windows,
                   "judged": len(judged),
                   "failing": len(failures),
                   "silent_reference_db": (round(silent_reference, 2)
                                           if silent_reference is not None
                                           else None)},
            threshold={"targets": "audio_mix.track_levels, as planned",
                       "silent_margin_db": silent_margin_db,
                       "speech_bearing_block_types":
                           list(SPEECH_BEARING_BLOCK_TYPES),
                       "gates": gate},
            severity=("error" if gate else "warning") if not meets else "info",
            detail=detail,
        )
    except Exception as e:
        return RenderQAResult("speech_above_bed", True, str(e), None,
                              "warning", f"Error measuring the mix: {e}")


def verify_resolution(video_path: str, expected_width: int = 1080, expected_height: int = 1920) -> RenderQAResult:
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'stream=width,height', '-of', 'json', video_path]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        data = json.loads(res.stdout)
        
        video_streams = [s for s in data.get("streams", []) if "width" in s]
        if not video_streams:
            return RenderQAResult("resolution", False, None, f"{expected_width}x{expected_height}", "error", "No video stream found")
            
        w = int(video_streams[0]["width"])
        h = int(video_streams[0]["height"])
        
        passed = (w == expected_width and h == expected_height)
        return RenderQAResult(
            metric="resolution",
            passed=passed,
            value={"width": w, "height": h},
            threshold={"expected_width": expected_width, "expected_height": expected_height},
            severity="error" if not passed else "info",
            detail=f"Resolution is {w}x{h}"
        )
    except Exception as e:
        return RenderQAResult("resolution", False, str(e), None, "error", f"Error: {e}")

def verify_framerate(video_path: str, expected_fps: float = 30.0, tolerance: float = 1.0) -> RenderQAResult:
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'stream=r_frame_rate', '-of', 'json', video_path]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        data = json.loads(res.stdout)
        
        video_streams = [s for s in data.get("streams", []) if "r_frame_rate" in s]
        if not video_streams:
            return RenderQAResult("framerate", False, None, expected_fps, "error", "No video stream found")
            
        r_frame_rate = video_streams[0]["r_frame_rate"]
        num, den = map(int, r_frame_rate.split('/'))
        fps = num / max(den, 1)
        
        passed = abs(fps - expected_fps) <= tolerance
        return RenderQAResult(
            metric="framerate",
            passed=passed,
            value=fps,
            threshold=expected_fps,
            severity="error" if not passed else "info",
            detail=f"Framerate is {fps:.2f}fps"
        )
    except Exception as e:
        return RenderQAResult("framerate", False, str(e), None, "error", f"Error: {e}")

def verify_duration(video_path: str, expected_seconds: float, tolerance_pct: float = 10.0) -> RenderQAResult:
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'json', video_path]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        data = json.loads(res.stdout)
        
        duration = float(data.get("format", {}).get("duration", 0))
        
        drift = abs(duration - expected_seconds)
        drift_pct = (drift / expected_seconds) * 100 if expected_seconds > 0 else 0
        
        passed = drift_pct <= tolerance_pct
        return RenderQAResult(
            metric="duration",
            passed=passed,
            value=duration,
            threshold=expected_seconds,
            severity="error" if not passed else "info",
            detail=f"Duration is {duration:.2f}s (expected {expected_seconds:.2f}s)"
        )
    except Exception as e:
        return RenderQAResult("duration", False, str(e), None, "error", f"Error: {e}")

def verify_audio_streams(video_path: str, min_streams: int = 1) -> RenderQAResult:
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'stream=codec_type', '-of', 'json', video_path]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        data = json.loads(res.stdout)
        
        audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
        count = len(audio_streams)
        
        passed = count >= min_streams
        return RenderQAResult(
            metric="audio_streams",
            passed=passed,
            value=count,
            threshold=min_streams,
            severity="error" if not passed else "info",
            detail=f"Found {count} audio streams"
        )
    except Exception as e:
        return RenderQAResult("audio_streams", False, str(e), None, "error", f"Error: {e}")

def sample_key_frames(video_path: str, output_dir: str, timestamps: List[float] = None) -> List[str]:
    """Extract frames at key moments for visual review."""
    if timestamps is None:
        try:
            cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
            dur_res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
            duration = float(dur_res.stdout.strip())
            timestamps = [0.5, duration / 2, max(0.0, duration - 2.0)]
        except Exception:
            timestamps = [0.5]

    os.makedirs(output_dir, exist_ok=True)
    extracted = []
    for t in timestamps:
        out_path = os.path.join(output_dir, f"frame_{t:.2f}.png")
        cmd = ['ffmpeg', '-y', '-ss', str(t), '-i', video_path, '-vframes', '1', '-q:v', '2', out_path]
        subprocess.run(cmd, capture_output=True, timeout=15)
        if os.path.exists(out_path):
            extracted.append(out_path)
            
    return extracted

def run_full_render_qa(video_path: str, expected_duration: float = None, target_lufs: float = -14.0,
                       declared_black_beats: Optional[List] = None,
                       expected_resolution: Optional[List[int]] = None,
                       expected_fps: Optional[float] = None,
                       framing_intents: Optional[Sequence[float]] = None,
                       chroma_floor: Optional[float] = None,
                       music_path: Optional[str] = None,
                       music_automation: Optional[Sequence[dict]] = None,
                       spine_blocks: Optional[Sequence[dict]] = None) -> List[RenderQAResult]:
    """Run every render QA check.

    `declared_black_beats` carries the black beats the plan declared, as
    `spine_contract.declared_black_beat_ranges` returns them, so the
    black-frame check judges the render by the same ruling
    `compile_manifest` judged the manifest by.

    `expected_resolution` is THE DELIVERY FORMAT, taken from the
    manifest the render was built from. The resolution gate used to
    compare against a hardcoded 1080x1920 while step 6.02 computed the
    manifest's value and dropped it on the floor. That default happened
    to be right, so the gate correctly failed project 001's landscape
    master - but a series that legitimately declares
    `horizontal_1920x1080` would have failed its own correct render. A
    gate has to check what was asked for, not what is usual.

    `framing_intents` are the per-clip framing declarations the manifest
    carries; `chroma_floor` is the colour floor, which has no default
    because it is an open captain decision; `music_path`,
    `music_automation` and `spine_blocks` are what P3 needs to fit the
    bed against the master, and without them P3 does not run at all
    rather than guessing at a music file.
    """
    results = []

    width, height = (expected_resolution or [1080, 1920])[:2]

    results.append(measure_lufs(video_path, target_lufs=target_lufs))
    results.append(detect_black_frames(video_path, declared_beats=declared_black_beats))
    results.append(detect_freeze_frames(video_path))
    results.append(analyze_color_histogram(video_path))
    results.append(measure_frame_occupancy(video_path,
                                           framing_intents=framing_intents))
    results.append(measure_chroma_presence(video_path,
                                           chroma_floor=chroma_floor))
    if music_path and music_automation:
        results.append(measure_speech_above_bed(
            video_path, music_path, music_automation,
            spine_blocks=spine_blocks))
    results.append(verify_resolution(video_path, expected_width=width,
                                     expected_height=height))
    results.append(verify_framerate(video_path)
                   if expected_fps is None else
                   verify_framerate(video_path, expected_fps=expected_fps))
    
    if expected_duration is not None:
        results.append(verify_duration(video_path, expected_duration))
        
    results.append(verify_audio_streams(video_path))
    
    return results
