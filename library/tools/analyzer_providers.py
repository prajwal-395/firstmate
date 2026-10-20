"""Provider selection and normalization for FI-03 evidence producers.

Provider selection and evidence execution planning share one boundary.
Producers provide measured records; requirements and budgets come from the
task. Selection fails closed when quality, cost or availability measurements
are missing. Transcript execution remains on its existing route.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from library.tools import evidence_contract as evidence


class ProviderSelectionError(ValueError):
    """No registered provider satisfies the requested evidence."""


@dataclass(frozen=True)
class ProviderCost:
    latency_ms: float | None
    memory_mb: float | None
    currency: float | None = None


@dataclass(frozen=True)
class QualityRecord:
    """Measured quality for a named benchmark and provider version."""

    benchmark_id: str
    accuracy: float
    stability: float
    measured_runs: int

    def __post_init__(self) -> None:
        if not self.benchmark_id or self.measured_runs < 1:
            raise ValueError("quality needs a benchmark and measured runs")
        if not 0 <= self.accuracy <= 1 or not 0 <= self.stability <= 1:
            raise ValueError("accuracy and stability must be in 0..1")


@dataclass(frozen=True)
class EvidenceRequirement:
    capability: str
    measurement_type: str
    minimum_accuracy: float
    minimum_stability: float
    maximum_latency_ms: float
    maximum_memory_mb: float
    benchmark_id: str


@dataclass(frozen=True)
class Provider:
    capability: str
    provider_id: str
    version: str
    availability_probe: Callable[[], tuple[bool, str]]
    declared_cost: ProviderCost
    quality: tuple[QualityRecord, ...]
    run: Callable[..., Any]


@dataclass(frozen=True)
class Selection:
    provider: Provider
    reason: str


@dataclass(frozen=True)
class ProviderResult:
    selection: Selection
    records: tuple[evidence.EvidenceRecord, ...]
    provenance: evidence.ProvenanceRecord


class PromotionTrigger(StrEnum):
    """Reasons an analyzer may spend for a higher fidelity tier."""

    LOW_CONFIDENCE = "low_confidence"
    EDIT_CRITICAL = "edit_critical_request"
    NAMED_OBJECT = "named_object_or_query_need"
    AMBIGUOUS = "ambiguous_result"


@dataclass(frozen=True)
class AnalyzerTier:
    """One ordered tier with measured quality and resource costs."""

    tier_id: str
    provider_id: str
    fidelity_metric: str
    fidelity: float
    time_seconds: float | None
    peak_memory_mb: float | None
    promotes_on: frozenset[PromotionTrigger] = frozenset()

    def __post_init__(self) -> None:
        if not self.tier_id or not self.provider_id or not self.fidelity_metric:
            raise ValueError("analyzer tier needs ids and a fidelity metric")
        if not 0 <= self.fidelity <= 1:
            raise ValueError("tier fidelity must be in 0..1")
        if ((self.time_seconds is not None and self.time_seconds < 0)
                or (self.peak_memory_mb is not None and self.peak_memory_mb < 0)):
            raise ValueError("tier time and memory must be non-negative")


@dataclass(frozen=True)
class CapabilityTiers:
    """Measured, ordered tiers for one capability."""

    capability: str
    tiers: tuple[AnalyzerTier, ...]

    def __post_init__(self) -> None:
        if not self.capability or not self.tiers:
            raise ValueError("tier plan needs a capability and at least one tier")
        if len({tier.tier_id for tier in self.tiers}) != len(self.tiers):
            raise ValueError("tier ids must be unique within a capability")


@dataclass(frozen=True)
class ExecutionPlan:
    """A measured combination of tiers that can meet a task quality bar."""

    plan_id: str
    capability: str
    tier_ids: tuple[str, ...]
    fidelity_metric: str
    fidelity: float
    time_seconds: float
    peak_memory_mb: float
    candidate_retention: float | None = None
    coverage: float | None = None
    false_negative_rate: float | None = None
    inputs: tuple[str, ...] = ()
    output_evidence_types: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all((self.plan_id, self.capability, self.tier_ids,
                    self.fidelity_metric)):
            raise ValueError("execution plan needs an id, capability, tiers and metric")
        if not 0 <= self.fidelity <= 1:
            raise ValueError("plan fidelity must be in 0..1")
        if self.time_seconds < 0 or self.peak_memory_mb < 0:
            raise ValueError("plan time and memory must be non-negative")
        for label, value in (("candidate_retention", self.candidate_retention),
                             ("coverage", self.coverage),
                             ("false_negative_rate", self.false_negative_rate)):
            if value is not None and not 0 <= value <= 1:
                raise ValueError(f"{label} must be in 0..1")


@dataclass(frozen=True)
class CapabilityPlans:
    """Candidate measured plans for a capability, with deterministic choice."""

    capability: str
    plans: tuple[ExecutionPlan, ...]

    def __post_init__(self) -> None:
        if not self.capability or not self.plans:
            raise ValueError("capability plans need a capability and plans")
        if any(plan.capability != self.capability for plan in self.plans):
            raise ValueError("all execution plans must match their capability")
        if len({plan.plan_id for plan in self.plans}) != len(self.plans):
            raise ValueError("execution plan ids must be unique")


def select_execution_plan(
    plans: CapabilityPlans,
    *,
    fidelity_metric: str,
    minimum_fidelity: float,
    required_tier_ids: frozenset[str] = frozenset(),
    required_output_evidence_types: frozenset[str] = frozenset(),
) -> ExecutionPlan:
    """Choose the cheapest measured plan meeting a task-declared quality bar."""
    if not 0 <= minimum_fidelity <= 1:
        raise ValueError("minimum_fidelity must be in 0..1")
    qualified = [
        plan for plan in plans.plans
        if plan.fidelity_metric == fidelity_metric
        and plan.fidelity >= minimum_fidelity
        and required_tier_ids.issubset(plan.tier_ids)
        and required_output_evidence_types.issubset(
            plan.output_evidence_types)
    ]
    if not qualified:
        raise ProviderSelectionError(
            f"no measured {plans.capability} plan meets {fidelity_metric} "
            f">= {minimum_fidelity:g}")
    return min(qualified, key=lambda plan: (
        plan.time_seconds, plan.peak_memory_mb, plan.plan_id))


@dataclass(frozen=True)
class TierRecord:
    """A result with the tier that produced it and its promotion reason."""

    tier_id: str
    provider_id: str
    promoted_because: str | None
    value: Any


def run_ordered_tiers(
    plan: CapabilityTiers,
    run: Callable[[AnalyzerTier], Any],
    *,
    triggers: frozenset[PromotionTrigger] = frozenset(),
) -> tuple[TierRecord, ...]:
    """Always run the cheapest declared tier first, then promote on named triggers."""
    rows: list[TierRecord] = []
    promotion_reason: str | None = None
    for index, tier in enumerate(plan.tiers):
        if index and (promotion_reason is None or
                      not (plan.tiers[index - 1].promotes_on & triggers)):
            break
        rows.append(TierRecord(tier.tier_id, tier.provider_id,
                               promotion_reason, run(tier)))
        if index + 1 < len(plan.tiers):
            matched = plan.tiers[index].promotes_on & triggers
            if not matched:
                break
            promotion_reason = ",".join(sorted(trigger.value for trigger in matched))
    return tuple(rows)


class ProviderRegistry:
    def __init__(self, providers: tuple[Provider, ...] = ()) -> None:
        self._providers: dict[tuple[str, str], Provider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: Provider) -> None:
        key = (provider.capability, provider.provider_id)
        if not all(key) or not provider.version:
            raise ValueError("provider needs capability, id and version")
        if key in self._providers:
            raise ValueError(f"duplicate provider {key!r}")
        self._providers[key] = provider

    def providers(self, capability: str) -> tuple[Provider, ...]:
        return tuple(p for p in self._providers.values()
                     if p.capability == capability)

    def select(self, requirement: EvidenceRequirement) -> Selection:
        evidence.registry_entry(requirement.measurement_type)
        rejected: list[str] = []
        qualified: list[tuple[tuple[float, float, str], Provider, str]] = []
        for provider in self.providers(requirement.capability):
            ok, detail = provider.availability_probe()
            if not ok:
                rejected.append(f"{provider.provider_id}: unavailable ({detail})")
                continue
            cost = provider.declared_cost
            if (cost.latency_ms is None or cost.memory_mb is None
                    or cost.latency_ms > requirement.maximum_latency_ms
                    or cost.memory_mb > requirement.maximum_memory_mb):
                rejected.append(f"{provider.provider_id}: cost is missing or exceeds limits")
                continue
            quality = next((q for q in provider.quality
                            if q.benchmark_id == requirement.benchmark_id), None)
            if quality is None:
                rejected.append(f"{provider.provider_id}: no measured quality for {requirement.benchmark_id}")
                continue
            if (quality.accuracy < requirement.minimum_accuracy
                    or quality.stability < requirement.minimum_stability):
                rejected.append(f"{provider.provider_id}: measured quality is below requirement")
                continue
            reason = (f"qualified on {quality.benchmark_id}: accuracy="
                      f"{quality.accuracy:g}, stability={quality.stability:g}, "
                      f"latency={cost.latency_ms:g}ms, memory={cost.memory_mb:g}MB; "
                      "lowest declared latency among qualified providers")
            qualified.append(((cost.latency_ms, cost.memory_mb,
                               provider.provider_id), provider, reason))
        if not qualified:
            why = "; ".join(rejected) if rejected else "no providers registered"
            raise ProviderSelectionError(
                f"no provider satisfies {requirement.capability}/"
                f"{requirement.measurement_type}: {why}")
        _, chosen, reason = min(qualified, key=lambda row: row[0])
        return Selection(chosen, reason)


def transcription_registry(
    *, voz_cost: ProviderCost, resolve_cost: ProviderCost,
    voz_quality: tuple[QualityRecord, ...],
    resolve_quality: tuple[QualityRecord, ...],
    voz_probe: Callable[[], tuple[bool, str]],
    resolve_probe: Callable[[], tuple[bool, str]],
) -> ProviderRegistry:
    """Register the two implemented transcription adapters.

    Costs and quality are deliberately required inputs: this repository
    has no comparable, checked-in benchmark for the two producers yet.
    Availability probes are supplied by the runtime boundary so fixtures
    never connect to Resolve.
    """
    return ProviderRegistry((
        Provider("transcription", "ren-voz", "adapter/1", voz_probe,
                 voz_cost, voz_quality, voz_transcribe),
        Provider("transcription", "resolve-get-transcription", "adapter/1",
                 resolve_probe, resolve_cost, resolve_quality,
                 resolve_transcribe),
    ))


def normalize_transcription(
    native: Mapping[str, Any], *, asset_id: str, clock_id: str,
    source_digest: str, provider_id: str, provider_version: str,
    start_tick: int = 0,
) -> tuple[evidence.EvidenceRecord, ...]:
    """Normalize Voz/Resolve transcript shapes to FI-03 M1 word records."""
    if not all((asset_id, clock_id, source_digest, provider_id, provider_version)):
        raise ValueError("transcript evidence requires asset, clock, source and provider ids")
    source = native.get("segments")
    if not isinstance(source, list):
        raise TypeError("transcript provider response requires a segments list")
    words: list[tuple[str, float, float, str | None]] = []
    for segment in source:
        if not isinstance(segment, Mapping):
            raise TypeError("transcript segment must be a mapping")
        segment_words = segment.get("words")
        if segment_words is None:
            # Voz's HeardSpeech words and Resolve word entries are both
            # accepted; a segment without word timings cannot claim M1.
            segment_words = [segment]
        if not isinstance(segment_words, list):
            raise TypeError("transcript words must be a list")
        for word in segment_words:
            if not isinstance(word, Mapping):
                raise TypeError("transcript word must be a mapping")
            text = str(word.get("word", word.get("text", ""))).strip()
            if not text:
                continue
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("transcript word needs numeric start and end") from exc
            if start < 0 or end <= start:
                raise ValueError("transcript word interval must be positive")
            words.append((text, start, end,
                          str(segment["speaker"]) if segment.get("speaker") is not None else None))
    records: list[evidence.EvidenceRecord] = []
    provenance = evidence.ProvenanceRecord(
        id=f"prov-{source_digest}-transcription-{provider_id}@{provider_version}",
        producer=provider_id, producer_version=provider_version,
        source_digest=source_digest,
        method=f"analyzer-provider/{provider_id}")
    provenance_id = provenance.id
    for index, (word, start, end, speaker) in enumerate(words):
        begin = start_tick + evidence.point_to_tick(start, evidence.TICKS_PER_SECOND)
        finish = start_tick + evidence.point_to_tick(end, evidence.TICKS_PER_SECOND)
        if finish <= begin:
            raise ValueError("transcript word collapses at FI-03 tick resolution")
        records.append(evidence.EvidenceRecord(
            asset_id=asset_id, clock_id=clock_id, start_tick=begin,
            end_tick=finish, target_entity_id=speaker, target_region=None,
            evidence_type=evidence.EvidenceType.MEASUREMENT,
            measurement_type="speech.word", value={"word": word,
                "index": index, "speaker": speaker}, unit="text",
            method=f"analyzer-provider/{provider_id}", sample_resolution=None,
            coverage=evidence.CoverageKind.COMPLETE, coverage_samples=1,
            coverage_sample_rate_hz=0.0, provenance_id=provenance_id,
            producer_version=provider_version,
            source_reference=source_digest,
        ))
    return tuple(records)


def transcribe_with_provider(
    registry: ProviderRegistry, requirement: EvidenceRequirement, *,
    asset_id: str, clock_id: str, source_digest: str, **provider_args: Any,
) -> ProviderResult:
    """Select, run and normalize one provider; retain the selection receipt."""
    if requirement.capability != "transcription":
        raise ProviderSelectionError("this adapter only executes transcription")
    selection = registry.select(requirement)
    raw = selection.provider.run(**provider_args)
    records = normalize_transcription(
        raw, asset_id=asset_id, clock_id=clock_id,
        source_digest=source_digest,
        provider_id=selection.provider.provider_id,
        provider_version=selection.provider.version,
    )
    provenance = evidence.ProvenanceRecord(
        id=records[0].provenance_id if records else
        f"prov-{source_digest}-transcription-"
        f"{selection.provider.provider_id}@{selection.provider.version}",
        producer=selection.provider.provider_id,
        producer_version=selection.provider.version,
        source_digest=source_digest,
        method=f"analyzer-provider/{selection.provider.provider_id}")
    return ProviderResult(selection, records, provenance)


def voz_transcribe(audio_path: str) -> Any:
    """Invoke Ren's Voz transcription path (dependency loaded on demand)."""
    from library.tools import heard_speech
    spoken = heard_speech.transcribe(audio_path)
    return {"segments": [{"text": word.word, "start": word.start,
                           "end": word.end} for word in spoken.words],
            "language": "", "provider_metadata": spoken.engine}


