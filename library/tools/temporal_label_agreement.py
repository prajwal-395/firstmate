"""Intake for blind multi-agent temporal-event labels.

Captain's standing annotation policy supersedes single-annotator expectations:
annotations come from two independent blind agent labellers on different
model families, with a third blind tie-breaker on disagreements. Outputs are
agent labels, never human labels.

A label pass is a plain JSON document::

    {"source_id": "...", "duration_seconds": 12.0,
     "annotator_id": "agent-<family>-<run>",
     "model_family": "<family>",
     "events": [{"event_id": "e1", "event_class": "waving",
       "identity_id": "person-1", "start": 1.0, "end": 2.0,
       "contact_evidence": "...", "state_change_evidence": null,
       "completeness": "complete|partial|uncertain",
       "evidence_ref": "frame@1.25s"}],
     "hard_negatives": [{"negative_id": "n1", "event_class": "waving",
       "identity_id": "person-1", "start": 3.0, "end": 4.0,
       "reason": "..."}]}

Agreement reuses the evaluator's one-to-one class-constrained IoU matching
(``temporal_event_benchmark._match``), so "two labellers agree" means the
same thing "a proposal matches gold" will mean at scoring time.
Adjudicated events are emitted in the evaluator's event-row shape with
``provenance.review_status == "adjudicated"`` and
``label_origin == "agent"`` throughout.
"""

from __future__ import annotations

import math
from typing import Any

from library.tools.analysis.temporal_event_benchmark import (
    COMPLETENESS,
    EVENT_CLASSES,
    _match,
    _stats,
    temporal_iou,
)

AGREEMENT_FORMAT = "temporal_agent_labels/1"
LABEL_ORIGIN = "agent"
COMPLETENESS_VALUES = tuple(COMPLETENESS)


class LabelAgreementError(ValueError):
    """A label pass cannot be validated, compared, or adjudicated."""


def _is_finite_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _check_interval(row: dict[str, Any], label: str,
                    duration: float) -> tuple[float, float]:
    start, end = row.get("start"), row.get("end")
    if (not _is_finite_number(start) or not _is_finite_number(end)
            or start < 0 or end <= start or end > duration):
        raise LabelAgreementError(
            f"{label}: interval must satisfy 0 <= start < end <= {duration}")
    return float(start), float(end)


def validate_pass(pass_doc: dict[str, Any]) -> dict[str, Any]:
    """Validate one blind label pass; return out-of-vocabulary warnings.

    Event classes outside the evaluator vocabulary are accepted but
    reported: they describe what the labeller saw yet cannot reach the
    held-out scorer until the vocabulary manifest adopts them.
    """
    for key in ("source_id", "annotator_id", "model_family"):
        value = pass_doc.get(key)
        if not isinstance(value, str) or not value.strip():
            raise LabelAgreementError(f"{key} must be a non-empty string")
    duration = pass_doc.get("duration_seconds")
    if (not _is_finite_number(duration) or duration <= 0):
        raise LabelAgreementError("duration_seconds must be positive")
    out_of_vocabulary: list[str] = []
    for group, id_key in (("events", "event_id"),
                          ("hard_negatives", "negative_id")):
        rows = pass_doc.get(group, [])
        if not isinstance(rows, list):
            raise LabelAgreementError(f"{group} must be a list")
        seen: set[str] = set()
        for row in rows:
            rid = row.get(id_key)
            if not isinstance(rid, str) or not rid.strip():
                raise LabelAgreementError(f"{group} ids must be non-empty")
            if rid in seen:
                raise LabelAgreementError(f"duplicate {group} id {rid!r}")
            seen.add(rid)
            _check_interval(row, rid, float(duration))
            if (row.get("identity_id") is not None
                    and (not isinstance(row["identity_id"], str)
                         or not row["identity_id"].strip())):
                raise LabelAgreementError(
                    f"{rid}: identity_id must be a string or null")
            cls = row.get("event_class")
            if not isinstance(cls, str) or not cls.strip():
                raise LabelAgreementError(f"{rid}: event_class is required")
            if cls not in EVENT_CLASSES and cls not in out_of_vocabulary:
                out_of_vocabulary.append(cls)
            if group == "events":
                if row.get("completeness") not in COMPLETENESS_VALUES:
                    raise LabelAgreementError(
                        f"{rid}: invalid completeness")
                if not any(
                        isinstance(row.get(key), str) and row[key].strip()
                        for key in ("contact_evidence",
                                    "state_change_evidence")):
                    raise LabelAgreementError(
                        f"{rid}: contact or state-change evidence is required")
            else:
                if (not isinstance(row.get("reason"), str)
                        or not row["reason"].strip()):
                    raise LabelAgreementError(
                        f"{rid}: hard negative requires a reason")
    return {"out_of_vocabulary_classes": sorted(out_of_vocabulary)}


