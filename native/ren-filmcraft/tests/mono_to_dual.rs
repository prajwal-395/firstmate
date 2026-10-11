//! Gap-2 qualification (pinned headless CLI): discrete-mono sources must
//! export dual-mono, matching Resolve; true-stereo sources keep their image.
//!
//! Provenance: the native real-reel trial (Reel 22) measured the native render
//! left-only during dialogue (right channel digital silence) while Resolve
//! renders dual-mono. Root cause is the adapter's import/placement channel
//! mapping, not the engine export: the reel's 4x-mono MXF sources (dialogue,
//! silence, ambience, ambience) import stereo-paired as `[[0,1],[2,3]]`, so
//! the placed first pair is [dialogue, silence]. The engine expresses the
//! Resolve presentation through `clip.audioChannels` (`stereo [[c,c]]` maps
//! mono channel `c` to both outputs), and [`plan_dual_mono_mapping`] plans it.
//!
//! The reel's 47-56 GB MXF sources stay out of the repo: `quad-mono.mxf`
//! reproduces their exact measured structure at 1.3 MB (H264 video with four
//! discrete mono PCM streams - hot dialogue, silence, quiet ambience, quiet
//! ambience - importing stereo-paired exactly like the reel sources), and
//! `true-stereo.mp4` carries different content per channel. Proof is
//! per-channel RMS measured off the exported render with ffmpeg, which is a
//! declared requirement of this file, so its absence fails the proof rather
//! than passing vacuously.
//!
//! Heavy: needs the pinned FilmCraft CLI plus ffmpeg/ffprobe. Run through
//! `vep-verify-run.sh`, never bare alongside other heavy lanes.

use ren_filmcraft::adapter::{
    import_operation, placement_operations, plan_dual_mono_mapping, plan_projection, verify_media,
};
use ren_filmcraft::{ExportFormat, FilmCraft, GpuRendering, HardwareEncoding, Operation, Request};
use ren_protocol::project_model::{decode_project_model, ProjectModel};
use std::path::{Path, PathBuf};

fn request(operation: Operation) -> Request<Operation> {
    Request {
        adapter_schema: ren_filmcraft::ADAPTER_SCHEMA,
        operation,
    }
}

fn quad_mono_fixture() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/quad-mono.mxf")
}

fn true_stereo_fixture() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/true-stereo.mp4")
}

