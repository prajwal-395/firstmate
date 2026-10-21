//! The queue, the scheduler loop and the executor, minus the socket.
//!
//! A direct port of `library/tools/resolved/server.py`'s `Broker`: the same
//! submission gate (the worker runs `jobs.prepare`), the same coalescing
//! and idempotency, the same schedule and execute loops, the same grant
//! lifecycle. The one difference is where a job RUNS: the Python broker
//! executes on its own Resolve connection, and the Rust daemon hands each
//! executed job to a Python worker process over stdio.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Arc, Condvar, Mutex};

use crate::scheduler::{self, Pending};
use crate::store::{JobRecord, JobStore, Receipt, RunningJob, TERMINAL};
use crate::worker;

/// One job as the executor sees it: the record plus its schedulable shape.
#[derive(Clone, Debug)]
pub struct JobView {
    pub id: String,
    pub kind: String,
    pub params: Value,
    pub project: String,
    pub timeline: String,
    pub qualification: bool,
    pub mode: String,
    pub executed: bool,
}

/// A submission's outcome: the job's id and whether it coalesced.
pub struct Submission {
    pub id: String,
    pub coalesced: bool,
}

/// Why a submission was refused (`jobs.JobRefused`).
///
/// PROD-003: a refusal says what KIND of block it was and what the
/// caller can do about it, not only that it happened. The vocabulary is
/// the Python taxonomy's (`refusal_taxonomy.BLOCKERS` and
/// `NEXT_ACTIONS`), and the Python worker names the pair for every
/// refusal it raises - the daemon carries it to the client unchanged.
pub struct Refusal {
    pub message: String,
    /// The kind of block, as `refusal_taxonomy.BLOCKERS` words it.
    pub blocker: String,
    /// The move the caller can take, as `refusal_taxonomy.NEXT_ACTIONS`
    /// words it.
    pub next_action: String,
}

impl Refusal {
    /// A refusal with the Python `JobRefused` defaults: the broker has no
    /// job kind, or no route for this one.
    pub fn new(message: String) -> Self {
        Refusal {
            message,
            blocker: DEFAULT_BLOCKER.to_string(),
            next_action: DEFAULT_NEXT_ACTION.to_string(),
        }
    }

    /// A refusal that names its own kind of block and next action.
    pub fn with_action(message: String, blocker: &str, next_action: &str) -> Self {
        Refusal {
            message,
            blocker: blocker.to_string(),
            next_action: next_action.to_string(),
        }
    }
}

/// The blocker `jobs.JobRefused` defaults to: the broker has no job kind,
/// or no route for this one.
pub const DEFAULT_BLOCKER: &str = "unsupported_operation";

/// The next action that blocker demands
/// (`refusal_taxonomy.BLOCKER_NEXT_ACTION`).
pub const DEFAULT_NEXT_ACTION: &str = "use_a_supported_operation";

/// The broker's shared state, behind one lock and one condition variable -
/// the Rust shape of the Python daemon's `threading.Condition`.
struct State {
    queued: HashMap<String, Pending>,
    running: HashMap<String, Pending>,
    started: HashMap<String, bool>,
    coalesce: HashMap<String, String>,
    cursor: (String, String),
    executor_queue: Vec<String>,
    stopping: bool,
}

pub struct Broker {
    pub store: Arc<JobStore>,
    state: Arc<(Mutex<State>, Condvar)>,
    pub watchdog: Arc<Mutex<Option<crate::watchdog::WatchdogHandle>>>,
    prepare_worker: Arc<Mutex<worker::Worker>>,
    pub execution_worker: Arc<Mutex<worker::Worker>>,
    schedule_thread: Option<std::thread::JoinHandle<()>>,
    execute_thread: Option<std::thread::JoinHandle<()>>,
}

impl Broker {
    pub fn new(store: Arc<JobStore>) -> Self {
        let state = Arc::new((
            Mutex::new(State {
                queued: HashMap::new(),
                running: HashMap::new(),
                started: HashMap::new(),
                coalesce: HashMap::new(),
                cursor: (String::new(), String::new()),
                executor_queue: Vec::new(),
                stopping: false,
            }),
            Condvar::new(),
        ));
        Broker {
            store,
            state,
            watchdog: Arc::new(Mutex::new(None)),
            prepare_worker: Arc::new(Mutex::new(worker::Worker::new())),
            execution_worker: Arc::new(Mutex::new(worker::Worker::new())),
            schedule_thread: None,
            execute_thread: None,
        }
    }

    pub fn start(&mut self) {
        if let Err(error) = self.prepare_worker.lock().unwrap().warm_prepare() {
            eprintln!("ren-resolved: could not warm prepare worker: {error}");
        }
        let state = Arc::clone(&self.state);
        let store = Arc::clone(&self.store);
        self.schedule_thread = Some(std::thread::spawn(move || {
            schedule_loop(state, store);
        }));
        let state = Arc::clone(&self.state);
        let store = Arc::clone(&self.store);
        let execution_worker = Arc::clone(&self.execution_worker);
        self.execute_thread = Some(std::thread::spawn(move || {
            execute_loop(state, store, execution_worker);
        }));
    }

    pub fn set_watchdog(&self, handle: crate::watchdog::WatchdogHandle) {
        *self.watchdog.lock().unwrap() = Some(handle);
    }

    // ── submission ───────────────────────────────────────────────

