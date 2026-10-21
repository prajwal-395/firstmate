//! The Unix socket server: one JSON object per line, one reply per request.
//!
//! A direct port of `library/tools/resolved/server.py`'s `_Handler` and
//! `_Server`: the same dispatch, the same grant bound to its connection
//! (a client that dies mid-section releases its grant by closing the
//! socket), the same shutdown. Each connection is a thread; the broker is
//! shared.

use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::os::fd::AsRawFd;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

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
/// Serializes the bind itself, so two brokers cannot both pass the
/// "nobody is serving" probe and one orphan the other.
pub const BIND_LOCK_NAME: &str = "ren-resolved.bind.lock";
/// An explicit override of the machine broker's directory.
pub const BROKER_DIR_ENV: &str = "PIPELINE_RESOLVE_BROKER_DIR";
/// The broker tells the processes it starts where its own directory is.
pub const BROKER_DIR_TOLD_ENV: &str = "REN_RESOLVED_BROKER_DIR";
/// The instance directory's override, as `resolve_lock` spells it.
pub const INSTANCE_DIR_ENV: &str = "PIPELINE_RESOLVE_INSTANCE_DIR";

/// The lock directory: `PIPELINE_RESOLVE_LOCK_DIR`, else the system temp -
/// the same rule as `resolve_lock.lock_dir`. The broker's socket,
/// database and cancel markers live in `broker_dir`, not here.
pub fn lock_dir() -> std::path::PathBuf {
    if let Some(value) = std::env::var_os("PIPELINE_RESOLVE_LOCK_DIR") {
        let text = value.to_string_lossy().to_string();
        if !text.is_empty() {
            return std::path::PathBuf::from(text);
        }
    }
    std::env::temp_dir()
}

/// Where the Resolve INSTANCE lease lives: one per machine, shared by
/// every broker driving the one Resolve. Mirrors
/// `resolve_lock.instance_lock_dir`: an explicit
/// `PIPELINE_RESOLVE_INSTANCE_DIR` wins; inside the test suite with no
/// explicit redirect the broker lock dir keeps isolating it; otherwise
/// the system temp, whatever `PIPELINE_RESOLVE_LOCK_DIR` says.
pub fn instance_lock_dir() -> std::path::PathBuf {
    if let Some(value) = std::env::var_os(INSTANCE_DIR_ENV) {
        let text = value.to_string_lossy().to_string();
        if !text.is_empty() {
            return std::path::PathBuf::from(text);
        }
    }
    if in_tests() {
        return lock_dir();
    }
    std::env::temp_dir()
}

/// Whether this process runs inside the test suite: the same signal
/// `resolve_lock` uses, so the suite's brokers keep their own
/// directory.
fn in_tests() -> bool {
    std::env::var_os("PYTEST_CURRENT_TEST").is_some_and(|v| !v.is_empty())
}

fn env_path(name: &str) -> Option<std::path::PathBuf> {
    std::env::var_os(name).and_then(|value| {
        let text = value.to_string_lossy().to_string();
        if text.is_empty() {
            None
        } else {
            Some(std::path::PathBuf::from(text))
        }
    })
}

/// The machine broker's directory OUTSIDE the test suite: where the
/// production broker serves, whatever this process isolated.
/// `PIPELINE_RESOLVE_INSTANCE_DIR` is deliberately NOT read here: it
/// redirects the instance directory for a test that must not compete
/// with the live captain, and a test's redirect is not the production
/// socket.
pub fn production_dir() -> std::path::PathBuf {
    if let Some(path) = env_path(BROKER_DIR_ENV) {
        return path;
    }
    std::env::temp_dir()
}

/// The directory this machine's ONE broker keeps its files in, when no
/// socket has been named yet. Mirrors
/// `resolved/broker_paths.broker_dir`: the machine-wide instance
/// directory outside the test suite, the suite's private lock directory
/// inside it.
pub fn derived_dir() -> std::path::PathBuf {
    if let Some(path) = env_path(BROKER_DIR_ENV) {
        return path;
    }
    instance_lock_dir()
}

/// The socket this daemon serves on, once it is known.
static SOCKET_PATH: OnceLock<std::path::PathBuf> = OnceLock::new();

/// Record the socket this daemon serves on. The broker's directory is
/// the directory its socket lives in, so a deliberately private broker
/// keeps its own queue, state and markers.
pub fn set_socket_path(path: &Path) {
    let _ = SOCKET_PATH.set(path.to_path_buf());
}

