# Sound-event lane findings (2026-10-16)

## Scope and decision

PANNs remains the timed-event baseline. Retain it behind the existing
`sound_event_pipeline` interface. FlexSED remains research-only and quarantined:
its weights' rights are unverified, and its current results are synthetic-fixture
measurements, not evidence of real-footage accuracy. Do not change defaults.

## What is already implemented

- `library/tools/analysis/sound_event_pipeline.py` produces PANNs frame-derived
  timed sound events with source-second boundaries. It remains the incumbent.
- `library/tools/analysis/sfx_pipeline.py` has an Audio Flamingo Next caption
  path for authorized noncommercial research, and the public LAION-CLAP path.
  Captions are descriptions, not a source of verified event timing.
- Generated SFX descriptions carry provider identity, review status, and an
  uncertainty statement; the SFX catalogue passes this metadata to the
  planner with the description. Legacy descriptions with no recorded source
  are explicitly marked as provenance-unrecorded.
- `FootageIndex.sound_search` already accepts natural-language queries over
  measured PANNs labels, but matching is lexical against available labels;
  FlexSED is the evaluated open-vocabulary query path.
- The new benchmark evaluator scores one-to-one, label-constrained temporal
  matches, onset/offset errors, rare/nonstandard classes, and hard negatives.
  Deterministic generated fixtures exercise overlaps without claiming to be
  real acoustic references.
- The PE-AV harness and its measured report are in
  `2026-10-16-pe-av-retrieval.report.json`.

## Measurements available

The PANNs and FlexSED reports in this directory use synthetic calibration
fixtures only. In the latest PANNs rerun (fp32 CPU, checkpoint
`Cnn14_DecisionLevelMax_mAP=0.385.pth`), warm processing was 4.59 seconds per
6-8 second clip in the first run and 12.79 seconds in the later serialized
rerun, peak RSS was 0.78 GB in that rerun, and synthetic-label recall was 0.0.
That recall is a harness signal only. It must not be presented as real-footage
performance. FlexSED's initial and rerun synthetic measurements recorded 9.63
and 35.29 seconds per clip respectively; the latter used 1.12 GB peak RSS and
F1 0.167. Its proposals and metrics are also not
agent-adjudicated real-footage results. Both reports record fp32 CPU precision.

Real calibration and held-out claims remain pending independent blind agent
labels, with a third model-family tie-breaker for disagreements. The request is
in the lane data directory as `label-request.md`; the held-out set must be
reported once and must not tune thresholds. Until those labels arrive, onset and
offset accuracy on real podcast/Apple 001 material is unmeasured.

## Candidate and availability notes

- **COSED:** its September 2026 paper describes open-vocabulary temporal sound
  detection and a multi-task benchmark. As of this research pass, I found no
  public publisher code or usable checkpoint to evaluate, so keep it on the
  watchlist and recheck availability before implementation. Rechecked
  2026-10-16: arXiv still exposes the paper but no code or checkpoint link;
  searches of the likely publisher GitHub and Hugging Face surfaces found no
  COSED artifact. This is an availability check, not evidence that no private
  or unindexed release exists. Sources:
  <https://arxiv.org/abs/2609.30083> and
  <https://github.com/JHU-LCAP>.
- **Audio Flamingo Next:** the publisher's captioner checkpoint is an 8B BF16
  model under NVIDIA's OneWay Noncommercial License. Its published Hugging Face
  revision is `62aa63a66927f04f8308620f8e4b65696d3ea0a6`; the local cache has
  the complete 16,534,531,504-byte `model.safetensors` at
  `~/.cache/huggingface/hub/models--nvidia--audio-flamingo-next-captioner-hf/`
  (downloaded for this benchmark; removed after the attempt). The authorized
  int4/MPS load succeeded and reported 5.6 GB allocated MPS memory, but caption
  generation did not complete after 897 seconds and was interrupted. Therefore
  inference latency, caption quality, and peak process RSS are unmeasured. The
  loader reported 5.6 GB current MPS allocation; this is not a peak or total
  process-footprint measurement. Do not infer event timing or visible-entity
  links from its captions. Its research-only purpose and license prevent
  commercial/default adoption. Source:
  <https://huggingface.co/nvidia/audio-flamingo-next-captioner-hf>.
- **FlexSED:** the model's open-vocabulary query interface is exercised with
  natural-language labels such as “Door slam”; timing still comes from its
  frame scores and explicit postprocessing. Checkpoint rights remain
  unverified. Source code is publicly available at
  <https://github.com/JHU-LCAP/FlexSED>.
- **PE-AV:** retain as research-only for this task. Its calibration report
  records 0.75 audio-to-video and video-to-audio match accuracy over four
  paired clips, but CPU embedding took about 1,879 seconds per 30-second clip.
  This small retrieval set and high cost do not support a default change.

## Boundaries and next work

No sound event is linked to a visible entity without independent visual evidence.
No caption substitutes for measured boundaries. The new
`sound_event_query.measure_query_with_separation` path triggers the existing
HTDemucs adapter only when a query-matched event is low-confidence or overlaps
measured speech/music. Its detections remain estimated-stem candidates and
cannot replace source measurements. The lane still needs agent-labeled
calibration/held-out scoring and a compatible complete AF-Next research
inference before AUD-052..064 can be marked complete.