    /// Validate and queue one submission. Refuses what `jobs.prepare`
    /// refuses - a non-conforming job is never queued.
    pub fn submit(
        &self,
        kind: &str,
        params: Value,
        owner: &str,
        qualification: bool,
        idempotency_key: Option<&str>,
    ) -> Result<Submission, Refusal> {
        if let Some(handle) = self.watchdog.lock().unwrap().as_ref() {
            if handle.is_degraded() {
                let status = handle.status();
                return Err(Refusal::new(format!(
                    "Resolve is unresponsive: {} (holder: {}, job: {}). \
                         Restart Resolve, then retry.",
                    status.degraded_reason,
                    if status.holder.is_empty() {
                        "none"
                    } else {
                        &status.holder
                    },
                    if status.job_id.is_empty() {
                        "none"
                    } else {
                        &status.job_id
                    }
                )));
            }
        }
        // The submission gate: `jobs.prepare`, run in the worker. It
        // refuses an unknown kind, a missing name, a qualification job
        // aimed anywhere but the qualification project.
        let shape = self.prepare(kind, &params, qualification)?;
        let now = crate::store::now();
        let job_id = uuid();
        let record = JobRecord {
            id: job_id.clone(),
            kind: kind.to_string(),
            priority: shape.priority.clone(),
            mode: shape.mode.clone(),
            executed: shape.executed,
            params,
            project: shape.project.clone(),
            timeline: shape.timeline.clone(),
            qualification,
            coalesce_key: shape.coalesce_key.clone(),
            interrupt_policy: shape.interrupt_policy.clone(),
            owner: owner.to_string(),
            description: shape.description.clone(),
            progress: shape.progress.clone(),
            state: "queued".to_string(),
            subscribers: 1,
            submitted: now,
        };
        let (lock, cond) = &*self.state;
        let mut state = lock.lock().unwrap();
        if let Some(key) = &shape.coalesce_key {
            if idempotency_key.is_none() {
                if let Some(joined) = state.coalesce.get(key).cloned() {
                    drop(state);
                    self.store.add_subscriber(&joined).ok();
                    return Ok(Submission {
                        id: joined,
                        coalesced: true,
                    });
                }
            }
        }
        if let Some(key) = idempotency_key {
            let digest = idempotency_digest(kind, &record.params, key);
            match self.store.insert_idempotent(&record, kind, key, &digest) {
                Ok(Some(previous)) => {
                    if previous.state != "done"
                        && previous.state != "failed"
                        && previous.state != "rejected"
                        && previous.state != "cancelled"
                    {
                        drop(state);
                        self.store.add_subscriber(&previous.id).ok();
                    }
                    return Ok(Submission {
                        id: previous.id,
                        coalesced: true,
                    });
                }
                Ok(None) => {}
                Err(exc) => {
                    return Err(Refusal::new(exc.to_string()));
                }
            }
        } else {
            self.store
                .insert(&record)
                .map_err(|exc| Refusal::new(exc.to_string()))?;
        }
        state.queued.insert(
            job_id.clone(),
            Pending {
                id: job_id.clone(),
                priority: shape.priority.clone(),
                mode: shape.mode.clone(),
                submitted: now,
                project: shape.project.clone(),
                timeline: shape.timeline.clone(),
                executed: shape.executed,
                qualification,
                locality: shape.locality.clone(),
            },
        );
        if let Some(key) = &shape.coalesce_key {
            state.coalesce.insert(key.clone(), job_id.clone());
        }
        if !shape.executed {
            state.started.insert(job_id.clone(), false);
        }
        cond.notify_all();
        Ok(Submission {
            id: job_id,
            coalesced: false,
        })
    }

    /// Run `jobs.prepare` in the worker: the schedulable shape of one
    /// submission, or its refusal.
    fn prepare(
        &self,
        kind: &str,
        params: &Value,
        qualification: bool,
    ) -> Result<PreparedShape, Refusal> {
        let request = worker::prepare_request(kind, params, qualification);
        let response = self
            .prepare_worker
            .lock()
            .unwrap()
            .run(&request)
            .map_err(Refusal::new)?;
        if !response.ok {
            // The Python worker names the kind of block and the next
            // action for every refusal it raises (`jobs.JobRefused`).
            // The daemon carries them to the client unchanged; a refusal
            // that names none keeps the `JobRefused` defaults.
            return Err(Refusal::with_action(
                response.error,
                response.blocker.as_deref().unwrap_or(DEFAULT_BLOCKER),
                response
                    .next_action
                    .as_deref()
                    .unwrap_or(DEFAULT_NEXT_ACTION),
            ));
        }
        let result = response.result.unwrap_or(Value::Null);
        Ok(PreparedShape {
            mode: result
                .get("mode")
                .and_then(|v| v.as_str())
                .unwrap_or("shared")
                .to_string(),
            priority: result
                .get("priority")
                .and_then(|v| v.as_str())
                .unwrap_or("read")
                .to_string(),
            executed: result
                .get("executed")
                .and_then(|v| v.as_bool())
                .unwrap_or(false),
            project: result
                .get("project")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            timeline: result
                .get("timeline")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            locality: result
                .get("locality")
                .and_then(|v| v.as_str())
                .unwrap_or("none")
                .to_string(),
            coalesce_key: result
                .get("coalesce_key")
                .and_then(|v| v.as_str())
                .map(String::from),
            interrupt_policy: result
                .get("interrupt_policy")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            description: result
                .get("description")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            progress: result
                .get("progress")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
        })
    }

