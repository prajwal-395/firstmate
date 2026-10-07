"""The script runner's contract, pinned against the mock driver.

Each test names the defect it catches: a behaviour that would be wrong if
the runner regressed. The two that matter most - anchors re-resolved at
run time (not cached from the mapping run), and the LLM fallback that
teaches the script - are the ones a future change would break first.
"""

from __future__ import annotations

import pytest

from library.tools.computer_use.client import DriverClient
from library.tools.computer_use.scripts import (
    Anchor,
    Expect,
    Script,
    ScriptError,
    ScriptStep,
    load_script,
    resolve_anchor,
    run_script,
)


def _script() -> Script:
    return Script(
        goal="switch to the Media page and click the Master label",
        steps=[
            ScriptStep(
                action="press",
                anchors=[Anchor(via="ax_path", params={"path": ["Workspace", "Switch to Page", "Media"]})],
                expect=Expect(via="text_visible", params={"text": "Media"}),
            ),
            ScriptStep(
                action="click",
                anchors=[Anchor(via="ocr", params={"text": "Master"})],
                expect=Expect(via="pixels_changed"),
            ),
        ],
    )


def test_a_script_with_ax_path_and_ocr_anchors_completes(client: DriverClient):
    """A script that switches pages via AX path and clicks an OCR-anchored
    control runs against the mock. The press resolves through
    find_element; the click resolves through the OCR label's measured
    frame."""
    result = run_script(client, _script())
    assert result.outcome == "done"
    assert len(result.steps) == 2
    assert result.steps[0]["via"] == "ax_path"
    assert result.steps[1]["via"] == "ocr"


def test_the_script_resolves_at_two_different_window_sizes(
    client: DriverClient, monkeypatch
):
    """The OCR frame moves with the window size; the script still
    resolves. A runner that cached the coordinates from the mapping run
    would click the wrong point at the second size."""
    monkeypatch.setenv("MOCK_WINDOW_SCALE", "1.0")
    first = resolve_anchor(client, Anchor(via="ocr", params={"text": "Master"}))
    assert first is not None
    _, first_params = first

    monkeypatch.setenv("MOCK_WINDOW_SCALE", "2.0")
    client._process.kill()
    client._process.wait()
    second = resolve_anchor(client, Anchor(via="ocr", params={"text": "Master"}))
    assert second is not None
    _, second_params = second

    assert first_params != second_params
    assert second_params["x"] == first_params["x"] * 2
    assert second_params["y"] == first_params["y"] * 2


def test_a_step_whose_anchor_does_not_resolve_falls_back_to_the_llm(
    client: DriverClient
):
    """A step whose anchor does not resolve (the label is not on screen)
    falls back to the LLM. The LLM's action is recorded back into the
    script as a new anchor - the LLM teaches the script, and the next run
    is deterministic."""
    script = Script(
        goal="click a label that is not there",
        steps=[
            ScriptStep(
                action="click",
                anchors=[Anchor(via="ocr", params={"text": "Not On Screen"})],
                expect=Expect(via="pixels_changed"),
            ),
        ],
    )
    llm_calls: list = []

    def llm(obs, step):
        llm_calls.append((obs, step))
        return {"action": "click", "x": 100.0, "y": 100.0}

    result = run_script(client, script, llm=llm)
    assert result.outcome == "done"
    assert len(llm_calls) == 1
    assert result.steps[0]["via"] == "llm"
    assert len(script.steps[0].anchors) == 2


def test_a_step_with_no_anchors_and_no_llm_fails(client: DriverClient):
    """A step with no anchors and no LLM to fall back to fails - the
    runner does not invent a target."""
    script = Script(
        goal="click nothing",
        steps=[ScriptStep(action="click", anchors=[])],
    )
    result = run_script(client, script)
    assert result.outcome == "failed"
    assert result.steps[0]["outcome"] == "failed"


def test_an_ax_path_that_matches_nothing_is_skipped(client: DriverClient):
    """An AX path that matches nothing is skipped, and the runner tries
    the next anchor. A runner that refused the step on the first anchor
    would never reach a fallback."""
    script = Script(
        goal="press a missing menu",
        steps=[
            ScriptStep(
                action="press",
                anchors=[
                    Anchor(via="ax_path", params={"path": ["No", "Such", "Menu"]}),
                    Anchor(via="ax_path", params={"path": ["Help"]}),
                ],
            ),
        ],
    )
    result = run_script(client, script)
    assert result.outcome == "done"
    assert result.steps[0]["via"] == "ax_path"


