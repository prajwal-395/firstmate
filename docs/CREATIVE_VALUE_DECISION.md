# How a creative value gets decided

> **STATUS: BUILT (2026-09-16).** `library/tools/decided_value.py` carries the rule;
> this file is its `[why]`. The design was gated and approved in phase 1 of
> `vep-how-a-creative-value-gets-decided`, and all three open questions in section 7
> were answered the way this document recommended. Section 8 records what was built and
> what is deliberately still open.

## The ruling this answers

The captain, 2026-09-16, across four replies, on nine constants that decide creative
outcomes:

> "i did not allow for any hardcoding when possible anywhere in the video editing
> pipeline -- these all stand as things that should be covered by creative reasoning by
> the LLM to see if music is sitting too loud, too soft, or just right. there is no hard
> coded number that hits this. perhaps a formula or some kind of audio anaysis that
> allows that judgement to be made is what im referring to"

> "these all seem hardcoded values that may not actually be able to generalize. it could
> very well be possible that we need to use values outside of these bounds but it also
> requires the LLM to be able to build the context and understanding, and then from there
> the reasoning to then be able to make those decisions and generalize them to what works
> for a video"

And on what a surviving default is allowed to mean:

> "the captions that are being rendered is the style that is preferred for the Lucie
> videos, this means that for another project they may not prefer that, so it is not
> something that should be hardcoded persay, but rather exist as a fallback if no
> preference is mentioned or there is no other way to see if something better works (like
> based on the video)"

That is a **precedence**, not nine answers: a stated preference wins; failing that the
project's creative direction; failing that the model reasoning over measured signal; and
only then a documented fallback that is visible AS a fallback.

## The defect this removes, stated once

