"""StagedScene: a whole animated picture, and no look inside the renderer.

Two halves, and both can fail.

The SOURCE half is free and runs everywhere: it pins the registration in
`Root.tsx` and asserts the composition states no ground colour, no
texture, no vignette, no palette and no camera move of its own - the
captain's rule of 2026-09-08, "i want no hardcoded values.  there are no
house glow looks, there are no settled house grain or anything."  This
is the half that would catch the reference's measurements
(`docs/ANIMATION_FIRST_REFERENCE.md`) being quietly baked in as defaults,
which is the specific failure this composition is exposed to.

The DELIVERY half renders ONE short PNG sequence through the same `npx
remotion render` the pipeline uses and measures the frames.  It proves
the five behaviours that separate a staged picture from an overlay, each
with a known answer written down before anything is measured:

  1. the camera MOVES the world (a declared zoom track makes a layer
     cover more of the frame);
  2. the vignette does NOT move with it (the corner is unchanged across
     the same zoom, because it belongs to the lens);
  3. a run's reveal COMPLETES on its declared second rather than
     starting there (absent before, full black on it);
  4. `holdFrames` puts the motion on twos (frame pairs identical, and
     the pair boundary not);
  5. a light both draws and CLIPS (a run inside a cone is present while
     the cone covers it and gone once it has rotated away).

Nothing here reads a real project and nothing here ships artwork: the
scene is declared inline from primitives the composition draws itself
(disc, rule, text, light), so there is no image file to stage.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")
COMPOSITION = os.path.join(
    REMOTION_DIR, "src", "compositions", "StagedScene", "index.tsx")
ROOT_TSX = os.path.join(REMOTION_DIR, "src", "Root.tsx")


# ── the source half: free, and it runs anywhere ──────────────────────

def test_root_tsx_registers_staged_scene():
    with open(ROOT_TSX, encoding="utf-8") as f:
        src = f.read()
    assert 'id="StagedScene"' in src
    assert "component={StagedScene}" in src


def test_studio_default_draws_nothing_and_chooses_nothing():
    """The studio default is a black ground with no layers.

    Same reasoning as `FullFrameCard`'s and `MotionGraphics`': a default
    carrying a surface colour, a texture and a camera move would be a
    look nobody chose sitting in the repository.  Black is the absence
    of a ground rather than a choice of one (AGENTS.md 10.5).
    """
    with open(ROOT_TSX, encoding="utf-8") as f:
        src = f.read()
    block = src.split('id="StagedScene"', 1)[1].split("<Composition", 1)[0]
    assert 'ground: { colour: "#000000" }' in block
    assert "layers: []" in block
    for forbidden in ("texture:", "vignette:", "camera:", "holdFrames:"):
        assert forbidden not in block, (
            f"the StagedScene studio default states {forbidden!r}; a "
            f"default look is exactly what this composition must not carry")


def _code_lines() -> list:
    """The composition's source with comments and docstrings removed."""
    with open(COMPOSITION, encoding="utf-8") as f:
        src = f.read()
    out, i = [], 0
    while True:
        start = src.find("/*", i)
        if start < 0:
            out.append(src[i:])
            break
        out.append(src[i:start])
        end = src.find("*/", start)
        i = len(src) if end < 0 else end + 2
    body = "".join(out)
    return [ln for ln in body.splitlines()
            if ln.strip() and not ln.strip().startswith("//")]


def test_the_composition_states_no_look():
    """No colour, no size, no camera move lives in the renderer.

    The one hex value the file may carry is none at all; the one rgba
    it may carry is the vignette's black, which is a darkening rather
    than a colour - the reading AGENTS.md 10.5 gives `NEUTRAL_CDL`.
    """
    code = "\n".join(_code_lines())
    import re
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", code)
    assert hexes == [], f"the renderer states colours: {hexes}"
    rgbas = re.findall(r"rgba?\([^)]*\)", code)
    assert all("0,0,0" in r for r in rgbas), (
        f"the renderer states a colour that is not the vignette's black: {rgbas}")
    for word in ("Montserrat", "Helvetica", "blur(8px)", "px solid"):
        assert word not in code, f"the renderer states {word!r}"


def test_the_reference_magnitudes_are_not_in_the_renderer():
    """`docs/ANIMATION_FIRST_REFERENCE.md` measured one piece.

    Its numbers - the 100 ms reveal lead, the 15 fps step, the 0.47
    vignette ratio - are recorded so a declaration can be argued
    against a measurement, not so anything can default to them.  A
    renderer that hardcoded any of them would read as a house look.
    """
    code = "\n".join(_code_lines())
    assert "holdFrames = 2" not in code and "holdFrames ?? 2" not in code
    assert "revealRamp ?? 0.1" not in code
    # The only fallbacks the reveal may take are "no ramp" and "no reveal".
    assert "revealRamp ?? 0" in code