def test_an_ax_path_splits_the_title_from_the_path(client: DriverClient,
                                                      monkeypatch):
    """The driver's filter matches `path` against the titles ABOVE the
    element and `title` against the element itself: the whole chain as
    `path` resolved nothing on live Resolve (the Magic Mask recipe's
    first step failed on it 2026-10-06), while the mock accepted
    either shape and hid the defect."""
    seen: list = []
    real_call = client.call

    def record(method, params=None):
        seen.append((method, dict(params or {})))
        return real_call(method, params)

    monkeypatch.setattr(client, "call", record)
    resolved = resolve_anchor(
        client, Anchor(via="ax_path",
                       params={"path": ["Workspace", "Switch to Page", "Media"]}))
    assert resolved is not None and resolved[0] == "press"
    finds = [p for m, p in seen if m == "find_element"]
    assert finds == [{"path": ["Workspace", "Switch to Page"],
                      "title": "Media"}]


def test_a_position_anchor_needs_no_resolution(client: DriverClient):
    """A position anchor is the last resort - it needs no resolution, just
    a `why`. It adapts to nothing, so it is the fallback when every
    measured anchor fails."""
    resolved = resolve_anchor(
        client, Anchor(via="position", params={"x": 50.0, "y": 60.0, "why": "fallback"})
    )
    assert resolved == ("click", {"x": 50.0, "y": 60.0})


def test_the_script_format_round_trips_through_json(client: DriverClient):
    """The script is a JSON document - it is written by the planner, read
    by the runner, and edited by the LLM fallback. A format that does not
    round-trip would lose the anchors the LLM taught."""
    import json

    script = _script()
    data = json.loads(json.dumps(script.to_dict()))
    restored = Script.from_dict(data)
    assert restored.goal == script.goal
    assert len(restored.steps) == 2
    assert restored.steps[0].anchors[0].via == "ax_path"
    assert restored.steps[1].anchors[0].params["text"] == "Master"


def test_an_unknown_expect_via_is_refused_at_parse_time():
    """An unknown expect via raises ValueError at Script.from_dict - a
    silent pass at run time would let a typo'd expectation verify every
    step."""
    data = {
        "goal": "test",
        "steps": [
            {
                "action": "click",
                "anchors": [{"via": "position", "params": {"x": 1, "y": 2}}],
                "expect": {"via": "typo", "params": {}},
            }
        ],
    }
    try:
        Script.from_dict(data)
        assert False, "should have raised ValueError"
    except ValueError as exc:
        assert "typo" in str(exc)


def test_the_llm_fallback_drives_the_action_exactly_once(
    client: DriverClient, monkeypatch
):
    """The LLM's action is driven exactly once - a translate that calls
    and then a fallback that calls again would drive every action twice."""
    calls: list[tuple[str, dict]] = []
    original_call = client.call

    def counting_call(method, params=None):
        calls.append((method, params))
        return original_call(method, params)

    monkeypatch.setattr(client, "call", counting_call)
    script = Script(
        goal="click a label that is not there",
        steps=[
            ScriptStep(
                action="click",
                anchors=[Anchor(via="ocr", params={"text": "Not On Screen"})],
                expect=Expect(via="pixels_changed"),
            ),
        ],
    )

    def llm(obs, step):
        return {"action": "click", "x": 100.0, "y": 100.0}

    result = run_script(client, script, llm=llm)
    assert result.outcome == "done"
    click_calls = [c for c in calls if c[0] == "click"]
    assert len(click_calls) == 1
    assert click_calls[0][1] == {"x": 100.0, "y": 100.0}


def test_the_learned_anchor_records_the_llms_real_target(client: DriverClient):
    """The anchor the LLM teaches is the one its action actually resolved
    - a click at (100, 200) teaches position (100, 200), not (0, 0). The
    second run replays the LLM's real target."""
    script = Script(
        goal="click a label that is not there",
        steps=[
            ScriptStep(
                action="click",
                anchors=[Anchor(via="ocr", params={"text": "Not On Screen"})],
                expect=Expect(via="pixels_changed"),
            ),
        ],
    )

    def llm(obs, step):
        return {"action": "click", "x": 100.0, "y": 200.0}

    result = run_script(client, script, llm=llm)
    assert result.outcome == "done"
    assert len(script.steps[0].anchors) == 2
    learned = script.steps[0].anchors[1]
    assert learned.via == "position"
    assert learned.params["x"] == 100.0
    assert learned.params["y"] == 200.0


def test_the_learned_anchor_for_a_press_records_the_ax_path(client: DriverClient):
    """A press teaches the AX path, not a position - the second run
    resolves the same menu path."""
    script = Script(
        goal="press a menu that is not there",
        steps=[
            ScriptStep(
                action="press",
                anchors=[Anchor(via="ax_path", params={"path": ["No", "Such", "Menu"]})],
                expect=Expect(via="pixels_changed"),
            ),
        ],
    )

    def llm(obs, step):
        return {"action": "press", "path": ["Workspace", "Switch to Page", "Media"]}

    result = run_script(client, script, llm=llm)
    assert result.outcome == "done"
    assert len(script.steps[0].anchors) == 2
    learned = script.steps[0].anchors[1]
    assert learned.via == "ax_path"
    assert learned.params["path"] == ["Workspace", "Switch to Page", "Media"]


