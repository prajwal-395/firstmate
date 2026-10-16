"""Adapters that freeze temporal-grounding predictions for scoring.

The incumbent is M7 geometric event candidates (``library/tools/event_spans.py``,
plus the Project Model candidate rules in
``library/tools/temporal_action_candidates.py``) judged on demand by the M8
verifier (``library/tools/span_verification.py``). Challengers are compact
temporal-grounding models (TimeLens 2, TimePLE) that answer a frozen
natural-language query with time intervals. This module converts either side
into the proposal rows scored by
``library/tools/analysis/temporal_event_benchmark.py`` and builds frozen
``ren_evaluation_report/1`` documents for the scorecard CLI
(``library/tools/evaluation_scorecard.py``).

This module runs no models, fetches no weights, decodes no footage, and
changes no capability defaults. SEM-043 (preserve the incumbent) is the
standing decision until a challenger wins on frozen calibration labels;
see ``docs/benchmarks/2026-10-16-temporal-grounding-candidates.md``.
"""

from __future__ import annotations

import hashlib
import math
import resource
import time
from typing import Any, Self

from library.tools.analysis.temporal_event_benchmark import EVENT_CLASSES

ADAPTER_REVISION = "temporal_grounding_adapters/1"
MODALITY = "temporal_grounding"

# Commercial policy vocabulary for SEM-042. Research-only and quarantined
# weights are never eligible for the Commercial path; only a measured win
# on frozen calibration labels can move an eligible challenger forward.
LICENSE_CLASSES = ("commercial", "conditional", "research_only", "quarantine")
ELIGIBLE_FOR_COMMERCIAL = ("commercial", "conditional")
ADOPTION_DECISIONS = (
    "adopt", "retain baseline", "research-only", "conditional fallback", "reject",
)

# TimeLens 2 answers each query with a JSON array of [start, end] pairs in
# seconds; TimePLE decodes to a continuous [start, end] interval. Both are
# normalized through the same strict parser below.
TIMELENS_PROMPT = (
    'Given the query: "{query}", return ALL time spans (in seconds) '
    "where the query is relevant.\n"
    "Output format MUST be a JSON array of [start, end] pairs.\n"
)

# Sampling the candidates were asked to run at. TimeLens 2's own published
# example uses 2 fps with a 480 px cap; the run record freezes whatever the
# benchmark run actually used so a later reader can tell.
DEFAULT_SAMPLING = {"fps": 2.0, "max_pixels": 480 * 480}


class TemporalGroundingAdapterError(ValueError):
    """A prediction cannot be produced, parsed, or frozen."""


def _is_finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def prompt_sha256(query: str) -> str:
    """Stable identity for the exact query prompt a run used."""
    return hashlib.sha256(
        TIMELENS_PROMPT.format(query=query).encode("utf-8")).hexdigest()


def query_id(query: str) -> str:
    """Short stable id for a frozen calibration query."""
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:12]


def parse_interval_pairs(raw: Any, *, duration_seconds: float) -> list[list[float]]:
    """Strictly parse a challenger answer into [start, end] pairs.

    Malformed answers raise: inventing intervals for a model would score
    the adapter, not the candidate. Callers that need a frozen record of
    the failure use :func:`record_malformed`.
    """
    if not isinstance(raw, list):
        raise TemporalGroundingAdapterError("answer must be a JSON array")
    pairs: list[list[float]] = []
    for index, item in enumerate(raw):
        if (not isinstance(item, (list, tuple)) or len(item) != 2):
            raise TemporalGroundingAdapterError(
                f"pair {index}: expected [start, end]")
        start, end = item
        if not _is_finite_number(start) or not _is_finite_number(end):
            raise TemporalGroundingAdapterError(
                f"pair {index}: bounds must be finite numbers")
        start, end = float(start), float(end)
        if start < 0 or end <= start or end > duration_seconds:
            raise TemporalGroundingAdapterError(
                f"pair {index}: interval [{start}, {end}] is outside "
                f"the 0..{duration_seconds} source range")
        pairs.append([start, end])
    return pairs


def record_malformed(*, query: str, raw_text: str, reason: str) -> dict[str, Any]:
    """Freeze a malformed challenger answer as a non-interval record."""
    return {
        "query_id": query_id(query),
        "query": query,
        "raw_text": raw_text[:2000],
        "reason": reason,
        "intervals": [],
        "repaired": False,
    }


