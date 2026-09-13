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
from library.tools import full_frame_element as ffe  # noqa: E402
from library.tools import overlay_verify  # noqa: E402
from library.tools import reel_build  # noqa: E402
from library.tools import speaker_identity as si  # noqa: E402
from library.tools.bookend_render import (  # noqa: E402
    render_bookend,
    render_declared_bookends,
)
from library.tools.delivery_format import DELIVERY_FORMATS  # noqa: E402
from library.tools.timed_text_overlay import (  # noqa: E402
    generate_timed_text_overlay_props,
    plan_timed_text_segments,
)
from library.tools.timed_text_render import (  # noqa: E402
    render_timed_text_segments,
)

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


@pytest.mark.parametrize("fn", [
    generate_subtitle_props_per_block,
    generate_motion_props,
    render_timed_text_segments,
    generate_timed_text_overlay_props,
    plan_timed_text_segments,
    render_bookend,
    render_declared_bookends,
    ffe.plan_reel_cards,
    si.plan_for_reel,
    reel_build.plan_cards,
])
def test_an_overlay_renderer_defaults_no_frame(fn):
    """Breaks on: restoring ``width=1080, height=1920`` (or any default)
    on the renderer - the exact shape that drew project 001 vertical."""
    _no_frame_default(fn, "width", "height")


@pytest.mark.parametrize("fn", [
    overlay_verify.verify_values,
    overlay_verify.verify_pixels,
])
def test_the_verifier_defaults_no_frame(fn):
    """Breaks on: restoring ``full_wh=(1080, 1920)`` on the verifier -
    judging a landscape timeline against a vertical frame."""
    _no_frame_default(fn, "full_wh")


def test_the_4_06_bridge_states_no_frame_on_its_own():
    """Breaks on: the ``width, height = 1080, 1920`` fallback returning
    to step 4.06's bridge - a model planning the layer against an
    assumed frame is the same defect one step earlier."""
    import library.steps.step_4_06_render_motion_graphics.bridge as bridge
    source = inspect.getsource(bridge.main)
    assert "width, height = 1080, 1920" not in source
    assert "width=1080" not in source and "height=1920" not in source


@pytest.mark.parametrize("call", [
    lambda: generate_subtitle_props_per_block(
        {"subtitle_entries": [], "style": {}}),
    lambda: generate_motion_props([], {}),
    lambda: render_timed_text_segments({}, "", ""),
    lambda: generate_timed_text_overlay_props({}),
    lambda: plan_timed_text_segments({}),
    lambda: render_bookend({}, ""),
    lambda: render_declared_bookends([], ""),
    lambda: ffe.plan_reel_cards([], ffe.ReelFacts(reel_number=1), 0, 30.0),
    lambda: si.plan_for_reel("R", [], 0.0, None),
    lambda: overlay_verify.verify_values([], {}),
    lambda: overlay_verify.verify_pixels([], {}),
])
def test_omitting_the_frame_is_a_type_error(call):
    with pytest.raises(TypeError):
        call()


def _subtitle_data():
    return {
        "subtitle_entries": [{
            "text": "hello", "timeline_start": 0.0, "timeline_end": 1.0,
            "spine_block_position": 0, "emphasis_words": [], "words": [],
        }],
        "style": {"font": "Montserrat", "size": 58},
    }


def test_a_horizontal_frame_reaches_the_subtitle_props():
    """Breaks on: subtitle props carrying anything but the frame handed
    in. Input: a 1920x1080 delivery - project 001's landscape shape."""
    props = generate_subtitle_props_per_block(
        _subtitle_data(), fps=30,
        width=HORIZONTAL[0], height=HORIZONTAL[1])
    assert props[0]["width"] == 1920
    assert props[0]["height"] == 1080


def test_a_horizontal_frame_reaches_the_motion_props():
    """Breaks on: motion props measured against anything but the frame
    handed in. Input: a 1920x1080 delivery."""
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
            "start_frame": 0, "duration_frames": 60,
            "x": 0.5, "y": 0.5,
        }],
    }}


def test_a_horizontal_frame_reaches_the_timed_text_props():
    """Breaks on: timed-text props carrying anything but the frame
    handed in. Input: a 1920x1080 delivery."""
    props = generate_timed_text_overlay_props(
        _timed_text_declaration(),
        width=HORIZONTAL[0], height=HORIZONTAL[1])
    assert props["width"] == 1920
    assert props["height"] == 1080
    segments = plan_timed_text_segments(
        _timed_text_declaration(),
        width=HORIZONTAL[0], height=HORIZONTAL[1])
    assert segments[0]["props"]["width"] == 1920
    assert segments[0]["props"]["height"] == 1080


def test_a_horizontal_frame_reaches_the_bookend_props():
    """Breaks on: bookend props carrying anything but the frame handed
    in. Input: a 1920x1080 delivery."""
    from library.tools.bookend_render import bookend_props
    props = bookend_props(
        {"duration_seconds": 0.5, "props": {}},
        30, HORIZONTAL[0], HORIZONTAL[1])
    assert props["width"] == 1920
    assert props["height"] == 1080


def test_a_horizontal_frame_reaches_the_full_frame_card():
    """Breaks on: a full-frame card planned at anything but the frame
    handed in. Input: a 1920x1080 delivery - a card IS the frame."""
    from library.tools.bookend_render import bookend_props  # noqa: F401
    declarations = ffe.declared_elements({"full_frame_elements": [{
        "element": "full_frame_card",
        "placement": "head",
        "duration_seconds": 1.0,
        "background": "#000000",
        "font_family": "Montserrat",
        "runs": [{"text": "hi", "type_role": "display",
                  "colour": "#FFFFFF"}],
    }]})
    facts = ffe.ReelFacts(reel_number=1)
    (card,) = ffe.plan_reel_cards(
        declarations, facts, 240, 24.0,
        width=HORIZONTAL[0], height=HORIZONTAL[1])
    assert card.props["width"] == 1920
    assert card.props["height"] == 1080


def test_a_horizontal_frame_reaches_the_verifier():
    """Breaks on: the verifier judging placements against anything but
    the frame handed in. Input: a 1920x1080 delivery with a canvas
    origin that differs per frame - the origin proves which frame was
    read."""
    placement = {"scaling": 1.0, "pan": 0.0, "tilt": 0.0}
    canvas = (840, 480)
    at_vertical = overlay_verify.canvas_origin(
        placement, canvas, VERTICAL)
    at_horizontal = overlay_verify.canvas_origin(
        placement, canvas, HORIZONTAL)
    assert at_vertical != at_horizontal


def test_both_declared_frames_are_statable_not_hardcoded():
    """Both frames come from the declaration this slice threads - the
    test above names no number the enumeration does not own."""
    assert DELIVERY_FORMATS["horizontal_1920x1080"] == (1920, 1080)
    assert DELIVERY_FORMATS["vertical_1080x1920"] == (1080, 1920)