def resolve_transcribe(clip: Any) -> Mapping[str, Any]:
    """Read a Resolve clip through the established GetTranscription path."""
    from library.tools.resolve_axi import _transcription_words
    language, segments = _transcription_words(clip)
    return {"segments": segments, "language": language}



# Capability graph and deterministic execution planning (FI-24).

class ExecutionProfile(StrEnum):
    FAST_DISCOVERY = "fast_discovery"
    EDIT_PREPARATION = "edit_preparation"
    PRECISION_EXECUTION = "precision_execution"


@dataclass(frozen=True)
class MeasuredQuality:
    benchmark_id: str
    metric: str
    value: float
    measured_runs: int

    def __post_init__(self) -> None:
        if not self.benchmark_id or not self.metric or self.measured_runs < 1:
            raise ValueError("quality records require benchmark, metric and runs")
        if not 0 <= self.value <= 1:
            raise ValueError("quality value must be in 0..1")


@dataclass(frozen=True)
class MeasuredCost:
    benchmark_id: str
    scope_id: str
    seconds: float
    peak_memory_mb: float
    invocation_cost: float = 0.0

    def __post_init__(self) -> None:
        values = (self.seconds, self.peak_memory_mb, self.invocation_cost)
        if (not self.benchmark_id or not self.scope_id
                or not all(math.isfinite(value) and value >= 0 for value in values)):
            raise ValueError("cost records must be named and non-negative")


