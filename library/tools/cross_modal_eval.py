"""Score held-out cross-modal relation annotations without loading models.

EVAL-09: after individual modalities pass, annotate speech/person/face/
event/object relations and unfamiliar scene categories; score relation
precision/recall and open-set abstention by modality and domain, keeping
evidence provenance and certainty labels in scoring.

The input is an evaluation record exported by an independent executor
(`predictions_from_project_model` below exports the shipped
`cross_modal_grounding` executor's relations into this shape). Gold
labels are separate, custodian-controlled data; the scorer rejects tuning
splits and records relation quality, abstention, provenance, certainty,
and measured execution cost by domain and modality.

Two annotation states are accepted. `sealed_custodian_only` scores fully
against custodian gold. `labels_pending_custodian` scores abstention only:
every gold list must be empty, predictions are recorded but never judged
(no false positives without something to be false against), and relation
precision/recall report null pending custodian labels from EVAL-03/05/06.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from library.tools.project_model_contract import Certainty

CERTAINTIES = {row.value for row in Certainty}

# Relation predicates the shipped grounding executor emits, grouped by the
# modality bucket they are scored under. Audio-visual alignment is recorded
# by that executor as cited overlap reports rather than relations, so the
# audio_visual_event bucket only fills when an executor emits relation rows.
PREDICATE_MODALITY = {
    "speaking_person_candidate": "speech_person",
    "speaks_as": "speech_person",
    "speaker_face_disagreement": "speech_person",
    "refers_to": "speech_reference",
}

MODALITIES = {
    "speech_person",
    "speech_reference",
    "face_event",
    "audio_visual_event",
    "person_object",
    "scene_category",
}

SEALED = "sealed_custodian_only"
PENDING = "labels_pending_custodian"

REFUSAL_EXIT_CODE = 2


def _relation_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (row["modality"], row["subject_id"], row["predicate"], row["object_id"])


def _validate_relation(row: dict[str, Any], *, gold: bool) -> None:
    _relation_key(row)
    if row["certainty"] not in CERTAINTIES:
        raise ValueError("relation certainty is not a Project Model certainty")
    provenance = row["provenance_ids" if gold else "evidence_provenance_ids"]
    if not provenance:
        raise ValueError("relations require evidence provenance")


def _validate(record: dict[str, Any]) -> None:
    if record["split"] != "held_out":
        raise ValueError("cross-modal quality scoring requires split=held_out")
    if record["tuning_access"] is not False:
        raise ValueError("held-out labels must be inaccessible to tuning")
    if not record["domain"] or not record["source_ids"]:
        raise ValueError("domain and source_ids are required")
    state = record["annotation_state"]
    if state not in (SEALED, PENDING):
        raise ValueError(
            "held-out labels must be sealed by the corpus "
            "custodian or explicitly pending custodian labels"
        )
    if record["executor"] == "" or record["executor_version"] == "":
        raise ValueError("executor identity and version are required")
    cost = record["cost"]
    if cost["elapsed_seconds"] < 0 or cost["model_passes"] < 0:
        raise ValueError("measured cost values must be non-negative")
    for case in record["cases"]:
        if not case["case_id"] or case["modality"] not in MODALITIES:
            raise ValueError("every case requires case_id and a known modality")
        for relation in case["gold"]:
            _validate_relation({**relation, "modality": case["modality"]}, gold=True)
        for relation in case["predictions"]:
            _validate_relation({**relation, "modality": case["modality"]}, gold=False)
        if state == PENDING:
            if not case["open_set"]:
                raise ValueError(
                    "labels-pending scoring accepts open-set abstention cases only"
                )
            if case["gold"]:
                raise ValueError(
                    "labels-pending scoring requires empty gold: "
                    "unguarded labels are not gold"
                )


def check_corpus_manifest(record: dict[str, Any], manifest: dict[str, Any]) -> None:
    """Refuse scored sources the EVAL-01 manifest does not hold out.

    A record's self-declared split is not enough: every scored source must
    be listed in the corpus manifest with split held_out, and the manifest
    must keep held-out labels sealed from tuning with no label payloads in
    the repository. Training, calibration and held-out sets stay separate.
    """
    access = manifest.get("held_out_access", {})
    if access.get("tuning_access") is not False:
        raise ValueError("corpus manifest must deny tuning access to held-out")
    if access.get("label_payloads_in_repository") is not False:
        raise ValueError("held-out label payloads must not live in the repo")
    held_out = {
        row.get("source_id")
        for row in manifest.get("sources", [])
        if row.get("split") == "held_out"
    }
    unknown = [sid for sid in record["source_ids"] if sid not in held_out]
    if unknown:
        raise ValueError(f"sources are not held out in the corpus manifest: {unknown}")


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | int | None]:
    # Empty denominators report null rather than zero: without gold there
    # is no accuracy to report, only abstention, provenance and cost.
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    if precision is None or recall is None or precision + recall == 0:
        f1 = None if precision is None or recall is None else 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)

    def rounded(value: float | None) -> float | None:
        return None if value is None else round(value, 6)

    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": rounded(precision),
        "recall": rounded(recall),
        "f1": rounded(f1),
    }


def score(
    record: dict[str, Any], corpus_manifest: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compute relation PRF and open-set/provenance/certainty scorecards."""
    _validate(record)
    if corpus_manifest is not None:
        check_corpus_manifest(record, corpus_manifest)
    pending = record["annotation_state"] == PENDING
    buckets: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "tp": 0,
            "fp": 0,
            "fn": 0,
            "open_cases": 0,
            "correct_abstentions": 0,
            "instances": 0,
            "unique_predicted": set(),
            "provenance_supported": 0,
            "matched_certainty": defaultdict(lambda: [0, 0]),
        }
    )
    for case in record["cases"]:
        bucket = buckets[(record["domain"], case["modality"])]
        gold = {
            _relation_key({**row, "modality": case["modality"]}): row
            for row in case["gold"]
        }
        predicted = {
            _relation_key({**row, "modality": case["modality"]}): row
            for row in case["predictions"]
        }
        matched = gold.keys() & predicted.keys()
        # Relation scoring is over distinct (subject, predicate, object)
        # triples: thirty-seven grounded utterances behind one
        # speaker-to-person link are one relation with thirty-seven
        # instances. Both counts are reported.
        bucket["instances"] += len(case["predictions"])
        bucket["unique_predicted"] |= predicted.keys()
        if pending:
            # Abstention only: predictions are recorded with their
            # provenance and certainty, never judged without gold.
            pass
        else:
            bucket["tp"] += len(matched)
            bucket["fp"] += len(predicted.keys() - gold.keys())
            bucket["fn"] += len(gold.keys() - predicted.keys())
        if case["open_set"]:
            bucket["open_cases"] += 1
            if not predicted:
                bucket["correct_abstentions"] += 1
        for key in matched:
            gold_row, predicted_row = gold[key], predicted[key]
            bucket["provenance_supported"] += bool(
                set(predicted_row["evidence_provenance_ids"])
                & set(gold_row["provenance_ids"])
            )
            certainty = predicted_row["certainty"]
            bucket["matched_certainty"][certainty][0] += 1
            bucket["matched_certainty"][certainty][1] += (
                certainty == gold_row["certainty"]
            )
    reports = []
    for (domain, modality), bucket in sorted(buckets.items()):
        if pending:
            quality: dict[str, float | int | None] = {
                "true_positive": None,
                "false_positive": None,
                "false_negative": None,
                "precision": None,
                "recall": None,
                "f1": None,
            }
        else:
            quality = _prf(bucket["tp"], bucket["fp"], bucket["fn"])
        reports.append(
            {
                "domain": domain,
                "modality": modality,
                **quality,
                "predicted_relation_instances": bucket["instances"],
                "predicted_relations": len(bucket["unique_predicted"]),
                "open_set_cases": bucket["open_cases"],
                "open_set_correct_abstentions": bucket["correct_abstentions"],
                "open_set_abstention_rate": round(
                    bucket["correct_abstentions"] / bucket["open_cases"], 6
                )
                if bucket["open_cases"]
                else None,
                "matched_relations_with_supported_provenance": bucket[
                    "provenance_supported"
                ],
                "certainty_accuracy_by_predicted_label": {
                    label: {
                        "matched": values[0],
                        "correct": values[1],
                        "accuracy": round(values[1] / values[0], 6),
                    }
                    for label, values in sorted(bucket["matched_certainty"].items())
                },
            }
        )
    return {
        "benchmark": record["benchmark_id"],
        "split": record["split"],
        "annotation_state": record["annotation_state"],
        "domain": record["domain"],
        "source_ids": record["source_ids"],
        "executor": record["executor"],
        "executor_version": record["executor_version"],
        "cost": {
            **record["cost"],
            "seconds_per_source": round(
                record["cost"]["elapsed_seconds"] / len(record["source_ids"]), 6
            ),
        },
        "scorecards": reports,
    }


