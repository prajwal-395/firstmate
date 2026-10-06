//! A small Rust client for the ML worker protocol.
//!
//! Spawns the Python ML worker (`python3 -m library.tools.ml_worker`),
//! sends one typed request over stdin, and reads one typed response
//! over stdout. Proves the end-to-end path: Rust -> typed request ->
//! Python ML worker -> typed response -> Rust.
//!
//! A crash in the worker (process exits before answering) is a typed
//! error, never a hang: the client reads with a deadline and reports
//! the worker's exit status.
//!
//! Usage:
//!     ren ml-worker-client [--texts "hello" "world"]
//!
//! Environment:
//!     REN_ENGINE_ROOT - the engine tree holding library/tools/ml_worker.py
//!     PIPELINE_PYTHON - the interpreter carrying the ML stack

use std::env;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use ren_protocol::ml_worker::{
    MlWorkerError, MlWorkerMessage, MlWorkerRequest, MlWorkerResponse, MlWorkerStatus,
};
use ren_protocol::capability::CapabilityId;
use serde_json::{json, Value};

const CAPABILITY_ID: &str = "ml.embed";
const READ_TIMEOUT: Duration = Duration::from_secs(120);

fn main() {
    let code = run();
    std::process::exit(code);
}

fn run() -> i32 {
    let args: Vec<String> = env::args().skip(1).collect();
    let texts = parse_texts(&args);

    let engine_root = match resolve_engine_root() {
        Ok(root) => root,
        Err(message) => {
            eprintln!("{message}");
            return 3;
        }
    };

    let interpreter = match resolve_interpreter(&engine_root) {
        Ok(path) => path,
        Err(message) => {
            eprintln!("{message}");
            return 3;
        }
    };

    let request = MlWorkerRequest {
        request_id: "ml-worker-client-1".to_string(),
        capability_id: CapabilityId(CAPABILITY_ID.to_string()),
        payload: json!({ "texts": texts }),
        deadline_ms: None,
    };

    match call_worker(&engine_root, &interpreter, &request) {
        Ok(response) => {
            print_response(&response);
            match response.status {
                MlWorkerStatus::Ok => 0,
                MlWorkerStatus::Refused => 3,
                MlWorkerStatus::Error => 4,
            }
        }
        Err(error) => {
            eprintln!(
                "ml-worker-client: worker failed - {}: {}: {}",
                error.code, error.what, error.detail
            );
            4
        }
    }
}

fn parse_texts(args: &[String]) -> Vec<String> {
    let mut texts = Vec::new();
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--texts" {
            i += 1;
            while i < args.len() && !args[i].starts_with("--") {
                texts.push(args[i].clone());
                i += 1;
            }
        } else {
            i += 1;
        }
    }
    if texts.is_empty() {
        texts = vec![
            "hello world".to_string(),
            "video editing pipeline".to_string(),
        ];
    }
    texts
}

fn resolve_engine_root() -> Result<PathBuf, String> {
    if let Some(value) = env::var_os("REN_ENGINE_ROOT") {
        let path = PathBuf::from(value);
        if is_engine_root(&path) {
            return Ok(path);
        }
        return Err(format!(
            "ren ml-worker-client: $REN_ENGINE_ROOT does not hold an engine: {}",
            path.display()
        ));
    }
    let exe = env::current_exe().map_err(|e| e.to_string())?;
    let mut dir = exe.parent();
    while let Some(candidate) = dir {
        if is_engine_root(candidate) {
            return Ok(candidate.to_path_buf());
        }
        dir = candidate.parent();
    }
    Err("ren ml-worker-client: no engine root found. Set $REN_ENGINE_ROOT.".to_string())
}

fn is_engine_root(path: &Path) -> bool {
    path.join("manage_project.py").is_file() && path.join("library").is_dir()
}

fn resolve_interpreter(root: &Path) -> Result<PathBuf, String> {
    let mut candidates = vec![];
    if let Some(value) = env::var_os("PIPELINE_PYTHON") {
        if !value.is_empty() {
            candidates.push(PathBuf::from(value));
        }
    }
    candidates.push(root.join(".venv").join("bin").join("python3"));
    for candidate in &candidates {
        if candidate.is_file() {
            return Ok(candidate.clone());
        }
    }
    Err(format!(
        "ren ml-worker-client: no interpreter found. Tried: {}",
        candidates
            .iter()
            .map(|c| c.display().to_string())
            .collect::<Vec<_>>()
            .join(", ")
    ))
}

