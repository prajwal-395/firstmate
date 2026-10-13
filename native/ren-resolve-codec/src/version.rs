//! Exact Resolve export identities supported by this crate.

/// Only the directly measured field-test build is accepted by the initial
/// observation schema. Matching `DbPrjVer` alone is insufficient evidence.
pub const SUPPORTED_DB_APP_VERSIONS: &[&str] = &["21.1.0.0014"];
pub const SUPPORTED_DB_PROJECT_VERSIONS: &[&str] = &["17"];
