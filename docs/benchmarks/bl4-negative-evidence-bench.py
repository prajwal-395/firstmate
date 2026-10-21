#!/usr/bin/env python3
"""BL-4: the negative-evidence benchmark.

Plan B-04 asks that a query can distinguish confidently absent from
sampled-but-not-seen, not analysed, failed and inconclusive. This
benchmark measures that on a REAL model, with queries whose ground
truth is "this never happens in this footage", and it enforces the one
gate that matters:

**A confident absence may never be issued from partial coverage.** Every
answer that claims an absence must rest on a completeness basis that is
complete, fresh and versioned - the same bar the Project Model's own
validator applies to a stored row.

Two query sets, with two different grounds for their truth:

- **record-backed**: the model holds a lane's measured absence over an
  asset (a zero-sample record with complete coverage). The lane looked
  across the whole asset and found nothing, so the truth is "absent".
- **transcript-backed**: the transcript of an asset is complete and
  does not contain a word. The truth comes from the transcript, not
  from the query layer, so it can disagree with the answer.

The report also counts what the model carries: the observations with a
processing status, the event boundaries that carry a bound, and the
rows in ``query_results``.

Usage:
    python3 docs/benchmarks/bl4-negative-evidence-bench.py \
        --project <ren-owned project> [--out report.json]

The project is READ: the benchmark loads the stored Project Model and,
with --migrate, re-migrates the project's measured evidence into a
model dict (also read-only) to measure the state a fresh migration
produces. It writes nothing into the project.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from library.tools import evidence_query, project_model_store

FIVE_STATES = ("present", "absent", "not_observed", "not_analyzed",
               "inconclusive")
FOUR_STATES = ("present", "confidently_absent", "not_yet_analysed",
               "inconclusive")


def load_model(project: str) -> tuple[int, dict]:
    """The project's stored Project Model, read-only."""
    revision, model = project_model_store.load_project_model(Path(project))
    return revision, model


def migrate_model(project: str) -> dict:
    """The model a fresh migration of the project's evidence produces."""
    from library.tools import project_model_evidence

    model, _report = project_model_evidence.migrate_project_evidence(
        str(project))
    return model


def _native(observation: dict) -> dict:
    payload = observation.get("payload") or {}
    contract = payload.get("evidence_contract")
    return contract if isinstance(contract, dict) else {}


def _lane_of(observation: dict) -> str:
    provenance = str(observation.get("provenance_id") or "")
    if provenance.startswith("prov-"):
        return provenance[len("prov-"):].rpartition("-")[2]
    return ""


def _measured_absence(observation: dict) -> bool:
    """A record that looked across its scope and found nothing."""
    contract = _native(observation)
    if not contract:
        return False
    if contract.get("coverage_status") != "complete":
        return False
    if contract.get("coverage_samples") not in (0, None):
        return False
    return contract.get("value") in ([], {}, None)


def record_backed_queries(model: dict) -> list[dict]:
    """One query per asset and measurement the lane recorded.

    The ground truth is the lane's own records: if any record for that
    asset and measurement carries a value, the measurement is there
    (``present``); if every record is a zero-sample record with an
    empty value, the lane covered the asset and found nothing
    (``absent``).
    """
    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for observation in model.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        kind = _native(observation).get("measurement_type")
        if not kind:
            continue
        groups[(str(observation.get("asset_id")), str(kind))].append(
            observation)
    queries = []
    for (asset_id, kind), observations in sorted(groups.items()):
        found = any(not _measured_absence(observation)
                   for observation in observations)
        queries.append({
            "set": "measurement-backed",
            "asset_id": asset_id,
            "predicate": {"predicate": "measurement",
                          "measurement_type": kind},
            "ground_truth": "present" if found else "absent",
            "evidence_id": observations[0].get("id"),
            "lane": _lane_of(observations[0]),
        })
    return queries


