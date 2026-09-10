"""verify_treatment: look at what a visual treatment drew.

Wraps the deterministic before/after half of
`library/tools/treatment_verify.py` - the checks that read the comp the
renderer writes and need no Resolve and no model. A skill entry point,
so a step (or the pipeline on its behalf) can invoke it by import or by
shell:

    python3 -m library.skills.verify_treatment.skill \
        --effects '{"tv_power_head": true}' --clip-dur 600 \
        --treatment-key tv_power_head --played-frames 72 \
        --project-folder /path/to/project --step-id plan_vfx

Every invocation writes a RECEIPT to
`<project>/pipeline_output/skill_runs/<step_id>/verify_treatment.json`
carrying the measured verdict. The receipt is what the must-check rule
reads back: a model asserting it checked writes no receipt, so a
self-reported check fails the gate the way a gate that cannot fail
should. See `library/tools/pipeline_skills.py`.

Stills are captured from the source file with ffmpeg (cheap seeks, no
Resolve) at the treatment window's timecodes and handed to the model
where its harness shows pictures (`window_frames.harness_shows_frames`);
where it cannot, a withholding notice stands in their place - a picture
has no smaller textual form. The Gemma pass runs only with
`--ask-vision` (the captain's cost ruling: deterministic checks run on
every build, the vision pass on request), reusing
`ask_the_footage.ask_vision`, and its answer is recorded as an opinion,
never enforced (AGENTS.md 10.4).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

SKILL_NAME = "verify_treatment"


def capture_window_stills(source_file: str, source_fps: float,
                          window: List[int], out_dir: str,
                          ) -> List[Dict[str, Any]]:
    """One still per window third, by fast seek. No Resolve needed."""
    if not source_file or not os.path.exists(source_file):
        return []
    w0, w1 = window[0], window[-1]
    fps = source_fps if source_fps and source_fps > 0 else 30.0
    moments = sorted({w0 / fps, (w0 + w1) / 2 / fps, w1 / fps})
    stills = []
    for i, moment in enumerate(moments):
        path = os.path.join(out_dir, f"treatment_{i:02d}.png")
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{max(0.0, moment):.3f}",
                 "-i", source_file, "-frames:v", "1",
                 "-f", "image2", path],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=60, check=False,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
        if (result.returncode == 0 and os.path.exists(path)
                and os.path.getsize(path) > 0):
            stills.append({"path": path,
                           "timeline_frame": int(round(moment * fps)),
                           "timestamp_seconds": round(moment, 3)})
    return stills


def run(effects: Dict[str, Any], treatment_key: str, clip_dur: int,
        project_folder: str, step_id: str,
        played_frames: Optional[int] = None,
        source_file: Optional[str] = None,
        source_fps: Optional[float] = None,
        harness: str = "agent",
        ask_vision: bool = False,
        question: Optional[str] = None) -> Dict[str, Any]:
    """Check one treatment's before/after and record the receipt.

    Returns a verdict dict with `passed`, the measured `verdict`, the
    `stills` (or a withholding notice), and the `vision` opinion. The
    deterministic half gates; the model's half is recorded, never
    enforced. Raises UnknownTreatment for a key nothing can check -
    an unchecked treatment must not read as a checked one.
    """
    from library.tools import pipeline_skills, treatment_verify
    from library.tools.window_frames import harness_shows_frames

    verdict = treatment_verify.verify_treatment(
        effects, treatment_key, clip_dur, played_frames=played_frames)

    shows = harness_shows_frames(harness)
    tmpdir = tempfile.mkdtemp(prefix="verify_treatment_")
    stills: List[Dict[str, Any]] = []
    withheld: Optional[str] = None
    if source_file:
        for still in capture_window_stills(
                source_file, source_fps or 30.0,
                verdict.get("window") or [0, 0], tmpdir):
            stills.append(still)
    if stills and not shows:
        withheld = (
            f"Frames of the treatment window were drawn for this run "
            f"and are NOT shown here: the {harness!r} harness cannot be "
            f"shown a picture, so this step decides from the measurements "
            f"alone. This line is the record of that, not a description "
            f"of the frames.")
    vision: Dict[str, Any] = {"available": False,
                              "reason": "not requested"}
    if ask_vision and stills and shows:
        from library.skills.ask_the_footage.skill import ask_vision
        vision = ask_vision(
            [s["path"] for s in stills],
            question or (f"Does this treatment ({treatment_key}) leave "
                         f"the subject visible where it claims to?"),
            "vfx")
    elif ask_vision:
        vision = {"available": False,
                  "reason": ("no still captured" if shows
                             else f"harness {harness!r} shows no pictures")}

    issues = []
    if not verdict.get("passed"):
        failure = verdict.get("failure")
        if failure == "outside_window":
            issues.append(
                f"outside_window: frames "
                f"{verdict.get('outside_window')} changed outside the "
                f"declared window {verdict.get('window')}")
        elif failure == "drew_nothing":
            issues.append(
                f"drew_nothing: the key armed nodes but 0 of "
                f"{verdict.get('played_frames')} rendered frames changed "
                f"- the animation is keyed past everything rendered")
        elif failure == "never_settles":
            issues.append(
                f"never_settles: {verdict.get('detail') or 'the animation '
                'is still drawn where it claims to leave neutral'}")
    if verdict.get("horizon") == "assumed_from_source_span":
        issues.append(
            "horizon assumed from the source span: pass --played-frames "
            "with the timeline's real rendered count, or a pool-fps vs "
            "timeline-fps mismatch parks an end-anchored animation past "
            "everything rendered and this check cannot see it")

    record = {
        "skill": SKILL_NAME,
        "treatment": treatment_key,
        "passed": bool(verdict.get("passed")),
        "verdict": verdict,
        "issues": issues,
        "stills": ([{**s, "shown": shows} for s in stills]
                   if shows else []),
        "stills_withheld": withheld,
        "stills_dir": tmpdir if (stills or withheld) else None,
        "vision": vision,
    }
    record["receipt"] = pipeline_skills.write_receipt(
        project_folder, step_id, SKILL_NAME, record)
    return record


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Before/after gate over a visual treatment "
                    "(skill: verify_treatment).")
    parser.add_argument("--effects", required=True,
                        help="JSON object of the clip's effect params")
    parser.add_argument("--treatment-key", required=True)
    parser.add_argument("--clip-dur", type=int, required=True)
    parser.add_argument("--played-frames", type=int, default=None,
                        help="Frames the timeline really renders; without "
                             "it the source span stands in and the "
                             "receipt says so")
    parser.add_argument("--project-folder", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--source-file", default=None)
    parser.add_argument("--source-fps", type=float, default=None)
    parser.add_argument("--harness", default="agent")
    parser.add_argument("--ask-vision", action="store_true",
                        help="Put the window stills to the local model; "
                             "on request only, never on every build")
    parser.add_argument("--question", default=None)
    args = parser.parse_args(argv)

    try:
        effects = json.loads(args.effects)
    except ValueError as exc:
        print(f"verify_treatment REFUSED: --effects is not JSON: {exc}",
              file=sys.stderr)
        return 2
    if not isinstance(effects, dict):
        print("verify_treatment REFUSED: --effects must be a JSON object",
              file=sys.stderr)
        return 2

    try:
        record = run(effects, args.treatment_key, args.clip_dur,
                     args.project_folder, args.step_id,
                     played_frames=args.played_frames,
                     source_file=args.source_file,
                     source_fps=args.source_fps,
                     harness=args.harness,
                     ask_vision=args.ask_vision,
                     question=args.question)
    except Exception as exc:  # noqa: BLE001 - refusal, still receipted
        from library.tools import pipeline_skills

        record = {"skill": SKILL_NAME, "passed": False,
                  "issues": [f"{type(exc).__name__}: {exc}"]}
        record["receipt"] = pipeline_skills.write_receipt(
            args.project_folder, args.step_id, SKILL_NAME, record)
        print(json.dumps(record, indent=2))
        return 1
    print(json.dumps(record, indent=2))
    return 0 if record.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
