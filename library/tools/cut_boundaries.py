"""Deterministic edit-point refinement for speech cuts (F04).

A passage the 2.02 aligner resolves already starts and ends on word
edges, but ON a word edge is not the same as IN silence: a cut flush
against the next word clips its attack when the aligner is a few tens
of milliseconds off, and it tells nobody when it lands inside an
action, on a motion apex, or against continuous speech. This module
pads each edge a bounded step into the surrounding silence and says
what it found there.

What it reads is passed in - word timings plus whatever evidence the
caller already holds (clip duration, scene boundaries, audio events,
motion peaks, action windows). What is absent is NAMED in
``unmeasured`` and never filled in: an unmeasured signal moves no edge.
Every returned edge carries the evidence ids that moved or flagged it,
so a reader can ask what decided the cut.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# A pad is a step into silence, not a re-timing: an edge moves outward
# by half the gap to the nearest excluded word, capped here. Half the
# gap can never cross the midpoint into the neighbour's speech, so two
# adjacent refined cuts can never claim the same audio for this reason.
# Grounded on calibration dialogue (F04, ffmpeg silencedetect at -35dB
# around the refedit-b calibration edges): inter-word gaps run
# 0.05-0.4s with pauses to 1.5s, and the reference word-edge cuts sit
# at or inside them - so the 0.15s cap keeps a pad inside real gaps
# while the 0.08s floor below flags cuts against continuous speech.
# Held-out is reported once, never tuned on.
MAX_SILENCE_PAD_S = 0.15

# Below this gap to the nearest excluded word, the cut sits against
# continuous speech and likely carries an audible discontinuity
# (clipped attack, room-tone break). Reported, never refused: how much
# silence a cut needs is the planner's judgement, this module's is the
# measurement.
MIN_SILENCE_S = 0.08

# An edge within this of a word edge counts as on that edge - the same
# tolerance the EVAL-04 agent-reference scorer treats as the same
# boundary, so the two measurements agree on what "on the word" means.
WORD_EDGE_TOL_S = 0.04

# A motion apex/onset or audio event within this of an edge is reported
# as near the cut. A scene boundary within SCENE_NEAR_S is reported as
# a motivated cut (the picture already changes there), not a risk.
MOTION_NEAR_S = 0.25
AUDIO_EVENT_NEAR_S = 0.25
SCENE_NEAR_S = 0.50
PROSODY_NEAR_S = 0.25
PROSODY_EMPHASIS_THRESHOLD = 0.5


def _word_span(word: Mapping[str, Any]) -> tuple[float, float] | None:
    """(start, end) of one word dict, in either key vocabulary."""
    try:
        start = word.get("source_start", word.get("start"))
        end = word.get("source_end", word.get("end"))
        start_f, end_f = float(start), float(end)
    except (TypeError, ValueError):
        return None
    if end_f < start_f:
        return None
    return (start_f, end_f)


def flatten_words(
    regions_or_words: Sequence[Mapping[str, Any]],
) -> list[tuple[float, float]]:
    """All word spans in time order, from region dicts or bare word dicts."""
    spans: list[tuple[float, float]] = []
    for entry in regions_or_words or []:
        if not isinstance(entry, Mapping):
            continue
        words = entry.get("words")
        if isinstance(words, list):
            for word in words:
                if isinstance(word, Mapping):
                    span = _word_span(word)
                    if span is not None:
                        spans.append(span)
        else:
            span = _word_span(entry)
            if span is not None:
                spans.append(span)
    spans.sort()
    return spans


def _gap_to_nearest(
    edge: float, spans: Sequence[tuple[float, float]], side: str
) -> float | None:
    """Seconds from `edge` to the nearest word strictly on `side`.

    `side` is "before" (word ends at or before the edge) or "after"
    (word starts at or after it). A word containing the edge is not a
    neighbour on either side - it is a mid-word cut, reported by
    `validate_edge`, not measured here. None when no word lies there.
    """
    best: float | None = None
    for start, end in spans:
        if side == "before":
            if end <= edge + WORD_EDGE_TOL_S:
                gap = edge - end
                if gap >= 0 and (best is None or gap < best):
                    best = gap
        else:
            if start >= edge - WORD_EDGE_TOL_S:
                gap = start - edge
                if gap >= 0 and (best is None or gap < best):
                    best = gap
    return best


def _edge_in_word(
    edge: float, spans: Sequence[tuple[float, float]]
) -> tuple[float, float] | None:
    """The word span strictly containing `edge`, or None.

    Within WORD_EDGE_TOL_S of either boundary counts as on the edge,
    not inside - aligner float noise is not a mid-word cut.
    """
    for start, end in spans:
        if start + WORD_EDGE_TOL_S < edge < end - WORD_EDGE_TOL_S:
            return (start, end)
    return None


def _peaks_near(
    edge: float, peaks: Sequence[Mapping[str, Any]], tolerance: float
) -> list[Mapping[str, Any]]:
    """Peak/event dicts whose time sits within `tolerance` of `edge`."""
    near = []
    for peak in peaks or []:
        if not isinstance(peak, Mapping):
            continue
        try:
            time = float(peak.get("time", peak.get("start")))
        except (TypeError, ValueError):
            continue
        if abs(time - edge) <= tolerance:
            near.append(peak)
    return near


def _scenes_near(
    edge: float, boundaries: Sequence[Any], tolerance: float
) -> list[float]:
    """Scene boundary times within `tolerance` of `edge`."""
    near = []
    for boundary in boundaries or []:
        try:
            time = float(boundary)
        except (TypeError, ValueError):
            continue
        if abs(time - edge) <= tolerance:
            near.append(time)
    return near


def _edge_evidence_ids(
    edge_peaks: Sequence[Mapping[str, Any]],
    edge_events: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Evidence ids backing one edge's flags, in first-seen order."""
    ids: list[str] = []
    for record in list(edge_peaks) + list(edge_events):
        evidence_id = record.get("evidence_id", record.get("id"))
        if evidence_id is not None and evidence_id not in ids:
            ids.append(str(evidence_id))
    return ids