def test_value_at_and_hold_are_exported_for_reading():
    """The scene's behaviour over time is readable without a render."""
    code = "\n".join(_code_lines())
    for name in ("export const valueAt", "export const heldFrame",
                 "export const runOpacity", "export const conePolygon"):
        assert name in code, f"{name} is not exported"


# ── the delivery half: one render, five measurements ─────────────────

pytestmark_delivery = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None,
    reason=(
        "needs remotion-subtitles/node_modules and npx. Runs anywhere the "
        "Remotion deps are installed - the same environment every other "
        "*_delivery test in this suite needs, and the one AGENTS.md 9 "
        "requires for any real run."
    ),
)

W, H, FPS, FRAMES = 216, 384, 30, 37


def _scene() -> dict:
    """A scene whose answers are known before anything is measured.

    Pure primary colours, because every assertion below is a colour
    count or a channel purity and nothing may be confused for anything
    else.  These are TEST FIXTURE values in a test file, not a palette:
    the renderer states none of them (see `test_the_composition_states_no_look`).
    """
    return {
        "ground": {
            "colour": "#000000",
            # Gentle, and deliberately so: this fixture measures colour
            # purity, and a vignette that crushed the middle of the frame
            # would make every assertion below a test of the vignette.
            "vignette": {"strength": 0.9, "radiusX": 1.2, "radiusY": 1.2},
        },
        # Zoom doubles over the second, so anything in the world covers
        # four times the area at t=1.0 that it did at t=0.0.
        "camera": {"zoom": [{"at": 0.0, "value": 1.0},
                            {"at": 1.0, "value": 2.0, "ease": "linear"}]},
        "layers": [
            {"kind": "disc", "id": "d", "size": 0.2, "colour": "#0000ff"},
            {"kind": "text", "id": "reveal",
             "runs": [{"text": "NOW", "size": 0.08, "weight": 900,
                       "colour": "#ff0000", "revealAt": 1.0}],
             "revealRamp": 0.2,
             "placement": {"x": 0.0, "y": -0.16, "width": 0.5}},
            # A cone that starts covering the run below it and rotates
            # away from it. 180 degrees points down the frame.
            {"kind": "light", "id": "beam", "x": 0.0, "y": 0.0,
             "angle": [{"at": 0.0, "value": 180.0},
                       {"at": 1.2, "value": 60.0, "ease": "linear"}],
             "length": 1.0, "spreadDeg": 30.0, "colour": "#ffffff",
             "opacity": 0.0},
            {"kind": "text", "id": "inbeam", "inLight": "beam",
             "runs": [{"text": "LIT", "size": 0.08, "weight": 900,
                       "colour": "#00ff00"}],
             "placement": {"x": 0.0, "y": 0.16, "width": 0.5}},
        ],
        "fontFamily": "Montserrat",
        "holdFrames": 2,
        "fps": FPS,
        "width": W,
        "height": H,
        "durationInFrames": FRAMES,
    }


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = tmp_path_factory.mktemp("staged_scene")
    props = out / "props.json"
    props.write_text(json.dumps(_scene()), encoding="utf-8")
    seq = out / "seq"
    result = subprocess.run(
        ["npx", "remotion", "render", "StagedScene", str(seq),
         "--props", str(props), "--sequence", "--image-format", "png",
         "--log", "error"],
        cwd=REMOTION_DIR, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=900, check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    frames = sorted(str(seq / n) for n in os.listdir(seq) if n.endswith(".png"))
    assert len(frames) == FRAMES, f"rendered {len(frames)} frames, wanted {FRAMES}"
    return frames


def _px(path):
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(path).convert("RGB")).astype(int)


def _count(a, r=None, g=None, b=None, tol=40):
    import numpy as np
    m = np.ones(a.shape[:2], dtype=bool)
    for ch, want in enumerate((r, g, b)):
        if want is None:
            continue
        m &= np.abs(a[:, :, ch] - want) <= tol
    return int(m.sum())


@pytestmark_delivery
def test_the_camera_moves_the_world(rendered):
    """A declared zoom track makes a world layer cover more of the frame.

    Zoom 1.0 -> 2.0 over the second, so the disc's area roughly
    quadruples.  Answer known before measuring: strictly more blue.
    """
    a0, a1 = _px(rendered[0]), _px(rendered[30])
    n0, n1 = _count(a0, b=255, r=0, g=0), _count(a1, b=255, r=0, g=0)
    assert n0 > 0, "the disc never drew at all"
    assert n1 > n0 * 2.5, (
        f"the camera did not move the world: disc covered {n0} px at t=0 "
        f"and {n1} px at t=1.0, and a 2x zoom should be nearer 4x")


