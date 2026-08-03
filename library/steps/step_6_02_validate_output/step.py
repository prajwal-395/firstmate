#!/usr/bin/env python3
"""
Step 6.02: Validate Output

Automated pre-checks on the rendered video before (optionally) handing
off to the LLM for subjective quality review.

Checks:
  1. File existence and reasonable size
  2. Technical validation via ffprobe (resolution, duration, codec, audio)
  3. Duration comparison against manifest expected duration
  4. Black frame detection (render sample frames, check file sizes)
  5. Audio presence and level check

Classification: Nondeterministic / Evaluation & Judgment
Input:  { "rendered_output": {...}, "assembly_manifest": {...} }
Output: { "validation_result": { status, checks, ... } }
"""
import json
import os
import subprocess
import sys
import tempfile


def _run_ffprobe(filepath, *args):
    """Run ffprobe and return parsed JSON output."""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            *args,
            filepath,
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, FileNotFoundError,
            json.JSONDecodeError, OSError):
        return None


def _extract_frame(filepath, frame_num, output_path, fps=30):
    """Extract a single frame as PNG using ffmpeg. Returns True on success."""
    timestamp = frame_num / fps
    try:
        result = subprocess.run(
            ['ffmpeg', '-y', '-ss', f'{timestamp:.3f}',
             '-i', filepath, '-frames:v', '1',
             '-f', 'image2', output_path],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def validate_output(rendered_output: dict, assembly_manifest: dict) -> dict:
    """Run automated validation checks on the rendered video.

    Args:
        rendered_output: Dict with at least 'output_path' pointing to the
            rendered video file.
        assembly_manifest: The manifest used to build the timeline, providing
            expected duration, resolution, and track counts.

    Returns:
        Validation result dict with per-check results and overall status.
    """
    video_path = rendered_output.get('output_path', '')
    project_settings = assembly_manifest.get('project', {})
    expected_fps = project_settings.get('frame_rate', 30)
    expected_resolution = project_settings.get('resolution', [1080, 1920])
    expected_duration = project_settings.get('duration_seconds', 0)

    checks = {}

    # ── Check 1: File existence and size ──
    file_check = {"pass": False, "issues": []}
    if not video_path or not os.path.exists(video_path):
        file_check["issues"].append(
            f"Rendered file not found: {video_path}")
    else:
        size_bytes = os.path.getsize(video_path)
        size_mb = size_bytes / (1024 * 1024)
        file_check["size_mb"] = round(size_mb, 2)

        if size_bytes < 100_000:  # < 100KB is almost certainly broken
            file_check["issues"].append(
                f"File suspiciously small: {size_mb:.2f} MB")
        else:
            file_check["pass"] = True

    checks["file_exists"] = file_check

    if not file_check["pass"]:
        # Can't proceed without a valid file
        return {
            "status": "fail",
            "checks": checks,
            "distribution_ready": False,
            "summary": "Rendered file missing or empty",
        }

    # ── Check 2: Technical validation via ffprobe ──
    tech_check = {"pass": False, "issues": []}
    probe = _run_ffprobe(video_path, '-show_format', '-show_streams')

    if not probe:
        tech_check["issues"].append("ffprobe failed to read file")
    else:
        streams = probe.get('streams', [])
        video_streams = [s for s in streams if s.get('codec_type') == 'video']
        audio_streams = [s for s in streams if s.get('codec_type') == 'audio']

        # Video stream checks
        if not video_streams:
            tech_check["issues"].append("No video stream found")
        else:
            vs = video_streams[0]
            width = int(vs.get('width', 0))
            height = int(vs.get('height', 0))
            tech_check["resolution"] = f"{width}x{height}"

            if (width != expected_resolution[0]
                    or height != expected_resolution[1]):
                tech_check["issues"].append(
                    f"Resolution mismatch: got {width}x{height}, "
                    f"expected {expected_resolution[0]}x{expected_resolution[1]}")

            # Frame rate check
            fps_str = vs.get('r_frame_rate', '0/1')
            if '/' in fps_str:
                num, den = fps_str.split('/')
                actual_fps = int(num) / max(int(den), 1)
            else:
                actual_fps = float(fps_str)
            tech_check["fps"] = round(actual_fps, 2)

            if abs(actual_fps - expected_fps) > 1.0:
                tech_check["issues"].append(
                    f"FPS mismatch: got {actual_fps:.2f}, "
                    f"expected {expected_fps}")

        # Audio stream checks
        if not audio_streams:
            tech_check["issues"].append("No audio stream found")
        else:
            tech_check["audio_streams"] = len(audio_streams)

        if not tech_check["issues"]:
            tech_check["pass"] = True

    checks["technical"] = tech_check

    # ── Check 3: Duration comparison ──
    duration_check = {"pass": False, "issues": []}
    fmt = probe.get('format', {}) if probe else {}
    actual_duration = float(fmt.get('duration', 0))
    duration_check["actual_seconds"] = round(actual_duration, 2)
    duration_check["expected_seconds"] = round(expected_duration, 2)

    if actual_duration == 0:
        duration_check["issues"].append("Could not determine duration")
    elif expected_duration > 0:
        drift = abs(actual_duration - expected_duration)
        drift_pct = (drift / expected_duration) * 100
        duration_check["drift_seconds"] = round(drift, 2)
        duration_check["drift_percent"] = round(drift_pct, 1)

        if drift_pct > 10:
            duration_check["issues"].append(
                f"Duration drift too large: {drift:.2f}s "
                f"({drift_pct:.1f}% off expected {expected_duration:.1f}s)")
        else:
            duration_check["pass"] = True
    else:
        # No expected duration to compare, just check it's reasonable
        if 10 <= actual_duration <= 120:
            duration_check["pass"] = True
        else:
            duration_check["issues"].append(
                f"Duration {actual_duration:.1f}s outside expected "
                f"30-60s range for shortform content")

    checks["duration"] = duration_check

    # ── Check 4: Black frame detection ──
    black_frame_check = {"pass": False, "issues": []}
    if actual_duration > 0:
        # Sample frames at 10%, 25%, 50%, 75%, 90% of the video
        sample_points = [0.1, 0.25, 0.5, 0.75, 0.9]
        total_frames = int(actual_duration * expected_fps)
        sample_frames = [int(p * total_frames) for p in sample_points]

        black_frames = []
        with tempfile.TemporaryDirectory() as tmpdir:
            for frame_num in sample_frames:
                png_path = os.path.join(tmpdir, f"frame_{frame_num}.png")
                extracted = _extract_frame(
                    video_path, frame_num, png_path, expected_fps)

                if extracted and os.path.exists(png_path):
                    frame_size = os.path.getsize(png_path)
                    # < 2KB is almost certainly a black/blank frame
                    if frame_size < 2048:
                        black_frames.append({
                            "frame": frame_num,
                            "time": round(frame_num / expected_fps, 2),
                            "size_bytes": frame_size,
                        })
                elif not extracted:
                    black_frames.append({
                        "frame": frame_num,
                        "time": round(frame_num / expected_fps, 2),
                        "error": "extraction failed",
                    })

        black_frame_check["sampled"] = len(sample_frames)
        black_frame_check["black_frames"] = black_frames

        if black_frames:
            positions = [f"{bf['time']:.1f}s" for bf in black_frames]
            black_frame_check["issues"].append(
                f"Black/blank frames detected at: {', '.join(positions)}")
        else:
            black_frame_check["pass"] = True
    else:
        black_frame_check["issues"].append(
            "Skipped: could not determine duration")

    checks["black_frames"] = black_frame_check

    # ── Check 5: Audio level check ──
    audio_check = {"pass": False, "issues": []}
    try:
        result = subprocess.run(
            ['ffmpeg', '-i', video_path,
             '-af', 'volumedetect', '-f', 'null', '-'],
            capture_output=True, text=True, timeout=60,
        )
        stderr = result.stderr
        # Parse volumedetect output
        for line in stderr.split('\n'):
            if 'mean_volume' in line:
                parts = line.split('mean_volume:')
                if len(parts) > 1:
                    vol_str = parts[1].strip().replace(' dB', '')
                    try:
                        mean_vol = float(vol_str)
                        audio_check["mean_volume_db"] = mean_vol

                        if mean_vol < -40:
                            audio_check["issues"].append(
                                f"Audio very quiet: {mean_vol:.1f} dB mean")
                        elif mean_vol > -3:
                            audio_check["issues"].append(
                                f"Audio may be clipping: {mean_vol:.1f} dB mean")
                    except ValueError:
                        pass

            if 'max_volume' in line:
                parts = line.split('max_volume:')
                if len(parts) > 1:
                    vol_str = parts[1].strip().replace(' dB', '')
                    try:
                        max_vol = float(vol_str)
                        audio_check["max_volume_db"] = max_vol

                        if max_vol >= 0:
                            audio_check["issues"].append(
                                f"Audio clipping detected: {max_vol:.1f} dB peak")
                    except ValueError:
                        pass

        if not audio_check["issues"]:
            if "mean_volume_db" in audio_check:
                audio_check["pass"] = True
            else:
                audio_check["issues"].append("Could not parse audio levels")

    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        audio_check["issues"].append(f"Audio analysis failed: {e}")

    checks["audio_levels"] = audio_check

    # ── Aggregate result ──
    all_passed = all(c.get("pass", False) for c in checks.values())
    critical_passed = all(
        checks.get(k, {}).get("pass", False)
        for k in ["file_exists", "technical", "black_frames"]
    )

    all_issues = []
    for name, check in checks.items():
        for issue in check.get("issues", []):
            all_issues.append(f"[{name}] {issue}")

    return {
        "status": "pass" if all_passed else "fail",
        "checks": checks,
        "all_issues": all_issues,
        "distribution_ready": all_passed,
        "critical_checks_passed": critical_passed,
        "summary": (
            "All automated checks passed"
            if all_passed
            else f"{len(all_issues)} issue(s) found"
        ),
        "recommended_action": (
            None if all_passed
            else "Review issues and re-render if critical checks failed"
        ),
    }


def _validate_build_result(build_result: dict, assembly_manifest: dict) -> dict:
    """Validate the timeline build result when no rendered file exists yet.

    This runs when step 6.01 has built the Resolve timeline but hasn't
    triggered a render pass. It validates the build report instead.
    """
    checks = {}
    project = assembly_manifest.get('project', {})

    # Check build success
    build_check = {"pass": False, "issues": []}
    if build_result.get("success"):
        build_check["pass"] = True
    else:
        build_check["issues"].append("Timeline build reported failure")

    if build_result.get("errors"):
        for err in build_result["errors"]:
            build_check["issues"].append(f"Build error: {err}")
        build_check["pass"] = False

    checks["build_success"] = build_check

    # Check track counts match manifest expectations
    track_check = {"pass": True, "issues": []}
    built_tracks = build_result.get("tracks", {})
    manifest_tracks = assembly_manifest.get("tracks", {})

    for track_name in ("V1", "V2", "A2"):
        expected_clips = len(manifest_tracks.get(track_name, {}).get("clips", []))
        actual_clips = built_tracks.get(track_name, 0)
        if expected_clips > 0 and actual_clips == 0:
            track_check["issues"].append(
                f"{track_name}: expected {expected_clips} clips, got 0")
            track_check["pass"] = False

    checks["track_counts"] = track_check

    # Check warnings
    warning_check = {"pass": True, "issues": []}
    warnings = build_result.get("warnings", [])
    if warnings:
        warning_check["issues"] = [f"Build warning: {w}" for w in warnings]
        # Warnings don't fail the check, just report

    checks["build_warnings"] = warning_check

    all_passed = all(c.get("pass", False) for c in checks.values())
    all_issues = []
    for name, check in checks.items():
        for issue in check.get("issues", []):
            all_issues.append(f"[{name}] {issue}")

    return {
        "status": "pass" if all_passed else "fail",
        "mode": "build_validation",
        "checks": checks,
        "all_issues": all_issues,
        "distribution_ready": False,  # Not rendered yet
        "critical_checks_passed": checks["build_success"]["pass"],
        "summary": (
            "Timeline build validated - ready for render"
            if all_passed
            else f"{len(all_issues)} issue(s) found in build"
        ),
        "recommended_action": (
            "Render the timeline in DaVinci Resolve, then re-run validation "
            "with output_path set" if all_passed
            else "Fix build errors and re-run step 6.01"
        ),
    }


def main():
    input_data = json.loads(sys.stdin.read())

    rendered_output = input_data.get("rendered_output", {})
    assembly_manifest = input_data.get("assembly_manifest", {})

    # Two modes: file validation (post-render) or build validation (post-build)
    if rendered_output.get("output_path"):
        result = validate_output(rendered_output, assembly_manifest)
    else:
        # No rendered file yet - validate the build result from step 6.01
        result = _validate_build_result(rendered_output, assembly_manifest)

    json.dump({"validation_result": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
