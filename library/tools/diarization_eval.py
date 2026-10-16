"""Evaluation primitives for diarization bakeoffs.

The evaluator accepts turns from any diarizer, including multi-speaker
overlap and uncertain predictions. It keeps diarization (who spoke when
inside one recording) separate from persistent identity matching across
recordings. Model probabilities are retained as observations; this module
does not assume that different models' scores are calibrated alike.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations, pairwise, permutations
from statistics import fmean
from typing import Any


class DiarizationEvaluationError(ValueError):
    """An evaluation record is malformed or cannot be compared."""


def _turns(rows: Sequence[Mapping[str, Any]], role: str) -> list[dict]:
    result = []
    for index, row in enumerate(rows):
        start, end = row["start"], row["end"]
        if (isinstance(start, bool) or isinstance(end, bool)
                or not isinstance(start, (int, float))
                or not isinstance(end, (int, float))
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start):
            raise DiarizationEvaluationError(
                f"{role}[{index}] must have finite non-negative start < end")
        speakers = row.get("speakers")
        if speakers is None:
            speaker = row.get("speaker")
            speakers = [] if speaker is None else [speaker]
        if not isinstance(speakers, (list, tuple)):
            raise DiarizationEvaluationError(
                f"{role}[{index}].speakers must be a list")
        if len(set(speakers)) != len(speakers) or any(
                not isinstance(s, str) or not s for s in speakers):
            raise DiarizationEvaluationError(
                f"{role}[{index}] has invalid or duplicate speaker ids")
        probabilities = row.get("speaker_probabilities", {})
        if not isinstance(probabilities, Mapping):
            raise DiarizationEvaluationError(
                f"{role}[{index}].speaker_probabilities must be an object")
        for speaker, probability in probabilities.items():
            if (speaker not in speakers or isinstance(probability, bool)
                    or not isinstance(probability, (int, float))
                    or not 0 <= probability <= 1):
                raise DiarizationEvaluationError(
                    f"{role}[{index}] has invalid speaker probability")
        result.append({"start": float(start), "end": float(end),
                       "speakers": tuple(speakers),
                       "uncertain": bool(row.get("uncertain", False)),
                       "speaker_probabilities": dict(probabilities)})
    return result


def _active(rows: Sequence[dict], time: float) -> set[str]:
    return {speaker for row in rows
            if row["start"] <= time < row["end"]
            for speaker in row["speakers"]}


def _speaker_mapping(reference: Sequence[dict], estimate: Sequence[dict],
                     boundaries: Sequence[float]) -> dict[str, str]:
    refs = sorted({s for row in reference for s in row["speakers"]})
    hyps = sorted({s for row in estimate for s in row["speakers"]})
    intersections: dict[tuple[str, str], float] = defaultdict(float)
    for left, right in pairwise(boundaries):
        if right <= left:
            continue
        refs_here = _active(reference, (left + right) / 2)
        hyps_here = _active(estimate, (left + right) / 2)
        for ref in refs_here:
            for hyp in hyps_here:
                intersections[(ref, hyp)] += right - left
    # Speaker label sets are small (the Sortformer candidate is capped at
    # four); exhaustive assignment makes the label permutation explicit.
    if len(refs) <= len(hyps):
        best = max(permutations(hyps, len(refs)), key=lambda perm: sum(
            intersections[(ref, hyp)] for ref, hyp in zip(refs, perm)),
                   default=())
        return dict(zip(refs, best))
    best = max(permutations(refs, len(hyps)), key=lambda perm: sum(
        intersections[(ref, hyp)] for ref, hyp in zip(perm, hyps)),
               default=())
    return {ref: hyp for ref, hyp in zip(best, hyps)}


def diarization_metrics(reference_rows: Sequence[Mapping[str, Any]],
                        estimate_rows: Sequence[Mapping[str, Any]], *,
                        collar_seconds: float = 0.25,
                        change_tolerance_seconds: float = 0.5) -> dict:
    """Return overlap-aware DER components and missed speaker-change counts.

    DER uses reference speaker time as denominator, a global optimal
    one-to-one speaker mapping, and a collar around reference boundaries.
    On each scored region, miss and false alarm account for cardinality
    differences; confusion accounts for unmatched simultaneous voices.
    """
    if collar_seconds < 0 or change_tolerance_seconds < 0:
        raise ValueError("collars and tolerances must be non-negative")
    reference = _turns(reference_rows, "reference")
    estimate = _turns(estimate_rows, "estimate")
    boundaries = sorted({edge for rows in (reference, estimate)
                         for row in rows for edge in (row["start"], row["end"])})
    if len(boundaries) < 2:
        return {"status": "no_scored_speech", "der": None,
                "miss_seconds": 0.0, "false_alarm_seconds": 0.0,
                "confusion_seconds": 0.0, "scored_speaker_seconds": 0.0,
                "speaker_mapping": {}, "collar_seconds": collar_seconds,
                "method": "overlap-aware time-weighted DER"}
    mapping = _speaker_mapping(reference, estimate, boundaries)
    miss = false_alarm = confusion = scored = 0.0
    collar_edges = [edge for row in reference
                    for edge in (row["start"], row["end"])]
    for left, right in pairwise(boundaries):
        if right <= left:
            continue
        midpoint = (left + right) / 2
        if any(abs(midpoint - edge) <= collar_seconds / 2
               for edge in collar_edges):
            continue
        refs = _active(reference, midpoint)
        hyps = _active(estimate, midpoint)
        scored += (right - left) * len(refs)
        mapped = {mapping.get(ref) for ref in refs} - {None}
        correct = len(mapped & hyps)
        matched = min(len(refs), len(hyps))
        miss += (right - left) * max(0, len(refs) - len(hyps))
        false_alarm += (right - left) * max(0, len(hyps) - len(refs))
        confusion += (right - left) * max(0, matched - correct)

    def changes(rows: Sequence[dict]) -> list[float]:
        ordered = sorted({edge for row in rows
                          for edge in (row["start"], row["end"])})
        result = []
        for edge in ordered[1:-1]:
            before = _active(rows, edge - 1e-7)
            after = _active(rows, edge + 1e-7)
            if before != after and before and after:
                result.append(edge)
        return result

    ref_changes = changes(reference)
    hyp_changes = changes(estimate)
    matched_indices: set[int] = set()
    matched_changes = 0
    for ref_time in ref_changes:
        candidates = [(abs(ref_time - hyp_time), index)
                      for index, hyp_time in enumerate(hyp_changes)
                      if index not in matched_indices
                      and abs(ref_time - hyp_time) <= change_tolerance_seconds]
        if candidates:
            _, index = min(candidates)
            matched_indices.add(index)
            matched_changes += 1
    errors = miss + false_alarm + confusion
    return {
        "status": "measured" if scored else "no_scored_speech",
        "der": round(errors / scored, 6) if scored else None,
        "miss_seconds": round(miss, 6),
        "false_alarm_seconds": round(false_alarm, 6),
        "confusion_seconds": round(confusion, 6),
        "scored_speaker_seconds": round(scored, 6),
        "speaker_mapping": mapping,
        "collar_seconds": collar_seconds,
        "method": "overlap-aware time-weighted DER",
        "speaker_changes": {
            "reference": len(ref_changes), "estimate": len(hyp_changes),
            "matched": matched_changes,
            "missed": len(ref_changes) - matched_changes,
            "spurious": len(hyp_changes) - matched_changes,
            "tolerance_seconds": change_tolerance_seconds,
        },
        "probabilities": {
            "present": any(row["speaker_probabilities"] for row in estimate),
            "calibration": "not assessed; raw model probabilities are not assumed comparable",
        },
        "uncertain_estimate_turns": sum(row["uncertain"] for row in estimate),
    }


def evaluate_record(record: Mapping[str, Any]) -> dict:
    """Evaluate one frozen sample with blind agent labels and tie-breaker."""
    sample_id = record["sample_id"]
    annotations = record["agent_annotations"]
    if not isinstance(annotations, list) or len(annotations) < 2:
        raise DiarizationEvaluationError(
            f"{sample_id}: at least two independent agent annotations required")
    annotator_ids = [item["annotator_id"] for item in annotations]
    model_families = [item["model_family"] for item in annotations]
    if (len(set(annotator_ids)) != len(annotator_ids)
            or len(set(model_families)) != len(model_families)
            or any(item.get("blind") is not True
                   or item.get("model_output_hidden") is not True
                   for item in annotations)):
        raise DiarizationEvaluationError(
            f"{sample_id}: labels must be blind and independent across model families")
    tie_breaker = record["tie_breaker"]
    if (tie_breaker["annotator_id"] in annotator_ids
            or tie_breaker["model_family"] in model_families
            or tie_breaker.get("blind") is not True
            or tie_breaker.get("model_output_hidden") is not True):
        raise DiarizationEvaluationError(
            f"{sample_id}: tie-breaker must be a third independent blind agent label")
    reference = record["adjudicated_reference"]
    prediction = record["prediction"]
    result = diarization_metrics(reference, prediction,
                                 collar_seconds=record.get("collar_seconds", 0.25),
                                 change_tolerance_seconds=record.get(
                                     "change_tolerance_seconds", 0.5))
    result.update({"sample_id": sample_id,
                   "split": record["split"],
                   "annotation_agreement": annotation_agreement(annotations),
                   "model": record["model"]})
    return result


def annotation_agreement(annotations: Sequence[Mapping[str, Any]]) -> dict:
    """Pairwise DER between independent labels, before tie-break adjudication."""
    rows = []
    for first, second in combinations(annotations, 2):
        compared = diarization_metrics(first["turns"], second["turns"],
                                       collar_seconds=0,
                                       change_tolerance_seconds=0.5)
        rows.append({"annotator_a": first["annotator_id"],
                     "annotator_b": second["annotator_id"],
                     "model_family_a": first["model_family"],
                     "model_family_b": second["model_family"],
                     "pairwise_der": compared["der"],
                     "speaker_changes": compared["speaker_changes"]})
    values = [row["pairwise_der"] for row in rows
              if row["pairwise_der"] is not None]
    return {"method": "pairwise overlap-aware DER, zero boundary collar",
            "pairwise": rows,
            "mean_pairwise_der": round(sum(values) / len(values), 6)
            if values else None}


def scorecard_report(*, revision: str, version_id: str, role: str,
                     model_id: str, model_revision: str, precision: str,
                     mode: str, split: str, license_class: str,
                     adoption_decision: str, sample_results: Sequence[Mapping],
                     measurements: Mapping[str, float],
                     evidence: Sequence[Mapping[str, str]]) -> dict:
    """Build one measured report in the shared ``ren_evaluation_report/1`` shape.

    A missing runtime or cost measurement refuses scorecard creation. This
    prevents an unmeasured dimension from looking like a zero-cost win.
    """
    from library.tools.evaluation_scorecard import FORMAT

    if split not in {"training", "calibration", "held_out"}:
        raise DiarizationEvaluationError(f"unsupported scorecard split: {split}")
    if role not in {"baseline", "candidate", "accepted"}:
        raise DiarizationEvaluationError(f"unsupported version role: {role}")
    if adoption_decision not in {"adopt", "retain baseline", "research-only",
                                  "conditional fallback", "reject"}:
        raise DiarizationEvaluationError(
            f"unsupported adoption decision: {adoption_decision}")
    if not sample_results:
        raise DiarizationEvaluationError("scorecard needs measured sample results")
    required_measurements = (
        "latency_cold_seconds", "latency_warm_seconds", "peak_memory_mb",
        "decode_seconds", "inference_seconds", "serialize_seconds",
        "end_to_end_cost_usd")
    for name in required_measurements:
        value = measurements[name]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0):
            raise DiarizationEvaluationError(
                f"measurements.{name} must be finite and non-negative")
    ders = [row["der"] for row in sample_results if row["der"] is not None]
    if not ders:
        raise DiarizationEvaluationError("scorecard needs scored reference speech")
    reference_changes = sum(row["speaker_changes"]["reference"]
                            for row in sample_results)
    missed_changes = sum(row["speaker_changes"]["missed"]
                         for row in sample_results)
    mean_confusion = fmean([row["confusion_seconds"] for row in sample_results])
    metrics = {
        "mean_der": {"value": fmean(ders), "unit": "ratio", "dimension": "quality"},
        "mean_confusion_seconds": {"value": mean_confusion, "unit": "seconds",
                                    "dimension": "quality"},
        "latency_cold_seconds": {"value": measurements["latency_cold_seconds"],
                                  "unit": "seconds", "dimension": "latency"},
        "latency_warm_seconds": {"value": measurements["latency_warm_seconds"],
                                  "unit": "seconds", "dimension": "latency"},
        "peak_memory_mb": {"value": measurements["peak_memory_mb"],
                            "unit": "MB", "dimension": "memory"},
        "decode_seconds": {"value": measurements["decode_seconds"],
                           "unit": "seconds", "dimension": "latency"},
        "inference_seconds": {"value": measurements["inference_seconds"],
                              "unit": "seconds", "dimension": "latency"},
        "serialize_seconds": {"value": measurements["serialize_seconds"],
                              "unit": "seconds", "dimension": "latency"},
        "end_to_end_cost_usd": {"value": measurements["end_to_end_cost_usd"],
                                "unit": "USD", "dimension": "cost"},
    }
    if reference_changes:
        metrics["missed_speaker_change_rate"] = {
            "value": missed_changes / reference_changes,
            "unit": "ratio", "dimension": "quality"}
    return {
        "format": FORMAT,
        "revision": revision,
        "version": {"id": version_id, "role": role,
                    "models": {model_id: model_revision}},
        "modality": "speaker_diarization",
        "split": split,
        "metrics": metrics,
        "evidence": [dict(item) for item in evidence],
        "selection_basis": [split] if split != "held_out" else [],
        "diarization": {
            "precision": precision, "mode": mode,
            "license_class": license_class,
            "adoption_decision": adoption_decision,
            "sample_count": len(sample_results),
            "reference_speaker_changes": reference_changes,
            "missed_speaker_changes": missed_changes,
            "runtime_measurements": dict(measurements),
        },
    }
