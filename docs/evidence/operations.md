# `library.tools.operations` - the history behind its contract

This is the module docstring of `library/tools/operations.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The named things an operator can ask the pipeline to do, and nothing else.

Ruling 1, which this module exists to obey
------------------------------------------
**An operation is a named, scoped entry point into an existing step's own
code.  It owns no logic.**

`Operation.run` POINTS AT the step's own function.  It never wraps it,
never reimplements it, and never adds a rule the step does not already
have.  `tests/test_operations_add_no_second_implementation.py` enforces
that by resolving every `run` back to the file it is defined in and
refusing anything that is not the owning step's own body, or a
`library/tools/` module that step already imports.

The alternative - a verb layer over `library/tools/` - loses for exactly
this reason: it violates Ruling 1 the moment a verb does a step's work,
and the 38 tools that already have a `__main__` block prove that
reachable is not the same as addressable.

Why `owning_node` is not decoration
-----------------------------------
The runner's per-step loop wraps a step body in eighteen services, and
sixteen of them are keyed by the DAG node id: the two ledgers, the run
status, the review gate, the marker routing, the step export, the three
run-level collectors.  An operation that did not declare its owning node
would lose its place in all of them - it would run, and nothing would
record that it had.  So an operation names the node whose decision it
is, and `library/tools/capabilities.problems` checks the name is real.

> **Superseded 2026-10-02.** The field is gone.  The node is still needed
> for the same sixteen services, but it is DERIVED: a capability names its
> executor's step directory (`owning_dir`), and its node is the one whose
> `step_ref` is that directory (`dag_adapter.node_of`).  Measured before
> the removal, every registered `owning_node` equalled that derivation,
> so the field said nothing the directory did not.

It is LEGACY metadata, not identity.  An operation's `name` is its
capability id (`library/tools/capabilities.py`), and every node-keyed
read below goes through `library/tools/dag_adapter.py` - the one place
that still translates a capability into the node those services key by,
until nothing reads the node and the graph can go.

The two services that are NOT node-keyed are the handbrake, which is
keyed by project, and the provenance snapshot, which takes no key at all
because it attributes by diffing the tree.  Provenance is therefore the
one service that already generalises to an operation, which is why the
design threads it first.

What an operation RETURNS, and why it is a type
----------------------------------------------
`OperationResult` is defined here, once, because four of the six hook
conditions in increment 6 are operation-derived - `operation_completed`,
`operation_refused`, `requirement_unsatisfied`, `output_empty` - and not
one of them can be built from a bare dict.  A hook handed a dict cannot
tell a refusal from a completion, cannot name WHICH requirement refused
or who produces it, and cannot tell produced-nothing from
produced-something.  That last one is the empty-side-passes shape the
design exists to stop, so a result type that cannot express it would
reintroduce it at the hook layer.

Defining it anywhere else means increment 6 inventing its own - a second
implementation, which Ruling 1 forbids - so it is here, and it is one
type for every operation.

**A refusal names a producer.**  `unsatisfied` carries the Requirement
objects themselves, not strings, so `refusal_reason()` can say what is
missing AND which step makes it.  A refusal that only says "missing" is
the prose prerequisite in a new costume.

What an operation REQUIRES, and why it is derived
-------------------------------------------------
`Operation.requires` is DERIVED, never hand-written: it is every
requirement in `library/tools/requirements.py` whose `consumers` include
this operation's `owning_node`.  Increment 3 already derives those from
`inputs[].required` and the DAG's `data_mapping`, so an operation
inherits exactly the prerequisites its step has - no more, no less, and
nothing to keep in sync.

Hand-writing them here would put a second requirement vocabulary beside
the one that exists, and the hand-written half would be prose in a
costume.  That is the defect this refactor removes, so `requires` is a
property with no setter and `_REGISTRY` carries no requirement literals.

What an operation EFFECTS, and why two are empty
-------------------------------------------------
`Operation.effect` is the exact mirror of `requires`: every requirement
in `library/tools/requirements.py` whose `produced_by` includes this
operation's `owning_node`.  Same vocabulary, same derivation discipline -
an effect in a different language could never be matched against a
precondition, so nothing composes.

Forty-one of the 45 operations have a non-empty effect.  Four are
empty, and the emptiness is TRUE, not a gap:
`run_scope.prerequisites` derives one condition per REQUIRED input, so
a node that no consumer requires anything from produces no requirement.
The four are one kind, verified against the tree (see
`EMPTY_EFFECT_REASONS` for the per-operation evidence):

* ARTIFACT - the product lands on disk for a caller, a gate or Resolve
  placement rather than travelling a DAG edge:
  `motion_graphics.render`, `motion_graphics.render_segment`,
  `reel.reading_context`, `reel.judge` (the judgement is read off
  disk).  (`reel.gate_stills`, the fifth artifact, shares `verify_reels`'
  verdict effect by the node granularity `Operation.effect`
  declares - a stills grab planned as a verdict would be the
  confidently-wrong result this layer exists to stop, so the route
  selector names `reel.verify` and no plan names the grab.)

The two kinds the tree was checked against - VERDICT, the effect that
is pass/fail rather than a state key, and ANALYSIS, the production no
REQUIRED edge carries - are both GONE from the blind set, by the
captain's rulings 2026-09-23: a verdict is expressible as a goal as
its own requirement kind (`requirements.VERDICTS`), and an edge that
may or may not carry state is expressible as a goal as its own
requirement kind (`requirements.OPTIONALS`), so
`sfx_library.validate`, `reel.verify`, `validation.resolve`,
`prosody.analyse`, `color_grade.resolve`, `ocr.extract`,
`transitions.resolve`, `sfx.resolve` and `vfx.resolve` each derive a
non-empty effect from those pools with nothing listed here.
The composition consequence below still holds for the four that remain:
a composer working backwards from requirements alone can never select
them, because artifact productions are invisible as goals.
Hand-writing four effects in a second vocabulary to make the field look
complete would be the defect this module removes, in a new costume.

This follows the layer's own enforced doctrine, not just this
module's taste: `Requirement.__post_init__`
(`library/tools/requirements.py`) refuses a predicate or coverage
requirement with an empty `produced_by` and refuses an environment
requirement with a non-empty one, on the grounds that `produced_by`
models exactly one thing - a prior step wrote this into state - and
excludes both machine facts and a step's own subject matter.  A
verdict (a gate's exit code, a raise) and an artifact handed to a
caller or a gate are both the excluded half, so expressing them as
`produced_by` entries would extend what the field means.  An
OPTIONAL edge is neither excluded half: the producer wrote a state
key some consumer takes, so naming the producer says exactly what
the field means - which is why `requirements.OPTIONALS` derives
through the same filter with zero changed lines.  What stays
unmodelled is production nothing takes through any edge at all when
no goal names it either - and after this change there is none left
in the registry: even `ocr.extract`, with no edge and no reader,
derives its effect from the goal that names its production.

Executing
---------
`Operation.execute` gathers the step's inputs the way the RUNNER does -
`gather_step_inputs`, not a copy of it - checks the requirements, and
either runs the step's own function or REFUSES naming what is missing
and which step produces it.  The refusal is the point as much as the
success: an operation that cannot say why it will not run is a resolver,
not an executor.

Reachability
------------
    python3 -m library.tools.operations --list
    python3 -m library.tools.operations subtitles.render --project <p> --region 45.0-72.0
    python3 -m library.tools.operations subtitles.splice --project <p>         --region 45.0-72.0 --set stored_plan=@subtitle_plan.json
    python3 -m library.tools.operations --emit-skill

Same registry, same scope vocabulary, same refusal, whether it is called
from a shell, from Python, or by the runner.

Running one at a REGION is the captain's third entry point - "a user
asking for certain specific sections of the video to be re-edited" - and
`docs/ENTRY_POINTS_MEASURED.md` measures which operations reach it,
which do not, and what the ones that do not would need.
```
