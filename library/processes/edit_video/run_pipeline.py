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



Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**Declare `interface.llm_outputs` on any hybrid step whose bridge emits a key the step also declares as an output**, or whose LLM contribution differs from the step's outputs.
`present_llm_step` builds the injected schema from `interface.outputs` minus what the bridge produced, so without the declaration the model is asked for nothing, or every attempt "fails". [why](docs/RULE_EVIDENCE.md#empty-llm-schema)

**A call with nothing to ask is not made.**
Declare `interface.llm_outputs` if the call is still needed. [why](docs/RULE_EVIDENCE.md#thirty-three-thousand-tokens-for-three-bytes)


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

The run summary reports `SUCCESS` only when the whole DAG is complete and `failed_steps` is empty in the project ledger - not just the steps this invocation touched.
- `FAILED`: a step failed, or emitted an `available: false`/hollow result, in this run or an earlier one. Exit code 1.
- `AWAITING_LLM`, `PARTIAL` (`--step`/`--from`/a review-gate pause left DAG steps unrun), `DRY_RUN`.
- `failed_steps` is current state, not a log: a step that later succeeds is removed from it.
- **A recorded failure of a step this DAG no longer contains is REPORTED and does not decide the status.** Never drop one: going quiet about a recorded failure is what `failed_steps` exists to prevent.
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
from library.tools import (brief_attachment, briefing_interview,
                           briefing_chat,
                           decided_value,
                           direction_contradiction,
                           operations, post_bridge_retry, run_restart,
                           second_pass, undetermined)
from library.tools import run_control
from library.tools import model_task
# The model-task service's names, re-exported: callers and the replay
# bench reach them through the runner. See library/tools/model_task.py.
from library.tools.model_task import (  # noqa: F401 - re-exports
    FULL_AUTO_AGENT, FULL_AUTO_DEPRECATED_AGY, LLMError,
    generate_output_schema_text, normalize_full_auto)
from library.tools import footage_identity, code_identity, step_ledger
from library.tools import stable_json
from library.tools.project_layout import Area, ProjectLayout
from library.tools import provenance
from library.tools import external_inputs, run_scope
from library.tools.ren_refusal import RenRefusal
from library.tools.versions import runs
from library.tools import requirements
from library.tools import breakpoints as run_breakpoints
from library.tools import run_profile

logger = logging.getLogger(__name__)

class PreBridgeError(Exception): pass
class PostBridgeError(Exception): pass


def looks_like_a_path(value: str) -> bool:
    """Whether a `source` value is naming a FILE at all.

    `source` is not only a clip path: music_selection uses
    it for which catalogue a track came from - "library",
    "project", "external" - and this check reported all
    three as unrecognised footage. A warning that fires on
    correct output is noise, and noise is how a real one
    gets scrolled past.

    A genuine file reference always carries a directory
    separator: the catalog holds absolute source paths, so
    anything the footage really contains arrives with one.
    A bare dotted tail is not enough - the designed pipeline
    names brand-template slots like "brand template
    style.series_look.cdl", whose suffix `os.path.splitext`
    reads as an extension but which no disk ever held.
    """
    return "/" in value or "\\" in value


def extract_clip_refs(obj) -> tuple:
    """Every `clip_id` value and every path-like `source` value in `obj`.

    A module-level function so the clip-reference check at the run site
    and the regression test read the same code. Returns `(ids, paths)`.

    What counts as a reference is deliberately narrow. A `clip_id`
    value carrying whitespace is a DEFINITION of the term - the
    grade-terms legend and the frame-strip legend both map the key to
    a sentence - not a use of it, and a real clip id (`clip_XXX`) is
    one token. Likewise a `source` value with no directory separator
    is a slot name or an enum, never a file the catalog could hold.
    """
    ids: set = set()
    paths: set = set()

    def extract_refs(node, ids, paths):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "clip_id" and isinstance(v, str):
                    token = v.strip()
                    if len(token.split()) == 1:
                        ids.add(token)
                elif k in ("source", "source_path", "clip") and isinstance(v, str):
                    if looks_like_a_path(v):
                        paths.add(v)
                else:
                    extract_refs(v, ids, paths)
        elif isinstance(node, list):
            for item in node:
                extract_refs(item, ids, paths)

    extract_refs(obj, ids, paths)
    return ids, paths


def unknown_clip_refs(output: dict, catalog_ids: set, catalog_paths: set) -> tuple:
    """Which references in a step `output` the catalog cannot account for.

    Returns `(unknown_ids, unknown_paths)`. Empty both means the step
    named nothing the footage does not contain.
    """
    out_ids, out_paths = extract_clip_refs(output)
    return out_ids - catalog_ids, out_paths - catalog_paths

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
#
# `project_context` is the captain's context folder plus the learnings
# routed to the step, as a MAP (library/tools/project_context.py).  It
# joins this tuple rather than growing a second routing mechanism: the
# step declares it, `gather_step_inputs` builds it, and it is restored
# BY NAME around projection like `creative_brief`, so an allow-list
# neither has to list it nor can drop it.  Unlike the brief it needs no
# attachment choice and never raises: an empty folder is a stated
# absence, and learnings simply scope to nothing.
PROCESS_LEVEL_INPUTS = ("sfx_library", "music_library", "creative_brief",
                        "project_context", "video_preferences")

# The one input carrying the LAST render's QA findings.  It is not a
# process-level input and it is not DAG-routable - see the block in
# `gather_step_inputs` that fills it, and library/tools/qa_findings.py.
QA_FINDINGS_INPUT = "render_qa_findings"

REQUIREMENT_OVERRIDES_KEY = "requirement_overrides"
"""Where a deliberate override is recorded in the project state.

An override is not a pass: a requirement REFUSED and a person said
proceed anyway.  Writing it here means a later reader of
`pipeline_data.json` - the dashboard, the panel, the captain tomorrow -
can ask "was the rough cut rejected when these captions were planned?"
and get an answer.  Going quiet about it is the defect the
refuse-with-override ruling exists to remove
(`data/decisions/rough-cut-gate.md`).

The command line is already on `pipeline_run.json` via `argv`, but that
records only that `--override` was TYPED.  This records what the
requirement actually refused with, which is the half a later reader
needs and cannot reconstruct.
"""

# The timeline transcript, produced by `library/tools/timeline_transcript.py`
# running outside the pipeline (it needs Resolve open and WhisperX).
# Like qa_findings, it is not DAG-routable: the producer is a CLI tool,
# not a step, so an edge cannot carry it.  It reaches a step because the
# step's manifest declares it, and `gather_step_inputs` reads it from
# the project's scratch directory.
TIMELINE_TRANSCRIPT_INPUT = "timeline_transcript"


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

    # A gating skill is read back from its receipt in `run_hybrid_step`,
    # which is the only runner path with a bounded retry to carry the
    # violation on. A gating skill declared anywhere else would render
    # in the prompt and never be read back - refused here, at plan
    # time, rather than silently unenforced. See
    # library/tools/pipeline_skills.GATING_COVERED_TYPES.
    from library.tools import pipeline_skills as _skills_check
    _gating = _skills_check.gating_skills(
        manifest, manifest.get("id", step_dir.name))
    if _gating:
        _runtime = manifest.get("implementation", {}).get(
            "default", {}).get("runtime", "")
        _has_bridge = has_bridge_py or (step_dir / "post_bridge.py").exists()
        _covered = (_runtime == "llm" and _has_bridge) or (
            _runtime != "llm" and _has_bridge and has_handoff_md)
        if not _covered:
            raise RuntimeError(
                f"{manifest.get('id', step_dir.name)}: gating skill(s) "
                f"{', '.join(_gating)} declared on a non-hybrid step. "
                f"The must-check read-back only covers hybrid answers; "
                f"declare gating skills on hybrid steps or report-only "
                f"skills here.")
    
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
    # project and is never MOVED in. Its bytes ARE snapshotted in, once
    # per run, into `pipeline_output/provenance/` - the fixed versioned
    # home its free path cannot have (library/tools/brief_snapshot.py).
    #
    # ATTACHING IT IS THE PROJECT'S CHOICE, and every reading of that
    # choice is stated.  It used to be automatic - a declared path went
    # into eight prompts on every run with no way to say "not this
    # time", and a project that declared none got silence.  The
    # captain's ruling of 2026-09-02 made it an opt-in whose refusal is
    # answered by an INTERVIEW rather than by nothing.  The path reaches
    # state only when the reading is ATTACHED, so every consumer
    # downstream - the whitelist, gather_step_inputs, the replay bench -
    # sees exactly what a project with no brief sees, with no second
    # place able to reach a different answer.
    # See library/tools/brief_attachment.py.
    if "creative_brief" not in state:
        import sys
        try:
            attachment = brief_attachment.read_declaration(project_dir)
        except brief_attachment.BriefAttachmentError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"Warning: failed to read creative_brief from "
                  f"project.yaml: {e}", file=sys.stderr)
            attachment = brief_attachment.Attachment(
                brief_attachment.NONE_DECLARED,
                basis="project.yaml could not be read")
        state["creative_brief_attachment"] = {
            "reading": attachment.reading,
            "path": attachment.path,
            "basis": attachment.basis,
        }
        if attachment.attached:
            state["creative_brief"] = attachment.path
        print(brief_attachment.describe(attachment), file=sys.stderr)

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
    # `resolve_template_reference("")` answers `no_brand_template()`.
    # The project's own declarations about the PRODUCT.  The whitelist in
    # `gather_step_inputs` broadcasts `project_config` to every step, and
    # nothing had ever put one in state - so the captain's
    # `target_duration_seconds` reached no gate on any run, and four
    # duration checks measured against a constant instead.  See
    # library/tools/duration_targets.py.
    #
    # Re-read on EVERY run, not just the first (finding 29): state
    # persists in pipeline_data.json, so a declaration changed after the
    # first run never reached a step again - B6 changed
    # `target_duration_seconds` 60 -> 30 and the rerun's 2.02 gate still
    # measured against 60, silently. A run whose declaration differs
    # from what the last run used says so on stderr, naming both.
    from library.tools.brand_registry import project_declared_config
    declared_cfg = project_declared_config(project_dir)
    if "project_config" not in state:
        if declared_cfg:
            state["project_config"] = declared_cfg
    elif state.get("project_config") != declared_cfg:
        import sys as _sys_cfg
        _prev = state.get("project_config")
        print(f"project_config differs from the last run: "
              f"project.yaml now declares {declared_cfg}, "
              f"the persisted state held {_prev} - "
              f"the declaration on disk wins for this run",
              file=_sys_cfg.stderr)
        if declared_cfg:
            state["project_config"] = declared_cfg
        else:
            state.pop("project_config", None)

    if "brand_template" not in state:
        from library.tools.brand_registry import (
            describe_brand_absence, project_template_name)
        declared = project_template_name(project_dir)
        if declared:
            state["brand_template"] = declared
        else:
            # STATED, not inferred.  An absent declaration used to resolve
            # silently to library/templates/default_brand.yaml, so the
            # only way to find out a project was rendering under a brand
            # nobody chose was to read the resolver.
            import sys
            print(describe_brand_absence(), file=sys.stderr)

    # The video's own preferences (project layer in style.yaml,
    # per-video layer in video.yaml - see library/tools/video_prefs).
    # Loaded here so every run reads them with no command from the
    # captain; a video that declares nothing contributes nothing, and
    # a malformed declaration refuses the run by name.  Carried, not
    # yet honoured: no step reads this key until its follow-up lands.
    # The edit_video process addresses no reel; a caller that has one
    # (the Ren change spec's `reel`) passes it to `build_style_context`.
    if "video_preferences" not in state:
        from library.tools import video_prefs as video_prefs_mod
        loaded_prefs = video_prefs_mod.load_video_preferences(
            project_dir)
        if loaded_prefs is not None:
            state["video_preferences"] = loaded_prefs

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


def _stamp_step_input_digest(project_dir: str, node_id: str, impl: dict,
                             inputs: dict, output: dict) -> None:
    """Stamp one step's input digest into the run record and report it.

    Runs AFTER the step succeeded, on the inputs it was actually handed
    and the result it produced. A step whose manifest declares no
    `input_digest` block stamps nothing and prints nothing. A step that
    declares one gets its digest recorded in `pipeline_run.json` and a
    comparison line on stderr: identical, changed, or unknown.

    This REPORTS and never skips - the caller runs the step first,
    unconditionally. A stamp that cannot be written (or computed) costs
    the comparison line, never the run: both failures print loudly and
    continue.
    """
    from library.tools import edit_input_digest
    manifest = (impl or {}).get("manifest")
    try:
        spec = edit_input_digest.digest_spec(manifest, node_id)
    except edit_input_digest.DigestError as exc:
        print(f"     ⚠ Input digest not recorded for {node_id}: {exc}",
              file=sys.stderr)
        return
    if spec is None:
        return
    step_dir = (impl or {}).get("step_dir")
    if step_dir is None and (impl or {}).get("prompt"):
        # `llm_only` carries no step_dir, only the prompt path - the
        # prompt's own directory is the code that drew it.
        from pathlib import Path as _Path
        step_dir = _Path(str((impl or {})["prompt"])).parent
    try:
        record = edit_input_digest.compute_digest(
            spec, inputs or {}, output or {}, step_dir)
    except edit_input_digest.DigestError as exc:  # pragma: no cover -
        # compute_digest returns error records rather than raising on
        # data; a raise here is a programming error one layer up.
        print(f"     ⚠ Input digest not recorded for {node_id}: {exc}",
              file=sys.stderr)
        return
    stored = dict(record)
    stored["step"] = node_id
    run_control.record_step_input_digest(project_dir, node_id, stored)
    try:
        previous = (run_control.read_run_status(project_dir)
                    .get("previous_step_input_digests") or {}).get(node_id)
    except Exception:  # noqa: BLE001 - a status read must never fail a run
        previous = None
    for line in edit_input_digest.comparison_lines(
            node_id, stored, previous):
        print(line, file=sys.stderr)


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
        # Canonical spelling (library/tools/stable_json.py): the same
        # state is the same bytes, so a build that changes nothing
        # leaves a clean tree and a variant merge never conflicts on
        # key order alone. Content churn (last_updated, ledgers) still
        # makes this file rebuild-not-merge - see versions.variants.
        stable_json.dump_stable(state, f)


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


def _rerun_invalidates(targets, stage_by_node: dict) -> set:
    """The steps whose recorded output the --rerun targets will discard.

    Read WITHOUT side effects, so the scope can be refused before
    `apply_rerun_requests` deletes anything.  A clip-level target leaves
    the step's output in place until the step re-runs, but the step is
    re-running either way, so counting it here costs nothing and keeps
    the reading conservative.
    """
    invalidated = set()
    for raw in targets or []:
        try:
            kind, value = step_ledger.parse_rerun_target(raw, stage_by_node)
        except step_ledger.LedgerError:
            continue  # apply_rerun_requests reports it properly
        if kind == "stage":
            invalidated |= {node_id for node_id, stage in stage_by_node.items()
                            if stage == value}
        elif kind == "step":
            invalidated.add(value)
        elif kind == "clip":
            invalidated.add(value.split(":", 1)[0])
        elif kind == "region":
            # Nothing. `apply_rerun_requests` REFUSES a region target
            # (see `_region_rerun_refusal`), so no recorded output is
            # about to be discarded and marking one invalidated would
            # strand a consumer for a re-run that never happens.
            continue
    return invalidated


def _region_rerun_refusal(step_id: str, span: str) -> str:
    """Why `--rerun <step>@<span>` refuses, and what does honour a region.

    Derived from the operations registry rather than written out: an
    operation that gains REGION scope is offered here with no edit, and
    one that loses it stops being offered. Naming a route that does not
    exist is the failure mode this whole layer keeps refusing to have.
    """
    from library.tools import operations, scope as scope_mod

    at_region = [op for op in operations.by_node(step_id)
                 if scope_mod.REGION in op.scopes]
    lines = [
        f"--rerun {step_id}@{span}: the runner cannot re-run part of a "
        f"step.",
        "",
        f"A step runs as a subprocess over JSON stdin and no step's "
        f"main() reads an address, so clearing {step_id}'s ledger entry "
        f"here would re-run it over the WHOLE timeline - not the "
        f"{span} you asked for, and not leaving the rest alone.",
    ]
    if at_region:
        lines += [
            "",
            f"What DOES run at a region is the operations registry, "
            f"which hands the step's own function the address:",
        ]
        for op in at_region:
            lines.append(
                f"  python3 -m library.tools.operations {op.name} "
                f"--project <project> --region {span}")
            lines.append(f"      {op.summary}")
    else:
        lines += [
            "",
            f"No operation on {step_id} runs at a region today "
            f"(library/tools/operations.py), so there is no partial "
            f"re-run of it to ask for. `--rerun {step_id}` re-runs the "
            f"whole step, and that is the only thing this step can be "
            f"asked to redo.",
        ]
    return "\n".join(lines)


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
        current = _current_source_fingerprints(project_dir)
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

        if kind == "region":
            # A region target names a step and an interval of the
            # TIMELINE, and THE RUNNER CANNOT HONOUR ONE. It is refused
            # rather than half-done.
            #
            # What it used to do: forget the step's ledger entry and
            # print "the region scope decides what is recomputed". That
            # sentence was not true. Nothing threads a `Scope` into a
            # step here - steps run as subprocesses over JSON stdin
            # (`run_deterministic_step`) and no step's `main()` reads an
            # address - so the step re-ran at PROJECT scope and redid the
            # WHOLE video. For `--rerun plan_transitions@32.0-48.0` that
            # is a model re-deciding every transition in the piece, which
            # is the opposite of "leave the rest alone".
            #
            # A flag that does the wrong thing while printing that it did
            # the right one is worse than one that refuses, so this
            # refuses and names what really does honour a region: the
            # operations registry, which passes the `Scope` to the step's
            # own function (`operations.SCOPE_PARAMETER`). Which
            # operations those are is DERIVED from the registry, never
            # listed here, so a step that gains a region-scoped operation
            # is offered without anybody editing this message.
            step_id, _, span = value.partition("@")
            raise step_ledger.LedgerError(_region_rerun_refusal(step_id, span))

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


def _current_source_fingerprints(project_dir: str) -> dict:
    """Video AND audio fingerprints, in their own id spaces.

    The identity check guards cached per-clip work, and audio files
    have per-audio indices now - a re-recorded voiceover must
    invalidate them the same way new footage does. `audio_001` ids
    cannot collide with `clip_001` ones, so one record holds both.
    """
    files, _skipped = footage_identity.enumerate_footage(project_dir)
    current = footage_identity.fingerprints_for(files)
    try:
        audio_files, _audio_skipped = footage_identity.enumerate_audio(
            project_dir)
    except (OSError, FileNotFoundError):
        return current
    current.update(footage_identity.fingerprints_for(audio_files))
    return current


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
        current = _current_source_fingerprints(project_dir)
    except (OSError, FileNotFoundError):
        # No raw/ yet, or unreadable. `scan` will fail with a real message.
        return None

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
    if delta.moved and not delta.footage_changed:
        # Relocation, not replacement: every clip's bytes are identical
        # and only the project's address changed (a copy or rename -
        # finding 7, execution-frontier report 2026-09-24). The cached
        # analysis still describes the footage, so nothing is
        # invalidated; the record is adopted at the new address so the
        # next run compares against where the footage lives now.
        state[step_ledger.SOURCE_FINGERPRINTS_KEY] = current
        print(f"  ℹ Source footage relocated ({delta.describe()}) - "
              f"identical content, keeping the cached preflight work",
              file=sys.stderr)
        return delta
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


def apply_code_identity(state: dict, stage_by_node: dict,
                        manifests: dict, nodes: dict):
    """Invalidate cached preflight work whose step code has changed.

    The companion to ``apply_source_identity``: that one watches the
    footage, this one watches the code.  Together they make "preflight
    is skipped once done" safe rather than merely fast.

    On a project that has never carried code hashes (every project
    before this check existed), the current hashes are ADOPTED without
    invalidating anything - the same pattern ``apply_source_identity``
    uses for a project with no recorded footage fingerprints.  The check
    earns its keep from the second run onward.
    """
    # Build {node_id: step_dir_path} for every preflight step.
    preflight_dirs = {}
    for node_id, stage in stage_by_node.items():
        if stage != step_ledger.PREFLIGHT:
            continue
        node = nodes.get(node_id)
        if not node:
            continue
        step_dir = get_step_dir(node)
        preflight_dirs[node_id] = str(step_dir)

    current = code_identity.code_hashes_for(
        preflight_dirs, repo_root=LIBRARY_ROOT.parent)
    recorded = state.get(step_ledger.CODE_FINGERPRINTS_KEY)

    if not recorded:
        # First run under the new bookkeeping. Adopt, do not invalidate.
        state[step_ledger.CODE_FINGERPRINTS_KEY] = current
        return []

    invalidated = []
    for node_id, current_hash in current.items():
        recorded_hash = recorded.get(node_id)
        if recorded_hash is None:
            # A step that was added after the hashes were first recorded.
            # Adopt its current hash.
            continue
        if recorded_hash != current_hash:
            if step_ledger.is_completed(state, node_id):
                step_ledger.forget(state, node_id)
                invalidated.append(node_id)
                print(f"     - {node_id}: re-runs (step code changed)",
                      file=sys.stderr)

    if invalidated:
        print(f"  ⚠ Step code changed for "
              f"{', '.join(sorted(invalidated))} - "
              f"invalidating their cached preflight output",
              file=sys.stderr)

    # Always record current hashes so future runs can compare.
    state[step_ledger.CODE_FINGERPRINTS_KEY] = current
    return invalidated


_EXTERNAL_STATE_CACHE = {}


def _verified_external_state(state: dict) -> dict:
    """Verified external state for the project this run is on.

    Cached per project folder because verification touches the disk -
    the `render_output` check runs ffprobe - and `gather_step_inputs` is
    called once per step. The runner loads it once up front and passes
    it down; this is the fallback for callers that do not, and for the
    replay bench, which reconstructs a context without a run.
    """
    project_folder = (state or {}).get("project_folder", "")
    if not project_folder:
        return {}
    if project_folder not in _EXTERNAL_STATE_CACHE:
        _EXTERNAL_STATE_CACHE[project_folder] = external_inputs.load(
            project_folder, state)
    return _EXTERNAL_STATE_CACHE[project_folder]


def gather_step_inputs(node_id: str, dag: dict, state: dict, manifest: dict = None, step_type: str = "unknown", external: dict = None) -> dict:
    """Gather inputs for a step from upstream outputs using edge data_mappings.

    Raises RuntimeError when a declared data_mapping source key is missing
    from the upstream step's outputs, unless the step's manifest marks that
    input as optional (required: false).  This prevents silent contract
    violations from propagating incomplete dicts downstream.  (Fix H3)

    `external` is verified state the captain supplied from outside the
    pipeline (#260, `library/tools/external_inputs.py`), keyed by the
    state key it stands in for.  It is consulted only where the upstream
    output does not carry the key, so a step that really ran always
    wins, and the value handed over is the SAME value `run_scope`
    checked before agreeing to the selection - the resolver cannot
    believe something the run then cannot use.  Loaded once per run and
    passed in; None means "look it up", which the replay bench and the
    tests rely on.
    """
    inputs = {}
    if external is None:
        external = _verified_external_state(state)

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
                    elif src_key in external:
                        # State the captain produced outside the pipeline
                        # and this run verified. It reaches the step as
                        # the step's own input, so nothing downstream has
                        # to know it was not computed here.
                        inputs[dst_key] = external[src_key].value
                    elif dst_key not in optional_inputs:
                        # Fix H3: Raise on missing required mapped input
                        # instead of silently skipping, so contract
                        # violations surface immediately.
                        #
                        # This is the BACKSTOP, not the gate. Since the
                        # requirements layer landed, the same condition
                        # is refused before the run starts - and against
                        # the EXECUTE set, so --step and --from no longer
                        # slip past it (library/tools/requirements.py).
                        # Reaching here means state changed under the run
                        # or a caller built inputs directly, so it still
                        # raises rather than continuing, and it names the
                        # producer the way a refusal would.
                        raise RuntimeError(
                            f"Step '{node_id}': data_mapping expects key "
                            f"'{src_key}' from upstream step '{source_id}', "
                            f"but it is missing from that step's outputs. "
                            f"Available keys: {list(source_outputs.keys())}. "
                            f"Run the producer once so its output is on "
                            f"file: --only {source_id}"
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

    # The LAST render's QA findings, for a step that declares them.
    #
    # A DAG edge cannot carry these.  `validate` (6.02) is the final node
    # and the one step with a review job, `review_rough_cut` (3.03), sits
    # in phase 3 - an edge from the one to the other is a back edge and
    # the topological sort would refuse it.  So it travels the way the
    # other non-DAG-routable globals above do, and it is a statement
    # about STATE rather than about lineage (AGENTS.md section 3): the
    # findings describe the last render of this project, whenever that
    # happened, and `load_findings` records WHICH source answered so the
    # step is never told a previous run's report is this one's.
    #
    # Nothing is broadcast.  A step gets this because its manifest asked.
    if manifest and QA_FINDINGS_INPUT in {
            inp.get("name") for inp in
            manifest.get("interface", {}).get("inputs", [])}:
        import sys
        if str(LIBRARY_ROOT.parent) not in sys.path:
            sys.path.append(str(LIBRARY_ROOT.parent))
        from library.tools import qa_findings as _qa_findings
        inputs[QA_FINDINGS_INPUT] = _qa_findings.findings_for_review(
            _qa_findings.load_findings(state.get("project_folder", ""), state))

    # The timeline transcript, produced by
    # `python3 -m library.tools.timeline_transcript <project> --write`
    # outside the pipeline.  It cannot be a DAG step: it needs Resolve
    # open and WhisperX loaded.  Like qa_findings above, it reaches a
    # step because the manifest declares it, and the file is read from
    # the project's scratch directory.
    if manifest and TIMELINE_TRANSCRIPT_INPUT in {
            inp.get("name") for inp in
            manifest.get("interface", {}).get("inputs", [])}:
        if TIMELINE_TRANSCRIPT_INPUT not in inputs:
            project_folder = state.get("project_folder", "")
            if project_folder:
                # One spelling of where it lands, owned by the module
                # that writes it - `timeline_transcript.transcript_path`.
                from library.tools.timeline_transcript import (
                    transcript_path as _transcript_path,
                )
                transcript_path = _transcript_path(project_folder)
                if transcript_path.is_file():
                    import json as _json
                    inputs[TIMELINE_TRANSCRIPT_INPUT] = _json.loads(
                        transcript_path.read_text(encoding="utf-8"))
                    # Corrections recorded since this transcript was
                    # written apply at READ time, so a stale on-disk
                    # file never reaches a step uncorrected - and a
                    # re-transcription is never required to carry them
                    # (`library/tools/transcript_corrections.py`).
                    try:
                        from library.tools import transcript_corrections as _tc
                        _tc.apply_to_document(
                            inputs[TIMELINE_TRANSCRIPT_INPUT],
                            project_folder)
                    except Exception as _exc:  # noqa: BLE001 - a correction
                        # pass must never refuse a step its transcript.
                        print(f"Warning: transcript corrections could not "
                              f"apply ({_exc}); continuing uncorrected.",
                              file=sys.stderr)
                else:
                    # Check whether the input is required.
                    _required = True
                    for inp in manifest.get("interface", {}).get("inputs", []):
                        if inp.get("name") == TIMELINE_TRANSCRIPT_INPUT:
                            _required = inp.get("required", True)
                            break
                    if _required:
                        raise RuntimeError(
                            f"Step '{node_id}' declares required input "
                            f"'{TIMELINE_TRANSCRIPT_INPUT}' but no transcript "
                            f"file exists at {transcript_path}. Run "
                            f"'python3 -m library.tools.timeline_transcript "
                            f"<project> --write' first."
                        )

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
            bt = resolve_template_reference(
                brand_reference,
                project_folder=state.get("project_folder"))
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
                # The brief travels as a REFERENCE the model can follow,
                # not as 47,903 bytes of copy in seven prompts.  See
                # library/tools/brief_reference.py for the rule and for
                # what the copy cost: 46.9% of every byte the pipeline's
                # replayable steps sent, 41.9% of it in sections no LLM
                # planning step can act on.
                #
                # The path recorded is ABSOLUTE, because it is the path
                # the model will have to use and it runs from wherever
                # the harness put it, not from the project folder.
                from library.tools.brief_reference import (
                    build_reference, project_pinned_sections,
                    project_series_identity)
                inputs["creative_brief"] = build_reference(
                    os.path.abspath(brief_path), content,
                    pinned=project_pinned_sections(project_folder),
                    # #258: the channel brief names several series and
                    # the model reached for one by name.  The project
                    # states which one this video is - or, by passing ""
                    # when it names none, has the absence said out loud.
                    series_identity=project_series_identity(
                        project_folder))

        # The captain's context folder plus the learnings routed to this
        # step, as a MAP the model follows with its shell - never a copy
        # (library/tools/project_context.py).  Declared like the brief,
        # routed the same way, but with no attachment choice and no
        # refusal: an empty folder is a stated absence and learnings
        # simply scope to nothing, so there is no missing-file case to
        # raise on.
        if "project_context" in step_inputs:
            project_folder = (inputs.get("project_folder", "")
                              or state.get("project_folder", ""))
            from library.tools.project_context import (
                project_context_for_step)
            inputs["project_context"] = project_context_for_step(
                project_folder, node_id)

        # The captain's own notes off the built timeline, routed to the
        # step that owns the decision each one is about.  This is the ONE
        # place a routed note enters a step's context, and it is the one
        # place that can refuse: `assert_deliverable` fails the run when a
        # note is routed to a step whose manifest cannot receive it, so a
        # note can never go missing into a context that reads exactly like
        # a context with no notes at all.
        #
        # Read off DISK rather than off state, because the notes are
        # collected by a command the captain runs between builds and
        # `pipeline_data.json` is rewritten by every step.  See
        # library/tools/marker_routing.py.
        notes_project_folder = inputs.get("project_folder", "")
        if notes_project_folder:
            from library.tools import marker_routing
            routed_notes = marker_routing.route_project(notes_project_folder)
            mine = marker_routing.assert_deliverable(
                node_id, manifest, routed_notes)
            if mine:
                inputs[marker_routing.STEP_INPUT_NAME] = (
                    marker_routing.prompt_block(mine, node_id=node_id))

    from library.tools.context_projector import declared_context_fields
    declared = declared_context_fields(manifest, node_id)
    if step_type == "llm_only" and declared is not None:
        saved_project_folder = inputs.get("project_folder", "")
        saved_fps = inputs.get("project_fps")
        saved_brand_template = inputs.get("brand_template")
        saved_creative_brief = inputs.get("creative_brief")
        # Restored BY NAME like the brief: the folder map plus the
        # learnings routed to this step.  A step's allow-list neither
        # has to list the captain's context nor can drop it.
        saved_project_context = inputs.get("project_context")
        saved_timeline_notes = inputs.get("timeline_notes")
        
        from library.tools.context_projector import project_fields
        inputs = project_fields(inputs, declared)
        
        inputs["project_folder"] = saved_project_folder
        if saved_fps is not None:
            inputs["project_fps"] = saved_fps
        if saved_brand_template is not None:
            inputs["brand_template"] = saved_brand_template
        if saved_creative_brief is not None:
            inputs["creative_brief"] = saved_creative_brief
        if saved_project_context is not None:
            inputs["project_context"] = saved_project_context
        if saved_timeline_notes is not None:
            inputs["timeline_notes"] = saved_timeline_notes

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


def _truncate_log(s: str, max_len: int = 4000) -> str:
    """Return the string with the middle elided if it exceeds max_len."""
    if not s or len(s) <= max_len:
        return s
    # 1/4 head, 3/4 tail. The actual failure is typically at the very end.
    head_len = max_len // 4
    tail_len = max_len - head_len - 15  # 15 for "\n...[elided]...\n"
    return s[:head_len] + "\n...[elided]...\n" + s[-tail_len:]


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
        # BOTH streams, because a step that fails is not obliged to have
        # chosen the one we happen to read. Step 6.01 wrote its reason to
        # stdout and exited 1 for the life of the pipeline, and this
        # branch reported the stderr tail alone - so a build that refused
        # for a stated cause reached step_errors, pipeline_log.jsonl and
        # the run summary as an exit code and a truncated log. The reason
        # existed the whole time and nothing read it.
        detail = f"Step failed (exit {code}):\n  stderr: {_truncate_log(stderr)}"
        if (stdout or "").strip():
            detail += f"\n  stdout: {_truncate_log(stdout)}"
        raise RuntimeError(detail)

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Step produced invalid JSON:\n"
            f"  stdout: {_truncate_log(stdout, 500)}\n"
            f"  stderr: {_truncate_log(stderr, 500)}"
        )


def project_step_context(inputs: dict, manifest: dict = None,
                         bridge_supplied: set = None) -> dict:
    """Narrow a step's inputs to what its manifest says the PROMPT reads.

    The one place a manifest's projection is read, so the step-replay
    bench can reconstruct a context by calling it rather than by
    modelling it.  Returns `inputs` unchanged when the manifest declares
    no `context_fields` - a step declaring none is handed every byte it
    was routed (AGENTS.md 10.1).  WHERE the declaration lives is
    `context_projector.declared_context_fields`'s question, not this
    function's: one written where nothing reads it raises rather than
    reading as "declares none".  The projection itself is
    `model_task.project_context`.
    """
    from library.tools.context_projector import declared_context_fields
    return model_task.project_context(
        inputs, declared_context_fields(manifest), bridge_supplied)


def llm_output_declarations(manifest: dict, already_have: set = None) -> list:
    """What the MODEL is asked to write for this step, as declarations.

    `model_task.asked_outputs` is the rule; this reads it off a manifest.
    `operations.Operation` asks the same question in reverse - a
    post-bridge run as an operation is handed the step's inputs and NOT
    the model's answer, so it must be able to say which keys are absent
    and refuse instead of resolving a plan nobody wrote.
    """
    interface = (manifest or {}).get("interface", {}) or {}
    return model_task.asked_outputs(
        interface["llm_outputs"] if "llm_outputs" in interface else None,
        interface.get("outputs", []), already_have)


def _merge_deterministic_llm_outputs(deterministic: dict,
                                     llm_output: dict) -> dict:
    """Combine deterministic measurements and the LLM's fields.

    `review_rough_cut` writes its mechanical verdict and narrative review
    into the same declared `rough_cut_review` object. Replacing that
    object with the LLM's partial contribution erased the measured
    `passed` field, so every scoped planning rerun refused afterward.
    `rough_cut_review` is the one shared object: its mechanical verdict
    and narrative review are separate contributions. Other colliding
    outputs keep the normal last-writer behavior.
    """
    combined = dict(deterministic)
    for key, value in llm_output.items():
        existing = combined.get(key)
        if (key == "rough_cut_review"
                and isinstance(existing, dict)
                and isinstance(value, dict)):
            merged = dict(existing)
            merged.update(value)
            combined[key] = merged
        else:
            combined[key] = value
    return combined


def model_task_for_step(prompt_path: str, node_id: str,
                        manifest: dict = None) -> model_task.ModelTask:
    """The legacy step adapter: a step's manifest read as a `ModelTask`.

    The service (`library/tools/model_task.py`) knows no manifest; this
    is the one place a step's declarations become its typed question.
    `interface.llm_outputs` is what the model writes, `interface.outputs`
    what the step emits (AGENTS.md 10.1); `context_fields` is read at the
    manifest's top level by `context_projector.declared_context_fields`,
    which refuses one declared where nothing reads it.
    """
    from library.tools import pipeline_skills
    from library.tools.context_projector import declared_context_fields
    declared = declared_context_fields(manifest)
    if not manifest:
        return model_task.ModelTask(
            key=node_id, handoff_path=prompt_path, declared=False,
            context_fields=None if declared is None else tuple(declared))
    interface = manifest.get("interface")
    has_interface = "interface" in manifest
    interface = interface or {}
    llm_outputs = interface.get("llm_outputs") if "llm_outputs" in interface else None
    skills = pipeline_skills.declared_skills(manifest, node_id)
    reads_brief = any(i.get("name") == "creative_brief"
                      for i in interface.get("inputs", []) or [])
    return model_task.ModelTask(
        key=node_id,
        handoff_path=prompt_path,
        outputs=(tuple(interface.get("outputs", []))
                 if has_interface else None),
        llm_outputs=None if llm_outputs is None else tuple(llm_outputs),
        context_fields=None if declared is None else tuple(declared),
        reads_brief=reads_brief,
        skills=None if skills is None else tuple(skills),
    )


def present_llm_step(prompt_path: str, inputs: dict, node_id: str, manifest: dict = None, full_auto: str = None, llm_timeout: int = 300, bridge_supplied: set = None, retry_feedback: str = "") -> dict:
    """Present an LLM step through the model-task service.

    A step's manifest is read as a `ModelTask` (`model_task_for_step`)
    and `model_task.run_model_task` does the rest - the handshake, the
    prompt, the schema, the QA retries.  See library/tools/model_task.py.
    """
    return model_task.run_model_task(
        model_task_for_step(prompt_path, node_id, manifest), inputs,
        full_auto=full_auto, llm_timeout=llm_timeout,
        bridge_supplied=bridge_supplied, retry_feedback=retry_feedback)





def run_subprocess(script_path: Path, inputs: dict) -> dict:
    """Run a Python script via subprocess with JSON stdin/stdout."""
    code, stdout, stderr = _run_step_subprocess(
        [sys.executable, str(script_path)], inputs, script_path.name)
    if code != 0:
        err_msg = f"Script {script_path.name} failed (exit {code}):\n"
        if stdout.strip():
            err_msg += f"  stdout:\n{_truncate_log(stdout)}\n"
        if stderr.strip():
            err_msg += f"  stderr:\n{_truncate_log(stderr)}"
        raise RuntimeError(err_msg.rstrip('\n'))
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"Script {script_path.name} produced invalid JSON:\n"
            f"  stdout: {_truncate_log(stdout, 500)}\n"
            f"  stderr: {_truncate_log(stderr, 500)}"
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

    # A post-bridge rejection is a CONTRACT violation, and it used to
    # reach nobody: `present_llm_step` had already returned, so the
    # retry-with-feedback path it owns was bypassed and the only recovery
    # was resampling the same byte-identical context until an answer
    # happened to pass.  The feedback now goes back in, BOUNDED.
    # See library/tools/post_bridge_retry.py.
    # A post-bridge may also ask for ANOTHER PASS rather than reject -
    # a valid answer that is incomplete by design, which is what step
    # 2.04 does when it has measured the sections the model shortlisted
    # and wants the choice made against them. Same plumbing, different
    # cargo; the bound is its own. See library/tools/second_pass.py.
    retry_feedback = ""
    passes_made = 1
    for attempt in range(1, post_bridge_retry.MAX_ATTEMPTS + 1):
        # The pipeline-runs-it route for recallable skills: a declared
        # gating skill no receipt covers yet is run HERE when the
        # answering harness has no shell to invoke it with, and its
        # verdict seeds the same retry context a contract rejection
        # travels on. A harness with a shell invokes the skill itself -
        # its prompt says so - so nothing runs for it. Skipped under
        # mock: a replayed answer has no model to correct. See
        # library/tools/pipeline_skills.py.
        from library.tools import pipeline_skills as _skills_rt
        retry_feedback += _skills_rt.ensure_gating_receipts(
            node_id, manifest, compressed, full_auto)
        # LLM creative decision on compressed context
        try:
            llm_output = present_llm_step(
                prompt_path, compressed, node_id, manifest, full_auto,
                llm_timeout, bridge_supplied=set(pre_output),
                retry_feedback=retry_feedback,
            )
        except RenRefusal:
            # A refusal already carries its fix - rewording it here
            # would strip the shape the boundary renders.
            raise
        except Exception as e:
            raise LLMError(
                f"LLM generation failed: {e}",
                "the answering backend raised outside the refusal shape",
                "read the wrapped error above - it names the cause - fix "
                "that, then re-run")

        if isinstance(llm_output, dict) and llm_output.get("__status") == "awaiting_llm":
            return llm_output

        # The must-check rule: a declared gating skill RAN, read back
        # from its receipt on disk - never from the answer's claim that
        # it checked. The answer itself scopes the rule: a gate scoped
        # to what its skill knows needs no receipt where the answer
        # names nothing verifiable (finding 5 - an empty 4.03 plan).
        # A missing receipt travels the post-bridge retry
        # path like any other contract violation: bounded retries
        # carrying the reason, and at the bound the step FAILS with it
        # named rather than proceeding on an unchecked answer.
        if normalize_full_auto(full_auto) != "mock":
            try:
                _skills_rt.assert_gating_skills_ran(
                    node_id, manifest,
                    compressed.get("project_folder", ""),
                    llm_output=llm_output)
            except _skills_rt.GatingSkillSkipped as e:
                violation = str(e)
                if attempt >= post_bridge_retry.MAX_ATTEMPTS:
                    raise PostBridgeError(
                        f"Post-bridge failed after "
                        f"{post_bridge_retry.MAX_ATTEMPTS} attempts "
                        f"(a declared gating skill never ran): "
                        f"{violation}")
                print(f"  Gating skill unrun on attempt {attempt} "
                      f"for {node_id}, carrying the violation back to "
                      f"the model: {violation.splitlines()[0][:200]}",
                      file=sys.stderr)
                retry_feedback += post_bridge_retry.feedback_block(
                    violation, attempt)
                continue

        if not post_bridge.exists():
            result = dict(pre_output)
            if isinstance(llm_output, dict):
                result.update(llm_output)
            return result

        merge_data = dict(inputs)
        merge_data.update(pre_output)
        # Which pass this is. A post-bridge that asks for another pass
        # needs to know when it is already answering one, or it asks
        # forever and re-measures on every attempt. See
        # library/tools/second_pass.py.
        merge_data[second_pass.PASS_KEY] = passes_made
        # What the model decided, for a post-bridge that has to act on
        # it. The field was split out of the answer above, so this is the
        # only route it travels. See library/tools/decided_value.py.
        if decided_value.decides(node_id):
            merge_data[decided_value.MERGE_KEY] = decided_value.stashed(
                node_id)
        if isinstance(llm_output, dict):
            merge_data.update(llm_output)
        else:
            merge_data["llm_raw_response"] = llm_output
        # Which pass this is, for a post-bridge that turns recorded
        # drops into a correction request on the first pass (finding
        # 34). Same shape as `second_pass.PASS_KEY` above: loop state,
        # not model output, so it is set after the answer is merged.
        merge_data[post_bridge_retry.ATTEMPT_KEY] = attempt
        try:
            final = run_subprocess(post_bridge, merge_data)
        except Exception as e:
            violation = str(e)
            if attempt >= post_bridge_retry.MAX_ATTEMPTS:
                # At the bound the step FAILS carrying the last
                # violation.  It does not proceed on a best attempt the
                # way the QA loop does: a rejected post-bridge means the
                # downstream contract is unsatisfied and there is no
                # partial output to proceed with.
                raise PostBridgeError(
                    f"Post-bridge failed after "
                    f"{post_bridge_retry.MAX_ATTEMPTS} attempts "
                    f"(the violation was carried back to the model on "
                    f"each retry): {violation}")
            print(f"  Post-bridge rejected attempt {attempt} for "
                  f"{node_id}, carrying the violation back to the model: "
                  f"{violation.splitlines()[0][:200]}", file=sys.stderr)
            _logger = get_logger()
            if _logger:
                _logger.log(step_id=node_id,
                            event_type="post_bridge_rejection",
                            error=violation,
                            detail={"attempt": attempt,
                                    "of": post_bridge_retry.MAX_ATTEMPTS})
            retry_feedback += post_bridge_retry.feedback_block(
                violation, attempt)
            continue

        # A request for another pass is SPLIT OUT of the answer before
        # anything reads it - it is a message to this loop, not one of
        # the step's outputs.
        final, pass_request = second_pass.take(final)
        if pass_request and passes_made < second_pass.MAX_PASSES:
            passes_made += 1
            reason = (pass_request["reason"]
                      or "measurements for the shortlist the answer named")
            print(f"  {node_id}: second pass - {reason}", file=sys.stderr)
            _logger = get_logger()
            if _logger:
                _logger.log(step_id=node_id, event_type="second_pass",
                            detail={"pass": passes_made,
                                    "of": second_pass.MAX_PASSES,
                                    "reason": pass_request["reason"]})
            retry_feedback += second_pass.block(pass_request, passes_made)
            continue
        if pass_request:
            # At the bound the request is IGNORED and this answer stands:
            # unlike a contract rejection, an unanswered second pass
            # leaves a perfectly valid output.
            print(f"  {node_id}: a further pass was requested and the "
                  f"bound of {second_pass.MAX_PASSES} is reached - this "
                  f"answer stands", file=sys.stderr)

        # Ensure bridge outputs are preserved if post-bridge didn't explicitly return them
        result = dict(pre_output)
        if isinstance(final, dict):
            result.update(final)
        return result

    raise PostBridgeError(
        f"Post-bridge for {node_id} neither succeeded nor failed within "
        f"{post_bridge_retry.MAX_ATTEMPTS} attempts")


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

    This covers ONLY hollow or unavailable output.  A considered
    negative verdict - `validate` reporting `fail` - is complete and
    usable output, not an absence of it, so it is NOT reported here;
    see `check_validation_verdict`, which fails the run under its own
    classification instead of borrowing this one's wording.
    """
    problems = []
    if node_id in OPTIONAL_ANALYSIS_STEPS or not isinstance(output, dict):
        return problems

    def inspect(value, path):
        if not isinstance(value, dict):
            return
        if value.get("available") is False:
            # An absence with a recorded ERROR is hollow (music_analysis
            # around a captured traceback). An absence with only a
            # REASON is a considered answer - render_subtitles on an
            # empty plan returns available:false with why, which is what
            # the step has always done, and a speechless edit has no
            # subtitles to render. Flagging that fails correct output
            # (AGENTS.md 10.4); an absence that records nothing stays
            # flagged.
            if value.get("error") or not value.get("reason"):
                reason = (value.get("error") or value.get("reason")
                          or "no reason given")
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


def check_validation_verdict(node_id: str, output: dict) -> list:
    """Detect a considered negative verdict from the validation step.

    `validate` reporting `fail` or `undetermined` produced a complete,
    useful judgement - the opposite of hollow output - so it must fail
    the run under its own classification rather than borrowing
    `check_output_is_real`'s "produced no usable output" wording, which
    makes a real hollow output harder to recognise.
    """
    problems = []
    if node_id == "validate" and isinstance(output, dict):
        v = output.get("validation_result")
        if isinstance(v, dict):
            if v.get("status") in ("fail", "undetermined") or not v.get("distribution_ready", True):
                problems.append(f"Validation outcome was not successful: {v.get('status')} - {v.get('summary', 'unknown')}")
    return problems


def validate_step_output(node_id: str, output: dict, manifest: dict = None) -> list:
    """Validate a step's output against its manifest declarations.
    Returns a list of warning/error strings.

    The check is `model_task.validate_declared_output`; this reads the
    declarations off a step's manifest."""
    if not manifest or "interface" not in manifest or "outputs" not in manifest["interface"]:
        return []
    return model_task.validate_declared_output(
        node_id, output, manifest["interface"]["outputs"])


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
    target: str = None,
    only: list = None,
    skip: list = None,
    with_steps: list = None,
    overrides: list = None,
    profile: str = None,
    break_at: list = None,
    no_break_at: list = None,
):
    """Execute the pipeline DAG.

    `target`/`only`/`skip`/`with_steps` scope the run.  They are resolved
    against the DAG BEFORE anything is deleted, saved or executed, so a
    selection that cannot be met refuses in a second rather than dying
    forty minutes in.  See library/tools/run_scope.py.

    `profile` names a DECLARED run configuration - which steps fire and
    where the run stops - read from `<project>/profiles/` or
    `library/profiles/`, or adopted by the project's own project.yaml.
    It contributes the same four words the flags above do and is handed
    to the same resolver, so it cannot express a selection `run_scope`
    would refuse.  See library/tools/run_profile.py.

    `break_at`/`no_break_at` arm and disarm the review gate PER STEP.
    `review_mode` is the special case "every step".  See
    library/tools/breakpoints.py.
    """
    # Initialize logger
    get_logger(project_dir)

    dag = load_dag()
    state = load_pipeline_state(project_dir)
    try:
        from library.tools import edit_spec
        pending_specs = edit_spec.pending_note_states(project_dir)
        pending_specs = {
            note_id: state for note_id, state in pending_specs.items()
            if state.get("state") != "superseded"}
    except (OSError, ValueError) as exc:
        pending_specs = {"edit_spec_state": {
            "reason": f"edit spec state cannot be read: {exc}",
            "questions": []}}
    if pending_specs:
        lines = []
        for note_id, pending in sorted(pending_specs.items()):
            request_id = pending.get("request_id")
            request_suffix = (f" (request {request_id})"
                              if request_id else "")
            lines.append(f"note {note_id}: {pending['reason']}"
                         f"{request_suffix}")
            if pending.get("prepare_command"):
                lines.append(
                    f"  translate it with: {pending['prepare_command']}")
            elif request_id:
                lines.append(
                    "  resolve it with `ren spec resolve <project> "
                    f"--id {request_id}`")
            lines.extend(
                f"  ask the editor: {question['question']}"
                for question in pending.get("questions", []))
        reason = (
            "the edit pipeline is held because a natural-language "
            "note has no fully resolved, recorded edit spec:\n"
            + "\n".join(lines)
            + "\nTranslate or resolve every listed note, then rerun "
              "`ren edit`.")
        if dry_run:
            print(f"\n  PENDING EDIT SPEC (dry run only)\n{reason}\n",
                  file=sys.stderr)
        else:
            print(f"\n  ✗ REFUSED - pending edit spec\n{reason}\n",
                  file=sys.stderr)
            summary = {"status": "REFUSED", "reason": reason}
            json.dump(summary, sys.stdout, indent=2)
            return summary
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

    # The chat interview, conducted before anything decides. When no
    # brief was attached and a host drives (`agent` backend), the first
    # handshake request is the interview itself: the host asks the user
    # in chat and answers back through the handshake, instead of the
    # questions landing unasked in the run summary. Every other path
    # returns state untouched. See library/tools/briefing_chat.py.
    if not dry_run and not single_step:
        try:
            state = briefing_chat.conduct_if_needed(
                project_dir, state, normalize_full_auto(full_auto),
                llm_timeout=llm_timeout, save=save_pipeline_state)
        except RenRefusal:
            raise
        except Exception as exc:  # noqa: BLE001 - never fail a run here
            print(f"  Briefing interview skipped ({exc}); continuing "
                  f"without a brief.", file=sys.stderr)

    # The run profile, read first: it contributes to the selection and to
    # the breakpoints, and a profile that cannot be read must refuse
    # before --rerun deletes anything.  A profile carries no power of its
    # own - it hands `run_scope.resolve` the same four words the flags do.
    try:
        active_profile = run_profile.resolve_for_run(
            project_dir, profile, known_steps=set(nodes))
    except run_profile.ProfileError as exc:
        print("\n  ✗ REFUSED - run profile\n", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("", file=sys.stderr)
        summary = {"status": "REFUSED", "reason": str(exc)}
        json.dump(summary, sys.stdout, indent=2)
        return summary

    # The scope, resolved first.  --rerun below DELETES artifacts, and
    # apply_source_identity WRITES state, so a selection that cannot be
    # met has to be refused before either of them runs.
    selection = run_profile.compose(
        active_profile,
        target=target,
        only=tuple(only or ()),
        skip=tuple(skip or ()),
        with_steps=tuple(with_steps or ()),
    )

    # Where this run stops.  Armed per step; `--review` is the every-step
    # case.  A breakpoint never strands a consumer, so an unreachable one
    # is NAMED in the header below rather than refused.
    try:
        gates = run_breakpoints.resolve(
            known_steps=set(nodes),
            # A breakpoint may name an OPERATION as well as a DAG node.
            # The region half is unbounded, so this is the namespace, not
            # the address set; `breakpoints._reject_unknown` parses the
            # address against it. See library/tools/operations.py.
            known_operations=set(operations.names()),
            profile_breakpoints=active_profile.breakpoints,
            review_all=bool(review_mode),
            break_at=tuple(break_at or ()),
            no_break_at=tuple(no_break_at or ()),
            profile_name=active_profile.name,
        )
    except run_breakpoints.BreakpointError as exc:
        print("\n  ✗ REFUSED - breakpoints\n", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("", file=sys.stderr)
        summary = {"status": "REFUSED", "reason": str(exc)}
        json.dump(summary, sys.stdout, indent=2)
        return summary
    # State the captain produced outside the pipeline, VERIFIED once for
    # the whole run: the resolver counts it and `gather_step_inputs`
    # hands the step the same value, so the two cannot disagree. A file
    # that does not check out refuses the run here, before anything is
    # deleted or written (#260).
    try:
        external = external_inputs.load(project_dir, state)
    except external_inputs.ExternalStateError as exc:
        print("\n  ✗ REFUSED - external state does not check out\n",
              file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("", file=sys.stderr)
        summary = {"status": "REFUSED", "reason": str(exc)}
        json.dump(summary, sys.stdout, indent=2)
        return summary

    try:
        scope = run_scope.resolve(
            selection, dag=dag, manifests=manifests, state=state,
            # `--step <id>` names one step outright, and naming a step is
            # a stronger statement than any default.
            always_include=[single_step] if single_step else [],
            # A --rerun target's output is about to be thrown away, so it
            # cannot be what makes it safe to leave a producer out.
            invalidated=_rerun_invalidates(rerun, stage_by_node),
            external={key: entry.value
                      for key, entry in external.items()},
        )
    except run_scope.ScopeError as exc:
        print(f"\n  \u2717 REFUSED\n", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("", file=sys.stderr)
        summary = {"status": "REFUSED", "reason": str(exc)}
        json.dump(summary, sys.stdout, indent=2)
        return summary

    # Displays a hand may have touched since the pipeline wrote them.
    # Print-only and fully guarded: a stale display must announce
    # itself on every run, and must never stop an unrelated build.
    # `library/tools/display_drift.py` owns the witness;
    # `library/tools/layer_coherence.py` owns the layer-vs-source
    # comparison. Snapshot after clean builds with
    # `python3 -m library.tools.display_drift snapshot <project>`.
    try:
        from library.tools import display_drift as _drift
        from library.tools import layer_coherence as _coherence
        _drift.check(project_dir)
        _report = _coherence.check_project(project_dir)
        _owned = sum(len(_report.get(key, []))
                     for key in ("wording", "pins", "assets"))
        if _owned:
            print(f"LAYER COHERENCE: {_owned} owned-layer "
                  f"divergence(s) stand - run "
                  f"`python3 -m library.tools.layer_coherence "
                  f"<project>` for both values of each.",
                  file=sys.stderr)
    except Exception as _exc:  # noqa: BLE001 - a witness never refuses
        print(f"Warning: pre-run display/coherence witness skipped "
              f"({_exc}); continuing.", file=sys.stderr)

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
        apply_code_identity(state, stage_by_node, manifests, nodes)
        save_pipeline_state(project_dir, state)

    # The steps a DEFAULT run of this pipeline would attempt: the whole
    # DAG minus what is off by default.  Completeness is measured against
    # this and not against `order`, or a step that is deselected by
    # default would hold every run at PARTIAL forever.
    universe = list(scope.universe)

    print(f"\n{'═'*60}", file=sys.stderr)
    print(f"  Pipeline: edit_video", file=sys.stderr)
    print(f"  Project: {project_dir}", file=sys.stderr)
    print(f"  Steps: {len(universe)}", file=sys.stderr)
    print(f"  Order: {' → '.join(universe)}", file=sys.stderr)
    for line in external_inputs.describe(external):
        print(line, file=sys.stderr)
    for line in run_profile.describe(active_profile):
        print(line, file=sys.stderr)
    for line in run_scope.describe(scope):
        print(line, file=sys.stderr)
    print(f"{'═'*60}\n", file=sys.stderr)

    # Determine which steps to run.  The scope has already decided which
    # steps this run may touch; --from and --step narrow that further and
    # behave exactly as they always have.
    selected = list(scope.steps_to_run)
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
        for node_id in selected:
            if single_step and node_id != single_step:
                continue
            # Skip the target step's ancestors (they are presumed complete)
            # but keep the target step itself and everything after it,
            # as well as parallel branches that aren't ancestors.
            if node_id in ancestors:
                continue
            steps_to_run.append(node_id)
    else:
        for node_id in selected:
            if single_step and node_id != single_step:
                continue
            steps_to_run.append(node_id)
    
    print(f"  Steps to run: {steps_to_run}", file=sys.stderr)

    # Requirements are checked against what will EXECUTE, not against the
    # plan the scope agreed to.
    #
    # `run_scope.resolve` above was handed the SELECTION, which carries
    # neither --from nor --step.  Both narrow `steps_to_run` right here,
    # AFTER the refusal has already been computed against the wider set.
    # So `--step plan_subtitles` on a fresh project passed the scope
    # check - every producer was notionally "in this run" - and then died
    # inside `gather_step_inputs` with an unhandled traceback:
    #
    #   RuntimeError: Step 'plan_subtitles': data_mapping expects key
    #   'audio_spine' from upstream step 'mesh_spine', but it is missing
    #   from that step's outputs. Available keys: []
    #
    # Asking again against the narrowed set turns that into a REFUSED
    # before anything is written, which matters beyond ergonomics: the
    # mid-run raise is recorded as a STEP FAILURE and colours `status` on
    # every later run until that step succeeds, while a refusal is
    # traceless.
    # An override is refused BY NAME before it is honoured: a typo, or a
    # requirement that has not opted in, must not quietly disable
    # nothing. See requirements.assert_overrides_are_real.
    try:
        requirements.assert_overrides_are_real(
            overrides or (), requirements.all_requirements(dag, manifests))
    except requirements.OverrideError as exc:
        print(f"\n  \u2717 REFUSED\n", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        print("", file=sys.stderr)
        summary = {"status": "REFUSED", "reason": str(exc)}
        json.dump(summary, sys.stdout, indent=2)
        return summary

    verdict = requirements.evaluate(
        steps_to_run,
        requirements.Context(
            project_folder=str(project_dir),
            state=state,
            run_set=frozenset(steps_to_run),
            recorded=run_scope.recorded_outputs(state),
            external={key: entry.value for key, entry in external.items()},
            overrides=frozenset(overrides or ()),
        ),
        # Derived from THIS RUN's dag and manifests, never from the copy
        # on disk. A caller may hand `run_pipeline` a reduced DAG - the
        # e2e tests drive a three-node one - and requirements read off
        # the full graph would refuse it for producers that graph does
        # not contain.
        requirements.all_requirements(dag, manifests),
    )
    unmet = verdict.unmet

    # An override is not a pass, and it is never silent.
    #
    # The requirement REFUSED and a person said proceed anyway, so the
    # refusal and what it refused about go onto the run's own record -
    # `pipeline_run.json` via `overrides_applied` below, the state file,
    # and the summary. A later reader asking "was the cut rejected when
    # these captions were planned?" gets an answer instead of silence,
    # which is the whole defect the ruling was about
    # (data/decisions/rough-cut-gate.md).
    override_records = [o.as_record() for o in verdict.overridden]
    if override_records:
        print(f"\n  \u26a0 OVERRIDDEN - proceeding past a requirement that "
              f"refused\n", file=sys.stderr)
        for record in override_records:
            print(f"  {record['requirement']}: {record['refused_because']}",
                  file=sys.stderr)
            print(f"      needed by: {record['needed_by']}", file=sys.stderr)
        print("", file=sys.stderr)
        if not dry_run:
            state[REQUIREMENT_OVERRIDES_KEY] = override_records
            save_pipeline_state(project_dir, state)

    # Two answers, because the two kinds are about different things.
    #
    # A STATE requirement being unmet means the run is incoherent: a
    # value it needs will not exist and no step in this run will make
    # one. Nothing can fix that but changing the selection, so it
    # REFUSES.
    #
    # An ENVIRONMENT requirement being unmet means this machine is not
    # provisioned - `scripts/install_node_deps.sh` has not been run,
    # parselmouth is not importable. That is a different claim, and it is REPORTED rather
    # than refused, for two reasons. The repository already treats a
    # missing `remotion-subtitles/node_modules` as an ordinary state and
    # skips honestly on it in twenty-odd tests; and a run may legitimately
    # never reach the renderer - it may stop at a review gate, or be
    # scoped short. Refusing every run on a box whose checkout is not
    # bound to the store would forbid work that succeeds today, which is a gate that fails
    # correct input (AGENTS.md 10.4) - no more coverage than one that
    # cannot fail.
    #
    # The value the requirement exists for is still delivered in full:
    # the operator is told at second zero, by name, with the remedy,
    # instead of finding out thirty-eight minutes in.
    machine_side = [u for u in unmet
                    if u.requirement.kind == requirements.KIND_ENVIRONMENT]
    unmet = [u for u in unmet
             if u.requirement.kind != requirements.KIND_ENVIRONMENT]

    if machine_side:
        print(f"\n  ⚠ THIS MACHINE IS NOT READY FOR EVERY SELECTED STEP "
              f"(reported, not refused)\n", file=sys.stderr)
        for entry in machine_side:
            print(f"  {entry.satisfaction.reason}", file=sys.stderr)
            print(f"      needed by: "
                  f"{', '.join(entry.requirement.consumers)}",
                  file=sys.stderr)
        print("", file=sys.stderr)

    if unmet:
        reason = "\n".join(requirements.describe_refusal(unmet))
        if dry_run:
            # A dry run REPORTS and does not refuse.
            #
            # Its whole job is to show the plan, and the plan is still
            # the plan on a machine that has not installed Remotion yet.
            # Refusing here would make the one command that exists to
            # ANSWER "what would this run do, and what is missing"
            # decline to answer it.  The findings are printed and carried
            # in the summary, so nothing goes quiet.
            print(f"\n  ⚠ REQUIREMENTS NOT MET (reported, not refused - "
                  f"this is a dry run)\n", file=sys.stderr)
            print(reason, file=sys.stderr)
            print("", file=sys.stderr)
        else:
            print(f"\n  ✗ REFUSED\n", file=sys.stderr)
            print(reason, file=sys.stderr)
            print("", file=sys.stderr)
            summary = {"status": "REFUSED", "reason": reason}
            json.dump(summary, sys.stdout, indent=2)
            return summary

    # Where this run stops, and - loudly - anywhere it was asked to stop
    # and will not reach.  Printed against `steps_to_run` rather than the
    # scope, because --step and --from narrow it further.
    for line in gates.describe(steps_to_run):
        print(line, file=sys.stderr)

    if dry_run:
        for node_id in steps_to_run:
            impl = get_step_implementation(get_step_dir(nodes[node_id]))
            done = step_ledger.is_completed(state, node_id)
            stage = stage_by_node[node_id]
            print(f"    {'[done] ' if done else '       '}{node_id} "
                  f"({impl['type']}, {stage})", file=sys.stderr)
        estimate = run_scope.estimated_seconds(scope, dag=dag,
                                               steps=steps_to_run)
        print(f"    ({estimate['selected']}s of estimated work selected, "
              f"{estimate['skipped']}s skipped)", file=sys.stderr)
        summary = {
            "status": "DRY_RUN",
            "profile": active_profile.name,
            "profile_path": active_profile.path,
            "breakpoints": gates.as_record(steps_to_run),
            "steps_to_run": steps_to_run,
            "skipped": list(scope.skipped),
            "skip_reasons": dict(scope.reasons),
            "satisfied_from_previous_run": {
                k: list(v) for k, v in scope.from_cache.items()},
            "satisfied_from_outside": {
                k: list(v) for k, v in scope.from_external.items()},
            # The steps this run does not perform because the captain
            # handed in what they would have made. Separate from
            # `skipped`: those are steps this run declined, these are
            # steps whose work is already done.
            "not_run_because_supplied": {
                k: list(v) for k, v in scope.supplied.items()},
            "estimated_seconds": estimate,
        }
        json.dump(summary, sys.stdout, indent=2)
        return summary

    # The creative brief's versioned home.  The declared path may sit
    # outside the project folder, where no static allow-list can reach
    # it - so the run snapshots the bytes it actually read into
    # `pipeline_output/provenance/`, which the allow-list already
    # versions, and the per-build commit carries them beside the step
    # outputs they shaped.  Once per run, here, and never in
    # `gather_step_inputs`: the replay bench calls that function and it
    # must not touch the project (see `record_delivery` below).  After
    # the dry-run return above, for the same reason a dry run reports
    # and writes nothing.  See library/tools/brief_snapshot.py.
    from library.tools import brief_snapshot as _brief_snapshot
    _brief_record = _brief_snapshot.record_brief_for_run(
        project_dir, state.get("creative_brief") or "")
    if _brief_record.get("snapshotted") and not _brief_record.get("unchanged"):
        print(f"  Creative brief snapshotted from "
              f"{_brief_record['source']!r} "
              f"(sha256 {_brief_record['sha256'][:12]}…)",
              file=sys.stderr)
    elif _brief_record.get("cleared"):
        print(f"  Creative brief: {_brief_record['reason']}",
              file=sys.stderr)
    elif not _brief_record.get("snapshotted") and _brief_record.get("reason"):
        if _brief_record["reason"] not in (
                "no brief attached and no snapshot on file",):
            print(f"  Warning: {_brief_record['reason']}", file=sys.stderr)

    completed = []
    failed = []
    awaiting_llm = []
    paused_at_gate = None
    held_before_step = None

    run_mode = run_control.describe_mode(
        full_auto=full_auto, auto_mode=auto_mode, review_mode=review_mode,
        resume_mode=resume_mode, single_step=single_step, from_step=from_step,
        rerun=rerun, scope=scope, profile=active_profile, breakpoints=gates,
    )
    # The profile and the breakpoints go on the run's own account of
    # itself, so a reader that never saw the command line - the Resolve
    # panel, the dashboard, the captain tomorrow - can tell what this run
    # was configured to do and where it meant to stop.
    # The previous run's account of itself is read BEFORE it is
    # replaced.  A run that halted on a contract violation and was
    # re-run 49 seconds later used to leave a status file describing one
    # clean pass; the restart now goes on the status file, on the
    # provenance run record and on the state file, so a reader of the
    # OUTPUTS sees it. See library/tools/run_restart.py.
    # A fresh collector per run: the runner is a process, but the
    # dashboard and the tests drive `run_pipeline` more than once inside
    # one, and a declaration from the previous run is not this run's.
    undetermined.reset()
    direction_contradiction.reset()
    briefing_interview.reset()
    decided_value.reset()
    _previous_status = run_control.read_run_status(project_dir)
    _restart = run_restart.classify(_previous_status, state)
    run_control.begin_run_status(project_dir, run_mode, steps_to_run,
                                 argv=sys.argv[1:],
                                 profile=active_profile,
                                 breakpoints=gates.as_record(steps_to_run),
                                 state=state,
                                 carry_run_group=bool(resume_mode))
    print(f"  Mode: {run_mode}", file=sys.stderr)
    for _line in run_restart.summary_lines(_restart):
        print(_line, file=sys.stderr)
    if _restart.is_restart:
        run_restart.append_to_state(state, _restart)
        save_pipeline_state(project_dir, state)

    # This run's identity, and the ledger every artifact it writes is
    # recorded against. `run_control` owns the handbrake protocol and
    # says whether a run is UP; this says which run a FILE came from,
    # which outlives the run by a lot. See library/tools/provenance.py.
    _run_id = provenance.new_run_id()
    # BUILT WITH BOTH DECLARATIONS, and the reason is not the one it
    # looks like.
    #
    # `ProvenanceLedger` refuses to record an operation it cannot CHECK,
    # and it reads the two id sets it was constructed with. The runner
    # built it with NEITHER - so the obvious reading is "the guard was
    # off and an invented operation would be recorded as fact".
    #
    # Measured, and it is the opposite. An absent declaration is already
    # a REFUSAL rather than a permit, so the bare ledger refuses
    # EVERYTHING:
    #
    #   bare  + operation_id="totally.made.up"   -> REFUSED
    #   bare  + operation_id="sfx_library.validate" (real) -> REFUSED
    #   wired + operation_id="sfx_library.validate" -> recorded
    #
    # So this is not a vacuous gate being closed; it is a fail-CLOSED
    # one being given the declarations it asks for. Until it is wired the
    # runner cannot attribute ANY operation - the first firing site to
    # try would raise `ProvenanceError` and take the run down with it,
    # which is latent only for as long as nothing attributes an
    # operation.
    _provenance = provenance.ProvenanceLedger(
        project_dir, step_ids=set(nodes), operation_ids=operations.names())
    _provenance.start_run(
        _run_id, mode=run_mode,
        restart=run_restart.as_record(_restart) if _restart.is_restart else None)
    _steps_this_run = []
    print(f"  Run:  {_run_id}", file=sys.stderr)

    # Archive last-write-wins directories (reasoning traces,
    # llm_requests, llm_responses) from any previous run before this
    # run's steps overwrite them.
    archived = runs.archive_previous_run(project_dir, _run_id)
    if archived:
        print(f"  Archived previous run traces to {archived}", file=sys.stderr)

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
            # A GATE VERDICT BINDS EVERY RUN, not only one that says
            # --resume.
            #
            # This block used to sit inside `if resume_mode:`, so a
            # plain re-run walked straight past an UNANSWERED gate and
            # reported SUCCESS - measured: pause at `scan`, re-run
            # without --resume, the gate is still `pending` and the run
            # says SUCCESS. It walked past a REJECTED one too, which
            # contradicts AGENTS.md 4's "a rejected gate halts the
            # pipeline entirely" in the one mode most runs use.
            #
            # That is the gate-that-cannot-fail class (AGENTS.md 10.4):
            # a pause a flag could skip reads as review coverage that
            # is not there. Firstmate's ruling, 2026-09-05, with the
            # reasoning in `data/decisions/gate-bypass.md`: an
            # unanswered review gate HALTS THE RUN.
            #
            # WHAT THIS CHANGES FOR --resume, said plainly rather than
            # discovered: this block was the flag's ONLY behavioural
            # effect in the runner, so `--resume` no longer changes what
            # happens at a gate. It is still accepted and still names
            # the run's intent in `pipeline_run.json` via
            # `run_control.describe_mode`; whether a flag with no
            # remaining effect should be retired is a separate call and
            # is not made here.
            # The verdict is read off status.json, which is the file that
            # HAS one. This used to branch on `load_gate_feedback(...)
            # .action == "pending"`, and that branch was UNREACHABLE:
            # `save_gate_snapshot` deletes feedback.json when it arms a
            # gate, and a feedback file is only ever written carrying a
            # real verdict, so an unanswered gate has no feedback.json at
            # all and `load_gate_feedback` returns None.
            #
            # Measured on the harness below: pause at `scan`, then re-run
            # WITH --resume and without answering - `['catalog',
            # 'temporal_index']` ran and the summary said SUCCESS while
            # status.json still said `pending`. So the pause was skippable
            # in every mode, not only without the flag; the bypass this
            # change was asked to close was the narrower half of it.
            #
            # feedback.json is still read, for the one verdict that
            # carries a payload.
            from library.tools.review_gate import (
                apply_feedback_to_output, get_gate_status, load_gate_feedback)
            verdict = get_gate_status(project_dir, node_id)
            if verdict == "pending":
                print(f"  ⏸  {node_id}: gate is pending, stopping.", file=sys.stderr)
                paused_at_gate = node_id
                break
            elif verdict == "rejected":
                print(f"  ✗  {node_id}: rejected by reviewer.", file=sys.stderr)
                failed.append(node_id)
                _record_step_failure(
                    state, node_id, "rejected by reviewer at review gate")
                save_pipeline_state(project_dir, state)
                break
            elif verdict == "revised":
                feedback = load_gate_feedback(project_dir, node_id)
                if feedback:
                    outputs = state.get("step_outputs", {})
                    step_output = outputs.get(node_id, {})
                    merged = apply_feedback_to_output(step_output, feedback)
                    outputs[node_id] = merged
                    state["step_outputs"] = outputs
                    save_pipeline_state(project_dir, state)
                    print(f"  ⏭  {node_id}: revised output applied", file=sys.stderr)

            print(f"  ⏭  {node_id}: already completed", file=sys.stderr)
            # Reused, not rebuilt: the run's own record says so, with no
            # duration, because there is nothing honest to time on a step
            # that did not run. Best-effort; never fails the run.
            run_control.record_step_timing(project_dir, node_id, reused=True)
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
        inputs = gather_step_inputs(node_id, dag, state, manifest=impl.get("manifest"), step_type=impl.get("type", "unknown"), external=external)
        print(f"     Inputs: {list(inputs.keys())}", file=sys.stderr)

        # A delivery is a thing that happened, so it is recorded where a
        # re-run cannot reach it and appended rather than replaced. This
        # is what lets the captain see what a step DID with their note,
        # not just where it was routed. `gather_step_inputs` cannot write
        # it: the replay bench calls that function and must not touch the
        # project.
        _delivered = (inputs.get("timeline_notes") or {}).get("notes") or []
        if _delivered:
            from library.tools import marker_routing as _marker_routing
            print(f"     Captain's notes: {len(_delivered)} routed to this "
                  f"step", file=sys.stderr)
            _marker_routing.record_delivery(
                project_dir, node_id,
                [n.get("note_id") for n in _delivered])
        
        try:
            start_time = time.time()
            # What the output tree looks like BEFORE this step. Compared
            # against the same listing afterwards, this is how every
            # artifact learns which step wrote it - without any step
            # having to say so, which matters because half of them hand
            # the writing to ffmpeg, Remotion or Resolve.
            # See library/tools/provenance.py.
            artifacts_before = _provenance.snapshot()

            # Defined inside `for node_id in steps_to_run`, so the loop
            # variables it reads must arrive as BOUND arguments, not as a
            # closure over the loop: an unbound closure keeps whatever the
            # loop holds when the closure RUNS, not when it was defined.
            # Called immediately by the retry loop today, so binding changes
            # nothing at runtime - it is what keeps a deferred or retried
            # call from running a LATER iteration's step with no error.
            def execute_step_once(impl=impl, inputs=inputs, node_id=node_id):
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
                        step_output = _merge_deterministic_llm_outputs(
                            step_output, llm_output)
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
                
                unknown_ids, unknown_paths = unknown_clip_refs(
                    output, catalog_ids, catalog_paths)
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
            # succeeded, whatever its exit code said.  A considered
            # negative verdict from `validate` fails below it, under its
            # own classification: it produced a complete judgement, not
            # an absence of output.
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

            verdict = check_validation_verdict(node_id, output)
            if verdict:
                message = (
                    f"Step '{node_id}' returned a negative verdict:\n  - "
                    + "\n  - ".join(verdict)
                )
                print(f"     \u2717 FAILED (ValidationFailed): {message}", file=sys.stderr)
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
            # What the step COST: one honest wall-clock number on the
            # run's own record, beside the reused marks above. `elapsed`
            # is measured around the execution above, retries included.
            run_control.record_step_timing(
                project_dir, node_id, duration_s=elapsed)
            save_pipeline_state(project_dir, state)

            # What this step ran on, stamped into the run record and
            # compared against the last completed run. REPORTS ONLY -
            # the step above already ran, unconditionally, and this
            # changes nothing about that. See
            # library/tools/edit_input_digest.py.
            _stamp_step_input_digest(
                project_dir, node_id, impl, inputs, output)
            
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

            if gates.armed_at(node_id):
                _save_review_gate(project_dir, node_id, node["name"], output, inputs, state)
                gate_dir = ProjectLayout(project_dir).read_path(
                    Area.GATES, node_id)
                print(f"\n     ⏸ BREAKPOINT after {node_id}. The run stops "
                      f"here.", file=sys.stderr)
                print(f"       Snapshot: {gate_dir / 'snapshot.json'}",
                      file=sys.stderr)
                print(f"       Answer it: python3 -m library.tools.review_gate "
                      f"answer --project {project_dir} --step {node_id} "
                      f"--approve|--reject|--revise <json>", file=sys.stderr)
                print(f"       Then carry on: "
                      f"{run_breakpoints.resume_command(sys.argv, sys.executable)}",
                      file=sys.stderr)

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
    recorded_failures = sorted(set(state.get("failed_steps", [])))
    # A recorded failure naming a step this DAG no longer contains can
    # never be cleared: `_record_step_failure` removes an entry only when
    # that step SUCCEEDS, and a step with no node never runs.  Unwiring
    # `prosody_analysis` (#F5) left exactly that on 001, and holding a
    # project at FAILED forever on a step the pipeline has stopped
    # running is not a verdict about this run.  It is REPORTED by name
    # and first - never dropped - and it does not decide `status`.
    stranded_failures = [n for n in recorded_failures if n not in nodes]
    outstanding_failures = [n for n in recorded_failures if n in nodes]
    never_run = [
        node_id for node_id in universe
        if not step_ledger.is_completed(state, node_id)
        and node_id not in awaiting_llm
    ]
    # `--step`, `--from`, a scoped selection and a review-gate pause all
    # leave DAG steps unrun on purpose.  Those runs are incomplete, not
    # broken, and must not report the same status as a run whose steps
    # blew up.  A step that is off BY DEFAULT is not in `universe` at
    # all, so it does not make an ordinary run look partial.
    partial_invocation = (
        list(steps_to_run) != list(universe)
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
    if stranded_failures:
        print(f"  Recorded failures of steps no longer in this pipeline: "
              f"{len(stranded_failures)}", file=sys.stderr)
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
    if stranded_failures:
        print(f"  ! {', '.join(stranded_failures)}: recorded as failed by "
              f"an earlier run, and no longer a step of this pipeline. No "
              f"run can clear this, so it does not decide the status.",
              file=sys.stderr)
    if outstanding_failures:
        print(f"  ✗ {', '.join(outstanding_failures)}", file=sys.stderr)
        for node_id in outstanding_failures:
            err = state.get("step_errors", {}).get(node_id, "")
            err_line = (str(err).splitlines() or ["(no error message)"])[0][:160]
            print(f"      {node_id}: {err_line}",
                  file=sys.stderr)
    if never_run:
        print(f"  ○ never completed: {', '.join(never_run)}", file=sys.stderr)

    # What the render QA measured, printed where a person will see it.
    #
    # Step 6.02 has always written every one of these to
    # exports/qa_report.json and nothing has ever opened it.  Reading is
    # not gating: `status` is decided above and this block runs after it,
    # so an advisory finding cannot fail a run.  A finding no reader
    # claims is printed first and loudest rather than dropped - see
    # library/tools/qa_findings.py.
    qa_summary = {}
    try:
        from library.tools import qa_findings as _qa
        _findings = _qa.load_findings(project_dir, state)
        for _line in _qa.summary_lines(_findings):
            print(_line, file=sys.stderr)
        qa_summary = {
            "source": _findings.source,
            "source_detail": _findings.source_detail,
            "counts": _findings.counts(),
            "unrouted_metrics": [f.metric for f in _findings.unrouted],
            "reportable": [f.as_dict() for f in _findings.reportable],
        }
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the render QA findings: {exc}",
              file=sys.stderr)

    # What the rough-cut review found and could not hand to anyone.
    #
    # Step 3.03 is asked for `cut_decisions` on every run.  Its per-cut
    # verdicts go to step 4.02's cuts table; the rows that name no single
    # cut have no downstream reader and cannot be given one - a finding
    # owned by step 2.02 is not acted on by putting it in the transitions
    # prompt (library/tools/cohesion_scope.py names that shape).  So they
    # are printed, the way the render QA findings above are.  On 001's run
    # of record one of them was the passage mis-anchor, diagnosed with its
    # cause and its owning step, and nothing read it.
    #
    # Reading is not gating: `status` is decided above this block.
    review_findings = []
    try:
        from library.tools import cut_verdicts as _cv
        _cut_decisions = (state.get("step_outputs", {})
                          .get("review_rough_cut", {}).get("cut_decisions"))
        for _line in _cv.summary_lines(_cut_decisions):
            print(_line, file=sys.stderr)
        review_findings = _cv.unplaced_findings(_cut_decisions)
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the rough-cut review findings: "
              f"{exc}", file=sys.stderr)

    # What the render review saw, printed where a person will see it.
    #
    # Step 6.01's model half watches the export it just built and writes
    # `render_review`. The verdict has no downstream step to act on it -
    # 6.02's validation is the gate with teeth - so the run summary is
    # its reader, the way the rough-cut review's unplaced findings are
    # printed above. Reading is not gating: `status` is decided above
    # this block.
    try:
        from library.tools import render_review as _rr
        _render_review = (state.get("step_outputs", {})
                          .get("render", {}).get("render_review"))
        for _line in _rr.summary_lines(_render_review):
            print(_line, file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the render review: "
              f"{exc}", file=sys.stderr)

    # The captain's notes that reached NOBODY on this run.
    #
    # A note is routed to the step that owns the decision it is about,
    # and three outcomes reach no prompt: `ambiguous` (the words name two
    # steps' decisions and nothing may pick between them),`unrouted` (the
    # words name none), and routed-to-a-deterministic-step, which has no
    # prompt at all. On 001, two of the three notes the captain typed on
    # 2026-08-28 were in that set and nothing in a run ever said so, so
    # they think they were heard.
    #
    # It is REPORTED and RECORDED, never resolved: auto-resolving an
    # ambiguity is the captain's call, and marker_routing's
    # WITHDRAWN_ROUTERS records why every tie-break was refused.
    #
    # Reading is not gating: `status` is decided above this block.
    notes_reaching_nobody = []
    try:
        from library.tools import marker_routing as _mr
        _routed = _mr.route_project(project_dir)
        for _line in _mr.undelivered_summary_lines(_routed):
            print(_line, file=sys.stderr)
        _left = _mr.undelivered(_routed)
        # The note's OWN record, in the same append-only log the
        # deliveries go to.
        _mr.record_non_delivery(project_dir, _left)
        notes_reaching_nobody = [
            {"note_id": n.note_id, "outcome": why, "reason": detail}
            for n, why, detail in _left]
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the captain's routed notes: "
              f"{exc}", file=sys.stderr)

    # What the aligner measured about each passage's INSIDES.
    #
    # `alignment_report` is written on step 2.02's own output and carries
    # a real per-passage measurement of the silence between words. Two
    # steps are routed it and both drop it by name; nothing else has ever
    # opened it. On 001's run of record it recorded a 1.169s silence
    # inside a 2.982s block - 39% of it - and no step, no gate and no
    # report said so. See library/tools/alignment_findings.py.
    #
    # It ORDERS and REPORTS. No threshold fires: AGENTS.md section 6's
    # "there is no gap threshold and no voiced-fraction band" is
    # unchanged, and `status` is decided above this block.
    alignment_summary = []
    try:
        from library.tools import alignment_findings as _af
        _report = ((state.get("step_outputs", {})
                    .get("speech_sequence", {})
                    .get("speech_sequence") or {}).get("alignment_report"))
        for _line in _af.summary_lines(_report):
            print(_line, file=sys.stderr)
        alignment_summary = _af.passage_rows(_report)
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the passage alignment report: "
              f"{exc}", file=sys.stderr)

    # What the creative steps said they could NOT determine from the
    # material they were routed.  Printed after `status` is decided, and
    # recorded onto the state file so an audit of the outputs sees it
    # without the process that collected it.  It is a demand signal, not
    # a verdict: nothing here fails or assigns.
    # See library/tools/undetermined.py.
    undetermined_records = []
    try:
        for _line in undetermined.summary_lines():
            print(f"  {_line}", file=sys.stderr)
        undetermined_records = undetermined.as_records()
        if undetermined_records:
            # MERGED, not replaced.  A `--rerun music_selection` answers
            # one step, and replacing the key erased the other eight
            # steps' declarations - the narrowest possible run destroying
            # the signal.  A step this run answered replaces its own rows;
            # a step it did not reach keeps them, marked as coming from a
            # previous run so a carried row is never read as fresh.
            undetermined_records = undetermined.merge_records(
                state.get("undetermined_declarations"), undetermined_records)
            state["undetermined_declarations"] = undetermined_records
            save_pipeline_state(project_dir, state)
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the step declarations: {exc}",
              file=sys.stderr)

    # What decided each creative value this run, and on what basis. Read
    # off the deciding step's own output rather than a collector, because
    # the decision is made in a post-bridge SUBPROCESS - and because the
    # record belongs on the step's output anyway, where a reader of the
    # spec finds it beside the value it explains.
    # See library/tools/decided_value.py.
    value_decision_records = []
    try:
        for _step_id in {s.deciding_step for s in decided_value.SLOTS.values()}:
            _out = (state.get("step_outputs", {}) or {}).get(_step_id) or {}
            for _key in _out:
                value_decision_records.extend(
                    decided_value.records_from(_out.get(_key)))
        for _line in decided_value.summary_lines(value_decision_records):
            print(f"  {_line}", file=sys.stderr)
        if value_decision_records:
            # MERGED, not replaced - the sibling keys' reasoning exactly.
            value_decision_records = decided_value.merge_records(
                state.get(decided_value.STATE_KEY), value_decision_records)
            state[decided_value.STATE_KEY] = value_decision_records
            save_pipeline_state(project_dir, state)
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the value decisions: {exc}",
              file=sys.stderr)

    # Where a step's measurements contradicted the creative direction it
    # was handed.  The step FLAGGED and COMPLIED - nothing in the
    # pipeline acts on this, and escalating it is the captain's.
    # See library/tools/direction_contradiction.py.
    contradiction_records = []
    try:
        for _line in direction_contradiction.summary_lines():
            print(f"  {_line}", file=sys.stderr)
        contradiction_records = direction_contradiction.as_records()
        if contradiction_records:
            # MERGED, not replaced - the sibling key's reasoning exactly
            # (see the `undetermined` block above).  A step this run
            # answered replaces its own rows; a step it did not reach
            # keeps them, marked so a carried flag about measurements
            # that may since have moved is never read as fresh.
            contradiction_records = direction_contradiction.merge_records(
                state.get("direction_contradictions"), contradiction_records)
            state["direction_contradictions"] = contradiction_records
            save_pipeline_state(project_dir, state)
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the contradiction flags: {exc}",
              file=sys.stderr)

    # What the planning steps would have asked the captain, on a run
    # with no creative brief attached.  This is the READER that closes
    # the loop the captain asked for: they read these, and answer them
    # by writing or extending the brief and attaching it.  Printed after
    # `status` is decided; nothing here fails or assigns.
    # See library/tools/briefing_interview.py.
    briefing_records = []
    try:
        for _line in briefing_interview.summary_lines():
            print(f"  {_line}", file=sys.stderr)
        briefing_records = briefing_interview.as_records()
        if briefing_records:
            # MERGED, not replaced - the two sibling keys' reasoning
            # exactly (see the `undetermined` block above).
            briefing_records = briefing_interview.merge_records(
                state.get("briefing_questions"), briefing_records)
            state["briefing_questions"] = briefing_records
            save_pipeline_state(project_dir, state)
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the briefing questions: {exc}",
              file=sys.stderr)

    # The model answers the last reels build still owes, counted
    # across reels. A reel built with no answer on file builds without
    # that layer and says so per reel on stderr - but the per-reel
    # lines are SAID, not REPORTED, and the run summary's SUCCESS is
    # DAG-completion only, so a build that owes a model answer
    # reported success and said nothing about it. This names the
    # incomplete reels. Read from the records the build wrote, never
    # re-derived. See library/tools/awaiting_model_answers.py.
    #
    # Reading is not gating: `status` is decided above this block.
    # An unanswered ask never fails a run - a build the captain wants
    # to look at is still worth building; what is not acceptable is
    # claiming it is finished (AGENTS.md 10.4).
    awaiting_report = {"reels": [], "outstanding_answers": 0}
    try:
        from library.tools import awaiting_model_answers as _awaiting
        awaiting_report = _awaiting.collect(project_dir, state=state)
        for _line in _awaiting.summary_lines(awaiting_report):
            print(f"  {_line}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - a report must not fail a run
        print(f"  WARNING: could not read the outstanding model answers: "
              f"{exc}", file=sys.stderr)

    print(f"{'═'*60}\n", file=sys.stderr)

    # Output final state
    summary = {
        "status": status,
        "undetermined_declarations": undetermined_records,
        "direction_contradictions": contradiction_records,
        "briefing_questions": briefing_records,
        "awaiting_model_answers": awaiting_report,
        "restart": (run_restart.as_record(_restart)
                    if _restart.is_restart else None),
        "qa_findings": qa_summary,
        "rough_cut_review_findings": review_findings,
        "passage_alignment": alignment_summary,
        "notes_reaching_nobody": notes_reaching_nobody,
        "completed": completed,
        "completed_steps": len(step_ledger.all_completed(state)),
        "stage_completed": stage_done,
        "awaiting_llm": awaiting_llm,
        "failed": failed,
        "outstanding_failures": outstanding_failures,
        "stranded_failures": stranded_failures,
        "never_completed": never_run,
        "partial_invocation": partial_invocation,
        "paused_at_gate": paused_at_gate,
        "held_before_step": held_before_step,
        "run_mode": run_mode,
        "skipped": list(scope.skipped),
        "skip_reasons": dict(scope.reasons),
        "not_run_because_supplied": {
            k: list(v) for k, v in scope.supplied.items()},
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
    # This invocation's own wall, and the running total of the run
    # GROUP it belongs to - so a plan run spread over several CLI
    # invocations still sums end to end. Best-effort: an uncomputable
    # wall is left unwritten, never estimated.
    try:
        run_control.record_run_total(project_dir)
    except Exception:  # noqa: BLE001 - a stamp must not fail a run
        pass
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


def _parse_full_auto_backend(value: str) -> str:
    """Accept compatibility spellings without exposing them in help."""
    if value in {FULL_AUTO_AGENT, FULL_AUTO_DEPRECATED_AGY, "api", "mock"}:
        return value
    raise argparse.ArgumentTypeError("expected 'agent'")


def build_parser() -> argparse.ArgumentParser:
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
                       help="Arm a review gate after EVERY step. The "
                            "every-step case of --break; see "
                            "library/tools/breakpoints.py")
    parser.add_argument("--resume", action="store_true",
                       help="Resume pipeline from pending gates")
    parser.add_argument(
        "--rerun", action="append", metavar="TARGET", default=[],
        help="Redo finished work. Repeatable. TARGET is a stage "
             "(preflight|edit), a step (temporal_index), or one clip of "
             "one step (temporal_index:clip_007). This is the only "
             "supported way to re-run a completed step; --from only "
             "trims the plan.")
    run_scope.add_scope_arguments(parser)
    run_profile.add_profile_arguments(parser)
    run_breakpoints.add_breakpoint_arguments(parser)
    parser.add_argument(
        "--full-auto", type=_parse_full_auto_backend, metavar="BACKEND",
        help="Run full pipeline autonomously with the agent backend")
    parser.add_argument("--llm-timeout", type=int, default=300,
                       help="Timeout for LLM response in agent backend")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    args.full_auto = normalize_full_auto(args.full_auto)

    # Carry the driving harness into code that never sees argv: the
    # still-vision route (`library/tools/still_vision.py`) reads
    # PIPELINE_HOST_HARNESS to decide whether a host with vision
    # answers still-frame inspection or gemma does. Empty means no
    # host drives (manual runs, non-agent full-auto) - stills stay
    # on gemma, exactly as before.
    os.environ["PIPELINE_HOST_HARNESS"] = args.full_auto or ""

    # Resolve project directory from slug if provided
    project_dir = args.project
    if args.slug:
        try:
            from tools.paths import project_root
            project_dir = str(project_root(args.slug))
        except (ImportError, FileNotFoundError) as e:
            print(f"Error resolving project slug '{args.slug}': {e}", file=sys.stderr)
            sys.exit(1)
    from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal
    summary = None
    try:
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
            target=args.target,
            only=args.only,
            skip=args.skip,
            with_steps=args.with_steps,
            overrides=args.overrides,
            profile=args.profile,
            break_at=args.break_at,
            no_break_at=args.no_break_at,
        )
    except RenRefusal as refused:
        # ONE place renders every refusal on `ren edit`'s path, in the
        # same shape the front door (`manage_project.py`) renders. An
        # unexpected error keeps its traceback and exit 1.
        print(refused.render(), file=sys.stderr)
        sys.exit(REFUSAL_EXIT_CODE)
    # A refused selection never started, and must not look like a run.
    # Exit 4, the refusal code (`library/tools/ren_refusal.py`): the
    # reason already printed in the refusal shape, with its fix.
    if (summary or {}).get("status") == "REFUSED":
        sys.exit(REFUSAL_EXIT_CODE)
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
