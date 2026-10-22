"""Evidence knowledge-graph read layer over the canonical Project Model.

The Project Model is the canonical store (DATA-001) and this module is a
read-only view over it, never a second store (DATA-003): every function
takes an already-loaded model dict, builds nothing on disk, and writes
nothing back. It closes the gaps the punchlist names for turning stored
evidence into a reusable graph:

- entity-centric and event-centric browsing (DATA-044/045),
- multi-hop relationship traversal (DATA-046),
- questions spanning several linked observations (DATA-047),
- selective slices so reasoning loads relevant evidence, not whole
  indexes (DATA-048), with per-collection indexes built lazily on first
  use,
- in-memory predicate indexes for temporal, entity, text and spatial
  access (DATA-049) over the JSON sidecar store the project keeps until
  a measured need says otherwise (DATA-050/051),
- the five-way absence vocabulary: present, absent (measured), not
  observed, not analysed, inconclusive (DATA-036),
- contradiction detection over speaker, object and event assertions
  (DATA-038) with routing to targeted verification shaped for
  ``query_directed_acquisition.plan_query_acquisition`` (DATA-039),
- decomposed answer uncertainty with a documented summary rank, never
  a calibrated probability (DATA-042), plus a loss audit naming scored
  rows whose type migration dropped (DATA-041).
Thresholds below were set on synthetic calibration fixtures and are
reported once in docs/benchmarks/vep-mu-graph-scorecard-2026-10-16.md;
they are named constants so a held-out run can move them with the
reason recorded beside the number.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

try:
    from library.tools.evidence_query import (
        MODALITY_TO_LANE,
        TICKS_PER_SECOND,
        classify_absence as _classify_absence,
    )
except ImportError:  # imported as `tools.*` from inside library/
    from tools.evidence_query import (
        MODALITY_TO_LANE,
        TICKS_PER_SECOND,
        classify_absence as _classify_absence,
    )

SPEECH_MODALITIES = frozenset({
    "speech", "speech_word", "voice_turn", "event_span",
})
"""Modalities whose observations can attribute speech to a speaker."""

SPEAKER_MIN_OVERLAP_TICKS = 250
"""Two speaker attributions must share at least a quarter second before
they count as conflicting; diarization boundaries jitter below this."""

ATTRIBUTE_MIN_OVERLAP_FRACTION = 0.5
"""Object/event conflicts need half of the shorter span shared, so a
passing overlap at a cut boundary is not a contradiction."""

OBJECT_MIN_IOU = 0.5
"""Box disagreement threshold, the same IoU >= 0.5 bar the October
detection bakeoff reports recall against."""

OBJECT_BOX_KEYS = ("box", "bbox", "bbox_pixels", "target_region")
"""Payload keys searched, in order, for a [x, y, w, h] box."""

OBJECT_ID_KEYS = ("object_id", "object_class_id", "object_label", "label")
"""Payload keys searched, in order, for the asserted object identity."""

ABSENT = "absent"
NOT_OBSERVED = "not_observed"
NOT_ANALYZED = "not_analyzed"
INCONCLUSIVE = "inconclusive"
PRESENT = "present"
ABSENCE_STATES = (PRESENT, ABSENT, NOT_OBSERVED, NOT_ANALYZED, INCONCLUSIVE)
"""DATA-036 vocabulary: measured absence is distinct from silence under
complete coverage (not observed), from never having run (not analysed),
and from partial or stale coverage (inconclusive)."""


def _interval_of(row: dict[str, Any]) -> tuple[str, int, int] | None:
    interval = (row or {}).get("interval") or {}
    clock_id = interval.get("clock_id")
    start, end = interval.get("start"), interval.get("end")
    if (not isinstance(clock_id, str) or not isinstance(start, int)
            or not isinstance(end, int) or isinstance(start, bool)
            or isinstance(end, bool) or end <= start):
        return None
    return clock_id, start, end


def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def _tokens(text: str) -> set[str]:
    return {part for part in "".join(
        ch.lower() if ch.isalnum() else " " for ch in text).split() if part}


def _payload_text(payload: Any, depth: int = 0) -> list[str]:
    """String leaves of a payload, for the lexical text index."""
    if depth > 3 or payload is None:
        return []
    if isinstance(payload, str):
        return [payload] if payload.strip() else []
    if isinstance(payload, (int, float)):
        return [str(payload)]
    if isinstance(payload, dict):
        found = []
        for value in payload.values():
            found.extend(_payload_text(value, depth + 1))
        return found
    if isinstance(payload, (list, tuple)):
        found = []
        for value in payload:
            found.extend(_payload_text(value, depth + 1))
        return found
    return []


def _box_of(payload: dict[str, Any]) -> list[float] | None:
    for key in OBJECT_BOX_KEYS:
        box = payload.get(key)
        if (isinstance(box, (list, tuple)) and len(box) == 4
                and all(isinstance(v, (int, float)) and math.isfinite(v)
                        for v in box) and box[2] > 0 and box[3] > 0):
            return [float(v) for v in box]
    geometry = payload.get("geometry")
    if isinstance(geometry, dict):
        return _box_of(geometry)
    return None


def _box_iou(first: list[float], second: list[float]) -> float:
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    inter = max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) * max(
        0.0, min(ay + ah, by + bh) - max(ay, by))
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _identity_of(payload: dict[str, Any]) -> str | None:
    for key in OBJECT_ID_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


class EvidenceIndex:
    """Lazy per-collection indexes over one model dict (DATA-048/049).

    The model dict is read, never copied wholesale: each sub-index is
    built on first use and only for the collections the caller touches,
    so a reasoning pass that needs one entity's observations does not
    pay for a whole-model scan.
    """

    def __init__(self, model: dict[str, Any]) -> None:
        if not isinstance(model, dict):
            raise ValueError("EvidenceIndex reads a Project Model dict")
        self._model = model
        self._by_clock: dict[str, list[tuple[int, int, dict]]] | None = None
        self._clock_maxend: dict[str, tuple[list, int]] | None = None
        self._obs_by_id: dict[str, dict] | None = None
        self._obs_entities: dict[str, set[str]] | None = None
        self._entity_obs: dict[str, set[str]] | None = None
        self._token_index: dict[str, set[str]] | None = None
        self._relations: dict[str, list[dict]] | None = None
        self._events_by_id: dict[str, dict] | None = None
        self._spatial: dict[str, list[tuple[int, int, list[float], str]]] | None = None

    @property
    def model(self) -> dict[str, Any]:
        """The underlying model dict, still owned by the caller."""
        return self._model

    def _ensure_temporal(self) -> None:
        if self._by_clock is not None:
            return
        by_clock: dict[str, list[tuple[int, int, dict]]] = defaultdict(list)
        obs_by_id = {}
        for obs in self._model.get("observations", []):
            if not isinstance(obs, dict) or not obs.get("id"):
                continue
            obs_by_id[obs["id"]] = obs
            span = _interval_of(obs)
            if span is None:
                continue
            clock_id, start, end = span
            by_clock[clock_id].append((start, end, obs))
        for rows in by_clock.values():
            rows.sort(key=lambda row: (row[0], row[1]))
        self._by_clock = dict(by_clock)
        # Max-end augmentation over the start-sorted rows (I01): a
        # segment tree per clock holding the maximum interval end in
        # each node range, so a narrow query prunes subtrees that
        # start too late or end too early instead of scanning every
        # earlier-starting record. The tree is static - the model is
        # read-only after build - and traversal runs left-to-right,
        # so results keep the (start, end) order the bisect scan gave.
        maxend = {}
        for clock, rows in by_clock.items():
            count = len(rows)
            size = 1 << (count - 1).bit_length() if count else 1
            tree: list = [float("-inf")] * (2 * size)
            for pos, (_, row_end, _) in enumerate(rows):
                tree[size + pos] = row_end
            for pos in range(size - 1, 0, -1):
                tree[pos] = max(tree[2 * pos], tree[2 * pos + 1])
            maxend[clock] = (tree, size)
        self._clock_maxend = maxend
        self._obs_by_id = obs_by_id

    def _ensure_entities(self) -> None:
        if self._obs_entities is not None:
            return
        self._ensure_temporal()
        obs_entities: dict[str, set[str]] = defaultdict(set)
        for link in self._model.get("identity_links", []):
            if not isinstance(link, dict):
                continue
            obs_id, entity_id = link.get("observation_id"), link.get("entity_id")
            if isinstance(obs_id, str) and isinstance(entity_id, str):
                obs_entities[obs_id].add(entity_id)
        for obs_id, obs in (self._obs_by_id or {}).items():
            payload = obs.get("payload")
            native = payload.get("evidence_contract") if isinstance(payload, dict) else None
            target = native.get("target_entity_id") if isinstance(native, dict) else None
            if isinstance(target, str) and target:
                obs_entities[obs_id].add(target)
        entity_obs: dict[str, set[str]] = defaultdict(set)
        for obs_id, entities in obs_entities.items():
            for entity_id in entities:
                entity_obs[entity_id].add(obs_id)
        self._obs_entities = dict(obs_entities)
        self._entity_obs = dict(entity_obs)

    def _ensure_text(self) -> None:
        if self._token_index is not None:
            return
        self._ensure_temporal()
        index: dict[str, set[str]] = defaultdict(set)
        for obs_id, obs in (self._obs_by_id or {}).items():
            text = " ".join([str(obs.get("modality", ""))] +
                            _payload_text(obs.get("payload")))
            for token in _tokens(text):
                index[token].add(f"obs:{obs_id}")
        for event in self._model.get("events", []):
            if not isinstance(event, dict) or not event.get("id"):
                continue
            text = " ".join([str(event.get("kind", ""))] +
                            _payload_text(event.get("payload")))
            for token in _tokens(text):
                index[token].add(f"event:{event['id']}")
        self._token_index = dict(index)

    def _ensure_relations(self) -> None:
        if self._relations is not None:
            return
        adjacency: dict[str, list[dict]] = defaultdict(list)
        for relation in self._model.get("relations", []):
            if not isinstance(relation, dict):
                continue
            for endpoint in (relation.get("subject_id"), relation.get("object_id")):
                if isinstance(endpoint, str):
                    adjacency[endpoint].append(relation)
        self._relations = dict(adjacency)

    def _ensure_events(self) -> None:
        if self._events_by_id is not None:
            return
        self._events_by_id = {
            event["id"]: event for event in self._model.get("events", [])
            if isinstance(event, dict) and event.get("id")}

    def _ensure_spatial(self) -> None:
        if self._spatial is not None:
            return
        self._ensure_temporal()
        spatial: dict[str, list[tuple[int, int, list[float], str]]] = defaultdict(list)
        for obs in self._model.get("observations", []):
            if not isinstance(obs, dict):
                continue
            payload = obs.get("payload")
            if not isinstance(payload, dict):
                continue
            box = _box_of(payload)
            span = _interval_of(obs)
            if box is None or span is None or not obs.get("id"):
                continue
            clock_id, start, end = span
            spatial[clock_id].append((start, end, box, obs["id"]))
        for rows in spatial.values():
            rows.sort(key=lambda row: (row[0], row[1]))
        self._spatial = dict(spatial)

    def _overlap_scan(self, clock_id: str, start: int, end: int,
                      *, point: bool) -> list[dict]:
        """Rows on a clock overlapping ``[start, end)`` in (start, end) order.

        With ``point`` the window is the single tick ``start`` and the
        containment read is ``row_start <= tick < row_end``; otherwise
        the read is ``row_start < end and row_end > start``. Both match
        the bisect scan this replaced, row for row.
        """
        self._ensure_temporal()
        rows = (self._by_clock or {}).get(clock_id)
        if not rows:
            return []
        tree, size = (self._clock_maxend or {})[clock_id]
        count = len(rows)
        found = []
        stack = [(1, 0, size)]
        while stack:
            node, lo, hi = stack.pop()
            if lo >= count:
                continue
            if tree[node] <= start:
                # No row under this node ends after the window's
                # start: nothing below can overlap.
                continue
            if hi - lo == 1:
                row_start, row_end, obs = rows[lo]
                if point:
                    if row_start <= start < row_end:
                        found.append(obs)
                elif row_start < end and row_end > start:
                    found.append(obs)
                continue
            if (rows[lo][0] > start) if point else (rows[lo][0] >= end):
                # Every row under this node starts past the window
                # (rows are start-sorted): nothing below can overlap.
                continue
            mid = (lo + hi) // 2
            stack.append((2 * node + 1, mid, hi))
            stack.append((2 * node, lo, mid))
        return found

    def observations_at(self, clock_id: str, tick: int) -> list[dict]:
        """Observations live on a clock at one tick (DATA-049 temporal)."""
        return self._overlap_scan(clock_id, tick, tick, point=True)

    def observations_overlapping(self, clock_id: str, start: int,
                                 end: int) -> list[dict]:
        """Observations on a clock overlapping ``[start, end)``."""
        if end <= start:
            raise ValueError("overlap window must be non-empty")
        return self._overlap_scan(clock_id, start, end, point=False)

    def entities_for_observation(self, obs_id: str) -> set[str]:
        """Entity ids attributed to one observation."""
        self._ensure_entities()
        return set((self._obs_entities or {}).get(obs_id, set()))

    def observations_for_entity(self, entity_id: str) -> list[dict]:
        """Observations attributed to one entity (DATA-044)."""
        self._ensure_entities()
        self._ensure_temporal()
        return [self._obs_by_id[obs_id]
                for obs_id in sorted(self._entity_obs.get(entity_id, set()))
                if obs_id in self._obs_by_id]

    def observation(self, observation_id: str) -> dict | None:
        """One observation by id, or None when the model has no such row."""
        self._ensure_temporal()
        return (self._obs_by_id or {}).get(observation_id)

    def observations_by_ids(self, observation_ids: list[str]) -> list[dict]:
        """The named observations the model holds, in the order named.

        A name the model does not hold is skipped: the caller asked for
        the rows that exist, and compares against its own count.
        """
        self._ensure_temporal()
        rows = []
        for observation_id in observation_ids or []:
            row = (self._obs_by_id or {}).get(observation_id)
            if row is not None:
                rows.append(row)
        return rows

    def resolve_entity(self, ref: str) -> dict[str, Any]:
        """Browse everything the model says about one entity (DATA-044).

        ``ref`` is an entity id first, then a label. The answer carries
        the entity row, its attributed observations, events it
        participates in, relations touching it, identity links naming
        it, and tracks whose samples cite its observations.
        """
        entity = None
        for row in self._model.get("entities", []):
            if isinstance(row, dict) and (row.get("id") == ref or row.get("label") == ref):
                entity = row
                if row.get("id") == ref:
                    break
        if entity is None:
            return {"entity": None, "observations": [], "events": [],
                    "relations": [], "identity_links": [], "tracks": [],
                    "found": False}
        self._ensure_entities()
        self._ensure_relations()
        self._ensure_events()
        entity_id = entity["id"]
        attributed = set(self._entity_obs.get(entity_id, set()))
        events = [event for event in self._model.get("events", [])
                  if isinstance(event, dict)
                  and entity_id in (event.get("participant_ids") or [])]
        links = [link for link in self._model.get("identity_links", [])
                 if isinstance(link, dict) and link.get("entity_id") == entity_id]
        tracks = [track for track in self._model.get("tracks", [])
                  if isinstance(track, dict) and any(
                      set(sample.get("evidence_ids", []) or []) & attributed
                      for sample in track.get("samples", [])
                      if isinstance(sample, dict))]
        return {"entity": entity, "found": True,
                "observations": [self._obs_by_id[obs_id] for obs_id in
                                 sorted(attributed) if obs_id in self._obs_by_id],
                "events": events,
                "relations": list(self._relations.get(entity_id, [])),
                "identity_links": links, "tracks": tracks}

    def resolve_event(self, event_id: str) -> dict[str, Any]:
        """Browse everything behind one event (DATA-045): the event row,
        its evidence observations, participants, and state changes."""
        self._ensure_events()
        self._ensure_temporal()
        event = self._events_by_id.get(event_id)
        if event is None:
            return {"event": None, "found": False, "evidence": [],
                    "participants": [], "state_changes": []}
        evidence = [self._obs_by_id[obs_id]
                    for obs_id in event.get("evidence_ids", [])
                    if obs_id in self._obs_by_id]
        participants = [row for row in self._model.get("entities", [])
                        if isinstance(row, dict)
                        and row.get("id") in (event.get("participant_ids") or [])]
        return {"event": event, "found": True, "evidence": evidence,
                "participants": participants,
                "state_changes": list(event.get("state_changes", []))}

    def traverse(self, entity_id: str, hops: int = 1) -> dict[str, Any]:
        """Multi-hop relationship walk from one entity (DATA-046).

        One hop follows typed relations and identity links; further hops
        continue through the entities those reach. Returns visited nodes
        and the edges crossed, each edge carrying its relation row or
        link row so every step stays evidence-backed.
        """
        if hops < 1:
            raise ValueError("traversal needs at least one hop")
        self._ensure_relations()
        self._ensure_entities()
        entity_ids = {row.get("id") for row in self._model.get("entities", [])
                      if isinstance(row, dict)}
        if entity_id not in entity_ids:
            return {"nodes": [], "edges": [], "found": False}
        seen = {entity_id}
        frontier = {entity_id}
        edges = []
        for _ in range(hops):
            nxt = set()
            for node in sorted(frontier):
                for relation in self._relations.get(node, []):
                    other = (relation.get("object_id")
                             if relation.get("subject_id") == node
                             else relation.get("subject_id"))
                    edges.append({"from": node, "to": other,
                                  "kind": "relation",
                                  "relation": relation.get("relation_type")
                                  or relation.get("kind"),
                                  "row": relation})
                    if isinstance(other, str) and other not in seen:
                        seen.add(other)
                        nxt.add(other)
                self._ensure_temporal()
                for link in self._model.get("identity_links", []):
                    if not isinstance(link, dict) or link.get("entity_id") != node:
                        continue
                    obs_id = link.get("observation_id")
                    others = (self._obs_entities.get(obs_id, set()) - {node}
                              if isinstance(obs_id, str) else set())
                    for other in sorted(others):
                        edges.append({"from": node, "to": other,
                                      "kind": "identity_link", "row": link})
                        if other not in seen:
                            seen.add(other)
                            nxt.add(other)
            frontier = nxt
            if not frontier:
                break
        nodes = [row for row in self._model.get("entities", [])
                 if isinstance(row, dict) and row.get("id") in seen]
        return {"nodes": nodes, "edges": edges, "found": True}

    def search_text(self, query: str) -> list[dict[str, Any]]:
        """Lexical lookup over observation payloads and events (DATA-049).

        This is a finding aid, not semantic retrieval: every term must
        appear, and hits name the row that carries them.
        """
        terms = _tokens(query)
        if not terms:
            raise ValueError("text search needs at least one token")
        self._ensure_text()
        posting = [self._token_index.get(term, set()) for term in terms]
        hits = set.intersection(*posting) if posting else set()
        self._ensure_events()
        self._ensure_temporal()
        results = []
        for hit in sorted(hits):
            kind, row_id = hit.split(":", 1)
            if kind == "obs" and row_id in self._obs_by_id:
                results.append({"kind": "observation",
                                "row": self._obs_by_id[row_id]})
            elif kind == "event" and row_id in self._events_by_id:
                results.append({"kind": "event",
                                "row": self._events_by_id[row_id]})
        return results

    def observations_overlapping_box(self, clock_id: str, box: list[float],
                                     *, start: int | None = None,
                                     end: int | None = None,
                                     min_iou: float = 0.0) -> list[dict]:
        """Observations on a clock whose payload box overlaps ``box``.

        ``box`` is ``[x, y, w, h]`` in the same normalized space the
        segmentation migration writes. An optional tick window narrows
        the scan before any box math runs (DATA-049 spatial).
        """
        if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
            raise ValueError("spatial query needs a non-empty [x, y, w, h] box")
        self._ensure_spatial()
        rows = self._spatial.get(clock_id, [])
        self._ensure_temporal()
        results = []
        for row_start, row_end, row_box, obs_id in rows:
            if start is not None and row_end <= start:
                continue
            if end is not None and row_start >= end:
                continue
            if _box_iou(box, row_box) >= min_iou and obs_id in self._obs_by_id:
                results.append(self._obs_by_id[obs_id])
        return results

    def answer_requirements(self, requirements: list[dict[str, Any]]
                            ) -> dict[str, Any]:
        """Resolve a question spanning linked observations (DATA-047).

        Each requirement names ``modality`` and/or ``measurement`` plus
        optional ``entity_id``, ``text`` and a tick ``window``. The
        answer carries per-requirement candidates and ``groups``: maximal
        sets with one candidate per requirement whose intervals pairwise
        overlap on one clock, with the union of their evidence ids and
        provenance. Requirements the model cannot meet are named, never
        papered over.
        """
        if not requirements:
            raise ValueError("multi-observation questions need requirements")
        self._ensure_temporal()
        self._ensure_entities()
        per_requirement = []
        for requirement in requirements:
            candidates = self._candidates_for(requirement)
            per_requirement.append({"requirement": requirement,
                                    "candidates": candidates})
        groups = self._link_groups(
            [entry["candidates"] for entry in per_requirement])
        unmet = [i for i, entry in enumerate(per_requirement)
                 if not entry["candidates"]]
        return {"requirements": [
            {"requirement": entry["requirement"],
             "candidate_ids": [obs["id"] for obs in entry["candidates"]]}
            for entry in per_requirement],
            "groups": groups, "unmet": unmet}

    def _candidates_for(self, requirement: dict[str, Any]) -> list[dict]:
        modality = requirement.get("modality")
        measurement = requirement.get("measurement")
        entity_id = requirement.get("entity_id")
        text = requirement.get("text")
        window = requirement.get("window") or {}
        allowed = None
        if entity_id is not None:
            allowed = self._entity_obs.get(entity_id, set())
            allowed = allowed | {
                obs["id"] for event in self._model.get("events", [])
                if isinstance(event, dict)
                and entity_id in (event.get("participant_ids") or [])
                for obs_id in (event.get("evidence_ids") or [])
                for obs in [self._obs_by_id.get(obs_id)]
                if obs is not None}
        wanted_terms = _tokens(text) if isinstance(text, str) else set()
        results = []
        for obs in self._model.get("observations", []):
            if not isinstance(obs, dict) or not obs.get("id"):
                continue
            if modality is not None and obs.get("modality") != modality:
                continue
            if measurement is not None:
                evidence = obs.get("evidence") or {}
                native = ((obs.get("payload") or {}).get("evidence_contract")
                          if isinstance(obs.get("payload"), dict) else None)
                observed_type = (evidence.get("measurement_type")
                                 if isinstance(evidence, dict) else None)
                if observed_type is None and isinstance(native, dict):
                    observed_type = native.get("measurement_type")
                if observed_type != measurement:
                    continue
            if allowed is not None and obs["id"] not in allowed:
                continue
            span = _interval_of(obs)
            if span is None:
                continue
            clock_id, start, end = span
            if (window.get("clock_id") is not None
                    and clock_id != window["clock_id"]):
                continue
            if window.get("start") is not None and end <= window["start"]:
                continue
            if window.get("end") is not None and start >= window["end"]:
                continue
            if wanted_terms:
                haystack = _tokens(" ".join(
                    [str(obs.get("modality", ""))] +
                    _payload_text(obs.get("payload"))))
                if not wanted_terms <= haystack:
                    continue
            results.append(obs)
        return results

    def _link_groups(self, candidate_lists: list[list[dict]]
                     ) -> list[dict[str, Any]]:
        non_empty = [cands for cands in candidate_lists if cands]
        if len(non_empty) != len(candidate_lists):
            return []
        groups = []
        for first in non_empty[0]:
            span = _interval_of(first)
            if span is None:
                continue
            clock_id, lo, hi = span
            chosen = [first]
            for cands in non_empty[1:]:
                match = None
                for obs in cands:
                    other = _interval_of(obs)
                    if other is None or other[0] != clock_id:
                        continue
                    if _overlap(lo, hi, other[1], other[2]) <= 0:
                        continue
                    match = obs
                    lo, hi = max(lo, other[1]), min(hi, other[2])
                    break
                if match is None:
                    break
                chosen.append(match)
            if len(chosen) == len(candidate_lists):
                evidence_ids = sorted({obs["id"] for obs in chosen})
                provenance = sorted({str(obs.get("provenance_id"))
                                     for obs in chosen if obs.get("provenance_id")})
                groups.append({
                    "interval": {"clock_id": clock_id, "start": lo, "end": hi,
                                 "start_s": lo / TICKS_PER_SECOND,
                                 "end_s": hi / TICKS_PER_SECOND},
                    "observation_ids": evidence_ids,
                    "provenance_ids": provenance})
        return groups

    def select(self, collections: list[str] | None = None,
               ids: dict[str, list[str]] | None = None) -> dict[str, Any]:
        """Return a slice carrying only the requested evidence (DATA-048).

        ``collections`` names model collections; ``ids`` narrows to row
        ids within them. The slice keeps the model ``id`` and
        ``schema_version`` so it still reads as a model fragment, and it
        records exactly what was selected.
        """
        available = {key for key, value in self._model.items()
                     if isinstance(value, list)}
        wanted = set(collections) if collections else set(available)
        unknown = wanted - available - {"id", "schema_version"}
        if unknown:
            raise ValueError(f"unknown evidence collections: {sorted(unknown)}")
        narrowed = dict(ids or {})
        for key, values in narrowed.items():
            if key not in available:
                raise ValueError(f"cannot narrow unknown collection {key!r}")
            if not isinstance(values, list):
                raise ValueError(f"id narrow for {key!r} must be a list")
        fragment: dict[str, Any] = {"id": self._model.get("id"),
                                    "schema_version": self._model.get(
                                        "schema_version"),
                                    "selected": sorted(wanted & available)}
        for key in wanted & available:
            rows = self._model.get(key, [])
            keep = set(narrowed.get(key, []))
            fragment[key] = [row for row in rows
                             if not keep or (isinstance(row, dict)
                                             and row.get("id") in keep)]
        return fragment


def find_contradictions(model: dict[str, Any],
                          scope: dict[str, Any] | None = None
                          ) -> list[dict[str, Any]]:
    """Detect contradictory speaker, object and event assertions (DATA-038).

    Three detectors plus recorded history, all over well-defined rows:

    - ``recorded``: identity links that already name
      ``contradicting_observation_ids`` are surfaced verbatim.
    - ``speaker``: two speech observations sharing a clock whose
      attributed speaker entities are disjoint and non-empty, with
      enough shared ticks to rule out boundary jitter.
    - ``object``: two observations on one asset and clock whose
      payload boxes agree spatially (IoU) but assert different object
      identities over a shared span.
    - ``event_duplicate``: two verified events of one kind sharing a
      participant and overlapping span on disjoint evidence, where at
      least one of them is wrong or both describe one occurrence twice.

    Everything returns ``needs_review``: detection routes to targeted
    verification, it never settles the assertion itself. ``scope`` is an
    optional ``{"clock_id", "start", "end"}`` window: rows outside it are
    never loaded into the detectors, so a per-query check pays for the
    queried span, not the whole model.
    """
    index = model if isinstance(model, EvidenceIndex) else EvidenceIndex(model)
    window = _checked_scope(scope)
    found = []
    found.extend(_recorded_contradictions(index.model, window))
    found.extend(_speaker_contradictions(index, window))
    found.extend(_object_contradictions(index, window))
    found.extend(_event_duplicates(index, window))
    return found


def _checked_scope(scope: dict[str, Any] | None
                   ) -> tuple[str, int, int] | None:
    if scope is None:
        return None
    clock_id, start, end = (scope.get("clock_id"), scope.get("start"),
                            scope.get("end"))
    if (not isinstance(clock_id, str) or not isinstance(start, int)
            or not isinstance(end, int) or isinstance(start, bool)
            or isinstance(end, bool) or end <= start):
        raise ValueError("contradiction scope needs a clock and a non-empty window")
    return clock_id, start, end


def _in_scope(span: tuple[str, int, int] | None,
              window: tuple[str, int, int] | None) -> bool:
    return (span is not None
            and (window is None or (span[0] == window[0]
                                    and span[1] < window[2]
                                    and span[2] > window[1])))


def _recorded_contradictions(model: dict[str, Any],
                              window: tuple[str, int, int] | None) -> list[dict]:
    spans = {}
    for obs in model.get("observations", []):
        if isinstance(obs, dict) and isinstance(obs.get("id"), str):
            spans[obs["id"]] = _interval_of(obs)
    found = []
    for link in model.get("identity_links", []):
        if not isinstance(link, dict):
            continue
        contradicting = link.get("contradicting_observation_ids") or []
        if not contradicting:
            continue
        named = [link.get("observation_id"), *contradicting]
        if window is not None and not any(
                _in_scope(spans.get(obs_id), window) for obs_id in named):
            continue
        found.append({"kind": "recorded", "status": "needs_review",
                          "entity_id": link.get("entity_id"),
                          "observation_id": link.get("observation_id"),
                          "contradicting_observation_ids": list(contradicting),
                          "link_id": link.get("id")})
    return found


def _speaker_sets(index: EvidenceIndex) -> dict[str, set[str]]:
    index._ensure_entities()
    speakers = {row.get("id") for row in index.model.get("entities", [])
                if isinstance(row, dict) and row.get("kind") == "speaker"}
    attributed: dict[str, set[str]] = {}
    for obs_id, entities in (index._obs_entities or {}).items():
        named = set(entities) & speakers
        if named:
            attributed[obs_id] = named
    return attributed


def _speaker_contradictions(index: EvidenceIndex,
                              window: tuple[str, int, int] | None) -> list[dict]:
    index._ensure_temporal()
    attributed = _speaker_sets(index)
    speech = [obs for obs in index.model.get("observations", [])
              if isinstance(obs, dict) and obs.get("modality") in SPEECH_MODALITIES
              and obs.get("id") in attributed
              and _in_scope(_interval_of(obs), window)]
    by_clock: dict[str, list[dict]] = defaultdict(list)
    for obs in speech:
        span = _interval_of(obs)
        if span is not None:
            by_clock[span[0]].append(obs)
    found = []
    for clock_id, rows in by_clock.items():
        ordered = sorted(rows, key=lambda obs: obs["interval"]["start"])
        for pos, first in enumerate(ordered):
            for second in ordered[pos + 1:]:
                if second["interval"]["start"] >= first["interval"]["end"]:
                    break
                shared = _overlap(first["interval"]["start"], first["interval"]["end"],
                                  second["interval"]["start"], second["interval"]["end"])
                if shared < SPEAKER_MIN_OVERLAP_TICKS:
                    continue
                first_who = attributed[first["id"]]
                second_who = attributed[second["id"]]
                if not first_who or not second_who or first_who == second_who:
                    continue
                lane = MODALITY_TO_LANE.get(
                    second.get("modality"), MODALITY_TO_LANE.get(first.get("modality")))
                found.append({
                    "kind": "speaker", "status": "needs_review",
                    "clock_id": clock_id,
                    "interval": {"start": max(first["interval"]["start"],
                                              second["interval"]["start"]),
                                 "end": min(first["interval"]["end"],
                                            second["interval"]["end"])},
                    "observation_ids": sorted([first["id"], second["id"]]),
                    "attributions": {first["id"]: sorted(first_who),
                                     second["id"]: sorted(second_who)},
                    "shared_ticks": shared, "lane": lane})
    return found


def _object_contradictions(index: EvidenceIndex,
                             window: tuple[str, int, int] | None) -> list[dict]:
    index._ensure_temporal()
    boxed: dict[tuple[str, str | None], list] = defaultdict(list)
    for obs in index.model.get("observations", []):
        if not isinstance(obs, dict):
            continue
        payload = obs.get("payload")
        if not isinstance(payload, dict):
            continue
        identity = _identity_of(payload)
        box = _box_of(payload)
        span = _interval_of(obs)
        if identity is None or box is None or not _in_scope(span, window):
            continue
        boxed[(span[0], obs.get("asset_id"))].append((obs, identity, box, span))
    found = []
    for (clock_id, _), rows in boxed.items():
        rows.sort(key=lambda row: row[3][1])
        for pos, (first, first_id, first_box, (_, f_lo, f_hi)) in enumerate(rows):
            for second, second_id, second_box, (_, s_lo, s_hi) in rows[pos + 1:]:
                if s_lo >= f_hi:
                    break
                if first_id == second_id:
                    continue
                shared = _overlap(f_lo, f_hi, s_lo, s_hi)
                shorter = min(f_hi - f_lo, s_hi - s_lo)
                if shared < ATTRIBUTE_MIN_OVERLAP_FRACTION * shorter:
                    continue
                if _box_iou(first_box, second_box) < OBJECT_MIN_IOU:
                    continue
                found.append({
                    "kind": "object", "status": "needs_review",
                    "clock_id": clock_id, "asset_id": first.get("asset_id"),
                    "interval": {"start": max(f_lo, s_lo), "end": min(f_hi, s_hi)},
                    "observation_ids": sorted([first["id"], second["id"]]),
                    "identities": {first["id"]: first_id, second["id"]: second_id},
                    "shared_ticks": shared, "lane": "segmentation"})
    return found


def _event_duplicates(index: EvidenceIndex,
                        window: tuple[str, int, int] | None) -> list[dict]:
    index._ensure_events()
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for event in index.model.get("events", []):
        if not isinstance(event, dict) or event.get("status") != "verified":
            continue
        span = _interval_of(event)
        if not _in_scope(span, window) or not isinstance(event.get("kind"), str):
            continue
        buckets[(span[0], event["kind"])].append(event)
    found = []
    for (clock_id, kind), rows in buckets.items():
        rows.sort(key=lambda event: event["interval"]["start"])
        for pos, first in enumerate(rows):
            f_lo, f_hi = first["interval"]["start"], first["interval"]["end"]
            for second in rows[pos + 1:]:
                s_lo, s_hi = (second["interval"]["start"],
                              second["interval"]["end"])
                if s_lo >= f_hi:
                    break
                shared = _overlap(f_lo, f_hi, s_lo, s_hi)
                shorter = min(f_hi - f_lo, s_hi - s_lo)
                if shared < ATTRIBUTE_MIN_OVERLAP_FRACTION * shorter:
                    continue
                shared_participants = (set(first.get("participant_ids", []))
                                       & set(second.get("participant_ids", [])))
                if not shared_participants:
                    continue
                if set(first.get("evidence_ids", [])) & set(
                        second.get("evidence_ids", [])):
                    continue
                found.append({
                    "kind": "event_duplicate", "status": "needs_review",
                    "clock_id": clock_id,
                    "interval": {"start": max(f_lo, s_lo),
                                 "end": min(f_hi, s_hi)},
                    "event_ids": sorted([first["id"], second["id"]]),
                    "event_kind": kind,
                    "participant_ids": sorted(shared_participants),
                    "shared_ticks": shared, "lane": "m7"})
    return found


def verification_request(contradiction: dict[str, Any]) -> dict[str, Any]:
    """Route one contradiction to targeted verification (DATA-039).

    The request carries ``resolvable_ambiguities`` rows plus the lane
    and scope a caller needs for
    ``query_directed_acquisition.plan_query_acquisition``: the
    acquisition planner owns budgets and execution, this module only
    says what disagrees, where, and which lane could settle it.
    """
    interval = contradiction.get("interval") or {}
    start = interval.get("start", 0) / TICKS_PER_SECOND
    end = interval.get("end", 0) / TICKS_PER_SECOND
    lane = contradiction.get("lane")
    lanes = [lane] if isinstance(lane, str) and lane else []
    return {
        "schema_version": 1,
        "contradiction_kind": contradiction.get("kind"),
        "status": "needs_review",
        "clock_id": contradiction.get("clock_id"),
        "interval_s": {"start_s": start, "end_s": end},
        "evidence_ids": list(contradiction.get("observation_ids")
                             or contradiction.get("event_ids") or []),
        "required_lanes": lanes,
        "resolvable_ambiguities": [
            {"lane": lane, "start_s": start, "end_s": end} for lane in lanes],
        "scope": {"start_s": start, "end_s": end},
    }


def classify_absence(status: str, *, has_explicit_negative: bool,
                     coverage_complete: bool) -> dict[str, Any]:
    """Compatibility export for the query layer's DATA-036 vocabulary."""
    return _classify_absence(
        status, has_explicit_negative=has_explicit_negative,
        coverage_complete=coverage_complete)


