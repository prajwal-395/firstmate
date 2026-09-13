"""A closing element every reel inherits, and it is the project's own file.

The captain, on Reel 09, on the clip `logo_reveal.mov` he laid onto the
timeline by hand, 2026-09-11::

    "this animation here is something i want applied to all of the reels
     being made"

It had no mechanism.  A hand-placed asset belongs to no layer, so every
rebuild of that reel rebuilt without it, and no other reel ever had it.

`full_frame_clip` is the mechanism: a project-supplied finished animation
declared ONCE under `effect.full_frame_elements`, planned against every
reel the project builds - the ones already promoted and the ones nobody
has planned yet - and placed on the declared card row
(`effect.card_row_role`) the way a rendered card is, so the coverage
assertion, the item count and the framing verdict all see it.

Where it sits against the ending freeze is the other half, and it is
stated rather than inferred: `reel_ending.ending_tail_frames`.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest

from library.tools import full_frame_element as ffe
from library.tools import reel_ending

FPS = 24000 / 1001


def _clip(tmp_path, name="logo.mov", width=1080, height=1920,
          seconds=3.0, fps=30):
    """A real movie file, because a clip is admitted on a MEASUREMENT."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not on PATH")
    out = tmp_path / name
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c=red:s={width}x{height}:r={fps}:d={seconds}",
         "-c:v", "prores_ks", "-profile:v", "3", str(out)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    if result.returncode != 0 or not out.exists():
        pytest.skip("ffmpeg cannot encode a fixture here")
    return str(out)


def _declaration(asset, placement="tail"):
    return {"element": "full_frame_clip", "placement": placement,
            "asset": asset, "reason": "captain 2026-09-11 marker"}


# ── The declaration ────────────────────────────────────────────────

def test_a_clip_declaration_is_where_which_and_why():
    declared = ffe.declared_elements(
        {"full_frame_elements": [_declaration("/abs/logo.mov")]})
    assert declared == [{
        "element": "full_frame_clip", "placement": "tail",
        "asset": "/abs/logo.mov", "reason": "captain 2026-09-11 marker"}]


def test_a_clip_with_no_asset_is_refused():
    """A clip IS a file; there is nothing for the engine to draw instead."""
    with pytest.raises(ffe.FullFrameDeclarationError) as raised:
        ffe.declared_elements({"full_frame_elements": [
            {"element": "full_frame_clip", "placement": "tail",
             "reason": "x"}]})
    assert "asset" in str(raised.value)


def test_a_clip_with_no_reason_is_refused():
    """The captain's own words are what any listing reads back - the
    same field `placed_assets.json` requires, for the same reason."""
    with pytest.raises(ffe.FullFrameDeclarationError) as raised:
        ffe.declared_elements({"full_frame_elements": [
            {"element": "full_frame_clip", "placement": "tail",
             "asset": "/abs/logo.mov"}]})
    assert "reason" in str(raised.value)


@pytest.mark.parametrize("field,value", [
    ("runs", [{"text": "hi", "type_role": "display"}]),
    ("duration_seconds", 2.0),
    ("background", "#000000"),
    ("entrance", "fade"),
    ("font_family", "Montserrat"),
    ("image", "mark.png"),
])
def test_a_field_with_no_reader_on_a_clip_is_refused_by_name(field, value):
    """Every pixel of a clip was drawn before the engine saw it, so a
    field about how a frame is DRAWN has no reader.  Refused rather than
    ignored: a project that wrote one believes it reached the picture.

    `duration_seconds` is the load-bearing one - a declared length that
    disagreed with the file would truncate the animation or freeze on
    its last frame, with nothing downstream able to tell.
    """
    declaration = {**_declaration("/abs/logo.mov"), field: value}
    with pytest.raises(ffe.FullFrameDeclarationError) as raised:
        ffe.declared_elements({"full_frame_elements": [declaration]})
    assert field in str(raised.value)


def test_a_clip_is_head_or_tail_and_never_mid_reel():
    for placement in ("middle", "span", ""):
        with pytest.raises(ffe.FullFrameDeclarationError):
            ffe.declared_elements({"full_frame_elements": [
                _declaration("/abs/logo.mov", placement=placement)]})
    for placement in ffe.PLACEMENTS:
        assert ffe.declared_elements({"full_frame_elements": [
            _declaration("/abs/logo.mov",
                         placement=placement)]})[0]["placement"] == placement


