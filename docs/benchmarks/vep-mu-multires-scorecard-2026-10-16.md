# vep-mu-multires scorecard (TMP-023..032) — 2026-10-16

Lane: multiresolution intervals on the Project Model. No model adoption,
no default change to sampling or inference, no checkpoint downloaded
(download approval exists; this lane needs none — pure representation change).

## Incumbent

- Project Model events: flat list, fixed-shape intervals, no parent links,
  no per-edge boundary quality.
- Retrieval: rigid 8 s video-chunk grid (`footage_query` / `cut_chunks`)
  or 1 s M9 world-model bins; bins carried authoritative-sounding naming.
- Cinematography hierarchy (scenes/shots/subshots/sequences) existed only
  as M4 observations + interpretations, not as queryable events.

## Change

- `Event`: optional `parent_event_ids`, `start_boundary` / `end_boundary`
  (`exact` / `estimated` / `open` + `uncertainty_ticks`), validated in
  Python (`project_model.validate_project_model`) and Rust (`ren-protocol`),
  schemas regenerated (`emit-schemas`), fixtures revalidated.
- `library/tools/project_model_temporal.retrieve_events`: overlap query on
  source-clock intervals returning exact intervals + ancestor lineage +
  source observations; no chunking, no binning.
- `_migrate_scenes` projects the measured cinematography hierarchy into
  events with containment-checked parents and edge quality derived from
  measured cut basis (shots/subshots) vs semantic grouping (scenes/sequences).
- M9 `world_model.json` relabeled in code and docs as a derived per-second
  lookup view; authoritative timing stays on source intervals (1 ms ticks,
  finer than a 60 fps frame). Only CLI/tests call `query_at_time`; no
  production edit path reads bins as timing truth.

## Measured

| Check | Result |
|---|---|
| Focused Project Model and temporal tests (`test_project_model_contract`, `test_project_model_evidence`, `test_world_model`, `test_footage_analysis`) | 69 passed after rebasing onto current `main` |
| `tests/contracts` | 329 passed after rebasing onto current `main` |
| `tests/unit/context/test_ledgers.py` + `tests/tooling/test_parallel_lane_routing.py` | 68 passed after rebasing onto current `main` |
| `cargo test -p ren-protocol` | 40 passed after rebasing onto current `main`, including `the_committed_schemas_are_current`, `every_fixture_validates_against_the_generated_schemas`, and the new hierarchy/boundary test |
| `ruff check --config ruff-ci-gate.toml` on all changed Python files | clean after rebasing onto current `main` |
| `retrieve_events`, 2001 events / 200 observations, 1 s query window | median 0.14 ms, p95 0.15 ms (in-memory scan, no I/O, no model) |
| Peak memory / residency | none added: no weights, no caches, per-call O(matches) lists |
| End-to-end cost | zero inference; migration runs inside the existing evidence build |

## Quality note

Boundary statuses are heuristic readings of existing measurement IDs, not
new measurements: a shot edge on a measured ffmpeg cut reads `estimated`,
media endpoints read `exact`, semantic scene/sequence edges with no cut
support read `open`. Agent-label evaluation does not apply (no perceptual
judgement introduced).
