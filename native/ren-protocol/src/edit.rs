//! The edit algebra and the patch transaction.
//!
//! Modeled from `library/tools/edit_algebra.py` (the union vocabulary of
//! reversible timeline operations, every op name and its fields, the
//! inverse) and `library/tools/edit_patch.py` (EditPatch, the condition
//! vocabulary).
//!
//! `EditOp` is keyed by the algebra id (`"op": "clip.trim"`), so a trim
//! with no edge cannot exist: the edge is a field of the variant, and
//! adding a variant fails compilation until every match handles it.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::capability::CapabilityId;
use crate::ids::{ClipId, Generation};

/// One operation in the union vocabulary. The wire form is the algebra
/// id under `"op"` with the op's fields beside it, exactly as
/// `edit_algebra.ALGEBRA` declares them.
///
/// Required fields are the op's `required` tuple; optional fields are
/// the ones the algebra reads with `.get(...)` and are omitted when
/// absent, as Python's `to_dict` omits them.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "op")]
pub enum EditOp {
    // ── markers (domain: markers) ──────────────────────────────────
    /// One marker per frame: a second add there replaces the first.
    #[serde(rename = "marker.add")]
    MarkerAdd {
        frame: i64,
        color: String,
        name: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        note: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        duration: Option<i64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        custom_data: Option<String>,
    },
    #[serde(rename = "marker.delete")]
    MarkerDelete { frame: i64 },
    #[serde(rename = "clip_marker.add")]
    ClipMarkerAdd {
        unique_id: ClipId,
        frame: i64,
        color: String,
        name: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        note: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        duration: Option<i64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        custom_data: Option<String>,
    },
    #[serde(rename = "clip_marker.delete")]
    ClipMarkerDelete { unique_id: ClipId, frame: i64 },

    // ── clip structure (domain: timeline_structure) ─────────────────
    /// A local delete leaves a gap; `clip.ripple` closes it.
    #[serde(rename = "clip.delete")]
    ClipDelete { unique_id: ClipId },
    /// Places a pool clip at a stated span; the inverse deletes it.
    #[serde(rename = "clip.insert")]
    ClipInsert {
        unique_id: ClipId,
        track_type: String,
        track_index: i64,
        record_in: i64,
        record_out: i64,
        media_pool_item: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        source_in: Option<i64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        source_out: Option<i64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        client_unique_id: Option<String>,
    },
    /// One clip into two at a source frame; join restores it.
    #[serde(rename = "clip.split")]
    ClipSplit { unique_id: ClipId, at_frame: i64 },
    /// Two adjacent clips into one; split restores them.
    #[serde(rename = "clip.join")]
    ClipJoin {
        unique_id: ClipId,
        other_unique_id: String,
    },
    /// New in/out points on one clip; the gap is the caller's.
    #[serde(rename = "clip.trim")]
    ClipTrim {
        unique_id: ClipId,
        in_frame: i64,
        out_frame: i64,
    },
    /// Shift every later clip on the track by delta frames.
    #[serde(rename = "clip.ripple")]
    ClipRipple {
        unique_id: ClipId,
        delta_frames: i64,
    },
    /// Roll the source window; duration and position unchanged.
    #[serde(rename = "clip.roll")]
    ClipRoll {
        unique_id: ClipId,
        delta_frames: i64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        source_in: Option<i64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        source_out: Option<i64>,
    },
    /// Slip the source under a fixed timeline window.
    #[serde(rename = "clip.slip")]
    ClipSlip {
        unique_id: ClipId,
        delta_frames: i64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        source_in: Option<i64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        source_out: Option<i64>,
    },
    /// Move the clip, rolling its neighbours to close the gaps.
    #[serde(rename = "clip.slide")]
    ClipSlide { unique_id: ClipId, to_frame: i64 },
    /// An item to a different record position on the same row.
    #[serde(rename = "clip.move")]
    ClipMove { unique_id: ClipId, to_frame: i64 },
    /// A clip's source for another pool clip at the same span.
    #[serde(rename = "clip.replace")]
    ClipReplace {
        unique_id: ClipId,
        source_file: String,
    },
    #[serde(rename = "clip.set_enabled")]
    ClipSetEnabled { unique_id: ClipId, enabled: bool },
    /// Link clips so later edits move them together.
    #[serde(rename = "clip.link")]
    ClipLink {
        unique_id: ClipId,
        with_unique_ids: Vec<String>,
    },
    #[serde(rename = "clip.unlink")]
    ClipUnlink { unique_id: ClipId },

