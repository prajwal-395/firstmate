"""Tests for the spine contract."""

import pytest
from library.tools.spine_contract import validate_spine_blocks, SpineContractError


def test_validate_spine_rejects_out_of_bounds_duration():
    blocks = [
        {
            "position": 1,
            "block_type": "speech",
            "clip_id": "c1",
            "source_start": 0.0,
            "source_end": 10.0,
            "timeline_start": 0.0,
            "timeline_end": 10.0,
            "word_timestamps": [{"word": "hello", "source_start": 0.0, "source_end": 1.0}],
            "alignment_method": "whisperx",
        }
    ]

    # B1 fold: the deleted `test_validate_spine_accepts_valid_duration`
    # lives on as the baseline here - the same shape at a valid duration
    # passes, so the zone has an inside and not just an outside.
    valid = [dict(blocks[0], source_end=60.0, timeline_end=60.0)]
    validate_spine_blocks(valid, total_duration=60.0,
                          target_duration_zone=(54.0, 60.0, 66.0))

    # Target zone [54.0, 66.0]. Total duration 10.0 is way below.
    with pytest.raises(SpineContractError) as exc:
        validate_spine_blocks(blocks, total_duration=10.0, target_duration_zone=(54.0, 60.0, 66.0))
    assert "outside the target duration zone" in str(exc.value)

def test_validate_spine_rejects_leading_silence():
    blocks = [
        {
            "position": 1,
            "block_type": "silence",
            "clip_id": None,
            "source_start": None,
            "source_end": None,
            "timeline_start": 0.0,
            "timeline_end": 5.0,
            "word_timestamps": [],
            "alignment_method": None,
        },
        {
            "position": 2,
            "block_type": "speech",
            "clip_id": "c1",
            "source_start": 0.0,
            "source_end": 60.0,
            "timeline_start": 5.0,
            "timeline_end": 65.0,
            "word_timestamps": [{"word": "hello", "source_start": 0.0, "source_end": 1.0}],
            "alignment_method": "whisperx",
        }
    ]
    
    with pytest.raises(SpineContractError) as exc:
        validate_spine_blocks(blocks, total_duration=65.0, target_duration_zone=(54.0, 60.0, 66.0))
    assert "leading silence block at the head of the timeline is not allowed" in str(exc.value)
