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


def _find_by_path(path) -> list:
    """Walk the AX tree and return the elements whose menu-title chain
    matches the path, outermost first. A path that matches nothing
    returns []."""
    if not path:
        return []
    matches = []

    def walk(node, chain):
        role = node.get("role")
        title = node.get("title")
        here = chain + [title] if role in ("AXMenuBarItem", "AXMenuItem") and title else chain
        if here == path:
            matches.append(node)
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
        path = params.get("path")
        matches = _find_by_path(path)
        return reply({"elements": matches})
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
