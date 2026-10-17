"""Score timed sound-event proposals against labeled reference events.

AUD-056: onset and offset errors are measured, not asserted. The
evaluator is deliberately independent of inference: it reads frozen
proposals and reference labels, so running it cannot load a model,
decode footage or alter project/source memory - same shape as
`library/tools/analysis/temporal_event_benchmark.py`, specialised for
sound (class-constrained one-to-one matching, boundary errors, rare
and overlap slices for AUD-057/058).

Input JSON schema (version 1)::

    {"schema_version": 1, "split": "calibration|held_out", "sources": [{
      "source_id": "...", "duration_seconds": 12.0,
      "proposal_generation_seconds": 0.02,
      "conditions": ["overlapping_speech_music"],
      "proposals": [{"proposal_id": "p1", "label": "Door slam",
        "start": 1.0, "end": 1.4, "confidence": 0.8,
        "proposal_method": "panns-cnn14-decisionlevelmax",
        "model_id": "panns.cnn14_decision_max"}],
      "reference_events": [{"event_id": "e1", "label": "Door slam",
        "start": 1.05, "end": 1.35, "rarity": "rare",
        "provenance": {"annotator_id": "agent:<family>",
          "review_status": "reviewed|adjudicated",
          "evidence_ref": "..."}}],
      "hard_negatives": [{"negative_id": "n1", "label": "Door slam",
        "start": 3.0, "end": 4.0, "reason": "..."}]}]}

Intervals are seconds from source start, half-open. Matching is
one-to-one and label-constrained (case-insensitive): candidate pairs
are taken in descending temporal-IoU order with deterministic
id tie-breaks, so the assignment is reproducible without a solver.
Boundary errors are measured on pairs matched at IoU >= 0.5 only - a
near-miss overlap says nothing about edge accuracy.

Selection discipline: a `held_out` input scores quality for reporting
only; choosing a version on held-out numbers is refused upstream by
`library/tools/evaluation_scorecard.py` (`selection_basis` admits
calibration/training only). This module reports both splits the same
way and never names a winner.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

SCHEMA_VERSION = 1
SPLITS = ("calibration", "held_out")
RARITIES = ("common", "rare", "nonstandard")
#: `synthetic` is construction truth from a deterministic generator
#: (`sound_event_fixtures`), not a judgement - calibration only, never
#: a substitute for independently labelled held-out references.
REVIEW_STATUSES = ("reviewed", "adjudicated", "synthetic")

#: The primary operating point. 0.3 and 0.7 are reported alongside so
#: a threshold choice stays visible instead of baked in.
PRIMARY_IOU = 0.5
REPORT_IOUS = (0.3, 0.5, 0.7)

#: A hard negative counts as hit when a same-label proposal overlaps
#: it at all; rejection is the complement.
HARD_NEGATIVE_HIT_IOU = 0.1


def _interval(row: dict, label: str) -> tuple[float, float]:
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


def _confidence(row: dict, label: str) -> float:
    value = row.get("confidence", 0.0)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{label}: confidence must be a finite number")
    return float(value)


def _validate(doc: dict) -> dict:
    if not isinstance(doc, dict) or doc.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version (want {SCHEMA_VERSION})")
    if doc.get("split") not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    sources = doc.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources must be a non-empty list")
    seen_sources = set()
    for source in sources:
        sid = source.get("source_id")
        if not isinstance(sid, str) or not sid.strip():
            raise ValueError("source_id must be a nonempty string")
        if sid in seen_sources:
            raise ValueError(f"duplicate source_id {sid!r}")
        seen_sources.add(sid)
        duration = source.get("duration_seconds")
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration <= 0
        ):
            raise ValueError(f"{sid}: duration_seconds must be positive")
        seconds = source.get("proposal_generation_seconds")
        if seconds is not None and (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError(
                f"{sid}: proposal_generation_seconds must be nonnegative or null"
            )
        conditions = source.get("conditions", [])
        if not isinstance(conditions, list) or any(
            not isinstance(c, str) or not c.strip() for c in conditions
        ):
            raise ValueError(f"{sid}: conditions must be a list of nonempty strings")
        for group, id_key in (
            ("proposals", "proposal_id"),
            ("reference_events", "event_id"),
            ("hard_negatives", "negative_id"),
        ):
            rows = source.get(group, [])
            if not isinstance(rows, list):
                raise ValueError(f"{sid}: {group} must be a list")
            seen_ids = set()
            for row in rows:
                rid = row.get(id_key) if isinstance(row, dict) else None
                if not isinstance(rid, str) or not rid.strip():
                    raise ValueError(f"{sid}: {group} entries need a nonempty {id_key}")
                if rid in seen_ids:
                    raise ValueError(f"{sid}: duplicate {id_key} {rid!r}")
                seen_ids.add(rid)
                label = row.get("label")
                if not isinstance(label, str) or not label.strip():
                    raise ValueError(f"{sid}: {rid} needs a nonempty label")
                _interval(row, f"{sid}:{rid}")
                if group == "proposals":
                    _confidence(row, f"{sid}:{rid}")
                if group == "reference_events":
                    if row.get("rarity", "common") not in RARITIES:
                        raise ValueError(
                            f"{sid}: {rid} rarity must be one of {RARITIES}"
                        )
                    provenance = row.get("provenance") or {}
                    if not isinstance(provenance, dict):
                        raise ValueError(f"{sid}: {rid} provenance must be an object")
                    if (
                        not isinstance(provenance.get("annotator_id"), str)
                        or not provenance["annotator_id"].strip()
                    ):
                        raise ValueError(
                            f"{sid}: {rid} provenance needs annotator_id"
                        )
                    if provenance.get("review_status") not in REVIEW_STATUSES:
                        raise ValueError(
                            f"{sid}: {rid} review_status must be one of "
                            f"{REVIEW_STATUSES}"
                        )
                    if (
                        not isinstance(provenance.get("evidence_ref"), str)
                        or not provenance["evidence_ref"].strip()
                    ):
                        raise ValueError(
                            f"{sid}: {rid} provenance needs evidence_ref"
                        )
    return doc


def temporal_iou(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Temporal IoU of two half-open intervals, 0.0 when disjoint."""
    start = max(first[0], second[0])
    end = min(first[1], second[1])
    overlap = max(0.0, end - start)
    if overlap <= 0.0:
        return 0.0
    union = (first[1] - first[0]) + (second[1] - second[0]) - overlap
    if union <= 0.0:
        return 0.0
    return overlap / union


