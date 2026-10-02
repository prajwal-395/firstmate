"""Every requirement must be able to say no, and say what to run.

Walks the WHOLE registry, never a curated list: each requirement's own
witnesses must refuse (with a remedy) and pass (with a source). There is
no skip list; a requirement that cannot refuse is deleted, not exempted.
History: `docs/evidence/requirements.md` (tests section).
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


def _witness_failures(req) -> list:
    """Every way `req`'s two witnesses fail to prove it can refuse (with
    a remedy) and pass (with a source)."""
    out = []
    verdict = req.check(req.refuting_context())
    if not verdict.is_unsatisfied:
        return [f"{req.name} returned SATISFIED for its own declared "
                f"refuting context - the witness is wrong or it cannot "
                f"fail, and one that cannot fail is deleted, not kept"]
    if not verdict.reason.strip():
        out.append(f"{req.name} refuses without saying why")
    # Two honest shapes: it NAMES a step that would produce the missing
    # thing, or it CARRIES an instruction (environment, external index).
    names_a_producer = bool(verdict.produced_by)
    carries_a_remedy = len(verdict.reason) > len(req.describe)
    if not (names_a_producer or carries_a_remedy):
        out.append(f"{req.name} refuses naming no producer and carrying "
                   f"no remedy: {verdict.reason!r}")
    if req.kind == R.KIND_ENVIRONMENT and names_a_producer:
        out.append(f"{req.name} is environment yet names a producer")
    if req.kind == R.KIND_ENVIRONMENT and not carries_a_remedy:
        out.append(f"{req.name} refuses with no remedy beyond restating "
                   f"itself")
    passed = req.check(req.satisfying_context())
    if not passed.is_satisfied:
        out.append(f"{req.name} returned UNSATISFIED for its own declared "
                   f"satisfying context: {passed.reason}")
    elif passed.source not in R.SOURCES:
        out.append(f"{req.name} passed without saying how it passed")
    return out


def test_every_requirement_refuses_with_a_remedy_and_passes_with_a_source():
    """Both witnesses, for EVERY hand-written requirement: it can refuse,
    says what to run, and can pass saying how. A refusal naming no remedy
    leaves the operator nowhere to go - the old `gather_step_inputs`
    raised mid-run naming the missing key and no producer. Vacuous if
    the registry is empty, so that is asserted first."""
    assert HAND_WRITTEN, "the hand-written registry is empty"
    assert DERIVED, "no state_key requirements were derived from the DAG"
    assert INJECTED, (
        "nothing was derived for the hard inputs the runner supplies from "
        "outside the DAG, so `select_reels` cannot refuse for any reason")
    failures = [f for req in HAND_WRITTEN for f in _witness_failures(req)]
    assert failures == [], "\n  ".join(failures)


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
