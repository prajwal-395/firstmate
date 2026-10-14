"""Deterministic, evidence-linked narrative relation hypotheses.

These are candidate interpretations, not asserted facts. The rules are
deliberately conservative; a local model may later refine this candidate set.
"""
from __future__ import annotations

import re
from typing import Any

RELATION_TYPES = frozenset({
    "claim", "topic", "explanation", "demonstration", "context_dependency",
})

_RULES = {
    "claim": re.compile(r"\b(?:I (?:think|believe|argue|claim)|we (?:should|must|need to)|the point is|in my view)\b", re.IGNORECASE),
    "explanation": re.compile(r"\b(?:because|since|therefore|that(?:'s| is) why|which means|as a result)\b", re.IGNORECASE),
    "demonstration": re.compile(r"\b(?:for example|for instance|such as|here's (?:an )?example|look at)\b", re.IGNORECASE),
    "context_dependency": re.compile(r"\b(?:this|that|these|those|it|they|he|she|as mentioned|the previous)\b", re.IGNORECASE),
}


def _passage_id(passage: dict[str, Any], index: int) -> str:
    return str(passage.get("position") or passage.get("passage_id") or f"body:{index + 1}")


def deterministic_candidates(passages: list[dict[str, Any]], events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return typed candidates tied to the passage and overlapping event IDs.

    A topic is emitted only when the passage explicitly carries one. Speech
    passage references are always retained; event references are added only
    when their source intervals overlap the aligned passage.
    """
    events = events or []
    output = []
    for index, passage in enumerate(passages):
        text = str(passage.get("text") or passage.get("passage") or passage.get("content") or "")
        pid = _passage_id(passage, index)
        start, end = passage.get("source_start"), passage.get("source_end")
        event_ids = [str(event["id"]) for event in events
                     if start is not None and end is not None
                     and event.get("id") is not None
                     and event.get("start") is not None and event.get("end") is not None
                     and float(event["start"]) < float(end)
                     and float(start) < float(event["end"])]
        kinds = [kind for kind, pattern in _RULES.items() if pattern.search(text)]
        topic = passage.get("topic") or passage.get("topics")
        if topic:
            kinds.append("topic")
        for kind in kinds:
            output.append({
                "id": f"narrative:{pid}:{kind}",
                "kind": kind,
                "text": text,
                "speech_passage_ids": [pid],
                "event_ids": event_ids,
                "evidence_ids": [f"speech:{pid}", *event_ids],
                "uncertainty": 0.5,
                "certainty": "interpreted",
                "status": "candidate",
                "producer": "narrative_relations.rules/1",
            })
    return output


def to_interpretations(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project candidates into the canonical Interpretation records."""
    return [{
        "id": candidate["id"],
        "kind": f"narrative_{candidate['kind']}",
        "author": candidate["producer"],
        "objective": None,
        "policy_id": None,
        "evidence_ids": candidate["evidence_ids"],
        "scope": {
            "speech_passage_ids": candidate["speech_passage_ids"],
            "event_ids": candidate["event_ids"],
        },
        "uncertainty": candidate["uncertainty"],
        "certainty": "interpreted",
        "narrative_relation": {
            "relation_type": candidate["kind"],
            "speech_passage_ids": candidate["speech_passage_ids"],
            "event_ids": candidate["event_ids"],
            "status": "candidate",
        },
    } for candidate in candidates]
