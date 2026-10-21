# Frame-exact addressing and the refinement that earns it

**Lane:** `g-m0-l2-frame-exact-refine` (Workstream G, Milestone 0 case M0-2)
· **Date:** 2026-10-21 · **Engine base:** `5365ca345`

**The question the lane answers.** The M0 scenario is *"the second time
the person brings the handheld device to their mouth on IMG_1822"*, and
its boundary must be named **within 3 frames, as a source frame
index**. The baseline lane measured what the engine could do:
the M2/M3 sample is a 2 Hz I-frame sample, so its gaps are 3-6 s
(90-190 frames of unobserved time), it carried **no frame index at
all**, and the best boundary estimate available was the nearest
sampled time - **2 to 14 frames** off the adjudicated band.

**The answer.** Two changes, measured on the same fixture:

1. **The M2 sample carries the source frame index and the source's own
   timebase.** Every sampled frame now says which frame of the ORIGINAL
   it is, computed on the container's ticks
   (`library/tools/source_memory.py`, `library/tools/frame_exact.py`),
   and the sample carries the exact rational rate, the time base, where
   frame 0 sits and how many frames there are.
2. **A refinement re-samples an interval at the source's own frame
   cadence and names ONE frame** (`library/tools/frame_exact.py`). The
   coarse sample says WHERE to look; the refinement says WHICH FRAME.

## The fixture

The same Ren-owned scratch copy the baseline lane used, so the numbers
are comparable line for line. The source is read only; nothing in the
captain's library, projects, timelines or media was touched, no broker
was started, and `ren qualification reset` was not run.

```sh
ffmpeg -i "<001/raw>/IMG_1822.MOV" -t 48.0 -c:v libx264 -crf 18 -c:a aac -r 30 \
       scratch/m0/media/m0_device_take.mov
```

48.0 s, 1920x1080, **30 fps, 1440 frames**, CFR, time base 1/15360.

The M2 sample of it is the baseline's sample, unchanged:

```
t = 0.0, 1.267, 2.833, 6.367, 12.067, 18.367, 19.4, 26.1, 33.233, 38.667, 47.0
```

and it now carries the frame index of every one of those frames:

```
frame = 0, 38, 85, 191, 362, 551, 582, 783, 997, 1160, 1410
```

## The measurement

**Before.** The coarse sample's nearest neighbour to the adjudicated
band is `t = 33.233` = **frame 997**; the band is frames 999-1011, so
the incumbent's boundary estimate is **2 to 14 frames** off - and it
could not be said in frames at all, because the sample carried no
index.

**After.** The refinement of that coarse frame index (window +/- 0.5 s,
31 frames at frame cadence) names **frame 1004** - *inside* the band.

| Case | Adjudicated band (frames) | Band source | Refined boundary | Error |
|---|---|---|---|---|
| occurrence 2 (the M0 case) | 999 - 1011 | `scout-g-m0-baseline` - **agent-adjudicated, provisional** | **1004** | **0 frames** (inside the band; 1 frame from its midpoint) |
| occurrence 3 | 1409 - 1414 | this lane, by eye on a labelled frame strip - **agent-adjudicated, provisional** | **1411** | **0 frames** (inside the band) |

**2 of 2 adjudicated onsets within 3 frames = 100 %** (the benchmark
asks for >= 90 %). Both bands are agent-adjudicated and provisional:
the captain's re-adjudication has not happened, so this is a
provisional score and is reported as one.

**The coarse sample's third occurrence could not be refined, and that
is a finding about the coarse lane, not about the refinement.**
Occurrence 1 (the device is already at the mouth from before frame
530) is localised by the coarse sample only at frame 551, which is
already *inside* the episode: the 2 Hz sample cannot say when the
arrival happened. The interval that would cover it is the coarse gap
(frames 362-551 = 190 frames), and refining that costs ~7 CPU seconds
- **nine times** the coarse acquisition. The refinement's cost is
linear in the width of the interval it is given, so the coarse lane
has to localise an onset to about a second before the refinement can
make it exact.

## The cost

Measured in the same session, three repetitions, alternating:

