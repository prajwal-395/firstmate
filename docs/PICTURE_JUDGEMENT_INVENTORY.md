# Nothing in this pipeline has ever seen the picture

An inventory, measured 2026-09-06 against the engine at `b836552` and the
captain's own Resolve project `Podcast (field test)`.

The question was: **what visual analysis exists, what does it produce, and does
anything consume it?** The answer has three parts, and the second is the one
worth the captain's attention.

---

## 1. What visual analysis exists, and does it run

Nine things in this engine open a video file and look at the pixels. Seven of
them are real and work. Here is every one, what it produces, and who reads it.

| Module | What it measures | Reader | Runs on the podcast? |
|---|---|---|---|
| `steps/step_1_04_temporal_index` | 12 signals per clip: scene boundaries, motion energy, optical flow direction, camera-motion decomposition, **face presence at 5 Hz** (value, `face_center_x`, `face_width`), hue/brightness at 1 Hz | `cutaway_window`, `subject_framing`, `camera_stability`, `framing_intent`, `compile_manifest` | **No - the step never ran** |
| `tools/analysis/vision_pipeline_v3` (step 1.03) | The VLM pass: scene, camera, actions, objects, assessment, `usable_ranges` | 6 of 12 LLM steps, via `vision_schema_adapter` | **No - `1_03_semantic_analysis/` is an empty directory** |
| `tools/analysis/picture_quality` | Laplacian sharpness at 5 Hz -> soft-picture ranges | `vision_pipeline_v3`, and nothing else | No |
| `tools/render_qa` | Decodes a finished **render**: black frames, freeze frames, frame occupancy, face intact, chroma presence, silence under picture | step 6.02, `manifest_validator`, `qa_findings` | No - the reels are timelines, never rendered |
| `tools/window_frames` | Actual JPEG frames of every candidate cutaway window, **into the prompt** | step 3.02's bridge | No |
| `tools/subject_framing` | Median face centre and width over a clip's range -> where to aim the crop | `compile_manifest._conform_fields` | No |
| `tools/analysis/object_segmentation` (1.06) | SAM2 masks | **Nothing.** Declared `wired=False` with a reason | No |
| `tools/analysis/ocr_extractor` (1.07) | On-screen text | Wired, deselected by default | No |
| `dashboard/footage_search` + `analysis/footage_segments` | Frame embeddings for cross-clip search | The dashboard only, on a button press | No |

`window_frames` is the only path in the engine that puts an **image** in front of
a model. Every other visual signal reaches a judgement as a number or a word.

### The reels product opens no video file at all

`library/processes/reels/dag.json` is two nodes, `build_reels` then
`verify_reels`. Between them:

- `reel_conformance_verifier` (3,416 lines) checks format, item count, picture
  holes, audio holes, caption timing, caption overlap. All structural.
- `reel_quality_bar` judges from the transcript.
- `reel_opening` reads the opening **words**.
- `reel_proposal`, `reel_exchange`, `reel_spine` read the transcript.

Grepping the seven `reel_*` modules for `ffmpeg`, `ffprobe`, `cv2`,
`render_qa` or `picture_quality` returns nothing. The word "frame" appears only
as a unit of time.

### And on this project, none of the picture analysis has ever run

`pipeline_output/steps/` in the podcast project holds `0_01`, `1_01`, `1_02`, an
**empty** `1_03_semantic_analysis/`, `3_04`, `3_05` and `4_05`. There is no
`1_04`. `pipeline_data.json` carries five step outputs: `scan`, `catalog`,
`select_reels`, `judge_reels`, `build_reels`.

So the answer to "does the existing analysis run" is not "it runs and nothing
reads it". It is **it does not run**, because the reels product is a second
process that does not include preflight, and the reels are cut from a supplied
rough cut rather than from analysed footage.

---

## 2. Signals computed and dropped

### `face_absent_times` and `face_present_times` - computed at 5 Hz, read by nothing

`step_1_04_temporal_index.compute_face_presence` returns five parallel arrays.
Three of them have readers. Two do not:

```
face_present_times   timestamps where face confidence > 0.5
face_absent_times    timestamps where face confidence < 0.1  (no face)
```

Every occurrence of either name in the whole of `library/` is inside the file
that produces it. `face_present_times` is read once - to print a percentage to
stderr. `face_absent_times` is read nowhere at all.

That signal is the brief's second gap verbatim: *"whether a shot actually holds,
or whether the speaker walks out of frame"*. It is already computed, already
written to disk, and no step, gate or prompt has ever looked at it.