# ── Finding the file ───────────────────────────────────────────────

def test_an_absolute_asset_is_taken_as_written(tmp_path):
    """A series keeps one brand animation beside the projects that share
    it; the declaration is the map (AGENTS.md 10.1)."""
    path = _clip(tmp_path)
    assert ffe.resolve_clip_asset(path, str(tmp_path / "project")) == path


def test_a_relative_asset_resolves_against_the_project(tmp_path):
    project = tmp_path / "project"
    (project / "brand_assets").mkdir(parents=True)
    path = _clip(project / "brand_assets", name="logo.mov")
    assert ffe.resolve_clip_asset(
        "brand_assets/logo.mov", str(project)) == path


def test_an_asset_that_is_not_a_file_refuses(tmp_path):
    with pytest.raises(ffe.FullFrameDeclarationError) as raised:
        ffe.resolve_clip_asset("brand_assets/missing.mov", str(tmp_path))
    assert "not a file" in str(raised.value)


# ── Measuring it ───────────────────────────────────────────────────

def test_a_clip_is_admitted_on_a_measurement(tmp_path):
    measured = ffe.measure_clip(_clip(tmp_path))
    assert (measured.width, measured.height) == (1080, 1920)
    assert measured.frame_count == 90
    assert measured.fps == pytest.approx(30.0)
    assert measured.duration_seconds == pytest.approx(3.0, abs=0.05)


def test_a_file_that_is_not_a_picture_refuses(tmp_path):
    not_a_movie = tmp_path / "notes.txt"
    not_a_movie.write_text("not a movie", encoding="utf-8")
    with pytest.raises(ffe.FullFrameDeclarationError):
        ffe.measure_clip(str(not_a_movie))


# ── Planning it against one reel ───────────────────────────────────

def test_a_tail_clip_plans_after_the_body_at_its_own_length(tmp_path):
    """The reel's own frames and the SOURCE's own frames are different
    counts, and both are carried.

    90 frames of 30fps source CONFORM to 71 frames of a 24000/1001
    reel - Resolve truncates 71.928. `round(3.0 * 23.976)` says 72, and
    on the first build of Reel 26 that one frame was an F13 error
    ("runs 71 frames, planned 72"); the captain's own hand placement of
    this animation on Reel 09 is 71 frames too.
    """
    path = _clip(tmp_path)
    cards = ffe.plan_reel_cards(
        ffe.declared_elements({"full_frame_elements": [_declaration(path)]}),
        facts=None, body_frames=600, fps=FPS, project_folder=str(tmp_path), width=1080, height=1920)
    assert len(cards) == 1
    card = cards[0]
    assert card.element == "full_frame_clip"
    assert card.reel_start_frame == 600
    assert card.duration_frames == 71
    assert int(round(3.0 * FPS)) == 72, "the arithmetic that was wrong"
    assert card.source_frames == 90
    # Nothing to render: the project's file IS the picture.
    assert card.rendered_path == path
    assert card.props == {}


def test_a_clip_that_is_not_the_delivery_frame_refuses(tmp_path):
    """Fitting it letterboxes the mark small or crops it, and which of
    those to do is taste the engine may not take (AGENTS.md 10.5, 13)."""
    path = _clip(tmp_path, width=1920, height=1080)
    with pytest.raises(ffe.FullFrameDeclarationError) as raised:
        ffe.plan_reel_cards(
            ffe.declared_elements(
                {"full_frame_elements": [_declaration(path)]}),
            facts=None, body_frames=600, fps=FPS,
            project_folder=str(tmp_path), width=1080, height=1920)
    assert "1920x1080" in str(raised.value)
    assert "1080x1920" in str(raised.value)


def test_the_renderer_passes_a_project_clip_through_untouched(tmp_path):
    """The engine renders a client's asset; it never edits one
    (AGENTS.md 13).  No Remotion runs, and the path does not move."""
    path = _clip(tmp_path)
    cards = ffe.plan_reel_cards(
        ffe.declared_elements({"full_frame_elements": [_declaration(path)]}),
        facts=None, body_frames=600, fps=FPS, project_folder=str(tmp_path), width=1080, height=1920)
    out = ffe.render_reel_cards(cards, "/nonexistent/remotion",
                                str(tmp_path / "renders"))
    assert [c.rendered_path for c in out] == [path]
    assert not (tmp_path / "renders").exists()


