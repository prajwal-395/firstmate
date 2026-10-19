# VCR representative reel fixture

`fixture.json` describes the disposable R12/R13 comparison timeline. It uses
the three synthetic qualification sources (`qual_a` through `qual_c`) and
keeps captions and overlays as local SVG assets. Its five controls must each
run through `direct`, `staged`, and `full_rebuild` routes. The live runner is
intentionally not supplied until it can use the Resolve broker and the
production edit/build entry points; do not substitute a mock for measurements.

## Route adapter contract

Invoke `scripts/vcr_comparison_matrix.py` with `--runner` followed by the
adapter command. The matrix passes `--fixture-dir`, `--control`, `--route`,
and `--receipt`. Each invocation gets its own fixture copy and must report a
JSON receipt with:

- `schema`: `vcr_route_receipt/1`
- `control` and `route`: the exact requested identifiers
- `final_state_sha256` and `render_sha256`: read-back state and actual render
- `rerendered_components` and `changed_placements`: measured identifier lists
- `resolve_lease_seconds`: exclusive Resolve lease duration for that route
- `engine_revision`: engine revision used
- `verification`: `passed` only after state and render verification

For each control, direct and staged routes must match the full rebuild's
final-state and render hashes. The report adds rerenders, placements, and
lease time avoided relative to that independent full rebuild. A failed or
missing receipt stops the matrix; driver wall time is recorded separately
and is not a substitute for Resolve lease time.

Run the qualification recovery script only when an interrupted R5 promotion
journal already exists. It resumes that exact transaction through the AXI
broker and refuses to create or infer one.
