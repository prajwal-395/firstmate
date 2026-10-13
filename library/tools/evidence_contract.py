"""FI-03: the measurement/evidence contract for footage intelligence.

Plan workstream A ("Ren - Unified Footage Intelligence & Adaptive
Perception", 2026-10-12): measure what can be measured, infer what
cannot be observed directly, and apply subjective judgment only when
the editing objective requires it. This module is the type system
that enforces that separation.

Four evidence types (plan A1): ``measurement`` (an instrument's
numeric or categorical output, e.g. Laplacian variance 142.6),
``inference`` (an interpretation with uncertainty, e.g. defocus
likelihood), ``evaluation`` (a measurement judged against an explicit
versioned policy - whose thresholds FI-22 owns, not this module), and
``creative_judgment`` (a subjective decision for one edit). A raw
instrument value must never inhabit an evaluation field: constructing
one raises :class:`EvidenceTypeError`.

Every record carries the plan A2 metadata: asset, interval on a
named clock, target entity/region, measurement type/value/unit/
method/sample-resolution, coverage (samples + rate), and a
provenance id. The unit + method registry below is the whole list:
sharpness at 180px and sharpness at 720p are different methods and
never compare equal.

Temporal alignment (plan A3): source ticks are authoritative.
Per-second bins (M9's) are an access optimization derived from
ticks, never a reduction of them - nothing here alters the M9 bins.
Rational/variable frame rates, multicam offsets with uncertainty,
and subsecond precision are preserved.

Provenance + coverage (plan C4 + A2 item 7): ``complete`` /
``sampled`` / ``inferred`` / ``unavailable``. A measured absence
(``complete`` with zero samples) is different from unmeasured
(``unavailable``): the scheduler must never read sparse sampling as
evidence that nothing happened.

Focus-distance honesty (plan C1) is encoded in the registry:
``lens.focus_distance_m`` is recorded lens metadata only (a
non-recorded method raises), ``image.sharpness`` is measured, and
``image.defocus_likelihood`` is an inference that must carry
uncertainty.

Storage is deliberately undecided here: records are plain JSON-able
dicts/dataclasses, so they ride the existing per-lane sidecars.
Whether dense curves later move to SQLite is the captain's open
call C3; nothing here depends on its answer.

Rust shape: ``native/ren-protocol/src/project_model.rs`` carries the
same contract as ``EvidenceContract`` on ``Observation`` (additive
``Option`` field, schema version unchanged). :func:`to_rust_evidence`
emits exactly that shape; its key set is pinned by test against the
Rust struct's serde names.

Real caller: ``library/tools/project_model_evidence.py`` audits
every migrated observation through :func:`audit_legacy_observation`.
:func:`migrate_m1_word` / :func:`migrate_m3_face_span` are the
FI-04-ready constructors for the M1/M3 lanes, proven here by tests;
the current migration keeps its shape and output byte-identical.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

EVIDENCE_CONTRACT_VERSION = 1
"""Version of this contract. Readers refuse records stamped newer."""

TICKS_PER_SECOND = 1000
"""Millisecond source clocks, the same convention
``project_model_evidence`` migrates on: integer ticks preserve every
measured span without a frame-rate assumption no lane recorded."""


class EvidenceType(str, Enum):
    MEASUREMENT = "measurement"
    INFERENCE = "inference"
    EVALUATION = "evaluation"
    CREATIVE_JUDGMENT = "creative_judgment"


class CertaintyState(str, Enum):
    OBSERVED = "observed"
    MEASURED = "measured"
    INFERRED = "inferred"
    INTERPRETED = "interpreted"


class ProcessingStatus(str, Enum):
    NOT_ANALYZED = "not_analyzed"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


class CoverageKind(str, Enum):
    COMPLETE = "complete"
    SAMPLED = "sampled"
    INFERRED = "inferred"
    UNAVAILABLE = "unavailable"


#: Coverage levels: the L0/L1/L2 analysis policy the contract must be able
#: to express without building the scheduler here.
ANALYSIS_LEVELS = ("l0", "l1", "l2")

#: Freshness of the producer run behind a record.
COVERAGE_FRESHNESS = ("fresh", "stale", "unknown")

#: Typed evidence-link kinds: a record cites the source samples/frames or
#: input observations it was read from, never a bare description.
EVIDENCE_REFERENCE_KINDS = ("source_sample", "source_frame", "observation")


class EvidenceContractError(ValueError):
    """Base for every contract refusal."""


class EvidenceTypeError(EvidenceContractError):
    """A value filed under the wrong evidence type."""


class UnknownMeasurementTypeError(EvidenceContractError):
    """A measurement type the registry does not know."""


class UnitMismatchError(EvidenceContractError):
    """A value carrying a unit its type does not declare."""


class TemporalAlignmentError(EvidenceContractError):
    """An interval that cannot sit on its clock."""


# ── Unit + method registry ─────────────────────────────────────────
#
# The whole vocabulary. A type's unit, kind and resolution rule are
# fixed here so two producers cannot silently disagree about what a
# number means. Thresholds are NOT here: what passes or fails is a
# versioned evaluation policy (FI-22), never a registry constant.

_REGISTRY: dict[str, dict[str, Any]] = {
    "image.sharpness.laplacian_variance": {
        "unit": "variance",
        "kind": EvidenceType.MEASUREMENT,
        "requires_resolution": True,
        "description": "Variance of the 4-neighbour Laplacian over a "
        "downscaled greyscale frame. Resolution-bound: the same frame "
        "at 180px and at 720p yields different methods.",
    },
    "lens.focus_distance_m": {
        "unit": "m",
        "kind": EvidenceType.MEASUREMENT,
        "requires_resolution": False,
        "recorded_only": True,
        "description": "Lens-reported focus distance in metres. Recorded "
        "metadata only: an image-derived estimate filed here raises.",
    },
    "image.defocus_likelihood": {
        "unit": "probability",
        "kind": EvidenceType.INFERENCE,
        "requires_resolution": True,
        "description": "Model-derived likelihood the picture is defocused, "
        "with uncertainty. Never a distance, never a verdict.",
    },
    "speech.word": {
        "unit": "text",
        "kind": EvidenceType.MEASUREMENT,
        "requires_resolution": False,
        "description": "One transcribed word with MFA boundaries (M1).",
    },
    "speech.utterance": {
        "unit": "text",
        "kind": EvidenceType.MEASUREMENT,
        "requires_resolution": False,
        "description": "One transcribed utterance: words plus text (M1).",
    },
    "person.face_box": {
        "unit": "normalized",
        "kind": EvidenceType.MEASUREMENT,
        "requires_resolution": True,
        "description": "One measured face box, image-normalised top-left "
        "origin (M3/M3b). Resolution names the pixels measured.",
    },
    "person.voice_turn": {
        "unit": "span",
        "kind": EvidenceType.MEASUREMENT,
        "requires_resolution": False,
        "description": "One diarized voice turn span (M3b/M1b).",
    },
    "eval.sharpness_pass": {
        "unit": "boolean",
        "kind": EvidenceType.EVALUATION,
        "requires_resolution": False,
        "description": "A sharpness measurement judged against a versioned "
        "policy. Carries the policy id and the measurement ids it read.",
    },
    "eval.dialogue_preserved": {
        "unit": "boolean",
        "kind": EvidenceType.EVALUATION,
        "requires_resolution": False,
        "description": "Dialogue continuity judged against a versioned "
        "policy, citing the speech evidence it read.",
    },
    "creative.soft_focus_keep": {
        "unit": "decision",
        "kind": EvidenceType.CREATIVE_JUDGMENT,
        "requires_resolution": False,
        "description": "A subjective decision to keep soft focus for one "
        "edit. Carries whose brief authorised it, never a measurement.",
    },
}

# Method stamps the lanes write today (FI-02 producer inventory).
# Documentary, not enforced: presence of a method on every record IS
# enforced, membership in this list is NOT - M1/M3b stamp instrument
# dicts rather than single strings, so their migrations carry the
# lane's stamp verbatim (FI-04 wires the real ones). An image method
# appends "@<short-side-px>" naming the pixels measured (contract
# vocabulary): the same instrument at two geometries is two methods
# of one family, never one method.
KNOWN_METHODS: tuple[str, ...] = (
    "vision-helper-v2 over M2",
    "expression-classifier-v2-m2-crops",
    "person-instance-mask-v1",
    "attention-saliency-v1-m2",
    "region-stats-v1-m2",
    "relationships-v2 over M3 pose + M3d regions",
    "picture-quality-deterministic_v1@180px",
)


def registry_entry(measurement_type: str) -> dict[str, Any]:
    """The registry row for a type, or raise when unknown."""
    try:
        return _REGISTRY[measurement_type]
    except KeyError:
        raise UnknownMeasurementTypeError(
            f"unknown measurement type {measurement_type!r}") from None


def method_family(method: str) -> str:
    """The instrument family: the method before any "@resolution"."""
    return method.split("@", 1)[0]


def same_method(first: str, second: str) -> bool:
    """Exact method equality. Sharpness at 180px vs 720p is False."""
    return first == second


# ── The record ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvidenceRecord:
    """One versioned measurement record (plan A2)."""

    asset_id: str
    clock_id: str
    start_tick: int
    end_tick: int
    target_entity_id: str | None
    target_region: str | None
    evidence_type: EvidenceType
    measurement_type: str
    value: Any
    unit: str
    method: str
    sample_resolution: list[int] | None
    coverage: CoverageKind
    coverage_samples: int
    coverage_sample_rate_hz: float
    provenance_id: str
    uncertainty: float | None = None
    policy_id: str | None = None
    basis_measurement_ids: tuple[str, ...] = ()
    contract_version: int = EVIDENCE_CONTRACT_VERSION
    certainty_state: CertaintyState | None = None
    processing_status: ProcessingStatus | None = None
    source_reference: str | None = None
    spatial_frame_id: str | None = None
    coordinate_space_id: str | None = None
    references: tuple[dict[str, Any], ...] = ()
    coverage_scope: dict[str, Any] | None = None
    coverage_level: str | None = None
    producer_version: str | None = None
    freshness: str | None = None

    @property
    def certainty(self) -> CertaintyState | None:
        """FI-03 compatibility adapter; policy evaluation has no certainty."""
        if self.certainty_state is not None:
            if self.evidence_type is EvidenceType.EVALUATION:
                raise EvidenceTypeError("policy evaluation cannot carry epistemic certainty")
            allowed = {
                EvidenceType.MEASUREMENT: {CertaintyState.OBSERVED, CertaintyState.MEASURED},
                EvidenceType.INFERENCE: {CertaintyState.INFERRED},
                EvidenceType.CREATIVE_JUDGMENT: {CertaintyState.INTERPRETED},
            }
            if self.certainty_state not in allowed[self.evidence_type]:
                raise EvidenceTypeError("certainty conflicts with legacy evidence kind")
            return self.certainty_state
        return {
            EvidenceType.MEASUREMENT: CertaintyState.MEASURED,
            EvidenceType.INFERENCE: CertaintyState.INFERRED,
            EvidenceType.CREATIVE_JUDGMENT: CertaintyState.INTERPRETED,
            EvidenceType.EVALUATION: None,
        }[self.evidence_type]

    def __post_init__(self) -> None:
        if self.contract_version > EVIDENCE_CONTRACT_VERSION:
            raise EvidenceContractError(
                f"record stamped v{self.contract_version}, "
                f"this reader knows v{EVIDENCE_CONTRACT_VERSION}")
        if not isinstance(self.start_tick, int) or not isinstance(
                self.end_tick, int):
            raise TemporalAlignmentError("ticks are integers")
        if self.end_tick <= self.start_tick:
            raise TemporalAlignmentError(
                "interval must be non-empty and half-open (start < end)")
        if self.start_tick < 0:
            raise TemporalAlignmentError("ticks are non-negative")
        entry = registry_entry(self.measurement_type)
        if self.unit != entry["unit"]:
            raise UnitMismatchError(
                f"{self.measurement_type} carries unit {self.unit!r}, "
                f"registry declares {entry['unit']!r}")
        if self.evidence_type is not entry["kind"]:
            raise EvidenceTypeError(
                f"{self.measurement_type} is {entry['kind'].value}, "
                f"not {self.evidence_type.value}: a raw instrument value "
                f"must not inhabit an evaluation field")
        if entry.get("recorded_only") and not self.method.startswith(
                "recorded"):
            raise EvidenceTypeError(
                f"{self.measurement_type} is recorded lens metadata; "
                f"method {self.method!r} did not record it")
        if entry.get("requires_resolution") and (
                not isinstance(self.sample_resolution, list)
                or len(self.sample_resolution) != 2
                or not all(isinstance(v, int) and v > 0
                           for v in self.sample_resolution)):
            raise EvidenceContractError(
                f"{self.measurement_type} requires sample_resolution "
                f"[width, height] naming the pixels measured")
        if self.evidence_type is EvidenceType.INFERENCE:
            if self.uncertainty is None:
                raise EvidenceContractError(
                    f"{self.measurement_type}: an inference without "
                    f"uncertainty is a guess, not evidence")
            if not 0.0 <= self.uncertainty <= 1.0:
                raise EvidenceContractError("uncertainty must be 0..1")
        if self.evidence_type is EvidenceType.EVALUATION:
            if not self.policy_id:
                raise EvidenceTypeError(
                    "an evaluation without a policy id is taste filed as "
                    "fact: name the versioned policy (FI-22 owns it)")
            if not self.basis_measurement_ids:
                raise EvidenceTypeError(
                    "an evaluation must cite the measurement ids it read")
        if self.evidence_type is EvidenceType.CREATIVE_JUDGMENT \
                and not self.policy_id:
            raise EvidenceTypeError(
                "a creative judgment must name whose brief authorised "
                "it (policy_id carries the brief or decision ref)")
        if self.coverage_samples < 0:
            raise EvidenceContractError("coverage samples are non-negative")
        if not math.isfinite(self.coverage_sample_rate_hz):
            raise EvidenceContractError("coverage rate must be finite")
        if self.coverage is CoverageKind.UNAVAILABLE and (
                self.coverage_samples or self.value is not None):
            raise EvidenceContractError(
                "unavailable coverage carries no samples and no value")
        if not self.method:
            raise EvidenceContractError("every value carries its method")
        if not self.provenance_id:
            raise EvidenceContractError("every value carries provenance")
        # Eagerly refuse a certainty state its evidence kind cannot carry:
        # a measurement is observed or measured, an inference inferred, a
        # creative judgment interpreted, and a policy evaluation has none.
        _ = self.certainty
        if self.freshness is not None and self.freshness not in COVERAGE_FRESHNESS:
            raise EvidenceContractError(
                f"freshness {self.freshness!r} is not one of {COVERAGE_FRESHNESS}")
        if self.coverage_level is not None and self.coverage_level not in ANALYSIS_LEVELS:
            raise EvidenceContractError(
                f"coverage level {self.coverage_level!r} is not one of {ANALYSIS_LEVELS}")
        for reference in self.references:
            if not isinstance(reference, dict):
                raise EvidenceContractError("evidence references are mappings")
            kind = reference.get("kind")
            if kind not in EVIDENCE_REFERENCE_KINDS:
                raise EvidenceContractError(
                    f"evidence reference kind {kind!r} is not one of {EVIDENCE_REFERENCE_KINDS}")
            if kind in ("source_sample", "source_frame"):
                key = "sample_index" if kind == "source_sample" else "frame_index"
                if not reference.get("stream_id") or not isinstance(reference.get(key), int):
                    raise EvidenceContractError(
                        f"{kind} references carry a stream id and an integer {key}")
            elif not reference.get("observation_id"):
                raise EvidenceContractError(
                    "observation references carry an observation id")
        if self.coverage_scope is not None:
            if not isinstance(self.coverage_scope, dict):
                raise EvidenceContractError("coverage scope is a mapping")
            unknown = set(self.coverage_scope) - {
                "interval", "coordinate_space_id", "target_entity_id"}
            if unknown:
                raise EvidenceContractError(
                    f"coverage scope carries unknown keys {sorted(unknown)}")
            interval = self.coverage_scope.get("interval")
            if interval is not None and (
                    not isinstance(interval, dict)
                    or not isinstance(interval.get("start"), int)
                    or not isinstance(interval.get("end"), int)
                    or interval["end"] <= interval["start"]
                    or not interval.get("clock_id")):
                raise EvidenceContractError(
                    "coverage scope intervals name a clock and a non-empty tick range")


def new_measurement(asset_id: str, clock_id: str, start_tick: int,
                    end_tick: int, measurement_type: str, value: Any,
                    method: str, provenance_id: str,
                    sample_resolution: list[int] | None = None,
                    target_entity_id: str | None = None,
                    target_region: str | None = None,
                    coverage: CoverageKind = CoverageKind.COMPLETE,
                    coverage_samples: int = 1,
                    coverage_sample_rate_hz: float = 0.0) -> EvidenceRecord:
    """Build a measurement record; raises on any contract violation."""
    return EvidenceRecord(
        asset_id=asset_id, clock_id=clock_id,
        start_tick=start_tick, end_tick=end_tick,
        target_entity_id=target_entity_id, target_region=target_region,
        evidence_type=EvidenceType.MEASUREMENT,
        measurement_type=measurement_type, value=value,
        unit=registry_entry(measurement_type)["unit"], method=method,
        sample_resolution=sample_resolution, coverage=coverage,
        coverage_samples=coverage_samples,
        coverage_sample_rate_hz=coverage_sample_rate_hz,
        provenance_id=provenance_id)


def new_inference(asset_id: str, clock_id: str, start_tick: int,
                  end_tick: int, measurement_type: str, value: Any,
                  method: str, provenance_id: str, uncertainty: float,
                  sample_resolution: list[int] | None = None,
                  target_entity_id: str | None = None,
                  target_region: str | None = None,
                  coverage: CoverageKind = CoverageKind.SAMPLED,
                  coverage_samples: int = 1,
                  coverage_sample_rate_hz: float = 0.0) -> EvidenceRecord:
    """Build an inference record; uncertainty is required."""
    return EvidenceRecord(
        asset_id=asset_id, clock_id=clock_id,
        start_tick=start_tick, end_tick=end_tick,
        target_entity_id=target_entity_id, target_region=target_region,
        evidence_type=EvidenceType.INFERENCE,
        measurement_type=measurement_type, value=value,
        unit=registry_entry(measurement_type)["unit"], method=method,
        sample_resolution=sample_resolution, coverage=coverage,
        coverage_samples=coverage_samples,
        coverage_sample_rate_hz=coverage_sample_rate_hz,
        provenance_id=provenance_id, uncertainty=uncertainty)


def new_evaluation(asset_id: str, clock_id: str, start_tick: int,
                   end_tick: int, measurement_type: str, value: Any,
                   method: str, provenance_id: str, policy_id: str,
                   basis_measurement_ids: tuple[str, ...] | list[str],
                   target_entity_id: str | None = None,
                   target_region: str | None = None) -> EvidenceRecord:
    """Build an evaluation record; policy + cited basis required."""
    return EvidenceRecord(
        asset_id=asset_id, clock_id=clock_id,
        start_tick=start_tick, end_tick=end_tick,
        target_entity_id=target_entity_id, target_region=target_region,
        evidence_type=EvidenceType.EVALUATION,
        measurement_type=measurement_type, value=value,
        unit=registry_entry(measurement_type)["unit"], method=method,
        sample_resolution=None, coverage=CoverageKind.COMPLETE,
        coverage_samples=len(tuple(basis_measurement_ids)),
        coverage_sample_rate_hz=0.0, provenance_id=provenance_id,
        policy_id=policy_id,
        basis_measurement_ids=tuple(basis_measurement_ids))


# ── Temporal alignment (plan A3) ───────────────────────────────────
#
# Source ticks are authoritative. Everything here preserves them;
# nothing bins them. Per-second bins are derived read-only by
# derive_second_bins for the M9 access path, which this item does
# not alter.


def sec_to_ticks(start_s: float, end_s: float,
                 ticks_per_second: int = TICKS_PER_SECOND,
                 ) -> tuple[int, int]:
    """Float seconds to half-open integer ticks, subsecond preserved.

    Floor/ceil (never round): the tick range covers the measured
    span. Refuses non-finite bounds and non-positive spans - a
    zero-length measurement cannot be a half-open interval.
    """
    try:
        start_f, end_f = float(start_s), float(end_s)
    except (TypeError, ValueError):
        raise TemporalAlignmentError(
            f"unalignable bounds {start_s!r}..{end_s!r}") from None
    if not (math.isfinite(start_f) and math.isfinite(end_f)):
        raise TemporalAlignmentError("bounds must be finite")
    if ticks_per_second <= 0:
        raise TemporalAlignmentError("ticks_per_second is positive")
    start_tick = math.floor(start_f * ticks_per_second)
    end_tick = math.ceil(end_f * ticks_per_second)
    if end_tick <= start_tick:
        raise TemporalAlignmentError(
            f"non-positive span {start_s!r}..{end_s!r}")
    if start_tick < 0:
        raise TemporalAlignmentError("ticks are non-negative")
    return (start_tick, end_tick)


def ticks_to_seconds(start_tick: int, end_tick: int,
                     ticks_per_second: int = TICKS_PER_SECOND,
                     ) -> tuple[float, float]:
    """Integer ticks back to float seconds."""
    return (start_tick / ticks_per_second, end_tick / ticks_per_second)


@dataclass(frozen=True)
class RationalRate:
    """A rational frame rate: variable rates stay exact (plan A3)."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if self.denominator <= 0:
            raise TemporalAlignmentError(
                "rate denominator must be positive")

    def hz(self) -> float:
        return self.numerator / self.denominator


