//! FI-03: the evidence contract is additive - no breaking change.
//!
//! An observation written before the contract (no `evidence` key)
//! still decodes, still validates, and still serializes back without
//! the key. A record filed under the wrong evidence type fails
//! validation, mirroring the Python `EvidenceTypeError`.

use ren_protocol::project_model::{
    validate_project_model, AssetId, ClockId, EntityId, Interval, Observation, ObservationId,
    ProjectModel, ProvenanceId, TimebasePolicy, EVIDENCE_CONTRACT_VERSION,
};
use serde_json::{json, Value};

fn observation_without_evidence() -> Value {
    json!({
        "id": "obs-d-m1-0001",
        "asset_id": "asset-d",
        "interval": {"clock_id": "clock-d", "start": 1000, "end": 1500},
        "modality": "speech_word",
        "payload": {"text": "hello"},
        "confidence": 0.9,
        "provenance_id": "prov-d-m1"
    })
}

fn observation_with_evidence(evidence: Value) -> Value {
    let mut observation = observation_without_evidence();
    observation["evidence"] = evidence;
    observation
}

fn measurement_evidence() -> Value {
    json!({
        "evidence_type": "measurement",
        "measurement_type": "speech.word",
        "value": {"text": "hello", "utterance_index": 0},
        "unit": "text",
        "method": "whisper-mfa-v1",
        "sample_resolution": null,
        "coverage": "complete",
        "coverage_samples": 1,
        "coverage_sample_rate_hz": 0.0,
        "target_entity_id": null,
        "target_region": null,
        "uncertainty": null,
        "policy_id": null,
        "basis_measurement_ids": [],
        "contract_version": EVIDENCE_CONTRACT_VERSION,
    })
}

fn model_with_observations(observations: Vec<Observation>) -> ProjectModel {
    ProjectModel {
        id: ren_protocol::project_model::ProjectId("test".into()),
        schema_version: ren_protocol::project_model::PROJECT_MODEL_SCHEMA_VERSION,
        timebase_policy: TimebasePolicy::IntegerTicks,
        clocks: vec![],
        assets: vec![],
        streams: vec![],
        entities: vec![],
        observations,
        identity_links: vec![],
        masks: vec![],
        object_classes: vec![],
        tracks: vec![],
        relations: vec![],
        intents: vec![],
        clip_uses: vec![],
        edits: vec![],
        effects: vec![],
        renders: vec![],
        frame_maps: vec![],
        nle_bindings: vec![],
        receipts: vec![],
        provenance: vec![],
        signals: vec![],
        events: vec![],
        interpretations: vec![],
        coordinate_spaces: vec![],
        time_transforms: vec![],
        space_transforms: vec![],
        query_results: vec![],
    }
}

#[test]
fn an_observation_without_evidence_still_decodes() {
    let parsed: Observation = serde_json::from_value(observation_without_evidence())
        .expect("a pre-contract observation must still decode");
    assert!(parsed.evidence.is_none());
    let reserialized = serde_json::to_value(&parsed).unwrap();
    assert!(
        reserialized.get("evidence").is_none(),
        "a contract-less observation serializes back without the key"
    );
    assert!(validate_project_model(&model_with_observations(vec![parsed])).is_ok());
}

#[test]
fn a_measurement_record_round_trips() {
    let parsed: Observation =
        serde_json::from_value(observation_with_evidence(measurement_evidence()))
            .expect("a contract observation must decode");
    let evidence = parsed.evidence.as_ref().expect("evidence must be present");
    assert_eq!(evidence.contract_version, EVIDENCE_CONTRACT_VERSION);
    assert!(validate_project_model(&model_with_observations(vec![parsed])).is_ok());
}

#[test]
fn an_evaluation_without_a_policy_is_refused() {
    let mut bad = measurement_evidence();
    bad["evidence_type"] = json!("evaluation");
    bad["measurement_type"] = json!("eval.sharpness_pass");
    bad["unit"] = json!("boolean");
    let parsed: Observation = serde_json::from_value(observation_with_evidence(bad))
        .expect("shape decodes; validation refuses it");
    assert!(validate_project_model(&model_with_observations(vec![parsed])).is_err());
}

#[test]
fn an_inference_without_uncertainty_is_refused() {
    let mut bad = measurement_evidence();
    bad["evidence_type"] = json!("inference");
    bad["measurement_type"] = json!("image.defocus_likelihood");
    bad["unit"] = json!("probability");
    let parsed: Observation = serde_json::from_value(observation_with_evidence(bad))
        .expect("shape decodes; validation refuses it");
    assert!(validate_project_model(&model_with_observations(vec![parsed])).is_err());
}

#[test]
fn clock_and_entity_ids_keep_their_shapes() {
    // The contract's target and clock references reuse the existing id
    // types; a typo here would be a key-name mismatch across the FFI.
    let _ = (
        AssetId("a".into()),
        ClockId("c".into()),
        EntityId("e".into()),
    );
    let _ = (
        ObservationId("o".into()),
        ProvenanceId("p".into()),
        Interval {
            clock_id: ClockId("c".into()),
            start: 0,
            end: 1,
        },
    );
}