def match_events(
    proposals: list[dict], references: list[dict]
) -> list[tuple[dict, dict, float]]:
    """One-to-one label-constrained matches, best IoU first.

    Labels compare case-insensitively on stripped text; each proposal
    and each reference matches at most once. Zero-IoU pairs never
    match: sharing a label with no overlap is a miss plus a false
    alarm, not a localisation.
    """
    refs_by_label: dict[str, list[dict]] = {}
    for ref in references:
        refs_by_label.setdefault(ref["label"].strip().lower(), []).append(ref)
    candidates = []
    for proposal in proposals:
        for ref in refs_by_label.get(proposal["label"].strip().lower(), []):
            iou = temporal_iou(
                (proposal["start"], proposal["end"]),
                (ref["start"], ref["end"]),
            )
            if iou > 0.0:
                candidates.append((iou, proposal, ref))
    candidates.sort(
        key=lambda item: (-item[0], item[1]["proposal_id"], item[2]["event_id"])
    )
    used_proposals, used_refs, matches = set(), set(), []
    for iou, proposal, ref in candidates:
        if (
            proposal["proposal_id"] in used_proposals
            or ref["event_id"] in used_refs
        ):
            continue
        used_proposals.add(proposal["proposal_id"])
        used_refs.add(ref["event_id"])
        matches.append((proposal, ref, iou))
    return matches


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p95": None, "n": 0}
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(math.ceil(0.95 * len(ordered)) - 1))
    return {
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "p95": ordered[index],
        "n": len(values),
    }


