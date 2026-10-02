# `library.tools.input_contract` - the history behind its contract

This is the module docstring of `library/tools/input_contract.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
One question, asked of every declared input: WHO REFUSES when it is absent.

The defect this exists to catch
-------------------------------
`compile_manifest` declared `transition_spec`, `enhancement_spec`,
`sfx_spec` and `color_grade_spec` REQUIRED while its own code called
three of them "optional enhancement specs" and read them with defaults.
One of the two was lying, and because `required` is what
`run_pipeline.gather_step_inputs` raises on and what
`library/tools/run_scope.py` derives a refusal from, the stricter of the
two won: a rough cut could not skip four planners it did not need, at a
measured cost of 446.5s on 001 (#260).

That is one instance of a class, so this module reads the whole class.

The two declarations, and the one question
------------------------------------------
A step declares `interface.inputs[].required`.  Its code does something
when the value is absent.  The question is not "does the code use
`.get()`" - almost every step in this pipeline does, and a `.get()` whose
default is unreachable is harmless.  The question is **who refuses**:

* `runner` - the input arrives on a DAG edge and is declared required, so
  `gather_step_inputs` raises before the step's code runs at all.  The
  step's default is dead code on that route.  The declaration is the
  enforcement.
* `step` - the step's own code refuses, and the evidence is a file and a
  line: a direct subscript, a `require_keys` call, or a guard that
  raises or exits.  This is the only enforcement a NON-edge input can
  have, because nothing raises on the way in.
* `nobody` - declared required and neither of the above.  A requirement
  nothing enforces.

And the mirror, for a declared-OPTIONAL input: the step's code must NOT
refuse, or a run that legitimately omits it dies inside the step.

Warrant is a different question from enforcement
------------------------------------------------
That a requirement is ENFORCED does not make it TRUE.  `compile_manifest`
was enforced perfectly and still wrong.  Establishing the truth of a
requirement means running the step without the input and looking at what
comes out, which this module does not do - `tests/test_compile_manifest_
without_the_decoration.py` does it for the step the captain's case runs
through.  What this module adds is the third fact that makes an
unwarranted requirement visible: whether anything CONSUMES the input at
all.  An input that reaches neither the step's code nor its prompt is a
requirement with no consumer.

Reaching the prompt is read off `context_fields`, the same allow-list
`run_pipeline.project_step_context` applies (AGENTS.md section 10.1): a
step declaring none is handed every byte it was routed, so every input
reaches its prompt.

The step that has no prompt at all
----------------------------------
That reading had one silent hole, and the very next task fell in it.
A step with no `handoff.md` declares no `context_fields` because there
is no prompt to project, and reading that absence as "handed every
byte" made `prompt_reads` True for every input of all fifteen
prompt-less steps in the DAG.  So `render_motion_graphics` declared
`creative_direction` and `enhancement_spec` REQUIRED, the DAG routed
both, `generate_motion_props` read neither, and the survey called them
consumed (#330).

A guard with a silent hole is worse than a known gap, because the
fields inside it read as verified.  Two things close this one:

* `prompt_reads` is False for a step with no prompt, asked of
  `run_pipeline.get_step_implementation` rather than of a list kept
  here;
* and because that step's CODE is then the only consumer it can have,
  "the code names the key" is no longer enough.  `trace_step_values`
  asks whether the value the key yields REACHES A USE - the two motion-
  graphics inputs are named in `step.py` and handed to a function that
  never mentions either parameter.

The findings are REPORTED, not failed.  Every one of them predates the
change that made the class visible, and escalating a pre-existing
finding to a build failure is the captain's decision;
`unread_by_a_prompt_less_step` is the report and `disagreements` is
unchanged.

What this still cannot see is printed by the survey itself rather than
left implicit: a step WITH a prompt is judged on whether its code NAMES
the key, because the prompt consumes it either way and the dataflow
question decides nothing there; and the value read is one-sided by
design - `_UNTRACEABLE` is the list of what it reads as used rather
than guessing about.

    python3 -m library.tools.input_contract          # the survey
    python3 -m library.tools.input_contract --bad    # disagreements only

`tests/test_input_declarations_are_true.py`.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

**No step may declare an input required that nothing refuses on, or optional that its own code refuses without.**
`library/tools/input_contract.py` surveys all 144 declared inputs of the DAG's 26 steps and says, for each, WHO refuses when it is absent - the runner (edge-routed and required), the step (with a file and a line), or nobody.
- **Enforcement is not warrant.** Establishing warrant means RUNNING the step without the input; `tests/test_compile_manifest_without_the_decoration.py` does that for every input of the one step that reads state directly instead of taking `gather_step_inputs`' word for it.
- A required input the step nonetheless runs without is recorded in `REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT` with what would go silently missing - and the test checks the record BOTH ways, so an entry for an input that really refuses is stale and fails.
- The line is AGENTS.md section 10.5's: `[]` for transitions is the absence of decoration and is optional; `{}` for the audio mix is the spine's declared `music_behavior` going missing and is not.
- `UNCONSUMED_DECLARATIONS` records an input read by neither the step's code nor its prompt, still declared because unrouting it would leave a `handoff.md` documenting a read that no longer happens. `UNROUTED_THOUGH_THE_HANDOFF_DOCUMENTS_IT` is its MIRROR - the declaration has gone and the handoff line has stayed. It is EMPTY: its one entry, `creative_direction.prosody_analysis`, closed on 2026-09-01 when the captain re-wired step 1.05 and the handoff line agreed again. It fails from BOTH sides: an entry whose input is declared again is stale, and so is one whose handoff no longer names the key.
- **A step with no `handoff.md` reaches no prompt, and its CODE is the only consumer it can have.** `step_has_a_prompt` asks `run_pipeline.get_step_implementation`, and `trace_step_values` then asks whether the value the key yields REACHES A USE - naming the key is not reading it.
- **That half REPORTS; it does not fail**, because escalating a pre-existing finding is the captain's call. `unread_by_a_prompt_less_step` is the report; `disagreements` is unchanged.
- **The value read is one-sided and says so.** `_UNTRACEABLE` is what it reads as USED rather than guessing about - anything but a plain function the step's own files define, an alias, a second hop.
- **Deterministic**: a `step.py`, run automatically over JSON stdin/stdout.
- **Hybrid**: a `bridge.py` that pre-computes context plus a `handoff.md` prompt for an LLM.
- **LLM-only**: only a `handoff.md`, generating the output from upstream context.
```
