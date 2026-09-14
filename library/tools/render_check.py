import json
import subprocess
import tempfile
import sys
import numpy as np
from dataclasses import dataclass
from typing import List, Optional, Tuple
from library.tools.reel_conformance_verifier import ReelPlan, PlannedPlacement, PlannedCaption

@dataclass
class RenderCheckFinding:
    metric: str
    passed: bool
    message: str

def check_geometry(video_path: str,
                   expected: Optional[Tuple[int, int]] = None
                   ) -> List[RenderCheckFinding]:
    """Is the rendered file the frame the project DECLARED?

    `expected` is the delivery format the caller resolved
    (`library/tools/delivery_format.py`). This was `width == 1080 and
    height == 1920` written in, which is a gate that FAILS correct
    output (AGENTS.md 10.4): a project declaring 16:9 long-form would
    have every correct render marked a defect for being what it asked
    for. A caller that names no frame gets a FAILING finding saying the
    shape was not checked - never a pass, because passing an unchecked
    shape is the exact defect this check exists for.
    """
    try:
        from library.tools.render_qa import _probe_video_size
        size = _probe_video_size(video_path)
        if size is None:
            return [RenderCheckFinding("geometry", False, "Could not probe video size")]
        width, height = size
        if expected is None:
            return [RenderCheckFinding(
                "geometry", False,
                f"Geometry is {width}x{height} and nothing declared the "
                f"frame it should be - not checked")]
        exp_w, exp_h = int(expected[0]), int(expected[1])
        passed = (width == exp_w and height == exp_h)
        msg = f"Geometry is {width}x{height}" if passed else f"Geometry is {width}x{height}, expected {exp_w}x{exp_h}"
        return [RenderCheckFinding("geometry", passed, msg)]
    except Exception as e:
        return [RenderCheckFinding("geometry", False, f"Geometry check failed: {e}")]

def check_duration(video_path: str, plan_seconds: float) -> List[RenderCheckFinding]:
    try:
        cmd = ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=15, check=True)
        duration = float(res.stdout.strip())
        # Matches within a frame (1/24 = 0.0416s, we use 0.05s)
        passed = abs(duration - plan_seconds) <= 0.05
        msg = f"Duration {duration:.2f}s matches plan {plan_seconds:.2f}s" if passed else f"Duration {duration:.2f}s differs from plan {plan_seconds:.2f}s by more than a frame"
        return [RenderCheckFinding("duration", passed, msg)]
    except Exception as e:
        return [RenderCheckFinding("duration", False, f"Duration check failed: {e}")]


def measure_segment_lufs(video_path: str, start: float, end: float) -> float:
    cmd = [
        'ffmpeg', '-ss', str(start), '-i', video_path, '-t', str(end - start),
        '-af', 'loudnorm=print_format=json', '-f', 'null', '-'
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)
    json_str = ""
    in_json = False
    for line in res.stderr.splitlines():
        if line.strip() == "{": in_json = True
        if in_json: json_str += line + "\n"
        if line.strip() == "}": break
    if not json_str: return -99.0
    try:
        data = json.loads(json_str)
        return float(data.get("input_i", -99.0))
    except (json.JSONDecodeError, ValueError):
        return -99.0

def check_audio_speakers(video_path: str, placements: Tuple[PlannedPlacement, ...]) -> List[RenderCheckFinding]:
    speaker_segments = {}
    for p in placements:
        spk = p.speaker or "unknown"
        start = p.record_seconds
        dur = p.source_out - p.source_in
        speaker_segments.setdefault(spk, []).append((start, start + dur))
    
    findings = []
    # If there are fewer than 2 speakers, we can't check BOTH speakers
    speakers = list(speaker_segments.keys())
    
    for spk, segments in speaker_segments.items():
        # Measure LUFS for the first few seconds of this speaker to avoid long ffmpeg calls
        # Or measure the longest segment
        segments.sort(key=lambda x: x[1]-x[0], reverse=True)
        test_seg = segments[0]
        lufs = measure_segment_lufs(video_path, test_seg[0], test_seg[1])
        
        # Target is usually -14 LUFS. If it's below -24, it's likely just the music bed or silence.
        if lufs < -24.0:
            findings.append(RenderCheckFinding("audio_speakers", False, f"Speaker {spk} audio is missing or too quiet ({lufs:.1f} LUFS)"))
        else:
            findings.append(RenderCheckFinding("audio_speakers", True, f"Speaker {spk} audio is present ({lufs:.1f} LUFS)"))
            
    if not findings:
        findings.append(RenderCheckFinding("audio_speakers", True, "No speakers to check"))
        
    return findings

