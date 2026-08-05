import pytest
from library.tools.fairlight_presets import (
    get_preset,
    select_preset_for_content,
    apply_fairlight_preset,
    FAIRLIGHT_PRESETS
)

def test_get_preset_exists():
    preset = get_preset("podcast_master")
    assert preset is not None
    assert "chain" in preset
    assert preset["target_lufs"] == -14

def test_get_preset_default():
    preset = get_preset("unknown_preset")
    assert preset == FAIRLIGHT_PRESETS["dialogue_enhancement"]

def test_select_preset_for_content():
    # Test brand preference overrides content type
    brand_audio = {"preferred_preset": "music_forward"}
    preset = select_preset_for_content("podcast", brand_audio)
    assert preset == "music_forward"

    # Test content type mapping
    assert select_preset_for_content("podcast", {}) == "podcast_master"
    assert select_preset_for_content("interview", None) == "podcast_master"
    assert select_preset_for_content("cinematic", {}) == "music_forward"
    assert select_preset_for_content("ambient", {}) == "ambient_bed"
    assert select_preset_for_content("vlog", {}) == "dialogue_enhancement"

def test_apply_fairlight_preset():
    class MockTimelineItem:
        def __init__(self):
            self.properties = {}
        def SetProperty(self, key, value):
            self.properties[key] = value

    item = MockTimelineItem()
    preset = get_preset("podcast_master")
    
    # Should return True if it has SetProperty
    assert apply_fairlight_preset(item, preset) is True
    
    # Should return False if it doesn't
    assert apply_fairlight_preset(object(), preset) is False