@dataclass(frozen=True)
class MulticamOffset:
    """A measured M6 offset with its sync uncertainty (plan E2 rule:
    uncertainty respected, never assumed aligned)."""

    offset_seconds: float
    uncertainty_seconds: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.offset_seconds):
            raise TemporalAlignmentError("offset must be finite")
        if not math.isfinite(self.uncertainty_seconds):
            raise TemporalAlignmentError("uncertainty must be finite")
        if self.uncertainty_seconds < 0:
            raise TemporalAlignmentError("uncertainty is non-negative")


def apply_offset(start_tick: int, end_tick: int, offset: MulticamOffset,
                 ticks_per_second: int = TICKS_PER_SECOND,
                 ) -> tuple[int, int, int]:
    """Shift ticks by a multicam offset; returns (start, end,
    uncertainty_ticks). The uncertainty travels with the shifted
    interval so a join can refuse to treat it as aligned."""
    shift = math.floor(offset.offset_seconds * ticks_per_second)
    moved = (start_tick + shift, end_tick + shift)
    if moved[1] <= moved[0]:
        raise TemporalAlignmentError("offset emptied the interval")
    if moved[0] < 0:
        raise TemporalAlignmentError("offset moved ticks before zero")
    uncertainty_ticks = math.ceil(
        offset.uncertainty_seconds * ticks_per_second)
    return (moved[0], moved[1], uncertainty_ticks)


