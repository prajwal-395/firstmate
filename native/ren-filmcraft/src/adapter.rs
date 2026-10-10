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
//!   explicit engine parameters) are translated by [`plan_ren_edit`], which
//!   resolves the target against the model and projection: split drives the
//!   video track plus linked audio explicitly, trim resolves both edges to
//!   regular-mode deltas, move converts timeline frames to engine ticks,
//!   delete drives `edit.clear` (or `edit.rippleDelete` with
//!   `after.ripple`), ripple plans one compensated `timeline.move` per
//!   affected placement in an order whose destinations are always free,
//!   constant retime (`retime`, `span_retime`) drives `clip.speedDuration`
//!   with ripple so followers shift, addressing every reconciled engine clip
//!   of the use in one call, and link (`clip.link`) re-groups a use's engine
//!   clips - a razor severs the A/V link on later pieces and a retime over
//!   severed ids ripples once per id, so the ramp expansion re-links each
//!   severed piece first (clear-then-link) while the intact first piece needs
//!   no repair; transition and audio gain map onto
//!   their driver commands. A stepped `retime.segments` ramp expands through
//!   [`expand_retime_ramp`] into razor points plus one constant `retime` per
//!   piece; anything FilmCraft cannot represent exactly
//!   (unexpanded ramps, whole-ramp reversal, inexact rates or fractional-frame
//!   retime spans, overlapping ripple destinations, plan-level ops,
//!   unknown roles/tracks) refuses with a NAMED loss code rather than
//!   a guess.
//! - The six measured FilmCraft behaviors (four defects plus two link rules:
//!   V1-only razor leaving linked audio,
//!   wrong trim delta, move landing one frame early, sub-frame-long
//!   non-zero-frame placements that contact-trim an abutting move, razor
//!   severing the A/V link on later pieces, retime over severed ids
//!   rippling once per id) are
//!   handled explicitly:
//!   razor/trim defects propagate verbatim through [`summarize_losses`],
//!   while the Ren move translation compensates the off-by-one so the clip
//!   lands frame-exact; move contact-trims report `move_overwrote` when the
//!   trimmed clip sits outside the moved set, while sub-frame
//!   normalization of the moved clips themselves is not loss; ramp piece
//!   retimes re-link severed pieces through `clip.link` so the retime
//!   ripples once; this adapter
//!   never swallows a driver loss.
//! - Retimed playback has no representation in `timeline.place`: a clip use
//!   whose source holds fewer frames than its timeline range refuses with
//!   `source_too_short` UNLESS a logged `retime`/`span_retime` edit explains
//!   the exact ratio (slow motion keeps the whole source window and plays
//!   it slower). A longer source is a trim and plays its leading
//!   timeline-length portion. The timeline-to-render frame map marks
//!   retimed placements [`MappingKind::Retimed`] with the exact playback
//!   speed as the rational rate (2/1 at 200%); spans stay timeline-to-render
//!   packing, so the rate documents playback rather than the span affine.
//!   A reversed retime negates the numerator (-1/1): the sign is the
//!   direction, and ren-core's tick math refuses negative rates loudly
//!   rather than mapping them forwards.
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
pub const ADAPTER_VERSION: u32 = 3;

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
        if source_frames < timeline_frames && !retime_explains(model, &clip_use.id.0, source_frames, timeline_frames) {
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

/// Refuse an unknown transition effect before planning any driver operation.
/// The engine fails such names generically at execution, after the project
/// may already be mutated; the checked-in authority
/// (`src/video_transitions.json`, measured from the pinned CLI) lets this
/// pure planner refuse first, with a named code. `None` (the engine default
/// transition) stays valid; the transition-type mapping itself belongs to
/// whoever mints the first producer.
fn check_transition_effect(edit: &EditOperation, effect: Option<&str>) -> Result<()> {
    match effect {
        None => Ok(()),
        Some(name) if super::transition_effect_known(name) => Ok(()),
        Some(name) => Err(unsupported(
            "unknown_transition_effect",
            format!(
                "edit `{}` names transition effect `{name}`, which is not among the pinned engine's VideoTransition names (`src/video_transitions.json`); omit `effect` for the engine default transition",
                edit.id.0
            ),
        )),
    }
}

/// Translate one model edit into the pinned driver operation. Only explicit
/// `filmcraft.*` operations are supported here; Ren's own editorial
/// vocabulary (`clip.trim` and the rest) translates through [`plan_ren_edit`],
/// which resolves targets against the model instead of inferring from
/// snapshots alone. Anything else refuses with `unsupported_edit_operation`.
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
            let effect = after
                .get("effect")
                .and_then(|v| v.as_str())
                .map(str::to_owned);
            check_transition_effect(edit, effect.as_deref())?;
            Ok(Operation::Transition {
                project,
                clip,
                edge,
                frames,
                effect,
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

/// Ren's real edit vocabulary, translated against the model and projection.
///
/// `plan_edit` above handles only explicit `filmcraft.*` operations.
/// This function handles what Ren actually decides in practice - at least
/// split, trim (both edges), move, delete/ripple delete, link, speed/retime,
/// transitions and audio gain - each mapped onto FilmCraft driver commands,
/// or refused with a NAMED loss code when FilmCraft cannot represent it
/// exactly. Nothing is guessed: every delta is resolved from the target
/// clip use's measured intervals, and every conversion must be exact.
///
/// Linked audio follows the picture explicitly: a split over a placement
/// that carries an audio track plans one razor on the video track and a
/// second razor on the linked audio track at the same frame, so the
/// measured V1-only razor defect surfaces as no loss instead of
/// `linked_audio_not_razored`. A split that names no audio drive while the
/// placement carries audio is still planned with both razors; callers that
/// execute only the first element will see the driver loss verbatim.
///
/// `timeline.patch` (the Unit 4 migration envelope) expands into its inner
/// algebra operations, each translated as if it were a top-level Ren edit
/// against the same model.
pub fn plan_ren_edit(
    model: &ProjectModel,
    projection: &Projection,
    edit: &EditOperation,
    context: &EditContext<'_>,
) -> Result<Vec<Operation>> {
    let project: PathBuf = context.project.into();
    match edit.operation.as_str() {
        // Explicit engine operations keep their single-op translation.
        op @ ("filmcraft.trim" | "filmcraft.split" | "filmcraft.razor" | "filmcraft.transition"
        | "filmcraft.audio_gain" | "filmcraft.move" | "filmcraft.export") => {
            Ok(vec![plan_edit(edit, context).map_err(|e| match e {
                AdapterError::Unsupported { code, detail } => {
                    unsupported(&code, format!("{op}: {detail}"))
                }
                other => other,
            })?])
        }
        "clip.split" => {
            let (target_id, clip) = ren_edit_target(edit, context)?;
            let clip_use = clip_use_by_id(model, target_id, edit)?;
            let placement = placement_by_id(projection, target_id, edit)?;
            let timeline = clock_by_id(model, &projection.timeline_clock)?;
            let after = after_object(edit)?;
            let at_frame = after
                .get("at_frame")
                .or_else(|| after.get("frame"))
                .and_then(|v| v.as_i64())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.at_frame` as an integer timeline frame",
                        edit.id.0
                    ))
                })?;
            let (start_frame, end_frame) = timeline_frames_of(model, timeline, clip_use, edit)?;
            if at_frame <= start_frame || at_frame >= end_frame {
                return Err(invalid(format!(
                    "edit `{}` splits `{target_id}` at frame {at_frame}, outside ({start_frame}, {end_frame})",
                    edit.id.0
                )));
            }
            let video_track = after
                .get("track")
                .and_then(|v| v.as_str())
                .map(str::to_owned)
                .unwrap_or_else(|| placement.track.clone());
            let mut operations = vec![Operation::Razor {
                project: project.clone(),
                track: video_track,
                frame: at_frame,
            }];
            // Drive linked audio explicitly: the pinned driver razors only
            // the named track, so a V1-only razor would leave linked audio
            // behind as `linked_audio_not_razored`.
            if let Some(audio_track) = placement.audio_track.clone() {
                operations.push(Operation::Razor {
                    project,
                    track: audio_track,
                    frame: at_frame,
                });
            }
            let _ = clip;
            Ok(operations)
        }
        "clip.trim" => {
            let (target_id, clip) = ren_edit_target(edit, context)?;
            let clip_use = clip_use_by_id(model, target_id, edit)?;
            let timeline = clock_by_id(model, &projection.timeline_clock)?;
            let source_clock = clock_by_id(model, &clip_use.source_interval.clock_id.0)?;
            let after = after_object(edit)?;
            let (old_start_frame, old_end_frame) =
                timeline_frames_of(model, timeline, clip_use, edit)?;
            let (new_start_frame, new_end_frame) =
                ren_trim_frames(edit, after, old_start_frame, old_end_frame)?;
            if new_start_frame >= new_end_frame {
                return Err(invalid(format!(
                    "edit `{}` trims `{target_id}` to an empty timeline range [{new_start_frame}, {new_end_frame})",
                    edit.id.0
                )));
            }
            let new_duration_frames = new_end_frame - new_start_frame;
            let source_len = clip_use.source_interval.end - clip_use.source_interval.start;
            let source_frames = ticks_to_frames(
                source_len,
                source_clock,
                &format!("edit `{}` source length", edit.id.0),
            )?;
            if source_frames < new_duration_frames {
                return Err(unsupported(
                    "source_too_short",
                    format!(
                        "edit `{}` trims `{target_id}` to {new_duration_frames} timeline frames from {source_frames} source frames; freeze, repeat, or retimed playback has no `timeline.place` representation",
                        edit.id.0
                    ),
                ));
            }
            let mut operations = Vec::new();
            if new_start_frame != old_start_frame {
                operations.push(Operation::Trim {
                    project: project.clone(),
                    clip,
                    edge: TrimEdge::In,
                    mode: TrimMode::Regular,
                    delta_frames: new_start_frame - old_start_frame,
                });
            }
            if new_end_frame != old_end_frame {
                operations.push(Operation::Trim {
                    project,
                    clip,
                    edge: TrimEdge::Out,
                    mode: TrimMode::Regular,
                    delta_frames: new_end_frame - old_end_frame,
                });
            }
            if operations.is_empty() {
                return Err(unsupported(
                    "empty_trim",
                    format!(
                        "edit `{}` changes no edge of `{target_id}`; [{old_start_frame}, {old_end_frame}) is already the target range",
                        edit.id.0
                    ),
                ));
            }
            Ok(operations)
        }
        "clip.move" | "clip.slide" => {
            let (target_id, clip) = ren_edit_target(edit, context)?;
            let placement = placement_by_id(projection, target_id, edit)?;
            let timeline = clock_by_id(model, &projection.timeline_clock)?;
            let after = after_object(edit)?;
            // `timeline.patch` inner ops name `to_frame`; an explicit
            // `timeline_ticks` (engine ticks, filmcraft.move shape) passes
            // through after exact validation.
            if let Some(ticks) = after.get("timeline_ticks").and_then(|v| v.as_i64()) {
                let track = after
                    .get("track")
                    .and_then(|v| v.as_str())
                    .map(str::to_owned)
                    .unwrap_or_else(|| placement.track.clone());
                let insert = after.get("insert").and_then(|v| v.as_bool()).unwrap_or(false);
                return Ok(vec![Operation::Move {
                    project,
                    clip,
                    track,
                    timeline_ticks: ticks,
                    insert,
                }]);
            }
            let to_frame = after
                .get("to_frame")
                .and_then(|v| v.as_i64())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.to_frame` as an integer timeline frame",
                        edit.id.0
                    ))
                })?;
            // Measured engine defect (qualification `probe_move_offsets`):
            // `move_ticks_for_frame` compensates the one-frame-early
            // landing; explicit `timeline_ticks` (the `filmcraft.move`
            // shape above) passes through untouched.
            let timeline_ticks = move_ticks_for_frame(
                to_frame,
                timeline,
                &format!("edit `{}` move target", edit.id.0),
            )?;
            let track = after
                .get("track")
                .and_then(|v| v.as_str())
                .map(str::to_owned)
                .unwrap_or_else(|| placement.track.clone());
            // `clip.move` is a local reposition; `clip.slide` rolls its
            // neighbours, which is FilmCraft's insert move.
            let insert = after
                .get("insert")
                .and_then(|v| v.as_bool())
                .unwrap_or_else(|| edit.operation.as_str() == "clip.slide");
            Ok(vec![Operation::Move {
                project,
                clip,
                track,
                timeline_ticks,
                insert,
            }])
        }
        "clip.delete" => {
            let (target_id, clip) = ren_edit_target(edit, context)?;
            // Existence against the model keeps the error anchored; the
            // engine follows linked audio itself (measured: `edit.clear`
            // removes the linked audio item and `edit.rippleDelete` shifts
            // every downstream track), so one id per clip use suffices.
            let _ = clip_use_by_id(model, target_id, edit)?;
            let ripple = delete_is_ripple(edit)?;
            if ripple {
                Ok(vec![Operation::RippleDelete {
                    project,
                    clips: vec![clip],
                }])
            } else {
                Ok(vec![Operation::Delete {
                    project,
                    clips: vec![clip],
                }])
            }
        }
        "clip.ripple" => {
            let (target_id, _) = ren_edit_target(edit, context)?;
            let clip_use = clip_use_by_id(model, target_id, edit)?.clone();
            let timeline = clock_by_id(model, &projection.timeline_clock)?;
            let delta = ripple_delta(edit)?;
            plan_ripple_moves(model, projection, context, edit, target_id, &clip_use, timeline, delta)
        }
        "clip.link" => {
            let (target_id, _) = ren_edit_target(edit, context)?;
            // Existence against the model keeps the error anchored; a clip
            // use is one timeline span, so its engine clips are
            // co-positioned and safe to group. The toggle converges only
            // through clear-then-link (see `Operation::Link`): callers drive
            // this twice for severed pieces and assert the receipts read
            // false-then-true rather than assuming one call links.
            let _ = clip_use_by_id(model, target_id, edit)?;
            let clips = context.clip_ids.get(target_id).cloned().ok_or_else(|| {
                invalid(format!(
                    "edit `{}` targets `{target_id}` which lost its reconciled engine clip ids",
                    edit.id.0
                ))
            })?;
            Ok(vec![Operation::Link { project, clips }])
        }
        "retime" | "span_retime" => {
            let (target_id, _) = ren_edit_target(edit, context)?;
            let clip_use = clip_use_by_id(model, target_id, edit)?.clone();
            let timeline = clock_by_id(model, &projection.timeline_clock)?;
            let after = after_object(edit)?;
            let percent = after
                .get("percent")
                .and_then(|v| v.as_f64())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.percent` as a number",
                        edit.id.0
                    ))
                })?;
            // Direction rides the engine's `reverse` flag (measured: it
            // persists on inspect and the render plays backwards); span math
            // below is magnitude-only either way.
            let reverse = match after.get("reverse") {
                None => false,
                Some(value) => value.as_bool().ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.reverse` as a boolean when present",
                        edit.id.0
                    ))
                })?,
            };
            let (old_start_frame, old_end_frame) =
                timeline_frames_of(model, timeline, &clip_use, edit)?;
            // Validate exactness up front: fractional retime spans refuse
            // here with a named code rather than rounding in the engine.
            retimed_length(edit, percent, old_end_frame - old_start_frame)?;
            // Drive every engine clip of the use, not just the primary: a
            // razor severs the A/V link on the second half (measured:
            // post-razor pieces carry distinct video/audio link ids), so a
            // video-only retime would leave a severed audio piece playing
            // its old speed under the retimed picture. One call carries the
            // whole array and ripples once (measured), never once per clip.
            let clips = context.clip_ids.get(target_id).cloned().ok_or_else(|| {
                invalid(format!(
                    "edit `{}` targets `{target_id}` which lost its reconciled engine clip ids",
                    edit.id.0
                ))
            })?;
            // Ripple so followers shift exactly as ren-core's ripple
            // temporal effect does; the engine shifts every downstream
            // track (measured). Intact linked audio follows the speed;
            // severed pieces ride the explicit id array above.
            Ok(vec![Operation::Retime {
                project,
                clips,
                speed_percent: percent,
                ripple: true,
                reverse,
            }])
        }
        "retime.segments" => Err(unsupported(
            "retime_segments_not_supported",
            format!(
                "edit `{}` names a stepped speed ramp; the pure planner cannot address the post-razor piece ids a ramp needs - expand it first with `expand_retime_ramp` into razor points plus one constant `retime` per piece instead",
                edit.id.0
            ),
        )),
        "transition" => {
            let (target_id, clip) = ren_edit_target(edit, context)?;
            let after = after_object(edit)?;
            // Ren's plan-level transition may nest its fields under
            // `transition` (the algebra carrier shape); accept both.
            let fields = after
                .get("transition")
                .filter(|v| v.is_object())
                .unwrap_or(after);
            let edge = match fields
                .get("edge")
                .and_then(|v| v.as_str())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.edge` (`in`/`out`) for `{target_id}`",
                        edit.id.0
                    ))
                })? {
                "in" => TrimEdge::In,
                "out" => TrimEdge::Out,
                other => {
                    return Err(invalid(format!(
                        "edit `{}` names unknown transition edge `{other}`",
                        edit.id.0
                    )));
                }
            };
            let frames = fields
                .get("frames")
                .and_then(|v| v.as_u64())
                .and_then(|v| u32::try_from(v).ok())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.frames` as a non-negative integer",
                        edit.id.0
                    ))
                })?;
            let effect = fields
                .get("effect")
                .and_then(|v| v.as_str())
                .map(str::to_owned);
            check_transition_effect(edit, effect.as_deref())?;
            Ok(vec![Operation::Transition {
                project,
                clip,
                edge,
                frames,
                effect,
            }])
        }
        "audio.gain" => {
            let (target_id, _) = ren_edit_target(edit, context)?;
            let placement = placement_by_id(projection, target_id, edit)?;
            let audio_track = placement.audio_track.clone().ok_or_else(|| {
                unsupported(
                    "no_audio_for_gain",
                    format!(
                        "edit `{}` gains `{target_id}`, which carries no audio stream; there is no mixer strip to drive",
                        edit.id.0
                    ),
                )
            })?;
            let after = after_object(edit)?;
            let gain_db = after
                .get("gain_db")
                .or_else(|| after.get("db"))
                .and_then(|v| v.as_f64())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.db` (or `after.gain_db`) as a number of dB",
                        edit.id.0
                    ))
                })?;
            if !gain_db.is_finite() {
                return Err(invalid(format!(
                    "edit `{}` names a non-finite gain of {gain_db} dB",
                    edit.id.0
                )));
            }
            let track = after
                .get("track")
                .and_then(|v| v.as_str())
                .map(str::to_owned)
                .unwrap_or(audio_track);
            Ok(vec![Operation::AudioGain {
                project,
                track,
                gain_db,
            }])
        }
        "timeline.patch" => {
            let after = after_object(edit)?;
            let operations = after.get("operations").and_then(|v| v.as_array()).ok_or_else(|| {
                invalid(format!(
                    "edit `{}` is a migrated patch without `after.operations`; expand the patch before projecting",
                    edit.id.0
                ))
            })?;
            let mut planned = Vec::new();
            for (index, inner) in operations.iter().enumerate() {
                let synthetic = patch_inner_to_edit(edit, index, inner)?;
                let mut steps = plan_ren_edit(model, projection, &synthetic, context).map_err(|e| match e {
                    AdapterError::Unsupported { code, detail } => unsupported(
                        &code,
                        format!("edit `{}` inner op {index}: {detail}", edit.id.0),
                    ),
                    other => other,
                })?;
                planned.append(&mut steps);
            }
            if planned.is_empty() {
                return Err(unsupported(
                    "empty_patch",
                    format!(
                        "edit `{}` carries no translatable inner operations; plan-level and refused ops leave no FilmCraft command",
                        edit.id.0
                    ),
                ));
            }
            Ok(planned)
        }
        "composed.insert" | "composed.extend" | "composed.move" | "composed.replace"
        | "edit_graph.aroll" | "edit_graph.broll" | "edit_ledger.cut" | "edit_ledger.trim"
        | "angle_plan" | "plan_change" | "shot_selection" | "speech_selection"
        | "story_pacing" | "music_selection" | "audio_mix" | "look" | "visual_effect"
        | "sound_effect" | "subject_effect" | "motion_graphic" | "brand_asset" | "end_card" => {
            Err(unsupported(
                "plan_level_not_executable",
                format!(
                    "edit `{}` names operation `{}`; it is a planning record, not a timeline write, and has no FilmCraft command",
                    edit.id.0, edit.operation
                ),
            ))
        }
        other if other.starts_with("edit_graph.")
            || other.starts_with("edit_ledger.")
            || other.starts_with("composed.") =>
        {
            Err(unsupported(
                "plan_level_not_executable",
                format!(
                    "edit `{}` names operation `{other}`; it is a planning record, not a timeline write, and has no FilmCraft command",
                    edit.id.0
                ),
            ))
        }
        other => Err(unsupported(
            "unsupported_edit_operation",
            format!(
                "edit `{}` names operation `{other}`; the FilmCraft projection maps Ren's split, trim, move, delete, ripple, link, retime, transition and audio gain - everything else refuses by name",
                edit.id.0
            ),
        )),
    }
}

/// Apply one translatable Ren edit to the model, returning the updated model.
///
/// This is the ren-core side of the equivalence proof: the same edit the
/// adapter plans into FilmCraft commands mutates the canonical clip uses
/// here, so qualification can compare the engine's inspected timeline
/// against these expected clip uses frame-exact, and the render's frame
/// map (via [`build_frame_map`]) against the mutated model's map.
/// Stepped `retime.segments`, reverse and plan-level records refuse here
/// with the same named code they refuse planning with: expand a ramp first
/// with [`expand_retime_ramp`], then mutate with each returned edit in order.
/// Constant retimes
/// log into `model.edits` so the frame map carries their exact rate.
pub fn apply_ren_edit_to_model(model: &ProjectModel, edit: &EditOperation) -> Result<ProjectModel> {
    match edit.operation.as_str() {
        "clip.split" => {
            let [target] = edit.target_ids.as_slice() else {
                return Err(invalid(format!(
                    "edit `{}` must target exactly one clip use; it targets {}",
                    edit.id.0,
                    edit.target_ids.len()
                )));
            };
            let clip_use = clip_use_by_id(model, target, edit)?.clone();
            let timeline = model
                .clocks
                .iter()
                .find(|c| c.id.0 == clip_use.timeline_interval.clock_id.0)
                .ok_or_else(|| {
                    unsupported(
                        "unknown_clock",
                        format!(
                            "edit `{}` targets `{target}` on an unknown timeline clock",
                            edit.id.0
                        ),
                    )
                })?;
            let after = after_object(edit)?;
            let at_frame = after
                .get("at_frame")
                .or_else(|| after.get("frame"))
                .and_then(|v| v.as_i64())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.at_frame` as an integer timeline frame",
                        edit.id.0
                    ))
                })?;
            let (start_frame, end_frame) = timeline_frames_of(model, timeline, &clip_use, edit)?;
            if at_frame <= start_frame || at_frame >= end_frame {
                return Err(invalid(format!(
                    "edit `{}` splits `{target}` at frame {at_frame}, outside ({start_frame}, {end_frame})",
                    edit.id.0
                )));
            }
            let at_tick = frames_to_timeline_ticks(at_frame, timeline, &format!("edit `{}` split point", edit.id.0))?;
            // Split the source window proportionally (exact only): the
            // timeline offset into the use maps onto the same fraction of
            // the source window, so 1:1 media splits at the same offset.
            let timeline_len = clip_use.timeline_interval.end - clip_use.timeline_interval.start;
            let source_len = clip_use.source_interval.end - clip_use.source_interval.start;
            let offset = at_tick - clip_use.timeline_interval.start;
            let product = i128::from(offset)
                .checked_mul(i128::from(source_len))
                .ok_or_else(|| invalid(format!("edit `{}` split scaling overflow", edit.id.0)))?;
            if product % i128::from(timeline_len) != 0 {
                return Err(unsupported(
                    "inexact_tick",
                    format!(
                        "edit `{}` splits `{target}` where the source window does not divide exactly; refusing rather than rounding",
                        edit.id.0
                    ),
                ));
            }
            let source_at = clip_use.source_interval.start + (product / i128::from(timeline_len)) as i64;
            let mut left = clip_use.clone();
            let mut right = clip_use.clone();
            left.id.0 = format!("{}-a", target);
            right.id.0 = format!("{}-b", target);
            left.timeline_interval.end = at_tick;
            left.source_interval.end = source_at;
            right.timeline_interval.start = at_tick;
            right.source_interval.start = source_at;
            let mut updated = model.clone();
            updated.clip_uses = model
                .clip_uses
                .iter()
                .filter(|u| u.id.0 != *target)
                .cloned()
                .chain([left, right])
                .collect();
            updated.clip_uses.sort_by(|a, b| {
                (a.timeline_interval.start, &a.id.0).cmp(&(b.timeline_interval.start, &b.id.0))
            });
            validate_project_model(&updated)
                .map_err(|detail| invalid(format!("split model rejected: {detail}")))?;
            Ok(updated)
        }
        "clip.trim" => {
            let [target] = edit.target_ids.as_slice() else {
                return Err(invalid(format!(
                    "edit `{}` must target exactly one clip use; it targets {}",
                    edit.id.0,
                    edit.target_ids.len()
                )));
            };
            let clip_use = clip_use_by_id(model, target, edit)?.clone();
            let timeline = model
                .clocks
                .iter()
                .find(|c| c.id.0 == clip_use.timeline_interval.clock_id.0)
                .ok_or_else(|| {
                    unsupported(
                        "unknown_clock",
                        format!(
                            "edit `{}` targets `{target}` on an unknown timeline clock",
                            edit.id.0
                        ),
                    )
                })?;
            let after = after_object(edit)?;
            let (old_start_frame, old_end_frame) =
                timeline_frames_of(model, timeline, &clip_use, edit)?;
            let (new_start_frame, new_end_frame) =
                ren_trim_frames(edit, after, old_start_frame, old_end_frame)?;
            if new_start_frame >= new_end_frame {
                return Err(invalid(format!(
                    "edit `{}` trims `{target}` to an empty range",
                    edit.id.0
                )));
            }
            let new_start_tick =
                frames_to_timeline_ticks(new_start_frame, timeline, &format!("edit `{}` trim start", edit.id.0))?;
            let new_end_tick =
                frames_to_timeline_ticks(new_end_frame, timeline, &format!("edit `{}` trim end", edit.id.0))?;
            let mut updated_use = clip_use.clone();
            updated_use.timeline_interval.start = new_start_tick;
            updated_use.timeline_interval.end = new_end_tick;
            // The source window is untouched: a longer source is a trim that
            // plays its leading timeline-length portion, and a shorter
            // source refuses below.
            let source_clock = model
                .clocks
                .iter()
                .find(|c| c.id.0 == updated_use.source_interval.clock_id.0)
                .ok_or_else(|| {
                    unsupported("unknown_clock", format!("edit `{}` source clock is unknown", edit.id.0))
                })?;
            let source_frames = ticks_to_frames(
                updated_use.source_interval.end - updated_use.source_interval.start,
                source_clock,
                &format!("edit `{}` source length", edit.id.0),
            )?;
            if source_frames < new_end_frame - new_start_frame {
                return Err(unsupported(
                    "source_too_short",
                    format!(
                        "edit `{}` trims `{target}` beyond its source media",
                        edit.id.0
                    ),
                ));
            }
            let mut updated = model.clone();
            for use_ in &mut updated.clip_uses {
                if use_.id.0 == *target {
                    *use_ = updated_use.clone();
                }
            }
            validate_project_model(&updated)
                .map_err(|detail| invalid(format!("trim model rejected: {detail}")))?;
            Ok(updated)
        }
        "clip.move" | "clip.slide" => {
            let [target] = edit.target_ids.as_slice() else {
                return Err(invalid(format!(
                    "edit `{}` must target exactly one clip use; it targets {}",
                    edit.id.0,
                    edit.target_ids.len()
                )));
            };
            let clip_use = clip_use_by_id(model, target, edit)?.clone();
            let timeline = model
                .clocks
                .iter()
                .find(|c| c.id.0 == clip_use.timeline_interval.clock_id.0)
                .ok_or_else(|| {
                    unsupported(
                        "unknown_clock",
                        format!(
                            "edit `{}` targets `{target}` on an unknown timeline clock",
                            edit.id.0
                        ),
                    )
                })?;
            let after = after_object(edit)?;
            let to_frame = after
                .get("to_frame")
                .and_then(|v| v.as_i64())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.to_frame` as an integer timeline frame",
                        edit.id.0
                    ))
                })?;
            let to_tick =
                frames_to_timeline_ticks(to_frame, timeline, &format!("edit `{}` move target", edit.id.0))?;
            let duration = clip_use.timeline_interval.end - clip_use.timeline_interval.start;
            let mut updated_use = clip_use.clone();
            updated_use.timeline_interval.start = to_tick;
            updated_use.timeline_interval.end = to_tick + duration;
            let mut updated = model.clone();
            for use_ in &mut updated.clip_uses {
                if use_.id.0 == *target {
                    *use_ = updated_use.clone();
                }
            }
            updated.clip_uses.sort_by(|a, b| {
                (a.timeline_interval.start, &a.id.0).cmp(&(b.timeline_interval.start, &b.id.0))
            });
            validate_project_model(&updated)
                .map_err(|detail| invalid(format!("move model rejected: {detail}")))?;
            Ok(updated)
        }
        "clip.delete" => {
            let [target] = edit.target_ids.as_slice() else {
                return Err(invalid(format!(
                    "edit `{}` must target exactly one clip use; it targets {}",
                    edit.id.0,
                    edit.target_ids.len()
                )));
            };
            let clip_use = clip_use_by_id(model, target, edit)?.clone();
            let ripple = delete_is_ripple(edit)?;
            let deleted_end = clip_use.timeline_interval.end;
            let mut updated = model.clone();
            updated.clip_uses.retain(|u| u.id.0 != *target);
            if ripple {
                // Followers close the gap exactly as ren-core's ripple does:
                // every clip use at or after the deleted end shifts left by
                // the deleted length, on every track.
                let length = deleted_end - clip_use.timeline_interval.start;
                for use_ in &mut updated.clip_uses {
                    if use_.timeline_interval.start >= deleted_end {
                        use_.timeline_interval.start -= length;
                        use_.timeline_interval.end -= length;
                    }
                }
            }
            validate_project_model(&updated)
                .map_err(|detail| invalid(format!("delete model rejected: {detail}")))?;
            Ok(updated)
        }
        "clip.ripple" => {
            let [target] = edit.target_ids.as_slice() else {
                return Err(invalid(format!(
                    "edit `{}` must target exactly one clip use; it targets {}",
                    edit.id.0,
                    edit.target_ids.len()
                )));
            };
            let clip_use = clip_use_by_id(model, target, edit)?.clone();
            let delta = ripple_delta(edit)?;
            let timeline = model
                .clocks
                .iter()
                .find(|c| c.id.0 == clip_use.timeline_interval.clock_id.0)
                .ok_or_else(|| {
                    unsupported(
                        "unknown_clock",
                        format!(
                            "edit `{}` targets `{target}` on an unknown timeline clock",
                            edit.id.0
                        ),
                    )
                })?;
            // Relative frame delta to ticks: ticks_per_second * den / num.
            let (num, den) = clock_frame_rate(timeline)?;
            let delta_ticks = scale_exact(
                delta,
                i128::from(timeline.ticks_per_second) * i128::from(den),
                i128::from(num),
                &format!("edit `{}` ripple delta", edit.id.0),
            )?;
            let target_start = clip_use.timeline_interval.start;
            let mut updated = model.clone();
            for use_ in &mut updated.clip_uses {
                if use_.timeline_interval.start >= target_start {
                    use_.timeline_interval.start += delta_ticks;
                    use_.timeline_interval.end += delta_ticks;
                }
            }
            // The mutation refuses the same overlap the planning
            // pre-check refuses: the shifted model must stay projectable
            // (the projection refuses any overlapping timeline ranges).
            let mut spans: Vec<(i64, i64)> = updated
                .clip_uses
                .iter()
                .map(|u| (u.timeline_interval.start, u.timeline_interval.end))
                .collect();
            spans.sort();
            if spans.windows(2).any(|pair| pair[0].1 > pair[1].0) {
                return Err(unsupported(
                    "ripple_would_overwrite",
                    format!(
                        "edit `{}` ripples `{target}` by {delta} frames; the shifted run overlaps - refusing rather than trimming",
                        edit.id.0
                    ),
                ));
            }
            validate_project_model(&updated)
                .map_err(|detail| invalid(format!("ripple model rejected: {detail}")))?;
            Ok(updated)
        }
        "clip.link" => {
            // A link changes no spans: the mutation is a validated no-op so
            // link edits flow through the same expand-then-apply loop as the
            // splits and retimes around them. The engine-side effect (one
            // link group per piece) is proven on the receipts, not here.
            let [target] = edit.target_ids.as_slice() else {
                return Err(invalid(format!(
                    "edit `{}` must target exactly one clip use; it targets {}",
                    edit.id.0,
                    edit.target_ids.len()
                )));
            };
            let _ = clip_use_by_id(model, target, edit)?;
            Ok(model.clone())
        }
        "retime" | "span_retime" => {
            let [target] = edit.target_ids.as_slice() else {
                return Err(invalid(format!(
                    "edit `{}` must target exactly one clip use; it targets {}",
                    edit.id.0,
                    edit.target_ids.len()
                )));
            };
            let clip_use = clip_use_by_id(model, target, edit)?.clone();
            let timeline = model
                .clocks
                .iter()
                .find(|c| c.id.0 == clip_use.timeline_interval.clock_id.0)
                .ok_or_else(|| {
                    unsupported(
                        "unknown_clock",
                        format!(
                            "edit `{}` targets `{target}` on an unknown timeline clock",
                            edit.id.0
                        ),
                    )
                })?;
            let after = after_object(edit)?;
            let percent = after
                .get("percent")
                .and_then(|v| v.as_f64())
                .ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.percent` as a number",
                        edit.id.0
                    ))
                })?;
            // Direction needs no span math of its own: a reversed retime
            // shortens, lengthens and ripples exactly like a forward one at
            // the same percent, and the logged edit below carries the flag
            // so the frame map marks the direction.
            if let Some(value) = after.get("reverse") {
                if value.as_bool().is_none() {
                    return Err(invalid(format!(
                        "edit `{}` needs `after.reverse` as a boolean when present",
                        edit.id.0
                    )));
                }
            }
            let (old_start_frame, old_end_frame) =
                timeline_frames_of(model, timeline, &clip_use, edit)?;
            let new_len_frames =
                retimed_length(edit, percent, old_end_frame - old_start_frame)?;
            let old_end = clip_use.timeline_interval.end;
            let new_end = clip_use.timeline_interval.start
                + frames_to_timeline_ticks(
                    new_len_frames,
                    timeline,
                    &format!("edit `{}` retimed end", edit.id.0),
                )?;
            // The start is anchored; followers shift by the length delta
            // exactly as ren-core's ripple temporal effect does. A retime
            // preserves relative order by construction, so no overlap
            // check is needed.
            let delta = new_end - old_end;
            let mut updated = model.clone();
            for use_ in &mut updated.clip_uses {
                if use_.id.0 == *target {
                    use_.timeline_interval.end = new_end;
                } else if use_.timeline_interval.start >= old_end {
                    use_.timeline_interval.start += delta;
                    use_.timeline_interval.end += delta;
                }
            }
            // Log the retime so the frame map carries its exact rate.
            updated.edits.push(edit.clone());
            validate_project_model(&updated)
                .map_err(|detail| invalid(format!("retime model rejected: {detail}")))?;
            Ok(updated)
        }
        "retime.segments" => Err(unsupported(
            "retime_segments_not_supported",
            format!(
                "edit `{}` names a stepped speed ramp; the model mutation plans constant retimes only - expand it first with `expand_retime_ramp` into `clip.split` plus one constant `retime` per piece instead",
                edit.id.0
            ),
        )),
        other => Err(unsupported(
            "unsupported_edit_operation",
            format!(
                "edit `{}` names operation `{other}`; the model mutation maps split, trim, move, delete, ripple, link and constant retime",
                edit.id.0
            ),
        )),
    }
}

