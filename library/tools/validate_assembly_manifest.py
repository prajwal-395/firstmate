#!/usr/bin/env python3
"""
Assembly Manifest Validator.

Validates the assembly_manifest.json produced by step 5.04 (compile_manifest)
against the JSON Schema at library/schema/assembly_manifest.schema.json.

Usage:
    # As a module:
    from validate_assembly_manifest import validate_assembly_manifest
    errors = validate_assembly_manifest(manifest_dict)
    if errors:
        for e in errors:
            print(e)

    # As a CLI tool:
    python validate_assembly_manifest.py path/to/assembly_manifest.json

Requires: pip install jsonschema
"""
import json
import os
import sys

try:
    from jsonschema import Draft7Validator
except ImportError:
    print("Error: jsonschema package is required. Install it using 'pip install jsonschema'")
    sys.exit(1)

SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "schema",
    "assembly_manifest.schema.json"
)

def validate_assembly_manifest(manifest: dict) -> list[str]:
    """
    Validates a manifest dict against the assembly manifest schema.
    Returns a list of error strings. An empty list means valid.
    """
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema = json.load(f)
    except Exception as e:
        return [f"Failed to load schema from {SCHEMA_PATH}: {e}"]

    validator = Draft7Validator(schema)
    errors = []
    
    for error in validator.iter_errors(manifest):
        path = ".".join([str(p) for p in error.path]) if error.path else "root"
        errors.append(f"{path}: {error.message}")
    
    return errors

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {os.path.basename(sys.argv[0])} <manifest.json>")
        sys.exit(1)
        
    manifest_path = sys.argv[1]
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        print(f"Error loading manifest {manifest_path}: {e}")
        sys.exit(1)
        
    errors = validate_assembly_manifest(manifest)
    if errors:
        print(f"Validation failed with {len(errors)} errors:")
        for err in errors:
            print(f" - {err}")
        sys.exit(1)
    else:
        print("Manifest is valid.")
        sys.exit(0)
