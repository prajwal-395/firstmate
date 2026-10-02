# Something watches the built reel

What this is, what it catches, what it misses, and what it costs.

The rule lives with the code: `library/tools/render_watch.py`.
This document is the ACCOUNTING - the part that has to be unflattering to
be worth anything.

---

## 1. The state this was built out of

Measured 2026-09-13, and unchanged from `PICTURE_JUDGEMENT_INVENTORY.md`
(2026-09-06):

| Where | What it looks at |
|---|---|
| `library/processes/reels/dag.json` | two nodes: `build_reels`, `verify_reels` |
| `reel_conformance_verifier.py` (3,416 lines) | format, item count, picture holes, audio holes, caption timing, caption overlap - **all structural** |
| `reel_quality_bar.py` | the **transcript** |
| `window_frames.py` | a real picture, declared by `step_3_02_select_broll` and `step_3_03_review_rough_cut` - **both `edit_video`, neither reachable from a reel** |
| `step_6_02_validate_output/handoff.md:19` | *"You are watching the RENDERED video ... The ultimate test: Would I post this?"* over three inputs - `assembly_manifest`, `rendered_output`, `project_folder` - **none of them a frame** |

The last row is the sharp one. It is not that nothing watched; it is that
the one step whose prose said it was watching **was handed a table**, and
a gate whose prose claims eyes it does not have reads as coverage
(AGENTS.md 10.4).

---

## 2. What was decided, and why

### What gets looked at: the RENDERED FILE

Every picture defect in the recorded corpus is in the COMPOSITE, not in a
source clip: subtitles at 160 px against an 85 px reference, motion
graphics and captions placed off the frame, a card composited on the
wrong row, a defocus held at full strength across a whole clip. None of
those exist in the footage and none exist on the plan. They exist only
once everything is drawn over everything else.

`window_frames` shows the SOURCE window a step is choosing.
`render_watch` shows the OUTPUT that choosing produced. Different
surfaces, which is why both exist and why the second reuses the first's
extractor rather than replacing it.

### So watching requires a render, and that is not designed around

A render is the expensive thing the captain must ask for (standing ruling
2026-09-09 / 2026-09-10; `reel_deliver.py` is built on it, and on
2026-09-09 they cancelled one mid-flight). This capability inherits that
exactly:

* **`edit_video`**: the render already happened - step 6.01 produced the
  file 6.02 is judging - so the watch is free there and runs on the
  render that ran.
* **`reels`**: a reel becomes a file only through `deliver-reel`, so
  `manage_project.py watch-reel` reads that file and **never starts a
  render**. A reel that has not been delivered is refused, with the verb
  that would deliver it named in the refusal.

`library/processes/reels/dag.json` stays two nodes.
`tests/contracts/test_reel_deliver_is_explicit.py` holds it there and
`tests/test_render_watch.py` asserts the watch path imports no render
path.

### Who looks

Whoever answers the step. On `edit_video` that is `step_6_02_validate_output`'s
own model; on the reels path it is the agent running the verb.
`window_frames.HARNESS_SHOWS_FRAMES` is the existing enumeration of which
harnesses can be shown a picture and it is reused unchanged - `agent` and
`mock` yes, `api` no, an unknown harness raises.

**No craft role was invented.** `craft_role.WITHOUT_A_DECLARED_ROLE`
records that whether a QA pass should be told it is an expert is a real
question and the captain's - *"a gate that is told it is an expert may
become a gate with an opinion"*. Showing it the picture gives it
CAPABILITY; a role would give it a STANCE. Only the first is taken here.

### What it is asked

`render_watch.WATCH_QUESTIONS` - eight questions, each one a specific
frame answers yes or no, each naming a defect and never a value:

| key | asks |
|---|---|
| `text_in_frame` | is every word of on-screen text fully inside the frame |
| `text_size` | at phone size, is a caption too large to sit over the speaker, or too small to read |
| `graphic_intact` | is every graphic whole, once, unstretched, not colliding with another |
| `subject_framed` | is the face inside the frame and not under an overlay |
| `picture_legible` | is it in focus and exposed, or blurred/smeared/crushed |
| `picture_says_something` | is this a recognisable subject or a shot of nothing |
| `frame_filled` | is there a black bar, a pillarbox, a visible source edge |
| `within_strip` | does anything appear, vanish, jump or slide inside one continuous shot |

***"Would I post this?"* is deliberately NOT one of them.** It is the
captain's bar and the right one for them, and it is not checkable by a
step: it asks for a whole judgement about a product rather than an
observation about a frame. The eight above are the honest version of the
same demand - they cover the defects the captain has actually had to type
onto a timeline.

