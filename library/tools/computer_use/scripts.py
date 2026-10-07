"""Deterministic UI scripts: anchors re-resolved at run time, per-step
verification, LLM fallback that teaches.

The captain's second use: the LLM plans and mapping scripts that adapt to
window size. A script is a JSON document - a list of steps, each naming
its target with EVERY anchor that can resolve it, in priority order. The
runner re-resolves each step's target fresh before acting: the window may
have moved or been resized between the mapping run and this run, so no
coordinate is cached across steps.

Anchors, in priority order:

    ax_path     a menu path - the most robust; background, lease-free for
                reads, no pixels. Resolved by `find_element(path=...)`.
                With a `value` param it SETS instead of pressing (a
                numeric Fairlight control); the write takes the lease
                like every other act. The value is a string the recipe
                formatted - a non-string fails fast.
    ocr         a text label - resolved by `read_text`, matched on the
                label's measured frame. Adapts to window size because the
                frame is measured at run time in window points.
    template    an image template - matched against a capture. For the Qt
                UI's icon controls, which are neither AX-reachable nor
                OCR-readable.
    offset      a offset from another anchor - for controls adjacent to a
                labeled one.
    position    an absolute window point - the last resort, allowed only
                with a `why` (a step says what it could not determine).

Per-step verification: each step declares its expected outcome, and a
step that does not meet it is refused - the runner tries the next anchor,
then falls back to the LLM. The LLM's action is recorded back into the
script as a new anchor: the LLM teaches the script, and the next run is
deterministic.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Optional

from library.tools.computer_use.client import DriverClient, DriverError
from library.tools.computer_use.observe import Observation, build_observation
from library.tools.computer_use.take import TakeExpired
from library.tools.ren_refusal import RenRefusal
from library.tools.resolve_lock import resolve_lease

#: The anchor kinds, in priority order. A script lists its anchors in
#: this order; the runner tries them in the order declared. `menu` is
#: a whole menu path executed server-side (`run_command`) - it needs
#: no visible element and no focus, so it sits first: a step that CAN
#: name its menu should. `key` is a keystroke, not a screen location -
#: it resolves without touching the AX tree or the pixels, so it sits
#: last among the locators: a step that CAN anchor to something
#: visible should. `wait` is a settle, not a screen location - it
#: resolves to the driver's own wait, so a UI-settle need is said in
#: the script instead of slept around it.
ANCHOR_VIAS = ("menu", "ax_path", "ocr", "template", "offset", "position",
               "key", "type", "wait")

#: The verification kinds. An unknown kind is refused at parse time, never
#: silently passed at run time.
EXPECT_VIAS = ("text_visible", "element_visible", "pixels_changed", "seconds")

#: Successful runs before a learned route counts as a recipe (the plan's
#: section 6: "after several successful runs"). The source is the plan's
#: own word - three is the smallest count "several" names.
PROMOTE_AFTER = 3

#: Names the recipes root outright, for a machine whose layout differs.
RECIPES_ROOT_ENV = "PIPELINE_RECIPES_DIR"

#: A recipe name is a filename - lower-case slugs only, so a name can
#: never escape the store directory.
RECIPE_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

#: Driver calls that post input events. In the background they go to
#: Resolve's process alone - which the probe measured as changing
#: nothing on Qt-drawn controls - so a recipe step that must land on
#: one runs with `foreground=True` and the flag rides on exactly
#: these calls. Anything else (find_element, captures, press) is
#: unaffected: AX reaches the menu bar in the background.
INPUT_CALLS = frozenset({"click", "drag", "scroll", "key", "type_text"})


class ScriptError(RenRefusal):
    """A script file that could not be read or run - refused with the fix."""


@dataclass
class Anchor:
    """One way to resolve a step's target.

    `via` names the anchor kind; `params` carries its parameters. An
    anchor resolves to a `(method, params)` driver call, or `None` when it
    does not resolve (the path matches nothing, the label is not on
    screen).
    """

    via: str
    params: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Anchor:
        return cls(via=data["via"], params=dict(data.get("params") or {}))

    def to_dict(self) -> dict:
        return {"via": self.via, "params": dict(self.params)}


@dataclass
class Expect:
    """The step's expected outcome, verified after the action.

    A step that does not meet its expectation is refused: the runner
    tries the next anchor, then falls back to the LLM. `pixels_changed`
    takes an optional `region` (`[x, y, w, h]` in window points) and
    diffs that rect instead of the whole frame; without one the whole
    screenshot (plus the AX and OCR surfaces) is the signal. A step
    with no `expect` at all is verified by the driver accepting the
    action - which proves nothing about the effect, so takes that must
    prove an effect declare one.
    """

    via: str
    params: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Expect:
        via = data["via"]
        if via not in EXPECT_VIAS:
            raise ValueError(
                f"unknown expect via {via!r} - the vocabulary is: "
                + ", ".join(EXPECT_VIAS)
            )
        return cls(via=via, params=dict(data.get("params") or {}))


@dataclass
class ScriptStep:
    """One step: an action, its anchors in priority order, and its
    expected outcome."""

    action: str
    anchors: list[Anchor]
    expect: Expect | None = None

    @classmethod
    def from_dict(cls, data: dict) -> ScriptStep:
        return cls(
            action=data["action"],
            anchors=[Anchor.from_dict(a) for a in data.get("anchors") or []],
            expect=Expect.from_dict(data["expect"]) if data.get("expect") else None,
        )

    def to_dict(self) -> dict:
        out = {"action": self.action, "anchors": [a.to_dict() for a in self.anchors]}
        if self.expect:
            out["expect"] = {"via": self.expect.via, "params": dict(self.expect.params)}
        return out


@dataclass
class Script:
    """A deterministic UI script: a goal and a list of steps."""

    goal: str
    steps: list[ScriptStep]

    @classmethod
    def from_dict(cls, data: dict) -> Script:
        return cls(
            goal=data.get("goal", ""),
            steps=[ScriptStep.from_dict(s) for s in data.get("steps") or []],
        )

    def to_dict(self) -> dict:
        return {"goal": self.goal, "steps": [s.to_dict() for s in self.steps]}


@dataclass
class ScriptResult:
    """What the script did: the outcome and each step's result."""

    outcome: str
    steps: list[dict] = field(default_factory=list)


