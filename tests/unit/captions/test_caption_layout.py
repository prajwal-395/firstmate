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
from __future__ import annotations
import textwrap
import pytest
from library.tools import subtitle_style
from library.tools.safe_area import safe_area_for_frame
import os
import sys
from pathlib import Path
import json
import yaml
from library.tools import full_frame_element as ffe
from library.tools import timeline_layout as layout
from library.tools import safe_zone_policy as szp
from library.tools.safe_area import resolve_safe_area
import subprocess
import numpy as np
from PIL import Image
from library.tools import platform_safe_zones as psz
from library.tools import caption_width as cw
from library.tools import reel_touchup as touchup
from library.tools.tight_box import placement_for_box
from library.tools import caption_band as band
from library.tools.motion_graphics_plan import resolve_plan


FRAME_W, FRAME_H = 1080, 1920


def _project(tmp_path, body: str):
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def test_a_declared_row_reaches_the_render_replacing_the_lift(tmp_path):
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

    # The declared row REPLACES the engine's lift rather than stacking.
    folder = _with_pipeline(tmp_path / "half", """\
        subtitle_position:
          caption_row: 0.5
          reason: half way down
        """)
    props = subtitle_style.resolve_subtitle_style(project_folder=folder)
    assert FRAME_H - props["safeArea"]["bottom"] == 960
    assert subtitle_style.CAPTION_LIFT_PX not in (
        props["safeArea"]["bottom"] - 960,)


def test_the_row_is_a_fraction_so_it_survives_a_format_change():
    """A pixel row would mean a different place on every delivery
    format. `caption_row_px` is the only place it becomes pixels."""
    assert subtitle_style.caption_row_px(0.71875, 1920) == 1380
    assert subtitle_style.caption_row_px(0.71875, 1280) == 920


MALFORMED = [
    ("subtitle_position: 0.7\n", TypeError, "must be a mapping"),
    ("subtitle_position:\n  reason: no row\n", ValueError, "names no"),
    ("subtitle_position:\n  caption_row: 1380\n", ValueError, "FRACTION"),
    ("subtitle_position:\n  caption_row: 0.7\n  nudge: 3\n",
     ValueError, "nothing reads"),
]


def test_a_malformed_declaration_refuses(tmp_path):
    """A caption position silently dropped is a caption the editor
    believes shipped - `project_subtitle_typography`'s own reasoning."""
    for n, (block, exc, match) in enumerate(MALFORMED):
        folder = _with_pipeline(tmp_path / str(n), block)
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


# --------------------------------------------------------------------------
# From test_caption_row_lands_where_reel_01_draws.py
#
# The caption row the captain chose, and the arithmetic that derives it.
#
# The captain, 2026-09-17, on Reel 01, hand-moving all 34 captions to
# Tilt -917: "i like that". The number is evidence of the PLACE, not its
# specification - stored transforms were halved when he set it - so the
# place was SETTLED BY MEASUREMENT: one gallery still of Reel 01 frame
# 20, caption white-fill rows 1462..1581 in the still against 283..403
# in the file (correlation 0.9977, canvas origin 1178.5, centre 1418.5).
# Row 1623, re-derived from his hand value rather than picked.
#
# That SUPERSEDES the same-day 0.7217 (Reel 26's band, Tilt -887) and
# the 2026-09-15 0.83125 (Reel 13's band) - two rulings on file and the
# older ones must not be picked, which is the exact failure this file
# guards alongside the arithmetic.
#
# The derivation, in full
# -----------------------
# Every number below is on the field test's 1080x1920 delivery frame with
# the captain's 840px wrap, whose structural caption canvas is 904x480
# (``tight_box.constant_caption_box``).
#
# 1.  ``subtitle_style.caption_row_px`` turns the declared fraction into a
#     pixel row, and ``_lifted_props`` turns that into the bottom inset
#     the render positions against::
#
#         row_px       = round(row * 1920)
#         safe.bottom  = 1920 - row_px
#
# 2.  ``tight_box.constant_caption_box`` hangs the canvas from that inset,
#     one ``PAD_BOTTOM`` past the card edge::
#
#         canvas_top    = 1920 - safe.bottom + PAD_BOTTOM - 480
#                       = row_px + 36 - 480
#         canvas_bottom = row_px + 36
#
#     so the canvas moves 1:1 with the row, in delivery pixels.
#
# 3.  ``resolve_transform`` stores that as Tilt, one unit being
#     ``canvas_h / frame_h`` times the measured draw gain - 0.25 × 2.0
#     = 0.5 delivery pixels under today's renderer (see
#     ``resolve_transform`` for the 2026-09-17 rendered-pixel
#     calibration, and ``tests/unit/resolve/test_draw_gain_measured.py`` for the
#     derivation from measurements rather than restatement)::
#
#         tilt = -(canvas_centre_y - 960) / 0.5
#
# What that gives for the current row, computed below rather than quoted:
#
#     ============  ======  ===========  ======  ==================
#     row           row_px  safe.bottom  Tilt    canvas frame rows
#     ============  ======  ===========  ======  ==================
#     0.8451        1623    297          -918    1179 .. 1659
#     ============  ======  ===========  ======  ==================
#
# -918 is two stored units - half a delivery pixel of rounding - from
# his hand -917, which draws canvas centre y 1418.5. HIS HAND VALUE IS
# THE GROUND TRUTH; THE FRACTION IS OUR RECONSTRUCTION OF IT.
#
# Where 0.8451 comes from
# -----------------------
# Canvas centre cy 1418.5, bottom 1658.5; the row such a canvas hangs
# from is its bottom less ``tight_box.PAD_BOTTOM``::
#
#     1658.5 - 36 = 1622.5      1622.5 / 1920 = 0.845052... -> 0.8451
#     caption_row_px(0.8451, 1920) = 1623, the pixel row the build uses.
#
# What this file does NOT prove
# -----------------------------
# That the renderer still draws gain 2.0. The gain is renderer state -
# it read 1.0 on 2026-09-11 - and a stored value that reads back
# correctly proves nothing about it. The check that belongs to the
# rebuild is the gate still: Reel 01 frame 20 must render its caption
# centre at y 1418.5.
#
# Synthetic under ``tmp_path``; nothing reaches Resolve or a real project.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import tight_box
from library.tools.reel_caption_row import declared_row
from library.tools.resolve_transform import drawn_origin


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


def _project_2(tmp_path, row=None):
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
    tilt, rows = _caption_canvas(_project_2(tmp_path, CAPTAIN_ROW))
    assert tilt == pytest.approx(HAND_TILT, abs=1.0)
    assert rows == pytest.approx(CANVAS_ROWS)


# ── Why the value is DERIVED and not chosen ────────────────────────


def test_the_new_placement_is_one_resolve_can_hold(tmp_path):
    """A row Resolve would clamp is a row that ships misplaced
    captions - the defect `placement_holds` exists for. -918 is well
    inside the measured Tilt rail."""
    style = subtitle_style.resolve_subtitle_style(
        project_folder=_project_2(tmp_path, CAPTAIN_ROW))
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
    folder = _project_2(tmp_path, CAPTAIN_ROW)
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


# --------------------------------------------------------------------------
# From test_captions_ride_the_declared_row.py
#
# Finding 21: every caption segment lands on the declared row.
#
# Scout B4/B2: one caption segment per build sat at Tilt 0, inside the
# picture ("today is march" mid-frame at 288-324), while every other
# segment rode its tight-box placement to the lower letterbox row
# (Tilt -870). The placement chain has exactly one shape that ships
# Tilt 0 silently: a TIGHT canvas reaching the placer with no
# `tight_box.placement`, where `placement=None` reads as "full canvas,
# nothing to do" and the clip sits centred.
#
# The fix closes that shape at both ends:
#
# - compile_manifest refuses a declared-tight segment with no placement,
#   naming it (a render-side programming error - re-running the build
#   cannot fix what the render did not record);
# - the build refuses such a segment by name instead - skipped, warned,
#   counted nowhere - so whatever arrives some other way (stale state,
#   a hand edit) can never centre a caption mutely again.
#
# Full-canvas segments draw their text natively and ride
# untransformed, exactly as before.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_6_01_render.resolve_build_timeline import (  # noqa: E402
    caption_segment_placement,
)
from library.steps.step_5_04_compile_manifest.step import (  # noqa: E402
    _assert_subtitle_overlay_matches_plan,
)


