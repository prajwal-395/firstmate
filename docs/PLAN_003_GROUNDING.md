# PLAN-003: grounding is a required planning stage

The punch list asks (PLAN-003):

> Make grounding a required planning stage.
> - Resolve each named target role to actual source media or timeline items.
> - Store verified identities and source intervals.
> - Distinguish claimed, observed, verified, and unresolved targets.
> - Re-query evidence when a target remains ambiguous.
> - Refuse destructive changes when a unique target cannot be established.

This is that, and the measurement on the eight-brief benchmark and on the
representative corpus (BASE-003).

## The defect it closes

A plan names TARGET ROLES - "the caption that misstates the number",
"the name bar", "the opening section" - and the only account of what
those roles point at was the run's own words. The eight-brief benchmark
showed what that costs: of the eleven target roles its plans name,
**two** name an item the recorded timeline can confirm, **two** name a
region the timeline answers with several items, and **seven** name
nothing at all. Nothing refused a destructive operation because its
target was not established; the refusals the benchmark recorded came
from the ROUTER (no eligible backend, no production route), which says
nothing about the target.

So the stage runs before anything is staged, and its answer is binding.

## What was built

- `library/tools/target_grounding.py` - the engine. One module, four
  answers, and no new store:

  | state | the answer |
  |---|---|
  | `verified` | exactly one candidate, with its identity and its source interval |
  | `observed` | the surfaces carried SEVERAL candidates: a unique target was not established |
  | `claimed` | the plan asserted an identity and no surface carries it |
  | `unresolved` | nothing was asserted, and nothing was found |

  - **The surfaces are the ones the engine already has**: the recorded
    timeline (`library.tools.timeline_shadow`), the typed evidence query
    (`library.tools.evidence_query`) and the occurrence identity
    (`library.tools.occurrence_identity`). Nothing new is stored.
  - **An ambiguous target is RE-QUERIED.** The timeline is asked first;
    when it answers with more than one candidate the evidence surface is
    asked again, and BOTH answers are in the receipt, so a reader sees
    what was asked and what came back.
  - **Two recordings of ONE clip are one target.** A rebuilt timeline
    records the same clip under a new id; the stage counts the MATERIAL
    once and keeps both ids, because counting them twice made a unique
    target look ambiguous.
  - **A destructive change needs a verified target.** An operation that
    removes, replaces or rewrites material is refused when the role it
    names is not `verified`. The destructive vocabulary is the engine's
    own safety class (`SAFETY_STRUCTURAL` in
    `library.tools.edit_algebra`), plus the five that rewrite or remove
    material in place (`clip.replace`, `multicam.select_angle`,
    `caption_fix`, `overlay.remove`, `overlay.replace`). An operation
    that destroys nothing is not forbidden: `overlay.position` on an
    ungrounded name bar is a move, not a loss.

- `library/tools/editorial_acceptance/grounding_stage.py` - the
  REQUIRED gate and the measurement on the eight-brief benchmark:
  `python3 -m library.tools.editorial_acceptance.grounding_stage
  --records tests/live/reports/editorial-bench-records`. It reads what a
  run actually said about a target (an item's id, a clip's file name,
  the record span it occupies, the row it lives on), grounds that
  against the RECORDED timeline, and reports the refusals.

- `evaluation/tools/grounding_corpus.py` - the measurement over the
  sealed corpus. It lives OUTSIDE the engine, because the sealed answer
  key must not be a path the engine can read:
  `python3 evaluation/tools/grounding_corpus.py`.

## The measurement, with its denominators

### The eight-brief benchmark

Over the eight recorded briefs
(`tests/live/reports/editorial-bench-records`):

