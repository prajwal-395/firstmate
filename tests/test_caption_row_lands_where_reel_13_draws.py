"""The caption row the captain chose, and the arithmetic that derives it.

The captain, 2026-09-15, looking at two exported frames side by side -
Reel 13 and Reel 09 - and choosing Reel 13:

    "well actually how the subtitles show up on this frame is how i like
    it compared to reel 9. i like the caption to be a bit lower than how
    it is on reel 09, that being said idk what the discrepencies in
    pan/tilt values are but the overall positoning of how this reel
    looks is how where i want the subtitles placed and i just want to
    use whatever pan/tilt values that get us there"

That REVERSES the question that was open.  It had been "conform Reels 09
and 13 up to the declared row 0.71875"; the answer is no, and the
DECLARATION is what moves.

What makes this delicate
------------------------
The position the captain likes is currently produced BY A DEFECT.  Reels
01, 13, 23, 30 and 31 hold exactly 2x the Pan/Tilt their own build
wrote; Reel 13's build snapshot says caption Tilt -864 and its live
timeline holds -1728.  The captain blessed the LOOK, not the doubling,
and said so in their own words.  So the target is stated as a property
of a CORRECT reel:

    a reel whose transforms are not doubled must draw its captions where
    Reel 13 draws them today.

That is what this file pins, and it needs no Resolve: the whole chain
from the declared row to the frame rows the caption canvas occupies is
arithmetic this repository already owns.

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
    ``canvas_h / frame_h = 480/1920 = 0.25`` delivery pixels::

        tilt = -(canvas_centre_y - 960) * 1920/480

What that gives for the two rows, computed below rather than quoted:

    ============  ======  ===========  ======  ==================
    row           row_px  safe.bottom  Tilt    canvas frame rows
    ============  ======  ===========  ======  ==================
    0.71875       1380    540          -864    936 .. 1416
    0.83125       1596    324          -1728   1152 .. 1632
    ============  ======  ===========  ======  ==================

-864 is exactly what Reel 13's own build wrote and exactly what Reels
09/26/28 hold live.  **-1728 is exactly what Reel 13 holds live** - the
frame the captain approved - so a correct reel under 0.83125 lands its
caption canvas on the identical frame rows, with no doubling anywhere.

Where 0.83125 comes from
------------------------
It is read back off the approved frame, not chosen.  Reel 13's live
caption Tilt is -1728 and its canvas therefore occupies frame rows
1152..1632; the row that canvas hangs from is its bottom less
``PAD_BOTTOM``::

    row_px = 1632 - 36 = 1596        1596 / 1920 = 0.83125 exactly

Cross-checked against exported pixels, measured 2026-09-15 on the live
builds (``caption_ink_all_reels.json`` in the re-measurement lane's
evidence): caption ink bottoms are 1362/1372/1378 (min/median/max) on
Reel 09 at row 1380, and 1579/1587/1594 on Reel 13.  The predicted shift
is 1632-1416 = 216 px; the measured shift is 215 px at the median and
216 px at the max.  The engine's own undeclared row here is 1599, three
pixels below the captain's choice - so what they picked is very close to
what this engine would have drawn with no declaration at all, and the
declaration is kept explicit anyway because the row is a per-series look
the project owns (AGENTS.md 14), not something to inherit by accident.

What this file does NOT prove
-----------------------------
That a rebuild stores -1728 rather than doubling it to -3456.  The
doubling is a live, uncaused defect on five reels and nothing here
repairs it; the check that belongs to the rebuild is in the PR body.

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

#: The row the captain chose on 2026-09-15, read back off Reel 13's
#: approved frame: its caption canvas bottom (1632) less `PAD_BOTTOM`.
CAPTAIN_ROW = 0.83125

#: The row that was declared before it, from the 2026-09-11 ruling. It
#: is where Reels 09/26/28 draw today - the frame the captain compared
#: against and rejected.
SUPERSEDED_ROW = 0.71875

#: Reel 13's live caption Tilt and the frame rows its 904x480 caption
#: canvas therefore occupies - the approved picture, measured
#: 2026-09-15 with the timeline CURRENT (a non-current handle returns
#: Pan/Tilt scaled by the current timeline's resolution).
REEL_13_LIVE_TILT = -1728.0
REEL_13_CANVAS_ROWS = (1152.0, 1632.0)

#: Reel 09's live caption Tilt and canvas rows on the same reading -
#: and also what Reel 13's OWN build snapshot wrote, before the
#: doubling.
REEL_09_LIVE_TILT = -864.0
REEL_09_CANVAS_ROWS = (936.0, 1416.0)

#: Caption ink bottoms measured on the current builds, 2026-09-15,
#: as (min, median, max) delivery rows over every caption on the reel.
REEL_13_INK_BOTTOMS = (1579, 1587, 1594)
REEL_09_INK_BOTTOMS = (1362, 1372, 1378)


def _project(tmp_path, row=None):
    """A project folder declaring `row`, or declaring no row at all."""
    if row is None:
        block = "subtitle_typography:\n  size: 58\n"
    else:
        block = (f"subtitle_position:\n"
                 f"  caption_row: {row}\n"
                 f"  reason: the captain's own, 2026-09-15, off Reel 13\n")
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

def test_the_new_row_draws_where_reel_13_draws_today(tmp_path):
    """The whole task, in one assertion.

    Under the new declaration a reel with UNDOUBLED transforms stores
    the same caption Tilt Reel 13 holds live and puts its caption
    canvas on the identical frame rows - so the captain gets the frame
    they approved without the defect that produced it.
    """
    tilt, rows = _caption_canvas(_project(tmp_path, CAPTAIN_ROW))
    assert tilt == pytest.approx(REEL_13_LIVE_TILT)
    assert rows == pytest.approx(REEL_13_CANVAS_ROWS)


def test_the_old_row_draws_where_reel_09_draws_today(tmp_path):
    """The other half of the same proof, and what makes the first one
    mean something: under the superseded declaration the same correct
    reel lands on Reel 09's rows - the frame the captain rejected."""
    tilt, rows = _caption_canvas(_project(tmp_path, SUPERSEDED_ROW))
    assert tilt == pytest.approx(REEL_09_LIVE_TILT)
    assert rows == pytest.approx(REEL_09_CANVAS_ROWS)


