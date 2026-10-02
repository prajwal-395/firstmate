# Semantic visuals land on the word that says them

Tests: `tests/unit/picture/test_semantic_visual.py`.

The gap this closes is SELECTION, not drawing. `MotionGraphics/index.tsx`
already draws Vox-shaped things - bars, counters, stamps, accents - and the
model was already asked to plan them. What nobody was ever asked is the
captain's question of 2026-09-08: *"if im talking about money, then having
assets of currency animated in"* - the step that reads "we're talking about
money here" and asks for currency.

Two rules govern the mechanism, and both are the captain's standing rules
applied to a new decision:

1. **The SUBJECT is the model's reasoning, never an engine table.**
   `semantic_visual` carries no keyword-to-icon mapping - no dict that turns
   "money" into "$". The model writes `subject` as free text and names the
   mark itself in `copy`; the engine resolves timing and geometry and never
   reads the subject to decide anything. `test_the_subject_is_inert` is the
   runnable statement: two entries differing only in subject resolve
   identically.
2. **The visual lands ON its word, not near it.** The previous lane's
   finding: things that decorate ACROSS the speech look wrong, things cued
   to their own measured word window look right. An entry names an
   `anchor_phrase` - words from the speech - and the engine searches the
   measured word timings for it (the AGENTS.md 6 discipline: anchored by
   SEARCH, never asserted). No word timings, no landing: the entry is
   dropped by name.

What the asset IS is stated honestly in `semantic_visual.ASSET_SOURCE`:
composed from type and shapes the renderer already draws. No network, no
licence, no fetch - a fetched illustration would need both, and a generated
one would need a model the pipeline does not run.

The structural no-keyword-table source scan (`test_the_engine_carries_no_keyword_table`) was removed in the 2026-10 suite halving; `test_the_subject_is_inert` is the behavioural statement of the same rule.

## The planner was never asked (authoring)

Tests: `tests/unit/picture/test_semantic_visual.py`.

PR 738 landed the mechanism - an entry names a free-text `subject` and an
`anchor_phrase`, and the visual arrives on that word's measured window -
and proved it with a hand-written demo. What it did not change is what
the model is asked to write, and the model has never written one on a
real run. Two halves of the ask were still missing:

1. The machine-readable half. The prompt's `## Required Output Format`
   block is rendered from the manifest's `interface.llm_outputs`
   description, and it still enumerates only the old entry shape
   (`start_seconds`/`duration_seconds`, no `subject`/`anchor_phrase`/
   `hold_seconds`). An answering agent following the machine-readable
   half - the shortcut the `could_not_determine` incident proved agents
   take - never emits the anchor keys.
2. The worked example. The handoff's answer template shows
   `anchor_phrase` BESIDE `start_seconds`/`duration_seconds` in one
   entry - the exact shape `resolve_plan` drops as
   `conflicting_timing`. A model copying the template is refused by
   name on every anchored entry.

The third test guards the whole authoring path the way the runner runs
it: the bridge builds `timeline_context_toon` off a spine, a
planner-authored entry quotes its anchor from that table, and
`generate_motion_props` lands it on the measured word window.
