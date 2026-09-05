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

def check_geometry(video_path: str) -> List[RenderCheckFinding]:
    try:
        from library.tools.render_qa import _probe_video_size
        size = _probe_video_size(video_path)
        if size is None:
            return [RenderCheckFinding("geometry", False, "Could not probe video size")]
        width, height = size
        passed = (width == 1080 and height == 1920)
        msg = f"Geometry is {width}x{height}" if passed else f"Geometry is {width}x{height}, expected 1080x1920"
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
    subtitle_overlay = plan_data.get("subtitle_overlay", {})
    segments = subtitle_overlay.get("segments", [])
    if not segments:
        return [RenderCheckFinding("captions", True, "No captions planned")]
        
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
            
        t_sample = start + min(0.5, dur / 2)
        t_overlay = t_sample - start
        
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

        alpha = get_overlay_plane(t_overlay, 'alpha')
        overlay_luma = get_overlay_plane(t_overlay, 'gray')
        
        if alpha is None or overlay_luma is None:
            failures.append(RenderCheckFinding("captions", False, f"Caption {i} overlay could not be read"))
            continue
            
        if np.max(alpha) < 10:
            failures.append(RenderCheckFinding("captions", False, f"Caption {i} overlay is blank"))
            continue
            
        alpha_mask = alpha > 128
        
        def get_frame(t):
            cmd = ['ffmpeg', '-nostdin', '-ss', str(t), '-i', video_path, '-vframes', '1', '-f', 'image2pipe', '-vcodec', 'rawvideo', '-pix_fmt', 'gray', '-']
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            b = p.stdout.read()
            p.wait()
            if len(b) > 0:
                return np.frombuffer(b, dtype=np.uint8)
            return None
            
        frame_during = get_frame(t_sample)
        
        if frame_during is None:
            failures.append(RenderCheckFinding("captions", False, f"Caption {i} could not extract frames"))
            continue
            
        if len(frame_during) != len(alpha_mask):
            failures.append(RenderCheckFinding("captions", False, f"Caption {i} resolution mismatch between overlay and video"))
            continue
            
        rendered_caption = frame_during[alpha_mask]
        expected_caption = overlay_luma[alpha_mask]
        
        diff = np.abs(rendered_caption.astype(np.int32) - expected_caption.astype(np.int32))
        mad = np.mean(diff)
        
        if mad > 50.0:
            failures.append(RenderCheckFinding("captions", False, f"Caption {i} not visibly drawn (MAD={mad:.1f})"))
            
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


def run_checks(video_path: str, plan_data: dict) -> List[RenderCheckFinding]:
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
    findings.extend(check_geometry(video_path))
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
