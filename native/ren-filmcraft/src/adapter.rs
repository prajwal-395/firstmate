//! Unit 6: deterministic projection between Ren's canonical Project Model and
//! the pinned FilmCraft driver in `super`.
//!
//! Ren's model stays canonical. This adapter serializes only the bounded subset
//! FilmCraft can represent (asset imports plus ordered clip placements on
//! video tracks, base picture on V1/V2 with compositing overlays above,
//! music/SFX stems dealt across the audio pool from A2 upward, per-clip gain
//! via `clip.audioGain`, track gain via `mixer.setValue`, caption/title
//! tracks from speech observations, LUT application, keyframed
//! transform/crop/opacity on clip uses, segmented person masks on their
//! source-linked placements, and track volume automation)
//! into driver [`super::Operation`]s, keeps the bidirectional Ren/engine ID map,
//! builds the exact timeline-to-render [`FrameMap`](ren_protocol::project_model::FrameMap),
//! validates the exported render asset, and carries every loss explicitly.
//!
//! EffectCraft ownership boundary: content EffectCraft already projects
//! track mattes and camera moves as `move.*`, `blur.gaussian` and
//! `color.saturation` in its verified map) stays there. This adapter never
//! duplicates it: such effects report `effect_owned_by_effectcraft` and
//! object tracks keep their existing loss codes. Source-segmented SAM2/M3d
//! masks are a distinct native opacity-mask input, not an EffectCraft matte.
//!
//! Fail-closed rules:
//!
//! - The projection is deterministic: clip uses sort by
//!   `(timeline_start, clip_use_id)`; imports sort by asset id; ID maps are
//!   `BTreeMap`s. Planning twice over the same model yields identical bytes.
//! - Anything the engine cannot represent is a projection [`super::LossReport`]
//!   (model effects, unsupported masks, object tracks, overlay positioning) or an
//!   [`AdapterError::Unsupported`] refusal (overlapping timeline ranges on one
//!   track, overlapping base picture, unknown roles, unsupported short sources,
//!   undecodable overlay codecs, inexact tick conversion, unknown edit
//!   operations). Nothing is guessed.
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
//!   no repair; transition maps onto its driver command, track gain onto
//!   `mixer.setValue`, and per-clip `audio.gain` onto `clip.audioGain`
//!   (mode `set`, first reconciled id - the engine follows clip links).
//!   Bed ducking (`audio.ducking` effects) expands into track volume
//!   automation on the bed's strip: the effect declares the ducked span,
//!   the full level, the dip depth and the ramp, and the plan drives the
//!   resulting points through `mixer.writeAutomation`. Keyframed ren edits
//!   (`audio.fade`, `audio.automation`) and pan still refuse by name:
//!   edits carry no declared depth or ramp of their own, and Ren's mix
//!   has no measured pan mapping on either path.
//!   A stepped `retime.segments` ramp expands through
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
//!   whose source holds fewer frames than its timeline range uses the named
//!   `freeze_last_frame` policy, implemented as a generated still over the
//!   missing tail, unless a logged `retime`/`span_retime` edit explains the
//!   exact ratio. A longer source is a trim and plays its leading
//!   timeline-length portion. The timeline-to-render frame map marks
//!   retimed placements [`MappingKind::Retimed`] with the exact playback
//!   speed as the rational rate (2/1 at 200%); spans stay timeline-to-render
//!   packing, so the rate documents playback rather than the span affine.
//!   A reversed retime negates the numerator (-1/1): the sign is the
//!   direction, and ren-core's tick math refuses negative rates loudly
//!   rather than mapping them forwards.
//! - Discrete-mono sources are dual-mono mapped, never left-only: a source
//!   whose model audio streams all declare one channel imports stereo-paired
//!   by the engine, so [`plan_dual_mono_mapping`] regroups its first channel
//!   onto both outputs before placement; true-stereo and unknown-channel
//!   sources keep engine behavior untouched, and anything unexpected about
//!   the queried mapping refuses rather than guessing a channel index.
//! - Resolve remains the finishing destination: `.fcproj` files are disposable
//!   engine working artifacts, never Ren's database.

use ren_protocol::project_model::{
    validate_project_model, Clock, EditOperation, FrameMap, FrameMapId, FrameMapSegment, Interval,
    MappingKind, ProjectModel, Rational, TimeDomain,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use super::{
    AudioChannelFormat, BitrateMode, ExportFormat, GpuRendering, HardwareEncoding, LossReport,
    Operation, TrimEdge, TrimMode, FILMCRAFT_REVISION,
};

/// FilmCraft's fixed project clock, ticks per second (see pinned CLI tests).
pub const ENGINE_TICKS_PER_SECOND: i64 = 254_016_000_000;
/// Version of this projection contract. Bump on any planning-rule change.
pub const ADAPTER_VERSION: u32 = 7;

/// Audio strips on a fresh FilmCraft sequence (`file.newSequence` defaults
/// to three audio tracks). A1 carries production dialogue (linked audio,
/// played at unity), A2 opens the music pool, A3 opens the SFX pool - the
/// same division `library/tools/otio_mix.py::track_role` owns on the
/// Resolve path, so a stem renamed on one path cannot mean something else
/// on the other.
pub const FRESH_SEQUENCE_AUDIO_TRACKS: u32 = 3;
/// First strip of the music pool (the bed allocates from here upward).
pub const MUSIC_POOL_BASE: u32 = 2;
/// First strip of the SFX pool when the bed uses nothing. With a bed, the
/// SFX pool starts above whatever the bed used, mirroring the OTIO
/// allocator's bed-then-SFX order.
pub const SFX_POOL_BASE: u32 = 3;

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

/// One ordered clip placement derived from a model `ClipUse`. `role` rides
/// along so the execution boundary (`verify_media`'s overlay codec gate)
/// and every reader can see what a placement is without rejoining the model.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Placement {
    pub clip_use_id: String,
    pub asset_id: String,
    pub role: String,
    pub track: String,
    pub audio_track: Option<String>,
    pub frame: i64,
    pub source_in_ticks: i64,
    pub duration_ticks: i64,
    pub timeline_start: i64,
    pub timeline_end: i64,
}

/// Declared handling for a source interval shorter than its timeline span.
/// Native execution materializes a still from the final available source
/// frame and places it over the missing timeline frames.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SpanShortfallPolicy {
    Refuse,
    #[default]
    FreezeLastFrame,
}

/// One measured source/timeline length mismatch carried by a projection.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpanShortfall {
    pub clip_use_id: String,
    pub source_frames: i64,
    pub timeline_frames: i64,
    pub missing_frames: i64,
}

/// One still generated at execution time to fill a declared picture shortfall.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SpanFreeze {
    pub clip_use_id: String,
    pub asset_id: String,
    pub uri: String,
    pub source_frame: i64,
    pub track: String,
    pub hold_start_frame: i64,
    pub held_frames: i64,
}

/// Deterministic plan over a Project Model. Serialize with [`projection_json`]
/// for byte-stable comparison.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Projection {
    pub adapter_version: u32,
    pub engine_revision: String,
    pub timeline_clock: String,
    /// Recorded choice used when source footage is shorter than its play.
    pub span_shortfall_policy: SpanShortfallPolicy,
    /// Every accepted mismatch, with frame counts, for an auditable render.
    pub span_shortfalls: Vec<SpanShortfall>,
    /// Execution-created still placements that fill each accepted mismatch.
    #[serde(default)]
    pub generated_span_freezes: Vec<SpanFreeze>,
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
///
/// Base picture (`a_roll`, `b_roll`, `end_card`) owns the timeline spine on
/// V1/V2; compositing overlays ride fixed upper tracks so a reel with
/// B-roll and captions never collides: `subtitle_overlay` on V3,
/// `motion_graphic` on V4, `frame_overlay` on V5 and `post_header` on V6.
/// An `end_card` is full-frame
/// tail/head content beside the footage, so it shares V1 and the same-track
/// rule forces it sequential with the base. V4-V6 exceed a fresh project's
/// three video tracks: plan [`add_tracks_operation`] before placing when
/// [`required_video_tracks`] reports more than three. Audio stems (`music`,
/// `sfx`) never reach this function: they allocate across the audio pool
/// in [`allocate_stem_strips`], because two overlapping bed pieces cannot
/// share one strip.
fn track_for_role(role: &str, clip_use_id: &str) -> Result<String> {
    match role {
        "a_roll" => Ok("V1".into()),
        "b_roll" => Ok("V2".into()),
        "subtitle_overlay" => Ok("V3".into()),
        "motion_graphic" => Ok("V4".into()),
        "frame_overlay" => Ok("V5".into()),
        "post_header" => Ok("V6".into()),
        "end_card" => Ok("V1".into()),
        _ => Err(unsupported(
            "unsupported_role",
            format!("clip use `{clip_use_id}` has role `{role}`; projection maps `a_roll`, `b_roll`, `subtitle_overlay`, `motion_graphic`, `frame_overlay`, `post_header` and `end_card` to picture and `music`/`sfx` to audio stems"),
        )),
    }
}

/// Roles that composite over the picture rather than extending it: they may
/// overlap the base spine (and each other across tracks) because the engine
/// stacks video tracks. Everything else is base picture, which stays
/// strictly sequential exactly as before this vocabulary existed.
fn is_compositing_overlay(role: &str) -> bool {
    matches!(
        role,
        "subtitle_overlay" | "motion_graphic" | "frame_overlay" | "post_header"
    )
}

/// Roles that drive the render span. Compositing overlays add no render
/// time - they draw onto the base frames beneath them - so [`build_frame_map`]
/// packs these only. A model with none of them is overlays over nothing and
/// refuses as `no_base_spine` rather than mapping an empty render.
fn is_spine_role(role: &str) -> bool {
    matches!(role, "a_roll" | "b_roll" | "end_card")
}

/// True for the roles that place sound onto audio strips rather than
/// picture onto video tracks.
fn is_stem_role(role: &str) -> bool {
    matches!(role, "music" | "sfx")
}

/// Spread stem uses across audio strips, bed then SFX.
///
/// Each use is `(clip_use_id, timeline_start, timeline_end)` in model
/// ticks. Music allocates first from [`MUSIC_POOL_BASE`] upward (a
/// declared crossfade is a real overlap, so the two pieces take two
/// strips); SFX allocates next from above whatever the bed used
/// (`max(SFX_POOL_BASE, bed_max + 1)`), which is the order the OTIO
/// allocator (`library/tools/otio_mix.py`, `music_bed.py`) renders in.
/// Within each pool, a use takes the lowest strip whose last occupant
/// ends at or before its start; anything else takes a fresh strip. Input
/// order decides nothing: uses sort by `(start, clip_use_id)`, so the
/// same model always deals the same strips.
fn allocate_stem_strips(uses: &[(String, i64, i64)], stem_base: u32) -> BTreeMap<String, String> {
    let mut ordered: Vec<_> = uses.iter().collect();
    ordered.sort_by(|a, b| (a.1, &a.0).cmp(&(b.1, &b.0)));
    let mut strip_end: BTreeMap<u32, i64> = BTreeMap::new();
    let mut assigned = BTreeMap::new();
    for (id, start, end) in ordered {
        let mut strip = stem_base;
        while strip_end
            .get(&strip)
            .is_some_and(|occupied_until| *occupied_until > *start)
        {
            strip += 1;
        }
        strip_end.insert(strip, *end);
        assigned.insert((*id).clone(), format!("A{strip}"));
    }
    assigned
}

/// Deal every stem use onto its strip: `clip_use_id` to `A{n}`.
///
/// Music deals first from [`MUSIC_POOL_BASE`]; SFX deals next from above
/// whatever the bed used. The caller passes ALL clip uses; picture roles
/// are ignored here (the overlap check above already owns them).
fn deal_stem_strips(ordered: &[&ren_protocol::project_model::ClipUse]) -> BTreeMap<String, String> {
    let spans = |role: &str| {
        ordered
            .iter()
            .filter(|u| u.role == role)
            .map(|u| {
                (
                    u.id.0.clone(),
                    u.timeline_interval.start,
                    u.timeline_interval.end,
                )
            })
            .collect::<Vec<_>>()
    };
    let mut dealt = allocate_stem_strips(&spans("music"), MUSIC_POOL_BASE);
    let bed_max = dealt
        .values()
        .filter_map(|strip| strip.strip_prefix('A').and_then(|n| n.parse::<u32>().ok()))
        .max()
        .unwrap_or(0);
    dealt.extend(allocate_stem_strips(
        &spans("sfx"),
        SFX_POOL_BASE.max(bed_max + 1),
    ));
    dealt
}

/// Highest audio strip number the projection addresses, over placed
/// tracks and linked-audio strips alike. Fresh sequences carry
/// [`FRESH_SEQUENCE_AUDIO_TRACKS`]; anything above needs
/// [`Operation::AddAudioTracks`] before the first placement lands.
pub fn audio_strips_used(projection: &Projection) -> u32 {
    projection
        .placements
        .iter()
        .flat_map(|placement| {
            [&placement.track]
                .into_iter()
                .chain(placement.audio_track.as_ref())
        })
        .filter_map(|strip| strip.strip_prefix('A'))
        .filter_map(|number| number.parse::<u32>().ok())
        .max()
        .unwrap_or(0)
}

/// The track-growth operation a stem plan needs, if any: `None` when the
/// plan fits the fresh sequence's own A1-A3. Callers run this after
/// `CreateProject` and before the first `Place`.
pub fn add_audio_tracks_operation(
    project: &Path,
    projection: &Projection,
) -> Result<Option<Operation>> {
    let highest = audio_strips_used(projection);
    if highest <= FRESH_SEQUENCE_AUDIO_TRACKS {
        return Ok(None);
    }
    Ok(Some(Operation::AddAudioTracks {
        project: project.into(),
        audio_tracks: highest - FRESH_SEQUENCE_AUDIO_TRACKS,
    }))
}

/// Pure, deterministic plan over `model`. No I/O, no CLI: the same model
/// document always yields the same projection bytes.
pub fn plan_projection(model: &ProjectModel) -> Result<Projection> {
    plan_projection_with_shortfall_policy(model, SpanShortfallPolicy::FreezeLastFrame)
}

