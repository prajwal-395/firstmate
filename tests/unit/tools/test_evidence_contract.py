"""FI-03: the measurement/evidence contract, proven on fixtures.

Every test names the defect it pins: a raw instrument value filed as
an evaluation, two resolutions compared as one method, a migration
that drops provenance, recorded lens data faked from pixels, ticks
narrowed into bins, or coverage that cannot say "unmeasured".

Hermetic: no project, no memory root, no model, no Resolve. The one
integration test drives ``project_model_evidence._Builder`` directly
with no filesystem.
"""

import pytest

from library.tools import evidence_contract as contract
from library.tools import project_model_evidence as evidence

ASSET = "asset-digest-001"
CLOCK = "clock-digest-001"
PROV_M1 = "prov-digest-001-m1"
PROV_M3 = "prov-digest-001-m3b"


def _sharpness(resolution, method):
    return contract.new_measurement(
        asset_id=ASSET, clock_id=CLOCK, start_tick=120000, end_tick=125000,
        measurement_type="image.sharpness.laplacian_variance", value=142.6,
        method=method, provenance_id="prov-digest-001-picture",
        sample_resolution=resolution,
        coverage=contract.CoverageKind.SAMPLED, coverage_samples=25,
        coverage_sample_rate_hz=5.0)


#: The real picture-quality stamp at its real geometry
#: (`SAMPLE_SHORT_SIDE_PX = 180` in `picture_quality.py`); the 720p
#: sibling names no lane today - FI-11 mints it. The test proves the
#: contract already treats the two geometries as different methods.
SHARP_180 = "picture-quality-deterministic_v1@180px"
SHARP_720 = "picture-quality-deterministic_v1@720p"


def test_raw_sharpness_cannot_inhabit_an_evaluation():
    """Acceptance: an evaluation-flavoured record from a raw
    instrument value raises - a Laplacian variance is a measurement,
    never a verdict."""
    with pytest.raises(contract.EvidenceTypeError):
        contract.new_evaluation(
            asset_id=ASSET, clock_id=CLOCK, start_tick=120000,
            end_tick=125000,
            measurement_type="image.sharpness.laplacian_variance",
            value=142.6, method=SHARP_180,
            provenance_id="prov-digest-001-picture",
            policy_id="steady_broll_v1", basis_measurement_ids=("m1",))
    with pytest.raises(contract.EvidenceTypeError):
        contract.EvidenceRecord(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1,
            target_entity_id=None, target_region=None,
            evidence_type=contract.EvidenceType.EVALUATION,
            measurement_type="image.sharpness.laplacian_variance",
            value=True, unit="variance",
            method=SHARP_180,
            sample_resolution=[320, 180],
            coverage=contract.CoverageKind.COMPLETE, coverage_samples=1,
            coverage_sample_rate_hz=0.0,
            provenance_id="prov-x", policy_id="steady_broll_v1",
            basis_measurement_ids=("m1",))


def test_evaluation_without_policy_or_basis_raises():
    """An evaluation that names no policy is taste filed as fact;
    one that cites no measurements has no basis."""
    with pytest.raises(contract.EvidenceTypeError):
        contract.new_evaluation(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
            measurement_type="eval.sharpness_pass", value=True,
            method="policy-check-v1", provenance_id="prov-x",
            policy_id="", basis_measurement_ids=("m1",))
    with pytest.raises(contract.EvidenceTypeError):
        contract.new_evaluation(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
            measurement_type="eval.sharpness_pass", value=True,
            method="policy-check-v1", provenance_id="prov-x",
            policy_id="steady_broll_v1", basis_measurement_ids=())


def test_sharpness_methods_differ_by_resolution():
    """Acceptance: sharpness at 180px vs 720p are different methods -
    the same frame at two geometries never compares equal."""
    low = _sharpness([320, 180], SHARP_180)
    high = _sharpness([1280, 720], SHARP_720)
    assert not contract.same_method(low.method, high.method)
    assert (contract.method_family(low.method)
            == contract.method_family(high.method))
    assert low.sample_resolution != high.sample_resolution


