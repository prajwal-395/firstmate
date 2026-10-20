//! The edit algebra: one NLE-neutral vocabulary of reversible timeline
//! operations, typed.
//!
//! Modeled from `library/tools/edit_algebra.py`. Every op carries its
//! conflict domain, required fields, write key, merge semantics,
//! temporal effect and inverse; the `span` and `verify` dispatches below
//! match over the `EditOp` enum EXHAUSTIVELY - no wildcard arm - so
//! adding a variant fails compilation until every behaviour is defined
//! (the captain's "the compiler becomes another contract test").

use ren_protocol::edit::EditOp;
use ren_protocol::Inverse;

use crate::snapshot::{Snapshot, FLOAT_TOLERANCE};

/// Merge semantics, least to most constraining
/// (`patch_algebra.MERGE_SEMANTICS`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MergeSemantics {
    Commutative,
    Ordered,
    Replace,
    Exclusive,
}

impl MergeSemantics {
    pub fn as_str(&self) -> &'static str {
        match self {
            MergeSemantics::Commutative => "commutative",
            MergeSemantics::Ordered => "ordered",
            MergeSemantics::Replace => "replace",
            MergeSemantics::Exclusive => "exclusive",
        }
    }

    pub fn parse(text: &str) -> Option<MergeSemantics> {
        match text {
            "commutative" => Some(MergeSemantics::Commutative),
            "ordered" => Some(MergeSemantics::Ordered),
            "replace" => Some(MergeSemantics::Replace),
            "exclusive" => Some(MergeSemantics::Exclusive),
            _ => None,
        }
    }

    /// The position in `MERGE_SEMANTICS`; a pair's verdict is its worst.
    pub fn rank(&self) -> usize {
        match self {
            MergeSemantics::Commutative => 0,
            MergeSemantics::Ordered => 1,
            MergeSemantics::Replace => 2,
            MergeSemantics::Exclusive => 3,
        }
    }
}

/// Temporal effect, least to most constraining
/// (`patch_algebra.TEMPORAL_EFFECTS`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TemporalEffect {
    Local,
    Ripple,
    Global,
}

impl TemporalEffect {
    pub fn as_str(&self) -> &'static str {
        match self {
            TemporalEffect::Local => "local",
            TemporalEffect::Ripple => "ripple",
            TemporalEffect::Global => "global",
        }
    }

    pub fn parse(text: &str) -> Option<TemporalEffect> {
        match text {
            "local" => Some(TemporalEffect::Local),
            "ripple" => Some(TemporalEffect::Ripple),
            "global" => Some(TemporalEffect::Global),
            _ => None,
        }
    }

    pub fn rank(&self) -> usize {
        match self {
            TemporalEffect::Local => 0,
            TemporalEffect::Ripple => 1,
            TemporalEffect::Global => 2,
        }
    }
}

/// One part of a write key. A key is a sequence of these; two keys MEET
/// when one is a prefix of the other (`patch_algebra._meet`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum KeyPart {
    Text(String),
    Num(i64),
}

/// The declaration of one algebra op: everything `edit_algebra.AlgebraOp`
/// carries except the callables (span/verify/key are the dispatches in
/// this module; apply stays Python).
#[derive(Clone, Debug)]
pub struct OpDeclaration {
    pub id: &'static str,
    pub domain: &'static str,
    pub required: &'static [&'static str],
    pub merge: MergeSemantics,
    pub temporal: TemporalEffect,
    pub item: bool,
    /// Whether the op has a native apply and so may be scheduled by
    /// EditPatch (`edit_algebra.patch_op_ids`).
    pub has_apply: bool,
    pub inverse: Inverse,
}