/// The broker's directory: the socket's own directory.
///
/// The broker tells the processes it starts where that is
/// (`REN_RESOLVED_BROKER_DIR`), so a worker writes its cancellation
/// markers beside the broker that queued the job.
pub fn broker_dir() -> std::path::PathBuf {
    if let Some(path) = env_path(BROKER_DIR_TOLD_ENV) {
        return path;
    }
    if let Some(path) = SOCKET_PATH.get() {
        return path
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_else(derived_dir);
    }
    derived_dir()
}

/// The production broker's socket: the machine's, whatever this
/// process isolated.
pub fn production_socket_path() -> std::path::PathBuf {
    production_dir().join(SOCKET_NAME)
}

/// Whether `path` is the production broker's socket.
pub fn is_production_socket(path: &Path) -> bool {
    let left = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let right = std::fs::canonicalize(production_socket_path())
        .unwrap_or_else(|_| production_socket_path());
    left == right
}

/// Whether `path` is the socket this machine's broker serves on.
pub fn is_machine_socket(path: &Path) -> bool {
    let machine = derived_dir().join(SOCKET_NAME);
    let left = std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
    let right = std::fs::canonicalize(&machine).unwrap_or(machine);
    left == right
}

/// Whether this broker would be the machine's broker, started where it
/// must not be: the machine's socket, served from a linked worktree.
pub fn pointed_at_a_worker_worktree(path: &Path) -> bool {
    let production = is_production_socket(path);
    if !(production || is_machine_socket(path)) {
        return false;
    }
    // The test suite's own private machine is exempt; the production
    // socket never is.
    if !production && in_tests() {
        return false;
    }
    linked_worktree()
}

/// Whether the current directory is a LINKED git worktree - a checkout
/// a worktree tool may reclaim. A broker started there would serve
/// production Resolve calls on unmerged code.
pub fn linked_worktree() -> bool {
    let (Some(git_dir), Some(common_dir)) = (git_path("--git-dir"), git_path("--git-common-dir"))
    else {
        return false;
    };
    let left = std::fs::canonicalize(&git_dir).unwrap_or(git_dir);
    let right = std::fs::canonicalize(&common_dir).unwrap_or(common_dir);
    left != right
}

/// One absolute git path, or None where git cannot answer.
fn git_path(flag: &str) -> Option<std::path::PathBuf> {
    let directory = std::env::current_dir().ok()?;
    let output = Command::new("git")
        .args(["rev-parse", "--path-format=absolute", flag])
        .current_dir(directory)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(std::path::PathBuf::from(text))
    }
}

/// What a probe of a socket path found.
pub enum Probe {
    /// A broker answered.
    Answer(String),
    /// Something holds the socket but did not answer: a wedged broker.
    /// Fail closed - its socket is not ours to take.
    Silent,
    /// Nobody holds the socket: the file is a dead broker's.
    Absent,
}

/// Ask whoever holds `path` whether a broker is serving there.
pub fn probe(path: &Path) -> Probe {
    let stream = match std::os::unix::net::UnixStream::connect(path) {
        Ok(stream) => stream,
        Err(_) => return Probe::Absent,
    };
    let mut stream = stream;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    if stream.write_all(b"{\"op\": \"ping\"}\n").is_err() {
        return Probe::Silent;
    }
    let _ = stream.flush();
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    match reader.read_line(&mut line) {
        Ok(0) => Probe::Silent,
        Ok(_) => Probe::Answer(line),
        Err(_) => Probe::Silent,
    }
}

pub fn socket_path() -> std::path::PathBuf {
    if let Some(path) = SOCKET_PATH.get() {
        return path.clone();
    }
    derived_dir().join(SOCKET_NAME)
}

pub fn db_path() -> std::path::PathBuf {
    broker_dir().join(DB_NAME)
}

pub fn handoff_fence_path() -> std::path::PathBuf {
    lock_dir().join("resolve_handoff.fence")
}

pub fn handoff_fenced() -> bool {
    match std::fs::metadata(handoff_fence_path()) {
        Ok(_) => true,
        Err(error) => error.kind() != std::io::ErrorKind::NotFound,
    }
}

