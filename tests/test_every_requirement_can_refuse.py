"""Every requirement must be able to say no, and say what to run.

Why this test exists
--------------------
A gate that cannot fail is worse than no gate, because it reads as
coverage (AGENTS.md 10.4).  This repository has already paid for that
lesson twice, and there is a third live instance:
`reel_conformance_verifier.py:1519` guards its caption check with
`if plan.captions and timeline.caption_items:`, `plan.captions` defaults
to `()` at `:177`, and `:1597` reports `captions_expected=0` - so an
empty expected side silently DISABLES the check and 762 real captions
pass verified by nothing.

Replacing 126 prose strings with executable requirements is exactly the
kind of change that could produce a fourth instance: a registry of
checks that all return SATISFIED because nobody ever fed them a state
they should refuse.

So this walks the WHOLE registry - never a curated list - and asserts of
each requirement that:

* there is a context in which it returns UNSATISFIED,
* that refusal carries a non-empty reason,
* that refusal names what would produce the missing thing, or is an
  environment requirement, where the remedy is in the reason instead,
* and there is a context in which it returns SATISFIED with a declared
  `source`.

The witnesses live on the `Requirement`, not here
--------------------------------------------------
`refuting_context` and `satisfying_context` are constructor arguments
with no defaults, so a requirement cannot be REGISTERED without them.
That is deliberate: a test that built witnesses centrally would be
silently outgrown by a new requirement nobody wrote one for, and would
still pass on the ones it knew.  The registration is the gate; this test
only reads it.

A requirement that genuinely cannot refuse is DELETED, not exempted.
There is no skip list in this file and there must not be one.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from library.tools import requirements as R


HAND_WRITTEN = list(R.registry())
DERIVED = list(R.derive_state_keys())
INJECTED = list(R.derive_runner_injected_keys())
"""The other derived half: the hard inputs no EDGE can carry. Walked in
full rather than sampled - there is one, its witnesses are AUTHORED
rather than generated from the DAG, and it is the only requirement in the
tree with no producer to fall back on."""


def _ids(reqs):
    return [r.name for r in reqs]


def test_the_registry_is_not_empty():
    """The whole test is vacuous if the registry is."""
    assert HAND_WRITTEN, (
        "the hand-written registry is empty, so every assertion below "
        "passes over nothing - which is the exact shape of the defect "
        "this file exists to catch")
    assert DERIVED, "no state_key requirements were derived from the DAG"
    assert INJECTED, (
        "nothing was derived for the hard inputs the runner supplies from "
        "outside the DAG, so `select_reels` is back to a contract that "
        "cannot refuse for any reason")


@pytest.mark.parametrize("req", HAND_WRITTEN, ids=_ids(HAND_WRITTEN))
def test_requirement_can_refuse(req):
    verdict = req.check(req.refuting_context())
    assert verdict.is_unsatisfied, (
        f"{req.name} returned SATISFIED for its own declared refuting "
        f"context. Either the witness is wrong or the requirement cannot "
        f"fail - and a requirement that cannot fail must be deleted, not "
        f"kept as coverage that is not there.")
    assert verdict.reason.strip(), (
        f"{req.name} refuses without saying why")


@pytest.mark.parametrize("req", HAND_WRITTEN, ids=_ids(HAND_WRITTEN))
def test_a_refusal_says_what_to_run(req):
    """A refusal that names no remedy leaves the operator nowhere to go.

    That was the old failure mode exactly: `gather_step_inputs` raised
    mid-run naming the missing key and no producer, so the answer to
    "what do I do now" was not in the message.
    """
    verdict = req.check(req.refuting_context())

    # Two honest shapes, and every refusal must be one of them:
    #  - it NAMES a step that would produce the missing thing, or
    #  - it CARRIES an instruction, for the things no step produces -
    #    an environment, or an index built outside the pipeline.
    # What is refused is a refusal that merely restates the requirement
    # and leaves the operator with nowhere to go, which is what the old
    # mid-run RuntimeError did.
    names_a_producer = bool(verdict.produced_by)
    carries_a_remedy = len(verdict.reason) > len(req.describe)

    assert names_a_producer or carries_a_remedy, (
        f"{req.name} refuses without naming a producer and without "
        f"carrying a remedy - the operator is told what is wrong and "
        f"nothing about what to do. Reason was: {verdict.reason!r}")

    if req.kind == R.KIND_ENVIRONMENT:
        assert not names_a_producer, (
            f"{req.name} is an environment requirement and cannot be "
            f"satisfied by running a step")
        assert carries_a_remedy, (
            f"{req.name} refuses with no remedy beyond restating itself")


@pytest.mark.parametrize("req", HAND_WRITTEN, ids=_ids(HAND_WRITTEN))
def test_requirement_can_pass_and_says_how(req):
    verdict = req.check(req.satisfying_context())
    assert verdict.is_satisfied, (
        f"{req.name} returned UNSATISFIED for its own declared "
        f"satisfying context: {verdict.reason}")
    assert verdict.source in R.SOURCES, (
        f"{req.name} passed without saying how it passed. A bare True is "
        f"what this type exists to prevent.")


@pytest.mark.parametrize("req", DERIVED[:40], ids=_ids(DERIVED[:40]))
def test_derived_state_keys_can_refuse(req):
    """The auto-derived half, sampled.

    Walked separately from the hand-written registry because its
    witnesses are GENERATED from the DAG rather than authored, so mixing
    them would bury the sixteen hand-written rows under ninety-odd
    trivially refutable ones. Sampled rather than exhaustive for the
    same reason - they are all the same shape by construction, and
    `test_requirements.py` pins that construction.
    """
    assert req.check(req.refuting_context()).is_unsatisfied
    assert req.check(req.satisfying_context()).is_satisfied


@pytest.mark.parametrize("req", INJECTED, ids=_ids(INJECTED))
def test_runner_injected_keys_can_refuse_and_say_what_to_do(req):
    """Both witnesses, plus the remedy - because there is no producer.

    Every other refusal in the tree can end with "run that step first".
    This one cannot: the transcript is written by a CLI tool that needs
    Resolve open, so the refusal has to carry the command itself or the
    operator is told what is wrong and nothing about what to do.
    """
    refusal = req.check(req.refuting_context())
    assert refusal.is_unsatisfied
    assert not refusal.produced_by, (
        f"{req.name} names a producer; no step writes this")
    assert len(refusal.reason) > len(req.describe), (
        f"{req.name} refuses by restating itself and names no producer, "
        f"so the operator has nowhere to go: {refusal.reason!r}")

    passed = req.check(req.satisfying_context())
    assert passed.is_satisfied, passed.reason
    assert passed.source in R.SOURCES


def test_no_requirement_is_registered_without_witnesses():
    """The constructor is the gate; this pins that it cannot be bypassed."""
    with pytest.raises(TypeError):
        R.Requirement(                      # type: ignore[call-arg]
            name="no.witnesses", kind=R.KIND_PREDICATE,
            describe="a requirement with no way to prove it can fail",
            produced_by=(), consumers=("scan",),
            check=lambda ctx: R.SATISFIED(R.MEASURED),
        )


def test_a_requirement_no_step_consumes_is_refused():
    """A requirement nothing can ever ask is the vacuous case."""
    with pytest.raises(ValueError, match="nothing can ever ask"):
        R.Requirement(
            name="nobody.wants.this", kind=R.KIND_PREDICATE,
            describe="orphan", produced_by=(), consumers=(),
            check=lambda ctx: R.SATISFIED(R.MEASURED),
            refuting_context=lambda: R.Context(),
            satisfying_context=lambda: R.Context(),
        )


def test_a_verdict_is_a_goal_only():
    """The verdict kind holds the opposite shape, enforced both ways.

    A verdict names the node that judges and NO consumers: the composer
    asks it as a goal, and no run ever asks it as a precondition.  A
    verdict with consumers would be asked before the judging step runs
    - the circular `sfx.index_loads` shape - and a verdict with no
    producer is unaskable even as a goal.  Neither registers.
    """
    with pytest.raises(ValueError, match="must name no consumers"):
        R.Requirement(
            name="verdict.consumed", kind=R.KIND_VERDICT,
            describe="a judgement a run refuses without",
            produced_by=("scan",), consumers=("scan",),
            check=lambda ctx: R.SATISFIED(R.MEASURED),
            refuting_context=lambda: R.Context(),
            satisfying_context=lambda: R.Context(),
        )
    with pytest.raises(ValueError, match="must name what produces"):
        R.Requirement(
            name="verdict.unproduced", kind=R.KIND_VERDICT,
            describe="a judgement nothing produces",
            produced_by=(), consumers=(),
            check=lambda ctx: R.SATISFIED(R.MEASURED),
            refuting_context=lambda: R.Context(),
            satisfying_context=lambda: R.Context(),
        )
    built = R.Requirement(
        name="verdict.produced", kind=R.KIND_VERDICT,
        describe="a judgement one node produces",
        produced_by=("scan",), consumers=(),
        check=lambda ctx: R.SATISFIED(R.MEASURED),
        refuting_context=lambda: R.Context(),
        satisfying_context=lambda: R.Context(),
    )
    assert built.produced_by == ("scan",)
    assert built.consumers == ()


def test_a_pass_cannot_invent_a_source():
    with pytest.raises(ValueError, match="not one of the declared"):
        R.SATISFIED("because I said so")


def test_a_refusal_cannot_be_empty():
    with pytest.raises(ValueError, match="must say why"):
        R.UNSATISFIED("   ")


def test_the_held_table_is_honest():
    """Whatever `UNAUTHORISED` holds must NOT be built, and whatever it
    does not hold must not be pretended to be held.

    This used to pin `rough_cut.approved` as unbuilt while the captain's
    question was open. It is now built, by ruling, so the assertion is
    the general one: the table and the registry may not both claim the
    same name.
    """
    built = {r.name for r in R.all_requirements()}
    both = built & set(R.UNAUTHORISED)
    assert both == set(), (
        f"these are registered AND recorded as held pending a decision, "
        f"which cannot both be true: {sorted(both)}")
    for name, note in R.UNAUTHORISED.items():
        assert len(note) > 40, f"{name} is held with no stated reason"
