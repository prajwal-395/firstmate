//! The PyO3 boundary: the `ren_native` extension module.
//!
//! Every function takes plain Python objects and converts them to the
//! typed Rust forms, so the editorial correctness logic runs over
//! ren-protocol's `EditOp`/`EditPatch` types with the compiler checking
//! every match. The FilmCraft planning functions run the pinned
//! headless adapter (`native/ren-filmcraft`) over ren-protocol's
//! project-model types the same way. The Python callers are
//! `library/tools/edit_backend.py` (the algebra) and
//! `library/tools/project_model_cli.py` (the `ren project-model`
//! verb); nothing here reaches Resolve.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyModule, PyString, PyTuple};
use serde_json::Value;
use std::collections::HashSet;
use std::sync::{Arc, RwLock};

use ren_protocol::edit::{Condition, EditOp, EditPatch};
use ren_protocol::{CapabilityId, ClipId, Generation};

use crate::algebra::{
    declaration as algebra_declaration, key_strings, op_ids as algebra_op_ids,
    patch_op_ids as algebra_patch_op_ids, span as op_span, MergeSemantics,
};
use crate::compose::{
    carry as compose_carry, compose as compose_patches, problems as composition_problems,
    respan as compose_respan, CarryRow, Semantics,
};
use crate::conditions::judge_all;
use crate::patch::{covered as span_covered, validate_patch as validate_patch_dict};
use crate::snapshot::{Clip, ColorInfo, FusionInfo, Markers, Snapshot, Track};
use crate::verify::verify as op_verify;

// ── Python <-> JSON conversion ───────────────────────────────────────

fn py_to_json(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if let Ok(dict) = obj.cast::<PyDict>() {
        let mut map = serde_json::Map::new();
        for (key, value) in dict.iter() {
            let key: String = key.extract()?;
            map.insert(key, py_to_json(&value)?);
        }
        return Ok(Value::Object(map));
    }
    if let Ok(list) = obj.cast::<PyList>() {
        let mut arr = Vec::new();
        for item in list.iter() {
            arr.push(py_to_json(&item)?);
        }
        return Ok(Value::Array(arr));
    }
    if let Ok(tuple) = obj.cast::<pyo3::types::PyTuple>() {
        let mut arr = Vec::new();
        for item in tuple.iter() {
            arr.push(py_to_json(&item)?);
        }
        return Ok(Value::Array(arr));
    }
    if let Ok(text) = obj.extract::<String>() {
        return Ok(Value::String(text));
    }
    // A Python bool is an int, so `True.extract::<i64>()` answers 1: the
    // boolean must be read FIRST or every flag reaches the snapshot as
    // 1/0 and the typed reading refuses it.
    if let Ok(flag) = obj.extract::<bool>() {
        return Ok(Value::from(flag));
    }
    if let Ok(int) = obj.extract::<i64>() {
        return Ok(Value::from(int));
    }
    if let Ok(float) = obj.extract::<f64>() {
        return Ok(serde_json::Number::from_f64(float)
            .map(Value::Number)
            .unwrap_or(Value::Null));
    }
    Err(PyValueError::new_err(format!(
        "cannot read {} as JSON",
        obj.get_type().name()?
    )))
}

/// Property values are usually scalar Resolve values. Check their exact
/// Python type first so the hot property-edit path does not probe every
/// container type before extracting a float or integer.
fn property_value_from_py(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if obj.is_instance_of::<PyBool>() {
        return Ok(Value::Bool(obj.extract()?));
    }
    if obj.is_instance_of::<PyInt>() {
        if let Ok(value) = obj.extract::<i64>() {
            return Ok(Value::from(value));
        }
    }
    if obj.is_instance_of::<PyFloat>() {
        return Ok(serde_json::Number::from_f64(obj.extract::<f64>()?)
            .map(Value::Number)
            .unwrap_or(Value::Null));
    }
    if let Ok(text) = obj.extract::<String>() {
        return Ok(Value::String(text));
    }
    py_to_json(obj)
}

fn json_to_py<'py>(py: Python<'py>, value: &Value) -> PyResult<Bound<'py, PyAny>> {
    match value {
        Value::Null => Ok(py.None().into_bound(py)),
        Value::Bool(flag) => Ok(pyo3::types::PyBool::new(py, *flag).to_owned().into_any()),
        Value::Number(num) => {
            if let Some(int) = num.as_i64() {
                Ok(int.into_pyobject(py)?.into_any())
            } else {
                Ok(num.as_f64().unwrap_or(0.0).into_pyobject(py)?.into_any())
            }
        }
        Value::String(text) => Ok(text.clone().into_pyobject(py)?.into_any()),
        Value::Array(arr) => {
            let list = PyList::empty(py);
            for item in arr {
                list.append(json_to_py(py, item)?)?;
            }
            Ok(list.into_any())
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (key, item) in map {
                dict.set_item(key, json_to_py(py, item)?)?;
            }
            Ok(dict.into_any())
        }
    }
}

// ── Boundary helpers ────────────────────────────────────────────────

fn snapshot_from_value(value: &Value) -> PyResult<Snapshot> {
    serde_json::from_value(value.clone())
        .map_err(|err| PyValueError::new_err(format!("not a timeline snapshot: {err}")))
}