    /// Wait until a grant's job has started (the caller may take Resolve).
    ///
    /// A grant that will never start - a qualification job the worker
    /// refused, or a daemon that stopped - answers False at once, so the
    /// caller's `acquire` reports the refusal instead of waiting out its
    /// whole timeout.
    pub fn wait_started(&self, job_id: &str, timeout: f64) -> bool {
        let (lock, cond) = &*self.state;
        let deadline = crate::store::now() + timeout.max(0.0);
        let mut state = lock.lock().unwrap();
        loop {
            if *state.started.get(job_id).unwrap_or(&false) {
                return true;
            }
            if let Some(job) = self.store.get(job_id) {
                if TERMINAL.contains(&job.state.as_str()) {
                    return false;
                }
            }
            let remaining = deadline - crate::store::now();
            if remaining <= 0.0 {
                return false;
            }
            let (next, _) = cond
                .wait_timeout(
                    state,
                    std::time::Duration::from_secs_f64(remaining.min(1.0)),
                )
                .unwrap();
            state = next;
            if state.stopping {
                return false;
            }
        }
    }

    /// A job's receipt, waiting until it is terminal or the deadline.
    pub fn result(&self, job_id: &str, wait: f64) -> Option<Receipt> {
        let (lock, cond) = &*self.state;
        let deadline = crate::store::now() + wait.max(0.0);
        let mut state = lock.lock().unwrap();
        loop {
            let job = self.store.get(job_id);
            if job.is_none()
                || job
                    .as_ref()
                    .map(|j| TERMINAL.contains(&j.state.as_str()))
                    .unwrap_or(false)
            {
                return job;
            }
            let remaining = deadline - crate::store::now();
            if remaining <= 0.0 {
                return job;
            }
            let (next, _) = cond
                .wait_timeout(
                    state,
                    std::time::Duration::from_secs_f64(remaining.min(1.0)),
                )
                .unwrap();
            state = next;
        }
    }

    /// Cancel a queued job.
    pub fn cancel(&self, job_id: &str, reason: &str) -> Option<Receipt> {
        let (lock, cond) = &*self.state;
        let mut state = lock.lock().unwrap();
        if state.queued.remove(job_id).is_some() {
            self.forget(&mut state, job_id);
            state.started.remove(job_id);
            self.store
                .update(
                    job_id,
                    &[
                        ("state", json!("cancelled")),
                        ("finished", json!(crate::store::now())),
                        ("error", json!(reason)),
                    ],
                )
                .ok();
            cond.notify_all();
        }
        self.store.get(job_id)
    }

    /// Stop queued work, or request Stop at the runner's declared safe
    /// checkpoint. The policy is persisted with the job at submission.
    pub fn request_cancel(&self, job_id: &str) -> Result<Option<Receipt>, String> {
        let (lock, _) = &*self.state;
        let state = lock.lock().unwrap();
        if state.queued.contains_key(job_id) {
            drop(state);
            return Ok(self.cancel(job_id, "cancelled"));
        }
        if !state.running.contains_key(job_id) {
            return Ok(self.store.get(job_id));
        }
        let job = self.store.get(job_id);
        let Some(job) = job else { return Ok(None) };
        if self
            .store
            .interrupt_policy(job_id)
            .unwrap_or_default()
            .is_empty()
        {
            return Err(format!(
                "{} has no safe running cancellation path; Resolve remains shielded until it finishes",
                job.kind));
        }
        let marker = crate::server::broker_dir()
            .join("ren-resolved-cancel")
            .join(job_id);
        std::fs::create_dir_all(marker.parent().unwrap())
            .map_err(|exc| format!("could not prepare safe Stop request: {exc}"))?;
        std::fs::write(marker, b"requested\n")
            .map_err(|exc| format!("could not send safe Stop request: {exc}"))?;
        Ok(Some(job))
    }

    /// Fence the scheduler before requesting safe stops from active jobs.
    pub fn request_handoff(&self) -> Result<crate::protocol::HandoffReply, String> {
        let (lock, _) = &*self.state;
        let state = lock.lock().unwrap();
        let job_ids: Vec<String> = state.running.keys().cloned().collect();
        let fence =
            crate::server::create_handoff_fence(job_ids.first().map(String::as_str).unwrap_or(""))?;
        drop(state);

        let mut request_accepted = Vec::new();
        let mut waiting_for_completion = Vec::new();
        for job_id in &job_ids {
            if self
                .store
                .interrupt_policy(job_id)
                .unwrap_or_default()
                .is_empty()
            {
                waiting_for_completion.push(job_id.clone());
            } else {
                self.request_cancel(job_id)?;
                request_accepted.push(job_id.clone());
            }
        }
        Ok(crate::protocol::HandoffReply {
            fence,
            request_accepted,
            waiting_for_completion,
            job_ids,
        })
    }

    /// Clear the durable fence only after the caller proves Resolve is idle.
    pub fn return_to_ren(&self) -> Result<bool, String> {
        let (lock, cond) = &*self.state;
        let state = lock.lock().unwrap();
        if !state.running.is_empty() {
            return Err("Resolve still has active broker jobs".to_string());
        }
        let cleared = crate::server::clear_handoff_fence()?;
        cond.notify_all();
        Ok(cleared)
    }

