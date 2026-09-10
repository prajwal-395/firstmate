# Decision queue vs the repo: what the 29 already have covered (2026-09-08)

A crewmate pass over the open video-pipeline decisions, verified against what
the merges actually did. Nothing was closed by this pass; firstmate routes
closures with the captain's word.

## What "the 29" is, exactly

`tasks-axi` backlog records with `vep-` ids, kind `captain`, hold `captain`
(genuinely awaiting the captain, not parked-`future`), `*-decision-*` ids,
created before 2026-09-08. That filter yields exactly 29. Five more records
in the same class were created today (covered in a short section below, not
in the 29). Parked-`future` items, the two flush goals, three non-decision
captain records and five ship/scout records are listed in the appendix with
one-line dispositions, untouched.

## Outcome counts (the 29)

- ALREADY ANSWERED: 3 (rows 1, 10, 8)
- PARTLY ANSWERED: 6 (rows 5, 22, 29, 12, 14, 19)
- STILL GENUINELY THEIRS: 19 (all others except row 6)
- Row 6 (chroma-floor premise) carries the clarified question the captain
  asked for, and no outcome.

HEAD verified at `a553ea4` (2026-09-08). All file:line citations are at
that commit.

## The rows

### 1. `field-test-blocked-on-planner-refusals` - ALREADY ANSWERED
Evidence: commit `69607b2`, PR #484, "Two refusals that killed a run over a
correct plan". Its message: "a legitimate plan met a refusal where the
answer was a drop, or nothing at all", covering both the VFX-on-B-roll
refusal (`2 VFX entries do not overlap any V1 clip` removed; comps now
reach V1 AND V2 via `library/tools/vfx_carriers.py`) and the A3 refusal
(narrowed to `_assert_no_doubled_sound` in
`library/steps/step_5_04_compile_manifest/step.py:212`, which refuses only
the same file at the same point over the same span and states "Layering
two DIFFERENT sounds here is fine").
Reason: both run-fatal defects the field test was to wait on are fixed,
so there is nothing left to wait on.

### 2. `master-limiter-preset` - STILL THEIRS
Evidence: `library/steps/step_6_01_render/resolve_build_timeline.py:1504-1560`
still probes `resolve.GetFairlightPresets()` for a preset named
`Pipeline_Master_Limiter` and falls back to the marker when it is absent;
`library/tools/fairlight_presets.py` holds engine-side chains only, not a
Resolve-side preset.
Reason: the settling experiment is the captain creating the preset in the
Fairlight page, which no merge can do for them.

### 3. `lucie-brand-assets-are-not-in-the-project` - STILL THEIRS
Evidence: `library/templates/lucie_client.yaml:12-18,67` still declares
`compositions/LucieLogoAnimation.tsx` and `compositions/LucieEndCard.tsx`
as project-side assets; the merged resolver handles flat-file-or-directory
shape, not absence.
Reason: getting the brand animations into a lucie_client project (copy,
reference, or re-point) is an authorization over the captain's project
store.

### 4. `mix-levels-need-an-owner` - PARTLY ANSWERED
Evidence changed: the hardcoded SFX track levels (-12/-10 "from style
spec") are WITHDRAWN - `library/steps/step_5_02_audio_mix/step.py:62-91`
now takes per-sound `volume_db` from the plan and records the withdrawal
in `library/tools/sfx_level.py`; both halves of the separation arithmetic
are measured (`library/tools/music_measurement.py`,
`library/tools/sfx_envelope.py` lineage, `speech_loudness.py`).
Evidence unchanged: the five clip gains still sit in
`library/tools/music_behavior.py:60-66` (-6/-18/-12/-12/-96),
`SEPARATION_TARGETS_DB` is EMPTY, and step 5.02 states "What no step
supplies is the separation a window OUGHT to deliver".
Reason: the engine stopped pretending the numbers were a style spec and
now names them as the open decision, but who declares them (template,
project, or plan per block) is still unanswered.

### 5. `sfx-works-when-is-binding` - STILL THEIRS
Evidence: `library/steps/step_4_04_plan_sfx/handoff.md` (frozen) and
`bridge.py:46` carry `works_when`/`avoid_when` with no statement of force;
`library/tools/sfx_library.py` documents the catalogue, not the reading.
Reason: nothing in the repo says which field binds, so the planner's
split reading (thematic use of one, strict use of the other) is still
unruled.

### 6. `craft-chroma-floor-premise` - CLARIFIED QUESTION (no outcome)
See the clarification section below.

### 7. `cutaway-ambient-audio` - STILL THEIRS
Evidence: `library/steps/step_5_04_compile_manifest/step.py:1609,1632`
still write `"video_only": True` unconditionally on every B-roll
assignment and interjection (same code the record cited at :1347/:1369,
lines moved); `library/steps/step_3_02_select_broll/post_bridge.py:275`
still carries "B-roll audio should NOT be linked".
Reason: the brief line vs the code stands exactly as filed; which wins is
a creative call about what the viewer hears.

### 8. `caption-conformance-is-vacuous` - ALREADY ANSWERED
Evidence: `library/tools/reel_conformance_verifier.py:4205`
`_derive_planned_captions` runs step 4.01's own rule through the reel's
spine ("a reel cannot be built to one rule and checked against another"),
which is the exact option the record offered; and the `NO-REFERENCE`
class (:178-196) refuses F2/F5/F6/F7 when the expected side is empty
instead of passing green. Landed via PR #559 (`890a61b`).
Reason: expected cards are now derived, and the vacuous pass is now a
refusal.

### 9. `caption-render-startup-cost` - STILL THEIRS
Evidence: `library/steps/step_4_05_render_subtitles/step.py:204` still
documents "Today's mechanism: one `npx remotion render` process per card";
`library/tools/remotion_batch.py` (bundle-once, `render-batch.mjs`
bundles once and renders every card through one browser) has no
production caller - only `tests/test_remotion_batch.py` imports it; PR
#681 pinned fan-out, not the startup term, and #707 reuses unchanged
captions (fewer cards, same per-card cost).
Reason: the proposed real fix exists unwired; the 763 bundle-and-launch
dominance the record measured is unrefuted.

### 10. `reel-capable-shape` - ALREADY ANSWERED
Evidence: `library/tools/reel_spine.py` is the producer the shape report
asked for (emits all five spine keys, `alignment_method` at :549, mic
bleed handled per PR #575); `library/tools/reel_build.py:1018` consumes
`spine_for_reel`; `library/steps/step_7_01_build_reels/step.py:27-28`
drives `subtitles.plan` and `subtitles.render_segment` through the
operation registry ("the caption path a reel takes is the caption path
the master takes"); `tests/test_operations_add_no_second_implementation.py`
pins one implementation, not two.
Reason: caption steps are reel-capable via a spine producer with no
parallel implementation, which is what the record asked firstmate to
verify before closing.

### 11. `stale-ranking-instruction` - STILL THEIRS
Evidence: `library/steps/step_2_02_speech_sequence/handoff.md:38` still
says "Prioritize passages from clips with higher interest_scores", while
`interest_score` appears nowhere in 2.01/2.02 `manifest.json`
`context_fields` or `library/tools/context_views.py`; handoffs are frozen
by policy.
Reason: the step is still told to do something impossible on every run,
and only the captain can rule on frozen wording.

### 12. `information-layer-ordering` - PARTLY ANSWERED
Evidence changed: the ingest-first case the report made has largely been
built since - usable ranges are measured and absent-means-empty (PR #248,
`2abeea9`), span points union action windows (13 of 17 clips now carry
2+ spans), and cutaway windows come from the picture, not muted audio
(PR #322, `53c8c00`).
Reason: the ingest lead the report proposed is substantially landed, so
what remains is sequencing the leftover plumbing items, not the
ingest-vs-plumbing fork.

### 13. `picture-standard-calibration` - STILL THEIRS
Evidence: `library/tools/perceptual_qa.py` still gated behind
`PIPELINE_PERCEPTUAL_QA=1` (`resolve_build_timeline.py:1966`); the review
channel still has no 001 notes in-repo; seven craft variants were
delivered 2026-09-08 per the hand-cut record, so the sitting now has
objects to judge.
Reason: the ask (one sitting, agree/disagree plus three anchored notes)
still needs the captain's eye; the variants only make it concrete.

### 14. `shared-craft-doctrine` - PARTLY ANSWERED
Evidence changed: `library/tools/craft_role.py:789` `prompt_block` is now
prepended to every LLM step's prompt (`run_pipeline.py:1727`) - the
per-step role half the report said was established is enforced in code.
Evidence unchanged: zero handoffs mention retention (verified
`rg -l retention library/steps/*/handoff.md` returns nothing), first
three seconds, or boring; no shared standard for a good shortform video
exists anywhere.
Reason: roles are now engine-enforced, but the doctrine text (write,
decline, or delegate a skeleton) is still the captain's, since handoff
wording is reserved to them.

### 15. `llm-backend-fork` - STILL THEIRS
Evidence: `present_llm_step` (`run_pipeline.py:1546`) still serves both
routes - `full_auto == "agy"` pull-style with `project_folder`
(:1836-1841) and the default `api` one-completion path
(`PIPELINE_LLM_PROVIDER` default `gemini`, model `gemini-2.5-flash`,
:1914-1915); PR #659 routed project tasks through the runner, which is
plumbing, not the fork.
Reason: committing to agy (reproducibility cost) or to api (implementing
function calling) is a risk-appetite call with no new measurement.

### 16. `render-qa-llm-role` - STILL THEIRS
Evidence: 6.01's handoff still declares Deterministic while the runner
still spends the hybrid call (`step.py:170` reads `visual_qa` from the
LLM result); the 28 frame grabs / 10 segment checks remain behind
`PIPELINE_PERCEPTUAL_QA=1`; the three options (drop, wire frames in,
keep as sanity reader) are all still open and unmeasured.
Reason: the shape the record describes is byte-for-byte the current
shape.

### 17. `captain-checkout-staged-reversal` - STILL THEIRS
Evidence: the record's proof (index tree equals PR 501, 16 files of
staged reversions, everything recoverable from origin/main) is about the
captain's own directory; no repo merge touches it, and recovery discards
state in their directory.
Reason: needs their explicit word by construction. Their checkout was
not entered for this pass.

### 18. `subtitle-default-size` - STILL THEIRS
Evidence: `library/tools/subtitle_style.py:46-51,69` records the
captain's ~85px Text+ reference and explicitly moves none of the four
sizes (192/160/144/120); `LEGACY_FONT_SIZE = 160` still renders (:82).
Reason: the engine deliberately declined to take this number - per the
standing taste rule that makes the decision live, not answered - and the
cadence consequence (cards hold ~2x words at ~85px) still needs weighing.

### 19. `buy-go-seat-overflow-lane` - PARTLY ANSWERED
Evidence: the record's own 2026-09-07 update answers one of three
unknowns without a seat (burst at 3+ concurrent workers: zero
rate-limits over 2.38M fresh tokens across 28 sessions) and corrects the
volume estimate down (1,400-2,400 tasks/month, not ~4,321).
Reason: two unknowns remain seat-only (metered-or-free status, cached-read
rate - the latter now the valuable one since cache read dominates 47:1),
and purchase plus spend are still the captain's.

### 20. `delivery-substrate-routing` - STILL THEIRS
Evidence: `speech_cue`/`body_language` are measured into v3 action
windows (`vision_schema_adapter.py:232-249`) but `context_views.py:238`
deliberately drops `body_language` from the picture rows ("restates the
same moment... costs 2.4x the bytes of `visual`"); no marked-VLM-reading
framing exists anywhere.
Reason: the observables reach nobody by design, and routing them as
marked readings against the objectivity posture is still an unmade call.

### 21. `live-affect-probe` - STILL THEIRS
Evidence: the scout report (`data/vep-probe-vision-affect-capability/report.md:35`)
states reliability "could NOT be established here"; the probe needs a GPU
vision pass with human labels, i.e. machine availability plus
authorization.
Reason: a measurement only the captain can authorize and schedule.

### 22. `delivery-view-successor` - PARTLY ANSWERED
Evidence changed: PR #417 (`881dec5`) re-wired step 1.05 into the DAG
with output to 2.01 and 2.02, and both manifests now declare
`view:prosody` - the "scrap prosody" half of the question dissolved, and
the routing half is restored for two of the four steps.
Evidence unchanged: `view:prosody` carries per-clip profiles, not the
per-block/per-passage delivery columns the report specified, and 2.05 /
3.03 declare nothing delivery-shaped.
Reason: the remaining question is narrowed to whether 2.05/3.03 need
per-block columns; note `context_views.py:58` still claims the view "has
NO consumer", which is stale since the re-wire (flagged, not fixed -
out of scope for this pass).

### 23. `craft-atmospheric-sfx` - STILL THEIRS
Evidence: the mechanism exists (risers in the catalogue, e.g.
`riser_2.mp3`; `swelling` envelope placement in
`step_4_04_plan_sfx/post_bridge.py:288`), but nothing plans atmospheric
layering on its own and 001 ships literal SFX only.
Reason: introducing it is taste about the video's sound, not a missing
capability.

### 24. `craft-j-cuts` - STILL THEIRS
Evidence: a swelling sound's start is placed at peak-minus-duration
(`post_bridge.py:288-293`), so sound-before-picture is mechanically
possible, but nothing plans an anticipatory swell and 001 anchors SFX to
cut boundaries.
Reason: allowing it as grammar is a style call, and the capability
existing does not make the choice.

### 25. `craft-kinetic-motion` - STILL THEIRS
Evidence: `zoom_emphasis` / `slow_zoom_in` / `slow_zoom_out` render
(`fusion/effects.py:143` Ken Burns), but `inject_default_ken_burns` was
removed - `step_4_03_plan_vfx/post_bridge.py:100,199` drops entries
naming no effect rather than defaulting to motion.
Reason: the engine deliberately adds no motion uninvited; constant
motion on static shots is taste.

### 26. `mg-authoring-pattern` - STILL THEIRS
Evidence: `library/tools/motion_graphics_vocabulary.py:75-77,1133-1138`
records the fork (`COPY_SOURCE_IS_UNSET`) and states a change forcing
either answer is a stop, not an implication; current machinery
(`generate_motion_props` to props file) is props-shaped but unauthorized
as the answer.
Reason: the biggest architectural fork is explicitly reserved, and the
file refuses to take it by drift.

### 27. `resolve-contention-strategy` - STILL THEIRS
Evidence: `library/tools/run_control.py` owns pid/hold/run-status only;
no cooperative lock, currency assertion, or headless scheduling exists;
options A/B/C all spend the captain's own review time or a project.
Reason: only the captain can decide what of their Resolve time to give
up.

### 28. `twelve-labs-footage-upload` - STILL THEIRS
Evidence: zero references to Twelve Labs anywhere under `library/`; the
record's terms analysis (section 4a training licence, ~$2 indexing cost)
is unmoved by any merge.
Reason: the choice is the licence, not the price or the engineering.

### 29. `anchor-input-versioning` - PARTLY ANSWERED
Evidence changed: the resolution merge now reads a real rate
(`cutaway_window.py:300-311` `index_resolution_seconds` from
`sample_rate_hz`), and span inputs are richer (action-window union, PR
#322) - the merge-logic half of the complaint moved.
Evidence unchanged: the emitted anchor map carries no fingerprint of the
vision-document state that produced it, so a re-run can still choose
from a different window map without saying so.
Reason: what remains is versioning the inputs beside the map; the anchor
logic itself was repaired.

## Clarified question: chroma-floor premise (row 6, no outcome per instruction)

What is actually being asked: the value question (p99 chroma >= 60 in 90%
of samples) is dead - the captain's own craft reference refutes it - so
the live question is whether ANY colour floor should exist, i.e. whether
a render should ever fail for being grey, and at what number.

Evidence for a floor: the shape is settled by measurement (p99 of
per-pixel chroma over lit pixels, `measure_chroma_presence` in
`library/tools/render_qa.py:1585`), which replaced the bad statistic
(frame-mean saturation, `min_sat: 10`, now removed -
`tests/test_baseline_craft_properties.py:518,541`); a deliberately
monochrome accident arguably deserves a refusal.

Evidence against any floor: the Gawx Art reference the captain chose as
good editing has median p99 18.4 with 98.6% of runtime below 60 and
92.1% below 40, while 001 (found inadequate) measures 31.2 - the
reference is 41% LESS colourful on the proposed statistic, and any value
passing the reference passes every frame of 001, so no floor
discriminates; three measured points (18.4, 61.5, 85.3) do not describe
one population.

Current engine posture (safe either way): `CHROMA_PRESENCE_GATES = False`
(`render_qa.py:576`), P2 reports and never fails, `chroma_floor`
defaults to null. Two hygiene findings, not fixes: the code comment at
`render_qa.py:569-576` names decision id
`vep-craft-reference-decomposition-decision-craft-chroma-floor-value`,
which no longer exists in the backlog (NOT_FOUND - likely one of the six
ruled stale); and the removed `min_sat` gate is fully gone.

Clarified question for the captain: "Your reference for good editing
fails every candidate colour floor including the removed one - is greyness
something this pipeline should ever refuse a render for, and if so, what
is it protecting: accidents (grey through failure) or taste (grey as a
look you did not choose)? A number cannot be set until that is answered,
because any number that passes your reference passes 001 too."

## Five records created today (same class, not in the 29)

- `muse-hosted-video-comparison` - STILL THEIRS. Needs a Meta API key,
  sending 3 clips of 001 footage to Meta, and token spend. Fresh; nothing
  to verify against.
- `reel-5-gate-failure` - STILL THEIRS, narrowed. Reel 05 was rebuilt in
  place and its caption defects are gone, but the gate refused it again,
  now for QB-CTA-ABSENT: the approved plan declares `call_to_action:
   null` and the bar demands a closer (`data/vep-rebuild-nineteen/report.md`
   in the firstmate home via #715). The ask is now: approve a closing passage (then rebuild that
  reel alone), or leave the approved timeline, which stands untouched.
- `grounding-choice` - STILL THEIRS. Face-seeded grading needs no choice;
  generic objects (laptop) still need one of three routes. Nothing landed
  on generic-object grounding.
- `scope-first-build` - STILL THEIRS, narrowed. PR #690 (`6401985`,
  today) proves face-box seeding (SAM 2 box prompt from recorded
  `face_boxes`, one deliberate target, honest decline otherwise), so the
  technical premise is measured; the greenlight for 2fps-matte subject
  grading via EffectMask remains the captain's.
- `renderer-seam-hyperframes` - STILL THEIRS. Scout recommends leaving
  the Remotion coupling alone until a graphic exists Remotion cannot
  draw; adopting either tool is spend plus direction.

## Appendix: everything else open in the pipeline queue, one line each

Parked-`future` (captain's 2026-08-25 style-layer and 2026-09-04 001
orderings; explicitly not to surface; left untouched):
`motion-graphics-content`, `one-off-title-card`, `music-bed-reversal-001`,
`confession-ending-stands`, `caption-animation-grammar`,
`which-rules-bind-001`, `motion-graphic-copy-source`,
`cut-15-or-cut-8-on-001`, `sfx-library-fits-a-different-video`,
`house-look-grain-never-chosen` (plus ship-kind `narrative-palette-reconcile`).

Non-decision captain records: `framing-ratio-floor-costs-a-pan` (taste:
floor working as designed, lowering it trades safety for coverage),
`required-creative-direction-blocks-a-lean-run` (product contract: eleven
manifests declare the edge required), `flush-the-pipeline-goal` and
`flush-pipeline-goal-2` (goals, not decisions).

Ship/scout records also waiting on the captain: `audit-report-partly-stale`
(process: date findings before dispatch), `captain-checkout-stuck` and
`captain-checkout-staged-reversal` (their checkout, their word),
`craft-hand-cut-reference` (their hand cut; 7 variants delivered
2026-09-08), `replan-on-a-real-api-backend` (needs their API key),
`twelve-labs-single-clip-eval` (needs their clip choice).

## Candidates this pass considered closing and rejected

1. `caption-render-startup-cost` via `library/tools/remotion_batch.py` -
   rejected: the module implements exactly the proposed fix but has no
   production caller (only the test imports it), while step 4.05 still
   documents per-card `npx` as today's mechanism. A title that sounds
   related; the merge did not wire it.
2. `mix-levels-need-an-owner` via the withdrawn SFX levels - rejected as
   a full closure: the style-spec fiction is gone but the five gains and
   the empty separation target remain, so PARTLY, not answered.
3. `delivery-substrate-routing` via `view:picture` reaching 2.02 -
   rejected: `body_language`/`speech_cue` are deliberately dropped from
   the routed rows, so the asked-for routing did not land.

## Done-check

No code changed in this pass (report document only), so per the captain's
test economy rule no test suite was run. Narrowest check proving the
change is exactly the deliverable and nothing else:

`git status --porcelain` shows only the new report file; `git diff --stat`
against the merged tree is empty of code.

```
$ git status --porcelain
?? docs/DECISION_QUEUE_2026-09-08.md
```