def transcript_backed_queries(model: dict, *, word: str) -> list[dict]:
    """One query per asset whose transcript never says the word.

    The transcript is the ground truth: an asset whose transcript holds
    no such word cannot contain it. The query layer is free to
    disagree - the report says how.
    """
    spoken: dict[str, set[str]] = collections.defaultdict(set)
    covered: set[str] = set()
    for observation in model.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        if observation.get("modality") != "speech_word":
            continue
        asset = str(observation.get("asset_id"))
        covered.add(asset)
        text = (observation.get("payload") or {}).get("text")
        if isinstance(text, str):
            spoken[asset].update(text.lower().split())
    return [{
        "set": "transcript-backed",
        "asset_id": asset,
        "predicate": {"predicate": "measurement",
                      "measurement_type": "speech.word",
                      "equals": word},
        "ground_truth": "absent",
        "lane": "m1",
    } for asset in sorted(covered) if word not in spoken[asset]]


def answer_of(model: dict, query: dict) -> dict:
    plan = evidence_query.compile_plan(
        [query["predicate"]], scope={"asset_id": query["asset_id"]})
    return evidence_query.evidence_for(model, plan)


def state_of(answer: dict) -> str:
    """The answer's state: five-state when the code has it, else the
    four-state vocabulary, mapped onto the five."""
    state = answer.get("answer_state")
    if state:
        return state
    status = answer.get("status")
    return {
        "present": "present",
        "confidently_absent": "absent",
        "not_yet_analysed": "not_analyzed",
        "inconclusive": "inconclusive",
    }.get(status, "inconclusive")


def basis_is_complete(answer: dict) -> bool:
    """Whether the answer's basis is complete, fresh and versioned.

    This is the gate: a confident absence whose basis is not complete,
    fresh and versioned is a claim nobody measured.
    """
    basis = answer.get("completeness_basis") or []
    if not basis:
        return False
    for row in basis:
        coverage = row.get("coverage")
        coverage = coverage if isinstance(coverage, str) else ""
        freshness = row.get("freshness")
        freshness = freshness if isinstance(freshness, str) else ""
        version = str(row.get("producer_version") or "").strip()
        if coverage != "complete" or freshness != "fresh" or not version:
            return False
    return True


def measure(model: dict, queries: list[dict]) -> dict:
    distribution = collections.Counter()
    by_set = collections.defaultdict(collections.Counter)
    violations: list[dict] = []
    absent = 0
    agrees = 0
    for query in queries:
        answer = answer_of(model, query)
        state = state_of(answer)
        distribution[state] += 1
        by_set[query["set"]][state] += 1
        truth = query.get("ground_truth")
        if truth and ((truth == "absent" and state == "absent")
                      or (truth == "present" and state == "present")):
            agrees += 1
        if state == "absent":
            absent += 1
            if not basis_is_complete(answer):
                violations.append({
                    "asset_id": query["asset_id"],
                    "query": query["predicate"],
                    "basis": answer.get("completeness_basis"),
                })
    return {
        "queries": len(queries),
        "distribution": {state: distribution.get(state, 0)
                         for state in FIVE_STATES},
        "distribution_by_set": {
            name: {state: counter.get(state, 0) for state in FIVE_STATES}
            for name, counter in sorted(by_set.items())},
        "answers_matching_the_ground_truth": agrees,
        "absent_answers": absent,
        "absent_without_a_complete_basis": len(violations),
        "violations": violations[:20],
        "gate": ("pass" if not violations else "fail"),
    }


