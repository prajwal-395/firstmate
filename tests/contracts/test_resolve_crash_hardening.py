"""Resolve crash hardening (2026-10-06 EXC_GUARD scripting-thread shutdown).

Three lanes each held their own fusionscript scripting connection beside
the broker's (intellisearch direct runner, power-windows ad-hoc /tmp
script, computer-use borrow cycles with ~6 LoadProjects in ~10 min), plus
144 leaked test brokers; the faulting thread died in
`Fusion::Platform::WaitAllThreads` while Resolve was loading/switching a
project, and computer-use saw `GetProjectManager` -> None seconds before.

Each test here names its defect: a failure says which crash half
regressed, not just which assertion tripped. All prove offline against
fakes - never the captain's projects, never a live Resolve.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── (1) one gateway: lane scripting is broker-only ────────────────────

def _fake_dvr(result):
    class _Dvr:
        def scriptapp(self, name="Resolve"):
            return result
    return _Dvr()


def test_lane_direct_scripting_refuses_outside_the_broker_worker(
        monkeypatch):
    """Defect: a lane opened its own fusionscript client beside the
    broker's and Resolve died in scripting-thread shutdown."""
    from library.tools import resolve_locale
    monkeypatch.setenv(resolve_locale.LANE_ENV, "vep-test-lane")
    dvr = _fake_dvr(object())
    with pytest.raises(RuntimeError, match="broker-only"):
        resolve_locale.scriptapp_preserving_locale(dvr, "Resolve")


def test_lane_direct_scripting_names_the_broker_route(monkeypatch):
    """The refusal is actionable: it says what to do instead."""
    from library.tools import resolve_locale
    monkeypatch.setenv(resolve_locale.LANE_ENV, "vep-test-lane")
    with pytest.raises(RuntimeError) as exc:
        resolve_locale.scriptapp_preserving_locale(
            _fake_dvr(object()), "Resolve")
    message = str(exc.value)
    assert "broker job" in message
    assert "resolve-axi run" in message
    assert "ren resolved submit" in message


def test_broker_worker_keeps_its_connection(monkeypatch):
    """The gateway refuses lanes, not the broker worker itself."""
    from library.tools import resolve_locale
    from library.tools.resolved import client
    monkeypatch.setenv(resolve_locale.LANE_ENV, "vep-test-lane")
    client.mark_executor()
    try:
        sentinel = object()
        assert resolve_locale.scriptapp_preserving_locale(
            _fake_dvr(sentinel), "Resolve") is sentinel
    finally:
        client._executor.active = False


def test_operator_process_without_a_lane_keeps_todays_behaviour():
    """No lane signal, no new refusal: operators and tests connect."""
    import os

    from library.tools import resolve_locale
    assert not (os.environ.get(resolve_locale.LANE_ENV) or "").strip()
    sentinel = object()
    assert resolve_locale.scriptapp_preserving_locale(
        _fake_dvr(sentinel), "Resolve") is sentinel


def test_the_gateway_guard_is_wired_into_the_handshake():
    """The guard lives in the connect helper, not in a docstring: the
    handshake calls it, and it keys on the lane signal and the broker
    worker mark."""
    from library.tools import resolve_locale
    source = Path(resolve_locale.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert isinstance(resolve_locale.direct_connect_refused(), str | type(None))
    assert "direct_connect_refused()" in source
    handshake = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "scriptapp_preserving_locale")
    calls = {getattr(call.func, "attr", getattr(call.func, "id", ""))
             for call in ast.walk(handshake)
             if isinstance(call, ast.Call)}
    assert "direct_connect_refused" in calls
    assert "LANE_ENV" in source and "in_broker" in source


