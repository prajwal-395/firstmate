# M0-1: a request for a named object answers with detections and a coverage map

**Lane:** `g-m0-l6-request-driven-objects` · **Date:** 2026-10-21 ·
**Engine base:** `5365ca345` ("Say who produced the value, and how sure it is (BL-3) (#3055)")
**Fixture:** the M0 scratch copy (48.0 s, 1440 frames @ 30 fps, 1920×1080) of Apple 001
`IMG_1822`, cut at 2026-10-21 into `/tmp/m0-l6/media/m0_device_take.mov`. The source
(`.../001/raw/IMG_1822.MOV`) was read and never written; the copy, the project and every
measurement of it live outside the checkout.

**Question this lane answered:** the M0 baseline's lane 6 — `object_segmentation` (1.06) is
plan-triggered, so a request-driven grounding question finds no objects at all. Can a query
name the object it needs, have the object lane answer it, and read back both the detections
and what was actually looked at?

**Short answer:** yes. A query for a named object is now a first-class predicate
(`object_presence`), the producer the task requires runs on request, and the answer carries
the detections, the coverage map and the D-01 coverage table by object category.

---

## 1. What changed

| | Before | After |
|---|---|---|
| A query for an object | not expressible: the query layer knew only `measurement` and `event_span` | `{"predicate": "object_presence", "phrase": "…", "requirement": "…"}` |
| The lane a query named | `segmentation` — the TRACKER's lane, which no request-driven producer served | `object_detection` — the request-driven lane, with a producer registered for it |
| Who ran | nobody: 1.06 segments only the clips a subject grade or a behind-subject plan names | the producer the task requirement selects, over the windows the query asks about |
| What came back | `not_yet_analysed` with the missing lane named | `present` with the detections, the coverage map and the coverage table |
| What was looked at | nothing said | every sampled frame, the rate, and the seconds nobody looked at |

New: `library/tools/analysis/object_grounding.py` (the request-driven producer), the
`object_presence` predicate in `library/tools/evidence_query.py`, the `object_detection`
producer in `library/tools/project_model_acquisition.py`, and `ren evidence --object`.

## 2. The producers, measured on this machine

The M0 fixture, 48 sampled frames of 1920×1080 source. The detection cost is the detector
alone; a pass also pays ONE extraction over the source, because the frames are selected by
their source index and the decoder walks the file once.

| Producer | Detection | Extraction | D-01 recall | Vocabulary |
|---|---:|---:|---:|---|
| `rf_detr_nano` | **0.384 s/frame** | 15.39 s per 48 s of source | 33/116 = 0.284 | fixed (COCO) |
| `owlv2` | 7.269 s/frame | (the same pass) | 65/116 = 0.560 | open |
| `grounding_dino` | 13.113 s/frame | (the same pass) | unmeasured | open |
| `vision_foreground` | 0.163 s/frame (MU-15) | — | 21/116 = 0.181 | **none** — names no phrase |

The Apple Vision baseline is 13× cheaper than the cheapest NAMED detector and cannot answer
a named question at all: it proposes regions, and a region is not an answer to "where is the
handheld device". `grounding_dino` is SLOWER than OWLv2 on this machine, which is why the
requirement never selects it.

Selection is by task requirement (C-08): `presence` takes the cheapest producer that can
answer the question, `grounded` takes the one with the best measured recall that can answer
it. A fixed-vocabulary producer is only eligible for a phrase it can name — asking
`rf_detr_nano` about a "handheld device" would answer nothing, and an empty answer reads as
"the object is not there".

## 3. The measurement

### 3.1 Before

```
$ ren evidence m0-pickup --object "handheld device"
REN_REFUSAL: unknown predicate 'object_presence': this query answers
             'measurement' and 'event_span' only
```

The measurement form of the same question (`object.detection` with `equals`) compiled, and
named the tracker's lane:

```
required_lanes: ["segmentation"]
status: not_yet_analysed
reasons: ["lane(s) ['segmentation'] never ran: nothing measured, so nothing can be absent yet"]
```

The Project Model carried 0 `object.detection` rows, and the migration said so in its own
notes: `clip_001: no 1.06 segmentation; no object tracks or subject masks`.

### 3.2 After — the cheap request (`presence`, "cell phone")

```
$ ren evidence m0-pickup --object "cell phone" --requirement presence --acquire --max-cost-seconds 300
```

* one pass over the whole source, **44.8 s** (budget 50.7 s), peak RSS 0.39 GB
  (74.5 s of wall clock for the whole call)
* producer `rf_detr_nano`, **76 detections**, 48 of 48 frames inspected, unexamined: none
* the D-01 coverage table's **`phone`** row — UNMEASURED on the D-01 corpus, because that
  corpus holds no phone — is now MEASURED: 76 instances over **31 of the 48** frames that
  carried one, coverage 0.646
* the strata, measured from the box geometry: 19 small instances, 7 partial

### 3.3 After — the grounded request (`grounded`, "handheld device")

