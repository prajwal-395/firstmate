//! The read-back verify judgements, ported from
//! `library/tools/edit_algebra.py`. Each judges an after-snapshot: did
//! the op's effect land? They are pure reads of the shadow snapshot, so
//! they are testable without Resolve - and the `verify` dispatch below
//! matches over `EditOp` EXHAUSTIVELY.

use ren_protocol::edit::EditOp;
use serde_json::Value;

use crate::algebra::KeyPart;
use crate::cdl::compare_cdl;
use crate::snapshot::{matches_marker, same_value, Snapshot};

/// The four framing properties SmartReframe is judged on
/// (`edit_algebra.SMART_REFRAME_KEYS`).
pub const SMART_REFRAME_KEYS: [&str; 4] = ["Pan", "Tilt", "ZoomX", "ZoomY"];

/// Format a JSON value the way Python's `repr` does, so a read-back
/// text matches the Python implementation's wording: numbers keep their
/// decimal (`99.0`, not `Number(99.0)`), strings use single quotes,
/// `None`/`True`/`False` are Python's.
fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(flag) => flag.to_string(),
        Value::Number(num) => {
            if let Some(int) = num.as_i64() {
                int.to_string()
            } else {
                format!("{:?}", num.as_f64().unwrap_or(0.0))
            }
        }
        Value::String(text) => format!("'{text}'"),
        Value::Array(_) | Value::Object(_) => value.to_string(),
    }
}

fn verify_marker_add(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::MarkerAdd { frame, .. } = op else {
        return None;
    };
    let marker = after.timeline_marker(*frame);
    let want = op_to_value(op);
    if marker.is_none() || !matches_marker(marker.unwrap(), &want) {
        let color = want.get("color").and_then(|v| v.as_str()).unwrap_or("");
        let name = want.get("name").and_then(|v| v.as_str()).unwrap_or("");
        return Some(format!(
            "no {color} marker {name:?} reads back at frame {frame}"
        ));
    }
    None
}

fn verify_marker_delete(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::MarkerDelete { frame } = op else {
        return None;
    };
    if after.timeline_marker(*frame).is_some() {
        return Some(format!(
            "a timeline marker still reads back at frame {frame}"
        ));
    }
    None
}

fn verify_clip_marker_add(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipMarkerAdd {
        unique_id, frame, ..
    } = op
    else {
        return None;
    };
    let marker = after.clip_marker(&unique_id.0, *frame);
    let want = op_to_value(op);
    if marker.is_none() || !matches_marker(marker.unwrap(), &want) {
        let name = want.get("name").and_then(|v| v.as_str()).unwrap_or("");
        return Some(format!(
            "no matching clip marker {name:?} reads back on {unique_id:?} at source frame {frame}"
        ));
    }
    None
}

fn verify_clip_marker_delete(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipMarkerDelete { unique_id, frame } = op else {
        return None;
    };
    if after.clip_marker(&unique_id.0, *frame).is_some() {
        return Some(format!(
            "a clip marker still reads back on {unique_id:?} at source frame {frame}"
        ));
    }
    None
}

fn verify_smart_reframe(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipSmartReframe {
        unique_id,
        keys,
        before,
    } = op
    else {
        return None;
    };
    let Some(clip) = after.clip(&unique_id.0) else {
        return Some(format!(
            "clip {unique_id:?} disappeared during Smart Reframe"
        ));
    };
    let unchanged = keys.iter().all(|key| {
        let want = before.get(key.as_str());
        let got = clip.transform.get(key.as_str());
        match (want, got) {
            (Some(w), Some(g)) => same_value(g, w),
            (None, None) => true,
            _ => false,
        }
    });
    if unchanged {
        return Some(format!(
            "SmartReframe reports success but Pan/Tilt/Zoom read back unchanged ({before})"
        ));
    }
    None
}

fn verify_enabled(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipSetEnabled { unique_id, enabled } = op else {
        return None;
    };
    let clip = after.clip(&unique_id.0);
    let got = clip.and_then(|c| c.enabled);
    if got != Some(*enabled) {
        return Some(format!(
            "clip {unique_id:?} reads enabled={}, not {enabled}",
            got.map(|b| b.to_string()).unwrap_or_else(|| "None".into())
        ));
    }
    None
}

