"""Model-task execution: the ONE way Ren asks a model for an answer.

Punch list item 6 (captain, 2026-10-01): the file handshake, prompt
presentation, schema validation, retries, still-image handling and the
agent interaction lived inside the legacy DAG runner as
`present_llm_step`, so a project-declared creative task
(`library/tools/creative_tasks.py`) had to import the runner and dress
itself up as a step-shaped manifest to reach a model. This module is
that machinery with no runner and no manifest in it:

* a `ModelTask` is the typed question - its key, its handoff, what the
  model is asked to write, which inputs the prompt may read, and the
  role / evidence / interview a project declaration adds;
* `run_model_task` presents it and returns the validated answer.

Two adapters build a `ModelTask` and nothing else does: the runner's
`present_llm_step` reads a step's manifest
(`library/processes/edit_video/run_pipeline.py`), and
`creative_tasks.model_task` reads a project's declaration. Neither the
service nor a task imports the runner.

What lives elsewhere and is reached, never restated here: the handshake
contract (`library/tools/llm_handshake.py`), still-frame inspection
through the driving Codex CLI or Gemma fallback
(`library/tools/still_vision.py`), the QA loop
(`library/tools/qa_feedback_loop.py`), and the guards' own registries
(`craft_role`, `undetermined`, `direction_contradiction`,
`briefing_interview`, `decided_value`, `pipeline_skills`).
"""

from __future__ import annotations

import datetime
import json
import sys
import time
from dataclasses import dataclass, field

from library.tools import (
    brief_attachment,
    briefing_chat,
    briefing_interview,
    craft_role,
    decided_value,
    direction_contradiction,
    llm_handshake,
    perf_ledger,
    pipeline_skills,
    undetermined,
)
from library.tools.pipeline_logger import get_logger, step_timer
from library.tools.project_layout import Area, ProjectLayout
from library.tools.ren_refusal import RenRefusal


class LLMError(RenRefusal):
    """An LLM step that could not be answered - refused with the fix."""


def _agent_sleep(seconds: float) -> None:
    """Wait inside the agent-backend poll loop.

    A module-level name so tests stub the wait THIS loop performs
    without patching the GLOBAL `time.sleep` every other thread in
    the process calls. Measured 2026-09-15 (PR 1154's chain): a stray
    thread from an earlier test spinning on `time.sleep(0.05)` kept
    running inside later agent-stub tests, and because those stubbed
    the global sleep, every stray sleep rewrote the later test's own
    response file concurrent with its read - surfacing as an
    unreproducible `LLMError` parse failure at suite scale only.
    Patch `model_task._agent_sleep`, never `time.sleep`.
    """
    time.sleep(seconds)


def _agent_clock() -> float:
    """Clock inside the agent-backend poll loop.

    Same reason as `_agent_sleep`: tests answering the agent backend
    fast-forward THIS clock without consuming `time.time` readings a
    stray thread performs. Patch `model_task._agent_clock`, never
    `time.time`.
    """
    return time.time()


# The `--full-auto` backends.  `agent` names the MECHANISM - the pipeline
# writes a request file, an agent answers it, the pipeline reads the answer -
# never the vendor that used to supply the agent.  `agy` is that old vendor
# name and stays accepted as a deprecated alias for it, because scripts,
# briefs, saved commands and muscle memory all pass it today.
FULL_AUTO_AGENT = "agent"
FULL_AUTO_DEPRECATED_AGY = "agy"


def normalize_full_auto(full_auto: str | None) -> str | None:
    """Map a `--full-auto` value to its canonical backend name.

    `agy` becomes `agent` with a plain deprecation on stderr.  Everything
    else passes through untouched, so this is safe to call on values that
    are already canonical, on `api`/`mock`, and on None (manual LLM).
    """
    if full_auto == FULL_AUTO_DEPRECATED_AGY:
        print("--full-auto agy is deprecated; use --full-auto agent "
              "instead (the same file-handoff mechanism, renamed).",
              file=sys.stderr)
        return FULL_AUTO_AGENT
    return full_auto


