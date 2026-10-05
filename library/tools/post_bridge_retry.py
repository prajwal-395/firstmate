"""post_bridge_retry.py - a contract rejection reaching the model that caused it.

`present_llm_step` has a retry-with-feedback path: when a QA check fails
it appends "QA Feedback from previous attempt" to the context and asks
again.  A post-bridge rejection never reached it.  `PostBridgeError` is
raised from `run_hybrid_step` AFTER `present_llm_step` has returned, so
the whole mechanism was bypassed by the one class of failure that most
needs it - a violation of a contract the model was never shown.

PROVEN on the 29 Aug 2026 run of 001: both `mesh_spine` attempts logged
raw 247,336 -> projected 4,911 -> toon 4,070, a BYTE-IDENTICAL context.
The second answer differed because the model was RESAMPLED, not
corrected: it deliberated 16.5s against the first attempt's 106.7s and
landed 2.6s above a floor it still could not see.  The pipeline's
recovery from a contract violation was resampling until something passed,
at a full model call per attempt, on a step whose raw input is a quarter
of a million tokens.

This module is the message and the bound.  The plumbing is the QA path's
own - `present_llm_step` takes the text and seeds `current_context` with
it before the first attempt, so the violation is in the archived request
file the same way QA feedback is.

**The retry is BOUNDED**, per the captain's ruling.  `MAX_ATTEMPTS` is
the total number of model calls a post-bridge rejection may cause, the
first one included.  At the bound the step FAILS with `PostBridgeError`
carrying the last violation - it does not proceed on a best attempt, the
way the QA loop does, because a post-bridge rejection means the
downstream contract is unsatisfied and there is no partial output to
proceed with.

**What happens at the bound if the model keeps failing the same way.**
The context grows by one feedback block per attempt and no more: each
block is bounded by `MAX_VIOLATION_CHARS` and there are at most
`MAX_ATTEMPTS - 1` of them, so the worst case is a fixed, small addition
to a context measured in thousands of tokens.  The blocks accumulate
rather than replace deliberately - a model that failed twice the same way
should see that it did.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/post_bridge_retry.py`. `present_llm_step` owns a retry-with-feedback path, but `PostBridgeError` is raised from `run_hybrid_step` AFTER it returns, so the one failure class that most needs feedback bypassed it. On 001's 29 Aug run both `mesh_spine` attempts logged raw 247,336 -> projected 4,911 -> toon 4,070: a BYTE-IDENTICAL context. Recovery from a contract violation was resampling until something passed.
- The violation is carried into the retry context by the QA path's own plumbing - `present_llm_step(retry_feedback=...)` seeds `current_context` - so it reaches the archived request file the same way QA feedback does. **Extend that path; do not build a second one.**
- **The retry is BOUNDED at `MAX_ATTEMPTS` model calls**, and at the bound the step FAILS carrying the last violation rather than proceeding on a best attempt: a rejected post-bridge means the downstream contract is unsatisfied and there is no partial output to proceed with.
- Feedback blocks ACCUMULATE, each elided to `MAX_VIOLATION_CHARS` from the middle, so a model that failed twice the same way sees that it did and the context still grows by a fixed, small amount.
- `tests/scenarios/test_post_bridge_rejection_reaches_the_model.py`.
"""

from __future__ import annotations

from typing import Optional

# Total model calls one post-bridge rejection may cause, first included.
# Two retries after the first answer, the same bound the QA loop uses.
MAX_ATTEMPTS = 3

# The merge-data key carrying which post-bridge pass this is (1-based,
# set by `run_hybrid_step` before each post-bridge call). A post-bridge
# that turns RECORDED drops into a correction request on the first pass
# - and ships whatever still resolves on a later one - reads this
# instead of asking forever (finding 34: a dropped 4.03 entry never went
# back to the model, so a one-word slip silently removed the whole
# request). Same shape as `second_pass.PASS_KEY`: loop state the bridge
# needs to know it is answering a retry. Absent outside the runner (a
# direct invocation, the replay bench), where there is no retry path
# for a refusal to travel - so a post-bridge outside the loop ships
# recorded drops exactly as before.
ATTEMPT_KEY = "post_bridge_attempt"

# One violation's worth of text.  A post-bridge failure carries a
# subprocess stderr tail, which can be long; the model needs the reason,
# not the traceback.
MAX_VIOLATION_CHARS = 2000

# The heading the feedback block carries.  One spelling, here, so a test
# can look for the thing the model is shown rather than for a phrase
# somebody retyped.
HEADING = "Contract rejection from previous attempt"


def _elide(text: str, limit: int = MAX_VIOLATION_CHARS) -> str:
    """Keep both ends.  The cause of a rejection is as often at the tail
    as at the head, which is the lesson of #409's stderr truncation."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    half = (limit - 20) // 2
    return f"{text[:half]}\n  ... [elided] ...\n{text[-half:]}"


def feedback_block(violation: str, attempt: int) -> str:
    """The text appended to the retry context.

    Deliberately the same shape as the QA loop's block: one heading, the
    verbatim reason, one instruction.  Extending that path rather than
    building a second one is the point.
    """
    return (
        f"\n\n{HEADING} (attempt {attempt}):\n"
        f"Your previous answer was rejected by the step's post-bridge, "
        f"which enforces the contract the downstream steps read:\n"
        f"{_elide(violation)}\n"
        f"Answer again, correcting exactly this. Everything else about "
        f"the task is unchanged.\n"
    )


def carries_violation(context: str) -> bool:
    """True when a context has been seeded with a rejection."""
    return HEADING in (context or "")


def file_exhaustion(project_folder: str, step_id: str, violation: str, *,
                    attempts: int = MAX_ATTEMPTS) -> Optional[dict]:
    """File the exhaustion of the retry bound into the edit history.

    At the bound the step FAILS carrying the last violation, and without
    this filing the rejection dies with the run: the next run
    re-attempts the same contract violation with no memory of the
    previous exhaustion, at a full model call per attempt. The artifact
    is the step id; the reason is the last violation, elided; the
    rejecting party is Ren's own contract, not the captain. Never
    raises - the step has already failed, and a history write must not
    mask that failure.
    """
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_edit_history import record_rejection

    review_dir = str(ProjectLayout(str(project_folder)).read_dir(Area.REVIEW))
    elided = _elide(violation)
    return record_rejection(
        review_dir, str(step_id),
        reason=elided or "the post-bridge rejected every attempt",
        rejecting_party="ren",
        refs={"step": str(step_id), "attempts": int(attempts),
              "violation": elided},
        summary=(f"post-bridge rejected {attempts} attempt(s) for "
                 f"{step_id}"))
