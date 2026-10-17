# PANNs on the sound-v2 weak-reference set

**Status:** limited, agent-labeled acoustic check; not gold ground truth.

The sound-v2 set contains five 15-second excerpts from Apple 001 and the permitted podcast material. Gemini produced eight positive event labels. An independent Anthropic-family second pass measured three as `agree_weak` (two traffic labels and one air-conditioning label), four as `flag` (three footsteps labels and the laughter span), and one as `unresolved` (footsteps under a whisper). The lane's separate spectral/energy pass also found only weak evidence for the three ambience events and no strong confirmation. The reference remains weak: both measurement passes can assess activity and boundaries, but cannot name traffic versus HVAC or establish semantic event identity. The second pass was transcript-assisted and is not a second blind semantic labeler.

## Scoring policy

Only the three positive labels both passes weakly support are scored. They are continuous low-frequency ambience claims spanning their 15-second excerpts. Both passes found a persistent low-frequency floor but no source onset or offset; excerpt boundaries are therefore censored. The scorer retains the full excerpt interval and does not claim measured boundary accuracy. There are no boundary-error samples for these three events.

The four flagged events and one unresolved event remain listed under `unscored_labels` in the inputs with their reason, and do not count as misses. All ten Gemini hard negatives are included to check false positives. “Not contradicted” means only that the second-pass acoustic checks found no contradictory candidate; it is not semantic proof of absence. The evaluator uses its declared same-label temporal-IoU threshold for hard-negative hits.

The inputs preserve exact label strings and do not alias PANNs `Vehicle` to `Traffic noise`. Scoring uses the existing class-constrained event matcher at temporal IoU 0.5. The 15-second edge-censored spans are not evidence of accurate onset/offset localization.

## Measurement

The already installed PANNs CNN14 checkpoint was used on CPU through `measure_sound_events`; no checkpoint was downloaded. The existing PANNs pipeline operating point was retained. The foreground run processed five clips under `semantics.analyse:model_memory` (20.581 seconds admitted execution, 0.635 seconds queue wait). This is a one-off report, not an adoption scorecard or a candidate comparison; no decode/inference split or peak-memory measurement was collected for this run.

| Split | Scored references | PANNs proposals | TP / FP / FN at IoU 0.5 | Hard negatives rejected |
|---|---:|---:|---:|---:|
| Calibration | 1 | 2 | 0 / 2 / 1 | 4 / 4 |
| Held out | 2 | 3 | 0 / 3 / 2 | 6 / 6 |

PANNs emitted only `Vehicle` on Apple 1816, `Vehicle` on Apple 1818, and `Vehicle` plus `Music` on Apple 1820. It emitted no proposals on either podcast excerpt. It did not emit the exact supported labels `Traffic noise, roadway noise` or `Air conditioning`; there were no matches, so boundary errors are undefined. The held-out result is reported once and was not used to tune the operating point.

The apparently perfect hard-negative rejection is limited to ten sampled spans and the scorer's exact-label, overlap rule. It does not establish broad false-positive performance. On Apple 1818, the `Vehicle` proposal overlaps the full-span `Vehicle` negative, but its temporal IoU against the 15-second negative span is below the evaluator's 0.1 hard-negative-hit floor; the evaluator therefore counts it as rejected. This illustrates why the sample count and rule must accompany the rate.

## Reproduction and artifacts

The scorer is `library/tools/analysis/sound_event_benchmark.py`. Inputs and outputs are:

- `2026-10-17-sound-v2-panns-calibration.input.json`
- `2026-10-17-sound-v2-panns-calibration.scored.json`
- `2026-10-17-sound-v2-panns-heldout.input.json`
- `2026-10-17-sound-v2-panns-heldout.scored.json`

PANNs proposals came from the five WAVs listed with hashes in the lane's `data/vep-mu-sound/label-request.md`; Gemini labels are in `data/vep-labels-audio-gemini/sound-v2/`; the independent second-pass measurements are in `data/vep-labels-audio-check/sound-v2/second-pass.json`. The first-pass acoustic measurements are in `data/vep-mu-sound/sound-v2-check.json` and the lane's `sound-v2-acoustic-review.md`.