    // ── clip properties (domain: picture_transform) ────────────────
    /// Resolve's SmartReframe; judged on its framing properties.
    #[serde(rename = "clip.smart_reframe")]
    ClipSmartReframe {
        unique_id: ClipId,
        keys: Vec<String>,
        before: Value,
    },
    /// One transform property; the inverse restores the old value.
    #[serde(rename = "clip.set_property")]
    ClipSetProperty {
        unique_id: ClipId,
        key: String,
        value: Value,
    },

    // ── retime (domain: timeline_structure) ─────────────────────────
    /// Constant-percent speed; a ramp reaches the timeline as stepped
    /// segments, never as a curve.
    #[serde(rename = "retime")]
    Retime {
        unique_id: ClipId,
        percent: f64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        duration: Option<i64>,
    },
    /// Stepped constant-speed segments; the measured form of a speed ramp.
    #[serde(rename = "retime.segments")]
    RetimeSegments { unique_id: ClipId, segments: Value },

    // ── picture keyframes (domain: picture_transform) ───────────────
    /// A keyframed picture parameter, written through the transform
    /// write log.
    #[serde(rename = "picture.keyframe")]
    PictureKeyframe {
        unique_id: ClipId,
        param: String,
        keyframes: Value,
    },

    // ── audio (domain: audio_mix) ───────────────────────────────────
    /// Per-track voice isolation, replayed by the build.
    #[serde(rename = "voice_isolation")]
    VoiceIsolation { track_index: i64 },
    /// Per-clip gain in dB. No live-Resolve apply; the OTIO mix path
    /// writes it.
    #[serde(rename = "audio.gain")]
    AudioGain { unique_id: ClipId, db: f64 },
    /// Refuses by name: Resolve's OTIO carries no measured audio-pan
    /// parameter.
    #[serde(rename = "audio.pan")]
    AudioPan { unique_id: ClipId, pan: f64 },
    /// Fade in/out with curve. No live-Resolve apply.
    #[serde(rename = "audio.fade")]
    AudioFade {
        unique_id: ClipId,
        fade_in: Value,
        fade_out: Value,
    },
    /// A keyframed audio lane. No live-Resolve apply.
    #[serde(rename = "audio.automation")]
    AudioAutomation {
        unique_id: ClipId,
        param: String,
        keyframes: Value,
    },

    // ── grade (domain: color) ────────────────────────────────────
    /// A node LUT on anchored clips, replayed by the build.
    #[serde(rename = "clip_lut")]
    ClipLut { unique_id: ClipId, lut: Value },
    /// A declared LUT/PowerGrade, replayed by the build.
    #[serde(rename = "grade")]
    Grade { unique_id: ClipId, grade: Value },
    /// Declared, NOT executable: the Graph API has no AddNode.
    #[serde(rename = "grade.node_add")]
    GradeNodeAdd { unique_id: ClipId, node_index: i64 },
    /// Declared, NOT executable: the Graph API has no RemoveNode.
    #[serde(rename = "grade.node_remove")]
    GradeNodeRemove { unique_id: ClipId, node_index: i64 },
    /// Declared, NOT executable: the Graph API has no ReorderNode.
    #[serde(rename = "grade.node_reorder")]
    GradeNodeReorder {
        unique_id: ClipId,
        from_index: i64,
        to_index: i64,
    },
    /// SetCDL the four terms onto one named node; judged by GetCDL
    /// re-read. The only node-grade op the Resolve API can execute.
    #[serde(rename = "grade.node_set")]
    GradeNodeSet {
        unique_id: ClipId,
        node_index: i64,
        params: Value,
    },

