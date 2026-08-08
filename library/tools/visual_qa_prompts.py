import json
from typing import Dict, Any

QA_RESPONSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "confidence": {"type": "number"},
        "detail": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {"type": "string"}
        }
    },
    "required": ["passed", "confidence", "detail", "issues"]
}

def _base_instruction() -> str:
    return (
        "Analyze the provided visual content and determine if it meets the criteria. "
        "You must respond with a JSON object exactly matching this schema:\n"
        '{"passed": bool, "confidence": float, "detail": string, "issues": [string]}\n'
        "Set 'passed' to true only if the visual content meets all expectations. "
        "If not, set 'passed' to false and list the reasons in 'issues'."
    )

def color_grade_prompt(clip_name: str, intended_look: str) -> str:
    return (
        f"{_base_instruction()}\n\n"
        f"Task: Evaluate the color grade for clip '{clip_name}'.\n"
        f"Expected Look: {intended_look}\n"
        "Check if the image correctly reflects this intended look in terms of color balance, contrast, and mood."
    )

def transition_prompt(transition_type: str, clip_a: str, clip_b: str) -> str:
    return (
        f"{_base_instruction()}\n\n"
        f"Task: Evaluate the transition between '{clip_a}' and '{clip_b}'.\n"
        f"Expected Transition Type: {transition_type}\n"
        "Check if the transition is visually smooth, correctly rendered, and fits the specified type."
    )

def subtitle_prompt(expected_text: str, position: str = "bottom-center") -> str:
    return (
        f"{_base_instruction()}\n\n"
        f"Task: Evaluate the subtitle overlay.\n"
        f"Expected Text: \"{expected_text}\"\n"
        f"Expected Position: {position}\n"
        "Check if the text is present, legible, matches the expected text exactly, and is located at the expected position."
    )

def vfx_prompt(effect_type: str, expected_behavior: str) -> str:
    return (
        f"{_base_instruction()}\n\n"
        f"Task: Evaluate the visual effect.\n"
        f"Effect Type: {effect_type}\n"
        f"Expected Behavior: {expected_behavior}\n"
        "Check if the effect is applied correctly, looks professional, and behaves as expected without visual artifacts."
    )

def clip_placement_prompt(expected_clip: str, track: str = "V1") -> str:
    return (
        f"{_base_instruction()}\n\n"
        f"Task: Evaluate the clip placement.\n"
        f"Expected Clip: {expected_clip}\n"
        f"Track: {track}\n"
        "Check if the visual content matches the expected clip and verify its framing and presence on the specified track."
    )

def general_qa_prompt(question: str) -> str:
    """Open-ended visual QA prompt."""
    return (
        f"{_base_instruction()}\n\n"
        f"Task: Answer the following quality assurance question based on the visual content.\n"
        f"Question: {question}\n"
        "Check carefully and respond accurately."
    )


# --- Frame Grab Path (Orchestrating LLM with inline vision) ---

_CHECK_TYPE_INSTRUCTIONS = {
    "color_grade": (
        "Focus on color grading: Does the overall color palette, contrast, "
        "and saturation match the intended look? Check for color casts, "
        "crushed blacks, or blown highlights."
    ),
    "transition": (
        "Focus on the transition effect: Is the transition visually smooth? "
        "Are there artifacts, hard cuts where there shouldn't be, or "
        "unnatural blending? Check for flash brightness, zoom artifacts, "
        "or incomplete dissolves."
    ),
    "subtitle": (
        "Focus on subtitle readability: Is the text clearly visible against "
        "the background? Is it positioned within safe zones? Check for "
        "overlapping elements, truncated text, or poor contrast."
    ),
    "vfx": (
        "Focus on visual effects: Is the effect (zoom, glow, vignette, etc.) "
        "rendering correctly? Check for artifacts, excessive intensity, "
        "edge tiling, or missing effects."
    ),
    "clip_placement": (
        "Focus on clip placement: Does the visual content match what is "
        "expected at this point in the timeline? Is the correct footage "
        "showing, or is there a mismatch?"
    ),
    "general": (
        "Perform a general visual quality check: Look for any issues "
        "including black frames, frozen frames, visual artifacts, incorrect "
        "framing, poor exposure, or anything that looks wrong."
    ),
}