/// The measured property-write paths read only clip identity and record
/// bounds, plus transforms for read-back. Extract that projection directly
/// instead of JSON-encoding unrelated Fusion, color and marker data.
fn edit_core_snapshot_from_py(
    value: &Bound<'_, PyAny>,
    include_transform: bool,
    target_ids: Option<&HashSet<String>>,
) -> PyResult<Snapshot> {
    let dict = value.cast::<PyDict>()?;
    let start_frame: i64 = dict
        .get_item("start_frame")?
        .ok_or_else(|| PyValueError::new_err("snapshot missing start_frame"))?
        .extract()?;
    let end_frame: i64 = dict
        .get_item("end_frame")?
        .ok_or_else(|| PyValueError::new_err("snapshot missing end_frame"))?
        .extract()?;
    let tracks_obj = dict
        .get_item("tracks")?
        .ok_or_else(|| PyValueError::new_err("snapshot missing tracks"))?;
    let tracks = tracks_obj.cast::<PyList>()?;
    let mut projected = Vec::with_capacity(tracks.len());
    for track in tracks.iter() {
        let track = track.cast::<PyDict>()?;
        let mut track_info: Option<(String, i64)> = None;
        let clips_obj = track
            .get_item("clips")?
            .ok_or_else(|| PyValueError::new_err("track missing clips"))?;
        let clips = clips_obj.cast::<PyList>()?;
        let mut projected_clips = Vec::with_capacity(clips.len());
        for clip in clips.iter() {
            let clip = clip.cast::<PyDict>()?;
            let unique_id: String = clip
                .get_item("unique_id")?
                .ok_or_else(|| PyValueError::new_err("clip missing unique_id"))?
                .extract()?;
            if target_ids.is_some_and(|ids| !ids.contains(&unique_id)) {
                continue;
            }
            let (track_type, index) = match &track_info {
                Some((track_type, index)) => (track_type.clone(), *index),
                None => {
                    let track_type: String = track
                        .get_item("type")?
                        .ok_or_else(|| PyValueError::new_err("track missing type"))?
                        .extract()?;
                    let index: i64 = track
                        .get_item("index")?
                        .ok_or_else(|| PyValueError::new_err("track missing index"))?
                        .extract()?;
                    track_info = Some((track_type.clone(), index));
                    (track_type, index)
                }
            };
            let record_in: i64 = clip
                .get_item("record_in")?
                .ok_or_else(|| PyValueError::new_err("clip missing record_in"))?
                .extract()?;
            let record_out: i64 = clip
                .get_item("record_out")?
                .ok_or_else(|| PyValueError::new_err("clip missing record_out"))?
                .extract()?;
            let mut transform_map = serde_json::Map::new();
            if include_transform {
                if let Some(transform) = clip.get_item("transform")? {
                    let transform = transform.cast::<PyDict>()?;
                    for (key, value) in transform.iter() {
                        transform_map.insert(key.extract()?, py_to_json(&value)?);
                    }
                }
            }
            projected_clips.push(Clip {
                unique_id,
                name: String::new(),
                track_type: track_type.clone(),
                track_index: index,
                track_name: String::new(),
                record_in,
                record_out,
                duration: 0,
                source_in_frame: None,
                source_out_frame: None,
                source_file: String::new(),
                enabled: None,
                speed: None,
                transform: transform_map,
                keyframes: serde_json::Map::new(),
                fusion: FusionInfo::default(),
                color: ColorInfo::default(),
                markers: Vec::new(),
            });
        }
        if let (Some((track_type, index)), false) = (track_info, projected_clips.is_empty()) {
            projected.push(Track {
                track_type,
                index,
                name: String::new(),
                clips: projected_clips,
            });
        }
    }
    Ok(Snapshot {
        start_frame,
        end_frame,
        tracks: projected,
        markers: Markers::default(),
    })
}

/// A parsed timeline snapshot that survives across native edit-core calls.
/// The lock also lets callers incrementally apply measured property writes.
#[pyclass]
struct SnapshotHandle {
    snapshot: Arc<RwLock<Snapshot>>,
}

#[pymethods]
impl SnapshotHandle {
    /// Apply a property write confirmed by a read-back measurement.
    /// Unsupported operations return false so the caller can refresh from
    /// a complete post-edit snapshot.
    fn apply_confirmed(&self, operation: &Bound<'_, PyAny>) -> PyResult<bool> {
        let value = py_to_json(operation)?;
        let Some(EditOp::ClipSetProperty {
            unique_id,
            key,
            value,
        }) = op_from_value(&value)
        else {
            return Ok(false);
        };
        let mut snapshot = self
            .snapshot
            .write()
            .map_err(|_| PyValueError::new_err("snapshot handle lock poisoned"))?;
        let mut found = None;
        for (track_index, track) in snapshot.tracks.iter().enumerate() {
            for (clip_index, clip) in track.clips.iter().enumerate() {
                if clip.unique_id == unique_id.0 {
                    if found.is_some() {
                        return Ok(false);
                    }
                    found = Some((track_index, clip_index));
                }
            }
        }
        let Some((track_index, clip_index)) = found else {
            return Ok(false);
        };
        snapshot.tracks[track_index].clips[clip_index]
            .transform
            .insert(key, value);
        Ok(true)
    }

    /// Replace the resident value after a non-incremental edit.
    fn replace(&self, value: &Bound<'_, PyAny>) -> PyResult<()> {
        let snapshot = snapshot_from_value(&py_to_json(value)?)?;
        *self
            .snapshot
            .write()
            .map_err(|_| PyValueError::new_err("snapshot handle lock poisoned"))? = snapshot;
        Ok(())
    }
}

