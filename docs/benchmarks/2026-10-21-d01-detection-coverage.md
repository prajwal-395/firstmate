# D-01 — measured detection coverage across object categories (2026-10-21)

**Status: complete.** One measured benchmark, three detectors, 24 labelled real
frames, 116 labelled instances, every number with its denominator, and one
defect found and fixed with a before/after measurement.

## What this is

D-01 asks for *measured detection coverage across object categories* — people,
hands, phones, laptops, cups, small objects, partially visible objects and
unusual footage. This document is that measurement: what Ren's detection path
finds, per category, on real footage, with the denominators beside every
number.

It is a **harness plus a run plus a label packet**, all three in this
repository:

- `library/tools/analysis/detection_coverage.py` — the harness: manifest
  validation, the strata, the scoring, the report, and the commands that
  extract the frames, run the detectors, score them and draw the labels.
- `docs/benchmarks/2026-10-21-d01-detection-coverage-manifest.json` — the
  label packet: 24 frames, each with its source clip and second, and every
  taxonomy instance labelled with a normalized box.
- `docs/benchmarks/2026-10-21-d01-detection-coverage.json` — the scored
  result, per detector, per class, per query phrase, per stratum.

## The corpus

24 frames from six real clips on this machine, extracted to scratch with
`ffmpeg` and never written back to the source media:

| Footage | Frames | What it is |
|---|---:|---|
| `LC4930`, `LC4931`, `LCATL0012` (GEO podcast MXF, 3840×2160) | 12 | the studio: seated speakers, a laptop, a cup at the frame edge, hands |
| `IMG_1807`, `IMG_1808`, `IMG_1809`, `IMG_1810`, `IMG_1812`, `IMG_1820` (001 raw MOV) | 6 | in-car dashcam and street walk-and-talk — **unusual footage** |
| `1_28_2026(ep0)`, `1_29_2026(ep1)` (handheld vertical, 1080×1920) | 6 | vertical selfie: a person cut by the frame edges, a hand at the face, a ceiling speaker, a light switch |

Twelve frames are studio, twelve are unusual. The unusual stratum is not
decoration: it is where the frame is vertical, handheld, in a car, or a face
fills the picture, and it is where "partially visible" stops being a corner
case.

## The labels

**One agent adjudicator, no human gold labels, no second adjudicator.** The
lane's worker looked at every frame and labelled every instance of the
taxonomy classes it could see, with normalized boxes read off the frame. These
are agent labels and the report says so wherever it matters.

The rules, all in the manifest:

- **Exhaustive within the taxonomy.** Every visible instance of a taxonomy
  class in a frame is labelled, so an unmatched detection of that class is a
  false positive rather than an unlabelled instance. Out-of-taxonomy objects
  (trees, chairs, buildings, car interiors) are not labelled and detections
  of them are not scored.
- **Small and partially visible are measured, not asserted.** An instance
  counts as SMALL when its box area is under 1% of the frame, and as PARTIAL
  when its box touches a frame edge. Neither is a label the adjudicator
  wrote; both are read off the geometry, so a labeler cannot flatter a
  detector by calling a miss "too small to see".
- **Unusual footage is a frame property**, declared per frame.
- **Every instance carries the query phrase it was labelled for.** The
  detector is asked those phrases, and a detection answers one of them.

The labels were corrected once: every frame was rendered with its boxes
drawn on it (`detection_coverage annotate`) and looked at. Seven corrections
followed — a "hand" that was really hair, three mis-placed glasses, three
mis-placed ceiling speakers, a hand and a car-door handle that were too
loose, and a person box that was too wide. The potted plant was relabelled
from its pot to the whole plant once it was clear that is what a detector
boxes.

## The scoring

Class-conditioned, one-to-one, IoU ≥ 0.5:

- A detection answers one phrase and matches only an instance labelled for
  that phrase. The class-agnostic Apple Vision foreground proposal names no
  phrase and may match any instance — but because it names no class it is no
  class's true positive and no class's false positive either, so it is
  counted on its own line.
- Matching is maximum-cardinality: one prediction cannot recall two
  overlapping labels, and a duplicate answer is a false positive rather than
  a second hit.
- A false positive belongs to the class its own phrase names, over the whole
  corpus, not to whichever class happens to sit first in the frame.
- **A category with no labelled instance is UNMEASURED, not zero.** The
  report prints `UNMEASURED (no instances)` and the JSON carries
  `"measured": false`.

## The results

### owlv2 — the incumbent (open vocabulary)

24 frames, 116 labelled instances, 371 detections, IoU >= 0.5.

