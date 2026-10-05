//! Receipts, refusals and recorded generations.
//!
//! Modeled from `library/tools/edit_patch.py` (the patch receipt),
//! `library/tools/ren_refusal.py` (the one refusal shape) and
//! `library/tools/timeline_shadow.py` (the recorded generation).

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::ids::{Generation, SnapshotHash, TimelineId};

/// One refusal's outcome. A refusal is not a failure: nothing ran,
/// nothing is half-written, and the next step is known.
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
