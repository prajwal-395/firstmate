"""`deliver-reel` is an explicit verb, and nothing may trigger it implicitly.

The captain's standing ruling (2026-09-09, 2026-09-10): a render is the
expensive thing they must ask for. So the ONLY path to a render is the
captain running `manage_project.py deliver-reel <project> <reel>`.

THE INPUT THAT BREAKS THIS: any call path from `build-reels`, `run`,
either DAG, or the operation registry into `library/tools/reel_deliver`
(or `execution.resolve_render`'s `render_timeline`). Add one and the
scan tests below fail. Getting this wrong turns a 15-minute build into
an hour of their machine without being asked.

A test builds its project under `tmp_path`, or it skips. It never falls
back to a real one.
"""

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── Nothing triggers a render implicitly ──────────────────────────

def _dag_node_ids():
    ids = []
    for dag_path in (REPO_ROOT / "library" / "processes").glob("*/dag.json"):
        dag = json.loads(dag_path.read_text(encoding="utf-8"))
        ids.extend(node["id"] for node in dag.get("nodes", []))
    return ids


def test_no_dag_node_renders():
    """No DAG in the tree names a deliver/render node.

    If a node were added, the reels process (or edit_video's run) could
    schedule it at the end of a build - the exact automatic render the
    ruling forbids. The reels process stays exactly two nodes.
    """
    from library.tools import processes

    assert processes.execution_order(processes.REELS) == [
        "build_reels", "verify_reels"]
    for node_id in _dag_node_ids():
        # A deliver/export node would schedule a render at the end of a
        # build - the exact automatic render the ruling forbids. (Nodes
        # named `render_*` render overlay ARTEFACTS onto timelines, not
        # video files, and are not this.)
        assert "deliver" not in node_id, node_id
        assert "export" not in node_id, node_id


def test_no_operation_renders():
    """The operation registry names no deliver operation.

    An operation would make a render addressable from the runner, the
    skill projection and `--break`/`--rerun` addresses - all implicit
    paths. The verb lives outside the registry on purpose.
    """
    from library.tools import operations

    assert "reel.deliver" not in operations.names()
    assert not [name for name in operations.names()
                if "deliver" in name]


def test_build_and_run_paths_cannot_reach_the_render():
    """`build-reels` and `run` never import the deliver path.

    THE INPUT THAT BREAKS THIS TEST is any reference from these bodies
    (or the process runners they drive) to `reel_deliver`,
    `deliver_reel` or `render_timeline`: that reference IS an implicit
    trigger, however it is reached. Read off the source, not the docs.
    """
    import manage_project

    for func in (manage_project.cmd_build_reels, manage_project.cmd_run,
                 manage_project.cmd_propose_reels):
        source = inspect.getsource(func)
        assert "reel_deliver" not in source, func.__name__
        assert "render_timeline" not in source, func.__name__

    for runner in (REPO_ROOT / "library" / "processes" / "edit_video"
                   / "run_pipeline.py",
                   REPO_ROOT / "library" / "processes" / "reels"
                   / "run_reels.py"):
        runner_source = runner.read_text(encoding="utf-8")
        assert "reel_deliver" not in runner_source, runner.name
        assert "deliver_reel" not in runner_source, runner.name


# ── The verb refuses rather than guesses ──────────────────────────

def _bare_project(tmp_path, pipeline_block=""):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        "name: Test\nslug: test\npipeline:\n"
        f"{pipeline_block}",
        encoding="utf-8")
    return str(root)


def _project_with_proposal(tmp_path, approved=(3,), proposed=()):
    from library.tools.reel_proposal import (
        Approval,
        ReelMoment,
        proposal_path,
        write_proposal,
    )

    folder = _bare_project(tmp_path)
    moments = [
        ReelMoment(number=n, slug="hook", reason="why",
                   timeline_start=0.0, timeline_end=10.0,
                   approval=Approval.APPROVED)
        for n in approved
    ] + [
        ReelMoment(number=n, slug="maybe", reason="why",
                   timeline_start=0.0, timeline_end=10.0,
                   approval=Approval.PROPOSED)
        for n in proposed
    ]
    write_proposal(proposal_path(folder), moments, {})
    return folder


def test_deliver_refuses_an_unknown_reel(tmp_path):
    """A reel number the plan does not name is refused, naming the input."""
    from library.tools import reel_deliver

    folder = _project_with_proposal(tmp_path)
    with pytest.raises(reel_deliver.DeliverRefused, match="no reel 9"):
        reel_deliver.timeline_name_for_reel(folder, 9)