/// One constant-speed piece of an expanded stepped ramp: the post-split Ren
/// clip-use id, the percent the constant `retime` for that piece plays, and
/// whether it plays backwards. `start_frame` is the piece's timeline frame
/// before any retime lands.
#[derive(Clone, Debug, PartialEq)]
pub struct RetimeRampPiece {
    pub piece_id: String,
    pub start_frame: i64,
    pub percent: f64,
    pub reverse: bool,
}

/// A stepped `retime.segments` ramp expanded into executable Ren edits: one
/// `clip.split` per interior razor point in timeline order, then one constant
/// `retime` per piece in REVERSE timeline order. Reverse order matters: each
/// retime ripples its followers, so retiming the last piece first leaves
/// every earlier piece unmoved, and each earlier retime then shifts only
/// already-retimed followers - the final layout is the pieces back to back
/// at their retimed lengths with followers shifted by the total delta.
///
/// Every piece after the first is preceded by the two `clip.link` edits the
/// engine toggle needs to converge (clear, then link): a razor severs the
/// A/V link on later pieces (measured distinct video/audio link ids), and a
/// retime over severed ids ripples once per id instead of once. The first
/// piece keeps the intact link and needs no repair.
#[derive(Clone, Debug, PartialEq)]
pub struct ExpandedRetimeRamp {
    pub target_id: String,
    pub razor_frames: Vec<i64>,
    pub pieces: Vec<RetimeRampPiece>,
    pub edits: Vec<EditOperation>,
}

