# Temporal grounding candidate scorecard (2026-10-16)

## Calibration labels

The calibration set contains LCATL0012 and IMG_1816. Two blind agent passes used different model families (OpenAI/Codex and OpenCode Muse Spark); a third blind pass used Anthropic Claude. Pairwise event agreement at temporal IoU >= 0.5 was 2/7 and 0/4 respectively, with per-source F1 0.308 and 0. The tie-break pass confirmed two of nine disputed IMG_1816 events and none of nine LCATL0012 events. The resulting agent consensus has four events; 16 disputed source annotations remain unresolved and are excluded. Free-form tie-break labels are mapped only when their event meaning directly matches the benchmark vocabulary. See `2026-10-16-temporal-grounding-agent-agreement.json` and `2026-10-16-temporal-grounding-agent-consensus.json`.

LC4930 and IMG_1818 are held out. Their labels remain sealed and no held-out score is reported.

## Incumbent: M7/M8 action candidates

**Decision: retain baseline.** The strongest available incumbent measurements are the recorded M8-verifier proxies in `2026-10-15-action-candidate-precision.md`: on held-out LC4930, the calibration filter leaves 6 of 26 hand-near-mouth proposals, with 2/6 stored-verifier agreement precision, 2/2 stored-positive retention, and 0.38 s mean boundary error on those two positives. These values describe M8 agreement for a different action query, not event truth. The available calibration data also records 21/46 M7 proposals after filtering and 2/2 stored M8 positives retained across LCATL0012 and LC4931. On the exact temporal-grounding calibration sources LCATL0012 and IMG_1816, M7/M8 proposal rows are absent. Event accuracy, temporal IoU, onset/offset error, repeated-event recall, and false-proposal rate against this agent consensus cannot be computed; absence of rows is not scored as zero.

Cold/warm latency, peak memory, decode/infer/serialize split and end-to-end cost for the same frozen temporal queries are also unmeasured. The only comparable scored evidence is therefore the historical proxy above; it is not a temporal-grounding leaderboard.

## Available candidate scores

| Candidate | Local temporal event score | Available evidence | Decision |
|---|---|---|---|
| M7/M8 incumbent | Not measurable on the frozen temporal queries | Historical hand-near-mouth proxy: 2/6 stored-verifier agreement precision, 2/2 stored-positive retention, 0.38 s mean boundary error on LC4930; 21/46 calibration proposals after filter | Retain baseline |
| TimeLens 2 4B | Not measured | Two Hugging Face attempts stalled at a 698,667,008-byte cached partial without an emitted error; the third resumable attempt ran 139 s with a 0-byte incomplete file and no log output. No published MLX build was found. | Research-only; no result |
| TimePLE 8B | Not measured | Custom `qwen3_vl_timeple` implementation; no local inference or event predictions recorded | Research-only; deferred |
| Pocket-STVG | Not measured | No executable implementation/checkpoint identified in this lane | Monitor |

The four resolved agent-consensus events are the only adjudicated calibration truth at present. The remaining 16 disagreements are excluded, and the held-out sources remain sealed. No candidate has local temporal IoU, onset/offset, repeat recall, or false-proposal scores on the frozen queries. The table keeps unavailable scores explicit rather than treating missing prediction files as negative predictions.

## TimeLens 2 4B

**Decision: research-only, not measured.** A Q4_K_M GGUF checkpoint was identified in a third-party conversion. The lane's inference attempt ran 149.7 s without output; two Hugging Face downloads then stalled with the cached model blob at 698,667,008 bytes and emitted no error. A published MLX build was not found. The steered third resumable transfer ran for 139 s, kept a 0-byte incomplete model file, and emitted no log output before it was stopped at turn close. No TimeLens2 quality, latency, memory, or cost result is inferred from the stalled attempts; the empty resumable state and log remain outside the worktree at `/tmp/vep-mu-tgrounding-download`.

## TimePLE 8B

**Decision: research-only, benchmark deferred.** Evaluate only after the TimeLens 2 4B comparison and license review. No run or checkpoint download is recorded.

## Pocket-STVG

**Decision: conditional fallback / monitor.** No executable implementation and checkpoint have been identified in the current lane record, so no score is available.

## Machine and commercial path

The available device is an Apple M5 with 24 GB unified memory, not an M5 Pro; SEM-036 remains unmeasured. TimeLens 2 and TimePLE remain outside the commercial path. The existing M7/M8 capability remains the commercial-eligibility baseline until a candidate has a measured win and license review. No default or capability replacement is proposed.