def predictions_from_project_model(model: dict[str, Any]) -> dict[str, list]:
    """Export grounding relations as scorer predictions, by modality.

    The demonstrated consumer of the `cross_modal_grounding` executor: it
    reads that executor's relations off a Project Model and returns
    prediction rows carrying the same evidence provenance and certainty
    labels the scorer accounts. Disagreement and ambiguous-reference
    reports withhold grounding rather than predicting, so they export
    nothing; the scorer counts that silence as abstention on open cases.
    """
    grouped: dict[str, list] = defaultdict(list)
    for relation in model.get("relations", []):
        predicate = relation["predicate"]
        if predicate not in PREDICATE_MODALITY:
            continue
        grouped[PREDICATE_MODALITY[predicate]].append(
            {
                "subject_id": relation["subject_id"],
                "predicate": predicate,
                "object_id": relation["object_id"],
                "certainty": relation["certainty"],
                "evidence_provenance_ids": sorted(relation["evidence_ids"]),
                "provenance_id": relation["provenance_id"],
                "status": relation["status"],
            }
        )
    return dict(grouped)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path)
    parser.add_argument(
        "--corpus-manifest",
        type=Path,
        default=None,
        help="EVAL-01 corpus manifest; scored sources must be held out in it",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        with args.record.open(encoding="utf-8") as stream:
            record = json.load(stream)
        manifest = None
        if args.corpus_manifest is not None:
            with args.corpus_manifest.open(encoding="utf-8") as stream:
                manifest = json.load(stream)
        report = score(record, manifest)
    except (ValueError, KeyError, OSError) as exc:
        print(f"cross_modal_eval refused: {exc}", file=sys.stderr)
        return REFUSAL_EXIT_CODE
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
