# plan_vfx (step 4.03) - reasoning

Written before the answer. Request: `pipeline_output/llm_requests/plan_vfx.json`
(prompt 6,078 chars; context 43,758 chars; constraints 41 chars - `VFX Intensity: 0.5`).

**The answer is an empty list.** That is the whole decision, so the reasoning is about why,
and about what I found while getting there.

## 1. What I actually read

- **`vfx_candidates_toon`, all 11 rows.** This is the table the prompt names as the data to
  decide from. Every row's `text` column is **empty**, and every row's `vfx_suggested`
  column reads **`No`**. Eleven rows, eleven blanks, eleven `No`s.
- **`enhancement_spec`** as it arrived in the context, carrying one pre-existing effect:
  `color_wash, 0.5, hook`.
- **`timed_spine`** - block durations, which is what I actually decided on.
- **`creative_direction`** - for the restraint question.
- **The camera metadata I tabulated at 2.01** (`mode`/`framing`/`stability`/`movement` per
  clip). This is not in this step's context in usable form, and it is the information the
  decision turns on, so I used my own earlier table. Declaring it.

**Not used:** `a_roll_assignments`, `b_roll_assignments`, `semantic_analysis_documents`,
`rough_cut_review` - all present again, none of them able to answer "should this shot move".

## 2. What I found in the bridge, and why it matters

`vfx_candidates_toon` looked wrong - a column of eleven identical `No`s and eleven empty
strings is not a measurement - so I read `library/steps/step_4_03_plan_vfx/bridge.py`. It
is an admitted stub. Its own comment says:

> `# In a real implementation we would look at semantic_analysis and timed_spine.`
> `# Here we just pass an empty list or basic summary to the LLM.`

Three specific consequences, all visible in what I was sent:

1. **`vfx_suggested` is the hardcoded string `"No"`.** The prompt describes this field as a
   "pre-computed suggestion on whether VFX are needed **based on motion/pose data**". It is
   not based on anything - the bridge writes `"vfx_suggested": "No"` literally, for every
   row. Had I trusted the field as documented, I would have reached the right answer for
   entirely the wrong reason, and I would have reached the same answer on a piece that
   badly needed VFX.
2. **`text` is always empty.** The bridge reads `slot.get("text", "")[:50]` off each A-roll
   assignment, and A-roll assignment rows carry no `text` key - their fields are
   `block_type`, `duration_frames`, `spine_block_position`, the timeline bounds and
   `video_segments`. So the column is structurally always blank. The one table built for
   this step tells me nothing about what any segment says.
3. **The bridge injects a `color_wash` at intensity 0.5 on the first segment**, unasked.
   `color_wash` is not in this step's own permitted toolkit (`slow_zoom_in`,
   `slow_zoom_out`, `zoom_emphasis`, `screen_shake`, `cut_in`, plus named Resolve builtins),
   and grepping `library/tools/fusion/` and `library/tools/execution/` finds nothing that
   draws it.

**How bad is (3)?** Less bad than it looks, and I checked rather than assumed. The
post-bridge rebuilds its output purely from `vfx_creative`
(`output = {"enhancement_spec": {"visual_effects": result}}` where `result =
resolve_vfx(creative, ...)`), so the bridge's `color_wash` is discarded and never reaches
the manifest. What it does reach is **the prompt**: the model is shown a pre-existing
coloured wash on its hook as though something had decided on one. That is a bridge stating
a creative value that no step chose - the shape AGENTS.md 10.5 describes as "a floor in a
BRIDGE is a floor, and that is where the last one hid". It cannot pad the output the way the
old VFX floor did, but it can anchor the answer, and an agent inclined to agree with its
context would keep it.

I mention it because I nearly did keep it. A coloured wash over the cold open is a plausible
idea, and it arrived looking like a decision someone had already made.

## 3. The actual decision

The prompt gives me the criterion in its own words: *"A static talking-head shot held for a
long time is where `slow_zoom_in` / `slow_zoom_out` earns its place - use it where it helps
and leave it off where it does not."* So the eligible set is blocks that are **long** and
**static**. Applying that to the eleven blocks:

| block | dur | clip | camera | eligible? |
|---|---|---|---|---|
| 7 | **15.99s** | clip_011 | selfie, **tilting_up**, **shaky** | long, but the frame is already moving |
| 14 | **8.67s** | clip_017 | mounted, **stationary**, **stable** | long AND static - the only one |
| hook, 2, 3, 5, 9, 10, 11, 12, 15 | 1.46-3.54s | - | - | too short for a slow zoom to read at all |