```
$ ren evidence m0-pickup --object "handheld device" --requirement grounded --acquire --max-cost-seconds 900
```

* one pass over the whole source, **531.3 s** (budget 546.4 s), peak RSS 0.47 GB
  (545.3 s of wall clock for the whole call)
* producer `owlv2` (measured recall 0.560), **58 detections**, 48 of 48 frames inspected
* the coverage table's **`handheld device`** row: 58 instances over **34 of the 48** frames
  that carried one, coverage 0.708
* the strata, measured from the box geometry: 6 small instances, 7 partial

At the M0 occurrence's adjudicated onset band (33.30–33.70 s, agent-adjudicated and
**provisional** pending the captain's re-adjudication) the object is detected at source
frames 960 (32.0 s), 990 (33.0 s) and 1020 (34.0 s) — score 0.338 at frame 990, box
`[0.278, 0.627, 0.381, 0.908]`.

### 3.4 What the answer looks like

```json
{
  "status": "present",
  "detections": [{"query": "handheld device", "phrase": "handheld device",
                  "detector": "owlv2", "score": 0.338,
                  "box": [0.278, 0.627, 0.381, 0.908],
                  "frame_index": 990, "time_seconds": 33.0,
                  "evidence_id": "obs-…-objdet-handheld_device-000990-00"}],
  "coverage_map": {"frames_inspected": 48, "sample_rate_hz": 1.0,
                   "inspected": [{"start_s": 0.0, "end_s": 48.0}],
                   "unexamined": [],
                   "producers": [{"id": "owlv2_named",
                                  "measured_fidelity": 0.560}]},
  "coverage_by_category": {"by_category": {"handheld device": {"instances": 58, …}}}
}
```

## 4. The honesty the answer keeps

* **A sampled pass never claims absence.** The coverage is `sampled`, so a request that
  finds nothing answers `inconclusive` with its coverage map — never `confidently_absent`
  and never a false negative (C-14).
* **The coverage row is not a detection.** The row that records what a pass looked at is
  filed as its own kind, so a pass that found nothing cannot report an object.
* **One question, one lane state.** The lane's coverage is counted PER QUESTION: a request
  for "handheld device" does not read the rows of an earlier "cell phone" request as a
  partial answer (that defect was measured live, and fixed).
* **The rows name the producer that wrote them.** The request-driven rows are filed in
  their own provenance lane (`object_detection`), not in 1.06's `segmentation` lane: a
  provenance record names ONE producer, and a row must never claim a producer that did not
  write it (BL-3).
* **The frame is the frame.** Every detection carries the SOURCE frame index it was
  measured on — the M2 sample's `index: null` defect is not repeated.

## 5. Limits, stated

* **This finds the object; it does not resolve the ordinal.** "The second time the person
  brings the device to their mouth" is lane 1's occurrence identity, not this lane's. The
  request answers "where is the handheld device", and the M0 onset band remains
  agent-adjudicated and provisional.
* **The request buys detections, not a track.** SAM 2 tracking stays the plan-triggered
  1.06 path; the answer says which producer answered and what that producer's measured
  recall is.
* **A full-source pass costs one decode of the file** (15.4 s for 48 s of 1080p here,
  measured). A long source is an expensive request; the cost is stated, not hidden.
* **`grounding_dino`'s recall is unmeasured**, so it cannot answer a `grounded` request;
  its cost was measured and it is not cheaper than OWLv2 here anyway.
* **The coverage table's categories are the D-01 taxonomy when the query names one of them**
  ("cell phone" → `phone`); a query for an object outside the taxonomy is reported under
  its own name, never folded into a class it was never asked about.

## 6. Reproduction

```sh
export PIPELINE_PROJECTS_ROOT=/tmp/m0-l6/projects
ffmpeg -i "<001/raw>/IMG_1822.MOV" -t 48.0 -c:v libx264 -crf 18 -c:a aac -r 30 \
       /tmp/m0-l6/media/m0_device_take.mov
bin/ren new m0-pickup --name "M0 Pickup Baseline" --fps 30 \
     --footage /tmp/m0-l6/media --no-speakers --shape picture-led
bin/ren edit m0-pickup --only scan --only catalog --auto --full-auto agent
bin/vep -m library.tools.project_model_evidence migrate "$PIPELINE_PROJECTS_ROOT/m0-pickup" \
     --commit --expected-revision 1 --reason "M0 request-driven objects baseline"

# the request, and the work it asks for
bin/ren evidence m0-pickup --object "handheld device" --requirement grounded \
     --acquire --max-cost-seconds 900
bin/ren evidence m0-pickup --object "handheld device"
```

Defect-naming tests: `tests/unit/picture/test_object_grounding.py` (18 of 20 fail on the
pre-fix revision; the two that pass are the pure-function tests of the new module). The
incident: `docs/RULE_EVIDENCE.md#the-grounding-question-that-could-not-ask-for-anything`.