/// Expand a stepped `retime.segments` ramp into razor points plus one
/// constant `retime` per piece.
///
/// The planner (`plan_ren_edit`) and the model mutation
/// (`apply_ren_edit_to_model`) both refuse a ramp with
/// `retime_segments_not_supported` because neither can address the
/// post-razor piece ids a ramp needs. This helper is the named owner of that
/// expansion: it validates every piece exactly the way a constant retime is
/// validated (`retime_rate_inexact`, `retime_inexact_duration`), derives the
/// post-split piece ids with the same `{target}-a` / `{target}-b` naming the
/// split mutation mints, repairs each severed piece's A/V link ahead of its
/// retime (two `clip.link` edits; the first piece keeps the intact link),
/// carries each piece's direction into its retime, and returns the edits in
/// execution order. A ramp-level `reverse` keeps refusing with
/// `reverse_not_supported` (whole-ramp reversal is reversed piece order, not
/// a flag on this expansion); the engine's per-clip `reverse` flag is wired
/// through every constant retime instead.
pub fn expand_retime_ramp(
    model: &ProjectModel,
    edit: &EditOperation,
) -> Result<ExpandedRetimeRamp> {
    if edit.operation.as_str() != "retime.segments" {
        return Err(invalid(format!(
            "edit `{}` is not a stepped speed ramp; `expand_retime_ramp` expands `retime.segments` only",
            edit.id.0
        )));
    }
    let [target] = edit.target_ids.as_slice() else {
        return Err(invalid(format!(
            "edit `{}` must target exactly one clip use; it targets {}",
            edit.id.0,
            edit.target_ids.len()
        )));
    };
    let clip_use = clip_use_by_id(model, target, edit)?.clone();
    let timeline = model
        .clocks
        .iter()
        .find(|c| c.id.0 == clip_use.timeline_interval.clock_id.0)
        .ok_or_else(|| {
            unsupported(
                "unknown_clock",
                format!(
                    "edit `{}` targets `{target}` on an unknown timeline clock",
                    edit.id.0
                ),
            )
        })?;
    let after = after_object(edit)?;
    // A ramp-level reverse would play the whole ramp backwards - reversed
    // piece order, not just reversed pieces - which this expansion does not
    // express. It keeps refusing by name; mark reversed pieces individually
    // with per-segment `reverse` flags instead.
    if after
        .get("reverse")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
    {
        return Err(unsupported(
            "reverse_not_supported",
            format!(
                "edit `{}` reverses the whole ramp on `{target}`; the expansion plays pieces in timeline order - mark reversed pieces individually instead",
                edit.id.0
            ),
        ));
    }
    let segments = after
        .get("segments")
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            invalid(format!(
                "edit `{}` needs `after.segments` as a non-empty array of `{{record_frame, percent}}` steps",
                edit.id.0
            ))
        })?;
    if segments.is_empty() {
        return Err(invalid(format!(
            "edit `{}` names a stepped speed ramp with no segments",
            edit.id.0
        )));
    }
    let mut steps: Vec<(i64, f64, bool)> = Vec::with_capacity(segments.len());
    for (index, segment) in segments.iter().enumerate() {
        // Direction rides the piece retime's flag; span math below is
        // magnitude-only either way.
        let reverse = match segment.get("reverse") {
            None => false,
            Some(value) => value.as_bool().ok_or_else(|| {
                invalid(format!(
                    "edit `{}` names segment {index} with `reverse` as a non-boolean",
                    edit.id.0
                ))
            })?,
        };
        let record_frame = segment.get("record_frame").and_then(|v| v.as_i64()).ok_or_else(|| {
            invalid(format!(
                "edit `{}` names segment {index} without `record_frame` as an integer timeline frame",
                edit.id.0
            ))
        })?;
        let percent = segment
            .get("percent")
            .and_then(|v| v.as_f64())
            .ok_or_else(|| {
                invalid(format!(
                    "edit `{}` names segment {index} without `percent` as a number",
                    edit.id.0
                ))
            })?;
        steps.push((record_frame, percent, reverse));
    }
    steps.sort_by_key(|(frame, _, _)| *frame);
    let (start_frame, end_frame) = timeline_frames_of(model, timeline, &clip_use, edit)?;
    if steps[0].0 != start_frame {
        return Err(invalid(format!(
            "edit `{}` ramps `{target}` from frame {}, but the clip starts at {start_frame}; a ramp must cover its clip from the first frame",
            edit.id.0, steps[0].0
        )));
    }
    for pair in steps.windows(2) {
        if pair[0].0 >= pair[1].0 {
            return Err(invalid(format!(
                "edit `{}` ramps `{target}` with a repeated razor point at frame {}; segment starts must strictly increase",
                edit.id.0, pair[1].0
            )));
        }
    }
    for (frame, _, _) in steps.iter().skip(1) {
        if *frame <= start_frame || *frame >= end_frame {
            return Err(invalid(format!(
                "edit `{}` ramps `{target}` with a razor point at frame {frame}, outside ({start_frame}, {end_frame})",
                edit.id.0
            )));
        }
    }
    // Validate every piece exactly as a constant retime: exact rational rate
    // and exact whole-frame retimed length, never rounded.
    let mut bounds: Vec<i64> = steps.iter().map(|(frame, _, _)| *frame).collect();
    bounds.push(end_frame);
    for window in bounds.windows(2) {
        let piece_len = window[1] - window[0];
        let percent = steps
            .iter()
            .find(|(frame, _, _)| *frame == window[0])
            .map(|(_, percent, _)| *percent)
            .ok_or_else(|| {
                invalid(format!(
                    "edit `{}` ramps `{target}` with an unaligned piece at frame {}",
                    edit.id.0, window[0]
                ))
            })?;
        percent_to_rate(percent, &edit.id.0)?;
        retimed_length(edit, percent, piece_len)?;
    }
    // Derive post-split piece ids with the exact `{target}-a` / `{target}-b`
    // naming the split mutation mints, simulating the splits in order.
    let mut pieces: Vec<(String, i64, i64)> = vec![(target.clone(), start_frame, end_frame)];
    let mut split_targets: Vec<(String, i64)> = Vec::new();
    for (frame, _, _) in steps.iter().skip(1) {
        let index = pieces
            .iter()
            .position(|(_, s, e)| *s < *frame && *frame < *e)
            .ok_or_else(|| {
                invalid(format!(
                    "edit `{}` ramps `{target}` with a razor point at frame {frame} that lands on no open piece",
                    edit.id.0
                ))
            })?;
        let (id, s, e) = pieces.remove(index);
        split_targets.push((id.clone(), *frame));
        pieces.insert(index, (format!("{id}-a"), s, *frame));
        pieces.insert(index + 1, (format!("{id}-b"), *frame, e));
    }
    let razor_frames: Vec<i64> = steps.iter().skip(1).map(|(frame, _, _)| *frame).collect();
    let ramp_pieces: Vec<RetimeRampPiece> = pieces
        .iter()
        .map(|(piece_id, start, _)| {
            let (percent, reverse) = steps
                .iter()
                .find(|(frame, _, _)| *frame == *start)
                .map(|(_, percent, reverse)| (*percent, *reverse))
                .expect("piece starts align with segment starts");
            RetimeRampPiece {
                piece_id: piece_id.clone(),
                start_frame: *start,
                percent,
                reverse,
            }
        })
        .collect();
    let mut edits =
        Vec::with_capacity(split_targets.len() + ramp_pieces.len() + 2 * (ramp_pieces.len() - 1));
    for (index, (split_id, at_frame)) in split_targets.iter().enumerate() {
        edits.push(EditOperation {
            id: ren_protocol::project_model::EditOperationId(format!(
                "{}-split-{}",
                edit.id.0,
                index + 1
            )),
            operation: "clip.split".into(),
            target_ids: vec![split_id.clone()],
            before: None,
            after: Some(serde_json::json!({"at_frame": at_frame})),
            intent_id: edit.intent_id.clone(),
            evidence_ids: edit.evidence_ids.clone(),
            author: edit.author.clone(),
            parent_revision: None,
        });
    }
    // Retimes run last piece first so each ripple shifts only followers that
    // already carry their final length. Every piece after the first carries
    // its two link repairs ahead of its retime; the first piece keeps the
    // intact link and needs none.
    for (index, piece) in ramp_pieces.iter().enumerate().rev() {
        if index > 0 {
            for pass in 1..=2 {
                edits.push(EditOperation {
                    id: ren_protocol::project_model::EditOperationId(format!(
                        "{}-link-{index}-{pass}",
                        edit.id.0
                    )),
                    operation: "clip.link".into(),
                    target_ids: vec![piece.piece_id.clone()],
                    before: None,
                    after: Some(serde_json::json!({})),
                    intent_id: edit.intent_id.clone(),
                    evidence_ids: edit.evidence_ids.clone(),
                    author: edit.author.clone(),
                    parent_revision: None,
                });
            }
        }
        edits.push(EditOperation {
            id: ren_protocol::project_model::EditOperationId(format!(
                "{}-retime-{}",
                edit.id.0,
                index + 1
            )),
            operation: "retime".into(),
            target_ids: vec![piece.piece_id.clone()],
            before: None,
            after: Some(if piece.reverse {
                serde_json::json!({"percent": piece.percent, "reverse": true})
            } else {
                serde_json::json!({"percent": piece.percent})
            }),
            intent_id: edit.intent_id.clone(),
            evidence_ids: edit.evidence_ids.clone(),
            author: edit.author.clone(),
            parent_revision: None,
        });
    }
    Ok(ExpandedRetimeRamp {
        target_id: target.clone(),
        razor_frames,
        pieces: ramp_pieces,
        edits,
    })
}