| Category | Instances | Recalled | Recall | Detections | True positives | Precision |
|---|---:|---:|---:|---:|---:|---:|
| person | 21 | 21 | 1.000 | 40 | 21 | 0.525 |
| hand | 34 | 13 | 0.382 | 36 | 13 | 0.361 |
| phone | 0 | - | UNMEASURED (no instances) | - | - | - |
| laptop | 12 | 12 | 1.000 | 21 | 12 | 0.571 |
| cup | 4 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| small_object | 45 | 19 | 0.422 | 274 | 19 | 0.069 |

| Query phrase | Instances | Recalled | Recall | Detections | True positives | Precision |
|---|---:|---:|---:|---:|---:|---:|
| person | 21 | 21 | 1.000 | 40 | 21 | 0.525 |
| hand | 34 | 13 | 0.382 | 36 | 13 | 0.361 |
| laptop | 12 | 12 | 1.000 | 21 | 12 | 0.571 |
| glasses | 14 | 8 | 0.571 | 17 | 8 | 0.471 |
| camera | 8 | 0 | 0.000 | 42 | 0 | 0.000 |
| cup | 4 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| plant pot | 4 | 4 | 1.000 | 8 | 4 | 0.500 |
| lamp | 4 | 2 | 0.500 | 37 | 2 | 0.054 |
| watch | 1 | 0 | 0.000 | 4 | 0 | 0.000 |
| car door handle | 1 | 1 | 1.000 | 91 | 1 | 0.011 |
| side mirror | 1 | 1 | 1.000 | 11 | 1 | 0.091 |
| hanging device | 1 | 1 | 1.000 | 17 | 1 | 0.059 |
| white charger | 1 | 0 | 0.000 | 1 | 0 | 0.000 |
| backpack | 1 | 0 | 0.000 | 1 | 0 | 0.000 |
| ceiling speaker | 5 | 2 | 0.400 | 29 | 2 | 0.069 |
| light switch | 4 | 0 | 0.000 | 16 | 0 | 0.000 |

| Stratum | Instances | Recalled | Recall |
|---|---:|---:|---:|
| small | 53 | 16 | 0.302 |
| partial | 48 | 27 | 0.562 |
| unusual_footage | 34 | 20 | 0.588 |
| studio_footage | 82 | 45 | 0.549 |

### rf_detr_nano — fixed vocabulary (COCO)

24 frames, 116 labelled instances, 101 detections, IoU >= 0.5.

| Category | Instances | Recalled | Recall | Detections | True positives | Precision |
|---|---:|---:|---:|---:|---:|---:|
| person | 21 | 21 | 1.000 | 48 | 21 | 0.438 |
| hand | 34 | 0 | 0.000 | 6 | 0 | 0.000 |
| phone | 0 | - | UNMEASURED (no instances) | - | - | - |
| laptop | 12 | 8 | 0.667 | 13 | 8 | 0.615 |
| cup | 4 | 0 | 0.000 | 6 | 0 | 0.000 |
| small_object | 45 | 4 | 0.089 | 28 | 4 | 0.143 |

| Query phrase | Instances | Recalled | Recall | Detections | True positives | Precision |
|---|---:|---:|---:|---:|---:|---:|
| person | 21 | 21 | 1.000 | 48 | 21 | 0.438 |
| hand | 34 | 0 | 0.000 | 6 | 0 | 0.000 |
| laptop | 12 | 8 | 0.667 | 13 | 8 | 0.615 |
| glasses | 14 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| camera | 8 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| cup | 4 | 0 | 0.000 | 6 | 0 | 0.000 |
| plant pot | 4 | 4 | 1.000 | 21 | 4 | 0.190 |
| lamp | 4 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| watch | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| car door handle | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| side mirror | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| hanging device | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| white charger | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| backpack | 1 | 0 | 0.000 | 7 | 0 | 0.000 |
| ceiling speaker | 5 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| light switch | 4 | 0 | 0.000 | 0 | 0 | n/a (no detections) |

| Stratum | Instances | Recalled | Recall |
|---|---:|---:|---:|
| small | 53 | 4 | 0.075 |
| partial | 48 | 13 | 0.271 |
| unusual_footage | 34 | 9 | 0.265 |
| studio_footage | 82 | 24 | 0.293 |

### vision_foreground — class-agnostic proposals

24 frames, 116 labelled instances, 0 detections, IoU >= 0.5.

| Category | Instances | Recalled | Recall | Detections | True positives | Precision |
|---|---:|---:|---:|---:|---:|---:|
| person | 21 | 21 | 1.000 | 0 | 0 | n/a (no detections) |
| hand | 34 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| phone | 0 | - | UNMEASURED (no instances) | - | - | - |
| laptop | 12 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| cup | 4 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| small_object | 45 | 0 | 0.000 | 0 | 0 | n/a (no detections) |

