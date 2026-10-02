# What the pipeline asks models, and what each question costs

Measured 2026-09-12 on the current tree (`a7f9397c`), with the repo's own
[step-replay bench](STEP_REPLAY_BENCH.md).  No pipeline run, no Resolve, no
project write - the bench reconstructs each step's real prompt off frozen
state with the runner's own assembly, so every figure below is the string
the pipeline would send.

**The tokenizer is named on every figure.**  `o200k_base`, via `tiktoken`,
which `library/tools/replay_bench/tokens.py` calls a STATED PROXY - Claude's
tokenizer is not public.  `utf8_bytes` is exact and is what the earlier
context audits report, so both are here.  Never quote the pipeline's own
`llm_token_stats`: it is `len(s.split()) * 1.3`, measured here at 0.47x to
0.77x the `o200k_base` count on the same strings.

Reproduce the whole table:

    python3 -m library.tools.replay_bench replay round3-20260829 <step> --rev WORKTREE

---

## 1. Which steps ask a model anything

The edit_video DAG has 29 nodes.  **Twelve of them make a call.**  The
other seventeen do not, and two of those are the interesting case: their
`interface` declares outputs their deterministic half has already produced,
so `llm_output_declarations` returns `[]` and `present_llm_step` prints
`no LLM output declared - nothing to ask, skipping the call` and returns.

| Step | Type | Asks the model for |
|---|---|---|
| `creative_direction` (2.01) | llm_only | `creative_direction` |
| `speech_sequence` (2.02) | hybrid | `speech_sequence` |
| `music_selection` (2.04) | hybrid | `music_selection` |
| `mesh_spine` (2.05) | hybrid | `structure`, `music_bed` |
| `select_broll` (3.02) | hybrid | `broll_creative`, `b_roll_interjections` |
| `review_rough_cut` (3.03) | deterministic_with_llm | `cut_decisions`, `rough_cut_review` |
| `plan_transitions` (4.02) | hybrid | `transition_creative` |
| `plan_vfx` (4.03) | hybrid | `vfx_creative` |
| `plan_sfx` (4.04) | hybrid | `sfx_creative` |
| `render_motion_graphics` (4.06) | hybrid | `motion_graphics_plan` |
| `color_grade` (5.01) | hybrid | `subject_grades`, `color_correction`, `grade_assessment` |
| `validate` (6.02) | hybrid | `validation_result` |
| ~~`semantic_analysis` (1.03)~~ | deterministic_with_llm | **nothing - call skipped** |
| ~~`render` (6.01)~~ | deterministic_with_llm | **nothing - call skipped** |

Counting the two skipped steps' contexts inflates a run total by ~48k
tokens.  They are assembled and then not sent.

The `reels` process adds `select_reels` (3.04), `judge_reels` (3.05) and
three per-reel steps; see section 4.

---

## 2. The cost of one full plan run

Snapshot `round3-20260829` (project 001), `o200k_base`, prompt + context.

| Step | Before | After | Delta |
|---|---:|---:|---:|
| `select_broll` | 24,449 | 22,902 | -1,547 |
| `plan_sfx` | 23,507 | 23,507 | 0 |
| `plan_transitions` | 21,018 | 19,471 | -1,547 |
| `plan_vfx` | 20,816 | 19,269 | -1,547 |
| `speech_sequence` | 19,295 | 19,295 | 0 |
| `review_rough_cut` | 17,566 | 17,436 | -130 |
| `validate` | 14,780 | 10,687 | -4,093 |
| `mesh_spine` | 14,061 | 14,061 | 0 |
| `creative_direction` | 12,575 | 12,575 | 0 |
| `render_motion_graphics` | 9,582 | 9,582 | 0 |
| `music_selection` | 9,167 | 9,167 | 0 |
| `color_grade` | 8,543 | 8,543 | 0 |
| **TOTAL** | **195,359** | **186,495** | **-8,864 (4.5%)** |

Instruction vs context: 41,297 of the 195,359 is the handoff, the injected
schema and the brand block; the other 154,062 is routed state.  **Four
fifths of the bill is context**, which is why every narrowing below is a
`context_fields` declaration and none is a prompt rewrite.

