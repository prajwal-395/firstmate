"""A mock of the driver's JSON protocol, for the offline suite.

The tests point `RESOLVE_CONTROL_BIN` at a generated copy of this file.
It speaks the driver's wire format - one JSON object per line, one
response per request - and emulates the measured behaviour the client
tests pin: element refs that survive between calls, the qualification
project gate, and a process that can die between calls.

It is a test double, not a second driver: it implements the protocol's
shape and the gates the client depends on, nothing else. Action methods
echo the params they were given, so a test can read the translation the
client produced - the echo is the driver's answer, not a copy of the
request.
"""

from __future__ import annotations

import json
import os
import sys
import time

QUALIFICATION_PROJECT = "Ren Qualification"

MAIN_WINDOW = {
    "id": 9295,
    "title": QUALIFICATION_PROJECT,
    "frame": {"x": 0.0, "y": 33.0, "width": 1512.0, "height": 949.0},
    "backing_scale": 2.0,
    "is_main": True,
}

#: Four real 128x64 PNGs. The mock reports them at backing scale 2.0,
#: so a client capturing at point size must downscale to 64x32. The
#: capture counter cycles through them, so a loop that verifies an
#: action by diffing the before/after screenshot sees a change.
CAPTURE_WIDTH = 128.0
CAPTURE_HEIGHT = 64.0
CAPTURE_SCALE = 2.0
CAPTURE_PNGS = (
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAABACAIAAABdtOgoAAAAqElEQVR4nO3TMREAMAwDsbRAwh9KYRXGLxIBD38+b3fo3HAbAXoeEBMgJkBMgJgAMQFiAsQEiAkQEyAmQEyAmAAxAWICxASICRATICZATICYADEBYgLEBIgJEBMgJkBMgJgAMQFiAsQEiAkQEyAmQEyAmAAxAWICxASICRATICZATICYADEBYgLEBIgJEBMgJkBMgJgAMQFiAsQEiAkQEyAmQEyAmADT+jcIAYS+iDAeAAAAAElFTkSuQmCC",
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAABACAIAAABdtOgoAAAAqElEQVR4nO3TMREAMAwDsbRAwh9KYRXGLxIBD38++3bo3HAbAXoeEBMgJkBMgJgAMQFiAsQEiAkQEyAmQEyAmAAxAWICxASICRATICZATICYADEBYgLEBIgJEBMgJkBMgJgAMQFiAsQEiAkQEyAmQEyAmAAxAWICxASICRATICZATICYADEBYgLEBIgJEBMgJkBMgJgAMQFiAsQEiAkQEyAmQEyAmADT+jZeAYTJHvTBAAAAAElFTkSuQmCC",
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAABACAIAAABdtOgoAAAAqElEQVR4nO3TMREAMAwDsbRAwh9KYRXGLxIBD38+u2/o3HAbAXoeEBMgJkBMgJgAMQFiAsQEiAkQEyAmQEyAmAAxAWICxASICRATICZATICYADEBYgLEBIgJEBMgJkBMgJgAMQFiAsQEiAkQEyAmQEyAmAAxAWICxASICRATICZATICYADEBYgLEBIgJEBMgJkBMgJgAMQFiAsQEiAkQEyAmQEyAmADT+jW0AYQOzzJXAAAAAElFTkSuQmCC",
    "iVBORw0KGgoAAAANSUhEUgAAAIAAAABACAIAAABdtOgoAAAAqElEQVR4nO3TMREAMAwDsbRAyh9KYBWGlhcBD38+u2/iXLidAng9ACsAVgCsAFgBsAJgBcAKgBUAKwBWAKwAWAGwAmAFwAqAFQArAFYArABYAbACYAXACoAVACsAVgCsAFgBsAJgBcAKgBUAKwBWAKwAWAGwAmAFwAqAFQArAFYArABYAbACYAXACoAVACsAVgCsAFgBsAJgBcAKgBUAKwBWAKwAWAHG+iP0Ai7JtYFbAAAAAElFTkSuQmCC",
)

_capture_counter = 0