def load_script(path) -> Script:
    """Read a script file into a `Script`.

    A missing file, malformed JSON or an unknown expect kind is a
    `ScriptError` naming the file - the caller's input is wrong, which
    is a refusal with a fix, not a traceback.
    """
    from pathlib import Path

    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScriptError(
            f"the script file {file} could not be read",
            str(exc),
            f"pass a readable script JSON file: ren computer-use script <file>",
            code="SCRIPT_UNREADABLE",
        ) from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ScriptError(
            f"the script file {file} is not JSON",
            str(exc),
            f"fix the JSON in {file} and retry",
            code="SCRIPT_NOT_JSON",
        ) from exc
    try:
        script = Script.from_dict(data)
    except (ValueError, KeyError, AttributeError, TypeError) as exc:
        raise ScriptError(
            f"the script file {file} is not a script",
            str(exc),
            f"each step needs an action, anchors and a known expect via "
            f"({', '.join(EXPECT_VIAS)}) - fix {file} and retry",
            code="SCRIPT_INVALID",
        ) from exc
    if not script.steps:
        raise ScriptError(
            f"the script file {file} has no steps",
            "an empty script runs nothing",
            f"add at least one step to {file} and retry",
            code="SCRIPT_EMPTY",
        )
    return script


def resolve_anchor(client: DriverClient, anchor: Anchor) -> tuple[str, dict] | None:
    """Resolve an anchor to a `(method, params)` driver call, or `None`.

    Re-resolved fresh at run time - no cached coordinates across steps.
    The window may have moved or been resized between the mapping run and
    this run, so every anchor is resolved against the current state.
    """
    if anchor.via == "menu":
        path = anchor.params.get("path")
        if not path:
            return None
        return ("run_command", {"path": list(path)})
    if anchor.via == "wait":
        return _resolve_wait(anchor)
    if anchor.via == "ax_path":
        path = anchor.params.get("path")
        if not path:
            return None
        # The driver's filter matches `path` against the titles ABOVE
        # the element and `title` against the element itself
        # (native/resolve-control/src/driver.rs): sending the whole
        # chain as `path` resolves nothing live - the target's own
        # title is not its ancestor.
        *above, title = path
        found = client.call("find_element", {"path": list(above),
                                             "title": title})
        elements = found.get("elements") or []
        if not elements:
            return None
        if "value" in anchor.params:
            # A numeric control (a Fairlight EQ frequency field): set
            # instead of pressing. The value is a STRING, formatted by
            # the recipe - the runner never formats numbers, so a
            # non-string is a malformed script and fails fast rather
            # than skipping to the next anchor.
            value = anchor.params["value"]
            if not isinstance(value, str):
                raise ValueError(
                    f"ax_path set_value needs a string value, got "
                    f"{value!r} - format numbers at the recipe layer")
            return ("set_value", {"element": elements[0]["ref"],
                                  "value": value})
        return ("press", {"element": elements[0]["ref"]})
    if anchor.via == "ocr":
        text = anchor.params.get("text")
        if not text:
            return None
        ocr = client.call("read_text")
        for label in ocr.get("elements") or []:
            if label.get("text") == text:
                frame = label.get("frame") or {}
                x = frame.get("x", 0.0) + frame.get("width", 0.0) / 2
                y = frame.get("y", 0.0) + frame.get("height", 0.0) / 2
                params = {"x": x, "y": y}
                if anchor.params.get("clicks") is not None:
                    params["clicks"] = anchor.params["clicks"]
                return ("click", params)
        return None
    if anchor.via == "position":
        x, y = anchor.params.get("x"), anchor.params.get("y")
        if x is None or y is None:
            return None
        params: dict = {"x": x, "y": y}
        if anchor.params.get("clicks") is not None:
            params["clicks"] = anchor.params["clicks"]
        return ("click", params)
    if anchor.via == "key":
        return _resolve_key(anchor)
    if anchor.via == "type":
        return _resolve_type(anchor)
    if anchor.via == "template":
        return _resolve_template(client, anchor)
    if anchor.via == "offset":
        return _resolve_offset(client, anchor)
    return None


