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
    /// The id of the request this answers. Empty when the worker did not
    /// name one.
    pub id: String,
    pub ok: bool,
    pub result: Option<Value>,
    pub error: String,
    pub refused: bool,
    pub cancelled: bool,
}

/// How many lines the daemon will skip before it gives up on finding the
/// answer to ITS request.
///
/// The worker's stdout is a protocol channel and a job that prints puts a
/// non-answer line on it. Reading one line per request then hands every
/// later job the PREVIOUS job's answer - measured on 2026-10-20, where
/// every `resolve-axi` command answered with the shadow watcher's
/// snapshot. The answer names the request it belongs to, so a line that
/// answers another request is skipped rather than believed.
const MAX_ANSWER_LINES: usize = 16;

impl WorkerResponse {
    /// The observation as a job outcome: `ok` carries the result, a refusal
    /// carries the refusal's fields, a failure carries only the error.
    pub fn into_job_outcome(self) -> (String, Option<Value>, String) {
        if self.cancelled {
            return ("cancelled".to_string(), self.result, self.error);
        }
        if self.ok {
            return ("done".to_string(), self.result, String::new());
        }
        if self.refused {
            return ("rejected".to_string(), self.result, self.error);
        }
        ("failed".to_string(), None, self.error)
    }
}

/// The broker's own pid, in the environment of every worker it starts.
///
/// The worker ends when that pid is gone: it holds the instance lease
/// while it works, and a worker that outlives its broker holds the one
/// Resolve against everyone else. The daemon's own shutdown kills an
/// idle worker; the worker watches this pid for the case where the
/// broker died with a job in flight (measured 2026-10-20: an orphan
/// worker held the instance flock for 3h10m after `ren resolved stop`).
pub const BROKER_PID_ENV: &str = "REN_RESOLVED_BROKER_PID";

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
    /// Set when the broker stopped this worker: it must not start again.
    stopped: bool,
}

impl Worker {
    pub fn new() -> Self {
        Worker {
            child: None,
            stdin: None,
            stdout: None,
            stopped: false,
        }
    }

    fn ensure_started(&mut self) -> Result<(), String> {
        if self.stopped {
            return Err("the Resolve worker was stopped with the broker".to_string());
        }
        if self.child.is_some() {
            return Ok(());
        }
        let interpreter = python_interpreter();
        let mut child = Command::new(&interpreter)
            .arg("-m")
            .arg("library.tools.resolved.worker")
            // The worker ends when this broker is gone: it holds the
            // instance lock while it works, so one that outlives its
            // broker holds the one Resolve against everyone else.
            .env(BROKER_PID_ENV, std::process::id().to_string())
            // The broker's directory, so the worker writes its
            // cancellation markers beside the broker that queued the
            // job rather than beside the machine's default.
            .env(
                crate::server::BROKER_DIR_TOLD_ENV,
                crate::server::broker_dir().to_string_lossy().to_string(),
            )
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|exc| format!("could not start the Resolve worker ({interpreter}): {exc}"))?;
        self.stdin = child.stdin.take();
        self.stdout = child.stdout.take().map(BufReader::new);
        self.child = Some(child);
        Ok(())
    }

    /// Start the prepare worker and complete a harmless validation request.
    /// This moves Python startup/import cost out of the first client submit.
    pub fn warm_prepare(&mut self) -> Result<(), String> {
        let response = self.run(&prepare_request(
            "lease",
            &serde_json::json!({"exclusive": false}),
            false,
        ))?;
        if response.ok {
            Ok(())
        } else {
            Err(format!("prepare worker warmup failed: {}", response.error))
        }
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
        let wanted = request_id(request);
        let mut last = String::new();
        for _ in 0..MAX_ANSWER_LINES {
            let mut line = String::new();
            let read = {
                let stdout = self.stdout.as_mut().expect("worker stdout");
                stdout.read_line(&mut line)
            };
            match read {
                Ok(0) => {
                    self.restart();
                    return Err("the Resolve worker closed its answer".to_string());
                }
                Ok(_) => match classify_line(&line, &wanted) {
                    Line::Answer(response) => return Ok(response),
                    Line::Skip => last = "another request's answer".to_string(),
                    Line::Broken(exc) => {
                        // A line that is not an answer at all: a job that
                        // printed on the protocol channel. Skip it and
                        // read on - the answer is still coming.
                        last = exc;
                    }
                },
                Err(exc) => {
                    self.restart();
                    return Err(format!("the Resolve worker did not answer: {exc}"));
                }
            }
        }
        Err(format!(
            "the Resolve worker did not answer this request within {MAX_ANSWER_LINES} lines: {last}"
        ))
    }

    /// Send one request, read one response, within `timeout`.
    ///
    /// `run` blocks until the worker answers: a child that never
    /// answers wedges the caller, and with it the daemon thread and
    /// the shared worker mutex behind it. A diagnostic that must
    /// answer promptly uses this instead: the read runs on a thread
    /// owning the stdout handle, and on expiry the child is killed -
    /// its closing pipe releases the reader, so no thread outlives
    /// the call - and the worker is reset, so the next request
    /// relaunches it.
    pub fn run_with_timeout(
        &mut self,
        request: &Value,
        timeout: std::time::Duration,
    ) -> Result<WorkerResponse, String> {
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
        let stdout = self.stdout.take().expect("worker stdout");
        let (tx, rx) = std::sync::mpsc::channel();
        let wanted = request_id(request);
        std::thread::spawn(move || {
            let mut reader = stdout;
            let mut last = String::new();
            for _ in 0..MAX_ANSWER_LINES {
                let mut line = String::new();
                let read = reader.read_line(&mut line);
                match read {
                    Ok(0) => {
                        let _ = tx.send((Err("the Resolve worker closed its answer".to_string()), reader));
                        return;
                    }
                    Ok(_) => match classify_line(&line, &wanted) {
                        Line::Answer(response) => {
                            let _ = tx.send((Ok(response), reader));
                            return;
                        }
                        Line::Skip => last = "another request's answer".to_string(),
                        Line::Broken(exc) => last = exc,
                    },
                    Err(exc) => {
                        let _ = tx.send((Err(format!("the Resolve worker did not answer: {exc}")), reader));
                        return;
                    }
                }
            }
            let _ = tx.send((
                Err(format!(
                    "the Resolve worker did not answer this request within {MAX_ANSWER_LINES} lines: {last}"
                )),
                reader,
            ));
        });
        let (answer, reader) = rx.recv_timeout(timeout).map_err(|_| {
            // The reader still owns the old stdout handle; killing the
            // child closes the pipe, so its read ends and it exits.
            // `restart` resets the worker - the next request
            // relaunches it.
            self.restart();
            format!(
                "the Resolve worker did not answer within {}s",
                timeout.as_secs_f64()
            )
        })?;
        self.stdout = Some(reader);
        answer
    }

    fn restart(&mut self) {
        if let Some(mut child) = self.child.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
        self.stdin = None;
        self.stdout = None;
    }

    /// End this worker: it must not outlive the broker that started it.
    ///
    /// The worker holds the instance lease while it works, so a worker
    /// left running after the broker stops holds the one Resolve against
    /// every later writer, which then times out waiting for an instance
    /// nobody is using. Stopping it here is the broker's half of that
    /// promise; a worker that was inside a job when the broker died ends
    /// itself instead (it watches the broker's pid).
    pub fn stop(&mut self) {
        self.stopped = true;
        self.restart();
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        self.stop();
    }
}