def _blinded(pass_doc: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    """Evaluator-shaped rows so ``_match`` keys on source and class.

    Annotator identity rides along in underscore keys the evaluator
    ignores, so adjudicated provenance can name the exact sides.
    """
    return [{**event, "_source_id": source_id,
             "_annotator_id": pass_doc.get("annotator_id"),
             "_model_family": pass_doc.get("model_family")}
            for event in pass_doc.get("events", [])]


def pairwise_agreement(pass_a: dict[str, Any], pass_b: dict[str, Any],
                       iou_threshold: float = 0.5) -> dict[str, Any]:
    """Agreement between two blind passes over the same source.

    Pass B is scored as "predictions" against pass A as "references" and
    vice versa; the symmetric F1 is the headline agreement figure. Unmatched
    events that still overlap a different-class event are reported as
    class confusion rather than pure misses.
    """
    if pass_a.get("source_id") != pass_b.get("source_id"):
        raise LabelAgreementError("passes cover different sources")
    if pass_a.get("annotator_id") == pass_b.get("annotator_id"):
        raise LabelAgreementError("agreement needs two distinct annotators")
    if not 0 < iou_threshold <= 1:
        raise LabelAgreementError("iou_threshold must be in (0, 1]")
    source_id = pass_a["source_id"]
    rows_a, rows_b = _blinded(pass_a, source_id), _blinded(pass_b, source_id)
    matches = _match(rows_b, rows_a, iou_threshold)
    matched_b = {id(p) for p, _, _ in matches}
    matched_a = {id(g) for _, g, _ in matches}
    confusion = []
    for event in rows_b:
        if id(event) in matched_b:
            continue
        for other in rows_a:
            if id(other) in matched_a:
                continue
            if other["event_class"] == event["event_class"]:
                continue
            iou = temporal_iou(
                (event["start"], event["end"]),
                (other["start"], other["end"]))
            if iou >= iou_threshold:
                confusion.append({
                    "pass_b_event_id": event["event_id"],
                    "pass_b_class": event["event_class"],
                    "pass_a_event_id": other["event_id"],
                    "pass_a_class": other["event_class"],
                    "iou": iou,
                })
    stats_b_vs_a = _stats(rows_b, rows_a, iou_threshold)
    stats_a_vs_b = _stats(rows_a, rows_b, iou_threshold)
    precision = stats_b_vs_a["precision"] or 0.0
    recall = stats_b_vs_a["recall"] or 0.0
    return {
        "source_id": source_id,
        "annotators": [pass_a["annotator_id"], pass_b["annotator_id"],
                       ],
        "model_families": [pass_a["model_family"],
                           pass_b["model_family"]],
        "iou_threshold": iou_threshold,
        "pass_a_events": len(rows_a),
        "pass_b_events": len(rows_b),
        "agreed_events": len(matches),
        "agreement_f1": (2 * precision * recall / (precision + recall)
                         if (precision + recall) else 0.0),
        "b_vs_a": stats_b_vs_a,
        "a_vs_b": stats_a_vs_b,
        "class_confusion": confusion,
        "label_origin": LABEL_ORIGIN,
    }


def adjudicate(pass_a: dict[str, Any], pass_b: dict[str, Any],
               tiebreaker: dict[str, Any],
               iou_threshold: float = 0.5) -> dict[str, Any]:
    """Adjudicate two blind passes with a blind third tie-breaker.

    Agreed events (matched at the threshold) merge to the mean of the two
    raw boundaries; both raw intervals stay in provenance. Disputed events
    go to the tie-breaker: it keeps the side it matches, and an event the
    tie-breaker matches on neither side is dropped as unresolved - never
    forced into the dataset. The tie-breaker must be blind: a pass sharing
    an annotator id with either side is refused.
    """
    for passed in (pass_a, pass_b, tiebreaker):
        validate_pass(passed)
    if not (pass_a.get("source_id") == pass_b.get("source_id")
            == tiebreaker.get("source_id")):
        raise LabelAgreementError("passes cover different sources")
    tie_id = tiebreaker.get("annotator_id")
    if tie_id in (pass_a.get("annotator_id"), pass_b.get("annotator_id")):
        raise LabelAgreementError(
            "tie-breaker must be a third annotator, blind to both sides")
    if len({pass_a.get("model_family"), pass_b.get("model_family"),
            tiebreaker.get("model_family")}) < 2:
        raise LabelAgreementError(
            "panel must span at least two model families")
    source_id = pass_a["source_id"]
    rows_a = _blinded(pass_a, source_id)
    rows_b = _blinded(pass_b, source_id)
    rows_t = _blinded(tiebreaker, source_id)
    matches = _match(rows_b, rows_a, iou_threshold)
    matched_a = {id(g) for _, g, _ in matches}
    matched_b = {id(p) for p, _, _ in matches}
    adjudicated: list[dict[str, Any]] = []
    raw_provenance: list[dict[str, Any]] = []
    for pred, gold, iou in matches:
        start = round((pred["start"] + gold["start"]) / 2, 3)
        end = round((pred["end"] + gold["end"]) / 2, 3)
        if end <= start:
            end = round(max(pred["end"], gold["end"]), 3)
            start = round(min(pred["start"], gold["start"]), 3)
        adjudicated.append(_adjudicated_row(
            source_id, pred, gold, start, end, iou, tie_id=None))
        raw_provenance.append({
            "event_id": adjudicated[-1]["event_id"],
            "basis": "agreement",
            "pass_a_interval": [gold["start"], gold["end"]],
            "pass_b_interval": [pred["start"], pred["end"]],
            "iou": iou,
        })
    disputed_a = [e for e in rows_a if id(e) not in matched_a]
    disputed_b = [e for e in rows_b if id(e) not in matched_b]
    tiebreak_kept = 0
    tiebreak_dropped = 0
    for event in disputed_a + disputed_b:
        side = [event]
        hit = _match(rows_t, side, iou_threshold)
        if not hit:
            tiebreak_dropped += 1
            raw_provenance.append({
                "event_id": event["event_id"],
                "basis": "dropped_no_tiebreak_match",
                "pass_interval": [event["start"], event["end"]],
                "iou": 0.0,
            })
            continue
        judge = hit[0][0]
        tiebreak_kept += 1
        adjudicated.append(_adjudicated_row(
            source_id, event, judge, judge["start"], judge["end"],
            hit[0][2], tie_id=tie_id))
        raw_provenance.append({
            "event_id": adjudicated[-1]["event_id"],
            "basis": "tiebreak",
            "pass_interval": [event["start"], event["end"]],
            "tiebreak_interval": [judge["start"], judge["end"]],
            "iou": hit[0][2],
        })
    return {
        "format": AGREEMENT_FORMAT,
        "source_id": source_id,
        "label_origin": LABEL_ORIGIN,
        "panel": {
            "pass_a": pass_a["annotator_id"],
            "pass_b": pass_b["annotator_id"],
            "tiebreaker": tie_id,
            "model_families": sorted({
                pass_a["model_family"], pass_b["model_family"],
                tiebreaker["model_family"]}),
        },
        "iou_threshold": iou_threshold,
        "agreed_events": len(matches),
        "tiebreak_kept": tiebreak_kept,
        "tiebreak_dropped": tiebreak_dropped,
        "adjudicated_events": adjudicated,
        "raw_provenance": raw_provenance,
    }


def _adjudicated_row(source_id: str, event: dict[str, Any],
                     other: dict[str, Any], start: float, end: float,
                     iou: float, tie_id: str | None) -> dict[str, Any]:
    """One evaluator-ready event row with full panel provenance."""
    evidence = event.get("contact_evidence") or event.get(
        "state_change_evidence")
    return {
        "event_id": f"{source_id}:adjudicated:{event['event_id']}",
        "event_class": event["event_class"],
        "identity_id": event.get("identity_id"),
        "start": start,
        "end": end,
        "contact_evidence": event.get("contact_evidence"),
        "state_change_evidence": event.get("state_change_evidence"),
        "completeness": event.get("completeness", "uncertain"),
        "repeat_group_id": event.get("repeat_group_id"),
        "label_origin": LABEL_ORIGIN,
        "provenance": {
            "annotator_id": (
                f"agent-panel:{event.get('_annotator_id', 'pass')}"
                f"+{other.get('_annotator_id', 'pass')}"
                + (f" tiebreak:{tie_id}" if tie_id else "")),
            "review_status": "adjudicated",
            "evidence_ref": (
                event.get("evidence_ref")
                or f"agent-panel visual evidence: {evidence}"),
            "panel_iou": iou,
        },
    }
