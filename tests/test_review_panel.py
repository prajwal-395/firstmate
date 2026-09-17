"""The drawn review listing, and the payload it refuses to draw without.

The captain, 2026-09-17, on a blue clip marker placed on the graphic at
frame 297 of Reel 30: *"this graphic looks bad, can we use like a mockup
of actual google reviews?"* - a verdict on what the `context_stamp` drew
(a label naming the reviews) and, beside it, the form he wants instead.

`review_panel` is that form as a roster element: a listing DRAWN from a
stated payload, where `website_panel` composites a capture somebody took.
What the engine supplies is the card, the rows and the stars; what it
never supplies is a word, a number or a colour on it - those are the
declaration's, and an entry arriving without them is dropped by name
rather than rendered blank (AGENTS.md 10.5).
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import mg_tight_box as mgt
from library.tools import motion_graphics_plan as mgp
from library.tools import motion_graphics_vocabulary as mgv
from library.tools import review_panel as rp

REMOTION_DIR = os.path.join(PROJECT_ROOT, "remotion-subtitles")
COMPOSITION = os.path.join(
    REMOTION_DIR, "src", "compositions", "MotionGraphics", "index.tsx")

renders_available = pytest.mark.skipif(
    not os.path.isdir(os.path.join(REMOTION_DIR, "node_modules"))
    or shutil.which("npx") is None,
    reason="needs remotion-subtitles/node_modules and npx (CI installs "
           "neither; run locally in the checkout)")

TIMELINE = 30.0
FPS = 24

PALETTE = {
    "surface": "#FFFFFF",
    "ink": "#202124",
    "muted": "#5F6368",
    "star": "#FBBC04",
    "rule": "#E8EAED",
}

ROWS = [
    {"author": "Dana R.", "stars": 5, "when": "2 weeks ago",
     "body": "Walked in on a Tuesday and still got a table."},
    {"author": "Marcus T.", "stars": 4, "when": "a month ago",
     "body": "Found it at the top of the map and it did not disappoint."},
]


def _data(**overrides):
    payload = {"rating": 4.8, "count": 214,
               "rows": [dict(row) for row in ROWS],
               "palette": dict(PALETTE)}
    payload.update(overrides)
    return payload


def _entry(**overrides):
    base = {
        "element": "review_panel",
        "anchor": "top_right",
        "start_seconds": 2.0,
        "duration_seconds": 3.0,
        "copy": {"display": "Maple Street Kitchen",
                 "micro": "Google reviews"},
        "data": _data(),
    }
    base.update(overrides)
    return base


def _resolve(plan):
    return mgp.resolve_plan(plan, timeline_duration=TIMELINE, fps=FPS,
                            palette_roles={})


# ── the vocabulary ───────────────────────────────────────────────────

def test_review_panel_is_in_the_vocabulary_and_drawable():
    assert mgv.canonical_key("review_panel") == "review_panel"
    element = mgv.ELEMENTS_BY_KEY["review_panel"]
    assert element.function == "quote"
    assert element.copy == "required"
    assert {"copy", "data", "type_role"} <= set(element.axes)
    assert element.reachable == mgv.REACHABLE_NOW
    assert element.key in mgp.DRAWABLE


def test_review_panel_declares_no_colour_role_because_it_draws_a_palette():
    """A brand role would fight the thing it is a mockup OF.

    Every other copy element draws in one resolved colour, so
    `resolve_plan` refuses it without one. This element draws five
    surfaces at once and they are the listing's own look, carried in its
    data - so it names the `colour_role` axis nowhere, and the refusal
    that guards it is the payload's instead.
    """
    element = mgv.ELEMENTS_BY_KEY["review_panel"]
    assert "colour_role" not in element.axes
    resolved = _resolve([_entry()])
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]


def test_the_roster_refuses_a_fabricated_record_and_a_live_fetch():
    """The two refusals that are about truth rather than layout."""
    refusals = " ".join(mgv.ELEMENTS_BY_KEY["review_panel"].never).lower()
    assert "fetch" in refusals and "scraped" in refusals
    assert "real named business" in refusals


# ── the payload, and what happens without it ─────────────────────────

def test_a_complete_payload_is_drawable():
    assert rp.unusable_reason(_data()) == ""
    rp.assert_drawable(_data())


@pytest.mark.parametrize("data,expect", [
    (None, "no `data`"),
    ({}, "rating"),
    (_data(rating=None), "rating"),
    (_data(count=None), "count"),
    (_data(rows=[]), "no review"),
    (_data(rows=[{"author": "Dana R."}]), "body"),
    (_data(rows=[{"body": "no one wrote this"}]), "author"),
    (_data(palette=None), "palette"),
    (_data(palette={k: v for k, v in PALETTE.items() if k != "star"}),
     "star"),
])
def test_an_incomplete_payload_says_what_is_missing(data, expect):
    reason = rp.unusable_reason(data)
    assert reason, f"{data!r} was accepted"
    assert expect in reason, reason


def test_every_palette_role_is_refused_when_absent():
    """A role nothing refuses on is a declaration that is not true
    (AGENTS.md 3): each one is drawn, so each one is required."""
    for role in rp.PALETTE_ROLES:
        short = {k: v for k, v in PALETTE.items() if k != role}
        assert role in rp.unusable_reason(_data(palette=short))


def test_the_payload_module_states_no_colour_and_no_copy():
    """It names the roles and fixes none of them."""
    with open(rp.__file__, encoding="utf-8") as handle:
        source = handle.read()
    # The test fixtures above carry hexes; the module must not.
    assert not re.search(r"#[0-9a-fA-F]{6}\b", source), (
        "library/tools/review_panel.py states a colour. It names the "
        "roles a declaration fills and fixes none of them.")


# ── plan resolution ──────────────────────────────────────────────────

def test_a_panel_with_a_payload_resolves_and_carries_it():
    resolved = _resolve([_entry()])
    assert resolved.proposed == 1
    assert not resolved.dropped, [d.as_record() for d in resolved.dropped]
    (moment,) = resolved.moments
    assert moment["element"] == "review_panel"
    assert moment["data"]["rating"] == 4.8
    assert [row["author"] for row in moment["data"]["rows"]] \
        == ["Dana R.", "Marcus T."]
    assert moment["data"]["palette"]["star"] == "#FBBC04"


def test_a_panel_with_no_reviews_is_dropped_by_name():
    resolved = _resolve([_entry(data=_data(rows=[]))])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason == "data_the_element_draws_from_is_absent"
    assert dropped.reason in mgp.DROP_REASONS
    assert "nothing to say" in dropped.detail


def test_a_panel_with_no_palette_is_dropped_rather_than_given_one():
    resolved = _resolve([_entry(data=_data(palette=None))])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason == "data_the_element_draws_from_is_absent"
    assert "palette" in dropped.detail


def test_a_panel_with_no_copy_is_dropped_like_any_copy_element():
    resolved = _resolve([_entry(copy={})])
    assert not resolved.moments
    (dropped,) = resolved.dropped
    assert dropped.reason == "no_copy_for_an_element_that_needs_one"


def test_the_refusal_is_asked_only_of_the_elements_whose_data_is_content():
    """`step_counter` and `comparison_bars` name the data axis and still
    say something without it; the table must not grow to mean "any
    element with a data axis", which would refuse correct output."""
    assert set(mgp.DATA_IS_THE_CONTENT) == {"review_panel"}
    resolved = _resolve([{
        "element": "step_counter", "anchor": "top_centre",
        "start_seconds": 1.0, "duration_seconds": 2.0,
        "color": "#FBF0B8", "copy": {"display": "SET UP"}}])
    assert resolved.moments, [d.as_record() for d in resolved.dropped]


