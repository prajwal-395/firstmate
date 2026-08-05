import json
import os
from pathlib import Path

LIBRARY_ROOT = Path("library")

def update_process_manifest():
    manifest_path = LIBRARY_ROOT / "processes" / "edit_video" / "manifest.json"
    if not manifest_path.exists():
        print(f"File {manifest_path} not found")
        return
    with open(manifest_path, "r") as f:
        data = json.load(f)
        
    inputs = data.setdefault("interface", {}).setdefault("inputs", [])
    if not any(inp["name"] == "brand_template" for inp in inputs):
        inputs.append({
            "name": "brand_template",
            "type": "string",
            "required": False,
            "description": "Absolute path to the brand template YAML/JSON file"
        })
        
    with open(manifest_path, "w") as f:
        json.dump(data, f, indent=2)
    print("Updated process manifest")

def update_step_manifest(step_name, new_inputs):
    manifest_path = LIBRARY_ROOT / "steps" / step_name / "manifest.json"
    if not manifest_path.exists():
        print(f"File {manifest_path} not found")
        return
    with open(manifest_path, "r") as f:
        data = json.load(f)
        
    inputs = data.setdefault("interface", {}).setdefault("inputs", [])
    for inp in new_inputs:
        if not any(i["name"] == inp["name"] for i in inputs):
            inputs.append(inp)
            
    reads = data.setdefault("state", {}).setdefault("reads", [])
    for inp in new_inputs:
        if inp["name"] not in reads:
            reads.append(inp["name"])
            
    with open(manifest_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Updated {step_name}")

if __name__ == "__main__":
    update_process_manifest()
    
    update_step_manifest("step_2_01_creative_direction", [
        {"name": "brand_style", "type": "object", "required": False, "description": "Brand style slots"},
        {"name": "brand_content", "type": "object", "required": False, "description": "Brand content slots"}
    ])
    
    update_step_manifest("step_4_02_plan_transitions", [
        {"name": "brand_effect", "type": "object", "required": False, "description": "Brand effect slots"}
    ])
    
    update_step_manifest("step_4_04_plan_sfx", [
        {"name": "brand_effect", "type": "object", "required": False, "description": "Brand effect slots"}
    ])
    
    update_step_manifest("step_5_01_color_grade", [
        {"name": "brand_style", "type": "object", "required": False, "description": "Brand style slots"}
    ])