@dataclass(frozen=True)
class ModelTask:
    """One question for a model, typed - no manifest, no DAG node.

    `outputs` is what the invocation as a whole emits and `llm_outputs`
    what the MODEL writes; when `llm_outputs` is None the model is asked
    for `outputs` minus whatever is already in hand (`asked_outputs`).
    `outputs` None means nothing was declared to validate against.
    `declared` False is a call that declares nothing at all: no schema
    is rendered, no guard field is asked for, nothing is validated.

    `context_fields` None hands the prompt every input; a tuple is the
    allow-list (`project_context`).

    `role`, `evidence` and `interviews_without_brief` are what a
    project-declared task adds through its own declaration; a step's
    equivalents are keyed by its id in the guards' registries, so a
    step leaves them empty.
    """

    key: str
    handoff_path: str
    declared: bool = True
    outputs: tuple[dict, ...] | None = None
    llm_outputs: tuple[dict, ...] | None = None
    context_fields: tuple[str, ...] | None = None
    reads_brief: bool = False
    skills: tuple[str, ...] | None = None
    role: str = ""
    evidence: dict[str, str] = field(default_factory=dict)
    interviews_without_brief: bool = False


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


def project_context(inputs: dict, declared, bridge_supplied: set | None = None) -> dict:
    """Narrow inputs to the allow-list `declared`, or return them whole.

    `declared` None means nothing was declared and the prompt is handed
    every byte it was routed (AGENTS.md 10.1).  The keys restored by
    name below are never the allow-list's to drop.
    """
    if declared is None:
        return inputs

    saved_project_folder = inputs.get("project_folder", "")
    saved_fps = inputs.get("project_fps")
    # None, not "default_brand": a project that declares no template
    # must stay declaring none through projection, or the restore below
    # invents a declaration the project never made.
    saved_brand_template = inputs.get("brand_template")
    saved_creative_brief = inputs.get("creative_brief")
    # Restored BY NAME for the same reason `creative_brief` is: a step's
    # allow-list neither has to list the captain's context nor can drop
    # it. AGENTS.md 10.1.
    saved_project_context = inputs.get("project_context")
    # Restored BY NAME for the same reason `creative_brief` is: a step's
    # allow-list neither has to list the captain's notes nor can drop
    # them. AGENTS.md 10.1.
    saved_timeline_notes = inputs.get("timeline_notes")
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
    inputs = project_fields(inputs, declared)

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
    if saved_project_context is not None:
        inputs["project_context"] = saved_project_context
    if saved_timeline_notes is not None:
        inputs["timeline_notes"] = saved_timeline_notes
    return inputs


def asked_outputs(llm_outputs, outputs, already_have: set | None = None) -> list:
    """What the MODEL is asked to write, as declarations.

    An invocation's OUTPUTS are what it emits; they are not what the
    model writes.  `mesh_spine`'s post-bridge computes `audio_spine` and
    `timed_spine` from a creative `structure` - asking the model for the
    computed keys made it fail QA every run and fall through to
    "proceeding with best attempt".  An explicit `llm_outputs` declares
    the model's actual contribution, and where it is absent the rule is
    the outputs minus whatever is already in hand (AGENTS.md 10.1).
    """
    if llm_outputs is not None:
        return list(llm_outputs)
    # Never ask for a key already in hand: pre-bridge outputs are merged
    # back in by the caller.
    have = set(already_have or ())
    return [o for o in (outputs or ())
            if o.get("name") not in have]


