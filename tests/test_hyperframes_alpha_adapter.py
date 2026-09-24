"""The HyperFrames alpha adapter: straight in, premultiplied (or flat) out.

The defect this names: HyperFrames PNG frames carry STRAIGHT alpha
with full-strength RGB, while the overlay carriage - and Resolve's
premultiplied read of the `qtrle` file encoded from these frames -
assumes no channel exceeds its own alpha. Encoding straight frames
unadapted composites halos and lifted blacks wherever the overlay is
semi-transparent, and the file still probes as valid RGBA: nothing
downstream would refuse it. So the adapter is asserted on pixels, not
on calls: a straight pixel premultiplies exactly, a transparent one
goes black-transparent, and a card flattens over its own declared
ground rather than over black.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import hyperframes_render as hf  # noqa: E402


def _write(tmp_path, name, pixels) -> str:
    path = str(tmp_path / name)
    Image.fromarray(np.array(pixels, dtype=np.uint8),
                    mode="RGBA").save(path)
    return path


def _read(path):
    return np.asarray(Image.open(path).convert("RGBA"))


def test_premultiply_straight_to_premultiplied(tmp_path):
    # Straight: full-strength RGB beside partial alpha. Premultiplied:
    # each channel scaled by its own alpha, and a fully transparent
    # pixel is black-transparent rather than colour-transparent.
    path = _write(tmp_path, "frame_000001.png", [
        [(255, 255, 255, 128), (200, 100, 50, 0)],
        [(255, 255, 255, 255), (0, 0, 0, 0)],
    ])
    assert hf.premultiply_frames([path]) == 1
    out = _read(path)
    assert tuple(out[0, 0]) == (128, 128, 128, 128)
    assert tuple(out[0, 1]) == (0, 0, 0, 0)
    assert tuple(out[1, 0]) == (255, 255, 255, 255)
    assert tuple(out[1, 1]) == (0, 0, 0, 0)
    # The carriage invariant: no channel exceeds its own alpha.
    assert bool((out[..., :3].astype(int) <= out[..., 3:4].astype(int)).all())


def test_flatten_composites_over_the_declared_ground(tmp_path):
    path = _write(tmp_path, "frame_000001.png", [
        [(0, 0, 0, 0), (255, 255, 255, 255)],
        [(255, 255, 255, 128), (0, 0, 0, 0)],
    ])
    assert hf.flatten_frames([path], "#101014") == 1
    out = _read(path)
    # Transparent paints the ground; opaque ink survives; half alpha
    # blends between them - (255+16)/2 rounded is 136, +green 136...
    assert tuple(out[0, 0]) == (16, 16, 20, 255)
    assert tuple(out[0, 1]) == (255, 255, 255, 255)
    assert tuple(out[1, 0]) == (136, 136, 138, 255)
    assert tuple(out[1, 1]) == (16, 16, 20, 255)


def test_flatten_refuses_a_ground_it_cannot_parse(tmp_path):
    path = _write(tmp_path, "frame_000001.png", [[(0, 0, 0, 0)]])
    with pytest.raises(hf.HyperFramesRenderError):
        hf.flatten_frames([path], "")
    with pytest.raises(hf.HyperFramesRenderError):
        hf.flatten_frames([path], "dark grey")


def test_fps_argument_recovers_exact_ratios():
    assert hf.fps_argument(23.976023976023978) == "24000/1001"
    assert hf.fps_argument(30.0) == "30"
    assert hf.fps_argument(25) == "25"


def test_template_registry_names_what_has_no_form():
    assert hf.hyperframes_template("SubtitleOverlay") == "SubtitleOverlay"
    assert hf.hyperframes_template("TimedTextOverlay") == "TimedTextOverlay"
    assert hf.hyperframes_template("FullFrameCard") == "FullFrameCard"
    assert hf.hyperframes_template("MotionGraphics") is None
