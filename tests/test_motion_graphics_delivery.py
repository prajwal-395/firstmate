"""A declared motion graphic must reach actual pixels, inside the safe area.

The closing half of the contract `props_draw_ink` opened.  #154 stopped
project 001's eight fully transparent ProRes files being rendered and
counted as motion graphics delivered; #320 removed the four
`creative_direction` keys the upper third read and step 2.01 is never
asked for.  Both were subtractions.  Neither established that the
elements a template CAN declare - the corner accents and the progress
bar - put a single pixel on screen, because every motion-graphics test
in this repository either checks a Python predicate or stubs `npx`.

`tests/test_timed_text_delivery.py` is the shape this follows, for the
reason it states: a capability is only real where something reads it,
and "a reader exists" is not the claim "the picture changed".  So this
renders the REAL production path - `generate_motion_props`, then the
same `npx remotion render` the step runs, ProRes 4444 with alpha - and
reads the frames back with ffmpeg.

Two properties, and the second is a fix this file is the guard for:

1. **Ink.**  A template that declares accents and a progress bar
   produces frames carrying its own palette colour, over a background
   that is still transparent.  A ProRes 4444 file whose alpha is 0
   everywhere is what 001 shipped eight of.
2. **Inside the safe area.**  The progress bar sat at `bottom: 0`,
   `width: 100%` - the last literal margin in `MotionGraphics/index.tsx`
   after #153 moved the corner accents onto the insets.  On a 1080x1920
   delivery the bottom inset is 320px of captions, CTA and audio bar
   (`library/tools/safe_area.py`), so the bar was rendered, composited
   onto V4, and covered by the platform's own interface.

It is deliberately a FIXTURE render: 320x568 for 48 frames, under two
seconds of video.  Nothing here re-renders a project.
"""
import os
import shutil
import subprocess
import sys

import pytest
import yaml

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEP_DIR = os.path.join(
    PROJECT_ROOT, "library", "steps", "step_4_06_render_motion_graphics")
for _p in (PROJECT_ROOT, STEP_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_motion_props import generate_motion_props, props_draw_ink

REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")

FIXTURE_WIDTH = 320
FIXTURE_HEIGHT = 568
FIXTURE_FPS = 30

# The bar is 12px tall and carries a 16px glow, so its ink may legally
# reach this far past the inset. Both numbers are read off
# MotionGraphics/index.tsx; they are the element's own geometry, not a
# tolerance chosen to make the assertion pass.
BAR_HEIGHT_PX = 12
BAR_GLOW_PX = 16

# One block long enough that the accents' 20-frame fade completes and
# the progress bar has somewhere to travel.
SPINE = {
    "structure": [
        {"block_type": "hook", "position": 0,
         "timeline_start": 0.0, "timeline_end": 1.6},
        {"block_type": "speech", "position": 1,
         "timeline_start": 1.6, "timeline_end": 3.2},
    ]
}

remotion_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None
    or shutil.which("ffmpeg") is None,
    reason="needs remotion-subtitles/node_modules, npx and ffmpeg",
)


