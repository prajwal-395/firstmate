import pytest
from library.steps.step_2_05_mesh_spine.post_bridge import enrich_spine

def test_mesh_spine_resolves_passage_ref_by_ordering():
    spine = {
        "structure": [
            {
                "position": 1,
                "block_type": "speech",
                "content": {"passage_ref": 1}
            },
            {
                "position": 2,
                "block_type": "speech",
                "content": {"passage_ref": 2}
            }
        ]
    }
    speech_sequence = {
        "body_sequence": [
            {
                "clip_id": "clip_001",
                "source_start": 0.0,
                "source_end": 2.0,
                "text": "First passage",
                "alignment_method": "whisperx",
                "word_timestamps": [{"word": "First", "source_start": 0.0, "source_end": 1.0}]
            },
            {
                "clip_id": "clip_002",
                "source_start": 3.0,
                "source_end": 5.0,
                "text": "Second passage",
                "alignment_method": "whisperx",
                "word_timestamps": [{"word": "Second", "source_start": 3.0, "source_end": 4.0}]
            }
        ]
    }
    
    # Should not raise ValueError about unresolved passages
    result = enrich_spine(spine, speech_sequence, {}, {"project_config": {"target_duration_seconds": 4.0}})
    
    # Verify the resolution worked
    blocks = result["audio_spine"]["structure"]
    assert len(blocks) == 2
    assert blocks[0]["clip_id"] == "clip_001"
    assert blocks[1]["clip_id"] == "clip_002"
