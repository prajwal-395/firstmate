"""The Through the 4th Wall Night card must reach actual pixels.

The second item in the captain's ratified build order (2026-08-20): one
series' intro card, as the proof that `effect.timed_text_overlay` is a
capability and not a slot. The card is
`tests/fixtures/night_card_project/project.yaml` - a PROJECT asset, which
is where `docs/ASSET_LIBRARY_PLAN.md` section 3 puts series artwork, and
it lives under tests/ only so this file can render it.

`tests/test_timed_text_delivery.py` proved the mechanism with primaries
on a 320x568 fixture. This proves the SHIPPED card, at the delivery
format the pipeline actually produces, against the geometry that render
actually has:

    project 001, exports/Pipeline_Edit.mp4, 1080x1920, 16:9 landscape
    source. Measured per row on frames at t=2.0s, 9.0s and 40.0s:
        rows    0.. 655   letterbox bar
        rows  656..1263   picture
        rows 1264..1919   letterbox bar, with burnt-in captions at
                          rows ~1699..1765

That band is the thing the removed 4th Wall end card missed: its `y:
0.15` put type at row 288, in dead black above the picture. So this file
asserts the card's ink lands inside rows 656..1263 - true whether the
episode's source is letterboxed like 001's or full-bleed vertical like a
real Night.

One render, 61 frames, the card's own length. Nothing here re-renders a
project.

THE TYPEFACE IS SUBSTITUTED, deliberately and visibly. The card declares
Nanum Pen Script, the series' locked face, and the captain ruled on
2026-08-20 that per-series typefaces live with their project and not in
the engine - so this repository must not carry the file. The render
below swaps in the engine's own bundled Montserrat and changes NOTHING
else; `test_the_render_differs_from_the_shipped_card_in_the_typeface_alone`
holds that to one key pair, and
`test_a_declared_typeface_that_is_not_staged_fails_the_render` covers the
half the substitution cannot: that the real face is loaded from the real
file or the render dies, rather than substituting in silence.
"""
import copy
import os
import shutil
import subprocess
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from library.tools.remotion_brand_linker import (  # noqa: E402
    REMOTION_BRAND_DIR,
    cleanup_brand_assets,
    prep_remotion,
)
from library.tools.render_fonts import BUNDLED_FONT_FAMILY  # noqa: E402
from library.tools.timed_text_overlay import resolve_declaration  # noqa: E402
from library.tools.timed_text_render import (  # noqa: E402
    TimedTextRenderError,
    render_timed_text_segments,
)

REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")
BUNDLED_FONT_FILE = os.path.join(
    REMOTION_DIR, "public", "fonts", "Montserrat-Variable.ttf")
CARD_PROJECT = os.path.join(
    PROJECT_ROOT, "tests", "fixtures", "night_card_project")

# The delivery format the card is authored against, and the picture band
# inside it. See the module docstring for how the band was measured.
WIDTH, HEIGHT, FPS = 1080, 1920, 30
PICTURE_TOP, PICTURE_BOTTOM = 656, 1263
CAPTION_TOP = 1699

# The two colours the card declares, from the series' locked palette.
FADED_BRASS = (0xD4, 0xA3, 0x4A)
ICE_BLUE = (0x00, 0xBF, 0xFF)

# A spine of the shape mesh_spine emits: the hook block's `position` is
# the string "hook", not 0. The card anchors to the END of it, which is
# the cold open line - "the hook comes first, the branding comes second".
SPINE = [
    {"position": "hook", "block_type": "hook",
     "timeline_start": 0.0, "timeline_end": 2.4},
    {"position": 1, "block_type": "speech",
     "timeline_start": 2.4, "timeline_end": 18.0},
]

renders_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None
    or shutil.which("ffmpeg") is None,
    reason="needs remotion-subtitles/node_modules, npx and ffmpeg",
)


