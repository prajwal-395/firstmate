//! Receipts, refusals and recorded generations.
//!
//! Modeled from `library/tools/edit_patch.py` (the patch receipt),
//! `library/tools/ren_refusal.py` (the one refusal shape),
//! `library/tools/refusal_taxonomy.py` (what blocks a refusal and what
//! the caller does next) and `library/tools/timeline_shadow.py` (the
//! recorded generation).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ids::{Generation, SnapshotHash, TimelineId};

/// What KIND of thing blocked a refusal
/// (`library/tools/refusal_taxonomy.py`'s `BLOCKERS`).
///
/// PROD-003: a refusal that says what happened and what to do about it
/// but never says what kind of block it was leaves a host to read prose:
/// missing media, an unsupported operation and a busy resource all read
/// as "it refused". These six are the whole vocabulary, and the Python
/// taxonomy owns it - the schema generated from this enum is what both
/// sides are checked against.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum Blocker {
    /// The media (or the rendered file, the index, the sign-off) the
    /// request needs is not there.
    MissingMedia,
    /// The engine has no proven route for what was asked.
    UnsupportedOperation,
    /// The state is not safe to act on yet.
    UnsafeState,
    /// The request means more than one thing, and the choice changes
    /// the result.
    AmbiguousIntent,
    /// What is on the record does not settle it.
    InsufficientEvidence,
    /// Something else holds the resource.
    ResourceContention,
}

/// The move the caller can take next
/// (`library/tools/refusal_taxonomy.py`'s `NEXT_ACTIONS`).
///
/// A refusal without a next action is a dead end: the caller is told
/// what happened and nothing else. The vocabulary is the taxonomy's,
/// and each blocker's own next action is the default
/// (`refusal_taxonomy.BLOCKER_NEXT_ACTION`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum NextAction {
    /// Put the file, the index or the record where the engine reads it.
    SupplyTheMissingMedia,
    /// Carry the argument the engine named.
    SupplyTheMissingParameter,
    /// Ask for something the engine has a proven route for.
    UseASupportedOperation,
    /// Resolve what is unsafe, then re-run.
    SettleTheStateFirst,
    /// A person answers the smallest question that unblocks this.
    AnswerTheQuestion,
    /// Record or measure what is missing, then re-run.
    CollectTheEvidence,
    /// The holder finishes; the same call may be repeated afterwards.
    WaitForTheResource,
    /// Say which of the several things was meant.
    StateTheIntent,
}

/// Whether the host acts, asks or waits
/// (`library/tools/ren_refusal.py`'s `RECOVERY_CLASS`).
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum RecoveryClass {
    /// The host may perform only the named safe recovery.
    AutoRecoverable,
    /// Ask the smallest question that unblocks.
    NeedsQuestion,
    /// State what must be resolved first.
    Blocked,
}

/// Why a PERSON is needed (`library/tools/ren_refusal.py`'s
/// `ASK_REASONS`).
///
/// `needs_question` says THAT a question is needed; the ask reason says
/// WHY, and the difference is between asking the person and the host
/// improvising a question for them. A refusal that does not ask carries
/// the empty reason, which is why the empty string is a value here and
/// not a missing field.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum AskReason {
    /// No person is needed.
    #[serde(rename = "")]
    NotAsked,
    /// The request means more than one thing.
    AmbiguousIntent,
    /// The change is the person's to approve, and nobody has said yes.
    MissingAuthorization,
    /// What is on the record does not settle it.
    InsufficientEvidence,
}

