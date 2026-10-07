"""The Magic Mask recipe: the deterministic seed and track step lists.

The recipe is data, so these tests pin the data: the step order the
Resolve Hands plan names (page, palette, mode, click), the anchor
kinds in priority order, the Ren-measured seed point riding the
click unchanged, and the refusals on a mode or direction nothing
can run. What the labels SAY on a real Resolve window is judged by
the live run, not here - a unit test cannot see the palette.
"""

from __future__ import annotations

import pytest

from library.tools import magic_mask
from library.tools.computer_use.scripts import Script
from library.tools.mask_seed import MaskSeed, Point

_FRAME_RANGE = magic_mask.SourceFrameRange(0, 119)


def _seed() -> MaskSeed:
    # The plan's own geometry example as the measured input: source
    # 1920x1080 in a 1440x900 viewer draws 1440x810 with a 45 px
    # letterbox, so source (1132, 449) lands on viewer (1719, 622) -
    # device pixels at backing scale 2. The builder carries the
    # geometry and converts the units; it never recomputes the point.
    return MaskSeed(
        clip_id="talk", frame=48, subject="speaker",
        source=Point(1132.0, 449.0), viewer=Point(1719.0, 622.0),
        object_id="obj_1", sample_index=2, seed_frame=48)


def _script() -> Script:
    return magic_mask.seed_script(
        _seed(), scale_factor=2.0, source_frame_range=_FRAME_RANGE)


def test_seed_script_is_page_library_search_palette_mode_click():
    script = _script()
    assert isinstance(script, Script)
    assert [s.action for s in script.steps] == [
        "press", "menu", "click", "type", "click", "click", "click"]
    assert [s.anchors[0].via for s in script.steps] == [
        "ax_path", "menu", "template", "type", "template", "ocr",
        "position"]
    # The palette step tries the measured toolbar icon first and keeps
    # the revealed Library row (double-click applies) as fallback.
    palette = script.steps[4]
    assert [a.via for a in palette.anchors] == ["template", "ocr"]
    assert (palette.anchors[0].params["template"]
            == "magic-mask-toolbar")
    assert palette.anchors[1].params["clicks"] == 2
    assert "source frames 0..119 inclusive" in script.goal


def test_seed_script_presses_the_color_page_through_the_menu_bar():
    step = _script().steps[0]
    assert step.anchors[0].params["path"] == [
        "Workspace", "Switch to Page", "Color"]
    # The page switch is judged by the pixels changing, not by the
    # press returning: the driver reports the event posted, never the
    # page having moved.
    assert step.expect is not None and step.expect.via == "pixels_changed"


def test_seed_script_opens_the_library_through_the_measured_route():
    """The palette row the apply step double-clicks only exists with
    the Effects Library open, so the library step opens it through the
    shared menu route the resolvefx lane measured - the same constant,
    never a copy. A Library that never opens fails pixels_changed
    instead of clicking on (found live 2026-10-07: the row was absent
    with the node editor showing)."""
    from library.tools.computer_use.scripts import EFFECTS_PANEL_PATH

    step = _script().steps[1]
    assert step.anchors[0].params["path"] == list(EFFECTS_PANEL_PATH)
    assert step.expect is not None and step.expect.via == "pixels_changed"


def test_seed_script_searches_through_the_registered_glass():
    """The Library search glass is Qt-drawn (no AX, no text), so the
    search click rides the registered glass template - the vocabulary's
    designed route for icon controls, named from the registry, never
    a path. A glass that matches nothing fails the step, so typing
    never runs blind (found live 2026-10-07: a mis-aimed glass click
    typed the query into the void and moved the playhead)."""
    from library.tools.computer_use.templates import ICON_TEMPLATES

    step = _script().steps[2]
    params = step.anchors[0].params
    assert params["template"] == "magic-mask-search"
    entry = ICON_TEMPLATES["magic-mask-search"]
    assert 0.0 < entry.threshold < 1.0
    assert step.expect is not None and step.expect.via == "pixels_changed"
    # Library contents paint asynchronously after the panel opens
    # (measured live: same anchor hit once, missed minutes later), so
    # the anchor settle-polls instead of racing the paint.
    assert params["settle_seconds"] == 90.0