    // ── power windows (domain: color) ──────────────────────────
    /// Declared, NOT executable: the Graph API exposes no window calls.
    #[serde(rename = "color.window.create")]
    WindowCreate {
        unique_id: ClipId,
        node_index: i64,
        shape: String,
        geometry: Value,
    },
    /// Declared, NOT executable: removing a window is the same GUI route.
    #[serde(rename = "color.window.delete")]
    WindowDelete { unique_id: ClipId, node_index: i64 },
    /// Declared, NOT executable: reshaping a window is the same GUI
    /// route as creating one.
    #[serde(rename = "color.window.modify")]
    WindowModify {
        unique_id: ClipId,
        node_index: i64,
        geometry: Value,
    },
    /// Declared, NOT executable: the tracker buttons are GUI-only.
    #[serde(rename = "color.window.track")]
    WindowTrack {
        unique_id: ClipId,
        node_index: i64,
        direction: String,
    },

    // ── qualifiers (domain: color) ─────────────────────────────
    /// Declared, NOT executable: the eyedropper pick is GUI-only.
    #[serde(rename = "color.qualifier.sample")]
    QualifierSample {
        unique_id: ClipId,
        node_index: i64,
        point: Value,
    },
    /// Declared, NOT executable: qualifier widths are Inspector-only.
    #[serde(rename = "color.qualifier.adjust")]
    QualifierAdjust {
        unique_id: ClipId,
        node_index: i64,
        params: Value,
    },

    // ── mask (domain: fusion) ──────────────────────────────────────
    /// Place a matte; the Fusion comp list verifies.
    #[serde(rename = "mask.create")]
    MaskCreate { unique_id: ClipId, matte: String },
    /// Remove a matte comp: the inverse of `mask.create`.
    #[serde(rename = "mask.delete")]
    MaskDelete { unique_id: ClipId, matte: String },
    /// Set a matte's static geometry; inputs it does not name keep their keys.
    #[serde(rename = "mask.edit")]
    MaskEdit {
        unique_id: ClipId,
        matte: String,
        geometry: Value,
    },
    /// Replace a matte's keyframes; an empty track unkeys it.
    #[serde(rename = "mask.track")]
    MaskTrack {
        unique_id: ClipId,
        matte: String,
        #[serde(default)]
        provider: Option<String>,
        track: Value,
        /// Static values for the inputs the track does not key.
        #[serde(default)]
        geometry: Option<Value>,
    },

    // ── multicam (domain: picture_transform) ────────────────────────
    /// A multicam angle change; the angle list re-read verifies.
    #[serde(rename = "multicam.angle")]
    MulticamAngle { unique_id: ClipId, angle_index: i64 },

    // ── compound (domain: timeline_structure) ────────────────────────
    /// Nest clips into one compound item; flatten restores them.
    #[serde(rename = "compound.create")]
    CompoundCreate {
        unique_id: ClipId,
        child_unique_ids: Vec<String>,
    },
    #[serde(rename = "compound.flatten")]
    CompoundFlatten { unique_id: ClipId },

    // ── tracks (domain: timeline_structure) ──────────────────────────
    /// A new named row; the touch-up's `add_row` views here.
    #[serde(rename = "track.add")]
    TrackAdd { track_type: String, name: String },
    #[serde(rename = "track.delete")]
    TrackDelete {
        track_type: String,
        track_index: i64,
    },
    #[serde(rename = "track.rename")]
    TrackRename {
        track_type: String,
        track_index: i64,
        name: String,
    },
    /// Move a row to a new index. A row carrying clips is refused:
    /// Resolve has no move-track call, and DeleteTrack destroys them.
    #[serde(rename = "track.reorder")]
    TrackReorder {
        track_type: String,
        from_index: i64,
        to_index: i64,
        name: String,
    },

