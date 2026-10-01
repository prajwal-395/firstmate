"""Measurements taken on the RENDERED file, not on the plan that made it.

Everything here judges the master ffmpeg produced.  That is the whole
point: a plan can declare a limiter, a fill and a duck, and none of it
reaches the picture or the mix unless something applied it.  A gate that
reads the manifest cannot tell the difference; these can.

Five of the baseline-craft properties live here.  P1 (the picture fills
the delivery frame, and one video has one geometry), P4 (deliverable
loudness without clipping) and P8 (no picture plays over digital
silence) FAIL a build.  P2 (somewhere in the frame there is colour) and
P3 (where someone speaks, the speech is above the bed) REPORT A NUMBER
and do not fail, because their thresholds are open captain decisions -
see the two `_GATES` booleans below, which are the whole of what
promoting them costs.  P8 has a reporting half of the same kind, its
near-silence ladder, and it is reported for the same reason.


Rules relocated from AGENTS.md 10.4
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.4 keeps the headline
and points here.

**Seven baseline-craft properties are checked on every build, and two of them deliberately do not fail.**
An eighth, a face cut by the frame edge, is measured on the render and gates through the same `framing` check (§10.3).

`render_qa.py` measures the RENDER:
- the picture fills the delivery frame and keeps ONE GEOMETRY PER DECLARED FRAMING
  (`measure_frame_occupancy`).
  **A letterbox bar is BLACK, FLAT and CONTIGUOUS FROM AN EDGE, and darkness alone does not make
  one.** A row joins a bar only while it carries no light (`BAR_ROW_MAX_LUMA`), has near-zero
  variance along itself and matches the row before it; the walk runs inward from the top and
  bottom boundaries and stops at the first row that is picture.
  [why](docs/RULE_EVIDENCE.md#a-dim-shot-is-not-a-letterbox-bar)
  **DARK is a range and BLACK is a value, and the variance half cannot carry the difference on
  its own.** A graded shadow at 4K is flat to within a luma level, so on the captain's craft
  reference 478 of 2418 samples read as letterboxed and the gate failed a correct 20-minute
  master by twenty times its own bound. A real bar measures a row mean of 0.000-0.14 against a
  shadow's 8.5; the level bound is the half that separates them, and the variance bound stays
  because it is what separates a bar from a dark picture on OUR footage.
  **A picture inset in black on ALL FOUR SIDES is a COMPOSITION, not a conform**, and carries no
  geometry: fitting one rectangle inside another leaves bars on one axis, never both. Such a
  frame is counted out and named, the way an entirely black one is - and a frame with no picture
  is judged by `blackdetect`'s own predicate (`_frame_is_black`), not by the bar walk eating the
  whole frame. [why](docs/RULE_EVIDENCE.md#a-dark-picture-is-not-a-black-bar)
  **Every sample is attributed to the clip playing over it** through `framing_spans`, which step
  6.02 builds off the manifest's `framing_delivered` (§10.3) with V2 winning an overlap. The fill
  floor applies to the FILL stretches and the consistency bound applies WITHIN each declared
  framing - never switch the consistency half off for a video declaring more than one framing.
  [why](docs/RULE_EVIDENCE.md#a-declaration-a-clip-cannot-honour)
  **An OVERLAY is not picture, and WHERE IT IS is read off the overlay, never guessed.** Overlay
  ink is neither dark nor flat, so the bar walk stops at it: 001's bottom bar read 347 rows under
  a caption and 656 without one, on a picture that never changes size. **Two fixed geometries
  have failed and a third must not be written.** The full width read every caption as picture;
  the strips outside a CENTRED caption box read the progress bar and the emphasis elements as
  picture, because every element `MotionGraphics/index.tsx` draws is laid out from the safe
  area's own left (90) and right (120) edges and glows past them - measured on 001's real render,
  ink at column 71, inside a 120-column strip AND inside a 90-column one, so narrowing the strips
  to the asymmetric insets fixes neither. Step 6.02 hands `measure_frame_occupancy` the manifest's
  own overlay segments and every pixel their ALPHA says they drew is masked out of the walk.
  **THREE READINGS**: segments given is exact; `[]` says this render carries no overlay and is
  exact; None says nobody asked, is measured full width and SAYS so on the result. A row the ink
  leaves under `MIN_OVERLAY_FREE_COLUMNS` pixels of is UNREADABLE and is resolved from whichever
  side the walk reaches next - counting it as bar would shrink a filling picture by the height of
  a bar drawn over it - and a frame with no readable row is counted out and named, never entered
  as 100%. The gate is not weakened: it still fails a picture that genuinely changes size under a
  full-width overlay.
  And `_stream_raw_frames` asks ffmpeg for `fps=N:round=up`, because the default `round=near`
  emits the LAST input frame to claim a slot - up to half a sample period after the label,
  which put a cutaway starting at 32.067s into the sample the A-roll clip before it owns.
  [why - both, measured on 001's correct render](docs/RULE_EVIDENCE.md#the-occupancy-gate-failed-a-correct-render)
- colour exists somewhere in the frame (`measure_chroma_presence`);
- speech sits above the bed (`measure_speech_above_bed`);
- the master is deliverable without clipping (`measure_lufs`, whose true-peak half sets `passed = False`);
- no picture plays over digital silence (`measure_silence_under_picture`).
  **Picture with nothing at all on any track is a defect on its own terms**, and it had no
  detector: `detect_black_frames` asks whether the picture went away, `verify_audio_streams` only
  that a stream exists, and `measure_lufs` barely moves on an 11% hole. 001 shipped 6.312s of
  exact digital zero, 11.1% of its runtime, including the last four seconds; the craft reference
  has 0.783s in twenty minutes, all of it over black.
  **The gate is DIGITAL ZERO alone, and it needs no taste**: the level is the delivery
  quantisation (a 16-bit sample is zero under half an LSB) and the duration floor is the timebase
  (§10.5's two frames). Black is not picture, so a fade or a declared beat is exempt by
  measurement rather than by rule.
  **How quiet a declared quiet moment may be is the captain's** (`craft-silence-under-picture`),
  so `NEAR_SILENCE_LADDER_DBFS` is REPORTED at every rung and gates at none. Do not encode a
  near-silence level here.
  **Silencing the MUSIC is not silencing the FILM.** `music_behavior: silent` is a legitimate
  decision (§10.5) and is not a declaration that the master carries nothing; nothing excuses a
  run today because no declaration exists to read. [why](docs/RULE_EVIDENCE.md#the-render-that-ended-on-four-seconds-of-nothing)

**`subtitle_gaps` measures the uncaptioned seconds INSIDE a speech block, and it reads the spine to know which those are.**
A caller with no spine gets the whole-timeline measurement and the result says which it made.

`manifest_validator.py` checks the PLAN: no caption card under 0.5s, and no effect family covering 100% of eligible items with two or fewer parameter sets.
- P6 exempts the last card in its block (ending where the block does) and reports it. [why](docs/RULE_EVIDENCE.md#the-caption-box-is-not-one-line)
- P7 judges DRAWN effects only. `CUT_TYPES` draw nothing; denominator is the transitions the plan wrote, not `len(v1_clips) - 1`. [why](docs/RULE_EVIDENCE.md#hard-cuts-are-not-an-effect-on-everything)

Chroma and the mix REPORT A NUMBER and pass.
Promoting either is ONE boolean (`CHROMA_PRESENCE_GATES`, `SPEECH_ABOVE_BED_GATES`); do not turn them into gates by another route. [why - including why frame-mean saturation is not the statistic](docs/RULE_EVIDENCE.md#baseline-craft-properties)

`SPEECH_ABOVE_BED_GATES` stays False: `background` means clip gain while the check reads it as SEPARATION.
Do not flip the boolean without changing one of the two. [why](docs/RULE_EVIDENCE.md#the-mix-target-is-not-a-separation)

**The bed is fitted at the SECTION that plays, and the offset is a REQUIRED argument.**
`measure_speech_above_bed` takes `music_offset_seconds` positionally with no default; `run_full_render_qa` declines P3 when it is None. [why](docs/RULE_EVIDENCE.md#the-bed-was-fitted-from-the-wrong-second)
"""

import json
import math
import subprocess
import os
import statistics
import tempfile
import re
from dataclasses import dataclass
from typing import Any, Iterator, List, NamedTuple, Optional, Sequence

try:
    from library.tools.spine_contract import MAX_DECLARED_BLACK_BEAT_SECONDS
except ImportError:  # imported as a top-level module from library/tools
    from spine_contract import MAX_DECLARED_BLACK_BEAT_SECONDS

try:
    from library.tools.framing_intent import DEFAULT_FRAMING_INTENT, FILL
except ImportError:  # imported as a top-level module from library/tools
    from framing_intent import DEFAULT_FRAMING_INTENT, FILL

try:
    from library.tools.subject_framing import load_face_cascade
except ImportError:  # imported as a top-level module from library/tools
    from subject_framing import load_face_cascade

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
DEFAULT_LUFS_TARGET = -14.0

# The ceiling is not a convention: it is the manifest's own
# `audio_mix.master_limiter.threshold_db`, which every compiled manifest
# so far declares as -1.0 with `enabled: true`.  A master above it is a
# master whose declared limiter did not run, and it clips on any lossy
# re-encode a platform performs.
DEFAULT_TRUE_PEAK_CEILING_DBTP = -1.0