def test_m1_word_migration_preserves_provenance():
    """Acceptance (half): one real M1 word into the contract with its
    provenance intact and subsecond ticks preserved."""
    word = {"start": 1.234, "end": 1.5, "text": "hello",
            "confidence": 0.9}
    # Fixture stamp: production callers pass the lane doc's instrument
    # verbatim (FI-04); fixtures carry fixture stamps, never coined
    # lane names.
    record = contract.migrate_m1_word(
        word, ASSET, CLOCK, PROV_M1, method="fixture:m1-instrument",
        utterance_index=0)
    assert record.measurement_type == "speech.word"
    assert record.value == {"text": "hello", "utterance_index": 0}
    assert record.provenance_id == PROV_M1
    assert (record.start_tick, record.end_tick) == (1234, 1500)
    assert record.evidence_type is contract.EvidenceType.MEASUREMENT
    legacy = contract.to_legacy_observation(record, "obs-1", "speech_word",
                                            confidence=0.9)
    assert legacy["provenance_id"] == PROV_M1
    assert legacy["payload"]["text"] == "hello"
    assert legacy["interval"] == {"clock_id": CLOCK, "start": 1234,
                                  "end": 1500}
    assert legacy["payload"]["evidence_contract"]["measurement_type"] \
        == "speech.word"


def test_m3_face_span_migration_preserves_provenance():
    """Acceptance (half): one real M3 face span into the contract
    with its provenance intact and the box unmodified."""
    span = {"start": 2.0, "end": 2.5, "box": [0.1, 0.2, 0.3, 0.4],
            "det_score": 0.97}
    # 1408 is `FACE_INPUT_MAX_WIDTH` in `person_entity.py` (M3b draws
    # at 1408px, never the 384px thumbnails); the stamp is a fixture
    # label, as above.
    record = contract.migrate_m3_face_span(
        span, "face_001", ASSET, CLOCK, PROV_M3,
        method="fixture:m3b-instrument", sample_resolution=[1408, 792])
    assert record.measurement_type == "person.face_box"
    assert record.value == {"face_track": "face_001",
                            "box": [0.1, 0.2, 0.3, 0.4]}
    assert record.provenance_id == PROV_M3
    assert record.target_region == "face"
    assert record.sample_resolution == [1408, 792]


def test_focus_distance_honesty():
    """Plan C1: lens distance is recorded, sharpness measured,
    defocus inferred with uncertainty - and each refuses the
    others' shape."""
    recorded = contract.new_measurement(
        asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
        measurement_type="lens.focus_distance_m", value=2.5,
        method="recorded:fixture-lens", provenance_id="prov-m0")
    assert recorded.unit == "m"
    with pytest.raises(contract.EvidenceTypeError):
        contract.new_measurement(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
            measurement_type="lens.focus_distance_m", value=2.5,
            method="estimated:fixture-defocus", provenance_id="prov-x")
    with pytest.raises(contract.EvidenceContractError):
        contract.new_inference(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
            measurement_type="image.defocus_likelihood", value=0.7,
            method="estimated:fixture-defocus", provenance_id="prov-x",
            uncertainty=None,
            sample_resolution=[320, 180])
    inferred = contract.new_inference(
        asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
        measurement_type="image.defocus_likelihood", value=0.7,
        method="estimated:fixture-defocus", provenance_id="prov-x",
        uncertainty=0.2, sample_resolution=[320, 180])
    assert inferred.unit == "probability"


def test_ticks_are_authoritative_and_bins_are_derived():
    """Plan A3: subsecond survives the float-to-tick trip, and the
    M9 one-second bins derive from ticks without narrowing them."""
    start, end = contract.sec_to_ticks(120.0, 125.0)
    assert (start, end) == (120000, 125000)
    narrow_start, narrow_end = contract.sec_to_ticks(1.2345, 1.2349)
    assert (narrow_start, narrow_end) == (1234, 1235)
    bins = contract.derive_second_bins(start, end)
    assert bins[0] == (120000, 121000)
    assert bins[-1] == (124000, 125000)
    assert bins[0][0] <= start and bins[-1][1] >= end
    with pytest.raises(contract.TemporalAlignmentError):
        contract.sec_to_ticks(2.0, 2.0)
    with pytest.raises(contract.TemporalAlignmentError):
        contract.sec_to_ticks(float("nan"), 1.0)


