"""End-to-end demonstration tasks for evidence-to-edit (EDIT-026..033).

The ten demonstration tasks from punchlist 14.3, each with its required
capabilities, its retrieval-vs-editing split metrics (EDIT-027), its
intent check (EDIT-028), and its failure attribution (EDIT-032) feeding
model prioritization (EDIT-033). Gold examples (EDIT-026) and agent
scoring (EDIT-031, agent-scored per the standing captain rule - never
human labels) live in the benchmark report this harness writes, not in
this module: this module defines the tasks and the scoring, the
calibration run supplies the footage-bound numbers.

Briefs built here match the `editorial_task_benchmark` contract
(intent, expected_source_moments, cut_ranges, coverage_requirements,
continuity_constraints, preference_rubric on [1,5]) so demo results
stay comparable with EVAL-04.
"""

from __future__ import annotations

from typing import Any
from collections.abc import Mapping, Sequence

# The ten demonstration tasks (punchlist 14.3): id, intent, and the
# capabilities the task requires. The capability names are the
# evidence/query kinds the pipeline already produces.
DEMO_TASKS: tuple[dict[str, Any], ...] = (
    {"id": "coherent_answer",
     "intent": "Find and insert a coherent answer from a podcast",
     "required_capabilities": ("speech", "diarization",
                               "semantic_retrieval", "edit_planning")},
    {"id": "product_pickup",
     "intent": "Find a product being picked up",
     "required_capabilities": ("detection", "tracking",
                               "temporal_grounding", "relationships")},
    {"id": "stable_broll",
     "intent": "Find a stable cinematic B-roll candidate",
     "required_capabilities": ("motion", "camera_measurements",
                               "visual_retrieval", "editorial_policy")},
    {"id": "visible_speaker",
     "intent": "Select the correct visible speaker",
     "required_capabilities": ("diarization", "active_speaker",
                               "identity")},
    {"id": "chart_metric",
     "intent": "Find a chart discussing a specific metric",
     "required_capabilities": ("ocr", "speech_retrieval",
                               "temporal_joins")},
    {"id": "environment_sound",
     "intent": "Find a particular environmental sound",
     "required_capabilities": ("audio_event_grounding",
                               "precise_timing")},
    {"id": "remove_distractor",
     "intent": "Remove a distracting sound",
     "required_capabilities": ("source_separation",
                               "output_verification")},
    {"id": "better_take",
     "intent": "Choose a better take",
     "required_capabilities": ("speech_comparison",
                               "technical_measurements",
                               "editorial_preferences")},
    {"id": "track_for_effect",
     "intent": "Track an object for an effect",
     "required_capabilities": ("grounding", "segmentation", "tracking",
                               "edit_integration")},
    {"id": "reaction_shot",
     "intent": "Locate a reaction shot",
     "required_capabilities": ("person_identity", "expressions",
                               "speech_context", "temporal_relations")},
)

_TASK_IDS = frozenset(task["id"] for task in DEMO_TASKS)


def task_definition(task_id: str) -> dict[str, Any]:
    """The definition of one demo task; KeyError on unknown ids."""
    for task in DEMO_TASKS:
        if task["id"] == task_id:
            return {"id": task["id"], "intent": task["intent"],
                    "required_capabilities":
                        tuple(task["required_capabilities"])}
    raise KeyError(f"unknown demo task: {task_id!r}")


