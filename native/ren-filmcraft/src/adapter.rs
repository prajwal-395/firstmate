//! Unit 6: deterministic projection between Ren's canonical Project Model and
//! the pinned FilmCraft driver in `super`.
//!
//! Ren's model stays canonical. This adapter serializes only the bounded subset
//! FilmCraft can represent (asset imports plus ordered clip placements on V1/V2)
//! into driver [`super::Operation`]s, keeps the bidirectional Ren/engine ID map,
//! builds the exact timeline-to-render [`FrameMap`](ren_protocol::project_model::FrameMap),
//! validates the exported render asset, and carries every loss explicitly.
//!
//! Fail-closed rules:
//!
//! - The projection is deterministic: clip uses sort by
//!   `(timeline_start, clip_use_id)`; imports sort by asset id; ID maps are
//!   `BTreeMap`s. Planning twice over the same model yields identical bytes.
//! - Anything the engine cannot represent is a projection [`super::LossReport`]
//!   (model effects, masks, object tracks) or an [`AdapterError::Unsupported`]
//!   refusal (overlapping timeline ranges, unknown roles, short sources,
//!   inexact tick conversion, unknown edit operations). Nothing is guessed.
//! - Legacy editorial operation strings (for example `clip.trim` without
//!   explicit engine parameters) are refused as unsupported: the adapter never
//!   infers an edge, mode, or delta from before/after snapshots.
//! - The two measured FilmCraft defects (V1-only razor leaving linked audio,
//!   wrong trim delta) are detected by the driver and propagated here verbatim
//!   through [`summarize_losses`]; this adapter never swallows a driver loss.
//! - Retimed playback has no representation in `timeline.place`: a clip use
//!   whose source holds fewer frames than its timeline range refuses with
//!   `source_too_short`. A longer source is a trim and plays its leading
//!   timeline-length portion.
//! - Resolve remains the finishing destination: `.fcproj` files are disposable
//!   engine working artifacts, never Ren's database.

use ren_protocol::project_model::{
    validate_project_model, Clock, EditOperation, FrameMap, FrameMapId, FrameMapSegment, Interval,
    MappingKind, ProjectModel, Rational,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use super::{
    ExportFormat, HardwareEncoding, LossReport, Operation, TrimEdge, TrimMode, FILMCRAFT_REVISION,
};

/// FilmCraft's fixed project clock, ticks per second (see pinned CLI tests).
pub const ENGINE_TICKS_PER_SECOND: i64 = 254_016_000_000;
/// Version of this projection contract. Bump on any planning-rule change.
pub const ADAPTER_VERSION: u32 = 1;

#[derive(Debug)]
pub enum AdapterError {
    Unsupported { code: String, detail: String },
    InvalidModel(String),
}

impl std::fmt::Display for AdapterError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unsupported { code, detail } => {
                write!(f, "FilmCraft projection refused ({code}): {detail}")
            }
            Self::InvalidModel(detail) => write!(f, "invalid Project Model: {detail}"),
        }
    }
}

impl std::error::Error for AdapterError {}

pub type Result<T> = std::result::Result<T, AdapterError>;

fn unsupported(code: &str, detail: String) -> AdapterError {
    AdapterError::Unsupported {
        code: code.into(),
        detail,
    }
}

fn invalid(detail: String) -> AdapterError {
    AdapterError::InvalidModel(detail)
}

/// One asset import, sorted by asset id in [`Projection`].
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AssetImport {
    pub asset_id: String,
    pub uri: String,
}

/// One ordered clip placement derived from a model `ClipUse`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Placement {
    pub clip_use_id: String,
    pub asset_id: String,
    pub track: String,
    pub audio_track: Option<String>,
    pub frame: i64,
    pub source_in_ticks: i64,
    pub duration_ticks: i64,
    pub timeline_start: i64,
    pub timeline_end: i64,
}

/// Deterministic plan over a Project Model. Serialize with [`projection_json`]
/// for byte-stable comparison.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Projection {
    pub adapter_version: u32,
    pub engine_revision: String,
    pub timeline_clock: String,
    pub imports: Vec<AssetImport>,
    pub placements: Vec<Placement>,
    pub losses: Vec<LossReport>,
}

/// Canonical JSON encoding of a projection (struct field order plus sorted
/// vectors and maps make repeated plans byte-identical).
pub fn projection_json(projection: &Projection) -> String {
    serde_json::to_string(projection).expect("projection serializes")
}

fn clock_by_id<'m>(model: &'m ProjectModel, id: &str) -> Result<&'m Clock> {
    model
        .clocks
        .iter()
        .find(|c| c.id.0 == id)
        .ok_or_else(|| unsupported("unknown_clock", format!("clock `{id}` is not in the model")))
}