@dataclass(frozen=True)
class CapabilityNode:
    capability_id: str
    input_evidence: frozenset[str]
    output_evidence: frozenset[str]
    tasks: frozenset[str]
    dependencies: tuple[str, ...] = ()
    quality: tuple[MeasuredQuality, ...] = ()
    costs: tuple[MeasuredCost, ...] = ()
    producer_id: str = ""
    invoke: Callable[[ProducerContext], Mapping[str, Any]] | None = None


@dataclass(frozen=True)
class EvidenceNeed:
    evidence_type: str
    quality_metric: str
    minimum_quality: float

    def __post_init__(self) -> None:
        if (not self.evidence_type or not self.quality_metric
                or not math.isfinite(self.minimum_quality)
                or not 0 <= self.minimum_quality <= 1):
            raise ValueError("evidence needs require a quality bar in 0..1")


@dataclass(frozen=True)
class EvidenceAvailable:
    evidence_type: str
    quality_metric: str
    quality: float
    source: str
    project_id: str

    def __post_init__(self) -> None:
        if (not self.evidence_type or not self.quality_metric or not self.source
                or not self.project_id or not math.isfinite(self.quality)
                or not 0 <= self.quality <= 1):
            raise ValueError("available evidence needs a project, source and quality in 0..1")


@dataclass(frozen=True)
class TaskRequest:
    task: str
    needs: tuple[EvidenceNeed, ...]
    benchmark_id: str
    budget_seconds: float
    profile: ExecutionProfile = ExecutionProfile.EDIT_PREPARATION
    project_id: str = ""
    authorise_cross_project: bool = False
    input_evidence: frozenset[str] = frozenset()
    interval: tuple[float, float] | None = None
    cost_scope_id: str = ""

    def __post_init__(self) -> None:
        if (not self.task or not self.benchmark_id
                or not math.isfinite(self.budget_seconds)
                or self.budget_seconds < 0):
            raise ValueError("task request needs a task, benchmark and finite budget")
        if self.interval is not None:
            start, end = self.interval
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise ValueError("task interval must be finite, non-empty and non-negative")


