//! The broker's job table and receipts: SQLite in WAL mode.
//!
//! Byte-compatible with `library/tools/resolved/store.py`: the same schema,
//! the same file (`ren-resolved.sqlite3` beside the socket), the same
//! recovery on open (a job left `queued` or `running` by a daemon that died
//! is closed as `failed`), and the same receipt shape. The Python daemon
//! and the Rust daemon can therefore read and write the same table - a
//! client that survives a daemon switch sees one continuous job history.

use rusqlite::{params, Connection, OptionalExtension, Row};
use serde_json::Value;
use std::path::Path;
use std::sync::Mutex;

/// The terminal states (`store.TERMINAL`).
pub const TERMINAL: [&str; 4] = ["done", "failed", "rejected", "cancelled"];

const RECEIPT_COLUMNS: &str = "id, kind, priority, mode, executed, params, \
    project, timeline, qualification, coalesce_key, owner, description, \
    progress, state, subscribers, submitted, started, finished, result, error";

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    priority TEXT NOT NULL,
    mode TEXT NOT NULL,
    executed INTEGER NOT NULL,
    params TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    timeline TEXT NOT NULL DEFAULT '',
    qualification INTEGER NOT NULL DEFAULT 0,
    coalesce_key TEXT,
    interrupt_policy TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    progress TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    subscribers INTEGER NOT NULL DEFAULT 1,
    submitted REAL NOT NULL,
    started REAL,
    finished REAL,
    result TEXT,
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE TABLE IF NOT EXISTS idempotency_records (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    digest TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE,
    PRIMARY KEY (kind, key)
);
";

/// One job as its receipt: what was asked, what Resolve cost, outcome.
///
/// Field order is the wire order - the Python `receipt(row)` builds the
/// dict from the SQLite row in column order and appends the two measured
/// seconds, and a client parsing either daemon's reply must see the same
/// fields in the same order.
#[derive(Clone, Debug, serde::Serialize)]
pub struct Receipt {
    pub id: String,
    pub kind: String,
    pub priority: String,
    pub mode: String,
    pub executed: bool,
    pub params: Value,
    pub project: String,
    pub timeline: String,
    pub qualification: bool,
    pub coalesce_key: Option<String>,
    pub owner: String,
    pub description: String,
    pub progress: String,
    pub state: String,
    pub subscribers: i64,
    pub submitted: f64,
    pub started: Option<f64>,
    pub finished: Option<f64>,
    pub result: Option<Value>,
    pub error: String,
    pub wait_seconds: Option<f64>,
    pub hold_seconds: Option<f64>,
}

/// The schedulable fields of one running job, as `broker.running()` reports
/// them (`dict(vars(Pending))` in the Python daemon).
#[derive(Clone, Debug, serde::Serialize)]
pub struct RunningJob {
    pub id: String,
    pub priority: String,
    pub mode: String,
    pub submitted: f64,
    pub project: String,
    pub timeline: String,
    pub executed: bool,
    pub qualification: bool,
    pub locality: String,
}

pub struct JobStore {
    conn: Mutex<Connection>,
}

/// One job as the broker queues it (`server.Broker.submit`'s record).
pub struct JobRecord {
    pub id: String,
    pub kind: String,
    pub priority: String,
    pub mode: String,
    pub executed: bool,
    pub params: Value,
    pub project: String,
    pub timeline: String,
    pub qualification: bool,
    pub coalesce_key: Option<String>,
    pub interrupt_policy: String,
    pub owner: String,
    pub description: String,
    pub progress: String,
    pub state: String,
    pub subscribers: i64,
    pub submitted: f64,
}

