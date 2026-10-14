"""FI-05: typed interval query over the Project Model + source-memory lanes.

``evidence_for(model, plan)`` answers one typed query plan with
interval-joined results, provenance and coverage per result, and one of
four states - ``present``, ``confidently_absent``, ``not_yet_analysed``,
``inconclusive``. ``compile_plan`` builds the plan; ``plan_from_filter``
compiles the evidence-backed subset of
``FootageIndex.filter()`` kwargs so the two stores can be parity-checked.

**What this is.** The interval-first evidence API the query layer was
missing: predicates name typed measurements, event spans or
relationship candidates with units and clock scopes, and the answer
carries the evidence behind it. An empty result says WHY it is empty:
measured-absent (the lane looked across the scope and found nothing)
is ``confidently_absent``; a lane that never ran is
``not_yet_analysed``; partial, stale or legacy-coverageless evidence is
``inconclusive``. A query never infers absence from an empty index.

**What this is not.** Not a replacement for ``FootageIndex.filter()``:
filter keeps its semantics unchanged, and parity tests pin that. VLM
facets (framing, camera mode, stability labels) have no evidence
equivalent and are refused at compile time rather than mapped onto
measurements. Synchronized cross-clock joins are refused (VI-02 owns
the transform graph); independent mode returns per-observation results
without comparing their clock ticks. Interpretations and evaluations are never query evidence: a
result is carried by measurements, never by taste or a verdict.

**Time.** Source seconds on the way in and out; model intervals are
millisecond ticks on their own clock, converted without rounding the
evidence (ticks are integers already). Joins intersect half-open
``[start, end)`` tick ranges within ONE clock only. ``join_mode`` defaults
to ``synchronized``; ``independent`` answers each predicate observation
separately and supports results from multiple source clocks.

**Reads.** The model dict only, plus an optional ``lane_presence``
hook ``{(digest, lane): {"present": bool, "freshness": str}}`` that
tells a lane that ran-but-was-never-migrated apart from one that never
ran. Without it, absence from the model reads as never analysed.
Nothing is written anywhere.
"""

from __future__ import annotations

from itertools import pairwise
from typing import Any

try:
    from library.tools import evidence_contract
except ImportError:  # imported as `tools.*` from inside library/
    from tools import evidence_contract

TICKS_PER_SECOND = 1000
"""Millisecond source clocks, the convention
:mod:`library.tools.project_model_evidence` migrates onto."""

PRESENT = "present"
CONFIDENTLY_ABSENT = "confidently_absent"
NOT_YET_ANALYSED = "not_yet_analysed"
INCONCLUSIVE = "inconclusive"
QUERY_STATES = (PRESENT, CONFIDENTLY_ABSENT, NOT_YET_ANALYSED, INCONCLUSIVE)
"""The four query outcomes. Complete: an answer carries exactly one."""

MEASURED_EVENTS = ("speaking", "on_screen")
"""M7 predicate spans: diarized turns attributed to a face, and visible
face tracks. Anything else names an M7/M10 candidate kind and is never
an answer until verified."""

MODALITY_TO_LANE = {
    "source_metadata": "m0",
    "speech_word": "m1",
    "speech": "m1",
    "speech_rate": "m1",
    "speech_pause": "m1",
    "speech_disfluency": "m1",
    "speech_repetition": "m1",
    "speech_completeness": "m1",
    "speech_take_link": "m1",
    "face_frame": "m3",
    "face": "m3b",
    "voice_turn": "m3b",
    "expression_frame": "m3c",
    "region_frame": "m3d",
    "saliency": "m3e",
    "region_stats": "m3f",
    "depth": "m3g",
    "scene": "m4",
    "sound_event": "m5",
    "multicam": "m6",
    "event_span": "m7",
    "event_candidate": "m7",
    "relationship_candidate": "m10",
}
"""Legacy observation modality to the source-memory lane that wrote it.
The lane is the last segment of ``prov-{digest}-{lane}`` provenance ids."""

MEASUREMENT_TO_LANE = {
    measurement: MODALITY_TO_LANE[modality]
    for modality, measurement in evidence_contract.MODALITY_TO_MEASUREMENT.items()
    if modality in MODALITY_TO_LANE
}
MEASUREMENT_TO_LANE.update({
    # FI-15 query facets use the same evidence query and answer contract.
    "image.sharpness.laplacian_variance": "m3",
    "image.luma.rec709_p50": "m3",
    "image.color.rgb_mean": "m3",
    "image.defocus_likelihood": "m3",
    "audio.sound_event": "m5",
    "sync.multicam_offset": "m6",
    "speech.take_link": "m1",
})
"""Registry measurement type to the lane that produces it."""

