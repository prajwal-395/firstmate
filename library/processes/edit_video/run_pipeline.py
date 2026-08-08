#!/usr/bin/env python3
"""
Pipeline Runner — Executes the edit_video DAG end-to-end.

This is the orchestrator entry point that:
1. Reads the DAG (nodes and edges vary by pipeline)
2. Resolves execution order via topological sort
3. For each step:
   - Deterministic steps: runs step.py with JSON stdin/stdout
   - Nondeterministic steps: presents handoff.md as LLM prompt
   - Bridge steps: runs bridge.py which orchestrates both
4. Tracks state in pipeline_data.json (full checkpoint support)

Usage:
    python3 run_pipeline.py --project /path/to/project
    python3 run_pipeline.py --project /path/to/project --from temporal_index
    python3 run_pipeline.py --project /path/to/project --step creative_direction
    python3 run_pipeline.py --project /path/to/project --dry-run
"""
import json
import os
import sys
import subprocess
import time
import argparse
from pathlib import Path
from collections import deque


# ── Path Configuration ──────────────────────────────────────────────

PILOT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LIBRARY_ROOT = PILOT_ROOT / "library"
STEPS_ROOT = LIBRARY_ROOT / "steps"
DAG_PATH = LIBRARY_ROOT / "processes/edit_video/dag.json"


# ── DAG Loader ──────────────────────────────────────────────────────

def load_dag(dag_path: str = None) -> dict:
    """Load and validate the DAG definition."""
    path = dag_path or str(DAG_PATH)
    with open(path) as f:
        return json.load(f)


