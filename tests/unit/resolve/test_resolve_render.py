"""H4 - render settings and the render queue are project-global.

History: docs/evidence/resolve_test_history.md#test_render_borrow_restore.
"""
import pytest
from unittest.mock import patch
from library.tools.segment_renderer import (
    render_segment,
)
from library.tools import resolve_lock
from library.tools.execution.resolve_render import (
    RenderError,
    _find_timeline,
    _select_timeline,
)
from tests.resolve_double import FakeTimeline, make_project
from library.tools import segment_renderer
from tests.resolve_double import FakeProject, FakeResolve
import datetime
import json
import os
import sys
import select
import subprocess
import textwrap
import threading
from pathlib import Path
from library.tools import resolve_axi
import shutil
from library.tools import visual_qa_router
from library.tools.qa_fidelity import (
    GENERATED_ASSET, RESOLVE_COMPOSITE, SOURCE_PIXEL)


def test_normal_neural_directives_never_call_resolve_stabilize():
    """A normal build can carry Super Scale without calling Stabilize."""
    from library.steps.step_6_01_render import resolve_build_timeline

    class Timeline:
        def GetItemListInTrack(self, media_type, track_index):
            assert media_type == "video"
            return [object()]

    manifest = {"neural_engine_directives": {
        "speech_1": {"super_scale": 2},
    }}
    with patch.object(resolve_build_timeline, "apply_stabilization") as apply:
        with patch.object(resolve_build_timeline, "apply_super_scale",
                           return_value=True):
            resolve_build_timeline._apply_neural_engine_directives(
                manifest, Timeline(), ["speech_1"], [], {"warnings": []})
    apply.assert_not_called()


def test_resolve_stabilize_refuses_a_directive_without_user_provenance():
    from library.steps.step_6_01_render import resolve_build_timeline

    class Timeline:
        def GetItemListInTrack(self, media_type, track_index):
            return [object()]

    manifest = {"neural_engine_directives": {
        "speech_1": {"stabilize": True},
    }}
    with patch.object(resolve_build_timeline, "apply_stabilization") as apply:
        with pytest.raises(ValueError, match="no valid user authorization"):
            resolve_build_timeline._apply_neural_engine_directives(
                manifest, Timeline(), ["speech_1"], [], {"warnings": []})
    apply.assert_not_called()


# ── Mock helpers ────────────────────────────────────────────────

class MockProject:
    """Tracks every call to render-state-mutating methods."""

    def __init__(self, *, format_codec=None, render_ok=True,
                 job_id="job-42", rendering_progress=None):
        self._format_codec = format_codec or {"format": "mov", "codec": "ProRes"}
        self._render_ok = render_ok
        self._job_id = job_id
        self._rendering_progress = rendering_progress or []

        # Call tracking
        self.deleted_all = False
        self.deleted_jobs = []
        self.set_format_codec_calls = []
        self.set_render_settings_calls = []
        self._queued = []

    def GetCurrentRenderFormatAndCodec(self):
        return dict(self._format_codec)

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self.set_format_codec_calls.append((fmt, codec))
        return True

    def SetRenderSettings(self, settings):
        self.set_render_settings_calls.append(dict(settings))
        return True

    def AddRenderJob(self):
        self._queued.append(dict(self.set_render_settings_calls[-1],
                                 JobId=self._job_id)
                            if self.set_render_settings_calls
                            else {"JobId": self._job_id})
        return self._job_id

    def GetRenderJobList(self):
        # `render_segment` reads the queue back before it starts, so a
        # range Resolve silently widened refuses instead of rendering
        # the whole timeline (`tests/test_segment_render_range_takes`).
        # The mock therefore has to report what it was asked for.
        return [dict(job) for job in self._queued]

    def StartRendering(self, *args, **kwargs):
        return self._render_ok

    def IsRenderingInProgress(self):
        if self._rendering_progress:
            return self._rendering_progress.pop(0)
        return False

    def StopRendering(self):
        pass

    def GetRenderJobStatus(self, job_id):
        return {"JobStatus": "Complete", "CompletionPercentage": 100}

    def DeleteAllRenderJobs(self):
        self.deleted_all = True
        return True

    def DeleteRenderJob(self, job_id):
        self.deleted_jobs.append(job_id)
        return True

    def GetRenderFormats(self):
        return {"mp4": ".mp4", "mov": ".mov"}

    def GetCurrentTimeline(self):
        return getattr(self, "_current_timeline", None)

    def SetCurrentTimeline(self, tl):
        self._current_timeline = tl
        return True