/// Plan a projection with an explicit, serializable span-shortfall choice.
/// The chosen policy is recorded in `Projection` so downstream execution and
/// audit artifacts cannot silently disagree about how a short source plays.
pub fn plan_projection_with_shortfall_policy(
    model: &ProjectModel,
    shortfall_policy: SpanShortfallPolicy,
) -> Result<Projection> {
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
    // Unknown roles refuse before any geometric check can mislabel
    // them: a `title` overlapping dialogue is an unmapped role, not a
    // picture overlap.
    for clip_use in &ordered {
        if !matches!(
            clip_use.role.as_str(),
            "a_roll"
                | "b_roll"
                | "end_card"
                | "subtitle_overlay"
                | "motion_graphic"
                | "frame_overlay"
                | "post_header"
                | "music"
                | "sfx"
        ) {
            return Err(unsupported(
                "unsupported_role",
                format!(
                    "clip use `{}` has role `{}`; projection maps `a_roll`, `b_roll`, `subtitle_overlay`, `motion_graphic`, `frame_overlay`, `post_header` and `end_card` to picture and `music`/`sfx` to audio stems",
                    clip_use.id.0, clip_use.role
                ),
            ));
        }
    }
    // Overlap discipline, per track pair: the same track never stacks (the
    // engine would trim silently), and base picture never overlaps base
    // picture even across tracks (B-roll stays a sequential cutaway, so the
    // frame map's sequential packing is unambiguous). An overlap involving a
    // compositing overlay is the normal case - captions over dialogue, a
    // bezel under everything - and is allowed across tracks. Stems sit out
    // this check entirely: the bed's declared crossfades ARE overlaps, and
    // the strip allocator below spreads them across tracks instead of
    // refusing. All pairs are checked, not just adjacent windows: a
    // same-track pair with an overlay between them in sort order still
    // collides.
    {
        let mut tracks: Vec<String> = Vec::with_capacity(ordered.len());
        for use_ in &ordered {
            tracks.push(if is_stem_role(&use_.role) {
                String::new()
            } else {
                track_for_role(&use_.role, &use_.id.0)?
            });
        }
        for (index, first) in ordered.iter().enumerate() {
            for (other_index, second) in ordered.iter().enumerate().skip(index + 1) {
                if is_stem_role(&first.role) || is_stem_role(&second.role) {
                    continue;
                }
                let (a, b) = (&first.timeline_interval, &second.timeline_interval);
                if a.start < b.end && b.start < a.end {
                    let shared =
                        is_compositing_overlay(&first.role) || is_compositing_overlay(&second.role);
                    if tracks[index] == tracks[other_index] || !shared {
                        return Err(unsupported(
                            "timeline_overlap",
                            format!(
                                "clip use `{}` ({} on {}) overlaps clip use `{}` ({} on {}) over [{}, {}); same-track stacking and base-picture overlap have no `timeline.place` representation",
                                first.id.0,
                                first.role,
                                tracks[index],
                                second.id.0,
                                second.role,
                                tracks[other_index],
                                a.start.max(b.start),
                                a.end.min(b.end),
                            ),
                        ));
                    }
                }
            }
        }
    }
    // Overlays over nothing refuse: compositing overlays add no render time
    // on their own. Stems alone need no spine.
    if ordered
        .iter()
        .any(|use_| is_compositing_overlay(&use_.role))
        && !ordered.iter().any(|use_| is_spine_role(&use_.role))
    {
        return Err(unsupported(
            "no_base_spine",
            "the model names compositing overlays but no `a_roll`, `b_roll` or `end_card` to draw them over; overlays add no render time on their own".into(),
        ));
    }
    // Deal the audio strips before placing anything: music first from the
    // pool base, then SFX above whatever the bed used.
    let stem_strips = deal_stem_strips(&ordered);

    let mut imports = BTreeMap::new();
    let mut placements = Vec::new();
    let mut span_shortfalls = Vec::new();
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
        if source_frames < timeline_frames
            && !retime_explains(model, &clip_use.id.0, source_frames, timeline_frames)
        {
            if shortfall_policy == SpanShortfallPolicy::Refuse {
                return Err(unsupported(
                    "source_too_short",
                    format!(
                        "clip use `{}` holds {source_frames} source frames for {timeline_frames} timeline frames; choose and record a span-shortfall policy",
                        clip_use.id.0
                    ),
                ));
            }
            if is_stem_role(&clip_use.role) {
                return Err(unsupported(
                    "audio_span_shortfall_policy_unsupported",
                    format!(
                        "clip use `{}` is an audio stem; `freeze_last_frame` only applies to picture",
                        clip_use.id.0
                    ),
                ));
            }
            span_shortfalls.push(SpanShortfall {
                clip_use_id: clip_use.id.0.clone(),
                source_frames,
                timeline_frames,
                missing_frames: timeline_frames - source_frames,
            });
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
        let (track, audio_track) = if is_stem_role(&clip_use.role) {
            // A stem with no audio stream names a silence no file can
            // play; the bed and SFX pools place sound, never picture.
            if !has_audio_stream(model, &asset.id.0) {
                return Err(unsupported(
                    "no_audio_for_stem",
                    format!(
                        "clip use `{}` has stem role `{}` but asset `{}` carries no audio stream; there is no sound to place",
                        clip_use.id.0, clip_use.role, asset.id.0
                    ),
                ));
            }
            let strip = stem_strips.get(&clip_use.id.0).ok_or_else(|| {
                invalid(format!(
                    "stem use `{}` dealt no strip; the allocator covers every stem role",
                    clip_use.id.0
                ))
            })?;
            // The strip IS the track for a sound-only placement, and it
            // stays on the placement as `audio_track` so volume automation
            // (the `audio.volume` plan) finds the strip to drive. The
            // driver sends the track once: the engine refuses `track` plus
            // `audioTrack` naming the same strip.
            (strip.clone(), Some(strip.clone()))
        } else {
            (
                track_for_role(&clip_use.role, &clip_use.id.0)?,
                // Only base picture carries audio: an overlay or card file's
                // incidental audio stream (the renderer writes silence onto
                // every overlay and card .mov) is never wired to the mix.
                (matches!(clip_use.role.as_str(), "a_roll" | "b_roll")
                    && has_audio_stream(model, &asset.id.0))
                .then(|| "A1".into()),
            )
        };
        placements.push(Placement {
            clip_use_id: clip_use.id.0.clone(),
            asset_id: asset.id.0.clone(),
            role: clip_use.role.clone(),
            track,
            audio_track,
            frame,
            source_in_ticks,
            duration_ticks,
            timeline_start: clip_use.timeline_interval.start,
            timeline_end: clip_use.timeline_interval.end,
        });
    }

    let mut losses = Vec::new();
    losses.extend(projectable_effect_losses(model, &placements, timeline)?);
    let mask_losses = model
        .masks
        .iter()
        .filter(|mask| !mask_has_native_target(model, mask, &placements))
        .count();
    if mask_losses > 0 {
        losses.push(LossReport {
            code: "mask_not_projected".into(),
            detail: format!(
                "{} mask(s) have no entity-linked SAM2/M3d seed covering a placed source start and were not sent",
                mask_losses
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
    let positioned: BTreeSet<&str> = model
        .effects
        .iter()
        .filter(|effect| effect.class_id == TRANSFORM_POSITION_CLASS)
        .map(|effect| effect.target_id.as_str())
        .collect();
    let unpositioned: Vec<&str> = placements
        .iter()
        .filter(|p| is_compositing_overlay(&p.role) && !positioned.contains(p.clip_use_id.as_str()))
        .map(|p| p.clip_use_id.as_str())
        .collect();
    // The projection carries no motion parameters (`timeline.place` takes
    // none), so every compositing overlay draws at the engine's default
    // position. Full-frame cards are exempt by role contract; anything
    // smaller than the sequence needs a follow-up positioning pass
    // (`effects.setParam` on the motion effect) before it sits on its row.
    if !unpositioned.is_empty() {
        losses.push(LossReport {
            code: "overlay_unpositioned".into(),
            detail: format!(
                "{} overlay placement(s) carry no motion parameters and draw at the engine default position: {}",
                unpositioned.len(),
                unpositioned.join(", "),
            ),
        });
    }

    Ok(Projection {
        adapter_version: ADAPTER_VERSION,
        engine_revision: FILMCRAFT_REVISION.into(),
        timeline_clock,
        span_shortfall_policy: shortfall_policy,
        span_shortfalls,
        generated_span_freezes: Vec::new(),
        imports: imports.into_values().collect(),
        placements,
        losses,
    })
}

fn mask_has_native_target(
    model: &ProjectModel,
    mask: &ren_protocol::project_model::Mask,
    placements: &[Placement],
) -> bool {
    let Some(kind) = mask.representation.get("kind").and_then(|v| v.as_str()) else {
        return false;
    };
    if !matches!(kind, "sam2_rle_track" | "region_rle") {
        return false;
    }
    let Some(clock) = model.clocks.iter().find(|c| c.id == mask.interval.clock_id) else {
        return false;
    };
    let TimeDomain::Source { asset_id, .. } = &clock.domain else {
        return false;
    };
    model.clip_uses.iter().any(|use_| {
        if !matches!(use_.role.as_str(), "a_roll" | "b_roll")
            || !use_.entity_links.contains(&mask.entity_id)
            || &use_.asset_id != asset_id
            || use_.source_interval.clock_id != mask.interval.clock_id
            || use_.source_interval.start < mask.interval.start
            || use_.source_interval.start >= mask.interval.end
            || !placements.iter().any(|p| p.clip_use_id == use_.id.0)
        {
            return false;
        }
        true
    })
}

// ---------------------------------------------------------------------------
// Model effects, captions and titles FilmCraft executes natively.
//
// Vocabulary (fail-closed; anything else stays an explicit loss):
//
// - Observations with modality `speech` or `caption` carrying
//   `payload.text` plan into a caption track (`captions.*`).
// - Observations with modality `title` carrying `payload.text` plan into
//   overlay titles (`graphics.newText`) bound to the overlapping clip use.
// - Effects with class `color.lut` and exactly `{"lut": str}` plan into
//   `lut.import` (file paths) plus `lumetri.setInputLut` (`builtin:`/`lib:`
//   refs apply in one phase).
// - Effects with class `transform.position` (`position: [x, y]`),
//   `transform.scale` (`scale: number`, a multiplier where 1.0 is
//   unchanged), `transform.rotation` (`rotation: number`) and
//   `opacity.opacity` (`opacity: number`) plan into static
//   `effects.setParam` writes; their keyframed forms use animation keys.
// - Effects with class `transform.crop` and static `left`/`top`/`right`/
//   `bottom` parameters plan into `effects.apply` plus one
//   `effects.setParam` per edge; animated crops refuse.
// - Effects with class `audio.volume` and numeric dB keyframes plan into
//   `mixer.writeAutomation` on the placement's audio strip (`volume` lane).
// - Effects with class `audio.ducking` plan the bed's dip under dialogue
//   into the same lane: the effect targets a `music` stem use and declares
//   `full_db` (bed level outside the duck), `depth_db` (how far below full
//   the ducked level sits) and `ramp_ticks` (ramp per edge, on the
//   effect interval's clock); its interval is the ducked span. The ramp,
//   depth and full level all arrive declared - this module defines none
//   of its own (AGENTS.md 10.5: no creative floors).
//
// EffectCraft ownership boundary (never duplicated here): `move.*`
// (camera moves), `blur.gaussian` and `color.saturation` (its verified
// map), masks and track mattes. Those report `effect_owned_by_effectcraft`
// (effects) or keep the mask/track loss codes.
//
// Out-of-span guard: a keyframe whose engine-tick time falls outside its
// target clip's timeline span refuses with `keyframe_out_of_span` at
// planning time. The engine answers such writes with success while
// placing nothing (measured: `effects.addKeyframe` at a timeline time
// before the clip's start returns null with zero keys read back), so
// planning must refuse rather than execute. Track volume automation is
// timeline-global and guards against the effect interval instead
// (`volume_key_outside_interval`).
// ---------------------------------------------------------------------------

/// Observation modalities that plan into the caption track.
pub const CAPTION_MODALITIES: [&str; 2] = ["speech", "caption"];
/// Observation modality that plans into overlay titles.
pub const TITLE_MODALITY: &str = "title";

/// Model effect class for LUT application (`lumetri.setInputLut`).
pub const LUT_CLASS_ID: &str = "color.lut";
/// Model effect classes for keyframed picture parameters.
pub const TRANSFORM_POSITION_CLASS: &str = "transform.position";
pub const TRANSFORM_SCALE_CLASS: &str = "transform.scale";
pub const TRANSFORM_ROTATION_CLASS: &str = "transform.rotation";
pub const TRANSFORM_CROP_CLASS: &str = "transform.crop";
pub const OPACITY_CLASS_ID: &str = "opacity.opacity";
/// Model effect class for track volume automation (`mixer.writeAutomation`).
pub const VOLUME_CLASS_ID: &str = "audio.volume";
/// Model effect class for bed ducking under dialogue. Same execution lane
/// as [`VOLUME_CLASS_ID`] (`mixer.writeAutomation` on the bed's strip);
/// the ducked curve is DERIVED from the effect's declared `full_db`,
/// `depth_db` and `ramp_ticks` plus its interval, never from a constant.
pub const DUCKING_CLASS_ID: &str = "audio.ducking";

/// Returns true for effect classes EffectCraft already projects: camera
/// moves (`move.*`) plus its verified `blur.gaussian` / `color.saturation`
/// map entries. The FilmCraft adapter reports those as owned elsewhere
/// and never executes them.
pub fn effectcraft_owned(class_id: &str) -> bool {
    class_id == "blur.gaussian" || class_id == "color.saturation" || class_id.starts_with("move.")
}

/// Returns true for effect classes this adapter executes natively.
pub fn filmcraft_executed(class_id: &str) -> bool {
    matches!(
        class_id,
        "color.lut"
            | "transform.position"
            | "transform.scale"
            | "transform.rotation"
            | "transform.crop"
            | "opacity.opacity"
            | "audio.volume"
            | "audio.ducking"
    )
}

/// One caption cue derived from a speech/caption observation. Times are
/// engine ticks on the sequence clock; `source_id` is the observation the
/// text was read from, so readback mismatches anchor to their source.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CaptionCue {
    pub text: String,
    pub speaker: Option<String>,
    pub start_ticks: i64,
    pub duration_ticks: i64,
    pub source_id: String,
}

/// One overlay title derived from a `title` observation, bound to the
/// clip use it overlaps. `time_ticks` is the title's engine-tick
/// placement; `duration_seconds` is whole seconds (the engine places
/// titles on whole-frame spans, and fractional seconds would round).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TitlePlan {
    pub text: String,
    pub clip_use_id: String,
    pub time_ticks: i64,
    pub duration_seconds: u64,
    pub source_id: String,
}

/// One LUT application. `lut` is either a `builtin:`/`lib:` engine
/// reference (one-phase apply) or a filesystem path (two-phase: import
/// first, then apply the receipt's `ref`).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LutPlan {
    pub effect_id: String,
    pub clip_use_id: String,
    pub lut: String,
}

impl LutPlan {
    /// True when `lut` is an engine reference that applies without import.
    pub fn is_reference(&self) -> bool {
        self.lut.starts_with("builtin:") || self.lut.starts_with("lib:")
    }
}

/// One keyframe in engine ticks with an engine-ready JSON value.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EffectKey {
    pub time_ticks: i64,
    pub value: serde_json::Value,
}

/// One keyframed picture plan: the engine effect/param to drive, whether
/// the non-built-in effect needs `effects.apply` first, the static
/// parameter writes (crop edges), and the timed keys.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EffectKeyPlan {
    pub effect_id: String,
    pub clip_use_id: String,
    pub engine_effect: String,
    pub engine_param: Option<String>,
    pub needs_apply: Option<String>,
    pub static_params: Vec<(String, serde_json::Value)>,
    pub keys: Vec<EffectKey>,
}

/// One track volume-automation plan: engine-tick (dB) points for the
/// placement's audio strip on the `volume` lane.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VolumePlan {
    pub effect_id: String,
    pub clip_use_id: String,
    pub strip: String,
    pub points: Vec<(i64, f64)>,
}

/// Model ticks on `clock_id` into FilmCraft engine ticks, refusing
/// inexact conversion rather than rounding.
fn model_to_engine_ticks(
    model: &ProjectModel,
    clock_id: &str,
    ticks: i64,
    what: &str,
) -> Result<i64> {
    let clock = model
        .clocks
        .iter()
        .find(|c| c.id.0 == clock_id)
        .ok_or_else(|| {
            unsupported(
                "unknown_clock",
                format!("{what}: clock `{clock_id}` is not in the model"),
            )
        })?;
    scale_exact(
        ticks,
        i128::from(ENGINE_TICKS_PER_SECOND),
        i128::from(clock.ticks_per_second),
        what,
    )
}

/// The target clip use's timeline span in engine ticks.
fn clip_engine_span(
    model: &ProjectModel,
    projection_timeline_clock: &str,
    clip_use_id: &str,
    edit_label: &str,
) -> Result<(i64, i64)> {
    let clip_use = model
        .clip_uses
        .iter()
        .find(|u| u.id.0 == clip_use_id)
        .ok_or_else(|| {
            invalid(format!(
                "{edit_label}: target `{clip_use_id}` is not a clip use in the model"
            ))
        })?;
    let start = model_to_engine_ticks(
        model,
        &clip_use.timeline_interval.clock_id.0,
        clip_use.timeline_interval.start,
        &format!("{edit_label}: timeline start"),
    )?;
    let end = model_to_engine_ticks(
        model,
        &clip_use.timeline_interval.clock_id.0,
        clip_use.timeline_interval.end,
        &format!("{edit_label}: timeline end"),
    )?;
    let _ = projection_timeline_clock;
    Ok((start, end))
}

fn observation_text(observation: &ren_protocol::project_model::Observation) -> Option<String> {
    observation
        .payload
        .get("text")
        .and_then(|v| v.as_str())
        .map(str::to_owned)
        .filter(|text| !text.trim().is_empty())
}

/// Pure, deterministic caption plan over speech/caption observations.
/// Empty text, missing text and empty intervals refuse rather than
/// placing blank or zero-length captions.
pub fn plan_captions(model: &ProjectModel) -> Result<Vec<CaptionCue>> {
    let mut cues = Vec::new();
    let mut ordered: Vec<_> = model.observations.iter().collect();
    ordered.sort_by(|a, b| {
        (a.interval.start, a.interval.end, &a.id.0).cmp(&(
            b.interval.start,
            b.interval.end,
            &b.id.0,
        ))
    });
    for observation in ordered {
        if !CAPTION_MODALITIES.contains(&observation.modality.as_str()) {
            continue;
        }
        let text = observation_text(observation).ok_or_else(|| {
            invalid(format!(
                "observation `{}` (modality `{}`) carries no non-empty `payload.text`; captions need words",
                observation.id.0, observation.modality
            ))
        })?;
        let start = model_to_engine_ticks(
            model,
            &observation.interval.clock_id.0,
            observation.interval.start,
            &format!("caption `{}` start", observation.id.0),
        )?;
        let end = model_to_engine_ticks(
            model,
            &observation.interval.clock_id.0,
            observation.interval.end,
            &format!("caption `{}` end", observation.id.0),
        )?;
        if end <= start {
            return Err(invalid(format!(
                "observation `{}` spans no engine time; a zero-length caption would place nothing",
                observation.id.0
            )));
        }
        cues.push(CaptionCue {
            speaker: observation
                .payload
                .get("speaker")
                .or_else(|| observation.payload.get("speaker_entity_id"))
                .and_then(|v| v.as_str())
                .map(str::to_owned),
            text,
            start_ticks: start,
            duration_ticks: end - start,
            source_id: observation.id.0.clone(),
        });
    }
    Ok(cues)
}

/// Ren-side equivalence counterpart for captions: the model must plan
/// exactly `expected` (same cues in the same order). Captions do not
/// alter clip geometry, so the model returns unchanged; the check keeps
/// the engine readback anchored to what the model actually says.
pub fn apply_captions_to_model(
    model: &ProjectModel,
    expected: &[CaptionCue],
) -> Result<ProjectModel> {
    let planned = plan_captions(model)?;
    if planned != expected {
        return Err(invalid(format!(
            "caption plan drifted: the model plans {} cue(s), expected {}",
            planned.len(),
            expected.len()
        )));
    }
    Ok(model.clone())
}

/// Pure, deterministic title plan over `title` observations. Each title
/// binds to the clip use whose timeline span overlaps the observation's
/// engine span; a title over no clip refuses (`title_without_clip`).
/// Fractional-second durations refuse (`title_fractional_seconds`): the
/// engine places titles on whole-frame spans.
pub fn plan_titles(model: &ProjectModel, placements: &[Placement]) -> Result<Vec<TitlePlan>> {
    let mut ordered: Vec<_> = model.observations.iter().collect();
    ordered.sort_by(|a, b| {
        (a.interval.start, a.interval.end, &a.id.0).cmp(&(
            b.interval.start,
            b.interval.end,
            &b.id.0,
        ))
    });
    let mut plans = Vec::new();
    for observation in ordered {
        if observation.modality.as_str() != TITLE_MODALITY {
            continue;
        }
        let text = observation_text(observation).ok_or_else(|| {
            invalid(format!(
                "observation `{}` (modality `title`) carries no non-empty `payload.text`; titles need words",
                observation.id.0
            ))
        })?;
        let start = model_to_engine_ticks(
            model,
            &observation.interval.clock_id.0,
            observation.interval.start,
            &format!("title `{}` start", observation.id.0),
        )?;
        let end = model_to_engine_ticks(
            model,
            &observation.interval.clock_id.0,
            observation.interval.end,
            &format!("title `{}` end", observation.id.0),
        )?;
        if end <= start {
            return Err(invalid(format!(
                "observation `{}` spans no engine time; a zero-length title would place nothing",
                observation.id.0
            )));
        }
        let span = end - start;
        if span % ENGINE_TICKS_PER_SECOND != 0 {
            return Err(unsupported(
                "title_fractional_seconds",
                format!(
                    "title `{}` spans {span} engine ticks, not whole seconds; the engine places titles on whole-frame spans, so fractional titles refuse rather than round",
                    observation.id.0
                ),
            ));
        }
        let host = placements
            .iter()
            .map(|placement| {
                let (clip_start, clip_end) = clip_engine_span(
                    model,
                    &placement.track,
                    &placement.clip_use_id,
                    &format!("title `{}` host", observation.id.0),
                )?;
                Ok((placement, clip_start, clip_end))
            })
            .collect::<Result<Vec<_>>>()?
            .into_iter()
            .filter(|(_, clip_start, clip_end)| start < *clip_end && *clip_start < end)
            .map(|(placement, _, _)| placement)
            .next()
            .ok_or_else(|| {
                unsupported(
                    "title_without_clip",
                    format!(
                        "title `{}` overlaps no placed clip use; titles bind to a clip and are never free-floating",
                        observation.id.0
                    ),
                )
            })?;
        plans.push(TitlePlan {
            text,
            clip_use_id: host.clip_use_id.clone(),
            time_ticks: start,
            duration_seconds: (span / ENGINE_TICKS_PER_SECOND) as u64,
            source_id: observation.id.0.clone(),
        });
    }
    Ok(plans)
}

/// Ren-side equivalence counterpart for titles.
pub fn apply_titles_to_model(
    model: &ProjectModel,
    placements: &[Placement],
    expected: &[TitlePlan],
) -> Result<ProjectModel> {
    let planned = plan_titles(model, placements)?;
    if planned != expected {
        return Err(invalid(format!(
            "title plan drifted: the model plans {} title(s), expected {}",
            planned.len(),
            expected.len()
        )));
    }
    Ok(model.clone())
}

/// Pure LUT plan over `color.lut` effects. `parameters` must carry exactly
/// `lut` as a non-empty string; the target must be a placed clip use.
pub fn plan_lut_effects(model: &ProjectModel, placements: &[Placement]) -> Result<Vec<LutPlan>> {
    let mut effects: Vec<_> = model.effects.iter().collect();
    effects.sort_by(|a, b| a.id.0.cmp(&b.id.0));
    let mut plans = Vec::new();
    for effect in effects {
        if effect.class_id.as_str() != LUT_CLASS_ID {
            continue;
        }
        if effect.parameters.len() != 1 || !effect.parameters.contains_key("lut") {
            return Err(unsupported(
                "lut_unknown_parameter",
                format!(
                    "effect `{}` (color.lut) must carry exactly `parameters.lut`; it carries {:?}",
                    effect.id.0,
                    effect.parameters.keys().collect::<Vec<_>>()
                ),
            ));
        }
        let lut = effect.parameters["lut"].as_str().ok_or_else(|| {
            invalid(format!(
                "effect `{}` (color.lut) needs `parameters.lut` as a string",
                effect.id.0
            ))
        })?;
        if lut.trim().is_empty() {
            return Err(unsupported(
                "lut_empty_reference",
                format!(
                    "effect `{}` (color.lut) names an empty LUT; refusing rather than applying a default look",
                    effect.id.0
                ),
            ));
        }
        if !effect.keyframes.is_empty() {
            return Err(unsupported(
                "lut_keyframes_not_supported",
                format!(
                    "effect `{}` (color.lut) carries keyframes; the Lumetri input LUT is a whole-clip assignment with no keyframe path",
                    effect.id.0
                ),
            ));
        }
        if !placements.iter().any(|p| p.clip_use_id == effect.target_id) {
            return Err(invalid(format!(
                "effect `{}` targets `{}` which has no placement in the projection",
                effect.id.0, effect.target_id
            )));
        }
        plans.push(LutPlan {
            effect_id: effect.id.0.clone(),
            clip_use_id: effect.target_id.clone(),
            lut: lut.to_owned(),
        });
    }
    Ok(plans)
}