def validate_declared_output(key: str, output: dict, outputs_spec) -> list:
    """Validate an output against its declared outputs.
    Returns a list of warning/error strings."""
    issues = []
    expected_keys = set()

    for spec in outputs_spec:
        name = spec.get("name")
        if not name:
            continue
        expected_keys.add(name)

        is_required = spec.get("required", True)

        if name not in output or output[name] is None:
            if is_required:
                issues.append(f"Step '{key}' output missing required key: '{name}'")
            else:
                print(f"  Warning: Step '{key}' output missing optional key: '{name}'", file=sys.stderr)
            continue

        val = output[name]
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
                issues.append(f"Step '{key}' output '{name}' expected type {expected_type_str}, got {type(val).__name__}")

        if is_required:
            # A step may declare that an empty output is a legitimate
            # creative answer (e.g. "no visual effects needed").  The
            # flag is per-key, not per-step, because only certain keys
            # on a step may legitimately be empty.  When absent, the
            # check fires - the safe direction.
            if spec.get("may_be_empty"):
                continue

            is_empty = False
            if isinstance(val, (list, dict, str)) and len(val) == 0 or isinstance(val, int) and val == 0 and name.startswith("total_") and name != "total_failed":
                is_empty = True

            if is_empty:
                issues.append(f"Step '{key}' output '{name}' is semantically empty: {val}")

    actual_keys = set(output.keys())
    extra_keys = [k for k in actual_keys - expected_keys if not k.startswith("__")]
    if extra_keys:
        issues.append(f"Step '{key}' output has unexpected extra fields: {', '.join(extra_keys)}")

    return issues


def run_model_task(task: ModelTask, inputs: dict, full_auto: str | None = None,
                   llm_timeout: int = 300, bridge_supplied: set | None = None,
                   retry_feedback: str = "") -> dict:
    """Present `task` to the answering backend and return its answer.

    `retry_feedback` is text a caller has already established this
    model's previous answer violated.  It seeds the SAME context the QA
    loop appends its own feedback to, before the first attempt, so a
    rejection raised after this function returned - a post-bridge
    contract violation - reaches the model that caused it instead of
    being answered by a resample against a byte-identical context.  See
    library/tools/post_bridge_retry.py.
    """
    return _run(task.key, task, inputs, full_auto, llm_timeout,
                bridge_supplied, retry_feedback)