@dataclass(frozen=True)
class PlannedProducer:
    capability_id: str
    producer_id: str
    outputs: tuple[str, ...]
    cost_seconds: float
    peak_memory_mb: float
    quality: tuple[MeasuredQuality, ...]
    dependencies: tuple[str, ...]
    interval: tuple[float, float] | None = None


@dataclass(frozen=True)
class EvidenceExecutionPlan:
    status: str
    missing_evidence: tuple[str, ...]
    producers: tuple[PlannedProducer, ...]
    reused_cache: tuple[EvidenceAvailable, ...]
    predicted_cost_seconds: float
    escalation_conditions: tuple[str, ...]
    stopping_conditions: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True)
class ProducerContext:
    """Project-scoped inputs passed to a selected producer."""

    project: Any
    source_file: str
    source_digest: str
    duration_seconds: float
    query: str
    memory_root: Any = None
    interval: tuple[float, float] | None = None
    project_model: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ExecutedProducer:
    capability_id: str
    status: str
    elapsed_seconds: float
    project_model_revision: int | None
    output: Mapping[str, Any]


class CapabilityGraph:
    def __init__(self, nodes: Iterable[CapabilityNode] = ()) -> None:
        self.nodes: dict[str, CapabilityNode] = {}
        for node in nodes:
            self.register(node)

    def register(self, node: CapabilityNode) -> None:
        if not node.capability_id or node.capability_id in self.nodes:
            raise ValueError(f"duplicate or empty capability id: {node.capability_id!r}")
        self.nodes[node.capability_id] = node