def check_captions(video_path: str, plan_data: dict) -> List[RenderCheckFinding]:
    if not isinstance(plan_data, dict):
        return [RenderCheckFinding("captions", False, "No plan data provided (cannot verify captions)")]

    if "subtitle_overlay" not in plan_data and "captions" not in plan_data:
        return [RenderCheckFinding("captions", False, "Caption plan data is absent (cannot verify captions)")]

    subtitle_overlay = plan_data.get("subtitle_overlay")
    if subtitle_overlay is not None:
        if not isinstance(subtitle_overlay, dict) or "segments" not in subtitle_overlay:
            return [RenderCheckFinding("captions", False, "Invalid or missing 'segments' in subtitle_overlay plan")]
        segments = subtitle_overlay.get("segments", [])
    else:
        segments = plan_data.get("captions", [])

    if not segments:
        return [RenderCheckFinding("captions", True, "Zero captions planned")]
        
    failures = []
    
    for i, seg in enumerate(segments):
        overlay_path = seg.get("overlay_path")
        start = seg.get("timeline_start")
        end = seg.get("timeline_end")
        
        if not overlay_path or start is None or end is None:
            failures.append(RenderCheckFinding("captions", False, f"Caption {i} missing metadata"))
            continue
            
        dur = end - start
        if dur <= 0:
            failures.append(RenderCheckFinding("captions", False, f"Caption {i} has invalid duration"))
            continue

        # Probe the SPAN, not one instant. A check that samples one
        # instant of a span cannot see a span that changes: the overlay
        # file may cut or fade mid-segment, so the one sample can land
        # on a blank frame while the render draws the caption on either
        # side of it (D5's single-pick shape, fixed there by union over
        # a triplet; `subtitle_qa` already probes with
        # `find_inked_timestamps`). The overlay is blank only when every
        # probe across the span is blank, and the caption counts as
        # drawn when any inked probe matches the render.
        track_fps = 30.0
        if isinstance(subtitle_overlay, dict):
            try:
                track_fps = float(subtitle_overlay.get("fps") or 30.0)
            except (TypeError, ValueError):
                # Declared but unreadable: probing at a guessed rate
                # would judge the caption off the wrong seconds, and a
                # pass there would be a default presented as a
                # measurement (AGENTS.md 10.3). Fail closed instead.
                failures.append(RenderCheckFinding("captions", False, f"Caption {i} overlay fps is unreadable (cannot verify placement)"))
                continue
        try:
            source_in = float(seg.get("source_in_frame") or 0.0) / track_fps
        except (TypeError, ValueError):
            failures.append(RenderCheckFinding("captions", False, f"Caption {i} source offset is unreadable (cannot verify placement)"))
            continue
        n_probes = 5
        t_samples = [start + dur * (k + 0.5) / n_probes for k in range(n_probes)]
        
        def get_overlay_plane(t, fmt, overlay_path=overlay_path):
            if fmt == 'alpha':
                vf = 'alphaextract,format=gray'
            else:
                vf = 'format=gray'
            cmd = ['ffmpeg', '-nostdin', '-ss', str(t), '-i', overlay_path, '-vframes', '1', '-vf', vf, '-f', 'image2pipe', '-vcodec', 'rawvideo', '-pix_fmt', 'gray', '-']
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            b = p.stdout.read()
            p.wait()
            if len(b) > 0:
                return np.frombuffer(b, dtype=np.uint8)
            return None

        def get_frame(t):
            cmd = ['ffmpeg', '-nostdin', '-ss', str(t), '-i', video_path, '-vframes', '1', '-f', 'image2pipe', '-vcodec', 'rawvideo', '-pix_fmt', 'gray', '-']
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            b = p.stdout.read()
            p.wait()
            if len(b) > 0:
                return np.frombuffer(b, dtype=np.uint8)
            return None

        best_mad = None
        saw_ink = False
        saw_unreadable = False
        mismatch = False
        for t_sample in t_samples:
            t_overlay = source_in + (t_sample - start)
            probe_alpha = get_overlay_plane(t_overlay, 'alpha')
            if probe_alpha is None:
                saw_unreadable = True
                continue
            if np.max(probe_alpha) < 10:
                continue
            probe_luma = get_overlay_plane(t_overlay, 'gray')
            if probe_luma is None:
                saw_unreadable = True
                continue
            probe_mask = probe_alpha > 128

            frame_probe = get_frame(t_sample)

            if frame_probe is None:
                saw_unreadable = True
                continue

            if len(frame_probe) != len(probe_mask):
                failures.append(RenderCheckFinding("captions", False, f"Caption {i} resolution mismatch between overlay and video"))
                mismatch = True
                break

            saw_ink = True
            rendered_caption = frame_probe[probe_mask]
            expected_caption = probe_luma[probe_mask]

            diff = np.abs(rendered_caption.astype(np.int32) - expected_caption.astype(np.int32))
            mad = float(np.mean(diff))
            if best_mad is None or mad < best_mad:
                best_mad = mad
            if mad <= 50.0:
                break

        if mismatch:
            continue
        if not saw_ink:
            if saw_unreadable:
                failures.append(RenderCheckFinding("captions", False, f"Caption {i} overlay could not be read"))
            else:
                failures.append(RenderCheckFinding("captions", False, f"Caption {i} overlay is blank at all {n_probes} probes across the span"))
            continue
        if best_mad is not None and best_mad > 50.0:
            failures.append(RenderCheckFinding("captions", False, f"Caption {i} not visibly drawn (MAD={best_mad:.1f})"))
            
    if not failures:
        return [RenderCheckFinding("captions", True, f"All {len(segments)} captions visible")]
    return failures




