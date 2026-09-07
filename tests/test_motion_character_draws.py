"""Every declared entrance and exit character must CHANGE THE FRAME.

`render_capability_index` proves a character has a node in the source.
This proves the node puts something on a frame that opacity alone cannot
explain - which is the claim the source parse cannot make and the one
that matters.

**Two characters passed the source parse and drew nothing**, and both are
recorded in `docs/RENDER_CAPABILITY_CEILING.md`:

* `exit: "typewriter"` had an arm, a comment saying characters
  "disappear in reverse", and no reverse reveal anywhere. The ink sat at
  x 235..845 mid-exit against 232..847 held - the same glyphs, dimmer.
* `glitch` carried its chromatic split as `textShadow` on the container,
  which `Runs` overrides by setting its own on every run. `max|R-B|` over
  every visible pixel was 0.

Neither is visible from the source. Both are obvious in a render, which
is why this file renders.

**The measurements are of what opacity CANNOT change**: where the ink is,
how big it is, and what colour it separates into. A frame at half opacity
has the same ink in the same place; a frame that slid, scaled, clipped,
revealed or split does not. Comparing raw pixels or an alpha IoU instead
would confound the two - measured, `fade` scores 0.873 IoU against the
held frame and `glitch` 0.917, so an IoU test would call the character
that draws nothing extra MORE distinct than the one that draws an RGB
split.

Runs at the size and shape the pipeline delivers - 1080x1920, the real
safe-area insets - because a character that works on a demo card and not
in a reel has not been proven.
"""

import json
import os
import shutil
import subprocess

import pytest

from library.tools import motion_graphics_vocabulary as vocabulary
from library.tools import render_capability_index as capability

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")

pytest.importorskip("PIL", reason="needs Pillow to measure a rendered frame")

remotion_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None,
    reason="needs remotion-subtitles/node_modules and npx; runs on any "
           "machine that has done `npm install` in remotion-subtitles, "
           "which is every machine that can render the pipeline's overlays",
)

#: The delivery shape, and the real insets for it.
WIDTH, HEIGHT, FPS = 1080, 1920, 30
SAFE_AREA = {"top": 120, "right": 120, "bottom": 320, "left": 90}

#: Long enough that a ramp is a small part of it, so the mid-ramp frame
#: of the entrance and the mid-ramp frame of the exit never overlap.
DURATION_FRAMES = 60

#: Frames sampled. The longest ramp is `typewriter` at 20 frames, so
#: frame 4 is inside every entrance ramp and frame 56 inside every exit
#: ramp, whatever the character.
ENTRANCE_FRAME = 4
EXIT_FRAME = DURATION_FRAMES - 4
HOLD_FRAME = 30


def _props(character: str, direction: str) -> dict:
    return {
        "elements": [{
            "element": "title_lockup", "anchor": "centre", "row": 0,
            "runs": [{"text": "MOTION CHECK", "type_role": "display"},
                     {"text": "every character, at delivery size",
                      "type_role": "supporting"}],
            "color": "#FFFFFF",
            "entrance": character if direction == "entrance" else "cut",
            "exit": character if direction == "exit" else "cut",
            "startFrame": 0, "durationFrames": DURATION_FRAMES,
            "timelineProgressStart": 0.0, "timelineProgressEnd": 1.0,
            "footprint": None, "emphasis": None,
        }],
        "fps": FPS, "width": WIDTH, "height": HEIGHT,
        "safeArea": SAFE_AREA, "durationInFrames": DURATION_FRAMES,
    }


