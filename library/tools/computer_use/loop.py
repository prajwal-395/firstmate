"""The observe-decide-act loop.

The loop is the caller observe.py and actions.py need: it builds an
observation, asks the model for an action, translates and drives it
against the driver, verifies the outcome, and repeats until the model
says done or a budget is exhausted.

Safety is inherited, not invented. The loop runs inside
`qualification_project.borrowed()` and takes the resolve lease per action,
releasing it during model inference so the captain's other agents
interleave with LLM think time - the lease is a critical section, not a
build. A refusal from the driver is propagated to the model as an
observation, never worked around. The loop can do anything the driver
allows and nothing the driver refuses.

Verification is what makes the loop safe to run unattended: the loop
never assumes an action worked because the driver returned `{"clicked":
true}` - the driver's return is the event being posted, not the UI having
changed (the probe measured `CGEventPostToPid` returning success while
changing nothing). Each action is verified by diffing the observation
before and after; an action that did not land is retried once, then
escalated.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from library.tools.computer_use.actions import Action
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
) -> LoopResult:
    """Run the observe-decide-act loop on a goal.

    Per step: observe (read-only), decide (model inference, lease
    released), act (lease taken), verify (lease taken). A failing action
    is retried once, then escalated. The loop stops at a terminal signal,
    a failed action, or the step budget. Driver refusals are recorded in
    history so the model sees them; non-retryable refusals fail the step
    immediately without a retry.
    """
    history: list[dict] = []

    for step in range(max_steps):
        obs = build_observation(client, goal, history, step)
        action_dict = model(obs)
        action = Action.from_dict(action_dict)
        if action.is_terminal:
            return LoopResult(
                outcome=action.kind, steps=history, final_action=action.kind
            )

        outcome, refusal = _act_and_verify(client, action, obs)
        entry = {"action": action.kind, "outcome": outcome}
        if refusal is not None:
            entry["refusal"] = refusal
        history.append(entry)
        if outcome == "failed":
            return LoopResult(
                outcome="failed", steps=history, final_action=action.kind
            )

    return LoopResult(outcome="budget_exhausted", steps=history)


def _drive(client: DriverClient, action: Action) -> None:
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
            result = client.call(method, params)
            method, params = gen.send(result)
    except StopIteration:
        pass


def _act_and_verify(
    client: DriverClient, action: Action, previous_obs: Observation
) -> tuple[str, str | None]:
    """Act with the lease taken, then verify the outcome.

    The lease is taken per action and held through the post-action
    verification - the observation that confirms the action landed is
    itself a read that must see the state the action produced. The lease
    is released during model inference. A failing action - a driver
    refusal, or no observable change - is retried once, then escalated.
    A non-retryable refusal fails the step immediately without a retry.

    Returns `(outcome, refusal)` where `refusal` is the driver's error
    code if the step failed on a refusal, else `None`.
    """
    refusal: str | None = None
    for _attempt in range(2):
        try:
            with resolve_lease("computer-use"):
                _drive(client, action)
                new_obs = build_observation(client, "", [], 0)
        except DriverError as exc:
            refusal = exc.driver_code
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
