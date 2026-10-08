//! The Unix socket server: one JSON object per line, one reply per request.
//!
//! A direct port of `library/tools/resolved/server.py`'s `_Handler` and
//! `_Server`: the same dispatch, the same grant bound to its connection
//! (a client that dies mid-section releases its grant by closing the
//! socket), the same shutdown. Each connection is a thread; the broker is
//! shared.

use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::Arc;

use crate::broker::Broker;
use crate::protocol::{
    self, BusyReply, ErrorReply, GrantedReply, JobReply, ListReply, OkReply, PingReply,
    RefusedReply, ReleasedReply, Request, SubmitReply, WatchdogReply,
};
use crate::store::JobStore;
use crate::watchdog::Watchdog;

/// The socket name, as the Python daemon's `SOCKET_NAME`.
pub const SOCKET_NAME: &str = "ren-resolved.sock";
/// The database name, as the Python daemon's `DB_NAME`.
pub const DB_NAME: &str = "ren-resolved.sqlite3";
pub const LEASE_NAME: &str = "resolve_instance.lease.json";

/// The lock directory: `PIPELINE_RESOLVE_LOCK_DIR`, else the system temp -
/// the same rule as `resolve_lock.lock_dir`.
pub fn lock_dir() -> std::path::PathBuf {
    if let Some(value) = std::env::var_os("PIPELINE_RESOLVE_LOCK_DIR") {
        let text = value.to_string_lossy().to_string();
        if !text.is_empty() {
            return std::path::PathBuf::from(text);
        }
    }
    std::env::temp_dir()
}

pub fn socket_path() -> std::path::PathBuf {
    lock_dir().join(SOCKET_NAME)
}

pub fn db_path() -> std::path::PathBuf {
    lock_dir().join(DB_NAME)
}

/// The daemon: the socket, the broker and the watchdog, minus the process.
pub struct Daemon {
    pub broker: Arc<Broker>,
    pub watchdog: Arc<Watchdog>,
    /// Set by the `shutdown` op; the serve loop notices and exits.
    pub shutdown: Arc<std::sync::atomic::AtomicBool>,
}

impl Daemon {
    pub fn new(store: Arc<JobStore>) -> Self {
        let mut broker = Broker::new(store);
        broker.start();
        let mut watchdog = Watchdog::new();
        let execution_worker = Arc::clone(&broker.execution_worker);
        watchdog.start(execution_worker);
        let handle = watchdog.handle();
        broker.set_watchdog(handle);
        Daemon {
            broker: Arc::new(broker),
            watchdog: Arc::new(watchdog),
            shutdown: Arc::new(std::sync::atomic::AtomicBool::new(false)),
        }
    }

    /// Dispatch one request on one connection. `grant` is the grant this
    /// connection holds, if any.
    pub fn dispatch(&self, request: &Request, grant: &mut Option<String>) -> String {
        match request {
            Request::Ping => {
                let lease = active_lease();
                protocol::wire(&PingReply {
                    ok: true,
                    pid: std::process::id() as i64,
                    serving: true,
                    responsive: true,
                    busy: lease.is_some(),
                    lease,
                })
            }
            Request::Submit { kind, params, owner, qualification, idempotency_key } => {
                match self.broker.submit(kind, params.clone(), owner, *qualification,
                                        idempotency_key.as_deref()) {
                    Ok(submission) => protocol::wire(&SubmitReply {
                        id: submission.id,
                        coalesced: submission.coalesced,
                    }),
                    Err(refusal) => protocol::wire(&RefusedReply {
                        error: refusal.message,
                        refused: true,
                    }),
                }
            }
            Request::Status { id } => {
                let job = self.broker.result(id, 0.0);
                protocol::wire(&JobReply { job })
            }
            Request::Result { id, wait } => {
                let job = self.broker.result(id, *wait);
                protocol::wire(&JobReply { job })
            }
            Request::Cancel { id } => {
                match self.broker.request_cancel(id) {
                    Ok(job) => protocol::wire(&JobReply { job }),
                    Err(error) => protocol::wire(&ErrorReply { error }),
                }
            }
            Request::List { limit } => {
                let jobs = self.broker.store.recent(*limit);
                let running = self.broker.running();
                protocol::wire(&ListReply { jobs, running })
            }
            Request::Acquire { params, owner, qualification, wait } => {
                self.acquire(params.clone(), owner, *qualification, *wait, grant)
            }
            Request::Release => {
                let held = grant.take();
                match held {
                    Some(job_id) => {
                        self.broker.release(&job_id, "done", "");
                        protocol::wire(&ReleasedReply { released: job_id })
                    }
                    None => protocol::wire(&ErrorReply {
                        error: "nothing is held on this connection".to_string(),
                    }),
                }
            }
            Request::Health => {
                let execution_worker = Arc::clone(&self.broker.execution_worker);
                let reply = crate::watchdog::health_reply(&execution_worker);
                protocol::wire(&reply)
            }
            Request::PostRestartCheck => {
                let execution_worker = Arc::clone(&self.broker.execution_worker);
                let reply = crate::watchdog::post_restart_check_reply(&execution_worker);
                protocol::wire(&reply)
            }
            Request::WatchdogStatus => {
                if self.broker.watchdog.lock().unwrap().is_some() {
                    protocol::wire(&self.watchdog.status_reply())
                } else {
                    protocol::wire(&WatchdogReply { watchdog: false })
                }
            }
            Request::Shutdown => {
                self.broker.stop();
                self.shutdown
                    .store(true, std::sync::atomic::Ordering::Relaxed);
                protocol::wire(&OkReply { ok: true })
            }
        }
    }

