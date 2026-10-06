//! The ML worker protocol: the typed request/response envelopes a
//! Rust client and a Python ML worker speak over stdio.
//!
//! Modeled from the captain's Rust-core plan (2026-10-05): "Rust ->
//! typed request -> Python ML worker (a model server, like resident
//! Gemma) -> typed response -> Rust." Major subsystems use process
//! boundaries - crash-isolated, versionable, observable, restartable,
//! language-neutral, no GIL - and Python stays for ML.
//!
//! The wire format is newline-delimited JSON on stdin/stdout. Every
//! message carries a `message` tag so a reader can dispatch on it. A
//! request names its capability by id; the worker refuses an unknown
//! capability with a typed refusal, never a hang. Progress is a stream
//! of events on the same channel. Cancellation is cooperative: the
//! worker checks between batches and stops early. Health is a request
//! like any other, answered immediately.
//!
//! One source of truth: the JSON Schemas in `schemas/` are generated
//! from these same definitions, and Python conforms through
//! `library/tools/protocol_conformance.py`.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::capability::CapabilityId;

/// The message kinds the stdio protocol speaks. Every message carries
/// a `message` tag; this enum is the typed view of that tag.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "message", rename_all = "snake_case")]
pub enum MlWorkerMessage {
    /// A request to run one capability.
    Request(MlWorkerRequest),
    /// The worker's answer to a request.
    Response(MlWorkerResponse),
    /// A progress event on a running request.
    Progress(MlWorkerProgress),
    /// A cancellation request for a running request.
    Cancel(MlWorkerCancel),
    /// A health check request.
    Health(MlWorkerHealth),
    /// The worker's health report.
    HealthReport(MlWorkerHealthReport),
}

/// One request to run a capability. The payload is capability-specific
/// JSON; the schema for each capability's payload is a separate
/// generated schema.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MlWorkerRequest {
    /// The request's unique id, chosen by the client. Correlates
    /// responses and progress events to this request.
    pub request_id: String,
    /// The capability to run. The worker refuses an unknown id with
    /// a typed refusal.
    pub capability_id: CapabilityId,
    /// The capability-specific input payload.
    pub payload: Value,
    /// Optional deadline in milliseconds from now. The worker refuses
    /// a request that cannot meet the deadline with a typed refusal.
    #[serde(default)]
    pub deadline_ms: Option<u64>,
}

/// The response's status. Every request gets exactly one response;
/// a refusal is a response with status Refused, not a hang or a
/// crash.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum MlWorkerStatus {
    /// The capability ran and produced a payload.
    Ok,
    /// The worker refused the request: nothing ran, nothing is
    /// half-written, and the refusal says why and what to do instead.
    Refused,
    /// The worker ran the capability and it raised. The error is
    /// typed; the worker itself is still alive and can answer the
    /// next request.
    Error,
}

/// One response to a request. Exactly one of payload, refusal or
/// error is set, matching the status.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MlWorkerResponse {
    /// The id of the request this responds to.
    pub request_id: String,
    /// The outcome.
    pub status: MlWorkerStatus,
    /// The capability-specific output payload. Some when status is
    /// Ok.
    #[serde(default)]
    pub payload: Option<Value>,
    /// The typed refusal. Some when status is Refused.
    #[serde(default)]
    pub refusal: Option<MlWorkerRefusal>,
    /// The typed error. Some when status is Error.
    #[serde(default)]
    pub error: Option<MlWorkerError>,
}

/// A typed refusal: the worker will not run this request. A refusal
/// is not a failure - nothing ran, nothing is half-written, and the
/// next step is known.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct MlWorkerRefusal {
    /// Stable machine-readable code, keyed off by the support bundle
    /// and structured logs instead of parsing prose.
    pub code: String,
    /// What happened.
    pub what: String,
    /// Why it refused.
    pub why: String,
    /// The exact next command or edit.
    pub fix: String,
}

/// A typed error: the worker ran the capability and it raised. The
/// worker itself is still alive and can answer the next request.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct MlWorkerError {
    /// Stable machine-readable code.
    pub code: String,
    /// What happened.
    pub what: String,
    /// The detail: the exception type and message, or the worker's
    /// own description of what went wrong.
    pub detail: String,
}

/// A progress event on a running request. The worker emits these on
/// the same channel as responses; a client that does not care can
/// ignore them.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MlWorkerProgress {
    /// The id of the request this progress is for.
    pub request_id: String,
    /// The phase name, chosen by the capability (e.g. "load_model",
    /// "encode").
    pub phase: String,
    /// Fraction complete in 0.0..=1.0. A capability that cannot
    /// report progress omits the field.
    #[serde(default)]
    pub fraction: Option<f64>,
    /// A human-readable status message.
    #[serde(default)]
    pub message: Option<String>,
}

/// A health check request. The worker answers immediately with a
/// health report, never a queue wait.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MlWorkerHealth {
    /// The request's unique id, like any other request.
    pub request_id: String,
}

/// The worker's health report.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MlWorkerHealthReport {
    /// The id of the health request this reports for.
    pub request_id: String,
    /// Whether the worker is ready to accept requests.
    pub ready: bool,
    /// The model the worker serves, when it serves one.
    #[serde(default)]
    pub model: Option<String>,
    /// Whether the model is loaded into memory.
    #[serde(default)]
    pub model_loaded: bool,
    /// Seconds since the worker process started.
    #[serde(default)]
    pub uptime_seconds: Option<f64>,
}

/// A cancellation request for a running request. Cooperative: the
/// worker checks between batches and stops early. A cancel for an
/// unknown or already-finished request id is answered with a
/// typed refusal, never a hang.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct MlWorkerCancel {
    /// The id of the request to cancel.
    pub request_id: String,
}