def test_deliver_refuses_an_unapproved_reel(tmp_path):
    """A moment the captain left PROPOSED is not deliverable."""
    from library.tools import reel_deliver

    folder = _project_with_proposal(tmp_path, approved=(), proposed=(4,))
    with pytest.raises(reel_deliver.DeliverRefused, match="not approved"):
        reel_deliver.timeline_name_for_reel(folder, 4)


# ── The declaration slot, not a default ───────────────────────────


def test_read_deliver_declaration_refuses_what_nothing_reads(tmp_path):
    """A preset key no renderer reads is refused, not silently kept."""
    from library.tools import reel_deliver

    folder = _bare_project(
        tmp_path, "  deliver_preset: {format: mp4, bitrate: high}\n")
    with pytest.raises(reel_deliver.DeliverRefused, match="bitrate"):
        reel_deliver.read_deliver_declaration(folder)


# ── The preflight: refuse fast, naming the stale overlays ───────

def _pool_tree(timeline_name, clips):
    """A fake media pool: one bin named for the reel, holding `clips`.

    Each clip is `(clip_name, file_path, pool_resolution_or_None)`.
    """
    items = []
    for name, path, resolution in clips:
        item = MagicMock()
        item.GetClipProperty.side_effect = lambda key, _n=name, _p=path, \
            _r=resolution: {"Clip Name": _n, "File Path": _p,
                            "Resolution": _r}[key]
        items.append(item)
    folder = MagicMock()
    folder.GetName.return_value = timeline_name
    folder.GetClipList.return_value = items
    folder.GetSubFolderList.return_value = []
    root = MagicMock()
    root.GetSubFolderList.return_value = [folder]
    project = MagicMock()
    project.GetMediaPool.return_value.GetRootFolder.return_value = root
    return project


def test_preflight_names_stale_overlays(tmp_path):
    """Pool metadata disagreeing with disk is named per file, fast.

    Measured 2026-09-13 on Reel 26: ten captions re-rendered at the
    constant 904 canvas while the pool still read each file's
    predecessor width. A deliver that queued the render anyway burned
    the captain's machine to learn what the pool already said.
    """
    from library.tools import reel_deliver

    stale_path = str(tmp_path / "sub_new.mov")
    fresh_path = str(tmp_path / "sub_ok.mov")
    Path(stale_path).write_bytes(b"\x00")
    Path(fresh_path).write_bytes(b"\x00")
    project = _pool_tree("Reel 03 - hook", [
        ("sub_new.mov", stale_path, "866x480"),
        ("sub_ok.mov", fresh_path, "904x480"),
    ])
    with patch.object(reel_deliver, "_disk_resolution",
                      side_effect=lambda p: (904, 480)):
        found = reel_deliver.overlay_staleness(project, "Reel 03 - hook")
    assert len(found) == 1
    assert found[0]["clip"] == "sub_new.mov"
    assert found[0]["pool_resolution"] == "866x480"
    assert found[0]["disk_resolution"] == "904x480"


def test_deliver_refuses_on_stale_overlays_before_rendering(tmp_path):
    """The refusal lands before a render job exists, naming the file."""
    from library.tools import reel_deliver

    folder = _project_with_proposal(tmp_path)

    timeline = MagicMock()
    timeline.GetName.return_value = "Reel 03 - hook"
    timeline.GetSetting.return_value = "30"
    timeline.GetStartFrame.return_value = 0
    timeline.GetEndFrame.return_value = 299
    project = MagicMock()
    project.GetCurrentTimeline.return_value = timeline
    render = MagicMock()

    stale = [{"clip": "sub_new.mov", "path": "/exports/sub_new.mov",
              "pool_resolution": "866x480",
              "disk_resolution": "904x480"}]
    with (patch("library.tools.reel_build._connect_resolve_project",
                return_value=project),
          patch("library.tools.execution.resolve_render._find_timeline",
                return_value=timeline),
          patch("library.tools.execution.resolve_render.render_timeline",
                render),
          patch.object(reel_deliver, "overlay_staleness",
                       return_value=stale)):
        with pytest.raises(reel_deliver.DeliverRefused,
                           match="sub_new.mov"):
            reel_deliver.deliver_reel(folder, 3)
    render.assert_not_called()


# ── The reel-shaped caller renders the reel's timeline ────────────

