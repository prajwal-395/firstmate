import pytest
from library.tools.audio_ducker import compute_sfx_ducking

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
