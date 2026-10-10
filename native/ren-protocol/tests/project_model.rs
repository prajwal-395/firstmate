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
