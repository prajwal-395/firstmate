//! JSON Schema generation from the same definitions the Rust types use.
//!
//! One schema per protocol type, written to `schemas/` by
//! `cargo run --bin emit-schemas`. `--check` fails when a committed
//! schema is stale, so a type change cannot land without its schema
//! landing beside it.

use schemars::{schema_for, Schema};
use serde::Serialize;
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

use crate::capability::{
    CapabilityId, CapabilitySpec, CostClass, ExecutionPhase, ExecutionPolicy, Executor,
    ExecutorKind, Freshness, LegacyNode, Locality, PatchSemantics, ResolveMode, ScopeKind,
};
use crate::edit::{Condition, EditOp, EditPatch, Inverse, Operation};
use crate::ids::{ClipId, Generation, SnapshotHash, TimelineId};
use crate::job::{JobKind, JobMode, JobPriority, JobShape, JobSubmission};
use crate::ml_worker::{
    MlWorkerCancel, MlWorkerError, MlWorkerHealth, MlWorkerHealthReport, MlWorkerMessage,
    MlWorkerProgress, MlWorkerRefusal, MlWorkerRequest, MlWorkerResponse, MlWorkerStatus,
};
use crate::project_model::*;
use crate::receipt::{
    GenerationRecord, JudgedOperation, Receipt, ReceiptStatus, Refusal, StoppedOperation,
};
use crate::resource::{ResourceDemand, ResourceName};
use crate::scope::{Region, Scope};
use crate::verification::VerificationFinding;

/// One named schema: the file stem and the schema itself.
pub struct NamedSchema {
    pub name: &'static str,
    pub title: &'static str,
    pub schema: Schema,
}

fn named(name: &'static str, title: &'static str, schema: Schema) -> NamedSchema {
    NamedSchema { name, title, schema }
}

/// Every protocol type's schema, in one enumeration. The list IS the
/// contract: a type without a row here has no schema, and a schema
/// without a type is a file nothing reads.
pub fn all_schemas() -> Vec<NamedSchema> {
    vec![
        named("capability-id", "CapabilityId", schema_for!(CapabilityId)),
        named("capability-spec", "CapabilitySpec", schema_for!(CapabilitySpec)),
        named("executor", "Executor", schema_for!(Executor)),
        named("executor-kind", "ExecutorKind", schema_for!(ExecutorKind)),
        named("legacy-node", "LegacyNode", schema_for!(LegacyNode)),
        named("scope-kind", "ScopeKind", schema_for!(ScopeKind)),
        named("resolve-mode", "ResolveMode", schema_for!(ResolveMode)),
        named("locality", "Locality", schema_for!(Locality)),
        named("freshness", "Freshness", schema_for!(Freshness)),
        named("execution-phase", "ExecutionPhase", schema_for!(ExecutionPhase)),
        named("patch-semantics", "PatchSemantics", schema_for!(PatchSemantics)),
        named("execution-policy", "ExecutionPolicy", schema_for!(ExecutionPolicy)),
        named("cost-class", "CostClass", schema_for!(CostClass)),
        named("scope", "Scope", schema_for!(Scope)),
        named("region", "Region", schema_for!(Region)),
        named("clip-id", "ClipId", schema_for!(ClipId)),
        named("timeline-id", "TimelineId", schema_for!(TimelineId)),
        named("generation", "Generation", schema_for!(Generation)),
        named("snapshot-hash", "SnapshotHash", schema_for!(SnapshotHash)),
        named("edit-op", "EditOp", schema_for!(EditOp)),
        named("inverse", "Inverse", schema_for!(Inverse)),
        named("operation", "Operation", schema_for!(Operation)),
        named("condition", "Condition", schema_for!(Condition)),
        named("edit-patch", "EditPatch", schema_for!(EditPatch)),
        named("receipt", "Receipt", schema_for!(Receipt)),
        named("receipt-status", "ReceiptStatus", schema_for!(ReceiptStatus)),
        named(
            "judged-operation",
            "JudgedOperation",
            schema_for!(JudgedOperation),
        ),
        named(
            "stopped-operation",
            "StoppedOperation",
            schema_for!(StoppedOperation),
        ),
        named("refusal", "Refusal", schema_for!(Refusal)),
        named("generation-record", "GenerationRecord", schema_for!(GenerationRecord)),
        named("resource-name", "ResourceName", schema_for!(ResourceName)),
        named("resource-demand", "ResourceDemand", schema_for!(ResourceDemand)),
        named(
            "verification-finding",
            "VerificationFinding",
            schema_for!(VerificationFinding),
        ),
        named("job-kind", "JobKind", schema_for!(JobKind)),
        named("job-mode", "JobMode", schema_for!(JobMode)),
        named("job-priority", "JobPriority", schema_for!(JobPriority)),
        named("job-submission", "JobSubmission", schema_for!(JobSubmission)),
        named("job-shape", "JobShape", schema_for!(JobShape)),
        named("ml-worker-message", "MlWorkerMessage", schema_for!(MlWorkerMessage)),
        named("ml-worker-request", "MlWorkerRequest", schema_for!(MlWorkerRequest)),
        named("ml-worker-response", "MlWorkerResponse", schema_for!(MlWorkerResponse)),
        named("ml-worker-status", "MlWorkerStatus", schema_for!(MlWorkerStatus)),
        named("ml-worker-refusal", "MlWorkerRefusal", schema_for!(MlWorkerRefusal)),
        named("ml-worker-error", "MlWorkerError", schema_for!(MlWorkerError)),
        named("ml-worker-progress", "MlWorkerProgress", schema_for!(MlWorkerProgress)),
        named("ml-worker-cancel", "MlWorkerCancel", schema_for!(MlWorkerCancel)),
        named("ml-worker-health", "MlWorkerHealth", schema_for!(MlWorkerHealth)),
        named(
            "ml-worker-health-report",
            "MlWorkerHealthReport",
            schema_for!(MlWorkerHealthReport),
        ),
        named("project-model", "ProjectModel", schema_for!(ProjectModel)),
        named("project-id", "ProjectId", schema_for!(ProjectId)),
        named("project-clock-id", "ClockId", schema_for!(ClockId)),
        named("project-time-domain", "TimeDomain", schema_for!(TimeDomain)),
        named("project-source-stream", "SourceStream", schema_for!(SourceStream)),
        named("project-asset", "Asset", schema_for!(Asset)),
        named("project-clock", "Clock", schema_for!(Clock)),
        named("project-interval", "Interval", schema_for!(Interval)),
        named("project-observation", "Observation", schema_for!(Observation)),
        named("project-entity", "Entity", schema_for!(Entity)),
        named("project-identity-link", "IdentityLink", schema_for!(IdentityLink)),
        named("project-mask", "Mask", schema_for!(Mask)),
        named("project-object-class", "ObjectClass", schema_for!(ObjectClass)),
        named("project-track-sample", "TrackSample", schema_for!(TrackSample)),
        named("project-object-track", "ObjectTrack", schema_for!(ObjectTrack)),
        named("project-relation", "Relation", schema_for!(Relation)),
        named("project-clip-use", "ClipUse", schema_for!(ClipUse)),
        named("project-editorial-intent", "EditorialIntent", schema_for!(EditorialIntent)),
        named("project-edit-operation", "EditOperation", schema_for!(EditOperation)),
        named("project-effect", "Effect", schema_for!(Effect)),
        named("project-keyframe", "Keyframe", schema_for!(Keyframe)),
        named("project-render", "Render", schema_for!(Render)),
        named("project-frame-map", "FrameMap", schema_for!(FrameMap)),
        named("project-frame-map-segment", "FrameMapSegment", schema_for!(FrameMapSegment)),
        named("project-nle-binding", "NleBinding", schema_for!(NleBinding)),
        named("project-render-receipt", "RenderReceipt", schema_for!(RenderReceipt)),
        named("project-provenance", "Provenance", schema_for!(Provenance)),
    ]
}

