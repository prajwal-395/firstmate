"""L5 query-directed evidence acquisition orchestrator.

Covers punch-list ACQ-003 through ACQ-031 (everything after the
requirement graph) and RET-035 through RET-037 (evidence-linked
intervals, edit planning, targeted reanalysis).

**What this is.** The loop around
:mod:`library.tools.requirement_graph` and
:mod:`library.tools.evidence_query`: check existing evidence first,
name what is missing, estimate its cost, pick the cheapest eligible
producer under edition/purpose/memory budgets, run targeted analysis
only on promising intervals, merge through the caller's refresh, and
re-evaluate until the conjunction holds or the budget is gone. Every
scheduled step records WHY it was acquired (ACQ-023).

**What this is not.** Not a producer: callers supply ``cached``,
``producers``, ``verifiers`` and ``refresh_model`` callbacks, exactly
like :mod:`library.tools.query_directed_acquisition`, whose executor
runs the measurement-lane steps. It never loads a model, reads media,
or downloads anything. Verification tasks (action/relationship
candidates) execute only through a caller-supplied verifier; without
one they are reported deferred, never faked.

License and memory are enforced at selection time: a producer whose
registry implementation is absent or unauthorized for the workload
purpose is refused with the reason named (fail closed, ACQ-007), and a
plan whose declared peak RSS exceeds the ceiling defers the heaviest
steps first (ACQ-008). Cost figures marked ``measured: True`` come
from checked-in benchmark docs; estimates are labeled estimates and
never presented as measurements.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:
    from library.tools import evidence_query, requirement_graph
    from library.tools.query_directed_acquisition import (
        execute_query_acquisition,
    )
except ImportError:  # imported as `tools.*` from inside library/
    from tools import evidence_query, requirement_graph
    from tools.query_directed_acquisition import execute_query_acquisition

SCHEMA_VERSION = 1

NO_LANE_YET = "no-lane-yet"

FIDELITY_DRAFT = "draft"
FIDELITY_STANDARD = "standard"
FIDELITY_EXHAUSTIVE = "exhaustive"
FIDELITIES = (FIDELITY_DRAFT, FIDELITY_STANDARD, FIDELITY_EXHAUSTIVE)

TIER_CHEAP = "cheap"
TIER_STANDARD = "standard"
TIER_HEAVY = "heavy"
TIERS_BY_FIDELITY = {
    FIDELITY_DRAFT: (TIER_CHEAP,),
    FIDELITY_STANDARD: (TIER_CHEAP, TIER_STANDARD),
    FIDELITY_EXHAUSTIVE: (TIER_CHEAP, TIER_STANDARD, TIER_HEAVY),
}

# Measured cost points (wall seconds and peak RSS) quoted from
# checked-in benchmark docs; entries without a measured point carry
# measured: False and a planning estimate that selection reports as
# such. Per-window cost scales with the window count, never with the
# source duration.
PRODUCER_CATALOG: dict[str, list[dict[str, Any]]] = {
    # Speech words are read from the migrated transcript: no model
    # pass, no marginal cost once M1 is migrated.
    "m1": [{
        "id": "m1-cached-transcript", "capability": "speech.transcribe",
        "tier": TIER_CHEAP, "cost_seconds_per_window": 0.0,
        "peak_rss_gb": 0.0, "window_seconds": None,
        "registry_implementation": None,
        "license_status": "unresolved", "purposes": ["permitted_research"],
        "measured": False,
        "note": "migrated-transcript lookup; no per-query inference",
    }],
    # Lexical retrieval narrowing over the footage index: numpy + BM25
    # half only, no embedder load (footage_query lexical path).
    "retrieval": [{
        "id": "retrieval-lexical", "capability": "video.retrieve",
        "tier": TIER_CHEAP, "cost_seconds_per_window": 0.05,
        "peak_rss_gb": 0.1, "window_seconds": None,
        "registry_implementation": None,
        "license_status": "unresolved", "purposes": ["permitted_research"],
        "measured": False,
        "note": "planning estimate: one 409x384-equivalent lexical pass",
    }],
    # M4 scene windows: 90 s per 10 s window, production path
    # (project_model_acquisition.M4_WINDOW_COST_SECONDS).
    "m4": [{
        "id": "m4-gemma-window", "capability": "visual.describe",
        "tier": TIER_HEAVY, "cost_seconds_per_window": 90.0,
        "peak_rss_gb": 4.0, "window_seconds": 10.0,
        "registry_implementation": "gemma.vision_4_12b_it_4bit",
        "license_status": None, "purposes": None,
        "measured": True,
        "note": "measured: VI-12 M4 window budget",
    }],
    # Named-object detection: OWLv2 3.64 s/frame measured on the MU-15
    # 40-frame set (object-detector-tiering-2026-10-14, recall 0.892);
    # Apple Vision nominations 0.16 s/frame as the cheap tier.
    "segmentation": [
        {"id": "segmentation-vision-nominations",
         "capability": "visual.detect",
         "tier": TIER_CHEAP, "cost_seconds_per_window": 1.6,
         "peak_rss_gb": 0.1, "window_seconds": 10.0,
         "registry_implementation": None,
         "license_status": "unresolved", "purposes": ["permitted_research"],
         "measured": True,
         "note": "measured 0.16 s/frame x 10 frames per 10 s window "
                 "(batched path); single-call reproduction on iPhone "
                 "frames 1.1-2.7 s/frame, so marginal 1.6 s budget "
                 "deferrals are expected variance, not failure; "
                 "nominations only, negatives stay not_yet_analysed"},
        {"id": "segmentation-owlv2-named",
         "capability": "visual.detect",
         "tier": TIER_STANDARD, "cost_seconds_per_window": 36.4,
         "peak_rss_gb": 1.2, "window_seconds": 10.0,
         "registry_implementation": None,
         "license_status": "unresolved", "purposes": ["permitted_research"],
         "measured": True,
         "note": "measured 3.64 s/frame x 10 frames per 10 s window "
                 "on the 40-still set; single-frame iPhone reproduction "
                 "6.3-12.5 s warm, 42.9-50.2 s cold, float32, peak "
                 "0.45-0.52 GB (this machine, 2026-10-16)"},
    ],
    # M5 sound events: migrated PANNs-scale lookup; no per-window
    # measured point is checked in.
    "m5": [{
        "id": "m5-cached-events", "capability": "audio.detect_event",
        "tier": TIER_CHEAP, "cost_seconds_per_window": 0.0,
        "peak_rss_gb": 0.0, "window_seconds": None,
        "registry_implementation": None,
        "license_status": "unresolved", "purposes": ["permitted_research"],
        "measured": False,
        "note": "migrated-event lookup; reanalysis cost unmeasured",
    }],
    # M7 event spans: migrated join; verification of open-vocabulary
    # candidates goes through verifier callbacks.
    "m7": [{
        "id": "m7-cached-spans", "capability": "video.locate_event",
        "tier": TIER_CHEAP, "cost_seconds_per_window": 0.0,
        "peak_rss_gb": 0.0, "window_seconds": None,
        "registry_implementation": None,
        "license_status": "unresolved", "purposes": ["permitted_research"],
        "measured": False,
        "note": "migrated-span lookup; candidate verification is separate",
    }],
    # M10 relationship candidates: migrated lookup only; physical
    # edges stay candidates until a verifier confirms them.
    "m10": [{
        "id": "m10-cached-candidates", "capability": "video.locate_event",
        "tier": TIER_CHEAP, "cost_seconds_per_window": 0.0,
        "peak_rss_gb": 0.0, "window_seconds": None,
        "registry_implementation": None,
        "license_status": "unresolved", "purposes": ["permitted_research"],
        "measured": False,
        "note": "migrated-candidate lookup; verification is separate",
    }],
    # OCR text tracks: migrated EasyOCR lookup; dense re-OCR cost is
    # unmeasured.
    "ocr_extraction": [{
        "id": "ocr-cached-tracks", "capability": "visual.ocr",
        "tier": TIER_CHEAP, "cost_seconds_per_window": 0.0,
        "peak_rss_gb": 0.0, "window_seconds": None,
        "registry_implementation": None,
        "license_status": "unresolved", "purposes": ["permitted_research"],
        "measured": False,
        "note": "migrated-track lookup; re-OCR cost unmeasured",
    }],
    # Bounded span verification (Gemma M8-style): 5 to 13 s per span
    # measured on the EVAL-04 calibration repeat; runs only through a
    # caller-supplied verifier.
    "verify": [{
        "id": "verify-bounded-span", "capability": "video.locate_event",
        "tier": TIER_STANDARD, "cost_seconds_per_window": 13.0,
        "peak_rss_gb": 4.0, "window_seconds": None,
        "registry_implementation": "gemma.vision_4_12b_it_4bit",
        "license_status": None, "purposes": None,
        "measured": True,
        "note": "measured 4.59 to 13.29 s per span on EVAL-04 "
                "calibration; caller verifier required",
    }],
}

# Prior probability that acquiring a lane resolves a requirement that
# names it: calibration-tunable ordering weights, documented defaults
# (ACQ-016/017). Speech text first (high selectivity, zero marginal
# cost), heavy VLM last. Chosen on calibration, reported once.
DEFAULT_SELECTIVITY = {
    "m1": 0.9, "retrieval": 0.8, "m5": 0.6, "ocr_extraction": 0.6,
    "segmentation": 0.5, "m7": 0.5, "m10": 0.4, "m4": 0.3,
    "verify": 0.5, "no-lane-yet": 0.0,
}


class AcquisitionError(ValueError):
    """An acquisition plan that cannot be built without guessing."""


def _registry_authorized(implementation_id: str, edition: str,
                         purpose: str,
                         registry: Mapping[str, Any] | None = None
                         ) -> tuple[bool, str]:
    """Whether the registry authorizes an implementation id.

    ``registry`` is a test seam: callers pass a fixture so planner
    behaviour does not depend on live license data. ``None`` reads
    the versioned registry file, the production path.
    """
    try:
        if requirement_graph.__name__.startswith("library"):
            from library.tools import model_registry
        else:  # pragma: no cover - import-path variant
            from tools import model_registry
        registry = registry if registry is not None else model_registry.load_registry()
        manifest = model_registry.manifest(edition, purpose, registry)
    except (OSError, ValueError) as exc:
        return False, f"model registry unreadable: {exc}"
    allowed = {item["id"] for item in manifest["implementations"]}
    if implementation_id in allowed:
        return True, "registry manifest authorizes this implementation"
    return False, ("implementation is absent from the "
                   f"{edition}/{purpose} manifest")


def select_producers(missing_lanes: Sequence[str], *,
                     edition: str = "personal",
                     purpose: str = "permitted_research",
                     fidelity: str = FIDELITY_STANDARD,
                     allow_escalation: bool = False,
                     resident_rss_gb: float = 0.0,
                     memory_ceiling_gb: float = 8.0,
                     registry: Mapping[str, Any] | None = None,
                     ) -> dict[str, dict[str, Any]]:
    """Cheapest eligible producer per missing lane (ACQ-005 to 008).

    Eligibility is fail-closed: a catalog entry naming a registry
    implementation must appear in that edition/purpose manifest; an
    entry with an unresolved inline license is refused for
    commercial_client work and allowed for permitted_research with
    the rationale recorded. Tiers above the fidelity budget are
    refused unless allow_escalation names them as second steps. Peak
    RSS beyond the ceiling defers the heaviest lanes first.
    """
    if fidelity not in FIDELITIES:
        raise AcquisitionError(
            f"fidelity is one of {FIDELITIES}, not {fidelity!r}")
    if edition not in ("commercial", "personal"):
        raise AcquisitionError(f"unknown edition {edition!r}")
    allowed_tiers = TIERS_BY_FIDELITY[fidelity]
    selection: dict[str, dict[str, Any]] = {}
    for lane in missing_lanes:
        entries = [dict(entry) for entry in PRODUCER_CATALOG.get(lane, ())]
        eligible: list[dict[str, Any]] = []
        refused: list[dict[str, Any]] = []
        for entry in entries:
            reasons: list[str] = []
            if entry["tier"] not in allowed_tiers and not (
                    allow_escalation and entry["tier"] == TIER_HEAVY):
                reasons.append(f"tier {entry['tier']} exceeds "
                               f"fidelity {fidelity}")
            implementation = entry.get("registry_implementation")
            if implementation:
                authorized, why = _registry_authorized(
                    implementation, edition, purpose, registry)
                entry["registry_note"] = why
                if not authorized:
                    reasons.append(why)
            else:
                status = entry.get("license_status")
                purposes = entry.get("purposes") or []
                if status != "unresolved" or purpose not in purposes:
                    reasons.append(
                        "no registry implementation and inline license "
                        f"{status!r} does not cover purpose {purpose!r}")
                elif purpose == "commercial_client":
                    reasons.append(
                        "unresolved inline license cannot authorize "
                        "commercial client work")
                else:
                    entry["registry_note"] = (
                        "no registry implementation yet: research "
                        "evaluation only, license review pending")
            if reasons:
                refused.append({"id": entry["id"], "reasons": reasons})
            else:
                eligible.append(entry)
        eligible.sort(key=lambda row: (row["cost_seconds_per_window"],
                                       row["peak_rss_gb"]))
        chosen = eligible[0] if eligible else None
        escalated = None
        if (allow_escalation and chosen is not None
                and chosen["tier"] != TIER_HEAVY):
            heavy = [row for row in eligible if row["tier"] == TIER_HEAVY]
            escalated = heavy[0] if heavy else None
        selection[lane] = {"lane": lane, "selected": chosen,
                           "escalation": escalated,
                           "alternatives": [row["id"] for row in eligible[1:]],
                           "refused": refused}
    # Memory admission across the chosen set: defer heaviest first.
    rss = float(resident_rss_gb)
    ranked = sorted(
        (lane for lane, row in selection.items() if row["selected"]),
        key=lambda lane: selection[lane]["selected"]["peak_rss_gb"],
        reverse=True)
    for lane in ranked:
        peak = float(selection[lane]["selected"]["peak_rss_gb"])
        if rss + peak > memory_ceiling_gb:
            selection[lane]["deferred_memory"] = (
                f"declared {peak} GB would exceed the {memory_ceiling_gb} "
                f"GB ceiling with {rss} GB resident")
            selection[lane]["selected"] = None
        else:
            rss += peak
    return selection


def _windows_for(scope: dict[str, Any], duration: float,
                 candidates: Sequence[Mapping[str, Any]] | None,
                 window_seconds: float | None,
                 exhaustive: bool, top_k: int) -> list[dict[str, float]]:
    """Targeted windows: retrieval candidates first (ACQ-009/026).

    Without candidates the scope window (or the whole source) is the
    interval. With candidates, the top-k scoring windows run first;
    exhaustive runs every candidate instead of the top-k.
    """
    if duration <= 0:
        raise AcquisitionError("duration_seconds must be positive")
    if candidates:
        rows = sorted(
            ({"start_s": round(float(row["start_s"]), 3),
              "end_s": round(float(row["end_s"]), 3),
              "score": float(row.get("score", 0.0))}
             for row in candidates
             if float(row["end_s"]) > float(row["start_s"])),
            key=lambda row: row["score"], reverse=True)
        picked = rows if exhaustive else rows[:max(1, top_k)]
        windows = [{"start_s": max(0.0, min(duration, row["start_s"])),
                    "end_s": max(0.0, min(duration, row["end_s"]))}
                   for row in picked]
        windows = [row for row in windows if row["end_s"] > row["start_s"]]
        if not windows:
            raise AcquisitionError("retrieval candidates fall outside "
                                   "the source")
        return windows
    start = float(scope.get("start_s", 0.0))
    end = float(scope.get("end_s", duration))
    start, end = max(0.0, start), min(duration, end)
    if end <= start:
        raise AcquisitionError("acquisition scope is empty")
    if window_seconds:
        windows = []
        cursor = start
        while cursor < end:
            windows.append({"start_s": round(cursor, 3),
                            "end_s": round(min(cursor + window_seconds,
                                               end), 3)})
            cursor += window_seconds
        return windows
    return [{"start_s": round(start, 3), "end_s": round(end, 3)}]


def plan_acquisition(compiled: dict[str, Any], model: Mapping[str, Any], *,
                     source_digest: str,
                     duration_seconds: float,
                     scope: dict[str, Any] | None = None,
                     edition: str = "personal",
                     purpose: str = "permitted_research",
                     fidelity: str = FIDELITY_STANDARD,
                     latency_budget_s: float | None = None,
                     exhaustive: bool = False,
                     allow_escalation: bool = False,
                     retrieval_candidates: Sequence[Mapping[str, Any]] | None = None,
                     top_k_windows: int = 3,
                     selectivity: Mapping[str, float] | None = None,
                     ledger: Mapping[str, Any] | None = None,
                     memory_ceiling_gb: float = 8.0,
                     resident_rss_gb: float = 0.0,
                     registry: Mapping[str, Any] | None = None,
                     ) -> dict[str, Any]:
    """Plan bounded acquisition for one compiled requirement graph.

    Reads existing evidence first (ACQ-003): every ``plan`` requirement
    is answered with :func:`evidence_query.evidence_for`, and only
    unsatisfied requirements schedule work (ACQ-004). Presents are
    kept, confidently_absent answers satisfy negated requirements and
    fail positive ones, and anything else names its missing lanes.
    Returns the acquisition plan with per-step cost estimates (ACQ-005),
    producer choice and rationale (ACQ-006/023), and the reasons lanes
    were refused or deferred.
    """
    if not source_digest or not str(source_digest).strip():
        raise AcquisitionError("source_digest identifies the source")
    if duration_seconds <= 0:
        raise AcquisitionError("duration_seconds must be positive")
    scope = dict(scope or {})
    priors = dict(DEFAULT_SELECTIVITY)
    priors.update(dict(selectivity or {}))

    answers: dict[str, dict[str, Any]] = {}
    missing: dict[str, list[str]] = {}
    satisfied: dict[str, bool] = {}
    failure: str | None = None
    for row in compiled.get("compiled", ()):
        req_id = row["requirement_id"]
        if row["kind"] == "plan":
            answer = evidence_query.evidence_for(dict(model), row["plan"])
            answers[req_id] = answer
            if row.get("negated"):
                if answer["status"] == evidence_query.CONFIDENTLY_ABSENT:
                    satisfied[req_id] = True
                elif answer["status"] == evidence_query.PRESENT:
                    satisfied[req_id] = False
                    failure = (f"{req_id} is present but the query "
                               f"requires its absence")
                else:
                    satisfied[req_id] = False
                    missing[req_id] = list(
                        answer.get("required_missing_lanes") or ())
            else:
                if answer["status"] == evidence_query.PRESENT:
                    satisfied[req_id] = True
                elif (answer["status"]
                        == evidence_query.CONFIDENTLY_ABSENT):
                    satisfied[req_id] = False
                    failure = (f"{req_id} is confidently absent: no "
                               f"acquisition can change a measured absence")
                else:
                    satisfied[req_id] = False
                    missing[req_id] = list(
                        answer.get("required_missing_lanes") or ())
        elif row["kind"] == "verify":
            satisfied[req_id] = False
            missing[req_id] = [row["lane"]]
        else:
            satisfied[req_id] = False
            lane = row.get("required_lane") or NO_LANE_YET
            missing[req_id] = [lane]

    if retrieval_candidates is None and "start_s" not in scope:
        # The conjunction is answered on overlapping intervals: where
        # the satisfied requirements already hold is where the rest
        # must be checked (ACQ-009). Absent requirements contribute no
        # interval, so they never widen the search.
        narrowed = _conjunction_windows(answers)
        if narrowed:
            retrieval_candidates = narrowed

    needed_lanes = sorted({lane for lanes in missing.values()
                           for lane in lanes})
    compiled_by_id_pre = {row["requirement_id"]: row
                          for row in compiled.get("compiled", ())}
    has_verify = any(compiled_by_id_pre.get(req_id, {}).get("kind")
                     == "verify" for req_id in missing)
    selection_lanes = [lane for lane in needed_lanes
                       if lane != NO_LANE_YET]
    if has_verify and "verify" not in selection_lanes:
        # Candidate verification is costed from the bounded-span
        # verification tier, not from the migrated-lookup tier.
        selection_lanes = sorted(set(selection_lanes) | {"verify"})
    selection = select_producers(
        selection_lanes,
        edition=edition, purpose=purpose, fidelity=fidelity,
        allow_escalation=allow_escalation,
        resident_rss_gb=resident_rss_gb,
        memory_ceiling_gb=memory_ceiling_gb,
        registry=registry)

    steps: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    compiled_by_id = {row["requirement_id"]: row
                      for row in compiled.get("compiled", ())}
    for req_id, lanes in missing.items():
        verify_task = (compiled_by_id.get(req_id, {}).get("kind")
                       == "verify")
        for lane in lanes:
            if lane == NO_LANE_YET:
                refused.append({
                    "requirement_id": req_id, "lane": lane,
                    "reason": "no producer exists for this requirement yet",
                })
                continue
            catalog_lane = "verify" if verify_task else lane
            choice = selection.get(catalog_lane, {})
            picked = choice.get("selected")
            if picked is None:
                refused.append({
                    "requirement_id": req_id, "lane": lane,
                    "reason": (choice.get("deferred_memory")
                               or "; ".join(
                                   f"{item['id']}: "
                                   f"{' / '.join(item['reasons'])}"
                                   for item in choice.get("refused", ()))
                               or "no eligible producer"),
                })
                continue
            window_size = (None if verify_task
                             else picked.get("window_seconds"))
            try:
                # Verification is scoped to the same promising windows:
                # candidates are verified where retrieval points, never
                # across the whole source by default.
                windows = _windows_for(
                    scope, duration_seconds, retrieval_candidates,
                    window_size, exhaustive, top_k_windows)
            except AcquisitionError as exc:
                refused.append({"requirement_id": req_id, "lane": lane,
                                "reason": str(exc)})
                continue
            for window in windows:
                key = ledger_key(source_digest, lane, window,
                                 picked["id"])
                if ledger is not None and key in ledger:
                    refused.append({
                        "requirement_id": req_id, "lane": lane,
                        "interval": window,
                        "reason": f"already acquired as {key} (ACQ-014)",
                    })
                    continue
                steps.append({
                    "requirement_id": req_id, "lane": lane,
                    "catalog_lane": catalog_lane,
                    "interval": window, "producer": picked["id"],
                    "capability": picked["capability"],
                    "tier": picked["tier"],
                    "verify_task": verify_task,
                    "cost_budget_seconds": round(
                        float(picked["cost_seconds_per_window"]), 3),
                    "peak_rss_gb": float(picked["peak_rss_gb"]),
                    "measured_cost": bool(picked["measured"]),
                    "why": (f"{req_id} is {answers.get(req_id, {}).get('status', 'unverified')}; "
                            f"{picked['id']} is the cheapest eligible "
                            f"producer ({picked.get('registry_note') or picked.get('note')})"),
                    "ledger_key": key,
                    "selectivity": float(priors.get(catalog_lane,
                                                         priors.get(lane,
                                                                    0.0))),
                })
    # Information value first: selectivity per second of inference,
    # with zero-cost cached lookups always ahead of model passes
    # (ACQ-016/017/018).
    for step in steps:
        cost = max(step["cost_budget_seconds"], 1e-6)
        step["value_per_second"] = round(step["selectivity"] / cost, 6)
    steps.sort(key=lambda row: (-row["value_per_second"],
                                row["cost_budget_seconds"]))
    estimated = round(sum(row["cost_budget_seconds"] for row in steps), 3)
    if latency_budget_s is not None and estimated > latency_budget_s:
        kept: list[dict[str, Any]] = []
        running = 0.0
        for step in steps:
            if running + step["cost_budget_seconds"] <= latency_budget_s:
                kept.append(step)
                running += step["cost_budget_seconds"]
            else:
                refused.append({
                    "requirement_id": step["requirement_id"],
                    "lane": step["lane"],
                    "interval": step["interval"],
                    "reason": (f"latency budget {latency_budget_s}s "
                               f"exhausted after {round(running, 3)}s"),
                })
        steps = kept
        estimated = round(running, 3)

    return {"schema_version": SCHEMA_VERSION,
            "action": "refine" if steps else "no_run",
            "graph_id": compiled.get("graph_id"),
            "source_digest": source_digest,
            "edition": edition, "purpose": purpose, "fidelity": fidelity,
            "exhaustive": exhaustive,
            "answers": answers, "satisfied": satisfied,
            "failure": failure,
            "selection": {lane: {"selected": (
                row["selected"]["id"] if row["selected"] else None),
                "alternatives": row["alternatives"],
                "refused": row["refused"],
                "escalation": (row["escalation"]["id"]
                               if row.get("escalation") else None)}
                for lane, row in selection.items()},
            "steps": steps, "refused": refused,
            "estimated_cost_seconds": estimated,
            "latency_budget_s": latency_budget_s,
            "compiled": list(compiled.get("compiled", ())),
            "narrowed_by_retrieval": bool(retrieval_candidates)}


def _conjunction_windows(
        answers: Mapping[str, dict[str, Any]]) -> list[dict[str, float]]:
    """Present-answer intervals as implicit retrieval candidates."""
    windows: list[dict[str, float]] = []
    for answer in answers.values():
        if answer.get("status") != evidence_query.PRESENT:
            continue
        for row in answer.get("results") or ():
            interval = row.get("interval") or {}
            if (isinstance(interval.get("start_s"), (int, float))
                    and isinstance(interval.get("end_s"), (int, float))
                    and interval["end_s"] > interval["start_s"]):
                windows.append({"start_s": round(interval["start_s"], 3),
                                "end_s": round(interval["end_s"], 3),
                                "score": 1.0})
    merged: list[dict[str, float]] = []
    for row in sorted(windows, key=lambda item: item["start_s"]):
        if merged and row["start_s"] <= merged[-1]["end_s"]:
            merged[-1]["end_s"] = max(merged[-1]["end_s"], row["end_s"])
        else:
            merged.append(dict(row))
    return merged


def plan_escalation(prior_plan: Mapping[str, Any],
                      answers_after: Mapping[str, dict[str, Any]], *,
                      latency_budget_s: float | None = None,
                      ledger: Mapping[str, Any] | None = None,
                      ) -> dict[str, Any]:
    """Second-round steps for requirements the cheap tier left open.

    Selective escalation (ACQ-019) is a new round, not a first-round
    guess: only requirements still unsatisfied after the previous
    execution, on lanes whose selection recorded an escalation
    producer, schedule work. Windows reuse the prior round's intervals
    for that lane (still the promising set); ledger hits dedup; the
    latency budget applies to the new round alone.
    """
    compiled_by_id = {row["requirement_id"]: row
                      for row in prior_plan.get("compiled", ())}
    _TIER_RANK = {TIER_CHEAP: 0, TIER_STANDARD: 1, TIER_HEAVY: 2}
    prior_windows: dict[str, list[dict[str, float]]] = {}
    prior_tier: dict[str, int] = {}
    for step in prior_plan.get("steps") or ():
        prior_windows.setdefault(step["lane"], [])
        if step["interval"] not in prior_windows[step["lane"]]:
            prior_windows[step["lane"]].append(step["interval"])
        rank = _TIER_RANK.get(step.get("tier"), 0)
        prior_tier[step["lane"]] = max(prior_tier.get(step["lane"], 0),
                                       rank)

    lane_of_requirement: dict[str, str] = {}
    for step in prior_plan.get("steps") or ():
        lane_of_requirement.setdefault(step["requirement_id"],
                                       step["lane"])

    steps: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for req_id, answer in answers_after.items():
        row = compiled_by_id.get(req_id, {})
        status = answer.get("status")
        satisfied = (status == evidence_query.PRESENT if not row.get("negated")
                     else status == evidence_query.CONFIDENTLY_ABSENT)
        if satisfied:
            continue
        lane = lane_of_requirement.get(req_id)
        if lane is None:
            refused.append({"requirement_id": req_id, "lane": None,
                            "reason": "no prior lane to escalate from"})
            continue
        if lane == "verify" or lane not in PRODUCER_CATALOG:
            refused.append({"requirement_id": req_id, "lane": lane,
                            "reason": "no stronger producer cataloged "
                                      "for this lane"})
            continue
        stronger = [entry for entry in PRODUCER_CATALOG[lane]
                    if _TIER_RANK.get(entry["tier"], 0)
                    > prior_tier.get(lane, 0)]
        if not stronger:
            refused.append({"requirement_id": req_id, "lane": lane,
                            "reason": "the catalog holds no heavier tier "
                                      "for this lane"})
            continue
        picked = min(stronger,
                     key=lambda item: item["cost_seconds_per_window"])
        for window in prior_windows.get(lane, ()):
            key = ledger_key(str(prior_plan.get("source_digest")), lane,
                             window, picked["id"])
            if ledger is not None and key in ledger:
                refused.append({
                    "requirement_id": req_id, "lane": lane,
                    "interval": window,
                    "reason": f"already acquired as {key} (ACQ-014)"})
                continue
            steps.append({
                "requirement_id": req_id, "lane": lane,
                "catalog_lane": lane,
                "interval": window, "producer": picked["id"],
                "capability": picked["capability"], "tier": picked["tier"],
                "verify_task": False,
                "cost_budget_seconds": round(
                    float(picked["cost_seconds_per_window"]), 3),
                "peak_rss_gb": float(picked["peak_rss_gb"]),
                "measured_cost": bool(picked["measured"]),
                "why": (f"{req_id} is still {status} after the "
                        f"{TIER_CHEAP} tier; {picked['id']} is the "
                        f"selective escalation ({picked.get('note')})"),
                "ledger_key": key,
                "selectivity": 0.0,
            })
    estimated = round(sum(row["cost_budget_seconds"] for row in steps), 3)
    if latency_budget_s is not None and estimated > latency_budget_s:
        kept, running = [], 0.0
        for step in steps:
            if running + step["cost_budget_seconds"] <= latency_budget_s:
                kept.append(step)
                running += step["cost_budget_seconds"]
            else:
                refused.append({
                    "requirement_id": step["requirement_id"],
                    "lane": step["lane"], "interval": step["interval"],
                    "reason": f"escalation budget {latency_budget_s}s "
                              f"exhausted after {round(running, 3)}s"})
        steps, estimated = kept, round(running, 3)
    return {"schema_version": SCHEMA_VERSION,
            "action": "refine" if steps else "no_run",
            "graph_id": prior_plan.get("graph_id"),
            "source_digest": prior_plan.get("source_digest"),
            "edition": prior_plan.get("edition"),
            "purpose": prior_plan.get("purpose"),
            "fidelity": FIDELITY_EXHAUSTIVE, "escalation_round": True,
            "steps": steps, "refused": refused,
            "estimated_cost_seconds": estimated,
            "latency_budget_s": latency_budget_s}


def ledger_key(source_digest: str, lane: str,
               window: Mapping[str, float], producer_id: str) -> str:
    """Dedup identity for one acquisition (ACQ-014)."""
    return (f"{source_digest}:{lane}:{window['start_s']}-"
            f"{window['end_s']}:{producer_id}")


def run_acquisition(acq_plan: Mapping[str, Any], *,
                    cached: Callable[..., Mapping[str, Any] | None],
                    producers: Mapping[str, Callable[..., Mapping[str, Any]]],
                    verifiers: Mapping[str, Callable[..., Mapping[str, Any]]] | None = None,
                    refresh_model: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
                    ledger: dict[str, Any] | None = None,
                    max_cost_seconds: float | None = None,
                    ) -> dict[str, Any]:
    """Execute an acquisition plan and re-evaluate (ACQ-009 to 013).

    Measurement-lane steps run through
    :func:`query_directed_acquisition.execute_query_acquisition`
    (cached first, then the targeted producer, serially per source).
    Verify-lane steps run through the caller verifier for their lane,
    or defer with the reason when none is supplied. When at least one
    step lands as acquired or cached and a refresh callback is given,
    the model is refreshed (ACQ-010, the merge) and every plan
    requirement is re-answered (ACQ-011). The run stops with the
    conjunction outcome or the exhausted budget named (ACQ-012);
    requirements that stay unverified keep their explicit uncertainty
    (ACQ-013).
    """
    steps = list(acq_plan.get("steps") or [])
    # Verify-kind requirements (action/relationship candidates) run
    # through caller verifiers even when their lane names a
    # measurement catalog: a candidate is never acquired as a
    # measurement.
    verify_steps = [row for row in steps if row.get("verify_task")]
    measure_steps = [row for row in steps if not row.get("verify_task")
                     and row["lane"] in PRODUCER_CATALOG]
    verify_steps.extend(row for row in steps
                        if not row.get("verify_task")
                        and row["lane"] not in PRODUCER_CATALOG)

    execution: dict[str, Any] = {"action": "no_run", "steps": []}
    if measure_steps:
        compat = {"schema_version": 1, "action": "refine",
                  "source_digest": acq_plan.get("source_digest"),
                  "rss_ceiling_gb": 8.0,
                  "steps": [{"lane": row["lane"],
                             "interval": row["interval"],
                             "cost_budget_seconds":
                                 row["cost_budget_seconds"],
                             "order": ["cached", "targeted"]}
                            for row in measure_steps]}
        execution = execute_query_acquisition(
            compat, cached=cached, producers=dict(producers),
            max_cost_seconds=max_cost_seconds)
        by_lane_interval = {(row["lane"], row["interval"]["start_s"],
                             row["interval"]["end_s"]): row
                            for row in measure_steps}
        for row in execution.get("steps", []):
            key = (row["lane"], row["start_s"], row["end_s"])
            planned = by_lane_interval.get(key, {})
            row["requirement_id"] = planned.get("requirement_id")
            row["why"] = planned.get("why")
            if row.get("status") in ("acquired", "cached") and (
                    ledger is not None and planned.get("ledger_key")):
                ledger[planned["ledger_key"]] = {
                    "status": row["status"],
                    "producer_version": row.get("model_version"),
                    "cost_seconds": row.get("cost_seconds"),
                }

    verifiers = dict(verifiers or {})
    verify_rows: list[dict[str, Any]] = []
    for row in verify_steps:
        verifier = verifiers.get(row["lane"])
        if verifier is None:
            verify_rows.append({
                "requirement_id": row["requirement_id"],
                "lane": row["lane"], "interval": row["interval"],
                "status": "deferred",
                "reason": "no verifier supplied for this candidate lane",
                "why": row.get("why")})
            continue
        try:
            verdict = dict(verifier(
                lane=row["lane"], start_s=row["interval"]["start_s"],
                end_s=row["interval"]["end_s"],
                budget_seconds=row["cost_budget_seconds"]))
        except Exception as exc:  # noqa: BLE001 - one verifier's failure is deferred work, not a run failure
            verify_rows.append({
                "requirement_id": row["requirement_id"],
                "lane": row["lane"], "interval": row["interval"],
                "status": "deferred",
                "reason": f"verifier failed: {exc}",
                "why": row.get("why")})
            continue
        verify_rows.append({
            "requirement_id": row["requirement_id"],
            "lane": row["lane"], "interval": row["interval"],
            "status": ("acquired" if verdict.get("measured")
                       else "deferred"),
            "result": verdict, "why": row.get("why")})
        if verdict.get("measured") and ledger is not None:
            ledger[row["ledger_key"]] = {
                "status": "acquired",
                "producer_version": verdict.get("producer_version"),
                "cost_seconds": verdict.get("cost_seconds"),
            }

    landed = any(row.get("status") in ("acquired", "cached")
                 for row in list(execution.get("steps", ())) + verify_rows)
    requery = None
    if landed and refresh_model is not None:
        requery = refresh_model(acq_plan)
    return {"acquisition": execution, "verifications": verify_rows,
            "landed": landed, "requery": requery,
            "stopped": ("budget exhausted" if _over_budget(
                acq_plan, execution, max_cost_seconds) else
                "conjunction decided" if requery is not None else
                "nothing landed")}


def _over_budget(acq_plan: Mapping[str, Any], execution: Mapping[str, Any],
                 ceiling: float | None) -> bool:
    if ceiling is None:
        return False
    spent = float(execution.get("cost_seconds", 0.0) or 0.0)
    return spent > ceiling


def find_contradictions(model: Mapping[str, Any],
                        compiled: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Contradictory evidence for one compiled graph (ACQ-025).

    Two observations answer the same measurement predicate on the same
    target over overlapping ticks with unequal scalar or text values.
    Either may be stale, miscalibrated, or wrong: the finding names
    both ids and refuses to pick, so the caller reports inconclusive
    instead of averaging them into a false answer.
    """
    rows: list[dict[str, Any]] = []
    observations = [obs for obs in model.get("observations", ())
                    if isinstance(obs, dict)]
    for item in compiled.get("compiled", ()):
        if item.get("kind") != "plan":
            continue
        for predicate in item["plan"]["predicates"]:
            if predicate.get("predicate") != "measurement":
                continue
            wanted = predicate["measurement_type"]
            target = predicate.get("target_entity_id")
            carrier = []
            for obs in observations:
                native = ((obs.get("payload") or {}).get("evidence_contract")
                          or {})
                if native.get("measurement_type") != wanted:
                    continue
                if (target is not None
                        and native.get("target_entity_id") != target):
                    continue
                interval = (obs.get("interval") or {})
                value = native.get("value")
                if not isinstance(interval.get("start"), int):
                    continue
                carrier.append({"id": obs.get("id"), "value": value,
                                "interval": interval})
            for left in carrier:
                for right in carrier:
                    if left["id"] >= right["id"]:
                        continue
                    if not _overlaps(left["interval"], right["interval"]):
                        continue
                    if _disagree(left["value"], right["value"]):
                        rows.append({
                            "requirement_id": item["requirement_id"],
                            "measurement_type": wanted,
                            "evidence_ids": sorted(
                                [left["id"], right["id"]]),
                            "values": [left["value"], right["value"]],
                        })
    return rows