| | before | after |
|---|---|---|
| briefs | 8 | 8 |
| target role instances named by the plans | 11 | 11 |
| grounded (verified against the recorded timeline) | not measured | **2 of 11** |
| observed (several candidates: no unique target) | not measured | **2 of 11** |
| claimed (asserted, carried by nothing) | not measured | **0 of 11** |
| unresolved (nothing asserted, nothing found) | not measured | **7 of 11** |
| destructive operations refused for an unestablished target | **0 of 8** | **8 of 8** |
| plans refused before staging by the gate | 0 of 8 | **4 of 8** |
| **planning** (the stage's own number) | **4 of 8** | **4 of 8** |

Per brief:

| brief | roles | grounded | observed | unresolved | destructive refused |
|---|---|---|---|---|---|
| `name-bar-fights-the-captions` | 1 | 1 | 0 | 0 | 0 of 0 |
| `open-with-the-cost` | 1 | 0 | 1 | 0 | 1 of 1 |
| `the-whole-job` | 7 | 1 | 1 | 5 | 5 of 5 |
| `the-wrong-caption` | 1 | 0 | 0 | 1 | 1 of 1 |
| `two-voices-at-once` | 1 | 0 | 0 | 1 | 1 of 1 |
| the three briefs that planned nothing | 0 | 0 | 0 | 0 | 0 of 0 |

Reading the numbers honestly:

- **The planning number does not drop**: 4 of 8 before and after. The
  grounding stage runs AFTER the planning stage and rewrites nothing
  the planning stage reads, so a plan whose targets are refused keeps
  the planning credit of the plan the run made. The same rule the
  feasibility gate follows (PLAN-005).
- **Four of the eight plans may not stage.** Three of them planned
  nothing, and the fourth is the name-bar brief, whose only operation
  moves an overlay rather than destroying anything.
- **The two grounded roles are the two the timeline can confirm.** The
  name bar names its item's id and its row, and the recorded timeline
  carries it; the whole-job brief's name bar names the same clip by its
  file name and its frames.
- **The two observed roles are genuinely ambiguous.** "The reel's first
  picture (frames 0-129)" covers two cameras, and the recorded timeline
  answers with both of them plus the overlays standing on those frames.
  The stage re-queried the evidence surface and the answer was still
  several, so the role stayed `observed` and `reel.opening` was
  refused.
- **The seven unresolved roles are the ones the run itself did not
  resolve**: no repeated passage, no second angle of a moment, no
  caption with a wrong number, no music bed, no wrong caption and no
  overlapping turn.
- **The "before" column is zero because nothing measured it.** The
  feasibility gate refused a plan naming a role the RUN did not
  resolve - the run's own account was the evidence - and the router
  refused on backend eligibility. No gate asked whether the target was
  an item the footage carries.

### The representative corpus (BASE-003)

Over the thirteen briefs of the sealed corpus, measured against the
corpus's OWN sealed evidence (its measured moments, and the recorded
timeline of each substrate):

| | value |
|---|---|
| briefs | 13 (8 name a target role; 5 do not) |
| target role instances | 8 |
| grounded (one measured moment, with its source interval) | **3 of 8** |
| observed (the premise names several measured moments) | **1 of 8** |
| unresolved (the premise is carried, but names no single item) | **4 of 8** |
| destructive operations refused for an unestablished target | **6 of 10** |

The corpus's briefs name their roles at the FEATURE level - "the
caption row", "the name card", "the second angle" - so the corpus's
own key grounds three of the eight roles to a single measured item and
leaves five where a run has to do the work. The three that ground:

- `open-on-the-cost-line` - the cost statement, one measured moment
  (`LCATL0011.MXF`, 109.14s-111.87s, the frame pulled and looked at);
- `hold-the-name-card-until-she-finishes` - the name card, one clip on
  `video6`, frames 0-84, which the recorded reel carries;
- `take-out-the-pause-where-she-stops` - the pause, one measured moment
  (`LC4932.MXF`, 4563.64s-4564.86s).

The one that stays observed is the repeated passage: the corpus measured
it three times, and "the earlier statement" does not say which of the
three, so `clip.remove` and `clip.trim` on it are refused.

## What was left undone

- **The stage is not wired into the AXI request path.** That path is
  identity-addressed already: a request's target is a `unique_id` or an
  editorial identity, the addressing layer (`timeline_addressing`)
  refuses an ambiguous one, and preparation resolves a name or a
  position to an item as a recorded ASSUMPTION. The stage would have
  been a second, weaker copy of a check that is already there, and it
  would have forbidden the captain's touch-up path, which addresses
  clips positionally on purpose. The stage is wired where roles are
  named in words: the editorial plan path.
- **No live run.** Both measurements read RECORDS and the machine's
  recorded timelines. They contact no Resolve and change no project. A
  live run on a Ren-owned scratch copy would measure the same gate
  against a live timeline; the recorded generation carries the items,
  their rows, their record spans and their source handles, and the
  effect state and the media kind would then be live too.
- **The "grounded" counts are machine-dependent.** They need the
  recorded timeline of the reel the benchmark ran against
  (`Ren Scratch - breaktest` / `Reel 13 - the-accounting-firm-ai-called-healthcare`).
  On a machine without it, the same measurement reports the two roles
  as `claimed` rather than `verified`, and the refusals are unchanged.
  The corpus's numbers do not depend on it: they come from the corpus's
  own measured moments.
- **The evidence surface is not read in the benchmark measurement.** The
  records carry no Project Model, so the stage asks the evidence surface
  and the answer is "the model was not read"; the receipt says so, and
  the ambiguity stays. The occurrence route is exercised by the unit
  tests against a model, not by the benchmark.
- **The acceptance report was not regenerated.** It carries absolute
  paths from the worktree it was measured in, and regenerating it here
  would rewrite those. The grounding stage's measurement has its own
  module and its own command, exactly as the feasibility gate's
  measurement does (PLAN-005).
