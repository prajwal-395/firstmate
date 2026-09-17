"""Each graphic's Tilt is derived from ITS OWN height, never a fixed one.

The captain, 2026-09-17, on Reel 01's hand-set captions: "pan and tilt
are relative to graphic frame size and video frame size". His durable
point: a caption row must be declared as a PLACE in pixels and each
graphic's Tilt derived from THAT graphic's own height, because one
stored value places two differently sized graphics in two different
places (shift_px = value * clip_dim / frame_dim -
`library/tools/resolve_transform.py`, measured on 16 rendered plates).

Every caption graphic in the project today is 904x480, so a fixed 480
in the derivation would look correct everywhere and be wrong the moment
one graphic differs - which is what varying tight canvases will cause.
Audited 2026-09-17: every placement call site already passes its own
canvas dims - `tight_box.placement_for_box` (all four caption paths),
`mg_tight_box`, and `overlay_intent.transform_for` (computed at
placement time against the canvas going down) - and both builders place
each segment's own `tight_box.placement` verbatim. No engine change was
made; this file pins the property so the coming varying-height work
cannot regress it silently.

A test that only exercises one size proves nothing about his point, so
this one places THREE different heights on the same declared row and
asserts all three land on the same screen centre. A fixed-480
derivation would miss by 100+px on the other two (shown in the second
test), which is what makes the first one mean something.

Synthetic under ``tmp_path``; nothing reaches Resolve or a real project.
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
    assert (box.width, box.height) == (904, 480), (
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


def test_the_tilts_differ_per_height_as_the_law_says(tmp_path):
    """The same screen place needs different stored Tilts per height -
    computed here from the measured law, not from the code under test.

    `shift_px = value * clip_dim / frame_dim`, so holding the centre
    fixed while the canvas height moves must move the Tilt as
    ``(FH/2 - cy) * FH / h``. A derivation that stored one value for
    every size would fail the first test by 100+px; these numbers say
    by exactly how much.
    """
    folder = _project(tmp_path, DECLARED_ROW)
    box, (cx, cy) = _declared_centre(folder)

    tilts = {}
    for height in (box.height,) + OTHER_HEIGHTS:
        placement = placement_for_box(box.width, height, cx, cy,
                                      FRAME_W, FRAME_H)
        tilts[height] = placement["tilt"]
        assert placement["tilt"] == pytest.approx(
            (FRAME_H / 2.0 - cy) * (FRAME_H / float(height)))

    # No one-size value: the 300-tall graphic needs nearly twice the
    # Tilt units of the 640-tall one for the identical screen centre.
    assert len(set(round(v, 6) for v in tilts.values())) == 3
    # And the fixed-480 spelling would be exactly the 480 entry -
    # which is what the other two are not.
    assert tilts[480] == pytest.approx(
        (FRAME_H / 2.0 - cy) * (FRAME_H / 480.0))
    assert abs(tilts[300] - tilts[480]) > 100
    assert abs(tilts[640] - tilts[480]) > 100