### `MIN_DETECTION_RATIO` declines rather than reports

`subject_framing.subject_center_x` returns `None` - meaning "leave the framing
centred" - whenever fewer than `MIN_DETECTION_RATIO` (0.34) of the samples in a
clip's range carry a detection, or fewer than `MIN_SAMPLES` (4) do.

The detector underneath is OpenCV's **frontal** Haar cascade, and the concern is
that "the subject is centred" and "the detector could not see them" collapse
into the same return value. A caller cannot tell a measurement from a shrug.

**On this footage that concern does not bite**, and section 6 is the
measurement rather than the assumption: 0 of 86 windows failed to detect, the
worst window detected in 80% of its samples, and the two windows where a pan
would be declined are declined by `CENTRE_DEADBAND` - the subject really is
centred - not by detection failure.

The structural point stands anyway. A signal whose absence and whose
"no change needed" are the same `None` cannot be audited on footage where it
DOES bite, and nothing today would say which had happened.

---

## 3. The defect this let through

`reel_build.build_reel_timeline` creates a 1080x1920 timeline and appends the
3840x2160 master clips. It sets no transform, and it never reads
`framing_intent`.

Read off Resolve on 2026-09-06 - all 49 timelines in the captain's project, the
20 harvest reels among them:

```
timeline resolution                1080x1920      (48 of 49; the master is 3840x2160)
timelineInputResMismatchBehavior   scaleToFit     (every timeline)
video items on those timelines     376
distinct transforms among them     1
   ZoomX=1.0  ZoomY=1.0  Pan=0.0  Tilt=0.0  Crop*=0.0  Scaling=0
```

Fitting 3840x2160 inside 1080x1920 gives a picture 1080 x 607.5. **Every frame
of every reel is a 16:9 strip occupying 31.64% of the delivery frame; the other
68.4% is black.** Nothing in the reels path could see it, because nothing in the
reels path opens a video file.

The engine has all three pieces that would have caught it and none of them is on
this path: `framing_intent` declares what the picture should be,
`compile_manifest._conform_fields` turns that into a zoom, and
`render_qa.measure_frame_occupancy` measures the result. All three live in
`edit_video`.

### Why it is not (today) a bug, and why it is still the finding

The project adopts `cinematic_narrative`, which declares
`style.framing_intent: 0.0` - the letterbox is that template's look. So what is
delivered happens to be what is declared, and the new check **passes all twenty
reels**.

It passes by coincidence. `build_reel_timeline` never read the declaration;
Resolve's default agreed with it. The engine's `DEFAULT_FRAMING_INTENT` is
`FILL`, so any project that does not adopt this one template asks for a full
frame, gets a 31.6% strip, and nothing anywhere says so.

A declaration that does not bind is the same defect whether or not today's value
happens to match.

---

## 4. What decoding actually costs

Measured on the real footage: 7 MXF files, 3840x2160 h264, 181.6 minutes,
132 GB.

| Operation | Measured |
|---|---|
| Seek + decode one frame, anywhere in any file | **0.33 s**, flat with file size and seek depth |
| Contiguous decode, 60 s window, 1 Hz | 11.2 s |
| Contiguous decode, 60 s window, 5 Hz | 11.2 s |
| Contiguous decode, 60 s window, 5 Hz at 480 px | 12.3 s |

**Sample rate is free; the 4K decode is the whole cost.** 1 Hz and 5 Hz cost the
same, so a picture check should sample as densely as it likes.

The rate is 0.186 s of wall per second of video. From that:

| Job | Cost |
|---|---|
| The opening frame of each of the 20 reels | 86 frames-worth of seek: **6.9 s** (measured) |
| Every second of picture the 20 approved reels play (86 windows, 1436 s) | **~4.5 minutes** |
| A full 5 Hz pass over all 181.6 minutes of source (what step 1.04 would cost) | **~34 minutes**, plus detector CPU |

Section 6 carries the measured number for the middle row, including face
detection.

**This is cheap.** The reason no picture signal reaches a reel judgement is not
cost. It is that nothing is wired to ask.

---

## 5. What was built: one signal, reaching one judgement

`library/tools/reel_framing.py` and `reel_conformance_verifier`'s **F12**.

F10 already proves the reel timeline **is** 1080x1920 - it exists because a
correct vertical timeline once rendered out landscape while every structural
check passed. F12 proves what is **in** that frame.

