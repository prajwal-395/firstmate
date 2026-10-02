"""H4 - render settings and the render queue are project-global.

History: docs/evidence/resolve_test_history.md#test_render_borrow_restore.
"""

import pytest

# ── segment_renderer ────────────────────────────────────────────
from library.tools.segment_renderer import (
    render_segment,
)


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
