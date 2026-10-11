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
        "request-matte.json",
        "request-move-drift.json",
        "request-move-punch.json",
        "request-move-shake.json",
        "request-move-cut.json",
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
    let matte = read_request("request-matte.json");
    assert!(matches!(
        &matte.operations[6],
        Operation::SetTrackMatte { layer, matte, kind }
        if layer == "checker.bmp"
            && matte == "mask-checker_matte_[00000-00001].png"
            && *kind == ren_effectcraft::TrackMatteKind::Alpha
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
    // Unit 7c: moves project as transform keys, never engine effects.
    // The drift opens at neutral scale and closes 4% in; the punch
    // peaks mid-window and settles back; the shake carries only
    // position keys; the cut holds one constant reframe.
    let drift = read_request("request-move-drift.json");
    let scales: Vec<(f64, f64)> = drift
        .operations
        .iter()
        .filter_map(|op| match op {
            Operation::Keyframe {
                path, time, value, ..
            } if path == "transform/scale" => Some((*time, value[0].as_f64().unwrap())),
            _ => None,
        })
        .collect();
    assert_eq!(scales.len(), 5);
    assert!((scales[0].1 - 100.0).abs() < 1e-9);
    assert!((scales[4].1 - 104.0).abs() < 1e-9);
    assert!(
        drift
            .operations
            .iter()
            .all(|op| !matches!(op, Operation::ApplyEffect { .. }))
    );
    let punch = read_request("request-move-punch.json");
    let peaks: Vec<f64> = punch
        .operations
        .iter()
        .filter_map(|op| match op {
            Operation::Keyframe { path, value, .. } if path == "transform/scale" => {
                Some(value[0].as_f64().unwrap())
            }
            _ => None,
        })
        .collect();
    assert_eq!(peaks.len(), 5);
    assert!((peaks[1] - 108.0).abs() < 1e-9);
    assert!((peaks[3] - 100.0).abs() < 1e-9);
    let shake = read_request("request-move-shake.json");
    assert!(shake.operations.iter().all(|op| !matches!(
        op,
        Operation::Keyframe { path, .. }
        if path == "transform/scale"
    )));
    assert_eq!(
        shake
            .operations
            .iter()
            .filter(|op| matches!(
                op,
                Operation::Keyframe { path, .. }
                if path == "transform/position"
            ))
            .count(),
        5
    );
    let cut = read_request("request-move-cut.json");
    let held: Vec<f64> = cut
        .operations
        .iter()
        .filter_map(|op| match op {
            Operation::Keyframe { path, value, .. } if path == "transform/scale" => {
                Some(value[0].as_f64().unwrap())
            }
            _ => None,
        })
        .collect();
    assert!(held.contains(&106.0));
    assert!(held.contains(&100.0));
}

#[test]
fn checked_in_requests_default_to_cpu() {
    // The adapter never sent `--gpu` before the GPU wiring, so the engine
    // default (CPU) always applied; the new preference parses as `Cpu` on
    // old projections, preserving that behavior, with `Auto` opt-in.
    for name in [
        "request-masked.json",
        "request-effect.json",
        "request-title.json",
        "request-matte.json",
        "request-move-drift.json",
        "request-move-punch.json",
        "request-move-shake.json",
        "request-move-cut.json",
    ] {
        let request = read_request(name);
        for render in &request.renders {
            assert_eq!(
                render.gpu,
                ren_effectcraft::GpuPreference::Cpu,
                "{name} renders without a GPU preference parse as Cpu"
            );
        }
    }
}

#[test]
fn projected_operations_validate_against_the_pinned_registry() {
    let driver = Driver::resolve().expect("the pinned EffectCraft CLI is buildable");
    for name in [
        "request-masked.json",
        "request-effect.json",
        "request-title.json",
        "request-matte.json",
        "request-move-drift.json",
        "request-move-punch.json",
        "request-move-shake.json",
        "request-move-cut.json",
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
