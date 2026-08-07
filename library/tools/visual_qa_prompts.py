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
    return (
        f"{_base_instruction()}\n\n"
        f"Task: Answer the following quality assurance question based on the visual content.\n"
        f"Question: {question}\n"
        "Check carefully and respond accurately."
    )

def pre_render_sweep_prompt() -> str:
    return (
        f"{_base_instruction()}\n\n"
        "Task: Perform a full pre-render sweep on the visual content.\n"
        "Check for any glaring issues such as offline media (media offline warning), visual glitches, "
        "unintended black frames, or broken compositions."
    )
