#!/usr/bin/env python3
"""Every edge's `data_mapping` against the manifests at both ends.

One process was checked, and there is more than one
--------------------------------------------------
This opened `library/processes/edit_video/dag.json` BY NAME, so the
`reels` process - `build_reels` -> `verify_reels`, carrying `reel_build`
- was never checked by the gate that exists to check exactly that.  It
is the same defect `library/tools/processes.py` was written to remove
("four callers were reading edit_video/dag.json as though it were the
only one"), in a fifth caller nobody had counted.

`processes.every_dag()` is the enumeration, so a process that exists
cannot be invisible here: `process_ids()` scans the directory rather
than listing.

The mirror question - who READS what an edge carries - is
`library/tools/output_contract.py`.
"""
import json
import sys
from pathlib import Path


def run_validation():
    """Print every mismatch and return how many there were.

    A mismatch is one of two things, and both are the key-name failure
    AGENTS.md 10.1 calls the dominant bug class: an edge naming a source
    key its producer does not declare, or a destination key its consumer
    does not declare.
    """
    from library.tools import processes

    root_dir = Path(__file__).resolve().parent.parent.parent
    errors = 0

    for process_id, dag in processes.every_dag().items():
        manifests = {}
        for node in dag.get("nodes", []):
            step_ref = node.get("step_ref", "")
            manifest_file = root_dir / 'library' / step_ref / 'manifest.json'
            if manifest_file.exists():
                with open(manifest_file, encoding="utf-8") as mf:
                    manifests[node["id"]] = json.load(mf)
            else:
                print(f"Warning: Manifest not found for step "
                      f"{node['id']} at {manifest_file}")

        for edge in dag.get("edges", []):
            src_id = edge["from"]
            dst_id = edge["to"]
            mapping = edge.get("data_mapping", {})

            src_manifest = manifests.get(src_id, {})
            dst_manifest = manifests.get(dst_id, {})

            src_outputs = [o.get("name") for o in
                           src_manifest.get("interface", {}).get("outputs", [])]
            dst_inputs = [i.get("name") for i in
                          dst_manifest.get("interface", {}).get("inputs", [])]

            for src_key, dst_key in mapping.items():
                if src_key not in src_outputs:
                    print(f"ERROR: [{process_id}] Edge {src_id} -> {dst_id} "
                          f"maps '{src_key}' to '{dst_key}', but '{src_key}' "
                          f"is missing from {src_id}'s manifest outputs.")
                    errors += 1

                if dst_key not in dst_inputs:
                    print(f"ERROR: [{process_id}] Edge {src_id} -> {dst_id} "
                          f"maps '{src_key}' to '{dst_key}', but '{dst_key}' "
                          f"is missing from {dst_id}'s manifest inputs.")
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
