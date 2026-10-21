"""A delivery happens when it is ASKED for, and never before.

The captain's standing ruling (2026-09-09, 2026-09-10): a render is
the expensive thing they must ask for; a timeline build is cheap and
is the thing they judge. So the delivery path - the one that renders,
masters, verifies and records a delivered file - is reachable from the
VERBS and from nowhere else.

THE INPUT THAT BREAKS THIS: any call path from `build-reels`, `run`,
either DAG, the operation registry or a pipeline runner into
`library/tools/deliver_and_verify` (or into `execution.resolve_render`'s
`render_timeline`). Add one and the scans below fail. Getting this
wrong turns a 15-minute build into an hour of the captain's machine
without being asked.

The same rule governs the revision route: `ren revise` delivers the
timeline the edit landed on only when `--deliver` was passed.
"""

import inspect
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

DELIVERY_MODULES = ("deliver_and_verify", "deliver_committed_edit",
                    "deliver_timeline")


def _dag_node_ids():
    ids = []
    for dag_path in (REPO_ROOT / "library" / "processes").glob("*/dag.json"):
        dag = json.loads(dag_path.read_text(encoding="utf-8"))
        ids.extend(node["id"] for node in dag.get("nodes", []))
    return ids


# ── Nothing triggers a delivery implicitly ────────────────────────

def test_nothing_triggers_a_delivery_implicitly():
    """No DAG node, no operation, and no `build-reels`/`run`/propose
    body or process runner reaches the delivery path."""
    import manage_project
    from library.tools import operations, processes

    assert processes.execution_order(processes.REELS) == [
        "build_reels", "verify_reels"]
    for node_id in _dag_node_ids():
        assert "deliver" not in node_id, node_id
        assert "export" not in node_id, node_id

    assert not [name for name in operations.names() if "deliver" in name]

    for func in (manage_project.cmd_build_reels, manage_project.cmd_run,
                 manage_project.cmd_propose_reels):
        source = inspect.getsource(func)
        for name in DELIVERY_MODULES:
            assert name not in source, (func.__name__, name)
        assert "render_timeline" not in source, func.__name__

    for runner in (REPO_ROOT / "library" / "processes" / "edit_video"
                   / "run_pipeline.py",
                   REPO_ROOT / "library" / "processes" / "reels"
                   / "run_reels.py"):
        runner_source = runner.read_text(encoding="utf-8")
        for name in DELIVERY_MODULES:
            assert name not in runner_source, (runner.name, name)
        assert "render_timeline" not in runner_source, runner.name


def test_the_delivery_path_is_not_a_dag_step():
    """A step directory that no capability reaches is a defect, and a
    delivery is not a step: it is a verb."""
    from library.tools import project_layout

    unreached = [step.dirname for step in project_layout.STEPS
                 if "deliver" in step.dirname
                 or "export" in step.dirname]
    assert unreached == []


# ── The revision route delivers only when asked ────────────────────

def test_revise_delivers_nothing_unless_it_was_asked(monkeypatch):
    """`ren revise` without `--deliver` renders nothing.

    The gate is on the record (`deliver_requested`), not in the caller,
    so no future caller can deliver by accident either.
    """
    from library.tools import revise

    called = []

    def fake_deliver(*args, **kwargs):
        called.append(args)
        return {"delivered": True}

    monkeypatch.setattr(
        "library.tools.deliver_and_verify.deliver_committed_edit",
        fake_deliver)

    outcome = revise._deliver_landed_edit(
        "/project", {"reel": "Reel 03 - hook"})
    assert called == []
    assert outcome["delivered"] is False
    assert "not asked to deliver" in outcome["error"]


def test_revise_delivers_the_landed_timeline_when_asked(monkeypatch):
    """With the ask recorded, the landed timeline is delivered through
    the one delivery path."""
    from library.tools import revise

    called = []

    def fake_deliver(project_folder, **kwargs):
        called.append((project_folder, kwargs))
        return {"delivered": True, "timeline_name": kwargs.get(
            "timeline_name"), "verification": {"output_path": "/tmp/x.mp4",
                                               "passed": True},
            "report_path": "/tmp/x.deliver.json"}

    monkeypatch.setattr(
        "library.tools.deliver_and_verify.deliver_committed_edit",
        fake_deliver)

    outcome = revise._deliver_landed_edit(
        "/project", {"reel": "Reel 03 - hook", "deliver_requested": True})
    assert outcome["delivered"] is True
    assert called[0][0] == "/project"
    assert called[0][1]["timeline_name"] == "Reel 03 - hook"


def test_a_failed_delivery_does_not_un_land_the_edit(monkeypatch):
    """The edit landed and the file did not arrive: the record says
    both, and the revision is not undone by a render that failed."""
    from library.tools import deliver_and_verify, revise

    def fake_deliver(*args, **kwargs):
        raise deliver_and_verify.DeliverRefused(
            "the render produced nothing", "Resolve wrote no file",
            "render again")

    monkeypatch.setattr(
        "library.tools.deliver_and_verify.deliver_committed_edit",
        fake_deliver)

    outcome = revise._deliver_landed_edit(
        "/project", {"reel": "Reel 03 - hook", "deliver_requested": True})
    assert outcome["delivered"] is False
    assert "the render produced nothing" in outcome["error"]
    assert outcome["fix"] == "render again"


# ── The AXI render route says what it produced ────────────────────

def test_the_axi_render_route_does_not_call_a_pre_master_file_verified(
        world, tmp_path, capsys):
    """`resolve-axi render inspect` renders a PRE-MASTER file, and says
    so.

    The old wording - "verified: yes" - read as though the file were
    finished, and the section-12 demo shipped exactly that file: 9.18 dB
    under the delivery target, with nothing on record that said so.
    """
    from library.tools import resolve_axi, resolve_lock

    out = tmp_path / "scratch"

    with resolve_lock.assume_sole_writer(
            "test: the fake project has no instance to contend for"):
        assert resolve_axi.cmd_render_inspect(
            type("Args", (), {"project": "", "out": str(out), "name": "",
                              "apply": True, "json": True})()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["pre_master"] is True
    assert payload["mastered"] is False
    assert payload["delivered"] is False
    assert "verified" not in payload
    assert Path(payload["record_path"]).is_file()
    record = json.loads(Path(payload["record_path"]).read_text(
        encoding="utf-8"))
    assert record["mastered"] is False
    assert record["delivered"] is False
    assert record["stage"] == "pre_master"


@pytest.fixture()
def world(monkeypatch, tmp_path):
    """The scratch Resolve project, with its timeline open."""
    import contextlib

    from library.tools import heavy_work_lock, resolve_axi
    from library.tools.execution import resolve_render
    from tests.resolve_double import FakeResolve, FakeTimeline, make_project

    timeline = FakeTimeline("Reel 13 - the-accounting-firm-ai-called-healthcare",
                            start_frame=0, end_frame=1907)
    project = make_project(name="Ren Scratch - breaktest",
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
    monkeypatch.setattr(resolve_render, "_assert_has_audio",
                        lambda _path: None)
    return project, timeline