def test_a_choose_teaches_the_ax_path_like_a_press(client: DriverClient):
    """A choose resolves through find_element then press, so it teaches
    the AX path - the choice intent already lives in the step's action.
    A choose that taught nothing would ask the LLM again on every run
    of a verified route."""
    script = Script(
        goal="choose a menu that is not there",
        steps=[
            ScriptStep(
                action="choose",
                anchors=[Anchor(via="ax_path", params={"path": ["No", "Such", "Menu"]})],
            ),
        ],
    )

    def llm(obs, step):
        return {"action": "choose", "path": ["Workspace", "Switch to Page", "Media"]}

    result = run_script(client, script, llm=llm)
    assert result.outcome == "done"
    assert len(script.steps[0].anchors) == 2
    learned = script.steps[0].anchors[1]
    assert learned.via == "ax_path"
    assert learned.params["path"] == ["Workspace", "Switch to Page", "Media"]


def test_a_double_click_teaches_the_position_with_both_clicks(
    client: DriverClient,
):
    """A double_click teaches position with clicks=2, and the position
    anchor forwards it to the driver. A taught double_click replayed as
    a single click would select instead of opening - the count is the
    action, not a detail."""
    script = Script(
        goal="double-click a label that is not there",
        steps=[
            ScriptStep(
                action="double_click",
                anchors=[Anchor(via="ocr", params={"text": "Not On Screen"})],
            ),
        ],
    )

    def llm(obs, step):
        return {"action": "double_click", "x": 100.0, "y": 200.0}

    result = run_script(client, script, llm=llm)
    assert result.outcome == "done"
    learned = script.steps[0].anchors[1]
    assert learned.via == "position"
    assert learned.params["clicks"] == 2
    assert resolve_anchor(client, learned) == (
        "click", {"x": 100.0, "y": 200.0, "clicks": 2})


def test_a_set_value_teaches_no_anchor(client: DriverClient):
    """A set_value has no anchor to teach: the anchor layer resolves
    paths to presses, and a taught press on a field would open its
    menu instead of setting it. The rescue still commits - only the
    teaching is refused."""
    script = Script(
        goal="set a field that is not there",
        steps=[
            ScriptStep(
                action="set_value",
                anchors=[Anchor(via="ax_path", params={"path": ["No", "Such", "Menu"]})],
            ),
        ],
    )

    def llm(obs, step):
        return {
            "action": "set_value",
            "path": ["Workspace", "Switch to Page", "Media"],
            "value": "Person",
        }

    result = run_script(client, script, llm=llm)
    assert result.outcome == "done"
    assert len(script.steps[0].anchors) == 1


def test_pixels_changed_verification_diffs_captures(client: DriverClient):
    """`pixels_changed` diffs the capture before the action against the
    capture after - a step that changes nothing fails verification."""
    script = Script(
        goal="click nothing",
        steps=[
            ScriptStep(
                action="click",
                anchors=[Anchor(via="position", params={"x": 100.0, "y": 100.0})],
                expect=Expect(via="pixels_changed"),
            ),
        ],
    )
    result = run_script(client, script)
    assert result.outcome == "done"


def test_the_lease_is_taken_per_step(client: DriverClient, monkeypatch):
    """The lease is taken per step's act+verify, the same way the loop's
    `_act_and_verify` does - the script drives the UI, so it needs the
    same exclusion the loop has."""
    from contextlib import contextmanager

    state: dict = {"held": False, "takes": 0}

    @contextmanager
    def tracking_lease(purpose, **kw):
        state["held"] = True
        state["takes"] += 1
        yield
        state["held"] = False

    monkeypatch.setattr(
        "library.tools.computer_use.scripts.resolve_lease",
        tracking_lease,
    )
    script = _script()
    result = run_script(client, script)
    assert result.outcome == "done"
    assert state["takes"] == 2


def test_element_visible_sends_the_element_predicate(client: DriverClient):
    """`element_visible` sends the element_visible predicate the driver
    supports (role/title/title_contains), not a text_visible predicate."""
    script = Script(
        goal="click and verify an element",
        steps=[
            ScriptStep(
                action="click",
                anchors=[Anchor(via="position", params={"x": 100.0, "y": 100.0})],
                expect=Expect(via="element_visible", params={"title": "Media"}),
            ),
        ],
    )
    result = run_script(client, script)
    assert result.outcome == "done"


