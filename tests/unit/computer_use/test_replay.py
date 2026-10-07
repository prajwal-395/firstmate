"""The replay suite: every recipe, end to end, proved.

Each test names the defect it catches: a step with no expect
reporting done, a raw coordinate surviving audit, a menu write
proved by acceptance instead of read-back, a restore that runs
before the result is read, a foreground take without the captain's
window. The offline layer runs on fakes - no Resolve, no screen -
and the live layer runs only in Ren Qualification.
"""

from __future__ import annotations

from library.tools.computer_use import replay as replay_mod
from library.tools.computer_use.replay import (
    FakeDriver,
    FakeResolve,
    RecipeSpec,
    audit_recipe,
    recipe_registry,
    replay_offline,
    run_suite_offline,
)
from library.tools.computer_use.scripts import (
    Anchor,
    Script,
    ScriptStep,
    run_script,
)


def test_the_registry_covers_every_named_recipe():
    """The suite replays what `ren computer-use script --recipe`
    runs: every choice on the verb is a row in the registry. A
    recipe the suite does not list is a route that rots unwatched."""
    names = {spec.name for spec in recipe_registry()}
    assert {"magic-mask-seed", "magic-mask-track", "window-create",
            "window-delete", "window-modify", "effect-panels",
            "qualifier-sample"} <= names


def test_background_runs_any_time_and_foreground_batches():
    """Background recipes need no screen; foreground ones batch into
    one take. The split is declared per recipe, so the live runner
    can batch the foreground take instead of taking the screen
    once per recipe."""
    specs = recipe_registry()
    background = {spec.name for spec in specs if spec.background}
    foreground = {spec.name for spec in specs if not spec.background}
    assert background == {"grade-node-add", "grade-node-remove",
                          "effect-panels", "window-create",
                          "window-delete", "window-track",
                          "intellisearch-open"}
    assert foreground == {"magic-mask-seed", "magic-mask-track",
                          "window-modify", "qualifier-sample"}


def test_every_registry_recipe_passes_offline(tmp_path):
    """One command runs every stored recipe end to end against the
    fakes: each step proved, each API proof read back, captures
    saved. A recipe that cannot prove its effect fails here - before
    any live run spends the captain's session on it."""
    report = run_suite_offline(tmp_path)
    assert report["failed"] == [], {
        name: next(recipe["reason"] for recipe in report["recipes"]
                   if recipe["name"] == name)
        for name in report["failed"]}
    assert len(report["passed"]) == len(list(recipe_registry()))
    for recipe in report["recipes"]:
        assert recipe["captures"], recipe["name"]


def test_a_step_with_no_expect_reports_done_unproved():
    """The defect tonight's takes showed: a step the driver merely
    accepted reports `ok` - and `proved` False. Done-means-accepted
    is recorded on the step, never mistaken for done-means-effect."""
    menu_only = Script(
        goal="press a menu",
        steps=[ScriptStep(
            action="press",
            anchors=[Anchor(via="ax_path",
                            params={"path": ["Workspace"]})])],
    )
    result = run_script(FakeDriver(), menu_only)
    assert result.outcome == "done"
    assert result.steps[0]["outcome"] == "ok"
    assert result.steps[0]["proved"] is False


def test_a_step_with_an_expect_reports_proved():
    """A step whose declared expect verified reports proved True -
    the suite can tell the two apart, which is what makes the
    unproved gate fail closed instead of silent."""
    from library.tools.computer_use.scripts import Expect

    script = Script(
        goal="click",
        steps=[ScriptStep(
            action="click",
            anchors=[Anchor(via="ocr", params={"text": "Master"})],
            expect=Expect(via="pixels_changed", params={}))],
    )
    result = run_script(FakeDriver(labels=("Master",)), script)
    assert result.outcome == "done"
    assert result.steps[0]["proved"] is True


def test_the_audit_names_a_step_no_proof_covers():
    """A step with no expect and no covering API proof is a named
    defect: the audit lists its index instead of the replay
    reporting done on it."""
    spec = RecipeSpec(
        name="unproven", background=True, goal="press blind",
        build=lambda: Script(
            goal="press blind",
            steps=[ScriptStep(
                action="press",
                anchors=[Anchor(
                    via="ax_path",
                    params={"path": ["Color", "Nodes"]})])]))
    audit, _ = audit_recipe(spec)
    assert not audit.passed
    assert audit.missing_expects == [0]


