//! Unit 6 qualification: the Project Model -> FilmCraft projection drives the
//! pinned headless CLI end to end. Placement, trim, split, delete, ripple
//! delete, ripple, retime, transition, audio,
//! render-asset validation, frame-map exactness, ID reconciliation,
//! unsupported-operation refusals, CLI-level loss proofs (a
//! mask/effect/track-carrying model executed end to end reports
//! byte-identical losses to the plan), and a concurrent-render probe
//! (N=1 vs N=2 vs N=4 wall/RSS, reported never gated) are all exercised
//! through the adapter.
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

use ren_filmcraft::adapter::{
    apply_ren_edit_to_model, build_frame_map as build_map, plan_projection as plan_proj,
    plan_ren_edit,
};

fn ren_edit_for(id: &str, operation: &str, target: &str, after: serde_json::Value) -> EditOperation {
    EditOperation {
        id: ren_protocol::project_model::EditOperationId(id.into()),
        operation: operation.into(),
        target_ids: vec![target.into()],
        before: None,
        after: Some(after),
        intent_id: None,
        evidence_ids: vec![],
        author: "ren-equivalence".into(),
        parent_revision: None,
    }
}

/// Video spans on the engine timeline, frame-exact: (startFrame, durationFrames).
fn inspect_spans(inspection: &serde_json::Value, kind: &str) -> Vec<(i64, i64)> {
    let mut spans = Vec::new();
    for track in inspection[kind].as_array().unwrap() {
        for item in track["items"].as_array().unwrap() {
            spans.push((
                item["startFrame"].as_i64().unwrap(),
                item["durationFrames"].as_i64().unwrap(),
            ));
        }
    }
    spans.sort();
    spans
}

/// Expected spans from the mutated model's projection: placement frame plus
/// timeline length in frames (the 24fps qualification clocks tick per frame).
fn expected_spans(model: &ProjectModel) -> Vec<(i64, i64)> {
    let projection = plan_proj(model).unwrap();
    let mut spans: Vec<(i64, i64)> = projection
        .placements
        .iter()
        .map(|p| {
            let duration_frames = p.timeline_end - p.timeline_start;
            (p.frame, duration_frames)
        })
        .collect();
    spans.sort();
    spans
}

#[test]
fn ren_split_equivalence_drives_linked_audio_with_no_loss() {
    let adapter = FilmCraft::new().unwrap();
    let model = test_model(&linked_av_fixture(), true, false);
    let placed = place_through_adapter(&adapter, "ren-split-equiv", &model);

    // A V1-only razor reproduces the measured defect, proving the loss is
    // real before the Ren plan drives both tracks.
    let before_len = {
        let inspection = adapter
            .execute(request(Operation::Inspect {
                project: placed.project.clone(),
            }))
            .unwrap();
        inspect_spans(&inspection.engine_response, "video").len()
    };
    assert_eq!(before_len, 1);
    let single_razor = Operation::Razor {
        project: placed.project.clone(),
        track: "V1".into(),
        frame: 24,
    };
    let defect = adapter.execute(request(single_razor)).unwrap();
    assert!(
        defect
            .losses
            .iter()
            .any(|loss| loss.code == "linked_audio_not_razored"),
        "a V1-only razor must still surface the measured defect: {:#?}",
        defect.losses
    );

    // Re-place cleanly, then plan Ren's own split: video plus linked audio.
    let placed = place_through_adapter(&adapter, "ren-split-clean", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let edit = ren_edit_for(
        "edit-ren-split",
        "clip.split",
        "use-first",
        serde_json::json!({"at_frame": 24}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    assert_eq!(
        operations.len(),
        2,
        "linked audio must be driven explicitly, got {operations:#?}"
    );
    let mut all_losses = Vec::new();
    let mut second_clean = true;
    for (index, operation) in operations.into_iter().enumerate() {
        let receipt = adapter.execute(request(operation)).unwrap();
        if index == 1
            && receipt
                .losses
                .iter()
                .any(|loss| loss.code == "linked_audio_not_razored")
        {
            second_clean = false;
        }
        all_losses.extend(receipt.losses.clone());
    }
    assert!(
        second_clean,
        "the explicit linked-audio razor must itself be clean: {all_losses:#?}"
    );

    // Equivalence: the engine timeline matches the ren-core-mutated model
    // frame-exact, and the frame map covers the same spans.
    let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&mutated),
        "FilmCraft video must match Ren's expected clip uses frame-exact"
    );
    assert_eq!(
        inspect_spans(&inspection.engine_response, "audio"),
        expected_spans(&mutated),
        "linked audio must follow the picture edit"
    );
    let before_map = build_map(&model, &projection, "clock-render", "map-before").unwrap();
    let after_projection = plan_proj(&mutated).unwrap();
    let after_map = build_map(&mutated, &after_projection, "clock-render", "map-after").unwrap();
    assert_eq!(ren_filmcraft::adapter::render_span(&before_map), 72);
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 72);
    assert_eq!(after_map.segments.len(), 2);
    assert_eq!(
        (after_map.segments[0].to.start, after_map.segments[0].to.end),
        (0, 24)
    );
    assert_eq!(
        (after_map.segments[1].to.start, after_map.segments[1].to.end),
        (24, 72)
    );
}

