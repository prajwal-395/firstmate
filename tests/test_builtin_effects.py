import os
from pathlib import Path
from library.tools.builtin_effect_loader import list_builtin_effects, get_effect_path, load_effect_setting

def test_index_json_entries():
    effects = list_builtin_effects()
    assert len(effects) == 143, f"Expected 143 built-in effects, got {len(effects)}"
    assert "advanced_camera_shake" in effects
    assert "chromatic_aberration" in effects

def test_effect_paths_exist():
    effects = list_builtin_effects()
    for name, data in effects.items():
        path = get_effect_path(name)
        assert path.exists(), f"Path for effect {name} does not exist: {path}"
        assert path.is_file(), f"Path for effect {name} is not a file: {path}"

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

def test_setting_files_are_valid():
    effects = list_builtin_effects()
    # Test a handful to avoid making the test too slow, or test all of them
    # Testing all 143 shouldn't take too long
    for name in effects:
        content = load_effect_setting(name)
        # Check for typical Fusion setting markers
        assert "Tools" in content or "MacroOperator" in content or "GroupOperator" in content, f"Effect {name} does not seem to be a valid Fusion setting file"