FILTER_TO_MEASUREMENT = {
    # The evidence-backed subset of FootageIndex.filter() kwargs.
    # VLM facets (framing, camera_mode, stability, movement, scene_type,
    # content_type) and audio-classification facets map to no migrated
    # evidence and are refused, never guessed.
    "has_speech": "event.speaking_span",
    "contains": "speech.word",
    "person": "person.voice_turn",
}

FACET_PREFIXES = {
    "quality": ("image.", "lens."),
    "audio": ("audio.",),
    "camera": ("sync.",),
    "take": ("speech.take_",),
}
"""Filter kwargs with an evidence equivalent, for parity checks."""


class EvidenceQueryError(ValueError):
    """A query that cannot be compiled or scoped to evidence."""


def _ticks(seconds: float) -> int:
    return round(float(seconds) * TICKS_PER_SECOND)


def _seconds(tick: int) -> float:
    return float(tick) / TICKS_PER_SECOND


def _observation_interval(obs: dict[str, Any]) -> tuple[str, int, int] | None:
    interval = obs.get("interval") or {}
    clock_id = interval.get("clock_id")
    start, end = interval.get("start"), interval.get("end")
    if (not isinstance(clock_id, str) or not isinstance(start, int)
            or not isinstance(end, int) or end <= start):
        return None
    return clock_id, start, end


def _native_evidence(obs: dict[str, Any]) -> dict[str, Any] | None:
    payload = obs.get("payload") or {}
    native = payload.get("evidence_contract")
    return native if isinstance(native, dict) else None


def _lane_of(obs: dict[str, Any]) -> str | None:
    prov = str(obs.get("provenance_id") or "")
    if prov.startswith("prov-"):
        _digest, _, lane = prov[len("prov-"):].rpartition("-")
        if lane:
            return lane
    return None


def _record_kind(obs: dict[str, Any]) -> str | None:
    """The registry measurement type an observation carries, legacy or new."""
    native = _native_evidence(obs)
    if native is not None and isinstance(native.get("measurement_type"), str):
        return native["measurement_type"]
    return evidence_contract.MODALITY_TO_MEASUREMENT.get(
        obs.get("modality"))


def _freshness_of(obs: dict[str, Any]) -> str:
    native = _native_evidence(obs)
    freshness = (native or {}).get("freshness")
    if freshness in evidence_contract.COVERAGE_FRESHNESS:
        return freshness
    return "unknown"


def _coverage_of(obs: dict[str, Any]) -> str:
    native = _native_evidence(obs)
    coverage = (native or {}).get("coverage")
    if isinstance(coverage, str) and coverage:
        return coverage
    return "unknown"


def _certainty_of(obs: dict[str, Any]) -> str | None:
    native = _native_evidence(obs)
    certainty = (native or {}).get("certainty")
    return certainty if isinstance(certainty, str) else None


def _producer_version_of(model: dict[str, Any],
                         obs: dict[str, Any]) -> str | None:
    native = _native_evidence(obs)
    version = (native or {}).get("producer_version")
    if isinstance(version, str) and version:
        return version
    for prov in model.get("provenance") or []:
        if (isinstance(prov, dict)
                and prov.get("id") == obs.get("provenance_id")):
            version = prov.get("producer_version")
            return version if isinstance(version, str) else None
    return None


def compile_plan(predicates: list[dict[str, Any]], *,
                 scope: dict[str, Any] | None = None,
                 level: str | None = None,
                 join_mode: str = "synchronized") -> dict[str, Any]:
    """Compile typed predicates into a query plan, or raise.

    A predicate is ``{"predicate": "measurement", "measurement_type",
    ...}`` with optional ``min``/``max``/``equals`` value bounds and
    ``clock_id``/``asset_id``/``target_entity_id`` scopes, or
    ``{"predicate": "event_span", "event", ...}`` with optional
    ``participant_id`` and ``verified_only``. ``scope`` is an optional
    ``{"clock_id", "asset_id", "start_s", "end_s"}`` window every
    predicate is read inside. Unknown measurement types, evaluation or
    creative-judgment types, unknown predicate kinds and inverted
    bounds raise: a plan that cannot name its evidence is refused,
    never widened into a guess.
    """
    if not isinstance(predicates, list) or not predicates:
        raise EvidenceQueryError("a plan asks at least one predicate")
    if level is not None and level not in evidence_contract.ANALYSIS_LEVELS:
        raise EvidenceQueryError(
            f"level {level!r} is not one of "
            f"{evidence_contract.ANALYSIS_LEVELS}")
    plan_scope: dict[str, Any] = {}
    if scope:
        for key in ("clock_id", "asset_id"):
            if scope.get(key) is not None:
                if not isinstance(scope[key], str):
                    raise EvidenceQueryError(
                        f"scope {key} must be a string")
                plan_scope[key] = scope[key]
        for key in ("start_s", "end_s"):
            if scope.get(key) is not None:
                try:
                    plan_scope[key] = float(scope[key])
                except (TypeError, ValueError):
                    raise EvidenceQueryError(
                        f"scope {key} must be seconds") from None
        if ("start_s" in plan_scope and "end_s" in plan_scope
                and plan_scope["end_s"] <= plan_scope["start_s"]):
            raise EvidenceQueryError("scope end_s must follow start_s")
    if join_mode not in ("synchronized", "independent"):
        raise EvidenceQueryError(
            "join_mode must be 'synchronized' or 'independent'")
    compiled = [_compile_predicate(predicate) for predicate in predicates]
    lanes = sorted({lane for lane in
                    (_required_lane(p) for p in compiled) if lane})
    return {"predicates": compiled, "required_lanes": lanes,
            "scope": plan_scope, "level": level, "join_mode": join_mode}


