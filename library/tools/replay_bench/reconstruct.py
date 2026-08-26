"""Rebuild one step's exact prompt and context, off frozen state, at a tree.

This file is BOTH a module and a standalone script, and the script half is
the point: comparing two revisions means importing `library.*` from a
checkout that is not this one, so the worker runs as a subprocess with the
target tree first on `sys.path` and nothing under `library/` imported at
module scope.  Import anything from `library` up here and the comparison
silently measures the current tree twice.

What it replays, and why each piece is the runner's own code rather than a
model of it:

- `gather_step_inputs` - the real DAG edge walk, the real optional-input
  rules, the real brand block.
- the step's own `bridge.py`, as a subprocess over JSON stdin, exactly as
  `run_hybrid_step` runs it.
- `project_fields` - the real projector, including the `-` drop paths.
- `json_to_toon` - the real serializer.
- `TemplateLoader.get_brand_constraints` - the real brand channel, which
  `present_llm_step` concatenates between the handoff and the context.

Two things are NOT replayed, and both are recorded on the result rather
than papered over:

- A `deterministic_with_llm` step runs `step.py` first and merges its
  output into the context.  Re-running `step.py` for `render` means
  driving Resolve, so the RECORDED output stands in for it.  That output
  has the step's own LLM answer merged into it, so the replay would feed
  the step its own answer; `llm_authored` names the keys to withhold.
  Nothing in the state records which keys those were - that is the
  per-attempt recording gap the context audit named - so they come from
  the archive's own `expected_schema`, or from the caller.
- A step's LLM call is not made.  This rebuilds the question, not the
  answer.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _load_tree(tree: Path):
    """Put `tree` first on the path and hand back the runner's own callables."""
    tree = Path(tree).resolve()
    sys.path.insert(0, str(tree))
    import library.processes.edit_video.run_pipeline as runner
    from library.processes.edit_video.run_pipeline import (
        gather_step_inputs,
        get_step_implementation,
    )
    from library.tools.context_projector import project_fields
    from library.tools.toon_serializer import json_to_toon
    # The runner's OWN projection, when the tree has it as a callable.  A
    # tree that predates the extraction has the same logic inlined in
    # `present_llm_step`, which cannot be called without calling an LLM,
    # so `_project` reproduces the inlined version for those - and that
    # version restored no pre-bridge table, which is what a pre-#201
    # archive contains.  Reproducing the tree in front of it is the whole
    # job: a reconstruction that assumed today's behaviour would report a
    # prompt no revision ever sent.
    return {
        "tree": tree,
        "gather_step_inputs": gather_step_inputs,
        "get_step_implementation": get_step_implementation,
        "project_fields": project_fields,
        "json_to_toon": json_to_toon,
        "project_step_context": getattr(runner, "project_step_context", None),
    }


def _brand_constraints(project_folder: str, node_id: str) -> str:
    """The brand channel, resolved the way `present_llm_step` resolves it."""
    from library.tools.brand_registry import (
        DEFAULT_TEMPLATE_NAME,
        project_template_name,
        reference_template_name,
    )
    from library.tools.template_loader import TemplateLoader
    name = (reference_template_name(project_template_name(project_folder))
            if project_folder else DEFAULT_TEMPLATE_NAME)
    return TemplateLoader(project_folder).get_brand_constraints(name, node_id)


def _restore_globals(projected: dict, saved: dict) -> dict:
    """`present_llm_step` puts these back after projection.  So does this."""
    projected["project_folder"] = saved.get("project_folder", "")
    for key in ("project_fps", "brand_template", "creative_brief"):
        if saved.get(key) is not None:
            projected[key] = saved[key]
    return projected


def _substitute(obj, old: str, new: str, counter: list):
    """Rewrite the snapshot's project path back to the one the run recorded.

    A reconstruction runs against the snapshot's frozen project directory,
    and `project_folder` is carried into the context verbatim.  Left alone,
    every replay would differ from the archive by one path string and by
    nothing else.  The count is reported, so a substitution that reached
    further than the path is visible rather than assumed away.
    """
    if old == new or not old:
        return obj
    if isinstance(obj, str):
        if old in obj:
            counter[0] += obj.count(old)
            return obj.replace(old, new)
        return obj
    if isinstance(obj, list):
        return [_substitute(v, old, new, counter) for v in obj]
    if isinstance(obj, dict):
        return {k: _substitute(v, old, new, counter) for k, v in obj.items()}
    return obj


