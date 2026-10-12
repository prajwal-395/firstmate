"""Build previews are opt-in, through the existing `ren preview` path."""

from argparse import ArgumentParser
from types import SimpleNamespace

import manage_project


def _run_build(tmp_path, monkeypatch, *, configured=False, requested=False):
    project = tmp_path / "project"
    project.mkdir()
    config = "pipeline:\n  preview_after_build: true\n" if configured else ""
    (project / "project.yaml").write_text(
        "name: Test\nslug: test\nproject_format_version: 1\n" + config,
        encoding="utf-8")

    monkeypatch.setattr(
        manage_project, "_reel_project_folder", lambda _project: str(project))
    from library.tools import worker_requirements
    monkeypatch.setattr(worker_requirements, "check_build", list)
    from library.processes.reels import run_reels
    monkeypatch.setattr(run_reels, "run", lambda *_args: 0)
    from library.tools.execution import asset_gc
    monkeypatch.setattr(asset_gc, "report_after", lambda *_args: None)
    from library.tools import reel_preview
    previews = []
    monkeypatch.setattr(
        reel_preview, "main", lambda argv: previews.append(argv) or 0)

    manage_project.cmd_build_reels(SimpleNamespace(
        project=str(project), preview=requested))
    return previews


def test_build_does_not_preview_by_default(tmp_path, monkeypatch):
    assert _run_build(tmp_path, monkeypatch) == []


def test_project_opt_in_previews_after_build(tmp_path, monkeypatch):
    previews = _run_build(tmp_path, monkeypatch, configured=True)
    assert previews == [[str(tmp_path / "project")]]


def test_build_preview_flag_previews_after_build(tmp_path, monkeypatch):
    previews = _run_build(tmp_path, monkeypatch, requested=True)
    assert previews == [[str(tmp_path / "project")]]


def test_build_parser_accepts_preview_flag():
    from library.processes.reels import run_reels

    parser = ArgumentParser()
    run_reels.add_arguments(parser)
    args = parser.parse_args(["project", "--preview"])
    assert args.preview is True


def test_revise_parser_forwards_preview_flag(monkeypatch, capsys):
    from library.tools import revise

    seen = {}
    monkeypatch.setattr(
        revise, "start",
        lambda project, note, **options: seen.update(options) or {
            "status": "awaiting_translation", "llm_request": "request.json"})
    assert revise.main(["project", "note", "--preview"]) == 0
    assert seen["preview"] is True
    assert '"status": "awaiting_translation"' in capsys.readouterr().out
