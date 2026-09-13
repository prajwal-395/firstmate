"""An overlay's position is carried on Pan/Tilt Resolve actually holds.

HISTORY, kept as evidence: the captain reported the same defect twice -
captions in the timeline carrying absurd Pan/Tilt values (they quoted
y=-7680), some on screen and some completely off frame. Read off the
live timeline on 2026-09-10, the rail Resolve held on that
1080x1920 project was -3840 - HALF what the first repair gated on,
which is why that repair refused one card and let 37 ride onto the
clamp. That reading does not reproduce - the rail is the 4x law now
(`tight_box.MEASURED_RAILS`: Pan 4320 / Tilt 7680 here, captain's call
2026-09-13) - but the SHIPPED_AND_HELD table below is what that day
actually measured, and the arithmetic it pins still explains the
incident. The measurement below says why, and pins the numbers the
clamp gate (`tight_box.placement_holds`) and the 480-pixel canvas
floor (`tight_box.MIN_CANVAS_HEIGHT`) were first calibrated against.

THE ROOT CAUSE, as arithmetic. Resolve's per-clip Pan/Tilt move a
clip by a fraction of its OWN size - shift = Pan * (clip_dim /
timeline_dim) * base_scale, the one measured law in
`library/tools/resolve_transform.py` - while Resolve pins the property
at a rail it does not report and refuses silently past it. On the
captain's 1080x1920 reels every caption box was bottom-anchored with
its lower edge at y=1636, so a 152-tall canvas asks for Tilt -7578.9
and gets -3840. `test_the_cliff_*` below reproduces the shipped asking
values from that arithmetic, and `test_the_floor_clears_the_cliff`
proves the answer: a canvas floored at 480 asks for -1744, which is
comfortably inside the rail. (A "draw gain of 2" was recorded here
between 2026-09-11 and this file's correction; it was calibrated
against a captured Pan/Tilt rather than one it had set, and it is
gone.)

THE CARRYING, as a property. A tight overlay renders only its drawn
bounds and lands on the timeline in three moves: `AppendToTimeline`
puts it down, `Scaling=1` draws it at native pixels, and Pan/Tilt move
it onto the full-frame coordinates the box computed - every one judged
by what Resolve RETURNS and then READ BACK, because past the rail the
return is a lie. The tests here prove the pipeline cannot express a
placement the gate would refuse, that a pre-carriage artefact cannot
be reused, and that a placed overlay Resolve has moved is REPORTED BY
NAME rather than shipped quietly.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    extract_frames,
    ink_union_of_frames,
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

def test_the_shipped_tilts_are_the_carriages_own_arithmetic():
    """Every value the pipeline asked Resolve for is reproduced by the
    retired carriage's formula, so the numbers below are this
    geometry's and not a coincidence."""
    for canvas_h, asked, _held in SHIPPED_AND_HELD:
        assert _required_tilt(canvas_h) == pytest.approx(asked, abs=0.01)


def test_the_rail_is_a_constant_in_property_space_not_a_clip_relative_cap():
    """THE DISCRIMINATING MEASUREMENT.

    Pan/Tilt are expressed in a unit relative to the CLIP's own size
    (`shift_y = -Tilt * placed_H / timeline_H`), so a natural worry is
    that the RAIL is clip-relative too - a cap on the resulting
    on-screen shift, which would present as a different Tilt limit for
    every box size and make any single number a coincidence of these
    particular captions.

    It is not. 37 items across 17 DISTINCT box geometries - heights
    152..246, a 1.62x spread, widths 840..902 - all read back the same
    -3840.0. A shift-capped rail would have produced twelve different
    values from -3840 down to -2373.
    """
    heights = sorted({h for _w, h in CLAMPED_BOX_GEOMETRIES})
    assert len(CLAMPED_BOX_GEOMETRIES) == 17
    assert max(heights) / min(heights) > 1.6, (
        "the box sizes must genuinely differ for this to discriminate")
    assert len({w for w, _h in CLAMPED_BOX_GEOMETRIES}) == 9

    # What a clip-relative (shift-capped) rail would have held, taking
    # the smallest box's shift as the cap.
    cap_px = MEASURED_TILT_RAIL_1080x1920 * min(heights) / FULL_H
    would_hold = {round(-cap_px * FULL_H / h, 1) for h in heights}
    assert len(would_hold) == len(heights), "each height, its own value"
    assert min(would_hold) < -3800 and max(would_hold) > -2400

    # What was actually held: one value, for every geometry.
    actually_held = {held for _h, _a, held in SHIPPED_AND_HELD
                     if held == -MEASURED_TILT_RAIL_1080x1920}
    assert actually_held == {-3840.0}

    # ...so the clamp is on the PROPERTY, independent of clip size.
    # Recorded as a number and not as a formula: one timeline geometry
    # cannot tell `2 x the height` from `2 x the longer side` from a
    # fixed constant, and generalising from one geometry is how the
    # retired carriage came to carry a rail twice the real one.
    assert MEASURED_TILT_RAIL_1080x1920 == 3840.0