fn clock_frame_rate(clock: &Clock) -> Result<(i64, i64)> {
    let rate = clock.frame_rate.as_ref().ok_or_else(|| {
        unsupported(
            "clock_without_frame_rate",
            format!("clock `{}` names no frame rate", clock.id.0),
        )
    })?;
    if rate.denominator == 0 {
        return Err(invalid(format!(
            "clock `{}` has a zero frame-rate denominator",
            clock.id.0
        )));
    }
    Ok((rate.numerator, i64::from(rate.denominator)))
}

/// Exact `ticks * num / den`, refusing fractional results rather than rounding.
fn scale_exact(ticks: i64, num: i128, den: i128, what: &str) -> Result<i64> {
    if den == 0 {
        return Err(invalid(format!("{what}: zero denominator")));
    }
    let product = i128::from(ticks)
        .checked_mul(num)
        .ok_or_else(|| invalid(format!("{what}: tick scaling overflow")))?;
    if product % den != 0 {
        return Err(unsupported(
            "inexact_tick",
            format!("{what}: {ticks} ticks do not convert exactly"),
        ));
    }
    i64::try_from(product / den)
        .map_err(|_| invalid(format!("{what}: converted tick overflows i64")))
}

fn ticks_to_frames(ticks: i64, clock: &Clock, what: &str) -> Result<i64> {
    let (num, den) = clock_frame_rate(clock)?;
    let tps = i128::from(clock.ticks_per_second);
    scale_exact(ticks, i128::from(num), tps * i128::from(den), what)
}

/// Duration in engine ticks, preserving time across clocks.
fn duration_to_engine_ticks(ticks: i64, clock: &Clock, what: &str) -> Result<i64> {
    scale_exact(
        ticks,
        i128::from(ENGINE_TICKS_PER_SECOND),
        i128::from(clock.ticks_per_second),
        what,
    )
}

fn has_audio_stream(model: &ProjectModel, asset_id: &str) -> bool {
    model.streams.iter().any(|s| {
        s.asset_id.0 == asset_id
            && s.metadata.get("codec_type").and_then(|v| v.as_str()) == Some("audio")
    })
}

/// Deterministic video track per role. Anything else has no mapping.
fn track_for_role(role: &str, clip_use_id: &str) -> Result<String> {
    match role {
        "a_roll" => Ok("V1".into()),
        "b_roll" => Ok("V2".into()),
        _ => Err(unsupported(
            "unsupported_role",
            format!("clip use `{clip_use_id}` has role `{role}`; projection maps only `a_roll` and `b_roll`"),
        )),
    }
}

