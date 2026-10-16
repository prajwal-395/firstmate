import pytest

from library.tools import edit_demo_tasks as edt


def _moments():
    return [
        {"moment_id": "m1", "source_id": "LCATL0012",
         "start_seconds": 102.0, "end_seconds": 116.0,
         "description": "complete answer about pricing",
         "evidence_ids": ["w1", "w2"]},
    ]


def test_ten_demo_tasks_defined():
    assert len(edt.DEMO_TASKS) == 10
    assert {task["id"] for task in edt.DEMO_TASKS} == {
        "coherent_answer", "product_pickup", "stable_broll",
        "visible_speaker", "chart_metric", "environment_sound",
        "remove_distractor", "better_take", "track_for_effect",
        "reaction_shot",
    }
    for task in edt.DEMO_TASKS:
        assert task["intent"] and task["required_capabilities"]


def test_unknown_task_fails_loudly():
    with pytest.raises(KeyError):
        edt.task_definition("cinematic_montage")


def test_brief_matches_benchmark_contract():
    brief = edt.build_demo_brief("coherent_answer", _moments())
    inner = brief["brief"]
    assert inner["intent"]
    assert len(inner["expected_source_moments"]) == 1
    assert len(inner["cut_ranges"]) == 1
    assert inner["coverage_requirements"] == ["m1"]
    assert inner["preference_rubric"]["scale"] == [1, 5]


def test_brief_refuses_empty_moments():
    with pytest.raises(ValueError):
        edt.build_demo_brief("coherent_answer", [])


def test_retrieval_and_edit_scored_separately():
    scores = edt.score_retrieval_vs_edit(
        retrieved_moment_ids=["m1", "extra"],
        edited_moment_ids=["m1"],
        expected_moment_ids=["m1", "m2"])
    assert scores["retrieval_recall"] == 0.5
    assert scores["editing_recall"] == 0.5
    assert scores["edit_precision"] == 1.0


def test_perception_failure_attribution():
    attribution = edt.attribute_failure(
        "coherent_answer",
        {"retrieval_recall": 0.0},
        None,
        missing_lanes=["speech.word"])
    assert attribution["failure_class"] == "perception"
    assert any("never ran" in cause
               for cause in attribution["causes"])
    record = edt.feedback_record(attribution, "coherent_answer")
    assert record["prioritize"]


def test_decision_failure_attribution():
    candidate = {"strength": "uncertain",
                 "strength_reasons": ["vetoed: mid-word"]}
    attribution = edt.attribute_failure(
        "coherent_answer", {"retrieval_recall": 1.0}, candidate)
    assert attribution["failure_class"] == "decision"


def test_success_needs_no_prioritization():
    candidate = {"strength": "strong", "strength_reasons": []}
    attribution = edt.attribute_failure(
        "coherent_answer", {"retrieval_recall": 1.0}, candidate)
    assert attribution["failure_class"] == "none"
    assert edt.feedback_record(
        attribution, "coherent_answer") == {"task_id": "coherent_answer",
                                            "prioritize": []}
