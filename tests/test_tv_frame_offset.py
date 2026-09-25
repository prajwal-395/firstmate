"""The frame and its window move together, or the picture shows black.

2026-09-25: the captain moved the picture down to make room for the post
header inside the platforms' safe zone (`tv_frame.offset_y`). The picture
is aimed into `screen_window_rect` and the bezel is placed by
`frame_properties`; if one moved without the other the picture would be
aimed at a window the bezel no longer frames.
"""

from PIL import Image

from library.tools import reel_look, tv_frame
from library.tools.resolve_transform import shift_px


def _asset(tmp_path):
    im = Image.new("RGBA", (3840, 2160), (0, 0, 0, 255))
    im.paste((0, 0, 0, 0), (200, 150, 3640, 2010))  # the screen window
    path = tmp_path / "tv.png"
    im.save(path)
    return str(path)


def _look(asset, offset):
    return {"asset": asset, "punch_in": 2.3, "power": None,
            "rotate": "auto", "offset_y": offset, "origin": "test"}


def test_window_and_bezel_move_down_by_the_same_pixels(tmp_path):
    asset = _asset(tmp_path)
    base = tv_frame.screen_window_rect(_look(asset, 0), 1080, 1920)
    moved = tv_frame.screen_window_rect(_look(asset, 220), 1080, 1920)
    assert moved[1] - base[1] == 220 and moved[3] - base[3] == 220
    assert moved[0] == base[0] and moved[2] == base[2]

    props = reel_look.frame_properties(_look(asset, 220), 1080, 1920,
                                       draw_gain=1.0)
    # Down is a negative Tilt; its drawn shift is the window's shift.
    assert props["Tilt"] < 0
    assert round(shift_px(-props["Tilt"], 1920, 1920, draw_gain=1.0)) == 220
    assert "Tilt" not in reel_look.frame_properties(_look(asset, 0),
                                                    1080, 1920)