#[pyfunction]
#[pyo3(signature = (value, operations=None, *, property_projection=false, include_transform=false))]
fn snapshot_handle(
    value: &Bound<'_, PyAny>,
    operations: Option<&Bound<'_, PyAny>>,
    property_projection: bool,
    include_transform: bool,
) -> PyResult<SnapshotHandle> {
    if let Ok(handle) = value.extract::<PyRef<'_, SnapshotHandle>>() {
        return Ok(SnapshotHandle {
            snapshot: Arc::clone(&handle.snapshot),
        });
    }
    let targets = operations
        .map(property_operations_from_py)
        .transpose()?
        .flatten()
        .map(|ops| {
            ops.iter()
                .filter_map(crate::algebra::op_unique_id)
                .collect::<HashSet<_>>()
        });
    let snapshot = if property_projection {
        edit_core_snapshot_from_py(value, include_transform, targets.as_ref())?
    } else {
        snapshot_from_value(&py_to_json(value)?)?
    };
    Ok(SnapshotHandle {
        snapshot: Arc::new(RwLock::new(snapshot)),
    })
}

fn with_snapshot<T>(
    value: &Bound<'_, PyAny>,
    property_projection: Option<bool>,
    target_ids: Option<&HashSet<String>>,
    compute: impl FnOnce(&Snapshot) -> PyResult<T>,
) -> PyResult<T> {
    if let Ok(handle) = value.extract::<PyRef<'_, SnapshotHandle>>() {
        let snapshot = handle
            .snapshot
            .read()
            .map_err(|_| PyValueError::new_err("snapshot handle lock poisoned"))?;
        return compute(&snapshot);
    }
    let snapshot = if let Some(include_transform) = property_projection {
        edit_core_snapshot_from_py(value, include_transform, target_ids)?
    } else {
        snapshot_from_value(&py_to_json(value)?)?
    };
    compute(&snapshot)
}

/// The typed op for a JSON op, or None when the op id is not in the
/// algebra (the Python side's `ALGEBRA.get` returns None there too).
fn op_from_value(value: &Value) -> Option<EditOp> {
    let op_id = value.get("op").and_then(|v| v.as_str())?;
    if crate::algebra::declaration(op_id).is_none() {
        return None;
    }
    serde_json::from_value(value.clone()).ok()
}

fn property_operations_from_py(operations: &Bound<'_, PyAny>) -> PyResult<Option<Vec<EditOp>>> {
    let Ok(operations) = operations.cast::<PyList>() else {
        return Ok(None);
    };
    let mut typed = Vec::with_capacity(operations.len());
    for operation in operations.iter() {
        let Ok(operation) = operation.cast::<PyDict>() else {
            return Ok(None);
        };
        let Some(op_id) = operation.get_item("op")? else {
            return Ok(None);
        };
        if op_id.extract::<&str>().ok() != Some("clip.set_property") {
            return Ok(None);
        }
        let (Some(unique_id), Some(key), Some(value)) = (
            operation
                .get_item("unique_id")?
                .and_then(|value| value.extract::<String>().ok()),
            operation
                .get_item("key")?
                .and_then(|value| value.extract::<String>().ok()),
            operation.get_item("value")?,
        ) else {
            return Ok(None);
        };
        typed.push(EditOp::ClipSetProperty {
            unique_id: ClipId(unique_id),
            key,
            value: property_value_from_py(&value)?,
        });
    }
    Ok(Some(typed))
}

fn target_ids(operations: &[EditOp]) -> HashSet<String> {
    operations
        .iter()
        .filter_map(crate::algebra::op_unique_id)
        .collect()
}

fn patch_from_value(value: &Value) -> Result<EditPatch, String> {
    serde_json::from_value(value.clone()).map_err(|err| format!("not an EditPatch: {err}"))
}