def refine_edge(
    edge: float,
    spans: Sequence[tuple[float, float]],
    side: str,
    *,
    bound: float | None = None,
    max_pad: float = MAX_SILENCE_PAD_S,
) -> dict[str, Any]:
    """Pad one edge a bounded step into silence.

    `side` is "head" (the cut's in-point: pad moves earlier into the
    gap before it) or "tail" (the out-point: pad moves later). The pad
    is half the gap to the nearest word on that side, capped at
    `max_pad`, and never crosses `bound` (the clip start for a head,
    the clip end for a tail - the handles the source actually has).
    A mid-word edge is NOT moved: only whoever ordered the trim owns a
    cut inside a word, and this module reports it instead.
    """
    gap = _gap_to_nearest(edge, spans, "before" if side == "head" else "after")
    mid_word = _edge_in_word(edge, spans)
    if mid_word is not None or gap is None:
        return {
            "edge": edge,
            "pad_applied": 0.0,
            "gap_seconds": gap,
            "mid_word": mid_word is not None,
        }
    pad = min(gap / 2.0, max_pad)
    moved = edge - pad if side == "head" else edge + pad
    if bound is not None:
        moved = max(moved, bound) if side == "head" else min(moved, bound)
    applied = abs(moved - edge)
    remaining = gap - applied
    return {
        "edge": round(moved, 3),
        "pad_applied": round(applied, 3),
        "gap_seconds": round(gap, 3),
        "gap_after_pad_seconds": round(max(remaining, 0.0), 3),
        "mid_word": False,
    }


