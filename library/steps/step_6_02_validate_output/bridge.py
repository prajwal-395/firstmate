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
from library.tools.project_layout import Area, ProjectLayout
from library.tools.render_qa import run_full_render_qa
from library.tools.spine_contract import declared_black_beat_ranges
from library.tools.subtitle_qa import verify_subtitle_timing


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
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
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
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False



def _declared_black_beats(assembly_manifest: dict) -> list:
    """Black beats the plan declared, as timeline ranges.

    `compile_manifest` already accepted these holes on the strength of the
    declaration; the render carries the same ruling into the final gate,
    so a beat that survived compilation is not failed here after a full
    render. Black nobody declared still fails.
    """
    return declared_black_beat_ranges(assembly_manifest.get("_spine_blocks") or [])


def _framing_spans(assembly_manifest: dict) -> list:
    """What each stretch of the timeline was supposed to look like.

    One `render_qa.FramingSpan` per picture clip, so every sampled frame
    of the master can be judged against the framing in force over it
    rather than against a flat set of declarations for the whole video.

    The intent read is `framing_delivered`, NOT `framing_intent`. The
    first is what the clip's geometry produces and the second is what was
    asked for, and they part company wherever a source already covers the
    delivery frame - a portrait cutaway fills a 9:16 frame however firmly
    the project declared bars, because it has none to give. Judging the
    render against the declaration would fail exactly the videos whose
    framing is right. See `library/tools/framing_intent.py`, "Declared is
    not delivered". A manifest compiled before that key existed carries
    only `framing_intent`, which is the same number on every clip that
    was actually conformed, so it is read as the fallback.

    V1 first and V2 second, because `render_qa._intent_at` lets the later
    span win an overlap and V2 is the track that covers V1.
    """
    from library.tools.render_qa import FramingSpan

    project_settings = assembly_manifest.get("project", {})
    fps = project_settings.get("frame_rate", 30.0)

    spans = []
    for track in ("V1", "V2"):
        for clip in assembly_manifest.get("tracks", {}).get(track, {}).get("clips", []):
            delivered = clip.get("framing_delivered")
            if delivered is None:
                delivered = clip.get("framing_intent")
            if delivered is None:
                continue
            start_frame = clip.get("timeline_in_frame")
            end_frame = clip.get("timeline_out_frame")
            if start_frame is None or end_frame is None or end_frame <= start_frame:
                continue
            spans.append(FramingSpan(float(start_frame) / fps, float(end_frame) / fps,
                                     float(delivered)))
    return spans


# The manifest keys carrying a rendered overlay, and the fps each track
# declares its `source_in_frame` in. Kept in step with
# `step_5_04_compile_manifest.OVERLAY_TRACKS`, which is the enumeration
# of what the manifest may carry: an overlay track this list forgets is
# an overlay whose ink reads as picture, which is the defect
# `measure_frame_occupancy` reopened three times.
OVERLAY_MANIFEST_KEYS = ("subtitle_overlay", "motion_graphics_overlay",
                         "timed_text_overlay")


def _overlay_segments(assembly_manifest: dict):
    """What the render drew OVER the picture, for the occupancy gate.

    P1 measures the letterbox bars, and an overlay drawn over a bar is
    neither dark nor flat, so the bar walk stops at it and the picture
    reads as taller than it is. It used to guess the footprint from the
    safe area, and every guess has been wrong for the next element
    somebody drew. These segments are the overlays themselves - P1 reads
    each one's alpha and masks exactly the pixels it drew.

    Returns None when the manifest names no overlay track AT ALL, because
    that is a manifest that has not been asked the question - a different
    claim from a manifest that carries the tracks and declares them
    empty, which is `[]` and is exact.
    """
    from library.tools.render_qa import OverlaySegment

    if not any(key in assembly_manifest for key in OVERLAY_MANIFEST_KEYS):
        return None

    segments = []
    for key in OVERLAY_MANIFEST_KEYS:
        track = assembly_manifest.get(key) or {}
        # The overlay steps render at the timeline's own rate and record
        # `source_in_frame` in it, so the second of the file that plays
        # at `timeline_start` is that frame over the track's fps.
        fps = float(track.get("fps") or 0.0) or float(
            (assembly_manifest.get("project") or {}).get("frame_rate") or 30.0)
        for seg in track.get("segments") or []:
            path = seg.get("overlay_path")
            start = seg.get("timeline_start")
            end = seg.get("timeline_end")
            if not path or start is None or end is None or end <= start:
                continue
            segments.append(OverlaySegment(
                str(path), float(start), float(end),
                float(seg.get("source_in_frame") or 0.0) / fps))
    return segments


def _music_bed(assembly_manifest: dict):
    """(music file, automation windows, offset) for speech-above-bed.

    The bed is the first A2 clip. The plan is
    `audio_mix.music_automation`, because that is the half that carries a
    LEVEL: `_spine_blocks[*].music_behavior` names the same behaviour in
    the same vocabulary (both now resolve through
    `library/tools/music_behavior.py`) but no dB, and P3 judges the render
    against the plan's own numbers.

    The OFFSET is the third thing P3 needs and used not to be passed at
    all.  Step 2.04 chooses which SECTION of the track plays and the A2
    clip carries that as `source_in`, so timeline second *t* is music
    file second *t + (source_in - timeline_in)*.  P3 was fitting the
    file from 0 against a render built from second 60 - see
    docs/RULE_EVIDENCE.md#the-bed-was-fitted-from-the-wrong-second.

    It did not always name the same behaviour. `_spine_block_entry` used
    to recompute a two-word `full`/`ducked` value from `block_type`, in
    which a declared silence did not exist at all - see
    docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary.
    """
    clips = assembly_manifest.get("tracks", {}).get("A2", {}).get("clips", [])
    music_path = clips[0].get("source_file") if clips else None
    automation = (assembly_manifest.get("audio_mix") or {}).get("music_automation") or []
    if not music_path or not os.path.exists(music_path):
        return None, [], None
    offset = (float(clips[0].get("source_in", 0.0) or 0.0)
              - float(clips[0].get("timeline_in", 0.0) or 0.0))
    return music_path, automation, offset


