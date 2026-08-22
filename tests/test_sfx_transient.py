import pytest
import os
import json
from library.steps.step_5_04_compile_manifest.step import main as compile_manifest_main

def test_sfx_transient_placement(monkeypatch, tmp_path):
    mock_sfx_index = [
        {
            "file": "delayed_hit.mp3",
            "path": "/mock/delayed_hit.mp3",
            "folder_category": "Impacts",
            "description": "impact boom",
            "technical": {
                "basic": {"duration": 2.0}
            },
            "transient_offset_sec": 0.53
        },
        {
            "file": "broken.mp3",
            "path": "/mock/broken.mp3",
            "folder_category": "Impacts",
            "description": "impact broken",
            "technical": {
                "basic": {"duration": 2.0}
            },
            "transient_offset_sec": "unknown"
        }
    ]
    
    original_exists = os.path.exists
    def mock_exists(path):
        if path in ["/mock/delayed_hit.mp3", "/mock/broken.mp3"]:
            return True
        return original_exists(path)
    monkeypatch.setattr(os.path, "exists", mock_exists)
    
    def mock_match(sfx_type, index):
        if sfx_type == "bass_impact":
            return "/mock/delayed_hit.mp3", 2.0, 0.53
        elif sfx_type == "click":
            return "/mock/broken.mp3", 2.0, "unknown"
        return None, None, None
        
    import library.steps.step_5_04_compile_manifest.step as step504
    monkeypatch.setattr(step504, "match_sfx_file", mock_match)
    monkeypatch.setattr(step504, "load_sfx_index", lambda: mock_sfx_index)

    input_state = {
        "a_roll_assignments": [],
        "b_roll_assignments": [],
        "b_roll_interjections": [],
        "subtitle_plan": {"subtitles": []},
        "transition_spec": [],
        "enhancement_spec": {"type": "none"},
        "color_grade_spec": {"lut": "none"},
        "audio_mix_spec": {"music_level": -20},
        "music_selection": {"path": "/mock/music.mp3"},
        "audio_spine": {"structure": [], "frame_rate": 30.0},
        "clip_catalog": [{"clip_id": "c1", "path": "test.mov", "width": 1080, "height": 1920}],
        "project_fps": 30.0,
        "semantic_analysis": {"semantic_analysis_documents": []},
        "sfx_spec": [
            {
                "label": "sfx_001",
                "sfx_type": "bass_impact",
                "timeline_in": 2.40,
                "timeline_out": 2.70
            },
            {
                "label": "sfx_002",
                "sfx_type": "click",
                "timeline_in": 5.0,
                "timeline_out": 6.0
            }
        ]
    }
    
    def mock_load(out_dir, filename):
        key = filename.split(".")[0]
        # Map step names to keys in input_state
        mapping = {
            "mesh_spine": "audio_spine",
            "assign_aroll": "a_roll_assignments",
            "select_broll": "b_roll_assignments",
            "plan_subtitles": "subtitle_plan",
            "plan_transitions": "transition_spec",
            "plan_sfx": "sfx_spec",
            "music_selection": "music_selection",
            "plan_vfx": "enhancement_spec",
            "color_grade": "color_grade_spec",
            "audio_mix": "audio_mix_spec",
            "semantic_analysis": "semantic_analysis",
            "catalog": "clip_catalog"
        }
        mapped_key = mapping.get(key)
        if mapped_key and mapped_key in input_state:
            if mapped_key == "clip_catalog":
                return {"clip_catalog": input_state["clip_catalog"]}
            if mapped_key == "audio_spine":
                return {"audio_spine": input_state["audio_spine"]}
            return input_state[mapped_key]
        return {}
        
    monkeypatch.setattr(step504, "load", mock_load)
    monkeypatch.setattr(step504, "resolve_delivery_format", lambda *args: [1080, 1920])
    
    monkeypatch.setattr(step504, "_assert_timeline_fully_covered", lambda x: None)
    manifest = step504.compile_manifest("dummy_project")
    
    assert "tracks" in manifest
    assert "A3" in manifest["tracks"]
    
    a3_clips = manifest["tracks"]["A3"]["clips"]
    assert len(a3_clips) == 2
    
    sfx_clip = a3_clips[0]
    assert sfx_clip["source_file"] == "/mock/delayed_hit.mp3"
    assert sfx_clip["source_in"] == 0.53
    assert sfx_clip["timeline_in"] == 2.40
    assert sfx_clip["timeline_out"] == 2.70

    sfx2_clip = a3_clips[1]
    assert sfx2_clip["source_file"] == "/mock/broken.mp3"
    assert sfx2_clip["source_in"] == 0.0
    assert sfx2_clip["timeline_in"] == 5.0
    assert sfx2_clip["timeline_out"] == 6.0
