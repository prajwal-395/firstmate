"""FI-05: typed interval query over the Project Model + source-memory lanes.

``evidence_for(model, plan)`` answers one typed query plan with
interval-joined results, provenance and coverage per result, and one of
four states - ``present``, ``confidently_absent``, ``not_yet_analysed``,
``inconclusive``. ``compile_plan`` builds the plan; ``plan_from_filter``
compiles the evidence-backed subset of
``FootageIndex.filter()`` kwargs so the two stores can be parity-checked.

A plan may also ask about a NAMED OBJECT (``object_presence``). An
object request names the object and the task requirement it carries,
and its answer carries the detections AND the coverage map - the frames
the pass inspected and the seconds it never looked at
(:mod:`library.tools.analysis.object_grounding`).

**The answer's own vocabulary is five states.** The four ``status``
values are the Project Model's row vocabulary (the Rust
``QueryResultStatus`` enum pins them), so a row can be written into the
model unchanged. The answer also carries ``answer_state``, the five-state
vocabulary of plan B-04 - ``present``, ``absent``, ``not_observed``,
``not_analyzed``, ``inconclusive`` - computed by
``evidence_graph.classify_absence``, which is the one implementation of
that table. A measured absence (``absent``) is not the same claim as a
complete lane that asserted nothing (``not_observed``), and the answer
says which one it is.

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
measurements. Synchronized cross-clock joins use bounded, piecewise
Project Model time transforms and refuse missing, out-of-domain or
over-bound mappings; independent mode returns per-observation results
without comparing their clock ticks. Interpretations and evaluations are never query evidence: a
result is carried by measurements, never by taste or a verdict.

**Time.** Source seconds on the way in and out; model intervals are
millisecond ticks on their own clock, converted without rounding the
evidence (ticks are integers already). Joins intersect half-open
``[start, end)`` tick ranges within ONE clock only. ``join_mode`` defaults
to ``synchronized``; it maps cross-clock intervals to the first predicate's
clock using time transforms whose uncertainty is at most
``max_clock_error_ticks`` (default zero). ``independent`` answers each
predicate observation separately and supports multiple source clocks.

**Reads.** The model dict only, plus an optional ``lane_presence``
hook ``{(digest, lane): {"present": bool, "freshness": str}}`` that
tells a lane that ran-but-was-never-migrated apart from one that never
ran. Without it, absence from the model reads as never analysed.
Nothing is written anywhere.
"""

from __future__ import annotations

from collections import deque
from fractions import Fraction
from itertools import pairwise
from typing import Any

try:
    from library.tools import evidence_contract, processing_status
    from library.tools import occurrence_identity
    from library.tools.analysis import object_grounding
    from library.tools.identity_reassociation import IDENTITY_SCOPES
    from library.tools.relation_classes import RELATION_CLASSES
except ImportError:  # imported as `tools.*` from inside library/
    from tools import evidence_contract, processing_status
    from tools import occurrence_identity
    from tools.analysis import object_grounding
    from tools.identity_reassociation import IDENTITY_SCOPES
    from tools.relation_classes import RELATION_CLASSES

TICKS_PER_SECOND = 1000
"""Millisecond source clocks, the convention
:mod:`library.tools.project_model_evidence` migrates onto."""

PRESENT = "present"
CONFIDENTLY_ABSENT = "confidently_absent"
NOT_YET_ANALYSED = "not_yet_analysed"
INCONCLUSIVE = "inconclusive"

ABSENT = "absent"
NOT_OBSERVED = "not_observed"
NOT_ANALYZED = "not_analyzed"

ANSWER_STATES = (PRESENT, ABSENT, NOT_OBSERVED, NOT_ANALYZED, INCONCLUSIVE)
"""The five-state answer vocabulary of plan B-04, in the order a reader
meets them. ``status`` keeps the model's four-state row vocabulary;
``answer_state`` is this one."""

COVERAGE_RANK = {"complete": 0, "sampled": 1, "inferred": 2, "unavailable": 3}
"""Coverage kinds from strongest to weakest: a completeness claim is a
claim about the whole lane, so the weakest record decides it."""

FRESHNESS_RANK = {"fresh": 0, "stale": 1, "unknown": 2}
"""Freshness from best to worst, in the same weakest-wins order."""


def classify_absence(status: str, *, has_explicit_negative: bool,
                     coverage_complete: bool) -> dict[str, str]:
    """Expand the four query outcomes into the DATA-036 answer vocabulary."""
    if status == PRESENT:
        return {"state": "present",
                "basis": "the lane returned evidence intervals"}
    if status == NOT_YET_ANALYSED:
        return {"state": "not_analyzed",
                "basis": "a required lane never produced evidence"}
    if status == INCONCLUSIVE or not coverage_complete:
        return {"state": "inconclusive",
                "basis": "coverage is partial, stale or missing"}
    if status == CONFIDENTLY_ABSENT and has_explicit_negative:
        return {"state": "absent",
                "basis": "an explicit negative assertion covers the scope"}
    if status == CONFIDENTLY_ABSENT:
        return {"state": "not_observed",
                "basis": "fresh complete coverage with no assertion either way"}
    raise ValueError(f"unknown evidence answer status {status!r}")


QUERY_STATES = (PRESENT, CONFIDENTLY_ABSENT, NOT_YET_ANALYSED, INCONCLUSIVE)
"""The four query outcomes. Complete: an answer carries exactly one."""

OBJECT_DETECTION_LANE = object_grounding.OBJECT_LANE
"""The lane the REQUEST-driven object detector writes into.

It is not 1.06's ``segmentation`` lane: a provenance record names one
producer, and a request is written by another one
(:mod:`library.tools.analysis.object_grounding`). The object
DETECTION measurement is answered by this lane, while the object's
track samples and visibility stay with the tracker that measured them.
"""


def answer_state(status: str, *, has_explicit_negative: bool,
                 coverage_complete: bool) -> dict[str, str]:
    """The five-state answer, computed by the graph layer's table.

    ``evidence_graph.classify_absence`` is the one implementation of the
    DATA-036 vocabulary; the query names it instead of repeating the
    table, so the two layers cannot disagree about what an absence is.
    """
    from library.tools import evidence_graph

    return evidence_graph.classify_absence(
        status, has_explicit_negative=has_explicit_negative,
        coverage_complete=coverage_complete)

MEASURED_EVENTS = ("speaking", "on_screen")
"""M7 predicate spans: diarized turns attributed to a face, and visible
face tracks. Anything else names an M7/M10 candidate kind and is never
an answer until verified."""

GRAPH_PREDICATES = ("entity", "identity_link", "relation", "interpretation",
                    "event", "track")
"""Predicate kinds answered from the Project Model's own collections.

The interval predicates answer "what was measured over this window";
these answer "what does the model hold about this thing". They are
routed to :mod:`library.tools.evidence_graph` - the read layer that
already indexes the model - and they never mix with the interval
predicates: a plan asks ONE kind of question, because a join between a
measured interval and a model row has no defined meaning."""

LINK_STATUSES = ("candidate", "verified")
"""The identity-link status vocabulary the model validates."""

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
    # OCR/UI tracks migrate under the ocr_extraction lane
    # (`ocr_project_model.py`): without this, an OCR measurement plan
    # names no required lane and absence is claimed with no coverage.
    "visual_text_track": "ocr_extraction",
    "visual_graphic_candidate": "ocr_extraction",
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
    # Object presence is answered by the REQUEST-driven detector, whose
    # provenance lane is `object_detection`: it is the lane that can run
    # on request. The tracker's own detector-seeded detections keep the
    # tracker's provenance lane, and the tracked object's visibility
    # stays with the tracker that measured it.
    "object.detection": OBJECT_DETECTION_LANE,
    "object.visibility": "segmentation",
    # OCR text and graphic tracks migrate under `ocr_extraction`
    # (see MODALITY_TO_LANE above): same coverage argument.
    "image.ocr_text_track": "ocr_extraction",
    "image.graphic_region_track": "ocr_extraction",
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
    return evidence_contract.point_to_tick(float(seconds), TICKS_PER_SECOND)


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


