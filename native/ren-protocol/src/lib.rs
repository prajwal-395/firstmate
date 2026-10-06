//! ren-protocol: Ren's typed protocol.
//!
//! The capability, scope, edit, receipt, resource and verification types
//! every worker speaks, modeled from what the Python code does today
//! (see each module for its source). Serde is the wire format; the JSON
//! Schemas in `schemas/` are generated from these same definitions, so
//! the internal type and the external agent/worker contract share one
//! source of truth.
//!
//! Boring Rust over clever Rust: concrete structs and enums, no async
//! trait hierarchies, explicit ownership.

pub mod capability;
pub mod edit;
pub mod ids;
pub mod job;
pub mod ml_worker;
pub mod receipt;
pub mod resource;
pub mod scope;
pub mod verification;

pub mod schemas;

pub use capability::{
    CapabilityId, CapabilitySpec, CostClass, ExecutionPhase, ExecutionPolicy, Executor,
    ExecutorKind, Freshness, LegacyNode, Locality, PatchSemantics, ResolveMode, ScopeKind,
};
pub use edit::{Condition, EditOp, EditPatch, Inverse, Operation};
pub use ids::{ClipId, Generation, SnapshotHash, TimelineId};
pub use job::{JobKind, JobMode, JobPriority, JobShape, JobSubmission};
pub use ml_worker::{
    MlWorkerCancel, MlWorkerError, MlWorkerHealth, MlWorkerHealthReport, MlWorkerMessage,
    MlWorkerProgress, MlWorkerRefusal, MlWorkerRequest, MlWorkerResponse, MlWorkerStatus,
};
pub use receipt::{
    GenerationRecord, JudgedOperation, Receipt, ReceiptStatus, Refusal, StoppedOperation,
};
pub use resource::{ResourceDemand, ResourceName};
pub use scope::{Region, Scope, ScopeError};
pub use verification::VerificationFinding;
