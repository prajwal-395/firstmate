//! Unit 6 qualification: the Project Model -> FilmCraft projection drives the
//! pinned headless CLI end to end. Placement, trim, split, delete, ripple
//! delete, ripple, retime, transition, audio,
//! the native audio mix (stems, per-clip gain, rendered LUFS/peak/sync),
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
    add_audio_tracks_operation, add_tracks_operation, apply_captions_to_model,
    apply_effect_keyframes_to_model, apply_lut_to_model, apply_titles_to_model,
    apply_volume_automation_to_model, asset_path, build_frame_map, caption_operations,
    effect_key_operations, import_operation, lut_apply_operations, lut_import_operations,
    plan_captions, plan_edit, plan_effect_keyframes, plan_lut_effects, plan_projection, plan_titles,
    plan_volume_automation, projection_json, read_effect_keyframes, reconcile_clip_ids, render_span,
    required_video_tracks, summarize_losses, title_operations, validate_render_asset, verify_media,
    volume_operations, EditContext,
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
        let import = projection
            .imports
            .iter()
            .find(|import| &import.asset_id == asset)
            .unwrap_or_else(|| panic!("no import for asset {asset}"));
        assert_eq!(&asset_path(Path::new("/"), &import.uri), path);
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
    // Stem plans addressing strips past the fresh sequence's own A1-A3
    // grow the sequence before the first placement lands; plans that fit
    // run no growth op at all.
    if let Some(growth) = add_audio_tracks_operation(&project, &projection).unwrap() {
        let grown = adapter.execute(request(growth)).unwrap();
        assert!(
            grown.engine_response["audio"].is_array(),
            "track growth must report its minted audio tracks"
        );
    }
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
            bitrate_kbps: None,
            bitrate_mode: None,
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
            .any(|loss| loss.code == "effect_owned_by_effectcraft"),
        "EffectCraft-owned effects stay there as explicit ownership entries, got {:#?}",
        projection.losses
    );
}

use ren_filmcraft::adapter::{
    apply_ren_edit_to_model, build_frame_map as build_map, expand_retime_ramp,
    plan_projection as plan_proj, plan_ren_edit,
};

fn ren_edit_for(
    id: &str,
    operation: &str,
    target: &str,
    after: serde_json::Value,
) -> EditOperation {
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
        // Per-clip gain lands on the use's audio item and reports it.
        let landed = receipt.engine_response["gainDb"]
            .as_array()
            .expect("clip.audioGain reports its landed levels");
        assert!(
            landed.iter().any(|entry| entry["gainDb"].as_f64() == Some(-3.0)),
            "receipt must land -3 dB: {landed:?}"
        );
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
    spans.sort_by_key(|a| a.0);
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
fn ren_retime_ramp_expands_to_razors_plus_constant_retimes() {
    let adapter = FilmCraft::new().unwrap();
    // 72 + 72 fixture model; the ramp plays the first 36 frames doubled and
    // the next 36 halved: 36 + 36 becomes 18 + 72, follower shifts +18.
    let model = test_model(&linked_av_fixture(), true, true);
    let placed = place_through_adapter(&adapter, "ren-ramp-equiv", &model);
    let ramp = ren_edit_for(
        "edit-ren-ramp",
        "retime.segments",
        "use-first",
        serde_json::json!({"segments": [
            {"record_frame": 0, "percent": 200.0},
            {"record_frame": 36, "percent": 50.0},
        ]}),
    );
    let expanded = expand_retime_ramp(&model, &ramp).unwrap();
    assert_eq!(expanded.razor_frames, vec![36]);
    // One split, two link repairs for the severed second piece, one constant
    // retime per piece.
    assert_eq!(expanded.edits.len(), 5);
    // The unexpanded ramp still refuses planning by name: the expansion is
    // the only route onto the engine.
    let projection = plan_proj(&model).unwrap();
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let err = plan_ren_edit(&model, &projection, &ramp, &context).unwrap_err();
    assert!(format!("{err}").contains("retime_segments_not_supported"));

    // Drive the split first, re-projecting so the piece placement resolves
    // against the current model. The V1 razor alone reproduces the measured
    // linked-audio defect; the explicit audio razor balances it.
    let mut model = model;
    let mut clip_ids = placed.clip_ids.clone();
    for edit in expanded
        .edits
        .iter()
        .filter(|e| e.operation == "clip.split")
    {
        let projection = plan_proj(&model).unwrap();
        let context = EditContext {
            project: &placed.project,
            clip_ids: &clip_ids,
        };
        let operations = plan_ren_edit(&model, &projection, edit, &context).unwrap();
        assert_eq!(operations.len(), 2);
        for (index, operation) in operations.into_iter().enumerate() {
            let receipt = adapter.execute(request(operation)).unwrap();
            if index == 0 {
                assert!(
                    receipt
                        .losses
                        .iter()
                        .any(|loss| loss.code == "linked_audio_not_razored"),
                    "the V1-only razor must still surface the measured defect: {:#?}",
                    receipt.losses
                );
            } else {
                assert!(
                    receipt.losses.is_empty(),
                    "the explicit linked-audio razor must be clean: {:#?}",
                    receipt.losses
                );
            }
        }
        model = apply_ren_edit_to_model(&model, edit).unwrap();
    }
    // Read the post-razor engine clip ids off inspection by start frame and
    // address the Ren piece ids onto their video-plus-audio pairs: a razor
    // severs the A/V link on the second half (measured distinct link ids),
    // so each piece retime drives both ids in one call. Mirrors the split
    // proof's id read-back.
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    let track_ids = |kind: &str| -> Vec<(i64, u64)> {
        let mut ids: Vec<(i64, u64)> = inspection.engine_response[kind][0]["items"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| {
                (
                    item["startFrame"].as_i64().unwrap(),
                    item["clip"].as_u64().unwrap(),
                )
            })
            .collect();
        ids.sort();
        ids
    };
    let video = track_ids("video");
    let audio = track_ids("audio");
    assert_eq!(video.len(), 3);
    assert_eq!(audio.len(), 3);
    for piece in &expanded.pieces {
        let engine_video = video
            .iter()
            .find(|(start, _)| *start == piece.start_frame)
            .unwrap_or_else(|| panic!("no engine video clip starts at {}", piece.start_frame))
            .1;
        let engine_audio = audio
            .iter()
            .find(|(start, _)| *start == piece.start_frame)
            .unwrap_or_else(|| panic!("no engine audio clip starts at {}", piece.start_frame))
            .1;
        clip_ids.insert(piece.piece_id.clone(), vec![engine_video, engine_audio]);
    }
    // Drive link repairs then constant retimes in expansion order. The
    // severed piece's two link receipts must read false-then-true (the
    // measured toggle convergence: clear, then link); each retime receipt is
    // lossless and linked audio follows the speed. The intact first piece
    // carries no link edits.
    let mut link_calls = 0;
    for edit in expanded
        .edits
        .iter()
        .filter(|e| e.operation != "clip.split")
    {
        let projection = plan_proj(&model).unwrap();
        let context = EditContext {
            project: &placed.project,
            clip_ids: &clip_ids,
        };
        let operations = plan_ren_edit(&model, &projection, edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        for operation in operations {
            let receipt = adapter.execute(request(operation)).unwrap();
            if edit.operation == "clip.link" {
                link_calls += 1;
                let linked = receipt.engine_response["linked"].as_bool();
                assert_eq!(
                    linked,
                    Some(link_calls % 2 == 0),
                    "severed link repair must read false-then-true: {:#?}",
                    receipt.engine_response
                );
            } else {
                assert!(
                    receipt.losses.is_empty(),
                    "a clean piece retime reports no loss: {:#?}",
                    receipt.losses
                );
            }
        }
        model = apply_ren_edit_to_model(&model, edit).unwrap();
    }
    assert_eq!(link_calls, 2);
    // Equivalence: the engine timeline matches the ren-core-mutated model
    // frame-exact on both tracks, with per-piece speeds on the picture.
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&model),
    );
    assert_eq!(
        inspect_spans(&inspection.engine_response, "audio"),
        expected_spans(&model),
    );
    assert_eq!(
        inspect_speeds(&inspection.engine_response),
        vec![(0, 18, 2.0), (18, 72, 0.5), (90, 72, 1.0)],
    );
    let after_projection = plan_proj(&model).unwrap();
    let after_map = build_map(&model, &after_projection, "clock-render", "map-ramp").unwrap();
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
    assert_eq!(
        after_map.segments[1].rate,
        Some(ren_protocol::project_model::Rational {
            numerator: 1,
            denominator: 2
        })
    );
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 162);
}

