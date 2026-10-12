//! The front door's contract, run against the built binary: the engine
//! root resolves, the interpreter is resolved through the repo's one ladder
//! with no Python helper process, the file-reading verbs are served
//! natively, and everything else execs the Python implementation with the
//! original argv and the engine's own environment.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

/// The engine root - the checkout this crate is built from.
fn engine_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Run the binary with a controlled environment.
fn run_ren(args: &[&str], env: &[(String, String)]) -> std::process::Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_ren"));
    command.args(args);
    for (key, value) in env {
        command.env(key, value);
    }
    command.output().unwrap()
}

#[test]
fn version_is_served_natively_with_no_python_anywhere() {
    // No python3 on PATH and no PIPELINE_PYTHON: a native verb must not
    // need a Python process to answer.
    let output = run_ren(
        &["--version"],
        &[
            (
                "REN_ENGINE_ROOT".to_string(),
                engine_root().to_string_lossy().to_string(),
            ),
            ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ],
    );
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.starts_with("ren 0.1.0+"), "stdout: {stdout}");
    assert!(stdout.contains("(engine "), "stdout: {stdout}");
}

#[test]
fn help_is_served_natively() {
    let output = run_ren(
        &["--help"],
        &[(
            "REN_ENGINE_ROOT".to_string(),
            engine_root().to_string_lossy().to_string(),
        )],
    );
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(stdout.contains("usage: ren <verb>"));
    assert!(stdout.contains("info"));
    assert!(stdout.contains("status"));
    assert!(stdout.contains("check"));
}

#[test]
fn help_json_is_the_python_catalog() {
    // The catalog's effects live only in the Python registry, so
    // `--help --json` must reach Python rather than print the native usage.
    let output = run_ren(
        &["--help", "--json"],
        &[(
            "REN_ENGINE_ROOT".to_string(),
            engine_root().to_string_lossy().to_string(),
        )],
    );
    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(!stdout.contains("usage: ren <verb>"), "stdout: {stdout}");
    assert!(stdout.contains("\"effects\""), "stdout: {stdout}");
}

#[test]
fn the_interpreter_is_resolved_without_a_python_helper() {
    // A verb that needs Python must still run: the ladder finds the
    // interpreter natively, so no python3 helper process is started. `doctor`
    // is read-only and exits non-zero on a machine with no Resolve, which is
    // fine - what matters is that it reached Python.
    let output = run_ren(
        &["doctor"],
        &[
            (
                "REN_ENGINE_ROOT".to_string(),
                engine_root().to_string_lossy().to_string(),
            ),
            ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ],
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    // It reached Python (a refusal or a report), not a ladder refusal.
    assert!(
        !stderr.contains("no interpreter carrying the ML stack"),
        "stderr: {stderr}"
    );
}

#[test]
fn no_engine_is_refused_with_the_fix() {
    let root = env::temp_dir().join(format!("ren-no-engine-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).unwrap();
    let output = run_ren(
        &["--help"],
        &[(
            "REN_ENGINE_ROOT".to_string(),
            root.to_string_lossy().to_string(),
        )],
    );
    assert_eq!(output.status.code(), Some(3));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("ren: no engine"), "stderr: {stderr}");
    let _ = fs::remove_dir_all(&root);
}

#[test]
fn an_unknown_verb_is_refused_with_the_usage() {
    let output = run_ren(
        &["bogus"],
        &[(
            "REN_ENGINE_ROOT".to_string(),
            engine_root().to_string_lossy().to_string(),
        )],
    );
    assert_eq!(output.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("ren: unknown verb 'bogus'"),
        "stderr: {stderr}"
    );
}