/// Pure, deterministic plan over `model`. No I/O, no CLI: the same model
/// document always yields the same projection bytes.
pub fn plan_projection(model: &ProjectModel) -> Result<Projection> {
    validate_project_model(model)
        .map_err(|detail| invalid(format!("project model rejected: {detail}")))?;
    if model.clip_uses.is_empty() {
        return Err(unsupported(
            "empty_timeline",
            "the model names no clip uses to project".into(),
        ));
    }
    let timeline_clock = model.clip_uses[0].timeline_interval.clock_id.0.clone();
    for clip_use in &model.clip_uses {
        if clip_use.timeline_interval.clock_id.0 != timeline_clock {
            return Err(unsupported(
                "clock_mismatch",
                format!(
                    "clip use `{}` is on clock `{}`, expected `{timeline_clock}`",
                    clip_use.id.0, clip_use.timeline_interval.clock_id.0
                ),
            ));
        }
    }
    let timeline = clock_by_id(model, &timeline_clock)?;
    let assets: BTreeMap<&str, _> = model.assets.iter().map(|a| (a.id.0.as_str(), a)).collect();

    let mut ordered: Vec<_> = model.clip_uses.iter().collect();
    ordered.sort_by(|a, b| {
        (a.timeline_interval.start, &a.id.0).cmp(&(b.timeline_interval.start, &b.id.0))
    });
    if ordered.windows(2).any(|pair| {
        let (first, second) = (&pair[0].timeline_interval, &pair[1].timeline_interval);
        first.end > second.start
    }) {
        return Err(unsupported(
            "timeline_overlap",
            "two clip uses claim overlapping timeline ranges; the projection places one track layer without guessing stacking".into(),
        ));
    }

    let mut imports = BTreeMap::new();
    let mut placements = Vec::new();
    for clip_use in ordered {
        let asset = assets.get(clip_use.asset_id.0.as_str()).ok_or_else(|| {
            unsupported(
                "unknown_asset",
                format!(
                    "clip use `{}` names asset `{}` that is not in the model",
                    clip_use.id.0, clip_use.asset_id.0
                ),
            )
        })?;
        imports.insert(
            asset.id.0.clone(),
            AssetImport {
                asset_id: asset.id.0.clone(),
                uri: asset.uri.clone(),
            },
        );
        let source_clock = clock_by_id(model, &clip_use.source_interval.clock_id.0)?;
        let source_len = clip_use.source_interval.end - clip_use.source_interval.start;
        let timeline_len = clip_use.timeline_interval.end - clip_use.timeline_interval.start;
        let source_frames = ticks_to_frames(
            source_len,
            source_clock,
            &format!("clip use `{}` source length", clip_use.id.0),
        )?;
        let timeline_frames = ticks_to_frames(
            timeline_len,
            timeline,
            &format!("clip use `{}` timeline length", clip_use.id.0),
        )?;
        if source_frames < timeline_frames {
            return Err(unsupported(
                "source_too_short",
                format!(
                    "clip use `{}` holds {source_frames} source frames for {timeline_frames} timeline frames; freeze, repeat, or retimed playback has no `timeline.place` representation",
                    clip_use.id.0
                ),
            ));
        }
        let frame = scale_exact(
            clip_use.timeline_interval.start,
            i128::from(clock_frame_rate(timeline)?.0),
            i128::from(timeline.ticks_per_second) * i128::from(clock_frame_rate(timeline)?.1),
            &format!("clip use `{}` timeline start", clip_use.id.0),
        )?;
        let source_in_ticks = scale_exact(
            clip_use.source_interval.start,
            i128::from(ENGINE_TICKS_PER_SECOND),
            i128::from(source_clock.ticks_per_second),
            &format!("clip use `{}` source start", clip_use.id.0),
        )?;
        let duration_ticks = duration_to_engine_ticks(
            timeline_len,
            timeline,
            &format!("clip use `{}` timeline duration", clip_use.id.0),
        )?;
        placements.push(Placement {
            clip_use_id: clip_use.id.0.clone(),
            asset_id: asset.id.0.clone(),
            track: track_for_role(&clip_use.role, &clip_use.id.0)?,
            audio_track: has_audio_stream(model, &asset.id.0).then(|| "A1".into()),
            frame,
            source_in_ticks,
            duration_ticks,
            timeline_start: clip_use.timeline_interval.start,
            timeline_end: clip_use.timeline_interval.end,
        });
    }

    let mut losses = Vec::new();
    if !model.effects.is_empty() {
        losses.push(LossReport {
            code: "effect_not_projected".into(),
            detail: format!(
                "the model carries {} effect(s); `timeline.place` carries no effect parameters, so none were sent",
                model.effects.len()
            ),
        });
    }
    if !model.masks.is_empty() {
        losses.push(LossReport {
            code: "mask_not_projected".into(),
            detail: format!(
                "the model carries {} mask(s); the FilmCraft projection has no mask operation, so none were sent",
                model.masks.len()
            ),
        });
    }
    if !model.tracks.is_empty() {
        losses.push(LossReport {
            code: "object_track_not_projected".into(),
            detail: format!(
                "the model carries {} object track(s); the FilmCraft projection has no track operation, so none were sent",
                model.tracks.len()
            ),
        });
    }

    Ok(Projection {
        adapter_version: ADAPTER_VERSION,
        engine_revision: FILMCRAFT_REVISION.into(),
        timeline_clock,
        imports: imports.into_values().collect(),
        placements,
        losses,
    })
}

/// Resolve an asset URI against a media directory. Absolute URIs pass through;
/// relative URIs join the directory. No existence check: execution verifies media.
pub fn asset_path(media_dir: &Path, uri: &str) -> PathBuf {
    let candidate = PathBuf::from(uri);
    if candidate.is_absolute() {
        candidate
    } else {
        media_dir.join(candidate)
    }
}

/// Refuse before the engine runs when a referenced media file is absent.
pub fn verify_media(
    media_dir: &Path,
    projection: &Projection,
) -> Result<BTreeMap<String, PathBuf>> {
    let mut resolved = BTreeMap::new();
    for import in &projection.imports {
        let path = asset_path(media_dir, &import.uri);
        if !path.is_file() {
            return Err(unsupported(
                "missing_media",
                format!(
                    "asset `{}` resolves to `{}` which is not a file",
                    import.asset_id,
                    path.display()
                ),
            ));
        }
        resolved.insert(import.asset_id.clone(), path);
    }
    Ok(resolved)
}

/// The single import operation for a projection: all asset paths, sorted by
/// asset id, in one deterministic call.
pub fn import_operation(
    project: PathBuf,
    media: &BTreeMap<String, PathBuf>,
    projection: &Projection,
) -> Operation {
    let mut paths = Vec::new();
    for import in &projection.imports {
        if let Some(path) = media.get(&import.asset_id) {
            paths.push(path.clone());
        }
    }
    Operation::ImportMedia { project, paths }
}