def _card_declaration() -> dict:
    """The shipped card, straight out of the project it belongs to."""
    with open(os.path.join(CARD_PROJECT, "project.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f)["effect"]["timed_text_overlay"]


def _project_with_substituted_typeface(root: str) -> str:
    """A copy of the card project carrying a font the engine may ship.

    Everything about the card except the two font keys is copied
    verbatim, and the staged file goes where a project's own typeface
    would: `<project>/brand_assets/`, from which `prep_remotion` copies
    it into Remotion's `public/brand/`.
    """
    os.makedirs(os.path.join(root, "brand_assets"), exist_ok=True)
    shutil.copy2(BUNDLED_FONT_FILE,
                 os.path.join(root, "brand_assets", "Montserrat-Variable.ttf"))

    with open(os.path.join(CARD_PROJECT, "project.yaml"), encoding="utf-8") as f:
        config = yaml.safe_load(f)
    declaration = config["effect"]["timed_text_overlay"]
    declaration["font_family"] = BUNDLED_FONT_FAMILY
    declaration["font_file"] = "Montserrat-Variable.ttf"
    with open(os.path.join(root, "project.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False)
    return root


def _extract_frames(mov_path: str, out_dir: str) -> list:
    """Every frame of the segment as RGBA PNGs, alpha preserved."""
    os.makedirs(out_dir, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", mov_path, "-pix_fmt", "rgba",
         os.path.join(out_dir, "frame_%03d.png")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr[-800:]
    return sorted(
        os.path.join(out_dir, n) for n in os.listdir(out_dir)
        if n.endswith(".png"))


def _colour_pixels(png_path: str, colour: tuple, min_alpha: int = 200,
                   tolerance: int = 30) -> list:
    """Every (x, y) drawing the given colour at or above ``min_alpha``.

    ``min_alpha`` is the whole point of the parameter: a presence check
    wants fully opaque ink, so a fade cannot be mistaken for the moment
    running; an ABSENCE check wants every pixel however faint, or a
    moment that is fading in reads as gone.
    """
    from PIL import Image
    image = Image.open(png_path).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    found = []
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a < min_alpha:
                continue
            if all(abs(c - t) <= tolerance for c, t in zip((r, g, b), colour)):
                found.append((x, y))
    return found


def _rows(pixels: list) -> set:
    return {y for _x, y in pixels}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """The real production path, at the real delivery format."""
    work = tmp_path_factory.mktemp("night_card")
    project = _project_with_substituted_typeface(str(work / "project"))

    prep = prep_remotion(project_folder=project)
    assert prep["brand"]["linked"], prep
    assert os.path.exists(
        os.path.join(REMOTION_BRAND_DIR, "Montserrat-Variable.ttf")), (
        "the project's typeface did not reach Remotion's public/brand/")

    try:
        segments = render_timed_text_segments(
            resolve_declaration({}, project),
            REMOTION_DIR,
            str(work / "segments"),
            fps=FPS, width=WIDTH, height=HEIGHT,
            spine_structure=SPINE,
        )
        frames = _extract_frames(
            segments[0]["overlay_path"], str(work / "frames"))
        yield segments, frames
    finally:
        cleanup_brand_assets()


# ─────────────────────────────────────────────────────────
# The card is one placeable segment, timed from the spine
# ─────────────────────────────────────────────────────────

def test_the_card_is_timed_from_the_hook_block_not_from_frames():
    """Re-cut the cold open and the card moves with it.

    The removed end card is the counter-example: absolute frames from a
    60.000s trial cut, landing 2.63s and 4.13s past the end of the
    54.869s render on disk. Here the same declaration produces a
    different timeline position for a different hook, with nothing edited.
    """
    from library.tools.timed_text_overlay import plan_timed_text_segments

    declaration = {"timed_text_overlay": _card_declaration()}
    as_shipped = plan_timed_text_segments(
        declaration, fps=FPS, width=WIDTH, height=HEIGHT,
        spine_structure=SPINE)
    longer_hook = copy.deepcopy(SPINE)
    longer_hook[0]["timeline_end"] = 4.9
    longer_hook[1]["timeline_start"] = 4.9
    re_cut = plan_timed_text_segments(
        declaration, fps=FPS, width=WIDTH, height=HEIGHT,
        spine_structure=longer_hook)

    assert as_shipped[0]["timeline_start"] == pytest.approx(2.533, abs=0.01)
    # The hook grew by 2.5s, so the card did too - to within the frames
    # the two starts independently round to.
    assert (re_cut[0]["timeline_start"] - as_shipped[0]["timeline_start"]
            == pytest.approx(2.5, abs=2 / FPS))
    # Its own length travels with it, to within the frame each moment's
    # start independently rounds to.
    assert as_shipped[0]["total_frames"] == 61
    assert abs(re_cut[0]["total_frames"] - 61) <= 1, (
        "the card's own length must not depend on where it lands")


def test_the_card_declares_geometry_inside_the_picture_the_pipeline_produces():
    """Both moments clear the letterbox bars, before anything renders.

    Positions are normalised against the whole delivery frame, so a
    declaration that must clear the bars states its own `y`. These are
    the values the card states, checked against the measured band.
    """
    for moment in _card_declaration()["moments"]:
        row = moment["y"] * HEIGHT
        assert PICTURE_TOP < row < PICTURE_BOTTOM, (
            f"{moment['text']!r} at y={moment['y']} is row {row:.0f}, "
            f"outside the picture band {PICTURE_TOP}..{PICTURE_BOTTOM}")
        assert row < CAPTION_TOP, (
            f"{moment['text']!r} would sit in the caption band")


def test_the_card_is_short_enough_to_be_an_intro_card():
    """"Be short - 2-3 seconds max" (overall_branding_creative_direction)."""
    moments = _card_declaration()["moments"]
    spans = [(m["offset_seconds"], m["offset_seconds"] + m["duration_seconds"])
             for m in moments]
    total = max(e for _s, e in spans) - min(s for s, _e in spans)
    assert 1.0 <= total <= 3.0, f"the card is on screen for {total}s"


# ─────────────────────────────────────────────────────────
# The pixels
# ─────────────────────────────────────────────────────────

@renders_available
def test_the_card_renders_one_segment_of_its_own_length(rendered):
    segments, frames = rendered
    assert len(segments) == 1, "the two moments overlap; they are one card"
    segment = segments[0]
    assert segment["moment_count"] == 2
    assert segment["total_frames"] == 61
    assert segment["timeline_start"] == pytest.approx(2.533, abs=0.01)
    assert len(frames) == 61
    assert os.path.getsize(segment["overlay_path"]) > 0


@renders_available
def test_both_lines_are_in_the_picture_where_the_card_is_at_full_opacity(
        rendered):
    """Segment frame 30: label and series name both fully opaque."""
    _segments, frames = rendered
    frame = frames[30]
    brass = _colour_pixels(frame, FADED_BRASS)
    blue = _colour_pixels(frame, ICE_BLUE)
    assert brass, f"no Faded Brass 'Night 1' in {frame}"
    assert blue, f"no Ice Blue 'Through the 4th Wall' in {frame}"
    assert max(_rows(brass)) < min(_rows(blue)), (
        "the episode label sits above the series name")


@renders_available
def test_every_pixel_of_the_card_lands_inside_the_picture(rendered):
    """The failure the removed end card shipped: type in the black bar."""
    _segments, frames = rendered
    frame = frames[30]
    for name, colour in (("Night 1", FADED_BRASS),
                         ("Through the 4th Wall", ICE_BLUE)):
        rows = _rows(_colour_pixels(frame, colour, min_alpha=1))
        assert rows, f"no {name!r} ink at all in {frame}"
        assert PICTURE_TOP <= min(rows) and max(rows) <= PICTURE_BOTTOM, (
            f"{name!r} renders at rows {min(rows)}-{max(rows)}, outside the "
            f"picture band {PICTURE_TOP}..{PICTURE_BOTTOM} of the frame this "
            f"pipeline produces")
        assert max(rows) < CAPTION_TOP, f"{name!r} collides with the captions"


@renders_available
def test_the_card_is_legible_where_it_lands(rendered):
    """Real glyphs at a real size, not a hairline nobody can read."""
    _segments, frames = rendered
    frame = frames[30]
    for name, colour, declared_size, min_ink in (
            ("Night 1", FADED_BRASS, 72, 1500),
            ("Through the 4th Wall", ICE_BLUE, 52, 1500)):
        pixels = _colour_pixels(frame, colour)
        rows = _rows(pixels)
        columns = {x for x, _y in pixels}
        height = max(rows) - min(rows) + 1
        assert len(pixels) >= min_ink, (
            f"{name!r} draws only {len(pixels)} opaque pixels")
        # Cap height is well under the em, so this is a floor rather than
        # a measurement of the face.
        assert height >= declared_size * 0.45, (
            f"{name!r} declared font_size {declared_size} but its ink is "
            f"only {height}px tall")
        # And an upper bound, because the failure it catches is silent:
        # an absolutely positioned box with only `left` set is
        # (frame width - left) px wide, so a moment at x=0.5 used to wrap
        # at HALF the frame. "Through the 4th Wall" broke onto two lines
        # and its second line rendered 60px below the declared y.
        assert height <= declared_size * 1.6, (
            f"{name!r} draws {height}px of ink for a {declared_size}px "
            f"font - it has wrapped onto more than one line")
        assert 0 < min(columns) and max(columns) < WIDTH - 1, (
            f"{name!r} touches the edge of the frame")


@renders_available
def test_the_series_name_is_absent_before_it_starts(rendered):
    """Segment frame 9: the label is opaque, the name has not begun."""
    _segments, frames = rendered
    frame = frames[9]
    assert _colour_pixels(frame, FADED_BRASS), f"no 'Night 1' in {frame}"
    assert not _colour_pixels(frame, ICE_BLUE, min_alpha=1), (
        f"the series name starts at segment frame 10 but is already in "
        f"{frame}")


@renders_available
def test_the_label_is_absent_after_it_ends(rendered):
    """Segment frame 60: the label ended at 60, the name runs to 61."""
    _segments, frames = rendered
    frame = frames[60]
    assert not _colour_pixels(frame, FADED_BRASS, min_alpha=1), (
        f"'Night 1' ends at segment frame 60 but is still in {frame}")
    assert _colour_pixels(frame, ICE_BLUE, min_alpha=1), (
        f"the series name should still be fading out in {frame}")


@renders_available
def test_the_card_composites_over_the_picture(rendered):
    """An opaque background would hide the frame it contextualises."""
    from PIL import Image
    _segments, frames = rendered
    image = Image.open(frames[30]).convert("RGBA")
    pixels = image.load()
    width, height = image.size
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1),
                 (width - 1, height - 1), (width // 2, 40)):
        assert pixels[x, y][3] == 0, (
            f"pixel ({x},{y}) has alpha {pixels[x, y][3]}; the segment "
            f"would paint over the video underneath it")


# ─────────────────────────────────────────────────────────
# The typeface: the half the substitution cannot prove
# ─────────────────────────────────────────────────────────

def test_the_render_differs_from_the_shipped_card_in_the_typeface_alone(
        tmp_path):
    """Bound the substitution, so the pixel proof stays a proof.

    If anything but the two font keys could differ, the frames above
    would be evidence about some other card.
    """
    substituted = os.path.join(
        str(_project_with_substituted_typeface(str(tmp_path / "p"))),
        "project.yaml")
    with open(substituted, encoding="utf-8") as f:
        rendered_declaration = yaml.safe_load(f)["effect"]["timed_text_overlay"]
    shipped = _card_declaration()

    differing = {k for k in set(shipped) | set(rendered_declaration)
                 if shipped.get(k) != rendered_declaration.get(k)}
    assert differing == {"font_family", "font_file"}, differing
    assert shipped["font_family"] == "Nanum Pen Script", (
        "the card must keep declaring the series' locked typeface")


@renders_available
def test_a_declared_typeface_that_is_not_staged_fails_the_render(tmp_path):
    """A missing per-series font must never substitute in silence.

    This is the card's own declaration, with its own Nanum Pen Script
    file genuinely absent - the state this repository is in and must stay
    in, because the engine may not carry a series typeface. Chromium
    would happily draw the copy in its fallback sans and produce a valid
    ProRes file of the right size; the loader throws instead.
    """
    cleanup_brand_assets()
    declaration = {"timed_text_overlay": _card_declaration()}
    with pytest.raises(TimedTextRenderError) as raised:
        render_timed_text_segments(
            declaration, REMOTION_DIR, str(tmp_path / "segments"),
            fps=FPS, width=WIDTH, height=HEIGHT, spine_structure=SPINE)
    assert "NanumPenScript-Regular.ttf" in str(raised.value)