    /// End a grant (or cancel it, if it was never started).
    pub fn release(&self, job_id: &str, state: &str, error: &str) {
        let (lock, cond) = &*self.state;
        let mut locked = lock.lock().unwrap();
        if locked.running.contains_key(job_id) {
            self.finish(&mut locked, job_id, state, None, error);
            cond.notify_all();
            return;
        }
        drop(locked);
        self.cancel(
            job_id,
            if error.is_empty() {
                "the client left before its turn"
            } else {
                error
            },
        );
    }

    /// The running jobs, as the `list` and `acquire` replies report them.
    pub fn running(&self) -> Vec<RunningJob> {
        let (lock, _) = &*self.state;
        let state = lock.lock().unwrap();
        state
            .running
            .values()
            .map(|p| RunningJob {
                id: p.id.clone(),
                priority: p.priority.clone(),
                mode: p.mode.clone(),
                submitted: p.submitted,
                project: p.project.clone(),
                timeline: p.timeline.clone(),
                executed: p.executed,
                qualification: p.qualification,
                locality: p.locality.clone(),
            })
            .collect()
    }

    pub fn stop(&self) {
        let (lock, cond) = &*self.state;
        let mut state = lock.lock().unwrap();
        state.stopping = true;
        cond.notify_all();
        drop(state);
        self.stop_workers();
    }

    /// End the workers: the broker's stop must not leave one behind.
    ///
    /// The worker holds the instance lease while it works, so a worker
    /// that outlives the broker holds the one Resolve against every
    /// later writer - which then times out waiting for an instance
    /// nobody is using (measured 2026-10-20: an orphan worker held the
    /// instance flock for 3h10m after `ren resolved stop`).
    ///
    /// The worker mutex is taken WITHOUT blocking: a worker inside a job
    /// holds it for the length of that job, and the broker's stop must
    /// not wait for a job to finish. A worker that is inside a job when
    /// the broker goes ends itself instead - it watches the broker's pid
    /// and gives the instance back when the broker is gone.
    pub fn stop_workers(&self) {
        if let Ok(mut worker) = self.prepare_worker.try_lock() {
            worker.stop();
        }
        if let Ok(mut worker) = self.execution_worker.try_lock() {
            worker.stop();
        }
    }

    // ── internals ────────────────────────────────────────────────

    fn forget(&self, state: &mut State, job_id: &str) {
        if let Some(job) = self.store.get(job_id) {
            if let Some(key) = &job.coalesce_key {
                if state
                    .coalesce
                    .get(key)
                    .map(|id| id == job_id)
                    .unwrap_or(false)
                {
                    state.coalesce.remove(key);
                }
            }
        }
    }

    /// Move a running job to a terminal state. The caller holds the lock.
    fn finish(
        &self,
        state: &mut State,
        job_id: &str,
        job_state: &str,
        result: Option<Value>,
        error: &str,
    ) {
        state.running.remove(job_id);
        self.forget(state, job_id);
        // A grant refused before it started wakes its waiter, which then
        // reads the terminal state and reports the refusal.
        state.started.remove(job_id);
        let mut fields: Vec<(&str, Value)> = vec![
            ("state", Value::String(job_state.to_string())),
            ("finished", json!(crate::store::now())),
        ];
        if let Some(result) = result {
            fields.push(("result", Value::String(canonical(&result))));
        }
        if !error.is_empty() {
            fields.push(("error", Value::String(error.to_string())));
        }
        self.store.update(job_id, &fields).ok();
    }
}

/// The schedulable shape `jobs.prepare` returns.
struct PreparedShape {
    mode: String,
    priority: String,
    executed: bool,
    project: String,
    timeline: String,
    locality: String,
    coalesce_key: Option<String>,
    interrupt_policy: String,
    description: String,
    progress: String,
}

fn schedule_loop(state: Arc<(Mutex<State>, Condvar)>, store: Arc<JobStore>) {
    let (lock, cond) = &*state;
    loop {
        let mut state = lock.lock().unwrap();
        if state.stopping {
            return;
        }
        if crate::server::handoff_fenced() {
            let (state, _) = cond
                .wait_timeout(state, std::time::Duration::from_millis(500))
                .unwrap();
            drop(state);
            continue;
        }
        reap_dead_owners(&mut state, &store);
        let now = crate::store::now();
        let running: Vec<Pending> = state.running.values().cloned().collect();
        let queued: Vec<Pending> = state.queued.values().cloned().collect();
        let next = scheduler::next_job(&queued, &running, (&state.cursor.0, &state.cursor.1), now);
        let next = match next {
            Some(next) => next,
            None => {
                let (next, _) = cond
                    .wait_timeout(state, std::time::Duration::from_secs(1))
                    .unwrap();
                state = next;
                continue;
            }
        };
        state.queued.remove(&next.id);
        state.running.insert(next.id.clone(), next.clone());
        if !next.project.is_empty() {
            state.cursor = (next.project.clone(), next.timeline.clone());
        }
        store
            .update(
                &next.id,
                &[
                    ("state", json!("running")),
                    ("started", json!(crate::store::now())),
                ],
            )
            .ok();
        if next.executed || next.qualification {
            state.executor_queue.push(next.id.clone());
        } else {
            state.started.insert(next.id.clone(), true);
        }
        cond.notify_all();
    }
}

