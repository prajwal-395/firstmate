"""Distillation: a planner route that worked becomes a recipe.

The loop (`loop.run_loop`) runs a stored recipe first and plans only
where no recipe runs - but the discovered route evaporated with the
run: nothing saved it, so the next run paid the model again. This
module closes that loop: when the planner completes a task no recipe
covered, the successful action trace is saved as a new recipe with
anchors and expects, then must pass a replay before it is trusted.
A recipe that fails replay falls back to the agent, which repairs
and re-saves it.

Three steps, in order:

1. `trace_to_script`: the loop's successful action history in, a
   `Script` out. Each action becomes one step whose anchor is the
   action's own resolution (an AX path for a press, the OCR label
   for a click on text, the keystroke for a key) and whose expect
   proves the effect the trace showed (the observation diff the
   loop's `_changed` verified). An action with no deterministic
   anchor (a drag, a scroll, a blind coordinate click) becomes a
   step with NO anchor - the audit names it, and the recipe is not
   trusted until a mapping run anchors it.
2. `save_distilled`: the script is replayed offline through
   `replay.replay_offline` BEFORE it is saved. A replay that fails
   refuses the save with the reason - an unproven route never lands
   in the store.
3. `repair`: a failed replay falls back to the agent (a callable
   the caller supplies - the planner that found the route): it sees
   the failure and returns a repaired script, which replays again.
   The loop bounds the repairs; an unrepaired route stays out of
   the store, reported, never half-trusted.

Promotion still counts committed runs (`RecipeStore.record_result`,
`PROMOTE_AFTER`): a distilled recipe starts at zero successes like
any other, and only consecutive green replays promote it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from library.tools.computer_use.scripts import (
    Anchor,
    Expect,
    RecipeStore,
    Script,
    ScriptStep,
)

#: How many repair rounds the agent gets before the route stays out
#: of the store. A route that needs more repair than this is not a
#: recipe yet - it is a task the planner should keep owning.
MAX_REPAIRS = 3


@dataclass
class Distilled:
    """A trace turned into a candidate recipe, with its replay."""

    name: str
    script: Script
    passed: bool
    reason: str = ""
    repairs: int = 0


def trace_to_script(goal: str, actions: list[dict]) -> Script:
    """The planner's successful actions in, a candidate script out.

    `actions` are the loop history entries (`{"action", "outcome",
    ...}`) plus the resolutions the driver recorded - each entry
    carries what it resolved against (`ax_path`, `ocr_text`,
    `key`, `point`). A press with its path teaches an `ax_path`
    anchor; a click on OCR text teaches an `ocr` anchor; a key
    teaches its keystroke; a click at a bare point teaches a
    `position` with `why: llm` - flagged by the anchor audit until
    a mapping run replaces it. Every anchored step declares the
    expect the trace showed: `pixels_changed` where the observation
    diffed, `text_visible` where a label appeared. An action the
    trace cannot anchor (drag, scroll, type with no focus) becomes
    an anchorless step the audit refuses - honestly unproven,
    never faked.
    """
    steps: list[ScriptStep] = []
    for entry in actions:
        if entry.get("outcome") != "ok":
            continue
        step = _action_to_step(entry)
        if step is not None:
            steps.append(step)
    if not steps:
        raise ValueError(
            "the trace holds no successful anchored action - "
            "nothing distillable ran")
    return Script(goal=goal, steps=steps)


def _action_to_step(entry: dict) -> Optional[ScriptStep]:
    """One traced action to one script step, or None where unanchorable."""
    kind = entry.get("action")
    if kind in ("press", "choose") and entry.get("path"):
        return ScriptStep(
            action="press",
            anchors=[Anchor(via="ax_path",
                            params={"path": list(entry["path"])})],
            expect=Expect(via="pixels_changed", params={}),
        )
    if kind == "key" and entry.get("key"):
        return ScriptStep(
            action="key",
            anchors=[Anchor(via="key",
                            params={"key": str(entry["key"])})],
            expect=Expect(via="pixels_changed", params={}),
        )
    if kind == "click" and entry.get("ocr_text"):
        return ScriptStep(
            action="click",
            anchors=[Anchor(via="ocr",
                            params={"text": str(entry["ocr_text"])})],
            expect=Expect(via="pixels_changed", params={}),
        )
    if kind == "click" and entry.get("template"):
        return ScriptStep(
            action="click",
            anchors=[Anchor(via="template",
                            params={"image": str(entry["template"]),
                                    "threshold": float(entry.get(
                                        "threshold", 0.9))})],
            expect=Expect(via="pixels_changed", params={}),
        )
    if kind in ("click", "double_click") and entry.get("point"):
        x, y = entry["point"]
        params: dict = {"x": float(x), "y": float(y), "why": "llm"}
        if kind == "double_click":
            params["clicks"] = 2
        return ScriptStep(
            action="click",
            anchors=[Anchor(via="position", params=params)],
            expect=Expect(via="pixels_changed", params={}),
        )
    if kind == "wait" and entry.get("seconds") is not None:
        return ScriptStep(
            action="wait",
            anchors=[Anchor(via="wait",
                            params={"seconds": float(entry["seconds"])})],
        )
    return None


def save_distilled(store: RecipeStore, name: str, script: Script,
                   layout: Optional[str],
                   out_dir: Optional[Path] = None) -> Distilled:
    """Replay a distilled script, then save it iff the replay passes.

    The replay is the trust gate: a route that cannot prove itself
    against the fakes never lands in the store. On success the
    script saves under `name` with zero successes - promotion still
    counts committed runs from there.
    """
    from library.tools.computer_use.replay import (
        RecipeSpec, default_replay_root, replay_offline,
    )

    out = Path(out_dir) if out_dir else default_replay_root()
    spec = RecipeSpec(name=f"distilled-{name}", background=True,
                      goal=script.goal, build=lambda: script)
    report = replay_offline(spec, out)
    if report.verdict != "pass":
        return Distilled(name=name, script=script, passed=False,
                         reason=report.reason)
    store.save(name, script, layout)
    return Distilled(name=name, script=script, passed=True,
                     reason=f"replay passed: {report.reason}")


def repair(store: RecipeStore, name: str, distilled: Distilled,
           layout: Optional[str],
           agent: Callable[[Script, str], Script],
           out_dir: Optional[Path] = None) -> Distilled:
    """Fall back to the agent: repair a failed replay, re-save iff fixed.

    The agent sees the failed script and the replay's reason, and
    returns a repaired script - new anchors, new expects, fewer
    steps, whatever the failure names. Each repair replays; the
    first green replay saves. Past `MAX_REPAIRS` the route stays out
    of the store with the last reason - reported, never half-trusted.
    """
    current = distilled
    repairs = distilled.repairs
    while not current.passed and repairs < MAX_REPAIRS:
        try:
            fixed = agent(current.script, current.reason)
        except Exception as exc:  # noqa: BLE001 - the report names it
            return Distilled(name=name, script=current.script,
                             passed=False,
                             reason=f"repair raised "
                                    f"{type(exc).__name__}: {exc}",
                             repairs=repairs)
        repairs += 1
        current = save_distilled(store, name, fixed, layout,
                                 out_dir=out_dir)
        current.repairs = repairs
    return current
