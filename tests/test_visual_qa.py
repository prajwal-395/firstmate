import pytest
import os
import json
from unittest.mock import patch, MagicMock, mock_open

from library.tools.segment_renderer import (
    render_segment, render_single_frame, cleanup_segment, SegmentRenderResult
)
from library.tools.visual_qa_router import (
    frame_to_timecode, plan_qa_checks, prepare_frame_grab,
    execute_frame_grab, execute_video_segment_check, parse_qa_response,
    format_frame_grab_for_llm, format_segment_result_for_llm, format_qa_plan_for_llm,
    QAPassPlan, FrameGrabRequest, VideoSegmentRequest, FrameGrabResult, VideoSegmentResult
)
from library.tools.qa_feedback_loop import (
    QAFeedbackConfig, QAFeedbackLoop, FeedbackIteration, FeedbackLoopResult
)
from library.tools.visual_qa_prompts import (
    frame_grab_inline_prompt, segment_analysis_prompt, adjustment_suggestion_prompt
)
from library.tools.timeline_qa import VisualQACheck


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
    return project


# --- 1. segment_renderer.py tests ---

@patch("os.makedirs")
@patch("os.listdir", return_value=["qa_segment_0_10.mov"])
@patch("os.path.getsize", return_value=5000)
@patch("library.tools.segment_renderer._find_rendered_file", return_value="/tmp/qa_segment_0_10.mov")
def test_render_segment_success(mock_find, mock_size, mock_listdir, mock_makedirs, mock_resolve, mock_project, mock_timeline):
    res = render_segment(mock_resolve, mock_project, mock_timeline, 0, 10, output_dir="/tmp")
    assert res.success is True
    assert res.path == "/tmp/qa_segment_0_10.mov"
    assert res.duration_frames == 11
    mock_project.AddRenderJob.assert_called_once()
    mock_project.StartRendering.assert_called_once()

@patch("os.makedirs")
def test_render_segment_timeout(mock_makedirs, mock_resolve, mock_project, mock_timeline):
    # Make it always rendering to trigger timeout
    mock_project.IsRenderingInProgress.side_effect = [True] * 5
    res = render_segment(mock_resolve, mock_project, mock_timeline, 0, 10, output_dir="/tmp", timeout=1)
    assert res.success is False
    assert "timed out" in res.error
    mock_project.StopRendering.assert_called_once()

@patch("os.makedirs")
@patch("library.tools.segment_renderer._find_rendered_file", return_value="/tmp/qa_segment_0_10.mov")
@patch("os.path.getsize", return_value=100) # Small size
def test_render_segment_small_file(mock_size, mock_find, mock_makedirs, mock_resolve, mock_project, mock_timeline):
    res = render_segment(mock_resolve, mock_project, mock_timeline, 0, 10, output_dir="/tmp")
    assert res.success is False
    assert "too small" in res.error

@patch("library.tools.segment_renderer.render_segment")
@patch("subprocess.run")
@patch("os.path.exists", return_value=True)
@patch("os.path.getsize", return_value=5000)
def test_render_single_frame(mock_size, mock_exists, mock_run, mock_render, mock_resolve, mock_project, mock_timeline):
    mock_render.return_value = SegmentRenderResult(
        path="/tmp/frame_10.mov", mark_in=10, mark_out=10, duration_frames=1, width=1080, height=1920, success=True
    )
    png_path = render_single_frame(mock_resolve, mock_project, mock_timeline, 10, output_dir="/tmp")
    assert png_path == "/tmp/frame_10.png"
    mock_run.assert_called_once()

@patch("os.remove")
@patch("os.path.exists", return_value=True)
def test_cleanup_segment(mock_exists, mock_remove):
    cleanup_segment("/tmp/test.mov")
    mock_remove.assert_called_once_with("/tmp/test.mov")


# --- 2. visual_qa_router.py tests ---

def test_frame_to_timecode():
    assert frame_to_timecode(0, 30.0) == "00:00:00:00"
    assert frame_to_timecode(30, 30.0) == "00:00:01:00"
    assert frame_to_timecode(3600 * 30 + 60 * 30 + 15, 30.0) == "01:01:00:15"

def test_plan_qa_checks():
    manifest = {
        "clips": [{"timeline_in_frame": 0, "timeline_out_frame": 60, "clip_name": "clip1", "source_file": "clip1"}],
        "transitions": [{"frame": 60, "duration_frames": 30, "type": "cross_dissolve"}],
        "vfx": [{"start_frame": 10, "end_frame": 40, "type": "blur"}],
        "color_grade": {"per_clip_adjustments": [{"source_file": "clip1", "intended_look": "warm"}]},
        "subtitles": [{"start_frame": 5, "text": "Hello"}]
    }
    
    # Test post_build phase (tests all except specific phases logic)
    plan = plan_qa_checks(manifest, phase="post_build")
    
    # clips (1) + transitions (1) + vfx (1) + color (1) + subtitles (1) = 4 frame grabs, 1 segment check
    assert len(plan.frame_grabs) == 4
    assert len(plan.segment_checks) == 1
    assert plan.segment_checks[0].check_type == "transition"

