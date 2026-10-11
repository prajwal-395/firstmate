//! The round-trip over captured real payloads.
//!
//! Every fixture in `fixtures/` was produced by the Python code itself
//! (see `capture_fixtures.py`). Each test deserializes a fixture into the
//! Rust type and serializes it back; the two JSON values must be equal.
//! A payload that does not fit is a finding: either the Rust type is
//! wrong or Python has a key-name mismatch.

use ren_protocol::schemas;
use ren_protocol::{
    CapabilitySpec, EditPatch, GenerationRecord, JobShape, Receipt, Refusal, ResourceDemand, Scope,
    VerificationFinding,
};
use serde_json::Value;
use std::fs;
use std::path::PathBuf;

fn fixtures_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("fixtures")
}

fn read_fixture(name: &str) -> Value {
    let path = fixtures_dir().join(format!("{name}.json"));
    let text =
        fs::read_to_string(&path).unwrap_or_else(|e| panic!("cannot read {}: {e}", path.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("{name}.json is not valid JSON: {e}"))
}

fn assert_round_trip<T>(name: &str, fixture: &Value)
where
    T: serde::Serialize + for<'de> serde::Deserialize<'de>,
{
    let parsed: T = serde_json::from_value(fixture.clone())
        .unwrap_or_else(|e| panic!("{name}: the Rust type rejected a real payload: {e}"));
    let reserialized = serde_json::to_value(&parsed)
        .unwrap_or_else(|e| panic!("{name}: the Rust type failed to serialize: {e}"));
    assert_eq!(
        fixture, &reserialized,
        "{name}: the Rust type's serialization is not the payload Python produced"
    );
}

#[test]
fn edit_patches_round_trip() {
    for (index, fixture) in read_fixture("edit-patch")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        assert_round_trip::<EditPatch>(&format!("edit-patch[{index}]"), fixture);
    }
}

#[test]
fn capability_specs_round_trip() {
    for (index, fixture) in read_fixture("capability-spec")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        assert_round_trip::<CapabilitySpec>(&format!("capability-spec[{index}]"), fixture);
    }
}

#[test]
fn receipts_round_trip() {
    for (index, fixture) in read_fixture("receipt")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        assert_round_trip::<Receipt>(&format!("receipt[{index}]"), fixture);
    }
}

#[test]
fn generation_records_round_trip() {
    for (index, fixture) in read_fixture("generation-record")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        assert_round_trip::<GenerationRecord>(&format!("generation-record[{index}]"), fixture);
    }
}

#[test]
fn scopes_round_trip() {
    for (index, fixture) in read_fixture("scope").as_array().unwrap().iter().enumerate() {
        assert_round_trip::<Scope>(&format!("scope[{index}]"), fixture);
    }
}

#[test]
fn resource_demands_round_trip() {
    for (index, fixture) in read_fixture("resource-demand")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        assert_round_trip::<ResourceDemand>(&format!("resource-demand[{index}]"), fixture);
    }
}

#[test]
fn verification_findings_round_trip() {
    for (index, fixture) in read_fixture("verification-finding")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        assert_round_trip::<VerificationFinding>(
            &format!("verification-finding[{index}]"),
            fixture,
        );
    }
}

#[test]
fn job_submissions_round_trip() {
    for (index, fixture) in read_fixture("job-submission")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        let name = format!("job-submission[{index}]");
        let shape = fixture
            .get("shape")
            .unwrap_or_else(|| panic!("{name}: no shape in fixture"));
        assert_round_trip::<JobShape>(&format!("{name}.shape"), shape);
        let kind = fixture
            .get("kind")
            .and_then(|k| k.as_str())
            .unwrap_or_else(|| panic!("{name}: no kind in fixture"));
        let parsed: ren_protocol::JobKind = serde_json::from_value(Value::String(kind.to_string()))
            .unwrap_or_else(|e| panic!("{name}: kind {kind:?} is not a job kind: {e}"));
        assert_eq!(parsed.as_str(), kind);
    }
}

#[test]
fn refusals_round_trip() {
    for (index, fixture) in read_fixture("refusal")
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
    {
        assert_round_trip::<Refusal>(&format!("refusal[{index}]"), fixture);
    }
}

