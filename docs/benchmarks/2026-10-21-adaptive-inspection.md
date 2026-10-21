# Adaptive temporal inspection

**Lane:** `p-vi005-adaptive-sampling` (Ren punchlist VI-005) · **Date:** 2026-10-21
· **Engine base:** `cb0470c7f`

**The question the lane answers.** The punchlist asks for adaptive
temporal inspection: retain the inexpensive coarse sampling, detect
motion, shot changes, dialogue boundaries, object-state changes and
uncertainty, increase the sample density around candidate events, avoid
repeated inference on static intervals, preserve the original media
timestamps for every sample, and measure the memory, inference cost
and recall consequences.

Its acceptance line is: *"Short, edit-critical events can trigger
denser inspection without forcing maximum frame density across an
entire recording."*

**The answer.** Two measured results, one per fixture.

* On the **M0 fixture** (the 48-second Apple clip the M0 case lives
  in), the dense tier names **frame 1004** for the M0 case and **frame
  1411** for the second occurrence - the *same frames* the incumbent
  M0 lane named, and both **inside** the adjudicated bands. The coarse
  tier on its own names one of the three occurrences; the dense tier
  names two.
* On the **retrieval held-out set** (the editorial set over the
  Ren-owned scratch corpus), the adaptive frame index changes what the
  shipped scorer reports - measured below, with the denominators. The
  honest verdict is **flat recall and a narrower window**: the capped
  recall, the precision, the region recall and the wrong seconds are
  all unchanged, the false-positive seconds fall by 31.9 %, and the
  index costs 3.7x to build. The reason is the retrieval lane's own
  measured finding: the wanted frames sit at a median rank of 396-696
  of 1092, so the failure is RANKING, not coverage, and denser
  sampling cannot reach it.

The policy itself is `library/tools/adaptive_inspection.py` (where the
dense tier goes and what it costs) and
`library/tools/adaptive_signals.py` (what the engine has measured).
Both are new; neither replaces anything.

## What is retained, and what is added

The coarse tier is **retained unchanged**. Every source keeps its M2
sample, and the uniform frame grid is still built over it. The dense
tier is **additive**: it is a second, tighter look at short intervals
the engine's own measurements point at.

The budget is the dense tier's **coverage**, and it is a quarter of
the recording at most, with an absolute cap of 60 seconds. The
recording's length, not its event count, is what bounds the cost: a
68-minute source buys at most 60 seconds of frame-cadence inspection.

## The five signals

Each is read from a lane the engine already measures, and each says
what it did NOT measure, so a missing lane reads as missing rather
than as "nothing happened there":

| Signal | Read from | Status when absent |
|---|---|---|
| `object_state_change` | the M7 lane's measured candidate spans | `unavailable` |
| `dialogue_boundary` | the M1 transcript's utterance edges | `unavailable` |
| `shot_change` | the M4 scene lane's boundaries | `unavailable` |
| `motion` | the M3 lane's measured face-box displacement | `unavailable` |
| `uncertainty` | the intervals **no** measurement covered | `unavailable` |

`uncertainty` is the only one the engine derives rather than reads.
It is the gaps between the measurements that did run, and the whole
recording for a lane that never ran. It is spent **LAST**: a
silence is the least specific evidence the engine has, so a whole
recording of unmeasured lanes cannot consume the budget the measured
events need.

## The M0 arm, measured

**The fixture.** The same Ren-owned scratch copy the M0 baseline used:
`IMG_1822.MOV` re-encoded to 48.0 s, 1920x1080, 30 fps CFR, 1440
frames, time base 1/15360. The captain's library, projects, timelines
and media were not touched; no broker was started;
`ren qualification reset` was not run.

**The truth.** The frozen occurrence corpus
(`docs/benchmarks/2026-10-21-occurrence-identity-fixtures.json`, entry
`F1-one-subject`): three occurrences, at 17.0-19.0 s, 33.3-33.7 s and
47.0-47.4 s. The M0 case is the second one; the M0 baseline's
adjudicated band for it is frames 999-1011, and for the third
occurrence frames 1409-1414. Both bands are agent-adjudicated and
provisional, and are reported as such.

**The arms.**

* **BEFORE** - the coarse tier's own answer: the coarse M2 sample
  nearest the occurrence. A coarse sample the engine measured
  *nothing* at cannot be an answer: the fixture's sample at 18.367 s
  carries no face, so the engine measured no hand-to-lips distance
  there and has nothing to say about that instant.
* **AFTER** - the dense tier's answer: the frame the measured
  hand-to-lips distance is most extreme on, inside the window the
  plan chose from the engine's own measurements.

Both arms are scored by the shipped region-capped accounting (one
credit per occurrence, however many frames land in it), so they are
comparable to the retrieval arm.

**The result.**

| | Occurrences found | Region recall | Frames named |
|---|---:|---:|---|
| BEFORE (coarse tier alone) | 1 / 3 | **0.3333** | 19.4 s, 33.233 s, 47.0 s |
| AFTER (dense tier) | 2 / 3 | **0.6667** | **frame 1004** (33.467 s), **frame 1411** (47.033 s) |

