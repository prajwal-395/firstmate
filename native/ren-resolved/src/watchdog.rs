//! A Resolve-degraded watchdog that stops queue growth.
//!
//! A direct port of `library/tools/resolve_watchdog.py`: the same bounded
//! probe (connect ~10s, round trip ~5s), the same degraded mark, the same
//! recovery on a later successful probe, the same never-auto-restart. The
//! one difference is where the probe RUNS: the Python watchdog probes from
//! the broker's own thread, and the Rust daemon hands each probe to the
//! execution worker, which takes the shared instance lease and connects.
//!
//! The probe's lease is the regression guard: a probe with no lease is
//! refused the scripting handshake whenever the broker is idle, and the
//! first probe marks Resolve degraded permanently - a wedged broker. So
//! the worker takes a shared lease (it only reads) with
//! `honor_captain=False` (a health check is not the captain's action), and
//! a lease another writer holds is skipped rather than marked: contention
//! is not a hang.

use serde_json::Value;
use std::sync::{Arc, Mutex};

use crate::worker;

/// How often the watchdog probes Resolve while idle.
pub const PROBE_INTERVAL_S: f64 = 30.0;

/// The watchdog's current state, for `watchdog_status`.
#[derive(Clone, Debug)]
pub struct WatchdogStatus {
    pub connected: bool,
    pub responsive: bool,
    pub degraded: bool,
    pub degraded_at: Option<f64>,
    pub degraded_reason: String,
    pub recovered_at: Option<f64>,
    pub holder: String,
    pub job_id: String,
}

/// A handle the broker reads: is Resolve degraded, and its status.
pub struct WatchdogHandle {
    inner: Arc<Mutex<WatchdogStatus>>,
}

impl WatchdogHandle {
    pub fn is_degraded(&self) -> bool {
        self.inner.lock().unwrap().degraded
    }

    pub fn status(&self) -> WatchdogStatus {
        self.inner.lock().unwrap().clone()
    }
}

/// The watchdog: a background thread that probes Resolve on a schedule.
pub struct Watchdog {
    inner: Arc<Mutex<WatchdogStatus>>,
    thread: Option<std::thread::JoinHandle<()>>,
    stop: Arc<Mutex<bool>>,
}

impl Watchdog {
    pub fn new() -> Self {
        Watchdog {
            inner: Arc::new(Mutex::new(WatchdogStatus {
                connected: false,
                responsive: true,
                degraded: false,
                degraded_at: None,
                degraded_reason: String::new(),
                recovered_at: None,
                holder: String::new(),
                job_id: String::new(),
            })),
            thread: None,
            stop: Arc::new(Mutex::new(false)),
        }
    }

    /// The handle the broker reads.
    pub fn handle(&self) -> WatchdogHandle {
        WatchdogHandle {
            inner: Arc::clone(&self.inner),
        }
    }

    pub fn start(&mut self, execution_worker: Arc<Mutex<worker::Worker>>) {
        let inner = Arc::clone(&self.inner);
        let stop = Arc::clone(&self.stop);
        self.thread = Some(std::thread::spawn(move || {
            watchdog_loop(inner, stop, execution_worker);
        }));
    }

    pub fn stop(&self) {
        *self.stop.lock().unwrap() = true;
    }

    /// The status as the `watchdog_status` reply reports it.
    pub fn status_reply(&self) -> crate::protocol::WatchdogStatusReply {
        let status = self.inner.lock().unwrap();
        crate::protocol::WatchdogStatusReply {
            watchdog: true,
            connected: status.connected,
            responsive: status.responsive,
            degraded: status.degraded,
            degraded_at: status.degraded_at,
            degraded_reason: status.degraded_reason.clone(),
            recovered_at: status.recovered_at,
            holder: status.holder.clone(),
            job_id: status.job_id.clone(),
        }
    }
}