class MockTimeline:
    def __init__(self, name="TestTimeline"):
        self._name = name

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return str(id(self))

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return 100

    def GetSetting(self, key):
        settings = {
            "timelineResolutionWidth": "1080",
            "timelineResolutionHeight": "1920",
        }
        return settings.get(key)


class MockResolve:
    def __init__(self, page="edit"):
        self._page = page
        self.opened_pages = []

    def GetCurrentPage(self):
        return self._page

    def OpenPage(self, page):
        self.opened_pages.append(page)
        self._page = page
        return True


class ExplodingProject(MockProject):
    """Raises during render to test the exception path."""

    def StartRendering(self, *args, **kwargs):
        raise RuntimeError("Simulated render explosion")


# ── H4 Tests: segment_renderer ─────────────────────────────────

class TestSegmentRendererRestoresWhatItBorrowed:
    """format/codec, the page and the render queue are restored on every
    exit, and only the job this process created is deleted - never
    everyone's (DeleteAllRenderJobs destroys the captain's queued jobs,
    H4a)."""

    def test_restored_on_success(self, tmp_path):
        resolve = MockResolve(page="edit")
        project = MockProject(format_codec={"format": "mov", "codec": "ProRes"},
                              job_id="my-job-99")
        # A fake rendered file so the function finds it.
        (tmp_path / "qa_segment_0_10.mov").write_bytes(b"\x00" * 4096)
        render_segment(resolve, project, MockTimeline(),
                       mark_in=0, mark_out=10, output_dir=str(tmp_path))
        assert project.set_format_codec_calls == [("mov", "ProRes")]
        assert "my-job-99" in project.deleted_jobs
        assert not project.deleted_all
        assert resolve.opened_pages[-1] == "edit"

    def test_restored_when_the_body_raises(self, tmp_path):
        """The bug H4 exists to prevent."""
        resolve = MockResolve(page="color")
        project = ExplodingProject(
            format_codec={"format": "mp4", "codec": "H264"}, job_id="my-job-77")
        with pytest.raises(RuntimeError, match="Simulated render explosion"):
            render_segment(resolve, project, MockTimeline(),
                           mark_in=0, mark_out=10, output_dir=str(tmp_path))
        assert project.set_format_codec_calls == [("mp4", "H264")]
        assert "my-job-77" in project.deleted_jobs
        assert not project.deleted_all
        assert resolve.opened_pages[-1] == "color"


# ── resolve_render (the full-timeline renderer) ────────────────
#
# render_timeline calls _connect() internally, so we monkeypatch it
# to inject our mocks.

from library.tools.execution.resolve_render import (
    render_timeline,
)


class RenderTimelineMockProject(MockProject):
    """Extends MockProject for render_timeline's broader API surface."""

    def __init__(self, *, timeline_name="TestTimeline", **kwargs):
        super().__init__(**kwargs)
        self._render_formats = {"mp4": ".mp4", "mov": ".mov"}
        self._timeline = MockTimeline(timeline_name)

    def GetRenderFormats(self):
        return self._render_formats

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self.set_format_codec_calls.append((fmt, codec))
        return True

    def GetCurrentRenderFormatAndCodec(self):
        return dict(self._format_codec)

    def GetRenderJobStatus(self, job_id):
        return {"JobStatus": "Complete", "CompletionPercentage": 100}

    def GetTimelineCount(self):
        return 1

    def GetTimelineByIndex(self, i):
        return self._timeline