#[test]
fn ren_trim_move_transition_gain_match_model_frame_exact() {
    let adapter = FilmCraft::new().unwrap();

    // Trim out edge 72 -> 60: engine and model agree, render map shortens.
    let model = test_model(&linked_av_fixture(), false, false);
    let placed = place_through_adapter(&adapter, "ren-trim-equiv", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let edit = ren_edit_for(
        "edit-ren-trim",
        "clip.trim",
        "use-first",
        serde_json::json!({"in_frame": 0, "out_frame": 60}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    assert_eq!(operations.len(), 1);
    for operation in operations {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(
            !receipt
                .losses
                .iter()
                .any(|loss| loss.code == "trim_delta_mismatch"),
            "a small exact trim must apply without delta loss: {:#?}",
            receipt.losses
        );
    }
    let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&mutated),
    );
    let after_projection = plan_proj(&mutated).unwrap();
    let after_map = build_map(&mutated, &after_projection, "clock-render", "map-trim").unwrap();
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 60);

    // Move to frame 24 (single-clip model, no overlap): engine and model agree.
    let model = test_model(&linked_av_fixture(), false, false);
    let placed = place_through_adapter(&adapter, "ren-move-equiv", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let edit = ren_edit_for(
        "edit-ren-move",
        "clip.move",
        "use-first",
        serde_json::json!({"to_frame": 24}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    for operation in operations {
        adapter.execute(request(operation)).unwrap();
    }
    let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&mutated),
    );

    // Transition and gain change no timeline spans; receipts prove the drive.
    let model = test_model(&linked_av_fixture(), true, false);
    let placed = place_through_adapter(&adapter, "ren-tg-equiv", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let razor = ren_edit_for(
        "edit-ren-tg-split",
        "clip.split",
        "use-first",
        serde_json::json!({"at_frame": 24}),
    );
    for operation in plan_ren_edit(&model, &projection, &razor, &context).unwrap() {
        adapter.execute(request(operation)).unwrap();
    }
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    let second_clip = inspection.engine_response["video"][0]["items"][1]["clip"]
        .as_u64()
        .unwrap();
    let mut ids = placed.clip_ids.clone();
    ids.insert("use-first".into(), vec![second_clip]);
    let tcontext = EditContext {
        project: &placed.project,
        clip_ids: &ids,
    };
    let transition = ren_edit_for(
        "edit-ren-transition",
        "transition",
        "use-first",
        serde_json::json!({"edge": "in", "frames": 4}),
    );
    for operation in plan_ren_edit(&model, &projection, &transition, &tcontext).unwrap() {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(receipt.engine_response["transition"].is_u64());
    }
    let gain = ren_edit_for(
        "edit-ren-gain",
        "audio.gain",
        "use-first",
        serde_json::json!({"db": -3.0}),
    );
    for operation in plan_ren_edit(&model, &projection, &gain, &context).unwrap() {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert_eq!(receipt.engine_response["value"].as_f64(), Some(-3.0));
    }
}

/// Three back-to-back V1 clips over the A/V fixture (72 frames each).
fn three_clip_model(media: &Path) -> ProjectModel {
    let document = serde_json::json!({
        "id": "adapter-qualification-three",
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
        "streams": [
            {"id": "stream-video", "asset_id": "asset-av", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}},
            {"id": "stream-audio", "asset_id": "asset-av", "stream_index": 1, "clock_id": "clock-source", "metadata": {"codec_type": "audio"}}
        ],
        "entities": [], "observations": [], "identity_links": [], "masks": [],
        "object_classes": [], "tracks": [], "relations": [], "intents": [],
        "clip_uses": [
            {"id": "use-first", "asset_id": "asset-av",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 72},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 72},
             "entity_links": [], "role": "a_roll", "intent_id": null},
            {"id": "use-second", "asset_id": "asset-av",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 72},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 72, "end": 144},
             "entity_links": [], "role": "a_roll", "intent_id": null},
            {"id": "use-third", "asset_id": "asset-av",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 72},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 144, "end": 216},
             "entity_links": [], "role": "a_roll", "intent_id": null}
        ],
        "edits": [], "effects": [], "renders": [], "frame_maps": [],
        "nle_bindings": [], "receipts": [],
        "provenance": [{"id": "prov-qual", "producer": "adapter-qualification", "producer_version": null, "source_digest": null, "recorded_at": null}]
    });
    decode_project_model(&document.to_string()).expect("three-clip model decodes")
}

