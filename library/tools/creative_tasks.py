"""A project declares a creative task the pipeline invokes, instead of adding a step.

Reel selection and similar judgements are project-shaped, so the engine
does not carry a permanent step most projects never run (captain,
2026-09-04).  A project declares the task in `pipeline.creative_tasks`;
the pipeline invokes it.

The current contract
--------------------
**The forcing function is the whole point.**  A declared task is invoked
through the SAME call steps go through - the model-task service itself
(`library/tools/model_task.py`), not a second mechanism - and the three
guards reconcile against the declaration rather than a step id:

* the task's role is PREPENDED to its handoff by the shared renderer
  (`craft_role.render_block`, through `role_block` and
  `prepend_task_role`);
* the floors gate reads the task's prompt, and a task whose role,
  handoff or output descriptions demand a count is REFUSED at
  declaration time against the same enumeration steps are read for
  (`library/tools/creative_floors.py`);
* a task that takes the direction with declared evidence gets the
  contradiction field rendered from its own evidence
  (`direction_contradiction.prompt_block_for`, fed by `task_evidence`);
  every invoked task gets the undetermined field;
* a task that declares `creative_brief` among its inputs is interviewed
  when no brief is attached, rather than planning in silence
  (`asks_interview`).

A task key lives in its own namespace, `task:<name>` (`TASK_PREFIX`,
`task_key`, `is_task_key`, `task_name`).  A name shadowing a step id,
carrying the separator, or naming a path is refused.

What a task is, in `pipeline.creative_tasks` - a list of mappings:

    - name: reel_pick                  # unique, not a step id, no ':'
      role:                             # the same completeness rule a
        discipline: short-form editor   # step's role answers to
        addressed_as: You are ...
        reads_with: [...]
        decides: [...]
        defers: [...]
      handoff: tasks/reel_pick.md       # project-relative, must exist
      inputs: [timeline_transcript]     # state keys the prompt may read
      outputs:                          # non-empty, or there is nothing
        - name: reel_selection          # to ask and no call is made
          type: object
          description: The chosen stretches.
      evidence:                         # optional; measurements the
        reel_candidates: turn counts    # task holds, for the flag field

Refused with `CreativeTaskError`, by name: a missing or duplicate name, a
name shadowing a step, an incomplete role, a missing or empty handoff, no
outputs, evidence without `creative_direction` among the inputs, a floor
in the role, the handoff or an output description, and an unknown key
(`TASK_KEYS`, `ROLE_KEYS`).  `tasks_for_project` loads and checks them.

What a task is NOT: it is not wired into any DAG, it writes no state key,
and no contract maps its outputs to a reader.  `present_creative_task`
returns the model's answer to its caller, and the caller is the reader.
The recorded prompt lands in `llm_requests/task:<name>.json` like any
other call the backend answers.

`select_reels` is NOT migrated here; it remains step 3.04.  This module
is the mechanism; the migration is a separate change.

Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module. They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/creative_tasks.py`. [why - the captain's
2026-09-04 ruling on step 3.4](docs/RULE_EVIDENCE.md#project-declared-creative-tasks)
- **A project declares a named creative task carrying a role and a
  handoff; the pipeline invokes it instead of adding a step.**
  `pipeline.creative_tasks` in `project.yaml` declares them.
- **The invocation is the model-task service itself
  (`model_task.run_model_task`), not a second mechanism.** A task key
  (`task:<name>`) is routed through the same call steps go through, so the recorded prompt, the schema rendering,
  the backends, the QA loop and the collectors apply with nothing
  reimplemented.
- **The three guards reconcile against declared tasks rather than step
  ids.** The role is prepended by the shared renderer; the floors gate
  reads the task's prompt and refuses a floored declaration; the
  contradiction field is rendered from the task's own declared evidence.
- `tests/test_project_declared_creative_tasks.py`.

The ruling and the reasoning behind this mechanism:
docs/evidence/creative_tasks.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple


#: The namespace task keys live in. A step id can never contain it -
#: task names carrying it are refused - so a guard branching on the
#: prefix cannot misread a step as a task.
TASK_PREFIX = "task:"

#: Keys a task declaration may carry. Anything else is refused, because
#: a misspelled key silently changing what is invoked is the key-name
#: bug class this repository refuses everywhere.
TASK_KEYS = frozenset(
    {"name", "role", "handoff", "inputs", "outputs", "evidence"})

#: What a role must answer. The same completeness rule a step's role
#: answers to: a discipline, what it reads the measurements with, what
#: it decides and what it does not. A role with no boundary reads as
#: licence.
ROLE_KEYS = ("discipline", "addressed_as", "reads_with", "decides", "defers")


class CreativeTaskError(ValueError):
    """A task declaration the pipeline refuses, by name."""


@dataclass(frozen=True)
class CreativeTask:
    """One project-declared creative task, loaded and refused already."""

    name: str
    key: str
    role: object
    handoff_path: str
    handoff_text: str
    inputs: Tuple[str, ...]
    outputs: Tuple[Dict, ...]
    evidence: Dict[str, str]


def task_key(name: str) -> str:
    """The invocation key for a task name."""
    return f"{TASK_PREFIX}{name}"


def is_task_key(node_id: str) -> bool:
    """Whether `node_id` names a project-declared task rather than a step."""
    return isinstance(node_id, str) and node_id.startswith(TASK_PREFIX)


def task_name(node_id: str) -> str:
    """The task name out of a task key, refused when it is not one."""
    if not is_task_key(node_id):
        raise CreativeTaskError(
            f"{node_id!r} is not a project-declared task key: task keys "
            f"read `{TASK_PREFIX}<name>`.")
    return node_id[len(TASK_PREFIX):]


def _refuse(name: str, why: str) -> None:
    raise CreativeTaskError(f"creative task {name!r}: {why}")


def _check_name(entry: dict, seen: set) -> str:
    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CreativeTaskError(
            f"a pipeline.creative_tasks entry names no task: each entry "
            f"needs a non-empty string `name` (got {entry!r}).")
    if name in seen:
        _refuse(name, "is declared twice. A task name is invoked by name, "
                      "and two tasks under one name cannot both be it.")
    if ":" in name or "/" in name or "\\" in name:
        _refuse(name, "carries a path or namespace separator. Task keys "
                      f"read `{TASK_PREFIX}<name>`, so a name holding one "
                      f"cannot be addressed.")
    from library.tools.project_layout import STEP_BY_ID
    from library.tools.undetermined import DECLARING_STEPS
    if name in STEP_BY_ID or name in DECLARING_STEPS:
        _refuse(name, "shadows a pipeline step. A task is not a step, and "
                      "a declaration colliding with one would split every "
                      "guard that reconciles by key.")
    return name


def _check_role(name: str, entry: dict):
    from library.tools.craft_role import CraftRole

    raw = entry.get("role")
    if not isinstance(raw, dict):
        _refuse(name, "declares no role: a task that does not say who is "
                      "reading is the generic agent the roles exist to "
                      "remove.")
    for field in ROLE_KEYS:
        value = raw.get(field)
        if isinstance(value, str):
            value = value.strip()
            if value:
                continue
            _refuse(name, f"its role's `{field}` is empty. A role is a "
                          f"discipline, what it reads the measurements "
                          f"with, what it decides and what it does not; a "
                          f"role with no boundary reads as licence.")
        if isinstance(value, (list, tuple)) and any(
                isinstance(v, str) and v.strip() for v in value):
            continue
        _refuse(name, f"its role's `{field}` is missing or empty. A role "
                      f"is a discipline, what it reads the measurements "
                      f"with, what it decides and what it does not; a role "
                      f"with no boundary reads as licence.")
    corrects = raw.get("corrects", ())
    if corrects is None:
        corrects = ()
    if isinstance(corrects, str):
        corrects = (corrects,)
    return CraftRole(
        step_id=task_key(name),
        discipline=raw["discipline"].strip(),
        addressed_as=raw["addressed_as"].strip(),
        reads_with=tuple(v.strip() for v in raw["reads_with"]
                         if isinstance(v, str) and v.strip()),
        decides=tuple(v.strip() for v in raw["decides"]
                      if isinstance(v, str) and v.strip()),
        defers=tuple(v.strip() for v in raw["defers"]
                     if isinstance(v, str) and v.strip()),
        corrects=tuple(v.strip() for v in corrects
                       if isinstance(v, str) and v.strip()),
    )


def _check_handoff(name: str, entry: dict, project_root: Path) -> tuple:
    raw = entry.get("handoff")
    if not isinstance(raw, str) or not raw.strip():
        _refuse(name, "names no handoff: a task with no handoff is a role "
                      "with no job, and a model reached with one is the "
                      "generic agent again.")
    path = Path(raw.strip())
    if not path.is_absolute():
        path = project_root / path
    if not path.is_file():
        _refuse(name, f"its handoff {raw!r} cannot be read at {path}: a "
                      f"declaration pointing at nothing invokes nothing.")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        _refuse(name, f"its handoff at {path} is empty: a declaration "
                      f"pointing at nothing invokes nothing.")
    return str(path), text


def _check_inputs(name: str, entry: dict) -> Tuple[str, ...]:
    raw = entry.get("inputs", [])
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(
            not isinstance(v, str) or not v.strip() for v in raw):
        _refuse(name, "`inputs` must be a list of state keys the prompt "
                      "may read.")
    return tuple(v.strip() for v in raw)


def _check_outputs(name: str, entry: dict) -> Tuple[Dict, ...]:
    raw = entry.get("outputs", [])
    if not isinstance(raw, list) or not raw:
        _refuse(name, "declares no outputs: a task with nothing to ask "
                      "makes no call, and the invocation is the whole "
                      "forcing function.")
    checked = []
    for i, spec in enumerate(raw):
        if not isinstance(spec, dict) or not spec.get("name") or not (
                isinstance(spec.get("name"), str)):
            _refuse(name, f"output [{i}] names nothing: every output "
                          f"needs a non-empty string `name`.")
        if not spec.get("type") or not isinstance(spec.get("type"), str):
            _refuse(name, f"output {spec.get('name')!r} declares no `type`: "
                          f"the schema the model answers is rendered from "
                          f"these, and an untyped key is a guess.")
        kept = {"name": spec["name"], "type": spec["type"]}
        if spec.get("description"):
            kept["description"] = spec["description"]
        for optional in ("required", "may_be_empty"):
            if optional in spec:
                kept[optional] = spec[optional]
        checked.append(kept)
    return tuple(checked)


def _check_evidence(name: str, entry: dict, inputs: Tuple[str, ...]) -> Dict[str, str]:
    raw = entry.get("evidence", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict) or any(
            not isinstance(k, str) or not k.strip() or
            not isinstance(v, str) or not v.strip()
            for k, v in raw.items()):
        _refuse(name, "`evidence` must be a mapping of input name to what "
                      "it measures.")
    if raw and "creative_direction" not in inputs:
        _refuse(name, "declares evidence but does not take "
                      "`creative_direction`: evidence with nothing "
                      "inherited to hold it against is prose disagreeing "
                      "with prose.")
    return {k.strip(): v.strip() for k, v in raw.items()}


def _check_floors(name: str, role, handoff_text: str,
                  outputs: Tuple[Dict, ...]) -> None:
    from library.tools.craft_role import render_block
    from library.tools.creative_floors import CreativeFloor, assert_no_floors

    try:
        assert_no_floors(render_block(role), f"the role of task {name!r}")
        assert_no_floors(handoff_text, f"the handoff of task {name!r}")
        for spec in outputs:
            if spec.get("description"):
                assert_no_floors(spec["description"],
                                 f"the `description` of task {name!r} "
                                 f"output {spec['name']!r}")
    except CreativeFloor as exc:
        raise CreativeTaskError(
            f"creative task {name!r} carries a creative floor: {exc}"
        ) from exc


def tasks_for_project(project_folder) -> Dict[str, CreativeTask]:
    """Every creative task the project declares, loaded and refused already.

    Reads `pipeline.creative_tasks` off the project's own `project.yaml`.
    A project declaring none declares none: empty in, empty out, no
    error. Anything malformed raises `CreativeTaskError` naming the
    entry and the reason, because a declaration that cannot be invoked
    is a document again.
    """
    from library.schemas.project_config import load_project_config

    root = Path(str(project_folder)).expanduser()
    config_path = root / "project.yaml"
    if not config_path.is_file():
        return {}
    try:
        config = load_project_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        raise CreativeTaskError(
            f"cannot load creative tasks: {exc}") from exc
    raw = config.pipeline.creative_tasks or []

    tasks: Dict[str, CreativeTask] = {}
    seen: set = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise CreativeTaskError(
                f"pipeline.creative_tasks[{i}] must be a mapping, got "
                f"{type(entry).__name__}.")
        unknown = sorted(set(entry) - TASK_KEYS)
        label = entry.get("name") or f"[{i}]"
        if unknown:
            _refuse(label, f"declares {unknown}, which nothing reads. It "
                           f"takes {sorted(TASK_KEYS)}.")
        name = _check_name(entry, seen)
        seen.add(name)
        role = _check_role(name, entry)
        handoff_path, handoff_text = _check_handoff(name, entry, root)
        inputs = _check_inputs(name, entry)
        outputs = _check_outputs(name, entry)
        evidence = _check_evidence(name, entry, inputs)
        _check_floors(name, role, handoff_text, outputs)
        tasks[name] = CreativeTask(
            name=name,
            key=task_key(name),
            role=role,
            handoff_path=handoff_path,
            handoff_text=handoff_text,
            inputs=inputs,
            outputs=outputs,
            evidence=evidence,
        )
    return tasks


def _load_task(project_folder, node_id: str) -> CreativeTask:
    """The task behind a task key, refused when it is not declared."""
    name = task_name(node_id)
    tasks = tasks_for_project(project_folder or "")
    if name not in tasks:
        known = sorted(tasks) or "none"
        raise CreativeTaskError(
            f"{node_id}: the project declares no creative task {name!r} "
            f"(declares: {known}). Declare it under "
            f"`pipeline.creative_tasks` or invoke nothing.")
    return tasks[name]


def role_block(task: CreativeTask) -> str:
    """The task's role, rendered by the shared renderer.

    `craft_role.render_block` is the one shape for "who is reading this
    context", whether the reader was declared by the engine or by the
    project. A second renderer would let the two disagree about what a
    role is.
    """
    from library.tools.craft_role import render_block
    return render_block(task.role)


def build_prompt(task: CreativeTask) -> str:
    """The declaration-controlled prompt: role, handoff, and appenders.

    This is what the floors gate reads. The archived request carries the
    rendered schema and the brand constraints beside it - neither is the
    declaration's, and both are floor-free by construction (output
    descriptions are refused above when they are not).
    """
    from library.tools import direction_contradiction, undetermined

    prompt = role_block(task) + task.handoff_text
    prompt += undetermined.prompt_block()
    if task.evidence:
        prompt += direction_contradiction.prompt_block_for(task.evidence)
    return prompt


def model_task(task: CreativeTask):
    """The task's declaration read as the service's typed question.

    No step-shaped manifest: `model_task.ModelTask` carries what the
    model writes (`llm_outputs`, which is also what the answer is
    validated against), which inputs the prompt may read
    (`context_fields`), and the three guards' task halves - the role
    prepended by the shared renderer, the declared evidence, and the
    interview a task that reads `creative_brief` is owed when none is
    attached.
    """
    from library.tools import model_task as service

    outputs = []
    for spec in task.outputs:
        rendered = {"name": spec["name"], "type": spec["type"]}
        if spec.get("description"):
            rendered["description"] = spec["description"]
        for optional in ("required", "may_be_empty"):
            if optional in spec:
                rendered[optional] = spec[optional]
        outputs.append(rendered)
    reads_brief = "creative_brief" in task.inputs
    return service.ModelTask(
        key=task.key,
        handoff_path=str(task.handoff_path),
        outputs=tuple(outputs),
        llm_outputs=tuple(outputs),
        context_fields=tuple(task.inputs),
        reads_brief=reads_brief,
        role=role_block(task),
        evidence=dict(task.evidence),
        interviews_without_brief=reads_brief,
    )


def present_creative_task(project_folder, name: str, context: dict,
                          full_auto: str = None, llm_timeout: int = 300,
                          bridge_supplied=None,
                          retry_feedback: str = "") -> dict:
    """Invoke a project-declared creative task and return its answer.

    The invocation IS the model-task service steps go through
    (`model_task.run_model_task`) - the same recorded prompt, the same
    schema rendering, the same backends, the same QA loop and the same
    collectors - so a task reaches a model under exactly the forcing
    function a step does, with no runner in between. `context` carries
    the state the task reads; only the keys its declaration names reach
    the prompt. The answer is returned to the caller, which owns it: a
    task is not DAG-wired, so it writes no state key and no contract
    maps its outputs onward.
    """
    from library.tools import model_task as service

    task = _load_task(project_folder, task_key(name))
    inputs = {"project_folder": str(project_folder)}
    for key in task.inputs:
        if context and key in context:
            inputs[key] = context[key]

    return service.run_model_task(
        model_task(task),
        inputs,
        full_auto=full_auto,
        llm_timeout=llm_timeout,
        bridge_supplied=set(bridge_supplied or ()),
        retry_feedback=retry_feedback,
    )


# ── The three guards' task halves ─────────────────────────────────────
#
# The service reads these off `model_task(task)`; the replay bench
# (`library/tools/replay_bench/reconstruct.py`) reads them by key, which
# is why they take a node id. Each returns the step-path value for a step
# id without touching disk:
# a step invocation must never pay for - or fail on - a project file it
# does not need. Only a `task:` key loads the declaration, and a task
# key that loads nothing raises rather than running role-less.


def prepend_task_role(project_folder, node_id: str, prompt: str) -> str:
    """Prepend the declaring project's role, or return `prompt` unchanged.

    Read beside `craft_role.prompt_block`, which answers for steps;
    this answers for tasks, through the same renderer. A task key whose
    declaration cannot load raises: a model reached with no role is the
    generic agent the roles exist to remove.
    """
    if not is_task_key(node_id):
        return prompt
    if not project_folder:
        raise CreativeTaskError(
            f"{node_id}: no project folder, so the declaring project's "
            f"role cannot load. A task invocation without its role is "
            f"refused rather than run generic.")
    return role_block(_load_task(project_folder, node_id)) + prompt


def task_evidence(project_folder, node_id: str) -> Dict[str, str]:
    """The measurements a task holds against the direction, or {}.

    Empty for every step id, and for a task that declares none - a task
    with no evidence gets no flag field, because a claim with no
    measurement behind it is an opinion, not a contradiction.
    """
    if not is_task_key(node_id):
        return {}
    if not project_folder:
        raise CreativeTaskError(
            f"{node_id}: no project folder, so the declaring project's "
            f"evidence cannot load.")
    return dict(_load_task(project_folder, node_id).evidence)


def asks_interview(project_folder, node_id: str,
                   brief_attached: bool) -> bool:
    """Whether the task is interviewed on this run.

    The same rule `briefing_interview` applies to steps, read off the
    task's own declaration instead of a manifest: a task handed the
    captain's brief and then asked what it wished the captain had said
    is being invited to manufacture a gap, and a task that never asked
    for the brief is not asked about one.
    """
    if brief_attached or not is_task_key(node_id) or not project_folder:
        return False
    return "creative_brief" in _load_task(project_folder, node_id).inputs
