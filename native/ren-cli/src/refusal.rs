//! The refusal shape `library.tools.ren_refusal` renders, and the
//! machine-readable line it emits.
//!
//! The native front door serves `info`, `status` and `check` without a
//! Python process, so it has to answer a refusal the way the Python
//! does - byte for byte, because a host reads both. The Python's shape
//! is one line of consequence, then the kind of block, then what, why
//! and fix, then the next action:
//!
//! ```text
//! ren: refused - <consequence>
//!   blocker: <kind> - <what kind of thing blocked this>
//!   what: <what happened>
//!   why: <why it refused>
//!   fix: <the exact next command or edit>
//!   next action: <the move the caller can take>
//! ```
//!
//! and, when the caller asked for machine-readable output
//! (`REN_OUTPUT=json`), one final line carrying the same refusal as
//! data:
//!
//! ```text
//! REN_RESULT {"ask_reason": "", "blocker": "...", ...}
//! ```
//!
//! The blocker vocabulary and its next actions are the Python
//! taxonomy's (`library/tools/refusal_taxonomy.py`): the six kinds of
//! block, and the move each one demands. This module carries them
//! because the front door cannot import Python - and a second
//! vocabulary would be a second answer to the same question.

use std::env;

use crate::json::write_json_string;

/// The consequence a refusal states, in plain language.
pub const USER_MESSAGE: &str = "I couldn't safely finish this step. \
The requested change has not been confirmed; resolve the issue below \
before continuing.";

/// The refusal's exit code: a refusal with a fix, nothing ran.
pub const REFUSAL_EXIT_CODE: i32 = 4;

/// The kinds of block a refusal can report.
pub const MISSING_MEDIA: &str = "missing_media";
pub const UNSUPPORTED_OPERATION: &str = "unsupported_operation";
pub const UNSAFE_STATE: &str = "unsafe_state";
pub const AMBIGUOUS_INTENT: &str = "ambiguous_intent";
pub const INSUFFICIENT_EVIDENCE: &str = "insufficient_evidence";
pub const RESOURCE_CONTENTION: &str = "resource_contention";

/// Each blocker's plain-language sentence - the line a caller reads.
pub fn plain_reason(blocker: &str) -> &'static str {
    match blocker {
        MISSING_MEDIA => "the media this change needs is not there",
        UNSUPPORTED_OPERATION => "the engine has no proven way to do this",
        UNSAFE_STATE => "the state on the timeline is not safe to change yet",
        AMBIGUOUS_INTENT => "the request means more than one thing",
        INSUFFICIENT_EVIDENCE => "what is on the record does not settle it",
        RESOURCE_CONTENTION => "something else is holding the resource",
        _ => "what blocked this is not classified",
    }
}

/// The next action each blocker demands.
pub fn next_action_for(blocker: &str) -> &'static str {
    match blocker {
        MISSING_MEDIA => "supply_the_missing_media",
        UNSUPPORTED_OPERATION => "use_a_supported_operation",
        UNSAFE_STATE => "settle_the_state_first",
        AMBIGUOUS_INTENT => "state_the_intent",
        INSUFFICIENT_EVIDENCE => "collect_the_evidence",
        RESOURCE_CONTENTION => "wait_for_the_resource",
        _ => "collect_the_evidence",
    }
}

/// One refusal: what happened, why, the fix, and the kind of block.
#[derive(Clone, Debug)]
pub struct Refusal {
    pub what: String,
    pub why: String,
    pub fix: String,
    pub blocker: &'static str,
    pub code: &'static str,
}

impl Refusal {
    /// A refusal of the base family, with the kind of block it is.
    pub fn new(what: impl Into<String>, why: impl Into<String>,
               fix: impl Into<String>, blocker: &'static str) -> Self {
        Refusal {
            what: what.into(),
            why: why.into(),
            fix: fix.into(),
            blocker,
            code: "REN_REFUSAL",
        }
    }

    /// A refusal of a named family, with its own machine-readable code.
    pub fn with_code(mut self, code: &'static str) -> Self {
        self.code = code;
        self
    }

    /// The prose a reader sees.
    pub fn render(&self) -> String {
        format!(
            "ren: refused - {USER_MESSAGE}\n  \
             blocker: {blocker} - {reason}\n  \
             what: {what}\n  why: {why}\n  fix: {fix}\n  \
             next action: {next_action}",
            reason = plain_reason(self.blocker),
            blocker = self.blocker,
            what = self.what,
            why = self.why,
            fix = self.fix,
            next_action = next_action_for(self.blocker),
        )
    }

