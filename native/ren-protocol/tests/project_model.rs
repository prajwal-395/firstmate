use ren_protocol::{decode_project_model, ProjectModelDecodeError, PROJECT_MODEL_SCHEMA_VERSION};

#[test]
fn shared_project_model_fixture_decodes_and_round_trips() {
    let fixture = include_str!("../fixtures/project-model.json");
    let model = decode_project_model(fixture).expect("valid shared fixture");
    assert_eq!(model.schema_version, PROJECT_MODEL_SCHEMA_VERSION);
    assert_eq!(model.entities.len(), 2);
    assert_eq!(model.clip_uses[0].entity_links[0].0, "person-1");
    let encoded = serde_json::to_string(&model).unwrap();
    let decoded = decode_project_model(&encoded).unwrap();
    assert_eq!(decoded, model);
}

#[test]
fn unknown_versions_and_empty_intervals_are_rejected() {
    let fixture = include_str!("../fixtures/project-model.json");
    let unknown = fixture.replace("\"schema_version\": 1", "\"schema_version\": 2");
    assert_eq!(
        decode_project_model(&unknown),
        Err(ProjectModelDecodeError::UnsupportedVersion(2))
    );
    let invalid_interval = fixture.replace("\"start\":0,\"end\":24", "\"start\":24,\"end\":24");
    assert!(matches!(
        decode_project_model(&invalid_interval),
        Err(ProjectModelDecodeError::InvalidModel(_))
    ));
}

#[test]
fn typed_events_queries_and_transforms_round_trip_without_implied_sync() {
    let mut model: serde_json::Value =
        serde_json::from_str(include_str!("../fixtures/project-model.json")).unwrap();
    model["streams"][0]["source_digest"] = serde_json::json!("sha256:source");
    model["streams"][0]["coverage"] = serde_json::json!("analyzed");
    model["observations"][0]["certainty"] = serde_json::json!("observed");
    model["observations"][0]["evidence"] = serde_json::json!({
        "evidence_type":"measurement", "measurement_type":"pts", "value":12,
        "unit":"ticks", "method":"demux", "sample_resolution":null,
        "coverage":"complete", "coverage_samples":1, "coverage_sample_rate_hz":0.0,
        "target_entity_id":null, "target_region":null, "uncertainty":null,
        "policy_id":null, "basis_measurement_ids":[], "contract_version":1,
        "certainty":"observed", "source_reference":"frame:12",
        "spatial_frame_id":"frame-12", "coordinate_space_id":"space-pixels"
    });
    model["events"] = serde_json::json!([{
        "id":"event-1", "interval":{"clock_id":"clock-source","start":1,"end":10},
        "kind":"picking_up", "participant_ids":["person-1"], "status":"candidate",
        "certainty":"inferred", "evidence_ids":["obs-1"], "confidence":null,
        "state_changes":[{"entity_id":"person-1","property":"held_object",
            "from":null,"to":"cup","evidence_ids":["obs-1"]}]
    }]);
    model["interpretations"] = serde_json::json!([{
        "id":"interpretation-1", "kind":"demonstration", "author":"fixture-model",
        "objective":"product-demo", "policy_id":null, "evidence_ids":["event-1"],
        "scope":{"interval":"event-1"}, "uncertainty":0.2, "certainty":"interpreted"
    }]);
    model["query_results"] = serde_json::json!([
        {"status":"present","candidate_evidence_ids":["obs-1"],"required_missing_lanes":[],"completeness_basis":[]},
        {"status":"confidently_absent","candidate_evidence_ids":[],"required_missing_lanes":[],"completeness_basis":[{"lane":"motion","coverage":"complete","freshness":"fresh","scope":{"interval":{"clock_id":"clock-source","start":0,"end":24},"coordinate_space_id":"space-pixels","target_entity_id":null},"producer_version":"motion-v1","level":"l1"}]},
        {"status":"not_yet_analysed","candidate_evidence_ids":[],"required_missing_lanes":["ocr"],"completeness_basis":[]},
        {"status":"inconclusive","candidate_evidence_ids":[],"required_missing_lanes":[],"completeness_basis":[]}
    ]);
    model["time_transforms"] = serde_json::json!([{
        "id":"source-to-presentation", "from_clock":"clock-source", "to_clock":"clock-source",
        "provenance_id":"prov-1", "segments":[{"from":{"clock_id":"clock-source","start":0,"end":24},
        "to":{"clock_id":"clock-source","start":0,"end":24},"slope":{"numerator":1,"denominator":1},
        "offset_ticks":0,"uncertainty_ticks":1,"invertible":true}]
    }]);
    let text = serde_json::to_string(&model).unwrap();
    let decoded = decode_project_model(&text).expect("FI-03R record fixture validates");
    assert_eq!(
        decoded.events[0].status,
        ren_protocol::project_model::VerificationStatus::Candidate
    );
    assert_eq!(decoded.events[0].state_changes.len(), 1);
    assert_eq!(decoded.query_results.len(), 4);
    assert_eq!(decoded.streams[0].clock_id.0, "clock-source");
    assert!(decoded.time_transforms[0].segments[0].uncertainty_ticks > 0);
    assert_eq!(
        serde_json::to_value(decoded).unwrap()["events"][0]["status"],
        "candidate"
    );
}

#[test]
fn legacy_fi03_certainty_adapter_keeps_policy_evaluation_separate() {
    use ren_protocol::project_model::{CertaintyState, EvidenceType};
    assert_eq!(
        EvidenceType::Measurement.legacy_certainty(),
        Some(CertaintyState::Measured)
    );
    assert_eq!(
        EvidenceType::Inference.legacy_certainty(),
        Some(CertaintyState::Inferred)
    );
    assert_eq!(
        EvidenceType::CreativeJudgment.legacy_certainty(),
        Some(CertaintyState::Interpreted)
    );
    assert_eq!(EvidenceType::Evaluation.legacy_certainty(), None);
}

