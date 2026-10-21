# PERF-011: the refusal that already happened

Test: `tests/unit/context/test_retry_guard.py`,
`tests/unit/context/test_runner_repeated_refusal.py`,
`tests/scenarios/test_perf011_repeated_failure_costs_nothing.py`.

## The defect

A lane that refuses a step and is re-run re-derives the same decision from the same inputs, and pays for the derivation again.

The engine kept no memory of a refusal. `pipeline_data.json` carried `failed_steps` and a message; the next run read neither as a decision, gathered the same inputs, ran the same code and asked the same question. The punchlist measured it: a planning step that refused was reconstructed for **3,220 s** on the next attempt, and four build attempts each refused the same way, each one redoing the work the refusal had already decided was pointless.

## The fix

`library/tools/retry_guard.py` is the memory. A REFUSAL is recorded against the identity of the state it was decided on, and the next attempt on that identity is refused immediately, with the cause named and the fix repeated:

```
  ✗ REFUSED (REN_REPEATED_REFUSAL): select_reels was not run - the same
    refusal was already decided on these inputs.
      select_reels refused before on exactly these inputs, code and run
      mode, and nothing relevant has changed since 2026-10-21T10:08:24:
      the plan names an overlay that is not on disk
      fix: render the overlay, then re-run the step. To run the step
      anyway, whatever the cause: `ren edit demo --rerun select_reels`.
```

**The identity is the one the planner cache already computes** (`library/tools/planning_reuse.py`): the gathered inputs, the files they point at, the source identity, the step's code, its incoming DAG edges and the run mode. That is the assumption/state/dependency set the punchlist names, and there is one fingerprint function in the engine, not two. A change to any of them changes the identity, and the retry is allowed again.

**What is remembered is a DECISION, not a sample.** A refusal the engine's own contract decided is a function of the state. A failure that depends on what the MODEL answered is not: the model is resampled, so the same inputs can produce a different answer. The runner marks the attempt that asked the model (`present_llm_step`), and a refusal raised after the model answered is never recorded - `library/tools/post_bridge_retry.py` already owns that case, and it files the exhaustion so the next run READS why its answer was refused.

`--rerun <step>` is the way past the memo. The operator's word beats it, and it clears the memo.

## The measurement

`tests/scenarios/test_perf011_repeated_failure_costs_nothing.py` runs the pair twice: once with the memo answering the repeat, and once with it answering nothing, which is the behaviour the engine shipped before the memo existed.

```
PERF-011 repeated-failure measurement: preparation 1.0s per attempt
  before (no memo): 4.03s for two runs (2 preparations)
  after  (memo):    1.85s for two runs (1 preparation)
  saved:            2.17s (1.0s of preparation not paid again)
```

The preparation in the test stands in for the real one - a planning step's model call, a build's staging and placement - and the shape of the measurement is the same: the second attempt pays nothing.

## The paths found

| Path | What it did | Now |
|---|---|---|
| The edit runner's step loop (`library/processes/edit_video/run_pipeline.py`) | A refused step was re-derived on the next run, on the same inputs, with no memory of the refusal. | The refusal is remembered against the step's identity and the repeat is refused immediately, in both the worker path and the serial path. |
| The scratch-origin queue (`library/tools/resolve_scratch_projects.py`) | The caller's wait for the origin ran in silence for up to the instance lease's 900 s budget, so a lane that waited a quarter of an hour learned nothing about who held the machine. | The wait says who holds the origin and how long it has waited. |

The wait's PLACEMENT is upstream's (PERF-004): the wait no longer runs inside the broker's turn - the command refuses fast with its own exit code and the caller waits outside the turn. What is left for this lane is that the wait says what it is waiting for.

## Named, not wired

The reels BUILD path is the other place the punchlist measured the same waste - four attempts, each refused the same way. It is not wired here, and the reason is a fidelity one worth stating rather than a budget one.

A build refusal is not always a function of the files: the replace guard's refusal (`library/tools/reel_replace_guard.py`) is a DIFF between the plan and the approved timeline, and the approved timeline is live Resolve state. A memo keyed on the plan and the code would refuse a build that would now pass - because the captain fixed the timeline in Resolve, not the plan. The identity a build memo needs therefore has to carry the live read-back (`library/tools/reel_rebuild_need.py` computes `engine_code_digest`, `project_wide_digest` and the per-reel derivation digests) and the operation's own arguments (a `--allow-drop` declaration changes the answer). Wiring it is a lane of its own: the read has to happen BEFORE the staging, or the memo cannot be checked before the expensive part.
