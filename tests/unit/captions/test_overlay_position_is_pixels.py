"""An overlay's position is carried on Pan/Tilt Resolve actually holds.

See `docs/evidence/overlay_position.md` for the incident and root cause analysis.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import overlay_placement, tight_box  # noqa: E402
from library.tools.overlay_mode import (  # noqa: E402
    OVERLAY_CARRIAGE,
    resolve_overlay_geometry,
)
from library.tools.overlay_placement import (  # noqa: E402
    apply_placement_transform,
    place_overlay_segment,
)
from library.tools.tight_box import (  # noqa: E402
    TightBoxMismatch,
    placement_for_box,
    placement_holds,
)

FULL_W, FULL_H = 1080, 1920

#: What step 4.05 ASKED Resolve for, and what Resolve HELD - read off
#: the live timeline on 2026-09-09 (Resolve 21, project "Podcast (field
#: test)", both Reel 09 timelines at 1080x1920) against
#: `4_05_render_subtitles/render_ledger.json`. `(canvas h, asked, held)`.
SHIPPED_AND_HELD = [
    (152, -7578.9473684210525, -3840.0),
    (158, -7254.683544303798, -3840.0),
    (160, -7152.0, -3840.0),
    (164, -6954.1463414634145, -3840.0),
    (166, -6858.795180722892, -3840.0),
    (220, -4939.636363636363, -3840.0),
    (224, -4834.285714285714, -3840.0),
    (232, -4634.48275862069, -3840.0),
    (236, -4539.661016949152, -3840.0),
    (242, -4403.305785123967, -3840.0),
    (244, -4359.3442622950815, -3840.0),
    (246, -4316.09756097561, -3840.0),
    (300, -3366.4, -3366.4),
]

#: Every DISTINCT box geometry among the 37 clamped items, as
#: `(width, height)`. This is the discriminating evidence: the
#: Pan/Tilt UNIT is relative to the clip's own size (`shift_y = -Tilt *
#: placed_H / timeline_H`), so if the RAIL were also clip-relative -
#: a cap on the resulting on-screen shift - these 17 geometries would
#: have read back 12 different values spread over -3840..-2373. They
#: all read back the same one.
CLAMPED_BOX_GEOMETRIES = [
    (840, 152), (902, 158), (866, 160), (840, 160), (840, 164),
    (888, 166), (840, 166), (870, 220), (840, 224), (896, 232),
    (884, 232), (840, 236), (876, 242), (840, 242), (840, 244),
    (898, 244), (840, 246),
]

#: The value Resolve pinned Tilt at, MEASURED, on a 1080x1920 timeline.
#: A NUMBER, deliberately not a formula. It happens to equal 2 x 1920,
#: but one timeline geometry cannot tell 2 x the height from 2 x the
#: longer side from a fixed constant - and writing a formula that fits
#: one data point into the code is exactly how the retired carriage came
#: to carry `4 x the timeline dimensions` as though it were measured.
#: Nothing depends on this number; it is recorded as evidence.
MEASURED_TILT_RAIL_1080x1920 = 3840.0

#: The bottom edge every caption canvas was anchored to.
CANVAS_BOTTOM = 1636


def _required_tilt(canvas_h: int) -> float:
    """The retired carriage's arithmetic, from the shipped geometry."""
    dy = CANVAS_BOTTOM - canvas_h / 2.0 - FULL_H / 2.0
    return -dy * (FULL_H / canvas_h)


# ── The root cause, reproduced from what shipped ──────────────────────













def test_the_floor_clears_the_cliff():
    """The composed answer to the cliff: the cliff is rail-relative.
    Under the 3840 row of the 2026-09-10 incident it sat at h = 270.4
    and every shipped 152..246px canvas overflowed unfloored; under
    the widened 4x-law rail it sits at h ~= 150.2, so only canvas
    140 overflows and 152 already holds. Either way the 480 floor is
    what clears it - the placement it asks for is -1744, thousands of
    units inside the rail. The gate below proves that per segment
    before anything reaches a timeline."""
    from library.tools.tight_box import MIN_CANVAS_HEIGHT
    assert MIN_CANVAS_HEIGHT == 480
    for canvas_h in (140, 152, 246, 270, MIN_CANVAS_HEIGHT):
        # History gain: this test pins the rail cliff the 2026-09-11
        # calibration measured (see HISTORY_GAIN in test_tight_box.py).
        placement = placement_for_box(
            840, canvas_h, FULL_W / 2.0,
            CANVAS_BOTTOM - canvas_h / 2.0, FULL_W, FULL_H,
            draw_gain=1.0)
        assert placement["scaling"] == 1
        if canvas_h <= 150:
            assert placement_holds(placement, FULL_W, FULL_H) != "", (
                f"h={canvas_h} is below the cliff and must overflow")
        else:
            assert placement_holds(placement, FULL_W, FULL_H) == ""
    assert abs(placement_for_box(
        840, MIN_CANVAS_HEIGHT, FULL_W / 2.0,
        CANVAS_BOTTOM - MIN_CANVAS_HEIGHT / 2.0,
        FULL_W, FULL_H, draw_gain=1.0)["tilt"]) <= 3400








