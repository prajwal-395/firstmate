"""What the cohesion review can ACT on where it runs, and what it can only OBSERVE.

`creative_cohesion` (step 5.03) is the next-to-last step of the edit.  Every
plan it reads - the transitions, the SFX, the VFX, the grade - is made in
steps 4.02 through 5.01, so the review cannot be moved upstream of the
decisions it reviews: it would arrive before its own inputs exist.  What it
CAN do is stop presenting a recommendation it has no route to apply as
though it were one.

On project 001's 2026-08-26 run the step emitted exactly one adjustment,
`speech_sequence.segment_order`, and `compile_manifest` refused it:
re-ordering the narrative would invalidate every downstream timing.
`applied_adjustments` was empty on that run and on every run before it.
A recommendation nobody can apply, reported in an array named
`adjustments`, reads as a change that was made.

So there are two lists, and a finding is in one or the other:

- `ACTIONABLE_AT_COHESION` is the (state key, field) pairs the manifest
  compiler really rewrites.  An entry here is a promise that
  `apply_cohesion_adjustments` has a branch that applies it, which
  `tests/test_cohesion_scope.py` checks by driving the real function.
  This is AGENTS.md section 10.2 - a capability is only real where the
  reader reads it - applied to the review's own output.
- `OWNED_UPSTREAM` is what the review may state and may not change, each
  naming the STEP that owns the decision, why the compiler refuses it,
  and the re-run that would act on it.  These reach the output as
  `observations`, never as `adjustments`.

A pair in neither list is UNKNOWN and raises here, so a new finding has to
say which side it is on rather than silently becoming an adjustment that is
then silently dropped.

**Step 5.03 is a pure OBSERVER today, and that is deliberate.**  Every
proposal it can still make - the engagement one - routes to
`OWNED_UPSTREAM`, so `adjustments` is empty for every input at every
energy, not only at the `moderate` project 001 declares (issue #272).  The four findings that
fed this route - two transition-duration adjustments and two SFX-density
observations - were creative thresholds the step chose ("high energy
means every transition under 500 ms, so make it 10 frames"), and they
were removed rather than re-tuned: there is no declared pace or density
to derive a replacement from, and inventing one is what AGENTS.md 10.5
forbids.  So `ACTIONABLE_AT_COHESION` currently has an applier and no
producer.

The two lists and the raise are kept because they are the GUARD, not the
finding: they are what stops the next check that is added from becoming
an adjustment nothing applies.  Do not read an always-empty `adjustments`
array as a clean bill of health on the edit - it means nothing proposed
anything.  `tests/test_cohesion_scope.py` drives the applier directly so
the branch stays exercised.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple


class UnknownCohesionFinding(KeyError):
    """A (state key, field) pair neither list declares.

    Raised rather than defaulted: the whole point of the two lists is that
    the review cannot emit a recommendation without saying whether it can
    be applied where it runs.
    """


@dataclass(frozen=True)
class Actionable:
    """A finding the manifest compiler applies without moving a frame."""

    applier: str
    """`module.function` that really rewrites it.  Evidence, so the claim
    can be driven rather than believed."""

    effect: str
    """What changes in the finished video when it is applied."""


@dataclass(frozen=True)
class OwnedUpstream:
    """A finding whose decision belongs to a step that has already run."""

    owner_step: str
    """The step directory name that owns the decision."""

    reason: str
    """Why it cannot be applied at compile time.  This is the sentence
    `apply_cohesion_adjustments` prints when it refuses."""

    how_to_act: str
    """The re-run that would actually change it, for a reader who decides
    the observation is worth acting on."""


# ── What can be applied where the review runs ────────────────────────
#
# One entry.  A transition's duration is a number the compiler rewrites in
# the spec before the manifest is written, and the Fusion pass draws the
# transition from that number - so it changes the picture without moving a
# cut point, a subtitle or a clip boundary.

ACTIONABLE_AT_COHESION: Dict[Tuple[str, str], Actionable] = {
    ("transition_spec", "duration_frames"): Actionable(
        applier="library/steps/step_5_04_compile_manifest/step.py"
                ":apply_cohesion_adjustments",
        effect="rewrites the transition's duration in the spec before the "
               "manifest is written; the Fusion pass draws the transition "
               "from that number, and no cut point, clip boundary or "
               "subtitle timing moves",
    ),
}


# ── What the review may state and may not change ─────────────────────

OWNED_UPSTREAM: Dict[Tuple[str, str], OwnedUpstream] = {
    ("sfx_spec", "density"): OwnedUpstream(
        owner_step="step_4_04_plan_sfx",
        reason="SFX are chosen and placed in step_4_04_plan_sfx; the "
               "manifest compiler cannot add or remove SFX",
        how_to_act="manage_project.py run <project> --rerun plan_sfx",
    ),
    ("speech_sequence", "segment_order"): OwnedUpstream(
        owner_step="step_2_02_speech_sequence",
        reason="re-ordering the narrative would invalidate every "
               "downstream timing; it belongs in step_2_02",
        # Not `--rerun speech_sequence`: re-ordering the passages
        # invalidates the spine, the assignments, every plan and every
        # rendered overlay, all of which are timed off timeline
        # positions.  `--rerun edit` discards exactly the edit ledger and
        # is structurally incapable of touching the preflight
        # enrichment (AGENTS.md section 3).
        how_to_act="manage_project.py run <project> --rerun edit",
    ),
}


def _key(target_step: str, field: str) -> Tuple[str, str]:
    return (str(target_step or ""), str(field or ""))


def is_actionable(target_step: str, field: str) -> bool:
    """Does the manifest compiler have a branch that applies this?"""
    return _key(target_step, field) in ACTIONABLE_AT_COHESION


def owner_of(target_step: str, field: str) -> OwnedUpstream:
    """The step that owns this decision, or None when it is actionable.

    Raises `UnknownCohesionFinding` for a pair neither list declares.
    """
    key = _key(target_step, field)
    if key in ACTIONABLE_AT_COHESION:
        return None
    if key in OWNED_UPSTREAM:
        return OWNED_UPSTREAM[key]
    raise UnknownCohesionFinding(
        f"creative_cohesion produced a finding about "
        f"{key[0]}.{key[1]}, which is in neither "
        f"cohesion_scope.ACTIONABLE_AT_COHESION nor OWNED_UPSTREAM. "
        f"Declare which it is: a finding the manifest compiler applies, "
        f"or one that names the step that owns the decision."
    )


def refusal_reason(target_step: str, field: str) -> str:
    """The sentence the compiler prints when it declines a finding.

    Sourced here rather than written twice, so the reason the review gives
    a reader and the reason the compiler logs cannot drift apart.  An
    UNKNOWN pair gets a refusal too - the compiler is the last line and
    must never drop an adjustment silently - but this function is not
    where an unknown pair is legitimised, and `owner_of` still raises.
    """
    key = _key(target_step, field)
    if key in ACTIONABLE_AT_COHESION:
        return ""
    if key in OWNED_UPSTREAM:
        return OWNED_UPSTREAM[key].reason
    return "no consumer in the manifest compiler"


def as_observation(proposal: dict) -> dict:
    """One upstream-owned finding, in the shape the review reports it.

    Carries no `suggested_value`.  The withdrawn shape offered
    `"front_loaded"`, which is a word this step invented about an
    ordering it never computed - a creative value substituted for a
    decision no step made (AGENTS.md section 10.5).  What an observation
    states is WHAT was found, WHO owns it and HOW to act on it.
    """
    target_step = proposal.get("target_step")
    field = proposal.get("field")
    owner = owner_of(target_step, field)
    return {
        "finding": proposal.get("finding", ""),
        "state_key": target_step,
        "field": field,
        "owner_step": owner.owner_step,
        "reason": owner.reason,
        "how_to_act": owner.how_to_act,
    }


def split(proposals) -> Tuple[List[dict], List[dict]]:
    """Route every finding to `adjustments` or to `observations`.

    Returns `(adjustments, observations)`.  Every proposal reaches exactly
    one of them or raises - there is no third outcome, which is what stops
    a finding being emitted as an adjustment and then dropped without a
    word.
    """
    adjustments: List[dict] = []
    observations: List[dict] = []
    for proposal in proposals or []:
        if is_actionable(proposal.get("target_step"), proposal.get("field")):
            adjustments.append(proposal)
        else:
            observations.append(as_observation(proposal))
    return adjustments, observations
