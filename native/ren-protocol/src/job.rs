//! The broker job model.
//!
//! Modeled from `library/tools/resolved/jobs.py` (the job kinds and
//! `prepare`'s schedulable shape) and `library/tools/resolved/scheduler.py`
//! (the priority bands and modes).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The job kinds the broker accepts (`jobs.KINDS`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub enum JobKind {
    /// A grant: the CALLER runs its own critical section. Never executed
    /// by the broker.
    #[serde(rename = "lease")]
    Lease,
    /// Observe one named timeline into the shadow store. Exclusive
    /// (the timeline must be current to read true); identical snapshots
    /// coalesce.
    #[serde(rename = "timeline.snapshot")]
    TimelineSnapshot,
    /// Commit one prepared EditPatch. Exclusive. Two submissions of one
    /// patch id coalesce.
    #[serde(rename = "timeline.apply_patch")]
    TimelineApplyPatch,
    /// One `resolve-axi` command line, run inside the broker so its reads
    /// coalesce across agents. Reads only, and only the named allowlist.
    #[serde(rename = "resolve_axi")]
    ResolveAxi,
    /// Read the installed OFX/ResolveFX effects and the running Resolve
    /// version, on the broker's own connection. Ren-owned and read-only:
    /// the script is fixed in Ren, never caller-supplied.
    #[serde(rename = "fusion.registry_summary")]
    FusionRegistrySummary,
}

impl JobKind {
    /// The kind's wire name.
    pub fn as_str(&self) -> &'static str {
        match self {
            JobKind::Lease => "lease",
            JobKind::TimelineSnapshot => "timeline.snapshot",
            JobKind::TimelineApplyPatch => "timeline.apply_patch",
            JobKind::ResolveAxi => "resolve_axi",
            JobKind::FusionRegistrySummary => "fusion.registry_summary",
        }
    }
}

/// The scheduler's modes (`scheduler.EXCLUSIVE`/`SHARED`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum JobMode {
    Exclusive,
    Shared,
}

/// The priority bands, most urgent first (`scheduler.PRIORITIES`): a
/// person waiting outranks a read, a read outranks a write, a write
/// outranks a render.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub enum JobPriority {
    #[serde(rename = "interactive")]
    Interactive,
    #[serde(rename = "read")]
    Read,
    #[serde(rename = "mutation")]
    Mutation,
    #[serde(rename = "build")]
    Build,
    #[serde(rename = "qa_render")]
    QaRender,
    #[serde(rename = "qualification")]
    Qualification,
    #[serde(rename = "export")]
    Export,
}

/// One job submission, as a client sends it to the broker's socket
/// (`server.py` protocol).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct JobSubmission {
    pub op: String,
    pub kind: JobKind,
    pub params: Value,
    #[serde(default)]
    pub owner: String,
    #[serde(default)]
    pub qualification: bool,
}

/// The schedulable fields of one submission (`jobs.prepare`'s shape):
/// what the queue and the scheduler need to know.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct JobShape {
    pub mode: JobMode,
    pub priority: JobPriority,
    pub executed: bool,
    #[serde(default)]
    pub project: String,
    #[serde(default)]
    pub timeline: String,
    #[serde(default)]
    pub locality: String,
    #[serde(default)]
    pub coalesce_key: Option<String>,
    #[serde(default)]
    pub qualification: bool,
    /// The job's DECLARED budget: the seconds it may hold the one
    /// Resolve before the broker stops it. Absent means the kind's
    /// own budget (`library/tools/resolved/job_budget.py`).
    #[serde(default)]
    pub budget_seconds: Option<f64>,
}
