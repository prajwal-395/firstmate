import pytest
from unittest.mock import patch, MagicMock
from library.tools.render_qa import (
    measure_lufs,
    detect_black_frames,
    detect_freeze_frames,
    analyze_color_histogram,
    verify_resolution,
    verify_framerate,
    verify_duration,
    run_full_render_qa
)

@patch('subprocess.run')
def test_measure_lufs(mock_run):
    mock_run.return_value = MagicMock(stderr='{\n"input_i": "-14.5",\n"input_tp": "-1.5"\n}', returncode=0)
    res = measure_lufs("dummy.mp4")
    assert res.passed
    assert res.value["input_i"] == -14.5
    assert res.value["input_tp"] == -1.5

@patch('subprocess.run')
def test_measure_lufs_fail(mock_run):
    mock_run.return_value = MagicMock(stderr='{\n"input_i": "-20.0",\n"input_tp": "-1.5"\n}', returncode=0)
    res = measure_lufs("dummy.mp4")
    assert not res.passed
    assert res.severity == "error"

@patch('subprocess.run')
def test_detect_black_frames(mock_run):
    mock_run.return_value = MagicMock(stderr='[blackdetect @ 0x123] black_start:1.5 black_end:2.5 black_duration:1.0', returncode=0)
    res = detect_black_frames("dummy.mp4")
    assert not res.passed
    assert len(res.value) == 1
    assert res.value[0]["duration"] == 1.0

@patch('subprocess.run')
def test_detect_black_frames_pass(mock_run):
    mock_run.return_value = MagicMock(stderr='', returncode=0)
    res = detect_black_frames("dummy.mp4")
    assert res.passed

@patch('subprocess.run')
def test_detect_freeze_frames(mock_run):
    stderr_out = '''lavfi.freezedetect.freeze_start: 3.0
lavfi.freezedetect.freeze_duration: 2.0
lavfi.freezedetect.freeze_end: 5.0'''
    mock_run.return_value = MagicMock(stderr=stderr_out, returncode=0)
    res = detect_freeze_frames("dummy.mp4")
    assert not res.passed
    assert len(res.value) == 1
    assert res.value[0]["start"] == 3.0
    assert res.value[0]["duration"] == 2.0
    assert res.value[0]["end"] == 5.0

@patch('subprocess.run')
def test_analyze_color_histogram(mock_run):
    # Mocking multiple subprocess calls for duration, ffmpeg, ffprobe
    def side_effect(cmd, **kwargs):
        if 'format=duration' in cmd:
            return MagicMock(stdout='10.0\n', returncode=0)
        elif 'ffmpeg' in cmd[0]:
            return MagicMock(returncode=0)
        elif any('signalstats' in arg for arg in cmd):
            # Return some mock json with tags
            out = '''{"frames": [{"tags": {"lavfi.signalstats.YAVG": "128", "lavfi.signalstats.SATAVG": "50"}}]}'''
            return MagicMock(stdout=out, returncode=0)
        return MagicMock(returncode=0)
        
    mock_run.side_effect = side_effect
    
    with patch('os.path.exists', return_value=True):
        res = analyze_color_histogram("dummy.mp4")
        assert res.passed
        assert len(res.value) == 5
        assert res.value[0]["yavg"] == 128.0

@patch('subprocess.run')
def test_verify_resolution(mock_run):
    mock_run.return_value = MagicMock(stdout='{"streams": [{"width": 1080, "height": 1920}]}', returncode=0)
    res = verify_resolution("dummy.mp4")
    assert res.passed

@patch('subprocess.run')
def test_verify_resolution_fail(mock_run):
    mock_run.return_value = MagicMock(stdout='{"streams": [{"width": 1920, "height": 1080}]}', returncode=0)
    res = verify_resolution("dummy.mp4")
    assert not res.passed

@patch('subprocess.run')
def test_verify_framerate(mock_run):
    mock_run.return_value = MagicMock(stdout='{"streams": [{"r_frame_rate": "30000/1000"}]}', returncode=0)
    res = verify_framerate("dummy.mp4")
    assert res.passed
    assert res.value == 30.0

@patch('subprocess.run')
def test_verify_duration(mock_run):
    mock_run.return_value = MagicMock(stdout='{"format": {"duration": "30.0"}}', returncode=0)
    res = verify_duration("dummy.mp4", expected_seconds=30.0)
    assert res.passed

@patch('library.tools.render_qa.measure_lufs')
@patch('library.tools.render_qa.detect_black_frames')
@patch('library.tools.render_qa.detect_freeze_frames')
@patch('library.tools.render_qa.analyze_color_histogram')
@patch('library.tools.render_qa.verify_resolution')
@patch('library.tools.render_qa.verify_framerate')
@patch('library.tools.render_qa.verify_duration')
@patch('library.tools.render_qa.verify_audio_streams')
def test_run_full_render_qa(m1, m2, m3, m4, m5, m6, m7, m8):
    results = run_full_render_qa("dummy.mp4", 30.0)
    assert len(results) == 8
