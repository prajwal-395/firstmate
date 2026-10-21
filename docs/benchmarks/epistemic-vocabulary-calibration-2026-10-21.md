# Epistemic vocabulary: what the evidence claims, and how sure it is

**Lane BL-3** (B-01 certainty vocabulary, B-03 confidence vs certainty).
**Measured 2026-10-21** on the Ren-owned scratch model
`~/Documents/content_stuff/video_projects/ren-scratch-breaktest/project_model`
(never the captain's footage, no Resolve, no broker).

## What was wrong

The evidence contract had a vocabulary and enforced it, but the data never
exercised it. Every registry type but one was filed as `measurement`, and
every row in the measured model was `measurement` / `measured`:

* **90,699 of 114,521 rows (79.2%)** were produced by a **learned model or a
  matcher over words** - a detector's box, a segmenter's region, a
  transcriber's word, a diarizer's turn, a take link between two
  utterances - and every one of them was filed as if an instrument had read
  it.
* `0` rows carried an `uncertainty`, and `0` of 474 rows that carried a
  confidence carried a `calibration_version`, so a raw detector score was
  the only thing standing between a reader and a claim of certainty.

That is the conflation B-03 forbids: a high-confidence **model prediction**
filed as a **measurement**.

## The rule this lane ships

Every registry type now names **who produced its value** -
`instrument | model | recorded | human` - and the evidence kind follows the
producer:

| producer | what it is | evidence kind | rows on the measured model |
|---|---|---|---:|
| `instrument` | a deterministic reduction of the decoded signal, or of data an instrument already established | `measurement` / `measured` | 23,815 |
| `model` | a learned model, or a matcher over words, asserted something | `inference` / `inferred` | 90,699 |
| `recorded` | metadata the capture device recorded | `measurement` / `measured` | 7 |
| `human` | a person authored the rule or made the judgement | `evaluation` / `creative_judgment` | 0 |

A model-produced type **cannot** be filed as a measurement: the record
refuses it (`evidence_contract.EvidenceRecord`), and `new_measurement`
refuses to build one. A model-produced value is an **inference** and carries
the uncertainty its producer earned:

* `1 - score` where the producer recorded its own score
  (`person.face_box` from the M3b `det_score`, `audio.sound_event` from the
  event confidence, `object.detection` from the detector's score, the OCR
  track from the mean OCR confidence);
* `UNCERTAINTY_UNSTATED` (1.0) where the producer recorded none - maximal,
  because the producer asserted nothing about how sure it was. 90,268 of the
  90,699 re-classified rows are in this band, and that is the honest
  reading: the ASR, the diarizer and the take-link matcher do not report a
  per-item confidence.

## Before and after, on 114,521 real rows

| | before | after |
|---|---:|---:|
| observations with an evidence contract | 114,521 | 114,521 |
| model-produced rows | 90,699 (79.2%) | 90,699 (79.2%) |
| **model-produced rows filed as a measurement** | **90,699** | **0** |
| inferences without an uncertainty | 0 | **0** |
| rows whose uncertainty came from a producer score | 0 | 431 |
| rows carrying the unstated maximum | 0 | 90,268 |
| **fields outside the epistemic three that moved** | - | **0** |
| contract refusals after the re-classification | - | 427 |

The 427 refusals are **pre-existing and not caused by this change**: the
stored model's `person.face_box` rows carry no `sample_resolution`, which
the registry has always demanded of that type. The same rows are refused
before the re-classification, for the same reason. A fresh migration writes
the resolution (`migrate_m3_face_span` passes the lane's pixels).

**Fidelity.** A re-classification may move `evidence_type`, `certainty` and
`uncertainty` and nothing else. The harness compares every other field of
every contract before and after: **0 violations across 114,521 rows**. No
value, interval, payload, coverage, provenance or reference changed.

## Per-type empirical error (the calibration)

The calibration is per type, carries the row count it was measured on, and
reports **unknown** for a type with no adjudicated set - an unmeasured error
is never a pass.

| type | adjudicated set | N rows | error | source |
|---|---|---:|---:|---|
| `object.detection` | object-detector tiering set (40 stills, hand-labelled boxes) | 148 | 10.8% false negatives (16/148 missed; recall 0.892) | [`docs/benchmarks/object-detector-tiering-2026-10-14.md`](object-detector-tiering-2026-10-14.md) |
| `object.detection`, `object.track_sample` (person) | held-out object-continuity set | 14 | 35.7% (recall 0.643, precision 0.643) | [`docs/benchmarks/2026-10-14-object-continuity-benchmark.json`](2026-10-14-object-continuity-benchmark.json) |
| `object.detection`, `object.track_sample` (laptop) | held-out object-continuity set | 6 | 100% (recall 0.0, no prediction) | same |
| every other model-produced type | none reachable | - | **unknown** | - |

Two facts a reader must have:

* the tiering adjudication set lived in a temporary directory
  (`$TMPDIR/opencode/mu15-bakeoff`) and was deleted with it, so its numbers
  are the **published measurement**, not a set this lane can re-score. The
  held-out object-continuity labels are sealed outside the repository by
  protocol; only their aggregate is in it.
* the 27 model-produced types carry **one** adjudicated set between them:
  the object family. For the other 25 (the transcript family, the region
  and face geometry, the depth, the scene descriptions, the sound events,
  the event and relation candidates) **no adjudicated set exists**, so their
  error is **unknown** and is reported as unknown. This lane does not
  manufacture labels to fill the table.

## The harness

```bash
# the census, before and after, on a stored model
python3 -m library.tools.epistemic_vocabulary \
    --model ~/Documents/content_stuff/video_projects/ren-scratch-breaktest/project_model/snapshot.json

# the calibration, against a sealed adjudicated set (kept outside this repository)
python3 -m library.tools.epistemic_vocabulary \
    --model <snapshot.json> --adjudication <sealed-set.json>
```

The adjudicated set names hand-adjudicated `labels` and the producer's
`predictions`; each prediction names the `type` it came from, so the error
is reported per type and not pooled. A label is matched by a same-class
prediction over the same frame with an IoU of at least 0.5; an unmatched
label is a miss and an unmatched prediction is a false positive, and both
count as error. The command exits non-zero while any model-produced type is
still filed as a measurement.

## Scope, named

* This lane is the **Project Model's evidence contract registry**. The
  source-memory envelope keeps its own coarse `evidence_type` /
  `certainty_state` fields; those rows are not contract records and were
  not touched here.
* The calibration of a producer's **raw score** is a separate question
  (B-03's `calibration_version`, BL-6's provenance lane). This lane stamps
  the uncertainty the producer earned; it does not claim the score is
  calibrated, and the census shows how many rows carry no score at all.

## What a reader can conclude

The vocabulary now says what it means: 79.2% of the measured model's rows
were model assertions, and all 79.2% are filed as inferences with an
uncertainty. The one type whose error is measured on an adjudicated set
errs on 10.8% of its rows, and the twenty-five types with no adjudicated
set are marked unknown rather than clean.
