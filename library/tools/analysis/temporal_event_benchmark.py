"""Score temporal action proposals and verified events against human labels.

The evaluator is deliberately independent of inference. It reads frozen
proposals, recorded verifier decisions and human annotations, so running it
cannot load a model, decode footage or alter project/source memory.

Input JSON schema (version 1)::

    {"schema_version": 1, "split": "held_out", "sources": [{
      "source_id": "...", "duration_seconds": 12.0,
      "proposal_generation_seconds": 0.02,
      "proposals": [{"proposal_id": "p1", "event_class": "reach",
        "identity_id": "person_1", "start": 1.0, "end": 2.0,
        "proposal_method": "geometric", "verifier": {
        "decision": "yes|no|abstain|not_run", "seconds": 0.4,
          "model_id": "..."}}],
      "events": [{"event_id": "e1", "event_class": "reach",
        "identity_id": "person_1", "start": 1.1, "end": 1.9,
        "contact_evidence": "...", "state_change_evidence": "...",
        "completeness": "complete|partial|uncertain",
        "provenance": {"annotator_id": "...",
          "review_status": "reviewed|adjudicated", "evidence_ref": "..."}}],
      "hard_negatives": [{"negative_id": "n1", "event_class": "reach",
        "identity_id": "person_1", "start": 3.0, "end": 4.0,
        "reason": "..."}]}]}

Intervals are seconds from source start and half-open. Event assignment is
one-to-one, class constrained, and maximizes match count then summed temporal
IoU.
Identity is measured separately so an otherwise correctly localized event
does not disappear from recall because a tracker renamed its subject.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path

SCHEMA_VERSION = 1
EVENT_CLASSES = ("reach", "contact", "pick_up", "use", "set_down")
DECISIONS = ("yes", "no", "abstain", "not_run")
COMPLETENESS = ("complete", "partial", "uncertain")


def _interval(row, label):
    start, end = row["start"], row["end"]
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, (int, float))
        or not isinstance(end, (int, float))
        or not math.isfinite(start)
        or not math.isfinite(end)
        or start < 0
        or end <= start
    ):
        raise ValueError(
            f"{label}: expected finite half-open interval with 0 <= start < end"
        )
    return float(start), float(end)


def _validate(doc, corpus_manifest):
    if doc["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {doc['schema_version']!r}")
    if doc["split"] != "held_out":
        raise ValueError("quality scoring is restricted to split='held_out'")
    access = corpus_manifest["held_out_access"]
    if (
        access["status"] != "sealed_custodian_only"
        or access["tuning_access"] is not False
        or access["label_payloads_in_repository"] is not False
    ):
        raise ValueError(
            "corpus manifest has not released held-out labels under custodian control"
        )
    manifest_sources = {
        source["source_id"]: source for source in corpus_manifest["sources"]
    }
    seen_sources = set()
    for source in doc["sources"]:
        sid = source["source_id"]
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError("source_id must be a nonempty string")
        if sid in seen_sources:
            raise ValueError(f"duplicate source_id {sid!r}")
        seen_sources.add(sid)
        if sid not in manifest_sources or manifest_sources[sid]["split"] != "held_out":
            raise ValueError(f"{sid}: source is not held out in the corpus manifest")
        duration = source["duration_seconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration <= 0
        ):
            raise ValueError(f"{sid}: duration_seconds must be positive")
        proposal_seconds = source.get("proposal_generation_seconds")
        if proposal_seconds is not None and (
            isinstance(proposal_seconds, bool)
            or not isinstance(proposal_seconds, (int, float))
            or not math.isfinite(proposal_seconds)
            or proposal_seconds < 0
        ):
            raise ValueError(
                f"{sid}: proposal_generation_seconds must be nonnegative or null"
            )
        for group in ("proposals", "events", "hard_negatives"):
            ids = set()
            for row in source[group]:
                id_key = {
                    "proposals": "proposal_id",
                    "events": "event_id",
                    "hard_negatives": "negative_id",
                }[group]
                rid = row[id_key]
                if not isinstance(rid, str) or not rid.strip():
                    raise ValueError(f"{sid}: {group} IDs must be nonempty strings")
                if rid in ids:
                    raise ValueError(f"{sid}: duplicate {group} id {rid!r}")
                ids.add(rid)
                _, end = _interval(row, f"{sid}/{rid}")
                identity = row["identity_id"]
                if identity is not None and (
                    not isinstance(identity, str) or not identity.strip()
                ):
                    raise ValueError(
                        f"{sid}/{rid}: identity_id must be a nonempty string or null"
                    )
                if end > duration:
                    raise ValueError(f"{sid}/{rid}: interval exceeds source duration")
                if row["event_class"] not in EVENT_CLASSES:
                    raise ValueError(
                        f"{sid}/{rid}: unknown event_class {row['event_class']!r}"
                    )
                if group == "events":
                    if row["completeness"] not in COMPLETENESS:
                        raise ValueError(f"{sid}/{rid}: invalid completeness")
                    provenance = row["provenance"]
                    if any(
                        not isinstance(provenance[key], str)
                        or not provenance[key].strip()
                        for key in ("annotator_id", "review_status", "evidence_ref")
                    ):
                        raise ValueError(
                            f"{sid}/{rid}: complete annotation provenance is required"
                        )
                    if provenance["review_status"] not in ("reviewed", "adjudicated"):
                        raise ValueError(
                            f"{sid}/{rid}: human event label must be reviewed or adjudicated"
                        )
                    if any(
                        value is not None and not isinstance(value, str)
                        for value in (
                            row["contact_evidence"],
                            row["state_change_evidence"],
                        )
                    ):
                        raise ValueError(f"{sid}/{rid}: evidence must be text or null")
                    if not any(
                        isinstance(value, str) and value.strip()
                        for value in (
                            row["contact_evidence"],
                            row["state_change_evidence"],
                        )
                    ):
                        raise ValueError(
                            f"{sid}/{rid}: contact or state-change evidence is required"
                        )
                if group == "proposals":
                    verifier = row["verifier"]
                    if (
                        not isinstance(row["proposal_method"], str)
                        or not row["proposal_method"].strip()
                    ):
                        raise ValueError(f"{sid}/{rid}: proposal_method is required")
                    if verifier["decision"] not in DECISIONS:
                        raise ValueError(f"{sid}/{rid}: invalid verifier decision")
                    if (
                        not isinstance(verifier["model_id"], str)
                        or not verifier["model_id"].strip()
                    ):
                        raise ValueError(f"{sid}/{rid}: verifier model_id is required")
                    seconds = verifier["seconds"]
                    if seconds is not None and (
                        isinstance(seconds, bool)
                        or not isinstance(seconds, (int, float))
                        or not math.isfinite(seconds)
                        or seconds < 0
                    ):
                        raise ValueError(
                            f"{sid}/{rid}: verifier seconds must be nonnegative or null"
                        )
                if group == "hard_negatives" and (
                    not isinstance(row["reason"], str) or not row["reason"].strip()
                ):
                    raise ValueError(f"{sid}/{rid}: hard negative requires a reason")


def temporal_iou(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    inter = max(0.0, hi - lo)
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


def m7_m8_proposals(
    events_doc,
    verdicts_doc,
    *,
    source_id,
    duration_seconds,
    proposal_generation_seconds=None,
    class_by_predicate,
    statement_by_predicate,
):
    """Adapt existing M7 candidates and M8 records into evaluator rows.

    Predicate-to-class and predicate-to-verifier-statement mappings are
    explicit caller inputs. This avoids silently treating an old gesture
    predicate (for example, ``hand_raise``) as a new event class. A missing
    recorded verdict becomes ``not_run`` with null cost; it is neither a
    negative answer nor an abstention by a verifier that was called.
    """
    from library.tools.span_verification import select_frames

    recorded = verdicts_doc.get("verdicts") or {}
    available = list(recorded.values())
    consumed = set()
    proposals = []
    for predicate, event_class in sorted(class_by_predicate.items()):
        if event_class not in EVENT_CLASSES:
            raise ValueError(f"{predicate}: unknown event class {event_class!r}")
        if predicate not in statement_by_predicate:
            raise ValueError(f"{predicate}: verifier statement is required")
        statement = statement_by_predicate[predicate].strip()
        if not statement:
            raise ValueError(f"{predicate}: verifier statement is empty")
        spans = ((events_doc.get("candidates") or {}).get(predicate) or {}).get(
            "spans"
        ) or []
        for index, span in enumerate(spans):
            times = [round(frame["t"], 3) for frame in select_frames(span)]
            match_index = next(
                (
                    i
                    for i, verdict in enumerate(available)
                    if i not in consumed
                    and verdict.get("statement", "").strip() == statement
                    and [round(t, 3) for t in (verdict.get("frames") or [])] == times
                ),
                None,
            )
            if match_index is None:
                answer, seconds, model_id = "not_run", None, "not_recorded"
            else:
                consumed.add(match_index)
                verdict = available[match_index]
                answer = verdict.get("answer")
                answer = answer if answer in ("yes", "no") else "abstain"
                seconds = verdict.get("seconds")
                model_id = verdict.get("model") or "not_recorded"
            proposals.append(
                {
                    "proposal_id": f"{source_id}:{predicate}:{index:05d}",
                    "event_class": event_class,
                    "identity_id": span.get("face_track"),
                    "start": span["start"],
                    "end": span["end"],
                    "proposal_method": f"m7_geometry:{predicate}",
                    "verifier": {
                        "decision": answer,
                        "seconds": seconds,
                        "model_id": model_id,
                    },
                }
            )
    return {
        "source_id": source_id,
        "duration_seconds": duration_seconds,
        "proposal_generation_seconds": proposal_generation_seconds,
        "proposals": proposals,
    }


def _match(predictions, gold, threshold):
    """Maximum-cardinality one-to-one match, then maximize summed IoU.

    A high-overlap prediction that is the only fit for one human event must
    not greedily consume another event's only possible match. The square
    Hungarian assignment uses a cardinality bonus larger than all possible
    IoU gains combined; deterministic row/column ordering breaks exact ties.
    """
    n = max(len(predictions), len(gold))
    if n == 0:
        return []
    cardinality_bonus = n + 1.0
    weights = [[0.0] * n for _ in range(n)]
    ious = {}
    for pi, pred in enumerate(predictions):
        for gi, truth in enumerate(gold):
            if pred["event_class"] != truth["event_class"]:
                continue
            if pred.get("_source_id") != truth.get("_source_id"):
                continue
            iou = temporal_iou(
                _interval(pred, pred["event_class"]),
                _interval(truth, truth["event_class"]),
            )
            if iou >= threshold:
                weights[pi][gi] = cardinality_bonus + iou
                ious[pi, gi] = iou

    # Hungarian algorithm for a minimum-cost square assignment. Negated
    # weights turn maximum-weight matching into minimum-cost assignment.
    cost = [[-value for value in row] for row in weights]
    u, v = [0.0] * (n + 1), [0.0] * (n + 1)
    assignment, way = [0] * (n + 1), [0] * (n + 1)
    for row in range(1, n + 1):
        assignment[0] = row
        col0 = 0
        min_cost = [math.inf] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[col0] = True
            row0 = assignment[col0]
            delta, col1 = math.inf, 0
            for col in range(1, n + 1):
                if used[col]:
                    continue
                current = cost[row0 - 1][col - 1] - u[row0] - v[col]
                if current < min_cost[col]:
                    min_cost[col], way[col] = current, col0
                if min_cost[col] < delta:
                    delta, col1 = min_cost[col], col
            for col in range(n + 1):
                if used[col]:
                    u[assignment[col]] += delta
                    v[col] -= delta
                else:
                    min_cost[col] -= delta
            col0 = col1
            if assignment[col0] == 0:
                break
        while True:
            col1 = way[col0]
            assignment[col0] = assignment[col1]
            col0 = col1
            if col0 == 0:
                break

    matches = []
    for col in range(1, n + 1):
        row = assignment[col] - 1
        gi = col - 1
        if (row, gi) in ious:
            matches.append((predictions[row], gold[gi], ious[row, gi]))
    return matches


def _stats(predictions, gold, threshold):
    matches = _match(predictions, gold, threshold)
    n_pred, n_gold, tp = len(predictions), len(gold), len(matches)
    boundary = [
        ((abs(p["start"] - g["start"]) + abs(p["end"] - g["end"])) / 2.0)
        for p, g, _ in matches
    ]
    identities = [
        p.get("identity_id") == g.get("identity_id")
        for p, g, _ in matches
        if p.get("identity_id") is not None and g.get("identity_id") is not None
    ]
    return {
        "predictions": n_pred,
        "gold_events": n_gold,
        "true_positives": tp,
        "false_positives": n_pred - tp,
        "false_negatives": n_gold - tp,
        "precision": tp / n_pred if n_pred else None,
        "recall": tp / n_gold if n_gold else None,
        "temporal_iou_mean": statistics.fmean(iou for _, _, iou in matches)
        if matches
        else None,
        "boundary_error_mean_seconds": statistics.fmean(boundary) if boundary else None,
        "boundary_error_p95_seconds": _percentile(boundary, 0.95),
        "identity_accuracy": sum(identities) / len(identities) if identities else None,
        "identity_scored": len(identities),
    }


def _percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def evaluate(doc, iou_threshold=0.5, *, corpus_manifest):
    """Compute proposal and verifier-selected event scores overall/by class."""
    _validate(doc, corpus_manifest)
    if not 0 < iou_threshold <= 1:
        raise ValueError("iou_threshold must be in (0, 1]")
    sources = doc["sources"]
    # Source identity is part of the matching key. Equal timestamps in two
    # different camera originals must never allow a cross-source match.
    gold = [{**e, "_source_id": s["source_id"]} for s in sources for e in s["events"]]
    proposals = [
        {**p, "_source_id": s["source_id"]} for s in sources for p in s["proposals"]
    ]
    proposal_stats = _stats(proposals, gold, iou_threshold)
    methods = {}
    for method in sorted({p["proposal_method"] for p in proposals}):
        method_proposals = [p for p in proposals if p["proposal_method"] == method]
        method_classes = sorted({p["event_class"] for p in method_proposals})
        methods[method] = {
            "event_classes_scored": method_classes,
            **_stats(
                method_proposals,
                [g for g in gold if g["event_class"] in method_classes],
                iou_threshold,
            ),
        }
    verified = [p for p in proposals if p["verifier"]["decision"] == "yes"]
    verified_stats = _stats(verified, gold, iou_threshold)
    no = sum(p["verifier"]["decision"] == "no" for p in proposals)
    abstain = sum(p["verifier"]["decision"] == "abstain" for p in proposals)
    not_run = sum(p["verifier"]["decision"] == "not_run" for p in proposals)
    calls = len(proposals) - not_run
    costs = [
        p["verifier"]["seconds"]
        for p in proposals
        if p["verifier"]["seconds"] is not None
    ]
    classes = {}
    for cls in EVENT_CLASSES:
        c_gold = [x for x in gold if x["event_class"] == cls]
        c_prop = [x for x in proposals if x["event_class"] == cls]
        c_verified = [x for x in c_prop if x["verifier"]["decision"] == "yes"]
        classes[cls] = {
            "proposal": _stats(c_prop, c_gold, iou_threshold),
            "verified": _stats(c_verified, c_gold, iou_threshold),
            "completeness": {
                value: sum(e["completeness"] == value for e in c_gold)
                for value in COMPLETENESS
            },
            "verifier": {
                "yes": len(c_verified),
                "no": sum(x["verifier"]["decision"] == "no" for x in c_prop),
                "abstain": sum(x["verifier"]["decision"] == "abstain" for x in c_prop),
                "not_run": sum(x["verifier"]["decision"] == "not_run" for x in c_prop),
                "calls": sum(x["verifier"]["decision"] != "not_run" for x in c_prop),
                "timed_calls": sum(
                    x["verifier"]["seconds"] is not None for x in c_prop
                ),
                "seconds": (
                    round(
                        sum(
                            x["verifier"]["seconds"] or 0
                            for x in c_prop
                            if x["verifier"]["seconds"] is not None
                        ),
                        6,
                    )
                    if any(x["verifier"]["seconds"] is not None for x in c_prop)
                    else None
                ),
            },
        }
    # Hard negatives are separate from event recall: a correctly rejected
    # proposal overlapping a labelled negative is informative but never gold.
    negatives = [(s, n) for s in sources for n in s["hard_negatives"]]
    negative_overlaps = 0
    negative_rejections = 0
    for source, negative in negatives:
        nearby = [
            p
            for p in source["proposals"]
            if p["event_class"] == negative["event_class"]
            and p.get("identity_id") == negative.get("identity_id")
            and temporal_iou(
                _interval(p, p["proposal_id"]),
                _interval(negative, negative["negative_id"]),
            )
            >= iou_threshold
        ]
        for p in nearby:
            negative_overlaps += 1
            negative_rejections += p["verifier"]["decision"] == "no"
    return {
        "schema_version": 1,
        "split": doc["split"],
        "iou_threshold": iou_threshold,
        "sources": len(sources),
        "human_events": len(gold),
        "hard_negatives": len(negatives),
        "proposal_generation": {
            "measured_sources": sum(
                s.get("proposal_generation_seconds") is not None for s in sources
            ),
            "seconds": (
                round(
                    sum(
                        s["proposal_generation_seconds"]
                        for s in sources
                        if s.get("proposal_generation_seconds") is not None
                    ),
                    6,
                )
                if any(
                    s.get("proposal_generation_seconds") is not None for s in sources
                )
                else None
            ),
        },
        "proposal": proposal_stats,
        "proposal_by_method": methods,
        "verified": verified_stats,
        "verifier": {
            "yes": len(verified),
            "no": no,
            "abstain": abstain,
            "not_run": not_run,
            "abstention_rate": abstain / calls if calls else None,
            "calls": calls,
            "timed_calls": len(costs),
            "seconds": round(sum(costs), 6) if costs else None,
            "seconds_per_call": statistics.fmean(costs) if costs else None,
            "seconds_per_verified_true_positive": (
                sum(costs) / verified_stats["true_positives"]
                if costs and verified_stats["true_positives"]
                else None
            ),
            "monetary_cost": None,
            "monetary_cost_note": "not reported by the recorded verifier",
        },
        "hard_negative_verifier": {
            "overlapping_proposals": negative_overlaps,
            "rejected": negative_rejections,
            "rejection_rate": negative_rejections / negative_overlaps
            if negative_overlaps
            else None,
        },
        "by_event_class": classes,
        "completeness": {
            value: sum(e["completeness"] == value for e in gold)
            for value in COMPLETENESS
        },
    }


def load_dataset(path, corpus_manifest_path):
    with Path(path).open(encoding="utf-8") as handle:
        doc = json.load(handle)
    with Path(corpus_manifest_path).open(encoding="utf-8") as handle:
        corpus_manifest = json.load(handle)
    _validate(doc, corpus_manifest)
    return doc, corpus_manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--corpus-manifest", type=Path, required=True)
    parser.add_argument("--iou", type=float, default=0.5)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        dataset, corpus_manifest = load_dataset(args.dataset, args.corpus_manifest)
        report = evaluate(dataset, args.iou, corpus_manifest=corpus_manifest)
        encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(encoded, encoding="utf-8")
        else:
            sys.stdout.write(encoded)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"temporal event benchmark: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - exercised as CLI
    raise SystemExit(main())