class RenderTimelineMockProjectManager:
    def __init__(self, project):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class RenderTimelineMockResolve(MockResolve):
    def __init__(self, project, **kwargs):
        super().__init__(**kwargs)
        self._pm = RenderTimelineMockProjectManager(project)

    def GetProjectManager(self):
        return self._pm


class ExplodingRenderProject(RenderTimelineMockProject):
    """SetRenderSettings raises to test the exception path."""

    def SetRenderSettings(self, settings):
        self.set_render_settings_calls.append(dict(settings))
        raise RuntimeError("Simulated SetRenderSettings explosion")


class TestRenderTimelineRestoreOnException:
    """resolve_render.render_timeline restores on the exception path."""

    def test_format_codec_and_page_restored_when_body_raises(self, monkeypatch, tmp_path):
        """format/codec and the page are restored even when SetRenderSettings raises."""
        project = ExplodingRenderProject(
            format_codec={"format": "mov", "codec": "ProRes"},
        )
        resolve = RenderTimelineMockResolve(project, page="color")

        monkeypatch.setattr(
            "library.tools.execution.resolve_render._connect",
            lambda: resolve,
        )

        with pytest.raises(RuntimeError, match="Simulated SetRenderSettings explosion"):
            render_timeline(
                timeline_name="TestTimeline",
                output_dir=str(tmp_path),
                output_name="test",
            )

        assert project.set_format_codec_calls == [
            ("mp4", "H264"),    # the format we asked for
            ("mov", "ProRes"),  # the restore in finally
        ], (
            "format/codec was NOT restored when the body raised - "
            "this is the bug H4 exists to prevent"
        )
        assert resolve.opened_pages[-1] == "color", (
            "Page was not restored when the body raised"
        )

    def test_own_job_deleted_when_body_raises(self, monkeypatch, tmp_path):
        """Our job is cleaned up even when the render fails mid-way."""
        project = RenderTimelineMockProject(
            format_codec={"format": "mov", "codec": "ProRes"},
            job_id="render-job-88",
        )
        resolve = RenderTimelineMockResolve(project, page="edit")

        # Make StartRendering raise after AddRenderJob succeeds
        def exploding_start(*args, **kwargs):
            raise RuntimeError("Simulated StartRendering explosion")
        project.StartRendering = exploding_start

        monkeypatch.setattr(
            "library.tools.execution.resolve_render._connect",
            lambda: resolve,
        )

        with pytest.raises(RuntimeError, match="Simulated StartRendering explosion"):
            render_timeline(
                timeline_name="TestTimeline",
                output_dir=str(tmp_path),
                output_name="test",
            )

        assert "render-job-88" in project.deleted_jobs, (
            "Own job was NOT cleaned up when the body raised"
        )
        assert not project.deleted_all, (
            "DeleteAllRenderJobs was called instead of DeleteRenderJob"
        )


# --------------------------------------------------------------------------
# From test_render_select_goes_through_the_guard.py
#
# Render selection goes through the cursor guard.
#
# `resolve_render._select_timeline` establishes the cursor before the
# render queue is touched. A direct `SetCurrentTimeline` there walked
# the cursor unleashed - the 2026-09-20 shape that killed a sibling
# lane's Fusion pass - so the select goes through
# `assert_current_timeline` (lease refusal plus read-back), while
# `_find_timeline` names a handle without moving anything at all.

@pytest.fixture
def unguarded(monkeypatch):
    """Undo conftest's session-wide sole-writer declaration."""
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)


def _project():
    mine = FakeTimeline("Pipeline_Edit")
    sibling = FakeTimeline("Reel 05 - final")
    project = make_project(timelines=[mine, sibling], current=sibling)
    return project, mine, sibling