def validate_output(rendered_output: dict, assembly_manifest: dict,
                    project_folder: str = "") -> dict:
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
    music_path, music_automation, music_offset = _music_bed(assembly_manifest)
    try:
        qa_results = run_full_render_qa(
            video_path, expected_duration,
            declared_black_beats=_declared_black_beats(assembly_manifest),
            # The delivery format the manifest was compiled at. These two
            # were computed above and then never passed, so the gate
            # judged every render against a hardcoded 1080x1920/30fps.
            expected_resolution=expected_resolution,
            expected_fps=expected_fps,
            framing_spans=_framing_spans(assembly_manifest),
            # No chroma floor is passed, deliberately: the value is an
            # open captain decision and P2 reports its number until one
            # exists. See render_qa.CHROMA_PRESENCE_GATES.
            music_path=music_path,
            music_automation=music_automation,
            music_offset_seconds=music_offset,
            spine_blocks=assembly_manifest.get("_spine_blocks") or [],
            # What was drawn over the picture, so P1 masks the pixels the
            # overlays really touched rather than the strips a centred
            # caption was assumed to leave alone.
            overlay_segments=_overlay_segments(assembly_manifest),
        )
    except Exception as e:
        print(f"Error running render_qa: {e}", file=sys.stderr)
        traceback.print_exc()

    # Subtitle QA
    subtitles = assembly_manifest.get("subtitles", [])
    if subtitles:
        try:
            # The spine is what tells the gap check that a stretch with
            # no caption on it is a beat the plan wrote rather than dead
            # caption time. See library/tools/subtitle_qa.py.
            qa_results.extend(verify_subtitle_timing(
                subtitles,
                spine_blocks=assembly_manifest.get("_spine_blocks") or []))
        except Exception as e:
            print(f"Error running subtitle_qa: {e}", file=sys.stderr)
            traceback.print_exc()
            
    # Process QA Results into existing checks format for compatibility
    tech_check = {"pass": True, "issues": []}
    framing_check = {"pass": True, "issues": []}
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
        elif r.metric == "frame_occupancy":
            # P1 gates. The picture filling the delivery frame and keeping
            # one geometry is the largest visible defect project 001
            # shipped, and nothing in this pipeline looked at it.
            if not r.passed:
                framing_check["pass"] = False
                framing_check["issues"].append(r.detail)
        elif r.metric == "face_intact":
            # A face the render still shows must not be cut by the frame
            # edge. It gates through the same check as P1 because it is
            # the same question - what the conform did to the picture -
            # and a reviewer reading "framing" wants both answers there.
            # The verdict on a conform too tight to leave a detectable
            # face at all is carried by manifest_validator's P8, before
            # the render.
            if not r.passed:
                framing_check["pass"] = False
                framing_check["issues"].append(r.detail)
        elif r.metric in ("chroma_presence", "speech_above_bed"):
            # P2 and P3 report and do not gate - their thresholds are open
            # captain decisions. The number is in qa_report either way,
            # which is the point: promoting them is a boolean in render_qa,
            # not a change here.
            pass
        elif r.metric in ["audio_streams", "lufs"]:
            if not r.passed:
                audio_check["pass"] = False
                audio_check["issues"].append(r.detail)
                
    checks["technical"] = tech_check
    checks["framing"] = framing_check
    checks["duration"] = duration_check
    checks["black_frames"] = black_frame_check
    checks["audio_levels"] = audio_check

    # ── Aggregate result ──
    all_passed = all(c.get("pass", False) for c in checks.values())
    critical_passed = all(
        checks.get(k, {}).get("pass", False)
        for k in ["file_exists", "technical", "framing", "black_frames"]
    )

    all_issues = []
    for name, check in checks.items():
        for issue in check.get("issues", []):
            all_issues.append(f"[{name}] {issue}")

    # Write QA report JSON.
    #
    # exports/ by name, not `dirname(video_path)`. The report belongs
    # with the deliverable it judges, and exports/ is one of the two
    # areas two steps legitimately share - 6.01 writes the render, 6.02
    # writes this. See library/tools/project_layout.py.
    if project_folder:
        qa_report_path = str(ProjectLayout(project_folder).write_path(
            Area.EXPORTS, "qa_report.json", step="validate"))
    else:
        qa_report_path = os.path.join(os.path.dirname(video_path),
                                      "qa_report.json")
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

def main():
    input_data = json.loads(sys.stdin.read())

    rendered_output = input_data.get("rendered_output", {})
    assembly_manifest = input_data.get("assembly_manifest", {})

    # Step 6.01 always exports now, so an absent output_path means the
    # export did not happen. Validating the build report instead used to
    # let a run finish "pass" with distribution_ready: false and nobody
    # noticing there was no video.
    if rendered_output.get("output_path"):
        result = validate_output(rendered_output, assembly_manifest,
                                 input_data.get("project_folder", ""))
    else:
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

    json.dump({"deterministic_validation": result}, sys.stdout, indent=2)

if __name__ == "__main__":
    main()
