"""A comp the Fusion pass did not plan is SAID, on the item it landed on.

Resolve can create an empty "Composition 1" when the Fusion page opens.
The pass must avoid that page switch, and its stray report remains the
tripwire for any unplanned comp that still appears.
"""

from library.tools.execution.apply_fusion_comps import (
    comp_census,
    stray_comps,
)
from tests.resolve_double import FakeComp, FakeTimeline, FakeTimelineItem, FakeTool


def _empty():
    # What Resolve's own comp exported as: the media pair and two
    # AudioDisplay tools, none of which draws.
    return FakeComp({"MediaIn1": FakeTool("MediaIn"),
                     "MediaOut1": FakeTool("MediaOut"),
                     "Left": FakeTool("AudioDisplay"),
                     "Right": FakeTool("AudioDisplay")})


def _drawing():
    return FakeComp({"MediaIn1": FakeTool("MediaIn"),
                     "Merge1": FakeTool("Merge"),
                     "MediaOut1": FakeTool("MediaOut")})


def _place(timeline, track, start):
    item = FakeTimelineItem(f"item@{start}", timeline, start=start)
    timeline._ensure_track("video", track)[1].append(item)
    return item


def test_an_unplanned_comp_is_said_and_one_that_draws_is_not_called_empty():
    timeline = FakeTimeline()
    picture = _place(timeline, 1, 0)       # the pass's own target
    card = _place(timeline, 6, 1619)       # where Resolve left its comp
    header = _place(timeline, 7, 0)        # an unplanned comp that draws
    before = comp_census(timeline)

    picture._fusion_comps.append(_drawing())
    card._fusion_comps.append(_empty())
    header._fusion_comps.append(_drawing())

    notes = stray_comps(timeline, before, {picture.GetUniqueId()})

    assert len(notes) == 2
    assert any("V6 @1619" in note and "draws nothing" in note
               for note in notes)
    assert any("V7 @0" in note and "it draws (['Merge'])" in note
               for note in notes)
