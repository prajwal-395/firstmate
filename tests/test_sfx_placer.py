import pytest
import sys
from unittest.mock import MagicMock, patch

# Mock heavy dependencies before importing
sys.modules['librosa'] = MagicMock()
sys.modules['librosa.onset'] = MagicMock()
sys.modules['librosa.feature'] = MagicMock()

from library.tools.execution.sfx_placer import (
    snap_to_beat,
    _parse_volume_db,
    _db_to_gain,
    score_sfx_for_context,
    pick_best_sfx,
    _apply_volume_to_placed_clip,
    plan_sfx_placement
)

@pytest.fixture
def sample_sfx():
    return {
        "file": "test.wav",
        "path": "/path/to/test.wav",
        "duration_s": 1.0,
        "peak_s": 0.5,
        "recognition_risk": "low",
        "abstractness": 0.8,
        "style_tags": ["modern", "clean"],
        "emotional_temperature": "neutral",
        "evokes": ["impact"],
    }

@pytest.fixture
def mock_timeline():
    timeline = MagicMock()
    mock_clip = MagicMock()
    mock_clip.GetStart.return_value = 100
    mock_clip.SetProperty.return_value = True
    timeline.GetItemListInTrack.return_value = [mock_clip]
    return timeline, mock_clip

def test_snap_to_beat():
    beat_times = [1.0, 2.0, 3.0]
    # Within tolerance
    snapped, was_snapped = snap_to_beat(1.05, beat_times, tolerance_s=0.1)
    assert snapped == 1.0
    assert was_snapped is True
    
    # Outside tolerance
    snapped, was_snapped = snap_to_beat(1.5, beat_times, tolerance_s=0.1)
    assert snapped == 1.5
    assert was_snapped is False
    
    # Empty beat times
    snapped, was_snapped = snap_to_beat(1.0, [], tolerance_s=0.1)
    assert snapped == 1.0
    assert was_snapped is False

def test_parse_volume_db():
    assert _parse_volume_db("-8 dB") == -8.0
    assert _parse_volume_db("-22 dB (subtle accent)") == -22.0
    assert _parse_volume_db("0.5 dB") == 0.5
    assert _parse_volume_db("invalid") is None

def test_db_to_gain():
    assert _db_to_gain(0.0) == 1.0
    assert abs(_db_to_gain(-6.0) - 0.501) < 0.01
    assert _db_to_gain(-100.0) == 0.0

def test_score_sfx_for_context(sample_sfx):
    context = {
        "emotion": "raw, vulnerable",
        "narrative": "A quiet moment",
        "sfx_intent": "subtle impact",
    }
    vstyle = ["modern", "raw"]
    
    score, reasons = score_sfx_for_context(sample_sfx, context, vstyle)
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
    assert len(reasons) > 0

def test_pick_best_sfx(sample_sfx):
    pool = [sample_sfx]
    used_recently = set()
    context = {"emotion": "neutral"}
    vstyle = ["modern"]
    
    # Should pick the only candidate
    picked, reasons = pick_best_sfx(pool, used_recently, context, vstyle, min_score=0.0)
    assert picked == sample_sfx
    assert sample_sfx["path"] in used_recently

def test_apply_volume_to_placed_clip(mock_timeline):
    timeline, mock_clip = mock_timeline
    track_idx = 1
    record_frame = 100
    volume_note = "-6 dB"
    
    result = _apply_volume_to_placed_clip(timeline, track_idx, record_frame, volume_note)
    
    assert result is True
    timeline.GetItemListInTrack.assert_called_with("audio", track_idx)
    # Gain for -6 dB is roughly 0.5
    mock_clip.SetProperty.assert_called_with("Volume", _db_to_gain(-6.0))

def test_plan_sfx_placement(sample_sfx):
    pools = {"whoosh": [sample_sfx]}
    tl_info = {
        "fps": 30.0,
        "duration_s": 10.0,
        "cuts": [{"time_s": 5.0, "transition_type": "hard_cut"}],
    }
    subtitles = []
    music = {"beat_times": [5.0]}
    segment_briefs = [
        {
            "type": "body",
            "start": 0.0,
            "end": 10.0,
            "emotion": "neutral",
            "sfx_params": {"density": "sparse", "prefer": ["whoosh"]},
            "entering_cut": {"time_s": 5.0, "transition_type": "hard_cut"},
        }
    ]
    
    plan = plan_sfx_placement(pools, tl_info, subtitles, music, segment_briefs, ["modern"])
    assert len(plan) == 1
    item = plan[0]
    
    # Target time should be snapped to beat at 5.0s
    assert item["target_time_s"] == 5.0
    # Placement time should account for pre_roll (peak_s = 0.5)
    assert item["placement_time_s"] == 4.5
    assert item["placement_frame"] == int(4.5 * 30.0)

def test_overlap_prevention_by_density(sample_sfx):
    # Setup a scenario where we have multiple B-roll clips in a "sparse" segment
    # Sparse density budget allows 1 SFX (plus 1 for broll) = 2 total placed
    pools = {"whoosh": [sample_sfx], "accent": [sample_sfx], "punctuation": [sample_sfx]}
    tl_info = {
        "fps": 30.0,
        "duration_s": 10.0,
        "cuts": [],
    }
    subtitles = []
    music = {"beat_times": []}
    segment_briefs = [
        {
            "type": "body",
            "start": 0.0,
            "end": 10.0,
            "emotion": "neutral",
            "sfx_params": {"density": "sparse", "prefer": ["whoosh"]},
            "entering_cut": None,
            "broll": [
                {"name": "broll1", "start_s": 2.0},
                {"name": "broll2", "start_s": 4.0},
                {"name": "broll3", "start_s": 6.0},
            ]
        }
    ]
    
    plan = plan_sfx_placement(pools, tl_info, subtitles, music, segment_briefs, ["modern"])
    
    # Even with 3 b-roll items, sparse budget prevents overlapping by limiting total placements.
    # placed_in_segment stops placing b-roll accents when budget + 1 is reached.
    assert len(plan) == 2

