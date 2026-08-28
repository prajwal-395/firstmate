# Subject masking and tracking - what SAM 2.1 measured on 001

**Status: MEASURED, and the step stays UNWIRED.**

This answers the captain's question of 2026-08-28 - "are we able to use any of the
magic mask features... blur out the background... colorgrading specific areas...
rotoscope out something" - and closes the two halves of #162 that were open:
mask quality on this footage, and cost per clip.

It is a measurement, not a wiring. `object_segmentation` (1.06) has no DAG node
before this document and has none after it. What changed is that its recorded
`unwired_reason` in `library/tools/project_layout.STEPS` now states what was
measured rather than what was assumed.

    library/tools/analysis/object_segmentation.py   SAM 2.1 wrapper, TrackedObject, find_match_cut_candidates
    library/steps/step_1_06_object_segmentation/    the unwired step
    tests/test_object_segmentation.py               the unit tests
    docs/RULE_EVIDENCE.md#sam-2-1-was-asked-for-a-config-that-does-not-exist

Resolve's own Magic Mask is not in the picture at all: `CreateMagicMask` returns
False for every mode and is recorded as withdrawn in
`library/tools/neural_engine.py`. Everything below is the Fusion route.

## 1. Why it had never run

    hydra.errors.MissingConfigException: Cannot find primary config
    'sam2.1_hiera_small.yaml'. Check that it's in your config search path.

**Neither environmental nor a packaging gap. The string in our code was wrong.**

`sam2` ships the config. `pip show`'s own tree has it at
`sam2/configs/sam2.1/sam2.1_hiera_s.yaml`, and the package's own
`HF_MODEL_ID_TO_FILENAMES` table maps `facebook/sam2.1-hiera-small` to exactly
that path. Two things were wrong with `"sam2.1_hiera_small.yaml"`:

- **the name**: the config is named for the SIZE (`_s`), the checkpoint for the
  WORD (`_small`). `sam2.1_hiera_small.yaml` is the checkpoint's stem with the
  wrong extension - a file that exists under no name.
- **the path**: `sam2/__init__.py` calls `initialize_config_module("sam2")`, so
  hydra's search root is the package. A config inside it must be addressed
  `configs/sam2.1/...`, and the bare name was missing that prefix.

The checkpoint was wrong the same way: `ckpt = "sam2.1_hiera_small.pt"` is a
bare relative filename, resolved against the process CWD, and nothing ever
downloaded it.

**The fix names the MODEL and lets `sam2`'s own table answer both halves** -
`build_sam2_video_predictor_hf(SAM2_MODEL_ID)` with
`SAM2_MODEL_ID = "facebook/sam2.1-hiera-small"`. The checkpoint comes from the
HuggingFace hub on first use and is cached. No config was vendored and no model
was substituted: this is SAM 2.1-hiera-small, the model the module always named.

## 2. MPS silently returns worse masks, so the two halves run on different devices

This is the finding with consequences beyond this module.

`SAM2AutomaticMaskGenerator` sweeps a grid of point prompts over the first frame
and keeps what clears `pred_iou_thresh=0.8` and `stability_score_thresh=0.95`.
On 001's clip_001 first frame:

| device | masks kept (default filters) | masks before filtering | predicted IoU min/median/max |
|---|---|---|---|
| MPS | **1** | 351 | 0.002 / **0.092** / 0.965 |
| CPU | **5** | 175 | 0.000 / **0.333** / 0.974 |

*(16x16 prompt grid; at the default 32x32 grid the same frame gives 1 mask on MPS
against 7 on CPU.)*

The maxima agree, so a prompt landing squarely on an object still scores. It is
the broad sweep that degrades on MPS, and the confidence filters then discard
nearly all of it. **Nothing raises and nothing warns** - the generator returns a
short list and the caller cannot tell a sparse frame from a degraded backend.
That is the failure mode this repository already knows from Resolve's scripting
proxies: a call that returns something plausible is not evidence it worked.

Propagation is a different story. Handed **identical** seed masks and asked to
track them through 001's clip_008, MPS and CPU agree on every frame where the
track holds, and MPS is 3.8x faster:

| | per-frame | per-object IoU against the other device, while tracking holds |
|---|---|---|
| MPS | **1.57s** | 0.88 - 0.98 |
| CPU | 5.98s | (reference) |

