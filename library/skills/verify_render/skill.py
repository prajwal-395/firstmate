"""verify_render: the deterministic gate over a rendered file.

Wraps the file-only half of `library/tools/render_qa.py` - the checks
that read the master ffmpeg produced and need no Resolve, no manifest
and no model. A skill entry point, so a step (or the pipeline on its
behalf) can invoke it by import or by shell:

    python3 -m library.skills.verify_render.skill \
        --video /path/to/master.mp4 --project-folder /path/to/project \
        --step-id validate [--expected-duration 63.2]
        [--expected-resolution 1080 1920] [--expected-fps 30]

Every invocation writes a RECEIPT to
`<project>/pipeline_output/skill_runs/<step_id>/verify_render.json`
carrying the per-check verdicts. The receipt is what the must-check
rule reads back: a model asserting it checked writes no receipt, so a
self-reported check fails the gate the way a gate that cannot fail
should. See `library/tools/pipeline_skills.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List, Optional

SKILL_NAME = "verify_render"


def run(video_path: str,
        project_folder: str,
        step_id: str,
        expected_duration: Optional[float] = None,
        expected_resolution: Optional[List[int]] = None,
        expected_fps: Optional[float] = None,
        declared_black_beats: Optional[List] = None) -> Dict[str, Any]:
    """Run the deterministic render checks and record the receipt.

    Returns a verdict dict with `passed`, per-check `checks`, and the
    `receipt` path. `passed` is False when any check fails - this skill
    GATES. Raises FileNotFoundError when the video is absent (an absent
    render is not a passing one) and records the refusal in the receipt.
    """
    from library.tools import pipeline_skills, render_qa

    import os
    if not video_path or not os.path.exists(video_path):
        verdict: Dict[str, Any] = {
            "skill": SKILL_NAME,
            "passed": False,
            "video_path": video_path,
            "checks": [],
            "issues": [f"render file not found: {video_path}"],
        }
        verdict["receipt"] = pipeline_skills.write_receipt(
            project_folder, step_id, SKILL_NAME, verdict)
        return verdict

    results = []
    try:
        results.append(render_qa.measure_lufs(video_path))
        results.append(render_qa.detect_black_frames(
            video_path, declared_beats=declared_black_beats))
        results.append(render_qa.detect_freeze_frames(video_path))
        results.append(render_qa.measure_silence_under_picture(video_path))
        width, height = (expected_resolution or [1080, 1920])[:2]
        results.append(render_qa.verify_resolution(
            video_path, expected_width=width, expected_height=height))
        if expected_fps is None:
            results.append(render_qa.verify_framerate(video_path))
        else:
            results.append(render_qa.verify_framerate(
                video_path, expected_fps=expected_fps))
        if expected_duration is not None:
            results.append(render_qa.verify_duration(
                video_path, expected_duration))
        results.append(render_qa.verify_audio_streams(video_path))
    except FileNotFoundError:
        verdict: Dict[str, Any] = {
            "skill": SKILL_NAME,
            "passed": False,
            "video_path": video_path,
            "checks": [],
            "issues": [f"render file not found: {video_path}"],
        }
        verdict["receipt"] = pipeline_skills.write_receipt(
            project_folder, step_id, SKILL_NAME, verdict)
        return verdict

    checks = [
        {"name": r.metric, "passed": r.passed, "detail": r.detail}
        for r in results
    ]
    issues = [f"{r.metric}: {r.detail}" for r in results if not r.passed]
    verdict = {
        "skill": SKILL_NAME,
        "passed": all(r.passed for r in results),
        "video_path": video_path,
        "checks": checks,
        "issues": issues,
    }
    verdict["receipt"] = pipeline_skills.write_receipt(
        project_folder, step_id, SKILL_NAME, verdict)
    return verdict


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic render gate (skill: verify_render).")
    parser.add_argument("--video", required=True)
    parser.add_argument("--project-folder", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--expected-duration", type=float, default=None)
    parser.add_argument("--expected-resolution", type=int, nargs=2,
                        default=None, metavar=("W", "H"))
    parser.add_argument("--expected-fps", type=float, default=None)
    args = parser.parse_args(argv)

    verdict = run(args.video, args.project_folder, args.step_id,
                  expected_duration=args.expected_duration,
                  expected_resolution=args.expected_resolution,
                  expected_fps=args.expected_fps)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
