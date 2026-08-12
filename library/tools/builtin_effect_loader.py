import json
from pathlib import Path
from typing import Dict, Any

from library.tools.paths import PRESETS_ROOT

BUILTIN_DIR = PRESETS_ROOT / "resolve-builtin"
INDEX_FILE = BUILTIN_DIR / "index.json"

_cached_index = None

def list_builtin_effects() -> Dict[str, Dict[str, Any]]:
    """Return the dictionary of built-in effects from index.json."""
    global _cached_index
    if _cached_index is None:
        if not INDEX_FILE.exists():
            return {}
        with open(INDEX_FILE, 'r') as f:
            _cached_index = json.load(f)
    return _cached_index

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