def topological_sort(dag: dict) -> list:
    """Topological sort of the DAG nodes, respecting edge dependencies."""
    nodes = {n["id"]: n for n in dag["nodes"]}
    
    # Build adjacency and in-degree
    in_degree = {n: 0 for n in nodes}
    adj = {n: [] for n in nodes}
    
    for edge in dag["edges"]:
        adj[edge["from"]].append(edge["to"])
        in_degree[edge["to"]] += 1
    
    # Kahn's algorithm
    queue = deque([n for n in nodes if in_degree[n] == 0])
    order = []
    
    while queue:
        node = queue.popleft()
        order.append(node)
        for neighbor in adj[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    
    if len(order) != len(nodes):
        raise ValueError("DAG has cycles!")
    
    return order


def _get_ancestors(node_id: str, dag: dict) -> set:
    """Return the set of strict ancestors of *node_id* in the DAG.

    An ancestor is any node that transitively feeds into *node_id* via
    the DAG edges.  The returned set does NOT include *node_id* itself.
    Used by --from to skip only the steps that the target depends on
    (presumed already complete) while keeping parallel branches alive.
    (Fix H2 helper)
    """
    # Build a reverse adjacency list: child -> set of parents
    reverse_adj: dict[str, set] = {}
    for edge in dag["edges"]:
        reverse_adj.setdefault(edge["to"], set()).add(edge["from"])

    ancestors: set = set()
    queue = deque(reverse_adj.get(node_id, []))
    while queue:
        parent = queue.popleft()
        if parent not in ancestors:
            ancestors.add(parent)
            queue.extend(reverse_adj.get(parent, []))
    return ancestors


def get_step_dir(dag_node: dict) -> Path:
    """Get the filesystem path for a step from its DAG node."""
    step_ref = dag_node["step_ref"]  # e.g., "steps/step_1_01_scan_project"
    return LIBRARY_ROOT / step_ref


def get_step_implementation(step_dir: Path) -> dict:
    """Determine what type of implementation a step has."""
    has_step_py = (step_dir / "step.py").exists()
    has_bridge_py = (step_dir / "bridge.py").exists()
    has_handoff_md = (step_dir / "handoff.md").exists()
    has_manifest = (step_dir / "manifest.json").exists()
    
    # Load manifest for metadata
    manifest = {}
    if has_manifest:
        with open(step_dir / "manifest.json") as f:
            manifest = json.load(f)
    
    determinism = manifest.get("determinism", "unknown")
    
    if has_step_py and not has_handoff_md:
        return {
            "type": "deterministic",
            "entry": str(step_dir / "step.py"),
            "determinism": determinism,
            "manifest": manifest,
        }
    elif has_step_py and has_handoff_md and not has_bridge_py:
        # Deterministic step with optional LLM review (e.g., rough cut
        # review runs mechanical checks, then handoff.md guides narrative
        # review). step.py runs first; handoff.md is informational.
        return {
            "type": "deterministic",
            "entry": str(step_dir / "step.py"),
            "prompt": str(step_dir / "handoff.md"),
            "determinism": determinism,
            "manifest": manifest,
        }
    elif has_bridge_py and has_handoff_md:
        return {
            "type": "hybrid",
            "entry": str(step_dir / "bridge.py"),
            "prompt": str(step_dir / "handoff.md"),
            "determinism": determinism,
            "manifest": manifest,
        }
    elif has_handoff_md and not has_bridge_py and not has_step_py:
        return {
            "type": "llm_only",
            "prompt": str(step_dir / "handoff.md"),
            "determinism": determinism,
            "manifest": manifest,
        }
    else:
        return {
            "type": "unknown",
            "files": {
                "step.py": has_step_py,
                "bridge.py": has_bridge_py,
                "handoff.md": has_handoff_md,
            },
            "determinism": determinism,
            "manifest": manifest,
        }


# ── State Management ────────────────────────────────────────────────

def load_pipeline_state(project_dir: str) -> dict:
    """Load or initialize pipeline state from project.
    
    Always sets project_folder from the argument, regardless of
    what's in the existing file.
    """
    state_path = os.path.join(project_dir, "pipeline_data.json")
    if os.path.exists(state_path):
        with open(state_path) as f:
            state = json.load(f)
    else:
        state = {
            "pipeline_version": "1.0",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "steps_completed": {},
            "step_outputs": {},
        }
    # Always inject project_folder from CLI
    state["project_folder"] = project_dir
    
    # Inject shared library paths from environment (via paths.py).
    # Priority: existing state value > env var > manifest default.
    # This replaces hardcoded paths with the centralized paths module.
    try:
        from tools.paths import sfx_library_path, music_library_path
        if "sfx_library" not in state:
            sfx = sfx_library_path()
            if sfx:
                state["sfx_library"] = sfx
        if "music_library" not in state:
            music = music_library_path()
            if music:
                state["music_library"] = music
    except ImportError:
        pass

    # Fall back to manifest defaults for anything still missing
    manifest_path = LIBRARY_ROOT / "processes" / "edit_video" / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            process_manifest = json.load(f)
        for inp in process_manifest.get("interface", {}).get("inputs", []):
            name = inp.get("name", "")
            if name in ("sfx_library", "music_library", "brand_template") and name not in state:
                default = inp.get("default", "")
                if default:
                    state[name] = default
    
    return state


def save_pipeline_state(project_dir: str, state: dict):
    """Save pipeline state to project."""
    state_path = os.path.join(project_dir, "pipeline_data.json")
    state["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


def gather_step_inputs(node_id: str, dag: dict, state: dict, manifest: dict = None) -> dict:
    """Gather inputs for a step from upstream outputs using edge data_mappings.

    Raises RuntimeError when a declared data_mapping source key is missing
    from the upstream step's outputs, unless the step's manifest marks that
    input as optional (required: false).  This prevents silent contract
    violations from propagating incomplete dicts downstream.  (Fix H3)
    """
    inputs = {}

    # Build a set of optional input names from the step manifest so we can
    # tolerate missing source keys for those inputs only.
    optional_inputs: set = set()
    if manifest:
        for inp in manifest.get("interface", {}).get("inputs", []):
            if not inp.get("required", True):
                optional_inputs.add(inp.get("name", ""))

    for edge in dag["edges"]:
        if edge["to"] == node_id:
            source_id = edge["from"]
            source_outputs = state.get("step_outputs", {}).get(source_id, {})

            # Apply data_mapping if specified
            mapping = edge.get("data_mapping", {})
            if mapping:
                for src_key, dst_key in mapping.items():
                    if src_key in source_outputs:
                        inputs[dst_key] = source_outputs[src_key]
                    elif dst_key not in optional_inputs:
                        # Fix H3: Raise on missing required mapped input
                        # instead of silently skipping, so contract
                        # violations surface immediately.
                        raise RuntimeError(
                            f"Step '{node_id}': data_mapping expects key "
                            f"'{src_key}' from upstream step '{source_id}', "
                            f"but it is missing from that step's outputs. "
                            f"Available keys: {list(source_outputs.keys())}"
                        )
            else:
                # No explicit mapping - merge all outputs
                inputs.update(source_outputs)

    # Always include project folder
    inputs["project_folder"] = state.get("project_folder", "")

    # Add brand template data if present
    if manifest:
        step_inputs = [inp.get("name") for inp in manifest.get("interface", {}).get("inputs", [])]
        brand_template_path = state.get("brand_template")
        
        if brand_template_path or any(x in step_inputs for x in ["brand_style", "brand_effect", "brand_content"]):
            try:
                import sys
                if str(LIBRARY_ROOT.parent) not in sys.path:
                    sys.path.append(str(LIBRARY_ROOT.parent))
                from library.tools.brand_registry import load_brand_template, query_slots
                bt = load_brand_template(brand_template_path if brand_template_path else "")
                if "brand_style" in step_inputs:
                    inputs["brand_style"] = query_slots(bt, "style")
                if "brand_effect" in step_inputs:
                    inputs["brand_effect"] = query_slots(bt, "effect")
                if "brand_content" in step_inputs:
                    inputs["brand_content"] = query_slots(bt, "content")
            except Exception as e:
                import sys
                print(f"Warning: failed to load brand template: {e}", file=sys.stderr)

    return inputs


# ── Step Execution ──────────────────────────────────────────────────

def run_deterministic_step(entry: str, inputs: dict) -> dict:
    """Run a deterministic step via subprocess (stdin JSON → stdout JSON)."""
    result = subprocess.run(
        ["python3", entry],
        input=json.dumps(inputs),
        capture_output=True,
        text=True,
        timeout=600,
    )
    
    if result.returncode != 0:
        raise RuntimeError(
            f"Step failed (exit {result.returncode}):\n"
            f"  stderr: {result.stderr[:500]}"
        )
    
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Step produced invalid JSON:\n"
            f"  stdout: {result.stdout[:500]}\n"
            f"  stderr: {result.stderr[:500]}"
        )


def present_llm_step(prompt_path: str, inputs: dict, node_id: str) -> dict:
    """Present an LLM step as a prompt for the user/Antigravity to complete.
    
    In automated mode, this writes the prompt + context to a handoff file
    and pauses execution. The LLM completes it and saves results.
    
    In interactive mode, this prints the prompt and waits for input.
    """
    with open(prompt_path) as f:
        prompt = f.read()
    
    print(f"\n{'─'*60}", file=sys.stderr)
    print(f"  ⏸  LLM STEP: {node_id}", file=sys.stderr)
    print(f"  Prompt: {prompt_path}", file=sys.stderr)
    print(f"  Inputs: {list(inputs.keys())}", file=sys.stderr)
    print(f"{'─'*60}", file=sys.stderr)
    print(f"\n  This step requires LLM judgment.", file=sys.stderr)
    print(f"  Copy the prompt from {prompt_path}", file=sys.stderr)
    print(f"  and provide the required inputs to Antigravity.", file=sys.stderr)
    print(f"\n  When complete, save the output to:", file=sys.stderr)
    print(f"    pipeline_data.json → step_outputs.{node_id}", file=sys.stderr)
    print(f"{'─'*60}\n", file=sys.stderr)
    
    # For non-interactive: return a marker indicating human/LLM needed
    return {
        "__status": "awaiting_llm",
        "__prompt": prompt_path,
        "__inputs_available": list(inputs.keys()),
    }


def run_hybrid_step(bridge_path: str, prompt_path: str, inputs: dict) -> dict:
    """Run a hybrid step: bridge.py handles the deterministic parts
    and produces context for the LLM prompt."""
    # Run bridge first to enrich inputs
    result = subprocess.run(
        ["python3", bridge_path],
        input=json.dumps(inputs),
        capture_output=True,
        text=True,
        timeout=600,
    )
    
    if result.returncode != 0:
        print(f"  Bridge stderr: {result.stderr[:300]}", file=sys.stderr)
        # Bridge failure is non-fatal — fall through to LLM with original inputs
        enriched = inputs
    else:
        try:
            enriched = json.loads(result.stdout)
        except json.JSONDecodeError:
            enriched = inputs
    
    return enriched


# ── Main Runner ─────────────────────────────────────────────────────

def run_pipeline(
    project_dir: str,
    from_step: str = None,
    single_step: str = None,
    dry_run: bool = False,
    auto_mode: bool = False,
    review_mode: bool = False,
):
    """Execute the pipeline DAG."""
    dag = load_dag()
    state = load_pipeline_state(project_dir)
    order = topological_sort(dag)
    nodes = {n["id"]: n for n in dag["nodes"]}
    
    print(f"\n{'═'*60}", file=sys.stderr)
    print(f"  Pipeline: edit_video", file=sys.stderr)
    print(f"  Project: {project_dir}", file=sys.stderr)
    print(f"  Steps: {len(order)}", file=sys.stderr)
    print(f"  Order: {' → '.join(order)}", file=sys.stderr)
    print(f"{'═'*60}\n", file=sys.stderr)
    
    # Determine which steps to run
    steps_to_run = []
    if from_step:
        # Fix H2: Ancestor-aware --from skip logic.
        # Instead of linearly skipping everything before the target in the
        # topo-sorted list (which arbitrarily kills parallel branches),
        # compute the set of ancestors that the target step depends on and
        # skip only those.  Parallel branches that are NOT ancestors of
        # from_step will still execute, preserving required data for
        # downstream steps.
        ancestors = _get_ancestors(from_step, dag)
        for node_id in order:
            if single_step and node_id != single_step:
                continue
            # Skip the target step's ancestors (they are presumed complete)
            # but keep the target step itself and everything after it,
            # as well as parallel branches that aren't ancestors.
            if node_id in ancestors:
                continue
            steps_to_run.append(node_id)
    else:
        for node_id in order:
            if single_step and node_id != single_step:
                continue
            steps_to_run.append(node_id)
    
    print(f"  Steps to run: {steps_to_run}", file=sys.stderr)
    
    completed = []
    failed = []
    awaiting_llm = []
    
    for node_id in steps_to_run:
        node = nodes[node_id]
        step_dir = get_step_dir(node)
        impl = get_step_implementation(step_dir)
        
        # Check if already completed
        if node_id in state.get("steps_completed", {}):
            print(f"  ⏭  {node_id}: already completed", file=sys.stderr)
            completed.append(node_id)
            continue
        
        print(f"\n  ▶  Step: {node_id} ({node['name']})", file=sys.stderr)
        print(f"     Type: {impl['type']} | Dir: {step_dir}", file=sys.stderr)
        
        if dry_run:
            inputs = gather_step_inputs(node_id, dag, state)
            print(f"     Inputs: {list(inputs.keys())}", file=sys.stderr)
            print(f"     [DRY RUN — skipping execution]", file=sys.stderr)
            completed.append(node_id)
            continue
        
        # Gather inputs from upstream (pass manifest for optional-input checking)
        inputs = gather_step_inputs(node_id, dag, state, manifest=impl.get("manifest"))
        print(f"     Inputs: {list(inputs.keys())}", file=sys.stderr)
        
        try:
            start_time = time.time()
            
            if impl["type"] == "deterministic":
                output = run_deterministic_step(impl["entry"], inputs)
                elapsed = time.time() - start_time
                print(f"     ✓ Completed in {elapsed:.1f}s", file=sys.stderr)
                print(f"     Outputs: {list(output.keys())}", file=sys.stderr)
                
                # Save state
                state.setdefault("step_outputs", {})[node_id] = output
                state.setdefault("steps_completed", {})[node_id] = {
                    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "elapsed_s": round(elapsed, 1),
                }
                save_pipeline_state(project_dir, state)
                
                # Export step output for dashboard review
                _export_step_for_review(project_dir, node_id, node["name"], output)
                
                # Review gate: pause if review mode is enabled
                if review_mode:
                    _save_review_gate(
                        project_dir, node_id, node["name"],
                        output, inputs,
                    )
                    print(f"     ⏸ Review gate saved. Inspect at dashboard.",
                          file=sys.stderr)
                
                completed.append(node_id)
                
            elif impl["type"] == "hybrid":
                # Run bridge for enrichment, then present for LLM
                enriched = run_hybrid_step(
                    impl["entry"], impl["prompt"], inputs
                )
                
                if auto_mode:
                    # Fix H1: Bridge output is context prepared for the LLM,
                    # NOT the final step output.  Storing it verbatim sends
                    # wrong data shapes downstream.  Mark it clearly so
                    # consumers can distinguish bridge context from real
                    # LLM-completed output.
                    auto_output = {
                        "__status": "auto_bridge",
                        "__bridge_context": enriched,
                        "__note": (
                            "This is bridge-generated context, not final "
                            "step output. The LLM interaction was skipped "
                            "in --auto mode."
                        ),
                    }
                    if isinstance(enriched, dict):
                        for k, v in enriched.items():
                            if k not in auto_output:
                                auto_output[k] = v
                    
                    state.setdefault("step_outputs", {})[node_id] = auto_output
                    state.setdefault("steps_completed", {})[node_id] = {
                        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "note": "auto-completed via bridge (context only)",
                    }
                    save_pipeline_state(project_dir, state)
                    completed.append(node_id)
                else:
                    output = present_llm_step(impl["prompt"], enriched, node_id)
                    awaiting_llm.append(node_id)
                    print(f"     ⏸ Awaiting LLM completion", file=sys.stderr)
                    # Fix C1: Break out of the execution loop so downstream
                    # steps don't fire with missing upstream data.
                    break

            elif impl["type"] == "llm_only":
                output = present_llm_step(impl["prompt"], inputs, node_id)
                awaiting_llm.append(node_id)
                print(f"     ⏸ Awaiting LLM completion", file=sys.stderr)
                # Fix C1: Break out of the execution loop so downstream
                # steps don't fire with missing upstream data.
                break
                
            else:
                print(f"     ⚠ Unknown implementation type: {impl['type']}", 
                      file=sys.stderr)
                failed.append(node_id)
                
        except Exception as e:
            print(f"     ✗ FAILED: {e}", file=sys.stderr)
            failed.append(node_id)
            
            # Check error policy
            error_policy = node.get("error_policy", {}).get("policy", "fail")
            if error_policy == "retry":
                max_retries = node.get("error_policy", {}).get("max_retries", 2)
                print(f"     Retry policy: up to {max_retries} retries", 
                      file=sys.stderr)
                # TODO: implement retry loop
            elif error_policy != "continue":
                print(f"     Stopping pipeline due to failure.", file=sys.stderr)
                break
    
    # Summary
    print(f"\n{'═'*60}", file=sys.stderr)
    print(f"  Pipeline Summary", file=sys.stderr)
    print(f"{'═'*60}", file=sys.stderr)
    print(f"  Completed:    {len(completed)} steps", file=sys.stderr)
    print(f"  Awaiting LLM: {len(awaiting_llm)} steps", file=sys.stderr)
    print(f"  Failed:       {len(failed)} steps", file=sys.stderr)
    
    if completed:
        print(f"  ✓ {', '.join(completed)}", file=sys.stderr)
    if awaiting_llm:
        print(f"  ⏸ {', '.join(awaiting_llm)}", file=sys.stderr)
    if failed:
        print(f"  ✗ {', '.join(failed)}", file=sys.stderr)
    
    print(f"{'═'*60}\n", file=sys.stderr)
    
    # Output final state
    summary = {
        "completed": completed,
        "awaiting_llm": awaiting_llm,
        "failed": failed,
        "state_file": os.path.join(project_dir, "pipeline_data.json"),
    }
    json.dump(summary, sys.stdout, indent=2)
    return summary


def _export_step_for_review(project_dir, step_id, step_name, output):
    """Export step output for dashboard review (non-critical, best-effort)."""
    try:
        from library.tools.step_exporter import export_step_output
        export_step_output(project_dir, step_id, step_name, output)
    except Exception as e:
        print(f"     (step export skipped: {e})", file=sys.stderr)


def _save_review_gate(project_dir, step_id, step_name, output, inputs):
    """Save a review gate snapshot (non-critical, best-effort)."""
    try:
        from library.tools.review_gate import save_gate_snapshot
        save_gate_snapshot(
            project_dir, step_id, step_name, output,
            upstream_context={k: str(type(v).__name__) for k, v in inputs.items()},
        )
    except Exception as e:
        print(f"     (gate save skipped: {e})", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Pipeline Runner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project", help="Project directory (absolute path)")
    group.add_argument("--slug", help="Project slug (looked up from project registry)")
    parser.add_argument("--from", dest="from_step", help="Start from this step")
    parser.add_argument("--step", help="Run only this step")
    parser.add_argument("--dry-run", action="store_true", help="Show plan without executing")
    parser.add_argument("--auto", action="store_true", 
                       help="Auto-complete hybrid steps (use bridge output as final)")
    parser.add_argument("--review", action="store_true",
                       help="Enable review gates: export step outputs and save gate snapshots for dashboard review")
    args = parser.parse_args()
    
    # Resolve project directory from slug if provided
    project_dir = args.project
    if args.slug:
        try:
            from tools.paths import project_root
            project_dir = str(project_root(args.slug))
        except (ImportError, FileNotFoundError) as e:
            print(f"Error resolving project slug '{args.slug}': {e}", file=sys.stderr)
            sys.exit(1)
    
    run_pipeline(
        project_dir=project_dir,
        from_step=args.from_step,
        single_step=args.step,
        dry_run=args.dry_run,
        auto_mode=args.auto,
        review_mode=args.review,
    )


if __name__ == "__main__":
    main()