def test_lane_helpers_open_no_scripting_client_of_their_own():
    """The crash lanes connected directly: intellisearch submits a
    broker job, computer-use goes through the guarded connect helper,
    and no lane helper imports the bindings (a direct import bypasses
    the gateway entirely - the /tmp/pw_take.py shape)."""
    offenders = []
    lane_files = list((REPO_ROOT / "library" / "tools" / "computer_use"
                       ).rglob("*.py"))
    lane_files.append(
        REPO_ROOT / "library" / "tools" / "intellisearch_collect.py")
    for path in lane_files:
        if "__pycache__" in str(path):
            continue
        source = path.read_text(encoding="utf-8")
        if "DaVinciResolveScript" in source or ".scriptapp(" in source:
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert not offenders, offenders


# ── (2) project-switch safety: live-check, settle, serialize ───────────

class _FakeProject:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name


class _FakeManager:
    def __init__(self, projects, open_name, dead_after=0):
        self.projects = dict(projects)
        self.open_name = open_name
        self.dead_after = dead_after
        self.loads = []

    def LoadProject(self, name):
        self.loads.append(name)
        self.open_name = name
        return self.projects.get(name)

    def GetCurrentProject(self):
        if self.dead_after and len(self.loads) >= self.dead_after:
            return None
        name = self.open_name
        return self.projects.get(name) if name else None


def test_switch_refuses_a_dead_manager_before_loading():
    """Defect: a LoadProject issued into a dead handle mid-switch."""
    from library.tools import qualification_project as qp
    with pytest.raises(qp.QualificationError, match="mid-switch"):
        qp.switch_project(None, "Ren Qualification", settle=0)


def test_switch_settles_and_stamps_the_guard(monkeypatch):
    """Defect: back-to-back LoadProjects with no quiet window; the
    broker dropped/reconnected mid-switch into the crash site."""
    from library.tools import qualification_project as qp
    from library.tools import resolve_switch_guard as guard
    guard._reset_for_tests()
    monkeypatch.setattr(guard, "SETTLE_SECONDS", 0)
    qual = _FakeProject("Ren Qualification")
    manager = _FakeManager({"Ren Qualification": qual}, "Captain")
    assert qp.switch_project(manager, "Ren Qualification", settle=0) is qual
    assert manager.loads == ["Ren Qualification"]
    assert guard.in_switch_window(settle=60)


def test_switch_refuses_a_handle_that_dies_in_the_window():
    """Defect: computer-use saw GetProjectManager -> None seconds
    before the crash and proceeded anyway."""
    from library.tools import qualification_project as qp
    qual = _FakeProject("Ren Qualification")
    manager = _FakeManager({"Ren Qualification": qual}, "Captain",
                           dead_after=1)
    with pytest.raises(qp.QualificationError, match="reads dead"):
        qp.switch_project(manager, "Ren Qualification", settle=0)


def test_switch_refuses_a_name_that_does_not_read_back():
    """A switch that does not read back is a switch that did not
    happen: refuse, naming what to reopen by hand."""
    from library.tools import qualification_project as qp
    manager = _FakeManager({}, "Captain")
    with pytest.raises(qp.QualificationError, match="by hand"):
        qp.switch_project(manager, "Ren Qualification", settle=0)


def test_restore_refuses_a_dead_manager_instead_of_switching():
    """The restore half of borrowed(): a None manager raises
    RestoreFailed with the hand-reopen note, never a bare
    AttributeError mid-switch."""
    from library.tools import qualification_project as qp

    class _DeadResolve:
        def GetProjectManager(self):
            return None

    with pytest.raises(qp.RestoreFailed, match="by hand"):
        qp.restore(_DeadResolve(), {"project": "Captain",
                                    "timeline": "Reel 07",
                                    "timeline_id": "uid-1"})


def test_every_loadproject_goes_through_the_switch_helper():
    """One LoadProject call site in production: the helper that
    live-checks, settles and stamps the guard. A new direct caller is
    a second switch discipline, which is the defect."""
    offenders = []
    for path in (REPO_ROOT / "library").rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if ".LoadProject(" not in source:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative == "library/tools/qualification_project.py":
            continue
        offenders.append(relative)
    assert not offenders, (
        "LoadProject outside qualification_project.switch_project: "
        + ", ".join(sorted(offenders)))


# ── (3) clean disconnect: never drop mid-switch ───────────────────────