def challenger_intervals_to_proposals(
    intervals: list[list[float]],
    *,
    source_id: str,
    event_class: str,
    proposal_method: str,
    identity_id: str | None = None,
    stage: str = "event_detection",
    refines_proposal_id: str | None = None,
) -> list[dict[str, Any]]:
    """Freeze challenger intervals as evaluator proposal rows.

    Detection rows (the default) carry ``verifier: not_run``: a challenger
    interval is a proposal until the M8 verifier judges it, exactly like an
    M7 span. Boundary-refinement rows must name the detection proposal they
    refine (SEM-041: detection and refinement score separately).
    """
    if not isinstance(source_id, str) or not source_id.strip():
        raise TemporalGroundingAdapterError("source_id must be non-empty")
    if event_class not in EVENT_CLASSES:
        raise TemporalGroundingAdapterError(
            f"unknown event_class {event_class!r}")
    if not isinstance(proposal_method, str) or not proposal_method.strip():
        raise TemporalGroundingAdapterError("proposal_method is required")
    if stage not in ("event_detection", "boundary_refinement"):
        raise TemporalGroundingAdapterError(f"invalid stage {stage!r}")
    if stage == "boundary_refinement" and (
            not isinstance(refines_proposal_id, str)
            or not refines_proposal_id.strip()):
        raise TemporalGroundingAdapterError(
            "boundary refinement requires refines_proposal_id")
    proposals = []
    for index, (start, end) in enumerate(intervals):
        row: dict[str, Any] = {
            "proposal_id": f"{source_id}:{proposal_method}:{index:05d}",
            "event_class": event_class,
            "identity_id": identity_id,
            "start": start,
            "end": end,
            "proposal_method": proposal_method,
            "stage": stage,
            "verifier": {
                "decision": "not_run",
                "seconds": None,
                "model_id": "not_recorded",
            },
        }
        if stage == "boundary_refinement":
            row["refines_proposal_id"] = refines_proposal_id
        proposals.append(row)
    return proposals


def incumbent_spans_to_proposals(
    spans: list[dict[str, Any]],
    *,
    source_id: str,
    predicate: str,
    event_class: str,
) -> list[dict[str, Any]]:
    """Freeze M7 geometric spans as evaluator proposal rows.

    The predicate-to-class mapping is an explicit caller input, matching
    ``m7_m8_proposals``: an old gesture predicate is never silently treated
    as a new event class. Spans without recorded M8 verdicts score as
    detection proposals with the verifier not run; spans with verdicts go
    through ``temporal_event_benchmark.m7_m8_proposals`` instead.
    """
    if event_class not in EVENT_CLASSES:
        raise TemporalGroundingAdapterError(
            f"{predicate}: unknown event class {event_class!r}")
    if not isinstance(predicate, str) or not predicate.strip():
        raise TemporalGroundingAdapterError("predicate is required")
    proposals = []
    for index, span in enumerate(spans):
        start, end = span.get("start"), span.get("end")
        if (not _is_finite_number(start) or not _is_finite_number(end)
                or start < 0 or end <= start):
            raise TemporalGroundingAdapterError(
                f"{predicate} span {index}: invalid interval")
        proposals.append({
            "proposal_id": f"{source_id}:{predicate}:{index:05d}",
            "event_class": event_class,
            "identity_id": span.get("face_track"),
            "start": float(start),
            "end": float(end),
            "proposal_method": f"m7_geometry:{predicate}",
            "stage": "event_detection",
            "verifier": {
                "decision": "not_run",
                "seconds": None,
                "model_id": "not_recorded",
            },
        })
    return proposals


def peak_rss_gb() -> float:
    """Peak resident memory of this process in GiB (ru_maxrss)."""
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 3)