def _resolve_key(anchor: Anchor) -> tuple[str, dict] | None:
    """Resolve a keystroke anchor to a driver `key` call, or `None`.

    A keystroke anchors to nothing on screen, so it resolves without a
    driver call - the combo is parsed here, through the model's own
    `parse_key`, and a malformed combo RAISES (a recipe bug, not a
    layout that moved). An empty combo resolves to nothing, like every
    other anchor with no parameters.
    """
    from library.tools.computer_use.actions import parse_key

    combo = anchor.params.get("key")
    if not combo:
        return None
    key, modifiers = parse_key(str(combo))
    return ("key", {"key": key, "modifiers": modifiers})


def _resolve_type(anchor: Anchor) -> tuple[str, dict] | None:
    """Resolve a type anchor to a driver `type_text` call, or `None`.

    Typing reaches the FOCUSED element, so like a keystroke it anchors
    to nothing on screen and resolves without a driver call. An empty
    text resolves to nothing. The caller focuses first (usually an
    offset click into a field); this step only carries the text. The
    method is `type_text` - the driver's vocabulary, which is also
    what carries the foreground flag onto the call. A resolver that
    emitted anything else would fail every take at the driver gate
    (found wiring the seed recipe's search field: `type` is refused
    as an unknown method).
    """
    text = anchor.params.get("text")
    if not text:
        return None
    if not isinstance(text, str):
        raise ValueError(
            f"type needs a string text, got {text!r} - format numbers "
            f"at the recipe layer")
    return ("type_text", {"text": text})


def _resolve_wait(anchor: Anchor) -> tuple[str, dict] | None:
    """Resolve a settle anchor to a driver `wait_for` call, or `None`.

    A settle is a measured need, not a guess: some UI writes land
    asynchronously (a stepped node selection the delete must follow),
    and a script that sleeps around the runner hides that need where
    no log records it. An absent duration resolves to nothing, like
    every other anchor with no parameters; a malformed one RAISES -
    a recipe bug, not a layout that moved.
    """
    seconds = anchor.params.get("seconds")
    if seconds is None:
        return None
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) \
            or seconds < 0:
        raise ValueError(
            f"a wait anchor needs a non-negative seconds number, got "
            f"{seconds!r}")
    return ("wait_for", {"predicate": {"type": "seconds",
                                       "duration": float(seconds)}})