def test_find_names_a_handle_without_moving_the_cursor(unguarded):
    """Lookup is read-only: no lease needed, cursor untouched."""
    project, mine, sibling = _project()
    assert _find_timeline(project, mine.GetName()) is mine
    assert project.GetCurrentTimeline() is sibling
    assert project.set_calls == []


def test_find_of_a_missing_timeline_names_it(unguarded):
    project, _, _ = _project()
    with pytest.raises(RenderError, match="not in this project"):
        _find_timeline(project, "gone")


def test_select_without_a_lease_is_refused_before_moving(unguarded):
    """The 2026-09-20 shape fails loud instead of landing elsewhere."""
    assert not resolve_lock.held()
    project, mine, sibling = _project()
    with pytest.raises(resolve_lock.UnguardedPlacementError):
        _select_timeline(project, mine.GetName())
    assert project.GetCurrentTimeline() is sibling
    assert project.set_calls == []


def test_select_under_a_lease_asserts_the_cursor():
    """Leased: the cursor is established and read back."""
    from library.tools.resolve_lock import assume_sole_writer

    project, mine, _sibling = _project()
    with assume_sole_writer("test: fake project has no instance to contend for"):
        assert _select_timeline(project, mine.GetName()) is mine
    assert project.GetCurrentTimeline() is mine
    assert project.set_calls == [mine.GetName()]


# --------------------------------------------------------------------------
# From test_segment_render_range_takes.py
#
# A one-frame render must render ONE frame, and prove it before starting.
#
# Measured 2026-09-11 on `Podcast (field test)` / Reel 09.  Asking
# `render_single_frame` for frame 300 queued a job with MarkIn 0 and
# MarkOut 1665 and rendered **1,471 TIFFs of the entire reel** before it
# was stopped by hand.  `SetRenderSettings` returned True, `AddRenderJob`
# returned a job id, and neither was a lie about anything except the one
# thing that mattered: Resolve ignores MarkIn/MarkOut unless
# `SelectAllFrames` is also set False.
#
# That is AGENTS.md 5 arriving from a new direction.  The rule there is
# that a Resolve call is judged by what it RETURNS - but both returns
# here were fine.  What was never read back was the QUEUE, which is where
# the range either took or did not.
#
# The cost of not reading it is not a slow check.  The captain's machine
# is the fleet's one hard CPU limiter, and a "single frame" that renders
# a whole timeline is an outage on it.  So the range is read back off
# `GetRenderJobList` and a mismatch refuses BEFORE `StartRendering`.

def _render(tmp_path, ignore_marks=False):
    """Ask for frame 300 of a 0-1665 reel."""
    tl = FakeTimeline("T", end_frame=1665)
    project = FakeProject([tl], current=tl)
    project.ignore_render_marks = ignore_marks
    result = segment_renderer.render_segment(
        FakeResolve(project), project, tl,
        mark_in=300, mark_out=300,
        output_dir=str(tmp_path), resolution="full",
        custom_name="frame_300")
    return project, result


def test_the_range_is_asked_for_with_select_all_frames_off(tmp_path):
    """Without this key Resolve silently renders the whole timeline."""
    project, _ = _render(tmp_path)
    settings = project.render_settings
    assert settings.get("SelectAllFrames") is False, (
        "SetRenderSettings was called without `SelectAllFrames: False`, "
        "so Resolve ignores MarkIn/MarkOut and queues the whole "
        "timeline. This is the 1,471-TIFF render of 2026-09-11.")
    assert settings["MarkIn"] == 300
    assert settings["MarkOut"] == 300


def test_a_range_that_did_not_take_refuses_before_rendering(tmp_path):
    """The whole-timeline case: queued 0-1665 for a request of 300."""
    project, result = _render(tmp_path, ignore_marks=True)
    assert result.success is False
    assert "0" in result.error and "1665" in result.error, result.error
    assert project.started_renders == [], (
        "render_segment started a render of a range nobody asked for - "
        "1,666 frames instead of 1. The queue is read back precisely so "
        "this refuses before any frame is rendered.")
    assert project.GetRenderJobList() == [], (
        "the refused job was left in the captain's render queue")


