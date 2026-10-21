#!/usr/bin/env python3
"""The labelled event-model questions: ordinal, before, after and while.

The measurement VI-006 asks for: a query about the SECOND time, and
queries about what happened BEFORE, AFTER and WHILE it, resolved on
EVENT EVIDENCE rather than on how two sentences look alike.

The corpus is ``tests/fixtures/vi006_event_model.json`` - a labelled
fixture of one podcast clip in which a laptop is opened twice.  Every
event carries the participants it names, the evidence that measured it
and (for the openings) the lid's own transition: ``closed -> opening
-> open``.

Usage::

    python3 docs/benchmarks/vi006_event_model_bench.py [--json]

The bench reads a fixture and writes nothing.  It reports, with the
denominator beside every number:

* ``queries`` - the labelled questions, and how many resolved;
* ``anchor`` - how many resolved to the RIGHT event;
* ``related`` - how many named the RIGHT set of related events;
* ``state_chain`` - the values the laptop's lid took, in order.

A question that cannot be resolved counts as UNRESOLVED.  It is never
counted as a wrong answer, and never counted as right.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import temporal_events, temporal_query
from library.tools.temporal_events import (
    Boundary,
    StateChange,
    TemporalEvent,
    TemporalEventIndex,
)

FIXTURE = (Path(__file__).resolve().parents[2] / "tests" / "fixtures"
           / "vi006_event_model.json")


def load_fixture(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def build_events(fixture: dict) -> list[TemporalEvent]:
    """The fixture's events, as the typed surface's own records."""
    clip_id = fixture["clip_id"]
    clock_id = fixture["clock_id"]
    entities = fixture.get("entities") or {}
    events: list[TemporalEvent] = []
    for row in fixture["events"]:
        subjects = tuple(row.get("subjects") or ())
        objects = tuple(row.get("objects") or ())
        changes = tuple(
            StateChange(entity_id=str(change.get("entity_id") or ""),
                        property=str(change.get("property") or ""),
                        before=change.get("from"),
                        after=change.get("to"),
                        evidence_ids=tuple(change.get("evidence_ids") or ()),
                        certainty=str(change.get("certainty") or "measured"))
            for change in (row.get("state_changes") or []))
        events.append(TemporalEvent(
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            action=row.get("action"),
            action_text=str(row.get("action_text") or ""),
            asset_id=f"clip:{clip_id}",
            clip_id=clip_id,
            source_file=None,
            clock_id=clock_id,
            start_seconds=float(row["start_seconds"]),
            end_seconds=float(row["end_seconds"]),
            onset=Boundary(float(row["start_seconds"]), "estimated", 0.05,
                           "the fixture's own edge"),
            intermediate_states=(),
            completion=Boundary(float(row["end_seconds"]), "estimated", 0.05,
                                "the fixture's own edge"),
            subjects=subjects,
            subject_labels=tuple(str((entities.get(sid) or {}).get("label")
                                      or "") for sid in subjects),
            objects=objects,
            object_labels=tuple(str((entities.get(oid) or {}).get("label")
                                    or "") for oid in objects),
            participant_ids=tuple(row.get("participant_ids") or ()),
            observed_participant_ids=tuple(
                row.get("observed_participant_ids") or ()),
            inferred_participant_ids=tuple(
                row.get("inferred_participant_ids") or ()),
            evidence_ids=tuple(row.get("evidence_ids") or ()),
            status=str(row.get("status") or "candidate"),
            certainty=str(row.get("certainty") or "inferred"),
            state_changes=changes,
        ))
    return events


def build_index(fixture: dict) -> TemporalEventIndex:
    events = temporal_events.assign_occurrences(build_events(fixture))
    return TemporalEventIndex(events=events)


def evaluate(index: TemporalEventIndex, fixture: dict) -> dict:
    """Every labelled question, and how it was answered."""
    rows = []
    for labelled in fixture["queries"]:
        query = labelled["query"]
        relation = labelled.get("relation")
        expected_anchor = str(labelled.get("anchor_event_id") or "")
        expected_related = set(labelled.get("expected_event_ids") or ())
        answer = temporal_query.answer(index, query)
        relational = answer.get("relational") or {}
        if relation is None:
            resolved = answer.get("resolution") or {}
            anchor_id = str((resolved.get("event") or {}).get("event_id")
                            or "")
            related = {row["event_id"] for row in answer.get("events") or []}
            status = resolved.get("status")
        else:
            status = relational.get("status")
            anchor_id = str((relational.get("anchor_event") or {}).get(
                "event_id") or "")
            related = {row["event_id"] for row in relational.get("events") or []}
        rows.append({
            "query": query,
            "relation": relation,
            "status": status,
            "resolved": status == "resolved",
            "anchor_event_id": anchor_id,
            "anchor_correct": anchor_id == expected_anchor,
            "related_event_ids": sorted(related),
            "related_expected": sorted(expected_related),
            "related_correct": related == expected_related,
            "reasons": list(relational.get("reasons") or []),
            "note": labelled.get("note"),
        })
    return {"queries": rows}


def evaluate_negatives(index: TemporalEventIndex, fixture: dict) -> list[dict]:
    """The questions that must NOT match an event they read like.

    A closing of the same lid reads like an opening of it.  Matching
    on how the two sentences LOOK would answer "she opens the lid"
    with the closing; matching on the event's own transition does not.
    """
    rows = []
    for labelled in fixture.get("negatives") or []:
        found = temporal_query.search_events(index, labelled["query"])
        matched = {row["event_id"] for row in found["events"]}
        forbidden = set(labelled.get("must_not_match") or ())
        rows.append({
            "query": labelled["query"],
            "matched": sorted(matched),
            "must_not_match": sorted(forbidden),
            "correct": not (matched & forbidden),
            "note": labelled.get("note"),
        })
    return rows


def measure_transition(index: TemporalEventIndex, fixture: dict) -> dict:
    """The state chain the fixture's lid transition makes."""
    wanted = fixture.get("state_transition") or {}
    entity_id = str(wanted.get("entity_id") or "")
    prop = str(wanted.get("property") or "")
    history = temporal_events.state_history(index.events, entity_id, prop)
    states = history[0]["states"] if history else []
    expected = list(wanted.get("expected_states") or ())
    return {
        "entity_id": entity_id,
        "property": prop,
        "states": states,
        "expected_states": expected,
        "correct": states == expected,
        "transitions": len(temporal_events.state_transitions(
            index.events, entity_id, prop)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score the labelled event-model questions")
    parser.add_argument("--fixture", default=str(FIXTURE))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    fixture = load_fixture(Path(args.fixture))
    index = build_index(fixture)
    scored = evaluate(index, fixture)
    negatives = evaluate_negatives(index, fixture)
    transition = measure_transition(index, fixture)
    rows = scored["queries"]
    total = len(rows)
    resolved = sum(1 for row in rows if row["resolved"])
    anchors = sum(1 for row in rows if row["anchor_correct"])
    related = sum(1 for row in rows if row["related_correct"])
    negatives_ok = sum(1 for row in negatives if row["correct"])

    report = {
        "fixture": Path(args.fixture).name,
        "events": len(index.events),
        "queries": total,
        "resolved": resolved,
        "anchor_correct": anchors,
        "related_correct": related,
        "negatives": negatives,
        "negatives_correct": negatives_ok,
        "state_chain": transition,
        "rows": rows,
    }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"fixture: {report['fixture']} ({report['events']} events)")
    print(f"queries: {total}   resolved: {resolved}/{total}   "
          f"anchor right: {anchors}/{total}   "
          f"related right: {related}/{total}")
    print()
    for row in rows:
        mark = "ok " if (row["resolved"] and row["anchor_correct"]
                         and row["related_correct"]) else "NO "
        print(f"  {mark}[{row['relation'] or 'ordinal'}] {row['query']}")
        print(f"      status={row['status']} anchor={row['anchor_event_id']}")
        if not row["related_correct"]:
            print(f"      related={row['related_event_ids']}")
            print(f"      expected={row['related_expected']}")
        for reason in row["reasons"]:
            print(f"      reason: {reason}")
    print()
    chain = transition
    print(f"state chain [{chain['entity_id']}.{chain['property']}]: "
          f"{' -> '.join(str(state) for state in chain['states'])}")
    print(f"  transitions: {chain['transitions']}   "
          f"correct: {chain['correct']}")
    print()
    for row in negatives:
        mark = "ok " if row["correct"] else "NO "
        print(f"  {mark}[negative] {row['query']}")
        print(f"      matched: {row['matched']}")
        print(f"      must not match: {row['must_not_match']}")
    return 0 if (related == total and chain["correct"]
                 and negatives_ok == len(negatives)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
