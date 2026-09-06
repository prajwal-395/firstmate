# The end-to-end reel build: a refusal, the data-loss bug behind it, and the fix

**Date:** 2026-09-06
**Project:** `Podcast (field test)` / `GEO Podcast - Synced`
(`~/Documents/content_stuff/video_projects/lucie/geo-podcast`)
**Branch:** `fm/vep-end-to-end-build`

---

## 0. Verdict first

**No reel was built, and no build should have been.** Driving `reel.build`
against the field-test project - the operation, through the process, exactly
as the task asked - would have **deleted all nineteen of the captain's
approved reel timelines** before placing anything. Every derived requirement
that operation declares was SATISFIED, so nothing would have stopped it.

The captain has confirmed the finding independently
(`reel_build.py:748-755` on `main`) and ruled that fixing it *is* the task:

> Do not add a separate non-destructive path beside the destructive one;
> that leaves the loaded gun on the table. [...] Consider whether it should
> refuse outright rather than delete when a timeline it did not plan to
> touch would be removed; a refusal is cheap and a deleted timeline is not.

This branch is that fix. **The end-to-end build is gated by the captain and
has not been run.** The captain's twenty timelines are proven byte-identical
below - not because a build was survived, but because none was attempted.

---

## 1. The operation calls made, in order

Every call below is read-only. Nothing wrote to Resolve.

| # | Call | Result |
|---|---|---|
| 1 | `operations.all()` -> names starting `reel` | `['reel.candidates', 'reel.select', 'reel.build', 'reel.verify']` |
| 2 | `operations.get("reel.build").requires` | `timeline_transcript.on_file`, `env.resolve_scripting`, `reel_plan.approved`, `resolve.timeline_binding` |
| 3 | `operations.get("reel.build").unmet(<project>)` | **`none`** - the operation would have run |
| 4 | `operations.get("reel.verify").unmet(<project>)` | `['state.verify_reels.reel_build']` - correctly refuses until the build node has run |
| 5 | `reel.build.execute(<project>, only_reels=[3], timeline_name_suffix=" (pipeline rebuild)")` | **completed - with `rebuild_reels_in_project` stubbed out, so Resolve was never reached.** Never run for real. See §2. |

Step 3 is the whole problem. `reel.build` is armed. Its contract refuses on
nothing. The next thing it does is delete nineteen timelines.

```
$ python3 -c "from library.tools import operations
print([o.name for o in operations.all() if o.name.startswith('reel')])"
['reel.candidates', 'reel.select', 'reel.build', 'reel.verify']
```

### The operation CAN carry the build - proven without touching Resolve

With `rebuild_reels_in_project` replaced by a stub, so Resolve is never
reached, the whole plumbing was exercised: overrides -> `Operation.execute`
-> `gather_step_inputs` -> the step body -> the module.

```
node order for the reels process: ['build_reels', 'verify_reels']
status  : completed
node    : build_reels
reached the module with:
             only = [3]
      name_suffix = ' (pipeline rebuild)'
    skip_captions = False
           verify = False
```

So the answer to the task's known unknowns is: **yes**, `reel.build` can now
drive a build for ONE named reel; **yes**, building into a new timeline name
is supported; the model is reached at SELECTION time (3.04), not at build
time, so there is no ordering circularity; and the caption path does not
need the reel to exist first - it is driven from the reel's own spine.

The one thing between this and a real timeline is the captain's gate.

### Why this is not a workaround

The task said: if you find yourself calling `reel_build.rebuild_reels_in_project`
directly, stop - reaching for it is itself the finding. I did not. I went
through `library/tools/operations.py`, checked the derived requirements, and
read what the step body would do next. The finding is one layer further in
than expected: the operation registry is correct, the process is correct, the
requirements are correct - and the module they all point at destroys the
captain's work.

---

## 2. The defect

`library/tools/reel_build.py` on `main`, immediately before placing anything:

```python
timelines_to_delete = []
for i in range(1, project.GetTimelineCount() + 1):
    t = project.GetTimelineByIndex(i)
    if t.GetName().startswith("Reel "):
        timelines_to_delete.append(t)

if timelines_to_delete:
    pool.DeleteTimelines(timelines_to_delete)
```

It landed in **#507**, inside a batch of eight unrelated reel fixes, and was
never the subject of a ruling. It is the convenience a standalone rebuild
script has and a pipeline operation must not.

Three distinct failures:

- **Prefix, not name.** `startswith("Reel ")` is the exact hazard AGENTS.md 5
  already names for addressing a Resolve *project* - *a near match lands
  elsewhere* - at the one call site where getting it wrong destroys work
  rather than reading the wrong thing.
- **Unconditional.** It runs before the build, over everything, whatever the
  plan contains. A reel the plan no longer describes is deleted with nothing
  saying so and nothing backing it up.
- **All-or-nothing.** There was no way to build one reel. The only available
  act was "delete nineteen, write nineteen".

The half of the pair that was already right is `plan_provenance`, which has
MERGED rather than replaced since **#568** for exactly this reason - *"a
partial rebuild must not delete the provenance of the reels it did not
touch"*. A partial rebuild could not happen, because the delete loop ran
first and took everything. The two halves contradicted each other on `main`.

### Deletion scope, before and after

Measured with the placer, the verifier and Resolve faked, against a project
carrying the master + 19 approved reels + 1 orphan reel the current plan no
longer contains (21 timelines):

```
main   / full rebuild        : 20 deleted, 0/19 approved survive, orphan destroyed
branch / full rebuild        : 19 deleted, 0/19 approved survive, orphan SURVIVES
main   / build ONE reel      : not expressible - TypeError: unexpected keyword argument 'only'
branch / build ONE reel      :  1 deleted, 18/19 approved survive
branch / one reel, new name  :  0 deleted, 19/19 approved survive
```

A full rebuild still replaces all nineteen, because that is what a full
rebuild *is*. What changed is that it replaces exactly its own output, that
building one reel is now a real act, and that nothing outside the plan is
touched.

---

## 3. The fix - one path, not two

Per the captain's ruling there is no second non-destructive path. The single
build path was changed:

- **`timelines_to_replace(project, target_names)`** - collects the existing
  timelines this build will place, **by exact name, never by prefix**.
- **`assert_deletion_scope(timelines, target_names)`** - asked of the list
  **about to be deleted**, immediately before `DeleteTimelines`, and REFUSES
  rather than deleting anything unplanned. Deliberately *not* a restatement
  of the selection: a guard that recomputes the selection cannot catch the
  selection being wrong, which is precisely how a loop meant to replace a
  reel came to take nineteen.
- **`only=[n, ...]`** and **`name_suffix="..."`** scope that same path. They
  are not a second path - they are what the one path is told to build and
  what to call it. `only` naming a reel the plan has not approved REFUSES
  rather than building nothing and reporting success.
- `name_suffix` reaches the **captions** too. The segment filename's
  `timeline` component is the only discriminator between two reels' overlays
  and it carries no caption content
  (`library/tools/subtitle_segment_id.py`), so a rebuild captioned under the
  old label would write new words into the exact `.mov` files the approved
  timeline still points at - **every database row identical and the picture
  changed**. That is a second, quieter version of the same data loss, and it
  is closed here.

Wired through: `library/steps/step_7_01_build_reels/step.py` reads
`only_reels` / `timeline_name_suffix` from the merged input dict, the step
manifest declares both as optional inputs, and `manage_project.py
build-reels` gains `--only-reel N` (repeatable) and `--name-suffix TEXT`.
The build record now carries `reels_requested` and `name_suffix`, so a
record of one reel built into a new container cannot read as a full rebuild
that happened to place one timeline.

### A second finding the fix produced

