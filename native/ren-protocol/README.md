# ren-protocol

Ren's typed protocol: the capability, scope, edit, receipt, resource and
verification types every worker speaks. Modeled from what the Python code
does today - each module names its source.

## Layout

- `src/` - the library crate. One module per protocol family:
  - `ids.rs` - the newtypes: `ClipId`, `TimelineId`, `Generation`, `SnapshotHash`
  - `capability.rs` - `CapabilityId`, `CapabilitySpec`, `ExecutionPhase`, `ExecutionPolicy`, `PatchSemantics`
  - `scope.rs` - `Scope`, `Region`
  - `edit.rs` - `EditOp` (all 69 algebra ops), `EditPatch`, `Condition`, `Inverse`
  - `receipt.rs` - `Receipt`, `Refusal`, `GenerationRecord`
  - `resource.rs` - `ResourceName`, `ResourceDemand`
  - `verification.rs` - `VerificationFinding`
  - `job.rs` - `JobKind`, `JobSubmission`, `JobShape`
  - `schemas.rs` - JSON Schema generation from the same definitions
- `schemas/` - the generated JSON Schemas, one file per type. Committed.
- `fixtures/` - real payloads captured from the Python code paths (`capture_fixtures.py`).
- `tests/roundtrip.rs` - the round-trip over the captured fixtures.
- `src/bin/emit-schemas.rs` - writes `schemas/`, `--check` fails when stale.

## Canonical project model

`src/project_model.rs` is the version 1, renderer and NLE independent
project data contract. It uses stable IDs, explicit clocks and integer
half-open intervals. `decode_project_model` rejects a missing or unknown
schema version and invalid intervals; no implicit migration is performed.
When storage and migrations are introduced, they must register an explicit
version conversion before accepting older documents. The shared fixture at
`fixtures/project-model.json` is decoded and round-tripped in Rust and read
and written by `library.tools.project_model` in Python. Its JSON Schema is
generated with the existing `emit-schemas` command.

## The workflow

```sh
cargo run --bin emit-schemas           # regenerate schemas/ after a type change
cargo run --bin emit-schemas -- --check # fail when a committed schema is stale
cargo test                              # the round-trip + the stale check
cargo clippy --all-targets
```

The schemas are generated from the Serde types, so the internal type and
the external agent/worker contract share one source of truth. Python
conforms through `library/tools/protocol_conformance.py`, which validates
at the boundaries (EditPatch serialization, broker job submission,
capability registry export) against these schemas.

## The fixture corpus

`fixtures/` holds real serialized payloads captured from the Python code
paths - a real `EditPatch.to_dict`, a real `capabilities.spec_of`, a real
`jobs.prepare` shape - never hand-written. `capture_fixtures.py` regenerates
them; the round-trip test deserializes each into the Rust type and asserts
the serialization matches. A payload that does not fit is a finding:
either the Rust type is wrong or Python has a key-name mismatch.

```sh
bin/vep native/ren-protocol/capture_fixtures.py
```

## Boring Rust over clever Rust

Concrete structs and enums, limited generics, simple traits, explicit
ownership, no async trait hierarchies. The workspace is `native/Cargo.toml`
(shared `native/target/`, gitignored); this crate is one member.