/// One worker output line, read against the request that asked for it.
#[derive(Debug)]
enum Line {
    /// The answer to the request that asked.
    Answer(WorkerResponse),
    /// A line that answers another request, or is not an answer at all.
    Skip,
    /// A line that could not be read as an answer, and why.
    Broken(String),
}

/// Classify one worker output line against the request that asked.
///
/// A line whose answer names ANOTHER request is not this request's
/// answer, and a line that is not an answer at all (a job that printed on
/// the protocol channel) is not either. Both are skipped: the daemon
/// reads on until it finds the answer to its own request.
fn classify_line(line: &str, wanted: &str) -> Line {
    match decode_response(line) {
        Ok(response) if response.id.is_empty() || response.id == wanted => {
            Line::Answer(response)
        }
        Ok(_) => Line::Skip,
        Err(exc) => Line::Broken(exc),
    }
}

/// The id a request names, or "" when it names none.
fn request_id(request: &Value) -> String {
    request
        .get("id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string()
}

/// Decode one worker answer line into a response.
fn decode_response(line: &str) -> Result<WorkerResponse, String> {
    let response: Value = serde_json::from_str(line.trim())
        .map_err(|exc| format!("the worker's answer was not JSON: {exc}"))?;
    Ok(WorkerResponse {
        id: response
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        ok: response
            .get("ok")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
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
        cancelled: response
            .get("cancelled")
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
    })
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

#[cfg(test)]
mod tests {
    use super::*;

    /// The defect, measured 2026-10-20: the worker's stdout is a
    /// protocol channel, and a job that prints on it put a line where an
    /// answer belonged. Reading one line per request then handed every
    /// later job the PREVIOUS job's answer - every `resolve-axi` command
    /// answered with the shadow watcher's snapshot.
    #[test]
    fn a_line_answering_another_request_is_not_this_requests_answer() {
        let line = "{\"id\": \"other-job\", \"ok\": true, \"result\": {\"n\": 1}}";
        match classify_line(line, "this-job") {
            Line::Answer(_) => panic!("another request's answer was taken as this one's"),
            other => assert!(matches!(other, Line::Skip)),
        }
    }

    #[test]
    fn a_line_that_is_not_an_answer_is_skipped() {
        let line = "touch-reel Reel 01 - a: composed";
        match classify_line(line, "this-job") {
            Line::Answer(_) => panic!("a printed line was taken as an answer"),
            Line::Broken(reason) => assert!(reason.contains("not JSON")),
            Line::Skip => panic!("a printed line is not another request's answer"),
        }
    }

    #[test]
    fn the_answer_to_this_request_is_taken() {
        let line = "{\"id\": \"this-job\", \"ok\": true, \"result\": {\"n\": 1}}";
        match classify_line(line, "this-job") {
            Line::Answer(response) => {
                assert!(response.ok);
                assert_eq!(response.id, "this-job");
            }
            _ => panic!("this request's own answer was not taken"),
        }
    }

    /// The warmup asks with no id, so an answer with no id is taken.
    #[test]
    fn an_answer_with_no_id_is_taken() {
        let line = "{\"id\": \"\", \"ok\": true, \"result\": {}}";
        match classify_line(line, "") {
            Line::Answer(response) => assert!(response.ok),
            _ => panic!("an answer with no id was not taken"),
        }
    }
}
