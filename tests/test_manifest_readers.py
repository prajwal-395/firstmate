"""
Ensure every manifest key produced by compile_manifest is actually read
by a downstream renderer, or explicitly exempted.

What this test can and cannot prove
-----------------------------------
It proves a reader EXISTS: that the named function really contains
`manifest[key]` or `manifest.get(key)`. It cannot prove the reader turns
the value into picture or sound, and there is no static check that can.
`smart_reframe` passed this test for its whole life while its reader
called a method Resolve does not expose on a Timeline and printed a tick
regardless of the answer.

So each entry carries a third element: one sentence saying what the
reader DOES with the value. Writing that sentence is the check - if you
cannot say what a viewer gets, the key is inert and belongs in
EXEMPTED_KEYS with a reason, or nowhere. The sentence is asserted
non-empty so it cannot be skipped, and it must be honest: "draws a
marker for a human editor" is a legitimate thing to write. It is what
`audio_mix` used to say, and no longer does - the mix reaches the sound
now, through library/tools/otio_mix.py.
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
    "vfx_planning_basis": (
        "Why the VFX layer is the length it is, including the entries "
        "this step dropped because no clip on V1 or V2 covered them. "
        "Recorded for the reviewer and the dashboard export; no renderer "
        "consumes it. See library/tools/vfx_plan_basis.py"
    ),
}

RENDERER = "library/steps/step_6_01_render/resolve_build_timeline.py"
FUSION = "library/tools/execution/apply_fusion_comps.py"
COMPILE = "library/steps/step_5_04_compile_manifest/step.py"

# The expected readers for each top-level manifest key.
# Mapping of key -> list of (module_path, function_name, what the reader
# does with the value).
EXPECTED_READERS = {
    "project": [
        (RENDERER, "build_timeline",
         "Sets the timeline resolution and timebase before any clip is placed."),
    ],
    "tracks": [
        (RENDERER, "build_timeline",
         "Places every V1/V2 video clip and every A1/A2/A3+ audio clip on the timeline."),
    ],
    "subtitles": [
        (COMPILE, "_apply_manifest_qa_checks",
         "Timing and overlap checks; the captions themselves reach the picture "
         "through subtitle_overlay, which carries the rendered Remotion segments."),
    ],
    "transitions": [
        ("library/tools/manifest_validator.py", "_check_distinct_cut_points",
         "The planner's record, validated for distinct cut points. The list the "
         "renderer draws from is fusion_effects.transitions, which carries clip indices."),
    ],
    "vfx": [
        (FUSION, "apply_fusion_comps",
         "Each entry becomes a Fusion comp on its clip via build_effect_comp."),
    ],
    "generator_overlays": [
        (RENDERER, "build_timeline",
         "Imports each .setting generator onto a V5 carrier clip."),
    ],
    "fusion_effects": [
        (FUSION, "apply_fusion_comps",
         "per_clip becomes the film-look comp on every V1/V2 clip; transitions "
         "become the drawn transition comp on the outgoing clip."),
    ],
    "neural_engine_directives": [
        (RENDERER, "build_timeline",
         "Calls Stabilize() and the Super Scale media-pool property per clip, and "
         "records a warning when Resolve declines."),
    ],
    "audio": [
        (RENDERER, "build_timeline",
         "Applies the named Fairlight preset to the timeline."),
    ],
    "color_grade": [
        (RENDERER, "build_timeline",
         "Applies the house look's CDL half - slope, offset, power, saturation - "
         "with SetCDL on every graded clip."),
    ],
    "audio_mix": [
        (RENDERER, "build_timeline",
         "Writes every planned dB onto the timeline through an OTIO round "
         "trip: the bed's per-block curve as Fairlight keyframes, each clip's "
         "volume_db as a static level. Falls back to a marker saying UNAPPLIED "
         "when Resolve declines the import, and keeps the master limiter as a "
         "marker because no clip-level route reaches a bus."),
    ],
    "_spine_blocks": [
        (COMPILE, "_assert_timeline_fully_covered",
         "Fails compilation on any stretch of timeline with no clip on it, except "
         "a hole the spine declared as an intentional black beat."),
    ],
    "subtitle_overlay": [
        (RENDERER, "build_timeline",
         "Places the rendered Remotion ProRes 4444 caption segments on V3."),
    ],
    "motion_graphics_overlay": [
        (RENDERER, "build_timeline",
         "Places the rendered Remotion motion-graphics segments on V4."),
    ],
    "timed_text_overlay": [
        (RENDERER, "build_timeline",
         "Places the rendered Remotion timed-text segments on V6, so each "
         "moment the brand template declared is burned over the picture for "
         "the frames it declared."),
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
        for module_path, func_name, effect in readers:
            assert does_function_read_key(module_path, func_name, key), \
                f"Function {func_name} in {module_path} does not actually consume manifest key '{key}'"
            # The reader existing is not the same as the reader doing
            # something. Say what a viewer gets, or the key is inert.
            assert effect and effect.strip(), (
                f"Manifest key '{key}' names {func_name} as its reader but does not "
                f"say what that reader does with the value. Write one sentence about "
                f"what reaches the picture or the sound, or move the key to "
                f"EXEMPTED_KEYS with a reason."
            )