@step_timer(step_id_kwarg="key")
def _run(key: str, task: ModelTask, inputs: dict, full_auto, llm_timeout,
         bridge_supplied, retry_feedback) -> dict:
    full_auto = normalize_full_auto(full_auto)
    raw_input_tokens = len(str(inputs).split()) * 1.3

    # Clause 5 of the reference rule: a harness that cannot follow a path
    # gets the document whole.  `gather_step_inputs` reads the brief and
    # step 4.04's `bridge.py` writes the SFX catalogue, and neither has
    # any idea which backend will answer; this is the one place that
    # does, so the restore happens here rather than the reference being
    # built conditionally somewhere that would have to guess.
    #
    # `brief_reference.REFERENCED_INPUTS` is the enumeration of what can
    # come back, and the path comes out of the reference by reading the
    # SAME line the model reads.
    if full_auto:
        from library.tools.brief_reference import restore_for_harness
        inputs, _restored = restore_for_harness(inputs, full_auto)
        for _key in _restored:
            print(f"  [llm] {key}: harness {full_auto!r} cannot read "
                  f"a file - carrying {_key} inline", file=sys.stderr)

        # The same clause from the other side, for a PICTURE.  A frame
        # strip has no smaller textual form to fall back to, so a
        # harness that cannot be shown one is handed nothing and the
        # step decides from its prose - which is what it did before the
        # strips existed.  The withheld key carries a line saying so, so
        # a reconstructed context never reads as a run where no frames
        # were drawn.  See library/tools/window_frames.py.
        from library.tools.window_frames import withhold_for_harness
        inputs, _withheld = withhold_for_harness(inputs, full_auto)
        for _key in _withheld:
            print(f"  [llm] {key}: harness {full_auto!r} cannot be "
                  f"shown a picture - withholding {_key}", file=sys.stderr)

    # For hybrid steps, inputs may not be projected yet. Project them now if needed.
    inputs = project_context(inputs, task.context_fields, bridge_supplied)

    projected_input_tokens = len(str(inputs).split()) * 1.3

    prompt_additions = inputs.pop("__prompt_additions", {})

    from library.tools.toon_serializer import json_to_toon
    toon_str = json_to_toon(inputs)

    toon_input_tokens = len(toon_str.split()) * 1.3
    reduction_pct = 100 * (1 - (toon_input_tokens / raw_input_tokens)) if raw_input_tokens > 0 else 0

    logger = get_logger()
    if logger:
        logger.log(
            step_id=key,
            event_type="llm_token_stats",
            token_count={
                "raw": int(raw_input_tokens),
                "projected": int(projected_input_tokens),
                "toon": int(toon_input_tokens),
                "reduction_pct": round(reduction_pct, 1)
            }
        )

    with open(task.handoff_path) as f:
        prompt = f.read()

    for marker, text in prompt_additions.items():
        if marker in prompt:
            prompt = prompt.replace(marker, text)

    # A table the prompt describes, arriving with zero rows, is reported
    # HERE - on the run that produces it - rather than found by an audit
    # weeks later.  Twice now it has been the second: `cuts_toon` (#218)
    # and `sfx_candidates_toon` (#223).  It is a report and never a gate:
    # an empty table can be the correct answer, and a gate that fails
    # correct output is not coverage (AGENTS.md 10.4).
    from library.tools.empty_table_guard import report_step_context
    _empty_tables = report_step_context(key, toon_str, prompt)
    if _empty_tables and logger:
        logger.log(
            step_id=key,
            event_type="empty_context_table",
            detail={
                "tables": [
                    {"key": t.key, "columns": list(t.columns),
                     "named_in_prompt": t.named_in_prompt}
                    for t in _empty_tables
                ]
            },
        )

    project_folder = inputs.get("project_folder", "")
    # TemplateLoader resolves by NAME against the project's own
    # brand.json first, then its templates dir.  Ask the PROJECT for
    # the name rather than fishing it out of `inputs`, for the
    # same reason delivery_format is a function of the project: a value in
    # flight can be renamed, defaulted and lost, and this one was - the
    # read here was `inputs.get("brand_template", "default_brand")` and the
    # key was never set, so every project's LLM brand constraints came from
    # default_brand.yaml whatever its project.yaml declared.
    from library.tools.brand_registry import (
        project_template_name,
        reference_template_name,
    )
    # "" when the project declares none, and `get_brand_constraints`
    # answers "" for it.  This read used to fall back to `default_brand`,
    # so a template-less project's step 2.01 was told the series runs at
    # "high" energy and its step 4.03 was told to plan VFX at 0.5 - taste
    # from a template nobody selected, in the prompt.
    brand_template = (reference_template_name(project_template_name(project_folder))
                      if project_folder else "")
    from library.tools.template_loader import TemplateLoader

    loader_instance = TemplateLoader(project_folder)
    constraints = loader_instance.get_brand_constraints(brand_template, key)

    from library.tools.qa_feedback_loop import LLMStepQA
    qa_loop = LLMStepQA(max_retries=2)
    current_context = toon_str
    if retry_feedback:
        current_context += retry_feedback
    # What the commissioner said in the chat interview, when one was
    # conducted at the start of this run: invocations that read the
    # `creative_brief` read the answers the way they read an attached
    # brief. Best-effort and never fatal - a run without answers reads
    # exactly what it read before. See library/tools/briefing_chat.py.
    try:
        if task.reads_brief:
            current_context = briefing_chat.prepend_answers(
                current_context, project_folder)
    except Exception:  # noqa: BLE001, S110 - context must never fail a step
        pass
    best_output = None

    expected_schema_str = ""
    validated_outputs = None
    if task.declared:
        llm_outputs = asked_outputs(
            task.llm_outputs, task.outputs,
            set(inputs) | set(bridge_supplied or ()))

        if inputs.get("timeline_notes") and llm_outputs:
            llm_outputs.append({
                "name": "note_acknowledgements",
                "type": "list",
                "required": True,
                "description": "Per-note acknowledgements. Each: note_id (the exact ID of the note you are acknowledging), action (what you did about it), rationale (why you did it, or why you declined to act - a reasoned decline is a valid acknowledgement)."
            })

        # `expected_schema_str` is NOT built here: it is built from
        # `schema_outputs` below, which is `llm_outputs` plus whatever
        # else the RENDERED schema asks for.  Building it here asked the
        # agent request file to describe a schema that did not yet exist.
        # See the note beside `schema_outputs`.

        # Nothing to ask.  An invocation reaches here with an empty
        # schema when every key it declares has already been produced -
        # by its own step.py, or by its pre-bridge - so there is no
        # question left for a model to answer.  `semantic_analysis` is
        # the standing case: its schema is `[]`, its handoff says in so
        # many words that no model authors the per-clip analysis, and its
        # answer every run was the three bytes `{}` for 33,000 tokens of
        # vision documents.  The call is skipped rather than made and
        # discarded.  This is a property of the schema, not of any step's
        # name: declare `interface.llm_outputs` and the call happens
        # again.
        # An invocation that DECIDES a creative value has something to
        # ask even when it declares no `llm_outputs`: its question is the
        # appended `value_decisions` field, and its answer is split back
        # out rather than becoming one of the outputs.  Step 5.02 is the
        # standing case - what it emits is the automation it computes,
        # and what it asks is how far above the bed the voice sits.  See
        # library/tools/decided_value.py.
        if not llm_outputs and not decided_value.decides(key):
            print(f"  [llm] {key}: no LLM output declared - "
                  f"nothing to ask, skipping the call", file=sys.stderr)
            return {}

        if task.outputs is not None:
            validated_outputs = llm_outputs

        # Recallable skills: what this invocation may call, and what it
        # must.  Empty for one that declares none, so this is one
        # unconditional block and an invocation that gains a skill needs
        # no change here. The reach is asserted on the same run: a
        # declared skill the prompt never carries fails rather than
        # reading as one that declares none.
        # See library/tools/pipeline_skills.py.
        prompt += pipeline_skills.skills_block(task.skills, full_auto)
        pipeline_skills.assert_skills_reach_prompt(
            key, task.skills, prompt)

        # Who the model IS when it answers.  PREPENDED rather than
        # appended, which is the one thing this does differently from the
        # schema appenders below: they ask for an extra FIELD and belong
        # beside the schema, and a role is the frame the rest of the
        # document is read in.  A step's role is keyed by its id
        # (`craft_role.prompt_block`, empty for a step with none); a
        # project-declared task's comes in `task.role`, rendered through
        # the same renderer, and sits in front of it.
        # See library/tools/craft_role.py, library/tools/creative_tasks.py.
        prompt = task.role + craft_role.prompt_block(key) + prompt

        # The declaration is asked for in the RENDERED schema and is NOT
        # one of the validated outputs: `validate_declared_output` would
        # then demand it, and the answer is taken back out below before
        # anything validates or reads it.
        # See library/tools/undetermined.py.
        schema_outputs = list(llm_outputs)
        if undetermined.declares(key):
            schema_outputs.append(undetermined.schema_entry())
            prompt += undetermined.prompt_block()

        # Where the invocation's own MEASUREMENTS disagree with the
        # creative direction it inherited.  Same route, same reason, and
        # the same rule that it never becomes one of the outputs.
        # A project-declared task holds the measurements its own
        # declaration names (`task.evidence`) rather than routed ones, so
        # its flag is rendered from those through the same builder.
        # See library/tools/direction_contradiction.py.
        if direction_contradiction.flags(key) or task.evidence:
            schema_outputs.append(direction_contradiction.schema_entry())
            if direction_contradiction.flags(key):
                prompt += direction_contradiction.prompt_block(key)
            else:
                prompt += direction_contradiction.prompt_block_for(
                    task.evidence)

        # No creative brief was attached, so the model is asked what it
        # would have needed to know rather than planning in silence.
        # CONDITIONAL, unlike its two siblings: an invocation handed the
        # captain's own brief and then asked what it wished the captain
        # had said is being invited to manufacture a gap.
        #
        # The condition is read off `inputs` and not off state, because
        # that is the same fact at the point of use: for an invocation
        # that reads `creative_brief`, the key is in `inputs` exactly
        # when the project attached one. One source, no second place
        # that can answer differently.
        # See library/tools/briefing_interview.py.
        brief_attached = bool(inputs.get("creative_brief"))
        if _interview_asked(task, brief_attached):
            schema_outputs.append(briefing_interview.schema_entry())
            try:
                _attachment = brief_attachment.read_declaration(
                    inputs.get("project_folder", ""))
                _reading, _basis = _attachment.reading, _attachment.basis
            except Exception:  # noqa: BLE001 - wording only; never fatal
                _reading, _basis = "", ""
            prompt += briefing_interview.prompt_block(_reading, _basis)

        # The creative values this invocation DECIDES, asked for over the
        # measurements its own bridge put in front of the model.  The
        # fourth appender, and the same three rules as its siblings: the
        # words live in ONE place rather than in N handoffs, the answer
        # is split back out before anything validates it, and nothing
        # here states how the judgement should come out.
        #
        # Unlike its siblings it is LOAD-BEARING - the step cannot write
        # its automation without it - so `run_hybrid_step` hands what was
        # split out to the post-bridge under `decided_value.MERGE_KEY`.
        # See library/tools/decided_value.py.
        if decided_value.decides(key):
            schema_outputs.append(decided_value.schema_entry())
            prompt += decided_value.prompt_block(key)

        # `schema_outputs` is now complete, and it is rendered TWICE: as
        # prose for the prompt, and as JSON for the agent request file's
        # `expected_schema`.  The two renderings are kept adjacent and
        # BELOW every appender on purpose.
        #
        # The JSON one used to be built from `llm_outputs`, above the
        # appenders, so `could_not_determine` reached the answering agent
        # in `prompt` and not in `expected_schema` - and an agent reading
        # the machine-readable half, a completely natural shortcut, never
        # emitted it.  All nine declaring steps then recorded
        # `not_declared`: the NON-ANSWER reading, filled with false
        # non-answers, in the one mode the pipeline actually runs in.
        # `direction_contradiction` above is the second appender and would
        # have been lost the same way.  Anything appended next goes ABOVE
        # this line, and then it cannot be.
        expected_schema_str = json.dumps(schema_outputs)
        schema_text = generate_output_schema_text(schema_outputs)
        marker = "<!-- OUTPUT_SCHEMA: auto-injected from manifest.json -->"
        if marker in prompt:
            prompt = prompt.replace(marker, schema_text)
        elif schema_text:
            prompt += "\n\n" + schema_text

    for attempt in range(qa_loop.max_retries + 1):
        parsed_result = None

        if full_auto == "mock":
            bak_file = ProjectLayout(project_folder).read_path(
                Area.LLM_RESPONSES_BAK, f"{key}.json")
            if bak_file.exists():
                print(f"  [MOCK] Reading LLM response from {bak_file}", file=sys.stderr)
                with open(bak_file, "r") as f:
                    parsed_result = json.load(f)
            else:
                raise LLMError(
                    f"mock response not found at {bak_file}",
                    "mock replays a recorded answer, and no answer was "
                    "recorded for this step",
                    f"place the recorded answer JSON at {bak_file} "
                    f"(archived from an answered run), then re-run with "
                    f"--full-auto mock")

        elif full_auto == "agent":
            parsed_result = _answer_by_handshake(
                key, project_folder, prompt, constraints, current_context,
                expected_schema_str, llm_timeout, raw_input_tokens, logger)

        if full_auto == "api":
            # API calls are out of scope: Ren answers LLM steps through
            # the host harness (OAuth CLIs like Claude Code, Codex and
            # opencode via the agent-mode file handshake), never through
            # a provider API key. `LLMClient` and its silent "{}" are
            # gone; this refuses in the refusal shape rather than
            # guessing.
            raise LLMError(
                "--full-auto api was removed",
                "Ren answers LLM steps through the host harness (the "
                "agent-mode file handshake), never through a provider "
                "API call",
                "re-run with --full-auto agent")

        parsed_result = _split_guard_fields(task, parsed_result, inputs,
                                            logger)

        def validate_for_llm(nid, out, outputs_spec):
            issues = validate_declared_output(nid, out, outputs_spec)
            if issues:
                raise RuntimeError("Validation failed:\n" + "\n".join(f"- {i}" for i in issues))

        passed, feedback = qa_loop.run_checks(
            key, parsed_result, validated_outputs,
            validate_for_llm if validated_outputs is not None else None)
        if passed:
            return parsed_result

        best_output = parsed_result
        if attempt < qa_loop.max_retries:
            print(f"  QA failed on attempt {attempt+1}, retrying: {feedback}", file=sys.stderr)
            current_context += f"\n\nQA Feedback from previous attempt:\nThe previous output failed validation: {feedback}\nPlease correct this."

    if not best_output:
        raise RuntimeError(f"Step '{key}' produced no LLM output (silent no-answer).")
    print(f"  Warning: QA failed after {qa_loop.max_retries} retries for {key}, proceeding with best attempt.", file=sys.stderr)
    return best_output