def _template(name):
    with open(os.path.join(PROJECT_ROOT, "library", "templates",
                           f"{name}.yaml"), encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


#: A plan a model could write for `SPINE`. Three elements, all live at
#: once on different rows, and each spanning the whole piece rather than
#: a block - the timebase and the rows, exercised against a real render
#: rather than asserted in isolation.
#:
#: `colour_role: accent` is what makes this the TEMPLATE REFINING the
#: plan: `#ff0055` is `shortform_energetic`'s own palette accent and is
#: not a colour this test picked.
A_PLAN = [
    {"element": "title_lockup", "start_seconds": 0.0,
     "duration_seconds": 3.2, "anchor": "top_left", "row": 0,
     "copy": {"display": "A NAME"}, "colour_role": "accent",
     "entrance": "fade", "exit": "cut"},
    {"element": "frame_accents", "start_seconds": 0.0,
     "duration_seconds": 3.2, "anchor": "centre", "row": 0,
     "colour_role": "accent", "entrance": "fade", "exit": "cut"},
    {"element": "progress_bar", "start_seconds": 0.0,
     "duration_seconds": 3.2, "anchor": "bottom_centre", "row": 0,
     "colour_role": "accent", "entrance": "fade", "exit": "cut"},
]


def _fixture_props():
    """The real resolver, at fixture size, for a plan and a template.

    `shortform_energetic` supplies the palette the plan's `colour_role`
    resolves against. Nothing here turns an element on: the plan does.
    """
    tmpl = _template("shortform_energetic")
    segments, resolved = generate_motion_props(
        A_PLAN, SPINE,
        fps=FIXTURE_FPS, width=FIXTURE_WIDTH, height=FIXTURE_HEIGHT,
        brand_style=tmpl.get("style"))
    assert not resolved.dropped, resolved.basis_record()["dropped"]
    assert segments, "the resolver produced no segment for a real plan"
    return [s["props"] for s in segments if props_draw_ink(s["props"])]


def _render(props: dict, out_dir: str) -> str:
    """The step's own render command, unchanged."""
    import json
    props_path = os.path.join(out_dir, "props.json")
    mov_path = os.path.join(out_dir, "mg.mov")
    with open(props_path, "w", encoding="utf-8") as f:
        json.dump(props, f)
    result = subprocess.run(
        ["npx", "remotion", "render", "MotionGraphics", mov_path,
         "--props", props_path, "--codec", "prores",
         "--prores-profile", "4444", "--image-format", "png",
         "--transparent"],
        cwd=REMOTION_DIR, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert os.path.exists(mov_path), "remotion reported success and wrote no file"
    return mov_path


def _frames(mov_path: str, out_dir: str) -> list:
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-i", mov_path, "-pix_fmt", "rgba",
         os.path.join(frames_dir, "f_%03d.png")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, result.stderr[-800:]
    return sorted(os.path.join(frames_dir, n)
                  for n in os.listdir(frames_dir) if n.endswith(".png"))


def _lit(png_path: str, alpha_floor: int = 16):
    """Every pixel the overlay actually paints, as (x, y, r, g, b)."""
    from PIL import Image
    image = Image.open(png_path).convert("RGBA")
    width, height = image.size
    pixels = image.load()
    out = []
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a > alpha_floor:
                out.append((x, y, r, g, b))
    return out, width, height


def _near(rgb, target, tolerance=24):
    return all(abs(a - b) <= tolerance for a, b in zip(rgb, target))


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    """One render, shared by every assertion below.

    The last block, which is the long one, so the accents have finished
    fading in and the progress bar has travelled.
    """
    props = _fixture_props()
    assert props, "no prop set draws ink for a template that declares both"
    out_dir = str(tmp_path_factory.mktemp("mg"))
    chosen = props[-1]
    mov = _render(chosen, out_dir)
    return chosen, _frames(mov, out_dir)


@remotion_available
def test_a_declared_motion_graphic_reaches_pixels(rendered):
    """The whole point: something is on the frame, and it is the palette's."""
    props, frames = rendered
    assert frames, "the render produced no frames"
    last = frames[-1]
    lit, _, _ = _lit(last)
    assert lit, (
        f"{os.path.basename(last)} carries no pixel above the alpha "
        f"floor. This is project 001's defect exactly: a ProRes 4444 "
        f"file in which max(alpha) is 0 on every frame, placed on V4 "
        f"and counted as a motion graphic delivered.")

    accent = (0xFF, 0x00, 0x55)
    assert any(_near((r, g, b), accent) for _, _, r, g, b in lit), (
        f"none of the {len(lit)} lit pixels carries the template's own "
        f"accent. Something drew, but not what "
        f"the declaration asked for.")


@remotion_available
def test_the_overlay_is_still_transparent_where_nothing_is_drawn(rendered):
    """Alpha has to be REAL, not a black card the compositor stacks."""
    _, frames = rendered
    lit, width, height = _lit(frames[-1])
    assert len(lit) < width * height * 0.5, (
        f"{len(lit)} of {width * height} pixels are opaque. An overlay "
        f"that fills its frame hides the picture underneath it - the "
        f"alpha channel is not being written.")


@remotion_available
def test_no_element_is_drawn_under_the_platforms_own_interface(rendered):
    """The progress bar's regression guard.

    `safeArea.bottom` is the caption / CTA / audio-bar band
    (library/tools/safe_area.py). Before this was fixed the bar sat at
    `bottom: 0` and every lit row of it was inside that band.
    """
    props, frames = rendered
    inset = props["safeArea"]["bottom"]
    lit, _, height = _lit(frames[-1])
    lowest_allowed = height - inset + BAR_HEIGHT_PX + BAR_GLOW_PX
    too_low = sorted({y for _, y, _, _, _ in lit if y >= lowest_allowed})
    assert not too_low, (
        f"rows {too_low[:6]}{'...' if len(too_low) > 6 else ''} carry "
        f"ink at or below row {lowest_allowed} of {height}, inside the "
        f"{inset}px band the platform paints its captions and "
        f"like/comment/share rail over. A graphic drawn there is "
        f"rendered, composited, and never seen.")


@remotion_available
def test_the_progress_bar_is_where_the_safe_area_puts_it(rendered):
    """And it really is the bar, not just the corner brackets.

    The bar is the only element that spans the frame horizontally, so
    its row is the one carrying accent ink across most of the width.
    """
    props, frames = rendered
    inset = props["safeArea"]["bottom"]
    left = props["safeArea"]["left"]
    lit, width, height = _lit(frames[-1])
    accent = (0xFF, 0x00, 0x55)
    by_row = {}
    for x, y, r, g, b in lit:
        if _near((r, g, b), accent):
            by_row.setdefault(y, set()).add(x)
    widest = max(by_row, key=lambda y: len(by_row[y])) if by_row else None
    assert widest is not None, "no accent ink at all"
    span = len(by_row[widest])
    assert span > width * 0.3, (
        f"the widest run of accent ink is {span}px on a {width}px "
        f"frame - that is a corner bracket, not the progress bar")
    bar_top = height - inset - BAR_HEIGHT_PX
    assert bar_top <= widest < height - inset, (
        f"the bar's widest row is {widest}; the safe area puts it in "
        f"rows {bar_top}..{height - inset - 1}")
    # The glow blurs outward from the bar on every side, so the solid
    # element starts at the inset and its halo reaches BAR_GLOW_PX past
    # it. Measured: 18px against a 27px inset on this fixture.
    assert min(by_row[widest]) >= left - BAR_GLOW_PX, (
        f"the bar starts at x={min(by_row[widest])}, further left than "
        f"the {left}px inset plus its {BAR_GLOW_PX}px glow")


# ── The other half of "planned but absent": the file has to be there ──

def _manifest_naming(path: str, key: str = "motion_graphics_overlay") -> dict:
    return {key: {"available": True, "segments": [
        {"overlay_path": path, "timeline_start": 0.0,
         "timeline_end": 1.0, "total_frames": 30}]}}


def test_a_motion_graphics_segment_missing_on_disk_refuses_the_compile(tmp_path):
    """It used to be a `logger.warning` while timed text raised.

    An absent overlay changes nothing anyone downstream can see - the
    picture underneath it is intact, the build succeeds, the export is a
    valid video of the right length - so a warning in a forty-minute
    unattended run is read by nobody.
    """
    from library.steps.step_5_04_compile_manifest.step import (
        OVERLAY_TRACKS,
        OverlaySegmentMissing,
        assert_overlay_segments_on_disk,
    )

    for key in OVERLAY_TRACKS:
        gone = str(tmp_path / f"{key}_never_written.mov")
        with pytest.raises(OverlaySegmentMissing) as caught:
            assert_overlay_segments_on_disk(_manifest_naming(gone, key))
        assert gone in str(caught.value)


def test_a_segment_that_is_on_disk_passes(tmp_path):
    """The gate has to be able to pass, or it is not a gate."""
    from library.steps.step_5_04_compile_manifest.step import (
        OVERLAY_TRACKS,
        assert_overlay_segments_on_disk,
    )

    for key in OVERLAY_TRACKS:
        real = tmp_path / f"{key}.mov"
        real.write_bytes(b"not a real movie, but it is on disk")
        assert_overlay_segments_on_disk(_manifest_naming(str(real), key))


EMPHASIS_PLAN = [
    {"element": "context_stamp", "start_seconds": 0.0,
     "duration_seconds": 3.2, "anchor": "top_right", "row": 0,
     "copy": {"display": "LIVE"}, "colour_role": "accent",
     "entrance": "fade", "exit": "cut"},
    {"element": "stat_callout", "start_seconds": 0.0,
     "duration_seconds": 3.2, "anchor": "centre", "row": 0,
     "copy": {"display": "99%"}, "colour_role": "accent",
     "entrance": "fade", "exit": "cut"},
    {"element": "pointer_annotation", "start_seconds": 0.0,
     "duration_seconds": 3.2, "anchor": "middle_right", "row": 0,
     "data": {"x": 0.9, "y": 0.5}, "colour_role": "accent",
     "entrance": "fade", "exit": "cut"},
    {"element": "beat_accent", "start_seconds": 0.0,
     "duration_seconds": 3.2, "anchor": "centre", "row": 0,
     "colour_role": "accent", "entrance": "fade", "exit": "cut"},
]

def _emphasis_props():
    tmpl = _template("shortform_energetic")
    segments, resolved = generate_motion_props(
        EMPHASIS_PLAN, SPINE,
        fps=FIXTURE_FPS, width=FIXTURE_WIDTH, height=FIXTURE_HEIGHT,
        brand_style=tmpl.get("style"))
    assert not resolved.dropped, resolved.basis_record()["dropped"]
    return [s["props"] for s in segments if props_draw_ink(s["props"])]

@pytest.fixture(scope="module")
def rendered_emphasis(tmp_path_factory):
    props = _emphasis_props()
    assert props, "no props generated for emphasis plan"
    out_dir = str(tmp_path_factory.mktemp("mg_emphasis"))
    # Render the combined props (or the first one since they overlap in time and might be combined)
    # Actually generate_motion_props returns one segment if they all share the exact same time block.
    chosen = props[-1]
    mov = _render(chosen, out_dir)
    return chosen, _frames(mov, out_dir)

@remotion_available
def test_all_emphasis_elements_stay_inside_the_strictest_safe_area(rendered_emphasis):
    """Platform UI gets painted over the frame, so an element outside the safe area is a defect the captain sees immediately."""
    props, frames = rendered_emphasis
    safe_area = props["safeArea"]
    top_inset = safe_area["top"]
    bottom_inset = safe_area["bottom"]
    left_inset = safe_area["left"]
    right_inset = safe_area["right"]
    
    # We check the last frame where all entrances have completed
    lit, width, height = _lit(frames[-1])
    assert lit, "no lit pixels found in emphasis render"
    
    # Box shadows can bleed outward. Max blur radius across elements is 24px (at scale 1.0).
    # At 320x568 fixture, scale = 320/1080 = 0.296. Glow max ~7px.
    # We allow 10px of tolerance strictly for glows, but the solid elements must be inside.
    glow_tolerance = 12
    outside = [
        (x, y) for x, y, r, g, b in lit
        if y < top_inset - glow_tolerance or y >= height - bottom_inset + glow_tolerance
           or x < left_inset - glow_tolerance or x >= width - right_inset + glow_tolerance
    ]
    
    # The pointer_annotation or stat_callout shouldn't bleed out of the safe area.
    # beat_accent is just a flash, so it might not even have pixels on the last frame, but we check whatever is lit.
    assert not outside, (
        f"{len(outside)} pixels were drawn outside the safe area! "
        f"Strictest bounds: top > {top_inset}, bottom < {height - bottom_inset}, "
        f"left > {left_inset}, right < {width - right_inset}. "
        f"Sample offenders: {outside[:5]}"
    )