pub fn read_handoff_fence() -> Option<Value> {
    let path = handoff_fence_path();
    match std::fs::read(&path) {
        Ok(bytes) => Some(serde_json::from_slice(&bytes).unwrap_or_else(
            |_| serde_json::json!({"id": "unreadable", "error": "handoff fence is malformed"}),
        )),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(_) => Some(serde_json::json!({
            "id": "unreadable", "error": "handoff fence is unreadable"
        })),
    }
}

pub fn create_handoff_fence(job_id: &str) -> Result<Value, String> {
    if let Some(existing) = read_handoff_fence() {
        return Ok(existing);
    }
    let path = handoff_fence_path();
    std::fs::create_dir_all(lock_dir()).map_err(|error| error.to_string())?;
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos();
    let fence = serde_json::json!({
        "id": format!("{}-{nanos:x}", std::process::id()),
        "job_id": job_id,
        "created": crate::store::now(),
        "owner": "Ren Resolve broker"
    });
    let temporary = path.with_extension(format!("fence.{}.tmp", std::process::id()));
    std::fs::write(&temporary, format!("{}\n", fence)).map_err(|error| error.to_string())?;
    std::fs::rename(&temporary, &path).map_err(|error| error.to_string())?;
    Ok(fence)
}

pub fn clear_handoff_fence() -> Result<bool, String> {
    match std::fs::remove_file(handoff_fence_path()) {
        Ok(()) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error.to_string()),
    }
}