def test_minus_3840_is_a_clamp_and_not_a_value_something_set():
    """It is suspiciously round, so it is checked rather than assumed.

    Three properties of the measured table, each of which a
    deliberately-written constant would fail:

    - the split is perfectly ORDERED - everything asked below 3840
      survived, everything above was pinned;
    - the rail is BRACKETED to [3366.4, 4316.1] by which items were
      left alone, and 3840 lies inside that bracket, which a constant
      something merely wrote has no reason to do;
    - the carriage's own arithmetic cannot EMIT -3840 for any box in
      the project (it needs h = 270.4).
    """
    held_exact = [asked for _h, asked, held in SHIPPED_AND_HELD
                  if abs(held - asked) <= 0.001]
    pinned = [asked for _h, asked, held in SHIPPED_AND_HELD
              if abs(held - asked) > 0.001]
    rail = MEASURED_TILT_RAIL_1080x1920

    assert all(abs(a) < rail for a in held_exact)
    assert all(abs(a) > rail for a in pinned)

    lower, upper = max(map(abs, held_exact)), min(map(abs, pinned))
    assert (lower, upper) == pytest.approx((3366.4, 4316.09756097561))
    assert lower < rail < upper, (
        "the pinned value lands inside the bracket the untouched items "
        "imply - a constant something wrote would not")

    # The only box height whose required Tilt IS -3840 is the cliff
    # itself, and no box in the project has it - so the carriage's own
    # arithmetic could not have produced this value for any of them.
    emitting_height = ((CANVAS_BOTTOM - FULL_H / 2.0) * FULL_H
                       / (rail + FULL_H / 2.0))
    assert emitting_height == pytest.approx(270.4, abs=0.05)
    assert all(abs(h - emitting_height) > 1.0
               for h, _a, _held in SHIPPED_AND_HELD)


def test_the_inspector_agrees_with_the_api():
    """A settled NEGATIVE, and worth as much as a positive.

    The captain read the Edit page Inspector on 2026-09-10: "all of
    them are -3840, except for 2 subtitle clips, one which is y=0 and
    another which is y=-3366.4". So there is no display scale, and the
    theory that the Inspector showed twice the API value - which would
    have explained the retired four-times figure - is dead.

    Both of his anomalies are in this table: -3366.4 is the one tight
    caption clearing the cliff, and y=0 is a FULL-CANVAS caption, which
    carries no transform because its position is already its pixels.
    """
    inspector_reported = -3366.4
    matches = [asked for _h, asked, held in SHIPPED_AND_HELD
               if held == pytest.approx(inspector_reported)]
    assert matches == [inspector_reported], (
        "the captain's -3366.4 is the single caption held exactly")
    assert not any(held == 0.0 for _h, _a, held in SHIPPED_AND_HELD), (
        "a y=0 caption carries no tight box at all, so it is not in "
        "this table - it is the full-canvas path, which needs no "
        "transform and is where every caption now lives")