`render_watch.NOT_ANSWERABLE_FROM_STILLS` is the other half, written
down, for the same reason `motion_graphics_vocabulary` raises on an entry
with no `never`: a list that says only what it covers teaches a reader to
trust it for everything.

| key | why not |
|---|---|
| `where speech is cut` | in the WORDS and the SOUND. No frame carries it |
| `anything audible` | levels, clipping, a bed swamping speech. `render_qa` measures these |
| `picture against sound at one instant` | the strip has no audio |
| `anything shorter than the sampling resolution` | at 1.0 s between samples, a 10-frame head, a 7-frame drift and a 4-frame card are never sampled |
| `motion between two samples` | a strip can show a clip IS blurred, never that a blur failed to ramp |
| `what another reel has and this one does not` | one watch is of one file. Nothing here makes a comparison |

### What it does with the answer: REPORTS

It fails no build and refuses no render. The structural verifier is
correct and cheap and stays the gate; this is additive.

A gate that refuses on a model's opinion is a new failure mode on a
pipeline whose gates have refused correct output three times this week
(`RULE_EVIDENCE.md#gates-that-fail-correct-output`). And every defect a
watcher adds is a judgement - *is that text too big*, *is that shot of
nothing* - so a judgement that blocks a build is the pipeline taking
taste on the model's behalf, which 10.5 forbids in the other direction
for the same reason.

**One thing IS hard, and it is mechanical.** A watch that was ASKED FOR
and drew no picture raises `NothingWasWatched`. It is about the
instrument, never the answer - the same line `render_qa` draws when its
toolkit fails to run. The record keeps `watched` and `answer` as separate
keys, because *nobody looked* and *somebody looked and saw nothing wrong*
are different facts, and collapsing them is the table-as-eyes defect
again.

---

## 3. The accounting: 4 of the audit's 15

`data/vep-what-is-the-pipeline-still-missing/report.md` §R1 names the
acceptance set exactly: *"Asks 1, 2, 4, 5, 6, 9, 10, 12, 13 and every one
of the six corrections."* Nine typed asks plus six recorded corrections.

**This design would have caught FOUR of those fifteen.**

| # | The captain's words | Verdict | Why |
|---|---|---|---|
| 1 | R01 "this cut on craig is a little jarring" | **WOULD NOT** | a cut point is in the words and the sound. `where speech is cut` |
| 2 | R09 "the tv on animation should start from fully black" | **WOULD NOT** | the lit head is a few frames. Below the 1.0 s sampling resolution |
| 4 | R13 "craig's line is a bit repetitive" | **WOULD NOT** | transcript |
| 5 | R13 "the ending tv close happens while she's finishing talking" | **WOULD NOT** | picture against sound at one instant. The strip has no audio |
| 6 | R13 "it shows on the wrong row in the timeline" | **WOULD CATCH** | the card composited over the wrong picture for a second or more. `graphic_intact`, `within_strip` |
| 9 | R28 "audio cut off ... and the CTA does not make sense with the video" | **WOULD NOT** | the audio half has no frame; the CTA half is a judgement about the whole reel's meaning, and this watch is per-span |
| 10 | R31 "the graphics renders for both the motion graphics and subtitles are messed up" | **WOULD CATCH** | the flagship case. `text_in_frame`, `graphic_intact` |
| 12 | 001 "this clip is honestly like broll of nothing" | **WOULD CATCH** | `picture_says_something` |
| 13 | 001 "why is this fully blurry" | **WOULD CATCH** | 11.03 s of a 59.43 s video at full defocus - eleven samples inside it. `picture_legible` |
| lc-0001 | transcript "lucy" -> "Lucie" | **WOULD NOT** | the watcher can READ the burnt-in caption and has no reference saying the spelling is wrong. The one that looks catchable and is not |
| lc-0002 | a verbal stumble left in | **WOULD NOT** | `where speech is cut` |
| lc-0003 | dead air appended past the approved end | **WOULD NOT** | same |
| lc-0004 | over-cut at jaccard 0.667, mid-sentence | **WOULD NOT** | same |
| lc-0005 | over-cut a refrain, leaving "...links up with A WEB." | **WOULD NOT** | same |
| lc-0006 | a real repeat it did not see | **WOULD NOT** | same |

**4 of 15.** That number is the finding, not a shortfall to be argued
away. **Eleven of the fifteen are one defect: WHERE SPEECH IS CUT.** Six
of the six corrections are that decision, and three more of the nine asks
are audible or textual. Eyes are not the fix for those - root capability
R3 is, and it owns `reel_build.redundant_takes`.

### The four the brief named are all caught

The brief set a floor: *"A watcher that would not have caught the blurry
clip, the oversized subtitles, the b-roll of nothing, or the off-frame
captions is not worth building."*