def measure_lufs(video_path: str, target_lufs: float = DEFAULT_LUFS_TARGET,
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


def _seek(window: Optional[tuple]) -> list:
    """ffmpeg INPUT options reading only `(start, end)` seconds.

    Input seeking while decoding is frame-accurate: ffmpeg decodes from
    the keyframe before `start` and discards what precedes it.
    """
    if not window:
        return []
    start, end = window
    return ['-ss', f'{start:.6f}', '-t', f'{max(end - start, 0.0):.6f}']


#: The shortest black / freeze each detector reports.  Named, because
#: `dirty_regions` derives its handles from them.
BLACK_MIN_SECONDS = 0.5
FREEZE_MIN_SECONDS = 1.0


def detect_black_frames(video_path: str, min_duration: float = BLACK_MIN_SECONDS,
                        declared_beats: Optional[List] = None,
                        max_declared_seconds: float = MAX_DECLARED_BLACK_BEAT_SECONDS,
                        window: Optional[tuple] = None) -> RenderQAResult:
    """Detect sustained black frames using ffmpeg blackdetect.

    Black the plan deliberately declared is not a defect - the captain's
    ruling is that a short, defensible hold on black is allowed.  Pass the
    declared beat ranges and each segment is tagged `declared`; only the
    undeclared ones fail the check.  With no ranges passed, every black
    segment fails, which is what an unplanned render deserves.

    `window` is `(start, end)` seconds to read instead of the whole file
    (`run_scoped_render_qa`); segment times stay on the FILE's clock.
    """
    try:
        cmd = [
            'ffmpeg', *_seek(window), '-i', video_path,
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
                    offset = window[0] if window else 0.0
                    segment = {
                        "start": float(m_start.group(1)) + offset,
                        "end": float(m_end.group(1)) + offset,
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

def detect_freeze_frames(video_path: str, min_duration: float = FREEZE_MIN_SECONDS,
                         window: Optional[tuple] = None) -> RenderQAResult:
    """Detect frozen/stuck frames using ffmpeg freezedetect.

    `window` is `(start, end)` seconds to read instead of the whole file
    (`run_scoped_render_qa`); segment times stay on the FILE's clock.
    freezedetect writes no `freeze_end` for a freeze still running when
    its input ends, so a windowed read reports one with `open: True` and
    the window's end as its `end` - it runs at least that far.  At the
    end of the FILE it is dropped, as a whole-file read drops it.
    """
    offset = window[0] if window else 0.0
    try:
        cmd = [
            'ffmpeg', *_seek(window), '-i', video_path,
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
                    current_freeze["start"] = float(m.group(1)) + offset
            elif "lavfi.freezedetect.freeze_duration" in line:
                m = re.search(r'freeze_duration: ([0-9.]+)', line)
                if m:
                    current_freeze["duration"] = float(m.group(1))
            elif "lavfi.freezedetect.freeze_end" in line:
                m = re.search(r'freeze_end: ([0-9.]+)', line)
                if m:
                    current_freeze["end"] = float(m.group(1)) + offset
                    if "start" in current_freeze and "duration" in current_freeze:
                        freeze_segments.append(dict(current_freeze))
                    current_freeze = {}
        if window and "start" in current_freeze:
            freeze_segments.append({
                "start": current_freeze["start"], "end": window[1],
                "duration": window[1] - current_freeze["start"],
                "open": True})

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

            try:
                if os.path.getsize(img_path) <= 0:
                    continue
            except OSError:
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

# blackdetect's own `picture_black_ratio_th` default, which is the
# predicate `detect_black_frames` already judges this pipeline's black
# by. Reused rather than reinvented so "there is no picture here" means
# one thing across this module: a frame is black when at least this share
# of its pixels sit under LIT_LUMA_THRESHOLD.
BLACK_PIXEL_RATIO = 0.98

# ── What a letterbox bar IS, as opposed to a dark picture ──
# Darkness alone cannot tell one from the other, and reading it as if it
# could failed a correctly-framed master (issue #221,
# docs/RULE_EVIDENCE.md#a-dim-shot-is-not-a-letterbox-bar). A bar is
# rendered black: it has no content, so its rows are FLAT, and it is laid
# against the frame boundary, so it is CONTIGUOUS FROM AN EDGE. A dark
# picture row has structure and a dark picture region sits wherever the
# subject is.
#
# Both bounds are measured, not chosen. On a real 1080x1920 master padded
# into 1080x608 bars and re-encoded, every bar row measures a
# within-row standard deviation of exactly 0.0; at crf 30 with noise
# added before the encode, 0.0 everywhere except the one ringing row
# against the picture edge, at 2.9. The dark PICTURE rows that this gate
# used to call bars measure 3.95 and up (project 001 at 50.0s: rows 0-114
# are a car headliner and a lit window, min row std 3.95, median 5.28).
# 2.0 sits in that gap with margin on both sides, and detection of a real
# bar is invariant to the choice across the whole 0.5-3.9 sweep.
BAR_ROW_MAX_STD = 2.0

# ...and consecutive bar rows are the same row. This is the second half
# of "flat": a dark vertical GRADIENT - a night sky, a vignette - can
# have low variance along each row while still being picture, and it is
# picture because it changes down the frame. A real bar's adjacent-row
# means differ by 0.0.
BAR_ROW_MAX_STEP = 2.0

# ...and a bar carries NO LIGHT. This is the half that was missing, and
# it is not the darkness rule coming back: DARK is a range and BLACK is a
# value. A bar is not exposed - nothing was drawn there - so it sits at
# the bottom of the scale, while a graded shadow is exposed picture that
# happens to be dim.
#
# Measured, and the gap is three orders of magnitude wide. Real bars: a
# 1080x608 picture padded into 1080x1920 and re-encoded measures a
# maximum bar row mean of 0.000 at crf 18 and crf 23 and 0.006 at crf 30,
# 0.000 again with noise added and the picture lanczos-scaled first, and
# project 001's own shipped master measures 0.00 to 0.14 over the 81
# samples whose A-roll is letterboxed. The dark PICTURE that trips this:
# on the captain's craft reference at 1121.0s - a full-bleed concert shot
# with a dark ceiling - the 363 rows the walk ate measure a row mean of
# 8.56 to 9.33 with a within-row standard deviation of 0.88 to 1.97. The
# variance half cannot carry that: at 3840 columns a graded shadow really
# IS flat to within a luma level, and BAR_ROW_MAX_STD's own measured gap
# (real bar 0.0-2.9, 001's dark picture 3.95+) does not exist on footage
# this dark. The reference's median frame luma is 38.9 of 255 against a
# 5th percentile of 6.7; our own footage has never been that dark, which
# is why nothing caught it.
#
# 1.0 is an order of magnitude above every real bar measured and an order
# of magnitude below the dark picture that trips it. Swept from 0.25 to
# 8.0 on 001's master, detection of its real bars is invariant: median
# 0.3167, min 0.3167, max 1.0000 at every value.
BAR_ROW_MAX_LUMA = 1.0

# 1 Hz is enough to ESTABLISH a defect at the magnitudes measured on 001,
# but a gate wants finer: a single filled B-roll cutaway shorter than a
# second must not slip between two samples.  2 Hz is the compromise -
# double the evidence, still ~110 frames on a 55s master.
DEFAULT_SAMPLE_FPS = 2.0

# ── P1 targets ──
# The frame FILLS by default (library/tools/framing_intent.py), so a
# master whose picture occupies a third of the delivery frame is a
# defect. 0.95 rather than 1.00 leaves room for a picture row at the very
# top or bottom that is genuinely both dark and featureless - an unlit
# ceiling, a shadow with no detail in it - which the bar test cannot
# distinguish from bar and should not pretend to. Project 001's master
# spends at most 8 of its 1920 rows that way, 0.4%.
MIN_FILL_ROW_FRACTION = 0.95

# The bar walk needs enough unmasked samples in a row to tell a flat bar
# from a dim picture row that happens to be flat where it was sampled. A
# row with fewer free pixels than this is UNREADABLE - the overlay has
# taken it - and the walk resolves it from its neighbours rather than
# judging it on a handful of pixels.
MIN_OVERLAY_FREE_COLUMNS = 64

# Alpha at or above this counts as ink. 1 of 255 - anything a compositor
# actually blended is masked, because a pixel the overlay touched is a
# pixel whose luma is not the picture's. Being conservative here costs a
# few masked rows; being generous is the defect this whole path exists
# for, since it is faint glow spilling out of an element's box that the
# fixed-strip guess kept missing.
OVERLAY_INK_ALPHA = 1

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
# through OTIO") closed that.
#
# The captain's ruling on issue #183 (2026-08-26) was "turn it on, but
# only after the next full run of 001 confirms it passes cleanly with the
# mix in place". That run was performed on 2026-08-26 and it does NOT
# pass cleanly, so the switch stays False and this is the measurement
# that keeps it there:
#
#   6 of 9 speech-bearing windows met the margin the plan declared.
#   Three `background` windows fell short of the +18 dB the plan asks
#   for: 26.41-29.52s at +12.6 dB, 29.52-33.32s at +13.6 dB and
#   35.82-41.68s at +12.5 dB. The two windows planned `silent` measured
#   -68.6 and -69.3 dBFS against a -36.6 dBFS median, so the planned
#   silence is real; the delivery route works.
#
# What fails is the TARGET, not the delivery. `background` means -18 dB
# and `audio_mix` applies that as an absolute clip gain, while this check
# reads it as the separation between the speech and the bed. Those agree
# only when the music file's own level is at or below the speech's. On
# 001 the music sits about 8.6 dB HOTTER than the iPhone speech
# (music -10.5 dBFS raw against speech -19.1 dBFS in the worst window),
# so -18 dB of gain buys about 12.5 dB of separation and no mix setting
# reaches 18. Turning this on today would fail every project whose bed is
# mastered louder than its dialogue, which is most of them.
#
# Promoting it is this boolean, and it needs one of: a loudness-relative
# bed level in `audio_mix`, or a target here that is the planned dB minus
# the measured source difference. Either is a decision, not a fix.
#
# Half of the first route is now built and the half that is missing is a
# number, not a mechanism. Step 2.04 measures every candidate
# (`library/tools/music_measurement.py`), the CHOSEN track carries its own
# scalars forward as `music_selection.measurements`, and step 5.02 reads
# them: `audio_mix.music_automation[].bed_level_after_gain_lufs` is where
# the clip gain actually puts the bed. Two things still do not exist:
#
#   * a declared separation target. `music_behavior.SEPARATION_TARGETS_DB`
#     is empty, so this check still falls back to the clip gain and now
#     SAYS SO per window in `required_margin_basis`. The number is the
#     same open captain decision as the five clip gains themselves.
#   * a separation target, again. The speech's own loudness IS now
#     measured - one ffmpeg loudnorm pass per block over the ranges
#     `a_roll_assignments` names (`library/tools/speech_loudness.py`), so
#     `audio_mix.music_automation[].separation_delivered_db` predicts
#     what each window will deliver and this check measures what it did.
#     Neither has anything to be judged against until a target exists.
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
                       sample_fps: float,
                       scaled: bool = False,
                       start_seconds: Optional[float] = None,
                       duration_seconds: Optional[float] = None,
                       extract: str = "",
                       trim: str = ""
                       ) -> Iterator["object"]:
    """Yield sampled frames as (planes, height, width) uint8 arrays.

    Streamed one frame at a time rather than read whole: a 55-second
    1080x1920 master at 2 Hz in yuv444p is 680 MB if you slurp it, and
    every consumer here reduces each frame to a handful of scalars.

    ``width`` / ``height`` are what the caller will RESHAPE to, so they
    must be the size ffmpeg really emits.  Pass ``scaled=True`` to have
    the filter chain resize to them; leave it False (the default) when
    they are the video's own probed size.  A caller that asks for a size
    the chain does not produce gets frames reshaped across frame
    boundaries and measures noise - silently, because the byte count
    still divides.

    ``start_seconds`` / ``duration_seconds`` restrict the stream to one
    window of the master, for a caller that already knows which seconds
    it needs - `measure_silence_under_picture` looks only at the stretches
    the audio flagged, so a twenty-minute film costs one second of decode
    rather than twenty minutes of it.  The first frame yielded is the one
    at ``start_seconds``.
    """
    import numpy as np

    frame_bytes = planes * width * height
    # `round=up` and not the default `near`.  The fps filter maps each
    # INPUT frame to an output slot by rounding its timestamp, and the
    # last input to claim a slot is the one emitted - so under `near` a
    # sample nominally at 32.0s can be a frame from up to half a sample
    # period LATER (0.25s at 2 Hz).  On project 001 that put a full-frame
    # cutaway starting at 32.067s into the sample labelled 32.0, which
    # `_intent_at` then attributed to the letterboxed A-roll clip before
    # it: the letterbox group's occupancy read max 1.0 against a real
    # 0.3167 and the render failed its own consistency check.  Measured
    # on the shipped master: the true cut is at pts_time 32.066667, and
    # `round=up` is the only mode whose sample at 32.0 carries it.
    chain = ''
    if trim:
        # Filters that choose WHICH seconds are read, on the file's own
        # clock, before the sample grid is laid over them.
        chain += f'{trim},'
    chain += f'fps={sample_fps}:round=up'
    if extract:
        # Filters that change WHAT is being read before it is sized -
        # `format=rgba,alphaextract` turns a transparent overlay into a
        # greyscale video of its own alpha, which is how the drawn
        # footprint of an overlay is read (`_overlay_ink_frames`).
        chain += f',{extract}'
    if scaled:
        chain += f',scale={width}:{height}'
    chain += f',format={pix_fmt}'
    cmd = ['ffmpeg', '-nostdin', '-v', 'error']
    if start_seconds:
        cmd += ['-ss', f'{float(start_seconds):.6f}']
    cmd += ['-i', video_path]
    if duration_seconds is not None:
        cmd += ['-t', f'{float(duration_seconds):.6f}']
    cmd += ['-vf', chain,
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


def _bar_rows(row_mean, row_std, readable=None,
              max_luma: float = BAR_ROW_MAX_LUMA,
              max_std: float = BAR_ROW_MAX_STD,
              max_step: float = BAR_ROW_MAX_STEP) -> int:
    """How many rows of letterbox bar run inward from index 0.

    A row joins the bar only while all three hold, and the walk stops at
    the first row that fails any of them - which is what makes the answer
    a BAR rather than a count of dark rows scattered through the picture.

    * black - mean luma at or under `max_luma`, so an exposed shadow is
      picture however dim it is.  This is a stricter statement than the
      `LIT_LUMA_THRESHOLD` the walk used to take, and a different one: a
      bar is not merely DARK, it carries no light at all;
    * flat along the row - standard deviation below `max_std`, so a dim
      row carrying a window highlight is picture;
    * flat against the last row the walk could READ, so a dark vertical
      gradient is picture.

    `readable` marks the rows an overlay left enough of to judge.  A row
    the overlay took is NOT evidence either way, so it neither joins the
    bar nor stops the walk: it is held pending and attributed to whichever
    side the walk resolves to next.  A pending run followed by a bar row
    was bar; a pending run followed by picture was PICTURE, and is
    returned outside the bar count.

    That attribution is the whole reason this takes a mask rather than
    simply skipping.  An overlay drawn at the bottom of a picture that
    FILLS - the progress bar - hides the rows nearest the edge, and
    counting them as bar reads as the picture having shrunk by exactly
    the height of the overlay.  Resolving them forward reads what is
    true: the first row the walk can see is picture, so nothing below it
    was bar.

    Call it on a reversed triple of arrays to measure the bottom bar, and
    on a transposed frame to measure the side bars of an inset.
    """
    n = int(row_mean.shape[0])
    i = 0
    pending = 0
    previous = None
    while i < n:
        if readable is not None and not bool(readable[i]):
            pending += 1
            i += 1
            continue
        if row_mean[i] > max_luma or row_std[i] >= max_std:
            break
        if previous is not None and abs(float(row_mean[i]) - previous) >= max_step:
            break
        previous = float(row_mean[i])
        i += 1
        pending = 0
    
    # If we saw at least one confirmed bar row, the unreadable run is bounded
    # on its outer side by bar. We resolve it from the side it came from, 
    # treating the fully-masked run as bar. If we saw no bar rows, it touches 
    # the edge, so we resolve it to what it reaches next (picture).
    if previous is not None:
        return i
    return i - pending


class OverlaySegment(NamedTuple):
    """One rendered overlay, as the manifest places it on the timeline.

    `path` is the transparent segment file the overlay step wrote,
    `start`/`end` are its timeline seconds and `source_in` is the second
    of the file that plays at `start`.

    This is the DRAWN geometry and not a description of it.  Every fixed
    guess at where an overlay is has been wrong: the walk used to measure
    the full width and read caption ink as picture, and the strips
    outside a centred caption box that replaced it read a progress bar
    and PR 462's emphasis elements as picture, because both are laid out
    from the safe area's own left and right edges and both spill a glow
    past them.  An element's alpha says exactly which pixels it touched,
    for shapes nobody has drawn yet.
    """

    path: str
    start: float
    end: float
    source_in: float = 0.0


def _overlay_ink_frames(segment: "OverlaySegment", sample_fps: float,
                        width: int, height: int, first_sample: float
                        ) -> Iterator["object"]:
    """Stream one boolean ink mask per sample of `segment`, from its alpha.

    `first_sample` is the timeline second of the first sample that lands
    inside the segment, so the stream starts at the matching second of the
    file and every frame after it lines up with a sample by construction.

    Decoded at twice the sample rate, and each sample yields the UNION of
    the three double-rate frames centred on it - the quarter-second
    before it, the quarter-second holding it, and the quarter-second
    after.  A single grid-aligned frame is a lottery pick wherever the
    file cuts: the frame the grid lands on can be the blank frame of a
    hard cut while the render shows the card on either side of it, and
    then the mask is empty exactly where the master carries ink.
    Measured on 001: sub_block_10 cuts caption cards at file 1.25s, the
    2 Hz grid read that blank frame at the 33.5s sample, `mask_at`
    collapsed the empty union to None, and the sample read 0.4688
    against a real 0.3167.  The triplet cannot miss ink the render
    shows - the render composites the same file at the same file
    second - and where it masks rows the render left bare, the walk
    resolves them as bar, which is what they are wherever a bar is what
    the overlay was drawn over.

    Scaled to the master's frame with `neighbor`, because the answer is a
    mask: interpolating one invents partial ink at every edge.
    """
    import collections
    offset = max(0.0, segment.source_in + (first_sample - segment.start))
    # TRIMMED, not seeked. `-ss` rebases the stream's timestamps onto the
    # seek point and the sample grid is laid out from there, which is
    # half a sample period away from the second that was asked for; on
    # 001 that read sub_block_8's alpha in the blank gap BETWEEN two
    # caption cards while the master was showing one. `trim` keeps the
    # file's own clock, and `setpts=PTS-STARTPTS` puts the grid on the
    # first frame at or after `offset` - within one frame of it, which
    # is the finest a video can be asked about.
    double_fps = 2.0 * float(sample_fps)
    source = _stream_raw_frames(
        segment.path, 'gray', 1, width, height, double_fps,
        # Bounded generously - the trim has already discarded the
        # head, so this only has to be long enough - because the
        # generator is closed the moment the segment stops being
        # live and nothing decodes past that anyway.
        duration_seconds=offset + max(0.0, segment.end - first_sample)
        + 2.0 / sample_fps,
        trim=f'trim=start={offset:.6f},setpts=PTS-STARTPTS',
        extract='format=rgba,alphaextract,scale='
                f'{width}:{height}:flags=neighbor')
    buffered = collections.deque()  # [(double_slot, mask)], slot = k
    slot = 0
    stream_done = False
    sample = 0
    while True:
        # Sample `sample` sits at double-slot 2*sample and owns the
        # triplet centred on it. It is ready once slot 2*sample+1 has
        # been decoded, or the file ends first - past the end the
        # triplet is partial, the way the single-frame stream used to
        # run past it on the duration grace below.
        need = 2 * sample + 1
        while not stream_done and (not buffered or buffered[-1][0] < need):
            try:
                frame = next(source)
            except StopIteration:
                stream_done = True
            else:
                buffered.append((slot, frame[0] >= OVERLAY_INK_ALPHA))
                slot += 1
        if not buffered:
            return
        owned = [mask for (k, mask) in buffered if k >= 2 * sample - 1]
        # The next sample owns 2*sample+1 and up, so everything below
        # that is dropped - never more than the masks live at one
        # instant, the way `_OverlayInk` promises its callers.
        while buffered and buffered[0][0] < 2 * sample + 1:
            buffered.popleft()
        union = owned[0]
        for mask in owned[1:]:
            union = union | mask
        sample += 1
        yield union


class _OverlayInk:
    """The ink every overlay draws, one sample at a time, in lockstep.

    Samples are visited in timeline order and each segment covers one
    contiguous run of them, so a segment is opened once - at the second
    of its own file that the first covered sample plays - and read one
    frame per sample until it ends.  Nothing seeks per sample and nothing
    holds more than the masks that are live at one instant.

    A segment whose file cannot be read is REPORTED, in `notes`, and its
    ink is then missing from the mask - which is the defect this class
    exists to prevent, so it must never pass silently.
    """

    def __init__(self, segments, sample_fps: float, width: int, height: int):
        self._segments = sorted(segments, key=lambda seg: (seg.start, seg.end))
        self._fps = float(sample_fps)
        self._width = int(width)
        self._height = int(height)
        self._next = 0
        self._live = []          # [(segment, iterator)]
        self.notes = []
        self.samples_with_ink = 0

    def mask_at(self, timestamp: float):
        """The union of every overlay's ink at `timestamp`, or None."""
        while (self._next < len(self._segments)
               and self._segments[self._next].start <= timestamp + 1e-6):
            segment = self._segments[self._next]
            self._next += 1
            if timestamp >= segment.end:
                continue
            self._live.append((segment, _overlay_ink_frames(
                segment, self._fps, self._width, self._height, timestamp)))

        union = None
        still_live = []
        for segment, frames in self._live:
            if timestamp >= segment.end:
                frames.close()
                continue
            try:
                ink = next(frames)
            except StopIteration:
                # The file ran out before its declared end. Say so: an
                # overlay whose ink stops being read is an overlay that
                # reads as picture again.
                self.notes.append(
                    f"{os.path.basename(segment.path)} ran out of frames "
                    f"at {timestamp:.1f}s, before the "
                    f"{segment.end:.1f}s the manifest places it to")
                frames.close()
                continue
            except Exception as exc:
                self.notes.append(
                    f"{os.path.basename(segment.path)} could not be read "
                    f"({exc}), so its ink is not masked")
                frames.close()
                continue
            still_live.append((segment, frames))
            union = ink if union is None else (union | ink)
        self._live = still_live

        if union is not None and bool(union.any()):
            self.samples_with_ink += 1
            return union
        return None

    def close(self) -> None:
        for _, frames in self._live:
            frames.close()
        self._live = []


def _rows_outside_the_ink(luma, ink, min_free: int = MIN_OVERLAY_FREE_COLUMNS):
    """Per-row mean, standard deviation and readability, ignoring ink.

    `ink` is the boolean footprint of every overlay drawn over this frame;
    None means nothing was drawn and the whole width is the measurement.
    A row left with fewer than `min_free` pixels is UNREADABLE - a handful
    of pixels cannot tell a flat bar from a dim picture row - and
    `_bar_rows` resolves it from its neighbours instead of judging it.
    """
    import numpy as np

    if ink is None:
        return luma.mean(axis=1), luma.std(axis=1), None

    free = ~ink
    counts = free.sum(axis=1).astype(np.float64)
    safe = np.maximum(counts, 1.0)
    values = np.where(free, luma, 0.0)
    mean = values.sum(axis=1) / safe
    variance = np.maximum((values * values).sum(axis=1) / safe - mean * mean,
                          0.0)
    return mean, np.sqrt(variance), counts >= min_free


def _frame_is_black(luma, lit_threshold: float = LIT_LUMA_THRESHOLD,
                    black_ratio: float = BLACK_PIXEL_RATIO) -> bool:
    """Whether a frame carries no picture at all.

    blackdetect's own predicate - at least `black_ratio` of the pixels
    under `lit_threshold` - which is what `detect_black_frames` already
    judges this pipeline's black by, so "there is no picture here" means
    one thing across this module.

    The bar walk used to answer this by proxy: a black frame was one
    whose two bar runs met in the middle.  That worked only while a bar
    row and a black picture row were the same thing, which `max_luma`
    ends - a fade held at luma 2 is black to any viewer and is not a bar.
    """
    return float((luma < lit_threshold).mean()) >= black_ratio


class FramingSpan(NamedTuple):
    """What one stretch of the timeline was supposed to look like.

    `start`/`end` are timeline seconds and `intent` is the framing the
    clip covering that stretch DELIVERS - `framing_delivered` off the
    manifest, not `framing_intent`.  The two differ wherever a source
    already covers the delivery frame; see
    `library/tools/framing_intent.py`, "Declared is not delivered".
    """

    start: float
    end: float
    intent: float


def _intent_at(spans: Sequence["FramingSpan"], t: float):
    """The intent in force at timeline second `t`, or None.

    Later spans win an overlap, which is what puts a V2 cutaway over the
    V1 clip it covers.
    """
    found = None
    for span in spans:
        # Half-open, so a sample landing exactly on a cut belongs to the
        # clip that starts there rather than the one that just ended. The
        # tolerance is on the START only: adjacent clips share a boundary
        # and float arithmetic must not drop a sample into the gap.
        if span.start - 1e-6 <= t < span.end:
            found = span.intent
    return found


def measure_frame_occupancy(
        video_path: str,
        framing_spans: Optional[Sequence["FramingSpan"]] = None,
        sample_fps: float = DEFAULT_SAMPLE_FPS,
        min_fill_fraction: float = MIN_FILL_ROW_FRACTION,
        max_spread: float = MAX_ROW_FRACTION_SPREAD,
        overlay_segments: Optional[Sequence["OverlaySegment"]] = None
        ) -> RenderQAResult:
    """P1: the picture fills the delivery frame, and one geometry per intent.

    Samples the master and, on each frame, measures the letterbox BARS -
    the runs of rows contiguous from the top and bottom edges that are
    dark AND flat, as `_bar_rows` defines it.  What is left between them
    is the picture, and its height over the frame height is the fraction
    of the delivery frame the picture occupies.

    Counting DARK rows instead is what this check used to do, and it
    cannot tell a black bar from a dark picture: on project 001's
    correctly-framed master it read a car interior at 50.0s as a 32% bar
    and failed the render (issue #221).  Requiring flatness discards a
    dim row that carries structure; requiring contiguity from an edge
    discards a dark region in the MIDDLE of the picture, which is where
    506 of that frame's 621 dark rows were; and requiring the row to be
    BLACK rather than dark discards a graded shadow, which is flat to
    within a luma level at 4K and which failed the captain's craft
    reference on 478 of 2418 samples.

    Two kinds of frame carry no geometry and are counted out rather than
    entered as an occupancy, each under its own name on the result:

    * a BLACK frame, by `_frame_is_black` - blackdetect's own predicate,
      so it is the same black `detect_black_frames` judges against the
      beats the plan declared;
    * a frame whose picture is INSET IN BLACK ON ALL FOUR SIDES, which a
      conform cannot produce - fitting one rectangle inside another
      leaves bars on one axis, never both - and which the craft
      reference uses as a compositional device.

    Every sample is attributed to the clip playing over it, through
    `framing_spans`, and the two assertions are then made PER DECLARED
    INTENT:

    * **Fill** - the median occupancy of the stretches declaring FILL is
      at least `min_fill_fraction`.  A stretch that declares bars gets no
      floor; there is no channel-wide number for how much of the frame a
      deliberately inset picture should occupy, and inventing one here
      would answer a brand decision.
    * **Consistency** - within one declared intent, the spread between
      the least and most occupied sampled frame is at most `max_spread`.
      A viewer cannot name "framing intent" but can see the picture jump
      size twice in 55 seconds.

    Grouping is what makes a per-clip framing choice CHECKABLE rather
    than exempt.  This check used to take a flat set of declarations and
    switch the consistency half off entirely as soon as that set held
    more than one value - so the moment a video declared its framing per
    clip, the only gate on its geometry stopped running.  That is a gate
    that cannot fail (AGENTS.md section 10.4).  Project 001 is the case:
    its landscape A-roll letterboxes and its portrait cutaways cannot,
    so it delivers two geometries on purpose, and each of them still owes
    one geometry to itself.

    `framing_spans` is that attribution - `(start, end, delivered_intent)`
    over the timeline, built by `step_6_02_validate_output` off the
    manifest.  None means nothing was declared, which resolves to one
    span of `DEFAULT_FRAMING_INTENT` over the whole video.

    `overlay_segments` is WHAT THE RENDER DREW OVER THE PICTURE, as
    `OverlaySegment`s off the same manifest.  Every pixel an overlay's
    own alpha says it touched is masked out of the bar walk, and a row
    left with too little to judge is resolved from its neighbours rather
    than counted as bar.  Every fixed guess at that footprint has failed
    - see the reading at the top of the body - so the geometry is read
    off the overlays themselves.  None means the caller said nothing,
    which is reported and is not the same claim as `[]`.
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

        # WHERE THE OVERLAYS ARE, read off the overlays themselves.
        #
        # An overlay is drawn over the picture AND over the bars, and its
        # ink is neither dark nor flat, so the bar walk stops at it. On
        # project 001's correctly-framed master the bottom bar read 347
        # rows under a caption and 656 rows without one, on a picture
        # that never changes size.
        #
        # Two fixed guesses at where the ink is have failed. The full
        # width read every caption as picture. The strips outside a
        # centred caption box - `max(insets.left, insets.right)` each
        # side - read the progress bar and PR 462's emphasis elements as
        # picture, because those are laid out from the safe area's OWN
        # left (90) and right (120) edges and glow past them: a 16px box
        # shadow puts ink at column 74, inside a 120-column strip and
        # inside a 90-column one. Narrowing the strips to the asymmetric
        # insets fixes neither, which is why it was rejected on review.
        #
        # THREE READINGS, and an absent declaration is not "no overlays":
        #
        # * segments given - the mask is the union of their own alpha at
        #   each sample. Exact for a shape nobody has drawn yet, glow,
        #   shadow, blur and all;
        # * `[]` - the caller says this render carries no overlay, so the
        #   whole width is picture and the measurement is exact;
        # * None - nobody said. The bars are measured across the whole
        #   width and the result SAYS the ink is unaccounted for, rather
        #   than guessing at a footprint again.
        overlay_note = None
        ink_reader = None
        if overlay_segments is None:
            overlay_note = (
                "no overlay geometry was supplied, so the bars are measured "
                "across the whole width and any overlay ink drawn over one "
                "reads as picture")
        elif overlay_segments:
            ink_reader = _OverlayInk([
                seg if isinstance(seg, OverlaySegment) else OverlaySegment(*seg)
                for seg in overlay_segments], sample_fps, width, height)

        fractions = []
        times = []
        bands = []
        bars = []
        black_frames = 0
        inset_frames = 0
        covered_frames = 0
        index = 0
        for frame in _stream_raw_frames(video_path, 'gray', 1,
                                        width, height, sample_fps):
            luma = frame[0].astype(np.float32)
            timestamp = index / sample_fps
            index += 1
            if _frame_is_black(luma):
                black_frames += 1
                continue
            ink = None if ink_reader is None else ink_reader.mask_at(timestamp)
            row_mean, row_std, readable = _rows_outside_the_ink(luma, ink)
            if readable is not None and not bool(readable.any()):
                # Ink edge to edge: there is no row of this frame the
                # overlay left enough of to read a geometry off. Counted
                # out and named, the way a black frame is - never entered
                # as an occupancy of 100%.
                covered_frames += 1
                continue
            top = _bar_rows(row_mean, row_std, readable)
            bottom = _bar_rows(
                row_mean[::-1], row_std[::-1],
                None if readable is None else readable[::-1])
            # A conform letterbox is the consequence of fitting a source
            # of a different aspect into the delivery frame, so the
            # picture between its bars spans the FULL WIDTH: fitting one
            # rectangle inside another leaves bars on one axis, never on
            # both.  Black on all four sides is a COMPOSITION - the craft
            # reference presents archival home video as a small rounded
            # rectangle inside black, and runs a three-panel split screen
            # with black gutters - and no conform geometry can be read
            # off it, so it is counted out and named rather than entered
            # as a letterbox.  Measured at 2 Hz: 277 of the reference's
            # 2441 samples, and 0 of project 001's 114, whose real bars
            # measure 0 side columns on every frame.
            #
            # The two bar runs meeting is the same thing seen from the
            # other side: the frame is not black (that was asked first)
            # and yet the picture never reaches the columns the bars were
            # measured on, so it is inset past them.
            inset = top + bottom >= height
            if not inset:
                band = luma[top:height - bottom, :]
                band_ink = None if ink is None else ink[top:height - bottom, :]
                column_mean, column_std, column_readable = \
                    _rows_outside_the_ink(band.T,
                                          None if band_ink is None
                                          else band_ink.T)
                inset = bool(
                    _bar_rows(column_mean, column_std, column_readable)
                    or _bar_rows(
                        column_mean[::-1], column_std[::-1],
                        None if column_readable is None
                        else column_readable[::-1]))
            if inset:
                inset_frames += 1
                continue
            fractions.append((height - top - bottom) / height)
            times.append(timestamp)
            bands.append((top, height - 1 - bottom))
            bars.append((top, bottom))

        if ink_reader is not None:
            ink_reader.close()

        if not fractions:
            unreadable = []
            if black_frames:
                unreadable.append(f"{black_frames} entirely black")
            if inset_frames:
                unreadable.append(f"{inset_frames} inset in black on all "
                                  f"four sides")
            if covered_frames:
                unreadable.append(f"{covered_frames} covered edge to edge "
                                  f"by an overlay")
            return RenderQAResult(
                "frame_occupancy", False, None, None, "error",
                f"Could not measure a picture in any sampled frame "
                f"({', '.join(unreadable)})"
                if unreadable else
                "Could not sample any frame from the render")

        spans = [FramingSpan(float(a), float(b), float(c))
                 for a, b, c in (framing_spans or [])]
        declared = sorted({s.intent for s in spans}) or \
            [float(DEFAULT_FRAMING_INTENT)]

        # Attribute every sample to the framing in force over it. A
        # sample the spans do not cover is UNATTRIBUTED and reported as
        # such: silently folding it into the nearest group would let a
        # gap in the manifest read as coverage.
        groups: dict = {}
        unattributed = 0
        for frac, when in zip(fractions, times):
            intent = _intent_at(spans, when) if spans \
                else float(DEFAULT_FRAMING_INTENT)
            if intent is None:
                unattributed += 1
                continue
            groups.setdefault(intent, []).append((frac, when))

        if spans and not groups:
            return RenderQAResult(
                "frame_occupancy", False, None, None, "error",
                f"None of the {len(fractions)} sampled frames falls inside "
                f"any of the {len(spans)} framing spans the manifest "
                f"declares - the render and the plan describe different "
                f"timelines")

        median_fraction = float(statistics.median(fractions))
        spread = float(max(fractions) - min(fractions))

        faults = []
        fill_applies = any(i == FILL for i in declared)
        fill_group = [f for f, _ in groups.get(float(FILL), [])]
        if fill_group:
            fill_median = float(statistics.median(fill_group))
            if fill_median < min_fill_fraction:
                faults.append(
                    f"the picture occupies {fill_median:.1%} of the frame "
                    f"height (floor {min_fill_fraction:.0%}) over the "
                    f"{len(fill_group)} sampled frames whose framing "
                    f"declares FILL - it is letterboxed and nothing asked "
                    f"for bars"
                )

        per_intent = {}
        for intent in sorted(groups):
            measured = groups[intent]
            values = [f for f, _ in measured]
            group_spread = float(max(values) - min(values))
            per_intent[f"{intent:g}"] = {
                "frames_sampled": len(values),
                "median_picture_fraction": round(
                    float(statistics.median(values)), 4),
                "min_picture_fraction": round(float(min(values)), 4),
                "max_picture_fraction": round(float(max(values)), 4),
                "spread": round(group_spread, 4),
            }
            if group_spread > max_spread:
                lo = min(measured, key=lambda m: m[0])
                hi = max(measured, key=lambda m: m[0])
                faults.append(
                    f"the picture changes size within one declared framing "
                    f"({intent:g}): {lo[0]:.1%} at {lo[1]:.1f}s vs "
                    f"{hi[0]:.1%} at {hi[1]:.1f}s (spread {group_spread:.2f}, "
                    f"bound {max_spread:.2f}) - one framing has one geometry"
                )

        detail = (f"picture occupies {median_fraction:.1%} of the frame "
                  f"(spread {spread:.2f} over {len(fractions)} samples; "
                  f"{len(declared)} declared framing"
                  f"{'s' if len(declared) != 1 else ''})")
        if unattributed:
            detail += (f"; {unattributed} of {len(fractions)} samples fall "
                       f"outside every declared span and were not judged")
        if inset_frames:
            detail += (f"; {inset_frames} samples carry a picture inset in "
                       f"black on all four sides, which is a composition "
                       f"and not a conform - no geometry was read off them")
        if covered_frames:
            detail += (f"; {covered_frames} samples are covered edge to edge "
                       f"by an overlay, leaving no row to read a geometry "
                       f"off")
        if overlay_note:
            detail += f"; {overlay_note}"
        for note in (ink_reader.notes if ink_reader else []):
            detail += f"; {note}"
        if faults:
            detail += " - " + "; ".join(faults)

        return RenderQAResult(
            metric="frame_occupancy",
            passed=not faults,
            value={
                "median_picture_fraction": round(median_fraction, 4),
                "min_picture_fraction": round(float(min(fractions)), 4),
                "max_picture_fraction": round(float(max(fractions)), 4),
                "spread": round(spread, 4),
                "frames_sampled": len(fractions),
                "black_frames_skipped": black_frames,
                "inset_frames_skipped": inset_frames,
                "overlay_covered_frames_skipped": covered_frames,
                "picture_band_first_frame": bands[0],
                "max_top_bar_rows": max(t for t, _ in bars),
                "max_bottom_bar_rows": max(b for _, b in bars),
                "declared_framing_intents": declared,
                "by_declared_framing": per_intent,
                "unattributed_samples": unattributed,
                "overlay_segments": (
                    None if overlay_segments is None
                    else len(overlay_segments)),
                "samples_carrying_overlay_ink": (
                    0 if ink_reader is None else ink_reader.samples_with_ink),
                "overlay_masking": (
                    overlay_note if overlay_note else
                    "every pixel each overlay's own alpha says it drew is "
                    "masked out of the bar walk, so overlay ink of any "
                    "shape is not read as picture"),
                "overlay_notes": (
                    list(ink_reader.notes) if ink_reader else []),
            },
            threshold={"min_fill_fraction": min_fill_fraction,
                       "max_spread": max_spread,
                       "lit_luma_threshold": LIT_LUMA_THRESHOLD,
                       "bar_row_max_luma": BAR_ROW_MAX_LUMA,
                       "bar_row_max_std": BAR_ROW_MAX_STD,
                       "bar_row_max_step": BAR_ROW_MAX_STEP,
                       "fill_floor_applies": fill_applies,
                       "consistency_applies": bool(groups)},
            severity="error" if faults else "info",
            detail=detail,
        )
    except Exception as e:
        return RenderQAResult("frame_occupancy", False, str(e), None,
                              "error", f"Error measuring frame occupancy: {e}")


# ── The face-crop guard ──
# `measure_frame_occupancy` asks whether the picture fills the frame.
# Nothing asked whether the SUBJECT survived being cropped into it, and a
# half-face at full bleed passes every other gate in this module - which
# is exactly what project 001 shipped on 2026-08-26 (AGENTS.md section
# 10.3, "The frame FILLS by default").
#
# Same short side as `step_1_04_temporal_index.FACE_SAMPLE_SHORT_SIDE`,
# for the same measured reason: past 480 the cascade's false positives
# grow faster than its recall. Aspect is preserved, so a portrait master
# costs the same as a landscape one.
MASTER_FACE_SHORT_SIDE = 480

# A detection smaller than this fraction of the frame is not the speaker.
# On a vertical master the subject's face is a large object; a 2% box is
# a passer-by, a poster, or a false positive on a bright rectangle, and
# failing a render because one of those touched the edge would be a gate
# that fails correct output.
MIN_SUBJECT_FACE_AREA = 0.03

# How far inside the frame a box must start before it counts as whole.
# Zero would make the gate fire on a face that merely reaches the edge
# pixel, which the cascade's box placement cannot resolve; 4 px on the
# 480-short-side sample is under 1% of the frame.
FACE_EDGE_TOLERANCE_PX = 4

# Fraction of the face-bearing samples that may show a cropped face
# before the render fails. Not zero: a speaker who leans out of frame for
# a moment is a moment, and a gate that fails a 40-minute render for one
# sampled frame teaches everyone to ignore the report. A conform that is
# geometrically wrong crops EVERY frame of the clips it governs, so it
# clears this bound by a wide margin.
MAX_CROPPED_FACE_FRACTION = 0.10


def _master_face_sample_size(width: int, height: int) -> tuple:
    """Sample (w, h) for the cascade, short side bounded, aspect kept."""
    target = min(MASTER_FACE_SHORT_SIDE, min(width, height))
    if width <= height:
        out_w, out_h = float(target), target * height / float(width)
    else:
        out_h, out_w = float(target), target * width / float(height)
    return (max(2, int(out_w / 2.0 + 0.5) * 2),
            max(2, int(out_h / 2.0 + 0.5) * 2))


def measure_face_intact(
        video_path: str,
        sample_fps: float = DEFAULT_SAMPLE_FPS,
        max_cropped_fraction: float = MAX_CROPPED_FACE_FRACTION) -> RenderQAResult:
    """A face the render detects is not cut off by the frame edge.

    The degenerate case of subject placement, and the only part of it that
    is statable: "face in the middle third" is wrong for a deliberately
    off-centre composition and no brand document asks for it, but nobody
    in any series wants a beheaded speaker.

    Measured on the MASTER, because that is the only place the question
    can be answered.  The plan's own geometry is checked separately by
    `manifest_validator`; this catches the case where the plan was fine
    and something downstream - a Fusion zoom, a transform Resolve applied
    differently, a comp that did not import - cropped the speaker anyway.

    Only boxes at least `MIN_SUBJECT_FACE_AREA` of the frame are judged,
    and a render fails only when more than `max_cropped_fraction` of its
    face-bearing samples are cropped.  Both bounds exist so the gate
    cannot fail correct output: the alternative - failing on any single
    edge-touching detection - fires on a passer-by and on a speaker who
    leans out of shot for half a second.

    A render in which NO face is detected passes and says so.  This is a
    guard against a specific defect, not an assertion that every video has
    a face in it.
    """
    try:
        import numpy as np
    except ImportError as e:  # pragma: no cover - numpy is a hard dependency
        return RenderQAResult("face_intact", True, str(e), None,
                              "warning", f"numpy unavailable: {e}")

    cascade = load_face_cascade()
    if cascade is None:
        # OpenCV 5 ships no Haar cascades. Saying so is the point: a
        # measurement that could not be taken must not read as a pass
        # that was earned.
        return RenderQAResult(
            "face_intact", True, None, None, "warning",
            "No Haar cascade available, face crop unmeasured")

    try:
        size = _probe_video_size(video_path)
        if not size:
            return RenderQAResult("face_intact", False, None, None,
                                  "error", "No video stream found")
        width, height = size
        sample_w, sample_h = _master_face_sample_size(width, height)
        frame_area = float(sample_w * sample_h)

        face_frames = 0
        cropped = []
        for index, frame in enumerate(_stream_raw_frames(
                video_path, 'gray', 1, sample_w, sample_h, sample_fps,
                scaled=True)):
            gray = np.ascontiguousarray(frame[0])
            boxes = [b for b in cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=3, minSize=(20, 20))
                if (b[2] * b[3]) / frame_area >= MIN_SUBJECT_FACE_AREA]
            if not boxes:
                continue
            face_frames += 1
            x, y, fw, fh = max(boxes, key=lambda b: b[2] * b[3])
            tol = FACE_EDGE_TOLERANCE_PX
            edges = []
            if x <= tol:
                edges.append("left")
            if x + fw >= sample_w - tol:
                edges.append("right")
            if y <= tol:
                edges.append("top")
            if y + fh >= sample_h - tol:
                edges.append("bottom")
            if edges:
                cropped.append({
                    "at_seconds": round(index / sample_fps, 2),
                    "edges": edges,
                    "box_fraction": [round(float(x) / sample_w, 4),
                                     round(float(y) / sample_h, 4),
                                     round(float(fw) / sample_w, 4),
                                     round(float(fh) / sample_h, 4)],
                })

        if face_frames == 0:
            return RenderQAResult(
                metric="face_intact", passed=True,
                value={"face_frames": 0, "cropped_frames": 0},
                threshold={"max_cropped_fraction": max_cropped_fraction,
                           "min_subject_face_area": MIN_SUBJECT_FACE_AREA},
                severity="info",
                detail="No face large enough to judge was detected in the "
                       "render - nothing to crop")

        fraction = len(cropped) / float(face_frames)
        failed = fraction > max_cropped_fraction
        detail = (f"{len(cropped)} of {face_frames} face-bearing samples "
                  f"show a face cut by the frame edge ({fraction:.1%})")
        if failed:
            worst = cropped[0]
            detail += (f" - first at {worst['at_seconds']}s off the "
                       f"{'/'.join(worst['edges'])} edge; the subject does "
                       f"not survive the conform")

        return RenderQAResult(
            metric="face_intact",
            passed=not failed,
            value={
                "face_frames": face_frames,
                "cropped_frames": len(cropped),
                "cropped_fraction": round(fraction, 4),
                "sample_size": [sample_w, sample_h],
                "examples": cropped[:8],
            },
            threshold={"max_cropped_fraction": max_cropped_fraction,
                       "min_subject_face_area": MIN_SUBJECT_FACE_AREA,
                       "edge_tolerance_px": FACE_EDGE_TOLERANCE_PX},
            severity="error" if failed else "info",
            detail=detail,
        )
    except Exception as e:
        return RenderQAResult("face_intact", False, str(e), None,
                              "error", f"Error measuring face crop: {e}")


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


@dataclass
class GradeSpan:
    """One graded clip's claim on the export, for `measure_grade_delivery`.

    `timeline_start`/`timeline_end` bound the clip on the EXPORT;
    `source_path` is the footage it was cut from (the ungraded
    reference); `source_start`/`source_end` bound the played range
    INSIDE that source, in seconds - the reference is measured on the
    same content the export plays, never on the whole file (measured
    2026-09-25 on the proof timeline: four items playing source 0-5s
    read chroma ratios of 1.11/2.01/2.23/2.23 against the whole-file
    median, while the same-range ratios read 0.499/0.904/1.0 - the
    whole-file reference aliases content differences as grade
    effects). None bounds the whole file, for callers with no range;
    `cdl` is the four specified terms in the plan's own spelling
    (`slope_r`...`saturation`, as `color_grade` carries them). `label`
    names the clip in per-row verdicts.
    """
    label: str
    timeline_start: float
    timeline_end: float
    source_path: str
    cdl: dict
    source_start: Optional[float] = None
    source_end: Optional[float] = None


def _median_luma_and_chroma(video_path: str, width: int, height: int,
                            sample_fps: float,
                            start_seconds: Optional[float] = None,
                            duration_seconds: Optional[float] = None):
    """(median frame-mean luma, median p99 chroma) over sampled frames.

    The chroma half is `measure_chroma_presence`'s statistic - 99th
    percentile of per-pixel chroma over LIT pixels only, so letterbox
    bars and true black cannot dilute it. (None, None) where nothing
    sampled.
    """
    import numpy as np

    lumas, chromas = [], []
    for frame in _stream_raw_frames(video_path, 'yuv444p', 3, width,
                                    height, sample_fps,
                                    start_seconds=start_seconds,
                                    duration_seconds=duration_seconds):
        y = frame[0].astype(np.float32)
        lumas.append(float(np.mean(y)))
        u = frame[1].astype(np.float32)
        v = frame[2].astype(np.float32)
        chroma = np.sqrt((u - 128.0) ** 2 + (v - 128.0) ** 2)
        lit = chroma[y >= LIT_LUMA_THRESHOLD]
        if lit.size:
            chromas.append(float(np.percentile(lit, 99)))
    if not lumas:
        return None, None
    return (float(statistics.median(lumas)),
            float(statistics.median(chromas)) if chromas else None)


def measure_grade_delivery(video_path: str,
                           grade_spans: Optional[Sequence[GradeSpan]] = None,
                           sample_fps: float = DEFAULT_SAMPLE_FPS
                           ) -> RenderQAResult:
    """P11: what the grade specified is what the exported pixels show.

    Finding 28: per-clip CDL reached `SetCDL` but barely showed in the
    export (saturation 0.88 measuring a chroma ratio of 0.96-1.03;
    +1.3 stops moving luma 43 -> 82 against ~130), while the build
    reported "Applied". The build's read-back (`cdl_readback`,
    judged where the grade is written) is the gate; this is the
    pixel verdict, REPORTED per graded clip, never gated: a shortfall
    confounds the grade with the pipeline and the encode, so it is
    said loudly rather than failed on.

    Per span the export window is measured against the SOURCE range it
    was cut from (median luma, median p99 chroma - the source is the
    only ungraded reference the validator holds), and the direction
    the CDL demands is checked with a dead zone: saturation at or
    below 0.95 must move chroma DOWN, at or above 1.05 UP; mean slope
    at or above 1.2 must move luma UP, at or below 0.85 DOWN. A span
    whose CDL is ~identity demands nothing and reads as info; a span
    with no source or no samples reads as unverifiable, never as
    delivered.
    """
    import numpy as np  # noqa: F401 - hard dependency, see chroma_presence

    if not grade_spans:
        return RenderQAResult(
            "grade_delivery", True, {"spans": []}, {"spans": 0},
            "info", "no graded spans declared - nothing to verify")

    size = _probe_video_size(video_path)
    if not size:
        return RenderQAResult("grade_delivery", True, None, None,
                              "warning", "No video stream found")
    width, height = size

    rows = []
    contradicts = 0
    for span in grade_spans or []:
        cdl = span.cdl or {}
        try:
            sat = float(cdl.get("saturation", 1.0))
        except (TypeError, ValueError):
            sat = 1.0
        try:
            gain = float(sum(float(cdl.get(f"slope_{c}", 1.0))
                             for c in "rgb") / 3.0)
        except (TypeError, ValueError):
            gain = 1.0
        demands = []
        if sat <= 0.95:
            demands.append(("chroma_down", sat))
        elif sat >= 1.05:
            demands.append(("chroma_up", sat))
        if gain >= 1.2:
            demands.append(("luma_up", gain))
        elif gain <= 0.85:
            demands.append(("luma_down", gain))
        duration = max(float(span.timeline_end)
                       - float(span.timeline_start), 0.0)
        if duration <= 0.0:
            rows.append({"label": span.label, "verdict": "unverifiable",
                         "detail": "empty timeline span"})
            continue
        exp_luma, exp_chroma = _median_luma_and_chroma(
            video_path, width, height, sample_fps,
            start_seconds=float(span.timeline_start),
            duration_seconds=duration)
        if exp_luma is None:
            rows.append({"label": span.label, "verdict": "unverifiable",
                         "detail": "no export frames sampled"})
            continue
        src_size = _probe_video_size(span.source_path)
        if not src_size:
            rows.append({"label": span.label, "verdict": "unverifiable",
                         "detail": f"source unreadable: {span.source_path}"})
            continue
        try:
            _src_start = (float(span.source_start)
                          if span.source_start is not None else None)
            _src_end = (float(span.source_end)
                        if span.source_end is not None else None)
        except (TypeError, ValueError):
            _src_start = _src_end = None
        _src_dur = ((max(_src_end - _src_start, 0.0))
                    if _src_start is not None and _src_end is not None
                    else None)
        src_luma, src_chroma = _median_luma_and_chroma(
            span.source_path, src_size[0], src_size[1], sample_fps,
            start_seconds=_src_start, duration_seconds=_src_dur)
        if src_luma is None or not src_chroma:
            rows.append({"label": span.label, "verdict": "unverifiable",
                         "detail": "no source frames sampled"})
            continue
        luma_ratio = exp_luma / max(src_luma, 1e-6)
        chroma_ratio = (exp_chroma / src_chroma if exp_chroma else None)
        misses = []
        for kind, amount in demands:
            if kind == "chroma_down" and chroma_ratio is not None \
                    and chroma_ratio >= 0.99:
                misses.append(f"saturation {amount:g} demands chroma "
                              f"down, measured ratio {chroma_ratio:.2f}")
            elif kind == "chroma_up" and chroma_ratio is not None \
                    and chroma_ratio <= 1.01:
                misses.append(f"saturation {amount:g} demands chroma "
                              f"up, measured ratio {chroma_ratio:.2f}")
            elif kind == "luma_up" and luma_ratio <= 1.10:
                misses.append(f"slope {amount:g} demands luma up, "
                              f"measured ratio {luma_ratio:.2f}")
            elif kind == "luma_down" and luma_ratio >= 0.95:
                misses.append(f"slope {amount:g} demands luma down, "
                              f"measured ratio {luma_ratio:.2f}")
        if not demands:
            rows.append({"label": span.label, "verdict": "no demand",
                         "detail": f"identity-ish CDL (sat {sat:g}, "
                                   f"slope {gain:g}) - nothing measurable "
                                   f"demanded",
                         "luma_ratio": round(luma_ratio, 3),
                         "chroma_ratio": (round(chroma_ratio, 3)
                                          if chroma_ratio else None)})
        elif misses:
            contradicts += 1
            rows.append({"label": span.label, "verdict": "contradicts",
                         "detail": "; ".join(misses),
                         "luma_ratio": round(luma_ratio, 3),
                         "chroma_ratio": (round(chroma_ratio, 3)
                                          if chroma_ratio else None)})
        else:
            rows.append({"label": span.label, "verdict": "delivered",
                         "detail": "export moves the demanded direction",
                         "luma_ratio": round(luma_ratio, 3),
                         "chroma_ratio": (round(chroma_ratio, 3)
                                          if chroma_ratio else None)})
    value = {"spans": rows, "contradicts": contradicts}
    if contradicts:
        return RenderQAResult(
            "grade_delivery", False, value, {"contradicts": 0},
            "warning",
            f"{contradicts} of {len(rows)} graded span(s) contradict "
            f"the specified grade on exported pixels - "
            + "; ".join(f"{r['label']}: {r['detail']}"
                        for r in rows if r["verdict"] == "contradicts"))
    return RenderQAResult(
        "grade_delivery", True, value, {"contradicts": 0}, "info",
        f"{len(rows)} graded span(s) verified against the source - "
        f"no span contradicts its grade")


def measure_speech_above_bed(
        video_path: str,
        music_path: str,
        music_automation: Sequence[dict],
        music_offset_seconds: float,
        spine_blocks: Optional[Sequence[dict]] = None,
        sample_rate: int = 48000,
        silent_margin_db: float = SILENT_WINDOW_MARGIN_DB,
        gate: bool = SPEECH_ABOVE_BED_GATES) -> RenderQAResult:
    """P3: where someone speaks, the speech is above the bed.

    For each window in `audio_mix.music_automation`, least-squares fits the
    music against the master (``g = <music, mix> / <music, music>``). When
    the plan carries word intervals, separation is measured only while
    words are spoken; the music's planned recovery in the gaps is measured
    separately. This keeps a bed doing what was requested in word gaps from
    being counted as louder under speech.

    **The targets are the manifest's own numbers**, not a convention and
    not taste: `background` -> +18 dB, `prominent` -> +6 dB, from
    `audio_mix.track_levels` against a speech reference of 0 dB.  A
    `silent` window is judged on the music's CONTRIBUTION, `silent_margin_db`
    below the median of the non-silent windows - the gain column shows
    whether the automation ran, the contribution column shows whether the
    viewer can hear it, and the second is the one that matters.

    **`music_offset_seconds` is required and has no default**, because
    the bed does not have to start at the head of its file.  Step 2.04
    chooses which SECTION of the track plays
    (`library/tools/music_section.py`) and `compile_manifest` places the
    A2 clip at that `source_in`, so timeline second *t* carries music
    file second *t + offset*.  Fitting the file from 0 against a render
    built from second 60 correlates two unrelated stretches of music:
    on project 001 that drove every window's correlation to |r| <= 0.03,
    collapsed the fitted music level to -53..-128 dB and turned 0 of 8
    speech windows meeting their target into 8 of 8 - a gate that could
    not fail.  The argument is required for the reason
    `beat_grid.beat_positions` requires the same one: a default of "no
    offset" is the value that is silently wrong (AGENTS.md 10.5).  The
    offset is recorded on the result beside the windows.

    Only speech-bearing blocks are judged; the rest are measured and
    reported. `spine_blocks` supplies `block_type` by position - read ONLY
    that from it. The behaviour, word intervals and levels are read from
    `music_automation`, which carries the dB this measurement is judged
    against; `_spine_blocks` carries the same behaviour vocabulary
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

        offset = float(music_offset_seconds)
        if offset < 0:
            return RenderQAResult(
                "speech_above_bed", True, None, None, "warning",
                f"The bed is placed at a negative offset ({offset} s) into "
                f"its own file, which nothing can fit against")

        def fit_window(window_mix, window_music):
            count = min(window_mix.size, window_music.size)
            if count < sample_rate // 10:  # under 100 ms: nothing to fit
                return None
            fitted_mix = window_mix[:count]
            fitted_music = window_music[:count]
            denominator = float(fitted_music @ fitted_music)
            if denominator <= 0:
                return None
            gain = float(fitted_music @ fitted_mix) / denominator
            residual = fitted_mix - gain * fitted_music
            music_db = _db(
                abs(gain) * float(np.sqrt(np.mean(fitted_music ** 2))))
            other_db = _db(float(np.sqrt(np.mean(residual ** 2))))
            corr_denom = float(np.sqrt(
                (fitted_mix @ fitted_mix) * denominator))
            return {
                "gain": gain,
                "music_db": music_db,
                "other_db": other_db,
                "margin_db": other_db - music_db,
                "fitted_gain_db": _db(abs(gain)),
                "correlation": (float(fitted_mix @ fitted_music)
                                / corr_denom if corr_denom > 0 else 0.0),
            }

        def collect_intervals(raw, start, end, count):
            intervals = []
            for item in raw or []:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    continue
                try:
                    left = max(start, float(item[0]))
                    right = min(end, float(item[1]))
                except (TypeError, ValueError):
                    continue
                if right <= left:
                    continue
                first = max(0, min(count,
                                   int(round((left - start) * sample_rate))))
                last = max(first, min(count,
                                      int(round((right - start)
                                                * sample_rate))))
                if last > first:
                    intervals.append((left, right, first, last))
            intervals.sort(key=lambda row: (row[0], row[1]))
            return intervals

        windows = []
        for w in music_automation:
            start = float(w.get("timeline_start", 0.0))
            end = float(w.get("timeline_end", 0.0))
            a = int(start * sample_rate)
            b = int(end * sample_rate)
            # The bed's own clock, not the timeline's: the A2 clip starts
            # `offset` seconds into the file (music_section, AGENTS.md 10.5).
            ma = int((start + offset) * sample_rate)
            mb = int((end + offset) * sample_rate)
            x = mix[a:min(b, mix.size)]
            y = music[ma:min(mb, music.size)]
            n = min(x.size, y.size)
            x, y = x[:n], y[:n]
            whole = fit_window(x, y)
            if whole is None:
                continue

            intervals = collect_intervals(
                w.get("word_intervals"), start, end, n)
            word_fit = whole
            measurement_scope = "whole_block"
            gap_fit = None
            required_gap_recovery = None
            if intervals:
                word_mix = np.concatenate(
                    [x[first:last] for _, _, first, last in intervals])
                word_music = np.concatenate(
                    [y[first:last] for _, _, first, last in intervals])
                measured_words = fit_window(word_mix, word_music)
                if measured_words is not None:
                    word_fit = measured_words
                    measurement_scope = "spoken_words"

                    merged = []
                    for left, right, _, _ in intervals:
                        if merged and left <= merged[-1][1]:
                            merged[-1] = (merged[-1][0],
                                          max(merged[-1][1], right))
                        else:
                            merged.append((left, right))
                    gaps = []
                    cursor = start
                    for left, right in merged:
                        if left > cursor:
                            gaps.append((cursor, left))
                        cursor = max(cursor, right)
                    if cursor < end:
                        gaps.append((cursor, end))

                    release_seconds = max(
                        0.0, float(w.get("word_gap_release_ms", 0.0))
                        / 1000.0)
                    settled_gaps = []
                    for left, right in gaps:
                        # The leading gap starts at its recovered level.
                        # After a word, the recovery ramp must finish before
                        # its samples count as delivered gap level.
                        settled_start = (
                            left if left <= start else
                            min(right, left + release_seconds))
                        first = max(0, min(
                            n, int(round((settled_start - start)
                                         * sample_rate))))
                        last = max(first, min(
                            n, int(round((right - start) * sample_rate))))
                        if last > first:
                            settled_gaps.append((first, last))
                    if settled_gaps:
                        gap_mix = np.concatenate(
                            [x[first:last] for first, last in settled_gaps])
                        gap_music = np.concatenate(
                            [y[first:last] for first, last in settled_gaps])
                        gap_fit = fit_window(gap_mix, gap_music)
                    target_level = w.get("target_level_db")
                    gap_level = w.get("word_gap_level_db")
                    if (isinstance(target_level, (int, float))
                            and isinstance(gap_level, (int, float))):
                        required_gap_recovery = float(gap_level) - float(
                            target_level)

            behaviour = w.get("music_behavior")
            position = w.get("spine_block_position")
            row = {
                "timeline_start": round(float(w.get("timeline_start", 0.0)), 3),
                "timeline_end": round(float(w.get("timeline_end", 0.0)), 3),
                "music_behavior": behaviour,
                "target_level_db": w.get("target_level_db"),
                "separation_target_db": w.get("separation_target_db"),
                "block_type": block_type_by_position.get(str(position)),
                "measurement_scope": measurement_scope,
                "music_in_mix_db": round(word_fit["music_db"], 2),
                "non_music_db": round(word_fit["other_db"], 2),
                "margin_db": round(word_fit["margin_db"], 2),
                "whole_block_margin_db": round(whole["margin_db"], 2),
                "fitted_gain_db": round(word_fit["fitted_gain_db"], 2),
                "correlation": round(word_fit["correlation"], 3),
            }
            if gap_fit is not None:
                row["word_gap_recovery_db"] = round(
                    _db(abs(gap_fit["gain"]))
                    - _db(abs(word_fit["gain"])), 2)
            if required_gap_recovery is not None:
                row["required_gap_recovery_db"] = round(
                    required_gap_recovery, 2)
            windows.append(row)

        if not windows:
            return RenderQAResult("speech_above_bed", True, None, None,
                                  "warning",
                                  "No music automation window was long enough to fit")

        non_silent = [win["music_in_mix_db"] for win in windows
                      if win["music_behavior"] != "silent"]
        silent_reference = (statistics.median(non_silent) if non_silent
                            else None)

        # A SEPARATION the plan asked for, when the plan asked for one -
        # `audio_mix.music_automation[].separation_target_db`.  Otherwise
        # the clip gain, sign-flipped, which is what this check has always
        # judged against and is NOT a separation target: a bed planned at
        # -18 dB is pushed 18 dB down from its own level, which only comes
        # out as 18 dB under the voice when the file is no hotter than the
        # speech.  The basis is recorded per window either way, so nobody
        # reads a clip gain as a margin somebody chose.  See
        # `library/tools/music_behavior.UNDECLARED_SEPARATION`.
        for win in windows:
            if win["music_behavior"] == "silent":
                win["required_margin_db"] = None
                win["required_margin_basis"] = None
                continue
            declared = win.get("separation_target_db")
            if isinstance(declared, (int, float)):
                win["required_margin_db"] = abs(float(declared))
                win["required_margin_basis"] = "declared_separation_target"
                continue
            target = win.get("target_level_db")
            win["required_margin_db"] = abs(float(target)) if target is not None \
                else None
            win["required_margin_basis"] = (
                "clip_gain_read_as_separation" if target is not None else None)

        judged, failures = [], []
        for win in windows:
            if win.get("block_type") not in SPEECH_BEARING_BLOCK_TYPES:
                win["judged"] = False
                continue
            win["judged"] = True
            if win["music_behavior"] == "silent":
                if silent_reference is None:
                    win["judged"] = False
                    continue
                ok = win["music_in_mix_db"] <= silent_reference - silent_margin_db
            else:
                need = win["required_margin_db"]
                if need is None:
                    win["judged"] = False
                    continue
                ok = win["margin_db"] >= need
            win["meets_plan"] = ok
            judged.append(win)
            if not ok:
                failures.append(win)

        meets = not failures
        bases = {win.get("required_margin_basis") for win in judged
                 if win.get("required_margin_basis")}
        on_clip_gain = "clip_gain_read_as_separation" in bases
        detail = (f"{len(judged) - len(failures)} of {len(judged)} "
                  f"speech-bearing windows meet the margin they were "
                  f"judged against")
        if on_clip_gain:
            detail += (" - which for at least one window is the CLIP GAIN, "
                       "not a separation target: the plan declares none "
                       "(music_behavior.SEPARATION_TARGETS_DB is empty)")
        if failures:
            worst = min(failures,
                        key=lambda win: (win["margin_db"] - (win["required_margin_db"] or 0)))
            detail += (f"; worst {worst['timeline_start']:.2f}-"
                       f"{worst['timeline_end']:.2f}s planned "
                       f"{worst['music_behavior']}, speech is "
                       f"{worst['margin_db']:+.1f} dB over the bed")
            if not gate:
                detail += (" - REPORTED ONLY; see SPEECH_ABOVE_BED_GATES for "
                           "why this is not a build failure yet")

        return RenderQAResult(
            metric="speech_above_bed",
            passed=meets if gate else True,
            value={"windows": windows,
                   # Recorded, so a reported margin can never be read
                   # without the offset it was fitted at.
                   "music_offset_seconds": round(offset, 3),
                   "judged": len(judged),
                   "failing": len(failures),
                   "silent_reference_db": (round(silent_reference, 2)
                                           if silent_reference is not None
                                           else None)},
            threshold={"targets": (
                           "audio_mix.music_automation[].separation_target_db "
                           "where the plan declares one, else the clip gain in "
                           "target_level_db - see required_margin_basis per "
                           "window"),
                       "judged_on_clip_gain": on_clip_gain,
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


# ── P8: the mix is never digitally silent under picture ──
#
# "Every frame of the timeline must show a clip" (AGENTS.md section 10.2)
# has no audio twin, and nothing in this repository looked for one.
# `detect_black_frames` asks whether the picture went away;
# `verify_audio_streams` asks only whether an audio stream EXISTS;
# `measure_lufs` asks whether the whole master is deliverable, which an
# 11% hole barely moves.  So a render could - and did - put four seconds
# of picture on screen with absolutely nothing on any track and pass
# every gate here.
#
# Measured on project 001's shipped master (`Pipeline_Edit_2.mp4`):
# 316,248 of its 2,718,656 mono samples are exactly zero (11.63%), in two
# runs - 41.643-43.943s (2.301s) and 52.627-56.639s (4.011s, the last
# four seconds of the video) - 6.312s of 56.639s, 11.14%.  The captain's
# craft reference, twenty minutes of finished documentary, has one run of
# 0.783s at 1219.379s, 0.06%, and it sits over black.  Both numbers are
# reproduced by this check.
#
# The mechanism on 001 is fully traceable and no part of it is a bug: a
# `transition_slot` declaring `music_behavior: silent` and an `outro`
# declaring `fade_out`, both covered by V2 cutaways placed `video_only`,
# with no A-roll under them because they are non-speech blocks.  Silencing
# the MUSIC is not silencing the FILM, and the vocabulary has no way to
# say the second - `music_behavior` names what the bed does and nothing
# declares that the master carries nothing.  Nothing here excuses a run
# for a declared behaviour, therefore: there is no declaration to read.
# If one is ever added, this is where it would be read, the way
# `segment_is_declared` reads `intentional_black_beat`.

# Digital zero, stated as a level so it sits on the same axis as the
# ladder below.  A 16-bit sample is zero exactly when its magnitude is
# under half an LSB, which is 20*log10(1/32768) dBFS.  This is not a
# chosen threshold: it is the resolution of the delivery quantisation,
# the finest distinction a delivered PCM stream can carry, and it is what
# reproduces the craft report's own count on both masters.
DIGITAL_ZERO_DBFS = -90.30899869919436

# ...and everything ABOVE that line is QUIET, not zero, and how quiet a
# declared "silent" moment may be is an OPEN CAPTAIN DECISION
# (`craft-silence-under-picture`).  So this ladder is REPORTED at every
# rung and gates at none of them: the numbers are there for whoever
# answers that question, and inventing a level here would answer it
# instead.  Only DIGITAL_ZERO_DBFS decides anything.
NEAR_SILENCE_LADDER_DBFS = (-80.0, -70.0, -60.0)

# The shortest silence that can be a hole in the sound, in FRAMES of the
# master's own timebase.  Mechanical, not a taste call, and it is
# AGENTS.md section 10.5's own floor: "The only floor is the timebase -
# two frames, one at level plus one of de-click ramp". That is the
# shortest sound this pipeline is able to place, so a gap shorter than it
# is a gap no plan could have filled. Below one frame the question does
# not arise at all - ordinary audio crosses zero constantly.
MIN_SILENCE_FRAMES = 2


def _probe_frame_rate(video_path: str) -> Optional[float]:
    """The first video stream's `r_frame_rate`, or None."""
    cmd = ['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
           '-show_entries', 'stream=r_frame_rate', '-of', 'json', video_path]
    res = subprocess.run(cmd, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=30)
    streams = json.loads(res.stdout or "{}").get("streams", [])
    if not streams or "r_frame_rate" not in streams[0]:
        return None
    num, _, den = streams[0]["r_frame_rate"].partition("/")
    try:
        fps = float(num) / max(float(den or 1), 1.0)
    except ValueError:
        return None
    return fps if fps > 0 else None


def _decode_mono_pcm16(path: str, sample_rate: int = 48000):
    """Decode to mono 16-bit PCM at `sample_rate`, as int16.

    16-bit and not float, because the question this answers is whether
    the DELIVERED sample is zero, and "zero" is a statement about the
    quantisation the delivery carries.  A lossy decode to float leaves
    tiny non-zero values in a region that is silent at any playable
    resolution: on project 001 the float decode finds 192,512 exact zeros
    and the 16-bit decode 316,248, and the second is the number that
    describes what a listener gets.
    """
    import numpy as np

    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-i', path,
           '-map', '0:a:0', '-ac', '1', '-ar', str(sample_rate),
           '-f', 's16le', '-acodec', 'pcm_s16le', '-']
    res = subprocess.run(cmd, capture_output=True, timeout=600)
    return np.frombuffer(res.stdout, dtype='<i2')


def _level_runs(magnitude, ceiling: float, sample_rate: int,
                min_seconds: float) -> List[tuple]:
    """Stretches where every sample sits at or under `ceiling`.

    `magnitude` is |sample| in the same units as `ceiling`.  Returned as
    (start, end, duration) in seconds, only for runs reaching
    `min_seconds` - a shorter one is a zero crossing, not a hole.
    """
    import numpy as np

    mask = (magnitude <= ceiling).astype(np.int8)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask, [0]))))
    runs = []
    for start, end in zip(edges[0::2], edges[1::2]):
        duration = (end - start) / sample_rate
        if duration >= min_seconds:
            runs.append((start / sample_rate, end / sample_rate, duration))
    return runs


