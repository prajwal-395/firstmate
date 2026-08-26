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
import threading
import time
import argparse
from pathlib import Path
from collections import deque
import re
import logging

from library.tools.pipeline_logger import get_logger, step_timer
from library.tools import run_control
from library.tools import footage_identity, step_ledger
from library.tools.project_layout import Area, ProjectLayout
from library.tools import provenance

logger = logging.getLogger(__name__)

class PreBridgeError(Exception): pass
class LLMError(Exception): pass
class PostBridgeError(Exception): pass

def _is_transient_error(e: Exception) -> bool:
    if isinstance(e, subprocess.TimeoutExpired):
        return True
    msg = str(e).lower()
    return any(x in msg for x in ["network", "rate limit", "timeout", "timed out", "503", "429", "connection", "socket", "500", "502", "too many requests"])


# Two entries, and both are load-bearing. `library/tools` is what makes the
# bare `from model_lifecycle import ...` below work; `library` is what makes
# the three `from tools.paths import ...` sites further down resolve. Without
# the second, every one of those raised ImportError into an `except
# ImportError: pass` and the shared asset libraries were never injected -
# PIPELINE_SFX_LIBRARY and PIPELINE_MUSIC_LIBRARY reached no run, and step
# 0.01 failed any project whose state did not already carry a path.
# tests/test_runner_library_paths.py asserts both imports work.
_LIBRARY_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_LIBRARY_DIR / "tools"))
sys.path.insert(0, str(_LIBRARY_DIR))
from model_lifecycle import unload_all


# ── Path Configuration ──────────────────────────────────────────────

PILOT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
LIBRARY_ROOT = PILOT_ROOT / "library"
STEPS_ROOT = LIBRARY_ROOT / "steps"
DAG_PATH = LIBRARY_ROOT / "processes/edit_video/dag.json"

# Values that belong to the RUN rather than to any upstream step, injected
# into state by load_pipeline_state.  A step gets one only by declaring it
# in its own manifest's interface.inputs - see gather_step_inputs.
PROCESS_LEVEL_INPUTS = ("sfx_library", "music_library", "creative_brief")


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


def _phase_of(dag_node: dict) -> str:
    """The pipeline phase digit a DAG node belongs to, from its step_ref.

    `steps/step_1_04_temporal_index` -> "1".  Read off the step_ref and
    not the node id, which is a short name like `temporal_index` and
    carries no phase at all.
    """
    name = Path(dag_node.get("step_ref", "")).name
    parts = name.split("_")
    if len(parts) > 1 and parts[0] == "step" and parts[1].isdigit():
        return parts[1]
    return ""


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
    state_path = str(ProjectLayout(project_dir).pipeline_data_path)
    if os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = {
            "pipeline_version": "1.0",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            # Two ledgers, two lifetimes. See library/tools/step_ledger.py.
            step_ledger.LEDGER_KEY[step_ledger.PREFLIGHT]: {},
            step_ledger.LEDGER_KEY[step_ledger.EDIT]: {},
            "step_outputs": {},
        }
    # Always inject project_folder from CLI
    state["project_folder"] = project_dir
    
    # Inject shared library paths from environment (via paths.py).
    # Priority: existing state value > env var > manifest default.
    #
    # Not wrapped in `except ImportError: pass` any more. It was, and the
    # import was broken, so the swallow is the whole reason this went
    # unnoticed: a fresh project got no sfx_library at all and step 0.01
    # failed with "No sfx_library path provided" while the environment had
    # a perfectly good library in PIPELINE_SFX_LIBRARY.
    from tools.paths import sfx_library_path, music_library_path
    if "sfx_library" not in state:
        sfx = sfx_library_path()
        if sfx:
            state["sfx_library"] = sfx
    if "music_library" not in state:
        music = music_library_path()
        if music:
            state["music_library"] = music

    # The captain's creative direction, declared per project.
    #
    # Seven handoffs tell the LLM to "read it in full before making any
    # creative decisions" and none could ever be given one: the loader in
    # gather_step_inputs gates on the step manifest declaring the input,
    # no manifest declared it, the key was in no whitelist, and the
    # process manifest had no entry to fall back to. Three independent
    # breaks, so writing `creative_brief` into a project.yaml did nothing
    # at all, silently. See docs/RUN_001_END_TO_END.md section 5.
    #
    # Read here rather than in step 1.01 because it belongs to the run,
    # not to the scan: steps in phases 2, 3 and 4 need it and none of them
    # is downstream of scan's project_config. Absolute paths are kept as
    # given, so a brief may live in a read-only planning tree outside the
    # project and is never copied in.
    if "creative_brief" not in state:
        project_yaml = os.path.join(project_dir, "project.yaml")
        if os.path.exists(project_yaml):
            try:
                import yaml
                with open(project_yaml, "r", encoding="utf-8") as f:
                    y = yaml.safe_load(f) or {}
                brief = (y.get("creative_brief")
                         or (y.get("pipeline") or {}).get("creative_brief")
                         or "")
                if brief:
                    state["creative_brief"] = brief
            except Exception as e:
                import sys
                print(f"Warning: failed to read creative_brief from "
                      f"project.yaml: {e}", file=sys.stderr)

    # The brand this project renders under, declared per project.
    #
    # Read here for the same reason creative_brief is: it belongs to the
    # RUN, not to any one step, and no DAG edge routes it.  Nothing
    # populated this key at all before - see project_template_name() in
    # library/tools/brand_registry.py for what that cost.
    #
    # Stored as the NAME the project declared, not a path.  An empty
    # declaration is left OUT of state so a project that names no template
    # keeps resolving through the same empty-reference path as before, and
    # `resolve_template_reference("")` sends that to default_brand on disk.
    if "brand_template" not in state:
        from library.tools.brand_registry import project_template_name
        declared = project_template_name(project_dir)
        if declared:
            state["brand_template"] = declared

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


def _record_step_failure(state: dict, node_id: str, message: str) -> None:
    """Record a step failure once, so the ledger reflects state not history.

    `failed_steps` used to be an append-only log: 69 entries accumulated
    across runs, a step that later succeeded stayed on the list, and the
    run summary ignored the whole thing anyway.
    """
    failed = state.setdefault("failed_steps", [])
    if node_id not in failed:
        failed.append(node_id)
    state.setdefault("step_errors", {})[node_id] = message
    # The ledger entry goes; the per-clip artifacts on disk stay. A
    # transcription that died on clip 12 of 17 keeps the eleven indices it
    # wrote, so the re-run pays for the remainder and not the lot.
    step_ledger.forget(state, node_id)


def _clear_step_failure(state: dict, node_id: str) -> None:
    """Drop a step's recorded failure once it has actually succeeded."""
    failed = state.get("failed_steps")
    if failed and node_id in failed:
        state["failed_steps"] = [s for s in failed if s != node_id]
    state.get("step_errors", {}).pop(node_id, None)


