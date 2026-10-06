"""The resolve.ui_transaction broker job: exclusivity, restore, sandbox, receipt.

Every test runs the broker on its OWN socket (`PIPELINE_RESOLVE_LOCK_DIR`
pointed at a tmp dir) with a FakeBackend as Resolve, so nothing contends
with a live broker and no test touches the captain's Resolve. The
FakeBackend is both the `resolve` handle (snapshot, establish, restore)
and the driver's backend (UI actions), so an action that changes the
page is visible to the next snapshot and the restore puts it back.

The fake lives HERE, in the tests, and is injected through the runner's
`driver_factory` - production gets the official DriverClient, which
refuses the job when the driver is not installed.
"""

import json
import time

import pytest

from library.tools import resolve_lock
from library.tools.resolved import jobs as job_kinds
from library.tools.resolved import scheduler
from library.tools.resolved.server import Broker
from library.tools.resolved.store import JobStore
from library.tools.resolved import ui_transaction
from library.tools.computer_use.client import DriverDead
from library.tools.reel_edit_history import ACT_UI_TRANSACTION


# ── The fake driver, injected by the tests ───────────────────────────

class FakeTimeline:
    def __init__(self, name, *, timecode="00:00:00:00", start_frame=0,
                 end_frame=100):
        self._name = name
        self._timecode = timecode
        self._start_frame = int(start_frame)
        self._end_frame = int(end_frame)

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return f"fake-timeline:{self._name}"

    def GetCurrentTimecode(self):
        return self._timecode

    def SetCurrentTimecode(self, timecode):
        self._timecode = str(timecode)

    def GetStartFrame(self):
        return self._start_frame

    def GetEndFrame(self):
        return self._end_frame


class FakeProject:
    def __init__(self, backend, name, timelines):
        self._backend = backend
        self._name = name
        self._timelines = timelines
        self._current = 0 if timelines else None

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return f"fake-project:{self._name}"

    def GetCurrentTimeline(self):
        if self._current is None:
            return None
        return self._timelines[self._current]

    def SetCurrentTimeline(self, timeline):
        for index, candidate in enumerate(self._timelines):
            if candidate is timeline:
                self._current = index
                return
        raise RuntimeError(f"no timeline {timeline!r} in {self._name!r}")

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        if 1 <= index <= len(self._timelines):
            return self._timelines[index - 1]
        return None


class FakeProjectManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class FakeBackend:
    """An in-memory Resolve: the scripting API surface plus UI state."""

    def __init__(self, *, project="Scratch", timelines=None, page="edit",
                 windows=None, ax_tree=None, node_count=0):
        self._project = FakeProject(self, project, timelines or [])
        self._page = page
        self._windows = windows or [
            {"id": 1, "title": "DaVinci Resolve",
             "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080}}]
        self._ax_tree = ax_tree if ax_tree is not None else _empty_tree()
        self._pointer = {"x": 0, "y": 0}
        self._frontmost = "Resolve"
        self._node_count = int(node_count)

    def GetProjectManager(self):
        return FakeProjectManager(self._project)

    def GetCurrentPage(self):
        return self._page

    def OpenPage(self, page):
        self._page = str(page)

    def GetVersionString(self):
        return "21.1.0-fake"

    def get_windows(self):
        return [dict(window) for window in self._windows]

    def get_ax_tree(self):
        return self._ax_tree

    def get_pointer_state(self):
        return dict(self._pointer)

    def get_frontmost_app(self):
        return self._frontmost

    def node_count(self):
        return self._node_count

    def set_node_count(self, count):
        self._node_count = int(count)

    def find_element(self, *, role="", title="", title_contains="",
                     label="", value="", path=None):
        return find_in_tree(self._ax_tree, role=role, title=title,
                            title_contains=title_contains, label=label,
                            value=value, path=path or [])

    def find_all_elements(self, *, role="", title="", title_contains="",
                          label="", value="", path=None):
        found = []
        find_all_in_tree(self._ax_tree, found, role=role, title=title,
                         title_contains=title_contains, label=label,
                         value=value, path=path or [])
        return found

    def element(self, ref):
        return element_by_ref(self._ax_tree, ref)

    def set_focused(self, ref):
        set_focused(self._ax_tree, ref)

    def press(self, ref):
        element = self.element(ref)
        if element is None:
            raise RuntimeError(f"no element {ref!r} to press")
        element["pressed"] = True

    def set_value(self, ref, value):
        element = self.element(ref)
        if element is None:
            raise RuntimeError(f"no element {ref!r} to set")
        element["value"] = str(value)

    def type_text(self, text):
        focused = focused_element(self._ax_tree)
        if focused is None:
            raise RuntimeError("no focused element to type into")
        focused["value"] = str(focused.get("value") or "") + str(text)

    def click(self, x, y):
        _check_point_in_window(self._windows, x, y)
        self._pointer = {"x": float(x), "y": float(y)}

    def drag(self, start, end):
        _check_point_in_window(self._windows, start.get("x", 0),
                               start.get("y", 0))
        _check_point_in_window(self._windows, end.get("x", 0),
                               end.get("y", 0))
        self._pointer = {"x": float(end.get("x", 0)),
                         "y": float(end.get("y", 0))}

    def scroll(self, dx, dy, x=0, y=0):
        _check_point_in_window(self._windows, x, y)

    def activate(self):
        self._frontmost = "Resolve"

    def wait_for(self, predicate, timeout=5.0, poll_interval=0.5):
        deadline = time.time() + max(0.0, float(timeout))
        while True:
            if _predicate_holds(self._ax_tree, predicate):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(0.01)

    def read_text(self, region=None):
        labels = []
        _collect_text(self._ax_tree, region, labels)
        return " ".join(labels)

    def capture_window(self):
        window = self._windows[0] if self._windows else {}
        return {"window": window.get("id"), "bounds": window.get("bounds"),
                "image": f"fake-capture:{window.get('title')}"}

    def capture_region(self, bounds):
        _check_point_in_window(self._windows, bounds.get("x", 0),
                               bounds.get("y", 0))
        return {"bounds": bounds, "image": "fake-capture:region"}


class FakeDriver:
    """The official DriverClient's call(method, params) interface, against a FakeBackend."""

    def __init__(self, backend):
        self._backend = backend
        self._refs = {}
        self._ref_counter = 0

    def call(self, method, params=None):
        params = dict(params or {})
        if method == "activate_resolve":
            self._backend.activate()
            return {}
        if method == "get_windows":
            return {"windows": self._backend.get_windows()}
        if method == "get_accessibility_tree":
            return {"tree": self._backend.get_ax_tree()}
        if method == "find_element":
            criteria = {k: params[k] for k in
                        ("role", "title", "title_contains", "label", "value")
                        if params.get(k)}
            if params.get("path"):
                criteria["path"] = params["path"]
            refs = self._backend.find_all_elements(**criteria)
            elements = []
            for ref in refs:
                element = self._backend.element(ref)
                self._ref_counter += 1
                driver_ref = f"fake-ref-{self._ref_counter}"
                self._refs[driver_ref] = ref
                elements.append({"ref": driver_ref,
                                 "role": element.get("role", ""),
                                 "title": element.get("title", "")})
            return {"elements": elements}
        if method == "press":
            self._backend.press(self._resolve_ref(params.get("element")))
            return {"pressed": True}
        if method == "set_value":
            self._backend.set_value(self._resolve_ref(params.get("element")),
                                    params.get("value", ""))
            return {"set": True}
        if method == "focus":
            self._backend.set_focused(self._resolve_ref(params.get("element")))
            return {"focused": True}
        if method == "type_text":
            self._backend.type_text(params.get("text", ""))
            return {"typed": len(params.get("text", ""))}
        if method == "key":
            focused = focused_element(self._backend.get_ax_tree())
            if focused is None:
                raise RuntimeError("no focused element to send a key to")
            keys = focused.setdefault("keys", [])
            keys.append({"key": str(params.get("key", "")),
                         "modifiers": list(params.get("modifiers") or ())})
            return {"sent": True}
        if method == "click":
            self._backend.click(params.get("x", 0), params.get("y", 0))
            return {"clicked": True}
        if method == "drag":
            self._backend.drag({"x": params.get("start_x", 0),
                                "y": params.get("start_y", 0)},
                               {"x": params.get("end_x", 0),
                                "y": params.get("end_y", 0)})
            return {"dragged": True}
        if method == "scroll":
            self._backend.scroll(params.get("delta_x", 0),
                                 params.get("delta_y", 0),
                                 params.get("x", 0), params.get("y", 0))
            return {"scrolled": True}
        if method == "wait_for":
            met = self._backend.wait_for(params.get("predicate", {}),
                                         params.get("timeout", 5.0))
            return {"met": met}
        if method == "read_text":
            return {"text": self._backend.read_text(params.get("region"))}
        if method == "get_pointer_state":
            return self._backend.get_pointer_state()
        if method == "get_frontmost_app":
            return {"name": self._backend.get_frontmost_app()}
        if method == "capture_window":
            return self._backend.capture_window()
        if method == "capture_region":
            return self._backend.capture_region(params)
        raise RuntimeError(f"unknown method: {method}")

    def _resolve_ref(self, ref):
        if ref in self._refs:
            return self._refs[ref]
        return ref

    def close(self):
        pass


def _empty_tree():
    return {"ref": "root", "role": "application", "title": "DaVinci Resolve",
            "label": "", "value": "",
            "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
            "focused": False, "children": []}


def _matches(element, *, role, title, title_contains, label, value,
             path=()):
    if role and element.get("role") != role:
        return False
    if title and element.get("title") != title:
        return False
    if title_contains and title_contains not in str(element.get("title") or ""):
        return False
    if label and element.get("label") != label:
        return False
    if value and element.get("value") != value:
        return False
    if path and list(element.get("path") or []) != list(path):
        return False
    return True


def find_in_tree(tree, **criteria):
    if _matches(tree, **criteria):
        return str(tree.get("ref") or "")
    for child in tree.get("children") or ():
        hit = find_in_tree(child, **criteria)
        if hit is not None:
            return hit
    return None


def find_all_in_tree(tree, found, **criteria):
    if _matches(tree, **criteria):
        found.append(str(tree.get("ref") or ""))
    for child in tree.get("children") or ():
        find_all_in_tree(child, found, **criteria)


def element_by_ref(tree, ref):
    if str(tree.get("ref") or "") == ref:
        return tree
    for child in tree.get("children") or ():
        hit = element_by_ref(child, ref)
        if hit is not None:
            return hit
    return None


def set_focused(tree, ref):
    if str(tree.get("ref") or "") == ref:
        tree["focused"] = True
        return
    for child in tree.get("children") or ():
        set_focused(child, ref)


def focused_element(tree):
    if tree.get("focused"):
        return tree
    for child in tree.get("children") or ():
        hit = focused_element(child)
        if hit is not None:
            return hit
    return None


def _predicate_holds(tree, predicate):
    if not isinstance(tree, dict):
        return False
    kind = predicate.get("type")
    if kind == "text_visible":
        text = str(predicate.get("text") or "")
        haystack = " ".join(str(tree.get(key) or "")
                            for key in ("label", "title", "value"))
        if text in haystack:
            return True
    if kind == "element_visible":
        role = predicate.get("role", "")
        title = predicate.get("title", "")
        if (not role or tree.get("role") == role) and \
           (not title or tree.get("title") == title):
            return True
    return any(_predicate_holds(child, predicate)
               for child in tree.get("children") or ())


def _collect_text(element, region, out):
    label = str(element.get("label") or element.get("title") or "")
    if label and (region is None or _in_bounds(element, region)):
        out.append(label)
    for child in element.get("children") or ():
        _collect_text(child, region, out)


def _in_bounds(element, region):
    bounds = element.get("bounds") or {}
    for key, want in region.items():
        if bounds.get(key) != want:
            return False
    return True


def _check_point_in_window(windows, x, y):
    for window in windows:
        bounds = window.get("bounds") or {}
        if (bounds.get("x", 0) <= float(x) <= bounds.get("x", 0)
                + bounds.get("width", 0)
                and bounds.get("y", 0) <= float(y) <= bounds.get("y", 0)
                + bounds.get("height", 0)):
            return
    raise RuntimeError(
        f"the point ({x}, {y}) is outside every Resolve window")


def _fake_driver_factory(backend):
    return FakeDriver(backend)


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def lock_dir(monkeypatch):
    import tempfile
    from pathlib import Path
    directory = Path(tempfile.mkdtemp(prefix="ren-uitxn-", dir="/tmp"))
    monkeypatch.setattr(resolve_lock, "LOCK_DIR_ENV", "PIPELINE_RESOLVE_LOCK_DIR")
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    yield directory
    import shutil
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def unguarded(monkeypatch):
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)


@pytest.fixture
def backend():
    tree = {
        "ref": "root", "role": "application", "title": "DaVinci Resolve",
        "label": "", "value": "",
        "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080},
        "focused": False,
        "children": [
            {"ref": "color-btn", "role": "button", "title": "Color",
             "label": "Color", "value": "",
             "bounds": {"x": 100, "y": 50, "width": 80, "height": 30},
             "focused": False, "children": []},
            {"ref": "magic-mask", "role": "button", "title": "Magic Mask",
             "label": "Magic Mask", "value": "",
             "bounds": {"x": 300, "y": 200, "width": 120, "height": 30},
             "focused": False, "children": []},
        ],
    }
    return FakeBackend(
        project="Scratch",
        timelines=[FakeTimeline("Reel 07"), FakeTimeline("Reel 02")],
        page="edit",
        ax_tree=tree,
        node_count=3,
    )


@pytest.fixture
def broker(lock_dir, unguarded, backend, monkeypatch):
    """A broker serving on the test's socket, connected to the FakeBackend.

    The runner's default driver factory is monkeypatched to the fake, so
    the broker's jobs run against the FakeBackend. Production uses the
    official DriverClient, which refuses when the driver is not installed.
    """
    monkeypatch.setattr(ui_transaction, "driver_factory",
                        _fake_driver_factory)
    store = JobStore(lock_dir / "ren-resolved.sqlite3")
    broker = Broker(store, connect=lambda: backend)
    yield broker
    broker.stop()
    store.close()


def _submit(broker, params, **kwargs):
    return broker.submit("resolve.ui_transaction", params, **kwargs)


def _wait_terminal(broker, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = broker.result(job_id)
        if job["state"] in ("done", "failed", "rejected", "cancelled"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach a terminal state")


# ── prepare: the shape the scheduler admits ─────────────────────────

def test_prepare_is_exclusive_mutation(backend):
    shape = job_kinds.prepare("resolve.ui_transaction", {
        "project": "Scratch",
        "timeline": "Reel 07",
        "actions": [{"action": "click", "target": {
            "kind": "viewer_coordinate", "x": 100, "y": 100}}],
    })
    assert shape["mode"] == scheduler.EXCLUSIVE
    assert shape["priority"] == "mutation"
    assert shape["executed"] is True
    assert shape["project"] == "Scratch"
    assert shape["timeline"] == "Reel 07"
    assert shape["locality"] == "timeline"


def test_prepare_refuses_unknown_action(backend):
    with pytest.raises(job_kinds.JobRefused):
        job_kinds.prepare("resolve.ui_transaction", {
            "project": "Scratch",
            "actions": [{"action": "close_application", "target": {
                "kind": "viewer_coordinate", "x": 0, "y": 0}}],
        })


def test_prepare_refuses_empty_actions(backend):
    with pytest.raises(job_kinds.JobRefused):
        job_kinds.prepare("resolve.ui_transaction", {
            "project": "Scratch", "actions": []})


# ── The typed refusal: no native driver, no committed receipt ──────

def test_refuses_and_files_no_receipt_when_native_driver_missing(
        broker, backend, tmp_path, monkeypatch):
    """With no native driver installed, the job is REJECTED and no
    committed receipt is filed into the edit history."""
    monkeypatch.setattr(ui_transaction, "driver_factory",
                        _raise_dead_factory)
    review_dir = str(tmp_path / "review")
    params = {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [{"action": "click", "target": {
            "kind": "viewer_coordinate", "x": 100, "y": 100}}],
        "review_dir": review_dir,
    }
    submitted = _submit(broker, params)
    job = _wait_terminal(broker, submitted["id"])
    assert job["state"] == "rejected"
    assert "driver binary" in job["error"]
    from library.tools import reel_edit_history as history
    entries = history.recorded_history(review_dir, "Reel 07")
    assert entries == []


def test_runner_raises_typed_refusal_without_native_driver(backend):
    """The runner itself raises JobRefused naming the missing driver."""
    with pytest.raises(job_kinds.JobRefused) as excinfo:
        ui_transaction.run(
            {"id": "x", "params": {
                "project": "Scratch", "timeline": "Reel 07",
                "actions": [{"action": "click", "target": {
                    "kind": "viewer_coordinate", "x": 0, "y": 0}}]}},
            backend, factory=_raise_dead_factory)
    assert "driver binary" in str(excinfo.value)


def _raise_dead_factory(backend):
    raise DriverDead(
        "the driver binary is not built",
        "cargo build --release has not run",
        "run: cargo build --release in native/resolve-control")


# ── Exclusivity against a concurrent exclusive job ──────────────────

def test_exclusive_job_waits_for_a_running_write(broker, backend):
    """A ui_transaction (EXCLUSIVE) queues behind a running exclusive job
    and does not start until that job finishes."""
    first = _submit(broker, {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [{"action": "wait", "for": {"text_visible": "NEVER"},
                      "timeout_seconds": 1.5}],
    })
    deadline = time.time() + 5
    while time.time() < deadline:
        if broker.result(first["id"])["state"] == "running":
            break
        time.sleep(0.02)
    assert broker.result(first["id"])["state"] == "running"

    second = _submit(broker, {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [{"action": "click", "target": {
            "kind": "viewer_coordinate", "x": 100, "y": 100}}],
    })
    time.sleep(0.2)
    assert broker.result(second["id"])["state"] == "queued"

    first_job = _wait_terminal(broker, first["id"])
    assert first_job["state"] == "done"
    assert first_job["result"]["status"] == "failed"
    second_job = _wait_terminal(broker, second["id"])
    assert second_job["state"] == "done"
    assert second_job["result"]["status"] == "committed"


# ── Context restored after a failed action ──────────────────────────

def test_context_restored_after_failed_action(broker, backend):
    """A transaction whose action fails still restores page/timeline/playhead."""
    backend.OpenPage("edit")
    project = backend.GetProjectManager().GetCurrentProject()
    project.SetCurrentTimeline(project.GetTimelineByIndex(1))
    project.GetCurrentTimeline().SetCurrentTimecode("00:00:10:00")

    params = {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [
            {"action": "click", "target": {
                "kind": "viewer_coordinate", "x": 100, "y": 100}},
            {"action": "press", "target": {
                "kind": "accessibility_element", "role": "button",
                "label": "Nonexistent"}},
        ],
    }
    submitted = _submit(broker, params)
    job = _wait_terminal(broker, submitted["id"])
    assert job["state"] == "done"
    result = job["result"]
    assert result["status"] == "failed"
    assert "Nonexistent" in result["result"]["error"]
    assert backend.GetCurrentPage() == "edit"
    project = backend.GetProjectManager().GetCurrentProject()
    assert project.GetCurrentTimeline().GetName() == "Reel 07"
    assert project.GetCurrentTimeline().GetCurrentTimecode() == "00:00:10:00"


def test_context_restored_after_failed_verification(broker, backend):
    """A transaction whose verification fails still restores the context."""
    backend.OpenPage("color")
    project = backend.GetProjectManager().GetCurrentProject()
    project.SetCurrentTimeline(project.GetTimelineByIndex(2))
    project.GetCurrentTimeline().SetCurrentTimecode("00:00:05:00")

    params = {
        "project": "Scratch", "timeline": "Reel 02",
        "actions": [{"action": "click", "target": {
            "kind": "viewer_coordinate", "x": 100, "y": 100}}],
        "verification": {"kind": "api_readback", "getter": "page",
                         "equals": "media"},
    }
    submitted = _submit(broker, params)
    job = _wait_terminal(broker, submitted["id"])
    result = job["result"]
    assert result["status"] == "failed"
    assert result["verification"]["matched"] is False
    project = backend.GetProjectManager().GetCurrentProject()
    assert backend.GetCurrentPage() == "color"
    assert project.GetCurrentTimeline().GetName() == "Reel 02"
    assert project.GetCurrentTimeline().GetCurrentTimecode() == "00:00:05:00"


# ── Sandbox refusals ────────────────────────────────────────────────

def test_sandbox_refuses_forbidden_intent(backend):
    with pytest.raises(job_kinds.JobRefused):
        job_kinds.prepare("resolve.ui_transaction", {
            "project": "Scratch",
            "actions": [{"action": "click", "intent": "delete_project",
                         "target": {"kind": "viewer_coordinate",
                                    "x": 0, "y": 0}}],
        })


def test_sandbox_refuses_coordinate_outside_window(broker, backend):
    """A coordinate target outside the Resolve window is refused by the driver."""
    params = {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [{"action": "click", "target": {
            "kind": "viewer_coordinate", "x": 5000, "y": 5000}}],
    }
    submitted = _submit(broker, params)
    job = _wait_terminal(broker, submitted["id"])
    result = job["result"]
    assert result["status"] == "failed"
    assert "outside" in result["result"]["error"]


def test_sandbox_refuses_unknown_element(broker, backend):
    """An element target not in the accessibility tree is refused."""
    params = {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [{"action": "press", "target": {
            "kind": "accessibility_element", "role": "button",
            "label": "Ghost"}}],
    }
    submitted = _submit(broker, params)
    job = _wait_terminal(broker, submitted["id"])
    result = job["result"]
    assert result["status"] == "failed"
    assert "Ghost" in result["result"]["error"]


def _menu_pair(backend):
    """Two commands under one title, as Resolve's menus really hold them."""
    backend._ax_tree["children"].extend([
        {"ref": "color-bypass", "role": "AXMenuItem", "title": "Color",
         "label": "", "value": "",
         "path": ["View", "Bypass Color and Fusion"],
         "focused": False, "children": []},
        {"ref": "color-switch", "role": "AXMenuItem", "title": "Color",
         "label": "", "value": "",
         "path": ["Workspace", "Switch to Page"],
         "focused": False, "children": []},
    ])


def test_path_qualified_element_presses_the_named_command(broker, backend):
    """A path-qualified target presses exactly the menu item it names."""
    _menu_pair(backend)
    params = {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [{"action": "press", "target": {
            "kind": "accessibility_element", "title": "Color",
            "path": ["Workspace", "Switch to Page"]}}],
    }
    submitted = _submit(broker, params)
    job = _wait_terminal(broker, submitted["id"])
    result = job["result"]
    assert result["status"] == "committed", result
    assert backend.element("color-switch").get("pressed") is True
    assert backend.element("color-bypass").get("pressed") is not True


def test_ambiguous_element_match_is_refused(broker, backend):
    """One title naming several commands refuses; nothing is acted on."""
    _menu_pair(backend)
    params = {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [{"action": "press", "target": {
            "kind": "accessibility_element", "title": "Color"}}],
    }
    submitted = _submit(broker, params)
    job = _wait_terminal(broker, submitted["id"])
    result = job["result"]
    assert result["status"] == "failed"
    assert "elements match" in result["result"]["error"]
    assert backend.element("color-switch").get("pressed") is not True
    assert backend.element("color-bypass").get("pressed") is not True


def test_prepare_refuses_malformed_path(backend):
    for bad in ("Workspace", [], ["Workspace", ""], [""]):
        with pytest.raises(job_kinds.JobRefused):
            job_kinds.prepare("resolve.ui_transaction", {
                "project": "Scratch",
                "actions": [{"action": "press", "target": {
                    "kind": "accessibility_element", "title": "Color",
                    "path": bad}}],
            })


def test_sandbox_refuses_unknown_verification_kind(backend):
    with pytest.raises(job_kinds.JobRefused):
        job_kinds.prepare("resolve.ui_transaction", {
            "project": "Scratch",
            "actions": [{"action": "click", "target": {
                "kind": "viewer_coordinate", "x": 0, "y": 0}}],
            "verification": {"kind": "render_and_diff"},
        })


# ── Receipt written on success and on failure ───────────────────────

def test_receipt_filed_on_success(broker, backend, tmp_path):
    review_dir = str(tmp_path / "review")
    params = {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [{"action": "press", "target": {
            "kind": "accessibility_element", "role": "button",
            "label": "Color"}}],
        "verification": {"kind": "api_readback", "getter": "page",
                         "equals": "edit"},
        "review_dir": review_dir,
    }
    submitted = _submit(broker, params)
    job = _wait_terminal(broker, submitted["id"])
    result = job["result"]
    assert result["status"] == "committed"
    assert result["verification"]["matched"] is True
    assert result["restored"]["page"] == "edit"
    assert result["restored"]["timeline"] == "Reel 07"
    from library.tools import reel_edit_history as history
    entries = history.recorded_history(review_dir, "Reel 07")
    ui_entries = [e for e in entries if e["act"] == ACT_UI_TRANSACTION]
    assert len(ui_entries) == 1
    assert ui_entries[0]["refs"]["provider"] == "resolve.ui"
    assert ui_entries[0]["refs"]["job_id"] == job["id"]
    assert ui_entries[0]["refs"]["result"]["committed"] is True


def test_receipt_filed_on_failure(broker, backend, tmp_path):
    review_dir = str(tmp_path / "review")
    params = {
        "project": "Scratch", "timeline": "Reel 07",
        "actions": [{"action": "press", "target": {
            "kind": "accessibility_element", "role": "button",
            "label": "Nonexistent"}}],
        "review_dir": review_dir,
    }
    submitted = _submit(broker, params)
    job = _wait_terminal(broker, submitted["id"])
    result = job["result"]
    assert result["status"] == "failed"
    from library.tools import reel_edit_history as history
    entries = history.recorded_history(review_dir, "Reel 07")
    ui_entries = [e for e in entries if e["act"] == ACT_UI_TRANSACTION]
    assert len(ui_entries) == 1
    assert ui_entries[0]["refs"]["result"]["error"]


# ── The runner, directly ────────────────────────────────────────────

def test_snapshot_records_unexposed_fields_as_none(backend):
    ctx = ui_transaction.snapshot_context(backend, FakeDriver(backend))
    assert ctx["project"] == "Scratch"
    assert ctx["timeline"] == "Reel 07"
    assert ctx["page"] == "edit"
    assert ctx["playhead"] == "00:00:00:00"
    assert ctx["selected"] is None
    assert ctx["focused_panel"] is None


def test_establish_moves_cursor_and_playhead(backend):
    project = backend.GetProjectManager().GetCurrentProject()
    project.SetCurrentTimeline(project.GetTimelineByIndex(2))
    spec = {"project": "Scratch", "timeline": "Reel 07",
            "target": {"playhead": "00:00:14:12"}}
    ui_transaction._establish(backend, spec)
    assert project.GetCurrentTimeline().GetName() == "Reel 07"
    assert project.GetCurrentTimeline().GetCurrentTimecode() == "00:00:14:12"


def test_establish_refuses_unmet_page_precondition(backend):
    backend.OpenPage("edit")
    spec = {"project": "Scratch", "timeline": "Reel 07",
            "preconditions": {"page": "color"}}
    with pytest.raises(job_kinds.JobRefused):
        ui_transaction._establish(backend, spec)


def test_wait_action_polls_until_text_appears(backend):
    """A wait action blocks until the text appears in the AX tree."""
    import threading
    tree = backend.get_ax_tree()

    def _add_text_later():
        time.sleep(0.1)
        tree["children"].append({
            "ref": "person-label", "role": "text", "title": "Person",
            "label": "Person", "value": "",
            "bounds": {"x": 0, "y": 0, "width": 10, "height": 10},
            "focused": False, "children": []})

    threading.Thread(target=_add_text_later, daemon=True).start()
    action = {"action": "wait", "for": {"text_visible": "Person"},
              "timeout_seconds": 5}
    ui_transaction._execute_action(FakeDriver(backend), action)
    assert "Person" in backend.read_text()


def test_wait_action_times_out(backend):
    action = {"action": "wait", "for": {"text_visible": "NEVER"},
              "timeout_seconds": 0.2}
    with pytest.raises(job_kinds.JobRefused):
        ui_transaction._execute_action(FakeDriver(backend), action)