def test_seed_script_types_the_query_only_after_focus():
    """Typing reaches the FOCUSED element, so the type step follows
    the glass click and is verified by the query text appearing -
    the filtered row. No text, no call (an empty query resolves to
    nothing, like every other anchor with no parameters)."""
    step = _script().steps[3]
    assert step.action == "type"
    assert step.anchors[0].params["text"] == "Magic Mask"
    assert step.expect is not None and step.expect.via == "text_visible"
    assert step.expect.params["text"] == "Magic Mask"


def test_seed_script_applies_the_row_with_a_double_click():
    """A single click on a Library row only selects it; the palette
    opens when the effect is APPLIED, so the row fallback click
    carries clicks=2 (the toolbar icon takes one). A row that applies
    nothing shows no Person label and fails the step instead of
    seeding unmasked footage."""
    step = _script().steps[4]
    assert [a.via for a in step.anchors] == ["template", "ocr"]
    params = step.anchors[1].params
    assert params["text"] == "Magic Mask"
    assert params["clicks"] == 2
    assert step.expect is not None and step.expect.via == "text_visible"
    assert step.expect.params["text"] == "Person"


def test_seed_script_clicks_window_points_not_device_pixels():
    """The seed's viewer point is device pixels (screenshot space) but
    a position anchor is an absolute window point, so the builder
    divides by the measured backing scale: (1719, 622) at 2x clicks
    (859.5, 311). Carrying the raw point double-clicks on Retina -
    found live 2026-10-07, when every seed-shaped click missed."""
    step = _script().steps[6]
    params = step.anchors[0].params
    assert (params["x"], params["y"]) == (859.5, 311.0)
    # A position anchor says why it is a bare coordinate: whose seed.
    assert "obj_1" in params["why"]
    # The seed stroke must draw: a background click that silently
    # changes nothing fails the step instead of reporting ok.
    assert step.expect is not None and step.expect.via == "pixels_changed"


def test_seed_script_refuses_a_scale_it_cannot_click_with():
    """A missing or non-positive backing scale is a caller bug, not a
    1.0 default: defaulting would silently double-click on Retina."""
    for bad in (0.0, -2.0):
        with pytest.raises(ValueError, match="bad backing scale"):
            magic_mask.seed_script(
                _seed(), scale_factor=bad,
                source_frame_range=_FRAME_RANGE)


def test_seed_script_refuses_a_mode_the_api_refuses():
    with pytest.raises(ValueError, match="bad Magic Mask mode"):
        magic_mask.seed_script(
            _seed(), mode="SLOW", scale_factor=2.0,
            source_frame_range=_FRAME_RANGE)


def test_open_palette_seed_skips_reveal_apply_and_mode():
    """When the palette is already open with no mask, the Library,
    search, type and apply steps would re-apply the effect (a second
    instance on shared footage), and the open palette renders no
    Person control to click - so the warm route is page, click only.
    The seed expect doubles as the palette-open proof: a viewer click
    with no mask tool behind it paints nothing and fails here
    instead of seeding blind."""
    script = magic_mask.seed_open_palette_script(
        _seed(), scale_factor=2.0, source_frame_range=_FRAME_RANGE)
    assert isinstance(script, Script)
    assert [s.action for s in script.steps] == ["press", "click"]
    assert [s.anchors[0].via for s in script.steps] == [
        "ax_path", "position"]
    seed = script.steps[1]
    assert (seed.anchors[0].params["x"],
            seed.anchors[0].params["y"]) == (859.5, 311.0)
    assert seed.expect is not None and seed.expect.via == "pixels_changed"


def test_open_palette_seed_refuses_like_the_full_route():
    """The warm route carries the same contract: no safe scale
    default, no unroutable mode."""
    with pytest.raises(ValueError, match="bad backing scale"):
        magic_mask.seed_open_palette_script(
            _seed(), scale_factor=0.0,
            source_frame_range=_FRAME_RANGE)
    with pytest.raises(ValueError, match="bad Magic Mask mode"):
        magic_mask.seed_open_palette_script(
            _seed(), mode="SLOW", scale_factor=2.0,
            source_frame_range=_FRAME_RANGE)


