//! The recorded timelines on this machine, read the way
//! `library/tools/timeline_shadow.py` reads them.
//!
//! The readiness check answers "which Resolve project is this, and what
//! has Ren read out of it?" from what is ON THE RECORD: the shadow
//! store holds every generation Ren has read, and the newest generation
//! of each timeline is its head. A live probe would put a hanging
//! Resolve on the front door's critical path, and a check that hangs
//! is not a check.
//!
//! The store is a SQLite file (`$REN_SHADOW_DB`, else
//! `$XDG_STATE_HOME/ren/timeline_shadow.sqlite3`). A store that is not
//! there has no recorded timelines, which is a fact to report and not a
//! failure: the Python opens a store that does not exist by CREATING
//! it, and an empty one answers the same way.

use std::collections::BTreeSet;
use std::path::PathBuf;

use rusqlite::Connection;

/// The shadow store's schema version this code writes.
pub const SHADOW_SCHEMA_VERSION: i64 = 2;

/// Where the shadow store lives, the way `timeline_shadow` places it.
pub fn default_db_path() -> PathBuf {
    if let Some(explicit) = std::env::var_os("REN_SHADOW_DB") {
        if !explicit.is_empty() {
            return PathBuf::from(explicit);
        }
    }
    let base = match std::env::var_os("XDG_STATE_HOME") {
        Some(value) if !value.is_empty() => PathBuf::from(value),
        _ => crate::engine::home_dir()
            .join(".local")
            .join("state"),
    };
    base.join("ren").join("timeline_shadow.sqlite3")
}

/// The recorded timeline names of one Resolve project, sorted.
///
/// The names are the ones the newest generation of each timeline
/// carries: a rename answers under the new name, because the head is
/// the newest generation.
pub fn recorded_timeline_names(project: &str) -> Result<Vec<String>, String> {
    let path = default_db_path();
    if !path.exists() {
        // A store that is not there has recorded nothing.
        return Ok(vec![]);
    }
    let connection = match Connection::open_with_flags(
        &path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
            | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) {
        Ok(opened) => opened,
        // A file that cannot be opened is a report, never a refusal:
        // the check states what it could not read.
        Err(error) => {
            return Err(format!("{error}"));
        }
    };
    let version = schema_version(&connection)?;
    if version > SHADOW_SCHEMA_VERSION {
        return Err(format!(
            "stale shadow worker: the shadow DB at {path} is schema v{version}, \
             but this worker's code writes v{SHADOW_SCHEMA_VERSION} and predates it. \
             Restart the Resolve broker from the current tree so the worker runs \
             current code; the DB is left untouched and no write was attempted - \
             do not delete the DB.",
            path = path.display(),
        ));
    }
    let mut statement = connection.prepare(
        "SELECT g.timeline_name, s.body FROM generations g JOIN snapshots s \
         ON s.hash=g.hash WHERE g.project=?1 AND g.generation=( \
         SELECT MAX(h.generation) FROM generations h \
         WHERE h.project=g.project AND h.timeline_id=g.timeline_id) \
         ORDER BY g.project, g.timeline_name, g.timeline_id",
    ).map_err(|error| format!("{error}"))?;
    let mut names: BTreeSet<String> = BTreeSet::new();
    let mut rows = statement
        .query([project])
        .map_err(|error| format!("{error}"))?;
    while let Some(row) = rows
        .next()
        .map_err(|error| format!("{error}"))?
    {
        let name: String = row.get(0).unwrap_or_default();
        let body: String = row.get(1).unwrap_or_default();
        // The Python reads the snapshot body as it walks the heads, so a
        // body that is not JSON is a store it cannot read.
        if serde_json::from_str::<serde_json::Value>(&body).is_err() {
            return Err(
                "a recorded snapshot is not readable JSON".to_string(),
            );
        }
        names.insert(name);
    }
    Ok(names.into_iter().collect())
}

/// The store's schema version: the stamped one, or the column floor.
fn schema_version(connection: &Connection) -> Result<i64, String> {
    let stamped: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .unwrap_or(0);
    let mut statement = match connection.prepare("PRAGMA table_info(generations)") {
        Ok(prepared) => prepared,
        Err(_) => return Ok(stamped),
    };
    let columns = match statement.query_map([], |row| row.get::<_, String>(1)) {
        Ok(rows) => rows.filter_map(|row| row.ok()).collect::<Vec<String>>(),
        Err(_) => return Ok(stamped),
    };
    if columns.is_empty() {
        return Ok(stamped);
    }
    let floor = if ["model_revision", "nle_binding_ids"]
        .iter()
        .all(|name| columns.iter().any(|column| column == name))
    {
        2
    } else {
        1
    };
    Ok(stamped.max(floor))
}
