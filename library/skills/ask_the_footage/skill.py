"""ask_the_footage: eyes on the picture, on request.

The captain's "feeding video segments to Gemma 4 12B for Q&A, or
capturing stills to ingest natively": a still (or short strip of
stills) is captured from the file with ffmpeg, deterministic ffmpeg
checks run over it, and the local Gemma 4 12B model is asked the
question - its answer recorded as an OPINION, never enforced as a
verdict. The deterministic half carries the verdict; the model's half
is reported. That split is AGENTS.md 10.4: a model-judged gate gets a
deterministic half that can carry the verdict, and the model's opinion
is recorded rather than enforced.

On request only - never on every build. The model load is gigabytes
and seconds; a render must not silently pay it unasked.

Shell invocation:

    python3 -m library.skills.ask_the_footage.skill \
        --video /path/to/segment.mp4 --question "Is the caption readable?" \
        --project-folder /path/to/project --step-id review_rough_cut \
        [--timestamp 12.5] [--check-type subtitle]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

SKILL_NAME = "ask_the_footage"


def capture_still(video_path: str, timestamp: float,
                  output_path: str) -> bool:
    """Capture one still from a file with ffmpeg. No Resolve needed.

    True only when ffmpeg succeeded AND a non-empty file is on disk:
    a zero-byte file is a capture that did not happen, and handing it
    to the model (or counting it as a still) repeats the 2026-09-10
    false finding - see `marker_capture`'s "WHEN THE ROUTE FAILS".
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}",
             "-i", video_path, "-frames:v", "1",
             "-f", "image2", output_path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, check=False,
        )
        return (result.returncode == 0
                and os.path.exists(output_path)
                and os.path.getsize(output_path) > 0)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


def ask_vision(still_paths: List[str], question: str,
               check_type: str,
               harness: Optional[str] = None,
               project_folder: Optional[str] = None,
               step_id: Optional[str] = None) -> Dict[str, Any]:
    """Ask whoever looks at stills: the driver first, gemma fallback.

    Routes through `library/tools/still_vision.py` (the captain's
    2026-09-24 ruling): a driving host with vision answers from its
    own eyes via the file handshake; with no host - or a blind one -
    the local Gemma 4 12B model answers, exactly as before.

    Reports model availability honestly, never raises for a missing
    model: an unavailable model is `available: False` with the
    reason, and the deterministic half still answers. A skill that
    refused without the model would be a gate that fails correct
    output whenever the model is unloaded. Host-handshake failures
    (refusals, timeouts) DO propagate: a host that mis-answers is a
    wiring problem, not an unavailable model, and failing loudly is
    what keeps the fallback honest.
    """
    from library.tools.visual_qa_prompts import frame_grab_inline_prompt
    from library.tools.visual_qa_router import parse_qa_response
    from library.tools.still_vision import inspect_stills

    prompt = frame_grab_inline_prompt(
        check_type, {"question": question})
    t0 = time.time()
    try:
        raw = inspect_stills(
            f"{prompt}\nQuestion: {question}", list(still_paths),
            harness=harness, project_folder=project_folder,
            step_id=step_id or "ask_the_footage",
            label=f"ask_the_footage:{check_type}", max_tokens=512)
    except Exception as e:  # noqa: BLE001 - availability, not a failure
        from library.tools.still_vision import StillVisionCallError
        from library.tools.llm_handshake import HandshakeRefusal
        if isinstance(e, (StillVisionCallError, HandshakeRefusal)):
            raise
        return {"available": False,
                "reason": f"{type(e).__name__}: {e}",
                "elapsed_seconds": round(time.time() - t0, 2)}
    verdict = parse_qa_response(raw)
    return {"available": True,
            "opinion_passed": verdict.get("passed", False),
            "confidence": verdict.get("confidence", 0.0),
            "detail": verdict.get("detail", raw[:500]),
            "issues": verdict.get("issues", []),
            "elapsed_seconds": round(time.time() - t0, 2)}