/// Engine speed field per video span, frame-exact: (start, duration, speed).
fn inspect_speeds(inspection: &serde_json::Value) -> Vec<(i64, i64, f64)> {
    let mut spans = Vec::new();
    for track in inspection["video"].as_array().unwrap() {
        for item in track["items"].as_array().unwrap() {
            spans.push((
                item["startFrame"].as_i64().unwrap(),
                item["durationFrames"].as_i64().unwrap(),
                item["speed"].as_f64().unwrap(),
            ));
        }
    }
    spans.sort_by(|a, b| a.0.cmp(&b.0));
    spans
}

#[test]
fn ren_delete_lift_leaves_gap_with_linked_audio() {
    let adapter = FilmCraft::new().unwrap();
    let model = three_clip_model(&linked_av_fixture());
    let placed = place_through_adapter(&adapter, "ren-delete-lift", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let edit = ren_edit_for(
        "edit-ren-delete",
        "clip.delete",
        "use-second",
        serde_json::json!({}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    assert_eq!(operations.len(), 1);
    for operation in operations {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(
            receipt.losses.is_empty(),
            "a clean lift reports no loss: {:#?}",
            receipt.losses
        );
    }
    // Equivalence: the engine timeline matches the ren-core-mutated model
    // frame-exact - the middle clip and its audio are gone, the gap stays.
    let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
    assert_eq!(mutated.clip_uses.len(), 2);
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&mutated),
    );
    assert_eq!(
        inspect_spans(&inspection.engine_response, "audio"),
        expected_spans(&mutated),
    );
    // The frame map keeps the gap explicit and the render span unchanged.
    let after_projection = plan_proj(&mutated).unwrap();
    let after_map = build_map(&mutated, &after_projection, "clock-render", "map-lift").unwrap();
    assert_eq!(after_map.segments.len(), 3);
    assert_eq!(
        after_map.segments[1].mapping_kind,
        ren_protocol::project_model::MappingKind::Gap
    );
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 216);
}

#[test]
fn ren_ripple_delete_shifts_followers_on_every_track() {
    let adapter = FilmCraft::new().unwrap();
    let model = three_clip_model(&linked_av_fixture());
    let placed = place_through_adapter(&adapter, "ren-ripple-delete", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let edit = ren_edit_for(
        "edit-ren-ripple-delete",
        "clip.delete",
        "use-first",
        serde_json::json!({"ripple": true}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    assert_eq!(operations.len(), 1);
    for operation in operations {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(
            receipt.losses.is_empty(),
            "a clean ripple delete reports no loss: {:#?}",
            receipt.losses
        );
    }
    let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&mutated),
    );
    assert_eq!(
        inspect_spans(&inspection.engine_response, "audio"),
        expected_spans(&mutated),
    );
    let after_projection = plan_proj(&mutated).unwrap();
    let after_map = build_map(&mutated, &after_projection, "clock-render", "map-extract").unwrap();
    assert_eq!(after_map.segments.len(), 2);
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 144);
}

