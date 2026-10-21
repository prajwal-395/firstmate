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

/// Canonical epistemic category. These values are orthogonal to confidence.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CertaintyState {
    Observed,
    Measured,
    Inferred,
    Interpreted,
}

/// Pipeline state is coverage metadata, never a certainty value.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ProcessingStatus {
    Analyzed,
    NotAnalyzed,
    Unavailable,
    Failed,
    Inconclusive,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum QueryResultStatus {
    Present,
    ConfidentlyAbsent,
    NotYetAnalysed,
    Inconclusive,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AnalysisLevel {
    L0,
    L1,
    L2,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum CoverageFreshness {
    Fresh,
    Stale,
    Unknown,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CoverageScope {
    pub interval: Option<Interval>,
    pub coordinate_space_id: Option<String>,
    pub target_entity_id: Option<EntityId>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CompletenessBasis {
    pub lane: String,
    pub coverage: CoverageKind,
    pub freshness: CoverageFreshness,
    pub scope: CoverageScope,
    pub producer_version: String,
    pub level: AnalysisLevel,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum VerificationStatus {
    Unverified,
    Candidate,
    Verified,
    Retracted,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ConfidenceKind {
    Rank,
    Logit,
    Cosine,
    CalibratedProbability,
    Uncertainty,
    RawScore,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TypedConfidence {
    pub value: f64,
    pub kind: ConfidenceKind,
    pub producer: String,
    pub calibration_version: Option<String>,
    pub population: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CoordinateSpace {
    pub id: String,
    pub kind: String,
    pub units: String,
    pub origin: String,
    pub axes: String,
    pub calibration: Option<String>,
    /// The source whose grid this space is, when it belongs to one. A
    /// space with no owner is a grid in the air: a place in it cannot
    /// be located in any file.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub asset_id: Option<AssetId>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TransformSegment {
    pub from: Interval,
    pub to: Interval,
    pub slope: Rational,
    pub offset_ticks: i64,
    pub uncertainty_ticks: u64,
    pub invertible: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TimeTransform {
    pub id: String,
    pub from_clock: ClockId,
    pub to_clock: ClockId,
    pub segments: Vec<TransformSegment>,
    pub provenance_id: ProvenanceId,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SpaceTransform {
    pub id: String,
    pub from_space: String,
    pub to_space: String,
    /// Row-major homogeneous matrix; dimensions are checked by the owner that applies it.
    pub matrix: Vec<f64>,
    pub uncertainty: Option<f64>,
    pub provenance_id: ProvenanceId,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Signal {
    pub id: String,
    pub asset_id: AssetId,
    pub interval: Interval,
    pub measurement_type: String,
    pub unit: String,
    pub samples: Vec<Value>,
    pub sample_clock_id: ClockId,
    pub provenance_id: ProvenanceId,
    pub processing_status: ProcessingStatus,
    /// The source signal from which this derived signal was produced.
    pub source_signal_id: Option<String>,
    /// Relative artifact reference for samples stored outside the model JSON.
    pub samples_ref: Option<String>,
    /// Epistemic qualifier for derived signals; separate from processing status.
    pub estimate_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sampling_method: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sample_resolution: Option<[u32; 2]>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub coverage_intervals: Vec<Interval>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Event {
    pub id: String,
    pub interval: Interval,
    pub kind: String,
    /// Canonical parent events for multiresolution retrieval. Several
    /// parents are allowed because a fine event can belong to both a scene
    /// and a longer sequence.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub parent_event_ids: Vec<String>,
    pub participant_ids: Vec<EntityId>,
    pub status: VerificationStatus,
    pub certainty: CertaintyState,
    pub evidence_ids: Vec<ObservationId>,
    pub confidence: Option<TypedConfidence>,
    /// Boundary quality is independent for the two ends. The interval stays
    /// a non-empty half-open envelope on its source clock; `open` means the
    /// semantic boundary was outside observed coverage.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub start_boundary: Option<TemporalBoundary>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub end_boundary: Option<TemporalBoundary>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub state_changes: Vec<EventStateChange>,
    /// Which time a REPEATED event happened.
    ///
    /// An event class (the `kind`) plus a subject names a series: the
    /// second time this person brings the device to their mouth. The
    /// index is 1-based within that series, and it is the index - not
    /// the event's own id - that survives a re-measure of the same
    /// source, so a receipt that names the second occurrence still
    /// names it after the lane ran again.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub occurrence: Option<EventOccurrence>,
}

/// One occurrence of a repeated event, inside its class and subject.
///
/// The class is the event's own `kind`; the subject is the entity the
/// occurrence counts for. An event that names no participant counts
/// within the class alone, and says so with an absent `subject_id`
/// rather than with a subject nobody measured.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EventOccurrence {
    /// The repeated class this occurrence belongs to.
    pub class: String,
    /// The subject the occurrence counts for, when the event names one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub subject_id: Option<String>,
    /// 1-based position within (class, subject).
    pub index: u32,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum BoundaryStatus {
    Exact,
    Estimated,
    Open,
}

/// Uncertainty radius around an interval edge, expressed in that clock's ticks.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TemporalBoundary {
    pub status: BoundaryStatus,
    pub uncertainty_ticks: Option<u64>,
}

/// A measured or inferred property transition belonging to an event.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EventStateChange {
    pub entity_id: EntityId,
    pub property: String,
    pub from: Option<Value>,
    pub to: Option<Value>,
    pub evidence_ids: Vec<ObservationId>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Interpretation {
    pub id: String,
    pub kind: String,
    pub author: String,
    pub objective: Option<String>,
    pub policy_id: Option<String>,
    pub evidence_ids: Vec<String>,
    pub scope: Value,
    pub uncertainty: Option<f64>,
    pub certainty: CertaintyState,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub narrative_relation: Option<NarrativeRelation>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum NarrativeRelationKind {
    Claim,
    Topic,
    Explanation,
    Demonstration,
    ContextDependency,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum NarrativeHypothesisStatus {
    Candidate,
}

/// Typed narrative relation hypothesis scoped to speech and event evidence.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct NarrativeRelation {
    pub relation_type: NarrativeRelationKind,
    pub speech_passage_ids: Vec<String>,
    pub event_ids: Vec<String>,
    pub status: NarrativeHypothesisStatus,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct QueryResult {
    pub status: QueryResultStatus,
    pub candidate_evidence_ids: Vec<String>,
    pub required_missing_lanes: Vec<String>,
    pub completeness_basis: Vec<CompletenessBasis>,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_digest: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provenance_id: Option<ProvenanceId>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coverage: Option<ProcessingStatus>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub producer_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub freshness: Option<CoverageFreshness>,
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
#[serde(rename_all = "snake_case")]
pub enum IdentityScope {
    Asset,
    CaptureGroup,
    Project,
    CrossProject,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct Entity {
    pub id: EntityId,
    pub kind: EntityKind,
    pub label: Option<String>,
    pub object_class_id: Option<ObjectClassId>,
    /// Lane 3: how widely this identity holds. `None` for records
    /// migrated before scopes existed, so older documents still decode.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub identity_scope: Option<IdentityScope>,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub certainty: Option<CertaintyState>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence_detail: Option<TypedConfidence>,
    /// FI-03 evidence contract, additive: `None` for every observation
    /// migrated before the contract existed, so older documents still
    /// decode. `skip_serializing_if` keeps the round-trip byte-identical
    /// for contract-less payloads.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evidence: Option<EvidenceContract>,
}

/// FI-03: the four evidence types (plan A1). A raw instrument value
/// lives under `Measurement` and must never inhabit `Evaluation`.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceType {
    Measurement,
    Inference,
    Evaluation,
    CreativeJudgment,
}

impl EvidenceType {
    /// Compatibility adapter for FI-03's evidence-kind vocabulary. Evaluation
    /// is a policy result and therefore has no epistemic certainty state.
    pub fn legacy_certainty(&self) -> Option<CertaintyState> {
        match self {
            Self::Measurement => Some(CertaintyState::Measured),
            Self::Inference => Some(CertaintyState::Inferred),
            Self::CreativeJudgment => Some(CertaintyState::Interpreted),
            Self::Evaluation => None,
        }
    }
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
    /// FI-22: stable IDs of the policy rules applied by this evaluation.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub basis_rule_ids: Vec<String>,
    pub contract_version: u32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub certainty: Option<CertaintyState>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub processing_status: Option<ProcessingStatus>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_reference: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub spatial_frame_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coordinate_space_id: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub references: Vec<SourceReference>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coverage_scope: Option<CoverageScope>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coverage_level: Option<AnalysisLevel>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub producer_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub freshness: Option<CoverageFreshness>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_stream_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub sampling_method: Option<String>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub temporal_coverage: Vec<Interval>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coverage_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verified_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub producer: Option<String>,
}

/// One native element of a source: a video frame or an audio sample.
///
/// The element is counted in the source's OWN indexing, never in a
/// re-indexed or re-timed one, so a reference to "frame 12" means the
/// same frame of the same file wherever it is read.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum SourceElement {
    Frame { frame_index: u64 },
    Sample { sample_index: u64 },
}

/// The universal source reference: the one way to point at the original
/// information a claim was read from.
///
/// It composes what the model already has - an asset, a stream, an
/// element, an interval, a region - and adds one optional level of
/// detail (a sub-element of another reference). Every variant names
/// the SOURCE, never a derived record: a claim about a picture is
/// grounded in the picture, not in the observation that described it.
///
/// The vocabulary names no application and no model provider. A
/// reference stays meaningful when the analysis that made it came from
/// one tool and the edit that uses it is executed by another.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum SourceReference {
    /// The original file, whole.
    Asset { asset_id: AssetId },
    /// One stream inside an asset.
    Stream { stream_id: StreamId },
    /// One native element of a stream, on the clock that counts it.
    Element {
        stream_id: StreamId,
        clock_id: ClockId,
        element: SourceElement,
    },
    /// A run of a stream's elements, on the clock that counts them.
    Interval {
        stream_id: StreamId,
        interval: Interval,
    },
    /// A place in a named coordinate space.
    Region { space_id: String, geometry: Value },
    /// A named part of another reference - a mask, a track, a layer.
    SubElement {
        parent: Box<SourceReference>,
        sub_kind: String,
        sub_id: String,
    },
}

impl SourceReference {
    /// Whether this reference names the SOURCE itself.
    ///
    /// A measurement is complete only when at least one of its
    /// references reaches the source: a stream, an element, an interval
    /// or a region. An asset reference names the file, and a sub-element
    /// is only as good as the parent it hangs from.
    pub fn names_source(&self) -> bool {
        match self {
            Self::Stream { .. } | Self::Element { .. } | Self::Interval { .. } => true,
            Self::Region { .. } => true,
            Self::Asset { .. } => false,
            Self::SubElement { parent, .. } => parent.names_source(),
        }
    }
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
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub supporting_observation_ids: Vec<ObservationId>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub contradicting_observation_ids: Vec<ObservationId>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scope: Option<Interval>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub verification_status: Option<VerificationStatus>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence_detail: Option<TypedConfidence>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub method_version: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub uncertainty: Option<f64>,
    /// Lane 3: the local track this link re-associates. `None` for
    /// observation-anchored links written before tracks were named.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub track_id: Option<TrackId>,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interval: Option<Interval>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_ids: Vec<ObservationId>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coordinate_space_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub space_transform_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub confidence_detail: Option<TypedConfidence>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ObjectTrack {
    pub id: TrackId,
    pub entity_id: EntityId,
    pub clock_id: ClockId,
    pub samples: Vec<TrackSample>,
    pub provenance_id: ProvenanceId,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub continuity: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub coverage: Option<ProcessingStatus>,
    /// Lane 3: the tracker's clip-local id, distinct from the durable
    /// entity id. `None` for tracks migrated before local ids existed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub local_track_id: Option<String>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RelationClass {
    Temporal,
    Physical,
    Semantic,
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
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence_ids: Vec<ObservationId>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub status: Option<VerificationStatus>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub certainty: Option<CertaintyState>,
    /// Lane 3: temporal edges are computable from clock-aligned
    /// intervals; physical edges need spatial/track evidence and stay
    /// candidates until verified; semantic edges need
    /// interpretation-level support and stay interpreted candidates.
    /// `None` for relations migrated before classes existed.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub relation_class: Option<RelationClass>,
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
    pub query_result_id: Option<String>,
    pub evidence_revision: Option<String>,
    pub creative_rationale: Option<String>,
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

/// FI-22: authored requirements and preferences used to produce evaluations.
/// An absent policy is represented by an empty collection, never by defaults.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct EvaluationPolicy {
    pub format: String,
    pub id: String,
    pub version: u32,
    pub project_id: ProjectId,
    pub requirements: Vec<PolicyRule>,
    pub preferences: Vec<PolicyRule>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PolicyRule {
    pub id: String,
    pub kind: String,
    pub measurement_type: String,
    pub operator: String,
    pub expected: Value,
    pub origin: String,
    pub origin_ref: String,
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
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub signals: Vec<Signal>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub events: Vec<Event>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub interpretations: Vec<Interpretation>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub coordinate_spaces: Vec<CoordinateSpace>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub time_transforms: Vec<TimeTransform>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub space_transforms: Vec<SpaceTransform>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub query_results: Vec<QueryResult>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evaluation_policies: Vec<EvaluationPolicy>,
    /// Canonical ordered composition state, committed in the same event history.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub timeline_states: Vec<TimelineState>,
}

/// Current ordered composition for one timeline, projected from edit history.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TimelineState {
    pub id: String,
    pub timeline_id: TimelineModelId,
    pub generation: u64,
    pub clip_use_ids: Vec<ClipUseId>,
    pub edit_operation_ids: Vec<EditOperationId>,
}

pub const PROJECT_MODEL_SCHEMA_VERSION: u32 = 1;
/// FI-03 evidence contract version. Observation documents keep schema
/// version 1: the contract rides the additive `evidence` field, and a
/// record stamped newer than this is refused by validation.
pub const EVIDENCE_CONTRACT_VERSION: u32 = 2;
/// The evidence-contract version that introduced the universal source
/// reference. A record stamped at or above it carries references that
/// name the source; a record stamped below it predates the contract
/// and keeps reading as it was written.
pub const SOURCE_REFERENCE_CONTRACT_VERSION: u32 = 2;
/// The one coordinate-space kind that describes itself.
///
/// The source's own pixel grid needs no calibration: it IS the source's
/// own grid. Every other space is derived from something, and a derived
/// space that does not say how it was established is a number with
/// nothing behind it.
pub const SELF_DESCRIBING_SPACE_KIND: &str = "source_pixels";
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
    let mut policy_versions = std::collections::BTreeSet::new();
    let mut policy_rules = std::collections::BTreeMap::new();
    for policy in &model.evaluation_policies {
        if policy.format != "evaluation_policy/1"
            || policy.id.is_empty()
            || policy.version == 0
            || policy.project_id != model.id
        {
            return Err("evaluation policy needs a supported format, positive version, id and matching project".into());
        }
        let policy_ref = format!("{}@v{}", policy.id, policy.version);
        if !policy_versions.insert(policy_ref.clone()) {
            return Err(format!("duplicate evaluation policy {policy_ref}"));
        }
        let mut ids = std::collections::BTreeMap::new();
        for (kind, rules) in [
            ("requirement", &policy.requirements),
            ("preference", &policy.preferences),
        ] {
            for rule in rules {
                if rule.kind != kind
                    || rule.id.is_empty()
                    || rule.measurement_type.is_empty()
                    || rule.origin.is_empty()
                    || rule.origin_ref.is_empty()
                    || !["eq", "ne", "lt", "lte", "gt", "gte"].contains(&rule.operator.as_str())
                    || ids
                        .insert(rule.id.clone(), rule.measurement_type.clone())
                        .is_some()
                {
                    return Err(format!("invalid or duplicate {kind} rule in {policy_ref}"));
                }
            }
        }
        policy_rules.insert(policy_ref, ids);
    }
    crate::transform_graph::validate_graph(model)?;
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
    let clock_ids: std::collections::BTreeSet<_> = model.clocks.iter().map(|c| &c.id).collect();
    let obs_ids: std::collections::BTreeSet<_> = model.observations.iter().map(|o| &o.id).collect();
    let entity_ids: std::collections::BTreeSet<_> = model.entities.iter().map(|e| &e.id).collect();
    for stream in &model.streams {
        if !clock_ids.is_empty() && !clock_ids.contains(&stream.clock_id) {
            return Err(format!(
                "stream {} references an unknown clock",
                stream.id.0
            ));
        }
        if stream.source_digest.as_ref().is_some_and(String::is_empty) {
            return Err("stream source digest must not be empty".into());
        }
    }
    for observation in &model.observations {
        if !clock_ids.is_empty() && !clock_ids.contains(&observation.interval.clock_id) {
            return Err(format!(
                "observation {} references an unknown clock",
                observation.id.0
            ));
        }
        if let Some(evidence) = &observation.evidence {
            if evidence
                .target_entity_id
                .as_ref()
                .is_some_and(|id| !entity_ids.is_empty() && !entity_ids.contains(id))
            {
                return Err("evidence references unknown target entity".into());
            }
            for reference in &evidence.basis_measurement_ids {
                if !obs_ids.is_empty() && !obs_ids.iter().any(|id| id.0 == *reference) {
                    return Err(format!(
                        "evidence references unknown observation {reference}"
                    ));
                }
            }
            validate_source_references(model, &evidence.references)?;
            // A measurement with nothing to point at is a number about
            // nothing: the record says a value was measured but not
            // which bytes it was measured from. Records written before
            // the reference contract existed keep reading; every record
            // the engine writes now carries the reference.
            if evidence.evidence_type == EvidenceType::Measurement
                && evidence.contract_version >= SOURCE_REFERENCE_CONTRACT_VERSION
                && !evidence.references.iter().any(SourceReference::names_source)
            {
                return Err(
                    "a measurement must carry at least one reference that names the source"
                        .into(),
                );
            }
        }
    }
    for link in &model.identity_links {
        if !entity_ids.is_empty() && !entity_ids.contains(&link.entity_id) {
            return Err("identity link references unknown entity".into());
        }
        for id in link
            .supporting_observation_ids
            .iter()
            .chain(&link.contradicting_observation_ids)
        {
            if !obs_ids.is_empty() && !obs_ids.contains(id) {
                return Err(format!(
                    "identity link references unknown observation {}",
                    id.0
                ));
            }
        }
        if link.status == IdentityStatus::Verified
            && link.supporting_observation_ids.is_empty()
            && link.observation_id.0.is_empty()
        {
            return Err("verified identity links require supporting observations".into());
        }
        if let Some(score) = &link.confidence_detail {
            validate_typed_confidence(score)?;
        }
    }
    for relation in &model.relations {
        if !valid_interval(&relation.interval) {
            return Err("relation interval must be non-empty and half-open".into());
        }
        if !entity_ids.is_empty()
            && (!entity_ids.contains(&relation.subject_id)
                || !entity_ids.contains(&relation.object_id))
        {
            return Err("relation references unknown participant".into());
        }
        if relation.status == Some(VerificationStatus::Verified) && relation.evidence_ids.is_empty()
        {
            return Err("verified relations require evidence".into());
        }
        if !obs_ids.is_empty() && relation.evidence_ids.iter().any(|id| !obs_ids.contains(id)) {
            return Err("relation references unknown observation".into());
        }
    }
    for event in &model.events {
        if !valid_interval(&event.interval) {
            return Err("event interval must be non-empty and half-open".into());
        }
        if !entity_ids.is_empty()
            && event
                .participant_ids
                .iter()
                .any(|id| !entity_ids.contains(id))
        {
            return Err("event references unknown participant".into());
        }
        if !obs_ids.is_empty() && event.evidence_ids.iter().any(|id| !obs_ids.contains(id)) {
            return Err("event references unknown observation".into());
        }
        if event.status == VerificationStatus::Verified && event.evidence_ids.is_empty() {
            return Err("verified events require evidence".into());
        }
        for change in &event.state_changes {
            if !entity_ids.is_empty() && !entity_ids.contains(&change.entity_id) {
                return Err("event state change references unknown entity".into());
            }
            if change.property.trim().is_empty() {
                return Err("event state change requires a property".into());
            }
            if change.evidence_ids.is_empty() {
                return Err("event state change requires evidence".into());
            }
            if !obs_ids.is_empty() && change.evidence_ids.iter().any(|id| !obs_ids.contains(id)) {
                return Err("event state change references unknown observation".into());
            }
        }
        if let Some(score) = &event.confidence {
            validate_typed_confidence(score)?;
        }
        for boundary in [&event.start_boundary, &event.end_boundary].into_iter().flatten() {
            if boundary.status == BoundaryStatus::Exact
                && boundary.uncertainty_ticks.is_some_and(|ticks| ticks != 0)
            {
                return Err("exact event boundaries cannot have non-zero uncertainty".into());
            }
            if boundary.status == BoundaryStatus::Open && boundary.uncertainty_ticks.is_some() {
                return Err("open event boundaries cannot claim bounded uncertainty".into());
            }
        }
    }
    let events_by_id: BTreeMap<_, _> = model.events.iter().map(|event| (&event.id, event)).collect();
    for event in &model.events {
        for parent_id in &event.parent_event_ids {
            if parent_id == &event.id {
                return Err("event cannot parent itself".into());
            }
            let parent = events_by_id.get(parent_id).ok_or("event references unknown parent event")?;
            if parent.interval.clock_id != event.interval.clock_id
                || parent.interval.start > event.interval.start
                || parent.interval.end < event.interval.end
            {
                return Err("parent event must contain child on the same clock".into());
            }
        }
    }
    for event in &model.events {
        let mut pending = event.parent_event_ids.clone();
        let mut seen = std::collections::BTreeSet::new();
        while let Some(parent_id) = pending.pop() {
            if !seen.insert(parent_id.clone()) {
                return Err("event hierarchy must be acyclic".into());
            }
            if let Some(parent) = events_by_id.get(&parent_id) {
                pending.extend(parent.parent_event_ids.iter().cloned());
            }
        }
    }
    for signal in &model.signals {
        if !valid_interval(&signal.interval) {
            return Err("signal interval must be non-empty and half-open".into());
        }
        if !clock_ids.is_empty()
            && (!clock_ids.contains(&signal.sample_clock_id)
                || !clock_ids.contains(&signal.interval.clock_id))
        {
            return Err("signal references unknown clock".into());
        }
        if signal.processing_status == ProcessingStatus::Unavailable && !signal.samples.is_empty() {
            return Err("unavailable signal cannot carry samples".into());
        }
    }
    for transform in &model.time_transforms {
        if !clock_ids.is_empty()
            && (!clock_ids.contains(&transform.from_clock)
                || !clock_ids.contains(&transform.to_clock))
        {
            return Err("time transform references unknown clock".into());
        }
        for segment in &transform.segments {
            if !valid_interval(&segment.from)
                || !valid_interval(&segment.to)
                || segment.slope.denominator == 0
            {
                return Err("time transform has invalid or discontinuous segment".into());
            }
            if segment.from.clock_id != transform.from_clock
                || segment.to.clock_id != transform.to_clock
            {
                return Err("time transform segment clock mismatch".into());
            }
        }
    }
    for interpretation in &model.interpretations {
        if interpretation.certainty != CertaintyState::Interpreted {
            return Err("interpretations must have interpreted certainty".into());
        }
        if interpretation
            .uncertainty
            .is_some_and(|v| !v.is_finite() || !(0.0..=1.0).contains(&v))
        {
            return Err("interpretation uncertainty must be between 0 and 1".into());
        }
    }
    for transform in &model.space_transforms {
        if transform.matrix.len() != 9 && transform.matrix.len() != 16 {
            return Err("space transform matrix must be 3x3 or 4x4".into());
        }
        if transform.matrix.iter().any(|v| !v.is_finite()) {
            return Err("space transform matrix values must be finite".into());
        }
    }
    for track in &model.tracks {
        if !clock_ids.is_empty() && !clock_ids.contains(&track.clock_id) {
            return Err("track references unknown clock".into());
        }
        if track
            .samples
            .windows(2)
            .any(|pair| pair[0].tick >= pair[1].tick)
        {
            return Err(
                "track samples must be strictly ordered; discontinuity must be explicit".into(),
            );
        }
        for sample in &track.samples {
            if sample
                .interval
                .as_ref()
                .is_some_and(|i| !valid_interval(i) || i.clock_id != track.clock_id)
            {
                return Err("track sample interval must be valid on the track clock".into());
            }
            if !obs_ids.is_empty() && sample.evidence_ids.iter().any(|id| !obs_ids.contains(id)) {
                return Err("track sample references unknown observation".into());
            }
            if sample
                .space_transform_id
                .as_ref()
                .is_some_and(|id| !model.space_transforms.iter().any(|t| &t.id == id))
            {
                return Err("track sample references unknown space transform".into());
            }
            if sample
                .coordinate_space_id
                .as_ref()
                .is_some_and(|id| !model.coordinate_spaces.iter().any(|s| &s.id == id))
            {
                return Err("track sample references unknown coordinate space".into());
            }
        }
    }
    for result in &model.query_results {
        if result.status == QueryResultStatus::ConfidentlyAbsent
            && result.completeness_basis.is_empty()
        {
            return Err("confident absence requires a completeness basis".into());
        }
        if result.status == QueryResultStatus::NotYetAnalysed
            && result.required_missing_lanes.is_empty()
        {
            return Err("not_yet_analysed requires missing lanes".into());
        }
        if result.status == QueryResultStatus::ConfidentlyAbsent
            && result.completeness_basis.iter().any(|basis| {
                basis.coverage != CoverageKind::Complete
                    || basis.freshness != CoverageFreshness::Fresh
                    || basis.producer_version.is_empty()
            })
        {
            return Err("confident absence requires fresh complete versioned coverage".into());
        }
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
    let clip_use_ids: std::collections::BTreeSet<_> = model
        .clip_uses
        .iter()
        .map(|row| row.id.0.as_str())
        .collect();
    let edit_ids: std::collections::BTreeSet<_> =
        model.edits.iter().map(|row| row.id.0.as_str()).collect();
    let mut timeline_ids = std::collections::BTreeSet::new();
    for state in &model.timeline_states {
        if state.id.is_empty()
            || state.timeline_id.0.is_empty()
            || !timeline_ids.insert(state.timeline_id.0.as_str())
        {
            return Err("timeline state requires unique non-empty timeline ids".into());
        }
        if state
            .clip_use_ids
            .iter()
            .any(|id| !clip_use_ids.contains(id.0.as_str()))
        {
            return Err("timeline state references unknown clip use".into());
        }
        if state
            .edit_operation_ids
            .iter()
            .any(|id| !edit_ids.contains(id.0.as_str()))
        {
            return Err("timeline state references unknown edit operation".into());
        }
    }
    for evidence in model
        .observations
        .iter()
        .filter_map(|x| x.evidence.as_ref())
    {
        if evidence.contract_version != EVIDENCE_CONTRACT_VERSION {
            return Err(format!(
                "unsupported evidence contract version {}",
                evidence.contract_version
            ));
        }
        if evidence.unit.is_empty() || evidence.method.is_empty() {
            return Err("evidence must carry unit and method".into());
        }
        if evidence.certainty.is_some() && evidence.evidence_type == EvidenceType::Evaluation {
            return Err("policy evaluation cannot be coerced into epistemic certainty".into());
        }
        if let Some(certainty) = &evidence.certainty {
            let compatible = match evidence.evidence_type {
                EvidenceType::Measurement => matches!(
                    certainty,
                    CertaintyState::Observed | CertaintyState::Measured
                ),
                EvidenceType::Inference => *certainty == CertaintyState::Inferred,
                EvidenceType::CreativeJudgment => *certainty == CertaintyState::Interpreted,
                EvidenceType::Evaluation => false,
            };
            if !compatible {
                return Err("legacy evidence kind conflicts with canonical certainty".into());
            }
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
                    return Err("an inference without uncertainty is not evidence".into());
                }
            }
            EvidenceType::Evaluation => {
                if evidence.policy_id.as_ref().is_none_or(|p| p.is_empty()) {
                    return Err("an evaluation must name its policy".into());
                }
                if evidence.basis_measurement_ids.is_empty() {
                    return Err("an evaluation must cite its measurements".into());
                }
                if evidence.basis_rule_ids.is_empty() {
                    return Err("an evaluation must cite its policy rules".into());
                }
                let policy_id = evidence
                    .policy_id
                    .as_deref()
                    .ok_or_else(|| "an evaluation must name its policy".to_string())?;
                let rules = policy_rules
                    .get(policy_id)
                    .ok_or_else(|| format!("evaluation references missing policy {policy_id}"))?;
                if evidence
                    .basis_rule_ids
                    .iter()
                    .any(|rule| !rules.contains_key(rule))
                {
                    return Err("evaluation references unknown policy rule".into());
                }
                let required_measurements: std::collections::BTreeSet<_> = evidence
                    .basis_rule_ids
                    .iter()
                    .filter_map(|rule| rules.get(rule).map(String::as_str))
                    .collect();
                for reference in &evidence.basis_measurement_ids {
                    let measurement = model
                        .observations
                        .iter()
                        .find(|o| o.id.0 == *reference)
                        .and_then(|o| o.evidence.as_ref());
                    if !measurement.is_some_and(|measurement| {
                        measurement.evidence_type == EvidenceType::Measurement
                            && required_measurements.contains(measurement.measurement_type.as_str())
                    }) {
                        return Err(
                            "evaluation cites a measurement unrelated to its policy rules".into(),
                        );
                    }
                }
            }
            EvidenceType::CreativeJudgment => {
                if evidence.policy_id.as_ref().is_none_or(|p| p.is_empty()) {
                    return Err("a creative judgment must name its authorizing brief".into());
                }
            }
            EvidenceType::Measurement => {}
        }
    }
    Ok(())
}

/// Every source reference must join to something the model declares.
///
/// A reference that names a stream the model does not carry, a clock it
/// does not know, or a coordinate space it never declared is a pointer
/// into nothing: the reader that follows it lands on no file, no frame
/// and no grid. The join is checked here so the hole is found when the
/// model is written, not when somebody follows the pointer.
fn validate_source_references(
    model: &ProjectModel,
    references: &[SourceReference],
) -> Result<(), String> {
    let asset_ids: std::collections::BTreeSet<_> =
        model.assets.iter().map(|a| &a.id).collect();
    let stream_ids: std::collections::BTreeSet<_> =
        model.streams.iter().map(|s| &s.id).collect();
    let clock_ids: std::collections::BTreeSet<_> =
        model.clocks.iter().map(|c| &c.id).collect();
    let space_ids: std::collections::BTreeSet<_> = model
        .coordinate_spaces
        .iter()
        .map(|s| s.id.as_str())
        .collect();
    for reference in references {
        match reference {
            SourceReference::Asset { asset_id } => {
                if !asset_ids.is_empty() && !asset_ids.contains(asset_id) {
                    return Err("source reference names an unknown asset".into());
                }
            }
            SourceReference::Stream { stream_id } => {
                if !stream_ids.is_empty() && !stream_ids.contains(stream_id) {
                    return Err("source reference names an unknown source stream".into());
                }
            }
            SourceReference::Element {
                stream_id,
                clock_id,
                ..
            } => {
                if !stream_ids.is_empty() && !stream_ids.contains(stream_id) {
                    return Err("source reference names an unknown source stream".into());
                }
                if !clock_ids.is_empty() && !clock_ids.contains(clock_id) {
                    return Err("source reference names an unknown clock".into());
                }
            }
            SourceReference::Interval {
                stream_id,
                interval,
            } => {
                if !stream_ids.is_empty() && !stream_ids.contains(stream_id) {
                    return Err("source reference names an unknown source stream".into());
                }
                if !clock_ids.is_empty() && !clock_ids.contains(&interval.clock_id) {
                    return Err("source reference names an unknown clock".into());
                }
                if interval.start >= interval.end {
                    return Err("source reference interval must be non-empty and half-open".into());
                }
            }
            SourceReference::Region { space_id, .. } => {
                if !space_ids.is_empty() && !space_ids.contains(space_id.as_str()) {
                    return Err("source reference names an unknown coordinate space".into());
                }
            }
            SourceReference::SubElement { parent, .. } => {
                validate_source_references(model, std::slice::from_ref(parent))?;
            }
        }
    }
    Ok(())
}

fn validate_typed_confidence(score: &TypedConfidence) -> Result<(), String> {
    if !score.value.is_finite() || score.producer.is_empty() {
        return Err("typed confidence needs a finite value and producer".into());
    }
    if score.kind == ConfidenceKind::CalibratedProbability
        && (score
            .calibration_version
            .as_deref()
            .unwrap_or("")
            .is_empty()
            || !(0.0..=1.0).contains(&score.value))
    {
        return Err("calibrated probability needs a version and a value between 0 and 1".into());
    }
    if score.kind == ConfidenceKind::Uncertainty && !(0.0..=1.0).contains(&score.value) {
        return Err("uncertainty must be between 0 and 1".into());
    }
    Ok(())
}