A hybrid step re-sends its whole context on every retry - up to three
post-bridge attempts each running a three-attempt QA loop - so a per-call
saving multiplies by up to nine on a bad run.

---

## 3. What was narrowed, and the evidence it did not degrade

Each change is one `context_fields` drop path.  For each, the bench was run
at `origin/main` and at `WORKTREE` on the same snapshot; the prompt is
byte-identical on both arms and the context delta localises to exactly the
named section.

### 3.1 The music selector's answer, carried inside the spine

`mesh_spine`'s output embeds the whole of `music_selection`, so every step
routed `timed_spine` is handed the music selector's answer a second time -
6,534 B per step, in four prompts.  It goes from the three whose handoff
gives it nothing to do:

- `select_broll` and `plan_vfx` name music nowhere in their handoffs, and
  neither lists `music_selection` in its "State Interaction / Reads" table.
- `plan_transitions` routes the top-level `music_selection` its handoff
  names, so the nested copy is the same object twice - see 3.2.

It STAYS in `plan_sfx`, and 3.5 is why.

### 3.2 `plan_transitions` was sent `music_selection` twice

It declares `music_selection` (its handoff names it) *and* `timed_spine`
(which embeds the same object).  The two sections were **byte-identical
after dedent** - 44 lines each, 6,444 B and 6,534 B in one prompt.  The
nested copy is dropped; the one the handoff names stays, whole.

### 3.3 The hook's word timings

AGENTS.md 10.1: word timings do not reach a prompt.  `speech_sequence`
carries them in **two** places - `body_sequence[].word_timestamps` and
`hook_segment.word_timestamps` - and both steps routed the whole object
dropped only the first, so eleven per-word timings reached `mesh_spine`
and `review_rough_cut` on every run.

They go from `review_rough_cut`, which reviews a built cut and reads a
passage by its own `source_start`/`source_end`.  They stay in `mesh_spine`,
and 3.5 is why.

### 3.4 `validate` was sent the QA report and the summary rendered from it

6.02's bridge writes `deterministic_validation` with `status`, `checks`,
`all_issues` and `summary` - the reading - and with `qa_report`, the 18 raw
metric rows those were rendered from: 11,737 B of nested JSON, including a
13-window `speech_above_bed` array and a `frame_occupancy` blob, in a prompt
whose handoff says "Watch the rendered video file" and whose State
Interaction table reads `rendered_output` alone.  `qa_report_path` names the
file on disk and 6.02 declares the GATING `verify_render` skill, so the rows
stay reachable.  This is AGENTS.md 10.1's "never send a summary and the
structure it was rendered from", and 10.1's "no raw value list reaches a
prompt".

### 3.5 Why two of the four drops stop one step short

A prompt is a READER.  `library/tools/data_map.py` joins three sources -
what code reads, what reaches a prompt, and the SHAPE of the documents a
real run wrote - and `UNREAD_BUDGET` fails when a field the pipeline
produces has no route left.  Dropping these two in *every* step that could
carry them does not stop them being produced; it only stops anything
reading them, and the ratchet fires:

| Field | Last prompt carrier | Why it must go on being produced |
|---|---|---|
| `timed_spine.music_selection.candidates_evaluated` and `.direction_justification.why_not_forbidden` | `plan_sfx` | `why_not_forbidden` is read by `music_selection_contract.validate_selection`, which fails 2.04 when a declared forbidden register has no entry - the check the "Inspirational Motivational Music Video" failure had nowhere to fail. `field_flow` cannot follow that read across the call, so the prompt is the only route it can see. `plan_sfx` is the right carrier of the four: its handoff is the only one that reasons about music. |
| `speech_sequence.hook_segment.word_timestamps` | `mesh_spine` | 2.05 builds the hook block's `content.word_timestamps` from it, and the spine contract (AGENTS.md 6) puts `word_timestamps` on every block. `data_map.NOT_READ_ANCHORS` already records the body passages' timings as read by no code at all. |

The honest end state for both is to stop producing the field - for the
music audit trail, to stop `mesh_spine` embedding it in the spines at all,
which would take ~24 kB out of `pipeline_data.json` every run and is a
larger saving than any prompt drop.  **That cannot be verified here.**
`data_map`'s counts come from `data_map_observed.json`, the shape of
documents a REAL RUN wrote, so a schema change is invisible to the ratchet
until a project is re-observed - and re-observing means running the
pipeline on the captain's projects.  It is left as named work rather than
done blind, and `tests/contracts/test_context_ships_it_once.py` pins both carriers so
a future drop fails with the reason rather than silently orphaning the
field.