def _merge_windows(windows: Sequence[tuple]) -> List[list]:
    """Overlapping (start, end) pairs merged into the fewest spans."""
    merged: List[list] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def _picture_map(video_path: str, windows: Sequence[list], fps: float,
                 width: int, height: int) -> dict:
    """Which frames inside `windows` carry a picture rather than black.

    Sampled at the master's OWN frame rate, so every frame of every
    window is examined and nothing falls between two samples.  Only the
    windows are decoded, which is what keeps this cheap: a healthy render
    has almost nothing to look at.

    Returns `{frame_index: is_black}` keyed on `round(t * fps)`.  A frame
    the video does not have - the audio stream outrunning the video, as
    it does on the craft reference by 0.07s - is simply absent from the
    map and carries no picture.
    """
    seen = {}
    for start, end in windows:
        duration = end - start
        if duration <= 0:
            continue
        for offset, frame in enumerate(
                _stream_raw_frames(video_path, 'gray', 1, width, height,
                                   fps, start_seconds=start,
                                   duration_seconds=duration)):
            index = int(round((start + offset / fps) * fps))
            seen[index] = _frame_is_black(frame[0])
    return seen


def _picture_seconds(run: tuple, picture: dict, fps: float) -> float:
    """Seconds of `run` that have a picture on screen, to the frame."""
    start, end, _ = run
    first = int(math.floor(start * fps))
    last = int(math.ceil(end * fps))
    lit = 0
    for index in range(first, last):
        if picture.get(index, True):        # absent means black or no video
            continue
        covered = min(end, (index + 1) / fps) - max(start, index / fps)
        if covered > 0:
            lit += covered
    return float(lit)