def freshness_of(obs: dict[str, Any]) -> str:
    native = _native_evidence(obs)
    freshness = (native or {}).get("freshness")
    if freshness in evidence_contract.COVERAGE_FRESHNESS:
        return freshness
    return "unknown"


def coverage_of(obs: dict[str, Any]) -> str:
    native = _native_evidence(obs)
    coverage = (native or {}).get("coverage_status") or (native or {}).get("coverage")
    if isinstance(coverage, str) and coverage:
        return coverage
    return "unknown"


def certainty_of(obs: dict[str, Any]) -> str | None:
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
                 join_mode: str = "synchronized",
                 max_clock_error_ticks: int = 0) -> dict[str, Any]:
    """Compile typed predicates into a query plan, or raise.

    A predicate is ``{"predicate": "measurement", "measurement_type",
    ...}`` with optional ``min``/``max``/``equals`` value bounds and
    ``clock_id``/``asset_id``/``target_entity_id`` scopes, or
    ``{"predicate": "event_span", "event", ...}`` with optional
    ``participant_id`` and ``verified_only``, or
    ``{"predicate": "nth_event", "event_class", "nth", ...}`` which
    names the n-th occurrence of a repeated event, or one of the GRAPH
    PREDICATES - ``entity``, ``identity_link``, ``relation``,
    ``interpretation``, ``event``, ``track`` - which name a row of the
    Project Model's own collections and are answered by
    :mod:`library.tools.evidence_graph`. ``scope`` is an optional
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
    if type(max_clock_error_ticks) is not int or max_clock_error_ticks < 0:
        raise EvidenceQueryError("max_clock_error_ticks must be a non-negative integer")
    compiled = [_compile_predicate(predicate) for predicate in predicates]
    forms = {predicate.get("form", "interval") for predicate in compiled}
    if len(forms) > 1:
        raise EvidenceQueryError(
            "a plan asks one kind of question: interval evidence "
            "('measurement', 'event_span') or graph evidence "
            f"{list(GRAPH_PREDICATES)}, never both")
    lanes = sorted({lane for lane in
                    (_required_lane(p) for p in compiled) if lane})
    object_queries = [{"phrase": p["phrase"], "requirement": p["requirement"]}
                      for p in compiled if p["predicate"] == "object_presence"]
    return {"predicates": compiled, "required_lanes": lanes,
            "object_queries": object_queries,
            "scope": plan_scope, "level": level, "join_mode": join_mode,
            "max_clock_error_ticks": max_clock_error_ticks}


def _compile_predicate(predicate: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(predicate, dict):
        raise EvidenceQueryError("predicates are mappings")
    kind = predicate.get("predicate")
    if kind == "measurement":
        return _compile_measurement(predicate)
    if kind == "event_span":
        return _compile_event(predicate)
    if kind == "nth_event":
        return _compile_nth_event(predicate)
    if kind == "object_presence":
        return _compile_object(predicate)
    if kind in GRAPH_PREDICATES:
        return _compile_graph(predicate)
    raise EvidenceQueryError(
        f"unknown predicate {kind!r}: this query answers "
        f"'measurement', 'event_span', 'nth_event', 'object_presence' and "
        f"the graph predicates {list(GRAPH_PREDICATES)}")


def _compile_object(predicate: dict[str, Any]) -> dict[str, Any]:
    """A request for a named object, compiled.

    The query names the object and the task requirement it carries:
    ``presence`` asks whether the object is in the window at all,
    ``grounded`` asks where it is. An unknown requirement is refused -
    a request that does not say what it needs gets neither a silent
    upgrade nor a silent downgrade.
    """
    phrase = predicate.get("phrase")
    if not isinstance(phrase, str) or not phrase.strip():
        raise EvidenceQueryError(
            "an object_presence names the object it asks about")
    requirement = predicate.get("requirement") or "grounded"
    if requirement not in object_grounding.REQUIREMENTS:
        raise EvidenceQueryError(
            f"unknown task requirement {requirement!r}: one of "
            f"{sorted(object_grounding.REQUIREMENTS)}")
    return {"predicate": "object_presence", "phrase": phrase.strip(),
            "requirement": requirement,
            "measurement_type": "object.detection", "unit": "record"}


def _string_selector(predicate: dict[str, Any], key: str) -> str | None:
    """One optional string selector, refused when it is not a string."""
    value = predicate.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise EvidenceQueryError(f"{key} must be a non-empty string")
    return value.strip()


def _enum_selector(predicate: dict[str, Any], key: str,
                   allowed: tuple[str, ...]) -> str | None:
    """One optional selector drawn from a closed vocabulary."""
    value = predicate.get(key)
    if value is None:
        return None
    if value not in allowed:
        raise EvidenceQueryError(
            f"{key} {value!r} is not one of {list(allowed)}")
    return value


def _compile_graph(predicate: dict[str, Any]) -> dict[str, Any]:
    """Compile one graph predicate: a named row of a model collection."""
    kind = predicate["predicate"]
    compiled: dict[str, Any] = {"predicate": kind, "form": "graph"}
    if kind == "entity":
        for key in ("entity_id", "label", "kind"):
            value = _string_selector(predicate, key)
            if value is not None:
                compiled[key] = value
        scope = _enum_selector(predicate, "identity_scope", IDENTITY_SCOPES)
        if scope is not None:
            compiled["identity_scope"] = scope
    elif kind == "identity_link":
        for key in ("entity_id", "observation_id", "method"):
            value = _string_selector(predicate, key)
            if value is not None:
                compiled[key] = value
        status = _enum_selector(predicate, "status", LINK_STATUSES)
        if status is not None:
            compiled["status"] = status
    elif kind == "relation":
        # The relation's own predicate is named ``relation_type``: a
        # graph predicate already spends the word "predicate" on its
        # kind, and one word cannot mean two things.
        for key in ("relation_type", "subject_id", "object_id"):
            value = _string_selector(predicate, key)
            if value is not None:
                compiled[key] = value
        relation_class = _enum_selector(predicate, "relation_class",
                                        RELATION_CLASSES)
        if relation_class is not None:
            compiled["relation_class"] = relation_class
        status = _enum_selector(predicate, "status", LINK_STATUSES)
        if status is not None:
            compiled["status"] = status
    elif kind == "interpretation":
        for key in ("kind", "evidence_id"):
            value = _string_selector(predicate, key)
            if value is not None:
                compiled[key] = value
        certainty = _enum_selector(
            predicate, "certainty",
            tuple(state.value for state in evidence_contract.CertaintyState))
        if certainty is not None:
            compiled["certainty"] = certainty
    elif kind == "event":
        for key in ("kind", "participant_id"):
            value = _string_selector(predicate, key)
            if value is not None:
                compiled[key] = value
        status = _enum_selector(predicate, "status", LINK_STATUSES)
        if status is not None:
            compiled["status"] = status
    elif kind == "track":
        for key in ("entity_id", "clock_id"):
            value = _string_selector(predicate, key)
            if value is not None:
                compiled[key] = value
    # A graph predicate with no selector asks for EVERY row of its
    # collection: the collection IS what it names, so the question is
    # answerable and is never widened into a guess.
    return compiled


def _compile_measurement(predicate: dict[str, Any]) -> dict[str, Any]:
    measurement_type = predicate.get("measurement_type")
    if not isinstance(measurement_type, str) or not measurement_type:
        raise EvidenceQueryError("a measurement names its measurement_type")
    try:
        entry = evidence_contract.registry_entry(measurement_type)
    except evidence_contract.UnknownMeasurementTypeError:
        raise EvidenceQueryError(
            f"unknown measurement type {measurement_type!r}") from None
    if entry["kind"] not in (evidence_contract.EvidenceType.MEASUREMENT,
                             evidence_contract.EvidenceType.INFERENCE):
        raise EvidenceQueryError(
            f"{measurement_type!r} is {entry['kind'].value}, not a "
            f"measurement: a query is answered by measurements and by the "
            f"inferences a model earned, never by a verdict or taste")
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


def _compile_nth_event(predicate: dict[str, Any]) -> dict[str, Any]:
    """Compile an ordinal predicate: the n-th occurrence of a class.

    The class is the model's own event kind, the subject is the entity
    the occurrence counts for, and ``nth`` is 1-based. A query that
    does not say which source it means is answered per source or
    REFUSED, never from whichever source happens to be first.
    """
    event_class = predicate.get("event_class")
    if not isinstance(event_class, str) or not event_class.strip():
        raise EvidenceQueryError("an nth_event names its event_class")
    nth = predicate.get("nth", predicate.get("occurrence_index"))
    if isinstance(nth, bool) or not isinstance(nth, int) or nth < 1:
        raise EvidenceQueryError("an nth_event names a 1-based nth")
    compiled: dict[str, Any] = {"predicate": "nth_event",
                                "event_class": event_class.strip(),
                                "nth": nth}
    for key in ("subject_id", "asset_id"):
        value = predicate.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise EvidenceQueryError(f"{key} must be a string")
        compiled[key] = value.strip()
    return compiled


def _required_lane(compiled: dict[str, Any]) -> str | None:
    if compiled["predicate"] == "measurement":
        return MEASUREMENT_TO_LANE.get(compiled["measurement_type"])
    if compiled.get("form") == "graph":
        # A model row IS the answer: no lane produced it, and no lane's
        # absence says anything about it.
        return None
    if compiled["predicate"] == "nth_event":
        # The events lane: the model numbers the occurrences the lane
        # produced, so a missing lane means no occurrence was measured.
        return "m7"
    if compiled["predicate"] == "object_presence":
        # The request-driven object lane: the lane that can run on
        # request, not the tracker that runs on a plan.
        return OBJECT_DETECTION_LANE
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


def _object_phrase_of(obs: dict[str, Any]) -> str:
    """The object name one row answers about."""
    native = _native_evidence(obs) or {}
    value = native.get("value")
    if isinstance(value, dict):
        for key in ("query", "phrase", "label"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
    return ""


def _is_coverage_row(obs: dict[str, Any]) -> bool:
    """A row that records what a pass LOOKED at, not a detection.

    The coverage row answers the same question a detection answers, so
    a query that counted it would report an object where the pass found
    none.
    """
    native = _native_evidence(obs) or {}
    value = native.get("value")
    return isinstance(value, dict) and value.get("kind") == "coverage"


def _matches_object(compiled: dict[str, Any], obs: dict[str, Any]) -> bool:
    """Whether a row is a detection of the object the query named."""
    if _record_kind(obs) != compiled["measurement_type"]:
        return False
    if _is_coverage_row(obs):
        return False
    wanted = str(compiled["phrase"]).strip().lower()
    return _object_phrase_of(obs).strip().lower() == wanted


def _belongs_to_query(obs: dict[str, Any],
                      compiled: dict[str, Any]) -> bool:
    """Whether a lane record is about the question this query asks.

    An object request is about ONE object: another object's detections
    are a different question's evidence, and a lane that answered
    "where is the phone" has not answered "where is the handheld
    device". The lane's coverage is counted per question, or a second
    request would read the first one's rows as a partial answer.
    """
    if compiled["predicate"] != "object_presence":
        return True
    if _matches_object(compiled, obs):
        return True
    if _is_coverage_row(obs):
        return (_object_phrase_of(obs).strip().lower()
                == str(compiled["phrase"]).strip().lower())
    return False


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
                        entities: dict[str, set[str]],
                        ordinal: dict[str, Any] | None = None
                        ) -> list[dict[str, Any]]:
    """Every observation interval one predicate is evidence for.

    An ordinal predicate is answered by the event it resolved to: only
    the observations that event cites are its evidence, and an ordinal
    that resolved to nothing matches nothing.
    """
    matches: list[dict[str, Any]] = []
    ordinal_evidence: set[str] = set()
    if compiled["predicate"] == "nth_event":
        if ordinal is None:
            return matches
        ordinal_evidence = {str(eid) for eid in (ordinal.get("evidence_ids") or [])}
        if not ordinal_evidence:
            return matches
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
        if compiled["predicate"] == "nth_event":
            if str(obs.get("id")) not in ordinal_evidence:
                continue
        native = _native_evidence(obs)
        if compiled["predicate"] == "measurement":
            if _record_kind(obs) != compiled["measurement_type"]:
                continue
            if (compiled["measurement_type"] == "object.detection"
                    and _is_coverage_row(obs)):
                # A coverage row records the look, not an object.
                continue
            if (compiled.get("target_entity_id")
                    and (native or {}).get("target_entity_id")
                    != compiled["target_entity_id"]):
                continue
            if not _matches_value(compiled, native, obs):
                continue
        elif compiled["predicate"] == "object_presence":
            if not _matches_object(compiled, obs):
                continue
        elif compiled["predicate"] == "event_span":
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
                        "freshness": freshness_of(obs),
                        "coverage": coverage_of(obs),
                        "certainty": certainty_of(obs)})
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


def _transform_path(model: dict[str, Any], source: str, target: str,
                    ) -> list[tuple[dict[str, Any], bool]] | None:
    """Find a directed path; bool marks inverse edges."""
    queue = deque([(source, [])])
    seen = {source}
    while queue:
        clock, path = queue.popleft()
        if clock == target:
            return path
        for transform in model.get("time_transforms", []):
            edges = [(transform.get("from_clock"), transform.get("to_clock"), False)]
            if all(segment.get("invertible") for segment in transform.get("segments", [])):
                edges.append((transform.get("to_clock"), transform.get("from_clock"), True))
            for from_clock, to_clock, inverse in edges:
                if from_clock != clock or to_clock in seen:
                    continue
                segments = transform.get("segments", [])
                if (not segments or any(type(s.get("uncertainty_ticks")) is not int
                                        or s["uncertainty_ticks"] < 0
                                        for s in segments)):
                    continue
                seen.add(to_clock)
                queue.append((to_clock, path + [(transform, inverse)]))
    return None


def _map_interval(clock: str, start: int, end: int,
                  path: list[tuple[dict[str, Any], bool]], max_error: int
                  ) -> tuple[list[tuple[str, int, int, int]] | None, bool]:
    """Map intervals through piecewise segments, refusing uncovered ticks."""
    pieces = [(clock, start, end, 0)]
    for transform, inverse in path:
        mapped: list[tuple[str, int, int, int]] = []
        exceeded = False
        for piece_clock, lo, hi, prior_error in pieces:
            candidates = []
            covered = []
            for segment in transform["segments"]:
                src_key, dst_key = ("to", "from") if inverse else ("from", "to")
                src, dst = segment[src_key], segment[dst_key]
                if src["clock_id"] != piece_clock:
                    continue
                a, b = max(lo, src["start"]), min(hi, src["end"])
                if b <= a:
                    continue
                covered.append((a, b))
                slope = segment["slope"]
                ratio = Fraction(slope["numerator"], slope["denominator"])
                if ratio <= 0:
                    continue
                output_ratio = 1 / ratio if inverse else ratio
                uncertainty = segment["uncertainty_ticks"]
                error = -(-(prior_error * output_ratio).numerator //
                          (prior_error * output_ratio).denominator) + uncertainty
                if error > max_error:
                    exceeded = True
                    continue
                offset = segment["offset_ticks"]
                if inverse:
                    a, b = (Fraction(a - offset, 1) / ratio,
                            Fraction(b - offset, 1) / ratio)
                else:
                    a, b = a * ratio + offset, b * ratio + offset
                # Map to integral ticks conservatively: only retain the
                # interval guaranteed to be covered under the error bound.
                out_lo = -(-a.numerator // a.denominator) + uncertainty
                out_hi = b.numerator // b.denominator - uncertainty
                if out_hi > out_lo:
                    candidates.append((dst["clock_id"], out_lo, out_hi, error))
            candidates.sort(key=lambda row: row[1])
            cursor = lo
            for a, b in sorted(covered):
                if a > cursor:
                    break
                cursor = max(cursor, b)
                if cursor >= hi:
                    break
            if cursor < hi:
                return None, exceeded
            mapped.extend(candidates)
        pieces = mapped
        if not pieces:
            return None, exceeded
    return pieces, False


def placements(model: dict[str, Any], observations: list[dict[str, Any]],
               project: str | None) -> list[dict[str, Any]]:
    """Where each observation came from: the file, and the exact seconds."""
    if not observations:
        return []
    try:
        from library.tools import observation_provenance
        return observation_provenance.source_placements(
            model, observations, project_folder=project)
    except (ImportError, ValueError):
        return []


def placements_by_id(model: dict[str, Any],
                     observations: list[dict[str, Any]],
                     project: str | None) -> dict[str, dict[str, Any]]:
    """The placements of many observations, in ONE pass, by id.

    The placement algebra indexes the model, so it is paid once per
    answer and never once per row.
    """
    rows = placements(model, observations, project)
    return {row.get("observation_id"): row for row in rows
            if isinstance(row.get("observation_id"), str)}


def payload_of(observation: dict[str, Any]) -> dict[str, Any]:
    """The observation's payload, when it has one."""
    payload = observation.get("payload")
    return payload if isinstance(payload, dict) else {}