AX_TREE = {
    "ref": "ax-root",
    "role": "AXApplication",
    "title": "DaVinci Resolve",
    "children": [
        {
            "ref": "ax-menubar",
            "role": "AXMenuBar",
            "title": None,
            "children": [
                {
                    "ref": "ax-menu-workspace",
                    "role": "AXMenuBarItem",
                    "title": "Workspace",
                    "actions": ["AXPress"],
                    "children": [
                        {
                            "ref": "ax-item-switch",
                            "role": "AXMenuItem",
                            "title": "Switch to Page",
                            "actions": ["AXPress"],
                            "path": ["Workspace", "Switch to Page"],
                            "children": [
                                {
                                    "ref": "ax-item-media",
                                    "role": "AXMenuItem",
                                    "title": "Media",
                                    "actions": ["AXPress"],
                                    "path": ["Workspace", "Switch to Page", "Media"],
                                },
                                {
                                    "ref": "ax-item-edit",
                                    "role": "AXMenuItem",
                                    "title": "Edit",
                                    "actions": ["AXPress"],
                                    "path": ["Workspace", "Switch to Page", "Edit"],
                                },
                                {
                                    "ref": "ax-item-color",
                                    "role": "AXMenuItem",
                                    "title": "Color",
                                    "actions": ["AXPress"],
                                    "path": ["Workspace", "Switch to Page", "Color"],
                                },
                            ],
                        },
                        {
                            "ref": "ax-item-show-panel",
                            "role": "AXMenuItem",
                            "title": "Show Panel in Workspace",
                            "actions": ["AXPress"],
                            "path": ["Workspace", "Show Panel in Workspace"],
                            "children": [
                                {
                                    "ref": "ax-item-panel-effects",
                                    "role": "AXMenuItem",
                                    "title": "Effects",
                                    "actions": ["AXPress"],
                                    "path": ["Workspace", "Show Panel in Workspace", "Effects"],
                                },
                                {
                                    "ref": "ax-item-panel-inspector",
                                    "role": "AXMenuItem",
                                    "title": "Inspector",
                                    "actions": ["AXPress"],
                                    "path": ["Workspace", "Show Panel in Workspace", "Inspector"],
                                },
                            ],
                        },
                    ],
                },
                {
                    "ref": "ax-menu-help",
                    "role": "AXMenuBarItem",
                    "title": "Help",
                    "actions": ["AXPress"],
                    "children": [
                        {
                            "ref": "ax-field-search",
                            "role": "AXTextField",
                            "title": None,
                            "value": "",
                            "actions": ["AXPress"],
                        },
                    ],
                },
                {
                    "ref": "ax-menu-color",
                    "role": "AXMenuBarItem",
                    "title": "Color",
                    "actions": ["AXPress"],
                    "children": [
                        {
                            "ref": "ax-item-nodes",
                            "role": "AXMenuItem",
                            "title": "Nodes",
                            "actions": ["AXPress"],
                            "path": ["Color", "Nodes"],
                            "children": [
                                {
                                    "ref": "ax-item-add-serial",
                                    "role": "AXMenuItem",
                                    "title": "Add Serial Node",
                                    "actions": ["AXPress"],
                                    "path": ["Color", "Nodes",
                                             "Add Serial Node"],
                                },
                                {
                                    # Measured live 2026-10-06 on the
                                    # Color page of 'Ren Qualification'.
                                    "ref": "ax-item-node-cpw",
                                    "role": "AXMenuItem",
                                    "title": "Add Serial Node + CPW",
                                    "actions": ["AXPress"],
                                    "path": ["Color", "Nodes",
                                             "Add Serial Node + CPW"],
                                },
                                {
                                    "ref": "ax-item-node-delete",
                                    "role": "AXMenuItem",
                                    "title": "Delete Current Node",
                                    "actions": ["AXPress"],
                                    "path": ["Color", "Nodes",
                                             "Delete Current Node"],
                                },
                            ],
                        },
                        {
                            "ref": "ax-item-tracker",
                            "role": "AXMenuItem",
                            "title": "Tracker",
                            "actions": ["AXPress"],
                            "path": ["Color", "Tracker"],
                            "children": [
                                {
                                    "ref": "ax-item-track-forward",
                                    "role": "AXMenuItem",
                                    "title": "Forward",
                                    "actions": ["AXPress"],
                                    "path": ["Color", "Tracker",
                                             "Forward"],
                                },
                                {
                                    "ref": "ax-item-track-reverse",
                                    "role": "AXMenuItem",
                                    "title": "Reverse",
                                    "actions": ["AXPress"],
                                    "path": ["Color", "Tracker",
                                             "Reverse"],
                                },
                            ],
                        },
                    ],
                },
            ],
        },
    ],
}


