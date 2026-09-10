# Animated explainer videos: the reading, the gap, and what was built

*Measured and built 2026-09-07, on the fourth of the captain's four named
capabilities. The other three are `docs/FULL_FRAME_ELEMENTS.md`,
`docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` and the motion-graphics roster
(AGENTS.md 16). This one builds on the first two and adds no third path.*

The captain's words:

> "there is a lot we can potentially do with it in terms of creating motion
> graphics and maybe even whole full screen animations and chroma key
> transitions and **animated explainer videos** which are all capabilities not
> flushed out for the pipeline."

---

## 1. Which of the three it is, and why

The phrase covers three different things and building the wrong one wastes the
work, so this is settled first.

| reading | what it is |
|---|---|
| (a) | a reel that is ENTIRELY animation, no talking heads, narrated over the hosts' audio |
| (b) | a sustained animated SEQUENCE inside an otherwise normal reel, explaining a concept the speech introduces |
| (c) | a diagram that BUILDS across several beats rather than one card appearing |

**(b) and (c) are one thing, and it is what was built.** (c) describes the
internal structure - stages revealed one at a time - and (b) describes where it
sits: over picture that keeps playing, while the speech goes on. Neither is a
capability without the other. A build whose stages are not timed to the words is
a graphic animating for its own sake; a sequence over speech that arrives whole
is a card. So:

> **An animated explainer is a STAGED graphic whose stages are revealed at the
> reel seconds where the speech says them.**

The vocabulary already said so, in its own words, before this module existed.
`motion_graphics_vocabulary`'s `list_build` states the refusal from the other
side: *"Where the items are not timed to the words. A set that appears all at
once is a quote_card."* **What makes an explainer an explainer is the anchoring,
and the anchoring is what nothing had.**

### Why (a) is not built, and what it would take

Not "hard". Mechanically it is **this, with the span equal to the whole reel and
the graphic opaque** - one declaration, no new code, and
`docs/FULL_FRAME_ELEMENTS.md` already carries a full-frame element that replaces
picture for its declared stretch.

What it needs and nobody has is sixty seconds of **authored artwork**: a ground,
a palette, a motion language and a drawing for every beat. AGENTS.md 14 puts
artwork in the project and AGENTS.md 10.5 forbids the engine stating taste. So
the engine can CARRY an all-animation reel and cannot AUTHOR one. That is a
statement about who draws it, not about whether it is reachable: a project that
owns the artwork declares a full-frame element for the span and gets it today.

---

## 2. The honest test: does the pipeline have the data?

An explainer needs three things - a CLAIM, its PARTS, and an ORDER to reveal
them in. Measured against what the pipeline really writes down:

| needed | produced? | where |
|---|---|---|
| the claim | **yes** | `reel_judgement.readings[].claim`, with `claim_quote` grounded in the reel's own words |
| its parts, enumerated and ordered | **NO** | the claim is ONE SENTENCE of prose; nothing splits it |
| when each part is said, in reel seconds | **derived** | the judge is already handed `lines` with `at` in REEL seconds; a part carrying its own quote is anchored by SEARCH |