fn verify_property(op: &EditOp, after: &Snapshot) -> Option<String> {
    // `transform_override` is judged by the same read-back as
    // `clip.set_property` (`edit_algebra`: both verify with
    // `_verify_property`).
    let (unique_id, key, value) = match op {
        EditOp::ClipSetProperty {
            unique_id,
            key,
            value,
        } => (unique_id, key, value),
        EditOp::TransformOverride {
            unique_id,
            key,
            value,
        } => (unique_id, key, value),
        _ => return None,
    };
    let clip = after.clip(&unique_id.0);
    let got = clip.and_then(|c| c.transform.get(key));
    let same = match (got, value) {
        (Some(g), v) => same_value(g, v),
        (None, Value::Null) => true,
        _ => false,
    };
    if !same {
        return Some(format!(
            "clip {unique_id:?} reads {key}={}, not {}",
            got.map(py_repr).unwrap_or_else(|| "None".into()),
            py_repr(value)
        ));
    }
    None
}

fn verify_delete(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipDelete { unique_id } = op else {
        return None;
    };
    if after.clip(&unique_id.0).is_some() {
        return Some(format!("clip {unique_id:?} still reads back"));
    }
    None
}

fn verify_split(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipSplit {
        unique_id,
        at_frame,
    } = op
    else {
        return None;
    };
    let clip = match after.clip(&unique_id.0) {
        Some(clip) => clip,
        None => return Some(format!("clip {unique_id:?} is not on the timeline")),
    };
    if clip.record_out != *at_frame {
        return Some(format!(
            "clip {unique_id:?} reads record_out={}, not {at_frame}: the split did not trim it to the first half",
            clip.record_out
        ));
    }
    None
}

fn verify_join(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipJoin {
        unique_id,
        other_unique_id,
    } = op
    else {
        return None;
    };
    if after.clip(&unique_id.0).is_none() {
        return Some(format!("clip {unique_id:?} is not on the timeline"));
    }
    if after.clip(other_unique_id).is_some() {
        return Some(format!(
            "clip {other_unique_id:?} still reads back: the join did not absorb it"
        ));
    }
    None
}

fn verify_trim(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipTrim {
        unique_id,
        in_frame,
        out_frame,
    } = op
    else {
        return None;
    };
    let clip = match after.clip(&unique_id.0) {
        Some(clip) => clip,
        None => return Some(format!("clip {unique_id:?} is not on the timeline")),
    };
    if clip.record_in != *in_frame || clip.record_out != *out_frame {
        return Some(format!(
            "clip {unique_id:?} reads {}..{}, not {in_frame}..{out_frame}",
            clip.record_in, clip.record_out
        ));
    }
    None
}

fn verify_ripple(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipRipple { unique_id, .. } = op else {
        return None;
    };
    if after.clip(&unique_id.0).is_none() {
        return Some(format!("clip {unique_id:?} is not on the timeline"));
    }
    None
}

fn verify_slide(op: &EditOp, after: &Snapshot) -> Option<String> {
    // `clip.move` is judged by the same read-back as `clip.slide`
    // (`edit_algebra`: both verify with `_verify_slide`).
    let (unique_id, to_frame) = match op {
        EditOp::ClipSlide {
            unique_id,
            to_frame,
        } => (unique_id, to_frame),
        EditOp::ClipMove {
            unique_id,
            to_frame,
        } => (unique_id, to_frame),
        _ => return None,
    };
    let clip = match after.clip(&unique_id.0) {
        Some(clip) => clip,
        None => return Some(format!("clip {unique_id:?} is not on the timeline")),
    };
    if clip.record_in != *to_frame {
        return Some(format!(
            "clip {unique_id:?} reads record_in={}, not {to_frame}",
            clip.record_in
        ));
    }
    None
}