/// The union vocabulary, in declaration order. The `id` is the key.
///
/// Built once (the `Inverse` carries `String`, so the table cannot be a
/// `const`) and cached in a `OnceLock`.
pub(crate) fn algebra() -> &'static [OpDeclaration] {
    static ALGEBRA: std::sync::OnceLock<Vec<OpDeclaration>> = std::sync::OnceLock::new();
    ALGEBRA.get_or_init(|| vec![
    // ── markers (domain: markers) ──────────────────────────────────
    OpDeclaration {
        id: "marker.add", domain: "markers",
        required: &["frame", "color", "name"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: false, has_apply: true,
        inverse: Inverse { op: Some(String::from("marker.delete")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "marker.delete", domain: "markers",
        required: &["frame"],
        merge: MergeSemantics::Commutative, temporal: TemporalEffect::Local,
        item: false, has_apply: true,
        inverse: Inverse { op: Some(String::from("marker.add")), self_inverse: false, reason: String::from("re-adds the deleted marker's fields") },
    },
    OpDeclaration {
        id: "clip_marker.add", domain: "markers",
        required: &["unique_id", "frame", "color", "name"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("clip_marker.delete")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "clip_marker.delete", domain: "markers",
        required: &["unique_id", "frame"],
        merge: MergeSemantics::Commutative, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("clip_marker.add")), self_inverse: false, reason: String::from("re-adds the deleted marker's fields") },
    },
    // ── clip structure (domain: timeline_structure) ─────────────────
    OpDeclaration {
        id: "clip.delete", domain: "timeline_structure",
        required: &["unique_id"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("clip.insert")), self_inverse: false, reason: String::from("re-inserts the deleted source range") },
    },
    OpDeclaration {
        id: "clip.insert", domain: "timeline_structure",
        required: &["unique_id", "track_type", "track_index", "record_in", "record_out", "media_pool_item"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: false, has_apply: true,
        inverse: Inverse { op: Some(String::from("clip.delete")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "clip.split", domain: "timeline_structure",
        required: &["unique_id", "at_frame"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: Some(String::from("clip.join")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "clip.join", domain: "timeline_structure",
        required: &["unique_id", "other_unique_id"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: Some(String::from("clip.split")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "clip.trim", domain: "timeline_structure",
        required: &["unique_id", "in_frame", "out_frame"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous in/out points") },
    },
    OpDeclaration {
        id: "clip.ripple", domain: "timeline_structure",
        required: &["unique_id", "delta_frames"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Ripple,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the opposite delta") },
    },
    OpDeclaration {
        id: "clip.roll", domain: "timeline_structure",
        required: &["unique_id", "delta_frames"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the negated delta") },
    },
    OpDeclaration {
        id: "clip.slip", domain: "timeline_structure",
        required: &["unique_id", "delta_frames"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the negated delta") },
    },
    OpDeclaration {
        id: "clip.slide", domain: "timeline_structure",
        required: &["unique_id", "to_frame"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Ripple,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse moves the clip back") },
    },
    OpDeclaration {
        id: "clip.move", domain: "timeline_structure",
        required: &["unique_id", "to_frame"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse moves the clip back") },
    },
    OpDeclaration {
        id: "clip.replace", domain: "timeline_structure",
        required: &["unique_id", "source_file"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous source") },
    },
    OpDeclaration {
        id: "clip.set_enabled", domain: "timeline_structure",
        required: &["unique_id", "enabled"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous state") },
    },
    OpDeclaration {
        id: "clip.link", domain: "timeline_structure",
        required: &["unique_id", "with_unique_ids"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("clip.unlink")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "clip.unlink", domain: "timeline_structure",
        required: &["unique_id"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("clip.link")), self_inverse: false, reason: String::new() },
    },
    // ── clip properties (domain: picture_transform) ────────────────
    OpDeclaration {
        id: "clip.smart_reframe", domain: "picture_transform",
        required: &["unique_id", "keys", "before"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("clip.set_property")), self_inverse: false, reason: String::from("restores the measured framing properties") },
    },
    OpDeclaration {
        id: "clip.set_property", domain: "picture_transform",
        required: &["unique_id", "key", "value"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous value") },
    },
    // ── retime (domain: timeline_structure) ─────────────────────────
    OpDeclaration {
        id: "retime", domain: "timeline_structure",
        required: &["unique_id", "percent"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Ripple,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous speed") },
    },
    OpDeclaration {
        id: "retime.segments", domain: "timeline_structure",
        required: &["unique_id", "segments"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Ripple,
        item: true, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous speed") },
    },
    // ── picture keyframes (domain: picture_transform) ───────────────
    OpDeclaration {
        id: "picture.keyframe", domain: "picture_transform",
        required: &["unique_id", "param", "keyframes"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous keyframes") },
    },
    // ── audio (domain: audio_mix) ───────────────────────────────────
    OpDeclaration {
        id: "voice_isolation", domain: "audio_mix",
        required: &["track_index"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse restores the pre-isolation state") },
    },
    OpDeclaration {
        id: "audio.gain", domain: "audio_mix",
        required: &["unique_id", "db"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous dB") },
    },
    OpDeclaration {
        id: "audio.pan", domain: "audio_mix",
        required: &["unique_id", "pan"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous pan") },
    },
    OpDeclaration {
        id: "audio.fade", domain: "audio_mix",
        required: &["unique_id", "fade_in", "fade_out"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous envelope") },
    },
    OpDeclaration {
        id: "audio.automation", domain: "audio_mix",
        required: &["unique_id", "param", "keyframes"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous keyframes") },
    },
    // ── grade (domain: color) ────────────────────────────────────
    OpDeclaration {
        id: "clip_lut", domain: "color",
        required: &["unique_id", "lut"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous LUT") },
    },
    OpDeclaration {
        id: "grade", domain: "color",
        required: &["unique_id", "grade"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous grade") },
    },
    OpDeclaration {
        id: "grade.node_add", domain: "color",
        required: &["unique_id", "node_index"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("grade.node_remove")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "grade.node_remove", domain: "color",
        required: &["unique_id", "node_index"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("grade.node_add")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "grade.node_reorder", domain: "color",
        required: &["unique_id", "from_index", "to_index"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse swaps the nodes back") },
    },
    OpDeclaration {
        id: "grade.node_set", domain: "color",
        required: &["unique_id", "node_index", "params"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous node grade") },
    },
    // ── power windows (domain: color) ──────────────────────────
    OpDeclaration {
        id: "color.window.create", domain: "color",
        required: &["unique_id", "node_index", "shape", "geometry"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: Some(String::from("color.window.delete")), self_inverse: false, reason: String::from("delete removes the window create placed") },
    },
    OpDeclaration {
        id: "color.window.delete", domain: "color",
        required: &["unique_id", "node_index"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: Some(String::from("color.window.create")), self_inverse: false, reason: String::from("create draws the window delete removed, with its shape and geometry") },
    },
    OpDeclaration {
        id: "color.window.modify", domain: "color",
        required: &["unique_id", "node_index", "geometry"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous geometry") },
    },
    OpDeclaration {
        id: "color.window.track", domain: "color",
        required: &["unique_id", "node_index", "direction"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous window track") },
    },
    // ── qualifiers (domain: color) ─────────────────────────────
    OpDeclaration {
        id: "color.qualifier.sample", domain: "color",
        required: &["unique_id", "node_index", "point"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous sampled range") },
    },
    OpDeclaration {
        id: "color.qualifier.adjust", domain: "color",
        required: &["unique_id", "node_index", "params"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous qualifier params") },
    },
    // ── mask (domain: fusion) ──────────────────────────────────────
    OpDeclaration {
        id: "mask.create", domain: "fusion",
        required: &["unique_id", "matte"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("mask.delete")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "mask.delete", domain: "fusion",
        required: &["unique_id", "matte"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: Some(String::from("mask.create")), self_inverse: false, reason: String::from("then mask.edit and mask.track restore its geometry and keys") },
    },
    OpDeclaration {
        id: "mask.edit", domain: "fusion",
        required: &["unique_id", "matte", "geometry"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous geometry") },
    },
    OpDeclaration {
        id: "mask.track", domain: "fusion",
        required: &["unique_id", "matte", "track"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous keyframes (empty means none) and the previous geometry") },
    },
    // ── multicam (domain: picture_transform) ────────────────────────
    OpDeclaration {
        id: "multicam.angle", domain: "picture_transform",
        required: &["unique_id", "angle_index"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous angle") },
    },
    OpDeclaration {
        id: "multicam.select_angle", domain: "picture_transform",
        required: &["unique_id", "angle_index"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous angle") },
    },
    // ── compound (domain: timeline_structure) ────────────────────────
    OpDeclaration {
        id: "compound.create", domain: "timeline_structure",
        required: &["unique_id", "child_unique_ids"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: Some(String::from("compound.flatten")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "compound.flatten", domain: "timeline_structure",
        required: &["unique_id"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: Some(String::from("compound.create")), self_inverse: false, reason: String::new() },
    },
    // ── tracks (domain: timeline_structure) ──────────────────────────
    OpDeclaration {
        id: "track.add", domain: "timeline_structure",
        required: &["track_type", "name"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: false, has_apply: true,
        inverse: Inverse { op: Some(String::from("track.delete")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "track.delete", domain: "timeline_structure",
        required: &["track_type", "track_index"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: false, has_apply: true,
        inverse: Inverse { op: Some(String::from("track.add")), self_inverse: false, reason: String::from("re-adds the row with its name") },
    },
    OpDeclaration {
        id: "track.rename", domain: "timeline_structure",
        required: &["track_type", "track_index", "name"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: false, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous name") },
    },
    OpDeclaration {
        id: "track.reorder", domain: "timeline_structure",
        required: &["track_type", "from_index", "to_index", "name"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: false, has_apply: true,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous index") },
    },
    // ── overlays / graphics (domain: fusion) ────────────────────────
    OpDeclaration {
        id: "overlay.add", domain: "fusion",
        required: &["row", "record_frame"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: Some(String::from("overlay.remove")), self_inverse: false, reason: String::new() },
    },
    OpDeclaration {
        id: "overlay.remove", domain: "fusion",
        required: &["unique_id"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: Some(String::from("overlay.add")), self_inverse: false, reason: String::from("re-places the removed item") },
    },
    OpDeclaration {
        id: "overlay.swap_pixels", domain: "fusion",
        required: &["unique_id", "file"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous file") },
    },
    OpDeclaration {
        id: "overlay.resize", domain: "fusion",
        required: &["unique_id", "width", "height"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous size") },
    },
    OpDeclaration {
        id: "overlay.position", domain: "fusion",
        required: &["unique_id", "dx", "dy"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous position") },
    },
    OpDeclaration {
        id: "overlay.entry_motion", domain: "fusion",
        required: &["unique_id", "frames"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse clears the animation") },
    },
    OpDeclaration {
        id: "hold.repair_window", domain: "fusion",
        required: &["unique_id"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous window") },
    },
    // ── projected captain edits (domain: picture_transform) ─────────
    OpDeclaration {
        id: "transform_override", domain: "picture_transform",
        required: &["unique_id", "key", "value"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous value") },
    },
    OpDeclaration {
        id: "span_retime", domain: "timeline_structure",
        required: &["unique_id", "percent"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Ripple,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous ranges") },
    },
    OpDeclaration {
        id: "drop_fragment", domain: "timeline_structure",
        required: &["unique_id"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: Some(String::from("clip.insert")), self_inverse: false, reason: String::from("re-inserts the dropped passage") },
    },
    OpDeclaration {
        id: "caption_fix", domain: "captions",
        required: &["unique_id", "text"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous text") },
    },
    OpDeclaration {
        id: "caption.style", domain: "captions",
        required: &["unique_id", "style"],
        merge: MergeSemantics::Replace, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous style") },
    },
    OpDeclaration {
        id: "redraw_closer", domain: "timeline_structure",
        required: &["unique_id", "start"],
        merge: MergeSemantics::Exclusive, temporal: TemporalEffect::Local,
        item: true, has_apply: false,
        inverse: Inverse { op: None, self_inverse: true, reason: String::from("the inverse carries the previous start") },
    },
    // ── plan-level ops (delivered to planning nodes) ────────────────
    OpDeclaration {
        id: "angle_plan", domain: "picture_transform",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse; its inverse is the previous plan (C-04)") },
    },
    OpDeclaration {
        id: "plan_change", domain: "timeline_structure",
        required: &["node", "note"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a carrier delivered to a planning node; its inverse is the previous plan (C-04)") },
    },
    OpDeclaration {
        id: "transition", domain: "fusion",
        required: &["carrier", "transition"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("transition deletion has no qualified compensation") },
    },
    OpDeclaration {
        id: "shot_selection", domain: "timeline_structure",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "speech_selection", domain: "timeline_structure",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "story_pacing", domain: "timeline_structure",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "music_selection", domain: "audio_mix",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "audio_mix", domain: "audio_mix",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "look", domain: "color",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "visual_effect", domain: "fusion",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "sound_effect", domain: "audio_mix",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "subject_effect", domain: "fusion",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "motion_graphic", domain: "fusion",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "brand_asset", domain: "fusion",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    OpDeclaration {
        id: "end_card", domain: "fusion",
        required: &["plan"],
        merge: MergeSemantics::Ordered, temporal: TemporalEffect::Local,
        item: false, has_apply: false,
        inverse: Inverse { op: None, self_inverse: false, reason: String::from("a planner re-run has no timeline inverse (C-04)") },
    },
    ])
}

/// The declaration for one algebra id, or None.
pub fn declaration(op_id: &str) -> Option<&'static OpDeclaration> {
    algebra().iter().find(|decl| decl.id == op_id)
}

/// Every algebra id, in declaration order.
pub fn op_ids() -> Vec<&'static str> {
    algebra().iter().map(|decl| decl.id).collect()
}

/// The ids EditPatch may schedule: every op with a native apply
/// (`edit_algebra.patch_op_ids`).
pub fn patch_op_ids() -> Vec<&'static str> {
    algebra()
        .iter()
        .filter(|decl| decl.has_apply)
        .map(|decl| decl.id)
        .collect()
}

// ── Span ─────────────────────────────────────────────────────────────

fn item_span(base: &Snapshot, unique_id: &str) -> Option<(i64, i64)> {
    base.clip(unique_id)
        .map(|clip| (clip.record_in, clip.record_out))
}

fn track_span(base: &Snapshot, track_type: &str, index: i64) -> Option<(i64, i64)> {
    let track = base.track(track_type, index)?;
    if track.clips.is_empty() {
        return Some((base.start_frame, base.end_frame));
    }
    let start = track.clips.iter().map(|c| c.record_in).min()?;
    let end = track.clips.iter().map(|c| c.record_out).max()?;
    Some((start, end))
}

fn timeline_span(base: &Snapshot) -> (i64, i64) {
    (base.start_frame, base.end_frame)
}

fn union_span(spans: &[Option<(i64, i64)>]) -> Option<(i64, i64)> {
    let spans: Vec<(i64, i64)> = spans.iter().copied().flatten().collect();
    if spans.is_empty() {
        return None;
    }
    Some((
        spans.iter().map(|s| s.0).min()?,
        spans.iter().map(|s| s.1).max()?,
    ))
}

fn ripple_span(base: &Snapshot, unique_id: &str) -> Option<(i64, i64)> {
    let clip = base.clip(unique_id)?;
    Some((clip.record_in, base.end_frame))
}

fn slide_span(base: &Snapshot, unique_id: &str, to_frame: i64) -> Option<(i64, i64)> {
    let clip = base.clip(unique_id)?;
    let start = clip.record_in.min(to_frame);
    let end = clip.record_out.max(to_frame + clip.duration);
    Some((start, end))
}

/// The frames an op touches over a base snapshot, or None when the op
/// addresses nothing on this base. EXHAUSTIVE over `EditOp`.
pub fn span(op: &EditOp, base: &Snapshot) -> Option<(i64, i64)> {
    match op {
        // ── markers ──────────────────────────────────────────────
        EditOp::MarkerAdd {
            frame, duration, ..
        } => Some((*frame, *frame + duration.unwrap_or(1))),
        EditOp::MarkerDelete { frame } => Some((*frame, *frame + 1)),
        EditOp::ClipMarkerAdd { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::ClipMarkerDelete { unique_id, .. } => item_span(base, &unique_id.0),

        // ── clip structure ──────────────────────────────────────
        EditOp::ClipDelete { unique_id } => item_span(base, &unique_id.0),
        EditOp::ClipInsert {
            record_in,
            record_out,
            ..
        } => Some((*record_in, *record_out)),
        EditOp::ClipSplit { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::ClipJoin {
            unique_id,
            other_unique_id,
        } => union_span(&[
            item_span(base, &unique_id.0),
            item_span(base, other_unique_id),
        ]),
        EditOp::ClipTrim { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::ClipRipple { unique_id, .. } => ripple_span(base, &unique_id.0),
        EditOp::ClipRoll { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::ClipSlip { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::ClipSlide {
            unique_id,
            to_frame,
        } => slide_span(base, &unique_id.0, *to_frame),
        EditOp::ClipMove {
            unique_id,
            to_frame,
        } => slide_span(base, &unique_id.0, *to_frame),
        EditOp::ClipReplace { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::ClipSetEnabled { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::ClipLink {
            unique_id,
            with_unique_ids,
        } => {
            let mut spans = vec![item_span(base, &unique_id.0)];
            for uid in with_unique_ids {
                spans.push(item_span(base, uid));
            }
            union_span(&spans)
        }
        EditOp::ClipUnlink { unique_id } => item_span(base, &unique_id.0),

        // ── clip properties ─────────────────────────────────────
        EditOp::ClipSmartReframe { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::ClipSetProperty { unique_id, .. } => item_span(base, &unique_id.0),

        // ── retime ──────────────────────────────────────────────
        EditOp::Retime { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::RetimeSegments { unique_id, .. } => item_span(base, &unique_id.0),

        // ── picture keyframes ───────────────────────────────────
        EditOp::PictureKeyframe { unique_id, .. } => item_span(base, &unique_id.0),

        // ── audio ───────────────────────────────────────────────
        EditOp::VoiceIsolation { track_index } => track_span(base, "audio", *track_index),
        EditOp::AudioGain { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::AudioPan { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::AudioFade { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::AudioAutomation { unique_id, .. } => item_span(base, &unique_id.0),

        // ── grade ───────────────────────────────────────────────
        EditOp::ClipLut { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::Grade { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::GradeNodeAdd { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::GradeNodeRemove { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::GradeNodeReorder { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::GradeNodeSet { unique_id, .. } => item_span(base, &unique_id.0),

        // ── power windows ──────────────────────────────────────
        EditOp::WindowCreate { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::WindowDelete { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::WindowModify { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::WindowTrack { unique_id, .. } => item_span(base, &unique_id.0),

        // ── qualifiers ─────────────────────────────────────────
        EditOp::QualifierSample { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::QualifierAdjust { unique_id, .. } => item_span(base, &unique_id.0),

        // ── mask ────────────────────────────────────────────────
        EditOp::MaskCreate { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::MaskDelete { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::MaskEdit { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::MaskTrack { unique_id, .. } => item_span(base, &unique_id.0),

        // ── multicam ────────────────────────────────────────────
        EditOp::MulticamAngle { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::MulticamSelectAngle { unique_id, .. } => item_span(base, &unique_id.0),

        // ── compound ────────────────────────────────────────────
        EditOp::CompoundCreate {
            child_unique_ids, ..
        } => {
            let spans: Vec<Option<(i64, i64)>> = child_unique_ids
                .iter()
                .map(|uid| item_span(base, uid))
                .collect();
            union_span(&spans)
        }
        EditOp::CompoundFlatten { unique_id } => item_span(base, &unique_id.0),

        // ── tracks ──────────────────────────────────────────────
        EditOp::TrackAdd { .. } => Some(timeline_span(base)),
        EditOp::TrackDelete {
            track_type,
            track_index,
        } => track_span(base, track_type, *track_index),
        EditOp::TrackRename {
            track_type,
            track_index,
            ..
        } => track_span(base, track_type, *track_index),
        EditOp::TrackReorder { .. } => Some(timeline_span(base)),

        // ── overlays / graphics ─────────────────────────────────
        EditOp::OverlayAdd {
            record_frame,
            duration,
            ..
        } => Some((*record_frame, *record_frame + duration.unwrap_or(1))),
        EditOp::OverlayRemove { unique_id } => item_span(base, &unique_id.0),
        EditOp::OverlaySwapPixels { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::OverlayResize { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::OverlayPosition { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::OverlayEntryMotion { unique_id, .. } => item_span(base, &unique_id.0),
        // Rebinds the existing hold item's Fusion comp windows in place.
        EditOp::HoldRepairWindow { unique_id } => item_span(base, &unique_id.0),

        // ── projected captain edits ────────────────────────────
        EditOp::TransformOverride { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::SpanRetime { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::DropFragment { unique_id } => item_span(base, &unique_id.0),
        EditOp::CaptionFix { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::CaptionStyle { unique_id, .. } => item_span(base, &unique_id.0),
        EditOp::RedrawCloser { unique_id, .. } => item_span(base, &unique_id.0),

        // ── plan-level ops: they address no timeline frames ────
        EditOp::AnglePlan { .. } => None,
        EditOp::PlanChange { .. } => None,
        EditOp::Transition { .. } => None,
        EditOp::ShotSelection { .. } => None,
        EditOp::SpeechSelection { .. } => None,
        EditOp::StoryPacing { .. } => None,
        EditOp::MusicSelection { .. } => None,
        EditOp::AudioMix { .. } => None,
        EditOp::Look { .. } => None,
        EditOp::VisualEffect { .. } => None,
        EditOp::SoundEffect { .. } => None,
        EditOp::SubjectEffect { .. } => None,
        EditOp::MotionGraphic { .. } => None,
        EditOp::BrandAsset { .. } => None,
        EditOp::EndCard { .. } => None,
    }
}

// ── Key ──────────────────────────────────────────────────────────────

/// What an op writes, as a key. Two keys MEET when one is a prefix of
/// the other. EXHAUSTIVE over `EditOp`.
pub fn key(op: &EditOp) -> Vec<KeyPart> {
    match op {
        // ── markers ──────────────────────────────────────────────
        EditOp::MarkerAdd { frame, .. } => {
            vec![KeyPart::Text("marker".to_string()), KeyPart::Num(*frame)]
        }
        EditOp::MarkerDelete { frame } => {
            vec![KeyPart::Text("marker".to_string()), KeyPart::Num(*frame)]
        }
        EditOp::ClipMarkerAdd {
            unique_id, frame, ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("marker".to_string()),
            KeyPart::Num(*frame),
        ],
        EditOp::ClipMarkerDelete { unique_id, frame } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("marker".to_string()),
            KeyPart::Num(*frame),
        ],

        // ── clip structure ──────────────────────────────────────
        EditOp::ClipDelete { unique_id } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipInsert { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipSplit { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipJoin { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipTrim { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipRipple { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipRoll { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipSlip { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipSlide { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipMove { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipReplace { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipSetEnabled { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("enabled".to_string()),
        ],
        EditOp::ClipLink { unique_id, .. } => {
            vec![
                KeyPart::Text("link".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::ClipUnlink { unique_id } => {
            vec![
                KeyPart::Text("link".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }

        // ── clip properties ─────────────────────────────────────
        EditOp::ClipSmartReframe { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("transform".to_string()),
        ],
        EditOp::ClipSetProperty { unique_id, key, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text(key.clone()),
        ],

        // ── retime ──────────────────────────────────────────────
        EditOp::Retime { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::RetimeSegments { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }

        // ── picture keyframes ───────────────────────────────────
        EditOp::PictureKeyframe {
            unique_id, param, ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("keyframe".to_string()),
            KeyPart::Text(param.clone()),
        ],

        // ── audio ───────────────────────────────────────────────
        EditOp::VoiceIsolation { track_index } => vec![
            KeyPart::Text("audio".to_string()),
            KeyPart::Text("isolation".to_string()),
            KeyPart::Num(*track_index),
        ],
        EditOp::AudioGain { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("audio".to_string()),
            KeyPart::Text("gain".to_string()),
        ],
        EditOp::AudioPan { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("audio".to_string()),
            KeyPart::Text("pan".to_string()),
        ],
        EditOp::AudioFade { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("audio".to_string()),
            KeyPart::Text("fade".to_string()),
        ],
        EditOp::AudioAutomation {
            unique_id, param, ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("audio".to_string()),
            KeyPart::Text("automation".to_string()),
            KeyPart::Text(param.clone()),
        ],

        // ── grade ───────────────────────────────────────────────
        EditOp::ClipLut { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("color".to_string()),
            KeyPart::Text("lut".to_string()),
        ],
        EditOp::Grade { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("color".to_string()),
            KeyPart::Text("grade".to_string()),
        ],
        EditOp::GradeNodeAdd {
            unique_id,
            node_index,
            ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("grade".to_string()),
            KeyPart::Num(*node_index),
        ],
        EditOp::GradeNodeRemove {
            unique_id,
            node_index,
            ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("grade".to_string()),
            KeyPart::Num(*node_index),
        ],
        EditOp::GradeNodeReorder {
            unique_id,
            from_index,
            ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("grade".to_string()),
            KeyPart::Num(*from_index),
        ],
        EditOp::GradeNodeSet {
            unique_id,
            node_index,
            ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("grade".to_string()),
            KeyPart::Num(*node_index),
        ],

        // ── power windows ──────────────────────────────────────
        EditOp::WindowCreate {
            unique_id,
            node_index,
            ..
        }
        | EditOp::WindowDelete {
            unique_id,
            node_index,
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("window".to_string()),
            KeyPart::Num(*node_index),
        ],
        EditOp::WindowModify {
            unique_id,
            node_index,
            ..
        }
        | EditOp::WindowTrack {
            unique_id,
            node_index,
            ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("window".to_string()),
            KeyPart::Num(*node_index),
        ],

        // ── qualifiers ─────────────────────────────────────────
        EditOp::QualifierSample {
            unique_id,
            node_index,
            ..
        }
        | EditOp::QualifierAdjust {
            unique_id,
            node_index,
            ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("qualifier".to_string()),
            KeyPart::Num(*node_index),
        ],

        // ── mask ────────────────────────────────────────────────
        EditOp::MaskCreate { unique_id, matte }
        | EditOp::MaskDelete { unique_id, matte }
        | EditOp::MaskEdit {
            unique_id, matte, ..
        }
        | EditOp::MaskTrack {
            unique_id, matte, ..
        } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("mask".to_string()),
            KeyPart::Text(matte.clone()),
        ],

        // ── multicam ────────────────────────────────────────────
        EditOp::MulticamAngle { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("angle".to_string()),
        ],
        EditOp::MulticamSelectAngle { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("angle".to_string()),
        ],

        // ── compound ────────────────────────────────────────────
        EditOp::CompoundCreate { unique_id, .. } => vec![
            KeyPart::Text("compound".to_string()),
            KeyPart::Text(unique_id.0.clone()),
        ],
        EditOp::CompoundFlatten { unique_id } => vec![
            KeyPart::Text("compound".to_string()),
            KeyPart::Text(unique_id.0.clone()),
        ],

        // ── tracks ──────────────────────────────────────────────
        EditOp::TrackAdd { track_type, .. } => vec![
            KeyPart::Text("track".to_string()),
            KeyPart::Text(track_type.clone()),
            KeyPart::Num(-1),
        ],
        EditOp::TrackDelete {
            track_type,
            track_index,
        } => vec![
            KeyPart::Text("track".to_string()),
            KeyPart::Text(track_type.clone()),
            KeyPart::Num(*track_index),
        ],
        EditOp::TrackRename {
            track_type,
            track_index,
            ..
        } => vec![
            KeyPart::Text("track".to_string()),
            KeyPart::Text(track_type.clone()),
            KeyPart::Num(*track_index),
        ],
        EditOp::TrackReorder {
            track_type,
            from_index,
            ..
        } => vec![
            KeyPart::Text("track".to_string()),
            KeyPart::Text(track_type.clone()),
            KeyPart::Num(*from_index),
        ],

        // ── overlays / graphics ─────────────────────────────────
        EditOp::OverlayAdd {
            row, record_frame, ..
        } => vec![
            KeyPart::Text("overlay".to_string()),
            KeyPart::Text(row.clone()),
            KeyPart::Num(*record_frame),
        ],
        EditOp::OverlayRemove { unique_id } => vec![
            KeyPart::Text("overlay".to_string()),
            KeyPart::Text(unique_id.0.clone()),
        ],
        EditOp::OverlaySwapPixels { unique_id, .. } => vec![
            KeyPart::Text("overlay".to_string()),
            KeyPart::Text(unique_id.0.clone()),
        ],
        EditOp::OverlayResize { unique_id, .. } => vec![
            KeyPart::Text("overlay".to_string()),
            KeyPart::Text(unique_id.0.clone()),
        ],
        EditOp::OverlayPosition { unique_id, .. } => vec![
            KeyPart::Text("overlay".to_string()),
            KeyPart::Text(unique_id.0.clone()),
        ],
        EditOp::OverlayEntryMotion { unique_id, .. } => vec![
            KeyPart::Text("overlay".to_string()),
            KeyPart::Text(unique_id.0.clone()),
        ],
        EditOp::HoldRepairWindow { unique_id } => vec![
            KeyPart::Text("hold".to_string()),
            KeyPart::Text(unique_id.0.clone()),
        ],

        // ── projected captain edits ────────────────────────────
        EditOp::TransformOverride { unique_id, key, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text(key.clone()),
        ],
        EditOp::SpanRetime { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::DropFragment { unique_id } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }
        EditOp::CaptionFix { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("caption".to_string()),
        ],
        EditOp::CaptionStyle { unique_id, .. } => vec![
            KeyPart::Text("clip".to_string()),
            KeyPart::Text(unique_id.0.clone()),
            KeyPart::Text("caption".to_string()),
            KeyPart::Text("style".to_string()),
        ],
        EditOp::RedrawCloser { unique_id, .. } => {
            vec![
                KeyPart::Text("clip".to_string()),
                KeyPart::Text(unique_id.0.clone()),
            ]
        }

        // ── plan-level ops ─────────────────────────────────────
        EditOp::AnglePlan { .. } => plan_key("angle_plan"),
        EditOp::PlanChange { .. } => plan_key("plan_change"),
        EditOp::Transition { .. } => plan_key("transition"),
        EditOp::ShotSelection { .. } => plan_key("shot_selection"),
        EditOp::SpeechSelection { .. } => plan_key("speech_selection"),
        EditOp::StoryPacing { .. } => plan_key("story_pacing"),
        EditOp::MusicSelection { .. } => plan_key("music_selection"),
        EditOp::AudioMix { .. } => plan_key("audio_mix"),
        EditOp::Look { .. } => plan_key("look"),
        EditOp::VisualEffect { .. } => plan_key("visual_effect"),
        EditOp::SoundEffect { .. } => plan_key("sound_effect"),
        EditOp::SubjectEffect { .. } => plan_key("subject_effect"),
        EditOp::MotionGraphic { .. } => plan_key("motion_graphic"),
        EditOp::BrandAsset { .. } => plan_key("brand_asset"),
        EditOp::EndCard { .. } => plan_key("end_card"),
    }
}

fn plan_key(id: &str) -> Vec<KeyPart> {
    vec![
        KeyPart::Text("plan".to_string()),
        KeyPart::Text(id.to_string()),
    ]
}

/// The key as string parts, for the Python boundary and the parity
/// harness (numbers rendered as decimal strings).
pub fn key_strings(op: &EditOp) -> Vec<String> {
    key(op)
        .into_iter()
        .map(|part| match part {
            KeyPart::Text(text) => text,
            KeyPart::Num(num) => num.to_string(),
        })
        .collect()
}

/// Whether two write keys MEET: one is a prefix of the other
/// (`patch_algebra._meet`).
pub fn meet(a: &[KeyPart], b: &[KeyPart]) -> bool {
    let n = a.len().min(b.len());
    a[..n] == b[..n]
}

/// The float tolerance, re-exported for the verify judgements.
pub fn float_tolerance() -> f64 {
    FLOAT_TOLERANCE
}

/// The clip an op addresses, when it addresses one by id, as text (the
/// id's wire spelling). EXHAUSTIVE.
pub fn op_unique_id(op: &EditOp) -> Option<String> {
    match op {
        EditOp::ClipMarkerAdd { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipMarkerDelete { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipDelete { unique_id } => Some(unique_id.0.clone()),
        EditOp::ClipInsert { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipSplit { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipJoin { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipTrim { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipRipple { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipRoll { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipSlip { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipSlide { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipMove { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipReplace { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipSetEnabled { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipLink { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipUnlink { unique_id } => Some(unique_id.0.clone()),
        EditOp::ClipSmartReframe { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipSetProperty { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::Retime { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::RetimeSegments { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::PictureKeyframe { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::AudioGain { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::AudioPan { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::AudioFade { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::AudioAutomation { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::ClipLut { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::Grade { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::GradeNodeAdd { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::GradeNodeRemove { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::GradeNodeReorder { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::GradeNodeSet { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::WindowCreate { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::WindowDelete { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::WindowModify { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::WindowTrack { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::QualifierSample { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::QualifierAdjust { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::MaskCreate { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::MaskDelete { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::MaskEdit { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::MaskTrack { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::MulticamAngle { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::MulticamSelectAngle { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::CompoundCreate { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::CompoundFlatten { unique_id } => Some(unique_id.0.clone()),
        EditOp::OverlayRemove { unique_id } => Some(unique_id.0.clone()),
        EditOp::OverlaySwapPixels { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::OverlayResize { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::OverlayPosition { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::OverlayEntryMotion { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::HoldRepairWindow { unique_id } => Some(unique_id.0.clone()),
        EditOp::TransformOverride { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::SpanRetime { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::DropFragment { unique_id } => Some(unique_id.0.clone()),
        EditOp::CaptionFix { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::CaptionStyle { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::RedrawCloser { unique_id, .. } => Some(unique_id.0.clone()),
        EditOp::MarkerAdd { .. } => None,
        EditOp::MarkerDelete { .. } => None,
        EditOp::VoiceIsolation { .. } => None,
        EditOp::TrackAdd { .. } => None,
        EditOp::TrackDelete { .. } => None,
        EditOp::TrackRename { .. } => None,
        EditOp::TrackReorder { .. } => None,
        EditOp::OverlayAdd { .. } => None,
        EditOp::AnglePlan { .. } => None,
        EditOp::PlanChange { .. } => None,
        EditOp::Transition { .. } => None,
        EditOp::ShotSelection { .. } => None,
        EditOp::SpeechSelection { .. } => None,
        EditOp::StoryPacing { .. } => None,
        EditOp::MusicSelection { .. } => None,
        EditOp::AudioMix { .. } => None,
        EditOp::Look { .. } => None,
        EditOp::VisualEffect { .. } => None,
        EditOp::SoundEffect { .. } => None,
        EditOp::SubjectEffect { .. } => None,
        EditOp::MotionGraphic { .. } => None,
        EditOp::BrandAsset { .. } => None,
        EditOp::EndCard { .. } => None,
    }
}

/// The frame an op addresses, when it addresses one. EXHAUSTIVE.
pub fn op_frame(op: &EditOp) -> Option<i64> {
    match op {
        EditOp::MarkerAdd { frame, .. } => Some(*frame),
        EditOp::MarkerDelete { frame } => Some(*frame),
        EditOp::ClipMarkerAdd { frame, .. } => Some(*frame),
        EditOp::ClipMarkerDelete { frame, .. } => Some(*frame),
        EditOp::ClipSplit { .. } => None,
        EditOp::ClipInsert { .. } => None,
        EditOp::ClipDelete { .. } => None,
        EditOp::ClipJoin { .. } => None,
        EditOp::ClipTrim { .. } => None,
        EditOp::ClipRipple { .. } => None,
        EditOp::ClipRoll { .. } => None,
        EditOp::ClipSlip { .. } => None,
        EditOp::ClipSlide { .. } => None,
        EditOp::ClipMove { .. } => None,
        EditOp::ClipReplace { .. } => None,
        EditOp::ClipSetEnabled { .. } => None,
        EditOp::ClipLink { .. } => None,
        EditOp::ClipUnlink { .. } => None,
        EditOp::ClipSmartReframe { .. } => None,
        EditOp::ClipSetProperty { .. } => None,
        EditOp::Retime { .. } => None,
        EditOp::RetimeSegments { .. } => None,
        EditOp::PictureKeyframe { .. } => None,
        EditOp::VoiceIsolation { .. } => None,
        EditOp::AudioGain { .. } => None,
        EditOp::AudioPan { .. } => None,
        EditOp::AudioFade { .. } => None,
        EditOp::AudioAutomation { .. } => None,
        EditOp::ClipLut { .. } => None,
        EditOp::Grade { .. } => None,
        EditOp::GradeNodeAdd { .. } => None,
        EditOp::GradeNodeRemove { .. } => None,
        EditOp::GradeNodeReorder { .. } => None,
        EditOp::GradeNodeSet { .. } => None,
        EditOp::WindowCreate { .. } => None,
        EditOp::WindowDelete { .. } => None,
        EditOp::WindowModify { .. } => None,
        EditOp::WindowTrack { .. } => None,
        EditOp::QualifierSample { .. } => None,
        EditOp::QualifierAdjust { .. } => None,
        EditOp::MaskCreate { .. } => None,
        EditOp::MaskDelete { .. } => None,
        EditOp::MaskEdit { .. } => None,
        EditOp::MaskTrack { .. } => None,
        EditOp::MulticamAngle { .. } => None,
        EditOp::MulticamSelectAngle { .. } => None,
        EditOp::CompoundCreate { .. } => None,
        EditOp::CompoundFlatten { .. } => None,
        EditOp::TrackAdd { .. } => None,
        EditOp::TrackDelete { .. } => None,
        EditOp::TrackRename { .. } => None,
        EditOp::TrackReorder { .. } => None,
        EditOp::OverlayAdd { .. } => None,
        EditOp::OverlayRemove { .. } => None,
        EditOp::OverlaySwapPixels { .. } => None,
        EditOp::OverlayResize { .. } => None,
        EditOp::OverlayPosition { .. } => None,
        EditOp::OverlayEntryMotion { .. } => None,
        // Fusion comp window state is not represented in Snapshot; the
        // staging operation performs its own stable re-read verification.
        EditOp::HoldRepairWindow { .. } => None,
        EditOp::TransformOverride { .. } => None,
        EditOp::SpanRetime { .. } => None,
        EditOp::DropFragment { .. } => None,
        EditOp::CaptionFix { .. } => None,
        EditOp::CaptionStyle { .. } => None,
        EditOp::RedrawCloser { .. } => None,
        EditOp::AnglePlan { .. } => None,
        EditOp::PlanChange { .. } => None,
        EditOp::Transition { .. } => None,
        EditOp::ShotSelection { .. } => None,
        EditOp::SpeechSelection { .. } => None,
        EditOp::StoryPacing { .. } => None,
        EditOp::MusicSelection { .. } => None,
        EditOp::AudioMix { .. } => None,
        EditOp::Look { .. } => None,
        EditOp::VisualEffect { .. } => None,
        EditOp::SoundEffect { .. } => None,
        EditOp::SubjectEffect { .. } => None,
        EditOp::MotionGraphic { .. } => None,
        EditOp::BrandAsset { .. } => None,
        EditOp::EndCard { .. } => None,
    }
}
