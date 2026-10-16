# Audio defect benchmark - FI-12 synthetic held-out baseline (EVAL-07)

`library.tools.analysis.audio_defect_benchmark` is the v1 event contract,
controlled injector and independent event scorer. This report is its initial
synthetic held-out quality/cost evidence, before the tonal peak and hum-route
calibration follow-up documented in `audio-defect-benchmark.md`.

## Protocol (synthetic defects, true by construction)

A 60 s synthetic clean program (48 kHz mono: 220 Hz + 330 Hz bed at about
-37 dBFS) stands in for a sealed held-out excerpt. `write_injected_set`
wrote one isolated variant per defect plus an untouched control; each
variant carries exact annotations from its injected interval. The FI-12
baseline (`measure_audio_quality` + `predictions_from_audio_quality`,
executor `fi12-audio-quality-event-adapter/1`) scored every variant, timed.
No captain footage, no model downloads, no tuning on these labels.

These are SYNTHETIC-defect detection scores, not real-world prevalence
claims. Real-defect hand labels remain a follow-up. Source separation was
not invoked and stays `excluded` in the manifest.

Measured 2026-10-15 under `heavy_work_lock --profile
music.analyse:model_memory` (queue wait < 0.3 s, 10.4 s execution for 9
full FI-12 runs). Boundary tolerance 2.0 s.

## Scores

| Variant defect | Precision | Recall | False alarms / program h | s / program min |
|---|---|---|---|---|
| clipping (+40 dB drive) | 0.25 | 1.00 | 180.0 | 0.8 |
| noise, broadband (+0.15) | 0.25 | 1.00 | 180.0 | 1.6 |
| noise, hum 120 Hz (+0.05) | 0.00 | 0.00 | 120.0 | 1.4 |
| loudness mismatch (+10 dB) | 0.50 | 1.00 | 60.0 | 2.0 |
| dropout (2 s silence) | 1.00 | 1.00 | 0.0 | 0.9 |
| dialogue interruption (1 s, speech span given) | 1.00 | 1.00 | 0.0 | 0.8 |
| room-tone discontinuity (white donor) | 0.25 | 1.00 | 180.0 | 1.0 |
| music/dialogue masking | 0.00 | 0.00 | 0.0 | 0.9 |
| control (no defects) | - | - | 0.0 | - |

Cost: roughly 1-2 s per program minute across variants.

## Reading

Detected with recall 1.0: clipping, broadband noise, loudness jumps,
dropouts, dialogue interruptions (when speech spans are supplied), and
room-tone jumps. Silence-based classes are exact (1.00/1.00, zero false
alarms). Two gaps stay visible: tonal hum trips no detector (FI-12 has no
tonal-noise route), and music/dialogue masking has no prediction route at
all - the adapter abstains by design, so its recall reads 0.00 rather than
a silent pass.

False-alarm rates scale from a 60 s program (one stray adjacent-interval
edge = 60/h), so compare them across variants, not against an hourly
budget; the control variant itself is clean at 0.0/h. Adjacent-interval
edge predictions (e.g. the loudness fall back to bed, the room-tone return
edge, the white donor tripping the broadband-noise detector) are the
dominant false-alarm source - a boundary-assignment problem, not hallucinated
defects. Correction recommendations are not scored here; they remain a
separate downstream decision measure per the scorer contract.
