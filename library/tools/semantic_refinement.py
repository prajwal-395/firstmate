"""A01: connect semantic refinement candidates to edit execution.

The punch list's A01: the sparse semantic-discovery pass already
schedules where the VLM looks (``library/tools/semantic_window_planner.py``,
consumed by ``library/tools/analysis/vision_pipeline_v3.py``), and its
plan already emits ``refinement_candidates`` - uncovered high-priority
or unmeasured bins. Nothing consumed them: the candidates were written
into the profile and the run ended. This module is the missing loop:

    rank candidates (activity, scene changes, speech, uncertainty,
    query relevance) -> select high-value ones inside a model-call
    budget -> run follow-up analysis only for those -> feed discovered
    events back into the source-memory index -> emit Q01 evidence
    references so the events reach edit execution -> stop when the
    search is resolved or the budget is spent, recording every skip.

What this module does not do, and why:

* It never replans the discovery pass. ``plan_semantic_windows`` owns
  ``window_starts_s``; this module reads ``refinement_candidates``
  and the plan's own ``window_seconds`` and changes neither. The
  sparse pass is preserved by construction, not by discipline.
* It never turns unsampled time into evidence. A candidate is a
  request to inspect, not a claim an event exists there (the
  planner's ``unseen_event_policy``); only an analyzed window's
  measured segments become events.
* It loads no model, decodes no media, spawns no subprocess. The
  follow-up analysis arrives as an injected ``analyze(start_s,
  end_s)`` callable - in production the shim-backed window analysis
  behind the same ``window_analyzer(start_s, end_s, source_file)``
  signature ``project_model_acquisition`` uses, which already holds
  its admission (Gemma only through the shared shim at
  ``http://127.0.0.1:8080``, never in-process). A lane that cannot
  offer an analyzer still gets ranking, selection and skips; the
  selected rows are recorded ``no_analyzer`` instead of silently
  dropped.
* It writes into no slot itself. The discovered scene segments reach
  the source-memory index through an injected ``feed_back(start_s,
  end_s, scenes, measured)`` callable - in production
  ``source_memory.merge_scene_window``, the writer built for merging
  one measured window without replacing source-wide records. Without
  one, events stay in the run record marked unfiled, never claimed
  filed.

The execution half is the Q01 evidence contract
(``library/tools/edit_evidence_contract.py``, PR 3210):
``reference_for_refined_event`` grounds each discovered event as an
``EvidenceReference`` a proposed edit can cite, so
``audit_reference`` reads it complete. A refined VLM scene segment
carries no producer score, so its uncertainty is maximal - the
producer asserted nothing (the same rule as
``evidence_contract.UNCERTAINTY_UNSTATED``): the reference is
complete and traceable, and it never executes alone (the Q01
autonomy bar refuses it to a person). A refined instrument boundary
(``scene_boundary``) files as measured and can go further.

Reach it as ``python3 -m library.tools.semantic_refinement --help``;
``rank`` scores a recorded window plan, ``audit`` reports the
before/after grounding of recorded decisions with denominators.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Any

from library.tools import edit_evidence_contract as evidence_contract
from library.tools import source_reference

#: Version of this module's run-record shape. Readers refuse newer.
REFINEMENT_CONTRACT_VERSION = 1

#: The run record format stamp.
REFINEMENT_FORMAT = "ren.semantic-refinement/1"

#: How each ranking signal weighs in the refinement score. Declared,
#: summing to exactly 1.0 (checked at import): moving a weight moves
#: which windows get a model call, so the blend is reviewable, never
#: tuned quietly.
REFINEMENT_SIGNAL_WEIGHTS = {
    "activity": 0.25,
    "scene_change": 0.20,
    "speech": 0.20,
    "uncertainty": 0.25,
    "query_relevance": 0.10,
}

_SIGNALS = tuple(REFINEMENT_SIGNAL_WEIGHTS)


def _check_weights() -> None:
    total = sum(REFINEMENT_SIGNAL_WEIGHTS.values())
    if abs(total - 1.0) > 1e-9:
        raise ValueError(
            f"REFINEMENT_SIGNAL_WEIGHTS must sum to 1.0, sums to {total}"
        )
    if any(weight < 0 for weight in REFINEMENT_SIGNAL_WEIGHTS.values()):
        raise ValueError("REFINEMENT_SIGNAL_WEIGHTS carries no negative weight")


_check_weights()

#: A refined VLM scene segment carries no producer score, so its Q01
#: uncertainty is maximal: the producer asserted nothing about how
#: sure it was. Complete and traceable, never autonomous alone.
REFINED_SCENE_UNCERTAINTY = 1.0

#: Below this score a candidate is not worth a model call. The
#: planner's own bar for emitting a candidate is 0.65-or-unmeasured;
#: refinement re-weighs with scene/speech/query evidence, so its bar
#: sits lower - a candidate the planner already flagged starts from
#: a suspicious posture, not from zero.
DEFAULT_VALUE_THRESHOLD = 0.30

#: The default ceiling on follow-up model calls per run. Four windows
#: is a second look at the plan's own refinement limit (12) without
#: approaching the discovery pass's own cost; the caller sets the
#: real budget, this keeps an unset one honest.
DEFAULT_BUDGET_MODEL_CALLS = 4


class RefinementError(ValueError):
    """A refinement run that cannot be what it claims to be."""


class StopReason(str, Enum):
    """Why the refinement loop stopped."""

    RESOLVED = "resolved"
    """Nothing worth a model call is left: every candidate is
    analyzed, already covered, or below the value threshold."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    """High-value candidates remain but the model-call budget is
    spent. The unanalyzed remainder stays listed, never analyzed."""

    NO_CANDIDATES = "no_candidates"
    """The plan emitted no refinement candidates: a fully covered
    search needs no refinement, and none is invented."""

    ANALYZER_UNAVAILABLE = "analyzer_unavailable"
    """High-value candidates exist but no follow-up analyzer was
    offered. Selection and skips are still recorded."""


#: Why a candidate never reached analysis. Every skipped row carries
#: one of these, so a run that analyzed nothing still says why.
SKIP_BELOW_THRESHOLD = "below_value_threshold"
SKIP_BUDGET_EXHAUSTED = "budget_exhausted"
SKIP_NO_ANALYZER = "no_analyzer"
SKIP_ALREADY_COVERED = "already_covered"
SKIP_ANALYSIS_FAILED = "analysis_failed"

SKIP_REASONS = (
    SKIP_BELOW_THRESHOLD,
    SKIP_BUDGET_EXHAUSTED,
    SKIP_NO_ANALYZER,
    SKIP_ALREADY_COVERED,
    SKIP_ANALYSIS_FAILED,
)


# ── Ranking ──────────────────────────────────────────────────────────


def _scene_boundary_times(temporal_index: Mapping[str, Any] | None) -> list[float]:
    """The measured cut points, tolerantly read.

    The temporal index records boundaries as ``{"timestamp": t}`` or
    ``{"time": t}``; either reads, and a row carrying neither is not a
    boundary. This mirrors the planner's own adapter, which is the
    authority on the shape.
    """
    times: list[float] = []
    for row in (temporal_index or {}).get("scene_boundaries", []):
        if not isinstance(row, Mapping):
            continue
        moment = row.get("timestamp", row.get("time", None))
        if isinstance(moment, bool) or not isinstance(moment, (int, float)):
            continue
        times.append(float(moment))
    return times


def _speech_overlap_fraction(candidate: Mapping[str, Any],
                              temporal_index: Mapping[str, Any] | None) -> float:
    """The share of the candidate span speech regions cover."""
    try:
        start = float(candidate["start"])
        end = float(candidate["end"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    width = end - start
    if width <= 0:
        return 0.0
    covered = 0.0
    for row in (temporal_index or {}).get("speech_regions", []):
        if not isinstance(row, Mapping):
            continue
        try:
            speech_start = float(row["start"])
            speech_end = float(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        overlap = min(end, speech_end) - max(start, speech_start)
        if overlap > 0:
            covered += overlap
    return min(1.0, covered / width)


def _query_relevance(candidate: Mapping[str, Any],
                     speech_texts: Sequence[Mapping[str, Any]],
                     query_terms: Sequence[str]) -> tuple[float, bool]:
    """The share of query terms spoken inside the candidate span.

    ``speech_texts`` are transcript segments with source-time bounds
    (``{"start", "end", "text"}``); a term matches as a case-folded
    substring. No query asked means no relevance claimed: 0.0 with
    ``asked=False``, never a default that reads as measured.
    """
    terms = [term.casefold() for term in query_terms if str(term).strip()]
    if not terms:
        return 0.0, False
    try:
        start = float(candidate["start"])
        end = float(candidate["end"])
    except (KeyError, TypeError, ValueError):
        return 0.0, True
    heard: list[str] = []
    for row in speech_texts:
        if not isinstance(row, Mapping):
            continue
        try:
            text_start = float(row["start"])
            text_end = float(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if text_end <= start or text_start >= end:
            continue
        text = row.get("text", "")
        if isinstance(text, str) and text:
            heard.append(text.casefold())
    if not heard:
        return 0.0, True
    corpus = " ".join(heard)
    hits = sum(1 for term in terms if term in corpus)
    return round(hits / len(terms), 6), True


def rank_refinement_candidates(
    plan: Mapping[str, Any],
    *,
    temporal_index: Mapping[str, Any] | None = None,
    speech_texts: Sequence[Mapping[str, Any]] = (),
    query_terms: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Score each refinement candidate on the five measured signals.

    ``plan`` is the ``plan_semantic_windows`` output; only its
    ``refinement_candidates`` are read - the discovery selection
    (``window_starts_s``) is never re-weighed here. Each row carries
    its per-signal contributions alongside the total, so a reader can
    see WHY a window earned a model call:

    * ``activity`` - the planner's own priority, but only when the
      candidate was emitted for measured activity (``activity_peak``).
      An unmeasured bin's priority IS its uncertainty, and one
      reading is filed under one axis, never both.
    * ``scene_change`` - 1.0 when a measured scene boundary falls in
      the candidate span, else 0.0.
    * ``speech`` - the fraction of the span speech regions cover.
    * ``uncertainty`` - 1.0 when the candidate was emitted as
      unmeasured (``unmeasured_activity``), else 0.0.
    * ``query_relevance`` - the fraction of query terms spoken in the
      span; 0.0 with ``query_asked=False`` when no query was asked.
    """
    if not isinstance(plan, Mapping):
        raise RefinementError("ranking reads the semantic window plan")
    candidates = plan.get("refinement_candidates", [])
    if not isinstance(candidates, list):
        raise RefinementError("the plan's refinement_candidates is not a list")
    boundaries = _scene_boundary_times(temporal_index)
    ranked: list[dict[str, Any]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise RefinementError("a refinement candidate must be a mapping")
        try:
            start = float(candidate["start"])
            end = float(candidate["end"])
            priority = float(candidate.get("priority", 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise RefinementError(
                f"a refinement candidate names a numeric span: {exc}"
            ) from exc
        reason = str(candidate.get("reason", ""))
        activity = priority if reason == "activity_peak" else 0.0
        scene_change = (
            1.0 if any(start <= moment < end for moment in boundaries) else 0.0
        )
        speech = _speech_overlap_fraction(candidate, temporal_index)
        uncertainty = 1.0 if reason == "unmeasured_activity" else 0.0
        query_relevance, query_asked = _query_relevance(
            candidate, speech_texts, query_terms)
        signals = {
            "activity": round(activity, 6),
            "scene_change": scene_change,
            "speech": round(speech, 6),
            "uncertainty": uncertainty,
            "query_relevance": query_relevance,
        }
        score = round(
            sum(signals[name] * REFINEMENT_SIGNAL_WEIGHTS[name]
                for name in _SIGNALS), 6)
        ranked.append({
            "start": start,
            "end": end,
            "priority": round(priority, 6),
            "reason": reason,
            "signals": signals,
            "query_asked": query_asked,
            "score": score,
        })
    ranked.sort(key=lambda row: (-row["score"], row["start"]))
    return ranked


# ── Selection ────────────────────────────────────────────────────────


def _inside_analyzed(start: float, end: float,
                     analyzed_windows: Sequence[Sequence[float]]) -> bool:
    """Whether an analyzed window already covers the candidate span."""
    for window in analyzed_windows:
        try:
            left, right = float(window[0]), float(window[1])
        except (IndexError, TypeError, ValueError):
            continue
        if left <= start and end <= right:
            return True
    return False


def select_for_refinement(
    ranked: Sequence[Mapping[str, Any]],
    *,
    budget_model_calls: int = DEFAULT_BUDGET_MODEL_CALLS,
    value_threshold: float = DEFAULT_VALUE_THRESHOLD,
    analyzed_windows: Sequence[Sequence[float]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split ranked candidates into selected and skipped-with-reason.

    Above the value threshold and inside the budget: selected, best
    score first. A candidate an analyzed window already covers is
    skipped as ``already_covered`` - re-analyzing measured time
    spends model calls to re-learn what is stored. Above the
    threshold but past the budget: ``budget_exhausted``. Below the
    threshold: ``below_value_threshold``. The two skipped lists are
    disjoint from the selected one, and every input row lands in
    exactly one output list.
    """
    if not isinstance(budget_model_calls, int) or budget_model_calls < 0:
        raise RefinementError("budget_model_calls is a non-negative integer")
    if not 0.0 <= float(value_threshold) <= 1.0:
        raise RefinementError("value_threshold sits in 0..1")
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for candidate in ranked:
        row = dict(candidate)
        start = float(row["start"])
        end = float(row["end"])
        if _inside_analyzed(start, end, analyzed_windows):
            row["skip_reason"] = SKIP_ALREADY_COVERED
            skipped.append(row)
        elif float(row["score"]) < float(value_threshold):
            row["skip_reason"] = SKIP_BELOW_THRESHOLD
            skipped.append(row)
        elif len(selected) < budget_model_calls:
            selected.append(row)
        else:
            row["skip_reason"] = SKIP_BUDGET_EXHAUSTED
            skipped.append(row)
    return selected, skipped


# ── Q01 references for refined events ────────────────────────────────


def _source_ids(content_digest: str | None,
                source: Mapping[str, Any] | None) -> dict[str, str]:
    """Asset, stream and clock ids for refined observations.

    An explicit ``source`` mapping wins; otherwise the ids derive
    from the content digest through the universal source reference -
    the same derivation the lanes use, never an invented string.
    """
    explicit = dict(source or {})
    if explicit.get("asset_id") and explicit.get("stream_id") \
            and explicit.get("clock_id"):
        return {
            "asset_id": str(explicit["asset_id"]),
            "stream_id": str(explicit["stream_id"]),
            "clock_id": str(explicit["clock_id"]),
        }
    if not content_digest:
        raise RefinementError(
            "refined observations need a content digest or an explicit source")
    return {
        "asset_id": source_reference.asset_id_for_digest(content_digest),
        "stream_id": source_reference.stream_id_for_digest(content_digest, 0),
        "clock_id": source_reference.clock_id_for_digest(content_digest),
    }


def reference_for_refined_event(
    event: Mapping[str, Any],
    observation_id: str,
    *,
    content_digest: str | None = None,
    source: Mapping[str, Any] | None = None,
    stream_id: str | None = None,
    clock_id: str | None = None,
    uncertainty: float | None = None,
) -> evidence_contract.EvidenceReference:
    """Ground one refined event as the reference a proposed edit cites.

    ``event`` is one discovered scene segment (``{"start", "end",
    ...}`` in source seconds) or one instrument boundary
    (``{"timestamp", ...}``). A scene segment is the VLM's reading -
    event kind ``scene``, filed inferred at maximal uncertainty (the
    producer scored nothing: complete, traceable, never autonomous
    alone). A boundary is the ffmpeg instrument's measurement -
    event kind ``scene_boundary``, filed measured at tick precision.
    What the caller cannot choose is refused the same way
    ``new_reference`` refuses it: the observation, the interval and
    the class travel together or not at all.
    """
    if not observation_id:
        raise RefinementError("a refined reference names its observation")
    ids = _source_ids(content_digest, source)
    if stream_id:
        ids["stream_id"] = stream_id
    if clock_id:
        ids["clock_id"] = clock_id
    observation = dict(event) if isinstance(event, Mapping) else {}
    start_tick: int | None = None
    end_tick: int | None = None
    if isinstance(event, Mapping) and "timestamp" in event \
            and "start" not in event:
        try:
            moment = float(event["timestamp"])
        except (TypeError, ValueError) as exc:
            raise RefinementError(
                f"a refined boundary names a numeric timestamp: {exc}") from exc
        # One tick holding the instant: the boundary names a moment,
        # and the universal clock counts whole ticks. The tick is the
        # placement of that moment, not a span invented around it.
        start_tick = math.floor(moment * source_reference.SOURCE_TICKS_PER_SECOND)
        end_tick = start_tick + 1
        event_kind = "scene_boundary"
        filed_uncertainty: float | None = None
    else:
        try:
            start_s = float(event["start"])
            end_s = float(event["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RefinementError(
                f"a refined event names a numeric span: {exc}") from exc
        if not end_s > start_s:
            raise RefinementError("a refined event spans non-empty time")
        start_tick, end_tick = source_reference.ticks_for_seconds(start_s, end_s)
        event_kind = "scene"
        filed_uncertainty = (
            REFINED_SCENE_UNCERTAINTY if uncertainty is None else float(uncertainty)
        )
        if not 0.0 <= filed_uncertainty <= 1.0:
            raise RefinementError("a refined uncertainty sits in 0..1")
    stored = {
        "id": observation_id,
        "asset_id": ids["asset_id"],
        "interval": {
            "clock_id": ids["clock_id"],
            "start": start_tick,
            "end": end_tick,
        },
    }
    return evidence_contract.reference_from_observation(
        stored,
        event_kind=event_kind,
        source=source_reference.interval(
            ids["stream_id"], ids["clock_id"], start_tick, end_tick),
        precision=evidence_contract.PRECISION_TICK,
        uncertainty=filed_uncertainty,
    )


# ── The loop ─────────────────────────────────────────────────────────


#: The follow-up analysis of one span. Production passes the
#: shim-backed window analysis (same signature VI-12 uses);
#: tests pass a stub. Returns the window result: ``{"measured":
#: bool, "scenes": [...], "boundaries": [...]}``.
AnalyzeWindow = Callable[[float, float], Mapping[str, Any]]

#: Filing of one analyzed window into the source-memory index.
#: Production passes ``source_memory.merge_scene_window`` bound to
#: the digest; tests pass a recorder.
FeedBack = Callable[[float, float, list, bool], None]


def run_refinement(
    plan: Mapping[str, Any],
    *,
    temporal_index: Mapping[str, Any] | None = None,
    speech_texts: Sequence[Mapping[str, Any]] = (),
    query_terms: Sequence[str] = (),
    budget_model_calls: int = DEFAULT_BUDGET_MODEL_CALLS,
    value_threshold: float = DEFAULT_VALUE_THRESHOLD,
    analyzed_windows: Sequence[Sequence[float]] = (),
    analyze: AnalyzeWindow | None = None,
    feed_back: FeedBack | None = None,
    content_digest: str | None = None,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the refinement loop over one recorded window plan.

    Ranks the plan's ``refinement_candidates``, selects the
    high-value ones inside the budget, analyzes only those, files
    what was discovered, and grounds each discovered event as a Q01
    reference. The run record carries ``ranked``, ``analyzed``,
    ``skipped`` (every skip with its reason), ``discovered_events``,
    ``observations`` (the stored rows the references cite),
    ``references`` (Q01 dicts by decision-free event id), the
    ``stop_reason`` and the ``resolved_fraction``.

    Stop rules, in order: no candidates (``no_candidates``);
    candidates worth analyzing but no analyzer
    (``analyzer_unavailable``); budget spent with high-value work
    remaining (``budget_exhausted``); otherwise ``resolved``. A
    failed analysis is recorded ``analysis_failed`` against that
    window and the loop continues - one window's failure moves to
    skipped, never aborts the run.
    """
    ranked = rank_refinement_candidates(
        plan, temporal_index=temporal_index,
        speech_texts=speech_texts, query_terms=query_terms)
    selected, skipped = select_for_refinement(
        ranked, budget_model_calls=budget_model_calls,
        value_threshold=value_threshold, analyzed_windows=analyzed_windows)
    skipped_rows = [
        {**row, "analyzed": False} for row in skipped
    ]
    if not ranked:
        stop = StopReason.NO_CANDIDATES
    elif selected and analyze is None:
        for row in selected:
            skipped_rows.append({**row, "skip_reason": SKIP_NO_ANALYZER,
                                 "analyzed": False})
        selected = []
        stop = StopReason.ANALYZER_UNAVAILABLE
    elif any(row.get("skip_reason") == SKIP_BUDGET_EXHAUSTED for row in skipped):
        stop = StopReason.BUDGET_EXHAUSTED
    else:
        stop = StopReason.RESOLVED

    analyzed_rows: list[dict[str, Any]] = []
    discovered: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    observations: dict[str, dict[str, Any]] = {}
    filed = 0
    calls = 0
    if analyze is not None:
        for row in selected:
            start, end = float(row["start"]), float(row["end"])
            calls += 1
            try:
                result = analyze(start, end)
            except Exception as exc:  # noqa: BLE001 - one window's
                # failure is a skipped row with a reason, never an
                # aborted run.
                skipped_rows.append({**row, "skip_reason": SKIP_ANALYSIS_FAILED,
                                     "analyzed": False,
                                     "failure": f"{type(exc).__name__}: {exc}"})
                continue
            measured = bool(result.get("measured", True))
            scenes = [dict(scene) for scene in (result.get("scenes") or [])
                      if isinstance(scene, Mapping)]
            boundaries = [dict(boundary)
                          for boundary in (result.get("boundaries") or [])
                          if isinstance(boundary, Mapping)]
            entry = {**row, "analyzed": True, "measured": measured,
                     "scenes": len(scenes), "boundaries": len(boundaries),
                     "filed": False}
            if measured and feed_back is not None:
                feed_back(start, end, scenes, measured)
                entry["filed"] = True
                filed += 1
            analyzed_rows.append(entry)
            for index, scene in enumerate(scenes):
                observation_id = (
                    f"refined-scene:{round(start * 1000)}:"
                    f"{round(end * 1000)}:{index}")
                try:
                    reference = reference_for_refined_event(
                        scene, observation_id,
                        content_digest=content_digest, source=source)
                except RefinementError as exc:
                    entry.setdefault("reference_failures", []).append(str(exc))
                    continue
                stored = {
                    "id": observation_id,
                    "asset_id": (
                        content_digest or (dict(source or {}).get("asset_id")) or ""
                    ),
                    "interval": {
                        "clock_id": reference.source["interval"]["clock_id"],
                        "start": reference.source["interval"]["start"],
                        "end": reference.source["interval"]["end"],
                    },
                }
                observations[observation_id] = stored
                discovered.append({
                    "observation_id": observation_id,
                    "window": [start, end],
                    "event": scene,
                })
                references.append(reference.to_dict())
        if stop is StopReason.RESOLVED or stop is StopReason.BUDGET_EXHAUSTED:
            remaining = [
                row for row in skipped_rows
                if row.get("skip_reason") == SKIP_BUDGET_EXHAUSTED
            ]
            stop = (StopReason.BUDGET_EXHAUSTED if remaining
                    else StopReason.RESOLVED)

    total = len(ranked)
    settled = sum(
        1 for row in list(analyzed_rows) + skipped_rows
        if row.get("analyzed")
        or row.get("skip_reason") in (SKIP_BELOW_THRESHOLD, SKIP_ALREADY_COVERED))
    return {
        "format": REFINEMENT_FORMAT,
        "version": REFINEMENT_CONTRACT_VERSION,
        "window_seconds": plan.get("window_seconds"),
        "task": plan.get("task"),
        "budget_model_calls": budget_model_calls,
        "value_threshold": value_threshold,
        "model_calls": calls,
        "ranked": ranked,
        "selected": [{key: row[key] for key in
                      ("start", "end", "score", "reason", "signals") if key in row}
                     for row in selected],
        "analyzed": analyzed_rows,
        "skipped": [
            {key: row[key] for key in
             ("start", "end", "score", "reason", "skip_reason",
              "failure", "analyzed") if key in row}
            for row in skipped_rows
        ],
        "discovered_events": discovered,
        "observations": observations,
        "references": references,
        "filed_windows": filed,
        "stop_reason": stop.value,
        "resolved_fraction": round(settled / total, 6) if total else 1.0,
        "candidates": total,
    }


# ── The measurement: recorded plans, before vs after ─────────────────


def audit_before_after(
    decisions: Sequence[Any],
    observations: Mapping[str, Any] | None,
    baseline_references: Mapping[str, Sequence[Any]] | None = None,
    refinement_references: Mapping[str, Sequence[Any]] | None = None,
    candidate_events: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The A01 measurement on recorded plans, with denominators.

    ``baseline`` audits the decisions as the engine carries them
    today - refinement candidates consumed by nothing, so nothing
    from refinement reaches execution. ``after`` overlays the
    references the refinement loop produced (per decision id,
    superseding the baseline where both name one). The headline is
    refinement candidates reaching execution with a complete
    evidence reference: ``refinement_complete_references`` before
    vs after, beside the grounded-edits denominators Q01 already
    reports.
    """
    baseline = baseline_references or {}
    refined = refinement_references or {}
    after_references = {**baseline, **refined}
    before = evidence_contract.audit_graph(
        decisions, observations, baseline, candidate_events)
    after = evidence_contract.audit_graph(
        decisions, observations, after_references, candidate_events)
    refined_events = {
        (reference["event_id"] if isinstance(reference, Mapping)
         else reference.event_id)
        for rows in refined.values() for reference in rows
    }
    refined_complete = sum(
        1 for row in after["decisions"] for reference in row["references"]
        if reference["complete"] and reference["event_id"] in refined_events)
    return {
        "format": REFINEMENT_FORMAT,
        "version": REFINEMENT_CONTRACT_VERSION,
        "before": before,
        "after": after,
        "refinement_candidates_reaching_execution": {
            "before": 0,
            "after": refined_complete,
        },
    }


# ── The CLI ──────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.semantic_refinement",
        description="rank semantic refinement candidates and audit "
        "their grounding (A01)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    rank = sub.add_parser("rank", help="rank a recorded window plan's candidates")
    rank.add_argument("plan", help="window plan JSON (plan_semantic_windows output)")
    rank.add_argument("--temporal-index", default="",
                      help="temporal index JSON (scene boundaries, speech regions)")
    rank.add_argument("--speech-texts", default="",
                      help="transcript segments JSON ([{start, end, text}])")
    rank.add_argument("--query", default="",
                      help="query terms, comma-separated, for query relevance")
    rank.add_argument("--threshold", type=float, default=DEFAULT_VALUE_THRESHOLD)
    rank.add_argument("--budget", type=int, default=DEFAULT_BUDGET_MODEL_CALLS)

    audit = sub.add_parser("audit", help="before/after grounding of recorded plans")
    audit.add_argument("decisions", help="decision document JSON")
    audit.add_argument("--observations", default="",
                       help="stored observations (JSON object by id)")
    audit.add_argument("--baseline", default="",
                       help="baseline references (JSON object by decision_id)")
    audit.add_argument("--refinement", default="",
                       help="refinement references (JSON object by decision_id)")
    args = parser.parse_args(argv)

    if args.command == "rank":
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        temporal_index = (
            json.loads(Path(args.temporal_index).read_text(encoding="utf-8"))
            if args.temporal_index else None)
        speech_texts = (
            json.loads(Path(args.speech_texts).read_text(encoding="utf-8"))
            if args.speech_texts else [])
        query_terms = [term.strip() for term in args.query.split(",") if term.strip()]
        ranked = rank_refinement_candidates(
            plan, temporal_index=temporal_index,
            speech_texts=speech_texts, query_terms=query_terms)
        selected, skipped = select_for_refinement(
            ranked, budget_model_calls=args.budget,
            value_threshold=args.threshold)
        print(json.dumps({"ranked": ranked, "selected": selected,
                          "skipped": skipped}, indent=2, ensure_ascii=False))
        return 0

    decisions_doc = json.loads(Path(args.decisions).read_text(encoding="utf-8"))
    if isinstance(decisions_doc, Mapping) and "decisions" in decisions_doc:
        from library.tools import editorial_decisions as _decisions

        decisions = _decisions.DecisionGraph.from_dict(decisions_doc).decisions
    elif isinstance(decisions_doc, list):
        decisions = decisions_doc
    else:
        print("the decision document is a list of decisions or a DecisionGraph",
              file=sys.stderr)
        return 2
    observations = (
        json.loads(Path(args.observations).read_text(encoding="utf-8"))
        if args.observations else None)
    baseline = (
        json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        if args.baseline else {})
    refined = (
        json.loads(Path(args.refinement).read_text(encoding="utf-8"))
        if args.refinement else {})
    report = audit_before_after(decisions, observations, baseline, refined)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