The pipeline has **ten private `BASES` enumerations** - `cohesion_scope`,
`cutaway_window`, `explainer_plan`, `motion_graphics_plan`, `reel_look`,
`reel_semantic_visual`, `run_restart`, `speaker_identity`, `timeline_decisions`,
`vfx_plan_basis` - plus a further crop of one-off `*_basis` fields (`color_correction`'s
`correction_basis`, `music_behavior`'s `required_margin_basis`, `input_contract`'s and
`marker_routing`'s), each one a per-module answer to "why is this value what it is".
Every one was invented on the day its own incident landed. They are good; there are just
ten-plus of them, and none of them is asked the same question the same way.

So when a sixteenth creative value needs deciding, the cheapest correct-looking thing to
write is a constant, and the second cheapest is a sixteenth private ladder. That is how
this pipeline got nine constants that decide creative outcomes while passing a guard
written about the incidents it already had.

The mechanism is therefore **one ladder, one address space, one trace, one registry** -
and it is deliberately the generalisation of a shape the repository already proved
(step 5.01, below), not a new invention.

## The precedent it generalises

Step 5.01 `color_grade` is the whole mechanism done once, by hand, for one value. It was
DETERMINISTIC until 2026-09-03; it measured project 001's nine clips at a 2.7x luma
spread and answered with the identity CDL on all nine, because the only authority that
could act on the measurement was a brand template slot and 001 names no template. The
captain's words on that half:

> *"i think we need to still let the LLM understand it should try to add some color
> grading if it thinks it is needed rather than saying no completely bc of a lack of brand
> template (like this is another aspect of the creative reasoning i was talking about that
> the LLM should be able to handle)."*

The fix was not a different constant. 5.01 became HYBRID: its bridge measures and emits
`clip_exposure` / `cut_adjacency` / `grade_terms_legend`, its handoff asks a colourist,
its post-bridge composes, `why` is REQUIRED on every entry and an entry without one is
DROPPED, nothing is clamped, nothing is substituted for a term the answer left out, and
`correction_basis` records which of four things an ungraded run is.

Everything below is that, named and made reusable.

---

## 1. The unit: a SLOT

A creative value is addressed by a **slot** - one dotted key in one enumeration,
`decided_value.SLOTS`, owned by the module that owns the decision. Nothing else in the
pipeline may name a creative value.

A slot row declares, and a row missing any of these raises at import:

| field | what it is |
|---|---|
| `key` | `mix.speech_above_bed_db` - the address, spelled once |
| `question` | the words the model is asked. ONE copy, reused; N copies in N `handoff.md` files is N things to keep equal (`undetermined`'s own argument) |
| `units` | what the number or word MEANS. No bound, no default, no scale the engine offers (`motion_graphics_vocabulary`'s discipline: axes carry no values) |
| `measurements` | the named measurements that make the judgement possible, each naming the module and function that PRODUCES it. A slot with no measurement source cannot be REASONED, and says so rather than pretending |
| `deciding_step` | the DAG node id that decides it. MUST be in `undetermined.DECLARING_STEPS` - a deterministic step cannot decide a creative value, and a slot naming one raises |
| `readers` | who consumes the decided value. A slot nothing reads is refused (`output_contract`'s rule: a declared output has a READER) |
| `preference_paths` | where a project may STATE it. A per-user taste profile is the shared fallback within this same stated tier |
| `fallback` | optional `Fallback(value, whose, why, superseded_by)`. See rung 4 |

`SLOTS` is the registry. It is the thing the guard reads, the thing the run summary
prints from, and the thing a future constant has to pass through.

## 2. The ladder: FIVE readings, resolved in ONE place

`decided_value.decide(key, ...)` is the only implementation of the captain's precedence.
A step asks for a slot; a step never looks anything up itself. Five readings, spelled
differently on purpose, which is the line this repository already draws between an
admitted absence and a measured emptiness (`usable_ranges` `[]`/`unmeasured`,
`primary_subject_visible` None, `undetermined`'s three states):

1. **`STATED`** - the project said so, or the user's taste profile did. A project
   declaration in `pipeline.creative_preferences` or a brand template slot wins; when
   neither states this scoped value, the pipeline reads the user's explicit profile
   from `$XDG_CONFIG_HOME/ren/taste_profile.json` (normally
   `~/.config/ren/taste_profile.json`, or beside `$REN_CONFIG`). The record names
   the source, who stated the profile value and their reason. `ren taste set` records it;
   reading never creates or fills the profile.
2. **`DIRECTED`** - a value the creative direction really declared, read through
   `creative_direction.py`'s key enumeration (which raises on a key that cannot exist). A
   rule acting on a value the direction really declared is not a fallback - AGENTS.md
   10.5 says so explicitly - which is what makes this rung legal.
3. **`REASONED`** - the model answered THIS RUN, over measurements it was SHOWN. The
   record carries the model's own `why`, and the measurement rows it was shown. An answer
   with no `why` is DROPPED, not kept (5.01's rule, unchanged).
4. **`FALLBACK`** - nothing above answered, and the slot has a REGISTERED fallback. The
   record says it is a fallback, says **whose** preference it is, and says what would have
   superseded it.
5. **`UNDETERMINED`** - nothing above answered and there is no registered fallback. There
   is NO VALUE. The consumer drops with the reason or refuses. Never 0, never a stand-in,
   never a number that reads as a measurement.

Three properties of the ladder that are the whole point:

- **A rung that cannot be reached is SKIPPED with its reason recorded, not guessed
  through.** If rung 3's measurement was not taken - `bed["measured"] is False`, a hollow
  prosody profile, an unmeasured range - the model is not asked to reason about a number
  nobody measured, and the run says so. This is the `profile_defect` / `usable_ranges`
  line applied to a decision instead of to a file.
- **`STATED` is the person's number, not the engine's.** A project preference or user
  profile value exists only after someone states it. Neither is required to be filled in,
  and an absent preference falls to rung 3, never to a value sitting in a default config
  file. `ren taste set` requires the person, the value and why they stated it; no profile
  ships with the engine.
- **Nothing reads the ladder's OUTPUT to decide something else about taste.** `decide()`
  returns a value and a record. Whatever ranked, filtered or second-guessed the model's
  answer would become the chooser (AGENTS.md 10.5).

## 3. How the model gets asked, without every step inventing it

The fourth schema appender, beside `undetermined`, `direction_contradiction` and
`briefing_interview`, in `run_pipeline.present_llm_step` - the same route, deliberately
its twin rather than a second invention:

- A step's `manifest.json` declares `decides: ["mix.speech_above_bed_db"]` at the TOP
  LEVEL, where `context_fields` lives.
- The runner appends ONE schema entry - `value_decisions`, a list of
  `{slot, value, why}` - and ONE prompt block rendered FROM the slot rows: the question,
  the units, and the explicit statement that there is no bound and that nothing is
  substituted for an answer left out.
- The measurements reach the prompt by the routes that already exist - the step's own
  pre-bridge table, or a `view:` name in `context_views` - never by a new one.
- The answer is SPLIT OUT of the model's output before anything validates it, exactly as
  its three siblings are, so `validate_step_output` does not demand it and the step's
  declared outputs do not change shape.
- **A declared slot whose question the prompt does not carry FAILS on the same run**
  (`pipeline_skills.assert_declared_skills_reach_prompt`'s precedent). This is the
  `could_not_determine` escape - the field reached `prompt` and not `expected_schema`, and
  nine steps recorded the NON-ANSWER on every run in the one mode the pipeline runs in -
  refused in advance rather than rediscovered.

## 4. The trace: a value with no decision record is not a value

One `Decision` per slot per model ATTEMPT (attempts numbered, never deduped - the
`post_bridge_retry` evidence argument), carrying: slot, basis, value, source (the exact
path, or the model's `why`, or the fallback's owner), the measurements it was decided
over with who measured them, step id and attempt number.

Each record lands in four places, and each has a reader:

1. `state["value_decisions"]`, **MERGED never replaced** - a step this run answered
   replaces its own rows; a step it did not reach keeps them, marked
   `from_a_previous_run` so a carried row is never read as fresh (`undetermined`'s rule).
2. The deciding step's **own output**, so the spec carries the basis beside the value -
   `correction_basis` generalised.
3. The **run summary**, one line per decision, naming the basis.
4. `provenance` / `run_traceback` see it by virtue of living in state.

And `assert_decided(key, value)` REFUSES a slot value that arrives with no record -
fail-closed, the `reel_rebuild_need` discipline. A decision with no trace is how this
pipeline got into this state, so the absence of a trace is the failure, not a gap.

## 5. How a FUTURE constant gets caught

The guard is `tests/test_no_creative_floors.py`, which WP3b rebound on 2026-09-14 from
the incidents to the principle: shape 1 sweeps every creative `.get()` fallback under
`library/tools` + `library/steps` keyed by the KEY rather than the literal, with a
fail-closed `CREATIVE_GET_EXEMPTIONS` registry where each entry names a category; shape 2
fingerprints catalogue-order picks and fetch truncations; shape 3-of-three drives a sparse
plan through every declaring step's bridge.

The mechanism plugs into it at three points, each derived and each fail-closed:

1. **A new exemption category, `decided-value-fallback`, whose text is READ FROM the slot
   row** instead of typed beside the line. The existing `reported-load-bearing-default`
   (today: `timed_text_overlay.text_align "center"`) and `needs-captain` (today:
   `explainer_plan.type_role "supporting"`) entries become slot rows with an owner and a
   superseding condition, or they fail. An exemption naming a slot with no registered
   fallback fails as stale - both directions, as the registry already works.
2. **A derived sweep over `SLOTS` itself**: every `deciding_step` is in
   `DECLARING_STEPS`; every one declares its slot in its manifest; every slot has at least
   one reader; every slot with a `measurements` row names a producing function that
   exists. A new slot is covered whether or not anybody remembers to add it - the rule
   Surfaces A and D already live by.
3. **The meta-test discipline, unchanged**: each new check ships with the removed line
   that proves it fires, or it does not ship. A check nobody has seen fail is the
   gates-that-cannot-fail defect wearing coverage's clothes.

The forcing function is the important half. A constant in a creative path is caught by
shape 1 or shape 2, and **the only passing exit is to become a slot** - which costs a
question, a measurement with a named producer, a deciding step that reaches a model, a
reader, a trace, and, if it survives at all, a fallback with a named OWNER. Registering is
not free, and it is not supposed to be.

---

## 6. The proof: the music bed level

**Chosen because it is the captain's own worked example, the two measurements that make
the judgement possible are already taken on every run, and there is already a reader
waiting for the decided value with nothing to read.**

### What is there today

`music_behavior.MUSIC_BEHAVIORS` maps five words to five numbers - `prominent` -6,
`background` -18, `fade_in`/`fade_out` -12, `silent` -96 dB. Step 5.02 `audio_mix` is
DETERMINISTIC and turns the word into the number. `render_qa` P3 measures what the render
delivers, and `SPEECH_ABOVE_BED_GATES` is False with a rule beside it saying why:
`background` means a CLIP GAIN while the check reads it as a SEPARATION.

The pipeline already knows this is the wrong shape. `music_behavior` carries an EMPTY
`SEPARATION_TARGETS_DB` and a paragraph saying "the plan may now CARRY one, per
behaviour, and today none is declared". The hole is already cut. Nothing has ever filled
it because the number was assumed to be the captain's to pick, and the 2026-09-16 ruling
says it is not a number anybody picks.

### The removal

**The decided value is not the dB.** It is `mix.speech_above_bed_db` - how far the voice
should sit above the bed in THIS piece, per behaviour word. The clip gain stops being a
constant and becomes arithmetic over two measurements and one judgement:

```
gain_db = (speech_lufs - speech_above_bed_db) - bed_integrated_lufs
```

That is the captain's "formula or some kind of audio analysis that allows that judgement
to be made", exactly. Both measured terms already exist and already run on every
pass: `music_measurement.bed_reading` (the chosen bed's integrated LUFS, folded on by
2.04's post-bridge) and `speech_loudness.measure_speech_blocks` (one ffmpeg `loudnorm`
pass per block over the ranges `a_roll_assignments` names - about 0.23 s a block, 1.2 s
for 001's eight). Step 5.02 already computes `separation_delivered_db` from them and
compares it with nothing, because there has never been anything to compare it with.

Concretely:

- **5.02 becomes HYBRID**, on 5.01's precedent. `bridge.py` emits the measurement table -
  per window: the behaviour the spine planned, the bed's own loudness, the speech's
  measured loudness, and what separation the current gain would deliver.  `handoff.md`
  asks a mix engineer the slot's question. `post_bridge.py` solves the gain per window,
  writes `music_automation` and records the `Decision`.
- **The value the model names is a SEPARATION, not a gain.** A separation is a thing an
  ear can be asked about ("is the music sitting too loud, too soft, or just right"); a
  clip gain is not, because it depends on how the file was mastered. This is the
  distinction `music_behavior` and `audio_mix` both already state at length in prose and
  neither can act on.
- **`silent` is NOT decided.** -96 dB is the absence of music, not a level - the
  `CUT_TYPES` / `NEUTRAL_CDL` category. It stays, registered as absence-of-decoration.
- **`fade_in` / `fade_out` are not a third level.** They are a MOVE between two decided
  levels, so they resolve to their endpoints rather than to -12. That removes a number
  without deciding one.
- **The five dB constants survive only as a registered `Fallback`**, whose `whose` is
  "the mix the Lucie videos shipped at" and whose `superseded_by` is "a measured bed and
  measured speech, or a stated project preference". Every window that takes it records
  basis `FALLBACK`; `audio_mix_spec` says so; the run summary says so. On any run where
  the bed and the speech are measured - which is every real run - it is not reached.

This is the captain's caption ruling applied to the mix: not hardcoded, "but rather exist
as a fallback if no preference is mentioned or there is no other way to see if something
better works (like based on the video)".

### What it does NOT do

It does not flip `SPEECH_ABOVE_BED_GATES`. The rule beside that boolean says not to flip
it without changing one of its two conditions; this change changes one of them (the plan
now carries a real separation target), and promoting a report to a gate is still the
captain's call, so it is named as a follow-up and left alone.

### Why not one of the other four first

- **Track-length ceiling (10x / 600 s)** is cheaper - 2.04 is already hybrid - but it is
  a bound that REFUSES rather than a value that reaches the viewer, so it would exercise
  rungs 1, 2 and 5 and barely touch rung 3, which is the rung the ruling is about.
- **Caption `text_align`** is the one the captain has ALREADY ruled may survive as a
  fallback, so proving the mechanism on it proves the rung that was never in doubt.
- **Energy vocabulary** is a READING of a declared word, not a value decided from
  measurement.
- **Look-matcher clamps** are bounds to remove, not a value to decide - and that module
  has a separate correctness bug (a missing clip frame ships an identity CDL while the
  record says a grade was applied) that should not travel inside a mechanism PR.

Sequenced as follow-ups, in the order the mechanism makes cheapest:
`music.max_track_duration` (2.04, already hybrid) -> `caption.text_align` (fallback rung,
project preference) -> `look.match_bounds` (bounds removal, after the identity-CDL bug) ->
`direction.energy_vocabulary` (reading, needs its own ruling).

---

## 7. The three things firstmate has to rule on

1. **5.02 becomes hybrid, which adds one model call per run.** That is a real cost on the
   captain's machine and it is a scope decision above the worker. The alternative is to
   decide the separation at 2.05 `mesh_spine`, which is already hybrid - but the speech
   is not measured until 3.01, so the model there would be reasoning about a separation
   with only half the arithmetic in front of it. 5.02 is the honest place; 2.05 is the
   cheap one.
2. **Per BEHAVIOUR, not per window.** The recommendation is that the model names one
   separation per behaviour word, having been shown every window's measurements, because
   the behaviour vocabulary is what the spine already speaks and a per-window value would
   duplicate `mesh_spine`'s decision one level down. A per-window override is expressible
   later without changing the slot.
3. **The five dB numbers survive as a named fallback rather than being deleted.** A run
   with an unmeasured bed (2.04 recorded no `measurements`) otherwise has NO level, and
   the bed has to play at something or not play. The reading here is that "not play" is a
   worse answer than "the Lucie mix, recorded as a fallback nobody chose for this
   project". If firstmate reads the ruling as "delete them and refuse the render", that is
   a one-line change to this design and the mechanism is unaffected.


---

## 8. What was built, and what is still open

### Built

- **`library/tools/decided_value.py`** - the registry (`SLOTS`), the five readings, the
  one ladder (`decide`), the solver contract, the trace (`Decision`, `merge_records`,
  `summary_lines`) and the fail-closed `assert_decided`. The registry checks itself at
  import: a slot decided by a step that reaches no model, a fallback nobody owns, a unit
  conversion with no registered formula, or a value nothing reads all raise there.
- **The fourth schema appender**, in `run_pipeline.present_llm_step`, beside
  `undetermined` / `direction_contradiction` / `briefing_interview`. The answer is split
  out before validation like its siblings, and - because it is load-bearing, unlike
  them - `run_hybrid_step` hands it to the post-bridge under `decided_value.MERGE_KEY`,
  the way `second_pass.PASS_KEY` already travels. Mirrored in
  `replay_bench/reconstruct.py` and `bench.py`, which is a gate.
- **Step 5.02 is HYBRID.** `bridge.py` measures the bed and the speech once and emits
  `bed_measurements` / `mix_windows` / `mix_decision_legend`; `handoff.md` asks the mix
  engineer; `post_bridge.py` puts the answer through the ladder, solves the gain and
  records the decision; `mix.py` is the half both halves share. `step.py` is gone.
  `audio_mix` joined `undetermined.DECLARING_STEPS` and `craft_role.ROLES` (mix engineer)
  by the same derivation that added `color_grade` in 2026-09-03.
- **The five dB are gone from `music_behavior`.** The vocabulary keeps its five words and
  carries no numbers. `DECIDED_BEHAVIORS` is the two whose level is decided;
  `SILENT_LEVEL_DB` is the absence of music; `level_for_block` resolves a fade to the
  level of the block it moves TO, and a fade that ends the piece ends in silence.
  `WITHDRAWN_LEVELS_DB` records each old number and where it went.
- **The reader that had nothing to read.** `render_qa.measure_speech_above_bed` has always
  looked for `music_automation[].separation_target_db` and always found None, so it judged
  against the clip gain and said so. The decided separation is what lands there now.
- **The guard sees it.** `tests/test_no_creative_floors.py` gained shape 4: four checks
  derived from `SLOTS` (model-reaching decider, manifest declaration, reader plus a
  measurement whose producer really exists, fallback with a named owner), each with the
  removed line that proves it fires, plus `audio_mix`'s two sparse drivers - one for the
  empty answer, one proving nothing is clamped.

### The speech reference for `prominent`, which the design did not foresee

`prominent` means music leads with no competing speech, so its windows usually carry none -
and a scope with nothing to measure against would have fallen to the fallback on every run,
which is the constant surviving in a new place. Its reference is therefore the loudest
speech in the PIECE: where the bed should sit when it leads, against the voice the rest of
the video is carried by. Still a measurement of this material, and
`measurements.speech_reference_basis` records which reading was used rather than leaving a
reader to assume.

### Deliberately still open

- **`SPEECH_ABOVE_BED_GATES` stays False.** The rule beside that boolean says not to flip it
  without changing one of its two conditions. This changes one of them - the plan now carries
  a real separation target rather than a clip gain read as one - and promoting a report to a
  gate is the captain's call, not a consequence.
- **Nothing checks whether the delivered mix actually sounds right.** The separation is
  decided and measured, and no loop closes between what was decided and what the render
  delivers. That is the captain's "evaluate and adjust its work until it gets it right", it
  belongs to the creative-reasoning-fidelity work being scoped separately, and it is named
  here so it is not mistaken for an oversight and not solved twice.
- **`fade_duration_seconds: 1.0`** in `TRACK_LEVELS` is how long the ramp between two levels
  is. It is arguably the same class of value and it was not in scope; it is named here rather
  than quietly left.
- **The four other constants**, in the order the mechanism makes cheapest:
  `music.max_track_duration` (2.04, already hybrid) -> `caption.text_align` (the fallback
  rung, plus a project preference) -> `look.match_bounds` (bounds removal, after the
  identity-CDL correctness bug) -> `direction.energy_vocabulary` (a reading, needs its own
  ruling).