/// Decode the measured property-write patch shape directly from Python.
/// It avoids recursively materializing a JSON tree and then deserializing
/// it back into the typed patch for every compose call. Other edit ops use
/// the exhaustive serde path.
fn property_patch_from_py(value: &Bound<'_, PyAny>) -> PyResult<Option<EditPatch>> {
    let Ok(dict) = value.cast::<PyDict>() else {
        return Ok(None);
    };
    let read_text = |key: &str| -> PyResult<Option<String>> {
        Ok(dict
            .get_item(key)?
            .and_then(|value| value.extract::<String>().ok()))
    };
    let (Some(id), Some(project), Some(timeline)) = (
        read_text("id")?,
        read_text("project")?,
        read_text("timeline")?,
    ) else {
        return Ok(None);
    };
    let Some(capability) = dict.get_item("capability")? else {
        return Ok(None);
    };
    if !capability.is_instance_of::<PyString>() {
        return Ok(None);
    }
    let Some(base_generation) = dict
        .get_item("base_generation")?
        .and_then(|value| value.extract::<u64>().ok())
    else {
        return Ok(None);
    };
    let Some(operations_value) = dict.get_item("operations")? else {
        return Ok(None);
    };
    let Ok(operations) = operations_value.cast::<PyList>() else {
        return Ok(None);
    };
    if operations.is_empty() {
        return Ok(None);
    }
    let mut typed_ops = Vec::with_capacity(operations.len());
    for operation in operations.iter() {
        let Ok(operation) = operation.cast::<PyDict>() else {
            return Ok(None);
        };
        let Some(op_id) = operation.get_item("op")? else {
            return Ok(None);
        };
        if op_id.extract::<&str>().ok() != Some("clip.set_property") {
            return Ok(None);
        }
        let (Some(unique_id), Some(key), Some(value)) = (
            operation
                .get_item("unique_id")?
                .and_then(|value| value.extract::<String>().ok()),
            operation
                .get_item("key")?
                .and_then(|value| value.extract::<String>().ok()),
            operation.get_item("value")?,
        ) else {
            return Ok(None);
        };
        typed_ops.push(EditOp::ClipSetProperty {
            unique_id: ClipId(unique_id),
            key,
            value: property_value_from_py(&value)?,
        });
    }
    let Some(spans) = dict.get_item("affected_spans")? else {
        return Ok(None);
    };
    let Ok(spans) = spans.cast::<PyList>() else {
        return Ok(None);
    };
    if spans.is_empty() {
        return Ok(None);
    }
    let mut typed_spans = Vec::with_capacity(spans.len());
    for span in spans.iter() {
        let pair = if let Ok(pair) = span.cast::<PyTuple>() {
            pair
        } else if let Ok(pair) = span.cast::<PyList>() {
            if pair.len() != 2 {
                return Ok(None);
            }
            let (Ok(start), Ok(end)) = (pair.get_item(0)?.extract(), pair.get_item(1)?.extract())
            else {
                return Ok(None);
            };
            if pair.get_item(0)?.is_instance_of::<PyBool>()
                || pair.get_item(1)?.is_instance_of::<PyBool>()
            {
                return Ok(None);
            }
            typed_spans.push((start, end));
            continue;
        } else {
            return Ok(None);
        };
        if pair.len() != 2 {
            return Ok(None);
        }
        let (Ok(start), Ok(end)) = (pair.get_item(0)?.extract(), pair.get_item(1)?.extract())
        else {
            return Ok(None);
        };
        if pair.get_item(0)?.is_instance_of::<PyBool>()
            || pair.get_item(1)?.is_instance_of::<PyBool>()
        {
            return Ok(None);
        }
        typed_spans.push((start, end));
    }
    let Some(domains) = dict.get_item("conflict_domains")? else {
        return Ok(None);
    };
    let Ok(domains) = domains.cast::<PyList>() else {
        return Ok(None);
    };
    let mut typed_domains = Vec::with_capacity(domains.len());
    for domain in domains.iter() {
        let Ok(domain) = domain.extract::<String>() else {
            return Ok(None);
        };
        typed_domains.push(domain);
    }
    Ok(Some(EditPatch {
        id,
        project,
        timeline,
        base_generation: Generation(base_generation),
        capability: CapabilityId(String::new()),
        affected_spans: typed_spans,
        conflict_domains: typed_domains,
        operations: typed_ops,
        preconditions: Vec::new(),
        postconditions: Vec::new(),
        base_git_sha: String::new(),
    }))
}

fn semantics_from_py(value: &Bound<'_, PyAny>) -> PyResult<Option<Semantics>> {
    let Ok(dict) = value.cast::<PyDict>() else {
        return Ok(None);
    };
    let Some(temporal_value) = dict.get_item("temporal_effect")? else {
        return Ok(None);
    };
    let Some(merge_value) = dict.get_item("merge_semantics")? else {
        return Ok(None);
    };
    let (Ok(temporal_text), Ok(merge_text)) = (
        temporal_value.extract::<&str>(),
        merge_value.extract::<&str>(),
    ) else {
        return Ok(None);
    };
    Ok(Some(Semantics {
        temporal_effect: crate::algebra::TemporalEffect::parse(temporal_text),
        merge_semantics: MergeSemantics::parse(merge_text),
    }))
}

fn semantics_from_value(value: &Value) -> PyResult<Value> {
    if value.is_object() {
        Ok(value.clone())
    } else {
        Err(PyValueError::new_err("not patch semantics"))
    }
}

// ── The module ──────────────────────────────────────────────────────

/// Every algebra id, in declaration order.
#[pyfunction]
fn op_ids() -> Vec<String> {
    algebra_op_ids().into_iter().map(String::from).collect()
}

/// The ids EditPatch may schedule: every op with a native apply.
#[pyfunction]
fn patch_op_ids() -> Vec<String> {
    algebra_patch_op_ids()
        .into_iter()
        .map(String::from)
        .collect()
}

/// The `native/` source fingerprint this extension was baked with, or
/// `"unknown"` when it was built without one. `edit_backend.rust()`
/// compares it against the running tree and refuses a build that does
/// not match.
#[pyfunction]
fn source_fingerprint() -> &'static str {
    option_env!("REN_NATIVE_SOURCE_FINGERPRINT").unwrap_or("unknown")
}

/// The declaration for one algebra id: domain, required, merge,
/// temporal, item, has_apply and the inverse.
#[pyfunction]
fn declaration<'py>(op_id: &str, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
    match algebra_declaration(op_id) {
        Some(decl) => {
            let value = serde_json::json!({
                "id": decl.id,
                "domain": decl.domain,
                "required": decl.required,
                "merge": decl.merge.as_str(),
                "temporal": decl.temporal.as_str(),
                "item": decl.item,
                "has_apply": decl.has_apply,
                "inverse": {
                    "op": decl.inverse.op,
                    "self_inverse": decl.inverse.self_inverse,
                    "reason": decl.inverse.reason,
                },
            });
            json_to_py(py, &value)
        }
        None => Ok(py.None().into_bound(py)),
    }
}

/// The frames an op touches over a base snapshot, or None.
#[pyfunction]
fn span(op: &Bound<'_, PyAny>, base: &Bound<'_, PyAny>) -> PyResult<Option<(i64, i64)>> {
    let op_value = py_to_json(op)?;
    let projection =
        (op_value.get("op").and_then(Value::as_str) == Some("clip.set_property")).then_some(false);
    let targets = op_from_value(&op_value).and_then(|op| crate::algebra::op_unique_id(&op));
    let targets = targets.map(|id| HashSet::from([id]));
    with_snapshot(base, projection, targets.as_ref(), |base| {
        Ok(op_from_value(&op_value).and_then(|op| op_span(&op, base)))
    })
}

