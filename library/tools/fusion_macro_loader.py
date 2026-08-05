import os
import json
from library.schemas.preset_metadata import PresetEntry

def load_macro(preset_entry: PresetEntry) -> dict:
    """Read a .setting file and return its parameters."""
    if not os.path.exists(preset_entry.file_path):
        return {}
    
    try:
        with open(preset_entry.file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            return {
                "file_path": preset_entry.file_path,
                "name": preset_entry.name,
                "raw_content": content,
                "tags": preset_entry.tags
            }
    except Exception as e:
        print(f"Error loading macro: {e}")
        return {}

def apply_macro_to_transition(timeline_item, macro_data: dict, duration_ms: int) -> bool:
    """Apply a Fusion macro as a transition between two clips using the Resolve scripting API."""
    if not macro_data or "file_path" not in macro_data:
        return False
    
    file_path = macro_data["file_path"]
    if not os.path.exists(file_path):
        return False
        
    try:
        if hasattr(timeline_item, "ImportFusionComp"):
            result = timeline_item.ImportFusionComp(file_path)
            # Result could be true or composition object depending on Resolve version/docs
            return bool(result)
        return False
    except Exception:
        return False

def list_available_transitions(preset_index) -> list:
    """Return all fusion-macro presets tagged as transitions."""
    results = []
    if not preset_index:
        return results
        
    for preset in preset_index.presets:
        if preset.category == "fusion-macro" and "transition" in [t.lower() for t in preset.tags]:
            results.append(preset)
    return results