    // ── overlays / graphics (domain: fusion) ────────────────────────
    /// A new overlay item on an overlay row.
    #[serde(rename = "overlay.add")]
    OverlayAdd {
        row: String,
        record_frame: i64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        duration: Option<i64>,
    },
    #[serde(rename = "overlay.remove")]
    OverlayRemove { unique_id: ClipId },
    /// An overlay's pixels for a re-rendered file.
    #[serde(rename = "overlay.swap_pixels")]
    OverlaySwapPixels { unique_id: ClipId, file: String },
    #[serde(rename = "overlay.resize")]
    OverlayResize {
        unique_id: ClipId,
        width: i64,
        height: i64,
    },
    #[serde(rename = "overlay.entry_motion")]
    OverlayEntryMotion { unique_id: ClipId, frames: i64 },
    /// Rebind the media windows on a freeze-hold tail without changing its picture.
    #[serde(rename = "hold.repair_window")]
    HoldRepairWindow { unique_id: ClipId },

    // ── projected captain edits (domain: picture_transform) ─────────
    /// Hold one Edit-page transform property.
    #[serde(rename = "transform_override")]
    TransformOverride {
        unique_id: ClipId,
        key: String,
        value: Value,
    },
    #[serde(rename = "span_retime")]
    SpanRetime { unique_id: ClipId, percent: f64 },
    /// Remove the speech (and so the picture) that says it.
    #[serde(rename = "drop_fragment")]
    DropFragment { unique_id: ClipId },
    /// Rewrite caption text.
    #[serde(rename = "caption_fix")]
    CaptionFix { unique_id: ClipId, text: String },
    /// Move a shared closer's start to the anchor's words.
    #[serde(rename = "redraw_closer")]
    RedrawCloser { unique_id: ClipId, start: f64 },

    // ── plan-level ops (delivered to planning nodes) ────────────────
    //
    // These are not timeline writes; a `plan_change` row is delivered to
    // the owning planner. Their inverse is the previous plan - semantic
    // undo (C-04) - so they are declared irreversible with the reason.
    /// Shapes the picture rows; delivered to the planner.
    #[serde(rename = "angle_plan")]
    AnglePlan { plan: Value },
    /// A plan-level change delivered to its owning step.
    #[serde(rename = "plan_change")]
    PlanChange { node: String, note: String },
    #[serde(rename = "transition")]
    Transition { carrier: Value, transition: Value },
    #[serde(rename = "shot_selection")]
    ShotSelection { plan: Value },
    #[serde(rename = "speech_selection")]
    SpeechSelection { plan: Value },
    #[serde(rename = "story_pacing")]
    StoryPacing { plan: Value },
    #[serde(rename = "music_selection")]
    MusicSelection { plan: Value },
    #[serde(rename = "audio_mix")]
    AudioMix { plan: Value },
    #[serde(rename = "look")]
    Look { plan: Value },
    #[serde(rename = "visual_effect")]
    VisualEffect { plan: Value },
    #[serde(rename = "sound_effect")]
    SoundEffect { plan: Value },
    #[serde(rename = "subject_effect")]
    SubjectEffect { plan: Value },
    #[serde(rename = "motion_graphic")]
    MotionGraphic { plan: Value },
    #[serde(rename = "brand_asset")]
    BrandAsset { plan: Value },
    #[serde(rename = "end_card")]
    EndCard { plan: Value },
}