def _seg(segment_id="sub_hook_abc123", geometry="tight",
         placement=None, **extra):
    seg = {"segment_id": segment_id, "geometry": geometry,
           "overlay_path": f"/tmp/{segment_id}.mov",
           "timeline_start": 0.0, "timeline_end": 2.0,
           "block_position": 1}
    if placement is not None or geometry == "tight":
        seg["tight_box"] = ({"width": 480, "height": 120,
                             "placement": placement}
                            if placement is not None else None)
    seg.update(extra)
    return seg


_PLACEMENT = {"scaling": 1, "pan": 0.0, "tilt": -870.0}


# ── the build guard ──────────────────────────────────────────────

def test_the_build_guard_never_centres_a_caption_silently():
    """Finding 21's shape - a tight canvas with no placement - is refused
    by name rather than shipped at a silent Tilt 0; full canvas rides
    untransformed; a segment too old to declare its geometry rides its
    placement if it has one and is refused if it has none."""
    placement, refusal = caption_segment_placement(
        _seg(placement=_PLACEMENT), 0, 3)
    assert (placement, refusal) == (_PLACEMENT, "")

    seg = _seg(segment_id="sub_today_is_march_9f2c", placement=None)
    assert seg.get("tight_box") is None
    placement, refusal = caption_segment_placement(seg, 7, 3)
    assert placement is None
    assert "sub_today_is_march_9f2c" in refusal
    assert "Tilt 0" in refusal

    placement, refusal = caption_segment_placement(
        _seg(geometry="full", placement=None), 0, 3)
    assert (placement, refusal) == (None, "")

    seg = _seg(placement=_PLACEMENT)
    del seg["geometry"]
    assert caption_segment_placement(seg, 0, 3) == (_PLACEMENT, "")
    seg = _seg(placement=None)
    del seg["geometry"]
    seg.pop("tight_box", None)
    placement, refusal = caption_segment_placement(seg, 0, 3)
    assert placement is None and refusal != ""


# ── the compile refusal ──────────────────────────────────────────

def _manifest(*segments):
    subs = [{"spine_block_position": 1, "timeline_start": 0.0,
             "timeline_end": 2.0}]
    return {"subtitles": subs,
            "project": {"frame_rate": 30.0},
            "subtitle_overlay": {"segments": list(segments)}}


def test_compile_refuses_only_a_tight_segment_with_no_placement():
    import pytest
    seg = _seg(segment_id="sub_today_is_march_9f2c", placement=None)
    with pytest.raises(ValueError, match="sub_today_is_march_9f2c"):
        _assert_subtitle_overlay_matches_plan(_manifest(seg))
    _assert_subtitle_overlay_matches_plan(
        _manifest(_seg(geometry="full", placement=None)))
    _assert_subtitle_overlay_matches_plan(
        _manifest(_seg(placement=_PLACEMENT)))


# --------------------------------------------------------------------------
# From test_reel_caption_row.py
#
# One reel's caption row, falling back to the project value.
#
# The caption row was one project-level fraction
# (`pipeline.subtitle_position.caption_row`): a reel that needed its
# own band had nowhere to say it. `external/reel_caption_row.json`
# (`library/tools/reel_caption_row.py`) is the per-reel override,
# preferred over the project value wherever the row is read and falling
# back to it where the reel declares none.
#
# Fail-before: `library.tools.reel_caption_row` has no `declared_row`
# and `project_caption_row` takes no reel - every test here errors on
# the attribute or the argument, not on an assertion.

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.tools import reel_caption_row
from library.tools.subtitle_style import project_caption_row


REEL = "Reel 01 - the-cta"
ROW = 0.72
PROJECT_ROW = 0.83
REASON = "captain: clears the lower third on this reel"


def _row(reel=REEL, row=ROW, reason=REASON):
    return {"reel": reel, "caption_row": row, "reason": reason}


def _project_3(tmp_path, pipeline_block=None):
    project = tmp_path / "project"
    project.mkdir()
    body = {"name": "T", "slug": "t"}
    if pipeline_block is not None:
        body["pipeline"] = pipeline_block
    (project / "project.yaml").write_text(yaml.safe_dump(body),
                                          encoding="utf-8")
    (project / "external").mkdir()
    return project


def _write_rows(project, body):
    (project / "external" / "reel_caption_row.json").write_text(
        json.dumps(body), encoding="utf-8")


# ── 1. The declaration validates, loudly ─────────────────────────────


def test_the_declaration_validates_loudly(tmp_path):
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.validate_rows([_row(row=1385)])  # a pixel row
    project = _project_3(tmp_path)
    assert reel_caption_row.load_rows(str(project)) == []  # no file
    _write_rows(project, {"version": 1, "rows": [{"reel": REEL}]})
    with pytest.raises(reel_caption_row.ReelCaptionRowError):
        reel_caption_row.load_rows(str(project))


def test_a_reel_row_wins_over_the_project_row_through_a_staging_suffix(
        tmp_path):
    project = _project_3(
        tmp_path,
        {"subtitle_position": {"caption_row": PROJECT_ROW,
                               "reason": "series look"}})
    _write_rows(project, {"version": 1, "rows": [_row()]})
    assert reel_caption_row.declared_row(str(project), REEL) == ROW
    assert project_caption_row(str(project), reel_name=REEL) == ROW
    assert reel_caption_row.declared_row(
        str(project), REEL + " (scratch 7) (rebuild staging)") == ROW


def test_everything_else_reads_the_project_row(tmp_path):
    """An undeclared reel, no reel name, and no override file at all
    each read the project value exactly."""
    project = _project_3(
        tmp_path,
        {"subtitle_position": {"caption_row": PROJECT_ROW,
                               "reason": "series look"}})
    assert project_caption_row(str(project), reel_name=REEL) == PROJECT_ROW
    _write_rows(project, {"version": 1, "rows": [_row()]})
    assert reel_caption_row.declared_row(
        str(project), "Reel 02 - something-else") is None
    assert project_caption_row(
        str(project), reel_name="Reel 02 - something-else") == PROJECT_ROW
    assert project_caption_row(str(project)) == PROJECT_ROW


# --------------------------------------------------------------------------
# From test_card_row_role.py
#
# The closing card's row is DECLARED, not defaulted.
#
# The closing logo animation landed on V1 - Akshita's camera row - on
# every reel because `build_reel_timeline` placed it on the first a-roll
# row by POSITION, with nothing declared to say otherwise (2026-09-12,
# captain's blue marker on Reel 13). The row is now one declared value -
# `effect.card_row_role`, a track-plan ROLE - resolved through
# `rows_for_role` like every other overlay element, and refused when
# absent.
#
# Covered here:
#   A. the declaration itself (project wins, bad values refused, the
#      actionable refusal when cards exist and no role does);
#   B. the layout: card spans mint the role's row, and the placer replays
#      the plan's own packing to find each card's lane;
#   C. the placement, against fake Resolve: a tail card lands on the row
#      NAMED for its role on layouts with different track counts - never
#      V1 by index - and cards without a role refuse before anything is
#      placed;
#   D. the verifier: F13 fails a card on a row whose NAME does not match
#      its declared role, and the snapshot classifier keeps a card on an
#      overlay row inside the picture bucket;
#   E. the rebuild digest carries the role where cards exist, and stays
#      byte-identical where they do not.

FPS = 24000 / 1001


# ── A. The declaration ─────────────────────────────────────────────

def _project_4(tmp_path, effect: dict):
    import yaml
    (tmp_path / "project.yaml").write_text(
        yaml.safe_dump({"effect": effect}), encoding="utf-8")
    return str(tmp_path)


def test_no_declaration_is_no_role_and_the_project_wins(tmp_path):
    assert ffe.resolve_card_row_role({}, str(tmp_path)) is None
    assert ffe.resolve_card_row_role(None, None) is None
    project = _project_4(tmp_path, {"card_row_role": "motion_graphics"})
    assert ffe.resolve_card_row_role(
        {"card_row_role": "semantic"}, project) == "motion_graphics"


