import json
from pathlib import Path

from library.tools.narrative_relations import (
    deterministic_candidates,
    to_interpretations,
)
from library.tools.project_model import validate_project_model

FIXTURE = Path(__file__).parents[2] / "fixtures" / "narrative_relations_proxy.json"


def _scores():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected = {(row["id"], label) for row in fixture["rows"] for label in row["labels"]}
    passages = [{"position": row["id"], "text": row["text"], **(
        {"topic": row["topic"]} if row.get("topic") else {})}
        for row in fixture["rows"]]
    predicted = {(row["speech_passage_ids"][0], row["kind"])
                 for row in deterministic_candidates(passages)}
    true_positive = len(expected & predicted)
    precision = true_positive / len(predicted)
    recall = true_positive / len(expected)
    return fixture, predicted, precision, recall


def test_proxy_argument_fixture_reports_relation_precision_recall():
    fixture, _, precision, recall = _scores()
    assert fixture["real_corpus_status"] == "pending human-annotated argument footage"
    assert precision == 1.0
    assert recall == 1.0


def test_hypotheses_link_speech_and_overlapping_events_with_uncertainty():
    passages = [{"position": "p1", "text": "I believe this works because it is measured.",
                 "source_start": 1.0, "source_end": 3.0}]
    candidates = deterministic_candidates(passages, [{"id": "event-1", "start": 2.0, "end": 4.0}])
    assert {item["kind"] for item in candidates} == {"claim", "explanation", "context_dependency"}
    assert all(item["evidence_ids"] == ["speech:p1", "event-1"] for item in candidates)
    assert all(item["uncertainty"] == 0.5 and item["status"] == "candidate" for item in candidates)
    interpretations = to_interpretations(candidates)
    assert all(item["scope"] == {"speech_passage_ids": ["p1"], "event_ids": ["event-1"]}
               for item in interpretations)


def test_narrative_interpretations_validate_as_project_model_records():
    model_path = Path(__file__).parents[2] / "fixtures" / "project_model_e2e" / "expected-project-model.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    model.setdefault("events", []).append({
        "id": "event-pickup",
        "interval": {"clock_id": model["clocks"][0]["id"], "start": 0, "end": 24},
        "kind": "demonstration", "participant_ids": [], "status": "candidate",
        "certainty": "inferred", "evidence_ids": [model["observations"][0]["id"]],
    })
    model.setdefault("interpretations", []).append({
        "id": "narrative:p1:explanation", "kind": "narrative_explanation",
        "author": "rules/1", "objective": None, "policy_id": None,
        "evidence_ids": ["speech:p1", "event-pickup"],
        "scope": {"speech_passage_ids": ["p1"], "event_ids": ["event-pickup"]},
        "uncertainty": 0.5, "certainty": "interpreted",
        "narrative_relation": {"relation_type": "explanation", "speech_passage_ids": ["p1"],
                               "event_ids": ["event-pickup"], "status": "candidate"},
    })
    validate_project_model(model)
    model["interpretations"][-1]["uncertainty"] = None
    try:
        validate_project_model(model)
    except ValueError as error:
        assert "explicit uncertainty" in str(error)
    else:
        raise AssertionError("narrative interpretation without uncertainty was accepted")
    model["interpretations"][-1]["uncertainty"] = 0.5
    model["interpretations"][-1]["scope"]["event_ids"] = ["missing-event"]
    model["interpretations"][-1]["narrative_relation"]["event_ids"] = ["missing-event"]
    model["interpretations"][-1]["evidence_ids"] = ["speech:p1", "missing-event"]
    try:
        validate_project_model(model)
    except ValueError as error:
        assert "unknown event" in str(error)
    else:
        raise AssertionError("narrative interpretation with unknown event was accepted")

