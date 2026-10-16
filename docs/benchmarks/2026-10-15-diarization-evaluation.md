# Speaker diarization evaluation plan and current measurements

Date: 2026-10-15. Scope: AUD-016 through AUD-029. The only footage scope
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

The frozen set will be selected and annotated from the permitted footage only.
It must contain these strata:

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

Calibration choices (collar, speaker-count constraints, thresholds and any
window sizes) are frozen before the held-out set is opened. Held-out is run
once after the settings are fixed. Windowed Sortformer windows are scored with
their actual reset behavior and boundary stitching, separately from a single
streaming pass.

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
| ECAPA baseline | `speechbrain/spkrec-ecapa-voxceleb` at revision `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286` (per `shared_environment.ECAPA_REVISION`); existing code and measured path | installed dependency; frozen identity available in `measurement_identity()` | retain baseline pending comparable set |
| FluidAudio Sortformer | Official FluidAudio documentation identifies four speaker slots, streaming and complete-buffer APIs, and NVIDIA Open Model License | Streaming + offline fp16 models downloaded for personal benchmark; revisions and sizes below | research-only until labeled bakeoff |
| pyannote Community-1 | Hugging Face model card identifies CC-BY-4.0 and gated access requiring accepted conditions/token | Weights NOT downloaded (`HF_TOKEN` unset, conditions unaccepted); `pyannote.audio 4.0.4` is present only in the system python 3.14, not the project venv (which deliberately carries no pyannote); contract adapter `library/tools/pyannote_diarization.py` maps Annotation output to evaluator turns, unmeasured | conditional fallback pending authorized access and bakeoff |
| pyannote Precision-3 | Hosted pyannoteAI service; pricing page lists €0.112/hour for batch Precision-3 at Developer pricing, with €19/month plan | no service call or audio upload; no on-device checkpoint. Cost math (AUD-024): a 53 s stem costs ~€0.0016, the 44-minute episode ~€0.082, plus the €19/month plan - cost is NOT the blocker | research-only: hosted path does not match the local-device bakeoff, and no permission to send this footage off-device was given |
| Desert Ant Who | Official SDK repository lists Who under "In closed beta": weights exist, but no SDK ships it; source-available SDK license says scale requires a commercial license | not downloaded or evaluated | deferred until a shipping SDK and model-specific commercial terms are available |

Sources: [FluidAudio Sortformer documentation](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/Diarization/Sortformer.md),
[FluidAudio diarization getting started](https://github.com/FluidInference/FluidAudio/blob/main/Documentation/Diarization/GettingStarted.md),
[Community-1 model card](https://huggingface.co/pyannote/speaker-diarization-community-1),
[Precision-3 model card](https://huggingface.co/pyannote/speaker-diarization-precision),
[pyannoteAI pricing](https://www.pyannote.ai/pricing),
[Desert Ant Core SDK repository](https://github.com/Desert-Ant-Labs/desert-ant-core).

## Downloads on record (all under the 2026-10-15 approval; removed after benchmark)

| Model | Published revision | Size on disk | Path |
|---|---|---:|---|
| Sortformer streaming fp16 | HF `ae9a27ab45dc0aa3abede7d2d6bad2b7a69aa6d1` (pinned in SDK `ModelNames.swift`) | 239,376,768 bytes in the two published weight files (8,948,544 and 230,428,224 bytes) plus config; 229 MB compiled | `~/Library/Application Support/FluidAudio/Models/sortformer/v3/fp16/Sortformer_v2.1.mlmodelc` |
| Sortformer offline fp16 (whole-file/windowed) | same repo/revision line as streaming (SDK downloads the `SortformerOffline_v2.1` artifact, no separate pin published) | 243 MB compiled | `.../v3/fp16/SortformerOffline_v2.1.mlmodelc` |

Both are fp16, not quantized variants. FluidAudio SDK source was cloned at
published Git HEAD `82eb4c1e0ab67e1c7d8de5092aa42a8f74cbd7b8` and built
locally at `/private/tmp/vep-mu-diarize-fluidaudio` (scratch, outside the
repo); the build does not modify the repository. Neither model is adopted:
both are removed after the benchmark unless a labeled bakeoff adopts one.

## Measured runtime (M5 / 24 GB Mac, 53.095 s stems, fp16 throughout)

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

No new long-form measurement here: no recording longer than the 53 s stems is
available on this machine (the multitrack MXF sources live with the captain;
Apple 001 raw clips are 3-17 s). Persistent cross-recording identity stays
with the ECAPA path it already lives on: `person_entity.py`
(`VOICE_MATCH_MIN_SECONDS = 21.0`, FAR=0/FRR=0 threshold study in
`docs/CROSS_SOURCE_VOICE_MEASUREMENT.md` over 12 geo-podcast sources) and
the single-track module's long-form reads (E3 overlap DER 0.0807, E4
44-minute episode DER 0.0945). Within-recording DER and cross-recording
identity are never folded into one number. The continuity claim this lane
can make now: the windowed stitcher held one identity across its 30.72 s
window edge on both 53 s stems (no label reset at the boundary).

Apple 001 spot check (different acoustic environment, phone footage):
`001/raw/IMG_1807.MOV` (17.1 s, mean -27 dB / max 0.0 dB) produced zero
Sortformer offline segments AND zero ECAPA VAD-kept windows
(`DiarizationUnavailable`, convergent silence). Without blind labels this is
not scored as correct music rejection or as missed quiet speech - it is
recorded as agreement between the two systems, nothing more.

## Adoption decisions (EVAL-032 shape; quality columns pending labels)

| Candidate | Precision shipped | Decision |
|---|---|---|
| ECAPA incumbent | MPS fp16 path as in `single_track_diarization.py` | retain baseline (still the only labeled-evidence path; k correct on akshita, k=2 dispute on craig awaits labels) |
| FluidAudio Sortformer streaming fp16 | fp16 CoreML | research-only until the labeled bakeoff; ~0.7x real-time makes it a non-default even if quality wins |
| FluidAudio Sortformer offline/windowed fp16 | fp16 CoreML | research-only until the labeled bakeoff; warm speed is the best measured, quality unknown |
| pyannote Community-1 (CC-BY-4.0, gated) | - | conditional fallback: no authorized access yet (`HF_TOKEN` unset, conditions unaccepted); no download attempted. Adapter `library/tools/pyannote_diarization.py` (tests in `tests/unit/audio/test_pyannote_diarization.py`) is ready so an authorized bakeoff needs no new glue; it is unmeasured by design |
| pyannote Precision-3 (hosted, EUR 0.112/hour + EUR 19/month plan) | - | research-only: hosted path does not match the local-device bakeoff and no permission to send this footage off-device was given |
| Desert Ant Who (closed beta, no shipping SDK) | - | deferred until a shipping SDK and model-specific commercial terms exist |

No formal `ren_evaluation_report/1` scorecard is emitted yet:
`scorecard_report` refuses creation without scored reference speech, and the
frozen agent-labeled manifest does not exist. The blind agent-label request
is in the task data directory (`label-request.md`); quality metrics, held-out
results, and any production default change wait on those labels.

No capability-interface change ships with this lane: every candidate reads
research-only, conditional fallback, or deferred, so the ECAPA incumbent
behind the existing interface is untouched and no adoption replaces it.