def test_anything_but_the_two_roles_or_no_role_with_cards_is_refused(
        tmp_path):
    for bad in ("V5", "captions"):
        with pytest.raises(ffe.CardRowRoleError, match="card_row_role"):
            ffe.resolve_card_row_role({"card_row_role": bad}, str(tmp_path))
    with pytest.raises(ffe.CardRowRoleError, match="card_row_role"):
        ffe.require_card_row_role({}, str(tmp_path))
    with pytest.raises(ValueError, match="card_role"):
        layout.plan_layout({"angles": _angles(), "card_role": "V5"})


# ── B. The layout ──────────────────────────────────────────────────

def _angles():
    return [{"key": "1", "label": "Akshita",
             "speech_name": "Akshita CH1", "program_channel": 1},
            {"key": "2", "label": "Craig",
             "speech_name": "Craig CH1", "program_channel": 1}]


def test_card_spans_mint_the_role_row_and_join_its_packing():
    """A row exists because something goes on it: the card alone is
    enough for a Semantic row."""
    plan = layout.plan_layout({
        "angles": _angles(), "caption_spans": [(0, 100)],
        "card_role": "semantic", "card_spans": [(480, 551)]})
    names = [(t.index, t.name) for t in plan.video_tracks]
    assert names == [(1, "Akshita"), (2, "Craig"), (3, "Subtitles"),
                     (4, "Semantic")]
    assert plan.rows_for_role("semantic")[0].index == 4

    # An overlay overlapping the card mints a second row rather than
    # sharing one - and the replay finds the card's lane.
    overlay = (400, 500)
    card = (480, 551)
    plan = layout.plan_layout({
        "angles": _angles(),
        "semantic_spans": [overlay],
        "card_role": "semantic", "card_spans": [card]})
    rows = plan.rows_for_role("semantic")
    assert [t.name for t in rows] == ["Semantic", "Semantic 2"]
    ordered = layout.card_spans_for_role(plan.material, "semantic")
    assert ordered == [overlay, card]
    assert layout.lane_of_span(ordered, 0) == 0
    assert layout.lane_of_span(ordered, 1) == 1
    assert rows[layout.lane_of_span(ordered, 1)].name == "Semantic 2"


# ── C. The placement, against fake Resolve ─────────────────────────
# The faithful fakes live with the SOP proof; this file only adds the
# card to the world they already build.

from tests.scenarios.test_reel_build_sop_conformance import (  # noqa: E402
    FakeMoment,
    _caption,
    _master_clips,
    _semantic,
    _transcript,
    _world,
)
from library.tools.full_frame_element import PlannedCard  # noqa: E402
from library.tools.reel_build import (  # noqa: E402
    ReelBuildError,
    build_reel_timeline,
)


def _tail_card(tmp_path, start=480, frames=71):
    path = tmp_path / "logo_reveal.mov"
    path.write_bytes(b"\x00")
    return PlannedCard(
        index=1, element="full_frame_clip", placement="tail",
        reel_start_frame=start, duration_seconds=3.0,
        duration_frames=frames, props={}, render_name="logo_reveal",
        source_frames=90, rendered_path=str(path))


def _card_row_names(timeline):
    return {i: timeline.GetTrackName("video", i)
            for i in range(1, timeline.GetTrackCount("video") + 1)}


def _row_of(timeline, name):
    for i in range(1, timeline.GetTrackCount("video") + 1):
        if any(item.GetName() == name
               for item in timeline.GetItemListInTrack("video", i)):
            return i
    return None


def test_a_tail_card_lands_on_the_row_named_for_its_role_not_v1(tmp_path):
    """The defect, planted: V1 here is Akshita, and the card must not
    be on it."""
    timeline, pool, project = _world()
    record = build_reel_timeline(
        project, FakeMoment(), _master_clips(), [_caption(2.0, 4.0)],
        23.976, 1080, 1920, str(tmp_path), _transcript(),
        cards=[_tail_card(tmp_path)],
        semantic_segments=[_semantic(0.0, 48)],
        master_timeline=None, program_channels={"1": 1, "2": 1},
        card_row_role="semantic")
    names = _card_row_names(timeline)
    assert names[1] == "Akshita"
    assert _row_of(timeline, "logo_reveal.mov") == 4
    assert names[4] == "Semantic"
    # And the SOP proof still reads the built timeline clean: named
    # rows, nothing empty, picture linked to its speech.
    from library.tools.timeline_conformance import verify_timeline
    from library.tools.timeline_layout import TrackPlan, TrackSpec
    raw = record["track_plan"]
    plan = TrackPlan(
        video_tracks=[TrackSpec(**t) for t in raw["video_tracks"]],
        audio_tracks=[TrackSpec(**t) for t in raw["audio_tracks"]],
        material=raw.get("material", {}))
    report = verify_timeline(timeline, plan=plan)
    assert report["passed"], report["violations"]

    # One declared value to change: a different role, a different track
    # count - and the card follows the NAME.
    timeline, pool, project = _world()
    build_reel_timeline(
        project, FakeMoment(), _master_clips(), [_caption(2.0, 4.0)],
        23.976, 1080, 1920, str(tmp_path), _transcript(),
        cards=[_tail_card(tmp_path)],
        master_timeline=None, program_channels={"1": 1, "2": 1},
        card_row_role="motion_graphics")
    names = _card_row_names(timeline)
    assert names[1] == "Akshita"
    row = _row_of(timeline, "logo_reveal.mov")
    assert row is not None and row != 1
    assert names[row] == "Motion Graphics"


def test_cards_without_a_declared_role_refuse_before_placing(tmp_path):
    timeline, pool, project = _world()
    with pytest.raises(ReelBuildError, match="card_row_role"):
        build_reel_timeline(
            project, FakeMoment(), _master_clips(), [], 23.976, 1080,
            1920, str(tmp_path), _transcript(),
            cards=[_tail_card(tmp_path)],
            master_timeline=None, program_channels={"1": 1, "2": 1},
            card_row_role=None)
    assert not hasattr(pool, "created_name"), \
        "the refusal must fire before a timeline exists"


# ── D. The verifier ────────────────────────────────────────────────

from library.tools.reel_conformance_verifier import (  # noqa: E402
    FindingClass,
    PlannedCard as _PlanCard,
    TimelineItem,
    _snapshot_to_reel_timeline,
    check_delivered_framing,
    check_full_frame_cards,
)

CARD_FILE = "/p/logo_reveal.mov"
CARD_FRAMES = 71


def _placed(track_index, track_name, start=480, frames=CARD_FRAMES,
            transform=None):
    return TimelineItem(
        track_type="video", track_index=track_index,
        start_frame=start, end_frame=start + frames,
        duration_frames=frames,
        source_start_frame=0, source_end_frame=90,
        source_file=CARD_FILE, speaker=None, name="logo_reveal.mov",
        track_name=track_name, transform=dict(transform or {}))


def _declared_card():
    return _PlanCard(render_name="logo_reveal", placement="tail",
                     reel_start_frame=480, duration_frames=CARD_FRAMES,
                     element="full_frame_clip")


def test_f13_fails_a_card_on_a_row_whose_name_breaks_its_role_only():
    """THE gate this task was missing: the logo on V1 Akshita, with
    semantic declared, is an error naming the declared row.

    The input that breaks it is a card item whose track NAME is a
    camera row while `card_row_role` declares an overlay role."""
    findings = check_full_frame_cards(
        "Reel 13", [_declared_card()], [_placed(1, "Akshita")],
        1080, 1920, FPS, card_row_role="semantic")
    assert [f.finding_class for f in findings] == [FindingClass.F13]
    assert "Semantic" in findings[0].message
    assert findings[0].detail["card_row_role"] == "semantic"
    # And passes the card on its declared row.
    assert check_full_frame_cards(
        "Reel 13", [_declared_card()], [_placed(5, "Semantic")],
        1080, 1920, FPS, card_row_role="semantic") == []


