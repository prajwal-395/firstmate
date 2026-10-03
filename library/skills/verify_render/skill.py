"""verify_render: the deterministic gate over a rendered file.

Wraps the file-only half of `library/tools/render_qa.py` - the checks
that read the master ffmpeg produced and need no Resolve, no manifest
and no model. A skill entry point, so a step (or the pipeline on its
behalf) can invoke it by import or by shell:

    python3 -m library.skills.verify_render.skill \
        --video /path/to/master.mp4 --project-folder /path/to/project \
        --step-id validate [--expected-duration 63.2]
        [--expected-resolution 1080 1920] [--expected-fps 23.976]
        [--dirty-receipt <touch receipt> ...]

`--dirty-receipt` scopes the run to what those touches changed
(`library/tools/dirty_regions.py`): black, freeze and silence re-read only
the dirty spans of their domain, loudness runs only when audio is dirty,
and the stream probes always run. What was not re-checked is NAMED in
`not_rechecked` - it is not passed. A receipt with no dirty block, or a
render older than the touch, falls back to the whole file.

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


def _measured_project_fps(project_folder: str) -> Optional[float]:
    """The frame rate the catalog measured off the footage, or None."""
    import os

    from library.tools import capability_outputs
    from library.tools.project_layout import PIPELINE_DATA_FILE
    path = os.path.join(project_folder or "", PIPELINE_DATA_FILE)
    if not project_folder or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        state = json.load(handle)
    fps = capability_outputs.value(state, "footage.catalog", "project_fps")
    return float(fps) if fps else None


def run(video_path: str,
        project_folder: str,
        step_id: str,
        expected_duration: Optional[float] = None,
        expected_resolution: Optional[List[int]] = None,
        expected_fps: Optional[float] = None,
        declared_black_beats: Optional[List] = None,
        dirty_receipts: Optional[List[str]] = None,
        declared_ending_black_spans: Optional[List] = None,
        declared_silence_spans: Optional[List] = None,
        assembly_manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
    not_rechecked: List[str] = []
    scope = "whole file"
    try:
        if assembly_manifest is not None:
            from library.tools.spine_contract import declared_black_beat_ranges

            ending_spans = render_qa.declared_ending_spans(assembly_manifest)
            if declared_black_beats is None:
                declared_black_beats = declared_black_beat_ranges(
                    assembly_manifest.get("_spine_blocks") or [])
            if declared_ending_black_spans is None:
                declared_ending_black_spans = ending_spans["black"]
            if declared_silence_spans is None:
                declared_silence_spans = ending_spans["silence"]
        from library.tools.dirty_regions import scope_for
        dirty = scope_for(video_path, dirty_receipts)
        if dirty is None or dirty["whole_reel"]:
            if dirty is not None:
                scope = f"whole file: {dirty['whole_reel_reason']}"
            results.append(render_qa.measure_lufs(video_path))
            results.append(render_qa.detect_black_frames(
                video_path, declared_beats=declared_black_beats,
                declared_ending_spans=declared_ending_black_spans))
            results.append(render_qa.detect_freeze_frames(video_path))
            results.append(render_qa.measure_silence_under_picture(
                video_path, declared_spans=declared_silence_spans))
        else:
            from library.tools.dirty_regions import AUDIO, describe
            scope = f"scoped to the touch: {describe(dirty)}"
            if AUDIO in dirty["dirty_domains"]:
                results.append(render_qa.measure_lufs(video_path))
            else:
                not_rechecked.append("lufs")
            scoped, skipped = render_qa.run_scoped_render_qa(
                video_path, dirty, declared_black_beats,
                declared_ending_black_spans, declared_silence_spans)
            results += scoped
            not_rechecked += skipped
        # The frame the render was built at: stated by the caller, else
        # read off the project's own declaration
        # (`library/tools/delivery_format.py` - project override over
        # template over vertical). Never a shape literal here: a
        # `.get`-style fallback to vertical would grade a render
        # against a guessed frame.
        frame = list(expected_resolution) if expected_resolution else None
        if frame is None:
            from library.tools.delivery_format import (
                resolve_delivery_format)
            frame = resolve_delivery_format(project_folder or None)
        width, height = frame[:2]
        results.append(render_qa.verify_resolution(
            video_path, expected_width=width, expected_height=height))
        # The rate, the same way: stated by the caller, else the rate the
        # project's catalog MEASURED off its footage. A hardcoded 30 here
        # failed a correct 23.976 reel (Reel 01, 2026-10-03).
        if expected_fps is None:
            expected_fps = _measured_project_fps(project_folder)
        if expected_fps is None:
            results.append(render_qa.framerate_not_checked(
                "no --expected-fps and no measured project_fps in the "
                "project's catalog"))
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
        "scope": scope,
        "not_rechecked": not_rechecked,
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
    parser.add_argument(
        "--assembly-manifest", default=None,
        help="compiled reel manifest; render QA allowances are derived "
             "from its ending effect and silent end card")
    parser.add_argument("--dirty-receipt", action="append", default=None,
                        help="a touch receipt; re-check only what it "
                             "changed (repeatable)")
    args = parser.parse_args(argv)

    assembly_manifest = None
    if args.assembly_manifest:
        with open(args.assembly_manifest, encoding="utf-8") as handle:
            assembly_manifest = json.load(handle)
        if not isinstance(assembly_manifest, dict):
            parser.error("--assembly-manifest must contain a JSON object")
    verdict = run(args.video, args.project_folder, args.step_id,
                  expected_duration=args.expected_duration,
                  expected_resolution=args.expected_resolution,
                  expected_fps=args.expected_fps,
                  assembly_manifest=assembly_manifest,
                  dirty_receipts=args.dirty_receipt)
    print(json.dumps(verdict, indent=2))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
