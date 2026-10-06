"""The loop's contract, pinned against the mock driver and a mock model.

Each test names the defect it catches: a behaviour that would be wrong if
the loop regressed. The lease discipline - released during inference,
re-taken per action - is the one a future change would break first,
because it looks like a harmless optimisation to hold the lease for the
whole loop.
"""

from __future__ import annotations

from contextlib import contextmanager

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.loop import run_loop


@contextmanager
def _tracking_lease(state: dict):
    """A mock resolve_lease that records whether it is held."""
    state["held"] = True
    state["takes"] = state.get("takes", 0) + 1
    yield
    state["held"] = False


def _press(path: list[str]) -> dict:
    return {"action": "press", "path": path}


def test_a_three_step_menu_navigation_task_completes(
    client: DriverClient, monkeypatch
):
    """A mock model + mock driver run a 3-step menu-navigation task:
    press Workspace, Switch to Page, Media, then done. Each press is
    accepted by the mock and the screenshot changes (the mock cycles
    captures), so every step verifies."""
    state: dict = {}
    monkeypatch.setattr(
        "library.tools.computer_use.loop.resolve_lease",
        lambda purpose, **kw: _tracking_lease(state),
    )
    actions = [
        _press(["Workspace"]),
        _press(["Workspace", "Switch to Page"]),
        _press(["Workspace", "Switch to Page", "Media"]),
        {"action": "done"},
    ]
    calls: list = []

    def model(obs):
        calls.append(obs)
        return actions[len(calls) - 1]

    result = run_loop(client, model, "switch to the Media page", max_steps=10)
    assert result.outcome == "done"
    assert len(result.steps) == 3
    assert all(step["outcome"] == "ok" for step in result.steps)
    assert [step["action"] for step in result.steps] == ["press", "press", "press"]


def test_the_lease_is_released_during_inference_and_re_taken_per_action(
    client: DriverClient, monkeypatch
):
    """The lease is a critical section, not a build: it is taken per
    action and released during model inference, so the captain's other
    agents interleave with LLM think time. A loop that held the lease for
    its whole run would wedge every other Resolve agent for the duration
    of the task."""
    state: dict = {"held": False, "takes": 0}
    monkeypatch.setattr(
        "library.tools.computer_use.loop.resolve_lease",
        lambda purpose, **kw: _tracking_lease(state),
    )
    actions = [_press(["Workspace"]), _press(["Help"]), {"action": "done"}]
    inference_leases: list[bool] = []

    def model(obs):
        inference_leases.append(state["held"])
        return actions[len(inference_leases) - 1]

    run_loop(client, model, "open two menus", max_steps=10)
    assert inference_leases == [False, False, False]
    assert state["takes"] == 2


def test_a_failing_action_is_retried_once_then_escalated(
    client: DriverClient, monkeypatch
):
    """A click the driver refuses (outside the window) is retried once,
    then the step fails and the loop escalates - it does not wedge, and
    it does not silently continue as if the click landed."""
    state: dict = {"held": False, "takes": 0}
    monkeypatch.setattr(
        "library.tools.computer_use.loop.resolve_lease",
        lambda purpose, **kw: _tracking_lease(state),
    )
    calls: list = []

    def model(obs):
        calls.append(obs)
        return {"action": "click", "x": 9999.0, "y": 9999.0}

    result = run_loop(client, model, "click off-window", max_steps=10)
    assert result.outcome == "failed"
    assert result.final_action == "click"
    assert len(result.steps) == 1
    assert result.steps[0]["outcome"] == "failed"
    assert state["takes"] == 2
    assert len(calls) == 1


def test_the_step_budget_stops_the_loop(client: DriverClient, monkeypatch):
    """A model that never says done stops at the step budget - a loop
    with no budget is a call that asks forever."""
    state: dict = {"held": False, "takes": 0}
    monkeypatch.setattr(
        "library.tools.computer_use.loop.resolve_lease",
        lambda purpose, **kw: _tracking_lease(state),
    )
    calls: list = []

    def model(obs):
        calls.append(obs)
        return _press(["Workspace"])

    result = run_loop(client, model, "loop forever", max_steps=3)
    assert result.outcome == "budget_exhausted"
    assert len(result.steps) == 3
    assert len(calls) == 3


