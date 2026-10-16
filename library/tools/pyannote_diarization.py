"""pyannote Community-1 adapter: pipeline output to evaluator turns.

AUD-023 contract work only: this module maps a pyannote ``Annotation``
(``pyannote/speaker-diarization-community-1``, CC-BY-4.0, gated access)
onto the turn rows ``library/tools/diarization_eval.py`` scores. The
weights are gated and were NOT downloaded (no ``HF_TOKEN``, conditions
unaccepted), so nothing here is measured - it exists so the day access
is authorized the bakeoff needs no new glue.

Overlap is preserved as overlapping single-speaker turns, which the
evaluator already scores as multi-speaker time. Probabilities are stored
raw (AUD-028: no calibration claim); uncertain spans are marked, never
guessed (AUD-027).

Heavy imports are function-local: importing this module never requires
pyannote, torch, or network, so routing and record-keeping run anywhere
and only the load path refuses without them.
"""

from __future__ import annotations

import math
import os
from typing import Any, Mapping

COMMUNITY_ONE_REPO = "pyannote/speaker-diarization-community-1"
"""Hugging Face repo of the candidate. Gated: accepted conditions + token."""

LICENSE_CLASS = "CC-BY-4.0 weights, gated access (accepted conditions + token)"
"""License class per the model card; see the diarization benchmark doc."""


class PyannoteUnavailable(RuntimeError):
    """pyannote Community-1 cannot run here: package, token, or weights."""


def require_access(token: str | None = None) -> str:
    """Return a token to attempt authorized access with, or refuse.

    Token presence alone authorizes nothing: the gate also needs the
    repo's accepted conditions. Absence refuses before any network, so
    this lane never attempts a download it was not granted.
    """
    value = token if token is not None else os.environ.get("HF_TOKEN")
    if not value:
        raise PyannoteUnavailable(
            f"{COMMUNITY_ONE_REPO} needs authorized access: set HF_TOKEN "
            "after accepting the repo's conditions; no weights downloaded"
        )
    return value


def load_pipeline(token: str | None = None):  # pragma: no cover - needs weights
    """Instantiate the Community-1 pipeline. Refuses without access.

    Kept behind this call (never at import) so the contract mapping
    below stays testable without the gated weights on disk.
    """
    require_access(token)
    from pyannote.audio import Pipeline

    return Pipeline.from_pretrained(COMMUNITY_ONE_REPO)


def build_turns(
    annotation: Any,
    *,
    probabilities: Mapping[str, float] | None = None,
    uncertain_segments: list[tuple[float, float]] | None = None,
    min_duration: float = 0.0,
) -> list[dict]:
    """Map a pyannote Annotation to evaluator turn rows.

    Reads ``annotation.itertracks(yield_label=True)`` - (segment, track,
    label) with ``segment.start``/``segment.end`` in seconds - and merges
    contiguous same-label segments into turns. Overlapping labels stay
    overlapping turns. Returns rows the evaluator accepts directly:
    ``start``/``end``/``speaker``, optional ``uncertain`` and raw
    ``speaker_probabilities``.
    """
    if min_duration < 0 or not math.isfinite(min_duration):
        raise PyannoteUnavailable("min_duration must be finite and non-negative")
    probabilities = dict(probabilities or {})
    for label, value in probabilities.items():
        if (not isinstance(label, str) or not label
                or isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 1):
            raise PyannoteUnavailable(
                f"probability for {label!r} must be a number in 0..1")
    uncertain_segments = [(float(s), float(e)) for s, e in (uncertain_segments or ())]
    for start, end in uncertain_segments:
        if (not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start):
            raise PyannoteUnavailable("uncertain segments need finite start < end")

    segments: dict[str, list[tuple[float, float]]] = {}
    order: list[str] = []
    for segment, _track, label in annotation.itertracks(yield_label=True):
        start, end = float(segment.start), float(segment.end)
        if (not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end <= start):
            raise PyannoteUnavailable(f"track {label!r} has invalid interval")
        if end - start < min_duration:
            continue
        if label not in segments:
            segments[label] = []
            order.append(label)
        segments[label].append((start, end))

    turns: list[dict] = []
    for label in order:
        merged: list[list[float]] = []
        for start, end in sorted(segments[label]):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            row: dict[str, Any] = {"start": start, "end": end, "speaker": label}
            if label in probabilities:
                row["speaker_probabilities"] = {label: probabilities[label]}
            if any(s < end and start < e for s, e in uncertain_segments):
                row["uncertain"] = True
            turns.append(row)
    turns.sort(key=lambda row: (row["start"], row["end"]))
    return turns
