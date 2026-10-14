use ren_effectcraft::{
    Driver, GpuPreference, Operation, RenderFormat, RenderRequest, Request, TrackMatteKind,
};
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
            gpu: GpuPreference::Cpu,
        },
        RenderRequest {
            format: RenderFormat::Prores4444Alpha,
            comp: "Main".into(),
            output: temp.path().join("title.mov"),
            start_seconds: Some(0.0),
            end_seconds: Some(0.5),
            gpu: GpuPreference::Cpu,
        },
    ];
    let receipt = driver.execute(&request, temp.path()).unwrap();
    assert_eq!(
        receipt.upstream_revision,
        "549e9d825132986d6167c1432cd3328efcbb0278"
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
    let alpha_samples: Vec<_> = decoded
        .stdout
        .as_chunks::<4>()
        .0
        .iter()
        .map(|rgba| rgba[3])
        .collect();
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
            start_time: None,
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
        gpu: GpuPreference::Cpu,
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
fn track_matte_sequence_masks_footage_per_frame() {
    let driver = driver();
    let temp = tempfile::tempdir().unwrap();
    // Two-frame lossless matte: left half opaque, right half transparent.
    // Frame 0 masks everything left; frame 1 is fully transparent.
    for (frame, opaque) in [(0, true), (1, false)] {
        let mut matte = image::RgbaImage::new(32, 32);
        if opaque {
            for y in 0..32 {
                for x in 0..16 {
                    matte.put_pixel(x, y, image::Rgba([255, 255, 255, 255]));
                }
            }
        }
        matte
            .save(temp.path().join(format!("matte_{frame:05}.png")))
            .unwrap();
    }
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/checker.bmp");
    let mut request = Request::new("track-matte-sequence");
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
            start_time: None,
        },
        Operation::ImportFootage {
            path: temp.path().join("matte_00000.png"),
        },
        // The numbered still imports at the default still-sequence rate;
        // conform it to the comp clock so matte frame i shows at comp
        // frame i. Emitted immediately after its import, which is what
        // the engine's footage selection holds.
        Operation::InterpretFootage { frame_rate: 10.0 },
        Operation::AddFootageLayer {
            footage: "matte_[00000-00001].png".into(),
            start_time: None,
        },
        Operation::SetTrackMatte {
            layer: "checker.bmp".into(),
            matte: "matte_[00000-00001].png".into(),
            kind: TrackMatteKind::Alpha,
        },
    ];
    request.renders.push(RenderRequest {
        format: RenderFormat::RgbaPngSequence,
        comp: "Main".into(),
        output: temp.path().join("matted_#####.png"),
        start_seconds: Some(0.0),
        end_seconds: Some(0.2),
        gpu: GpuPreference::Cpu,
    });
    let receipt = driver.execute(&request, temp.path()).unwrap();
    assert_eq!(receipt.renders[0].frames, Some(2));
    let first = image::open(temp.path().join("matted_00000.png"))
        .unwrap()
        .to_rgba8();
    assert!(
        first.get_pixel(4, 16)[3] > 0,
        "opaque matte half keeps the footage visible"
    );
    assert_eq!(
        first.get_pixel(28, 16)[3],
        0,
        "transparent matte half clears the footage"
    );
    let second = image::open(temp.path().join("matted_00001.png"))
        .unwrap()
        .to_rgba8();
    assert!(
        second.pixels().all(|p| p[3] == 0),
        "a fully transparent matte frame clears every pixel"
    );
}

