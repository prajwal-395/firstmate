#!/usr/bin/env python3
"""The SAME corpus, scored against any checkout of the engine.

The before/after column of ``docs/benchmarks/2026-10-22-vi006-event-model.md``
is reproducible with this script: it builds the fixture's events with the
fields BOTH checkouts understand, asks each labelled question through the
public answer surface, and prints the counts.

Usage::

    python3 docs/benchmarks/vi006_event_model_baseline.py [repo-root]

With no argument it scores the checkout it runs in.  To score the base
commit::

    git archive 20c3988f3 | tar -x -C /tmp/vi006_base
    python3 docs/benchmarks/vi006_event_model_baseline.py /tmp/vi006_base

A question the surface cannot answer counts as UNRESOLVED - never as a
wrong answer, and never as a right one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(
    __file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from library.tools import temporal_events, temporal_query
from library.tools.temporal_events import (
    Boundary,
    StateChange,
    TemporalEvent,
    TemporalEventIndex,
)

FIXTURE = (Path(__file__).resolve().parents[2] / "tests" / "fixtures"
           / "vi006_event_model.json")


def build_index(fixture: dict) -> TemporalEventIndex:
    clip_id = fixture["clip_id"]
    clock_id = fixture["clock_id"]
    entities = fixture.get("entities") or {}
    events = []
    for row in fixture["events"]:
        subjects = tuple(row.get("subjects") or ())
        objects = tuple(row.get("objects") or ())
        events.append(TemporalEvent(
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            action=row.get("action"),
            action_text=str(row.get("action_text") or ""),
            asset_id=f"clip:{clip_id}", clip_id=clip_id, source_file=None,
            clock_id=clock_id,
            start_seconds=float(row["start_seconds"]),
            end_seconds=float(row["end_seconds"]),
            onset=Boundary(float(row["start_seconds"]), "estimated", 0.05,
                           "the fixture's own edge"),
            intermediate_states=(),
            completion=Boundary(float(row["end_seconds"]), "estimated", 0.05,
                                "the fixture's own edge"),
            subjects=subjects,
            subject_labels=tuple(str((entities.get(s) or {}).get("label") or "")
                                 for s in subjects),
            objects=objects,
            object_labels=tuple(str((entities.get(o) or {}).get("label") or "")
                                for o in objects),
            evidence_ids=tuple(row.get("evidence_ids") or ()),
            status=str(row.get("status") or "candidate"),
            certainty=str(row.get("certainty") or "inferred"),
            state_changes=tuple(
                StateChange(entity_id=str(change.get("entity_id") or ""),
                            property=str(change.get("property") or ""),
                            before=change.get("from"),
                            after=change.get("to"),
                            evidence_ids=tuple(change.get("evidence_ids") or ()))
                for change in (row.get("state_changes") or [])),
        ))
    return TemporalEventIndex(
        events=temporal_events.assign_occurrences(events))


def main() -> int:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    index = build_index(fixture)
    total = resolved = anchor_ok = related_ok = 0
    for labelled in fixture["queries"]:
        relation = labelled.get("relation")
        expected_anchor = labelled["anchor_event_id"]
        expected_related = set(labelled.get("expected_event_ids") or ())
        answer = temporal_query.answer(index, labelled["query"])
        relational = answer.get("relational") or {}
        if relation is None:
            resolution = answer.get("resolution") or {}
            status = resolution.get("status")
            anchor = str((resolution.get("event") or {}).get("event_id") or "")
            related = {row["event_id"] for row in answer.get("events") or []}
        else:
            status = relational.get("status")
            anchor = str((relational.get("anchor_event") or {}).get(
                "event_id") or "")
            related = {row["event_id"] for row in relational.get("events") or []}
        total += 1
        resolved += 1 if status == "resolved" else 0
        anchor_ok += 1 if anchor == expected_anchor else 0
        related_ok += 1 if related == expected_related else 0
        print(f"  [{relation or 'ordinal'}] {labelled['query']}")
        print(f"      status={status} anchor_ok={anchor == expected_anchor} "
              f"related_ok={related == expected_related}")
    print(f"checkout: {REPO}")
    print(f"queries: {total}  resolved: {resolved}/{total}  "
          f"anchor right: {anchor_ok}/{total}  "
          f"related right: {related_ok}/{total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
