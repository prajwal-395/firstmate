"""Window modification: panel fields by label, verified by re-read.

Each test names the defect it catches. Geometry runs on synthetic
captures; the runner's refusals run against small fakes. Nothing here
renders pixels or touches live Resolve. The label-to-field offsets
(+55x/+5pt) were measured on real Color page captures across both
panel columns.
"""

from __future__ import annotations

import pytest

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.scripts import ScriptError
from library.tools.computer_use import window_modify
from library.tools.computer_use.window_modify import (
    FIELD_DX,
    FIELD_DY,
    build_window_modify_script,
    _read_panel_values,
)


def test_the_builder_clicks_types_and_commits_per_field():
    """Each panel parameter is three steps in order: offset-click into
    the field, type the text, Return to commit. A builder emitting
    clicks without types would focus fields and change nothing."""
    script = build_window_modify_script({"size": "75.00", "pan": "60.00"})
    assert len(script.steps) == 6
    kinds = [step.action for step in script.steps]
    assert kinds == ["click", "type", "key"] * 2
    type_step = script.steps[1]
    assert type_step.anchors[0].via == "type"
    assert type_step.anchors[0].params["text"] == "75.00"
    key_step = script.steps[2]
    assert key_step.anchors[0].via == "key"


def test_the_builder_refuses_unmeasured_controls():
    """A control whose label the OCR pass never returned gets no
    invented anchor - refused at build."""
    with pytest.raises(ScriptError, match="UNMEASURED|unmeasured"):
        build_window_modify_script({"vignette": "1.00"})


def test_the_builder_refuses_unformatted_values():
    """The script carries text; numbers are formatted at the recipe
    layer, never in the script."""
    with pytest.raises(ScriptError, match="UNFORMATTED|not a string"):
        build_window_modify_script({"size": 75.0})


def test_the_reader_finds_values_beside_labels():
    """The re-read pairs each label with its nearest value text -
    which is what the verification compares."""

    class _Client:
        def call(self, method, params=None):
            assert method == "read_text"
            return {"elements": [
                {"text": "Size",
                 "frame": {"x": 100.0, "y": 200.0,
                           "width": 24.0, "height": 11.0}},
                {"text": "75.00",
                 "frame": {"x": 100.0 + FIELD_DX - 16.0,
                           "y": 200.0 + FIELD_DY - 5.0,
                           "width": 33.0, "height": 11.0}},
                {"text": "Scopes", "frame": {"x": 900.0, "y": 600.0,
                                              "width": 50.0, "height": 13.0}},
            ]}

    found = _read_panel_values(_Client(), ["Size"])
    assert found == {"Size": "75.00"}


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


def test_the_runner_refuses_an_unaddressed_op(client: DriverClient):
    """A modify without its clip or values runs no clicks."""
    with pytest.raises(ScriptError, match="UNADDRESSED|clip and values"):
        window_modify.run_window_modify(
            client, _resolve(), {"unique_id": "clip-1"})


def test_the_runner_refuses_a_reread_mismatch(client: DriverClient,
                                              monkeypatch):
    """Defect: a typed value the panel does not show back is not set -
    the re-read mismatch refuses instead of claiming it."""
    real_call = client.call
    calls = {"n": 0}

    def scoped_call(method, params=None):
        if method == "read_text":
            calls["n"] += 1
            value = "50.00" if calls["n"] == 1 else "50.00"
            return {"elements": [
                {"text": "Scopes", "frame": {"x": 900.0, "y": 600.0,
                                              "width": 50.0, "height": 13.0}},
                {"text": "Size", "frame": {"x": 100.0, "y": 200.0,
                                           "width": 24.0, "height": 11.0}},
                {"text": value, "frame": {"x": 139.0, "y": 200.0,
                                           "width": 33.0, "height": 11.0}},
            ]}
        return real_call(method, params)

    monkeypatch.setattr(client, "call", scoped_call)
    import library.tools.computer_use.window_modify as mod

    monkeypatch.setattr(
        mod, "run_script",
        lambda *a, **k: type("R", (), {"outcome": "done", "steps": []})())
    with pytest.raises(ScriptError, match="UNVERIFIED|does not show"):
        mod.run_window_modify(
            client, _resolve(),
            {"unique_id": "clip-1", "values": {"size": "75.00"},
             "timeline": "Q Spine"})


def test_the_verb_refuses_an_unknown_recipe_name():
    """--recipe stays a fixed choice list."""
    from library.tools.computer_use import __main__ as main_module

    with pytest.raises(SystemExit) as exc:
        main_module.main(["script", "--recipe", "no-such-recipe"])
    assert exc.value.code == 2


def test_a_type_anchor_resolves_to_a_type_call(client: DriverClient):
    """Defect, caught on the slice-8 branch: the builder emits type
    steps but no resolver produced type calls - every modify would
    focus fields and type nothing. The via resolves with no screen
    anchor, carrying only the text, to the driver's `type_text`
    method (the method the loop path already emits - a resolver
    emitting anything else fails every take at the driver gate)."""
    from library.tools.computer_use.scripts import Anchor, resolve_anchor

    resolved = resolve_anchor(
        client, Anchor(via="type", params={"text": "75.00"}))
    assert resolved == ("type_text", {"text": "75.00"})
    assert resolve_anchor(
        client, Anchor(via="type", params={"text": ""})) is None
