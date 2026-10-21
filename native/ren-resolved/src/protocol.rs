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
    Shutdown {
        /// Who is asking: the pid and the command line, as the client
        /// sent them. The daemon logs it, because a stop that names no
        /// one is a stop nobody can trace (measured 2026-10-21: the
        /// broker's log showed nineteen shutdowns in one day and named
        /// the requester of none).
        #[serde(default)]
        requester: Option<String>,
    },
}

fn default_limit() -> i64 {
    20
}

fn default_wait() -> f64 {
    900.0
}

/// `{"ok": true, "pid": N, "serving": true, "responsive": true,
///   "busy": false, "lease": null, "stale_lease": null}`
#[derive(serde::Serialize)]
pub struct PingReply {
    pub ok: bool,
    pub pid: i64,
    pub serving: bool,
    pub responsive: bool,
    pub busy: bool,
    /// The holder the instance FLOCK says, if any.
    pub lease: Option<LeaseStatus>,
    /// A lease record with no flock behind it: the instance is free and
    /// the record names a holder that is gone (2026-10-20: a dead
    /// holder's record read as "busy" for hours after the lock was
    /// free).
    pub stale_lease: Option<LeaseStatus>,
}

/// A diagnostic snapshot of the exclusive Resolve lease, when one is held.
#[derive(serde::Serialize, serde::Deserialize, Clone)]
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
///
/// PROD-003: a refusal names the KIND of block it was and the move the
/// caller can take, exactly as the Python broker's reply does
/// (`server.py`'s dispatch catches `jobs.JobRefused` and answers
/// `{"error", "refused", "blocker", "next_action"}`). A reply that
/// names neither is a refusal the caller cannot act on.
#[derive(serde::Serialize)]
pub struct RefusedReply {
    pub error: String,
    pub refused: bool,
    /// The kind of block (`refusal_taxonomy.BLOCKERS`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub blocker: Option<String>,
    /// The move the caller can take (`refusal_taxonomy.NEXT_ACTIONS`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub next_action: Option<String>,
}

impl RefusedReply {
    /// A refusal that names its kind of block and its next action.
    pub fn with_action(error: String, blocker: &str, next_action: &str) -> Self {
        RefusedReply {
            error,
            refused: true,
            blocker: Some(blocker.to_string()),
            next_action: Some(next_action.to_string()),
        }
    }

    /// A refusal that names no kind of block - the shape the Python
    /// broker answers a REJECTED job with.
    pub fn plain(error: String) -> Self {
        RefusedReply {
            error,
            refused: true,
            blocker: None,
            next_action: None,
        }
    }
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    /// PROD-003: the Python broker answers a refused submission with
    /// `{"error", "refused", "blocker", "next_action"}` - the same four
    /// keys, so a client reads one refusal shape whichever daemon
    /// served it.
    #[test]
    fn a_refused_reply_names_the_kind_of_block_and_the_next_action() {
        let reply = RefusedReply::with_action(
            "unknown job kind 'bogus'".to_string(),
            "unsupported_operation",
            "use_a_supported_operation",
        );
        let value: Value = serde_json::from_str(&wire(&reply)).unwrap();
        let mut keys: Vec<&String> = value.as_object().unwrap().keys().collect();
        keys.sort();
        assert_eq!(keys, vec!["blocker", "error", "next_action", "refused"]);
        assert_eq!(value["blocker"], "unsupported_operation");
        assert_eq!(value["next_action"], "use_a_supported_operation");
    }

    /// A rejected job names no kind of block, and the reply says so by
    /// leaving the keys out: the Python broker answers that one with the
    /// error alone.
    #[test]
    fn a_refused_reply_with_no_kind_of_block_names_no_key() {
        let reply = RefusedReply::plain("the job was rejected".to_string());
        let value: Value = serde_json::from_str(&wire(&reply)).unwrap();
        let mut keys: Vec<&String> = value.as_object().unwrap().keys().collect();
        keys.sort();
        assert_eq!(keys, vec!["error", "refused"]);
    }
}
