import pytest
from unittest.mock import patch, MagicMock

from library.tools.segment_renderer import (
    render_segment
)


# --- Mocks ---
@pytest.fixture
def mock_resolve():
    resolve = MagicMock()
    resolve.GetCurrentPage.return_value = "edit"
    return resolve

@pytest.fixture
def mock_timeline():
    tl = MagicMock()
    tl.GetUniqueId.return_value = "test-timeline-uid"
    tl.GetName.return_value = "Test Timeline"
    return tl

@pytest.fixture
def mock_project(mock_timeline):
    project = MagicMock()
    project.IsRenderingInProgress.side_effect = [True, False]
    project.GetCurrentTimeline.return_value = mock_timeline
    project.AddRenderJob.return_value = "job-1"
    # `render_segment` reads the queue back before starting, because
    # Resolve ignores MarkIn/MarkOut unless SelectAllFrames is False
    # and a "single frame" then renders the whole timeline. A bare
    # MagicMock returns a Mock here, which is not a list of dicts - so
    # the queue is spelled out rather than auto-specced.
    project.GetRenderJobList.return_value = [
        {"JobId": "job-1", "MarkIn": 0, "MarkOut": 10}]
    return project


# --- 1. segment_renderer.py tests ---



@patch("os.makedirs")
@patch("library.tools.segment_renderer._find_rendered_file", return_value="/tmp/qa_segment_0_10.mov")
@patch("os.path.getsize", return_value=100) # Small size
def test_render_segment_small_file(mock_size, mock_find, mock_makedirs, mock_resolve, mock_project, mock_timeline):
    res = render_segment(mock_resolve, mock_project, mock_timeline, 0, 10, output_dir="/tmp")
    assert res.success is False
    assert "too small" in res.error




# --- 2. visual_qa_router.py tests ---









# --- 3. qa_feedback_loop.py tests ---







# --- 4. visual_qa_prompts.py tests ---



