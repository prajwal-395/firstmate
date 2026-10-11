//! Patch validation, ported from `library/tools/edit_patch.py`
//! (`validate`). A declaration must be true (AGENTS.md 3): every
//! operation's conflict domain must be declared, and every frame an
//! operation touches must sit inside a declared span.
//!
//! The capability-registry checks (the capability exists, declares
//! patch semantics, and has a compatible execution phase) stay Python:
//! they read the registry. This module checks the patch against the
//! base snapshot and the capability's DECLARED semantics.
//!
//! The patch arrives as ren-protocol's `EditPatch`: an op that is
//! unknown, or lacks a required field, or carries a malformed condition
//! has already failed to deserialize at the boundary - a refusal, with
//! the deserialization error as the reason. What remains here is the
//! SEMANTIC checking the typed form cannot do: domains, declared
//! operations, transform keys, smart-reframe keys and span coverage.

use ren_protocol::edit::{EditOp, EditPatch};
use serde::Deserialize;
use serde_json::Value;

use crate::algebra::{algebra, op_unique_id, span, MergeSemantics};
use crate::snapshot::Snapshot;
use crate::verify::SMART_REFRAME_KEYS;

/// What a patch may declare it touches (`edit_patch.CONFLICT_DOMAINS`).
pub const CONFLICT_DOMAINS: &[&str] = &[
    "timeline_structure",
    "captions",
    "picture_transform",
    "color",
    "fusion",
    "audio_mix",
    "markers",
];

/// Keys `clip.set_property` may write (`edit_patch.TRANSFORM_KEYS`).
pub const TRANSFORM_KEYS: &[&str] = &[
    "Pan",
    "Tilt",
    "ZoomX",
    "ZoomY",
    "ZoomGang",
    "RotationAngle",
    "AnchorPointX",
    "AnchorPointY",
    "Pitch",
    "Yaw",
    "FlipX",
    "FlipY",
    "CropLeft",
    "CropRight",
    "CropTop",
    "CropBottom",
    "CropSoftness",
    "CropRetain",
    "Opacity",
];

/// Mode keys `clip.set_property` may write (`edit_patch.MODE_KEYS`).
/// Resolve modes that are not magnitudes. `Scaling` is the scaling MODE
/// (1 = Crop - native pixels, centred), not a magnification.
pub const MODE_KEYS: &[&str] = &["Scaling"];

/// The capability's declared patch semantics, as `validate` reads them.
#[derive(Deserialize)]
pub struct SemanticsDict {
    pub operations: Vec<String>,
    pub conflict_domains: Vec<String>,
}

/// Whether `span` sits inside one of the declared spans
/// (`edit_patch._covered`).
pub fn covered(span: (i64, i64), spans: &[(i64, i64)]) -> bool {
    spans.iter().any(|s| s.0 <= span.0 && span.1 <= s.1)
}

/// Refuse a patch whose declarations are not true of what it does.
/// `semantics` is the capability's declared patch semantics
/// (`{operations, conflict_domains}`).
pub fn validate_patch(patch: &EditPatch, base: &Snapshot, semantics: &Value) -> Result<(), String> {
    if patch.id.is_empty() || patch.base_generation.0 < 1 {
        return Err("has no id or no base generation".into());
    }
    let semantics: SemanticsDict = serde_json::from_value(semantics.clone())
        .map_err(|_| "the capability's patch semantics are malformed".to_string())?;

    let beyond: Vec<&String> = patch
        .conflict_domains
        .iter()
        .filter(|d| !semantics.conflict_domains.contains(d))
        .collect();
    if !beyond.is_empty() {
        let mut names: Vec<&String> = beyond;
        names.sort();
        return Err(format!(
            "declares domains {names:?}, beyond what the capability declares"
        ));
    }
    if patch.operations.is_empty() || patch.affected_spans.is_empty() {
        return Err("declares no operations or no affected spans".into());
    }
    if patch.affected_spans.iter().any(|s| s.0 >= s.1) {
        return Err(format!(
            "declares an empty span in {:?}",
            patch.affected_spans
        ));
    }
    let unknown: Vec<&String> = patch
        .conflict_domains
        .iter()
        .filter(|d| !CONFLICT_DOMAINS.contains(&d.as_str()))
        .collect();
    if !unknown.is_empty() {
        let mut names: Vec<&String> = unknown;
        names.sort();
        return Err(format!("declares unknown conflict domains {names:?}"));
    }

    let mut insert_spans: std::collections::HashMap<String, Option<(i64, i64)>> =
        std::collections::HashMap::new();
    for (index, op) in patch.operations.iter().enumerate() {
        let op_id = op.op_id();
        let Some(decl) = algebra().iter().find(|d| d.id == op_id) else {
            return Err(format!("operation {index} is {op_id:?}"));
        };
        if !decl.has_apply {
            return Err(format!(
                "operation {index} ({op_id}) is not a measured, read-back-judged write"
            ));
        }
        if !semantics.operations.iter().any(|o| o == decl.id) {
            return Err(format!(
                "operation {index} ({op_id}) is not one the capability declares"
            ));
        }
        if !patch.conflict_domains.iter().any(|d| d == decl.domain) {
            return Err(format!(
                "operation {index} ({op_id}) writes {:?}, which the patch does not declare",
                decl.domain
            ));
        }
        if op_id == "clip.set_property" {
            let EditOp::ClipSetProperty { key, .. } = op else {
                unreachable!()
            };
            if !TRANSFORM_KEYS.contains(&key.as_str()) && !MODE_KEYS.contains(&key.as_str()) {
                return Err(format!("operation {index} writes property {key:?}"));
            }
        }
        if op_id == "clip.smart_reframe" {
            let EditOp::ClipSmartReframe { keys, before, .. } = op else {
                unreachable!()
            };
            if keys.iter().map(|s| s.as_str()).collect::<Vec<_>>() != SMART_REFRAME_KEYS {
                return Err(format!("operation {index} names reframe keys {keys:?}"));
            }
            let complete = before
                .as_object()
                .map(|m| SMART_REFRAME_KEYS.iter().all(|k| m.contains_key(*k)))
                .unwrap_or(false);
            if !complete {
                return Err(format!(
                    "operation {index} has no complete framing read-back"
                ));
            }
        }
        let op_span = if op_id == "clip.insert" {
            let unique_id = op_unique_id(op).unwrap_or_default();
            let s = span(op, base);
            insert_spans.insert(unique_id, s);
            s
        } else if let Some(uid) = op_unique_id(op) {
            match insert_spans.get(&uid) {
                Some(cached) => *cached,
                None => span(op, base),
            }
        } else {
            span(op, base)
        };
        let Some(op_span) = op_span else {
            return Err(format!(
                "operation {index} ({op_id}) targets clip {:?}, which is not exactly once on the base generation",
                op_unique_id(op).unwrap_or_else(|| "None".into())
            ));
        };
        if !covered(op_span, &patch.affected_spans) {
            return Err(format!(
                "operation {index} ({op_id}) touches frames [{}, {}], outside the declared spans",
                op_span.0, op_span.1
            ));
        }
    }
    Ok(())
}

/// The merge-semantics rank helper, re-exported for the compose module.
pub fn merge_rank(semantics: &str) -> Option<usize> {
    MergeSemantics::parse(semantics).map(|m| m.rank())
}