def measure_silence_under_picture(
        video_path: str,
        ladder: Sequence[float] = NEAR_SILENCE_LADDER_DBFS,
        sample_rate: int = 48000) -> RenderQAResult:
    """P8: no stretch of picture plays over digital silence.

    Two halves, and only the first decides anything.

    **The gate** is DIGITAL ZERO under a picture.  A run of samples that
    are all exactly zero, lasting at least `MIN_SILENCE_FRAMES` of the
    master's own timebase, with a frame of picture on screen over it, is
    a defect on its own terms: the plan placed a clip there, so it asked
    for that moment to exist, and the master delivers nothing for a
    listener to hear while it plays.  No taste is involved - the level is
    the delivery quantisation and the duration is the shortest sound this
    pipeline can place.

    **The ladder REPORTS** how much of the render sits under each of
    `NEAR_SILENCE_LADDER_DBFS` while a picture is on screen, and where.
    It fails nothing, because how quiet a declared quiet moment may be is
    an open captain decision and a number chosen here would answer it.

    Black is not picture: a fade, a declared beat, the tail of a film all
    play over nothing on purpose, and `detect_black_frames` owns whether
    the black itself was declared.  A frame counts as black by
    `BLACK_PIXEL_RATIO` of `LIT_LUMA_THRESHOLD`, which is blackdetect's
    own predicate and the one `detect_black_frames` already uses.
    """
    try:
        import numpy as np
    except ImportError as e:  # pragma: no cover - numpy is a hard dependency
        return RenderQAResult("silence_under_picture", False, str(e), None,
                              "error", f"numpy unavailable: {e}")

    try:
        size = _probe_video_size(video_path)
        fps = _probe_frame_rate(video_path)
        if not size or not fps:
            return RenderQAResult(
                metric="silence_under_picture", passed=False, value=None,
                threshold=None, severity="error",
                detail="No video stream to measure a picture against")
        width, height = size

        samples = _decode_mono_pcm16(video_path, sample_rate)
        if samples.size == 0:
            return RenderQAResult(
                metric="silence_under_picture", passed=False, value=None,
                threshold={"digital_zero_dbfs": DIGITAL_ZERO_DBFS},
                severity="error",
                detail="The master carries no decodable audio at all, so "
                       "every frame of picture plays over silence - "
                       "`audio_streams` owns the missing stream itself")

        runtime = samples.size / sample_rate
        magnitude = np.abs(samples.astype(np.int32))
        floor_seconds = MIN_SILENCE_FRAMES / fps

        levels = [DIGITAL_ZERO_DBFS] + [float(x) for x in ladder]
        runs_by_level = {}
        for level in levels:
            ceiling = (0.0 if level <= DIGITAL_ZERO_DBFS
                       else 32768.0 * (10.0 ** (level / 20.0)))
            runs_by_level[level] = _level_runs(magnitude, ceiling,
                                               sample_rate, floor_seconds)

        # One decode pass over the union of everything any rung named.
        windows = _merge_windows([(s, e) for runs in runs_by_level.values()
                                  for s, e, _ in runs])
        picture = _picture_map(video_path, windows, fps, width, height)

        by_level = {}
        for level in levels:
            rows = []
            for run in runs_by_level[level]:
                lit = _picture_seconds(run, picture, fps)
                rows.append({"start": round(run[0], 3),
                             "end": round(run[1], 3),
                             "duration": round(run[2], 3),
                             "seconds_under_picture": round(lit, 3)})
            total = sum(r["duration"] for r in rows)
            lit_total = sum(r["seconds_under_picture"] for r in rows)
            by_level[f"{level:.2f}" if level > DIGITAL_ZERO_DBFS
                     else "digital_zero"] = {
                "dbfs": round(level, 2),
                "runs": len(rows),
                "seconds": round(total, 3),
                "fraction_of_runtime": round(total / runtime, 4) if runtime else 0.0,
                "seconds_under_picture": round(lit_total, 3),
                "fraction_under_picture": (round(lit_total / runtime, 4)
                                           if runtime else 0.0),
                "gates": level <= DIGITAL_ZERO_DBFS,
                "where": rows,
            }

        zero = by_level["digital_zero"]
        offenders = [r for r in zero["where"]
                     if r["seconds_under_picture"] >= floor_seconds]
        faults = []
        for row in offenders:
            faults.append(
                f"{row['seconds_under_picture']:.3f}s of picture plays over "
                f"digital silence at {row['start']:.3f}-{row['end']:.3f}s")

        detail = (
            f"{zero['seconds']:.3f}s of the {runtime:.3f}s master is at "
            f"digital zero ({zero['fraction_of_runtime']:.1%}), "
            f"{zero['seconds_under_picture']:.3f}s of it with a picture on "
            f"screen ({zero['fraction_under_picture']:.1%})")
        near = ", ".join(
            f"{by_level[f'{lvl:.2f}']['seconds_under_picture']:.3f}s under "
            f"{lvl:g} dBFS" for lvl in (float(x) for x in ladder))
        if near:
            detail += f"; reported and not judged: {near}"
        if faults:
            detail += " - " + "; ".join(faults)

        return RenderQAResult(
            metric="silence_under_picture",
            passed=not faults,
            value={
                "runtime_seconds": round(runtime, 3),
                "frame_rate": round(fps, 3),
                "minimum_run_seconds": round(floor_seconds, 4),
                "by_level": by_level,
            },
            threshold={
                "digital_zero_dbfs": DIGITAL_ZERO_DBFS,
                "near_silence_ladder_dbfs": [float(x) for x in ladder],
                "ladder_gates": False,
                "minimum_run_frames": MIN_SILENCE_FRAMES,
                "black_pixel_ratio": BLACK_PIXEL_RATIO,
                "lit_luma_threshold": LIT_LUMA_THRESHOLD,
            },
            severity="error" if faults else "info",
            detail=detail,
        )
    except Exception as e:
        return RenderQAResult("silence_under_picture", False, str(e), None,
                              "error",
                              f"Error measuring silence under picture: {e}")


