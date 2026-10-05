//! `ren`: the native front door an engine tree ships at `bin/ren`.
//!
//! Two kinds of work happen here. The front door itself - resolving the
//! engine and an interpreter, then `exec`ing `python -m ren.cli` with the
//! original argv - is unchanged: stdout, stderr, the exit code and every
//! signal pass through to the interpreter, and `ren` is one process end to
//! end for the verbs that need Python.
//!
//! On top of that, the verbs whose logic is reading Ren's own files,
//! config and on-disk records are served natively, with no Python process
//! started: `--version`, `--help`, `info`, `status`, `check`. Each mirrors
//! the Python function it replaces, so its output is identical; where the
//! Python reaches a part this crate does not port, the verb falls back to
//! execing Python rather than drifting.
//!
//! Interpreter discovery asks the repo's one ladder
//! (`library.tools.shared_environment --resolve-interpreter`) instead of
//! carrying a copy: the ladder is Python data with two callers that cannot
//! import it, and a second copy would drift the first time only one side
//! changed. When the ladder finds nothing, the refusal is the ladder's own
//! message, verbatim, so the wording has one source.

use std::env;
use std::ffi::OsString;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;

mod commands;
mod config;
mod engine;
mod json;
mod project;

use crate::commands::Outcome;

/// Every refusal here exits 3, the code `bin/vep` and `ren.cli` use for
/// "no engine / no interpreter": a refusal, not a crash.
const REFUSAL_EXIT_CODE: i32 = 3;

fn main() {
    let argv: Vec<OsString> = env::args_os().skip(1).collect();
    let code = run(&argv);
    std::process::exit(code);
}

/// The whole front door. Returns the exit code `main` exits with.
fn run(argv: &[OsString]) -> i32 {
    // `--version` answers from the package itself: no engine needed, so a
    // machine with nothing installed still names its build.
    if argv.first().map(|arg| arg == "--version" || arg == "-V").unwrap_or(false)
        || argv.first().map(|arg| arg == "version").unwrap_or(false)
    {
        println!("{}", commands::version_line(None));
        return 0;
    }

    let engine_root = match resolve_engine() {
        Ok(root) => root,
        Err(message) => {
            eprintln!("{message}");
            return REFUSAL_EXIT_CODE;
        }
    };

    if !engine_root.join("manage_project.py").is_file()
        || !engine_root.join("bin").join("vep").is_file()
    {
        eprintln!(
            "ren: no engine at {engine_root}.\n\
             Expected manage_project.py and bin/vep beside it - point $REN_ENGINE_ROOT \
             at a built engine tree (`python3 -m ren.package_engine --dest <dir>`) or \
             install Ren editable from a checkout (README, Quickstart).",
            engine_root = engine_root.display()
        );
        return REFUSAL_EXIT_CODE;
    }

    if argv.is_empty() {
        println!("{}", commands::usage());
        return 1;
    }

    let name = argv[0].to_string_lossy().to_string();
    if name == "-h" || name == "--help" || name == "help" {
        println!("{}", commands::usage());
        return 0;
    }

    if name == "info" || name == "status" || name == "check" {
        let reference = argv.get(1).map(|arg| arg.to_string_lossy().to_string()).unwrap_or_default();
        let outcome = match name.as_str() {
            "info" => commands::info(&reference, &engine_root),
            "status" => commands::status(&reference, &engine_root),
            _ => commands::check(&reference, &engine_root),
        };
        return match outcome {
            Outcome::Exited(code) => code,
            Outcome::Fallback => exec_python(&engine_root, argv).unwrap_or(REFUSAL_EXIT_CODE),
        };
    }

    if !commands::is_known_verb(&name) {
        eprintln!("ren: unknown verb '{name}'; `ren --help` lists them.");
        return 2;
    }

    exec_python(&engine_root, argv).unwrap_or(REFUSAL_EXIT_CODE)
}

/// The engine root, or the refusal naming what was tried and the fix.
fn resolve_engine() -> Result<PathBuf, String> {
    if let Some(root) = engine::find_engine_root() {
        return Ok(root);
    }
    if env::var_os(engine::ENGINE_ENV).map(|value| !value.is_empty()).unwrap_or(false) {
        return Err(engine::explicit_root_refusal());
    }
    Err(engine::no_engine_refusal())
}