fn clip_use_by_id<'m>(
    model: &'m ProjectModel,
    target: &str,
    edit: &EditOperation,
) -> Result<&'m ren_protocol::project_model::ClipUse> {
    model
        .clip_uses
        .iter()
        .find(|u| u.id.0 == target)
        .ok_or_else(|| {
            invalid(format!(
                "edit `{}` targets `{target}` which is not a clip use in the model",
                edit.id.0
            ))
        })
}

fn placement_by_id<'p>(
    projection: &'p Projection,
    target: &str,
    edit: &EditOperation,
) -> Result<&'p Placement> {
    projection
        .placements
        .iter()
        .find(|p| p.clip_use_id == target)
        .ok_or_else(|| {
            invalid(format!(
                "edit `{}` targets `{target}` which has no placement in the projection",
                edit.id.0
            ))
        })
}

fn ren_edit_target<'e>(
    edit: &'e EditOperation,
    context: &EditContext<'_>,
) -> Result<(&'e String, u64)> {
    edit_target(edit, context)
}

fn timeline_frames_of(
    _model: &ProjectModel,
    timeline: &Clock,
    clip_use: &ren_protocol::project_model::ClipUse,
    edit: &EditOperation,
) -> Result<(i64, i64)> {
    let start = ticks_to_frames(
        clip_use.timeline_interval.start,
        timeline,
        &format!("edit `{}` timeline start", edit.id.0),
    )?;
    // A timeline interval's end frame is start + length in frames: the
    // half-open end rarely converts exactly on its own, so derive it.
    let len = ticks_to_frames(
        clip_use.timeline_interval.end - clip_use.timeline_interval.start,
        timeline,
        &format!("edit `{}` timeline length", edit.id.0),
    )?;
    Ok((start, start + len))
}

/// Exact frame count to timeline ticks on `clock`.
fn frames_to_timeline_ticks(frame: i64, clock: &Clock, what: &str) -> Result<i64> {
    let (num, den) = clock_frame_rate(clock)?;
    scale_exact(
        frame,
        i128::from(clock.ticks_per_second) * i128::from(den),
        i128::from(num),
        what,
    )
}

/// Exact timeline frame count to FilmCraft engine ticks.
fn frames_to_engine_ticks(frame: i64, clock: &Clock, what: &str) -> Result<i64> {
    let (num, den) = clock_frame_rate(clock)?;
    scale_exact(
        frame,
        i128::from(ENGINE_TICKS_PER_SECOND) * i128::from(den),
        i128::from(num),
        what,
    )
}

fn gcd128(mut a: i128, mut b: i128) -> i128 {
    while b != 0 {
        let r = a % b;
        a = b;
        b = r;
    }
    a.max(1)
}

/// Exact rational playback speed from a Ren percent (200.0 -> 2/1).
///
/// Parses the shortest round-trip decimal, never the binary float, so clean
/// speeds stay exact. Refuses with `retime_rate_inexact` when the reduced
/// denominator cannot fit the frame-map Rational rather than rounding:
/// FilmCraft stores speed as a float, and the frame map must carry the
/// rational rate exactly.
fn percent_to_rate(percent: f64, edit_id: &str) -> Result<Rational> {
    if !percent.is_finite() || percent <= 0.0 {
        return Err(invalid(format!(
            "edit `{edit_id}` names a non-positive or non-finite speed of {percent}%"
        )));
    }
    let text = format!("{percent}");
    if text.contains(['e', 'E']) {
        return Err(unsupported(
            "retime_rate_inexact",
            format!(
                "edit `{edit_id}` retimes at {percent}%, whose decimal form is not an exact short fraction; refusing rather than rounding"
            ),
        ));
    }
    let (int_part, frac_part) = match text.split_once('.') {
        Some((i, f)) => (i, f),
        None => (text.as_str(), ""),
    };
    let mantissa: i128 = format!("{int_part}{frac_part}").parse().map_err(|_| {
        unsupported(
            "retime_rate_inexact",
            format!(
                "edit `{edit_id}` retimes at {percent}%, whose exact form overflows; refusing rather than rounding"
            ),
        )
    })?;
    let scale = 10i128
        .checked_pow(frac_part.len() as u32)
        .and_then(|s| s.checked_mul(100))
        .ok_or_else(|| {
            unsupported(
                "retime_rate_inexact",
                format!(
                    "edit `{edit_id}` retimes at {percent}%, whose exact denominator overflows; refusing rather than rounding"
                ),
            )
        })?;
    let divisor = gcd128(mantissa, scale);
    let num = mantissa / divisor;
    let den = scale / divisor;
    let numerator = i64::try_from(num).map_err(|_| {
        unsupported(
            "retime_rate_inexact",
            format!("edit `{edit_id}` retimes at {percent}%, whose exact numerator overflows i64"),
        )
    })?;
    let denominator = u32::try_from(den).map_err(|_| {
        unsupported(
            "retime_rate_inexact",
            format!("edit `{edit_id}` retimes at {percent}%, whose exact denominator overflows u32; refusing rather than rounding"),
        )
    })?;
    Ok(Rational {
        numerator,
        denominator,
    })
}

/// The logged retime speed for a clip use, if a `retime`/`span_retime` edit
/// in the model targets it with a parseable percent.
fn retime_rate_for(model: &ProjectModel, clip_use_id: &str) -> Option<Rational> {
    model.edits.iter().find_map(|edit| {
        if !matches!(edit.operation.as_str(), "retime" | "span_retime") {
            return None;
        }
        if !edit.target_ids.iter().any(|t| t == clip_use_id) {
            return None;
        }
        let percent = edit.after.as_ref()?.get("percent")?.as_f64()?;
        percent_to_rate(percent, &edit.id.0).ok()
    })
}

/// Whether the logged retime for a clip use plays backwards: direction is
/// orthogonal to the speed magnitude, so it reads the edit flag rather than
/// the rate.
fn retime_reversed(model: &ProjectModel, clip_use_id: &str) -> bool {
    model.edits.iter().any(|edit| {
        matches!(edit.operation.as_str(), "retime" | "span_retime")
            && edit.target_ids.iter().any(|t| t == clip_use_id)
            && edit
                .after
                .as_ref()
                .and_then(|after| after.get("reverse"))
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
    })
}

/// Slow motion keeps the whole source window and plays it slower, so a
/// timeline span longer than its source is projectable exactly when a
/// logged retime edit explains the ratio: `timeline * |num| == source * den`
/// in integer arithmetic, no rounding on either side. Direction is
/// irrelevant to the span ratio, so the comparison uses the magnitude.
fn retime_explains(
    model: &ProjectModel,
    clip_use_id: &str,
    source_frames: i64,
    timeline_frames: i64,
) -> bool {
    retime_rate_for(model, clip_use_id).is_some_and(|rate| {
        let magnitude = i128::from(rate.numerator).abs();
        i128::from(timeline_frames) * magnitude
            == i128::from(source_frames) * i128::from(rate.denominator)
    })
}