**The gap is one field, and row three is not a second gap** - it is arithmetic
over row two. Give a part the words where it is said and the reel second falls
out of a search, which is the discipline AGENTS.md 6 already applies to a
passage (*"A passage is anchored by SEARCH, never by the occurrence nearest the
hint"*) and the discipline `reel_quality_bar.check_reading` already applies to
`claim_quote` and every `assumes_known[].quote`.

### The cheap versions, and why each was refused

Three things already on disk look like parts and are not:

- **`assumes_known`** is a grounded, ordered list with quotes - and it is what
  the reel does NOT explain. Drawing it would caption the reel's own gaps.
- **The reel's own sentences** are ordered and timed. Staging them is refused by
  the vocabulary itself: *"For items the speech does not actually enumerate. A
  list imposes a structure, and imposing one the speaker did not use misreads
  them."*
- **Splitting `claim` on "and" or a semicolon** is an engine inventing a
  structure, which is the whole of AGENTS.md 10.5.

### What was missing, twice over

Beyond the field, two mechanisms were absent and both are now built:

1. **The reels path had no route to the motion-graphics layer at all.**
   `motion_graphics` appears nowhere in `reel_build.py`; the reels process is
   `build_reels` and `verify_reels` and neither drew a graphic of any kind. No
   reel has ever carried one.
2. **`motion_graphics_plan` times an entry from `start_seconds` the model states
   freely**, with nothing anchoring it to a word. So even on the master, a build
   timed to the speech was not expressible - and `MotionGraphics/index.tsx`
   staggered `list_build` by an EQUAL SHARE of the element's duration, which is
   a graphic on its own clock rather than the roster's *"each as it is said"*.

---

## 3. The one field

`claim_parts`, added to step 3.05's ask - `reel_quality_bar.READING_FIELDS`,
`READING_SCHEMA` and the handoff - as an ORDERED, `contains`-grounded,
**OPTIONAL** list of `{part, quote}`.

Optional for the same reason `takeaway_quote` is: *"this claim has no parts"* is
a real answer and the common one. A claim that is one indivisible statement gets
no explainer, and that is correct.

**Its quote is not evidence FOR the part - it is the only thing that puts the
part in time.** So an ungrounded part is a part that cannot be drawn at all
rather than one drawn without support, and `check_reading` refuses the whole
reading, which is what it already does for `assumes_known`.

Checked against the captain's own 31 readings of the field-test episode:
**31/31 still ground with the field added.** Adding it breaks no existing
reading.

### A defect the field found

`reel_quality_bar.normalise(None)` returned the word `"none"`, because
`_words` did `str(text).lower()`. So a judge that answered an OPTIONAL field by
leaving the key out - rather than sending `""` - had its **whole reading
refused**, since "none" is not in what the reel says. `takeaway_quote` is
declared optional precisely so *"this reel delivers nothing a listener could
use"* can be answered, and that answer was unsendable in its most natural form.

A gate that fails correct output (AGENTS.md 10.4). It never fired only because
every reading on disk happens to carry the key, and `claim_parts` would have
inherited it. Fixed, and named in `tests/test_explainer_plan.py`.

---

## 4. The route, which is not a third one

```
reel_judgement.readings[].claim_parts    the model's grounded parts
  -> explainer_plan.anchor_stages        part -> reel second, BY SEARCH
  -> explainer_plan.plan_entries         stages -> ONE plan entry
  -> motion_graphics_plan.resolve_plan   the EXISTING resolver
  -> motion_graphics_plan.plan_segments  the EXISTING clusterer
  -> motion_graphics.render_segment      step 4.06's OWN renderer
  -> explainer_plan.measure_render       LOOK at what was drawn
  -> reel_build.build_reel_timeline      placed on the explainer track
  -> reel_conformance_verifier           F21, failing both ways
```

Every arrow after the second is code that already existed. This lane owns the
first two, the measurement, and the enumeration around them.

**No new element draws.** The three an explainer can be built from -
`list_build`, `comparison_bars`, `step_counter` - are already `reachable_now` in
`motion_graphics_vocabulary` and already drawn by `MotionGraphics/index.tsx`. A
fourth entry that drew nothing would be the defect class this project has spent
a week removing. `explainer_plan.assert_elements_stage` derives the subset from
the roster's own `what_it_is`/`needs` wording rather than listing it twice, so a
roster edit that removed the staging from one of them fails there.

**No new renderer, and no second `npx remotion render` call.**
`render_one_segment` was lifted out of step 4.06's own loop unchanged - same
composition, codec, profile, `--transparent` and timeout - and registered as
`motion_graphics.render_segment`. That is the shape `subtitles.render_segment`
already has beside `subtitles.render`, and for the same reason: the reels path
needs the UNIT, not the pass.

**What the two landed lanes settled, and this lane inherited rather than
re-derived:**

- the renderer boundary (`docs/FULL_FRAME_ELEMENTS.md` §1.5): picture that has
  to be CREATED is Remotion's; a treatment of picture that EXISTS is Fusion's.
  An explainer is created picture.
- the shape of an element laid over a reel
  (`docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` §4): **additive**, on its own video
  track, timing untouched either side, with the plan **RECORDED by the build**
  and graded against what was recorded rather than re-derived.

### The assumption, and how it reconciled

**Neither lane had landed on `main` when this was written**; both were in flight
on the same merge base, so the track allocation was an assumption. Both have
since landed - #605 (chroma transitions) and #606 (full-frame elements) - and
this lane was rebased onto them. **The assumption held, and nothing had to
move:**

- `explainer_plan.EXPLAINER_TRACK = 5`. The full-frame lane places cards on
  **V1** (they replace picture, so they must be inside the coverage assertion);
  the transition lane took **V4** (`transition_overlay.OVERLAY_TRACK = 4`). V5
  was free and still is, so an explainer, a transition element and a full-frame
  card can all be on one reel at once. The master's own allocation -
  `timeline_decisions` maps `motion_graphics_overlay` to V4 there - is recorded
  rather than followed, because on a reel V4 is taken.
- The three capabilities are three DIFFERENT layers, not three paths to one
  thing, and the rebase is what proves it: a card REPLACES picture (opaque, V1),
  a transition element TREATS the cut between two clips (V4), an explainer is
  ADDITIVE over picture that keeps playing (V5, `--transparent`). Each is a
  distinct `if` in `build_reel_timeline` and a distinct bucket in the snapshot
  reader; none of them shares a branch with another.
- **The transition lane found the same defect independently and fixed it
  first.** The snapshot reader classified `track <= 2 -> picture`, `== 3 ->
  captions`, `audio`, and an implicit else that **dropped the item on the
  floor**. #605 replaced that else with `unclassified_items` and F19, which
  reports whatever lands there. So the explainer's own `elif` for V5 is not
  what keeps it visible - F19 would have caught a V5 item with no bucket on the
  first run. Two lanes reaching the same repair from opposite ends is the
  strongest evidence that neither invented it.

---

## 5. Where an explainer sits: the measurement that was owed

`docs/FULL_FRAME_ELEMENTS.md` §7 recorded owed work and named its owner:

> "What is missing is a **picture-area enumeration** ... That is the owed work,
> and it belongs to whoever owns the overlay layer, not here."

`timed_text_overlay` records the same absence from the other side and works
around it by making every moment state its own `y`. `explainer_plan.picture_bands`
is that enumeration, and it measures nothing new: it is the intersection of two
existing measurements - the rectangle a reel really delivers
(`reel_framing.delivered_picture`) and the platform's keep-clear insets
(`safe_area`).

Measured on the field-test project, and it agrees exactly with the full-frame
lane's independent reading of the same thing:

```
frame        1080 x 1920
picture      rows  656..1264      (3840x2160 fitted; 31.6% of the frame)
safe area    top 120  bottom 320  left 90  right 120

band  above  rows  120.. 656   536 rows   no picture under it
band  over   rows  656..1264   608 rows   picture under it
band  below  rows 1264..1600   336 rows   no picture under it
```

**This answers the lane's own open question - whether an explainer competes with
the speech underneath it.** On this project it need not: there are 872 rows of
dead frame inside the safe area carrying nothing at all.

**It measures and reports; it does not choose.** Which band an explainer sits in
is the declaration's, and an explainer over the picture is NOT refused - a
graphic over the shot is a legitimate gesture and refusing it would be a gate
that fails correct output. What the run says is what the declaration bought: the
band's height, whether it exists on this frame at all, and how much picture it
covers.

**And it REACHES the picture rather than only describing it**, with no new
drawing code: the composition already resolves its nine-position `anchor` grid
against `props.safeArea`, so `band_insets` hands it the band's own rectangle in
place of the whole safe box and `bottom_left` means the bottom left of THAT
band. One prop, a narrower box, no second positioning mechanism.

---

## 6. What can fail, in both directions

A gate that cannot fail is worse than no gate (AGENTS.md 10.4), and so is one
that fails correct output. Both directions:

**Refused before anything is built** (`explainer_plan.REFUSALS`, and a reason
outside the tuple raises):

| refusal | when |
|---|---|
| `ungrounded` | the quote is not in the words this reel plays, or the part carries none |
| `out_of_order` | the reel says this part BEFORE the one listed ahead of it |
| `off_the_end` | the part is anchored at or past the reel's end |
| `not_drawable` | the declared element is not one the renderer dispatches on |
| `nothing_to_draw` | no stage survived (AGENTS.md 10.2) |

Ordering is checked against the **speech**, not the list: the list is the order
the model wrote them in, the speech is the order a listener hears them, and when
they disagree the speech is right.

A malformed declaration **RAISES by name** rather than being dropped, for the
reason `bookends.assert_no_invented_bookends` gives: a declaration that vanishes
into a log line is how the 4th Wall end card survived four months. Eleven
malformed shapes are tested individually.

**Refused after it is rendered** (`explainer_plan.render_findings`, on the
ALPHA channel of the last content frame):

- **ERROR, `frame_edge`**: ink reaching the outermost row or column. A graphic
  laid out inside a 90px left inset cannot legitimately reach column zero, so
  ink there means the frame cut it off - and Remotion renders a valid picture of
  the right size that nothing downstream can tell apart. The segment is REFUSED
  rather than placed.
- **WARNING, `outside_the_band`**: ink outside the declared band, in rows. Never
  an error: the boundary is shared with the picture and a text shadow
  legitimately falls across it. Measured on the real render of reel 21, the
  shadow crosses by exactly **one row**, and a check that failed on that would
  fail correct output.

**Graded on the built timeline** (F21, `check_explainer`), against the plan the
build RECORDED in `pipeline_output/review/explainer_plans.json` - never
re-derived, for the reason `docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` §4 gives:
a re-derived plan is only the build's plan while nothing changed in between, and
that assumption already produced 42 confident meaningless errors on this path.

- a planned explainer the timeline does not carry;
- an item on the explainer track no plan accounts for (the out-of-band append);
- an explainer at the wrong reel second, or of the wrong length;
- two explainer items overlapping.

Paired by **record frame**, never list index - the mistake that turned F2 into
701 meaningless findings. A build that recorded NO plan is not graded at all,
because a project built before explainers existed has no record and grading it
against an absence would fail every correct reel.

---

## 7. Rendered, and looked at

Composited over a **real reel frame at real size**, not a demo card and not a
generated background. Evidence and reproduction: `data/vep-explainer-video/`
in the firstmate home.

**The reel.** Field-test episode, reel 21,
`however-good-the-website-is-its-a-fifth`, 84.3 seconds. Its claim, in the
judge's own words: *"However good the website is, it is a fifth of what AI looks
at - the rest is a background check across LinkedIn, Crunchbase, the Google
business profile, Reddit, Instagram and press mentions."*

**The parts, and where the reel says them.** Six, each anchored by search to the
word its quote begins on:

| stage | quote | reel second | precision |
|---|---|---|---|
| LinkedIn | "your LinkedIn" | 40.694 | word |
| Crunchbase | "your Crunchbase" | 41.499 | word |
| Google Business profile | "your Google Business profile" | 42.303 | word |
| Reddit threads | "Reddit threads" | 43.710 | word |
| Instagram | "your Instagram" | 44.715 | word |
| Press mentions | "press mentions" | 45.922 | word |

**Why word precision is not a nicety.** The six sources are enumerated in one
breath and sit in **two** transcript segments, at 39.93s and 43.71s. Anchored to
the LINE - which is all the judge's own `lines` table carries - all six collapse
onto those two instants and the build stops being a build. So
`played_speech(..., with_words=True)` was added: off for every existing caller,
because **word timings do not reach a prompt** (AGENTS.md 10.1) and the judge's
`lines` table is a prompt. The build reads them; the model does not.
`Stage.precision` records which anchor answered, so a coarser one is stated
rather than assumed.

**The render.** 1080x1920, ProRes 4444, `yuva444p12le`, 197 frames at 23.976,
placed at reel 40.707s. Measured on its last frame: ink rows 320..657, cols
90..560, 61,778 ink pixels, no frame edge touched.

**Looked at.** `explainer_over_reel_*.jpg` - the same reel frame at 40.2s (the
explainer has not started), 42.5s (three stages up) and 46.5s (all six).

### What looking at it found

1. **The bullet was in the wrong place, and only a real frame showed it.** The
   marker was a fixed `marginTop: 10px` against a 46.8px line box, which put an
   8px dot near the cap height - it reads as a stray apostrophe ABOVE the word
   rather than a bullet beside it. Now derived from the line box
   (`LINE_HEIGHT`), so a marker cannot drift from the text it marks. This is
   pre-existing `list_build` drawing, not new code; it had only ever been seen
   over a generated background.
2. **The list reserves its final height and does not jump.** All six items are
   laid out from the first frame with the unrevealed ones at zero opacity, so
   the block is bottom-anchored in its band and items appear into a stable
   layout. Correct, and worth stating because it is what stops a build from
   shoving the earlier items upward on every stage.
3. **The band works.** The explainer occupies rows 320..657 of a frame whose
   picture starts at 656. It touches the picture's first row with one row of
   shadow and covers nothing.

### The other band, also rendered - and what it costs

`band: above` on this project is the top letterbox bar, and nothing plays there.
That makes it the EASY case, so the same reel was re-rendered with `band: over`
to see the one that is not: `explainer_over_band_42s.jpg` and `_46s.jpg`, same
six stages, same seconds, composited over the same real frames.

```
BAND:  over   rect [90, 656, 960, 1264]   height 608
       covers_picture: true   picture_covered_fraction: 0.3167
MEASURED: {"rows": [928, 1265], "cols": [90, 560], "touches_frame_edge": []}
WARNING:  the explainer's ink runs 0 row(s) above and 1 row(s) below the
          'over' band [656..1264]
```

It draws, it is bottom-pinned inside the derived band, and it clears the frame.
**What it also shows is that legibility in the `over` band is a property of the
FOOTAGE, and nothing in the reels process measures it.** The first three stages
sit on the dark acoustic wall and read cleanly; the last two cross the speaker's
hands and the table's specular highlight and read markedly worse.

That is deliberately not repaired here, and the reason is the same one
`reel_framing` recorded from the other side: `picture_quality`,
`temporal_index`, `window_frames` and `render_qa` all live in `edit_video`, and
**the reels process runs none of them**, so a reel has no measurement of what
its own picture is doing at a given second. A legibility threshold would be an
invented one (AGENTS.md 10.5) and a gate on it would fail correct output - a
graphic over a shot is a legitimate gesture. So `band_report` says the graphic
covers the picture, says what fraction, and **stops there rather than claiming a
legibility it did not measure** (AGENTS.md 10.3).

This matters beyond this project. `framing_intent: 0.0` is what puts a usable
empty bar above this reel's picture; the engine's own default is **FILL**
(AGENTS.md 10.3), and on a filled frame `above` and `below` have zero height and
`band_insets` REFUSES them by name. So the `over` band is what most projects
would actually get, which is why it is rendered here rather than described.

### The failing case, also rendered

A twelve-item list in the same 536-row band, rendered and measured:

```
MEASURED: {"rows": [0, 657], "cols": [90, 560], "touches_frame_edge": ["top"]}
ERROR:   the explainer's ink reaches the top edge of the frame, so the frame
         has cut it off; it does not fit the band it was laid out in
WARNING: the explainer's ink runs 120 row(s) above and 1 row(s) below the
         'above' band [120..656]
```

The correct build gets the warning alone; the overflowing one gets both, and the
error refuses the segment. **Both directions on real renders, not on fixtures.**

---

## 8. What is NOT built, and why

- **No all-animation reel.** Section 1. The mechanism carries it; the artwork is
  the captain's (AGENTS.md 14).
- **No copy the pipeline authored.** Every word an explainer draws is a `part`
  the judge wrote while reading that reel's own lines, and every one is grounded
  in a quote. Nothing summarises, shortens, title-cases or rewrites them.
- **No default colour, typeface, element, band, anchor, hold or motion.** Each
  is refused when absent rather than filled in, and
  `test_the_engine_supplies_no_value_of_its_own` checks all of them.
- **No explainer on the edit_video path.** `reel_build` places on a track it
  owns; `compile_manifest`'s V-track allocation is a separate question with its
  own coverage assertion, and answering it here would have been a second
  mechanism rather than one. The same boundary
  `docs/CHROMA_KEY_TRANSITIONS_MEASURED.md` §8 drew.
- **No second stage of a build authored by the engine.** If a claim's parts are
  said in a different order than the reading lists them, the stage is REFUSED
  rather than reordered: reordering would be the engine deciding what the reel
  meant.
