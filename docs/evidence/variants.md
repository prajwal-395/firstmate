# `library.tools.versions.variants` - the history behind its contract

This is the module docstring of `library/tools/versions/variants.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
VARIANTS: candidate reel versions alive beside a promoted one - declared on
a branch, built, compared and CHOSEN. Part of the version model
(`library/tools/versions/__init__.py`); AGENTS.md 3: run state.

Declaring: timeline variations as git branches
----------------------------------------------

The captain's version-control ask: make the LLM create multiple
variations of timelines, keep them connected through clones and forks
of the project git repo, and combine edits from one variation with
comments from another into a final - as native git operations.

Why this works is architectural, and it is worth stating precisely
because it reads at first glance like it contradicts the scout's
ruling. `data/vep-version-control-for-edits/report.md` section 7 rule
4 says: do NOT attempt timeline-level merge, restore-from-export, or
`.drp` surgery - merging EXPORTED timelines (OTIO, `.drp`, serializer
JSON) is closed because every export is a lossy projection that does
not round-trip. This module does the opposite approach, the one that
same section sanctions: a timeline is DERIVED - step 6.01 deletes the
build timeline and rebuilds it from declarations rather than editing
in place - so a variation is not a thing to be merged. Its
DECLARATIONS are. Merge the declarations, rebuild, and the combined
timeline falls out. That is ordinary git on ordinary text, and the two
rulings agree: never merge the projection, always merge the source.

Three pieces:

1. `create_variation` - one command that branches from the current
   committed state, records the variation's seam spec as a declaration
   (the cutaway spec that issue #925 found living NOWHERE in the
   project is exactly what this file exists to hold), and commits, so
   the branch carries everything a rebuild needs. The rebuild itself
   still runs through the normal build paths (`build_reel_variants`
   for seam comparisons, `build-reels` for plan reels) into the
   derived timeline name - the branch half never touches Resolve, so it
   runs anywhere.
2. A branch name and a timeline name derivable from each other, both
   ways (`variant/r09-reaction-cutaway` <-> `Reel 09 - ...
   (reaction-cutaway)`), so a human looking at either knows the other.
3. `merge_variations` - git merge plus the one judgement the merge
   needs: generated run state (`pipeline_data.json`,
   `pipeline_run.json`, step outputs, the manifest, LLM request
   records) is REBUILT, never hand-merged, so conflicting generated
   paths resolve to the target side automatically and only
   declaration conflicts reach a human. Marker pulls never conflict
   at all - each pull is its own timestamped file, so merging them is
   a union. Model ANSWERS (`llm_responses`) are the deliberate
   exception: written once per decision, never rewritten by a
   rebuild, so they merge for real - one side answering wins cleanly,
   both sides answering differently conflicts for a human. The merged
   tree is then rebuilt (in Resolve, by the caller) and committed.

What the branch half does NOT do: issue #925's per-row promote diff. A
merge workflow makes that defect MORE dangerous - merging
declarations is precisely where a feature goes missing quietly - so
until that gate lands, every merged variation must end with a human
read-back of the rebuilt timeline (the 29f9722-style verification),
not with a green build. The two ship together; see the docstring on
`merge_variations`.

Past seam-only, and the boundary that is held
---------------------------------------------
The spec landed able to say one thing: a different SEAM. That is why
it never served a real question - "the same reel with the other CTA"
is what a creative A/B actually is, and it could not be written down.

It now reaches the per-project DECLARATIONS too (`declares`, derived
from `external_inputs.DECLARATIONS`), and stops exactly there.
`OUT_OF_VOCABULARY` names what is out and who owns each near miss. The
rule that draws the line: a variant may differ in a declaration the
ordinary rebuild already reads, and in the seam offsets
`build_reel_timeline` already takes - nothing else. Both are things the
rebuild does on its ordinary path, so a variant is the rebuild with one
input changed. Anything wider would need the builder to take a path the
rebuild does not take, which is a SECOND BUILDER - and then the thing
beside the approved reel is no longer the approved reel with one change
in it, and the difference the captain is looking at is not the one they
asked for.

The declaration itself is never in the spec. It is the CONTENT of
`external/<store>.json` on the variant's own branch, which is the only
place the rebuild reads a declaration from - so `branch_requirement`
refuses a declaring variant built from anywhere else.

Timelines or branches: BOTH, and they are one object
----------------------------------------------------
A variant's DECLARATIONS live on a branch and its PICTURE lives on a
Resolve timeline; the two names derive from each other, both ways.
Git holds one branch at a time and Resolve holds every timeline at
once, and that asymmetry is not a problem - it is the reason both
halves exist. Declarations are merged ONE AT A TIME (the branch half
below); pictures are compared SIDE BY SIDE, which is the half the
captain actually watches (the built half below).

Built, compared and chosen
--------------------------
Two versions of a reel alive at once, compared, and one CHOSEN.

The captain, 2026-09-12, answering
`data/vep-can-it-hold-up-in-a-real-editing-workflow` §7: two versions of
a reel alive at once should be a ROUTINE workflow - expose
`build_reel_variants` and the variant branches, widen the spec past
seam-only, retire rather than delete on promotion. Retirement landed in
part one (`reel_retirement`); this is the half that makes the other two
mean something.

What was missing was not the building. `build_reel_variants` has put N
treatments of one reel side by side, atomically, with namespaced
captions and a `watch` note per variant, since it landed - and had no
caller. What was missing was everything AFTER the build:

- the two versions could only be compared BY EYE, because nothing kept
  what either one contained;
- CHOOSING one was not an act. There was no command, no consequence and
  no record: the loser sat in the bin under its comparison name until
  somebody deleted it by hand, and the round said nothing about a
  decision having been made at all.

So this half holds three things, and they are in the order a captain
uses them.

1. What is ALIVE
----------------
`record_build` stores, per built variant, its timeline name, its suffix,
what it declares, its `watch` note and the ROW SNAPSHOT
(`reel_read.rows_of`) of what was placed. Written by
`reel_build.build_reel_variants` at the moment each variant passes
conformance, into `pipeline_output/review/reel_variant_builds.json` -
beside the rounds, the sign-offs and the specs, on the version-control
allow-list.

The rows are the same payload `rounds` stores per round, for the
same reason: a few kilobytes of JSON outlives the timeline it describes,
so a comparison stays answerable after the loser has been archived and
collected. That asymmetry is what lets the lifecycle END.

2. COMPARED, not just watched
-----------------------------
`compare` runs two stored snapshots through `rounds.diff_reel` -
which is `reel_replace_guard.diff_rows` with the added rows put back -
and answers "what actually differs between the J-cut and the reaction
cutaway" off disk, with Resolve closed, in milliseconds. The captain
still watches both; this says where to look.

3. CHOOSING, as an act with a consequence
-----------------------------------------
`choose` is the whole point:

- the CHOSEN variant is renamed to the reel's own name. It becomes the
  reel - not a copy of it, the same timeline;
- the version that HELD that name is retired to `05 - Reels/Archive`
  exactly as a promotion retires it (`reel_retirement.retire_timelines`),
  because from the reel's point of view this IS a promotion;
- every UNCHOSEN variant is renamed into the same archive with the same
  `(archived round NNN)` suffix, so no comparison timeline is ever left
  in a bin the captain reviews under a name that reads like a
  deliverable;
- the ROUND records which suffix was chosen, what it was chosen over,
  and WHY. `--why` is required: a choice with no reason is not a
  decision anybody can read six weeks later.

The clutter bound, stated
-------------------------
The captain has asked four separate times for leftover timelines to be
cleared, and a variant workflow is exactly how clutter arrives - two
timelines per reel per comparison, for ever. So the bound is stated
here and it is the same one `reel_retirement` takes, on the same axis:

    at most `RETAINED_UNCHOSEN` (1) unchosen variant survives PER REEL.

Per REEL, deliberately, not per variant identity. `reel_retirement`
groups archived generations by the reel name they parse back to, and
every new suffix is a new identity - so a per-identity bound would keep
one archived timeline per suffix the project ever tried, which grows
with the number of comparisons and recreates the complaint. Grouping the
reel's variants together means the archive holds, per reel, the previous
cut and the most recent runner-up, and nothing else. Bounded by how many
reels the project has; not by how long it runs, nor by how often the
captain compares.

The reel's OWN retired generations are bounded by the same act, through
`reel_retirement.plan_collection` rather than a second rule here: a
choice retires the version that held the reel's name exactly as a
promotion does, so without asking that bound the archive would grow one
generation per choice - this bound arriving by the other door. Measured
2026-09-12 against a real Resolve, which is how it was found.

Two exceptions, both inherited from retirement and for its reasons: a
generation carrying a captain SIGN-OFF is never collected, and every
retained one is NAMED in the report on every choice. The sign-off case
is the one thing here that is not bounded by a constant - it is bounded
by how many sign-offs the captain grants, each explicit, each
withdrawable, and each named on every choice.

Variants alive at once are fine. Variants accumulating are not. What
bounds them is this constant and the fact that `choose` runs the
collection every time.

A variant and the sign-off
--------------------------
A variant IS built, and the captain's ruling was that a sign-off
attaches to a BUILT reel - so a variant can be signed off, and
`reel_signoff` needs no change to allow it: `feedback_ledger
.base_reel_name` strips the build's own container suffixes and leaves
everything else alone, so `Reel 09 - ... (reaction-cutaway)` is its own
sign-off identity already.

What it MEANS is decided here:

- choosing a variant over a SIGNED-OFF incumbent refuses unless the
  choice declares it, in `reel_signoff`'s own declare-then-proceed shape
  and with its own message - the chosen variant is taking that reel's
  name, which is precisely the replacement a sign-off exists to catch;
- choosing a variant that itself carries a sign-off CARRIES the sign-off
  onto the reel's name. The cut the captain approved did not change; its
  container did, and a sign-off that lapsed because a timeline was
  renamed would be an approval the machine withdrew;
- a signed-off variant that LOSES keeps its sign-off and is never
  collected. It is archived, which is where the version that was not
  chosen belongs, and the approval stays on it because the captain gave
  it.

`tests/test_version_variants.py`, `tests/test_version_variant_choice.py`.
```