impl JobStore {
    pub fn open(path: &Path) -> rusqlite::Result<Self> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL")?;
        conn.execute_batch(SCHEMA)?;
        let has_interrupt_policy = {
            let mut statement = conn.prepare("PRAGMA table_info(jobs)")?;
            let names: Vec<String> = statement
                .query_map([], |row| row.get::<_, String>(1))?
                .filter_map(Result::ok)
                .collect();
            names.iter().any(|name| name == "interrupt_policy")
        };
        if !has_interrupt_policy {
            conn.execute(
                "ALTER TABLE jobs ADD COLUMN interrupt_policy TEXT NOT NULL DEFAULT ''",
                [],
            )?;
        }
        for column in ["description", "progress"] {
            let mut statement = conn.prepare("PRAGMA table_info(jobs)")?;
            let columns = statement.query_map([], |row| row.get::<_, String>(1))?;
            if !columns.filter_map(Result::ok).any(|name| name == column) {
                conn.execute(
                    &format!("ALTER TABLE jobs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"),
                    [],
                )?;
            }
        }
        // A job left queued or running by a daemon that died is closed as
        // failed: its client lost its connection with that daemon, so
        // nobody is waiting on it any more.
        conn.execute(
            "UPDATE jobs SET state='failed', finished=?1, \
             error='the broker that held this job exited' \
             WHERE state IN ('queued', 'running')",
            params![now()],
        )?;
        Ok(JobStore {
            conn: Mutex::new(conn),
        })
    }

    fn insert_job(&self, job: &JobRecord) -> rusqlite::Result<()> {
        self.conn.lock().unwrap().execute(
            "INSERT INTO jobs (id, kind, priority, mode, executed, params, \
             project, timeline, qualification, coalesce_key, interrupt_policy, \
             owner, description, progress, state, subscribers, submitted) VALUES \
             (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17)",
            params![
                job.id,
                job.kind,
                job.priority,
                job.mode,
                job.executed as i64,
                canonical_json(&job.params),
                job.project,
                job.timeline,
                job.qualification as i64,
                job.coalesce_key,
                job.interrupt_policy,
                job.owner,
                job.description,
                job.progress,
                job.state,
                job.subscribers,
                job.submitted,
            ],
        )?;
        Ok(())
    }

    /// Insert one job record.
    pub fn insert(&self, job: &JobRecord) -> rusqlite::Result<()> {
        self.insert_job(job)
    }

    /// Insert a job and its durable idempotency key atomically.
    ///
    /// Returns the previous receipt for an identical retry, or None after
    /// inserting a new job. A key reused with different contents is a
    /// conflict - the caller refuses the submission.
    pub fn insert_idempotent(
        &self,
        job: &JobRecord,
        kind: &str,
        key: &str,
        digest: &str,
    ) -> rusqlite::Result<Option<Receipt>> {
        let conn = self.conn.lock().unwrap();
        let tx = conn.unchecked_transaction()?;
        let previous: Option<(String, String)> = tx
            .query_row(
                "SELECT digest, job_id FROM idempotency_records \
                 WHERE kind=?1 AND key=?2",
                params![kind, key],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        if let Some((previous_digest, job_id)) = previous {
            if previous_digest != digest {
                tx.rollback()?;
                return Err(conflict(format!(
                    "idempotency key {key:?} for {kind} was already used with \
                     different contents"
                )));
            }
            let found = tx
                .query_row(
                    &format!("SELECT {RECEIPT_COLUMNS} FROM jobs WHERE id=?1"),
                    params![job_id],
                    receipt_from_row,
                )
                .optional()?
                .ok_or_else(|| {
                    conflict(format!(
                        "idempotency record for {kind} {key:?} points to a \
                         missing job"
                    ))
                })?;
            tx.commit()?;
            return Ok(Some(found));
        }
        self.insert_job_with(&tx, job)?;
        tx.execute(
            "INSERT INTO idempotency_records (kind, key, digest, job_id) \
             VALUES (?1, ?2, ?3, ?4)",
            params![kind, key, digest, job.id],
        )?;
        tx.commit()?;
        Ok(None)
    }

    fn insert_job_with(&self, tx: &rusqlite::Transaction, job: &JobRecord) -> rusqlite::Result<()> {
        tx.execute(
            "INSERT INTO jobs (id, kind, priority, mode, executed, params, \
             project, timeline, qualification, coalesce_key, interrupt_policy, \
             owner, state, subscribers, submitted) VALUES \
             (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
            params![
                job.id,
                job.kind,
                job.priority,
                job.mode,
                job.executed as i64,
                canonical_json(&job.params),
                job.project,
                job.timeline,
                job.qualification as i64,
                job.coalesce_key,
                job.interrupt_policy,
                job.owner,
                job.state,
                job.subscribers,
                job.submitted,
            ],
        )?;
        Ok(())
    }

    /// Update one job's columns. Values are the DB-ready forms: strings for
    /// text columns, numbers for real columns, and a JSON string (not an
    /// object) for `result`.
    pub fn update(&self, job_id: &str, fields: &[(&str, Value)]) -> rusqlite::Result<()> {
        if fields.is_empty() {
            return Ok(());
        }
        let mut sql = String::from("UPDATE jobs SET ");
        for (index, (name, _)) in fields.iter().enumerate() {
            if index > 0 {
                sql.push_str(", ");
            }
            sql.push_str(name);
            sql.push_str("=?");
        }
        sql.push_str(" WHERE id=?");
        let mut values: Vec<DbValue> = fields.iter().map(|(_, v)| DbValue::from(v)).collect();
        values.push(DbValue::Text(job_id.to_string()));
        let binds: Vec<&dyn rusqlite::ToSql> =
            values.iter().map(|v| v as &dyn rusqlite::ToSql).collect();
        self.conn.lock().unwrap().execute(&sql, binds.as_slice())?;
        Ok(())
    }

    pub fn add_subscriber(&self, job_id: &str) -> rusqlite::Result<()> {
        self.conn.lock().unwrap().execute(
            "UPDATE jobs SET subscribers = subscribers + 1 WHERE id=?1",
            params![job_id],
        )?;
        Ok(())
    }

    pub fn get(&self, job_id: &str) -> Option<Receipt> {
        self.conn
            .lock()
            .unwrap()
            .query_row(
                &format!("SELECT {RECEIPT_COLUMNS} FROM jobs WHERE id=?1"),
                params![job_id],
                receipt_from_row,
            )
            .optional()
            .ok()
            .flatten()
    }

    pub fn interrupt_policy(&self, job_id: &str) -> Option<String> {
        self.conn
            .lock()
            .unwrap()
            .query_row(
                "SELECT interrupt_policy FROM jobs WHERE id=?1",
                params![job_id],
                |row| row.get(0),
            )
            .optional()
            .ok()
            .flatten()
    }

    pub fn recent(&self, limit: i64) -> Vec<Receipt> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = match conn.prepare(&format!(
            "SELECT {RECEIPT_COLUMNS} FROM jobs ORDER BY submitted DESC LIMIT ?1"
        )) {
            Ok(stmt) => stmt,
            Err(_) => return Vec::new(),
        };
        let rows = match stmt.query_map(params![limit], receipt_from_row) {
            Ok(rows) => rows,
            Err(_) => return Vec::new(),
        };
        rows.filter_map(|r| r.ok()).collect()
    }
}