# One backup per RUN, taken before this process first overwrites the
# state.  Not per save: this function runs after every step, so a
# per-save policy would spend the whole retention window inside a single
# run and lose the thing the captain's nine hand-made `.bak*` files were
# actually protecting - the state as it stood BEFORE the run started.
# The store, the naming and the bound are in library/tools/project_layout.py.
_BACKED_UP_THIS_PROCESS: set = set()


def save_pipeline_state(project_dir: str, state: dict):
    """Save pipeline state to project."""
    layout = ProjectLayout(project_dir)
    key = str(layout.root)
    if key not in _BACKED_UP_THIS_PROCESS:
        _BACKED_UP_THIS_PROCESS.add(key)
        layout.backup_pipeline_data(label="run")
    state_path = str(layout.pipeline_data_path)
    state["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ── The preflight / edit split ──────────────────────────────────────
#
# Three mechanisms, and only three:
#
#   1. Every step manifest declares classification.stage, so which side a
#      step is on is DECLARED rather than hardcoded here.
#   2. --rerun is the only supported way to redo finished work. Before it
#      existed, --from merely trimmed the plan and the skip-if-finished
#      check fired anyway, so the only route was to move
#      pipeline_data.json aside - which is how project 001 paid for forty
#      minutes of WhisperX twice.
#   3. A source-identity check invalidates a clip's cached analysis when
#      the footage behind it is replaced. That is what makes "preflight is
#      skipped once done" safe and not merely fast.
#
# Deliberately NOT here: a caching framework or a content-addressed
# artifact store. The artifacts are already per clip on disk.


def _load_step_manifest(step_dir: Path) -> dict:
    """A step's manifest, read straight off disk.

    Deliberately not routed through `get_step_implementation`: the stage
    is a property of the step as it exists in the repository, and reading
    it here keeps the split honest even where the implementation lookup
    is stubbed.
    """
    manifest_path = step_dir / "manifest.json"
    if not manifest_path.exists():
        raise step_ledger.LedgerError(
            f"Step directory {step_dir} has no manifest.json, so it declares "
            f"no stage. Every step must declare one of "
            f"{list(step_ledger.STAGES)}."
        )
    with open(manifest_path) as f:
        return json.load(f)


def _manifest_map(nodes: dict) -> dict:
    return {node_id: _load_step_manifest(get_step_dir(node))
            for node_id, node in nodes.items()}


def _stage_map(manifests: dict) -> dict:
    """{node_id: stage} for every node in the DAG, read from its manifest."""
    return {node_id: step_ledger.stage_of(manifest, node_id)
            for node_id, manifest in manifests.items()}


def _delete_clip_artifacts(project_dir: str, manifest: dict, clip_id: str,
                           fingerprint_sets: list) -> list:
    """Delete one clip's declared artifacts. Returns the paths removed.

    ``fingerprint_sets`` are the footage records to translate the clip id
    into a file stem with - both the recorded one and the current one,
    because a renumbered clip id points at a different stem in each.
    """
    patterns = step_ledger.per_clip_artifacts(manifest)
    if not patterns:
        return []
    stems = {""}
    for fps in fingerprint_sets:
        stem = footage_identity.stem_for(clip_id, fps or {})
        if stem:
            stems.add(stem)
    removed = []
    for stem in stems:
        for path in step_ledger.artifact_paths(project_dir, patterns,
                                               clip_id, stem):
            if "{" in path:  # a pattern needing a stem we could not resolve
                continue
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    removed.append(path)
                except OSError as e:
                    print(f"     ⚠ could not remove {path}: {e}",
                          file=sys.stderr)
    return removed


def apply_rerun_requests(project_dir: str, state: dict, targets: list,
                         stage_by_node: dict, manifests: dict) -> list:
    """Honour every --rerun target. The operator's word beats the ledger.

    A target that names nothing raises, because a typo that silently
    re-runs nothing is how an operator concludes the flag does not work.
    """
    if not targets:
        return []

    current = {}
    try:
        files, _skipped = footage_identity.enumerate_footage(project_dir)
        current = footage_identity.fingerprints_for(files)
    except (OSError, FileNotFoundError):
        pass
    recorded = state.get(step_ledger.SOURCE_FINGERPRINTS_KEY, {})

    applied = []
    for raw in targets:
        kind, value = step_ledger.parse_rerun_target(raw, stage_by_node)

        if kind == "stage":
            cleared = step_ledger.reset_stage(state, value, stage_by_node)
            applied.append(f"stage {value}: cleared {len(cleared)} steps")
            continue

        if kind == "step":
            step_id = value
            step_ledger.forget(state, step_id)
            state.get("step_outputs", {}).pop(step_id, None)
            _clear_step_failure(state, step_id)
            # "Re-run the step" has to mean recompute. A per-clip step
            # reuses whatever is still on disk, so leaving the artifacts
            # in place would make the request a no-op.
            removed = []
            for clip_id in sorted(set(current) | set(recorded)):
                removed += _delete_clip_artifacts(
                    project_dir, manifests.get(step_id, {}), clip_id,
                    [recorded, current])
            applied.append(
                f"step {step_id}: ledger cleared"
                + (f", {len(removed)} artifacts removed" if removed else ""))
            continue

        step_id, _, clip_id = value.partition(":")
        manifest = manifests.get(step_id, {})
        if not step_ledger.per_clip_artifacts(manifest):
            raise step_ledger.LedgerError(
                f"--rerun {raw!r}: step '{step_id}' declares no "
                f"per_clip_artifacts, so it has no per-clip granularity to "
                f"re-run. Use --rerun {step_id} to re-run the whole step."
            )
        if current and clip_id not in current and clip_id not in recorded:
            raise step_ledger.LedgerError(
                f"--rerun {raw!r}: this project has no clip {clip_id!r}. "
                f"Known: {', '.join(sorted(current)) or '(none)'}"
            )
        removed = _delete_clip_artifacts(project_dir, manifest, clip_id,
                                         [recorded, current])
        # The step's own output has to be re-emitted, so its ledger entry
        # goes too. Every OTHER clip's artifact survives, so the re-run
        # recomputes exactly this one.
        step_ledger.forget(state, step_id)
        _clear_step_failure(state, step_id)
        applied.append(
            f"clip {clip_id} of {step_id}: {len(removed)} artifacts removed")

    return applied


def apply_source_identity(project_dir: str, state: dict, stage_by_node: dict,
                          manifests: dict):
    """Invalidate cached preflight work whose source footage has changed.

    Identity is size plus mtime per file - see
    ``library/tools/footage_identity.py`` for why it is not a content
    hash. The comparison is against the footage the preflight stage last
    saw, recorded in ``source_fingerprints``.

    On a project that has never carried that record, the current footage
    is ADOPTED without invalidating anything: an existing ledger is the
    operator's claim and there is no evidence against it. The check earns
    its keep from the second run onward.
    """
    try:
        files, _skipped = footage_identity.enumerate_footage(project_dir)
    except (OSError, FileNotFoundError):
        # No raw/ yet, or unreadable. `scan` will fail with a real message.
        return None

    current = footage_identity.fingerprints_for(files)
    recorded = state.get(step_ledger.SOURCE_FINGERPRINTS_KEY)
    if not recorded:
        state[step_ledger.SOURCE_FINGERPRINTS_KEY] = current
        return None

    if recorded and not current:
        # Every clip gone at once is far more likely an unmounted volume
        # or a mistyped project path than a deliberate emptying, and the
        # invalidation it would trigger is total.  Refuse, loudly.
        print("  ⚠ The raw footage directory enumerates to nothing while "
              f"{len(recorded)} clips are on record. Refusing to invalidate "
              "the preflight work - check the footage is where it should be. "
              "Use --rerun preflight if the project really has been emptied.",
              file=sys.stderr)
        return None

    delta = footage_identity.compare(recorded, current)
    if not delta.footage_changed:
        return delta

    print(f"  ⚠ Source footage changed ({delta.describe()}) - "
          f"invalidating the preflight work that depended on it",
          file=sys.stderr)

    stale = delta.stale_clip_ids
    for node_id, stage in sorted(stage_by_node.items()):
        if stage != step_ledger.PREFLIGHT:
            continue
        manifest = manifests.get(node_id, {})
        patterns = step_ledger.per_clip_artifacts(manifest)
        if not patterns:
            # Whole-step preflight work - the scan and the catalog. Both
            # describe the footage SET, and the set moved.
            if step_ledger.is_completed(state, node_id):
                step_ledger.forget(state, node_id)
                print(f"     - {node_id}: re-runs (describes the whole set)",
                      file=sys.stderr)
            continue
        removed = []
        for clip_id in stale:
            removed += _delete_clip_artifacts(project_dir, manifest, clip_id,
                                              [recorded, current])
        if removed or step_ledger.is_completed(state, node_id):
            step_ledger.forget(state, node_id)
            print(f"     - {node_id}: re-runs for "
                  f"{', '.join(stale) or 'no clips'} "
                  f"({len(removed)} artifacts removed)", file=sys.stderr)

    state[step_ledger.SOURCE_FINGERPRINTS_KEY] = current
    return delta


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

    # Add a small explicit whitelist for globals that aren't DAG-routable.
    #
    # brand_template is NOT on it. `state["brand_template"]` holds the
    # REFERENCE the project declared (a name, or a path); the one reader
    # that declares this input - step_5_01_color_grade - does
    # `brand_template.get("style", {})` and needs the resolved TEMPLATE.
    # Broadcasting the string under the same key would hand that step a
    # str and crash it, so the brand block below injects the resolved
    # template to the steps that asked for it and nothing else.
    for w_key in ["project_folder", "project_config"]:
        if w_key in state:
            inputs[w_key] = state[w_key]

    # Process-level inputs (sfx_library, music_library) reach a step only if
    # that step's OWN manifest declares it needs them.  An entry node has no
    # incoming edges, so a data_mapping cannot route anything to it:
    # `validate_sfx_library` is an entry node whose step.py exits 1 on a
    # missing `sfx_library`, and its manifest declared `inputs: []`, so the
    # step could never receive the one value it requires.  Declaring the
    # input is what asks for it; nothing is broadcast to steps that did not.
    if manifest:
        declared = {inp.get("name") for inp in
                    manifest.get("interface", {}).get("inputs", [])}
        for g_key in PROCESS_LEVEL_INPUTS:
            if g_key in declared and g_key in state and g_key not in inputs:
                inputs[g_key] = state[g_key]

    if manifest and manifest.get("state", {}).get("reads"):
        import sys
        print(f"Warning: Step '{node_id}' manifest contains deprecated 'state.reads'. Use DAG data_mapping instead.", file=sys.stderr)

    # Resolve the project's brand template for the steps that declare it.
    if manifest:
        step_inputs = [inp.get("name") for inp in manifest.get("interface", {}).get("inputs", [])]
        # Read the reference off STATE, not off inputs: it is a run-level
        # global with no DAG edge, and reading it back out of `inputs`
        # only worked while something put it there.  Nothing did.
        brand_reference = state.get("brand_template", "")
        wants = [x for x in ("brand_template", "brand_style", "brand_effect",
                             "brand_content") if x in step_inputs]

        if wants:
            import sys
            if str(LIBRARY_ROOT.parent) not in sys.path:
                sys.path.append(str(LIBRARY_ROOT.parent))
            from dataclasses import asdict
            from library.tools.brand_registry import (
                resolve_template_reference, query_slots)
            # A declared template that cannot be resolved RAISES.  This was
            # `except Exception: print("Warning: ...")`, which is the swallow
            # half of the same defect: even once the name reached here, a
            # typo would have printed one line into a log and rendered the
            # in-code default anyway.
            bt = resolve_template_reference(brand_reference)
            if "brand_template" in step_inputs:
                inputs["brand_template"] = asdict(bt)
            if "brand_style" in step_inputs:
                inputs["brand_style"] = query_slots(bt, "style")
            if "brand_effect" in step_inputs:
                inputs["brand_effect"] = query_slots(bt, "effect")
            if "brand_content" in step_inputs:
                inputs["brand_content"] = query_slots(bt, "content")

        # Inject creative brief markdown content when the step declares it.
        #
        # A declared brief that cannot be read RAISES. It used to warn and
        # leave `inputs["creative_brief"]` holding the path string, which
        # would then be pasted into the prompt as if it were the brief -
        # the step would report success having read a filename. A brief
        # the captain asked for and the pipeline could not open is a
        # contract violation, not a degraded mode.
        if "creative_brief" in step_inputs:
            brief_path = inputs.get("creative_brief", "")
            if brief_path:
                project_folder = inputs.get("project_folder", "")
                if not os.path.isabs(brief_path) and project_folder:
                    brief_path = os.path.join(project_folder, brief_path)
                try:
                    with open(brief_path, "r", encoding="utf-8") as bf:
                        content = bf.read()
                except OSError as e:
                    raise RuntimeError(
                        f"Step '{node_id}' declares creative_brief and the "
                        f"project points at {brief_path!r}, which cannot be "
                        f"read: {e}"
                    ) from e
                if not content.strip():
                    raise RuntimeError(
                        f"Step '{node_id}' declares creative_brief and the "
                        f"project points at {brief_path!r}, which is empty."
                    )
                inputs["creative_brief"] = content

    if step_type == "llm_only" and manifest and "context_fields" in manifest:
        saved_project_folder = inputs.get("project_folder", "")
        saved_fps = inputs.get("project_fps")
        saved_brand_template = inputs.get("brand_template")
        saved_creative_brief = inputs.get("creative_brief")
        
        from library.tools.context_projector import project_fields
        inputs = project_fields(inputs, manifest["context_fields"])
        
        inputs["project_folder"] = saved_project_folder
        if saved_fps is not None:
            inputs["project_fps"] = saved_fps
        if saved_brand_template is not None:
            inputs["brand_template"] = saved_brand_template
        if saved_creative_brief is not None:
            inputs["creative_brief"] = saved_creative_brief

    return inputs


# ── Step Execution ──────────────────────────────────────────────────

# Every step subprocess used to be killed at a hardcoded 600 seconds, which
# is the same number the DAG carries as `semantic_analysis`'s
# `estimated_duration_seconds`. An estimate is a PLANNING number; using it
# as a deadline means a step that takes longer than someone once guessed is
# killed rather than reported slow. On project 001 - 17 clips, 13.5 minutes
# of footage - the vision pass needs 45 to 90 minutes and was killed four
# clips in, then RETRIED, because `_is_transient` treats "timed out" as
# transient. `temporal_index`, `render_subtitles` and `render` are all in
# the same range on real footage. That is why no project on disk has ever
# had a completed run.
#
# The timeout that remains exists to break a WEDGE, not to enforce an
# estimate, so it is generous, single, and documented. Override with
# PIPELINE_STEP_TIMEOUT_SECONDS; a value of 0 or less means no timeout.
STEP_TIMEOUT_ENV = "PIPELINE_STEP_TIMEOUT_SECONDS"
DEFAULT_STEP_TIMEOUT_SECONDS = 4 * 60 * 60


def step_timeout_seconds():
    """Seconds before a step subprocess is killed, or None for no limit."""
    raw = os.environ.get(STEP_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_STEP_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(
            f"{STEP_TIMEOUT_ENV}={raw!r} is not a number of seconds")
    return value if value > 0 else None


def _run_step_subprocess(argv: list, inputs: dict, label: str):
    """Run a step, streaming its stderr as it arrives.

    stdout is the step's JSON result and is captured. stderr is its log,
    and it is echoed line by line rather than held until the step exits -
    a 90-minute vision pass under `capture_output=True` is indistinguishable
    from a wedged one, which is exactly the state this runner was in.
    """
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        bufsize=1,
    )

    # Both pipes get their own reader, and stdin is written by a third.
    #
    # This used to echo stderr from a thread while `communicate()` ran on
    # the same Popen, and `communicate()` reads BOTH pipes. Two readers on
    # one pipe is a race the selector cannot win: it reports stderr
    # readable, the pump has already taken the bytes, and the main thread
    # blocks forever in read() - so nothing ever drains STDOUT. The step
    # then blocks in write() as soon as its result exceeds the 64KB pipe
    # buffer, and neither side can move.
    #
    # Measured on this run: semantic_analysis finished all 17 clips,
    # printed "Collected 17 clip profiles", and sat there. `sample` showed
    # the child in _Py_write_impl -> write() and BOTH parent threads in
    # read(). It is the whole step's output that overflows, so this hits
    # any step whose JSON is larger than a page or two - which is most of
    # the analysis phase, on any project big enough to matter.
    #
    # Do not "simplify" this back to communicate(). Streaming stderr and
    # communicate() cannot both own that pipe.
    captured_out = []
    captured_err = []

    def _pump_err():
        for line in proc.stderr:
            captured_err.append(line)
            print(f"     | {line.rstrip()}", file=sys.stderr, flush=True)

    def _pump_out():
        for chunk in iter(lambda: proc.stdout.read(65536), ""):
            captured_out.append(chunk)

    def _feed():
        try:
            proc.stdin.write(json.dumps(inputs))
            proc.stdin.close()
        except (BrokenPipeError, ValueError):
            pass

    readers = [
        threading.Thread(target=_pump_err, daemon=True),
        threading.Thread(target=_pump_out, daemon=True),
        threading.Thread(target=_feed, daemon=True),
    ]
    for t in readers:
        t.start()

    try:
        proc.wait(timeout=step_timeout_seconds())
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        for t in readers:
            t.join(timeout=5)
        raise
    # The pipes can still hold buffered data after the child exits; join
    # the readers before reporting, or a fast step loses its own output.
    for t in readers:
        t.join(timeout=30)
    return proc.returncode, "".join(captured_out), "".join(captured_err)


def run_deterministic_step(entry: str, inputs: dict) -> dict:
    """Run a deterministic step via subprocess (stdin JSON → stdout JSON)."""
    code, stdout, stderr = _run_step_subprocess(
        [sys.executable, entry], inputs, os.path.basename(entry))

    if code != 0:
        raise RuntimeError(
            f"Step failed (exit {code}):\n"
            f"  stderr: {stderr}"
        )

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Step produced invalid JSON:\n"
            f"  stdout: {stdout[:500]}\n"
            f"  stderr: {stderr[:500]}"
        )


