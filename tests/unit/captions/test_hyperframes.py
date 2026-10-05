"""The HyperFrames alpha adapter: straight in, premultiplied (or flat) out.

The defect this names: HyperFrames PNG frames carry STRAIGHT alpha
with full-strength RGB, while the overlay carriage - and Resolve's
premultiplied read of the `qtrle` file encoded from these frames -
assumes no channel exceeds its own alpha. Encoding straight frames
unadapted composites halos and lifted blacks wherever the overlay is
semi-transparent, and the file still probes as valid RGBA: nothing
downstream would refuse it. So the adapter is asserted on pixels, not
on calls: a straight pixel premultiplies exactly, a transparent one
goes black-transparent, and a card flattens over its own declared
ground rather than over black.
"""
from __future__ import annotations
import os
import sys
import numpy as np
import pytest
from PIL import Image
from library.tools.logo_bulb import (
    ClosingProfile,
    ClosingText,
    ClosingTextMotion,
    parse_ground,
    text_full_frames,
    text_layer,
    text_element_bounds,
    text_motion_layer,
    text_motion_state,
)


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import hyperframes_render as hf  # noqa: E402


def _write(tmp_path, name, pixels) -> str:
    path = str(tmp_path / name)
    Image.fromarray(np.array(pixels, dtype=np.uint8),
                    mode="RGBA").save(path)
    return path


def _read(path):
    return np.asarray(Image.open(path).convert("RGBA"))


def test_premultiply_straight_to_premultiplied(tmp_path):
    # Straight: full-strength RGB beside partial alpha. Premultiplied:
    # each channel scaled by its own alpha, and a fully transparent
    # pixel is black-transparent rather than colour-transparent.
    path = _write(tmp_path, "frame_000001.png", [
        [(255, 255, 255, 128), (200, 100, 50, 0)],
        [(255, 255, 255, 255), (0, 0, 0, 0)],
    ])
    assert hf.premultiply_frames([path]) == 1
    out = _read(path)
    assert tuple(out[0, 0]) == (128, 128, 128, 128)
    assert tuple(out[0, 1]) == (0, 0, 0, 0)
    assert tuple(out[1, 0]) == (255, 255, 255, 255)
    assert tuple(out[1, 1]) == (0, 0, 0, 0)
    # The carriage invariant: no channel exceeds its own alpha.
    assert bool((out[..., :3].astype(int) <= out[..., 3:4].astype(int)).all())


def test_flatten_composites_over_the_declared_ground(tmp_path):
    path = _write(tmp_path, "frame_000001.png", [
        [(0, 0, 0, 0), (255, 255, 255, 255)],
        [(255, 255, 255, 128), (0, 0, 0, 0)],
    ])
    assert hf.flatten_frames([path], "#101014") == 1
    out = _read(path)
    # Transparent paints the ground; opaque ink survives; half alpha
    # blends between them - (255+16)/2 rounded is 136, +green 136...
    assert tuple(out[0, 0]) == (16, 16, 20, 255)
    assert tuple(out[0, 1]) == (255, 255, 255, 255)
    assert tuple(out[1, 0]) == (136, 136, 138, 255)
    assert tuple(out[1, 1]) == (16, 16, 20, 255)


def test_flatten_refuses_a_ground_it_cannot_parse(tmp_path):
    path = _write(tmp_path, "frame_000001.png", [[(0, 0, 0, 0)]])
    with pytest.raises(hf.HyperFramesRenderError):
        hf.flatten_frames([path], "")
    with pytest.raises(hf.HyperFramesRenderError):
        hf.flatten_frames([path], "dark grey")


def test_fps_argument_recovers_exact_ratios():
    assert hf.fps_argument(23.976023976023978) == "24000/1001"
    assert hf.fps_argument(30.0) == "30"
    assert hf.fps_argument(25) == "25"


def test_template_registry_names_what_has_no_form():
    assert hf.hyperframes_template("SubtitleOverlay") == "SubtitleOverlay"
    assert hf.hyperframes_template("TimedTextOverlay") == "TimedTextOverlay"
    assert hf.hyperframes_template("FullFrameCard") == "FullFrameCard"
    assert hf.hyperframes_template("MotionGraphics") == "MotionGraphics"
    assert hf.hyperframes_template("PostHeader") == "PostHeader"


# --------------------------------------------------------------------------
# From test_hyperframes_digit_bake.py
#
# The digit_counter spring: our physics, pinned to the reference.
#
# The defect this names is physics drifting from the reference: the
# odometer roll eases per Remotion's damped-spring integrator, and a
# reimplementation that only looks right would draw a differently-easing
# roll while reporting success. So the port (`remotion_spring`, pure
# stdlib, no `remotion` import and no node) is pinned against spring
# values the reference produced, recorded below as literals - and the
# bake is proved to need neither node nor the Remotion tree at all.
#
# Literals recorded 2026-09-24 from `require("remotion")` 4.0.486,
# config {damping 15, stiffness 80, mass 0.8} (the composition's own):
# natural duration 19 frames at 30fps (15 at 24fps), then the 0-to-1
# positions below.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))


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


