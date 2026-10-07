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
import hashlib
import json
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Optional

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

    The session half (the plan's section 2, `ResolveSessionState`) is
    filled from the scripting API where a session dict is supplied -
    `project`, `timeline`, `page`, `playhead` and `resolve_version`
    come from `read_api_session`, and `target_clip` is the transaction's
    own target carried through so the model sees what the actions act
    on. `window_scale` is the main window's backing scale, `focused_element`
    the AX tree's focused node, `layout_fingerprint` the structure hash.
    Every API field defaults to None: an absent measurement is never
    invented. `visible_panels` is NOT here - neither the driver protocol
    nor the scripting API enumerates panels, and the fingerprint covers
    the layout drift panels would cause.
    """

    screenshot: bytes
    screenshot_points: tuple[int, int]
    ax_surface: list[dict]
    ocr_labels: list[dict]
    window_frame: dict
    goal: str = ""
    history: list[dict] = field(default_factory=list)
    step: int = 0
    target: str = "resolve"
    resolve_version: Optional[str] = None
    project: Optional[str] = None
    timeline: Optional[str] = None
    page: Optional[str] = None
    playhead: Optional[str] = None
    target_clip: Optional[dict] = None
    window_scale: Optional[float] = None
    focused_element: Optional[str] = None
    layout_fingerprint: str = ""

    def render(self) -> str:
        """The text half of the observation - the image goes separately.

        Rendered for the prompt, never stored: the model sees this plus
        the screenshot, and the next observation is built fresh from the
        driver.
        """
        lines = [f"goal: {self.goal}", f"step: {self.step}", f"target: {self.target}"]
        if self.history:
            lines.append("history:")
            for entry in self.history:
                line = f"  - {entry.get('action')}: {entry.get('outcome')}"
                if entry.get("refusal"):
                    line += f" (refusal: {entry['refusal']})"
                lines.append(line)
        else:
            lines.append("history: none")
        lines.append(f"window: {self.window_frame}")
        if self.window_scale is not None:
            lines.append(f"window scale: {self.window_scale}")
        session = {
            "resolve": self.resolve_version,
            "project": self.project,
            "timeline": self.timeline,
            "page": self.page,
            "playhead": self.playhead,
        }
        known = {k: v for k, v in session.items() if v is not None}
        lines.append(
            "session: "
            + (", ".join(f"{k}={v}" for k, v in known.items()) or "unknown")
        )
        if self.target_clip is not None:
            lines.append(f"target clip: {self.target_clip}")
        if self.focused_element is not None:
            lines.append(f"focused: {self.focused_element}")
        if self.layout_fingerprint:
            lines.append(f"layout: {self.layout_fingerprint}")
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


def layout_fingerprint(ax_surface: list[dict]) -> str:
    """Hash the actionable surface's STRUCTURE - not its coordinates.

    The canonical form is the sorted `(role, title, path)` of each
    projected node: values, frames and refs are excluded, so typing in
    a field or moving the window does not change it, while a panel
    opening or closing (nodes appearing or vanishing) does. A recipe
    store keys on this to refuse a stale route without a threshold -
    equal means same layout, different means re-resolve, and there is
    no middle that needs a cutoff.
    """
    canonical = sorted(
        (
            str(node.get("role") or ""),
            str(node.get("title") or ""),
            tuple(str(bit) for bit in node.get("path") or ()),
        )
        for node in ax_surface
    )
    return hashlib.sha256(
        json.dumps(canonical, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]


def _focused_label(tree: Any) -> Optional[str]:
    """The AX tree's focused node, or None where nothing is focused.

    The twin of `resolved.ui_transaction._focused_label`, kept local so
    this module does not import the broker job: the broker reads focus
    off its own tree call, this one off the observation's.
    """
    if not isinstance(tree, dict):
        return None
    if tree.get("focused"):
        return str(tree.get("label") or tree.get("title") or "") or None
    for child in tree.get("children") or ():
        hit = _focused_label(child)
        if hit is not None:
            return hit
    return None


def read_api_session(resolve: Any, target_clip: Optional[dict] = None) -> dict:
    """Read the session half of the state model off the scripting API.

    Project, timeline, page, playhead and the Resolve version come from
    the API - the driver cannot see them. Anything the API does not
    expose on this build is None, never invented. `target_clip` is the
    caller's own target (the transaction spec's `{"clip_id": ...}`),
    carried through so the observation names what the actions act on.
    """
    version: Optional[str] = None
    try:
        version = resolve.GetVersionString() or None
    except Exception:                                   # noqa: BLE001
        version = None
    try:
        current_page = resolve.GetCurrentPage() or None
    except Exception:                                   # noqa: BLE001
        current_page = None
    project_name: Optional[str] = None
    timeline_name: Optional[str] = None
    playhead: Optional[str] = None
    try:
        project = resolve.GetProjectManager().GetCurrentProject()
    except Exception:                                   # noqa: BLE001
        project = None
    if project is not None:
        try:
            project_name = project.GetName() or None
        except Exception:                               # noqa: BLE001
            project_name = None
        try:
            timeline = project.GetCurrentTimeline()
        except Exception:                               # noqa: BLE001
            timeline = None
        if timeline is not None:
            try:
                timeline_name = timeline.GetName() or None
            except Exception:                           # noqa: BLE001
                timeline_name = None
            try:
                playhead = timeline.GetCurrentTimecode() or None
            except Exception:                           # noqa: BLE001
                playhead = None
    return {
        "resolve_version": version,
        "project": project_name,
        "timeline": timeline_name,
        "page": current_page,
        "playhead": playhead,
        "target_clip": dict(target_clip) if target_clip is not None else None,
    }


def capture_at_point_size(
    client: DriverClient, target: str = "resolve", pid: int | None = None
) -> tuple[bytes, tuple[int, int]]:
    """Capture the window at point size for the model.

    The driver captures at backing resolution (`scale` is image pixels per
    window point, 2.0 on a Retina display). The model sees the point-size
    image: the coordinates it returns are window points, and a point-size
    image is a quarter of the tokens. A region capture for a zoomed look
    is the loop's business (`capture_region`), not this default.
    """
    from PIL import Image

    params: dict = {"target": target}
    if pid is not None:
        params["pid"] = pid
    result = client.call("capture_window", params)
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
    session: dict | None = None,
    target: str = "resolve",
    pid: int | None = None,
) -> Observation:
    """Build the model's observation from the driver.

    Four driver calls: a capture, the AX tree, OCR, and the window list.
    Every one is read-only, so the observation is safe to build against
    the captain's own project - it changes nothing. `session` is the
    API-read half (`read_api_session`) where the caller has a resolve
    handle; without it the session fields stay None and the render says
    `session: unknown` rather than inventing one. `target` selects whose
    window is observed (the driver's default); the observation records
    it so the model knows which app it sees.
    """
    extra = {"pid": pid} if pid is not None else {}
    screenshot, points = capture_at_point_size(client, target, pid)
    tree = client.call(
        "get_accessibility_tree", {"max_depth": 12, "target": target, **extra}
    )["tree"]
    ocr = client.call("read_text", {"target": target, **extra})
    windows = client.call("get_windows", {"target": target})["windows"]
    main = next((w for w in windows if w.get("is_main")), {})
    surface = project_ax_tree(tree)
    session = dict(session or {})
    return Observation(
        screenshot=screenshot,
        screenshot_points=points,
        ax_surface=surface,
        ocr_labels=list(ocr.get("elements") or []),
        window_frame=dict(main.get("frame") or {}),
        goal=goal,
        history=list(history or []),
        step=step,
        target=target,
        resolve_version=session.get("resolve_version"),
        project=session.get("project"),
        timeline=session.get("timeline"),
        page=session.get("page"),
        playhead=session.get("playhead"),
        target_clip=session.get("target_clip"),
        window_scale=main.get("backing_scale"),
        focused_element=_focused_label(tree),
        layout_fingerprint=layout_fingerprint(surface),
    )
