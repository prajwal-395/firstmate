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
        # The runner assembles state in TWO halves and the bench read only
        # the first.  See `_run_level_state` below.
        "load_pipeline_state": getattr(runner, "load_pipeline_state", None),
    }


def _run_level_state(api, state: dict, project_dir: str, notes: list) -> dict:
    """Add the half of state the runner reads off project.yaml and the env.

    `load_pipeline_state` is the runner's whole state assembly: it parses
    `pipeline_data.json` and then overlays the values that belong to the
    RUN rather than to any upstream step - `creative_brief` and
    `brand_template` off `project.yaml`, `sfx_library` and `music_library`
    off the environment.  The bench used to `json.load` the frozen state
    file and stop there, which is a MODEL of that assembly missing its
    second half, and it fails in the one direction that matters: a project
    declaring a brief reconstructs as a project declaring none, so a
    routing change reads as a no-op.  Measured on 001 the day it was
    pointed at the channel brief - seven steps declare `creative_brief`
    and all seven replayed without it.

    The frozen bytes stay authoritative: only keys the snapshot does not
    already carry are added, so a `--state` override still governs
    everything it names.
    """
    loader = api.get("load_pipeline_state")
    if loader is None:
        notes.append("this tree has no load_pipeline_state: the run-level "
                     "half of state (creative_brief, brand_template, the "
                     "asset libraries) is not reconstructed")
        return state
    overlaid = loader(project_dir)
    added = sorted(k for k in overlaid if k not in state)
    for key in added:
        state[key] = overlaid[key]
    if added:
        notes.append("run-level state read off project.yaml and the "
                     f"environment, as the runner reads it: {added}")
    return state


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

    notes = []
    state = _run_level_state(api, state, project_dir, notes)

    inputs = api["gather_step_inputs"](node_id, dag, state, manifest=manifest,
                                       step_type=step_type)

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

    # The bench reconstructs what a tree REALLY sent, so a declaration
    # that tree never read is reported and then honoured as that tree
    # honoured it - which is to say not at all. Raising here would make
    # the bench refuse to show the very context the defect produced.
    from library.tools.context_projector import (
        MisplacedContextFields, declared_context_fields,
    )
    try:
        projected_paths = declared_context_fields(manifest, node_id)
    except MisplacedContextFields as inert:
        projected_paths = None
        notes.append(f"NOT PROJECTED: {inert}")
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
        if not llm_outputs:
            expected_schema = json.dumps(llm_outputs)
            notes.append("empty schema: nothing to ask, the runner skips "
                         "this call entirely")
        else:
            from library.processes.edit_video.run_pipeline import (
                generate_output_schema_text,
            )
            # The runner appends fields to the RENDERED schema and
            # their instructions to the prompt - `could_not_determine` for
            # the declaring steps, `contradicts_direction` for the
            # flagging ones - and PREPENDS a role for the steps that
            # declare one.  Reconstructing without it makes
            # every declaring step read as a difference `verify` cannot
            # account for - and `verify` is a gate, so a reconstruction
            # that cannot reproduce the past cannot be trusted to compare
            # futures.  Mirrors `present_llm_step`, in the same order.
            # A revision that predates one of the modules has nothing to
            # import, and that is a real difference between the trees
            # rather than something to paper over - so it is NOTED, not
            # swallowed.  A further appender in the runner needs a block
            # here.
            #
            # `craft_role` is the fourth contribution and the only one
            # that PREPENDS: it is the role the rest of the prompt is
            # read in, not an extra field, so it carries no schema entry.
            try:
                from library.tools import craft_role
            except ImportError:
                notes.append("this tree has no library.tools.craft_role: "
                             "the prompt is reconstructed with no role "
                             "block prepended")
            else:
                prompt = craft_role.prompt_block(node_id) + prompt

            # A project-declared task's role, evidence and interview are
            # read off the project's own declaration rather than the
            # step tables above, so they need the declaration loaded.
            # Empty for every step id; a task key that loads nothing is
            # NOTED rather than reconstructed quietly. Mirrors
            # `present_llm_step`, in the same order. A revision that
            # predates the module has nothing to import, and that is a
            # real difference between the trees rather than something to
            # paper over - so it is NOTED, not swallowed.
            _task_evidence: dict = {}
            _task_interview = False
            try:
                from library.tools import creative_tasks as _creative_tasks
            except ImportError:
                notes.append("this tree has no "
                             "library.tools.creative_tasks: a "
                             "project-declared task prompt is reconstructed "
                             "without its role block")
            else:
                if _creative_tasks.is_task_key(node_id):
                    try:
                        prompt = _creative_tasks.prepend_task_role(
                            project_dir, node_id, prompt)
                        _task_evidence = _creative_tasks.task_evidence(
                            project_dir, node_id)
                        _task_interview = _creative_tasks.asks_interview(
                            project_dir, node_id,
                            bool(inputs.get("creative_brief")))
                    except Exception as exc:  # noqa: BLE001 - wording only
                        notes.append(f"project-declared task {node_id} did "
                                     f"not load: {exc}")

            schema_outputs = list(llm_outputs)
            try:
                from library.tools import undetermined
            except ImportError:
                notes.append("this tree has no library.tools.undetermined: "
                             "the schema is reconstructed without the "
                             "could_not_determine field")
            else:
                if undetermined.declares(node_id):
                    schema_outputs.append(undetermined.schema_entry())
                    prompt += undetermined.prompt_block()
            try:
                from library.tools import direction_contradiction
            except ImportError:
                notes.append("this tree has no "
                             "library.tools.direction_contradiction: the "
                             "schema is reconstructed without the "
                             "contradicts_direction field")
            else:
                if direction_contradiction.flags(node_id):
                    schema_outputs.append(
                        direction_contradiction.schema_entry())
                    # Per step, because its evidence sources are.
                    prompt += direction_contradiction.prompt_block(node_id)
                elif _task_evidence:
                    schema_outputs.append(
                        direction_contradiction.schema_entry())
                    prompt += direction_contradiction.prompt_block_for(
                        _task_evidence)
            try:
                from library.tools import brief_attachment, briefing_interview
            except ImportError:
                notes.append("this tree has no "
                             "library.tools.briefing_interview: the schema "
                             "is reconstructed without the "
                             "briefing_questions field")
            else:
                # CONDITIONAL, unlike the two above: asked only on a run
                # with no brief attached.  The condition is the archived
                # inputs' own `creative_brief`, which is the same fact
                # `present_llm_step` reads at the same point.
                _attached = bool(inputs.get("creative_brief"))
                if briefing_interview.asks(node_id, _attached) or _task_interview:
                    schema_outputs.append(briefing_interview.schema_entry())
                    try:
                        _a = brief_attachment.read_declaration(project_dir)
                        _reading, _basis = _a.reading, _a.basis
                    except Exception:  # noqa: BLE001 - wording only
                        _reading, _basis = "", ""
                    prompt += briefing_interview.prompt_block(_reading, _basis)
            expected_schema = json.dumps(schema_outputs)
            schema_text = generate_output_schema_text(schema_outputs)
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