/// The spans for a batch of ops over one snapshot. Conversion is paid
/// once for the shared snapshot, and the Rust loop runs without Python
/// object conversion inside the operation loop.
#[pyfunction]
fn span_many(
    ops: &Bound<'_, PyAny>,
    base: &Bound<'_, PyAny>,
    _py: Python<'_>,
) -> PyResult<Vec<Option<(i64, i64)>>> {
    if let Some(ops) = property_operations_from_py(ops)? {
        let targets = target_ids(&ops);
        return with_snapshot(base, Some(false), Some(&targets), |base| {
            Ok(ops.iter().map(|op| op_span(op, base)).collect())
        });
    }
    let ops_value = py_to_json(ops)?;
    let ops = ops_value
        .as_array()
        .ok_or_else(|| PyValueError::new_err("ops must be a list"))?
        .clone();
    let property_only = ops
        .iter()
        .all(|op| op.get("op").and_then(Value::as_str) == Some("clip.set_property"));
    let typed: Vec<EditOp> = ops.iter().filter_map(op_from_value).collect();
    let targets = target_ids(&typed);
    with_snapshot(
        base,
        property_only.then_some(false),
        Some(&targets),
        |base| {
            Ok(ops
                .iter()
                .map(|value| op_from_value(value).and_then(|op| op_span(&op, base)))
                .collect())
        },
    )
}

/// The write key of one op, as string parts (numbers rendered as
/// decimal strings).
#[pyfunction]
fn key(op: &Bound<'_, PyAny>) -> Vec<String> {
    let Ok(op_value) = py_to_json(op) else {
        return vec![];
    };
    op_from_value(&op_value)
        .map(|op| key_strings(&op))
        .unwrap_or_default()
}

/// The read-back verify judgement: None when the effect landed, the
/// failure text when it did not.
#[pyfunction]
fn verify(op: &Bound<'_, PyAny>, after: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    let op_value = py_to_json(op)?;
    let projection =
        (op_value.get("op").and_then(Value::as_str) == Some("clip.set_property")).then_some(true);
    let targets = op_from_value(&op_value).and_then(|op| crate::algebra::op_unique_id(&op));
    let targets = targets.map(|id| HashSet::from([id]));
    with_snapshot(after, projection, targets.as_ref(), |after| {
        Ok(op_from_value(&op_value).and_then(|op| op_verify(&op, after)))
    })
}

/// Read-back judgements for a batch of ops against one snapshot.
#[pyfunction]
fn verify_many(
    ops: &Bound<'_, PyAny>,
    after: &Bound<'_, PyAny>,
    _py: Python<'_>,
) -> PyResult<Vec<Option<String>>> {
    if let Some(ops) = property_operations_from_py(ops)? {
        let targets = target_ids(&ops);
        return with_snapshot(after, Some(true), Some(&targets), |after| {
            Ok(ops.iter().map(|op| op_verify(op, after)).collect())
        });
    }
    let ops_value = py_to_json(ops)?;
    let ops = ops_value
        .as_array()
        .ok_or_else(|| PyValueError::new_err("ops must be a list"))?
        .clone();
    let property_only = ops
        .iter()
        .all(|op| op.get("op").and_then(Value::as_str) == Some("clip.set_property"));
    let typed: Vec<EditOp> = ops.iter().filter_map(op_from_value).collect();
    let targets = target_ids(&typed);
    with_snapshot(
        after,
        property_only.then_some(true),
        Some(&targets),
        |after| {
            Ok(ops
                .iter()
                .map(|value| op_from_value(value).and_then(|op| op_verify(&op, after)))
                .collect())
        },
    )
}

/// Judge conditions on a snapshot: each failure as `"{kind}: {failure}"`.
#[pyfunction]
fn judge_conditions(
    conditions: &Bound<'_, PyAny>,
    snap: &Bound<'_, PyAny>,
    base: &Bound<'_, PyAny>,
) -> PyResult<Vec<String>> {
    let conditions_value = py_to_json(conditions)?;
    let snap = snapshot_from_value(&py_to_json(snap)?)?;
    let base = snapshot_from_value(&py_to_json(base)?)?;
    let conditions: Vec<Condition> = serde_json::from_value(conditions_value)
        .map_err(|err| PyValueError::new_err(format!("not conditions: {err}")))?;
    Ok(judge_all(&conditions, &snap, &base))
}

/// Validate a patch against the base snapshot and the capability's
/// declared patch semantics. None when the declarations are true, the
/// refusal reason when they are not.
#[pyfunction]
fn validate_patch(
    patch: &Bound<'_, PyAny>,
    base: &Bound<'_, PyAny>,
    semantics: &Bound<'_, PyAny>,
) -> PyResult<Option<String>> {
    let patch = match patch_from_value(&py_to_json(patch)?) {
        Ok(patch) => patch,
        Err(reason) => return Ok(Some(reason)),
    };
    let base = snapshot_from_value(&py_to_json(base)?)?;
    let semantics = semantics_from_value(&py_to_json(semantics)?)?;
    match validate_patch_dict(&patch, &base, &semantics) {
        Ok(()) => Ok(None),
        Err(reason) => Ok(Some(reason)),
    }
}

