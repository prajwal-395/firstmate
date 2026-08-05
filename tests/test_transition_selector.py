import pytest
from library.tools.transition_selector import select_transition
from library.schemas.preset_metadata import PresetEntry

class DummyIndex:
    def __init__(self, presets):
        self.presets = presets

def test_select_transition_same_speaker():
    from_clip = {"speaker": "speaker_A", "scene_id": "scene_1"}
    to_clip = {"speaker": "speaker_A", "scene_id": "scene_1"}
    brand = {"transition_types": ["cut", "dissolve"], "transition_duration_ms": 500}
    
    res = select_transition(from_clip, to_clip, brand, None, {})
    assert res["type"] == "cut"

def test_select_transition_scene_change():
    from_clip = {"speaker": "speaker_A", "scene_id": "scene_1"}
    to_clip = {"speaker": "speaker_B", "scene_id": "scene_2"}
    brand = {"transition_types": ["wipe", "dissolve"], "transition_duration_ms": 600}
    
    res = select_transition(from_clip, to_clip, brand, None, {})
    assert res["type"] == "wipe"
    assert res["duration_ms"] == 600

def test_select_transition_high_energy_macro():
    from_clip = {"speaker": "speaker_A", "scene_id": "scene_1"}
    to_clip = {"speaker": "speaker_B", "scene_id": "scene_2"}
    brand = {"transition_types": ["cut", "macro"], "transition_duration_ms": 400}
    creative = {"energy": "high", "mood": "action"}
    
    p = PresetEntry(name="FlashMacro", description="", category="fusion-macro", tags=["transition", "high", "action"], compatibility={}, file_path="")
    idx = DummyIndex([p])
    
    res = select_transition(from_clip, to_clip, brand, idx, creative)
    assert res["type"] == "macro"
    assert res["macro_preset"] == p
    assert res["duration_ms"] == 400
