# The keys, not the hand: the ordinal does not move, and here is why

**Lane:** `g-m0-l8-keys` · **Date:** 2026-10-22 · **Fixture:** the whole
85.82 s take of Apple 001 `IMG_1822`, on the scratch byte copy
`/tmp/m0-e2e/media/IMG_1822_keys_take.MOV` (md5
`f3af6808c314cadefc15218baf273037`, identical to the read-only source) ·
**Ground truth:** the six adjudicated arrivals, unchanged
(`docs/benchmarks/2026-10-21-m0-e2e-whole-clip.md`), still
agent-adjudicated and provisional.

**The question this lane was asked.** PR 3170 found 12 occurrences of a
repeated arrival on this take, and the ordinal was off: the engine
measures the HAND and the truth measures the KEYS. Use the object
identity of PR 3134 to measure the keys themselves, and answer one
question: **is the SECOND key arrival the second occurrence, and what
is its frame error, before vs after?**

**The answer: NO - and the answer is the same before and after.**

| | before (the HAND) | after (the KEYS) |
|---|---|---|
| occurrences the class carries | 12 | **9** |
| the class's SECOND occurrence | 14.987 - 17.886 s (the truth's **first** arrival) | 14.987 - 19.822 s (the truth's **first** arrival) |
| the truth's SECOND arrival (33.40 s, frame 1002) is the class's | **5th** occurrence | **5th** occurrence |
| that occurrence's coarse onset frame | 1015 (+13 frames) | 1015 (+13 frames) |
| its frame-exact arrival, refined at 30 fps | **1002 (0 frames)** - the HAND's arrival | **1009 (+7 frames)** - the KEYS' first measured contact; the keys' box overlaps the lips from **1021 (+19)** |
| occurrences of the truth's six carried (±1.5 s) | 5 of 6 | 4 of 6 |

So measuring the keys **does not** make the second arrival the second
occurrence, and it does not move the frame error of the arrival that IS
the truth's second arrival. What it does change is measured, and the
three causes are measured too - §4.

---

## 1. What landed, and what it is for

Two new modules and one new candidate class, all on the EXISTING lanes
(no step, no DAG node, no rebuild of the identity lane):

* `library/tools/object_measurements.py` - the per-source OBJECT
  measurement: the frames are the source's own M2 sample (the frames
  the faces and hands were measured on, so the two halves join by frame
  index), decoded at the source's own resolution in ONE pass, asked
  about the named object, and given ONE IDENTITY PER PHYSICAL OBJECT by
  `library/tools/analysis/object_identity` (PR 3134). The record is
  the new source-memory slot `objects.json`.
* `library/tools/object_contact.py` - the geometry and the split: the
  distance from the object's measured BOX to the measured LIPS in face
  widths, and the contact episodes, using the SAME two levels the hand
  lane uses, read from ONE place (`library/tools/contact_onsets`).
* `event_spans.py` - the candidate class `object_contact_onset`: one
  span per measured contact ONSET of the object, each span carrying the
  object identities it saw. It is ADDITIVE: `hand_contact_onset` keeps
  its meaning and its spans, exactly as `hand_contact_onset` was
  additive to `hand_near_mouth`.

The class is a CANDIDATE, not an answer: it is put to the local VLM
like every other candidate in the M7 record.

## 2. The fixture, and what was NOT re-measured

| | |
|---|---|
| M2 (the coarse sample) | 89 frames on the source's own I-frame cadence, median spacing 0.967 s - UNCHANGED |
| M3 (faces, hands, lips) | 89 frames, 87 faces - UNCHANGED |
| the object measurement | NEW: 89 frames, OWLv2 (`google/owlv2-base-patch16-ensemble`), score floor 0.1, at the source's own resolution |
| the cost | 5545.6 s (92 min) for 89 frames - 62.3 s per frame on this machine (MPS) |

Nothing in the captain's projects, timelines or media was touched; no
broker was started; `ren qualification reset` was not run. Every number
below is of the scratch copy.

## 3. BEFORE and AFTER, occurrence by occurrence

The truth's six: **A1** 15.4 s (frame ~462), **A2** 33.40 s (frame
1002, adjudicated at 30 fps, ±2 frames), **A3** 46.0 s (~1380), **A4**
53.0 s (~1590), **A5** 61.5 s (~1845), **A6** 82.5 s (~2475).

**BEFORE - `hand_contact_onset` (12 occurrences, the incumbent):**

| # | onset (s) | frame | nearest truth | frame error |
|---|---|---|---|---|
| 1 | 3.868 | 116 | - | - |
| 2 | **15.470** | 463 | **A1** | +1 |
| 3 | 21.272 | 638 | - | - |
| 4 | 25.140 | 754 | - | - |
| 5 | **33.842** | **1015** | **A2** | **+13** |
| 6 | 46.412 | 1392 | A3 | +12 |
| 7 | 50.278 | 1507 | - | - |
| 8 | 54.147 | 1624 | A4 | +34 |
| 9 | 63.815 | 1914 | A5 | +69 |
| 10 | 70.583 | 2117 | - | - |
| 11 | 76.385 | 2291 | - | - |
| 12 | 83.153 | 2494 | A6 | +19 |

