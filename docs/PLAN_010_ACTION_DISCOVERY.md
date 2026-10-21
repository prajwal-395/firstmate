# PLAN-010: making the available actions discoverable

The punch list asks, in five lines:

> Let the agent query the currently supported operations and their
> constraints. Present the smallest relevant schema and examples. Show
> known preservation limits and expected verification. Return actionable
> refusal categories and recovery options. Do not require the model to
> memorize the full 84-operation inventory.

This is what landed, and what it measures.

## What exists, and what was missing

The registry was never the problem. `edit_algebra.OP_STATUS` holds every
operation with its semantics, backends, qualification and preservation
limits; `operation_map` publishes the canonical record;
`capability_coverage` (BASE-002) answers "may this run here"; and
`resolve-axi capabilities` answers "what does this operation mean" and
"search the registry by name".

Two things were missing.

**The query an agent actually makes.** The only searchable surface
matched NAMES: `capabilities --search` scores the operation id, its
domain and its note. An agent asking in the language of the EDIT - "hold
the name card until she finishes", "take the soft focus card out" - got a
ranking over technical vocabulary, with nothing that told it which of the
ten rows an ordinary routing may use.

**A whole-operation answer where a decision-sized answer was needed.** The
answers that existed were the 141 KB of generated reference tables, the
operation map and the coverage report: everything about an operation, or
nothing. Nothing answered, in one place, "what does this action
PRESERVE, how is it VERIFIED, and what does it refuse with".

## What landed

One module, `library/tools/action_discovery.py`, and one verb:

```sh
bin/resolve-axi actions "move the name bar down"   # the actions for an effect
bin/resolve-axi actions                             # the whole supported menu
```

The module declares NO operation, NO backend, NO qualification and NO
route. Every fact in an answer is read from the registries: the operation
map for semantics and the route, the coverage report for qualification
and the editorial request counts, the refusal taxonomy for the six
blockers and their next actions, the recovery instruction for the
failure-category vocabulary, and the feasibility gate's own checks for
which check raises which blocker.

The answer carries the five things the punch list names:

| what | where in the answer |
|---|---|
| the smallest relevant schema, and an example | `card.schema`, `card.example` |
| the known preservation limits | `card.preserves` (the safety class, the recorded limits, the native state the operation does not preserve) |
| the expected verification | `card.verifies` (the verification contract, and the visual and audio checks the evidence recorded) |
| actionable refusal categories and recovery options | `card.refusals` (the taxonomy's six blockers, each with its next action and the checks that raise it), `recovery_options` (one per execution failure category) |
| the inventory nobody has to memorize | the menu holds the SUPPORTED operations - 14 of 84 - and `actions` with no argument prints them all |

**The supported menu is the point.** An operation ordinary routing may
not use is not a choice. The operations that are declared but not
routable are reported BESIDE the menu, each with the blocker that keeps
it out (`unsupported_operation`), the next action, and the supported
operations in its own domain - so an agent that wanted `clip.trim` learns
that `clip.trim` cannot be routed and that `clip.remove` can.

**The effect vocabulary is derived, not written.** It is the registry's
own text: the operation id, its domain, its note, its parameter names,
and the AXI CLI's own help text for the commands that perform it ("remove
an item and close the gap on its track"). A name that matches the effect
is strong evidence; prose that mentions the effect orders the candidates
and never makes them members.

## The measurement

The round-2 planning briefs are the BASE-003 corpus: thirteen briefs
written against real footage, with the answer key sealed outside the
engine. The tool is `evaluation/tools/plan010_actions.py` (it lives
outside `library/`, `ren/` and `native/`, because the sealed key must not
reach a path the engine opens).

A planner action choice is measured on the choices the ENGINE OFFERS a
planner for each brief, because that is the thing a discoverability
change moves. The BEFORE surface is the shipped one
(`capabilities --search "<the brief's instruction>"`, ten rows, ranked
over technical vocabulary). The AFTER surface is the new one.

### The named measurement: choices that name an operation that EXISTS and is QUALIFIED

| view | before | after |
|---|---|---|
| first choice per brief | **4/13** | **13/13** |
| the menu offered per brief | **39/130** | **65/65** |
| the key's own declared choices | **16/36** | unchanged (the key is sealed) |

The third row is the underlying gap, measured and not moved by this
change: the corpus's answer key names 36 planner action choices across
the briefs, every one of them exists, and only 16 of them are
production-enabled. Twenty of them would be refused at the far end of
the write path. That is the population the new surface protects a
planner from.

### The guard: is the offered choice the RIGHT one?

A menu of qualified operations is worth nothing if it offers the wrong
one, so the choice is also scored against the key's allowed choices, over
the eight briefs that expect an edit:

| view | before | after |
|---|---|---|
| first choice is among the brief's allowed choices | **1/8** | **3/8** |
| the menu holds an allowed, qualified choice | - | **5/8** |
| everything the surface offers holds an allowed, qualified choice | - | **6/8** |

The last row counts the alternatives the surface names beside an
operation it refuses, because a planner reading the card is offered
those too.

**Two briefs do not reach a qualified choice, and they cannot.** The
briefs `take-out-the-soft-focus-card-i-asked-about` and
`take-out-the-pause-where-she-stops` name operations that are all
unroutable (`overlay.remove`, `clip.trim`, `clip.ripple`, `clip.join`),
so no discoverability change can offer a qualified choice for them. The
surface says so instead of pretending.

### What the measurement does NOT say

- It is not a model run. No planner model was asked to plan these briefs
  before and after; the measurement is of what the engine OFFERS, read
  off the two surfaces with the same query. A model run would measure the
  model as much as the surface.
- The relevance guard is measured against a sealed key that was written
  against the same footage, and a brief whose premise the footage does
  not carry is still counted as a brief.
- The five negative briefs name no operation, so they are scored on
  existence and qualification only. A surface that offered an operation
  for `make this reel punchier` would be wrong, and the measurement
  cannot say so.

## Reproducing it

```sh
bin/vep evaluation/tools/plan010_actions.py
```

## What was left undone

- No model was run against the briefs, so the number is a property of
  the surfaces and not of a planner's reading of them.
- The lexical effect matching is weak on briefs whose effect is phrased
  editorially ("cut the first of the two and keep the second"): the
  registry's vocabulary is technical, and no honest matcher over the
  registry's own words bridges "cut" to `clip.remove`. The surface falls
  back to the supported menu ordered by how often the editorial loop asks
  for each operation, and the alternatives named beside a refused
  operation carry the rest.
- No live Resolve run: the surface reads the registry and the
  qualification records and never touches Resolve, and the briefs'
  substrates were not re-measured.
- The AGENTS.md index was left alone: the brief allows factual
  corrections there and no new rules, and this lane adds a rule.