/// The daemon: the socket, the broker and the watchdog, minus the process.
pub struct Daemon {
    pub broker: Arc<Broker>,
    pub watchdog: Arc<Watchdog>,
    /// Set by the `shutdown` op; the serve loop notices and exits.
    pub shutdown: Arc<std::sync::atomic::AtomicBool>,
    shadow_watcher: std::sync::Mutex<Option<Child>>,
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
            shadow_watcher: std::sync::Mutex::new(None),
        }
    }

    fn start_shadow_watcher(&self) {
        *self.shadow_watcher.lock().unwrap() = start_shadow_watcher();
    }

    /// Stop the hosted watcher along with the broker process.
    pub fn stop(&self) {
        if let Some(mut child) = self.shadow_watcher.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }

    /// Dispatch one request on one connection. `grant` is the grant this
    /// connection holds, if any.
    pub fn dispatch(&self, request: &Request, grant: &mut Option<String>) -> String {
        match request {
            Request::Ping => {
                let (active, recorded) = lease_probe_in(&instance_lock_dir());
                let stale = if active.is_none() { recorded } else { None };
                protocol::wire(&PingReply {
                    ok: true,
                    pid: std::process::id() as i64,
                    serving: true,
                    responsive: true,
                    busy: active.is_some(),
                    lease: active,
                    stale_lease: stale,
                })
            }
            Request::Submit {
                kind,
                params,
                owner,
                qualification,
                idempotency_key,
            } => {
                match self.broker.submit(
                    kind,
                    params.clone(),
                    owner,
                    *qualification,
                    idempotency_key.as_deref(),
                ) {
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
                let interrupt_policy = job
                    .as_ref()
                    .and_then(|_| self.broker.store.interrupt_policy(id));
                protocol::wire(&JobReply {
                    job,
                    interrupt_policy,
                })
            }
            Request::Result { id, wait } => {
                let job = self.broker.result(id, *wait);
                protocol::wire(&JobReply {
                    job,
                    interrupt_policy: None,
                })
            }
            Request::Cancel { id } => match self.broker.request_cancel(id) {
                Ok(job) => protocol::wire(&JobReply {
                    job,
                    interrupt_policy: None,
                }),
                Err(error) => protocol::wire(&ErrorReply { error }),
            },
            Request::Activity => {
                let active: Vec<_> = self
                    .broker
                    .running()
                    .into_iter()
                    .filter_map(|running| self.broker.store.get(&running.id))
                    .collect();
                let count = active.len();
                let jobs: Vec<Value> = active
                    .into_iter()
                    .map(|job| {
                        let purpose = job
                            .params
                            .get("purpose")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string();
                        let interrupt_policy = self
                            .broker
                            .store
                            .interrupt_policy(&job.id)
                            .unwrap_or_default();
                        serde_json::json!({
                            "id": job.id, "kind": job.kind, "mode": job.mode,
                            "description": job.description, "progress": job.progress,
                            "owner": job.owner.chars().take(40).collect::<String>(),
                            "params": {"purpose": purpose.chars().take(80).collect::<String>()},
                            "state": job.state, "started": job.started,
                            "submitted": job.submitted,
                            "interrupt_policy": interrupt_policy
                        })
                    })
                    .take(4)
                    .collect();
                protocol::wire(&protocol::ActivityReply { count, jobs })
            }
            Request::Handoff => match self.broker.request_handoff() {
                Ok(reply) => protocol::wire(&reply),
                Err(error) => protocol::wire(&ErrorReply { error }),
            },
            Request::HandoffStatus => {
                let fence = read_handoff_fence();
                let active_jobs: Vec<_> = self
                    .broker
                    .running()
                    .into_iter()
                    .filter_map(|running| self.broker.store.get(&running.id))
                    .collect();
                let lease = active_lease();
                protocol::wire(&protocol::HandoffStatusReply {
                    fenced: fence.is_some(),
                    ready: fence.is_some() && active_jobs.is_empty() && lease.is_none(),
                    fence,
                    active_jobs,
                    lease,
                })
            }
            Request::ReturnToRen => {
                if !self.broker.running().is_empty() || active_lease().is_some() {
                    protocol::wire(&ErrorReply {
                        error: "Resolve is still in use; wait for the active job and lease to end before returning control to Ren".to_string(),
                    })
                } else {
                    match self.broker.return_to_ren() {
                        Ok(fence_cleared) => protocol::wire(&protocol::ReturnToRenReply {
                            returned: true,
                            fence_cleared,
                        }),
                        Err(error) => protocol::wire(&ErrorReply { error }),
                    }
                }
            }
            Request::List { limit } => {
                let jobs = self.broker.store.recent(*limit);
                let running = self.broker.running();
                protocol::wire(&ListReply { jobs, running })
            }
            Request::Acquire {
                params,
                owner,
                qualification,
                wait,
            } => self.acquire(params.clone(), owner, *qualification, *wait, grant),
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
                    .store(true, std::sync::atomic::Ordering::Release);
                protocol::wire(&OkReply { ok: true })
            }
        }
    }

    /// The `acquire` op: submit a lease job and wait for it to start.
    fn acquire(
        &self,
        params: Value,
        owner: &str,
        qualification: bool,
        wait: f64,
        grant: &mut Option<String>,
    ) -> String {
        let submission = match self
            .broker
            .submit("lease", params, owner, qualification, None)
        {
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
                self.broker
                    .release(&job_id, "done", "the client's wait ran out");
                return protocol::wire(&BusyReply {
                    error: "timed out waiting for Resolve".to_string(),
                    busy: true,
                    running: self.broker.running(),
                });
            }
        };
        if !started {
            self.broker
                .release(&job_id, "done", "the client's wait ran out");
            return protocol::wire(&BusyReply {
                error: "timed out waiting for Resolve".to_string(),
                busy: true,
                running: self.broker.running(),
            });
        }
        if job.state == "running" {
            *grant = Some(job_id);
            return protocol::wire(&GrantedReply {
                granted: grant.clone().unwrap(),
            });
        }
        if job.state == "rejected" {
            return protocol::wire(&RefusedReply {
                error: job.error,
                refused: true,
            });
        }
        self.broker
            .release(&job_id, "done", "the client's wait ran out");
        protocol::wire(&BusyReply {
            error: "timed out waiting for Resolve".to_string(),
            busy: true,
            running: self.broker.running(),
        })
    }
}