def derivation_rows(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What produced each observation: the derivation, and its references.

    An answer that names the evidence but not the derivation cannot
    answer "what produced this". Every observation gets a row, and a row
    whose derivation was never recorded says so - a hole is visible, not
    filled with a default.
    """
    rows: list[dict[str, Any]] = []
    for observation in observations:
        native = _native_evidence(observation)
        legacy = observation.get("evidence")
        legacy = legacy if isinstance(legacy, dict) else {}
        native = native or legacy
        derivation = native.get("derivation")
        derivation = derivation if isinstance(derivation, dict) else None
        model_provenance = payload_of(observation).get("model_provenance")
        license_context = None
        if isinstance(model_provenance, dict):
            license_context = model_provenance.get("license_context")
        if license_context is None:
            license_context = native.get("license_context")
        references = native.get("references")
        if not references:
            references = legacy.get("references")
        rows.append({
            "observation_id": observation.get("id"),
            "derivation_recorded": derivation is not None,
            "producer": native.get("producer"),
            "producer_version": native.get("producer_version"),
            "method": native.get("method"),
            "sampling_method": native.get("sampling_method"),
            "preprocessing": (derivation or {}).get("preprocessing"),
            "sampling_policy": (derivation or {}).get("sampling_policy"),
            "source_revision": (derivation or {}).get("source_revision"),
            "derivation_digest": (derivation or {}).get("derivation_digest"),
            "license_context": license_context,
            "references": list(references or []),
        })
    return rows


def _graph_answer(model: dict[str, Any], plan: dict[str, Any],
                  compiled: list[dict[str, Any]],
                  project: str | None) -> dict[str, Any]:
    """Answer a graph plan from the Project Model's own collections.

    The rows come from :mod:`library.tools.evidence_graph`, the read
    layer that already indexes the model. Every result names the
    observations that back it, where each of them came from in the
    source file, and what produced it.

    The status vocabulary here is the five-way DATA-036 one, not the
    four-way measured-absence one: a model row that is absent is not a
    measured absence, it is a row the model does not hold.
    """
    from library.tools import evidence_graph

    index = evidence_graph.EvidenceIndex(model)
    results: list[dict[str, Any]] = []
    every_observation: dict[str, dict[str, Any]] = {}
    for predicate in compiled:
        for row in _graph_rows(index, predicate):
            observations = _backing_observations(index, predicate, row)
            for observation in observations:
                every_observation.setdefault(observation["id"], observation)
            results.append({
                "predicate": predicate["predicate"],
                "id": row.get("id"),
                "row": row,
                "evidence_ids": sorted({obs["id"] for obs in observations}),
                "provenance_ids": sorted({str(obs.get("provenance_id"))
                                         for obs in observations
                                         if obs.get("provenance_id")}),
                "observation_ids": [obs["id"] for obs in observations],
                "derivation": derivation_rows(observations),
            })
    placement = placements_by_id(model, list(every_observation.values()), project)
    for result in results:
        result["sources"] = [placement[observation_id]
                             for observation_id in result["observation_ids"]
                             if observation_id in placement]
        del result["observation_ids"]
    if results:
        return {"status": "present", "results": results,
                "answer_state": "present",
                "completeness_basis": [], "required_missing_lanes": [],
                "reasons": [],
                "lane_corrections": [],
                "absence": {"state": "present",
                            "basis": "the model holds the rows this plan names"}}
    return {"status": "not_observed", "results": [],
            "answer_state": "not_observed",
            "completeness_basis": [], "required_missing_lanes": [],
            "reasons": [
                ("the model holds no row matching this predicate: nothing "
                 "was measured absent, the model simply carries no such row")],
            "lane_corrections": [],
            "absence": {"state": "not_observed",
                        "basis": "the model holds no row of this kind"}}


def _graph_rows(index: Any, compiled: dict[str, Any]) -> list[dict[str, Any]]:
    """The model rows one graph predicate names."""
    kind = compiled["predicate"]
    model = index.model
    if kind == "entity":
        rows = [row for row in model.get("entities") or []
                if isinstance(row, dict)]
        if "entity_id" in compiled:
            rows = [row for row in rows if row.get("id") == compiled["entity_id"]]
        if "label" in compiled:
            wanted = compiled["label"].lower()
            rows = [row for row in rows
                    if str(row.get("label") or "").lower() == wanted]
        if "kind" in compiled:
            rows = [row for row in rows if row.get("kind") == compiled["kind"]]
        if "identity_scope" in compiled:
            rows = [row for row in rows
                    if row.get("identity_scope") == compiled["identity_scope"]]
        return rows
    if kind == "identity_link":
        rows = [row for row in model.get("identity_links") or []
                if isinstance(row, dict)]
        for key in ("entity_id", "observation_id", "method", "status"):
            if key in compiled:
                rows = [row for row in rows if row.get(key) == compiled[key]]
        return rows
    if kind == "relation":
        rows = [row for row in model.get("relations") or []
                if isinstance(row, dict)]
        for key in ("subject_id", "object_id", "status"):
            if key in compiled:
                rows = [row for row in rows if row.get(key) == compiled[key]]
        if "relation_class" in compiled:
            rows = [row for row in rows
                    if row.get("relation_class") == compiled["relation_class"]]
        if "relation_type" in compiled:
            wanted = compiled["relation_type"]
            rows = [row for row in rows
                    if (row.get("predicate") or row.get("relation_type"))
                    == wanted]
        return rows
    if kind == "interpretation":
        rows = [row for row in model.get("interpretations") or []
                if isinstance(row, dict)]
        if "kind" in compiled:
            rows = [row for row in rows if row.get("kind") == compiled["kind"]]
        if "certainty" in compiled:
            rows = [row for row in rows
                    if row.get("certainty") == compiled["certainty"]]
        if "evidence_id" in compiled:
            rows = [row for row in rows
                    if compiled["evidence_id"] in (row.get("evidence_ids") or [])]
        return rows
    if kind == "event":
        rows = [row for row in model.get("events") or []
                if isinstance(row, dict)]
        if "kind" in compiled:
            rows = [row for row in rows if row.get("kind") == compiled["kind"]]
        if "status" in compiled:
            rows = [row for row in rows if row.get("status") == compiled["status"]]
        if "participant_id" in compiled:
            rows = [row for row in rows
                    if compiled["participant_id"] in (row.get("participant_ids") or [])]
        return rows
    if kind == "track":
        rows = [row for row in model.get("tracks") or []
                if isinstance(row, dict)]
        for key in ("entity_id", "clock_id"):
            if key in compiled:
                rows = [row for row in rows if row.get(key) == compiled[key]]
        return rows
    raise EvidenceQueryError(f"unknown graph predicate {kind!r}")


def _backing_observations(index: Any, compiled: dict[str, Any],
                          row: dict[str, Any]) -> list[dict[str, Any]]:
    """The observations a model row rests on."""
    kind = compiled["predicate"]
    if kind == "entity":
        return index.observations_for_entity(row.get("id"))
    if kind == "identity_link":
        named = [row.get("observation_id")]
        named.extend(row.get("supporting_observation_ids") or [])
        return index.observations_by_ids([name for name in named if name])
    if kind == "relation":
        return index.observations_by_ids(row.get("evidence_ids") or [])
    if kind == "interpretation":
        return index.observations_by_ids(row.get("evidence_ids") or [])
    if kind == "event":
        return index.observations_by_ids(row.get("evidence_ids") or [])
    if kind == "track":
        named: list[str] = []
        for sample in row.get("samples") or []:
            if isinstance(sample, dict):
                named.extend(sample.get("evidence_ids") or [])
        return index.observations_by_ids(named)
    raise EvidenceQueryError(f"unknown graph predicate {kind!r}")


def evidence_for(model: dict[str, Any], plan: dict[str, Any], *,
                 lane_presence: dict[tuple[str, str], dict[str, Any]] | None = None,
                 project: str | None = None
                 ) -> dict[str, Any]:
    """Answer one compiled plan over a Project Model dict.

    Returns ``{"status", "answer_state", "answer_basis", "results",
    "candidate_evidence_ids", "completeness_basis",
    "required_missing_lanes", "reasons"}``. Results carry their joined
    interval in ticks and seconds plus the contributing evidence ids,
    provenance ids, certainties, coverage and freshness - the reader
    can place every row without reopening the model - and the
    ``derivation`` of every contributing observation, so the answer can
    also say what produced it. ``lane_presence`` optionally maps
    ``(digest, lane)`` to ``{"present", "freshness"}`` sidecar truth so
    a lane that ran but never migrated reads ``inconclusive`` instead
    of ``not_yet_analysed``.

    ``status`` is the model's four-state row vocabulary; ``answer_state``
    is the five-state answer of plan B-04 (see :func:`answer_state`).

    A plan of GRAPH predicates is answered from the model's own
    collections by :mod:`library.tools.evidence_graph`; the answer then
    carries the DATA-036 five-way vocabulary, because a model row that
    is absent is not a measured absence.
    """
    if not isinstance(model, dict):
        raise EvidenceQueryError("evidence_for reads a Project Model dict")
    if (not isinstance(plan, dict) or not isinstance(
            plan.get("predicates"), list) or not plan["predicates"]):
        raise EvidenceQueryError("evidence_for answers a compiled plan")
    # The predicates are compiled HERE as well as in compile_plan: a
    # plan may arrive as a JSON file an agent wrote by hand, and an
    # unknown or malformed predicate is refused, never guessed at.
    compiled_predicates = [_compile_predicate(predicate)
                           for predicate in plan["predicates"]]
    forms = {predicate.get("form", "interval") for predicate in compiled_predicates}
    if len(forms) > 1:
        raise EvidenceQueryError(
            "a plan asks one kind of question: interval evidence "
            "('measurement', 'event_span') or graph evidence "
            f"{list(GRAPH_PREDICATES)}, never both")
    if "graph" in forms:
        return _graph_answer(model, plan, compiled_predicates, project)
    plan = {**plan, "predicates": compiled_predicates}
    scope = plan.get("scope") or {}
    level = plan.get("level")
    entities = _observation_entities(model)
    ordinals: list[dict[str, Any] | None] = []
    for compiled in plan["predicates"]:
        if compiled["predicate"] != "nth_event":
            ordinals.append(None)
            continue
        try:
            ordinals.append(occurrence_identity.nth_event(
                model, compiled["event_class"],
                subject_id=compiled.get("subject_id"),
                asset_id=compiled.get("asset_id") or scope.get("asset_id"),
                n=compiled["nth"]))
        except occurrence_identity.OccurrenceIdentityError as exc:
            # The class was measured in more than one source and the
            # query did not say which: the n-th occurrence is a
            # different moment in each, so the answer is refused.
            return {"status": INCONCLUSIVE, "results": [],
                    "completeness_basis": [],
                    "required_missing_lanes": sorted(set(
                        plan.get("required_lanes") or [])),
                    "reasons": [str(exc)]}
    per_predicate = [_match_observations(model, compiled, scope, entities,
                                         ordinal)
                     for compiled, ordinal in zip(plan["predicates"],
                                                  ordinals)]
    clocks = sorted({match["clock_id"] for matches in per_predicate
                     for match in matches})
    join_mode = plan.get("join_mode", "synchronized")
    if len(clocks) > 1 and join_mode == "synchronized":
        target_clock = per_predicate[0][0]["clock_id"]
        max_error = plan.get("max_clock_error_ticks", 0)
        transformed: list[list[dict[str, Any]]] = []
        refusal: str | None = None
        for matches in per_predicate:
            rows: list[dict[str, Any]] = []
            for match in matches:
                if match["clock_id"] == target_clock:
                    rows.append(match)
                    continue
                path = _transform_path(model, match["clock_id"], target_clock)
                if path is None:
                    refusal = (f"no time-transform path from {match['clock_id']} "
                               f"to {target_clock} within {max_error} ticks")
                    break
                pieces, exceeded = _map_interval(
                    match["clock_id"], match["start"],
                    match["end"], path, max_error)
                if pieces is None:
                    refusal = ((f"time-transform error exceeds {max_error} ticks "
                                f"for interval [{match['start']}, {match['end']})")
                               if exceeded else
                               (f"interval [{match['start']}, {match['end']}) "
                                f"is outside the valid time-transform domain"))
                    break
                for clock_id, start, end, error in pieces:
                    rows.append({**match, "clock_id": clock_id,
                                 "start": start, "end": end,
                                 "transform_error_ticks": error,
                                 "transform_ids": [t["id"] for t, _ in path]})
            transformed.append(rows)
            if refusal:
                break
        if refusal:
            return _answer(INCONCLUSIVE, results=[],
                           completeness_basis=[],
                           required_missing_lanes=sorted(set(
                               plan.get("required_lanes") or [])),
                           reasons=[refusal],
                           coverage_complete=False)
        per_predicate = transformed
        clocks = [target_clock]
    if join_mode == "independent":
        joined = [(match["start"], match["end"], [match])
                  for matches in per_predicate for match in matches]
    else:
        joined = _intersect(per_predicate) if all(per_predicate) else []
    fresh_results: list[dict[str, Any]] = []
    stale_dropped = 0
    result_observations: list[list[dict[str, Any]]] = []
    for lo, hi, contributors in joined:
        if any(match["freshness"] == "stale" for match in contributors):
            stale_dropped += 1
            continue
        clock_id = contributors[0]["clock_id"]
        observations = [match["obs"] for match in contributors]
        result_observations.append(observations)
        row = {
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
            "time_transform_ids": sorted({transform_id
                for match in contributors
                for transform_id in match.get("transform_ids", [])}),
            "clock_error_ticks": max((match.get("transform_error_ticks", 0)
                                       for match in contributors), default=0),
            "derivation": derivation_rows(observations),
        }
        row.update(_occurrence_of_row(model, plan, ordinals, contributors))
        fresh_results.append(row)
    # The placements are paid ONCE per answer: the placement algebra
    # indexes the model, so a per-row call would index it per row.
    every_observation: dict[str, dict[str, Any]] = {}
    for observations in result_observations:
        for observation in observations:
            if observation.get("id"):
                every_observation.setdefault(observation["id"], observation)
    placement = placements_by_id(model, list(every_observation.values()),
                                 project)
    for result, observations in zip(fresh_results, result_observations):
        result["sources"] = [placement[observation["id"]]
                             for observation in observations
                             if observation.get("id") in placement]
    reasons: list[str] = []
    if stale_dropped:
        reasons.append(f"{stale_dropped} joined interval(s) include stale "
                       f"required evidence and carry no answer")
    for compiled, ordinal in zip(plan["predicates"], ordinals):
        if compiled["predicate"] != "nth_event" or ordinal is not None:
            continue
        klass = compiled["event_class"]
        known = len(occurrence_identity.events_of_class(
            model, klass, subject_id=compiled.get("subject_id"),
            asset_id=compiled.get("asset_id") or scope.get("asset_id")))
        reasons.append(
            f"the model carries {known} occurrence(s) of {klass!r}: there "
            f"is no occurrence {compiled['nth']}")
    corrections = _lane_corrections(model, compiled_predicates)
    reasons.extend(_correction_reasons(corrections))
    extension = _object_extension(model, plan, per_predicate, scope)
    if fresh_results:
        return _answer(PRESENT, results=fresh_results,
                       completeness_basis=_basis_for(
                           model, plan, per_predicate, level),
                       required_missing_lanes=[],
                       reasons=reasons,
                       candidate_evidence_ids={
                           evidence_id
                           for result in fresh_results
                           for evidence_id in result["evidence_ids"]},
                       lane_corrections=corrections, **extension)
    answer = _empty_answer(model, plan, per_predicate, scope, level,
                            lane_presence, reasons, corrections)
    answer.update(extension)
    return answer


def _object_extension(model: dict[str, Any], plan: dict[str, Any],
                      per_predicate: list[list[dict[str, Any]]],
                      scope: dict[str, Any]) -> dict[str, Any]:
    """The detections, the coverage map and the coverage table.

    A query for a named object answers with the boxes it found AND with
    what it looked at: the coverage map names the frames the pass
    inspected and the seconds it never looked at, so a reader can tell a
    narrow answer from a broad one.
    """
    object_predicates = [compiled for compiled in plan["predicates"]
                         if compiled["predicate"] == "object_presence"]
    if not object_predicates:
        return {}
    matches: list[dict[str, Any]] = []
    for compiled, rows in zip(plan["predicates"], per_predicate):
        if compiled["predicate"] == "object_presence":
            matches.extend(rows)
    detections = object_grounding.detection_rows(matches)
    phrases = [compiled["phrase"] for compiled in object_predicates]
    coverage = object_grounding.coverage_map_for(
        model, phrases=phrases, scope=scope)
    table = object_grounding.coverage_table(
        detections, frames_inspected=int(coverage.get("frames_inspected") or 0))
    identities: dict[str, dict[str, Any]] = {}
    for row in detections:
        entity_id = row.get("entity_id")
        if not entity_id:
            continue
        entry = identities.setdefault(entity_id, {
            "phrase": row.get("query"), "detections": 0, "frames": [],
            "visibility": {}})
        entry["detections"] += 1
        if row.get("frame_index") is not None:
            entry["frames"].append(row["frame_index"])
        if row.get("visibility"):
            entry["visibility"][str(row["frame_index"])] = row["visibility"]
    return {"detections": detections, "coverage_map": coverage,
            "coverage_by_category": table,
            "identities": identities}


def _answer(status: str, *, results: list[dict[str, Any]],
            completeness_basis: list[dict[str, Any]],
            required_missing_lanes: list[str], reasons: list[str],
            has_explicit_negative: bool = False,
            coverage_complete: bool = True,
            candidate_evidence_ids: set[str] | None = None,
            **extra: Any) -> dict[str, Any]:
    """One answer, carrying both vocabularies and its own basis.

    ``status`` says which of the model's four row states this answer
    is; ``answer_state`` says which of the five answer states it is.
    The two are not the same claim: ``confidently_absent`` covers both
    a measured absence and a complete lane that asserted nothing, and
    only the five-state answer tells those apart.
    """
    state = answer_state(status,
                         has_explicit_negative=has_explicit_negative,
                         coverage_complete=coverage_complete)
    return {"status": status,
            "answer_state": state["state"],
            "absence": {"state": state["state"], "basis": state["basis"]},
            "results": results,
            "candidate_evidence_ids": sorted(candidate_evidence_ids or set()),
            "completeness_basis": completeness_basis,
            "required_missing_lanes": list(required_missing_lanes),
            "reasons": list(reasons),
            **extra}


def _occurrence_of_row(model: dict[str, Any], plan: dict[str, Any],
                       ordinals: list[dict[str, Any] | None],
                       contributors: list[dict[str, Any]]
                       ) -> dict[str, Any]:
    """The occurrence a result row names, when the plan asked for one.

    An ordinal predicate answers with the event it resolved to, and the
    row says WHICH occurrence that was: the event id, the class, the
    subject and the position - plus the receipt that lets a later
    reader check the answer without re-running the lane that produced
    it.
    """
    wanted = {str(match["obs"].get("id")) for match in contributors}
    for compiled, ordinal in zip(plan["predicates"], ordinals):
        if compiled["predicate"] != "nth_event" or ordinal is None:
            continue
        evidence = {str(eid) for eid in (ordinal.get("evidence_ids") or [])}
        if not (evidence & wanted):
            continue
        described = occurrence_identity.descriptor(model, ordinal)
        return {
            "event_id": described["event_id"],
            "occurrence": {
                "class": described["event_class"],
                "subject_id": described["subject_id"],
                "index": described["occurrence_index"],
            },
            "occurrence_key": described["occurrence_key"],
            "occurrence_receipt": occurrence_identity.receipt(model, ordinal),
        }
    return {}


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
    return [{"lane": lane,
             "coverage": _weakest_coverage(slot["coverage"]),
             "freshness": _weakest_freshness(slot["freshness"]),
             "lane_state": "present",
             "scope": ({"asset_id": asset} if asset else {}),
             "producer_version": slot["producer_version"],
             "level": level}
            for (lane, asset), slot in sorted(
                lanes.items(), key=lambda item: str(item[0]))]


def _weakest_coverage(values: Any) -> str:
    """The weakest coverage among a lane's records.

    A completeness claim is a claim about the whole lane, so the
    weakest record decides it: a lane that is complete for one record
    and sampled for another has not covered the scope completely.
    """
    candidates = [value for value in (values or []) if value in COVERAGE_RANK]
    if not candidates:
        return "unavailable"
    return max(candidates, key=lambda value: COVERAGE_RANK[value])


def _weakest_freshness(values: Any) -> str:
    """The weakest freshness among a lane's records."""
    candidates = [value for value in (values or []) if value in FRESHNESS_RANK]
    if not candidates:
        return "unknown"
    return max(candidates, key=lambda value: FRESHNESS_RANK[value])


def _empty_answer(model: dict[str, Any], plan: dict[str, Any],
                  per_predicate: list[list[dict[str, Any]]],
                  scope: dict[str, Any], level: str | None,
                  lane_presence: dict[tuple[str, str], dict[str, Any]] | None,
                  reasons: list[str],
                  corrections: list[dict[str, Any]] | None = None
                  ) -> dict[str, Any]:
    corrections = corrections or []
    candidate_only = any(
        compiled["predicate"] == "event_span"
        and compiled["event"] not in MEASURED_EVENTS
        and not matches
        and _has_unverified_candidates(model, compiled, scope)
        for compiled, matches in zip(plan["predicates"], per_predicate))
    lane_states = _lane_states(model, plan, per_predicate, scope,
                               lane_presence)
    states = {lane: info["state"] for lane, info in lane_states.items()}
    missing = sorted(lane for lane, state in states.items()
                     if state == "missing")
    not_analyzed = sorted(lane for lane, state in states.items()
                          if state == "not_analyzed")
    ran_unmigrated = sorted(lane for lane, state in states.items()
                            if state == "ran_unmigrated")
    failed = sorted(lane for lane, state in states.items()
                    if state == "failed")
    partial = sorted(lane for lane, state in states.items()
                     if state in ("partial", "stale", "legacy"))
    basis = _empty_basis(lane_states, scope, level)
    if candidate_only:
        reasons = reasons + [("candidates are never answers: unverified "
                              "spans name what to verify, never what happened")]
        return _answer(INCONCLUSIVE, results=[], completeness_basis=basis,
                       required_missing_lanes=missing, reasons=reasons,
                       coverage_complete=False,
                       lane_corrections=corrections)
    if (missing or not_analyzed) and not partial and not ran_unmigrated \
            and not failed:
        if missing:
            reasons = reasons + [
                (f"lane(s) {missing} never ran: nothing measured, so "
                 f"nothing can be absent yet")]
        if not_analyzed:
            reasons = reasons + [
                (f"lane(s) {not_analyzed} ran but analysed nothing: "
                 f"no evidence was produced, so nothing can be absent yet")]
        return _answer(NOT_YET_ANALYSED, results=[],
                       completeness_basis=basis,
                       required_missing_lanes=sorted(missing + not_analyzed),
                       reasons=reasons, lane_corrections=corrections)
    if ran_unmigrated or partial or failed:
        if ran_unmigrated:
            reasons = reasons + [
                (f"lane(s) {ran_unmigrated} ran but migrated nothing: "
                 f"the migration dropped or refused its records")]
        if partial:
            reasons = reasons + [
                (f"lane(s) {partial} cover only part of the scope, are "
                 f"stale, or carry no coverage: absence needs fresh "
                 f"complete coverage, not a gap")]
        if failed:
            reasons = reasons + [
                (f"lane(s) {failed} failed: the analysis did not "
                 f"complete, so no claim either way is available")]
        return _answer(INCONCLUSIVE, results=[], completeness_basis=basis,
                       required_missing_lanes=sorted(
                           missing + not_analyzed + failed),
                       reasons=reasons, coverage_complete=False,
                       lane_corrections=corrections)
    if missing or not_analyzed:
        reasons = reasons + [
            (f"lane(s) {sorted(missing + not_analyzed)} produced no "
             f"evidence: nothing measured, so nothing can be absent yet")]
        return _answer(NOT_YET_ANALYSED, results=[],
                       completeness_basis=basis,
                       required_missing_lanes=sorted(missing + not_analyzed),
                       reasons=reasons, lane_corrections=corrections)
    reasons = reasons + [("every required lane measured the scope "
                          "completely and found nothing")]
    return _answer(CONFIDENTLY_ABSENT, results=[],
                   completeness_basis=basis,
                   required_missing_lanes=[],
                   reasons=reasons,
                   has_explicit_negative=True,
                   candidate_evidence_ids={
                       evidence_id
                       for lane, info in lane_states.items()
                       for evidence_id in info.get("evidence_ids", [])},
                   lane_corrections=corrections)


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
                 ) -> dict[str, dict[str, Any]]:
    """One state per required lane: covers_absent, partial, stale,
    legacy, missing, ran_unmigrated, failed or not_analyzed.

    State reads ALL of the lane's records, not just this query's
    matches: a word no transcript contains is still a lane that ran.
    Only a fresh measured-absence record (complete coverage, zero
    samples, spanning the scope) makes absence confident.

    A lane whose records all say the analysis FAILED is a lane that
    ran and did not finish: it is neither absent nor unanalysed, and
    the answer says so instead of reading the gap as an absence.
    """
    states: dict[str, dict[str, Any]] = {}
    lane_records = _lane_records(model)
    for compiled in plan["predicates"]:
        required = _required_lane(compiled)
        if required is None:
            continue
        for lane in _effective_lanes(lane_records, compiled, required):
            if lane in states:
                continue
            records = [obs for obs
                       in _scoped_records(lane_records.get(lane, []), scope)
                       if _belongs_to_query(obs, compiled)]
            fresh = [obs for obs in records if freshness_of(obs) == "fresh"]
            absences = [obs for obs in fresh
                        if _record_answers_predicate(obs, compiled)
                        and _covers_scope(obs, scope)
                        and _establishes_absence(obs)]
            if absences:
                states[lane] = {
                    "state": "covers_absent",
                    "producer_version": _producer_version_of(model,
                                                             absences[0]),
                    "evidence_ids": sorted({str(obs.get("id"))
                                            for obs in absences
                                            if obs.get("id")}),
                }
                continue
            processing = {processing_status.processing_status_of(obs)
                          for obs in records} - {None}
            if processing and processing <= {"failed"}:
                states[lane] = {"state": "failed",
                                "producer_version": _producer_version_of(
                                    model, records[0]),
                                "evidence_ids": []}
                continue
            if processing and processing <= {"not_analyzed"}:
                states[lane] = {"state": "not_analyzed",
                                "producer_version": _producer_version_of(
                                    model, records[0]),
                                "evidence_ids": []}
                continue
            if fresh:
                states[lane] = {"state": "partial",
                                "producer_version": _producer_version_of(
                                    model, fresh[0]),
                                "evidence_ids": []}
                continue
            if any(freshness_of(obs) == "stale" for obs in records):
                states[lane] = {"state": "stale",
                                "producer_version": _producer_version_of(
                                    model, records[0]),
                                "evidence_ids": []}
                continue
            if records:
                states[lane] = {"state": "legacy",
                                "producer_version": _producer_version_of(
                                    model, records[0]),
                                "evidence_ids": []}
                continue
            if lane_presence is not None and _lane_ran(
                    lane, scope, lane_presence):
                states[lane] = {"state": "ran_unmigrated",
                                "producer_version": None,
                                "evidence_ids": []}
            else:
                states[lane] = {"state": "missing", "producer_version": None,
                                "evidence_ids": []}
    return states