def generate_output_schema_text(outputs: list) -> str:
    """Generate a clean JSON schema block from manifest outputs."""
    if not outputs:
        return ""
    
    schema_text = "## Required Output Format\n\nPlease return a JSON object containing the following fields:\n\n```json\n{\n"
    
    for i, out in enumerate(outputs):
        name = out.get("name", "unknown")
        type_str = out.get("type", "any")
        desc = out.get("description", "")
        req = "required" if out.get("required", True) else "optional"
        
        if desc:
            schema_text += f"  // {desc} ({req})\n"
        else:
            schema_text += f"  // ({req})\n"
            
        is_last = i == len(outputs) - 1
        comma = "" if is_last else ","
        
        if type_str.lower() in ["str", "string"]:
            schema_text += f'  "{name}": "..."{comma}\n'
        elif type_str.lower() in ["int", "integer", "number", "float"]:
            schema_text += f'  "{name}": 0{comma}\n'
        elif type_str.lower() in ["bool", "boolean"]:
            schema_text += f'  "{name}": false{comma}\n'
        elif type_str.lower() in ["list", "array"]:
            schema_text += f'  "{name}": []{comma}\n'
        elif type_str.lower() in ["dict", "object"]:
            schema_text += f'  "{name}": {{}}{comma}\n'
        else:
            schema_text += f'  "{name}": "..."{comma}\n'
            
    schema_text += "}\n```"
    return schema_text


