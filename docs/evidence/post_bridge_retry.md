# Post-bridge rejection reaches the model - test history

Moved from `tests/test_post_bridge_rejection_reaches_the_model.py` when the suite was halved (2026-10-02).

## The byte-identical retry (module docstring)

A post-bridge rejection reaches the model that caused it.

PROVEN on the 29 Aug 2026 run of 001: both `mesh_spine` attempts logged
raw 247,336 -> projected 4,911 -> toon 4,070, a BYTE-IDENTICAL context.
`present_llm_step` owns a retry-with-feedback path, but `PostBridgeError`
is raised from `run_hybrid_step` after it has already returned, so the
one class of failure that most needs the feedback bypassed it entirely.
The second answer differed because the model was RESAMPLED, not
corrected - 16.5s of deliberation against the first attempt's 106.7s,
landing 2.6s above a floor it still could not see.

These tests drive the real `run_hybrid_step` against a real failing
post-bridge and read the ARCHIVED REQUEST FILE - the bytes the model is
handed - rather than a captured argument.  That is the exact artefact the
29 Aug run proved was identical across attempts.

## The stray answerer thread (`_answerer` docstring)

Answer up to `len(answers)` agent requests, then STOP - loudly.

The surplus answers are deliberate: the bounded-retry tests offer MORE
answers than the step may consume, to prove the step stops asking on
its own.  But a thread that never meets its last request used to spin
on `time.sleep(0.05)` until its 120 s deadline - minutes after its own
test, and file, had finished.  At suite scale that stray was still
alive inside LATER agent-stub tests, which patch the GLOBAL
`time.sleep`: every stray sleep became a rewrite of the later test's
own response file, concurrent with that test's read, and a read
landing between truncate and write parsed as empty.  Measured
2026-09-15 as `LLMError: Failed to read or parse agent LLM response
as JSON` in `test_project_declared_creative_tasks.py` at suite scale
only (12/12 in isolation).  So the thread's lifetime ends HERE, owned
by the test that started it: call the returned stopper in a `finally`.