def _overlaps(first: Mapping[str, Any],
              second: Mapping[str, Any]) -> bool:
    if first.get("clock_id") != second.get("clock_id"):
        return False
    return (first["start"] < second["end"]
            and second["start"] < first["end"])


def _disagree(first: Any, second: Any) -> bool:
    if isinstance(first, bool) or isinstance(second, bool):
        return first is not second
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return first != second
    if isinstance(first, str) and isinstance(second, str):
        return first.strip().lower() != second.strip().lower()
    return False


def summarize_answer(answer: Mapping[str, Any],
                     limit: int = 10) -> dict[str, Any]:
    """Compact evidence summary: intervals and ids only (ACQ-029/031).

    Carries what the reasoning model needs to cite or drill into, and
    nothing it does not: no transcripts, no payloads, no giant JSON
    (ACQ-030).
    """
    results = list(answer.get("results") or [])[:max(0, limit)]
    return {
        "status": answer.get("status"),
        "intervals": [
            {"start_s": row["interval"]["start_s"],
             "end_s": row["interval"]["end_s"],
             "evidence_ids": row.get("evidence_ids", []),
             "lanes": row.get("lanes", [])}
            for row in results
            if isinstance(row.get("interval"), dict)],
        "reasons": list(answer.get("reasons") or []),
        "required_missing_lanes": list(
            answer.get("required_missing_lanes") or []),
    }