#[test]
fn ambiguous_identity_and_relationship_candidates_do_not_become_verified() {
    let mut model: serde_json::Value =
        serde_json::from_str(include_str!("../fixtures/project-model.json")).unwrap();
    model["identity_links"][0]["status"] = serde_json::json!("candidate");
    model["identity_links"][0]["verification_status"] = serde_json::json!("candidate");
    model["identity_links"][0]["contradicting_observation_ids"] = serde_json::json!(["obs-1"]);
    model["relations"][0]["status"] = serde_json::json!("candidate");
    let parsed =
        decode_project_model(&model.to_string()).expect("candidates remain valid hypotheses");
    assert_eq!(
        parsed.identity_links[0].status,
        ren_protocol::project_model::IdentityStatus::Candidate
    );
    assert_eq!(
        parsed.relations[0].status,
        Some(ren_protocol::project_model::VerificationStatus::Candidate)
    );
    model["identity_links"][0]["verification_status"] = serde_json::json!("retracted");
    assert_eq!(
        decode_project_model(&model.to_string())
            .unwrap()
            .identity_links[0]
            .verification_status,
        Some(ren_protocol::project_model::VerificationStatus::Retracted)
    );
}

#[test]
fn contract_less_project_model_files_still_decode() {
    // Acceptance: a file written before the FI-03R contract (no new
    // collections, no additive evidence fields) decodes and validates;
    // every FI-03R field is optional, so absence is never a defect.
    let mut model: serde_json::Value =
        serde_json::from_str(include_str!("../fixtures/project-model.json")).unwrap();
    for key in [
        "signals",
        "events",
        "interpretations",
        "coordinate_spaces",
        "time_transforms",
        "space_transforms",
        "query_results",
    ] {
        model.as_object_mut().unwrap().remove(key);
    }
    let object = model.as_object_mut().unwrap();
    for stream in object["streams"].as_array_mut().unwrap() {
        for key in [
            "source_digest",
            "provenance_id",
            "coverage",
            "producer_version",
            "freshness",
        ] {
            stream.as_object_mut().unwrap().remove(key);
        }
    }
    for observation in object["observations"].as_array_mut().unwrap() {
        let observation = observation.as_object_mut().unwrap();
        observation.remove("certainty");
        observation.remove("confidence_detail");
        observation.remove("evidence");
    }
    for link in object["identity_links"].as_array_mut().unwrap() {
        let link = link.as_object_mut().unwrap();
        for key in [
            "supporting_observation_ids",
            "contradicting_observation_ids",
            "scope",
            "verification_status",
            "confidence_detail",
            "method_version",
            "uncertainty",
        ] {
            link.remove(key);
        }
    }
    for track in object["tracks"].as_array_mut().unwrap() {
        let track = track.as_object_mut().unwrap();
        for key in ["kind", "continuity", "coverage"] {
            track.remove(key);
        }
        for sample in track["samples"].as_array_mut().unwrap() {
            let sample = sample.as_object_mut().unwrap();
            for key in [
                "interval",
                "evidence_ids",
                "coordinate_space_id",
                "space_transform_id",
                "confidence_detail",
            ] {
                sample.remove(key);
            }
        }
    }
    for relation in object["relations"].as_array_mut().unwrap() {
        let relation = relation.as_object_mut().unwrap();
        for key in ["evidence_ids", "status", "certainty"] {
            relation.remove(key);
        }
    }
    let text = serde_json::to_string(&model).unwrap();
    let decoded = decode_project_model(&text).expect("old project-model files still load");
    assert!(decoded.signals.is_empty());
    assert!(decoded.events.is_empty());
    assert!(decoded.query_results.is_empty());
    let encoded = serde_json::to_string(&decoded).unwrap();
    decode_project_model(&encoded).expect("stripped model round-trips");
}

#[test]
fn verified_events_without_evidence_are_refused() {
    let mut model: serde_json::Value =
        serde_json::from_str(include_str!("../fixtures/project-model.json")).unwrap();
    model["events"] = serde_json::json!([{
        "id":"event-unproven", "interval":{"clock_id":"clock-source","start":1,"end":10},
        "kind":"picking_up", "participant_ids":[], "status":"verified",
        "certainty":"inferred", "evidence_ids":[], "confidence":null
    }]);
    assert!(decode_project_model(&model.to_string()).is_err());
}

#[test]
fn audio_and_video_stream_clocks_remain_independent_without_a_sync_transform() {
    let mut model: serde_json::Value =
        serde_json::from_str(include_str!("../fixtures/project-model.json")).unwrap();
    model["clocks"].as_array_mut().unwrap().push(serde_json::json!({
        "id":"clock-audio", "domain":{"kind":"source","asset_id":"asset-source","stream_id":"stream-audio"},
        "ticks_per_second":48000,"frame_rate":null
    }));
    model["streams"].as_array_mut().unwrap().push(serde_json::json!({
        "id":"stream-audio","asset_id":"asset-source","stream_index":1,"clock_id":"clock-audio","metadata":{"codec":"pcm"}
    }));
    let parsed =
        decode_project_model(&model.to_string()).expect("independent stream clocks are valid");
    assert_ne!(parsed.streams[0].clock_id, parsed.streams[1].clock_id);
    assert!(!parsed
        .time_transforms
        .iter()
        .any(|t| (t.from_clock == parsed.streams[0].clock_id
            && t.to_clock == parsed.streams[1].clock_id)
            || (t.from_clock == parsed.streams[1].clock_id
                && t.to_clock == parsed.streams[0].clock_id)));
}