def model_state(model: dict) -> dict:
    """What the model itself carries about coverage and uncertainty."""
    from library.tools import processing_status

    observations = model.get("observations") or []
    with_status = sum(1 for row in observations
                      if processing_status.processing_status_of(row) is not None)
    statuses = collections.Counter(
        processing_status.processing_status_of(row) for row in observations)
    estimated = bounded = 0
    for event in model.get("events") or []:
        for key in ("start_boundary", "end_boundary"):
            boundary = event.get(key)
            if not boundary:
                continue
            if boundary.get("status") == "estimated":
                estimated += 1
                if boundary.get("uncertainty_ticks") is not None:
                    bounded += 1
    return {
        "observations": len(observations),
        "observations_with_a_processing_status": with_status,
        "processing_status_counts": {
            str(name): count for name, count in sorted(
                statuses.items(), key=lambda item: str(item[0]))},
        "events": len(model.get("events") or []),
        "estimated_boundaries": estimated,
        "estimated_boundaries_with_a_bound": bounded,
        "query_result_rows": len(model.get("query_results") or []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True,
                        help="a Ren-owned project to measure (read-only)")
    parser.add_argument("--word", default="bl4absenttoken",
                        help="the word the transcript never says")
    parser.add_argument("--upgrade", action="store_true",
                        help="also measure the stored model brought up to "
                             "the coverage contract (in memory; the project "
                             "is not written)")
    parser.add_argument("--migrate", action="store_true",
                        help="also measure the model a fresh migration "
                             "produces (reads the project; writes nothing)")
    parser.add_argument("--write-rows", metavar="DIR",
                        help="file the answers' rows into a WRITABLE COPY "
                             "of a model store, and report how many rows "
                             "the model then holds")
    parser.add_argument("--write-rows-limit", type=int, default=10)
    parser.add_argument("--queries", type=int, default=0,
                        help="measure only the first N queries (0: all)")
    parser.add_argument("--out", help="write the JSON report here")
    args = parser.parse_args(argv)

    project = Path(args.project).expanduser()
    revision, stored = load_model(str(project))
    report: dict = {
        "project": str(project),
        "stored_revision": revision,
        "vocabulary": {
            "five_states": list(FIVE_STATES),
            "four_states": list(FOUR_STATES),
            "answer_has_a_five_state_field":
                "answer_state" in (evidence_query.evidence_for.__doc__ or ""),
        },
    }

    queries = (record_backed_queries(stored)
               + transcript_backed_queries(stored, word=args.word))
    if args.queries:
        queries = queries[:args.queries]
    started = time.time()
    report["stored"] = {
        "model_state": model_state(stored),
        "negative_evidence": measure(stored, queries),
        "seconds": round(time.time() - started, 3),
    }

    if args.upgrade:
        from library.tools import project_model_evidence

        started = time.time()
        upgraded = json.loads(json.dumps(stored))
        upgrade_report = project_model_evidence.apply_coverage_contract(
            upgraded)
        report["upgraded"] = {
            "upgrade": upgrade_report,
            "model_state": model_state(upgraded),
            "negative_evidence": measure(upgraded, queries),
            "seconds": round(time.time() - started, 3),
        }

    if args.migrate:
        started = time.time()
        migrated = migrate_model(str(project))
        migrated_queries = (record_backed_queries(migrated)
                            + transcript_backed_queries(migrated,
                                                        word=args.word))
        report["migrated"] = {
            "model_state": model_state(migrated),
            "negative_evidence": measure(migrated, migrated_queries),
            "seconds": round(time.time() - started, 3),
        }

    if args.write_rows:
        report["written_rows"] = write_rows(
            args.write_rows, stored, queries, limit=args.write_rows_limit)

    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n",
                                  encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def write_rows(store_copy: str, model: dict, queries: list[dict], *,
               limit: int) -> dict:
    """File the answers' rows into a writable copy of the model store.

    The copy is the project's own store, copied: the measurement writes
    rows and counts them, and the project's own model is never touched.
    """
    from library.tools import query_results

    copy_root = Path(store_copy).expanduser()
    revision, stored = project_model_store.load_project_model(copy_root)
    written = 0
    statuses: dict[str, int] = collections.Counter()
    for query in queries[:limit]:
        answer = answer_of(model, query)
        row = query_results.row_for_answer(answer)
        statuses[row["status"]] += 1
        revision = project_model_store.record_query_result(
            copy_root, stored, row, expected_revision=revision)
        stored = project_model_store.load_project_model(copy_root)[1]
        written += 1
    final_revision, final_model = project_model_store.load_project_model(
        copy_root)
    return {
        "store_copy": str(copy_root),
        "rows_written": written,
        "row_statuses": dict(sorted(statuses.items())),
        "query_result_rows_in_the_model": len(final_model.get(
            "query_results") or []),
        "revision": final_revision,
    }


if __name__ == "__main__":
    raise SystemExit(main())