def new_run_record(
    *,
    model_id: str,
    publisher_revision: str,
    precision: str,
    license_class: str,
    query: str,
    source_id: str,
    duration_seconds: float,
    sampling: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open a timed run record a benchmark run fills in.

    Timing is split into decode / inference / serialization seconds so a
    later reader can see where the cost went (EVAL-021..024). Quality fields
    stay null until adjudicated labels for the split exist; no metric is
    ever synthesized to fill a gap.
    """
    if license_class not in LICENSE_CLASSES:
        raise TemporalGroundingAdapterError(
            f"unknown license_class {license_class!r}")
    if not isinstance(model_id, str) or not model_id.strip():
        raise TemporalGroundingAdapterError("model_id is required")
    if not isinstance(publisher_revision, str) or not publisher_revision.strip():
        raise TemporalGroundingAdapterError("publisher_revision is required")
    return {
        "adapter_revision": ADAPTER_REVISION,
        "model_id": model_id,
        "publisher_revision": publisher_revision,
        "precision": precision,
        "license_class": license_class,
        "query_id": query_id(query),
        "query": query,
        "prompt_sha256": prompt_sha256(query),
        "source_id": source_id,
        "duration_seconds": duration_seconds,
        "sampling": sampling if sampling is not None else dict(DEFAULT_SAMPLING),
        "device": None,
        "timing": {
            "decode_seconds": None,
            "infer_seconds": None,
            "serialize_seconds": None,
            "cold_seconds": None,
            "warm_seconds": None,
        },
        "peak_memory_gb": None,
        "intervals": None,
        "malformed": None,
    }


def close_run_record(
    record: dict[str, Any],
    *,
    decode_seconds: float | None,
    infer_seconds: float | None,
    serialize_seconds: float | None,
    cold_seconds: float | None = None,
    warm_seconds: float | None = None,
    device: str | None = None,
    intervals: list[list[float]] | None = None,
    malformed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fill a run record with measured timing and the frozen output."""
    if (intervals is None) == (malformed is None):
        raise TemporalGroundingAdapterError(
            "a run ends with intervals or a malformed record, not both/neither")
    for key, value in (("decode_seconds", decode_seconds),
                       ("infer_seconds", infer_seconds),
                       ("serialize_seconds", serialize_seconds),
                       ("cold_seconds", cold_seconds),
                       ("warm_seconds", warm_seconds)):
        if value is not None and (
                not _is_finite_number(value) or value < 0):
            raise TemporalGroundingAdapterError(f"{key} must be nonneg or null")
    record["timing"].update({
        "decode_seconds": decode_seconds,
        "infer_seconds": infer_seconds,
        "serialize_seconds": serialize_seconds,
        "cold_seconds": cold_seconds,
        "warm_seconds": warm_seconds,
    })
    record["peak_memory_gb"] = peak_rss_gb()
    if device is not None:
        record["device"] = device
    record["intervals"] = intervals
    record["malformed"] = malformed
    return record


def run_end_to_end_seconds(record: dict[str, Any]) -> float | None:
    """Summed decode/infer/serialize cost, null when any leg is unmeasured."""
    legs = [record["timing"][key] for key in (
        "decode_seconds", "infer_seconds", "serialize_seconds")]
    if any(leg is None for leg in legs):
        return None
    return float(sum(legs))


def to_evaluation_metrics(
    record: dict[str, Any],
    *,
    quality: dict[str, float] | None = None,
    cost_per_source_hour: float | None = None,
) -> dict[str, dict[str, Any]]:
    """Emit dimension-tagged metrics for a ``ren_evaluation_report/1``.

    Quality metrics are included only when adjudicated labels for the split
    exist. Latency, memory and cost dimensions are always emitted when
    measured, so a run without labels still yields a resource scorecard.
    """
    metrics: dict[str, dict[str, Any]] = {}
    end_to_end = run_end_to_end_seconds(record)
    if end_to_end is not None:
        metrics["end_to_end_seconds"] = {
            "value": end_to_end, "unit": "seconds", "dimension": "latency"}
    timing = record["timing"]
    for key, name in (("decode_seconds", "decode_seconds"),
                      ("infer_seconds", "infer_seconds"),
                      ("serialize_seconds", "serialize_seconds"),
                      ("cold_seconds", "cold_start_seconds"),
                      ("warm_seconds", "warm_infer_seconds")):
        if timing[key] is not None:
            metrics[name] = {
                "value": timing[key], "unit": "seconds",
                "dimension": "latency"}
    if record.get("peak_memory_gb") is not None:
        metrics["peak_memory_gb"] = {
            "value": record["peak_memory_gb"], "unit": "GiB",
            "dimension": "memory"}
    if cost_per_source_hour is not None:
        metrics["cost_per_source_hour"] = {
            "value": cost_per_source_hour, "unit": "cost_per_hour",
            "dimension": "cost"}
    if quality:
        for name, value in quality.items():
            metrics[name] = {
                "value": value, "unit": "ratio", "dimension": "quality"}
    return metrics


def build_evaluation_report(
    *,
    version_id: str,
    role: str,
    models: dict[str, str],
    split: str,
    metrics: dict[str, dict[str, Any]],
    evidence: list[dict[str, str]],
    revision: str,
    selection_basis: list[str],
) -> dict[str, Any]:
    """Emit one frozen ``ren_evaluation_report/1`` for the scorecard CLI."""
    if role not in ("baseline", "candidate", "accepted"):
        raise TemporalGroundingAdapterError(
            f"role must be baseline/candidate/accepted, got {role!r}")
    if split not in ("training", "calibration", "held_out"):
        raise TemporalGroundingAdapterError(
            f"split must be a corpus split, got {split!r}")
    if any(part not in ("training", "calibration")
           for part in selection_basis):
        raise TemporalGroundingAdapterError(
            "selection_basis may name training/calibration only")
    return {
        "format": "ren_evaluation_report/1",
        "revision": revision,
        "version": {"id": version_id, "role": role, "models": models},
        "modality": MODALITY,
        "split": split,
        "metrics": metrics,
        "evidence": evidence,
        "selection_basis": selection_basis,
    }


def detection_refinement_delta(
    detection_stats: dict[str, Any],
    refinement_stats: dict[str, Any],
) -> dict[str, Any]:
    """Frame the SEM-041 comparison: what boundary refinement changed.

    Both stats dicts are ``_stats`` rows from the evaluator (detection and
    boundary_refinement fields of one report). This helper frames deltas;
    it does not re-score. A refinement that never fires reports null
    deltas rather than zero improvement.
    """
    deltas: dict[str, Any] = {}
    for key in ("temporal_iou_mean", "boundary_error_mean_seconds",
                "onset_error_mean_seconds", "offset_error_mean_seconds",
                "precision", "recall"):
        base, refined = detection_stats.get(key), refinement_stats.get(key)
        if isinstance(base, (int, float)) and isinstance(refined, (int, float)):
            deltas[key] = refined - base
        else:
            deltas[key] = None
    return {
        "detection_predictions": detection_stats.get("predictions"),
        "refinement_predictions": refinement_stats.get("predictions"),
        "deltas_refinement_minus_detection": deltas,
        "selection_note": "report only; this comparison does not rank versions",
    }


def commercial_eligibility(
    license_class: str,
    beats_incumbent_on_calibration: bool | None,
) -> dict[str, Any]:
    """SEM-042 policy check for the Commercial temporal-grounding path.

    Research-only and quarantined weights are never eligible, even when
    they win on quality. An eligible challenger still needs a measured
    calibration win before it can replace the incumbent default.
    """
    if license_class not in LICENSE_CLASSES:
        raise TemporalGroundingAdapterError(
            f"unknown license_class {license_class!r}")
    if license_class not in ELIGIBLE_FOR_COMMERCIAL:
        return {
            "eligible_for_commercial": False,
            "reason": f"license_class {license_class} is restricted to "
                      "personal-benchmark-only use",
        }
    if beats_incumbent_on_calibration is None:
        return {
            "eligible_for_commercial": False,
            "reason": "no frozen calibration comparison against the "
                      "incumbent yet; quality unmeasured",
        }
    if not beats_incumbent_on_calibration:
        return {
            "eligible_for_commercial": False,
            "reason": "does not beat the incumbent on frozen calibration; "
                      "incumbent retained per SEM-043",
        }
    return {
        "eligible_for_commercial": True,
        "reason": "license-cleared and measured calibration win; adoption "
                  "still needs an explicit EVAL-032 decision",
    }


def timed_block() -> Any:
    """Context manager measuring wall seconds for one benchmark leg."""

    class _Timer:
        def __enter__(self) -> Self:
            self.started = time.perf_counter()
            self.seconds: float | None = None
            return self

        def __exit__(self, *_exc: object) -> None:
            self.seconds = time.perf_counter() - self.started

    return _Timer()