def test_the_cliff_follows_from_the_measured_rail():
    """A caption clears the rail only above canvas height 270.4px,
    which on this project is the single three-line card and nothing
    else."""
    cliff = ((CANVAS_BOTTOM - FULL_H / 2.0) * FULL_H
             / (MEASURED_TILT_RAIL_1080x1920 + FULL_H / 2.0))
    assert cliff == pytest.approx(270.4, abs=0.05)
    for canvas_h, asked, held in SHIPPED_AND_HELD:
        if canvas_h > cliff:
            assert held == pytest.approx(asked, abs=0.01)
        else:
            assert held == pytest.approx(
                -MEASURED_TILT_RAIL_1080x1920, abs=0.01)
    assert [h for h, _, _ in SHIPPED_AND_HELD if h > cliff] == [300]


def test_a_clamped_caption_lands_low_but_still_on_the_frame():
    """What the clamp actually does to the picture - and what it does
    NOT do.

    A pinned Tilt -3840 puts the canvas centre 2h below frame centre,
    so every one of the twelve clamped captions rides low, under its
    band, and every one of them is still ON the frame: the tallest
    clamped box, 246px, has its bottom edge at row 1575.

    This is worth pinning because it is the OPPOSITE of what was
    recorded here for a day. Under the retired "gain of 2" the same
    clamp read as putting seven of these captions off the bottom
    edge, and that reading was used as evidence FOR the gain. The
    clamp misplaces captions; it does not make them disappear. The
    overlays that really are invisible on the captain's reels are
    seventeen motion graphics stored at Tilt 5184, which the measured
    relation puts at frame rows -576..-96 - off the TOP.
    """
    clamped = [(h, held) for h, asked, held in SHIPPED_AND_HELD
               if abs(held - asked) > 0.5]
    assert len(clamped) == 12
    for canvas_h, held in clamped:
        landed_cy = FULL_H / 2.0 - held * (canvas_h / FULL_H)
        wanted_cy = CANVAS_BOTTOM - canvas_h / 2.0
        error = wanted_cy - landed_cy
        assert error == pytest.approx(676 - 2.5 * canvas_h, abs=0.01)
    on_frame = [h for h, _ in clamped
                if FULL_H / 2.0 + 3840.0 * (h / FULL_H) + h / 2.0
                <= FULL_H]
    assert on_frame == [h for h, _ in clamped], (
        "the clamp lands every shipped box low, and none of them off "
        "the bottom edge")
    assert max(FULL_H / 2.0 + 3840.0 * (h / FULL_H) + h / 2.0
               for h, _ in clamped) == pytest.approx(1575.0, abs=0.5)


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
        placement = placement_for_box(
            840, canvas_h, FULL_W / 2.0,
            CANVAS_BOTTOM - canvas_h / 2.0, FULL_W, FULL_H)
        assert placement["scaling"] == 1
        if canvas_h <= 150:
            assert placement_holds(placement, FULL_W, FULL_H) != "", (
                f"h={canvas_h} is below the cliff and must overflow")
        else:
            assert placement_holds(placement, FULL_W, FULL_H) == ""
    assert abs(placement_for_box(
        840, MIN_CANVAS_HEIGHT, FULL_W / 2.0,
        CANVAS_BOTTOM - MIN_CANVAS_HEIGHT / 2.0,
        FULL_W, FULL_H)["tilt"]) <= 3400


