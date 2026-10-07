"""The window-create recipe: measured menu route, op params in.

Each test names the defect it catches. The menu paths were measured
live 2026-10-06 on the Color page of 'Ren Qualification'; the mock
driver carries the same subtree, so the resolution tests run offline.
Nothing here renders pixels or touches live Resolve.
"""

from __future__ import annotations

import pytest

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.scripts import (
    Anchor,
    ScriptError,
    resolve_anchor,
)
from library.tools.computer_use import window_recipe
from library.tools.computer_use.window_recipe import (
    build_window_create_script,
)


def test_a_full_menu_path_resolves_through_the_title_split(
    client: DriverClient,
):
    """Defect: the driver matches `path` against the ancestors and the
    leaf is never matched - a full menu path as ancestors resolves
    nothing (measured live: ELEMENT_NOT_FOUND on ["View",
    "Power Window"]). The fallback split resolves it."""
    resolved = resolve_anchor(
        client,
        Anchor(via="ax_path",
               params={"path": ["Color", "Nodes", "Add Serial Node + CPW"]}),
    )
    assert resolved is not None
    method, params = resolved
    assert method == "press"
    assert params["element"]


def test_a_short_menu_path_resolves_as_before(client: DriverClient):
    """The fallback must not break container anchors: a path the whole
    match already resolves keeps resolving without the title split."""
    resolved = resolve_anchor(
        client,
        Anchor(via="ax_path",
               params={"path": ["Workspace", "Switch to Page", "Media"]}),
    )
    assert resolved is not None
    assert resolved[0] == "press"
    assert resolved[1]["element"]


def test_the_builder_presses_the_shape_s_menu_item():
    """Defect: a builder handing the runner the wrong menu item would
    draw the wrong window - circle must press + CPW, polygon + PPW."""
    script = build_window_create_script(
        {"unique_id": "a", "node_index": 2, "shape": "circle",
         "geometry": {"center": [0.5, 0.5]}})
    assert len(script.steps) == 1
    step = script.steps[0]
    assert step.action == "press"
    assert step.anchors[0].params["path"] == [
        "Color", "Nodes", "Add Serial Node + CPW"]
    poly = build_window_create_script(
        {"unique_id": "a", "node_index": 2, "shape": "polygon",
         "geometry": {"center": [0.5, 0.5]}})
    assert poly.steps[0].anchors[0].params["path"][-1] == \
        "Add Serial Node + PPW"


def test_the_builder_refuses_an_unmenued_shape():
    """Defect: gradient has no `Color > Nodes` item - a builder
    inventing one would hand the runner a press that matches nothing,
    failing mid-run after the lease and borrow. Refused at build."""
    with pytest.raises(ScriptError, match="gradient"):
        build_window_create_script(
            {"unique_id": "a", "node_index": 2, "shape": "gradient",
             "geometry": {"center": [0.5, 0.5]}})


def test_the_builder_refuses_an_unaddressed_op():
    """Defect: a window without its clip or node would run the press
    and claim a receipt naming nothing. Refused at build."""
    with pytest.raises(ScriptError, match="clip and node"):
        build_window_create_script({"shape": "circle"})


class _Item:
    def __init__(self, uid, counts):
        self._uid = uid
        self._counts = counts

    def GetUniqueId(self):
        return self._uid

    def GetNumNodes(self):
        return self._counts[0]

    def GetStart(self):
        return 96

    def GetDuration(self):
        return 72


class _Timeline:
    def __init__(self, item):
        self._item = item

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
    def __init__(self, timeline, name="Ren Qualification"):
        self._timeline = timeline
        self._name = name

    def GetName(self):
        return self._name

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


class _Resolve:
    def __init__(self, project):
        self._manager = _Manager(project)

    def GetProjectManager(self):
        return self._manager

    def OpenPage(self, _page):
        return True


def test_the_runner_refuses_a_non_append_index(client: DriverClient, monkeypatch):
    """Defect: the menu route appends exactly one past the count - an
    op naming any other node would     place the window elsewhere and
    claim the named one. Refused before anything presses."""
    item = _Item("clip-1", [1])
    resolve = _Resolve(_Project(_Timeline(item)))
    real_call = client.call

    def scoped_call(method, params=None):
        if method == "read_text":
            return {"elements": [{"text": "Scopes", "frame": {}}]}
        return real_call(method, params)

    monkeypatch.setattr(client, "call", scoped_call)
    with pytest.raises(ScriptError, match="not_append|NOT_APPEND|one past"):
        window_recipe.run_window_create(
            client, resolve,
            {"unique_id": "clip-1", "node_index": 5, "shape": "circle",
             "geometry": {}})


