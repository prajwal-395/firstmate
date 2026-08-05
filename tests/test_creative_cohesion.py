import json
import pytest
from library.steps.step_5_03_creative_cohesion.step import review_creative_cohesion

def test_creative_cohesion_high_energy_mismatch():
    inputs = {
        "creative_direction": {
            "target_energy": "high",
            "pacing": {"cuts_per_minute": 30}
        },
        "transition_spec": {
            "transitions": [
                {"type": "cross_dissolve", "duration_frames": 30}
            ]
        },
        "sfx_spec": [
            # sparse SFX
        ],
        "speech_sequence": {
            "body_sequence": [
                {"start_time": 0, "end_time": 60}
            ]
        },
        "color_grade_spec": {
            "mood": "soft and muted"
        }
    }
    
    review = review_creative_cohesion(inputs)
    assert review["cohesion_score"] < 100
    
    warnings = review["warnings"]
    assert any("High energy but found slow transition" in w for w in warnings)
    assert any("High energy but sparse SFX" in w for w in warnings)
    assert any("High energy but color grade is soft/muted" in w for w in warnings)
    
    adjustments = review["adjustments"]
    assert any(a["target_step"] == "transition_spec" and a["field"] == "duration_frames" and a["suggested_value"] == 10 for a in adjustments)

def test_creative_cohesion_aligned_specs():
    inputs = {
        "creative_direction": {
            "target_energy": "calm",
            "pacing": {"cuts_per_minute": 5}
        },
        "transition_spec": {
            "transitions": [
                {"type": "cross_dissolve", "duration_frames": 45},
                {"type": "dip_to_black", "duration_frames": 45},
                {"type": "fade_in", "duration_frames": 60},
                {"type": "cut", "duration_frames": 0},
                {"type": "cut", "duration_frames": 0}
            ]
        },
        "sfx_spec": [
            # sparse SFX (5 per min)
            {"sfx_type": "whoosh"},
            {"sfx_type": "swell"},
            {"sfx_type": "swell"},
            {"sfx_type": "swell"},
            {"sfx_type": "swell"}
        ],
        "speech_sequence": {
            "body_sequence": [
                {"start_time": 0, "end_time": 60}
            ]
        },
        "color_grade_spec": {
            "mood": "soft and muted"
        }
    }
    
    review = review_creative_cohesion(inputs)
    assert review["cohesion_score"] == 100
    assert len(review["warnings"]) == 0
    assert len(review["adjustments"]) == 0

def test_creative_cohesion_missing_inputs():
    # Should handle missing inputs gracefully
    inputs = {}
    review = review_creative_cohesion(inputs)
    assert review["cohesion_score"] == 100
    assert len(review["warnings"]) == 0

def test_auto_adjustments_applied():
    inputs = {
        "creative_direction": {
            "target_energy": "high"
        },
        "transition_spec": {
            "transitions": [
                {"type": "cross_dissolve", "duration_frames": 30}
            ]
        }
    }
    review = review_creative_cohesion(inputs)
    # The step output itself doesn't mutate transitions in the input object, 
    # but we can verify it outputs applied_adjustments if score < 70
    assert review["cohesion_score"] < 100
    
    # We set score -= 5 for transition mismatch, wait, if score is 95, it won't auto-apply!
    # Let's add more mismatches to drop score below 70.
    inputs = {
        "creative_direction": {
            "target_energy": "high"
        },
        "transition_spec": {
            "transitions": [
                {"type": "cross_dissolve", "duration_frames": 30}
            ]
        },
        "sfx_spec": [],
        "color_grade_spec": {"mood": "soft"},
        "speech_sequence": {
            "hook_segment": {"engagement": 1},
            "body_sequence": [{"engagement": 20, "end_time": 60}]
        }
    }
    review = review_creative_cohesion(inputs)
    # 1 transition mismatch = -5
    # SFX mismatch = -5
    # Color mismatch = -5
    # Pacing mismatch = (skipped because no cuts_per_minute provided)
    # Engagement mismatch = -10
    # Total score = 75, still >= 70!
    
    # Let's add 6 bad transitions to force it under 70
    inputs["transition_spec"]["transitions"] = [{"type": "cross_dissolve", "duration_frames": 30} for _ in range(7)]
    review = review_creative_cohesion(inputs)
    
    assert review["cohesion_score"] < 70
    assert len(review["applied_adjustments"]) > 0
    
    # Check if the transition in the input object was modified! 
    # Wait, our code actually modifies `transitions` in-place inside `review_creative_cohesion`!
    assert inputs["transition_spec"]["transitions"][0]["duration_frames"] == 10

