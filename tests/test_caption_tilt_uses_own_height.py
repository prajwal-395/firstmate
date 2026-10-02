"""Each graphic's Tilt is derived from ITS OWN height, never a fixed one.

See `docs/evidence/caption_tilt.md` for the incident and invariant.
"""

import os
import sys
import textwrap

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import subtitle_style, tight_box  # noqa: E402
from library.tools.resolve_transform import drawn_centre  # noqa: E402
from library.tools.tight_box import placement_for_box  # noqa: E402

FRAME_W, FRAME_H = 1080, 1920

#: The declared row these pin against - the captain's 2026-09-17 Reel 01
#: ruling. Any row would do; this one ties the pin to the live series.
DECLARED_ROW = 0.8451

#: Graphic heights that genuinely differ: the structural 480 canvas and
#: two impostors either side of it, a 1.6x spread and more. If these
#: ever collapse to one size the pin is void, and the first test says so.
OTHER_HEIGHTS = (300, 640)


def _project(tmp_path, row):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "project.yaml").write_text(
        "name: test\ndelivery_format: vertical_1080x1920\npipeline:\n"
        + textwrap.indent(
            f"subtitle_position:\n  caption_row: {row}\n"
            "  reason: pinning the per-graphic derivation\n",
            "  "),
        encoding="utf-8")
    return str(tmp_path)


def _declared_centre(project_folder):
    """The screen centre the declared row yields, via the real path.

    The structural caption box the engine really builds, and where its
    own placement really puts it - no restatement of the anchor rule.
    """
    style = subtitle_style.resolve_subtitle_style(
        project_folder=project_folder)
    box = tight_box.constant_caption_box({
        "width": FRAME_W, "height": FRAME_H, "style": style,
        "subtitles": [{"text": "a caption"}],
    })
    wrap = style["captionMaxWidth"]
    assert (box.width, box.height) == (
        tight_box._ceil_even(wrap + 2 * tight_box.PAD_X
                             + tight_box.TRAILING_MARGIN_PX), 480), (
        "the structural canvas moved - recheck what this pin assumes")
    assert box.placement["pan"] == pytest.approx(0.0), (
        "captions are centred; a pan here is a layout change, not sizing")
    return box, drawn_centre(box.width, box.height, FRAME_W, FRAME_H,
                             box.placement["pan"], box.placement["tilt"])


def test_three_heights_on_one_row_share_one_screen_centre(tmp_path):
    """The captain's point, in one assertion per height.

    The centre the declared row yields for the structural canvas, then
    the same centre reached for two different canvas heights through
    `placement_for_box` - the function every builder calls. All three
    land on the identical screen centre.
    """
    folder = _project(tmp_path, DECLARED_ROW)
    box, (cx, cy) = _declared_centre(folder)
    assert cx == pytest.approx(FRAME_W / 2.0)

    for height in OTHER_HEIGHTS:
        assert height != box.height, (
            "the impostor heights must genuinely differ for this to "
            "discriminate")
        placement = placement_for_box(box.width, height, cx, cy,
                                      FRAME_W, FRAME_H)
        got_cx, got_cy = drawn_centre(box.width, height, FRAME_W,
                                      FRAME_H, placement["pan"],
                                      placement["tilt"])
        assert (got_cx, got_cy) == pytest.approx((cx, cy))