def test_multicam_offset_carries_uncertainty():
    """A shifted interval travels with its sync uncertainty, so a
    join can refuse to treat it as aligned."""
    offset = contract.MulticamOffset(offset_seconds=0.5,
                                     uncertainty_seconds=0.02)
    moved = contract.apply_offset(1000, 2000, offset)
    assert moved == (1500, 2500, 20)
    with pytest.raises(contract.TemporalAlignmentError):
        contract.MulticamOffset(offset_seconds=0.5,
                                uncertainty_seconds=-0.1)


def test_unavailable_carries_nothing():
    """Coverage semantics: unavailable means unmeasured - no samples
    and no value smuggled alongside; measured-absent is complete
    with zero samples instead."""
    absent = contract.new_measurement(
        asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
        measurement_type="speech.word", value="",
        method="whisper-mfa-v1", provenance_id="prov-x",
        coverage=contract.CoverageKind.COMPLETE, coverage_samples=0,
        coverage_sample_rate_hz=0.0)
    assert absent.coverage_samples == 0
    with pytest.raises(contract.EvidenceContractError):
        contract.new_measurement(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1000,
            measurement_type="speech.word", value="hello",
            method="whisper-mfa-v1", provenance_id="prov-x",
            coverage=contract.CoverageKind.UNAVAILABLE,
            coverage_samples=0, coverage_sample_rate_hz=0.0)


def test_migration_without_a_method_raises():
    """The contract refuses to coin a method: a migration that names
    none is a defect, not a default."""
    with pytest.raises(contract.EvidenceContractError):
        contract.migrate_m1_word(
            {"start": 1.0, "end": 1.5, "text": "hello"}, ASSET, CLOCK,
            PROV_M1, method="")
    with pytest.raises(contract.EvidenceContractError):
        contract.migrate_m3_face_span(
            {"start": 1.0, "end": 1.5, "box": [0, 0, 1, 1]}, "face_001",
            ASSET, CLOCK, PROV_M3, method="",
            sample_resolution=[1408, 792])


def test_unknown_type_and_unit_mismatch_raise():
    with pytest.raises(contract.UnknownMeasurementTypeError):
        contract.new_measurement(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1,
            measurement_type="image.vibes", value=1.0,
            method="m", provenance_id="p")
    with pytest.raises(contract.UnitMismatchError):
        contract.EvidenceRecord(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1,
            target_entity_id=None, target_region=None,
            evidence_type=contract.EvidenceType.MEASUREMENT,
            measurement_type="image.sharpness.laplacian_variance",
            value=1.0, unit="metres",
            method=SHARP_180,
            sample_resolution=[320, 180],
            coverage=contract.CoverageKind.COMPLETE, coverage_samples=1,
            coverage_sample_rate_hz=0.0, provenance_id="p")


def test_rust_evidence_keys_are_pinned():
    """The Python dict and the Rust struct share one key set; drift
    breaks the protocol silently, so the set is asserted here."""
    record = _sharpness([320, 180], SHARP_180)
    assert set(contract.to_rust_evidence(record)) \
        == set(contract.RUST_EVIDENCE_KEYS)


def test_canonical_certainty_is_explicit_and_evaluation_stays_separate():
    from dataclasses import replace

    observed = replace(_sharpness([320, 180], SHARP_180),
                       certainty_state=contract.CertaintyState.OBSERVED)
    assert contract.to_rust_evidence(observed)["certainty"] == "observed"
    assert contract.to_rust_evidence(_sharpness([320, 180], SHARP_180))["certainty"] == "measured"