Nine of eleven blocks are between 1.46s and 3.54s. A slow zoom needs seconds to become
perceptible; on a 1.46-second card it is a scale change, not a drift. So the real choice was
between exactly two blocks.

**Block 7 (16s): rejected.** It is the long static hold the rule was written for, except it
is not static. clip_011's camera is `tilting_up` and its stability is `shaky` - handheld
selfie, walking. The frame is already moving more than a slow zoom would move it, and
adding a zoom to shaky handheld tends to make the shake more visible rather than less. It
also already has two B-roll interjections cut into it, which is the relief it actually
needed.

**Block 14 (8.67s, the climax): rejected, and this is the interesting one.** It is the only
genuinely static shot in the edit - mounted, stationary, stable, medium framing - held for
8.67 seconds. On the prompt's stated criterion it is not merely eligible, it is the textbook
case, and if I were applying the rule mechanically it would get a `slow_zoom_in`.

I left it alone because **I have already decided twice that nothing decorates this block.**
At 2.05 I set its `music_behavior` to `silent` - the only silent block in the piece - on the
grounds that the rawest eight seconds in thirteen minutes of footage should be undecorated.
At 3.02 I declined to put a cutaway over it, writing "talking head, no cutaway, no effect -
let it be plain". Adding a zoom now would contradict both, and it would specifically undo
the silence's work: the point of pulling the music is that nothing is doing anything, and a
drifting frame is something doing something.

Consistency across steps is worth more here than obedience to a per-step rule.

**A third, independent reason to be sparing, which applies to every A-roll block.** All
three speech clips are true 1920x1080 landscape (rotation 0) being conformed into a
1080x1920 portrait delivery frame. Filling that frame needs roughly a 3.16x zoom relative to
fit - about 31.6% of the source width - so the A-roll is already substantially blown up
before any effect is applied. A `slow_zoom_in` on top pushes further into an image that is
already soft. This is not decisive on its own (the backdrop route may hold some clips below
full fill zoom) but it means VFX here costs resolution in a way it would not on the B-roll.

**So: nothing is eligible once the criteria are applied honestly, and the two blocks that
come closest each have a specific reason not to.** The prompt says in as many words that an
empty list is a legitimate answer for a piece that wants stillness, and the captain's ruling
of 2026-08-20 removed the floors that used to make this answer impossible. This is a
sixty-second piece about someone too self-conscious to be filmed. It wants stillness.

## 4. What I considered and rejected beyond the zoom question

- **`screen_shake`** on the climax or on "even if it's bad, even if i hate it, i will post
  it". Rejected outright: it is an impact effect, and this piece has no impacts. The
  direction says anything hard-hitting fights the footage.
- **`zoom_emphasis`** on the final line. Genuinely tempting - it is the strongest sentence
  in the material and "punches in and settles back" is what you would do to it in a punchier
  edit. Rejected because block 15 is 2.71 seconds; a punch-in plus a settle inside 2.7s is a
  gesture, and the direction asks the ending to be flat and certain rather than emphasised.
- **The lens flares and Resolve builtins** (`lens_flare_v11` through `v40`,
  `chromatic_aberration`, `posterize`, `chrome`, `cloth`, `3d_tube_maker`). Nothing here
  wants a lens flare.
- **Generator overlays** (`rain`, `snow`, `embers`, `fireworks`, `matrix`). No.
- **Keeping the bridge's `color_wash`** - section 2.

## 5. What I was missing

- **Everything `vfx_candidates_toon` was supposed to give me.** Both of its informative
  columns are structurally empty (section 2). The decision above was made from block
  durations in `timed_spine` plus a camera table I built two steps ago, because the table
  built for this step carries no signal.
- **Motion/pose data.** The prompt says `vfx_suggested` is computed from it. It is not
  computed from anything, and the underlying motion energy that step 1.04 measures is not
  routed here in any form. Whether a shot is "already dynamic" - which the prompt's own
  error-handling table names as a reason to skip effects - is exactly what motion data would
  answer, and I answered it from a `stability` and `movement` label instead.
- **Sight of the frames**, again. Whether clip_017's static medium shot actually feels dead
  over 8.67 seconds is a question you answer by watching it.
- **A creative brief.** Ninth step. The prompt says a brief would tell me "how visually
  restrained or energetic the final video should feel", which is the entire question at
  this step.

