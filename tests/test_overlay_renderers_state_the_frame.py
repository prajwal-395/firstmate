"""Overlay renderers state the frame; none assumes one.

Slice 2 of the delivery-format generalisation. On project 001 a
landscape delivery let these renderers keep drawing vertical - they
carried ``width=1080, height=1920`` as default parameters - and the
overlays composited as a visible lighter band down the central 1080px
of the picture, which a vision model named unprompted on five of eight
sampled frames. See library/tools/delivery_format.py.

The rule: every named overlay renderer takes the frame from the
declared format. A default is what let these drift; making the caller
state the frame stops it recurring. No new hardcoded constant anywhere
- values come from ``delivery_format.py``, which owns them.

The input that breaks each test below is stated on the test: omit the
frame, or hand a horizontal one.
"""
import inspect
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.generate_remotion_props import (  # noqa: E402
    generate_subtitle_props_per_block,
)
from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (  # noqa: E402
    generate_motion_props,
)
from library.tools import overlay_verify  # noqa: E402

VERTICAL = (1080, 1920)
HORIZONTAL = (1920, 1080)


def _no_frame_default(fn, *names):
    """The named parameters exist and carry no default.

    Fails the moment a renderer regresses to assuming a frame: any
    default - 1080x1920, 1920x1080, or None - lets a caller drift.
    """
    params = inspect.signature(fn).parameters
    for name in names:
        assert name in params, f"{fn.__name__} lost its {name} parameter"
        assert params[name].default is inspect.Parameter.empty, (
            f"{fn.__name__} defaults {name} to "
            f"{params[name].default!r}; the caller must state the frame")


def test_an_overlay_renderer_defaults_no_frame():
    """B1 collapse of the renderer and verifier variants - the same
    property twice.

    Breaks on: restoring ``width=1080, height=1920`` (or any default)
    on a renderer - the exact shape that drew project 001 vertical -
    or restoring ``full_wh=(1080, 1920)`` on the verifier, judging a
    landscape timeline against a vertical frame."""
    for fn, names in ((generate_subtitle_props_per_block, ("width", "height")),
                      (generate_motion_props, ("width", "height")),
                      (overlay_verify.verify_values, ("full_wh",))):
        _no_frame_default(fn, *names)
    # And omitting the frame really is a TypeError at the call.
    with pytest.raises(TypeError):
        generate_subtitle_props_per_block({"subtitle_entries": [], "style": {}})
    with pytest.raises(TypeError):
        generate_motion_props([], {})




def _subtitle_data():
    return {
        "subtitle_entries": [{
            "text": "hello", "timeline_start": 0.0, "timeline_end": 1.0,
            "spine_block_position": 0, "emphasis_words": [], "words": [],
        }],
        "style": {"font": "Montserrat", "size": 58},
    }


def test_a_horizontal_frame_reaches_the_subtitle_and_motion_props():
    """Breaks on: subtitle props carrying anything but the frame handed
    in. Input: a 1920x1080 delivery - project 001's landscape shape."""
    props = generate_subtitle_props_per_block(
        _subtitle_data(), fps=30,
        width=HORIZONTAL[0], height=HORIZONTAL[1])
    assert props[0]["width"] == 1920
    assert props[0]["height"] == 1080
    # And the motion props are measured against it too.
    spine = {"structure": [{
        "block_type": "speech", "position": 1,
        "timeline_start": 0.0, "timeline_end": 4.0,
    }]}
    segments, resolved = generate_motion_props(
        [{"element": "title_lockup", "start_seconds": 0.5,
          "duration_seconds": 2.0, "anchor": "top_left",
          "copy": {"display": "A NAME"}, "color": "#F5F5F0"}],
        spine, width=HORIZONTAL[0], height=HORIZONTAL[1],
        project_folder=None)
    assert segments, resolved.basis_record()
    assert segments[0]["props"]["width"] == 1920
    assert segments[0]["props"]["height"] == 1080


def _timed_text_declaration():
    return {"timed_text_overlay": {
        "font_family": "Helvetica",
        "moments": [{
            "text": "Night 1", "color": "#FFFFFF",
            "font_size": 42, "font_weight": 400,
            "text_shadow": "none",
            "start_frame": 0, "duration_frames": 60,
            "x": 0.5, "y": 0.5,
            "fade_in_frames": 10, "fade_out_frames": 10,
        }],
    }}










