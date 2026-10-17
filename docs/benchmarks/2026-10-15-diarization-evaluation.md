# Speaker diarization evaluation plan and current measurements

Date: 2026-10-15; round-3 scorecards added 2026-10-16. Scope: AUD-016 through AUD-029. The only footage scope
for this lane is the geo-podcast interview and Apple 001 material identified
in the task brief. Media remains read-only; annotation and evaluation artifacts
are kept outside the repository until their distribution is approved.

## What already exists

- `library/tools/single_track_diarization.py` is the incumbent: 16 kHz mono,
  ECAPA embeddings from 1.5 second windows at 0.5 second hops, an energy VAD,
  agglomerative clustering, and an estimated speaker count. It emits turns,
  window embeddings and cluster centroids. It assigns one speaker per turn and
  explicitly does not represent simultaneous speakers.
- `library/tools/person_entity.py` preserves ECAPA centroids and separately
  measures and joins voice identities across sources. `VOICE_MATCH_MIN_SECONDS`
  and the measured cross-source threshold are identity matching policy; they
  are not diarization outputs.
- `library/tools/overlapped_speech.py` independently measures overlap using
  per-camera microphones. It is not a replacement for overlap-aware diarization.
- `library/tools/speech_measurements.py::diarization_der` already measures
  single-speaker DER, but explicitly returns unsupported for overlapped
  references. It does not report missed speaker changes.
- Historical measurements in `docs/CROSS_SOURCE_VOICE_MEASUREMENT.md` and the
  incumbent module docstring are useful context, but do not share a frozen,
  agent-labeled corpus and protocol with the candidates below. They are not a
  comparative scorecard for this bakeoff.

## Evaluation set

The frozen 12-sample set was selected and annotated from the permitted footage only. It covers these strata:

| Stratum | Required coverage | Split use |
|---|---|---|
| Interview turns | clean and acoustically difficult turn changes | calibration and held-out |
| Podcast | close and distant speech, bleed, and natural overlap | calibration and held-out |
| Multicamera | synchronized views and differing camera microphones | calibration and held-out |
| Overlap | simultaneous speech, including unequal and near-equal levels | calibration and held-out |
| Long recording | sustained speaker continuity and later recurring turns | calibration and held-out |
| Apple 001 | the allowed separate recording for cross-recording identity checks | calibration and held-out |

The manifest records stable sample IDs, source ID, source interval, duration,
stratum, split, source-clock mapping, and privacy-safe media reference. At least
two independent blind agent labellers from distinct model families label every
sample; a third independent blind agent resolves disagreements. Candidate
outputs stay hidden from all three. The evaluation artifact records pairwise
agent-label agreement before adjudication. No evaluation sample or labels are
created from unrelated project footage.

The 2026-10-16 settings freeze fixes the 0.25 s DER collar, 0.5 s change tolerance, per-sample global one-to-one speaker mapping, CLI-default thresholds, and Sortformer streaming/offline modes. Held-out outputs below were generated and scored once after that freeze. No tuning followed the held-out run.

## Measurements and policy

`library/tools/diarization_eval.py` provides the overlap-aware evaluator. It
reports DER with miss, false-alarm and speaker-confusion time, plus missed and
spurious speaker changes. Overlap is represented as multiple active speaker
IDs. A prediction may be marked uncertain and may carry per-speaker
probabilities. Probabilities are stored as raw model values; no cross-model
calibration is claimed. Persistent identity is measured separately through
the existing ECAPA cross-source identity study and never folded into within-
recording DER.

Every candidate scorecard must include the common corpus checksum and:

- DER and its miss, false-alarm, and confusion components;
- speaker-change recall, missed changes, and spurious changes;
- annotation agreement and overlap coverage;
- cold and warm end-to-end latency, peak memory, and decode/inference/
  serialization time split;
- model and runtime published revisions, precision, and exact license class;
- an adoption decision: adopt, retain baseline, research-only, conditional
  fallback, or reject.

## Candidate and license status

