//! `ren`: the native front door an engine tree ships at `bin/ren`.
//!
//! The Python implementation is untouched. This binary resolves the
//! engine root and an interpreter, then `exec`s `python -m ren.cli`
//! with the original argv - so stdin, stdout, stderr, the exit code
//! and every signal pass through to the interpreter untouched, and
//! `ren` is one process end to end.
//!
//! Interpreter discovery asks the repo's one ladder
//! (`library.tools.shared_environment --resolve-interpreter`) instead
//! of carrying a copy: the ladder is Python data with two callers that
//! cannot import it, and a second copy would drift the first time only
//! one side changed. When the ladder finds nothing, the refusal is
//! the ladder's own message, verbatim, so the wording has one source.

use std::env;
use std::ffi::OsString;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;

/// Every refusal here exits 3, the code `bin/vep` and `ren.cli` use
/// for "no engine / no interpreter": a refusal, not a crash.
const REFUSAL_EXIT_CODE: i32 = 3;

/// The ladder's answer, as `shared_environment --resolve-interpreter`
/// prints it on stdout. `tried` travels only inside `error` and is not
/// read here.
#[derive(serde::Deserialize, Default)]
struct InterpreterReport {
    python: String,
    #[serde(default)]
    error: String,
}

fn main() {
    let argv: Vec<OsString> = env::args_os().skip(1).collect();
    if let Err(message) = run(&argv) {
        eprintln!("{message}");
        std::process::exit(REFUSAL_EXIT_CODE);
    }
}

/// The whole front door: resolve, then replace this process. Returns
/// `Err` only for a refusal, which `main` prints before exiting.
fn run(argv: &[OsString]) -> Result<(), String> {
    let root = engine_root()?;
    let python3 = python3_on_path().ok_or_else(|| {
        "ren: no python3 on PATH to resolve the interpreter ladder.\n\
         Install a Python 3, then see docs/ML_ENVIRONMENT.md."
            .to_string()
    })?;
    let report = ask_ladder(&python3, &root);
    if report.python.is_empty() {
        return Err(refusal(&report));
    }
    exec_python(&report.python, &root, argv)
}

/// The engine root: `$REN_ENGINE_ROOT` when it names one (an empty
/// value counts as unset, matching the `${REN_ENGINE_ROOT:-...}` the
/// bash front door used), else the tree this binary sits in.
fn engine_root() -> Result<PathBuf, String> {
    if let Some(explicit) = explicit_engine_root() {
        return Ok(explicit);
    }
    engine_root_from(&current_exe()?)
}

fn explicit_engine_root() -> Option<PathBuf> {
    match env::var_os("REN_ENGINE_ROOT") {
        Some(value) if !value.is_empty() => Some(PathBuf::from(value)),
        _ => None,
    }
}

fn current_exe() -> Result<PathBuf, String> {
    env::current_exe().map_err(|err| format!("ren: cannot locate itself: {err}"))
}

/// The tree holding `bin/<exe>`. Symlinks are resolved first, so a
/// front door reached through the installed `current` pointer still
/// names the version directory it actually runs from.
fn engine_root_from(exe: &Path) -> Result<PathBuf, String> {
    let resolved = fs::canonicalize(exe).unwrap_or_else(|_| exe.to_path_buf());
    let bin_dir = resolved.parent().ok_or_else(|| {
        "ren: cannot locate the engine root beside this binary.".to_string()
    })?;
    bin_dir.parent().map(Path::to_path_buf).ok_or_else(|| {
        "ren: cannot locate the engine root beside this binary.".to_string()
    })
}

/// The ladder's query vehicle: the first `python3` on PATH, found the
/// way `bin/vep` finds it. Any Python answers - the ladder evaluates
/// only stdlib path predicates, so the asker's version does not matter.
fn python3_on_path() -> Option<PathBuf> {
    let path = env::var_os("PATH")?;
    first_executable(&path, "python3")
}

fn first_executable(path: &OsString, name: &str) -> Option<PathBuf> {
    for dir in env::split_paths(path) {
        let candidate = dir.join(name);
        if is_executable(&candidate) {
            return Some(candidate);
        }
    }
    None
}

fn is_executable(path: &Path) -> bool {
    matches!(fs::metadata(path),
        Ok(metadata) if metadata.is_file() && metadata.permissions().mode() & 0o111 != 0)
}

/// Ask the ladder where the interpreter carrying the ML stack is. A
/// query that cannot be answered (no vehicle, unparseable answer) is
/// the ladder finding nothing: the refusal below then says the answer
/// was unparseable rather than inventing a reason.
fn ask_ladder(python3: &Path, root: &Path) -> InterpreterReport {
    let Some(pythonpath) = engine_pythonpath(root) else {
        return InterpreterReport::default();
    };
    let Ok(output) = Command::new(python3)
        .arg("-m")
        .arg("library.tools.shared_environment")
        .arg("--resolve-interpreter")
        .arg("--repo-root")
        .arg(root)
        .env("PYTHONPATH", pythonpath)
        .env("PYTHONSAFEPATH", "1")
        .output()
    else {
        return InterpreterReport::default();
    };
    serde_json::from_slice(&output.stdout).unwrap_or_default()
}

/// The refusal, in `bin/vep`'s shape: one line saying what this is,
/// then the ladder's own message naming what is missing and the fix.
fn refusal(report: &InterpreterReport) -> String {
    let mut message = String::from("ren: refusing - no interpreter carrying the ML stack.");
    if report.error.is_empty() {
        message.push_str(
            "\n(the ladder answered nothing parseable; see docs/ML_ENVIRONMENT.md)",
        );
    } else {
        message.push('\n');
        message.push_str(&report.error);
    }
    message
}

