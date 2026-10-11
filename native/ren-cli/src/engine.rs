//! Where the Ren engine lives, resolved the way `ren.engine_root` resolves
//! it - the native front door and the native verbs must name the same
//! engine the Python implementation would, so a command's output cannot
//! drift between the two.
//!
//! The resolution order is `ren.engine_root.find_engine_root`'s, verbatim:
//! `$REN_ENGINE_ROOT` (when it names a real engine), then the tree holding
//! the binary, then the `<vep_home>/current` pointer. A `$REN_ENGINE_ROOT`
//! that does not hold an engine resolves to NOTHING - it is an explicit
//! choice, not a fallback - so the caller refuses naming it.

use std::env;
use std::ffi::OsString;
use std::fs;
use std::path::{Path, PathBuf};

/// `$REN_ENGINE_ROOT`, the one variable that names the engine outright.
pub const ENGINE_ENV: &str = "REN_ENGINE_ROOT";

/// What makes a directory an engine root, as `(sentinel, kind)` - the same
/// table `ren.engine_root.SENTINELS` carries.
const SENTINELS: &[(&str, &str)] = &[
    ("manage_project.py", "file"),
    ("library", "directory"),
    ("ren/cli.py", "file"),
    ("bin/ren", "file"),
    ("bin/vep", "file"),
];

/// The per-machine directory shared things live in: `$PIPELINE_VEP_HOME`,
/// else `$XDG_DATA_HOME/vep`, else `~/.local/share/vep`.
pub fn vep_home() -> PathBuf {
    if let Some(explicit) = env::var_os("PIPELINE_VEP_HOME") {
        return expanduser(&PathBuf::from(explicit));
    }
    let base = match env::var_os("XDG_DATA_HOME") {
        Some(xdg) => expanduser(&PathBuf::from(xdg)),
        None => home_dir().join(".local").join("share"),
    };
    base.join("vep")
}

/// The current user's home, the way `Path.home()` answers it.
pub fn home_dir() -> PathBuf {
    match env::var_os("HOME") {
        Some(home) => PathBuf::from(home),
        None => PathBuf::from("/"),
    }
}

/// Expand a leading `~` against the current user's home, the way
/// `Path.expanduser()` answers it.
pub fn expanduser(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    if text == "~" {
        return home_dir();
    }
    if let Some(rest) = text.strip_prefix("~/") {
        return home_dir().join(rest);
    }
    path.to_path_buf()
}

/// Does `path` hold the engine this resolver would run? The sentinel table
/// above, every one required.
pub fn is_engine_root(path: &Path) -> bool {
    if !path.is_dir() {
        return false;
    }
    SENTINELS.iter().all(|(sentinel, kind)| {
        let candidate = path.join(sentinel);
        if *kind == "file" {
            candidate.is_file()
        } else {
            candidate.is_dir()
        }
    })
}

/// The tree holding `bin/<exe>`, resolving symlinks first so a front door
/// reached through the installed `current` pointer still names the version
/// directory it actually runs from.
pub fn engine_root_from(exe: &Path) -> Option<PathBuf> {
    let resolved = fs::canonicalize(exe).unwrap_or_else(|_| exe.to_path_buf());
    let bin_dir = resolved.parent()?;
    bin_dir.parent().map(Path::to_path_buf)
}

/// This binary's own path, for `engine_root_from`.
pub fn current_exe() -> Option<PathBuf> {
    env::current_exe().ok()
}

/// `<vep_home>/current`: the symlink naming the live version. A dangling or
/// absent pointer is `None`, never an error.
pub fn installed_root() -> Option<PathBuf> {
    let link = vep_home().join("current");
    if !link.exists() && !link.is_symlink() {
        return None;
    }
    let target = fs::canonicalize(&link).ok()?;
    if is_engine_root(&target) {
        return Some(target);
    }
    if is_engine_root(&target.join("engine")) {
        return Some(target.join("engine"));
    }
    None
}