fn execute_loop(
    state: Arc<(Mutex<State>, Condvar)>,
    store: Arc<JobStore>,
    execution_worker: Arc<Mutex<worker::Worker>>,
) {
    let (lock, cond) = &*state;
    loop {
        let job_id = {
            let mut state = lock.lock().unwrap();
            loop {
                if state.stopping {
                    return;
                }
                if let Some(id) = state.executor_queue.first().cloned() {
                    state.executor_queue.remove(0);
                    break id;
                }
                let (next, _) = cond
                    .wait_timeout(state, std::time::Duration::from_secs(1))
                    .unwrap();
                state = next;
            }
        };
        let job = store.get(&job_id);
        let job = match job {
            Some(job) => job,
            None => continue,
        };
        let view = JobView {
            id: job.id.clone(),
            kind: job.kind.clone(),
            params: job.params.clone(),
            project: job.project.clone(),
            timeline: job.timeline.clone(),
            qualification: job.qualification,
            mode: job.mode.clone(),
            executed: job.executed,
        };
        let (job_state, result, error) = run_job(&view, &execution_worker);
        let (lock, cond) = &*state;
        let mut state = lock.lock().unwrap();
        if job_state == "granted" {
            // Checked; the caller takes Resolve now that the broker has
            // let go of it.
            state.started.insert(job_id.clone(), true);
            cond.notify_all();
            continue;
        }
        finish_job(&store, &mut state, &job_id, &job_state, result, &error);
        cond.notify_all();
    }
}

/// Move a running job to a terminal state. The caller holds the lock.
fn finish_job(
    store: &JobStore,
    state: &mut State,
    job_id: &str,
    job_state: &str,
    result: Option<Value>,
    error: &str,
) {
    state.running.remove(job_id);
    if let Some(job) = store.get(job_id) {
        if let Some(key) = &job.coalesce_key {
            if state
                .coalesce
                .get(key)
                .map(|id| id == job_id)
                .unwrap_or(false)
            {
                state.coalesce.remove(key);
            }
        }
    }
    state.started.remove(job_id);
    let mut fields: Vec<(&str, Value)> = vec![
        ("state", Value::String(job_state.to_string())),
        ("finished", json!(crate::store::now())),
    ];
    if let Some(result) = result {
        fields.push(("result", Value::String(canonical(&result))));
    }
    if !error.is_empty() {
        fields.push(("error", Value::String(error.to_string())));
    }
    store.update(job_id, &fields).ok();
}

/// Run one executed job in the execution worker; return (state, result, error).
///
/// The worker holds the instance lease for the whole job, connection
/// included - the daemon is a Rust process and the flock lives in Python.
fn run_job(
    view: &JobView,
    worker: &std::sync::Mutex<worker::Worker>,
) -> (String, Option<Value>, String) {
    let request = worker::job_request(view);
    let response = match worker.lock().unwrap().run(&request) {
        Ok(response) => response,
        Err(exc) => return ("failed".to_string(), None, exc),
    };
    let (state, result, error) = response.into_job_outcome();
    if state == "done" && !view.executed {
        return ("granted".to_string(), result, error);
    }
    (state, result, error)
}

/// Cancel a queued grant whose owner is gone.
///
/// A GRANT is a client's own turn: the client asked for it on the
/// connection that holds it. A client that dies while waiting leaves its
/// grant queued, and the daemon's own connection thread is still blocked
/// waiting for that turn - so the grant sits in the queue for the whole
/// lease timeout (900 s by default) with nobody waiting for it, holding
/// a place every other lane waits behind (measured 2026-10-21: eight such
/// grants sat in the queue of a busy machine, two of them past 600 s).
///
/// The owner is the process that asked, and the pid is in the owner's
/// name, so the daemon can CHECK whether it is gone rather than guess.
///
/// A JOB THAT HAS NOT STARTED and whose owner is dead is reaped
/// PROMPTLY - on owner death, not when it reaches the front of the
/// queue. Nobody is left to read its receipt, verify it or promote it,
/// and Ren never lands an unverified write it cannot account for
/// (captain's ruling, 2026-10-21; measured the same day: five
/// dead-owner jobs held places 1, 2, 3, 8 and 11 of a busy queue for
/// 365-2032 s).
///
/// A job that is ALREADY RUNNING finishes, and its receipt is recorded
/// for whoever comes back: it may already have done its work. A LIVE
/// owner is never reaped on idle time.
fn reap_dead_owners(state: &mut State, store: &JobStore) {
    let dead: Vec<String> = state
        .queued
        .keys()
        .filter(|id| match store.get(id) {
            Some(receipt) => owner_is_gone(&receipt.owner),
            None => false,
        })
        .cloned()
        .collect();
    for id in dead {
        state.queued.remove(&id);
        state.started.remove(&id);
        if let Some(job) = store.get(&id) {
            if let Some(key) = &job.coalesce_key {
                if state.coalesce.get(key).map(|held| *held == id).unwrap_or(false) {
                    state.coalesce.remove(key);
                }
            }
        }
        store
            .update(
                &id,
                &[
                    ("state", json!("cancelled")),
                    ("finished", json!(crate::store::now())),
                    ("error", json!("reaped:owner-gone")),
                ],
            )
            .ok();
    }
}

/// Whether the owner names a process that is gone.
///
/// A name that is not a pid at all (a lane's task name on its own)
/// yields None, and the job is left alone: absence is not death.
fn owner_is_gone(owner: &str) -> bool {
    match owner_pid(owner) {
        Some(pid) => !process_alive(pid),
        None => false,
    }
}

