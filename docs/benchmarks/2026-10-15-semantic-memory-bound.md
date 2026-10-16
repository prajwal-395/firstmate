# Semantic-analysis memory bound (2026-10-15)

> Measurement update: the 11.27 GB sample below was the initial observed
> physical peak. The later scheduler process-tree measurement recorded
> 14.43 GB, so the active Gemma reservation is 15 GB. See
> [2026-10-15-memory-admission.md](2026-10-15-memory-admission.md); its
> measured value supersedes the 12 GB proposal in this historical report.

## Incident

2026-10-15 the captain stopped vep-cap06's Resolve qualification
semantic-analysis step (`library/tools/analysis/vision_pipeline_v3.py`,
`1_03_semantic_analysis` via `footage_intelligence --execute
semantics.analyse`). It held 15 GB (14 GB compressed or swapped) with
swap at 21.0/21.5 GB on the 24 GB Mac - the same shape as the
10-04/05/08 kernel panics. Earlier the same day the same step hung for
54 minutes holding 14 GB. PR 2585 made the load offline and fail on
stalls but did not bound memory.

## Cause

The resident is the Gemma 4 12B 4-bit weights
(`mlx-community/gemma-4-12b-it-4bit`) plus one inference call's
buffers - not a frame, embedding, batch, pool, or cache leak:

- Frames are JPEG files on disk (`extract_frames` returns path
  lists); windows are MP4 files on disk (`extract_video_clips`).
- The worker pool is one local thread plus a bounded cloud lane
  holding no decoded frames.
- Per-call images are bounded (`LOCAL_COARSE_BATCH_SIZE`, 4 per local
  call, PR 2602 - a 15/16-image local batch OOMed Metal even with one
  analyzer).
- The weights are one shared instance per process
  (`model_lifecycle.managed_model` cache, reused across local still
  passes since PR 2602).

What was missing: the scheduler admitted any number of concurrent
model sessions. `semantics.analyse:model_memory` declared no demand
(`profiles()` read `{}`), so two sessions on the 24 GB host each grew
toward ~11 GB and overlapped into swap - the 15 GB / 21.0-21.5 GB swap
shape above.

## Measured numbers

Reproduced on the qualification source
`~/.local/share/vep/qualification/622307e41719/real_footage/LCATL0011-9b5ec750c8db.MXF`
(214.5 s), under `semantics.analyse:model_memory` in the foreground:

| | before | after |
|---|---|---|
| `model_memory` scheduler demand | `{}` (no reservation) | `{"ram_gb": 12}` |
| Single-session peak, child process | 11.27 GB peak physical footprint (`/usr/bin/time -l`: 1.89 GB max RSS parent-side, 10.5 GB sampled physical peak) | unchanged - the bound, not the weights, is the fix |
| Second concurrent session | admitted immediately; two ~11 GB sessions overlap into swap | waits on `live OS memory pressure` until the first unloads |
| Outcome on LCATL0011 | Metal timeout after 1755 s, no profile | same single-session ceiling; overlap removed |

The 12 GB reservation is the measured 11.27 GB peak rounded up.
Admission additionally requires 10% live OS headroom
(`MEMORY_HEADROOM_FRACTION`), so on a 24 GB host a 12 GB session
needs 14.4 GB available and two sessions cannot both hold grants
once the first session's weights land.

## Original PR 2614 fix (superseded by the later footprint measurement)

- `library/tools/operations.py`: `semantics.analyse:model_memory`
  declares `resources=(("ram_gb", 12),)` with `resource_basis` naming
  the measurement above.
- `library/tools/resource_scheduler.py`: `RESOURCES` admits `ram_gb`
  as a declared demand (enforced against live OS memory at admission,
  not a static capacity - captain's ruling 2026-10-15 stands);
  `_capability_demand` passes it through from capability phases.
- `library/tools/heavy_work_lock.py`, `library/tools/capabilities.py`:
  doc/audit wording for declared RAM reservations.
- `library/tools/analysis/vision_pipeline_v3.py`: attribution
  docstring updated to the measured peak and the reservation; plus a
  lazy-loader fix (`_load_mlx_model`) - the nested
  `load_cached_model` referenced the lazily-imported `load` as a bare
  global, which raises `NameError` when mlx was never imported in the
  profiling/measurement harness.

## Quality

Unchanged by construction: no prompt, batching, decoder, or model
change on this branch (batching is PR 2602's, untouched here). The
reservation affects admission order only, never a pass's inputs.

## Proof

- `tests/unit/context/test_concurrency.py::test_measured_model_ram_reservation_serializes_semantic_sessions`:
  second session waits after the first session's measured footprint
  lands, admits after release.
- `test_capability_resource_profiles_are_derived_from_execution_policy`:
  only `semantics.analyse:model_memory` carries `ram_gb`.
- Suites on this branch: `test_concurrency` 59 passed,
  `test_vision_pipeline` 64 passed, `tests/contracts` 324 passed,
  `test_ledgers` + `test_parallel_lane_routing` 68 passed,
  `test_operations` 98 passed; `ruff check --config ruff-ci-gate.toml`
  clean on all touched files.

## Residual risk, stated plainly

The guard reads live OS memory, so two sessions admitted in the same
poll interval before either session's weights land could still
overlap; the second's next admission poll (0.5 s) sees the first
session's footprint and waits. A static gigabyte capacity would close
that window but contradicts the no-static-budgets ruling. Full
LCATL0011 end-to-end (before/after profile comparison) was not
re-run in this lane: the single-session shape already exceeds this
host's comfortable margin (Metal timeout at 11.27 GB), so the
deliverable is the serialization bound, not a reduced peak.
