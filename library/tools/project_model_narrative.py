"""Persist speech-sequence interpretations in the canonical Project Model."""
from __future__ import annotations

from pathlib import Path
from typing import Any

AUTHOR = "narrative_relations.rules/1"


def pipeline_reference(speech_sequence: dict[str, Any],
                      persisted: dict[str, Any]) -> dict[str, Any]:
    """Return the pipeline view after interpretations become model-owned.

    The runner still needs the speech body for downstream planning, but
    retaining interpretation rows there would create a second evidence
    authority. Keep stable IDs and the exact committed model revision as
    the reference back to the canonical event history.
    """
    sequence = dict(speech_sequence)
    sequence.pop("interpretations", None)
    sequence["interpretation_ids"] = list(persisted["interpretation_ids"])
    sequence["project_model_revision"] = persisted["revision"]
    return sequence


def persist_speech_sequence(project_folder: str | Path,
                            speech_sequence: dict[str, Any]) -> dict[str, Any]:
    """Upsert the sequence's hypotheses by reference, preserving model authority."""
    from library.tools import project_model_evidence, project_model_store
    from library.tools.project_model import validate_project_model

    interpretations = speech_sequence.get("interpretations", [])
    if not isinstance(interpretations, list):
        raise TypeError("speech_sequence.interpretations must be a list")
    try:
        revision, model = project_model_store.load_project_model(Path(project_folder))
    except FileNotFoundError:
        model, _report = project_model_evidence.migrate_project_evidence(str(project_folder))
        revision = 0

    event_ids = {row["id"] for row in model.get("events", [])}
    incoming = []
    for row in interpretations:
        if not isinstance(row, dict):
            raise TypeError("narrative interpretation must be an object")
        # Event links are retained only when they resolve to the canonical
        # model. The speech passage remains the source authority by reference.
        clean = dict(row)
        scope = dict(row["scope"])
        candidate_events = list(scope.get("event_ids", []))
        valid_events = [event_id for event_id in candidate_events
                        if event_id in event_ids]
        scope["event_ids"] = valid_events
        clean["scope"] = scope
        relation = dict(row["narrative_relation"])
        relation["event_ids"] = valid_events
        clean["narrative_relation"] = relation
        clean["evidence_ids"] = [ref for ref in row["evidence_ids"]
                                 if ref not in candidate_events]
        clean["evidence_ids"] = list(dict.fromkeys(
            [*clean["evidence_ids"], *valid_events]))
        incoming.append(clean)

    model = dict(model)
    model["interpretations"] = [
        row for row in model.get("interpretations", [])
        if row.get("author") != AUTHOR
    ] + incoming
    model.setdefault("interpretations", [])
    for attempt in range(3):
        validate_project_model(model)
        try:
            new_revision = project_model_store.commit_project_model(
                Path(project_folder), model, expected_revision=revision,
                author="speech_sequence",
                reason="persist narrative interpretations")
            break
        except RuntimeError:
            if attempt == 2:
                raise
            revision, model = project_model_store.load_project_model(
                Path(project_folder))
            model = dict(model)
            model["interpretations"] = [
                row for row in model.get("interpretations", [])
                if row.get("author") != AUTHOR
            ] + incoming
    return {"status": "committed", "revision": new_revision,
            "interpretation_ids": [row["id"] for row in incoming]}