/// One `timeline.place` per placement, in projection order. `items` maps asset
/// ids to the engine item ids minted by the import receipt.
pub fn placement_operations(
    project: &Path,
    items: &BTreeMap<String, u64>,
    projection: &Projection,
) -> Result<Vec<(String, Operation)>> {
    let mut operations = Vec::new();
    for placement in &projection.placements {
        let item = items.get(&placement.asset_id).ok_or_else(|| {
            invalid(format!(
                "no engine item for asset `{}`; reconcile the import receipt first",
                placement.asset_id
            ))
        })?;
        operations.push((
            placement.clip_use_id.clone(),
            Operation::Place {
                project: project.into(),
                item: *item,
                track: placement.track.clone(),
                audio_track: placement.audio_track.clone(),
                frame: placement.frame,
                source_in_ticks: placement.source_in_ticks,
                duration_ticks: placement.duration_ticks,
            },
        ));
    }
    Ok(operations)
}

/// Reconcile engine-minted clip ids back onto stable Ren clip-use ids.
/// `place_receipt_clips` carries each place receipt's `clips` id array in
/// placement order. A linked-audio placement mints one id per track, so each
/// entry keeps the whole array in driver receipt order (video first, as the
/// pinned driver tests consume it); edits address the first id. Empty
/// receipts and count mismatches refuse rather than guess the pairing.
pub fn reconcile_clip_ids(
    projection: &Projection,
    place_receipt_clips: &[Vec<u64>],
) -> Result<BTreeMap<String, Vec<u64>>> {
    if place_receipt_clips.len() != projection.placements.len() {
        return Err(invalid(format!(
            "placement count {} does not match receipt count {}",
            projection.placements.len(),
            place_receipt_clips.len()
        )));
    }
    let mut map = BTreeMap::new();
    for (placement, clips) in projection.placements.iter().zip(place_receipt_clips.iter()) {
        if clips.is_empty() {
            return Err(invalid(format!(
                "placement for `{}` returned no engine clips",
                placement.clip_use_id
            )));
        }
        map.insert(placement.clip_use_id.clone(), clips.clone());
    }
    Ok(map)
}

/// The primary engine clip for a reconciled clip use: the first id in driver
/// receipt order, which edits address.
pub fn primary_clip_id(ids: &[u64], clip_use_id: &str) -> Result<u64> {
    ids.first().copied().ok_or_else(|| {
        invalid(format!(
            "clip use `{clip_use_id}` reconciled to no engine clips"
        ))
    })
}

/// Context for translating one model edit into a driver operation.
pub struct EditContext<'p> {
    pub project: &'p Path,
    pub clip_ids: &'p BTreeMap<String, Vec<u64>>,
}

fn edit_target<'e>(
    edit: &'e EditOperation,
    context: &EditContext<'_>,
) -> Result<(&'e String, u64)> {
    let [target] = edit.target_ids.as_slice() else {
        return Err(invalid(format!(
            "edit `{}` must target exactly one clip use; it targets {}",
            edit.id.0,
            edit.target_ids.len()
        )));
    };
    let ids = context.clip_ids.get(target).ok_or_else(|| {
        invalid(format!(
            "edit `{}` targets `{target}` which has no reconciled engine clip id",
            edit.id.0
        ))
    })?;
    Ok((target, primary_clip_id(ids, target)?))
}

fn after_object(edit: &EditOperation) -> Result<&serde_json::Value> {
    edit.after.as_ref().ok_or_else(|| {
        invalid(format!(
            "edit `{}` carries no `after` parameters for the FilmCraft projection",
            edit.id.0
        ))
    })
}

fn after_str(edit: &EditOperation, after: &serde_json::Value, key: &str) -> Result<String> {
    after
        .get(key)
        .and_then(|v| v.as_str())
        .map(str::to_owned)
        .ok_or_else(|| {
            invalid(format!(
                "edit `{}` needs `after.{key}` as a string",
                edit.id.0
            ))
        })
}

fn after_i64(edit: &EditOperation, after: &serde_json::Value, key: &str) -> Result<i64> {
    after.get(key).and_then(|v| v.as_i64()).ok_or_else(|| {
        invalid(format!(
            "edit `{}` needs `after.{key}` as an integer",
            edit.id.0
        ))
    })
}