def test_deliver_renders_exactly_the_reel_timeline(tmp_path):
    """The caller passes the reel's EXACT name to the existing renderer.

    `resolve_render.render_timeline` produces the raw render; delivery
    masters that file into exports and verifies the returned path. This
    proves the reel-shaped caller with Resolve stubbed and QA answering
    pass on a real (tiny) file.
    """
    from library.tools import reel_deliver
    from library.tools.render_qa import RenderQAResult

    folder = _project_with_proposal(tmp_path)
    video = Path(folder) / "reel_pre_master.mp4"
    video.write_bytes(b"\x00" * 200_000)

    timeline = MagicMock()
    timeline.GetName.return_value = "Reel 03 - hook"
    timeline.GetSetting.return_value = "30"
    timeline.GetStartFrame.return_value = 0
    timeline.GetEndFrame.return_value = 299  # 300 frames @ 30fps = 10s

    # The captain's cursor: on the master, and it must stay there -
    # deliver looks the reel up by handle and never selects it, so
    # there is nothing to put back (2026-09-20: the unleased select
    # and restore walked the cursor out from under a sibling lane).
    prior = MagicMock()
    prior.GetName.return_value = "GEO Podcast - Synced"
    project = MagicMock()
    project.GetCurrentTimeline.return_value = prior
    project.GetTimelineCount.return_value = 2
    project.GetTimelineByIndex.side_effect = [timeline, prior]

    calls = {}

    def fake_render(timeline_name="", output_dir="", output_name="",
                    fmt="", codec="", timeout_seconds=0):
        calls.update(timeline_name=timeline_name, output_dir=output_dir,
                     output_name=output_name, fmt=fmt, codec=codec)
        raw_path = Path(output_dir) / f"{output_name}.mp4"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(video.read_bytes())
        return {"output_path": str(raw_path), "size_bytes": 200_000,
                "job_id": "j1", "job_status": "Complete",
                "timeline_name": timeline_name, "format": fmt,
                "codec": codec}

    def fake_master(report, output_path):
        Path(output_path).write_bytes(Path(report["output_path"]).read_bytes())
        return {**report, "raw_output_path": report["output_path"],
                "output_path": output_path,
                "mastering": {"output": {"lufs": -14.0,
                                           "true_peak_dbtp": -1.5}}}

    def fake_duration(path, expected_seconds, tolerance_pct=10.0):
        assert abs(expected_seconds - 10.0) < 0.01
        return RenderQAResult("duration", True, expected_seconds,
                              expected_seconds, "info", "10s")

    def fake_resolution(path, expected_width=0, expected_height=0):
        assert (expected_width, expected_height) == (1080, 1920)
        return RenderQAResult("resolution", True,
                              {"width": 1080, "height": 1920},
                              {"expected_width": 1080,
                               "expected_height": 1920}, "info", "1080x1920")

    def fake_audio(path, min_streams=1):
        return RenderQAResult("audio_streams", True, 2, 1, "info", "2")

    def fake_black(path, **kwargs):
        return RenderQAResult("black_frames", True, [], None, "info",
                              "no black")

    with (patch("library.tools.reel_build._connect_resolve_project",
                return_value=project),
          patch("library.tools.execution.resolve_render.render_timeline",
                side_effect=fake_render),
          patch("library.tools.master_loudness.master_render_report",
                side_effect=fake_master),
          patch("library.tools.render_qa.verify_duration",
                side_effect=fake_duration),
          patch("library.tools.render_qa.verify_resolution",
                side_effect=fake_resolution),
          patch("library.tools.render_qa.verify_audio_streams",
                side_effect=fake_audio),
          patch("library.tools.render_qa.detect_black_frames",
                side_effect=fake_black),
          # The success path runs against a clean pool: the stale case
          # is proven by test_deliver_refuses_on_stale_overlays_.
          patch("library.tools.reel_deliver.overlay_staleness",
                return_value=[])):
        full = reel_deliver.deliver_reel(folder, 3)

    assert calls["timeline_name"] == "Reel 03 - hook"
    assert calls["output_dir"].endswith("pipeline_output/scratch")
    # The captain's cursor never moved: deliver names a handle and
    # `render_timeline` selects under its own lease.
    project.SetCurrentTimeline.assert_not_called()
    assert full["delivered"] is True
    assert full["verification"]["passed"] is True
    assert full["verification"]["content_present"] is True
    assert full["render"]["output_path"].endswith(
        "exports/Reel 03 - hook.mp4")
    assert full["render"]["raw_output_path"].endswith(
        "pipeline_output/scratch/Reel 03 - hook_pre_master.mp4")
    assert Path(full["report_path"]).is_file()