def fetch_evidence_detail(model: Mapping[str, Any],
                          evidence_id: str) -> dict[str, Any] | None:
    """One observation by id for selective drill-down (ACQ-031)."""
    for obs in model.get("observations") or ():
        if isinstance(obs, dict) and obs.get("id") == evidence_id:
            return dict(obs)
    return None


def to_edit_candidates(graph_id: str,
                       answers: Mapping[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Evidence-linked intervals for edit planning (RET-035/036).

    Each candidate carries the source interval plus the observation
    ids, provenance ids and query that support it: the factual basis
    an edit decision cites. Creative rationale is never included; the
    edit planner supplies that separately.
    """
    candidates = []
    for req_id, answer in answers.items():
        for row in answer.get("results") or ():
            interval = row.get("interval") or {}
            candidates.append({
                "graph_id": graph_id, "requirement_id": req_id,
                "interval": {
                    "clock_id": interval.get("clock_id"),
                    "start_s": interval.get("start_s"),
                    "end_s": interval.get("end_s")},
                "evidence_ids": list(row.get("evidence_ids") or []),
                "provenance_ids": list(row.get("provenance_ids") or []),
                "status": answer.get("status"),
            })
    return candidates


def ambiguities_from_candidates(
        model: Mapping[str, Any], compiled: Mapping[str, Any], *,
        windows: Sequence[Mapping[str, float]] | None = None
        ) -> list[dict[str, Any]]:
    """Resolvable ambiguities for targeted reanalysis (RET-037).

    Unverified M7/M10 candidate spans inside the promising windows
    become the named ambiguities
    :func:`query_directed_acquisition.plan_query_acquisition` accepts:
    a lane plus an interval, never a vague "look harder".
    """
    rows: list[dict[str, Any]] = []
    for obs in model.get("observations") or ():
        if not isinstance(obs, dict):
            continue
        modality = obs.get("modality")
        if modality not in ("event_candidate", "relationship_candidate"):
            continue
        interval = obs.get("interval") or {}
        lane = _lane_of(obs)
        if lane is None:
            continue
        start_s = interval.get("start", 0) / 1000.0
        end_s = interval.get("end", 0) / 1000.0
        if windows and not any(start_s < row["end_s"] and row["start_s"] < end_s
                               for row in windows):
            continue
        rows.append({"lane": lane, "start_s": round(start_s, 3),
                     "end_s": round(end_s, 3),
                     "candidate_id": obs.get("id")})
    return rows


def _lane_of(obs: Mapping[str, Any]) -> str | None:
    prov = str(obs.get("provenance_id") or "")
    if prov.startswith("prov-"):
        _digest, _, lane = prov[len("prov-"):].rpartition("-")
        return lane or None
    return None


def marginal_gain_per_second(before: Mapping[str, bool],
                             after: Mapping[str, bool],
                             extra_cost_seconds: float) -> dict[str, Any]:
    """Useful answers gained per second of extra inference (ACQ-024).

    Utility is the share of requirements with a terminal, citable
    answer (satisfied, whether present or confidently absent). A zero
    or negative gain is reported as zero, never hidden in an average.
    """
    def utility(states: Mapping[str, bool]) -> float:
        if not states:
            return 0.0
        return sum(1 for value in states.values() if value) / len(states)

    gain = utility(after) - utility(before)
    cost = max(float(extra_cost_seconds), 1e-6)
    return {"utility_before": round(utility(before), 4),
            "utility_after": round(utility(after), 4),
            "gain": round(gain, 4),
            "extra_cost_seconds": round(float(extra_cost_seconds), 3),
            "answers_per_second": round(max(gain, 0.0) / cost, 6)}


def write_ledger(ledger: Mapping[str, Any], path: str | Path) -> None:
    """Persist the acquisition ledger (ACQ-014/015 reuse record)."""
    Path(path).write_text(json.dumps(ledger, indent=2, sort_keys=True),
                         encoding="utf-8")


def read_ledger(path: str | Path) -> dict[str, Any]:
    """Load a persisted acquisition ledger; missing file means empty."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