/// Roll/slip: the source window reads back at the declared frames. The
/// op carries the expected `source_in`/`source_out`; without them the
/// read-back cannot judge.
fn verify_source_frames(op: &EditOp, after: &Snapshot) -> Option<String> {
    let (unique_id, source_in, source_out) = match op {
        EditOp::ClipRoll {
            unique_id,
            source_in,
            source_out,
            ..
        } => (unique_id, source_in, source_out),
        EditOp::ClipSlip {
            unique_id,
            source_in,
            source_out,
            ..
        } => (unique_id, source_in, source_out),
        _ => return None,
    };
    let (Some(source_in), Some(source_out)) = (source_in, source_out) else {
        return None;
    };
    let clip = match after.clip(&unique_id.0) {
        Some(clip) => clip,
        None => return Some(format!("clip {unique_id:?} is not on the timeline")),
    };
    if clip.source_in_frame != Some(*source_in) || clip.source_out_frame != Some(*source_out) {
        return Some(format!(
            "clip {unique_id:?} reads source {}..{}, not {source_in}..{source_out}",
            clip.source_in_frame
                .map(|v| v.to_string())
                .unwrap_or_else(|| "None".into()),
            clip.source_out_frame
                .map(|v| v.to_string())
                .unwrap_or_else(|| "None".into())
        ));
    }
    None
}

fn verify_replace(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::ClipReplace {
        unique_id,
        source_file,
    } = op
    else {
        return None;
    };
    let clip = match after.clip(&unique_id.0) {
        Some(clip) => clip,
        None => return Some(format!("clip {unique_id:?} is not on the timeline")),
    };
    if clip.source_file != *source_file {
        return Some(format!(
            "clip {unique_id:?} reads source '{}', not '{source_file}'",
            clip.source_file
        ));
    }
    None
}

fn verify_retime(op: &EditOp, after: &Snapshot) -> Option<String> {
    let (unique_id, duration) = match op {
        EditOp::Retime {
            unique_id,
            duration,
            ..
        } => (unique_id, duration),
        EditOp::SpanRetime { unique_id, .. } => (unique_id, &None),
        _ => return None,
    };
    let clip = match after.clip(&unique_id.0) {
        Some(clip) => clip,
        None => return Some(format!("clip {unique_id:?} is not on the timeline")),
    };
    if let Some(duration) = duration {
        if clip.duration != *duration {
            return Some(format!(
                "clip {unique_id:?} reads duration={}, not {duration}",
                clip.duration
            ));
        }
    }
    None
}

fn verify_retime_segments(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::RetimeSegments {
        unique_id,
        segments,
    } = op
    else {
        return None;
    };
    let segs = segments.as_array()?;
    let mut sorted: Vec<&Value> = segs.iter().collect();
    sorted.sort_by_key(|s| s.get("record_frame").and_then(|v| v.as_i64()).unwrap_or(0));
    let prefix = format!("{}-split-", unique_id.0);
    let mut pieces: Vec<&crate::snapshot::Clip> = after
        .clips()
        .into_iter()
        .filter(|c| c.unique_id == unique_id.0 || c.unique_id.starts_with(&prefix))
        .collect();
    pieces.sort_by_key(|c| c.record_in);
    if pieces.len() != sorted.len() {
        return Some(format!(
            "clip {unique_id:?} reads {} pieces, not {}",
            pieces.len(),
            sorted.len()
        ));
    }
    for (index, (piece, segment)) in pieces.iter().zip(sorted.iter()).enumerate() {
        let Some(speed) = piece.speed else {
            return Some(format!(
                "piece {index} of clip {unique_id:?} reads no speed"
            ));
        };
        let want = segment
            .get("percent")
            .and_then(|v| v.as_f64())
            .unwrap_or(0.0);
        if (speed - want).abs() > crate::snapshot::FLOAT_TOLERANCE {
            return Some(format!(
                "piece {index} of clip {unique_id:?} reads speed={speed:?}, not {want:?}"
            ));
        }
    }
    None
}

fn verify_picture_keyframe(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::PictureKeyframe {
        unique_id,
        param,
        keyframes,
    } = op
    else {
        return None;
    };
    let clip = match after.clip(&unique_id.0) {
        Some(clip) => clip,
        None => return Some(format!("clip {unique_id:?} is not on the timeline")),
    };
    let kfs = clip
        .keyframes
        .get(param)
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    let mut read_back: std::collections::HashMap<i64, &Value> = std::collections::HashMap::new();
    for kf in &kfs {
        if let Some(frame) = kf.get("frame").and_then(|v| v.as_i64()) {
            read_back.insert(frame, kf);
        }
    }
    let want_kfs = keyframes.as_array()?;
    for keyframe in want_kfs {
        let Some(frame) = keyframe.get("frame").and_then(|v| v.as_i64()) else {
            continue;
        };
        let Some(got) = read_back.get(&frame) else {
            return Some(format!(
                "no keyframe reads back on {unique_id:?} for {param:?} at frame {frame}"
            ));
        };
        let got_value = got.get("value");
        let want_value = keyframe.get("value");
        if !matches!((got_value, want_value), (Some(g), Some(w)) if same_value(g, w)) {
            return Some(format!(
                "keyframe on {unique_id:?} for {param:?} at frame {frame} reads value={}, not {}",
                got_value.map(py_repr).unwrap_or_else(|| "None".into()),
                want_value.map(py_repr).unwrap_or_else(|| "None".into())
            ));
        }
    }
    None
}

