import pytest
from library.tools.audio_reactive_sfx import align_sfx_to_prosody, scale_sfx_density

def test_align_sfx_to_prosody():
    sfx_spec = [
        {"type": "whoosh", "start_time": 1.2},
        {"type": "bass_impact", "start_time": 3.0},
        {"type": "riser", "start_time": 5.0}
    ]
    
    prosody_data = {
        "pauses": [{"start_time": 1.0}],
        "emphasis_peaks": [3.2]
    }
    
    engagement_scores = {
        "6.0": 0.9,
        "7.0": 0.5
    }
    
    result = align_sfx_to_prosody(sfx_spec, prosody_data, engagement_scores)
    
    assert len(result) == 3
    # Whoosh aligns to nearest pause (1.0)
    assert result[0]["start_time"] == 1.0
    # Impact aligns to nearest emphasis peak (3.2)
    assert result[1]["start_time"] == 3.2
    # Riser aligns to end 1.5s before engagement peak (6.0 - 1.5 = 4.5)
    assert result[2]["start_time"] == 4.5

def test_scale_sfx_density():
    sfx_spec = [
        {"type": "whoosh", "start_time": 1.0},
        {"type": "impact", "start_time": 2.0},
        {"type": "impact", "start_time": 3.0},
        {"type": "ambient", "start_time": 4.0}
    ]
    
    # High: keeps all
    result = scale_sfx_density(sfx_spec, "high")
    assert len(result) == 4
    
    # Moderate: keeps half impacts
    result = scale_sfx_density(sfx_spec, "moderate")
    assert len(result) == 3
    assert [s["start_time"] for s in result] == [1.0, 2.0, 4.0]
    
    # Calm: removes impacts
    result = scale_sfx_density(sfx_spec, "calm")
    assert len(result) == 2
    assert [s["start_time"] for s in result] == [1.0, 4.0]
