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

"""
import json
import os
import sys
import subprocess
import time
import argparse
from pathlib import Path
from collections import deque
import re
import logging

from library.tools.pipeline_logger import get_logger, step_timer

logger = logging.getLogger(__name__)

class PreBridgeError(Exception): pass
class LLMError(Exception): pass
class PostBridgeError(Exception): pass

def _is_transient_error(e: Exception) -> bool:
    if isinstance(e, subprocess.TimeoutExpired):
        return True
    msg = str(e).lower()
    return any(x in msg for x in ["network", "rate limit", "timeout", "timed out", "503", "429", "connection", "socket", "500", "502", "too many requests"])


sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
from model_lifecycle import unload_all


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
    
    runtime = manifest.get("implementation", {}).get("default", {}).get("runtime", "")
    if runtime == "llm":
        if has_bridge_py or (step_dir / "post_bridge.py").exists():
            return {
                "type": "hybrid",
                "step_dir": step_dir,
                "prompt": str(step_dir / "handoff.md"),
                "determinism": determinism,
                "manifest": manifest,
            }
        else:
            return {
                "type": "llm_only",
                "prompt": str(step_dir / "handoff.md"),
                "determinism": determinism,
                "manifest": manifest,
            }
            
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
            "type": "deterministic_with_llm",
            "entry": str(step_dir / "step.py"),
            "prompt": str(step_dir / "handoff.md"),
            "determinism": determinism,
            "manifest": manifest,
        }
    elif (has_bridge_py or (step_dir / "post_bridge.py").exists()) and has_handoff_md:
        return {
            "type": "hybrid",
            "step_dir": step_dir,
            "prompt": str(step_dir / "handoff.md"),
            "determinism": determinism,
            "manifest": manifest,
        }
    elif has_handoff_md and not (has_bridge_py or (step_dir / "post_bridge.py").exists()) and not has_step_py:
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
            if name in ("sfx_library", "music_library", "brand_template", "creative_brief") and name not in state:
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


def gather_step_inputs(node_id: str, dag: dict, state: dict, manifest: dict = None, step_type: str = "unknown") -> dict:
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

    # Add a small explicit whitelist for globals that aren't DAG-routable
    for w_key in ["project_folder", "brand_template", "project_config"]:
        if w_key in state:
            inputs[w_key] = state[w_key]

    if manifest and manifest.get("state", {}).get("reads"):
        import sys
        print(f"Warning: Step '{node_id}' manifest contains deprecated 'state.reads'. Use DAG data_mapping instead.", file=sys.stderr)

    # Note: any other keys previously fetched from global state (like project_fps, project_resolution, sfx_library)
    # must now be explicitly mapped via DAG edges.

    # Process brand template and creative brief if they are in inputs (either via DAG or whitelist)
    if manifest:
        step_inputs = [inp.get("name") for inp in manifest.get("interface", {}).get("inputs", [])]
        brand_template_path = inputs.get("brand_template")
        
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

        # Inject creative brief markdown content when the step declares it
        if "creative_brief" in step_inputs:
            brief_path = inputs.get("creative_brief", "")
            if brief_path:
                try:
                    project_folder = inputs.get("project_folder", "")
                    if not os.path.isabs(brief_path) and project_folder:
                        brief_path = os.path.join(project_folder, brief_path)
                    if os.path.exists(brief_path):
                        with open(brief_path, "r", encoding="utf-8") as bf:
                            inputs["creative_brief"] = bf.read()
                except Exception as e:
                    import sys
                    print(f"Warning: failed to load creative brief: {e}", file=sys.stderr)

    if step_type == "llm_only" and manifest and "context_fields" in manifest:
        saved_project_folder = inputs.get("project_folder", "")
        saved_fps = inputs.get("project_fps")
        saved_res = inputs.get("project_resolution")
        saved_brand_template = inputs.get("brand_template")
        saved_creative_brief = inputs.get("creative_brief")
        
        from library.tools.context_projector import project_fields
        inputs = project_fields(inputs, manifest["context_fields"])
        
        inputs["project_folder"] = saved_project_folder
        if saved_fps is not None:
            inputs["project_fps"] = saved_fps
        if saved_res is not None:
            inputs["project_resolution"] = saved_res
        if saved_brand_template is not None:
            inputs["brand_template"] = saved_brand_template
        if saved_creative_brief is not None:
            inputs["creative_brief"] = saved_creative_brief

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
            f"  stderr: {result.stderr}"
        )
    
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Step produced invalid JSON:\n"
            f"  stdout: {result.stdout[:500]}\n"
            f"  stderr: {result.stderr[:500]}"
        )


@step_timer(step_id_kwarg="node_id")
def present_llm_step(prompt_path: str, inputs: dict, node_id: str, manifest: dict = None, full_auto: str = None, llm_timeout: int = 300) -> dict:
    """Present an LLM step and execute it using LLMClient or AGY backend.
    
    In automated mode, this calls the LLM and returns the parsed output.
    """
    raw_input_tokens = len(str(inputs).split()) * 1.3
    
    # For hybrid steps, inputs may not be projected yet. Project them now if needed.
    if manifest and "context_fields" in manifest:
        saved_project_folder = inputs.get("project_folder", "")
        saved_fps = inputs.get("project_fps")
        saved_res = inputs.get("project_resolution")
        saved_brand_template = inputs.get("brand_template", "default_brand")
        saved_creative_brief = inputs.get("creative_brief")
        
        from library.tools.context_projector import project_fields
        inputs = project_fields(inputs, manifest["context_fields"])
        
        inputs["project_folder"] = saved_project_folder
        if saved_fps is not None:
            inputs["project_fps"] = saved_fps
        if saved_res is not None:
            inputs["project_resolution"] = saved_res
        if saved_brand_template is not None:
            inputs["brand_template"] = saved_brand_template
        if saved_creative_brief is not None:
            inputs["creative_brief"] = saved_creative_brief
        
    projected_input_tokens = len(str(inputs).split()) * 1.3
        
    from library.tools.toon_serializer import json_to_toon
    toon_str = json_to_toon(inputs)
    
    toon_input_tokens = len(toon_str.split()) * 1.3
    reduction_pct = 100 * (1 - (toon_input_tokens / raw_input_tokens)) if raw_input_tokens > 0 else 0
    
    logger = get_logger()
    if logger:
        logger.log(
            step_id=node_id,
            event_type="llm_token_stats",
            token_count={
                "raw": int(raw_input_tokens),
                "projected": int(projected_input_tokens),
                "toon": int(toon_input_tokens),
                "reduction_pct": round(reduction_pct, 1)
            }
        )
        
    with open(prompt_path) as f:
        prompt = f.read()
        
    project_folder = inputs.get("project_folder", "")
    brand_template = inputs.get("brand_template", "default_brand")
    from library.tools.template_loader import TemplateLoader
    
    loader_instance = TemplateLoader(project_folder)
    constraints = loader_instance.get_brand_constraints(brand_template, node_id)
        
    from library.tools.qa_feedback_loop import LLMStepQA
    qa_loop = LLMStepQA(max_retries=2)
    current_context = toon_str
    best_output = None
    
    expected_schema_str = ""
    llm_manifest = None
    if manifest:
        outputs = manifest.get("interface", {}).get("outputs", [])
        llm_outputs = [o for o in outputs if o.get("name") not in inputs]
        expected_schema_str = json.dumps(llm_outputs)
        
        llm_manifest = dict(manifest)
        if "interface" in manifest:
            llm_manifest["interface"] = dict(manifest["interface"])
            llm_manifest["interface"]["outputs"] = llm_outputs

    for attempt in range(qa_loop.max_retries + 1):
        full_prompt = prompt + constraints + "\n\nContext:\n" + current_context
        parsed_result = None
        
        if full_auto == "agy":
            import datetime
            from pathlib import Path
            project_folder = inputs.get("project_folder", "")
            if not project_folder:
                raise LLMError("project_folder required in inputs for agy backend")
                
            requests_dir = Path(project_folder) / "pipeline_output" / "llm_requests"
            responses_dir = Path(project_folder) / "pipeline_output" / "llm_responses"
            requests_dir.mkdir(parents=True, exist_ok=True)
            responses_dir.mkdir(parents=True, exist_ok=True)
            
            req_file = requests_dir / f"{node_id}.json"
            res_file = responses_dir / f"{node_id}.json"
            
            if res_file.exists():
                res_file.unlink()
                
            req_data = {
                "step_id": node_id,
                "prompt": prompt,
                "context": current_context,
                "expected_schema": expected_schema_str,
                "project_folder": project_folder,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            
            with open(req_file, "w") as f:
                json.dump(req_data, f, indent=2)
                
            print(f"LLM_REQUEST_READY: {req_file}", file=sys.stdout)
            sys.stdout.flush()
            
            print(f"  Waiting for AGY response for {node_id} (timeout {llm_timeout}s)...", file=sys.stderr)
            start_wait = time.time()
            start_time_llm = time.time()
            
            while time.time() - start_wait < llm_timeout:
                if res_file.exists():
                    time.sleep(0.5)
                    try:
                        with open(res_file, "r") as f:
                            res_content = f.read()
                        parsed_result = json.loads(res_content)
                    except Exception as e:
                        raise LLMError(f"Failed to read or parse AGY LLM response as JSON: {e}")
                        
                    if logger:
                        response_tokens = len(res_content.split()) * 1.3
                        logger.log(
                            step_id=node_id,
                            event_type="llm_generation",
                            backend="agy",
                            latency=round(time.time() - start_time_llm, 2),
                            token_count={
                                "prompt": int(raw_input_tokens + len(prompt.split()) * 1.3),
                                "response": int(response_tokens)
                            }
                        )
                    break
                time.sleep(2)
                
            if parsed_result is None:
                raise LLMError(f"Timeout ({llm_timeout}s) waiting for AGY LLM response at {res_file}")

        else:
            llm_config = {}
            if manifest and "llm_config" in manifest:
                llm_config = manifest["llm_config"]
                
            provider = os.environ.get("PIPELINE_LLM_PROVIDER", llm_config.get("provider", "gemini"))
            model = os.environ.get("PIPELINE_LLM_MODEL", llm_config.get("model", "gemini-2.5-flash"))
            temperature = llm_config.get("temperature", 0.7)
            max_output_tokens = llm_config.get("max_output_tokens", 4096)
            
            from library.tools.llm_client import LLMClient
            client = LLMClient(provider, model, temperature=temperature, max_output_tokens=max_output_tokens)
            
            print(f"  Calling LLM ({provider}/{model}) for {node_id}...", file=sys.stderr)
            start_time_llm = time.time()
            result_text = client.generate(full_prompt, system="You are a video editor and pipeline orchestrator.")
            latency = time.time() - start_time_llm
            
            if full_auto == "api":
                if not result_text or result_text.strip() == "{}" or "missing_api_key" in result_text:
                    raise LLMError("API call failed or returned empty response.")
                    
                if logger:
                    response_tokens = len(result_text.split()) * 1.3
                    logger.log(
                        step_id=node_id,
                        event_type="llm_generation",
                        backend="api",
                        latency=round(latency, 2),
                        token_count={
                            "prompt": int(raw_input_tokens + len(prompt.split()) * 1.3),
                            "response": int(response_tokens)
                        }
                    )
                    
                try:
                    json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', result_text, re.DOTALL)
                    if json_match:
                        result_json = json_match.group(1)
                    else:
                        result_json = result_text
                    parsed_result = json.loads(result_json)
                except Exception as e:
                    raise LLMError(f"Failed to parse LLM JSON output from API: {e}\nRaw output: {result_text[:200]}")
            else:
                if not result_text or result_text.strip() == "{}" or "missing_api_key" in result_text:
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
                    
                    return {
                        "__status": "awaiting_llm",
                        "__prompt": prompt_path,
                        "__inputs_available": list(inputs.keys()),
                        "__context": current_context,
                    }
                    
                try:
                    json_match = re.search(r'```(?:json)?\s*(.*?)\s*```', result_text, re.DOTALL)
                    if json_match:
                        result_json = json_match.group(1)
                    else:
                        result_json = result_text
                    parsed_result = json.loads(result_json)
                except Exception as e:
                    print(f"  Warning: failed to parse LLM output as JSON: {e}", file=sys.stderr)
                    return {
                        "__status": "awaiting_llm",
                        "__prompt": prompt_path,
                        "__inputs_available": list(inputs.keys()),
                        "__context": current_context,
                        "__llm_raw_output": result_text
                    }
                    
        passed, feedback = qa_loop.run_checks(node_id, parsed_result, llm_manifest if manifest else None, validate_step_output)
        if passed:
            return parsed_result
            
        best_output = parsed_result
        if attempt < qa_loop.max_retries:
            print(f"  QA failed on attempt {attempt+1}, retrying: {feedback}", file=sys.stderr)
            current_context += f"\n\nQA Feedback from previous attempt:\nThe previous output failed validation: {feedback}\nPlease correct this."
            
    print(f"  Warning: QA failed after {qa_loop.max_retries} retries for {node_id}, proceeding with best attempt.", file=sys.stderr)
    return best_output or {}




def run_subprocess(script_path: Path, inputs: dict) -> dict:
    """Run a Python script via subprocess with JSON stdin/stdout."""
    result = subprocess.run(
        ["python3", str(script_path)],
        input=json.dumps(inputs),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Script {script_path.name} failed (exit {result.returncode}):\n"
            f"  stderr: {result.stderr[:500]}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Script {script_path.name} produced invalid JSON:\n"
            f"  stdout: {result.stdout[:500]}\n"
            f"  stderr: {result.stderr[:500]}"
        )

@step_timer(step_id_kwarg="node_id")
def run_hybrid_step(step_dir: Path, inputs: dict, node_id: str, manifest: dict = None, full_auto: str = None, llm_timeout: int = 300) -> dict:
    """Run a hybrid step: pre-bridge handles context compression, LLM makes creative decision,
    and post-bridge resolves numerical constraints."""
    pre_bridge = step_dir / "bridge.py"
    post_bridge = step_dir / "post_bridge.py"
    prompt_path = str(step_dir / "handoff.md")
    
    compressed = dict(inputs)
    pre_output = {}
    if pre_bridge.exists():
        try:
            pre_output = run_subprocess(pre_bridge, inputs)
            compressed.update(pre_output)
            if "project_folder" not in compressed or not compressed["project_folder"]:
                compressed["project_folder"] = inputs.get("project_folder", "")
        except Exception as e:
            raise PreBridgeError(f"Pre-bridge failed: {e}")
    
    # LLM creative decision on compressed context
    try:
        llm_output = present_llm_step(prompt_path, compressed, node_id, manifest, full_auto, llm_timeout)
    except Exception as e:
        raise LLMError(f"LLM generation failed: {e}")
    
    if isinstance(llm_output, dict) and llm_output.get("__status") == "awaiting_llm":
        return llm_output
        
    if post_bridge.exists():
        try:
            merge_data = dict(inputs)
            merge_data.update(pre_output)
            if isinstance(llm_output, dict):
                merge_data.update(llm_output)
            else:
                merge_data["llm_raw_response"] = llm_output
            final = run_subprocess(post_bridge, merge_data)
            
            # Ensure bridge outputs are preserved if post-bridge didn't explicitly return them
            result = dict(pre_output)
            if isinstance(final, dict):
                result.update(final)
            return result
        except Exception as e:
            raise PostBridgeError(f"Post-bridge failed: {e}")
    
    result = dict(pre_output)
    if isinstance(llm_output, dict):
        result.update(llm_output)
    return result


def validate_step_output(node_id: str, output: dict, manifest: dict = None) -> None:
    """Validate a step's output against its manifest declarations."""
    if not manifest or "interface" not in manifest or "outputs" not in manifest["interface"]:
        return

    outputs_spec = manifest["interface"]["outputs"]
    for spec in outputs_spec:
        key = spec.get("name")
        if not key:
            continue
            
        is_required = spec.get("required", True)
        
        if key not in output or output[key] is None:
            if is_required:
                raise RuntimeError(f"Step '{node_id}' output missing required key: '{key}'")
            else:
                import sys
                print(f"  Warning: Step '{node_id}' output missing optional key: '{key}'", file=sys.stderr)
            continue
            
        val = output[key]
        expected_type_str = spec.get("type", "").lower()
        if expected_type_str:
            type_map = {
                "dict": dict, "object": dict,
                "list": list, "array": list,
                "str": str, "string": str,
                "int": int, "float": float, "number": (int, float),
                "bool": bool, "boolean": bool
            }
            expected_type = type_map.get(expected_type_str)
            if expected_type and not isinstance(val, expected_type):
                raise RuntimeError(f"Step '{node_id}' output '{key}' expected type {expected_type_str}, got {type(val).__name__}")
                
        if is_required:
            is_empty = False
            if isinstance(val, (list, dict, str)) and len(val) == 0:
                is_empty = True
            elif isinstance(val, int) and val == 0 and key.startswith("total_") and key != "total_failed":
                is_empty = True
                
            if is_empty:
                raise RuntimeError(f"Step '{node_id}' output '{key}' is semantically empty: {val}")


