#!/usr/bin/env python3
"""VI-008: the relations a model actually holds, and what they are worth.

The measurement the punchlist names: **precision and recall per
predicate**, on frames a human (here: the lane's own agent, looking at
every frame) labelled by hand, against the geometric candidates the
engine derives and the relations the local model confirms.

    python3 docs/benchmarks/vep-vi008-relation-bench.py \
        --project <scratch project> --memory-root <scratch memory root> \
        --detections docs/benchmarks/vi008-relation-detections.json \
        --labels docs/benchmarks/vi008-relation-labels.json \
        --verify --out /tmp/vi008.json

The script reads the project and the scratch memory; it writes nothing
to the project.  The DETECTIONS file holds the object lane's rows for
the labelled frames, so the measurement can be repeated without paying
the detector again; `--live` re-measures them instead.

What the report says, and what it does not:

- `before` is the state of the model BEFORE the relation lane: which
  relation predicates it carries at all, and what a co-occurrence
  answer would have said about the same window.
- `candidates` is every geometric contact the geometry proposed, with
  the frames each one rests on.
- `confirmed` is what survived the model's judgement, and
  `rejected` is what did not: the candidate/confirmed distinction is
  the measurement, not a comment on it.
- `per_predicate` is the engine's own scorer
  (`relation_classes.score_relations`) over the confirmed relations and
  over the candidates, so the value of the verification step is visible
  as a difference between two numbers on the SAME candidates.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import relation_classes, relation_verification
from library.tools.analysis import object_grounding


def _model(project: str, memory_root: str) -> tuple[dict, dict]:
    """The Project Model the measurement runs against."""
    from library.tools.project_model_evidence import migrate_project_evidence

    return migrate_project_evidence(project, memory_root=memory_root)


def _object_rows(model: dict, detections: dict, source_file: str
                 ) -> list[dict]:
    """The object lane's rows, built by the lane's own builder."""
    geometry = object_grounding.probe_source(source_file)
    indices = detections.get("frame_indices") or []
    inspected = object_grounding.coverage_intervals(
        indices, fps=geometry["fps"],
        sample_fps=float(detections.get("sample_rate_hz") or 1.0))
    scope = dict(detections.get("scope") or {})
    result = {
        "detections": detections.get("detections") or [],
        "coverage": {
            "scope": scope,
            "sample_rate_hz": float(detections.get("sample_rate_hz") or 1.0),
            "frames_inspected": len(indices),
            "frame_indices": indices,
            "inspected": object_grounding.merge_intervals(inspected),
            "unexamined": object_grounding.unexamined(scope, inspected),
            "source_fps": geometry["fps"],
            "source_frame_count": geometry["frame_count"],
        },
        "producer": {"id": detections.get("producer") or "owlv2_named",
                     "detector": "owlv2", "tier": "standard"},
    }
    rows, _provenance = object_grounding.build_observations(
        model=model,
        digest=detections["content_digest"],
        asset_id=detections["asset_id"],
        clock_id=detections["clock_id"],
        phrases=[detections["phrase"]],
        result=result)
    return rows


def _corpus(labels: dict) -> dict:
    """The corpus's own counts: frames, contacts, negatives."""
    windows = labels.get("windows") or []
    contacts = sum(len(window.get("contacts") or []) for window in windows)
    negatives = sum(len(window.get("negatives") or []) for window in windows)
    return {"windows": len(windows), "labelled_contacts": contacts,
            "labelled_negatives": negatives,
            "labelled_frames": contacts + negatives}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="VI-008: the relations a model holds, per predicate.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--memory-root", required=True)
    parser.add_argument("--model-json", default="",
                        help="a migrated model JSON, so the measurement "
                             "does not pay the migration again")
    parser.add_argument("--detections", required=True,
                        help="the object lane's rows for the labelled frames")
    parser.add_argument("--labels", required=True)
    parser.add_argument("--source-file", default="",
                        help="the media the detections were measured on")
    parser.add_argument("--verify", action="store_true",
                        help="put the ambiguous candidates to the local model")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    detections = json.loads(Path(args.detections).read_text(encoding="utf-8"))
    labels = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    if args.model_json:
        model = json.loads(Path(args.model_json).read_text(encoding="utf-8"))
    else:
        model, _report = _model(args.project, args.memory_root)

    before = {
        "relations": len(model.get("relations") or []),
        "by_predicate": _counts(model.get("relations") or [], "predicate"),
        "physical": sum(1 for row in model.get("relations") or []
                        if row.get("relation_class") == "physical"),
        "verified": sum(1 for row in model.get("relations") or []
                        if row.get("status") == "verified"),
    }

    rows = _object_rows(model, detections, args.source_file
                        or _asset_uri(model, detections["asset_id"]))
    model.setdefault("observations", []).extend(rows)

    candidates = relation_verification.derive_candidates(model)
    verdicts = []
    if args.verify:
        assets = {row.get("id"): row.get("uri")
                  for row in model.get("assets") or []
                  if isinstance(row, dict)}
        verdicts = relation_verification.verify_ambiguous(
            relation_verification.ambiguous_candidates(candidates),
            source_files={key: value for key, value in assets.items()
                          if isinstance(value, str)},
            memory_root=Path(args.memory_root))
    confirmed = relation_verification.confirmed_relations(candidates, verdicts)
    rejected = relation_verification.rejected_candidates(candidates, verdicts)

    entity_labels = {labels["person_id"]: "P",
                     relation_verification.object_entity_id(
                         detections["asset_id"], detections["phrase"]): "C"}
    entity_kinds = {labels["person_id"]: "person",
                    relation_verification.object_entity_id(
                        detections["asset_id"], detections["phrase"]): "object"}
    score_labels = {"model": model, "windows": labels.get("windows") or []}
    filed = relation_verification.rows_to_file(candidates, confirmed, rejected)
    confirmed_rows = [row for row in filed if row.get("status") == "verified"]
    candidate_rows = [row for row in filed if row.get("status") == "candidate"]
    scored = relation_classes.score_relations(
        confirmed_rows, score_labels, entity_labels,
        predicates=("touching", "holding"), entity_kinds=entity_kinds)
    scored_candidates = relation_classes.score_relations(
        candidate_rows, score_labels, entity_labels,
        predicates=("touching", "holding"), entity_kinds=entity_kinds)

    result = {
        "project": args.project,
        "memory_root": args.memory_root,
        "before": before,
        "corpus": _corpus(labels),
        "candidates": len(candidates),
        "ambiguous": len(relation_verification.ambiguous_candidates(candidates)),
        "confirmed": len(confirmed),
        "rejected": len(rejected),
        "per_predicate_confirmed": scored["per_predicate"],
        "per_predicate_candidates": scored_candidates["per_predicate"],
        "confirmed_score": scored,
        "candidate_score": scored_candidates,
        "relations": [
            {"id": row["id"], "predicate": row["predicate"],
             "status": row["status"], "interval": row["interval"],
             "strength": (row.get("geometry") or {}).get("strength"),
             "confirmation": row.get("confirmation"),
             "verification": row.get("verification")}
            for row in filed],
        "verdicts": verdicts,
    }
    print(json.dumps(result, indent=2, default=str))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, default=str),
                                  encoding="utf-8")
    return 0


def _counts(rows: list[dict], key: str) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key))
        counts[value] = counts.get(value, 0) + 1
    return counts


def _asset_uri(model: dict, asset_id: str) -> str:
    for row in model.get("assets") or []:
        if isinstance(row, dict) and row.get("id") == asset_id:
            return str(row.get("uri") or "")
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
