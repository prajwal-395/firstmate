#!/usr/bin/env python3
"""
Step 6.1: Render Final Video

Assembles the final video in DaVinci Resolve.
Calls resolve_build_timeline.py's main function with the correct arguments.

Input: { "assembly_manifest": {...}, ... }
Output: {
    "render_output": {
        "timeline_name": "...",
        "status": "success",
        ...
    }
}
"""
import json
import sys
import os
from resolve_build_timeline import build_timeline

def run(inputs: dict) -> dict:
    manifest = inputs.get("assembly_manifest", {})
    if not manifest:
        # Sometimes orchestrator passes it as the root or under another key
        # Check if project is in inputs, which means it might be the manifest itself
        if "project" in inputs and "tracks" in inputs:
            manifest = inputs
        else:
            raise ValueError("assembly_manifest missing from inputs")
            
    try:
        # Build timeline (this connects to Resolve)
        result = build_timeline(
            manifest=manifest,
            project_name=manifest.get("project", {}).get("name", "Pipeline_Edit"),
            delete_existing=True
        )
        
        if not result.get("success") and result.get("errors"):
            if any("Cannot connect to DaVinci Resolve" in str(e) for e in result.get("errors", [])):
                raise ConnectionError("DaVinci Resolve is not running or not accessible.")
            raise RuntimeError(f"Timeline build failed: {result.get('errors')}")
            
        return {
            "render_output": {
                "timeline_name": result.get("timeline_name"),
                "status": "success",
                "success": result.get("success", True),
                "errors": result.get("errors", []),
                "tracks": result.get("tracks", {}),
                "warnings": result.get("warnings", [])
            }
        }
        
    except ConnectionError as e:
        # Fail fast if Resolve isn't running
        raise RuntimeError(f"ConnectionError: {str(e)}")
    except Exception as e:
        raise RuntimeError(f"Render failed: {str(e)}")


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}
        
    try:
        result = run(input_data)
        json.dump(result, sys.stdout, indent=2)
    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "step": "6.1_render"
        }))
        sys.exit(1)


if __name__ == "__main__":
    main()