Class-agnostic proposals: 21 (21 matched an instance, 0 matched none). They name no class, so they are no class's true positive and no class's false positive.

| Query phrase | Instances | Recalled | Recall | Detections | True positives | Precision |
|---|---:|---:|---:|---:|---:|---:|
| person | 21 | 21 | 1.000 | 0 | 0 | n/a (no detections) |
| hand | 34 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| laptop | 12 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| glasses | 14 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| camera | 8 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| cup | 4 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| plant pot | 4 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| lamp | 4 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| watch | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| car door handle | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| side mirror | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| hanging device | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| white charger | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| backpack | 1 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| ceiling speaker | 5 | 0 | 0.000 | 0 | 0 | n/a (no detections) |
| light switch | 4 | 0 | 0.000 | 0 | 0 | n/a (no detections) |

| Stratum | Instances | Recalled | Recall |
|---|---:|---:|---:|
| small | 53 | 0 | 0.000 |
| partial | 48 | 9 | 0.188 |
| unusual_footage | 34 | 9 | 0.265 |
| studio_footage | 82 | 12 | 0.146 |


## What the numbers say

**People and laptops are covered. Everything else is a gap, and the answers
are noisy.** The incumbent (OWLv2) finds every labelled person (21/21) and
every labelled laptop (12/12), and finds nothing else reliably:

- **Hands: 13/34 (0.382).** The largest class in the corpus and the one the
  incumbent is worst at among the classes it can answer. The other
  detectors are worse: the fixed-vocabulary one answers no hand at all
  (COCO has no hand class) and the class-agnostic proposal finds none.
- **Cups: 0/4.** The incumbent never answered a "cup" on any frame, not
  even for the translucent cup at the bottom-right corner of four frames.
  The fixed-vocabulary detector answered six cups and every one was wrong.
- **Small objects: 19/45 (0.422) at a precision of 0.069.** 274 answers,
  19 right. Per phrase the pattern is sharper: the incumbent answers 42
  boxes for "camera" and none of them is the camera on the tripod; 91 for
  "car door handle" and one of them is; 37 for "lamp" and two are. Asking
  the open-vocabulary model about a small, specific thing produces a flood
  of confident wrong boxes.
- **Phones: UNMEASURED.** This corpus holds no phone. The frames come from
  the studio (the table carries a laptop and a cup, no phone), from the car
  (a navigation display, which is not a phone), and from the vertical
  selfies (no phone in frame). The machine's other corpora were surveyed
  for the same purpose — the podcast media, the 001 raw clips, the 002 vlog
  segments, `Raw Videos`, and the `digi dump` photo folders — and none of
  the sampled frames holds a phone either. **Phone detection coverage is
  therefore not measured here, and it is not zero: it is unknown.** The
  harness scores the moment a phone-bearing frame is labelled.

The precision columns are the other half of the story, and they are the
reason the small-object numbers matter: the incumbent answers about twice as
many boxes as it gets right for people (21 true positives out of 40
answers) and for laptops (12 of 21). A pipeline that takes those answers as
seeds is mostly seeding noise.

**Unusual footage is not where it breaks.** Recall on the twelve unusual
frames (0.588) is slightly *higher* than on the twelve studio frames
(0.549) for the incumbent. The vertical selfie frames are hard for a
different reason than the studio frames: a face fills the picture and the
frame is 1080×1920, so the small objects in it (the ceiling speaker, the
light switch) are a handful of pixels. The measured small stratum says so
plainly: 16/53 (0.302) for the incumbent against 49/63 for everything else.

**The class-agnostic proposal cannot support a negative answer.** Apple's
foreground proposals covered 21/21 people — every person in the corpus — and
not one hand, laptop, cup or small object. A foreground proposal is a
candidate generator, and D-01's numbers say plainly that on this footage it
answers "is there a person here" and nothing else.

## The defect this found, and the fix

**A true detection was discarded, and a false one was invented, because of
how a detector's class name was matched to the queried name.**
`library/tools/analysis/open_vocabulary_detect.py`, `_match_phrase`.

The fixed-vocabulary detector answers COCO class names; the adapter maps
each one back to the queried phrase. The rule was "exact, else one name
contains the other". That rule does both of the wrong things:

- **It dropped a true detection.** COCO calls the little plant on the table
  a *potted plant*; the query said *plant pot*. Neither name contains the
  other, so the adapter threw the detection away — and the box was right:
  the detector's box `[0.326, 0.439, 0.375, 0.542]` sits on the plant that
  the label puts at `[0.326, 0.437, 0.367, 0.539]`. Ren measured 0/4
  plant-pot coverage for a detector that found all four.
- **It invented detections.** COCO's *car* **is** a substring of *car door
  handle*, so every car in frame was reported as a door handle. Asking for
  a door handle produced 92 "door handle" detections, none of them a door
  handle.

