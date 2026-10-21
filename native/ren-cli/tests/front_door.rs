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

/// A tree that satisfies the engine-root sentinels, so the front door
/// resolves an engine and reaches the interpreter ladder.
fn engine_tree(root: &Path) {
    fs::create_dir_all(root.join("library")).unwrap();
    fs::create_dir_all(root.join("ren")).unwrap();
    fs::create_dir_all(root.join("bin")).unwrap();
    fs::write(root.join("manage_project.py"), "").unwrap();
    fs::write(root.join("ren").join("cli.py"), "").unwrap();
    fs::write(root.join("bin").join("ren"), "").unwrap();
    fs::write(root.join("bin").join("vep"), "").unwrap();
}

#[test]
fn a_missing_interpreter_is_refused_WITH_THE_REASON() {
    // A refusal nobody reads is a defect: the front door built the
    // ladder's message and threw it away, so `ren doctor` on a machine
    // with no interpreter exited 3 and printed NOTHING
    // (measured 2026-10-21 on a clean install into a throwaway prefix).
    let base = env::temp_dir().join(format!("ren-no-interpreter-{}", std::process::id()));
    let _ = fs::remove_dir_all(&base);
    let engine = base.join("versions").join("v1");
    engine_tree(&engine);
    let empty = base.join("empty");
    fs::create_dir_all(&empty).unwrap();
    let output = run_ren(
        &["doctor"],
        &[
            (
                "REN_ENGINE_ROOT".to_string(),
                engine.to_string_lossy().to_string(),
            ),
            ("PIPELINE_VEP_HOME".to_string(), empty.to_string_lossy().to_string()),
            ("HOME".to_string(), empty.to_string_lossy().to_string()),
            ("XDG_DATA_HOME".to_string(), empty.to_string_lossy().to_string()),
            ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ],
    );
    assert_eq!(output.status.code(), Some(3));
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("no interpreter carrying the ML stack"),
        "stderr: {stderr}"
    );
    assert!(!stderr.is_empty(), "the refusal said nothing");
    let _ = fs::remove_dir_all(&base);
}

#[test]
fn the_engines_own_install_home_is_on_the_interpreter_ladder() {
    // An engine installed into a prefix of its own keeps its runtime
    // venv beside that prefix (`<home>/venv-py312`), and the machine's
    // default `vep_home()` knows nothing about it. Without this rung the
    // installed `current/bin/ren` could not run at all (measured
    // 2026-10-21: a clean install into a throwaway prefix exited 3 with
    // no output).
    let base = env::temp_dir().join(format!("ren-own-home-{}", std::process::id()));
    let _ = fs::remove_dir_all(&base);
    let engine = base.join("versions").join("v1");
    engine_tree(&engine);
    let venv_python = base.join("venv-py312").join("bin").join("python3");
    fs::create_dir_all(venv_python.parent().unwrap()).unwrap();
    fs::write(
        &venv_python,
        "#!/bin/sh\necho ENGINE-OWN-VENV\nexit 0\n",
    )
    .unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut permissions = fs::metadata(&venv_python).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(&venv_python, permissions).unwrap();
    }
    let empty = base.join("empty");
    fs::create_dir_all(&empty).unwrap();
    let output = run_ren(
        &["doctor"],
        &[
            (
                "REN_ENGINE_ROOT".to_string(),
                engine.to_string_lossy().to_string(),
            ),
            ("PIPELINE_VEP_HOME".to_string(), empty.to_string_lossy().to_string()),
            ("HOME".to_string(), empty.to_string_lossy().to_string()),
            ("XDG_DATA_HOME".to_string(), empty.to_string_lossy().to_string()),
            ("PATH".to_string(), "/usr/bin:/bin".to_string()),
        ],
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    assert!(
        stdout.contains("ENGINE-OWN-VENV"),
        "the engine's own venv was not on the ladder; stdout: {stdout}; stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let _ = fs::remove_dir_all(&base);
}