def _resolve_template(client: DriverClient, anchor: Anchor) -> tuple[str, dict] | None:
    """Match an image template against a capture. For the Qt UI's icon
    controls, which are neither AX-reachable nor OCR-readable.

    The anchor names the template by registry (`template:` - provenance,
    measured threshold and cut scale from `templates.py`) or by file
    (`image:` - the legacy direct path). A template with no file, no
    measured threshold, or no readable pixels resolves to nothing, so
    the step falls through to its next anchor; a threshold outside
    (0, 1) is a malformed recipe and fails fast. The crop is rescaled
    to the live capture's backing scale before matching, so one crop
    serves every display; without a recorded cut scale the crop is
    matched as is.
    """
    import cv2
    import numpy as np

    from library.tools.computer_use.templates import TemplateMissing

    threshold = anchor.params.get("threshold")
    template_np = None
    cut_scale = None
    if anchor.params.get("template"):
        from library.tools.computer_use.templates import load_template

        try:
            template_np, entry = load_template(anchor.params["template"])
        except TemplateMissing:
            return None
        cut_scale = entry.cut_scale
        if threshold is None:
            threshold = entry.threshold
    elif anchor.params.get("image"):
        from PIL import Image

        try:
            with Image.open(anchor.params["image"]) as template:
                template_np = np.array(template.convert("RGB"))
        except (OSError, ValueError):
            return None
        cut_scale = anchor.params.get("scale")
    else:
        return None
    if threshold is None:
        return None
    threshold = float(threshold)
    if not 0.0 < threshold < 1.0:
        raise ValueError(
            f"template threshold {threshold!r} is outside (0, 1) - "
            f"record the measured match score in templates.py, not a guess")
    if anchor.params.get("scale") is not None:
        cut_scale = float(anchor.params["scale"])
    capture = client.call("capture_window")
    from PIL import Image

    with Image.open(BytesIO(base64.b64decode(capture["image"]))) as img:
        capture_np = np.array(img.convert("RGB"))
    # Clicks take window points, but the capture (and the template cut
    # from it) is in backing pixels - measured live: an unscaled
    # template hit at 2x reads outside the window and the driver
    # refuses it OUTSIDE_RESOLVE. The capture carries its own scale.
    scale = float(capture.get("scale") or 1.0)
    if cut_scale is not None and float(cut_scale) != scale:
        factor = scale / float(cut_scale)
        template_np = cv2.resize(
            template_np, None, fx=factor, fy=factor,
            interpolation=cv2.INTER_AREA if factor < 1.0 else cv2.INTER_LINEAR)
    result = cv2.matchTemplate(capture_np, template_np, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None
    h, w = template_np.shape[:2]
    x = (max_loc[0] + w / 2) / scale
    y = (max_loc[1] + h / 2) / scale
    params: dict = {"x": x, "y": y}
    if anchor.params.get("clicks") is not None:
        params["clicks"] = anchor.params["clicks"]
    return ("click", params)


def _resolve_offset(client: DriverClient, anchor: Anchor) -> tuple[str, dict] | None:
    """Resolve an offset from another anchor. For controls adjacent to a
    labeled one."""
    base = anchor.params.get("anchor")
    if not base:
        return None
    resolved = resolve_anchor(client, Anchor.from_dict(base))
    if resolved is None:
        return None
    method, params = resolved
    if method != "click":
        return None
    dx = float(anchor.params.get("dx", 0.0))
    dy = float(anchor.params.get("dy", 0.0))
    return ("click", {"x": params["x"] + dx, "y": params["y"] + dy})


def verify_expect(
    client: DriverClient,
    expect: Expect | None,
    before: Observation | None = None,
) -> bool:
    """Verify a step's expected outcome. A step that does not declare an
    expectation is verified by the driver accepting the action.

    `pixels_changed` diffs the capture before the action against the
    capture after - the loop's `_changed` is the precedent. `seconds`
    returns the driver's own answer, never an unconditional pass.
    """
    if expect is None:
        return True
    if expect.via == "text_visible":
        result = client.call(
            "wait_for",
            {"predicate": {"type": "text_visible", "text": expect.params.get("text", "")}},
        )
        return bool(result.get("met"))
    if expect.via == "element_visible":
        predicate: dict = {"type": "element_visible"}
        if expect.params.get("role"):
            predicate["role"] = expect.params["role"]
        if expect.params.get("title"):
            predicate["title"] = expect.params["title"]
        if expect.params.get("title_contains"):
            predicate["title_contains"] = expect.params["title_contains"]
        result = client.call("wait_for", {"predicate": predicate})
        return bool(result.get("met"))
    if expect.via == "seconds":
        result = client.call(
            "wait_for",
            {"predicate": {"type": "seconds", "duration": expect.params.get("duration", 0.0)}},
        )
        return bool(result.get("met"))
    if expect.via == "pixels_changed":
        if before is None:
            return False
        after = build_observation(client, "", [], 0)
        if expect.params.get("region") is not None:
            return _region_changed(before.screenshot, after.screenshot,
                                   expect.params["region"])
        return (
            before.screenshot != after.screenshot
            or before.ax_surface != after.ax_surface
            or before.ocr_labels != after.ocr_labels
        )
    return False


def _region_changed(before_png: bytes, after_png: bytes,
                    region) -> bool:
    """Whether a window-point rect changed between two screenshots.

    `region` is `[x, y, w, h]` in window points; the observation
    screenshot is a point-size PNG, so points map 1:1 to its pixels.
    A malformed rect (non-positive size, negative origin) is a recipe
    bug and fails fast; a rect with no overlap with the image reads as
    no observable area, hence unchanged. Under a whole-take lease
    nothing but this take paints, so any change in the rect is this
    step's effect.
    """
    from PIL import Image

    try:
        x, y, w, h = (float(v) for v in region)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"pixels_changed region {region!r} is not [x, y, w, h]: {exc}"
        ) from exc
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        raise ValueError(
            f"pixels_changed region {region!r} needs a non-negative "
            f"origin and positive size")
    with Image.open(BytesIO(before_png)) as before_img:
        before_rgb = before_img.convert("RGB")
        width, height = before_rgb.size
        box = (int(x), int(y), min(int(x + w), width), min(int(y + h), height))
        if box[2] <= box[0] or box[3] <= box[1]:
            return False
        before_crop = before_rgb.crop(box)
    with Image.open(BytesIO(after_png)) as after_img:
        after_rgb = after_img.convert("RGB")
        if after_rgb.size != (width, height):
            return True
        after_crop = after_rgb.crop(box)
    return list(before_crop.getdata()) != list(after_crop.getdata())


def run_script(
    client: DriverClient,
    script: Script,
    llm: Callable[[Observation, ScriptStep], dict] | None = None,
    *,
    foreground: bool = False,
    deadline: float | None = None,
) -> ScriptResult:
    """Run a script against the client.

    Per step: try each anchor in declared order, resolve it fresh, execute
    the action, verify the expected outcome. If no anchor resolves and
    verifies, fall back to the LLM (if provided) - the LLM's action is
    recorded back into the script as a new anchor, so the next run is
    deterministic. If the LLM also fails, the script aborts with the
    failure named.

    `foreground` posts the input-event calls at the HID tap (the real
    cursor) instead of to Resolve's process alone. Background input
    changes nothing on Qt-drawn controls (measured), so a recipe that
    clicks one runs foreground - explicitly, never by default, because
    it takes the captain's screen, cursor or keyboard.

    `deadline` is the take's monotonic bound: past it the run raises
    `TakeExpired` before the next step instead of running past the
    take's wall-clock budget. A refusal, not a step failure - the
    steps did not fail, the take ran out of time.
    """
    results: list[dict] = []
    for index, step in enumerate(script.steps):
        if deadline is not None and time.monotonic() >= deadline:
            raise TakeExpired(
                f"the take ran past its bound at step {index}",
                "the steps did not fail - the take ran out of time",
                "raise the bound or split the take, then retry",
                code="TAKE_EXPIRED",
            )
        outcome = _run_step(client, step, index, llm, script,
                            foreground=foreground)
        results.append(outcome)
        if outcome["outcome"] == "failed":
            return ScriptResult(outcome="failed", steps=results)
    return ScriptResult(outcome="done", steps=results)