def answer_uncertainty(results: list[dict[str, Any]],
                       *, contradictions: int = 0) -> dict[str, Any]:
    """Decompose what an answer does and does not establish (DATA-042).

    Components are counted, never modelled: the fraction of results on
    fresh complete coverage, the fraction carrying calibrated
    confidence, the worst clock error, and open contradictions touching
    the answer. ``summary`` is a documented rank for sorting answers,
    explicitly not a calibrated probability (DATA-041): it multiplies
    the coverage, freshness and agreement fractions and discounts by
    clock error and contradictions.
    """
    total = len(results)
    if total == 0:
        return {"results": 0, "coverage_fraction": 0.0,
                "freshness_fraction": 0.0, "calibrated_fraction": 0.0,
                "worst_clock_error_ticks": 0, "contradictions": contradictions,
                "summary": 0.0,
                "reading": "no results carry no certainty either way"}
    covered = sum(1 for row in results
                  if "complete" in {str(c).lower() for c in row.get("coverage", [])}
                  or row.get("coverage") == "complete")
    fresh = sum(1 for row in results
                if "fresh" in {str(f).lower() for f in row.get("freshness", [])}
                or row.get("freshness") == "fresh")
    calibrated = sum(1 for row in results
                     if row.get("confidence_kind") == "calibrated_probability")
    worst_error = max([int(row.get("clock_error_ticks", 0) or 0)
                       for row in results] + [0])
    coverage_fraction = covered / total
    freshness_fraction = fresh / total
    agreement = 1.0 / (1.0 + contradictions)
    clock_discount = 1.0 / (1.0 + worst_error / TICKS_PER_SECOND)
    summary = round(coverage_fraction * freshness_fraction
                    * agreement * clock_discount, 4)
    return {"results": total, "coverage_fraction": round(coverage_fraction, 4),
            "freshness_fraction": round(freshness_fraction, 4),
            "calibrated_fraction": round(calibrated / total, 4),
            "worst_clock_error_ticks": worst_error,
            "contradictions": contradictions, "summary": summary,
            "reading": ("share of results on fresh complete coverage, "
                        "discounted by clock error and open contradictions")}


