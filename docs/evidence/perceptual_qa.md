# Perceptual QA (Q8)

Tests: `tests/unit/picture/test_perceptual_qa.py`.

## Observation only

The perceptual pass watches the render and is OBSERVATION ONLY until there is
evidence about its false-positive rate: `results["success"]` in
`step_6_01_render/resolve_build_timeline.py` must never read it, and the pass
writes `results["perceptual_observation"]`. The calibration run has not
happened, and the one clean calibration frame already produced a finding.

## The hang: a fixed key-name bug switched on a dormant path

Each frame grab is a REAL Deliver-page render, polled to completion. That loop
never ran in production, because `plan_qa_checks` read a top-level `"clips"`
key that has never existed. Fixing that switched on a dormant path costing one
render per placed clip - minutes added to every export, unasked - and it hung
the test suite for ten minutes of wall clock on 2.75 seconds of CPU, because a
MagicMock answers "yes" to `IsRenderingInProgress()` forever and the poll slept
out its whole timeout per grab.

A fix that turns a silent no-op into a silent multi-minute cost is a poor
trade, so both the grabs and the perceptual pass are opt-in
(`perceptual_qa_enabled()`; behaviour pinned by
`test_perceptual_observation_is_off_by_default`).

## Calibration facts

- `fills_frame` was dropped (`DROPPED_DIMENSIONS`): it answered true on a
  letterboxed frame, contradicting itself, and gave the same answer on all
  three calibration replies.
- The three captured calibration replies never answered `text_legible` at all;
  before `unanswered` existed the parser skipped the absent key and all three
  frames reported clean.
- The prompt asks what is wrong, never for a score or rating.

The Fusion subprocess in the renderer is bounded by
`FUSION_SUBPROCESS_TIMEOUT_S`: an unbounded one can hang a real render with no
diagnostic. (Source-text tests pinning these were removed in the 2026-10 suite
halving; this note keeps the reasons.)
