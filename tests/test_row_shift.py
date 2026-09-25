"""One pixel move is a DIFFERENT Tilt on a picture clip than on an overlay.

2026-09-25: moving the picture, its frame and the captions of 30 reels by
pixel counts. A Tilt unit moves a clip by a fraction of its OWN drawn
size, so treating a unit as a pixel - or the picture's law as the
overlay's - would move each row by a different wrong amount.
"""

import pytest

from library.tools import row_shift
from library.tools.resolve_transform import fit_base_scale, shift_px


def _tracks():
    return [
        {"type": "video", "index": 1, "name": "Craig", "clips": [
            {"name": "A.MXF", "source_file": "pic",
             "transform": {"Tilt": -0.79}}]},
        {"type": "video", "index": 4, "name": "Subtitles", "clips": [
            {"name": "sub.mov", "source_file": "cap",
             "transform": {"Tilt": -1836.0}}]},
        {"type": "video", "index": 6, "name": "Motion Graphics", "clips": [
            {"name": "logo_bulb.mov", "source_file": "card",
             "transform": {"Tilt": 0.0}}]},
    ]


SIZES = {"pic": (3840, 2160), "cap": (904, 480), "card": (1080, 1920)}


def test_each_row_moves_the_stated_pixels_on_its_own_law():
    spec = row_shift.shift_spec(
        _tracks(), 1, {"Craig": 220, "Subtitles": -26,
                       "Motion Graphics": 220},
        frame=(1080, 1920), draw_gain=1.0, skip_prefixes=("logo_",),
        size_of=SIZES.__getitem__)
    by_row = {e["row"]: e["properties"]["Tilt"] for e in spec["edits"]}
    assert "V6" not in by_row  # the end card is skipped
    fit = fit_base_scale(3840, 2160, 1080, 1920)
    picture_px = shift_px(-0.79 - by_row["V1"], 2160, 1920, fit, 1.0)
    caption_px = shift_px(by_row["V4"] + 1836.0, 480, 1920, 1.0, 1.0)
    assert round(picture_px) == 220   # down: Tilt decreased
    assert round(caption_px) == 26     # up: Tilt increased


def test_a_row_the_reel_does_not_carry_is_refused():
    with pytest.raises(row_shift.RowShiftError):
        row_shift.shift_spec(_tracks(), 1, {"Frame": 220}, frame=(1080, 1920),
                             draw_gain=1.0, size_of=SIZES.__getitem__)
