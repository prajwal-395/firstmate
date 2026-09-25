"""The measured non-speech sound events, read from what step 1.04 banks.

Step 1.04's per-clip summary carries `sound_events` - timed AudioSet
labels from PANNs Cnn14 DecisionLevelMax
(`library/tools/analysis/sound_event_pipeline.py`) - in SOURCE seconds
beside `sound_event_method`. This module is the single place that knows
that shape, so there is one name to change if it ever moves, exactly
as `library/tools/music_sections.py` is for the section grid and
`library/tools/beat_grid.py` for the beat series.

**Time domain.** Event times are measured on the SOURCE clip's clock,
like motion peaks. The anchor resolves them through
`source_to_timeline` at post-bridge time - the same mapping the word
and motion forms use - so nothing here needs the spine.

**Addressable events.** Labels arrive verbatim from the model's own
AudioSet vocabulary; a label no clip measured (a laugh on a run with
no laughter) is reported with what IS present, never coined. Speech
never appears: the producer withholds voiced-speech classes because
the transcript times speech to the word. An unmeasured clip is an
empty reading: callers that PLACE refuse (the anchor), callers that
SHOW state the absence in one line (the view).

`tests/test_sound_events.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

#: A clip that measured carries its producer's method name; anything
#: else (missing, `unmeasured: <reason>`) is not a measurement.
UNMEASURED_PREFIX = "unmeasured"


def measured(summary: Optional[Dict[str, Any]]) -> bool:
    """Whether this clip's summary carries a sound-event measurement."""
    if not isinstance(summary, dict):
        return False
    method = summary.get("sound_event_method")
    if not isinstance(method, str) or not method:
        return False
    if method == UNMEASURED_PREFIX or method.startswith(
            UNMEASURED_PREFIX + ":"):
        return False
    return isinstance(summary.get("sound_events"), list)


def events(summary: Optional[Dict[str, Any]]) -> List[dict]:
    """The clip's measured events, in source seconds, in time order.

    Empty where the clip is unmeasured or measured nothing - empty
    means "do not show, do not place", which is what every caller
    already does with an empty motion peak list.
    """
    if not measured(summary):
        return []
    rows = []
    for event in summary.get("sound_events") or []:
        if not isinstance(event, dict):
            continue
        try:
            start = float(event["start"])
            end = float(event["end"])
            confidence = float(event.get("confidence", 0.0))
        except (TypeError, ValueError, KeyError):
            continue
        label = event.get("label")
        if not isinstance(label, str) or not label.strip() or end < start:
            continue
        rows.append({
            "label": label,
            "start_seconds": start,
            "end_seconds": end,
            "confidence": confidence,
        })
    rows.sort(key=lambda r: (r["start_seconds"], r["end_seconds"]))
    return rows


def present_labels(summary: Optional[Dict[str, Any]]) -> List[str]:
    """Labels a plan entry may name on this clip, in time order."""
    seen = []
    for event in events(summary):
        if event["label"] not in seen:
            seen.append(event["label"])
    return seen


def events_in_block(summary: Optional[Dict[str, Any]],
                    source_start: float,
                    source_end: float) -> List[dict]:
    """Measured events overlapping a block's source range.

    The ±0.1 s tolerance is the motion anchor's (`sub_block_anchor`
    admits a peak just outside the range) - an event starting on the
    cut is addressable from either side.
    """
    return [e for e in events(summary)
            if e["start_seconds"] <= source_end + 0.1
            and e["end_seconds"] >= source_start - 0.1]


def find_events(summary: Optional[Dict[str, Any]],
                label: str) -> Tuple[List[dict], List[str]]:
    """Events named `label`, plus labels the clip carries.

    Matching is case-insensitive against the model's verbatim labels;
    the returned rows keep the verbatim form. Returns `(matches,
    present)`: the rows named (empty where none) and the present
    labels for the refusal fix. Never raises for a missing label -
    the caller owns the refusal shape.
    """
    want = (label or "").strip().lower()
    rows = [e for e in events(summary)
            if e["label"].lower() == want] if want else []
    return rows, present_labels(summary)