@pytestmark_delivery
def test_the_vignette_belongs_to_the_lens_not_the_world(rendered):
    """The corner is unchanged across the same zoom.

    A vignette drawn inside the camera would scale with it and the
    corner would lighten.  Answer known before measuring: identical.
    """
    a0, a1 = _px(rendered[0]), _px(rendered[30])
    for y, x in ((2, 2), (2, W - 3), (H - 3, 2), (H - 3, W - 3)):
        assert abs(int(a0[y, x].sum()) - int(a1[y, x].sum())) <= 3, (
            f"corner ({x},{y}) moved with the world: {a0[y, x]} -> {a1[y, x]}")


@pytestmark_delivery
def test_a_reveal_completes_on_its_second_rather_than_starting_there(rendered):
    """`revealAt` 1.0 with a 0.2 s ramp: nothing at 0.79, full at 1.0.

    This is the rule measured in §1 of the read - the reference's type
    reaches full opacity ON the syllable - and the opposite of showing
    a cue once its start has passed.
    """
    before = _px(rendered[22])   # t = 0.733 (held to 0.733), ramp not begun
    mid = _px(rendered[28])      # t = 0.933, inside the ramp
    at = _px(rendered[30])       # t = 1.000, the declared moment
    assert _count(before, r=255, g=0, b=0, tol=60) == 0, (
        "the run drew before its reveal ramp began")
    assert _count(mid, r=255, g=0, b=0, tol=60) == 0, (
        "a mid-ramp run reached full red; the ramp is not ramping")
    assert _count(mid, r=120, g=0, b=0, tol=90) > 0, (
        "the run was absent mid-ramp; the ramp starts too late")
    assert _count(at, r=255, g=0, b=0, tol=40) > 0, (
        "the run was not fully drawn on its declared second")


@pytestmark_delivery
def test_hold_frames_puts_the_motion_on_twos(rendered):
    """`holdFrames` 2: pairs identical, and the boundary between them not."""
    import numpy as np
    a2, a3, a4 = (_px(rendered[i]) for i in (2, 3, 4))
    assert np.array_equal(a2, a3), "frames 2 and 3 differ under holdFrames 2"
    assert not np.array_equal(a3, a4), (
        "frames 3 and 4 are identical, so nothing is moving at all and the "
        "hold above proves nothing")


@pytestmark_delivery
def test_a_texture_that_cannot_load_does_not_stall_the_render(tmp_path):
    """A ground texture is held by `delayRender` until it decodes.

    That is the fix for Remotion's own `no-background-image` rule - a
    lazily-fetched tile can be captured before it paints - but a handle
    that is only cleared on success turns a missing tile into the render
    hanging for its whole timeout.  Answer known before measuring: a
    texture naming a file that is not there still renders, promptly, and
    still draws the ground colour underneath it.

    `remotion still` rather than the module fixture, because this needs
    different props and one frame answers it.
    """
    scene = _scene()
    scene["ground"]["texture"] = "brand/a-tile-that-is-not-there.png"
    scene["ground"]["textureOpacity"] = 0.5
    props = tmp_path / "props.json"
    props.write_text(json.dumps(scene), encoding="utf-8")
    out = tmp_path / "still.png"
    result = subprocess.run(
        ["npx", "remotion", "still", "StagedScene", str(out),
         "--props", str(props), "--frame", "0", "--timeout", "20000",
         "--log", "error"],
        cwd=REMOTION_DIR, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300, check=False,
    )
    assert result.returncode == 0, result.stderr[-3000:]
    a = _px(str(out))
    assert _count(a, b=255, r=0, g=0) > 0, (
        "the ground drew nothing at all with an absent texture")


@pytestmark_delivery
def test_a_light_both_draws_and_clips(rendered):
    """A run inside a cone is present under it and gone once it sweeps away.

    The cone starts at 180 degrees (down the frame, over the run) and
    rotates to 60.  Answer known before measuring: green early, none
    late.  The run itself is never faded - only the light leaves it,
    which is what the reference does to "space" (§7 of the read).
    """
    early = _px(rendered[0])
    late = _px(rendered[36])
    assert _count(early, r=0, g=255, b=0, tol=60) > 0, (
        "the run inside the cone never drew, so the clip proves nothing")
    assert _count(late, r=0, g=255, b=0, tol=60) == 0, (
        "the run survived the cone rotating off it: the light is drawing "
        "but not clipping")
