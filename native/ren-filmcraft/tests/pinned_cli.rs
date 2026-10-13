use ren_filmcraft::{
    BitrateMode, ExportFormat, FilmCraft, GpuRendering, HardwareEncoding, Operation, Request,
    TrimEdge, TrimMode, ADAPTER_SCHEMA, DEFAULT_H264_BITRATE_KBPS,
};
use serde_json::json;

fn request(operation: Operation) -> Request<Operation> {
    Request {
        adapter_schema: ADAPTER_SCHEMA,
        operation,
    }
}

fn fixture() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/linked-av.mp4")
}

fn new_project(adapter: &FilmCraft, root: &std::path::Path, name: &str) -> std::path::PathBuf {
    let path = root.join(format!("{name}.fcproj"));
    let receipt = adapter
        .execute(request(Operation::CreateProject {
            path: path.clone(),
            name: name.into(),
        }))
        .unwrap();
    assert_eq!(receipt.engine_revision, ren_filmcraft::FILMCRAFT_REVISION);
    assert!(receipt.engine_response["sequence"].is_u64());
    path
}

fn import_and_place(adapter: &FilmCraft, project: &std::path::Path, audio: bool) -> u64 {
    let imported = adapter
        .execute(request(Operation::ImportMedia {
            project: project.into(),
            paths: vec![fixture()],
        }))
        .unwrap();
    assert!(imported.engine_response["errors"]
        .as_array()
        .unwrap()
        .is_empty());
    let item = imported.engine_response["items"][0].as_u64().unwrap();
    // 3 seconds in FilmCraft's 254016000000 ticks/second project clock.
    let placed = adapter
        .execute(request(Operation::Place {
            project: project.into(),
            item,
            track: "V1".into(),
            audio_track: audio.then(|| "A1".into()),
            video: true,
            audio,
            frame: 0,
            source_in_ticks: 0,
            duration_ticks: 762_048_000_000,
        }))
        .unwrap();
    let clips = placed.engine_response["clips"].as_array().unwrap();
    clips[0].as_u64().unwrap()
}

#[test]
fn pinned_cli_detects_v1_only_razor_leaving_linked_audio() {
    let adapter = FilmCraft::new().unwrap();
    let temp = tempfile::tempdir().unwrap();
    let project = new_project(&adapter, temp.path(), "razor-defect");
    let video_clip = import_and_place(&adapter, &project, true);
    let receipt = adapter
        .execute(request(Operation::Razor {
            project,
            track: "V1".into(),
            frame: 24,
        }))
        .unwrap();
    assert!(receipt.engine_response["cuts"].as_u64().unwrap_or_default() > 0);
    assert!(
        receipt
            .losses
            .iter()
            .any(|loss| loss.code == "linked_audio_not_razored"),
        "clip {video_clip}: {:#?}",
        receipt.losses
    );
}

#[test]
fn pinned_cli_reports_trim_delta_that_does_not_match_requested_frames() {
    let adapter = FilmCraft::new().unwrap();
    let temp = tempfile::tempdir().unwrap();
    let project = new_project(&adapter, temp.path(), "trim-defect");
    let clip = import_and_place(&adapter, &project, false);
    let receipt = adapter
        .execute(request(Operation::Trim {
            project,
            clip,
            edge: TrimEdge::Out,
            mode: TrimMode::Ripple,
            delta_frames: -318,
        }))
        .unwrap();
    assert!(
        receipt
            .losses
            .iter()
            .any(|loss| loss.code == "trim_delta_mismatch"),
        "{:#?}",
        receipt
    );
}

#[test]
fn engine_response_unknown_fields_are_refused() {
    let response = json!({"cuts":2,"future_field":true});
    assert!(matches!(
        ren_filmcraft::validate_response_contract("timeline.razor", &response),
        Err(ren_filmcraft::Error::UnknownResponse { .. })
    ));
}