def test_the_runner_refuses_the_wrong_page(client: DriverClient):
    """Defect, caught live: the API switch reads color while the pixels
    stay on Deliver, and the menu press lands on the wrong page adding
    nothing (nodes 1 -> 1). The runner reads the labels and refuses
    before pressing unless the Color page shows."""
    item = _Item("clip-1", [1])
    resolve = _Resolve(_Project(_Timeline(item)))
    with pytest.raises(ScriptError, match="WRONG_PAGE|no Color page"):
        window_recipe.run_window_create(
            client, resolve,
            {"unique_id": "clip-1", "node_index": 2, "shape": "circle",
             "geometry": {}})

def test_the_runner_refuses_the_wrong_current_clip(
    client: DriverClient, monkeypatch,
):
    """Defect, caught live: the menu windows the clip under the
    playhead, and the playhead sat on qual_a while the op named qual_b
    - a node appeared on the wrong clip. The runner reads the current
    clip back and refuses the mismatch before pressing."""
    item = _Item("clip-1", [1])
    other = _Item("clip-9", [1])

    class _WrongTimeline(_Timeline):
        def GetCurrentVideoItem(self):
            return other

    resolve = _Resolve(_Project(_WrongTimeline(item)))
    real_call = client.call

    def scoped_call(method, params=None):
        if method == "read_text":
            return {"elements": [{"text": "Scopes", "frame": {}}]}
        return real_call(method, params)

    monkeypatch.setattr(client, "call", scoped_call)
    with pytest.raises(ScriptError, match="NOT_CURRENT|not .*clip-1"):
        window_recipe.run_window_create(
            client, resolve,
            {"unique_id": "clip-1", "node_index": 2, "shape": "circle",
             "geometry": {}})


def test_the_runner_claims_the_window_by_the_count(
    client: DriverClient, monkeypatch,
):
    """The menu press plus exactly one gained node claims the window;
    the receipt names the shape, the item and both counts."""
    counts = [1]
    item = _Item("clip-1", counts)
    resolve = _Resolve(_Project(_Timeline(item)))
    real_run_script = window_recipe.run_script
    real_call = client.call

    def scoped_call(method, params=None):
        if method == "read_text":
            return {"elements": [{"text": "Scopes", "frame": {}}]}
        return real_call(method, params)

    def bumped_scriptRunner(_client, script):
        counts[0] += 1
        return real_run_script(_client, script)

    monkeypatch.setattr(client, "call", scoped_call)
    monkeypatch.setattr(window_recipe, "run_script", bumped_scriptRunner)
    receipt = window_recipe.run_window_create(
        client, resolve,
        {"unique_id": "clip-1", "node_index": 2, "shape": "circle",
         "geometry": {"center": [0.5, 0.5]}})
    assert receipt["nodes_before"] == 1
    assert receipt["nodes_after"] == 2
    assert receipt["menu_item"] == "Add Serial Node + CPW"
    assert receipt["op"] == "color.window.create"


def test_the_verb_refuses_an_unknown_recipe_name():
    """Defect: a typo'd recipe name failing silently would run nothing
    and claim nothing. argparse refuses it (exit 2) because --recipe
    is a fixed choice list, not free text."""
    from library.tools.computer_use import __main__ as main_module

    with pytest.raises(SystemExit) as exc:
        main_module.main(["script", "--recipe", "no-such-recipe"])
    assert exc.value.code == 2


def test_the_verb_goes_through_one_take(monkeypatch, capsys):
    """window-create enters one ui_take: a hand-rolled lease releases
    between establish and act, and a snapshot there moves the take's
    ground."""
    from contextlib import contextmanager
    from types import SimpleNamespace

    takes: list = []

    @contextmanager
    def recording_take(purpose, **kw):
        takes.append(purpose)
        yield SimpleNamespace(resolve=object(), deadline=1234.0)

    monkeypatch.setattr(
        "library.tools.computer_use.take.ui_take", recording_take)
    seen: list = []
    monkeypatch.setattr(
        window_recipe, "run_window_create",
        lambda client, resolve, op: seen.append(op) or {"op": "color.window.create"},
    )
    args = SimpleNamespace(timeline="Q Seed", clip="uid-1", node=2,
                           shape="circle", seed=None, mode="BI",
                           direction="forward", playhead_timecode=None,
                           allow_foreground=False)
    assert window_recipe.run_recipe_verb(object(), args) == 0
    assert takes == ["computer-use script recipe window-create"]
    assert seen[0]["unique_id"] == "uid-1"