/// Engine reverse flag per video span, frame-exact: (start, reversed).
fn inspect_reverse(inspection: &serde_json::Value, kind: &str) -> Vec<(i64, bool)> {
    let mut spans = Vec::new();
    for track in inspection[kind].as_array().unwrap() {
        for item in track["items"].as_array().unwrap() {
            spans.push((
                item["startFrame"].as_i64().unwrap(),
                item["reverse"].as_bool().unwrap(),
            ));
        }
    }
    spans.sort();
    spans
}

/// Average PSNR (dB) of `rev` against `fwd` under a direction hypothesis:
/// `reversed=false` compares frame-for-frame, `reversed=true` compares
/// against the reversed frame order. Shells out to ffmpeg's psnr filter;
/// ffmpeg is a declared requirement of this file, so its absence fails the
/// proof rather than passing vacuously.
fn psnr_average(rev: &Path, fwd: &Path, reversed: bool) -> f64 {
    let filter = if reversed {
        "[1:v]reverse[ra];[0:v][ra]psnr"
    } else {
        "[0:v][1:v]psnr"
    };
    let output = std::process::Command::new("ffmpeg")
        .args(["-v", "info", "-i"])
        .arg(rev)
        .args(["-i"])
        .arg(fwd)
        .args(["-lavfi", filter, "-f", "null", "-"])
        .output()
        .expect("ffmpeg must be on PATH for the render-direction proof");
    assert!(
        output.status.success(),
        "ffmpeg psnr comparison failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    let marker = "average:";
    let start = stderr
        .find(marker)
        .expect("ffmpeg psnr output carries an average")
        + marker.len();
    let rest = stderr[start..].trim_start();
    let end = rest
        .find(|c: char| c != '.' && !c.is_ascii_digit())
        .unwrap_or(rest.len());
    let number = &rest[..end];
    if number == "inf" {
        f64::INFINITY
    } else {
        number.parse().expect("psnr average parses as f64")
    }
}

#[test]
fn ren_reverse_plays_backwards_frame_exact() {
    let adapter = FilmCraft::new().unwrap();
    // Single 72-frame A/V clip: reversing at full speed changes nothing but
    // direction.
    let model = test_model(&linked_av_fixture(), true, false);
    let placed = place_through_adapter(&adapter, "ren-reverse", &model);
    // Forward control render first; the reversed render follows the retime.
    let forward_render = placed._temp.path().join("ren-reverse-fwd.mp4");
    let forward = adapter
        .execute(request(Operation::Export {
            project: placed.project.clone(),
            path: forward_render.clone(),
            format: ExportFormat::H264,
            hardware_encoder: HardwareEncoding::Off,
            bitrate_kbps: None,
            bitrate_mode: None,
        }))
        .unwrap();
    assert_eq!(
        forward.engine_response["result"]["frames"].as_u64(),
        Some(72)
    );
    let projection = plan_proj(&model).unwrap();
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let edit = ren_edit_for(
        "edit-ren-reverse",
        "retime",
        "use-first",
        serde_json::json!({"percent": 100.0, "reverse": true}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    assert_eq!(operations.len(), 1);
    for operation in operations {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(
            receipt.losses.is_empty(),
            "a clean reverse reports no loss: {:#?}",
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
    // Direction is set on both tracks; speed stays 1.0.
    for kind in ["video", "audio"] {
        assert_eq!(
            inspect_reverse(&inspection.engine_response, kind),
            vec![(0, true)],
            "{kind} must play backwards"
        );
    }
    assert_eq!(
        inspect_speeds(&inspection.engine_response),
        vec![(0, 72, 1.0)],
    );
    // The frame map carries the direction in the sign: -1/1.
    let after_projection = plan_proj(&mutated).unwrap();
    let after_map = build_map(&mutated, &after_projection, "clock-render", "map-rev").unwrap();
    assert_eq!(
        after_map.segments[0].mapping_kind,
        ren_protocol::project_model::MappingKind::Retimed
    );
    assert_eq!(
        after_map.segments[0].rate,
        Some(ren_protocol::project_model::Rational {
            numerator: -1,
            denominator: 1
        })
    );
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 72);
    // The render plays backwards: decoded frame order matches the reversed
    // source, not frame-for-frame. Bounds from the 2026-10-10 measurement
    // (61 vs 39 dB on this fixture) keep 10+ dB headroom; a static fixture
    // would tie and fail the margin, correctly unproven.
    let reversed_render = placed._temp.path().join("ren-reverse-rev.mp4");
    let reversed = adapter
        .execute(request(Operation::Export {
            project: placed.project.clone(),
            path: reversed_render.clone(),
            format: ExportFormat::H264,
            hardware_encoder: HardwareEncoding::Off,
            bitrate_kbps: None,
            bitrate_mode: None,
        }))
        .unwrap();
    assert_eq!(
        reversed.engine_response["result"]["frames"].as_u64(),
        Some(72)
    );
    let fwd_psnr = psnr_average(&reversed_render, &forward_render, false);
    let rev_psnr = psnr_average(&reversed_render, &forward_render, true);
    assert!(
        rev_psnr >= 50.0,
        "the reversed alignment must be near-identical, got {rev_psnr:.1} dB"
    );
    assert!(
        rev_psnr - fwd_psnr >= 10.0,
        "reversed {rev_psnr:.1} dB must beat frame-for-frame {fwd_psnr:.1} dB by 10 dB"
    );

    // Reversed at double speed halves spans exactly like forward.
    let model = test_model(&linked_av_fixture(), true, true);
    let placed = place_through_adapter(&adapter, "ren-reverse-fast", &model);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let projection = plan_proj(&model).unwrap();
    let edit = ren_edit_for(
        "edit-ren-reverse-fast",
        "retime",
        "use-first",
        serde_json::json!({"percent": 200.0, "reverse": true}),
    );
    let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
    assert_eq!(operations.len(), 1);
    for operation in operations {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(
            receipt.losses.is_empty(),
            "a clean reversed speed-up reports no loss: {:#?}",
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
    for kind in ["video", "audio"] {
        assert_eq!(
            inspect_reverse(&inspection.engine_response, kind),
            vec![(0, true), (36, false)],
            "{kind} must reverse only the first clip"
        );
    }
    assert_eq!(
        inspect_speeds(&inspection.engine_response),
        vec![(0, 36, 2.0), (36, 72, 1.0)],
    );
    let after_projection = plan_proj(&mutated).unwrap();
    let after_map = build_map(&mutated, &after_projection, "clock-render", "map-rev-fast").unwrap();
    assert_eq!(
        after_map.segments[0].rate,
        Some(ren_protocol::project_model::Rational {
            numerator: -2,
            denominator: 1
        })
    );
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 108);
}

#[test]
fn ren_retime_ramp_with_reversed_piece_matches_model_frame_exact() {
    let adapter = FilmCraft::new().unwrap();
    // 72 + 72 fixture model; the ramp plays the first 36 frames doubled
    // forwards and the next 36 halved backwards: 36 + 36 becomes 18 + 72.
    let model = test_model(&linked_av_fixture(), true, true);
    let placed = place_through_adapter(&adapter, "ren-ramp-rev", &model);
    let ramp = ren_edit_for(
        "edit-ren-ramp-rev",
        "retime.segments",
        "use-first",
        serde_json::json!({"segments": [
            {"record_frame": 0, "percent": 200.0},
            {"record_frame": 36, "percent": 50.0, "reverse": true},
        ]}),
    );
    let expanded = expand_retime_ramp(&model, &ramp).unwrap();
    assert_eq!(expanded.razor_frames, vec![36]);
    assert_eq!(expanded.edits.len(), 5);
    assert!(expanded.pieces[1].reverse);

    let mut model = model;
    let mut clip_ids = placed.clip_ids.clone();
    for edit in expanded.edits.iter().filter(|e| e.operation == "clip.split") {
        let projection = plan_proj(&model).unwrap();
        let context = EditContext {
            project: &placed.project,
            clip_ids: &clip_ids,
        };
        let operations = plan_ren_edit(&model, &projection, edit, &context).unwrap();
        assert_eq!(operations.len(), 2);
        for operation in operations {
            adapter.execute(request(operation)).unwrap();
        }
        model = apply_ren_edit_to_model(&model, edit).unwrap();
    }
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    let track_ids = |kind: &str| -> Vec<(i64, u64)> {
        let mut ids: Vec<(i64, u64)> = inspection.engine_response[kind][0]["items"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| {
                (
                    item["startFrame"].as_i64().unwrap(),
                    item["clip"].as_u64().unwrap(),
                )
            })
            .collect();
        ids.sort();
        ids
    };
    let video = track_ids("video");
    let audio = track_ids("audio");
    for piece in &expanded.pieces {
        let engine_video = video
            .iter()
            .find(|(start, _)| *start == piece.start_frame)
            .unwrap()
            .1;
        let engine_audio = audio
            .iter()
            .find(|(start, _)| *start == piece.start_frame)
            .unwrap()
            .1;
        clip_ids.insert(piece.piece_id.clone(), vec![engine_video, engine_audio]);
    }
    let mut link_calls = 0;
    for edit in expanded
        .edits
        .iter()
        .filter(|e| e.operation != "clip.split")
    {
        let projection = plan_proj(&model).unwrap();
        let context = EditContext {
            project: &placed.project,
            clip_ids: &clip_ids,
        };
        let operations = plan_ren_edit(&model, &projection, edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        for operation in operations {
            let receipt = adapter.execute(request(operation)).unwrap();
            if edit.operation == "clip.link" {
                link_calls += 1;
                assert_eq!(
                    receipt.engine_response["linked"].as_bool(),
                    Some(link_calls % 2 == 0),
                    "severed link repair must read false-then-true: {:#?}",
                    receipt.engine_response
                );
            } else {
                assert!(
                    receipt.losses.is_empty(),
                    "a clean piece retime reports no loss: {:#?}",
                    receipt.losses
                );
            }
        }
        model = apply_ren_edit_to_model(&model, edit).unwrap();
    }
    assert_eq!(link_calls, 2);
    // Equivalence on both tracks: spans match, only the second piece plays
    // backwards, speeds read per piece.
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&model),
    );
    assert_eq!(
        inspect_spans(&inspection.engine_response, "audio"),
        expected_spans(&model),
    );
    for kind in ["video", "audio"] {
        assert_eq!(
            inspect_reverse(&inspection.engine_response, kind),
            vec![(0, false), (18, true), (90, false)],
            "{kind} must reverse only the second piece"
        );
    }
    assert_eq!(
        inspect_speeds(&inspection.engine_response),
        vec![(0, 18, 2.0), (18, 72, 0.5), (90, 72, 1.0)],
    );
    let after_projection = plan_proj(&model).unwrap();
    let after_map = build_map(&model, &after_projection, "clock-render", "map-ramp-rev").unwrap();
    assert_eq!(
        after_map.segments[0].rate,
        Some(ren_protocol::project_model::Rational {
            numerator: 2,
            denominator: 1
        })
    );
    assert_eq!(
        after_map.segments[1].rate,
        Some(ren_protocol::project_model::Rational {
            numerator: -1,
            denominator: 2
        })
    );
    assert_eq!(ren_filmcraft::adapter::render_span(&after_map), 162);
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

/// Pin the sequence to the model's 24fps timeline rate. The adapter plans
/// every tick in engine ticks at that rate; on the default 23.976 preset
/// the engine frame-floors tick writes (measured), so the pin keeps all
/// readback below verbatim instead of rate-rounded.
fn pin_24fps(adapter: &FilmCraft, project: &Path) {
    let receipt = adapter
        .execute(request(Operation::SequenceSettings {
            project: project.into(),
            fps: 24.0,
        }))
        .unwrap();
    assert_eq!(receipt.engine_response["scaledClips"].as_u64(), Some(0));
}

fn push_observation(model: &mut ProjectModel, id: &str, clock: &str, start: i64, end: i64, modality: &str, text: &str) {
    model.observations.push(
        serde_json::from_value(serde_json::json!(
            {"id": id, "asset_id": "asset-av",
             "interval": {"clock_id": clock, "start": start, "end": end},
             "modality": modality, "payload": {"text": text},
             "confidence": 1.0, "provenance_id": "prov-qual"}
        ))
        .unwrap(),
    );
}

fn push_effect(model: &mut ProjectModel, id: &str, class: &str, target: &str, params: serde_json::Value, keys: serde_json::Value) {
    model.effects.push(
        serde_json::from_value(serde_json::json!(
            {"id": id, "class_id": class, "target_id": target,
             "interval": {"clock_id": "clock-timeline", "start": 0, "end": 72},
             "parameters": params, "keyframes": keys}
        ))
        .unwrap(),
    );
}

#[test]
fn captions_and_titles_place_frame_exact_with_readback() {
    let adapter = FilmCraft::new().unwrap();
    let mut model = test_model(&linked_av_fixture(), false, false);
    push_observation(&mut model, "obs-hello", "clock-source", 0, 24, "speech", "Hello");
    push_observation(&mut model, "obs-world", "clock-source", 24, 48, "speech", "World");
    push_observation(&mut model, "obs-title", "clock-timeline", 0, 48, "title", "Opening");
    let projection = plan_projection(&model).unwrap();
    assert!(projection.losses.is_empty(), "captions and titles execute, they are never losses: {:#?}", projection.losses);

    let cues = plan_captions(&model).unwrap();
    assert_eq!(cues.len(), 2);
    assert_eq!(apply_captions_to_model(&model, &cues).unwrap().observations.len(), 3);
    let titles = plan_titles(&model, &projection.placements).unwrap();
    assert_eq!(titles.len(), 1);
    assert_eq!(
        apply_titles_to_model(&model, &projection.placements, &titles)
            .unwrap()
            .observations
            .len(),
        3
    );

    let placed = place_through_adapter(&adapter, "captions-titles", &model);
    pin_24fps(&adapter, &placed.project);

    let track = adapter
        .execute(request(Operation::CreateCaptionTrack {
            project: placed.project.clone(),
            format: "Subtitle".into(),
            name: Some("Subs".into()),
        }))
        .unwrap();
    let track_id = track.engine_response["track"].as_u64().unwrap();
    for operation in caption_operations(&placed.project, Some("C1".into()), &cues) {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(receipt.engine_response["caption"].is_u64());
    }
    let listed = adapter
        .execute(request(Operation::ListCaptions {
            project: placed.project.clone(),
            track: None,
        }))
        .unwrap();
    let items = listed.engine_response["tracks"][0]["captions"].as_array().unwrap();
    assert_eq!(items.len(), 2);
    for (item, cue) in items.iter().zip(cues.iter()) {
        assert_eq!(item["text"].as_str(), Some(cue.text.as_str()));
        assert_eq!(item["start"].as_i64(), Some(cue.start_ticks), "caption start must read back verbatim at 24fps");
        assert_eq!(
            item["end"].as_i64(),
            Some(cue.start_ticks + cue.duration_ticks),
            "caption end must read back verbatim at 24fps"
        );
    }

    // A text/timing revision round-trips through the same readback.
    let first = items[0]["id"].as_u64().unwrap();
    adapter
        .execute(request(Operation::SetCaptionText {
            project: placed.project.clone(),
            caption: first,
            text: "Hello edited".into(),
            speaker: None,
        }))
        .unwrap();
    adapter
        .execute(request(Operation::SetCaptionTimes {
            project: placed.project.clone(),
            caption: first,
            start_ticks: 0,
            end_ticks: 254_016_000_000,
        }))
        .unwrap();
    let relisted = adapter
        .execute(request(Operation::ListCaptions {
            project: placed.project.clone(),
            track: None,
        }))
        .unwrap();
    let edited = relisted.engine_response["tracks"][0]["captions"][0].clone();
    assert_eq!(edited["text"].as_str(), Some("Hello edited"));
    assert_eq!(edited["start"].as_i64(), Some(0));
    assert_eq!(edited["end"].as_i64(), Some(254_016_000_000));
    assert_eq!(
        listed.engine_response["tracks"][0]["id"].as_u64(),
        Some(track_id),
        "the created track must be the one that lists back"
    );

    // Titles bind to the host clip and read back frame-exact.
    let title_ops = title_operations(&placed.project, &placed.clip_ids, &titles).unwrap();
    assert_eq!(title_ops.len(), 1);
    let receipt = adapter.execute(request(title_ops.into_iter().next().unwrap())).unwrap();
    let title_clip = receipt.engine_response["clip"].as_u64().unwrap();
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    let titled = inspection.engine_response["video"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|track| track["items"].as_array().unwrap().iter())
        .find(|item| item["clip"].as_u64() == Some(title_clip))
        .cloned()
        .expect("the title item must be on the engine timeline");
    assert_eq!(titled["name"].as_str(), Some("Opening"));
    assert_eq!(titled["startFrame"].as_i64(), Some(0));
    assert_eq!(titled["durationFrames"].as_i64(), Some(48));
}

#[test]
fn lut_builtin_and_file_import_apply_with_readback() {
    let adapter = FilmCraft::new().unwrap();
    let mut model = test_model(&linked_av_fixture(), false, false);
    push_effect(
        &mut model,
        "effect-lut-builtin",
        "color.lut",
        "use-first",
        serde_json::json!({"lut": "builtin:logc3-awg3-to-rec709"}),
        serde_json::json!([]),
    );
    let projection = plan_projection(&model).unwrap();
    assert!(projection.losses.is_empty(), "a projectable LUT is executed, never a loss: {:#?}", projection.losses);
    let plans = plan_lut_effects(&model, &projection.placements).unwrap();
    assert_eq!(plans.len(), 1);
    assert!(plans[0].is_reference());
    assert_eq!(
        apply_lut_to_model(&model, &projection.placements, &plans)
            .unwrap()
            .effects
            .len(),
        1
    );
    assert!(lut_import_operations(Path::new("test.fcproj"), &plans).is_empty());

    let placed = place_through_adapter(&adapter, "lut-apply", &model);
    pin_24fps(&adapter, &placed.project);
    let applies = lut_apply_operations(&placed.project, &placed.clip_ids, &plans, &BTreeMap::new()).unwrap();
    assert_eq!(applies.len(), 1);
    let receipt = adapter.execute(request(applies.into_iter().next().unwrap())).unwrap();
    assert_eq!(receipt.engine_response["lut"].as_str(), Some("builtin:logc3-awg3-to-rec709"));
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    let video_item = &inspection.engine_response["video"][0]["items"][0];
    let lumetri = video_item["effects"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["effect"].as_str() == Some("lumetri"))
        .expect("applying a LUT must attach Lumetri to the clip");
    assert!(
        lumetri["params"]["input_lut"]["value"].as_str().unwrap_or("").contains("builtin:logc3-awg3-to-rec709"),
        "the Lumetri input LUT must read back the applied ref: {}",
        lumetri["params"]["input_lut"]
    );

    // File LUTs import in a second phase: the receipt mints the ref the
    // apply then drives, and the same inspect path reads it back.
    let cube = placed._temp.path().join("qual.cube");
    std::fs::write(
        &cube,
        "TITLE \"qual\"\nLUT_3D_SIZE 2\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n0.0 0.0 0.0\n0.0 0.0 1.0\n0.0 1.0 0.0\n0.0 1.0 1.0\n1.0 0.0 0.0\n1.0 0.0 1.0\n1.0 1.0 0.0\n1.0 1.0 1.0\n",
    )
    .unwrap();
    let mut filed = test_model(&linked_av_fixture(), false, false);
    push_effect(
        &mut filed,
        "effect-lut-file",
        "color.lut",
        "use-first",
        serde_json::json!({"lut": cube.to_str().unwrap()}),
        serde_json::json!([]),
    );
    let filed_projection = plan_projection(&filed).unwrap();
    let filed_plans = plan_lut_effects(&filed, &filed_projection.placements).unwrap();
    assert!(!filed_plans[0].is_reference());
    let placed = place_through_adapter(&adapter, "lut-file", &filed);
    pin_24fps(&adapter, &placed.project);
    let imports = lut_import_operations(&placed.project, &filed_plans);
    assert_eq!(imports.len(), 1);
    let imported = adapter.execute(request(imports.into_iter().next().unwrap())).unwrap();
    let minted = imported.engine_response["ref"].as_str().unwrap().to_owned();
    assert!(minted.starts_with("lib:"), "file imports mint a library ref, got {minted}");
    let listed = adapter
        .execute(request(Operation::ListLuts {
            project: placed.project.clone(),
        }))
        .unwrap();
    assert!(
        listed.engine_response["library"].as_array().unwrap().iter().any(|e| e["ref"].as_str() == Some(minted.as_str())),
        "the imported LUT must list back: {}",
        listed.engine_response
    );
    let refs = BTreeMap::from([("effect-lut-file".to_owned(), minted.clone())]);
    let applies = lut_apply_operations(&placed.project, &placed.clip_ids, &filed_plans, &refs).unwrap();
    let receipt = adapter.execute(request(applies.into_iter().next().unwrap())).unwrap();
    assert_eq!(receipt.engine_response["lut"].as_str(), Some(minted.as_str()));
}

#[test]
fn keyframed_picture_keys_read_back_exact_and_out_of_span_refuses() {
    let adapter = FilmCraft::new().unwrap();
    let mut model = test_model(&linked_av_fixture(), false, false);
    push_effect(
        &mut model,
        "effect-pos",
        "transform.position",
        "use-first",
        serde_json::json!({}),
        serde_json::json!([{"tick": 0, "value": [100.0, 200.0]}, {"tick": 36, "value": [300.0, 400.0]}]),
    );
    push_effect(
        &mut model,
        "effect-opacity",
        "opacity.opacity",
        "use-first",
        serde_json::json!({}),
        serde_json::json!([{"tick": 0, "value": 30.0}, {"tick": 36, "value": 80.0}]),
    );
    push_effect(
        &mut model,
        "effect-crop",
        "transform.crop",
        "use-first",
        serde_json::json!({"left": 5.0, "top": 0.0, "right": 5.0, "bottom": 0.0}),
        serde_json::json!([]),
    );
    let projection = plan_projection(&model).unwrap();
    assert!(projection.losses.is_empty(), "projectable picture effects execute, they are never losses: {:#?}", projection.losses);
    let plans = plan_effect_keyframes(&model, &projection.placements).unwrap();
    assert_eq!(plans.len(), 3);
    assert_eq!(
        apply_effect_keyframes_to_model(&model, &projection.placements, &plans)
            .unwrap()
            .effects
            .len(),
        3
    );

    let placed = place_through_adapter(&adapter, "picture-keys", &model);
    pin_24fps(&adapter, &placed.project);
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let operations = effect_key_operations(&placed.project, context.clip_ids, &plans).unwrap();
    for operation in operations {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert!(receipt.losses.is_empty(), "exact key writes report no loss: {:#?}", receipt.losses);
    }
    let video_clip = ren_filmcraft::adapter::primary_clip_id(&placed.clip_ids["use-first"], "use-first").unwrap();

    // Exact media-time readback from the project file: timeline ticks at
    // 24fps are media ticks for a clip placed at zero with sourceIn zero.
    let position = read_effect_keyframes(&placed.project, video_clip, "motion", "position").unwrap();
    assert_eq!(
        position,
        vec![
            (0, serde_json::json!([100.0, 200.0])),
            (381_024_000_000, serde_json::json!([300.0, 400.0])),
        ]
    );
    let opacity = read_effect_keyframes(&placed.project, video_clip, "opacity", "opacity").unwrap();
    assert_eq!(
        opacity,
        vec![(0, serde_json::json!(30.0)), (381_024_000_000, serde_json::json!(80.0))]
    );
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    let video_item = &inspection.engine_response["video"][0]["items"][0];
    let crop = video_item["effects"]
        .as_array()
        .unwrap()
        .iter()
        .find(|e| e["effect"].as_str() == Some("crop"))
        .expect("the static crop must be applied to the clip");
    for (edge, want) in [("left", 5.0), ("top", 0.0), ("right", 5.0), ("bottom", 0.0)] {
        let rendered = crop["params"][edge]["value"].as_str().unwrap_or("");
        let parsed: f64 = rendered
            .strip_prefix("Float(")
            .and_then(|s| s.strip_suffix(")"))
            .and_then(|s| s.parse().ok())
            .expect("crop edges read back as Float values");
        assert_eq!(parsed, want, "crop edge {edge} must read back exact");
    }

    // The out-of-span guard: a key past the clip end refuses at planning
    // time with its named code. The engine would answer such a write with
    // success while placing nothing (measured), so this refusal is the
    // compensation - nothing out-of-span ever executes.
    let mut spanned = test_model(&linked_av_fixture(), false, false);
    push_effect(
        &mut spanned,
        "effect-far",
        "transform.scale",
        "use-first",
        serde_json::json!({}),
        serde_json::json!([{"tick": 0, "value": 100.0}, {"tick": 200, "value": 150.0}]),
    );
    // Widen the effect interval past the clip so the clip-span guard is
    // the one that fires, not the interval guard.
    spanned.effects[0].interval.end = 300;
    // Fail-closed: the whole projection refuses with the named code, so
    // nothing out-of-span ever executes.
    let err = plan_projection(&spanned).unwrap_err();
    assert!(
        format!("{err}").contains("keyframe_out_of_span"),
        "the projection must refuse out-of-span keys by name, got {err}"
    );
    let spanned_projection = plan_projection(&test_model(&linked_av_fixture(), false, false)).unwrap();
    let err = plan_effect_keyframes(&spanned, &spanned_projection.placements).unwrap_err();
    assert!(
        format!("{err}").contains("keyframe_out_of_span"),
        "out-of-span keys must refuse by name, got {err}"
    );
    let model_err = apply_effect_keyframes_to_model(&spanned, &spanned_projection.placements, &[])
        .unwrap_err();
    assert!(
        format!("{model_err}").contains("keyframe_out_of_span"),
        "the apply counterpart must refuse the same content, got {model_err}"
    );
}

#[test]
fn volume_automation_writes_and_reads_back_exact() {
    let adapter = FilmCraft::new().unwrap();
    let mut model = test_model(&linked_av_fixture(), true, false);
    push_effect(
        &mut model,
        "effect-vol",
        "audio.volume",
        "use-first",
        serde_json::json!({}),
        serde_json::json!([{"tick": 0, "value": -3.0}, {"tick": 36, "value": -6.0}]),
    );
    let projection = plan_projection(&model).unwrap();
    assert!(projection.losses.is_empty(), "volume automation executes, it is never a loss: {:#?}", projection.losses);
    let plans = plan_volume_automation(&model, &projection.placements).unwrap();
    assert_eq!(plans.len(), 1);
    assert_eq!(plans[0].strip, "A1");
    assert_eq!(
        apply_volume_automation_to_model(&model, &projection.placements, &plans)
            .unwrap()
            .effects
            .len(),
        1
    );

    let placed = place_through_adapter(&adapter, "volume-auto", &model);
    pin_24fps(&adapter, &placed.project);
    for operation in volume_operations(&placed.project, &plans) {
        let receipt = adapter.execute(request(operation)).unwrap();
        assert_eq!(receipt.engine_response["points"].as_u64(), Some(2));
    }
    let mixer = adapter
        .execute(request(Operation::InspectMixer {
            project: placed.project.clone(),
        }))
        .unwrap();
    let strip = mixer.engine_response["strips"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["ref"].as_str() == Some("A1"))
        .expect("A1 must be in the mixer readback")
        .clone();
    assert_eq!(
        strip["lanes"]["volume"],
        serde_json::json!([[0i64, -3.0], [381_024_000_000i64, -6.0]]),
        "automation lanes must read back tick-exact"
    );
}

/// G9 loss proof: a mask/effect/track-carrying model is executed through
/// the pinned CLI, and the executed receipt losses must equal the
/// plan-level losses byte-identical. Plan-level-only assertions (see the
/// `effect_owned_by_effectcraft` check in
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
            "effect_owned_by_effectcraft",
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
                        bitrate_kbps: None,
                        bitrate_mode: None,
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

/// Overlay fixtures: a green H264 base plus a ProRes 4444 overlay carrying
/// an opaque white box on a fully transparent canvas (the shape of a
/// caption file: ink over nothing). Generated by ffmpeg in the test temp
/// dir, so no binary fixtures are committed for overlay coverage. Both are
/// cut at the fresh project's own 1920x1080 frame: the adapter's create
/// path takes no dimensions, so pixel proofs must meet the default
/// sequence where it is.
fn overlay_qualification_media(dir: &Path) -> (PathBuf, PathBuf) {
    let base = dir.join("overlay-base.mp4");
    let overlay = dir.join("overlay-box.mov");
    let status = std::process::Command::new("ffmpeg")
        .args(["-y", "-v", "error"])
        .args(["-f", "lavfi", "-i", "color=c=green:s=1920x1080:r=24:d=1"])
        .args(["-pix_fmt", "yuv420p"])
        .arg(&base)
        .status()
        .expect("ffmpeg must be on PATH for overlay qualification");
    assert!(status.success(), "base fixture must generate");
    let status = std::process::Command::new("ffmpeg")
        .args(["-y", "-v", "error"])
        .args([
            "-f",
            "lavfi",
            "-i",
            "color=c=black@0:s=1920x1080:r=24:d=1,format=yuva444p10le",
        ])
        .args([
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=480x480:r=24:d=1,format=yuva444p10le",
        ])
        .args([
            "-filter_complex",
            "[0][1]overlay=720:300:format=yuv444",
        ])
        .args(["-c:v", "prores_ks", "-pix_fmt", "yuva444p10le", "-profile:v", "4444"])
        .arg(&overlay)
        .status()
        .expect("ffmpeg must be on PATH for overlay qualification");
    assert!(status.success(), "overlay fixture must generate");
    (base, overlay)
}

/// A 24-frame base use with subtitle and motion-graphic uses composited
/// over its full span: the Reel 22 shape (captions over dialogue, graphics
/// over captions) at fixture scale.
fn overlay_model(base: &Path, overlay: &Path) -> ProjectModel {
    let document = serde_json::json!({
        "id": "adapter-qualification-overlay",
        "schema_version": 1,
        "timebase_policy": "rational_frames",
        "clocks": [
            {"id": "clock-source", "domain": {"kind": "source", "asset_id": "asset-base", "stream_id": "stream-video"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
            {"id": "clock-timeline", "domain": {"kind": "project"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
            {"id": "clock-render", "domain": {"kind": "render", "render_id": "render-1"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}}
        ],
        "assets": [
            {"id": "asset-base", "kind": "video", "uri": base.to_str().unwrap(), "content_digest": null, "metadata": {}, "provenance": ["prov-qual"]},
            {"id": "asset-overlay", "kind": "video", "uri": overlay.to_str().unwrap(), "content_digest": null, "metadata": {}, "provenance": ["prov-qual"]}
        ],
        "streams": [
            {"id": "stream-base", "asset_id": "asset-base", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}},
            {"id": "stream-overlay", "asset_id": "asset-overlay", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}}
        ],
        "entities": [], "observations": [], "identity_links": [], "masks": [],
        "object_classes": [], "tracks": [], "relations": [], "intents": [],
        "clip_uses": [
            {"id": "use-base", "asset_id": "asset-base",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 24},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
             "entity_links": [], "role": "a_roll", "intent_id": null},
            {"id": "use-sub", "asset_id": "asset-overlay",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 24},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
             "entity_links": [], "role": "subtitle_overlay", "intent_id": null},
            {"id": "use-mg", "asset_id": "asset-overlay",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 24},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
             "entity_links": [], "role": "motion_graphic", "intent_id": null}
        ],
        "edits": [], "effects": [], "renders": [], "frame_maps": [],
        "nle_bindings": [], "receipts": [],
        "provenance": [{"id": "prov-qual", "producer": "adapter-qualification", "producer_version": null, "source_digest": null, "recorded_at": null}]
    });
    decode_project_model(&document.to_string()).expect("overlay model decodes")
}

/// Dominant RGB pixel of one cropped region of one export frame: robust to
/// encoder noise where byte equality would be brittle.
fn region_dominant(path: &Path, frame: i64, crop: &str) -> (u8, u8, u8) {
    let output = std::process::Command::new("ffmpeg")
        .args(["-y", "-v", "error", "-i"])
        .arg(path)
        .args(["-vf", &format!("select=eq(n\\,{frame}),crop={crop}")])
        .args(["-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
        .output()
        .expect("ffmpeg must be on PATH for overlay pixel proofs");
    assert!(
        output.status.success(),
        "region crop must decode: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let bytes = output.stdout;
    assert!(!bytes.is_empty() && bytes.len() % 3 == 0);
    let mut counts = BTreeMap::new();
    for pixel in bytes.chunks_exact(3) {
        *counts.entry((pixel[0], pixel[1], pixel[2])).or_insert(0u32) += 1;
    }
    counts
        .into_iter()
        .max_by_key(|(_, count)| *count)
        .map(|(pixel, _)| pixel)
        .unwrap()
}

fn export_project(
    adapter: &FilmCraft,
    project: &Path,
    name: &str,
    out: PathBuf,
) -> serde_json::Value {
    let receipt = adapter
        .execute(request(Operation::Export {
            project: project.into(),
            path: out.clone(),
            format: ExportFormat::H264,
            hardware_encoder: HardwareEncoding::Auto,
            bitrate_kbps: None,
            bitrate_mode: None,
        }))
        .unwrap();
    assert!(receipt.engine_response["job"].is_u64(), "{name} must export");
    validate_render_asset(&out).unwrap();
    receipt.engine_response
}

#[test]
fn overlay_roles_place_frame_exact_and_composite_alpha() {
    let adapter = FilmCraft::new().unwrap();
    let temp = tempfile::tempdir().unwrap();
    let (base_media, overlay_media) = overlay_qualification_media(temp.path());
    let model = overlay_model(&base_media, &overlay_media);
    let projection = plan_projection(&model).unwrap();
    assert_eq!(projection.placements.len(), 3);
    assert_eq!(
        projection
            .placements
            .iter()
            .map(|p| p.track.as_str())
            .collect::<Vec<_>>(),
        vec!["V1", "V4", "V3"]
    );
    assert!(
        projection
            .losses
            .iter()
            .any(|loss| loss.code == "overlay_unpositioned"),
        "compositing overlays must carry the positioning loss: {:#?}",
        projection.losses
    );
    // The real ffprobe gate passes ProRes-with-alpha overlays.
    let media = verify_media(Path::new("/"), &projection).unwrap();
    assert_eq!(media.len(), 2);
    // V4 exceeds the fresh project's three video tracks: the provisioning
    // op is part of the plan, driven once after create.
    assert_eq!(required_video_tracks(&projection), 4);

    let project = temp.path().join("overlay-roles.fcproj");
    adapter
        .execute(request(Operation::CreateProject {
            path: project.clone(),
            name: "overlay-roles".into(),
        }))
        .unwrap();
    let tracks_op =
        add_tracks_operation(&project, &projection).expect("V4 needs provisioning");
    let tracks_receipt = adapter.execute(request(tracks_op)).unwrap();
    assert_eq!(
        tracks_receipt.engine_response["video"]
            .as_array()
            .unwrap()
            .len(),
        1,
        "one video track must be added: {}",
        tracks_receipt.engine_response
    );
    let imported = adapter
        .execute(request(import_operation(
            project.clone(),
            &media,
            &projection,
        )))
        .unwrap();
    let engine_items: Vec<u64> = imported.engine_response["items"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap())
        .collect();
    let items: BTreeMap<String, u64> = projection
        .imports
        .iter()
        .zip(engine_items)
        .map(|(import, item)| (import.asset_id.clone(), item))
        .collect();
    let mut clips_per_receipt = Vec::new();
    for (_, operation) in
        ren_filmcraft::adapter::placement_operations(&project, &items, &projection).unwrap()
    {
        let receipt = adapter.execute(request(operation)).unwrap();
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
    assert_eq!(clip_ids.len(), 3);

    // Equivalence: the engine timeline matches the model frame-exact on
    // every track, overlays included.
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: project.clone(),
        }))
        .unwrap();
    assert_eq!(
        inspect_spans(&inspection.engine_response, "video"),
        expected_spans(&model),
    );
    // The frame map still packs the spine alone: overlays add no segments
    // and no render time.
    let map = build_frame_map(&model, &projection, "clock-render", "map-overlay").unwrap();
    assert_eq!(map.segments.len(), 1);
    assert_eq!(render_span(&map), 24);

    // Alpha proof: the composited export draws the overlay's opaque box
    // over the base and shows the base through its transparency. The
    // overlay box sits at 720,300+480x480; the corner is always base.
    let full_render = temp.path().join("overlay-full.mp4");
    export_project(&adapter, &project, "overlay-full", full_render.clone());
    let (box_r, box_g, box_b) = region_dominant(&full_render, 12, "480:480:720:300");
    assert!(
        box_r.min(box_g).min(box_b) > 170,
        "the overlay box must draw near-white over the base, got ({box_r},{box_g},{box_b})"
    );
    let (corner_r, corner_g, corner_b) = region_dominant(&full_render, 12, "10:10:5:5");
    assert!(
        corner_g > 80 && corner_g > corner_r + 40 && corner_g > corner_b + 40,
        "overlay transparency must show the green base, got ({corner_r},{corner_g},{corner_b})"
    );
}

#[test]
fn overlay_qtrle_refuses_at_verify_media_with_real_ffprobe() {
    let temp = tempfile::tempdir().unwrap();
    let qtrle = temp.path().join("overlay-qtrle.mov");
    let status = std::process::Command::new("ffmpeg")
        .args(["-y", "-v", "error"])
        .args(["-f", "lavfi", "-i", "color=c=white:s=160x120:r=24:d=1,format=rgba"])
        .args(["-c:v", "qtrle"])
        .arg(&qtrle)
        .status()
        .expect("ffmpeg must be on PATH for overlay qualification");
    assert!(status.success(), "qtrle fixture must generate");
    let (base_media, _) = overlay_qualification_media(temp.path());
    let mut model = overlay_model(&base_media, &qtrle);
    // Point the overlay asset at the qtrle file (the base stays H264).
    model.assets[1].uri = qtrle.to_str().unwrap().into();
    let projection = plan_projection(&model).unwrap();
    // Planning succeeds (the planner is pure); the media boundary refuses
    // what the engine decodes to nothing.
    let err = verify_media(Path::new("/"), &projection).unwrap_err();
    match err {
        ren_filmcraft::adapter::AdapterError::Unsupported { code, detail } => {
            assert_eq!(code, "overlay_codec_not_supported");
            assert!(detail.contains("qtrle"), "refusal must name the probe: {detail}");
        }
        other => panic!("qtrle overlays must refuse at verify_media, got {other:?}"),
    }
}


// ── Native audio mix: per-clip gain + bed/SFX stems, measured ──
//
// Ren's declared mix intent (per-clip dB, bed/SFX stems on their own
// strips, after `library/tools/otio_mix.py` and `music_bed.py`) renders
// through FilmCraft: the bed pair overlaps like a declared crossfade
// (A2+A3), the SFX hit starts above the bed (A4, via track growth), and
// each clip carries a static gain. This test measures the RENDERED mix -
// LUFS per soloed stem, true peak, sync - against that intent, and proves
// the model equivalence (logged edits re-plan identically, inspected
// `gainDb` equals the log). Keyframed ducking is out of scope until the
// volume-automation op lands; the static bed plays unducked here.

fn mix_bed_fixture() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/mix-bed.wav")
}

fn mix_sfx_fixture() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/mix-sfx.wav")
}

/// Dialogue (3s A/V) plus a crossfaded bed pair plus one SFX hit, on the
/// same 24fps clocks as `test_model` (one tick per frame).
fn mix_model(av: &Path, bed: &Path, sfx: &Path) -> ProjectModel {
    let document = serde_json::json!({
        "id": "adapter-qualification-mix",
        "schema_version": 1,
        "timebase_policy": "rational_frames",
        "clocks": [
            {"id": "clock-source", "domain": {"kind": "source", "asset_id": "asset-av", "stream_id": "stream-video"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
            {"id": "clock-timeline", "domain": {"kind": "project"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
            {"id": "clock-render", "domain": {"kind": "render", "render_id": "render-1"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}}
        ],
        "assets": [
            {"id": "asset-av", "kind": "video", "uri": av.to_str().unwrap(), "content_digest": null, "metadata": {}, "provenance": ["prov-qual"]},
            {"id": "asset-bed", "kind": "audio", "uri": bed.to_str().unwrap(), "content_digest": null, "metadata": {}, "provenance": ["prov-qual"]},
            {"id": "asset-sfx", "kind": "audio", "uri": sfx.to_str().unwrap(), "content_digest": null, "metadata": {}, "provenance": ["prov-qual"]}
        ],
        "streams": [
            {"id": "stream-video", "asset_id": "asset-av", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}},
            {"id": "stream-dialogue", "asset_id": "asset-av", "stream_index": 1, "clock_id": "clock-source", "metadata": {"codec_type": "audio"}},
            {"id": "stream-bed", "asset_id": "asset-bed", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "audio"}},
            {"id": "stream-sfx", "asset_id": "asset-sfx", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "audio"}}
        ],
        "entities": [], "observations": [], "identity_links": [], "masks": [],
        "object_classes": [], "tracks": [], "relations": [], "intents": [],
        "clip_uses": [
            {"id": "use-dialogue", "asset_id": "asset-av",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 72},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 72},
             "entity_links": [], "role": "a_roll", "intent_id": null},
            {"id": "use-bed-a", "asset_id": "asset-bed",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 48},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 48},
             "entity_links": [], "role": "music", "intent_id": null},
            {"id": "use-bed-b", "asset_id": "asset-bed",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 48},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 24, "end": 72},
             "entity_links": [], "role": "music", "intent_id": null},
            {"id": "use-hit", "asset_id": "asset-sfx",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 24},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 48, "end": 72},
             "entity_links": [], "role": "sfx", "intent_id": null}
        ],
        "edits": [], "effects": [], "renders": [], "frame_maps": [],
        "nle_bindings": [], "receipts": [],
        "provenance": [{"id": "prov-qual", "producer": "adapter-qualification", "producer_version": null, "source_digest": null, "recorded_at": null}]
    });
    decode_project_model(&document.to_string()).expect("mix model decodes")
}