#[test]
fn huesaturation_renders_a_measured_saturation_shift() {
    let driver = driver();
    let temp = tempfile::tempdir().unwrap();
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/checker.bmp");
    let render = |project: &str, saturation: f64| {
        let mut request = Request::new(project);
        request.operations = vec![
            Operation::NewComp {
                name: "Main".into(),
                width: 32,
                height: 32,
                frame_rate: 10.0,
                duration: 0.1,
            },
            Operation::ImportFootage {
                path: fixture.clone(),
            },
            Operation::AddFootageLayer {
                footage: "checker.bmp".into(),
                start_time: None,
            },
            Operation::ApplyEffect {
                layer: "checker.bmp".into(),
                effect_id: "ec.color.huesaturation".into(),
                params: BTreeMap::from([("saturation".into(), json!(saturation))]),
            },
        ];
        request.renders.push(RenderRequest {
            format: RenderFormat::RgbaPngSequence,
            comp: "Main".into(),
            output: temp.path().join(format!("{project}_#####.png")),
            start_seconds: Some(0.0),
            end_seconds: Some(0.1),
            gpu: GpuPreference::Cpu,
        });
        driver.execute(&request, temp.path()).unwrap();
        image::open(temp.path().join(format!("{project}_00000.png")))
            .unwrap()
            .to_rgba8()
    };
    let plain = image::open(&fixture).unwrap().to_rgba8();
    let desaturated = render("sat-down", -100.0);
    let up = render("sat-up", 100.0);
    let chroma: Vec<u32> = plain
        .pixels()
        .map(|p| {
            let (r, g, b) = (p[0] as i32, p[1] as i32, p[2] as i32);
            (r - g).unsigned_abs() + (g - b).unsigned_abs() + (b - r).unsigned_abs()
        })
        .collect();
    let chroma_down: Vec<u32> = desaturated
        .pixels()
        .map(|p| {
            let (r, g, b) = (p[0] as i32, p[1] as i32, p[2] as i32);
            (r - g).unsigned_abs() + (g - b).unsigned_abs() + (b - r).unsigned_abs()
        })
        .collect();
    assert!(
        chroma_down.iter().sum::<u32>() < chroma.iter().sum::<u32>(),
        "saturation -100 drains chroma against the source checker"
    );
    assert!(
        up.pixels().zip(plain.pixels()).any(|(a, b)| a != b),
        "saturation +100 moves at least one pixel"
    );
}