def derive_second_bins(start_tick: int, end_tick: int,
                       ticks_per_second: int = TICKS_PER_SECOND,
                       ) -> list[tuple[int, int]]:
    """Per-second bins covering a tick range: the M9 access
    optimization, derived read-only. The ticks stay authoritative;
    binning never narrows them."""
    if end_tick <= start_tick:
        raise TemporalAlignmentError("bins need a non-empty range")
    first = start_tick // ticks_per_second
    last = (end_tick - 1) // ticks_per_second
    return [(s * ticks_per_second, (s + 1) * ticks_per_second)
            for s in range(first, last + 1)]


# ── Provenance ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProvenanceRecord:
    """Who produced a record, with what, from which bytes."""

    id: str
    producer: str
    producer_version: str | None
    source_digest: str
    method: str


def make_provenance_id(source_digest: str, lane: str) -> str:
    return f"prov-{source_digest}-{lane}"


def make_provenance(source_digest: str, lane: str, producer: str,
                    method: str,
                    producer_version: str | None = None) -> ProvenanceRecord:
    return ProvenanceRecord(
        id=make_provenance_id(source_digest, lane), producer=producer,
        producer_version=producer_version, source_digest=source_digest,
        method=method)


# ── Migration: current M0-M10 records into the contract ────────────
#
# Backward-compatible readers keep working: to_legacy_observation
# emits the Observation shape the Project Model already stores
# (modality + payload + confidence + provenance_id), with the
# contract carried additively inside payload["evidence_contract"].
# Nothing already stored changes meaning.

