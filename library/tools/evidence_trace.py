"""B-16: why does Ren believe this, and what original evidence supports it?

The Project Model holds an object's observations, the identity links that
propose two observations are the same thing, the relations between
entities, the interpretations that read meaning into them, and the
events they take part in - and every one of those rows names the
observations it rests on. What was missing was the ONE reader that
follows a reference through that chain and out to the source footage.

This module is that reader. :func:`trace` takes one reference - an
entity, an event, an observation or a track - and answers with:

- the row the reference names, and everything the model says about it;
- its observations, its possible identity links, its relations, the
  events it takes part in, its tracks, and the interpretations resting
  on it;
- the multi-hop walk through relations and identity links, so a reader
  can see the path rather than only the neighbourhood;
- for every leaf observation, WHERE IT CAME FROM: the source file and
  the exact seconds, and WHAT PRODUCED IT: the derivation, the method,
  the licence context and the observation's own references;
- the contradictions that touch the trace, and what the answer does and
  does not establish.

**Absent is absent.** A reference the model does not hold is answered
with the reason, never with a nearest match: a trace of the wrong thing
is worse than no trace. A leaf observation that cannot be placed in the
source is in the answer with its reason, never dropped.

Nothing is written anywhere. The model dict is read, and the read layer
that indexes it is :mod:`library.tools.evidence_graph`.
"""

from __future__ import annotations

from typing import Any

from library.tools import evidence_graph
from library.tools.evidence_query import (
    certainty_of,
    coverage_of,
    derivation_rows,
    freshness_of,
    placements,
)

FORMAT = "evidence_trace/1"
"""The answer's own format tag, so a reader can refuse an unknown one."""

DEFAULT_HOPS = 2
"""How far the walk follows relations and identity links by default."""


class EvidenceTraceError(ValueError):
    """A trace that cannot be answered as asked."""


def trace(model: dict[str, Any], ref: str, *,
          project: str | None = None,
          hops: int = DEFAULT_HOPS) -> dict[str, Any]:
    """Follow one reference through the model and out to the footage.

    ``ref`` is an entity id (or label), an event id, an observation id
    or a track id. The answer names what the reference was resolved to,
    so a reader never has to guess which of them it got.
    """
    if not isinstance(model, dict):
        raise EvidenceTraceError("a trace reads a Project Model dict")
    if not isinstance(ref, str) or not ref.strip():
        raise EvidenceTraceError("a trace needs a reference to follow")
    if not isinstance(hops, int) or hops < 1:
        raise EvidenceTraceError("a trace walks at least one hop")
    reference = ref.strip()
    index = evidence_graph.EvidenceIndex(model)
    candidates = _referent(index, reference)
    if not candidates:
        return {"format": FORMAT, "ref": reference, "found": False,
                "reason": ("no entity, event, observation or track in the "
                           "model carries this reference"),
                "sources": [], "derivation": []}
    if len(candidates) > 1:
        return {"format": FORMAT, "ref": reference, "found": False,
                "reason": ("the reference names more than one thing in the "
                           "model; name the one you mean"),
                "candidates": [{"kind": kind, "id": row.get("id")}
                               for kind, row in candidates],
                "sources": [], "derivation": []}
    kind, row = candidates[0]
    if kind == "entity":
        answer = _trace_entity(model, index, row, hops, project)
    elif kind == "event":
        answer = _trace_event(model, index, row, project)
    elif kind == "observation":
        answer = _trace_observation(model, index, row, project)
    else:
        answer = _trace_track(model, index, row, project)
    answer.update({"format": FORMAT, "ref": reference, "kind": kind,
                   "found": True, "hops": hops})
    return answer


def _referent(index: evidence_graph.EvidenceIndex,
              ref: str) -> list[tuple[str, dict[str, Any]]]:
    """What the reference names: one row, or several (ambiguous)."""
    model = index.model
    found: list[tuple[str, dict[str, Any]]] = []
    entity = next((row for row in model.get("entities") or []
                   if isinstance(row, dict) and row.get("id") == ref), None)
    if entity is None:
        entity = next((row for row in model.get("entities") or []
                       if isinstance(row, dict)
                       and str(row.get("label") or "").lower() == ref.lower()),
                      None)
    if entity is not None:
        found.append(("entity", entity))
    event = next((row for row in model.get("events") or []
                  if isinstance(row, dict) and row.get("id") == ref), None)
    if event is not None:
        found.append(("event", event))
    observation = index.observation(ref)
    if observation is not None:
        found.append(("observation", observation))
    track = next((row for row in model.get("tracks") or []
                  if isinstance(row, dict) and row.get("id") == ref), None)
    if track is not None:
        found.append(("track", track))
    return found


