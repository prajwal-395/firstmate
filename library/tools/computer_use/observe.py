"""The model's observation: a capture, the projected AX surface, OCR labels.

The loop (PR 3) shows the model exactly what this module builds - nothing
more. The raw AX tree is 1634 nodes of which the actionable surface is the
menu bar; the projection keeps only the nodes with actions and drops the
rest, so the model never sees the structure the summary was rendered from
(repo rule: never send a summary and its own source). The capture is
downscaled to the window's point size - the coordinates the model returns
are window points, and a point-size image is a quarter of the tokens on a
Retina display.

The observation carries the task state too - the goal, the action history
- because a step's decision must be sourced from its own context: the model
sees what it has already tried, never a remembered coordinate.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from io import BytesIO

from library.tools.computer_use.client import DriverClient

#: Roles whose titles form a menu path. A text field under a menu keeps
#: the menu's path - it is found by role, not by path.
MENU_ROLES = ("AXMenuBarItem", "AXMenuItem")


@dataclass
class Observation:
    """What the model sees at one step.

    `screenshot` is a point-size PNG; `ax_surface` is the projected
    actionable nodes; `ocr_labels` are the driver's OCR elements with
    frames in window points; `window_frame` is the main window's frame in
    screen points. `goal`, `history` and `step` are the task state the
    loop maintains.
    """

    screenshot: bytes
    screenshot_points: tuple[int, int]
    ax_surface: list[dict]
    ocr_labels: list[dict]
    window_frame: dict
    goal: str = ""
    history: list[dict] = field(default_factory=list)
    step: int = 0

    def render(self) -> str:
        """The text half of the observation - the image goes separately.

        Rendered for the prompt, never stored: the model sees this plus
        the screenshot, and the next observation is built fresh from the
        driver.
        """
        lines = [f"goal: {self.goal}", f"step: {self.step}"]
        if self.history:
            lines.append("history:")
            for entry in self.history:
                lines.append(f"  - {entry.get('action')}: {entry.get('outcome')}")
        else:
            lines.append("history: none")
        lines.append(f"window: {self.window_frame}")
        lines.append("AX surface (actionable nodes):")
        for node in self.ax_surface:
            title = node.get("title")
            label = f"{node['role']} {title!r}" if title else node["role"]
            lines.append(f"  - {label} path={node['path']} actions={node['actions']}")
        lines.append("OCR labels:")
        for label in self.ocr_labels:
            frame = label.get("frame") or {}
            lines.append(
                f"  - {label.get('text')!r} at "
                f"({frame.get('x')}, {frame.get('y')}) "
                f"{frame.get('width')}x{frame.get('height')}"
            )
        return "\n".join(lines)


def project_ax_tree(tree: dict) -> list[dict]:
    """Project the raw AX tree to its actionable surface.

    Keeps only nodes with a non-empty `actions` list - the menu items and
    fields the model can press or set - flattened to a list with their
    menu paths. The path is the chain of menu titles from the menu bar
    item down, which is what `find_element(path=...)` takes. Nodes
    without actions (the application, the menu bar itself, structural
    groups) are dropped: they are not actionable and would only spend
    tokens.
    """
    projected: list[dict] = []

    def walk(node: dict, path: list[str]) -> None:
        role = node.get("role")
        title = node.get("title")
        here = path + [title] if role in MENU_ROLES and title else path
        if node.get("actions"):
            entry = {
                "role": role,
                "title": title,
                "path": here,
                "actions": list(node["actions"]),
            }
            if node.get("value") is not None:
                entry["value"] = node["value"]
            projected.append(entry)
        for child in node.get("children") or []:
            walk(child, here)

    walk(tree, [])
    return projected


def capture_at_point_size(client: DriverClient) -> tuple[bytes, tuple[int, int]]:
    """Capture the window at point size for the model.

    The driver captures at backing resolution (`scale` is image pixels per
    window point, 2.0 on a Retina display). The model sees the point-size
    image: the coordinates it returns are window points, and a point-size
    image is a quarter of the tokens. A region capture for a zoomed look
    is the loop's business (`capture_region`), not this default.
    """
    from PIL import Image

    result = client.call("capture_window")
    image = base64.b64decode(result["image"])
    width = int(round(result["width"] / result["scale"]))
    height = int(round(result["height"] / result["scale"]))
    with Image.open(BytesIO(image)) as img:
        img = img.convert("RGB").resize((width, height), Image.LANCZOS)
        out = BytesIO()
        img.save(out, format="PNG")
        return out.getvalue(), (width, height)


def build_observation(
    client: DriverClient,
    goal: str,
    history: list[dict] | None = None,
    step: int = 0,
) -> Observation:
    """Build the model's observation from the driver.

    Four driver calls: a capture, the AX tree, OCR, and the window list.
    Every one is read-only, so the observation is safe to build against
    the captain's own project - it changes nothing.
    """
    screenshot, points = capture_at_point_size(client)
    tree = client.call("get_accessibility_tree", {"max_depth": 12})["tree"]
    ocr = client.call("read_text")
    windows = client.call("get_windows")["windows"]
    main = next((w for w in windows if w.get("is_main")), {})
    return Observation(
        screenshot=screenshot,
        screenshot_points=points,
        ax_surface=project_ax_tree(tree),
        ocr_labels=list(ocr.get("elements") or []),
        window_frame=dict(main.get("frame") or {}),
        goal=goal,
        history=list(history or []),
        step=step,
    )