/// How `later` lands once `earlier` has: `{verdict, reason}`.
#[pyfunction]
fn compose<'py>(
    earlier: &Bound<'_, PyAny>,
    later: &Bound<'_, PyAny>,
    base: &Bound<'_, PyAny>,
    a_sem: &Bound<'_, PyAny>,
    b_sem: &Bound<'_, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let earlier = match property_patch_from_py(earlier)? {
        Some(patch) => patch,
        None => serde_json::from_value(py_to_json(earlier)?)
            .map_err(|err| PyValueError::new_err(format!("not an EditPatch: {err}")))?,
    };
    let later = match property_patch_from_py(later)? {
        Some(patch) => patch,
        None => serde_json::from_value(py_to_json(later)?)
            .map_err(|err| PyValueError::new_err(format!("not an EditPatch: {err}")))?,
    };
    let property_only = later
        .operations
        .iter()
        .all(|op| op.op_id() == "clip.set_property");
    let a_sem = semantics_from_py(a_sem)?;
    let b_sem = semantics_from_py(b_sem)?;
    let base_is_handle = base.extract::<PyRef<'_, SnapshotHandle>>().is_ok();
    let targets = if property_only && !base_is_handle {
        Some(target_ids(&later.operations))
    } else {
        None
    };
    with_snapshot(
        base,
        property_only.then_some(false),
        targets.as_ref(),
        |base| {
            let found = compose_patches(&earlier, &later, base, a_sem.as_ref(), b_sem.as_ref());
            let result = PyDict::new(py);
            result.set_item("verdict", found.verdict.as_str())?;
            result.set_item("reason", found.reason)?;
            Ok(result.into_any())
        },
    )
}

/// A stale patch moved onto the head, or the reason it cannot.
/// `rows` are the generations since the base:
/// `[{generation, source, patch, snapshot}, ...]`.
#[pyfunction]
fn carry<'py>(
    patch: &Bound<'_, PyAny>,
    rows: &Bound<'_, PyAny>,
    semantics: &Bound<'_, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let patch = match patch_from_value(&py_to_json(patch)?) {
        Ok(patch) => patch,
        Err(reason) => return Err(PyValueError::new_err(reason)),
    };
    let rows_value = py_to_json(rows)?;
    let rows: Vec<CarryRow> = serde_json::from_value(rows_value)
        .map_err(|err| PyValueError::new_err(format!("not carry rows: {err}")))?;
    let semantics = semantics_from_value(&py_to_json(semantics)?)?;
    match compose_carry(&patch, &rows, &semantics) {
        Ok(moved) => {
            let value = serde_json::to_value(&moved)
                .map_err(|err| PyValueError::new_err(err.to_string()))?;
            json_to_py(py, &value)
        }
        Err(reason) => Err(PyValueError::new_err(reason)),
    }
}

/// Whether two write keys MEET: one is a prefix of the other.
#[pyfunction]
fn meet(a: &Bound<'_, PyAny>, b: &Bound<'_, PyAny>) -> bool {
    let (Ok(a), Ok(b)) = (py_to_json(a), py_to_json(b)) else {
        return false;
    };
    let parse = |value: &Value| -> Vec<crate::algebra::KeyPart> {
        value
            .as_array()
            .map(|arr| {
                arr.iter()
                    .map(|part| match part {
                        Value::Number(num) => {
                            crate::algebra::KeyPart::Num(num.as_i64().unwrap_or(0))
                        }
                        Value::String(text) => crate::algebra::KeyPart::Text(text.clone()),
                        _ => crate::algebra::KeyPart::Text(String::new()),
                    })
                    .collect()
            })
            .unwrap_or_default()
    };
    crate::algebra::meet(&parse(&a), &parse(&b))
}

/// The strictest of merge semantics, or None when none parse.
#[pyfunction]
fn strictest(semantics: Vec<String>) -> Option<String> {
    let parsed: Vec<&str> = semantics.iter().map(|s| s.as_str()).collect();
    let worst = parsed
        .iter()
        .filter_map(|s| MergeSemantics::parse(s))
        .max_by_key(|m| m.rank());
    worst.map(|m| m.as_str().to_string())
}

/// Whether a span sits inside one of the declared spans.
#[pyfunction]
fn covered(span: (i64, i64), spans: Vec<(i64, i64)>) -> bool {
    span_covered(span, &spans)
}

/// Re-span a patch past an earlier ripple.
#[pyfunction]
fn respan<'py>(
    patch: &Bound<'_, PyAny>,
    earlier: &Bound<'_, PyAny>,
    base: &Bound<'_, PyAny>,
    head: &Bound<'_, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let patch = patch_from_value(&py_to_json(patch)?).map_err(PyValueError::new_err)?;
    let earlier: EditPatch = serde_json::from_value(py_to_json(earlier)?)
        .map_err(|err| PyValueError::new_err(format!("not an EditPatch: {err}")))?;
    let base = snapshot_from_value(&py_to_json(base)?)?;
    let head = snapshot_from_value(&py_to_json(head)?)?;
    let moved = compose_respan(&patch, &earlier, &base, &head);
    let value =
        serde_json::to_value(&moved).map_err(|err| PyValueError::new_err(err.to_string()))?;
    json_to_py(py, &value)
}

/// Every capability declaration its own operations break.
#[pyfunction]
fn problems(declarations: &Bound<'_, PyAny>, capability_ids: &Bound<'_, PyAny>) -> Vec<String> {
    let (Ok(declarations), Ok(capability_ids)) =
        (py_to_json(declarations), py_to_json(capability_ids))
    else {
        return vec!["declarations must be objects".into()];
    };
    composition_problems(&declarations, &capability_ids)
}