fn start_shadow_watcher() -> Option<Child> {
    if std::env::var("REN_SHADOW_WATCHER").unwrap_or_default() == "0" {
        return None;
    }
    let python = std::env::var("PIPELINE_PYTHON").unwrap_or_else(|_| "python3".to_string());
    match Command::new(&python)
        .args(["-m", "library.tools.resolved.shadow_watcher_host"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()
    {
        Ok(child) => Some(child),
        Err(error) => {
            eprintln!("ren-resolved: could not start shadow watcher ({python}): {error}");
            None
        }
    }
}

/// Read the diagnostic lease record without entering the Resolve worker.
fn active_lease() -> Option<protocol::LeaseStatus> {
    lease_probe_in(&instance_lock_dir()).0
}

/// The lease, answered by the instance FLOCK, with the record beside it.
///
/// The JSON is diagnostic, not a lock. A killed writer cannot remove it,
/// but the kernel releases its flock. Do not unlink the record: a new
/// writer may be publishing its own as this probe returns. A record with
/// no flock behind it is STALE - the instance is free, and the record
/// names a holder that is gone.
fn lease_probe_in(
    directory: &Path,
) -> (Option<protocol::LeaseStatus>, Option<protocol::LeaseStatus>) {
    let recorded = read_lease_record(directory);
    if flock_is_held(directory) {
        (recorded.clone(), recorded)
    } else {
        (None, recorded)
    }
}

/// The diagnostic record on disk, whoever wrote it.
fn read_lease_record(directory: &Path) -> Option<protocol::LeaseStatus> {
    let body = std::fs::read_to_string(directory.join(LEASE_NAME)).ok()?;
    serde_json::from_str(&body).ok()
}

/// Whether the instance flock is held by someone right now.
fn flock_is_held(directory: &Path) -> bool {
    use std::fs::OpenOptions;
    use std::os::fd::AsRawFd;

    match OpenOptions::new()
        .create(true)
        .truncate(false)
        .append(true)
        .open(directory.join("resolve_instance.lock"))
    {
        Ok(lock) => {
            // SAFETY: lock owns this valid descriptor for the entire call.
            unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_SH | libc::LOCK_NB) != 0 }
        }
        // An unprobeable lock must not make an existing holder disappear.
        Err(_) => true,
    }
}

/// Bind the broker's socket, refusing one a live broker holds.
///
/// The probe happens under a bind lock, so two brokers starting at once
/// cannot both pass it and one orphan the other - and a socket a live
/// broker holds is never taken from it: the daemon refuses, loudly,
/// instead of unlinking somebody else's socket and binding a new one
/// (measured 2026-10-20: a broker started from a worker worktree
/// unlinked the running broker's socket, so two brokers drove the one
/// Resolve and neither could be reached).
pub fn bind_socket(path: &Path) -> std::io::Result<UnixListener> {
    let directory = path.parent().unwrap_or_else(|| Path::new("."));
    std::fs::create_dir_all(directory)?;
    let bind_lock_path = directory.join(BIND_LOCK_NAME);
    let bind_lock = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(&bind_lock_path)?;
    unsafe {
        libc::flock(bind_lock.as_raw_fd(), libc::LOCK_EX);
    }
    let refusal = match probe(path) {
        Probe::Answer(_) => Some(format!(
            "ren-resolved: is already serving on {}",
            path.display()
        )),
        Probe::Silent => Some(format!(
            "ren-resolved: {} is held by a broker that did not answer - \
             refusing to take its socket",
            path.display()
        )),
        Probe::Absent => None,
    };
    if let Some(message) = refusal {
        unsafe {
            libc::flock(bind_lock.as_raw_fd(), libc::LOCK_UN);
        }
        return Err(std::io::Error::new(std::io::ErrorKind::AddrInUse, message));
    }
    if path.exists() {
        // A dead broker's socket: nobody answers it.
        std::fs::remove_file(path).ok();
    }
    let listener = UnixListener::bind(path)?;
    std::fs::set_permissions(path, std::os::unix::fs::PermissionsExt::from_mode(0o600))?;
    unsafe {
        libc::flock(bind_lock.as_raw_fd(), libc::LOCK_UN);
    }
    Ok(listener)
}

/// Serve on the socket until `shutdown` or a signal.
///
/// `accept` blocks until a client arrives. The shutdown handler connects once
/// after replying, waking the accept loop without imposing a polling delay on
/// ordinary requests.
pub fn serve(path: &Path, daemon: Arc<Daemon>) -> std::io::Result<()> {
    let listener = bind_socket(path)?;
    daemon.start_shadow_watcher();
    while let Ok((stream, _)) = listener.accept() {
        if daemon.shutdown.load(std::sync::atomic::Ordering::Acquire) {
            break;
        }
        let daemon = Arc::clone(&daemon);
        let wake_path = path.to_path_buf();
        std::thread::spawn(move || {
            handle_connection(stream, &daemon, &wake_path);
        });
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
    const KNOWN: [&str; 16] = [
        "ping",
        "submit",
        "status",
        "result",
        "cancel",
        "activity",
        "handoff",
        "handoff_status",
        "return_to_ren",
        "list",
        "acquire",
        "release",
        "health",
        "post_restart_check",
        "watchdog_status",
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

fn handle_connection(stream: UnixStream, daemon: &Daemon, wake_path: &Path) {
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
            // Landed in the broker log with when: the 2026-10-08 stop
            // was found with no known cause because neither starts nor
            // stops were logged.
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);
            eprintln!("{now} ren-resolved: shutdown requested - stopping");
            // Wake the blocking accept so the server can exit promptly.
            let _ = UnixStream::connect(wake_path);
            break;
        }
    }
    // A client that dies mid-section releases its grant by closing the
    // socket, so the queue never wedges on a dead holder.
    if let Some(job_id) = grant.take() {
        daemon
            .broker
            .release(&job_id, "done", "the client disconnected while holding");
    }
}