**AFTER - `object_contact_onset` (9 occurrences, the new class):**

| # | onset (s) | frame | nearest truth | frame error | object identities |
|---|---|---|---|---|---|
| 1 | 3.868 | 116 | - | - | obj-0002, obj-0005, obj-0010, obj-0013 |
| 2 | **15.470** | 463 | **A1** | +1 | obj-0002, obj-0024 |
| 3 | 22.238 | 666 | - | - | obj-0027 |
| 4 | 25.140 | 754 | - | - | obj-0030 |
| 5 | **33.842** | **1015** | **A2** | **+13** | obj-0027, obj-0035 |
| 6 | 46.412 | 1392 | A3 | +12 | obj-0002, obj-0012, obj-0035, obj-0045, obj-0048 |
| 7 | 63.815 | 1914 | A5 | +69 | obj-0051 |
| 8 | 71.550 | 2145 | - | - | obj-0058 |
| 9 | 83.153 | 2494 | A6 | +19 | obj-0065 |

**What the chain answers.** `nth_event` on the committed Project Model
(revision 9):

```
hand_contact_onset   nth=2  ->  14.987 s - 17.886 s   (the truth's FIRST arrival)
object_contact_onset nth=2  ->  14.987 s - 19.822 s   (the truth's FIRST arrival)
object_contact_onset nth=5  ->  33.358 s - 38.191 s   (the truth's SECOND arrival)
```

## 4. Why the ordinal does not move - three measured causes

**(a) The DETECTOR'S BOX IS PADDED OVER THE HAND'S REGION.** The
open-vocabulary detector answers "car keys" with two kinds of box: a
tight one (0.19%-1.32% of the frame, 31 of 118 detections) and a padded
one over the hand and keys together (2.5%-4.9%, 45 of 118). Measured
per frame at 3.868 s and 33.842 s, the ONLY box the detector returned
is the padded one (areas 0.0486 and 0.0273), and the padded box's
distance to the lips is what the class measures. So the object's
measured contact is still the hand's contact, one measurement removed
from it.

The frame-cadence refinement of the two episodes that sit before A2
says the same thing, and rules out the coarse sample as the cause:

| episode | window | the keys' measured contact at 30 fps |
|---|---|---|
| #1, 3.868 s | 3.368-4.368 s | inside the contact level from frame **108** (distance 0.000) through frame 123 - the padded box is over the lips for the whole episode, and at frame 116 the keys are at the BROW with the mouth wide open |
| #3, 22.238 s | 21.738-22.738 s | inside the contact level from frame **651** (distance 0.109) onwards - at frame 666 the keys are at the EYE, and the padded box is 0.11-0.16 face widths from the lips |

So neither episode is a sampling artifact: it is the padded box that
reaches the lips.

**(b) THE GROUND TRUTH'S LIST IS SHORT.** The episode at **25.140 s**
(frame 754) is not a hand contact with the keys below the lips: on the
scratch copy the key fob is ON the mouth (looked at, frame by frame).
It is a real keys-at-lips arrival, and the adjudicated six does not
list it. So a COMPLETE list of keys-at-lips arrivals has at least one
more arrival before A2 than the adjudicated six has, and A2 cannot be
the second arrival of a complete list. The earlier lane already said
the six is short "at least two arrivals"
(`docs/benchmarks/2026-10-21-m0-contact-occurrences.md` §5); this
measurement names one of them and shows it.

**(c) THE COARSE CADENCE MISSES ONE OF THE SIX.** The object's
arrivals are SHORTER than the hand's contacts, and the 0.967 s sample
plus the detector's recall loses one: at 54.147 s (frame 1624) the
detector returned NOTHING for "car keys", and at 53.180 s (frame 1595)
the tight box measured 0.49 face widths from the lips. The hand class
still carries A4 (its occurrence #8 at 54.147 s) because the HAND
lingers at the lips; the object class has no measurement there at all.
That is why the object class carries 4 of the truth's six where the
hand class carries 5: the object's class is MORE sensitive to the
sampling, not less faithful.

## 5. The identity lane's own measurement (PR 3134, on this fixture)

The identity half of the object lane was measured on the same 89
frames, and its report is in the record:

* 118 observed detections over 62 of the 89 inspected frames (36 frames
  carried more than one box, up to five);
* **65 identities for one physical key ring** - the identity FRAGMENTS
  at this cadence: the within-clip association is a box overlap
  (IoU >= 0.30) between consecutive sampled frames, and a hand-held
  object does not overlap its own previous box one second earlier;
* 40 of the 65 tracks began AMBIGUOUSLY (the lane's own refusal to
  merge);
* 65 cross-clip abstentions, 0 links - one clip, so nothing to link.

So the identity cannot be the thing that carries the ordinal at this
cadence. The class therefore measures the OBJECT CLASS's contact (the
object nearest the lips in each inspected frame) and PUBLISHES the
identity of that object per occurrence, and the report says the
identity fragmented rather than pretending one object spans the take.