def test_a_terminal_signal_ends_the_loop_without_acting(
    client: DriverClient, monkeypatch
):
    """`need_input` ends the loop before any action - the model is
    asking the captain something, and acting past it would be wrong."""
    state: dict = {"held": False, "takes": 0}
    monkeypatch.setattr(
        "library.tools.computer_use.loop.resolve_lease",
        lambda purpose, **kw: _tracking_lease(state),
    )
    calls: list = []

    def model(obs):
        calls.append(obs)
        return {"action": "need_input"}

    result = run_loop(client, model, "ask the captain", max_steps=10)
    assert result.outcome == "need_input"
    assert result.steps == []
    assert state["takes"] == 0
    assert len(calls) == 1


def test_a_non_retryable_refusal_fails_immediately_without_retry(
    client: DriverClient, monkeypatch
):
    """A non-retryable refusal (the open project is not the qualification
    project) fails the step immediately - the model cannot fix it, and
    retrying would wedge the loop. The refusal is recorded in history."""
    state: dict = {"held": False, "takes": 0}
    monkeypatch.setattr(
        "library.tools.computer_use.loop.resolve_lease",
        lambda purpose, **kw: _tracking_lease(state),
    )
    monkeypatch.setenv("MOCK_OPEN_PROJECT", "Captain's Project")
    calls: list = []

    def model(obs):
        calls.append(obs)
        return {"action": "press", "path": ["Workspace"]}

    result = run_loop(client, model, "press Workspace", max_steps=10)
    assert result.outcome == "failed"
    assert result.final_action == "press"
    assert len(result.steps) == 1
    assert result.steps[0]["outcome"] == "failed"
    assert result.steps[0]["refusal"] == "PROJECT_NOT_QUALIFICATION"
    assert state["takes"] == 1
    assert len(calls) == 1


def test_a_retryable_refusal_is_retried_once_then_escalated(
    client: DriverClient, monkeypatch
):
    """A retryable refusal (a click outside the window) is retried once,
    then the step fails. The model sees the refusal in its next
    observation and may choose a different action."""
    state: dict = {"held": False, "takes": 0}
    monkeypatch.setattr(
        "library.tools.computer_use.loop.resolve_lease",
        lambda purpose, **kw: _tracking_lease(state),
    )
    calls: list = []

    def model(obs):
        calls.append(obs)
        return {"action": "click", "x": 9999.0, "y": 9999.0}

    result = run_loop(client, model, "click off-window", max_steps=10)
    assert result.outcome == "failed"
    assert result.final_action == "click"
    assert len(result.steps) == 1
    assert result.steps[0]["outcome"] == "failed"
    assert result.steps[0]["refusal"] == "OUTSIDE_RESOLVE"
    assert state["takes"] == 2
    assert len(calls) == 1


def test_the_lease_is_held_for_post_action_verification(
    client: DriverClient, monkeypatch
):
    """The lease is held through the post-action verification - the
    observation that confirms the action landed is itself a read that
    must see the state the action produced. A loop that released the
    lease before verifying would race a foreign writer."""
    state: dict = {"held": False, "takes": 0}
    monkeypatch.setattr(
        "library.tools.computer_use.loop.resolve_lease",
        lambda purpose, **kw: _tracking_lease(state),
    )
    actions = [_press(["Workspace"]), {"action": "done"}]
    observation_leases: list[bool] = []
    model_calls: list = []

    def model(obs):
        model_calls.append(obs)
        return actions[len(model_calls) - 1]

    import library.tools.computer_use.loop as loop_module
    original_build = loop_module.build_observation

    def tracking_build(client, goal, history, step):
        observation_leases.append(state["held"])
        return original_build(client, goal, history, step)

    monkeypatch.setattr(loop_module, "build_observation", tracking_build)

    run_loop(client, model, "press Workspace", max_steps=10)
    assert observation_leases[0] is False
    assert observation_leases[1] is True
