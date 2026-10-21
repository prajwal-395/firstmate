"""One enumeration: the refusal that already happened.

A lane that refuses a step and is then re-run re-derives the same
decision from the same inputs, and pays for the derivation again. The
brief for PERF-011 measured that: a planning step that refused was
reconstructed for 3,220 s on the next attempt, and four build attempts
each refused the same way, each one redoing the work the refusal had
already decided was pointless.

This module is the memory that stops the second payment. It holds a
REFUSAL - a decision, with a cause and a fix - against the identity of
the state it was decided on. The next attempt on the SAME identity is
refused immediately, with the cause named, and the step is not run.

**What is remembered is a decision, not a sample.** A refusal decided
by the engine's own contract - a missing input, an unsatisfied
requirement, a plan the step cannot honour - is a function of the
state, and the state is what the identity covers. A failure that
depends on what the MODEL answered is not: the model is resampled, so
the same inputs can produce a different answer and a different
outcome. The caller says which it is by telling the runner whether the
attempt reached the model (`library/processes/edit_video/run_pipeline.py`
knows), and a refusal raised after the model answered is never recorded
here. `library/tools/post_bridge_retry.py` owns that case already: it
files the exhaustion so the next run READS why its answer was refused.

**The identity is the one the planner cache already computes**
(`library/tools/planning_reuse.py`): the gathered inputs, the files they
point at, the source identity, the step's code, its incoming DAG edges
and the run mode. That is the assumption, state and dependency set the
punchlist names, and it is already the answer to "would this attempt
see the same world?". A memo without that witness cannot be matched,
and a memo that cannot be matched is a claim nobody can check, so it is
not written.

**Where it lives:** the project state, under `STATE_KEY`, beside the
planner cache's own record. It is project-scoped because the decision
was about this project's files, and it survives a run because the point
is to change the NEXT run.

One enumeration: `library/tools/retry_guard.py`.
"""

from __future__ import annotations

import time
from typing import Any

from library.tools.ren_refusal import RenRefusal

STATE_KEY = "retry_guard"
"""The key in `pipeline_data.json` that holds the memo."""

FORMAT = "retry_guard/1"
"""The record's format. A record in another format is a miss, never a match."""

MAX_ENTRIES = 64
"""How many refusals one project remembers. The oldest goes first: a
project with more than sixty-four refused steps is not served by a
ledger nobody reads."""


class RepeatedRefusal(RenRefusal):
    """The refusal that was already decided, refused again for free.

    It carries the ORIGINAL cause and fix - the reason the step refused
    is the reason it refuses now - and adds the way past it, so the
    person reading it can either fix the cause or force the step to run
    again.
    """

    CODE = "REN_REPEATED_REFUSAL"
    RECOVERY_CLASS = "blocked"
    USER_MESSAGE = (
        "This step refused before on exactly these inputs, and nothing "
        "relevant has changed since, so it was not run again. The cause "
        "is below.")


def _refusal_fields(refusal: Any) -> dict | None:
    """The four fields a memo entry needs, from a refusal or its dict."""
    if isinstance(refusal, RenRefusal):
        payload = refusal.to_dict()
    elif isinstance(refusal, dict):
        payload = refusal
    else:
        return None
    fields = {name: str(payload.get(name) or "").strip()
              for name in ("code", "what", "why", "fix")}
    if not all(fields.values()):
        return None
    return fields


def _entries(state: dict) -> dict:
    """The memo, as a dict of operation -> entry."""
    if not isinstance(state, dict):
        return {}
    memo = state.get(STATE_KEY)
    if not isinstance(memo, dict):
        return {}
    return {key: value for key, value in memo.items()
            if isinstance(value, dict)}


def record(state: dict, operation: str, identity: str, refusal: Any,
           seconds: float | None = None) -> bool:
    """Remember a refusal against the identity it was decided on.

    Returns whether it was recorded. A refusal with no identity is not
    recorded: without the witness nothing can match it, and a memo that
    cannot be matched is a claim nobody can check. A refusal with no
    cause or no fix is not recorded either - the next attempt has to be
    able to say WHY, and a memo that cannot say why is worse than no
    memo, because it reads like a decision.

    `seconds` is what the failed attempt cost, so the repeat can say
    what it did not pay again.
    """
    if not isinstance(state, dict) or not operation or not identity:
        return False
    fields = _refusal_fields(refusal)
    if fields is None:
        return False
    memo = state.get(STATE_KEY)
    if not isinstance(memo, dict):
        memo = {}
    entry = {
        "format": FORMAT,
        "operation": str(operation),
        "identity": str(identity),
        **fields,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if seconds is not None:
        try:
            entry["seconds"] = round(float(seconds), 3)
        except (TypeError, ValueError):
            pass
    memo[str(operation)] = entry
    if len(memo) > MAX_ENTRIES:
        for key in sorted(memo, key=lambda name: memo[name].get(
                "recorded_at", ""))[:len(memo) - MAX_ENTRIES]:
            memo.pop(key, None)
    state[STATE_KEY] = memo
    return True


def entry(state: dict, operation: str) -> dict | None:
    """The refusal remembered for one operation, or None."""
    return _entries(state).get(str(operation))


def repeated(state: dict, operation: str, identity: str) -> dict | None:
    """The remembered refusal for this operation on THIS identity.

    None means the attempt may run: either nothing was remembered for
    the operation, or what was remembered was decided on a different
    state - a changed input, a changed file, changed code, a changed run
    mode. That difference is the licence to try again.
    """
    if not identity:
        return None
    found = entry(state, operation)
    if not found:
        return None
    if found.get("format") != FORMAT:
        return None
    if found.get("identity") != identity:
        return None
    return found


def refusal_for(operation: str, memo: dict, rerun: str = "") -> RenRefusal:
    """The refusal a repeated failure is answered with.

    It names the cause that was already decided and the way past it:
    fix the cause, or force the step to run again on purpose.
    """
    memo = memo or {}
    cost = memo.get("seconds")
    paid = ""
    if isinstance(cost, (int, float)) and cost > 0:
        paid = (f" The attempt it replaces cost {cost:.1f}s of preparation, "
                f"and this one cost none.")
    fix = str(memo.get("fix") or "").strip()
    if rerun:
        if fix and not fix.endswith((".", "!", "?")):
            fix += "."
        fix = (f"{fix} To run the step anyway, whatever the cause: "
               f"`{rerun}`.").strip()
    return RepeatedRefusal(
        what=(f"{operation} refused before on exactly these inputs, code "
              f"and run mode, and nothing relevant has changed since "
              f"{memo.get('recorded_at') or 'the last attempt'}: "
              f"{memo.get('what') or 'the recorded refusal'}"),
        why=(f"the same refusal was already decided on this step's identity "
             f"- the gathered inputs, the files they point at, the step's "
             f"code and the run mode are unchanged - so running it again "
             f"would decide the same thing and pay for the preparation "
             f"again. The cause was: {memo.get('why') or 'not recorded'}."
             f"{paid}"),
        fix=fix or f"resolve the cause above, then re-run {operation}.",
        code="REN_REPEATED_REFUSAL",
    )


def forget(state: dict, operation: str) -> None:
    """Drop the remembered refusal once the step has succeeded.

    A refusal that has been resolved is not a decision any more, and a
    memo that outlives its cause would refuse a step that now works.
    """
    memo = state.get(STATE_KEY)
    if isinstance(memo, dict):
        memo.pop(str(operation), None)
        if not memo:
            state.pop(STATE_KEY, None)
