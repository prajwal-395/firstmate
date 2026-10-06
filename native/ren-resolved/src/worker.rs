//! The Python Resolve worker: a persistent child process per role.
//!
//! The daemon is a Rust process; the Resolve scripting API and the
//! submission gate (`jobs.prepare`) are Python. So the daemon keeps TWO
//! persistent worker processes, both speaking the same stdio protocol:
//!
//!   * the PREPARE worker answers `prepare` requests - the submission gate.
//!     It is fast (no Resolve connection, no lease), so a submission never
//!     waits behind a job that is running.
//!   * the EXECUTION worker answers `job`, `probe`, `health` and
//!     `post_restart_check` requests. It takes the instance lease per job
//!     and keeps its Resolve connection across requests, as the Python
//!     broker does.
//!
//! A worker that dies is restarted for the next request; the request that
//! killed it fails, and the daemon says so.

use serde_json::Value;
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};

/// The worker's answer to one request.
#[derive(Clone, Debug)]
pub struct WorkerResponse {
    pub ok: bool,
    pub result: Option<Value>,
    pub error: String,
    pub refused: bool,
}

impl WorkerResponse {
    /// The observation as a job outcome: `ok` carries the result, a refusal
    /// carries the refusal's fields, a failure carries only the error.
    pub fn into_job_outcome(self) -> (String, Option<Value>, String) {
        if self.ok {
            return ("done".to_string(), self.result, String::new());
        }
        if self.refused {
            return ("rejected".to_string(), self.result, self.error);
        }
        ("failed".to_string(), None, self.error)
    }
}

/// Launch the Python interpreter for a worker.
///
/// `PIPELINE_PYTHON` is the ladder's answer (set by `bin/vep` and the
/// plugin); `python3` on PATH is the fallback. The worker imports
/// `library.tools.resolved.worker` from the engine root, which the daemon's
/// own environment already carries on `PYTHONPATH` (the client's
/// `_start_detached` set it before starting the daemon).
fn python_interpreter() -> String {
    if let Some(value) = std::env::var_os("PIPELINE_PYTHON") {
        let text = value.to_string_lossy().to_string();
        if !text.is_empty() {
            return text;
        }
    }
    "python3".to_string()
}

/// One persistent worker process.
pub struct Worker {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    stdout: Option<BufReader<std::process::ChildStdout>>,
}

impl Worker {
    pub fn new() -> Self {
        Worker {
            child: None,
            stdin: None,
            stdout: None,
        }
    }

    fn ensure_started(&mut self) -> Result<(), String> {
        if self.child.is_some() {
            return Ok(());
        }
        let interpreter = python_interpreter();
        let mut child = Command::new(&interpreter)
            .arg("-m")
            .arg("library.tools.resolved.worker")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|exc| {
                format!("could not start the Resolve worker ({interpreter}): {exc}")
            })?;
        self.stdin = child.stdin.take();
        self.stdout = child.stdout.take().map(BufReader::new);
        self.child = Some(child);
        Ok(())
    }

    /// Send one request, read one response. A worker that dies answering is
    /// restarted; the caller sees the failure.
    pub fn run(&mut self, request: &Value) -> Result<WorkerResponse, String> {
        self.ensure_started()?;
        {
            let stdin = self.stdin.as_mut().expect("worker stdin");
            let body = serde_json::to_string(request).unwrap_or_else(|_| "{}".to_string());
            stdin
                .write_all(body.as_bytes())
                .and_then(|_| stdin.write_all(b"\n"))
                .and_then(|_| stdin.flush())
                .map_err(|exc| {
                    self.restart();
                    format!("the Resolve worker did not take the request: {exc}")
                })?;
        }
        let mut line = String::new();
        let read = {
            let stdout = self.stdout.as_mut().expect("worker stdout");
            stdout.read_line(&mut line)
        };
        match read {
            Ok(0) => {
                self.restart();
                Err("the Resolve worker closed its answer".to_string())
            }
            Ok(_) => {
                let response: Value = serde_json::from_str(line.trim()).map_err(|exc| {
                    format!("the worker's answer was not JSON: {exc}")
                })?;
                Ok(WorkerResponse {
                    ok: response.get("ok").and_then(|v| v.as_bool()).unwrap_or(false),
                    result: response.get("result").cloned(),
                    error: response
                        .get("error")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    refused: response
                        .get("refused")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                })
            }
            Err(exc) => {
                self.restart();
                Err(format!("the Resolve worker did not answer: {exc}"))
            }
        }
    }

    fn restart(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.stdin = None;
        self.stdout = None;
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// The worker request for one submission's validation.
pub fn prepare_request(kind: &str, params: &Value, qualification: bool) -> Value {
    serde_json::json!({
        "id": "",
        "kind": "prepare",
        "submit": {"kind": kind, "params": params, "qualification": qualification},
    })
}

/// The worker request for one broker job.
pub fn job_request(job: &crate::broker::JobView) -> Value {
    serde_json::json!({
        "id": job.id,
        "kind": "job",
        "job": {
            "id": job.id,
            "kind": job.kind,
            "params": job.params,
            "project": job.project,
            "timeline": job.timeline,
            "qualification": job.qualification,
            "mode": job.mode,
            "executed": job.executed,
        },
    })
}

/// The worker request for the watchdog's bounded probe.
pub fn probe_request(id: &str) -> Value {
    serde_json::json!({"id": id, "kind": "probe"})
}

/// The worker request for a health round trip.
pub fn health_request(id: &str) -> Value {
    serde_json::json!({"id": id, "kind": "health"})
}

/// The worker request for the post-restart check.
pub fn post_restart_check_request(id: &str) -> Value {
    serde_json::json!({"id": id, "kind": "post_restart_check"})
}
