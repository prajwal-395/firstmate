import pytest
from unittest.mock import Mock, patch, mock_open
from library.tools.fusion_macro_loader import load_macro, apply_macro_to_transition, list_available_transitions
from library.schemas.preset_metadata import PresetEntry

class DummyIndex:
    def __init__(self, presets):
        self.presets = presets

def test_load_macro():
    preset = PresetEntry(name="Test", description="", category="fusion-macro", tags=[], compatibility={}, file_path="/fake/path.setting")
    with patch("os.path.exists", return_value=True):
        with patch("builtins.open", mock_open(read_data="macro_content")):
            result = load_macro(preset)
            assert result["name"] == "Test"
            assert result["raw_content"] == "macro_content"

def test_apply_macro_to_transition_success():
    tl_item = Mock()
    tl_item.ImportFusionComp.return_value = True
    
    with patch("os.path.exists", return_value=True):
        res = apply_macro_to_transition(tl_item, {"file_path": "/fake/path.setting"}, 500)
        assert res is True
        tl_item.ImportFusionComp.assert_called_once_with("/fake/path.setting")

def test_apply_macro_to_transition_failure():
    tl_item = Mock()
    tl_item.ImportFusionComp.return_value = False
    
    with patch("os.path.exists", return_value=True):
        res = apply_macro_to_transition(tl_item, {"file_path": "/fake/path.setting"}, 500)
        assert res is False

def test_list_available_transitions():
    p1 = PresetEntry(name="T1", description="", category="fusion-macro", tags=["transition"], compatibility={}, file_path="")
    p2 = PresetEntry(name="P2", description="", category="fairlight", tags=["transition"], compatibility={}, file_path="")
    p3 = PresetEntry(name="T3", description="", category="fusion-macro", tags=["other"], compatibility={}, file_path="")
    idx = DummyIndex([p1, p2, p3])
    
    res = list_available_transitions(idx)
    assert len(res) == 1
    assert res[0].name == "T1"