def test_track_script_clicks_the_named_direction_only():
    assert magic_mask.track_script("forward", _FRAME_RANGE).steps[0].anchors[0].params[
        "text"] == "Track Forward"
    assert magic_mask.track_script("backward", _FRAME_RANGE).steps[0].anchors[0].params[
        "text"] == "Track Backward"
    assert "source frames 0..119 inclusive" in magic_mask.track_script(
        "forward", _FRAME_RANGE).goal
    with pytest.raises(ValueError, match="bad track direction"):
        magic_mask.track_script("sideways", _FRAME_RANGE)


class _SourceItem:
    def __init__(self, start=0, end=119):
        self.start = start
        self.end = end

    def GetSourceStartFrame(self):
        return self.start

    def GetSourceEndFrame(self):
        return self.end


def test_track_range_defaults_to_a_short_measured_trim():
    actual = magic_mask.source_frame_range_for_item(
        _SourceItem(), seed_frame=48)
    assert actual == _FRAME_RANGE


def test_track_range_refuses_a_long_source_before_tracking():
    with pytest.raises(ValueError, match="exceeds the 120-frame"):
        magic_mask.source_frame_range_for_item(
            _SourceItem(end=120), seed_frame=48)


def test_explicit_range_must_match_the_trim_and_contain_the_seed():
    with pytest.raises(ValueError, match="do not match the timeline item's"):
        magic_mask.source_frame_range_for_item(
            _SourceItem(), "1:119", seed_frame=48)
    with pytest.raises(ValueError, match="falls outside source range"):
        magic_mask.source_frame_range_for_item(
            _SourceItem(), "0:119", seed_frame=120)


def test_source_frame_range_parser_requires_inclusive_integer_bounds():
    assert magic_mask.parse_source_frame_range("12:99") == \
        magic_mask.SourceFrameRange(12, 99)
    with pytest.raises(ValueError, match="START:END"):
        magic_mask.parse_source_frame_range("12-")


def test_foreground_posts_input_at_the_hid_tap_only(client, monkeypatch):
    """`foreground=True` rides on the input-event calls and on nothing
    else: a background AX press already reaches the menu bar, and
    stamping it foreground would take the captain's screen for no
    reason. Fails on origin/main, which takes no foreground flag."""
    from library.tools.computer_use.scripts import (
        Anchor, run_script, ScriptStep)

    seen: list = []
    real_call = client.call

    def record(method, params=None):
        seen.append((method, dict(params or {})))
        return real_call(method, params)

    monkeypatch.setattr(client, "call", record)
    script = _mock_script()
    result = run_script(client, script, foreground=True)
    assert result.outcome == "done", result
    presses = [p for m, p in seen if m == "press"]
    clicks = [p for m, p in seen if m == "click"]
    assert presses and all("allow_foreground" not in p for p in presses)
    assert clicks and all(p.get("allow_foreground") is True
                          for p in clicks)


def test_background_sends_no_foreground_flag(client, monkeypatch):
    """The default stays background: no flag on any call."""
    from library.tools.computer_use.scripts import run_script

    seen: list = []
    real_call = client.call

    def record(method, params=None):
        seen.append((method, dict(params or {})))
        return real_call(method, params)

    monkeypatch.setattr(client, "call", record)
    assert run_script(client, _mock_script()).outcome == "done"
    assert seen and all("allow_foreground" not in p for _, p in seen)


def _mock_script():
    """Two steps the mock driver completes: the AX page press and an
    OCR-anchored click (the same pair `test_scripts` proves)."""
    from library.tools.computer_use.scripts import Anchor, Script, ScriptStep

    return Script(
        goal="press a menu path and click a label",
        steps=[
            ScriptStep(
                action="press",
                anchors=[Anchor(via="ax_path", params={
                    "path": ["Workspace", "Switch to Page", "Media"]})],
            ),
            ScriptStep(
                action="click",
                anchors=[Anchor(via="ocr", params={"text": "Master"})],
            ),
        ],
    )
