# Event-caption candidates: license findings and adoption decisions (SEM-018..020, SEM-025)

Status 2026-10-16: harness, adapter, and comparison tooling are landed and
tested; agent labels are pending from firstmate (`label-request.md` in this
lane's data dir); no candidate has measured quality numbers yet. Nothing
below invents a measurement. This record covers technical comparison scope
and evidence grounding only. It does not assess editorial quality.

## Incumbent baseline (retain)

- What: folded Gemma call per 10 s window (`analyze_windows` in
  `library/tools/analysis/vision_pipeline_v3.py`, model
  `mlx-community/gemma-4-12b-it-4bit`), sequential windows, decode 2 fps.
- Adapter: `library/tools/event_caption_adapter.py`
  (`gemma_windows_to_events`, revision `event_caption_adapter/1`) emits one
  event per action with window + reconstructed-plan frame evidence
  (SEM-023/024). Scene/camera/assessment prose is never converted to events.
- Decision: **retain baseline**. No challenger has a measured scorecard, so
  per ARCH-004/005 nothing replaces it. No default or schema changed.
- Pending (needs labels, then a model pass): SEM-017 incumbent recall on
  calibration; SEM-021 sequential vs parallel with equivalent sampling/input
  via `compare_modes`; SEM-022 one-pass replacement. Execution protocol:
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

## CapRL-Video-4B (SEM-019) - research-only, not downloaded

Findings 2026-10-16 from the publisher's card (`internlm/CapRL-Video-4B`):

- Card front-matter claims `apache-2.0`, but the license section reads
  "Code Apache-2.0 / Data CC BY-NC 4.0" with "data and code ... licensed
  for research use only". The weight grant is therefore ambiguous and the
  conservative reading governs.
- Trained from Qwen3-VL-4B for dense timestamped captioning; GGUF builds
  exist in the 2B/4B siblings, none advertised for the Video-4B weights.

Decision: **research-only**. Personal-benchmark-only, never shipped, never
represented as Commercial-eligible, independent of the front-matter tag.
No download until agent labels exist to score it against, and then only
for a personal research run.

## Useful evidence per unit of compute (SEM-025)

Adoption bar (unchanged): a dedicated captioner is adopted only when
frozen `ren_evaluation_report/1` rows show it improves matched,
evidence-backed events per inference second and per peak GB-second
without material regression in recall, precision, unsupported-claim rate,
timestamp quality, or memory/latency on the 24 GB Mac. Held-out rows are
report-only and never select a version.

No scorecard JSON is emitted in this change: every quality cell is still
unmeasured (labels pending), and a JSON of empty metrics would read as a
result. The first measured rows land with the calibration run, reusing
`library/tools/evaluation_scorecard.py` (already on main).