def test_an_unknown_expect_via_is_rejected_at_script_from_dict():
    """An unknown expect via raises ValueError at Script.from_dict - a
    silent pass at run time would let a typo'd expectation verify every
    step."""
    data = {
        "goal": "test",
        "steps": [
            {
                "action": "click",
                "anchors": [{"via": "position", "params": {"x": 1, "y": 2}}],
                "expect": {"via": "typo", "params": {}},
            }
        ],
    }
    try:
        Script.from_dict(data)
        assert False, "should have raised ValueError"
    except ValueError as exc:
        assert "typo" in str(exc)


def test_load_script_refuses_a_missing_file(tmp_path):
    """A script path that names nothing refuses with the fix - the
    caller's input is wrong, which is a refusal, not a traceback."""
    import pytest

    with pytest.raises(ScriptError) as excinfo:
        load_script(tmp_path / "absent.json")

    assert excinfo.value.code == "SCRIPT_UNREADABLE"


def test_load_script_refuses_an_empty_script(tmp_path):
    """Valid JSON with no steps is refused - an empty script runs
    nothing, and running nothing successfully would read as done."""
    import pytest

    path = tmp_path / "empty.json"
    path.write_text('{"goal": "nothing", "steps": []}', encoding="utf-8")

    with pytest.raises(ScriptError) as excinfo:
        load_script(path)

    assert excinfo.value.code == "SCRIPT_EMPTY"


def test_an_ax_path_with_a_string_value_sets_instead_of_pressing(client: DriverClient):
    """A numeric control (a Fairlight EQ frequency field) is settable
    from a script: an ax_path anchor carrying a string value resolves
    to set_value with the element ref and the exact string. Before
    this, scripts could only press and click, so no deterministic
    script could drive a numeric control at all."""
    script = Script(
        goal="set a field through its AX path",
        steps=[
            ScriptStep(
                action="set_value",
                anchors=[Anchor(via="ax_path", params={
                    "path": ["Workspace", "Switch to Page", "Media"],
                    "value": "80"})],
            ),
        ],
    )
    resolved = resolve_anchor(client, script.steps[0].anchors[0])
    assert resolved is not None
    method, params = resolved
    assert method == "set_value"
    assert params["value"] == "80"
    result = run_script(client, script)
    assert result.outcome == "done"
    assert result.steps[0]["via"] == "ax_path"


def test_an_ax_path_with_a_non_string_value_fails_fast(client: DriverClient):
    """A set_value with a non-string value raises instead of skipping
    to the next anchor - the script is malformed (numbers are
    formatted at the recipe layer), and silently trying the next
    anchor would set a different control or none."""
    script = Script(
        goal="set a field with an unformatted number",
        steps=[
            ScriptStep(
                action="set_value",
                anchors=[Anchor(via="ax_path", params={
                    "path": ["Workspace", "Switch to Page", "Media"],
                    "value": 80.0})],
            ),
        ],
    )
    try:
        resolve_anchor(client, script.steps[0].anchors[0])
        assert False, "should have raised ValueError"
    except ValueError as exc:
        assert "string" in str(exc)


def test_an_ax_path_without_a_value_still_presses(client: DriverClient):
    """The value-carrying ax_path changes nothing for plain presses: an
    anchor without a value resolves to press exactly as before."""
    resolved = resolve_anchor(client, Anchor(via="ax_path", params={
        "path": ["Workspace", "Switch to Page", "Media"]}))
    assert resolved is not None
    method, _ = resolved
    assert method == "press"


def test_the_node_add_recipe_names_menu_routes_only():
    """The grade.node_add recipe must reach Resolve server-side: both
    steps name a whole menu path for `run_command` - no screen
    coordinate, no keystroke. A viewer click would need the
    foreground and a window-size-dependent point; a keystroke lands
    in whatever app is frontmost (measured live: Shift+6 reached the
    browser, not Resolve). The menu route reached Resolve with
    another app frontmost."""
    from library.tools.computer_use.scripts import (
        GRADE_COLOR_PAGE_PATH,
        GRADE_NODE_ADD_PATH,
        grade_node_add_script,
    )

    script = grade_node_add_script()
    assert len(script.steps) == 2
    page, add = script.steps
    assert page.anchors[0].via == "menu"
    assert page.anchors[0].params["path"] == GRADE_COLOR_PAGE_PATH
    assert add.anchors[0].via == "menu"
    assert add.anchors[0].params["path"] == GRADE_NODE_ADD_PATH
    for step in script.steps:
        assert all(a.via == "menu" for a in step.anchors), step.action


