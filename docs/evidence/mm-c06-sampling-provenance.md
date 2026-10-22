# C06 - Frame sampling provenance is persisted with the index

Lane: mm-c06-sampling-provenance. Punchlist item C06 (build_frame_index):
adaptive sampling plans are saved with index metadata, with
frame-selection reasons and source coverage, so coverage is inspectable
during troubleshooting. Acceptance: every indexed frame set can be
traced back to its selection policy.

## The defect

`build_frame_index(..., sampling="adaptive")` constructed the adaptive
plan per clip (`measured_triggers` -> `plan_inspection` ->
`select_dense_samples`) and collected it in a local `plans` list - then
never wrote it into the payload. The docstring already promised "The
plan that chose them travels in the index"; the inspected
`frame_index.json` carried no plan, no per-frame reason, no coverage.
Uniform builds recorded even less: just the mode string.

## The fix (this lane, `library/tools/analysis/footage_frames.py`)

- The payload now carries `sampling_plans`: one entry per indexed clip
  with `mode`, `step_s`, the policy parameters
  (`policy_version`, `dense_coverage_fraction`, `max_dense_seconds`),
  `grid_frame_count` / `dense_frame_count`, the measured `signals`
  behind the plan, and - for adaptive clips - the full plan (dense
  windows with trigger evidence, `deferred`, `skipped_static`).
  Uniform clips record a `mode: uniform` entry with `plan: None`.
- Every frame row carries `selection` (`uniform` | `dense`) and
  `selection_evidence` (the dense window's measured evidence, empty
  for grid frames), derived from the retained grid plus the plan's
  dense windows (`_selection_for`).
- `FrameIndex.sampling_provenance()` reads the stored payload only -
  no model, no decode, no re-embedding - and a payload built before
  plans were persisted reports `complete: False` with the reason,
  never a silent empty plan.
- `ren search ... inspect-frames` (new `inspect-frames` subcommand in
  `library/tools/analysis/footage_query.py`) prints per-clip coverage:
  grid + dense frame counts, dense budget spent, each extra-sampled
  window with its evidence, budget-deferred triggers, static intervals
  left coarse, and unmeasured signals. `--json` prints the record;
  `--clip` narrows to one clip.

Selection arithmetic is untouched: `_adaptive_m2_frames` still
returns the same `(selected, plan)` (existing tests pin the
two-tuple and the grid-retention property), so recall cannot move.

## Measurement (stubbed M2/CLIP/triggers, no model)

Two-clip fixture, M2 every 2 s, one measured trigger per clip
(motion @ 64 s off the 10 s grid; shot change @ 20 s on it):

| | before | after |
|---|---|---|
| clips with a persisted plan | 0/2 | 2/2 |
| frames naming their selection policy | 0/19 | 19/19 (18 uniform + 1 dense) |
| dense evidence on the dense frame | - | `M3 motion[3]` |
| `inspect-frames` output | no such command | prints `63.5-64.5s: M3 motion[3]`, budget, coarse intervals |

The 20 s shot-change window adds no frame because t=20 s is already
on the grid - correct: the dense tier adds, it never duplicates.

## Regression check

- `tests/unit/picture/test_footage_query.py` +
  `tests/unit/tools/test_adaptive_inspection.py` +
  `tests/unit/picture/test_progressive_fidelity.py`: 161 passed.
- Five new defect-naming tests (persisted plans, per-frame trace,
  uniform policy, inspect output, planless payload reads incomplete).
- Ruff on the three touched files: 28 errors, all pre-existing and
  outside this diff (32 before, 4 of them mine, fixed); no new
  violations introduced.
