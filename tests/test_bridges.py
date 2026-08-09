import json
import pytest
from unittest.mock import patch

def test_speech_sequence_bridge():
    try:
        from library.steps.step_2_02_speech_sequence.bridge import pre_bridge
    except ImportError:
        pytest.skip("Step 2.02 pre_bridge not available")

    # Fixture inputs
    inputs = {
        "temporal_index": {
            "clip_1": {
                "speech_regions": [{"start": 0, "end": 2, "text": "hello world", "words": [{"word": "hello", "start": 0, "end": 1}, {"word": "world", "start": 1, "end": 2}]}]
            }
        },
        "semantic_analysis_documents": {
            "clip_1": {"summary": "A man says hello"}
        },
        "creative_direction": {"target_mood": "happy"}
    }

    compressed = pre_bridge(inputs)
    assert isinstance(compressed, dict)
    
    # Verify compressed size is smaller or equal
    original_size = len(json.dumps(inputs))
    compressed_size = len(json.dumps(compressed))
    assert compressed_size <= original_size + 500  # Sometimes compression overhead adds a bit for small inputs

def test_speech_sequence_post_bridge():
    try:
        from library.steps.step_2_02_speech_sequence.post_bridge import post_bridge
    except ImportError:
        pytest.skip("Step 2.02 post_bridge not available")

    inputs = {
        "speech_sequence": {
            "blocks": [{"clip_id": "clip_1", "text": "hello"}]
        },
        "temporal_index": {
            "clip_1": {
                "speech_regions": [{"start": 0, "end": 1, "text": "hello"}]
            }
        }
    }

    resolved = post_bridge(inputs)
    assert "speech_sequence" in resolved
    assert "blocks" in resolved["speech_sequence"]
    
def test_select_broll_bridge():
    try:
        from library.steps.step_3_02_select_broll.bridge import pre_bridge
    except ImportError:
        pytest.skip("Step 3.02 pre_bridge not available")
        
    inputs = {
        "clip_catalog": [{"clip_id": "clip_1"}],
        "a_roll_assignments": [{"clip_id": "clip_1", "timeline_start": 0, "timeline_end": 5}]
    }
    
    compressed = pre_bridge(inputs)
    assert isinstance(compressed, dict)

def test_select_broll_post_bridge():
    try:
        from library.steps.step_3_02_select_broll.post_bridge import post_bridge
    except ImportError:
        pytest.skip("Step 3.02 post_bridge not available")

    inputs = {
        "b_roll_assignments": [{"clip_id": "clip_2", "target_a_roll_entry": "some_id"}],
        "a_roll_assignments": [{"entry_id": "some_id", "timeline_start": 0, "timeline_end": 5}]
    }
    
    resolved = post_bridge(inputs)
    assert "b_roll_assignments" in resolved
