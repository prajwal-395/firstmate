#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

def run_validation():
    root_dir = Path(__file__).resolve().parent.parent.parent
    dag_path = root_dir / 'library' / 'processes' / 'edit_video' / 'dag.json'
    steps_dir = root_dir / 'library' / 'steps'

    with open(dag_path) as f:
        dag = json.load(f)

    # Load all manifests
    manifests = {}
    step_ref_to_id = {}
    
    for node in dag.get("nodes", []):
        step_id = node["id"]
        step_ref = node["step_ref"]
        step_ref_to_id[step_ref] = step_id
        
        manifest_file = root_dir / 'library' / step_ref / 'manifest.json'
        if manifest_file.exists():
            with open(manifest_file) as mf:
                manifests[step_id] = json.load(mf)
        else:
            print(f"Warning: Manifest not found for step {step_id} at {manifest_file}")

    errors = 0

    for edge in dag.get("edges", []):
        src_id = edge["from"]
        dst_id = edge["to"]
        mapping = edge.get("data_mapping", {})

        src_manifest = manifests.get(src_id, {})
        dst_manifest = manifests.get(dst_id, {})

        src_outputs = [o.get("name") for o in src_manifest.get("interface", {}).get("outputs", [])]
        dst_inputs = [i.get("name") for i in dst_manifest.get("interface", {}).get("inputs", [])]

        for src_key, dst_key in mapping.items():
            if src_key not in src_outputs:
                print(f"ERROR: Edge {src_id} -> {dst_id} maps '{src_key}' to '{dst_key}', "
                      f"but '{src_key}' is missing from {src_id}'s manifest outputs.")
                errors += 1

            if dst_key not in dst_inputs:
                print(f"ERROR: Edge {src_id} -> {dst_id} maps '{src_key}' to '{dst_key}', "
                      f"but '{dst_key}' is missing from {dst_id}'s manifest inputs.")
                errors += 1

    return errors

def main():
    errors = run_validation()
    if errors > 0:
        print(f"\nFound {errors} contract mismatches.")
        sys.exit(1)
    else:
        print("All DAG contracts are valid.")
        sys.exit(0)

if __name__ == "__main__":
    main()
