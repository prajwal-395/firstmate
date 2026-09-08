"""A full-frame card's image slot: the brand logo it could never draw.

Found 2026-09-08 by the animated-reel lane
(docs/ANIMATED_REEL_CEILING.md): `FullFrameCard` props carried no image
slot, so the staged logo SVGs/PNGs the brand linker made reachable were
undrawable by the composition. No eyebrow text substitutes for a
wordmark.

The shape follows `channel_bug`, not a second mechanism: the
declaration names a file out of the project's own brand_assets/, the
plan resolves it to a staged public path, the composition draws it,
and a name that resolves to nothing REFUSES the card - the engine
ships no artwork (AGENTS.md 14) and there is no substitute to draw.

Every gate here is proved in BOTH directions, and the drawing itself
is proved by RENDERING, not by asserting a node exists: the still test
below fails on a composition that ignores the prop, because a source
parse proves existence and only pixels prove drawing.
"""
import copy
import json
import os
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import full_frame_element as ffe  # noqa: E402

REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")

renders_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None,
    reason="needs remotion-subtitles/node_modules and npx",
)

FPS = 30
WIDTH = 540
HEIGHT = 960


def _declaration(**overrides):
    base = {
        "element": "full_frame_card",
        "placement": "head",
        "duration_seconds": 2.0,
        "background": "#000000",
        "font_family": "Montserrat",
        "runs": [
            {"text": "Season two", "type_role": "display",
             "colour": "#FFFFFF"},
        ],
    }
    base.update(overrides)
    return {"full_frame_elements": [base]}


def _facts():
    return ffe.ReelFacts(reel_number=1, speakers=("A",),
                         opening=(), opening_window=3.0)


# ── The declaration ────────────────────────────────────────────────


def test_a_card_may_name_a_project_image():
    """The other half of every refusal below."""
    normalised = ffe.declared_elements(
        _declaration(image="lucie-logo.png", image_width=320))
    assert normalised[0]["image"] == "lucie-logo.png"
    assert normalised[0]["image_width"] == 320.0


def test_a_card_naming_no_image_is_still_valid():
    """The slot is optional: every declaration on disk names none."""
    normalised = ffe.declared_elements(_declaration())
    assert normalised[0]["image"] is None
    assert normalised[0]["image_width"] is None


@pytest.mark.parametrize("overrides,expect", [
    ({"image": ""}, "brand_assets"),
    ({"image": "   "}, "brand_assets"),
    ({"image": 42}, "brand_assets"),
    ({"image": "logo.png", "image_width": 0}, "positive number"),
    ({"image": "logo.png", "image_width": -4}, "positive number"),
    ({"image": "logo.png", "image_width": "wide"}, "positive number"),
    ({"image_width": 320}, "does not name"),
])
def test_a_malformed_image_declaration_is_refused_by_name(
        overrides, expect):
    with pytest.raises(ffe.FullFrameDeclarationError, match=expect):
        ffe.declared_elements(_declaration(**overrides))


# ── Planning ───────────────────────────────────────────────────────


def test_a_named_image_resolves_to_the_staged_path():
    declarations = ffe.declared_elements(
        _declaration(image="lucie-logo.png", image_width=320))
    cards = ffe.plan_reel_cards(
        declarations, _facts(), 100, FPS,
        resolve_asset=lambda name: f"brand/{name}")
    assert cards[0].props["image"] == "brand/lucie-logo.png"
    assert cards[0].props["imageWidth"] == 320.0


def test_an_unstated_width_leaves_no_width_in_props():
    """Containment is the composition's mechanics, not a planned number."""
    declarations = ffe.declared_elements(
        _declaration(image="lucie-logo.png"))
    cards = ffe.plan_reel_cards(
        declarations, _facts(), 100, FPS,
        resolve_asset=lambda name: f"brand/{name}")
    assert cards[0].props["image"] == "brand/lucie-logo.png"
    assert "imageWidth" not in cards[0].props


def test_a_card_naming_no_image_carries_no_image_key():
    """Props written before this slot existed render byte-identically."""
    declarations = ffe.declared_elements(_declaration())
    cards = ffe.plan_reel_cards(declarations, _facts(), 100, FPS)
    assert "image" not in cards[0].props
    assert "imageWidth" not in cards[0].props


def test_an_unresolvable_image_refuses_the_card():
    """The engine ships no artwork, so there is no substitute to draw."""
    declarations = ffe.declared_elements(
        _declaration(image="missing-logo.png"))
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="not in the project's brand_assets"):
        ffe.plan_reel_cards(declarations, _facts(), 100, FPS,
                            resolve_asset=lambda name: "")


