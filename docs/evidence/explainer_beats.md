# The BEAT route: an explainer that is PICTURES, timed to the words

*Measured 2026-10-21 on the captain's rejected full-screen explainer over
Reel 26, on the scratch copy (`Ren Scratch - breaktest`).  The captain's
verdict on what that lane produced, verbatim:*

> "the explainer animation that you made for reel 26 actually sucks, it
> was just the subtitles animated into the screen but it wasn't even times.
> there were no pictures or visual elements that actually composed an
> animation"

Two defects, and they are two defects:

1. **What was drawn was the reel's own words.**  The stage route
   (`explainer_plan.plan_entries`) builds ONE plan entry whose `copy` runs
   are the stages.  The thing that appears IS the caption, in pieces.  A
   BEAT is the other thing: a PICTURE - a project-owned still - with the
   words reduced to a supporting label.

2. **Nothing landed on the words.**  A beat is anchored by SEARCH over the
   reel's own WORD timings, and the offset between the picture and the
   word is MEASURED off the rendered file and reported per beat.

## What was rendered, and what it measured

Reel 26 - `write-for-the-question-your-customer-ask`, 39.7 s, 1080x1920,
23.976 fps, over the reel's own speech.  Seven beats, each a project-owned
still at an absolute canvas box, each with a supporting label:

| beat | words it illustrates | word at | picture arrived | picture landed | arrival vs the word | landing vs the word |
|---|---|---|---|---|---|---|
| 1 | `kind of content` | 0.12 s | 0.04 s | 0.13 s | **-78 ms** | **+5 ms** |
| 2 | `answers specific questions` | 7.16 s | 6.84 s | 7.13 s | **-320 ms** | **-28 ms** |
| 3 | `your customer comes up to you` | 11.40 s | 11.09 s | 11.39 s | **-306 ms** | **-14 ms** |
| 4 | `you make content based on that` | 17.39 s | 17.10 s | 17.35 s | **-290 ms** | **-39 ms** |
| 5 | `good for Google but not for AI` | 23.94 s | 23.65 s | 23.94 s | **-291 ms** | **+1 ms** |
| 6 | `very interesting niche` | 26.69 s | 26.40 s | 26.65 s | **-289 ms** | **-38 ms** |
| 7 | `AI really likes that` | 32.08 s | 31.78 s | 32.07 s | **-298 ms** | **-6 ms** |

**Every picture LEADS the word it illustrates, and every picture is
COMPLETE within 40 ms of it.**  That is the measured rule of
`docs/ANIMATION_FIRST_REFERENCE.md` §1 - the reference's illustrations
arrive 67-839 ms before the noun they illustrate, and "a word completes on
its syllable. It does not begin there."

Beat 1's lead is 78 ms rather than 300 because its word is the reel's
FIRST word (0.12 s) and the picture cannot arrive before the clip does.

The contact sheet - one frame per beat, at the frame the picture landed,
with its timestamp and the words the reel says there - is
`docs/evidence/explainer_beats/contact_sheet_reel26.png`.  The raw
measurement is `docs/evidence/explainer_beats/beat_offsets_reel26.json`.

## How the measurement is taken

`explainer_plan.measure_beat_offsets` reads the RENDERED file, not the
plan.  A plan says when a picture was supposed to arrive; only the
picture settles when it did.

- The frame stream is a quarter-scale luma read (ffmpeg), so a 950-frame
  scene is measured without holding 3 GB of pixels.
- The GROUND's tone is the MODE of the region's pixels over the clip -
  the tone the region spends most of its pixels on.  The MEDIAN is wrong
  here and measurably so: a picture that is on screen for half the clip
  puts the median BETWEEN the two tones, so every frame of bare ground
  reads as a deviation and the picture appears to arrive at 0.00 s.
- The region's deviation from the ground is SEGMENTED into the stretches
  in which something was drawn, and each beat is matched to the segment
  its word falls inside.  Beats share a box - they take turns in the
  same place - so one curve per beat would read the first picture's
  arrival for every beat, which is a measurement of nothing.