impl EditOp {
    /// The algebra id (`"clip.trim"`), the op's identity.
    pub fn op_id(&self) -> &'static str {
        match self {
            EditOp::MarkerAdd { .. } => "marker.add",
            EditOp::MarkerDelete { .. } => "marker.delete",
            EditOp::ClipMarkerAdd { .. } => "clip_marker.add",
            EditOp::ClipMarkerDelete { .. } => "clip_marker.delete",
            EditOp::ClipDelete { .. } => "clip.delete",
            EditOp::ClipInsert { .. } => "clip.insert",
            EditOp::ClipSplit { .. } => "clip.split",
            EditOp::ClipJoin { .. } => "clip.join",
            EditOp::ClipTrim { .. } => "clip.trim",
            EditOp::ClipRipple { .. } => "clip.ripple",
            EditOp::ClipRoll { .. } => "clip.roll",
            EditOp::ClipSlip { .. } => "clip.slip",
            EditOp::ClipSlide { .. } => "clip.slide",
            EditOp::ClipMove { .. } => "clip.move",
            EditOp::ClipReplace { .. } => "clip.replace",
            EditOp::ClipSetEnabled { .. } => "clip.set_enabled",
            EditOp::ClipLink { .. } => "clip.link",
            EditOp::ClipUnlink { .. } => "clip.unlink",
            EditOp::ClipSmartReframe { .. } => "clip.smart_reframe",
            EditOp::ClipSetProperty { .. } => "clip.set_property",
            EditOp::Retime { .. } => "retime",
            EditOp::RetimeSegments { .. } => "retime.segments",
            EditOp::PictureKeyframe { .. } => "picture.keyframe",
            EditOp::VoiceIsolation { .. } => "voice_isolation",
            EditOp::AudioGain { .. } => "audio.gain",
            EditOp::AudioPan { .. } => "audio.pan",
            EditOp::AudioFade { .. } => "audio.fade",
            EditOp::AudioAutomation { .. } => "audio.automation",
            EditOp::ClipLut { .. } => "clip_lut",
            EditOp::Grade { .. } => "grade",
            EditOp::GradeNodeAdd { .. } => "grade.node_add",
            EditOp::GradeNodeRemove { .. } => "grade.node_remove",
            EditOp::GradeNodeReorder { .. } => "grade.node_reorder",
            EditOp::GradeNodeSet { .. } => "grade.node_set",
            EditOp::WindowCreate { .. } => "color.window.create",
            EditOp::WindowDelete { .. } => "color.window.delete",
            EditOp::WindowModify { .. } => "color.window.modify",
            EditOp::WindowTrack { .. } => "color.window.track",
            EditOp::QualifierSample { .. } => "color.qualifier.sample",
            EditOp::QualifierAdjust { .. } => "color.qualifier.adjust",
            EditOp::MaskCreate { .. } => "mask.create",
            EditOp::MaskDelete { .. } => "mask.delete",
            EditOp::MaskEdit { .. } => "mask.edit",
            EditOp::MaskTrack { .. } => "mask.track",
            EditOp::MulticamAngle { .. } => "multicam.angle",
            EditOp::CompoundCreate { .. } => "compound.create",
            EditOp::CompoundFlatten { .. } => "compound.flatten",
            EditOp::TrackAdd { .. } => "track.add",
            EditOp::TrackDelete { .. } => "track.delete",
            EditOp::TrackRename { .. } => "track.rename",
            EditOp::TrackReorder { .. } => "track.reorder",
            EditOp::OverlayAdd { .. } => "overlay.add",
            EditOp::OverlayRemove { .. } => "overlay.remove",
            EditOp::OverlaySwapPixels { .. } => "overlay.swap_pixels",
            EditOp::OverlayResize { .. } => "overlay.resize",
            EditOp::OverlayEntryMotion { .. } => "overlay.entry_motion",
            EditOp::HoldRepairWindow { .. } => "hold.repair_window",
            EditOp::TransformOverride { .. } => "transform_override",
            EditOp::SpanRetime { .. } => "span_retime",
            EditOp::DropFragment { .. } => "drop_fragment",
            EditOp::CaptionFix { .. } => "caption_fix",
            EditOp::RedrawCloser { .. } => "redraw_closer",
            EditOp::AnglePlan { .. } => "angle_plan",
            EditOp::PlanChange { .. } => "plan_change",
            EditOp::Transition { .. } => "transition",
            EditOp::ShotSelection { .. } => "shot_selection",
            EditOp::SpeechSelection { .. } => "speech_selection",
            EditOp::StoryPacing { .. } => "story_pacing",
            EditOp::MusicSelection { .. } => "music_selection",
            EditOp::AudioMix { .. } => "audio_mix",
            EditOp::Look { .. } => "look",
            EditOp::VisualEffect { .. } => "visual_effect",
            EditOp::SoundEffect { .. } => "sound_effect",
            EditOp::SubjectEffect { .. } => "subject_effect",
            EditOp::MotionGraphic { .. } => "motion_graphic",
            EditOp::BrandAsset { .. } => "brand_asset",
            EditOp::EndCard { .. } => "end_card",
        }
    }
}

