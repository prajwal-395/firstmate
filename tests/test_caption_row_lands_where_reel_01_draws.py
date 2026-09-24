"""The caption row the captain chose, and the arithmetic that derives it.

The captain, 2026-09-17, on Reel 01, hand-moving all 34 captions to
Tilt -917: "i like that". The number is evidence of the PLACE, not its
specification - stored transforms were halved when he set it - so the
place was SETTLED BY MEASUREMENT: one gallery still of Reel 01 frame
20, caption white-fill rows 1462..1581 in the still against 283..403
in the file (correlation 0.9977, canvas origin 1178.5, centre 1418.5).
Row 1623, re-derived from his hand value rather than picked.

That SUPERSEDES the same-day 0.7217 (Reel 26's band, Tilt -887) and
the 2026-09-15 0.83125 (Reel 13's band) - two rulings on file and the
older ones must not be picked, which is the exact failure this file
guards alongside the arithmetic.

The derivation, in full
-----------------------
Every number below is on the field test's 1080x1920 delivery frame with
the captain's 840px wrap, whose structural caption canvas is 904x480
(``tight_box.constant_caption_box``).

1.  ``subtitle_style.caption_row_px`` turns the declared fraction into a
    pixel row, and ``_lifted_props`` turns that into the bottom inset
    the render positions against::

        row_px       = round(row * 1920)
        safe.bottom  = 1920 - row_px

2.  ``tight_box.constant_caption_box`` hangs the canvas from that inset,
    one ``PAD_BOTTOM`` past the card edge::

        canvas_top    = 1920 - safe.bottom + PAD_BOTTOM - 480
                      = row_px + 36 - 480
        canvas_bottom = row_px + 36

    so the canvas moves 1:1 with the row, in delivery pixels.

3.  ``resolve_transform`` stores that as Tilt, one unit being
    ``canvas_h / frame_h`` times the measured draw gain - 0.25 × 2.0
    = 0.5 delivery pixels under today's renderer (see
    ``resolve_transform`` for the 2026-09-17 rendered-pixel
    calibration, and ``tests/test_draw_gain_measured.py`` for the
    derivation from measurements rather than restatement)::

        tilt = -(canvas_centre_y - 960) / 0.5

What that gives for the current row, computed below rather than quoted:

    ============  ======  ===========  ======  ==================
    row           row_px  safe.bottom  Tilt    canvas frame rows
    ============  ======  ===========  ======  ==================
    0.8451        1623    297          -918    1179 .. 1659
    ============  ======  ===========  ======  ==================

-918 is two stored units - half a delivery pixel of rounding - from
his hand -917, which draws canvas centre y 1418.5. HIS HAND VALUE IS
THE GROUND TRUTH; THE FRACTION IS OUR RECONSTRUCTION OF IT.

Where 0.8451 comes from
-----------------------
Canvas centre cy 1418.5, bottom 1658.5; the row such a canvas hangs
from is its bottom less ``tight_box.PAD_BOTTOM``::

    1658.5 - 36 = 1622.5      1622.5 / 1920 = 0.845052... -> 0.8451
    caption_row_px(0.8451, 1920) = 1623, the pixel row the build uses.

What this file does NOT prove
-----------------------------
That the renderer still draws gain 2.0. The gain is renderer state -
it read 1.0 on 2026-09-11 - and a stored value that reads back
correctly proves nothing about it. The check that belongs to the
rebuild is the gate still: Reel 01 frame 20 must render its caption
centre at y 1418.5.

Synthetic under ``tmp_path``; nothing reaches Resolve or a real project.
"""

import os
import sys
import textwrap

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import subtitle_style, tight_box
from library.tools.reel_caption_row import declared_row
from library.tools.resolve_transform import drawn_origin

FRAME_W, FRAME_H = 1080, 1920

#: The row the captain chose on 2026-09-17, re-derived from his
#: hand-moved Tilt -917 by still measurement: canvas centre y 1418.5,
#: bottom 1658.5, less `PAD_BOTTOM`.
CAPTAIN_ROW = 0.8451

#: His hand value and the place it renders - the ground truth the
#: fraction reconstructs. The conversion stores -918, two stored units
#: (half a delivery pixel of row rounding) from his -917.
HAND_TILT = -917.0
HAND_CENTRE_Y = 1418.5

#: Where the current declaration puts the 904x480 caption canvas.
CANVAS_ROWS = (1179.0, 1659.0)