fn verify_track_name(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::TrackRename {
        track_type,
        track_index,
        name,
    } = op
    else {
        return None;
    };
    let track = match after.track(track_type, *track_index) {
        Some(track) => track,
        None => return Some(format!("{track_type}{track_index} is not on the timeline")),
    };
    if track.name != *name {
        return Some(format!(
            "{track_type}{track_index} reads {:?}, not {name:?}",
            track.name
        ));
    }
    None
}

fn verify_track_named(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::TrackAdd {
        track_type, name, ..
    } = op
    else {
        return None;
    };
    for track in &after.tracks {
        if track.track_type == *track_type && track.name == *name {
            return None;
        }
    }
    Some(format!("no {track_type} track named {name:?} reads back"))
}

fn verify_track_present(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::TrackDelete {
        track_type,
        track_index,
    } = op
    else {
        return None;
    };
    if after.track(track_type, *track_index).is_some() {
        return Some(format!("{track_type}{track_index} still reads back"));
    }
    None
}

fn verify_track_reorder(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::TrackReorder {
        track_type,
        from_index,
        to_index,
        name,
    } = op
    else {
        return None;
    };
    let landed = match after.track(track_type, *to_index) {
        Some(track) => track,
        None => return Some(format!("{track_type}{to_index} is not on the timeline")),
    };
    if landed.name != *name {
        return Some(format!(
            "{track_type}{to_index} reads {:?}, not {name:?}: the row did not land at the declared index",
            landed.name
        ));
    }
    if from_index != to_index {
        if let Some(left) = after.track(track_type, *from_index) {
            if left.name == *name {
                return Some(format!(
                    "{track_type}{from_index} still reads {name:?}: the row did not move"
                ));
            }
        }
    }
    None
}

/// The inputs a matte op declares (`reel_read.MATTE_PARAMS`).
const MATTE_PARAMS: [&str; 3] = ["Center", "Width", "Height"];

/// `_matte_read`: the op's matte as the after-read reports it.
fn matte_read<'a>(after: &'a Snapshot, unique_id: &str, matte: &str) -> Result<&'a Value, String> {
    let clip = after
        .clip(unique_id)
        .ok_or_else(|| format!("clip {unique_id:?} is not on the timeline"))?;
    if !clip.fusion.comp_names.iter().any(|n| n == matte) {
        return Err(format!(
            "clip {unique_id:?} carries no matte {matte:?}; reads {:?}",
            clip.fusion.comp_names
        ));
    }
    match clip.fusion.mattes.as_ref().and_then(|m| m.get(matte)) {
        Some(state) if !state.is_null() => Ok(state),
        _ => Err(format!("matte {matte:?} carries no readable RectangleMask")),
    }
}

/// `_matte_value_failure`: a declared value against what reads back;
/// a point recurses per axis.
fn matte_value_failure(label: &str, got: Option<&Value>, want: &Value) -> Option<String> {
    if let Value::Object(axes) = want {
        for (axis, axis_want) in axes {
            let axis_got = got.and_then(|g| g.as_object()).and_then(|g| g.get(axis));
            if let Some(failure) = matte_value_failure(&format!("{label}.{axis}"), axis_got, axis_want) {
                return Some(failure);
            }
        }
        return None;
    }
    match got {
        Some(got) if same_value(got, want) => None,
        got => Some(format!(
            "{label} reads {}, not {}",
            got.map(py_repr).unwrap_or_else(|| "None".to_string()),
            py_repr(want)
        )),
    }
}