def _still(tmp_path, character, direction, frame):
    props_path = tmp_path / f"{direction}_{character}.json"
    out_path = tmp_path / f"{direction}_{character}_{frame}.png"
    props_path.write_text(json.dumps(_props(character, direction)),
                          encoding="utf-8")
    result = subprocess.run(
        ["npx", "remotion", "still", "MotionGraphics", str(out_path),
         f"--frame={frame}", f"--props={props_path}", "--image-format=png"],
        cwd=REMOTION_DIR, capture_output=True, text=True,
        encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return out_path


def _measure(path):
    """What a change of opacity cannot alter.

    **The alpha is NORMALISED by its own maximum before the ink is
    thresholded**, and that is not a detail. Without it this measurement
    is confounded by exactly the thing it exists to exclude: at 20%
    opacity a solid glyph pixel is alpha 51 and its anti-aliased edge is
    alpha 10, so a fixed threshold eats the edge and reports the ink as
    narrower. Measured that way, `test_typewriter_reveals_in_both_directions`
    PASSED against a build whose typewriter exit had been deliberately
    disabled - the width shrank because the element was fading, not
    because any character had been concealed. Dividing by the frame's own
    maximum removes the uniform scale and leaves only the shape.
    """
    import numpy as np
    from PIL import Image

    image = np.asarray(Image.open(path).convert("RGBA")).astype(int)
    raw = image[..., 3]
    peak = int(raw.max())
    assert peak > 0, f"{path} has no ink at all"
    alpha = raw * (255.0 / peak)
    ink = alpha > 8
    assert ink.any(), f"{path} has no ink at all"
    ys, xs = np.where(ink)
    red, blue = image[..., 0], image[..., 2]
    # Chroma is read from the RAW alpha: a channel split is a difference
    # between colours, and normalising cannot manufacture one.
    visible = raw > 16
    return {
        "left": int(xs.min()), "right": int(xs.max()),
        "top": int(ys.min()), "bottom": int(ys.max()),
        "width": int(xs.max() - xs.min()),
        "height": int(ys.max() - ys.min()),
        # Pixels where red and blue have come apart. Zero for every
        # character but `glitch`, whatever the opacity.
        "chroma": int(((np.abs(red - blue) > 40) & visible).sum()),
    }


@pytest.fixture(scope="module")
def held(tmp_path_factory):
    """The element at rest: full opacity, no ramp active."""
    directory = tmp_path_factory.mktemp("held")
    return _measure(_still(directory, "cut", "entrance", HOLD_FRAME))


@pytest.fixture(scope="module")
def fade_control(tmp_path_factory):
    """`fade` at its own mid-ramp, in each direction. The null hypothesis.

    **This is the control, and normalising the alpha is not enough
    without it.** A drop shadow's faint tail quantises to alpha 0 at low
    opacity and no normalisation recovers a zero, so every ramped frame's
    ink box is a few pixels tighter than the held frame's whatever the
    character does. Measured against `held` alone,
    `test_typewriter_reveals_in_both_directions` PASSED twice against a
    build whose typewriter exit was deliberately disabled - once before
    the alpha was normalised, and again after.

    `fade` changes opacity and nothing else, so whatever box drift it
    shows IS the drift opacity causes. A character has to beat it.
    """
    directory = tmp_path_factory.mktemp("fade")
    return {
        "entrance": _measure(_still(directory, "fade", "entrance",
                                    ENTRANCE_FRAME)),
        "exit": _measure(_still(directory, "fade", "exit", EXIT_FRAME)),
    }


def _beats_the_fade(measured, control, held, axis):
    """Whether `axis` moved further than pure opacity moves it."""
    return (abs(measured[axis] - held[axis])
            > abs(control[axis] - held[axis]))


@remotion_available
def test_the_instrument_sees_colour_separation_when_it_is_there(tmp_path):
    """Validate the chroma ruler before trusting a zero from it.

    A zero from an unvalidated instrument is not evidence - three
    measurements in this repository's recent history were instrument
    failures reported as clean results.
    """
    props = _props("cut", "entrance")
    props["elements"][0]["color"] = "#FF0000"
    props_path = tmp_path / "red.json"
    props_path.write_text(json.dumps(props), encoding="utf-8")
    out = tmp_path / "red.png"
    result = subprocess.run(
        ["npx", "remotion", "still", "MotionGraphics", str(out),
         f"--frame={HOLD_FRAME}", f"--props={props_path}",
         "--image-format=png"],
        cwd=REMOTION_DIR, capture_output=True, text=True,
        encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    assert _measure(out)["chroma"] > 0, (
        "the chroma measurement reports zero on a deliberately red "
        "element, so a zero from it means nothing")


@remotion_available
@pytest.mark.parametrize("direction", ["entrance", "exit"])
@pytest.mark.parametrize(
    "character",
    [c for c in vocabulary.AXES_BY_NAME["entrance"].positions
     if c not in capability.NO_RAMP_CHARACTERS])
def test_every_declared_character_changes_the_frame(
        tmp_path, held, fade_control, character, direction):
    """A declared character that only changes opacity is a fade renamed.

    `fade` itself is excluded by `NO_RAMP_CHARACTERS`: it IS opacity, and
    asking it to do something else would be a gate failing correct
    output.
    """
    frame = ENTRANCE_FRAME if direction == "entrance" else EXIT_FRAME
    measured = _measure(_still(tmp_path, character, direction, frame))
    control = fade_control[direction]

    moved = any(_beats_the_fade(measured, control, held, axis)
                for axis in ("left", "top"))
    resized = any(_beats_the_fade(measured, control, held, axis)
                  for axis in ("width", "height"))
    split = measured["chroma"] > control["chroma"]

    assert moved or resized or split, (
        f"{direction} {character!r} moved the ink no further, resized it no "
        f"more and separated no colour beyond what a plain `fade` does at "
        f"its own mid-ramp. Whatever the source says it does, on a frame it "
        f"is a fade wearing another name - held={held}, fade={control}, "
        f"measured={measured}"
    )


@remotion_available
@pytest.mark.parametrize("direction", ["entrance", "exit"])
def test_typewriter_reveals_in_both_directions(
        tmp_path, held, fade_control, direction):
    """The exit half did not exist, under a comment saying it did."""
    frame = ENTRANCE_FRAME if direction == "entrance" else EXIT_FRAME
    measured = _measure(_still(tmp_path, "typewriter", direction, frame))
    control = fade_control[direction]
    assert measured["width"] < control["width"], (
        f"a typewriter {direction} is a reveal: mid-ramp the ink must be "
        f"NARROWER than a plain fade's at its own mid-ramp, because some "
        f"characters are not on screen at all. Got {measured['width']}px "
        f"against {control['width']}px faded and {held['width']}px held."
    )


@remotion_available
@pytest.mark.parametrize("direction", ["entrance", "exit"])
def test_glitch_separates_the_colour_channels(
        tmp_path, held, fade_control, direction):
    """It carried the split as `textShadow`, which `Runs` overrides."""
    frame = ENTRANCE_FRAME if direction == "entrance" else EXIT_FRAME
    measured = _measure(_still(tmp_path, "glitch", direction, frame))
    assert fade_control[direction]["chroma"] == 0, (
        "a plain fade already separates colour, so this test cannot tell "
        "whether the glitch put it there")
    assert measured["chroma"] > 0, (
        "a glitch ramp put no chromatic separation on the frame. The "
        "aberration must reach the pixels, not just the container's "
        "style - see the `chromaticSplit` comment in MotionGraphics.")
    assert held["chroma"] == 0, (
        "the held frame already has colour separation, so this test "
        "cannot tell whether the glitch put it there")


@remotion_available
def test_slide_and_mask_run_opposite_ways_on_the_way_out(tmp_path, held):
    """An exit that animates the same way as its entrance is a copy-paste.

    `slide` enters from below and leaves upward; `mask` wipes in from one
    side and out to the other.
    """
    slide_in = _measure(_still(tmp_path, "slide", "entrance", ENTRANCE_FRAME))
    slide_out = _measure(_still(tmp_path, "slide", "exit", EXIT_FRAME))
    assert (slide_in["top"] - held["top"]) * (slide_out["top"] - held["top"]) < 0, (
        f"slide moves the same way in and out - in {slide_in['top']}, "
        f"out {slide_out['top']}, held {held['top']}")

    mask_in = _measure(_still(tmp_path, "mask", "entrance", ENTRANCE_FRAME))
    mask_out = _measure(_still(tmp_path, "mask", "exit", EXIT_FRAME))
    assert mask_in["left"] < mask_out["left"], (
        f"mask clips from the same edge in and out - in kept from "
        f"{mask_in['left']}, out from {mask_out['left']}")
