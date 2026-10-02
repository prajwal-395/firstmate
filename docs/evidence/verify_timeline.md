# `verify_timeline` - the skill, and why it gates step 6.02

Moved from the docstring of `tests/test_verify_timeline_gate_on_validate.py`
(merged into `tests/unit/reels/test_verify_timeline_skill.py` on 2026-10-02).

PR 1168 wrapped the timeline-SOP verifier as a gating skill and proved the
SKILL both ways (eleven tests on fake Resolve) - but no step manifest declared
it, so it gated nothing: exactly the shape of a gate that cannot fail. The gate
tests prove the GATE, through the step rather than by calling the skill
directly:

- 6.02 declares `verify_timeline` beside `verify_render`, at the manifest's top
  level - the `verify_treatment`-on-4.03 shape, not a second declaration shape;
- a violating timeline STOPS the step and a conforming one PASSES it, both
  through the step's own post-bridge verdict (`resolve_validation`) fed by REAL
  skill receipts - the deterministic file half and the model's answer both say
  pass, so only the timeline gate can be what stops the violating build;
- a refusal (Resolve down) stops the step rather than passing it;
- a pass that openly skipped the link/stream checks stops the step when the
  plan was in the step's inputs, and stands when no plan existed (an old
  build): the structural half is the whole gate that run could answer.

What this does NOT exercise, and what would be needed: the full
`run_hybrid_step` loop ending in `distribution_ready: true` for a conforming
build. The loop runs 6.02's bridge first, and the bridge's deterministic half
measures the exported file with ffprobe/ffmpeg - without a real render on disk
it reports fail before any timeline verdict matters, so a conforming pass
through the whole loop needs a real export plus Resolve open on the built
timeline, answered through the agent harness. The attribution in the tests is
cleaner for it: both halves pass and only the receipt differs.
