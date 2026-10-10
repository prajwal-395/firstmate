use ren_filmcraft::{
    ExportFormat, FilmCraft, HardwareEncoding, Operation, Request, TrimEdge, TrimMode,
    ADAPTER_SCHEMA,
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
            }))
            .unwrap();
        assert!(receipt.engine_response["job"].is_u64());
        assert!(path.is_file(), "{name} export missing: {}", path.display());
    }
    assert!(clip > 0);
}