# ── The placer: it sets the transform, then reads it back ────────────

class _Item:
    """A Resolve timeline item. `frozen` props simulate the silent
    clamp: `SetProperty` returns True but the held value does not
    move - the captain's -3840."""

    def __init__(self, start, held=None, frozen=None, unread=()):
        self._start = start
        self._held = dict(held or {})
        self._frozen = dict(frozen or {})
        self._held.update(self._frozen)
        self._unread = set(unread)
        self.set_calls = {}

    def GetStart(self):
        return self._start

    def GetProperty(self, prop):
        if prop in self._unread:
            return None
        return self._held.get(prop)

    def SetProperty(self, prop, value):
        self.set_calls[prop] = value
        if prop not in self._frozen:
            self._held[prop] = value
        return True


class _Timeline:
    def __init__(self, items):
        self._items = items

    def GetItemListInTrack(self, kind, index):
        assert kind == "video"
        return self._items


class _Pool:
    def __init__(self, result=("placed",)):
        self._result = result
        self.calls = []

    def AppendToTimeline(self, specs):
        self.calls.append(specs)
        return self._result


def _box_placement():
    return {"scaling": 1, "pan": 140.0, "tilt": -1720.0}




def test_a_clamped_overlay_is_reported_by_name():
    """Resolve holding the rail value read off the captain's live
    timeline is a REPORT naming the overlay - the clip IS on the
    timeline, and failing the build over a movable graphic would
    trade a misplaced one for a missing one."""
    item = _Item(10, frozen={"Tilt": -MEASURED_TILT_RAIL_1080x1920})
    pool = _Pool()
    ok, note = place_overlay_segment(
        pool, _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement=_box_placement(), label="sub_reel-09_akshita_9")
    assert ok
    assert "sub_reel-09_akshita_9" in note
    assert "Tilt" in note and "-3840" in note










def test_the_step_record_carries_a_placement_for_tight(tmp_path):
    """4.05's entry shape keeps `tight_box`, and a tight segment fills
    it with the placement the build applies - a reader finds the box,
    never an origin and never nothing."""
    from library.steps.step_4_05_render_subtitles import step as render_step

    source = open(render_step.__file__, encoding="utf-8").read()
    assert '"tight_box": _placement_record(),' in source
    assert '"placement": tight.placement,' in source


def test_every_overlay_row_goes_through_the_one_placer():
    """Captions, motion graphics AND timed text.

    Timed text used to carry its own inline `AppendToTimeline`, which
    made it the one overlay row nobody ever asked Resolve what it held.
    A second placer is also a second chance to land one frame off.
    """
    source = open(os.path.join(
        PROJECT_ROOT, "library", "steps", "step_6_01_render",
        "resolve_build_timeline.py"), encoding="utf-8").read()
    # One call each for captions, motion graphics and timed text.
    assert "    place_overlay_segment,\n" in source
    assert source.count("place_overlay_segment(") >= 3
    assert '"trackIndex": _tt_row' not in source, (
        "timed text appends inline again, outside the one placer")
    assert '"trackIndex": _caption_row' not in source
    assert '"trackIndex": _mg_row' not in source




class _EntryTimeline:
    def __init__(self, width, height):
        self._settings = {"timelineResolutionWidth": width,
                          "timelineResolutionHeight": height}

    def GetSetting(self, key):
        return self._settings[key]

    def GetName(self):
        return "entry"


class _EntryProject:
    def __init__(self, timeline):
        self._timeline = timeline

    def GetCurrentTimeline(self):
        return self._timeline




def test_mismatched_entry_size_is_a_refusal():
    """The Reel 09 positioning proof, 2026-09-10: Pan/Tilt sets are
    interpreted in the entry timeline's units and silently converted
    to the target's (-1700 stored as -1912.5 across 3840x2160 ->
    1080x1920, as -1511.11 the other way), while same-process
    read-back echoes the set value. The refusal names both sizes so
    the build reconnects from a same-size timeline instead of
    placing a wrong-but-stored one."""
    from library.tools.overlay_placement import entry_unit_mismatch

    project = _EntryProject(_EntryTimeline("3840", "2160"))
    reason = entry_unit_mismatch(project, (1080, 1920))
    assert reason != ""
    assert "3840x2160" in reason
    assert "1080x1920" in reason