def test_the_node_remove_recipe_steps_to_the_target(client: DriverClient):
    """The grade.node_remove recipe adds a marker (the known start -
    stepping wraps with no selection readout, measured live), steps
    to the target, deletes it, steps to the marker, deletes that -
    all server-side menu commands with settles. A recipe that
    clicked viewer points would need the foreground; one without
    settles silently no-ops (measured live: rapid presses removed
    nothing).
    """
    from library.tools.computer_use.scripts import (
        GRADE_COLOR_PAGE_PATH,
        GRADE_NODE_ADD_PATH,
        GRADE_NODE_DELETE_PATH,
        GRADE_NODE_NEXT_PATH,
        GRADE_NODE_PREV_PATH,
        grade_node_remove_script,
    )

    script = grade_node_remove_script(2, 3)
    menus = [step.anchors[0].params["path"] for step in script.steps
             if step.action == "menu"]
    assert menus == ([GRADE_COLOR_PAGE_PATH, GRADE_NODE_ADD_PATH]
                     + [GRADE_NODE_PREV_PATH] * 2
                     + [GRADE_NODE_DELETE_PATH]
                     + [GRADE_NODE_NEXT_PATH]
                     + [GRADE_NODE_DELETE_PATH])
    for step in script.steps:
        assert all(a.via in ("menu", "wait") for a in step.anchors)


def test_the_node_remove_recipe_refuses_an_absent_node():
    """A recipe for a node outside 1..count refuses at build time - a
    recipe that stepped anyway would delete whatever the stepping
    lands on, the wrong node by construction."""
    from library.tools.computer_use.scripts import grade_node_remove_script

    for bad in ((0, 3), (4, 3), (1, 0)):
        try:
            grade_node_remove_script(*bad)
            assert False, f"should have raised for {bad}"
        except ValueError as exc:
            assert "outside" in str(exc)


def test_a_menu_anchor_resolves_to_run_command(client: DriverClient):
    """A menu anchor resolves to one `run_command` with the whole path -
    no find_element round-trip, no screen lookup. A menu path resolved
    through the AX tree would need its submenu visible; submenus are
    AX-lazy until opened (measured live: Switch to Page exposed one
    empty child), so the tree route cannot see the page it names."""
    from library.tools.computer_use.scripts import grade_node_add_script

    script = grade_node_add_script()
    assert resolve_anchor(client, script.steps[1].anchors[0]) == (
        "run_command", {"path": ["Color", "Nodes", "Add Serial Node"]})


def test_the_node_add_recipe_runs_to_done_against_the_mock(
    client: DriverClient,
):
    """Both menu paths execute against the mock driver: the whole
    write route runs offline. A recipe naming a path the menu does
    not carry fails here - before any live run spends the captain's
    session on it."""
    from library.tools.computer_use.scripts import grade_node_add_script

    result = run_script(client, grade_node_add_script())
    assert result.outcome == "done", result.steps
    assert [step["via"] for step in result.steps] == ["menu", "menu"]


def test_a_key_action_teaches_its_keystroke(client: DriverClient):
    """A key resolves to a driver call with no screen lookup, so it
    teaches a key anchor - the next run sends the same chord without
    the LLM. A key that taught nothing would burn a model call on
    every run of a deterministic route."""
    script = Script(
        goal="send a shortcut that is not in the script",
        steps=[
            ScriptStep(
                action="key",
                anchors=[Anchor(via="ocr", params={"text": "Not On Screen"})],
            ),
        ],
    )

    def llm(obs, step):
        return {"action": "key", "key": "option+s"}

    result = run_script(client, script, llm=llm)
    assert result.outcome == "done"
    assert len(script.steps[0].anchors) == 2
    learned = script.steps[0].anchors[1]
    assert learned.via == "key"
    assert learned.params["key"] == "option+s"
    assert run_script(client, script).outcome == "done"


def test_the_panels_recipe_names_measured_menu_routes_only():
    """The effect-panels recipe opens the two panels apply-by-search
    stands on through the menu paths the live mapping pressed
    (pressed:true, Ren Qualification, background, no focus). Every
    step is a menu anchor: a coordinate would need foreground
    pixels, and background captures hold pre-switch frames (mapped
    2026-10-07), so nothing here may name one. A search or apply
    step would type into whatever panel happened to be open - those
    anchors wait for the foreground mapping run."""
    from library.tools.computer_use.scripts import (
        EFFECTS_EDIT_PAGE_PATH,
        EFFECTS_PANEL_PATH,
        INSPECTOR_PANEL_PATH,
        effect_panels_script,
    )

    script = effect_panels_script()
    assert len(script.steps) == 3
    page, effects, inspector = script.steps
    assert page.anchors[0].via == "menu"
    assert page.anchors[0].params["path"] == EFFECTS_EDIT_PAGE_PATH
    assert effects.anchors[0].via == "menu"
    assert effects.anchors[0].params["path"] == EFFECTS_PANEL_PATH
    assert inspector.anchors[0].via == "menu"
    assert inspector.anchors[0].params["path"] == INSPECTOR_PANEL_PATH
    coordinate_vias = {"position", "ocr", "template", "offset"}
    for step in script.steps:
        assert all(a.via not in coordinate_vias for a in step.anchors), \
            step.action