- The rectangle a clip puts on the delivery frame is arithmetic on its own
  Resolve transform. No decode, no model, no score.
- The rectangle the project **asked for** is the same arithmetic run forwards
  from `framing_intent` - the existing enumeration, not a second spelling.
- The comparison is between **integer pixel rectangles**. The only tolerance is
  one pixel of the delivery frame, which is the resolution of the medium.
- A non-zero Resolve crop makes it **refuse to grade** that item rather than
  assume units it has never observed a non-zero value for.
- A source the catalog does not carry, or a project that resolves no
  declaration, produces a **warning naming what could not be read** - never
  silence.

`timeline_ingest.TimelineClip` gained one field, `transform`, read with
`GetProperty()` and no argument. That is the whole plumbing.

### Both directions, on the captain's twenty harvest reels

```
catalog sources: 7   declared framing_intent=0.0 crop=1.0
harvest reels: 20

--- AS DECLARED (cinematic_narrative, letterbox is the look): declared_intent=0.0 ---
    reels clean: 20/20   errors: 0   warnings: 0

--- AS THE ENGINE DEFAULTS (framing_intent.DEFAULT_FRAMING_INTENT = FILL): declared_intent=1.0 ---
    reels clean: 0/20   errors: 20   warnings: 0
    e.g. Reel 01 - geo-is-comprehension-not-position (harvest)
         4 of 4 picture items: the picture fills 31.67% of the frame
         (framing_intent 0.000) where the project declares 100.00%
         (framing_intent 1.000). Delivered rect (0, 656, 1080, 1264),
         declared (-1167, 0, 2246, 1920) in a 1080x1920 frame.

--- NOTHING RESOLVED (no project folder): declared_intent=None ---
    reels clean: 0/20   errors: 0   warnings: 20
```

The gate passes correct output and fails a real defect on the same timelines.
Only the declaration changes between the two runs.

### How the arithmetic is known to be about the reels and not about the tool

Not from its own confidence. `framing_intent.py` records an **independent
measurement, taken before this module existed**: `render_qa`'s occupancy pass
over project 001's real export found the A-roll picture occupying rows
**656..1263** of a 1920-row frame.

This module, on a different project, different source files, different source
resolution, computes rows **656..1264**. Same top row; the bottom row is 607.5
rounded up instead of down.

The instrument reproduces a previously recorded measurement of a real render to
one pixel row.

---

## 6. What the picture actually says, once you look

Measured by decoding every picture window the 20 approved reels play, at 5 Hz,
with the repo's own `subject_framing.load_face_cascade` - the same detector step
1.04 and `render_qa` use.

```
86 windows, 1436.3 s of 3840x2160 h264, sampled at 5 Hz
7,179 frames decoded and run through the Haar cascade
370.5 s wall   =  0.258 s per second of video   =  51.6 ms per frame
```

**Six minutes and twelve seconds to look at every frame-second of every
approved reel.** That is the whole cost of the thing nobody has been doing.

What it found contradicts what the frames suggested, which is why it was
measured rather than asserted:

| | |
|---|---|
| Windows with zero face detections | **0 of 86** |
| Detection ratio: min / median / max | **0.80 / 1.00 / 1.00** |
| `subject_framing.MIN_DETECTION_RATIO` | 0.34 |
| Windows where `subject_center_x` would decline a pan | 2 of 86, and both for `CENTRE_DEADBAND`, not detection failure |
| Median face centre across windows: min / median / max | **0.469 / 0.500 / 0.528** |
| Windows whose subject sits outside `CENTRE_DEADBAND` (0.04) | **0 of 86** |

The prediction going in was that a **frontal** Haar cascade would fail on this
footage, because the speaker spends much of the episode looking down at the
table. It does not. Boxes drawn on six frames - one per source file, both
cameras - land on the face every time, including a frame where the speaker's
eyes are closed and their head is down.

### So the framing question has a measured answer

The face is **32 px of a 320 px sample, 10.0% of the source width**, centred at
0.48-0.53 in every window.

```
a FILL crop keeps      31.6% of the source width, centred
the subject needs      13.0%  (10.0% face + SUBJECT_HEADROOM 0.15 each side)
                       -> the fill crop holds the subject, with room to spare
subject_safe_zoom      7.69   vs fill zoom 3.16
                       -> compile_manifest's backdrop route is not needed here
```

**These reels could ship full-frame and both speakers would survive it.** No
pan, no subject tracking, no backdrop, no preflight pass. The only thing
standing between the captain's reels and a full 1080x1920 frame is that
`build_reel_timeline` never asked `framing_intent` what to do - which is
precisely what F12 now says out loud.

