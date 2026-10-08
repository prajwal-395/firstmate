"""The replay suite: every recipe, end to end, proved.

Each test names the defect it catches: a step with no expect
reporting done, a raw coordinate surviving audit, a menu write
proved by acceptance instead of read-back, a restore that runs
before the result is read, a foreground take without the captain's
window. The offline layer runs on fakes - no Resolve, no screen -
and the live layer runs only in Ren Qualification.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from library.tools.computer_use import replay as replay_mod
from library.tools.computer_use.replay import (
    FakeDriver,
    FakeResolve,
    RecipeAudit,
    RecipeReport,
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


def test_offline_replay_never_requests_a_resolve_lease(tmp_path, monkeypatch):
    """Offline fake replays do not wait on or occupy the live broker."""
    from library.tools.computer_use import scripts as scripts_module

    def refused_lease(*args, **kwargs):
        raise AssertionError("offline replay requested a Resolve lease")

    monkeypatch.setattr(scripts_module, "resolve_lease", refused_lease)
    report = run_suite_offline(tmp_path)
    assert report["failed"] == []


def test_live_failure_capture_is_saved_before_take_restore(tmp_path):
    """A refused live step leaves a screen capture in its recipe row."""
    import base64

    spec = recipe_registry()[0]
    audit, _script = audit_recipe(spec)
    report = RecipeReport(spec.name, spec.background, audit)

    class CaptureClient:
        def call(self, method, params=None):
            assert method == "capture_window"
            return {"image": base64.b64encode(b"screen evidence").decode()}

    replay_mod._capture_live(CaptureClient(), spec, tmp_path,
                             report, "failed")

    capture = tmp_path / f"{spec.name}-live-failed.png"
    assert report.captures == [str(capture)]
    assert capture.read_bytes() == b"screen evidence"


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


def test_live_take_activation_requires_a_driver_receipt():
    """Qt AX presses need Resolve's shipped NSWorkspace activation
    path, and a missing activation receipt must refuse before a press."""
    driver = FakeDriver()
    replay_mod._activate_resolve_for_live_take(driver)
    assert driver.activated is True

    class NoReceipt:
        def call(self, method, params=None):
            assert method == "activate_resolve"
            return {"activated": False}

    try:
        replay_mod._activate_resolve_for_live_take(NoReceipt())
        assert False, "activation without receipt should refuse"
    except RuntimeError as exc:
        assert "did not read back" in str(exc)


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


def test_window_presses_establish_the_color_page():
    """Defect, caught live 2026-10-07: the replay pressed the window
    menus against the Edit page - `ok` via ax_path, nodes 1 -> 1.
    Every recipe whose press acts on the Color page establishes it
    (playhead, OpenPage, Scopes) before the runner presses."""
    assert {"grade-node-remove", "window-create", "window-delete",
            "window-track"} <= set(replay_mod.COLOR_PAGE_SPECS)


class _LiveItem:
    """The scratch clip the live helpers read: nodes plus an id."""

    def __init__(self, nodes: int, uid: str = "scratch-uid"):
        self._nodes = nodes
        self._uid = uid

    def GetNumNodes(self):
        return self._nodes

    def GetUniqueId(self):
        return self._uid

    def GetStart(self):
        return 0

    def GetDuration(self):
        return 72


class _LiveTimeline:
    """The scratch timeline: the playhead lands, the clip reads back."""

    def __init__(self, item):
        self._item = item

    def SetCurrentTimecode(self, _timecode):
        return True

    def GetCurrentVideoItem(self):
        return self._item


class _LiveResolve:
    """OpenPage answers color; the page reads back what it opened."""

    def __init__(self):
        self.page = "edit"

    def OpenPage(self, page):
        self.page = page
        return True

    def GetCurrentPage(self):
        return self.page


class _LiveClient:
    """read_text names the labels given; run_command bumps the item."""

    def __init__(self, item, labels=("Scopes",), menus_closed=True):
        self._item = item
        self._labels = labels
        self._menus_closed = menus_closed
        self.pressed = []

    def call(self, method, params=None):
        if method == "read_text":
            return {"elements": [{"text": text}
                                 for text in self._labels]}
        if method == "run_command":
            self.pressed.append((params or {}).get("path"))
            self._item._nodes += 1
            return {"pressed": True,
                    "menus_closed": self._menus_closed}
        raise AssertionError(f"unexpected call {method!r}")


def test_establish_lands_the_playhead_and_reads_scopes():
    """The Color-page establishment puts the playhead on the scratch
    clip, opens color, and reads Scopes back - a press without it
    proved nothing live."""
    item = _LiveItem(1)
    resolve = _LiveResolve()
    client = _LiveClient(item)
    reason = replay_mod._live_establish_color_page(
        resolve, _LiveTimeline(item), item, client)
    assert reason is None
    assert resolve.page == "color"


def test_establish_refuses_without_scopes():
    """No Scopes label is a refusal naming the wrong page - the menu
    would press against pixels it cannot see, and the suite fails
    the recipe instead of claiming blind."""
    item = _LiveItem(1)
    client = _LiveClient(item, labels=("Master",))
    reason = replay_mod._live_establish_color_page(
        _LiveResolve(), _LiveTimeline(item), item, client)
    assert reason is not None and "Scopes" in reason


def test_delete_setup_grows_one_node_to_two():
    """A fresh scratch holds one node and deleting it is refused
    upstream - the replay adds the serial first, so the delete it
    proves is the deterministic last-of-two."""
    item = _LiveItem(1)
    client = _LiveClient(item)
    assert replay_mod._live_ensure_two_nodes(item, client) is None
    assert item.GetNumNodes() == 2
    assert client.pressed == [["Color", "Nodes", "Add Serial Node"]]


def test_delete_setup_keeps_two_nodes():
    """Two nodes need no setup press: the helper leaves the clip it
    was given alone."""
    item = _LiveItem(2)
    client = _LiveClient(item)
    assert replay_mod._live_ensure_two_nodes(item, client) is None
    assert client.pressed == []


def test_delete_setup_stops_when_its_menu_is_not_closed():
    """The setup serial counts as an action too: it cannot continue
    into the delete recipe when Resolve did not read the menu closed."""
    item = _LiveItem(1)
    client = _LiveClient(item, menus_closed=False)
    reason = replay_mod._live_ensure_two_nodes(item, client)
    assert reason is not None
    assert "MenuClosureError" in reason


def test_grade_node_remove_setup_grows_a_fresh_clip_to_three_nodes():
    """The remove recipe is authored for node 2 of 3. Its setup must
    build that exact graph before the recipe starts, with each serial
    addition closed and read back."""
    item = _LiveItem(1)
    client = _LiveClient(item)

    assert replay_mod._live_ensure_node_count(
        item, client, 3, "grade-node-remove") is None
    assert item.GetNumNodes() == 3
    assert client.pressed == [
        ["Color", "Nodes", "Add Serial Node"],
        ["Color", "Nodes", "Add Serial Node"],
    ]


def test_grade_node_remove_setup_refuses_an_oversized_graph():
    """A setup with more nodes than the recipe expects cannot silently
    target a different node."""
    item = _LiveItem(4)
    client = _LiveClient(item)

    reason = replay_mod._live_ensure_node_count(
        item, client, 3, "grade-node-remove")

    assert reason is not None and "above the 3-node setup" in reason
    assert client.pressed == []


def test_grade_node_remove_setup_requires_explicit_menu_closure():
    """An accepted press without a positive close receipt cannot be
    followed by another setup press or by the remove recipe."""
    item = _LiveItem(1)
    client = _LiveClient(item, menus_closed=None)

    reason = replay_mod._live_ensure_node_count(
        item, client, 3, "grade-node-remove")

    assert reason is not None and "MenuClosureError" in reason
    assert len(client.pressed) == 1


def test_grade_node_remove_proof_requires_the_declared_three_to_two_change():
    """The earlier live report passed 1 -> 0. That is not proof of
    removing node 2 from the declared three-node start."""
    assert replay_mod._live_nodes_ok("grade-node-remove", 3, 2)
    assert not replay_mod._live_nodes_ok("grade-node-remove", 1, 0)
    assert not replay_mod._live_nodes_ok("grade-node-remove", 3, 1)


@pytest.mark.parametrize(
    ("result_nodes", "expected_verdict", "capture_suffix"),
    [(2, "pass", "passed"), (1, "fail", "failed")],
)
def test_live_grade_node_remove_reads_the_recipe_result_before_cleanup(
        tmp_path, monkeypatch, result_nodes, expected_verdict, capture_suffix):
    """The live route builds node 2 of 3 before acting, then records
    the exact removal before deleting the scratch timeline."""
    import base64

    from library.tools.computer_use import client as client_module
    from library.tools.computer_use import scripts as scripts_module
    from library.tools import qualification_project, resolve_lock

    spec = next(spec for spec in recipe_registry()
                if spec.name == "grade-node-remove")
    item = _LiveItem(1)
    resolve = _LiveResolve()
    resolve.GetProjectManager = lambda: object()
    project = SimpleNamespace(GetMediaPool=lambda: object())
    order = []

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def call(self, method, params=None):
            if method == "run_command":
                item._nodes += 1
                return {"pressed": True, "menus_closed": True}
            if method == "capture_window":
                return {"image": base64.b64encode(b"capture").decode()}
            raise AssertionError(f"unexpected client call {method!r}")

    def run_recipe(_client, _script, deadline):
        assert deadline == 123
        assert item.GetNumNodes() == 3
        order.append(("act", item.GetNumNodes()))
        item._nodes = result_nodes
        return SimpleNamespace(steps=[], outcome="done")

    monkeypatch.setattr(client_module, "DriverClient", Client)
    monkeypatch.setattr(scripts_module, "run_script", run_recipe)
    monkeypatch.setattr(
        qualification_project, "switch_project",
        lambda manager, name: project if name == "Ren Qualification"
        else None)
    monkeypatch.setattr(
        replay_mod, "_scratch_timeline",
        lambda _project, _pool, _name: (object(), item))
    monkeypatch.setattr(replay_mod, "_delete_scratch",
                        lambda _pool, _timeline: order.append(("cleanup",)))
    monkeypatch.setattr(resolve_lock, "assert_current_timeline",
                        lambda _project, _timeline: None)
    monkeypatch.setattr(replay_mod, "_replay_sleep", lambda _seconds: None)

    def establish_color(_resolve, _timeline, _item, _client):
        resolve.page = "color"
        return None

    monkeypatch.setattr(replay_mod, "_live_establish_color_page",
                        establish_color)
    take = SimpleNamespace(resolve=resolve, deadline=123)

    report = replay_mod._live_run_background(spec, take, tmp_path)

    assert report.verdict == expected_verdict
    assert {proof["name"]: proof["passed"] for proof in report.proofs} == {
        "nodes": result_nodes == 2, "page": True}
    assert next(proof["detail"] for proof in report.proofs
                if proof["name"] == "nodes") == f"nodes 3 -> {result_nodes}"
    assert order == [("act", 3), ("cleanup",)]
    assert report.captures[0].endswith(
        f"grade-node-remove-live-{capture_suffix}.png")


def test_restore_proof_names_this_takes_own_start():
    """Restore evidence is bound to the snapshot this take captured,
    even when the previous take or watcher saw another timeline."""
    start = {"project": "Podcast", "timeline_id": "timeline-04",
             "timeline": "Reel 04", "page": "edit"}
    take = SimpleNamespace(borrowed_from=start, restore_verified=True,
                           restore_error="")
    report = RecipeReport("grade-node-add", True,
                          RecipeAudit("grade-node-add", 2),
                          verdict="pass")

    assert replay_mod._record_take_restore(report, take)
    restore = next(proof for proof in report.proofs
                   if proof["name"] == "restore")
    assert restore["passed"] is True
    assert "Reel 04" in restore["detail"]
    assert "timeline-04" in restore["detail"]
    assert "Reel 11" not in restore["detail"]


def test_restore_mismatch_fails_recipe():
    """A borrowed() mismatch cannot leave a live recipe green."""
    take = SimpleNamespace(
        borrowed_from={"project": "Podcast", "timeline_id": "timeline-04",
                       "timeline": "Reel 04", "page": "edit"},
        restore_verified=False,
        restore_error="timeline id did not read back")
    report = RecipeReport("grade-node-add", True,
                          RecipeAudit("grade-node-add", 2),
                          verdict="pass")

    assert replay_mod._record_take_restore(report, take) is False
    assert report.verdict == "fail"
    assert "timeline id did not read back" in report.reason


def test_live_replay_stops_after_restore_mismatch(tmp_path, monkeypatch):
    """A failed cursor restore prevents the suite from starting its
    next live take against a state no longer known to belong to it."""
    specs = [spec for spec in recipe_registry()
             if spec.name in ("grade-node-add", "grade-node-remove")]
    started = []
    written = []

    @contextmanager
    def mismatched_take(purpose):
        started.append(purpose)
        yield SimpleNamespace(
            resolve=object(), deadline=1,
            borrowed_from={"project": "Podcast", "timeline_id": "t04",
                           "timeline": "Reel 04", "page": "edit"},
            restore_verified=False, restore_error="timeline mismatch")

    def successful_recipe(spec, _take, _out):
        audit, _script = audit_recipe(spec)
        return RecipeReport(spec.name, True, audit, verdict="pass")

    monkeypatch.setattr(replay_mod, "recipe_registry", lambda: specs)
    monkeypatch.setattr(replay_mod, "_live_run_background", successful_recipe)
    monkeypatch.setattr(replay_mod, "write_report",
                        lambda report, _out: written.append(report)
                        or tmp_path / "report.json")
    monkeypatch.setattr(
        "library.tools.computer_use.take.ui_take", mismatched_take)
    monkeypatch.setattr(
        "library.tools.resolved.client.serving", lambda: True)

    result = replay_mod._main_live(
        SimpleNamespace(allow_foreground=False), tmp_path, ())

    assert result == 1
    assert started == ["recipe replay grade-node-add"]
    assert written[0]["failed"] == ["grade-node-add", "grade-node-remove"]
    assert "not run" in written[0]["recipes"][1]["reason"]
    restore = next(proof for proof in written[0]["recipes"][0]["proofs"]
                   if proof["name"] == "restore")
    assert restore["passed"] is False


def test_live_replay_stops_after_unverified_menu(tmp_path, monkeypatch):
    """An unverified menu is reported with evidence and stops later takes."""
    specs = [spec for spec in recipe_registry()
             if spec.name in ("grade-node-add", "grade-node-remove")]
    started = []
    written = []

    @contextmanager
    def successful_restore(purpose):
        started.append(purpose)
        yield SimpleNamespace(
            resolve=object(), deadline=1,
            borrowed_from={"project": "Podcast", "timeline_id": "t04",
                           "timeline": "Reel 04", "page": "edit"},
            restore_verified=True, restore_error="")

    def menu_failure(spec, _take, _out):
        audit, _script = audit_recipe(spec)
        return RecipeReport(
            spec.name, True, audit, verdict="fail",
            reason=("MenuClosureError: menu action returned "
                    "menus_closed=false; close evidence={'expanded': True}"))

    monkeypatch.setattr(replay_mod, "recipe_registry", lambda: specs)
    monkeypatch.setattr(replay_mod, "_live_run_background", menu_failure)
    monkeypatch.setattr(replay_mod, "write_report",
                        lambda report, _out: written.append(report)
                        or tmp_path / "report.json")
    monkeypatch.setattr(
        "library.tools.computer_use.take.ui_take", successful_restore)
    monkeypatch.setattr(
        "library.tools.resolved.client.serving", lambda: True)

    result = replay_mod._main_live(
        SimpleNamespace(allow_foreground=False), tmp_path, ())

    assert result == 1
    assert started == ["recipe replay grade-node-add"]
    assert written[0]["failed"] == ["grade-node-add", "grade-node-remove"]
    assert "MenuClosureError" in written[0]["recipes"][0]["reason"]
    assert "not run" in written[0]["recipes"][1]["reason"]


def test_live_replay_stops_after_activation_fails(tmp_path, monkeypatch):
    """An activation miss fails before recipe input; the suite does not
    spend later takes repeating the same frontmost-app failure."""
    specs = [spec for spec in recipe_registry()
             if spec.name in ("grade-node-add", "grade-node-remove")]
    started = []
    written = []

    @contextmanager
    def successful_restore(purpose):
        started.append(purpose)
        yield SimpleNamespace(
            resolve=object(), deadline=1,
            borrowed_from={"project": "Podcast", "timeline_id": "t04",
                           "timeline": "Reel 04", "page": "edit"},
            restore_verified=True, restore_error="")

    def activation_failure(spec, _take, _out):
        audit, _script = audit_recipe(spec)
        return RecipeReport(
            spec.name, True, audit, verdict="fail",
            reason="RuntimeError: Resolve activation did not read back")

    monkeypatch.setattr(replay_mod, "recipe_registry", lambda: specs)
    monkeypatch.setattr(replay_mod, "_live_run_background", activation_failure)
    monkeypatch.setattr(replay_mod, "write_report",
                        lambda report, _out: written.append(report)
                        or tmp_path / "report.json")
    monkeypatch.setattr(
        "library.tools.computer_use.take.ui_take", successful_restore)
    monkeypatch.setattr(
        "library.tools.resolved.client.serving", lambda: True)

    result = replay_mod._main_live(
        SimpleNamespace(allow_foreground=False), tmp_path, ())

    assert result == 1
    assert started == ["recipe replay grade-node-add"]
    assert written[0]["failed"] == ["grade-node-add", "grade-node-remove"]
    assert "not run" in written[0]["recipes"][1]["reason"]