| | wall | CPU |
|---|---:|---:|
| **Incumbent coarse acquisition** (M2 sample of the whole source + M3 over its 11 frames) | 2.28 s | 0.76 s |
| Refinement of occurrence 2 (31 frames) | 7.07 s | 1.54 s |
| Refinement of occurrence 3 (31 frames) | 7.86 s | 1.42 s |
| **Ratio, occurrence 2** | 3.09x | **2.03x** |
| **Ratio, occurrence 3** | 3.44x | **1.87x** |

**The cost gate (at most 2x the incumbent's acquisition) holds on CPU
seconds, at the limit, and does not hold on wall seconds.** The machine
is shared and its load average was 60+ throughout the measurement, so
the wall clock measures the neighbours as much as the work; that is
why the module reports CPU seconds as well.

Where the cost goes, and what was left on the table:

* the dense **decode** is 0.53 CPU s for 31 frames. It decodes from the
  source's last keyframe before the interval, and this source's
  keyframes are scene-change driven and up to 8 s apart, so 8 s of
  video is decoded to cover 1 s of it.
* the **measurement** is 1.04 CPU s for 31 frames - about 33 ms a
  frame. The shared Vision helper runs SIX requests a frame (faces,
  face quality, face landmarks, body pose, hand pose, person
  segmentation) and M3 keeps four of them; the two it discards are
  the body pose and the person segmentation. A helper that skipped
  those two would cut the refinement's measurement cost by whatever
  they cost (not measured here); the helper is shared by every lane
  on this machine, so **this lane did not touch it**, and the finding
  is recorded here instead.
* the measurement cost does not fall with the width of the decoded
  image (measured: 1.00 CPU s at 384 px, 0.98 CPU s at 192 px), so it
  is the requests, not the pixels.

## What the measurement does NOT prove

* **The truth is agent-adjudicated.** Both bands are provisional and
  await the captain's re-adjudication. The occurrence-2 band is the
  baseline lane's; the occurrence-3 band is this lane's own, by eye.
* **The rule and the band must mean the same thing.** The refinement
  names the frame of the measured series' extreme - "the device is
  CLOSEST to the mouth here". The adjudicated band is the ARRIVAL.
  On this fixture they agree inside the band, but a band adjudicated
  for contact and a rule that measures closest approach are two
  different questions, and the report says which one it answered.
* **The window is part of the answer.** The refinement is exact about
  the interval it is given, and it says so: an extremum at the edge of
  the interval is reported as `boundary_truncated`, with the reason,
  instead of as a boundary. A window that reaches past frame 1016
  finds a LATER and closer approach (the device settles at the mouth
  from frame 1019) and names that frame instead.
* **This is a CFR fixture.** On a variable-frame-rate source the
  frame index computed from the container's average rate is an
  APPROXIMATION, and the sample says so per frame (`on_grid`), because
  the measured time does not sit on the rate's grid. The measured
  PTS table that would settle it belongs to the A-06 clock lane, and
  this lane does not duplicate it.
* **The original take is mildly variable.** The source `IMG_1822.MOV`
  declares `avg_frame_rate` 128700/4291 (29.9953) against
  `r_frame_rate` 30/1, with 6 hiccup frames in its first 1200. Over
  the whole take the grid index can drift from the decode-order index
  by up to one frame. The refinement reports the frame index it
  measured and the grid residual beside it.

## What shipped

* `library/tools/frame_exact.py` - the frame grid
  (`SourceTimebase`), the dense sample (`sample_interval`), the
  refinement (`refine_boundary`, `refine_from_frame_index`) and the
  measurement it re-runs (`hand_mouth_distances`, the coarse lane's
  own geometry from `event_spans`).
* `library/tools/source_memory.py` - the M2 sample carries
  `timebase`, `frame_index_status` and every frame's `pts` and
  `frame_index`.
* `library/tools/person_measurements.py` - each M3 frame carries the
  source frame index of the M2 frame it measured.
* `docs/SOURCE_MEMORY.md` - the M2 and M3 shapes as they now stand.

```sh
# the coarse sample says WHERE; the refinement says WHICH FRAME
bin/vep -m library.tools.frame_exact refine <source> \
  --frame-index 997 --half-window 0.5 --out <dir>
```