/// Ren-side equivalence counterpart for LUTs.
pub fn apply_lut_to_model(
    model: &ProjectModel,
    placements: &[Placement],
    expected: &[LutPlan],
) -> Result<ProjectModel> {
    let planned = plan_lut_effects(model, placements)?;
    if planned != expected {
        return Err(invalid(format!(
            "LUT plan drifted: the model plans {} application(s), expected {}",
            planned.len(),
            expected.len()
        )));
    }
    Ok(model.clone())
}

fn finite_number(value: &serde_json::Value) -> Option<f64> {
    value.as_f64().filter(|v| v.is_finite())
}

fn engine_value_for(
    class_id: &str,
    value: &serde_json::Value,
    effect_id: &str,
) -> Result<serde_json::Value> {
    match class_id {
        TRANSFORM_POSITION_CLASS => {
            let pair = value.as_array().ok_or_else(|| {
                invalid(format!(
                    "effect `{effect_id}` (transform.position) needs a `[x, y]` value"
                ))
            })?;
            if pair.len() != 2 || pair.iter().any(|v| finite_number(v).is_none()) {
                return Err(invalid(format!(
                    "effect `{effect_id}` (transform.position) needs two finite numbers, got {value}"
                )));
            }
            Ok(serde_json::json!([pair[0], pair[1]]))
        }
        TRANSFORM_SCALE_CLASS | TRANSFORM_ROTATION_CLASS | OPACITY_CLASS_ID => {
            let number = finite_number(value).ok_or_else(|| {
                invalid(format!(
                    "effect `{effect_id}` ({class_id}) needs a finite number, got {value}"
                ))
            })?;
            if class_id == TRANSFORM_SCALE_CLASS && number <= 0.0 {
                return Err(invalid(format!(
                    "effect `{effect_id}` (transform.scale) needs a positive scale, got {value}"
                )));
            }
            let engine_number = if class_id == TRANSFORM_SCALE_CLASS {
                // Project Model scale is a multiplicative factor relative
                // to source pixels (1.0 is unchanged); FilmCraft's
                // motion.scale parameter is a percentage (100.0 is unchanged).
                number * 100.0
            } else {
                number
            };
            Ok(serde_json::json!(engine_number))
        }
        _ => Err(unsupported(
            "effect_value_not_supported",
            format!("effect `{effect_id}` ({class_id}) has no engine value mapping"),
        )),
    }
}

fn engine_effect_param(class_id: &str) -> (&'static str, &'static str, bool) {
    match class_id {
        TRANSFORM_POSITION_CLASS => ("motion", "position", false),
        TRANSFORM_SCALE_CLASS => ("motion", "scale", false),
        TRANSFORM_ROTATION_CLASS => ("motion", "rotation", false),
        OPACITY_CLASS_ID => ("opacity", "opacity", false),
        TRANSFORM_CROP_CLASS => ("crop", "", true),
        _ => ("", "", false),
    }
}

/// Pure keyframe plan over the transform/crop/opacity effect classes.
/// Every key tick must sit inside both the effect interval and the target
/// clip's timeline span (engine ticks); anything outside refuses with
/// `keyframe_out_of_span` instead of reaching the engine, which would
/// answer success while placing nothing. Static `transform.crop`
/// parameters plan as whole-clip writes; animated crops refuse with
/// `crop_animation_not_supported`.
pub fn plan_effect_keyframes(
    model: &ProjectModel,
    placements: &[Placement],
) -> Result<Vec<EffectKeyPlan>> {
    let mut effects: Vec<_> = model.effects.iter().collect();
    effects.sort_by(|a, b| a.id.0.cmp(&b.id.0));
    let mut plans = Vec::new();
    for effect in effects {
        let class_id = effect.class_id.as_str();
        let (engine_effect, engine_param, is_crop) = match class_id {
            "transform.position" | "transform.scale" | "transform.rotation" | "opacity.opacity"
            | "transform.crop" => engine_effect_param(class_id),
            _ => continue,
        };
        let placement = placements
            .iter()
            .find(|p| p.clip_use_id == effect.target_id)
            .ok_or_else(|| {
                invalid(format!(
                    "effect `{}` targets `{}` which has no placement in the projection",
                    effect.id.0, effect.target_id
                ))
            })?;
        let (clip_start, clip_end) = clip_engine_span(
            model,
            &placement.track,
            &effect.target_id,
            &format!("effect `{}` span", effect.id.0),
        )?;
        let effect_start = model_to_engine_ticks(
            model,
            &effect.interval.clock_id.0,
            effect.interval.start,
            &format!("effect `{}` interval start", effect.id.0),
        )?;
        let effect_end = model_to_engine_ticks(
            model,
            &effect.interval.clock_id.0,
            effect.interval.end,
            &format!("effect `{}` interval end", effect.id.0),
        )?;
        if effect.keyframes.is_empty()
            && !is_crop
            && (effect_start != clip_start || effect_end != clip_end)
        {
            return Err(unsupported(
                "static_transform_span_mismatch",
                format!(
                    "effect `{}` ({class_id}) is static but its interval does not cover the target clip use; static transforms must cover the full placement",
                    effect.id.0
                ),
            ));
        }
        if is_crop {
            if !effect.keyframes.is_empty() {
                return Err(unsupported(
                    "crop_animation_not_supported",
                    format!(
                        "effect `{}` (transform.crop) carries keyframes; animated crops have no measured engine path - plan one static rect per effect instead",
                        effect.id.0
                    ),
                ));
            }
            let mut static_params = Vec::new();
            for edge in ["left", "top", "right", "bottom"] {
                let value = effect.parameters.get(edge).ok_or_else(|| {
                    invalid(format!(
                        "effect `{}` (transform.crop) needs `parameters.{edge}` as a number",
                        effect.id.0
                    ))
                })?;
                let number = finite_number(value).ok_or_else(|| {
                    invalid(format!(
                        "effect `{}` (transform.crop) needs `parameters.{edge}` as a finite number, got {value}",
                        effect.id.0
                    ))
                })?;
                static_params.push((edge.to_owned(), serde_json::json!(number)));
            }
            if let Some(extra) = effect
                .parameters
                .keys()
                .find(|k| !["left", "top", "right", "bottom"].contains(&k.as_str()))
            {
                return Err(unsupported(
                    "crop_unknown_parameter",
                    format!(
                        "effect `{}` (transform.crop) carries unknown parameter `{extra}`; the engine crop exposes only left/top/right/bottom",
                        effect.id.0
                    ),
                ));
            }
            plans.push(EffectKeyPlan {
                effect_id: effect.id.0.clone(),
                clip_use_id: effect.target_id.clone(),
                engine_effect: engine_effect.into(),
                engine_param: None,
                needs_apply: Some("crop".into()),
                static_params,
                keys: Vec::new(),
            });
            continue;
        }
        if effect.keyframes.is_empty() {
            let (parameter, expected) = match class_id {
                TRANSFORM_POSITION_CLASS => ("position", "a two-number [x, y] pair"),
                TRANSFORM_SCALE_CLASS => ("scale", "a finite number"),
                TRANSFORM_ROTATION_CLASS => ("rotation", "a finite number"),
                OPACITY_CLASS_ID => ("opacity", "a finite number"),
                _ => unreachable!(),
            };
            if effect.parameters.len() != 1 || !effect.parameters.contains_key(parameter) {
                return Err(invalid(format!(
                    "effect `{}` ({class_id}) needs exactly `parameters.{parameter}` ({expected})",
                    effect.id.0
                )));
            }
            let raw = &effect.parameters[parameter];
            let value = if class_id == TRANSFORM_POSITION_CLASS {
                let pair = raw.as_array().ok_or_else(|| {
                    invalid(format!(
                        "effect `{}` (transform.position) needs a `[x, y]` value",
                        effect.id.0
                    ))
                })?;
                if pair.len() != 2 || pair.iter().any(|part| finite_number(part).is_none()) {
                    return Err(invalid(format!(
                        "effect `{}` (transform.position) needs two finite numbers, got {raw}",
                        effect.id.0
                    )));
                }
                serde_json::json!([pair[0], pair[1]])
            } else {
                let number = finite_number(raw).ok_or_else(|| {
                    invalid(format!(
                        "effect `{}` ({class_id}) needs a finite number, got {raw}",
                        effect.id.0
                    ))
                })?;
                if class_id == TRANSFORM_SCALE_CLASS && number <= 0.0 {
                    return Err(invalid(format!(
                        "effect `{}` (transform.scale) needs a positive scale, got {raw}",
                        effect.id.0
                    )));
                }
                let engine_number = if class_id == TRANSFORM_SCALE_CLASS {
                    // Project Model scale is a multiplicative factor;
                    // FilmCraft stores motion.scale as percent.
                    number * 100.0
                } else {
                    number
                };
                serde_json::json!(engine_number)
            };
            plans.push(EffectKeyPlan {
                effect_id: effect.id.0.clone(),
                clip_use_id: effect.target_id.clone(),
                engine_effect: engine_effect.into(),
                engine_param: None,
                needs_apply: None,
                static_params: vec![(parameter.into(), value)],
                keys: Vec::new(),
            });
            continue;
        }
        if !effect.parameters.is_empty() {
            return Err(unsupported(
                "effect_static_params_unsupported",
                format!(
                    "effect `{}` ({class_id}) carries static parameters; keyframed picture effects express values through keyframes only",
                    effect.id.0
                ),
            ));
        }
        let mut keys = Vec::new();
        let mut ordered: Vec<_> = effect.keyframes.iter().collect();
        ordered.sort_by_key(|k| k.tick);
        for key in ordered {
            let time = model_to_engine_ticks(
                model,
                &effect.interval.clock_id.0,
                key.tick,
                &format!("effect `{}` keyframe", effect.id.0),
            )?;
            if time < effect_start || time >= effect_end {
                return Err(unsupported(
                    "keyframe_out_of_span",
                    format!(
                        "effect `{}` keyframe at tick {} sits outside its interval; refusing rather than placing nothing",
                        effect.id.0, key.tick
                    ),
                ));
            }
            if time < clip_start || time >= clip_end {
                return Err(unsupported(
                    "keyframe_out_of_span",
                    format!(
                        "effect `{}` keyframe at tick {} falls outside its clip's timeline span; the engine answers such writes with success while placing nothing, so planning refuses",
                        effect.id.0, key.tick
                    ),
                ));
            }
            keys.push(EffectKey {
                time_ticks: time,
                value: engine_value_for(class_id, &key.value, &effect.id.0)?,
            });
        }
        plans.push(EffectKeyPlan {
            effect_id: effect.id.0.clone(),
            clip_use_id: effect.target_id.clone(),
            engine_effect: engine_effect.into(),
            engine_param: Some(engine_param.into()),
            needs_apply: None,
            static_params: Vec::new(),
            keys,
        });
    }
    Ok(plans)
}

/// Ren-side equivalence counterpart for keyframed picture effects.
pub fn apply_effect_keyframes_to_model(
    model: &ProjectModel,
    placements: &[Placement],
    expected: &[EffectKeyPlan],
) -> Result<ProjectModel> {
    let planned = plan_effect_keyframes(model, placements)?;
    if planned != expected {
        return Err(invalid(format!(
            "picture-key plan drifted: the model plans {} effect(s), expected {}",
            planned.len(),
            expected.len()
        )));
    }
    Ok(model.clone())
}

/// Pure volume-automation plan over `audio.volume` effects. Points are
/// engine ticks with finite dB values on the placement's audio strip;
/// keys outside the effect interval refuse with
/// `volume_key_outside_interval`. A target without an audio strip refuses
/// with `no_audio_for_volume`.
pub fn plan_volume_automation(
    model: &ProjectModel,
    placements: &[Placement],
) -> Result<Vec<VolumePlan>> {
    let mut effects: Vec<_> = model.effects.iter().collect();
    effects.sort_by(|a, b| a.id.0.cmp(&b.id.0));
    let mut plans = Vec::new();
    for effect in effects {
        if effect.class_id.as_str() != VOLUME_CLASS_ID {
            continue;
        }
        if !effect.parameters.is_empty() {
            return Err(unsupported(
                "volume_static_params_unsupported",
                format!(
                    "effect `{}` (audio.volume) carries static parameters; volume automation expresses levels through keyframes only",
                    effect.id.0
                ),
            ));
        }
        if effect.keyframes.is_empty() {
            return Err(unsupported(
                "volume_has_no_keyframes",
                format!(
                    "effect `{}` (audio.volume) carries no keyframes; automation with nothing timed would drive nothing",
                    effect.id.0
                ),
            ));
        }
        let placement = placements
            .iter()
            .find(|p| p.clip_use_id == effect.target_id)
            .ok_or_else(|| {
                invalid(format!(
                    "effect `{}` targets `{}` which has no placement in the projection",
                    effect.id.0, effect.target_id
                ))
            })?;
        let strip = placement.audio_track.clone().ok_or_else(|| {
            unsupported(
                "no_audio_for_volume",
                format!(
                    "effect `{}` automates `{}` which carries no audio stream; there is no mixer strip to drive",
                    effect.id.0, effect.target_id
                ),
            )
        })?;
        let effect_start = model_to_engine_ticks(
            model,
            &effect.interval.clock_id.0,
            effect.interval.start,
            &format!("effect `{}` interval start", effect.id.0),
        )?;
        let effect_end = model_to_engine_ticks(
            model,
            &effect.interval.clock_id.0,
            effect.interval.end,
            &format!("effect `{}` interval end", effect.id.0),
        )?;
        let mut points = Vec::new();
        let mut ordered: Vec<_> = effect.keyframes.iter().collect();
        ordered.sort_by_key(|k| k.tick);
        for key in ordered {
            let time = model_to_engine_ticks(
                model,
                &effect.interval.clock_id.0,
                key.tick,
                &format!("effect `{}` keyframe", effect.id.0),
            )?;
            if time < effect_start || time >= effect_end {
                return Err(unsupported(
                    "volume_key_outside_interval",
                    format!(
                        "effect `{}` keyframe at tick {} sits outside its interval; refusing rather than automating where nothing plays",
                        effect.id.0, key.tick
                    ),
                ));
            }
            let db = finite_number(&key.value).ok_or_else(|| {
                invalid(format!(
                    "effect `{}` (audio.volume) needs finite dB numbers, got {}",
                    effect.id.0, key.value
                ))
            })?;
            points.push((time, db));
        }
        plans.push(VolumePlan {
            effect_id: effect.id.0.clone(),
            clip_use_id: effect.target_id.clone(),
            strip,
            points,
        });
    }
    Ok(plans)
}

/// Ren-side equivalence counterpart for volume automation.
pub fn apply_volume_automation_to_model(
    model: &ProjectModel,
    placements: &[Placement],
    expected: &[VolumePlan],
) -> Result<ProjectModel> {
    let planned = plan_volume_automation(model, placements)?;
    if planned != expected {
        return Err(invalid(format!(
            "volume plan drifted: the model plans {} automation(s), expected {}",
            planned.len(),
            expected.len()
        )));
    }
    Ok(model.clone())
}

/// Pure bed-ducking plan over `audio.ducking` effects. Each effect targets a
/// `music` stem use and declares its own curve: `full_db` (bed level outside
/// the duck), `depth_db` (how far below full the ducked level sits, a
/// positive number of dB) and `ramp_ticks` (ramp per edge, on the effect
/// interval's clock, zero for a hard step); the effect interval is the
/// ducked span (the dialogue it dips under). The plan derives one
/// [`VolumePlan`] per effect on the bed's strip and executes through the
/// same [`volume_operations`] lane as [`VOLUME_CLASS_ID`].
///
/// Fail-closed: keyframes refuse (`ducking_keyframes_unsupported` - levels
/// come from the declared depth and ramp, not keys), unknown parameters
/// refuse (`ducking_unknown_parameter`), a missing or non-finite full
/// level, a missing, non-finite or non-positive depth and a missing,
/// non-integer or negative ramp refuse as invalid model data, a target
/// without an audio strip refuses (`no_audio_for_ducking`), a non-music
/// target refuses (`ducking_target_not_music` - SFX ducking policy is out
/// of scope), and a ducked span outside its bed's timeline span refuses
/// (`ducking_outside_bed`). Ramp holds clamp into the bed span; the ducked
/// keys themselves stay exact.
pub fn plan_bed_ducking(model: &ProjectModel, placements: &[Placement]) -> Result<Vec<VolumePlan>> {
    let mut effects: Vec<_> = model.effects.iter().collect();
    effects.sort_by(|a, b| a.id.0.cmp(&b.id.0));
    let mut plans = Vec::new();
    for effect in effects {
        if effect.class_id.as_str() != DUCKING_CLASS_ID {
            continue;
        }
        if !effect.keyframes.is_empty() {
            return Err(unsupported(
                "ducking_keyframes_unsupported",
                format!(
                    "effect `{}` (audio.ducking) carries keyframes; ducking levels derive from its declared full_db, depth_db and ramp_ticks, not keys",
                    effect.id.0
                ),
            ));
        }
        for key in effect.parameters.keys() {
            if !matches!(key.as_str(), "full_db" | "depth_db" | "ramp_ticks") {
                return Err(unsupported(
                    "ducking_unknown_parameter",
                    format!(
                        "effect `{}` (audio.ducking) carries unmapped parameter `{key}`; the ducking curve reads full_db, depth_db and ramp_ticks only",
                        effect.id.0
                    ),
                ));
            }
        }
        let full_db = effect
            .parameters
            .get("full_db")
            .and_then(finite_number)
            .ok_or_else(|| {
                invalid(format!(
                    "effect `{}` (audio.ducking) needs `full_db` as a finite dB level for the bed outside the duck",
                    effect.id.0
                ))
            })?;
        let depth_db = effect
            .parameters
            .get("depth_db")
            .and_then(finite_number)
            .ok_or_else(|| {
                invalid(format!(
                    "effect `{}` (audio.ducking) needs `depth_db` as a finite positive dB dip below its full level",
                    effect.id.0
                ))
            })?;
        if depth_db <= 0.0 {
            return Err(invalid(format!(
                "effect `{}` (audio.ducking) needs `depth_db` positive, got {depth_db}; a zero dip would drive nothing",
                effect.id.0
            )));
        }
        let ramp_ticks = effect
            .parameters
            .get("ramp_ticks")
            .and_then(|v| v.as_i64())
            .ok_or_else(|| {
                invalid(format!(
                    "effect `{}` (audio.ducking) needs `ramp_ticks` as a non-negative integer ramp per edge on its interval clock",
                    effect.id.0
                ))
            })?;
        if ramp_ticks < 0 {
            return Err(invalid(format!(
                "effect `{}` (audio.ducking) needs `ramp_ticks` non-negative, got {ramp_ticks}",
                effect.id.0
            )));
        }
        let placement = placements
            .iter()
            .find(|p| p.clip_use_id == effect.target_id)
            .ok_or_else(|| {
                invalid(format!(
                    "effect `{}` targets `{}` which has no placement in the projection",
                    effect.id.0, effect.target_id
                ))
            })?;
        let strip = placement.audio_track.clone().ok_or_else(|| {
            unsupported(
                "no_audio_for_ducking",
                format!(
                    "effect `{}` ducks `{}` which carries no audio stream; there is no mixer strip to drive",
                    effect.id.0, effect.target_id
                ),
            )
        })?;
        let clip_use = model
            .clip_uses
            .iter()
            .find(|u| u.id.0 == effect.target_id)
            .ok_or_else(|| {
                invalid(format!(
                    "effect `{}` targets `{}` which is not a clip use in the model",
                    effect.id.0, effect.target_id
                ))
            })?;
        if clip_use.role.as_str() != "music" {
            return Err(unsupported(
                "ducking_target_not_music",
                format!(
                    "effect `{}` ducks `{}` with role `{}`; the native mix ducks the music bed only, SFX ducking policy stays with the Resolve path",
                    effect.id.0, effect.target_id, clip_use.role
                ),
            ));
        }
        let bed_clock = model
            .clocks
            .iter()
            .find(|c| c.id.0 == clip_use.timeline_interval.clock_id.0)
            .ok_or_else(|| {
                invalid(format!(
                    "effect `{}` target `{}` names unknown timeline clock `{}`",
                    effect.id.0, effect.target_id, clip_use.timeline_interval.clock_id.0
                ))
            })?;
        let bed_start = scale_exact(
            placement.timeline_start,
            i128::from(ENGINE_TICKS_PER_SECOND),
            i128::from(bed_clock.ticks_per_second),
            &format!("effect `{}` bed span start", effect.id.0),
        )?;
        let bed_end = scale_exact(
            placement.timeline_end,
            i128::from(ENGINE_TICKS_PER_SECOND),
            i128::from(bed_clock.ticks_per_second),
            &format!("effect `{}` bed span end", effect.id.0),
        )?;
        let duck_start = model_to_engine_ticks(
            model,
            &effect.interval.clock_id.0,
            effect.interval.start,
            &format!("effect `{}` ducked span start", effect.id.0),
        )?;
        let duck_end = model_to_engine_ticks(
            model,
            &effect.interval.clock_id.0,
            effect.interval.end,
            &format!("effect `{}` ducked span end", effect.id.0),
        )?;
        if duck_start < bed_start || duck_end > bed_end {
            return Err(unsupported(
                "ducking_outside_bed",
                format!(
                    "effect `{}` ducks [{duck_start}, {duck_end}) outside its bed span [{bed_start}, {bed_end}); refusing rather than automating where nothing plays",
                    effect.id.0
                ),
            ));
        }
        let ramp = scale_exact(
            ramp_ticks,
            i128::from(ENGINE_TICKS_PER_SECOND),
            i128::from(
                model
                    .clocks
                    .iter()
                    .find(|c| c.id.0 == effect.interval.clock_id.0)
                    .ok_or_else(|| {
                        invalid(format!(
                            "effect `{}` names unknown interval clock `{}`",
                            effect.id.0, effect.interval.clock_id.0
                        ))
                    })?
                    .ticks_per_second,
            ),
            &format!("effect `{}` ramp", effect.id.0),
        )?;
        let ducked_db = full_db - depth_db;
        // Open at full, hold full until the ramp-in starts, land ducked at
        // the span start, hold ducked to the span end, ramp back to full,
        // hold full to the bed end. A ducked edge ON the bed edge drops
        // its hold point (the bed opens/closes ducked); a zero ramp drops
        // the ramp points (a hard step). Guards keep every tick strictly
        // increasing, so no tick carries two levels.
        let mut points = Vec::new();
        if duck_start > bed_start {
            points.push((bed_start, full_db));
        }
        if ramp > 0 && duck_start - ramp > bed_start {
            points.push((duck_start - ramp, full_db));
        }
        points.push((duck_start, ducked_db));
        points.push((duck_end, ducked_db));
        if ramp > 0 && duck_end + ramp < bed_end {
            points.push((duck_end + ramp, full_db));
        }
        if duck_end < bed_end {
            points.push((bed_end, full_db));
        }
        plans.push(VolumePlan {
            effect_id: effect.id.0.clone(),
            clip_use_id: effect.target_id.clone(),
            strip,
            points,
        });
    }
    Ok(plans)
}

