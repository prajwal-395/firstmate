"""One real edit through the composer and the oracle, no rebuild.

A minor edit (the Reel 26 ending swap) is reached by naming the touchup
capability, never by teaching the composer free text; the oracle answers
the reel goal against the LIVE timeline and fails closed on a name it
does not evaluate. History: `docs/evidence/composer.md`.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from library.tools import composer as C
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


def test_oracle_answers_the_reel_goal_off_the_screen_and_fails_closed():
    """Records claim no built reel, the timeline shows one: the screen
    wins over the paperwork (the touchup's own precondition check). An
    empty live timeline does not satisfy the goal, and a name nobody
    evaluates still raises rather than answering."""
    expected = _rows()
    built = _rows(_Item("logo_bulb_23976.mov", 0, 72))
    evaluation = Oracle.evaluate_precondition_against_live(
        REEL_GOAL, expected, built)
    assert evaluation["precondition"] == REEL_GOAL
    assert evaluation["expected_picture"] is False
    assert evaluation["live_picture"] is True
    assert evaluation["satisfied_by_live_timeline"] is True
    assert evaluation["records_agree_with_live"] is False

    live = _rows()
    evaluation = Oracle.evaluate_precondition_against_live(
        REEL_GOAL, {}, live)
    assert evaluation["satisfied_by_live_timeline"] is False
    with pytest.raises(Oracle.TimelineOracleError):
        Oracle.evaluate_precondition_against_live(
            "reel 26 ending swap", {}, live)
