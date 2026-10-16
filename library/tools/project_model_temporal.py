"""Interval-first retrieval over canonical Project Model events.

The one-second M9 world model is a convenient derived lookup view. This
module queries the Project Model's source-clock intervals directly, so
variable-length events, overlaps, boundary uncertainty and source evidence
remain available to callers.
"""
from __future__ import annotations

from typing import Any


def retrieve_events(model: dict[str, Any], interval: dict[str, Any], *,
                    kind_prefix: str | None = None) -> list[dict[str, Any]]:
    """Return overlapping events coarse-to-fine with ancestors and evidence.

    Results keep each event's original interval untouched. Multiple parent
    links are retained in the ancestor list; overlapping sibling events are
    returned independently rather than collapsed into a chunk.
    """
    clock_id = interval["clock_id"]
    start, end = interval["start"], interval["end"]
    if not isinstance(clock_id, str) or not clock_id or start >= end:
        raise ValueError("query interval must be a non-empty named-clock interval")

    events = model["events"]
    by_id = {event["id"]: event for event in events}
    observations = {observation["id"]: observation
                    for observation in model["observations"]}
    matches = [event for event in events
               if event["interval"]["clock_id"] == clock_id
               and event["interval"]["start"] < end
               and event["interval"]["end"] > start
               and (kind_prefix is None or event["kind"].startswith(kind_prefix))]
    matches.sort(key=lambda event: (
        event["interval"]["start"],
        -(event["interval"]["end"] - event["interval"]["start"]),
        event["id"],
    ))

    def ancestors(event: dict[str, Any]) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        pending = list(event.get("parent_event_ids", []))
        while pending:
            parent_id = pending.pop()
            if parent_id in found:
                continue
            parent = by_id[parent_id]
            found[parent_id] = parent
            pending.extend(parent.get("parent_event_ids", []))
        return sorted(found.values(), key=lambda parent: (
            -(parent["interval"]["end"] - parent["interval"]["start"]),
            parent["interval"]["start"], parent["id"],
        ))

    results = []
    for event in matches:
        lineage = [*ancestors(event), event]
        source_evidence = [observations[evidence_id]
                           for evidence_id in event["evidence_ids"]
                           if evidence_id in observations]
        results.append({"event": event, "lineage": lineage,
                        "source_evidence": source_evidence})
    return results
