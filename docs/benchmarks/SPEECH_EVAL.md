# Voz and MFA speech evaluation

`library/tools/speech_eval.py` is the independent scorer for the current
Voz transcript plus MFA word-placement path. It compares a gold annotation
with a saved prediction; it does not call either model. Each sample records
its split and speech condition, and the output keeps recognition, placement,
speaker attribution, refusal and runtime as separate readings.

## Evidence contract

Reference and prediction are UTF-8 JSON objects with a stable `sample_id` and
`words` arrays. Every word row has `text`, `start`, `end`, and `speaker`;
times are seconds from the source start. `speaker` may be null only when the
reference cannot attribute a speaker. A prediction may use `status` of
`answered`, `abstained`, or `failed`, plus a refusal `reason`. Reference labels
must be manually transcribed and word-aligned before any model run; predicted
text or MFA timings must never be copied into the reference.

The scorer normalizes Unicode, case, and punctuation, then reports WER and
CER with substitution, deletion, and insertion counts. Boundary errors are
computed only for exactly recognized words, separately for starts and ends,
using median and nearest-rank p95 absolute milliseconds. Speaker accuracy is
also computed only for exactly recognized words with a known reference
speaker. Failed and abstained predictions remain visible as their own counts;
they are not silently discarded. Runtime is divided by the sample's source
audio duration to report seconds per audio minute.

Punctuation has a separate micro precision/recall/F1 over exact lexical word
matches. It scores ending comma, period, semicolon, colon, exclamation, and
question marks; punctuation on inserted, deleted, or substituted words is
outside this metric, so the report keeps WER and punctuation distinct. If no
words match exactly, punctuation is `unmeasured` with null scores rather than
an apparent perfect result. This is a technical transcription metric, not an
editorial preference for which punctuation to display.

Example invocation after independent labels and a current-path prediction
exist:

```sh
bin/vep -m library.tools.speech_eval <reference.json> <prediction.json> \
  --split held_out --condition clean \
  --audio-seconds 12.0 --elapsed-seconds 4.0
```

The reviewed correction subset can be reproduced as grouped split and
condition output:

```sh
bin/vep -m library.tools.speech_eval \
  --batch docs/benchmarks/eval05-reviewed-spans.json
```

The harness availability check fails on the EVAL-01 base commit and emits
the reviewed-span split report on this branch:

```sh
git cat-file -e 81897f6ff:library/tools/speech_eval.py  # fails: absent on base
bin/vep -m library.tools.speech_eval --batch \
  docs/benchmarks/eval05-reviewed-spans.json
```

## Measurement status

The current consumer transcript is the 2026-10-04 GEO Podcast - Synced
record: Voz (`da` 0.1.1) plus MFA (`english_us_arpa` v3.0.0), arm `hybrid`.
Four spans with explicit human-reviewed wording fixes were compared with only
those corrected words. No new model was downloaded. The reproducible sample
and provenance notes are in `eval05-reviewed-spans.json`.

| Split | Reviewed samples | Reference words | S / D / I | WER | CER | Speaker accuracy | Boundary error |
|---|---:|---:|---:|---:|---:|---:|---|
| Training | 3 | 5 | 0 / 0 / 1 | 20.0% | 31.0% | 5/5 | unmeasured |
| Calibration | 1 | 3 | 0 / 0 / 0 | 0.0% | 0.0% | 3/3 | unmeasured |
| Combined reviewed subset | 4 | 8 | 0 / 0 / 1 | 12.5% | 22.5% | 8/8 | unmeasured |

The scorer includes spaces when calculating CER.

All four predictions answered; failures 0, abstentions 0. These are targeted
human-reviewed correction spans, not a random sample, so their WER is a
diagnostic and not an estimate of general corpus accuracy. The references
come from marker feedback and subtitle review records. The saved transcript
reports the current Voz/MFA path and contains the matching word output. The
training subset includes one doubled `probably` that reviewers confirmed was
spoken once; this is one insertion. Held-out sources have no human-reviewed
correction spans, and the split manifest still declares held-out labels
`not_created` and `tuning_access: false`. Speech condition is `unknown` for
these samples; the review notes do not support clean/accented/overlap/noise
labels.

The run ledger also records one 0.84-second `transcript.reindex` on LC4932
through the hybrid path: Voz 3.050 s, language identification 0.156 s, MFA
36.193 s, total 39.399 s or 2,814.2 seconds per source-audio minute. The
scorer reports this under the training split. This is a subsecond reindex whose
per-invocation MFA overhead dominates; it is not a full-file throughput
estimate. Audio extraction is excluded. No comparable current run records
word-boundary gold, so median and p95 boundary error are unmeasured. The
historical 25.6 ms wav2vec2 disagreement is not reused.

To complete the held-out and boundary measurements, the corpus custodian
must keep annotations outside tuning workspaces and double-check speech text,
speaker, and both word edges against source audio. A useful initial study is
20 minutes of labeled speech - five minutes each of clean, accented,
overlapped, and explicitly characterized noisy speech - split across training,
calibration, and held-out groups without leakage. At an estimated 5-8 minutes
of human transcription and word alignment per audio minute, that is 100-160
minutes of labeling (about 1.7-2.7 hours), plus independent review. Use
`speech-eval-label-template.json`; noisy augmentation must be declared as
synthetic if it is not naturally present.
