#!/usr/bin/env python3
"""Lane BL-2 benchmark: the typed relations a model actually holds.

The measurement is the one the grounding programme names: the
relations a Project Model carries, per predicate, with the count of
`verified` and `candidate` edges, and the precision/recall of the
physical edges against the existing blind-label corpus.

    python3 docs/benchmarks/vep-bl2-typed-relations-bench.py \
        --project <scratch project> --memory-root <scratch memory root> \
        --labels <blind label dir> --out /tmp/bl2.json

Nothing is written to the project: the migration only reads the
project's catalog and its measured lanes, and the report goes to
`--out`. The blind labels are read as they are; the labellers never
see model internals.

What the report says, and what it does not:

- `relations.by_predicate` counts the model's typed edges.
- `relations.status` counts `verified` against `candidate`.
- `relations.labels` says how many blind-label contacts and pickups
  name media THIS model contains. A corpus whose media the model does
  not hold has no comparable label: precision and recall are
  undefined there, and the report says `uncovered` rather than
  printing a zero that would read as a score.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import relation_classes  # noqa: E402
from library.tools.project_model_evidence import (  # noqa: E402
    migrate_project_evidence,
)


def _blind_labels(label_dir: Path) -> dict:
    """Every blind-label window under `label_dir`, as one label set."""
    windows = []
    for path in sorted(label_dir.rglob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or "source_file" not in doc:
            continue
        windows.append({
            "source_file": doc["source_file"],
            "split": doc.get("split"),
            "contacts": doc.get("contacts") or [],
            "pickups": doc.get("pickups") or [],
        })
    return {"windows": windows}


def _asset_names(model: dict) -> set[str]:
    return {os.path.basename(str(row.get("uri") or ""))
            for row in model.get("assets") or []}


def _label_coverage(labels: dict, names: set[str]) -> dict:
    covered = [window for window in labels.get("windows") or []
               if os.path.basename(str(window["source_file"])) in names]
    contacts = sum(len(window.get("contacts") or [])
                   for window in covered)
    pickups = sum(len(window.get("pickups") or [])
                  for window in covered)
    return {
        "windows": len(labels.get("windows") or []),
        "windows_in_model": len(covered),
        "contacts_in_model": contacts,
        "pickups_in_model": pickups,
        "sources": sorted({os.path.basename(str(window["source_file"]))
                           for window in covered}),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Lane BL-2: the typed relations a model holds.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--memory-root", required=True)
    parser.add_argument("--labels", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    model, report = migrate_project_evidence(
        args.project, memory_root=args.memory_root)
    relations = model.get("relations") or []

    by_predicate = collections.Counter(
        row.get("predicate") for row in relations)
    by_class = collections.Counter(
        row.get("relation_class") for row in relations)
    by_status = collections.Counter(
        row.get("status") for row in relations)
    by_certainty = collections.Counter(
        row.get("certainty") for row in relations)

    hand_rows = [row for row in model.get("observations") or []
                 if row.get("modality") in ("face_frame", "face")
                 and (row.get("payload") or {}).get("hands")]
    result = {
        "project": args.project,
        "memory_root": args.memory_root,
        "counts": report.get("counts"),
        "relations": {
            "total": len(relations),
            "by_predicate": dict(by_predicate),
            "by_class": dict(by_class),
            "by_status": dict(by_status),
            "by_certainty": dict(by_certainty),
        },
        "evidence": {
            "hand_bearing_observations": len(hand_rows),
            "relationship_candidate_observations": len([
                row for row in model.get("observations") or []
                if row.get("modality") == "relationship_candidate"]),
        },
        "published_predicates": {
            name: {
                "relation_class": row["relation_class"],
                "derived_by": row["derived_by"],
            }
            for name, row in getattr(relation_classes, "PREDICATES",
                                     {}).items()
        },
    }

    if args.labels:
        labels = _blind_labels(Path(args.labels))
        names = _asset_names(model)
        coverage = _label_coverage(labels, names)
        entity_labels: dict[str, str] = {}
        entity_kinds = {row["id"]: row.get("kind")
                        for row in model.get("entities") or []
                        if isinstance(row, dict)}
        scores = {}
        if coverage["contacts_in_model"]:
            predicates = tuple(getattr(relation_classes,
                                       "PHYSICAL_PREDICATES",
                                       ("touching",)))
            scores = relation_classes.score_relations(
                relations, labels, entity_labels,
                predicates=predicates,
                entity_kinds=entity_kinds)
        result["blind_labels"] = {
            "coverage": coverage,
            "score_relations": scores or None,
            "score_pickups": None,
        }
        if not coverage["contacts_in_model"]:
            result["blind_labels"]["note"] = (
                "the blind-label corpus names media this model does not "
                "hold: no comparable label, so precision and recall are "
                "undefined here - a coverage gap, not a score")

    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