def test_an_image_with_no_lookup_refuses_the_card():
    declarations = ffe.declared_elements(
        _declaration(image="lucie-logo.png"))
    with pytest.raises(ffe.FullFrameDeclarationError,
                       match="no way to look a project asset up"):
        ffe.plan_reel_cards(declarations, _facts(), 100, FPS)


# ── The frame ──────────────────────────────────────────────────────


def _wordmark_stand_in(path: str, width: int = 400,
                       height: int = 160) -> None:
    """A stand-in for a project's own wordmark.

    Drawn, not shipped: the engine holds no artwork (AGENTS.md 14), so
    the fixture is generated here and staged only for the render below.
    Sun Orange on transparent - a colour neither the black ground nor
    the white runs carry, so its presence proves the IMAGE drew.
    """
    from PIL import Image, ImageDraw
    mark = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(mark)
    draw.rectangle([0, 0, width - 1, height - 1],
                   fill=(255, 170, 77, 255))
    draw.rectangle([10, 10, width - 11, height - 11],
                   outline=(128, 60, 10, 255), width=6)
    mark.save(path, "PNG")


def _is_mark(pixel) -> bool:
    r, g, b = pixel[0], pixel[1], pixel[2]
    return r > 200 and 100 < g < 220 and b < 120


def _card_props(tmp_path, image=None, image_width=None):
    props = {
        "runs": [{"text": "Season two", "type_role": "display",
                  "colour": "#FFFFFF"}],
        "background": "#000000",
        "entrance": "cut",
        "exit": "cut",
        "fontFamily": "Montserrat",
        "fps": FPS,
        "width": WIDTH,
        "height": HEIGHT,
        "durationInFrames": 30,
        "safeArea": {"top": 60, "right": 60, "bottom": 160, "left": 45},
    }
    if image is not None:
        props["image"] = image
    if image_width is not None:
        props["imageWidth"] = image_width
    return props


def _still(props: dict, tmp_path, name: str):
    from PIL import Image
    props_path = tmp_path / f"{name}.json"
    props_path.write_text(json.dumps(props), encoding="utf-8")
    out_path = tmp_path / f"{name}.png"
    result = subprocess.run(
        ["npx", "remotion", "still", "FullFrameCard", str(out_path),
         "--frame=15", f"--props={props_path}", "--image-format=png"],
        cwd=REMOTION_DIR, capture_output=True, text=True,
        encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return Image.open(out_path).convert("RGB")


@renders_available
def test_the_named_image_reaches_pixels(tmp_path):
    """The staged wordmark reaches the frame; without it, it does not.

    Two stills of the same card, the only difference the `image` prop.
    A composition that merely carries the prop and never draws it
    renders both identically - which is exactly the defect this pins,
    so this test FAILS on the pre-fix composition and PASSES after.
    """
    pytest.importorskip("PIL", reason="needs Pillow to draw the fixture")
    from PIL import Image

    brand_dir = os.path.join(REMOTION_DIR, "public", "brand")
    os.makedirs(brand_dir, exist_ok=True)
    staged = os.path.join(brand_dir, "fullframe_wordmark_fixture.png")
    fixture = tmp_path / "wordmark.png"
    _wordmark_stand_in(str(fixture))
    shutil.copy2(str(fixture), staged)
    try:
        plain = _still(_card_props(tmp_path), tmp_path, "card_plain")
        branded = _still(
            _card_props(tmp_path,
                        image="brand/fullframe_wordmark_fixture.png",
                        image_width=300),
            tmp_path, "card_branded")

        assert plain.size == branded.size == (WIDTH, HEIGHT)
        plain_px = plain.load()
        branded_px = branded.load()
        mark_in_plain = sum(
            1 for y in range(HEIGHT) for x in range(WIDTH)
            if _is_mark(plain_px[x, y]))
        assert mark_in_plain == 0, (
            f"the card with no image drew {mark_in_plain} wordmark "
            f"pixels - the control frame is contaminated")

        mark_xs = [x for y in range(HEIGHT) for x in range(WIDTH)
                   if _is_mark(branded_px[x, y])]
        assert len(mark_xs) > 20000, (
            f"the named image drew only {len(mark_xs)} wordmark pixels")
        drawn_width = max(mark_xs) - min(mark_xs)
        assert 250 <= drawn_width <= 350, (
            f"the declared image_width=300 drew {drawn_width}px wide")

        changed = sum(
            1 for y in range(HEIGHT) for x in range(WIDTH)
            if abs(branded_px[x, y][0] - plain_px[x, y][0]) > 8
            or abs(branded_px[x, y][1] - plain_px[x, y][1]) > 8
            or abs(branded_px[x, y][2] - plain_px[x, y][2]) > 8)
        assert changed > 30000, (
            f"the image changed only {changed} pixels against the card "
            f"without one")
    finally:
        if os.path.exists(staged):
            os.remove(staged)