/// The pid the owner names, or None where it names none.
///
/// A process is `pid-1234`; a lane that names itself carries the pid
/// beside its name (`lane-3 (pid-1234)`).
fn owner_pid(owner: &str) -> Option<i32> {
    let chars: Vec<char> = owner.chars().collect();
    let mut index = 0;
    while index + 3 <= chars.len() {
        if chars[index] == 'p' && chars[index + 1] == 'i' && chars[index + 2] == 'd' {
            let mut cursor = index + 3;
            if cursor < chars.len()
                && (chars[cursor] == '-' || chars[cursor] == '=' || chars[cursor] == ' ')
            {
                cursor += 1;
            }
            let start = cursor;
            while cursor < chars.len() && chars[cursor].is_ascii_digit() {
                cursor += 1;
            }
            if cursor > start {
                let digits: String = chars[start..cursor].iter().collect();
                if let Ok(pid) = digits.parse::<i32>() {
                    if pid > 0 {
                        return Some(pid);
                    }
                }
            }
            index = cursor;
        } else {
            index += 1;
        }
    }
    None
}

/// Whether the process is still there.
///
/// A pid this daemon cannot signal belongs to somebody else and is
/// alive; a pid nobody answers for is gone.
fn process_alive(pid: i32) -> bool {
    if pid <= 0 {
        return false;
    }
    // SAFETY: kill(pid, 0) only checks for the process's existence.
    if unsafe { libc::kill(pid, 0) } == 0 {
        return true;
    }
    std::io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
}

fn uuid() -> String {
    let mut bytes = [0u8; 8];
    if let Ok(mut file) = std::fs::File::open("/dev/urandom") {
        use std::io::Read;
        let _ = file.read_exact(&mut bytes);
    }
    let mut value = u64::from_le_bytes(bytes);
    value ^= crate::store::now().to_bits();
    format!("{:016x}", value)
}

fn canonical(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "null".to_string())
}

/// The idempotency digest: `jobs.canonical_digest` over the kind and
/// params, or over the patch for `timeline.apply_patch`.
fn idempotency_digest(kind: &str, params: &Value, key: &str) -> String {
    let _ = key;
    if kind == "timeline.apply_patch" {
        if let Some(patch) = params.get("patch") {
            return canonical_digest(patch);
        }
    }
    canonical_digest(params)
}

fn canonical_digest(value: &Value) -> String {
    let body = serde_json::to_string(value).unwrap_or_default();
    let parsed: Value = serde_json::from_str(&body).unwrap_or(Value::Null);
    let sorted = sort_value(&parsed);
    let text = serde_json::to_string(&sorted).unwrap_or_default();
    sha256(text.as_bytes())
}

fn sort_value(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut sorted: Vec<(&String, &Value)> = map.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(b.0));
            let mut out = serde_json::Map::new();
            for (k, v) in sorted {
                out.insert(k.clone(), sort_value(v));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(sort_value).collect()),
        other => other.clone(),
    }
}

