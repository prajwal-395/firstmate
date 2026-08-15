import json
import os
import re
import tempfile
from pathlib import Path
from typing import Dict, Any, Tuple

from library.tools.paths import PRESETS_ROOT
from library.tools.fusion.parser import parse_setting
from library.tools.fusion.nodes import FusionNode

BUILTIN_DIR = PRESETS_ROOT / "resolve-builtin"
INDEX_FILE = BUILTIN_DIR / "index.json"

_cached_index = None
_cached_classification = None

def list_builtin_effects() -> Dict[str, Dict[str, Any]]:
    """Return the dictionary of built-in effects from index.json."""
    global _cached_index
    if _cached_index is None:
        if not INDEX_FILE.exists():
            return {}
        with open(INDEX_FILE, 'r') as f:
            _cached_index = json.load(f)
    return _cached_index


def _has_image_input(setting_path: Path) -> bool:
    """Check whether a .setting file declares an image input.

    The signal is ``MainInput1 = InstanceInput`` at the GroupOperator /
    MacroOperator level. That declaration gives the macro its image
    input - without it the preset generates pixels from nothing and has
    no picture to modify.

    We match the raw text rather than round-tripping through the parser
    because the parser deliberately drops InstanceInput declarations
    (see import_customized_effect's WITHDRAWN note).
    """
    with open(setting_path, "r", encoding="utf-8") as f:
        content = f.read()
    return bool(re.search(r"MainInput1\s*=\s*InstanceInput", content))


def classify_builtin_effects() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Split built-in presets into clip effects and generators.

    Returns:
        (clip_effects, generators) where each is a dict of
        {name: index_entry} from index.json.

    Clip effects declare an image input (MainInput1 = InstanceInput)
    and modify the picture they receive. Generators produce content
    from nothing and are routed to the overlay track (V5) by
    resolve_generator_overlays in step 4.03's post_bridge.

    The split is derived from each preset's declared image input,
    not from the category label in index.json.
    """
    global _cached_classification
    if _cached_classification is not None:
        return _cached_classification

    effects = list_builtin_effects()
    clip_effects = {}
    generators = {}

    for name, entry in effects.items():
        setting_path = BUILTIN_DIR / entry["path"]
        if setting_path.exists() and _has_image_input(setting_path):
            clip_effects[name] = entry
        else:
            generators[name] = entry

    _cached_classification = (clip_effects, generators)
    return _cached_classification


def list_clip_effects() -> Dict[str, Dict[str, Any]]:
    """Return only presets that modify the picture (have an image input)."""
    clip_effects, _ = classify_builtin_effects()
    return clip_effects


def list_generator_effects() -> Dict[str, Dict[str, Any]]:
    """Return only presets that generate content (no image input)."""
    _, generators = classify_builtin_effects()
    return generators


def is_generator_effect(effect_name: str) -> bool:
    """True if the named effect is a generator (no image input).

    Raises ValueError if the effect is not in the index at all.
    """
    effects = list_builtin_effects()
    if effect_name not in effects:
        raise ValueError(f"Built-in effect not found: {effect_name}")
    _, generators = classify_builtin_effects()
    return effect_name in generators

def get_effect_path(effect_name: str) -> Path:
    """Return the absolute path to the .setting file for the given effect."""
    effects = list_builtin_effects()
    if effect_name not in effects:
        raise ValueError(f"Built-in effect not found: {effect_name}")
    rel_path = effects[effect_name]["path"]
    return BUILTIN_DIR / rel_path

def load_effect_setting(effect_name: str) -> str:
    """Read and return the Lua table content as a string."""
    path = get_effect_path(effect_name)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()

def import_effect_to_clip(clip: Any, effect_name: str) -> bool:
    """Import the built-in effect into the provided Resolve clip.
    
    Args:
        clip: The TimelineItem clip object from DaVinci Resolve.
        effect_name: The snake_case name of the built-in effect.
        
    Returns:
        True if successful. Note: ImportFusionComp returns a composition object,
        we return True if it doesn't crash, but clip.GetFusionCompNameList() 
        should be checked by the caller to verify.
    """
    path = get_effect_path(effect_name)
    # The Resolve API for ImportFusionComp requires a string path
    clip.ImportFusionComp(str(path.resolve()))
    return True

def import_customized_effect(clip: Any, effect_name: str, overrides: dict) -> bool:
    """Import a built-in effect with modified parameters.

    WITHDRAWN from the render path - `apply_fusion_comps` imports built-in
    presets verbatim instead. Do not wire this back in for a macro.

    The object model has no GroupOperator/MacroOperator, so parsing a
    .setting and re-serializing it emits a bare `Composition` with the
    macro wrapper and every InstanceInput gone. That includes MainInput1,
    which is what gives the macro its image input, so the result cannot
    receive the clip's picture at all. Measured across the 143 shipped
    presets: 43 InstanceInput declarations -> 0, and 0 MediaIn/MediaOut in
    any output. Advanced Camera Shake went 10,201 bytes -> 3,783.

    Overriding an exposed macro control means editing its InstanceInput in
    place, not round-tripping the file. Nobody has asked for that yet.

    1. Load the .setting file content
    2. Parse it with fusion/parser.py
    3. Apply overrides to matching node inputs
    4. Serialize back to a temp .setting file
    5. Import via clip.ImportFusionComp()
    """
    content = load_effect_setting(effect_name)
    comp = parse_setting(content)
    
    for node in comp.nodes:
        if isinstance(node, FusionNode):
            for k, v in overrides.items():
                # Apply override if the node already has this input,
                # or if the user prefixed the key with the node name.
                if "." in k:
                    node_name, param = k.split(".", 1)
                    if node.name == node_name:
                        node.set_input(param, v)
                elif k in node.inputs:
                    node.set_input(k, v)
                # If it's a specific tool like AdvancedCameraShake and key is overall_magnitude,
                # it's safer to just set it if the tool type matches the parameter's likely target,
                # but setting it blindly on all nodes is dangerous.
                # However, for macros that don't have the input in the parsed dict (defaults),
                # we might need to set it anyway. Let's just set it on the main tool.
                # We'll set it on any node where tool_type == node.name (without numbers) 
                # or just set it on all nodes and let Fusion ignore invalid inputs.
                # Actually, Fusion ignores invalid inputs on nodes.
                else:
                    # To support cases where the default value wasn't in the .setting file:
                    # we will just add it. Fusion ignores inputs that don't exist.
                    node.set_input(k, v)
                    
    fd, temp_path = tempfile.mkstemp(suffix=".setting")
    with os.fdopen(fd, 'w') as f:
        f.write(comp.serialize())
        
    try:
        clip.ImportFusionComp(temp_path)
    finally:
        os.remove(temp_path)
        
    return True
