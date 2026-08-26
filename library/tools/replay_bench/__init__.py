"""The step-replay bench: rebuild a step's prompt and context off frozen state.

Item 1 of the information-layer plan.  Every other item in that plan is a
claim about whether the model decides better, and "we cannot tell if it
helped" is the failure mode the work keeps hitting.  A full run plus a
Resolve render answers one such question; this answers the same class of
question in minutes, per step, while other workers hold the project.

It MEASURES.  It changes nothing about what a step computes or receives,
it never runs the pipeline, it never touches Resolve, and it never writes
to a project.  Nothing under `library/steps/` or `library/processes/` may
import it - `tests/test_replay_bench.py` fails if one does.

See docs/STEP_REPLAY_BENCH.md.
"""
