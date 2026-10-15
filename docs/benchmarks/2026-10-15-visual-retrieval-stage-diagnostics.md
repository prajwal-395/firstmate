# Visual retrieval stage diagnosis and calibration sweep

## Decision

Do not change the shipped visual retrieval settings. The calibration sweep
identified interval merging as a useful place to test, and selected a
60-second merge gap for one held-out evaluation. Held-out recall improved, but
useful intervals fell from 4 to 2 and query seconds per useful interval rose
from 1.61 to 2.30. The candidate also produced 140- and 210-second returned
ranges. That is a worse editing cost and an overly broad scrub range, so the
candidate does not meet the adoption bar. This task ships the stage measurement
and its diagnostic metadata correction only.

## Protocol

The calibration-only set combines the five registered visual queries C01-C05
from `library/tools/visual_retrieval_calib_extra.json` with the frozen corpus
and scoring rules from `library/tools/retrieval_eval_set.json`. The existing
frame index was used; no index rebuild, model download, or held-out query was
used during calibration. Settings were compared by changing only candidate
pool size, interval limit, or merge gap. The baseline uses pool 40, five
intervals, and a 30-second gap.

Stage diagnosis found full indexed-frame coverage for all five calibration
queries. Four queries had positive frames in their candidate pool and returned
at least one positive interval. C05 had 29 labeled positive spans, 29 indexed
frames in those spans, and two positive candidate frames, but none of its five
returned intervals touched a positive span. The observed bottleneck was
interval ranking or merging, with candidate ranking also sparse for C05. The
calibration baseline had macro recall 0.0108 and macro precision 0.5200 over
five queries (13 useful intervals among 25 returned intervals).

## Calibration sweep

| Change from baseline | Macro recall | Macro precision | Useful intervals / returned |
|---|---:|---:|---:|
| Candidate pool 60 | 0.0112 | 0.5200 | 13 / 25 |
| Candidate pool 80 | 0.0174 | 0.5200 | 13 / 25 |
| Candidate pool 120 | 0.0240 | 0.5200 | 13 / 25 |
| Interval limit 10 | 0.0255 | 0.5000 | 25 / 50 |
| Interval limit 20 | 0.0528 | 0.5011 | 50 / 100 |
| Merge gap 5 seconds | 0.0069 | 0.5200 | 13 / 25 |
| Merge gap 10 seconds | 0.0069 | 0.5200 | 13 / 25 |
| Merge gap 20 seconds | 0.0073 | 0.5200 | 13 / 25 |
| Merge gap 45 seconds | 0.0186 | 0.5200 | 13 / 25 |
| Merge gap 60 seconds | 0.0260 | 0.5200 | 13 / 25 |
| Merge gap 90 seconds | 0.0630 | 0.4800 | 12 / 25 |

The interval limit experiments increase the number of ranges a search returns.
The 90-second merge setting produces the largest calibration recall, but lowers
precision and merges distant sightings into broad ranges. The 60-second gap
was the calibration choice for a single held-out run because it raised recall
while retaining baseline precision and the same number of returned intervals.

## Held-out confirmation, run once

The frozen EVAL-02 set was evaluated once with pool 40, five returned
intervals, and the calibration-selected 60-second merge gap. Code revision was
`20e8e943b1d1ed6e618792197ffb8e329fd90223`.

| Held-out measurement | Shipped baseline, 30 seconds | Candidate, 60 seconds |
|---|---:|---:|
| Macro recall, 4 present queries | 0.1473 | 0.3721 |
| Macro precision | 0.6667 | 0.6667 |
| Useful intervals | 4 | 2 |
| Query seconds | 6.455 | 4.600 |
| Seconds per useful interval | 1.61 | 2.30 |
| Negative queries abstained | 2/3 | 2/3 |

Held-out recall rose on V02 (0.2558 to 0.4884) and V06 (0.3333 to 1.0), while
V03 and V07 remained at zero. Precision and negative behavior were unchanged.
The lower total latency is not evidence of a speedup because first-query model
load time differs across runs. The candidate's useful-interval cost worsened,
and its longest returned ranges reached 210 seconds. No retrieval setting is
adopted.

The stage diagnostic now carries the search policy actually used by each
result. This keeps candidate pool size, interval limit, and merge gap accurate
when the benchmark evaluates a calibration experiment instead of defaults.