def test_spring_matches_recorded_reference_values():
    for frame, duration, expected in _CURVE:
        assert hf.remotion_spring(
            frame=frame, fps=30, duration_in_frames=duration
        ) == pytest.approx(expected, abs=1e-12), (frame, duration)
    # Settled past the end.
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


def test_bake_aligns_to_characters_without_node_or_remotion(monkeypatch):
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
    # 1234 formats grouped ("1,234", like the composition's
    # toLocaleString): four digit series of 30 frames, comma static.
    assert len(offsets) == 5
    assert offsets[1] is None
    assert all(len(series) == 30 for i, series in enumerate(offsets)
               if i != 1)
    assert all(series[0] == pytest.approx(0.0) for series in offsets
               if series is not None)
    # The pinned curve, through the bake: digit 4 (last, unstaggered)
    # at local frame 1 over its 30-frame span.
    assert offsets[4][1] == pytest.approx(
        -4 * hf.remotion_spring(frame=1, fps=30, duration_in_frames=30))


def test_no_counter_bakes_nothing():
    """A card without digit_counter passes through untouched."""
    props = _counter_props()
    props["elements"][0]["element"] = "title_lockup"
    assert hf._bake_digit_springs(props, None) is props


# --------------------------------------------------------------------------
# From test_logo_bulb_text_motion.py
#
# The animated closing lines arrive and leave instead of sitting static.
#
# One test naming the defect: the two bottom lines' opacity and position
# were constant across all 72 frames (up with the cut, out on the fade).
# With a declared motion they rise in staggered, hold at full legibility,
# and leave together before the final black.

RATE = 23.976
"""The conform every reel already built holds a slot for."""


def test_animated_lines_arrive_and_leave_instead_of_sitting_static():
    profile = ClosingProfile()
    motion = ClosingTextMotion(
        style="rise",
        line1_in=(0, 7),
        line2_in=(4, 11),
        lines_out=(61, 66),
        rise_px=28,
        exit_px=14,
    )
    text = ClosingText(
        lines=("See your brand the way AI does", "luciecontent.com"),
        color=parse_ground("#F5F5F5"),
        motion=motion,
    )

    states1 = [text_motion_state(index, motion, 1) for index in range(72)]
    states2 = [text_motion_state(index, motion, 2) for index in range(72)]
    # The defect: presence stuck at one value and travel at zero on
    # every frame.
    assert {presence for presence, _ in states1} != {1.0}
    assert {offset for _, offset in states1} != {0.0}
    assert {presence for presence, _ in states2} != {1.0}
    assert {offset for _, offset in states2} != {0.0}
    # Staggered: line 1 sits while line 2 is still arriving.
    assert states1[8] == (1.0, 0.0)
    assert states2[8][0] < 1.0
    # A shared hold, then a shared exit that is over before the black.
    assert all(presence == 1.0 and offset == 0.0
               for presence, offset in states1[12:61] + states2[12:61])
    assert states1[66] == (0.0, 14.0)
    assert states2[66] == (0.0, 14.0)
    # The full-legibility stand clears the 2.0s floor inside 72 frames.
    full = text_full_frames(text, [1.0] * 72, [1.0] * 72)
    assert (full[0], full[-1], len(full)) == (11, 61, 51)
    assert len(full) / RATE >= 2.0
    # The frames differ on real pixels, and the hold IS the static layer.
    layers = [text_motion_layer(text, 1080, 1920, profile, index)
              for index in (0, 8, 30, 63, 67)]
    inks = [layer[..., 3].sum() for layer in layers]
    assert inks[0] < inks[2]
    assert inks[3] < inks[2]
    assert inks[4] == 0.0
    static = text_layer(
        ClosingText(lines=text.lines, color=text.color), 1080, 1920,
        profile)
    assert np.array_equal(layers[2], static)


def test_every_closing_text_line_stays_inside_all_shortform_safe_zones():
    from library.tools.safe_zone_policy import default_policy, resolve_layout

    profile = ClosingProfile()
    motion = ClosingTextMotion(
        style="rise",
        line1_in=(0, 7),
        line2_in=(4, 11),
        lines_out=(61, 66),
        rise_px=28,
        exit_px=14,
    )
    text = ClosingText(
        lines=("See your brand the way AI does", "luciecontent.com"),
        color=parse_ground("#F5F5F5"),
        motion=motion,
    )
    safe = resolve_layout(default_policy(), frame=(1080, 1920))

    assert safe.policy.platforms == (
        "tiktok", "instagram_reels", "youtube_shorts", "linkedin")
    for frame in range(72):
        boxes = text_element_bounds(text, 1080, 1920, profile, frame)
        assert len(boxes) == 2
        assert boxes[0][3] < boxes[1][1], (
            f"line order crossed at frame {frame}: {boxes}")
        for line, box in zip(text.lines, boxes):
            intrusions = safe.intrusions(box)
            assert not intrusions, (
                f"closing line {line!r} leaves the safe zone at frame "
                f"{frame}, bounds={box}, intrusions={intrusions}")
