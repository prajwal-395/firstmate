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
    return magic_mask.seed_script(_seed(), scale_factor=2.0)


def test_seed_script_is_page_palette_mode_click_in_order():
    script = _script()
    assert isinstance(script, Script)
    assert [s.action for s in script.steps] == [
        "press", "click", "click", "click"]
    assert [s.anchors[0].via for s in script.steps] == [
        "ax_path", "template", "ocr", "position"]
    # The palette step tries the measured icon first and keeps the
    # plan-stated OCR text as fallback: an unmeasured template resolves
    # to nothing and the step behaves as before.
    palette = script.steps[1]
    assert [a.via for a in palette.anchors] == ["template", "ocr"]
    assert palette.anchors[0].params["template"] == "magic-mask-toolbar"


def test_seed_script_presses_the_color_page_through_the_menu_bar():
    step = _script().steps[0]
    assert step.anchors[0].params["path"] == [
        "Workspace", "Switch to Page", "Color"]
    # The page switch is judged by the pixels changing, not by the
    # press returning: the driver reports the event posted, never the
    # page having moved.
    assert step.expect is not None and step.expect.via == "pixels_changed"


def test_seed_script_clicks_window_points_not_device_pixels():
    """The seed's viewer point is device pixels (screenshot space) but
    a position anchor is an absolute window point, so the builder
    divides by the measured backing scale: (1719, 622) at 2x clicks
    (859.5, 311). Carrying the raw point double-clicks on Retina -
    found live 2026-10-07, when every seed-shaped click missed."""
    step = _script().steps[3]
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
            magic_mask.seed_script(_seed(), scale_factor=bad)


def test_seed_script_refuses_a_mode_the_api_refuses():
    with pytest.raises(ValueError, match="bad Magic Mask mode"):
        magic_mask.seed_script(_seed(), mode="SLOW", scale_factor=2.0)


def test_track_script_clicks_the_named_direction_only():
    assert magic_mask.track_script("forward").steps[0].anchors[0].params[
        "text"] == "Track Forward"
    assert magic_mask.track_script("backward").steps[0].anchors[0].params[
        "text"] == "Track Backward"
    with pytest.raises(ValueError, match="bad track direction"):
        magic_mask.track_script("sideways")


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