# ── Main Runner ─────────────────────────────────────────────────────

def run_pipeline(
    project_dir: str,
    from_step: str = None,
    single_step: str = None,

    auto_mode: bool = False,
    review_mode: bool = False,
    resume_mode: bool = False,
    full_auto: str = None,
    llm_timeout: int = 300,
):
    """Execute the pipeline DAG."""
    # Initialize logger
    get_logger(project_dir)
    
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
    
    current_phase = None
    
    for node_id in steps_to_run:
        # Extract phase from node_id (e.g. 'step_1_01_scan' -> '1')
        parts = node_id.split('_')
        phase = parts[1] if len(parts) > 1 else None
        if phase and current_phase and phase != current_phase:
            print(f"\n  [Phase Transition] {current_phase} -> {phase}. Freeing VRAM...", file=sys.stderr)
            unload_all()
        if phase:
            current_phase = phase
            
        node = nodes[node_id]
        step_dir = get_step_dir(node)
        impl = get_step_implementation(step_dir)
        
        # Check if already completed
        if node_id in state.get("steps_completed", {}):
            if resume_mode:
                from library.tools.review_gate import load_gate_feedback, apply_feedback_to_output
                feedback = load_gate_feedback(project_dir, node_id)
                if feedback:
                    if feedback.action == "pending":
                        print(f"  ⏸  {node_id}: gate is pending, stopping.", file=sys.stderr)
                        break
                    elif feedback.action == "rejected":
                        print(f"  ✗  {node_id}: rejected by reviewer.", file=sys.stderr)
                        failed.append(node_id)
                        state.setdefault("failed_steps", []).append(node_id)
                        save_pipeline_state(project_dir, state)
                        break
                    elif feedback.action == "revised":
                        outputs = state.get("step_outputs", {})
                        step_output = outputs.get(node_id, {})
                        merged = apply_feedback_to_output(step_output, feedback)
                        outputs[node_id] = merged
                        state["step_outputs"] = outputs
                        save_pipeline_state(project_dir, state)
                        print(f"  ⏭  {node_id}: revised output applied", file=sys.stderr)
            
            print(f"  ⏭  {node_id}: already completed", file=sys.stderr)
            completed.append(node_id)
            continue
        
        print(f"\n  ▶  Step: {node_id} ({node['name']})", file=sys.stderr)
        print(f"     Type: {impl['type']} | Dir: {step_dir}", file=sys.stderr)
        

        
        # Gather inputs from upstream (pass manifest for optional-input checking)
        inputs = gather_step_inputs(node_id, dag, state, manifest=impl.get("manifest"), step_type=impl.get("type", "unknown"))
        print(f"     Inputs: {list(inputs.keys())}", file=sys.stderr)
        
        try:
            start_time = time.time()
            
            def execute_step_once():
                if impl["type"] == "deterministic":
                    return run_deterministic_step(impl["entry"], inputs), False
                elif impl["type"] == "deterministic_with_llm":
                    step_output = run_deterministic_step(impl["entry"], inputs)
                    merged_inputs = dict(inputs)
                    merged_inputs.update(step_output)
                    llm_output = present_llm_step(impl["prompt"], merged_inputs, node_id, manifest=impl.get("manifest"), full_auto=full_auto, llm_timeout=llm_timeout)
                    if isinstance(llm_output, dict) and llm_output.get("__status") == "awaiting_llm":
                        return llm_output, True
                    if isinstance(llm_output, dict):
                        step_output.update(llm_output)
                    return step_output, False
                elif impl["type"] == "hybrid":
                    if auto_mode:
                        # In auto mode, use the pre-bridge context output as the final step output.
                        step_dir_path = impl["step_dir"]
                        pre_bridge = step_dir_path / "bridge.py"
                        if pre_bridge.exists():
                            return run_subprocess(pre_bridge, inputs), False
                        else:
                            return inputs, False
                    else:
                        output = run_hybrid_step(impl["step_dir"], inputs, node_id, impl.get("manifest"), full_auto, llm_timeout)
                        return output, (isinstance(output, dict) and output.get("__status") == "awaiting_llm")
                elif impl["type"] == "llm_only":
                    output = present_llm_step(impl["prompt"], inputs, node_id, manifest=impl.get("manifest"), full_auto=full_auto, llm_timeout=llm_timeout)
                    return output, (isinstance(output, dict) and output.get("__status") == "awaiting_llm")
                else:
                    raise RuntimeError(f"Unknown implementation type: {impl['type']}")
                    
            error_policy = node.get("error_policy", {}).get("policy", "fail")
            max_retries = node.get("error_policy", {}).get("max_retries", 3)
            
            success = False
            for attempt in range(1, max_retries + 2):
                try:
                    output, is_awaiting = execute_step_once()
                    success = True
                    break
                except Exception as step_e:
                    is_trans = _is_transient_error(step_e)
                    error_type = step_e.__class__.__name__
                    if attempt <= max_retries and (error_policy == "retry" or is_trans):
                        print(f"     ✗ FAILED ({error_type}): {step_e}", file=sys.stderr)
                        print(f"     [Retry {attempt}/{max_retries} due to transient error/policy]", file=sys.stderr)
                        time.sleep(2 ** attempt)
                    else:
                        # Give up
                        print(f"     ✗ FAILED ({error_type}): {step_e}", file=sys.stderr)
                        logger = get_logger()
                        if logger:
                            logger.log(step_id=node_id, event_type="step_failed", error=str(step_e))
                        state.setdefault("failed_steps", []).append(node_id)
                        state.setdefault("step_errors", {})[node_id] = str(step_e)
                        save_pipeline_state(project_dir, state)
                        failed.append(node_id)
                        break

            if not success:
                print(f"     Stopping pipeline due to failure.", file=sys.stderr)
                break
                
            if is_awaiting:
                awaiting_llm.append(node_id)
                print(f"     ⏸ Awaiting LLM completion", file=sys.stderr)
                break
                
            # Success logic
            elapsed = time.time() - start_time

            # Wrap LLM output in expected manifest key if missing (for LLM steps without post_bridge)
            if impl["type"] in ("llm_only", "hybrid"):
                has_post_bridge = impl["type"] == "hybrid" and (impl["step_dir"] / "post_bridge.py").exists()
                if not has_post_bridge and impl.get("manifest") and "interface" in impl["manifest"]:
                    outputs_spec = impl["manifest"]["interface"].get("outputs", [])
                    if len(outputs_spec) == 1 and isinstance(output, dict):
                        key = outputs_spec[0].get("name")
                        if key and key not in output:
                            output = {key: output}

            print(f"     ✓ Completed in {elapsed:.1f}s", file=sys.stderr)
            print(f"     Outputs: {list(output.keys())}", file=sys.stderr)
            
            # Check 1.3: LLM Response Clip ID Validation
            if impl["type"] in ("llm_only", "hybrid") and not is_awaiting:
                catalog = state.get("step_outputs", {}).get("catalog", {}).get("clips", [])
                catalog_ids = {c.get("clip_id") for c in catalog if c.get("clip_id")}
                catalog_paths = {c.get("source_path") for c in catalog if c.get("source_path")}
                
                def extract_refs(obj, ids, paths):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k == "clip_id" and isinstance(v, str): ids.add(v)
                            elif k in ("source", "source_path", "clip") and isinstance(v, str): paths.add(v)
                            else: extract_refs(v, ids, paths)
                    elif isinstance(obj, list):
                        for item in obj: extract_refs(item, ids, paths)
                
                out_ids = set()
                out_paths = set()
                extract_refs(output, out_ids, out_paths)
                
                unknown_ids = out_ids - catalog_ids
                unknown_paths = out_paths - catalog_paths
                if unknown_ids or unknown_paths:
                    print(f"     ⚠ WARNING: LLM returned unrecognized clip references.", file=sys.stderr)
                    if unknown_ids: print(f"       Unknown clip_ids: {unknown_ids}", file=sys.stderr)
                    if unknown_paths: print(f"       Unknown paths: {unknown_paths}", file=sys.stderr)
            
            # Validate output against manifest
            try:
                validate_step_output(node_id, output, impl.get("manifest"))
            except RuntimeError as e:
                print(f"     ✗ INVALID OUTPUT: {e}", file=sys.stderr)
                logger = get_logger()
                if logger:
                    logger.log(step_id=node_id, event_type="step_failed", error=str(e))
                state.setdefault("failed_steps", []).append(node_id)
                state.setdefault("step_errors", {})[node_id] = str(e)
                save_pipeline_state(project_dir, state)
                failed.append(node_id)
                break
            
            state.setdefault("step_outputs", {})[node_id] = output
            state.setdefault("steps_completed", {})[node_id] = {
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "elapsed_s": round(elapsed, 1),
            }
            if auto_mode and impl["type"] == "hybrid":
                state["steps_completed"][node_id]["note"] = "auto-completed via bridge (context only)"
            save_pipeline_state(project_dir, state)
            
            if node_id == "mesh_spine":
                step_outputs = state.get("step_outputs", {})
                mesh_output = step_outputs.get('mesh_spine', {})
                total_duration = mesh_output.get('total_duration', mesh_output.get('duration_seconds', 0))
                MIN_DURATION = 30  # seconds, for shortform
                if total_duration > 0 and total_duration < MIN_DURATION:
                    logger.warning(f"Mesh spine duration ({total_duration:.1f}s) below minimum ({MIN_DURATION}s). Consider using more footage.")
            
            _export_step_for_review(project_dir, node_id, node["name"], output, state)
            
            if review_mode:
                _save_review_gate(project_dir, node_id, node["name"], output, inputs, state)
                print(f"     ⏸ Review gate saved. Inspect at dashboard.", file=sys.stderr)
                
                try:
                    from library.tools.step_exporter import load_step_summary, generate_summary
                    summary_md = load_step_summary(project_dir, node_id)
                    if not summary_md:
                        summary_md = generate_summary(node_id, node["name"], output)
                except Exception:
                    summary_md = "Review required for this step."

                completed.append(node_id)
                break
                
            completed.append(node_id)

                
        except Exception as e:
            # Unhandled errors outside step execution
            print(f"     ✗ FAILED UNEXPECTEDLY: {e}", file=sys.stderr)
            logger = get_logger()
            if logger:
                logger.log(step_id=node_id, event_type="step_failed", error=str(e))
            state.setdefault("failed_steps", []).append(node_id)
            save_pipeline_state(project_dir, state)
            failed.append(node_id)
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