def test_subtitles_default_to_tight(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.yaml").write_text("name: t\n", encoding="utf-8")
    assert resolve_overlay_geometry(str(project)) == "tight"


def test_explicit_tight_is_honoured(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: t\npipeline:\n  subtitle_overlay_geometry: tight\n",
        encoding="utf-8")
    assert resolve_overlay_geometry(str(project)) == "tight"


def test_a_frame_baked_artefact_cannot_be_reused(tmp_path):
    """The reuse key names the CARRIAGE, so a `frame-baked-1` file is
    unusable rather than merely stale: it baked its position into
    delivery-frame pixels, and placing it under today's rule would
    transform a full-frame clip off the frame."""
    sys.path.insert(0, os.path.join(
        PROJECT_ROOT, "library", "steps", "step_4_05_render_subtitles"))
    from library.steps.step_4_05_render_subtitles import step as render_step

    remotion = tmp_path / "remotion"
    (remotion / "src").mkdir(parents=True)
    (remotion / "src" / "x.tsx").write_text("export const x = 1;\n",
                                            encoding="utf-8")
    props = {"width": FULL_W, "height": FULL_H, "subtitles": []}
    today = render_step._reuse_key(props, str(remotion), "tight", "video")
    render_step.OVERLAY_CARRIAGE, saved = "frame-baked-1", \
        render_step.OVERLAY_CARRIAGE
    try:
        framed = render_step._reuse_key(props, str(remotion), "tight",
                                        "video")
    finally:
        render_step.OVERLAY_CARRIAGE = saved
    assert today and framed
    assert today != framed, (
        "a frame-baked key still matches: a delivery-frame artefact "
        "would be reused and transformed off the frame")
    assert today.endswith(f"+{OVERLAY_CARRIAGE}")
    assert OVERLAY_CARRIAGE == "tight-480-4"


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


def test_a_tight_overlay_is_set_then_read_back():
    item = _Item(10)
    pool = _Pool()
    ok, note = place_overlay_segment(
        pool, _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement=_box_placement(), label="cap_1")
    assert ok and note == ""
    assert item.set_calls == {"Scaling": 1, "Pan": 140.0, "Tilt": -1720.0}


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


def test_a_full_canvas_overlay_writes_nothing():
    item = _Item(10)
    pool = _Pool()
    ok, note = place_overlay_segment(
        pool, _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40, label="cap_1")
    assert ok and note == ""
    assert item.set_calls == {}, (
        "the placer wrote a property: a full-canvas overlay needs none")


def test_a_readback_that_is_unavailable_falls_back_to_the_return():
    """A proxy that does not serve `GetProperty` cannot be judged by
    it - so the `SetProperty` return is the judgement, the old
    behaviour, rather than a refusal of a placement nothing saw."""
    item = _Item(10, unread=("Tilt",))
    ok, note = place_overlay_segment(
        _Pool(), _Timeline([item]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement=_box_placement(), label="cap_1")
    assert ok and note == ""


def test_an_overlay_with_no_item_at_its_record_frame_says_so():
    """No fallback to the last item on the track: judging a neighbour
    is a caption wearing another caption's geometry. An item that
    cannot be named is an unavailable read-back, not a mismatch."""
    _ok, note = place_overlay_segment(
        _Pool(), _Timeline([_Item(99)]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40,
        placement=_box_placement(), label="cap_1")
    assert "no timeline item" in note
    assert "cap_1" in note


def test_a_failed_append_is_reported_and_not_read_back():
    pool = _Pool(result=None)
    ok, note = place_overlay_segment(
        pool, _Timeline([]), object(),
        track_index=3, record_frame=10,
        source_in_frame=0, source_out_frame=40, label="cap_1")
    assert ok is False and "AppendToTimeline returned nothing" in note


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


def test_json_round_trip_of_a_recorded_box_carries_a_placement():
    from library.tools.tight_box import TightBox

    box = TightBox(width=296, height=312, props={},
                   placement={"scaling": 1, "pan": 140.0,
                              "tilt": -1720.0},
                   union_w=200.0, union_h=216.0,
                   full_width=FULL_W, full_height=FULL_H)
    record = {"width": box.width, "height": box.height,
              "placement": box.placement}
    assert json.loads(json.dumps(record))["placement"]["tilt"] == -1720.0
    assert not hasattr(box, "origin")


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


def test_matching_entry_size_places_literally():
    from library.tools.overlay_placement import entry_unit_mismatch

    project = _EntryProject(_EntryTimeline("1080", "1920"))
    assert entry_unit_mismatch(project, (1080, 1920)) == ""


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


def test_no_open_timeline_is_no_entry_to_judge():
    from library.tools.overlay_placement import entry_unit_mismatch

    assert entry_unit_mismatch(_EntryProject(None), (1080, 1920)) == ""