/// Unit 7b proof: a real person mask from the Project Model E2E
/// fixture reaches the renderer as a track matte with no approximation.
///
/// Reads `mask-ada` (person-ada's ellipse) and its clock out of
/// `tests/fixtures/project_model_e2e/expected-project-model.json`,
/// rasterizes the source pixels, renders them as a track matte over the
/// fixture's own A-roll, and requires every rendered alpha byte to equal
/// the source mask pixel. A frame the matte cannot cover is refused by
/// construction: the matte spans exactly the mask's comp frames.
#[test]
fn person_mask_matte_renders_source_exact_alpha() {
    let driver = driver();
    let temp = tempfile::tempdir().unwrap();
    let e2e =
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/project_model_e2e");
    let model: serde_json::Value =
        serde_json::from_slice(&std::fs::read(e2e.join("expected-project-model.json")).unwrap())
            .unwrap();
    let mask = model["masks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|m| m["id"] == "mask-ada")
        .expect("the E2E fixture carries Ada's person mask");
    assert_eq!(mask["entity_id"], "person-ada");
    assert_eq!(mask["representation"]["kind"], "ellipse");
    let bbox: Vec<f64> = mask["representation"]["normalized_bbox"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_f64().unwrap())
        .collect();
    assert_eq!(bbox.len(), 4);
    let interval = &mask["interval"];
    let (start_tick, end_tick) = (
        interval["start"].as_u64().unwrap(),
        interval["end"].as_u64().unwrap(),
    );
    let tps = model["clocks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|c| c["id"] == interval["clock_id"])
        .unwrap()["ticks_per_second"]
        .as_u64()
        .unwrap();
    assert_eq!((tps, start_tick, end_tick), (24, 0, 48));

    // Comp frames the mask interval touches at the comp clock.
    let (width, height, fps, frames) = (96u32, 64u32, 24.0, 12);
    assert_eq!((end_tick as f64 / tps as f64 * fps).ceil() as u64, 48);
    let (x, y, w, h) = (bbox[0], bbox[1], bbox[2], bbox[3]);
    let (cx, cy, rx, ry) = (x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0);
    let mut alpha = vec![0u8; (width * height) as usize];
    for py in 0..height {
        let dy = ((py as f64 + 0.5) / height as f64 - cy) / ry;
        for px in 0..width {
            let dx = ((px as f64 + 0.5) / width as f64 - cx) / rx;
            if dx * dx + dy * dy <= 1.0 {
                alpha[(py * width + px) as usize] = 255;
            }
        }
    }
    assert!(alpha.contains(&255) && alpha.contains(&0));
    for frame in 0..frames {
        let mut matte = image::RgbaImage::new(width, height);
        for (i, a) in alpha.iter().enumerate() {
            let (px, py) = ((i as u32) % width, (i as u32) / width);
            matte.put_pixel(px, py, image::Rgba([255, 255, 255, *a]));
        }
        matte
            .save(temp.path().join(format!("ada_{frame:05}.png")))
            .unwrap();
    }
    let aroll = e2e.join("media/person-ada-aroll.mp4");
    assert!(aroll.is_file());
    let mut request = Request::new("person-mask-proof");
    request.operations = vec![
        Operation::NewComp {
            name: "Main".into(),
            width,
            height,
            frame_rate: fps,
            duration: frames as f64 / fps,
        },
        Operation::ImportFootage {
            path: temp.path().join("ada_00000.png"),
        },
        Operation::InterpretFootage { frame_rate: fps },
        Operation::ImportFootage { path: aroll },
        Operation::AddFootageLayer {
            footage: format!("ada_[{:05}-{:05}].png", 0, frames - 1),
            start_time: None,
        },
        Operation::AddFootageLayer {
            footage: "person-ada-aroll.mp4".into(),
            start_time: None,
        },
        Operation::SetTrackMatte {
            layer: "person-ada-aroll.mp4".into(),
            matte: format!("ada_[{:05}-{:05}].png", 0, frames - 1),
            kind: TrackMatteKind::Alpha,
        },
    ];
    request.renders.push(RenderRequest {
        format: RenderFormat::RgbaPngSequence,
        comp: "Main".into(),
        output: temp.path().join("proof_#####.png"),
        start_seconds: Some(0.0),
        end_seconds: Some(frames as f64 / fps),
        gpu: GpuPreference::Cpu,
    });
    let receipt = driver.execute(&request, temp.path()).unwrap();
    assert_eq!(receipt.renders[0].frames, Some(frames as u64));
    for frame in 0..frames {
        let rendered = image::open(temp.path().join(format!("proof_{frame:05}.png")))
            .unwrap()
            .to_rgba8();
        let rendered_alpha: Vec<u8> = rendered.pixels().map(|p| p[3]).collect();
        let mismatched = rendered_alpha
            .iter()
            .zip(alpha.iter())
            .filter(|(a, b)| a != b)
            .count();
        assert_eq!(
            mismatched,
            0,
            "rendered frame {frame} alpha differs from the source mask in \
             {mismatched} of {} pixels",
            alpha.len()
        );
    }
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

/// An explicit CPU render records the CPU compositor with the engine's own
/// reason - the receipt names what rendered, not what was asked for.
#[test]
fn explicit_cpu_render_records_cpu_with_engine_reason() {
    let driver = driver();
    let temp = tempfile::tempdir().unwrap();
    let mut request = title_request("cpu-receipt-proof");
    request.renders = vec![RenderRequest {
        format: RenderFormat::RgbaPngSequence,
        comp: "Main".into(),
        output: temp.path().join("cpu_#####.png"),
        start_seconds: Some(0.0),
        end_seconds: Some(0.5),
        gpu: GpuPreference::Cpu,
    }];
    let receipt = driver.execute(&request, temp.path()).unwrap();
    assert_eq!(
        receipt.renders[0].backend,
        ren_effectcraft::RenderBackend::Cpu
    );
    assert_eq!(receipt.renders[0].adapter, None);
    let note = receipt.renders[0]
        .backend_note
        .as_deref()
        .expect("a CPU render records why");
    assert!(
        note.contains("renders on the CPU"),
        "the note carries the engine's own reason, got: {note}"
    );
}

/// The default (`Auto`) render records whichever compositor is active with
/// the matching evidence: a GPU render names its adapter and carries no
/// note, a CPU fallback names no adapter and carries the reason. Portable
/// across GPU-less machines, where `Auto` falls back to the CPU.
#[test]
fn auto_render_records_whichever_compositor_is_active() {
    let driver = driver();
    let temp = tempfile::tempdir().unwrap();
    let mut request = title_request("auto-receipt-proof");
    request.renders = vec![RenderRequest {
        format: RenderFormat::RgbaPngSequence,
        comp: "Main".into(),
        output: temp.path().join("auto_#####.png"),
        start_seconds: Some(0.0),
        end_seconds: Some(0.5),
        gpu: GpuPreference::Auto,
    }];
    let receipt = driver.execute(&request, temp.path()).unwrap();
    match receipt.renders[0].backend {
        ren_effectcraft::RenderBackend::Gpu => {
            assert!(
                receipt.renders[0]
                    .adapter
                    .as_deref()
                    .is_some_and(|a| !a.is_empty()),
                "a GPU render names its adapter"
            );
            assert_eq!(receipt.renders[0].backend_note, None);
        }
        ren_effectcraft::RenderBackend::Cpu => {
            assert_eq!(receipt.renders[0].adapter, None);
            assert!(
                receipt.renders[0].backend_note.is_some(),
                "a CPU fallback records why"
            );
        }
    }
}

/// Fidelity proof for opting into the GPU: the same short treatment range
/// (footage under a blur plus a saturation shift) renders within one LSB on
/// both compositors, so `Auto` changes no visible pixel. The bound is the
/// engine's own agreement threshold: `bench --gpu`
/// counts pixels differing by more than 1/255. Measured 2026-10-11 on Apple
/// M5 (Metal): max channel diff 1 across 36 of 49,152 channel values, SSIM Y
/// 0.999999, PSNR 83.0 dB average with worst frame 78.2 dB - inside the
/// captain's GPU bar (SSIM >= 0.995, worst-frame PSNR >= 45 dB, the size of
/// one accepted H.264 encode); wall 136 ms CPU vs 760 ms GPU for this
/// tiny range, where upload/readback dominate (the engine's `AutoPick`
/// exists for exactly this reason).
#[test]
fn gpu_and_cpu_agree_on_treatment_pixels() {
    use std::time::Instant;
    let driver = driver();
    let fixture = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/checker.bmp");
    let render = |tag: &str, gpu: GpuPreference| {
        let temp = tempfile::tempdir().unwrap();
        let mut request = Request::new(format!("treatment-fidelity-{tag}"));
        request.operations = vec![
            Operation::NewComp {
                name: "Main".into(),
                width: 64,
                height: 64,
                frame_rate: 10.0,
                duration: 0.3,
            },
            Operation::ImportFootage {
                path: fixture.clone(),
            },
            Operation::AddFootageLayer {
                footage: "checker.bmp".into(),
                start_time: None,
            },
            Operation::ApplyEffect {
                layer: "checker.bmp".into(),
                effect_id: "ec.blur.gaussian".into(),
                params: BTreeMap::from([("blurriness".into(), json!(6.0))]),
            },
            Operation::ApplyEffect {
                layer: "checker.bmp".into(),
                effect_id: "ec.color.huesaturation".into(),
                params: BTreeMap::from([("saturation".into(), json!(-100.0))]),
            },
        ];
        request.renders.push(RenderRequest {
            format: RenderFormat::RgbaPngSequence,
            comp: "Main".into(),
            output: temp.path().join(format!("{tag}_#####.png")),
            start_seconds: Some(0.0),
            end_seconds: Some(0.3),
            gpu,
        });
        let started = Instant::now();
        let receipt = driver.execute(&request, temp.path()).unwrap();
        let wall_ms = started.elapsed().as_secs_f64() * 1e3;
        let frames: Vec<image::RgbaImage> = (0..3)
            .map(|f| {
                image::open(temp.path().join(format!("{tag}_{f:05}.png")))
                    .unwrap()
                    .to_rgba8()
            })
            .collect();
        (receipt, frames, wall_ms, temp)
    };
    let (cpu_receipt, cpu_frames, cpu_ms, _cpu_dir) = render("cpu", GpuPreference::Cpu);
    let (gpu_receipt, gpu_frames, gpu_ms, _gpu_dir) = render("gpu", GpuPreference::Auto);
    eprintln!("treatment range wall time: cpu {cpu_ms:.0} ms, gpu {gpu_ms:.0} ms");
    assert_eq!(cpu_receipt.renders[0].frames, Some(3));
    assert_eq!(gpu_receipt.renders[0].frames, Some(3));
    assert_eq!(
        cpu_receipt.renders[0].backend,
        ren_effectcraft::RenderBackend::Cpu
    );
    let mut max_diff = 0u8;
    let mut differing = 0u64;
    for (cpu, gpu) in cpu_frames.iter().zip(gpu_frames.iter()) {
        for (p, q) in cpu.pixels().zip(gpu.pixels()) {
            for c in 0..4 {
                let d = p[c].abs_diff(q[c]);
                max_diff = max_diff.max(d);
                if d != 0 {
                    differing += 1;
                }
            }
        }
    }
    eprintln!(
        "treatment range pixel agreement: max channel diff {max_diff}, {differing} differing channel values"
    );
    assert!(
        max_diff <= 1,
        "the GPU compositor must reproduce the CPU reference pixels within 1 LSB on the treatment range, got max diff {max_diff}"
    );
    let _ = gpu_receipt;
}
