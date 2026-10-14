"""FFrames draws SVG image assets instead of refusing them.

A brand logo as SVG is ordinary project data (the Reel 13 rebuild died in
overlay planning on the project's SVG avatar with ``ImageError Unsupported
Format(Unknown)``). The FFrames image path rasterizes SVG assets at the
size the layout asks for, the way the browser draws them (``contain``:
fit, centred). These tests render a still with an SVG avatar through the
full FFrames path and measure the pixels.

This test renders pixels through the real binary, so it is expensive -
one still - and states that reason here. It is skipped where the binary
is not built.
"""

import base64

import numpy as np
import pytest
from PIL import Image

from library.graphics_ir import lowering
from library.tools import fframes_render

#: A small SVG fixture: a red disc on a dark ground, square like a logo.
SVG_FIXTURE = """\
<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">\
<rect width="64" height="64" fill="#123456"/>\
<circle cx="32" cy="32" r="20" fill="#FF0000"/>\
</svg>"""


def _fframes_available() -> bool:
    usable, _ = fframes_render.fframes_available()
    return bool(usable)


def _props(avatar_src: str) -> dict:
    return {
        "avatarSrc": avatar_src,
        "avatarShape": "square",
        "avatarBackground": "#FFFFFF",
        "displayName": "T",
        "handle": "t",
        "verified": False,
        "hook": "hi",
        "fontFamily": "Montserrat",
        "nameSize": 32,
        "handleSize": 24,
        "hookSize": 28,
        "box": {"left": 0, "top": 0, "width": 400},
        "fps": 30,
        "width": 480,
        "height": 270,
        "durationInFrames": 1,
    }


def _avatar_bounds(props: dict):
    scene = lowering.lower_post_header(
        fframes_render.resolve_props("PostHeader", props, ""))
    node = next(n for n in scene.nodes if n.id == "avatar")
    return node.bounds


@pytest.mark.skipif(not _fframes_available(), reason="FFrames binary not available")
@pytest.mark.parametrize("as_data_uri", [True, False])
def test_fframes_draws_an_svg_avatar(tmp_path, as_data_uri):
    """An SVG avatar renders where the layout puts it, in its own colours."""
    if as_data_uri:
        avatar_src = ("data:image/svg+xml;base64,"
                      + base64.b64encode(SVG_FIXTURE.encode()).decode())
    else:
        svg_path = tmp_path / "avatar.svg"
        svg_path.write_text(SVG_FIXTURE, encoding="utf-8")
        avatar_src = str(svg_path)
    props = _props(avatar_src)
    bounds = _avatar_bounds(props)

    frames_dir = tmp_path / "frames"
    work_dir = tmp_path / "work"
    frames_dir.mkdir()
    work_dir.mkdir()
    rendered = fframes_render.render_one_card_frames(
        "PostHeader", props, str(frames_dir), str(work_dir), "")
    assert rendered["frames"], "FFrames rendered no frames for the SVG still"

    pixels = np.asarray(Image.open(rendered["frames"][0]).convert("RGB")).astype(np.float32)
    x0, y0 = int(bounds.x), int(bounds.y)
    region = pixels[y0:y0 + int(bounds.height), x0:x0 + int(bounds.width)]
    red = ((region[..., 0] > 200) & (region[..., 1] < 80)
           & (region[..., 2] < 80)).sum()
    assert red > 100, (
        f"FFrames drew no red disc ink in the avatar box "
        f"{(x0, y0, bounds.width, bounds.height)}: {red} red pixels")
    ground = ((np.abs(region[..., 0] - 0x12) < 24)
              & (np.abs(region[..., 1] - 0x34) < 24)
              & (np.abs(region[..., 2] - 0x56) < 24)).sum()
    assert ground > 100, (
        f"FFrames drew no dark-ground ink in the avatar box: "
        f"{ground} ground pixels")