| The floor | Caught by |
|---|---|
| the blurry clip (ask 13) | `picture_legible` |
| the oversized subtitles (ask 11) | `text_size` |
| the b-roll of nothing (ask 12) | `picture_says_something` |
| the off-frame captions (ask 10, §7.3) | `text_in_frame` |

Ask 11 - *"why are the subtitles so big?"*, 160 px against an 85 px
reference - is the clearest single catch of the whole corpus and the
audit's own 15 does not include it. Counting the 13 asks and 6
corrections as 19, this design catches **five**.

### What would move the number, and what it would cost

`SECONDS_UNSEEN_BETWEEN_SAMPLES` is ONE constant, shared with
`window_frames`, stated in every block it reaches. Ask 2 (a lit animation
head), ask 8 (a 7-frame caption drift, and a card trimmed to 4 frames)
and the ramp half of ask 13 are all invisible only because they are
shorter than it.

Sampling a 35 s reel at 0.25 s instead of 1.0 s is 140 frames rather than
35: **18 strips rather than 5**, and roughly four times the decode - which
is still seconds. The cost is the MODEL reading eighteen images per reel
instead of five. That is a spend decision and it belongs to the captain,
which is why the number is a constant with its reasoning beside it rather
than a heuristic that varies by span.

---

## 4. What it costs

Measured 2026-09-13 on this machine, against a synthetic **1080x1920 30
fps h264** file of **35.0 s** - the reel frame and a realistic reel
length:

| | measured |
|---|---|
| strips drawn | **5** (8 frames each, 1.0 s apart, covering 0.000-35.000 s end to end) |
| wall clock to draw all five | **4.03 s** |
| bytes on disk | **319 KB** total, 64 KB per strip |
| a re-run with the strips already there | **0.05 s** |

That is the whole machine cost of watching one reel. It is not the whole
cost of the capability: **the expensive half is the model opening five
images**, and eight reels is forty.

### Against the captain's cheap-always / expensive-on-request ruling (2026-09-09)

| | runs | what |
|---|---|---|
| **CHEAP, ALWAYS** | every `build-reels` | `verify_reels` and `reel_conformance_verifier`, unchanged. **This change adds nothing to a build.** No `build-reels`, no `run`, no DAG node and no operation reaches `render_watch` |
| **EXPENSIVE, ON REQUEST** | `manage_project.py watch-reel <project> <reel>` | 4 s of decode over a file the captain already asked for, plus a model reading five images |
| **FREE, WHERE A RENDER ALREADY RAN** | `step_6_02_validate_output` on the `edit_video` path | the render is step 6.01's; drawing the strips is seconds on top of a QA pass that already decodes the file |

The split falls where it does because **watching is downstream of
rendering**, and rendering is already the thing the captain must ask for.
Nothing new had to be gated.

---

## 5. What step 6.02 says now

Its handoff no longer opens *"You are watching the RENDERED video"*. It
names the two things it receives and says what each one is:
`deterministic_validation` is the MEASUREMENTS,
`render_watch_frames` is the PICTURE, **open them**. And:

> **When `render_watch_frames` is absent, or carries a line saying the
> frames were withheld, NOTHING HAS WATCHED THIS RENDER.** Say so in your
> answer and judge from the measurements alone.

The bridge backs that with a fact rather than a promise: it draws the
strips off the rendered file, emits the block only when strips exist,
and writes `watched: true|false` into the verdict. A run that drew
nothing appends `[watched] no frames were drawn from the render, so
NOTHING SAW THIS PICTURE` to `all_issues`. It still does not FAIL - the
picture half reports - but it can no longer be silent about having no
eyes.

A harness that cannot be shown a picture (`api`) has the block withheld
by `window_frames.withhold_for_harness`, the one place that knows a
harness has no eyes. Its notice is this module's own, not the generic
one, for a specific reason: a step that spent its whole life claiming to
watch would read *"frames were withheld"* as permission to claim it
again. The line it gets says **NOTHING HAS WATCHED THIS RENDER**.

---

## 6. Known unknowns, answered

| The brief asked | Answer |
|---|---|
| Does watching require a render? | **Yes**, and it is not designed around. §2 |
| Can a model answer usefully from stills, or does it need motion? | **Both are true and the boundary is written down.** Four of the fifteen are answerable from stills; the motion-only and audio-only cases are enumerated in `NOT_ANSWERABLE_FROM_STILLS` and reprinted in every block |
| Does the honest first version report rather than gate? | **It reports.** The only hard failure is mechanical - a watch that drew no picture. §2 |
| How many of the 15 are genuinely watchable? | **Four.** Eleven are one defect, *where speech is cut*, and that is R3's. §3 |