def reconstruct(tree: Path, state: dict, node_id: str, project_dir: str,
                declared_project_folder: str | None = None,
                llm_authored: list | None = None,
                qa_feedback: str = "") -> dict:
    """Rebuild `node_id`'s prompt and context.  No project write, no Resolve."""
    api = _load_tree(tree)
    tree = api["tree"]
    declared = declared_project_folder or project_dir

    with open(tree / "library/processes/edit_video/dag.json", encoding="utf-8") as fh:
        dag = json.load(fh)
    nodes = {n["id"]: n for n in dag["nodes"]}
    if node_id not in nodes:
        raise KeyError(f"{node_id!r} is not a node in this tree's DAG. "
                       f"Nodes: {sorted(nodes)}")

    step_dir = tree / "library" / nodes[node_id]["step_ref"]
    impl = api["get_step_implementation"](step_dir)
    manifest = impl.get("manifest") or {}
    step_type = impl["type"]

    # The state a replay reads records the ORIGINAL project folder.  Point
    # it at the frozen copy so bridges and TemplateLoader read frozen
    # bytes, and put the recorded string back before serialising.
    state = dict(state)
    state["project_folder"] = project_dir

    inputs = api["gather_step_inputs"](node_id, dag, state, manifest=manifest,
                                       step_type=step_type)

    notes = []
    bridge_ran = False
    bridge_supplied: set = set()
    withheld = []
    if step_type == "deterministic_with_llm":
        recorded = dict(state.get("step_outputs", {}).get(node_id, {}))
        for key in (llm_authored or []):
            if key in recorded:
                recorded.pop(key)
                withheld.append(key)
        inputs.update(recorded)
        notes.append(
            "step.py not re-run: its recorded output stands in for it"
            + (f"; withheld the step's own LLM keys {withheld}" if withheld
               else "; the step's own LLM answer is still in that output"))
    elif step_type == "hybrid" and (step_dir / "bridge.py").exists():
        proc = subprocess.run(
            [sys.executable, str(step_dir / "bridge.py")],
            input=json.dumps(inputs), capture_output=True, text=True,
            encoding="utf-8", cwd=str(tree),
            env={**os.environ, "PYTHONPATH": str(tree)}, check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{node_id}: pre-bridge failed (exit {proc.returncode}):\n"
                f"{proc.stderr[-2000:]}")
        pre_output = json.loads(proc.stdout)
        inputs.update(pre_output)
        if not inputs.get("project_folder"):
            inputs["project_folder"] = project_dir
        bridge_ran = True
        bridge_supplied = set(pre_output)
        notes.append(f"pre-bridge ran, contributed {sorted(pre_output)}")

    projected_paths = manifest.get("context_fields")
    if projected_paths:
        before = set(inputs)
        if api["project_step_context"] is not None:
            inputs = api["project_step_context"](
                inputs, manifest, bridge_supplied)
        else:
            saved = {k: inputs.get(k) for k in
                     ("project_fps", "brand_template", "creative_brief")}
            saved["project_folder"] = inputs.get("project_folder", "")
            inputs = _restore_globals(
                api["project_fields"](inputs, projected_paths), saved)
        dropped = sorted(bridge_supplied & (before - set(inputs)))
        if dropped:
            notes.append(
                "this tree's runner drops a pre-bridge table the handoff "
                f"tells the model to read: {dropped}")

    subs = [0]
    inputs = _substitute(inputs, project_dir, declared, subs)

    context = api["json_to_toon"](inputs)
    if qa_feedback:
        context += qa_feedback

    prompt = ""
    prompt_path = step_dir / "handoff.md"
    if prompt_path.is_file():
        prompt = prompt_path.read_text(encoding="utf-8")

    # The schema `present_llm_step` injects, and the rule it injects by.
    expected_schema = ""
    interface = manifest.get("interface", {})
    if manifest:
        if "llm_outputs" in interface:
            llm_outputs = interface["llm_outputs"]
        else:
            already_have = set(inputs)
            llm_outputs = [o for o in interface.get("outputs", [])
                           if o.get("name") not in already_have]
        expected_schema = json.dumps(llm_outputs)
        if not llm_outputs:
            notes.append("empty schema: nothing to ask, the runner skips "
                         "this call entirely")
        else:
            from library.processes.edit_video.run_pipeline import (
                generate_output_schema_text,
            )
            schema_text = generate_output_schema_text(llm_outputs)
            marker = "<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->"
            if marker in prompt:
                prompt = prompt.replace(marker, schema_text)
            elif schema_text:
                prompt += "\n\n" + schema_text

    constraints = _brand_constraints(project_dir, node_id)

    return {
        "step_id": node_id,
        "step_type": step_type,
        "tree": str(tree),
        "prompt": prompt + constraints,
        "constraints": constraints,
        "context": context,
        "expected_schema": expected_schema,
        "context_fields": projected_paths or [],
        "top_level_keys": sorted(inputs),
        "bridge_ran": bridge_ran,
        "llm_keys_withheld": withheld,
        "project_folder_substitutions": subs[0],
        "notes": notes,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rebuild one step's prompt and context.")
    ap.add_argument("--tree", required=True)
    ap.add_argument("--state", required=True, help="frozen pipeline_data.json")
    ap.add_argument("--step", required=True)
    ap.add_argument("--project-dir", required=True,
                    help="the frozen project folder bridges read")
    ap.add_argument("--declared-project-folder", default=None)
    ap.add_argument("--llm-authored", default="",
                    help="comma-separated keys the step's own LLM wrote")
    ap.add_argument("--qa-feedback", default="",
                    help="the QA-retry block to append, for replaying a retry")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    with open(args.state, encoding="utf-8") as fh:
        state = json.load(fh)
    result = reconstruct(
        Path(args.tree), state, args.step, args.project_dir,
        declared_project_folder=args.declared_project_folder,
        llm_authored=[k for k in args.llm_authored.split(",") if k],
        qa_feedback=args.qa_feedback,
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
