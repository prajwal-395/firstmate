//! Unit 6 qualification: the Project Model -> FilmCraft projection drives the
//! pinned headless CLI end to end. Placement, trim, split, transition, audio,
//! render-asset validation, frame-map exactness, ID reconciliation, and
//! unsupported-operation refusals are all exercised through the adapter.
//!
//! Heavy: needs the pinned FilmCraft CLI plus ffmpeg/ffprobe. Run through
//! `vep-verify-run.sh`, never bare alongside other heavy lanes.

use ren_filmcraft::adapter::{
    asset_path, build_frame_map, import_operation, plan_edit, plan_projection, projection_json,
    reconcile_clip_ids, render_span, summarize_losses, validate_render_asset, verify_media,
    EditContext,
};
use ren_filmcraft::{ExportFormat, FilmCraft, HardwareEncoding, Operation, Request};
use ren_protocol::project_model::{decode_project_model, EditOperation, ProjectModel};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

fn request(operation: Operation) -> Request<Operation> {
    Request {
        adapter_schema: ren_filmcraft::ADAPTER_SCHEMA,
        operation,
    }
}

fn linked_av_fixture() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/linked-av.mp4")
}

/// Minimal two-clock model over the committed A/V fixture. The fixture plays
/// three seconds; at the declared 24 fps clocks that is 72 ticks of source
/// for 72 ticks of timeline per placement.
fn test_model(media: &Path, with_audio: bool, second_clip: bool) -> ProjectModel {
    let mut streams = vec![serde_json::json!(
        {"id": "stream-video", "asset_id": "asset-av", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}}
    )];
    if with_audio {
        streams.push(serde_json::json!(
            {"id": "stream-audio", "asset_id": "asset-av", "stream_index": 1, "clock_id": "clock-source", "metadata": {"codec_type": "audio"}}
        ));
    }
    let mut clip_uses = vec![serde_json::json!(
        {"id": "use-first", "asset_id": "asset-av",
         "source_interval": {"clock_id": "clock-source", "start": 0, "end": 72},
         "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 72},
         "entity_links": [], "role": "a_roll", "intent_id": null}
    )];
    if second_clip {
        clip_uses.push(serde_json::json!(
            {"id": "use-second", "asset_id": "asset-av",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 72},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 72, "end": 144},
             "entity_links": [], "role": "a_roll", "intent_id": null}
        ));
    }
    let document = serde_json::json!({
        "id": "adapter-qualification",
        "schema_version": 1,
        "timebase_policy": "rational_frames",
        "clocks": [
            {"id": "clock-source", "domain": {"kind": "source", "asset_id": "asset-av", "stream_id": "stream-video"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
            {"id": "clock-timeline", "domain": {"kind": "project"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
            {"id": "clock-render", "domain": {"kind": "render", "render_id": "render-1"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}}
        ],
        "assets": [
            {"id": "asset-av", "kind": "video", "uri": media.to_str().unwrap(), "content_digest": null, "metadata": {}, "provenance": ["prov-qual"]}
        ],
        "streams": streams,
        "entities": [], "observations": [], "identity_links": [], "masks": [],
        "object_classes": [], "tracks": [], "relations": [], "intents": [],
        "clip_uses": clip_uses,
        "edits": [], "effects": [], "renders": [], "frame_maps": [],
        "nle_bindings": [], "receipts": [],
        "provenance": [{"id": "prov-qual", "producer": "adapter-qualification", "producer_version": null, "source_digest": null, "recorded_at": null}]
    });
    decode_project_model(&document.to_string()).expect("qualification model decodes")
}

fn filmcraft_edit(id: &str, operation: &str, after: serde_json::Value) -> EditOperation {
    EditOperation {
        id: ren_protocol::project_model::EditOperationId(id.into()),
        operation: operation.into(),
        target_ids: vec!["use-first".into()],
        before: None,
        after: Some(after),
        intent_id: None,
        evidence_ids: vec![],
        author: "adapter-qualification".into(),
        parent_revision: None,
    }
}

struct PlacedProject {
    _temp: tempfile::TempDir,
    project: PathBuf,
    clip_ids: BTreeMap<String, Vec<u64>>,
    placement_losses: Vec<Vec<ren_filmcraft::LossReport>>,
}

fn place_through_adapter(adapter: &FilmCraft, name: &str, model: &ProjectModel) -> PlacedProject {
    let temp = tempfile::tempdir().unwrap();
    let projection = plan_projection(model).unwrap();
    assert_eq!(
        projection_json(&projection),
        projection_json(&plan_projection(model).unwrap()),
        "the pinned revision must plan deterministically"
    );
    let media = verify_media(Path::new("/"), &projection).unwrap();
    for (asset, path) in &media {
        assert_eq!(
            &asset_path(Path::new("/"), &projection.imports[0].uri),
            path
        );
        assert!(path.is_file(), "missing media for {asset}");
    }
    let project = temp.path().join(format!("{name}.fcproj"));
    adapter
        .execute(request(Operation::CreateProject {
            path: project.clone(),
            name: name.into(),
        }))
        .unwrap();
    let imported = adapter
        .execute(request(import_operation(
            project.clone(),
            &media,
            &projection,
        )))
        .unwrap();
    assert!(
        imported.engine_response["errors"]
            .as_array()
            .unwrap()
            .is_empty(),
        "import must be error-free: {}",
        imported.engine_response
    );
    let engine_items: Vec<u64> = imported.engine_response["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap())
        .collect();
    assert_eq!(engine_items.len(), projection.imports.len());
    let items: BTreeMap<String, u64> = projection
        .imports
        .iter()
        .zip(engine_items)
        .map(|(import, item)| (import.asset_id.clone(), item))
        .collect();
    let mut clips_per_receipt = Vec::new();
    let mut placement_losses = Vec::new();
    for (_, operation) in
        ren_filmcraft::adapter::placement_operations(&project, &items, &projection).unwrap()
    {
        let receipt = adapter.execute(request(operation)).unwrap();
        placement_losses.push(receipt.losses.clone());
        clips_per_receipt.push(
            receipt.engine_response["clips"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_u64().unwrap())
                .collect(),
        );
    }
    let clip_ids = reconcile_clip_ids(&projection, &clips_per_receipt).unwrap();
    assert_eq!(clip_ids.len(), projection.placements.len());
    PlacedProject {
        _temp: temp,
        project,
        clip_ids,
        placement_losses,
    }
}

#[test]
fn adapter_headless_edit_yields_receipt_render_map_ids_and_losses() {
    let adapter = FilmCraft::new().unwrap();
    let model = test_model(&linked_av_fixture(), true, true);
    let projection = plan_projection(&model).unwrap();
    assert_eq!(projection.placements.len(), 2);
    assert!(projection.losses.is_empty());
    let placed = place_through_adapter(&adapter, "adapter-flow", &model);

    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    let items: usize = inspection.engine_response["video"]
        .as_array()
        .unwrap()
        .iter()
        .map(|track| track["items"].as_array().unwrap().len())
        .sum();
    assert_eq!(items, 2, "both placements must be on the engine timeline");
    let v1_clips: Vec<u64> = inspection.engine_response["video"][0]["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item["clip"].as_u64().unwrap())
        .collect();
    assert!(
        v1_clips.contains(
            &ren_filmcraft::adapter::primary_clip_id(&placed.clip_ids["use-first"], "use-first")
                .unwrap()
        ),
        "the reconciled primary id must be the V1 video clip, not linked audio"
    );

    let map = build_frame_map(&model, &projection, "clock-render", "map-qual").unwrap();
    assert_eq!(map.segments.len(), 2);
    assert_eq!((map.segments[0].to.start, map.segments[0].to.end), (0, 72));
    assert_eq!(
        (map.segments[1].to.start, map.segments[1].to.end),
        (72, 144)
    );
    assert_eq!(render_span(&map), 144);
    assert_eq!(
        map.segments[0].source_clip_use.as_ref().unwrap().0,
        "use-first"
    );

    let render = placed._temp.path().join("adapter-flow.mp4");
    let export = adapter
        .execute(request(Operation::Export {
            project: placed.project.clone(),
            path: render.clone(),
            format: ExportFormat::H264,
            hardware_encoder: HardwareEncoding::Auto,
        }))
        .unwrap();
    assert!(export.engine_response["job"].is_u64());
    let bytes = validate_render_asset(&render).unwrap();
    assert!(bytes > 0);

    let losses = summarize_losses(&projection, &placed.placement_losses);
    assert!(
        losses.is_empty(),
        "a clean placement flow reports no loss: {losses:#?}"
    );
}

#[test]
fn adapter_surfaces_razor_and_trim_defects_as_losses() {
    let adapter = FilmCraft::new().unwrap();
    let model = test_model(&linked_av_fixture(), true, false);
    let placed = place_through_adapter(&adapter, "adapter-defects", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };

    let razor = plan_edit(
        &filmcraft_edit(
            "edit-split",
            "filmcraft.split",
            serde_json::json!({"track": "V1", "frame": 24}),
        ),
        &context,
    )
    .unwrap();
    let receipt = adapter.execute(request(razor)).unwrap();
    assert!(
        receipt.engine_response["cuts"].as_u64().unwrap_or_default() > 0,
        "the split must cut: {}",
        receipt.engine_response
    );
    assert!(
        receipt
            .losses
            .iter()
            .any(|loss| loss.code == "linked_audio_not_razored"),
        "the V1-only razor defect must surface, got {:#?}",
        receipt.losses
    );

    let trim_model = test_model(&linked_av_fixture(), false, false);
    let trim_placed = place_through_adapter(&adapter, "adapter-trim-defect", &trim_model);
    let trim_context = EditContext {
        project: &trim_placed.project,
        clip_ids: &trim_placed.clip_ids,
    };
    let trim = plan_edit(
        &filmcraft_edit(
            "edit-trim",
            "filmcraft.trim",
            serde_json::json!({"edge": "out", "mode": "ripple", "delta_frames": -318}),
        ),
        &trim_context,
    )
    .unwrap();
    let receipt = adapter.execute(request(trim)).unwrap();
    assert!(
        receipt
            .losses
            .iter()
            .any(|loss| loss.code == "trim_delta_mismatch"),
        "the wrong-trim-delta defect must surface, got {:#?}",
        receipt.losses
    );
    let losses = summarize_losses(&plan_projection(&trim_model).unwrap(), &[receipt.losses]);
    assert!(
        losses.iter().any(|loss| loss.code == "trim_delta_mismatch"),
        "adapter loss summaries keep driver defects verbatim"
    );
}

#[test]
fn adapter_drives_transition_audio_move_and_refuses_unsupported() {
    let adapter = FilmCraft::new().unwrap();
    let model = test_model(&linked_av_fixture(), true, false);
    let placed = place_through_adapter(&adapter, "adapter-edits", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };

    let split = plan_edit(
        &filmcraft_edit(
            "edit-split",
            "filmcraft.split",
            serde_json::json!({"track": "V1", "frame": 24}),
        ),
        &context,
    )
    .unwrap();
    adapter.execute(request(split)).unwrap();
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    let second_clip = inspection.engine_response["video"][0]["items"][1]["clip"]
        .as_u64()
        .unwrap();
    let mut transition_context_ids = placed.clip_ids.clone();
    transition_context_ids.insert("use-first".into(), vec![second_clip]);
    let transition_context = EditContext {
        project: &placed.project,
        clip_ids: &transition_context_ids,
    };
    let transition = plan_edit(
        &filmcraft_edit(
            "edit-transition",
            "filmcraft.transition",
            serde_json::json!({"edge": "in", "frames": 4}),
        ),
        &transition_context,
    )
    .unwrap();
    let receipt = adapter.execute(request(transition)).unwrap();
    assert!(receipt.engine_response["transition"].is_u64());

    let gain = plan_edit(
        &filmcraft_edit(
            "edit-gain",
            "filmcraft.audio_gain",
            serde_json::json!({"track": "A1", "gain_db": -3.0}),
        ),
        &context,
    )
    .unwrap();
    let receipt = adapter.execute(request(gain)).unwrap();
    assert_eq!(receipt.engine_response["value"].as_f64(), Some(-3.0));

    let shift = plan_edit(
        &filmcraft_edit(
            "edit-move",
            "filmcraft.move",
            serde_json::json!({"track": "V2", "timeline_ticks": 254016000000i64, "insert": true}),
        ),
        &transition_context,
    )
    .unwrap();
    let receipt = adapter.execute(request(shift)).unwrap();
    assert!(receipt.engine_response["moved"]
        .as_array()
        .unwrap()
        .contains(&serde_json::json!(second_clip)));

    let still = placed._temp.path().join("adapter-frame.png");
    let rendered = adapter
        .execute(request(Operation::RenderFrame {
            project: placed.project.clone(),
            frame: 0,
            path: still.clone(),
        }))
        .unwrap();
    assert_eq!(rendered.engine_response["path"], still.to_str().unwrap());
    assert!(still.is_file());

    let legacy = filmcraft_edit(
        "edit-legacy",
        "clip.trim",
        serde_json::json!({"timeline_end": 36, "duration": 36}),
    );
    let err = plan_edit(&legacy, &context).unwrap_err();
    assert!(
        format!("{err}").contains("unsupported_edit_operation"),
        "legacy operation strings must refuse without inference: {err}"
    );

    let mut effected = test_model(&linked_av_fixture(), true, false);
    effected.effects.push(
        serde_json::from_value(serde_json::json!(
            {"id": "effect-tint", "class_id": "color.saturation", "target_id": "use-first",
             "interval": {"clock_id": "clock-timeline", "start": 0, "end": 72},
             "parameters": {"saturation": 0.8}, "keyframes": []}
        ))
        .unwrap(),
    );
    let projection = plan_projection(&effected).unwrap();
    assert!(
        projection
            .losses
            .iter()
            .any(|loss| loss.code == "effect_not_projected"),
        "model effects must appear as explicit loss entries"
    );
}