The two withheld drops are worth 1,734 tokens per run of the 10,598 that
would otherwise be available.

### 3.6 The evidence

Two halves, both reproducible.

**Mechanical.**  Every input each changed step's handoff names was hashed on
both arms.  All identical except the two containers narrowed, and inside
those, the part the handoff names is unchanged:

| Step | `timed_spine.structure` | `speech_sequence.body_sequence` | everything else the handoff names |
|---|---|---|---|
| `select_broll` | IDENTICAL (7,059 B) | - | IDENTICAL |
| `plan_vfx` | IDENTICAL | - | IDENTICAL |
| `plan_transitions` | IDENTICAL | - | IDENTICAL |
| `review_rough_cut` | - | IDENTICAL (4,110 B) | IDENTICAL |
| `validate` | - | - | IDENTICAL, `assembly_manifest` included |

**Answered.**  `select_broll` is the change with the most removed, so both
arms were answered - five slot assignments and two interjections, each with
its rationale - and diffed with the bench:

    python3 -m library.tools.replay_bench compare round3-20260829 select_broll \
        --rev-a origin/main --rev-b WORKTREE --answer-a a.json --answer-b b.json
    -> prompt identical: True
       timed_spine  A=13698  B=7163  +6,535 B
       answers identical: True

Every placement cites the block's `visual_note`, the candidate's
`usable_range`, its `framing`/`stability` or the creative direction - all
byte-identical on both arms.  Nothing in the removed block names a clip, a
slot, a framing or a pacing.  Both arms were answered by one agent, which
is what `--full-auto agent` does in production.

The gates are in `tests/contracts/test_context_ships_it_once.py`, and each was shown
to FAIL with its drop path removed before being left passing.

---

## 4. The reel path

The captain's field-test podcast holds 24 request records under
`pipeline_output/llm_requests/`, eight reels by three per-reel steps:

| Step | 8 reels | mean/reel |
|---|---:|---:|
| `reel_semantic_visual` | 59,365 | 7,420 |
| `reel_span_visual` | 22,421 | 2,802 |
| `reel_motion` | 6,143 | 767 |
| **all 24 records** | **87,929** | **10,991** |

Plus, measured off snapshot `reel-endtoend-before`, the two whole-timeline
steps: `select_reels` 38,382 (33,006 of it `spoken_lines`, the 929-line
timeline transcript) and `judge_reels` 15,889.

**The archive is not the full set, and the real cost is higher.**
`present_llm_step` writes `requests_dir / f"{node_id}.json"` - last-write-wins
per step id - so it can never show one whole run, it holds nothing from a
step that ran outside `--full-auto agent`, and every QA retry overwrites the
attempt before it.  Neither `select_reels` nor `judge_reels` is in that
directory at all, though both ran.

---

## 5. Found, priced, and NOT changed

| What | Cost per plan run | Why it stands |
|---|---:|---|
| `view:picture`, 10,044 B to six steps | ~15,000 tok | Sits BESIDE `analysis.scene` on a different axis: per-window appearance vs per-clip prose. Deliberate. |
| `creative_brief` reference map, 5,465 B to nine steps | ~12,000 tok | 1.3-5.5 kB standing in for a 47,903 B document, which is the whole point of AGENTS.md 10.1's "a reference is an ABSOLUTE PATH plus a MAP". Trimming the per-heading excerpts would remove the reader's basis for deciding whether to open a section. |
| `creative_direction.rationale` (2,190 B) and `key_moments` (1,162 B) to eight steps whose handoffs never name them | ~6,700 tok | **A captain's call, not an engineering one.** `library/tools/creative_direction.py` records both as `PROMPT_ONLY_KEYS` and calls that legitimate for a compass, and 2.01's rationale is written to downstream steps in as many words ("What I am NOT choosing, and why it matters downstream"). Unread-by-the-handoff is not unread-by-the-model. Priced here so the decision can be taken on a number. |
| `assembly_manifest`, 27,466 B to `validate` | ~7,000 tok | 6.02's `context_fields` is a standing "everything minus" decision (`context_projector.project_fields`), and the manifest is what a judge would cross-check a duration or a transition against. Narrowing it needs the captain's reading of what 6.02 is for, not a drop path. |
| `motion_elements_toon`, the whole 18-element roster, per call | ~3,600 tok x N | AGENTS.md 16: "The whole roster ships - nothing is shortlisted, because whatever selects a shortlist becomes the chooser." |
| `spoken_lines`, 929 lines to `select_reels` | ~18,000 tok | It is the material the step selects FROM. |

