# Run status

History moved out of test module docstrings; the tests keep the invariant.

## `tests/unit/reels/test_a_stranded_failure_does_not_decide_the_status.py` (moved from its module docstring, 2026-10-02)

A recorded failure of a step the pipeline no longer runs.

`failed_steps` is current state, not a log (AGENTS.md section 3): an
entry is removed when that step SUCCEEDS.  So a recorded failure naming
a step with no DAG node can never be cleared - the step never runs, and
the only code path that clears an entry is unreachable for it.

That is exactly what unwiring `prosody_analysis` (#F5,
docs/PROSODY_MEASURED.md) left on project 001, whose `failed_steps` reads
`['validate', 'prosody_analysis']`.  Holding every future run of that
project at FAILED on a step this pipeline has stopped running is not a
verdict about the run.

So the runner PARTITIONS the recorded failures against the DAG's own
nodes.  The stranded half is reported by name, first, saying no run can
clear it - never dropped, because going quiet about a recorded failure is
the trap `failed_steps` exists to avoid - and it does not decide
`status`.