def test_a_job_that_never_reached_the_queue_refuses(tmp_path):
    """AddRenderJob returning an id is not evidence of a queued job."""
    tl = FakeTimeline("T", end_frame=1665)
    project = FakeProject([tl], current=tl)
    project.GetRenderJobList = lambda: []
    result = segment_renderer.render_segment(
        FakeResolve(project), project, tl, mark_in=300, mark_out=300,
        output_dir=str(tmp_path), resolution="full", custom_name="f")
    assert result.success is False
    assert project.started_renders == []


def test_a_range_that_took_is_allowed_to_render(tmp_path):
    """The guard must not refuse the correct case - it would be a gate
    that fails correct output (AGENTS.md 10.4)."""
    project, result = _render(tmp_path)
    assert project.started_renders == [["job-1"]], (
        "the render never started even though Resolve queued exactly "
        "the frame that was asked for")
    # No file was ever written by these fakes, so the result reports
    # the MISSING FILE - not a refused range.
    assert result.success is False
    assert "not found" in (result.error or "").lower(), result.error


# --------------------------------------------------------------------------
# From test_export_filename_matches_timeline.py
#
# D9 regression: the export file must carry the timeline's timestamp.
#
# PR 460 timestamps the Resolve timeline (`<base>_<strftime>_<dur>s`) so
# drafts accumulate instead of colliding. But step 6.01's `_export_timeline`
# recomputed the export filename from the manifest's base project name, so
# every run overwrote `exports/<base>.mp4` in place while the timelines
# piled up beside it.
#
# The export name must be the timeline name the build just created - the
# expression is identity, not a second timestamp computation that could
# drift by a second from the timeline's own.

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
# step.py does `from resolve_build_timeline import build_timeline` - a
# sibling import served by tests/conftest.py, which owns every non-root
# sys.path entry so collection order cannot change what it binds to.

# Full package path, never bare `import step`: several test modules insert
# different step directories at sys.path[0] (and collection order decides
# which one a bare name binds to), so the bare name resolves to the wrong
# step.py under full-directory collection.
from library.steps.step_6_01_render import step as render_step


def _timestamped_timeline_name(base_name, duration_seconds):
    """The same expression resolve_build_timeline uses for the timeline."""
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    duration_str = f"_{int(duration_seconds)}s" if duration_seconds else ""
    return f"{base_name}_{timestamp}{duration_str}"


def _capture_export_name(monkeypatch, tmp_path):
    """Run _export_timeline with the render and mastering stubbed.

    Resolve renders `<name>_pre_master` into scratch and mastering writes
    the delivery file into exports; `captured["delivered"]` is the path
    the mastered export is written to.
    """
    from library.tools import master_loudness

    captured = {}

    class FakeProc:
        returncode = 0
        stdout = json.dumps({"output_path": str(tmp_path / "raw.mp4"),
                             "size_bytes": 1, "job_id": "j",
                             "job_status": "Complete", "format": "mp4",
                             "codec": "H264"})
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    def fake_master(report, output_path, **kwargs):
        captured["delivered"] = output_path
        return dict(report, output_path=output_path)

    monkeypatch.setattr(render_step.subprocess, "run", fake_run)
    monkeypatch.setattr(master_loudness, "master_render_report", fake_master)
    return captured


def test_export_filename_matches_timestamped_timeline(monkeypatch, tmp_path):
    """The --name passed to the render must equal the timeline just built."""
    base_name = "Pipeline_Edit_Replanned"
    timeline_name = _timestamped_timeline_name(base_name, 58)
    assert timeline_name != base_name  # the timestamp scheme actually fired

    manifest = {"project": {"name": base_name}}
    inputs = {"project_folder": str(tmp_path)}

    captured = _capture_export_name(monkeypatch, tmp_path)
    render_step._export_timeline(timeline_name, inputs, manifest)

    cmd = captured["cmd"]
    name_flag = cmd[cmd.index("--name") + 1]
    # Identity with the built timeline's name - not the manifest base name.
    assert name_flag == f"{timeline_name}_pre_master"
    assert os.path.basename(captured["delivered"]) == f"{timeline_name}.mp4"