MODALITY_TO_MEASUREMENT: dict[str, str] = {
    "speech_word": "speech.word",
    "speech": "speech.utterance",
    "face": "person.face_box",
    "voice_turn": "person.voice_turn",
}


def audit_legacy_observation(modality: str, payload: dict[str, Any]) -> str | None:
    """Audit one legacy observation against the contract.

    Returns the measurement type a mapped modality carries, or None
    for modalities this contract does not (yet) cover. Raises
    EvidenceTypeError when a mapped modality's payload files an
    instrument value where the registry demands an evaluation kind -
    which today never happens, because every mapped modality is a
    measurement. The audit is additive: unmapped modalities pass
    through untouched.
    """
    measurement_type = MODALITY_TO_MEASUREMENT.get(modality)
    if measurement_type is None:
        return None
    entry = registry_entry(measurement_type)
    if entry["kind"] is not EvidenceType.MEASUREMENT:
        raise EvidenceTypeError(
            f"modality {modality!r} files a raw value as "
            f"{entry['kind'].value}")
    if not isinstance(payload, dict):
        raise EvidenceContractError(
            f"modality {modality!r} carries no payload dict")
    return measurement_type


def migrate_m1_word(word: dict[str, Any], asset_id: str, clock_id: str,
                    provenance_id: str, method: str,
                    utterance_index: int = 0,
                    ticks_per_second: int = TICKS_PER_SECOND,
                    ) -> EvidenceRecord:
    """One real M1 word (``transcript.words.json`` shape) into the
    contract. The provenance id travels intact: the record cites the
    lane run that heard the word, never a new one. ``method`` is the
    lane doc's own instrument stamp, passed verbatim - M1 stamps an
    instrument dict (arm/aligner/transcriber), not a single string,
    so the caller composes it and the contract refuses an empty one
    rather than coining a canonical name."""
    if not isinstance(word, dict):
        raise EvidenceContractError("M1 word must be a dict")
    if not method:
        raise EvidenceContractError("M1 migration names no method")
    start_tick, end_tick = sec_to_ticks(
        word.get("start"), word.get("end"), ticks_per_second)
    text = word.get("text", word.get("word"))
    if not isinstance(text, str) or not text:
        raise EvidenceContractError("M1 word carries no text")
    record = new_measurement(
        asset_id=asset_id, clock_id=clock_id,
        start_tick=start_tick, end_tick=end_tick,
        measurement_type="speech.word",
        value={"text": text, "utterance_index": utterance_index},
        method=method, provenance_id=provenance_id,
        coverage=CoverageKind.COMPLETE, coverage_samples=1,
        coverage_sample_rate_hz=0.0)
    confidence = word.get("confidence")
    if confidence is not None:
        try:
            number = float(confidence)
        except (TypeError, ValueError):
            number = None
        if number is not None and not 0.0 <= number <= 1.0:
            raise EvidenceContractError(
                "M1 confidence outside 0..1 is not evidence")
    return record


