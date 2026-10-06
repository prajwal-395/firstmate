"""The action vocabulary the model may return, translated into driver calls.

The model never touches Resolve. It returns one structured action - a
flat dict `{"action": <kind>, ...params}` - and this module validates it
against the schema and translates it into the driver calls that carry it
out. The driver's gates apply unchanged: the qualification project,
background input, the locked screen. The loop (PR 3) drives the
translation; this module owns the vocabulary and nothing else.

The vocabulary is the driver's method set, nothing more:

    press      an AX menu path - {"action": "press", "path": ["Workspace", "Switch to Page", "Media"]}
    click      a window point - {"action": "click", "x": 100.0, "y": 200.0}
    key        a keystroke - {"action": "key", "key": "shift+4"}
    type       text into the focused element - {"action": "type", "text": "render"}
    drag       between two points - {"action": "drag", "start_x": ..., "start_y": ..., "end_x": ..., "end_y": ...}
    scroll     at a point - {"action": "scroll", "x": ..., "y": ..., "delta_x": ..., "delta_y": ...}
    wait       a fixed duration - {"action": "wait", "seconds": 2.0}
    screenshot a fresh capture - {"action": "screenshot"}

Three terminal signals end the loop: `done`, `need_input`,
`budget_exhausted`. They translate to no driver call.

Unknown actions are refused, not guessed - a model that invents an action
must be told, not silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generator

from library.tools.computer_use.client import DriverClient, DriverError

#: The actions the model may return, and the driver calls behind them.
ACTION_KINDS = ("press", "click", "key", "type", "drag", "scroll",
                "wait", "screenshot")

#: Terminal signals - they end the loop and translate to no driver call.
TERMINAL_KINDS = ("done", "need_input", "budget_exhausted")

#: The driver's modifier vocabulary. A model that names another spelling
#: is refused, not guessed.
MODIFIERS = ("cmd", "shift", "option", "ctrl", "fn")

#: Params each action kind requires. A missing param is a model error,
#: refused here - the driver's own INVALID_PARAMS would be a later,
#: less clear refusal.
REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "press": ("path",),
    "click": ("x", "y"),
    "key": ("key",),
    "type": ("text",),
    "drag": ("start_x", "start_y", "end_x", "end_y"),
    "scroll": ("x", "y"),
    "wait": ("seconds",),
    "screenshot": (),
}


class ActionError(ValueError):
    """The model returned an action the schema does not allow."""


@dataclass
class Action:
    """One validated action the model returned.

    `params` holds the action's parameters after validation - the path,
    the coordinates, the key. Translation is a generator because `press`
    is two driver calls: `find_element` to resolve the path to a ref, then
    `press` with the ref the driver issued. The generator yields a
    `(method, params)` call, receives the driver's result, and yields the
    next - the loop drives it.
    """

    kind: str
    params: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Action:
        """Validate the model's action dict against the schema.

        Raises `ActionError` on an unknown kind, a missing param, or a
        param of the wrong shape - the model is told what it may return,
        and an action outside that vocabulary is a model error, not
        something to guess at.
        """
        if not isinstance(data, dict):
            raise ActionError(f"an action is a dict, got {type(data).__name__}")
        kind = data.get("action")
        if not isinstance(kind, str):
            raise ActionError("an action needs an 'action' kind")
        if kind in TERMINAL_KINDS:
            return cls(kind=kind)
        if kind not in ACTION_KINDS:
            raise ActionError(
                f"unknown action {kind!r} - the vocabulary is: "
                + ", ".join(ACTION_KINDS + TERMINAL_KINDS)
            )
        params = {k: v for k, v in data.items() if k != "action"}
        missing = [p for p in REQUIRED_PARAMS[kind] if p not in params]
        if missing:
            raise ActionError(
                f"action {kind!r} needs {', '.join(REQUIRED_PARAMS[kind])} - "
                f"missing: {', '.join(missing)}"
            )
        _check_params(kind, params)
        return cls(kind=kind, params=params)

    @property
    def is_terminal(self) -> bool:
        return self.kind in TERMINAL_KINDS

    def translate(
        self, client: DriverClient
    ) -> Generator[tuple[str, dict], dict, None]:
        """Yield `(method, params)` driver calls for this action.

        Each yielded call is made against `client`; the driver's result is
        sent back into the generator. A `press` yields `find_element`
        first and `press` second, with the ref the driver issued - the
        path is resolved at run time, never cached. A terminal signal
        yields nothing.
        """
        if self.is_terminal:
            return
        if self.kind == "press":
            path = self.params["path"]
            found = yield ("find_element", {"path": list(path)})
            elements = found.get("elements") if isinstance(found, dict) else None
            if not elements:
                raise DriverError(
                    "ELEMENT_NOT_FOUND",
                    f"no element matches the menu path {list(path)}",
                )
            yield ("press", {"element": elements[0]["ref"]})
        elif self.kind == "click":
            yield ("click", {"x": self.params["x"], "y": self.params["y"]})
        elif self.kind == "key":
            key, modifiers = parse_key(self.params["key"])
            yield ("key", {"key": key, "modifiers": modifiers})
        elif self.kind == "type":
            yield ("type_text", {"text": self.params["text"]})
        elif self.kind == "drag":
            yield (
                "drag",
                {
                    "start_x": self.params["start_x"],
                    "start_y": self.params["start_y"],
                    "end_x": self.params["end_x"],
                    "end_y": self.params["end_y"],
                },
            )
        elif self.kind == "scroll":
            yield (
                "scroll",
                {
                    "x": self.params["x"],
                    "y": self.params["y"],
                    "delta_x": self.params.get("delta_x", 0.0),
                    "delta_y": self.params.get("delta_y", 0.0),
                },
            )
        elif self.kind == "wait":
            yield (
                "wait_for",
                {"predicate": {"type": "seconds", "duration": self.params["seconds"]}},
            )
        elif self.kind == "screenshot":
            yield ("capture_window", {})


def parse_key(combo: str) -> tuple[str, list[str]]:
    """Parse a key combination into the driver's `(key, modifiers)`.

    `"shift+4"` -> `("4", ["shift"])`; `"cmd+shift+t"` -> `("t", ["cmd",
    "shift"])`; `"return"` -> `("return", [])`. The driver refuses a key
    name it does not know, so an unknown modifier is refused here with the
    vocabulary named - a clearer error than the driver's INVALID_PARAMS.
    """
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ActionError("a key combination is empty")
    key = parts[-1]
    modifiers = parts[:-1]
    unknown = [m for m in modifiers if m not in MODIFIERS]
    if unknown:
        raise ActionError(
            f"unknown modifier(s) {', '.join(unknown)} - the modifiers are: "
            + ", ".join(MODIFIERS)
        )
    return key, modifiers


def _check_params(kind: str, params: dict) -> None:
    """The per-kind shape checks beyond presence."""
    if kind == "press":
        path = params["path"]
        if not isinstance(path, list) or not path or not all(
            isinstance(p, str) and p for p in path
        ):
            raise ActionError("press path is a non-empty list of menu titles")
    elif kind in ("click", "scroll"):
        for name in ("x", "y"):
            if not isinstance(params[name], (int, float)):
                raise ActionError(f"{kind} {name} is a number")
    elif kind == "key":
        if not isinstance(params["key"], str) or not params["key"]:
            raise ActionError("key is a non-empty string")
        parse_key(params["key"])
    elif kind == "type":
        if not isinstance(params["text"], str):
            raise ActionError("type text is a string")
    elif kind == "drag":
        for name in ("start_x", "start_y", "end_x", "end_y"):
            if not isinstance(params[name], (int, float)):
                raise ActionError(f"drag {name} is a number")
    elif kind == "wait":
        seconds = params["seconds"]
        if not isinstance(seconds, (int, float)) or seconds < 0:
            raise ActionError("wait seconds is a non-negative number")