/// One refusal's outcome. A refusal is not a failure: nothing ran,
/// nothing is half-written, and the next step is known.
///
/// PROD-003: the shape is `RenRefusal.to_dict()` - what happened, why,
/// the fix, and the three things a host acts on: the KIND of block, the
/// next action, and whether it may recover, ask or wait. Every field is
/// required, because a refusal missing one of them is the defect this
/// type exists to remove.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct Refusal {
    /// Stable machine-readable code (`RenRefusal.code`), keyed off by
    /// the support bundle and structured logs instead of parsing prose.
    pub code: String,
    /// What happened.
    pub what: String,
    /// Why it refused.
    pub why: String,
    /// The exact next command or edit.
    pub fix: String,
    /// The consequence and the next step, in plain language
    /// (`RenRefusal.user_message`).
    pub user_message: String,
    /// Whether the host acts, asks or waits.
    pub recovery_class: RecoveryClass,
    /// The kind of badness the refusal is about, or the empty string
    /// when it is not about a conflict (`RenRefusal.conflict_class`).
    pub conflict_class: String,
    /// Why a person is needed, or the empty reason.
    pub ask_reason: AskReason,
    /// What kind of thing blocked this.
    pub blocker: Blocker,
    /// The move the caller can take.
    pub next_action: NextAction,
    /// The blocker's plain-language sentence
    /// (`refusal_taxonomy.plain_reason`), carried as Python sends it.
    pub blocking_reason: String,
}

impl Refusal {
    /// The default code derived from a class name (`SpliceRefused` ->
    /// `REN_SPLICE_REFUSED`), `ren_refusal._default_code`.
    pub fn default_code(class_name: &str) -> String {
        if class_name == "RenRefusal" {
            return "REN_REFUSAL".to_string();
        }
        let mut snake = String::new();
        for (index, ch) in class_name.chars().enumerate() {
            if ch.is_uppercase() && index > 0 {
                snake.push('_');
            }
            snake.extend(ch.to_uppercase());
        }
        if snake.starts_with("REN_") {
            snake
        } else {
            format!("REN_{snake}")
        }
    }
}

/// The receipt's status. Every operation is JUDGED BY THE READ-BACK,
/// never by its return value; a failed judgement is
/// `verification_failed` and the generation still records what Resolve
/// really holds.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "snake_case")]
pub enum ReceiptStatus {
    Committed,
    VerificationFailed,
}

/// One operation's read-back judgement.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct JudgedOperation {
    pub index: i64,
    pub op: String,
    pub returned: bool,
    /// The read-back's failure text, or None when it reads back true.
    #[serde(default)]
    pub failure: Option<String>,
}

/// Where an operation raised and the patch stopped. The receipt says
/// which ran; nothing is rolled back.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct StoppedOperation {
    pub index: i64,
    pub op: String,
    /// `f"{type(raised).__name__}: {raised}"`.
    pub raised: String,
}

/// The patch receipt, as `edit_patch.apply_patch` returns it (with
/// `_receipt_of`'s three additions).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct Receipt {
    pub patch_id: String,
    pub status: ReceiptStatus,
    pub base_generation: Generation,
    pub operations: Vec<JudgedOperation>,
    #[serde(default)]
    pub stopped: Option<StoppedOperation>,
    #[serde(default)]
    pub postcondition_failures: Vec<String>,
    pub exclusive_hold_seconds: f64,
    /// `_receipt_of`: the generation this receipt recorded.
    pub generation: Generation,
    /// `_receipt_of`: the fingerprint (snapshot hash) of the read-back.
    pub fingerprint: SnapshotHash,
    /// `_receipt_of`: the generation's summary.
    pub readback: Value,
}

/// A recorded generation of one timeline (`timeline_shadow.Generation`).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct GenerationRecord {
    pub project: String,
    pub timeline_id: TimelineId,
    pub generation: Generation,
    pub timeline_name: String,
    pub hash: SnapshotHash,
    /// `patch` | `observed` (`timeline_shadow.SOURCES`).
    pub source: String,
    #[serde(default)]
    pub patch: Option<Value>,
    #[serde(default)]
    pub receipt: Option<Value>,
    pub recorded_at: f64,
    pub verified_at: f64,
}

impl GenerationRecord {
    /// The summary form (`Generation.summary`).
    pub fn summary(&self) -> Value {
        serde_json::json!({
            "project": self.project,
            "timeline": self.timeline_name,
            "timeline_id": self.timeline_id.0,
            "generation": self.generation.0,
            "hash": self.hash,
            "source": self.source,
            "patch_id": self.patch.as_ref().and_then(|p| p.get("id")),
            "recorded_at": self.recorded_at,
            "verified_at": self.verified_at,
        })
    }
}
