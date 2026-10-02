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
from library.tools import segment_renderer
from tests.resolve_double import FakeProject, FakeResolve, FakeTimeline


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
