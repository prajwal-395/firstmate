#!/usr/bin/env python3
"""
Step 2.4: Music Selection

Selects background music based on creative direction and audio spine.
Queries available music based on mood/tempo.

Input: { "creative_direction": {...}, "audio_spine": {...}, "project_folder": "..." }
Output: {
    "music_selection": {
        "audio_path": "...",
        "title": "...",
        "source_url": "...",
        "duration_seconds": 120.0,
        "bpm": 120,
        "key": "C"
    }
}
"""
import json
import os
import sys
from search_youtube import search_youtube
from download_track import download_audio

def run(inputs: dict) -> dict:
    creative_direction = inputs.get("creative_direction", {})
    mood = creative_direction.get("target_mood", "motivational")
    energy = creative_direction.get("target_energy", "medium")
    
    project_folder = inputs.get("project_folder", "./")
    output_dir = os.path.join(project_folder, "music")
    
    query = f"{mood} {energy} background music no copyright"
    
    try:
        search_results = search_youtube(query, max_results=1)
        if not search_results.get("results"):
            raise ValueError("No results found.")
            
        best_match = search_results["results"][0]
        url = best_match["url"]
        
        track_info = download_audio(url, output_dir)
        return {
            "music_selection": track_info
        }
        
    except Exception as e:
        # Fallback if download or search fails
        return {
            "music_selection": {
                "audio_path": "",
                "title": f"Fallback track for {mood}",
                "source_url": "",
                "duration_seconds": 0,
                "bpm": None,
                "key": None,
                "error": str(e)
            }
        }

def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}
        
    result = run(input_data)
    json.dump(result, sys.stdout, indent=2)

if __name__ == "__main__":
    main()
