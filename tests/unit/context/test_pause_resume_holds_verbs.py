"""T4: the hidden `python3 -m` controls are `ren` verbs.

`ren pause` / `ren resume` wrap the handbrake (`run_control`), `ren notes
pull` wraps `marker_feedback pull`, and `ren holds release` wraps
`staging_holds.release_hold`. Every refusal that used to name a file to
delete or a `python3 -c` line names the verb instead.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]


def _ns(**kwargs):
    return type("Args", (), kwargs)()


# ------------------------------------------------------- pause / resume


def test_pause_creates_the_hold_file_and_resume_removes_it(tmp_path, capsys):
    from library.tools import run_control
    from manage_project import cmd_pause, cmd_resume

    project = str(tmp_path / "demo")
    Path(project).mkdir()

    cmd_pause(_ns(project=project, reason="lunch", by="captain"))
    hold = run_control.hold_requested(project)
    assert hold is not None
    assert hold["requested_by"] == "captain"
    assert hold["reason"] == "lunch"
    assert "ren resume" in capsys.readouterr().out

    cmd_resume(_ns(project=project))
    assert run_control.hold_requested(project) is None

    cmd_resume(_ns(project=project))
    assert "nothing to release" in capsys.readouterr().out


def test_a_held_project_refusal_names_ren_resume_not_a_file(tmp_path):
    from library.tools import run_control
    from library.tools.ren_refusal import RenRefusal
    from manage_project import cmd_replay_ledger

    project = str(tmp_path / "demo")
    Path(project).mkdir()
    run_control.request_hold(project, requested_by="captain", reason="lunch")

    with pytest.raises(RenRefusal) as held:
        cmd_replay_ledger(_ns(project=project))
    message = f"{held.value.what}\n{held.value.why}\n{held.value.fix}"
    assert "ren resume" in message
    assert "pipeline.hold" not in message


def test_refusal_sources_name_no_hold_file_and_no_python3_c():
    """The acceptance grep, pinned: no "delete pipeline.hold" in the
    refusal sources, no "python3 -c" in the staging-holds refusals."""
    manage = (REPO / "manage_project.py").read_text(encoding="utf-8")
    assert "delete pipeline.hold" not in manage
    holds = (REPO / "library" / "tools" / "staging_holds.py").read_text(
        encoding="utf-8"
    )
    assert "python3 -c" not in holds
    hook = (REPO / "ren" / "hooks" / "marker_hook.py").read_text(encoding="utf-8")
    assert "ren markers pull" not in hook


# ------------------------------------------------------------- notes pull


class _FakeTimeline:
    def GetName(self):
        return "Reel 1 - test"

    def GetStartFrame(self):
        return 1000

    def GetSetting(self, key):
        return "30" if key == "timelineFrameRate" else None

    def GetMarkers(self):
        return {
            50: {
                "name": "fix the cut",
                "note": "",
                "color": "Red",
                "duration": 1,
                "customData": "",
            },
            90: {
                "name": "",
                "note": "trim the tail",
                "color": "Blue",
                "duration": 1,
                "customData": "",
            },
        }

    def GetTrackCount(self, track_type):
        return 0

    def GetItemListInTrack(self, track_type, index):
        return []


class _FakeProject:
    def GetName(self):
        return "Test Project"


@pytest.fixture()
def broker_double(monkeypatch):
    """Resolve is never touched: the open timeline is a double and the
    instance lease is a no-op context manager."""
    import library.tools.marker_feedback as feedback
    from library.tools import resolve_lock

    timeline, project = _FakeTimeline(), _FakeProject()
    monkeypatch.setattr(
        feedback, "current_timeline", lambda resolve=None: (timeline, project)
    )
    monkeypatch.setattr(
        resolve_lock, "resolve_lease", lambda *args, **kwargs: contextlib.nullcontext()
    )
    return timeline


def _pulled_notes(project_folder: str) -> list:
    from library.tools.marker_feedback import pulled_files

    files = pulled_files(project_folder)
    assert len(files) == 1, f"one pull file, saw {files}"
    _, payload = files[0]
    assert payload["note_count"] == 2
    return payload["notes"]


def test_notes_pull_returns_the_same_records_as_the_module_cli(
    tmp_path, broker_double, capsys
):
    import library.tools.marker_feedback as feedback
    from manage_project import cmd_notes

    verb_project = str(tmp_path / "via-verb")
    module_project = str(tmp_path / "via-module")
    Path(verb_project).mkdir()
    Path(module_project).mkdir()

    with pytest.raises(SystemExit) as verb_exit:
        cmd_notes(_ns(action="pull", slug=verb_project, all=False, write=False))
    assert verb_exit.value.code == 0
    verb_notes = _pulled_notes(verb_project)

    assert feedback.main(["pull", "--project", module_project]) == 0
    module_notes = _pulled_notes(module_project)

    for left, right in zip(verb_notes, module_notes):
        assert (left["text"], left["frame"]) == (right["text"], right["frame"])
    assert [note["text"] for note in verb_notes] == ["fix the cut", "trim the tail"]
    assert "2 note(s)" in capsys.readouterr().out


def test_notes_without_pull_still_routes(tmp_path, capsys):
    """`ren notes <project>` keeps its old shape: one positional is the
    project, not an action."""
    from manage_project import cmd_notes

    project = str(tmp_path / "demo")
    Path(project).mkdir()
    with pytest.raises(SystemExit) as done:
        cmd_notes(_ns(action=project, slug="", all=False, write=False))
    assert done.value.code == 0


# ------------------------------------------------------- holds release


def test_holds_release_drops_the_hold(tmp_path, capsys):
    from library.tools import staging_holds
    from manage_project import cmd_holds

    project = str(tmp_path / "demo")
    staging_holds.take_hold(
        project,
        "Reel 3 (staging)",
        awaiting="Reel 3 - final",
        reason="verify",
        taken_by="test",
    )

    cmd_holds(_ns(action="release", project=project, timeline="Reel 3 (staging)"))
    assert staging_holds.read_holds(project) == {}
    assert "Released" in capsys.readouterr().out

    cmd_holds(_ns(action="release", project=project, timeline="Reel 3 (staging)"))
    assert "nothing released" in capsys.readouterr().out


def test_holds_refusal_names_the_verb_not_python3():
    from library.tools import staging_holds

    entry = {
        "awaiting": "Reel 3 - final",
        "taken_at": "2026-10-11T00:00:00",
        "reason": "verify",
        "taken_by": "test",
    }
    message = staging_holds.refusal_message("Reel 3 (staging)", entry)
    assert "ren holds release" in message
    assert "python3" not in message


# ------------------------------------------------------- registration


def test_new_verbs_are_registered_in_both_front_doors():
    from ren.commands import VERBS

    by_name = {verb.name: verb for verb in VERBS}
    assert by_name["pause"].subcommand == "pause"
    assert by_name["resume"].subcommand == "resume"
    assert by_name["holds"].subcommand == "holds"
    assert by_name["notes"].subcommand == "notes"
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8"
    )
    for name in ("pause", "resume", "holds", "notes"):
        assert f'name: "{name}"' in native