def test_the_two_rows_differ_by_the_shift_the_pixels_measured(tmp_path):
    """216 delivery pixels, predicted; 215 at the median of the
    measured ink and 216 at its max. Agreement to a pixel is what says
    the arithmetic above describes the frames the captain looked at,
    rather than a model that merely sounds right."""
    _, old_rows = _caption_canvas(_project(tmp_path / "a", SUPERSEDED_ROW))
    _, new_rows = _caption_canvas(_project(tmp_path / "b", CAPTAIN_ROW))
    predicted = new_rows[1] - old_rows[1]
    assert predicted == pytest.approx(216.0)

    for measured_13, measured_09 in zip(REEL_13_INK_BOTTOMS,
                                        REEL_09_INK_BOTTOMS):
        assert abs((measured_13 - measured_09) - predicted) <= 1


def test_the_measured_ink_lands_inside_the_new_canvas(tmp_path):
    """Every caption ink bottom measured on the approved reel sits
    inside the canvas the new declaration produces, and within the
    placement tolerance of the new row - while the superseded row
    misses every one of them by ~200px, eight times that tolerance."""
    _, (top, bottom) = _caption_canvas(_project(tmp_path, CAPTAIN_ROW))
    new_row_px = subtitle_style.caption_row_px(CAPTAIN_ROW, FRAME_H)
    old_row_px = subtitle_style.caption_row_px(SUPERSEDED_ROW, FRAME_H)

    for ink_bottom in REEL_13_INK_BOTTOMS:
        assert top < ink_bottom < bottom
        assert abs(ink_bottom - new_row_px) <= tight_box.INTENT_TOLERANCE_PX
        assert abs(ink_bottom - old_row_px) > 8 * \
            tight_box.INTENT_TOLERANCE_PX


