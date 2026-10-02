# `library.tools.creative_tasks` - the history behind its contract

This is the module docstring of `library/tools/creative_tasks.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
A project declares a creative task the pipeline invokes, instead of adding a step.

Raised by the captain 2026-09-04 on seeing select_reels land as step 3.4:
*"wait did you make it a permanent pipeline step?? if so why?"* Reel
selection is project-shaped - a marketing render or a single-video edit
has no reels, and the engine would carry a step most projects never run.
Firstmate's translation ("make it a step") fixed the real complaint - the
judgement circumvented the pipeline, no model was ever reached - but
conflated two claims: "the LLM must do this with a real brief" and "this
must be a permanent step".

What the assessment established: `project_config` already carries
`creative_brief`, and that surface gives the same class of guarantee - a
project-declared document that provably reaches a prompt. What it does
NOT carry is a craft role or a task: the brief is context handed to a
step that already exists, and it never causes a model to be INVOKED.

THE FORCING FUNCTION IS THE WHOLE POINT. Step-hood is what forces an
actual invocation with a recorded prompt, and what makes the three
guards reach it - craft_role's role plumbing, the floors gate's derived
roster, direction_contradiction's coverage. A brief that nothing invokes
is a document, and a document a worker reads and then acts on in-turn is
EXACTLY the failure diagnosed in findings section 15, with better
paperwork. So a declared task is invoked through the SAME forcing
function steps go through - the model-task service itself
(`library/tools/model_task.py`), not a second mechanism - and the three
guards reconcile against the declaration rather than a step id:

* the task's role is PREPENDED to its handoff by the shared renderer
  (`craft_role.render_block` - one shape for who is reading, whether the
  reader was declared by the engine or by the project);
* the floors gate reads the task's prompt, and a task whose role,
  handoff or output descriptions demand a count is REFUSED at
  declaration time against the same enumeration steps are read for
  (`library/tools/creative_floors.py`);
* a task that takes the direction with declared evidence gets the
  contradiction field rendered from its own evidence
  (`direction_contradiction.prompt_block_for` - the OFF_DAG_MEASUREMENTS
  shape, which `select_reels` and `music_selection` already take for
  measurements no edge carries); every invoked task gets the
  undetermined field, because every model-reaching invocation declares
  rather than forming a subset;
* a task that declares `creative_brief` among its inputs is interviewed
  when no brief is attached, rather than planning in silence
  (`asks_interview` - the same rule `briefing_interview` applies to
  steps, read off the task's own declaration).

A task key lives in its own namespace, `task:<name>`. A name shadowing a
step id, carrying the separator, or naming a path is refused: a task is
not a step, and a declaration that collides with one would split every
guard that reconciles by key.

What a task is, in `pipeline.creative_tasks` - a list of mappings:

    - name: reel_pick                  # unique, not a step id, no ':'
      role:                             # THREE things and no fourth, the
        discipline: short-form editor   # same completeness rule a step's
        addressed_as: You are ...       # role answers to
        reads_with: [...]               # what it reads measurements with
        decides: [...]                  # what is its to decide
        defers: [...]                   # what is not, and who owns it
      handoff: tasks/reel_pick.md       # project-relative, must exist
      inputs: [timeline_transcript]     # state keys the prompt may read
      outputs:                          # what the model is asked to write;
        - name: reel_selection          # non-empty, or there is nothing
          type: object                  # to ask and no call is made
          description: The chosen stretches.
      evidence:                         # optional; names measurements the
        reel_candidates: turn counts    # task holds, for the flag field

Refused, by name: a missing or duplicate name, a name shadowing a step,
an incomplete role, a missing or empty handoff, no outputs, evidence
without `creative_direction` among the inputs (evidence with nothing
inherited to hold against is prose disagreeing with prose), a floor in
the role, the handoff or an output description, and an unknown key (a
misspelled key silently changing what is invoked is the key-name bug
class this repository refuses everywhere).

What a task is NOT: it is not wired into any DAG, it writes no state
key, and no contract maps its outputs to a reader. `present_creative_task`
returns the model's answer to its caller, and the caller is the reader -
the task is invoked, its answer is owned where it was asked for. The
recorded prompt lands in `llm_requests/task:<name>.json` like any other
call the backend answers, so an audit reads exactly what the model read.

`select_reels` is NOT migrated here. The record makes the migration
conditional on the mechanism ("if that holds, select_reels is the first
thing migrated") and defers it past the live field test ("design it
deliberately rather than folding it into a live test"). This module is
the mechanism; the migration is a separate change once the test lands.

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
- `tests/unit/context/test_project_intake.py`.
```