def _run_step(
    client: DriverClient,
    step: ScriptStep,
    index: int,
    llm: Callable[[Observation, ScriptStep], dict] | None,
    script: Script,
    *,
    foreground: bool = False,
) -> dict:
    """Run one step: try each anchor in order, then fall back to the LLM.

    The lease is taken per step's act+verify, the same way the loop's
    `_act_and_verify` does - the script drives the UI, so it needs the
    same exclusion the loop has.
    """
    for anchor in step.anchors:
        resolved = resolve_anchor(client, anchor)
        if resolved is None:
            continue
        method, params = resolved
        if foreground and method in INPUT_CALLS:
            params = {**params, "allow_foreground": True}
        try:
            with resolve_lease("computer-use"):
                before = build_observation(client, "", [], 0)
                client.call(method, params)
                if verify_expect(client, step.expect, before=before):
                    return {"step": index, "action": step.action, "outcome": "ok", "via": anchor.via}
        except DriverError:
            continue
    if llm is not None:
        return _llm_fallback(client, step, index, llm, script)
    return {"step": index, "action": step.action, "outcome": "failed", "via": "none"}


def _llm_fallback(
    client: DriverClient,
    step: ScriptStep,
    index: int,
    llm: Callable[[Observation, ScriptStep], dict],
    script: Script,
) -> dict:
    """Fall back to the LLM when no anchor resolves and verifies.

    The LLM sees the current observation and the failed step, and returns
    an action. The action is driven once (not twice - `_translate`
    collects without calling). On success, the anchor the LLM's action
    actually resolved is recorded back into the script - the LLM teaches
    the script, and the next run is deterministic. If the LLM's action
    also fails, the step fails.
    """
    obs = build_observation(client, script.goal, [], index)
    action_dict = llm(obs, step)
    if action_dict.get("action") in ("done", "need_input", "budget_exhausted"):
        return {"step": index, "action": step.action, "outcome": "failed", "via": "llm"}
    try:
        from library.tools.computer_use.actions import Action

        action = Action.from_dict(action_dict)
    except (DriverError, ValueError):
        return {"step": index, "action": step.action, "outcome": "failed", "via": "llm"}
    try:
        with resolve_lease("computer-use"):
            before = build_observation(client, "", [], 0)
            _drive(client, action)
            if not verify_expect(client, step.expect, before=before):
                return {"step": index, "action": step.action, "outcome": "failed", "via": "llm"}
    except DriverError:
        return {"step": index, "action": step.action, "outcome": "failed", "via": "llm"}
    _record_learned_anchor(step, action)
    return {"step": index, "action": step.action, "outcome": "ok", "via": "llm"}


def _drive(client: DriverClient, action) -> None:
    """Drive an action's translation against the client, once.

    The translation is a generator: it yields a `(method, params)` call,
    receives the driver's result, and yields the next. A `press` needs the
    `find_element` result to issue the `press` - the path is resolved at
    run time, never cached. Each call is made exactly once.
    """
    gen = action.translate(client)
    try:
        method, params = next(gen)
        while True:
            result = client.call(method, params)
            method, params = gen.send(result)
    except StopIteration:
        pass


def _record_learned_anchor(step: ScriptStep, action) -> None:
    """Record the anchor the LLM's action actually resolved.

    A `press` or `choose` teaches the AX path; a `click` teaches the
    position, a `double_click` the position with both clicks; a `key`
    teaches the keystroke. A `set_value` teaches nothing - the anchor
    layer has no value write, and teaching its path as an AX press
    would open the control instead of setting it. An action with no
    deterministic anchor (type, drag, scroll, wait) teaches nothing -
    recording a fake position would make the next run click the
    screen origin.
    """
    if action.kind in ("press", "choose"):
        step.anchors.append(
            Anchor(via="ax_path", params={"path": list(action.params["path"])})
        )
    elif action.kind == "key":
        step.anchors.append(
            Anchor(via="key", params={"key": str(action.params["key"])})
        )
    elif action.kind == "click":
        step.anchors.append(
            Anchor(
                via="position",
                params={
                    "x": action.params["x"],
                    "y": action.params["y"],
                    "why": "llm",
                },
            )
        )
    elif action.kind == "double_click":
        step.anchors.append(
            Anchor(
                via="position",
                params={
                    "x": action.params["x"],
                    "y": action.params["y"],
                    "clicks": 2,
                    "why": "llm",
                },
            )
        )


def default_recipes_root() -> Path:
    """The per-machine recipes directory.

    `PIPELINE_RECIPES_DIR` names it outright. Otherwise it lives under
    `vep_home()` - one shared location per machine, outside every
    checkout - so a disposable worktree never takes the learned routes
    with it.
    """
    explicit = os.environ.get(RECIPES_ROOT_ENV, "")
    if explicit:
        return Path(explicit).expanduser()
    from library.tools.shared_environment import vep_home

    return vep_home() / "recipes"