def _observations_summary(observations: list[dict[str, Any]]
                          ) -> list[dict[str, Any]]:
    """One line per observation: what it is, where it sits, how fresh."""
    rows = []
    for observation in observations:
        interval = observation.get("interval") or {}
        rows.append({
            "observation_id": observation.get("id"),
            "modality": observation.get("modality"),
            "asset_id": observation.get("asset_id"),
            "clock_id": interval.get("clock_id"),
            "start": interval.get("start"),
            "end": interval.get("end"),
            "provenance_id": observation.get("provenance_id"),
            "coverage": coverage_of(observation),
            "freshness": freshness_of(observation),
            "certainty": certainty_of(observation),
        })
    return rows


def _interpretations_for(model: dict[str, Any],
                         observation_ids: set[str],
                         event_ids: set[str],
                         entity_id: str | None) -> list[dict[str, Any]]:
    """The interpretations that rest on this trace's evidence.

    An interpretation is included when it cites one of the traced
    observations, when its scope names one of the traced events, or
    when its scope names the traced entity.
    """
    rows = []
    for row in model.get("interpretations") or []:
        if not isinstance(row, dict):
            continue
        scope = row.get("scope") or {}
        cites_observation = bool(set(row.get("evidence_ids") or [])
                                 & observation_ids)
        cites_event = bool(set(scope.get("event_ids") or []) & event_ids)
        cites_entity = bool(entity_id and entity_id in (
            scope.get("entity_ids") or []))
        if cites_observation or cites_event or cites_entity:
            rows.append(row)
    return rows


def _unique(ids: list[str]) -> list[str]:
    """The ids, once each, in the order they were named."""
    seen: list[str] = []
    for value in ids:
        if value not in seen:
            seen.append(value)
    return seen


def _observation_ids(observations: list[dict[str, Any]]) -> list[str]:
    """The ids of the observations themselves."""
    return [observation["id"] for observation in observations
            if isinstance(observation.get("id"), str)]


def _cited_observation_ids(rows: list[Any], *keys: str) -> list[str]:
    """The observation ids the named keys of these rows point at."""
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in keys:
            value = row.get(key)
            if isinstance(value, str) and value not in found:
                found.append(value)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, str) and item not in found:
                        found.append(item)
    return found


def _contradictions_for(index: evidence_graph.EvidenceIndex,
                         observation_ids: set[str]) -> list[dict[str, Any]]:
    """The contradictions that touch this trace, and only those."""
    if not observation_ids:
        return []
    found = []
    for contradiction in evidence_graph.find_contradictions(index):
        named = set(contradiction.get("observation_ids") or [])
        named |= set(contradiction.get("event_ids") or [])
        if named & observation_ids:
            found.append(contradiction)
    return found