/// The engine root by the resolution order, or `None`. Never refuses - a
/// caller that needs the engine refuses through the message.
pub fn find_engine_root() -> Option<PathBuf> {
    if let Some(explicit) = explicit_engine_root() {
        return Some(explicit);
    }
    if let Some(exe) = current_exe() {
        if let Some(adjacent) = engine_root_from(&exe) {
            if is_engine_root(&adjacent) {
                return Some(adjacent);
            }
        }
    }
    installed_root()
}

/// `$REN_ENGINE_ROOT` when it names a real engine. An empty value counts as
/// unset; a value that holds no engine resolves to nothing (the caller
/// refuses), so this returns `None` for it too.
fn explicit_engine_root() -> Option<PathBuf> {
    let value = env::var_os(ENGINE_ENV)?;
    if value.is_empty() {
        return None;
    }
    let path = expanduser(&PathBuf::from(value));
    if is_engine_root(&path) {
        Some(path)
    } else {
        None
    }
}

/// The refusal when `$REN_ENGINE_ROOT` is set but names no engine: the
/// variable is an explicit choice, so it is refused rather than fallen back
/// from.
pub fn explicit_root_refusal() -> String {
    let value = env::var_os(ENGINE_ENV).unwrap_or_default();
    let value = value.to_string_lossy().to_string();
    format!(
        "ren: no engine at ${ENGINE_ENV}={value}.\n\
         That variable names the engine root outright; it must hold \
         manage_project.py, library/ and ren/cli.py.\n\
         Unset it to let ren find the checkout or the installed pointer instead."
    )
}

/// The refusal when nothing resolves: every place the resolver looked, and
/// the fix.
pub fn no_engine_refusal() -> String {
    let mut lines = vec![
        "ren: no engine found.".to_string(),
        format!("    ${ENGINE_ENV}=",)
            + &env::var_os(ENGINE_ENV)
                .map(|v| v.to_string_lossy().to_string())
                .unwrap_or_else(|| "(unset)".to_string()),
    ];
    if let Some(exe) = current_exe() {
        if let Some(adjacent) = engine_root_from(&exe) {
            lines.push(format!(
                "    beside the ren package: {}",
                adjacent.display()
            ));
        }
    }
    let link = vep_home().join("current");
    let target = fs::read_link(&link)
        .map(|path| path.to_string_lossy().to_string())
        .unwrap_or_else(|_| "(no current pointer)".to_string());
    lines.push(format!(
        "    installed pointer: {} -> {}",
        link.display(),
        target
    ));
    lines.push(
        "Install Ren from a checkout (`pip install -e <checkout>`, README \
         Quickstart), point $REN_ENGINE_ROOT at a built engine tree \
         (`python3 -m ren.package_engine --dest <dir>`), or install a \
         versioned Ren (which points <vep_home>/current at its tree)."
            .to_string(),
    );
    lines.join("\n")
}

/// `PYTHONPATH` with the engine root prepended, as the bash front door and
/// `bin/vep` set it: the tree this binary runs from first, the caller's own
/// path after it.
pub fn engine_pythonpath(root: &Path) -> Option<OsString> {
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
    fn the_tree_holding_the_binary_is_the_engine_root() {
        let exe = Path::new("/tmp/some-engine/bin/ren");
        assert_eq!(
            engine_root_from(exe),
            Some(PathBuf::from("/tmp/some-engine"))
        );
    }

    #[test]
    fn a_binary_at_the_root_has_no_engine_root() {
        assert_eq!(engine_root_from(Path::new("/ren")), None);
    }

    #[test]
    fn an_explicit_engine_root_that_is_no_engine_resolves_to_nothing() {
        let _guard = env_lock();
        env::set_var(ENGINE_ENV, "/tmp/not-an-engine");
        assert_eq!(explicit_engine_root(), None);
        env::remove_var(ENGINE_ENV);
    }

    #[test]
    fn an_empty_engine_root_counts_as_unset() {
        let _guard = env_lock();
        env::set_var(ENGINE_ENV, "");
        assert_eq!(explicit_engine_root(), None);
        env::remove_var(ENGINE_ENV);
    }

    /// Process environment is shared, so the tests that set a variable run
    /// one at a time.
    fn env_lock() -> std::sync::MutexGuard<'static, ()> {
        static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
        LOCK.lock().unwrap()
    }
}