They diverge only on which frame each gives up on an object, which is past the
point the track is usable.

**So the generator runs on the CPU and propagation runs on MPS**, as
`GENERATOR_DEVICE` and `PROPAGATION_DEVICE` in `object_segmentation.py`. The
generator runs ONCE per clip on ONE frame, so being right costs a bounded 15-35s
per clip; propagation is per frame, and is where a 3.8x factor is worth having.

## 3. Sampling denser does not rescue a lost track

`segment_clip(sample_fps=...)` decides how many frames are extracted. Run three
ways on clip_008 (IMG_1813, 9.1s):

| sample_fps | frames | wall | per frame | largest object held | small objects last seen |
|---|---|---|---|---|---|
| 2.0 | 18 | 75.2s | 4.18s | 12/18, to 8.5s | 2.0 - 5.5s |
| 6.0 | 54 | 202.2s | 3.74s | 18/54, to 5.7s | 0.2 - 4.3s |
| 15.0 | 136 | 528.0s | 3.88s | 94/136, to 9.0s | 2.3 - 4.5s |

Two things fall out, and both matter for whether this is affordable:

- **Cost is per FRAME and flat**, 3.7-4.2s whatever the rate. So cost is linear
  in `sample_fps`: 15 fps costs 7x what 2 fps costs for the same clip.
- **The small tracks die at 2-4.5 seconds at EVERY rate.** Denser sampling buys
  temporal resolution on the tracks that survive; it does not extend the ones
  that do not. The tracker is losing these objects to the footage, not to the
  gap between samples.


## 4. The run: all 17 clips of 001, 807 seconds

`sample_fps=2.0`, the value step 1.06 passes. Kind is measured, not eyeballed -
`face_presence` from step 1.04 at 5 Hz, SELFIE at >=50% of samples carrying a
face. "Subject" is the tracked object covering the most frame area.

| clip | kind | dur | frames | wall | s/frame | xRT | objs | MB | subject track |
|---|---|---|---|---|---|---|---|---|---|
| clip_001 | no-face | 3.6 | 7 | 36.1 | 5.16 | 10.1 | 7 | 0.08 | TRACKED 7/7 |
| clip_002 | no-face | 17.1 | 34 | 125.5 | 3.69 | 7.3 | 9 | 0.75 | PARTIAL 12/34, unbroken 6.0s |
| clip_003 | SELFIE | 27.8 | 56 | 206.5 | 3.69 | 7.4 | 10 | 1.58 | TRACKED 56/56 |
| clip_004 | no-face | 26.8 | 54 | 169.0 | 3.13 | 6.3 | 7 | 0.61 | PARTIAL 24/54, unbroken 4.0s |
| clip_005 | no-face | 7.9 | 16 | 73.8 | 4.61 | 9.3 | 7 | 0.18 | PARTIAL 7/16, unbroken 3.5s |
| clip_006 | no-face | 22.9 | 46 | 77.5 | 1.69 | 3.4 | 3 | 0.88 | TRACKED 46/46 |
| clip_007 | part-face | 139.1 | 278 | 250.0 | 0.90 | 1.8 | 2 | 4.69 | TRACKED 278/278 |
| clip_008 | no-face | 9.1 | 18 | 92.4 | 5.13 | 10.2 | 9 | 0.50 | PARTIAL 12/18, unbroken 0.5s |
| clip_009 | part-face | 45.9 | 92 | 316.9 | 3.45 | 6.9 | 10 | 3.92 | TRACKED 92/92 |
| clip_010 | SELFIE | 6.3 | 13 | 66.7 | 5.13 | 10.6 | 10 | 0.67 | TRACKED 13/13 |
| clip_011 | SELFIE | 188.6 | 377 | 1347.9 | 3.58 | 7.1 | 10 | 19.11 | TRACKED 377/377 |
| clip_012 | SELFIE | 41.5 | 83 | 322.7 | 3.89 | 7.8 | 10 | 3.89 | TRACKED 83/83 |
| clip_013 | SELFIE | 85.5 | 171 | 650.6 | 3.80 | 7.6 | 8 | 6.70 | TRACKED 171/171 |
| clip_014 | no-face | 4.7 | 9 | 100.2 | 11.13 | 21.3 | 10 | 0.25 | PARTIAL 5/9, unbroken 1.0s |
| clip_015 | SELFIE | 52.5 | 105 | **1934.8** | **18.43** | **36.8** | 9 | 4.38 | TRACKED 105/105 |
| clip_016 | SELFIE | 41.9 | 84 | 204.0 | 2.43 | 4.9 | 6 | 4.14 | TRACKED 84/84 |
| clip_017 | part-face | 85.8 | 172 | 584.0 | 3.40 | 6.8 | 10 | 6.00 | TRACKED 172/172 |
| **TOTAL** | | **807.0** | 1615 | **6558.7** | med **3.69** | **8.1** | | **58.3** | |