## 6. Confidence

**High that empty is defensible**, and I want to be careful about the difference between
that and "high that empty is right". Every block fails the prompt's own eligibility test for
a stated reason, and the two nearest misses have specific arguments against them. Nothing
here is an evasion of the question.

**Medium that empty is right.** The honest counter-argument: shortform convention is that
the frame is always moving, the brand template asks for `vfx_intensity: 0.5` and
`energy_profile: high`, and a sixty-second edit with zero VFX may simply read as flat on a
phone. I overruled the brand's energy profile at 2.01 with reasons and I am consistent with
that here, but consistency with my own earlier call is not the same as being correct.

**What would change my answer:** watching block 14. If the mounted shot dies over 8.67
seconds, a `subtle` `slow_zoom_in` there is the single change to make - and it would mean
overriding the "no decoration" decision I made at 2.05 and 3.02, which is a coherent thing
for a reviewer to decide with the footage in front of them and not a thing I can decide from
a metadata table.

**The bridge findings in section 2 are not confidence-limited** - they are read straight
off the source and are true whatever anyone thinks of the empty plan.

---

## POSTSCRIPT - written AFTER the answer, and marked as such

Everything above was written before I submitted the empty plan. This section is what
happened next, and it belongs on the record because it is the most important thing this
step found.

**The QA loop rejected the empty plan three times:**

```
QA failed on attempt 1, retrying: Validation failed:
- Step 'plan_vfx' output 'vfx_creative' is semantically empty: []
```

...and the same on attempts 2 and 3, after which:

```
Warning: QA failed after 2 retries for plan_vfx, proceeding with best attempt.
✓ Completed in 320.2s
Outputs: ['vfx_candidates_toon', 'enhancement_spec']
```

**So the same creative floor is still in the pipeline, in a fourth hiding place.** It has
been removed from the prompt (the handoff now says "an empty list is a legitimate answer
for a piece that wants stillness"), removed from the post-bridge (whose comment says "An
empty plan is a legitimate answer - the handoff says so in as many words - and this is where
the code used to disagree with it"), and there is a test asserting it
(`tests/test_no_creative_floors.py::test_plan_vfx_accepts_an_empty_plan`). It survives in
the **generic step-output validator**, `validate_step_output` in
`library/processes/edit_video/run_pipeline.py:1467`, which flags any required list of length
zero as "semantically empty". `plan_vfx`'s manifest declares
`interface.llm_outputs.vfx_creative` with `"required": true`, so an empty list can never
pass. The validator knows nothing about creative planning steps; it applies a general rule,
and for this key the general rule is a creative floor.

**I did NOT fix it, and the reason is that it does not block the run.** The brief says to fix
only what blocks and report the rest. After the retries are exhausted the runner proceeds
with the best attempt, the empty plan reaches the post-bridge intact, and
`enhancement_spec.visual_effects` is empty as intended. The step reports `✓ Completed`.

**But it is not harmless, and this is the part worth the captain's attention.** What it costs:

1. **Three LLM round trips instead of one** - 320s on this step against ~235s for the
   others, for an answer that did not change.
2. **A false failure signal.** The log says "QA failed after 2 retries" on a step that
   produced exactly the documented correct answer. Anyone reading this run's log will see a
   failure that is not one.
3. **Pressure toward the wrong answer, which is the real cost.** The retry appends the
   validation error to the context and asks the model to correct it. An agent that treats
   QA feedback as authoritative - which is what QA feedback is for - will add a VFX it does
   not believe in on attempt 2. The floor no longer pads the plan mechanically; it now asks
   the model to pad it, three times, with increasing insistence. That is a softer floor than
   the one the captain removed, but it points the same way, and it is invisible in the
   output because a padded plan looks like a decision.

I held the empty answer across all three attempts because my reasoning had not changed and
because padding here is precisely what the ruling of 2026-08-20 exists to prevent. I want to
be clear that holding it was a deliberate act of stubbornness against the pipeline's own
feedback signal, and that an agent behaving more agreeably would have produced a different
video.

**The minimal fix, if the captain wants one**, is to let an output spec declare that an
empty value is legitimate - a `may_be_empty: true` on the `vfx_creative` entry in
`step_4_03_plan_vfx/manifest.json`, honoured by the `is_empty` branch in
`validate_step_output`. That keeps the emptiness check for every other step, where it is
doing real work, and states the exception in the one place that already declares this key.
It is one field and one condition. I have not made that change because the run completed
without it.
