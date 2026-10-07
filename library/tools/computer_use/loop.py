"""The observe-decide-act loop.

The loop is the caller observe.py and actions.py need: it builds an
observation, asks the model for an action, translates and drives it
against the driver, verifies the outcome, and repeats until the model
says done or a budget is exhausted.

Safety is inherited, not invented. The loop runs inside
`qualification_project.borrowed()` and holds one exclusive take lease
for the whole run - observe, think, act, verify - because anything
that runs between two steps against Resolve changes what the next
step sees (measured: a lost seed, disagreeing pixels). Releasing during
model inference so other agents interleave was the old rule; a UI take
does not interleave. A SkyLight refusal is recorded and returned to the
model for a fresh decision, so it can choose an accessible menu or AX
action. Other non-retryable refusals stop the run.

Verification is what makes the loop safe to run unattended: the loop
never assumes an action worked because the driver returned `{"clicked":
true}` - the driver's return is the event being posted, not the UI having
changed (the old process route measured `CGEventPostToPid` returning
success while changing nothing). Each action is verified by diffing the
observation before and after; an action that did not land is retried once.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field

from library.tools.computer_use.actions import Action, INPUT_CALLS
from library.tools.computer_use.client import DriverClient, DriverError
from library.tools.computer_use.observe import Observation, build_observation
from library.tools.resolve_lock import resolve_lease

#: The model sees an observation and returns one action dict (or a
#: terminal signal). The loop validates it against the schema.
Model = Callable[[Observation], dict]

#: Driver refusals the model may recover from by choosing a different
#: action. A non-retryable refusal (the project is not the qualification
#: project, the screen is locked, Resolve is not running) is not retried:
#: the model cannot fix it, and retrying would wedge the loop.
RETRYABLE_REFUSALS = frozenset({
    "ELEMENT_NOT_FOUND",
    "ELEMENT_STALE",
    "OUTSIDE_RESOLVE",
    "CAPTURE_FAILED",
    "OCR_FAILED",
    "INTERNAL",
})

# A refused SkyLight route is not retried with the same input event. It
# goes back to the model so it can select an AX/menu action instead.
REPLAN_REFUSALS = frozenset({"SKYLIGHT_UNAVAILABLE"})


@dataclass
class LoopResult:
    """What the loop did: the outcome and the steps it took.

    `outcome` is `done`, `need_input`, `budget_exhausted` or `failed`.
    `steps` records each action and its outcome (`ok` or `failed`).
    """

    outcome: str
    steps: list[dict] = field(default_factory=list)
    final_action: str = ""


def run_loop(
    client: DriverClient,
    model: Model,
    goal: str,
    max_steps: int = 20,
    recipe: str | None = None,
    store=None,
    layout: str | None = None,
    target: str = "resolve",
    pid: int | None = None,
    deadline: float | None = None,
    save_trace_as: str | None = None,
    skylight: bool = True,
) -> LoopResult:
    """Run the observe-decide-act loop on a goal.

    Per step: observe (read-only), decide (model inference), act,
    verify. The whole run holds one exclusive take lease on Resolve -
    think time included - so no snapshot, reset or sibling job runs
    between two steps (a reentrant no-op inside a verb's `ui_take`).
    Off Resolve no lease is taken: nothing is touched, so there is
    nothing to serialize against. `deadline` is the take's monotonic
    bound: past it the loop stops as `budget_exhausted` before asking
    the model again - time is a budget like steps and dollars.
    `target` selects whose window is observed and acted on. Resolve
    input calls use SkyLight unless `skylight=False`; Canva routes input
    through SkyLight because Chrome filters the process route.

    Where `recipe` names a stored recipe (and `store` is its
    `RecipeStore`), the recipe executes FIRST - the plan's section 4,
    known recipe before computer-use planning. The model backs the
    recipe's own LLM fallback, so a step whose anchors went stale is
    rescued and taught, not bounced to full planning. A recipe that
    commits ends the loop as done; an unknown name, a moved layout or
    a failed recipe falls through to the planner below, which
    discovers the route the recipe could not run.

    Where `save_trace_as` names a recipe and the planner (not a
    recipe) reaches done, the successful action trace is distilled
    into a candidate script and replayed before it is trusted
    (`distill.save_distilled`): a green replay saves it to `store`
    with zero successes, a red one keeps it out with the reason on
    the step history. Distillation never fails the task - the task
    is done; only the saving can fail, and it says so.
    """
    if recipe is not None and store is not None:
        from library.tools.computer_use.scripts import run_recipe

        ran = run_recipe(client, store, recipe, layout,
                         llm=lambda obs, step: model(obs))
        if ran is not None and ran.outcome == "done":
            return LoopResult(
                outcome="done",
                steps=[{**step, "via_recipe": recipe} for step in ran.steps],
                final_action=f"recipe:{recipe}",
            )
    history: list[dict] = []

    # One exclusive lease for the whole run on Resolve - think time
    # included. A reentrant no-op inside a verb's `ui_take`; the real
    # fence for a direct caller. Off Resolve no lease is taken.
    take = (resolve_lease("computer-use loop", exclusive=True)
            if target == "resolve" else nullcontext())
    with take:
        for step in range(max_steps):
            if deadline is not None and time.monotonic() >= deadline:
                return LoopResult(
                    outcome="budget_exhausted", steps=history,
                    final_action="budget_exhausted",
                )
            obs = build_observation(client, goal, history, step, target=target, pid=pid)
            action_dict = model(obs)
            action = Action.from_dict(action_dict)
            if action.is_terminal:
                if action.kind == "done":
                    _maybe_distill(history, goal, store, layout,
                                   save_trace_as)
                return LoopResult(
                    outcome=action.kind, steps=history, final_action=action.kind
                )

            outcome, refusal = _act_and_verify(
                client, action, obs, target, pid, skylight=skylight)
            entry = {"action": action.kind, "outcome": outcome,
                     "params": dict(action.params)}
            if refusal is not None:
                entry["refusal"] = refusal
            history.append(entry)
            if outcome == "failed" and refusal in REPLAN_REFUSALS:
                continue
            if outcome == "failed":
                return LoopResult(
                    outcome="failed", steps=history, final_action=action.kind
                )

    return LoopResult(outcome="budget_exhausted", steps=history)


def _maybe_distill(history: list[dict], goal: str,
                   store, layout: str | None,
                   save_trace_as: str | None) -> None:
    """Save the planner's route as a recipe, replay-gated.

    Reaching terminal-done means the planner did the work (a recipe
    that committed returns before the loop starts planning). A green
    replay saves the trace under `save_trace_as`; a red one appends
    the reason to the history and keeps the route out of the store.
    Never raises past the loop - distillation is bookkeeping, not
    the task.
    """
    if not save_trace_as or store is None:
        return
    try:
        from library.tools.computer_use.distill import (
            save_distilled, trace_to_script,
        )

        script = trace_to_script(goal, history)
        distilled = save_distilled(store, save_trace_as, script, layout)
    except Exception as exc:  # noqa: BLE001 - bookkeeping never fails a task
        history.append({"action": "distill", "outcome": "failed",
                        "reason": f"{type(exc).__name__}: {exc}"})
        return
    history.append({
        "action": "distill",
        "outcome": "ok" if distilled.passed else "failed",
        "reason": distilled.reason,
        "recipe": save_trace_as,
    })


def _targeted(
    method: str,
    params: dict,
    target: str,
    pid: int | None = None,
    *,
    skylight: bool = True,
) -> dict:
    """Route UI input at the chosen app, leaving AX/menu calls intact."""
    if target == "resolve":
        if skylight and method in INPUT_CALLS:
            return {**params, "allow_skylight": True}
        return params
    routed = {**params, "target": target, "allow_skylight": True}
    if pid is not None:
        routed["pid"] = pid
    return routed


def _drive(
    client: DriverClient,
    action: Action,
    target: str = "resolve",
    pid: int | None = None,
    *,
    skylight: bool = True,
) -> None:
    """Drive an action's translation against the client.

    The translation is a generator: it yields a `(method, params)` call,
    receives the driver's result, and yields the next. A `press` needs the
    `find_element` result to issue the `press` - the path is resolved at
    run time, never cached. A `for` loop over the generator would not send
    the result back, so the press would see `None` and refuse.
    """
    gen = action.translate(client)
    try:
        method, params = next(gen)
        while True:
            result = client.call(
                method, _targeted(method, params, target, pid,
                                  skylight=skylight))
            method, params = gen.send(result)
    except StopIteration:
        pass


def _act_and_verify(
    client: DriverClient,
    action: Action,
    previous_obs: Observation,
    target: str = "resolve",
    pid: int | None = None,
    *,
    skylight: bool = True,
) -> tuple[str, str | None]:
    """Act with the lease taken, then verify the outcome.

    The lease is taken per action and held through the post-action
    verification - the observation that confirms the action landed is
    itself a read that must see the state the action produced. The lease
    is released during model inference. A failing action - a driver
    refusal, or no observable change - is retried once. A SkyLight
    refusal ends this action immediately and returns to the model for an
    AX/menu replan. A non-retryable refusal fails the step immediately.
    Off Resolve the lease is skipped: no Resolve is touched, so there is
    nothing to serialize against.

    Returns `(outcome, refusal)` where `refusal` is the driver's error
    code if the step failed on a refusal, else `None`.
    """
    refusal: str | None = None
    for _attempt in range(2):
        try:
            if target == "resolve":
                with resolve_lease("computer-use"):
                    _drive(client, action, target, pid, skylight=skylight)
                    new_obs = build_observation(client, "", [], 0, target=target, pid=pid)
            else:
                _drive(client, action, target, pid, skylight=skylight)
                new_obs = build_observation(client, "", [], 0, target=target, pid=pid)
        except DriverError as exc:
            refusal = exc.driver_code
            if refusal in REPLAN_REFUSALS:
                return "failed", refusal
            if refusal not in RETRYABLE_REFUSALS:
                return "failed", refusal
            continue
        if _changed(previous_obs, new_obs):
            return "ok", None
    return "failed", refusal


def _changed(old: Observation, new: Observation) -> bool:
    """Did the observation change?

    A screenshot, AX surface or OCR diff means the action landed; no
    change means it may not have. The screenshot is the coarsest signal
    - a page switch, a menu opening, a dialog all change it - and the AX
    surface and OCR labels catch changes the screenshot's downscale
    might miss.
    """
    return (
        old.screenshot != new.screenshot
        or old.ax_surface != new.ax_surface
        or old.ocr_labels != new.ocr_labels
    )
