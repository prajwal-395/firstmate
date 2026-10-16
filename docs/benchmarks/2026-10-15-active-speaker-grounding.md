# Active-speaker grounding lane: implementation audit and qualification plan

Scope: AUD-030 through AUD-042. Footage is limited to the existing GEO
podcast and Apple 001 originals. The podcast has synchronized camera pairs;
Apple 001 is single-camera phone footage. Source media remains read-only.

## What Ren already has

- M1/M3b produce speech spans and ECAPA diarized voice tracks. M3b also
  produces `speech_face_links` by temporal co-occurrence. That is useful as
  a candidate relation on single-person angles, but co-occurrence alone
  cannot distinguish a listener from the speaker.
- M3 measures faces, mouth landmarks, and sampled face tracks. M7's
  `link_voices_to_faces` compares mouth-motion ratios inside and outside a
  voice turn and currently publishes a `speaking` predicate. Its own
  `VOICE_FACE_MIN_FRAMES` requires only 20 sampled frames; the evidence is
  sparse compared with LR-ASD's 25 fps visual input.
- M6 measures source-clock offsets and records offset evidence. The
  cross-modal grounding executor maps measured offsets when joining
  observations from different clocks. Offset support is not universal:
  an absent or unmeasured mapping must remain unjoined.
- `cross_modal_grounding.apply_project_model` already reconciles speech,
  diarization, face identity and M7 speaking evidence. Those matches now
  remain `speaking_person_candidate` relations; disagreement remains a
  separate `speaker_face_disagreement` candidate. `cross_modal_eval` scores
  the candidate predicate under speech/person relations.
- ECAPA identity and face identity are already measured independently.
  Existing cross-source voice matching and M7 face attribution are distinct
  operations and should remain distinct.

## Gaps and constraints

1. M7 still emits its `speaking` event span from mouth movement, but that
   observation no longer creates a verified identity link. Cross-modal
   grounding emits only a candidate relation. ASD score observations can be
   projected with `add_active_speaker_candidates`; the relation cites the raw
   score, voice turn, face track and, for cross-clock audio, a time-alignment
   observation that names the measured transform. Joins beyond the explicit
   error bound are refused. The agreed relation uses
   `speaking_person_candidate`, subject = diarized voice entity, object =
   visible person entity, visible-camera interval, `temporal` class,
   `candidate` status and `inferred` certainty. Raw ASD values remain in
   evidence observations and are never copied into calibrated confidence.
2. The Ren adapter exists at `library/tools/asd_lr.py` (AUD-030): it
   consumes measured face boxes in sample coordinates plus source clocks,
   emits per-frame `active_speaker_score` observations with raw uncalibrated
   scores, and loads the publisher checkpoint only from an explicit path -
   nothing vendored, nothing fetched. Its inference was verified
   frame-identical against the calibration smoke run (249/249 scores,
   2026-10-16). Two measured limits: MPS on torch 2.8.0 has no `max_pool3d`
   kernel, so inference needs `PYTORCH_ENABLE_MPS_FALLBACK=1` (those ops
   fall back to CPU) and the device must be recorded as MPS-with-CPU-fallback;
   and face crops still need a measured face box per scored track - the
   upstream S3FD detector (separate runtime-fetched checkpoint) is not used.
3. A correct ASD run needs per-face temporal crops and synchronized audio.
   The upstream demo hardcodes CUDA, uses an S3FD face detector whose
   checkpoint is fetched separately at runtime, and expects 25 fps. Ren's
   existing sparse face sampling is not a drop-in input. The adapter
   consumes the existing face tracks and source clocks, avoids hidden network
   fetches, and preserves candidate scores rather than converting a high
   score into identity certainty.