def test_the_classifier_keeps_a_card_on_an_overlay_row_as_picture():
    """Without the plan's names a card on V5 files as a semantic
    visual; with them it stays picture - and the picture extent reads
    past the footage end to the card's end."""

    class _Clip:
        def __init__(self, index, name, track_name, start, end):
            self.track_type = "video"
            self.track_index = index
            self.track_name = track_name
            self.timeline_start, self.timeline_end = start, end
            self.duration = end - start
            self.source_in_frame, self.source_out_frame = 0, 90
            self.source_file = name
            self.speaker = None
            self.name, self.resolve_item_id = name.rsplit("/", 1)[-1], ""

    class _Snapshot:
        fps = FPS
        timeline_name = "Reel 13"
        start_frame, end_frame = 0, 551
        width, height = 1080, 1920
        clips = [_Clip(1, "/m/a.MXF", "Akshita", 0.0, 20.0),
                 _Clip(5, CARD_FILE, "Semantic", 20.0, 23.0)]

    card = _declared_card()
    card = _PlanCard(render_name=card.render_name,
                     placement=card.placement,
                     reel_start_frame=int(round(20.0 * FPS)),
                     duration_frames=int(round(3.0 * FPS)),
                     element=card.element)
    plain = _snapshot_to_reel_timeline(_Snapshot())
    assert len(plain.video_items) == 1, \
        "without the plan's names the card files as decoration"
    named = _snapshot_to_reel_timeline(_Snapshot(), cards=[card])
    assert len(named.video_items) == 2
    assert named.semantic_items == ()
    assert named.picture_frames == int(round(23.0 * FPS))


def test_f12_grades_a_card_on_its_declared_row_against_the_frame():
    """A card that moved off V1 must still pass through F12 - not
    leave it silently."""
    identity = {"ZoomX": 1.0, "ZoomY": 1.0, "Pan": 0.0, "Tilt": 0.0,
                "CropLeft": 0.0, "CropRight": 0.0,
                "CropTop": 0.0, "CropBottom": 0.0}
    item = _placed(5, "Semantic", transform=identity)
    assert check_delivered_framing(
        "Reel 13", [item], 1080, 1920, source_sizes={},
        declared_intent=0.0, cards=[_declared_card()]) == []


# ── E. The digest ──────────────────────────────────────────────────

from library.tools import reel_rebuild_need as need  # noqa: E402


def _derivation(**overrides):
    body = dict(
        reel_number=1,
        engine_code="e" * 64,
        project_wide="p" * 64,
        plan_content_hash="l" * 64,
        transcript_hash="t" * 64,
        master_digest="m" * 64,
        ranges=[(1.0, 2.0)],
        placements_list=[],
        cards=[],
        caption_segments=[],
        explainer_segments=[],
        semantic_segments=[],
        overlay_placements=[],
        motion_record={},
        ending=None,
        look=None,
        grade_cdl=None,
        grade_look=None,
        power_grade=None,
    )
    body.update(overrides)
    return need.derivation_digest(**body)


def _digest_card():
    return type("C", (), {"placement": "tail",
                          "render_name": "logo_reveal",
                          "reel_start_frame": 480,
                          "duration_frames": 71})()


def test_a_moved_card_row_changes_the_derivation():
    """A reel whose closing card moves rows IS stale: the digest must
    say so, or the fix never reaches the sixteen reels carrying the
    logo on V1."""
    before = _derivation(cards=[_digest_card()])
    after = _derivation(cards=[_digest_card()],
                        card_row_role="semantic")
    assert before != after


# ── E2. The neighbouring row-name gap, same class ──────────────────

from library.tools.reel_build import reel_angles  # noqa: E402
from library.tools.timeline_ingest import TimelineClip  # noqa: E402


def test_a_motion_graphics_row_is_a_layer_not_a_camera():
    """`speaker_identity.TRACK_NAME` is "Motion Graphics", and the reel
    builder tells angles from decoration by the layout owner's names -
    which omitted it. A master carrying lower thirds read as a third
    camera; it must not."""
    def _clip(index, name):
        return TimelineClip(
            resolve_item_id=f"mg-{index}", track_type="video",
            track_index=index, track_name=name, speaker=None,
            source_file="/m/mg.mov", source_in=0.0, source_out=1.0,
            source_in_frame=0, source_out_frame=24, source_frames=100,
            timeline_start=0.0, timeline_end=1.0, name="mg")
    clips = [_clip(1, "Akshita"), _clip(2, "Craig"),
             _clip(5, "Motion Graphics")]
    assert [a["key"] for a in reel_angles(clips)] == ["1", "2"]


# --------------------------------------------------------------------------
# From test_safe_zone_policy.py
#
# The project's safe-zone policy reaches what lays a reel out.
#
# The captain, 2026-09-25: the safe-area tooling must bound, "load in
# preferences and configs for specific platforms, but also custom rules
# that might be set". Each test names what breaks when that stops being
# true.

def _project_5(tmp_path, block: str) -> str:
    (tmp_path / "project.yaml").write_text(
        "name: t\npipeline:\n  safe_zones:\n" + block, encoding="utf-8")
    return str(tmp_path)


def test_a_platform_and_phone_preference_changes_what_is_kept_clear(tmp_path):
    # Made for TikTok on iPhones only: no LinkedIn rail and no 21:9
    # crop, so the project gets its room back - and the insets every
    # caption and graphic lays out from say so.
    everyone = resolve_safe_area(None)
    folder = _project_5(tmp_path, "    platforms: [tiktok]\n"
                                "    devices: iphone\n")
    mine = resolve_safe_area(folder)
    assert mine.centered_usable_width > everyone.centered_usable_width
    assert szp.project_layout(folder).visible()[0] < \
        szp.resolve_layout().visible()[0]


def test_a_custom_rule_reaches_the_layout_consumers_read(tmp_path):
    folder = _project_5(
        tmp_path,
        "    keep_out:\n"
        "      - {rect: [0, 1300, 1080, 1500], reason: lower-third bar}\n")
    layout = szp.project_layout(folder)
    hits = layout.intrusions((400, 1350, 600, 1400))
    assert [h["platform"] for h in hits] == [szp.CUSTOM]
    assert hits[0]["ui"] == "lower-third bar"
    # A caption on those rows has no centred width at all.
    assert layout.centred_clear_width(1350, 1400)[0] == 0


def test_a_margin_grows_every_app_element_but_not_the_crop(tmp_path):
    plain = szp.resolve_layout()
    padded = szp.project_layout(_project_5(tmp_path, "    margin: 12\n"))
    assert padded.visible() == plain.visible()
    assert padded.insets()["right"] == plain.insets()["right"] + 12


UNHONOURABLE = [
    ("    platform: [tiktok]\n", "nothing reads"),
    ("    platforms: [myspace]\n", "myspace"),
    ("    devices: [Nokia 3310]\n", "Nokia"),
    ("    ignore: [tiktok.left]\n", "side strip"),
    ("    keep_out:\n      - {rect: [0, 0, 10, 10]}\n", "no reason"),
    ("    keep_out:\n      - {rect: [10, 0, 5, 10], reason: x}\n",
     "x1 > x0"),
]


def test_a_rule_that_cannot_be_honoured_is_refused_not_dropped(tmp_path):
    for block, says in UNHONOURABLE:
        with pytest.raises(szp.SafeZonePolicyError, match=says):
            szp.project_policy(_project_5(tmp_path, block))


def test_fit_scale_shrinks_a_picture_inside_what_every_phone_shows():
    layout = szp.resolve_layout()
    picture = (6, 480, 1069, 1880)  # the geo-podcast finals, 2026-09-25
    k = layout.fit_scale(picture)
    assert k < 1
    cx = (picture[0] + picture[2]) / 2
    x0 = cx + (picture[0] - cx) * k
    x1 = cx + (picture[2] - cx) * k
    vx0, _vy0, vx1, _vy1 = layout.visible()
    assert vx0 <= x0 and x1 <= vx1
    # ...and no smaller than it has to be.
    assert min(x0 - vx0, vx1 - x1) < 1


# --------------------------------------------------------------------------
# From test_caption_safe_area.py
#
# The safe area, and the caption fitter that had never run.
#
# One enumeration (`library/tools/safe_area.py`), four consumers, and a
# fitter that actually runs: no caption LINE is drawn wider than the usable
# width. History: docs/evidence/caption_safe_area.md.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_01_plan_subtitles.step import (  # noqa: E402
    MAX_CAPTION_LINES,
    build_caption_fitter,
    generate_subtitles,
    split_into_groups,
)
from library.tools.safe_area import (  # noqa: E402
    SAFE_AREAS,
    UnknownSafeArea,
    safe_area_for_format,
    safe_area_profile,
)
from library.tools.subtitle_style import (  # noqa: E402
    CAPTION_LIFT_PX,
    resolve_subtitle_style,
)