@step_timer(step_id_kwarg="node_id")
def present_llm_step(prompt_path: str, inputs: dict, node_id: str, manifest: dict = None, full_auto: str = None, llm_timeout: int = 300, bridge_supplied: set = None) -> dict:
    """Present an LLM step and execute it using LLMClient or AGY backend.
    
    In automated mode, this calls the LLM and returns the parsed output.
    """
    raw_input_tokens = len(str(inputs).split()) * 1.3
    
    # For hybrid steps, inputs may not be projected yet. Project them now if needed.
    if manifest and "context_fields" in manifest:
        saved_project_folder = inputs.get("project_folder", "")
        saved_fps = inputs.get("project_fps")
        # None, not "default_brand": a project that declares no template
        # must stay declaring none through projection, or the restore below
        # invents a declaration the project never made.
        saved_brand_template = inputs.get("brand_template")
        saved_creative_brief = inputs.get("creative_brief")
        # A pre-bridge exists to build the ONE table its handoff tells the
        # model to read, so projecting that table away is always wrong -
        # the step is then instructed to use data the prompt does not
        # carry. Four steps shipped that way: `cuts_toon`,
        # `vfx_candidates_toon`, `sfx_candidates_toon` and
        # `transcripts_toon`/`topics_toon` were all computed and then
        # deleted, because only `select_broll` happened to name its table
        # in `context_fields`. Restoring by NAME here, rather than adding
        # four more allow-list entries, is deliberate: a new hybrid step
        # gets this for free and it cannot go stale the way four lists
        # can. See AGENTS.md 10.1 on key-name mismatches.
        saved_bridge = {k: inputs[k] for k in (bridge_supplied or set())
                        if k in inputs}

        from library.tools.context_projector import project_fields
        inputs = project_fields(inputs, manifest["context_fields"])

        # Only where projection dropped the key ENTIRELY. A manifest that
        # names the table itself (select_broll does) may still narrow it
        # with sub-paths, and that narrowing is a decision to respect.
        for _bridge_key, _bridge_value in saved_bridge.items():
            if _bridge_key not in inputs:
                inputs[_bridge_key] = _bridge_value

        inputs["project_folder"] = saved_project_folder
        if saved_fps is not None:
            inputs["project_fps"] = saved_fps
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
    # TemplateLoader resolves by NAME against library/templates/.  Ask the
    # PROJECT for the name rather than fishing it out of `inputs`, for the
    # same reason delivery_format is a function of the project: a value in
    # flight can be renamed, defaulted and lost, and this one was - the
    # read here was `inputs.get("brand_template", "default_brand")` and the
    # key was never set, so every project's LLM brand constraints came from
    # default_brand.yaml whatever its project.yaml declared.
    from library.tools.brand_registry import (
        DEFAULT_TEMPLATE_NAME, project_template_name, reference_template_name)
    brand_template = (reference_template_name(project_template_name(project_folder))
                      if project_folder else DEFAULT_TEMPLATE_NAME)
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
        interface = manifest.get("interface", {})
        # A hybrid step's OUTPUTS are what the step emits; they are not
        # what the LLM writes. mesh_spine's post-bridge computes
        # audio_spine and timed_spine from a creative `structure` - asking
        # the LLM for the computed keys made it fail QA every run and fall
        # through to "proceeding with best attempt".
        # `interface.llm_outputs` declares the LLM's actual contribution.
        if "llm_outputs" in interface:
            llm_outputs = interface["llm_outputs"]
        else:
            outputs = interface.get("outputs", [])
            # Never ask for a key the step already has: pre-bridge outputs
            # are merged back in by run_hybrid_step.
            already_have = set(inputs) | set(bridge_supplied or ())
            llm_outputs = [
                o for o in outputs if o.get("name") not in already_have
            ]
        expected_schema_str = json.dumps(llm_outputs)

        # Nothing to ask.  A step reaches here with an empty schema when
        # every key it declares has already been produced - by its own
        # step.py, or by its pre-bridge - so there is no question left for
        # a model to answer.  `semantic_analysis` is the standing case:
        # its schema is `[]`, its handoff says in so many words that no
        # model authors the per-clip analysis, and its answer every run
        # was the three bytes `{}` for 33,000 tokens of vision documents.
        # The call is skipped rather than made and discarded.  This is a
        # property of the schema, not of any step's name: declare
        # `interface.llm_outputs` and the call happens again.
        if not llm_outputs:
            print(f"  [llm] {node_id}: no LLM output declared - "
                  f"nothing to ask, skipping the call", file=sys.stderr)
            return {}

        llm_manifest = dict(manifest)
        if "interface" in manifest:
            llm_manifest["interface"] = dict(manifest["interface"])
            llm_manifest["interface"]["outputs"] = llm_outputs

        # Inject dynamic schema into prompt
        schema_text = generate_output_schema_text(llm_outputs)
        marker = "<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->"
        if marker in prompt:
            prompt = prompt.replace(marker, schema_text)
        elif schema_text:
            prompt += "\n\n" + schema_text

    for attempt in range(qa_loop.max_retries + 1):
        full_prompt = prompt + constraints + "\n\nContext:\n" + current_context
        parsed_result = None
        
        if full_auto == "mock":
            from pathlib import Path
            project_folder = inputs.get("project_folder", "")
            bak_file = ProjectLayout(project_folder).read_path(
                Area.LLM_RESPONSES_BAK, f"{node_id}.json")
            if bak_file.exists():
                print(f"  [MOCK] Reading LLM response from {bak_file}", file=sys.stderr)
                with open(bak_file, "r") as f:
                    parsed_result = json.load(f)
            else:
                raise LLMError(f"Mock response not found at {bak_file}")

        elif full_auto == "agy":
            import datetime
            from pathlib import Path
            project_folder = inputs.get("project_folder", "")
            if not project_folder:
                raise LLMError("project_folder required in inputs for agy backend")
                
            _layout = ProjectLayout(project_folder)
            requests_dir = _layout.write_dir(Area.LLM_REQUESTS)
            responses_dir = _layout.write_dir(Area.LLM_RESPONSES)
            
            req_file = requests_dir / f"{node_id}.json"
            res_file = responses_dir / f"{node_id}.json"
            
            if res_file.exists():
                res_file.unlink()
                
            req_data = {
                "step_id": node_id,
                # The brand's constraints are PROMPT TEXT: `full_prompt`
                # places them between the handoff and the context for
                # every other backend.  This file carried `prompt` alone,
                # so the answering agent never saw them - meaning that
                # even with get_brand_constraints returning a real string,
                # the mode this pipeline actually runs in would still have
                # dropped it.  Recorded separately too, so the archive
                # shows what the brand contributed to a call.
                "prompt": prompt + constraints,
                "constraints": constraints,
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

        if full_auto == "api":
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
                    
        def validate_for_llm(nid, out, man):
            issues = validate_step_output(nid, out, man)
            if issues:
                raise RuntimeError("Validation failed:\n" + "\n".join(f"- {i}" for i in issues))
                
        passed, feedback = qa_loop.run_checks(node_id, parsed_result, llm_manifest if manifest else None, validate_for_llm)
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
    code, stdout, stderr = _run_step_subprocess(
        [sys.executable, str(script_path)], inputs, script_path.name)
    if code != 0:
        raise RuntimeError(
            f"Script {script_path.name} failed (exit {code}):\n"
            f"  stderr: {stderr}"
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Script {script_path.name} produced invalid JSON:\n"
            f"  stdout: {stdout[:500]}\n"
            f"  stderr: {stderr[:500]}"
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
        llm_output = present_llm_step(
            prompt_path, compressed, node_id, manifest, full_auto,
            llm_timeout, bridge_supplied=set(pre_output),
        )
    except Exception as e:
        raise LLMError(f"LLM generation failed: {e}")
    
    if isinstance(llm_output, dict) and llm_output.get("__status") == "awaiting_llm":
        return llm_output
    if 'pre_output' not in locals():
        pre_output = {}
        
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


# Steps whose output is allowed to say "I could not run" without stopping
# the pipeline.  Everything NOT listed here must produce real output: an
# `available: false` result from any other step is a failure, not a note.
OPTIONAL_ANALYSIS_STEPS = frozenset()


def check_output_is_real(node_id: str, output: dict) -> list:
    """Detect steps that report success while emitting nothing usable.

    Three separate steps used to do this in the same run: prosody wrote
    `available: true` with zero profiles, music_analysis wrote
    `available: false` around a captured traceback, and the pipeline
    carried on and still called the run SUCCESS.
    """
    problems = []
    if node_id in OPTIONAL_ANALYSIS_STEPS or not isinstance(output, dict):
        return problems

    def inspect(value, path):
        if not isinstance(value, dict):
            return
        if value.get("available") is False:
            reason = value.get("error") or value.get("reason") or "no reason given"
            problems.append(
                f"{path} reports available=false: "
                f"{str(reason).splitlines()[0][:200]}"
            )
        elif value.get("available") is True:
            payload = {
                k: v for k, v in value.items()
                if k not in ("available", "error", "reason")
            }
            if payload and all(
                isinstance(v, (list, dict, str)) and len(v) == 0
                for v in payload.values()
                if isinstance(v, (list, dict, str))
            ) and any(isinstance(v, (list, dict)) for v in payload.values()):
                problems.append(
                    f"{path} reports available=true but every payload "
                    f"field is empty: {sorted(payload)}"
                )
        for key, sub in value.items():
            inspect(sub, f"{path}.{key}" if path else key)

    inspect(output, node_id)
    return problems


def validate_step_output(node_id: str, output: dict, manifest: dict = None) -> list:
    """Validate a step's output against its manifest declarations.
    Returns a list of warning/error strings."""
    issues = []
    if not manifest or "interface" not in manifest or "outputs" not in manifest["interface"]:
        return issues

    outputs_spec = manifest["interface"]["outputs"]
    expected_keys = set()
    
    for spec in outputs_spec:
        key = spec.get("name")
        if not key:
            continue
        expected_keys.add(key)
        
        is_required = spec.get("required", True)
        
        if key not in output or output[key] is None:
            if is_required:
                issues.append(f"Step '{node_id}' output missing required key: '{key}'")
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
                issues.append(f"Step '{node_id}' output '{key}' expected type {expected_type_str}, got {type(val).__name__}")
                
        if is_required:
            is_empty = False
            if isinstance(val, (list, dict, str)) and len(val) == 0:
                is_empty = True
            elif isinstance(val, int) and val == 0 and key.startswith("total_") and key != "total_failed":
                is_empty = True
                
            if is_empty:
                issues.append(f"Step '{node_id}' output '{key}' is semantically empty: {val}")

    actual_keys = set(output.keys())
    extra_keys = [k for k in actual_keys - expected_keys if not k.startswith("__")]
    if extra_keys:
        issues.append(f"Step '{node_id}' output has unexpected extra fields: {', '.join(extra_keys)}")

    return issues


# ── Main Runner ─────────────────────────────────────────────────────

def run_pipeline(
    project_dir: str,
    from_step: str = None,
    single_step: str = None,

    dry_run: bool = False,
    auto_mode: bool = False,
    review_mode: bool = False,
    resume_mode: bool = False,
    full_auto: str = None,
    llm_timeout: int = 300,
    rerun: list = None,
):
    """Execute the pipeline DAG."""
    # Initialize logger
    get_logger(project_dir)

    dag = load_dag()
    state = load_pipeline_state(project_dir)
    order = topological_sort(dag)
    nodes = {n["id"]: n for n in dag["nodes"]}

    # The split ledger. Stages are declared per step; a project written
    # before the split is folded into the two ledgers once, here.
    manifests = _manifest_map(nodes)
    stage_by_node = _stage_map(manifests)
    migrated = step_ledger.migrate_legacy(state, stage_by_node)
    if migrated:
        print(f"  Migrated {len(migrated)} steps from the single "
              f"'steps_completed' ledger into preflight/edit",
              file=sys.stderr)

    # --rerun deletes artifacts and clears ledger entries, so a dry run
    # reports the request rather than performing it.
    if rerun and dry_run:
        for raw in rerun:
            kind, value = step_ledger.parse_rerun_target(raw, stage_by_node)
            print(f"  ↻ would re-run ({kind}): {value}", file=sys.stderr)
    elif rerun:
        for line in apply_rerun_requests(project_dir, state, rerun,
                                         stage_by_node, manifests):
            print(f"  ↻ re-run requested - {line}", file=sys.stderr)

    if not dry_run:
        apply_source_identity(project_dir, state, stage_by_node, manifests)
        save_pipeline_state(project_dir, state)

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

    if dry_run:
        for node_id in steps_to_run:
            impl = get_step_implementation(get_step_dir(nodes[node_id]))
            done = step_ledger.is_completed(state, node_id)
            stage = stage_by_node[node_id]
            print(f"    {'[done] ' if done else '       '}{node_id} "
                  f"({impl['type']}, {stage})", file=sys.stderr)
        summary = {"status": "DRY_RUN", "steps_to_run": steps_to_run}
        json.dump(summary, sys.stdout, indent=2)
        return summary

    
    completed = []
    failed = []
    awaiting_llm = []
    paused_at_gate = None
    held_before_step = None

    run_mode = run_control.describe_mode(
        full_auto=full_auto, auto_mode=auto_mode, review_mode=review_mode,
        resume_mode=resume_mode, single_step=single_step, from_step=from_step,
        rerun=rerun,
    )
    run_control.begin_run_status(project_dir, run_mode, steps_to_run,
                                 argv=sys.argv[1:])
    print(f"  Mode: {run_mode}", file=sys.stderr)

    # This run's identity, and the ledger every artifact it writes is
    # recorded against. `run_control` owns the handbrake protocol and
    # says whether a run is UP; this says which run a FILE came from,
    # which outlives the run by a lot. See library/tools/provenance.py.
    _run_id = provenance.new_run_id()
    _provenance = provenance.ProvenanceLedger(project_dir)
    _provenance.start_run(_run_id, mode=run_mode)
    _steps_this_run = []
    print(f"  Run:  {_run_id}", file=sys.stderr)

    current_phase = None
    
    for node_id in steps_to_run:
        # The handbrake.  Checked here, at the boundary between steps, so
        # the step that was in flight when the captain pressed Pause has
        # already written its output and its state.  Stopping mid-step
        # would leave pipeline_data.json describing a step that only half
        # happened, and nothing downstream could tell.
        hold = run_control.hold_requested(project_dir)
        if hold:
            requested_by = hold.get("requested_by") or "unknown"
            print(f"\n  \u270b Handbrake engaged by {requested_by} - holding "
                  f"before {node_id}", file=sys.stderr)
            held_before_step = node_id
            run_control.write_run_status(
                project_dir, status="held", current_step=None,
                held_before_step=node_id, hold=hold,
            )
            break

        node = nodes[node_id]

        # Free the loaded ML models when the run crosses a phase boundary.
        #
        # This read the phase off the DAG NODE ID, which is `scan`, not
        # `step_1_01_scan_project`: `"scan".split("_")` is one element, so
        # `phase` was None for every node in the DAG and `unload_all()`
        # never once fired.  The step_ref is where the phase actually
        # lives.  It matters most at the boundary this refactor names -
        # preflight holds WhisperX, wav2vec2 and the vision model, and the
        # edit stage needs none of them.
        phase = _phase_of(node)
        if phase and current_phase and phase != current_phase:
            print(f"\n  [Phase Transition] {current_phase} -> {phase}. "
                  f"Freeing VRAM...", file=sys.stderr)
            unload_all()
        if phase:
            current_phase = phase


        step_dir = get_step_dir(node)
        impl = get_step_implementation(step_dir)
        
        # Check if already completed.  A finished preflight step is
        # skipped by default and stays skipped until either the
        # source-identity check or an explicit --rerun says otherwise -
        # that is the captain's ruling of "same command, preflight
        # auto-skipped once done", and it is why there is no separate
        # preflight command.
        if step_ledger.is_completed(state, node_id):
            if resume_mode:
                from library.tools.review_gate import load_gate_feedback, apply_feedback_to_output
                feedback = load_gate_feedback(project_dir, node_id)
                if feedback:
                    if feedback.action == "pending":
                        print(f"  ⏸  {node_id}: gate is pending, stopping.", file=sys.stderr)
                        paused_at_gate = node_id
                        break
                    elif feedback.action == "rejected":
                        print(f"  ✗  {node_id}: rejected by reviewer.", file=sys.stderr)
                        failed.append(node_id)
                        _record_step_failure(
                            state, node_id, "rejected by reviewer at review gate")
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
        
        run_control.write_run_status(
            project_dir, current_step=node_id,
            current_step_name=node["name"],
            current_step_started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        print(f"\n  ▶  Step: {node_id} ({node['name']})", file=sys.stderr)
        print(f"     Type: {impl['type']} | Dir: {step_dir}", file=sys.stderr)
        

        
        # Gather inputs from upstream (pass manifest for optional-input checking)
        inputs = gather_step_inputs(node_id, dag, state, manifest=impl.get("manifest"), step_type=impl.get("type", "unknown"))
        print(f"     Inputs: {list(inputs.keys())}", file=sys.stderr)
        
        try:
            start_time = time.time()
            # What the output tree looks like BEFORE this step. Compared
            # against the same listing afterwards, this is how every
            # artifact learns which step wrote it - without any step
            # having to say so, which matters because half of them hand
            # the writing to ffmpeg, Remotion or Resolve.
            # See library/tools/provenance.py.
            artifacts_before = _provenance.snapshot()

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
                        _record_step_failure(state, node_id, str(step_e))
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
                # The catalog step writes `clip_catalog`; reading `clips`
                # made this check see an empty catalog and warn that every
                # legitimate clip_id was unrecognized.
                catalog = state.get("step_outputs", {}).get("catalog", {}).get("clip_catalog", [])
                catalog_ids = {c.get("clip_id") for c in catalog if c.get("clip_id")}
                catalog_paths = {
                    c.get(key) for c in catalog
                    for key in ("source_file", "path") if c.get(key)
                }
                
                def looks_like_a_path(value: str) -> bool:
                    """Whether a `source` value is naming a FILE at all.

                    `source` is not only a clip path: music_selection uses
                    it for which catalogue a track came from - "library",
                    "project", "external" - and this check reported all
                    three as unrecognised footage. A warning that fires on
                    correct output is noise, and noise is how a real one
                    gets scrolled past.
                    """
                    return "/" in value or "\\" in value or bool(
                        os.path.splitext(value)[1])

                def extract_refs(obj, ids, paths):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k == "clip_id" and isinstance(v, str): ids.add(v)
                            elif k in ("source", "source_path", "clip") and isinstance(v, str):
                                if looks_like_a_path(v): paths.add(v)
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
            issues = validate_step_output(node_id, output, impl.get("manifest"))
            if issues:
                print(f"     ⚠ WARNING: Output validation issues for {node_id}:", file=sys.stderr)
                for issue in issues:
                    print(f"       - {issue}", file=sys.stderr)
                # Do not crash on validation failures (warning mode for now)

            # A step that emits an unavailable or hollow result has not
            # succeeded, whatever its exit code said.
            hollow = check_output_is_real(node_id, output)
            if hollow:
                message = (
                    f"Step '{node_id}' reported success but produced no "
                    f"usable output:\n  - " + "\n  - ".join(hollow)
                )
                print(f"     \u2717 FAILED (HollowOutput): {message}", file=sys.stderr)
                run_logger = get_logger()
                if run_logger:
                    run_logger.log(step_id=node_id, event_type="step_failed",
                                   error=message)
                _record_step_failure(state, node_id, message)
                save_pipeline_state(project_dir, state)
                failed.append(node_id)
                print("     Stopping pipeline due to failure.", file=sys.stderr)
                break

            state.setdefault("step_outputs", {})[node_id] = output
            _clear_step_failure(state, node_id)
            entry = {
                "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "elapsed_s": round(elapsed, 1),
            }
            if auto_mode and impl["type"] == "hybrid":
                entry["note"] = "auto-completed via bridge (context only)"
            step_ledger.record(state, stage_by_node[node_id], node_id, entry)
            save_pipeline_state(project_dir, state)
            
            if node_id == "mesh_spine":
                step_outputs = state.get("step_outputs", {})
                mesh_output = step_outputs.get('mesh_spine', {})
                total_duration = mesh_output.get('total_duration', mesh_output.get('duration_seconds', 0))
                MIN_DURATION = 30  # seconds, for shortform
                if total_duration > 0 and total_duration < MIN_DURATION:
                    logger.warning(f"Mesh spine duration ({total_duration:.1f}s) below minimum ({MIN_DURATION}s). Consider using more footage.")
            
            _export_step_for_review(project_dir, node_id, node["name"], output, state)

            # Now, and not before the export: `_export_step_for_review`
            # writes <step_id>.json and <step_id>.summary.md, and a
            # snapshot taken above them attributes a step's own export to
            # nobody. Everything the step's completion caused to appear
            # is what belongs to the step.
            # See library/tools/provenance.py.
            _provenance.observe(node_id, _run_id, artifacts_before,
                                _provenance.snapshot())
            _steps_this_run.append(node_id)

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
                paused_at_gate = node_id
                run_control.write_run_status(
                    project_dir, status="gate_pending", current_step=None,
                    last_completed_step=node_id, paused_at_gate=node_id,
                )
                break

            completed.append(node_id)
            run_control.write_run_status(
                project_dir, current_step=None,
                last_completed_step=node_id,
            )

                
        except Exception as e:
            # Unhandled errors outside step execution
            print(f"     ✗ FAILED UNEXPECTEDLY: {e}", file=sys.stderr)
            logger = get_logger()
            if logger:
                logger.log(step_id=node_id, event_type="step_failed", error=str(e))
            _record_step_failure(state, node_id, str(e))
            save_pipeline_state(project_dir, state)
            failed.append(node_id)
            break
    
    # ── Summary ──
    # Status is derived from the whole project ledger, not just the steps
    # this invocation happened to touch.  A resumed run that skipped every
    # step used to report "Failed: 0" while pipeline_data.json still held
    # 69 unresolved failures, and that is how a hollow timeline shipped as
    # SUCCESS.
    outstanding_failures = sorted(set(state.get("failed_steps", [])))
    never_run = [
        node_id for node_id in order
        if not step_ledger.is_completed(state, node_id)
        and node_id not in awaiting_llm
    ]
    # `--step`, `--from` and a review-gate pause all leave DAG steps unrun
    # on purpose.  Those runs are incomplete, not broken, and must not
    # report the same status as a run whose steps blew up.
    partial_invocation = (
        list(steps_to_run) != list(order)
        or paused_at_gate is not None
        or held_before_step is not None
    )

    if outstanding_failures or failed:
        status = "FAILED"
    elif awaiting_llm:
        status = "AWAITING_LLM"
    elif never_run:
        status = "PARTIAL" if partial_invocation else "FAILED"
    else:
        status = "SUCCESS"

    print(f"\n{'═'*60}", file=sys.stderr)
    print(f"  Pipeline Summary", file=sys.stderr)
    print(f"{'═'*60}", file=sys.stderr)
    print(f"  Status:       {status}", file=sys.stderr)
    print(f"  Ran now:      {len(completed)} steps", file=sys.stderr)
    print(f"  Awaiting LLM: {len(awaiting_llm)} steps", file=sys.stderr)
    print(f"  Failed now:   {len(failed)} steps", file=sys.stderr)
    print(f"  Outstanding failures (all runs): "
          f"{len(outstanding_failures)}", file=sys.stderr)
    print(f"  Never completed: {len(never_run)} steps", file=sys.stderr)
    stage_totals = {stage: sum(1 for s in stage_by_node.values() if s == stage)
                    for stage in step_ledger.STAGES}
    stage_done = {
        stage: len(state.get(step_ledger.LEDGER_KEY[stage], {}))
        for stage in step_ledger.STAGES
    }
    print("  Ledgers:      " + ", ".join(
        f"{stage} {stage_done[stage]}/{stage_totals[stage]}"
        for stage in step_ledger.STAGES), file=sys.stderr)
    if paused_at_gate:
        print(f"  Paused at review gate: {paused_at_gate}", file=sys.stderr)
    if held_before_step:
        print(f"  Held by handbrake before: {held_before_step}",
              file=sys.stderr)

    if completed:
        print(f"  ✓ {', '.join(completed)}", file=sys.stderr)
    if awaiting_llm:
        print(f"  ⏸ {', '.join(awaiting_llm)}", file=sys.stderr)
    if outstanding_failures:
        print(f"  ✗ {', '.join(outstanding_failures)}", file=sys.stderr)
        for node_id in outstanding_failures:
            err = state.get("step_errors", {}).get(node_id, "")
            err_line = (str(err).splitlines() or ["(no error message)"])[0][:160]
            print(f"      {node_id}: {err_line}",
                  file=sys.stderr)
    if never_run:
        print(f"  ○ never completed: {', '.join(never_run)}", file=sys.stderr)

    print(f"{'═'*60}\n", file=sys.stderr)

    # Output final state
    summary = {
        "status": status,
        "completed": completed,
        "completed_steps": len(step_ledger.all_completed(state)),
        "stage_completed": stage_done,
        "awaiting_llm": awaiting_llm,
        "failed": failed,
        "outstanding_failures": outstanding_failures,
        "never_completed": never_run,
        "partial_invocation": partial_invocation,
        "paused_at_gate": paused_at_gate,
        "held_before_step": held_before_step,
        "run_mode": run_mode,
        "state_file": str(ProjectLayout(project_dir).pipeline_data_path),
    }
    # The run's own account of how it ended.  `held` is not overwritten:
    # the dashboard distinguishes "the captain stopped it" from "it ran
    # out of steps", and only the former should offer Resume as the
    # obvious next press.
    run_control.write_run_status(
        project_dir,
        status="held" if held_before_step else status.lower(),
        current_step=None,
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        summary_status=status,
        held_before_step=held_before_step,
        paused_at_gate=paused_at_gate,
    )
    _provenance.end_run(_run_id, status.lower(), _steps_this_run)

    # Regenerate the two readable documents from what was just recorded.
    # Generated rather than written, and regenerated on every run, so the
    # folder never describes a run that is two runs old.
    # See library/tools/run_traceback.py.
    try:
        from library.tools.run_traceback import write_traceback
        written = write_traceback(project_dir)
        print(f"  Traceback: {written['traceback']}", file=sys.stderr)
        print(f"  Artifacts: {written['artifact_index']}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not write the run traceback: {exc}",
              file=sys.stderr)

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
    parser.add_argument("--dry-run", action="store_true",
                       help="Print the execution plan without running steps")

    parser.add_argument("--auto", action="store_true", 
                       help="Auto-complete hybrid steps (use bridge output as final)")
    parser.add_argument("--review", action="store_true",
                       help="Enable review gates: export step outputs and save gate snapshots for dashboard review")
    parser.add_argument("--resume", action="store_true",
                       help="Resume pipeline from pending gates")
    parser.add_argument(
        "--rerun", action="append", metavar="TARGET", default=[],
        help="Redo finished work. Repeatable. TARGET is a stage "
             "(preflight|edit), a step (temporal_index), or one clip of "
             "one step (temporal_index:clip_007). This is the only "
             "supported way to re-run a completed step; --from only "
             "trims the plan.")
    parser.add_argument("--full-auto", choices=["agy", "api", "mock"], help="Run full pipeline autonomously using specified LLM backend")
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
    summary = run_pipeline(
        project_dir=project_dir,
        from_step=args.from_step,
        single_step=args.step,

        dry_run=args.dry_run,
        auto_mode=args.auto,
        review_mode=args.review,
        resume_mode=args.resume,
        full_auto=args.full_auto,
        llm_timeout=args.llm_timeout,
        rerun=args.rerun,
    )
    # A failed run must look failed to whatever invoked us. Printing
    # "Status: FAILED" and exiting 0 is how a hollow timeline shipped as a
    # green CI job.
    if (summary or {}).get("status") == "FAILED":
        sys.exit(1)


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