# ── Why the value is DERIVED and not chosen ────────────────────────

def test_the_row_is_read_back_off_the_approved_canvas():
    """0.83125 is not a taste value: it is Reel 13's own canvas bottom
    less `PAD_BOTTOM`, over the delivery height, and it divides
    exactly."""
    row_px = REEL_13_CANVAS_ROWS[1] - tight_box.PAD_BOTTOM
    assert row_px == 1596.0
    assert row_px / FRAME_H == CAPTAIN_ROW
    assert subtitle_style.caption_row_px(CAPTAIN_ROW, FRAME_H) == 1596


@pytest.mark.parametrize("row", [0.6, 0.71875, 0.8, CAPTAIN_ROW, 0.85])
def test_the_canvas_bottom_tracks_the_row_one_for_one(tmp_path, row):
    """The relation the derivation rests on, checked across the band
    rather than at the two points it has to hold at."""
    _, (_, bottom) = _caption_canvas(_project(tmp_path, row))
    row_px = subtitle_style.caption_row_px(row, FRAME_H)
    assert bottom == pytest.approx(row_px + tight_box.PAD_BOTTOM)


def test_the_new_placement_is_one_resolve_can_hold(tmp_path):
    """A row Resolve would clamp is a row that ships misplaced
    captions - the defect `placement_holds` exists for. -1728 is well
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


@pytest.mark.parametrize("reel", REELS)
def test_the_row_governs_every_reel_not_just_the_one_looked_at(tmp_path,
                                                               reel):
    """*"the overall positioning of how this reel looks is how where i
    want the subtitles placed"* is a statement about the series, so it
    is the PROJECT row that moves. With no `reel_caption_row.json` in
    `external/`, every reel resolves to it - which is also what makes
    a later per-reel pin still possible without unpicking this."""
    folder = _project(tmp_path, CAPTAIN_ROW)
    assert declared_row(folder, reel) is None
    assert subtitle_style.project_caption_row(
        folder, reel_name=reel) == pytest.approx(CAPTAIN_ROW)
    tilt, rows = _caption_canvas(folder, reel_name=reel)
    assert tilt == pytest.approx(REEL_13_LIVE_TILT)
    assert rows == pytest.approx(REEL_13_CANVAS_ROWS)


def test_a_per_reel_pin_still_outranks_the_project_row(tmp_path):
    """The project row is the series look; the per-reel file stays the
    way one reel says something different. Moving the project value
    does not take that away."""
    folder = _project(tmp_path, CAPTAIN_ROW)
    external = tmp_path / "external"
    external.mkdir()
    (external / "reel_caption_row.json").write_text(
        '{"version": 1, "rows": [{"reel": "Reel 13", '
        '"caption_row": 0.71875, "reason": "a later pin, if one is '
        'ever asked for"}]}', encoding="utf-8")
    assert subtitle_style.project_caption_row(
        folder, reel_name=REELS[2]) == pytest.approx(SUPERSEDED_ROW)
    assert subtitle_style.project_caption_row(
        folder, reel_name=REELS[1]) == pytest.approx(CAPTAIN_ROW)


# ── What else the row moves, stated rather than discovered ─────────

def test_the_lower_third_floor_follows_the_captions_down(tmp_path):
    """Moving the caption row moves the floor the lower thirds clear,
    by design: `speaker_identity.placement_box` grows its box upward
    from whichever row the project declared. Captions 216px lower means
    216px more room for the identity graphic, and a reviewer should see
    that here rather than on a rebuilt reel."""
    from library.tools.speaker_identity import placement_box

    tallest_card = 188
    old = placement_box(_project(tmp_path / "a", SUPERSEDED_ROW),
                        FRAME_W, FRAME_H, tallest_card)
    new = placement_box(_project(tmp_path / "b", CAPTAIN_ROW),
                        FRAME_W, FRAME_H, tallest_card)
    assert old["caption_row"] == 1380
    assert new["caption_row"] == 1596
    assert new["floor"] - old["floor"] == 216
    assert old["insets"]["bottom"] - new["insets"]["bottom"] == 216