#[test]
fn ren_ripple_shifts_target_and_followers_frame_exact() {
    let adapter = FilmCraft::new().unwrap();
    // A gapped timeline (gap 72-96): ripple destinations never touch a
    // neighbour, so the engine lands fully clean. (Measured engine quirk:
    // non-zero-frame placements store ~0.07 frames long, so a ripple that
    // lands abutting a neighbour contact-trims it - reported as
    // `move_overwrote` - while gapped destinations stay exact.)
    let mut model = test_model(&linked_av_fixture(), true, true);
    model.clip_uses[1].timeline_interval.start = 96;
    model.clip_uses[1].timeline_interval.end = 168;
    let placed = place_through_adapter(&adapter, "ren-ripple", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let edit = ren_edit_for(
        "edit-ren-ripple",
        "clip.ripple",
        "use-first",
        serde_json::json!({"delta_frames": 12}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    assert_eq!(operations.len(), 2, "target plus one follower");
    for operation in operations {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(
            receipt.losses.is_empty(),
            "gapped ripple destinations stay free: {:#?}",
            receipt.losses
        );
    }
    let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&mutated),
    );
    assert_eq!(
        inspect_spans(&inspection.engine_response, "audio"),
        expected_spans(&mutated),
    );
    // A pure shift keeps the render span.
    let after_projection = plan_proj(&mutated).unwrap();
    let after_map = build_map(&mutated, &after_projection, "clock-render", "map-ripple").unwrap();
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 168);
}

#[test]
fn ren_retime_up_and_down_match_model_with_exact_rate() {
    let adapter = FilmCraft::new().unwrap();
    // Speed-up: 72 + 72 becomes 36 + 72, rate 2/1.
    let model = test_model(&linked_av_fixture(), true, true);
    let placed = place_through_adapter(&adapter, "ren-retime-up", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let edit = ren_edit_for(
        "edit-ren-retime-up",
        "retime",
        "use-first",
        serde_json::json!({"percent": 200.0}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    assert_eq!(operations.len(), 1);
    for operation in operations {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(
            receipt.losses.is_empty(),
            "a clean retime reports no loss: {:#?}",
            receipt.losses
        );
    }
    let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&mutated),
    );
    assert_eq!(
        inspect_spans(&inspection.engine_response, "audio"),
        expected_spans(&mutated),
    );
    assert_eq!(
        inspect_speeds(&inspection.engine_response),
        vec![(0, 36, 2.0), (36, 72, 1.0)],
    );
    let after_projection = plan_proj(&mutated).unwrap();
    let after_map = build_map(&mutated, &after_projection, "clock-render", "map-fast").unwrap();
    assert_eq!(
        after_map.segments[0].rate,
        Some(ren_protocol::project_model::Rational {
            numerator: 2,
            denominator: 1
        })
    );
    assert_eq!(
        after_map.segments[0].mapping_kind,
        ren_protocol::project_model::MappingKind::Retimed
    );
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 108);

    // Slow-down: 72 + 72 becomes 144 + 72, rate 1/2. The mutated model
    // re-projects because the logged retime explains the longer span.
    let model = test_model(&linked_av_fixture(), true, true);
    let placed = place_through_adapter(&adapter, "ren-retime-down", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let edit = ren_edit_for(
        "edit-ren-retime-down",
        "span_retime",
        "use-first",
        serde_json::json!({"percent": 50.0}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    assert_eq!(operations.len(), 1);
    for operation in operations {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(
            receipt.losses.is_empty(),
            "a clean slow-down reports no loss: {:#?}",
            receipt.losses
        );
    }
    let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&mutated),
    );
    assert_eq!(
        inspect_spans(&inspection.engine_response, "audio"),
        expected_spans(&mutated),
    );
    assert_eq!(
        inspect_speeds(&inspection.engine_response),
        vec![(0, 144, 0.5), (144, 72, 1.0)],
    );
    let after_projection = plan_proj(&mutated).unwrap();
    let after_map = build_map(&mutated, &after_projection, "clock-render", "map-slow").unwrap();
    assert_eq!(
        after_map.segments[0].rate,
        Some(ren_protocol::project_model::Rational {
            numerator: 1,
            denominator: 2
        })
    );
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 216);
}