fn receipt_from_row(row: &Row) -> rusqlite::Result<Receipt> {
    let params: String = row.get(5)?;
    let result: Option<String> = row.get(18)?;
    let started: Option<f64> = row.get(16)?;
    let finished: Option<f64> = row.get(17)?;
    let submitted: f64 = row.get(15)?;
    Ok(Receipt {
        id: row.get(0)?,
        kind: row.get(1)?,
        priority: row.get(2)?,
        mode: row.get(3)?,
        executed: row.get::<_, i64>(4)? != 0,
        params: serde_json::from_str(&params).unwrap_or(Value::Null),
        project: row.get(6)?,
        timeline: row.get(7)?,
        qualification: row.get::<_, i64>(8)? != 0,
        coalesce_key: row.get(9)?,
        owner: row.get(10)?,
        description: row.get(11)?,
        progress: row.get(12)?,
        state: row.get(13)?,
        subscribers: row.get(14)?,
        submitted,
        started,
        finished,
        result: match result {
            Some(text) => serde_json::from_str(&text).ok(),
            None => None,
        },
        error: row.get(19)?,
        wait_seconds: started.map(|started| round3(started - submitted)),
        hold_seconds: match (started, finished) {
            (Some(started), Some(finished)) => Some(round3(finished - started)),
            _ => None,
        },
    })
}

fn conflict(message: String) -> rusqlite::Error {
    rusqlite::Error::SqliteFailure(rusqlite::ffi::Error::new(19), Some(message))
}

/// A DB-ready value for the store's `update`: the JSON values the broker
/// writes, converted to the column types. `result` arrives as a JSON
/// string and is stored as text; `state` and `error` are text;
/// `finished` and `started` are reals.
enum DbValue {
    Text(String),
    Real(f64),
    Integer(i64),
    Null,
}

impl From<&Value> for DbValue {
    fn from(value: &Value) -> Self {
        match value {
            Value::String(text) => DbValue::Text(text.clone()),
            Value::Number(number) => {
                if let Some(integer) = number.as_i64() {
                    DbValue::Integer(integer)
                } else {
                    DbValue::Real(number.as_f64().unwrap_or(0.0))
                }
            }
            Value::Bool(flag) => DbValue::Integer(if *flag { 1 } else { 0 }),
            _ => DbValue::Null,
        }
    }
}

impl rusqlite::ToSql for DbValue {
    fn to_sql(&self) -> rusqlite::Result<rusqlite::types::ToSqlOutput<'_>> {
        match self {
            DbValue::Text(text) => text.to_sql(),
            DbValue::Real(value) => value.to_sql(),
            DbValue::Integer(value) => value.to_sql(),
            DbValue::Null => Ok(rusqlite::types::ToSqlOutput::Owned(
                rusqlite::types::Value::Null,
            )),
        }
    }
}

