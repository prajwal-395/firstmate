//! The native verbs' contract, run against the built binary: for every
//! command this crate serves natively, the native stdout, stderr and exit
//! code are identical to the Python implementation's on the same inputs.
//!
//! This is the gate that keeps a native command from drifting: a later
//! Python change that alters what `ren info` prints fails here, on the
//! native side, rather than shipping a silent divergence.

use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

/// The engine root - the checkout this crate is built from. `CARGO_MANIFEST_DIR`
/// is `native/ren-cli`; the engine root is two levels up.
fn engine_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Run the native binary with a controlled environment.
fn run_native(args: &[&str], env: &[(String, String)]) -> std::process::Output {
    let mut command = Command::new(env!("CARGO_BIN_EXE_ren"));
    command.args(args);
    command.env("REN_ENGINE_ROOT", engine_root());
    for (key, value) in env {
        command.env(key, value);
    }
    command.output().unwrap()
}

/// Run the Python implementation with the same environment.
fn run_python(args: &[&str], env: &[(String, String)]) -> std::process::Output {
    let mut command = Command::new("python3");
    command.arg("-m").arg("ren.cli").args(args);
    command.env("REN_ENGINE_ROOT", engine_root());
    command.env("PYTHONPATH", engine_root());
    for (key, value) in env {
        command.env(key, value);
    }
    command.output().unwrap()
}

/// Assert the native and Python runs agree on stdout, stderr and exit code.
fn assert_identical(args: &[&str], env: &[(String, String)]) {
    let native = run_native(args, env);
    let python = run_python(args, env);
    assert_eq!(
        native.stdout,
        python.stdout,
        "stdout differs for `{args:?}`\nnative: {}\npython: {}",
        String::from_utf8_lossy(&native.stdout),
        String::from_utf8_lossy(&python.stdout),
    );
    assert_eq!(
        native.stderr,
        python.stderr,
        "stderr differs for `{args:?}`\nnative: {}\npython: {}",
        String::from_utf8_lossy(&native.stderr),
        String::from_utf8_lossy(&python.stderr),
    );
    assert_eq!(
        native.status,
        python.status,
        "exit code differs for `{args:?}`: native {:?}, python {:?}",
        native.status.code(),
        python.status.code(),
    );
}

/// A scratch project root holding one valid project, and the env that
/// points PIPELINE_PROJECTS_ROOT at it.
struct Project {
    _root: PathBuf,
    projects_root: PathBuf,
}

fn setup_project(label: &str, slug: &str) -> Project {
    let root = env::temp_dir().join(format!("ren-native-{label}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&root);
    let projects_root = root.join("projects");
    let project = projects_root.join(slug);
    fs::create_dir_all(project.join("raw")).unwrap();
    fs::create_dir_all(project.join("exports")).unwrap();
    fs::write(
        project.join("project.yaml"),
        format!(
            "project_format_version: 2\n\
             name: Demo Project\n\
             slug: {slug}\n\
             client: \"\"\n\
             created: \"2026-10-01\"\n\
             status: in_progress\n\
             source:\n\
             \x20 fps: 29.97\n\
             \x20 language: en\n\
             \x20 shape: speech\n\
             pipeline:\n\
             \x20 brand_template: \"\"\n\
             \x20 creative_brief: brief.md\n\
             resolve:\n\
             \x20 project_name: Demo Project\n\
             \x20 database: local\n\
             \x20 folder: \"\"\n\
             \x20 timeline_name: Main Edit\n\
             tags:\n\
             \x20 - test\n\
             description: A demo\n\
             ren_version: \"ren 0.1.0+abc.dev.personal\"\n"
        ),
    )
    .unwrap();
    fs::write(project.join("brief.md"), "brief\n").unwrap();
    Project {
        _root: root,
        projects_root,
    }
}

fn projects_env(project: &Project) -> Vec<(String, String)> {
    vec![(
        "PIPELINE_PROJECTS_ROOT".to_string(),
        project.projects_root.to_string_lossy().to_string(),
    )]
}

#[test]
fn version_matches_python() {
    assert_identical(&["--version"], &[]);
}

#[test]
fn version_word_matches_python() {
    assert_identical(&["version"], &[]);
}

#[test]
fn help_matches_python() {
    assert_identical(&["--help"], &[]);
}

#[test]
fn no_args_matches_python() {
    assert_identical(&[], &[]);
}

#[test]
fn info_matches_python() {
    let project = setup_project("info", "demo");
    assert_identical(&["info", "demo"], &projects_env(&project));
}

#[test]
fn info_not_found_matches_python() {
    let project = setup_project("info404", "demo");
    assert_identical(&["info", "nonexistent"], &projects_env(&project));
}

#[test]
fn status_matches_python() {
    let project = setup_project("status", "demo");
    assert_identical(&["status", "demo"], &projects_env(&project));
}

#[test]
fn status_not_found_matches_python() {
    let project = setup_project("status404", "demo");
    assert_identical(&["status", "nonexistent"], &projects_env(&project));
}

#[test]
fn check_matches_python() {
    // A real tiny video, because check's core logic is reading footage with
    // ffprobe - a refusal path alone would not exercise it. Encoded once per
    // test run, not per assertion.
    let project = setup_project("check", "demo");
    let video = project
        .projects_root
        .join("demo")
        .join("raw")
        .join("clip.mov");
    let encoded = Command::new("ffmpeg")
        .arg("-v")
        .arg("error")
        .arg("-f")
        .arg("lavfi")
        .arg("-i")
        .arg("testsrc=duration=1:size=320x240:rate=30")
        .arg("-pix_fmt")
        .arg("yuv420p")
        .arg(&video)
        .arg("-y")
        .output()
        .unwrap();
    if !encoded.status.success() {
        eprintln!("skipping check test: ffmpeg unavailable");
        return;
    }
    let sfx = project._root.join("sfx");
    let music = project._root.join("music");
    fs::create_dir_all(&sfx).unwrap();
    fs::create_dir_all(&music).unwrap();
    let mut env = projects_env(&project);
    env.push((
        "PIPELINE_SFX_LIBRARY".to_string(),
        sfx.to_string_lossy().to_string(),
    ));
    env.push((
        "PIPELINE_MUSIC_LIBRARY".to_string(),
        music.to_string_lossy().to_string(),
    ));
    assert_identical(&["check", "demo"], &env);
}

#[test]
fn check_not_found_matches_python() {
    let project = setup_project("check404", "demo");
    assert_identical(&["check", "nonexistent"], &projects_env(&project));
}

#[test]
fn check_without_raw_folder_matches_python() {
    // A project with no raw/ folder: both must refuse identically, not
    // crash - the native enumerate_footage refuses, and so must Python.
    let project = setup_project("noraw", "demo");
    let _ = fs::remove_dir_all(project.projects_root.join("demo").join("raw"));
    assert_identical(&["check", "demo"], &projects_env(&project));
}

#[test]
fn unknown_verb_matches_python() {
    assert_identical(&["bogus"], &[]);
}