#[test]
fn pinned_cli_drives_edits_inspection_still_render_and_all_required_exports() {
    let adapter = FilmCraft::new().unwrap();
    let temp = tempfile::tempdir().unwrap();
    let project = new_project(&adapter, temp.path(), "operations");
    let clip = import_and_place(&adapter, &project, true);
    let razor = adapter
        .execute(request(Operation::Razor {
            project: project.clone(),
            track: "V1".into(),
            frame: 24,
        }))
        .unwrap();
    assert!(razor.engine_response["cuts"].as_u64().unwrap_or_default() > 0);

    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: project.clone(),
        }))
        .unwrap();
    let second_clip = inspection.engine_response["video"][0]["items"][1]["clip"]
        .as_u64()
        .unwrap();
    let transition = adapter
        .execute(request(Operation::Transition {
            project: project.clone(),
            clip: second_clip,
            edge: TrimEdge::In,
            frames: 4,
            effect: None,
        }))
        .unwrap();
    assert!(transition.engine_response["transition"].is_u64());

    let gain = adapter
        .execute(request(Operation::AudioGain {
            project: project.clone(),
            track: "A1".into(),
            gain_db: -3.0,
        }))
        .unwrap();
    assert_eq!(gain.engine_response["value"].as_f64(), Some(-3.0));

    let moved = adapter
        .execute(request(Operation::Move {
            project: project.clone(),
            clip: second_clip,
            track: "V2".into(),
            timeline_ticks: 254_016_000_000,
            insert: true,
        }))
        .unwrap();
    assert!(moved.engine_response["moved"]
        .as_array()
        .unwrap()
        .contains(&json!(second_clip)));

    let still = temp.path().join("frame.png");
    let rendered = adapter
        .execute(request(Operation::RenderFrame {
            project: project.clone(),
            frame: 0,
            path: still.clone(),
        }))
        .unwrap();
    assert_eq!(rendered.engine_response["path"], still.to_str().unwrap());
    assert!(still.is_file());

    for (name, format, extension, hardware) in [
        ("h264", ExportFormat::H264, "mp4", HardwareEncoding::Auto),
        ("prores", ExportFormat::ProRes, "mov", HardwareEncoding::Off),
        ("wav", ExportFormat::Wav, "wav", HardwareEncoding::Off),
    ] {
        let path = temp.path().join(format!("{name}.{extension}"));
        let receipt = adapter
            .execute(request(Operation::Export {
                project: project.clone(),
                path: path.clone(),
                format,
                hardware_encoder: hardware,
                gpu_rendering: GpuRendering::Off,
                bitrate_kbps: None,
                bitrate_mode: None,
            }))
            .unwrap();
        assert!(receipt.engine_response["job"].is_u64());
        assert!(path.is_file(), "{name} export missing: {}", path.display());
        // The render path the adapter requested is recorded on the
        // receipt, so a silently inert GPU path stays visible.
        assert_eq!(receipt.hardware_encoding, Some(hardware));
        assert_eq!(receipt.gpu_rendering, Some(GpuRendering::Off));
    }
    assert!(clip > 0);
}