fn round3(value: f64) -> f64 {
    (value * 1000.0).round() / 1000.0
}

/// JSON with object-key order fixed and whitespace removed - the digest and
/// stored form the Python daemon uses (`json.dumps(..., sort_keys=True,
/// separators=(",", ":"))`).
fn canonical_json(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "null".to_string())
}

pub fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn old_job_tables_migrate_receipt_fields() {
        let scratch = std::env::temp_dir().join(format!(
            "ren-resolved-store-migration-{}-{}.sqlite3",
            std::process::id(),
            now().to_bits(),
        ));
        {
            let conn = Connection::open(&scratch).unwrap();
            conn.execute_batch(
                "CREATE TABLE jobs (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    executed INTEGER NOT NULL,
                    params TEXT NOT NULL,
                    project TEXT NOT NULL DEFAULT '',
                    timeline TEXT NOT NULL DEFAULT '',
                    qualification INTEGER NOT NULL DEFAULT 0,
                    coalesce_key TEXT,
                    interrupt_policy TEXT NOT NULL DEFAULT '',
                    owner TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    subscribers INTEGER NOT NULL DEFAULT 1,
                    submitted REAL NOT NULL,
                    started REAL,
                    finished REAL,
                    result TEXT,
                    error TEXT NOT NULL DEFAULT ''
                );
                INSERT INTO jobs (
                    id, kind, priority, mode, executed, params, owner, state,
                    submitted
                ) VALUES (
                    'legacy', 'lease', 'read', 'shared', 0, '{}', '', 'done', 1.0
                );",
            )
            .unwrap();
        }

        let store = JobStore::open(&scratch).unwrap();
        let receipt = store.get("legacy").unwrap();
        assert_eq!(receipt.description, "");
        assert_eq!(receipt.progress, "");
        drop(store);

        let _ = std::fs::remove_file(&scratch);
        let _ = std::fs::remove_file(scratch.with_extension("sqlite3-wal"));
        let _ = std::fs::remove_file(scratch.with_extension("sqlite3-shm"));
    }

    #[test]
    fn idempotent_receipt_uses_named_columns_on_retry() {
        let scratch = std::env::temp_dir().join(format!(
            "ren-resolved-idempotent-retry-{}-{}.sqlite3",
            std::process::id(),
            now().to_bits(),
        ));
        // The saved-receipt decoder uses fixed positions. A wildcard select
        // couples those positions to physical schema order and reads the
        // REAL `finished` value as the TEXT `result` field on retry.
        let store = JobStore::open(&scratch).unwrap();
        let job = JobRecord {
            id: "job-legacy-idempotency".into(),
            kind: "semantic_edit".into(),
            priority: "normal".into(),
            mode: "exclusive".into(),
            executed: true,
            params: serde_json::json!({"request": "logo resize"}),
            project: "Ren Scratch - breaktest".into(),
            timeline: "Breaktest - Reel 13".into(),
            qualification: false,
            coalesce_key: None,
            interrupt_policy: "".into(),
            owner: "breaktest".into(),
            description: "logo resize".into(),
            progress: "applied".into(),
            state: "queued".into(),
            subscribers: 1,
            submitted: 1_700_000_000.0,
        };
        assert!(store
            .insert_idempotent(&job, "semantic_edit", "logo-1", "digest-1")
            .unwrap()
            .is_none());
        store
            .update(
                &job.id,
                &[
                    ("state", Value::String("done".into())),
                    ("finished", Value::from(1_700_000_001.25)),
                    ("result", Value::String("{\"verified\":true}".into())),
                ],
            )
            .unwrap();

        let previous = store
            .insert_idempotent(&job, "semantic_edit", "logo-1", "digest-1")
            .unwrap()
            .expect("identical retry should return the saved receipt");
        assert_eq!(previous.id, job.id);
        assert_eq!(previous.finished, Some(1_700_000_001.25));
        assert_eq!(previous.result, Some(serde_json::json!({"verified": true})));
        assert_eq!(previous.description, "");
        assert_eq!(previous.progress, "");

        drop(store);
        let _ = std::fs::remove_file(&scratch);
        let _ = std::fs::remove_file(scratch.with_extension("sqlite3-wal"));
        let _ = std::fs::remove_file(scratch.with_extension("sqlite3-shm"));
    }
}