/// Engine clip id to its inspected `gainDb`, over every audio item.
fn inspect_gains(inspection: &serde_json::Value) -> BTreeMap<u64, f64> {
    let mut gains = BTreeMap::new();
    for track in inspection["audio"].as_array().unwrap() {
        for item in track["items"].as_array().unwrap() {
            gains.insert(
                item["clip"].as_u64().unwrap(),
                item["gainDb"].as_f64().unwrap(),
            );
        }
    }
    gains
}

/// Solo one mixer strip headlessly. Measurement scaffolding only: stem
/// isolation for the LUFS proof below, not part of the mix contract (the
/// mix itself runs entirely through driver operations).
fn set_strip_solo(cli: &Path, project: &Path, strip: &str, solo: bool) {
    let output = std::process::Command::new(cli)
        .arg("--project")
        .arg(project)
        .args(["exec", "mixer.setStrip"])
        .arg(serde_json::json!({"strip": strip, "solo": solo}).to_string())
        .arg("--compact")
        .arg("--save")
        .output()
        .expect("pinned CLI must run mixer.setStrip");
    assert!(
        output.status.success(),
        "mixer.setStrip {strip} solo={solo} failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let response: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("setStrip answers JSON");
    assert_eq!(
        response["solo"], serde_json::json!(solo),
        "strip {strip} must report solo={solo}"
    );
}

fn export_mix_wav(adapter: &FilmCraft, project: &Path, dir: &Path, name: &str) -> PathBuf {
    let out = dir.join(name);
    let receipt = adapter
        .execute(request(Operation::Export {
            project: project.into(),
            path: out.clone(),
            format: ExportFormat::Wav,
            hardware_encoder: HardwareEncoding::Off,
            bitrate_kbps: None,
            bitrate_mode: None,
        }))
        .unwrap();
    assert!(receipt.engine_response["job"].is_u64());
    validate_render_asset(&out).unwrap();
    out
}

/// (integrated LUFS, true-peak dBTP) off a rendered wav, via loudnorm.
/// ffmpeg is a declared requirement of this file: its absence fails.
fn measure_loudness(wav: &Path) -> (f64, f64) {
    let output = std::process::Command::new("ffmpeg")
        .arg("-hide_banner")
        .arg("-i")
        .arg(wav)
        .args(["-map", "0:a", "-filter:a", "loudnorm=print_format=json", "-f", "null", "-"])
        .output()
        .expect("ffmpeg must be on PATH for the mix proof");
    assert!(
        output.status.success(),
        "loudnorm failed on {}: {}",
        wav.display(),
        String::from_utf8_lossy(&output.stderr)
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    let start = stderr.find('{').expect("loudnorm prints a JSON block");
    let end = stderr.rfind('}').expect("loudnorm JSON block closes");
    let summary: serde_json::Value =
        serde_json::from_str(&stderr[start..=end]).expect("loudnorm JSON parses");
    let lufs: f64 = summary["input_i"]
        .as_str()
        .expect("loudnorm reports input_i")
        .parse()
        .expect("input_i parses as a number");
    let peak: f64 = summary["input_tp"]
        .as_str()
        .expect("loudnorm reports input_tp")
        .parse()
        .expect("input_tp parses as a number");
    (lufs, peak)
}

/// Rendered duration in seconds, via ffprobe.
fn measure_duration(wav: &Path) -> f64 {
    let output = std::process::Command::new("ffprobe")
        .arg("-hide_banner")
        .arg("-v")
        .arg("error")
        .arg("-show_entries")
        .arg("format=duration")
        .arg("-of")
        .arg("csv=p=0")
        .arg(wav)
        .output()
        .expect("ffprobe must be on PATH for the mix proof");
    assert!(output.status.success());
    String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse()
        .expect("ffprobe duration parses as seconds")
}

/// First sound onset in seconds: the end of the leading silence on a stem
/// whose head is silent (the SFX hit enters at frame 48 of 24fps = 2.0s).
fn measure_onset(wav: &Path) -> f64 {
    let output = std::process::Command::new("ffmpeg")
        .arg("-hide_banner")
        .arg("-i")
        .arg(wav)
        .args(["-af", "silencedetect=noise=-40dB:d=0.2", "-f", "null", "-"])
        .output()
        .expect("ffmpeg must be on PATH for the mix proof");
    let stderr = String::from_utf8_lossy(&output.stderr);
    stderr
        .lines()
        .find_map(|line| {
            line.split("silence_end: ")
                .nth(1)
                .and_then(|tail| tail.split_whitespace().next())
                .and_then(|seconds| seconds.parse::<f64>().ok())
        })
        .expect("the hit stem must open with a measurable silence")
}

#[test]
fn native_audio_mix_renders_declared_levels_and_sync() {
    let adapter = FilmCraft::new().unwrap();
    let cli = ren_filmcraft::locate_or_build_cli().unwrap();
    let model = mix_model(&linked_av_fixture(), &mix_bed_fixture(), &mix_sfx_fixture());
    let projection = plan_projection(&model).unwrap();
    assert!(
        projection.losses.is_empty(),
        "a pure stem mix plans no loss: {:#?}",
        projection.losses
    );
    // The crossfaded bed pair takes A2+A3; the hit starts above the bed.
    let strip_of = |id: &str| {
        projection
            .placements
            .iter()
            .find(|p| p.clip_use_id == id)
            .unwrap_or_else(|| panic!("no placement for {id}"))
            .track
            .clone()
    };
    assert_eq!(strip_of("use-dialogue"), "V1");
    assert_eq!(strip_of("use-bed-a"), "A2");
    assert_eq!(strip_of("use-bed-b"), "A3");
    assert_eq!(strip_of("use-hit"), "A4");
    let placed = place_through_adapter(&adapter, "adapter-mix", &model);

    // Declared intent, in dB: bed pieces at -6, hit at -3, dialogue unity.
    let context = EditContext {
        project: &placed.project,
        clip_ids: &placed.clip_ids,
    };
    let intents = [
        ("edit-gain-bed-a", "use-bed-a", -6.0),
        ("edit-gain-bed-b", "use-bed-b", -6.0),
        ("edit-gain-hit", "use-hit", -3.0),
    ];
    let mut mutated = model.clone();
    for (id, target, db) in intents {
        let edit = ren_edit_for(id, "audio.gain", target, serde_json::json!({"db": db}));
        let operations =
            ren_filmcraft::adapter::plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        let operation = operations.into_iter().next().unwrap();
        let engine_audio: u64 = placed.clip_ids[target][0];
        assert!(
            matches!(&operation, Operation::ClipAudioGain { clips, gain_db, .. }
                if clips == &vec![engine_audio] && (*gain_db - db).abs() < f64::EPSILON),
            "gain on {target} must address its reconciled audio at {db} dB, got {operation:?}"
        );
        let receipt = adapter.execute(request(operation)).unwrap();
        let landed = receipt.engine_response["gainDb"]
            .as_array()
            .expect("clip.audioGain reports its landed levels");
        assert!(
            landed.iter().any(|entry| (entry["gainDb"].as_f64().unwrap() - db).abs() < 0.001),
            "receipt must land {db} dB for {target}: {landed:?}"
        );
        // Model equivalence: the span-neutral edit logs, and the logged
        // edit re-plans to the identical operation.
        mutated =
            ren_filmcraft::adapter::apply_ren_edit_to_model(&mutated, &edit).unwrap();
        let replanned = ren_filmcraft::adapter::plan_ren_edit(
            &mutated,
            &plan_projection(&mutated).unwrap(),
            mutated.edits.last().unwrap(),
            &context,
        )
        .unwrap();
        assert_eq!(replanned.len(), 1);
        assert!(
            matches!(&replanned[0], Operation::ClipAudioGain { clips, gain_db, .. }
                if clips == &vec![engine_audio] && (*gain_db - db).abs() < f64::EPSILON),
            "logged gain on {target} must re-plan identically"
        );
    }
    assert_eq!(mutated.edits.len(), 3);

    // Frame-exact stems plus exact landed gains, read back off inspect.
    let inspection = adapter
        .execute(request(Operation::Inspect {
            project: placed.project.clone(),
        }))
        .unwrap();
    let mut audio_spans = inspect_spans(&inspection.engine_response, "audio");
    audio_spans.sort();
    assert_eq!(
        audio_spans,
        vec![(0, 48), (0, 72), (24, 48), (48, 24)],
        "dialogue, both bed pieces and the hit must sit frame-exact"
    );
    let gains = inspect_gains(&inspection.engine_response);
    // A linked use reconciles to video-plus-audio ids; the gain lands on
    // the audio item, so read through every reconciled id.
    let gain_of = |target: &str| {
        placed.clip_ids[target]
            .iter()
            .find_map(|id| gains.get(id))
            .copied()
            .unwrap_or_else(|| panic!("no inspected gain for {target}"))
    };
    assert_eq!(gain_of("use-dialogue"), 0.0);
    assert_eq!(gain_of("use-bed-a"), -6.0);
    assert_eq!(gain_of("use-bed-b"), -6.0);
    assert_eq!(gain_of("use-hit"), -3.0);

    // LUFS per stem: solo each stem's strips, export, measure. The bed
    // is two crossfaded pieces on A2+A3, so its stem solos both. The
    // solo toggles are measurement scaffolding (direct CLI); the mix ran
    // on driver ops.
    let dir = placed._temp.path();
    let stem = |strips: &[&str], name: &str| {
        for strip in strips {
            set_strip_solo(&cli, &placed.project, strip, true);
        }
        let wav = export_mix_wav(&adapter, &placed.project, dir, name);
        for strip in strips {
            set_strip_solo(&cli, &placed.project, strip, false);
        }
        wav
    };
    let dialogue_wav = stem(&["A1"], "stem-dialogue.wav");
    let bed_wav = stem(&["A2", "A3"], "stem-bed.wav");
    let hit_wav = stem(&["A4"], "stem-hit.wav");
    let (dialogue_lufs, _) = measure_loudness(&dialogue_wav);
    let (bed_lufs_before, bed_peak) = measure_loudness(&bed_wav);
    eprintln!("[mix-proof] dialogue {dialogue_lufs:.2} LUFS, bed {bed_lufs_before:.2} LUFS peak {bed_peak:.2} dBTP");
    assert!(
        bed_peak <= -1.0,
        "bed stem true peak must sit under the -1 dBTP hygiene ceiling, got {bed_peak}"
    );
    // Sync: the hit enters at frame 48 of 24fps; every stem renders the
    // whole 3s timeline.
    let onset = measure_onset(&hit_wav);
    assert!(
        (onset - 2.0).abs() < 0.2,
        "hit onset must sit at 2.0s, measured {onset}"
    );
    for wav in [&dialogue_wav, &bed_wav, &hit_wav] {
        let seconds = measure_duration(wav);
        assert!(
            (seconds - 3.0).abs() < 0.15,
            "{} must render the 3s timeline, measured {seconds}s",
            wav.display()
        );
    }

    // The rendered mix follows the declared intent: -6 dB more gain on
    // both bed pieces reads as 6 dB more bed LUFS, and dialogue does not
    // move (per-clip locality - a strip-level change would move with it).
    for (id, target) in [("edit-regain-bed-a", "use-bed-a"), ("edit-regain-bed-b", "use-bed-b")] {
        let edit = ren_edit_for(id, "audio.gain", target, serde_json::json!({"db": 0.0}));
        let operations =
            ren_filmcraft::adapter::plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        adapter.execute(request(operations.into_iter().next().unwrap())).unwrap();
    }
    let bed_up_wav = stem(&["A2", "A3"], "stem-bed-up.wav");
    let (bed_lufs_after, _) = measure_loudness(&bed_up_wav);
    let dialogue_again_wav = stem(&["A1"], "stem-dialogue-again.wav");
    let (dialogue_lufs_after, _) = measure_loudness(&dialogue_again_wav);
    let delta = bed_lufs_after - bed_lufs_before;
    eprintln!("[mix-proof] bed {bed_lufs_before:.2} -> {bed_lufs_after:.2} LUFS (delta {delta:.2})");
    assert!(
        (delta - 6.0).abs() < 1.0,
        "raising both bed pieces 6 dB must raise bed LUFS ~6 dB, measured {delta:.2}"
    );
    assert!(
        (dialogue_lufs_after - dialogue_lufs).abs() < 0.5,
        "dialogue must not move with the bed gain, measured {}",
        dialogue_lufs_after - dialogue_lufs
    );

    // Full mix: renders, peaks under the ceiling, spans the timeline.
    let mix_wav = export_mix_wav(&adapter, &placed.project, dir, "full-mix.wav");
    let (mix_lufs, mix_peak) = measure_loudness(&mix_wav);
    let mix_seconds = measure_duration(&mix_wav);
    eprintln!("[mix-proof] full mix {mix_lufs:.2} LUFS peak {mix_peak:.2} dBTP {mix_seconds:.3}s");
    assert!(
        mix_peak <= -1.0,
        "full-mix true peak must sit under -1 dBTP, got {mix_peak}"
    );
    assert!(
        (mix_seconds - 3.0).abs() < 0.15,
        "full mix must span the 3s timeline, measured {mix_seconds}s"
    );
}
