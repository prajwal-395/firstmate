"""Item 4: one real edit through the composer and the oracle, no rebuild.

The captain's ask: a minor edit (the Reel 26 ending swap -
`logo_bulb_23976.mov` for `logo_bulb_lines_23976.mov` on V7, same span,
no played-length change) must go through the COMPOSER to the shortest
capability set, with preconditions checked against the LIVE TIMELINE
through the oracle, executing as a TOUCHUP - never a rebuild.

Finding, measured 2026-09-23: the three merged items do not compose
into that ask yet. Each half works; they share no joint.

1. THE COMPOSER CANNOT RESOLVE THIS GOAL. The ending swap names no
   requirement in the vocabulary `Operation.requires` and
   `Operation.effect` share, so any honest phrasing refuses as
   unknown - "no capability produces ...". The touchup mechanism
   (`library/tools/reel_touchup.py`, `manage_project.py touch-reel`)
   is not a registered operation, so no goal can resolve to it.
2. THE ONLY REEL GOAL RESOLVES TO THE REBUILD. The one goal that
   writes a reel, `state.verify_reels.reel_build`, completes with
   exactly `("reel.build",)`. Executing the composed plan for this
   edit would be the rebuild route - the exact thing the project
   exists to stop - so this test pins that the composer offers no
   touchup route rather than executing it.
3. THE ORACLE SPEAKS NO PRECONDITION ANY PLAN DECLARES. Its one
   live precondition, `rough_cut_exists`, is not in the requirement
   vocabulary, and it raises on every name `reel.build` actually
   requires - so no composed plan's preconditions can be checked
   against the live timeline through it today.

These tests pin the gap, not the demo. When a touchup operation is
registered with its effect in the requirement vocabulary, and the
oracle evaluates a name the plans declare, test 1 must resolve and
test 3 must evaluate - that is the day item 4 passes. Until then a
demo that reaches the touchup by hand proves nothing, and per the
brief it is not attempted here: no Resolve was touched, no timeline
was built beside his, and nothing was staged.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import composer as C
from library.tools import operations as O
from library.tools import requirements as R
from library.tools import timeline_oracle as Oracle

#: The goal as phrased to the composer for this task: the Reel 26
#: ending swap, stated plainly. It names no requirement, because no
#: requirement names it.
ENDING_SWAP_GOAL = "reel 26 ending swap"


def test_ending_swap_goal_refuses_by_name():
    """The composer refuses the ending swap, naming what nothing produces.

    The refusal IS the finding: routing around it by hand-writing the
    capability sequence would prove nothing, so this pins the refusal
    instead of bridging it.
    """
    comp = C.compose(ENDING_SWAP_GOAL)
    assert comp.refused
    assert comp.unknown_goal
    assert comp.blocker == ENDING_SWAP_GOAL
    assert (f"no capability produces {ENDING_SWAP_GOAL}"
            in comp.refusal_reason())


def test_no_registered_operation_names_the_touchup():
    """`reel_touchup.apply_touchup` has no capability, so no goal reaches it.

    The composer plans capabilities - registered operations - only.
    Until the touchup mechanism is declared as one with its effect in
    the requirement vocabulary, resolving any edit to it is
    unreachable by construction, not by oversight.
    """
    assert not [name for name in O.names() if "touch" in name]


def test_only_reel_goal_resolves_to_the_rebuild():
    """The one reel-writing goal completes - as the rebuild, not a touchup.

    Executing this plan for the ending swap would be a correct
    timeline by the wrong route, which the brief counts as failure of
    the capability. Pinned here so a future touchup registration
    visibly changes what this goal resolves to.
    """
    comp = C.compose("state.verify_reels.reel_build")
    assert comp.completed
    assert comp.operations == ("reel.build",)


def test_oracle_precondition_is_outside_the_requirement_vocabulary():
    """`rough_cut_exists` is live-readable but no plan declares it.

    The oracle and the composer share no names: the vocabulary holds
    108 requirements and the oracle's one precondition is not among
    them, so preconditions cannot be evaluated against the live
    timeline through it - only against what a plan says should hold.
    """
    names = {req.name for req in R.all_requirements()}
    assert "rough_cut_exists" not in names


def test_oracle_refuses_every_precondition_the_rebuild_declares():
    """The oracle cannot check any precondition of the composed plan.

    `reel.build` requires six names; the oracle evaluates exactly one
    name, and it is none of them. Each of the six raises rather than
    answering, because a check that does not exist is not a check
    that passes.
    """
    op = next(op for op in O.all() if op.name == "reel.build")
    assert len(op.requires) > 0
    for req in op.requires:
        with pytest.raises(Oracle.TimelineOracleError):
            Oracle.evaluate_precondition_against_live(req.name, {}, {})