# ── the box the renderer draws into ──────────────────────────────────

def _tsx_review_panel_table() -> dict:
    with open(COMPOSITION, encoding="utf-8") as handle:
        tsx = handle.read()
    block = re.search(r"export const REVIEW_PANEL = \{(.*?)\n\};",
                      tsx, re.S)
    assert block, "the composition no longer exports REVIEW_PANEL"
    return {key: float(value) for key, value in
            re.findall(r"^\s*(\w+):\s*([0-9.]+),", block.group(1), re.M)}


def test_the_python_mirror_and_the_composition_state_one_card():
    """Two spellings of one card is two answers to one question.

    `mg_tight_box` predicts this element's union from its own copy of the
    composition's numbers, and a tight canvas cut from a stale copy
    clips ink. Every key the mirror holds must match the drawing.
    """
    drawn = _tsx_review_panel_table()
    for key, value in mgt.REVIEW_PANEL.items():
        assert key in drawn, (
            f"mg_tight_box mirrors REVIEW_PANEL[{key!r}] and the "
            f"composition no longer states it")
        assert float(drawn[key]) == float(value), (
            f"REVIEW_PANEL[{key!r}] is {drawn[key]} in the composition "
            f"and {value} in mg_tight_box")


def test_the_predicted_size_grows_by_a_whole_row_per_review():
    one = mgt.review_panel_size({"data": _data(rows=ROWS[:1])}, 1.0)
    two = mgt.review_panel_size({"data": _data(rows=ROWS)}, 1.0)
    assert one[0] == two[0] == float(mgt.REVIEW_PANEL["width"])
    assert two[1] > one[1]
    assert two[1] - one[1] == one[1] - mgt.review_panel_size(
        {"data": _data(rows=[])}, 1.0)[1]


