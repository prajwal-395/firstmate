use ren_effectcraft::{Driver, Operation, RenderFormat, RenderRequest, Request};
use serde_json::json;
use std::collections::BTreeMap;
use std::path::PathBuf;

fn driver() -> Driver {
    Driver::resolve().expect("the pinned EffectCraft CLI is buildable")
}

fn title_request(project: &str) -> Request {
    let mut request = Request::new(project);
    request.operations = vec![
        Operation::NewComp {
            name: "Main".into(),
            width: 96,
            height: 64,
            frame_rate: 10.0,
            duration: 0.5,
        },
        Operation::AddText {
            name: "Title".into(),
            text: "REN".into(),
            size: 28.0,
            fill: "#ffffff".into(),
            position: [48.0, 32.0],
            font: Some("Inter".into()),
        },
        Operation::Keyframe {
            layer: "Title".into(),
            path: "transform/opacity".into(),
            time: 0.0,
            value: json!(0.0),
        },
        Operation::Keyframe {
            layer: "Title".into(),
            path: "transform/opacity".into(),
            time: 0.2,
            value: json!(100.0),
        },
    ];
    request
}

#[test]
fn animated_title_exports_rgba_png_sequence_and_prores_4444_alpha() {
    let driver = driver();
    let temp = tempfile::tempdir().unwrap();
    let mut request = title_request("animated-alpha-title");
    request.renders = vec![
        RenderRequest {
            format: RenderFormat::RgbaPngSequence,
            comp: "Main".into(),
            output: temp.path().join("title_#####.png"),
            start_seconds: Some(0.0),
            end_seconds: Some(0.5),
        },
        RenderRequest {
            format: RenderFormat::Prores4444Alpha,
            comp: "Main".into(),
            output: temp.path().join("title.mov"),
            start_seconds: Some(0.0),
            end_seconds: Some(0.5),
        },
    ];
    let receipt = driver.execute(&request, temp.path()).unwrap();
    assert_eq!(
        receipt.upstream_revision,
        "813c7c4650f0d4c805da620c8d5837c9600ad2e3"
    );
    assert_eq!(receipt.renders[0].width, 96);
    assert_eq!(receipt.renders[0].height, 64);
    assert_eq!(receipt.renders[0].frames, Some(5));
    assert!(receipt.renders[0].has_alpha);
    assert!(
        receipt.renders[1].has_alpha,
        "ProRes output must have an alpha pixel format"
    );
    let decoded = std::process::Command::new("ffmpeg")
        .args(["-v", "error", "-i"])
        .arg(temp.path().join("title.mov"))
        .args(["-f", "rawvideo", "-pix_fmt", "rgba", "-"])
        .output()
        .unwrap();
    assert!(decoded.status.success());
    let alpha_samples: Vec<_> = decoded.stdout.chunks_exact(4).map(|rgba| rgba[3]).collect();
    assert!(
        alpha_samples.contains(&0),
        "transparent pixels survive ProRes encoding"
    );
    assert!(
        alpha_samples.iter().any(|alpha| *alpha > 0),
        "visible title pixels survive ProRes encoding"
    );
    let first = image::open(temp.path().join("title_00000.png"))
        .unwrap()
        .to_rgba8();
    let last = image::open(temp.path().join("title_00004.png"))
        .unwrap()
        .to_rgba8();
    assert_eq!(
        first.pixels().map(|p| p[3] as u64).sum::<u64>(),
        0,
        "title is transparent before its fade-in"
    );
    assert!(
        last.pixels().any(|p| p[3] > 0),
        "animated title becomes visible"
    );
}

#[test]
fn imported_footage_accepts_mask_and_blur() {
    let driver = driver();
    let temp = tempfile::tempdir().unwrap();
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/checker.bmp");
    let mut request = Request::new("masked-footage-blur");
    request.operations = vec![
        Operation::NewComp {
            name: "Main".into(),
            width: 32,
            height: 32,
            frame_rate: 10.0,
            duration: 0.2,
        },
        Operation::ImportFootage { path: fixture },
        Operation::AddFootageLayer {
            footage: "checker.bmp".into(),
        },
        Operation::AddMask {
            layer: "checker.bmp".into(),
            shape: "ellipse".into(),
            rect: [4.0, 4.0, 24.0, 24.0],
        },
        Operation::ApplyEffect {
            layer: "checker.bmp".into(),
            effect_id: "ec.blur.gaussian".into(),
            params: BTreeMap::from([("blurriness".into(), json!(6.0))]),
        },
    ];
    request.renders.push(RenderRequest {
        format: RenderFormat::RgbaPngSequence,
        comp: "Main".into(),
        output: temp.path().join("masked_#####.png"),
        start_seconds: Some(0.0),
        end_seconds: Some(0.2),
    });
    let receipt = driver.execute(&request, temp.path()).unwrap();
    assert!(receipt.project_file.is_file());
    assert_eq!(receipt.renders[0].frames, Some(2));
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

#[test]
fn unknown_effect_and_parameter_are_refused_before_engine_execution() {
    let driver = driver();
    let effect = Operation::ApplyEffect {
        layer: "Title".into(),
        effect_id: "ren.effect.does-not-exist".into(),
        params: BTreeMap::new(),
    };
    assert!(matches!(
        driver.validate_operation(&effect),
        Err(ren_effectcraft::Error::UnknownEffect(_))
    ));
    let gaussian = driver
        .effects()
        .find(|e| e.id == "ec.blur.gaussian")
        .unwrap();
    let parameter = gaussian
        .parameters
        .values()
        .next()
        .expect("registry provides parameter identifiers");
    let bad = Operation::ApplyEffect {
        layer: "Title".into(),
        effect_id: gaussian.id.clone(),
        params: BTreeMap::from([("not-a-real-parameter".into(), json!(1.0))]),
    };
    assert!(matches!(
        driver.validate_operation(&bad),
        Err(ren_effectcraft::Error::UnknownParameter { .. })
    ));
    let wrong_value = match parameter.value_kind {
        "number" => json!("wrong type"),
        "string" => json!(1.0),
        "boolean" => json!(1.0),
        "array" => json!(1.0),
        "object" => json!(1.0),
        _ => json!(1.0),
    };
    let wrong_type = Operation::ApplyEffect {
        layer: "Title".into(),
        effect_id: gaussian.id.clone(),
        params: BTreeMap::from([(parameter.id.clone(), wrong_value)]),
    };
    assert!(matches!(
        driver.validate_operation(&wrong_type),
        Err(ren_effectcraft::Error::ParameterType { .. })
    ));
}
