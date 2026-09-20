"""The bullet-uniformity pixel measurement, on synthetic frames.

`test_list_build_uniform_size` proves the behaviour end to end through
a real Remotion render; these pin the measurement itself, with no
renderer, no ffmpeg and no project: two bands of ink at different
glyph heights must read as two different sizes, two bands at one
height as uniform, and a band holding nothing glyph-like must refuse
rather than report a number.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.mg_bullet_measure import body_heights, siblings_uniform


def _bands_png(path, bands):
    """White rectangles on black: `bands` is [(top, height, count)]."""
    from PIL import Image

    image = Image.new("RGB", (400, 200), (0, 0, 0))
    pixels = image.load()
    for top, height, count in bands:
        for index in range(count):
            left = 20 + index * 30
            for y in range(top, top + height):
                for x in range(left, left + 10):
                    pixels[x, y] = (255, 255, 255)
    image.save(path)
    return path


def test_uneven_bands_read_as_two_sizes(tmp_path):
    png = _bands_png(str(tmp_path / "ladder.png"), [(10, 33, 4),
                                                   (70, 21, 4)])
    bodies = body_heights(png)
    assert bodies == [33, 21], f"expected the 33/21 ladder, got {bodies}"
    assert not siblings_uniform(bodies)


def test_uniform_bands_read_as_one_size(tmp_path):
    png = _bands_png(str(tmp_path / "uniform.png"), [(10, 33, 4),
                                                    (70, 33, 4)])
    bodies = body_heights(png)
    assert bodies == [33, 33], f"expected uniform 33s, got {bodies}"
    assert siblings_uniform(bodies)


def test_band_with_no_glyphs_refuses(tmp_path):
    from PIL import Image

    png = str(tmp_path / "specks.png")
    image = Image.new("RGB", (400, 200), (0, 0, 0))
    pixels = image.load()
    for y in range(10, 16):
        for x in range(20, 22):
            pixels[x, y] = (255, 255, 255)
    image.save(png)
    with pytest.raises(AssertionError, match="no glyphs in band"):
        body_heights(png)


def test_empty_frame_is_not_uniform(tmp_path):
    from PIL import Image

    png = str(tmp_path / "blank.png")
    Image.new("RGB", (400, 200), (0, 0, 0)).save(png)
    assert body_heights(png) == []
    assert not siblings_uniform([])