def test_switch_guard_quiet_window_semantics(monkeypatch):
    """The window a connection drop must respect: stamped now means
    no drop; with the settle expired the drop is free again."""
    from library.tools import resolve_switch_guard as guard
    guard._reset_for_tests()
    assert guard.seconds_since_switch() == float("inf")
    assert guard.connection_may_drop()
    guard.note_project_switch()
    assert guard.in_switch_window(settle=60)
    assert not guard.connection_may_drop()
    monkeypatch.setattr(guard, "SETTLE_SECONDS", 0)
    assert guard.connection_may_drop()


def test_broker_and_worker_keep_connections_through_the_window():
    """The crash site is dropping a scripting connection while Resolve
    loads: both connection owners consult the guard before dropping."""
    for name in ("library/tools/resolved/server.py",
                 "library/tools/resolved/worker.py"):
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "connection_may_drop" in source, name
        assert "wait_for_settle" in source, name


# ── follow-up: the one-off path is a broker job, not a direct client ──
#
# Ad-hoc lane scripts (`/tmp/pw_take.py`, the intellisearch direct
# runner) imported DaVinciResolveScript themselves, bypassing the
# gateway entirely. The documented one-off is `resolve-axi run`
# (locally under the lease, or submitted as a broker `resolve_axi`
# job onto the broker's own connection); `--borrow` adds the
# save/restore half and `open_project` the settled switch half.


def test_run_scope_offers_the_settled_switch():
    """`open_project` is in RUN_SCOPE and in the scope `run` builds:
    a script that switches projects has the settled path, so a raw
    `manager.LoadProject` in a script is never the only way."""
    from library.tools import resolve_axi
    assert "open_project" in resolve_axi.RUN_SCOPE
    source = Path(resolve_axi.__file__).read_text(encoding="utf-8")
    assert '"open_project": (refuse_project_switch if recorded else' in source
    assert 'lambda name: _run_open_project' in source
    assert "open_project(name)" in source


def test_run_borrow_needs_the_exclusive_lease(monkeypatch, capsys):
    """Defect: a borrowed one-off outside the exclusive lease would
    save/restore against writers it cannot exclude. `--borrow` without
    `--unsafe` refuses before reading any file or touching Resolve."""
    from library.tools import resolve_axi
    monkeypatch.setenv(resolve_axi.RUN_OPT_IN_ENV, "1")
    args = resolve_axi.build_parser().parse_args(
        ["run", "--borrow", "--file", "/nonexistent/one-off.py"])
    assert resolve_axi.cmd_run(args) == 1
    out = capsys.readouterr().out
    assert "--unsafe" in out
    assert "cannot read script file" not in out


class _BorrowTimeline:
    def __init__(self, name, uid):
        self._name, self._uid = name, uid

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return self._uid


class _BorrowProject:
    def __init__(self, name, timelines):
        self._name = name
        self._timelines = timelines
        self._current = timelines[0] if timelines else None

    def GetName(self):
        return self._name

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        return self._timelines[index - 1]

    def GetCurrentTimeline(self):
        return self._current

    def SetCurrentTimeline(self, timeline):
        self._current = timeline
        return True


class _BorrowManager:
    def __init__(self, captain, qual):
        self._projects = {captain.GetName(): captain,
                          qual.GetName(): qual}
        self._open = captain
        self._folder_path = ()

    def LoadProject(self, name):
        self._open = self._projects.get(name)
        return self._open

    def GetCurrentProject(self):
        return self._open

    def SaveProject(self):
        return True

    def GetCurrentFolder(self):
        return self._folder_path[-1] if self._folder_path else ""

    def GotoParentFolder(self):
        if not self._folder_path:
            return False
        self._folder_path = self._folder_path[:-1]
        return True

    def GotoRootFolder(self):
        self._folder_path = ()
        return True

    def GetFoldersInCurrentFolder(self):
        return {}

    def OpenFolder(self, name):
        return False