**109.3 minutes of wall clock for 13.4 minutes of footage.**

- **clip_015's 18.43 s/frame is not SAM's cost.** The machine went into swap
  thrash - 38.0 GB of 38.9 GB swap used, 866 MB free, `pmset -g therm` recording
  no thermal warning - and the very next clip recovered to 2.43 s/frame. Excluding
  it, the run is **4624s for 754s = 6.1x realtime**. Both numbers are real; the
  8.1x is what a sweep actually costs, the 6.1x is what the model costs.
  The memory is held where `ps` cannot see it: the worker's RSS was 4.6 GB while
  the system had lost 38 GB, because Metal/MPS allocations do not appear in
  process RSS and `unload_model`'s `torch.mps.empty_cache()` does not get it all
  back across 17 load/unload cycles.
- **Cost tracks TRACKED-OBJECT COUNT, not clip length.** 2 objects is 0.90
  s/frame, 3 is 1.69, 6 is 2.43, 10 is 3.4-3.9. The 139-second clip_007 was the
  CHEAPEST clip in the run per frame and finished in 1.8x realtime.
- **Short clips are all overhead.** clip_014 (4.7s) cost 21.3x realtime because a
  model load (~19s) and a CPU generator pass (~30s) are charged per clip whatever
  its length. `managed_model` reloads the model for every clip - 17 loads in this
  run.

### The mask data is 58.3 MB, and it must not go in the state file

4.3 MB per minute of footage at 2 fps; one clip (clip_011, 188.6s) is 19.1 MB on
its own. `pipeline_data.json` for this project is 4.5 MB, so the masks are **13x
the entire pipeline state**. The step already does the right thing - per-clip
files with only a filename in the state - and that shape must not be relaxed.

Two things follow if this is ever wired. At 30 fps rather than 2 the same footage
is **875 MB**, and RLE of a raster is the wrong storage for it - a per-frame
polygon or a compressed matte movie is an order of magnitude smaller and is what
Fusion wants to read anyway.

## 5. What the masks actually look like

Pixel counts do not tell you whether a matte is usable, so masks were drawn back
onto their own frames with the contour outlined. **The verdict split cleanly by
whether a person is in shot, and NOT by how shaky the shot is:**

| kind | n | TRACKED | PARTIAL | median unbroken subject track |
|---|---|---|---|---|
| SELFIE | 7 | **7** | 0 | 42.0s |
| part-face | 3 | **3** | 0 | 86.0s |
| no-face | 7 | 2 | 5 | **3.5s** |

**Every clip with a person in it tracked that person for the entire clip.** That
includes the two longest in the project - clip_011 at 188.5 seconds unbroken and
clip_007 at 139.0 - and it includes the shakiest.

This inverts the expectation the task carried. Shake is not what defeats SAM 2;
**framing** is. On a selfie the subject fills the frame, so the largest region the
automatic generator finds on frame 0 IS the person, and SAM 2's memory attention
holds it through walking, camera roll, changing backgrounds and daylight-to-dusk.
On the wide no-face clips the generator seeds on arbitrary background regions -
a car window, a patch of road - which drift and are lost in 0.5 to 6 seconds.

**Edge quality is a separate axis from track survival, and it follows CONTRAST:**

- **clip_007** (139s walking, daylight, high contrast): the best of the run. The
  contour follows the hairline, collar and shoulder, and at t=46s it correctly
  separates hair from bare tree branches.