    /// The `acquire` op: submit a lease job and wait for it to start.
    fn acquire(&self, params: Value, owner: &str, qualification: bool,
               wait: f64, grant: &mut Option<String>) -> String {
        let submission = match self.broker.submit("lease", params, owner, qualification, None) {
            Ok(submission) => submission,
            Err(refusal) => {
                return protocol::wire(&RefusedReply {
                    error: refusal.message,
                    refused: true,
                });
            }
        };
        let job_id = submission.id;
        let started = self.broker.wait_started(&job_id, wait);
        let job = self.broker.result(&job_id, 0.0);
        let job = match job {
            Some(job) => job,
            None => {
                self.broker.release(&job_id, "done", "the client's wait ran out");
                return protocol::wire(&BusyReply {
                    error: "timed out waiting for Resolve".to_string(),
                    busy: true,
                    running: self.broker.running(),
                });
            }
        };
        if !started {
            self.broker.release(&job_id, "done", "the client's wait ran out");
            return protocol::wire(&BusyReply {
                error: "timed out waiting for Resolve".to_string(),
                busy: true,
                running: self.broker.running(),
            });
        }
        if job.state == "running" {
            *grant = Some(job_id);
            return protocol::wire(&GrantedReply { granted: grant.clone().unwrap() });
        }
        if job.state == "rejected" {
            return protocol::wire(&RefusedReply {
                error: job.error,
                refused: true,
            });
        }
        self.broker.release(&job_id, "done", "the client's wait ran out");
        protocol::wire(&BusyReply {
            error: "timed out waiting for Resolve".to_string(),
            busy: true,
            running: self.broker.running(),
        })
    }
}

/// Read the diagnostic lease record without entering the Resolve worker.
fn active_lease() -> Option<protocol::LeaseStatus> {
    let path = lock_dir().join(LEASE_NAME);
    let body = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&body).ok()
}

/// Serve on the socket until `shutdown` or a signal.
///
/// A short poll interval, as the Python daemon's `serve_forever`:
/// `shutdown` is answered at once, but the serve loop notices on its next
/// tick - and a client that was asked to stop should be gone by the time
/// `stop` returns.
pub fn serve(path: &Path, daemon: Arc<Daemon>) -> std::io::Result<()> {
    if path.exists() {
        // A dead broker's socket: nobody answers it.
        std::fs::remove_file(path).ok();
    }
    let listener = UnixListener::bind(path)?;
    std::fs::set_permissions(path, std::os::unix::fs::PermissionsExt::from_mode(0o600))?;
    listener.set_nonblocking(true)?;
    loop {
        if daemon.shutdown.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        match listener.accept() {
            Ok((stream, _)) => {
                let daemon = Arc::clone(&daemon);
                std::thread::spawn(move || {
                    // The listener is nonblocking for the accept poll; an
                    // accepted stream inherits that, and a read with no
                    // data ready (a client idle between requests, such as
                    // a grant held across a sleep) would fail as EAGAIN
                    // and look like a disconnect. Connections are
                    // blocking, as the Python daemon's are.
                    let _ = stream.set_nonblocking(false);
                    handle_connection(stream, &daemon);
                });
            }
            Err(exc) if exc.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            Err(_) => break,
        }
    }
    Ok(())
}

/// Parse one request line. An unknown `op` is answered as the Python
/// daemon answers it - `{"error": "unknown op 'X'"}` - not as a
/// deserialization failure.
fn parse_request(line: &str) -> Result<Request, String> {
    let value: Value = match serde_json::from_str(line) {
        Ok(value) => value,
        Err(exc) => {
            return Err(protocol::wire(&ErrorReply {
                error: format!("the request was not JSON: {exc}"),
            }))
        }
    };
    let op = value.get("op").and_then(|v| v.as_str()).unwrap_or("");
    const KNOWN: [&str; 12] = [
        "ping", "submit", "status", "result", "cancel", "list", "acquire",
        "release", "health", "post_restart_check", "watchdog_status",
        "shutdown",
    ];
    if !KNOWN.contains(&op) {
        return Err(protocol::wire(&ErrorReply {
            error: format!("unknown op '{op}'"),
        }));
    }
    match serde_json::from_value::<Request>(value) {
        Ok(request) => Ok(request),
        Err(exc) => Err(protocol::wire(&ErrorReply {
            error: format!("the request was not valid: {exc}"),
        })),
    }
}

fn handle_connection(stream: UnixStream, daemon: &Daemon) {
    let mut reader = BufReader::new(stream.try_clone().expect("socket clone"));
    let mut writer = stream;
    let mut grant: Option<String> = None;
    let mut buffer = String::new();
    loop {
        buffer.clear();
        match reader.read_line(&mut buffer) {
            Ok(0) => break,
            Ok(_) => {}
            Err(_) => break,
        }
        let request: Request = match parse_request(buffer.trim()) {
            Ok(request) => request,
            Err(reply) => {
                let _ = writer.write_all(reply.as_bytes());
                let _ = writer.flush();
                continue;
            }
        };
        let reply = daemon.dispatch(&request, &mut grant);
        if writer.write_all(reply.as_bytes()).is_err() {
            break;
        }
        if writer.flush().is_err() {
            break;
        }
        if matches!(request, Request::Shutdown) {
            break;
        }
    }
    // A client that dies mid-section releases its grant by closing the
    // socket, so the queue never wedges on a dead holder.
    if let Some(job_id) = grant.take() {
        daemon.broker.release(&job_id, "done", "the client disconnected while holding");
    }
}
