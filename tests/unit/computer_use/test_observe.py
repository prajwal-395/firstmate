"""The observation's contract, pinned against the mock driver.

Each test names the defect it catches: a behaviour that would be wrong if
observe regressed. The two that matter most - the model never sees the
raw AX tree, and the capture is at point size - are the ones a future
change would break first, because both look like harmless simplifications.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.observe import (
    build_observation,
    capture_at_point_size,
    project_ax_tree,
)

from tests.mock_driver import AX_TREE, CAPTURE_HEIGHT, CAPTURE_SCALE, CAPTURE_WIDTH


def _png_size(png: bytes) -> tuple[int, int]:
    with Image.open(BytesIO(png)) as img:
        return img.size


def test_the_projection_keeps_only_actionable_nodes():
    """The raw tree's structural nodes - the application, the menu bar
    itself - are not actionable and must not reach the model. A projection
    that kept them would spend the model's context on nodes it cannot
    press, and the model would try to press the application."""
    projected = project_ax_tree(AX_TREE)
    roles = {node["role"] for node in projected}
    assert "AXApplication" not in roles
    assert "AXMenuBar" not in roles
    assert "AXMenuBarItem" in roles
    assert "AXMenuItem" in roles
    assert "AXTextField" in roles


def test_the_projection_flattens_to_menu_paths():
    """Each actionable node carries the menu path find_element takes -
    the chain of titles from the menu bar item down. A projection that
    kept the tree shape would make the model re-derive the path at every
    step, and a wrong path is a missed press."""
    projected = project_ax_tree(AX_TREE)
    by_title = {node["title"]: node for node in projected if node["title"]}
    assert by_title["Media"]["path"] == ["Workspace", "Switch to Page", "Media"]
    assert by_title["Workspace"]["path"] == ["Workspace"]
    assert by_title["Switch to Page"]["path"] == ["Workspace", "Switch to Page"]


def test_a_node_without_a_title_keeps_its_parents_path():
    """The Help search field has no title - it is found by role, not by
    path. Its projection keeps the menu path so the model knows where it
    lives, and the role so it knows not to path-match it."""
    projected = project_ax_tree(AX_TREE)
    fields = [node for node in projected if node["role"] == "AXTextField"]
    assert len(fields) == 1
    assert fields[0]["title"] is None
    assert fields[0]["path"] == ["Help"]


def test_an_observation_contains_no_raw_tree(client: DriverClient):
    """The model's observation carries the projection, never the raw tree.
    The raw 1634-node tree is the structure the summary was rendered from
    - sending both is the repo's named defect, and at 40-60K tokens it
    would crowd out the screenshot."""
    obs = build_observation(client, goal="switch to the Media page")
    assert obs.ax_surface == project_ax_tree(AX_TREE)
    assert all("children" not in node for node in obs.ax_surface)
    assert all("ref" not in node for node in obs.ax_surface)


def test_the_capture_is_at_point_size(client: DriverClient):
    """The driver captures at backing resolution (128x64 at scale 2.0 on
    the mock's Retina); the model sees 64x32. A client that passed the
    backing image through would bill the model four times the tokens and
    hand it coordinates in the wrong space."""
    screenshot, points = capture_at_point_size(client)
    assert points == (
        int(CAPTURE_WIDTH / CAPTURE_SCALE),
        int(CAPTURE_HEIGHT / CAPTURE_SCALE),
    )
    assert _png_size(screenshot) == (64, 32)


def test_the_observation_carries_the_ocr_labels_in_window_points(
    client: DriverClient,
):
    """OCR labels arrive in window-relative points - the same space the
    model's click coordinates are in. An observation that converted them
    to backing pixels would put every label off by the scale factor."""
    obs = build_observation(client, goal="click the Master label")
    assert len(obs.ocr_labels) == 1
    label = obs.ocr_labels[0]
    assert label["text"] == "Master"
    frame = label["frame"]
    assert frame["x"] == 21.98
    assert frame["y"] == 96.66


def test_the_observation_carries_the_task_state(client: DriverClient):
    """The goal and history travel with the observation - a step's
    decision must be sourced from its own context, so the model sees what
    it has already tried, not a remembered coordinate."""
    history = [{"action": "press Workspace", "outcome": "ok"}]
    obs = build_observation(client, goal="switch pages", history=history, step=3)
    assert obs.goal == "switch pages"
    assert obs.history == history
    assert obs.step == 3


def test_render_names_the_actionable_surface_and_the_labels(
    client: DriverClient,
):
    """The rendered text is what the model reads beside the screenshot. It
    names the AX surface and the OCR labels - a render that dropped
    either would leave the model guessing at controls it can see."""
    obs = build_observation(client, goal="switch to the Media page")
    text = obs.render()
    assert "goal: switch to the Media page" in text
    assert "AXMenuItem 'Media'" in text
    assert "path=['Workspace', 'Switch to Page', 'Media']" in text
    assert "'Master'" in text


def test_the_observation_names_its_target_in_every_call(client: DriverClient, monkeypatch):
    """A chrome observation reaches the driver's chrome route on all
    four calls - an observation that observed Resolve while the loop
    acts on Chrome would decide from the wrong app."""
    calls: list = []
    original_call = client.call

    def recording_call(method, params=None):
        calls.append((method, dict(params or {})))
        return original_call(method, params)

    monkeypatch.setattr(client, "call", recording_call)
    obs = build_observation(client, goal="look at canva", target="chrome")

    assert obs.target == "chrome"
    assert "target: chrome" in obs.render()
    by_method = {method: params for method, params in calls}
    for method in ("capture_window", "get_accessibility_tree", "read_text", "get_windows"):
        assert by_method[method].get("target") == "chrome", method