- ARRIVAL is the picture's first ink, scanning back from the segment's
  own start but never across into the previous picture's stretch.
- LANDING is the first frame the picture reaches 90% of ITS OWN peak (not
  the clip's: a dark picture on a dark ground never reaches the
  brightness of a white one).

## What the engine refuses

- a beat with no `asset` - `no_picture`: a beat IS a picture, and a beat
  without one is the caption rail the captain rejected;
- a beat whose `asset` is not on disk - `asset_not_found`: a picture that
  is not there is a hole in the frame;
- a beat whose `quote` is not in the reel's own words - `ungrounded`;
- a beat the reel says EARLIER than the one before it - `out_of_order`;
- a beat anchored past the reel's end - `off_the_end`;
- a declaration with no ground, no typeface or no label size - raised by
  name: every look in the scene is the PROJECT's (AGENTS.md 10.5).

## Where the artwork lives

The stills are PROJECT assets (`brand_assets/`), not engine artwork
(AGENTS.md 14).  They are staged into Remotion's `public/brand/` and the
props carry the staged `brand/<file>` path.  The engine draws the
COMPOSITION - the ground, the camera, the timing, the reveals - and the
project draws the pictures.

## The render path

`library/tools/explainer_render.py` draws the scene with the SAME
composition the animation IR is lowered into (`StagedScene`), through the
shared batch (`library/tools/remotion_batch.py`).  There is no second
scene renderer.

One change was needed in that batch: a long scene (950 frames here)
re-opened Remotion's `delayRender()` font handle on a busy machine and
the render died with a message about a FONT it was never waiting on.
`render_batch` now takes a `render_timeout_ms` and states it; the default
(Remotion's 30 s) stands when nobody states one.

## The lesson from the motion-graphics skill the captain pointed at

The captain pointed at a Higgsfield page about generating motion from a
prompt, and at the skill it grew into
(`NightZpy/explanatory-animations-skill`: sixteen named didactic PATTERNS -
system flow, lifecycle, comparison, layered transform, timeline, counter,
shape morph, SVG line drawing, orbital, particle flow - a declared
palette/typography/icon set, ONE Anime.js timeline per widget, and a
Remotion export path).  Read as reference data, it says one thing about how
a dense Vox-style explainer is made:

> **the density comes from a PATTERN of elements that OVERLAP and keep
> MOVING on one timeline, not from a queue of one-shot entrances.**

**Would the approach get Ren there?**  The METHOD would, and three parts
of it are now in the beat route:

| the skill's lesson | in Ren |
|---|---|
| a scene is a PATTERN of elements, not one icon | `elements:` - several per beat, each at its own absolute box, with `connectors:` between them |
| one timeline, so the motion OVERLAPS and CONTINUES | `carry:` - an element is drawn ONCE and MOVES into the next beat; `drift:` - an element keeps moving for as long as it is on the frame; the camera track moves for the whole piece |
| the palette, the type and the icons are DECLARED | every colour, size, box and magnitude is the project's (AGENTS.md 10.5, 14) |

**What would NOT get Ren there, and why:**

- **A generative video model cannot time a picture to a word.**  The whole
  point of this route is that each beat is anchored by SEARCH over the
  reel's own word timings and the offset is MEASURED off the rendered
  file.  A generated frame arrives when the model says so, which is the
  defect the captain named the first time ("it wasn't even times").
- **The pattern catalogue is not a renderer.**  Sixteen patterns would be
  sixteen new drawing arms, and each pattern's look is a taste (AGENTS.md
  10.5) that belongs to the project.  The beat route takes the SHAPE of
  the idea - the project declares the composition, the engine draws and
  times it - and leaves the catalogue for a lane that can own the
  drawings.

The 2 fps sheet of the result is
`docs/evidence/explainer_beats/render-2fps-sheet_reel26.png`: every frame
of the render at its second, with the progress mark travelling across the
top as the piece's own clock.