def _interview_asked(task: ModelTask, brief_attached: bool) -> bool:
    """Whether this invocation is interviewed for a missing brief.

    A step by `briefing_interview.asks`, which reads step manifests; a
    project-declared task by its own declaration, the same rule.
    """
    return (briefing_interview.asks(task.key, brief_attached)
            or (task.interviews_without_brief and not brief_attached))


def _answer_by_handshake(key, project_folder, prompt, constraints,
                         current_context, expected_schema_str, llm_timeout,
                         raw_input_tokens, logger) -> dict:
    """File the request, wait for the host's answer, return it parsed.

    The handshake contract - the request's shape, what a valid response
    is, how a malformed one refuses - is `library/tools/llm_handshake.py`.
    """
    if not project_folder:
        raise LLMError(
            "project_folder required in inputs for agent backend",
            "the agent backend files the request under the "
            "project's pipeline_output, so without it there is "
            "nowhere to write the handoff",
            "this is a caller bug, not a usage bug - fix the "
            "caller to pass project_folder in the step inputs")

    _layout = ProjectLayout(project_folder)
    requests_dir = _layout.write_dir(Area.LLM_REQUESTS)
    responses_dir = _layout.write_dir(Area.LLM_RESPONSES)

    req_file = requests_dir / f"{key}.json"
    res_file = responses_dir / f"{key}.json"

    req_data = {
        "step_id": key,
        # The brand's constraints are PROMPT TEXT: the request
        # carries `prompt + constraints`, placing them between
        # the handoff and the context the way the prompt reads.
        # Recorded separately too, so the archive shows what
        # the brand contributed to a call - the answering agent
        # once never saw them, and the separate field is what
        # proves it does now.
        "prompt": prompt + constraints,
        "constraints": constraints,
        "context": current_context,
        "expected_schema": expected_schema_str,
        "project_folder": project_folder,
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat()
    }

    llm_handshake.publish_request(req_file, res_file, req_data)

    print(f"{llm_handshake.READY_MARKER}: {req_file}", file=sys.stdout)
    sys.stdout.flush()

    print(f"  Waiting for agent response for {key} (timeout {llm_timeout}s)...", file=sys.stderr)
    start_wait = _agent_clock()
    start_time_llm = _agent_clock()

    while _agent_clock() - start_wait < llm_timeout:
        if res_file.exists():
            _agent_sleep(0.5)
            try:
                with open(res_file, "r") as f:
                    res_content = f.read()
                # The handshake owns what a response may be; a
                # malformed one refuses WITH the fix (which file to
                # repair, how to resume).
                parsed_result = llm_handshake.validate_response(
                    key, res_content, project_folder)
            except LLMError:
                raise
            except llm_handshake.HandshakeRefusal as e:
                raise LLMError(e._ren.what, e._ren.why, e._ren.fix)
            except Exception as e:  # noqa: BLE001 - refused with the fix
                raise LLMError(
                    f"failed to read or parse agent LLM response "
                    f"as JSON: {e}",
                    f"the answer at {res_file} is not the JSON the "
                    f"request's expected_schema asked for",
                    f"write the answer as one JSON object matching "
                    f"the request's expected_schema to {res_file}, "
                    f"then the run picks it up on retry")

            # The host model's wait, priced. Tokens are the same
            # word-count estimate the run log carries, and say so.
            perf_ledger.record(
                "host_model", _agent_clock() - start_time_llm,
                backend="agent", calls=1, tokens_estimated=True,
                input_tokens=int(raw_input_tokens + len(prompt.split()) * 1.3),
                output_tokens=int(len(res_content.split()) * 1.3))
            if logger:
                response_tokens = len(res_content.split()) * 1.3
                logger.log(
                    step_id=key,
                    event_type="llm_generation",
                    backend="agent",
                    latency=round(_agent_clock() - start_time_llm, 2),
                    token_count={
                        "prompt": int(raw_input_tokens + len(prompt.split()) * 1.3),
                        "response": int(response_tokens)
                    }
                )
            return parsed_result
        _agent_sleep(2)

    raise LLMError(
        f"Timeout ({llm_timeout}s) waiting for agent LLM "
        f"response at {res_file}",
        f"the request is filed at {req_file} and no answer "
        f"arrived before the timeout",
        f"answer the request: write one JSON object matching "
        f"the request's expected_schema to {res_file} (the "
        f"run prints LLM_REQUEST_READY with the request path), "
        f"then re-run - or re-run with a larger --llm-timeout")