def score_source(source: dict) -> dict:
    """Score one source: F1 at three IoUs, boundary errors, negatives."""
    proposals = [
        {**row, "start": float(row["start"]), "end": float(row["end"])}
        for row in source.get("proposals", [])
    ]
    references = [
        {**row, "start": float(row["start"]), "end": float(row["end"])}
        for row in source.get("reference_events", [])
    ]
    matches = match_events(proposals, references)
    matched_refs = {ref["event_id"] for _, ref, _ in matches}
    matched_props = {prop["proposal_id"] for prop, _, _ in matches}

    by_iou: dict[str, dict] = {}
    for threshold in REPORT_IOUS:
        kept = [(p, r, iou) for p, r, iou in matches if iou >= threshold]
        kept_refs = {r["event_id"] for _, r, _ in kept}
        kept_props = {p["proposal_id"] for p, _, _ in kept}
        true_positives = len(kept)
        false_positives = len(proposals) - len(kept_props)
        false_negatives = len(references) - len(kept_refs)
        precision = (
            true_positives / (true_positives + false_positives)
            if (true_positives + false_positives)
            else None
        )
        recall = (
            true_positives / (true_positives + false_negatives)
            if (true_positives + false_negatives)
            else None
        )
        by_iou[str(threshold)] = {
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "precision": precision,
            "recall": recall,
            "f1": (
                2 * precision * recall / (precision + recall)
                if precision is not None
                and recall is not None
                and (precision + recall) > 0
                else None
            ),
        }

    primary = [
        (prop, ref, iou) for prop, ref, iou in matches if iou >= PRIMARY_IOU
    ]
    onset_errors = [prop["start"] - ref["start"] for prop, ref, _ in primary]
    offset_errors = [prop["end"] - ref["end"] for prop, ref, _ in primary]
    onset_abs = [abs(e) for e in onset_errors]
    offset_abs = [abs(e) for e in offset_errors]

    per_label: dict[str, dict] = {}
    labels = sorted({r["label"] for r in references} | {p["label"] for p in proposals})
    for label in labels:
        want = label.strip().lower()
        ref_ids = {
            r["event_id"] for r in references if r["label"].strip().lower() == want
        }
        prop_ids = {
            p["proposal_id"] for p in proposals if p["label"].strip().lower() == want
        }
        hits = {
            r["event_id"]
            for p, r, iou in matches
            if iou >= PRIMARY_IOU and r["event_id"] in ref_ids
        }
        hit_props = {
            p["proposal_id"]
            for p, r, iou in matches
            if iou >= PRIMARY_IOU and p["proposal_id"] in prop_ids
        }
        recall = len(hits) / len(ref_ids) if ref_ids else None
        precision = len(hit_props) / len(prop_ids) if prop_ids else None
        rarity = sorted(
            {
                r.get("rarity", "common")
                for r in references
                if r["label"].strip().lower() == want
            }
        )
        per_label[label] = {
            "references": len(ref_ids),
            "proposals": len(prop_ids),
            "recall_at_0_5": recall,
            "precision_at_0_5": precision,
            "rarity": rarity,
        }

    rare_refs = {
        r["event_id"]
        for r in references
        if r.get("rarity", "common") in ("rare", "nonstandard")
    }
    rare_hits = {
        r["event_id"]
        for _, r, iou in matches
        if iou >= PRIMARY_IOU and r["event_id"] in rare_refs
    }

    negatives = source.get("hard_negatives", [])
    rejected = 0
    for negative in negatives:
        span = (float(negative["start"]), float(negative["end"]))
        want = negative["label"].strip().lower()
        hit = any(
            p["label"].strip().lower() == want
            and temporal_iou((p["start"], p["end"]), span) >= HARD_NEGATIVE_HIT_IOU
            for p in proposals
        )
        if not hit:
            rejected += 1

    return {
        "source_id": source["source_id"],
        "conditions": list(source.get("conditions", [])),
        "n_proposals": len(proposals),
        "n_references": len(references),
        "n_matched_pairs": len(matches),
        "by_iou": by_iou,
        "onset_error_seconds": {
            "signed": _quantiles(onset_errors),
            "absolute": _quantiles(onset_abs),
        },
        "offset_error_seconds": {
            "signed": _quantiles(offset_errors),
            "absolute": _quantiles(offset_abs),
        },
        # Signed samples for corpus-level pooling (AUD-056 report
        # metrics pool matches, never means of means).
        "boundary_samples": {"onset": onset_errors, "offset": offset_errors},
        "per_label": per_label,
        "rare_recall_at_0_5": (
            len(rare_hits) / len(rare_refs) if rare_refs else None
        ),
        "n_rare_references": len(rare_refs),
        "hard_negatives": {
            "n": len(negatives),
            "rejected": rejected,
            "rejection_rate": (rejected / len(negatives)) if negatives else None,
        },
        "proposal_generation_seconds": source.get("proposal_generation_seconds"),
    }


