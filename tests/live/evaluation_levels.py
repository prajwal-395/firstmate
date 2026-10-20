"""Shared evaluation taxonomy; reports must name the level they establish."""

LEVELS = {
    1: {
        "name": "primitive_qualification",
        "question": "Can the backend perform a specified operation?",
    },
    2: {
        "name": "semantic_execution",
        "question": "Can AXI safely execute a typed request?",
    },
    3: {
        "name": "autonomous_orchestration",
        "question": "Can Ren infer operations and targets from an instruction?",
    },
    4: {
        "name": "editorial_acceptance",
        "question": "Does the completed video satisfy the creative brief?",
    },
}


def catalog() -> list[dict[str, object]]:
    coverage = {
        1: "live_backend_qualification_suites",
        2: "evaluator_directed_semantic_acceptance",
        3: "blind_runner_available_agent_driver_required",
        4: "not_measured_no_editorial_brief_scorer",
    }
    return [{"level": number, **definition,
             "evidence_status": coverage[number]}
            for number, definition in LEVELS.items()]
