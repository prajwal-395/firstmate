"""Item 4: one real edit through the composer and the oracle, no rebuild.

The captain's ask: a minor edit (the Reel 26 ending swap -
`logo_bulb_23976.mov` for `logo_bulb_lines_23976.mov` on V7, same span,
no played-length change) must go through the COMPOSER to the shortest
capability set, with preconditions checked against the LIVE TIMELINE
through the oracle, executing as a TOUCHUP - never a rebuild.

Reconciled post-fix form (vep-reconcile-the-two-gap-closers). PR #1324
pinned the gap OPEN with five tests; two lanes closed it from opposite
sides and this file records, assertion by assertion, what flipped:

1. TOUCHUP REGISTERED (`vep-ren-register-the-touchup-capability`):
   `reel.touchup` is now a registered operation owned by `build_reels`,
   its effect DERIVED in the requirement vocabulary like every other
   operation's (effect = `state.verify_reels.reel_build`, requires
   identical to its sibling `reel.build`). The old pin asserted no name
   contains "touch"; the new pin asserts the registration and derivation.
2. ORACLE SPEAKS A DECLARED NAME (both lanes; reconciled):
   `evaluate_precondition_against_live` now evaluates
   `state.verify_reels.reel_build` - the reel goal itself - against the
   live rows, the same picture-presence judgement as `rough_cut_exists`
   under a name the plans declare. The oracle lane
   (`vep-ren-oracle-speaks-a-declared-name`) additionally answers every
   OTHER declared name via its own requirement check; the touchup lane
   named the two live names `LIVE_PRECONDITIONS`. Both spellings resolve
   here (`LIVE_PRECONDITIONS == (LEGACY_PRECONDITION,) +
   LIVE_REQUIREMENTS`) with one three-case evaluation. The old pins
   asserted the oracle knew no plan name and raised on all six rebuild
   requires; the new pins assert it evaluates the reel goal live and
   answers rather than raising.

What is deliberately UNCHANGED, and pinned as such: free text is
still refused by name (a goal has to name a requirement - that
refusal is correct behaviour, not a defect), and the reel goal
still completes as the rebuild route (the touchup is chosen BY
NAME, never by rewriting the goal's default plan). No new
vocabulary was added on either side to close the gap.
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
#: requirement names it - and none was added to bridge it.
ENDING_SWAP_GOAL = "reel 26 ending swap"

#: The plan-declared name the oracle now speaks: the reel goal.
REEL_GOAL = "state.verify_reels.reel_build"


def test_ending_swap_goal_refuses_by_name():
    """The composer refuses the ending swap, naming what nothing produces.

    SURVIVES UNWEAKENED (was test 1 of the gap pin, PR #1324): closing
    the gap by teaching the composer a new vocabulary word would be
    prose in a costume. The touchup is reached by naming the capability
    (`operations.get("reel.touchup")`), not by phrasing free text.
    """
    comp = C.compose(ENDING_SWAP_GOAL)
    assert comp.refused
    assert comp.unknown_goal
    assert comp.blocker == ENDING_SWAP_GOAL
    assert (f"no capability produces {ENDING_SWAP_GOAL}"
            in comp.refusal_reason())


class _Item:
    def __init__(self, name, start, end):
        self._name = name
        self._start = start
        self._end = end

    def GetName(self):
        return self._name

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end

    def GetDuration(self):
        return self._end - self._start

    def GetUniqueId(self):
        return f"uid-{self._name}-{self._start}"

    def GetMediaPoolItem(self):
        return None

    def GetProperty(self):
        return {}

    def GetMarkers(self):
        return {}


class _Timeline:
    def __init__(self, items):
        self._items = items

    def GetName(self):
        return "Reel 26 - ending"

    def GetTrackCount(self, track_type):
        return 1 if track_type == "video" else 0

    def GetTrackName(self, track_type, index):
        assert (track_type, index) == ("video", 1)
        return "V1"

    def GetItemListInTrack(self, track_type, index):
        assert (track_type, index) == ("video", 1)
        return list(self._items)

    def SetCurrentTimeline(self, _timeline):  # pragma: no cover
        raise AssertionError("a read-only lane moved the cursor")


def _rows(*items):
    return Oracle.snapshot_live_rows(_Timeline(list(items)))


def test_oracle_evaluates_the_plan_name_against_the_live_cut():
    """The composed plan's live truth, answered from the screen.

    FLIPPED by `vep-ren-oracle-speaks-a-declared-name`: the gap pin
    (`test_oracle_refuses_every_precondition_the_rebuild_declares`)
    asserted each of `reel.build`'s requires RAISES
    `TimelineOracleError`. Now the plan-declared reel goal answers:
    records claim no built reel, the timeline shows one, and the
    oracle reports the screen winning over the paperwork - which is
    what the touchup's own precondition check calls.
    """
    expected = _rows()
    live = _rows(_Item("logo_bulb_23976.mov", 0, 72))
    evaluation = Oracle.evaluate_precondition_against_live(
        REEL_GOAL, expected, live)
    assert evaluation["precondition"] == REEL_GOAL
    assert evaluation["expected_picture"] is False
    assert evaluation["live_picture"] is True
    assert evaluation["satisfied_by_live_timeline"] is True
    assert evaluation["records_agree_with_live"] is False


def test_oracle_reports_an_unbuilt_reel_and_still_refuses_unknown():
    """Both halves of the fail-closed contract: an empty live timeline
    does not satisfy the goal, and a name nobody evaluates still
    raises rather than answering."""
    live = _rows()
    evaluation = Oracle.evaluate_precondition_against_live(
        REEL_GOAL, {}, live)
    assert evaluation["satisfied_by_live_timeline"] is False
    with pytest.raises(Oracle.TimelineOracleError):
        Oracle.evaluate_precondition_against_live(
            "reel 26 ending swap", {}, live)