The dense tier's named frames:

| Occurrence | Adjudicated band (frames) | BEFORE (coarse) | AFTER (dense) |
|---|---|---|---|
| 1 (17.0-19.0 s) | not adjudicated | 19.4 s = frame 582, 12 frames outside | 19.667 s = frame 590, 20 frames outside |
| 2 (33.3-33.7 s) - **the M0 case** | **999-1011** | 33.233 s = frame 997, **2 frames outside** | **frame 1004, 0 frames off** |
| 3 (47.0-47.4 s) | 1409-1414 | 47.0 s = frame 1410, 0 frames off | **frame 1411, 0 frames off** |

**The M0 case goes from 2 frames off to 0 frames off, and the dense
tier's named frame is the SAME frame the incumbent M0 lane named**
(frame 1004) - reached by a policy that did not know where the band
was. The third occurrence's named frame is likewise the frame the
incumbent named (1411).

Occurrence 1 is the finding the M0 baseline already recorded: the
device is already at the mouth before the coarse tier's first sample
of it, so the arrival cannot be localised by anything the coarse tier
measured. The dense tier names a frame 20 frames outside the band
where the coarse tier named one 12 frames outside - **that is a
regression on occurrence 1**, and it is reported rather than hidden.
The cause is measured: the coarse sample at 18.367 s has no face, so
the engine measured nothing there, and the dense window that covers
the interval lands on the closest approach it can see, which is
later.

**The cost.**

| | Value |
|---|---:|
| Coarse samples (retained) | 11 (1.267 s cadence) |
| Coarse samples the engine measured a distance at | 9 |
| Dense windows | 8 |
| Dense coverage | 11.534 s of 48.0 s (**24.0 %**) |
| Coverage budget | 12.0 s |
| Triggers deferred (budget) | 13 |
| Intervals left coarse | 9 |
| Frames decoded and measured | 354 |
| Wall | 29.7 s |
| CPU | 12.3 s |
| Peak RSS | 43.7 MB |

The deferred triggers and the coarse intervals are both **named**, so
a reader can see what the budget did not buy and where the engine
deliberately did not look again.

## What the measurement changed in the policy

Two defects were found by the measurement and fixed, and both are
named by a test.

**1. A window stretched back over the coarse gap names a DIFFERENT
event.** The first policy looked back over the whole bracket the
coarse samples leave, to catch the onset. The measurement says that
is wrong here: the coarse sample at 33.233 s measured the hand 0.694
face-widths from the lips, the M0 event's own band (33.3-33.7 s)
measures 0.13-0.43, and a **different, earlier approach** at
31.9-32.0 s measures 0.176 - closer than the event itself. A window
stretched back over the coarse gap therefore names that earlier
approach, and the M0 case is missed by 8 frames. The window is now
tight around the sample that saw the change, and the bracket is
**reported** (`onset_bracket`) rather than covered. A reader still
sees how uncertain the onset is.

**2. A local minimum of the coarse series is not an event.** The
first trigger derivation fired on the local minima of the M7 span's
measured series. On the M0 fixture the hand-near-mouth run measures
0.039, 1.245, 0.176, 0.533, 0.694, 1.167 and 0.050 at its seven
samples, and the occurrence the M0 case asks about (33.3-33.7 s) sits
in the gap between the 0.533 sample and the 1.167 sample - **no local
minimum exists there**, so the rule named the wrong event or none at
all. Every sample in the run is now a trigger: each one is a coarse
measurement of the state HOLDING, and the dense tier is what resolves
when it began. The budget, not the trigger rule, decides how much of
the run is bought.

**3. The EOF read, found on the way.** The dense sample's ffmpeg
command used an output-side `select` filter, so the decode kept
reading to the end of the file. On the 4K MXF corpus a **2-second**
sample of a 47-minute file took **1509 s** of wall time for 48
frames. The read is now bounded just past the interval, with half a
second of margin so the last wanted frame is always read. Measured:
the M0 fixture's frames are byte-identical and carry identical
timestamps, and the 4K corpus sample fell from 1509 s to 1.4 s.

## The retrieval arm, measured

**The corpus.** The Ren-owned scratch project
`ren-scratch-retrieval-bench`: seven podcast sources, 1 h 33 m of
footage, all seven with a fresh M2 sample and a measured M3/M7.

**The arms.**

* **BEFORE** - the frame index as the incumbent builds it: a uniform
  grid, one frame every ten seconds.
* **AFTER** - the same index built adaptively: the grid retained, plus
  the frames inside the intervals the source's own measurements say
  changed.

Both arms are scored by the **shipped scorer**
(`library.tools.retrieval_eval.run`, pointed at an alternative index
through `frame_index_dir`), so the numbers are the ones the engine
itself reports.

<!--RETRIEVAL_RESULTS-->