def _export_step_for_review(project_dir, step_id, step_name, output, state=None):
    """Export step output for dashboard review (non-critical, best-effort)."""
    try:
        from library.tools.step_exporter import export_step_output
        export_step_output(project_dir, step_id, step_name, output)
    except Exception as e:
        import traceback
        err_msg = f"Dashboard export failed for {step_id}: {e}\n{traceback.format_exc()}"
        print(f"     ⚠ {err_msg}", file=sys.stderr)
        if state is not None:
            state.setdefault("warnings", []).append(err_msg)
            save_pipeline_state(project_dir, state)


def _save_review_gate(project_dir, step_id, step_name, output, inputs, state=None):
    """Save a review gate snapshot (non-critical, best-effort)."""
    try:
        from library.tools.review_gate import save_gate_snapshot
        save_gate_snapshot(
            project_dir, step_id, step_name, output,
            upstream_context={k: str(type(v).__name__) for k, v in inputs.items()},
        )
    except Exception as e:
        import traceback
        err_msg = f"Review gate save failed for {step_id}: {e}\n{traceback.format_exc()}"
        print(f"     ⚠ {err_msg}", file=sys.stderr)
        if state is not None:
            state.setdefault("warnings", []).append(err_msg)
            save_pipeline_state(project_dir, state)