def migrate_m3_face_span(span: dict[str, Any], track_id: str,
                         asset_id: str, clock_id: str,
                         provenance_id: str, method: str,
                         sample_resolution: list[int],
                         ticks_per_second: int = TICKS_PER_SECOND,
                         ) -> EvidenceRecord:
    """One real M3/M3b face span (``identity.json`` faces[].spans[]
    shape) into the contract. Boxes stay image-normalised, top-left
    origin. ``method`` and ``sample_resolution`` are the measuring
    lane's own stamp and pixels, passed verbatim - the contract
    refuses an absent one rather than guessing them."""
    if not isinstance(span, dict):
        raise EvidenceContractError("M3 face span must be a dict")
    if not method:
        raise EvidenceContractError("M3 migration names no method")
    start_tick, end_tick = sec_to_ticks(
        span.get("start"), span.get("end"), ticks_per_second)
    box = span.get("box")
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        raise EvidenceContractError("M3 face span carries no box")
    if not track_id:
        raise EvidenceContractError("M3 face span names no track")
    return new_measurement(
        asset_id=asset_id, clock_id=clock_id,
        start_tick=start_tick, end_tick=end_tick,
        measurement_type="person.face_box",
        value={"face_track": track_id, "box": list(box)},
        method=method, provenance_id=provenance_id,
        sample_resolution=list(sample_resolution),
        target_entity_id=None, target_region="face",
        coverage=CoverageKind.SAMPLED, coverage_samples=1,
        coverage_sample_rate_hz=0.0)