| Candidate | Published identity / license evidence | Local status | Decision |
|---|---|---|---|
| ECAPA baseline | `speechbrain/spkrec-ecapa-voxceleb` at revision `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286`; Apache-2.0 model card | existing MPS path; no new download | retain baseline for identity embeddings and current integration; diarization scorecard shows poor transfer on this evaluation set |
| FluidAudio Sortformer offline | CoreML conversion `FluidInference/diar-streaming-sortformer-coreml` at `ae9a27ab45dc0aa3abede7d2d6bad2b7a69aa6d1` (CC-BY-4.0); base checkpoint `nvidia/diar_streaming_sortformer_4spk-v2.1` at `cd03eee90fbec18297ac31b8c21546e596b7f71c` (NVIDIA Open Model License); published 6-bit palettized offline bundle; FluidAudio source Apache-2.0 | commercial use permitted with CC-BY attribution and under the NVIDIA Open Model License; both license records map to the implementation and output records carry conditions | adopt offline Sortformer for speaker turns; ECAPA computes one identity embedding per resulting turn and remains the fallback diarizer. Cross-recording identity continues to use ECAPA embeddings. |
| pyannote Community-1 | Hugging Face model card identifies CC-BY-4.0 and gated access requiring accepted conditions/token | Weights NOT downloaded (`HF_TOKEN` unset, conditions unaccepted); `pyannote.audio 4.0.4` is present only in the system python 3.14, not the project venv (which deliberately carries no pyannote); contract adapter `library/tools/pyannote_diarization.py` maps Annotation output to evaluator turns, unmeasured | conditional fallback pending authorized access and bakeoff |
| pyannote Precision-3 | Hosted pyannoteAI service; pricing page lists €0.112/hour for batch Precision-3 at Developer pricing, with €19/month plan | no service call or audio upload; no on-device checkpoint. Cost math (AUD-024): a 53 s stem costs ~€0.0016, the 44-minute episode ~€0.082, plus the €19/month plan - cost is NOT the blocker | research-only: hosted path does not match the local-device bakeoff, and no permission to send this footage off-device was given |
| Desert Ant Who | Official SDK repository lists Who under "In closed beta": weights exist, but no SDK ships it; source-available SDK license says scale requires a commercial license | not downloaded or evaluated | deferred until a shipping SDK and model-specific commercial terms are available |