#: The row this supersedes (2026-09-15, Reel 13's band), kept for the
#: tests that prove a later pin and the lower-third floor still move
#: with the declaration.
SUPERSEDED_ROW = 0.83125


def _project(tmp_path, row=None):
    """A project folder declaring `row`, or declaring no row at all."""
    if row is None:
        block = "subtitle_typography:\n  size: 58\n"
    else:
        block = (f"subtitle_position:\n"
                 f"  caption_row: {row}\n"
                 f"  reason: the captain's own, 2026-09-17, off Reel 01\n")
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "project.yaml").write_text(
        "name: test\ndelivery_format: vertical_1080x1920\npipeline:\n"
        + textwrap.indent(block, "  "), encoding="utf-8")
    return str(tmp_path)


def _caption_canvas(project_folder, reel_name=None):
    """The tight caption canvas one project's declaration produces.

    Returns `(tilt, (top_row, bottom_row))` - the value Resolve would
    store and where that value actually puts the canvas on the frame,
    both through the engine's own modules rather than restated here.
    """
    style = subtitle_style.resolve_subtitle_style(
        project_folder=project_folder, reel_name=reel_name)
    box = tight_box.constant_caption_box({
        "width": FRAME_W, "height": FRAME_H, "style": style,
        "subtitles": [{"text": "a caption"}],
    })
    _, origin_y = drawn_origin(box.width, box.height, FRAME_W, FRAME_H,
                               box.placement["pan"],
                               box.placement["tilt"])
    return box.placement["tilt"], (origin_y, origin_y + box.height)


# ── The captain's choice, as a property of a CORRECT reel ──────────

def test_the_row_draws_the_hand_place(tmp_path):
    """The whole task, in one assertion.

    Under the declared row a reel stores Tilt -918 - his hand -917 to
    half a delivery pixel - and puts its caption canvas on rows
    1179..1659, centre 1419: the place he chose by hand, settled by
    still measurement rather than arithmetic.
    """
    tilt, rows = _caption_canvas(_project(tmp_path, CAPTAIN_ROW))
    assert tilt == pytest.approx(HAND_TILT, abs=1.0)
    assert rows == pytest.approx(CANVAS_ROWS)




# ── Why the value is DERIVED and not chosen ────────────────────────





def test_the_new_placement_is_one_resolve_can_hold(tmp_path):
    """A row Resolve would clamp is a row that ships misplaced
    captions - the defect `placement_holds` exists for. -918 is well
    inside the measured Tilt rail."""
    style = subtitle_style.resolve_subtitle_style(
        project_folder=_project(tmp_path, CAPTAIN_ROW))
    box = tight_box.constant_caption_box({
        "width": FRAME_W, "height": FRAME_H, "style": style,
        "subtitles": [{"text": "a caption"}],
    })
    assert tight_box.placement_holds(box.placement, FRAME_W, FRAME_H) == ""


# ── Project-level, not a pin on one reel ───────────────────────────

REELS = (
    "Reel 01 - geo-is-comprehension-not-position",
    "Reel 09 - your-website-is-only-20-percent (final)",
    "Reel 13 - the-accounting-firm-ai-called-healthcare",
    "Reel 23 - why-small-business-wins-on-ai",
    "Reel 26 - write-for-the-question-your-customer-ask",
    "Reel 28 - the-nail-salon-query-google-cant-answer",
    "Reel 30 - your-google-business-profile-and-the-map",
    "Reel 31 - is-there-a-way-to-game-ai",
)




def test_a_per_reel_pin_still_outranks_the_project_row(tmp_path):
    """The project row is the series look; the per-reel file stays the
    way one reel says something different. Moving the project value
    does not take that away."""
    folder = _project(tmp_path, CAPTAIN_ROW)
    external = tmp_path / "external"
    external.mkdir()
    (external / "reel_caption_row.json").write_text(
        '{"version": 1, "rows": [{"reel": "Reel 13", '
        '"caption_row": 0.83125, "reason": "a later pin, if one is '
        'ever asked for"}]}', encoding="utf-8")
    assert subtitle_style.project_caption_row(
        folder, reel_name=REELS[2]) == pytest.approx(SUPERSEDED_ROW)
    assert subtitle_style.project_caption_row(
        folder, reel_name=REELS[1]) == pytest.approx(CAPTAIN_ROW)


# ── What else the row moves, stated rather than discovered ─────────