def nodes_from_measured_plans(*, capability_id: str, benchmark_id: str,
                             cost_scope_id: str,
                             tasks: frozenset[str], input_evidence: frozenset[str],
                             measured_runs: int, plans,
                             invokers: Mapping[str, Callable[[ProducerContext], Mapping[str, Any]]] | None = None,
                             ) -> tuple[CapabilityNode, ...]:
    """Adapt analyzer-provider plan records without manufacturing measurements."""
    if not benchmark_id or not cost_scope_id or measured_runs < 1:
        raise ValueError("measured plans need benchmark, cost scope and run count")
    return tuple(
        CapabilityNode(
            capability_id=f"{capability_id}:{plan.plan_id}",
            input_evidence=frozenset(plan.inputs) | input_evidence,
            output_evidence=frozenset(plan.output_evidence_types),
            tasks=tasks,
            quality=(MeasuredQuality(benchmark_id, plan.fidelity_metric,
                                     plan.fidelity, measured_runs),),
            # These adapter records normalize elapsed time per measured
            # frame. Price the recorded batch scope, not one frame.
            costs=(MeasuredCost(benchmark_id, cost_scope_id,
                                plan.time_seconds * measured_runs,
                                plan.peak_memory_mb),),
            producer_id=", ".join(plan.dependencies),
            invoke=(invokers or {}).get(plan.plan_id),
        ) for plan in plans.plans
    )


def targeted_producer_catalog(*, invokers: Mapping[str, Callable[[ProducerContext], Mapping[str, Any]]] | None = None,
                              measurements: Mapping[str, tuple[MeasuredQuality, ...]] | None = None,
                              costs: Mapping[str, tuple[MeasuredCost, ...]] | None = None,
                              ) -> tuple[CapabilityNode, ...]:
    """Describe existing interval/clip producers; unmeasured nodes stay ineligible.

    Costs and quality are intentionally empty until an evaluation records them.
    The catalog makes available acquisition surfaces visible without guessing.
    """
    invokers, measurements, costs = invokers or {}, measurements or {}, costs or {}
    specs = (
        CapabilityNode("m4_scene_window", frozenset({"video_interval"}),
                       frozenset({"scene.observation"}), frozenset({"scene_query"}),
                       producer_id="project_model_acquisition.query_project"),
        CapabilityNode("object_detection", frozenset({"frame", "object_query"}),
                       frozenset({"object.detection"}), frozenset({"named_object"}),
                       producer_id="open_vocabulary_detect.acquire_query_detections"),
        CapabilityNode("ocr_selected_frames", frozenset({"frame_interval"}),
                       frozenset({"text.track"}), frozenset({"text_query"}),
                       producer_id="step_1_07_ocr_extraction"),
        CapabilityNode("region_analysis", frozenset({"clip_interval", "subject_query"}),
                       frozenset({"person.region"}), frozenset({"region_query"}),
                       producer_id="person_regions.build_source_regions"),
        CapabilityNode("mask_analysis", frozenset({"clip_interval", "object.detection"}),
                       frozenset({"object.mask"}), frozenset({"mask_query"}),
                       producer_id="step_1_06_object_segmentation"),
        CapabilityNode("speech_span_refinement", frozenset({"audio_span"}),
                       frozenset({"speech.alignment"}), frozenset({"speaker_query"}),
                       producer_id="hybrid_transcription.span_refinement"),
    )
    return tuple(CapabilityNode(
        node.capability_id, node.input_evidence, node.output_evidence, node.tasks,
        dependencies=node.dependencies,
        quality=measurements.get(node.capability_id, ()),
        costs=costs.get(node.capability_id, ()),
        producer_id=node.producer_id,
        invoke=invokers.get(node.capability_id),
    ) for node in specs)


