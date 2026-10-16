"""Policy-controlled escalation between lightweight and strong VLM tiers.

SEM-013. The task tiers come from the SEM-001..004 audit
(docs/benchmarks/2026-10-15-vlm-task-audit.md); the tier defaults reflect
the measured 2026-10-16 calibration scorecard
(docs/benchmarks/2026-10-16-vlm-candidate-verdicts.md).

This module is POLICY ONLY: it selects model ids, it never loads a model
and never changes what `VisionModel` uses today. The incumbent stays the
default in every path unless the caller explicitly opts into escalation.
The harness capability adapter (vep-mu-harness) consumes this policy when
its contract lands; until then nothing routes through it.
"""

from __future__ import annotations

INCUMBENT_MODEL_ID = "mlx-community/gemma-4-12b-it-4bit"

# Conditional fallback qualified on calibration (SEM-005 verdict); stays
# behind the explicit opt-in until the license lane clears the conversion
# artifact and held-out confirms.
FALLBACK_MODEL_ID = "mlx-community/Qwen3.5-4B-4bit"

# Tier membership is by task class, not by model ranking. A lightweight
# first pass must never suppress the incumbent for temporal/relational
# tasks (SEM-002 audit).
SIMPLE_TASK_CLASSES = frozenset({
    "object_presence",
    "scene_classification",
    "static_object_description",
})

COMPLEX_TASK_CLASSES = frozenset({
    "action_localization",
    "person_object_interaction",
    "camera_motion",
    "audiovisual_grounding",
    "multi_window_reasoning",
})

KNOWN_TASK_CLASSES = SIMPLE_TASK_CLASSES | COMPLEX_TASK_CLASSES


def select_tier(task_class: str) -> str:
    """Return 'lightweight' or 'strong' for a known task class.

    Unknown task classes resolve to 'strong': the incumbent answers rather
    than a lightweight model guessing outside its qualified scope.
    """
    if task_class in SIMPLE_TASK_CLASSES:
        return "lightweight"
    return "strong"


def should_escalate(observation: dict) -> tuple[bool, str]:
    """Decide from OBSERVABLE output signals whether a lightweight answer
    must be re-done by the strong tier.

    `observation` carries only what the caller measured about the
    lightweight answer: status, parse flags (`json`, `schema`, `timestamps`),
    whether the claim lists came back empty, and whether the scene read
    `unknown`. Returns
    (escalate, reason); reason is always a measured signal, never taste.
    """
    if observation.get("status", "completed") != "completed":
        return True, f"lightweight inference status is {observation['status']}"
    parse = observation.get("parse", {})
    if not parse.get("json", False):
        return True, "lightweight output is not JSON"
    if not parse.get("schema", False):
        return True, "lightweight output fails the response schema"
    if not parse.get("timestamps", True):
        return True, "lightweight output has invalid source-clock timestamps"
    if not observation.get("objects") and not observation.get("actions"):
        return True, "lightweight answer carries no claims"
    if observation.get("scene_class") == "unknown":
        return True, "lightweight answer abstained on scene"
    return False, "lightweight answer is well-formed and non-empty"


def resolve_model_id(tier: str, escalation_enabled: bool = False) -> str:
    """Map a tier to a model id. The incumbent is returned unless the
    caller explicitly opts into the lightweight candidate; there is no path
    here that changes the default.
    """
    if tier == "lightweight" and escalation_enabled:
        return FALLBACK_MODEL_ID
    return INCUMBENT_MODEL_ID
