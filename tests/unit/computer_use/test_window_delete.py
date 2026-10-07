"""Window deletion: the menu press and its read-back.

Each test names the defect it catches. The menu path was measured live
2026-10-06 on the Color page of 'Ren Qualification'; the mock driver
carries it, so the resolution tests run offline. Nothing here renders
pixels or touches live Resolve.
"""

from __future__ import annotations

import pytest

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.scripts import (
    ScriptError,
)
from library.tools.computer_use import window_delete
from library.tools.computer_use.window_delete import (
    MENU_PATH,
    build_window_delete_script,
)


def test_the_builder_presses_delete_current_node(client: DriverClient):
    """The delete step names the measured menu item - a builder
    inventing its own path would press nothing."""
    from library.tools.computer_use.scripts import resolve_anchor
    from library.tools.computer_use.scripts import Anchor

    script = build_window_delete_script()
    assert len(script.steps) == 1
    assert script.steps[0].anchors[0].params["path"] == MENU_PATH
    resolved = resolve_anchor(client, script.steps[0].anchors[0])
    assert resolved is not None
    assert resolved[0] == "press"


class _Item:
    def __init__(self, uid, counts, name="qual_b.mp4"):
        self._uid = uid
        self._counts = counts
        self._name = name

    def GetUniqueId(self):
        return self._uid

    def GetNumNodes(self):
        return self._counts[0]

    def GetName(self):
        return self._name

    def GetStart(self):
        return 96

    def GetDuration(self):
        return 72


class _Timeline:
    def __init__(self, item, name="Q Spine"):
        self._item = item
        self._name = name

    def GetName(self):
        return self._name

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

    def GetCurrentProject(self):
        return self._project


class _Resolve:
    def __init__(self, project):
        self._manager = _Manager(project)

    def GetProjectManager(self):
        return self._manager

    def OpenPage(self, _page):
        return True


def _resolve(counts=(2,)):
    item = _Item("clip-1", list(counts))
    return _Resolve(_Project(_Timeline(item)))


def _scoped(client, monkeypatch):
    real_call = client.call

    def scoped_call(method, params=None):
        if method == "read_text":
            return {"elements": [{"text": "Scopes", "frame": {}}]}
        return real_call(method, params)

    monkeypatch.setattr(client, "call", scoped_call)


def _op(**extra):
    op = {"unique_id": "clip-1", "node_index": 2,
          "timeline": "Q Spine"}
    op.update(extra)
    return op


def test_the_runner_refuses_a_single_node_clip(
    client: DriverClient, monkeypatch,
):
    """Defect: deleting the last node is not a window removal anyone
    measured - the runner refuses a one-node clip before pressing."""
    _scoped(client, monkeypatch)
    with pytest.raises(ScriptError, match="NO_WINDOW|one-node|1 node"):
        window_delete.run_window_delete(
            client, _resolve((1,)), _op(node_index=1))


def test_the_runner_refuses_a_non_last_index(
    client: DriverClient, monkeypatch,
):
    """Defect: the menu deletes whichever node is current and no API
    reads which that is - only the last node is safe. Any other index
    is refused before pressing."""
    _scoped(client, monkeypatch)
    with pytest.raises(ScriptError, match="NOT_LAST|last node"):
        window_delete.run_window_delete(
            client, _resolve((3,)), _op(node_index=1))


def test_the_runner_claims_the_delete_by_the_count(
    client: DriverClient, monkeypatch,
):
    """The menu press plus exactly one fewer node claims the delete."""
    _scoped(client, monkeypatch)
    counts = [2]
    item = _Item("clip-1", counts)
    resolve = _Resolve(_Project(_Timeline(item)))
    real_run_script = window_delete.run_script

    def dropped_script_runner(_client, script):
        counts[0] -= 1
        return real_run_script(_client, script)

    monkeypatch.setattr(window_delete, "run_script", dropped_script_runner)
    receipt = window_delete.run_window_delete(client, resolve, _op())
    assert receipt["nodes_before"] == 2
    assert receipt["nodes_after"] == 1
    assert receipt["menu_item"] == "Color > Nodes > Delete Current Node"
    assert receipt["op"] == "color.window.delete"


def test_the_verb_refuses_an_unknown_recipe_name():
    """--recipe is a fixed choice list, so a typo fails at the flag
    (exit 2), never inside a builder."""
    from library.tools.computer_use import __main__ as main_module

    with pytest.raises(SystemExit) as exc:
        main_module.main(["script", "--recipe", "no-such-recipe"])
    assert exc.value.code == 2


def test_the_verb_goes_through_one_take(monkeypatch, capsys):
    """window-delete enters one ui_take: same gate as window-create -
    the take holds from establish through verify."""
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
        window_delete, "run_window_delete",
        lambda client, resolve, op: seen.append(op) or {"op": "color.window.delete"},
    )
    args = SimpleNamespace(timeline="Q Spine", clip="uid-9", node=3,
                           seed=None, mode="BI", direction="forward",
                           playhead_timecode=None, shape=None,
                           allow_foreground=False)
    assert window_delete.run_recipe_verb(object(), args) == 0
    assert takes == ["computer-use script recipe window-delete"]
    assert seen[0]["unique_id"] == "uid-9"
