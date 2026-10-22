"""The portable multimodal evidence model: what it refuses, and what it fixes.

Every test here names a DEFECT the model exists to end. A gate that
cannot fail is worse than no gate (AGENTS.md 10.4), so each test
asserts a refusal or a measured change, and none of them asserts that
the code merely runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from library.tools import claim_provenance
from library.tools import evidence_contract
from library.tools import source_reference
from library.tools import portable_evidence as pe

DIGEST = "a" * 64


def _contract(**overrides):
    """One FI-03 contract as the M1 migration writes it."""
    contract = {
        "evidence_type": "measurement",
        "certainty": "measured",
        "measurement_type": "speech.word",
        "value": {"text": "hello"},
        "unit": "text",
        "method": "voz-mfa",
        "sample_resolution": None,
        "coverage": "complete",
        "coverage_samples": 1,
        "coverage_sample_rate_hz": 0.0,
        "target_entity_id": None,
        "target_region": None,
        "uncertainty": None,
        "policy_id": None,
        "basis_measurement_ids": [],
        "basis_rule_ids": [],
        "contract_version": 1,
        "references": [],
    }
    contract.update(overrides)
    return contract


def _observation(**overrides):
    """One stored observation, as the Project Model holds it."""
    observation = {
        "id": "obs-1",
        "asset_id": "asset-a",
        "interval": {"clock_id": "clock-a", "start": 1000, "end": 1500},
        "modality": "speech_word",
        "payload": {
            "text": "hello",
            "utterance_index": 0,
            "evidence_contract": _contract(),
        },
        "confidence": None,
        "provenance_id": "prov-a-m1",
    }
    observation.update(overrides)
    return observation


def _model(observations=None):
    """A small Project Model: one asset, one video stream, one audio."""
    return {
        "id": "project-test",
        "schema_version": 1,
        "assets": [{
            "id": "asset-a",
            "content_digest": DIGEST,
            "kind": "video",
            "metadata": {"duration_seconds": 10.0},
        }],
        "streams": [
            {"id": "stream-a", "asset_id": "asset-a", "clock_id": "clock-a",
             "stream_index": 0, "metadata": {"codec_type": "video"}},
            {"id": "stream-a-1", "asset_id": "asset-a", "clock_id": "clock-a",
             "stream_index": 1, "metadata": {"codec_type": "audio"}},
        ],
        "clocks": [{
            "id": "clock-a",
            "domain": {"kind": "source", "asset_id": "asset-a"},
            "ticks_per_second": 1000,
            "frame_rate": {"numerator": 25, "denominator": 1},
        }],
        "provenance": [{
            "id": "prov-a-m1",
            "producer": "library.tools.source_memory:M1",
            "producer_version": "1",
            "source_digest": DIGEST,
        }],
        "observations": list(observations or [_observation()]),
        "clip_uses": [{"id": "use:reel-x:0000", "asset_id": "asset-a"}],
    }


# ── The model ──────────────────────────────────────────────────────


def test_the_model_reuses_the_existing_vocabularies():
    """The portable model adds no second vocabulary.

    The source reference is the universal one, the epistemic class is
    the one the claim producers declare, and the coverage kinds are
    the contract's. A fourth spelling of any of them is the thing
    this lane exists to prevent.
    """
    assert pe.PROVENANCE_CLASSES == (
        "measured", "inferred", "interpreted", claim_provenance.UNKNOWN_CLASS)
    assert pe.COVERAGE_KINDS == tuple(
        kind.value for kind in evidence_contract.CoverageKind)
    assert pe.SOURCE_MODALITY["text"] == "audio"


def test_one_source_clock_rate():
    """ONE enumeration: the contract's tick rate IS the reference's.

    Two spellings of a source's tick rate are two clocks that can
    drift apart, and a record counted on one reads at the wrong time
    on the other.
    """
    assert (evidence_contract.TICKS_PER_SECOND
            == source_reference.SOURCE_TICKS_PER_SECOND)


# ── The class a model description is filed under ───────────────────


def test_a_models_description_is_not_filed_as_a_measurement():
    """The defect: production files EVERY model description as measured.

    A transcription is an inference - a model asserted it. The stored
    contract says ``measurement``/``measured``, and a reader has no way
    to tell that from an instrument's reading.
    """
    model = _model()
    records, refusals = pe.migrate_observations(
        model, model["observations"])
    assert refusals == []
    assert records[0]["provenance"]["class"] == "inferred"
    assert records[0]["provenance"]["filed_class"] == "measured"
    assert records[0]["lossy"] is True


def test_the_census_names_the_filing_disagreement():
    """The disagreement is counted, not averaged away."""
    model = _model()
    records, _refusals = pe.migrate_observations(model,
                                                 model["observations"])
    index = pe.ModelIndex(model)
    stored = pe.census_rows(pe._stored_rows(model["observations"], index))
    portable = pe.census_rows(pe._portable_rows(records))
    assert stored["filing_disagreements"] == 1
    assert stored["class_defects"] == 1
    assert portable["class_defects"] == 0
    assert portable["filing_disagreements"] == 1


# ── The three attributes ──────────────────────────────────────────


def test_a_record_with_no_reference_carries_none_of_the_three():
    """The before: a stored record with no source reference.

    It has a class and a coverage, and it points at nothing.
    """
    model = _model()
    index = pe.ModelIndex(model)
    stored = pe.census_rows(pe._stored_rows(model["observations"], index))
    assert stored["with_source_reference"] == 0
    assert stored["with_all_three"] == 0


def test_the_migrated_record_carries_all_three():
    """The after: one shape, and every attribute on it."""
    model = _model()
    records, _refusals = pe.migrate_observations(model,
                                                 model["observations"])
    portable = pe.census_rows(pe._portable_rows(records))
    assert portable["distinct_shapes"] == 1
    assert portable["with_source_reference"] == 1
    assert portable["with_provenance_class"] == 1
    assert portable["with_coverage"] == 1
    assert portable["with_all_three"] == 1


def test_a_record_with_no_contract_is_refused_by_name():
    """A record with nothing to migrate is refused, not guessed at."""
    model = _model([_observation(payload={"text": "hello"})])
    records, refusals = pe.migrate_observations(model,
                                                 model["observations"])
    assert records == []
    assert refusals and "no evidence contract" in refusals[0]["reason"]


# ── Recovering the original frames and samples ────────────────────


def test_a_span_longer_than_its_source_is_refused():
    """The defect the reel build leaves behind: a span on another grid.

    A record whose interval ends past the source's own length is not a
    span of that source, and a reference to it would recover frames
    that are not there. It is refused, and the refusal is named.
    """
    model = _model([_observation(
        interval={"clock_id": "clock-a", "start": 1, "end": 10 ** 30})])
    records, refusals = pe.migrate_observations(model,
                                                 model["observations"])
    assert records == []
    assert refusals and "not a span of this source" in refusals[0]["reason"]


def test_a_transcription_points_at_the_audio_it_was_heard_in():
    """A text record is read from the audio, and says so."""
    model = _model()
    records, _refusals = pe.migrate_observations(model,
                                                 model["observations"])
    record = records[0]
    assert record["modality"] == "text"
    assert record["source_references"][0]["stream_id"] == "stream-a-1"
    assert record["modality_refs"][0]["modality"] == "audio"


def test_a_legacy_reference_is_upgraded_and_never_written():
    """The pre-contract spelling is read; the modern one is written."""
    legacy = {"kind": "source_frame", "stream_id": "stream-a",
              "frame_index": 12}
    model = _model([_observation(
        payload={"text": "hello",
                 "evidence_contract": _contract(references=[legacy])})])
    records, _refusals = pe.migrate_observations(model,
                                                 model["observations"])
    reference = records[0]["source_references"][0]
    assert reference["kind"] == "element"
    assert reference["element"] == {"kind": "frame", "frame_index": 12}
    assert reference["clock_id"] == "clock-a"


# ── The refusals the model makes on a record ──────────────────────


def _portable_record(**overrides):
    record = {
        "id": "rec-1",
        "kind": "interval",
        "modality": "text",
        "asset_id": "asset-a",
        "stream_id": "stream-a-1",
        "clock_id": "clock-a",
        "interval": {"clock_id": "clock-a", "start": 0, "end": 10},
        "source_references": [{
            "kind": "interval", "stream_id": "stream-a-1",
            "interval": {"clock_id": "clock-a", "start": 0, "end": 10}}],
        "modality_refs": [],
        "provenance": {"class": "inferred", "producer": "p",
                       "measurement_type": "speech.word"},
        "coverage": {"kind": "complete", "samples": 1},
        "lossy": True,
        "semantic": {"text": "hello"},
        "value": None,
        "entities": [],
        "relation": None,
        "evidence_model": pe.SCHEMA,
        "version": pe.SCHEMA_VERSION,
    }
    record.update(overrides)
    return record


def test_a_record_with_no_source_reference_is_refused():
    """A record that names no source is a record about nothing."""
    with pytest.raises(pe.PortableEvidenceError) as refusal:
        pe.parse_record(_portable_record(source_references=[]))
    assert "source reference" in str(refusal.value)


def test_a_lossy_description_filed_as_a_measurement_is_refused():
    """A model's words are not an instrument's reading."""
    with pytest.raises(pe.PortableEvidenceError) as refusal:
        pe.parse_record(_portable_record(
            provenance={"class": "measured", "producer": "p",
                        "measurement_type": "speech.word"}))
    assert "not a measurement" in str(refusal.value)


def test_a_newer_record_is_refused():
    """A record this reader does not know is never half-read."""
    with pytest.raises(pe.PortableEvidenceError) as refusal:
        pe.parse_record(_portable_record(version=pe.SCHEMA_VERSION + 1))
    assert "this reader knows" in str(refusal.value)


def test_an_unavailable_coverage_carries_no_value():
    """Unmeasured is not measured-empty."""
    with pytest.raises(pe.PortableEvidenceError) as refusal:
        pe.parse_record(_portable_record(
            coverage={"kind": "unavailable", "samples": 0},
            value={"text": "hello"}))
    assert "unavailable coverage carries no samples and no value" in \
        str(refusal.value)


# ── The document ──────────────────────────────────────────────────


def test_the_document_separates_the_media_from_the_records():
    """The immutable original media is one section; the records another."""
    model = _model()
    records, _refusals = pe.migrate_observations(model,
                                                 model["observations"])
    document = pe.build_portable_document(model, records,
                                          pe.ModelIndex(model),
                                          ["asset-a"])
    assert document["schema"] == pe.SCHEMA
    assert document["schema_version"] == pe.SCHEMA_VERSION
    assert document["sources"][0]["content_digest"] == DIGEST
    assert document["records"][0]["id"] == "obs-1"
    assert "uri" not in document["sources"][0]


def test_a_document_carrying_a_local_path_is_refused():
    """A portable document names the media, not where this machine keeps it."""
    local = str(Path.home() / "media.mov")
    with pytest.raises(pe.PortableEvidenceError) as refusal:
        pe.assert_portable({"sources": [{"uri": local}]})
    assert "local path" in str(refusal.value)
