import os
from pathlib import Path
from library.tools.builtin_effect_loader import list_builtin_effects, get_effect_path, load_effect_setting



def test_fuzzy_matching_logic():
    effects = list_builtin_effects()
    
    def fuzzy_match(preset_name):
        if preset_name in effects:
            return preset_name
        for b_name in effects:
            if preset_name in b_name or b_name in preset_name:
                return b_name
        return None

    assert fuzzy_match("advanced_camera_shake") == "advanced_camera_shake"
    assert fuzzy_match("camera_shake") == "advanced_camera_shake"
    assert fuzzy_match("chromatic_aberration") == "chromatic_aberration"
    assert fuzzy_match("lens_flare") is not None # should match one of the lens flares
    assert fuzzy_match("non_existent_effect_123") is None

