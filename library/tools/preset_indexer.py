import os
import json
from typing import List, Optional
from library.schemas.preset_metadata import PresetEntry

class PresetIndex:
    def __init__(self, presets: List[PresetEntry]):
        self.presets = presets

def scan_library(library_path: str) -> PresetIndex:
    presets = []
    for root, _, files in os.walk(library_path):
        for f in files:
            if f.endswith('.meta.json'):
                meta_path = os.path.join(root, f)
                try:
                    with open(meta_path, 'r', encoding='utf-8') as mf:
                        data = json.load(mf)
                    
                    target_file = data.get("file_path")
                    if not target_file:
                        base_name = f[:-10] # remove .meta.json
                        possible_files = [cf for cf in files if cf.startswith(base_name) and not cf.endswith('.meta.json')]
                        if possible_files:
                            target_file = os.path.join(root, possible_files[0])
                        else:
                            target_file = meta_path # fallback
                    else:
                        if not os.path.isabs(target_file):
                            target_file = os.path.join(root, target_file)

                    entry = PresetEntry.from_dict(data, target_file)
                    presets.append(entry)
                except Exception as e:
                    print(f"Error loading {meta_path}: {e}")
    return PresetIndex(presets)

def find_presets(index: PresetIndex, category: str, tags: List[str]) -> List[PresetEntry]:
    results = []
    for p in index.presets:
        if category and p.category != category:
            continue
        if tags and not all(t in p.tags for t in tags):
            continue
        results.append(p)
    return results

def find_preset_for_mood(index: PresetIndex, mood: str, energy: str) -> Optional[PresetEntry]:
    best_match = None
    best_score = -1
    
    mood_lower = mood.lower()
    energy_lower = energy.lower()
    
    for p in index.presets:
        score = 0
        p_tags_lower = [t.lower() for t in p.tags]
        p_moods_lower = [m.lower() for m in p.mood_match]
        p_energies_lower = [e.lower() for e in p.energy_match]
        
        if mood_lower in p_moods_lower:
            score += 3
        elif mood_lower in p_tags_lower:
            score += 2
        elif any(mood_lower in t or t in mood_lower for t in p_tags_lower):
            score += 1
            
        if energy_lower in p_energies_lower:
            score += 3
        elif energy_lower in p_tags_lower:
            score += 2
            
        if score > best_score:
            best_score = score
            best_match = p
            
    return best_match if best_score > 0 else None
