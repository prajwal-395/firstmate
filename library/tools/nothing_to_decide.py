"""A model call the MATERIAL leaves nothing to decide is not made.

`model_task` already skips a call whose schema is empty - a property of
the DECLARATION (AGENTS.md 10.1, "A call with nothing to ask is not
made").  This is the other half: a call whose declaration asks for
something, on material that presents no decision.  Step 3.05 handed no
reel's words is the standing case.  Measured 2026-10-01 on a scratch
project: zero readable reels filed THREE handshakes (38,616 request
bytes), because the only honest answer - `readings: []` - fails the QA
loop as "semantically empty", is retried twice, and then ships as the
"best attempt" anyway.  The output was the same bytes either way.

The rule is narrow on purpose:

* **Only a pre-bridge declares it**, from its own deterministic
  evidence, with a REASON naming that evidence.  The runner never
  infers it, and nothing here guesses at what "too little to decide"
  means - that would be a creative floor (AGENTS.md 10.5).
* **Only where no answer could change execution.**  An empty table the
  post-bridge would refuse every entry against qualifies; a table that
  is merely small, or one a model could legitimately answer with
  nothing, does not - asking is how "nothing" gets DECIDED there.
* **Nothing is substituted.**  The post-bridge runs on an empty answer,
  which is the answer the step produces when no decision exists, and
  the reason is handed to it under `KEY` so the output SAYS the call was
  not made, beside the `model_call_skipped` run event.

Steps that declare it, and the evidence each reads:

    judge_reels (3.05)   `reels_to_read` has no row: every reading is
                         checked word for word against a reel it was
                         shown, so with none shown no reading can stand.
"""

from __future__ import annotations

KEY = "nothing_to_decide"
"""The pre-bridge output key carrying the reason, and the key the
post-bridge receives it under."""

EVENT = "model_call_skipped"


def declare(reason: str) -> dict:
    """What a pre-bridge merges into its output to stand the call down."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("nothing_to_decide needs a reason naming the "
                         "evidence; an unexplained skip reads as a "
                         "decision nobody made")
    return {KEY: reason}


def take(pre_output: dict) -> str | None:
    """Split the declaration out of a pre-bridge's output.

    Returns the reason, or None when the pre-bridge declared nothing.
    The key is REMOVED, so it never reaches a prompt or the step's
    stored output as though it were one of the step's own keys.
    """
    if not isinstance(pre_output, dict) or KEY not in pre_output:
        return None
    reason = pre_output.pop(KEY)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"a pre-bridge declared {KEY!r} with no reason "
                         f"({reason!r}); name the evidence")
    return reason