def to_legacy_observation(record: EvidenceRecord, obs_id: str,
                          modality: str, confidence: float | None = None,
                          ) -> dict[str, Any]:
    """A contract record in the Observation shape existing readers
    already parse. Legacy keys stay top-level in the payload; the
    contract rides additively under ``evidence_contract``."""
    payload: dict[str, Any] = {}
    if isinstance(record.value, dict):
        payload.update(record.value)
    else:
        payload["value"] = record.value
    payload["evidence_contract"] = to_rust_evidence(record)
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise EvidenceContractError("confidence must be 0..1")
    return {
        "id": obs_id,
        "asset_id": record.asset_id,
        "interval": {"clock_id": record.clock_id,
                     "start": record.start_tick, "end": record.end_tick},
        "modality": modality,
        "payload": payload,
        "confidence": confidence,
        "provenance_id": record.provenance_id,
    }


def to_rust_evidence(record: EvidenceRecord) -> dict[str, Any]:
    """The ``EvidenceContract`` shape ``native/ren-protocol`` stores
    on its ``Observation``. Key names match the Rust struct's serde
    names exactly; a test pins the set."""
    result = {
        "evidence_type": record.evidence_type.value,
        "measurement_type": record.measurement_type,
        "value": record.value,
        "unit": record.unit,
        "method": record.method,
        "sample_resolution": record.sample_resolution,
        "coverage": record.coverage.value,
        "coverage_samples": record.coverage_samples,
        "coverage_sample_rate_hz": record.coverage_sample_rate_hz,
        "target_entity_id": record.target_entity_id,
        "target_region": record.target_region,
        "uncertainty": record.uncertainty,
        "policy_id": record.policy_id,
        "basis_measurement_ids": list(record.basis_measurement_ids),
        "contract_version": record.contract_version,
        "certainty": record.certainty.value if record.certainty is not None else None,
        "processing_status": record.processing_status.value if record.processing_status is not None else None,
        "source_reference": record.source_reference,
        "spatial_frame_id": record.spatial_frame_id,
        "coordinate_space_id": record.coordinate_space_id,
        "references": [dict(reference) for reference in record.references],
        "coverage_scope": dict(record.coverage_scope) if record.coverage_scope is not None else None,
        "coverage_level": record.coverage_level,
        "producer_version": record.producer_version,
        "freshness": record.freshness,
    }
    return result