def _leaf_rows(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The rows the uncertainty summary reads."""
    return [{"observation_id": observation.get("id"),
             "coverage": [coverage_of(observation)],
             "freshness": [freshness_of(observation)],
             "clock_error_ticks": 0}
            for observation in observations]


def _trace_entity(model: dict[str, Any],
                  index: evidence_graph.EvidenceIndex,
                  entity: dict[str, Any], hops: int,
                  project: str | None) -> dict[str, Any]:
    """Everything the model says about one entity, and the walk from it."""
    entity_id = entity.get("id")
    resolved = index.resolve_entity(entity_id)
    observations = list(resolved.get("observations") or [])
    links = list(resolved.get("identity_links") or [])
    relations = list(resolved.get("relations") or [])
    events = list(resolved.get("events") or [])
    tracks = list(resolved.get("tracks") or [])
    traversal = index.traverse(entity_id, hops=hops)
    observation_ids = {observation.get("id") for observation in observations}
    event_ids = {event.get("id") for event in events}
    interpretations = _interpretations_for(model, observation_ids, event_ids,
                                           entity_id)
    leaf_ids = _unique(_observation_ids(observations)
                       + _cited_observation_ids(links, "observation_id",
                                               "supporting_observation_ids")
                       + _cited_observation_ids(relations, "evidence_ids")
                       + _cited_observation_ids(events, "evidence_ids"))
    leaves = index.observations_by_ids(leaf_ids)
    contradictions = _contradictions_for(index, observation_ids)
    return {
        "entity": entity,
        "observations": _observations_summary(observations),
        "identity_links": links,
        "relations": relations,
        "events": events,
        "tracks": tracks,
        "interpretations": interpretations,
        "traversal": {"nodes": traversal.get("nodes", []),
                      "edges": traversal.get("edges", [])},
        "leaf_observation_ids": leaf_ids,
        "sources": placements(model, leaves, project),
        "derivation": derivation_rows(leaves),
        "contradictions": contradictions,
        "uncertainty": evidence_graph.answer_uncertainty(
            _leaf_rows(leaves), contradictions=len(contradictions)),
    }


def _trace_event(model: dict[str, Any],
                 index: evidence_graph.EvidenceIndex,
                 event: dict[str, Any],
                 project: str | None) -> dict[str, Any]:
    """One event: its evidence, its participants, its state changes."""
    resolved = index.resolve_event(event.get("id"))
    evidence = list(resolved.get("evidence") or [])
    participants = list(resolved.get("participants") or [])
    state_changes = list(resolved.get("state_changes") or [])
    observation_ids = {observation.get("id") for observation in evidence}
    event_ids = {event.get("id")}
    interpretations = _interpretations_for(model, observation_ids, event_ids,
                                           None)
    leaf_ids = _unique(_cited_observation_ids(evidence, "id")
                       + _cited_observation_ids(state_changes, "evidence_ids"))
    leaves = index.observations_by_ids(leaf_ids)
    contradictions = _contradictions_for(index, observation_ids)
    return {
        "event": event,
        "observations": _observations_summary(evidence),
        "participants": participants,
        "state_changes": state_changes,
        "interpretations": interpretations,
        "leaf_observation_ids": leaf_ids,
        "sources": placements(model, leaves, project),
        "derivation": derivation_rows(leaves),
        "contradictions": contradictions,
        "uncertainty": evidence_graph.answer_uncertainty(
            _leaf_rows(leaves), contradictions=len(contradictions)),
    }


def _trace_observation(model: dict[str, Any],
                       index: evidence_graph.EvidenceIndex,
                       observation: dict[str, Any],
                       project: str | None) -> dict[str, Any]:
    """One observation: what it says, who it is attributed to, where it is."""
    observation_id = observation.get("id")
    entities = sorted(index.entities_for_observation(observation_id))
    links = [link for link in model.get("identity_links") or []
             if isinstance(link, dict)
             and (link.get("observation_id") == observation_id
                  or observation_id in (link.get("supporting_observation_ids")
                                        or []))]
    leaves = index.observations_by_ids([observation_id])
    contradictions = _contradictions_for(index, {observation_id})
    return {
        "observation": observation,
        "observations": _observations_summary(leaves),
        "entities": entities,
        "identity_links": links,
        "leaf_observation_ids": [observation_id],
        "sources": placements(model, leaves, project),
        "derivation": derivation_rows(leaves),
        "contradictions": contradictions,
        "uncertainty": evidence_graph.answer_uncertainty(
            _leaf_rows(leaves), contradictions=len(contradictions)),
    }


def _trace_track(model: dict[str, Any],
                 index: evidence_graph.EvidenceIndex,
                 track: dict[str, Any],
                 project: str | None) -> dict[str, Any]:
    """One track: its samples, the evidence behind them, its owner."""
    named: list[str] = []
    for sample in track.get("samples") or []:
        if isinstance(sample, dict):
            named.extend(sample.get("evidence_ids") or [])
    leaves = index.observations_by_ids(named)
    observation_ids = {observation.get("id") for observation in leaves}
    links = [link for link in model.get("identity_links") or []
             if isinstance(link, dict) and link.get("track_id") == track.get("id")]
    contradictions = _contradictions_for(index, observation_ids)
    return {
        "track": track,
        "observations": _observations_summary(leaves),
        "identity_links": links,
        "leaf_observation_ids": [observation.get("id") for observation in leaves],
        "sources": placements(model, leaves, project),
        "derivation": derivation_rows(leaves),
        "contradictions": contradictions,
        "uncertainty": evidence_graph.answer_uncertainty(
            _leaf_rows(leaves), contradictions=len(contradictions)),
    }
