# Model provenance on reusable evidence

`library/tools/model_evidence_provenance.py` records the producing implementation,
checkpoint revision, license context, workload purpose, and the model's explicit
output reuse class. It keeps those fields beside evidence metadata and does not
change `certainty` or confidence.

Each implementation in `library/model_registry/registry.json` declares
`output_reuse` and points to the license evidence behind its classification.
Commercial outputs may be reused in any edition. Personal non-commercial
outputs are limited to personal editions. Research-only and unknown outputs are
benchmark-only and cannot be persisted as reusable provenance or returned to an
edit. The registry flags a published output restriction that appears tighter
than its assigned class for human review. Unknown and unresolved license terms
fall into the benchmark-only class.

`record_generation` persists provenance append-only at
`<project>/project_model/model_evidence_provenance.jsonl`; it refuses to persist
benchmark-only output. `reuse_evidence` rechecks the current class, edition,
purpose, checkpoint, license evidence and evidence-id binding. Cross-project
reuse also requires mutual opt-in from both projects. Each attempt is appended
to an audit JSONL record. A restricted item can be recomputed through a caller
provided model runner, and the replacement must pass the same target checks.

## Producer and reader integration

The production M4 scene-window producer records the exact registry
implementation, checkpoint, license class and workload on each stored window,
along with the producing project identity. Its cache reader audits each reuse
and requires cross-project opt-in. Project Model migration carries the model
record onto scene observations, and `ren evidence` filters legacy or otherwise
ineligible M4 rows before answering. The current Gemma implementation is
unresolved, so production M4 inference refuses before loading media or weights.
Personal-only output also requires a project-private memory root because the
default Source Memory cache is shared across projects.

Other legacy Source Memory lanes still lack exact registry model identifiers.
They do not gain reusable model provenance from their old producer/version
strings; consumers must fail closed until those producer boundaries are wired.

## Existing foundations

- Source Memory envelopes (merged [PR 2616](https://github.com/prajwal-395/video_editing_pilot/pull/2616)) record producer/version, sampling, coverage, confidence semantics, and source digest.
- Project Model stores separate certainty (`observed`, `measured`, `inferred`, `interpreted`) and typed confidence.
- Cross-project asset queries already require mutual opt-in.