def _compile_predicate(predicate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(predicate, dict):
        raise EvidenceQueryError("predicates are mappings")
    kind = predicate.get("predicate")
    if kind == "measurement":
        return _compile_measurement(predicate)
    if kind == "event_span":
        return _compile_event(predicate)
    raise EvidenceQueryError(
        f"unknown predicate {kind!r}: this query answers "
        f"'measurement' and 'event_span' only")


def _compile_measurement(predicate: dict[str, Any]) -> dict[str, Any]:
    measurement_type = predicate.get("measurement_type")
    if not isinstance(measurement_type, str) or not measurement_type:
        raise EvidenceQueryError("a measurement names its measurement_type")
    try:
        entry = evidence_contract.registry_entry(measurement_type)
    except evidence_contract.UnknownMeasurementTypeError:
        raise EvidenceQueryError(
            f"unknown measurement type {measurement_type!r}") from None
    if entry["kind"] is not evidence_contract.EvidenceType.MEASUREMENT:
        raise EvidenceQueryError(
            f"{measurement_type!r} is {entry['kind'].value}, not a "
            f"measurement: a query is answered by measurements, never "
            f"by a verdict or taste")
    compiled: dict[str, Any] = {"predicate": "measurement",
                                "measurement_type": measurement_type,
                                "unit": entry["unit"]}
    for bound in ("min", "max"):
        if predicate.get(bound) is not None:
            try:
                compiled[bound] = float(predicate[bound])
            except (TypeError, ValueError):
                raise EvidenceQueryError(
                    f"{measurement_type!r} bound {bound} must be "
                    f"numeric") from None
    if ("min" in compiled and "max" in compiled
            and compiled["max"] < compiled["min"]):
        raise EvidenceQueryError(
            f"{measurement_type!r} bounds are inverted")
    if predicate.get("equals") is not None:
        compiled["equals"] = predicate["equals"]
    for key in ("clock_id", "asset_id", "target_entity_id"):
        if predicate.get(key) is not None:
            if not isinstance(predicate[key], str):
                raise EvidenceQueryError(f"{key} must be a string")
            compiled[key] = predicate[key]
    return compiled


def _compile_event(predicate: dict[str, Any]) -> dict[str, Any]:
    event = predicate.get("event")
    if not isinstance(event, str) or not event:
        raise EvidenceQueryError("an event_span names its event")
    compiled: dict[str, Any] = {"predicate": "event_span", "event": event,
                                "verified_only": bool(
                                    predicate.get("verified_only", False))}
    participant = predicate.get("participant_id")
    if participant is not None:
        if not isinstance(participant, str) or not participant.strip():
            raise EvidenceQueryError("participant_id must be a string")
        compiled["participant_id"] = participant.strip()
    return compiled


def _required_lane(compiled: dict[str, Any]) -> str | None:
    if compiled["predicate"] == "measurement":
        return MEASUREMENT_TO_LANE.get(compiled["measurement_type"])
    if compiled["event"] in MEASURED_EVENTS:
        return "m7"
    return "m7"


def plan_from_filter(**kwargs: Any) -> dict[str, Any]:
    """Compile the evidence-backed subset of ``FootageIndex.filter()``
    kwargs into a typed plan. Facets with no evidence equivalent
    (framing, camera_mode, stability, movement, scene_type,
    content_type, durations, motion/face bounds, audio classification)
    raise rather than mapping a VLM label onto a measurement.
    """
    unmapped = sorted(key for key in kwargs
                      if kwargs[key] is not None
                      and key not in FILTER_TO_MEASUREMENT)
    if unmapped:
        raise EvidenceQueryError(
            f"no evidence equivalent for facet(s) {unmapped}: use "
            f"FootageIndex.filter() for VLM facets, never this plan")
    predicates: list[dict[str, Any]] = []
    if kwargs.get("has_speech") is True:
        predicates.append({"predicate": "event_span", "event": "speaking"})
    elif kwargs.get("has_speech") is False:
        raise EvidenceQueryError(
            "has_speech=False asks for absence: run the has_speech=True "
            "plan and read confidently_absent, never a negated predicate")
    if kwargs.get("contains") is not None:
        predicates.append({"predicate": "measurement",
                           "measurement_type": "speech.word",
                           "equals": kwargs["contains"]})
    if kwargs.get("person") is not None:
        predicates.append({"predicate": "event_span", "event": "speaking",
                           "participant_id": kwargs["person"]})
    if not predicates:
        raise EvidenceQueryError(
            "no evidence-backed facet in this filter call")
    return compile_plan(predicates)


def plan_from_facets(**facets: dict[str, Any]) -> dict[str, Any]:
    """Compile FI-15 typed quality/audio/camera/take facets.

    Each supplied facet is a measurement predicate body containing a
    registered ``measurement_type`` and its typed value constraint. The
    resulting plan is answered by :func:`evidence_for`, preserving its
    four-state coverage semantics. Unknown or cross-family measurements
    refuse instead of widening the query.
    """
    unknown = sorted(set(facets) - set(FACET_PREFIXES))
    if unknown:
        raise EvidenceQueryError(f"unknown facet(s) {unknown}")
    predicates = []
    for family, body in facets.items():
        if not isinstance(body, dict):
            raise EvidenceQueryError(f"{family} facet must be a mapping")
        measurement_type = body.get("measurement_type")
        if not isinstance(measurement_type, str) or not measurement_type.startswith(
                FACET_PREFIXES[family]):
            raise EvidenceQueryError(
                f"{family} facet requires a registered {family} measurement")
        predicates.append({"predicate": "measurement", **body})
    if not predicates:
        raise EvidenceQueryError("a query names at least one typed facet")
    return compile_plan(predicates)


def _matches_value(compiled: dict[str, Any], native: dict[str, Any] | None,
                   obs: dict[str, Any]) -> bool:
    if "equals" in compiled:
        value = (native or {}).get("value")
        if isinstance(value, dict):
            haystack = " ".join(str(part) for part in value.values()
                                if isinstance(part, str))
        elif isinstance(value, list):
            haystack = " ".join(str(part) for part in value
                                if isinstance(part, str))
        else:
            haystack = value
            if isinstance(haystack, str):
                pass
            elif haystack is None:
                payload = obs.get("payload") or {}
                haystack = payload.get("text") or ""
            else:
                return haystack == compiled["equals"]
        wanted = str(compiled["equals"]).lower()
        return wanted in str(haystack).lower()
    if "min" in compiled or "max" in compiled:
        value = (native or {}).get("value")
        if not isinstance(value, (int, float)):
            return False
        if "min" in compiled and value < compiled["min"]:
            return False
        return not ("max" in compiled and value > compiled["max"])
    return True


def _entity_ids_for(model: dict[str, Any],
                    ref: str) -> set[str]:
    """Entity ids a participant ref names: id first, then label."""
    ids = {entity.get("id") for entity in model.get("entities") or []
           if isinstance(entity, dict) and entity.get("id") == ref}
    if ids:
        return ids
    lowered = ref.lower()
    return {entity.get("id") for entity in model.get("entities") or []
            if isinstance(entity, dict)
            and isinstance(entity.get("label"), str)
            and entity["label"].lower() == lowered
            and isinstance(entity.get("id"), str)}


def _observation_entities(model: dict[str, Any]) -> dict[str, set[str]]:
    """Observation id to entity ids via identity links and native targets."""
    mapping: dict[str, set[str]] = {}
    for link in model.get("identity_links") or []:
        if not isinstance(link, dict):
            continue
        obs_id, entity_id = link.get("observation_id"), link.get("entity_id")
        if isinstance(obs_id, str) and isinstance(entity_id, str):
            mapping.setdefault(obs_id, set()).add(entity_id)
    return mapping


def _matches_participant(obs: dict[str, Any], wanted: set[str],
                         entities: dict[str, set[str]]) -> bool:
    if obs.get("id") in entities and entities[obs["id"]] & wanted:
        return True
    payload = obs.get("payload") or {}
    tracks = {payload.get("voice_track"), payload.get("face_track")}
    if tracks & wanted:
        return True
    native = _native_evidence(obs) or {}
    return native.get("target_entity_id") in wanted


def _match_observations(model: dict[str, Any], compiled: dict[str, Any],
                        scope: dict[str, Any],
                        entities: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Every observation interval one predicate is evidence for."""
    matches: list[dict[str, Any]] = []
    for obs in model.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        ticks = _observation_interval(obs)
        if ticks is None:
            continue
        clock_id, start, end = ticks
        if scope.get("clock_id") and clock_id != scope["clock_id"]:
            continue
        if (scope.get("asset_id") and obs.get("asset_id")
                != scope["asset_id"]):
            continue
        if "start_s" in scope and end <= _ticks(scope["start_s"]):
            continue
        if "end_s" in scope and start >= _ticks(scope["end_s"]):
            continue
        native = _native_evidence(obs)
        if compiled["predicate"] == "measurement":
            if _record_kind(obs) != compiled["measurement_type"]:
                continue
            if (compiled.get("target_entity_id")
                    and (native or {}).get("target_entity_id")
                    != compiled["target_entity_id"]):
                continue
            if not _matches_value(compiled, native, obs):
                continue
        else:
            event = compiled["event"]
            payload = obs.get("payload") or {}
            if payload.get("spans") == []:
                # A measured-absence marker (empty span set over the
                # source): what makes absence confident, never a match.
                continue
            if event in MEASURED_EVENTS:
                if obs.get("modality") != "event_span":
                    continue
                if payload.get("predicate") != event:
                    continue
            else:
                # Candidates are never answers: only a VERIFIED M7
                # candidate can carry a present. M10 relationship
                # candidates carry no verdict at all, so they never
                # match here; _has_unverified_candidates reports them.
                if obs.get("modality") != "event_candidate":
                    continue
                if (obs.get("payload") or {}).get("predicate") != event:
                    continue
                if not (obs.get("payload") or {}).get("verified"):
                    continue
            if compiled.get("participant_id"):
                wanted = _entity_ids_for(model, compiled["participant_id"])
                if not wanted:
                    continue
                if not _matches_participant(obs, wanted, entities):
                    continue
        matches.append({"clock_id": clock_id, "start": start, "end": end,
                        "obs": obs, "lane": _lane_of(obs),
                        "freshness": _freshness_of(obs),
                        "coverage": _coverage_of(obs),
                        "certainty": _certainty_of(obs)})
    return matches


def _intersect(matches_per_predicate: list[list[dict[str, Any]]]
               ) -> list[tuple[int, int, list[dict[str, Any]]]]:
    """Maximal tick ranges every predicate covers, with contributors."""
    edges = sorted({edge for matches in matches_per_predicate
                    for match in matches
                    for edge in (match["start"], match["end"])})
    joined: list[tuple[int, int, list[dict[str, Any]]]] = []
    for lo, hi in pairwise(edges):
        if hi <= lo:
            continue
        contributors: list[dict[str, Any]] = []
        for matches in matches_per_predicate:
            covering = [match for match in matches
                        if match["start"] <= lo and match["end"] >= hi]
            if not covering:
                break
            contributors.extend(covering)
        else:
            joined.append((lo, hi, contributors))
    merged: list[tuple[int, int, list[dict[str, Any]]]] = []
    for lo, hi, contributors in joined:
        if (merged and merged[-1][1] == lo
                and {id(c["obs"]) for c in merged[-1][2]}
                == {id(c["obs"]) for c in contributors}):
            merged[-1] = (merged[-1][0], hi, contributors)
        else:
            merged.append((lo, hi, contributors))
    return merged


def evidence_for(model: dict[str, Any], plan: dict[str, Any], *,
                 lane_presence: dict[tuple[str, str], dict[str, Any]] | None = None
                 ) -> dict[str, Any]:
    """Answer one compiled plan over a Project Model dict.

    Returns ``{"status", "results", "completeness_basis",
    "required_missing_lanes", "reasons"}``. Results carry their joined
    interval in ticks and seconds plus the contributing evidence ids,
    provenance ids, certainties, coverage and freshness - the reader
    can place every row without reopening the model. ``lane_presence``
    optionally maps ``(digest, lane)`` to ``{"present", "freshness"}``
    sidecar truth so a lane that ran but never migrated reads
    ``inconclusive`` instead of ``not_yet_analysed``.
    """
    if not isinstance(model, dict):
        raise EvidenceQueryError("evidence_for reads a Project Model dict")
    if (not isinstance(plan, dict) or not isinstance(
            plan.get("predicates"), list) or not plan["predicates"]):
        raise EvidenceQueryError("evidence_for answers a compiled plan")
    scope = plan.get("scope") or {}
    level = plan.get("level")
    entities = _observation_entities(model)
    per_predicate = [_match_observations(model, compiled, scope, entities)
                     for compiled in plan["predicates"]]
    clocks = sorted({match["clock_id"] for matches in per_predicate
                     for match in matches})
    join_mode = plan.get("join_mode", "synchronized")
    if len(clocks) > 1 and join_mode == "synchronized":
        return {"status": INCONCLUSIVE, "results": [],
                "completeness_basis": [],
                "required_missing_lanes": sorted(set(
                    plan.get("required_lanes") or [])),
                "reasons": [(f"predicates span clocks {clocks}: interval "
                             f"joins need one clock, and cross-clock "
                             f"transforms belong to VI-02, not this query")]}
    if join_mode == "independent":
        joined = [(match["start"], match["end"], [match])
                  for matches in per_predicate for match in matches]
    else:
        joined = _intersect(per_predicate) if all(per_predicate) else []
    fresh_results: list[dict[str, Any]] = []
    stale_dropped = 0
    for lo, hi, contributors in joined:
        if any(match["freshness"] == "stale" for match in contributors):
            stale_dropped += 1
            continue
        clock_id = contributors[0]["clock_id"]
        fresh_results.append({
            "interval": {"clock_id": clock_id, "start": lo, "end": hi,
                         "start_s": _seconds(lo), "end_s": _seconds(hi)},
            "asset_id": contributors[0]["obs"].get("asset_id"),
            "evidence_ids": sorted({str(match["obs"].get("id"))
                                    for match in contributors
                                    if match["obs"].get("id")}),
            "provenance_ids": sorted({str(match["obs"].get(
                "provenance_id")) for match in contributors
                if match["obs"].get("provenance_id")}),
            "certainty": sorted({m["certainty"] for m in contributors
                                 if m["certainty"]}),
            "coverage": sorted({m["coverage"] for m in contributors}),
            "freshness": sorted({m["freshness"] for m in contributors}),
            "lanes": sorted({m["lane"] for m in contributors if m["lane"]}),
        })
    reasons: list[str] = []
    if stale_dropped:
        reasons.append(f"{stale_dropped} joined interval(s) include stale "
                       f"required evidence and carry no answer")
    if fresh_results:
        return {"status": PRESENT, "results": fresh_results,
                "completeness_basis": _basis_for(
                    model, plan, per_predicate, level),
                "required_missing_lanes": [], "reasons": reasons}
    return _empty_answer(model, plan, per_predicate, scope, level,
                         lane_presence, reasons)


def evidence_for_with_acquisition(model: dict[str, Any], plan: dict[str, Any], *,
                                  source_digest: str,
                                  duration_seconds: float,
                                  cached: Any,
                                  producers: dict[str, Any],
                                  refresh_model: Any,
                                  max_cost_seconds: float,
                                  available_lanes: list[str] | None = None,
                                  producer_budgets: dict[str, float] | None = None,
                                  resolvable_ambiguities: list[dict[str, Any]] | None = None
                                  ) -> dict[str, Any]:
    """Opt-in evidence query that can refine missing evidence within VI-12 budgets.

    Ordinary ``evidence_for`` remains a read-only query. This entry point
    requires explicit producer callbacks and a model refresh/commit callback.
    """
    from library.tools.query_directed_acquisition import query_with_acquisition

    return query_with_acquisition(
        model, plan, source_digest=source_digest,
        duration_seconds=duration_seconds, cached=cached,
        producers=producers, refresh_model=refresh_model,
        max_cost_seconds=max_cost_seconds, available_lanes=available_lanes,
        producer_budgets=producer_budgets,
        resolvable_ambiguities=resolvable_ambiguities or ())


def _basis_for(model: dict[str, Any], plan: dict[str, Any],
               per_predicate: list[list[dict[str, Any]]],
               level: str | None) -> list[dict[str, Any]]:
    lanes: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for matches in per_predicate:
        for match in matches:
            key = (match["lane"], match["obs"].get("asset_id"))
            slot = lanes.setdefault(key, {
                "lane": match["lane"], "asset_id": match["obs"].get(
                    "asset_id"), "coverage": set(), "freshness": set(),
                "producer_version": None})
            slot["coverage"].add(match["coverage"])
            slot["freshness"].add(match["freshness"])
            version = _producer_version_of(model, match["obs"])
            if version:
                slot["producer_version"] = version
    return [{"lane": lane, "coverage": sorted(slot["coverage"]),
             "freshness": sorted(slot["freshness"]),
             "scope": ({"asset_id": asset} if asset else {}),
             "producer_version": slot["producer_version"],
             "level": level}
            for (lane, asset), slot in sorted(
                lanes.items(), key=lambda item: str(item[0]))]


def _empty_answer(model: dict[str, Any], plan: dict[str, Any],
                  per_predicate: list[list[dict[str, Any]]],
                  scope: dict[str, Any], level: str | None,
                  lane_presence: dict[tuple[str, str], dict[str, Any]] | None,
                  reasons: list[str]) -> dict[str, Any]:
    candidate_only = any(
        compiled["predicate"] == "event_span"
        and compiled["event"] not in MEASURED_EVENTS
        and not matches
        and _has_unverified_candidates(model, compiled, scope)
        for compiled, matches in zip(plan["predicates"], per_predicate))
    lane_states = _lane_states(model, plan, per_predicate, scope,
                               lane_presence)
    missing = sorted(lane for lane, state in lane_states.items()
                     if state == "missing")
    ran_unmigrated = sorted(lane for lane, state in lane_states.items()
                            if state == "ran_unmigrated")
    partial = sorted(lane for lane, state in lane_states.items()
                     if state in ("partial", "stale", "legacy"))
    if candidate_only:
        reasons = reasons + [("candidates are never answers: unverified "
                              "spans name what to verify, never what happened")]
        return {"status": INCONCLUSIVE, "results": [],
                "completeness_basis": _empty_basis(
                    lane_states, scope, level),
                "required_missing_lanes": missing, "reasons": reasons}
    if missing and not partial and not ran_unmigrated:
        reasons = reasons + [
            (f"lane(s) {missing} never ran: nothing measured, so "
             f"nothing can be absent yet")]
        return {"status": NOT_YET_ANALYSED, "results": [],
                "completeness_basis": _empty_basis(
                    lane_states, scope, level),
                "required_missing_lanes": missing, "reasons": reasons}
    if ran_unmigrated or partial:
        if ran_unmigrated:
            reasons = reasons + [
                (f"lane(s) {ran_unmigrated} ran but migrated nothing: "
                 f"the migration dropped or refused its records")]
        if partial:
            reasons = reasons + [
                (f"lane(s) {partial} cover only part of the scope, are "
                 f"stale, or carry no coverage: absence needs fresh "
                 f"complete coverage, not a gap")]
        return {"status": INCONCLUSIVE, "results": [],
                "completeness_basis": _empty_basis(
                    lane_states, scope, level),
                "required_missing_lanes": missing, "reasons": reasons}
    if missing:
        reasons = reasons + [
            (f"lane(s) {missing} never ran: nothing measured, so "
             f"nothing can be absent yet")]
        return {"status": NOT_YET_ANALYSED, "results": [],
                "completeness_basis": _empty_basis(
                    lane_states, scope, level),
                "required_missing_lanes": missing, "reasons": reasons}
    reasons = reasons + [("every required lane measured the scope "
                          "completely and found nothing")]
    return {"status": CONFIDENTLY_ABSENT, "results": [],
            "completeness_basis": _empty_basis(lane_states, scope, level),
            "required_missing_lanes": [], "reasons": reasons}


def _has_unverified_candidates(model: dict[str, Any],
                               compiled: dict[str, Any],
                               scope: dict[str, Any]) -> bool:
    for obs in model.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        modality = obs.get("modality")
        payload = obs.get("payload") or {}
        if payload.get("spans") == []:
            continue
        if modality == "event_candidate":
            if payload.get("predicate") != compiled["event"]:
                continue
        elif modality == "relationship_candidate":
            if payload.get("relation") != compiled["event"]:
                continue
        else:
            continue
        ticks = _observation_interval(obs)
        if ticks is None:
            continue
        clock_id, start, end = ticks
        if scope.get("clock_id") and clock_id != scope["clock_id"]:
            continue
        if "start_s" in scope and end <= _ticks(scope["start_s"]):
            continue
        if "end_s" in scope and start >= _ticks(scope["end_s"]):
            continue
        return True
    return False


def _lane_states(model: dict[str, Any], plan: dict[str, Any],
                 per_predicate: list[list[dict[str, Any]]],
                 scope: dict[str, Any],
                 lane_presence: dict[tuple[str, str], dict[str, Any]] | None
                 ) -> dict[str, str]:
    """One state per required lane: covers_absent, partial, stale,
    legacy, missing, or ran_unmigrated.

    State reads ALL of the lane's records, not just this query's
    matches: a word no transcript contains is still a lane that ran.
    Only a fresh measured-absence record (complete coverage, zero
    samples, spanning the scope) makes absence confident.
    """
    states: dict[str, str] = {}
    lane_records: dict[str, list[dict[str, Any]]] = {}
    for obs in model.get("observations") or []:
        if not isinstance(obs, dict):
            continue
        lane = _lane_of(obs)
        if lane is None:
            kind = _record_kind(obs)
            lane = MEASUREMENT_TO_LANE.get(kind or "")
        if lane:
            lane_records.setdefault(lane, []).append(obs)
    for compiled in plan["predicates"]:
        required = _required_lane(compiled)
        if required is None or required in states:
            continue
        records = [obs for obs in lane_records.get(required, [])
                   if (not scope.get("asset_id")
                       or obs.get("asset_id") == scope["asset_id"])
                   and (not scope.get("clock_id")
                        or _observation_clock(obs) == scope["clock_id"])]
        fresh = [obs for obs in records if _freshness_of(obs) == "fresh"]
        if any(_record_answers_predicate(obs, compiled)
               and _covers_scope(obs, scope) for obs in fresh):
            states[required] = "covers_absent"
            continue
        if fresh:
            states[required] = "partial"
            continue
        if any(_freshness_of(obs) == "stale" for obs in records):
            states[required] = "stale"
            continue
        if records:
            states[required] = "legacy"
            continue
        if lane_presence is not None and _lane_ran(
                required, scope, lane_presence):
            states[required] = "ran_unmigrated"
        else:
            states[required] = "missing"
    return states


def _record_answers_predicate(obs: dict[str, Any],
                              compiled: dict[str, Any]) -> bool:
    """Keep lane completeness evidence scoped to the requested measurement."""
    if compiled["predicate"] == "measurement":
        return _record_kind(obs) == compiled["measurement_type"]
    payload = obs.get("payload") or {}
    if compiled["event"] in MEASURED_EVENTS:
        return (obs.get("modality") == "event_span"
                and payload.get("predicate") == compiled["event"])
    return (obs.get("modality") == "event_candidate"
            and payload.get("predicate") == compiled["event"])


def _observation_clock(obs: dict[str, Any]) -> str | None:
    ticks = _observation_interval(obs)
    return ticks[0] if ticks is not None else None


def _covers_scope(obs: dict[str, Any], scope: dict[str, Any]) -> bool:
    """Whether one record is a measured absence across the scope: native
    coverage complete, zero samples, empty value, and - for a windowed
    scope - an interval covering the window."""
    if not _is_measured_absence(obs):
        return False
    if "start_s" not in scope and "end_s" not in scope:
        return True
    ticks = _observation_interval(obs)
    if ticks is None:
        return False
    _clock_id, start, end = ticks
    if "start_s" in scope and start > _ticks(scope["start_s"]):
        return False
    return not ("end_s" in scope and end < _ticks(scope["end_s"]))


def _is_measured_absence(obs: dict[str, Any]) -> bool:
    """A record that looked and found nothing: native coverage
    complete with a zero-sample or empty value."""
    native = _native_evidence(obs)
    if native is None:
        return False
    if native.get("coverage") != "complete":
        return False
    if native.get("coverage_samples") not in (0, None):
        return False
    value = native.get("value")
    return value in ([], {}, None)


def _lane_ran(lane: str, scope: dict[str, Any],
              lane_presence: dict[tuple[str, str], dict[str, Any]]) -> bool:
    asset = scope.get("asset_id")
    for (digest, name), info in lane_presence.items():
        _ = digest
        if name != lane:
            continue
        if asset is not None:
            clock_asset = info.get("asset_id")
            if clock_asset is not None and clock_asset != asset:
                continue
        if isinstance(info, dict) and info.get("present"):
            return True
    return False


def _empty_basis(lane_states: dict[str, str], scope: dict[str, Any],
                 level: str | None) -> list[dict[str, Any]]:
    basis_scope: dict[str, Any] = {}
    if scope.get("clock_id"):
        basis_scope["clock_id"] = scope["clock_id"]
    if scope.get("asset_id"):
        basis_scope["asset_id"] = scope["asset_id"]
    if "start_s" in scope or "end_s" in scope:
        basis_scope["window_s"] = [scope.get("start_s"), scope.get("end_s")]
    return [{"lane": lane, "coverage": state, "freshness": "unknown",
             "scope": dict(basis_scope), "producer_version": None,
             "level": level}
            for lane, state in sorted(lane_states.items())]
