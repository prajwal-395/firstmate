"""Named timeline selection is a guarded AXI cursor operation."""

from contextlib import nullcontext
from types import SimpleNamespace

from library.tools import resolve_axi_core_commands as commands


class _Timeline:
    def __init__(self, name, uid):
        self.name = name
        self.uid = uid

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid


def test_live_timeline_refresh_can_select_named_ren_master_and_verify_cursor(
        monkeypatch, capsys):
    before = _Timeline("Reel 13 - staging", "staging-id")
    target = _Timeline("GEO Podcast - Synced", "master-id")

    class Project:
        name = "Ren Scratch - breaktest"

        def GetName(self):
            return self.name

        def SetCurrentTimeline(self, timeline):
            resolve.current = timeline
            return True

    project = Project()
    resolve = SimpleNamespace(project=project, current=before)
    monkeypatch.setattr(commands, "_connect", lambda: resolve)
    monkeypatch.setattr(commands, "_lease", lambda exclusive: nullcontext())
    monkeypatch.setattr(commands, "_project", lambda _resolve, _name: project)
    monkeypatch.setattr(
        commands, "_target_timeline", lambda _project, _name: (target, False, ""))
    monkeypatch.setattr(
        "library.tools.marker_feedback.current_timeline",
        lambda active: (active.current, active.project))

    result = commands.cmd_timeline_current(SimpleNamespace(
        project=project.name, timeline=target.name, apply=True))

    assert result == 0
    assert resolve.current is target
    output = capsys.readouterr().out
    assert "cursor_before: Reel 13 - staging" in output
    assert "cursor_after: GEO Podcast - Synced" in output
    assert "verified: yes" in output


def test_live_timeline_selection_refuses_captain_project_before_resolve(
        monkeypatch, capsys):
    monkeypatch.setattr(
        commands, "_connect",
        lambda: (_ for _ in ()).throw(AssertionError(
            "captain project selection must refuse before connecting")))

    result = commands.cmd_timeline_current(SimpleNamespace(
        project="Podcast (captain project)",
        timeline="GEO Podcast - Synced", apply=True))

    assert result != 0
    assert "Ren-owned project" in capsys.readouterr().out