def _confidence_rows(model: dict[str, Any]) -> list[tuple[str, dict]]:
    """Every row carrying a confidence: observations, identity links,
    events and track samples, each tagged with where it was found."""
    rows = []
    for collection in ("observations", "identity_links", "events"):
        for row in model.get(collection, []) or []:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                rows.append((collection, row))
    for track in model.get("tracks", []) or []:
        if not isinstance(track, dict):
            continue
        for pos, sample in enumerate(track.get("samples", []) or []):
            if isinstance(sample, dict):
                rows.append((f"tracks[{track.get('id')}].samples[{pos}]", sample))
    return rows


def audit_confidence_loss(model: dict[str, Any]) -> dict[str, Any]:
    """Report where raw scores lost their typed meaning (DATA-041).

    A row with a ``confidence`` float but no ``confidence_detail`` was
    collapsed in migration: its kind (rank, logit, cosine, raw_score)
    and producer are gone, so no reader can tell a ranking score from
    a probability. Rows WITH detail split into ``calibrated`` (a
    probability with a calibration version, the only kind that reads
    as one) and ``raw`` (every other honest kind). The report names
    ids per bucket so the migration fix can be verified row by row:
    this audit reads empty exactly when every scored row carries its
    type. It never recalibrates anything itself.
    """
    if not isinstance(model, dict):
        raise ValueError("confidence audit reads a Project Model dict")
    untyped: list[str] = []
    calibrated: list[str] = []
    raw: dict[str, list[str]] = {}
    for where, row in _confidence_rows(model):
        if row.get("confidence") is None:
            continue
        label = row.get("id", where) if isinstance(row.get("id"), str) else where
        detail = row.get("confidence_detail")
        if not isinstance(detail, dict):
            untyped.append(str(label))
            continue
        kind = detail.get("kind")
        if kind == "calibrated_probability":
            calibrated.append(str(label))
        else:
            raw.setdefault(str(kind), []).append(str(label))
    return {"untyped": sorted(untyped), "calibrated": sorted(calibrated),
            "raw": {kind: sorted(ids) for kind, ids in sorted(raw.items())},
            "reading": ("rows with a score but no type cannot be read "
                        "as probabilities")}
