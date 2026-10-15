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
