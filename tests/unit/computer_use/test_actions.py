"""The action schema's contract, pinned against the mock driver.

The acceptance test is the property over the schema: every action the
schema allows translates into driver calls the driver accepts. Each test
names the defect it catches - a translation that dropped a parameter, a
press that skipped the find_element, an action the schema refused to
guess at.
"""

from __future__ import annotations

import pytest

from library.tools.computer_use.actions import (
    ACTION_KINDS,
    Action,
    ActionError,
    parse_key,
)
from library.tools.computer_use.client import DriverClient, DriverError

#: One valid action per kind, with coordinates inside the mock's main
#: window (0,33 1512x949). The property test drives each through the
#: translation and asserts the driver accepts every call.
VALID_ACTIONS: dict[str, dict] = {
    "press": {"action": "press", "path": ["Workspace", "Switch to Page", "Media"]},
    "choose": {"action": "choose", "path": ["Workspace", "Switch to Page", "Media"]},
    "set_value": {
        "action": "set_value",
        "path": ["Workspace", "Switch to Page", "Media"],
        "value": "Person",
    },
    "click": {"action": "click", "x": 100.0, "y": 100.0},
    "double_click": {"action": "double_click", "x": 100.0, "y": 100.0},
    "key": {"action": "key", "key": "shift+4"},
    "type": {"action": "type", "text": "render"},
    "drag": {
        "action": "drag",
        "start_x": 10.0,
        "start_y": 10.0,
        "end_x": 50.0,
        "end_y": 50.0,
    },
    "scroll": {"action": "scroll", "x": 100.0, "y": 100.0, "delta_y": -3.0},
    "wait": {"action": "wait", "seconds": 0.01},
    "screenshot": {"action": "screenshot"},
}


def drive(client: DriverClient, action: Action) -> list[tuple[str, dict]]:
    """Drive an action's translation against the client.

    The generator yields a `(method, params)` call, the client makes it,
    and the driver's result goes back into the generator - a press needs
    the find_element result to issue the press. Returns the calls made, in
    order, so a test can assert the translation's shape.
    """
    calls: list[tuple[str, dict]] = []
    gen = action.translate(client)
    try:
        method, params = next(gen)
        while True:
            calls.append((method, params))
            result = client.call(method, params)
            method, params = gen.send(result)
    except StopIteration:
        pass
    return calls


@pytest.mark.parametrize("kind", sorted(ACTION_KINDS))
def test_every_action_the_schema_allows_translates_to_calls_the_driver_accepts(
    client: DriverClient, kind: str
):
    """The property over the schema: each valid action drives to
    completion against the mock with no refusal. A translation that
    emitted a call the driver refuses - a click outside the window, a key
    with an unknown modifier, a press with a stale ref - would wedge the
    loop on the first live run."""
    calls = drive(client, Action.from_dict(VALID_ACTIONS[kind]))
    assert calls, f"action {kind!r} translated to no driver calls"


def test_press_resolves_the_path_then_presses_the_ref(client: DriverClient):
    """A press is two driver calls: find_element resolves the menu path to
    a ref, then press takes the ref the driver issued. A translation that
    pressed a remembered ref would break the moment the AX tree changed -
    the ref table is the client's, and the driver invalidates refs when
    the tree does."""
    calls = drive(client, Action.from_dict(VALID_ACTIONS["press"]))
    assert [method for method, _ in calls] == ["find_element", "press"]
    assert calls[0][1] == {"path": ["Workspace", "Switch to Page", "Media"]}
    assert calls[1][1]["element"].startswith("ax-")


def test_click_translates_to_a_driver_click_with_the_same_coordinates(
    client: DriverClient,
):
    """The coordinates pass through unchanged - window points in, window
    points to the driver. A translation that scaled them (to backing
    pixels, or from a normalized space) would miss every control."""
    calls = drive(client, Action.from_dict(VALID_ACTIONS["click"]))
    assert calls == [("click", {"x": 100.0, "y": 100.0})]


def test_key_parses_the_combination_into_the_drivers_vocabulary(
    client: DriverClient,
):
    """`"shift+4"` is the model's spelling; the driver takes a key name and
    a modifier list. The parse is the translation - a key sent as
    `"shift+4"` whole would be refused by the driver's INVALID_PARAMS."""
    calls = drive(client, Action.from_dict(VALID_ACTIONS["key"]))
    assert calls == [("key", {"key": "4", "modifiers": ["shift"]})]