def validate_edge(
    edge: float,
    spans: Sequence[tuple[float, float]],
    *,
    gap: float | None = None,
    action_windows: Sequence[Mapping[str, Any]] | None = None,
    motion_peaks: Sequence[Mapping[str, Any]] | None = None,
    audio_events: Sequence[Mapping[str, Any]] | None = None,
    scene_boundaries: Sequence[Any] | None = None,
    prosodic_words: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Flags for one cut edge, each naming the evidence behind it.

    `hard_cut` - the gap to excluded speech is below MIN_SILENCE_S.
    `mid_word` - the edge sits inside a word (a stated trim's cut).
    `action_crossing` - the edge lands inside a measured action window.
    `motion_peak_near` / `audio_event_near` - a measured peak or event
    within tolerance. `scene_at_cut` - a scene boundary within
    tolerance: the picture changes there anyway, so the cut is
    motivated, which is information, not a risk. `prosody_peak_near` -
    a measured emphasized word boundary is near the edit point.
    """
    flags: list[str] = []
    if gap is not None and gap < MIN_SILENCE_S:
        flags.append("hard_cut")
    if _edge_in_word(edge, spans) is not None:
        flags.append("mid_word")
    crossed: list[Mapping[str, Any]] = []
    if action_windows is not None:
        for window in action_windows:
            if not isinstance(window, Mapping):
                continue
            try:
                start = float(window["start"])
                end = float(window["end"])
            except (TypeError, ValueError, KeyError):
                continue
            if start <= edge <= end:
                flags.append("action_crossing")
                crossed.append(window)
                break
    near_motion = (
        _peaks_near(edge, motion_peaks, MOTION_NEAR_S)
        if motion_peaks is not None
        else []
    )
    if near_motion:
        flags.append("motion_peak_near")
    near_audio = (
        _peaks_near(edge, audio_events, AUDIO_EVENT_NEAR_S)
        if audio_events is not None
        else []
    )
    if near_audio:
        flags.append("audio_event_near")
    near_scenes = (
        _scenes_near(edge, scene_boundaries, SCENE_NEAR_S)
        if scene_boundaries is not None
        else []
    )
    if near_scenes:
        flags.append("scene_at_cut")
    near_prosody = []
    if prosodic_words is not None:
        for word in prosodic_words:
            if not isinstance(word, Mapping):
                continue
            try:
                start = float(word["start"])
                end = float(word["end"])
                emphasis = float(word["emphasis"])
            except (TypeError, ValueError, KeyError):
                continue
            if (emphasis >= PROSODY_EMPHASIS_THRESHOLD
                    and (abs(start - edge) <= PROSODY_NEAR_S
                         or abs(end - edge) <= PROSODY_NEAR_S)):
                near_prosody.append(word)
    if near_prosody:
        flags.append("prosody_peak_near")
    return {
        "flags": flags,
        "action_windows_crossed": [
            {
                "start": w.get("start"),
                "end": w.get("end"),
                "label": w.get("label", w.get("action")),
            }
            for w in crossed
        ],
        "motion_peaks_near": [
            {"time": p.get("time"), "kind": p.get("kind")}
            for p in near_motion
        ],
        "audio_events_near": [
            {"time": p.get("time", p.get("start")), "label": p.get("label", p.get("class"))}
            for p in near_audio
        ],
        "scene_boundaries_near": near_scenes,
        "prosodic_words_near": [
            {
                "word": word.get("word"),
                "start": word.get("start"),
                "end": word.get("end"),
                "emphasis": word.get("emphasis"),
            }
            for word in near_prosody
        ],
        "evidence_ids": _edge_evidence_ids(near_motion, near_audio),
    }


def refine_cut(
    start: float,
    end: float,
    clip_words: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Any] | None = None,
    *,
    max_pad: float = MAX_SILENCE_PAD_S,
    max_pad_head: float | None = None,
    max_pad_tail: float | None = None,
) -> dict[str, Any]:
    """Refine one cut's in/out points and validate both edges.

    `clip_words` is every timed word on the source clip (region dicts
    or bare word dicts) - the neighbours the gaps are measured
    against. `evidence` carries whatever else the caller holds; every
    key is optional and every absent one is named in `unmeasured`:

      - `clip_duration_seconds`: bounds the tail pad (the head is
        always clamped at zero - no cut starts before the source).
      - `action_windows`: [{start, end, label}] measured action spans.
        An edge inside one, or a span that starts inside the cut and
        ends past it (a truncated action), is flagged.
      - `motion_peaks`: [{time, kind}] motion onsets/apexes.
      - `audio_events`: [{time|start, label}] measured sound events.
      - `scene_boundaries`: [seconds] visual cut points.
      - `prosodic_words`: measured per-word emphasis rows from step 1.05.

    Returns the refined bounds plus a per-edge account: what moved,
    what flagged, which evidence decided, and what was unmeasured.
    `max_pad_head`/`max_pad_tail` cap one edge below `max_pad` (step
    2.02's zone fit); absent they both read `max_pad`.
    """
    evidence = evidence or {}
    spans = flatten_words(clip_words)
    duration = evidence.get("clip_duration_seconds")
    try:
        duration_f = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_f = None
    actions = evidence.get("action_windows")
    motions = evidence.get("motion_peaks")
    audio = evidence.get("audio_events")
    scenes = evidence.get("scene_boundaries")
    prosodic_words = evidence.get("prosodic_words")

    head = refine_edge(
        float(start), spans, "head", bound=0.0,
        max_pad=max_pad if max_pad_head is None else max_pad_head,
    )
    tail = refine_edge(
        float(end), spans, "tail", bound=duration_f,
        max_pad=max_pad if max_pad_tail is None else max_pad_tail,
    )
    new_start = head["edge"]
    new_end = tail["edge"]
    if new_end < new_start:
        # A whole sub-pad cut cannot invert: keep the word span the
        # aligner measured and say the pad did not fit.
        new_start, new_end = float(start), float(end)
        head["pad_applied"] = 0.0
        tail["pad_applied"] = 0.0

    head_check = validate_edge(
        new_start, spans, gap=head.get("gap_seconds"),
        action_windows=actions, motion_peaks=motions,
        audio_events=audio, scene_boundaries=scenes,
        prosodic_words=prosodic_words,
    )
    tail_check = validate_edge(
        new_end, spans, gap=tail.get("gap_seconds"),
        action_windows=actions, motion_peaks=motions,
        audio_events=audio, scene_boundaries=scenes,
        prosodic_words=prosodic_words,
    )
    span_flags: list[str] = []
    truncated: list[dict[str, Any]] = []
    if actions is not None:
        for window in actions:
            if not isinstance(window, Mapping):
                continue
            try:
                window_start = float(window["start"])
                window_end = float(window["end"])
            except (TypeError, ValueError, KeyError):
                continue
            if new_start < window_start < new_end < window_end:
                span_flags.append("truncated_action")
                truncated.append({
                    "start": window.get("start"),
                    "end": window.get("end"),
                    "label": window.get("label", window.get("action")),
                })
                break
    unmeasured = [
        name
        for name, present in (
            ("clip_duration", duration_f is not None),
            ("action_windows", actions is not None),
            ("motion_peaks", motions is not None),
            ("audio_events", audio is not None),
            ("scene_boundaries", scenes is not None),
            ("prosody", prosodic_words is not None),
        )
        if not present
    ]
    return {
        "source_start": new_start,
        "source_end": new_end,
        "head": {**head, **head_check},
        "tail": {**tail, **tail_check},
        "span_flags": span_flags,
        "truncated_actions": truncated,
        "unmeasured": unmeasured,
    }