def verify_resolution(video_path: str, expected_width: int, expected_height: int) -> RenderQAResult:
    """Grade the render's frame against the DECLARED one.

    Both dimensions are REQUIRED: a default here would grade a render
    against a guessed frame, which is the defect this gate exists for
    (AGENTS.md 10.1). Callers pass the delivery format the render was
    built from; a caller with nothing declared reports NOT CHECKED
    (see `run_full_render_qa`) rather than calling this at vertical.
    """
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
        try:
            if os.path.getsize(out_path) <= 0:
                continue
        except OSError:
            continue
        extracted.append(out_path)
            
    return extracted

def run_full_render_qa(video_path: str, expected_duration: float = None,
                       target_lufs: float = DEFAULT_LUFS_TARGET,
                       declared_black_beats: Optional[List] = None,
                       expected_resolution: Optional[List[int]] = None,
                       expected_fps: Optional[float] = None,
                       framing_spans: Optional[Sequence["FramingSpan"]] = None,
                       chroma_floor: Optional[float] = None,
                       music_path: Optional[str] = None,
                       music_automation: Optional[Sequence[dict]] = None,
                       music_offset_seconds: Optional[float] = None,
                       spine_blocks: Optional[Sequence[dict]] = None,
                       overlay_segments: Optional[Sequence["OverlaySegment"]] = None,
                       grade_spans: Optional[Sequence["GradeSpan"]] = None,
                       true_peak_ceiling: float =
                       DEFAULT_TRUE_PEAK_CEILING_DBTP
                       ) -> List[RenderQAResult]:
    """Run every render QA check.

    `declared_black_beats` carries the black beats the plan declared, as
    `spine_contract.declared_black_beat_ranges` returns them, so the
    black-frame check judges the render by the same ruling
    `compile_manifest` judged the manifest by.

    P8 (`measure_silence_under_picture`) needs nothing but the file: it
    finds the stretches of digital zero in the master's own audio and
    decodes only those seconds of picture, so it costs 2.8 seconds on a
    twenty-minute 4K master.

    `expected_resolution` is THE DELIVERY FORMAT, taken from the
    manifest the render was built from. The resolution gate used to
    compare against a hardcoded 1080x1920 while step 6.02 computed the
    manifest's value and dropped it on the floor. That default happened
    to be right, so the gate correctly failed project 001's landscape
    master - but a series that legitimately declares
    `horizontal_1920x1080` would have failed its own correct render. A
    gate has to check what was asked for, not what is usual. None
    means the caller declared nothing, and the resolution result then
    reports NOT CHECKED and fails - never a pass, never a silent
    vertical.

    `framing_spans` are the per-clip framing declarations the manifest
    carries, laid out over the timeline so each sampled frame can be
    judged against the framing that was in force over it;
    `chroma_floor` is the colour floor, which has no default
    because it is an open captain decision; `music_path`,
    `music_automation`, `music_offset_seconds` and `spine_blocks` are
    what P3 needs to fit the bed against the master, and without them P3
    does not run at all rather than guessing at a music file or at where
    in that file the bed starts.  `music_offset_seconds` is separate from
    the other two and checked separately: a caller that has the file and
    the plan but cannot say which SECTION of the track plays does not
    have enough to fit anything, and a default of 0.0 there is the value
    that is silently wrong - see `measure_speech_above_bed`.

    `overlay_segments` are the rendered overlays the manifest places over
    the picture, so P1 can mask the pixels they drew instead of guessing
    at where an overlay sits.  Passing None says nothing was established
    about them, and P1 reports that rather than treating it as none.

    `grade_spans` are the per-clip grades the manifest carries, so P11
    (`measure_grade_delivery`) can judge each graded span's exported
    pixels against the source they were cut from.  None runs no grade
    verdict - a caller that never established the grades says so by
    passing none, not by passing an empty list (which is exact: graded
    nowhere, verified nowhere).
    """
    results = []

    results.append(measure_lufs(
        video_path, target_lufs=target_lufs,
        true_peak_ceiling=true_peak_ceiling))
    results.append(detect_black_frames(video_path, declared_beats=declared_black_beats))
    results.append(detect_freeze_frames(video_path))
    results.append(analyze_color_histogram(video_path))
    results.append(measure_frame_occupancy(video_path,
                                           framing_spans=framing_spans,
                                           overlay_segments=overlay_segments))
    results.append(measure_chroma_presence(video_path,
                                           chroma_floor=chroma_floor))
    results.append(measure_face_intact(video_path))
    results.append(measure_silence_under_picture(video_path))
    if music_path and music_automation and music_offset_seconds is not None:
        results.append(measure_speech_above_bed(
            video_path, music_path, music_automation, music_offset_seconds,
            spine_blocks=spine_blocks))
    # The frame the render was built at, declared by the caller - never
    # defaulted here. A caller that names none gets a FAILING result
    # saying the shape was not checked, never a pass and never a
    # silent vertical: passing an unchecked shape is the exact defect
    # this gate exists for (the slice-1 reels gates read the same way).
    if expected_resolution is None:
        results.append(RenderQAResult(
            "resolution", False, None, None, "error",
            "No declared frame to grade the render against - the shape "
            "was not checked. Pass expected_resolution: the delivery "
            "format the render was built from "
            "(library/tools/delivery_format.py)."))
    else:
        width, height = expected_resolution[:2]
        results.append(verify_resolution(video_path,
                                         expected_width=width,
                                         expected_height=height))
    results.append(verify_framerate(video_path)
                   if expected_fps is None else
                   verify_framerate(video_path, expected_fps=expected_fps))
    
    if expected_duration is not None:
        results.append(verify_duration(video_path, expected_duration))

    results.append(verify_audio_streams(video_path))

    if grade_spans is not None:
        results.append(measure_grade_delivery(video_path, grade_spans))

    return results


