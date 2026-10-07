"""Qualifier sampling: the eyedropper route and the picture mapping.

Each test names the defect it catches. Geometry runs on synthetic
captures (PIL + numpy, in-process); the runner's refusals run against
small fakes. Nothing here renders pixels or touches live Resolve. The
template threshold (0.9) sits between the measured true match (1.0)
and the best measured impostor (0.851) on real Color page captures.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.scripts import ScriptError
from library.tools.computer_use import qualifier_recipe
from library.tools.computer_use.qualifier_recipe import (
    build_qualifier_sample_script,
    find_image_rect,
    viewer_point,
)


def _capture(width=320, height=180, picture=None):
    """A dark-chrome capture with a saturated picture rect inside."""
    img = Image.new("RGB", (width, height), (40, 40, 40))
    draw = ImageDraw.Draw(img)
    left, top, right, bottom = picture or (60, 20, 260, 140)
    bar = max(1, (right - left) // 6)
    for i, x in enumerate(range(left, right, bar)):
        shade = 60 + (i * 23) % 120
        draw.rectangle([x, top, min(x + bar, right), bottom],
                       fill=(shade, 30, 200))
    return img


def test_the_rect_hugs_the_picture():
    """Defect: a luma rule mistakes the bright-gray node graph for the
    viewer (measured live). Saturation finds the picture and skips the
    chrome."""
    rect = find_image_rect(_capture())
    left, top, right, bottom = rect
    assert left == pytest.approx(60, abs=6)
    assert top == pytest.approx(20, abs=6)
    assert right == pytest.approx(260, abs=6)
    assert bottom == pytest.approx(140, abs=6)


def test_a_pictureless_frame_is_refused_not_clicked():
    """Defect: a blind click on chrome would sample the UI instead of
    footage. A dark frame measures no rect and refuses."""
    img = Image.new("RGB", (320, 180), (40, 40, 40))
    with pytest.raises(ScriptError, match="NO_PICTURE|no picture"):
        find_image_rect(img)


def test_the_mapping_hits_corners_and_center():
    """The normalized picture point lands on the rect directly - the
    rect already excludes bars, so no aspect arithmetic can mismatch."""
    rect = (100, 50, 300, 150)
    assert viewer_point(rect, 0.0, 0.0) == (100, 50)
    assert viewer_point(rect, 1.0, 1.0) == (300, 150)
    assert viewer_point(rect, 0.5, 0.5) == (200, 100)


def test_a_point_off_the_picture_is_refused():
    """A normalized point outside 0..1 names chrome, not footage."""
    with pytest.raises(ScriptError, match="UNMAPPABLE|0..1"):
        viewer_point((100, 50, 300, 150), 1.2, 0.5)


def test_the_builder_clicks_eyedropper_then_point(tmp_path):
    """The sample is two clicks in order: the template eyedropper,
    then the mapped position with its measured why."""
    template = tmp_path / "qpick.png"
    template.write_bytes(b"fake")
    script = build_qualifier_sample_script(str(template), (455.0, 605.0))
    assert len(script.steps) == 2
    first, second = script.steps
    assert first.action == "click"
    assert first.anchors[0].via == "template"
    assert first.anchors[0].params["image"] == str(template)
    assert second.action == "click"
    assert second.anchors[0].via == "position"
    assert second.anchors[0].params["x"] == 455.0
    assert "why" in second.anchors[0].params


class _Item:
    def __init__(self, uid):
        self._uid = uid

    def GetUniqueId(self):
        return self._uid

    def GetStart(self):
        return 96

    def GetDuration(self):
        return 72


class _Timeline:
    def __init__(self, item):
        self._item = item

    def GetName(self):
        return "Q Spine"

    def GetUniqueId(self):
        return "timeline-1"

    def GetTrackCount(self, _track):
        return 1

    def GetItemListInTrack(self, _track, _index):
        return [self._item]

    def SetCurrentTimecode(self, _tc):
        return True

    def GetCurrentVideoItem(self):
        return self._item


class _Project:
    def __init__(self, timeline):
        self._timeline = timeline

    def GetName(self):
        return "Ren Qualification"

    def GetTimelineCount(self):
        return 1

    def GetTimelineByIndex(self, _index):
        return self._timeline

    def SetCurrentTimeline(self, _timeline):
        return True

    def GetCurrentTimeline(self):
        return self._timeline


class _Manager:
    def __init__(self, project):
        self._project = project

    def LoadProject(self, _name):
        return self._project

    def GetCurrentProject(self):
        return self._project


class _Resolve:
    def __init__(self, project):
        self._manager = _Manager(project)

    def GetProjectManager(self):
        return self._manager

    def OpenPage(self, _page):
        return True


def _resolve():
    return _Resolve(_Project(_Timeline(_Item("clip-1"))))


def test_the_runner_refuses_a_pointless_op(client: DriverClient):
    """A sample without its clip or point runs no clicks."""
    with pytest.raises(ScriptError, match="UNADDRESSED|clip and point"):
        qualifier_recipe.run_qualifier_sample(
            client, _resolve(), {"unique_id": "clip-1"})


def test_the_runner_refuses_the_wrong_page(client: DriverClient,
                                           monkeypatch):
    """The mock reads no Scopes label, so the page gate refuses before
    any click - the eyedropper would sample the wrong page."""
    real_call = client.call

    def scoped_call(method, params=None):
        if method == "capture_window":
            import base64
            from io import BytesIO

            buf = BytesIO()
            _capture().save(buf, format="PNG")
            return {"image": base64.b64encode(buf.getvalue()).decode(),
                    "width": 320.0, "height": 180.0, "scale": 1.0}
        return real_call(method, params)

    monkeypatch.setattr(client, "call", scoped_call)
    with pytest.raises(ScriptError, match="WRONG_PAGE|no Color page"):
        qualifier_recipe.run_qualifier_sample(
            client, _resolve(),
            {"unique_id": "clip-1", "point": [0.5, 0.5],
             "timeline": "Q Spine"})


def test_the_verb_refuses_an_unknown_recipe_name():
    """--recipe stays a fixed choice list."""
    from library.tools.computer_use import __main__ as main_module

    with pytest.raises(SystemExit) as exc:
        main_module.main(["script", "--recipe", "no-such-recipe"])
    assert exc.value.code == 2