def main():
    parser = argparse.ArgumentParser(description="Pipeline Runner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--project", help="Project directory (absolute path)")
    group.add_argument("--slug", help="Project slug (looked up from project registry)")
    parser.add_argument("--from", "--start-from", dest="from_step", help="Start from this step")
    parser.add_argument("--step", help="Run only this step")

    parser.add_argument("--auto", action="store_true", 
                       help="Auto-complete hybrid steps (use bridge output as final)")
    parser.add_argument("--review", action="store_true",
                       help="Enable review gates: export step outputs and save gate snapshots for dashboard review")
    parser.add_argument("--resume", action="store_true",
                       help="Resume pipeline from pending gates")
    parser.add_argument("--full-auto", choices=["agy", "api"],
                       help="Run full pipeline autonomously using specified LLM backend")
    parser.add_argument("--llm-timeout", type=int, default=300,
                       help="Timeout for LLM response in agy backend")
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

        auto_mode=args.auto,
        review_mode=args.review,
        resume_mode=args.resume,
        full_auto=args.full_auto,
        llm_timeout=args.llm_timeout,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project")
    parser.add_argument("--slug")
    args, _ = parser.parse_known_args()
    
    project_dir = args.project
    if args.slug:
        try:
            from tools.paths import project_root
            project_dir = str(project_root(args.slug))
        except (ImportError, FileNotFoundError):
            pass
            
    pid_file = os.path.join(project_dir, "pipeline.pid") if project_dir else None
    if pid_file:
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
            
    try:
        main()
    finally:
        if pid_file and os.path.exists(pid_file):
            try:
                os.remove(pid_file)
            except OSError:
                pass