#: The exact key set ``to_rust_evidence`` emits, pinned against the
#: Rust ``EvidenceContract`` struct's serde fields.
RUST_EVIDENCE_KEYS: frozenset[str] = frozenset({
    "evidence_type", "measurement_type", "value", "unit", "method",
    "sample_resolution", "coverage", "coverage_samples",
    "coverage_sample_rate_hz", "target_entity_id", "target_region",
    "uncertainty", "policy_id", "basis_measurement_ids",
    "contract_version",
    "certainty", "processing_status", "source_reference",
    "spatial_frame_id", "coordinate_space_id", "references",
    "coverage_scope", "coverage_level", "producer_version", "freshness",
})


#: Why ``unavailable`` and measured-absent differ, in one place for
#: prompts and reports to quote.
COVERAGE_NOTE = (
    "unavailable means unmeasured: the lane never looked. A complete "
    "record with zero samples means measured and absent. The scheduler "
    "must never read sparse sampling as evidence that nothing happened.")

__all__ = [
    "ANALYSIS_LEVELS",
    "COVERAGE_FRESHNESS",
    "COVERAGE_NOTE",
    "EVIDENCE_CONTRACT_VERSION",
    "EVIDENCE_REFERENCE_KINDS",
    "KNOWN_METHODS",
    "MODALITY_TO_MEASUREMENT",
    "RUST_EVIDENCE_KEYS",
    "TICKS_PER_SECOND",
    "CertaintyState",
    "CoverageKind",
    "EvidenceContractError",
    "EvidenceRecord",
    "EvidenceType",
    "EvidenceTypeError",
    "MulticamOffset",
    "ProcessingStatus",
    "ProvenanceRecord",
    "RationalRate",
    "TemporalAlignmentError",
    "UnitMismatchError",
    "UnknownMeasurementTypeError",
    "apply_offset",
    "audit_legacy_observation",
    "derive_second_bins",
    "make_provenance",
    "make_provenance_id",
    "method_family",
    "migrate_m1_word",
    "migrate_m3_face_span",
    "new_evaluation",
    "new_inference",
    "new_measurement",
    "registry_entry",
    "same_method",
    "sec_to_ticks",
    "ticks_to_seconds",
    "to_legacy_observation",
    "to_rust_evidence",
]
