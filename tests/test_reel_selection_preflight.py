import pytest
from library.steps.step_3_04_select_reels.bridge import build_context
from library.steps.step_3_04_select_reels.post_bridge import resolve
from library.tools.reel_proposal import validate_proposal

def test_bridge_and_post_bridge_preflight():
    transcript = {
        "segments": [
            {"speaker": "Craig", "text": "so tell me about your company", "timeline_start": 0.0, "timeline_end": 5.0, "resolve_item_id": "u", "source_file": "a.MXF", "source_start": 0.0, "source_end": 5.0},
            {"speaker": "Akshita", "text": "we do video editing", "timeline_start": 6.0, "timeline_end": 10.0, "resolve_item_id": "v", "source_file": "a.MXF", "source_start": 6.0, "source_end": 10.0},
            {"speaker": "Craig", "text": "that sounds cool. go to our website", "timeline_start": 11.0, "timeline_end": 15.0, "resolve_item_id": "w", "source_file": "a.MXF", "source_start": 11.0, "source_end": 15.0},
            
            {"speaker": "Akshita", "text": "just me talking for a long time about nothing in particular", "timeline_start": 35.0, "timeline_end": 45.0, "resolve_item_id": "z", "source_file": "a.MXF", "source_start": 35.0, "source_end": 45.0},
        ],
        "derived_from": {"duration_seconds": 100.0}
    }
    
    llm_output = {
        "moments": [
            {"start": 0.0, "end": 15.0, "slug": "first-exchange", "reason": "good intro"},
            {"start": 35.0, "end": 45.0, "slug": "single-speaker", "reason": "just talking"}
        ]
    }
    
    out = resolve(llm_output, {"timeline_transcript": transcript})
    
    moments = out["reel_selection"]["moments"]
    dropped = out["reel_selection"]["dropped"]
    
    assert len(moments) > 0

