//! The condition vocabulary, ported from `library/tools/edit_patch.py`
//! (`CONDITIONS` and the `_cond_*` judges). A condition is judged on a
//! snapshot - preconditions on the live base, postconditions on the
//! read-back - and `_judge` returns every failure, never stopping at the
//! first.

use ren_protocol::edit::Condition;
use serde_json::Value;

use crate::snapshot::{matches_marker, same_value, Snapshot};

/// The duration of a snapshot in frames (`edit_patch._duration`).
fn duration(snapshot: &Snapshot) -> i64 {
    snapshot.end_frame - snapshot.start_frame
}

fn cond_clip_present(condition: &Condition, snap: &Snapshot) -> Option<String> {
    let Condition::ClipPresent { unique_id } = condition else {
        return None;
    };
    if snap.clip(&unique_id.0).is_none() {
        return Some(format!("clip {unique_id:?} is not on the timeline"));
    }
    None
}

fn cond_clip_absent(condition: &Condition, snap: &Snapshot) -> Option<String> {
    let Condition::ClipAbsent { unique_id } = condition else {
        return None;
    };
    if snap.clip(&unique_id.0).is_some() {
        return Some(format!("clip {unique_id:?} is on the timeline"));
    }
    None
}

fn cond_marker_present(condition: &Condition, snap: &Snapshot) -> Option<String> {
    let Condition::MarkerPresent { frame, .. } = condition else {
        return None;
    };
    let found = snap
        .timeline_marker(*frame)
        .filter(|marker| matches_marker(marker, &condition_to_value(condition)));
    if found.is_none() {
        return Some(format!("no matching timeline marker at frame {frame}"));
    }
    None
}

fn cond_marker_absent(condition: &Condition, snap: &Snapshot) -> Option<String> {
    let Condition::MarkerAbsent { frame } = condition else {
        return None;
    };
    if snap.timeline_marker(*frame).is_some() {
        return Some(format!("a timeline marker is at frame {frame}"));
    }
    None
}

fn cond_clip_marker_present(condition: &Condition, snap: &Snapshot) -> Option<String> {
    let Condition::ClipMarkerPresent {
        unique_id, frame, ..
    } = condition
    else {
        return None;
    };
    let found = snap
        .clip_marker(&unique_id.0, *frame)
        .filter(|marker| matches_marker(marker, &condition_to_value(condition)));
    if found.is_none() {
        return Some(format!(
            "no matching clip marker at source frame {frame} on {unique_id:?}"
        ));
    }
    None
}

fn cond_clip_marker_absent(condition: &Condition, snap: &Snapshot) -> Option<String> {
    let Condition::ClipMarkerAbsent { unique_id, frame } = condition else {
        return None;
    };
    if snap.clip_marker(&unique_id.0, *frame).is_some() {
        return Some(format!(
            "a clip marker is at source frame {frame} on {unique_id:?}"
        ));
    }
    None
}

fn cond_clip_transform_equals(condition: &Condition, snap: &Snapshot) -> Option<String> {
    let Condition::ClipTransformEquals {
        unique_id,
        properties,
    } = condition
    else {
        return None;
    };
    let clip = match snap.clip(&unique_id.0) {
        Some(clip) => clip,
        None => return Some(format!("clip {unique_id:?} is not on the timeline")),
    };
    for (key, want) in properties {
        let got = clip.transform.get(key);
        let same = match (got, want) {
            (Some(g), w) => same_value(g, w),
            (None, Value::Null) => true,
            _ => false,
        };
        if !same {
            return Some(format!(
                "clip {unique_id:?} reads {key}={got:?}, not {want:?}"
            ));
        }
    }
    None
}

fn cond_duration_unchanged(
    condition: &Condition,
    snap: &Snapshot,
    base: &Snapshot,
) -> Option<String> {
    let Condition::DurationUnchanged {} = condition else {
        return None;
    };
    if duration(snap) != duration(base) {
        return Some(format!(
            "duration is {} frames, was {}",
            duration(snap),
            duration(base)
        ));
    }
    None
}

fn cond_clip_count(condition: &Condition, snap: &Snapshot) -> Option<String> {
    let Condition::ClipCount {
        track_type,
        track_index,
        count,
    } = condition
    else {
        return None;
    };
    let found = snap
        .clips()
        .iter()
        .filter(|clip| clip.track_type == *track_type && clip.track_index == *track_index)
        .count() as i64;
    if found != *count {
        return Some(format!(
            "{track_type}{track_index} holds {found} clips, not {count}"
        ));
    }
    None
}

fn condition_to_value(condition: &Condition) -> Value {
    serde_json::to_value(condition).unwrap_or(Value::Null)
}

/// Judge one condition: None when it holds, the failure text when it
/// does not.
pub fn judge(condition: &Condition, snap: &Snapshot, base: &Snapshot) -> Option<String> {
    match condition {
        Condition::ClipPresent { .. } => cond_clip_present(condition, snap),
        Condition::ClipAbsent { .. } => cond_clip_absent(condition, snap),
        Condition::MarkerPresent { .. } => cond_marker_present(condition, snap),
        Condition::MarkerAbsent { .. } => cond_marker_absent(condition, snap),
        Condition::ClipMarkerPresent { .. } => cond_clip_marker_present(condition, snap),
        Condition::ClipMarkerAbsent { .. } => cond_clip_marker_absent(condition, snap),
        Condition::ClipTransformEquals { .. } => cond_clip_transform_equals(condition, snap),
        Condition::DurationUnchanged {} => cond_duration_unchanged(condition, snap, base),
        Condition::ClipCount { .. } => cond_clip_count(condition, snap),
    }
}

/// The condition's kind, as its wire spelling.
pub fn condition_kind(condition: &Condition) -> &'static str {
    match condition {
        Condition::ClipPresent { .. } => "clip_present",
        Condition::ClipAbsent { .. } => "clip_absent",
        Condition::MarkerPresent { .. } => "marker_present",
        Condition::MarkerAbsent { .. } => "marker_absent",
        Condition::ClipMarkerPresent { .. } => "clip_marker_present",
        Condition::ClipMarkerAbsent { .. } => "clip_marker_absent",
        Condition::ClipTransformEquals { .. } => "clip_transform_equals",
        Condition::DurationUnchanged {} => "duration_unchanged",
        Condition::ClipCount { .. } => "clip_count",
    }
}

/// Judge every condition, returning each failure as
/// `"{kind}: {failure}"` (`edit_patch._judge`).
pub fn judge_all(conditions: &[Condition], snap: &Snapshot, base: &Snapshot) -> Vec<String> {
    conditions
        .iter()
        .filter_map(|condition| {
            judge(condition, snap, base)
                .map(|failure| format!("{}: {failure}", condition_kind(condition)))
        })
        .collect()
}