/// Exact retimed timeline length in frames: `old_len * 100 / percent`.
/// Refuses fractional results with `retime_inexact_duration` rather than
/// rounding: the engine rounds fractional retime spans to whole frames.
fn retimed_length(edit: &EditOperation, percent: f64, old_len_frames: i64) -> Result<i64> {
    let rate = percent_to_rate(percent, &edit.id.0)?;
    let product = i128::from(old_len_frames)
        .checked_mul(i128::from(rate.denominator))
        .ok_or_else(|| invalid(format!("edit `{}` retime scaling overflow", edit.id.0)))?;
    let num = i128::from(rate.numerator);
    if product % num != 0 {
        return Err(unsupported(
            "retime_inexact_duration",
            format!(
                "edit `{}` retimes {old_len_frames} frames at {percent}% ({} frames); refusing rather than rounding to whole frames",
                edit.id.0,
                product as f64 / num as f64,
            ),
        ));
    }
    i64::try_from(product / num)
        .map_err(|_| invalid(format!("edit `{}` retimed length overflows i64", edit.id.0)))
}

/// Engine ticks for a Ren move destination frame, compensating the measured
/// defect: `timeline.move` lands one frame early for every F > 0 (frame 0
/// is exact). Explicit `timeline_ticks` shapes pass through untouched.
fn move_ticks_for_frame(to_frame: i64, timeline: &Clock, what: &str) -> Result<i64> {
    let compensated = if to_frame > 0 { to_frame + 1 } else { 0 };
    frames_to_engine_ticks(compensated, timeline, what)
}

/// One compensated `timeline.move` per affected placement for `clip.ripple`.
///
/// ren-core's ripple shifts the target and every clip at or after its
/// timeline start, on every track; the engine's own ripple paths
/// (`clip.speedDuration` ripple, `edit.rippleDelete`) shift the same set
/// (measured). Order keeps every destination free in transit: right shifts
/// run last-first (each dest was vacated by its successor), left shifts run
/// first-last. A destination overlapping a same-track clip refuses with
/// `ripple_would_overwrite` instead of letting the engine trim it (the
/// move receipt reports such trims in `overwritten`, and a clean ripple
/// carries none). Cross-track overlap is fine and never refuses: the
/// engine stacks tracks.
///
/// Measured engine quirk (fourth defect): non-zero-frame placements store
/// a sub-frame-longer duration (~0.07 frames), so a ripple that lands
/// ABUTTING a neighbour contact-trims it - new engine ids, frame readback
/// fuzzed by one, reported as `move_overwrote` when the trimmed clip is
/// outside the moved set. Gapped destinations land fully clean; packed
/// timelines execute correctly at tick level with a visible loss.
#[allow(clippy::too_many_arguments)]
fn plan_ripple_moves(
    model: &ProjectModel,
    projection: &Projection,
    context: &EditContext<'_>,
    edit: &EditOperation,
    target_id: &str,
    clip_use: &ren_protocol::project_model::ClipUse,
    timeline: &Clock,
    delta: i64,
) -> Result<Vec<Operation>> {
    let project: PathBuf = context.project.into();
    let (target_start, _) = timeline_frames_of(model, timeline, clip_use, edit)?;
    struct RippleMove {
        clip_use_id: String,
        clip: u64,
        track: String,
        old_start: i64,
        old_end: i64,
    }
    let mut affected = Vec::new();
    for placement in &projection.placements {
        let len = ticks_to_frames(
            placement.timeline_end - placement.timeline_start,
            timeline,
            &format!("edit `{}` ripple length", edit.id.0),
        )?;
        let (start, end) = (placement.frame, placement.frame + len);
        if start < target_start {
            continue;
        }
        let ids = context
            .clip_ids
            .get(&placement.clip_use_id)
            .ok_or_else(|| {
                invalid(format!(
                    "edit `{}` ripples `{}` which has no reconciled engine clip id",
                    edit.id.0, placement.clip_use_id
                ))
            })?;
        affected.push(RippleMove {
            clip_use_id: placement.clip_use_id.clone(),
            clip: primary_clip_id(ids, &placement.clip_use_id)?,
            track: placement.track.clone(),
            old_start: start,
            old_end: end,
        });
    }
    if delta > 0 {
        affected.sort_by(|a, b| (b.old_start, &b.clip_use_id).cmp(&(a.old_start, &a.clip_use_id)));
    } else {
        affected.sort_by(|a, b| (a.old_start, &a.clip_use_id).cmp(&(b.old_start, &b.clip_use_id)));
    }
    // Simulate occupancy in execution order: every span at its current
    // position (old for not-yet-moved, new for moved, fixed for the rest).
    let mut spans: Vec<(String, i64, i64, String)> = Vec::new();
    for placement in &projection.placements {
        let len = ticks_to_frames(
            placement.timeline_end - placement.timeline_start,
            timeline,
            &format!("edit `{}` ripple span", edit.id.0),
        )?;
        spans.push((
            placement.track.clone(),
            placement.frame,
            placement.frame + len,
            placement.clip_use_id.clone(),
        ));
    }
    for ripple in &affected {
        let (new_start, new_end) = (ripple.old_start + delta, ripple.old_end + delta);
        for (track, start, end, id) in &spans {
            if *track != ripple.track || *id == ripple.clip_use_id {
                continue;
            }
            if new_start < *end && *start < new_end {
                return Err(unsupported(
                    "ripple_would_overwrite",
                    format!(
                        "edit `{}` ripples `{target_id}` by {delta} frames; `{}` would land on same-track `{id}` [{start}, {end}); refusing rather than trimming it",
                        edit.id.0, ripple.clip_use_id
                    ),
                ));
            }
        }
        if let Some(span) = spans.iter_mut().find(|s| s.3 == ripple.clip_use_id) {
            span.1 = new_start;
            span.2 = new_end;
        }
    }
    let mut operations = Vec::new();
    for ripple in &affected {
        let to_frame = ripple.old_start + delta;
        operations.push(Operation::Move {
            project: project.clone(),
            clip: ripple.clip,
            track: ripple.track.clone(),
            timeline_ticks: move_ticks_for_frame(
                to_frame,
                timeline,
                &format!("edit `{}` ripple target", edit.id.0),
            )?,
            insert: false,
        });
    }
    Ok(operations)
}
/// `after.ripple` for `clip.delete`: lift (false, the default, leaves a
/// gap) or ripple delete (true, followers close the gap).
fn delete_is_ripple(edit: &EditOperation) -> Result<bool> {
    if let Some(flag) = edit.after.as_ref().and_then(|v| v.get("ripple")) {
        if !flag.is_boolean() {
            return Err(invalid(format!(
                "edit `{}` needs `after.ripple` as a boolean",
                edit.id.0
            )));
        }
    }
    Ok(edit
        .after
        .as_ref()
        .and_then(|v| v.get("ripple"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false))
}

/// Integer `after.delta_frames` for `clip.ripple`.
fn ripple_delta(edit: &EditOperation) -> Result<i64> {
    let delta = edit
        .after
        .as_ref()
        .and_then(|v| v.get("delta_frames"))
        .and_then(|v| v.as_i64())
        .ok_or_else(|| {
            invalid(format!(
                "edit `{}` needs `after.delta_frames` as an integer",
                edit.id.0
            ))
        })?;
    if delta == 0 {
        return Err(unsupported(
            "empty_ripple",
            format!("edit `{}` ripples by 0 frames; nothing moves", edit.id.0),
        ));
    }
    Ok(delta)
}

/// Resolve a trim's new timeline frames from every `after` shape Ren emits.
///
/// Accepts explicit `in_frame`/`out_frame`, the E2E fixture's
/// `timeline_end`/`duration` pair, or an explicit `edge`/`delta_frames`
/// pair against the current frames. Missing edges keep the model's edge.
fn ren_trim_frames(
    edit: &EditOperation,
    after: &serde_json::Value,
    old_start: i64,
    old_end: i64,
) -> Result<(i64, i64)> {
    if let Some(edge) = after.get("edge").and_then(|v| v.as_str()) {
        let delta = after
            .get("delta_frames")
            .and_then(|v| v.as_i64())
            .ok_or_else(|| {
                invalid(format!(
                    "edit `{}` names an edge without an integer `after.delta_frames`",
                    edit.id.0
                ))
            })?;
        return match edge {
            "in" => Ok((old_start + delta, old_end)),
            "out" => Ok((old_start, old_end + delta)),
            other => Err(invalid(format!(
                "edit `{}` names unknown trim edge `{other}`",
                edit.id.0
            ))),
        };
    }
    let mut new_start = old_start;
    let mut new_end = old_end;
    if let Some(in_frame) = after.get("in_frame").and_then(|v| v.as_i64()) {
        new_start = in_frame;
    }
    if let Some(out_frame) = after.get("out_frame").and_then(|v| v.as_i64()) {
        new_end = out_frame;
    }
    if let Some(timeline_end) = after.get("timeline_end").and_then(|v| v.as_i64()) {
        new_end = timeline_end;
        if let Some(duration) = after.get("duration").and_then(|v| v.as_i64()) {
            new_start = timeline_end - duration;
        }
    } else if let Some(duration) = after.get("duration").and_then(|v| v.as_i64()) {
        // A bare duration shortens the out edge, keeping the start.
        new_end = new_start + duration;
    }
    Ok((new_start, new_end))
}

/// Turn one `timeline.patch` inner algebra operation into a synthetic
/// [`EditOperation`] the Ren translator can plan directly.
///
/// Inner operations name their algebra id under `op` (Python `to_dict`
/// shape) with the op's fields beside it; the inner `unique_id` becomes
/// the single target. Only the translatable timeline writes map here;
/// anything else becomes a synthetic edit whose operation string carries
/// the refusal through `plan_ren_edit` with its named code.
fn patch_inner_to_edit(
    outer: &EditOperation,
    index: usize,
    inner: &serde_json::Value,
) -> Result<EditOperation> {
    let object = inner.as_object().ok_or_else(|| {
        invalid(format!(
            "edit `{}` inner op {index} is not an object",
            outer.id.0
        ))
    })?;
    let op_id = object
        .get("op")
        .or_else(|| object.get("id"))
        .or_else(|| object.get("operation"))
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            invalid(format!(
                "edit `{}` inner op {index} names no `op` algebra id",
                outer.id.0
            ))
        })?;
    let target = object
        .get("unique_id")
        .and_then(|v| v.as_str())
        .map(str::to_owned)
        .unwrap_or_default();
    // The inner op's own fields become the synthetic `after`, minus the
    // identity keys that are addressing, not parameters.
    let mut after = serde_json::Map::new();
    for (key, value) in object {
        if matches!(key.as_str(), "op" | "id" | "operation" | "unique_id") {
            continue;
        }
        after.insert(key.clone(), value.clone());
    }
    // Normalise the two field spellings Ren emits: `at_frame` (algebra)
    // and `frame` (qualification shorthand) both address a split point;
    // `db` (algebra `audio.gain`) and `gain_db` (driver) both name gain.
    Ok(EditOperation {
        id: ren_protocol::project_model::EditOperationId(format!("{}#p{index}", outer.id.0)),
        operation: op_id.into(),
        target_ids: if target.is_empty() {
            vec![]
        } else {
            vec![target]
        },
        before: None,
        after: Some(serde_json::Value::Object(after)),
        intent_id: outer.intent_id.clone(),
        evidence_ids: vec![],
        author: outer.author.clone(),
        parent_revision: None,
    })
}

