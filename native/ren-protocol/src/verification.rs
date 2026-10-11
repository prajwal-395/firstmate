//! The verification finding: one row of a render QA report.
//!
//! Modeled from `library/tools/qa_findings.py` (Finding). A finding is
//! measured, classified and owned; reading it never gates.

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The check's own verdict did not pass.
pub const FAILING: &str = "failing";

/// It passed the gate and still missed its own target. Report-only.
pub const ADVISORY: &str = "advisory";

/// Nothing to say.
pub const CLEAN: &str = "clean";

/// Loudest first. Anything else is reported under its own name.
pub const SEVERITIES: [&str; 3] = ["error", "warning", "info"];

/// The one severity a gate is entitled to act on. Reading never gates.
pub const BLOCKING_SEVERITY: &str = "error";

/// One row of `qa_report.json`, as the reader sees it.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize, JsonSchema)]
pub struct VerificationFinding {
    pub metric: String,
    pub passed: bool,
    pub severity: String,
    pub detail: String,
    #[serde(default)]
    pub value: Option<Value>,
    #[serde(default)]
    pub threshold: Option<Value>,
}

impl VerificationFinding {
    /// The classified verdict (`Finding.verdict`): a check that did not
    /// pass is FAILING; one that passed while carrying a non-`info`
    /// severity is ADVISORY if and only if its metric is report-only;
    /// everything else is CLEAN.
    pub fn verdict(&self, report_only_metrics: &[&str]) -> &'static str {
        if !self.passed {
            return FAILING;
        }
        if report_only_metrics.contains(&self.metric.as_str()) && self.severity != "info" {
            return ADVISORY;
        }
        CLEAN
    }

    /// Whether a GATE would be entitled to act on it. Reading it here
    /// never does.
    pub fn blocks(&self) -> bool {
        !self.passed && self.severity == BLOCKING_SEVERITY
    }
}