4. C³ASD (AUD-035 verdict, 2026-10-16): no usable weights located. The paper
   (Hong and Park, "Multi-Level Consistency-Driven Representation Learning
   for Robust Active Speaker Detection", arXiv 2607.03018, July 2026) proposes
   embedding-level inter-modality consistency plus two further consistency
   constraints with no extra learnable parameters beyond a light
   classification head; no official code/weights release surfaced in the
   publisher channels checked. It cannot be benchmarked without training or
   imported weights, so it stays on the vep-mu-watch candidate list and out
   of this lane. No model was downloaded for it.
5. FunCineForge reconciliation (AUD-042, studied from the IJCAI-26 paper and
   the `speaker_diarization` recipe README; nothing imported, no code read
   into the repo): the pattern is audio speaker embeddings (CAM++) as the
   PRIMARY clusters, visible faces scored with TalkNet-ASD (Ultra-Light face
   detector, 1 frame in 5 at 25 fps, highest score wins the active frame),
   normalized face embeddings (CurricularFace) as AUXILIARY cues in joint
   unsupervised clustering (3D-Speaker style), output as RTTM. Reported
   5.3% DER audio-only to 3.7% multimodal on their multi-person conversation
   set. What Ren takes: ECAPA voice identity stays primary, ASD face scores
   stay auxiliary, and disagreement stays a separate recorded relation
   (`speaker_face_disagreement`) rather than a clustering override - which
   is also what ARCH-006 (observed/measured vs inferred) requires. What
   stays out: the dubbing synthesis stack (MLLM plus flow-matching acoustic
   decoder), dataset construction, and TTS - none of it is speaker
   attribution.

## Candidate discovery

The official [LR-ASD repository](https://github.com/Junhua-Liao/LR-ASD)
contains the IJCV 2025 implementation and AVA-trained weights. The sampled
publisher revision is `1b6dcd2d8fc2895683de6508ec6294ec47d388ca`; the AVA
checkpoint is the tracked `weight/pretrain_AVA.model` at that revision
(3,426,337 bytes). Its repository advertises 0.84M parameters, 0.51 GFLOPs,
and 94.45% AVA validation mAP; these are upstream figures, not Ren
measurements. The repository advertises MIT for its source. Checkpoint and
training-data commercial terms still require the license lane's decision.

The checkpoint was downloaded to
`/tmp/vep-mu-asd-models/LR-ASD/weight/pretrain_AVA.model` for this lane's
benchmark. The checkout is pinned above. No TalkSet checkpoint or AVA,
Columbia, or other external footage is part of the Ren qualification set.
Remove the checkpoint after the benchmark unless it is adopted; it must not
be shipped as research-only weights.

## Qualification protocol

- Use LCATL0012 as calibration only. Choose thresholds and handling for
  visible single-speaker, multiple-visible-face, off-screen/voiceover,
  noise, obstruction, and synchronization strata there.
- Freeze settings before evaluating held-out LCATL0011 once. Keep the
  synchronized camera leakage group intact. Do not tune on held-out results.
- For each interval, collect independent blind agent labels from at least
  two model families and a third tie-breaker. Record per-labeler decisions,
  agreement, tie-breaks, visibility, and uncertain cases. Report these as
  agent labels, not gold or human labels. The candidate predictions and
  incumbent outputs must be hidden from labelers.
- Compare LR-ASD against the current M7 mouth-motion output, the existing
  co-occurrence links, and ECAPA identity separately. Score speech activity,
  visible-person attribution, off-screen/voiceover handling, abstention,
  contradiction rate, and temporal overlap. Report strata rather than only
  an aggregate.
- Apply only measured M6 source-clock transforms. Report offsets and their
  uncertainty with joined evidence; refuse joins without a compatible
  measured mapping.
- Measure cold/warm latency, peak memory, preprocessing/decode/inference/
  serialization split, and end-to-end cost on the 24 GB Mac. Use full
  precision for this sub-1.5M-parameter model; no lower-precision rerun is
  required unless it becomes a shortlist candidate.

## Calibration smoke measurement

One fixed 10-second segment of calibration source LCATL0012 (source seconds
40-50) was run through the AVA checkpoint at full precision on MPS. The
input was one 25 fps face crop from the existing M3b face track, expanded
from its measured 1408x792 coordinate box into the 3840x2160 source frame.
This is a single visible-face smoke measurement, not an accuracy evaluation:
the speaker labels were not exposed, no threshold was selected, and no
held-out source was read.

The adapter-free inference emitted 249 per-frame scores (mean 0.902294,
range 0.000190-0.999921). Scores were stable on a repeated warm pass.
Inference took 5.058 s cold and 2.419 s warm; model load took 0.101 s;
decode/crop took 3.982 s; MFCC preparation took 0.011 s. Peak process RSS
was 799,653,888 bytes and MPS driver allocation after inference was
1,168,834,560 bytes. Serialization was not separately timed. The measured
interval's M7 output attributes voice_001 to face_001 for 40.000-65.000 s,
and the M3b ECAPA diarizer assigns voice_001 to 9.96 of the 10 seconds;
voice_002 has no turn in this interval. This single-face sample is
consistent across LR-ASD score, M7 and ECAPA turn timing, but cannot
establish speaker attribution accuracy or improvement over the incumbent.
See the raw scores in `/tmp/vep-mu-asd-models/lr-asd-lcatl0012-40-50.json`.

This smoke run does not test multiple visible faces, off-screen speech,
voiceover, noise, obstruction or cross-camera offset handling. The current
Ren M3b data contain only one face track in this camera. Those cases need
agent-labelled intervals and a Ren track adapter before qualification.

## Incumbent baseline and reconciliation scaffolding (AUD-036..041)

The incumbents LR-ASD must beat are specified, not estimated:

- M7 mouth-motion link (`library/tools/event_spans.py::link_voices_to_faces`):
  per voice track, mean lip-motion inside its turns divided by mean motion
  outside them, per face track; links the best face only when at least 20
  inside frames exist and the ratio reaches 1.5. Every ratio is recorded
  whether linked or not. On the smoke window it attributes voice_001 to
  face_001 for 40.000-65.000 s.
- M3b co-occurrence (`speech_face_links`): temporal co-occurrence only, a
  candidate on single-person angles, unable to separate a listener from a
  speaker by construction.
- ECAPA diarization (M3b): independent voice identity; on the smoke window
  voice_001 holds 9.96 of 10 seconds and voice_002 has no turn. ECAPA and
  face identity are measured independently and stay distinct operations.

Reconciliation machinery already in place, pending attribution tie-break for quality:

- AUD-036 (ECAPA comparison): the eval separates ECAPA voice identity from
  face attribution; the protocol scores them apart before any joint number.
- AUD-037 (contradictions): voice/face disagreement is a separate
  `speaker_face_disagreement` candidate relation, never an overwrite.
- AUD-038 (offsets): only measured M6 source-clock transforms join
  evidence; the projection refuses joins above the caller's
  `max_error_ticks` and cites the transform in a `time_alignment`
  observation. Same-clock, cross-clock-cited, cross-asset, and refused
  joins are all unit-tested. No new offset was measured in this lane.
- AUD-040 (mouth movement is not proof): the M7 `speaking` span remains an
  observation, but grounding identity links and relations from it are
  `candidate`/`inferred` with null confidence - asserted by test.
- AUD-041 (does reconciliation help): `cross_modal_eval` scores the
  candidate predicate under speech/person relations with strata, abstention,
  contradiction rate, and temporal overlap. The runner is ready; round-3
  attribution metrics wait on the blind tie-break, with held-out LCATL0011
  untouched.

## Round-3 blind-label agreement

Two independent agent label sets are now available for the 24 calibration
windows: OpenAI/Codex and opencode Muse-Spark. The predeclared agreement unit
is a window for speech presence. It requires both labelers to report speech
or both to report none. That agrees in 24/24 windows (20 speech-present, 4
empty). For speech timing, compare the union of reported turn intervals within
each window; this avoids treating a different sentence segmentation as a
different speech span. Aggregate interval IoU is 0.9553 (158.20 s shared of
165.60 s union); mean window IoU over the 20 speech windows is 0.9533. This is
agent-label agreement, not model accuracy.

Attribution is not scoreable from A/B consensus: A supplied 24 pause-grouped
turns and marked every association uncertain; B supplied 48 finer-grained
turns, 25 visible and 23 off-screen. The turn boundaries do not pair reliably,
and an uncertain association is not agreement with a specific visible or
off-screen call. The condition fields are likewise not scored because A marked
each condition uncertain in every window. No attribution or condition
accuracy is inferred from these labels. A blind tie-break packet now covers
only the 20 windows where speech presence agrees but turn-level attribution
does not; it is at
`/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/data/vep-mu-asd/tiebreak-request.md`.

At this point in the A/B agreement pass, the previous personal benchmark
checkpoint and scratch outputs had been removed, so candidate quality was
still unscored. The later C tie-break and scheduled scoring are recorded in the
following section.

## Round-3 tie-break and LR-ASD scoring

The third blind labeler used Anthropic Claude/Sonnet 5.5, independent of A
(OpenAI/Codex) and B (opencode Muse-Spark). The tie-break packet covered 20
windows. Its structured JSON contains 58 turns: 19 visible P1, 35 off-screen,
and 4 uncertain. The accompanying prose report says 79 turns (29 visible, 44
off-screen, 6 uncertain), which conflicts with the structured records. Counts
and scoring below use the structured JSON; the discrepancy is retained as a
label artifact, not silently reconciled. This is agent adjudication, not gold
truth.

The predeclared split keeps LCATL0012 as calibration and freezes its turn-mean
score threshold at 0.4013986, chosen to maximize turn-level balanced accuracy
(ties favor the higher threshold). The threshold was applied once to the other
11 labeled windows: nine LC4931 windows and two Apple 001 windows. LR-ASD ran
in full precision on MPS with PyTorch CPU fallback for its unsupported
`max_pool3d` operation. The AVA checkpoint came from LR-ASD revision
`1b6dcd2d8fc2895683de6508ec6294ec47d388ca`, was 3,426,337 bytes, and was
removed after the run.

On calibration, the turn classifier scored 6/6 visible turns and correctly
abstained on 19/21 off-screen turns (balanced accuracy 0.952, turn-score AUC
0.976). On the frozen evaluation set it attributed 6/13 visible turns and
abstained on 13/14 off-screen turns (visible attribution 0.462, off-screen
abstention 0.929, accuracy 0.704, balanced accuracy 0.695, turn-score AUC
0.846). At frame level on the same evaluation windows, active-score precision
was 0.903, recall 0.744, F1 0.816, and visible-interval temporal IoU 0.689.
The frame calculation treats annotated off-screen speech and silence as
negative, and excludes uncertain intervals. These small, agent-labeled
samples do not establish human accuracy or generalization.

The evaluation used OpenCV Haar frontal-face detections with a largest-then-
nearest crop heuristic, not Ren's face tracks. The structured labels mark no
multi-face window, so multiple-face selection was not measured. Four windows
are marked partially obscured and one has masking noise; those strata are too
small for reliable separate claims. Three windows explicitly document silent
lip movement or lip/audio contradictions, supporting the rule that mouth
movement alone cannot establish identity. LR-ASD is a visible-person activity
candidate, not a voice identity system; no aligned ECAPA or incumbent M7/M3b
predictions were available for this packet, so identity improvement and
contradiction-rate comparisons remain unmeasured.

The scheduled run used `semantics.analyse:model_memory` (its declared profile
is empty because the candidate is below the 5 GB routing threshold), with a
2.5 GB in-process abort guard. Scheduler sampling measured 1.03 GB peak process
tree RSS; MPS reported 1.0905 GB driver allocation, for a conservative sum of
2.1205 GB. Twenty windows took 371.47 s scheduler execution time: 273.17 s
decode/preprocess, 92.65 s inference, 1.52 s serialization, and 0.067 s model
load. First-window end-to-end was 22.20 s; the mean for the remaining 19 was
18.17 s. The MIT license is permissive. LR-ASD stays research-only because
held-out visible attribution is low and the required incumbent/ECAPA and
multi-face comparisons are absent. The detailed scorecard records the
measurements and remaining scope.

## Relation contract coordination

The relation shape was approved through firstmate on 2026-10-15. The
audio-to-visible-person candidate relation is owned here; F06 consumes it
read-only. The implementation leaves the existing Project Model schema
unchanged and puts transform references in a cited `time_alignment`
observation because relation evidence is restricted to observation IDs.

## Status

LR-ASD remains a benchmark-only candidate. The Ren adapter
(`library.tools.asd_lr`) is integrated and verified frame-identical to the
smoke run; labeled-window quality, latency, and memory are now measured. A/B
labels agree on speech presence and temporal coverage but cannot establish
attribution agreement; C supplied an independent blind tie-break, with a
structured/prose count discrepancy recorded above. Held-out visible
attribution is low, and multi-face, ECAPA, and incumbent comparisons remain
unmeasured. The reusable Project Model projection is integrated; checkpoint
inference remains isolated to the personal benchmark environment.
The detailed scorecard is
`2026-10-15-active-speaker-lr-asd.json`. No adoption or baseline replacement
is justified yet. C³ASD has no runnable weights and stays a watched
candidate; FunCineForge contributed its reconciliation pattern only.
