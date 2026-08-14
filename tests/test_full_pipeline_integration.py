import os
import json
import pytest
from library.tools.look_matcher import analyze_frame_colors, compute_match_cdl
from library.tools.dctl_generator import generate_film_emulation_dctl, generate_look_match_dctl
from library.tools.fusion_macro_loader import load_macro, list_available_transitions
from library.tools.transition_selector import select_transition
from library.tools.transition_vocabulary import PLANNABLE_TYPES
from library.tools.fairlight_presets import get_preset, select_preset_for_content
from library.tools.audio_ducker import compute_ducking_curves
from library.tools.audio_reactive_sfx import align_sfx_to_prosody, scale_sfx_density
from library.tools.brand_registry import load_brand_template, query_slots
from library.tools.preset_indexer import scan_library, find_presets, find_preset_for_mood
from library.tools.neural_engine import apply_smart_reframe
from library.tools.engagement_scorer import compute_engagement
from library.schemas.brand_template import BrandTemplate
from library.schemas.preset_metadata import PresetEntry
from library.steps.step_5_03_creative_cohesion.step import review_creative_cohesion
from library.steps.step_5_04_compile_manifest.step import compile_manifest
from library.processes.edit_video.run_pipeline import gather_step_inputs

def test_full_brand_template_flow():
    template = load_brand_template("cinematic_narrative")
    assert template is not None
    assert hasattr(template, "style")
    assert hasattr(template, "effect")
    
    from dataclasses import asdict
    # Mock gather_step_inputs behavior for cohesion review
    inputs = {
        "creative_direction": {"target_energy": "high"},
        "brand_template": asdict(template)
    }
    result = review_creative_cohesion(inputs)
    assert "cohesion_score" in result

def test_preset_library_scan():
    presets = scan_library("library/presets")
    # 13 after the three fusion-macro TRANSITION descriptors were removed:
    # they pointed at .setting files that do not exist, so selecting one
    # failed and the renderer substituted a transition nobody chose.
    assert len(presets.presets) >= 13
    # verify find_preset_for_mood returns results
    mood_preset = find_preset_for_mood(presets, mood="cinematic", energy="high")
    assert mood_preset is not None

def test_color_grade_look_match_chain():
    cdl = compute_match_cdl(
        {"highlights": [0.1, 0.2, 0.3], "midtones": [0.1, 0.2, 0.3], "shadows": [0.1, 0.2, 0.3]},
        {"highlights": [0.15, 0.25, 0.35], "midtones": [0.15, 0.25, 0.35], "shadows": [0.15, 0.25, 0.35]}
    )
    assert "slope" in cdl
    
    inputs = {
        "color_grade_spec": {
            "cdl": cdl
        },
        # A one-block spine, not an empty one: an empty structure
        # compiles a 60s timeline with no picture on it, which is a
        # manifest describing a minute of black.
        "audio_spine": {
            "structure": [
                {
                    "block_type": "speech",
                    "position": 1,
                    "clip_id": "c1",
                    "source_start": 2.417,
                    "source_end": 12.417,
                    "timeline_start": 0.0,
                    "timeline_end": 10.0,
                    "content": {"clip_id": "c1"},
                }
            ],
            "frame_rate": 30.0,
        },
        "clip_catalog": [
            {"clip_id": "c1", "path": __file__, "width": 1080,
             "height": 1920, "duration_seconds": 10.0},
        ],
        "a_roll_assignments": [
            {
                "spine_block_position": 1,
                "clip_id": "c1",
                "source_file": __file__,
                "video_in": 2.417,
                "video_out": 12.417,
                "timeline_start": 0.0,
                "timeline_end": 10.0,
            }
        ],
        "b_roll_assignments": [],
        "transition_spec": [],
        "enhancement_spec": [],
        "sfx_spec": [],
        "audio_mix_spec": {}
    }
    from unittest.mock import patch
    with patch("library.steps.step_5_04_compile_manifest.step.load", side_effect=lambda out_dir, filename: inputs):
        manifest = {"assembly_manifest": compile_manifest("dummy")}
    assert "assembly_manifest" in manifest
    assert manifest["assembly_manifest"].get("color_grade", {}).get("cdl") == cdl

def test_transition_selection_only_yields_drawable_types():
    """A brand list of undrawable types must not produce one anyway."""
    clip_a = {"name": "clip1", "clip_id": "clip_001"}
    clip_b = {"name": "clip2", "clip_id": "clip_002"}

    # "dissolve" is withdrawn and "cut" canonicalises to hard_cut, so the
    # only thing this brand actually permits is a hard cut.
    transition = select_transition(
        clip_a, clip_b,
        {"transition_types": ["cut", "dissolve"]},
        {"target_energy": "high"},
    )
    assert transition["type"] in PLANNABLE_TYPES

    # With the full vocabulary allowed, a high-energy scene change gets a
    # transition the renderer can draw.
    energetic = select_transition(
        clip_a, clip_b, {}, {"target_energy": "high"},
    )
    assert energetic["type"] == "flash"

def test_audio_chain():
    preset = select_preset_for_content("narrative", "cinematic")
    assert preset is not None
    
    speech_regions = [{"start_time": 1.0, "end_time": 2.0}]
    curves = compute_ducking_curves(speech_regions, music_track_duration=3.0)
    assert len(curves) > 0
    
    sfx = [{"start": 1.2, "name": "woosh"}]
    prosody = {"peaks": [1.25]}
    aligned_sfx = align_sfx_to_prosody(sfx, prosody, engagement_scores={})
    assert len(aligned_sfx) == 1

def test_cohesion_review_end_to_end():
    inputs = {
        "creative_direction": {"target_energy": "high"},
        "transition_spec": [
            {"type": "cross_dissolve", "duration_frames": 60}
        ]
    }
    result = review_creative_cohesion(inputs)
    assert result["cohesion_score"] < 100
    assert len(result["warnings"]) > 0
    assert len(result["adjustments"]) > 0
