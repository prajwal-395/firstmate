"""Hermetic tests for the FI-03 analyzer-provider transcription slice."""

import pytest

from library.tools import analyzer_providers as ap
from library.tools import evidence_contract as evidence

BENCHMARK = "fixture-transcription-v1"


def _provider(provider_id, *, latency, memory, accuracy, stability,
              available=True, run=None):
    return ap.Provider(
        capability="transcription", provider_id=provider_id, version="1",
        availability_probe=lambda: (available, "fixture probe"),
        declared_cost=ap.ProviderCost(latency, memory),
        quality=(ap.QualityRecord(BENCHMARK, accuracy, stability, 3),),
        run=run or (lambda **kwargs: {"segments": []}),
    )


def _requirement(**changes):
    values = {"capability": "transcription",
              "measurement_type": "speech.word",
              "minimum_accuracy": .9, "minimum_stability": .9,
              "maximum_latency_ms": 1000, "maximum_memory_mb": 1024,
              "benchmark_id": BENCHMARK}
    values.update(changes)
    return ap.EvidenceRequirement(**values)


def test_registry_selects_lowest_latency_that_meets_measured_requirement():
    registry = ap.ProviderRegistry((
        _provider("slow", latency=400, memory=300, accuracy=.98, stability=.99),
        _provider("quick-but-inaccurate", latency=25, memory=200,
                  accuracy=.7, stability=.99),
        _provider("fast", latency=80, memory=250, accuracy=.95, stability=.96),
    ))

    selected = registry.select(_requirement())

    assert selected.provider.provider_id == "fast"
    assert "lowest declared latency" in selected.reason
    assert "accuracy=0.95" in selected.reason


def test_registry_refuses_without_quality_and_names_why():
    registry = ap.ProviderRegistry((ap.Provider(
        "transcription", "unmeasured", "1", lambda: (True, "ready"),
        ap.ProviderCost(10, 10), (), lambda **kwargs: None),))

    with pytest.raises(ap.ProviderSelectionError, match="no measured quality"):
        registry.select(_requirement())


@pytest.mark.parametrize("native,provider", [
    ({"segments": [{"word": "hello", "start": .1, "end": .4}]}, "ren-voz"),
    ({"segments": [{"text": "hello there", "speaker": "S1",
                     "words": [{"text": "hello", "start": .1, "end": .4},
                               {"text": "there", "start": .5, "end": .8}]}]},
     "resolve-get-transcription"),
])
def test_normalizes_native_transcripts_to_fi03_records(native, provider):
    records = ap.normalize_transcription(
        native, asset_id="asset-a", clock_id="source-clock",
        source_digest="sha256:abc", provider_id=provider,
        provider_version="fixture-1", start_tick=1000)

    assert [row.value["word"] for row in records] == (
        ["hello"] if provider == "ren-voz" else ["hello", "there"])
    assert records[0].measurement_type == "speech.word"
    assert records[0].evidence_type is evidence.EvidenceType.MEASUREMENT
    assert records[0].start_tick == 1100
    assert records[0].provenance_id == (
        f"prov-sha256:abc-transcription-{provider}@fixture-1")
    assert records[0].producer_version == "fixture-1"
    assert records[0].method == f"analyzer-provider/{provider}"


def test_transcription_execution_keeps_selection_reason_and_normalized_output():
    registry = ap.ProviderRegistry((_provider(
        "fixture", latency=20, memory=100, accuracy=.99, stability=.99,
        run=lambda **kwargs: {"segments": [{"text": "word",
                                             "start": 0, "end": .2}]}),))

    result = ap.transcribe_with_provider(
        registry, _requirement(), asset_id="a", clock_id="c",
        source_digest="digest")

    assert result.selection.provider.provider_id == "fixture"
    assert "qualified" in result.selection.reason
    assert result.records[0].value["word"] == "word"
    assert result.provenance.producer == "fixture"
    assert result.records[0].provenance_id == result.provenance.id


def test_transcription_registry_wires_exactly_two_providers_without_live_resolve():
    registry = ap.transcription_registry(
        voz_cost=ap.ProviderCost(1, 1), resolve_cost=ap.ProviderCost(2, 2),
        voz_quality=(), resolve_quality=(),
        voz_probe=lambda: (False, "fixture unavailable"),
        resolve_probe=lambda: (False, "fixture unavailable"))

    assert {p.provider_id for p in registry.providers("transcription")} == {
        "ren-voz", "resolve-get-transcription"}
