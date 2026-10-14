//! VI-02 model-level acceptance: the validated transform graph behind the
//! Project Model's clocks and coordinate spaces.
//!
//! The shared fixture's own time/space transforms must validate; overlapping
//! segments, dangling endpoints and duplicate ids must refuse decode; and a
//! nontrivial source PTS -> presentation -> timeline chain must map with
//! propagated uncertainty while an ambiguous join refuses.

use ren_protocol::project_model::ProjectModelDecodeError;
use ren_protocol::{decode_project_model, transform_graph};

fn fixture() -> serde_json::Value {
    serde_json::from_str(include_str!("../fixtures/project-model.json")).unwrap()
}

fn decode(value: &serde_json::Value) -> Result<(), ProjectModelDecodeError> {
    decode_project_model(&value.to_string()).map(|_| ())
}

#[test]
fn shared_fixture_graph_validates() {
    decode(&fixture()).expect("fixture time/space transforms form a valid graph");
}

#[test]
fn self_join_transform_matches_the_evidence_contract() {
    // FI-03R holds a clock-source -> clock-source transform as valid; the
    // graph validator must accept what the contract holds.
    let mut model = fixture();
    model["time_transforms"][0]["from_clock"] = serde_json::json!("clock-source");
    model["time_transforms"][0]["to_clock"] = serde_json::json!("clock-source");
    model["time_transforms"][0]["segments"][0]["to"]["clock_id"] =
        serde_json::json!("clock-source");
    decode(&model).expect("self-join transform is well-formed");
}

#[test]
fn overlapping_time_segments_refuse() {
    let mut model = fixture();
    let mut extra = model["time_transforms"][0]["segments"][0].clone();
    extra["from"]["start"] = serde_json::json!(12);
    extra["from"]["end"] = serde_json::json!(30);
    extra["to"]["start"] = serde_json::json!(12);
    extra["to"]["end"] = serde_json::json!(30);
    model["time_transforms"][0]["segments"]
        .as_array_mut()
        .unwrap()
        .push(extra);
    assert!(matches!(
        decode(&model),
        Err(ProjectModelDecodeError::InvalidModel(_))
    ));
}

#[test]
fn dangling_clock_and_space_endpoints_refuse() {
    let mut model = fixture();
    model["time_transforms"][0]["to_clock"] = serde_json::json!("clock-missing");
    assert!(matches!(
        decode(&model),
        Err(ProjectModelDecodeError::InvalidModel(_))
    ));
    let mut model = fixture();
    model["space_transforms"][0]["to_space"] = serde_json::json!("space-missing");
    assert!(matches!(
        decode(&model),
        Err(ProjectModelDecodeError::InvalidModel(_))
    ));
}

#[test]
fn duplicate_transform_ids_refuse() {
    let mut model = fixture();
    let dupe = model["time_transforms"][0].clone();
    model["time_transforms"].as_array_mut().unwrap().push(dupe);
    assert!(matches!(
        decode(&model),
        Err(ProjectModelDecodeError::InvalidModel(_))
    ));
}

#[test]
fn nontrivial_chain_maps_and_ambiguous_join_refuses() {
    let model = decode_project_model(&fixture().to_string()).unwrap();
    let mapped =
        transform_graph::map_time(&model.time_transforms, "clock-source", "clock-timeline", 7)
            .expect("fixture chain covers the tick");
    assert_eq!(mapped.tick, 7);
    assert_eq!(mapped.uncertainty_ticks, 1);
    assert_eq!(mapped.path, vec!["source-presentation"]);
    // A second direct edge makes the join ambiguous, which must refuse
    // rather than silently pick a path.
    let mut two = model.time_transforms.clone();
    let mut alt = two[0].clone();
    alt.id = "source-presentation-alt".into();
    two.push(alt);
    assert_eq!(
        transform_graph::map_time(&two, "clock-source", "clock-timeline", 7),
        Err(transform_graph::TransformError::AmbiguousPath)
    );
}