def test_export_falls_back_to_manifest_when_no_timeline(monkeypatch, tmp_path):
    """Without a built timeline name there is nothing timestamped to match."""
    from library.tools.brand_registry import DEFAULT_TIMELINE_NAME

    manifest = {"project": {"name": "Some_Base"}}
    inputs = {"project_folder": str(tmp_path)}

    captured = _capture_export_name(monkeypatch, tmp_path)
    render_step._export_timeline("", inputs, manifest)

    cmd = captured["cmd"]
    name_flag = cmd[cmd.index("--name") + 1]
    assert name_flag == f"{manifest['project']['name']}_pre_master"
    assert os.path.basename(captured["delivered"]) == "Some_Base.mp4"

    captured2 = _capture_export_name(monkeypatch, tmp_path)
    render_step._export_timeline("", inputs, {})
    cmd2 = captured2["cmd"]
    assert cmd2[cmd2.index("--name") + 1] == \
        f"{DEFAULT_TIMELINE_NAME}_pre_master"
    assert os.path.basename(captured2["delivered"]) == \
        f"{DEFAULT_TIMELINE_NAME}.mp4"


# --------------------------------------------------------------------------
# From test_resolve_axi_render_lease.py
#
# A started Resolve render keeps the instance lease through completion.

REPO = Path(__file__).resolve().parents[3]

_WAITER = textwrap.dedent("""
    import sys
    sys.path.insert(0, {repo!r})
    from library.tools import resolve_lock
    original_flock = resolve_lock._flock
    reported_contention = False
    def observed_flock(handle, exclusive, blocking):
        global reported_contention
        acquired = original_flock(handle, exclusive, blocking)
        if not acquired and not reported_contention:
            reported_contention = True
            print("WAITING", flush=True)
        return acquired
    resolve_lock._flock = observed_flock
    with resolve_lock.resolve_lease("second leased client", timeout=5):
        print("ACQUIRED", flush=True)
""")


class _Project:
    def __init__(self):
        self.rendering = False
        self.queue_reads = 0
        self.render_observed = threading.Event()
        self.release_queue_read = threading.Event()

    def GetName(self):
        return "Podcast (field test)"

    def GetRenderJobList(self):
        self.queue_reads += 1
        if self.queue_reads > 1:
            # The old implementation returns after this read despite the
            # render still running. Hold it here until the other process has
            # proved it is contending for the real temporary flock.
            self.release_queue_read.wait(timeout=5)
        return [{"JobId": "job-1", "RenderJobName": "stub render",
                 "TimelineName": "Reel 01", "TargetDir": "/tmp",
                 "OutputFilename": "stub.mov"}]

    def GetRenderJobStatus(self, _job_id):
        return {"JobStatus": "Rendering" if self.rendering else "Complete",
                "CompletionPercentage": 50 if self.rendering else 100}

    def StartRendering(self, _job_ids, _interactive=False):
        self.rendering = True
        return True

    def IsRenderingInProgress(self):
        self.render_observed.set()
        return self.rendering


class _Manager:
    def __init__(self, project):
        self.project = project

    def GetCurrentProject(self):
        return self.project


class _Resolve:
    def __init__(self, project):
        self.manager = _Manager(project)

    def GetProjectManager(self):
        return self.manager