/// Translate one model edit into the pinned driver operation. Only explicit
/// `filmcraft.*` operations are supported; every other operation string -
/// including legacy editorial strings such as `clip.trim` - refuses with
/// `unsupported_edit_operation` instead of being inferred.
pub fn plan_edit(edit: &EditOperation, context: &EditContext<'_>) -> Result<Operation> {
    let project = context.project.into();
    match edit.operation.as_str() {
        "filmcraft.trim" => {
            let (_, clip) = edit_target(edit, context)?;
            let after = after_object(edit)?;
            let edge = match after_str(edit, after, "edge")?.as_str() {
                "in" => TrimEdge::In,
                "out" => TrimEdge::Out,
                other => {
                    return Err(invalid(format!(
                        "edit `{}` names unknown trim edge `{other}`",
                        edit.id.0
                    )));
                }
            };
            let mode = match after_str(edit, after, "mode")?.as_str() {
                "regular" => TrimMode::Regular,
                "ripple" => TrimMode::Ripple,
                other => {
                    return Err(invalid(format!(
                        "edit `{}` names unknown trim mode `{other}`",
                        edit.id.0
                    )));
                }
            };
            Ok(Operation::Trim {
                project,
                clip,
                edge,
                mode,
                delta_frames: after_i64(edit, after, "delta_frames")?,
            })
        }
        "filmcraft.split" | "filmcraft.razor" => {
            let after = after_object(edit)?;
            Ok(Operation::Razor {
                project,
                track: after_str(edit, after, "track")?,
                frame: after_i64(edit, after, "frame")?,
            })
        }
        "filmcraft.transition" => {
            let (_, clip) = edit_target(edit, context)?;
            let after = after_object(edit)?;
            let edge = match after_str(edit, after, "edge")?.as_str() {
                "in" => TrimEdge::In,
                "out" => TrimEdge::Out,
                other => {
                    return Err(invalid(format!(
                        "edit `{}` names unknown transition edge `{other}`",
                        edit.id.0
                    )));
                }
            };
            let frames = after
                .get("frames")
                .and_then(|v| v.as_u64())
                .and_then(|v| u32::try_from(v).ok())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.frames` as a non-negative integer",
                        edit.id.0
                    ))
                })?;
            Ok(Operation::Transition {
                project,
                clip,
                edge,
                frames,
                effect: after
                    .get("effect")
                    .and_then(|v| v.as_str())
                    .map(str::to_owned),
            })
        }
        "filmcraft.audio_gain" => {
            let after = after_object(edit)?;
            let gain_db = after.get("gain_db").and_then(|v| v.as_f64()).ok_or_else(|| {
                invalid(format!(
                    "edit `{}` needs `after.gain_db` as a number",
                    edit.id.0
                ))
            })?;
            Ok(Operation::AudioGain {
                project,
                track: after_str(edit, after, "track")?,
                gain_db,
            })
        }
        "filmcraft.move" => {
            let (_, clip) = edit_target(edit, context)?;
            let after = after_object(edit)?;
            Ok(Operation::Move {
                project,
                clip,
                track: after_str(edit, after, "track")?,
                timeline_ticks: after_i64(edit, after, "timeline_ticks")?,
                insert: after.get("insert").and_then(|v| v.as_bool()).ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.insert` as a boolean",
                        edit.id.0
                    ))
                })?,
            })
        }
        "filmcraft.export" => {
            let after = after_object(edit)?;
            let format = match after_str(edit, after, "format")?.as_str() {
                "h264" => ExportFormat::H264,
                "prores" => ExportFormat::ProRes,
                "wav" => ExportFormat::Wav,
                other => {
                    return Err(invalid(format!(
                        "edit `{}` names unknown export format `{other}`",
                        edit.id.0
                    )));
                }
            };
            let hardware_encoder = match after
                .get("hardware")
                .and_then(|v| v.as_str())
                .unwrap_or("off")
            {
                "off" => HardwareEncoding::Off,
                "auto" => HardwareEncoding::Auto,
                other => {
                    return Err(invalid(format!(
                        "edit `{}` names unknown hardware encoding `{other}`",
                        edit.id.0
                    )));
                }
            };
            let path = PathBuf::from(after_str(edit, after, "path")?);
            Ok(Operation::Export {
                project,
                path,
                format,
                hardware_encoder,
            })
        }
        other => Err(unsupported(
            "unsupported_edit_operation",
            format!(
                "edit `{}` names operation `{other}`; the FilmCraft projection supports only explicit `filmcraft.*` operations",
                edit.id.0
            ),
        )),
    }
}