def windows() -> list:
    """The window list, with the main window's title reading the open
    project - the real driver's title IS the project name (measured:
    the title read 'Ren Qualification' inside the borrow)."""
    return [dict(MAIN_WINDOW, title=_open_project())]


def _refuse(request_id, code: str, message: str) -> dict:
    return {"id": request_id, "ok": False, "error": {"code": code, "message": message}}


def _open_project() -> str:
    """The project the driver reads back - the gate's input.

    `MOCK_OPEN_PROJECT` moves it, which is how a test puts the captain's
    project open and watches the gate refuse.
    """
    return os.environ.get("MOCK_OPEN_PROJECT", QUALIFICATION_PROJECT)


def _find_by_path(path, title=None) -> list:
    """Walk the AX tree and return the elements the real driver's
    filter would match: `path` is the menu-title chain ABOVE the
    element, `title` the element's own title (absent matches any
    title under that chain - the same rule `find_element` applies
    live in native/resolve-control/src/driver.rs). A pair that
    matches nothing returns []."""
    if path is None:
        path = []
    matches = []

    def walk(node, ancestors):
        role = node.get("role")
        node_title = node.get("title")
        if ancestors == path and (title is None or node_title == title) and node_title:
            matches.append(node)
        here = (ancestors + [node_title]
                if role in ("AXMenuBarItem", "AXMenuItem") and node_title
                else ancestors)
        for child in node.get("children") or []:
            walk(child, here)

    walk(AX_TREE, [])
    return matches