def run(video_path: str, question: str,
        project_folder: str, step_id: str,
        timestamp: Optional[float] = None,
        check_type: str = "general",
        sample_count: int = 3,
        harness: Optional[str] = None) -> Dict[str, Any]:
    """Capture stills, run deterministic checks, ask Gemma, record all of it.

    Returns an OBSERVATION, not a verdict: `deterministic_passed` is the
    half that can fail (and does, on really broken footage), while
    `vision` is the model's recorded opinion. Nothing here gates a
    build - a caller that wants a gate uses `verify_render`.
    """
    from library.tools import pipeline_skills, render_qa

    t_start = time.time()
    if not os.path.exists(video_path):
        observation: Dict[str, Any] = {
            "skill": SKILL_NAME,
            "video_path": video_path,
            "question": question,
            "deterministic_passed": False,
            "deterministic": {},
            "stills": [],
            "vision": {"available": False,
                       "reason": "no file to look at"},
            "issues": [f"file not found: {video_path}"],
            "elapsed_seconds": round(time.time() - t_start, 2),
        }
        observation["receipt"] = pipeline_skills.write_receipt(
            project_folder, step_id, SKILL_NAME, observation)
        return observation

    duration = _probe_duration(video_path)
    moments = _sample_moments(timestamp, duration, sample_count)

    tmpdir = tempfile.mkdtemp(prefix="ask_the_footage_")
    stills: List[str] = []
    for i, moment in enumerate(moments):
        path = os.path.join(tmpdir, f"still_{i:02d}.png")
        if capture_still(video_path, moment, path):
            stills.append(path)

    deterministic: Dict[str, Any] = {}
    for name, check in (
            ("black_frames", render_qa.detect_black_frames(video_path)),
            ("freeze_frames", render_qa.detect_freeze_frames(video_path))):
        deterministic[name] = {"passed": check.passed,
                               "detail": check.detail}
    deterministic_passed = all(
        v["passed"] for v in deterministic.values())
    issues = [f"{k}: {v['detail']}" for k, v in deterministic.items()
              if not v["passed"]]
    if not stills:
        issues.append("no still could be captured from the file")

    vision: Dict[str, Any] = {"available": False,
                              "reason": "no still captured"}
    if stills:
        vision = ask_vision(stills, question, check_type,
                            harness=harness,
                            project_folder=project_folder,
                            step_id=step_id)

    observation = {
        "skill": SKILL_NAME,
        "video_path": video_path,
        "question": question,
        "check_type": check_type,
        "deterministic_passed": deterministic_passed and bool(stills),
        "deterministic": deterministic,
        "stills": stills,
        "vision": vision,
        "issues": issues,
        "elapsed_seconds": round(time.time() - t_start, 2),
    }
    observation["receipt"] = pipeline_skills.write_receipt(
        project_folder, step_id, SKILL_NAME, observation)
    return observation


def _probe_duration(video_path: str) -> Optional[float]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_entries", "format=duration", video_path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=15, check=False,
        )
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001 - unknown length just means t=0
        return None


def _sample_moments(timestamp: Optional[float],
                    duration: Optional[float],
                    sample_count: int) -> List[float]:
    if timestamp is not None:
        return [max(0.0, timestamp)]
    if duration:
        span = max(0.0, duration - 0.5)
        if sample_count <= 1 or span <= 0:
            return [0.0]
        return [round(span * i / (sample_count - 1), 3)
                for i in range(sample_count)]
    return [0.0]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Look at the footage and answer a question "
                    "(skill: ask_the_footage). Reports; never gates.")
    parser.add_argument("--video", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--project-folder", required=True)
    parser.add_argument("--step-id", required=True)
    parser.add_argument("--timestamp", type=float, default=None)
    parser.add_argument("--check-type", default="general")
    parser.add_argument("--harness", default=None,
                        help="Driving harness for the still-vision route "
                             "(agent, mock); unset means no host drives, "
                             "so gemma answers. Also read from "
                             "PIPELINE_HOST_HARNESS when unset here.")
    args = parser.parse_args(argv)

    observation = run(args.video, args.question, args.project_folder,
                      args.step_id, timestamp=args.timestamp,
                      check_type=args.check_type, harness=args.harness)
    print(json.dumps(observation, indent=2))
    # Exit zero: a reported opinion is not a failure, even when the
    # deterministic half found something. The caller reads the dict.
    return 0


if __name__ == "__main__":
    sys.exit(main())