#[test]
fn ren_refusals_carry_named_codes_through_the_cli_path() {
    let model = test_model(&linked_av_fixture(), true, false);
    let projection = plan_proj(&model).unwrap();
    let clip_ids = std::collections::BTreeMap::from([("use-first".to_owned(), vec![7u64])]);
    let context = EditContext {
        project: std::path::Path::new("test.fcproj"),
        clip_ids: &clip_ids,
    };
    for (operation, after, code) in [
        (
            "retime.segments",
            serde_json::json!({"segments": [{"record_frame": 36, "percent": 200.0}]}),
            "retime_segments_not_supported",
        ),
        (
            "retime",
            serde_json::json!({"percent": 200.0, "reverse": true}),
            "reverse_not_supported",
        ),
        (
            "retime",
            serde_json::json!({"percent": 33.333333333333336}),
            "retime_rate_inexact",
        ),
        (
            "retime",
            serde_json::json!({"percent": 123.456}),
            "retime_inexact_duration",
        ),
        (
            "clip.ripple",
            serde_json::json!({"delta_frames": 0}),
            "empty_ripple",
        ),
    ] {
        let edit = ren_edit_for("edit-refuse", operation, "use-first", after);
        let err = plan_ren_edit(&model, &projection, &edit, &context).unwrap_err();
        assert!(
            format!("{err}").contains(code),
            "{operation} must refuse as {code}, got {err}"
        );
        let model_err = apply_ren_edit_to_model(&model, &edit).unwrap_err();
        assert!(
            format!("{model_err}").contains(code),
            "model mutation must refuse {operation} with the same code, got {model_err}"
        );
    }
}

/// G9 loss proof: a mask/effect/track-carrying model is executed through
/// the pinned CLI, and the executed receipt losses must equal the
/// plan-level losses byte-identical. Plan-level-only assertions (see the
/// `effect_not_projected` check in
/// `adapter_drives_transition_audio_move_and_refuses_unsupported`) cannot
/// catch a driver that drops or invents a loss at execution time.
#[test]
fn mask_effect_track_losses_proven_byte_identical_through_the_pinned_cli() {
    let adapter = FilmCraft::new().unwrap();
    let mut model = test_model(&linked_av_fixture(), true, false);
    model.masks.push(
        serde_json::from_value(serde_json::json!(
            {"id": "mask-qual", "entity_id": "person-qual",
             "interval": {"clock_id": "clock-timeline", "start": 0, "end": 72},
             "representation": {"kind": "ellipse",
                                "normalized_bbox": [0.35, 0.1, 0.3, 0.65]},
             "source_model": "adapter-qualification", "provenance_id": "prov-qual"}
        ))
        .unwrap(),
    );
    model.tracks.push(
        serde_json::from_value(serde_json::json!(
            {"id": "track-qual", "entity_id": "person-qual",
             "clock_id": "clock-timeline",
             "samples": [
                 {"tick": 0, "geometry": {"x": 0.5, "y": 0.4},
                  "confidence": 0.9, "coordinate_space": "normalized"},
                 {"tick": 71, "geometry": {"x": 0.52, "y": 0.41},
                  "confidence": 0.9, "coordinate_space": "normalized"}
             ],
             "provenance_id": "prov-qual"}
        ))
        .unwrap(),
    );
    model.effects.push(
        serde_json::from_value(serde_json::json!(
            {"id": "effect-qual", "class_id": "color.saturation", "target_id": "use-first",
             "interval": {"clock_id": "clock-timeline", "start": 0, "end": 72},
             "parameters": {"saturation": 0.8}, "keyframes": []}
        ))
        .unwrap(),
    );

    // Plan level: all three loss codes must be present before any execution.
    let projection = plan_proj(&model).unwrap();
    let mut plan_codes: Vec<&str> = projection
        .losses
        .iter()
        .map(|loss| loss.code.as_str())
        .collect();
    plan_codes.sort();
    assert_eq!(
        plan_codes,
        vec![
            "effect_not_projected",
            "mask_not_projected",
            "object_track_not_projected"
        ],
        "a mask/effect/track-carrying model must plan all three losses"
    );
    let plan_json = serde_json::to_string(&projection.losses).unwrap();

    // Execution level: place the same model through the pinned CLI.
    let placed = place_through_adapter(&adapter, "adapter-loss-proof", &model);
    for (index, receipt_losses) in placed.placement_losses.iter().enumerate() {
        assert!(
            receipt_losses.is_empty(),
            "placement receipt {index} must add no driver loss, got {receipt_losses:#?}"
        );
    }

    // The proof: executed receipts equal the plan-level losses byte-identical,
    // so the engine run neither dropped nor invented a loss.
    let summary = summarize_losses(&projection, &placed.placement_losses);
    let summary_json = serde_json::to_string(&summary).unwrap();
    assert_eq!(
        summary_json, plan_json,
        "executed receipt losses must equal the plan-level losses byte-identical"
    );
}

