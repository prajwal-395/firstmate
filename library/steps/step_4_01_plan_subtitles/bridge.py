#!/usr/bin/env python3
"""4.01 bridge: plan captions deterministically, then show only note context."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.steps.step_4_01_plan_subtitles.step import (
    DEFAULT_CAPTION_CASE,
    DEFAULT_CAPTION_FPS,
    generate_subtitles,
)
from library.tools import caption_feedback, nothing_to_decide


def build_context(data: dict) -> dict:
    audio_spine = data["audio_spine"]
    brand_effect = data.get("brand_effect") or {}
    plan = generate_subtitles(
        audio_spine,
        caption_case=brand_effect.get("caption_case", DEFAULT_CAPTION_CASE),
        brand_effect=brand_effect,
        brand_style=data.get("brand_style") or {},
        project_folder=data.get("project_folder", ""),
        fps=data.get("project_fps") or DEFAULT_CAPTION_FPS,
    )["subtitle_plan"]
    context = caption_feedback.build_context(
        data.get("timeline_notes") or {}, plan, audio_spine)
    output = {
        "subtitle_plan": plan,
        "caption_feedback_context": context,
    }
    if not context["notes"]:
        output.update(nothing_to_decide.declare(
            "no timeline notes reached subtitle planning, so there is no "
            "caption wording to check"))
    return output


def main() -> None:
    try:
        print(json.dumps(build_context(json.loads(sys.stdin.read()))))
    except Exception:
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
