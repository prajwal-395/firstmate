//! The wire protocol: one JSON object per line over the Unix socket, one
//! reply line per request.
//!
//! Byte-compatible with `library/tools/resolved/server.py`'s protocol - the
//! same request shapes, the same reply field names, the same error shapes.
//! A client (`library/tools/resolved/client.py`) cannot tell which daemon
//! serves it. Replies are typed structs so the field set is exact; serde
//! serializes them in declaration order, the order the Python daemon's
//! `receipt(row)` builds them in.

use serde::Deserialize;
use serde_json::Value;

/// One request, as a client sends it. The `op` tag discriminates.
#[derive(Deserialize, Debug)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Request {
    Ping,
    Submit {
        kind: String,
        #[serde(default)]
        params: Value,
        #[serde(default)]
        owner: String,
        #[serde(default)]
        qualification: bool,
        #[serde(default)]
        idempotency_key: Option<String>,
    },
    Status {
        id: String,
    },
    Result {
        id: String,
        #[serde(default)]
        wait: f64,
    },
    Cancel {
        id: String,
    },
    Activity,
    Handoff,
    HandoffStatus,
    ReturnToRen,
    List {
        #[serde(default = "default_limit")]
        limit: i64,
    },
    Acquire {
        #[serde(default)]
        params: Value,
        #[serde(default)]
        owner: String,
        #[serde(default)]
        qualification: bool,
        #[serde(default = "default_wait")]
        wait: f64,
    },
    Release,
    Health,
    PostRestartCheck,
    WatchdogStatus,
    Shutdown,
}

fn default_limit() -> i64 {
    20
}

fn default_wait() -> f64 {
    900.0
}

/// `{"ok": true, "pid": N, "serving": true, "responsive": true,
///   "busy": false, "lease": null}`
#[derive(serde::Serialize)]
pub struct PingReply {
    pub ok: bool,
    pub pid: i64,
    pub serving: bool,
    pub responsive: bool,
    pub busy: bool,
    pub lease: Option<LeaseStatus>,
}

/// A diagnostic snapshot of the exclusive Resolve lease, when one is held.
#[derive(serde::Serialize, serde::Deserialize)]
pub struct LeaseStatus {
    pub owner: String,
    pub purpose: String,
    pub pid: i64,
    pub host: String,
    pub since: f64,
    #[serde(default)]
    pub wait_seconds: f64,
    #[serde(default)]
    pub waited_on: String,
}

/// `{"id": ..., "coalesced": bool}`
#[derive(serde::Serialize)]
pub struct SubmitReply {
    pub id: String,
    pub coalesced: bool,
}

/// `{"job": <receipt>}` - `null` where the store does not know the job, as
/// the Python daemon's `broker.result` returns None.
#[derive(serde::Serialize)]
pub struct JobReply {
    pub job: Option<crate::store::Receipt>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub interrupt_policy: Option<String>,
}

#[derive(serde::Serialize)]
pub struct ActivityReply {
    pub count: usize,
    pub jobs: Vec<serde_json::Value>,
}

#[derive(serde::Serialize)]
pub struct HandoffReply {
    pub fence: serde_json::Value,
    pub request_accepted: Vec<String>,
    pub waiting_for_completion: Vec<String>,
    pub job_ids: Vec<String>,
}

#[derive(serde::Serialize)]
pub struct HandoffStatusReply {
    pub fenced: bool,
    pub fence: Option<serde_json::Value>,
    pub ready: bool,
    pub active_jobs: Vec<crate::store::Receipt>,
    pub lease: Option<LeaseStatus>,
}

#[derive(serde::Serialize)]
pub struct ReturnToRenReply {
    pub returned: bool,
    pub fence_cleared: bool,
}

/// `{"jobs": [...], "running": [...]}`
#[derive(serde::Serialize)]
pub struct ListReply {
    pub jobs: Vec<crate::store::Receipt>,
    pub running: Vec<crate::store::RunningJob>,
}

/// `{"granted": ID}`
#[derive(serde::Serialize)]
pub struct GrantedReply {
    pub granted: String,
}

/// `{"released": ID}`
#[derive(serde::Serialize)]
pub struct ReleasedReply {
    pub released: String,
}

/// `{"connected": bool, "responsive": bool}`
#[derive(serde::Serialize)]
pub struct HealthReply {
    pub connected: bool,
    pub responsive: bool,
}

/// `{"watchdog": false}`
#[derive(serde::Serialize)]
pub struct WatchdogReply {
    pub watchdog: bool,
}

/// `{"watchdog": true, ...status}`
#[derive(serde::Serialize)]
pub struct WatchdogStatusReply {
    pub watchdog: bool,
    pub connected: bool,
    pub responsive: bool,
    pub degraded: bool,
    pub degraded_at: Option<f64>,
    pub degraded_reason: String,
    pub recovered_at: Option<f64>,
    pub holder: String,
    pub job_id: String,
}

/// `{"ok": true}`
#[derive(serde::Serialize)]
pub struct OkReply {
    pub ok: bool,
}

/// `{"error": "..."}`
#[derive(serde::Serialize)]
pub struct ErrorReply {
    pub error: String,
}

/// `{"error": "...", "refused": true}`
#[derive(serde::Serialize)]
pub struct RefusedReply {
    pub error: String,
    pub refused: bool,
}

/// `{"error": "timed out waiting for Resolve", "busy": true, "running": [...]}`
#[derive(serde::Serialize)]
pub struct BusyReply {
    pub error: String,
    pub busy: bool,
    pub running: Vec<crate::store::RunningJob>,
}

/// The post-restart check result (`resolve_watchdog.post_restart_check`).
#[derive(serde::Serialize)]
pub struct PostRestartCheckReply {
    pub ok: bool,
    pub project: String,
    pub timeline: String,
    pub timeline_count: i64,
    pub error: String,
    pub timelines_intact: Vec<String>,
    pub timelines_stale: Vec<Value>,
}

/// Serialize one reply to the wire form: compact JSON plus a trailing
/// newline. The Python daemon sends `json.dumps(payload)` (default
/// separators, spaces); this daemon sends compact JSON. The client parses
/// both identically - the semantic content is the same, and the parity
/// test compares parsed replies.
pub fn wire<T: serde::Serialize>(reply: &T) -> String {
    let mut line = serde_json::to_string(reply).unwrap_or_else(|_| "{}".to_string());
    line.push('\n');
    line
}
