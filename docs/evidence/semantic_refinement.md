# Semantic refinement reaches edit execution (A01)

Code: `library/tools/semantic_refinement.py`.
Tests: `tests/unit/tools/test_semantic_refinement.py` (17 tests).

The punch list's **A01**: "Connect semantic refinement candidates to
actual execution." The sparse semantic-discovery pass already existed
(`library/tools/semantic_window_planner.py`, consumed by
`library/tools/analysis/vision_pipeline_v3.py`), and its plan already
emitted `refinement_candidates` - uncovered high-priority or
unmeasured bins. Nothing consumed them: the candidates were written
into the profile beside `window_starts_s` and the run ended. The
discovery pass scheduled where the VLM looks; no loop decided what
deserved a second look, ran it, or filed what it found.

## What the loop is

`run_refinement` over one recorded window plan, in five steps the
punchlist names:

1. **Consume** the plan's existing `refinement_candidates`
   (the discovery selection is read, never re-weighed or replanned).
2. **Rank** each candidate on five measured signals with declared
   weights summing to 1.0 (`REFINEMENT_SIGNAL_WEIGHTS`): planner
   activity (only when emitted for measured activity), scene-change
   overlap from the temporal index, speech cover fraction, uncertainty
   (only when emitted as unmeasured - one reading per axis, never
   both), and query-term hits in overlapping transcript segments
   (0.0 with `query_asked: false` when no query was asked).
3. **Select** inside a model-call budget and value threshold; the
   rest are skipped with the reason (`below_value_threshold`,
   `budget_exhausted`, `already_covered`, `no_analyzer`).
4. **Analyze only the selected** through an injected
   `analyze(start_s, end_s)` callable - in production the shim-backed
   window analysis behind the `window_analyzer` signature VI-12
   already uses. This module loads no model and decodes no media.
   A failed window is an `analysis_failed` skip, never an aborted run.
5. **Feed back** discovered scene segments through an injected
   `feed_back` - in production `source_memory.merge_scene_window`,
   the writer built for merging one measured window - and ground each
   as a Q01 `EvidenceReference` a proposed edit can cite.

Stop rules, in order: `no_candidates`, `analyzer_unavailable`,
`budget_exhausted` (high-value work remains, budget spent), else
`resolved`. Every run records which windows were skipped and why.

## Certainty of refined events

A refined VLM scene segment carries no producer score, so its Q01
uncertainty is maximal (`REFINED_SCENE_UNCERTAINTY = 1.0`, the same
rule as `evidence_contract.UNCERTAINTY_UNSTATED`): complete and
traceable, and it never executes alone - the Q01 autonomy bar
(uncertainty over 0.2) routes it to a person. A refined instrument
boundary (`scene_boundary`) files as measured at tick precision and
can go further. The loop invents no spans: candidates are requests
to inspect, and only an analyzed window's measured segments become
events.

## Measured on the recorded plans

Same shape as the Q01 measurement: recorded decisions audited
before the loop (candidates consumed by nothing, as the engine
carries them today) and after grounding them through the loop's
references, with denominators (`test_recorded_plans_before_and_after`).
Two refinement-candidate windows analyzed by a stub analyzer, one
untouched revise-note decision alongside:

- grounded edits: **0/3 before, 2/3 after** (the revise note-id
  names no observation and stays ungrounded - there is nothing to
  ground it to, which is the finding, not a gap in the audit);
- complete references: **0 before, 2 after** (both inferred scene
  segments at maximal uncertainty);
- refinement candidates reaching execution with a complete evidence
  reference: **0 before, 2 after**;
- gate verdicts under an autonomous request: both refined scene
  references need a person; the note-id is refused.

Filing is proven against a real source-memory slot (no model, no
media): `merge_scene_window` into a scratch root, read back through
`read_scenes` - the discovered scene lands in `scenes.json` with a
`complete` row in `window_coverage`.

## What is still missing

- The standard analysis run does not call the loop yet: the wiring
  point is post-discovery in `vision_pipeline_v3` (same
  `window_analyzer(start_s, end_s, source_file)` signature VI-12
  passes today), default-off until a recorded rollout measures its
  model-call cost against the discovery pass. This lane changes no
  pipeline behavior.
- Refined `scene` references always carry maximal uncertainty: a
  producer-scored VLM pass would let high-confidence segments reach
  the autonomy bar instead of every segment needing a person.
- Precision `element` has no refined producer yet, same as Q01's
  standing gap: every refined reference today is a tick interval.