    /// The machine-readable line, in the Python's own JSON shape.
    ///
    /// The keys are the Python's `RenRefusal.to_dict()` fields, sorted,
    /// and the values are the same strings - a host that reads one
    /// reads the other.
    pub fn result_json(&self) -> String {
        let mut out = String::from("{");
        // The fields, in the Python's sorted order: the string fields,
        // and the two that are not strings (a number and a boolean).
        let fields: [(&str, &str, bool); 13] = [
            ("ask_reason", "", false),
            ("blocker", self.blocker, false),
            ("blocking_reason", plain_reason(self.blocker), false),
            ("code", self.code, false),
            ("conflict_class", "", false),
            ("exit", "4", true),
            ("fix", &self.fix, false),
            ("next_action", next_action_for(self.blocker), false),
            ("ok", "false", true),
            ("recovery_class", "blocked", false),
            ("user_message", USER_MESSAGE, false),
            ("what", &self.what, false),
            ("why", &self.why, false),
        ];
        for (index, (key, value, raw)) in fields.iter().enumerate() {
            if index > 0 {
                out.push_str(", ");
            }
            out.push('"');
            out.push_str(key);
            out.push_str("\": ");
            if *raw {
                out.push_str(value);
            } else {
                write_json_string(&mut out, value);
            }
        }
        out.push('}');
        out
    }

    /// Print the refusal and, when the caller wants it, its data line.
    pub fn emit(&self) {
        eprintln!("{}", self.render());
        if json_output_enabled() {
            eprintln!("REN_RESULT {}", self.result_json());
        }
    }
}

/// The harnesses a person drives Ren from, and the binaries they answer
/// on (`ren/hosts.py`).
const HARNESS_BINARIES: [&str; 3] = ["claude", "opencode", "codex"];

/// Whether the caller wants the machine-readable result line.
///
/// `REN_OUTPUT=json` asks for it. When the variable is not set at all,
/// the presence of a supported harness on PATH turns it on: a host
/// reads the data line, a person reads the prose (`ren/hosts.py`). A
/// value the user set explicitly always wins - an empty value keeps the
/// prose only.
pub fn json_output_enabled() -> bool {
    match env::var_os("REN_OUTPUT") {
        Some(value) => value == "json",
        None => {
            if harness_on_path() {
                env::set_var("REN_OUTPUT", "json");
                true
            } else {
                false
            }
        }
    }
}

/// Whether a supported chat harness is installed on this machine.
fn harness_on_path() -> bool {
    let Some(path) = env::var_os("PATH") else {
        return false;
    };
    for directory in env::split_paths(&path) {
        for binary in HARNESS_BINARIES {
            let candidate = directory.join(binary);
            if is_executable(&candidate) {
                return true;
            }
        }
    }
    false
}

fn is_executable(path: &std::path::Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    matches!(std::fs::metadata(path),
        Ok(metadata) if metadata.is_file()
            && metadata.permissions().mode() & 0o111 != 0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_prose_carries_the_blocker_and_the_next_action() {
        let refusal = Refusal::new(
            "no footage files found in project",
            "the check cannot verify footage that is not there",
            "copy raw footage to the project's raw footage dir",
            MISSING_MEDIA,
        );
        let rendered = refusal.render();
        assert!(rendered.starts_with("ren: refused - I couldn't safely finish this step."));
        assert!(rendered.contains(
            "  blocker: missing_media - the media this change needs is not there\n"));
        assert!(rendered.contains("  next action: supply_the_missing_media"));
    }

    #[test]
    fn the_data_line_carries_the_same_fields_the_python_does() {
        let refusal = Refusal::new("what happened", "why", "fix", MISSING_MEDIA);
        let json = refusal.result_json();
        assert!(json.starts_with("{\"ask_reason\": \"\", \"blocker\": \"missing_media\", "));
        assert!(json.contains("\"blocking_reason\": \"the media this change needs is not there\""));
        assert!(json.contains("\"exit\": 4"));
        assert!(json.contains("\"ok\": false"));
        assert!(json.contains("\"recovery_class\": \"blocked\""));
        assert!(json.ends_with("}"));
    }

    #[test]
    fn a_value_the_user_set_wins_over_the_harness_default() {
        env::set_var("REN_OUTPUT", "");
        assert!(!json_output_enabled());
        env::remove_var("REN_OUTPUT");
    }
}