def test_prepare_frame_grab():
    req = prepare_frame_grab(15, "color_grade", {"intended_look": "cool"})
    assert req.frame_number == 15
    assert req.timecode == "00:00:00:15"
    assert req.check_type == "color_grade"
    assert "cool" in req.prompt

@patch("library.tools.visual_qa_router.render_single_frame", return_value="/tmp/test.png")
def test_execute_frame_grab(mock_render, mock_resolve, mock_project, mock_timeline):
    req = FrameGrabRequest(frame_number=10, timecode="00:00:00:10", check_type="color_grade", prompt="", context={})
    
    with patch("builtins.open", mock_open(read_data=b"image_data")):
        res = execute_frame_grab(mock_resolve, mock_project, mock_timeline, req)
        assert res.check.passed is True
        assert res.base64_image == "aW1hZ2VfZGF0YQ==" # base64 of "image_data"
        assert res.image_path == "/tmp/test.png"

@patch("library.tools.visual_qa_router.render_segment")
@patch("library.tools.video_segment_analyzer.run_analysis")
@patch("library.tools.visual_qa_router.cleanup_segment")
def test_execute_video_segment_check(mock_cleanup, mock_analyze, mock_render, mock_resolve, mock_project, mock_timeline):
    mock_render.return_value = SegmentRenderResult(
        path="/tmp/seg.mov", mark_in=0, mark_out=30, duration_frames=31, width=720, height=1280, success=True
    )
    mock_analyze.return_value = {
        "deterministic": {"black_frames": {"passed": True}},
        "vision_analysis": {"passed": True, "confidence": 0.9, "detail": "Looks good", "issues": []}
    }
    
    req = VideoSegmentRequest(mark_in=0, mark_out=30, check_type="transition", prompt="", context={})
    res = execute_video_segment_check(mock_resolve, mock_project, mock_timeline, req, cleanup=True)
    
    assert res.check.passed is True
    assert res.check.confidence == 0.9
    mock_cleanup.assert_called_once()

def test_parse_qa_response():
    # Plain text
    res1 = parse_qa_response("Everything looks good here.")
    assert res1["passed"] is True
    
    # JSON
    res2 = parse_qa_response('{"passed": false, "confidence": 0.8, "detail": "bad", "issues": ["too dark"]}')
    assert res2["passed"] is False
    assert res2["issues"] == ["too dark"]
    
    # Markdown-fenced JSON
    res3 = parse_qa_response('```json\n{"passed": true, "confidence": 0.95, "detail": "ok", "issues": []}\n```')
    assert res3["passed"] is True
    assert res3["confidence"] == 0.95

def test_format_for_llm():
    # FrameGrab
    check = VisualQACheck(name="test", passed=True, expected="test", actual="test", model_used="test", confidence=0.9, frame_timecode="00:00:00:00", detail="ok", issues=[])
    fg_res = FrameGrabResult(check=check, base64_image="abc")
    fg_dict = format_frame_grab_for_llm(fg_res)
    assert fg_dict["type"] == "frame_grab_result"
    assert fg_dict["has_image"] is True
    
    # Segment
    seg_res = VideoSegmentResult(check=check, deterministic={"lufs": {"passed": True}})
    seg_dict = format_segment_result_for_llm(seg_res)
    assert seg_dict["type"] == "video_segment_result"
    assert seg_dict["deterministic_checks"]["lufs"]["passed"] is True


# --- 3. qa_feedback_loop.py tests ---

def test_qa_feedback_config():
    manifest = {"visual_qa": {"max_retries": 5, "video_segment_enabled": False}}
    config = QAFeedbackConfig.from_manifest(manifest)
    assert config.max_retries == 5
    assert config.video_segment_enabled is False
    assert config.frame_grab_enabled is True