def check_black_and_freeze(video_path: str, plan_master_holes: List[dict] = None) -> List[RenderCheckFinding]:
    from library.tools.render_qa import detect_black_frames, detect_freeze_frames
    
    # Check for 1-frame holes as well (fps=24 -> ~0.041s)
    # The minimum duration should be just under 1 frame
    black_res = detect_black_frames(video_path, min_duration=0.03)
    freeze_res = detect_freeze_frames(video_path, min_duration=1.0)
    
    findings = []
    
    # Process black frames
    if not black_res.passed:
        for seg in black_res.value:
            # Check if it aligns with an inherited master hole
            is_inherited = False
            if plan_master_holes:
                for mh in plan_master_holes:
                    # mh might have "at_seconds", "length"
                    mh_sec = mh.get("at_seconds", -1)
                    if abs(mh_sec - seg["start"]) < 0.1:
                        is_inherited = True
                        break
            
            dur_frames = int(round(seg["duration"] * 24))
            if is_inherited:
                findings.append(RenderCheckFinding("black_frames", True, f"Inherited {dur_frames}-frame picture hole at {seg['start']:.2f}s (Warning)"))
            else:
                findings.append(RenderCheckFinding("black_frames", False, f"Undeclared {dur_frames}-frame black hole at {seg['start']:.2f}s"))
    else:
        findings.append(RenderCheckFinding("black_frames", True, "No undeclared black frames detected"))
        
    # Process freeze frames
    if not freeze_res.passed:
        for seg in freeze_res.value:
            findings.append(RenderCheckFinding("freeze_frames", False, f"Frozen frames detected at {seg.get('start', 0):.2f}s for {seg.get('duration', 0):.2f}s"))
    else:
        findings.append(RenderCheckFinding("freeze_frames", True, "No freeze frames detected"))
        
    return findings


def run_checks(video_path: str, plan_data: dict,
               expected_frame: Optional[Tuple[int, int]] = None
               ) -> List[RenderCheckFinding]:
    """Grade a rendered reel against the plan it was built from.

    `expected_frame` is the project's declared delivery format; without
    it `check_geometry` reports the shape as unchecked rather than
    passing it.
    """
    plan_seconds = plan_data.get("plan_seconds", 0.0)
    
    # Placements
    placements = []
    for p in plan_data.get("placements", []):
        placements.append(PlannedPlacement(
            track_index=p.get("track_index", 1),
            speaker=p.get("speaker"),
            record_seconds=p.get("record_seconds", 0.0),
            source_in=p.get("source_in", 0.0),
            source_out=p.get("source_out", 0.0),
            source_file=p.get("source_file", "")
        ))
        
    # Captions
    captions = []
    for c in plan_data.get("captions", []):
        captions.append(PlannedCaption(
            start_seconds=c.get("start_seconds", 0.0),
            end_seconds=c.get("end_seconds", 0.0),
            text=c.get("text", ""),
            speaker=c.get("speaker"),
            frames=c.get("frames", 0)
        ))
        
    master_holes = plan_data.get("master_holes", [])
    
    findings = []
    findings.extend(check_geometry(video_path, expected_frame))
    findings.extend(check_duration(video_path, plan_seconds))
    findings.extend(check_audio_speakers(video_path, tuple(placements)))
    findings.extend(check_captions(video_path, plan_data))
    findings.extend(check_black_and_freeze(video_path, master_holes))
    return findings

def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Rendered Reel Checker")
    parser.add_argument("--video", required=True, help="Rendered reel .mov/.mp4")
    parser.add_argument("--plan", required=True, help="JSON file containing the reel plan")
    parser.add_argument("--json", help="Path to output JSON")
    args = parser.parse_args(argv)
    
    with open(args.plan, "r") as f:
        plan_data = json.load(f)
        
    findings = run_checks(args.video, plan_data)
    
    failures = [f for f in findings if not f.passed]
    
    report = {
        "video": args.video,
        "passed": len(failures) == 0,
        "findings": [{"metric": f.metric, "passed": f.passed, "message": f.message} for f in findings]
    }
    
    for f in findings:
        status = "PASS" if f.passed else "FAIL"
        print(f"[{status}] {f.metric}: {f.message}", file=sys.stderr)
        
    if args.json:
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2)
            
    if failures:
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