def build_demo_brief(task_id: str,
                     moments: Sequence[Mapping[str, Any]],
                     preference_rubric: Mapping[str, Any] | None = None
                     ) -> dict[str, Any]:
    """A benchmark-contract brief for one demo task (EDIT-026 input).

    `moments` carry `moment_id`, `source_id`, `start_seconds`,
    `end_seconds`, `description` and `evidence_ids`; each becomes an
    expected source moment with a cut range citing it as its reason.
    Raises KeyError on an unknown task, ValueError on an empty moment
    list: a demo with nothing to find demonstrates nothing.
    """
    definition = task_definition(task_id)
    moment_list = list(moments)
    if not moment_list:
        raise ValueError(f"demo task {task_id}: no moments supplied")
    expected = []
    cut_ranges = []
    for moment in moment_list:
        expected.append({
            "moment_id": str(moment["moment_id"]),
            "source_id": str(moment["source_id"]),
            "start_seconds": float(moment["start_seconds"]),
            "end_seconds": float(moment["end_seconds"]),
            "description": str(moment.get("description", "")),
            "evidence_ids": [str(evidence_id)
                             for evidence_id in
                             moment.get("evidence_ids", [])]})
        cut_ranges.append({
            "moment_id": str(moment["moment_id"]),
            "start_seconds": float(moment["start_seconds"]),
            "end_seconds": float(moment["end_seconds"]),
            "reason": f"demo {task_id}: cover expected moment "
                      f"{moment['moment_id']}"})
    rubric = (dict(preference_rubric) if preference_rubric is not None
              else {"scale": [1, 5],
                    "criterion": "the selected media satisfies the intent"})
    return {"task_id": task_id, "intent": definition["intent"],
            "brief": {
                "intent": definition["intent"],
                "expected_source_moments": expected,
                "cut_ranges": cut_ranges,
                "coverage_requirements": [
                    moment["moment_id"] for moment in expected],
                "continuity_constraints": [],
                "preference_rubric": rubric}}


def score_retrieval_vs_edit(
        retrieved_moment_ids: Sequence[str],
        edited_moment_ids: Sequence[str],
        expected_moment_ids: Sequence[str]) -> dict[str, Any]:
    """Retrieval success separately from editing success (EDIT-027).

    Retrieval recall: fraction of expected moments retrieved.
    Editing recall: fraction of expected moments covered by the edit.
    Edit precision: fraction of edited moments that were expected.
    The three numbers separate "found it" from "cut it" from "cut only
    it", so a retrieval win with an editing loss reads as exactly that.
    """
    expected = set(expected_moment_ids)
    retrieved = set(retrieved_moment_ids) & expected
    edited = set(edited_moment_ids) & expected
    precision_base = set(edited_moment_ids)
    return {
        "retrieval_recall": len(retrieved) / len(expected) if expected else 0.0,
        "editing_recall": len(edited) / len(expected) if expected else 0.0,
        "edit_precision": (len(edited) / len(precision_base)
                           if precision_base else 0.0),
    }


def attribute_failure(task_id: str,
                      retrieval: Mapping[str, Any],
                      candidate: Mapping[str, Any] | None,
                      missing_lanes: Sequence[str] = ()) -> dict[str, Any]:
    """Attribute an editing failure to perception or decision (EDIT-032).

    Perception failure: the retrieval half missed (recall < 1), a
    required capability's lanes never ran, or the candidate abstained
    for missing evidence. Decision failure: the evidence was present
    and the candidate still missed (uncertain grade, veto, or uncovered
    moment). Returns `{task_id, failure_class, causes[]}` where the
    class is `perception`, `decision`, or `none`.
    """
    task_definition(task_id)
    causes: list[str] = []
    if retrieval.get("retrieval_recall", 0.0) < 1.0:
        causes.append("retrieval missed an expected moment")
    for lane in missing_lanes:
        causes.append(f"required evidence lane never ran: {lane}")
    if candidate is None:
        causes.append("no candidate built (abstained upstream)")
    elif candidate.get("strength") == "uncertain":
        for reason in candidate.get("strength_reasons", []):
            causes.append(f"candidate uncertain: {reason}")
    if not causes:
        return {"task_id": task_id, "failure_class": "none", "causes": []}
    perception_markers = ("retrieval missed", "never ran", "abstained",
                          "predicate failed", "no supporting evidence")
    failure_class = ("perception" if any(
        any(marker in cause for marker in perception_markers)
        for cause in causes) else "decision")
    return {"task_id": task_id, "failure_class": failure_class,
            "causes": causes}


def feedback_record(attribution: Mapping[str, Any],
                    task_id: str) -> dict[str, Any]:
    """Feed a failure back into model prioritization (EDIT-033).

    Carries the failing task, its failure class, and the named causes so
    a model lane can claim the cause it owns. A `none` attribution
    yields no record: there is nothing to prioritize.
    """
    if attribution.get("failure_class") == "none":
        return {"task_id": task_id, "prioritize": []}
    return {"task_id": task_id,
            "failure_class": attribution.get("failure_class"),
            "prioritize": list(attribution.get("causes", []))}
