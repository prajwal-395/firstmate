"""Resolve SFX types to real audio files in the shared SFX library.

The planner used to pick any type it liked and only discover three steps
later, inside compile_manifest, that the library had nothing to play.
Type resolution now lives in one place so the planner can restrict itself
to what actually exists and fail at plan time if it does not.
"""

import glob
import json
import os

# Type -> the words we look for in a library entry's description, folder
# category and filename.
TYPE_KEYWORDS = {
    "whoosh":         ["whoosh", "swish", "air", "wind", "sweep"],
    "swish":          ["swish", "whoosh", "sweep"],
    "bass_impact":    ["impact", "bass", "hit", "boom", "thud",
                       "punch", "slam"],
    "riser":          ["riser", "rise", "swell", "build", "tension"],
    "click":          ["click", "tick", "tap", "snap"],
    "tick":           ["tick", "click", "tap"],
    "reverse_cymbal": ["reverse", "cymbal", "crash"],
    "swell":          ["swell", "pad", "atmosphere", "rise"],
}

# Index files that are metadata about the library, not entries in it.
_NON_ENTRY_FILES = (
    "sfx_index.json", "library_analysis.json", "library_semantic.json",
)


def load_sfx_index(library_path: str = "") -> list:
    """Load the SFX library index, or the per-file profiles under it."""
    if not library_path:
        library_path = os.environ.get("PIPELINE_SFX_LIBRARY", "")
    if not library_path:
        try:
            from library.tools.paths import sfx_library_path
            library_path = sfx_library_path()
        except ImportError:
            return []
    if not library_path:
        return []

    index_path = os.path.join(library_path, "sfx_index.json")
    if os.path.exists(index_path):
        try:
            with open(index_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    entries = []
    profiles = os.path.join(library_path, "profiles")
    if os.path.isdir(profiles):
        for path in sorted(glob.glob(os.path.join(profiles, "*.json"))):
            if os.path.basename(path) in _NON_ENTRY_FILES:
                continue
            try:
                with open(path) as f:
                    entries.append(json.load(f))
            except (json.JSONDecodeError, IOError):
                continue
    return entries


def match_sfx_file(sfx_type: str, index_entries: list):
    """Best (file_path, duration) for a type, or (None, None)."""
    if not index_entries:
        return None, None

    keywords = TYPE_KEYWORDS.get(sfx_type, [sfx_type])
    best_score = 0
    best_entry = None
    for entry in index_entries:
        desc = (entry.get("description", "") or "").lower()
        cat = (entry.get("folder_category", "") or "").lower()
        fname = (entry.get("file", "") or "").lower()
        searchable = f"{desc} {cat} {fname}"
        score = sum(1 for kw in keywords if kw in searchable)
        if score > best_score:
            best_score = score
            best_entry = entry

    if best_entry and best_score > 0:
        path = best_entry.get("path", "")
        duration = (best_entry.get("technical", {})
                    .get("basic", {}).get("duration", 0.5))
        return path, duration

    return None, None


def available_sfx_types(index_entries: list = None) -> list:
    """The SFX types this library can actually play, sorted."""
    if index_entries is None:
        index_entries = load_sfx_index()
    available = []
    for sfx_type in sorted(TYPE_KEYWORDS):
        path, _ = match_sfx_file(sfx_type, index_entries)
        if path and os.path.exists(path):
            available.append(sfx_type)
    return available
