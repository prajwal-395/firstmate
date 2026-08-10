#!/usr/bin/env python3
"""
Step 2.1: Define Creative Direction

Generates creative direction (tone, pacing, style, mood) from the analyzed footage.
This step acts as a bridge for an LLM but also produces sensible defaults when run deterministically.

Input: { "clip_catalog": {...}, "semantic_analysis_documents": {...}, "temporal_index": {...}, "prosody_analysis": {...} }
Output: {
    "creative_direction": {
        "narrative_theme": "string",
        "target_mood": "string",
        "target_energy": "string",
        "energy_arc": "string",
        "emotional_landscape": "string",
        "audience_emotion": "string",
        "key_moments": [],
        "rationale": "string"
    }
}
"""
import json
import sys


def generate_creative_direction(inputs: dict) -> dict:
    """
    Generate sensible defaults for creative direction based on input data.
    """
    clip_catalog = inputs.get("clip_catalog", {})
    semantic_docs = inputs.get("semantic_analysis_documents", {})
    
    # Try to find some key moments from the semantic docs or catalog
    key_moments = []
    if isinstance(semantic_docs, dict):
        for clip_id, doc in list(semantic_docs.items())[:3]:
            # Use some placeholder description if we can't extract a good one easily
            desc = doc.get("visual_content", {}).get("description", f"Key moment from {clip_id}")
            key_moments.append(desc)
    elif isinstance(semantic_docs, list):
        for doc in semantic_docs[:3]:
            clip_id = doc.get("clip_id", "unknown")
            doc_data = doc.get("document", doc) if isinstance(doc, dict) else {}
            desc = doc_data.get("visual_content", {}).get("description", f"Key moment from {clip_id}")
            key_moments.append(desc)
    
    # Sensible defaults
    direction = {
        "narrative_theme": "A dynamic and engaging narrative woven from the raw footage.",
        "target_mood": "motivational",
        "target_energy": "medium",
        "energy_arc": "start medium -> build tension -> climax at key moments -> resolve",
        "emotional_landscape": "An inspiring journey with clear emotional peaks.",
        "audience_emotion": "Inspired and engaged.",
        "key_moments": key_moments if key_moments else ["Opening hook", "Main action sequence", "Resolution"],
        "rationale": "Default deterministic fallback based on available clip density."
    }
    
    # We also pass through the context so it can act as a bridge if the runner supports it
    # But the primary output is the creative_direction block.
    return {
        "creative_direction": direction,
        "__bridge_context": inputs # Optional bridge context
    }


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}
        
    result = generate_creative_direction(input_data)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
