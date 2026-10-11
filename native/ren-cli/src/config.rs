//! The per-user config file and the checkout's `.env`, loaded the way
//! `library.tools.paths` loads them - the native verbs read the same
//! `PIPELINE_PROJECTS_ROOT` / `PIPELINE_SFX_LIBRARY` / `PIPELINE_MUSIC_LIBRARY`
//! the Python does, so a command's output cannot drift between the two.
//!
//! Three layers, first one to name a key wins: the process environment,
//! the per-user config file, the checkout's `.env`. Then the code default
//! at each `get` below. Both files use the same `KEY=value` format, parsed
//! with no dependency, because this runs where neither python-dotenv nor
//! tomllib can be assumed.

use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};

use crate::engine;

/// Names the per-user config file outright, for a test or a second user.
const USER_CONFIG_ENV: &str = "REN_CONFIG";

/// Where the per-user config lives: `$REN_CONFIG`, else
/// `$XDG_CONFIG_HOME/ren/config.env`, else `~/.config/ren/config.env`.
fn user_config_path() -> PathBuf {
    if let Some(explicit) = env::var_os(USER_CONFIG_ENV) {
        return engine::expanduser(&PathBuf::from(explicit));
    }
    let base = match env::var_os("XDG_CONFIG_HOME") {
        Some(xdg) => engine::expanduser(&PathBuf::from(xdg)),
        None => engine::home_dir().join(".config"),
    };
    engine::expanduser(&base).join("ren").join("config.env")
}

/// `KEY=value` pairs from a `.env`-format file; empty when there is no file.
///
/// Comments, blank lines and lines without `=` are skipped; surrounding
/// quotes are stripped; `$HOME` and `~` in values are expanded.
fn read_env_file(env_path: &Path) -> HashMap<String, String> {
    let mut values = HashMap::new();
    let Ok(text) = fs::read_to_string(env_path) else {
        return values;
    };
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        let key = key.trim().to_string();
        let mut value = value.trim().to_string();
        if value.len() >= 2 {
            let first = value.chars().next();
            let last = value.chars().last();
            if first == last && matches!(first, Some('"') | Some('\'')) {
                value = value[1..value.len() - 1].to_string();
            }
        }
        value = crate::project::expand_portable_pub(&value, Path::new(""), &engine_root_or_cwd());
        values.insert(key, value);
    }
    values
}

fn engine_root_or_cwd() -> PathBuf {
    engine::find_engine_root().unwrap_or_else(|| PathBuf::from("."))
}

/// The checkout's `.env`, beside the engine root.
fn checkout_env_path(engine_root: &Path) -> PathBuf {
    engine_root.join(".env")
}

/// The value of `key`: the process environment, then the per-user config,
/// then the checkout's `.env`, then `default`. The one precedence
/// `library.tools.paths` loads its paths with.
pub fn get(key: &str, default: &str, engine_root: &Path) -> String {
    if let Some(value) = env::var_os(key) {
        if !value.is_empty() {
            return value.to_string_lossy().to_string();
        }
    }
    let user_values = read_env_file(&user_config_path());
    if let Some(value) = user_values.get(key) {
        return value.clone();
    }
    let checkout_values = read_env_file(&checkout_env_path(engine_root));
    if let Some(value) = checkout_values.get(key) {
        return value.clone();
    }
    default.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_config_file_yields_the_default() {
        let value = get(
            "PIPELINE_DEFINITELY_NOT_SET",
            "fallback",
            Path::new("/tmp/engine"),
        );
        assert_eq!(value, "fallback");
    }

    #[test]
    fn the_process_environment_wins_over_every_file() {
        let root = Path::new("/tmp/engine");
        env::set_var("PIPELINE_TEST_MARKER", "from-env");
        let value = get("PIPELINE_TEST_MARKER", "default", root);
        env::remove_var("PIPELINE_TEST_MARKER");
        assert_eq!(value, "from-env");
    }
}
