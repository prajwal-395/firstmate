# Object detector tiering measurement (2026-10-14)

## Scope

Measured the existing MU-15 bakeoff set: 40 stills and 148 hand-labeled boxes.
The phrases came from Gemma object outputs, so this is not an unbiased object
query benchmark. These results apply to the named-object phrases and sampled
frames in that set, not to exhaustive detection over all footage.

Each pass ran under `bin/vep -m library.tools.heavy_work_lock run` on the same
machine. Wall time includes model/helper setup and all frame inference. Peak
memory is process peak RSS. The baseline ran OWLv2 on all 40 frames. The tiered
plan ran Apple Vision on each query frame, then OWLv2 for the named-object
query, recording both tiers separately.

## Results

| Plan | Wall (s) | Peak RSS (MB) | Named-object boxes recalled | Recall | Candidate retention | False negatives |
|---|---:|---:|---:|---:|---:|---:|
| OWLv2 baseline | 145.55 | 1174.56 | 132 / 148 | 0.892 | n/a | 16 / 148 |
| Apple Vision nominations | 6.53 | 26.73 | 36 / 148 | 0.243 | 36 / 148 (24.3%) | 112 / 148 (75.7%) |
| Apple Vision then OWLv2 | 155.16 | 1194.02 | 132 / 148 | 0.892 | 36 / 148 (24.3%) | 16 / 148 (10.8%) |

The tiered plan recovered 96 of the 112 boxes missed by Vision; 16 remained
missed by both. On this set, consulting Vision before OWLv2 added 9.61 seconds
(6.6%) and 19.45 MB peak RSS compared with OWLv2 alone. Vision is useful as a
cheap nomination tier, but its negative result cannot answer a named-object
query. The tier planner therefore reports an empty Vision-only result as
`not_yet_analysed`; an empty promoted result is `inconclusive`, because even
OWLv2 missed 10.8% of the labels.

## Deterministic plan selection

The selector chooses the lowest measured wall-time plan that meets the task's
minimum recall and required output evidence types:

- For a named-object query requiring recall >= 0.85 and `object.detection`, it
  chooses `owlv2_named_queries`: 145.55 s total (3.64 s/frame), 1174.56 MB,
  and 132/148 recall (0.892). Vision-first is not selected because it raises
  cost without improving recall or adding a required output.
- For a task accepting `object.foreground_candidate` and recall >= 0.20, it
  chooses `vision_nominations`: 6.53 s total (0.16 s/frame), 26.73 MB, and
  36/148 recall (0.243). A negative candidate result remains
  `not_yet_analysed`.
- `vision_then_owlv2` is selected only when the task requires both foreground
  candidates and named detections. That plan retains the same named-query
  recall as OWLv2 alone and records Vision's candidate output alongside it.

## Run record

The measurement used the authorized scratch set at
`/private/var/folders/k6/k07x_c1s4315ksh0n2gwhfkw0000gn/T/opencode/mu15-bakeoff/`
without modifying it. The benchmark run reported 40 frames, 148 boxes, and
OWLv2 recall 132/148 before the tiered pass. Tiered per-frame inference time
was 8.35 s for Vision and 146.80 s for OWLv2, excluding their shared run
orchestration overhead; the full tiered wall time was 155.16 s.
