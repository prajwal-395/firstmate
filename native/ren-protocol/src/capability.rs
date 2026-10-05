//! The capability model: what Ren can do, keyed by capability id.
//!
//! Modeled from `library/tools/capabilities.py` (CapabilitySpec, Executor,
//! LegacyNode) and `library/tools/operations.py` (ExecutionPhase,
//! ExecutionPolicy, PatchSemantics). The capability id is the identity;
//! the DAG node is legacy metadata.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::resource::{ResourceDemand, ResourceName};

/// A capability's stable id (`speech.enrich`, `spine.mesh`). The registry
/// refuses an empty id, one with spaces, or a duplicate
/// (`capabilities.problems`).
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize, JsonSchema)]
pub struct CapabilityId(pub String);

/// What runs when the capability is executed.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum ExecutorKind {
    /// A Python function in the owning step's body.
    Function,
    /// A prompt the runner asks a model for; `attr` is empty.
    Prompt,
}

/// The executor: a repo-relative file under `library/steps/` and the
/// function's name (empty for a prompt).
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Executor {
    pub kind: ExecutorKind,
    pub path: String,
    pub attr: String,
}

/// The DAG node a capability is attributed to, kept for compatibility.
/// The step ledgers, review gates and provenance are still keyed by it.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct LegacyNode {
    pub node_id: String,
    pub process: String,
}

/// The scope kinds an operation runs against (`scope.KINDS`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum ScopeKind {
    Project,
    Clip,
    Region,
    Reel,
}

/// A phase's Resolve access mode (`operations.RESOLVE_MODES`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum ResolveMode {
    #[default]
    None,
    Shared,
    Exclusive,
}

/// A phase's locality (`operations.LOCALITIES`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum Locality {
    #[default]
    None,
    Project,
    Timeline,
}

/// A phase's freshness requirement (`operations.FRESHNESS_REQUIREMENTS`).
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Freshness {
    #[default]
    None,
    TimelineGeneration,
}

/// One schedulable phase of a capability's execution contract.
///
/// `resources` are maximum amounts, clamped to the machine's capacity by
/// the scheduler. An exclusive Resolve phase also reserves the
/// scheduler's `resolve_cursor` resource, so the two schedulers share one
/// declaration.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ExecutionPhase {
    pub name: String,
    #[serde(default)]
    pub resolve_mode: ResolveMode,
    #[serde(default)]
    pub locality: Locality,
    #[serde(default)]
    pub freshness: Freshness,
    #[serde(default)]
    pub resources: Vec<(ResourceName, u64)>,
    #[serde(default)]
    pub lock_sites: Vec<String>,
    #[serde(default)]
    pub entry_points: Vec<String>,
    #[serde(default)]
    pub why: String,
    #[serde(default)]
    pub resource_basis: String,
}

/// The EditPatch operations and composition a capability permits.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct PatchSemantics {
    pub operations: Vec<String>,
    pub conflict_domains: Vec<String>,
    pub temporal_effect: String,
    pub merge_semantics: String,
}

/// Resource, Resolve, locality, freshness and patch policy for a
/// capability. The sole execution-policy declaration: routing and
/// scheduler profiles are computed from it.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ExecutionPolicy {
    #[serde(default)]
    pub phases: Vec<ExecutionPhase>,
    #[serde(default)]
    pub patch: Option<PatchSemantics>,
}

/// The cost class (`capabilities.COST_CLASSES`), decided by the module's
/// cost rule: HEAVY holds the machine-wide heavy-work lock, MODEL needs a
/// host model's answer, LIGHT is local Python on files and state.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum CostClass {
    Heavy,
    Model,
    #[default]
    Light,
}

/// One capability: the derived view of one registry entry.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct CapabilitySpec {
    pub id: CapabilityId,
    pub summary: String,
    #[serde(default)]
    pub scopes: Vec<ScopeKind>,
    pub executor: Executor,
    #[serde(default)]
    pub requires: Vec<String>,
    #[serde(default)]
    pub effects: Vec<String>,
    #[serde(default)]
    pub produces: Vec<String>,
    #[serde(default)]
    pub consumes: Vec<String>,
    #[serde(default)]
    pub assumes_machine: Vec<String>,
    #[serde(default)]
    pub assumes_outside: Vec<String>,
    #[serde(default)]
    pub needs_model_answer: bool,
    #[serde(default)]
    pub caller_supplied: bool,
    #[serde(default)]
    pub execution: ExecutionPolicy,
    #[serde(default)]
    pub artifact_areas: Vec<String>,
    #[serde(default)]
    pub legacy: Option<LegacyNode>,
    #[serde(default)]
    pub cost_class: CostClass,
    #[serde(default)]
    pub cost_basis: String,
    #[serde(default)]
    pub creative_policy: Option<String>,
}

impl CapabilitySpec {
    /// The capability's EditPatch declaration, when it authors one
    /// (`CapabilitySpec.patch_semantics`).
    pub fn patch_semantics(&self) -> Option<&PatchSemantics> {
        self.execution.patch.as_ref()
    }

    /// The machine demand of one phase, with an exclusive Resolve mode
    /// reserving `resolve_cursor` (`resource_scheduler._capability_demand`).
    pub fn phase_demand(&self, phase_name: &str) -> Option<ResourceDemand> {
        let phase = self.execution.phases.iter().find(|p| p.name == phase_name)?;
        let mut entries: Vec<(ResourceName, u64)> = phase.resources.clone();
        if phase.resolve_mode == ResolveMode::Exclusive {
            entries.push((ResourceName::ResolveCursor, 1));
        }
        Some(ResourceDemand(entries.into_iter().collect()))
    }
}