def _split_guard_fields(task: ModelTask, parsed_result, inputs: dict,
                        logger):
    """Take the guard fields back out of an answer, recording each.

    What the model could not determine, its briefing questions, its
    contradiction flag and its decided values are SPLIT OUT here, before
    anything validates or reads the answer: none is one of the outputs.
    An absent field is recorded as a non-answer and never as "nothing
    was missing".  The predicates are recomputed here rather than
    carried from the schema section, which only runs when something
    was declared to ask: a call with no declaration still splits an
    answer's fields out.
    """
    key = task.key
    parsed_result, _declaration = undetermined.take(key, parsed_result)
    if undetermined.declares(key):
        undetermined.record(_declaration)
        if logger:
            logger.log(
                step_id=key,
                event_type="undetermined_declaration",
                detail=undetermined.as_records([_declaration])[0],
            )

    brief_attached = bool(inputs.get("creative_brief"))
    _asked = _interview_asked(task, brief_attached)
    parsed_result, _interview = briefing_interview.take(
        key, parsed_result, brief_attached, _asked)
    if _asked:
        briefing_interview.record(_interview)
        if logger:
            logger.log(
                step_id=key,
                event_type="briefing_questions",
                detail=briefing_interview.as_records([_interview])[0],
            )

    # The task's own declared evidence, or None for the derivation -
    # which is every step.  Compliance is structural: the output that
    # leaves here is the one produced without the field, so an
    # invocation that flags cannot deviate.
    _take_evidence = task.evidence or None
    parsed_result, _flag = direction_contradiction.take(
        key, parsed_result, _take_evidence)
    if direction_contradiction.flags(key) or _take_evidence:
        direction_contradiction.record(_flag)
        if logger:
            logger.log(
                step_id=key,
                event_type="direction_contradiction",
                detail=direction_contradiction.as_records([_flag])[0],
            )

    # An entry naming a slot this invocation does not decide, or
    # carrying no `why`, is dropped inside `take` rather than travelling:
    # a mix level nobody can review is what this route exists to replace.
    parsed_result, _decided = decided_value.take(key, parsed_result)
    if decided_value.decides(key):
        decided_value.stash(key, _decided)
        if logger:
            logger.log(
                step_id=key,
                event_type="value_decisions_answered",
                detail={"answered": decided_value.stashed(key)},
            )
    return parsed_result