def test_a_menu_write_is_proved_by_read_back_not_acceptance(tmp_path):
    """The grade-node-add route passes because the node count reads
    back +1 after the run - not because the menu pressed. The proof
    names the delta it proved."""
    report = replay_offline(
        next(spec for spec in recipe_registry()
             if spec.name == "grade-node-add"), tmp_path)
    assert report.verdict == "pass", report.reason
    nodes = next(proof for proof in report.proofs
                 if proof["name"] == "nodes")
    assert nodes["passed"] and "1 -> 2" in nodes["detail"]


def test_a_malformed_pixel_region_fails_fast():
    """A pixels_changed region that is not a rect (missing keys,
    non-positive size, negative origin) is a recipe bug and fails
    fast - a step cannot prove its effect against a rect that is
    not a rect, and silently reading it as unchanged would pass a
    step that proved nothing."""
    import pytest

    from library.tools.computer_use.scripts import _region_changed

    shot = FakeDriver().call("capture_window")["image"]
    import base64

    raw = base64.b64decode(shot)
    for bad in ({}, {"x": 0.0}, [0.0, 0.0, -1.0, 4.0],
                [0.0, 0.0, 4.0, 0.0], [-1.0, 0.0, 4.0, 4.0]):
        try:
            _region_changed(raw, raw, bad)
            assert False, f"should have raised for {bad!r}"
        except (ValueError, TypeError):
            pass


def test_a_named_region_proves_a_local_change():
    """A pixels_changed expect with a named region proves the change
    inside it: identical frames read unchanged, frames differing
    inside the region read changed."""
    import base64

    from library.tools.computer_use import scripts as scripts_mod
    from library.tools.computer_use.scripts import Expect

    driver = FakeDriver()
    frames = [driver.call("capture_window")["image"] for _ in range(2)]

    region = [0.0, 0.0, 128.0, 128.0]
    before = base64.b64decode(frames[0])
    assert scripts_mod._region_changed(before, before,
                                       region) is False
    assert scripts_mod._region_changed(
        before, base64.b64decode(frames[1]), region) is True
    expect = Expect(via="pixels_changed",
                    params={"region": region})
    assert expect.params["region"] == [0.0, 0.0, 128.0, 128.0]


def test_a_type_anchor_drives_type_text():
    """Defect, caught mapping: the driver's method is `type_text` -
    a `type` anchor resolving to `type` refused live as an unknown
    method while every mock-less check passed. The resolver emits
    the driver's own name, which also carries the foreground flag."""
    from library.tools.computer_use.scripts import (
        INPUT_CALLS,
        resolve_anchor,
    )

    method, params = resolve_anchor(
        FakeDriver(), Anchor(via="type", params={"text": "1.00"}))
    assert method == "type_text"
    assert method in INPUT_CALLS
    assert params == {"text": "1.00"}


def test_clicks_outside_the_window_refuse():
    """A click outside the main window refuses OUTSIDE_RESOLVE instead
    of posting into whatever sits there - the fake holds the same
    boundary the real driver holds."""
    from library.tools.computer_use.client import DriverError

    try:
        FakeDriver().call("click", {"x": 9999.0, "y": 9999.0})
        assert False, "should have raised DriverError"
    except DriverError as exc:
        assert exc.driver_code == "OUTSIDE_RESOLVE"


def test_foreground_live_refuses_without_grant_and_flag(monkeypatch):
    """A foreground recipe replays live only with BOTH the flag and
    firstmate's grant. Either missing refuses with the fix - the
    suite never takes the captain's screen unasked."""
    monkeypatch.delenv(replay_mod.CAPTAIN_WINDOW_ENV, raising=False)
    spec = next(spec for spec in recipe_registry()
                if spec.name == "qualifier-sample")
    assert replay_mod._live_guard(spec, False) is not None
    assert replay_mod._live_guard(spec, True) is not None
    monkeypatch.setenv(replay_mod.CAPTAIN_WINDOW_ENV, "granted")
    assert replay_mod._live_guard(spec, True) is None
    assert replay_mod._live_guard(spec, False) is not None
    background = next(spec for spec in recipe_registry()
                      if spec.name == "grade-node-add")
    assert replay_mod._live_guard(background, False) is None


def test_fake_resolve_advances_only_on_known_menus():
    """The fake proves plumbing, not wishes: unknown presses record
    but change nothing, while the measured routes advance page,
    panels, nodes and the tracker."""
    fake = FakeResolve()
    fake.press(["Color", "Nodes", "Add Serial Node"])
    assert fake.node_count() == 2
    fake.press(["Color", "Nodes", "Delete Current Node"])
    assert fake.node_count() == 1
    fake.press(["Workspace", "Show Panel in Workspace", "Effects"])
    assert "Effects" in fake.panels
    fake.press(["Color", "Tracker", "Forward"])
    assert fake.tracked is True
