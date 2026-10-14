"""FFrames text fidelity: node opacity ramps and whole-text tracking.

Two renderer defects the S9 census measured (G5/G6), pinned at the pixel
level so a rebuilt-but-stale binary fails loudly:

- G6: a TEXT node carrying its own opacity animations (TimedTextOverlay's
  fade ramps) rendered at full opacity for every frame but the first -
  ``render_node`` replaced the node's group instead of drawing inside
  it, dropping the sampled opacity. Before: frame 1 already full;
  after: the ramp matches HyperFrames frame for frame.
- G5: a whole-text node with ``letter_spacing`` rendered untracked -
  ``draw_text`` never set the attribute, so the display run measured
  tracked (matching Chrome) but drew untracked (48 px narrow on the
  real card, ink IoU 0.56). After: ink IoU 0.91, the residual being
  Skia-vs-Chrome rasterization.

Both tests render a handful of tiny frames through the real binary, so
each costs about a second and states that reason here.
"""

import numpy as np
import pytest
from PIL import Image

from library.graphics_ir.model import (
    Animation,
    Bounds,
    LinearEasing,
    Node,
    NodeType,
    Scene,
    TextContent,
    Timing,
)
from library.graphics_ir.serialization import write_scene
from library.tools import fframes_render


def _fframes_available() -> bool:
    usable, _ = fframes_render.fframes_available()
    return bool(usable)


def _bundled_font():
    from library.tools.hyperframes_render import bundled_font_file

    bundled = bundled_font_file()
    return str(bundled) if bundled.is_file() else None


def _render(scene: Scene, tmp_path, tag: str):
    font = _bundled_font()
    assert font is not None, "bundled Montserrat is missing; cannot stage faces"
    for node in scene.nodes:
        if node.text is not None:
            node.text.font_file = font
    fframes_render.stage_scene_fonts(scene, str(tmp_path / "fonts"))
    scene_path = str(tmp_path / f"{tag}.json")
    write_scene(scene, scene_path)
    frames_dir = tmp_path / f"{tag}_frames"
    frames_dir.mkdir(exist_ok=True)
    return fframes_render.render_png_sequence(scene_path, str(frames_dir), scene.fps)


def _mean_alpha(path) -> float:
    return float(
        np.asarray(Image.open(path).convert("RGBA"), dtype=np.float64)[..., 3].mean()
    )


def _ink_width(path, y0: int, y1: int) -> int:
    alpha = np.asarray(Image.open(path).convert("RGBA"), dtype=np.int32)[y0:y1, :, 3]
    cols = np.nonzero((alpha > 128).sum(0))[0]
    assert len(cols), f"no ink in rows {y0}-{y1} of {path}"
    return int(cols.max() - cols.min() + 1)


def _text_node(
    node_id: str,
    text: str,
    y: float,
    spacing: float,
    animations=None,
    duration_frames: int = 6,
) -> Node:
    return Node(
        id=node_id,
        type=NodeType.TEXT,
        text=TextContent(
            text=text,
            font_family="Montserrat",
            font_file=None,
            font_size=56.0,
            font_weight=900.0,
            color="#FFFFFF",
            text_align="center",
            line_height=1.1,
            letter_spacing=spacing,
            text_transform="uppercase",
        ),
        bounds=Bounds(x=0.0, y=y, width=400.0, height=70.0),
        timing=Timing(start=0.0, duration=duration_frames / 30.0),
        animations=list(animations or []),
    )


@pytest.mark.skipif(not _fframes_available(), reason="FFrames binary not available")
def test_text_node_opacity_ramp_renders(tmp_path):
    """A text node's own fade-in/fade-out ramps shape its pixels (G6)."""
    fps = 30.0
    # Production-shaped ramps: fade-in over frames 0-3, full at frame 4,
    # fade-out over frames 4-7. Kept non-overlapping the way the
    # lowering clamps them, so each frame samples exactly one ramp.
    animations = [
        Animation(
            target="moment",
            property="opacity",
            from_value=0.0,
            to_value=1.0,
            start=0.0,
            duration=4 / fps,
            easing=LinearEasing(),
        ),
        Animation(
            target="moment",
            property="opacity",
            from_value=1.0,
            to_value=0.0,
            start=4 / fps,
            duration=4 / fps,
            easing=LinearEasing(),
        ),
    ]
    scene = Scene(
        version=1,
        fps=fps,
        width=400,
        height=200,
        duration=8 / fps,
        nodes=[
            _text_node("moment", "GOOGLE", 60.0, 0.0, animations, duration_frames=8)
        ],
    )
    frames = _render(scene, tmp_path, "ramp")
    assert len(frames) == 8
    curve = [_mean_alpha(f) for f in frames]
    top = max(curve)
    assert top > 0, "ramp card drew nothing at all"
    norm = [v / top for v in curve]
    # Frame 0 shut, mid-ramps half, handover full, then symmetric fall.
    assert norm[0] < 0.05, f"frame 0 should be shut, got {norm[0]:.3f}"
    assert abs(norm[2] - 0.5) < 0.15, (
        f"frame 2 should be mid-fade-in, got {norm[2]:.3f}"
    )
    assert norm[4] > 0.95, f"frame 4 should be full, got {norm[4]:.3f}"
    assert abs(norm[6] - 0.5) < 0.15, (
        f"frame 6 should be mid-fade-out, got {norm[6]:.3f}"
    )


@pytest.mark.skipif(not _fframes_available(), reason="FFrames binary not available")
def test_whole_text_letter_spacing_renders(tmp_path):
    """A whole-text node's tracking widens its ink (G5)."""
    scene = Scene(
        version=1,
        fps=30.0,
        width=400,
        height=200,
        duration=1 / 30.0,
        nodes=[
            _text_node("plain", "GOOGLE", 10.0, 0.0),
            _text_node("tracked", "GOOGLE", 110.0, 3.0),
        ],
    )
    frames = _render(scene, tmp_path, "tracking")
    plain = _ink_width(frames[0], 10, 80)
    tracked = _ink_width(frames[0], 110, 180)
    # usvg draws the spacing on every cluster but the last: 3 px across
    # the 5 gaps of GOOGLE. ±4 px covers antialiasing; a renderer that
    # drops the attribute measures ~0 here (the shipped defect).
    assert abs((tracked - plain) - 3.0 * (len("GOOGLE") - 1)) <= 4.0, (
        f"tracking did not draw: plain={plain} tracked={tracked}"
    )