/// Replace this process with the Python implementation. `exec` means the
/// interpreter's exit code and signals are `ren`'s, untouched.
fn exec_python(root: &Path, argv: &[OsString]) -> Result<i32, String> {
    let interpreter = resolve_interpreter(root)?;
    let pythonpath = engine::engine_pythonpath(root)
        .ok_or_else(|| "ren: cannot build PYTHONPATH for the engine.".to_string())?;
    let exec_error = Command::new(interpreter)
        .arg("-m")
        .arg("ren.cli")
        .args(argv)
        .env("REN_ENGINE_ROOT", root)
        .env("PYTHONPATH", pythonpath)
        .env("PYTHONSAFEPATH", "1")
        .exec();
    Err(format!(
        "ren: cannot run the interpreter - {exec_error}.\n\
         Point $PIPELINE_PYTHON at a working Python 3.12, or run `ren setup`."
    ))
}

/// The interpreter carrying the ML stack, resolved through the repo's one
/// ladder - now natively, so finding it starts no Python process. The
/// ladder is `shared_environment.INTERPRETER_CANDIDATES`: `$PIPELINE_PYTHON`,
/// then the durable venv under `vep_home`, then the checkout's own `.venv`.
/// There is no stock-interpreter rung, and there must not be one.
fn resolve_interpreter(root: &Path) -> Result<String, String> {
    let candidates = interpreter_candidates(root);
    for candidate in &candidates {
        if candidate.is_file() && is_executable(candidate) {
            return Ok(candidate.to_string_lossy().to_string());
        }
    }
    Err(refusal(&no_interpreter_message(&candidates)))
}

fn interpreter_candidates(root: &Path) -> Vec<PathBuf> {
    let mut candidates = vec![];
    if let Some(value) = env::var_os("PIPELINE_PYTHON") {
        if !value.is_empty() {
            candidates.push(engine::expanduser(&PathBuf::from(value)));
        }
    }
    candidates.push(engine::vep_home().join("venv-py312").join("bin").join("python3"));
    candidates.push(root.join(".venv").join("bin").join("python3"));
    candidates
}

/// The refusal, in `bin/vep`'s shape: one line saying what this is, then
/// the ladder's own message naming what is missing and the fix.
fn refusal(message: &str) -> String {
    format!("ren: refusing - no interpreter carrying the ML stack.\n{message}")
}

/// The ladder's own message, verbatim from `shared_environment`.
fn no_interpreter_message(candidates: &[PathBuf]) -> String {
    let mut lines = vec![
        "No Python interpreter carrying the ML stack was found.".to_string(),
        "Tried, in order:".to_string(),
    ];
    if candidates.is_empty() {
        lines.push("    (nothing - no rung applied)".to_string());
    } else {
        for candidate in candidates {
            lines.push(format!("    {}", candidate.display()));
        }
    }
    lines.push(
        "`run_pipeline.py` imports whisperx, mlx_vlm and torch through its steps, so a \
         stock interpreter is not a fallback - it dies inside a step rather than here.\n\
         Build the durable one ONCE PER MACHINE at \
         ~/.local/share/vep/venv-py312; docs/ML_ENVIRONMENT.md is the procedure."
            .to_string(),
    );
    lines.join("\n")
}

fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    matches!(std::fs::metadata(path),
        Ok(metadata) if metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_refusal_carries_the_ladders_own_message() {
        let message = refusal(
            "No Python interpreter carrying the ML stack was found.\nTried, in order:",
        );
        assert!(message.starts_with("ren: refusing - no interpreter carrying the ML stack.\n"));
        assert!(message.contains("No Python interpreter carrying the ML stack was found."));
    }

    #[test]
    fn the_ladder_finds_the_durable_venv() {
        let root = Path::new("/tmp/some-engine");
        let interpreter = resolve_interpreter(root).unwrap();
        assert!(interpreter.contains("python3"));
    }
}