/// Ren-side equivalence counterpart for bed ducking.
pub fn apply_bed_ducking_to_model(
    model: &ProjectModel,
    placements: &[Placement],
    expected: &[VolumePlan],
) -> Result<ProjectModel> {
    let planned = plan_bed_ducking(model, placements)?;
    if planned != expected {
        return Err(invalid(format!(
            "ducking plan drifted: the model plans {} duck(s), expected {}",
            planned.len(),
            expected.len()
        )));
    }
    Ok(model.clone())
}

/// Partition model effects into executed plans versus explicit losses.
/// Projectable effects validate here (fail-closed: a malformed projectable
/// effect refuses the whole projection with its named code).
/// EffectCraft-owned classes report `effect_owned_by_effectcraft`; every
/// other unmapped class keeps `effect_not_projected`.
fn projectable_effect_losses(
    model: &ProjectModel,
    placements: &[Placement],
    _timeline: &Clock,
) -> Result<Vec<LossReport>> {
    let lut = plan_lut_effects(model, placements)?;
    let keys = plan_effect_keyframes(model, placements)?;
    let volume = plan_volume_automation(model, placements)?;
    let ducking = plan_bed_ducking(model, placements)?;
    let planned: usize = lut.len() + keys.len() + volume.len() + ducking.len();
    let mut owned = 0usize;
    let mut unmapped = 0usize;
    for effect in &model.effects {
        let class_id = effect.class_id.as_str();
        if filmcraft_executed(class_id) {
            continue;
        }
        if effectcraft_owned(class_id) {
            owned += 1;
        } else {
            unmapped += 1;
        }
    }
    let mut losses = Vec::new();
    if owned > 0 {
        losses.push(LossReport {
            code: "effect_owned_by_effectcraft".into(),
            detail: format!(
                "the model carries {owned} EffectCraft-owned effect(s) (camera moves, blur/saturation); they stay with EffectCraft and were not sent here"
            ),
        });
    }
    if unmapped > 0 {
        losses.push(LossReport {
            code: "effect_not_projected".into(),
            detail: format!(
                "the model carries {unmapped} effect(s) with no FilmCraft mapping; none were sent"
            ),
        });
    }
    let _ = planned;
    Ok(losses)
}

/// Caption operations for planned cues on one track: one `AddCaption`
/// per cue, in plan order. The caller creates the track first with
/// [`super::Operation::CreateCaptionTrack`] and passes its id back.
pub fn caption_operations(
    project: &Path,
    track: Option<String>,
    cues: &[CaptionCue],
) -> Vec<super::Operation> {
    cues.iter()
        .map(|cue| super::Operation::AddCaption {
            project: project.into(),
            track: track.clone(),
            text: cue.text.clone(),
            time_ticks: cue.start_ticks,
            duration_ticks: cue.duration_ticks,
        })
        .collect()
}

/// Title operations for planned titles: one `GraphicsNewText` per title,
/// bound to its host clip use's engine clip id and placed at the planned
/// engine-tick time. Times store verbatim on a sequence pinned to the
/// model's timeline rate (see `SequenceSettings`); the qualification pins
/// 24fps so placement reads back frame-exact.
pub fn title_operations(
    project: &Path,
    clip_ids: &BTreeMap<String, Vec<u64>>,
    plans: &[TitlePlan],
) -> Result<Vec<super::Operation>> {
    let mut operations = Vec::new();
    for plan in plans {
        let ids = clip_ids.get(&plan.clip_use_id).ok_or_else(|| {
            invalid(format!(
                "title `{}` binds `{}` which has no reconciled engine clip id",
                plan.source_id, plan.clip_use_id
            ))
        })?;
        operations.push(super::Operation::GraphicsNewText {
            project: project.into(),
            text: plan.text.clone(),
            clip: Some(primary_clip_id(ids, &plan.clip_use_id)?),
            time_ticks: Some(plan.time_ticks),
            duration_seconds: Some(plan.duration_seconds as f64),
        });
    }
    Ok(operations)
}

/// LUT import operations for path-based plans: one `ImportLut` per plan
/// whose `lut` is a filesystem path. Reference plans (`builtin:`/`lib:`)
/// need no import and produce no operation here.
pub fn lut_import_operations(project: &Path, plans: &[LutPlan]) -> Vec<super::Operation> {
    plans
        .iter()
        .filter(|plan| !plan.is_reference())
        .map(|plan| super::Operation::ImportLut {
            project: project.into(),
            path: PathBuf::from(&plan.lut),
            name: Some(plan.effect_id.clone()),
        })
        .collect()
}

/// LUT apply operations: one `ApplyLut` per plan, resolving path-based
/// plans through `imported_refs` (effect id to the receipt's `ref`).
pub fn lut_apply_operations(
    project: &Path,
    clip_ids: &BTreeMap<String, Vec<u64>>,
    plans: &[LutPlan],
    imported_refs: &BTreeMap<String, String>,
) -> Result<Vec<super::Operation>> {
    let mut operations = Vec::new();
    for plan in plans {
        let ids = clip_ids.get(&plan.clip_use_id).ok_or_else(|| {
            invalid(format!(
                "effect `{}` targets `{}` which has no reconciled engine clip id",
                plan.effect_id, plan.clip_use_id
            ))
        })?;
        let lut = if plan.is_reference() {
            plan.lut.clone()
        } else {
            imported_refs.get(&plan.effect_id).cloned().ok_or_else(|| {
                invalid(format!(
                    "effect `{}` imports `{}` with no receipt ref; import it before applying",
                    plan.effect_id, plan.lut
                ))
            })?
        };
        operations.push(super::Operation::ApplyLut {
            project: project.into(),
            clip: primary_clip_id(ids, &plan.clip_use_id)?,
            lut,
        });
    }
    Ok(operations)
}

/// Picture operations for keyframed plans: `ApplyEffect` for non-built-in
/// effects (crop), one `ToggleEffectAnimation` per animated param, then
/// one timed `SetEffectParam` per key - the measured write path that
/// creates exact keys readable back from the project file.
pub fn effect_key_operations(
    project: &Path,
    clip_ids: &BTreeMap<String, Vec<u64>>,
    plans: &[EffectKeyPlan],
) -> Result<Vec<super::Operation>> {
    let mut operations = Vec::new();
    for plan in plans {
        let ids = clip_ids.get(&plan.clip_use_id).ok_or_else(|| {
            invalid(format!(
                "effect `{}` targets `{}` which has no reconciled engine clip id",
                plan.effect_id, plan.clip_use_id
            ))
        })?;
        let clip = primary_clip_id(ids, &plan.clip_use_id)?;
        if let Some(apply) = plan.needs_apply.clone() {
            operations.push(super::Operation::ApplyEffect {
                project: project.into(),
                clips: vec![clip],
                effect: apply,
            });
        }
        for (param, value) in &plan.static_params {
            operations.push(super::Operation::SetEffectParam {
                project: project.into(),
                clip,
                effect: plan.engine_effect.clone(),
                param: param.clone(),
                value: value.clone(),
                time_ticks: None,
            });
        }
        if let Some(param) = plan.engine_param.clone() {
            if !plan.keys.is_empty() {
                operations.push(super::Operation::ToggleEffectAnimation {
                    project: project.into(),
                    clip,
                    effect: plan.engine_effect.clone(),
                    param: param.clone(),
                });
                for key in &plan.keys {
                    operations.push(super::Operation::SetEffectParam {
                        project: project.into(),
                        clip,
                        effect: plan.engine_effect.clone(),
                        param: param.clone(),
                        value: key.value.clone(),
                        time_ticks: Some(key.time_ticks),
                    });
                }
            }
        }
    }
    Ok(operations)
}

/// Model-owned static and animated picture transforms, executed after each
/// clip use has a reconciled engine id and before export.
pub fn project_picture_effect_operations(
    project: &Path,
    model: &ProjectModel,
    placements: &[Placement],
    clip_ids: &BTreeMap<String, Vec<u64>>,
) -> Result<Vec<super::Operation>> {
    let plans = plan_effect_keyframes(model, placements)?;
    effect_key_operations(project, clip_ids, &plans)
}

/// Volume operations for automation plans: one `MixerWriteAutomation`
/// per plan on the placement's audio strip (`volume` lane).
pub fn volume_operations(project: &Path, plans: &[VolumePlan]) -> Vec<super::Operation> {
    plans
        .iter()
        .map(|plan| super::Operation::MixerWriteAutomation {
            project: project.into(),
            strip: plan.strip.clone(),
            lane: "volume".into(),
            points: plan.points.clone(),
        })
        .collect()
}

/// Read keyframes back from a saved `.fcproj` file: the engine stores
/// effect keys in media ticks, and `sequence.inspect` reports only
/// counts, so frame-exact readback parses the project JSON. Returns
/// `(media_ticks, plain_value)` pairs in stored order, where engine
/// value wrappers (`Float`, `Vec2`, `Bool`, `Text`, `Choice`) are
/// unwrapped to plain JSON.
pub fn read_effect_keyframes(
    project_path: &Path,
    clip: u64,
    effect: &str,
    param: &str,
) -> Result<Vec<(i64, serde_json::Value)>> {
    let bytes = std::fs::read(project_path).map_err(|e| {
        invalid(format!(
            "keyframe readback `{}` is unreadable: {e}",
            project_path.display()
        ))
    })?;
    let document: serde_json::Value = serde_json::from_slice(&bytes).map_err(|e| {
        invalid(format!(
            "keyframe readback `{}` is not JSON: {e}",
            project_path.display()
        ))
    })?;
    let items = document
        .pointer("/project/items")
        .and_then(|v| v.as_object())
        .ok_or_else(|| invalid("keyframe readback: project items are missing".to_string()))?;
    let mut found: Option<Vec<(i64, serde_json::Value)>> = None;
    for item in items.values() {
        let tracks = item
            .pointer("/kind/Sequence/video_tracks")
            .and_then(|v| v.as_array());
        let Some(tracks) = tracks else { continue };
        for track in tracks {
            let clips = track.get("items").and_then(|v| v.as_array());
            let Some(clips) = clips else { continue };
            for entry in clips {
                if entry.get("id").and_then(|v| v.as_u64()) != Some(clip) {
                    continue;
                }
                let effects = entry.get("effects").and_then(|v| v.as_array());
                let Some(effects) = effects else { continue };
                for effect_entry in effects {
                    if effect_entry.get("effect").and_then(|v| v.as_str()) != Some(effect) {
                        continue;
                    }
                    let params = effect_entry.get("params").and_then(|v| v.as_object());
                    let Some(params) = params else { continue };
                    let Some(param_entry) = params.get(param) else {
                        continue;
                    };
                    let mut keys = Vec::new();
                    if let Some(stored) = param_entry.get("keyframes").and_then(|v| v.as_array()) {
                        for key in stored {
                            let time = key.get("time").and_then(|v| v.as_i64()).ok_or_else(|| {
                                invalid(format!(
                                    "keyframe readback: clip {clip} {effect}/{param} key has no integer time"
                                ))
                            })?;
                            let value =
                                key.get("value").cloned().unwrap_or(serde_json::Value::Null);
                            keys.push((time, unwrap_engine_value(&value)));
                        }
                    }
                    found = Some(keys);
                }
            }
        }
    }
    found.ok_or_else(|| {
        invalid(format!(
            "keyframe readback: clip {clip} {effect}/{param} is not in `{}`",
            project_path.display()
        ))
    })
}

/// Unwrap one engine value wrapper (`Float`, `Vec2`, `Bool`, `Text`,
/// `Choice`) to plain JSON for comparison. Unknown shapes pass through.
fn unwrap_engine_value(value: &serde_json::Value) -> serde_json::Value {
    if let Some(object) = value.as_object() {
        if object.len() == 1 {
            if let Some(number) = object.get("Float").and_then(|v| v.as_f64()) {
                return serde_json::json!(number);
            }
            if let Some(text) = object.get("Text").and_then(|v| v.as_str()) {
                return serde_json::json!(text);
            }
            if let Some(flag) = object.get("Bool").and_then(|v| v.as_bool()) {
                return serde_json::json!(flag);
            }
            if let Some(choice) = object.get("Choice").and_then(|v| v.as_u64()) {
                return serde_json::json!(choice);
            }
            if let Some(vec2) = object.get("Vec2") {
                let x = vec2.get("x").and_then(|v| v.as_f64());
                let y = vec2.get("y").and_then(|v| v.as_f64());
                if let (Some(x), Some(y)) = (x, y) {
                    return serde_json::json!([x, y]);
                }
            }
        }
    }
    value.clone()
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

/// Refuse before the engine runs when a referenced media file is absent,
/// and when an overlay or card file uses a codec the pinned engine cannot
/// draw.
///
/// Measured on the pinned revision: ProRes with alpha composites over the
/// base (transparent pixels show the picture, opaque pixels cover it), while
/// QuickTime Animation (`qtrle`) and PNG-in-.mov decode to nothing - the
/// base shows through as if the overlay were never placed. A caption file
/// in `qtrle` placed without this gate ships a reel with no captions and no
/// error, which is exactly what the verified projection exists to prevent.
/// Base picture (`a_roll`/`b_roll`) is never gated: camera codecs are the
/// engine's own business and outside this lane's measurements.
pub fn verify_media(
    media_dir: &Path,
    projection: &Projection,
) -> Result<BTreeMap<String, PathBuf>> {
    verify_media_with_probe(media_dir, projection, &ffprobe_video_codec)
}

/// [`verify_media`] with an injectable codec probe (tests stub the probe;
/// production reads `ffprobe`). `probe` sees each overlay/card asset path
/// and reports `(codec_name, pix_fmt)` of its first video stream, or `None`
/// when the file carries no decodable video description.
pub fn verify_media_with_probe(
    media_dir: &Path,
    projection: &Projection,
    probe: &dyn Fn(&Path) -> Option<(String, String)>,
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
    let mut overlay_assets: BTreeMap<&str, &str> = BTreeMap::new();
    for placement in &projection.placements {
        if matches!(
            placement.role.as_str(),
            "subtitle_overlay" | "motion_graphic" | "frame_overlay" | "post_header" | "end_card"
        ) {
            overlay_assets.insert(placement.asset_id.as_str(), placement.role.as_str());
        }
    }
    for (asset_id, role) in overlay_assets {
        let path = resolved.get(asset_id).ok_or_else(|| {
            invalid(format!(
                "placement names asset `{asset_id}` with no import to resolve it from"
            ))
        })?;
        match probe(path) {
            None => {
                return Err(unsupported(
                    "overlay_codec_unverified",
                    format!(
                        "asset `{asset_id}` ({role}) resolves to `{}` but ffprobe reports no video codec for it; refusing an overlay the engine may not draw - probe the file or transcode it to ProRes 4444",
                        path.display(),
                    ),
                ));
            }
            Some((codec, pix_fmt)) => {
                overlay_codec_compliant(role, asset_id, &codec, &pix_fmt)?;
            }
        }
    }
    Ok(resolved)
}

/// Whether the pinned engine draws an overlay file with this video codec
/// and pixel format. Pure and measured: ProRes with alpha composites;
/// `qtrle` and PNG-in-.mov decode to nothing (the base shows through);
/// anything else is unmeasured and refuses rather than ships unseen.
/// Full-frame `end_card`s need no alpha (opaque cards cover the tail), so
/// they accept the measured opaque decodes too; compositing overlays must
/// carry alpha or they would box over the picture.
pub fn overlay_codec_compliant(
    role: &str,
    asset_id: &str,
    codec: &str,
    pix_fmt: &str,
) -> Result<()> {
    let alpha = pix_fmt.contains('a');
    let ok = match role {
        "subtitle_overlay" | "motion_graphic" | "frame_overlay" | "post_header" => {
            codec == "prores" && alpha
        }
        "end_card" => codec == "prores" || codec == "h264",
        _ => {
            return Err(invalid(format!(
                "asset `{asset_id}` with role `{role}` is not an overlay or card; the codec gate covers overlay roles only"
            )));
        }
    };
    if ok {
        return Ok(());
    }
    Err(unsupported(
        "overlay_codec_not_supported",
        format!(
            "asset `{asset_id}` ({role}) probes as {codec}/{pix_fmt}, which the pinned engine does not draw (measured: ProRes with alpha composites, `qtrle` and PNG decode to nothing); transcode the overlay to ProRes 4444{0} first",
            if role == "end_card" {
                " or H264"
            } else {
                ""
            },
        ),
    ))
}

/// `(codec_name, pix_fmt)` of a file's first video stream via `ffprobe`,
/// or `None` when ffprobe is absent or reports no video stream.
fn ffprobe_video_codec(path: &Path) -> Option<(String, String)> {
    let output = std::process::Command::new("ffprobe")
        .args([
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "json",
        ])
        .arg(path)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).ok()?;
    let stream = value.get("streams")?.as_array()?.first()?;
    Some((
        stream.get("codec_name")?.as_str()?.to_owned(),
        stream.get("pix_fmt")?.as_str()?.to_owned(),
    ))
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
                video: !is_stem_role(&placement.role),
                audio: is_stem_role(&placement.role)
                    || matches!(placement.role.as_str(), "a_roll" | "b_roll"),
                frame: placement.frame,
                source_in_ticks: placement.source_in_ticks,
                duration_ticks: placement.duration_ticks,
            },
        ));
    }
    Ok(operations)
}