---

## 6. Not examined

- **The `reels` process's own `context_fields`.**  Only `select_reels` and
  `judge_reels` were measured; the three per-reel steps were priced off the
  captain's archive and not audited for duplication.
- **`compile_manifest`, `creative_cohesion`, `audio_mix`, `assign_aroll`,
  `plan_subtitles`, `render_subtitles`.**  All deterministic - they assemble
  a large context that no model is ever shown.  `compile_manifest`'s is
  104,766 tokens' worth and costs nothing.
- **Why `plan_sfx`'s reconstruction is not reproducible.**  Comparing
  `origin/main` against ITSELF reports `context identical: False` with
  no section delta and a byte-identical length, so something in its
  pre-bridge varies between runs. Pre-existing, unrelated to anything
  here, and it makes that one step's before/after unprovable by the
  bench.
- **Output tokens.**  Every figure here is what is SENT.
- **Model choice per step.**  Out of scope without evidence a cheaper model
  is sufficient.
- **Whether the run count itself can drop** - fewer calls rather than
  smaller ones.

---

## 7. `select_reels`, re-measured and narrowed (2026-10-01)

Snapshot `reel-endtoend-before`, `o200k_base`, tree `579d5954`.

**The transcript is no longer the large item.** `spoken_lines` is 482
bound-segment rows and 16,967 tokens, not the 929 lines and 33,006 of
section 4. It stays inline: the handoff asks the model to read the whole
conversation for closers, so a reference would move the same tokens
from the prompt into the answering agent's own reads.

**The repeated-take evidence was.** Three per-candidate fields of
`reel_candidates` - `retake_candidates` 11,649, `repetition_inside`
4,306, `possible_retellings` 3,895 - were 19,850 tokens, carried for all
37 candidates, though a `takes_dropped` verdict is drawn only for a
stretch the model chooses. They now travel by reference
(`library/tools/reel_diagnostics_reference.py`, the brief's mechanism):

| | prompt | context | total |
|---|---:|---:|---:|
| before | 6,392 | 43,385 | 49,777 |
| after | 6,555 | 24,661 | 31,216 (-37.3%) |

All 39 removed items are verbatim at their own candidate's line range
(`tests/unit/context/test_brief.py` follows the reference the
same way). Answered: arm A (before) twice and arm B (after) once, each
by a fresh agent, compared on timeline coverage and on the seconds
struck in `takes_dropped`:

| pair | spans matched (IoU >= 0.8) | timeline Jaccard | strike-second Jaccard |
|---|---:|---:|---:|
| A1 vs A2 (run-to-run) | 27/30 | 0.861 | 0.427 |
| A1 vs B1 | 27/30 | 0.878 | 0.361 |
| A2 vs B1 | 29/32 | 0.935 | 0.616 |

B is inside A's own variance, and B struck takes in 15 moments (197 s)
against A's 13 and 13 (120 s, 160 s), so the evidence was read, not
skipped. The B agent loaded the document with a script; about 3k tokens
of it reached its context.

**The plan path, re-measured on `round3-20260829`:** 213,735 tokens
(61,253 prompt, 152,482 context - 71% routed). No step carries one
large searchable document any more: the brief, the per-clip footage
analysis and the SFX catalogue already travel by reference, and what is
left is 1-4k-token sections spread across twelve steps.

**The bench writes into a live project.** A snapshot's `pipeline_output`
is a symlink to the source project's, so a bridge that writes a
referenced document (3.02, 3.04, 4.04) writes it there during a replay.
Not fixed here. Fixed 2026-10-01: a replay runs in a throwaway clone
(`replay_bench.snapshot.isolated_project`, docs/STEP_REPLAY_BENCH.md).
