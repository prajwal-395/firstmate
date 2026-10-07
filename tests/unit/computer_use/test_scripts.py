"""The script runner's contract, pinned against the mock driver.

Each test names the defect it catches: a behaviour that would be wrong if
the runner regressed. The two that matter most - anchors re-resolved at
run time (not cached from the mapping run), and the LLM fallback that
teaches the script - are the ones a future change would break first.
"""

from __future__ import annotations

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