class _BorrowResolve:
    def __init__(self, manager):
        self._manager = manager
        self._page = "edit"

    def GetProjectManager(self):
        return self._manager

    def GetCurrentPage(self):
        return self._page

    def OpenPage(self, page):
        self._page = page
        return True


def test_run_borrow_saves_switches_and_restores(monkeypatch, capsys):
    """The /tmp/pw_take.py shape, legitimized: under `--borrow --unsafe`
    the script switches with `open_project` and the captain's project
    and timeline read back afterwards - all offline against fakes."""
    import contextlib

    from library.tools import resolve_axi
    from library.tools import resolve_switch_guard as guard
    monkeypatch.setenv(resolve_axi.RUN_OPT_IN_ENV, "1")
    monkeypatch.setattr(guard, "SETTLE_SECONDS", 0)
    captain = _BorrowProject(
        "Podcast (field test)",
        [_BorrowTimeline("Reel 07", "uid-captain")])
    qual = _BorrowProject(
        "Ren Qualification", [_BorrowTimeline("Q Spine", "uid-q")])
    resolve = _BorrowResolve(_BorrowManager(captain, qual))
    monkeypatch.setattr(resolve_axi, "_connect", lambda: resolve)
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    args = resolve_axi.build_parser().parse_args(
        ["run", "--borrow", "--unsafe", "--script",
         ("proj = open_project('Ren Qualification')\n"
          "result = {'opened': proj.GetName()}")])
    assert resolve_axi.cmd_run(args) == 0
    out = capsys.readouterr().out
    assert "Ren Qualification" in out
    assert resolve._manager.GetCurrentProject() is captain
    assert (captain.GetCurrentTimeline().GetUniqueId()
            == "uid-captain")


def test_run_borrow_restores_even_when_the_script_raises(
        monkeypatch, capsys):
    """A failing one-off must not keep the borrowed Resolve: the
    restore runs on the way out, also when the script raised."""
    import contextlib

    from library.tools import resolve_axi
    from library.tools import resolve_switch_guard as guard
    monkeypatch.setenv(resolve_axi.RUN_OPT_IN_ENV, "1")
    monkeypatch.setattr(guard, "SETTLE_SECONDS", 0)
    captain = _BorrowProject(
        "Podcast (field test)",
        [_BorrowTimeline("Reel 07", "uid-captain")])
    qual = _BorrowProject(
        "Ren Qualification", [_BorrowTimeline("Q Spine", "uid-q")])
    resolve = _BorrowResolve(_BorrowManager(captain, qual))
    monkeypatch.setattr(resolve_axi, "_connect", lambda: resolve)
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: contextlib.nullcontext())
    args = resolve_axi.build_parser().parse_args(
        ["run", "--borrow", "--unsafe", "--script",
         "open_project('Ren Qualification')\nraise ValueError('boom')"])
    assert resolve_axi.cmd_run(args) == 1
    assert "boom" in capsys.readouterr().out
    assert resolve._manager.GetCurrentProject() is captain


def test_run_script_job_submits_through_the_broker(monkeypatch):
    """The documented lane path prepares as a broker `resolve_axi`
    job: a purpose-recorded run is admitted without developer opt-in,
    while an unrecorded run remains behind the developer-shell gate."""
    from library.tools import resolve_axi
    from library.tools.resolved import jobs
    argv = ["run", "--unsafe", "--borrow", "--file", "one-off.py"]
    monkeypatch.delenv(resolve_axi.RUN_OPT_IN_ENV, raising=False)
    with pytest.raises(jobs.JobRefused, match="opt-in"):
        jobs.prepare("resolve_axi", {"argv": argv})
    recorded = jobs.prepare("resolve_axi", {"argv": [
        "run", "--purpose", "inspect a named timeline", "--script",
        "result = timeline.GetName()",
    ]})
    assert recorded["mode"] == "exclusive"
    assert recorded["executed"]
    monkeypatch.setenv(resolve_axi.RUN_OPT_IN_ENV, "1")
    shape = jobs.prepare("resolve_axi", {"argv": argv})
    assert shape["mode"] == "exclusive"
    assert shape["executed"]