That also retires the recommendation this document was about to make. Running
`step_1_04_temporal_index` over the source would cost ~34 minutes and buy
nothing for framing on this footage, because the subject is already centred and
the detector already sees them.

---

## 7. What is still unanswered, and what it would cost

The brief named five things the pipeline cannot tell. One is now answerable.

| Gap | Status | What it would take |
|---|---|---|
| Whether a cut lands mid-gesture or mid-blink | **Open.** Needs a per-frame motion signal at the cut and a taste judgement about what "mid-gesture" means | ~4.5 min of decode for all 20 reels. The judgement half is a model's, and there is no deterministic half to carry a verdict - so it would have to be **recorded, not gated** |
| Whether a shot holds, or the speaker walks out of frame | **Half built and thrown away.** `face_absent_times` already measures it at 5 Hz and nothing reads it | Computing it for the 86 reel windows costs **6m 12s**, measured. On THIS episode it would report that every shot holds (section 6), so the value is on footage that moves - and the wiring is the same either way |
| Whether both speakers are framed usably after the crop | **Answered.** Section 6: face is 10.0% of source width, centred at 0.48-0.53; a fill crop keeps 31.6% centred | Nothing. The measurement is done and the answer is yes. What is missing is a build that applies a crop at all |
| Whether the first frame is a usable opening image | **Cheap and open.** 6.9 s to decode all twenty (measured) | "Usable" is taste. A deterministic half exists for "is there a picture at all" and it is already covered by `picture_holes`. The rest should be **shown to a model**, which `window_frames` already knows how to do |
| Whether anything on screen contradicts what is said | **Open, and partly free.** Which camera is on screen at each second and who is speaking are both in metadata already | The metadata half costs nothing. But a reaction shot is a legitimate choice, so it can only be **reported**, never gated |

### The recommendations, in order of what they buy per minute spent

**1. Decide what `framing_intent` the podcast actually wants, and make
`build_reel_timeline` read it.** This costs no decode at all. Section 6 measured
that a full-fill centre crop holds both speakers with room to spare, so the
choice between a 31.6% strip and a full 1080x1920 frame is now a decision the
captain can take on the evidence rather than one Resolve's default takes for
them. F12 will hold whichever answer they give.

**2. Show the reel judge a frame.** `window_frames` already puts real JPEGs in a
prompt, and `reel_proposal` already knows the exact `(source_file,
source_second)` of every reel's opening - it is `source_spans[0]`. Twenty
opening frames cost **6.9 seconds**, measured. That closes the opening-image gap
with no new machinery and no threshold anywhere: the model looks, and taste
stays with the model.

**3. Do NOT run the preflight pass for framing.** That was this document's
expected recommendation and section 6 retired it. `step_1_04_temporal_index`
over the seven source files would cost ~34 minutes and buy nothing here: the
subject is already centred, `subject_framing` would decline to move the crop on
84 of 86 windows, and it would be right to. Run it when the footage is a
walk-and-talk, not for this.

### What was deliberately not built

No vision pipeline. No model that watches video. No shot-quality score. No
threshold. F12 decodes nothing and judges nothing - it compares a rectangle the
build produced against a rectangle the project declared, and both are arithmetic
on numbers already on disk.

---

## 8. Numbers in this document that were the instrument, and how that was caught

Two, and both were caught by making the measurement produce something that could
be looked at.

**"0.01 s to seek and decode a 4K frame."** The first timing loop reported
10 ms per frame, flat, on a 60 GB file. It was written as
`set -- $spec; f=$1; t=$2` in zsh, which does not word-split an unquoted
parameter - so `$t` was empty, `ffmpeg -ss ""` failed instantly, and the loop
timed six failures. Rewriting it to write a JPEG and assert the file is
non-empty gave **0.33 s**, which is the number in section 4. The tell was in the
loop's own echo, which printed `@ s` with nothing between.

**"The frontal cascade will not see this speaker."** Predicted from the contact
sheet of the twenty opening frames, where the speaker is often looking down.
Section 6 measured 0 windows out of 86 with no detection and a median detection
ratio of 1.00, and boxes drawn on six frames - one per source file - land on the
face every time, including one with the eyes closed and the head down. The
prediction was wrong and section 2's paragraph now says so.

The general rule both cases obey: a measurement that produces only a number can
be wrong silently. One that produces a file, an image or a box you can look at
cannot.