def test_the_panels_recipe_runs_to_done_against_the_mock(
    client: DriverClient,
):
    """All three menu paths execute against the mock driver: the
    panels route runs offline. A recipe naming a path the menu does
    not carry fails here - before a live run spends the captain's
    session pressing it."""
    from library.tools.computer_use.scripts import effect_panels_script

    result = run_script(client, effect_panels_script())
    assert result.outcome == "done", result.steps
    assert [step["via"] for step in result.steps] == ["menu"] * 3


def test_a_menu_anchor_resolves_a_remove_path(client: DriverClient):
    """The remove recipe's delete step resolves through the same menu
    anchor to its own whole path - the anchor carries any command,
    not just the add route."""
    from library.tools.computer_use.scripts import (
        GRADE_NODE_DELETE_PATH, grade_node_remove_script)

    script = grade_node_remove_script(1, 1)
    assert resolve_anchor(client, script.steps[-2].anchors[0]) == (
        "run_command", {"path": GRADE_NODE_DELETE_PATH})


def test_the_node_remove_recipe_runs_to_done_against_the_mock(
    client: DriverClient,
):
    """Every menu path in the remove recipe executes against the mock
    driver: the whole write route runs offline. A recipe naming a
    path the menu does not carry fails here - before any live run
    spends the captain's session on it."""
    from library.tools.computer_use.scripts import grade_node_remove_script

    result = run_script(client, grade_node_remove_script(2, 3))
    assert result.outcome == "done", result.steps
    assert {step["via"] for step in result.steps} == {"menu", "wait"}


def test_a_wait_anchor_resolves_to_the_driver_wait(client: DriverClient):
    """A wait anchor resolves to the driver's own seconds wait, so a
    UI-settle need is said in the script instead of slept around it.
    A settle the runner cannot see would hide in wall-clock gaps no
    log records."""
    assert resolve_anchor(
        client, Anchor(via="wait", params={"seconds": 3.0})) == (
        "wait_for", {"predicate": {"type": "seconds", "duration": 3.0}})


def test_a_malformed_wait_anchor_fails_fast():
    """A wait anchor with a malformed duration raises instead of
    resolving to a zero wait - a negative settle would silently run
    the un-settled sequence the wait was added to prevent."""
    try:
        resolve_anchor(None, Anchor(via="wait", params={"seconds": -1}))
        assert False, "should have raised ValueError"
    except ValueError as exc:
        assert "seconds" in str(exc)


def test_the_settled_run_sleeps_waits_in_process(client: DriverClient,
                                                 monkeypatch):
    """Waits sleep in-process instead of asking the driver: the
    driver's own waits need an unlocked screen (measured live:
    `wait_for` answers SCREEN_LOCKED while menu commands execute),
    so a settled run on a locked machine would fail every wait step.
    The durations stay declared on the steps."""
    import time

    from library.tools.computer_use.scripts import (
        grade_node_remove_script, run_settled_script)

    slept: list = []
    monkeypatch.setattr(time, "sleep", slept.append)
    script = grade_node_remove_script(2, 3, settle_seconds=0.5)
    result = run_settled_script(client, script)
    assert result.outcome == "done", result.steps
    assert slept == [0.5] * 7
    assert [step["via"] for step in result.steps] == (
        ["menu", "wait"] * 7)
    assert all(step["outcome"] == "ok" for step in result.steps)


def test_the_settled_run_fails_a_step_nothing_resolves():
    """A settled step no anchor resolves fails the run instead of
    skipping on - silently skipping a step would run the rest of the
    route against a UI state the skipped step was meant to establish.
    """
    from library.tools.computer_use.scripts import (
        Script, ScriptStep, run_settled_script)

    class _Dead:
        def call(self, method, params=None):
            raise AssertionError("no driver call expected")

        def close(self):
            pass

    script = Script(
        goal="press a menu that is not there",
        steps=[ScriptStep(
            action="menu",
            anchors=[Anchor(via="menu", params={"path": []})])],
    )
    result = run_settled_script(_Dead(), script)
    assert result.outcome == "failed"
    assert result.steps == [{"step": 0, "action": "menu",
                             "outcome": "failed", "via": "none"}]