## 6. The frame error of the truth's second arrival, measured twice

Same window (33.342 s - 34.342 s), same 31 dense frames, same declared
contact level (0.25 face widths), two geometries:

| geometry | arrival frame | error vs the adjudicated 1002 |
|---|---|---|
| the HAND (the incumbent, `frame_exact --rule arrival`) | **1002** (33.408 s) | **0 frames** |
| the KEYS (the new class, the object's own box) | **1009** (33.63 s) | **+7 frames** |
| the KEYS, stricter: the first frame in which the keys' box OVERLAPS the lips | **1021** (34.03 s) | **+19 frames** |

The adjudicated frame 1002 is where the HAND is at the lips. The keys
reach the lips 7 to 19 frames later, and the measurement says so. The
coarse onset of the same occurrence (1015, +13) sits between the two,
which is what a 0.967 s sample can say.

The keys' distance series over that window, frame by frame: 1000
0.754, 1001 0.448, **1002 0.313**, 1003 0.608, 1004 0.494, 1005 0.528,
... 1009 0.245, ... 1015 0.048, ... 1021-1024 **0.000**, ...

## 7. What is NOT proved here

* **The ordinal is not fixed.** The second key arrival is the FIFTH
  occurrence of the object class, exactly as it was the fifth of the
  hand class. Nothing in this measurement claims otherwise.
* **The ground truth is still agent-adjudicated.** §4(b) names one
  arrival the six omits; the captain's re-adjudication is still owed,
  and it is the thing that decides whether "the second arrival" can
  ever be the second occurrence.
* **The class is not a better ANSWER than the hand class.** It is a
  different measurement: 9 episodes against 12, 4 of the truth's six
  against 5, and the three episodes it drops (50.278 s, 70.583 s,
  76.385 s) are hand contacts with the keys demonstrably elsewhere.
  Whether the object class should REPLACE the hand class is a product
  decision; this lane's measurement does not make it.
* **One take, one speaker, one object.** The release level, the
  contact level and the detector's behaviour are all measured on this
  take. Nothing here is calibrated on a corpus.
* **The identity's fragmentation is measured at ONE cadence.** A finer
  cadence (the keys' box overlapping its own previous box) would hold
  the track; 89 frames at 62.3 s per frame is 92 minutes per take, and
  a 2 Hz pass would be three hours.

## 8. What would make the ordinal mean what the chain is asked for

1. **A TIGHT OBJECT BOX.** The repo already has the producer that can
   answer it: the 1.06 segmentation lane's mask for a NAMED object
   (`library/tools/analysis/object_segmentation.py`), not a detection
   box. §4(a) is the measurement that says the detection box cannot.
2. **A CADENCE FINER THAN THE ARRIVAL.** §4(c): the arrival at 53.0 s
   is shorter than the 0.967 s sample.
3. **A RE-ADJUDICATED TRUTH.** §4(b): the six omits at least one real
   arrival before A2.

## 9. Reproduction

```sh
export PIPELINE_PROJECTS_ROOT=/tmp/m0-e2e/projects
SCOPE=~/.local/share/vep/source_memory/projects/<project-scope>

# the object measurement (heavy: 92 min for the 89 M2 frames)
bin/vep -m library.tools.object_measurements build /tmp/m0-e2e/projects/m0-keys \
    --phrase "car keys" --memory-root "$SCOPE"

# the M7 record, with the new candidate class
bin/vep -m library.tools.event_spans build /tmp/m0-e2e/projects/m0-keys

# the model, and the occurrence the chain names
bin/vep -m library.tools.project_model_evidence migrate /tmp/m0-e2e/projects/m0-keys \
    --memory-root "$SCOPE" --commit --expected-revision 8 \
    --reason "M0: the object's own contact class"
bin/vep docs/benchmarks/m0_object_contact_occurrences.py \
    --project /tmp/m0-e2e/projects/m0-keys --memory-root "$SCOPE"

# the frame-exact arrival, with the keys' own geometry
bin/vep docs/benchmarks/m0_object_contact_occurrences.py \
    --project /tmp/m0-e2e/projects/m0-keys --memory-root "$SCOPE" \
    --refine-occurrence 5 --refine-window 0.5
```

Defect-naming tests: `tests/unit/picture/test_object_contact_onsets.py`.
The bench: `docs/benchmarks/m0_object_contact_occurrences.py`.
