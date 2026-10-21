"""ONE deliver and verify, for a committed edit no reel plan names.

The defect this covers: a committed edit on an arbitrary timeline had
no engine path to a delivered file. `ren deliver` renders REELS built
by the reels process; a full pipeline run covers a run with an
`assembly_manifest`; the AXI edit path had neither. So the run hand-
rolled an export (the section-12 demo wrote `/tmp/bench/render_export.sh`
by hand) and every hand-rolled route skipped the mastering, the
declarations and the records - which is how a file 9.18 dB under the
delivery target shipped with nothing on record that said so.

What this proves:

* the committed-edit route masters, verifies and RECORDS, exactly like
  the reel route;
* a timeline that cannot be found is refused BY NAME, and nothing is
  rendered;
* nothing renders unless the delivery was asked for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from library.tools import deliver_and_verify, reel_deliver, resolve_lock
from library.tools.execution import resolve_render
from tests.resolve_double import FakeTimeline, make_project

TIMELINE_NAME = "Master - the whole episode"


def _project(tmp_path):
    cfg = {"name": "T", "slug": "t",
           "resolve": {"project_name": "Scratch"},
           "pipeline": {}}
    (tmp_path / "project.yaml").write_text(yaml.safe_dump(cfg),
                                           encoding="utf-8")
    return str(tmp_path)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A project with one live timeline, Resolve and the machine faked."""
    folder = _project(tmp_path)
    timeline = FakeTimeline(TIMELINE_NAME, start_frame=0, end_frame=299)
    project = make_project(name="Scratch", timelines=[timeline],
                           current=timeline, frame_rate=30)

    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "lock"))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    # The worker gate is pinned, not measured: this proves the delivery
    # path, and the live `render.build` advertisement depends on
    # whether Resolve happens to be reachable on the runner.
    monkeypatch.setattr(reel_deliver, "check_render", list)
    monkeypatch.setattr("library.tools.reel_build._connect_resolve_project",
                        lambda name: project)

    calls = {}

    def fake_render(timeline_name="", output_dir="", output_name="",
                    fmt="", codec="", timeout_seconds=0, timeline_id="",
                    expected_project_name=""):
        calls.update(timeline_name=timeline_name, timeline_id=timeline_id,
                     expected_project_name=expected_project_name,
                     output_dir=output_dir, output_name=output_name)
        raw = Path(output_dir) / f"{output_name}.mp4"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"\x00" * 200_000)
        return {"output_path": str(raw), "size_bytes": 200_000,
                "job_id": "j1", "job_status": "Complete",
                "timeline_name": timeline_name, "format": fmt,
                "codec": codec}

    def fake_master(report, output_path, target_lufs=-14.0,
                    true_peak_ceiling=-1.0):
        calls.update(target_lufs=target_lufs,
                     true_peak_ceiling=true_peak_ceiling,
                     mastered_to=str(output_path))
        Path(output_path).write_bytes(Path(report["output_path"]).read_bytes())
        return {**report, "raw_output_path": report["output_path"],
                "output_path": output_path, "mastered": True,
                "stage": "mastered", "pre_master": False,
                "mastering": {"output": {"lufs": target_lufs,
                                         "true_peak_dbtp": -1.5}}}

    monkeypatch.setattr(resolve_render, "render_timeline", fake_render)
    monkeypatch.setattr("library.tools.master_loudness.master_render_report",
                        fake_master)
    monkeypatch.setattr(reel_deliver, "verify_deliverable", lambda *a, **k: {
        "output_path": a[0], "passed": True, "duration": {"passed": True},
        "resolution": {"passed": True}, "audio": {"passed": True},
        "content_present": True})
    return folder, calls


def test_a_committed_edit_is_delivered_verified_and_recorded(world,
                                                             tmp_path):
    """The route a reel plan does not name still gets the whole path:
    the render, the mastering to the delivery target, the verification,
    and the delivery identity record beside the file."""
    folder, calls = world

    full = deliver_and_verify.deliver_committed_edit(
        folder, timeline_name=TIMELINE_NAME, source="ren deliver --timeline")

    assert full["delivered"] is True
    # The file handed over is the MASTERED one, not the raw render.
    assert full["render"]["output_path"].endswith(
        f"exports/{TIMELINE_NAME}.mp4")
    assert full["render"]["raw_output_path"].endswith(
        f"scratch/{TIMELINE_NAME}_pre_master.mp4")
    # The delivery spec's targets reached the mastering call.
    assert calls["target_lufs"] == -14.0
    assert calls["true_peak_ceiling"] == -1.0
    # The record beside the file says what the file is.
    record = json.loads(
        (tmp_path / "exports" / f"{TIMELINE_NAME}.deliver.json").read_text(
            encoding="utf-8"))
    assert record["timeline_name"] == TIMELINE_NAME
    assert record["mastered"] is True
    assert record["delivered"] is True
    assert record["source"] == "ren deliver --timeline"
    assert record["timeline_seconds"] == 10.0


def test_a_committed_edit_delivery_is_on_the_ledger(world, tmp_path):
    """The project's ledger names the file, so the batch's skip-check
    and the identity criterion can both find it."""
    from library.tools import deliver_all

    folder, _calls = world
    full = deliver_and_verify.deliver_committed_edit(
        folder, timeline_name=TIMELINE_NAME)

    ledger = deliver_all.read_ledger(folder)
    assert TIMELINE_NAME in ledger
    assert ledger[TIMELINE_NAME]["output_path"] == (
        full["verification"]["output_path"])


def test_a_timeline_that_is_not_there_is_refused_by_name(world):
    """An unknown timeline renders nothing and says which name it
    looked for: delivering the wrong timeline is worse than delivering
    nothing."""
    folder, calls = world

    with pytest.raises(deliver_and_verify.DeliverRefused,
                       match="no unique live timeline"):
        deliver_and_verify.deliver_committed_edit(
            folder, timeline_name="A timeline that is not there")

    assert "render_timeline" not in calls


def test_an_unnamed_delivery_is_refused(world):
    """A delivery renders ONE timeline. Naming none is the unasked
    render this path exists to prevent."""
    folder, calls = world

    with pytest.raises(deliver_and_verify.DeliverRefused,
                       match="no timeline named"):
        deliver_and_verify.deliver_committed_edit(folder)

    assert "render_timeline" not in calls