def test_a_template_hit_returns_window_points(client: DriverClient,
                                              monkeypatch, tmp_path):
    """Defect, caught live: the match is in backing pixels but clicks
    take window points - an unscaled hit at 2x reads outside the window
    and the driver refuses it OUTSIDE_RESOLVE. The capture carries its
    own scale and the resolver divides by it."""
    import base64
    from io import BytesIO

    from PIL import Image, ImageDraw

    from library.tools.computer_use.scripts import _resolve_template

    real_call = client.call
    # A synthetic capture with a known bright square - the mock's
    # stills are near-flat, and normalized correlation on a flat
    # crop reads 0 wherever it lands.
    img = Image.new("RGB", (128, 64), (20, 20, 20))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 20, 60, 40], fill=(200, 200, 200))
    draw.rectangle([46, 26, 54, 34], fill=(10, 10, 10))
    buf = BytesIO()
    img.save(buf, format="PNG")
    fixed = {"image": base64.b64encode(buf.getvalue()).decode("ascii"),
             "width": 128.0, "height": 64.0, "scale": 2.0}
    scale = float(fixed["scale"])
    monkeypatch.setattr(
        client, "call",
        lambda method, params=None: (fixed if method == "capture_window"
                                     else real_call(method, params)))
    box = (40, 20, 60, 40)
    template_path = tmp_path / "template.png"
    img.crop(box).save(template_path)
    resolved = _resolve_template(
        client, Anchor(via="template",
                       params={"image": str(template_path),
                               "threshold": 0.5}))
    assert resolved is not None
    method, params = resolved
    assert method == "click"
    assert params["x"] == pytest.approx((box[0] + box[2]) / 2 / scale)
    assert params["y"] == pytest.approx((box[1] + box[3]) / 2 / scale)


def test_click_counts_ride_ocr_and_template_resolvers(client: DriverClient,
                                                       monkeypatch, tmp_path):
    """A Library row applies on double-click, so the resolvers carry
    the anchor's click count onto the call - the same three lines the
    position resolver already has. A count the resolver dropped would
    select rows and apply nothing, failing every apply downstream."""
    import base64
    from io import BytesIO

    from PIL import Image, ImageDraw

    from library.tools.computer_use.scripts import resolve_anchor

    resolved = resolve_anchor(
        client, Anchor(via="ocr",
                       params={"text": "Master", "clicks": 2}))
    assert resolved is not None
    method, params = resolved
    assert method == "click"
    assert params["clicks"] == 2

    img = Image.new("RGB", (128, 64), (20, 20, 20))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 20, 60, 40], fill=(200, 200, 200))
    draw.rectangle([46, 26, 54, 34], fill=(10, 10, 10))
    buf = BytesIO()
    img.save(buf, format="PNG")
    fixed = {"image": base64.b64encode(buf.getvalue()).decode("ascii"),
             "width": 128.0, "height": 64.0, "scale": 1.0}
    real_call = client.call
    monkeypatch.setattr(
        client, "call",
        lambda method, params=None: (fixed if method == "capture_window"
                                     else real_call(method, params)))
    template_path = tmp_path / "template.png"
    img.crop((40, 20, 60, 40)).save(template_path)
    resolved = resolve_anchor(
        client, Anchor(via="template",
                       params={"image": str(template_path),
                               "threshold": 0.5, "clicks": 2}))
    assert resolved is not None
    method, params = resolved
    assert method == "click"
    assert params["clicks"] == 2


def test_a_past_deadline_raises_before_touching_the_driver():
    """Past the take's bound the run raises TakeExpired before the first
    step - a refusal, not a step failure. A runner that acted past its
    bound would run the take past its wall-clock budget."""
    import time as _time

    from library.tools.computer_use.take import TakeExpired

    class _Dead:
        def call(self, method, params=None):
            raise AssertionError("no driver call expected")

    script = Script(
        goal="switch pages",
        steps=[ScriptStep(
            action="press",
            anchors=[Anchor(via="ax_path",
                            params={"path": ["Workspace"]})])],
    )
    try:
        run_script(_Dead(), script, deadline=_time.monotonic() - 1.0)
    except TakeExpired as exc:
        assert exc.code == "TAKE_EXPIRED"
    else:
        raise AssertionError("an expired take must refuse, not run")


def test_region_changed_inside_and_outside():
    """A rect that changed reads changed; a rect over identical pixels
    reads unchanged - the named region, not the frame, is the signal."""
    from PIL import Image

    from library.tools.computer_use.scripts import _region_changed
    from io import BytesIO

    def png(color, box=None, fill=None):
        img = Image.new("RGB", (64, 64), color)
        if box:
            from PIL import ImageDraw
            ImageDraw.Draw(img).rectangle(box, fill=fill)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    base = png((20, 20, 20))
    marked = png((20, 20, 20), box=[10, 10, 20, 20], fill=(200, 200, 200))
    assert _region_changed(base, marked, [0, 0, 64, 64]) is True
    assert _region_changed(base, marked, [10, 10, 11, 11]) is True
    assert _region_changed(base, marked, [40, 40, 10, 10]) is False
    assert _region_changed(base, base, [0, 0, 64, 64]) is False