// ── FilmCraft headless adapter ───────────────────────────────────────
//
// The `ren project-model` verb plans through these instead of carrying
// its own copy of the adapter: the projection, the per-edit
// translation, the receipt reconciliation and the operation-to-command
// mapping all run here, so a plan the verb prints and the run the
// driver executes cannot drift apart.

fn filmcraft_decode_model(
    model: &Bound<'_, PyAny>,
) -> PyResult<ren_protocol::project_model::ProjectModel> {
    let json = py_to_json(model)?;
    let text =
        serde_json::to_string(&json).map_err(|err| PyValueError::new_err(err.to_string()))?;
    ren_protocol::project_model::decode_project_model(&text)
        .map_err(|err| PyValueError::new_err(format!("{err:?}")))
}

fn filmcraft_decode_projection(
    projection: &Bound<'_, PyAny>,
) -> PyResult<ren_filmcraft::adapter::Projection> {
    serde_json::from_value(py_to_json(projection)?)
        .map_err(|err| PyValueError::new_err(format!("not a FilmCraft projection: {err}")))
}

/// Project a model onto the pinned FilmCraft headless driver: imports,
/// placements and losses, exactly what the driver executes.
#[pyfunction]
fn filmcraft_plan_projection<'py>(
    model: &Bound<'_, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let model = filmcraft_decode_model(model)?;
    let projection = ren_filmcraft::adapter::plan_projection(&model)
        .map_err(|err| PyValueError::new_err(err.to_string()))?;
    let value =
        serde_json::to_value(&projection).map_err(|err| PyValueError::new_err(err.to_string()))?;
    json_to_py(py, &value)
}

/// Plan all model-owned picture transforms against reconciled clip ids.
#[pyfunction]
#[pyo3(signature = (model, projection, clip_ids, project))]
fn filmcraft_plan_picture_effects<'py>(
    model: &Bound<'_, PyAny>,
    projection: &Bound<'_, PyAny>,
    clip_ids: std::collections::HashMap<String, Vec<u64>>,
    project: &str,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let model = filmcraft_decode_model(model)?;
    let projection = filmcraft_decode_projection(projection)?;
    let ids: std::collections::BTreeMap<String, Vec<u64>> = clip_ids.into_iter().collect();
    let operations = ren_filmcraft::adapter::project_picture_effect_operations(
        std::path::Path::new(project),
        &model,
        &projection.placements,
        &ids,
    )
    .map_err(|err| PyValueError::new_err(err.to_string()))?;
    let value =
        serde_json::to_value(&operations).map_err(|err| PyValueError::new_err(err.to_string()))?;
    json_to_py(py, &value)
}

/// Provision the overlay tracks a FilmCraft projection addresses.
#[pyfunction]
#[pyo3(signature = (projection, project))]
fn filmcraft_add_tracks_operation<'py>(
    projection: &Bound<'_, PyAny>,
    project: &str,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let projection = filmcraft_decode_projection(projection)?;
    let operation =
        ren_filmcraft::adapter::add_tracks_operation(std::path::Path::new(project), &projection);
    let value = operation
        .map(serde_json::to_value)
        .transpose()
        .map_err(|err| PyValueError::new_err(err.to_string()))?
        .unwrap_or(serde_json::Value::Null);
    json_to_py(py, &value)
}

/// Translate one model edit into driver operations. `clip_ids` maps a
/// placed clip use onto its reconciled engine clip ids; planning with
/// placeholder ids validates every parameter without executing.
#[pyfunction]
#[pyo3(signature = (model, projection, edit, clip_ids, project))]
fn filmcraft_plan_ren_edit<'py>(
    model: &Bound<'_, PyAny>,
    projection: &Bound<'_, PyAny>,
    edit: &Bound<'_, PyAny>,
    clip_ids: std::collections::HashMap<String, Vec<u64>>,
    project: &str,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let model = filmcraft_decode_model(model)?;
    let projection = filmcraft_decode_projection(projection)?;
    let edit: ren_protocol::project_model::EditOperation =
        serde_json::from_value(py_to_json(edit)?)
            .map_err(|err| PyValueError::new_err(format!("not a model edit: {err}")))?;
    let ids: std::collections::BTreeMap<String, Vec<u64>> = clip_ids.into_iter().collect();
    let project_path = std::path::PathBuf::from(project);
    let context = ren_filmcraft::adapter::EditContext {
        project: &project_path,
        clip_ids: &ids,
    };
    let operations = ren_filmcraft::adapter::plan_ren_edit(&model, &projection, &edit, &context)
        .map_err(|err| PyValueError::new_err(err.to_string()))?;
    let value =
        serde_json::to_value(&operations).map_err(|err| PyValueError::new_err(err.to_string()))?;
    json_to_py(py, &value)
}

/// Describe one driver operation as the engine `exec` invocation that
/// runs it: project, operation name, engine command, params, and save.
#[pyfunction]
fn filmcraft_describe_operation<'py>(
    operation: &Bound<'_, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let operation: ren_filmcraft::Operation = serde_json::from_value(py_to_json(operation)?)
        .map_err(|err| PyValueError::new_err(format!("not a FilmCraft operation: {err}")))?;
    let (project, name, command, params, save) =
        ren_filmcraft::plan(operation).map_err(|err| PyValueError::new_err(err.to_string()))?;
    let value = serde_json::json!({
        "project": project,
        "operation": name,
        "command": command,
        "params": params,
        "save": save,
    });
    json_to_py(py, &value)
}