# ── Scoped QA: re-check only what a touch changed (`dirty_regions`) ──
#
# The three LOCATED detectors - black, freeze, silence under picture -
# report findings at times, so each can be read over the dirty spans and
# its findings kept where the touch changed frames.  Black and freeze
# decode only the spans; silence already decodes only the silent stretches
# of picture (0.47s on a 46s reel), so it reads the whole file and is
# filtered.  Program-wide measures (integrated loudness, the stream
# probes) are the caller's: a span's loudness is not the master's.


#: Which domain dirties which located detector.
SCOPED_DETECTORS = {
    "black_frames": ("picture",),
    "freeze_frames": ("picture",),
    "silence_under_picture": ("picture", "audio"),
}


def _file_seconds(video_path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, encoding="utf-8", check=True, timeout=60)
    return float(out.stdout.strip())


def run_scoped_render_qa(video_path: str, dirty: dict,
                         declared_black_beats: Optional[List] = None
                         ) -> tuple:
    """The located detectors, over the dirty spans only.

    Returns `(results, not_rechecked)`: one `RenderQAResult` per detector
    a dirty domain routes to, whose findings are those overlapping a
    span's CORE (the frames the edit touched); and the detectors no dirty
    domain reaches, by name.  A finding wholly inside a handle is in
    frames the touch did not change and is not this touch's.
    """
    from library.tools.dirty_regions import spans_for

    results, not_rechecked = [], []
    file_end = _file_seconds(video_path) - 1e-3
    for metric, domains in SCOPED_DETECTORS.items():
        spans = spans_for(dirty, domains)
        if not spans:
            not_rechecked.append(metric)
            continue
        found, errors = [], []
        if metric == "silence_under_picture":
            part = measure_silence_under_picture(video_path)
            parts = [(None, part)]
        elif metric == "black_frames":
            parts = [(span, detect_black_frames(
                video_path, declared_beats=declared_black_beats,
                window=span[:2])) for span in spans]
        else:
            parts = [(span, detect_freeze_frames(
                video_path, window=span[:2])) for span in spans]
        for span, part in parts:
            if part.value is None or isinstance(part.value, str):
                errors.append(part.detail)
                continue
            if metric == "black_frames":
                rows = [r for r in part.value if not r["declared"]]
            elif metric == "freeze_frames":
                rows = [r for r in part.value
                        if not (r.get("open") and span[1] >= file_end)]
            else:
                rows = [r for r in
                        part.value["by_level"]["digital_zero"]["where"]
                        if r["seconds_under_picture"]
                        >= part.value["minimum_run_seconds"]]
            found += [r for r in rows if any(
                r["start"] < hi and r["end"] > lo
                for _, _, lo, hi in ([span] if span else spans))]
        covered = ", ".join(f"{s[2]:.3f}-{s[3]:.3f}s" for s in spans)
        passed = not found and not errors
        detail = (f"over the dirty span(s) {covered}: "
                  + (f"{len(found)} finding(s)" if found
                     else "nothing found"))
        if errors:
            detail += "; NOT MEASURED - " + "; ".join(errors)
        results.append(RenderQAResult(
            metric=metric, passed=passed, value=found,
            threshold={"spans": [list(s) for s in spans]},
            severity="info" if passed else "error", detail=detail))
    return results, not_rechecked