/// Exact timeline-to-render frame map for a projection. Render output packs
/// placements in timeline order from render tick zero; each placement maps
/// one-to-one onto its render span and names its source clip use.
/// Timeline gaps between placements become explicit `Gap` segments so a
/// later swap-back refuses to move keyframes across unrendered picture.
/// Overlapping placements are refused by [`plan_projection`] before this runs.
pub fn build_frame_map(
    model: &ProjectModel,
    projection: &Projection,
    render_clock_id: &str,
    map_id: &str,
) -> Result<FrameMap> {
    let render_clock = clock_by_id(model, render_clock_id)?;
    let timeline = clock_by_id(model, &projection.timeline_clock)?;
    let render_tps = i128::from(render_clock.ticks_per_second);
    let timeline_tps = i128::from(timeline.ticks_per_second);
    let mut segments = Vec::new();
    let mut cursor = 0i64;
    let mut previous_end: Option<i64> = None;
    for placement in &projection.placements {
        if let Some(previous) = previous_end {
            if placement.timeline_start > previous {
                let gap_len = placement.timeline_start - previous;
                let render_len = scale_exact(gap_len, render_tps, timeline_tps, "timeline gap")?;
                let gap_end = cursor
                    .checked_add(render_len)
                    .ok_or_else(|| invalid("render cursor overflow over timeline gaps".into()))?;
                segments.push(FrameMapSegment {
                    from: Interval {
                        clock_id: timeline.id.clone(),
                        start: previous,
                        end: placement.timeline_start,
                    },
                    to: Interval {
                        clock_id: render_clock.id.clone(),
                        start: cursor,
                        end: gap_end,
                    },
                    mapping_kind: MappingKind::Gap,
                    rate: None,
                    source_clip_use: None,
                });
                cursor = gap_end;
            }
        }
        let timeline_len = placement.timeline_end - placement.timeline_start;
        let render_len = scale_exact(timeline_len, render_tps, timeline_tps, "placement")?;
        let render_end = cursor
            .checked_add(render_len)
            .ok_or_else(|| invalid("render cursor overflow over placements".into()))?;
        segments.push(FrameMapSegment {
            from: Interval {
                clock_id: timeline.id.clone(),
                start: placement.timeline_start,
                end: placement.timeline_end,
            },
            to: Interval {
                clock_id: render_clock.id.clone(),
                start: cursor,
                end: render_end,
            },
            mapping_kind: MappingKind::OneToOne,
            rate: Some(Rational {
                numerator: 1,
                denominator: 1,
            }),
            source_clip_use: Some(ren_protocol::project_model::ClipUseId(
                placement.clip_use_id.clone(),
            )),
        });
        cursor = render_end;
        previous_end = Some(placement.timeline_end);
    }
    Ok(FrameMap {
        id: FrameMapId(map_id.into()),
        from_clock: timeline.id.clone(),
        to_clock: render_clock.id.clone(),
        segments,
    })
}

/// The render span this frame map covers, in render ticks.
pub fn render_span(map: &FrameMap) -> i64 {
    map.segments.iter().map(|s| s.to.end).max().unwrap_or(0)
}

/// Validated render asset: the export must exist on disk and be non-empty.
/// When `ffprobe` is available the file must also decode to at least one
/// stream; when it is absent the existence check stands on its own.
pub fn validate_render_asset(path: &Path) -> Result<u64> {
    let size = std::fs::metadata(path)
        .map_err(|e| {
            invalid(format!(
                "render asset `{}` is unreadable: {e}",
                path.display()
            ))
        })?
        .len();
    if size == 0 {
        return Err(invalid(format!(
            "render asset `{}` is empty",
            path.display()
        )));
    }
    match std::process::Command::new("ffprobe")
        .args([
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
        ])
        .arg(path)
        .output()
    {
        Ok(output) if output.status.success() => {
            let value: serde_json::Value = serde_json::from_slice(&output.stdout).map_err(|e| {
                invalid(format!(
                    "ffprobe returned invalid JSON for `{}`: {e}",
                    path.display()
                ))
            })?;
            let streams = value["streams"].as_array().ok_or_else(|| {
                invalid(format!("ffprobe found no streams in `{}`", path.display()))
            })?;
            if streams.is_empty() {
                return Err(invalid(format!(
                    "ffprobe found no streams in `{}`",
                    path.display()
                )));
            }
        }
        // ffprobe absent or failing to launch is an environment gap, not proof
        // the render is bad: existence plus non-empty size already validated it.
        _ => {}
    }
    Ok(size)
}

/// Concatenate projection losses with every driver receipt's loss report.
/// Driver losses pass through verbatim: known engine defects stay visible.
pub fn summarize_losses(
    projection: &Projection,
    receipt_losses: &[Vec<LossReport>],
) -> Vec<LossReport> {
    let mut losses = projection.losses.clone();
    for batch in receipt_losses {
        losses.extend(batch.iter().cloned());
    }
    losses
}

#[cfg(test)]
mod tests {
    use super::*;
    use ren_protocol::project_model::decode_project_model;