The fix keeps the rule honest in both directions: a label longer than the
query still answers it (*cell phone* answers *phone*), but a **one-word**
label no longer answers a longer query (*car* is not a *car door handle*),
and two **compound** names that are the same nouns in another order are one
noun (*potted plant* is a *plant pot*). A shared word is not enough on its
own: *traffic light* is still not a *light switch*.

Measured before and after, same frames, same labels, same threshold:

| Measurement | Before | After |
|---|---:|---:|
| RF-DETR nano, instances recalled | 29/116 (0.250) | 33/116 (0.284) |
| RF-DETR nano, small objects recalled | 0/45 (0.000) | 4/45 (0.089) |
| RF-DETR nano, "car door handle" answers | 92 | 0 |
| RF-DETR nano, named answers in total | 172 | 101 |
| OWLv2, every measurement | unchanged | unchanged |

The four recovered instances are the potted plants. The 71 removed answers
were cars reported as door handles. The incumbent's numbers do not move,
because OWLv2 answers with the queried phrase and never goes through this
mapping.

The defect is named by a test:
`tests/unit/picture/test_detection_coverage.py`,
`test_a_fixed_vocabulary_detection_of_the_queried_object_is_not_dropped_for_a_naming_reason`
and
`test_the_naming_rule_still_refuses_a_one_word_label_for_a_longer_phrase`.

## What this does NOT measure

- **No human gold labels.** One agent adjudicator labelled the frames.
  A second adjudicator would change the denominators, and the boxes are
  visual estimates read off a frame, not pixel annotations.
- **Not coverage of the footage.** 24 frames is a sample, not the 10,895
  seconds of podcast media and the 807 seconds of the 001 project the
  earlier tracking coverage report measured. The numbers here are about
  these frames.
- **Not whole-video detection.** The measurement is class-conditioned
  localization on frames whose content is known, which is what the
  detection path is asked to do. It is not open-world discovery: a
  detector is never asked "what is in this frame", only "where is the X I
  named".
- **Not tracking, segmentation or identity.** D-03, D-04 and D-05.
- **Phone coverage is UNMEASURED**, as above.
- **Apple Vision's numbers are recall only.** The proposal names no class,
  so there is no per-class precision to report.

## Reproduction

The frames live outside the repository (they are derived from the source
media), and the manifest carries the clip and the second for each one:

```bash
MANIFEST=docs/benchmarks/2026-10-21-d01-detection-coverage-manifest.json
FRAMES=~/.cache/ren-d01/frames/native

# 1. re-extract the labelled frames from the source clips
bin/vep -m library.tools.analysis.detection_coverage extract \
    --manifest $MANIFEST --out-dir $FRAMES

# 2. look at what was labelled (draws the boxes on the frames)
bin/vep -m library.tools.analysis.detection_coverage annotate \
    --manifest $MANIFEST --frames $FRAMES --out-dir ~/.cache/ren-d01/annotated

# 3. run a detector (heavy: it loads a model)
bin/vep -m library.tools.heavy_work_lock run --owner d01-detection-coverage \
    --ram-gb 8 -- \
    bin/vep -m library.tools.analysis.detection_coverage detect \
        --manifest $MANIFEST --frames $FRAMES --detector owlv2 \
        --out ~/.cache/ren-d01/owlv2.json

# 4. score it, with the denominators
bin/vep -m library.tools.analysis.detection_coverage report \
    --manifest $MANIFEST \
    --detections ~/.cache/ren-d01/owlv2.json \
    --detections ~/.cache/ren-d01/rf_detr_nano.json \
    --detections ~/.cache/ren-d01/vision_foreground.json \
    --out docs/benchmarks/2026-10-21-d01-detection-coverage.json \
    --markdown docs/benchmarks/2026-10-21-d01-detection-coverage.tables.md
```

The table section above is the output of step 4; the JSON is the artifact of
record.

**Models.** `google/owlv2-base-patch16-ensemble` (revision
`cfd3195ba4ea9592eec887ded089f4c08eff231d`), float32 on MPS;
`Roboflow/rf-detr-nano` (revision `9f201577d9415f0378084748d27e68586e6b3600`),
float32 on MPS; Apple Vision foreground proposals through the platform API.
All weights came from the machine's existing cache; nothing was downloaded.
Run cost on this machine: OWLv2 406.7 s for 24 frames, RF-DETR nano 60.1 s,
Apple Vision 9.7 s, each under the heavy-work lock.

**Source media.** The frames are real footage from this machine: the GEO
podcast MXFs, the 001 raw clips and the vertical handheld clips. They were
read read-only and extracted to scratch; no source media, project,
timeline or Resolve state was touched.
