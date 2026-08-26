# The step-replay bench

Rebuild any step's exact prompt and context off frozen state, at a named
revision, and diff two revisions against each other.

**No pipeline run. No Resolve. No project write.** It can be used while
other workers hold the project, which is the point: the alternative way to
answer "did that change the edit" costs a full run plus a Resolve render.

    python3 -m library.tools.replay_bench capture <project> [--id NAME]
    python3 -m library.tools.replay_bench list
    python3 -m library.tools.replay_bench check   <snapshot>
    python3 -m library.tools.replay_bench replay  <snapshot> <step> [--rev REV]
    python3 -m library.tools.replay_bench compare <snapshot> <step> --rev-a A --rev-b B
                                                  [--answer-a f.json --answer-b g.json]
    python3 -m library.tools.replay_bench verify  <snapshot> [--rev REV]

`--rev` takes a git revision or the literal `WORKTREE`, which is this
checkout as it stands, uncommitted edits included. A revision that is not
`WORKTREE` is checked out into a detached worktree under the snapshot
store and reused across steps.

## What it replays, and what it does not

The reconstruction is the runner's own code, not a model of it:
`gather_step_inputs` (the real DAG edge walk, the real brand block), the
step's own `bridge.py` as a subprocess over JSON stdin, `project_fields`
including the `-` drop paths, `json_to_toon`, the handoff with the schema
injected from the manifest, and `TemplateLoader.get_brand_constraints`.

Two things are not replayed, and both are recorded on the result:

- **A `deterministic_with_llm` step's `step.py` is not re-run.** For
  `render` that would mean driving Resolve. The step's RECORDED output
  stands in for it - which means the recorded output has that step's own
  LLM answer merged into it, so a naive replay feeds the step its own
  answer. `--llm-authored` names the keys to withhold; `verify` reads them
  out of the archive's `expected_schema`. **Nothing in `pipeline_data.json`
  records which keys a model wrote** - that gap is item 8 of the
  information-layer plan, and it is why this cannot be derived.
- **The LLM call is not made.** This rebuilds the question, not the answer.
  Answers are diffed when you supply them (`--answer-a` / `--answer-b`).

## The snapshot decision

**Captured into a store outside the repository; only the MANIFEST is
committed**, to `tests/fixtures/replay_snapshots/`.

Committing the payload would make results reproducible by anyone and would
go stale silently: 001's `pipeline_data.json` alone is 4.2 MB of one
client's transcripts and vision documents, and the moment a step changes
what it emits the fixture describes a pipeline that no longer exists.
Capturing on demand is always current and makes two people's results
incomparable.

The manifest is a few kilobytes and carries the sha256 of every copied
file, a listing digest of every referenced area, the source project, the
capture time, the repository HEAD at capture, and the runner's own account
of what it was doing. Two people can establish they hold the same bytes
without either shipping 7 MB of somebody's footage analysis.

`pipeline_data.json` and `project.yaml` are COPIED - they move under a
running pipeline. `raw/`, `music/`, `assets/`, `brand_assets/`,
`compositions/` and `pipeline_output/` are REFERENCED by symlink: 001's
music library is 3 GB and its output tree is 2 GB, and copying either per
snapshot is a backup, not a snapshot. They are also the areas a pre-bridge
reads and never writes.

Default store: `~/.video_editing_pilot/replay_snapshots`, overridable with
`PIPELINE_REPLAY_SNAPSHOTS` or `--store`.

### How staleness is noticed

Three separate questions, kept apart because they have different answers:

| question | how | reported as |
|---|---|---|
| Has the snapshot itself been edited? | sha256 of every copied file | `sealed` |
| Has the project moved since capture? | listing digest per referenced area | `references_drifted` |
| Did THIS run write anything? | the same digests, before and after | `wrote_nothing` |

The second is usually true on a project other workers are running, and is
usually harmless - the frozen state is frozen. The third is the one that
proves the bench is read-only, and it is a measurement rather than a
promise.

A snapshot taken while a run is in flight is TORN: `pipeline_data.json` is
rewritten after every step. `capture` reads `pipeline_run.json` and says
so.

### Two overrides, and why they exist

`--state` and `--archive` freeze bytes that are no longer at the project's
own addresses. Both are needed more often than they look:
`pipeline_output/llm_requests/` is **last-write-wins per step with nothing
marking a run boundary**, so a later run silently replaces the archive a
state file belongs with. Every copied file records the absolute path it
came from, and `--note` carries the provenance a path cannot.

## Token counts

Measured from the reconstructed string, with the tokenizer named.

- `utf8_bytes` is always available and EXACT. It is what the LLM context
  audit and the information-layer plan both report, so their figures and
  these are directly comparable.
- `o200k_base` needs `tiktoken`, which is not in `requirements.txt`. It is
  a **stated proxy**: Claude's tokenizer is not public and Gemini's needs
  an API key. When `tiktoken` is absent the count is absent - the bench
  does not print an estimate under a real tokenizer's name.