/// Reconcile engine-minted clip ids back onto stable Ren clip-use ids.
/// `receipts` carries each place receipt's `clips` array in placement
/// order; count mismatches refuse rather than guess the pairing.
#[pyfunction]
fn filmcraft_reconcile_clip_ids<'py>(
    projection: &Bound<'_, PyAny>,
    receipts: Vec<Vec<u64>>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let projection = filmcraft_decode_projection(projection)?;
    let reconciled = ren_filmcraft::adapter::reconcile_clip_ids(&projection, &receipts)
        .map_err(|err| PyValueError::new_err(err.to_string()))?;
    let value =
        serde_json::to_value(&reconciled).map_err(|err| PyValueError::new_err(err.to_string()))?;
    json_to_py(py, &value)
}

/// Refuse before the engine runs when referenced media is absent.
#[pyfunction]
fn filmcraft_verify_media<'py>(
    projection: &Bound<'_, PyAny>,
    media_dir: &str,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let projection = filmcraft_decode_projection(projection)?;
    let resolved =
        ren_filmcraft::adapter::verify_media(std::path::Path::new(media_dir), &projection)
            .map_err(|err| PyValueError::new_err(err.to_string()))?;
    let value =
        serde_json::to_value(&resolved).map_err(|err| PyValueError::new_err(err.to_string()))?;
    json_to_py(py, &value)
}

/// The single import operation for a projection over resolved media.
#[pyfunction]
#[pyo3(signature = (projection, media, project))]
fn filmcraft_import_operation<'py>(
    projection: &Bound<'_, PyAny>,
    media: std::collections::HashMap<String, String>,
    project: &str,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let projection = filmcraft_decode_projection(projection)?;
    let resolved: std::collections::BTreeMap<String, std::path::PathBuf> = media
        .into_iter()
        .map(|(key, value)| (key, std::path::PathBuf::from(value)))
        .collect();
    let operation = ren_filmcraft::adapter::import_operation(
        std::path::PathBuf::from(project),
        &resolved,
        &projection,
    );
    let value =
        serde_json::to_value(&operation).map_err(|err| PyValueError::new_err(err.to_string()))?;
    json_to_py(py, &value)
}

/// One `timeline.place` per placement, in projection order. `items`
/// maps asset ids to the engine item ids the import receipt minted.
#[pyfunction]
#[pyo3(signature = (projection, items, project))]
fn filmcraft_placement_operations<'py>(
    projection: &Bound<'_, PyAny>,
    items: std::collections::HashMap<String, u64>,
    project: &str,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let projection = filmcraft_decode_projection(projection)?;
    let ordered: std::collections::BTreeMap<String, u64> = items.into_iter().collect();
    let operations = ren_filmcraft::adapter::placement_operations(
        std::path::Path::new(project),
        &ordered,
        &projection,
    )
    .map_err(|err| PyValueError::new_err(err.to_string()))?;
    let value =
        serde_json::to_value(&operations).map_err(|err| PyValueError::new_err(err.to_string()))?;
    json_to_py(py, &value)
}

/// Plan the adapter's measured dual-mono mapping for one imported source.
#[pyfunction]
#[pyo3(signature = (model, asset_id, item, project, current_mapping))]
fn filmcraft_plan_dual_mono_mapping<'py>(
    model: &Bound<'_, PyAny>,
    asset_id: &str,
    item: u64,
    project: &str,
    current_mapping: &Bound<'_, PyAny>,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyAny>> {
    let model = filmcraft_decode_model(model)?;
    let mapping = py_to_json(current_mapping)?;
    let operation = ren_filmcraft::adapter::plan_dual_mono_mapping(
        &model,
        asset_id,
        item,
        std::path::Path::new(project),
        &mapping,
    )
    .map_err(|err| PyValueError::new_err(err.to_string()))?;
    let value =
        serde_json::to_value(operation).map_err(|err| PyValueError::new_err(err.to_string()))?;
    json_to_py(py, &value)
}

/// The `ren_native` extension module.
#[pymodule]
fn ren_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<SnapshotHandle>()?;
    m.add_function(wrap_pyfunction!(snapshot_handle, m)?)?;
    m.add_function(wrap_pyfunction!(op_ids, m)?)?;
    m.add_function(wrap_pyfunction!(patch_op_ids, m)?)?;
    m.add_function(wrap_pyfunction!(source_fingerprint, m)?)?;
    m.add_function(wrap_pyfunction!(declaration, m)?)?;
    m.add_function(wrap_pyfunction!(span, m)?)?;
    m.add_function(wrap_pyfunction!(span_many, m)?)?;
    m.add_function(wrap_pyfunction!(key, m)?)?;
    m.add_function(wrap_pyfunction!(verify, m)?)?;
    m.add_function(wrap_pyfunction!(verify_many, m)?)?;
    m.add_function(wrap_pyfunction!(judge_conditions, m)?)?;
    m.add_function(wrap_pyfunction!(validate_patch, m)?)?;
    m.add_function(wrap_pyfunction!(compose, m)?)?;
    m.add_function(wrap_pyfunction!(carry, m)?)?;
    m.add_function(wrap_pyfunction!(meet, m)?)?;
    m.add_function(wrap_pyfunction!(strictest, m)?)?;
    m.add_function(wrap_pyfunction!(covered, m)?)?;
    m.add_function(wrap_pyfunction!(respan, m)?)?;
    m.add_function(wrap_pyfunction!(problems, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_plan_projection, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_plan_picture_effects, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_add_tracks_operation, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_plan_ren_edit, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_describe_operation, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_reconcile_clip_ids, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_verify_media, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_import_operation, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_placement_operations, m)?)?;
    m.add_function(wrap_pyfunction!(filmcraft_plan_dual_mono_mapping, m)?)?;
    Ok(())
}