/// Gap-4 proof: the probed H264 video stream bitrate lands within
/// ±10% of the requested `bitrate_kbps`, and an omitted bitrate lands
/// within ±10% of Ren's delivery default (`DEFAULT_H264_BITRATE_KBPS`).
///
/// The source is generated high-complexity 1080p (testsrc2 + white noise):
/// trivial content saturates the encoder well below any delivery target
/// (measured: the 160x90 A/V fixture caps near 250 kbps video no matter the
/// request), so the proof needs content demanding enough to reach it.
/// Software encoding (`HardwareEncoding::Off`) keeps the proof
/// deterministic; ffmpeg/ffprobe are declared requirements, so their
/// absence fails rather than passing vacuously.
#[test]
fn pinned_cli_h264_export_honors_requested_bitrate() {
    const TOLERANCE: f64 = 0.10;
    const REQUEST_KBPS: u32 = 4_000;

    fn run(cmd: &str, args: &[&str]) -> std::process::Output {
        std::process::Command::new(cmd)
            .args(args)
            .output()
            .unwrap_or_else(|e| panic!("{cmd} must be on PATH for the bitrate proof: {e}"))
    }

    let adapter = FilmCraft::new().unwrap();
    let temp = tempfile::tempdir().unwrap();
    let source = temp.path().join("noise-src.mp4");
    let generated = run(
        "ffmpeg",
        &[
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=24:duration=3",
            "-f",
            "lavfi",
            "-i",
            "anoisesrc=color=white:duration=3:sample_rate=48000",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            source.to_str().unwrap(),
        ],
    );
    assert!(
        generated.status.success() && source.is_file(),
        "ffmpeg must generate the bitrate-proof source: {}",
        String::from_utf8_lossy(&generated.stderr)
    );

    let project = new_project(&adapter, temp.path(), "bitrate-proof");
    let imported = adapter
        .execute(request(Operation::ImportMedia {
            project: project.clone(),
            paths: vec![source],
        }))
        .unwrap();
    let item = imported.engine_response["items"][0].as_u64().unwrap();
    adapter
        .execute(request(Operation::Place {
            project: project.clone(),
            item,
            track: "V1".into(),
            audio_track: Some("A1".into()),
            video: true,
            audio: true,
            frame: 0,
            source_in_ticks: 0,
            // 3 seconds in the engine's project clock.
            duration_ticks: 762_048_000_000,
        }))
        .unwrap();

    fn video_bitrate_bps(path: &std::path::Path) -> u64 {
        let probed = std::process::Command::new("ffprobe")
            .args([
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=bit_rate",
                "-of",
                "csv=p=0",
            ])
            .arg(path)
            .output()
            .unwrap_or_else(|e| panic!("ffprobe must be on PATH for the bitrate proof: {e}"));
        assert!(
            probed.status.success(),
            "ffprobe must read {}: {}",
            path.display(),
            String::from_utf8_lossy(&probed.stderr)
        );
        String::from_utf8_lossy(&probed.stdout)
            .trim()
            .parse()
            .unwrap_or_else(|e| panic!("video bit_rate must parse for {}: {e}", path.display()))
    }

    // Explicit request: CBR 4000 kbps software-encoded.
    let explicit = temp.path().join("bitrate-explicit.mp4");
    adapter
        .execute(request(Operation::Export {
            project: project.clone(),
            path: explicit.clone(),
            format: ExportFormat::H264,
            hardware_encoder: HardwareEncoding::Off,
            gpu_rendering: GpuRendering::Off,
            bitrate_kbps: Some(REQUEST_KBPS),
            bitrate_mode: Some(BitrateMode::Cbr),
        }))
        .unwrap();
    let actual = video_bitrate_bps(&explicit) as f64;
    let target = f64::from(REQUEST_KBPS) * 1000.0;
    assert!(
        (actual - target).abs() / target <= TOLERANCE,
        "explicit {REQUEST_KBPS} kbps request probed at {actual} bps, outside ±{}%",
        TOLERANCE * 100.0,
    );

    // Omitted bitrate: Ren's delivery default.
    let defaulted = temp.path().join("bitrate-default.mp4");
    adapter
        .execute(request(Operation::Export {
            project: project.clone(),
            path: defaulted.clone(),
            format: ExportFormat::H264,
            hardware_encoder: HardwareEncoding::Off,
            gpu_rendering: GpuRendering::Off,
            bitrate_kbps: None,
            bitrate_mode: None,
        }))
        .unwrap();
    let actual = video_bitrate_bps(&defaulted) as f64;
    let target = f64::from(DEFAULT_H264_BITRATE_KBPS) * 1000.0;
    assert!(
        (actual - target).abs() / target <= TOLERANCE,
        "default export probed at {actual} bps, outside ±{}% of Ren's delivery default ({target} bps)",
        TOLERANCE * 100.0,
    );
}

#[test]
fn checked_in_transition_authority_matches_the_pinned_cli() {
    // G10 drift guard: `src/video_transitions.json` is the plan-time
    // authority for transition effect validation. The pinned CLI must still
    // report exactly its id set; a pin move re-measures the file.
    let cli = ren_filmcraft::locate_or_build_cli().unwrap();
    let output = std::process::Command::new(&cli)
        .args([
            "exec",
            "effects.list",
            r#"{"kind":"VideoTransition"}"#,
            "--compact",
        ])
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "effects.list must succeed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let live: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    let mut live_ids: Vec<&str> = live
        .as_array()
        .expect("effects.list answers an array")
        .iter()
        .map(|entry| entry["id"].as_str().expect("transition entry carries id"))
        .collect();
    live_ids.sort_unstable();
    let authority: serde_json::Value =
        serde_json::from_str(include_str!("../src/video_transitions.json")).unwrap();
    let mut pinned_ids: Vec<&str> = authority
        .as_array()
        .unwrap()
        .iter()
        .map(|entry| entry["id"].as_str().unwrap())
        .collect();
    pinned_ids.sort_unstable();
    assert_eq!(
        live_ids, pinned_ids,
        "pinned CLI drifted from the checked-in transition authority; re-measure `effects.list` and update `src/video_transitions.json` with the pin move"
    );
}
