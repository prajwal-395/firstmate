//! The PyO3 boundary: the `ren_native` extension module.
//!
//! Every function takes plain Python objects and converts them to the
//! typed Rust forms, so the editorial correctness logic runs over
//! ren-protocol's `EditOp`/`EditPatch` types with the compiler checking
//! every match. The Python side (`library/tools/edit_backend.py`) is the
//! only caller; nothing here reaches Resolve.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList, PyModule};
use serde_json::Value;

use ren_protocol::edit::{Condition, EditOp, EditPatch};

use crate::algebra::{
    declaration as algebra_declaration, key_strings, op_ids as algebra_op_ids,
    patch_op_ids as algebra_patch_op_ids, span as op_span, MergeSemantics,
};
use crate::compose::{
    carry as compose_carry, compose as compose_patches, problems as composition_problems,
    respan as compose_respan, CarryRow,
};
use crate::conditions::judge_all;
use crate::patch::{covered as span_covered, validate_patch as validate_patch_dict};
use crate::snapshot::Snapshot;
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

/// The typed op for a JSON op, or None when the op id is not in the
/// algebra (the Python side's `ALGEBRA.get` returns None there too).
fn op_from_value(value: &Value) -> Option<EditOp> {
    let op_id = value.get("op").and_then(|v| v.as_str())?;
    if crate::algebra::declaration(op_id).is_none() {
        return None;
    }
    serde_json::from_value(value.clone()).ok()
}

fn patch_from_value(value: &Value) -> Result<EditPatch, String> {
    serde_json::from_value(value.clone()).map_err(|err| format!("not an EditPatch: {err}"))
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
    let base = snapshot_from_value(&py_to_json(base)?)?;
    Ok(op_from_value(&op_value).and_then(|op| op_span(&op, &base)))
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
    let after = snapshot_from_value(&py_to_json(after)?)?;
    Ok(op_from_value(&op_value).and_then(|op| op_verify(&op, &after)))
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
    let earlier: EditPatch = serde_json::from_value(py_to_json(earlier)?)
        .map_err(|err| PyValueError::new_err(format!("not an EditPatch: {err}")))?;
    let later: EditPatch = serde_json::from_value(py_to_json(later)?)
        .map_err(|err| PyValueError::new_err(format!("not an EditPatch: {err}")))?;
    let base = snapshot_from_value(&py_to_json(base)?)?;
    let a_sem = py_to_json(a_sem)?;
    let b_sem = py_to_json(b_sem)?;
    let found = compose_patches(&earlier, &later, &base, &a_sem, &b_sem);
    let value = serde_json::json!({
        "verdict": found.verdict.as_str(),
        "reason": found.reason,
    });
    json_to_py(py, &value)
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

/// The `ren_native` extension module.
#[pymodule]
fn ren_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(op_ids, m)?)?;
    m.add_function(wrap_pyfunction!(patch_op_ids, m)?)?;
    m.add_function(wrap_pyfunction!(declaration, m)?)?;
    m.add_function(wrap_pyfunction!(span, m)?)?;
    m.add_function(wrap_pyfunction!(key, m)?)?;
    m.add_function(wrap_pyfunction!(verify, m)?)?;
    m.add_function(wrap_pyfunction!(judge_conditions, m)?)?;
    m.add_function(wrap_pyfunction!(validate_patch, m)?)?;
    m.add_function(wrap_pyfunction!(compose, m)?)?;
    m.add_function(wrap_pyfunction!(carry, m)?)?;
    m.add_function(wrap_pyfunction!(meet, m)?)?;
    m.add_function(wrap_pyfunction!(strictest, m)?)?;
    m.add_function(wrap_pyfunction!(covered, m)?)?;
    m.add_function(wrap_pyfunction!(respan, m)?)?;
    m.add_function(wrap_pyfunction!(problems, m)?)?;
    Ok(())
}
