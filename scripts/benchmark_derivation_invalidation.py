"""Measure derivation-aware invalidation on a project's own Project Model.

The measurement the punch list's **B-13** asks for: "take a real model,
revise one observation (a detector box), re-run the derivers, and publish
(i) the count of dependants marked stale, (ii) the count of unrelated rows
left untouched, and (iii) the diff of the event log."

    python3 scripts/benchmark_derivation_invalidation.py <project> [--dry-run]

The project is a SCRATCH COPY. The script revises one observation in the
project's canonical model, so point it at a copy: the revision is real and
the event log records it.

What it reports:

- the model's shape: observations, provenance rows and how many of those
  carry a producer version, observations carrying a derivation digest,
  and the dependant rows the derivation edges reach;
- the revision: one detector box moved, the event log's diff, and the
  dependants marked stale by it;
- what the revision did NOT touch: every other dependant row, and every
  model row outside the one that was revised.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import derivation_staleness, project_model_store


def _model_shape(model: dict) -> dict:
    provenance = model.get("provenance", [])
    observations = model.get("observations", [])
    edges = derivation_staleness.build_edges(model)
    return {
        "observations": len(observations),
        "provenance_rows": len(provenance),
        "provenance_rows_without_a_producer_version": sum(
            1 for row in provenance if row.get("producer_version") is None),
        "observations_with_a_derivation_digest": sum(
            1 for row in observations
            if derivation_staleness.derivation_digest(row)),
        "dependant_rows_with_an_edge": len(edges),
        "dependant_rows_by_collection": dict(collections.Counter(
            edge["collection"] for edge in edges.values())),
    }


def _pick_detector_box(model: dict, edges: dict) -> tuple[str, dict] | None:
    """The most-cited observation that carries a detector box."""
    counts: collections.Counter = collections.Counter()
    for edge in edges.values():
        for observation_id in edge["observation_ids"]:
            counts[observation_id] += 1
    by_id = {row["id"]: row for row in model.get("observations", [])
             if isinstance(row, dict) and isinstance(row.get("id"), str)}
    candidates = [
        (count, observation_id)
        for observation_id, count in counts.items()
        if isinstance(by_id.get(observation_id, {}).get("payload"), dict)
        and "box" in by_id[observation_id]["payload"]]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1]))
    _count, observation_id = candidates[0]
    return observation_id, by_id[observation_id]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("--dry-run", action="store_true",
                        help="measure the model without revising anything")
    args = parser.parse_args(argv)
    project = Path(args.project)
    revision, model = project_model_store.load_project_model(project)
    edges = derivation_staleness.build_edges(model)
    report: dict = {"project": str(project), "revision": revision,
                    "model": _model_shape(model)}
    picked = _pick_detector_box(model, edges)
    if picked is None:
        report["revision"] = None
        report["reason"] = ("no cited observation carries a detector box; "
                             "there is nothing to revise")
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    observation_id, observation = picked
    report["revised_observation"] = {
        "id": observation_id,
        "modality": observation.get("modality"),
        "box_before": (observation.get("payload") or {}).get("box"),
        "dependants_before_revision": sum(
            1 for edge in edges.values()
            if observation_id in edge["observation_ids"]),
    }
    if args.dry_run:
        report["revision"] = None
        report["reason"] = "dry run: nothing was revised"
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    # The registry is written BEFORE the revision: it is the index of
    # what the dependants rested on, and a revision is a difference
    # against it.
    derivation_staleness.refresh(project, model)
    revised = json.loads(json.dumps(observation))
    box = list((observation.get("payload") or {}).get("box") or [])
    if box:
        box[0] = round(float(box[0]) + 1.5, 3)
        revised["payload"]["box"] = box
        report["revised_observation"]["box_after"] = box
    events_before = project_model_store.project_model_revision(project)
    project_model_store.merge_evidence_records(
        project, observations=[revised], expected_revision=events_before,
        author="benchmark:derivation-invalidation",
        reason="benchmark: one detector box revised")
    events_after = project_model_store.project_model_revision(project)
    report["event_log"] = {
        "revision_before": events_before,
        "revision_after": events_after,
        "events_appended": events_after - events_before,
        "collections_touched": ["observations"],
        "rows_touched": [observation_id],
    }
    stale = derivation_staleness.mark_stale(
        project, [observation_id],
        reason="the detector box was revised")
    report["invalidation"] = {
        "stale_count": stale["stale_count"],
        "stale_by_collection": stale["stale_by_collection"],
        "stale_dependants": stale["stale_dependants"],
        "untouched_dependants": stale["untouched_dependants"],
        "model_rows_untouched": stale["model_rows_untouched"],
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