fn sha256(bytes: &[u8]) -> String {
    // A small, dependency-free SHA-256 for the idempotency digest.
    let mut state: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut message = bytes.to_vec();
    let bit_len = (bytes.len() as u64) * 8;
    message.push(0x80);
    while message.len() % 64 != 56 {
        message.push(0);
    }
    message.extend_from_slice(&bit_len.to_be_bytes());
    for chunk in message.chunks(64) {
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                chunk[i * 4],
                chunk[i * 4 + 1],
                chunk[i * 4 + 2],
                chunk[i * 4 + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let mut a = state[0];
        let mut b = state[1];
        let mut c = state[2];
        let mut d = state[3];
        let mut e = state[4];
        let mut f = state[5];
        let mut g = state[6];
        let mut h = state[7];
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let temp1 = h
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = s0.wrapping_add(maj);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }
        state[0] = state[0].wrapping_add(a);
        state[1] = state[1].wrapping_add(b);
        state[2] = state[2].wrapping_add(c);
        state[3] = state[3].wrapping_add(d);
        state[4] = state[4].wrapping_add(e);
        state[5] = state[5].wrapping_add(f);
        state[6] = state[6].wrapping_add(g);
        state[7] = state[7].wrapping_add(h);
    }
    let mut out = String::new();
    for word in state {
        out.push_str(&format!("{:08x}", word));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// A pid that cannot exist: the default pid ceiling on the machines
    /// this runs on is well below it, so `kill` answers "no such
    /// process" every time.
    const DEAD_PID: i32 = 4_194_303;

    fn scratch_dir(tag: &str) -> std::path::PathBuf {
        let scratch = std::env::temp_dir().join(format!(
            "ren-resolved-{tag}-{}-{}",
            std::process::id(),
            crate::store::now().to_bits()
        ));
        std::fs::create_dir_all(&scratch).unwrap();
        scratch
    }

    fn queued_grant(store: &JobStore, id: &str, owner: &str) {
        store
            .insert(&JobRecord {
                id: id.to_string(),
                kind: "lease".to_string(),
                priority: "mutation".to_string(),
                mode: "exclusive".to_string(),
                executed: false,
                params: json!({}),
                project: String::new(),
                timeline: String::new(),
                qualification: false,
                coalesce_key: None,
                interrupt_policy: String::new(),
                owner: owner.to_string(),
                description: "abandoned".to_string(),
                progress: String::new(),
                state: "queued".to_string(),
                subscribers: 1,
                submitted: crate::store::now(),
            })
            .unwrap();
    }

    /// The defect, measured 2026-10-21: a client that dies while
    /// waiting for its turn leaves its grant queued, and the daemon's
    /// own connection thread stays blocked on that turn for the whole
    /// lease timeout (900 s). The grant holds a place every other lane
    /// waits behind, and nobody is waiting for it.
    #[test]
    fn a_queued_grant_whose_owner_is_gone_is_reaped() {
        let scratch = scratch_dir("reap");
        let db = scratch.join("jobs.sqlite3");
        let store = Arc::new(JobStore::open(&db).unwrap());
        queued_grant(&store, "abandoned", &format!("pid-{DEAD_PID}"));
        let broker = Broker::new(Arc::clone(&store));
        {
            let (lock, _) = &*broker.state;
            let mut state = lock.lock().unwrap();
            state.queued.insert(
                "abandoned".to_string(),
                Pending {
                    id: "abandoned".to_string(),
                    priority: "mutation".to_string(),
                    mode: "exclusive".to_string(),
                    submitted: crate::store::now(),
                    project: String::new(),
                    timeline: String::new(),
                    executed: false,
                    qualification: false,
                    locality: "none".to_string(),
                },
            );
            reap_dead_owners(&mut state, &store);
            assert!(
                !state.queued.contains_key("abandoned"),
                "the dead owner's grant stayed in the queue"
            );
        }
        let receipt = store.get("abandoned").unwrap();
        assert_eq!(receipt.state, "cancelled");
        assert!(
            receipt.error.contains("owner"),
            "the reason did not name the owner: {}",
            receipt.error
        );
        drop(broker);
        drop(store);
        std::fs::remove_dir_all(scratch).ok();
    }

    /// A live lane's grant is nobody's to cancel.
    #[test]
    fn a_queued_grant_whose_owner_is_alive_is_kept() {
        let scratch = scratch_dir("reap-live");
        let db = scratch.join("jobs.sqlite3");
        let store = Arc::new(JobStore::open(&db).unwrap());
        let owner = format!("pid-{}", std::process::id());
        queued_grant(&store, "live", &owner);
        let broker = Broker::new(Arc::clone(&store));
        {
            let (lock, _) = &*broker.state;
            let mut state = lock.lock().unwrap();
            state.queued.insert(
                "live".to_string(),
                Pending {
                    id: "live".to_string(),
                    priority: "mutation".to_string(),
                    mode: "exclusive".to_string(),
                    submitted: crate::store::now(),
                    project: String::new(),
                    timeline: String::new(),
                    executed: false,
                    qualification: false,
                    locality: "none".to_string(),
                },
            );
            reap_dead_owners(&mut state, &store);
            assert!(state.queued.contains_key("live"));
        }
        assert_eq!(store.get("live").unwrap().state, "queued");
        drop(broker);
        drop(store);
        std::fs::remove_dir_all(scratch).ok();
    }

    /// A job that has NOT STARTED and whose owner is dead is reaped
    /// promptly, whatever its kind: nobody is left to read its receipt,
    /// verify it or promote it, and Ren never lands an unverified write
    /// it cannot account for (captain's ruling, 2026-10-21). Measured the
    /// same day: five dead-owner jobs held places 1, 2, 3, 8 and 11 of
    /// a busy queue for 365-2032 s.
    #[test]
    fn a_queued_job_that_has_not_started_is_reaped_when_its_owner_is_gone() {
        let scratch = scratch_dir("reap-executed");
        let db = scratch.join("jobs.sqlite3");
        let store = Arc::new(JobStore::open(&db).unwrap());
        store
            .insert(&JobRecord {
                id: "executed".to_string(),
                kind: "resolve_axi".to_string(),
                priority: "mutation".to_string(),
                mode: "exclusive".to_string(),
                executed: true,
                params: json!({}),
                project: String::new(),
                timeline: String::new(),
                qualification: false,
                coalesce_key: None,
                interrupt_policy: String::new(),
                owner: format!("pid-{DEAD_PID}"),
                description: "submitted".to_string(),
                progress: String::new(),
                state: "queued".to_string(),
                subscribers: 1,
                submitted: crate::store::now(),
            })
            .unwrap();
        let broker = Broker::new(Arc::clone(&store));
        {
            let (lock, _) = &*broker.state;
            let mut state = lock.lock().unwrap();
            state.queued.insert(
                "executed".to_string(),
                Pending {
                    id: "executed".to_string(),
                    priority: "mutation".to_string(),
                    mode: "exclusive".to_string(),
                    submitted: crate::store::now(),
                    project: String::new(),
                    timeline: String::new(),
                    executed: true,
                    qualification: false,
                    locality: "none".to_string(),
                },
            );
            reap_dead_owners(&mut state, &store);
            assert!(
                !state.queued.contains_key("executed"),
                "the dead owner's job stayed in the queue"
            );
        }
        let receipt = store.get("executed").unwrap();
        assert_eq!(receipt.state, "cancelled");
        assert_eq!(receipt.error, "reaped:owner-gone");
        drop(broker);
        drop(store);
        std::fs::remove_dir_all(scratch).ok();
    }

    /// A job that is ALREADY RUNNING finishes: it may have done its
    /// work, and its receipt is recorded for whoever comes back.
    #[test]
    fn a_running_job_with_a_dead_owner_is_left_to_finish() {
        let scratch = scratch_dir("reap-running");
        let db = scratch.join("jobs.sqlite3");
        let store = Arc::new(JobStore::open(&db).unwrap());
        store
            .insert(&JobRecord {
                id: "running".to_string(),
                kind: "resolve_axi".to_string(),
                priority: "mutation".to_string(),
                mode: "exclusive".to_string(),
                executed: true,
                params: json!({}),
                project: String::new(),
                timeline: String::new(),
                qualification: false,
                coalesce_key: None,
                interrupt_policy: String::new(),
                owner: format!("pid-{DEAD_PID}"),
                description: "submitted".to_string(),
                progress: String::new(),
                state: "running".to_string(),
                subscribers: 1,
                submitted: crate::store::now(),
            })
            .unwrap();
        let broker = Broker::new(Arc::clone(&store));
        {
            let (lock, _) = &*broker.state;
            let mut state = lock.lock().unwrap();
            state.running.insert(
                "running".to_string(),
                Pending {
                    id: "running".to_string(),
                    priority: "mutation".to_string(),
                    mode: "exclusive".to_string(),
                    submitted: crate::store::now(),
                    project: String::new(),
                    timeline: String::new(),
                    executed: true,
                    qualification: false,
                    locality: "none".to_string(),
                },
            );
            reap_dead_owners(&mut state, &store);
            assert!(
                state.running.contains_key("running"),
                "a running job was reaped: it may have done its work"
            );
        }
        assert_eq!(store.get("running").unwrap().state, "running");
        drop(broker);
        drop(store);
        std::fs::remove_dir_all(scratch).ok();
    }

    /// The pid is the only part of an owner a broker can CHECK.
    #[test]
    fn the_owner_s_pid_is_read_out_of_its_name() {
        assert_eq!(owner_pid("pid-1234"), Some(1234));
        assert_eq!(owner_pid("lane-3 (pid-1234)"), Some(1234));
        assert_eq!(owner_pid("axi001"), None);
        assert_eq!(owner_pid(""), None);
        assert!(owner_is_gone(&format!("pid-{DEAD_PID}")));
        assert!(!owner_is_gone(&format!("pid-{}", std::process::id())));
        assert!(!owner_is_gone("axi001"));
    }

    #[test]
    fn running_stop_uses_the_persisted_runner_policy() {
        let scratch = std::env::temp_dir().join(format!(
            "ren-resolved-cancel-test-{}-{}",
            std::process::id(),
            crate::store::now().to_bits()
        ));
        std::fs::create_dir_all(&scratch).unwrap();
        std::env::set_var("PIPELINE_RESOLVE_LOCK_DIR", &scratch);
        // The broker's own directory: where its cancellation markers
        // live, beside the socket it serves on.
        std::env::set_var(crate::server::BROKER_DIR_TOLD_ENV, &scratch);
        let db = scratch.join("jobs.sqlite3");
        let store = Arc::new(JobStore::open(&db).unwrap());
        let broker = Broker::new(Arc::clone(&store));

        for (id, kind, policy) in [
            (
                "probe",
                "resolve.activity_probe",
                "checkpoint:borrowed-context-restored",
            ),
            (
                "edit",
                "timeline.apply_patch",
                "commit:verified-editpatch-generation",
            ),
            ("unsafe", "resolve.ui_transaction", ""),
        ] {
            store
                .insert(&JobRecord {
                    id: id.to_string(),
                    kind: kind.to_string(),
                    priority: "interactive".to_string(),
                    mode: "exclusive".to_string(),
                    executed: true,
                    params: json!({}),
                    project: "Ren Qualification".to_string(),
                    timeline: String::new(),
                    qualification: true,
                    coalesce_key: None,
                    owner: "test".to_string(),
                    interrupt_policy: policy.to_string(),
                    description: "test job".to_string(),
                    progress: String::new(),
                    state: "running".to_string(),
                    subscribers: 1,
                    submitted: crate::store::now(),
                })
                .unwrap();
            broker.state.0.lock().unwrap().running.insert(
                id.to_string(),
                Pending {
                    id: id.to_string(),
                    priority: "interactive".to_string(),
                    mode: "exclusive".to_string(),
                    submitted: crate::store::now(),
                    project: "Ren Qualification".to_string(),
                    timeline: String::new(),
                    executed: true,
                    qualification: true,
                    locality: "project".to_string(),
                },
            );
        }

        assert_eq!(
            broker.request_cancel("probe").unwrap().unwrap().state,
            "running"
        );
        assert!(scratch.join("ren-resolved-cancel/probe").is_file());
        assert_eq!(
            broker.request_cancel("edit").unwrap().unwrap().state,
            "running"
        );
        assert!(scratch.join("ren-resolved-cancel/edit").is_file());
        assert!(broker
            .request_cancel("unsafe")
            .unwrap_err()
            .contains("no safe running cancellation"));
        assert!(!scratch.join("ren-resolved-cancel/unsafe").exists());

        drop(broker);
        drop(store);
        std::env::remove_var("PIPELINE_RESOLVE_LOCK_DIR");
        std::env::remove_var(crate::server::BROKER_DIR_TOLD_ENV);
        std::fs::remove_dir_all(scratch).unwrap();
    }
}