/// Peak reaped-child RSS in MB. macOS reports `ru_maxrss` in bytes, Linux
/// in kilobytes. RUSAGE_CHILDREN accumulates over the whole test process,
/// so batches report the cumulative peak: honest for a report, unusable
/// as a gate, which is exactly the probe's contract.
fn peak_child_rss_mb() -> f64 {
    // SAFETY: getrusage with RUSAGE_CHILDREN only reads kernel counters
    // into the caller-provided struct.
    let mut usage: libc::rusage = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::getrusage(libc::RUSAGE_CHILDREN, &mut usage) };
    assert_eq!(rc, 0, "getrusage(RUSAGE_CHILDREN) must succeed");
    let maxrss = usage.ru_maxrss as f64;
    if cfg!(target_os = "macos") {
        maxrss / (1024.0 * 1024.0)
    } else {
        maxrss / 1024.0
    }
}

/// Export one placed timeline on `slots` parallel workers, each on its own
/// project-file copy (exports do not save, but parallel workers must never
/// share a project file). Returns batch wall seconds and per-slot bytes.
fn export_batch(adapter: &FilmCraft, project: &Path, dir: &Path, slots: usize) -> (f64, Vec<u64>) {
    let started = std::time::Instant::now();
    let results: Vec<(usize, u64)> = std::thread::scope(|scope| {
        let mut handles = Vec::new();
        for slot in 0..slots {
            let slot_project = dir.join(format!("concurrency-{slot}.fcproj"));
            std::fs::copy(project, &slot_project).unwrap();
            let out = dir.join(format!("concurrency-{slot}.mp4"));
            handles.push(scope.spawn(move || {
                let receipt = adapter
                    .execute(request(Operation::Export {
                        project: slot_project,
                        path: out.clone(),
                        format: ExportFormat::H264,
                        hardware_encoder: HardwareEncoding::Auto,
                    }))
                    .unwrap();
                assert!(receipt.engine_response["job"].is_u64());
                let bytes = validate_render_asset(&out).unwrap();
                (slot, bytes)
            }));
        }
        handles
            .into_iter()
            .map(|handle| handle.join().unwrap())
            .collect()
    });
    let mut sizes: Vec<u64> = vec![0; slots];
    for (slot, bytes) in results {
        sizes[slot] = bytes;
    }
    (started.elapsed().as_secs_f64(), sizes)
}

/// G9 concurrency probe: N=1 vs N=2 vs N=4 parallel exports of one placed
/// timeline, reporting wall time and peak child RSS. This REPORTS and never
/// gates: no assertion touches the scaling ratios, so a poorly scaling
/// engine turns the numbers, not a red test, into the record.
#[test]
fn concurrent_render_probe_reports_wall_and_rss_without_gating() {
    let adapter = FilmCraft::new().unwrap();
    let model = test_model(&linked_av_fixture(), true, false);
    let placed = place_through_adapter(&adapter, "adapter-concurrency", &model);

    let mut serial_wall = 0.0f64;
    let mut rows: Vec<(usize, f64, f64, Vec<u64>)> = Vec::new();
    for slots in [1usize, 2, 4] {
        let batch_dir = placed._temp.path().join(format!("batch-{slots}"));
        std::fs::create_dir_all(&batch_dir).unwrap();
        let (wall, sizes) = export_batch(&adapter, &placed.project, &batch_dir, slots);
        assert!(
            sizes.iter().all(|bytes| *bytes > 0),
            "every parallel export must land a non-empty render, got {sizes:?}"
        );
        let rss_mb = peak_child_rss_mb();
        if slots == 1 {
            serial_wall = wall;
        }
        rows.push((slots, wall, rss_mb, sizes));
    }
    for (slots, wall, rss_mb, sizes) in &rows {
        eprintln!(
            "[concurrency-probe] slots={slots} wall={wall:.2}s \
             per_render={:.2}s throughput_vs_serial={:.2}x \
             peak_child_rss={rss_mb:.1}MB bytes={sizes:?}",
            wall / *slots as f64,
            serial_wall * *slots as f64 / wall,
        );
    }
}
