"""Differing platform specs render variants instead of missing rows.

On a fixture with 2 approved (signed-off) reels delivered for
`tiktok` + `master`, the batch renders 2 masters and reframes 2
`master` variants off them - no named-missing receipt row. The render,
the reframe and the QC are doubles; the project lives under `tmp_path`.
"""

from __future__ import annotations

import argparse
import json
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


class _ReframeDouble:
    """A counting stand-in for `deliver_variants.render_variant`."""

    def __init__(self, fail_on: tuple = ()):
        self.calls: list[dict] = []
        self.fail_on = set(fail_on)

    def __call__(self, *, master_path, spec, output_path,
                 face_centers=None, sample_rate_hz=5.0, work_dir=""):
        reel = ("Reel 01" if "Reel 01 - pricing.mp4" in master_path
                else "Reel 02" if "Reel 02 - hook.mp4" in master_path
                else "?")
        row = {"reel": reel, "master_path": master_path,
               "platform": spec.platform,
               "width": spec.width, "height": spec.height,
               "output_path": output_path, "face_centers": face_centers}
        self.calls.append(row)
        if reel in self.fail_on:
            raise RuntimeError("boom: the reframe failed")
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"fixture-variant")
        return {"output_path": output_path, "width": spec.width,
                "height": spec.height, "fps": spec.fps,
                "fps_source": "double", "crop": "centred",
                "crop_reason": "double", "crop_windows": 1,
                "audio": "double", "master": {}, "sendcmd_path": ""}


class _QcDouble:
    """A stand-in for `deliver_all.run_qc`, recording each spec judged."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, path, spec, expected_seconds):
        self.calls.append({"path": path, "platform": spec.platform})
        return {"platform": spec.platform, "passed": True,
                "duration": {"passed": True}, "black_frames": {"passed": True},
                "loudness": {"passed": True}, "grade": {"passed": True},
                "captions": None}


def _receipts(project: Path) -> list[dict]:
    directory = project / "exports" / "assignments"
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*-delivery.json"))] \
        if directory.is_dir() else []


def _deliver(project, render=None, reframe=None, qc=None):
    from library.tools import deliver_all

    render = render or _RenderDouble(project)
    reframe = reframe if reframe is not None else _ReframeDouble()
    qc = qc or _QcDouble()
    report = deliver_all.deliver_all_approved(
        str(project), platforms=("master",), deliver_one=render,
        qc_one=qc, reframe_one=reframe)
    return report, render, reframe, qc


def test_differing_specs_render_variants_not_missing_rows(project):
    report, render, reframe, qc = _deliver(project)

    assert [job["serves"] for job in report["jobs"]] == [
        ["tiktok"], ["master"]]
    assert sorted(render.calls) == [1, 2]
    assert sorted(row["reel"] for row in report["variants"]) == [1, 2]
    assert all(row["serves"] == ["master"] for row in report["variants"])
    assert report["variant_notes"] == []
    assert report["variant_failures"] == []
    # Each variant reframed off its own reel's master picture.
    by_reel = {row["reel"]: row for row in reframe.calls}
    assert by_reel["Reel 01"]["master_path"].endswith(
        "Reel 01 - pricing.mp4")
    assert by_reel["Reel 02"]["master_path"].endswith("Reel 02 - hook.mp4")
    assert {row["platform"] for row in reframe.calls} == {"master"}
    # Each variant QC'd against its own spec, not the master's.
    assert {row["platform"] for row in qc.calls} == {"tiktok", "master"}

    files = sorted((project / "exports").glob("Reel *.mp4"))
    assert len(files) == 4

    [entry] = _receipts(project)
    rows = {row["name"]: row for row in entry["files"]}
    assert rows["Reel 01 - pricing.master.mp4"]["status"] == "ready"
    assert rows["Reel 02 - hook.master.mp4"]["status"] == "ready"
    assert rows["Reel 01 - pricing.master.mp4"]["spec"]["platform"] == \
        "master"
    assert rows["Reel 01 - pricing.master.mp4"]["platform"] == "master"
    assert entry["selected_platforms"] == ["master", "tiktok"]
    assert entry["missing_outputs"] == []
    assert entry["status"] == "complete"


def test_second_invocation_with_nothing_changed_skips_variants(project):
    report, render, reframe, qc = _deliver(project)
    assert len(report["variants"]) == 2

    from library.tools import deliver_all

    second = deliver_all.deliver_all_approved(
        str(project), platforms=("master",), deliver_one=render,
        qc_one=qc, reframe_one=reframe)
    assert len(reframe.calls) == 2
    assert second["variants"] == []
    assert sorted(row["reel"]
                  for row in second["variants_up_to_date"]) == [1, 2]
    assert len(_receipts(project)) == 1


def test_variant_reframe_failure_is_a_failure_row(project):
    report = _deliver(
        project, reframe=_ReframeDouble(fail_on=("Reel 02",)))[0]

    assert sorted(row["reel"] for row in report["variants"]) == [1]
    [failure] = report["variant_failures"]
    assert failure["reel"] == 2
    assert failure["serves"] == ["master"]
    assert "boom" in failure["error"]

    [entry] = _receipts(project)
    rows = {row["name"]: row for row in entry["files"]}
    assert rows["Reel 01 - pricing.master.mp4"]["status"] == "ready"
    assert rows["Reel 02 - hook.master.mp4"]["status"] == "missing"
    assert "Reel 02 - hook.master.mp4" in entry["missing_outputs"]
    assert entry["status"] == "incomplete"


def test_variant_pending_when_its_master_was_refused(project):
    from library.tools import deliver_all
    from library.tools.reel_deliver import DeliverRefused

    base = _RenderDouble(project)

    def refusing(folder, reel, **kwargs):
        if int(reel) == 2:
            raise DeliverRefused(
                "no reel", "the build never placed it", "rebuild it")
        return base(folder, reel, **kwargs)

    reframe, qc = _ReframeDouble(), _QcDouble()
    report = deliver_all.deliver_all_approved(
        str(project), platforms=("master",), deliver_one=refusing,
        qc_one=qc, reframe_one=reframe)

    assert [row["reel"] for row in report["failed"]] == [2]
    assert [row["reel"] for row in report["variants"]] == [1]
    [note] = report["variant_notes"]
    assert note["reel"] == 2
    assert "no picture to reframe" in note["reason"]


def test_face_track_reaches_the_reframe(project):
    from library.tools import deliver_all

    render, reframe, qc = (_RenderDouble(project), _ReframeDouble(),
                           _QcDouble())
    track = [0.4, 0.5, 0.6]
    seen = []

    def track_for(timeline, duration_seconds, sample_rate_hz):
        seen.append((timeline, duration_seconds, sample_rate_hz))
        return track

    deliver_all.deliver_all_approved(
        str(project), platforms=("master",), deliver_one=render,
        qc_one=qc, reframe_one=reframe, face_track_for=track_for)
    assert len(reframe.calls) == 2
    assert all(row["face_centers"] == track for row in reframe.calls)
    assert sorted(name for name, _, _ in seen) == [
        "Reel 01 - pricing", "Reel 02 - hook"]
