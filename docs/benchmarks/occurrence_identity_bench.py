"""M0-L1: measure the occurrence axis.

    python3 docs/benchmarks/occurrence_identity_bench.py

Three numbers, over the frozen corpus in
``2026-10-21-occurrence-identity-fixtures.json``:

- **repeat-event recall** - the share of repeated instances that the
  canonical model numbers at all;
- **occurrence-index exactness** - the share whose number is the
  occurrence's true time order inside its class and subject;
- **index stability across a re-measure** - the share whose occurrence
  key is unchanged when the lane rewrites the records and their ids.

It also measures the LIVE model: the Project Model the migration
builds for a Ren-owned scratch project, and what the query verb answers
for it. When the event lane never ran on that project, the live section
says so with the lane's name - it does not report zero as a score.

Stdlib plus the engine's own modules; nothing is written outside the
repository, and no footage is touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.tools import evidence_query, occurrence_identity

FIXTURES = REPO_ROOT / "docs/benchmarks/2026-10-21-occurrence-identity-fixtures.json"
TICKS_PER_SECOND = 1000


def _observation(obs_id, asset_id, clock_id, klass, start_s, end_s):
    start = int(start_s * TICKS_PER_SECOND)
    end = int(end_s * TICKS_PER_SECOND)
    return {"id": obs_id, "asset_id": asset_id,
            "interval": {"clock_id": clock_id, "start": start, "end": end},
            "modality": "event_candidate",
            "payload": {"predicate": klass, "face_track": "face-1",
                        "verdicts": [], "verified": True},
            "confidence": None, "provenance_id": "prov-fixture-m7"}


def _event(event_id, klass, subject_id, asset_id, clock_id, start_s, end_s,
           observation_id):
    return {"id": event_id, "kind": klass,
            "interval": {"clock_id": clock_id,
                         "start": int(start_s * TICKS_PER_SECOND),
                         "end": int(end_s * TICKS_PER_SECOND)},
            "participant_ids": [subject_id] if subject_id else [],
            "status": "verified", "certainty": "inferred",
            "evidence_ids": [observation_id]}


def _model_for(fixture):
    """A Project Model holding the fixture's occurrences."""
    asset_id = fixture["asset_id"]
    clock_id = fixture["clock_id"]
    subject_id = fixture["subject_id"]
    klass = fixture["class"]
    events, observations = [], []
    series = [{"asset_id": asset_id, "clock_id": clock_id,
               "class": klass, "occurrences": fixture["occurrences"]}]
    for other in fixture.get("other_series", []):
        series.append({"asset_id": asset_id, "clock_id": clock_id,
                       "class": other["class"],
                       "occurrences": other["occurrences"]})
    other_source = fixture.get("other_source")
    if other_source:
        series.append({"asset_id": other_source["asset_id"],
                       "clock_id": other_source["clock_id"],
                       "class": klass,
                       "occurrences": other_source["occurrences"]})
    counter = 0
    for entry in series:
        for occurrence in entry["occurrences"]:
            counter += 1
            obs_id = f"obs-fixture-{counter:04d}"
            observations.append(_observation(
                obs_id, entry["asset_id"], entry["clock_id"], entry["class"],
                occurrence["start_s"], occurrence["end_s"]))
            events.append(_event(
                f"event-{counter:04d}", entry["class"], subject_id,
                entry["asset_id"], entry["clock_id"], occurrence["start_s"],
                occurrence["end_s"], obs_id))
    assets, clocks = {}, {}
    for entry in series:
        assets.setdefault(entry["asset_id"], entry["asset_id"])
        clocks.setdefault(entry["clock_id"], entry["asset_id"])
    model = {
        "id": f"project-{fixture['id']}", "schema_version": 1,
        "timebase_policy": "integer_ticks",
        "clocks": [{"id": clock_id, "ticks_per_second": TICKS_PER_SECOND,
                    "domain": {"kind": "source", "asset_id": asset_id,
                               "stream_id": f"stream-{asset_id}"},
                    "frame_rate": {"numerator": 30, "denominator": 1}}
                   for clock_id, asset_id in sorted(clocks.items())],
        "assets": [{"id": asset_id, "kind": "video", "uri": "fixture.mov",
                    "content_digest": f"digest-{asset_id}", "metadata": {},
                    "provenance": []}
                   for asset_id in sorted(assets)],
        "streams": [{"id": f"stream-{asset_id}", "asset_id": asset_id,
                     "clock_id": clock_id, "source_digest": f"digest-{asset_id}",
                     "stream_index": 0, "metadata": {}}
                    for clock_id, asset_id in sorted(clocks.items())],
        "entities": [{"id": subject_id, "kind": "person", "label": subject_id,
                      "object_class_id": None, "identity_scope": "project"}],
        "observations": observations, "events": events,
        "identity_links": [], "tracks": [], "relations": [], "provenance": [],
        "clip_uses": [], "edits": [], "effects": [], "renders": [],
        "frame_maps": [], "nle_bindings": [], "receipts": [], "signals": [],
        "masks": [], "object_classes": [], "intents": [],
        "timeline_states": [], "evaluation_policies": [],
        "interpretations": [], "time_transforms": [],
        "coordinate_spaces": [], "space_transforms": [], "query_results": [],
    }
    return model


def _remeasure(model):
    """The same source, measured again: new record ids, same moments."""
    again = json.loads(json.dumps(model))
    for observation in again["observations"]:
        observation["id"] = observation["id"].replace("obs-", "obs-r2-")
    for event in again["events"]:
        event["id"] = event["id"].replace("event-", "event-r2-")
        event["evidence_ids"] = [eid.replace("obs-", "obs-r2-")
                                 for eid in event["evidence_ids"]]
    return again


