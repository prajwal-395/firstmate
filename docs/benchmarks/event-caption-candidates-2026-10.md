# Event-caption candidates: license findings and adoption decisions (SEM-018..020, SEM-025)

Status 2026-10-16: harness, adapter, and comparison tooling are landed and
tested. Calibration agent labels, incumbent/one-pass measurements, and the
completed CapRL calibration run are recorded in
[`event-caption-benchmark-2026-10.md`](event-caption-benchmark-2026-10.md).
The Gemma exact lexical comparison produced no matches; CapRL produced one
raw-label match and none against adjudicated labels. Exact lexical matching
cannot establish semantic caption quality. This record covers technical
comparison scope and evidence grounding only. It does not assess editorial
quality.

## Incumbent baseline (retain)

- What: folded Gemma call per 10 s window (`analyze_windows` in
  `library/tools/analysis/vision_pipeline_v3.py`, model
  `mlx-community/gemma-4-12b-it-4bit`), sequential windows, decode 2 fps.
- Adapter: `library/tools/event_caption_adapter.py`
  (`gemma_windows_to_events`, revision `event_caption_adapter/1`) emits one
  event per action with window + reconstructed-plan frame evidence
  (SEM-023/024). Scene/camera/assessment prose is never converted to events.
- Decision: **retain baseline**. The first exact-action comparison gives
  0 matches for both the incumbent and events-only prompt under raw A, raw B,
  and adjudicated references. This is a limitation of the exact lexical
  matcher, not evidence of zero semantic accuracy. No default or schema
  changed.
- Measured: SEM-017 ran on 30 calibration windows, 30/30 parsed, 1336.8 s,
  cold load 40.7 s, 1.0 GB process RSS (but the process's unified-memory
  footprint measured 7.54 GB during a later failed rerun). SEM-022's
  events-only pass parsed 25/30 in 1859.2 s, cold load 48.9 s, 0.92 GB RSS.
  It is slower with five parse failures, so it is not adopted. Reproducible
  details and the failed rerun are in the benchmark report.
- SEM-021 parallel comparison was not completed: two concurrent shards
  previously drove system swap to 32.2/32.8 GB and were killed. Do not retry
  concurrent same-model shards; require a measured admission reservation
  before considering a parallel run.
- Remaining candidate work: SEM-018 PadCaptioner is not fetched until its
  checkpoint and base permissions are verified; SEM-019 CapRL is
  research-only. Its completed calibration scorecard is report-only and does
  not support adoption. No candidate is adopted. Execution protocol
  for any later comparable run:
  same windows, same sampling plan, wall-clock both, freeze one
  `ren_evaluation_report/1` per mode/split with
  `build_evaluation_report`, aggregate with
  `python3 -m library.tools.evaluation_scorecard`.

## PadCaptioner-3B (SEM-018) - gated, not downloaded

Findings 2026-10-16 from the publisher's pages (no weights fetched):

- Checkpoint `wenzhengzeng/PadCaptioner-3B` publishes **no model card and
  no license statement** on its HuggingFace page.
- The paper builds on Qwen2.5-VL (visual + LLM) and Whisper-Large-v3
  (audio), fine-tuned with LoRA on a ChronusAV subset. None of the
  weight, base-checkpoint, dataset, or conversion rights is established by
  the checkpoint page.
- Published weights are ~9.7 GB across two xet files: above what stays
  resident next to Resolve on the 24 GB Mac without a lifecycle plan.

Decision: **conditional fallback at best - do not fetch or run** until the
checkpoint license and the Qwen2.5-VL / Whisper-Large-v3 base permissions
are verified with vep-mu-license. A source paper does not establish
weight, base-checkpoint, dataset, or conversion rights.

## CapRL-Video-4B (SEM-019) - research-only; calibration measured

Findings 2026-10-16 from the publisher's card (`internlm/CapRL-Video-4B`):

- Card front-matter claims `apache-2.0`, but the license section reads
  "Code Apache-2.0 / Data CC BY-NC 4.0" with "data and code ... licensed
  for research use only". The weight grant is therefore ambiguous and the
  conservative reading governs.
- Trained from Qwen3-VL-4B for dense timestamped captioning; GGUF builds
  exist in the 2B/4B siblings, none advertised for the Video-4B weights.

Decision: **research-only**. Personal-benchmark-only, never shipped, never
represented as Commercial-eligible, independent of the front-matter tag.

Approved personal-benchmark download (not committed):

- `mradermacher/CapRL-Video-4B-GGUF@87bfd741f670dbbbe5bd94a50e3c93728f01c210`
- Q4_K_M weights: 2,716,070,016 bytes; Q8_0 projector: 453,974,336 bytes.
- Temporary local cache: `/tmp/vep-mu-captions/caprl-cache` (removed after
  the completed calibration run).
- Upstream model revision: `internlm/CapRL-Video-4B@d932a039f2a7f580abf9d9e07dabe60a18eb1960`.

The completed 30-window calibration run used 2 fps video sampling and 512
image tokens. It parsed all windows into 80 individual event records. The
first window took 37.17 s; later windows had a 180.16 s median, but every
window ran in a fresh process and therefore reloaded the model. Total wall
time was 4717.84 s. The sampled process-tree physical-footprint peak was
5.753 GB under a 6 GB scheduler reservation. RSS and the
decode/inference/serialization split were not measured.

The scorer matched 0/73 adjudicated agent labels, 0/62 raw-A labels, and 1/51
raw-B labels (raw-B recall 0.0196, precision 0.0125, unsupported-claim rate
0.9875). The single match had 0 s start error, 1 s end error, and 0.75
interval IoU. Evidence coverage was 1.0 from a reconstructed uniform input
sampling plan; `llama-cli` does not report the exact timestamps of consumed
frames. Exact normalized action matching is lexical and does not establish
semantic caption quality or evidence truth. The aggregate scorecard is
[`2026-10-16-event-caption-caprl.json`](2026-10-16-event-caption-caprl.json);
labels and full split-level scores stay outside the repository. The model
cache was deleted after this personal calibration benchmark.

Decision remains **research-only** due to the publisher's non-commercial
research terms and the absence of a quality or compute-efficiency gain over
Gemma under the available exact-match measure. Keep the incumbent default;
do not interpret these calibration metrics as held-out performance.

## Useful evidence per unit of compute (SEM-025)

Adoption bar (unchanged): a dedicated captioner is adopted only when
frozen `ren_evaluation_report/1` rows show it improves matched,
evidence-backed events per inference second and per peak GB-second
without material regression in recall, precision, unsupported-claim rate,
timestamp quality, or memory/latency on the 24 GB Mac. Held-out rows are
report-only and never select a version.

The CapRL calibration scorecard is report-only. The measured exact-match
result does not demonstrate improved useful evidence per unit of compute,
and the 30-window run took 3.53x the completed Gemma sequential run's wall
time. The candidate remains research-only; Gemma stays the baseline. No
held-out data was used for selection.