/// Replace this process with the Python implementation. `exec` means
/// the interpreter's exit code and signals are `ren`'s, untouched.
fn exec_python(interpreter: &str, root: &Path, argv: &[OsString]) -> Result<(), String> {
    let pythonpath = engine_pythonpath(root)
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
        "ren: cannot run {interpreter} - {exec_error}.\n\
         Point $PIPELINE_PYTHON at a working Python 3.12, or run `ren setup`."
    ))
}

/// `PYTHONPATH` with the engine root prepended, as the bash front door
/// and `bin/vep` set it: the tree this binary runs from first, the
/// caller's own path after it.
fn engine_pythonpath(root: &Path) -> Option<OsString> {
    let mut paths = vec![root.to_path_buf()];
    if let Some(existing) = env::var_os("PYTHONPATH") {
        if !existing.is_empty() {
            paths.extend(env::split_paths(&existing));
        }
    }
    env::join_paths(paths).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn engine_root_is_the_tree_holding_the_binary() {
        let exe = Path::new("/tmp/some-engine/bin/ren");
        assert_eq!(
            engine_root_from(exe).unwrap(),
            PathBuf::from("/tmp/some-engine")
        );
    }

    #[test]
    fn engine_root_of_a_binary_at_the_root_is_refused() {
        assert!(engine_root_from(Path::new("/ren")).is_err());
    }

    #[test]
    fn an_explicit_engine_root_wins_over_the_binarys_own_tree() {
        let _guard = env_lock();
        env::set_var("REN_ENGINE_ROOT", "/tmp/elsewhere-engine");
        assert_eq!(
            explicit_engine_root(),
            Some(PathBuf::from("/tmp/elsewhere-engine"))
        );
        env::remove_var("REN_ENGINE_ROOT");
    }

    #[test]
    fn an_empty_engine_root_counts_as_unset() {
        let _guard = env_lock();
        env::set_var("REN_ENGINE_ROOT", "");
        // `${REN_ENGINE_ROOT:-...}` treated empty as unset; so does this.
        assert_eq!(explicit_engine_root(), None);
        env::remove_var("REN_ENGINE_ROOT");
    }

    #[test]
    fn the_first_executable_on_path_wins() {
        let root = unique_temp("first-wins");
        let first = root.join("first");
        let second = root.join("second");
        let _ = fs::create_dir_all(&first);
        let _ = fs::create_dir_all(&second);
        let vehicle = first.join("python3");
        fs::write(&vehicle, b"").unwrap();
        make_executable(&vehicle);
        let path = env::join_paths([first, second]).unwrap();
        assert_eq!(first_executable(&path, "python3"), Some(vehicle));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn nothing_executable_means_no_vehicle() {
        let root = unique_temp("none");
        let _ = fs::create_dir_all(&root);
        let path = env::join_paths([root.clone()]).unwrap();
        assert_eq!(first_executable(&path, "python3"), None);
        let _ = fs::remove_dir_all(&root);
    }

    /// A scratch directory no other test in this binary shares: the
    /// tests here run in one process, and a shared name would let one
    /// test's cleanup delete another's fixture.
    fn unique_temp(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "ren-cli-{label}-{}",
            std::process::id()
        ))
    }

    #[test]
    fn the_engine_root_is_prepended_to_pythonpath() {
        let _guard = env_lock();
        let root = Path::new("/tmp/some-engine");
        env::set_var("PYTHONPATH", "/already/there");
        assert_eq!(
            engine_pythonpath(root).unwrap(),
            OsString::from("/tmp/some-engine:/already/there")
        );
        env::remove_var("PYTHONPATH");
        assert_eq!(
            engine_pythonpath(root).unwrap(),
            OsString::from("/tmp/some-engine")
        );
    }

    #[test]
    fn an_empty_pythonpath_adds_nothing() {
        let _guard = env_lock();
        let root = Path::new("/tmp/some-engine");
        env::set_var("PYTHONPATH", "");
        assert_eq!(
            engine_pythonpath(root).unwrap(),
            OsString::from("/tmp/some-engine")
        );
        env::remove_var("PYTHONPATH");
    }

    /// Process environment is shared, so the tests that set a variable
    /// run one at a time.
    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        LOCK.lock().unwrap()
    }

    #[test]
    fn the_refusal_carries_the_ladders_own_message() {
        let report = InterpreterReport {
            python: String::new(),
            error: "No Python interpreter carrying the ML stack was found.".to_string(),
        };
        let message = refusal(&report);
        assert!(message.starts_with("ren: refusing - no interpreter carrying the ML stack.\n"));
        assert!(message.contains("No Python interpreter carrying the ML stack was found."));
    }

    #[test]
    fn an_unparseable_ladder_answer_says_so() {
        let report = InterpreterReport::default();
        let message = refusal(&report);
        assert!(message.contains("(the ladder answered nothing parseable; see docs/ML_ENVIRONMENT.md)"));
    }

    #[test]
    fn an_unrunnable_interpreter_is_refused_with_the_fix() {
        let error = exec_python(
            "/nonexistent/ren-python",
            Path::new("/tmp/some-engine"),
            &[],
        )
        .unwrap_err();
        assert!(error.contains("cannot run /nonexistent/ren-python"));
        assert!(error.contains("ren setup"));
    }

    fn make_executable(path: &Path) {
        let mut permissions = fs::metadata(path).unwrap().permissions();
        permissions.set_mode(0o755);
        fs::set_permissions(path, permissions).unwrap();
    }
}
