"""A render for inspection, through the broker.

The break test could not LOOK at the scratch reel it had revised. The
reel had been IMPORTED rather than built, so it had no Project Model
rows and `ren preview` refused it; and the broker's allowlist carried
no render at all, so the timeline could not be rendered for inspection
either. The review loop had no way to see what it had changed.

`resolve-axi render inspect` is that render. It renders the OPEN
timeline to a scratch file and changes NOTHING in Resolve - no
timeline, no clip, no media-pool item - so the broker may run it: the
review loop renders through the same queue as every other Resolve job,
and the file it produces is the file `ren preview` and `ren deliver`
produce, because it is the SAME render implementation.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest

from library.tools import resolve_axi, resolve_lock
from tests.resolve_double import FakeResolve, FakeTimeline, make_project

TIMELINE_NAME = "Reel 13 - the-accounting-firm-ai-called-healthcare"


def _args(**overrides):
    fields = {"project": "", "out": "", "name": "", "apply": False,
              "json": False}
    fields.update(overrides)
    return type("Args", (), fields)()


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """The scratch Resolve project, with its reel open."""
    return _world(monkeypatch, "Ren Scratch - breaktest")


def _world(monkeypatch, project_name):
    """A Resolve project with one open timeline, and no real Resolve."""
    from library.tools import heavy_work_lock
    from library.tools.execution import resolve_render

    timeline = FakeTimeline(TIMELINE_NAME, start_frame=0, end_frame=1907)
    project = make_project(name=project_name,
                           timelines=[timeline], current=timeline,
                           frame_rate=23.976)
    resolve = FakeResolve(project)

    def engine(job, _timeline):
        path = Path(job["TargetDir"]) / f"{job['OutputFilename']}.mp4"
        path.write_bytes(b"0" * 200_000)
        return True

    project.render_engine = engine
    monkeypatch.setattr(resolve_axi, "_connect", lambda: resolve)
    monkeypatch.setattr(heavy_work_lock, "heavy_work_lock",
                        lambda *_a, **_k: contextlib.nullcontext())
    # The fake file carries no audio stream; the probe is not what this
    # test is about.
    monkeypatch.setattr(resolve_render, "_assert_has_audio",
                        lambda _path: None)
    return project, timeline


# ── The broker's side ───────────────────────────────────────────────


def test_the_broker_runs_the_inspection_render():
    """The render for inspection is broker-eligible.

    It changes no Resolve state, so the broker may run it - and it runs
    EXCLUSIVE, because a render may not share the instance with another
    writer.
    """
    from library.tools.resolved import jobs

    shape = jobs.prepare("resolve_axi", {
        "argv": ["render", "inspect", "--out", "/tmp/inspect", "--apply"]})

    assert shape["mode"] == "exclusive"
    assert shape["priority"] == "qa_render"
    assert shape["executed"] is True


def test_the_broker_still_refuses_the_render_writes():
    """The inspection render is the only render on the list.

    `render queue` and `render start` write to Resolve's render queue
    and spend machine time on a timeline the caller named; they stay
    off the broker.
    """
    from library.tools.resolved import jobs

    for argv in (["render", "queue", "--timeline", TIMELINE_NAME],
                 ["render", "start", "--all"],
                 ["render", "stop"]):
        with pytest.raises(jobs.JobRefused, match="not on the broker"):
            jobs.prepare("resolve_axi", {"argv": argv})


# ── The command's side ─────────────────────────────────────────────


def test_the_inspection_render_writes_a_file_and_leaves_resolve_alone(
        world, tmp_path, capsys):
    """The file lands in the scratch directory, and nothing else moves.

    The render job is deleted, the render format and codec are put
    back, and the cursor stays on the timeline it was already on.
    """
    project, timeline = world
    out = tmp_path / "scratch"

    with resolve_lock.assume_sole_writer(
            "test: the fake project has no instance to contend for"):
        assert resolve_axi.cmd_render_inspect(
            _args(out=str(out), apply=True)) == 0

    written = list(out.glob("*.mp4"))
    assert len(written) == 1, written
    assert written[0].stat().st_size == 200_000
    assert project.render_jobs == [], "the render job was left in the queue"
    assert project.GetCurrentRenderFormatAndCodec() == {
        "format": "mov", "codec": "H.264"}, "the borrowed format was not put back"
    assert project.GetCurrentTimeline() is timeline, (
        "the render moved the cursor off the timeline the caller was "
        "looking at")


def test_the_inspection_render_is_a_plan_until_it_is_applied(
        world, tmp_path, capsys):
    """Without `--apply` nothing is rendered: the plan says what would
    happen, and the file is not written."""
    project, _timeline = world
    out = tmp_path / "scratch"

    with resolve_lock.assume_sole_writer(
            "test: the fake project has no instance to contend for"):
        assert resolve_axi.cmd_render_inspect(_args(out=str(out))) == 0

    assert not out.exists()
    assert project.started_renders == []
    assert "dry run" in capsys.readouterr().out


def test_the_inspection_render_names_the_file_it_wrote(
        world, tmp_path, capsys):
    """The receipt says which file to look at."""
    project, _timeline = world
    out = tmp_path / "scratch"

    with resolve_lock.assume_sole_writer(
            "test: the fake project has no instance to contend for"):
        assert resolve_axi.cmd_render_inspect(
            _args(out=str(out), apply=True, json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["timeline"] == TIMELINE_NAME
    assert Path(payload["output_path"]).is_file()


def test_the_inspection_render_refuses_without_an_output_directory(
        world, capsys):
    """No `--out` is a refusal, not a render into the current directory."""
    project, _timeline = world

    with resolve_lock.assume_sole_writer(
            "test: the fake project has no instance to contend for"):
        assert resolve_axi.cmd_render_inspect(_args(apply=True)) == 1

    assert "--out" in capsys.readouterr().out
    assert project.started_renders == []


def test_the_inspection_render_is_not_refused_on_the_captains_project(
        monkeypatch, tmp_path, capsys):
    """The captain's project carries no refusal of its own here.

    The render for inspection changes nothing in Resolve, so the only
    refusals it inherits are the ones every render already inherits:
    the instance lease, the captain's hold, and the render settings it
    borrows and puts back. It must not refuse a project because that
    project is not a scratch project - that would be a new protection
    the review loop never asked for.
    """
    project, timeline = _world(monkeypatch, "Podcast (field test)")
    out = tmp_path / "scratch"

    with resolve_lock.assume_sole_writer(
            "test: the fake project has no instance to contend for"):
        assert resolve_axi.cmd_render_inspect(
            _args(project="Podcast (field test)", out=str(out),
                  apply=True)) == 0

    assert len(list(out.glob("*.mp4"))) == 1
    assert project.GetCurrentTimeline() is timeline
