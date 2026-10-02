# Step 3.04 `select_reels` - why it reaches a model

Moved verbatim on 2026-10-02 from `tests/unit/reels/test_reel_selection.py`
when the test file kept only its invariant. The handoff-wording tests that
pinned the captain's definition of a reel, the measurements-not-scores line
and judging overlap on meaning (not seconds) were removed as prompt-wording
pins; the wording itself lives in
`library/steps/step_3_04_select_reels/handoff.md`.

## Module docstring

```text
Reel selection reaches a MODEL, and says which craft it is.

The defect these tests were written against, measured 2026-09-04: reel
selection was `reel_proposal.py` plus `reel_exchange.py`, which between
them had no model call, no handoff and no craft role, and was absent from
`undetermined.DECLARING_STEPS`. A crewmate chose the sixteen
conversations in its own turn and committed a validator around the
result.

The root cause is the part worth keeping: **every guard in this engine
aims at the ENGINE inventing taste and none at a WORKER supplying it**,
so a judgement that arrives already made passes every gate. These tests
are the one that looks the other way - they ask whether a model is
actually reached, not whether the engine holds a constant.

An earlier version of this file used `xfail` for the unbuilt fix.
`tests/tooling/test_no_unfailable_tests.py` refused it, correctly: an xfail for
work nobody has started cannot fail. Section 15 of
`docs/FIELD_TEST_PODCAST_FINDINGS.md` carries the specification.
```

## Comment blocks

```text
# ── The step can actually RUN ────────────────────────────────────────
#
# Caught in review, and it is the same shape as the defect it was built
# to fix: step_3_04_select_reels was registered in DECLARING_STEPS, given
# a craft role, and asserted in its own docs to reach a model - while the
# directory held nothing but manifest.json. No handoff means no System
# Context and no Task Prompt, so the role had nothing to prepend to and
# nothing told the model what it was doing.

# ── The format, as the captain defined it ────────────────────────────
#
# These pin the parts of the definition that were ANSWERED rather than
# inferred. Each one was missing at some point and cost a review round:
# the opening was never mentioned to the model at all, the transcript
# being garbled was read as a bad reel rather than a bad passage, and
# both the CTA and overlap rules had been written as prohibitions the
# captain does not hold.
```
