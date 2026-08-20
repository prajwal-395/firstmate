import json
import subprocess
import os
import tempfile
import re
from dataclasses import dataclass
from typing import Any, List, Optional

try:
    from library.tools.spine_contract import MAX_DECLARED_BLACK_BEAT_SECONDS
except ImportError:  # imported as a top-level module from library/tools
    from spine_contract import MAX_DECLARED_BLACK_BEAT_SECONDS

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

def measure_lufs(video_path: str, target_lufs: float = -14.0, tolerance: float = 2.0) -> RenderQAResult:
    """Measure integrated LUFS using ffmpeg loudnorm filter."""
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
        
        passed = abs(input_i - target_lufs) <= tolerance
        severity = "error" if not passed else "info"
        if passed and input_tp > -1.0:
            severity = "warning"
            
        detail = f"LUFS: {input_i:.2f}, True Peak: {input_tp:.2f}"
        
        return RenderQAResult(
            metric="lufs",
            passed=passed,
            value={"input_i": input_i, "input_tp": input_tp},
            threshold={"target_lufs": target_lufs, "tolerance": tolerance},
            severity=severity,
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
    """Sample frames at key moments and analyze color distribution."""
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
                
                # SATAVG is typically 0-255 in signalstats
                if satavg < 10:
                    issues.append(f"Extremely low saturation at {t:.2f}s (sat={satavg:.1f})")
            except Exception as e:
                pass

    passed = len(issues) == 0
    detail = "Color levels look normal" if passed else "; ".join(issues)
    
    return RenderQAResult(
        metric="color_histogram",
        passed=passed,
        value=frames_data,
        threshold={"min_y": 16, "max_y": 240, "min_sat": 10},
        severity="warning" if not passed else "info",
        detail=detail
    )

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
                       expected_fps: Optional[float] = None) -> List[RenderQAResult]:
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
    """
    results = []

    width, height = (expected_resolution or [1080, 1920])[:2]

    results.append(measure_lufs(video_path, target_lufs=target_lufs))
    results.append(detect_black_frames(video_path, declared_beats=declared_black_beats))
    results.append(detect_freeze_frames(video_path))
    results.append(analyze_color_histogram(video_path))
    results.append(verify_resolution(video_path, expected_width=width,
                                     expected_height=height))
    results.append(verify_framerate(video_path)
                   if expected_fps is None else
                   verify_framerate(video_path, expected_fps=expected_fps))
    
    if expected_duration is not None:
        results.append(verify_duration(video_path, expected_duration))
        
    results.append(verify_audio_streams(video_path))
    
    return results