@patch("library.tools.qa_feedback_loop.execute_frame_grab")
@patch("library.tools.qa_feedback_loop.analyze_frame_locally")
def test_qa_feedback_loop_run(mock_analyze, mock_execute, mock_resolve, mock_project, mock_timeline):
    config = QAFeedbackConfig(max_retries=2, video_segment_enabled=False)
    loop = QAFeedbackLoop(mock_resolve, mock_project, mock_timeline, config)
    
    req = FrameGrabRequest(frame_number=10, timecode="00:00:00:10", check_type="color_grade", prompt="", context={})
    plan = QAPassPlan(frame_grabs=[req])
    
    mock_execute.return_value = FrameGrabResult(
        check=VisualQACheck(name="test", passed=True, expected="", actual="", model_used="", confidence=0.9, frame_timecode="00:00:00:00", detail="ok", issues=[]),
        image_path="/tmp/test.png"
    )
    mock_analyze.return_value = VisualQACheck(name="test", passed=True, confidence=0.9, expected="", actual="", model_used="", frame_timecode="00:00:00:00", detail="ok", issues=[])
    
    res = loop.run(plan)
    assert res.passed is True
    assert res.total_attempts == 1
    assert len(res.iterations) == 1

@patch("library.tools.qa_feedback_loop.execute_frame_grab")
@patch("library.tools.qa_feedback_loop.analyze_frame_locally")
def test_qa_feedback_loop_retry(mock_analyze, mock_execute, mock_resolve, mock_project, mock_timeline):
    config = QAFeedbackConfig(max_retries=3, video_segment_enabled=False)
    loop = QAFeedbackLoop(mock_resolve, mock_project, mock_timeline, config)
    
    req = FrameGrabRequest(frame_number=10, timecode="00:00:00:10", check_type="color_grade", prompt="", context={})
    plan = QAPassPlan(frame_grabs=[req])
    
    mock_execute.return_value = FrameGrabResult(
        check=VisualQACheck(name="test", passed=True, expected="", actual="", model_used="", confidence=0.9, frame_timecode="00:00:00:00", detail="ok", issues=[]),
        image_path="/tmp/test.png"
    )
    
    # Fail first time, pass second time
    mock_analyze.side_effect = [
        VisualQACheck(name="test", passed=False, confidence=0.9, expected="", actual="", model_used="", issues=["too dark"], frame_timecode="00:00:00:00", detail="fail"),
        VisualQACheck(name="test", passed=True, confidence=0.9, expected="", actual="", model_used="", issues=[], frame_timecode="00:00:00:00", detail="ok")
    ]
    
    callbacks = []
    def on_adj(prompt, ctx):
        callbacks.append(prompt)
        
    res = loop.run(plan, on_adjustment=on_adj)
    
    assert res.passed is True
    assert res.total_attempts == 2
    assert len(res.iterations) == 2
    assert len(callbacks) == 1
    assert "too dark" in callbacks[0]

@patch("library.tools.qa_feedback_loop.QAFeedbackLoop._run_frame_grab_loop", return_value=True)
def test_run_single_check(mock_run, mock_resolve, mock_project, mock_timeline):
    loop = QAFeedbackLoop(mock_resolve, mock_project, mock_timeline)
    res = loop.run_single_check(10, "color_grade", {})
    assert res.passed is True
    mock_run.assert_called_once()

def test_evaluate():
    loop = QAFeedbackLoop(None, None, None, QAFeedbackConfig(min_confidence=0.8))
    
    # Fail -> retry
    check_fail = VisualQACheck(name="", passed=False, expected="", actual="", model_used="", issues=["err"], confidence=0.0, frame_timecode="00:00", detail="fail")
    passed, retry = loop._evaluate(check_fail)
    assert not passed and retry
    
    # Pass low conf -> retry
    check_low = VisualQACheck(name="", passed=True, confidence=0.5, expected="", actual="", model_used="", issues=[], frame_timecode="00:00", detail="ok")
    passed, retry = loop._evaluate(check_low)
    assert not passed and retry
    
    # Pass high conf -> no retry
    check_high = VisualQACheck(name="", passed=True, confidence=0.9, expected="", actual="", model_used="", issues=[], frame_timecode="00:00", detail="ok")
    passed, retry = loop._evaluate(check_high)
    assert passed and not retry


# --- 4. visual_qa_prompts.py tests ---

def test_frame_grab_inline_prompt():
    prompt = frame_grab_inline_prompt("color_grade", {"clip_name": "test.mp4", "intended_look": "warm"})
    assert "color grading" in prompt
    assert "test.mp4" in prompt
    assert "warm" in prompt
    assert "JSON object" in prompt

def test_segment_analysis_prompt():
    prompt = segment_analysis_prompt("transition", {"transition_type": "fade", "duration_seconds": 2.0})
    assert "transition effect" in prompt
    assert "fade" in prompt
    assert "2.00s" in prompt
    assert "temporal consistency" in prompt

def test_adjustment_suggestion_prompt():
    prompt = adjustment_suggestion_prompt("color_grade", ["too bright"], 1, 3)
    assert "FAILED (attempt 1/3)" in prompt
    assert "too bright" in prompt
    assert "suggest specific adjustments" in prompt