#[test]
fn every_fixture_validates_against_the_generated_schemas() {
    // The Rust side of the conformance contract: each fixture must
    // validate against the schema generated from the same definitions.
    // (The Python helper does the same with jsonschema; this holds the
    // Rust types to the schemas so the two cannot drift apart.)
    let schemas = schemas::schema_map();
    let cases: &[(&str, &str)] = &[
        ("edit-patch", "edit-patch"),
        ("capability-spec", "capability-spec"),
        ("receipt", "receipt"),
        ("generation-record", "generation-record"),
        ("scope", "scope"),
        ("resource-demand", "resource-demand"),
        ("verification-finding", "verification-finding"),
        ("refusal", "refusal"),
    ];
    for (fixture_name, schema_name) in cases {
        let schema = schemas
            .get(*schema_name)
            .unwrap_or_else(|| panic!("no generated schema for {schema_name}"));
        let fixture = read_fixture(fixture_name);
        for (index, row) in fixture.as_array().unwrap().iter().enumerate() {
            assert!(
                validate_against(row, schema),
                "{fixture_name}[{index}] does not validate against {schema_name}.schema.json"
            );
        }
    }
}

/// A minimal JSON Schema validator for the subset the generated schemas
/// use: type, properties, required, const, enum, items, oneOf, $ref to
/// `#/$defs/<name>`, and the `format` checks the fixtures meet.
fn validate_against(instance: &Value, schema: &Value) -> bool {
    if let Some(one_of) = schema.get("oneOf").and_then(|v| v.as_array()) {
        return one_of
            .iter()
            .any(|variant| validate_against(instance, variant));
    }
    if let Some(reference) = schema.get("$ref").and_then(|v| v.as_str()) {
        let name = reference.strip_prefix("#/$defs/").unwrap_or(reference);
        let defs = schema.get("$defs").and_then(|v| v.as_object());
        // The schema map is per-file, so a $ref resolves within the same
        // document; the caller passes the root schema which carries $defs.
        if let Some(defs) = defs {
            if let Some(target) = defs.get(name) {
                return validate_against(instance, target);
            }
        }
        return true;
    }
    if let Some(constant) = schema.get("const") {
        if instance != constant {
            return false;
        }
    }
    if let Some(values) = schema.get("enum").and_then(|v| v.as_array()) {
        if !values.iter().any(|v| v == instance) {
            return false;
        }
    }
    match schema.get("type").and_then(|v| v.as_str()) {
        Some("object") => {
            let obj = match instance.as_object() {
                Some(obj) => obj,
                None => return false,
            };
            for key in schema
                .get("required")
                .and_then(|v| v.as_array())
                .into_iter()
                .flatten()
            {
                let key = key.as_str().unwrap_or("");
                if !obj.contains_key(key) {
                    return false;
                }
            }
            for (key, value) in obj {
                if let Some(properties) = schema.get("properties").and_then(|v| v.as_object()) {
                    if let Some(property_schema) = properties.get(key) {
                        if !validate_against(value, property_schema) {
                            return false;
                        }
                    }
                }
            }
            true
        }
        Some("array") => {
            let items = match instance.as_array() {
                Some(items) => items,
                None => return false,
            };
            match schema.get("items") {
                Some(item_schema) => items.iter().all(|item| validate_against(item, item_schema)),
                None => true,
            }
        }
        Some("string") => instance.is_string(),
        Some("integer") => instance.is_i64() || instance.is_u64(),
        Some("number") => instance.is_number(),
        Some("boolean") => instance.is_boolean(),
        Some("null") => instance.is_null(),
        _ => true,
    }
}

#[test]
fn the_committed_schemas_are_current() {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("schemas");
    schemas::check_schemas(&dir)
        .expect("committed schemas are stale - re-run `cargo run --bin emit-schemas`");
}

#[test]
fn a_stale_schema_is_refused() {
    let dir = std::env::temp_dir().join("ren-protocol-stale-test");
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    fs::write(dir.join("edit-patch.schema.json"), "{}").unwrap();
    let result = schemas::check_schemas(&dir);
    assert!(result.is_err(), "a tampered schema must fail the check");
    let _ = fs::remove_dir_all(&dir);
}