@dataclass
class RecipeStore:
    """Named, persisted, verified scripts - the plan's section 4.

    A verified script becomes a named recipe here; the loop executes
    the named recipe BEFORE any computer-use planning, and an unknown
    name (or a layout the recipe was not verified on) falls through
    to the planner. `layout` is an opaque string the caller supplies -
    the layout fingerprint where the observation carries one - compared
    by equality: same layout runs, a moved layout re-plans, and there
    is no middle that needs a cutoff. Successes count consecutive
    committed runs; a failure resets to zero, so a flaky route never
    promotes.
    """

    root: Path

    def _path(self, name: str) -> Path:
        if not RECIPE_NAME_RE.fullmatch(name):
            raise ValueError(
                f"a recipe name is a lower-case slug, got {name!r}")
        return self.root / f"{name}.json"

    def save(self, name: str, script: Script, layout: Optional[str]) -> Path:
        """Name a verified script. Overwrites the name it is given -
        re-verifying a route replaces the old one, and the success
        count restarts because the steps may have changed."""
        path = self._path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({
                "name": name,
                "layout": layout,
                "successes": 0,
                "script": script.to_dict(),
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    def load(self, name: str) -> Optional[tuple[Script, dict]]:
        """The named recipe, or None where no verified route exists.

        None is the planner's cue, not an error: an unknown name means
        the computer-use loop discovers the route.
        """
        try:
            raw = self._path(name).read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        try:
            record = json.loads(raw)
            script = Script.from_dict(record["script"])
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        return script, record

    def record_result(self, name: str, ok: bool) -> Optional[int]:
        """Count one execution toward (or against) promotion.

        A committed run increments the consecutive successes; anything
        else resets to zero. Returns the new count, or None where the
        name is not a stored recipe.
        """
        loaded = self.load(name)
        if loaded is None:
            return None
        script, record = loaded
        record["successes"] = int(record.get("successes") or 0) + 1 if ok else 0
        record["script"] = script.to_dict()
        self._path(name).write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return int(record["successes"])

    def is_promoted(self, name: str) -> bool:
        """A recipe with PROMOTE_AFTER consecutive committed runs."""
        loaded = self.load(name)
        if loaded is None:
            return False
        return int(loaded[1].get("successes") or 0) >= PROMOTE_AFTER


def run_recipe(
    client: DriverClient,
    store: RecipeStore,
    name: str,
    layout: Optional[str] = None,
    llm: Callable[[Observation, ScriptStep], dict] | None = None,
) -> Optional[ScriptResult]:
    """Execute the named recipe BEFORE any computer-use planning.

    Unknown name, unreadable record or a layout the recipe was not
    verified on returns None - the caller plans instead. A recipe
    that runs records its own result toward promotion, so the store
    learns which routes hold without the planner watching. Where
    `llm` is given, a step no anchor resolves falls back to it, and a
    committed run that learned new anchors saves the enriched script
    back under the name - the plan's section 6, a verified
    computer-use route recorded as a recipe. A run that taught
    nothing, or one that did not commit, leaves the stored route
    alone.
    """
    loaded = store.load(name)
    if loaded is None:
        return None
    script, record = loaded
    if record.get("layout") != layout:
        return None
    taught = sum(len(step.anchors) for step in script.steps)
    result = run_script(client, script, llm=llm)
    if (
        result.outcome == "done"
        and sum(len(step.anchors) for step in script.steps) > taught
    ):
        store.save(name, script, layout)
    store.record_result(name, result.outcome == "done")
    return result


# ── Grade-node recipes: the Resolve Hands Color slice ────────────────
#
# The edit algebra says `grade.node_set` works and node_add/remove/
# reorder do not, because the 21.1 Graph API exposes no topology
# changes. UI control fixes it: the write is GUI-only, the
# verification is API-readable (the captain's plan, section 11).
# These constructors are the deterministic recipes the plan's
# section 6 names - the route a mapping run would otherwise
# rediscover each time, written down once it is measured.

#: The Color page in the menu bar, outermost first. The page-switch
#: shape is measured live: `list_commands` on the running Resolve
#: answers the whole path, and `run_command` presses it
#: server-side - the Switch to Page submenu's children are AX-lazy
#: (absent until the menu opens), so `find_element` cannot resolve a
#: page path but `run_command` executes it.
GRADE_COLOR_PAGE_PATH = ["Workspace", "Switch to Page", "Color"]

#: Add Serial Node in the Color page menu, outermost first. Measured
#: live the same way: `Color > Nodes > Add Serial Node` is a pressable
#: command, and pressing it grew `GetNumNodes` 1 -> 2 on the
#: qualification timeline - all with another app frontmost, so the
#: whole write route is background-capable.
GRADE_NODE_ADD_PATH = ["Color", "Nodes", "Add Serial Node"]


def grade_node_add_script() -> Script:
    """The deterministic recipe for exactly one serial node.

    Two menu commands, executed server-side: establish the Color page,
    then press Add Serial Node. The recipe names NO viewer coordinate
    and NO keystroke: keys land in whatever app is frontmost (measured
    live - Shift+6 reached the browser, not Resolve), while the menu
    route reached Resolve with another app frontmost.

    Neither step declares a pixel or text expectation: a menu press
    has no screen predicate, and the one run where the page press
    answered pressed:true without switching still landed the node.
    The write is judged OUTSIDE the script, by re-reading
    `GetNumNodes` through the API - the count must grow by exactly
    one (`edit_algebra._apply_grade_node_add`). A step that declares
    no expectation is verified by the driver accepting the action.
    """
    return Script(
        goal="add one serial node on the Color page (grade.node_add)",
        steps=[
            ScriptStep(
                action="menu",
                anchors=[Anchor(
                    via="menu",
                    params={"path": list(GRADE_COLOR_PAGE_PATH)},
                )],
            ),
            ScriptStep(
                action="menu",
                anchors=[Anchor(
                    via="menu",
                    params={"path": list(GRADE_NODE_ADD_PATH)},
                )],
            ),
        ],
    )


# ── Effect-panels recipe: the Resolve Hands ResolveFX slice ──────────
#
# The captain's plan (section 13) applies an effect through the
# Effects Library and sets its parameters in the Inspector. Both
# panels open through menu commands, measured live 2026-10-07 in
# Ren Qualification under borrowed(): `run_command` answered
# pressed:true for each path on three consecutive borrows (all
# restored and read back), with another app frontmost and the
# display giving no image - the whole route is background-capable.
# The same runs showed background captures hold pre-switch pixels
# (an occluded Resolve does not repaint), so the search field, the
# effect rows and the Inspector controls get NO anchor here: their
# strings wait for a foreground mapping run, and a recipe that
# typed into whatever panel happened to be open would dress the
# wrong clip. This constructor is the panels half the apply route
# stands on.

#: The Edit page in the menu bar, outermost first. The Switch to
#: Page shape is the grade route's measurement (`GRADE_COLOR_PAGE_PATH`).
EFFECTS_EDIT_PAGE_PATH = ["Workspace", "Switch to Page", "Edit"]

#: The Effects Library panel toggle. Measured live: pressed:true in
#: Ren Qualification, background, no focus.
EFFECTS_PANEL_PATH = ["Workspace", "Show Panel in Workspace", "Effects"]

#: The Inspector panel toggle. Measured live the same way.
INSPECTOR_PANEL_PATH = ["Workspace", "Show Panel in Workspace",
                        "Inspector"]


def effect_panels_script() -> Script:
    """Open the Effects Library and Inspector panels on the Edit page.

    Three menu commands, executed server-side: establish the Edit
    page, then open each panel. The recipe names NO viewer coordinate
    and NO keystroke, and declares NO pixel or text expectation - a
    menu press has no screen predicate, and a step that declares
    none is verified by the driver accepting the action (the grade
    recipe's rule). What the panels SHOW once open - the search
    field, the effect rows, the parameter controls - is a later
    recipe step, mapped against foreground pixels, not here.
    """
    return Script(
        goal="open the Effects Library and Inspector panels (resolvefx)",
        steps=[
            ScriptStep(
                action="menu",
                anchors=[Anchor(
                    via="menu",
                    params={"path": list(EFFECTS_EDIT_PAGE_PATH)},
                )],
            ),
            ScriptStep(
                action="menu",
                anchors=[Anchor(
                    via="menu",
                    params={"path": list(EFFECTS_PANEL_PATH)},
                )],
            ),
            ScriptStep(
                action="menu",
                anchors=[Anchor(
                    via="menu",
                    params={"path": list(INSPECTOR_PANEL_PATH)},
                )],
            ),
        ],
    )
#: The node-stepping commands in the Color page menu, outermost
#: first. Live measurement: Previous/Next step one node per settled
#: press and WRAP around the ends (ten settled Previous presses on
#: four nodes deleted node 2, not node 1 - there is no saturation),
#: so stepping needs a KNOWN start. A press without settle silently
#: no-ops - the selection the delete must follow lands
#: asynchronously - so every press is followed by a settle step.
#: What the selection survives is also measured: page presses,
#: OpenPage cycles, timeline and clip switches all PRESERVE it, and
#: a delete leaves current at the same POSITION (the next node
#: shifts into it). The only known start is a just-added node, so
#: the remove recipe adds a marker serial first.
GRADE_NODE_PREV_PATH = ["Color", "Nodes", "Previous Node"]
GRADE_NODE_NEXT_PATH = ["Color", "Nodes", "Next Node"]
GRADE_NODE_DELETE_PATH = ["Color", "Nodes", "Delete Current Node"]

#: The settle between UI presses, in seconds. Measured live: a
#: delete immediately after its step removed nothing (3 -> 3
#: twice), while the same sequence with 3 s between presses
#: removed the stepped-to node. The source is that trial, on the
#: qualification timeline.
GRADE_STEP_SETTLE_SECONDS = 3.0


def _menu_step(path: list) -> ScriptStep:
    return ScriptStep(
        action="menu",
        anchors=[Anchor(via="menu", params={"path": list(path)})],
    )


def _settle_step(seconds: float = GRADE_STEP_SETTLE_SECONDS) -> ScriptStep:
    return ScriptStep(
        action="wait",
        anchors=[Anchor(via="wait", params={"seconds": seconds})],
    )

def _marker_add_script(
        settle_seconds: float = GRADE_STEP_SETTLE_SECONDS) -> Script:
    """Stage one: establish the page and add the marker serial.

    A just-added node is selected - the one known start stepping
    has. The apply verifies the count grew before stepping anywhere.
    """
    return Script(
        goal="add the marker serial (grade.node_remove stage one)",
        steps=[_menu_step(GRADE_COLOR_PAGE_PATH),
               _settle_step(settle_seconds),
               _menu_step(GRADE_NODE_ADD_PATH),
               _settle_step(settle_seconds)],
    )


def _remove_target_script(node_index: int, node_count: int,
                          settle_seconds: float = GRADE_STEP_SETTLE_SECONDS,
                          ) -> Script:
    """Stage two: step from the marker to the target, delete it.

    From marker position count + 1, Previous x (count + 1 - index)
    lands exactly on the target with no wrap encountered. The apply
    verifies the count and the target label before touching the
    marker.
    """
    steps = []
    for _ in range(node_count + 1 - node_index):
        steps += [_menu_step(GRADE_NODE_PREV_PATH),
                  _settle_step(settle_seconds)]
    steps += [_menu_step(GRADE_NODE_DELETE_PATH),
              _settle_step(settle_seconds)]
    return Script(
        goal=f"remove node {node_index} of {node_count}: take it",
        steps=steps,
    )


def _remove_marker_script(node_index: int, node_count: int,
                          settle_seconds: float = GRADE_STEP_SETTLE_SECONDS,
                          ) -> Script:
    """Stage three: step from the target's old position to the marker,
    delete it.

    The delete leaves current at the same position with the marker
    last, so Next x (count - index) reaches it with no wrap
    encountered. The apply verifies the count and the labels before
    restoring the originals.
    """
    steps = []
    for _ in range(node_count - node_index):
        steps += [_menu_step(GRADE_NODE_NEXT_PATH),
                  _settle_step(settle_seconds)]
    steps += [_menu_step(GRADE_NODE_DELETE_PATH),
              _settle_step(settle_seconds)]
    return Script(
        goal=f"remove node {node_index} of {node_count}: clear the marker",
        steps=steps,
    )


def grade_node_remove_script(node_index: int, node_count: int,
                             settle_seconds: float = GRADE_STEP_SETTLE_SECONDS,
                             ) -> Script:
    """The deterministic recipe for removing node `node_index`.

    All three stages as one script (the apply runs and verifies them
    separately): add a marker serial (the known start), step to the
    target, delete it, step to the marker, delete it. Every press
    carries its settle: the recipe says the measured need instead of
    sleeping around the runner. The recipe names NO viewer
    coordinate and NO keystroke - the whole route is server-side
    menu commands, background-capable with another app frontmost.

    No step declares a pixel or text expectation: a menu press has
    no screen predicate. The write is judged OUTSIDE the script, by
    re-reading the graph through the API - each stage's count must
    move by exactly one AND each removed position's label must be
    gone (`edit_algebra._apply_grade_node_remove`).
    """
    _check_remove_args(node_index, node_count)
    first = _marker_add_script(settle_seconds)
    second = _remove_target_script(node_index, node_count, settle_seconds)
    third = _remove_marker_script(node_index, node_count, settle_seconds)
    return Script(
        goal=f"remove node {node_index} of {node_count} on the Color "
             f"page (grade.node_remove)",
        steps=first.steps + second.steps + third.steps,
    )


def _check_remove_args(node_index: int, node_count: int) -> None:
    if (isinstance(node_index, bool) or not isinstance(node_index, int)
            or isinstance(node_count, bool)
            or not isinstance(node_count, int)):
        raise ValueError(
            f"a node_remove recipe needs integer index and count, got "
            f"{node_index!r} of {node_count!r}")
    if not 1 <= node_index <= node_count:
        raise ValueError(
            f"node {node_index} is outside 1..{node_count} - a recipe "
            f"for a node that is not there would delete whatever the "
            f"stepping lands on")


def run_settled_script(client: DriverClient, script: Script) -> ScriptResult:
    """Run a menu+wait script where waits sleep in-process.

    The driver's own waits need an unlocked screen (measured live:
    `wait_for` answers SCREEN_LOCKED while menu commands execute
    fine), so a wait step sleeps here instead of asking the driver -
    the duration stays declared on the step, and the step record
    carries it. Menu steps resolve and call exactly like the script
    runner's; anything else (or a refused resolve) fails the step,
    and a failed step fails the     run. One lease around the whole
    run: the caller already holds the exclusive instance lease, and
    the lease is reentrant.
    """
    results: list[dict] = []
    with resolve_lease("computer-use settled script"):
        for index, step in enumerate(script.steps):
            results.append(_run_settled_step(client, step, index))
            if results[-1]["outcome"] == "failed":
                return ScriptResult(outcome="failed", steps=results)
    return ScriptResult(outcome="done", steps=results)


def _run_settled_step(client: DriverClient, step: ScriptStep,
                      index: int) -> dict:
    """One settled step: menus call, waits sleep, anything else fails."""
    for anchor in step.anchors:
        if anchor.via == "wait":
            seconds = anchor.params.get("seconds")
            if not isinstance(seconds, (int, float)) \
                    or isinstance(seconds, bool) or seconds < 0:
                continue
            import time as _time

            _time.sleep(float(seconds))
            return {"step": index, "action": step.action,
                    "outcome": "ok", "via": "wait",
                    "seconds": float(seconds)}
        resolved = resolve_anchor(client, anchor)
        if resolved is None:
            continue
        method, params = resolved
        try:
            client.call(method, params)
        except DriverError:
            continue
        return {"step": index, "action": step.action,
                "outcome": "ok", "via": anchor.via}
    return {"step": index, "action": step.action,
            "outcome": "failed", "via": "none"}
