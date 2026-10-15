# Audio defect benchmark

`library.tools.analysis.audio_defect_benchmark` defines the v1 event contract
and scorer for clipping, noise (including hum), loudness mismatch, dropouts,
dialogue interruption, room-tone discontinuity, and music/dialogue masking.
Events are source-time half-open intervals. The scorer uses one-to-one
same-class matching, reports precision/recall, duration and boundary error,
and false alarms per program hour. Runtime is reported as seconds per program
minute. Correction recommendations are recorded and reported separately from
detection quality.

## Synthetic held-out protocol

Use a clean excerpt from a rights-cleared source already marked `held_out` in
the editorial corpus manifest. Keep the extracted source excerpt read-only.
`inject_defects(samples, sample_rate, recipes)` produces an untouched control,
one isolated variant per recipe, and exact annotations from each injected
interval. Record recipe strengths, random seeds, base source ID and hash in the
benchmark manifest. Injected variants belong only to `held_out`; the validator
refuses synthetic variants in training or calibration and refuses a leakage
group crossing splits. Keep labels sealed from tuning. The report must call
these synthetic-defect results and must not infer real-world defect prevalence
from them.

The source-separation route is excluded. It is not invoked by the injector or
scorer. Synthetic event scores measure localization and false alarms under
controlled perturbations; real-defect labels and correction recommendations
still require separate human review.

## Score input

The scorer accepts one JSON document containing `program_seconds`,
`runtime_seconds`, `annotations`, `predictions`, and optionally
`correction_suggestions`. For an audio file, provide `audio_path`,
`program_seconds`, `annotations`, and optional `speech_spans`; this executes
FI-12 and its baseline event adapter:

```sh
bin/vep -m library.tools.heavy_work_lock run --owner vep-eval07 \
  --profile music.analyse:model_memory -- \
  bin/vep -m library.tools.analysis.audio_defect_benchmark <score-input.json>
```

The acceptance report must include per-defect and overall scores, elapsed
runtime, seconds per program minute, the held-out source hash, and the
measurement executor/version. The initial FI-12 adapter emits candidates for
clipping, broadband noise, loudness jumps, silence/dropouts, dialogue
interruptions when speech spans are supplied, and quiet-band centroid jumps.
It emits no music/dialogue masking candidates because FI-12 has no independent
measurement for that relation; this limitation must remain visible in recall.
This module supplies the evidence contract, controlled injector and independent
event scorer. Synthetic held-out scores are not real-defect prevalence claims.

## Turning measurements into an editorial report

`problem_report_from_audio_quality(measurement, speech_spans=...)` produces
timestamped, actionable `measured_candidate` findings. These are review
signals, not automatic technical failures: transient loudness and spectral
changes can be part of dialogue performance. The report keeps
`editorial_preferences` empty, identifies why masking was not assessed without
dialogue/music stem evidence, and never presents an editorial choice as a
measured defect. A nearly silent isolated channel (below -55 LUFS) is treated
as inactive and does not generate a full-program dropout alarm. Adjacent
windows are coalesced into one review episode.

For a source-level JSON report, use the existing measurement CLI with
`--problems`; repeat `--speech-span START:END` to localize interruptions:

```sh
bin/vep -m library.tools.heavy_work_lock run --owner audio-review \
  --profile music.analyse:model_memory -- \
  bin/vep -m library.tools.audio_quality <source> --problems \
    --speech-span 30:45
```

On the calibration podcast LCATL0012, source seconds 30-90, the initial
one-minute track pass produced 7 candidates on track 0, 13 on the nearly
silent track 1 (including one full-span dropout), 5 on track 2 and 14 on track
3. After episode grouping and the inactive-track gate it produced 4, 0, 4 and
9 candidates respectively (39 down to 17, a 56% reduction). The nearly silent
track measured -67.41 LUFS. These are unreviewed
screening candidates, not confirmed false positives; the transcript and
editorial annotation do not label every acoustic transition as clean or
defective. FI-12's EVAL-07 synthetic control remains the only labeled clean
false-alarm measurement in this baseline (0.0 alarms/program-hour). The
calibration pass motivates the inactive-track gate and episode grouping, but
does not tune thresholds against held-out variants.