**Never use the pipeline's own token figures.** `present_llm_step` records
`len(s.split()) * 1.3` under the key `token_count`, and that is the only
place the pipeline writes one. Measured on 001's own contexts at HEAD, it
is 0.38x to 0.54x the `o200k_base` count; the context audit measured the
full range at 0.3x to 9.9x, in both directions. `tokens.pipeline_heuristic`
reproduces it deliberately so a report can show the two side by side, and
it is unreachable through `count()` or `measure()` because those name a
tokenizer and it names none.

## The gate: reconstruct the past before comparing futures

`verify` reconstructs every context in a snapshot's archive and compares
byte for byte with what the runner really wrote. **If it cannot reproduce
the past it cannot be trusted to compare futures**, so `verify` exits
non-zero on any unaccounted difference.

Two passes per step, and the difference between them is the whole
discipline. The raw pass accounts for nothing. The explained pass
subtracts the two causes the bench can name AND reproduce, both read out
of the archive itself and both reported per step:

- **The archive is a QA retry.** `present_llm_step` appends a QA feedback
  block to the context before re-asking, and a request file is
  last-write-wins per step, so a surviving file may be the retry rather
  than the first attempt.
- **A `deterministic_with_llm` step's self-reference**, as above.

A step that matches only after an explanation is reported as
`EXACT (explained)` and never as a clean pass.

### The recorded result, on 001

Snapshot `001-2026-08-26T1058Z` - 001's state and complete eleven-step
archive as they stood at 2026-08-26 10:58 local, copied out before the
11:06 run began overwriting `llm_requests/`.

**The archive is entirely pre-#194, so reconstruct it at `940893d`.** At
that revision all eleven match:

```
step                   type                      recon B  archive B     delta  verdict
creative_direction     llm_only                  113,053    113,053        +0  EXACT
mesh_spine             hybrid                  1,212,806  1,212,806        +0  EXACT (explained)
music_selection        hybrid                      7,515      7,515        +0  EXACT
plan_sfx               hybrid                     68,211     68,211        +0  EXACT
plan_transitions       hybrid                    180,248    180,248        +0  EXACT
plan_vfx               hybrid                     55,963     55,963        +0  EXACT
render                 deterministic_with_llm     41,697     41,697        +0  EXACT (explained)
review_rough_cut       deterministic_with_llm  1,240,137  1,240,137        +0  EXACT (explained)
select_broll           hybrid                     90,972     90,972        +0  EXACT
semantic_analysis      deterministic_with_llm    116,010    116,010        +0  EXACT
speech_sequence        hybrid                    147,990    147,990        +0  EXACT
8 exact, 3 exact after a named and reproduced cause, 0 unaccounted, of 11
```

The three exceptions the LLM context audit reported as unexplained
differences - `mesh_spine` -183 B, `review_rough_cut` +9,542 B and
`render` - each reduce to zero once their cause is subtracted, and each
localises to exactly one top-level section of the context. The audit
attributed `mesh_spine`'s -183 B to state moving after the call; it is the
QA-retry block, and `brand_content` is the section it lands after.

At `WORKTREE` the same eleven no longer match, and that is the bench
working rather than failing: the deltas are exactly what #194 changed.
`temporal_index` leaves `mesh_spine` and `review_rough_cut`;
`timed_spine.*.word_timestamps` leaves the four planning steps at 18,133 B
each; `clip_catalog` and `semantic_analysis_documents` arrive at
`mesh_spine`. The `mesh_spine` figure the bench measures at HEAD - 9,857
`o200k_base` tokens - is the figure #194's own commit message claims.

## Two limits worth knowing before trusting a result

**A frozen snapshot ages against the tree, and the bench cannot tell you
when it has aged too far.** It can tell you the snapshot is sealed and
whether the project has moved; it cannot tell you that a step now reads a
key the frozen state predates. What it does instead is fail loudly:
`gather_step_inputs` raises on a missing required mapped input and
`project_fields` raises when every item of a declared path projects empty.
A snapshot too old to reconstruct a step reports an error for that step,
not a quiet answer.

**Answering the same question twice with the same model is not a
controlled experiment.** Model variance can swamp the difference a
routing change makes, and a single pair of answers cannot separate them.
The bench diffs answers because the diff is worth reading, not because one
diff settles anything. To see past variance you need repetition - the same
context answered n times to establish the within-revision spread, before
any between-revision difference means anything - and the bench does not do
that for you: it makes n answers cheap to generate and gives you a
structural diff to score them with.

## Where the bench must not go

It measures the pipeline; the pipeline must not read it. Nothing under
`library/steps/`, `library/processes/` or `library/dashboard/` may import
it, and `tests/test_replay_bench.py` fails if one does - the same
discipline `tests/test_footage_query_prototype.py` holds for the footage
index (AGENTS.md §2).

`reconstruct.py` imports nothing from `library` at module scope, and a
test enforces that too: it runs as a subprocess with the TARGET tree first
on `sys.path`, and a module-scope import would bind whichever tree the
parent was on - silently measuring the same code on both sides of a
comparison.