/// Add one generated final-frame still for each declared source shortfall.
/// The Python bridge only extracts the image; all placement math and policy
/// choices remain in this native adapter.
pub fn expand_span_freezes(
    projection: &Projection,
    still_paths: &BTreeMap<String, PathBuf>,
) -> Result<Projection> {
    if projection.span_shortfalls.is_empty() {
        if !still_paths.is_empty() {
            return Err(invalid(
                "freeze stills were supplied for a projection with no shortfalls".into(),
            ));
        }
        return Ok(projection.clone());
    }
    if projection.span_shortfall_policy != SpanShortfallPolicy::FreezeLastFrame {
        return Err(unsupported(
            "span_freeze_policy_mismatch",
            "still images were supplied while the projection policy is not freeze_last_frame"
                .into(),
        ));
    }
    if still_paths.len() != projection.span_shortfalls.len() {
        return Err(invalid(format!(
            "{} shortfalls do not match {} extracted stills",
            projection.span_shortfalls.len(),
            still_paths.len()
        )));
    }
    let shortfall_ids: BTreeSet<&str> = projection
        .span_shortfalls
        .iter()
        .map(|row| row.clip_use_id.as_str())
        .collect();
    if still_paths
        .keys()
        .any(|id| !shortfall_ids.contains(id.as_str()))
    {
        return Err(invalid("freeze still map names an unknown clip use".into()));
    }

    let mut expanded = projection.clone();
    expanded.generated_span_freezes.clear();
    for shortfall in &projection.span_shortfalls {
        let path = still_paths.get(&shortfall.clip_use_id).ok_or_else(|| {
            invalid(format!(
                "no extracted freeze still for `{}`",
                shortfall.clip_use_id
            ))
        })?;
        let index = expanded
            .placements
            .iter()
            .position(|p| p.clip_use_id == shortfall.clip_use_id)
            .ok_or_else(|| invalid(format!("no placement for `{}`", shortfall.clip_use_id)))?;
        let original = expanded.placements[index].clone();
        let timeline_frames = shortfall.timeline_frames;
        let source_frames = shortfall.source_frames;
        let missing_frames = shortfall.missing_frames;
        if timeline_frames <= 0
            || source_frames <= 0
            || missing_frames <= 0
            || source_frames + missing_frames != timeline_frames
        {
            return Err(invalid(format!(
                "invalid freeze frame counts for `{}`",
                shortfall.clip_use_id
            )));
        }
        let (engine_per_frame, engine_remainder) = (
            original.duration_ticks / timeline_frames,
            original.duration_ticks % timeline_frames,
        );
        let model_span = original.timeline_end - original.timeline_start;
        let (model_per_frame, model_remainder) =
            (model_span / timeline_frames, model_span % timeline_frames);
        if engine_remainder != 0
            || model_remainder != 0
            || engine_per_frame <= 0
            || model_per_frame <= 0
        {
            return Err(unsupported(
                "span_freeze_inexact_frame",
                format!(
                    "clip use `{}` does not land on whole frames",
                    shortfall.clip_use_id
                ),
            ));
        }
        let short_engine_span = engine_per_frame * source_frames;
        let short_model_span = model_per_frame * source_frames;
        let hold_track = if original.role == "a_roll" {
            // Keep linked dialogue at full duration. Base-picture tracks do
            // not overlap, so V2 is clear while A-roll owns V1.
            "V2".to_owned()
        } else {
            expanded.placements[index].duration_ticks = short_engine_span;
            expanded.placements[index].timeline_end = original.timeline_start + short_model_span;
            original.track.clone()
        };
        let hold_start_frame = original.frame + source_frames;
        let hold_start_tick = original.timeline_start + short_model_span;
        let asset_id = format!("freeze:{}", shortfall.clip_use_id);
        let hold = Placement {
            clip_use_id: shortfall.clip_use_id.clone(),
            asset_id: asset_id.clone(),
            role: original.role.clone(),
            track: hold_track.clone(),
            audio_track: None,
            frame: hold_start_frame,
            source_in_ticks: 0,
            duration_ticks: engine_per_frame * missing_frames,
            timeline_start: hold_start_tick,
            timeline_end: hold_start_tick + model_per_frame * missing_frames,
        };
        expanded.placements.insert(index + 1, hold);
        expanded.imports.push(AssetImport {
            asset_id: asset_id.clone(),
            uri: path.to_string_lossy().into_owned(),
        });
        expanded.generated_span_freezes.push(SpanFreeze {
            clip_use_id: shortfall.clip_use_id.clone(),
            asset_id,
            uri: path.to_string_lossy().into_owned(),
            source_frame: source_frames - 1,
            track: hold_track,
            hold_start_frame,
            held_frames: missing_frames,
        });
    }
    expanded.imports.sort_by(|a, b| a.asset_id.cmp(&b.asset_id));
    Ok(expanded)
}

/// Highest video track number a projection addresses (`V3` -> 3). Fresh
/// FilmCraft projects carry three video tracks; anything above needs
/// [`add_tracks_operation`] before the first place, or the engine refuses
/// the place with `no track V4`.
pub fn required_video_tracks(projection: &Projection) -> u32 {
    projection
        .placements
        .iter()
        .filter_map(|p| p.track.strip_prefix('V')?.parse::<u32>().ok())
        .max()
        .unwrap_or(0)
}

/// Video tracks a fresh engine project already carries. `CreateProject`
/// takes no track counts, so every project starts here unless the caller
/// pre-created it wider (as the dimensions workaround already does).
pub const DEFAULT_VIDEO_TRACKS: u32 = 3;

