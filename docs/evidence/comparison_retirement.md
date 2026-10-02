# `library.tools.comparison_retirement` - the history behind its contract

This is the module docstring of `library/tools/comparison_retirement.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Comparison timelines retire like reels do, and end the same way.

The gap this closes
-------------------
A suffix verification build (`rebuild_reels_in_project(name_suffix=...)`,
e.g. `Reel 13 - ... (baseline scratch)`) promotes into its suffixed
final, takes a hold on it (`staging_holds` - awaiting an explicit human
promotion decision), and then nothing ever retires or collects it. The
next comparison suffix lands beside it, and the one after that beside
both. Measured 2026-09-13 on `lucie/geo-podcast`: 24 timeline records
for about 9 reels - the same accumulation the reel archive was built to
stop, arriving by the door the archive does not watch. The captain has
asked four separate times for leftover timelines to be cleared, and
`resolve_bin_layout.SCRATCH_BIN` records the incident a throwaway left
where it could be reviewed caused.

So this module extends `reel_retirement`'s treatment to them, reusing
its shape rather than inventing a second one: the same home-plus-
lifecycle split, the same scope guard, the same never-collect-a-
sign-off rule.

The home: `05 - Reels/Archive`, the SAME bin
--------------------------------------------
Checked before adding: `resolve_bin_layout` declares the archive bin
for "superseded reel versions, moved here when a rebuild lands rather
than renamed with a suffix" - a superseded comparison IS one - and no
bin is declared for comparison generations, so no new bin is invented
(`resolve_bin_layout` owns every bin path). What comes free with the
same home: `is_canonical` already accepts it, the organiser already
files a stray `(archived round NNN)` name back into it
(`resolve_organization`, whatever the reel part names - including a
comparison identity), and the conformance sweep already declines to
grade an archived name (`grades_as_a_reel`). A retired comparison is
renamed on the way in (`... (baseline scratch) (archived round 007)`),
so it cannot be mistaken for the live comparison in any list - the
same rename `reel_retirement.retire_timelines` performs, called
verbatim rather than restated.

The lifecycle: bounded by REELS, never by rounds
------------------------------------------------
`RETAINED_COMPARISONS = 1` per base reel. On the next promotion that
touches the base reel, live comparisons beyond the newest retire to
the archive, and archived comparisons beyond the newest one are
collected. The project holds, per reel, the current comparison and
the previous one, and nothing else - bounded by how many reels the
project has, not by how long it runs or how often the captain
compares.

Why one, not two: `manage_project.py round-diff` is the consumer the
retention number was checked against, and it reads stored rows, never
live timelines - `versions.rounds.diff_rounds` runs over the
`versions.rounds` rounds document off disk, with no Resolve, and every
promotion (suffixed or not) stamps the rows the replace guard already
read. Zero live copies are needed for a comparison; the one retained
copy exists so the latest comparison stays openable in Resolve. That
is measured behaviour, not preference, and it is why the record
outliving the timeline is what makes a lifecycle that ends acceptable
at all - the same asymmetry `reel_retirement` and `versions.variants`
state for their own generations.

What moves, and what never does
-------------------------------
- LIVE comparisons are only ever RETIRED (renamed into the archive),
  never deleted by this rule. Deletion is irreversible on the
  captain's machine; a rename is reported and reversible.
- Only ARCHIVED comparison generations are collected, and only when
  the version record proves a newer generation of the same base reel
  exists. Collection is `assert_deletion_scope`-guarded to exactly the
  planned archived names; a guard refusal stops and is reported, never
  worked around.
- A generation carrying a captain SIGN-OFF is never collected - and,
  further, a signed-off LIVE comparison is never retired either. The
  sign-off is read through `reel_signoff.signoff_for` on the
  comparison's own identity, the same way `versions.variants` treats a
  signed-off loser. A sign-off on the BASE reel does not protect its
  comparisons: the approved cut lives on the base timeline itself,
  which this rule never names, and the rows live in the round record.
  That is what keeps the bound a bound for signed-off reels - each
  sign-off explicit, each withdrawable, each named on every build.
- A HELD comparison is never retired: the hold names a promotion
  decision that is still open (`staging_holds`, issue #971), and
  moving the timeline out from under it would strand the hold
  pointing at a dead name. Held generations are kept and NAMED on
  every round.
- An UNRECORDED live comparison - no landed round in the version
  record - is never retired either. It is either hand-made (the
  captain's own work, which automation never auto-touches) or a build
  whose stamp failed (already reported as "round NOT stamped" where
  it happened). Kept, and named every round so a human can act. The
  bound therefore holds for everything the engine records, which is
  every engine build on the normal path.
- Only reels the promotion TOUCHED are considered at all - the same
  "a promotion tidies what it touched and nothing else" rule
  `plan_collection` keeps. A comparison whose base reel did not
  promote is untouched, however stale.

What this deliberately does NOT own
-----------------------------------
- Declared VARIANTS (`Reel 09 - ... (reaction-cutaway)`) are excluded
  by exact name through the spec record. A variant is work the
  captain deliberately asked to keep alive until CHOSEN, and
  `versions.variants` already bounds it (`RETAINED_UNCHOSEN = 1` per
  reel on every choice, with the rows stored so the comparison
  survives collection). Two bounds for one family would be two
  chances to disagree about whose the timeline is.
- STAGING containers (`... (rebuild staging)`) and pre-rebuild
  BACKUPS are excluded: they are owned by the build/promote/discard
  path plus `staging_holds`. A pending promotion is not disposable
  scratch, even when it wears a scratch-shaped name.
- Plan finals, the master timeline and firstmate's proof timelines
  are never members. And the reel archive's own generations
  (`<base> (archived round NNN)`) parse back to the base reel, never
  to a comparison identity, so the two retentions cannot collect each
  other's charges.

Wiring: `promote_staged_reels` runs this in phase 4 beside the reel
retirement, never fatally and always reported. A refusal leaves the
promoted reels promoted and the comparisons standing.

`tests/unit/reels/test_comparison_retirement.py`.
```