def frame_grab_inline_prompt(check_type: str, context: Dict[str, Any]) -> str:
    """Build a prompt for the orchestrating LLM to analyze a grabbed frame.

    This prompt accompanies a base64 frame image sent to the LLM via its
    vision capability. The LLM should return a structured QA verdict.

    Args:
        check_type: One of the _CHECK_TYPE_INSTRUCTIONS keys.
        context: Dict with optional keys like 'clip_name', 'timecode',
                 'intended_look', 'effect_type', 'expected_text', etc.
    """
    instruction = _CHECK_TYPE_INSTRUCTIONS.get(check_type, _CHECK_TYPE_INSTRUCTIONS["general"])

    context_lines = ["Timeline Context:"]
    if context.get("clip_name"):
        context_lines.append(f"  Clip: {context['clip_name']}")
    if context.get("timecode"):
        context_lines.append(f"  Timecode: {context['timecode']}")
    if context.get("intended_look"):
        context_lines.append(f"  Intended Look: {context['intended_look']}")
    if context.get("effect_type"):
        context_lines.append(f"  Effect Type: {context['effect_type']}")
    if context.get("expected_text"):
        context_lines.append(f"  Expected Text: {context['expected_text']}")
    if context.get("expected_behavior"):
        context_lines.append(f"  Expected Behavior: {context['expected_behavior']}")
    if context.get("transition_type"):
        context_lines.append(f"  Transition: {context['transition_type']}")
    if context.get("notes"):
        context_lines.append(f"  Notes: {context['notes']}")

    context_block = "\n".join(context_lines)

    return f"""You are performing inline visual QA on a grabbed timeline frame.

{instruction}

{context_block}

Respond ONLY with a valid JSON object matching this exact schema:
{json.dumps(QA_RESPONSE_SCHEMA, indent=2)}
"""


def segment_analysis_prompt(check_type: str, context: Dict[str, Any]) -> str:
    """Build a prompt for Gemma 4 12B to analyze a rendered video segment.

    This prompt accompanies extracted video frames sent to the local
    vision model. It should produce a structured QA verdict covering
    temporal/motion aspects that single frames cannot capture.

    Args:
        check_type: One of the _CHECK_TYPE_INSTRUCTIONS keys.
        context: Dict with optional keys for timeline context.
    """
    instruction = _CHECK_TYPE_INSTRUCTIONS.get(check_type, _CHECK_TYPE_INSTRUCTIONS["general"])

    context_lines = ["Segment Context:"]
    if context.get("clip_name"):
        context_lines.append(f"  Clip: {context['clip_name']}")
    if context.get("mark_in"):
        context_lines.append(f"  Mark In (frame): {context['mark_in']}")
    if context.get("mark_out"):
        context_lines.append(f"  Mark Out (frame): {context['mark_out']}")
    if context.get("duration_seconds"):
        context_lines.append(f"  Duration: {context['duration_seconds']:.2f}s")
    if context.get("intended_look"):
        context_lines.append(f"  Intended Look: {context['intended_look']}")
    if context.get("effect_type"):
        context_lines.append(f"  Effect Type: {context['effect_type']}")
    if context.get("transition_type"):
        context_lines.append(f"  Transition: {context['transition_type']}")
    if context.get("notes"):
        context_lines.append(f"  Notes: {context['notes']}")

    context_block = "\n".join(context_lines)

    return f"""You are analyzing a rendered video segment for quality assurance.
You are seeing multiple frames sampled from this segment.

{instruction}

Pay special attention to temporal consistency across frames:
- Are there jarring jumps between frames?
- Does motion appear smooth or stuttery?
- Do effects animate correctly across the segment?
- Are there any frames that look broken, black, or frozen?

{context_block}

Respond ONLY with a valid JSON object matching this exact schema:
{json.dumps(QA_RESPONSE_SCHEMA, indent=2)}
"""


def adjustment_suggestion_prompt(check_type: str, issues: list,
                                 attempt: int, max_retries: int) -> str:
    """Format QA failures into an actionable prompt for the orchestrating LLM.

    This prompt is fed back to the LLM after a QA check fails, telling it
    what went wrong and asking it to suggest specific adjustments.

    Args:
        check_type: What was being checked.
        issues: List of issue strings from the QA verdict.
        attempt: Current retry attempt number (1-indexed).
        max_retries: Maximum retries allowed.
    """
    issues_block = "\n".join(f"  - {issue}" for issue in issues)

    return f"""Visual QA check FAILED (attempt {attempt}/{max_retries}).

Check type: {check_type}
Issues found:
{issues_block}

Based on these issues, suggest specific adjustments to fix the problems.
Consider:
- For color_grade issues: CDL slope/offset/power adjustments, LUT changes
- For transition issues: timing, intensity, easing curve changes
- For vfx issues: effect parameter adjustments (blur, zoom, glow levels)
- For subtitle issues: position, font size, background opacity changes
- For clip_placement issues: source clip or trim point corrections

Respond with a JSON object:
{{
  "adjustments": [
    {{
      "target": "string - what to adjust (e.g. 'clip_3_color', 'transition_2_zoom')",
      "action": "string - what to do (e.g. 'reduce_brightness', 'increase_duration')",
      "parameter": "string - specific parameter name if applicable",
      "current_value": "any - current value if known",
      "suggested_value": "any - suggested new value"
    }}
  ],
  "reasoning": "string - explanation of why these adjustments should fix the issues"
}}
"""

def pre_render_sweep_prompt() -> str:
    return (
        f"{_base_instruction()}\n\n"
        "Task: Perform a full pre-render sweep on the visual content.\n"
        "Check for any glaring issues such as offline media (media offline warning), visual glitches, "
        "unintended black frames, or broken compositions."
    )