Sources: [NVIDIA Sortformer v2.1 model card](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1/tree/cd03eee90fbec18297ac31b8c21546e596b7f71c), [NVIDIA Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/), [FluidInference CoreML model card at evaluated revision](https://huggingface.co/FluidInference/diar-streaming-sortformer-coreml/tree/ae9a27ab45dc0aa3abede7d2d6bad2b7a69aa6d1), [FluidAudio source license at evaluated revision](https://github.com/FluidInference/FluidAudio/blob/82eb4c1e0ab67e1c7d8de5092aa42a8f74cbd7b8/LICENSE), [SpeechBrain ECAPA model card](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb), [FluidAudio Sortformer documentation](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/Diarization/Sortformer.md),
[FluidAudio diarization getting started](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/Diarization/GettingStarted.md),
[Community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1),
[Precision-3 model card](https://huggingface.co/pyannote/speaker-diarization-precision),
[pyannoteAI pricing](https://www.pyannote.ai/pricing),
[Desert Ant Core SDK repository](https://github.com/Desert-Ant-Labs/desert-ant-core).

## Downloads on record (all under the 2026-10-15 approval; removed after benchmark)

| Model | Published revision | Precision and size measured on disk | Path, now removed |
|---|---|---|---|
| Sortformer streaming | `FluidInference/diar-streaming-sortformer-coreml`, `ae9a27ab45dc0aa3abede7d2d6bad2b7a69aa6d1` | 6-bit palettized CoreML, 94,848 KiB | `~/Library/Application Support/FluidAudio/Models/sortformer/v3/palettized/Sortformer_v2.1.mlmodelc` |
| Sortformer offline | same model revision | 6-bit palettized CoreML, 90,084 KiB; fp16 CoreML, 248,212 KiB | `.../v3/palettized/SortformerOffline_v2.1.mlmodelc` and `.../v3/fp16/SortformerOffline_v2.1.mlmodelc` |

The 6-bit set was used for the frozen streaming/offline bakeoff. The offline
fp16 copy was downloaded only for the required calibration precision-gap
rerun. The fp16 run raised calibration micro DER from 0.2431 to 0.3043 and
macro DER from 0.4949 to 0.5363; it detected 4/12 reference speaker changes
rather than the 6/12 found by 6-bit offline. The fp16 calibration run did not
open or rescore held-out. The pinned 6-bit offline bundle is published in
the same Hub repository at `v3/palettized/SortformerOffline_v2.1.mlmodelc`.
FluidAudio SDK source was cloned at published Git HEAD
`82eb4c1e0ab67e1c7d8de5092aa42a8f74cbd7b8` and built under
`/private/tmp/vep-mu-diarize-fluidaudio`. The evaluation model trees were
removed after scoring; `scripts/install_sortformer.sh` restores the pinned
runtime and model under the shared machine environment when needed.

## Measured runtime (M5 / 24 GB Mac, 53.095 s stems; Sortformer fp16)

Stems (read-only): `lucie/geo-podcast/audio/akshita_audio.wav` (close mic,
single dominant speaker) and `craig_audio.wav` (distant mic, bleed).
Peak RSS is `/usr/bin/time -l` maximum resident set size of the CLI process;
ANE/GPU-side memory is not in that number.

| Pass | Cold init | Decode | Inference | Serialize | Peak RSS | Segments found |
|---|---|---:|---:|---:|---:|---|
| Sortformer streaming, akshita | 173.51 s (first-ever, incl. compile) | 0.436 s | 76.68 s cold / 64.68 s warm (0.7-0.8x real-time) | 0.034 s | 350,257,152 B cold / 310,198,272 B warm | spk0 25.20-27.60, 27.76-45.04, 47.84-52.88 (24.7 s) |
| Sortformer streaming, craig | warm only | 0.008 s | ~60 s wall | 0.003 s | 509,968,384 B | spk0 1.12-20.64, 20.72-24.32, 45.36-47.52; spk1 0.40 s at 24.40-24.80 |
| Sortformer offline, akshita | 85.55 s (model download) + load | 0.020 s | 4.69 s | 0.001 s | 328,908,800 B cold | spk0 25.20-44.96, 47.84-53.12 (25.04 s) |
| Sortformer offline, craig | warm | 0.004 s | 1.24 s | 0.001 s | 77,709,312 B | spk0 0.16-0.56, 1.12-24.32, 45.36-47.52; spk1 0.40 s at 24.40-24.80 |
| ECAPA incumbent (MPS), akshita | 14.77 s wall (infer 5.6 s) | inside pass | 5.6 s cold / 1.65 s warm wall | - | 489,750,528 B | speaker_01 24.5-45.0, 47.0-53.0 (k=1, 2 turns) |
| ECAPA incumbent (MPS), craig | 15.96 s wall (infer 8.1 s) | inside pass | 8.1 s cold / 4.43 s warm wall | - | 515,424,256 B | speaker_01 0-24.5; speaker_02 44.5-47.0 (k=2, 2 turns) |

The akshita streaming rerun reproduced the first pass's segments exactly
(deterministic across runs). ECAPA cold/warm turns matched exactly on both
stems. The CLI JSON carries per-frame speaker probabilities
(`frameProbabilitiesBySpeakerIndex`); they are stored raw, with no
calibration claim.

## Streaming vs independently windowed (AUD-021)

The `--offline` CLI mode is the windowed mode: a fused `mel -> speaker_preds`
graph over fixed 30.72 s windows (3072 mel frames to 384 output frames),
tiled with ~8 s overlap and stitched by `SortformerSpeakerStitcher` - no
streaming state carried across the file. Findings on these two stems:

- Akshita: windowed and streaming agree to ~0.2 s on all boundaries; both
  report one speaker, no identity reset visible at the 30.72 s window edge.
- Craig: both modes agree on the dominant speaker and on the 0.40 s second
  voice at 24.40-24.80; windowed adds a 0.16-0.56 s onset blip streaming
  does not emit.
- Cost: windowed warm inference is 1.24-4.69 s vs streaming's ~60-65 s on
  the same 53 s - better than real-time by an order of magnitude, because it
  makes one fused pass per window instead of stepping state every 480 ms.

These are runtime and agreement observations only. They are not DER,
overlap, or speaker-change quality scores: without the blind agent labels
there is no reference to score against, and the craig 44.5-47.5 s region is
exactly where ECAPA (second speaker) and Sortformer (same speaker 0)
disagree.

## Persistent identity (AUD-022, AUD-025, AUD-026)

Within-recording diarization now includes the 150 s held-out LCATL0013 window
and later turns at 600-660 s in LC4932. Persistent cross-recording identity
was not remeasured here and stays with the ECAPA path it already lives on:
`person_entity.py`
(`VOICE_MATCH_MIN_SECONDS = 21.0`, FAR=0/FRR=0 threshold study in
`docs/CROSS_SOURCE_VOICE_MEASUREMENT.md` over 12 geo-podcast sources) and
the single-track module's long-form reads (E3 overlap DER 0.0807, E4
44-minute episode DER 0.0945). Within-recording DER and cross-recording
identity are never folded into one number. The continuity claim this lane
can make now: the windowed stitcher held one identity across its 30.72 s
window edge on both 53 s stems (no label reset at the boundary).

Apple 001 calibration check (different acoustic environment, phone footage):
`IMG_1808.MOV` and `IMG_1807.MOV` are scored against the round-3 adjudicated
agent references below. The former contains brief reference speech and is
missed by both Sortformer offline precision variants; the latter contains a
short uncertain reference interval and is missed by all three measured
candidates. These are narrow calibration observations, not broad claims
about music rejection or quiet-speech performance.

## Round-3 blind labels and measured bakeoff

The frozen reference file is `adjudicated-references-round3.jsonl`
(SHA-256 `5ba42533bd0250d3fc9adc0a2f6f3310b48523029ef6ea61765eef62c963e811`).
The settings freeze is `settings-freeze-round3.json` (SHA-256
`f38474340b4983fb8ab3f68bad2b7797086f3858d9f6f4fc2d7d6d8b7afd2faa`).
Labels are `agent_labels`, not human gold. Two independent blind annotators
(OpenAI/Codex and opencode muse-spark) agreed within the predeclared 0.25 s
tolerance on 1/12 samples; their mean pairwise overlap-aware DER was 0.3266.
A third independent blind Anthropic Claude Sonnet 5.5 annotator adjudicated
the eleven disputed samples. This level of disagreement limits the precision
of any candidate ranking. No adjudicated reference interval marks simultaneous
speakers, so overlap quality is unmeasured, despite overlap being sought in
the sample design.

| Sample | A/B pairwise DER | A/B speech-count disagreement (s) | Reference source |
|---|---:|---:|---|
| DIA-C1 | 0.3101 | 2.85 | third-family adjudication |
| DIA-C2 | 0.3471 | 2.56 | third-family adjudication |
| DIA-C3 | 0.1872 | 5.11 | third-family adjudication |
| DIA-C4 | 0.4283 | 2.43 | third-family adjudication |
| DIA-C5 | 0.3077 | 0.48 | third-family adjudication |
| DIA-C6 | 0.1481 | 0.12 | A/B agree within timing tolerance |
| DIA-H1 | 0.3798 | 3.61 | third-family adjudication |
| DIA-H2 | 0.0208 | 0.93 | third-family adjudication |
| DIA-H3 | 0.4451 | 5.06 | third-family adjudication |
| DIA-H4 | 0.3823 | 2.26 | third-family adjudication |
| DIA-H5 | 0.0568 | 2.91 | third-family adjudication |
| DIA-H6 | 0.9054 | 16.08 | third-family adjudication |

Scoring uses overlap-aware DER, 0.25 s reference-boundary collar, a global
optimal one-to-one speaker mapping per sample, and 0.5 s speaker-change
matching tolerance. Errors above 1.0 DER are possible when false-alarm time
exceeds reference speaker time. Durations are scored speaker-seconds after
the collar. The held-out predictions were created once from frozen settings;
no parameters or thresholds were changed after they were opened.

### Aggregate quality

| Candidate and precision | Calibration macro / micro DER | Calibration miss / FA / confusion seconds | Calibration changes matched / reference (spurious) | Held-out macro / micro DER | Held-out miss / FA / confusion seconds | Held-out changes matched / reference (spurious) |
|---|---:|---:|---:|---:|---:|---:|
| ECAPA incumbent, MPS default precision (no explicit quantization) | 1.7762 / 1.2266 | 164.80 / 77.54 / 5.21 | 0 / 12 (2 estimated) | 1.3945 / 0.9802 | 237.84 / 105.75 / 12.59 | 0 / 23 (25 estimated) |
| Sortformer streaming, 6-bit palettized | 0.5338 / 0.3373 | 18.17 / 1.33 / 5.36 | 4 / 12 (39 estimated) | 0.5998 / 0.5548 | 118.46 / 60.88 / 19.59 | 7 / 23 (49 estimated) |
| Sortformer offline/windowed, 6-bit palettized | 0.4949 / 0.2431 | 25.50 / 11.10 / 11.21 | 6 / 12 (32 estimated) | 0.6094 / 0.5869 | 103.16 / 69.26 / 38.11 | 7 / 23 (45 estimated) |
| Sortformer offline/windowed, fp16 calibration precision check | 0.5363 / 0.3043 | 35.05 / 9.90 / 14.64 | 4 / 12 (26 estimated) | not run | not run | not run |

Change counts are reported as matched/reference, with estimated totals in
parentheses; missed changes equal reference minus matched. Sortformer
streaming change recall falls from 4/12 on calibration to 7/23 held-out;
offline falls from 6/12 to 7/23. ECAPA misses all speaker changes on both
splits. The 6-bit offline mode wins calibration DER, but its held-out micro
DER is slightly worse than streaming and it introduces more confusion time.
The candidate does not have enough reliable speaker-change evidence for
adoption, and overlap quality remains unmeasured.

### Sample DER

| Sample | ECAPA | Sortformer streaming 6-bit | Sortformer offline 6-bit |
|---|---:|---:|---:|
| DIA-C1 | 0.5272 | 0.5093 | 0.2307 |
| DIA-C2 | 1.8489 | 0.0987 | 0.0707 |
| DIA-C3 | 0.8835 | 0.4059 | 0.3376 |
| DIA-C4 | 1.3603 | 0.3555 | 0.3304 |
| DIA-C5 | 5.0370 | 0.8333 | 1.0000 |
| DIA-C6 | 1.0000 | 1.0000 | 1.0000 |
| DIA-H1 | 0.4692 | 0.4313 | 0.3701 |
| DIA-H2 | 1.5405 | 0.4240 | 0.4291 |
| DIA-H3 | 0.7405 | 0.1830 | 0.2794 |
| DIA-H4 | 1.2234 | 1.9543 | 2.0396 |
| DIA-H5 | 0.2388 | 0.2300 | 0.1902 |
| DIA-H6 | 4.1546 | 0.3763 | 0.3479 |

C5's 5.037 ECAPA DER reflects 9.52 seconds false alarm against just 1.89
scored reference speaker-seconds, so the value exceeds 1.0.
The short C5/C6 references contribute little to the corpus denominator, so
macro and micro DER are both shown.

### Runtime, memory, and serialization

All model processes used the resource scheduler, one process per model lane
at a time. Sortformer used a 0.75 GB reservation. A separate short streaming
probe measured 0.35 GB peak RSS and 0.077 GB physical footprint; all six-file
calibration and held-out mode runs completed without reaching their reservation.
The six-file fp16 calibration check also completed under 0.75 GB. ECAPA
calibration completed within a 2.0 GB reservation. Its C1 probe measured
0.63 GB peak RSS and 1.701 GB physical footprint. The held-out batch reached
2.87 GB process-tree physical footprint under a 2.0 GB declaration and was
stopped by the scheduler after saving H1-H5; H6 was then run alone with a
3.5 GB reservation. All six held-out samples therefore have exactly one
prediction. The stopped job's 2.87 GB measurement is retained as the observed
batch peak, not hidden as a successful 2.0 GB run.

| Candidate / mode | Cold model initialization | Subsequent per-sample inference | Decode / serialize split | End-to-end wall sample latency |
|---|---:|---:|---|---|
| ECAPA MPS | first call 2.38-2.79 s wall in fresh Python runs, including import/model setup | 0.2-1.2 s per call; calibration median 0.5 s, held-out median 0.85 s; each call reloads the ECAPA encoder | read/decode median ~0.0012 s; JSON serialization median ~0.00023 s | calibration subsequent-call median 0.35 s, held-out subsequent-call median 0.82 s; first-call wall as above |
| Sortformer streaming 6-bit | CLI reports model initialization around 0.07-0.12 s after the one-time model download | calibration median 8.79 s, held-out median 5.79 s; 150 s held-out H3 took 14.89 s | CLI does not expose decode or serialization timings; no split is claimed | per-call wall was not separately instrumented; processing time is the reported inference proxy |
| Sortformer offline 6-bit | CLI reports model initialization around 0.11-0.21 s after the one-time model download | calibration median 0.17 s, held-out median 0.20 s; 150 s held-out H3 took 0.44 s | CLI does not expose decode or serialization timings; no split is claimed | per-call wall was not separately instrumented; processing time is the reported inference proxy |
| Sortformer offline fp16 calibration check | first model initialization was 18.14 s (model fetch and load together); subsequent calls 0.11-0.15 s | calibration median 0.12 s | CLI does not expose decode or serialization timings; no split is claimed | per-call wall was not separately instrumented; processing time is the reported inference proxy |

Sortformer streaming is slower than offline but remains faster than real-time
on these clips. Offline inference is 300x real-time on H3. Local inference had
no hosted API charge; electricity and machine amortization were not metered.
These figures do not include first-time checkpoint download or caller
orchestration. CLI JSON records segments, duration, processing time, and RTFx,
but no decode/serialization split or per-speaker probabilities. No model
probability calibration was performed. ECAPA emits no speaker probabilities;
raw Sortformer probabilities remain model-specific and are not compared as
calibrated confidence scores. The formal `ren_evaluation_report/1` helper
requires numeric timing and cost fields, so no formal JSON report is emitted
with fabricated zeros for unavailable Sortformer measurements. The Markdown
scorecards report every measured value and identify the unavailable split.

### Adoption and scope decisions

| Candidate | Decision | Reason |
|---|---|---|
| ECAPA incumbent | retain baseline for current pipeline and persistent voice-identity embeddings | Existing integration remains valuable for identity matching, but this set's DER is poor, especially on later windows; it misses every reference speaker change. The evaluation does not replace the separate cross-source identity study. |
| Sortformer streaming 6-bit | research-only | Better DER than ECAPA here and efficient enough for streaming, but change recall is low and held-out mode comparison does not show a stable quality winner. |
| Sortformer offline 6-bit | adopt as default speaker-turn diarizer; ECAPA identity embeddings and fallback retained | Commercial use is permitted with conditions; this mode had the best calibration DER and fast runtime. Held-out DER remains .6094 macro / .5869 micro with weak speaker-change recall, which remains a product limitation. |
| Sortformer offline fp16 | reject as a precision default for this bakeoff | Calibration DER is worse than the 6-bit offline run; no held-out run was made because held-out was already scored once. |
| pyannote Community-1 | conditional fallback pending authorized access and a separate scheduled bakeoff | Adapter is ready; weights were not downloaded. |
| pyannote Precision-3 | research-only | Hosted path and service cost do not match the local-device bakeoff, and no permission to send this footage off-device was given. |
| Desert Ant Who | deferred | No shipping SDK and model-specific commercial terms are available. |

`single_track_diarization.diarize_track` now routes through the registered offline Sortformer implementation. ECAPA measures turn-level identity vectors and remains the fallback if Sortformer is unavailable; it also remains the cross-recording voice matcher. The model provenance and license conditions travel with transcript diarization and M1b voice tracks. Sortformer supports at most four speakers; requests above that limit fall back to ECAPA. The allowed corpus is
small, A/B agreement is low, the adjudicated references contain no overlap,
and Sortformer has weak change recall. Persistent identity remains a separate
ECAPA measurement; this task does not establish cross-recording identity
accuracy for Sortformer. Downloads were removed after measurement. The full
precision offline check was calibration-only; the single frozen held-out run
for each main mode was not repeated.
