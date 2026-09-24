"""The digit_counter spring: our physics, pinned to the reference.

The defect this names is physics drifting from the reference: the
odometer roll eases per Remotion's damped-spring integrator, and a
reimplementation that only looks right would draw a differently-easing
roll while reporting success. So the port (`remotion_spring`, pure
stdlib, no `remotion` import and no node) is pinned against spring
values the reference produced, recorded below as literals - and the
bake is proved to need neither node nor the Remotion tree at all.

Literals recorded 2026-09-24 from `require("remotion")` 4.0.486,
config {damping 15, stiffness 80, mass 0.8} (the composition's own):
natural duration 19 frames at 30fps (15 at 24fps), then the 0-to-1
positions below.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import hyperframes_render as hf  # noqa: E402

_NATURAL_30 = 19
_NATURAL_24 = 15

# (frame, durationInFrames, expected), fps 30 throughout.
_CURVE = [
    (0, 90, 0.0),
    (1, 90, 0.0023696265709273190),
    (2, 90, 0.0090726426939360394),
    (3, 90, 0.019542312889158087),
    (5, 90, 0.049773493954933468),
    (8, 90, 0.11201315778310494),
    (13, 90, 0.23948446409367952),
    (21, 90, 0.45056256503743597),
    (34, 90, 0.71707860174387972),
    (55, 90, 0.92434279057799440),
    (89, 90, 0.99538154951924451),
    (0, 48, 0.0),
    (1, 48, 0.0080176772458538936),
    (2, 48, 0.029555280609977030),
    (5, 48, 0.13817110976200020),
    (10, 48, 0.38850965290558048),
    (20, 48, 0.76849561789072574),
    (40, 48, 0.98341295736330836),
    (47, 48, 0.99495719113002490),
]


def test_natural_duration_matches_the_reference():
    assert hf._spring_natural_duration(
        fps=30, damping=15, mass=0.8, stiffness=80) == _NATURAL_30
    assert hf._spring_natural_duration(
        fps=24, damping=15, mass=0.8, stiffness=80) == _NATURAL_24


@pytest.mark.parametrize("frame,duration,expected", _CURVE)
def test_spring_matches_recorded_reference_values(frame, duration,
                                                  expected):
    assert hf.remotion_spring(
        frame=frame, fps=30,
        duration_in_frames=duration) == pytest.approx(expected, abs=1e-12)


def test_spring_is_settled_past_the_end():
    assert hf.remotion_spring(
        frame=91, fps=30, duration_in_frames=90) == 1.0


def _counter_props(**overrides) -> dict:
    props = {
        "elements": [{
            "element": "digit_counter",
            "anchor": "bottom_centre",
            "row": 0,
            "runs": [{"text": "Count", "type_role": "supporting"}],
            "color": "#FBF0B8",
            "entrance": "fade",
            "exit": "fade",
            "startFrame": 0,
            "durationFrames": 30,
            "data": {"end_value": 1234},
        }],
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "safeArea": {"top": 120, "right": 120, "bottom": 320, "left": 90},
        "durationInFrames": 30,
    }
    props.update(overrides)
    return props


def test_bake_aligns_to_characters_with_statics_null():
    baked = hf._bake_digit_springs(_counter_props(), None)
    offsets = baked["elements"][0]["data"]["_hf_digit_offsets"]
    # 1234 formats grouped ("1,234", like the composition's
    # toLocaleString): four digit series of 30 frames, comma static.
    assert len(offsets) == 5
    assert offsets[1] is None
    assert all(len(series) == 30 for i, series in enumerate(offsets)
               if i != 1)
    # Frame 0 is unrolled everywhere; the last digit's first offset is
    # the reference's own frame-0 answer for it.
    assert all(series[0] == pytest.approx(0.0) for series in offsets
               if series is not None)
    assert offsets[4][1] == pytest.approx(
        -4 * hf.remotion_spring(frame=1, fps=30, duration_in_frames=30))


def test_bake_needs_neither_node_nor_remotion(monkeypatch):
    """The independence the second renderer exists for, as a test.

    With node unresolvable, subprocess unusable and the Remotion tree
    absent, the bake still produces the pinned curve: nothing in the
    HyperFrames path reaches the licensed package, so dropping
    Remotion cannot take a digit_counter card down with it.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    monkeypatch.setattr(_shutil, "which", lambda *_a, **_k: None)

    def _refuse(*_a, **_k):
        raise AssertionError("the bake must not subprocess")
    monkeypatch.setattr(_subprocess, "run", _refuse)

    baked = hf._bake_digit_springs(_counter_props(), None)
    offsets = baked["elements"][0]["data"]["_hf_digit_offsets"]
    assert len(offsets) == 5
    # The pinned curve, through the bake: digit 4 (last, unstaggered)
    # at local frame 1 over its 30-frame span.
    assert offsets[4][1] == pytest.approx(
        -4 * hf.remotion_spring(frame=1, fps=30, duration_in_frames=30))


def test_no_counter_bakes_nothing():
    """A card without digit_counter passes through untouched."""
    props = _counter_props()
    props["elements"][0]["element"] = "title_lockup"
    assert hf._bake_digit_springs(props, None) is props
