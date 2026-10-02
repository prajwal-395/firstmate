from unittest.mock import patch, MagicMock
from library.tools.render_qa import (
    measure_lufs,
    detect_black_frames,
    detect_freeze_frames,
    verify_resolution,
    verify_framerate,
    verify_duration
)

@patch('subprocess.run')
def test_measure_lufs_fails_off_target_and_judges_the_declared_one(mock_run):
    mock_run.return_value = MagicMock(stderr='{\n"input_i": "-20.0",\n"input_tp": "-1.5"\n}', returncode=0)
    res = measure_lufs("dummy.mp4")
    assert not res.passed
    assert res.severity == "error"
    # An explicit -16 LUFS / -1 dBTP request is judged as declared.
    mock_run.return_value = MagicMock(
        stderr='{\n"input_i": "-16.0",\n"input_tp": "-1.5"\n}',
        returncode=0)
    result = measure_lufs(
        "dummy.mp4", target_lufs=-16.0, true_peak_ceiling=-1.0)
    assert result.passed
    assert result.threshold == {
        "target_lufs": -16.0, "tolerance": 1.0,
        "true_peak_ceiling": -1.0}


@patch('subprocess.run')
def test_black_and_freeze_frames_are_parsed_and_fail(mock_run):
    mock_run.return_value = MagicMock(stderr='[blackdetect @ 0x123] black_start:1.5 black_end:2.5 black_duration:1.0', returncode=0)
    res = detect_black_frames("dummy.mp4")
    assert not res.passed
    assert len(res.value) == 1
    assert res.value[0]["duration"] == 1.0
    mock_run.return_value = MagicMock(stderr=(
        "lavfi.freezedetect.freeze_start: 3.0\n"
        "lavfi.freezedetect.freeze_duration: 2.0\n"
        "lavfi.freezedetect.freeze_end: 5.0"), returncode=0)
    res = detect_freeze_frames("dummy.mp4")
    assert not res.passed
    assert len(res.value) == 1
    assert (res.value[0]["start"], res.value[0]["duration"],
            res.value[0]["end"]) == (3.0, 2.0, 5.0)


@patch('subprocess.run')
def test_resolution_framerate_and_duration_are_read_off_the_file(mock_run):
    mock_run.return_value = MagicMock(stdout='{"streams": [{"width": 1920, "height": 1080}]}', returncode=0)
    assert not verify_resolution("dummy.mp4", 1080, 1920).passed
    mock_run.return_value = MagicMock(stdout='{"streams": [{"r_frame_rate": "30000/1000"}]}', returncode=0)
    res = verify_framerate("dummy.mp4")
    assert res.passed
    assert res.value == 30.0
    mock_run.return_value = MagicMock(stdout='{"format": {"duration": "30.0"}}', returncode=0)
    assert verify_duration("dummy.mp4", expected_seconds=30.0).passed


def test_bar_rows_fully_masked_run_letterbox():
    from library.tools.render_qa import _bar_rows
    import numpy as np
    
    # 10 rows. 
    # rows 0,1,2: bar (mean 0)
    # rows 3,4,5: unreadable (masked by overlay)
    # rows 6,7,8,9: picture (mean 50)
    
    row_mean = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 50.0, 50.0, 50.0, 50.0])
    row_std = np.zeros(10)
    readable = np.array([True, True, True, False, False, False, True, True, True, True])
    
    # Walk starts at 0.
    # 0,1,2 are bar.
    # 3,4,5 are unreadable.
    # 6 breaks (picture).
    # Since previous is not None (it saw 0,1,2), the unreadable run is bounded by bar on outer side.
    # It should return i=6, treating the unreadable rows as bar.
    assert _bar_rows(row_mean, row_std, readable=readable) == 6
