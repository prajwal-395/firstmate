# `library.tools.run_scope` - the history behind its contract

This is the module docstring of `library/tools/run_scope.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
One enumeration for scoping a run: what to run, what to leave out, and
the refusal when the two do not fit together.

The problem this exists to remove
---------------------------------
`--step` runs exactly one step and `--from` runs a suffix.  Neither can
express "run these and nothing else", neither can express "run everything
except this", and neither consults the DAG about the selection itself.
So a selection that dropped a producer failed forty minutes in, inside
`gather_step_inputs`, rather than in the second before the run began.

The captain's ask (2026-08-28, #250): "we should have that ability to be
able to quickly deselect and select what specific steps we want to fire
off for a run - like for example if i just need the roughcut with
subtitles and nothing else then many intermediate steps can just be
skipped instead of having unecessary processing wasted."

What a selection is
-------------------
Three inputs, and they compose:

* **target** - a NAME for a destination, resolved here.  A target names
  its GOAL steps and nothing else; the step list is the goals' dependency
  closure, walked off the DAG on every run.  A target that carried a
  hand-written step list would be wrong the first time a step was
  inserted, and a target nobody trusts is a target nobody uses.
* **only** - goal steps given directly, for the case no target covers.
* **skip** - steps this run declines, whatever else selected them.

`with_steps` re-selects a step that is off by default (below).

How a selection resolves
------------------------
1.  Goals are the target's goals, or `only`, or - when neither is given -
    the whole selectable universe.
2.  The run set is the goals' closure over HARD edges.  Soft edges are
    not followed, so a step whose consumers can all live without it is
    not dragged in by a target that does not want it.
3.  Anything in `skip` comes out of the run set.
4.  Every remaining step's hard parents must be IN the run set or already
    SATISFIED - recorded in the project ledger with an output on file.
    Anything else is a refusal, named before the run starts.

A hard edge and a soft edge
---------------------------
The DAG's `data_mapping` says which of a producer's output keys reach a
consumer.  The consumer's manifest says which of its inputs are optional.
An edge is HARD when it carries at least one key the consumer does NOT
declare optional - that is exactly the condition under which
`run_pipeline.gather_step_inputs` raises.  Nothing here guesses: the
refusal and the crash it prevents read the same two declarations.

Excluding a producer refuses its consumers; it does not silently drop them
--------------------------------------------------------------------------
#250 left this open.  Deciding it from what actually happens: excluding a
producer whose output nothing has on file makes `gather_step_inputs`
raise mid-run.  There is no "let downstream cope" - the consumer has no
code path for an absent required input, by design (AGENTS.md section
10.1: never `.get()` a default for a key a contract promises).  So a
selection that strands a consumer is REFUSED, and the refusal says the
two ways out: skip the consumer too, or run the producer once so its
output is on file.

"I just want the rough cut" is expressed by naming a GOAL, not by
excluding twelve steps - which is what targets are for.

A prerequisite is a condition on STATE, not on lineage
------------------------------------------------------
The captain, #260, 2026-08-28: "we need to refactor the system to have
the pipeline be customizable with prereqs ... so we can continue to have
strong contract enforcment but still have the pipeline configuration
ability".

So a step declares what must EXIST for it to run - a `Prerequisite`,
one per required key - and this asks whether that state exists, by any
of three means: a step in this run makes it, a previous run recorded it,
or the captain supplied it from outside and it CHECKED OUT
(`library/tools/external_inputs.py`).  Which step would normally have
made it is one of the three answers, not the question.

That is also why a producer that finished and recorded a DIFFERENT key
does not satisfy: `gather_step_inputs` raises on the key.

A cached artifact satisfies an excluded dependency
--------------------------------------------------
That is what makes a scoped re-run fast, and it is the ledger's job
already: a step recorded complete WITH an output under `step_outputs` is
satisfied, and `gather_step_inputs` will find that output whether or not
this run re-computes it.  Both halves are required - a ledger entry with
no output is not an artifact.  Staleness is the ledger's business
(source fingerprints, `--rerun`), not this module's.

What the captain already has, and does not want made again
----------------------------------------------------------
A value under `<project>/external/` is a REQUEST, not a record: the
captain who cut the rough on the timeline by hand, or chose the music
track themselves, put it there so the pipeline would not do it again.
So a step every one of whose routed outputs has been SUPPLIED does not
run, on any run shape - `supplied_producers` names them and `resolve`
takes them out of the universe the way `DESELECTED_BY_DEFAULT` is taken
out.

Two properties this has to have, and both were measured absent before it
was written.  MEASURED on the DAG as it stands, supplying `audio_spine`,
`speech_sequence` and `a_roll_assignments` and resolving a plain full
run: 26 steps selected, `from_external` empty - `mesh_spine` and
`assign_aroll` both ran and their output shadowed what was supplied,
because `gather_step_inputs` reads `step_outputs` before it reads
`external`.  A hand-made cut was silently rebuilt, which is the exact
thing the rule below says does not happen.

* ALL of a producer's routed keys, never some of them.  `mesh_spine`
  hands out `audio_spine` AND `timed_spine`; leaving it out with only
  the first supplied would drop the second in silence.  A producer with
  an edge carrying NO `data_mapping` merges its whole output and so
  names no keys at all - it can never be fully supplied, and that is
  correctly conservative rather than a gap.
* NAMING the step outranks nothing here.  `--only`, `--with` and
  `--step` re-select a step that is off by default, because a default is
  weaker than a request - but a supplied value is ALSO a request, and
  the two contradict.  Running the step would overwrite what the captain
  handed in, so the pair is REFUSED by name, like `--skip X --only X`.

The three entry points this serves, and what each still needs, are
measured in `docs/ENTRY_POINTS_MEASURED.md`.

Steps that are off by default
-----------------------------
`DESELECTED_BY_DEFAULT` is the one place a step is declared wired but not
run.  It is not a second unwired list: the step IS in the DAG, it runs
whenever it is named, and turning it on is `--with <id>`.  A step may
only be here if nothing hard-depends on it, or every default run would
refuse; `tests/test_run_scope.py` checks that.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration, `library/tools/run_scope.py`, and both CLIs register its flags from it.
- **A selection is resolved against the DAG before the run starts, or refused.** A selection that strands a consumer names the consumer, the producer and the missing output keys.
- **A prerequisite is a condition on STATE, not on lineage.** `run_scope.Prerequisite` is one required KEY, and the resolver asks whether that key exists by any of three means: a step in this run makes it, a previous run recorded it, or it was supplied from outside and CHECKED.
- **An edge is HARD when it carries a key the consumer does not declare optional** - the same condition `gather_step_inputs` raises on. Soft parents are not pulled in by a target.
- **Excluding a producer REFUSES its consumers; it never drops them silently.** There is no "let downstream cope": a required input has no absent-value code path (section 10.1). Say "I just want the rough cut" by naming a GOAL, not by excluding twelve steps.
- **A recorded output satisfies an excluded dependency** - ledger entry, a `step_outputs` value, AND the KEY inside it.   A `--rerun` target is about to be discarded, so it satisfies nothing.
- **A recorded output does not remove a step from the run; a SUPPLIED one does.** History is not a request. The captain putting a value under `external/` is saying "do not make this", so the closure stops at that producer.
- **A target names its GOAL steps and nothing else.** The step list is walked off the DAG every run, so inserting a step upstream keeps the target right without anybody editing it. `rough_cut_subtitles` is the one target; add another only on evidence.
- **A step that is off by default is reported on every run**, including a plain full one, and is not counted as never-completed - a step that exists and silently never runs is the trap this file's step-directory check exists to stop.
- `tests/test_run_scope.py`.
```