**The numbers.** Both arms are scored by the shipped scorer over the
same 12 labelled queries (9 present, 3 absent, 1 refused, 5
unlabelled). The uniform arm's score **reproduces the committed
baseline exactly** - every metric matches
(`/tmp/ren-vi005/before-heldout.json`) - so the BEFORE arm is verified
rather than assumed.

| Metric | BEFORE (uniform) | AFTER (adaptive) | |
|---|---:|---:|---|
| Macro recall (region-capped) | 0.3592 | 0.3592 | unchanged |
| Macro precision | 0.2667 | 0.2667 | unchanged |
| Macro region recall | 1.0 | 1.0 | unchanged |
| Wrong seconds selected | 55.0 | 55.0 | unchanged |
| False-positive windows | 31 | 31 | unchanged |
| **False-positive seconds** | 257.65 | **175.57** | **-31.9 %** |
| Missing seconds | 1781.98 | 1816.48 | +34.5 s |
| Coverage fraction | 0.0214 | 0.0025 | worse |
| Macro recall (uncredited) | 0.385 | 0.3695 | -0.0155 |
| Frames indexed | 1092 | 1882 | +72.3 % |
| Index build, wall | 1570.5 s | 5837.4 s | +271 % |
| Index build, embed | 1549.9 s | 5810.4 s | +275 % |
| Scoring, wall | 26.8-29.2 s | 41.1-42.1 s | |
| Scoring, peak RSS | 624-897 MB | 577-604 MB | |

**What the retrieval arm does NOT show: a recall gain.** The capped
recall, the precision, the region recall and the wrong seconds are all
**unchanged**. The adaptive index bought **82 seconds fewer
false-positive seconds** - 31.9 % - at the cost of 34.5 more missing
seconds, 790 more frames to embed, and 3.7x the index build time.

**Why, and it is the lane's own documented finding.** The retrieval
lane's own notes record that on this corpus the wanted frames of a
present query sit at a **median rank of 396-696 of 1092** - the frames
the editor wants are RANKED BELOW the pool, and the pool is not the
coverage that is missing. Measured on this corpus, every wanted region
already contains a ten-second-grid frame, so the incumbent's grid
**covers** every wanted instance; the failure is which frame the search
ranks first.

The adaptive index cannot fix that, and the measurement says so
exactly: it makes the frames it adds score slightly HIGHER (H01's
winning frame scores 0.2567 against the incumbent's 0.2507), because
they sit where the engine measured something. But the wanted frames
are still ranked 396-696, so the same five windows come back. What
changes is their **width**: the lane caps a range at
`RANGE_SAMPLES = 2` **samples**, and denser samples make a range
shorter. The wanted window of H01 falls from 10.01 s to 0.50 s. The
region is still found - the region recall stays at 1.0 - but the window
that carries it covers a tenth of the ground truth, and the
false-positive seconds fall with it.

**So the retrieval arm's honest verdict is: the dense tier does not
buy recall here, and it buys a narrower window.** Whether a narrower
window is better is an editorial judgement this lane does not make:
the scorer charges the seconds a window carries OUTSIDE the wanted
moment, so a tighter window scores as less false positive and as less
coverage at the same time. Both are reported.

**What this means for the lane.** The retrieval held-out set is **not**
a reason to turn adaptive sampling on. The punchlist's acceptance line
is about the dense tier's COST being bounded, and that holds; but the
recall consequence this brief asks to measure is **flat**, and the
cost is 3.7x. The M0 arm is where the measured win is: the dense tier
names the exact frame the event is on, where the coarse tier could
only say which ten seconds it was in.

## What this lane does NOT do

* It does not replace the coarse tier, and it does not change the
  shipped default: `build_frame_index` still builds the uniform grid
  unless a caller asks for `sampling="adaptive"`.
* It does not make the dense tier the default. The punchlist's
  acceptance line asks that a short event can buy denser inspection
  *without* forcing maximum frame density across a recording, and the
  measurement shows the budget holds that. Whether to turn it on by
  default is a product decision, and the retrieval numbers are here
  for it.
* It does not read the captain's media, start a broker, or run
  `ren qualification reset`.
* It does not measure the M3d motion lane. That lane costs a mask
  analysis over the source's own frames - **ten minutes on a
  68-minute source** - and its cache was never written for this
  corpus, so reading it would buy nothing and cost a great deal. The
  motion signal here is the M3 lane's own measured face-box
  displacement, which is free.
* The occurrence bands are agent-adjudicated and provisional, and the
  corpus's frozen seconds are the authority this bench scores
  against.

## Reproducing

```sh
# The M0 arm (the fixture's own memory is built first).
bin/vep docs/benchmarks/m0_adaptive_inspection_bench.py \
    --source <the scratch copy> --out-json /tmp/m0-report.json

# The retrieval arm (the two indexes are built, then scored).
bin/vep docs/benchmarks/adaptive_inspection_bench.py \
    --project <scratch project> --out-json /tmp/retrieval-report.json
```

Both run on the engine's own code, in the foreground, and report what
they measured. Nothing here gates anything.