def default_capability_graph(*, producer_invokers: Mapping[str, Callable[[ProducerContext], Mapping[str, Any]]] | None = None) -> CapabilityGraph:
    """Build the current graph from measured analyzer plans and producer catalog."""
    from library.tools.analysis.open_vocabulary_detect import OBJECT_DETECTION_PLANS

    producer_invokers = {"object_detection": _detect_object_interval,
                         **(producer_invokers or {})}
    # Detector plan ids map to their tiered invocation, while the other
    # targeted adapters remain present and fail closed without measurements.
    detector_invokers = {plan.plan_id: producer_invokers.get("object_detection")
                         for plan in OBJECT_DETECTION_PLANS.plans}
    measured_object_nodes = nodes_from_measured_plans(
        capability_id="object_detection",
        benchmark_id="object-detector-tiering-2026-10-14",
        cost_scope_id="object-detector-tiering-2026-10-14:40-frames-148-boxes",
        measured_runs=40,
        tasks=frozenset({"named_object"}),
        input_evidence=frozenset({"frame", "object_query"}),
        plans=OBJECT_DETECTION_PLANS,
        invokers=detector_invokers)
    return CapabilityGraph((*targeted_producer_catalog(invokers=producer_invokers),
                            *measured_object_nodes))


def execute_project_model_plan(graph: CapabilityGraph,
                               plan: EvidenceExecutionPlan,
                               context: ProducerContext,
                               *, author: str = "ren",
                               reason: str = "planned interval evidence acquisition"
                               ) -> dict[str, Any]:
    """Execute a ready plan and merge every producer delta incrementally.

    All selected invokers are checked before the first call. Each returned
    delta has observations/provenance/events in the canonical Project Model
    shapes and is merged against the current store revision.
    """
    if plan.status != "ready":
        return {"status": plan.status, "steps": [],
                "predicted_cost_seconds": plan.predicted_cost_seconds,
                "reason": plan.reason}
    if not plan.producers:
        return {"status": "cached", "steps": [],
                "predicted_cost_seconds": 0.0, "actual_cost_seconds": 0.0}
    nodes = []
    for item in plan.producers:
        node = graph.nodes[item.capability_id]
        if node.invoke is None:
            return {"status": "abstain", "steps": [],
                    "reason": f"producer {item.capability_id} has no registered executor"}
        nodes.append((item, node))
    import time
    from dataclasses import replace

    from library.tools import project_model_store

    spent = 0.0
    steps = []
    for item, node in nodes:
        revision, current_model = project_model_store.load_project_model(context.project)
        started = time.perf_counter()
        delta = dict(node.invoke(replace(context, project_model=current_model)))
        elapsed = time.perf_counter() - started
        merged = project_model_store.merge_evidence_records(
            context.project,
            observations=delta.get("observations", []),
            events_to_merge=delta.get("events", []),
            provenance=delta.get("provenance", []),
            expected_revision=revision, author=author, reason=reason)
        spent += elapsed
        steps.append({"capability_id": item.capability_id,
                      "producer_id": item.producer_id,
                      "status": "merged", "elapsed_seconds": round(elapsed, 6),
                      "project_model_revision": merged,
                      "observations": len(delta.get("observations", [])),
                      "answer": delta.get("answer")})
    return {"status": "executed", "steps": steps,
            "predicted_cost_seconds": plan.predicted_cost_seconds,
            "actual_cost_seconds": round(spent, 6)}