/// Two-second model over `media` at 24 fps clocks (48 source frames for 48
/// timeline frames). `audio` carries the model's audio stream metadata, each
/// entry `(stream_index, channels)`; channel counts are what
/// [`plan_dual_mono_mapping`] reads, so the quad-mono fixture declares four
/// one-channel streams and the true-stereo fixture one two-channel stream.
fn test_model(media: &Path, audio: &[(u32, u64)]) -> ProjectModel {
    let mut streams = vec![serde_json::json!(
        {"id": "stream-video", "asset_id": "asset-a", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}}
    )];
    for (index, channels) in audio {
        streams.push(serde_json::json!(
            {"id": format!("stream-audio-{index}"), "asset_id": "asset-a", "stream_index": index, "clock_id": "clock-source", "metadata": {"codec_type": "audio", "channels": channels}}
        ));
    }
    let document = serde_json::json!({
        "id": "mono-to-dual-qualification",
        "schema_version": 1,
        "timebase_policy": "rational_frames",
        "clocks": [
            {"id": "clock-source", "domain": {"kind": "source", "asset_id": "asset-a", "stream_id": "stream-video"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
            {"id": "clock-timeline", "domain": {"kind": "project"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
            {"id": "clock-render", "domain": {"kind": "render", "render_id": "render-1"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}}
        ],
        "assets": [
            {"id": "asset-a", "kind": "video", "uri": media.to_str().unwrap(), "content_digest": null, "metadata": {}, "provenance": ["prov-qual"]}
        ],
        "streams": streams,
        "entities": [], "observations": [], "identity_links": [], "masks": [],
        "object_classes": [], "tracks": [], "relations": [], "intents": [],
        "clip_uses": [
            {"id": "use-first", "asset_id": "asset-a",
             "source_interval": {"clock_id": "clock-source", "start": 0, "end": 48},
             "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 48},
             "entity_links": [], "role": "a_roll", "intent_id": null}
        ],
        "edits": [], "effects": [], "renders": [], "frame_maps": [],
        "nle_bindings": [], "receipts": [],
        "provenance": [{"id": "prov-qual", "producer": "mono-to-dual-qualification", "producer_version": null, "source_digest": null, "recorded_at": null}]
    });
    decode_project_model(&document.to_string()).expect("qualification model decodes")
}

struct ImportedProject {
    _temp: tempfile::TempDir,
    project: PathBuf,
    item: u64,
    mapping: serde_json::Value,
}

fn import_through_adapter(
    adapter: &FilmCraft,
    name: &str,
    model: &ProjectModel,
) -> ImportedProject {
    let temp = tempfile::tempdir().unwrap();
    let projection = plan_projection(model).unwrap();
    assert_eq!(projection.placements.len(), 1);
    let media = verify_media(Path::new("/"), &projection).unwrap();
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
    let item = imported.engine_response["items"][0].as_u64().unwrap();
    let mapping = adapter
        .execute(request(Operation::AudioChannels {
            project: project.clone(),
            items: vec![item],
            format: None,
            clips: None,
        }))
        .unwrap()
        .engine_response;
    ImportedProject {
        _temp: temp,
        project,
        item,
        mapping,
    }
}

/// Linear RMS (0..1) of one export channel: decode the stereo render, keep
/// only channel `ch`, and root-mean-square the 16-bit samples. ffmpeg is a
/// declared requirement, so a failed decode fails the proof outright.
fn channel_rms(render: &Path, ch: u32) -> f64 {
    let output = std::process::Command::new("ffmpeg")
        .args(["-v", "error", "-i"])
        .arg(render)
        .args(["-map", "0:a", "-filter:a"])
        .arg(format!("pan=mono|c0=c{ch}"))
        .args(["-f", "s16le", "-ac", "1", "-ar", "48000", "-"])
        .output()
        .expect("ffmpeg must be on PATH for the per-channel RMS proof");
    assert!(
        output.status.success(),
        "ffmpeg channel decode failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(
        output.stdout.len().is_multiple_of(2) && !output.stdout.is_empty(),
        "channel {ch} decoded to no audio"
    );
    let mut sum = 0.0;
    let mut count = 0usize;
    for pair in output.stdout.as_chunks::<2>().0 {
        let sample = i16::from_le_bytes([pair[0], pair[1]]) as f64 / 32768.0;
        sum += sample * sample;
        count += 1;
    }
    (sum / count as f64).sqrt()
}

fn channel_pcm(render: &Path, ch: u32) -> Vec<u8> {
    let output = std::process::Command::new("ffmpeg")
        .args(["-v", "error", "-i"])
        .arg(render)
        .args(["-map", "0:a", "-filter:a"])
        .arg(format!("pan=mono|c0=c{ch}"))
        .args(["-f", "s16le", "-ac", "1", "-ar", "48000", "-"])
        .output()
        .expect("ffmpeg must be on PATH for the channel-identity proof");
    assert!(output.status.success());
    output.stdout
}

fn place_one(adapter: &FilmCraft, project: &Path, item: u64, model: &ProjectModel) -> Vec<u64> {
    let projection = plan_projection(model).unwrap();
    let items = std::collections::BTreeMap::from([("asset-a".to_owned(), item)]);
    let operations = placement_operations(project, &items, &projection).unwrap();
    assert_eq!(operations.len(), 1);
    let receipt = adapter
        .execute(request(operations.into_iter().next().unwrap().1))
        .unwrap();
    receipt.engine_response["clips"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_u64().unwrap())
        .collect()
}

fn export_h264(adapter: &FilmCraft, project: &Path, name: &str) -> PathBuf {
    let render = project.with_file_name(format!("{name}.mp4"));
    let receipt = adapter
        .execute(request(Operation::Export {
            project: project.into(),
            path: render.clone(),
            format: ExportFormat::H264,
            hardware_encoder: HardwareEncoding::Off,
            gpu_rendering: GpuRendering::Off,
            bitrate_kbps: None,
            bitrate_mode: None,
        }))
        .unwrap();
    assert!(receipt.engine_response["job"].is_u64());
    assert!(render.is_file(), "export missing: {}", render.display());
    render
}

fn db(rms: f64) -> f64 {
    20.0 * rms.max(1e-9).log10()
}

#[test]
fn discrete_mono_without_mapping_exports_left_only() {
    // The defect, through the adapter with no channel instruction: discrete
    // mono streams (dialogue plus silence, the reel MXF shape) import
    // stereo-paired, and the export plays dialogue left with silence right.
    let adapter = FilmCraft::new().unwrap();
    let model = test_model(&quad_mono_fixture(), &[(1, 1), (2, 1), (3, 1), (4, 1)]);
    let placed = import_through_adapter(&adapter, "mono-unmapped", &model);
    assert!(
        plan_dual_mono_mapping(
            &model,
            "asset-a",
            placed.item,
            &placed.project,
            &placed.mapping
        )
        .unwrap()
        .is_some(),
        "the unmapped discrete-mono source must want a remap: {}",
        placed.mapping
    );
    let clips = place_one(&adapter, &placed.project, placed.item, &model);
    assert_eq!(
        clips.len(),
        2,
        "the unmapped placement mints video plus linked audio, got {clips:?}"
    );
    let render = export_h264(&adapter, &placed.project, "mono-unmapped");
    let (left, right) = (channel_rms(&render, 0), channel_rms(&render, 1));
    assert!(
        db(left) > -40.0,
        "dialogue must be hot on the left, got {:.1} dBFS",
        db(left)
    );
    assert!(
        db(left) - db(right) > 20.0,
        "without the mapping the right channel must sit 20+ dB below the left, got L {:.1} / R {:.1} dBFS",
        db(left),
        db(right)
    );
}

#[test]
fn discrete_mono_with_mapping_exports_dual_mono() {
    // The fix: query, plan, execute the dual-mono remap between import and
    // placement, and the stereo export carries dialogue on both channels.
    let adapter = FilmCraft::new().unwrap();
    let model = test_model(&quad_mono_fixture(), &[(1, 1), (2, 1), (3, 1), (4, 1)]);
    let placed = import_through_adapter(&adapter, "mono-dual", &model);
    let remap = plan_dual_mono_mapping(
        &model,
        "asset-a",
        placed.item,
        &placed.project,
        &placed.mapping,
    )
    .unwrap()
    .expect("discrete mono in a stereo pair must plan a remap");
    let receipt = adapter.execute(request(remap)).unwrap();
    assert_eq!(
        receipt.engine_response["items"],
        serde_json::json!([{"item": placed.item, "format": "Stereo", "clips": [[0, 0]]}]),
        "the remap must duplicate the first mono channel: {}",
        receipt.engine_response
    );
    let clips = place_one(&adapter, &placed.project, placed.item, &model);
    assert_eq!(
        clips.len(),
        2,
        "the dual-mono placement keeps the video-plus-audio shape, got {clips:?}"
    );
    let render = export_h264(&adapter, &placed.project, "mono-dual");
    let (left, right) = (channel_rms(&render, 0), channel_rms(&render, 1));
    assert!(
        db(left) > -40.0,
        "dialogue must stay hot, got {:.1} dBFS",
        db(left)
    );
    assert!(
        (db(left) - db(right)).abs() < 1.0,
        "dual-mono channels must match within 1 dB, got L {:.1} / R {:.1} dBFS",
        db(left),
        db(right)
    );
}

#[test]
fn true_stereo_keeps_its_image() {
    // The fail-closed half: a true-stereo source (different content per
    // channel) plans no remap, and the export preserves the image instead of
    // collapsing it to one channel doubled.
    let adapter = FilmCraft::new().unwrap();
    let model = test_model(&true_stereo_fixture(), &[(1, 2)]);
    let placed = import_through_adapter(&adapter, "stereo-kept", &model);
    assert!(
        plan_dual_mono_mapping(
            &model,
            "asset-a",
            placed.item,
            &placed.project,
            &placed.mapping
        )
        .unwrap()
        .is_none(),
        "true stereo must keep engine behavior: {}",
        placed.mapping
    );
    place_one(&adapter, &placed.project, placed.item, &model);
    let render = export_h264(&adapter, &placed.project, "stereo-kept");
    let (left, right) = (channel_rms(&render, 0), channel_rms(&render, 1));
    assert!(
        db(left) > -40.0 && db(right) > -40.0,
        "both stereo channels must stay hot, got L {:.1} / R {:.1} dBFS",
        db(left),
        db(right)
    );
    assert_ne!(
        channel_pcm(&render, 0),
        channel_pcm(&render, 1),
        "the stereo image must survive: channels must not be bit-identical"
    );
}