def handle(request: dict) -> dict:
    global _capture_counter
    method = request.get("method", "")
    params = request.get("params") or {}
    request_id = request.get("id", 0)

    def reply(result: dict) -> dict:
        return {"id": request_id, "ok": True, "result": result}

    if method == "get_windows":
        return reply({"windows": windows()})
    if method == "get_frontmost_app":
        return reply({"bundle_id": "com.apple.finder", "name": "Finder"})
    if method == "get_accessibility_tree":
        return reply({"tree": AX_TREE})
    if method == "find_element":
        matches = _find_by_path(params.get("path"), params.get("title"))
        return reply({"elements": matches})
    if method == "run_command":
        if _open_project() != QUALIFICATION_PROJECT:
            return _refuse(
                request_id,
                "PROJECT_NOT_QUALIFICATION",
                f"the open Resolve project is {_open_project()!r}",
            )
        path = params.get("path") or []
        if not path or not all(path):
            return _refuse(request_id, "INVALID_PARAMS",
                           "run_command needs a path array of menu titles")
        matches = [n for n in _find_by_path(path[:-1], path[-1])
                   if n.get("role") == "AXMenuItem"
                   and "AXPress" in (n.get("actions") or [])]
        if not matches:
            return _refuse(request_id, "ELEMENT_NOT_FOUND",
                           f"no pressable element matches the menu path "
                           f"{path}")
        if len(matches) > 1:
            return _refuse(request_id, "AMBIGUOUS_COMMAND",
                           f"{len(matches)} elements match the menu path "
                           f"{path}")
        return reply({"pressed": True, "path": list(path)})
    if method in ("press", "set_value", "focus"):
        if _open_project() != QUALIFICATION_PROJECT:
            return _refuse(
                request_id,
                "PROJECT_NOT_QUALIFICATION",
                f"the open Resolve project is {_open_project()!r}",
            )
        if method == "press":
            return reply({"pressed": True})
        if method == "set_value":
            return reply({"set": True, "value": params.get("value")})
        return reply({"focused": True})
    if method == "click":
        if _open_project() != QUALIFICATION_PROJECT:
            return _refuse(
                request_id,
                "PROJECT_NOT_QUALIFICATION",
                f"the open Resolve project is {_open_project()!r}",
            )
        x, y = params.get("x"), params.get("y")
        frame = MAIN_WINDOW["frame"]
        if x is None or y is None:
            return _refuse(request_id, "INVALID_PARAMS", "click needs x and y")
        if not (
            frame["x"] <= x <= frame["x"] + frame["width"]
            and frame["y"] <= y <= frame["y"] + frame["height"]
        ):
            return _refuse(request_id, "OUTSIDE_RESOLVE", f"click at ({x}, {y}) is outside the main window")
        return reply({"clicked": True, "at": [x, y], "clicks": params.get("clicks", 1)})
    if method == "drag":
        if _open_project() != QUALIFICATION_PROJECT:
            return _refuse(
                request_id,
                "PROJECT_NOT_QUALIFICATION",
                f"the open Resolve project is {_open_project()!r}",
            )
        keys = ("start_x", "start_y", "end_x", "end_y")
        if any(params.get(k) is None for k in keys):
            return _refuse(request_id, "INVALID_PARAMS", "drag needs start_x, start_y, end_x, end_y")
        return reply({"dragged": True, "from": [params["start_x"], params["start_y"]], "to": [params["end_x"], params["end_y"]]})
    if method == "scroll":
        if _open_project() != QUALIFICATION_PROJECT:
            return _refuse(
                request_id,
                "PROJECT_NOT_QUALIFICATION",
                f"the open Resolve project is {_open_project()!r}",
            )
        if params.get("x") is None or params.get("y") is None:
            return _refuse(request_id, "INVALID_PARAMS", "scroll needs x and y")
        return reply({"scrolled": True, "at": [params["x"], params["y"]], "delta": [params.get("delta_x", 0.0), params.get("delta_y", 0.0)]})
    if method == "key":
        if _open_project() != QUALIFICATION_PROJECT:
            return _refuse(
                request_id,
                "PROJECT_NOT_QUALIFICATION",
                f"the open Resolve project is {_open_project()!r}",
            )
        if not params.get("key"):
            return _refuse(request_id, "INVALID_PARAMS", "key needs a key name")
        return reply({"sent": True, "key": params["key"], "modifiers": params.get("modifiers", [])})
    if method == "type_text":
        if _open_project() != QUALIFICATION_PROJECT:
            return _refuse(
                request_id,
                "PROJECT_NOT_QUALIFICATION",
                f"the open Resolve project is {_open_project()!r}",
            )
        if not params.get("text"):
            return _refuse(request_id, "INVALID_PARAMS", "type_text needs text")
        return reply({"typed": len(params["text"]), "text": params["text"]})
    if method == "capture_window":
        _capture_counter += 1
        return reply({"image": CAPTURE_PNGS[_capture_counter % len(CAPTURE_PNGS)], "width": CAPTURE_WIDTH, "height": CAPTURE_HEIGHT, "scale": CAPTURE_SCALE})
    if method == "capture_region":
        x, y = params.get("x"), params.get("y")
        w, h = params.get("width"), params.get("height")
        if x is None or y is None or w is None or h is None:
            return _refuse(request_id, "INVALID_PARAMS", "capture_region needs x, y, width, height")
        _capture_counter += 1
        return reply({"image": CAPTURE_PNGS[_capture_counter % len(CAPTURE_PNGS)], "width": w, "height": h, "scale": CAPTURE_SCALE})
    if method == "wait_for":
        predicate = params.get("predicate") or {}
        pred_type = predicate.get("type")
        if pred_type == "seconds":
            time.sleep(min(float(predicate.get("duration", 0.0)), 0.05))
            return reply({"met": True, "elapsed": 0.0})
        if pred_type == "text_visible":
            return reply({"met": (predicate.get("text") or "") in "Master Media", "elapsed": 0.0})
        if pred_type == "element_visible":
            title = predicate.get("title") or ""
            title_contains = predicate.get("title_contains") or ""
            met = bool(title or title_contains)
            return reply({"met": met, "elapsed": 0.0})
        return _refuse(request_id, "INVALID_PARAMS", f"unknown predicate type: {pred_type}")
    if method == "read_text":
        scale = float(os.environ.get("MOCK_WINDOW_SCALE", "1.0"))
        base_frame = {"x": 21.98, "y": 96.66, "width": 46.15, "height": 10.99}
        frame = {k: v * scale for k, v in base_frame.items()}
        return reply(
            {
                "text": "Master Media",
                "elements": [
                    {
                        "text": "Master",
                        "confidence": 0.99,
                        "frame": frame,
                    }
                ],
            }
        )
    return _refuse(request_id, "INVALID_PARAMS", f"unknown method: {method}")


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        response = handle(request)
        if os.environ.get("MOCK_WRONG_ID"):
            response["id"] = request.get("id", 0) + 100
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
