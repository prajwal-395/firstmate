import pytest
from library.tools.audio_ducker import compute_ducking_curves, compute_sfx_ducking

def test_compute_ducking_curves_empty():
    keyframes = compute_ducking_curves([], 10.0)
    assert len(keyframes) == 1
    assert keyframes[0]["time_ms"] == 0
    assert keyframes[0]["volume_db"] == -12.0

def test_compute_ducking_curves_with_speech():
    segments = [
        {"start_time": 1.0, "end_time": 2.0},
        {"start_time": 4.0, "end_time": 5.0}
    ]
    keyframes = compute_ducking_curves(segments, 10.0)
    
    # 0 ms: -12
    # 800 ms: -12 (ramp down starts)
    # 1000 ms: -18 (speech starts)
    # 2000 ms: -18 (speech ends)
    # 2500 ms: -12 (ramp up ends)
    # 3800 ms: -12 (ramp down starts)
    # 4000 ms: -18 (speech starts)
    # 5000 ms: -18 (speech ends)
    # 5500 ms: -12 (ramp up ends)
    
    expected_times = [0, 800, 1000, 2000, 2500, 3800, 4000, 5000, 5500]
    expected_vols = [-12.0, -12.0, -18.0, -18.0, -12.0, -12.0, -18.0, -18.0, -12.0]
    
    assert len(keyframes) == len(expected_times)
    for i, kf in enumerate(keyframes):
        assert kf["time_ms"] == expected_times[i]
        assert kf["volume_db"] == expected_vols[i]

def test_compute_sfx_ducking():
    sfx = [
        {"start_time": 0.5, "end_time": 0.8, "volume_db": -10.0},
        {"start_time": 1.5, "end_time": 2.5, "volume_db": -5.0}, # Overlaps with 2.0-3.0
    ]
    speech = [
        {"start_time": 2.0, "end_time": 3.0}
    ]
    
    result = compute_sfx_ducking(sfx, speech)
    assert len(result) == 2
    assert result[0]["volume_db"] == -10.0 # No overlap
    assert result[1]["volume_db"] == -11.0 # Ducked by 6dB
