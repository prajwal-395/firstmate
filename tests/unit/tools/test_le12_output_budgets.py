"""LE-12 · Output budgets for the chatty defaults.

Each budgeted default is pinned against the renderer it replaces, on a
fixture built under `tmp_path`:

* `ren outcomes` defaults to a summary (counts per action and per
  verification state, plus the 5 newest notes) of at most 2,000 bytes;
  `--full` reproduces today's `render_text` byte-for-byte.
* `ren edit --dry-run` prints the plan once: JSON on stdout when stdout
  is not a TTY or `--json` is passed, human text otherwise; the
  non-TTY combined output is at most 3,000 bytes.
* `ren doctor` defaults to FAIL/WARN rows plus one summary line of at
  most 1,500 bytes on a healthy fixture; `--all` reproduces `render`.
* The still-overlays and dedupe-stills dry-runs accept `--json`.

Hermetic: absolute `tmp_path` projects only, and every host/agent
environment marker the code under test reads is cleared.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import feedback_outcomes as outcomes  # noqa: E402
from library.tools import marker_feedback  # noqa: E402
from library.tools.project_layout import Area, ProjectLayout  # noqa: E402
from ren import doctor  # noqa: E402


def _write_pull(project, filename, timeline, pulled_at, notes):
    layout = ProjectLayout(project)
    path = layout.write_path(Area.MARKER_FEEDBACK, filename)
    path.write_text(json.dumps({
        "format": marker_feedback.PULL_FORMAT,
        "pulled_at": pulled_at,
        "resolve_project": "Ren Qualification",
        "timeline": timeline,
        "timeline_start_frame": 0,
        "timeline_fps": 30.0,
        "note_count": len(notes),
        "notes": notes,
    }, indent=2) + "\n", encoding="utf-8")
    return path


def _big_note(index: int) -> dict:
    body = ("Keep the lower third clear of the speaker's hand; "
            "the caption overlaps the gesture on the wide shot. ") * 4
    return {
        "source": "timeline_marker", "name": "feedback",
        "note": f"note {index}: {body}",
        "text": f"feedback\n\nnote {index}: {body}",
        "frame": 1000 + index, "frame_in_timeline_space": 1000 + index,
        "timecode": f"00:00:{index:02d}:00",
        "custom_data": {}, "custom_data_raw": "", "note_id": "",
    }


def _make_outcomes_project(tmp_path: Path, count: int = 12) -> Path:
    project = tmp_path / "le12-outcomes"
    project.mkdir()
    for index in range(count):
        reel = (index % 4) + 1
        timeline = f"Reel {reel:02d}"
        _write_pull(
            project,
            f"Reel_{reel:02d}.2026-10-{index + 1:02d}T12-00-00Z.markers.json",
            timeline, f"2026-10-{index + 1:02d}T12:00:00+00:00",
            [_big_note(index)])
        note = {
            "note_id": f"Reel_{reel:02d}:timeline_marker:{1000 + index}",
            "stable_note_id": "",
            "source": "timeline_marker",
            "name": "feedback",
            "note": _big_note(index)["note"],
            "text": _big_note(index)["text"],
            "frame": 1000 + index,
            "frame_in_timeline_space": 1000 + index,
            "timecode": f"00:00:{index:02d}:00",
            "timeline": timeline,
            "pull_file": "",
            "collected_at": f"2026-10-{index + 1:02d}T12:00:00+00:00",
        }
        kind = ["rebuild", "patch", "declined", "blocked"][index % 4]
        if kind == "declined":
            from library.tools import marker_resolution
            marker_resolution.record_resolution(
                str(project), note,
                marker_resolution.STATUS_DECLINED,
                action="decline: keep the original framing " * 3,
                rationale="The hand crosses the lower third only briefly. " * 3,
            )
        else:
            outcomes.record_plan(
                str(project), note, action_kind=kind,
                summary=("Move the lower third above the speaker's hand; "
                         "extend the hold through the gesture. " * 3),
                outcome_id=f"planned-{index}")
    return project


def test_outcomes_default_is_a_summary_and_full_restores_the_ledger(
        tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    monkeypatch.delenv("REN_ENGINE_ROOT", raising=False)
    project = _make_outcomes_project(tmp_path)

    assert outcomes.main([str(project)]) == 0
    summary = capsys.readouterr().out
    assert len(summary.encode("utf-8")) <= 2000

    document = outcomes.build(str(project))
    full = outcomes.render_text(document)
    assert "actions:" in summary and "verification:" in summary
    assert "newest 5 note(s):" in summary
    assert len(summary.splitlines()) <= len(full.splitlines())

    assert outcomes.main([str(project), "--full"]) == 0
    assert capsys.readouterr().out == full + "\n"

    assert outcomes.main([str(project), "--json"]) == 0
    parsed = json.loads(capsys.readouterr().out)
    assert len(parsed["notes"]) == len(document["notes"])


def _bare_project(tmp_path: Path) -> Path:
    project = tmp_path / "le12-dry-run"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: LE12 Test\nslug: le12-test\n", encoding="utf-8")
    (project / "pipeline_data.json").write_text(
        json.dumps({"project_folder": str(project),
                    "preflight_completed": {}, "edit_completed": {},
                    "step_outputs": {}}), encoding="utf-8")
    return project


def test_edit_dry_run_prints_json_once_when_stdout_is_not_a_tty(
        tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    monkeypatch.delenv("PIPELINE_HOST_HARNESS", raising=False)
    project = _bare_project(tmp_path)

    from library.processes.edit_video import run_pipeline as runner
    assert runner.build_parser().parse_args(["--project", "x", "--json"]).json is True

    summary = runner.run_pipeline(str(project), dry_run=True)
    captured = capsys.readouterr()
    assert summary["status"] == "DRY_RUN"
    parsed = json.loads(captured.out)
    assert parsed["status"] == "DRY_RUN"
    assert parsed["steps_to_run"] == summary["steps_to_run"]
    # The human per-step plan stays quiet in JSON mode.
    assert "validate_sfx_library (" not in captured.err
    combined = (captured.out + captured.err).encode("utf-8")
    assert len(combined) <= 3000


def test_edit_dry_run_prints_human_text_on_a_tty(
        tmp_path, monkeypatch):
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    monkeypatch.delenv("PIPELINE_HOST_HARNESS", raising=False)
    project = _bare_project(tmp_path)

    from library.processes.edit_video import run_pipeline as runner

    class _TTYOut(io.StringIO):
        def isatty(self):
            return True

    fake_out, fake_err = _TTYOut(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", fake_err)
    summary = runner.run_pipeline(str(project), dry_run=True)
    assert summary["status"] == "DRY_RUN"
    assert fake_out.getvalue() == ""
    assert "validate_sfx_library" in fake_err.getvalue()

    # --json wins over the TTY.
    fake_out, fake_err = _TTYOut(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake_out)
    monkeypatch.setattr(sys, "stderr", fake_err)
    runner.run_pipeline(str(project), dry_run=True, dry_run_json=True)
    assert json.loads(fake_out.getvalue())["status"] == "DRY_RUN"


def _healthy_checks():
    checks = [doctor.Check(f"check-{index:02d}", True,
                           "stubbed detail for a passing check")
              for index in range(30)]
    return checks


def test_doctor_default_is_brief_and_all_restores_todays_output(
        monkeypatch, capsys):
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    monkeypatch.delenv("REN_ENGINE_ROOT", raising=False)
    monkeypatch.setattr(
        doctor, "run_checks", lambda probe=None, needs=None: _healthy_checks())

    assert doctor.main([]) == 0
    brief = capsys.readouterr().out
    assert len(brief.encode("utf-8")) <= 1500
    assert "PASS" not in brief
    assert "passed," in brief

    assert doctor.main(["--all"]) == 0
    full = capsys.readouterr().out
    assert full == doctor.render(_healthy_checks()) + "\n"
    assert "PASS" in full
    assert len(full.encode("utf-8")) > len(brief.encode("utf-8"))


def test_doctor_brief_keeps_fail_warn_rows(monkeypatch, capsys):
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    rows = _healthy_checks() + [
        doctor.Check("broken", False, "a baseline need is missing",
                     fix="install it", need="python.core"),
        doctor.Check("unsure", False, "the probe timed out",
                     fix="retry", need="resolve.scripting",
                     undetermined=True),
    ]
    monkeypatch.setattr(
        doctor, "run_checks", lambda probe=None, needs=None: rows)

    assert doctor.main([]) == 1
    out = capsys.readouterr().out
    assert "FAIL  broken" in out
    assert "UNKNOWN  unsure" in out
    assert "PASS" not in out


def test_still_dry_runs_run_outcomes_and_doctor_advertise_the_new_flags(
        capsys, monkeypatch):
    """The new flags parse: `--json` on both still dry-runs and `run`,
    `--full` on outcomes, `--all` on doctor."""
    import manage_project

    monkeypatch.delenv("REN_OUTPUT", raising=False)
    monkeypatch.delenv("REN_ENGINE_ROOT", raising=False)
    for argv in (["manage_project.py", "still-overlays", "--help"],
                 ["manage_project.py", "dedupe-stills", "--help"],
                 ["manage_project.py", "run", "--help"]):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit) as exited:
            manage_project.main()
        assert exited.value.code == 0
        assert "--json" in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["ren outcomes", "--help"])
    with pytest.raises(SystemExit) as exited:
        outcomes.main(["--help"])
    assert exited.value.code == 0
    assert "--full" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exited:
        doctor.main(["--help"])
    assert exited.value.code == 0
    assert "--all" in capsys.readouterr().out