/// Exact timeline-to-render frame map for a projection. Render output packs
/// placements in timeline order from render tick zero; each placement maps
/// onto its render span and names its source clip use. Placements with a
/// logged constant retime map [`MappingKind::Retimed`] with the exact
/// playback speed; everything else maps one-to-one.
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
        // A logged constant retime marks its placement Retimed with the
        // exact playback speed (2/1 at 200%): the engine's `speed` field as
        // a rational. A reversed retime negates the numerator (-1/1 at full
        // reverse): the sign IS the direction, the magnitude the speed, and
        // ren-core's tick math refuses negative rates loudly rather than
        // mapping them forwards. Spans stay timeline-to-render packing - the
        // rate documents playback rather than the span affine.
        let (mapping_kind, rate) = match retime_rate_for(model, &placement.clip_use_id) {
            Some(speed) => {
                let rate = if retime_reversed(model, &placement.clip_use_id) {
                    Rational {
                        numerator: speed.numerator.checked_neg().ok_or_else(|| {
                            invalid(format!(
                                "reversed rate for `{}` overflows",
                                placement.clip_use_id
                            ))
                        })?,
                        denominator: speed.denominator,
                    }
                } else {
                    speed
                };
                (MappingKind::Retimed, rate)
            }
            None => (
                MappingKind::OneToOne,
                Rational {
                    numerator: 1,
                    denominator: 1,
                },
            ),
        };
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
            mapping_kind,
            rate: Some(rate),
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
    fn unknown_transition_effects_refuse_at_plan_time() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let context = EditContext {
            project: Path::new("test.fcproj"),
            clip_ids: &BTreeMap::from([("use-a".to_owned(), vec![7u64])]),
        };
        // Names the engine does not list refuse on every planning path,
        // before any driver operation is produced.
        for (operation, after) in [
            (
                "filmcraft.transition",
                serde_json::json!({"edge": "in", "frames": 4, "effect": "smooth_cut"}),
            ),
            (
                "transition",
                serde_json::json!({"edge": "in", "frames": 4, "effect": "spin"}),
            ),
            (
                "transition",
                serde_json::json!({"transition": {"edge": "out", "frames": 4, "effect": "bogus_xyz_nope"}}),
            ),
        ] {
            let edit = filmcraft_edit("edit-transition", operation, after);
            let err = if operation.starts_with("filmcraft.") {
                plan_edit(&edit, &context).unwrap_err()
            } else {
                plan_ren_edit(&model, &projection, &edit, &context).unwrap_err()
            };
            assert!(
                matches!(err, AdapterError::Unsupported { ref code, .. } if code == "unknown_transition_effect"),
                "{operation} must refuse the unknown effect, got {err:?}"
            );
        }
        // Known ids, display names and case/space variants still plan, as
        // does the omitted effect (the engine default transition).
        for effect in [
            serde_json::json!({"edge": "in", "frames": 4, "effect": "cross_dissolve"}),
            serde_json::json!({"edge": "in", "frames": 4, "effect": "Cross Dissolve"}),
            serde_json::json!({"edge": "in", "frames": 4, "effect": "SLIDE"}),
            serde_json::json!({"edge": "in", "frames": 4}),
        ] {
            let edit = filmcraft_edit("edit-transition", "transition", effect.clone());
            let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
            assert_eq!(operations.len(), 1);
            assert!(matches!(operations[0], Operation::Transition { .. }));
            let edit = filmcraft_edit("edit-transition", "filmcraft.transition", effect);
            assert!(matches!(
                plan_edit(&edit, &context).unwrap(),
                Operation::Transition { .. }
            ));
        }
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

    fn ren_edit_context<'a>(clip_ids: &'a BTreeMap<String, Vec<u64>>) -> EditContext<'a> {
        EditContext {
            project: Path::new("test.fcproj"),
            clip_ids,
        }
    }

    fn ren_edit(
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
            author: "unit".into(),
            parent_revision: None,
        }
    }

    #[test]
    fn ren_split_drives_video_plus_linked_audio_explicitly() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        // use-a carries an audio stream, so the plan must drive both tracks.
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64, 8u64])]);
        let context = ren_edit_context(&clip_ids);
        let edit = ren_edit(
            "edit-ren-split",
            "clip.split",
            "use-a",
            serde_json::json!({"at_frame": 12}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert_eq!(
            operations.len(),
            2,
            "linked audio must be driven explicitly"
        );
        assert!(matches!(
            &operations[0],
            Operation::Razor { track, frame: 12, .. } if track == "V1"
        ));
        assert!(matches!(
            &operations[1],
            Operation::Razor { track, frame: 12, .. } if track == "A1"
        ));
        // A placement without audio plans a single video razor.
        let clip_ids = BTreeMap::from([("use-b".to_owned(), vec![9u64])]);
        let context = ren_edit_context(&clip_ids);
        let edit = ren_edit(
            "edit-ren-split-b",
            "clip.split",
            "use-b",
            serde_json::json!({"at_frame": 30}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::Razor { track, frame: 30, .. } if track == "V2"
        ));
    }

    #[test]
    fn ren_trim_resolves_both_edges_to_regular_trims() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64])]);
        let context = ren_edit_context(&clip_ids);
        // Out edge only: 24 -> 20.
        let edit = ren_edit(
            "edit-ren-trim-out",
            "clip.trim",
            "use-a",
            serde_json::json!({"in_frame": 0, "out_frame": 20}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::Trim {
                clip: 7,
                edge: TrimEdge::Out,
                mode: TrimMode::Regular,
                delta_frames: -4,
                ..
            }
        ));
        // Both edges move: two trims, in then out.
        let edit = ren_edit(
            "edit-ren-trim-both",
            "clip.trim",
            "use-a",
            serde_json::json!({"in_frame": 2, "out_frame": 20}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 2);
        assert!(matches!(
            &operations[0],
            Operation::Trim {
                edge: TrimEdge::In,
                delta_frames: 2,
                ..
            }
        ));
        assert!(matches!(
            &operations[1],
            Operation::Trim {
                edge: TrimEdge::Out,
                delta_frames: -4,
                ..
            }
        ));
    }

    #[test]
    fn ren_trim_accepts_e2e_fixture_shape() {
        let json =
            include_str!("../../../tests/fixtures/project_model_e2e/expected-project-model.json");
        let model = decode_project_model(json).expect("e2e fixture decodes");
        // The fixture stores the POST-trim state (Ada 0-36); rewind to the
        // pre-trim layout (Ada 0-48, followers shifted +12) so the stored
        // edit record plans a real -12 out-edge delta.
        let mut pre = model.clone();
        for use_ in &mut pre.clip_uses {
            match use_.id.0.as_str() {
                "use-ada" => {
                    use_.timeline_interval.end = 48;
                }
                "use-ben" => {
                    use_.timeline_interval.start = 48;
                    use_.timeline_interval.end = 96;
                }
                "use-broll" => {
                    use_.timeline_interval.start = 96;
                    use_.timeline_interval.end = 120;
                }
                _ => {}
            }
        }
        let projection = plan_projection(&pre).unwrap();
        let clip_ids = BTreeMap::from([
            ("use-ada".to_owned(), vec![11u64]),
            ("use-ben".to_owned(), vec![12u64]),
            ("use-broll".to_owned(), vec![13u64]),
        ]);
        let context = ren_edit_context(&clip_ids);
        // The fixture's own edit record: Ada 48 -> 36 on the out edge.
        let stored = model
            .edits
            .iter()
            .find(|e| e.operation == "clip.trim")
            .expect("fixture carries a clip.trim edit");
        let operations = plan_ren_edit(&pre, &projection, stored, &context).unwrap();
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::Trim {
                clip: 11,
                edge: TrimEdge::Out,
                mode: TrimMode::Regular,
                delta_frames: -12,
                ..
            }
        ));
    }

    #[test]
    fn ren_move_transition_and_gain_map_to_driver_commands() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64])]);
        let context = ren_edit_context(&clip_ids);
        let edit = ren_edit(
            "edit-ren-move",
            "clip.move",
            "use-a",
            serde_json::json!({"to_frame": 48}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        match &operations[0] {
            Operation::Move {
                clip: 7,
                track,
                timeline_ticks,
                insert: false,
                ..
            } => {
                assert_eq!(track, "V1");
                // The move compensates the measured one-frame-early defect.
                assert_eq!(*timeline_ticks, 49 * ENGINE_TICKS_PER_SECOND / 24);
            }
            other => panic!("expected a local V1 move, got {other:?}"),
        }
        let edit = ren_edit(
            "edit-ren-transition",
            "transition",
            "use-a",
            serde_json::json!({"edge": "in", "frames": 4}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert!(matches!(
            &operations[0],
            Operation::Transition {
                clip: 7,
                edge: TrimEdge::In,
                frames: 4,
                ..
            }
        ));
        let edit = ren_edit(
            "edit-ren-gain",
            "audio.gain",
            "use-a",
            serde_json::json!({"db": -3.0}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert!(matches!(
            &operations[0],
            Operation::AudioGain { track, gain_db, .. }
            if track == "A1" && *gain_db == -3.0
        ));
    }

    #[test]
    fn ren_patch_expands_inner_algebra_operations() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64, 8u64])]);
        let context = ren_edit_context(&clip_ids);
        let edit = EditOperation {
            id: ren_protocol::project_model::EditOperationId("edit-patch-1".into()),
            operation: "timeline.patch".into(),
            target_ids: vec!["capability:edit".into()],
            before: None,
            after: Some(serde_json::json!({
                "operations": [
                    {"op": "clip.split", "unique_id": "use-a", "at_frame": 12},
                ],
            })),
            intent_id: None,
            evidence_ids: vec![],
            author: "unit".into(),
            parent_revision: None,
        };
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        // The inner split drives video plus linked audio.
        assert_eq!(operations.len(), 2);
        assert!(operations
            .iter()
            .all(|op| matches!(op, Operation::Razor { .. })));
    }

    #[test]
    fn ren_unrepresentable_operations_refuse_with_named_codes() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64])]);
        let context = ren_edit_context(&clip_ids);
        for (operation, after, code) in [
            (
                "retime.segments",
                serde_json::json!({"segments": [{"record_frame": 12, "percent": 200.0}]}),
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
            (
                "edit_graph.aroll",
                serde_json::json!({}),
                "plan_level_not_executable",
            ),
            (
                "composed.extend",
                serde_json::json!({}),
                "plan_level_not_executable",
            ),
        ] {
            let edit = ren_edit("edit-refuse", operation, "use-a", after);
            let err = plan_ren_edit(&model, &projection, &edit, &context).unwrap_err();
            assert!(
                matches!(&err, AdapterError::Unsupported { code: got, .. } if got == code),
                "{operation} must refuse as {code}, got {err:?}"
            );
        }
        // A ripple landing on a same-track predecessor refuses instead of
        // trimming it: with both uses on V1, use-b (24-48) shifted -30
        // would cover use-a (0-24).
        let mut stacked = minimal_model();
        stacked.clip_uses[0].role = "a_roll".into();
        let projection = plan_projection(&stacked).unwrap();
        let clip_ids = BTreeMap::from([
            ("use-a".to_owned(), vec![7u64]),
            ("use-b".to_owned(), vec![9u64]),
        ]);
        let context = ren_edit_context(&clip_ids);
        let edit = ren_edit(
            "edit-ripple-overwrite",
            "clip.ripple",
            "use-b",
            serde_json::json!({"delta_frames": -30}),
        );
        let err = plan_ren_edit(&stacked, &projection, &edit, &context).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "ripple_would_overwrite"),
            "overlapping ripple must refuse by name, got {err:?}"
        );
        // The model mutation refuses with the same code.
        let model_err = apply_ren_edit_to_model(&stacked, &edit).unwrap_err();
        assert!(
            format!("{model_err}").contains("ripple_would_overwrite"),
            "model mutation must refuse with the same code, got {model_err}"
        );
        // Gain on a placement without audio refuses by name.
        let clip_ids = BTreeMap::from([("use-b".to_owned(), vec![9u64])]);
        let context = ren_edit_context(&clip_ids);
        let edit = ren_edit(
            "edit-gain-noaudio",
            "audio.gain",
            "use-b",
            serde_json::json!({"db": -3.0}),
        );
        let err = plan_ren_edit(&model, &projection, &edit, &context).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "no_audio_for_gain"),
            "gain without audio must refuse by name, got {err:?}"
        );
    }

    #[test]
    fn ren_model_mutation_matches_planned_trims_and_moves() {
        let model = minimal_model();
        // Trim use-a 24 -> 20 on the out edge; the mutated model's
        // projection must carry the shorter placement the plan describes.
        let edit = ren_edit(
            "edit-mutate-trim",
            "clip.trim",
            "use-a",
            serde_json::json!({"in_frame": 0, "out_frame": 20}),
        );
        let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
        let use_a = mutated
            .clip_uses
            .iter()
            .find(|u| u.id.0 == "use-a")
            .unwrap();
        assert_eq!(
            (use_a.timeline_interval.start, use_a.timeline_interval.end),
            (0, 20)
        );
        let projection = plan_projection(&mutated).unwrap();
        assert_eq!(projection.placements[0].clip_use_id, "use-a");
        assert_eq!(
            projection.placements[0].duration_ticks,
            20 * ENGINE_TICKS_PER_SECOND / 24
        );
        // Split use-a at 12: two clip uses back to back, same total span.
        let edit = ren_edit(
            "edit-mutate-split",
            "clip.split",
            "use-a",
            serde_json::json!({"at_frame": 12}),
        );
        let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
        let halves: Vec<_> = mutated
            .clip_uses
            .iter()
            .filter(|u| u.id.0 == "use-a-a" || u.id.0 == "use-a-b")
            .collect();
        assert_eq!(halves.len(), 2);
        assert_eq!(
            (
                halves[0].timeline_interval.start,
                halves[0].timeline_interval.end
            ),
            (0, 12)
        );
        assert_eq!(
            (
                halves[1].timeline_interval.start,
                halves[1].timeline_interval.end
            ),
            (12, 24)
        );
    }

    #[test]
    fn percent_to_rate_is_exact_or_refuses() {
        for (percent, num, den) in [
            (200.0, 2, 1),
            (50.0, 1, 2),
            (150.0, 3, 2),
            (100.0, 1, 1),
            (12.5, 1, 8),
            (25.0, 1, 4),
        ] {
            let rate = percent_to_rate(percent, "edit-rate").unwrap();
            assert_eq!(
                (rate.numerator, rate.denominator),
                (num, den),
                "{percent}% must be exact"
            );
        }
        for percent in [33.333333333333336, 123456789.123456789, 1e300] {
            let err = percent_to_rate(percent, "edit-rate").unwrap_err();
            assert!(
                matches!(err, AdapterError::Unsupported { ref code, .. } if code == "retime_rate_inexact"),
                "{percent}% must refuse as retime_rate_inexact, got {err:?}"
            );
        }
        for percent in [0.0, -50.0, f64::NAN, f64::INFINITY] {
            assert!(
                percent_to_rate(percent, "edit-rate").is_err(),
                "{percent}% must refuse"
            );
        }
    }

    #[test]
    fn ren_delete_plans_lift_or_ripple_delete() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64, 8u64])]);
        let context = ren_edit_context(&clip_ids);
        let lift = ren_edit(
            "edit-delete-lift",
            "clip.delete",
            "use-a",
            serde_json::json!({}),
        );
        let operations = plan_ren_edit(&model, &projection, &lift, &context).unwrap();
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::Delete { clips, .. } if clips == &vec![7u64]
        ));
        // The ripple flag defaults off; an explicit false still lifts.
        let lift = ren_edit(
            "edit-delete-lift-false",
            "clip.delete",
            "use-a",
            serde_json::json!({"ripple": false}),
        );
        let operations = plan_ren_edit(&model, &projection, &lift, &context).unwrap();
        assert!(matches!(&operations[0], Operation::Delete { .. }));
        let ripple = ren_edit(
            "edit-delete-ripple",
            "clip.delete",
            "use-a",
            serde_json::json!({"ripple": true}),
        );
        let operations = plan_ren_edit(&model, &projection, &ripple, &context).unwrap();
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::RippleDelete { clips, .. } if clips == &vec![7u64]
        ));
        // A non-boolean ripple flag is malformed, not a guess.
        let bad = ren_edit(
            "edit-delete-bad",
            "clip.delete",
            "use-a",
            serde_json::json!({"ripple": "yes"}),
        );
        assert!(plan_ren_edit(&model, &projection, &bad, &context).is_err());
    }

    #[test]
    fn ren_ripple_plans_compensated_moves_in_free_order() {
        let mut stacked = minimal_model();
        stacked.clip_uses[0].role = "a_roll".into();
        let projection = plan_projection(&stacked).unwrap();
        let clip_ids = BTreeMap::from([
            ("use-a".to_owned(), vec![7u64]),
            ("use-b".to_owned(), vec![9u64]),
        ]);
        let context = ren_edit_context(&clip_ids);
        // Right shift runs last-first so every destination is free.
        let edit = ren_edit(
            "edit-ripple-right",
            "clip.ripple",
            "use-a",
            serde_json::json!({"delta_frames": 12}),
        );
        let operations = plan_ren_edit(&stacked, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 2);
        match (&operations[0], &operations[1]) {
            (
                Operation::Move {
                    clip: 9,
                    timeline_ticks: second,
                    insert: false,
                    ..
                },
                Operation::Move {
                    clip: 7,
                    timeline_ticks: first,
                    insert: false,
                    ..
                },
            ) => {
                // use-b 24 -> 36, use-a 0 -> 12, both compensated +1.
                assert_eq!(*second, 37 * ENGINE_TICKS_PER_SECOND / 24);
                assert_eq!(*first, 13 * ENGINE_TICKS_PER_SECOND / 24);
            }
            other => panic!("expected two compensated moves, got {other:?}"),
        }
        // Left shift runs first-last; open a gap so the destination is
        // free (a packed left shift lands on its predecessor and refuses).
        stacked.clip_uses[0].timeline_interval.start = 36;
        stacked.clip_uses[0].timeline_interval.end = 60;
        let projection = plan_projection(&stacked).unwrap();
        let edit = ren_edit(
            "edit-ripple-left",
            "clip.ripple",
            "use-b",
            serde_json::json!({"delta_frames": -12}),
        );
        let operations = plan_ren_edit(&stacked, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        match &operations[0] {
            Operation::Move {
                clip: 9,
                timeline_ticks,
                insert: false,
                ..
            } => {
                // use-b 36 -> 24, compensated +1. use-a is upstream and stays.
                assert_eq!(*timeline_ticks, 25 * ENGINE_TICKS_PER_SECOND / 24);
            }
            other => panic!("expected one compensated move, got {other:?}"),
        }
    }

    #[test]
    fn ren_retime_plans_single_ripple_speed() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64])]);
        let context = ren_edit_context(&clip_ids);
        for operation in ["retime", "span_retime"] {
            let edit = ren_edit(
                "edit-retime",
                operation,
                "use-a",
                serde_json::json!({"percent": 200.0}),
            );
            let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
            assert_eq!(operations.len(), 1);
            assert!(matches!(
                &operations[0],
                Operation::Retime {
                    clips,
                    speed_percent,
                    ripple: true,
                    ..
                } if clips == &vec![7u64] && *speed_percent == 200.0
            ));
        }
    }

    #[test]
    fn ren_retime_drives_every_reconciled_clip() {
        // A use with linked audio reconciles to video plus audio ids; the
        // planned retime carries both in one call so a severed audio piece
        // retimes with its picture. A video-only use still plans one id.
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64, 8u64])]);
        let context = ren_edit_context(&clip_ids);
        let edit = ren_edit(
            "edit-retime-av",
            "retime",
            "use-a",
            serde_json::json!({"percent": 50.0}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::Retime {
                clips,
                speed_percent,
                ripple: true,
                ..
            } if clips == &vec![7u64, 8u64] && *speed_percent == 50.0
        ));
        let clip_ids = BTreeMap::from([("use-b".to_owned(), vec![9u64])]);
        let context = ren_edit_context(&clip_ids);
        let edit = ren_edit(
            "edit-retime-v",
            "retime",
            "use-b",
            serde_json::json!({"percent": 200.0}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert!(matches!(
            &operations[0],
            Operation::Retime { clips, .. } if clips == &vec![9u64]
        ));
    }

    #[test]
    fn ren_link_plans_grouped_ids_and_mutates_nothing() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64, 8u64])]);
        let context = ren_edit_context(&clip_ids);
        let edit = ren_edit("edit-link", "clip.link", "use-a", serde_json::json!({}));
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::Link { clips, .. } if clips == &vec![7u64, 8u64]
        ));
        // The mutation is a validated no-op: spans are untouched.
        let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
        assert_eq!(mutated.clip_uses, model.clip_uses);
        // An unknown target refuses on both paths.
        let edit = ren_edit(
            "edit-link-ghost",
            "clip.link",
            "use-ghost",
            serde_json::json!({}),
        );
        assert!(plan_ren_edit(&model, &projection, &edit, &context).is_err());
        assert!(apply_ren_edit_to_model(&model, &edit).is_err());
    }

    #[test]
    fn ren_reverse_plans_flagged_speed_with_negative_map_rate() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64, 8u64])]);
        let context = ren_edit_context(&clip_ids);
        // Reverse plans onto the engine flag: same span math as forward at
        // the same percent, direction carried separately.
        let edit = ren_edit(
            "edit-reverse",
            "retime",
            "use-a",
            serde_json::json!({"percent": 200.0, "reverse": true}),
        );
        let operations = plan_ren_edit(&model, &projection, &edit, &context).unwrap();
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::Retime {
                clips,
                speed_percent,
                ripple: true,
                reverse: true,
                ..
            } if clips == &vec![7u64, 8u64] && *speed_percent == 200.0
        ));
        let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
        let use_a = mutated
            .clip_uses
            .iter()
            .find(|u| u.id.0 == "use-a")
            .unwrap();
        assert_eq!(
            (use_a.timeline_interval.start, use_a.timeline_interval.end),
            (0, 12)
        );
        // The map marks the direction with the sign: -2/1, never forward.
        let projection = plan_projection(&mutated).unwrap();
        let map = build_frame_map(&mutated, &projection, "clock-render", "map-rev").unwrap();
        assert_eq!(map.segments[0].mapping_kind, MappingKind::Retimed);
        assert_eq!(
            map.segments[0].rate,
            Some(Rational {
                numerator: -2,
                denominator: 1
            })
        );
        // A reversed slow-down still re-projects: the span ratio is
        // magnitude-only, so the logged retime explains the longer span.
        let edit = ren_edit(
            "edit-reverse-slow",
            "span_retime",
            "use-a",
            serde_json::json!({"percent": 50.0, "reverse": true}),
        );
        let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
        let projection = plan_projection(&mutated).unwrap();
        let map = build_frame_map(&mutated, &projection, "clock-render", "map-rev-slow").unwrap();
        assert_eq!(
            map.segments[0].rate,
            Some(Rational {
                numerator: -1,
                denominator: 2
            })
        );
        assert_eq!(render_span(&map), 72);
        // A non-boolean reverse flag is caller error on both paths.
        let edit = ren_edit(
            "edit-reverse-bad",
            "retime",
            "use-a",
            serde_json::json!({"percent": 200.0, "reverse": "yes"}),
        );
        assert!(matches!(
            plan_ren_edit(&model, &projection, &edit, &context).unwrap_err(),
            AdapterError::InvalidModel(_)
        ));
        assert!(matches!(
            apply_ren_edit_to_model(&model, &edit).unwrap_err(),
            AdapterError::InvalidModel(_)
        ));
    }

    #[test]
    fn ren_model_mutation_matches_planned_deletes_ripples_retimes() {
        let mut stacked = minimal_model();
        stacked.clip_uses[0].role = "a_roll".into();
        // Lift: the target goes, followers stay, a gap remains.
        let edit = ren_edit(
            "edit-mutate-lift",
            "clip.delete",
            "use-a",
            serde_json::json!({}),
        );
        let mutated = apply_ren_edit_to_model(&stacked, &edit).unwrap();
        assert_eq!(mutated.clip_uses.len(), 1);
        assert_eq!(mutated.clip_uses[0].id.0, "use-b");
        assert_eq!(
            (
                mutated.clip_uses[0].timeline_interval.start,
                mutated.clip_uses[0].timeline_interval.end
            ),
            (24, 48)
        );
        // Ripple delete: followers close the gap.
        let edit = ren_edit(
            "edit-mutate-ripple-delete",
            "clip.delete",
            "use-a",
            serde_json::json!({"ripple": true}),
        );
        let mutated = apply_ren_edit_to_model(&stacked, &edit).unwrap();
        assert_eq!(mutated.clip_uses.len(), 1);
        assert_eq!(
            (
                mutated.clip_uses[0].timeline_interval.start,
                mutated.clip_uses[0].timeline_interval.end
            ),
            (0, 24)
        );
        // Standalone ripple shifts the target and everything downstream.
        let edit = ren_edit(
            "edit-mutate-ripple",
            "clip.ripple",
            "use-a",
            serde_json::json!({"delta_frames": 12}),
        );
        let mutated = apply_ren_edit_to_model(&stacked, &edit).unwrap();
        let starts: Vec<(String, i64, i64)> = {
            let mut uses: Vec<_> = mutated.clip_uses.iter().collect();
            uses.sort_by_key(|u| u.timeline_interval.start);
            uses.into_iter()
                .map(|u| {
                    (
                        u.id.0.clone(),
                        u.timeline_interval.start,
                        u.timeline_interval.end,
                    )
                })
                .collect()
        };
        assert_eq!(
            starts,
            vec![("use-a".to_owned(), 12, 36), ("use-b".to_owned(), 36, 60),]
        );
        // Speed-up anchors the start and shortens; the map carries 2/1.
        let edit = ren_edit(
            "edit-mutate-retime-up",
            "retime",
            "use-a",
            serde_json::json!({"percent": 200.0}),
        );
        let mutated = apply_ren_edit_to_model(&stacked, &edit).unwrap();
        let use_a = mutated
            .clip_uses
            .iter()
            .find(|u| u.id.0 == "use-a")
            .unwrap();
        assert_eq!(
            (use_a.timeline_interval.start, use_a.timeline_interval.end),
            (0, 12)
        );
        let use_b = mutated
            .clip_uses
            .iter()
            .find(|u| u.id.0 == "use-b")
            .unwrap();
        assert_eq!(
            (use_b.timeline_interval.start, use_b.timeline_interval.end),
            (12, 36)
        );
        let projection = plan_projection(&mutated).unwrap();
        let map = build_frame_map(&mutated, &projection, "clock-render", "map-retime").unwrap();
        assert_eq!(map.segments[0].mapping_kind, MappingKind::Retimed);
        assert_eq!(
            map.segments[0].rate,
            Some(Rational {
                numerator: 2,
                denominator: 1
            })
        );
        assert_eq!(map.segments[1].mapping_kind, MappingKind::OneToOne);
        assert_eq!(render_span(&map), 36);
        // Slow-down doubles the span and still re-projects: the logged
        // retime edit explains the longer timeline span.
        let edit = ren_edit(
            "edit-mutate-retime-down",
            "retime",
            "use-a",
            serde_json::json!({"percent": 50.0}),
        );
        let mutated = apply_ren_edit_to_model(&stacked, &edit).unwrap();
        let use_a = mutated
            .clip_uses
            .iter()
            .find(|u| u.id.0 == "use-a")
            .unwrap();
        assert_eq!(
            (use_a.timeline_interval.start, use_a.timeline_interval.end),
            (0, 48)
        );
        let projection = plan_projection(&mutated).unwrap();
        let map = build_frame_map(&mutated, &projection, "clock-render", "map-slow").unwrap();
        assert_eq!(map.segments[0].mapping_kind, MappingKind::Retimed);
        assert_eq!(
            map.segments[0].rate,
            Some(Rational {
                numerator: 1,
                denominator: 2
            })
        );
        assert_eq!(render_span(&map), 72);
    }

    #[test]
    fn retime_ramp_expands_to_splits_then_reverse_order_retimes() {
        // use-a spans (0, 24): two 12-frame pieces, fast then slow.
        let model = minimal_model();
        let ramp = ren_edit(
            "edit-ramp",
            "retime.segments",
            "use-a",
            serde_json::json!({"segments": [
                {"record_frame": 12, "percent": 50.0},
                {"record_frame": 0, "percent": 200.0},
            ]}),
        );
        // Segments arrive unsorted; the expansion sorts them.
        let expanded = expand_retime_ramp(&model, &ramp).unwrap();
        assert_eq!(expanded.target_id, "use-a");
        assert_eq!(expanded.razor_frames, vec![12]);
        assert_eq!(
            expanded.pieces,
            vec![
                RetimeRampPiece {
                    piece_id: "use-a-a".into(),
                    start_frame: 0,
                    percent: 200.0,
                    reverse: false,
                },
                RetimeRampPiece {
                    piece_id: "use-a-b".into(),
                    start_frame: 12,
                    percent: 50.0,
                    reverse: false,
                },
            ]
        );
        assert_eq!(expanded.edits.len(), 5);
        // Splits run in timeline order.
        assert_eq!(expanded.edits[0].operation, "clip.split");
        assert_eq!(expanded.edits[0].target_ids, vec!["use-a".to_owned()]);
        assert_eq!(
            expanded.edits[0].after,
            Some(serde_json::json!({"at_frame": 12}))
        );
        // The severed second piece carries its two link repairs ahead of its
        // retime; retimes run last piece first so each ripple shifts only
        // followers that already carry their final length.
        assert_eq!(expanded.edits[1].operation, "clip.link");
        assert_eq!(expanded.edits[1].target_ids, vec!["use-a-b".to_owned()]);
        assert_eq!(expanded.edits[2].operation, "clip.link");
        assert_eq!(expanded.edits[2].target_ids, vec!["use-a-b".to_owned()]);
        assert_eq!(expanded.edits[3].operation, "retime");
        assert_eq!(expanded.edits[3].target_ids, vec!["use-a-b".to_owned()]);
        assert_eq!(
            expanded.edits[3].after,
            Some(serde_json::json!({"percent": 50.0}))
        );
        // The first piece keeps the intact link: retime only, no repairs.
        assert_eq!(expanded.edits[4].operation, "retime");
        assert_eq!(expanded.edits[4].target_ids, vec!["use-a-a".to_owned()]);
        assert_eq!(
            expanded.edits[4].after,
            Some(serde_json::json!({"percent": 200.0}))
        );
        assert_eq!(expanded.edits[0].id.0, "edit-ramp-split-1");
        assert_eq!(expanded.edits[1].id.0, "edit-ramp-link-1-1");
        assert_eq!(expanded.edits[2].id.0, "edit-ramp-link-1-2");
        assert_eq!(expanded.edits[3].id.0, "edit-ramp-retime-2");
        assert_eq!(expanded.edits[4].id.0, "edit-ramp-retime-1");
        // Deterministic: expanding twice mints identical edits.
        let again = expand_retime_ramp(&model, &ramp).unwrap();
        assert_eq!(
            serde_json::to_string(&expanded.edits).unwrap(),
            serde_json::to_string(&again.edits).unwrap()
        );
    }

    #[test]
    fn retime_ramp_mutation_matches_piecewise_constant_speeds() {
        let model = minimal_model();
        let ramp = ren_edit(
            "edit-ramp",
            "retime.segments",
            "use-a",
            serde_json::json!({"segments": [
                {"record_frame": 0, "percent": 200.0},
                {"record_frame": 12, "percent": 50.0},
            ]}),
        );
        let expanded = expand_retime_ramp(&model, &ramp).unwrap();
        let mut mutated = model.clone();
        for edit in &expanded.edits {
            mutated = apply_ren_edit_to_model(&mutated, edit).unwrap();
        }
        // 12 frames at 200% play in 6, 12 at 50% in 24: the pieces sit back
        // to back and the V2 follower shifts by the total delta (+6).
        let spans: Vec<(String, i64, i64)> = {
            let mut uses: Vec<_> = mutated.clip_uses.iter().collect();
            uses.sort_by_key(|u| u.timeline_interval.start);
            uses.into_iter()
                .map(|u| {
                    (
                        u.id.0.clone(),
                        u.timeline_interval.start,
                        u.timeline_interval.end,
                    )
                })
                .collect()
        };
        assert_eq!(
            spans,
            vec![
                ("use-a-a".to_owned(), 0, 6),
                ("use-a-b".to_owned(), 6, 30),
                ("use-b".to_owned(), 30, 54),
            ]
        );
        // The mutated model re-projects and the map carries both rates.
        let projection = plan_projection(&mutated).unwrap();
        let map = build_frame_map(&mutated, &projection, "clock-render", "map-ramp").unwrap();
        assert_eq!(map.segments.len(), 3);
        assert_eq!(map.segments[0].mapping_kind, MappingKind::Retimed);
        assert_eq!(
            map.segments[0].rate,
            Some(Rational {
                numerator: 2,
                denominator: 1
            })
        );
        assert_eq!(map.segments[1].mapping_kind, MappingKind::Retimed);
        assert_eq!(
            map.segments[1].rate,
            Some(Rational {
                numerator: 1,
                denominator: 2
            })
        );
        assert_eq!(map.segments[2].mapping_kind, MappingKind::OneToOne);
        assert_eq!(render_span(&map), 54);
    }

    #[test]
    fn retime_ramp_nests_piece_ids_for_three_steps() {
        let model = minimal_model();
        let ramp = ren_edit(
            "edit-ramp-3",
            "retime.segments",
            "use-a",
            serde_json::json!({"segments": [
                {"record_frame": 0, "percent": 100.0},
                {"record_frame": 8, "percent": 200.0},
                {"record_frame": 16, "percent": 50.0},
            ]}),
        );
        let expanded = expand_retime_ramp(&model, &ramp).unwrap();
        assert_eq!(expanded.razor_frames, vec![8, 16]);
        // The second split addresses the right half of the first, so piece
        // ids nest exactly as sequential split mutations mint them.
        assert_eq!(
            expanded
                .pieces
                .iter()
                .map(|p| p.piece_id.clone())
                .collect::<Vec<_>>(),
            vec!["use-a-a", "use-a-b-a", "use-a-b-b"]
        );
        // Splits, then per severed piece (last first) two link repairs plus
        // its retime, then the intact first piece's retime alone.
        assert_eq!(expanded.edits.len(), 9);
        let ops: Vec<(&str, &str)> = expanded
            .edits
            .iter()
            .map(|e| (e.operation.as_str(), e.target_ids[0].as_str()))
            .collect();
        assert_eq!(
            ops,
            vec![
                ("clip.split", "use-a"),
                ("clip.split", "use-a-b"),
                ("clip.link", "use-a-b-b"),
                ("clip.link", "use-a-b-b"),
                ("retime", "use-a-b-b"),
                ("clip.link", "use-a-b-a"),
                ("clip.link", "use-a-b-a"),
                ("retime", "use-a-b-a"),
                ("retime", "use-a-a"),
            ]
        );
        let mut mutated = model.clone();
        for edit in &expanded.edits {
            mutated = apply_ren_edit_to_model(&mutated, edit).unwrap();
        }
        // 8 at 100% stays 8, 8 at 200% plays in 4, 8 at 50% in 16.
        let spans: Vec<(String, i64, i64)> = {
            let mut uses: Vec<_> = mutated.clip_uses.iter().collect();
            uses.sort_by_key(|u| u.timeline_interval.start);
            uses.into_iter()
                .map(|u| {
                    (
                        u.id.0.clone(),
                        u.timeline_interval.start,
                        u.timeline_interval.end,
                    )
                })
                .collect()
        };
        assert_eq!(
            spans,
            vec![
                ("use-a-a".to_owned(), 0, 8),
                ("use-a-b-a".to_owned(), 8, 12),
                ("use-a-b-b".to_owned(), 12, 28),
                ("use-b".to_owned(), 28, 52),
            ]
        );
    }

    #[test]
    fn retime_ramp_single_step_is_one_constant_retime() {
        let model = minimal_model();
        let ramp = ren_edit(
            "edit-ramp-1",
            "retime.segments",
            "use-a",
            serde_json::json!({"segments": [{"record_frame": 0, "percent": 50.0}]}),
        );
        let expanded = expand_retime_ramp(&model, &ramp).unwrap();
        assert!(expanded.razor_frames.is_empty());
        assert_eq!(expanded.edits.len(), 1);
        assert_eq!(expanded.edits[0].operation, "retime");
        assert_eq!(expanded.edits[0].target_ids, vec!["use-a".to_owned()]);
    }

    #[test]
    fn retime_ramp_level_reverse_keeps_refusing_with_a_named_code() {
        let model = minimal_model();
        // A ramp-level reverse would play the whole ramp backwards -
        // reversed piece order, not just reversed pieces - which the
        // expansion does not express. It keeps refusing by name.
        let ramp = ren_edit(
            "edit-ramp-rev",
            "retime.segments",
            "use-a",
            serde_json::json!({"reverse": true, "segments": [
                {"record_frame": 0, "percent": 200.0},
                {"record_frame": 12, "percent": 200.0},
            ]}),
        );
        let err = expand_retime_ramp(&model, &ramp).unwrap_err();
        assert!(
            matches!(&err, AdapterError::Unsupported { code, .. } if code == "reverse_not_supported"),
            "ramp reverse must refuse by name, got {err:?}"
        );
        // A reversed piece inside the ramp expands instead: direction rides
        // the piece retime's flag while span math stays magnitude-only.
        let ramp = ren_edit(
            "edit-ramp-seg-rev",
            "retime.segments",
            "use-a",
            serde_json::json!({"segments": [
                {"record_frame": 0, "percent": 200.0},
                {"record_frame": 12, "percent": 50.0, "reverse": true},
            ]}),
        );
        let expanded = expand_retime_ramp(&model, &ramp).unwrap();
        assert!(expanded.pieces[0].reverse == false);
        assert!(expanded.pieces[1].reverse == true);
        let retime = expanded
            .edits
            .iter()
            .find(|e| e.operation == "retime" && e.target_ids == vec!["use-a-b".to_owned()])
            .unwrap();
        assert_eq!(
            retime.after,
            Some(serde_json::json!({"percent": 50.0, "reverse": true}))
        );
        // A non-boolean reverse flag is caller error, not a direction.
        let ramp = ren_edit(
            "edit-ramp-seg-rev-bad",
            "retime.segments",
            "use-a",
            serde_json::json!({"segments": [
                {"record_frame": 0, "percent": 200.0},
                {"record_frame": 12, "percent": 50.0, "reverse": "yes"},
            ]}),
        );
        assert!(matches!(
            expand_retime_ramp(&model, &ramp).unwrap_err(),
            AdapterError::InvalidModel(_)
        ));
    }

    #[test]
    fn retime_ramp_malformed_shapes_refuse_without_guessing() {
        let model = minimal_model();
        // Not a ramp at all.
        let edit = ren_edit(
            "edit-not-ramp",
            "retime",
            "use-a",
            serde_json::json!({"percent": 200.0}),
        );
        assert!(expand_retime_ramp(&model, &edit).is_err());
        for (id, after) in [
            // No segments.
            ("edit-ramp-empty", serde_json::json!({"segments": []})),
            // The ramp must cover its clip from the first frame.
            (
                "edit-ramp-late-start",
                serde_json::json!({"segments": [
                    {"record_frame": 4, "percent": 200.0},
                    {"record_frame": 12, "percent": 200.0},
                ]}),
            ),
            // A repeated razor point is not two pieces.
            (
                "edit-ramp-dup",
                serde_json::json!({"segments": [
                    {"record_frame": 0, "percent": 200.0},
                    {"record_frame": 12, "percent": 200.0},
                    {"record_frame": 12, "percent": 50.0},
                ]}),
            ),
            // A razor point at the clip end splits nothing.
            (
                "edit-ramp-past-end",
                serde_json::json!({"segments": [
                    {"record_frame": 0, "percent": 200.0},
                    {"record_frame": 24, "percent": 50.0},
                ]}),
            ),
        ] {
            let ramp = ren_edit(id, "retime.segments", "use-a", after);
            let err = expand_retime_ramp(&model, &ramp).unwrap_err();
            assert!(
                matches!(err, AdapterError::InvalidModel(_)),
                "{id} must refuse as invalid, got {err:?}"
            );
        }
        // An inexact rate refuses exactly as a constant retime does.
        let ramp = ren_edit(
            "edit-ramp-rate",
            "retime.segments",
            "use-a",
            serde_json::json!({"segments": [
                {"record_frame": 0, "percent": 33.333333333333336},
                {"record_frame": 12, "percent": 100.0},
            ]}),
        );
        let err = expand_retime_ramp(&model, &ramp).unwrap_err();
        assert!(
            matches!(&err, AdapterError::Unsupported { code, .. } if code == "retime_rate_inexact"),
            "inexact ramp rate must refuse by name, got {err:?}"
        );
        // A fractional piece length refuses rather than rounding: 10 frames
        // at 150% play in 20/3 frames.
        let ramp = ren_edit(
            "edit-ramp-fraction",
            "retime.segments",
            "use-a",
            serde_json::json!({"segments": [
                {"record_frame": 0, "percent": 150.0},
                {"record_frame": 10, "percent": 100.0},
            ]}),
        );
        let err = expand_retime_ramp(&model, &ramp).unwrap_err();
        assert!(
            matches!(&err, AdapterError::Unsupported { code, .. } if code == "retime_inexact_duration"),
            "fractional ramp piece must refuse by name, got {err:?}"
        );
    }

    #[test]
    fn retime_ramp_refusals_name_the_expansion_helper() {
        // The unexpanded ramp still refuses planning and mutation by name,
        // and the refusal now says who owns the expansion.
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64])]);
        let context = ren_edit_context(&clip_ids);
        let ramp = ren_edit(
            "edit-ramp-raw",
            "retime.segments",
            "use-a",
            serde_json::json!({"segments": [
                {"record_frame": 0, "percent": 200.0},
                {"record_frame": 12, "percent": 50.0},
            ]}),
        );
        let err = plan_ren_edit(&model, &projection, &ramp, &context).unwrap_err();
        assert!(
            matches!(&err, AdapterError::Unsupported { code, .. } if code == "retime_segments_not_supported")
                && format!("{err}").contains("expand_retime_ramp"),
            "plan refusal must name the helper, got {err:?}"
        );
        let err = apply_ren_edit_to_model(&model, &ramp).unwrap_err();
        assert!(
            matches!(&err, AdapterError::Unsupported { code, .. } if code == "retime_segments_not_supported")
                && format!("{err}").contains("expand_retime_ramp"),
            "mutation refusal must name the helper, got {err:?}"
        );
    }
}
