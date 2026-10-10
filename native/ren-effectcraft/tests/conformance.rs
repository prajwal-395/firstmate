//! Unit 7: the Project Model adapter's projections speak this crate's contract.
//!
//! The checked-in conformance requests (built by
//! `library/tools/project_model_effectcraft.py`) must deserialize into the
//! driver's versioned `Request` - including its `deny_unknown_fields`
//! gates - and every operation in them must pass `Driver::validate_operation`
//! against the pinned registry. The unknown-effect request must be refused
//! before any engine execution. Rendering the masked request must reproduce
//! the agreed conformance pixels (transparent corners, visible center).

use ren_effectcraft::{Driver, Operation, Request};
use std::path::PathBuf;

fn fixture(name: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/effectcraft_fframes_conformance")
        .join(name)
}

fn read_request(name: &str) -> Request {
    let bytes = std::fs::read(fixture(name)).expect("conformance request fixture exists");
    serde_json::from_slice(&bytes).expect("conformance request speaks the driver contract")
}

#[test]
fn projected_requests_parse_as_versioned_driver_requests() {
    for name in [
        "request-masked.json",
        "request-effect.json",
        "request-title.json",
    ] {
        let request = read_request(name);
        assert_eq!(request.version, ren_effectcraft::REQUEST_VERSION);
        assert!(!request.project_id.trim().is_empty());
        assert!(
            !request.operations.is_empty(),
            "{name} must carry operations"
        );
    }
    let masked = read_request("request-masked.json");
    assert!(matches!(
        &masked.operations[3],
        Operation::AddMask { layer, shape, rect }
        if layer == "checker.bmp" && shape == "ellipse"
            && rect == &[4.0, 4.0, 24.0, 24.0]
    ));
    let effect = read_request("request-effect.json");
    assert!(matches!(
        effect.operations.iter().find_map(|op| match op {
            Operation::ApplyEffect { effect_id, params, .. } => Some((effect_id, params)),
            _ => None,
        }),
        Some((id, params))
        if id == "ec.blur.gaussian"
            && params.get("blurriness").and_then(|v| v.as_f64()) == Some(6.0)
    ));
}

#[test]
fn projected_operations_validate_against_the_pinned_registry() {
    let driver = Driver::resolve().expect("the pinned EffectCraft CLI is buildable");
    for name in [
        "request-masked.json",
        "request-effect.json",
        "request-title.json",
    ] {
        let request = read_request(name);
        for op in &request.operations {
            driver
                .validate_operation(op)
                .unwrap_or_else(|e| panic!("{name} operation refused: {e}"));
        }
    }
}

#[test]
fn unknown_effect_fixture_is_refused_before_engine_execution() {
    let driver = Driver::resolve().expect("the pinned EffectCraft CLI is buildable");
    let request = read_request("request-unknown-effect.json");
    let refused = request
        .operations
        .iter()
        .any(|op| driver.validate_operation(op).is_err());
    assert!(refused, "the unknown effect must be refused");
}

#[test]
fn masked_conformance_request_renders_the_agreed_pixels() {
    let driver = Driver::resolve().expect("the pinned EffectCraft CLI is buildable");
    let temp = tempfile::tempdir().unwrap();
    let mut request = read_request("request-masked.json");
    // Fixture media lives beside the driver's own fixtures; point the
    // projection at it without editing the checked-in request.
    let checker = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/checker.bmp");
    for op in &mut request.operations {
        if let Operation::ImportFootage { path } = op {
            *path = checker.clone();
        }
    }
    for render in &mut request.renders {
        render.output = temp.path().join("masked_#####.png");
    }
    let receipt = driver.execute(&request, temp.path()).unwrap();
    assert_eq!(receipt.renders[0].frames, Some(2));
    assert!(receipt.renders[0].has_alpha);
    let rendered = image::open(temp.path().join("masked_00000.png"))
        .unwrap()
        .to_rgba8();
    assert_eq!(
        rendered.get_pixel(0, 0)[3],
        0,
        "ellipse mask clears pixels outside the footage area"
    );
    assert!(
        rendered.get_pixel(16, 16)[3] > 0,
        "masked footage remains visible at the center"
    );
}