def score_document(doc: dict) -> dict:
    """Validate then score every source, plus corpus-wide aggregates."""
    _validate(doc)
    sources = [score_source(source) for source in doc["sources"]]
    true_positives = sum(s["by_iou"][str(PRIMARY_IOU)]["true_positives"] for s in sources)
    false_positives = sum(
        s["by_iou"][str(PRIMARY_IOU)]["false_positives"] for s in sources
    )
    false_negatives = sum(
        s["by_iou"][str(PRIMARY_IOU)]["false_negatives"] for s in sources
    )
    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives)
        else None
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives)
        else None
    )
    condition_rows: dict[str, dict] = {}
    for scored, raw in zip(sources, doc["sources"]):
        for condition in scored["conditions"]:
            cell = condition_rows.setdefault(
                condition, {"true_positives": 0, "false_positives": 0,
                            "false_negatives": 0, "sources": 0}
            )
            cell["true_positives"] += scored["by_iou"][str(PRIMARY_IOU)][
                "true_positives"
            ]
            cell["false_positives"] += scored["by_iou"][str(PRIMARY_IOU)][
                "false_positives"
            ]
            cell["false_negatives"] += scored["by_iou"][str(PRIMARY_IOU)][
                "false_negatives"
            ]
            cell["sources"] += 1
    for cell in condition_rows.values():
        tp, fp, fn = cell["true_positives"], cell["false_positives"], cell["false_negatives"]
        cell["precision"] = tp / (tp + fp) if (tp + fp) else None
        cell["recall"] = tp / (tp + fn) if (tp + fn) else None
    generation = [
        s["proposal_generation_seconds"] for s in sources
        if s["proposal_generation_seconds"] is not None
    ]
    pooled_onset = [e for s in sources for e in s["boundary_samples"]["onset"]]
    pooled_offset = [e for s in sources for e in s["boundary_samples"]["offset"]]
    for scored in sources:
        del scored["boundary_samples"]
    return {
        "schema": f"ren_sound_event_benchmark/{SCHEMA_VERSION}",
        "split": doc["split"],
        "n_sources": len(sources),
        "overall_at_0_5": {
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "precision": precision,
            "recall": recall,
            "f1": (
                2 * precision * recall / (precision + recall)
                if precision is not None
                and recall is not None
                and (precision + recall) > 0
                else None
            ),
        },
        "by_condition": condition_rows,
        "proposal_generation_seconds": _quantiles(generation),
        "overall_boundary_errors": {
            "onset_signed": _quantiles(pooled_onset),
            "onset_absolute": _quantiles([abs(e) for e in pooled_onset]),
            "offset_signed": _quantiles(pooled_offset),
            "offset_absolute": _quantiles([abs(e) for e in pooled_offset]),
        },
        "sources": sources,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="benchmark input JSON (version 1)")
    parser.add_argument("--out", type=Path, help="write the scored JSON here")
    args = parser.parse_args(argv)
    try:
        doc = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read input: {exc}")
    try:
        result = score_document(doc)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