fn geometry_failure(matte: &str, state: &Value, geometry: &Value, skip: &[String]) -> Option<String> {
    let declared = geometry.as_object()?;
    for (key, want) in declared {
        if skip.iter().any(|s| s == key) {
            continue;
        }
        let got = state.get("geometry").and_then(|g| g.get(key));
        if let Some(failure) = matte_value_failure(&format!("matte {matte:?} {key}"), got, want) {
            return Some(failure);
        }
    }
    None
}

fn frame_of(value: &Value) -> Option<f64> {
    value.get("frame").and_then(Value::as_f64)
}

fn verify_mask(op: &EditOp, after: &Snapshot) -> Option<String> {
    match op {
        EditOp::MaskCreate { unique_id, matte } => matte_read(after, &unique_id.0, matte).err(),
        EditOp::MaskDelete { unique_id, matte } => {
            let Some(clip) = after.clip(&unique_id.0) else {
                return Some(format!("clip {:?} is not on the timeline", unique_id.0));
            };
            if clip.fusion.comp_names.iter().any(|n| n == matte) {
                return Some(format!("clip {:?} still carries matte {matte:?}", unique_id.0));
            }
            None
        }
        EditOp::MaskEdit { unique_id, matte, geometry } => match matte_read(after, &unique_id.0, matte) {
            Err(failure) => Some(failure),
            Ok(state) => geometry_failure(matte, state, geometry, &[]),
        },
        EditOp::MaskTrack { unique_id, matte, track, geometry, .. } => {
            let state = match matte_read(after, &unique_id.0, matte) {
                Err(failure) => return Some(failure),
                Ok(state) => state,
            };
            let got: Vec<&Value> = state
                .get("keyframes")
                .and_then(Value::as_array)
                .map(|keys| keys.iter().collect())
                .unwrap_or_default();
            let declared: Vec<&Value> = track
                .get("keyframes")
                .and_then(Value::as_array)
                .map(|keys| keys.iter().collect())
                .unwrap_or_default();
            let declared_frames: Vec<f64> = declared.iter().filter_map(|k| frame_of(k)).collect();
            if got.iter().filter_map(|k| frame_of(k)).any(|f| !declared_frames.contains(&f)) {
                return Some(format!("matte {matte:?} is keyed at undeclared frames"));
            }
            for keyframe in &declared {
                let frame = frame_of(keyframe)?;
                let Some(entry) = got.iter().find(|k| frame_of(k) == Some(frame)) else {
                    return Some(format!("matte {matte:?} has no keyframe at frame {frame}"));
                };
                for param in MATTE_PARAMS {
                    if let Some(want) = keyframe.get(param) {
                        let label = format!("matte {matte:?} frame {frame} {param}");
                        if let Some(failure) = matte_value_failure(&label, entry.get(param), want) {
                            return Some(failure);
                        }
                    }
                }
            }
            let keyed: Vec<String> = declared
                .iter()
                .filter_map(|k| k.as_object())
                .flat_map(|k| k.keys().filter(|key| *key != "frame").cloned())
                .collect();
            geometry.as_ref().and_then(|g| geometry_failure(matte, state, g, &keyed))
        }
        _ => None,
    }
}

fn verify_grade_node_set(op: &EditOp, after: &Snapshot) -> Option<String> {
    let EditOp::GradeNodeSet {
        unique_id,
        node_index,
        params,
    } = op
    else {
        return None;
    };
    let clip = match after.clip(&unique_id.0) {
        Some(clip) => clip,
        None => return Some(format!("clip {unique_id:?} is not on the timeline")),
    };
    let problems = compare_cdl(&Value::Object(clip.color.cdl.clone()), params);
    if problems.is_empty() {
        return None;
    }
    Some(format!(
        "clip {unique_id:?} node {node_index} reads back: {}",
        problems.join("; ")
    ))
}

fn verify_presence(op: &EditOp, after: &Snapshot, present: bool, what: &str) -> Option<String> {
    let unique_id = match op {
        EditOp::ClipInsert { unique_id, .. } => unique_id,
        EditOp::DropFragment { unique_id } => unique_id,
        EditOp::OverlayRemove { unique_id } => unique_id,
        EditOp::CompoundCreate { unique_id, .. } => unique_id,
        EditOp::CompoundFlatten { unique_id } => unique_id,
        _ => return None,
    };
    let is_present = after.clip(&unique_id.0).is_some();
    if present && !is_present {
        return Some(format!("{what} {unique_id:?} is not on the timeline"));
    }
    if !present && is_present {
        return Some(format!("{what} {unique_id:?} still reads back"));
    }
    None
}

