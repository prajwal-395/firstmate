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
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Optional

from library.tools.computer_use.client import DriverClient, DriverError
from library.tools.computer_use.observe import Observation, build_observation
from library.tools.ren_refusal import RenRefusal
from library.tools.resolve_lock import resolve_lease

#: The anchor kinds, in priority order. A script lists its anchors in
#: this order; the runner tries them in the order declared.
ANCHOR_VIAS = ("ax_path", "ocr", "template", "offset", "position")

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
    tries the next anchor, then falls back to the LLM.
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
    if anchor.via == "ax_path":
        path = anchor.params.get("path")
        if not path:
            return None
        found = client.call("find_element", {"path": list(path)})
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
                return ("click", {"x": x, "y": y})
        return None
    if anchor.via == "position":
        x, y = anchor.params.get("x"), anchor.params.get("y")
        if x is None or y is None:
            return None
        params: dict = {"x": x, "y": y}
        if anchor.params.get("clicks") is not None:
            params["clicks"] = anchor.params["clicks"]
        return ("click", params)
    if anchor.via == "template":
        return _resolve_template(client, anchor)
    if anchor.via == "offset":
        return _resolve_offset(client, anchor)
    return None


def _resolve_template(client: DriverClient, anchor: Anchor) -> tuple[str, dict] | None:
    """Match an image template against a capture. For the Qt UI's icon
    controls, which are neither AX-reachable nor OCR-readable."""
    import cv2
    import numpy as np

    image_path = anchor.params.get("image")
    if not image_path:
        return None
    from PIL import Image

    with Image.open(image_path) as template:
        template_np = np.array(template.convert("RGB"))
    capture = client.call("capture_window")
    from PIL import Image

    with Image.open(BytesIO(base64.b64decode(capture["image"]))) as img:
        capture_np = np.array(img.convert("RGB"))
    result = cv2.matchTemplate(capture_np, template_np, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    threshold = float(anchor.params.get("threshold", 0.9))
    if max_val < threshold:
        return None
    h, w = template_np.shape[:2]
    x = max_loc[0] + w / 2
    y = max_loc[1] + h / 2
    return ("click", {"x": x, "y": y})


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
        return (
            before.screenshot != after.screenshot
            or before.ax_surface != after.ax_surface
            or before.ocr_labels != after.ocr_labels
        )
    return False


def run_script(
    client: DriverClient,
    script: Script,
    llm: Callable[[Observation, ScriptStep], dict] | None = None,
) -> ScriptResult:
    """Run a script against the client.

    Per step: try each anchor in declared order, resolve it fresh, execute
    the action, verify the expected outcome. If no anchor resolves and
    verifies, fall back to the LLM (if provided) - the LLM's action is
    recorded back into the script as a new anchor, so the next run is
    deterministic. If the LLM also fails, the script aborts with the
    failure named.
    """
    results: list[dict] = []
    for index, step in enumerate(script.steps):
        outcome = _run_step(client, step, index, llm, script)
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
    position, a `double_click` the position with both clicks. A
    `set_value` teaches nothing - the anchor layer has no value write,
    and teaching its path as an AX press would open the control instead
    of setting it. An action with no deterministic anchor (key, type,
    drag, scroll, wait) teaches nothing - recording a fake position
    would make the next run click the screen origin.
    """
    if action.kind in ("press", "choose"):
        step.anchors.append(
            Anchor(via="ax_path", params={"path": list(action.params["path"])})
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
) -> Optional[ScriptResult]:
    """Execute the named recipe BEFORE any computer-use planning.

    Unknown name, unreadable record or a layout the recipe was not
    verified on returns None - the caller plans instead. A recipe
    that runs records its own result toward promotion, so the store
    learns which routes hold without the planner watching.
    """
    loaded = store.load(name)
    if loaded is None:
        return None
    script, record = loaded
    if record.get("layout") != layout:
        return None
    result = run_script(client, script)
    store.record_result(name, result.outcome == "done")
    return result
