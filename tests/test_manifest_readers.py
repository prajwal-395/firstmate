"""
Ensure every manifest key produced by compile_manifest is actually read
by a downstream renderer, or explicitly exempted.
"""
import ast
import pathlib
import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# Keys that are deliberately written to the manifest but are not read
# by any renderer. Each must have a documented reason.
EXEMPTED_KEYS = {
    "transitions_downgraded": "Recorded for dashboard export only, no renderer consumes it",
    "cohesion_adjustments": "Recorded for dashboard export only, no renderer consumes it",
}

# The expected readers for each top-level manifest key.
# Mapping of key -> list of (module_path, function_name)
EXPECTED_READERS = {
    "project": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "tracks": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "subtitles": [
        ("library/steps/step_5_04_compile_manifest/step.py", "_apply_manifest_qa_checks"),
    ],
    "transitions": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "vfx": [
        ("library/tools/execution/apply_fusion_comps.py", "apply_fusion_comps"),
    ],
    "generator_overlays": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "fusion_effects": [
        ("library/tools/execution/apply_fusion_comps.py", "apply_fusion_comps"),
    ],
    "neural_engine_directives": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "audio": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "color_grade": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "audio_mix": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "_spine_blocks": [
        ("library/steps/step_5_04_compile_manifest/step.py", "_assert_timeline_fully_covered"),
    ],
    "subtitle_overlay": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "motion_graphics_overlay": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
    "smart_reframe": [
        ("library/steps/step_6_01_render/resolve_build_timeline.py", "build_timeline"),
    ],
}

def get_manifest_keys():
    """Dynamically discover the top-level keys written to the manifest."""
    step_path = REPO / "library/steps/step_5_04_compile_manifest/step.py"
    with open(step_path, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'compile_manifest':
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id == 'manifest':
                            if isinstance(stmt.value, ast.Dict):
                                keys = []
                                for key in stmt.value.keys:
                                    if isinstance(key, ast.Constant):
                                        keys.append(key.value)
                                return keys
    return []

def does_function_read_key(module_path, function_name, target_key):
    """Check if the function in the module reads manifest.get(target_key) or manifest[target_key]."""
    path = REPO / module_path
    with open(path, 'r', encoding='utf-8') as f:
        tree = ast.parse(f.read())
    
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            # We found the function, now walk its body to find the read
            for subnode in ast.walk(node):
                if isinstance(subnode, ast.Call):
                    if isinstance(subnode.func, ast.Attribute) and subnode.func.attr == 'get':
                        if isinstance(subnode.func.value, ast.Name) and subnode.func.value.id in ('manifest', 'input_data'):
                            if subnode.args and isinstance(subnode.args[0], ast.Constant) and subnode.args[0].value == target_key:
                                return True
                elif isinstance(subnode, ast.Subscript):
                    if isinstance(subnode.value, ast.Name) and subnode.value.id in ('manifest', 'input_data'):
                        if isinstance(subnode.slice, ast.Constant) and subnode.slice.value == target_key:
                            return True
    return False

def test_every_manifest_key_has_a_reader_or_is_exempt():
    keys = get_manifest_keys()
    assert keys, "Could not discover any manifest keys"
    
    for key in keys:
        if key in EXEMPTED_KEYS:
            continue
        
        assert key in EXPECTED_READERS, f"Manifest key '{key}' has no expected reader and is not exempted."
        
        readers = EXPECTED_READERS[key]
        for module_path, func_name in readers:
            assert does_function_read_key(module_path, func_name, key), \
                f"Function {func_name} in {module_path} does not actually consume manifest key '{key}'"
