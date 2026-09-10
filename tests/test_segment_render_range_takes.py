"""A one-frame render must render ONE frame, and prove it before starting.

Measured 2026-09-11 on `Podcast (field test)` / Reel 09.  Asking
`render_single_frame` for frame 300 queued a job with MarkIn 0 and
MarkOut 1665 and rendered **1,471 TIFFs of the entire reel** before it
was stopped by hand.  `SetRenderSettings` returned True, `AddRenderJob`
returned a job id, and neither was a lie about anything except the one
thing that mattered: Resolve ignores MarkIn/MarkOut unless
`SelectAllFrames` is also set False.

That is AGENTS.md 5 arriving from a new direction.  The rule there is
that a Resolve call is judged by what it RETURNS - but both returns
here were fine.  What was never read back was the QUEUE, which is where
the range either took or did not.

The cost of not reading it is not a slow check.  The captain's machine
is the fleet's one hard CPU limiter, and a "single frame" that renders
a whole timeline is an outage on it.  So the range is read back off
`GetRenderJobList` and a mismatch refuses BEFORE `StartRendering`.
"""
import pytest

from library.tools import segment_renderer


class FakeTimeline:
    def GetName(self):
        return "T"

    def GetUniqueId(self):
        return "tl-1"


class FakeResolve:
    def __init__(self):
        self.pages = []

    def GetCurrentPage(self):
        return "edit"

    def OpenPage(self, page):
        self.pages.append(page)
        return True


class FakeProject:
    """Records what it was asked, and what it would have started."""

    def __init__(self, queued_in, queued_out, timeline):
        self.queued_in, self.queued_out = queued_in, queued_out
        self.settings = None
        self.started = []
        self.deleted = []
        self._timeline = timeline

    # -- render settings -------------------------------------------
    def GetCurrentRenderFormatAndCodec(self):
        return {"format": "mov", "codec": "H.264"}

    def SetCurrentRenderFormatAndCodec(self, fmt, codec):
        return True

    def SetRenderSettings(self, settings):
        self.settings = dict(settings)
        return True

    def AddRenderJob(self):
        return "job-1"

    def GetRenderJobList(self):
        return [{"JobId": "job-1",
                 "MarkIn": self.queued_in,
                 "MarkOut": self.queued_out}]

    def StartRendering(self, jobs, isInteractiveMode=False):
        self.started.append(list(jobs))
        return True

    def IsRenderingInProgress(self):
        return False

    def DeleteRenderJob(self, job_id):
        self.deleted.append(job_id)
        return True

    # -- timeline ---------------------------------------------------
    def SetCurrentTimeline(self, timeline):
        self._timeline = timeline
        return True

    def GetCurrentTimeline(self):
        return self._timeline


def _render(tmp_path, queued_in, queued_out):
    tl = FakeTimeline()
    project = FakeProject(queued_in, queued_out, tl)
    result = segment_renderer.render_segment(
        FakeResolve(), project, tl,
        mark_in=300, mark_out=300,
        output_dir=str(tmp_path), resolution="full",
        custom_name="frame_300")
    return project, result


def test_the_range_is_asked_for_with_select_all_frames_off(tmp_path):
    """Without this key Resolve silently renders the whole timeline."""
    project, _ = _render(tmp_path, 300, 300)
    assert project.settings is not None
    assert project.settings.get("SelectAllFrames") is False, (
        "SetRenderSettings was called without `SelectAllFrames: False`, "
        "so Resolve ignores MarkIn/MarkOut and queues the whole "
        "timeline. This is the 1,471-TIFF render of 2026-09-11.")
    assert project.settings["MarkIn"] == 300
    assert project.settings["MarkOut"] == 300


def test_a_range_that_did_not_take_refuses_before_rendering(tmp_path):
    """The whole-timeline case: queued 0-1665 for a request of 300."""
    project, result = _render(tmp_path, 0, 1665)
    assert result.success is False
    assert "0" in result.error and "1665" in result.error, result.error
    assert project.started == [], (
        "render_segment started a render of a range nobody asked for - "
        "1,666 frames instead of 1. The queue is read back precisely so "
        "this refuses before any frame is rendered.")
    assert project.deleted == ["job-1"], (
        "the refused job was left in the captain's render queue")


def test_a_job_that_never_reached_the_queue_refuses(tmp_path):
    """AddRenderJob returning an id is not evidence of a queued job."""
    tl = FakeTimeline()
    project = FakeProject(300, 300, tl)
    project.GetRenderJobList = lambda: []
    result = segment_renderer.render_segment(
        FakeResolve(), project, tl, mark_in=300, mark_out=300,
        output_dir=str(tmp_path), resolution="full", custom_name="f")
    assert result.success is False
    assert project.started == []


def test_a_range_that_took_is_allowed_to_render(tmp_path):
    """The guard must not refuse the correct case - it would be a gate
    that fails correct output (AGENTS.md 10.4)."""
    project, result = _render(tmp_path, 300, 300)
    assert project.started == [["job-1"]], (
        "the render never started even though Resolve queued exactly "
        "the frame that was asked for")
    # No file was ever written by these fakes, so the result reports
    # the MISSING FILE - not a refused range.
    assert result.success is False
    assert "not found" in (result.error or "").lower(), result.error