def test_render_start_keeps_lease_until_resolve_reports_completion(
        tmp_path, monkeypatch):
    lock_dir = tmp_path / "resolve-lock"
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lock_dir))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)

    project = _Project()
    monkeypatch.setattr(resolve_axi, "_connect", lambda: _Resolve(project))
    monkeypatch.setattr(resolve_axi, "RENDER_POLL_SECONDS", 0.01,
                        raising=False)
    results = []
    command = threading.Thread(
        target=lambda: results.append(resolve_axi.cmd_render_start(
            type("Args", (), {"project": "", "job": [], "all": True,
                               "apply": True})())))
    command.start()

    waiter = None
    try:
        assert project.render_observed.wait(timeout=2)
        waiter_env = dict(os.environ)
        # The subprocess is an independent client, not a child worker that
        # should inherit the caller's lease.
        waiter_env.pop(resolve_lock.INHERIT_ENV, None)
        waiter_env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
        waiter = subprocess.Popen(
            [sys.executable, "-c", _WAITER.format(repo=str(REPO))],
            cwd=REPO,
            env=waiter_env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        assert waiter.stdout.readline().strip() == "WAITING"

        # Let any post-start queue read return. The fixed command continues
        # polling under the lease; the old command released it here.
        project.release_queue_read.set()
        acquired_before_completion, _, _ = select.select(
            [waiter.stdout], [], [], 0.4)
        assert not acquired_before_completion, \
            "a second Resolve client entered while the render was in progress"

        project.rendering = False
        command.join(timeout=3)
        assert not command.is_alive(), "render command did not finish"
        assert results == [0]
        assert waiter.stdout.readline().strip() == "ACQUIRED"
        waiter.communicate(timeout=3)
    finally:
        project.release_queue_read.set()
        project.rendering = False
        command.join(timeout=3)
        if waiter is not None:
            if waiter.poll() is None:
                waiter.terminate()
            waiter.communicate(timeout=3)


# --------------------------------------------------------------------------
# From test_render_batching.py
#
# A QA pass renders through Resolve ONCE, and only what needs the composite.
#
# Before: every frame grab and every segment check was its own
# configure-render-cleanup on the Deliver page, the perceptual observation
# rendered the same clip midpoints a second time, and `clip_placement` -
# "is this the right footage?", which the source file answers - was
# rendered through Resolve too. `qa_fidelity` classifies each check;
# `segment_renderer.render_batch` renders a pass's composite ranges in one
# batch and ffmpeg cuts the frames out of it.

def _project_2(lie_on=None):
    """A 0-1665 reel; ``lie_on`` is the MarkIn whose job queues the whole
    timeline anyway (the 2026-09-11 shape)."""
    tl = FakeTimeline("T", end_frame=1665)
    project = FakeProject([tl], current=tl)
    project.ignore_render_marks = {lie_on} if lie_on is not None else False
    return tl, project


def test_neighbouring_ranges_merge_and_distant_ones_do_not():
    assert segment_renderer.merge_ranges(
        [(372, 420), (300, 369), (900, 900)], max_gap_frames=5) == [
        (300, 420), (900, 900)]
    assert segment_renderer.merge_ranges([(10, 10), (11, 11)], 0) == [(10, 11)]
    assert segment_renderer.merge_ranges([(10, 10), (12, 12)], 0) == [
        (10, 10), (12, 12)]


def test_a_batch_starts_every_job_together_and_deletes_only_its_own(tmp_path):
    tl, project = _project_2()
    project.render_jobs.append({"JobId": "captain", "MarkIn": 0, "MarkOut": 9})
    segment_renderer.render_batch(
        FakeResolve(project), project, tl, [(300, 300), (310, 340), (2000, 2000)],
        output_dir=str(tmp_path), max_gap_frames=30)
    assert project.started_renders == [["job-2", "job-3"]], (
        "one StartRendering for the whole batch, one job per merged range")
    assert sorted(project.deleted_render_jobs) == ["job-2", "job-3"]
    assert [job["JobId"] for job in project.GetRenderJobList()] == ["captain"]


def test_one_range_that_did_not_take_refuses_the_whole_batch(tmp_path):
    tl, project = _project_2(lie_on=2000)
    batch = segment_renderer.render_batch(
        FakeResolve(project), project, tl, [(300, 300), (2000, 2000)],
        output_dir=str(tmp_path))
    assert project.started_renders == [], "a whole-timeline job must never start"
    assert batch.error and "1665" in batch.error
    assert sorted(project.deleted_render_jobs) == ["job-1", "job-2"]


def _manifest():
    clip = {"source_file": "/media/a.mov", "source_in": 4.0, "source_out": 8.0,
            "timeline_in_frame": 0, "timeline_out_frame": 120}
    card = {"source_file": "/cards/end.mov", "source_in": 0.0, "source_out": 3.0,
            "timeline_in_frame": 120, "timeline_out_frame": 210,
            "bookend": "end_card"}
    return {
        "tracks": {"V1": {"clips": [clip, card]}},
        "transitions": [{"timeline_frame": 118, "duration_frames": 4}],
        "vfx": [{"timeline_in_frame": 40, "timeline_out_frame": 80}],
    }


def test_clip_placement_reads_the_file_and_never_renders(monkeypatch, tmp_path):
    plan = visual_qa_router.plan_qa_checks(_manifest(), phase="post_build")
    by_type = {}
    for g in plan.frame_grabs:
        by_type.setdefault(g.check_type, []).append(g.fidelity)
    assert by_type["clip_placement"] == [SOURCE_PIXEL, GENERATED_ASSET]
    assert by_type["vfx"] == [RESOLVE_COMPOSITE]

    read = []
    monkeypatch.setattr(visual_qa_router, "extract_source_frame",
                        lambda f, t, d=None: read.append((f, t)) or None)
    rendered = []
    monkeypatch.setattr(visual_qa_router, "render_batch",
                        lambda *a, **k: rendered.append(a[3]) or
                        segment_renderer.BatchRenderResult(segments=[
                            segment_renderer.SegmentRenderResult(
                                path="", mark_in=a, mark_out=b,
                                duration_frames=b - a + 1, width=1, height=1,
                                success=False, error="fake")
                            for a, b in segment_renderer.merge_ranges(a[3])]))
    monkeypatch.setattr(visual_qa_router, "_analyze_segment",
                        lambda req, seg, *a, **k: seg)

    out = visual_qa_router.execute_qa_plan(
        None, None, None, plan, extra_frames=[60, 165],
        output_dir=str(tmp_path))

    assert read == [("/media/a.mov", 6.0), ("/cards/end.mov", 1.5)]
    assert len(rendered) == 1, "the whole pass is ONE Resolve render"
    frames = {a for a, b in rendered[0] if a == b}
    assert frames == {60, 165}, (
        "only the composite grab and the extra (perceptual) frames render; "
        "the clip_placement midpoints do not")
    assert (113, 127) in rendered[0], "the transition segment is in the batch"
    assert len(out.frame_results) == len(plan.frame_grabs)
    assert all(r.check.passed is False for r in out.frame_results), (
        "a frame nothing produced is a FAILED check, never a pass")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_frames_cut_from_a_merged_render_are_the_frames_asked_for(tmp_path):
    """Frame 150 of a render of 100-219 is decoded frame 50, exactly."""
    seg_path = tmp_path / "seg.mov"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         "testsrc2=size=160x120:rate=30", "-frames:v", "120",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(seg_path)],
        check=True, capture_output=True)
    seg = segment_renderer.SegmentRenderResult(
        path=str(seg_path), mark_in=100, mark_out=219, duration_frames=120,
        width=160, height=120, success=True)

    got = segment_renderer.extract_frames(seg, [150, 219, 500], str(tmp_path))
    assert set(got) == {150, 219}

    def md5s(path):
        out = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", path, "-pix_fmt", "rgb24",
             "-f", "framemd5", "-"], check=True, capture_output=True,
            encoding="utf-8").stdout
        return [ln.rsplit(",", 1)[1].strip() for ln in out.splitlines()
                if ln and not ln.startswith("#")]

    every = md5s(str(seg_path))
    assert md5s(got[150]) == [every[50]]
    assert md5s(got[219]) == [every[119]]