impl Drop for Watchdog {
    fn drop(&mut self) {
        self.stop();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn watchdog_loop(
    inner: Arc<Mutex<WatchdogStatus>>,
    stop: Arc<Mutex<bool>>,
    execution_worker: Arc<Mutex<worker::Worker>>,
) {
    loop {
        // Wait one probe interval, or until stopped.
        let deadline = crate::store::now() + PROBE_INTERVAL_S;
        loop {
            if *stop.lock().unwrap() {
                return;
            }
            let remaining = deadline - crate::store::now();
            if remaining <= 0.0 {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        if *stop.lock().unwrap() {
            return;
        }
        probe(&inner, &execution_worker);
    }
}

/// One bounded probe, in the execution worker.
///
/// The worker takes a shared instance lease and connects; the daemon marks
/// or clears the degraded flag from the answer. A probe the worker could
/// not take the lease for (contention) is skipped, not marked.
fn probe(inner: &Arc<Mutex<WatchdogStatus>>, execution_worker: &Arc<Mutex<worker::Worker>>) {
    let request = worker::probe_request("");
    let response = match execution_worker.lock().unwrap().run(&request) {
        Ok(response) => response,
        Err(exc) => {
            mark_degraded(inner, &format!("probe failed: {exc}"), "", "");
            return;
        }
    };
    let result = match response.result {
        Some(result) => result,
        None => {
            mark_degraded(inner, &response.error, "", "");
            return;
        }
    };
    if response.ok {
        let connected = result.get("connected").and_then(|v| v.as_bool()).unwrap_or(false);
        let responsive = result.get("responsive").and_then(|v| v.as_bool()).unwrap_or(false);
        let mut status = inner.lock().unwrap();
        status.connected = connected;
        status.responsive = responsive;
        if status.degraded {
            // A bounded probe succeeded while degraded: Resolve recovered.
            status.degraded = false;
            status.degraded_at = None;
            status.degraded_reason = String::new();
            status.recovered_at = Some(crate::store::now());
        }
        return;
    }
    // A probe that could not take the lease is contention, not a hang.
    let contention = result.get("contention").and_then(|v| v.as_bool()).unwrap_or(false);
    if contention {
        return;
    }
    let error = result
        .get("error")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    mark_degraded(inner, &format!("probe failed: {error}"), "", "");
}

fn mark_degraded(inner: &Arc<Mutex<WatchdogStatus>>, reason: &str, holder: &str, job_id: &str) {
    let mut status = inner.lock().unwrap();
    status.degraded = true;
    status.degraded_at = Some(crate::store::now());
    status.degraded_reason = reason.to_string();
    status.holder = holder.to_string();
    status.job_id = job_id.to_string();
}

/// The health status, from the execution worker.
pub fn health_reply(execution_worker: &Arc<Mutex<worker::Worker>>) -> crate::protocol::HealthReply {
    let request = worker::health_request("");
    match execution_worker.lock().unwrap().run(&request) {
        Ok(response) => {
            let result = response.result.unwrap_or(Value::Null);
            crate::protocol::HealthReply {
                connected: result.get("connected").and_then(|v| v.as_bool()).unwrap_or(false),
                responsive: result.get("responsive").and_then(|v| v.as_bool()).unwrap_or(false),
            }
        }
        Err(_) => crate::protocol::HealthReply {
            connected: false,
            responsive: false,
        },
    }
}

/// The post-restart check, from the execution worker.
pub fn post_restart_check_reply(
    execution_worker: &Arc<Mutex<worker::Worker>>,
) -> crate::protocol::PostRestartCheckReply {
    let request = worker::post_restart_check_request("");
    match execution_worker.lock().unwrap().run(&request) {
        Ok(response) => {
            let result = response.result.unwrap_or(Value::Null);
            crate::protocol::PostRestartCheckReply {
                ok: result.get("ok").and_then(|v| v.as_bool()).unwrap_or(false),
                project: result.get("project").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                timeline: result.get("timeline").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                timeline_count: result.get("timeline_count").and_then(|v| v.as_i64()).unwrap_or(0),
                error: result.get("error").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                timelines_intact: result
                    .get("timelines_intact")
                    .and_then(|v| v.as_array())
                    .map(|items| items.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                    .unwrap_or_default(),
                timelines_stale: result
                    .get("timelines_stale")
                    .and_then(|v| v.as_array())
                    .cloned()
                    .unwrap_or_default(),
            }
        }
        Err(exc) => crate::protocol::PostRestartCheckReply {
            ok: false,
            project: String::new(),
            timeline: String::new(),
            timeline_count: 0,
            error: exc,
            timelines_intact: Vec::new(),
            timelines_stale: Vec::new(),
        },
    }
}