def _establishes_absence(obs: dict[str, Any]) -> bool:
    """Whether a record may carry a measured absence.

    A record whose processing status says the analysis failed, was
    unavailable or was never made carries no absence: its coverage
    claim is void, and a void claim cannot say the thing is not there.
    A legacy record (no status at all) keeps whatever coverage it
    recorded.
    """
    status = processing_status.processing_status_of(obs)
    return status in (None, processing_status.ANALYZED)


def _lane_records(model: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Every observation, filed under the lane that produced it.

    The lane comes from the producer's OWN stamp - the lane suffix of
    the provenance id - so a lane that produced evidence is the lane
    the answer talks about.
    """
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
    return lane_records


def _scoped_records(records: list[dict[str, Any]],
                    scope: dict[str, Any]) -> list[dict[str, Any]]:
    """The records of one lane that fall inside the query's scope."""
    return [obs for obs in records
            if (not scope.get("asset_id")
                or obs.get("asset_id") == scope["asset_id"])
            and (not scope.get("clock_id")
                 or _observation_clock(obs) == scope["clock_id"])]


def _producer_lanes(lane_records: dict[str, list[dict[str, Any]]],
                    compiled: dict[str, Any], required: str
                    ) -> list[str]:
    """The lanes whose OWN records answer this predicate.

    A declared lane that produced nothing of this kind is not evidence
    that nothing was produced: the registry's lane map is a
    declaration, and the producer's own stamp is the record. These
    lanes are what the answer is judged on, and the correction is
    reported.
    """
    producers = []
    for lane, rows in lane_records.items():
        if lane == required:
            continue
        if any(_record_answers_predicate(obs, compiled) for obs in rows):
            producers.append(lane)
    return sorted(producers)


def _produced(lane_records: dict[str, list[dict[str, Any]]],
              compiled: dict[str, Any], lane: str) -> bool:
    """Whether one lane produced the evidence this predicate asks about."""
    return any(_record_answers_predicate(obs, compiled)
               for obs in lane_records.get(lane, []))


def _effective_lanes(lane_records: dict[str, list[dict[str, Any]]],
                     compiled: dict[str, Any], required: str) -> list[str]:
    """The lanes an answer about this predicate is judged on."""
    if _produced(lane_records, compiled, required):
        return [required]
    producers = _producer_lanes(lane_records, compiled, required)
    return producers or [required]


def _lane_corrections(model: dict[str, Any],
                      compiled_predicates: list[dict[str, Any]]
                      ) -> list[dict[str, Any]]:
    """Where the registry's lane map names a lane the producer contradicts.

    The declared lane is reported AND the lane that actually produced
    the evidence, so a reader sees both rather than being told one
    lane never ran when another one did.
    """
    lane_records = _lane_records(model)
    corrections: list[dict[str, Any]] = []
    for compiled in compiled_predicates:
        required = _required_lane(compiled)
        if required is None:
            continue
        if _produced(lane_records, compiled, required):
            continue
        for lane in _producer_lanes(lane_records, compiled, required):
            corrections.append({
                "predicate": compiled["predicate"],
                "measurement_type": compiled.get("measurement_type"),
                "event": compiled.get("event"),
                "declared_lane": required,
                "producer_lane": lane,
            })
    return corrections


def _correction_reasons(corrections: list[dict[str, Any]]) -> list[str]:
    """One reason per corrected lane, naming both lanes."""
    reasons = []
    for correction in corrections:
        asked = correction.get("measurement_type") or correction.get("event")
        reasons.append(
            f"the registry names lane {correction['declared_lane']!r} for "
            f"{asked!r}, but the producer's own lane stamp is "
            f"{correction['producer_lane']!r}: the answer is judged on the "
            f"lane that produced the evidence")
    return reasons


def _record_answers_predicate(obs: dict[str, Any],
                              compiled: dict[str, Any]) -> bool:
    """Keep lane completeness evidence scoped to the requested measurement."""
    if compiled["predicate"] == "measurement":
        return _record_kind(obs) == compiled["measurement_type"]
    if compiled["predicate"] == "object_presence":
        return _matches_object(compiled, obs)
    payload = obs.get("payload") or {}
    if compiled["predicate"] == "nth_event":
        # The events lane answers an ordinal question with the records
        # of the class it names.
        return payload.get("predicate") == compiled["event_class"]
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
    if not _is_measured_absence(obs) or not _explicit_coverage_spans_scope(obs, scope):
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
    if (native.get("coverage_status") != "complete"
            or native.get("coverage") != "complete"
            or native.get("verified_status") not in ("verified", "not_required")):
        return False
    if native.get("coverage_samples") not in (0, None):
        return False
    value = native.get("value")
    return value in ([], {}, None)


def _explicit_coverage_spans_scope(obs: dict[str, Any],
                                   scope: dict[str, Any]) -> bool:
    """Only explicit, fresh coverage can establish a negative answer.

    An observation's matching interval is not a coverage claim. Old records
    without temporal coverage and sparse samples therefore abstain.
    """
    native = _native_evidence(obs)
    intervals = (native or {}).get("temporal_coverage")
    if not isinstance(intervals, list) or not intervals:
        return False
    ticks = _observation_interval(obs)
    if ticks is None:
        return False
    clock_id, record_start, record_end = ticks
    start = _ticks(scope["start_s"]) if "start_s" in scope else record_start
    end = _ticks(scope["end_s"]) if "end_s" in scope else record_end
    ranges = sorted(
        (row.get("start"), row.get("end"))
        for row in intervals
        if isinstance(row, dict) and row.get("clock_id") == clock_id
        and isinstance(row.get("start"), int)
        and isinstance(row.get("end"), int)
        and row["end"] > row["start"])
    cursor = start
    for lo, hi in ranges:
        if hi <= cursor:
            continue
        if lo > cursor:
            return False
        cursor = max(cursor, hi)
        if cursor >= end:
            return True
    return False


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


LANE_STATE_COVERAGE = {
    "covers_absent": "complete",
    "partial": "sampled",
    "stale": "sampled",
    "legacy": "unavailable",
    "missing": "unavailable",
    "ran_unmigrated": "unavailable",
    "failed": "unavailable",
    "not_analyzed": "unavailable",
}
"""Lane state to the model's coverage kind. A lane that measured the
scope completely and found nothing is ``complete``; a lane that covered
part of it is ``sampled``; a lane that never looked is ``unavailable``."""

LANE_STATE_FRESHNESS = {
    "covers_absent": "fresh",
    "partial": "fresh",
    "stale": "stale",
    "legacy": "unknown",
    "missing": "unknown",
    "ran_unmigrated": "unknown",
    "failed": "fresh",
    "not_analyzed": "fresh",
}
"""Lane state to the model's freshness. Freshness says how current the
producer run behind the lane is; a lane that failed is current - it ran
against this source - and its failure is named in the reason."""


def _empty_basis(lane_states: dict[str, dict[str, Any]], scope: dict[str, Any],
                 level: str | None) -> list[dict[str, Any]]:
    basis_scope: dict[str, Any] = {}
    if scope.get("clock_id"):
        basis_scope["clock_id"] = scope["clock_id"]
    if scope.get("asset_id"):
        basis_scope["asset_id"] = scope["asset_id"]
    if "start_s" in scope or "end_s" in scope:
        basis_scope["window_s"] = [scope.get("start_s"), scope.get("end_s")]
    return [{"lane": lane,
             "coverage": LANE_STATE_COVERAGE.get(info["state"], "unavailable"),
             "freshness": LANE_STATE_FRESHNESS.get(info["state"], "unknown"),
             "lane_state": info["state"],
             "scope": dict(basis_scope),
             "producer_version": info.get("producer_version"),
             "level": level}
            for lane, info in sorted(lane_states.items())]
