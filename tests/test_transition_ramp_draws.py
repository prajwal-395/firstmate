"""A planned transition ramps over its planned frames, and stops.

Counts the frames a transition is actually DRAWN on, off the comp the
renderer writes, over the frames Resolve renders for that clip. Cases are
project 001's run of record and issue #202; history in
`docs/evidence/transition_ramp_draws.md`.
"""

import pytest

from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.fusion.effects import _reset_counters
from library.tools.fusion.played_window import (
    TransitionLongerThanTheClip,
    played_length,
)
from library.tools.fusion.transition_frames import (
    drawn_frames,
    transition_splines,
)


#: One placement of 001's run of record: the source clip's own frame
#: count, the SOURCE frames the timeline plays, and the planned ramp.
#: ``source_in``/``source_out`` are ``round(seconds * 30)`` on the
#: manifest's own values, which is what apply_fusion_comps computes.
CASES = [
    # (name, half, ttype, clip_dur, source_in, source_out, dur_frames)
    ("cut 8  tail  speech_7_seg0  IMG_1816",
     "tail", "defocus", 5656, 3016, 3495, 15),
    ("cut 8  head  speech_9_seg0  IMG_1817",
     "head", "defocus", 1245, 654, 725, 15),
    ("cut 13 tail  speech_12_seg0 IMG_1817",
     "tail", "defocus", 1245, 1018, 1062, 15),
    ("cut 13 head  speech_14_seg0 IMG_1822",
     "head", "defocus", 2574, 944, 1204, 15),
    # Issue #202: spine block 18, timeline 51.941-54.323, clip_012 /
    # IMG_1817, duration_frames 15.  Same shape, different type.
    ("#202  head  clip_012       IMG_1817",
     "head", "zoom_blur", 1245, 654, 725, 15),
    ("#202  tail  clip_012       IMG_1817",
     "tail", "zoom_blur", 1245, 654, 725, 15),
]

#: Every type the renderer can draw, exercised on one awkward window
#: (a short segment cut from deep inside a long source), because
#: `zoom_blur` reached a render for the first time two years after it
#: was advertised and drew the wrong thing.
EVERY_TYPE = ["fade_to_black", "zoom_blur", "defocus", "flash"]


def _comp(half, ttype, clip_dur, source_in, source_out, dur_frames):
    """The comp the renderer really writes for one transition half.

    Driven through ``build_effect_comp`` - the function
    ``apply_fusion_comps`` calls - so the test measures the route the
    picture takes, not a builder called directly.
    """
    _reset_counters()
    effects = {
        f"{half}_transition": ttype,
        f"{half}_transition_frames": dur_frames,
    }
    if source_in is not None:
        effects["source_in_frame"] = source_in
        effects["source_out_frame"] = source_out
    return build_effect_comp(effects, clip_dur, (1920, 1080))


@pytest.mark.parametrize(
    "name,half,ttype,clip_dur,source_in,source_out,dur_frames", CASES,
    ids=[c[0].split()[0] + c[0].split()[1] + c[0].split()[2] for c in CASES])
def test_a_planned_transition_draws_for_its_planned_frames(
        name, half, ttype, clip_dur, source_in, source_out, dur_frames):
    """Drawn frames == planned frames, and they sit where planned."""
    comp = _comp(half, ttype, clip_dur, source_in, source_out, dur_frames)
    length = played_length(clip_dur, source_in, source_out)

    splines = transition_splines(comp)
    assert splines, f"{name}: the comp carries no animated transition"

    for spline_name, keys in splines.items():
        drawn = drawn_frames(keys, length, half)
        where = f"{name} [{spline_name}]"

        assert drawn, (
            f"{where}: 0 of {dur_frames} planned frames drew. The ramp is "
            f"outside the {length} frames this clip plays."
        )
        assert len(drawn) == dur_frames, (
            f"{where}: {len(drawn)} frames drawn, {dur_frames} planned. "
            f"{length} frames play."
        )

        if half == "head":
            expected = set(range(0, dur_frames))
        else:
            expected = set(range(length - dur_frames, length))
        assert set(drawn) == expected, (
            f"{where}: drawn on frames {min(drawn)}..{max(drawn)}, "
            f"planned {min(expected)}..{max(expected)} of {length}."
        )


def test_no_frame_carries_an_unplanned_transition():
    """Every played frame outside the ramp is exactly neutral.

    This is the half that caught it. The count above can be right while
    the effect is also held across the rest of the clip - which is what
    001 shipped: the ramp never ran, and 331 frames sat at full strength.
    """
    clip_dur, source_in, source_out, dur_frames = 1245, 654, 725, 15
    length = played_length(clip_dur, source_in, source_out)
    for ttype in EVERY_TYPE:
        for half in ("head", "tail"):
            comp = _comp(half, ttype, clip_dur, source_in, source_out,
                         dur_frames)
            for spline_name, keys in transition_splines(comp).items():
                drawn = set(drawn_frames(keys, length, half))
                unplanned = sorted(
                    f for f in range(length)
                    if f in drawn
                    and not (f < dur_frames if half == "head"
                             else f >= length - dur_frames))
                assert not unplanned, (
                    f"{ttype} {half} [{spline_name}]: {len(unplanned)} of "
                    f"{length} played frames carry an unplanned effect, "
                    f"frames {unplanned[0]}..{unplanned[-1]}."
                )


def test_a_ramp_longer_than_its_clip_is_refused_not_drawn():
    """A ramp with no room never reaches neutral, so it is refused."""
    for ttype in EVERY_TYPE:
        with pytest.raises(TransitionLongerThanTheClip):
            _comp("head", ttype, clip_dur=1245, source_in=654,
                  source_out=664, dur_frames=15)


def test_the_whole_source_case_still_places_its_ramp_at_the_end():
    """A clip that plays all of its source keeps the old geometry."""
    comp = _comp("tail", "defocus", clip_dur=90,
                 source_in=None, source_out=None, dur_frames=8)
    for _name, keys in transition_splines(comp).items():
        assert max(f for f, _ in keys) == 89
        assert set(drawn_frames(keys, 90, "tail")) == set(range(82, 90))
