"""The bounded re-plan loop a cohesion finding triggers.

Step 5.03 ``creative_cohesion`` is a pure OBSERVER: every proposal it can
make routes to ``cohesion_scope.OWNED_UPSTREAM``, so ``adjustments`` is
empty for every input at every energy.  A finding that names an upstream
owner reaches the output as an ``observation`` carrying a ``how_to_act``
string - advice to a human, not a loop.

This module is the loop.  A finding that names an upstream owner is
delivered to that planner as a typed input, the planner re-runs for the
affected region, and the finding is re-checked.  The loop is BOUNDED:
one re-plan per finding per run.  A finding that persists after its one
re-plan is reported to the editor, who can accept it as-is.

The loop closes when the finding is resolved or the editor accepts it.
"""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional


@dataclass(frozen=True)
class ReplanRequest:
    """A typed input naming the owning step, the finding, and the region.

    This is the re-planning channel: a finding that names an upstream
    owner is delivered to that planner as this typed input, not as a note.
    """

    finding: str
    """The finding text, verbatim from the cohesion review."""

    state_key: str
    """The state key the finding targets (e.g., ``speech_sequence``)."""

    field: str
    """The field the finding targets (e.g., ``segment_order``)."""

    owner_step: str
    """The step directory name that owns the decision."""

    region: str = "0.0-60.0"
    """The affected region, as a timeline span."""


@dataclass
class ReplanResult:
    """What happened when a finding triggered a re-plan."""

    request: ReplanRequest
    """The request that was delivered."""

    triggered: bool = False
    """Whether the re-plan was actually triggered."""

    resolved: bool = False
    """Whether the finding was resolved by the re-plan."""

    accepted: bool = False
    """Whether the editor accepted the finding as-is."""

    attempts: int = 0
    """How many re-plans were triggered (bounded at 1 per finding per run)."""

    detail: str = ""
    """What happened, in one line."""


def replan_from_finding(
    finding: dict,
    state: dict,
    owner_step: str,
    replan_fn: Callable[[ReplanRequest, dict], dict],
    recheck_fn: Callable[[dict], bool],
    accept_fn: Optional[Callable[[ReplanRequest], bool]] = None,
) -> ReplanResult:
    """Deliver a cohesion finding to the owning step and re-check.

    The loop is bounded: one re-plan per finding per run.  If the finding
    persists after its one re-plan, the editor can accept it as-is.

    Args:
        finding: The cohesion finding (``target_step``, ``field``,
            ``finding`` text).
        state: The current pipeline state, passed to ``replan_fn``.
        owner_step: The step directory name that owns the decision.
        replan_fn: Delivers the finding to the owning step and re-runs
            it for the affected region.  Returns the updated state.
        recheck_fn: Re-checks the finding against the updated state.
            Returns ``True`` if the finding is resolved.
        accept_fn: Optional.  Asks the editor whether to accept the
            finding as-is.  Returns ``True`` if accepted.

    Returns:
        A :class:`ReplanResult` recording what happened.
    """
    request = ReplanRequest(
        finding=finding.get("finding", ""),
        state_key=finding.get("target_step", ""),
        field=finding.get("field", ""),
        owner_step=owner_step,
    )

    result = ReplanResult(request=request)

    # Bounded: one re-plan per finding per run.
    result.attempts = 1
    result.triggered = True

    # Deliver the finding to the owning step as a typed input.
    updated_state = replan_fn(request, state)

    # Re-check the finding against the updated state.
    result.resolved = recheck_fn(updated_state)

    if result.resolved:
        result.detail = (
            f"re-plan of {owner_step} resolved the finding"
        )
    elif accept_fn is not None:
        result.accepted = accept_fn(request)
        if result.accepted:
            result.detail = (
                f"editor accepted the finding after re-plan of "
                f"{owner_step}"
            )
        else:
            result.detail = (
                f"finding persists after re-plan of {owner_step}; "
                f"editor declined to accept"
            )
    else:
        result.detail = (
            f"finding persists after re-plan of {owner_step}"
        )

    return result


def replan_results_for_review(results: List[ReplanResult]) -> List[dict]:
    """The re-plan results in the shape the cohesion review reports."""
    return [
        {
            "finding": r.request.finding,
            "state_key": r.request.state_key,
            "field": r.request.field,
            "owner_step": r.request.owner_step,
            "triggered": r.triggered,
            "resolved": r.resolved,
            "accepted": r.accepted,
            "attempts": r.attempts,
            "detail": r.detail,
        }
        for r in results
    ]