/// The op as a JSON value, for the marker matchers (`_matches` reads
/// the op's own fields).
fn op_to_value(op: &EditOp) -> Value {
    serde_json::to_value(op).unwrap_or(Value::Null)
}

/// The read-back judgement of one op against an after-snapshot: None
/// when the effect landed, the failure text when it did not. EXHAUSTIVE
/// over `EditOp`.
pub fn verify(op: &EditOp, after: &Snapshot) -> Option<String> {
    match op {
        EditOp::MarkerAdd { .. } => verify_marker_add(op, after),
        EditOp::MarkerDelete { .. } => verify_marker_delete(op, after),
        EditOp::ClipMarkerAdd { .. } => verify_clip_marker_add(op, after),
        EditOp::ClipMarkerDelete { .. } => verify_clip_marker_delete(op, after),
        EditOp::ClipDelete { .. } => verify_delete(op, after),
        EditOp::ClipInsert { .. } => verify_presence(op, after, true, "clip"),
        EditOp::ClipSplit { .. } => verify_split(op, after),
        EditOp::ClipJoin { .. } => verify_join(op, after),
        EditOp::ClipTrim { .. } => verify_trim(op, after),
        EditOp::ClipRipple { .. } => verify_ripple(op, after),
        EditOp::ClipRoll { .. } => verify_source_frames(op, after),
        EditOp::ClipSlip { .. } => verify_source_frames(op, after),
        EditOp::ClipSlide { .. } => verify_slide(op, after),
        EditOp::ClipMove { .. } => verify_slide(op, after),
        EditOp::ClipReplace { .. } => verify_replace(op, after),
        EditOp::ClipSetEnabled { .. } => verify_enabled(op, after),
        EditOp::ClipLink { .. } => None,
        EditOp::ClipUnlink { .. } => None,
        EditOp::ClipSmartReframe { .. } => verify_smart_reframe(op, after),
        EditOp::ClipSetProperty { .. } => verify_property(op, after),
        EditOp::Retime { .. } => verify_retime(op, after),
        EditOp::RetimeSegments { .. } => verify_retime_segments(op, after),
        EditOp::PictureKeyframe { .. } => verify_picture_keyframe(op, after),
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
        EditOp::GradeNodeSet { .. } => verify_grade_node_set(op, after),
        EditOp::WindowCreate { .. } => None,
        EditOp::WindowDelete { .. } => None,
        EditOp::QualifierSample { .. } => None,
        EditOp::QualifierAdjust { .. } => None,
        EditOp::MaskCreate { .. }
        | EditOp::MaskDelete { .. }
        | EditOp::MaskEdit { .. }
        | EditOp::MaskTrack { .. } => verify_mask(op, after),
        EditOp::MulticamAngle { .. } => None,
        EditOp::CompoundCreate { .. } => verify_presence(op, after, true, "compound"),
        EditOp::CompoundFlatten { .. } => verify_presence(op, after, false, "compound"),
        EditOp::TrackAdd { .. } => verify_track_named(op, after),
        EditOp::TrackDelete { .. } => verify_track_present(op, after),
        EditOp::TrackRename { .. } => verify_track_name(op, after),
        EditOp::TrackReorder { .. } => verify_track_reorder(op, after),
        EditOp::OverlayAdd { .. } => None,
        EditOp::OverlayRemove { .. } => verify_presence(op, after, false, "overlay"),
        EditOp::OverlaySwapPixels { .. } => None,
        EditOp::OverlayResize { .. } => None,
        EditOp::OverlayEntryMotion { .. } => None,
        EditOp::TransformOverride { .. } => verify_property(op, after),
        EditOp::SpanRetime { .. } => verify_retime(op, after),
        EditOp::DropFragment { .. } => verify_presence(op, after, false, "clip"),
        EditOp::CaptionFix { .. } => None,
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

/// The key parts, re-exported for the compose module.
pub fn key_parts(op: &EditOp) -> Vec<KeyPart> {
    crate::algebra::key(op)
}
