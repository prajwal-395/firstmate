#!/usr/bin/env python3
"""Bridge for LLM-driven SFX queries.

Reads a query JSON from stdin, searches the FAISS index, returns results.
Designed to be called by the orchestrator during step 4.04.
"""
import json, sys, os

# Add the analysis tools directory to the path (repo-relative)
PILOT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PILOT_ROOT, 'library', 'tools', 'analysis'))
from sfx_query import SFXIndex

def main():
    try:
        input_data = sys.stdin.read()
        if not input_data.strip():
            print(json.dumps({"error": "Empty input"}))
            return
        data = json.loads(input_data)
        query = data.get("query", "")
        top_k = data.get("top_k", 5)
        filters = data.get("filters", {})
        
        # Allow profiles_dir to be passed in (from pipeline config)
        profiles_dir = data.get("sfx_profiles_dir")
        idx = SFXIndex(profiles_dir) if profiles_dir else SFXIndex()
        
        if query:
            results = idx.search_and_filter(query, top_k=top_k, **filters)
        else:
            # Just apply filters if no query
            results = idx.filter(**filters)[:top_k]
            
        json.dump({"results": results}, sys.stdout, indent=2)
    except Exception as e:
        json.dump({"error": str(e)}, sys.stdout)

if __name__ == '__main__':
    main()