def test_region_outside_the_image_is_unchanged_and_malformed_raises():
    """A rect with no overlap reads unchanged (nothing observable);
    a malformed rect is a recipe bug and fails fast."""
    import pytest

    from PIL import Image

    from library.tools.computer_use.scripts import _region_changed
    from io import BytesIO

    buf = BytesIO()
    Image.new("RGB", (64, 64), (20, 20, 20)).save(buf, format="PNG")
    shot = buf.getvalue()
    assert _region_changed(shot, shot, [100, 100, 10, 10]) is False
    with pytest.raises(ValueError):
        _region_changed(shot, shot, [10, 10, 0, 10])
    with pytest.raises(ValueError):
        _region_changed(shot, shot, [-1, 10, 10, 10])
def test_the_measured_registry_template_resolves():
    """The committed magic-mask crop matches itself pasted into a
    capture at the registry threshold with no per-call threshold: the
    recipe names the template, the registry carries the measurement."""
    import base64
    from io import BytesIO

    import pytest

    from PIL import Image

    from library.tools.computer_use.scripts import _resolve_template
    from library.tools.computer_use.templates import load_template

    pixels, entry = load_template("magic-mask-toolbar")
    h, w = pixels.shape[:2]
    crop = Image.fromarray(pixels)
    img = Image.new("RGB", (400, 300), (20, 20, 20))
    img.paste(crop, (100, 80))
    buf = BytesIO()
    img.save(buf, format="PNG")

    class _Capture:
        def call(self, method, params=None):
            assert method == "capture_window"
            return {"image": base64.b64encode(buf.getvalue()).decode("ascii"),
                    "width": 400.0, "height": 300.0, "scale": 2.0}

    resolved = _resolve_template(
        _Capture(), Anchor(via="template",
                           params={"template": "magic-mask-toolbar"}))
    assert resolved is not None
    method, params = resolved
    assert method == "click"
    assert params["x"] == pytest.approx((100 + w / 2) / 2.0)
    assert params["y"] == pytest.approx((80 + h / 2) / 2.0)


def test_a_missing_template_file_resolves_to_nothing_not_a_crash(tmp_path):
    """A named file that is absent or unreadable resolves to nothing:
    yesterday's crop moved or was never captured, and the run must
    fall through, not traceback."""
    from library.tools.computer_use.scripts import _resolve_template

    class _Dead:
        def call(self, method, params=None):
            raise AssertionError("no driver call expected")

    resolved = _resolve_template(
        _Dead(), Anchor(via="template",
                        params={"image": str(tmp_path / "gone.png"),
                                "threshold": 0.5}))
    assert resolved is None


def test_a_threshold_outside_unit_range_fails_fast(tmp_path):
    """A threshold at or above 1 (or at/below 0) is a malformed recipe,
    not a layout that moved: it raises instead of matching nothing
    forever."""
    from PIL import Image

    from library.tools.computer_use.scripts import _resolve_template

    template_path = tmp_path / "template.png"
    Image.new("RGB", (8, 8), (200, 200, 200)).save(template_path)

    class _Dead:
        def call(self, method, params=None):
            raise AssertionError("no driver call expected")

    with pytest.raises(ValueError):
        _resolve_template(
            _Dead(), Anchor(via="template",
                            params={"image": str(template_path),
                                    "threshold": 1.5}))


def test_a_template_cut_at_another_scale_still_lands(monkeypatch, tmp_path):
    """One crop serves every display: a template cut at backing scale
    2x matches a 1x capture after the resolver rescales it to the
    capture's own scale. Without the rescale the grids disagree and a
    fixed threshold never clears."""
    import base64
    from io import BytesIO

    from PIL import Image, ImageDraw

    from library.tools.computer_use.scripts import _resolve_template

    img = Image.new("RGB", (128, 64), (20, 20, 20))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 20, 60, 40], fill=(200, 200, 200))
    draw.rectangle([46, 26, 54, 34], fill=(10, 10, 10))
    buf = BytesIO()
    img.save(buf, format="PNG")

    class _Capture:
        def call(self, method, params=None):
            assert method == "capture_window"
            return {"image": base64.b64encode(buf.getvalue()).decode("ascii"),
                    "width": 128.0, "height": 64.0, "scale": 1.0}

    box = (40, 20, 60, 40)
    big = img.crop(box).resize((40, 40))
    template_path = tmp_path / "template2x.png"
    big.save(template_path)
    resolved = _resolve_template(
        _Capture(), Anchor(via="template",
                           params={"image": str(template_path),
                                   "threshold": 0.5, "scale": 2.0}))
    assert resolved is not None
    method, params = resolved
    assert method == "click"
    assert params["x"] == pytest.approx((box[0] + box[2]) / 2)
    assert params["y"] == pytest.approx((box[1] + box[3]) / 2)