The first version put the `only_reels` validation in the step body, and
`tests/test_input_declarations_are_true.py` failed three ways on it: a step
that guards an input and raises READS as refusing without it, while its own
manifest declares that input OPTIONAL - the disagreement AGENTS.md 3 exists
to catch ("no step may declare an input ... optional that its own code
refuses without").

The guard was also in the wrong place. What a malformed `only_reels` *means*
belongs to the code that interprets the value, so `reel_build.reel_numbers`
now owns it - accepting `None`, a list, or a string like `"3 5"` / `"3,5"`,
because an operation's overrides arrive from a shell and from JSON on stdin
alike - and REFUSING a timeline name rather than guessing which moment it
refers to. The step forwards the value untouched.

### Tests that bite, in both directions

`tests/test_reel_build_touches_only_its_own_timelines.py` - 12 tests. The
fake media pool **really removes** what it is handed, so "the other eighteen
are still there" is read off the project afterwards rather than inferred
from a mock call.

| test | direction |
|---|---|
| `test_building_one_reel_leaves_the_other_eighteen_present` | survivors asserted **by name**; on `main` this reads `['GEO Podcast - Synced']` |
| `test_building_one_reel_into_a_new_name_deletes_nothing_at_all` | `DeleteTimelines` never called |
| `test_a_full_rebuild_replaces_its_own_output_and_spares_an_orphan` | the orphan the old loop destroyed |
| `test_the_guard_permits_replacing_exactly_what_is_being_placed` | **must NOT fire** on correct input |
| `test_the_guard_refuses_a_timeline_that_was_never_planned` | **must fire**, and name the timeline |
| `test_the_guard_is_wired_where_the_deletion_happens` | over-collecting selection driven through the **real** build path; refuses before anything is deleted or placed |
| `test_the_selection_matches_a_name_exactly_never_by_prefix` | `Reel 03`, `Reel 03 (old)`, `Reel 30` - one match |
| `test_only_refuses_a_reel_the_plan_has_not_approved` | refusal, nothing deleted |
| `test_the_suffix_reaches_the_caption_filenames` | the quiet half of the data loss |
| `test_the_record_says_what_the_build_was_asked_for` | the record cannot lie about its scope |
| `test_reel_numbers_accepts_the_spellings_an_override_arrives_in` | `None`, `[3]`, `["3", 5]`, `"3 5"`, `"3,5"`, `[]` |
| `test_reel_numbers_refuses_a_name_rather_than_guessing_a_moment` | a timeline name is refused, not resolved |

All twelve pass on this branch. On `origin/main` the module-level import of
`assert_deletion_scope` and `timelines_to_replace` fails, so the file cannot
collect - which is honest but is *not* a demonstration of the behaviour. The
behaviour itself was demonstrated separately, by running the same fake
project through `main`'s own `rebuild_reels_in_project` with no new symbol
imported: that is the before/after table above, and on `main` it reads *"20
deleted, 0/19 approved survive"*.

The guard is proven to fire **where it is wired**, not merely where it is
defined - which is the distinction three of today's checks failed.

---

## 4. What the model decided, and its reasoning

Selection **did** reach a model: step 3.04 `select_reels` is a hybrid step
(`reel.candidates` -> model -> `reel.select`), and its published output is
`pipeline_output/review/reel_proposals_v2.json`, 19 moments, all approved by
the captain. This is the plan the build would have been driven from, and it
is quoted here verbatim rather than summarised.

**Reel 03, the model's own words:**

> "The tightest thing said in the episode and it is already whole: Akshita
> lands the line, Craig takes it into why it decides who gets recommended,
> and Akshita closes on the invitation herself. **No borrowed closer
> needed.**"

That last clause is a real decision, not decoration. The captain's format
closes reels on a spoken CTA taken from anywhere in the episode; the model
declined to borrow one here and set `call_to_action: null`, on the grounds
that this passage already ends on the invitation. Confirmed mechanically:
`reel_build.cta_range(moment)` returns `None`, so `reel_ranges` is the body
window alone.

**Span chosen:** master 301.241s - 341.270s (40.029s), speakers Akshita and
Craig, slug `search-didnt-change-the-question-did`.

**The takes.** The model recorded two `duplicate_takes` on this moment - the
repetition the captain heard. The build's own retake scan
(`redundant_takes`, narrow by design: same speaker, durations within 2x,
containment >= 0.75, Jaccard >= 0.55, second within 30s of the first) finds
**three** cuts and keeps the LATER take each time:

| dropped | kept | text |
|---|---|---|
| 301.241 - 302.566 (1.325s) | 306.801 - 307.596 | "Yeah, so search didn't change." |
| 302.626 - 303.449 (0.823s) | 307.840 - 308.840 | "The question changed." |
| 309.920 - 310.120 (0.200s) | 310.120 - 310.380 | "Yeah, so search didn't change. The question changed. And whoever AI understands best, gets the answer." |

**Keep ranges:** `[(302.566, 302.626), (303.449, 309.920), (310.120, 341.270)]`
= **37.681s of 40.029s**, i.e. **2.348s removed**.

**The captain's approval, verbatim, in the plan:**

> "yeah i skimmed through and these are a lot better overall, i'm not gonna
> give rigerous feedback on the scripts, but im gonna approve them and want
> you to build it all out and then i'll give feedback on the actual timeline
> you create"

Caption planning (4.01) is deterministic from the spine's own word
timestamps; it is not a model call, and this report does not claim one.

---

## 5. The 2.29 seconds of repeated speech

**Measured on the plan, not on a new timeline, because no new timeline
exists.** Stated plainly so the number is not read as more than it is.

The task briefed 2.29s. The measurement today is **2.348s** (1.325 + 0.823 +
0.200). I did not reproduce 2.29 and am not going to claim it: the two
smallest terms differ by 0.058s, which is the width of the 0.06s keep-sliver
between the first two cuts, so the earlier figure was likely taken before
`#576` (06:38 today) re-read rows that cross a cut. The honest statement is
**2.348s as measured now, on `main` as of `46b4685`.**

**Is it in the captain's existing Reel 03? Yes.** Read off a copy of
`Project.db`, not off a plan:

- V1 carries 4 clips: 131 + 138 + 383 + 307 = **959 frames** = **40.00s** at
  23.976fps. That is the *full* span. The keep-ranges would give 903.5
  frames.
- The Sep-5 conformance report for that timeline: `actual_frames: 959`,
  `plan_frames: 959.7`, **`bad_take_cuts: 0`**.
- Its caption track opens on
  `..._akshita_yeah-so-search-didn-t-change_...mov` - the repeated line is
  not only played, it is captioned.

So the repetition the captain heard is present in their approved Reel 03,
the plan says it should be cut, and **whether the rebuild actually removes
it can only be answered by a build that has not been permitted to run.**
That is the one part of the task this report cannot close.

Worth flagging for whenever the build is gated: today's verifier applies
`redundant_takes` when deriving the expected plan, so re-running it against
the captain's *existing* reels will now report length mismatches on the
uncut ones. That is the instrument telling the truth about old output, not
a new defect - and `verify_built_reels` raises on any error finding, so a
future `reel.verify` may fail for reasons that belong to the nineteen
originals rather than to anything newly built.

---

## 6. Verifier findings on the new timeline

**None. There is no new timeline.** The conformance verifier was not run
against the live project, because the captain's instruction is that no build
runs until they have gated this fix, and the verifier's own value here is as
the *post-build* gate.

For reference, the last run on disk
(`pipeline_output/review/conformance_report.json`, 2026-09-05, before all
nine of today's changes): 19 reels checked, **0 errors, 6 warnings**, all
`PQ-LENGTH`. Reel 03's is *"plan is 40.0s, under the 45.0s minimum
guidance"* - a warning about the reel being short, which the 2.348s cut will
make marginally shorter, not longer.

---

## 7. Is this reel good? A plain answer for the captain

I cannot tell you whether the rebuilt Reel 03 is good, because I was right
not to build it and you have since confirmed that. What I can tell you:

1. **Your Reel 03 has the repetition in it.** 959 frames, zero bad-take
   cuts, and the doubled line is captioned on screen. You heard it because
   it is there.
2. **The plan to remove it is sound and narrow.** 2.348s across three cuts,
   all same-speaker, all keeping the later take. It will not touch Craig's
   section or the closer.
3. **The model's choice of this moment holds up.** It picked the one passage
   that already ends on the invitation and explicitly declined to borrow a
   closer - which is the judgement, not the mechanics.
4. **The rebuild will be 37.7s, ~2.3s shorter than yours.** That takes it
   further under the 45s guidance the verifier already warns about on this
   reel. Worth a decision from you: cut the repetition and accept a shorter
   reel, or widen the span.
5. **What nearly happened is the real headline.** An operation merged three
   hours before I ran it was armed, satisfied all its declared requirements,
   and would have deleted nineteen approved timelines to write nineteen. The
   contract layer was working perfectly and pointed at a module that
   destroys work. A prerequisite check cannot catch this class of fault -
   only a guard at the call site can, which is why this branch adds one that
   refuses.

---

## 8. Before/after hashes of all 20 original timelines

Content hashes over the full timeline -> sequence -> container -> track ->
item join, from **copies** of
`~/Pictures/Davinci/.../Podcast (field test)/Project.db`. Never opened in
place. "Before" was taken at the start of this task, "after" at the end.

| timeline | items | md5 before | md5 after | identical |
|---|---:|---|---|---|
| `GEO Podcast - Synced` | 334 | `35cccb8dc667923a4b033a83657aab05` | `35cccb8dc667923a4b033a83657aab05` | yes |
| `Reel 01 - seo-ranks-geo-understands` | 34 | `a6e1fae97011d91646ce2341dabceb94` | `a6e1fae97011d91646ce2341dabceb94` | yes |
| `Reel 02 - seo-that-hurts-your-ai-ranking` | 55 | `24330c9ef5634ebfbcf7c286a5ac166b` | `24330c9ef5634ebfbcf7c286a5ac166b` | yes |
| `Reel 03 - search-didnt-change-the-question-did` | 35 | `6f507bf6c723786513be983ef3175a59` | `6f507bf6c723786513be983ef3175a59` | yes |
| `Reel 04 - consistency-beats-size` | 44 | `84ade97e9c2d97f31fb16e27f42eb7f9` | `84ade97e9c2d97f31fb16e27f42eb7f9` | yes |
| `Reel 05 - the-audit-that-was-eye-opening` | 82 | `0b126903921df9e409f966eaec221fe1` | `0b126903921df9e409f966eaec221fe1` | yes |
| `Reel 06 - where-ai-is-reading-you` | 36 | `a02d171125a690aa86002b3659d2f406` | `a02d171125a690aa86002b3659d2f406` | yes |
| `Reel 07 - website-is-resume` | 29 | `2766f9bfea4a3fbac1e496392a4cd1c1` | `2766f9bfea4a3fbac1e496392a4cd1c1` | yes |
| `Reel 08 - why-ai-trusts-a-cited-brand` | 46 | `14b1c8579a83bca23c13ac5296ac4f85` | `14b1c8579a83bca23c13ac5296ac4f85` | yes |
| `Reel 09 - first-step-is-understanding` | 34 | `f968bad1262a2d57e5a0c307f9d1e93a` | `f968bad1262a2d57e5a0c307f9d1e93a` | yes |
| `Reel 10 - the-seven-modules` | 51 | `de855719a18e9e394e45bb00667738db` | `de855719a18e9e394e45bb00667738db` | yes |
| `Reel 11 - what-hallucinating-actually-means` | 52 | `8926bb2a3780167680a89e867485f610` | `8926bb2a3780167680a89e867485f610` | yes |
| `Reel 12 - not-a-content-problem` | 31 | `257a66f2ff60e34ad6ae3fd1c30814b8` | `257a66f2ff60e34ad6ae3fd1c30814b8` | yes |
| `Reel 13 - a-score-is-not-a-fix` | 47 | `2c08fea0d8e92bad457b65d002d691e9` | `2c08fea0d8e92bad457b65d002d691e9` | yes |
| `Reel 14 - small-business-beats-the-behemoths` | 62 | `5f2f0b1705cc429f1b9c18addc827c4c` | `5f2f0b1705cc429f1b9c18addc827c4c` | yes |
| `Reel 15 - why-youtube-outranks-other-video` | 43 | `cdd047baf61949121246ad475a65521f` | `cdd047baf61949121246ad475a65521f` | yes |
| `Reel 16 - how-people-actually-search-now` | 55 | `ffcb7e91daaf3685dda3defbad898636` | `ffcb7e91daaf3685dda3defbad898636` | yes |
| `Reel 17 - four-slots-and-nothing-else` | 76 | `ea91352b972ad0e6a5a0811a65cba60e` | `ea91352b972ad0e6a5a0811a65cba60e` | yes |
| `Reel 18 - your-google-business-profile` | 58 | `0ef3d264b5bca4262307473eaad02f31` | `0ef3d264b5bca4262307473eaad02f31` | yes |
| `Reel 19 - can-you-game-ai` | 61 | `9b7d37f8d826c89263c9c1f7d61818c0` | `9b7d37f8d826c89263c9c1f7d61818c0` | yes |
```
before count 20   after count 20
CHANGED OR MISSING: none
ADDED: none
```

**20 timelines before, 20 after, all 20 byte-identical, none added.** This
is not evidence that a build was survived - it is evidence that no build was
attempted. The only write this task made anywhere near the project was an
empty `pipeline_output/steps/4_05_render_subtitles/` directory created by a
read-only path probe, which has been removed.

---

## 9. The gate

```
$ FULL_SUITE_GATE_PYTHON=/Users/prajwal/.treehouse/video_editing_pilot-9487f5/8/video_editing_pilot/.venv/bin/python \
    ./scripts/full_suite_gate.sh
exit=0
FULL-SUITE GATE: PASS  |  main: 4722 passed, 5 skipped  |  heavy_ml 2 passed, 0 skipped
```

Unqualified PASS, exit 0 - not `NARROWED PASS`, and `heavy_ml 2 passed, 0
skipped`, so no capability was missing. The two Resolve-driving test files
are named as deliberately not measured, which is the gate's own standing
exclusion and costs no coverage here.

**The first run of this gate FAILED**, 3 failed, and the three were the
`input_contract` disagreement described in §3 - my own manifest saying
`only_reels` was optional while my own step body raised without it. The
tests were doing exactly their job. The resolution was to make the
declaration TRUE in the direction that is true: `only_reels` **really is
optional** - absent means a full rebuild of every approved reel, which is
what every caller had, and the deletion guard holds unchanged on that path
(`test_a_full_rebuild_replaces_its_own_output_and_spares_an_orphan`). The
value's interpretation moved to its reader, `reel_build.reel_numbers`. No
test was silenced, no case was special-cased, and the guard was not softened.

Narrower selections run alongside it: 637 tests across the reel, operations,
DAG-contract, requirements, manifest and CLI families, and
`ruff check --config ruff-ci-gate.toml library/ tests/ manage_project.py` -
**All checks passed**.

---

## 10. Owed

`AGENTS.md` should carry a one-line headline for this rule under §5, pointing
at `library/tools/reel_build.py`. It does not, because
`scripts/check_agents_md_size.py` currently passes with **19 spare bytes**
against a ceiling that may only ratchet DOWN - so adding a line means
trading down another section's budget, and deciding which rule gets shorter
is a judgement above this task. The rule itself is stated with the code it
governs (`assert_deletion_scope`, quoting the captain's ruling verbatim),
pinned by a named test, and the incident is recorded in
`docs/RULE_EVIDENCE.md` under `the-build-that-deleted-nineteen-timelines-to-write-one`.