/// What undoes an op: an algebra id, or an explicit irreversible reason.
///
/// `op` names the algebra id that undoes this one (paired ops name each
/// other). `self_inverse` marks a value setter: the inverse is the same
/// op carrying the previous value. An irreversible op carries a reason.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Inverse {
    #[serde(default)]
    pub op: Option<String>,
    #[serde(default)]
    pub self_inverse: bool,
    #[serde(default)]
    pub reason: String,
}

impl Inverse {
    /// Genuinely irreversible: no paired op and not a value setter.
    pub fn irreversible(&self) -> bool {
        self.op.is_none() && !self.self_inverse
    }
}

/// The patch-layer view of an algebra op (`edit_algebra.Operation`): what
/// EditPatch schedules and judges. The callables (`span`, `apply`,
/// `verify`, `key`) are Python-side and do not cross the wire; the
/// declarative fields do.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Operation {
    /// The conflict domain (`edit_patch.CONFLICT_DOMAINS`).
    pub domain: String,
    /// The op's `required` tuple.
    pub required: Vec<String>,
    /// `replace` | `commutative` | `exclusive` | `ordered`.
    pub merge: String,
    #[serde(default)]
    pub item: bool,
    #[serde(default = "default_temporal")]
    pub temporal: String,
}

fn default_temporal() -> String {
    "local".to_string()
}

/// A condition on a snapshot, judged before (preconditions) or after
/// (postconditions) the write. One of a fixed vocabulary, with its keys.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind")]
pub enum Condition {
    #[serde(rename = "clip_present")]
    ClipPresent { unique_id: ClipId },
    #[serde(rename = "clip_absent")]
    ClipAbsent { unique_id: ClipId },
    /// The optional fields are matched only when present
    /// (`edit_algebra._matches`).
    #[serde(rename = "marker_present")]
    MarkerPresent {
        frame: i64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        color: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        name: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        note: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        duration: Option<i64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        custom_data: Option<String>,
    },
    #[serde(rename = "marker_absent")]
    MarkerAbsent { frame: i64 },
    #[serde(rename = "clip_marker_present")]
    ClipMarkerPresent {
        unique_id: ClipId,
        frame: i64,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        color: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        name: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        note: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        duration: Option<i64>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        custom_data: Option<String>,
    },
    #[serde(rename = "clip_marker_absent")]
    ClipMarkerAbsent { unique_id: ClipId, frame: i64 },
    #[serde(rename = "clip_transform_equals")]
    ClipTransformEquals {
        unique_id: ClipId,
        properties: std::collections::BTreeMap<String, Value>,
    },
    #[serde(rename = "duration_unchanged")]
    DurationUnchanged {},
    #[serde(rename = "clip_count")]
    ClipCount {
        track_type: String,
        track_index: i64,
        count: i64,
    },
}

/// The unit of work a Resolve timeline accepts.
///
/// A declaration must be true (AGENTS.md 3): every operation's conflict
/// domain must be declared, and every frame an operation touches must sit
/// inside a declared span. `validate` refuses a patch that says less than
/// it does, checked against the BASE snapshot before Resolve is touched.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct EditPatch {
    /// Idempotency key, chosen by the author.
    pub id: String,
    /// The Resolve project's EXACT name (the open one).
    pub project: String,
    /// The timeline's EXACT name.
    pub timeline: String,
    /// The shadow generation the patch was planned against; 0 is never
    /// valid.
    pub base_generation: Generation,
    /// A capability id (`capabilities.get` must know it).
    pub capability: CapabilityId,
    /// `[[start, end), ...]` in timeline frames.
    pub affected_spans: Vec<(i64, i64)>,
    /// A subset of `edit_patch.CONFLICT_DOMAINS`.
    pub conflict_domains: Vec<String>,
    pub operations: Vec<EditOp>,
    #[serde(default)]
    pub preconditions: Vec<Condition>,
    #[serde(default)]
    pub postconditions: Vec<Condition>,
    /// The plan's commit.
    #[serde(default)]
    pub base_git_sha: String,
    /// Project Model revision read while constructing this edit.
    #[serde(default)]
    pub expected_model_revision: Option<u64>,
}
