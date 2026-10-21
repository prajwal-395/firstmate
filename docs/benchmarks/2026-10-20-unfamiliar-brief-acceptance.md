# Unfamiliar briefs, run live: eight briefs, five stages, classified causes

The punch list asks for a set of unfamiliar editorial briefs against
representative real footage, measured per stage, judged on the exported
file, with the failure causes classified so the next engineering change
targets the actual bottleneck.

This is that run, live, on 2026-10-20.

- the harness: `library/tools/editorial_acceptance/` (PRs 2900, 2904,
  2906), merged onto this branch's base and re-measured
- the briefs: all eight of `library/tools/editorial_acceptance/brief_set.json`
- the records: `tests/live/reports/editorial-bench-records/*.json` (one per brief)
- the report: `tests/live/reports/editorial-brief-acceptance.json` (`ren accept`)

## What was run, and against what

**Substrate.** The acceptance set was run against real footage, on Ren-owned
scratch copies only. Reel 13 of the captain's `Podcast (field test)` project
("the accounting firm AI called healthcare", 79.5s, vertical 1080x1920 at
23.976) was exported to a `.drt` from the Ren-owned scratch project
`Ren Scratch - breaktest` and copied into eight fresh scratch projects
(`Ren Scratch - bench <brief id>`) through the merged scratch import. Each
copy carries its own media pool with 56 items and zero offline media. The
captain's projects and timelines were never edited, and `ren qualification
reset` was never run.

**Method.** Every brief was worked by hand through the front door, not
through a script: the request was read, the footage was searched, a plan
was named, and the plan was put to the engine's own router and executor.
Every Resolve write went through the serving broker lease
(`ren resolved submit` / `ren revise`); nothing was paced by hand.

**Evidence.** The reel's own words were read: all 45 caption cards' rendered
text (the cards are the reel's spoken word), the timeline's audio and video
row spans, the media pool's 56 items, and the master timeline's transcript.
The reel was rendered (`resolve-axi render inspect`) to look at, not only to
read.

## The numbers, with their denominators

These are the numbers **as the run produced them**, and they are the
numbers the shipped report still carries: the premise measurement
described under cause 1 below is reported ALONGSIDE them and does not
move them.

> **Amendment, 2026-10-21 - the planning number is now GATED on the
> brief's acceptance criteria.** The harness used to compute the
> acceptance criteria a plan did not declare covered, publish the gap in
> the detail, and leave it out of the pass condition, so a plan that
> named one criterion and was silent about the preservation of the rest
> read as a valid answer. `planning` now FAILS on that gap, and the
> planning number moves from **8/8 = 1.00** to **4/8 = 0.50** - the
> three refusal briefs (whose correct outcome is to plan nothing, and
> which the gate does not touch) plus the one edit brief that declares
> all ten of its criteria. Alongside the headline the report publishes
> `criteria_coverage`: **15 of 22** acceptance criteria declared covered
> across the five briefs that expect an edit. Every other number here is
> unchanged, and the bottleneck is still `interpretation`.

> **Amendment, 2026-10-21 - the interpretation stage is now published as
> a SPLIT.** The stage measures two different things: whether the run
> READ the request, and whether it could GROUND the work in something
> the footage carries. The report now publishes both beside the
> unchanged headline:
>
> | view | measured | passed | value |
> |---|---|---|---|
> | `reading` (briefs) | 8 | 8 | 1.00 |
> | `reading` with the request's stated constraints | 8 | 7 | 0.875 |
> | `grounding` (role instances, applicable) | 4 | 3 | 0.75 |
> | `grounding` (role instances, all) | 11 | 3 | 0.27 |
>
> The one reading defect the run made is now MEASURED rather than
> invisible: `name-bar-fights-the-captions` says "Move it down", and the
> run's restatement says "move it so it stops colliding" - the direction
> was dropped, and no schema could express it before. It is reported as
> `direction-down` in the reading view.
>
> The grounding view counts ROLE INSTANCES, and keeps three answers
> apart: seven role instances are **not applicable** (the substrate does
> not carry the premise the request presupposes, so there was nothing to
> point at), three of the four applicable ones were resolved, and of the
> four the run CLAIMED to resolve, exactly one is **verified** - the
> recorded timeline carries the clip it named. The other three name no
> clip the harness can check, so they are unverified rather than
> resolved-and-trusted. The headline is untouched by all of this.


`ren accept --records tests/live/reports/editorial-bench-records`
(eight briefs, eight scored, none not-run):

| stage | measured | passed | value | not measured |
|---|---|---|---|---|
| interpretation | 8 | 5 | 0.625 | 0 |
| retrieval | 4 | 0 | 0.0 | 4 |
| planning | 8 | 4 | 0.5 | 0 |
| execution | 8 | 3 | 0.375 | 0 |
| final quality | 0 | - | not measured | 8 |

Briefs passed: **3 of 8** (the three briefs whose correct outcome is to
report back). Briefs failed: **5 of 8**.

There is deliberately no combined score: the five stages are not the same
unit, and the denominators differ (four briefs declare no retrieval
expectation, so retrieval has four; no brief delivered a file, so final
quality has none).

**Final quality was not measured on any brief, because no brief delivered
a file.** No brief's work reached a committed edit, so there was nothing to
export, and the harness says `not_measured` rather than a zero. The render
path itself was exercised (the reel renders, 111.3 MB, 79.5s, with audio);
what is missing is an edit to render.

## Per brief

| brief | bottleneck | cause (harness) | what actually happened |
|---|---|---|---|
| `no-extra-footage` | - | - | **pass**: no workshop-bench footage exists in the project; said so, changed nothing |
| `logo-not-in-the-project` | - | - | **pass**: the named logo is not in the project; said so, changed nothing |
| `keep-it-but-tighten-it` | - | - | **pass**: keeping every word and tightening the cut are two requests; asked which, changed nothing |
| `open-with-the-cost` | retrieval | `retrieval.unavailable_asset` | the reel's 45 caption cards name no cost; the only cost statement in the project (Craig, "eat into their budget", 00:13.9 of the master) is a **different recording** and is not in this reel |
| `name-bar-fights-the-captions` | planning | `planning.criteria_not_covered` | the name bar was found and measured (the lower third `b-soft-focus-craig.mov`, V6, frames 0-84), and **the engine has no route to move it** - see below. Its plan also declared two of the brief's three criteria covered and stayed silent about `others-untouched`, which is the gap the planning gate now names; the execution refusal below is still true, and is still the reason the edit could not happen |
| `two-voices-at-once` | interpretation | `interpretation.target_unresolved` | there is no overlapping speech to make followable: the two dialogue tracks are disjoint, and the reel's two camera rows are two **speakers**, not two angles of one moment |
| `the-wrong-caption` | interpretation | `interpretation.target_unresolved` | no caption names a word the speech does not - all 45 cards match their speech; and `caption_fix` could not have rewritten a rendered card anyway |
| `the-whole-job` | interpretation | `interpretation.target_unresolved` | four of its seven targets are absent from the footage (no repeated passage, no second angle, no wrong-number caption, **no music bed**); the rest had no eligible route |

## The causes, classified - and what each one asks for next

The harness names the first failing stage per brief. Underneath it there
are three distinct bottlenecks, and they need three different changes.

### 1. The brief set's premises do not hold against real footage

Four of the eight briefs name something the footage does not contain: a
statement of what it costs, a moment of overlapping speech, a caption that
names the wrong word, a music bed. This is a **set** problem, not a run
problem: an acceptance set is only meaningful if the footage it runs
against satisfies the briefs' premises. The remedy is to give each brief a
footage profile whose content actually carries its premise - a reel with a
music bed for the levels brief, a reel with a second camera angle for the
angle brief - and to state the profile per brief in the set, which the
brief set already does (`footage_profile`) but does not yet ENFORCE.

**This is now measured, alongside the score.** Each brief states the
premise its request presupposes of the footage
(`oracle.footage_requirements`), each substrate reel is measured once
and recorded with the features it carries and the features it was
checked for and does not carry
(`library/tools/editorial_acceptance/substrates.json`), and the
harness reports, NEXT TO the score, whether the substrate carried the
premise - naming the premise that is missing. The headline is
unchanged: the run is still scored, and a brief the footage cannot
carry still counts as the engine's result. A run also picks its
substrate per brief - the one whose profile matches and whose measured
content carries the premise - and the report names, per brief, the
exact premises a substrate still has to carry. See
[the premise contract](../EDITORIAL_ACCEPTANCE_PREMISES.md).

Re-measured against the same records, the premise view says that **four
of the eight briefs were not carried by reel 13**
(`open-with-the-cost`, `two-voices-at-once`, `the-wrong-caption`,
`the-whole-job`): the reel carries no cost statement, no overlapping
speech, no second angle of the same moment, no caption that misstates
its speech and no music bed. Those four briefs stay in the headline -
three of the eight briefs pass, five fail, and the bottleneck is still
`interpretation` - and the premise view counts them as `not_measured`
alongside: 4 of 8 briefs measured, 4 not measured, 3 of the 4 measured
passing.

**The fix is the substrate, and it is not a harness fix.** Making every
brief runnable means running each brief against footage that satisfies
its premise - a reel with a music bed for the levels brief, a reel with
a second camera angle for the angle brief - which is the reel export
lane's work. The pairing in the report is the work list: per brief,
the profile it is written against and the premises its substrate has
to carry.

The run also hit a hard limit here: only one reel of the captain's podcast
could be copied out. The scratch import takes a `.drt` from the project
that is open, and a recorded script may not switch projects, so the other
thirty reels of `Podcast (field test)` could not be exported for this run.
**A reel-level export from an arbitrary project is the capability this
benchmark needs next** - without it, every brief is measured against
reel 13 whatever its premise.

### 2. Production routing is closed on real footage

The one operation with a production-qualified executor - `clip.set_property`,
the transform write - refuses on this reel, with the engine's own words:

> runtime state does not satisfy recorded qualification predicates:
> timeline_density is 'dense', not 'light'; project_settings is
> 'portrait_1080x1920', not 'landscape_1920x1080'; frame_rate is '23.976',
> not '24'; existing_effects is 'transforms+retiming', not one of
> ['none', 'transforms']

The qualification evidence was earned on the qualification lane's synthetic
timeline: landscape 1920x1080, 24 fps, light, no effects. Ren's own reels
are **vertical 1080x1920 at 23.976**, dense, with transforms - so the
predicate can never hold on the product's real format and production
routing fails closed on every real reel. This is the single highest-value
finding of the run: the qualification lane qualifies on a format the
product does not ship.

**What this asks for:** re-qualify the operation set on a real-footage
timeline in the product's delivery format (or widen the predicates to the
formats the product actually produces). Until then, no edit to a real reel
is reachable through production routing, and every brief that needs one
stops at the execution stage.

### 3. Two capabilities do not cover the media the briefs name

- `overlay.position` (the name bar): *"edit 0 (position_overlay) targets
  unsupported media 'b-soft-focus-craig.mov' - the first route is limited
  to a native PNG still without a drawing Fusion comp"*. A lower third that
  is a rendered `.mov` cannot be moved. The brief set's name-bar brief
  needs a `.mov` lower third, or the capability needs to cover one.
- `reel.opening`, `clip.trim` (alone), `multicam.select_angle`,
  `broll.insert`, `caption_fix`: refused by the router under the
  production policy, and **not covered by the protected experimental
  policy either** - which names `overlay.position`, `audio.gain`,
  `transition`, `overlay.resize`, `clip.split`, `clip.slide`, `clip.roll`
  and the two compounds `{clip.trim, clip.insert}` and
  `{clip.replace, caption.style}`. A lone `clip.trim` and a lone
  `clip.replace` are unroutable, so "tighten the opening" and "swap this
  clip's source" have no route at all.

### A defect that blocked the whole lane, and is fixed here

`ren revise` could not apply **any** program written to its published
schema. The schema declares `program.base_generation` a **string**; the
request builder passed that string straight into the request's integer
`expected_base_generation`, and the staleness check compared an int with a
str. Every program was refused with the same number on both sides:

> request base generation 1 is stale; recorded head is 1

Fixed in this branch (`library/tools/revise.py`: the request now carries
`_program_base(program)`, the int the module already had), with a test in
`tests/contracts/test_revise_axi_integration.py`. Before the fix, the lane
stopped at the first prepare; after it, the request prepared, routed and
reached the executor, and the real refusals above became visible.

## What was left undone

- **No brief delivered a file**, so `final_quality` has no denominator and
  the independent judge never saw a delivered file. The judge path itself
  is in the harness (`--judge module:attribute`); it had nothing to read.
- **The `the-whole-job` demonstration** scored 4 of its obligations
  established (`interpreted`, `plan-valid`, `no-open-transaction`, and one
  more), with `evidence-backed`, `qualified-route` and `unrelated-unchanged`
  unestablished - the last two for the reasons above.
- **Only reel 13 was available** as a substrate (see cause 1).
- The brief set's own premises (a wrong caption, a music bed, overlapping
  speech) were **not seeded** into the substrate: they are the creator's
  pre-existing edits in the fiction, and fabricating them would have meant
  the run grading its own fixture. They are now STATED per brief and
  checked against the substrate, and the pairing in the report says which
  footage each of those briefs still needs.

## Reproducing this run

```sh
bin/ren accept --list
bin/ren accept --records tests/live/reports/editorial-bench-records \
    --report tests/live/reports/editorial-brief-acceptance.json
```

The records are data; the report is regenerated from them. The evidence
behind each refusal is quoted in the record's `execution.refusal.reason`
and in the table above.