FRAME_W_2 = 1080
FRAME_H_2 = 1920

# The line the audit measured the clipping on: `sub_block_5.mov` of the
# shipped export carried "announcement" as a single card, and that word
# ran off both edges of the frame.
CLIPPING_LINE = (
    "today I have a big announcement to make about the brand template "
    "and it will change everything"
)


def _spine(text):
    """A minimal spine carrying one speech block of `text`."""
    words, t = [], 0.0
    for word in text.split():
        words.append({"word": word, "source_start": round(t, 3),
                      "source_end": round(t + 0.30, 3)})
        t += 0.42
    return {"structure": [{
        "block_type": "speech",
        "position": 1,
        "timeline_start": 0.0,
        "timeline_end": words[-1]["source_end"],
        "source_start": 0.0,
        "source_end": words[-1]["source_end"],
        "clip_id": "clip_001",
        "alignment_method": "whisperx",
        "word_timestamps": words,
        "content": {"text": text},
    }]}


# ── The enumeration ──

def test_vertical_profile_keeps_captions_off_every_apps_ui():
    """The captain, 2026-09-25: the published map (top 120, bottom 320,
    right 120) put the longest caption lines under the apps' action
    rails. The profile is DERIVED from the measured zones, so a centred
    caption as wide as it allows, on the row it hangs from, touches no
    app element on any modelled phone."""
    from library.tools.safe_zone_policy import resolve_layout

    insets = safe_area_for_format("vertical_1080x1920")
    layout = resolve_layout()
    width = insets.centered_usable_width
    bottom = FRAME_H_2 - insets.bottom
    caption = ((1080 - width) // 2, bottom - 240,
               (1080 + width) // 2, bottom)
    assert layout.intrusions(caption) == []
    # ...and it is no narrower than it has to be: one pixel wider on
    # each side meets a rail.
    wider = (caption[0] - 1, caption[1], caption[2] + 1, caption[3])
    assert layout.intrusions(wider)


def test_every_format_has_a_fractional_profile_and_unknown_raises():
    """The same product in more pixels gets the same profile, scaled."""
    hd = safe_area_for_format("vertical_1080x1920")
    uhd = safe_area_for_format("vertical_2160x3840")
    assert uhd.profile == hd.profile
    for edge in ("top", "right", "bottom", "left"):
        assert abs(getattr(uhd, edge) - 2 * getattr(hd, edge)) <= 1
    # Every delivery format has a profile (one without has unplaced
    # captions), and an unknown one raises rather than defaulting.
    from library.tools.delivery_format import DELIVERY_FORMATS
    assert set(DELIVERY_FORMATS) == set(SAFE_AREAS)
    with pytest.raises(UnknownSafeArea):
        safe_area_profile("vertical_9000x16000")


# ── The fitter actually runs ──

def test_fitter_measures_the_real_font_at_the_rendered_weight():
    """`fits_fn` used to be None on every run. Prove it is not."""
    style = resolve_subtitle_style({}, {})
    fitter = build_caption_fitter(style, resolve_safe_area())
    assert fitter.measured, (
        "the caption fitter fell back to an estimate; it should have "
        f"opened the bundled font for {style['fontFamily']!r}")
    assert fitter.font_path.endswith("Montserrat-Variable.ttf")
    assert fitter.font_size == style["fontSize"]
    # Montserrat-Variable defaults to Thin (100); the render draws 800,
    # so the weight axis must be set or it measures a face nobody renders.
    sa = resolve_safe_area()
    thin = build_caption_fitter(
        {"fontFamily": "Montserrat", "fontSize": 160, "fontWeight": 100}, sa)
    bold = build_caption_fitter(
        {"fontFamily": "Montserrat", "fontSize": 160, "fontWeight": 800}, sa)
    assert bold.text_width("announcement") > thin.text_width("announcement")


def test_the_step_runs_the_fitter_on_a_real_invocation():
    """End to end through `main()`, the way the runner calls it."""
    payload = {"audio_spine": _spine(CLIPPING_LINE), "brand_effect": {},
               "brand_style": {}}
    proc = subprocess.run(
        [sys.executable,
         os.path.join(PROJECT_ROOT, "library", "steps",
                      "step_4_01_plan_subtitles", "step.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        encoding="utf-8", cwd=PROJECT_ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    plan = json.loads(proc.stdout)["subtitle_plan"]
    # The platform's own bottom inset is 320 (`safe_area.py`); the
    # caption row sits CAPTION_LIFT_PX above it and these are the
    # CAPTION props, so the lift is the number that reaches the
    # renderer. Written against the constant rather than today's value:
    # it was 11 before #979 corrected it to 1, and a test spelling the
    # number fails on the next correction instead of grading it.
    platform = safe_area_for_format("vertical_1080x1920")
    assert plan["style"]["safeArea"]["bottom"] == (platform.bottom
                                                   + CAPTION_LIFT_PX)
    assert (plan["style"]["captionMaxWidth"]
            == platform.centered_usable_width)
    # The fitter's own report. It only ever prints when it measured a
    # card too wide for the frame, which is what the grouper could not
    # see before.
    assert "usable width" in proc.stderr, proc.stderr


# ── The regression: no card is emitted wider than the caption box ──

def _wrapped_lines(fitter, text):
    """The lines the caption box wraps `text` onto, greedily."""
    lines, current = [], []
    for word in text.split():
        if current and fitter.text_width(
                " ".join(current + [word])) > fitter.usable_width:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def test_no_caption_line_or_card_overflows_the_caption_box():
    """The defect, pinned: no LINE runs off the frame (the box wraps, so
    the bound is per line), no card exceeds the box's lines, and one
    unbreakable word scales its card down. Before: columns 0-1079."""
    style = resolve_subtitle_style({}, {})
    safe_area = resolve_safe_area()
    fitter = build_caption_fitter(style, safe_area)

    entries = generate_subtitles(
        _spine(CLIPPING_LINE), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]
    assert entries

    outline = style["outlineWidth"]
    left_edge, right_edge = FRAME_W_2, 0
    for entry in entries:
        scale = entry["fit_scale"]
        assert 0 < scale <= 1.0
        for line in _wrapped_lines(fitter, entry["text"]):
            ink = fitter.text_width(line) * scale + 2 * outline
            assert ink <= safe_area.centered_usable_width, (
                f"card {entry['id']} line {line!r} draws {ink:.0f}px of ink "
                f"in a {safe_area.centered_usable_width}px usable width")
            left_edge = min(left_edge, (FRAME_W_2 - ink) / 2.0)
            right_edge = max(right_edge, (FRAME_W_2 + ink) / 2.0)

    assert left_edge >= safe_area.left, (
        f"caption ink starts at column {left_edge:.0f}, inside the "
        f"{safe_area.left}px left safe margin")
    assert right_edge <= FRAME_W_2 - safe_area.right, (
        f"caption ink ends at column {right_edge:.0f}, inside the "
        f"{safe_area.right}px right safe margin")

    # A card is a phrase, not a paragraph: the grouper fits the BOX, up to
    # MAX_CAPTION_LINES.
    for entry in entries:
        assert fitter.line_count(entry["text"]) <= MAX_CAPTION_LINES, (
            f"card {entry['id']} {entry['text']!r} wraps onto "
            f"{fitter.line_count(entry['text'])} lines")

    # An unbreakable word is shrunk rather than clipped: the card scales,
    # the style does not.
    assert fitter.word_width("announcement") > fitter.usable_width, (
        "the fixture word no longer overflows; pick one that does")

    card = next(e for e in entries if "announcement" in e["text"])
    assert card["fit_scale"] < 1.0
    assert (fitter.word_width("announcement") * card["fit_scale"]
            <= fitter.usable_width)
    # The style is untouched - only this card is drawn smaller.
    assert style["fontSize"] == resolve_subtitle_style({}, {})["fontSize"]


# ── All four consumers read the one enumeration ──

def test_the_style_and_the_grouper_read_the_one_safe_area():
    props = resolve_subtitle_style({}, {})
    platform = safe_area_for_format("vertical_1080x1920").as_props()
    # Every inset is the platform's, EXCEPT the bottom: the caption row
    # sits CAPTION_LIFT_PX above it, and that lift is the whole reason
    # these props are not the platform's own
    # (`library/tools/subtitle_style.py`).
    assert props["safeArea"] == {**platform,
                                 "bottom": platform["bottom"]
                                 + CAPTION_LIFT_PX}
    # `captionMaxWidth` derives from the UNLIFTED left/right insets.
    assert props["captionMaxWidth"] == 1080 - 2 * max(platform["left"],
                                                      platform["right"])
    # The grouper reads the same width - else captions lift clear of the
    # platform UI and still clip left and right.
    safe_area = resolve_safe_area()
    fitter = build_caption_fitter(props, safe_area)
    assert fitter.usable_width == (
        safe_area.centered_usable_width - 2 * props["outlineWidth"])


# ── The grouper does not leave runts ──
#
# The balanced split. A greedy fill packs each card to the width limit and
# leaves the remainder as the next card, and a card is on screen only
# until the NEXT card's first word - so the remainder flashes. On project
# 001 that produced 76 cards under half a second out of 96. Nothing
# downstream can repair it: `enforce_min_duration` extends a card while
# preserving the next card's spoken start, then overlap repair trims or
# merges the pair. A greedy card's neighbour starts immediately.

# The real opening line of project 001, at its real delivery speed.
# Greedy grouping leaves "me." alone for 0.24s - 7 frames at 30fps.
FAST_LINE = "i can feel the silent judgment of the people behind me."


def _fast_spine(text, per_word=0.24, gap=0.0):
    """A speech block delivered fast enough to make runt cards."""
    words, t = [], 0.0
    for word in text.split():
        words.append({"word": word, "source_start": round(t, 3),
                      "source_end": round(t + per_word, 3)})
        t += per_word + gap
    return {"structure": [{
        "block_type": "speech",
        "position": 1,
        "timeline_start": 0.0,
        "timeline_end": words[-1]["source_end"],
        "source_start": 0.0,
        "source_end": words[-1]["source_end"],
        "clip_id": "clip_001",
        "alignment_method": "whisperx",
        "word_timestamps": words,
        "content": {"text": text},
    }]}


def _durations(entries):
    return [e["timeline_end"] - e["timeline_start"] for e in entries]


def test_the_split_is_balanced_not_greedy():
    """No card flashes where a different split of the same words would not.

    The words all fit the box in more than one way; the grouper has to
    pick the partition that keeps every card on screen, not the one that
    fills each card first.
    """
    entries = generate_subtitles(
        _fast_spine(FAST_LINE), caption_case="lowercase",
    )["subtitle_plan"]["subtitle_entries"]
    assert entries
    flashing = [(e["text"], d) for e, d in zip(entries, _durations(entries))
                if d < 0.5]
    assert not flashing, f"cards under 0.5s: {flashing}"


def test_a_single_word_is_always_a_legal_card():
    """The base case that guarantees a partition exists.

    One word wider than the box cannot be wrapped away - `fit_scale`
    draws that card smaller - so the split must never be unable to place
    it.
    """
    fitter = build_caption_fitter(
        resolve_subtitle_style({}, {}), resolve_safe_area())
    assert fitter.word_width("announcement") > fitter.usable_width, (
        "the fixture word no longer overflows; pick one that does")
    groups = split_into_groups(
        [{"word": "announcement", "start": 0.0, "end": 0.8}],
        fits_fn=fitter.fits_in_box, display_until=0.8)
    assert [g["text"] for g in groups] == ["announcement"]


# ── The cascade drops no word (moved from test_subtitle_sync.py) ──

def test_subtitle_cascade_drops_no_word():
    """The tail of a block ("single day.") used to be truncated by the
    cascade and clamping; every spoken word must reach a card."""
    audio_spine = {
        "structure": [
            {
                "position": 1,
                "block_type": "speech",
                "timeline_start": 20.87,
                "timeline_end": 24.41,
                "source_start": 63.135,
                "source_end": 66.675,
                "content": {"text": "and so my very, very small announcement is that i just want to post every single day."},
                "word_timestamps": [
                    {"word": "and", "source_start": 63.0, "source_end": 63.1},
                    {"word": "so", "source_start": 63.1, "source_end": 63.2},
                    {"word": "my", "source_start": 63.2, "source_end": 63.3},
                    {"word": "very,", "source_start": 63.3, "source_end": 63.4},
                    {"word": "very", "source_start": 63.4, "source_end": 63.5},
                    {"word": "small", "source_start": 63.5, "source_end": 63.6},
                    {"word": "announcement", "source_start": 63.6, "source_end": 64.0},
                    {"word": "is", "source_start": 64.0, "source_end": 64.1},
                    {"word": "that", "source_start": 64.1, "source_end": 64.2},
                    {"word": "i", "source_start": 64.2, "source_end": 64.3},
                    {"word": "just", "source_start": 64.3, "source_end": 64.4},
                    {"word": "want", "source_start": 64.4, "source_end": 64.5},
                    {"word": "to", "source_start": 64.5, "source_end": 65.0},
                    {"word": "post", "source_start": 65.0, "source_end": 65.5},
                    {"word": "every", "source_start": 65.5, "source_end": 66.0},
                    {"word": "single", "source_start": 66.395, "source_end": 66.535},
                    {"word": "day.", "source_start": 66.595, "source_end": 66.675},
                ]
            }
        ]
    }

    result = generate_subtitles(audio_spine, caption_case="lowercase", brand_effect={}, brand_style={})
    entries = result["subtitle_plan"]["subtitle_entries"]

    # Assert that all words made it through the cascade and clamping logic.
    # Previous behaviour truncated the tail of the block ("single day.").
    #
    # This asserts the WORDS survive, not which card each lands on. It used
    # to assert the literal card "single day.", which was the grouping a
    # `max_chars = 18` fallback produced; captions are grouped by measured
    # width now (library/tools/safe_area.py, and step 4.01's CaptionFitter),
    # and the split is balanced rather than greedy, so which card any given
    # word lands on is not stable and is not the invariant here. The
    # invariant the test is named for is that nothing is dropped.
    texts = [e["text"] for e in entries]
    spoken = " ".join(w["word"] for w in audio_spine["structure"][0]
                      ["word_timestamps"]).lower()
    assert " ".join(texts) == spoken, (
        f"words lost or reordered.\n  got: {texts}\n  want: {spoken}")
    # The tail of the block specifically, because that is what used to be
    # truncated - on whichever card the split put them.
    assert texts[-1].endswith("single day."), texts


# --------------------------------------------------------------------------
# From test_platform_safe_zones.py
#
# The safe-zone guides draw the table, and a guide can never render.
#
# Each test names the defect it catches:
#
# * a checked-in overlay PNG that no longer draws
#   `platform_safe_zones.PLATFORMS` - the guide an editor trusts would
#   mark the wrong pixels;
# * a post header drawn with a hook nobody wrote (AGENTS.md 10.5);
# * a guide item Resolve reads back ENABLED - the guide would render into
#   the export (measured: the ROW disable returned True and rendered).

def test_checked_in_overlay_draws_exactly_the_table():
    for name in psz.OVERLAY_NAMES:
        path = psz.overlay_path(name)
        assert os.path.isfile(path), f"regenerate: {path}"
        with Image.open(path) as image:
            assert image.size == psz.REFERENCE_SIZE
            alpha = np.asarray(image.convert("RGBA"))[:, :, 3] > 0
        expected = psz.covered_mask(name, *psz.REFERENCE_SIZE)
        # Every covered pixel is washed, and nothing outside the bands is
        # touched - the lines and legend are drawn inside the bands.
        assert np.array_equal(alpha, expected)


def test_intrusions_names_the_zone_a_box_sits_in():
    # A box across the top 100 rows sits under every app's status bar,
    # and under TikTok's tabs where a short Android status bar lifts them.
    hits = psz.intrusions((300, 20, 700, 100))
    assert {h["platform"] for h in hits} == set(psz.PLATFORMS)
    assert {h["band"] for h in hits} == {"camera", "status-time", "tabs"}
    # The combined safe box clears every zone.
    assert psz.intrusions((130, 300, 770, 830)) == []
    # The captain, 2026-09-25: the Shorts search icon and menu were drawn
    # as a band across the whole top, marking everything left of them
    # unsafe. Each is its own box, so the row beside them is clear...
    assert psz.intrusions((100, 180, 780, 240), "youtube_shorts") == []
    # ...and the icon itself is still covered.
    hits = psz.intrusions((780, 180, 900, 240), "youtube_shorts")
    assert [h["band"] for h in hits] == ["search"]


def test_the_model_lays_the_screenshot_out_where_it_was_measured():
    # Laid out on the phone the screenshots came from, every element
    # lands exactly where the screenshot's own cover mapping puts it. A
    # pin read off the wrong edge, or an inset applied twice, moves a
    # box here before it moves one on a phone nobody has captured.
    for key in sorted(psz.CAPTURES):
        capture = psz.CAPTURES[key]
        phone = psz.DEVICE_BY_NAME[psz.MEASURED_DEVICE]
        s = capture.region / 1920
        ox = (1080 * s - psz.SCREENSHOT[0]) / 2
        laid = {z.name: z.rect for z in psz.zones_on(key, phone)}
        for element in capture.elements:
            x0, y0, x1, y1 = element.box
            want = (max(0, round((x0 - psz.PAD + ox) / s)),
                    max(0, round((y0 - psz.PAD) / s)),
                    min(1080, round((x1 + psz.PAD + ox) / s)),
                    min(1920, round((y1 + psz.PAD) / s)))
            got = laid[element.name]
            assert all(abs(a - b) <= 1 for a, b in zip(got, want)), (
                element.name, got, want)


def _project_6(tmp_path, avatar):
    (tmp_path / "project.yaml").write_text(
        "effect:\n  post_header:\n"
        f"    avatar: {avatar}\n    name: Acme\n    handle: acme\n"
        "    verified: true\n    top: 0.02\n    name_size: 30\n"
        "    handle_size: 26\n    hook_size: 30\n", encoding="utf-8")
    return str(tmp_path)


def test_a_reel_with_no_or_an_empty_hook_gets_no_header(tmp_path):
    from library.tools import reel_post_header as rph

    avatar = tmp_path / "a.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(avatar)
    folder = _project_6(tmp_path, avatar)

    def never(*_a, **_k):
        raise AssertionError("rendered a header nobody wrote a hook for")

    plan = rph.plan_for_reel("Reel 01", 1, [(0, 48)], 24.0, 1080, 1920,
                             folder, render=never, draw_gain=1.0)
    assert plan.basis == rph.NO_HOOK_WRITTEN
    assert plan.segments == []

    # An empty hook is refused, not drawn.
    (tmp_path / "external").mkdir()
    (tmp_path / "external" / rph.HOOKS_FILE).write_text(
        json.dumps({"hooks": {"1": {"hook": "  "}}}), encoding="utf-8")
    with pytest.raises(rph.PostHeaderError):
        rph.hook_for(folder, 1)


def test_the_header_is_a_tight_canvas_placed_where_it_laid_out(tmp_path):
    # The captain, 2026-09-25: the header was placed FULL FRAME, so
    # moving it meant re-rendering. It is cut to its ink and placed by
    # Pan/Tilt - and that Pan/Tilt must draw the canvas exactly where
    # the full-frame layout put the ink, or the header jumps on the
    # swap.
    from library.tools import reel_post_header as rph
    from library.tools.tight_box import MIN_CANVAS_HEIGHT, ink_screen_box

    still = tmp_path / "post_header_x.png"
    image = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    image.paste((255, 255, 255, 255), (130, 275, 951, 461))
    image.save(still)
    tight, canvas = rph.tight_still(str(still), (1080, 1920))
    with Image.open(tight) as cut:
        size = cut.size
    assert size == (canvas[2] - canvas[0], canvas[3] - canvas[1])
    assert size[0] < 1080 and size[1] >= MIN_CANVAS_HEIGHT
    assert size[0] % 2 == 0 and size[1] % 2 == 0
    for draw_gain in (1.0, 2.0):
        placement = rph.placement_for(canvas, (1080, 1920), draw_gain)
        ink = rph.ink_box(tight)
        drawn = ink_screen_box(size[0], size[1], placement, ink, 1080, 1920,
                               draw_gain=draw_gain)
        assert all(abs(a - b) < 0.5 for a, b in zip(drawn, (130, 275, 951, 461)))


class _Item:
    """A placed item whose switch-off claims success and changes nothing."""

    def SetClipEnabled(self, value):
        return True

    def GetClipEnabled(self):
        return True


class _Timeline:
    """Enough of Resolve's Timeline to place a guide, with a stuck enable."""

    def __init__(self):
        self.tracks = ["A-Roll"]
        self.enabled = {}

    def GetName(self):
        return "Reel 01"

    def GetStartFrame(self):
        return 86400

    def GetEndFrame(self):
        return 86400 + 47

    def GetTrackCount(self, _kind):
        return len(self.tracks)

    def GetTrackName(self, _kind, index):
        return self.tracks[index - 1]

    def SetTrackName(self, _kind, index, name):
        self.tracks[index - 1] = name
        return True

    def AddTrack(self, _kind):
        self.tracks.append("Video")
        return True

    def DeleteTrack(self, _kind, index):
        del self.tracks[index - 1]
        return True

    def GetItemListInTrack(self, _kind, index):
        return [_Item()] if self.tracks[index - 1] == "Safe Zones" else []

    def SetTrackEnable(self, _kind, index, value):
        return True  # claims success, changes nothing

    def GetIsTrackEnabled(self, _kind, index):
        return True


class _Pool:
    def ImportMedia(self, paths):
        return ["item"]

    def AppendToTimeline(self, infos):
        return ["placed"]


class _Project:
    def GetMediaPool(self):
        return _Pool()


def test_a_guide_item_that_reads_back_enabled_is_refused(tmp_path, monkeypatch):
    from library.tools import safe_zone_guide as szg

    monkeypatch.setattr(szg, "guide_movie", lambda *a, **k: "guide.mov")
    timeline = _Timeline()
    with pytest.raises(szg.SafeZoneGuideError, match="ENABLED"):
        szg.place_on_timeline(_Project(), timeline, str(tmp_path),
                              psz.COMBINED, 24.0)
    # ...and the row that would have rendered is gone again.
    assert timeline.tracks == ["A-Roll"]


def _item(start, frames, name="post_header_0123456789", row="Post Header"):
    from library.tools.reel_conformance_verifier import TimelineItem

    return TimelineItem(track_type="video", track_index=9,
                        start_frame=start, end_frame=start + frames,
                        duration_frames=frames, source_start_frame=0,
                        source_end_frame=frames, source_file=name + ".mov",
                        speaker=None, name=name + ".mov", track_name=row)


def test_f25_catches_a_header_placed_somewhere_the_build_did_not_record():
    from library.tools.reel_conformance_verifier import check_post_header

    record = {"segments": [{"timeline_start": 0.0, "total_frames": 480}]}
    assert check_post_header("Reel 01", [_item(0, 480)], [], record,
                             24.0) == []
    # Placed short: the header would vanish partway through the reel.
    assert check_post_header("Reel 01", [_item(0, 120)], [], record, 24.0)
    # Placed with no record at all: out of band.
    assert check_post_header("Reel 01", [_item(0, 480)], [], None, 24.0)
    # A non-guide file on the disabled guide row never renders.
    assert check_post_header(
        "Reel 01", [], [_item(0, 480, name="tv_frame_x", row="Safe Zones")],
        None, 24.0)


# --------------------------------------------------------------------------
# From test_caption_width.py
#
# `caption-width`: only the captions that run under a platform's UI move.
#
# The captain, 2026-09-25: the longest caption lines on his accepted reels
# ran under the apps' action rails. Narrowing re-renders exactly the cards
# whose words wrap wider than the clear width, swaps them in place, and
# leaves every card that already fits alone.

FRAME = (1080, 1920)


def _card(tmp_path, name, text, bottom):
    """A 904x480 tight caption card whose card bottom sits at ``bottom``."""
    words = [{"word": w, "startFrame": 0, "endFrame": 10}
             for w in text.split()]
    props = {
        "subtitles": [{"text": text, "startFrame": 0, "endFrame": 10,
                       "emphasisWords": [], "words": words}],
        "fps": 24.0, "width": 904, "height": 480, "durationInFrames": 12,
        "style": {"fontFamily": "Montserrat", "fontSize": 58,
                  "fontWeight": 800, "outlineWidth": 4, "position": "bottom",
                  "captionMaxWidth": 840,
                  "safeArea": {"top": 444, "right": 24, "bottom": 36,
                               "left": 24}},
        "_source_in_frame": 0, "_source_out_frame": 12,
    }
    mov = tmp_path / f"{name}.mov"
    mov.write_bytes(b"")
    (tmp_path / f"{name}_props.json").write_text(json.dumps(props))
    # Place the canvas so its card bottom (canvas bottom less the 36px
    # pad) lands on `bottom`.
    centre_y = bottom + 36 - 240
    placement = placement_for_box(904, 480, 540, centre_y, *FRAME, 1.0)
    return {"source_file": str(mov), "record_in": 0, "duration": 12,
            "transform": {"Pan": placement["pan"],
                          "Tilt": placement["tilt"]}}


def _tracks(clips):
    return [{"type": "video", "index": 4, "name": "Subtitles",
             "clips": clips}]


def test_only_the_cards_wider_than_the_clear_width_are_swapped(tmp_path):
    wide = _card(tmp_path, "wide",
                 "Everybody wants the visibility nobody measures", 1560)
    narrow = _card(tmp_path, "narrow", "yes.", 1560)
    rendered = []

    def render(folder, pairs, gain):
        rendered.extend(pairs)
        return {p["old_mov"]: p["old_mov"][:-4] + "_narrow.mov"
                for p in pairs}

    spec = cw.touch_spec(str(tmp_path), 1, _tracks([wide, narrow]),
                         timeline_label="Reel 01", draw_gain=1.0,
                         render=render)
    # The band the cards draw on is clear of every zone only inside the
    # platforms' rails, so the width is well under the 840 they wrapped at.
    assert spec["max_width"] < 700
    assert [p["old_mov"] for p in rendered] == [wide["source_file"]]
    style = rendered[0]["props"]["style"]
    assert style["captionMaxWidth"] == spec["max_width"]
    # Rendered through the caption step from FULL-frame props.
    assert (rendered[0]["props"]["width"],
            rendered[0]["props"]["height"]) == FRAME
    assert spec["edits"] == [{"op": "swap_pixels", "row": "V4", "item": 0,
                              "media": wide["source_file"][:-4]
                              + "_narrow.mov"}]


def test_captions_inside_a_zone_are_refused_by_name(tmp_path):
    # A card bottom at 1600 sits in TikTok's caption block on a phone
    # with a thin home bar: no centred width clears that, and the answer
    # is to move the row, not to find a width.
    low = _card(tmp_path, "low", "a caption sitting too low", 1600)
    with pytest.raises(cw.CaptionWidthError, match="tiktok text"):
        cw.touch_spec(str(tmp_path), 1, _tracks([low]),
                      timeline_label="Reel 01", draw_gain=1.0,
                      render=lambda *a: pytest.fail("rendered"))


def test_caption_width_swap_preserves_the_placed_caption_source_trim(
        tmp_path):
    card = _card(tmp_path, "wide", "Everybody wants the visibility nobody measures",
                 1560)
    card.update({"record_out": 12, "left_offset": 12,
                 "right_offset": 100})
    generated = cw.touch_spec(
        str(tmp_path), 1, _tracks([card]), timeline_label="Reel 01",
        draw_gain=1.0,
        render=lambda _folder, pairs, _gain: {
            pair["old_mov"]: pair["old_mov"][:-4] + "_narrow.mov"
            for pair in pairs})

    qualification = touchup.qualify(_tracks([card]), generated)

    assert qualification.insertions[0].left_offset == 12


# --------------------------------------------------------------------------
# From test_caption_tilt_uses_own_height.py
#
# Each graphic's Tilt is derived from ITS OWN height, never a fixed one.
#
# See `docs/evidence/caption_tilt.md` for the incident and invariant.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.resolve_transform import drawn_centre  # noqa: E402


#: The declared row these pin against - the captain's 2026-09-17 Reel 01
#: ruling. Any row would do; this one ties the pin to the live series.
DECLARED_ROW = 0.8451

#: Graphic heights that genuinely differ: the structural 480 canvas and
#: two impostors either side of it, a 1.6x spread and more. If these
#: ever collapse to one size the pin is void, and the first test says so.
OTHER_HEIGHTS = (300, 640)


def _project_7(tmp_path, row):
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
    folder = _project_7(tmp_path, DECLARED_ROW)
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


# --------------------------------------------------------------------------
# From test_caption_band.py
#
# The caption-collision rule `lower_third` was waiting on.
#
# It must REFUSE a real collision and must PASS everything else - a gate
# that fails correct output is no more coverage than one that cannot fail
# (AGENTS.md 10.4).

SPINE = {"structure": [
    {"block_type": "speech", "timeline_start": 0.0, "timeline_end": 10.0},
    {"block_type": "broll", "timeline_start": 10.0, "timeline_end": 20.0},
    {"block_type": "hook", "timeline_start": 20.0, "timeline_end": 24.0},
]}


def _entry(element, anchor, start, duration, copy="Dr Ada Lovelace"):
    entry = {"element": element, "anchor": anchor,
             "start_seconds": start, "duration_seconds": duration,
             "color": "#ffffff"}
    if copy is not None:
        entry["copy"] = [{"text": copy, "type_role": "display"}]
    return entry


def _resolve(entry, bands=frozenset({"bottom"})):
    return resolve_plan([entry], timeline_duration=30, fps=30,
                        caption_bands=bands,
                        captioned_spans=band.captioned_spans(SPINE))


def test_copy_in_the_caption_band_is_refused_only_where_the_band_is_known():
    resolved = _resolve(_entry("lower_third", "bottom_centre", 1, 4))
    assert not resolved.moments
    assert resolved.dropped[0].reason == "collides_with_the_caption_band"
    assert "bottom band" in resolved.dropped[0].detail
    # No guess: a resolver called without the rule behaves as before it.
    resolved = resolve_plan([_entry("lower_third", "bottom_centre", 1, 4)],
                            timeline_duration=30, fps=30)
    assert resolved.moments


def test_an_unreadable_caption_position_raises_rather_than_guessing(monkeypatch):
    """A position nothing can place is a refusal, never a default band.

    Guessing `bottom` here would put a graphic under a caption on every
    project whose style names something this module has not seen.
    """
    monkeypatch.setattr(band, "resolve_subtitle_style",
                        lambda **_: {"position": "sideways"})
    with pytest.raises(band.CaptionBandError):
        band.occupied_bands()


# ── Two graphics drawn through each other ────────────────────────────
#
# A different collision from the caption one, found the same way: by
# compositing over a real reel frame rather than rendering one element
# at a time. `library/tools/motion_graphics_plan.overlapping_pairs`.

from library.tools.motion_graphics_plan import overlapping_pairs  # noqa: E402


def _moment(element, anchor, start=0, frames=90, row=0):
    return {"element": element, "anchor": anchor, "row": row,
            "startFrame": start, "durationFrames": frames}


def test_graphics_drawn_through_each_other_are_reported():
    """A full-width centre collides with a corner in its own band;
    chrome (`persist`, FOR holding under the piece) is reported as
    chrome; and two cards at one anchor in one row at one moment are one
    layout slot occupied twice - the Reel 06 overlap of 2026-09-12 the
    detector built for it returned [] on."""
    pairs = overlapping_pairs([
        _moment("title_lockup", "top_centre"),
        _moment("context_stamp", "top_left"),
    ])
    assert len(pairs) == 1
    assert set(pairs[0]["elements"]) == {"title_lockup", "context_stamp"}
    assert pairs[0]["involves_chrome"] is False

    pairs = overlapping_pairs([
        _moment("title_lockup", "top_centre"),
        _moment("channel_bug", "top_right"),
    ])
    assert len(pairs) == 1 and pairs[0]["involves_chrome"] is True

    pairs = overlapping_pairs([
        _moment("lower_third", "bottom_left", start=0, frames=84),
        _moment("lower_third", "bottom_left", start=77, frames=84),
    ])
    assert len(pairs) == 1
    assert pairs[0]["anchors"] == ["bottom_left", "bottom_left"]
    assert pairs[0]["frames"] == [77, 84]
    assert "row 0" in pairs[0]["why"]