fn call_worker(
    engine_root: &Path,
    interpreter: &Path,
    request: &MlWorkerRequest,
) -> Result<MlWorkerResponse, MlWorkerError> {
    let mut child = Command::new(interpreter)
        .arg("-m")
        .arg("library.tools.ml_worker")
        .current_dir(engine_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| MlWorkerError {
            code: "ML_WORKER_SPAWN_FAILED".to_string(),
            what: "cannot spawn the ML worker".to_string(),
            detail: e.to_string(),
        })?;

    let request_line = serde_json::to_string(&MlWorkerMessage::Request(request.clone()))
        .map_err(|e| MlWorkerError {
            code: "ML_WORKER_SERIALIZE_FAILED".to_string(),
            what: "cannot serialize the request".to_string(),
            detail: e.to_string(),
        })?;

    {
        let stdin = child.stdin.as_mut().unwrap();
        writeln!(stdin, "{request_line}").map_err(|e| MlWorkerError {
            code: "ML_WORKER_STDIN_FAILED".to_string(),
            what: "cannot write the request".to_string(),
            detail: e.to_string(),
        })?;
    }

    let stdout = child.stdout.take().unwrap();
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    let started = Instant::now();

    loop {
        line.clear();
        let read = reader.read_line(&mut line).map_err(|e| MlWorkerError {
            code: "ML_WORKER_READ_FAILED".to_string(),
            what: "cannot read the worker's stdout".to_string(),
            detail: e.to_string(),
        })?;

        if read == 0 {
            let status = child.wait().ok();
            return Err(MlWorkerError {
                code: "ML_WORKER_CRASHED".to_string(),
                what: "the ML worker exited before answering".to_string(),
                detail: format!(
                    "exit status: {}",
                    status
                        .map(|s| s.to_string())
                        .unwrap_or_else(|| "unknown".to_string())
                ),
            });
        }

        let message: Value = serde_json::from_str(&line).map_err(|e| MlWorkerError {
            code: "ML_WORKER_BAD_MESSAGE".to_string(),
            what: "the worker sent a message that is not valid JSON".to_string(),
            detail: e.to_string(),
        })?;

        if message["message"] == "response" {
            let response: MlWorkerResponse =
                serde_json::from_value(message).map_err(|e| MlWorkerError {
                    code: "ML_WORKER_BAD_RESPONSE".to_string(),
                    what: "the worker's response does not fit MlWorkerResponse".to_string(),
                    detail: e.to_string(),
                })?;
            return Ok(response);
        }

        if started.elapsed() > READ_TIMEOUT {
            let _ = child.kill();
            return Err(MlWorkerError {
                code: "ML_WORKER_TIMEOUT".to_string(),
                what: "the worker did not answer in time".to_string(),
                detail: format!("no response after {:?}", READ_TIMEOUT),
            });
        }
    }
}

fn print_response(response: &MlWorkerResponse) {
    match response.status {
        MlWorkerStatus::Ok => {
            if let Some(payload) = &response.payload {
                let vectors = payload["vectors"].as_array().map(|v| v.len()).unwrap_or(0);
                let dimension = payload["dimension"].as_i64().unwrap_or(0);
                let model = payload["model"].as_str().unwrap_or("unknown");
                println!(
                    "ok: {vectors} vectors, dimension {dimension}, model {model}"
                );
            } else {
                println!("ok: (no payload)");
            }
        }
        MlWorkerStatus::Refused => {
            if let Some(refusal) = &response.refusal {
                println!(
                    "refused: {} - {} (fix: {})",
                    refusal.code, refusal.what, refusal.fix
                );
            } else {
                println!("refused: (no refusal detail)");
            }
        }
        MlWorkerStatus::Error => {
            if let Some(error) = &response.error {
                println!("error: {} - {}", error.code, error.what);
            } else {
                println!("error: (no error detail)");
            }
        }
    }
}
