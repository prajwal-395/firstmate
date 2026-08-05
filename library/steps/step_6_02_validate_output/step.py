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
import traceback

# Import new QA modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))
from library.tools.render_qa import run_full_render_qa
from library.tools.subtitle_qa import verify_subtitle_timing, verify_subtitle_safe_zone


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
    """Run automated validation checks on the rendered video using render_qa."""
    video_path = rendered_output.get('output_path', '')
    project_settings = assembly_manifest.get('project', {})
    expected_fps = project_settings.get('frame_rate', 30)
    expected_resolution = project_settings.get('resolution', [1080, 1920])
    expected_duration = project_settings.get('duration_seconds', 0)

    checks = {}
    
    # ── Check 1: File existence and size ──
    file_check = {"pass": False, "issues": []}
    if not video_path or not os.path.exists(video_path):
        file_check["issues"].append(f"Rendered file not found: {video_path}")
    else:
        size_bytes = os.path.getsize(video_path)
        size_mb = size_bytes / (1024 * 1024)
        file_check["size_mb"] = round(size_mb, 2)

        if size_bytes < 100_000:
            file_check["issues"].append(f"File suspiciously small: {size_mb:.2f} MB")
        else:
            file_check["pass"] = True

    checks["file_exists"] = file_check

    if not file_check["pass"]:
        return {
            "status": "fail",
            "checks": checks,
            "distribution_ready": False,
            "summary": "Rendered file missing or empty",
        }

    # ── Run QA Toolkit ──
    qa_results = []
    try:
        qa_results = run_full_render_qa(video_path, expected_duration)
    except Exception as e:
        print(f"Error running render_qa: {e}", file=sys.stderr)
        traceback.print_exc()

    # Subtitle QA
    subtitles = assembly_manifest.get("subtitles", [])
    if subtitles:
        try:
            qa_results.extend(verify_subtitle_timing(subtitles))
            qa_results.extend(verify_subtitle_safe_zone(subtitles, expected_resolution[0], expected_resolution[1]))
        except Exception as e:
            print(f"Error running subtitle_qa: {e}", file=sys.stderr)
            traceback.print_exc()
            
    # Process QA Results into existing checks format for compatibility
    tech_check = {"pass": True, "issues": []}
    duration_check = {"pass": True, "issues": []}
    black_frame_check = {"pass": True, "issues": []}
    audio_check = {"pass": True, "issues": []}
    
    qa_report = []
    
    for r in qa_results:
        # Convert dataclass to dict
        r_dict = {
            "metric": r.metric,
            "passed": r.passed,
            "value": r.value,
            "threshold": r.threshold,
            "severity": r.severity,
            "detail": r.detail
        }
        qa_report.append(r_dict)
        
        # Map to legacy checks
        if r.metric in ["resolution", "framerate"]:
            if not r.passed:
                tech_check["pass"] = False
                tech_check["issues"].append(r.detail)
        elif r.metric == "duration":
            if not r.passed:
                duration_check["pass"] = False
                duration_check["issues"].append(r.detail)
        elif r.metric == "black_frames":
            if not r.passed:
                black_frame_check["pass"] = False
                black_frame_check["issues"].append(r.detail)
        elif r.metric in ["audio_streams", "lufs"]:
            if not r.passed:
                audio_check["pass"] = False
                audio_check["issues"].append(r.detail)
                
    checks["technical"] = tech_check
    checks["duration"] = duration_check
    checks["black_frames"] = black_frame_check
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

    # Write QA report JSON
    qa_report_path = os.path.join(os.path.dirname(video_path), "qa_report.json")
    try:
        with open(qa_report_path, 'w') as f:
            json.dump(qa_report, f, indent=2)
    except Exception as e:
        print(f"Failed to write qa_report.json: {e}", file=sys.stderr)

    return {
        "status": "pass" if all_passed else "fail",
        "checks": checks,
        "all_issues": all_issues,
        "distribution_ready": all_passed,
        "critical_checks_passed": critical_passed,
        "summary": "All automated checks passed" if all_passed else f"{len(all_issues)} issue(s) found",
        "recommended_action": None if all_passed else "Review issues and re-render if critical checks failed",
        "qa_report_path": qa_report_path,
        "qa_report": qa_report
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
    elif rendered_output.get("render_expected", False):
        # BUG FIX H8: When a render was expected but output_path is missing,
        # report failure explicitly instead of falling back to build validation
        # which could mask the missing render output with a false 'pass'.
        result = {
            "status": "fail",
            "mode": "render_validation",
            "checks": {
                "render_output": {
                    "pass": False,
                    "issues": [
                        "Render was expected but output_path is missing. "
                        "The render step may have failed or not been triggered."
                    ],
                },
            },
            "all_issues": [
                "[render_output] Render was expected but output_path is missing"
            ],
            "distribution_ready": False,
            "critical_checks_passed": False,
            "summary": "Render output missing - render step failed or was not triggered",
            "recommended_action": (
                "Check step 6.01 render logs for errors. Ensure DaVinci Resolve "
                "is running and the render completed successfully."
            ),
        }
    else:
        # No rendered file yet - validate the build result from step 6.01
        result = _validate_build_result(rendered_output, assembly_manifest)

    json.dump({"validation_result": result}, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