def _detect_object_interval(context: ProducerContext) -> Mapping[str, Any]:
    """Run the measured OWLv2 tier over exactly the nominated M2 frame window."""
    import hashlib
    from pathlib import Path

    from library.tools import evidence_contract, source_memory
    from library.tools.analysis.open_vocabulary_detect import detect_image_tiered

    if not context.query.strip() or context.interval is None:
        raise ValueError("named-object acquisition requires a phrase and interval")
    m2 = source_memory.read_m2(context.source_digest, context.memory_root)
    if m2 is None or not source_memory.is_fresh(m2, context.source_file):
        raise ValueError("named-object acquisition requires fresh M2 frames")
    start, end = context.interval
    frame_rows = [row for row in m2["frames"]
                  if start <= float(row["t"]) < end]
    # The only measured cost record is the 40-frame tiering benchmark. Refuse
    # extrapolation to a differently sized frame batch instead of scaling it.
    if len(frame_rows) != 40:
        raise ValueError("measured object route requires exactly 40 nominated M2 frames")
    asset = next((row for row in context.project_model["assets"]
                  if row["content_digest"] == context.source_digest), None)
    if asset is None:
        raise ValueError("Project Model has no asset for the requested source")
    clock_id = next(row["id"] for row in context.project_model["clocks"]
                    if row["domain"].get("asset_id") == asset["id"])
    root = Path(context.memory_root).expanduser() if context.memory_root else source_memory.memory_root()
    frames_by_time = sorted(frame_rows, key=lambda row: row["t"])
    from PIL import Image
    first_frame_path = root / context.source_digest / frames_by_time[0]["file"]
    with Image.open(first_frame_path) as image:
        width, height = image.size
    period = (float(frames_by_time[1]["t"]) - float(frames_by_time[0]["t"])) if len(frames_by_time) > 1 else 0.5
    records = []
    query_id = hashlib.sha256(context.query.casefold().encode("utf-8")).hexdigest()[:12]
    provenance_id = f"prov-{context.source_digest}-planner-object-detection-{query_id}"
    for frame in frames_by_time:
        frame_path = root / context.source_digest / frame["file"]
        result = detect_image_tiered(
            str(frame_path), [context.query], named_object_requested=True,
            minimum_fidelity=0.85,
            required_output_evidence_types=frozenset({"object.detection"}))
        at = float(frame["t"])
        for index, detection in enumerate(result.detections):
            tick = evidence_contract.point_to_tick(at, evidence_contract.TICKS_PER_SECOND)
            value = {"label": detection.phrase, "box": detection.box,
                     "score": detection.score, "detector": detection.detector,
                     "tier_id": detection.tier_id,
                     "frame_time_s": at}
            record = evidence_contract.new_measurement(
                asset_id=asset["id"], clock_id=clock_id,
                start_tick=tick,
                end_tick=tick + max(1, evidence_contract.point_to_tick(
                    period, evidence_contract.TICKS_PER_SECOND)),
                measurement_type="object.detection", value=value,
                method=f"{detection.detector}-open-vocabulary-detection",
                provenance_id=provenance_id,
                sample_resolution=[width, height],
                coverage=evidence_contract.CoverageKind.SAMPLED,
                coverage_samples=1, coverage_sample_rate_hz=0.0)
            obs_id = (f"obs-planner-object-{context.source_digest[:12]}-"
                      f"{query_id}-{tick}-{index}")
            records.append(evidence_contract.to_legacy_observation(
                record, obs_id, "object_detection", detection.score))
    return {
        "observations": records,
        "provenance": [{"id": provenance_id, "producer": "OWLv2",
                        "producer_version": "google/owlv2-base-patch16-ensemble",
                        "source_digest": context.source_digest,
                        "recorded_at": None}],
        "answer": {"detections": len(records), "query": context.query,
                   "interval": list(context.interval),
                   "status": "candidate" if records else "inconclusive"},
    }


@dataclass(frozen=True)
class ProfilePolicy:
    """Execution policy; quality remains a caller-owned request value."""

    escalation: str


PROFILE_POLICIES = {
    ExecutionProfile.FAST_DISCOVERY: ProfilePolicy("escalate only for unmet requested evidence"),
    ExecutionProfile.EDIT_PREPARATION: ProfilePolicy("escalate until requested evidence meets its bar"),
    ExecutionProfile.PRECISION_EXECUTION: ProfilePolicy("escalate until requested evidence meets its bar or abstain"),
}


