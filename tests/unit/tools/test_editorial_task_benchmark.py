from library.tools import editorial_task_benchmark as benchmark


def _run(split: str, output: str) -> dict:
    return {
        "split": split,
        "stages": {stage: "complete" for stage in benchmark.REQUIRED_GRAPH_STAGES},
        "reviewable_output": output,
        "evidence_decisions": [
            {
                "decision": source,
                "rationale": "measured support",
                "evidence_ids": [f"evidence:{source}"],
                "evidence_source": source,
            }
            for source in sorted(benchmark.REQUIRED_EVIDENCE_SOURCES)
        ],
        "metrics": {
            "task_success": True,
            "evidence_grounding": 1.0,
            "cut_accuracy": 0.9,
            "coverage": 1.0,
            "continuity_errors": 0,
            "human_revision_distance": 2,
            "human_revision_distance_unit": "edit_graph_operations",
            "human_preference": 4,
            "wall_seconds": 12.5,
            "interventions": 0,
            "model_calls": 2,
            "model_seconds": 5.0,
            "compute_cost_usd": 0.0,
        },
    }


def _brief(source_id: str) -> dict:
    return {
        "intent": "Make a concise, clear edit grounded in the selected source moment.",
        "expected_source_moments": [
            {
                "id": "moment-1",
                "source_id": source_id,
                "start_seconds": 1.0,
                "end_seconds": 3.0,
                "description": "Core moment",
                "evidence_ids": ["pm:moment-1"],
            }
        ],
        "cut_ranges": [
            {
                "source_id": source_id,
                "start_seconds": 1.0,
                "end_seconds": 3.0,
                "moment_id": "moment-1",
                "reason": "Preserve the complete answer.",
            }
        ],
        "coverage_requirements": [{"id": "cover-core", "moment_ids": ["moment-1"]}],
        "continuity_constraints": [
            {"id": "speaker-match", "rule": "Show the speaking participant."}
        ],
        "preference_rubric": [
            {"id": "clarity", "description": "Dialogue clarity", "scale": [1, 5]}
        ],
    }


def _valid_report() -> dict:
    return {
        "tasks": [
            {
                "id": "dialogue",
                "brief": _brief("LCATL0013"),
                "task_types": ["dialogue_multicam"],
                "human_reference": {"path": "reference/dialogue.otio", "blinded": True},
                "runs": [
                    _run("training", "renders/dialogue-a.mp4"),
                    _run("held_out", "renders/dialogue-b.mp4"),
                ],
            },
            {
                "id": "visual",
                "brief": _brief("IMG_1818"),
                "task_types": ["visual_action", "visual_object"],
                "human_reference": {"path": "reference/visual.otio", "blinded": True},
                "runs": [
                    _run("calibration", "renders/visual-a.mp4"),
                    _run("calibration", "renders/visual-b.mp4"),
                ],
            },
        ]
    }


def test_accepts_two_briefs_covering_three_task_types_twice_with_holdout(tmp_path):
    for path in (
        "reference/dialogue.otio",
        "reference/visual.otio",
        "renders/dialogue-a.mp4",
        "renders/dialogue-b.mp4",
        "renders/visual-a.mp4",
        "renders/visual-b.mp4",
    ):
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("review artifact", encoding="utf-8")

    result = benchmark.validate_report(_valid_report(), output_root=tmp_path)

    assert result["valid"] is True
    assert result["task_count"] == 2
    assert result["task_types"] == sorted(benchmark.REQUIRED_TASK_TYPES)
    assert result["held_out_task_types"] == ["dialogue_multicam"]


def test_refuses_missing_human_reference_evidence_and_measurements():
    report = _valid_report()
    report["tasks"][0]["human_reference"]["blinded"] = False
    run = report["tasks"][1]["runs"][0]
    run["evidence_decisions"] = []
    del run["metrics"]["human_revision_distance"]

    result = benchmark.validate_report(report)

    assert result["valid"] is False
    assert any("must be recorded as blinded" in error for error in result["errors"])
    assert any(
        "evidence decision ledger is missing" in error for error in result["errors"]
    )
    assert any("human_revision_distance" in error for error in result["errors"])


def test_refuses_brief_without_moments_ranges_coverage_continuity_and_rubric():
    report = _valid_report()
    report["tasks"][1]["brief"] = {"intent": "A visual edit"}

    result = benchmark.validate_report(report)

    assert result["valid"] is False
    assert any(
        "expected source moments are required" in error for error in result["errors"]
    )
    assert any(
        "expected cut ranges are required" in error for error in result["errors"]
    )
    assert any(
        "coverage requirements are required" in error for error in result["errors"]
    )
    assert any(
        "continuity constraints are required" in error for error in result["errors"]
    )
    assert any(
        "human preference rubrics are required" in error for error in result["errors"]
    )


def test_refuses_artifact_paths_that_escape_output_root(tmp_path):
    report = _valid_report()
    report["tasks"][0]["runs"][0]["reviewable_output"] = "../outside.mp4"

    result = benchmark.validate_report(report, output_root=tmp_path)

    assert result["valid"] is False
    assert any("outside output_root" in error for error in result["errors"])


def test_aggregate_preserves_missing_metrics_as_unmeasured():
    report = {"tasks": [{"runs": [{"metrics": {"task_success": True}}]}]}

    scores = benchmark.aggregate_scores(report)

    assert scores["task_success"] == 1.0
    assert scores["cut_accuracy"] is None


def test_aggregate_does_not_score_malformed_values_as_numbers():
    report = {"tasks": [{"runs": [{"metrics": {"cut_accuracy": "unknown"}}]}]}

    scores = benchmark.aggregate_scores(report)

    assert scores["cut_accuracy"] is None


def test_agent_reference_score_uses_ordered_temporal_overlap_and_recall():
    result = benchmark.score_agent_reference(
        [
            {"source_id": "a", "start": 0.0, "end": 10.0},
            {"source_id": "b", "start": 20.0, "end": 30.0},
        ],
        [
            {"source_id": "a", "start": 0.0, "end": 10.0},
            {"source_id": "b", "start": 25.0, "end": 35.0},
        ],
    )

    assert result["cut_accuracy"] == 2 / 3
    assert result["coverage"] == 0.75
    assert result["human_revision_distance"] == 2
    assert result["boundary_edit_count"] == 2
    assert result["human_revision_distance_unit"] == "edit_graph_operations"


def test_agent_reference_score_empty_edit_is_measured_zero_with_insertions():
    result = benchmark.score_agent_reference(
        [], [{"source_id": "a", "start": 0.0, "end": 2.0}]
    )

    assert result["cut_accuracy"] == 0.0
    assert result["coverage"] == 0.0
    assert result["human_revision_distance"] == 1
