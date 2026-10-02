# Context projection: what reaches a prompt

Narrative moved out of the context tests. The rules live in AGENTS.md 10.1 and
`library/tools/context_projector.py`; the tests keep the invariants.

## Nothing reaches a prompt twice (tests/test_context_ships_it_once.py)

An audit of the ten LLM request payloads the 2026-08-26 clean run of 001
wrote to `pipeline_output/llm_requests/` found the same mistake three
times over: the stored artifact and what a reader needs were treated as
the same object, so the layer that assembles context shipped both.

* `speech_sequence` received all 110 transcript lines as `transcript`
  (the view) and again as `transcripts_toon` (its own pre-bridge),
  19,844 characters and a quarter of the step's context - and the two
  copies disagreed about which column was the start time.
* `analysis.scene` is `scene[]` rendered as prose and `analysis.motion`
  is `camera[]` rendered as prose. Five steps were handed the prose
  AND the structure it was rendered from, in the same row.
* `music_analysis` carries 274 beat times, 69 downbeats and a 198-point
  energy curve. They reached `mesh_spine`, `plan_sfx` and
  `plan_transitions` one value per line. Nothing reads them: beat
  proximity is decided in `plan_transitions`' post-bridge, off the
  UNPROJECTED inputs, before any model sees a context.

The 2026-09-12 audit found four more of the same shape and priced every
model call the pipeline makes: `docs/MODEL_CALL_COST_AUDIT.md`.

### The music audit trail, out of the spines

`mesh_spine`'s output used to embed the whole of `music_selection`, so every
step routed `timed_spine` or `audio_spine` was handed the music selector's
answer a second time (measured on 001's round3 snapshot: 6,534 B per step, in
four prompts). Captain's ruling, 2026-09-16: stop producing it. `mesh_spine`
writes no `music_selection` key into either spine, and the record lives in its
own file - `library/tools/music_audit_trail.py`, written by step 2.04. The
nested-copy apparatus (drop paths, last-carrier pin) went with it.

### Word timings

`speech_sequence` carries timings in two places -
`body_sequence[].word_timestamps` and `hook_segment.word_timestamps` - and both
steps routed the whole object dropped only the first, so the hook's eleven
timings reached both prompts. 3.03 reviews a built cut and has no use for them;
2.05 keeps them as the last prompt carrier (it builds the hook block's
`content.word_timestamps` from it).

### The QA report beside its summary

6.02's bridge writes `deterministic_validation` with `status`, `checks`,
`all_issues` and `summary`, and with `qa_report`, the 18 raw metric rows those
were rendered from - 11,737 B of nested JSON in one prompt on 001.
`qa_report_path` names the file on disk and 6.02 declares the gating
`verify_render` skill, so the rows are reachable rather than withheld.

## A declaration the projection never read (tests/test_context_fields_binds.py)

Step 3.04 declared its allow-list under `interface`, beside the inputs and
outputs. `project_step_context` looks at the manifest's top level, found nothing
there, and took that for "this step declares none" - a real and deliberate
state (`render` and `validate` are in it) and therefore indistinguishable from
the accident. The projection never ran. Every reel selection the pipeline had
made was made from a request in which 817,317 characters - 92% of it, 8,509
words carrying individual start/end timings - were the raw transcript the
step's declaration had asked to drop.

The tests read the declaration from BOTH locations, so they exercise the
mechanism rather than the fix: one fails on the misplaced declaration, one on
the consequence through the real `project_step_context`, one in the other
direction (a gate that passed by deleting everything would be no gate), and the
LLM steps are DERIVED from the DAG because the hand-written list in
`test_llm_context_routing.py` did not name `select_reels`.

## Learned context

The captain: *"the LLM can record new bits of information that it learns like
feedback into the context for the project so it doesn't run into making the
same errors again."* Captain inputs are never written to
(`project_layout.Kind.INPUT`), so the write-back is a separate area -
`learned_context/`, pipeline-owned, read alongside `context/` on every later
run and attributed so the captain can tell what they said from what the
pipeline concluded.

A learning is one of three kinds, each with a named reader (AGENTS.md 10.4: a
learning nothing consumes is refused at record time): `correction` (the
captain overturned a decision; read by the step that owns it), `mistake_fix`
(the pipeline caught its own error; read by the step that made it),
`settled_decision` (asked once, never again; read by every step deciding that
thing). Retirement and correction are operations with a reason, recorded in
the learning's own history - an append-only pile of stale conclusions is the
clutter the captain complained about.