def measure_fixture(fixture) -> dict:
    """Measure one fixture: recall, exactness, stability, and the query."""
    model = _model_for(fixture)
    occurrence_identity.assign_occurrences(model)
    expected = {}
    for event in model["events"]:
        occurrence = event.get("occurrence") or {}
        key = (occurrence.get("class"), occurrence.get("subject_id"),
               occurrence.get("index"))
        expected[key] = event["id"]

    instances = 0
    numbered = 0
    exact = 0
    for event in model["events"]:
        instances += 1
        occurrence = event.get("occurrence")
        if not occurrence:
            continue
        numbered += 1
        if occurrence.get("index") == _expected_index(model, event):
            exact += 1

    again = _remeasure(model)
    occurrence_identity.assign_occurrences(again)
    keys_before = {event["id"]: occurrence_identity.occurrence_key(model, event)
                   for event in model["events"]}
    keys_after = {event["id"]: occurrence_identity.occurrence_key(again, event)
                  for event in again["events"]}
    stable = 0
    for event_id, key in keys_before.items():
        other = event_id.replace("event-", "event-r2-")
        if keys_after.get(other) == key:
            stable += 1

    queried = _query(model, fixture)
    return {
        "fixture": fixture["id"],
        "instances": instances,
        "numbered": numbered,
        "recall": round(numbered / instances, 6) if instances else None,
        "exact": exact,
        "exactness": round(exact / instances, 6) if instances else None,
        "stable": stable,
        "stability": round(stable / instances, 6) if instances else None,
        "query": queried,
    }


def _expected_index(model, event) -> int:
    """The occurrence's true time order inside its class and subject."""
    siblings = occurrence_identity.events_of_class(
        model, event["kind"],
        subject_id=occurrence_identity.subject_of(event),
        asset_id=occurrence_identity.asset_of(model, event))
    for index, sibling in enumerate(siblings, start=1):
        if sibling["id"] == event["id"]:
            return index
    return 0


def _query(model, fixture) -> dict:
    """What the query verb answers for each occurrence of the series.

    A series that lives in two sources is asked twice: unscoped, which
    must be REFUSED (the second occurrence is a different moment in
    each take), and scoped to one source, which must answer.
    """
    out = {"asked": 0, "answered": 0, "right": 0, "notes": [],
           "scoped": None}
    for nth in range(1, 6):
        plan = evidence_query.compile_plan([
            {"predicate": "nth_event", "event_class": fixture["class"],
             "nth": nth}])
        answer = evidence_query.evidence_for(model, plan)
        out["asked"] += 1
        if answer["status"] != evidence_query.PRESENT:
            out["notes"].append(f"nth={nth}: {answer['status']}")
            continue
        out["answered"] += 1
        row = answer["results"][0]
        if row.get("occurrence", {}).get("index") == nth:
            out["right"] += 1
    other = fixture.get("other_source")
    if other:
        scoped = {"asked": 0, "answered": 0, "right": 0, "notes": []}
        for nth in range(1, 6):
            plan = evidence_query.compile_plan(
                [{"predicate": "nth_event", "event_class": fixture["class"],
                  "nth": nth}],
                scope={"asset_id": other["asset_id"]})
            answer = evidence_query.evidence_for(model, plan)
            scoped["asked"] += 1
            if answer["status"] != evidence_query.PRESENT:
                scoped["notes"].append(f"nth={nth}: {answer['status']}")
                continue
            scoped["answered"] += 1
            row = answer["results"][0]
            if row.get("occurrence", {}).get("index") == nth:
                scoped["right"] += 1
        out["scoped"] = scoped
    return out


def measure_live(project_folder: str | None) -> dict:
    """The live model: what the migration builds, and what it answers."""
    if not project_folder or not Path(project_folder).is_dir():
        return {"state": "not_run",
                "reason": "no scratch project was given"}
    from library.tools import project_model_evidence
    model, report = project_model_evidence.migrate_project_evidence(project_folder)
    occurrence_identity.assign_occurrences(model)
    numbered = sum(1 for event in model.get("events", [])
                   if event.get("occurrence"))
    return {
        "state": "measured",
        "project": project_folder,
        "events": len(model.get("events", [])),
        "numbered": numbered,
        "notes": [note for note in report.get("notes", [])
                  if "M4" in note or "M7" in note or "M3b" in note],
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    project = argv[0] if argv else None
    corpus = json.loads(FIXTURES.read_text(encoding="utf-8"))
    rows = []
    for fixture in corpus["fixtures"]:
        rows.append(measure_fixture(fixture))
    recall = min(row["recall"] for row in rows if row["recall"] is not None)
    exactness = min(row["exactness"] for row in rows
                    if row["exactness"] is not None)
    stability = min(row["stability"] for row in rows
                     if row["stability"] is not None)
    report = {
        "corpus_id": corpus["corpus_id"],
        "fixtures": rows,
        "repeat_event_recall": recall,
        "occurrence_index_exactness": exactness,
        "index_stability_across_a_remeasure": stability,
        "live": measure_live(project),
        "targets": {"repeat_event_recall": 0.9,
                    "occurrence_index_exactness": 1.0,
                    "index_stability_across_a_remeasure": 1.0},
    }
    report["passes"] = (recall >= report["targets"]["repeat_event_recall"]
                        and exactness >= 1.0 and stability >= 1.0)
    print(json.dumps(report, indent=2))
    return 0 if report["passes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