def plan_execution(
    graph: CapabilityGraph,
    request: TaskRequest,
    available: Iterable[EvidenceAvailable] = (),
) -> EvidenceExecutionPlan:
    if request.budget_seconds < 0:
        raise ValueError("budget_seconds must be non-negative")
    if not request.project_id and not request.authorise_cross_project:
        return _abstain("source project is not identified; cross-project acquisition needs authorisation")
    cached = tuple(available)
    selected_cache: list[EvidenceAvailable] = []
    missing: list[EvidenceNeed] = []
    for need in request.needs:
        hit = next((row for row in cached if row.evidence_type == need.evidence_type
                    and row.quality_metric == need.quality_metric
                    and (row.project_id == request.project_id
                         or request.authorise_cross_project)
                    and row.quality >= need.minimum_quality), None)
        if hit:
            selected_cache.append(hit)
        else:
            missing.append(need)
    if not missing:
        return EvidenceExecutionPlan("ready", (), (), tuple(selected_cache), 0.0, (),
                             ("all requested evidence is already sufficient",),
                             "answered from cached evidence")

    producers: dict[str, PlannedProducer] = {}
    unresolved: list[str] = []
    for need in missing:
        candidates = []
        for node in graph.nodes.values():
            if (need.evidence_type not in node.output_evidence
                    or request.task not in node.tasks):
                continue
            quality = tuple(q for q in node.quality
                            if q.benchmark_id == request.benchmark_id
                            and q.metric == need.quality_metric
                            and q.value >= need.minimum_quality)
            cost = next((c for c in node.costs
                         if c.benchmark_id == request.benchmark_id
                         and c.scope_id == request.cost_scope_id), None)
            if quality and cost:
                candidates.append((cost.seconds, cost.peak_memory_mb,
                                   node.capability_id, node, cost, quality))
        if not candidates:
            unresolved.append(need.evidence_type)
            continue
        _, _, _, node, cost, quality = min(candidates)
        for input_type in node.input_evidence:
            if input_type not in request.input_evidence and input_type not in {
                    row.evidence_type for row in cached}:
                unresolved.append(input_type)
        for dependency in node.dependencies:
            prerequisite = graph.nodes.get(dependency)
            if prerequisite is None:
                unresolved.append(f"dependency:{dependency}")
                continue
            dep_quality = next((q for q in prerequisite.quality
                                if q.benchmark_id == request.benchmark_id), None)
            dep_cost = next((c for c in prerequisite.costs
                             if c.benchmark_id == request.benchmark_id
                             and c.scope_id == request.cost_scope_id), None)
            if dep_quality is None or dep_cost is None:
                unresolved.append(f"dependency:{dependency}:unmeasured")
                continue
            producers[dependency] = PlannedProducer(
                dependency, prerequisite.producer_id or dependency,
                tuple(sorted(prerequisite.output_evidence)), dep_cost.seconds,
                dep_cost.peak_memory_mb, (dep_quality,), prerequisite.dependencies,
                request.interval)
        producers[node.capability_id] = PlannedProducer(
            node.capability_id, node.producer_id or node.capability_id,
            tuple(sorted(node.output_evidence)), cost.seconds,
            cost.peak_memory_mb, quality, node.dependencies, request.interval)

    ordered = _producer_order(producers)
    total = sum(row.cost_seconds for row in ordered)
    missing_names = tuple(sorted(set(unresolved)))
    if missing_names:
        return EvidenceExecutionPlan("abstain", missing_names, ordered,
                             tuple(selected_cache), total, (),
                             ("no measured producer meets the requested quality",),
                             "missing capability, dependency, or measured record")
    if total > request.budget_seconds:
        return EvidenceExecutionPlan("budget_exceeded", tuple(sorted(n.evidence_type for n in missing)),
                             ordered, tuple(selected_cache), total,
                             ("request a larger per-task budget",),
                             ("do not invoke producers before explicit authorisation",),
                             "predicted cost exceeds the declared task budget")
    policy = PROFILE_POLICIES[request.profile]
    return EvidenceExecutionPlan("ready", (), ordered, tuple(selected_cache), total,
                         (policy.escalation,),
                         ("stop when requested evidence meets its quality bar",
                          "abstain if measured evidence remains insufficient"))


def _abstain(reason: str) -> EvidenceExecutionPlan:
    return EvidenceExecutionPlan("abstain", (), (), (), 0.0, (),
                         ("do not run analysis without an authorised source scope",),
                         reason)


def _producer_order(producers: Mapping[str, PlannedProducer]
                    ) -> tuple[PlannedProducer, ...]:
    """Topologically order selected nodes, with stable ties and cycle refusal."""
    ordered: list[PlannedProducer] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError(f"capability dependency cycle at {key}")
        if key in visited:
            return
        visiting.add(key)
        producer = producers[key]
        for dependency in sorted(producer.dependencies):
            if dependency in producers:
                visit(dependency)
        visiting.remove(key)
        visited.add(key)
        ordered.append(producer)

    for key in sorted(producers):
        visit(key)
    return tuple(ordered)