    fn minimal_model_json() -> String {
        serde_json::json!({
            "id": "adapter-unit",
            "schema_version": 1,
            "timebase_policy": "rational_frames",
            "clocks": [
                {"id": "clock-source", "domain": {"kind": "source", "asset_id": "asset-a", "stream_id": "stream-video"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
                {"id": "clock-timeline", "domain": {"kind": "project"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
                {"id": "clock-render", "domain": {"kind": "render", "render_id": "render-1"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}}
            ],
            "assets": [
                {"id": "asset-a", "kind": "video", "uri": "media/a.mp4", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]},
                {"id": "asset-b", "kind": "video", "uri": "media/b.mp4", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]}
            ],
            "streams": [
                {"id": "stream-video", "asset_id": "asset-a", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}},
                {"id": "stream-audio", "asset_id": "asset-a", "stream_index": 1, "clock_id": "clock-source", "metadata": {"codec_type": "audio"}}
            ],
            "entities": [], "observations": [], "identity_links": [], "masks": [],
            "object_classes": [], "tracks": [], "relations": [], "intents": [],
            "clip_uses": [
                {"id": "use-b", "asset_id": "asset-b", "source_interval": {"clock_id": "clock-source", "start": 0, "end": 24}, "timeline_interval": {"clock_id": "clock-timeline", "start": 24, "end": 48}, "entity_links": [], "role": "b_roll", "intent_id": null},
                {"id": "use-a", "asset_id": "asset-a", "source_interval": {"clock_id": "clock-source", "start": 0, "end": 24}, "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 24}, "entity_links": [], "role": "a_roll", "intent_id": null}
            ],
            "edits": [], "effects": [], "renders": [], "frame_maps": [],
            "nle_bindings": [], "receipts": [],
            "provenance": [{"id": "prov-1", "producer": "unit", "producer_version": null, "source_digest": null, "recorded_at": null}]
        })
        .to_string()
    }

    fn minimal_model() -> ProjectModel {
        decode_project_model(&minimal_model_json()).expect("unit model decodes")
    }

    #[test]
    fn projection_is_deterministic_and_orders_by_timeline_start() {
        let model = minimal_model();
        let first = plan_projection(&model).unwrap();
        let second = plan_projection(&model).unwrap();
        assert_eq!(projection_json(&first), projection_json(&second));
        assert_eq!(first.engine_revision, FILMCRAFT_REVISION);
        assert_eq!(first.placements.len(), 2);
        // Input order was b-then-a; the plan orders a-then-b by timeline start.
        assert_eq!(first.placements[0].clip_use_id, "use-a");
        assert_eq!(first.placements[1].clip_use_id, "use-b");
        assert_eq!(first.placements[0].track, "V1");
        assert_eq!(first.placements[1].track, "V2");
        assert_eq!(first.placements[0].audio_track, Some("A1".into()));
        assert_eq!(first.placements[1].audio_track, None);
        assert_eq!(first.placements[0].frame, 0);
        assert_eq!(first.placements[1].frame, 24);
        assert_eq!(
            first.placements[0].duration_ticks,
            24 * ENGINE_TICKS_PER_SECOND / 24
        );
        assert!(first.losses.is_empty());
    }

    #[test]
    fn e2e_fixture_projects_three_placements_with_effect_and_mask_losses() {
        let json =
            include_str!("../../../tests/fixtures/project_model_e2e/expected-project-model.json");
        let model = decode_project_model(json).expect("e2e fixture decodes");
        let projection = plan_projection(&model).unwrap();
        assert_eq!(projection.placements.len(), 3);
        assert_eq!(projection.placements[0].clip_use_id, "use-ada");
        // The fixture trims Ada from 48 source frames to a 36-frame timeline span.
        assert_eq!(
            projection.placements[0].duration_ticks,
            36 * ENGINE_TICKS_PER_SECOND / 24
        );
        assert_eq!(projection.placements[2].track, "V2");
        let codes: Vec<_> = projection.losses.iter().map(|l| l.code.as_str()).collect();
        assert!(codes.contains(&"effect_not_projected"));
        assert!(codes.contains(&"mask_not_projected"));
    }

    #[test]
    fn overlapping_timeline_ranges_refuse() {
        let mut model = minimal_model();
        // clip_uses[0] is use-b (24-48); pulling its start before use-a ends
        // creates a genuine overlap.
        model.clip_uses[0].timeline_interval.start = 12;
        let err = plan_projection(&model).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "timeline_overlap")
        );
    }