# ── Where it sits against the ending ───────────────────────────────

def _ending(tail_hold="freeze"):
    return {"reel": "Reel 26 - x",
            "ends_on": {"anchor_phrase": "alpha beta"},
            "tail_element": "tv_power_tail",
            "tail_hold": tail_hold,
            "reason": "captain marker"}


def test_a_freeze_owns_frames_after_the_body_and_says_how_many():
    """The freeze holds the ending shot's last frame for exactly as long
    as the switch-off needs, so those frames are picture the reel plays
    and anything after the body starts after them."""
    assert reel_ending.ending_tail_frames(_ending()) == (
        reel_ending.tail_room_frames(_ending()))
    assert reel_ending.ending_tail_frames(_ending()) == 19
    # `none` holds nothing.
    assert reel_ending.ending_tail_frames(_ending(tail_hold="none")) == 0
    assert reel_ending.ending_tail_frames(None) == 0


def test_the_tail_clip_starts_after_the_switch_off_not_over_it(tmp_path):
    """THE ORDERING, and it is the whole reason this plumbing exists.

    picture live -> held frame with the switch-off over it -> the clip.

    The switch-off ends at BLACK (gain 0.0, the set off), so the card is
    what the brand shows once the picture is gone. Planned the other way
    the logo would draw over a frame still collapsing to a dot
    underneath it, and the switch-off would finish under the logo.
    """
    from library.tools.reel_build import plan_cards

    path = _clip(tmp_path)
    declarations = ffe.declared_elements(
        {"full_frame_elements": [_declaration(path)]})

    class _Moment:
        number = 26
        timeline_name = "Reel 26 - x"

    ranges = [(0.0, 600 / FPS)]
    body = int(round(600 / FPS * FPS))

    without = plan_cards(_Moment(), {}, ranges, str(tmp_path), FPS,
                         declarations=declarations, width=1080, height=1920)
    with_freeze = plan_cards(_Moment(), {}, ranges, str(tmp_path), FPS,
                             declarations=declarations, ending=_ending(), width=1080, height=1920)
    assert without[0].reel_start_frame == body
    assert with_freeze[0].reel_start_frame == body + 19
    # A declared `none` ending holds nothing, so nothing moves.
    assert plan_cards(_Moment(), {}, ranges, str(tmp_path), FPS,
                      declarations=declarations,
                      ending=_ending(tail_hold="none"),
                      width=1080, height=1920)[0].reel_start_frame == body


def test_a_clip_at_the_reels_own_rate_keeps_every_frame(tmp_path):
    """The conform arithmetic must not cost a same-rate clip a frame to
    float error - which is every card this engine renders itself."""
    path = _clip(tmp_path, name="native.mov", seconds=2.0, fps=24)
    cards = ffe.plan_reel_cards(
        ffe.declared_elements({"full_frame_elements": [_declaration(path)]}),
        facts=None, body_frames=0, fps=24.0, project_folder=str(tmp_path), width=1080, height=1920)
    assert cards[0].source_frames == 48
    assert cards[0].duration_frames == 48


def test_a_project_clip_is_not_promoted_out_from_under_itself(tmp_path):
    """Promotion exists because scratch/ may be thrown away at any
    moment. A project's own brand animation has no such declaration
    over it, and a promoted copy would go stale in silence the next
    time the series re-cuts its logo."""
    from library.tools.reel_placed_assets import promote_cards

    shared = tmp_path / "shared"
    shared.mkdir()
    path = _clip(shared, name="logo.mov")
    project = tmp_path / "project"
    (project / "pipeline_output").mkdir(parents=True)
    cards = ffe.plan_reel_cards(
        ffe.declared_elements({"full_frame_elements": [_declaration(path)]}),
        facts=None, body_frames=600, fps=FPS, project_folder=str(project), width=1080, height=1920)
    assert [c.rendered_path
            for c in promote_cards(cards, str(project))] == [path]


def test_the_roster_records_what_a_clip_is_never_for():
    """An entry that only says what a thing IS teaches a model to reach
    for it everywhere (AGENTS.md 16)."""
    ffe.assert_roster_is_well_formed()
    entry = ffe.ELEMENTS_BY_KEY["full_frame_clip"]
    assert entry.copy == "none"
    assert entry.reachable == ffe.REACHABLE_NOW
    joined = " ".join(entry.never).lower()
    assert "verbatim" in joined
    assert "duration" in joined
