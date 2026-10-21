"""T16: `ren deliver <project>` delivers every approved reel, once.

On a fixture with 2 approved (signed-off) reels and 1 unapproved, the
batch produces exactly 2 files plus 1 receipt naming both, their QC
verdicts and the resume point. A second invocation with nothing
changed re-renders nothing. The render and the QC are doubles; the
project lives under `tmp_path`.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

REELS = {1: "Reel 01 - pricing", 2: "Reel 02 - hook"}


def _proposal_moments():
    from library.tools.reel_proposal import Approval, ReelMoment

    return [
        ReelMoment(number=1, slug="pricing", reason="the price lands",
                   timeline_start=10.0, timeline_end=40.0,
                   approval=Approval.APPROVED),
        ReelMoment(number=2, slug="hook", reason="the hook holds",
                   timeline_start=50.0, timeline_end=80.0,
                   approval=Approval.APPROVED),
        ReelMoment(number=3, slug="maybe", reason="maybe later",
                   timeline_start=90.0, timeline_end=100.0,
                   approval=Approval.PROPOSED),
    ]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "pricing-show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Pricing Show"\nslug: pricing-show\nproject_format_version: 1\n',
        encoding="utf-8")
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    monkeypatch.setenv("REN_DELIVER_REVEAL", "0")

    from library.tools.reel_proposal import proposal_path, write_proposal

    write_proposal(proposal_path(str(root)), _proposal_moments(), {})

    import manage_project
    from library.tools import assignment_spine

    assignment_spine.on_propose(str(root))
    assignment_spine.on_approve(str(root))
    # The spine's own build act, without Resolve: approved -> built.
    act = assignment_spine.begin_build(str(root))
    assignment_spine.end_act(act, ok=True)
    manage_project.cmd_sign_off(argparse.Namespace(
        slug=str(root), reel=REELS[1], note="ship it", by="captain",
        withdraw=False))
    manage_project.cmd_sign_off(argparse.Namespace(
        slug=str(root), reel=REELS[2], note="ship it", by="captain",
        withdraw=False))
    return root


class _RenderDouble:
    """A counting stand-in for `reel_deliver.deliver_reel`."""

    def __init__(self, project: Path):
        self.project = project
        self.calls: list[int] = []

    def __call__(self, folder, reel, **kwargs):
        self.calls.append(int(reel))
        timeline = REELS[int(reel)]
        output = self.project / "exports" / f"{timeline}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fixture-render")
        check = {"passed": True, "value": 30.0, "detail": "fixture"}
        return {
            "timeline_name": timeline, "reel": int(reel),
            "settings": {"needs_captain_word": []},
            "render": {"output_path": str(output)},
            "verification": {
                "output_path": str(output), "duration": dict(check),
                "resolution": dict(check), "audio": dict(check),
                "content_present": True, "opens_on_black": False,
                "passed": True},
            "delivered": True,
            "report_path": str(output) + ".deliver.json"}


class _QcDouble:
    """A stand-in for `deliver_all.run_qc`."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, path, spec, expected_seconds,
                 *, declared_silence_spans=None):
        self.calls.append(path)
        return {"platform": spec.platform, "passed": True,
                "duration": {"passed": True}, "black_frames": {"passed": True},
                "loudness": {"passed": True}, "grade": {"passed": True},
                "captions": None}


def _receipts(project: Path) -> list[dict]:
    directory = project / "exports" / "assignments"
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*-delivery.json"))] \
        if directory.is_dir() else []


def test_deliver_all_produces_two_files_and_one_receipt(project, monkeypatch):
    from library.tools import deliver_all

    render, qc = _RenderDouble(project), _QcDouble()
    report = deliver_all.deliver_all_approved(
        str(project), deliver_one=render, qc_one=qc)

    assert sorted(render.calls) == [1, 2]
    assert len(report["delivered"]) == 2
    assert report["up_to_date"] == []
    assert report["failed"] == []
    assert [(row["reel"]) for row in report["delivered"]] == [1, 2]
    assert len(qc.calls) == 2

    files = sorted((project / "exports").glob("Reel *.mp4"))
    assert len(files) == 2

    [unapproved] = report["unapproved"]
    assert unapproved["number"] == 3

    [entry] = _receipts(project)
    names = sorted(row["name"] for row in entry["files"])
    assert names == ["Reel 01 - pricing.mp4", "Reel 02 - hook.mp4"]
    assert all((row.get("qc") or {}).get("passed") for row in entry["files"])
    # Reel 3 is still proposed, so the resume point truthfully names it.
    assert entry["resume_point"]["action"] == "approve"
    assert "Reel 03 - maybe" in entry["resume_point"]["why"]
    assert entry["status"] == "complete"
    assert entry["selected_platforms"] == report["platforms"]

    [receipt] = report["receipts"]
    assert receipt["path"] == str(project / "exports" / "assignments"
                                  / Path(receipt["path"]).name)


def test_second_invocation_with_nothing_changed_renders_nothing(
        project, monkeypatch):
    from library.tools import deliver_all

    render, qc = _RenderDouble(project), _QcDouble()
    first = deliver_all.deliver_all_approved(
        str(project), deliver_one=render, qc_one=qc)
    assert len(first["delivered"]) == 2

    second = deliver_all.deliver_all_approved(
        str(project), deliver_one=render, qc_one=qc)
    assert render.calls == [1, 2]
    assert second["delivered"] == []
    assert sorted(row["reel"] for row in second["up_to_date"]) == [1, 2]
    assert len(qc.calls) == 2
    assert len(_receipts(project)) == 1


def test_batch_stays_explicit_like_the_single_verb():
    """No DAG node, no operation and no runner reaches `deliver_all`."""
    import inspect

    import manage_project
    from library.tools import operations, processes
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[2]
    assert processes.execution_order(processes.REELS) == [
        "build_reels", "verify_reels"]
    assert not [name for name in operations.names() if "deliver" in name]
    for func in (manage_project.cmd_build_reels, manage_project.cmd_run,
                 manage_project.cmd_propose_reels):
        source = inspect.getsource(func)
        assert "deliver_all" not in source, func.__name__
        assert "render_timeline" not in source, func.__name__
    for runner in (repo / "library" / "processes" / "edit_video"
                   / "run_pipeline.py",
                   repo / "library" / "processes" / "reels"
                   / "run_reels.py"):
        source = runner.read_text(encoding="utf-8")
        assert "deliver_all" not in source, runner.name


def test_cli_deliver_without_a_reel_delivers_all_approved(
        project, monkeypatch, capsys):
    import manage_project
    from library.tools import deliver_all, reel_deliver

    render, qc = _RenderDouble(project), _QcDouble()
    monkeypatch.setattr(reel_deliver, "deliver_reel", render)
    monkeypatch.setattr(deliver_all, "run_qc", qc)
    manage_project.cmd_deliver_reel(argparse.Namespace(
        project=str(project), reel=None, output_dir="", name="",
        timeout=1, platform=[], preset="", naming="",
        no_prompt=True, no_reveal=True))
    assert sorted(render.calls) == [1, 2]
    out = capsys.readouterr().out
    assert "Reel 01 - pricing" in out
    assert "receipt:" in out


def test_cli_deliver_without_a_reel_and_a_name_is_refused(
        project, monkeypatch):
    import manage_project
    from library.tools import reel_deliver

    with pytest.raises(reel_deliver.DeliverRefused, match="--name"):
        manage_project.cmd_deliver_reel(argparse.Namespace(
            project=str(project), reel=None, output_dir="", name="x.mp4",
            timeout=1, platform=[], preset="", naming="",
            no_prompt=True, no_reveal=True))
