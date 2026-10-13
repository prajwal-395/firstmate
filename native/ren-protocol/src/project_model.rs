//! Ren's canonical, renderer and NLE independent project model contract.
//!
//! Version 1 is intentionally a data contract only. Persisted documents
//! with another version are rejected by `decode_project_model`; migrations
//! belong to the storage unit and must be explicit before they are accepted.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

macro_rules! id_type {
    ($($name:ident),+ $(,)?) => {$ (
        #[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize, JsonSchema)]
        #[serde(transparent)]
        pub struct $name(pub String);
    )+};
}

id_type!(
    ProjectId,
    AssetId,
    StreamId,
    ClockId,
    EntityId,
    ObservationId,
    IdentityLinkId,
    MaskId,
    ObjectClassId,
    TrackId,
    RelationId,
    IntentId,
    TimelineModelId,
    ClipUseId,
    EditOperationId,
    EffectId,
    RenderId,
    FrameMapId,
    NleBindingId,
    ReceiptId,
    ProvenanceId
);

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Rational {
    pub numerator: i64,
    pub denominator: u32,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum TimeDomain {
    Source {
        asset_id: AssetId,
        stream_id: Option<StreamId>,
    },
    Project,
    Timeline {
        timeline_id: TimelineModelId,
    },
    Render {
        render_id: RenderId,
    },
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Clock {
    pub id: ClockId,
    pub domain: TimeDomain,
    pub ticks_per_second: u64,
    pub frame_rate: Option<Rational>,
}

/// Half-open integer tick range [start, end); `start < end` is required by validation.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Interval {
    pub clock_id: ClockId,
    pub start: i64,
    pub end: i64,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AssetKind {
    Video,
    Audio,
    Image,
    Mask,
    Proxy,
    Render,
    Other,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Asset {
    pub id: AssetId,
    pub kind: AssetKind,
    pub uri: String,
    pub content_digest: Option<String>,
    pub metadata: BTreeMap<String, Value>,
    pub provenance: Vec<ProvenanceId>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SourceStream {
    pub id: StreamId,
    pub asset_id: AssetId,
    pub stream_index: u32,
    pub clock_id: ClockId,
    pub metadata: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EntityKind {
    Person,
    Object,
    Scene,
    Speaker,
    Camera,
    Other,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Entity {
    pub id: EntityId,
    pub kind: EntityKind,
    pub label: Option<String>,
    pub object_class_id: Option<ObjectClassId>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Provenance {
    pub id: ProvenanceId,
    pub producer: String,
    pub producer_version: Option<String>,
    pub source_digest: Option<String>,
    pub recorded_at: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Observation {
    pub id: ObservationId,
    pub asset_id: AssetId,
    pub interval: Interval,
    pub modality: String,
    pub payload: Value,
    pub confidence: Option<f64>,
    pub provenance_id: ProvenanceId,
    /// FI-03 evidence contract, additive: `None` for every observation
    /// migrated before the contract existed, so older documents still
    /// decode. `skip_serializing_if` keeps the round-trip byte-identical
    /// for contract-less payloads.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evidence: Option<EvidenceContract>,
}

/// FI-03: the four evidence types (plan A1). A raw instrument value
/// lives under `Measurement` and must never inhabit `Evaluation`.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceType {
    Measurement,
    Inference,
    Evaluation,
    CreativeJudgment,
}

/// FI-03 provenance + coverage semantics (plan C4 + A2 item 7).
/// `Unavailable` means unmeasured - the lane never looked. A measured
/// absence is `Complete` with zero samples instead.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CoverageKind {
    Complete,
    Sampled,
    Inferred,
    Unavailable,
}

/// FI-03: one versioned measurement record (plan A2) - asset, interval
/// on a named clock (the observation's own), target entity/region,
/// type/value/unit/method/sample-resolution, coverage and provenance.
/// Mirrors `library/tools/evidence_contract.py::to_rust_evidence`;
/// the Python key set is pinned by test against these serde names.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EvidenceContract {
    pub evidence_type: EvidenceType,
    pub measurement_type: String,
    pub value: Value,
    pub unit: String,
    pub method: String,
    pub sample_resolution: Option<[u32; 2]>,
    pub coverage: CoverageKind,
    pub coverage_samples: u64,
    pub coverage_sample_rate_hz: f64,
    pub target_entity_id: Option<EntityId>,
    pub target_region: Option<String>,
    pub uncertainty: Option<f64>,
    pub policy_id: Option<String>,
    pub basis_measurement_ids: Vec<String>,
    pub contract_version: u32,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum IdentityStatus {
    Candidate,
    Verified,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct IdentityLink {
    pub id: IdentityLinkId,
    pub entity_id: EntityId,
    pub observation_id: ObservationId,
    pub confidence: Option<f64>,
    pub method: String,
    pub status: IdentityStatus,
    pub provenance_id: ProvenanceId,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Mask {
    pub id: MaskId,
    pub entity_id: EntityId,
    pub interval: Interval,
    pub representation: Value,
    pub source_model: String,
    pub provenance_id: ProvenanceId,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ObjectClass {
    pub id: ObjectClassId,
    pub name: String,
    pub taxonomy: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TrackSample {
    pub tick: i64,
    pub geometry: Value,
    pub confidence: Option<f64>,
    pub coordinate_space: String,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ObjectTrack {
    pub id: TrackId,
    pub entity_id: EntityId,
    pub clock_id: ClockId,
    pub samples: Vec<TrackSample>,
    pub provenance_id: ProvenanceId,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Relation {
    pub id: RelationId,
    pub subject_id: EntityId,
    pub predicate: String,
    pub object_id: EntityId,
    pub interval: Interval,
    pub confidence: Option<f64>,
    pub provenance_id: ProvenanceId,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ClipUse {
    pub id: ClipUseId,
    pub asset_id: AssetId,
    pub source_interval: Interval,
    pub timeline_interval: Interval,
    pub entity_links: Vec<EntityId>,
    pub role: String,
    pub intent_id: Option<IntentId>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditorialIntent {
    pub id: IntentId,
    pub brief_ref: Option<String>,
    pub constraints: Vec<Value>,
    pub evidence_links: Vec<ObservationId>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EditOperation {
    pub id: EditOperationId,
    pub operation: String,
    pub target_ids: Vec<String>,
    pub before: Option<Value>,
    pub after: Option<Value>,
    pub intent_id: Option<IntentId>,
    pub evidence_ids: Vec<ObservationId>,
    pub author: String,
    pub parent_revision: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Keyframe {
    pub tick: i64,
    pub value: Value,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
/// Effect declaration and supported picture-transform parameter conventions.
/// `transform.position` uses `parameters.position: [x, y]` in sequence
/// pixels or timed `[x, y]` keyframes. `transform.scale` uses a uniform
/// positive multiplicative factor (`1.0` is unchanged) in
/// `parameters.scale` or numeric keyframes. Static
/// transforms cover the full target clip use; keyframed transforms may
/// vary within their interval.
pub struct Effect {
    pub id: EffectId,
    pub class_id: String,
    pub target_id: String,
    pub interval: Interval,
    pub parameters: BTreeMap<String, Value>,
    pub keyframes: Vec<Keyframe>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum MappingKind {
    OneToOne,
    Retimed,
    Freeze,
    Repeat,
    Generated,
    Gap,
    TransitionBlend,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct FrameMapSegment {
    pub from: Interval,
    pub to: Interval,
    pub mapping_kind: MappingKind,
    pub rate: Option<Rational>,
    pub source_clip_use: Option<ClipUseId>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct FrameMap {
    pub id: FrameMapId,
    pub from_clock: ClockId,
    pub to_clock: ClockId,
    pub segments: Vec<FrameMapSegment>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RenderReceipt {
    pub id: ReceiptId,
    pub status: String,
    pub renderer: String,
    pub input_revision: String,
    pub output_asset_id: AssetId,
    pub details: Value,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Render {
    pub id: RenderId,
    pub input_revision: String,
    pub output_asset_id: AssetId,
    pub settings: BTreeMap<String, Value>,
    pub receipt_id: ReceiptId,
    pub frame_map_id: FrameMapId,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct NleBinding {
    pub id: NleBindingId,
    pub timeline_id: TimelineModelId,
    pub system: String,
    pub project_identity: String,
    pub timeline_identity: String,
    pub generation: u64,
    pub clip_bindings: BTreeMap<ClipUseId, String>,
    pub external_bindings: BTreeMap<String, Value>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum TimebasePolicy {
    IntegerTicks,
    IntegerFrames,
    RationalFrames,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ProjectModel {
    pub id: ProjectId,
    pub schema_version: u32,
    pub timebase_policy: TimebasePolicy,
    pub clocks: Vec<Clock>,
    pub assets: Vec<Asset>,
    pub streams: Vec<SourceStream>,
    pub entities: Vec<Entity>,
    pub observations: Vec<Observation>,
    pub identity_links: Vec<IdentityLink>,
    pub masks: Vec<Mask>,
    pub object_classes: Vec<ObjectClass>,
    pub tracks: Vec<ObjectTrack>,
    pub relations: Vec<Relation>,
    pub intents: Vec<EditorialIntent>,
    pub clip_uses: Vec<ClipUse>,
    pub edits: Vec<EditOperation>,
    pub effects: Vec<Effect>,
    pub renders: Vec<Render>,
    pub frame_maps: Vec<FrameMap>,
    pub nle_bindings: Vec<NleBinding>,
    pub receipts: Vec<RenderReceipt>,
    pub provenance: Vec<Provenance>,
}

pub const PROJECT_MODEL_SCHEMA_VERSION: u32 = 1;
/// FI-03 evidence contract version. Observation documents keep schema
/// version 1: the contract rides the additive `evidence` field, and a
/// record stamped newer than this is refused by validation.
pub const EVIDENCE_CONTRACT_VERSION: u32 = 1;
#[derive(Debug, PartialEq, Eq)]
pub enum ProjectModelDecodeError {
    InvalidJson(String),
    MissingVersion,
    UnsupportedVersion(u64),
    InvalidModel(String),
}

/// Version mismatch is deliberately a hard error. A later storage layer must
/// register an explicit migration before older/newer versions can be read.
pub fn decode_project_model(json: &str) -> Result<ProjectModel, ProjectModelDecodeError> {
    let value: Value = serde_json::from_str(json)
        .map_err(|e| ProjectModelDecodeError::InvalidJson(e.to_string()))?;
    let version = value
        .get("schema_version")
        .and_then(Value::as_u64)
        .ok_or(ProjectModelDecodeError::MissingVersion)?;
    if version != u64::from(PROJECT_MODEL_SCHEMA_VERSION) {
        return Err(ProjectModelDecodeError::UnsupportedVersion(version));
    }
    let model: ProjectModel = serde_json::from_value(value)
        .map_err(|e| ProjectModelDecodeError::InvalidModel(e.to_string()))?;
    validate_project_model(&model).map_err(ProjectModelDecodeError::InvalidModel)?;
    Ok(model)
}

pub fn validate_project_model(model: &ProjectModel) -> Result<(), String> {
    if model.schema_version != PROJECT_MODEL_SCHEMA_VERSION {
        return Err(format!(
            "unsupported project model version {}",
            model.schema_version
        ));
    }
    let valid_interval = |interval: &Interval| interval.start < interval.end;
    for (name, valid) in [
        (
            "observation",
            model
                .observations
                .iter()
                .all(|x| valid_interval(&x.interval)),
        ),
        (
            "mask",
            model.masks.iter().all(|x| valid_interval(&x.interval)),
        ),
        (
            "relation",
            model.relations.iter().all(|x| valid_interval(&x.interval)),
        ),
        (
            "effect",
            model.effects.iter().all(|x| valid_interval(&x.interval)),
        ),
        (
            "clip use source",
            model
                .clip_uses
                .iter()
                .all(|x| valid_interval(&x.source_interval)),
        ),
        (
            "clip use timeline",
            model
                .clip_uses
                .iter()
                .all(|x| valid_interval(&x.timeline_interval)),
        ),
    ] {
        if !valid {
            return Err(format!(
                "{name} interval must be non-empty and half-open (start < end)"
            ));
        }
    }
    if model.clocks.iter().any(|clock| {
        clock.ticks_per_second == 0
            || clock
                .frame_rate
                .as_ref()
                .is_some_and(|rate| rate.denominator == 0)
    }) {
        return Err("clock rates must have positive denominators and ticks_per_second".into());
    }
    if model
        .observations
        .iter()
        .any(|x| x.confidence.is_some_and(|v| !(0.0..=1.0).contains(&v)))
        || model
            .identity_links
            .iter()
            .any(|x| x.confidence.is_some_and(|v| !(0.0..=1.0).contains(&v)))
        || model
            .tracks
            .iter()
            .flat_map(|x| &x.samples)
            .any(|x| x.confidence.is_some_and(|v| !(0.0..=1.0).contains(&v)))
        || model
            .relations
            .iter()
            .any(|x| x.confidence.is_some_and(|v| !(0.0..=1.0).contains(&v)))
    {
        return Err("confidence must be between 0 and 1".into());
    }
    for segment in model.frame_maps.iter().flat_map(|m| &m.segments) {
        if !valid_interval(&segment.from) || !valid_interval(&segment.to) {
            return Err("frame map intervals must be non-empty and half-open (start < end)".into());
        }
        if segment
            .rate
            .as_ref()
            .is_some_and(|rate| rate.denominator == 0)
        {
            return Err("frame map rate denominator must be positive".into());
        }
    }
    for evidence in model.observations.iter().filter_map(|x| x.evidence.as_ref()) {
        if evidence.contract_version != EVIDENCE_CONTRACT_VERSION {
            return Err(format!(
                "unsupported evidence contract version {}",
                evidence.contract_version
            ));
        }
        if evidence.unit.is_empty() || evidence.method.is_empty() {
            return Err("evidence must carry unit and method".into());
        }
        if evidence
            .uncertainty
            .is_some_and(|v| !(0.0..=1.0).contains(&v))
        {
            return Err("evidence uncertainty must be between 0 and 1".into());
        }
        match evidence.evidence_type {
            EvidenceType::Inference => {
                if evidence.uncertainty.is_none() {
                    return Err(
                        "an inference without uncertainty is not evidence".into(),
                    );
                }
            }
            EvidenceType::Evaluation => {
                if evidence.policy_id.as_ref().is_none_or(|p| p.is_empty()) {
                    return Err("an evaluation must name its policy".into());
                }
                if evidence.basis_measurement_ids.is_empty() {
                    return Err("an evaluation must cite its measurements".into());
                }
            }
            EvidenceType::CreativeJudgment => {
                if evidence.policy_id.as_ref().is_none_or(|p| p.is_empty()) {
                    return Err(
                        "a creative judgment must name its authorizing brief".into(),
                    );
                }
            }
            EvidenceType::Measurement => {}
        }
    }
    Ok(())
}