def test_a_panel_tightens_instead_of_taking_the_whole_frame():
    resolved = _resolve([_entry()])
    (moment,) = resolved.moments
    safe = {"top": 120, "right": 120, "bottom": 320, "left": 90}
    (segment,) = mgp.plan_segments(
        [moment], fps=FPS, width=1080, height=1920, safe_area=safe)
    box, refusal = mgt.tighten_motion_graphics_props_with_reason(
        segment["props"], timeline_size=(1080, 1920), draw_gain=1.0)
    assert box is not None, refusal
    width, height = mgt.review_panel_size(moment, 1.0)
    assert box.width == mgt._ceil_even(width + 2 * mgt.MG_PAD)
    assert box.height >= height + 2 * mgt.MG_PAD


# ── the frame ────────────────────────────────────────────────────────

def _props(**overrides):
    element = {
        "element": "review_panel", "anchor": "top_right", "row": 0,
        "runs": [{"text": "Maple Street Kitchen", "type_role": "display"},
                 {"text": "Google reviews", "type_role": "micro"}],
        "color": "", "entrance": "cut", "exit": "cut",
        "startFrame": 0, "durationFrames": 24,
        "timelineProgressStart": 0.0, "timelineProgressEnd": 1.0,
        "footprint": None, "emphasis": None, "data": _data(),
    }
    element.update(overrides)
    return {"elements": [element], "fps": FPS, "width": 1080,
            "height": 1920,
            "safeArea": {"top": 120, "right": 120, "bottom": 320,
                         "left": 90},
            "durationInFrames": 24}


def _still(tmp_path, props, name):
    props_path = tmp_path / f"{name}.json"
    props_path.write_text(json.dumps(props), encoding="utf-8")
    out_path = tmp_path / f"{name}.png"
    result = subprocess.run(
        ["npx", "remotion", "still", "MotionGraphics", str(out_path),
         "--frame=12", f"--props={props_path}", "--image-format=png"],
        cwd=REMOTION_DIR, capture_output=True, text=True,
        encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return out_path


@renders_available
def test_the_card_the_predictor_measures_is_the_card_that_draws(tmp_path):
    """The prediction is exact, and this is what proves it.

    Every box of the card carries an explicit height and its bodies are
    clamped to reserved line boxes, so there is no wrap to estimate -
    the drawn ink must land inside the predicted rectangle plus the
    shadow, and its top-right corner must sit at the safe-area corner.
    """
    pytest.importorskip("PIL", reason="needs Pillow to read the frame")
    from PIL import Image

    image = Image.open(_still(tmp_path, _props(), "review_panel"))
    image = image.convert("RGBA")
    bbox = image.split()[3].getbbox()
    assert bbox, "review_panel drew nothing at all"
    width, height = mgt.review_panel_size(
        _props()["elements"][0], 1.0)
    # The card's own corner, before the shadow: top-right at the safe
    # area, and the card no wider or taller than the predictor said.
    x0, y0, x1, y1 = bbox
    assert x1 - x0 <= width + 2 * mgt.MG_PAD
    assert y1 - y0 <= height + 2 * mgt.MG_PAD
    opaque = Image.open(tmp_path / "review_panel.png").convert("RGBA")
    alpha = opaque.split()[3].load()
    solid = [(x, y) for y in range(0, 900) for x in range(0, 1080)
             if alpha[x, y] == 255]
    assert solid, "the card drew no opaque pixel"
    left = min(x for x, _ in solid)
    right = max(x for x, _ in solid)
    top = min(y for _, y in solid)
    assert abs((right + 1) - (1080 - 120)) <= 1, right
    assert abs((right + 1 - left) - width) <= 1, (left, right, width)
    assert abs(top - 120) <= 1, top


@renders_available
def test_a_panel_with_no_rows_draws_nothing_rather_than_an_empty_card(
        tmp_path):
    """The composition's own half of the refusal.

    `resolve_plan` never lets such an entry through, so this is the
    belt on a hand-edited props file in the Remotion studio: a card with
    no reviews is not drawn at all.
    """
    pytest.importorskip("PIL", reason="needs Pillow to read the frame")
    from PIL import Image

    image = Image.open(
        _still(tmp_path, _props(data=_data(rows=[])), "no_rows"))
    assert image.convert("RGBA").split()[3].getbbox() is None


@renders_available
def test_the_rating_is_drawn_in_the_declared_star_colour(tmp_path):
    """The stars are the declaration's colour, drawn - not a glyph.

    Rendered twice with two different star roles: the pixels have to
    move, or the role is not reaching the drawing.
    """
    pytest.importorskip("PIL", reason="needs Pillow to read the frame")
    from PIL import Image

    def star_pixels(colour, name):
        palette = dict(PALETTE, star=colour)
        image = Image.open(_still(
            tmp_path, _props(data=_data(palette=palette)), name)
        ).convert("RGB")
        target = tuple(int(colour[i:i + 2], 16) for i in (1, 3, 5))
        return sum(1 for y in range(0, 900) for x in range(0, 1080)
                   if image.getpixel((x, y)) == target)

    assert star_pixels("#FBBC04", "amber") > 500
    assert star_pixels("#1A73E8", "blue") > 500
