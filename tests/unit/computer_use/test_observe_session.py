"""The observation's session half: API state and the layout fingerprint.

Each test names the defect it catches: a behaviour that silently loses
the model's structured view of Resolve and leaves it driving from a
bare screenshot.
"""

from __future__ import annotations

import copy

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.observe import (
    _focused_label,
    build_observation,
    layout_fingerprint,
    project_ax_tree,
    read_api_session,
)

from tests.mock_driver import AX_TREE


def test_typing_in_a_field_does_not_change_the_layout():
    """The fingerprint hashes structure, not values: typing in the Help
    search field must not change it. A fingerprint that hashed values
    would flip on every keystroke, and a recipe store keyed on it
    would refuse every route after the first character typed."""
    before = project_ax_tree(AX_TREE)
    typed = copy.deepcopy(AX_TREE)

    def set_value(node):
        if node.get("role") == "AXTextField":
            node["value"] = "render settings"
        for child in node.get("children") or []:
            set_value(child)

    set_value(typed)
    assert layout_fingerprint(project_ax_tree(typed)) == layout_fingerprint(before)


def test_a_new_panel_changes_the_layout():
    """A panel opening adds actionable nodes, and the fingerprint must
    change with it. A fingerprint blind to structure would let a stale
    recipe run against a layout that no longer has its controls."""
    before = project_ax_tree(AX_TREE)
    restructured = copy.deepcopy(AX_TREE)
    restructured["children"][0]["children"].append(
        {
            "ref": "ax-menu-effects",
            "role": "AXMenuBarItem",
            "title": "Effects",
            "actions": ["AXPress"],
            "children": [],
        }
    )
    assert layout_fingerprint(project_ax_tree(restructured)) != layout_fingerprint(
        before
    )


def test_the_session_reaches_the_model_prompt(client: DriverClient):
    """Page, timeline and playhead come from the API, not the pixels -
    the model cannot read the page off a screenshot. A session that
    never reached the render would leave the model guessing which page
    its actions land on."""
    session = {
        "resolve_version": "21.1",
        "project": "Ren Qualification",
        "timeline": "Reel 07",
        "page": "color",
        "playhead": "00:00:14:12",
        "target_clip": {"clip_id": "abc"},
    }
    obs = build_observation(client, goal="seed a mask", session=session)
    assert obs.page == "color"
    assert obs.playhead == "00:00:14:12"
    assert obs.resolve_version == "21.1"
    assert obs.window_scale == 2.0
    assert len(obs.layout_fingerprint) == 16
    text = obs.render()
    assert "page=color" in text
    assert "timeline=Reel 07" in text
    assert "playhead=00:00:14:12" in text
    assert "target clip: {'clip_id': 'abc'}" in text
    assert f"layout: {obs.layout_fingerprint}" in text


def test_an_observation_without_a_resolve_handle_says_so(client: DriverClient):
    """Without a resolve handle there is no API state, and the render
    must say `session: unknown` rather than invent a page. An invented
    page would send menu paths at the wrong workspace."""
    obs = build_observation(client, goal="switch pages")
    assert obs.page is None
    assert obs.playhead is None
    assert "session: unknown" in obs.render()


def test_the_api_reader_records_absence_never_invention():
    """A Resolve build that exposes no version, page or timeline gives
    Nones, not empty strings or guesses. A reader that defaulted would
    teach the model a project name that is not open."""

    class BareResolve:
        def GetVersionString(self):
            raise AttributeError("no version here")

        def GetCurrentPage(self):
            raise AttributeError("no page here")

        def GetProjectManager(self):
            raise AttributeError("no project here")

    session = read_api_session(BareResolve(), target_clip={"clip_id": "abc"})
    assert session == {
        "resolve_version": None,
        "project": None,
        "timeline": None,
        "page": None,
        "playhead": None,
        "target_clip": {"clip_id": "abc"},
    }


def test_the_api_reader_names_the_open_project():
    """The session names the project, timeline, page and playhead the
    API reports - the same four `snapshot_context` reads, so the broker
    job and the observation agree on where Resolve is."""

    class Timeline:
        def GetName(self):
            return "Reel 07"

        def GetCurrentTimecode(self):
            return "00:00:14:12"

    class Project:
        def GetName(self):
            return "Ren Qualification"

        def GetCurrentTimeline(self):
            return Timeline()

    class Resolve:
        def GetVersionString(self):
            return "21.1.0"

        def GetCurrentPage(self):
            return "color"

        def GetProjectManager(self):
            class Manager:
                def GetCurrentProject(self):
                    return Project()

            return Manager()

    session = read_api_session(Resolve())
    assert session["project"] == "Ren Qualification"
    assert session["timeline"] == "Reel 07"
    assert session["page"] == "color"
    assert session["playhead"] == "00:00:14:12"
    assert session["resolve_version"] == "21.1.0"
    assert session["target_clip"] is None


def test_focus_is_read_off_the_tree_not_invented():
    """The focused element comes from the AX tree's focused flag. A
    reader that defaulted to the window title would tell the model its
    keystrokes land somewhere they do not."""
    assert _focused_label(AX_TREE) is None
    tree = {
        "role": "AXApplication",
        "children": [
            {
                "role": "AXTextField",
                "title": "",
                "label": "Help search",
                "focused": True,
            }
        ],
    }
    assert _focused_label(tree) == "Help search"