/// The schemas as a single JSON object keyed by file stem, the form the
/// Python conformance helper loads.
pub fn schema_map() -> BTreeMap<String, serde_json::Value> {
    let mut out = BTreeMap::new();
    for entry in all_schemas() {
        out.insert(entry.name.to_string(), serde_json::to_value(&entry.schema).unwrap());
    }
    out
}

fn render(schema: &Schema) -> String {
    serde_json::to_string_pretty(schema).unwrap()
}

/// Write every schema to `dir` as `<name>.schema.json`. Returns the
/// number of files written.
pub fn write_schemas(dir: &Path) -> Result<usize, String> {
    fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    let mut written = 0;
    for entry in all_schemas() {
        let path = dir.join(format!("{}.schema.json", entry.name));
        let body = render(&entry.schema);
        fs::write(&path, body).map_err(|e| format!("cannot write {}: {e}", path.display()))?;
        written += 1;
    }
    Ok(written)
}

/// Fail when a committed schema is stale: regenerate and compare. A
/// type change without its schema landing beside it is a finding, not
/// a silent drift.
pub fn check_schemas(dir: &Path) -> Result<(), String> {
    let mut problems = Vec::new();
    for entry in all_schemas() {
        let path = dir.join(format!("{}.schema.json", entry.name));
        let committed = match fs::read_to_string(&path) {
            Ok(text) => text,
            Err(e) => {
                problems.push(format!("{} is missing: {e}", path.display()));
                continue;
            }
        };
        let fresh = render(&entry.schema);
        if committed != fresh {
            problems.push(format!(
                "{} is stale: the type changed and the schema did not\n\
                 re-run `cargo run --bin emit-schemas`",
                path.display()
            ));
        }
    }
    if problems.is_empty() {
        Ok(())
    } else {
        Err(problems.join("\n"))
    }
}

/// Serialize a schema map (used by tests and the Python conformance
/// helper's fixture).
pub fn schema_map_json() -> String {
    serde_json::to_string_pretty(&schema_map()).unwrap()
}

/// A serializable view of the schema map for downstream consumers.
pub fn schema_map_value() -> serde_json::Value {
    serde_json::to_value(schema_map()).unwrap()
}

/// Keep the unused-import warning off `Serialize` when the bin is the
/// only consumer of the pretty printer.
#[allow(dead_code)]
fn _assert_serialize<T: Serialize>() {}
