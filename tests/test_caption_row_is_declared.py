"""A project declares WHERE its caption row is, and values are COMPUTED.

The captain, 2026-09-11, looking at Reel 28: *"all of the subtitles were
positioned at y=-870 but the subtitles were not in the right place, and
in actuality positioning them closer to y=-420 is around where the
subtitles should actually be."*

Measured on artefact pixels, that was not a stored-number complaint. The
engine's own caption row (safe-area bottom inset lifted by
`CAPTION_LIFT_PX`, 1599 on 1080x1920) is where exactly ONE reel of the
field test draws - the only one rebuilt since that row took its current
value - and four others sit together 220px above it.

So the row is a per-series look and belongs to the project (AGENTS.md
14). It is declared as a PLACE on the delivered frame, never as a stored
transform: a full-frame caption at Tilt 0 and a 480-tall tight caption
at Tilt -870 draw in the same place, so a declaration in transform units
would mean two different things for one row. That is the trap this
project has fallen into twice.

Synthetic under `tmp_path`; nothing reaches Resolve or a real project.
"""

import textwrap

import pytest

from library.tools import subtitle_style
from library.tools.safe_area import safe_area_for_frame

FRAME_W, FRAME_H = 1080, 1920


def _project(tmp_path, body: str):
    (tmp_path / "project.yaml").write_text(textwrap.dedent(body),
                                           encoding="utf-8")
    return str(tmp_path)


BASE = """\
name: test
delivery_format: vertical_1080x1920
pipeline:
{block}
"""


def _with_pipeline(tmp_path, block: str):
    return _project(tmp_path, BASE.format(
        block=textwrap.indent(textwrap.dedent(block), "  ")))


# ── The declaration ────────────────────────────────────────────────

def test_a_project_that_declares_nothing_keeps_the_engine_row(tmp_path):
    """The engine states no row for anybody else's series."""
    folder = _with_pipeline(tmp_path, "subtitle_typography:\n  size: 58\n")
    assert subtitle_style.project_caption_row(folder) is None
    props = subtitle_style.resolve_subtitle_style(project_folder=folder)
    engine_row = (FRAME_H - safe_area_for_frame(FRAME_W, FRAME_H).bottom
                  - subtitle_style.CAPTION_LIFT_PX)
    assert FRAME_H - props["safeArea"]["bottom"] == engine_row


def test_a_declared_row_reaches_the_render(tmp_path):
    folder = _with_pipeline(tmp_path, """\
        subtitle_position:
          caption_row: 0.71875
          reason: the captain's own, 2026-09-11
        """)
    assert subtitle_style.project_caption_row(folder) == pytest.approx(0.71875)
    props = subtitle_style.resolve_subtitle_style(project_folder=folder)
    # 0.71875 * 1920 = 1380, and the overlay positions against the
    # distance from the BOTTOM edge, which is what has to move.
    assert FRAME_H - props["safeArea"]["bottom"] == 1380
    assert props["safeArea"]["bottom"] == 540


def test_a_declared_row_replaces_the_lift_rather_than_stacking(tmp_path):
    """The project is naming the row. The engine's own lift is not a
    second opinion to add on top of it."""
    folder = _with_pipeline(tmp_path, """\
        subtitle_position:
          caption_row: 0.5
          reason: half way down
        """)
    props = subtitle_style.resolve_subtitle_style(project_folder=folder)
    assert FRAME_H - props["safeArea"]["bottom"] == 960
    assert subtitle_style.CAPTION_LIFT_PX not in (
        props["safeArea"]["bottom"] - 960,)


def test_only_the_bottom_inset_moves(tmp_path):
    """`captionMaxWidth` and the other three insets are platform facts
    and motion graphics read the profile directly, so none of them
    follows the captions."""
    (tmp_path / "a").mkdir(parents=True, exist_ok=True)
    (tmp_path / "b").mkdir(parents=True, exist_ok=True)
    plain = _with_pipeline(tmp_path / "a", "subtitle_typography:\n  size: 58\n")
    moved = _with_pipeline(tmp_path / "b", """\
        subtitle_position:
          caption_row: 0.6
          reason: moved
        """)
    before = subtitle_style.resolve_subtitle_style(project_folder=plain)
    after = subtitle_style.resolve_subtitle_style(project_folder=moved)
    assert before["captionMaxWidth"] == after["captionMaxWidth"]
    for edge in ("top", "left", "right"):
        assert before["safeArea"][edge] == after["safeArea"][edge]
    assert before["safeArea"]["bottom"] != after["safeArea"]["bottom"]


def test_the_row_is_a_fraction_so_it_survives_a_format_change():
    """A pixel row would mean a different place on every delivery
    format. `caption_row_px` is the only place it becomes pixels."""
    assert subtitle_style.caption_row_px(0.71875, 1920) == 1380
    assert subtitle_style.caption_row_px(0.71875, 1280) == 920


@pytest.mark.parametrize("block,exc,match", [
    ("subtitle_position: 0.7\n", TypeError, "must be a mapping"),
    ("subtitle_position:\n  reason: no row\n", ValueError, "names no"),
    ("subtitle_position:\n  caption_row: 1380\n", ValueError, "FRACTION"),
    ("subtitle_position:\n  caption_row: 0\n", ValueError, "FRACTION"),
    ("subtitle_position:\n  caption_row: nope\n", TypeError, "must be a number"),
    ("subtitle_position:\n  caption_row: 0.7\n  nudge: 3\n",
     ValueError, "nothing reads"),
])
def test_a_malformed_declaration_refuses(tmp_path, block, exc, match):
    """A caption position silently dropped is a caption the editor
    believes shipped - `project_subtitle_typography`'s own reasoning."""
    folder = _with_pipeline(tmp_path, block)
    with pytest.raises(exc, match=match):
        subtitle_style.project_caption_row(folder)


# ── Why it is a ROW and not a number ───────────────────────────────

def test_two_carriages_need_different_numbers_for_one_row():
    """The trap, stated as a test. A full-frame caption and a tight one
    draw in the same place from very different stored values, so a
    declaration in transform units means two things at once."""
    from library.tools.tight_box import placement_for_box

    row = 1380
    # A 480-tall tight canvas whose bottom sits PAD_BOTTOM under the row.
    tight = placement_for_box(840, 480, 540.0, row + 36 - 240.0,
                              FRAME_W, FRAME_H)
    # The full-frame carriage draws the same ink at Tilt 0 - the render
    # put it there - so the two stored numbers differ by hundreds.
    assert tight["tilt"] != 0
    assert abs(tight["tilt"]) > 100
