"""Provider selection and normalization for FI-03 evidence producers.

This first slice wires transcript evidence only. Providers produce their
native response; this module is the sole boundary that turns it into FI-03
records. Selection is explicit and fail-closed when quality measurements or
availability are missing. Quality limits are caller-authored requirements,
not policy defaults (FI-22).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
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
        begin = start_tick + round(start * evidence.TICKS_PER_SECOND)
        finish = start_tick + round(end * evidence.TICKS_PER_SECOND)
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