def test_illegal_certainty_coercions_raise():
    """Acceptance: the four states are epistemic, not ordinal - a
    measurement is observed or measured, an inference inferred, a
    creative judgment interpreted, and a policy evaluation has none."""
    from dataclasses import replace

    with pytest.raises(contract.EvidenceTypeError):
        replace(_sharpness([320, 180], SHARP_180),
                certainty_state=contract.CertaintyState.INFERRED)
    with pytest.raises(contract.EvidenceTypeError):
        contract.EvidenceRecord(
            asset_id=ASSET, clock_id=CLOCK, start_tick=0, end_tick=1,
            target_entity_id=None, target_region=None,
            evidence_type=contract.EvidenceType.EVALUATION,
            measurement_type="eval.sharpness_pass",
            value=True, unit="boolean",
            method="policy-check-v1",
            sample_resolution=None,
            coverage=contract.CoverageKind.COMPLETE, coverage_samples=1,
            coverage_sample_rate_hz=0.0, provenance_id="prov-x",
            policy_id="steady_broll_v1", basis_measurement_ids=("m1",),
            basis_rule_ids=("sharpness-min",),
            certainty_state=contract.CertaintyState.MEASURED)


def test_evidence_links_to_source_samples_and_frames():
    """Acceptance (observations row): a record cites the source
    samples/frames and input observations it was read from, plus its
    spatial frame, coordinate space, coverage scope/level, producer
    version and freshness - and the emitted dict carries them to Rust."""
    from dataclasses import replace

    record = replace(
        _sharpness([320, 180], SHARP_180),
        processing_status=contract.ProcessingStatus.NOT_ANALYZED,
        source_reference="frame:12",
        spatial_frame_id="frame-12",
        coordinate_space_id="space-pixels",
        references=(
            {"kind": "source_frame", "stream_id": "stream-video",
             "frame_index": 12},
            {"kind": "observation", "observation_id": "obs-1"},
        ),
        coverage_scope={"interval": {"clock_id": CLOCK, "start": 0,
                                     "end": 1000},
                        "coordinate_space_id": "space-pixels",
                        "target_entity_id": None},
        coverage_level="l1",
        producer_version="picture-quality-deterministic_v1",
        freshness="fresh")
    emitted = contract.to_rust_evidence(record)
    assert set(emitted) == set(contract.RUST_EVIDENCE_KEYS)
    assert emitted["references"][0]["frame_index"] == 12
    assert emitted["coverage_scope"]["coordinate_space_id"] == "space-pixels"
    assert emitted["coverage_level"] == "l1"
    assert emitted["freshness"] == "fresh"


def test_evidence_links_refuse_unknown_kinds_and_ranges():
    from dataclasses import replace

    with pytest.raises(contract.EvidenceContractError):
        replace(_sharpness([320, 180], SHARP_180),
                references=({"kind": "vibes", "stream_id": "s"},))
    with pytest.raises(contract.EvidenceContractError):
        replace(_sharpness([320, 180], SHARP_180),
                references=({"kind": "source_frame", "stream_id": "s"},))
    with pytest.raises(contract.EvidenceContractError):
        replace(_sharpness([320, 180], SHARP_180), freshness="mouldy")
    with pytest.raises(contract.EvidenceContractError):
        replace(_sharpness([320, 180], SHARP_180), coverage_level="l9")
    with pytest.raises(contract.EvidenceContractError):
        replace(_sharpness([320, 180], SHARP_180),
                coverage_scope={"interval": {"clock_id": CLOCK, "start": 9,
                                             "end": 9}})


def test_migration_path_audits_mapped_modalities():
    """The builder audits mapped modalities and leaves unmapped M7/M10
    prose untouched."""
    builder = evidence._Builder("test-project")
    assert builder.observation(
        "d", "m1", ASSET, CLOCK, 1.0, 1.5, "speech_word",
        {"text": "hello"}, 0.9, "library.tools.source_memory:M1")
    assert builder.observation(
        "d", "m3b", ASSET, CLOCK, 2.0, 2.5, "face",
        {"face_track": "face_001"}, None,
        "library.tools.person_entity")
    assert builder.observation(
        "d", "m4", ASSET, CLOCK, 0.0, 5.0, "scene",
        {"location": "studio"}, None, "v3") is not None
    assert contract.audit_legacy_observation("scene", {}) == (
        "video.scene_segment")
    assert contract.audit_legacy_observation("sound_event", {}) == (
        "audio.sound_event")