- **clip_013** (85.5s, the shakiest long selfie, sunset): holds all 171 frames;
  the edge softens on motion-blurred frames where it cuts through smeared hair.
- **clip_016** (41.9s, dim parking garage, the shakiest clip in the project):
  holds all 84 frames, but the edge is the **worst** of the selfies - ragged and
  speckled along the hair boundary, where dark hair meets a low-contrast grey
  ceiling.
- **clip_003** (27.8s, mid-shot in a car): held 56/56, but tracked **exposed skin**
  - face, hands, forearms - with the shirt and most of the hair outside the mask.
  When the subject does not fill the frame, "largest region" stops meaning "person".

**The masks carry speckle, and the cause is permanent on this machine.** SAM 2's
`fill_hole_area` post-processing calls `sam2._C.get_connected_componnets`, whose
only source is `sam2/csrc/connected_components.cu` - CUDA. There is no `.so` in
the wheel and none can be built on Apple Silicon, so every run prints

    UserWarning: cannot import name '_C' from 'sam2'
    Skipping the post-processing step due to the error above.

and every mask keeps its pinholes. A `cv2` connected-component pass would replace
it for almost nothing; installing something will not.

## 6. Verdicts on the captain's three uses

Each is judged against the masks above. **None of this is built** - deciding
whether it is worth building is what this document is for.

### What all three need from Fusion

- **A mask has to become a file.** Fusion reads a matte as an image sequence
  through a `Loader`, and `library/tools/fusion/nodes.py` has no Loader - every
  node it builds is generated (Background, EllipseMask, Transform, Merge), and
  `EffectMask` is wired in exactly one place, the vignette. So: an RLE-to-image
  sequence writer, a `Loader` node type, and an `EffectMask` wire into whichever
  tool is being limited.
- **Source resolution and the played window.** Both are already rules here -
  AGENTS.md §5 "Frame mapping" and "Background sized to the delivery frame". A
  matte sequence obeys them or it lands on the wrong frames.
- **The rate has to match the timeline.** Masks are sampled at 2 fps against a
  30 fps timeline. §3 measured that denser sampling costs linearly and rescues
  nothing, so this is a straight 15x bill on a cost that is already 6.1x realtime.

### Region-specific colour grading - YES, and it is the one to build first

The measured masks support this today:

- **Every clip with a person tracked that person for the whole clip**, which is
  what a grade needs: one matte, held for the shot's duration.
- **A grade tolerates a soft or slightly wrong edge.** A few pixels of spill on a
  skin-tone correction is invisible. The same error on a composite is a halo.
- **A grade tolerates a slow matte.** A hue push held over an 85-second shot does
  not chatter, so the 2 fps sampling hurts least here - and it is the only one of
  the three where the 15x resampling bill can be declined.
- Even clip_003's skin-only matte is *useful* here; it is what a colourist would
  isolate by hand.

Fusion carries it natively: `ColorCorrector` or `BrightnessContrast` with the
matte on `EffectMask`. Note this would be the FUSION half of the house look, not
the CDL half - `SetCDL` is whole-frame by definition and has no mask input (§12).

### Background blur - PROBABLY, and the edge is the gate, not the track

The track is no longer the problem: 10 of 10 person clips held their subject for
the full duration, including the shakiest. Two things stand between that and a
shippable blur:

- **The edge has to survive the shot's contrast.** clip_007 would look right.
  clip_016 - dark hair against a dim grey ceiling, the ragged speckled boundary -
  would read as a broken effect, and it is the shakiest and dimmest of the A-roll.
  The CUDA-only hole filling above is part of this, and is cheap to replace.
- **2 fps will chatter.** A matte that updates every 15th frame on a moving hair
  edge is exactly what #162 rules out - "a mask that flickers at the edges is
  worse than no mask". This needs 30 fps sampling, and therefore the 15x bill.

Worth knowing before spending any of that: **this pipeline already ships a
background blur** by another route - the blurred plate behind letterboxed footage,
`fx.subject_backdrop` on the `framing_backdrop` route (§10.3). It needs no matte
because it blurs a copy of the whole frame. A SAM matte buys the *sharper* version
of an effect that already exists, which is a smaller prize than it first appears.

### Rotoscoping something out - NO

Removal needs three things and this delivers one:

- **The matte** - yes, on person clips.
- **Choosing WHAT goes.** There is no subject prompt. `segment_clip` keeps the ten
  largest regions the automatic generator finds on frame 0, so on the no-face
  clips the "subject" is a car window, and an editor cannot name the thing they
  want gone.
- **Something to put behind it.** A clean plate or inpainting. This repository has
  no answer to that at all, and SAM does not provide one.

The no-face clips are also where tracking is weakest - 5 of 7 PARTIAL, median
unbroken track 3.5 seconds - and they are exactly the B-roll where an unwanted
object is most likely to need removing.

### The one change that would move all three

**Seed the tracker from the face box instead of from "the ten biggest blobs".**
`step_1_04_temporal_index.compute_face_presence` already emits a face box at 5 Hz,
and SAM 2's `add_new_points_or_box` takes exactly that. That converts an
arbitrary-region tracker into a subject tracker, fixes clip_003's skin-only matte,
gives rotoscoping a way to name its target, and would cut cost sharply - tracking
1 object instead of 10 is the difference between 0.90 and 3.9 s/frame measured
above. It is a small change and it is the prerequisite for all three uses.

## 7. find_match_cut_candidates(), exercised

Nobody had seen its output. On clip_001 against clip_002 - both portrait, so they
clear its same-resolution guard:

    clip_001: res=(1920, 1080) frames=7  objects=7 mask_entries=27
    clip_002: res=(1920, 1080) frames=34 objects=9 mask_entries=109
    comparisons: 27x109 = 2,943

    find_match_cut_candidates -> 0 candidates above its 0.5 IoU threshold,
    2.6s (0.885 ms/comparison)

    same silhouette IoUs, ranked, with no threshold applied:
      IoU=0.326  clip_001 f6 (3.0s obj_2) <-> clip_002 f6 (3.0s obj_2)
      IoU=0.302  clip_001 f6 (3.0s obj_2) <-> clip_002 f6 (3.0s obj_3)
      IoU=0.254  clip_001 f6 (3.0s obj_2) <-> clip_002 f7 (3.5s obj_2)
      ...
    2,943 pairs: max=0.326 p99=0.127 median=0.0000  above 0.5: 0

**It works and it is honest** - it computes a sensible ranking and correctly
reports no match cut between these two clips.

**But it cannot be run on this project's real output.** Three limits, all
measured:

- **It is quadratic in mask entries and decodes a full raster inside the inner
  loop.** Counted off the run's own output, clip_011 carries 3,417 mask entries
  and clip_013 carries 870, so that ONE pair is 2,972,790 comparisons - **43.8
  minutes** at the measured 0.885 ms. All 136 pairs of this project is 34.7
  million comparisons, **about 0.4 days**, to answer a question a human answers
  by scrubbing.
- **A same-resolution guard splits the project in two.** It returns `[]`
  immediately unless both clips share a resolution, and 001 is 7 clips at
  1920x1080 portrait and 10 at 1080x1920 landscape. No portrait clip can ever be
  compared against a landscape one.
- **It compares silhouettes with no notion of what they are.** With the current
  seeding, a "match" can be a car window against a road sign.

Making it usable means comparing shape descriptors rather than rasters, and
comparing far fewer candidate frames. That is a rewrite, not a tuning.

## 8. Recommendation

**Keep the step unwired, and do not build the Fusion connection yet.**

The capability is real - better than expected on this footage, and the answer to
the captain's question is "yes, through Fusion, and the tracker holds the subject
across your shakiest walking selfies". But three things should land before any of
it reaches a timeline, in this order:

1. **Seed from the face box** (§6). Small, and everything else depends on it.
2. **Decide what consumes a mask**, which is #162's question 3 and still open.
   Wiring a producer nothing reads is the defect #187 exists to prevent.
3. **Then pick ONE use** - region grading is the cheapest and most robust - and
   build the Loader/EffectMask route for it alone.

The cost is the standing objection. **6.1x realtime** means 001's 13.4 minutes of
footage costs about 80 minutes of machine, on a machine that already goes into
swap thrash doing it, that other work shares, and where stabilization is already
the memory ceiling (§5). At the 30 fps a blur would need, it is 15x that. This is
not something to run on every project by default; it is something to run on the
clips a human has already decided deserve it.
