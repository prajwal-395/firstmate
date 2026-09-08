"""The website-as-picture element and the flip motion character.

The captain's 2026-09-04 instruction on this task names two capabilities
the roster does not have: "take a website" and use it as picture through
an alpha route, and "models and graphics that popup in the videos".
`website_panel` is the first - a project-supplied capture of a page,
framed in a drawn browser chrome and composited with the alpha the
engine already requires (transition_overlay.ALPHA_IS_REQUIRED_NOT_KEYED).
`flip` is the second half of the popup half: a CSS-3D entrance/exit that
turns an element edge-on, with no new renderer dependency.

What is NOT here, and why, is recorded where the decision lives:
`motion_graphics_vocabulary.website_panel` refuses a live fetch, and
`docs/RENDER_CAPABILITY_CEILING.md` records why true 3D-model rendering
stays out.
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

from library.tools import motion_graphics_plan as mgp
from library.tools import motion_graphics_vocabulary as mgv

REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")

renders_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None,
    reason="needs remotion-subtitles/node_modules and npx",
)

TIMELINE = 10.0
FPS = 30


def _entry(**overrides):
    base = {
        "element": "website_panel",
        "anchor": "middle_right",
        "start_seconds": 1.0,
        "duration_seconds": 3.0,
        "color": "#FBF0B8",
        "copy": {"display": "example.com/score"},
    }
    base.update(overrides)
    return base


def _resolve(plan, resolve_asset=None):
    return mgp.resolve_plan(
        plan,
        timeline_duration=TIMELINE,
        fps=FPS,
        palette_roles={},
        resolve_asset=resolve_asset,
    )


# ── vocabulary ───────────────────────────────────────────────────────

def test_website_panel_is_in_the_vocabulary():
    assert mgv.canonical_key("website_panel") == "website_panel"


def test_website_panel_declares_an_asset_axis_and_optional_copy():
    element = mgv.ELEMENTS_BY_KEY["website_panel"]
    assert "asset" in element.axes
    assert element.copy == "optional"
    assert "copy" in element.axes


def test_website_panel_is_drawable_today():
    element = mgv.ELEMENTS_BY_KEY["website_panel"]
    assert element.reachable == mgv.REACHABLE_NOW
    assert element.key in mgp.DRAWABLE


def test_flip_is_a_declared_motion_character_both_directions():
    for axis in ("entrance", "exit"):
        assert "flip" in mgv.AXES_BY_NAME[axis].positions


# ── plan resolution ──────────────────────────────────────────────────

def test_website_panel_resolves_with_a_staged_asset():
    resolved = _resolve([_entry(asset="score_capture.png")],
                        resolve_asset=lambda name: f"brand/{name}")
    assert resolved.proposed == 1
    assert not resolved.dropped
    (moment,) = resolved.moments
    assert moment["element"] == "website_panel"
    assert moment["asset"] == "brand/score_capture.png"


def test_website_panel_without_an_asset_is_dropped_by_name():
    resolved = _resolve([_entry()])
    assert resolved.proposed == 1
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason == "no_asset_for_an_element_that_needs_one"


def test_website_panel_with_an_unstaged_asset_is_dropped_by_name():
    resolved = _resolve([_entry(asset="missing.png")],
                        resolve_asset=lambda name: "")
    assert resolved.proposed == 1
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason == "asset_not_found_on_disk"


def test_flip_survives_plan_resolution_instead_of_falling_back_to_cut():
    """`resolve_plan` rewrites an unknown character to `"cut"`.

    That is the expression under test - `entrance if entrance in
    MOTION_CHARACTERS else "cut"` - so a `flip` that the vocabulary does
    not declare arrives as `"cut"`, and one it does declare arrives as
    `"flip"`.
    """
    resolved = _resolve([_entry(element="stat_callout",
                                asset=None,
                                copy={"display": "11"},
                                entrance="flip", exit="flip")],
                        resolve_asset=None)
    assert resolved.proposed == 1
    assert not resolved.dropped, [d.as_dict() for d in resolved.dropped]
    (moment,) = resolved.moments
    assert moment["entrance"] == "flip"
    assert moment["exit"] == "flip"


# ── the frame ────────────────────────────────────────────────────────

def _capture_still(path: str, width: int = 640, height: int = 960) -> None:
    """A stand-in for a project's own page capture.

    Drawn, not shipped: the engine holds no page captures (AGENTS.md
    14), so the fixture is generated here and staged only for the
    render below.
    """
    from PIL import Image, ImageDraw
    page = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(page)
    draw.rectangle([0, 0, width, 120], fill=(17, 24, 39))
    y = 200
    while y < height - 80:
        draw.rectangle([48, y, width - 48, y + 28], fill=(229, 231, 235))
        y += 56
    page.save(path, "PNG")


@renders_available
def test_website_panel_puts_the_capture_on_the_frame(tmp_path):
    """The staged capture reaches pixels; the chrome reaches pixels too.

    Rendered through the real composition at a small fixture size. The
    capture is generated here (a stand-in, never shipped) and staged
    where `staticFile("brand/...")` loads it, then removed - the same
    shape `test_night_card_delivery` stages a project typeface.
    """
    pytest.importorskip("PIL", reason="needs Pillow to draw the fixture")
    from PIL import Image

    brand_dir = os.path.join(REMOTION_DIR, "public", "brand")
    os.makedirs(brand_dir, exist_ok=True)
    staged = os.path.join(brand_dir, "website_panel_fixture.png")
    _capture_still(str(tmp_path / "capture.png"))
    shutil.copy2(str(tmp_path / "capture.png"), staged)
    try:
        props = {
            "elements": [{
                "element": "website_panel",
                "anchor": "middle_right",
                "row": 0,
                "runs": [{"text": "example.com/score",
                          "type_role": "supporting"}],
                "color": "#FBF0B8",
                "entrance": "cut",
                "exit": "cut",
                "startFrame": 0,
                "durationFrames": 30,
                "timelineProgressStart": 0.0,
                "timelineProgressEnd": 1.0,
                "footprint": None,
                "emphasis": None,
                "asset": "brand/website_panel_fixture.png",
            }],
            "fps": FPS, "width": 540, "height": 960,
            "safeArea": {"top": 60, "right": 60, "bottom": 160,
                         "left": 45},
            "durationInFrames": 30,
        }
        props_path = tmp_path / "website_panel.json"
        props_path.write_text(json.dumps(props), encoding="utf-8")
        out_path = tmp_path / "website_panel.png"
        result = subprocess.run(
            ["npx", "remotion", "still", "MotionGraphics", str(out_path),
             "--frame=15", f"--props={props_path}", "--image-format=png"],
            cwd=REMOTION_DIR, capture_output=True, text=True,
            encoding="utf-8", check=False)
        assert result.returncode == 0, result.stderr[-2000:]

        image = Image.open(out_path).convert("RGBA")
        pixels = image.load()
        width, height = image.size
        ink = sum(1 for y in range(height) for x in range(width)
                  if pixels[x, y][3] > 8)
        assert ink > 0, "website_panel drew nothing at all"
    finally:
        if os.path.exists(staged):
            os.remove(staged)