/// The `sequence.addTracks` operation provisioning a projection's overlay
/// tracks, or `None` when the default three video tracks suffice. Drive it
/// after create and before import: one call per project, never once per
/// placement (the command adds tracks; repeating it stacks empties).
pub fn add_tracks_operation(project: &Path, projection: &Projection) -> Option<Operation> {
    let needed = required_video_tracks(projection).saturating_sub(DEFAULT_VIDEO_TRACKS);
    if needed == 0 {
        return None;
    }
    Some(Operation::AddTracks {
        project: project.into(),
        video: needed,
        audio: 0,
    })
}
/// Dual-mono channel mapping for discrete-mono sources.
///
/// Measured at the pinned revision: a source whose audio streams are discrete
/// mono (the reel's 4x-mono MXF: dialogue, silence, ambience, ambience)
/// imports grouped into stereo pairs (`[[0,1],[2,3]]`), and `timeline.place`
/// carries the first pair onto the stereo master verbatim - dialogue left,
/// digital silence right - while Resolve renders the same source dual-mono
/// (dialogue on both channels). The engine expresses the Resolve presentation
/// through `clip.audioChannels`: `stereo [[c,c]]` maps mono channel `c` to
/// both outputs (measured: per-channel mean volumes match to 0.1 dB), and the
/// placement keeps its two-clip video-plus-audio shape.
///
/// Pure: `current_mapping` is the `engine_response` of an [`Operation::AudioChannels`]
/// query over the imported items. Returns `Some` exactly when every audio
/// stream the model names for `asset_id` declares `metadata.channels == 1`
/// (discrete mono, so no stereo image exists to preserve) and the engine's
/// current mapping is not already that dual-mono single group; the remap keeps
/// the first channel the placement would carry and duplicates it. Returns
/// `None` for video-only assets, true-stereo or unknown-channel sources (the
/// engine behavior stands - nothing is guessed), and sources the engine
/// already maps dual-mono. A mapping document that names no entry for `item`
/// or no channel groups refuses rather than guessing a channel index.
///
/// Call it between import and placement: query, plan, execute the returned
/// operation when present, then place as usual.
pub fn plan_dual_mono_mapping(
    model: &ProjectModel,
    asset_id: &str,
    item: u64,
    project: &Path,
    current_mapping: &serde_json::Value,
) -> Result<Option<Operation>> {
    let audio: Vec<_> = model
        .streams
        .iter()
        .filter(|s| {
            s.asset_id.0 == asset_id
                && s.metadata.get("codec_type").and_then(|v| v.as_str()) == Some("audio")
        })
        .collect();
    if audio.is_empty() {
        return Ok(None);
    }
    if audio
        .iter()
        .any(|s| s.metadata.get("channels").and_then(|v| v.as_u64()) != Some(1))
    {
        return Ok(None);
    }
    let entries = current_mapping
        .get("items")
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            invalid(format!(
                "channel-mapping query for asset `{asset_id}` answers no `items` array; pass the `AudioChannels` query receipt straight through"
            ))
        })?;
    let entry = entries
        .iter()
        .find(|e| e.get("item").and_then(|v| v.as_u64()) == Some(item))
        .ok_or_else(|| {
            invalid(format!(
                "channel-mapping query names no entry for imported item {item} (asset `{asset_id}`); refusing to guess its channels"
            ))
        })?;
    let groups: Vec<Vec<u64>> = entry
        .get("clips")
        .and_then(|v| v.as_array())
        .ok_or_else(|| {
            invalid(format!(
                "channel-mapping entry for item {item} (asset `{asset_id}`) names no `clips` groups"
            ))
        })?
        .iter()
        .map(|g| {
            g.as_array()
                .ok_or_else(|| {
                    invalid(format!(
                        "channel-mapping entry for item {item} (asset `{asset_id}`) carries a non-array channel group"
                    ))
                })?
                .iter()
                .map(|c| {
                    c.as_u64().ok_or_else(|| {
                        invalid(format!(
                            "channel-mapping entry for item {item} (asset `{asset_id}`) carries a non-integer channel"
                        ))
                    })
                })
                .collect::<Result<Vec<u64>>>()
        })
        .collect::<Result<Vec<Vec<u64>>>>()?;
    let format = entry.get("format").and_then(|v| v.as_str()).unwrap_or("");
    if groups.len() == 1
        && groups[0].len() == 2
        && groups[0][0] == groups[0][1]
        && format.eq_ignore_ascii_case("stereo")
    {
        return Ok(None);
    }
    let channel = groups
        .first()
        .and_then(|g| g.first())
        .copied()
        .ok_or_else(|| {
            invalid(format!(
                "channel-mapping entry for item {item} (asset `{asset_id}`) names no channels to duplicate"
            ))
        })?;
    let channel = u32::try_from(channel).map_err(|_| {
        invalid(format!(
            "channel-mapping entry for item {item} (asset `{asset_id}`) names channel {channel}, out of range"
        ))
    })?;
    Ok(Some(Operation::AudioChannels {
        project: project.into(),
        items: vec![item],
        format: Some(AudioChannelFormat::Stereo),
        clips: Some(vec![vec![channel, channel]]),
    }))
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
        map.entry(placement.clip_use_id.clone())
            .or_insert_with(Vec::new)
            .extend(clips.iter().copied());
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
            // The compositor path rides alongside the encoder choice and is
            // recorded with it on the receipt. `auto` plans into an
            // `Operation::Export` and executes since 0800a8b (the exec path
            // falls back to the CPU reference path there, byte-identical to
            // `off`; measured 2026-10-11) - `off` stays the default by the
            // fidelity rule, and switching it on stays the captain's call.
            let gpu_rendering = match after
                .get("gpu")
                .and_then(|v| v.as_str())
                .unwrap_or("off")
            {
                "off" => GpuRendering::Off,
                "auto" => GpuRendering::Auto,
                other => {
                    return Err(invalid(format!(
                        "edit `{}` names unknown gpu rendering `{other}`",
                        edit.id.0
                    )));
                }
            };
            let path = PathBuf::from(after_str(edit, after, "path")?);
            // Optional bitrate pair, H264 only: absent takes Ren's delivery
            // default in the driver (`DEFAULT_H264_BITRATE_KBPS` /
            // `DEFAULT_BITRATE_MODE`); the driver refuses zero, the absurd
            // and non-H264 pairings, so anything parsed here is sendable.
            let bitrate_kbps = match after.get("bitrate_kbps") {
                None => None,
                Some(value) => Some(u32::try_from(value.as_u64().ok_or_else(|| {
                    invalid(format!(
                        "edit `{}` needs `after.bitrate_kbps` as a non-negative integer of kbps",
                        edit.id.0
                    ))
                })?)
                .map_err(|_| {
                    invalid(format!(
                        "edit `{}` names a bitrate above u32 kbps",
                        edit.id.0
                    ))
                })?),
            };
            let bitrate_mode = match after.get("bitrate_mode").and_then(|v| v.as_str()) {
                None => None,
                Some("cbr") => Some(BitrateMode::Cbr),
                Some("vbr1Pass") => Some(BitrateMode::Vbr1Pass),
                Some("vbr2Pass") => Some(BitrateMode::Vbr2Pass),
                Some(other) => {
                    return Err(invalid(format!(
                        "edit `{}` names unknown bitrate mode `{other}`; the pinned engine accepts only `cbr`, `vbr1Pass` and `vbr2Pass`",
                        edit.id.0
                    )));
                }
            };
            Ok(Operation::Export {
                project,
                path,
                format,
                hardware_encoder,
                gpu_rendering,
                bitrate_kbps,
                bitrate_mode,
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
            // Gain needs a strip behind the use: the placement carries it
            // (`A1` for linked dialogue, the dealt stem strip for bed/SFX),
            // and a use with no audio stream has nothing to gain.
            placement.audio_track.clone().ok_or_else(|| {
                unsupported(
                    "no_audio_for_gain",
                    format!(
                        "edit `{}` gains `{target_id}`, which carries no audio stream; there is no clip gain to drive",
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
            // One id: the engine follows clip links (measured: gaining a
            // linked video item writes its audio item's `gainDb`), so the
            // use's first reconciled id lands the level on the sound
            // whether the use is linked dialogue or a stem placement.
            let clips = context.clip_ids.get(target_id).cloned().ok_or_else(|| {
                invalid(format!(
                    "edit `{}` targets `{target_id}` which lost its reconciled engine clip ids",
                    edit.id.0
                ))
            })?;
            let clip = *clips.first().ok_or_else(|| {
                invalid(format!(
                    "edit `{}` targets `{target_id}` which reconciled to no engine clips",
                    edit.id.0
                ))
            })?;
            Ok(vec![Operation::ClipAudioGain {
                project,
                clips: vec![clip],
                gain_db,
            }])
        }
        "audio.pan" => {
            let (target_id, _) = ren_edit_target(edit, context)?;
            Err(unsupported(
                "audio_pan_not_projected",
                format!(
                    "edit `{}` pans `{target_id}`; Ren's mix has no measured pan mapping - Resolve's OTIO audio surface is volume only (`library/tools/otio_mix.py`), so a pan would render differently per engine; refusing rather than splitting the mix",
                    edit.id.0
                ),
            ))
        }
        "audio.fade" | "audio.automation" => {
            let (target_id, _) = ren_edit_target(edit, context)?;
            let after = after_object(edit)?;
            // The one parameter the keyframed lane will ever drive is
            // volume; anything else has no spelling on either path
            // (mirrors the OTIO volume-only rule).
            if let Some(param) = after.get("param").and_then(|v| v.as_str()) {
                if param != "volume" {
                    return Err(invalid(format!(
                        "edit `{}` automates parameter `{param}` on `{target_id}`; the audio surface is volume only",
                        edit.id.0
                    )));
                }
            }
            Err(unsupported(
                "audio_keyframes_require_volume_automation",
                format!(
                    "edit `{}` times keyframes on `{target_id}`; keyframed strip volume lands with the volume-automation op (audio.volume effects over `mixer.setKeyframe`), which this adapter plans once that lane merges - static `audio.gain` is the only keyframe-free level until then",
                    edit.id.0
                ),
            ))
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
        // The equivalence counterpart of the `audio.gain` plan: gain is
        // span-neutral, so the mutation records the edit in the model's
        // log (the retime pattern) rather than moving a use. Qualification
        // compares the engine's inspected `gainDb` against these logged
        // levels, and re-planning the logged edit over the mutated model
        // must plan the identical `ClipAudioGain`.
        "audio.gain" => {
            let [target] = edit.target_ids.as_slice() else {
                return Err(invalid(format!(
                    "edit `{}` must target exactly one clip use; it targets {}",
                    edit.id.0,
                    edit.target_ids.len()
                )));
            };
            let clip_use = clip_use_by_id(model, target, edit)?;
            if !has_audio_stream(model, clip_use.asset_id.0.as_str()) {
                return Err(unsupported(
                    "no_audio_for_gain",
                    format!(
                        "edit `{}` gains `{target}`, which carries no audio stream; there is no clip gain to record",
                        edit.id.0
                    ),
                ));
            }
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
            let mut updated = model.clone();
            updated.edits.push(edit.clone());
            validate_project_model(&updated)
                .map_err(|detail| invalid(format!("gain model rejected: {detail}")))?;
            Ok(updated)
        }
        other => Err(unsupported(
            "unsupported_edit_operation",
            format!(
                "edit `{}` names operation `{other}`; the model mutation maps split, trim, move, delete, ripple, link, constant retime and clip gain",
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
/// the spine placements (`a_roll`, `b_roll`, `end_card`) in timeline order
/// from render tick zero; each placement maps onto its render span and names
/// its source clip use. Compositing overlays draw onto the base frames
/// beneath them and add no render time, so they contribute no segments.
/// Placements with a logged constant retime map [`MappingKind::Retimed`] with
/// the exact playback speed; everything else maps one-to-one.
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
    for placement in projection
        .placements
        .iter()
        .filter(|p| is_spine_role(&p.role))
    {
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
    fn only_linked_person_segmentations_covering_clip_start_clear_mask_loss() {
        let mut model = minimal_model();
        model.clip_uses[1]
            .entity_links
            .push(ren_protocol::project_model::EntityId("person-a".into()));
        let placement = plan_projection(&model).unwrap().placements[0].clone();
        let mask = ren_protocol::project_model::Mask {
            id: ren_protocol::project_model::MaskId("mask-a".into()),
            entity_id: ren_protocol::project_model::EntityId("person-a".into()),
            interval: Interval {
                clock_id: ren_protocol::project_model::ClockId("clock-source".into()),
                start: 0,
                end: 24,
            },
            representation: serde_json::json!({"kind":"region_rle"}),
            source_model: "test-region-mask".into(),
            provenance_id: ren_protocol::project_model::ProvenanceId("prov-1".into()),
        };
        assert!(mask_has_native_target(&model, &mask, &[placement.clone()]));

        let mut face_box = mask.clone();
        face_box.representation = serde_json::json!({"kind":"face_bbox_track"});
        assert!(!mask_has_native_target(
            &model,
            &face_box,
            &[placement.clone()]
        ));

        let mut partial = mask;
        partial.interval.start = 1;
        assert!(!mask_has_native_target(&model, &partial, &[placement]));
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
        // The fixture's tint effect is EffectCraft-owned saturation: it
        // stays there rather than duplicating here.
        assert!(codes.contains(&"effect_owned_by_effectcraft"));
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
    fn default_policy_records_short_sources_and_unknown_roles_refuse() {
        let mut model = minimal_model();
        model.clip_uses[0].source_interval.end = 4;
        let projection = plan_projection(&model).unwrap();
        assert_eq!(
            projection.span_shortfall_policy,
            SpanShortfallPolicy::FreezeLastFrame
        );
        assert_eq!(projection.span_shortfalls.len(), 1);
        let refused =
            plan_projection_with_shortfall_policy(&model, SpanShortfallPolicy::Refuse).unwrap_err();
        assert!(matches!(refused, AdapterError::Unsupported { ref code, .. }
            if code == "source_too_short"));

        let mut model = minimal_model();
        model.clip_uses[0].role = "interview".into();
        let err = plan_projection(&model).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "unsupported_role")
        );
    }

    fn model_with_uses(uses: serde_json::Value) -> ProjectModel {
        let document = serde_json::json!({
            "id": "adapter-overlay-unit",
            "schema_version": 1,
            "timebase_policy": "rational_frames",
            "clocks": [
                {"id": "clock-source", "domain": {"kind": "source", "asset_id": "asset-a", "stream_id": "stream-video"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
                {"id": "clock-timeline", "domain": {"kind": "project"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
                {"id": "clock-render", "domain": {"kind": "render", "render_id": "render-1"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}}
            ],
            "assets": [
                {"id": "asset-a", "kind": "video", "uri": "media/a.mp4", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]},
                {"id": "asset-b", "kind": "video", "uri": "media/b.mov", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]}
            ],
            "streams": [
                {"id": "stream-video", "asset_id": "asset-a", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}},
                {"id": "stream-audio", "asset_id": "asset-a", "stream_index": 1, "clock_id": "clock-source", "metadata": {"codec_type": "audio"}}
            ],
            "entities": [], "observations": [], "identity_links": [], "masks": [],
            "object_classes": [], "tracks": [], "relations": [], "intents": [],
            "clip_uses": uses,
            "edits": [], "effects": [], "renders": [], "frame_maps": [],
            "nle_bindings": [], "receipts": [],
            "provenance": [{"id": "prov-1", "producer": "unit", "producer_version": null, "source_digest": null, "recorded_at": null}]
        });
        decode_project_model(&document.to_string()).expect("overlay unit model decodes")
    }

    fn clip_use(
        id: &str,
        asset: &str,
        role: &str,
        src: (i64, i64),
        tl: (i64, i64),
    ) -> serde_json::Value {
        serde_json::json!({
            "id": id, "asset_id": asset,
            "source_interval": {"clock_id": "clock-source", "start": src.0, "end": src.1},
            "timeline_interval": {"clock_id": "clock-timeline", "start": tl.0, "end": tl.1},
            "entity_links": [], "role": role, "intent_id": null
        })
    }

    #[test]
    fn overlay_roles_project_to_fixed_tracks_without_audio() {
        let model = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-b", "asset-b", "b_roll", (0, 24), (24, 48)),
            clip_use("use-sub", "asset-b", "subtitle_overlay", (0, 48), (0, 48)),
            clip_use("use-mg", "asset-b", "motion_graphic", (0, 24), (0, 24)),
            clip_use("use-frame", "asset-b", "frame_overlay", (0, 48), (0, 48)),
            clip_use("use-card", "asset-b", "end_card", (0, 12), (48, 60)),
        ]));
        let projection = plan_projection(&model).unwrap();
        assert_eq!(projection.adapter_version, ADAPTER_VERSION);
        let tracks: Vec<(&str, &str, &Option<String>)> = projection
            .placements
            .iter()
            .map(|p| (p.clip_use_id.as_str(), p.track.as_str(), &p.audio_track))
            .collect();
        // Projection order is (timeline_start, id): the three uses opening
        // at frame 0 sort by id under the V1 base.
        assert_eq!(
            tracks,
            vec![
                ("use-a", "V1", &Some("A1".into())),
                ("use-frame", "V5", &None),
                ("use-mg", "V4", &None),
                ("use-sub", "V3", &None),
                ("use-b", "V2", &None),
                ("use-card", "V1", &None),
            ]
        );
        // Roles ride the placements for the execution boundary to read.
        assert!(projection.placements.iter().all(|p| !p.role.is_empty()));
        assert_eq!(projection.placements[0].role, "a_roll");
        assert_eq!(projection.placements[3].role, "subtitle_overlay");
        // The base asset's audio stream reaches A1; overlay and card files
        // never wire audio even when the asset carries an audio stream.
        let voiced = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use(
                "use-voice-sub",
                "asset-a",
                "subtitle_overlay",
                (0, 24),
                (0, 24)
            ),
            clip_use("use-voice-card", "asset-a", "end_card", (24, 36), (24, 36)),
        ]));
        let voiced = plan_projection(&voiced).unwrap();
        let audio: Vec<(&str, &Option<String>)> = voiced
            .placements
            .iter()
            .map(|p| (p.clip_use_id.as_str(), &p.audio_track))
            .collect();
        assert_eq!(
            audio,
            vec![
                ("use-a", &Some("A1".into())),
                ("use-voice-sub", &None),
                ("use-voice-card", &None),
            ]
        );
        let operations = placement_operations(
            std::path::Path::new("reel.fcproj"),
            &std::collections::BTreeMap::from([
                ("asset-a".to_owned(), 1),
                ("asset-b".to_owned(), 2),
            ]),
            &voiced,
        )
        .unwrap();
        let placement_flags: Vec<(&str, bool, bool)> = operations
            .iter()
            .map(|(id, operation)| match operation {
                Operation::Place { video, audio, .. } => (id.as_str(), *video, *audio),
                _ => unreachable!("placement planner emits Place operations"),
            })
            .collect();
        assert_eq!(
            placement_flags,
            vec![
                ("use-a", true, true),
                ("use-voice-sub", true, false),
                ("use-voice-card", true, false),
            ]
        );
        // Compositing overlays carry the positioning loss by name; the
        // full-frame card and the base do not.
        assert_eq!(projection.losses.len(), 1);
        assert_eq!(projection.losses[0].code, "overlay_unpositioned");
        for name in ["use-frame", "use-mg", "use-sub"] {
            assert!(
                projection.losses[0].detail.contains(name),
                "loss must name {name}: {}",
                projection.losses[0].detail
            );
        }
        assert!(!projection.losses[0].detail.contains("use-card"));
    }

    #[test]
    fn post_header_uses_its_own_compositing_track_and_grows_the_sequence() {
        let model = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 48), (0, 48)),
            clip_use("use-header", "asset-b", "post_header", (0, 48), (0, 48)),
        ]));
        let projection = plan_projection(&model).unwrap();
        let header = projection
            .placements
            .iter()
            .find(|placement| placement.clip_use_id == "use-header")
            .unwrap();
        assert_eq!(header.track, "V6");
        assert_eq!(required_video_tracks(&projection), 6);
        match add_tracks_operation(Path::new("project"), &projection) {
            Some(Operation::AddTracks { video, audio, .. }) => {
                assert_eq!((video, audio), (3, 0));
            }
            other => panic!("post_header requires three tracks beyond the default, got {other:?}"),
        }
    }

    #[test]
    fn overlay_overlap_discipline() {
        // Same-track overlay stacking refuses, even non-adjacent in sort
        // order: the V1 pair below has the V3 subtitle between them.
        let stacked = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 20), (0, 20)),
            clip_use("use-sub", "asset-b", "subtitle_overlay", (0, 5), (5, 10)),
            clip_use("use-a2", "asset-b", "a_roll", (0, 5), (10, 15)),
        ]));
        let err = plan_projection(&stacked).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "timeline_overlap"),
            "non-adjacent same-track overlap must refuse, got {err:?}"
        );
        // Two subtitles over the same span refuse on their shared V3.
        let subs = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-s1", "asset-b", "subtitle_overlay", (0, 12), (0, 12)),
            clip_use("use-s2", "asset-b", "subtitle_overlay", (0, 12), (6, 18)),
        ]));
        let err = plan_projection(&subs).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "timeline_overlap"),
            "same-track subtitle stacking must refuse, got {err:?}"
        );
        // Base picture stays sequential across tracks: B-roll cannot cover
        // A-roll, and a card cannot overlap the body.
        for (id, role, tl) in [
            ("use-b", "b_roll", (10, 15)),
            ("use-card", "end_card", (10, 20)),
        ] {
            let covered = model_with_uses(serde_json::json!([
                clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
                clip_use(id, "asset-b", role, (0, 10), tl),
            ]));
            let err = plan_projection(&covered).unwrap_err();
            assert!(
                matches!(err, AdapterError::Unsupported { ref code, .. } if code == "timeline_overlap"),
                "{role} overlapping the base must refuse, got {err:?}"
            );
        }
        // Overlays across tracks compose freely over the base and each other.
        let composed = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-sub", "asset-b", "subtitle_overlay", (0, 24), (0, 24)),
            clip_use("use-mg", "asset-b", "motion_graphic", (0, 24), (12, 24)),
            clip_use("use-frame", "asset-b", "frame_overlay", (0, 24), (0, 24)),
        ]));
        assert_eq!(plan_projection(&composed).unwrap().placements.len(), 4);
    }

    #[test]
    fn overlays_without_a_spine_refuse() {
        let floating = model_with_uses(serde_json::json!([clip_use(
            "use-sub",
            "asset-b",
            "subtitle_overlay",
            (0, 24),
            (0, 24)
        ),]));
        let err = plan_projection(&floating).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "no_base_spine"),
            "overlays over nothing must refuse, got {err:?}"
        );
        // A lone end card is a spine of its own and projects.
        let card = model_with_uses(serde_json::json!([clip_use(
            "use-card",
            "asset-b",
            "end_card",
            (0, 72),
            (0, 72)
        ),]));
        let projection = plan_projection(&card).unwrap();
        assert_eq!(projection.placements.len(), 1);
        assert_eq!(projection.placements[0].track, "V1");
    }

    #[test]
    fn overlay_short_sources_are_recorded_like_base() {
        let short = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-sub", "asset-b", "subtitle_overlay", (0, 10), (0, 70)),
        ]));
        let projection = plan_projection(&short).unwrap();
        assert_eq!(projection.span_shortfalls.len(), 1);
        assert_eq!(projection.span_shortfalls[0].clip_use_id, "use-sub");
    }

    #[test]
    fn reel_22_craig_cut_source_span_regression() {
        // Native trial, Reel 22: `use-craig` holds source 87030..87284
        // (254 frames, Resolve's raw `GetSourceEndFrame` readback) for a
        // 255-frame timeline use, and the as-recorded projection refused as
        // `source_too_short` until the source end was hand-patched +1 to
        // the played end 87285. The refusal below locks the fail-closed
        // half; the corrected projection locks the pipelineable half.
        // Clocks tick per frame (24 tps at 24 fps), as in the trial model.
        let uses = |source_end: i64| {
            serde_json::json!([
                clip_use(
                    "use-craig",
                    "asset-a",
                    "a_roll",
                    (87030, source_end),
                    (0, 255)
                ),
                clip_use(
                    "use-akshita",
                    "asset-b",
                    "a_roll",
                    (87375, 88303),
                    (255, 1183)
                ),
            ])
        };
        let err = plan_projection_with_shortfall_policy(
            &model_with_uses(uses(87284)),
            SpanShortfallPolicy::Refuse,
        )
        .unwrap_err();
        match &err {
            AdapterError::Unsupported { code, detail } => {
                assert_eq!(code, "source_too_short");
                assert!(
                    detail.contains("use-craig"),
                    "the refusal must name the short use: {detail}"
                );
            }
            other => panic!("as-recorded Craig cut must refuse, got {other:?}"),
        }
        let fixed = plan_projection(&model_with_uses(uses(87285))).unwrap();
        assert_eq!(fixed.placements.len(), 2);
        assert_eq!(fixed.placements[0].clip_use_id, "use-craig");
        assert_eq!(fixed.placements[0].track, "V1");
        assert_eq!(fixed.placements[0].frame, 0);
        assert_eq!(
            fixed.placements[0].duration_ticks,
            255 * ENGINE_TICKS_PER_SECOND / 24
        );
        assert!(fixed.losses.is_empty());
    }

    #[test]
    fn reel_22_one_frame_shortfall_requires_and_records_freeze_policy() {
        let model = model_with_uses(serde_json::json!([
            clip_use("use-craig", "asset-a", "a_roll", (87030, 87284), (0, 255)),
            clip_use(
                "use-akshita",
                "asset-b",
                "a_roll",
                (87375, 88303),
                (255, 1183)
            ),
        ]));
        let refused =
            plan_projection_with_shortfall_policy(&model, SpanShortfallPolicy::Refuse).unwrap_err();
        assert!(matches!(refused, AdapterError::Unsupported { ref code, .. }
            if code == "source_too_short"));

        let frozen = plan_projection(&model).unwrap();
        assert_eq!(
            frozen.span_shortfall_policy,
            SpanShortfallPolicy::FreezeLastFrame
        );
        assert_eq!(frozen.placements[0].clip_use_id, "use-craig");
        assert_eq!(
            frozen.placements[0].duration_ticks,
            255 * ENGINE_TICKS_PER_SECOND / 24
        );
        assert_eq!(
            frozen.span_shortfalls,
            vec![SpanShortfall {
                clip_use_id: "use-craig".into(),
                source_frames: 254,
                timeline_frames: 255,
                missing_frames: 1,
            }]
        );
        assert!(
            projection_json(&frozen).contains("\"span_shortfall_policy\":\"freeze_last_frame\"")
        );

        let temp = tempfile::tempdir().unwrap();
        let still = temp.path().join("craig-last.png");
        std::fs::write(&still, b"fixture still").unwrap();
        let expanded = expand_span_freezes(
            &frozen,
            &BTreeMap::from([("use-craig".into(), still.clone())]),
        )
        .unwrap();
        assert_eq!(expanded.placements.len(), 3);
        assert_eq!(
            expanded.placements[0].duration_ticks,
            frozen.placements[0].duration_ticks
        );
        assert_eq!(expanded.placements[1].frame, 254);
        assert_eq!(expanded.placements[1].track, "V2");
        assert_eq!(
            expanded.placements[1].duration_ticks,
            ENGINE_TICKS_PER_SECOND / 24
        );
        assert_eq!(expanded.generated_span_freezes[0].source_frame, 253);

        let mg_model = model_with_uses(serde_json::json!([
            clip_use("use-base", "asset-b", "a_roll", (0, 84), (0, 84)),
            clip_use("use-mg", "asset-a", "motion_graphic", (0, 83), (0, 84)),
        ]));
        let mg_projection = plan_projection(&mg_model).unwrap();
        let mg_still = temp.path().join("mg-last.png");
        std::fs::write(&mg_still, b"fixture still").unwrap();
        let mg_expanded = expand_span_freezes(
            &mg_projection,
            &BTreeMap::from([("use-mg".into(), mg_still)]),
        )
        .unwrap();
        assert_eq!(mg_expanded.placements.len(), 3);
        assert_eq!(
            mg_expanded.placements[1].duration_ticks,
            83 * ENGINE_TICKS_PER_SECOND / 24
        );
        assert_eq!(mg_expanded.placements[2].frame, 83);
        assert_eq!(mg_expanded.placements[2].track, "V4");
        assert_eq!(mg_expanded.generated_span_freezes[0].held_frames, 1);
    }

    #[test]
    fn frame_map_packs_the_spine_and_skips_overlays() {
        let model = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-sub", "asset-b", "subtitle_overlay", (0, 48), (0, 48)),
            clip_use("use-b", "asset-b", "b_roll", (0, 24), (24, 48)),
            clip_use("use-card", "asset-b", "end_card", (0, 12), (48, 60)),
        ]));
        let projection = plan_projection(&model).unwrap();
        let map = build_frame_map(&model, &projection, "clock-render", "map-spine").unwrap();
        // Base spine only: the full-timeline subtitle contributes no
        // segments and no render time.
        assert_eq!(map.segments.len(), 3);
        assert!(map.segments.iter().all(|s| s
            .source_clip_use
            .as_ref()
            .is_some_and(|id| id.0 != "use-sub")));
        assert_eq!(
            map.segments
                .iter()
                .map(|s| s.source_clip_use.as_ref().unwrap().0.as_str())
                .collect::<Vec<_>>(),
            vec!["use-a", "use-b", "use-card"]
        );
        assert_eq!((map.segments[2].to.start, map.segments[2].to.end), (48, 60));
        assert_eq!(render_span(&map), 60);
    }

    #[test]
    fn track_provisioning_counts_overlay_tracks() {
        let base = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-b", "asset-b", "b_roll", (0, 24), (24, 48)),
        ]));
        let base = plan_projection(&base).unwrap();
        assert_eq!(required_video_tracks(&base), 2);
        assert!(add_tracks_operation(Path::new("x.fcproj"), &base).is_none());
        let subs = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-sub", "asset-b", "subtitle_overlay", (0, 24), (0, 24)),
        ]));
        let subs = plan_projection(&subs).unwrap();
        assert_eq!(required_video_tracks(&subs), 3);
        assert!(add_tracks_operation(Path::new("x.fcproj"), &subs).is_none());
        let full = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-sub", "asset-b", "subtitle_overlay", (0, 24), (0, 24)),
            clip_use("use-mg", "asset-b", "motion_graphic", (0, 24), (0, 24)),
            clip_use("use-frame", "asset-b", "frame_overlay", (0, 24), (0, 24)),
        ]));
        let full = plan_projection(&full).unwrap();
        assert_eq!(required_video_tracks(&full), 5);
        match add_tracks_operation(Path::new("x.fcproj"), &full) {
            Some(Operation::AddTracks { video, audio, .. }) => {
                assert_eq!((video, audio), (2, 0));
            }
            other => panic!("V5 overlays need two added tracks, got {other:?}"),
        }
    }

    #[test]
    fn overlay_codec_gate_names_what_the_engine_draws() {
        // Measured allowlist: ProRes with alpha composites; opaque ProRes
        // and H264 are full-frame decodes, fine for cards but a box over
        // the picture for compositing roles. `qtrle` and PNG decode to
        // nothing. Anything unmeasured refuses.
        for role in [
            "subtitle_overlay",
            "motion_graphic",
            "frame_overlay",
            "post_header",
        ] {
            overlay_codec_compliant(role, "use-x", "prores", "yuva444p10le").unwrap();
            overlay_codec_compliant(role, "use-x", "prores", "yuva444p12le").unwrap();
            for (codec, pix_fmt) in [
                ("qtrle", "argb"),
                ("png", "rgba"),
                ("h264", "yuv420p"),
                ("prores", "yuv422p10le"),
                ("hevc", "yuv420p"),
            ] {
                let err = overlay_codec_compliant(role, "use-x", codec, pix_fmt).unwrap_err();
                assert!(
                    matches!(err, AdapterError::Unsupported { ref code, .. } if code == "overlay_codec_not_supported"),
                    "{role} {codec}/{pix_fmt} must refuse, got {err:?}"
                );
            }
        }
        overlay_codec_compliant("end_card", "use-c", "prores", "yuva444p12le").unwrap();
        overlay_codec_compliant("end_card", "use-c", "prores", "yuv422p10le").unwrap();
        overlay_codec_compliant("end_card", "use-c", "h264", "yuv420p").unwrap();
        let err = overlay_codec_compliant("end_card", "use-c", "qtrle", "argb").unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "overlay_codec_not_supported"),
            "a qtrle end card draws nothing and must refuse, got {err:?}"
        );
        assert!(matches!(
            overlay_codec_compliant("a_roll", "use-a", "prores", "yuv422p10le").unwrap_err(),
            AdapterError::InvalidModel(_)
        ));
    }

    #[test]
    fn verify_media_gates_overlay_codecs_only() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("media")).unwrap();
        for name in ["a.mp4", "b.mov"] {
            std::fs::write(dir.path().join("media").join(name), b"fake").unwrap();
        }
        // Base picture never probes: a panicking probe proves the gate
        // does not touch base assets.
        let base = model_with_uses(serde_json::json!([clip_use(
            "use-a",
            "asset-a",
            "a_roll",
            (0, 24),
            (0, 24)
        ),]));
        let projection = plan_projection(&base).unwrap();
        let resolved = verify_media_with_probe(dir.path(), &projection, &|_| {
            panic!("base assets must not probe")
        })
        .unwrap();
        assert_eq!(resolved.len(), 1);
        // Overlays probe per asset (one probe per asset, not per use).
        let mixed = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-s1", "asset-b", "subtitle_overlay", (0, 12), (0, 12)),
            clip_use("use-s2", "asset-b", "subtitle_overlay", (0, 12), (12, 24)),
        ]));
        let projection = plan_projection(&mixed).unwrap();
        let probes = std::cell::Cell::new(0);
        let resolved = verify_media_with_probe(dir.path(), &projection, &|path| {
            probes.set(probes.get() + 1);
            assert!(path.ends_with("b.mov"));
            Some(("prores".into(), "yuva444p10le".into()))
        })
        .unwrap();
        assert_eq!((probes.get(), resolved.len()), (1, 2));
        // An undecodable overlay refuses by name; an unprobed one refuses
        // as unverified rather than shipping unseen.
        for (probe, code) in [
            (
                Some(("qtrle".to_string(), "argb".to_string())),
                "overlay_codec_not_supported",
            ),
            (None, "overlay_codec_unverified"),
        ] {
            let err =
                verify_media_with_probe(dir.path(), &projection, &|_| probe.clone()).unwrap_err();
            assert!(
                matches!(err, AdapterError::Unsupported { code: ref c, .. } if c == code),
                "expected {code}, got {err:?}"
            );
        }
    }

    fn model_with_audio_channels(channels: &[Option<u64>]) -> ProjectModel {
        let mut model = minimal_model();
        model
            .streams
            .retain(|s| s.metadata.get("codec_type").and_then(|v| v.as_str()) != Some("audio"));
        for (index, channel_count) in channels.iter().enumerate() {
            let mut metadata =
                BTreeMap::from([("codec_type".to_owned(), serde_json::Value::from("audio"))]);
            if let Some(count) = channel_count {
                metadata.insert("channels".to_owned(), serde_json::Value::from(*count));
            }
            model
                .streams
                .push(ren_protocol::project_model::SourceStream {
                    id: ren_protocol::project_model::StreamId(format!("stream-audio-{index}")),
                    asset_id: ren_protocol::project_model::AssetId("asset-a".into()),
                    stream_index: index as u32 + 1,
                    clock_id: ren_protocol::project_model::ClockId("clock-source".into()),
                    metadata,
                    source_digest: None,
                    provenance_id: None,
                    coverage: None,
                    producer_version: None,
                    freshness: None,
                });
        }
        model
    }

    fn mapping_of(item: u64, format: &str, clips: serde_json::Value) -> serde_json::Value {
        serde_json::json!({"items": [{"item": item, "format": format, "clips": clips}]})
    }

    #[test]
    fn dual_mono_mapping_fires_only_for_discrete_mono() {
        let project = Path::new("test.fcproj");
        // The reel shape: discrete-mono streams the engine stereo-paired.
        let model = model_with_audio_channels(&[Some(1), Some(1), Some(1), Some(1)]);
        let mapping = mapping_of(8, "Stereo", serde_json::json!([[0, 1], [2, 3]]));
        let planned = plan_dual_mono_mapping(&model, "asset-a", 8, project, &mapping)
            .unwrap()
            .expect("discrete mono in a stereo pair must plan a remap");
        assert_eq!(
            planned,
            Operation::AudioChannels {
                project: project.into(),
                items: vec![8],
                format: Some(AudioChannelFormat::Stereo),
                clips: Some(vec![vec![0, 0]]),
            }
        );
        // Mono-regrouped discrete mono still collapses to the first channel.
        let mapping = mapping_of(8, "Mono", serde_json::json!([[0], [1], [2], [3]]));
        let planned = plan_dual_mono_mapping(&model, "asset-a", 8, project, &mapping)
            .unwrap()
            .expect("discrete mono in mono groups must plan a remap");
        assert!(matches!(
            planned,
            Operation::AudioChannels {
                format: Some(AudioChannelFormat::Stereo),
                clips: Some(_),
                ..
            }
        ));
        // Already dual-mono is untouched.
        let mapping = mapping_of(8, "Stereo", serde_json::json!([[0, 0]]));
        assert!(
            plan_dual_mono_mapping(&model, "asset-a", 8, project, &mapping)
                .unwrap()
                .is_none()
        );
        // True stereo keeps the engine behavior: no stereo image is collapsed.
        let stereo = model_with_audio_channels(&[Some(2)]);
        let mapping = mapping_of(8, "Stereo", serde_json::json!([[0, 1]]));
        assert!(
            plan_dual_mono_mapping(&stereo, "asset-a", 8, project, &mapping)
                .unwrap()
                .is_none()
        );
        // Unknown channel counts keep the engine behavior: nothing is guessed.
        let unknown = model_with_audio_channels(&[None]);
        assert!(
            plan_dual_mono_mapping(&unknown, "asset-a", 8, project, &mapping)
                .unwrap()
                .is_none()
        );
        // Mixed mono-plus-stereo is not discrete mono: untouched.
        let mixed = model_with_audio_channels(&[Some(1), Some(2)]);
        assert!(
            plan_dual_mono_mapping(&mixed, "asset-a", 8, project, &mapping)
                .unwrap()
                .is_none()
        );
        // Video-only assets name no audio to map.
        let mut silent = model_with_audio_channels(&[]);
        silent.streams.clear();
        assert!(
            plan_dual_mono_mapping(&silent, "asset-a", 8, project, &mapping)
                .unwrap()
                .is_none()
        );
        // A mapping document that cannot answer the channel question refuses.
        for bad in [
            serde_json::json!({"items": []}),
            serde_json::json!({"items": [{"item": 9, "format": "Stereo", "clips": [[0, 1]]}]}),
            serde_json::json!({"items": [{"item": 8, "format": "Stereo"}]}),
            serde_json::json!({"items": [{"item": 8, "format": "Stereo", "clips": []}]}),
            serde_json::json!({"items": [{"item": 8, "format": "Stereo", "clips": [[]]}]}),
        ] {
            assert!(
                plan_dual_mono_mapping(&model, "asset-a", 8, project, &bad).is_err(),
                "must refuse, got {bad}"
            );
        }
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
        assert!(matches!(
            export,
            Operation::Export {
                bitrate_kbps: None,
                bitrate_mode: None,
                ..
            }
        ));
        // An explicit bitrate pair passes through typed; an unknown mode
        // refuses at plan time, before any driver operation is produced.
        let export = plan_edit(
            &filmcraft_edit(
                "edit-export-bitrate",
                "filmcraft.export",
                serde_json::json!({"path": "/tmp/out.mp4", "format": "h264",
                    "bitrate_kbps": 10_000, "bitrate_mode": "cbr"}),
            ),
            &context,
        )
        .unwrap();
        assert!(matches!(
            export,
            Operation::Export {
                bitrate_kbps: Some(10_000),
                bitrate_mode: Some(BitrateMode::Cbr),
                ..
            }
        ));
        let err = plan_edit(
            &filmcraft_edit(
                "edit-export-bad-mode",
                "filmcraft.export",
                serde_json::json!({"path": "/tmp/out.mp4", "format": "h264",
                    "bitrate_mode": "bogus"}),
            ),
            &context,
        )
        .unwrap_err();
        assert!(
            format!("{err}").contains("unknown bitrate mode"),
            "unexpected error: {err}"
        );
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

        let mut split = projection.clone();
        split.placements.push(split.placements[0].clone());
        let split_map = reconcile_clip_ids(&split, &[vec![11], vec![22], vec![12]]).unwrap();
        assert_eq!(split_map.get("use-a"), Some(&vec![11, 12]));
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

    fn stem_model() -> ProjectModel {
        // Dialogue plus a crossfaded bed pair (40-48 overlap) plus one
        // SFX hit: every source window covers its timeline span exactly.
        let json = serde_json::json!({
            "id": "adapter-unit-stems",
            "schema_version": 1,
            "timebase_policy": "rational_frames",
            "clocks": [
                {"id": "clock-source", "domain": {"kind": "source", "asset_id": "asset-av", "stream_id": "stream-video"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
                {"id": "clock-timeline", "domain": {"kind": "project"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
                {"id": "clock-render", "domain": {"kind": "render", "render_id": "render-1"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}}
            ],
            "assets": [
                {"id": "asset-av", "kind": "video", "uri": "media/av.mp4", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]},
                {"id": "asset-bed", "kind": "audio", "uri": "media/bed.wav", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]},
                {"id": "asset-sfx", "kind": "audio", "uri": "media/sfx.wav", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]}
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
                {"id": "use-dialogue", "asset_id": "asset-av", "source_interval": {"clock_id": "clock-source", "start": 0, "end": 96}, "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 96}, "entity_links": [], "role": "a_roll", "intent_id": null},
                {"id": "use-bed-a", "asset_id": "asset-bed", "source_interval": {"clock_id": "clock-source", "start": 0, "end": 48}, "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 48}, "entity_links": [], "role": "music", "intent_id": null},
                {"id": "use-bed-b", "asset_id": "asset-bed", "source_interval": {"clock_id": "clock-source", "start": 0, "end": 56}, "timeline_interval": {"clock_id": "clock-timeline", "start": 40, "end": 96}, "entity_links": [], "role": "music", "intent_id": null},
                {"id": "use-hit", "asset_id": "asset-sfx", "source_interval": {"clock_id": "clock-source", "start": 0, "end": 24}, "timeline_interval": {"clock_id": "clock-timeline", "start": 48, "end": 72}, "entity_links": [], "role": "sfx", "intent_id": null}
            ],
            "edits": [], "effects": [], "renders": [], "frame_maps": [],
            "nle_bindings": [], "receipts": [],
            "provenance": [{"id": "prov-1", "producer": "unit", "producer_version": null, "source_digest": null, "recorded_at": null}]
        });
        decode_project_model(&json.to_string()).expect("stem model decodes")
    }

    #[test]
    fn stem_roles_deal_across_strips_bed_then_sfx() {
        let model = stem_model();
        let projection = plan_projection(&model).unwrap();
        let strip_of = |id: &str| {
            projection
                .placements
                .iter()
                .find(|p| p.clip_use_id == id)
                .unwrap_or_else(|| panic!("no placement for {id}"))
        };
        // Dialogue stays linked on V1/A1.
        assert_eq!(strip_of("use-dialogue").track, "V1");
        assert_eq!(strip_of("use-dialogue").audio_track.as_deref(), Some("A1"));
        // The crossfaded bed pair overlaps, so it takes two strips; the
        // SFX hit starts above whatever the bed used.
        assert_eq!(strip_of("use-bed-a").track, "A2");
        assert_eq!(strip_of("use-bed-b").track, "A3");
        assert_eq!(strip_of("use-hit").track, "A4");
        for id in ["use-bed-a", "use-bed-b", "use-hit"] {
            let placement = strip_of(id);
            assert_eq!(
                placement.audio_track.as_deref(),
                Some(placement.track.as_str()),
                "{id} must carry its strip for volume automation"
            );
        }
        // One strip past the fresh sequence's own three: the plan grows
        // the sequence before the first placement lands.
        assert_eq!(audio_strips_used(&projection), 4);
        let growth = add_audio_tracks_operation(std::path::Path::new("x.fcproj"), &projection)
            .unwrap()
            .expect("A4 needs track growth");
        assert!(matches!(
            growth,
            Operation::AddAudioTracks {
                audio_tracks: 1,
                ..
            }
        ));
        // A plan that fits A1-A3 grows nothing.
        let fitting = minimal_model();
        let fitting_projection = plan_projection(&fitting).unwrap();
        assert_eq!(audio_strips_used(&fitting_projection), 1);
        assert!(
            add_audio_tracks_operation(std::path::Path::new("x.fcproj"), &fitting_projection,)
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn stem_edge_cases_refuse_or_reuse_by_name() {
        // A stem with no audio stream names a silence no file can play.
        let mut silent = stem_model();
        silent.assets.retain(|asset| asset.id.0 != "asset-sfx");
        silent
            .streams
            .retain(|stream| stream.asset_id.0 != "asset-sfx");
        silent.assets.push(
            serde_json::from_value(serde_json::json!(
                {"id": "asset-quiet", "kind": "video", "uri": "media/q.mp4", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]}
            ))
            .unwrap(),
        );
        silent.streams.push(
            serde_json::from_value(serde_json::json!(
                {"id": "stream-quiet", "asset_id": "asset-quiet", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}}
            ))
            .unwrap(),
        );
        for use_ in &mut silent.clip_uses {
            if use_.id.0 == "use-hit" {
                use_.asset_id.0 = "asset-quiet".into();
            }
        }
        let err = plan_projection(&silent).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "no_audio_for_stem"),
            "stem without audio must refuse by name, got {err:?}"
        );
        // A non-overlapping second SFX reuses its pool's first strip.
        let mut reuse = stem_model();
        for use_ in &mut reuse.clip_uses {
            if use_.id.0 == "use-bed-b" {
                // End the second bed piece where the first ends: no
                // overlap, one strip for the whole bed.
                use_.timeline_interval.start = 48;
                use_.source_interval.end = 48;
            }
        }
        let projection = plan_projection(&reuse).unwrap();
        let strip_of = |id: &str| {
            projection
                .placements
                .iter()
                .find(|p| p.clip_use_id == id)
                .unwrap()
                .track
                .clone()
        };
        assert_eq!(strip_of("use-bed-a"), "A2");
        assert_eq!(strip_of("use-bed-b"), "A2");
        assert_eq!(strip_of("use-hit"), "A3");
        assert_eq!(audio_strips_used(&projection), 3);
        // Picture overlaps still refuse with stems on the timeline, and
        // unknown roles still refuse.
        let mut stacked = stem_model();
        for use_ in &mut stacked.clip_uses {
            if use_.id.0 == "use-dialogue" {
                use_.timeline_interval.end = 200;
                use_.source_interval.end = 200;
            }
        }
        // use-dialogue 0-200 now overlaps use-bed-a 0-48 in time, but
        // stems stack legally - refusal needs a second PICTURE use.
        assert!(plan_projection(&stacked).is_ok());
        stacked.clip_uses.push(
            serde_json::from_value(serde_json::json!(
                {"id": "use-second-picture", "asset_id": "asset-av", "source_interval": {"clock_id": "clock-source", "start": 0, "end": 96}, "timeline_interval": {"clock_id": "clock-timeline", "start": 10, "end": 50}, "entity_links": [], "role": "b_roll", "intent_id": null}
            ))
            .unwrap(),
        );
        let err = plan_projection(&stacked).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "timeline_overlap"),
            "picture overlap must refuse by name, got {err:?}"
        );
        stacked.clip_uses.pop();
        for use_ in &mut stacked.clip_uses {
            if use_.id.0 == "use-hit" {
                use_.role = "title".into();
            }
        }
        let err = plan_projection(&stacked).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "unsupported_role"),
            "unknown role must refuse by name, got {err:?}"
        );
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
        // Per-clip gain addresses the use's first reconciled id; the
        // engine follows the link onto the audio item itself.
        assert!(matches!(
            &operations[0],
            Operation::ClipAudioGain { clips, gain_db, .. }
            if clips == &vec![7u64] && *gain_db == -3.0
        ));
        // The model counterpart logs the span-neutral edit; re-planning
        // the logged edit over the mutated model plans the identical op.
        let mutated = apply_ren_edit_to_model(&model, &edit).unwrap();
        assert_eq!(mutated.edits.len(), model.edits.len() + 1);
        let logged = mutated.edits.last().unwrap();
        assert_eq!(logged.operation, "audio.gain");
        let replanned = plan_ren_edit(
            &mutated,
            &plan_projection(&mutated).unwrap(),
            logged,
            &context,
        )
        .unwrap();
        assert_eq!(replanned, operations);
        // Gain without an audio stream refuses on both paths (use-b is
        // video-only in the minimal model).
        let silent_ids = BTreeMap::from([
            ("use-a".to_owned(), vec![7u64]),
            ("use-b".to_owned(), vec![9u64]),
        ]);
        let silent_context = ren_edit_context(&silent_ids);
        let silent_edit = ren_edit(
            "edit-ren-gain-silent",
            "audio.gain",
            "use-b",
            serde_json::json!({"db": -3.0}),
        );
        let err = plan_ren_edit(&model, &projection, &silent_edit, &silent_context).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "no_audio_for_gain"),
            "gain without audio must refuse by name, got {err:?}"
        );
        let err = apply_ren_edit_to_model(&model, &silent_edit).unwrap_err();
        assert!(
            matches!(err, AdapterError::Unsupported { ref code, .. } if code == "no_audio_for_gain"),
            "gain mutation without audio must refuse by name, got {err:?}"
        );
        // Keyframed audio and pan refuse by name: keyframes ride the
        // volume-automation op once that lane merges.
        for (operation, after, want) in [
            (
                "audio.fade",
                serde_json::json!({"fade_in": 12, "fade_out": 12}),
                "audio_keyframes_require_volume_automation",
            ),
            (
                "audio.automation",
                serde_json::json!({"param": "volume", "keyframes": {"0": -6.0}}),
                "audio_keyframes_require_volume_automation",
            ),
            (
                "audio.pan",
                serde_json::json!({"pan": 0.5}),
                "audio_pan_not_projected",
            ),
        ] {
            let edit = ren_edit("edit-ren-keyed", operation, "use-a", after);
            let err = plan_ren_edit(&model, &projection, &edit, &context).unwrap_err();
            assert!(
                matches!(err, AdapterError::Unsupported { ref code, .. } if code == want),
                "{operation} must refuse as {want}, got {err:?}"
            );
        }
        // A non-volume automation parameter is invalid on either path.
        let edit = ren_edit(
            "edit-ren-pan-param",
            "audio.automation",
            "use-a",
            serde_json::json!({"param": "pan", "keyframes": {"0": 0.5}}),
        );
        assert!(matches!(
            plan_ren_edit(&model, &projection, &edit, &context),
            Err(AdapterError::InvalidModel(_))
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
        for percent in [33.333333333333336, 123_456_789.123_456_79, 1e300] {
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
    fn filmcraft_vocabularies_partition_without_duplicating_effectcraft() {
        assert!(effectcraft_owned("move.slow_zoom_in"));
        assert!(effectcraft_owned("blur.gaussian"));
        assert!(effectcraft_owned("color.saturation"));
        assert!(!effectcraft_owned("color.lut"));
        assert!(!effectcraft_owned("transform.position"));
        assert!(filmcraft_executed("color.lut"));
        assert!(filmcraft_executed("transform.position"));
        assert!(filmcraft_executed("transform.scale"));
        assert!(filmcraft_executed("transform.rotation"));
        assert!(filmcraft_executed("transform.crop"));
        assert!(filmcraft_executed("opacity.opacity"));
        assert!(filmcraft_executed("audio.volume"));
        assert!(!filmcraft_executed("move.slow_zoom_in"));
        assert!(!filmcraft_executed("blur.gaussian"));
        assert!(!filmcraft_executed("neon.dreams"));
        // A model carrying only EffectCraft-owned effects reports
        // ownership, never a silent drop or a duplicate plan.
        let mut model = minimal_model();
        model.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-move", "class_id": "move.slow_zoom_in",
                 "target_id": "use-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {"zoom_start": 1.0, "zoom_end": 1.5}, "keyframes": []}
            ))
            .unwrap(),
        );
        let projection = plan_projection(&model).unwrap();
        let codes: Vec<_> = projection.losses.iter().map(|l| l.code.as_str()).collect();
        assert!(codes.contains(&"effect_owned_by_effectcraft"));
        assert!(!codes.contains(&"effect_not_projected"));
        assert!(plan_effect_keyframes(&model, &projection.placements)
            .unwrap()
            .is_empty());
        // An unmapped class keeps the explicit loss.
        model.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-weird", "class_id": "neon.dreams",
                 "target_id": "use-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {}, "keyframes": []}
            ))
            .unwrap(),
        );
        let projection = plan_projection(&model).unwrap();
        let codes: Vec<_> = projection.losses.iter().map(|l| l.code.as_str()).collect();
        assert!(codes.contains(&"effect_not_projected"));
    }

    #[test]
    fn captions_plan_from_speech_and_apply_anchors() {
        let mut model = minimal_model();
        model.observations.push(
            serde_json::from_value(serde_json::json!(
                {"id": "obs-speech", "asset_id": "asset-a",
                 "interval": {"clock_id": "clock-source", "start": 0, "end": 24},
                 "modality": "speech",
                 "payload": {"text": "Hello world", "speaker_entity_id": "person-ada"},
                 "confidence": 1.0, "provenance_id": "prov-1"}
            ))
            .unwrap(),
        );
        let cues = plan_captions(&model).unwrap();
        assert_eq!(cues.len(), 1);
        assert_eq!(cues[0].text, "Hello world");
        assert_eq!(cues[0].speaker.as_deref(), Some("person-ada"));
        // The 24tps source clock scales to engine ticks (24 model ticks
        // are one engine second).
        assert_eq!(
            (cues[0].start_ticks, cues[0].duration_ticks),
            (0, 254_016_000_000)
        );
        // The apply counterpart anchors the same cues to the model.
        let applied = apply_captions_to_model(&model, &cues).unwrap();
        assert_eq!(applied.clip_uses.len(), model.clip_uses.len());
        // Drift between plan and expectation refuses rather than captioning
        // the wrong words.
        let mut drifted = cues.clone();
        drifted[0].text = "Other words".into();
        assert!(apply_captions_to_model(&model, &drifted).is_err());
        // Caption operations carry exact ticks in plan order.
        let operations = caption_operations(Path::new("test.fcproj"), Some("C1".into()), &cues);
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::AddCaption { text, time_ticks: 0, duration_ticks: 254_016_000_000, .. } if text == "Hello world"
        ));
    }

    #[test]
    fn lut_plans_reference_and_guards_unknown_params() {
        let mut model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        model.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-lut", "class_id": "color.lut", "target_id": "use-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {"lut": "builtin:logc3-awg3-to-rec709"}, "keyframes": []}
            ))
            .unwrap(),
        );
        let plans = plan_lut_effects(&model, &projection.placements).unwrap();
        assert_eq!(plans.len(), 1);
        assert!(plans[0].is_reference());
        let applied = apply_lut_to_model(&model, &projection.placements, &plans).unwrap();
        assert_eq!(applied.effects.len(), 1);
        // A projectable LUT is executed, never a loss.
        let projection = plan_projection(&model).unwrap();
        assert!(projection
            .losses
            .iter()
            .all(|l| l.code != "effect_not_projected"));
        // Unknown parameters refuse by name.
        model.effects[0]
            .parameters
            .insert("strength".into(), serde_json::json!(0.5));
        let err = plan_lut_effects(&model, &projection.placements).unwrap_err();
        assert!(
            matches!(&err, AdapterError::Unsupported { ref code, .. } if code == "lut_unknown_parameter"),
            "got {err:?}"
        );
    }

    #[test]
    fn picture_keys_guard_out_of_span_and_drive_exact_writes() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let mut keyed = model.clone();
        keyed.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-pos", "class_id": "transform.position", "target_id": "use-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {},
                 "keyframes": [{"tick": 0, "value": [100.0, 200.0]}, {"tick": 12, "value": [300.0, 400.0]}]}
            ))
            .unwrap(),
        );
        let plans = plan_effect_keyframes(&keyed, &projection.placements).unwrap();
        assert_eq!(plans.len(), 1);
        assert_eq!(plans[0].engine_effect, "motion");
        assert_eq!(plans[0].engine_param.as_deref(), Some("position"));
        assert_eq!(
            plans[0]
                .keys
                .iter()
                .map(|k| k.time_ticks)
                .collect::<Vec<_>>(),
            vec![0, 127_008_000_000]
        );
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64])]);
        let operations =
            effect_key_operations(Path::new("test.fcproj"), &clip_ids, &plans).unwrap();
        // One animation toggle plus one timed write per key.
        assert_eq!(operations.len(), 3);
        assert!(matches!(
            &operations[0],
            Operation::ToggleEffectAnimation { clip: 7, .. }
        ));
        assert!(matches!(
            &operations[1],
            Operation::SetEffectParam {
                time_ticks: Some(0),
                ..
            }
        ));
        let applied =
            apply_effect_keyframes_to_model(&keyed, &projection.placements, &plans).unwrap();
        assert_eq!(applied.effects.len(), 1);
        // A key outside the clip span refuses with the named out-of-span
        // code instead of reaching the engine, which would answer success
        // while placing nothing.
        keyed.effects[0].keyframes.push(
            serde_json::from_value(serde_json::json!({"tick": 48, "value": [0.0, 0.0]})).unwrap(),
        );
        // The effect interval must also cover the key for the clip-span
        // guard to be the one that fires; widen it past the clip end.
        keyed.effects[0].interval.end = 72;
        let err = plan_effect_keyframes(&keyed, &projection.placements).unwrap_err();
        assert!(
            matches!(&err, AdapterError::Unsupported { ref code, .. } if code == "keyframe_out_of_span"),
            "got {err:?}"
        );
        // Static crop rects plan whole-clip writes with an apply first.
        let mut cropped = model.clone();
        cropped.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-crop", "class_id": "transform.crop", "target_id": "use-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {"left": 5.0, "top": 0.0, "right": 5.0, "bottom": 0.0},
                 "keyframes": []}
            ))
            .unwrap(),
        );
        let plans = plan_effect_keyframes(&cropped, &projection.placements).unwrap();
        assert_eq!(plans.len(), 1);
        assert_eq!(plans[0].needs_apply.as_deref(), Some("crop"));
        assert_eq!(plans[0].static_params.len(), 4);
        let operations =
            effect_key_operations(Path::new("test.fcproj"), &clip_ids, &plans).unwrap();
        assert_eq!(operations.len(), 5);
        assert!(matches!(&operations[0], Operation::ApplyEffect { .. }));
    }

    #[test]
    fn static_transform_effects_are_written_from_the_model_contract() {
        let mut model = minimal_model();
        model.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-static-position", "class_id": "transform.position", "target_id": "use-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {"position": [120.0, -480.0]}, "keyframes": []}
            )).unwrap(),
        );
        model.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-static-scale", "class_id": "transform.scale", "target_id": "use-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {"scale": 2.1386}, "keyframes": []}
            ))
            .unwrap(),
        );
        let projection = plan_projection(&model).unwrap();
        let plans = plan_effect_keyframes(&model, &projection.placements).unwrap();
        assert_eq!(plans.len(), 2);
        assert_eq!(
            plans[0].static_params,
            vec![("position".into(), serde_json::json!([120.0, -480.0]))]
        );
        assert_eq!(
            plans[1].static_params,
            vec![("scale".into(), serde_json::json!(2.1386_f64 * 100.0))]
        );
        assert!(plans
            .iter()
            .all(|plan| plan.keys.is_empty() && plan.engine_param.is_none()));
        let clip_ids = BTreeMap::from([("use-a".into(), vec![7])]);
        let operations =
            effect_key_operations(Path::new("test.fcproj"), &clip_ids, &plans).unwrap();
        assert_eq!(operations.len(), 2);
        assert!(
            matches!(&operations[0], Operation::SetEffectParam { param, value, time_ticks: None, .. }
            if param == "position" && value == &serde_json::json!([120.0, -480.0]))
        );
        assert!(
            matches!(&operations[1], Operation::SetEffectParam { param, value, time_ticks: None, .. }
            if param == "scale" && value == &serde_json::json!(2.1386_f64 * 100.0))
        );
    }

    #[test]
    fn keyframed_scale_multiplier_converts_to_filmcraft_percent() {
        let mut model = minimal_model();
        model.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-scale-keys", "class_id": "transform.scale", "target_id": "use-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {},
                 "keyframes": [{"tick": 0, "value": 1.0}, {"tick": 12, "value": 2.1386}]}
            ))
            .unwrap(),
        );
        let projection = plan_projection(&model).unwrap();
        let plans = plan_effect_keyframes(&model, &projection.placements).unwrap();
        assert_eq!(plans[0].keys[0].value, serde_json::json!(100.0));
        assert_eq!(
            plans[0].keys[1].value,
            serde_json::json!(2.1386_f64 * 100.0)
        );
    }

    #[test]
    fn a_model_position_effect_clears_the_overlay_position_loss() {
        let mut model = model_with_uses(serde_json::json!([
            clip_use("use-a", "asset-a", "a_roll", (0, 24), (0, 24)),
            clip_use("use-sub", "asset-a", "subtitle_overlay", (0, 24), (0, 24)),
        ]));
        model.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-sub-position", "class_id": "transform.position", "target_id": "use-sub",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {"position": [540.0, 1500.0]}, "keyframes": []}
            )).unwrap(),
        );
        let projection = plan_projection(&model).unwrap();
        assert!(!projection
            .losses
            .iter()
            .any(|loss| loss.code == "overlay_unpositioned"));
    }

    #[test]
    fn volume_automation_plans_strip_points_and_refuses_silence() {
        let model = minimal_model();
        let projection = plan_projection(&model).unwrap();
        let mut automated = model.clone();
        automated.effects.push(
            serde_json::from_value(serde_json::json!(
                {"id": "effect-vol", "class_id": "audio.volume", "target_id": "use-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 24},
                 "parameters": {},
                 "keyframes": [{"tick": 0, "value": -3.0}, {"tick": 12, "value": -6.0}]}
            ))
            .unwrap(),
        );
        let plans = plan_volume_automation(&automated, &projection.placements).unwrap();
        assert_eq!(plans.len(), 1);
        // use-a carries an audio stream, so the plan drives A1.
        assert_eq!(plans[0].strip, "A1");
        assert_eq!(plans[0].points, vec![(0, -3.0), (127_008_000_000, -6.0)]);
        let operations = volume_operations(Path::new("test.fcproj"), &plans);
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::MixerWriteAutomation { points, .. } if points.len() == 2
        ));
        let applied =
            apply_volume_automation_to_model(&automated, &projection.placements, &plans).unwrap();
        assert_eq!(applied.effects.len(), 1);
        // A target without audio refuses by name.
        automated.effects[0].target_id = "use-b".into();
        let err = plan_volume_automation(&automated, &projection.placements).unwrap_err();
        assert!(
            matches!(&err, AdapterError::Unsupported { ref code, .. } if code == "no_audio_for_volume"),
            "got {err:?}"
        );
    }

    /// Dialogue plus a music bed on the same 24tps clocks as
    /// [`minimal_model`]: the bed plays 0-48 while dialogue plays 12-36.
    fn ducking_model() -> ProjectModel {
        let document = serde_json::json!({
            "id": "adapter-unit-ducking",
            "schema_version": 1,
            "timebase_policy": "rational_frames",
            "clocks": [
                {"id": "clock-source", "domain": {"kind": "source", "asset_id": "asset-a", "stream_id": "stream-video"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
                {"id": "clock-timeline", "domain": {"kind": "project"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}},
                {"id": "clock-render", "domain": {"kind": "render", "render_id": "render-1"}, "ticks_per_second": 24, "frame_rate": {"numerator": 24, "denominator": 1}}
            ],
            "assets": [
                {"id": "asset-a", "kind": "video", "uri": "media/a.mp4", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]},
                {"id": "asset-bed", "kind": "audio", "uri": "media/bed.wav", "content_digest": null, "metadata": {}, "provenance": ["prov-1"]}
            ],
            "streams": [
                {"id": "stream-video", "asset_id": "asset-a", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "video"}},
                {"id": "stream-dialogue", "asset_id": "asset-a", "stream_index": 1, "clock_id": "clock-source", "metadata": {"codec_type": "audio"}},
                {"id": "stream-bed", "asset_id": "asset-bed", "stream_index": 0, "clock_id": "clock-source", "metadata": {"codec_type": "audio"}}
            ],
            "entities": [], "observations": [], "identity_links": [], "masks": [],
            "object_classes": [], "tracks": [], "relations": [], "intents": [],
            "clip_uses": [
                {"id": "use-dialogue", "asset_id": "asset-a",
                 "source_interval": {"clock_id": "clock-source", "start": 12, "end": 36},
                 "timeline_interval": {"clock_id": "clock-timeline", "start": 12, "end": 36},
                 "entity_links": [], "role": "a_roll", "intent_id": null},
                {"id": "use-bed", "asset_id": "asset-bed",
                 "source_interval": {"clock_id": "clock-source", "start": 0, "end": 48},
                 "timeline_interval": {"clock_id": "clock-timeline", "start": 0, "end": 48},
                 "entity_links": [], "role": "music", "intent_id": null}
            ],
            "edits": [], "effects": [], "renders": [], "frame_maps": [],
            "nle_bindings": [], "receipts": [],
            "provenance": [{"id": "prov-1", "producer": "unit", "producer_version": null, "source_digest": null, "recorded_at": null}]
        });
        decode_project_model(&document.to_string()).expect("ducking model decodes")
    }

    fn ducking_effect_json(
        id: &str,
        target: &str,
        start: i64,
        end: i64,
        params: serde_json::Value,
        keys: serde_json::Value,
    ) -> serde_json::Value {
        serde_json::json!(
            {"id": id, "class_id": "audio.ducking", "target_id": target,
             "interval": {"clock_id": "clock-timeline", "start": start, "end": end},
             "parameters": params, "keyframes": keys}
        )
    }

    #[test]
    fn bed_ducking_derives_ramped_points_from_declared_depth_and_ramp() {
        let model = ducking_model();
        let projection = plan_projection(&model).unwrap();
        // The lone bed deals onto A2; dialogue stays linked on V1/A1.
        assert_eq!(
            projection
                .placements
                .iter()
                .find(|p| p.clip_use_id == "use-bed")
                .unwrap()
                .track,
            "A2"
        );
        let mut ducked = model.clone();
        ducked.effects.push(
            serde_json::from_value(ducking_effect_json(
                "effect-duck",
                "use-bed",
                12,
                36,
                serde_json::json!({"full_db": 0.0, "depth_db": 6.0, "ramp_ticks": 6}),
                serde_json::json!([]),
            ))
            .unwrap(),
        );
        let reprojected = plan_projection(&ducked).unwrap();
        assert!(
            reprojected.losses.is_empty(),
            "ducking executes, it is never a loss: {:#?}",
            reprojected.losses
        );
        let plans = plan_bed_ducking(&ducked, &reprojected.placements).unwrap();
        assert_eq!(plans.len(), 1);
        assert_eq!(plans[0].strip, "A2");
        // One engine tick per 24tps model tick is 10_584_000_000: full at
        // open, ramp-in over 6 ticks onto -6 dB at 12, hold to 36, ramp-out
        // to full at 42, hold to the bed end at 48.
        assert_eq!(
            plans[0].points,
            vec![
                (0, 0.0),
                (63_504_000_000, 0.0),
                (127_008_000_000, -6.0),
                (381_024_000_000, -6.0),
                (444_528_000_000, 0.0),
                (508_032_000_000, 0.0),
            ]
        );
        let operations = volume_operations(Path::new("test.fcproj"), &plans);
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::MixerWriteAutomation { strip, lane, points, .. }
                if strip == "A2" && lane == "volume" && points.len() == 6
        ));
        let applied = apply_bed_ducking_to_model(&ducked, &reprojected.placements, &plans).unwrap();
        assert_eq!(applied.effects.len(), 1);
    }

    #[test]
    fn bed_ducking_refuses_undeclared_or_misplaced_curves_by_name() {
        let model = ducking_model();
        let projection = plan_projection(&model).unwrap();
        let refused = |params: serde_json::Value,
                       keys: serde_json::Value,
                       target: &str,
                       start: i64,
                       end: i64| {
            let mut attempted = model.clone();
            attempted.effects.push(
                serde_json::from_value(ducking_effect_json(
                    "effect-duck",
                    target,
                    start,
                    end,
                    params,
                    keys,
                ))
                .unwrap(),
            );
            plan_bed_ducking(&attempted, &projection.placements).unwrap_err()
        };
        let full = || serde_json::json!({"full_db": 0.0, "depth_db": 6.0, "ramp_ticks": 6});
        // Keyframes have no meaning on a derived curve.
        let err = refused(
            full(),
            serde_json::json!([{"tick": 12, "value": -6.0}]),
            "use-bed",
            12,
            36,
        );
        assert!(
            matches!(&err, AdapterError::Unsupported { ref code, .. } if code == "ducking_keyframes_unsupported"),
            "got {err:?}"
        );
        // Only full_db, depth_db and ramp_ticks are read.
        let mut extra = full();
        extra["attack_ms"] = serde_json::json!(200);
        let err = refused(extra, serde_json::json!([]), "use-bed", 12, 36);
        assert!(
            matches!(&err, AdapterError::Unsupported { ref code, .. } if code == "ducking_unknown_parameter"),
            "got {err:?}"
        );
        // A zero dip would drive nothing.
        let err = refused(
            serde_json::json!({"full_db": 0.0, "depth_db": 0.0, "ramp_ticks": 6}),
            serde_json::json!([]),
            "use-bed",
            12,
            36,
        );
        assert!(format!("{err}").contains("positive"), "got {err:?}");
        // A negative ramp runs backwards.
        let err = refused(
            serde_json::json!({"full_db": 0.0, "depth_db": 6.0, "ramp_ticks": -1}),
            serde_json::json!([]),
            "use-bed",
            12,
            36,
        );
        assert!(format!("{err}").contains("non-negative"), "got {err:?}");
        // Dialogue and SFX are not ducked on this path.
        let err = refused(full(), serde_json::json!([]), "use-dialogue", 12, 36);
        assert!(
            matches!(&err, AdapterError::Unsupported { ref code, .. } if code == "ducking_target_not_music"),
            "got {err:?}"
        );
        // The ducked span must sit inside the bed it drives.
        let err = refused(full(), serde_json::json!([]), "use-bed", 0, 72);
        assert!(
            matches!(&err, AdapterError::Unsupported { ref code, .. } if code == "ducking_outside_bed"),
            "got {err:?}"
        );
    }

    #[test]
    fn titles_bind_to_overlapping_clips_and_read_back() {
        let mut model = minimal_model();
        model.observations.push(
            serde_json::from_value(serde_json::json!(
                {"id": "obs-title", "asset_id": "asset-a",
                 "interval": {"clock_id": "clock-timeline", "start": 0, "end": 48},
                 "modality": "title",
                 "payload": {"text": "Opening"},
                 "confidence": 1.0, "provenance_id": "prov-1"}
            ))
            .unwrap(),
        );
        let projection = plan_projection(&model).unwrap();
        let plans = plan_titles(&model, &projection.placements).unwrap();
        assert_eq!(plans.len(), 1);
        assert_eq!(plans[0].text, "Opening");
        assert_eq!(plans[0].duration_seconds, 2);
        let clip_ids = BTreeMap::from([("use-a".to_owned(), vec![7u64])]);
        let operations = title_operations(Path::new("test.fcproj"), &clip_ids, &plans).unwrap();
        assert_eq!(operations.len(), 1);
        assert!(matches!(
            &operations[0],
            Operation::GraphicsNewText { clip: Some(7), .. }
        ));
        // Fractional-second titles refuse rather than round a span.
        model.observations[0].interval.end = 36;
        let err = plan_titles(&model, &projection.placements).unwrap_err();
        assert!(
            matches!(&err, AdapterError::Unsupported { ref code, .. } if code == "title_fractional_seconds"),
            "got {err:?}"
        );
    }

    #[test]
    fn fcproj_keyframe_readback_unwraps_engine_values() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("keys.fcproj");
        std::fs::write(
            &path,
            serde_json::json!({
                "project": {"items": {"7": {"kind": {"Sequence": {"video_tracks": [
                    {"items": [{"id": 10, "effects": [
                        {"effect": "motion", "params": {"position": {"value": {"Vec2": {"x": 1.0, "y": 2.0}},
                         "keyframes": [
                            {"time": 0, "value": {"Vec2": {"x": 1.0, "y": 2.0}}},
                            {"time": 12, "value": {"Vec2": {"x": 3.0, "y": 4.0}}}]}}},
                        {"effect": "opacity", "params": {"opacity": {"value": {"Float": 50.0}}}}
                    ]}]}
                ]}}}}}
            })
            .to_string(),
        )
        .unwrap();
        let keys = read_effect_keyframes(&path, 10, "motion", "position").unwrap();
        assert_eq!(
            keys,
            vec![
                (0, serde_json::json!([1.0, 2.0])),
                (12, serde_json::json!([3.0, 4.0])),
            ]
        );
        assert!(read_effect_keyframes(&path, 10, "motion", "scale").is_err());
        assert!(read_effect_keyframes(&path, 99, "motion", "position").is_err());
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
        assert!(!expanded.pieces[0].reverse);
        assert!(expanded.pieces[1].reverse);
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