def test_a_key_with_no_modifiers_translates_plain(client: DriverClient):
    calls = drive(client, Action.from_dict({"action": "key", "key": "return"}))
    assert calls == [("key", {"key": "return", "modifiers": []})]


def test_an_unknown_action_is_refused_not_guessed():
    """A model that invents an action must be told the vocabulary, not
    silently dropped - a dropped action looks identical to a landed one in
    the next screenshot."""
    with pytest.raises(ActionError) as unknown:
        Action.from_dict({"action": "teleport", "x": 1.0})
    assert "teleport" in str(unknown.value)
    assert "press" in str(unknown.value)


def test_a_missing_param_is_refused_with_the_vocabulary():
    with pytest.raises(ActionError) as missing:
        Action.from_dict({"action": "click", "x": 100.0})
    assert "y" in str(missing.value)


def test_an_unknown_modifier_is_refused_with_the_vocabulary():
    with pytest.raises(ActionError) as unknown:
        Action.from_dict({"action": "key", "key": "hyper+space"})
    assert "hyper" in str(unknown.value)
    assert "cmd" in str(unknown.value)


def test_a_press_whose_path_matches_nothing_is_refused(client: DriverClient):
    """The driver's find_element answers ELEMENT_NOT_FOUND for an unknown
    path; the translation surfaces that as a refusal naming the path, not
    as a press on an empty ref list."""
    action = Action.from_dict({"action": "press", "path": ["No", "Such", "Menu"]})
    gen = action.translate(client)
    next(gen)
    with pytest.raises(DriverError) as not_found:
        gen.send({"elements": []})
    assert not_found.value.driver_code == "ELEMENT_NOT_FOUND"


def test_terminal_signals_translate_to_no_driver_call(client: DriverClient):
    """done / need_input / budget_exhausted end the loop - they must not
    reach the driver, which would refuse them as unknown methods."""
    for kind in ("done", "need_input", "budget_exhausted"):
        action = Action.from_dict({"action": kind})
        assert action.is_terminal
        assert drive(client, action) == []


def test_parse_key_splits_modifiers_and_normalizes_case():
    assert parse_key("cmd+shift+t") == ("t", ["cmd", "shift"])
    assert parse_key("CMD+S") == ("s", ["cmd"])
    assert parse_key("escape") == ("escape", [])


def test_set_value_resolves_the_path_then_sets_not_presses(client: DriverClient):
    """A set_value is find_element then set_value with the value. A
    translation that pressed instead would open the control's menu
    rather than set the field - choosing 'Person' in Magic Mask by
    pressing its field opens the dropdown and stops there."""
    calls = drive(client, Action.from_dict(VALID_ACTIONS["set_value"]))
    assert [method for method, _ in calls] == ["find_element", "set_value"]
    assert calls[1][1]["value"] == "Person"
    assert calls[1][1]["element"].startswith("ax-")


def test_a_set_value_without_a_value_is_refused():
    """set_value without the value is a model error, refused with the
    vocabulary - the driver would receive an empty write otherwise."""
    with pytest.raises(ActionError) as missing:
        Action.from_dict(
            {"action": "set_value", "path": ["Workspace", "Switch to Page"]}
        )
    assert "value" in str(missing.value)


def test_double_click_is_one_driver_call_with_two_clicks(client: DriverClient):
    """A double_click is a single click call carrying clicks=2. Two
    separate clicks would land as select-then-select - opening a clip's
    rename or nothing - instead of the open the double click means."""
    calls = drive(client, Action.from_dict(VALID_ACTIONS["double_click"]))
    assert calls == [("click", {"x": 100.0, "y": 100.0, "clicks": 2})]


def test_choose_resolves_then_presses_like_a_menu_choice(client: DriverClient):
    """choose records the intent was a choice, but the driver only
    presses: the translation is find_element then press. A choose that
    clicked coordinates would depend on the menu's screen position."""
    calls = drive(client, Action.from_dict(VALID_ACTIONS["choose"]))
    assert [method for method, _ in calls] == ["find_element", "press"]