    #[test]
    fn short_sources_and_unknown_roles_refuse() {
        let mut model = minimal_model();
        model.clip_uses[0].source_interval.end = 4;
        let err = plan_projection(&model).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "source_too_short")
        );

        let mut model = minimal_model();
        model.clip_uses[0].role = "interview".into();
        let err = plan_projection(&model).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "unsupported_role")
        );
    }

    #[test]
    fn legacy_edit_strings_refuse_without_inference() {
        let context = EditContext {
            project: Path::new("test.fcproj"),
            clip_ids: &BTreeMap::from([("use-a".to_owned(), vec![7u64])]),
        };
        for operation in [
            "clip.trim",
            "clip.split",
            "timeline.patch",
            "edit_graph.aroll",
            "composed.extend",
        ] {
            let edit = EditOperation {
                id: ren_protocol::project_model::EditOperationId("edit-legacy".into()),
                operation: operation.into(),
                target_ids: vec!["use-a".into()],
                before: None,
                after: Some(serde_json::json!({"end": 12})),
                intent_id: None,
                evidence_ids: vec![],
                author: "unit".into(),
                parent_revision: None,
            };
            let err = plan_edit(&edit, &context).unwrap_err();
            assert!(
                matches!(err, AdapterError::Unsupported { ref code, .. } if code == "unsupported_edit_operation"),
                "{operation} must refuse, got {err:?}"
            );
        }
    }

    fn filmcraft_edit(id: &str, operation: &str, after: serde_json::Value) -> EditOperation {
        EditOperation {
            id: ren_protocol::project_model::EditOperationId(id.into()),
            operation: operation.into(),
            target_ids: vec!["use-a".into()],
            before: None,
            after: Some(after),
            intent_id: None,
            evidence_ids: vec![],
            author: "unit".into(),
            parent_revision: None,
        }
    }

    #[test]
    fn explicit_filmcraft_edits_map_to_driver_operations() {
        let context = EditContext {
            project: Path::new("test.fcproj"),
            clip_ids: &BTreeMap::from([("use-a".to_owned(), vec![7u64])]),
        };
        let trim = plan_edit(
            &filmcraft_edit(
                "edit-trim",
                "filmcraft.trim",
                serde_json::json!({"edge": "out", "mode": "ripple", "delta_frames": -4}),
            ),
            &context,
        )
        .unwrap();
        assert!(matches!(
            trim,
            Operation::Trim {
                clip: 7,
                edge: TrimEdge::Out,
                mode: TrimMode::Ripple,
                delta_frames: -4,
                ..
            }
        ));
        let split = plan_edit(
            &filmcraft_edit(
                "edit-split",
                "filmcraft.split",
                serde_json::json!({"track": "V1", "frame": 12}),
            ),
            &context,
        )
        .unwrap();
        assert!(matches!(split, Operation::Razor { .. }));
        let gain = plan_edit(
            &filmcraft_edit(
                "edit-gain",
                "filmcraft.audio_gain",
                serde_json::json!({"track": "A1", "gain_db": -3.0}),
            ),
            &context,
        )
        .unwrap();
        assert!(matches!(gain, Operation::AudioGain { .. }));
        let export = plan_edit(
            &filmcraft_edit(
                "edit-export",
                "filmcraft.export",
                serde_json::json!({"path": "/tmp/out.mp4", "format": "h264"}),
            ),
            &context,
        )
        .unwrap();
        assert!(matches!(export, Operation::Export { .. }));
    }

    #[test]
    fn frame_map_packs_in_timeline_order_with_explicit_gaps() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let map = build_frame_map(&model, &projection, "clock-render", "map-unit").unwrap();
        assert_eq!(map.segments.len(), 2);
        assert_eq!((map.segments[0].to.start, map.segments[0].to.end), (0, 24));
        assert_eq!((map.segments[1].to.start, map.segments[1].to.end), (24, 48));
        assert_eq!(render_span(&map), 48);

        // A timeline gap becomes an explicit Gap segment, never silent black.
        // clip_uses[0] is use-b; moving it later opens a 24-36 gap.
        let mut gapped = minimal_model();
        gapped.clip_uses[0].timeline_interval.start = 36;
        gapped.clip_uses[0].timeline_interval.end = 60;
        let projection = plan_projection(&gapped).unwrap();
        let map = build_frame_map(&gapped, &projection, "clock-render", "map-gap").unwrap();
        assert_eq!(map.segments.len(), 3);
        assert_eq!(map.segments[1].mapping_kind, MappingKind::Gap);
        assert_eq!(
            (map.segments[1].from.start, map.segments[1].from.end),
            (24, 36)
        );
        assert_eq!((map.segments[0].to.end, map.segments[2].to.start), (24, 36));
    }

    #[test]
    fn reconcile_maps_each_placement_to_its_engine_clips() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        assert!(reconcile_clip_ids(&projection, &[vec![1]]).is_err());
        assert!(reconcile_clip_ids(&projection, &[vec![1], vec![]]).is_err());
        let map = reconcile_clip_ids(&projection, &[vec![11, 12], vec![22]]).unwrap();
        assert_eq!(map.get("use-a"), Some(&vec![11, 12]));
        assert_eq!(primary_clip_id(&map["use-a"], "use-a").unwrap(), 11);
        assert_eq!(map.get("use-b"), Some(&vec![22]));
    }

    #[test]
    fn driver_losses_pass_through_verbatim() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let driver_loss = LossReport {
            code: "linked_audio_not_razored".into(),
            detail: "picture gained a segment audio did not".into(),
        };
        let losses = summarize_losses(&projection, &[vec![driver_loss.clone()]]);
        assert!(losses.contains(&driver_loss));
    }

    #[test]
    fn render_asset_validation_refuses_missing_and_empty_files() {
        let missing = PathBuf::from("/nonexistent-ren-adapter/render.mp4");
        assert!(validate_render_asset(&missing).is_err());
        let dir = tempfile::tempdir().unwrap();
        let empty = dir.path().join("empty.mp4");
        std::fs::write(&empty, b"").unwrap();
        assert!(validate_render_asset(&empty).is_err());
    }
}
