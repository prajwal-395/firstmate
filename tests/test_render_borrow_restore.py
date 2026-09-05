"""H4 - render settings and the render queue are project-global.

Neither renderer used to restore them. A render that changes project-
global settings and leaves them changed means the NEXT render inherits
them, and the captain's own manual render inherits them too.

These tests prove:
- format/codec is saved before mutation and restored in finally
- only the job this process created is deleted (not DeleteAllRenderJobs)
- restore happens on the success path
- restore happens when the body RAISES (the path that is always missing)
- the page is restored on both paths
- RenderSettingsError is importable and documents the hazard

All tests use mock objects - no Resolve writes.
"""
import os
import sys

import pytest

# ── segment_renderer ────────────────────────────────────────────
from library.tools.segment_renderer import (
    RenderSettingsError,
    render_segment,
    SegmentRenderResult,
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

    def GetCurrentRenderFormatAndCodec(self):
        return dict(self._format_codec)

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        self.set_format_codec_calls.append((fmt, codec))
        return True

    def SetRenderSettings(self, settings):
        self.set_render_settings_calls.append(dict(settings))
        return True

    def AddRenderJob(self):
        return self._job_id

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

class TestSegmentRendererFormatCodecRestore:
    """format/codec is saved before mutation and restored on every exit."""

    def test_format_codec_restored_on_success(self, tmp_path):
        """The format/codec is restored after a successful render."""
        resolve = MockResolve()
        project = MockProject(format_codec={"format": "mov", "codec": "ProRes"})
        timeline = MockTimeline()

        # Create a fake rendered file so the function finds it
        fake_file = tmp_path / "qa_segment_0_10.mov"
        fake_file.write_bytes(b"\x00" * 4096)

        render_segment(
            resolve, project, timeline,
            mark_in=0, mark_out=10,
            output_dir=str(tmp_path),
        )

        assert project.set_format_codec_calls == [("mov", "ProRes")], (
            "format/codec was not restored after successful render"
        )

    def test_format_codec_restored_when_body_raises(self, tmp_path):
        """The format/codec is restored even when the render body raises."""
        resolve = MockResolve()
        project = ExplodingProject(
            format_codec={"format": "mp4", "codec": "H264"},
        )
        timeline = MockTimeline()

        with pytest.raises(RuntimeError, match="Simulated render explosion"):
            render_segment(
                resolve, project, timeline,
                mark_in=0, mark_out=10,
                output_dir=str(tmp_path),
            )

        assert project.set_format_codec_calls == [("mp4", "H264")], (
            "format/codec was NOT restored when the body raised - "
            "this is the bug H4 exists to prevent"
        )


class TestSegmentRendererJobCleanup:
    """Only the job this process created is deleted, not everyone's."""

    def test_own_job_deleted_on_success(self, tmp_path):
        """Our render job is cleaned up after success."""
        resolve = MockResolve()
        project = MockProject(job_id="my-job-99")
        timeline = MockTimeline()

        fake_file = tmp_path / "qa_segment_0_10.mov"
        fake_file.write_bytes(b"\x00" * 4096)

        render_segment(
            resolve, project, timeline,
            mark_in=0, mark_out=10,
            output_dir=str(tmp_path),
        )

        assert "my-job-99" in project.deleted_jobs
        assert not project.deleted_all, (
            "DeleteAllRenderJobs was called - this destroys the captain's "
            "queued jobs (H4a)"
        )

    def test_own_job_deleted_when_body_raises(self, tmp_path):
        """Our render job is cleaned up even when the body raises."""
        resolve = MockResolve()
        project = ExplodingProject(job_id="my-job-77")
        timeline = MockTimeline()

        with pytest.raises(RuntimeError, match="Simulated render explosion"):
            render_segment(
                resolve, project, timeline,
                mark_in=0, mark_out=10,
                output_dir=str(tmp_path),
            )

        assert "my-job-77" in project.deleted_jobs
        assert not project.deleted_all

    def test_delete_all_render_jobs_never_called(self, tmp_path):
        """DeleteAllRenderJobs is never called - it destroys others' jobs."""
        resolve = MockResolve()
        project = MockProject()
        timeline = MockTimeline()

        fake_file = tmp_path / "qa_segment_0_10.mov"
        fake_file.write_bytes(b"\x00" * 4096)

        render_segment(
            resolve, project, timeline,
            mark_in=0, mark_out=10,
            output_dir=str(tmp_path),
        )

        assert not project.deleted_all, (
            "DeleteAllRenderJobs was called. This is H4a: it deletes the "
            "captain's queued render jobs and any concurrent process's jobs."
        )


class TestSegmentRendererPageRestore:
    """The Deliver page is restored on every exit path."""

    def test_page_restored_on_success(self, tmp_path):
        resolve = MockResolve(page="edit")
        project = MockProject()
        timeline = MockTimeline()

        fake_file = tmp_path / "qa_segment_0_10.mov"
        fake_file.write_bytes(b"\x00" * 4096)

        render_segment(
            resolve, project, timeline,
            mark_in=0, mark_out=10,
            output_dir=str(tmp_path),
        )

        assert resolve.opened_pages[-1] == "edit", (
            "Page was not restored after render"
        )

    def test_page_restored_when_body_raises(self, tmp_path):
        resolve = MockResolve(page="color")
        project = ExplodingProject()
        timeline = MockTimeline()

        with pytest.raises(RuntimeError):
            render_segment(
                resolve, project, timeline,
                mark_in=0, mark_out=10,
                output_dir=str(tmp_path),
            )

        assert resolve.opened_pages[-1] == "color", (
            "Page was not restored when the body raised"
        )


class TestRenderSettingsErrorDocumentation:
    """The error class documents the hazard."""

    def test_render_settings_error_exists(self):
        assert issubclass(RenderSettingsError, RuntimeError)

    def test_render_settings_error_docstring_mentions_h4(self):
        assert "H4" in RenderSettingsError.__doc__

    def test_render_settings_error_docstring_mentions_no_get(self):
        assert "GetRenderSettings" in RenderSettingsError.__doc__


# ── resolve_render (the full-timeline renderer) ────────────────
#
# render_timeline calls _connect() internally, so we monkeypatch it
# to inject our mocks.

from library.tools.execution.resolve_render import (
    RenderError,
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

    def test_format_codec_restored_when_body_raises(self, monkeypatch, tmp_path):
        """format/codec is restored even when SetRenderSettings raises."""
        project = ExplodingRenderProject(
            format_codec={"format": "mov", "codec": "ProRes"},
        )
        timeline = MockTimeline("TestTimeline")
        resolve = RenderTimelineMockResolve(project, page="edit")

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

    def test_page_restored_when_body_raises(self, monkeypatch, tmp_path):
        """The page is restored even when the body raises."""
        project = ExplodingRenderProject(
            format_codec={"format": "mov", "codec": "ProRes"},
        )
        timeline = MockTimeline("TestTimeline")
        resolve = RenderTimelineMockResolve(project, page="color")

        monkeypatch.setattr(
            "library.tools.execution.resolve_render._connect",
            lambda: resolve,
        )

        with pytest.raises(RuntimeError):
            render_timeline(
                timeline_name="TestTimeline",
                output_dir=str(tmp_path),
                output_name="test",
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
        timeline = MockTimeline("TestTimeline")
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