#[cfg(test)]
mod bind_tests {
    use super::*;

    /// The defect, measured 2026-10-20: a broker started from a worker
    /// worktree UNLINKED the running broker's socket and bound its own,
    /// so two brokers drove the one Resolve and the first could not be
    /// reached at all. A second broker refuses a socket that answers,
    /// and never takes it.
    #[test]
    fn a_second_broker_never_takes_a_serving_socket() {
        let directory =
            std::env::temp_dir().join(format!("ren-resolved-bind-test-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let path = directory.join(SOCKET_NAME);
        let listener = UnixListener::bind(&path).unwrap();
        // The serving broker answers a ping, like the real one does.
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut line = String::new();
            let _ = reader.read_line(&mut line);
            let _ = stream.write_all(b"{\"ok\":true,\"pid\":1}\n");
            let _ = stream.flush();
        });

        let error = bind_socket(&path).unwrap_err();
        assert!(
            error.to_string().contains("already serving"),
            "the second broker said: {error}"
        );
        assert!(
            path.exists(),
            "the serving broker's socket was taken from it"
        );
        server.join().unwrap();
        std::fs::remove_dir_all(directory).unwrap();
    }

    /// A socket nobody answers is a dead broker's: it is taken and
    /// rebound, rather than refused.
    #[test]
    fn a_dead_brokers_socket_is_taken() {
        let directory =
            std::env::temp_dir().join(format!("ren-resolved-dead-test-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let path = directory.join(SOCKET_NAME);
        std::fs::write(&path, b"").unwrap();
        assert!(bind_socket(&path).is_ok());
        std::fs::remove_dir_all(directory).unwrap();
    }
}

#[cfg(test)]
mod lease_tests {
    use super::*;
    use std::os::fd::AsRawFd;

    /// The defect, measured 2026-10-20: a dead holder's lease record
    /// outlived the lock, and `ren resolved status` read the record
    /// instead of the flock - so a FREE instance was called busy, with
    /// the dead holder's pid, for hours.
    #[test]
    fn a_record_with_no_flock_behind_it_is_stale_not_busy() {
        let directory =
            std::env::temp_dir().join(format!("ren-resolved-stale-test-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let _lock = std::fs::File::create(directory.join("resolve_instance.lock")).unwrap();
        let record = serde_json::json!({"owner": "pid-72326", "purpose": "place",
            "pid": 72326, "host": "test", "since": 1.0});
        std::fs::write(directory.join(LEASE_NAME), record.to_string()).unwrap();

        let (active, recorded) = lease_probe_in(&directory);
        assert!(active.is_none(), "a free instance was reported as held");
        assert_eq!(recorded.unwrap().pid, 72326, "the record was not read back");
        // The record is left alone: another writer may be publishing its own.
        assert!(directory.join(LEASE_NAME).exists());
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn abandoned_record_is_ignored_and_a_new_holder_is_visible() {
        let directory =
            std::env::temp_dir().join(format!("ren-resolved-lease-test-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let lock = std::fs::File::create(directory.join("resolve_instance.lock")).unwrap();
        let record = serde_json::json!({"owner": "build", "purpose": "place",
            "pid": 123, "host": "test", "since": 1.0});
        std::fs::write(directory.join(LEASE_NAME), record.to_string()).unwrap();
        assert!(lease_probe_in(&directory).0.is_none());
        // SAFETY: the file owns a valid descriptor throughout both calls.
        assert_eq!(unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX) }, 0);
        assert_eq!(lease_probe_in(&directory).0.unwrap().pid, 123);
        assert_eq!(unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_UN) }, 0);
        assert!(lease_probe_in(&directory).0.is_none());
        assert!(directory.join(LEASE_NAME).exists());
        std::fs::remove_dir_all(directory).unwrap();
    }
}
