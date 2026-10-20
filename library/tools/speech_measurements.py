"""Typed speech measurements shared by M1 and edit-point consumers.

This module only measures and reports. It never changes speech spans or
produces a trim decision. Boundary uncertainty remains explicitly
uncalibrated until annotated audio establishes an error distribution.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from itertools import pairwise, permutations

NONVERBAL_LABELS = {
    "laugh": "laughter", "laughing": "laughter", "laughter": "laughter",
    "chuckle": "laughter", "cough": "cough", "coughing": "cough",
    "sigh": "sigh", "sighing": "sigh", "gasp": "gasp", "gasping": "gasp",
    "cry": "crying", "crying": "crying", "scream": "scream",
    "screaming": "scream", "breath": "breathing", "breathing": "breathing",
}
_NONVERBAL = re.compile(r"\[([^\]]+)\]|\(([^)]+)\)")


def nonverbal_vocalizations(text: str, start: float, end: float,
                            method: str = "explicit transcript annotation") -> list[dict]:
    """Return only explicitly marked vocalizations; do not infer from silence."""
    records = []
    for match in _NONVERBAL.finditer(text or ""):
        label = (match.group(1) or match.group(2) or "").strip().lower()
        kind = NONVERBAL_LABELS.get(label)
        if kind:
            records.append({
                "kind": kind, "start": float(start), "end": float(end),
                "status": "candidate", "method": method,
                "boundary_uncertainty": {"estimate_ms": None,
                                         "status": "uncalibrated"},
            })
    return records


def boundary_error(reference: Sequence[float], estimate: Sequence[float]) -> dict:
    """Report matched ordered-boundary MAE and signed bias in milliseconds."""
    if len(reference) != len(estimate):
        return {"status": "incomparable", "reference_count": len(reference),
                "estimate_count": len(estimate), "mae_ms": None,
                "method": "ordered boundary comparison"}
    errors = [(float(e) - float(r)) * 1000
              for r, e in zip(reference, estimate)]
    if not errors:
        return {"status": "no_boundaries", "mae_ms": None,
                "method": "ordered boundary comparison"}
    return {"status": "measured", "count": len(errors),
            "mae_ms": round(sum(abs(e) for e in errors) / len(errors), 3),
            "bias_ms": round(sum(errors) / len(errors), 3),
            "method": "ordered boundary comparison"}


def diarization_der(reference: Sequence[dict], estimate: Sequence[dict],
                    collar_seconds: float = 0.25) -> dict:
    """Compute time-weighted DER for single-speaker, non-overlap fixtures.

    The current M1 contract is speaker turns; overlapped reference regions
    are reported unsupported rather than flattened into a misleading score.
    The overlap-aware scorer lives in
    ``library.tools.diarization_eval.diarization_metrics`` (and its
    ``overlap_metrics``, which scores the overlap alone), and this
    refusal says where it is rather than leaving a reader to find it.
    """
    if collar_seconds < 0:
        raise ValueError("collar_seconds must be non-negative")
    boundaries = sorted({float(x[k]) for rows in (reference, estimate)
                         for x in rows for k in ("start", "end")})
    ref_speakers = sorted({str(x["speaker"]) for x in reference})
    hyp_speakers = sorted({str(x["speaker"]) for x in estimate})
    overlaps = {r: {h: 0.0 for h in hyp_speakers} for r in ref_speakers}
    for left, right in pairwise(boundaries):
        midpoint = (left + right) / 2
        active_ref = [str(x["speaker"]) for x in reference
                      if float(x["start"]) <= midpoint < float(x["end"])]
        active_hyp = [str(x["speaker"]) for x in estimate
                      if float(x["start"]) <= midpoint < float(x["end"])]
        if len(active_ref) > 1:
            return {"status": "unsupported_overlap", "der": None,
                    "collar_seconds": collar_seconds,
                    "method": "time-weighted diarization error rate",
                    "overlap_aware_scorer": (
                        "library.tools.diarization_eval.diarization_metrics "
                        "and library.tools.diarization_eval.overlap_metrics")}
        if active_ref and active_hyp:
            overlaps[active_ref[0]][active_hyp[0]] += right - left
    # Cluster labels are arbitrary: choose the one-to-one mapping with
    # greatest overlap before measuring confusion.
    mapping = {}
    if ref_speakers and hyp_speakers:
        best = max(permutations(hyp_speakers, min(len(ref_speakers),
                                                  len(hyp_speakers))),
                   key=lambda perm: sum(overlaps[r].get(h, 0.0)
                                        for r, h in zip(ref_speakers, perm)))
        mapping = dict(zip(ref_speakers, best))
    scored = miss = false_alarm = confusion = 0.0
    for left, right in pairwise(boundaries):
        midpoint = (left + right) / 2
        if any(abs(midpoint - edge) <= collar_seconds / 2
               for rows in (reference, estimate) for x in rows
               for edge in (float(x["start"]), float(x["end"]))):
            continue
        ref = [str(x["speaker"]) for x in reference
               if float(x["start"]) <= midpoint < float(x["end"])]
        hyp = [str(x["speaker"]) for x in estimate
               if float(x["start"]) <= midpoint < float(x["end"])]
        if not ref:
            if hyp:
                false_alarm += (right - left) * len(hyp)
            continue
        duration = right - left
        scored += duration * len(ref)
        if not hyp:
            miss += duration * len(ref)
        elif len(hyp) > len(ref):
            false_alarm += duration * (len(hyp) - len(ref))
        elif mapping.get(ref[0]) != hyp[0]:
            confusion += duration
    errors = miss + false_alarm + confusion
    return {"status": "measured" if scored else "no_scored_speech",
            "der": round(errors / scored, 6) if scored else None,
            "miss_seconds": round(miss, 6),
            "false_alarm_seconds": round(false_alarm, 6),
            "confusion_seconds": round(confusion, 6),
            "speaker_mapping": mapping,
            "scored_seconds": round(scored, 6),
            "collar_seconds": collar_seconds,
            "method": "time-weighted diarization error rate"}


def voice_quality_record(profile: dict, source: str,
                         start: float | None = None,
                         end: float | None = None) -> dict:
    """Reference existing Praat measurements without inventing a label."""
    record = {"source": source,
              "method": "Praat via speech_advanced_pipeline",
              "status": "measured" if profile else "unmeasured",
              "measurements": dict(profile or {}),
              "interpretation": "none; acoustic measurements only",
              "boundary_uncertainty": {
                  "estimate_ms": None,
                  "status": "uncalibrated" if start is not None and end is not None
                  else "interval_unavailable",
              }}
    if start is not None and end is not None:
        if end < start:
            raise ValueError("voice-quality interval end precedes start")
        record["start"] = float(start)
        record["end"] = float(end)
    return record
